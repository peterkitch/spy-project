"""K=6 MTF validation sidecar -> ranking-artifact JOIN helper (PR-1).

Builds an in-memory ``k6_mtf_ranking_v2`` fixture payload by joining a
Phase 5 validation sidecar (empirical p / q / Bonferroni / bootstrap CI
per strategy) onto a ``k6_mtf_ranking_v1`` ranking artifact (descriptive
metrics per secondary). The two artifacts are NOT joined anywhere else
today: the ranking artifact holds descriptive metrics only and the
empirical values live solely in the separate validation sidecar.

This module is intentionally separate from
``promote_k6_mtf_artifact.py``: the join builds the v2 payload, the
promotion helper (or its v2-aware validator) validates it. This module
NEVER writes the real public fixture. It may write only to an explicitly
supplied output path (tests use ``tmp_path``; a smoke run uses an
OS-temp path), and refuses to write anywhere under
``frontend/public/``.

Operator-locked display model (PR-1 schema/gate layer):

- One ranked board: all qualifying ranked secondaries are displayed,
  ordered by the ranking artifact's own order, regardless of q-value.
- Exactly two ``validation_outcome`` values: ``board_validated`` and
  ``not_validated``. There is NO ``near_threshold`` or third outcome.
- ``board_validated`` is derived ONLY when
  ``empirical_validation_status == "validated"`` AND
  ``bh_q_value <= multiple_comparisons_control_alpha``. Everything else
  (validated-but-q>alpha, ``empirical_not_run``, ``empirical_failed``)
  is ``not_validated``.
- ``empirical_validation_status`` is carried verbatim as a separate
  field so PR-2 can distinguish tested-but-not-passing from
  not-testable / sparse-trigger rows.
- Validation outcome and ranking status are independent axes; a row may
  rank high and be ``not_validated``.
- The Stage-A-excluded secondaries are carried drop-and-list in a
  separate top-level ``stage_a_excluded_secondaries`` list with reasons.
  This module never recovers, rotates, or rewrites frozen K=6 stacks.

Fail-closed: any unknown empirical status, any ranking/sidecar row
mismatch, missing alpha, non-"valid" validation_status, SHA mismatch,
malformed ``strategy_id``, or duplicate secondary refuses to emit a
fixture (raises ``ValidationJoinError``).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


V2_SCHEMA_VERSION = "k6_mtf_ranking_v2"
V1_SCHEMA_VERSION = "k6_mtf_ranking_v1"

VALIDATION_OUTCOME_BOARD = "board_validated"
VALIDATION_OUTCOME_NOT = "not_validated"
VALIDATION_OUTCOMES = frozenset({VALIDATION_OUTCOME_BOARD, VALIDATION_OUTCOME_NOT})

# Empirical statuses the join knows how to map. Anything else is an
# unknown status and fails closed. ``empirical_failed`` is included
# defensively even though the 205 run only produced ``validated`` and
# ``empirical_not_run`` (code may also allow ``empirical_failed``).
KNOWN_EMPIRICAL_STATUSES = frozenset(
    {"validated", "empirical_not_run", "empirical_failed"}
)
NOT_VALIDATED_EMPIRICAL_STATUSES = frozenset(
    {"empirical_not_run", "empirical_failed"}
)

EMPIRICAL_NOT_RUN_REASON = "sparse directional triggers"

_STRATEGY_ID_RE = re.compile(r"^k6_mtf:(?P<secondary>.+)$")

# Generic local-absolute-path matcher (NOT a real path; token fragments
# Users/home/mnt are matcher literals). Used to reject any path-like
# string that leaks a local filesystem location into the v2 payload.
_LOCAL_ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/])"          # drive-letter absolute (C:\ or C:/)
    r"|(?:^[\\/])"                  # leading slash absolute
    r"|(?:/(?:Users|home|mnt)/)"   # posix user/home/mount roots
    r"|(?:\\)"                      # any backslash
)
_LOCAL_NAME_MARKERS = ("appdata", "miniconda")


class ValidationJoinError(Exception):
    """Raised when the validation/ranking join refuses to build a v2
    payload. Nothing is written when this is raised."""


# ---------------------------------------------------------------------------
# Small IO helpers
# ---------------------------------------------------------------------------


def compute_file_sha256(path: str | os.PathLike) -> str:
    """Raw-file SHA-256 hex digest, matching the promotion helper's
    ``_compute_sha256`` (raw bytes, 64 KiB chunks)."""
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: str | os.PathLike, *, label: str) -> Any:
    p = Path(path)
    if not p.is_file():
        raise ValidationJoinError(f"{label} not found: {p.as_posix()}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationJoinError(f"{label} unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationJoinError(f"{label} not valid JSON: {exc}") from exc


def load_validation_sidecar(path: str | os.PathLike) -> dict:
    """Load and shallow-shape-check a validation sidecar JSON object."""
    payload = _load_json(path, label="validation sidecar")
    if not isinstance(payload, dict):
        raise ValidationJoinError("validation sidecar root is not a JSON object")
    return payload


def _project_root() -> Path:
    # This module lives at <PROJECT_DIR>/utils/react_publish/.
    return Path(__file__).resolve().parents[2]


def _project_relative_or_none(
    path: Optional[str | os.PathLike], project_root: Optional[Path] = None,
) -> Optional[str]:
    """Return a project-relative POSIX path string, or None if the path
    is outside the project root / unresolvable. NEVER returns a local
    absolute path (privacy: source_* metadata must not leak machine
    paths)."""
    if path is None:
        return None
    if project_root is None:
        project_root = _project_root()
    p = Path(path)
    try:
        if p.is_absolute():
            rel = p.resolve().relative_to(project_root.resolve())
            return rel.as_posix()
        # Already-relative input: normalize separators, accept as-is.
        rel_posix = p.as_posix()
        if _LOCAL_ABS_PATH_RE.search(rel_posix):
            return None
        return rel_posix
    except ValueError:
        # Outside the project root -> do not leak an absolute path.
        return None


def _assert_no_local_path(value: Any, *, where: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        return
    if _LOCAL_ABS_PATH_RE.search(value):
        raise ValidationJoinError(
            f"{where} contains a local absolute path token: {value!r}"
        )
    lowered = value.lower()
    for marker in _LOCAL_NAME_MARKERS:
        if marker in lowered:
            raise ValidationJoinError(
                f"{where} contains a local path marker {marker!r}: {value!r}"
            )


# ---------------------------------------------------------------------------
# Stage-A exclusion normalization
# ---------------------------------------------------------------------------


def _normalize_stage_a_exclusions(
    supplied: Optional[Sequence[Any]],
    *,
    evidence_source: str,
) -> List[dict]:
    """Normalize a supplied Stage-A exclusion list into the v2
    drop-list record shape. Each entry may be a bare ticker string or a
    dict carrying ``secondary`` / ``reason`` / ``causes``."""
    out: List[dict] = []
    if not supplied:
        return out
    for entry in supplied:
        if isinstance(entry, str):
            sec = entry.strip()
            if not sec:
                continue
            out.append({
                "secondary": sec,
                "reason": "stage_a_unavailable",
                "causes": [],
                "evidence_source": evidence_source,
            })
            continue
        if isinstance(entry, Mapping):
            sec = str(entry.get("secondary") or "").strip()
            if not sec:
                raise ValidationJoinError(
                    "stage_a exclusion record missing 'secondary'"
                )
            causes_raw = entry.get("causes") or []
            causes: List[dict] = []
            for c in causes_raw:
                if not isinstance(c, Mapping):
                    continue
                rec = {
                    "ticker": c.get("ticker"),
                    "ticker_classification": c.get("ticker_classification"),
                    "dependent_role": c.get("dependent_role"),
                }
                if c.get("member_token") is not None:
                    rec["member_token"] = c.get("member_token")
                if c.get("member_protocol") is not None:
                    rec["member_protocol"] = c.get("member_protocol")
                causes.append(rec)
            out.append({
                "secondary": sec,
                "reason": entry.get("reason") or "stage_a_unavailable",
                "causes": causes,
                "evidence_source": entry.get("evidence_source") or evidence_source,
            })
            continue
        raise ValidationJoinError(
            f"unsupported stage_a exclusion entry type: {type(entry).__name__}"
        )
    return out


def _stage_a_exclusions_from_execute_summary(summary: Mapping[str, Any]) -> List[dict]:
    """Extract Stage-A exclusions from a recook execute-summary dict.

    Reads ``stageA.excluded_secondaries`` (per-secondary causes) and,
    when present, enriches each cause from the top-level ``exclusions``
    list (member_token / member_protocol). Never recovers/rotates.
    """
    stage_a = summary.get("stageA")
    if not isinstance(stage_a, Mapping):
        raise ValidationJoinError(
            "execute summary missing stageA block"
        )
    excluded = stage_a.get("excluded_secondaries")
    if not isinstance(excluded, list):
        raise ValidationJoinError(
            "execute summary stageA.excluded_secondaries is not a list"
        )
    # Index the top-level exclusions[] by (secondary, ticker) for member
    # token/protocol enrichment when available.
    enrich: Dict[tuple, dict] = {}
    top = summary.get("exclusions")
    if isinstance(top, list):
        for rec in top:
            if not isinstance(rec, Mapping):
                continue
            key = (rec.get("secondary"), rec.get("ticker"))
            enrich[key] = rec
    out: List[dict] = []
    for entry in excluded:
        if not isinstance(entry, Mapping):
            continue
        sec = str(entry.get("secondary") or "").strip()
        if not sec:
            continue
        causes: List[dict] = []
        reasons: List[str] = []
        for c in entry.get("causes") or []:
            if not isinstance(c, Mapping):
                continue
            ticker = c.get("ticker")
            classification = c.get("ticker_classification")
            rec = {
                "ticker": ticker,
                "ticker_classification": classification,
                "dependent_role": c.get("dependent_role"),
            }
            extra = enrich.get((sec, ticker))
            if isinstance(extra, Mapping):
                if extra.get("member_token") is not None:
                    rec["member_token"] = extra.get("member_token")
                if extra.get("member_protocol") is not None:
                    rec["member_protocol"] = extra.get("member_protocol")
            causes.append(rec)
            if classification:
                reasons.append(str(classification))
        reason = (
            "stage_a_unavailable:" + ",".join(sorted(set(reasons)))
            if reasons else "stage_a_unavailable"
        )
        out.append({
            "secondary": sec,
            "reason": reason,
            "causes": causes,
            "evidence_source": "execute_summary",
        })
    return out


# ---------------------------------------------------------------------------
# Core join
# ---------------------------------------------------------------------------


def _index_sidecar_strategies(validation_payload: Mapping[str, Any]) -> Dict[str, dict]:
    """Return {secondary: strategy_entry} parsed from sidecar
    ``strategies[].strategy_id`` (``k6_mtf:<SECONDARY>``). Raises on
    malformed ids or duplicate secondaries (fail-closed)."""
    strategies = validation_payload.get("strategies")
    if not isinstance(strategies, list):
        raise ValidationJoinError("validation sidecar 'strategies' is not a list")
    out: Dict[str, dict] = {}
    for entry in strategies:
        if not isinstance(entry, Mapping):
            raise ValidationJoinError("sidecar strategy entry is not an object")
        sid = entry.get("strategy_id")
        if not isinstance(sid, str):
            raise ValidationJoinError(
                f"sidecar strategy_id is not a string: {sid!r}"
            )
        m = _STRATEGY_ID_RE.match(sid)
        if not m:
            raise ValidationJoinError(
                f"sidecar strategy_id does not match 'k6_mtf:<SECONDARY>': {sid!r}"
            )
        sec = m.group("secondary")
        if sec in out:
            raise ValidationJoinError(
                f"duplicate sidecar strategy for secondary {sec!r}"
            )
        out[sec] = dict(entry)
    return out


def _is_finite_number(value: Any) -> bool:
    """True iff ``value`` is a real finite number (not bool, not None,
    not NaN, not +/-inf)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _derive_validation_outcome(
    empirical_status: Any, bh_q_value: Any, alpha: float,
) -> str:
    """Two-outcome derivation. Raises on unknown empirical status, and
    fails closed when a ``validated`` row lacks a real finite row-level
    q-value (missing / null / non-numeric / NaN / non-finite) -- such a
    row cannot be honestly classified board_validated vs not_validated."""
    if empirical_status not in KNOWN_EMPIRICAL_STATUSES:
        raise ValidationJoinError(
            f"unknown empirical_validation_status: {empirical_status!r}"
        )
    if empirical_status == "validated":
        if not _is_finite_number(bh_q_value):
            raise ValidationJoinError(
                "validated row requires a finite bh_q_value to derive "
                f"validation_outcome; got {bh_q_value!r}"
            )
        return (
            VALIDATION_OUTCOME_BOARD
            if float(bh_q_value) <= alpha else VALIDATION_OUTCOME_NOT
        )
    # empirical_not_run / empirical_failed: no validated-row q-value rule.
    return VALIDATION_OUTCOME_NOT


