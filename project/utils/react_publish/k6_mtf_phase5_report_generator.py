"""K=6 MTF 205-scope Phase 5 honest-validation report generator (PR-2a).

Emits, from the ranking artifact + validation sidecar + Stage-A
exclusion input:

1. A human-readable Markdown report mirroring the known-good 8-ticker
   report structure at
   ``md_library/shared/2026-06-01_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT.md``,
   scaled to the 205-secondary candidate.
2. A paired machine-readable JSON manifest carrying the binding fields
   the promotion gate reads (report SHA, sidecar SHA, run ids, counts,
   methodology). The gate reads THIS manifest, never the Markdown prose.

Strict rules:

- Fail closed: if neither a supplied Stage-A list nor an execute-summary
  path is provided, generate nothing (raise ``ReportGenerationError``).
  Never fabricate or infer exclusions.
- The Markdown report does NOT contain its own SHA (self-reference
  problem); the operator/gate computes the report file SHA after the
  final bytes are written. The manifest records that report SHA.
- All cited paths in both outputs are project-relative ONLY. An
  execute-summary input path (possibly OS-temp) is NEVER echoed into the
  outputs; only the secondary/cause data is recorded.
- This module performs NO promotion and writes NO frontend/public
  fixture.

This module reuses the PR-1 join helper's loaders and Stage-A
normalizers so the report's exclusion shape matches the v2 fixture's
``stage_a_excluded_secondaries``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from utils.react_publish.k6_mtf_validation_join import (
    ValidationJoinError,
    _is_finite_number,
    _normalize_stage_a_exclusions,
    _stage_a_exclusions_from_execute_summary,
    compute_file_sha256,
)


REPORT_MANIFEST_SCHEMA = "k6_mtf_phase5_report_manifest"
REPORT_MANIFEST_VERSION = "v1"
FIXTURE_SCHEMA_VERSION_EXPECTED = "k6_mtf_ranking_v2"

_LOCAL_ABS_PATH_RE_NOTE = (
    "paths in outputs are project-relative only; see _assert_project_relative"
)

# Generic local-absolute-path matcher (token fragments are matcher
# literals, not real paths).
import re as _re

_LOCAL_ABS_PATH_RE = _re.compile(
    r"(?:[A-Za-z]:[\\/])"
    r"|(?:^[\\/])"
    r"|(?:/(?:Users|home|mnt)/)"
    r"|(?:\\)"
)
_LOCAL_NAME_MARKERS = ("appdata", "miniconda", "temp/")


class ReportGenerationError(Exception):
    """Raised when the report/manifest cannot be generated safely."""


# ---------------------------------------------------------------------------
# Path / privacy helpers
# ---------------------------------------------------------------------------


def _assert_project_relative(value: str, *, where: str) -> str:
    """Raise if ``value`` is not a safe project-relative path string."""
    if not isinstance(value, str) or not value:
        raise ReportGenerationError(f"{where} must be a non-empty string")
    if _LOCAL_ABS_PATH_RE.search(value):
        raise ReportGenerationError(
            f"{where} is not project-relative (local path token): {value!r}"
        )
    low = value.lower()
    for marker in _LOCAL_NAME_MARKERS:
        if marker in low:
            raise ReportGenerationError(
                f"{where} contains a local path marker {marker!r}: {value!r}"
            )
    return value


# ---------------------------------------------------------------------------
# Sidecar-derived counts and per-row partitions
# ---------------------------------------------------------------------------


def _strategy_secondary(strat: Mapping[str, Any]) -> str:
    sid = str(strat.get("strategy_id") or "")
    return sid.split(":", 1)[1] if ":" in sid else sid


def derive_counts(validation_payload: Mapping[str, Any]) -> Dict[str, int]:
    """Derive the report/manifest counts from the sidecar per-row data
    (not from memory). Fail closed on a non-'valid' sidecar."""
    if validation_payload.get("validation_status") != "valid":
        raise ReportGenerationError(
            "validation_status must be 'valid'; got "
            f"{validation_payload.get('validation_status')!r}"
        )
    alpha_raw = validation_payload.get("multiple_comparisons_control_alpha")
    if not isinstance(alpha_raw, (int, float)):
        raise ReportGenerationError(
            "sidecar missing multiple_comparisons_control_alpha"
        )
    alpha = float(alpha_raw)
    strategies = validation_payload.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise ReportGenerationError("sidecar 'strategies' must be a non-empty list")
    tested = len(strategies)
    board = 0
    validated = 0
    empirical_not_run = 0
    for s in strategies:
        status = s.get("empirical_validation_status")
        if status == "validated":
            validated += 1
            bh_q = s.get("bh_q_value")
            if _is_finite_number(bh_q) and float(bh_q) <= alpha:
                board += 1
        elif status == "empirical_not_run":
            empirical_not_run += 1
    not_validated = tested - board
    validated_but_not_bh = validated - board
    return {
        "tested": tested,
        "board_validated": board,
        "not_validated": not_validated,
        "validated_but_not_bh": validated_but_not_bh,
        "empirical_validated": validated,
        "empirical_not_run": empirical_not_run,
    }


def _methodology(validation_payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "n_permutations": validation_payload.get("n_permutations"),
        "n_bootstrap_samples": validation_payload.get("n_bootstrap_samples"),
        "walk_forward_n_folds": validation_payload.get("walk_forward_n_folds"),
        "mc_method": validation_payload.get("multiple_comparisons_control_method"),
        "alpha": validation_payload.get("multiple_comparisons_control_alpha"),
        "bootstrap_ci_level": validation_payload.get("bootstrap_ci_level"),
        "contract_version": validation_payload.get("validation_contract_version"),
        "methodology_version": validation_payload.get("validation_methodology_version"),
        "rng_seed": validation_payload.get("rng_seed"),
    }


def _partition_rows(
    validation_payload: Mapping[str, Any], alpha: float,
) -> Dict[str, List[dict]]:
    board: List[dict] = []
    not_bh: List[dict] = []
    not_run: List[dict] = []
    for s in validation_payload.get("strategies", []):
        status = s.get("empirical_validation_status")
        bh_q = s.get("bh_q_value")
        if status == "validated" and _is_finite_number(bh_q) and float(bh_q) <= alpha:
            board.append(s)
        elif status == "validated":
            not_bh.append(s)
        elif status == "empirical_not_run":
            not_run.append(s)
        else:
            # empirical_failed or anything else -> not_validated bucket.
            not_bh.append(s)
    return {"board": board, "not_bh": not_bh, "not_run": not_run}


def _fnum(x: Any, nd: int = 4) -> str:
    if _is_finite_number(x):
        return f"{float(x):.{nd}f}"
    return "n/a"


def _sorted_by_sharpe(rows: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(
        rows,
        key=lambda s: (s["sharpe"] if _is_finite_number(s.get("sharpe")) else -1e18),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Markdown builder
# ---------------------------------------------------------------------------


def build_markdown_report(
    *,
    report_date: str,
    ranking_payload: Mapping[str, Any],
    validation_payload: Mapping[str, Any],
    validation_sidecar_sha256: str,
    ranking_artifact_path: str,
    validation_sidecar_path: str,
    stage_a_excluded: Sequence[Mapping[str, Any]],
    report_path: str,
    manifest_path: str,
) -> str:
    """Build the 205-scope Markdown report body (no self SHA)."""
    counts = derive_counts(validation_payload)
    method = _methodology(validation_payload)
    alpha = float(validation_payload.get("multiple_comparisons_control_alpha"))
    parts = _partition_rows(validation_payload, alpha)

    ranking_run_id = ranking_payload.get("run_id")
    validation_run_id = validation_payload.get("run_id")

    # Distinct unavailable source tickers across all Stage-A causes.
    unavailable: List[str] = []
    seen_unavail: set = set()
    for e in stage_a_excluded:
        for c in e.get("causes") or []:
            t = c.get("ticker")
            if isinstance(t, str) and t and t not in seen_unavail:
                seen_unavail.add(t)
                unavailable.append(t)

    rng_seed_disclosure = (
        "null (not persisted in this legacy sidecar)"
        if method["rng_seed"] is None else str(method["rng_seed"])
    )

    L: List[str] = []
    L.append("# K=6 MTF Phase 5 Honest-Validation Report (205-secondary candidate)")
    L.append("")
    L.append(f"**Date:** {report_date}")
    L.append("")
    L.append(
        "**Status:** OPERATOR-REVIEW REPORT PACKAGE (docs-only; derived from "
        "the accepted private 205-secondary candidate's validation sidecar; "
        "no compute, no promotion, no deploy)"
    )
    L.append("")
    L.append("**Author:** PRJCT9 sprint")
    L.append("")
    L.append(
        "**Scope:** Phase 5 honest-validation evidence package for the K=6 "
        "MTF 205-secondary private partial candidate (the accepted clean "
        "subset of the full-248 recook). This report mirrors the 8-ticker "
        "launch report structure scaled to 205 secondaries."
    )
    L.append("")
    L.append("---")
    L.append("")
    # 1. Executive summary
    L.append("## 1. Executive summary")
    L.append("")
    L.append(
        f"- {counts['tested']} secondaries were validated under the locked "
        "walk-forward + Benjamini-Hochberg + empirical methodology; "
        f"`validation_status=valid`."
    )
    L.append(
        f"- {counts['board_validated']} secondaries are board_validated "
        f"(BH-reported, `bh_q_value <= {alpha}`)."
    )
    L.append(
        f"- {counts['not_validated']} secondaries are not_validated and remain "
        "displayed on the research board for transparency: "
        f"{counts['validated_but_not_bh']} were tested but did not clear the "
        f"BH gate, and {counts['empirical_not_run']} were not testable "
        "(`empirical_not_run`; sparse directional triggers)."
    )
    L.append(
        f"- {len(stage_a_excluded)} secondaries were Stage-A excluded "
        f"(drop-and-list) due to {len(unavailable)} unavailable source "
        "tickers; they are omitted from the ranked board and listed in the "
        "coverage section."
    )
    L.append(
        "- There are exactly two validation outcomes (board_validated / "
        "not_validated). There is no near_threshold tier."
    )
    L.append("")
    # 2. Evidence sources / artifact-run binding
    L.append("## 2. Evidence sources and artifact-run binding")
    L.append("")
    L.append("All paths are project-relative.")
    L.append("")
    L.append("**Ranking artifact (accepted 205-secondary candidate):**")
    L.append("")
    L.append(f"- Path: `{ranking_artifact_path}`")
    L.append(f"- Ranking `run_id`: `{ranking_run_id}`")
    L.append("")
    L.append("**Validation sidecar (`validation_status=valid`):**")
    L.append("")
    L.append(f"- Path: `{validation_sidecar_path}`")
    L.append(f"- SHA-256: `{validation_sidecar_sha256}`")
    L.append(f"- Validation `run_id`: `{validation_run_id}`")
    L.append("")
    L.append("**Paired machine-readable report manifest (for the promotion gate):**")
    L.append("")
    L.append(f"- Path: `{manifest_path}`")
    L.append(
        "- The manifest carries the report SHA-256, the sidecar SHA-256, the "
        "ranking/validation run ids, the counts, and the methodology fields. "
        "The promotion gate reads the manifest (not this Markdown prose) for "
        "its binding checks."
    )
    L.append("")
    L.append(
        "This report does NOT carry its own SHA-256 inside its body. The "
        "operator/gate computes the report file SHA-256 after the final byte "
        "content is fixed; the manifest records that value as `report_sha256`."
    )
    L.append("")
    # 3. Methodology summary
    L.append("## 3. Methodology summary")
    L.append("")
    L.append(
        "Source: locked methodology at "
        "`md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md` "
        "(Sections 4, 6, 7, 8, 9, 10, 13.5, 15). Same engine and adapter as "
        "the 8-ticker launch report; only the universe scope differs."
    )
    L.append("")
    L.append(
        "- Walk-forward OOS evaluation; same-secondary buy-and-hold baseline; "
        "Benjamini-Hochberg primary multiple-comparisons control with "
        "Bonferroni supplementary disclosure; direction-preserving empirical "
        "permutation p-value + bootstrap Sharpe CI for BH-survivors plus "
        "borderline candidates; honest `empirical_not_run` for strategies "
        "outside the empirical subset; strict no-lookahead per-fold cutoffs."
    )
    L.append("")
    # 4. Campaign parameters
    L.append("## 4. Campaign parameters")
    L.append("")
    L.append("All values extracted from the validation sidecar (not from memory).")
    L.append("")
    L.append("| Parameter | Value |")
    L.append("|---|---|")
    L.append(f"| Validation `run_id` | `{validation_run_id}` |")
    L.append(f"| `producer_engine` | `{validation_payload.get('producer_engine')}` |")
    L.append(f"| `app_surface` | `{validation_payload.get('app_surface')}` |")
    L.append(f"| `validation_contract_version` | `{method['contract_version']}` |")
    L.append(f"| `validation_methodology_version` | `{method['methodology_version']}` |")
    L.append(f"| `validation_status` | `{validation_payload.get('validation_status')}` |")
    L.append(f"| `data_available_through` | {validation_payload.get('data_available_through')} |")
    L.append(f"| In-sample window | {validation_payload.get('in_sample_window_start')} -- {validation_payload.get('in_sample_window_end')} |")
    L.append(f"| OOS window | {validation_payload.get('oos_window_start')} -- {validation_payload.get('oos_window_end')} |")
    L.append(f"| `walk_forward_n_folds` | {method['walk_forward_n_folds']} |")
    L.append(f"| `multiple_comparisons_control_method` | `{method['mc_method']}` |")
    L.append(f"| `multiple_comparisons_supplementary` | `{validation_payload.get('multiple_comparisons_supplementary')}` |")
    L.append(f"| `multiple_comparisons_control_alpha` | {method['alpha']} |")
    L.append(f"| `n_permutations` | {method['n_permutations']} |")
    L.append(f"| `n_bootstrap_samples` | {method['n_bootstrap_samples']} |")
    L.append(f"| `bootstrap_ci_level` | {method['bootstrap_ci_level']} |")
    L.append(f"| `n_strategies_tested` | {counts['tested']} |")
    L.append(f"| `n_strategies_reported` (board_validated) | {counts['board_validated']} |")
    L.append(f"| `rng_seed` | {rng_seed_disclosure} |")
    L.append("")
    # 5. Validation universe
    L.append("## 5. Validation universe")
    L.append("")
    L.append(
        f"The validated universe is the {counts['tested']} ranked secondaries "
        "of the accepted private 205-secondary candidate. This is the clean "
        "subset of the full-248 recook after Stage-A exclusions; it is a "
        "deliberately partial universe (see Section 8). The full ranked list "
        "and every per-row metric live in the cited ranking artifact and "
        "validation sidecar; this report summarizes and excerpts them."
    )
    L.append("")
    # 6. Results summary
    L.append("## 6. Results summary")
    L.append("")
    surv = validation_payload.get("survivorship_summary") or {}
    L.append("**Survivorship (from the sidecar `survivorship_summary`):**")
    L.append("")
    for k in (
        "total_tested", "total_reported_bh", "total_empirical_validated",
        "total_empirical_not_run", "did_not_survive_bh",
        "did_not_survive_empirical", "did_not_survive_no_triggers",
        "did_not_survive_insufficient_history",
    ):
        if k in surv:
            L.append(f"- `{k}`: {surv[k]}")
    L.append("")
    L.append("**Outcome partition (derived per-row from the sidecar):**")
    L.append("")
    L.append(f"- board_validated: {counts['board_validated']}")
    L.append(f"- not_validated: {counts['not_validated']} "
             f"(= {counts['validated_but_not_bh']} validated-but-not-BH "
             f"+ {counts['empirical_not_run']} empirical_not_run)")
    L.append(f"- empirical_validated total: {counts['empirical_validated']}")
    L.append("")
    # 6a. board_validated representative survivors
    L.append("### 6a. Board-validated survivors (88): representative rows")
    L.append("")
    L.append(
        "Top representative board_validated rows by aggregate Sharpe (full "
        "set in the sidecar):"
    )
    L.append("")
    L.append("| `strategy_id` | Aggregate Sharpe | BH q-value | Bonferroni p | Empirical p |")
    L.append("|---|---|---|---|---|")
    for s in _sorted_by_sharpe(parts["board"])[:15]:
        L.append(
            f"| `{s.get('strategy_id')}` | {_fnum(s.get('sharpe'))} | "
            f"{_fnum(s.get('bh_q_value'))} | {_fnum(s.get('bonferroni_p_value'))} | "
            f"{_fnum(s.get('empirical_p_value'))} |"
        )
    L.append("")
    # 6b. validated-but-not-BH
    L.append(
        f"### 6b. Not_validated -- tested but did not clear BH "
        f"({counts['validated_but_not_bh']})"
    )
    L.append("")
    L.append(
        "These rows ran the empirical layer (`empirical_validation_status="
        "validated`) but their BH q-value exceeded alpha. They remain "
        "displayed as not_validated for research transparency."
    )
    L.append("")
    L.append("| `strategy_id` | Aggregate Sharpe | BH q-value | Empirical p |")
    L.append("|---|---|---|---|")
    for s in sorted(
        parts["not_bh"],
        key=lambda s: (s["bh_q_value"] if _is_finite_number(s.get("bh_q_value")) else 1e18),
    ):
        L.append(
            f"| `{s.get('strategy_id')}` | {_fnum(s.get('sharpe'))} | "
            f"{_fnum(s.get('bh_q_value'))} | {_fnum(s.get('empirical_p_value'))} |"
        )
    L.append("")
    # 6c. empirical_not_run
    L.append(
        f"### 6c. Not_validated -- not testable / sparse directional triggers "
        f"({counts['empirical_not_run']})"
    )
    L.append("")
    L.append(
        "These secondaries had too few pooled directional (Buy/Short) "
        "triggers to run the empirical permutation/bootstrap null "
        "(`empirical_validation_status=empirical_not_run`). Empirical p-value "
        "and bootstrap CI are N/A for these rows; BH q-value / Bonferroni "
        "render when present. They remain displayed as not_validated."
    )
    L.append("")
    not_run_secs = sorted(_strategy_secondary(s) for s in parts["not_run"])
    L.append("Secondaries (empirical_not_run):")
    L.append("")
    L.append("`" + ", ".join(not_run_secs) + "`")
    L.append("")
    # 7. Stage-A exclusions
    L.append("## 7. Stage-A exclusions (drop-and-list coverage)")
    L.append("")
    L.append(
        f"{len(stage_a_excluded)} secondaries were excluded upstream at Stage "
        f"A due to {len(unavailable)} unavailable source tickers. They are "
        "NOT part of the ranked board and are listed here for coverage "
        "transparency only. Per operator-locked policy, frozen K=6 stacks are "
        "NOT recovered, rotated, remapped, or rewritten."
    )
    L.append("")
    L.append(f"Unavailable source tickers ({len(unavailable)}): "
             f"`{', '.join(sorted(unavailable))}`")
    L.append("")
    L.append("| Excluded secondary | Reason | Causes |")
    L.append("|---|---|---|")
    for e in sorted(stage_a_excluded, key=lambda e: str(e.get("secondary"))):
        cause_strs = []
        for c in e.get("causes") or []:
            t = c.get("ticker")
            cls = c.get("ticker_classification")
            role = c.get("dependent_role")
            cs = f"{t} ({cls}; {role})"
            if c.get("member_token"):
                cs += f" {c.get('member_token')}"
            cause_strs.append(cs)
        L.append(
            f"| {e.get('secondary')} | {e.get('reason')} | "
            f"{'; '.join(cause_strs) if cause_strs else '(none)'} |"
        )
    L.append("")
    # 8. Interpretation
    L.append("## 8. Interpretation")
    L.append("")
    L.append(
        f"- The {counts['tested']}-secondary validation campaign is valid: "
        "the walk-forward grid completed across all folds, BH/Bonferroni ran "
        "on the full set, and the empirical layer ran for the BH-survivor "
        "plus borderline subset."
    )
    L.append(
        f"- {counts['board_validated']} secondaries are the operator's honest "
        "board-validated evidence base. They are evidence, not predictions or "
        "guarantees of future performance."
    )
    L.append(
        "- not_validated rows (both tested-but-not-BH and empirical_not_run) "
        "remain visible as research-ranked rows; the board never lets a high "
        "rank read as statistical validation."
    )
    L.append("")
    # 9. Limitations / honesty notes
    L.append("## 9. Limitations and honesty notes")
    L.append("")
    L.append(
        "- **Accepted partial universe.** This candidate is the clean subset "
        "of the full-248 recook; the universe is deliberately partial."
    )
    L.append(
        "- **Stage-A drop-and-list policy.** The "
        f"{len(stage_a_excluded)} Stage-A exclusions are listed with causes "
        "and are not recovered or rotated."
    )
    L.append(
        "- **not_validated rows remain visible.** They are disclosed for "
        "transparency, not promoted as board-validated."
    )
    L.append(
        "- **empirical_not_run** rows reflect sparse directional triggers, a "
        "data characteristic, not a process failure."
    )
    L.append(
        f"- **rng_seed is {rng_seed_disclosure}.** This sidecar predates "
        "rng_seed persistence in the validation contract; the seed used at "
        "run time was not persisted into this artifact. Future reruns persist "
        "it. This is honest disclosure, not missing evidence."
    )
    L.append(
        "- **Survivorship / data-source disclosure** is consistent with the "
        "8-ticker report: self-administered validation evidence over a "
        "yfinance-sourced universe; bounded to the tested universe and run; "
        "not a guarantee of future performance."
    )
    L.append(
        "- **Report generation performs no promotion** and writes no "
        "frontend/public fixture."
    )
    L.append("")
    # 10. Promotion-helper inputs (future)
    L.append("## 10. Promotion-helper inputs (FUTURE; do NOT run from this PR)")
    L.append("")
    L.append(
        "When the operator later authorizes v2 public promotion (and Phase 5G "
        "data licensing is separately cleared), the promotion gate consumes "
        "the binding manifest plus this report and the sidecar. Required "
        "inputs (paths project-relative): the v2 fixture, this report path + "
        "its computed SHA-256, the paired report-manifest path, and the "
        "validation sidecar path + SHA-256. The gate verifies report <-> "
        "manifest <-> sidecar <-> fixture agreement and refuses on any "
        "mismatch. Public promotion remains a separate, explicit "
        "operator-authorized action; merging this report does not promote."
    )
    L.append("")
    # 11. Final status
    L.append("## 11. Final status")
    L.append("")
    L.append(
        "- Phase 5 honest-validation report for the 205-secondary candidate "
        "is prepared once this report and its manifest are merged."
    )
    L.append(
        "- Public promotion remains separately gated and BLOCKED until the "
        "operator explicitly authorizes it AND Phase 5G data licensing is "
        "separately cleared."
    )
    L.append("")
    L.append("End of report.")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    report_path: str,
    report_sha256: str,
    ranking_artifact_path: str,
    ranking_run_id: Any,
    validation_sidecar_path: str,
    validation_sidecar_sha256: str,
    validation_run_id: Any,
    counts: Mapping[str, int],
    methodology: Mapping[str, Any],
    generated_at_utc: str,
) -> dict:
    return {
        "report_manifest_schema": REPORT_MANIFEST_SCHEMA,
        "version": REPORT_MANIFEST_VERSION,
        "report_path": report_path,
        "report_sha256": report_sha256,
        "ranking_artifact_path": ranking_artifact_path,
        "ranking_run_id": ranking_run_id,
        "validation_sidecar_path": validation_sidecar_path,
        "validation_sidecar_sha256": validation_sidecar_sha256,
        "validation_run_id": validation_run_id,
        "fixture_schema_version_expected": FIXTURE_SCHEMA_VERSION_EXPECTED,
        "counts": {
            "tested": counts["tested"],
            "board_validated": counts["board_validated"],
            "not_validated": counts["not_validated"],
            "stage_a_excluded": counts["stage_a_excluded"],
            "empirical_validated": counts["empirical_validated"],
            "empirical_not_run": counts["empirical_not_run"],
            "validated_but_not_bh": counts["validated_but_not_bh"],
        },
        "methodology": {
            "n_permutations": methodology.get("n_permutations"),
            "n_bootstrap_samples": methodology.get("n_bootstrap_samples"),
            "walk_forward_n_folds": methodology.get("walk_forward_n_folds"),
            "mc_method": methodology.get("mc_method"),
            "alpha": methodology.get("alpha"),
            "bootstrap_ci_level": methodology.get("bootstrap_ci_level"),
            "contract_version": methodology.get("contract_version"),
            "methodology_version": methodology.get("methodology_version"),
            "rng_seed": methodology.get("rng_seed"),
        },
        "generated_at_utc": generated_at_utc,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _load_json(path: str | os.PathLike, *, label: str) -> Any:
    p = Path(path)
    if not p.is_file():
        raise ReportGenerationError(f"{label} not found: {p.as_posix()}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportGenerationError(f"{label} unreadable/invalid: {exc}") from exc


def _resolve_stage_a(
    *,
    stage_a_excluded_secondaries: Optional[Sequence[Any]],
    execute_summary_path: Optional[str | os.PathLike],
) -> List[dict]:
    """Stage-A resolution: supplied list preferred, else execute-summary
    path. Fail closed (raise) if neither is provided."""
    if stage_a_excluded_secondaries:
        try:
            return _normalize_stage_a_exclusions(
                stage_a_excluded_secondaries, evidence_source="supplied_context",
            )
        except ValidationJoinError as exc:
            raise ReportGenerationError(str(exc)) from exc
    if execute_summary_path is not None:
        summary = _load_json(execute_summary_path, label="execute summary")
        if not isinstance(summary, dict):
            raise ReportGenerationError("execute summary root is not a JSON object")
        try:
            return _stage_a_exclusions_from_execute_summary(summary)
        except ValidationJoinError as exc:
            raise ReportGenerationError(str(exc)) from exc
    raise ReportGenerationError(
        "no Stage-A exclusion input supplied (need a supplied list or an "
        "execute-summary path); refusing to generate (fail-closed)"
    )


def generate_report_and_manifest(
    *,
    ranking_path: str | os.PathLike,
    validation_sidecar_path: str | os.PathLike,
    report_output_path: str | os.PathLike,
    manifest_output_path: str | os.PathLike,
    report_relative_path: str,
    ranking_relative_path: str,
    sidecar_relative_path: str,
    generated_at_utc: str,
    report_date: str,
    expected_validation_sidecar_sha256: Optional[str] = None,
    stage_a_excluded_secondaries: Optional[Sequence[Any]] = None,
    execute_summary_path: Optional[str | os.PathLike] = None,
) -> dict:
    """Generate the Markdown report + JSON manifest. Writes both files.

    The ``*_relative_path`` arguments are the project-relative path
    strings cited inside the outputs; they are privacy-validated so no
    local absolute path can leak. The execute-summary input path is
    NEVER written into the outputs.
    """
    _assert_project_relative(report_relative_path, where="report_relative_path")
    _assert_project_relative(ranking_relative_path, where="ranking_relative_path")
    _assert_project_relative(sidecar_relative_path, where="sidecar_relative_path")

    sidecar_sha = compute_file_sha256(validation_sidecar_path)
    if expected_validation_sidecar_sha256 is not None:
        exp = str(expected_validation_sidecar_sha256).strip().lower()
        if exp != sidecar_sha:
            raise ReportGenerationError(
                f"validation sidecar SHA mismatch: expected {exp}, computed "
                f"{sidecar_sha}"
            )

    ranking_payload = _load_json(ranking_path, label="ranking artifact")
    validation_payload = _load_json(validation_sidecar_path, label="validation sidecar")
    if not isinstance(ranking_payload, dict) or not isinstance(validation_payload, dict):
        raise ReportGenerationError("ranking/sidecar root is not a JSON object")

    stage_a = _resolve_stage_a(
        stage_a_excluded_secondaries=stage_a_excluded_secondaries,
        execute_summary_path=execute_summary_path,
    )

    counts = derive_counts(validation_payload)
    counts_with_stage_a = dict(counts)
    counts_with_stage_a["stage_a_excluded"] = len(stage_a)
    method = _methodology(validation_payload)

    markdown = build_markdown_report(
        report_date=report_date,
        ranking_payload=ranking_payload,
        validation_payload=validation_payload,
        validation_sidecar_sha256=sidecar_sha,
        ranking_artifact_path=ranking_relative_path,
        validation_sidecar_path=sidecar_relative_path,
        stage_a_excluded=stage_a,
        report_path=report_relative_path,
        manifest_path=_manifest_relative(report_relative_path),
    )

    # Privacy guard: the rendered Markdown must contain no local-abs path.
    _assert_no_local_path_in_text(markdown, where="generated Markdown report")

    report_out = Path(report_output_path)
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_bytes = markdown.encode("utf-8")
    report_out.write_bytes(report_bytes)
    report_sha256 = compute_file_sha256(report_out)

    manifest = build_manifest(
        report_path=report_relative_path,
        report_sha256=report_sha256,
        ranking_artifact_path=ranking_relative_path,
        ranking_run_id=ranking_payload.get("run_id"),
        validation_sidecar_path=sidecar_relative_path,
        validation_sidecar_sha256=sidecar_sha,
        validation_run_id=validation_payload.get("run_id"),
        counts=counts_with_stage_a,
        methodology=method,
        generated_at_utc=generated_at_utc,
    )
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    _assert_no_local_path_in_text(manifest_text, where="generated manifest")

    manifest_out = Path(manifest_output_path)
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(manifest_text, encoding="utf-8")

    return {
        "report_path": report_relative_path,
        "report_sha256": report_sha256,
        "manifest": manifest,
        "counts": counts_with_stage_a,
    }


def _manifest_relative(report_relative_path: str) -> str:
    if report_relative_path.endswith(".md"):
        return report_relative_path[: -len(".md")] + ".manifest.json"
    return report_relative_path + ".manifest.json"


def _assert_no_local_path_in_text(text: str, *, where: str) -> None:
    if _LOCAL_ABS_PATH_RE.search(text):
        raise ReportGenerationError(
            f"{where} contains a local absolute path token; refusing"
        )
    low = text.lower()
    for marker in _LOCAL_NAME_MARKERS:
        if marker in low:
            raise ReportGenerationError(
                f"{where} contains a local path marker {marker!r}; refusing"
            )