def _enriched_row(
    row: Mapping[str, Any],
    strat: Mapping[str, Any],
    *,
    alpha: float,
    validation_run_id: Optional[str],
    validation_artifact_sha256: Optional[str],
) -> dict:
    empirical_status = strat.get("empirical_validation_status")
    bh_q = strat.get("bh_q_value")
    outcome = _derive_validation_outcome(empirical_status, bh_q, alpha)
    not_run_reason = (
        EMPIRICAL_NOT_RUN_REASON
        if empirical_status == "empirical_not_run" else None
    )
    new_row = dict(row)  # preserve all v1 descriptive fields verbatim
    new_row.update({
        "validation_outcome": outcome,
        "empirical_validation_status": empirical_status,
        "empirical_p_value": strat.get("empirical_p_value"),
        "parametric_p_value": strat.get("parametric_p_value"),
        "bh_q_value": strat.get("bh_q_value"),
        "bonferroni_p_value": strat.get("bonferroni_p_value"),
        "bootstrap_sharpe_ci_lower": strat.get("bootstrap_sharpe_ci_lower"),
        "bootstrap_sharpe_ci_upper": strat.get("bootstrap_sharpe_ci_upper"),
        "empirical_not_run_reason": not_run_reason,
        "validation_trigger_days": strat.get("trigger_days"),
        "validation_strategy_id": strat.get("strategy_id"),
        "validation_run_id": validation_run_id,
        "validation_artifact_sha256": validation_artifact_sha256,
    })
    return new_row


def build_k6_mtf_ranking_v2_fixture(
    ranking_payload: Mapping[str, Any],
    validation_payload: Mapping[str, Any],
    *,
    validation_sidecar_sha256: str,
    stage_a_excluded_secondaries: Optional[Sequence[Any]] = None,
    rng_seed: Optional[int] = None,
    source_sidecar_path: Optional[str] = None,
    source_ranking_path: Optional[str] = None,
    stage_a_evidence_source: str = "supplied_context",
) -> dict:
    """Join ``validation_payload`` onto ``ranking_payload`` and return a
    ``k6_mtf_ranking_v2`` fixture dict. Fail-closed on any mismatch."""
    if not isinstance(ranking_payload, Mapping):
        raise ValidationJoinError("ranking payload is not a JSON object")
    if not isinstance(validation_payload, Mapping):
        raise ValidationJoinError("validation payload is not a JSON object")

    # --- top-level gate checks ------------------------------------------
    if ranking_payload.get("schema_version") != V1_SCHEMA_VERSION:
        raise ValidationJoinError(
            "ranking artifact schema_version must be "
            f"{V1_SCHEMA_VERSION!r}; got {ranking_payload.get('schema_version')!r}"
        )
    vstatus = validation_payload.get("validation_status")
    if vstatus != "valid":
        raise ValidationJoinError(
            f"validation_status must be 'valid'; got {vstatus!r}"
        )
    alpha_raw = validation_payload.get("multiple_comparisons_control_alpha")
    if not isinstance(alpha_raw, (int, float)):
        raise ValidationJoinError(
            "validation sidecar missing multiple_comparisons_control_alpha"
        )
    alpha = float(alpha_raw)

    rows = ranking_payload.get("per_secondary")
    if not isinstance(rows, list) or not rows:
        raise ValidationJoinError("ranking per_secondary must be a non-empty list")

    # Duplicate ranked secondaries are rejected.
    seen_secs: set = set()
    for r in rows:
        if not isinstance(r, Mapping):
            raise ValidationJoinError("per_secondary entry is not an object")
        sec = r.get("secondary")
        if not isinstance(sec, str) or not sec:
            raise ValidationJoinError(f"per_secondary entry has no secondary: {r!r}")
        if sec in seen_secs:
            raise ValidationJoinError(f"duplicate ranking secondary: {sec!r}")
        seen_secs.add(sec)

    sidecar_by_sec = _index_sidecar_strategies(validation_payload)
    validation_run_id = validation_payload.get("run_id")

    # --- per-row join (every ranked row must have a sidecar entry) ------
    matched_secs: set = set()
    enriched_rows: List[dict] = []
    for r in rows:
        sec = r["secondary"]
        strat = sidecar_by_sec.get(sec)
        if strat is None:
            raise ValidationJoinError(
                f"ranking row {sec!r} has no matching sidecar strategy"
            )
        matched_secs.add(sec)
        enriched_rows.append(_enriched_row(
            r, strat,
            alpha=alpha,
            validation_run_id=validation_run_id,
            validation_artifact_sha256=validation_sidecar_sha256,
        ))

    # Sidecar entries with no matching ranking row are refused
    # (fail-closed; the 205 run is exactly 1:1).
    extra = sorted(set(sidecar_by_sec.keys()) - matched_secs)
    if extra:
        raise ValidationJoinError(
            "sidecar has strategies with no matching ranking row: "
            f"{extra!r}"
        )

    # --- counts / summary -----------------------------------------------
    board = sum(
        1 for r in enriched_rows
        if r["validation_outcome"] == VALIDATION_OUTCOME_BOARD
    )
    not_validated = len(enriched_rows) - board
    empirical_status_counts: Dict[str, int] = {}
    for r in enriched_rows:
        k = str(r.get("empirical_validation_status"))
        empirical_status_counts[k] = empirical_status_counts.get(k, 0) + 1

    stage_a = _normalize_stage_a_exclusions(
        stage_a_excluded_secondaries, evidence_source=stage_a_evidence_source,
    )

    # --- resolve rng_seed: explicit arg wins, else sidecar field --------
    resolved_seed: Optional[int]
    if rng_seed is not None:
        resolved_seed = int(rng_seed)
    elif validation_payload.get("rng_seed") is not None:
        resolved_seed = int(validation_payload.get("rng_seed"))
    else:
        resolved_seed = None

    rel_sidecar = _project_relative_or_none(source_sidecar_path)
    rel_ranking = _project_relative_or_none(source_ranking_path)
    _assert_no_local_path(rel_sidecar, where="validation_metadata.source_sidecar_path")
    _assert_no_local_path(rel_ranking, where="validation_metadata.source_ranking_path")

    validation_metadata = {
        "run_id": validation_run_id,
        "artifact_sha256": validation_sidecar_sha256,
        "validation_status": vstatus,
        "validated_as_of_utc": (
            validation_payload.get("evaluation_time")
        ),
        "data_available_through": validation_payload.get("data_available_through"),
        "n_strategies_tested": validation_payload.get("n_strategies_tested"),
        "n_strategies_reported": validation_payload.get("n_strategies_reported"),
        "n_permutations": validation_payload.get("n_permutations"),
        "n_bootstrap_samples": validation_payload.get("n_bootstrap_samples"),
        "walk_forward_n_folds": validation_payload.get("walk_forward_n_folds"),
        "bootstrap_ci_level": validation_payload.get("bootstrap_ci_level"),
        "multiple_comparisons_control_alpha": alpha,
        "multiple_comparisons_control_method": validation_payload.get(
            "multiple_comparisons_control_method"
        ),
        "multiple_comparisons_supplementary": validation_payload.get(
            "multiple_comparisons_supplementary"
        ),
        "validation_contract_version": validation_payload.get(
            "validation_contract_version"
        ),
        "validation_methodology_version": validation_payload.get(
            "validation_methodology_version"
        ),
        "rng_seed": resolved_seed,
        "source_sidecar_path": rel_sidecar,
        "source_ranking_path": rel_ranking,
    }

    validation_summary = {
        "board_validated_count": board,
        "not_validated_count": not_validated,
        "empirical_status_counts": empirical_status_counts,
        "stage_a_excluded_count": len(stage_a),
        "displayed_ranked_count": len(enriched_rows),
        "validation_non_reported_count": not_validated,
    }

    payload: Dict[str, Any] = {
        "schema_version": V2_SCHEMA_VERSION,
        "generated_at_utc": ranking_payload.get("generated_at_utc"),
        "run_id": ranking_payload.get("run_id"),
        "secondaries_requested": list(ranking_payload.get("secondaries_requested") or []),
        "secondaries_ranked": list(ranking_payload.get("secondaries_ranked") or []),
        "per_secondary": enriched_rows,
        "issues": list(ranking_payload.get("issues") or []),
        "validation_metadata": validation_metadata,
        "validation_summary": validation_summary,
        "stage_a_excluded_secondaries": stage_a,
    }
    return payload


# ---------------------------------------------------------------------------
# Load-and-build orchestration (optional file write, never public fixture)
# ---------------------------------------------------------------------------


def _refuse_public_fixture_path(output_path: Path) -> None:
    parts = [p.lower() for p in output_path.resolve().parts]
    # Refuse any write that lands under frontend/public.
    for i in range(len(parts) - 1):
        if parts[i] == "frontend" and parts[i + 1] == "public":
            raise ValidationJoinError(
                "refusing to write under frontend/public (no public fixture "
                "writes in the join helper)"
            )


def _atomic_write_json(payload: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=output_path.name + ".", suffix=".part", dir=str(output_path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(str(tmp_path), str(output_path))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def load_and_build_k6_mtf_ranking_v2(
    ranking_path: str | os.PathLike,
    validation_sidecar_path: str | os.PathLike,
    *,
    expected_validation_sidecar_sha256: Optional[str] = None,
    stage_a_excluded_secondaries: Optional[Sequence[Any]] = None,
    execute_summary_path: Optional[str | os.PathLike] = None,
    output_path: Optional[str | os.PathLike] = None,
) -> dict:
    """Load both artifacts, optionally verify the sidecar SHA, resolve
    Stage-A exclusions (execute-summary preferred, supplied list
    fallback), build the v2 payload, and optionally write it to an
    explicit non-public output path.

    Never writes the real public fixture. ``output_path`` under
    ``frontend/public`` is refused.
    """
    sidecar_sha = compute_file_sha256(validation_sidecar_path)
    if expected_validation_sidecar_sha256 is not None:
        expected = str(expected_validation_sidecar_sha256).strip().lower()
        if expected != sidecar_sha:
            raise ValidationJoinError(
                "validation sidecar SHA-256 mismatch: expected "
                f"{expected}, computed {sidecar_sha}"
            )

    ranking_payload = _load_json(ranking_path, label="ranking artifact")
    if not isinstance(ranking_payload, dict):
        raise ValidationJoinError("ranking artifact root is not a JSON object")
    validation_payload = load_validation_sidecar(validation_sidecar_path)

    # Stage-A exclusions: execute-summary preferred, supplied fallback.
    stage_a_list: Optional[Sequence[Any]] = stage_a_excluded_secondaries
    evidence_source = "supplied_context"
    if execute_summary_path is not None:
        summary = _load_json(execute_summary_path, label="execute summary")
        if not isinstance(summary, dict):
            raise ValidationJoinError("execute summary root is not a JSON object")
        stage_a_list = _stage_a_exclusions_from_execute_summary(summary)
        evidence_source = "execute_summary"

    payload = build_k6_mtf_ranking_v2_fixture(
        ranking_payload,
        validation_payload,
        validation_sidecar_sha256=sidecar_sha,
        stage_a_excluded_secondaries=stage_a_list,
        rng_seed=None,  # resolved from sidecar rng_seed inside the builder
        source_sidecar_path=str(validation_sidecar_path),
        source_ranking_path=str(ranking_path),
        stage_a_evidence_source=evidence_source,
    )

    if output_path is not None:
        out = Path(output_path)
        _refuse_public_fixture_path(out)
        _atomic_write_json(payload, out)

    return payload
