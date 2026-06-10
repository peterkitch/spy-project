"""Shared combine/proof-assembly primitive for the autonomous publish tail.

This module is caller-neutral: BOTH the Build-and-Rank publish path and the
future Re-rank publish path call ``combine_and_assemble`` with the same
arguments. The ONLY difference between callers is the upstream row set they
supply (a subset of fresh rows for Build-and-Rank; potentially all rows for
Re-rank). This primitive performs the SAME combine + proof assembly either
way and never branches on caller identity.

It takes a prior published full-board v2 fixture plus a freshly-validated row
set and assembles a merged full-board v2 fixture and the four proof artifacts
the UNCHANGED ``promote_k6_mtf_artifact`` tooling accepts as a normal
full-board public promotion:

  * merged slim v2 fixture (carried Blob metadata preserved; fresh rows
    stamped from caller-supplied CCC records),
  * composite validation sidecar (one strategy per merged row; carried
    verdicts lifted/reconstructed, fresh verdicts from the fresh sidecar;
    rng_seed null; an additive top-level composite-provenance inventory),
  * composite Phase-5 report + matching report manifest (honest
    carry-forward prose; binds report <-> manifest <-> sidecar <-> fixture),
  * combined CCC verification manifest (carried records referenced, never
    re-uploaded/re-GET; fresh records from the caller).

It is the STANDALONE assembler only. It performs NO network, NO Blob
upload/GET, NO promote --write, NO public fixture write, NO git. It writes
only under the caller-supplied ``output_dir`` and refuses any output under
``frontend/public``. Fail-closed throughout.

The composite-sidecar run-level fields follow the unseeded carry-forward
model (see the published board, which is already rng_seed=null): the single
top-level rng_seed is null (literally true for every row), run_id is the
assembly run id, evaluation_time is the assembly time, and true per-row
provenance is preserved in the merged fixture (validation_run_id,
validation_artifact_sha256, validated_as_of_utc) plus the additive top-level
``composite_validation_provenance`` inventory.

ASCII-only. Stdlib only. Promote validator functions are imported lazily,
read-only, ONLY for an optional internal self-check (no write/network path is
reached); the import is never required for assembly.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION_V2 = "k6_mtf_ranking_v2"
PROMOTION_MANIFEST_SCHEMA_VERSION = "k6_mtf_promotion_manifest_v1"
PHASE5_REPORT_MANIFEST_SCHEMA = "k6_mtf_phase5_report_manifest"
PHASE5_REPORT_MANIFEST_VERSION = "v1"
CCC_VERIFICATION_SCHEMA_VERSION = "k6_mtf_ccc_sidecar_verification_v1"
CCC_SIDECAR_SCHEMA_VERSION = "k6_mtf_ccc_series_sidecar_v1"
CCC_SERIES_SOURCE_BLOB = "vercel_blob"

COMPOSITE_PROVENANCE_SCHEMA = "k6_mtf_composite_validation_provenance_v1"
COMPOSITE_PROOF_TYPE = "multi_run_carry_forward"

# Per-row fields the merged fixture rows must carry (mirrors promote's
# PER_SECONDARY_REQUIRED + PER_SECONDARY_V2_VALIDATION_REQUIRED). Verified
# locally so assembly fails closed even when the promote self-check is skipped.
_PER_ROW_REQUIRED = (
    "secondary", "rank", "status", "history_artifact_path", "history_as_of_date",
    "current_snapshot", "k6_stack", "sharpe_k6_mtf", "total_capture_pct",
    "avg_capture_pct", "stddev_pct", "match_count", "capture_count",
    "trade_count", "no_trade_count", "skipped_capture_count", "win_count",
    "loss_count", "win_pct", "low_sample_warning", "ccc_series", "issues",
)
_PER_ROW_V2_VALIDATION_REQUIRED = (
    "validation_outcome", "empirical_validation_status", "empirical_p_value",
    "parametric_p_value", "bh_q_value", "bonferroni_p_value",
    "bootstrap_sharpe_ci_lower", "bootstrap_sharpe_ci_upper",
    "empirical_not_run_reason", "validation_trigger_days",
    "validation_strategy_id", "validation_run_id", "validation_artifact_sha256",
)
_CCC_ROW_METADATA = (
    "ccc_series_source", "ccc_series_sidecar_schema_version", "ccc_series_url",
    "ccc_series_pathname", "ccc_series_sha256", "ccc_series_byte_size",
    "ccc_series_points", "ccc_series_first_date", "ccc_series_last_date",
)
_CCC_RECORD_FIELDS = (
    "secondary", "pathname", "url", "sha256", "byte_size", "points",
    "first_date", "last_date", "reused", "get_verified",
)
# CCC verification record field -> slim-fixture row field (exact-equality map,
# mirrors promote.validate_ccc_verification_against_fixture).
_CCC_FIELD_MAP = {
    "pathname": "ccc_series_pathname",
    "url": "ccc_series_url",
    "sha256": "ccc_series_sha256",
    "byte_size": "ccc_series_byte_size",
    "points": "ccc_series_points",
    "first_date": "ccc_series_first_date",
    "last_date": "ccc_series_last_date",
}

# Full methodology lock set (honesty, not just promote-binding). Names are the
# validation-sidecar field names. The first group is also present in the
# fixture validation_metadata; the second group lives only in the sidecar.
#
# walk_forward_n_folds is intentionally NOT in this set: it is DERIVED from each
# validation cohort's available history grid (more/deeper-history secondaries ->
# more folds), not a regime parameter. A deep-history carried board (e.g. 99
# folds) may legitimately merge freshly-built shorter-history rows (e.g. 15
# folds) when the true methodology/regime fields below all match. The fold count
# is therefore advisory (recorded, never gating); see the board_folds derivation
# in combine_and_assemble.
_METHODOLOGY_LOCK_FIXTURE = (
    "validation_contract_version", "validation_methodology_version",
    "multiple_comparisons_control_method", "multiple_comparisons_control_alpha",
    "n_permutations", "n_bootstrap_samples",
)
_METHODOLOGY_LOCK_SIDECAR_ONLY = (
    "multiple_comparisons_supplementary", "bootstrap_ci_level",
    "borderline_tolerance_multiplier", "outcome_windows", "baseline_method",
)
_METHODOLOGY_LOCK_ALL = _METHODOLOGY_LOCK_FIXTURE + _METHODOLOGY_LOCK_SIDECAR_ONLY


class CombineError(Exception):
    """Fail-closed refusal raised by the combine/proof assembler."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CombineError(f"{label} not found: {Path(path).as_posix()}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CombineError(f"{label} unreadable/invalid JSON: {exc}") from exc


def _dump_bytes(obj: Any) -> bytes:
    """Deterministic, LF, trailing-newline JSON bytes (the bytes we hash)."""
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical_lf_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _lf_sha256_file(path: Path) -> str:
    """LF-normalized SHA (mirrors promote._compute_sha256_lf, used for the
    prior fixture <-> promotion-manifest source_sha256 check)."""
    return hashlib.sha256(_canonical_lf_bytes(Path(path).read_bytes())).hexdigest()


# --- member-aware exclusion scanner (mirrors the orchestrator; copied locally
#     so the primitive does not import the orchestrator's operational code) ---
_PROTOCOL_RE = re.compile(r"\[[DI]\]$")
_SEED_SUFFIX_RE = re.compile(r"-[DI]$")


def _norm_ticker(raw: Any) -> str:
    return str(raw).strip().upper()


def _member_tokens(token: Any) -> set:
    out: set = set()
    s = str(token).strip()
    if not s:
        return out
    out.add(_norm_ticker(s))
    out.add(_norm_ticker(_PROTOCOL_RE.sub("", s)))
    for piece in s.split("_"):
        p = piece.strip()
        if not p:
            continue
        out.add(_norm_ticker(p))
        nb = _PROTOCOL_RE.sub("", p)
        out.add(_norm_ticker(nb))
        out.add(_norm_ticker(_SEED_SUFFIX_RE.sub("", nb)))
    out.discard("")
    return out


def _collect_strings(obj: Any, out: list) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            out.append(str(k))
            _collect_strings(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_strings(v, out)


def _scan_excluded(fixture: Mapping[str, Any], excluded: set) -> set:
    """Member-aware scan of the merged fixture for excluded tickers, skipping
    the structured ``stage_a_excluded_secondaries`` disclosure block."""
    if not excluded:
        return set()
    excl_norm = {_norm_ticker(t) for t in excluded}
    scan_obj = {k: v for k, v in fixture.items()
                if k != "stage_a_excluded_secondaries"}
    strings: list = []
    _collect_strings(scan_obj, strings)
    found: set = set()
    for s in strings:
        for variant in _member_tokens(s):
            if variant in excl_norm:
                found.add(variant)
    return found


# ---------------------------------------------------------------------------
# Canonical comparator (mirrors k6_mtf_ranking_engine._rank_records)
# ---------------------------------------------------------------------------


def _sort_and_rank(rows: list) -> list:
    """Sort ranked numeric-Sharpe rows by (-sharpe, -total_capture, secondary),
    append unranked (null-Sharpe) rows by secondary, reassign rank 1..N over
    ranked rows. Mutates ``rank``; returns the ordered list."""
    ranked = [r for r in rows
              if r.get("status") == "ranked" and r.get("sharpe_k6_mtf") is not None]
    nulls = [r for r in rows if r.get("status") == "unranked"]
    leftover = [r for r in rows if r not in ranked and r not in nulls]
    if leftover:
        raise CombineError(
            "merged board contains rows that are neither ranked-with-Sharpe nor "
            f"unranked: {sorted(str(r.get('secondary')) for r in leftover)!r}")
    ranked.sort(key=lambda r: (-float(r["sharpe_k6_mtf"]),
                               -float(r.get("total_capture_pct") or 0.0),
                               str(r.get("secondary") or "")))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    nulls.sort(key=lambda r: str(r.get("secondary") or ""))
    return ranked + nulls


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def combine_and_assemble(
    *,
    prior_fixture_path: Any,
    prior_promotion_manifest_path: Any,
    prior_ccc_verification_manifest_path: Any,
    fresh_rows: Sequence[Mapping[str, Any]],
    fresh_validation_sidecar: Mapping[str, Any],
    fresh_ccc_records: Sequence[Mapping[str, Any]],
    assembly_run_id: str,
    assembled_at_utc: str,
    output_dir: Any,
    excluded_tickers: Iterable[str] = (),
    prior_validation_sidecar_path: Any = None,
    project_root: Any = None,
    reverify_carried_ccc: bool = False,
    run_self_check: bool = True,
) -> dict:
    """Assemble a merged full-board v2 fixture and promote-ready proof
    artifacts from a prior published board + a freshly-validated row set.

    Caller-neutral: Build-and-Rank passes a subset of fresh rows; Re-rank may
    pass all rows. Fail-closed. Writes only under ``output_dir``. No network,
    no Blob, no promote write, no git. Returns a summary dict with project-
    relative artifact paths and invariant counts.
    """
    if reverify_carried_ccc:
        raise CombineError(
            "reverify_carried_ccc=True is unsupported in this module: re-GET of "
            "carried CCC sidecars would require network; refusing (future hook)")

    project_root = (Path(project_root).resolve() if project_root is not None
                    else Path(__file__).resolve().parent)
    output_dir = Path(output_dir).resolve()

    # --- output_dir safety: must be under project_root, never under a public
    #     fixture path. ------------------------------------------------------
    try:
        rel_out = output_dir.relative_to(project_root)
    except ValueError as exc:
        raise CombineError(
            "output_dir must be under project_root") from exc
    rel_parts = rel_out.parts
    if rel_parts[:2] == ("frontend", "public"):
        raise CombineError(
            "refusing output_dir under frontend/public (no public fixture writes)")
    if not rel_parts or rel_parts[0] not in ("output", "md_library"):
        raise CombineError(
            "output_dir must be under output/ or md_library/ (project-relative) "
            f"so proof-manifest paths are valid; got {rel_out.as_posix()!r}")

    # =====================================================================
    # 1. Load + validate the prior published board.
    # =====================================================================
    prior_fixture = _load_json(prior_fixture_path, "prior fixture")
    if not isinstance(prior_fixture, dict) or (
            prior_fixture.get("schema_version") != SCHEMA_VERSION_V2):
        raise CombineError(
            "prior fixture is not a k6_mtf_ranking_v2 object")
    prior_rows = prior_fixture.get("per_secondary")
    if not isinstance(prior_rows, list) or not prior_rows:
        raise CombineError("prior fixture per_secondary must be a non-empty list")

    prior_promo = _load_json(prior_promotion_manifest_path,
                             "prior promotion manifest")
    if not isinstance(prior_promo, dict):
        raise CombineError("prior promotion manifest is not a JSON object")
    prior_source_sha = str(prior_promo.get("source_sha256") or "").strip().lower()
    actual_prior_lf_sha = _lf_sha256_file(Path(prior_fixture_path))
    if prior_source_sha != actual_prior_lf_sha:
        raise CombineError(
            "prior fixture canonical-LF SHA does not match prior promotion "
            f"manifest source_sha256: manifest {prior_source_sha!r}, fixture "
            f"{actual_prior_lf_sha!r}")

    prior_meta = prior_fixture.get("validation_metadata") or {}

    # --- index prior rows + fresh inputs (dup-checked); decide carried set --
    prior_by_sec: dict = {}
    for r in prior_rows:
        if not isinstance(r, dict):
            raise CombineError("prior fixture row is not a mapping")
        sec = _norm_ticker(r.get("secondary"))
        if not sec:
            raise CombineError("prior fixture row has no secondary")
        if sec in prior_by_sec:
            raise CombineError(f"duplicate secondary in prior fixture: {sec!r}")
        prior_by_sec[sec] = r

    fresh_sc = fresh_validation_sidecar
    if not isinstance(fresh_sc, dict):
        raise CombineError("fresh_validation_sidecar must be a mapping")
    if fresh_sc.get("validation_status") != "valid":
        raise CombineError("fresh_validation_sidecar validation_status != 'valid'")
    fresh_run_id = fresh_sc.get("run_id")

    fresh_by_sec: dict = {}
    for r in fresh_rows:
        if not isinstance(r, dict):
            raise CombineError("fresh row is not a mapping")
        sec = _norm_ticker(r.get("secondary"))
        if not sec:
            raise CombineError("fresh row has no secondary")
        if sec in fresh_by_sec:
            raise CombineError(f"duplicate secondary in fresh_rows: {sec!r}")
        if r.get("status") != "ranked":
            raise CombineError(f"fresh row {sec!r} status != 'ranked'")
        fresh_by_sec[sec] = r

    fresh_ccc_by_sec: dict = {}
    for rec in fresh_ccc_records:
        if not isinstance(rec, dict):
            raise CombineError("fresh CCC record is not a mapping")
        sec = _norm_ticker(rec.get("secondary"))
        if sec in fresh_ccc_by_sec:
            raise CombineError(f"duplicate secondary in fresh_ccc_records: {sec!r}")
        fresh_ccc_by_sec[sec] = rec

    carried_secs = {s for s in prior_by_sec if s not in fresh_by_sec}
    has_carried = bool(carried_secs)

    # =====================================================================
    # 2. rng_seed (unseeded-only) + prior-sidecar requirement + methodology
    #    lock + prior/fresh sidecar bindings + prior CCC validation.
    # =====================================================================
    prior_sc = (_load_json(prior_validation_sidecar_path, "prior validation sidecar")
                if prior_validation_sidecar_path is not None else None)
    if prior_meta.get("rng_seed") is not None:
        raise CombineError(
            "prior board rng_seed is non-null; multi-run seed provenance needs a "
            "separate schema decision (unseeded-only supported)")
    if prior_sc is not None and prior_sc.get("rng_seed") is not None:
        raise CombineError("prior validation sidecar rng_seed is non-null")
    if fresh_sc.get("rng_seed") is not None:
        raise CombineError(
            "fresh validation sidecar rng_seed is non-null; unseeded-only "
            "supported in this primitive")

    # Carried rows REQUIRE a prior validation sidecar: fixture-visible fields
    # cannot prove all methodology fields, and per-strategy provenance must be
    # bound. Prior-sidecar-absent assembly is allowed ONLY when there are no
    # carried rows (all-fresh / Re-rank-style). No prior-fixture-only carried
    # reconstruction in this primitive.
    if has_carried and prior_sc is None:
        raise CombineError(
            "carried rows present but prior_validation_sidecar_path is absent; "
            "carry-forward requires a prior validation sidecar to prove the full "
            "methodology lock and per-strategy provenance")

    # Fixture-available methodology fields (prior board metadata vs fresh).
    for f in _METHODOLOGY_LOCK_FIXTURE:
        pv, fv = prior_meta.get(f), fresh_sc.get(f)
        if pv != fv:
            raise CombineError(
                f"methodology lock mismatch on {f!r}: prior board {pv!r} != "
                f"fresh {fv!r}")
    # Full lock incl. promote-unbound honesty fields, against the prior sidecar.
    methodology_fully_locked = prior_sc is not None
    if prior_sc is not None:
        for f in _METHODOLOGY_LOCK_ALL:
            pv, fv = prior_sc.get(f), fresh_sc.get(f)
            if pv != fv:
                raise CombineError(
                    f"methodology lock mismatch on {f!r}: prior sidecar {pv!r} "
                    f"!= fresh {fv!r}")
    locked = {f: fresh_sc.get(f) for f in _METHODOLOGY_LOCK_ALL}
    alpha = float(locked["multiple_comparisons_control_alpha"])

    # walk_forward_n_folds is advisory/data-derived (see _METHODOLOGY_LOCK_*).
    # Board-wide value: the fresh count when there are no carried rows; the
    # shared integer when carried and fresh cohorts agree; None ("composite /
    # mixed by validation cohort") when they differ. Prefer the prior sidecar's
    # count as the carried-cohort source; fall back to prior fixture metadata
    # only when no prior sidecar is supplied. Differing folds never raise.
    fresh_folds = fresh_sc.get("walk_forward_n_folds")
    if has_carried:
        prior_folds = (prior_sc.get("walk_forward_n_folds")
                       if prior_sc is not None
                       else prior_meta.get("walk_forward_n_folds"))
        board_folds = fresh_folds if prior_folds == fresh_folds else None
    else:
        board_folds = fresh_folds

    # --- Fix 3: bind the supplied prior sidecar to the prior fixture --------
    prior_strat_by_sec: dict = {}
    prior_sc_sha = None
    if prior_sc is not None:
        prior_sc_sha = _sha256_file(Path(prior_validation_sidecar_path))
        declared = str(prior_meta.get("artifact_sha256") or "").strip().lower()
        if prior_sc_sha != declared:
            raise CombineError(
                "prior validation sidecar SHA-256 does not match prior fixture "
                f"validation_metadata.artifact_sha256: file {prior_sc_sha}, "
                f"metadata {declared!r}")
        if (prior_sc.get("run_id") is not None
                and prior_meta.get("run_id") is not None
                and prior_sc.get("run_id") != prior_meta.get("run_id")):
            raise CombineError(
                "prior validation sidecar run_id does not match prior fixture "
                f"validation_metadata.run_id: sidecar {prior_sc.get('run_id')!r}, "
                f"fixture {prior_meta.get('run_id')!r}")
        prior_strat_by_sec = _index_strategies(
            prior_sc.get("strategies"), set(prior_by_sec), "prior")

    # --- Fix 2: bind the fresh sidecar to the fresh rows (strict 1:1) -------
    fresh_strat_by_sec = _index_strategies(
        fresh_sc.get("strategies"), set(fresh_by_sec), "fresh")
    for sec, frow in fresh_by_sec.items():
        want_sid = f"k6_mtf:{frow.get('secondary')}"
        if frow.get("validation_strategy_id") != want_sid:
            raise CombineError(
                f"fresh row {sec!r} validation_strategy_id "
                f"{frow.get('validation_strategy_id')!r} != {want_sid!r}")
        if frow.get("validation_run_id") != fresh_run_id:
            raise CombineError(
                f"fresh row {sec!r} validation_run_id "
                f"{frow.get('validation_run_id')!r} != fresh sidecar run_id "
                f"{fresh_run_id!r}")

    # --- Fix 4: fully validate the prior CCC manifest vs the prior fixture --
    prior_ccc_by_sec = _validate_prior_ccc_manifest(
        prior_ccc_manifest=_load_json(prior_ccc_verification_manifest_path,
                                      "prior CCC verification manifest"),
        prior_fixture=prior_fixture, prior_promo=prior_promo)

    # =====================================================================
    # 3. Per-row provenance + upsert.
    # =====================================================================
    board_asof = prior_meta.get("validated_as_of_utc")
    merged_rows: list = []
    provenance_rows: dict = {}

    # Carried rows: every prior secondary NOT superseded by a fresh row.
    for sec in carried_secs:
        prow = prior_by_sec[sec]
        row = dict(prow)  # copy; only rank + validated_as_of_utc may change
        if "validated_as_of_utc" not in row or row.get("validated_as_of_utc") in (None, ""):
            if not board_asof:
                raise CombineError(
                    f"carried row {sec!r} has no validated_as_of_utc and prior "
                    "board-wide validated_as_of_utc is missing; cannot backfill")
            row["validated_as_of_utc"] = board_asof
        _assert_carried_unchanged(prow, row)
        _bind_carried_provenance(
            sec, row, prior_strat_by_sec[sec],
            prior_sc_run_id=(prior_sc.get("run_id") if prior_sc else None),
            prior_sc_sha=prior_sc_sha)
        merged_rows.append(row)
        provenance_rows[sec] = {"role": "carried", "ccc_source": "carried"}

    # Fresh rows: add new / upsert existing.
    fresh_eval_time = fresh_sc.get("evaluation_time")
    for sec, frow in fresh_by_sec.items():
        row = dict(frow)
        crec = fresh_ccc_by_sec.get(sec)
        if crec is None:
            raise CombineError(f"fresh row {sec!r} has no matching fresh_ccc_record")
        _stamp_fresh_ccc(row, crec)
        row["validated_as_of_utc"] = fresh_eval_time
        _assert_row_fields(sec, row)
        merged_rows.append(row)
        provenance_rows[sec] = {"role": "fresh", "ccc_source": "fresh"}

    # No carried row dropped; no duplicate in the merged board.
    carried_present = {s for s, p in provenance_rows.items() if p["role"] == "carried"}
    if carried_present != carried_secs:
        raise CombineError(
            "carried-row set changed during merge: missing "
            f"{sorted(carried_secs - carried_present)!r}")
    merged_secs = [_norm_ticker(r["secondary"]) for r in merged_rows]
    if len(merged_secs) != len(set(merged_secs)):
        raise CombineError("duplicate secondary in merged board")

    # =====================================================================
    # 5. Sort + rank (canonical comparator).
    # =====================================================================
    for r in merged_rows:
        if r.get("status") != "ranked":
            raise CombineError(
                f"merged row {r.get('secondary')!r} is not 'ranked' (first "
                "primitive assembles a ranked-only board)")
    merged_rows = _sort_and_rank(merged_rows)
    ranks = [r["rank"] for r in merged_rows]
    if ranks != list(range(1, len(merged_rows) + 1)):
        raise CombineError("merged rank sequence is not contiguous 1..N")
    ranked_secs = [r["secondary"] for r in merged_rows]

    # =====================================================================
    # 4. Composite validation sidecar (one strategy per merged row). Carried
    #    strategies are lifted from the (SHA/run/1:1-bound) prior sidecar;
    #    fresh strategies from the fresh sidecar. No prior-fixture-only
    #    reconstruction; every merged row strategy_id == k6_mtf:<SECONDARY>.
    # =====================================================================
    composite_strategies: list = []
    for row in merged_rows:
        sec = _norm_ticker(row["secondary"])
        want_sid = f"k6_mtf:{row['secondary']}"
        if row.get("validation_strategy_id") != want_sid:
            raise CombineError(
                f"merged row {sec!r} validation_strategy_id "
                f"{row.get('validation_strategy_id')!r} != {want_sid!r}")
        role = provenance_rows[sec]["role"]
        src = (fresh_strat_by_sec[sec] if role == "fresh"
               else prior_strat_by_sec[sec])
        strat = _strategy_from_source(src, row)
        # provenance is additive + promote-ignored.
        strat["validation_source"] = {
            "role": role,
            "run_id": row.get("validation_run_id"),
            "artifact_sha256": row.get("validation_artifact_sha256"),
            "validated_as_of_utc": row.get("validated_as_of_utc"),
        }
        composite_strategies.append(strat)

    sidecar_counts = _derive_counts(composite_strategies, alpha)
    if sidecar_counts["tested"] != len(merged_rows):
        raise CombineError("composite sidecar strategy count != merged row count")

    source_runs = _source_run_inventory(
        prior_meta, prior_sc, fresh_sc, provenance_rows)
    composite_sidecar = {
        "validation_contract_version": locked["validation_contract_version"],
        "validation_methodology_version": locked["validation_methodology_version"],
        "validation_status": "valid",
        "run_id": assembly_run_id,
        "producer_engine": "crunch_combine_proof",
        "app_surface": "k6_mtf_ranking",
        "evaluation_time": assembled_at_utc,
        "rng_seed": None,
        "multiple_comparisons_control_method":
            locked["multiple_comparisons_control_method"],
        "multiple_comparisons_control_alpha":
            locked["multiple_comparisons_control_alpha"],
        "multiple_comparisons_supplementary":
            locked["multiple_comparisons_supplementary"],
        "n_permutations": locked["n_permutations"],
        "n_bootstrap_samples": locked["n_bootstrap_samples"],
        "bootstrap_ci_level": locked["bootstrap_ci_level"],
        "borderline_tolerance_multiplier": locked["borderline_tolerance_multiplier"],
        "walk_forward_n_folds": board_folds,
        "outcome_windows": locked["outcome_windows"],
        "baseline_method": locked["baseline_method"],
        "n_strategies_tested": sidecar_counts["tested"],
        "n_strategies_reported": sidecar_counts["board_validated"],
        "n_strategies_survived_empirical": sidecar_counts["empirical_validated"],
        "issues": [],
        "strategies": composite_strategies,
        # Additive top-level provenance (promote-ignored; makes the null seed
        # and the multi-run assembly explicit and recoverable).
        "composite_validation_provenance": {
            "schema_version": COMPOSITE_PROVENANCE_SCHEMA,
            "proof_type": COMPOSITE_PROOF_TYPE,
            "assembled_at_utc": assembled_at_utc,
            "assembly_run_id": assembly_run_id,
            "rng_seed_policy": (
                "null at top level: this is a multi-run carry-forward assembly; "
                "every source cohort is unseeded (rng_seed=null), so the single "
                "top-level rng_seed is literally true for all rows"),
            "methodology_compatibility": (
                "fully_locked_against_prior_sidecar" if methodology_fully_locked
                else "all_fresh_no_carried_no_prior_sidecar"),
            "source_validation_runs": source_runs,
        },
    }
    sidecar_bytes = _dump_bytes(composite_sidecar)
    sidecar_sha = _sha256_bytes(sidecar_bytes)

    # =====================================================================
    # 7. Merged fixture validation_metadata + summary.
    # =====================================================================
    board_validated = sum(1 for r in merged_rows
                          if r.get("validation_outcome") == "board_validated")
    not_validated = len(merged_rows) - board_validated
    emp_counts: dict = {}
    for r in merged_rows:
        st = r.get("empirical_validation_status")
        emp_counts[st] = emp_counts.get(st, 0) + 1
    merged_stage_a = [s for s in (prior_fixture.get("stage_a_excluded_secondaries")
                                  or [])
                      if _norm_ticker(s) not in set(merged_secs)]

    sidecar_rel = (rel_out / "composite_validation_sidecar.json").as_posix()
    fixture_rel = (rel_out / "merged_k6_mtf_ranking_v2.json").as_posix()
    merged_fixture = {
        "schema_version": SCHEMA_VERSION_V2,
        "generated_at_utc": assembled_at_utc,
        "run_id": assembly_run_id,
        "secondaries_requested": list(ranked_secs),
        "secondaries_ranked": list(ranked_secs),
        "per_secondary": merged_rows,
        "issues": [],
        "stage_a_excluded_secondaries": merged_stage_a,
        "validation_metadata": {
            "run_id": assembly_run_id,
            "artifact_sha256": sidecar_sha,
            "validation_status": "valid",
            "validated_as_of_utc": assembled_at_utc,
            "data_available_through": fresh_sc.get("data_available_through"),
            "n_strategies_tested": len(merged_rows),
            "n_strategies_reported": board_validated,
            "n_permutations": locked["n_permutations"],
            "n_bootstrap_samples": locked["n_bootstrap_samples"],
            "walk_forward_n_folds": board_folds,
            "bootstrap_ci_level": locked["bootstrap_ci_level"],
            "multiple_comparisons_control_alpha":
                locked["multiple_comparisons_control_alpha"],
            "multiple_comparisons_control_method":
                locked["multiple_comparisons_control_method"],
            "multiple_comparisons_supplementary":
                locked["multiple_comparisons_supplementary"],
            "validation_contract_version": locked["validation_contract_version"],
            "validation_methodology_version":
                locked["validation_methodology_version"],
            "rng_seed": None,
            "source_sidecar_path": sidecar_rel,
            "source_ranking_path": fixture_rel,
        },
        "validation_summary": {
            "board_validated_count": board_validated,
            "not_validated_count": not_validated,
            "empirical_status_counts": emp_counts,
            "stage_a_excluded_count": len(merged_stage_a),
            "displayed_ranked_count": len(merged_rows),
            "validation_non_reported_count": not_validated,
        },
    }

    # cross-check fixture counts == sidecar-derived counts (promote layer B).
    if board_validated != sidecar_counts["board_validated"]:
        raise CombineError(
            "fixture board_validated_count != sidecar-derived board_validated "
            f"({board_validated} != {sidecar_counts['board_validated']})")

    # =====================================================================
    # 8. Composite report + report manifest.
    # =====================================================================
    report_rel = (rel_out / "composite_phase5_report.md").as_posix()
    manifest_rel = (rel_out / "composite_phase5_report.manifest.json").as_posix()
    report_text = _compose_report(
        assembly_run_id=assembly_run_id, assembled_at_utc=assembled_at_utc,
        merged_count=len(merged_rows), board_validated=board_validated,
        not_validated=not_validated,
        carried_count=sum(1 for p in provenance_rows.values() if p["role"] == "carried"),
        fresh_count=sum(1 for p in provenance_rows.values() if p["role"] == "fresh"),
        locked=locked, source_runs=source_runs, board_folds=board_folds,
        methodology_fully_locked=methodology_fully_locked)
    report_bytes = report_text.encode("utf-8")
    report_sha = _sha256_bytes(report_bytes)

    report_manifest = {
        "report_manifest_schema": PHASE5_REPORT_MANIFEST_SCHEMA,
        "version": PHASE5_REPORT_MANIFEST_VERSION,
        "report_path": report_rel,
        "ranking_artifact_path": fixture_rel,
        "validation_sidecar_path": sidecar_rel,
        "report_sha256": report_sha,
        "validation_sidecar_sha256": sidecar_sha,
        "validation_run_id": assembly_run_id,
        "ranking_run_id": assembly_run_id,
        "fixture_schema_version_expected": SCHEMA_VERSION_V2,
        "methodology": {
            "n_permutations": locked["n_permutations"],
            "n_bootstrap_samples": locked["n_bootstrap_samples"],
            "walk_forward_n_folds": board_folds,
            "mc_method": locked["multiple_comparisons_control_method"],
            "alpha": locked["multiple_comparisons_control_alpha"],
            "contract_version": locked["validation_contract_version"],
            "methodology_version": locked["validation_methodology_version"],
            "bootstrap_ci_level": locked["bootstrap_ci_level"],
            "rng_seed": None,
        },
        "counts": {
            "tested": len(merged_rows),
            "board_validated": board_validated,
            "not_validated": not_validated,
            "stage_a_excluded": len(merged_stage_a),
            "empirical_validated": sidecar_counts["empirical_validated"],
            "empirical_not_run": sidecar_counts["empirical_not_run"],
            "empirical_failed": sidecar_counts["empirical_failed"],
            "validated_but_not_bh": sidecar_counts["validated_but_not_bh"],
        },
    }

    # =====================================================================
    # 9. Combined CCC verification manifest.
    # =====================================================================
    ccc_records: list = []
    for row in merged_rows:
        if row.get("ccc_series_source") != CCC_SERIES_SOURCE_BLOB:
            raise CombineError(
                f"merged row {row.get('secondary')!r} is not Blob-sourced; the "
                "first primitive assembles an all-Blob slim board")
        sec = _norm_ticker(row["secondary"])
        role = provenance_rows[sec]["ccc_source"]
        if role == "carried":
            rec = prior_ccc_by_sec.get(sec)
            if rec is None:
                raise CombineError(f"no carried CCC record for {sec!r}")
        else:
            rec = fresh_ccc_by_sec.get(sec)
            if rec is None:
                raise CombineError(f"no fresh CCC record for {sec!r}")
        record = _ccc_record(rec, row, sec)
        ccc_records.append(record)
    combined_ccc = {
        "schema_version": CCC_VERIFICATION_SCHEMA_VERSION,
        "sidecar_schema_version": CCC_SIDECAR_SCHEMA_VERSION,
        "ranking_run_id": assembly_run_id,
        "records": ccc_records,
    }

    # =====================================================================
    # 10. Exclusion scan (skip the structured Stage-A disclosure block).
    # =====================================================================
    leaked = _scan_excluded(merged_fixture, set(excluded_tickers))
    if leaked:
        raise CombineError(
            f"excluded ticker(s) present in merged fixture: {sorted(leaked)!r}")

    # =====================================================================
    # Write artifacts (only under output_dir).
    # =====================================================================
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_bytes = _dump_bytes(merged_fixture)
    paths = {
        "merged_fixture": output_dir / "merged_k6_mtf_ranking_v2.json",
        "composite_sidecar": output_dir / "composite_validation_sidecar.json",
        "composite_report": output_dir / "composite_phase5_report.md",
        "composite_report_manifest": output_dir / "composite_phase5_report.manifest.json",
        "combined_ccc_manifest": output_dir / "combined_ccc_sidecar_verification.json",
    }
    paths["composite_sidecar"].write_bytes(sidecar_bytes)
    paths["merged_fixture"].write_bytes(fixture_bytes)
    paths["composite_report"].write_bytes(report_bytes)
    paths["composite_report_manifest"].write_bytes(_dump_bytes(report_manifest))
    paths["combined_ccc_manifest"].write_bytes(_dump_bytes(combined_ccc))

    # =====================================================================
    # Optional promote self-check (read-only; no write/network reached).
    # =====================================================================
    self_check = {"ran": False}
    if run_self_check:
        self_check = _promote_self_check(
            fixture_payload=merged_fixture,
            sidecar_path=paths["composite_sidecar"],
            sidecar_sha=sidecar_sha,
            report_path=paths["composite_report"],
            report_sha=report_sha,
            manifest_path=paths["composite_report_manifest"],
            ccc_manifest=combined_ccc,
            project_root=project_root)

    summary = {
        "assembly_run_id": assembly_run_id,
        "assembled_at_utc": assembled_at_utc,
        "prior_row_count": len(prior_rows),
        "merged_row_count": len(merged_rows),
        "carried_count": sum(1 for p in provenance_rows.values() if p["role"] == "carried"),
        "fresh_count": sum(1 for p in provenance_rows.values() if p["role"] == "fresh"),
        "net_new_count": len(merged_rows) - len(prior_rows),
        "board_validated_count": board_validated,
        "not_validated_count": not_validated,
        "stage_a_excluded_count": len(merged_stage_a),
        "composite_sidecar_sha256": sidecar_sha,
        "report_sha256": report_sha,
        "methodology_fully_locked": methodology_fully_locked,
        "ccc_record_count": len(ccc_records),
        "paths": {k: v.relative_to(project_root).as_posix() for k, v in paths.items()},
        "promote_self_check": self_check,
    }
    (output_dir / "combine_proof_summary.json").write_bytes(_dump_bytes(summary))
    return summary


# ---------------------------------------------------------------------------
# Internal assembly helpers
# ---------------------------------------------------------------------------

_CARRIED_MUTABLE = {"rank", "validated_as_of_utc"}


def _assert_carried_unchanged(prior_row: Mapping[str, Any],
                              new_row: Mapping[str, Any]) -> None:
    keys = set(prior_row.keys()) | set(new_row.keys())
    for k in keys:
        if k in _CARRIED_MUTABLE:
            continue
        if prior_row.get(k) != new_row.get(k):
            raise CombineError(
                f"carried row {prior_row.get('secondary')!r} field {k!r} changed "
                "outside allowed fields (rank, validated_as_of_utc)")


def _assert_row_fields(sec: str, row: Mapping[str, Any]) -> None:
    for k in _PER_ROW_REQUIRED:
        if k not in row:
            raise CombineError(f"fresh row {sec!r} missing required field {k!r}")
    for k in _PER_ROW_V2_VALIDATION_REQUIRED:
        if k not in row:
            raise CombineError(f"fresh row {sec!r} missing v2 validation field {k!r}")
    for k in _CCC_ROW_METADATA:
        if k not in row:
            raise CombineError(f"fresh row {sec!r} missing CCC metadata field {k!r}")


def _stamp_fresh_ccc(row: dict, rec: Mapping[str, Any]) -> None:
    """Stamp a fresh row's slim Blob metadata from a caller-supplied record."""
    for f in ("pathname", "url", "sha256", "byte_size", "points",
              "first_date", "last_date"):
        if f not in rec:
            raise CombineError(
                f"fresh CCC record for {rec.get('secondary')!r} missing {f!r}")
    if rec.get("get_verified") is not True:
        raise CombineError(
            f"fresh CCC record for {rec.get('secondary')!r} get_verified must be true")
    row["ccc_series"] = []
    row["ccc_series_source"] = CCC_SERIES_SOURCE_BLOB
    row["ccc_series_sidecar_schema_version"] = CCC_SIDECAR_SCHEMA_VERSION
    row["ccc_series_url"] = rec["url"]
    row["ccc_series_pathname"] = rec["pathname"]
    row["ccc_series_sha256"] = rec["sha256"]
    row["ccc_series_byte_size"] = rec["byte_size"]
    row["ccc_series_points"] = rec["points"]
    row["ccc_series_first_date"] = rec["first_date"]
    row["ccc_series_last_date"] = rec["last_date"]


def _strategy_from_source(src: Mapping[str, Any], row: Mapping[str, Any]) -> dict:
    """Lift a strategy entry from a real sidecar, asserting its (status, q)
    agree with the fixture row so a tampered fixture cannot slip through."""
    strat = dict(src)
    if strat.get("empirical_validation_status") != row.get("empirical_validation_status"):
        raise CombineError(
            f"sidecar/fixture empirical_validation_status disagree for "
            f"{row.get('secondary')!r}")
    if strat.get("bh_q_value") != row.get("bh_q_value"):
        raise CombineError(
            f"sidecar/fixture bh_q_value disagree for {row.get('secondary')!r}")
    return strat


def _index_strategies(strategies: Any, expected_secs: set, label: str) -> dict:
    """Index a sidecar's strategies by secondary, enforcing the join's strict
    contract: every strategy_id == 'k6_mtf:<SECONDARY>', no duplicate
    secondary, and the strategy secondary set EXACTLY equals expected_secs
    (no missing, no extra). Returns {secondary: strategy}."""
    if not isinstance(strategies, list) or not strategies:
        raise CombineError(f"{label} sidecar 'strategies' must be a non-empty list")
    by_sec: dict = {}
    for s in strategies:
        if not isinstance(s, dict):
            raise CombineError(f"{label} sidecar strategy is not an object")
        sid = s.get("strategy_id")
        if not isinstance(sid, str) or not sid.startswith("k6_mtf:"):
            raise CombineError(
                f"{label} sidecar strategy_id is malformed (must be "
                f"'k6_mtf:<SECONDARY>'): {sid!r}")
        sec = _norm_ticker(sid[len("k6_mtf:"):])
        if not sec or sid != f"k6_mtf:{sec}":
            raise CombineError(
                f"{label} sidecar strategy_id secondary mismatch/normalization: "
                f"{sid!r}")
        if sec in by_sec:
            raise CombineError(
                f"{label} sidecar duplicate strategy secondary: {sec!r}")
        by_sec[sec] = s
    if set(by_sec) != set(expected_secs):
        missing = sorted(set(expected_secs) - set(by_sec))
        extra = sorted(set(by_sec) - set(expected_secs))
        raise CombineError(
            f"{label} sidecar strategy set != expected secondary set; missing "
            f"{missing!r}, extra {extra!r}")
    return by_sec


def _bind_carried_provenance(sec: str, row: Mapping[str, Any],
                             prior_strat: Mapping[str, Any], *,
                             prior_sc_run_id: Any, prior_sc_sha: Any) -> None:
    """Bind a carried row's recorded validation provenance to its prior
    strategy. Prefer per-strategy additive validation_source (multi-prior
    carry); otherwise fall back to the legacy single-run prior sidecar
    run_id/SHA. Fail closed if neither can be bound."""
    want_sid = f"k6_mtf:{row.get('secondary')}"
    if row.get("validation_strategy_id") != want_sid:
        raise CombineError(
            f"carried row {sec!r} validation_strategy_id "
            f"{row.get('validation_strategy_id')!r} != {want_sid!r}")
    vsource = (prior_strat.get("validation_source")
               if isinstance(prior_strat, dict) else None)
    bound = False
    if isinstance(vsource, dict) and (
            vsource.get("run_id") is not None
            or vsource.get("artifact_sha256") is not None):
        if vsource.get("run_id") is not None and (
                row.get("validation_run_id") != vsource.get("run_id")):
            raise CombineError(
                f"carried row {sec!r} validation_run_id "
                f"{row.get('validation_run_id')!r} != prior strategy "
                f"validation_source.run_id {vsource.get('run_id')!r}")
        if vsource.get("artifact_sha256") is not None and (
                row.get("validation_artifact_sha256")
                != vsource.get("artifact_sha256")):
            raise CombineError(
                f"carried row {sec!r} validation_artifact_sha256 != prior "
                "strategy validation_source.artifact_sha256")
        bound = True
    if not bound:
        if prior_sc_run_id is None or prior_sc_sha is None:
            raise CombineError(
                f"carried row {sec!r} provenance cannot be bound: prior strategy "
                "has no validation_source and the legacy prior sidecar run_id/SHA "
                "is unavailable; multi-prior carry-forward requires explicit "
                "per-strategy provenance")
        if row.get("validation_run_id") != prior_sc_run_id:
            raise CombineError(
                f"carried row {sec!r} validation_run_id "
                f"{row.get('validation_run_id')!r} != bound prior sidecar run_id "
                f"{prior_sc_run_id!r}")
        if row.get("validation_artifact_sha256") != prior_sc_sha:
            raise CombineError(
                f"carried row {sec!r} validation_artifact_sha256 != bound prior "
                "sidecar SHA")


def _validate_prior_ccc_manifest(*, prior_ccc_manifest: Any,
                                 prior_fixture: Mapping[str, Any],
                                 prior_promo: Mapping[str, Any]) -> dict:
    """Fully validate the prior CCC verification manifest against the prior
    fixture before any carried record is trusted (this module never re-GETs
    carried Blob URLs, so the prior manifest is the proof source). Returns
    {secondary: record}."""
    m = prior_ccc_manifest
    if not isinstance(m, dict):
        raise CombineError("prior CCC verification manifest is not a JSON object")
    if m.get("schema_version") != CCC_VERIFICATION_SCHEMA_VERSION:
        raise CombineError(
            "prior CCC manifest schema_version mismatch: "
            f"{m.get('schema_version')!r}")
    if m.get("sidecar_schema_version") != CCC_SIDECAR_SCHEMA_VERSION:
        raise CombineError(
            "prior CCC manifest sidecar_schema_version mismatch: "
            f"{m.get('sidecar_schema_version')!r}")
    if m.get("ranking_run_id") != prior_fixture.get("run_id"):
        raise CombineError(
            "prior CCC manifest ranking_run_id "
            f"{m.get('ranking_run_id')!r} != prior fixture run_id "
            f"{prior_fixture.get('run_id')!r}")
    records = m.get("records")
    if not isinstance(records, list) or not records:
        raise CombineError("prior CCC manifest records must be a non-empty list")
    blob_rows = {
        _norm_ticker(r.get("secondary")): r
        for r in (prior_fixture.get("per_secondary") or [])
        if isinstance(r, dict) and r.get("ccc_series_source") == CCC_SERIES_SOURCE_BLOB
    }
    by_sec: dict = {}
    seen_path: set = set()
    seen_url: set = set()
    seen_sha: set = set()
    for rec in records:
        if not isinstance(rec, dict):
            raise CombineError("prior CCC record is not an object")
        for f in _CCC_RECORD_FIELDS:
            if f not in rec:
                raise CombineError(f"prior CCC record missing field {f!r}")
        if rec.get("get_verified") is not True:
            raise CombineError(
                f"prior CCC record get_verified must be true for "
                f"{rec.get('secondary')!r}")
        sec = _norm_ticker(rec.get("secondary"))
        if sec in by_sec:
            raise CombineError(f"prior CCC duplicate secondary: {sec!r}")
        for val, seen, lbl in ((rec.get("pathname"), seen_path, "pathname"),
                               (rec.get("url"), seen_url, "url"),
                               (rec.get("sha256"), seen_sha, "sha256")):
            if val in seen:
                raise CombineError(f"prior CCC duplicate {lbl}: {val!r}")
            seen.add(val)
        by_sec[sec] = rec
    if set(by_sec) != set(blob_rows):
        missing = sorted(set(blob_rows) - set(by_sec))
        extra = sorted(set(by_sec) - set(blob_rows))
        raise CombineError(
            f"prior CCC record set != prior Blob-row set; missing {missing!r}, "
            f"extra {extra!r}")
    for sec, rec in by_sec.items():
        row = blob_rows[sec]
        for rec_field, row_field in _CCC_FIELD_MAP.items():
            if rec.get(rec_field) != row.get(row_field):
                raise CombineError(
                    f"prior CCC record/row mismatch for {sec!r} on {rec_field}: "
                    f"record {rec.get(rec_field)!r} != row {row.get(row_field)!r}")
    # Prior promotion-manifest CCC summary agreement (where the field exists).
    storage = prior_promo.get("ccc_series_storage")
    if isinstance(storage, dict) and by_sec:
        total_bytes = sum(int(r["byte_size"]) for r in by_sec.values())
        largest = max(int(r["byte_size"]) for r in by_sec.values())
        total_points = sum(int(r["points"]) for r in by_sec.values())
        for key, expected in (("sidecar_count", len(by_sec)),
                              ("total_sidecar_bytes", total_bytes),
                              ("largest_sidecar_bytes", largest),
                              ("total_sidecar_points", total_points),
                              ("all_sidecars_get_verified", True)):
            if key in storage and storage.get(key) != expected:
                raise CombineError(
                    f"prior promotion manifest ccc_series_storage.{key} "
                    f"({storage.get(key)!r}) != derived ({expected!r})")
    return by_sec


def _is_finite(v: Any) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def _derive_counts(strategies: Sequence[Mapping[str, Any]], alpha: float) -> dict:
    """Mirror promote._derive_sidecar_counts so the composite sidecar's counts
    match what promote will derive."""
    tested = len(strategies)
    board = validated = enr = ef = 0
    for s in strategies:
        st = s.get("empirical_validation_status")
        if st == "validated":
            validated += 1
            bh_q = s.get("bh_q_value")
            if not _is_finite(bh_q):
                raise CombineError(
                    f"composite strategy {s.get('strategy_id')!r} validated but "
                    "bh_q_value non-finite")
            if float(bh_q) <= alpha:
                board += 1
        elif st == "empirical_not_run":
            enr += 1
        elif st == "empirical_failed":
            ef += 1
    return {
        "tested": tested, "board_validated": board, "not_validated": tested - board,
        "empirical_validated": validated, "empirical_not_run": enr,
        "empirical_failed": ef, "validated_but_not_bh": validated - board,
    }


def _source_run_inventory(prior_meta, prior_sc, fresh_sc, provenance_rows) -> list:
    carried_secs = sorted(s for s, p in provenance_rows.items() if p["role"] == "carried")
    fresh_secs = sorted(s for s, p in provenance_rows.items() if p["role"] == "fresh")
    carried_run_id = (prior_sc.get("run_id") if prior_sc is not None
                      else prior_meta.get("run_id"))
    carried_sha = (prior_meta.get("artifact_sha256"))
    carried_eval = prior_meta.get("validated_as_of_utc")
    inv = []
    if carried_secs:
        inv.append({
            "role": "carried",
            "run_id": carried_run_id,
            "artifact_sha256": carried_sha,
            "evaluation_time": carried_eval,
            "rng_seed": None,
            "row_count": len(carried_secs),
            "secondaries": carried_secs,
            # carried rows always bind to a supplied prior validation sidecar.
            "provenance_source": "prior_validation_sidecar",
        })
    if fresh_secs:
        inv.append({
            "role": "fresh",
            "run_id": fresh_sc.get("run_id"),
            "artifact_sha256": None,
            "evaluation_time": fresh_sc.get("evaluation_time"),
            "rng_seed": None,
            "row_count": len(fresh_secs),
            "secondaries": fresh_secs,
            "provenance_source": "fresh_validation_run",
        })
    return inv


def _ccc_record(rec: Mapping[str, Any], row: Mapping[str, Any], sec: str) -> dict:
    """Build one CCC verification record and assert exact equality with the
    merged slim row (mirrors promote.validate_ccc_verification_against_fixture)."""
    out = {}
    for f in _CCC_RECORD_FIELDS:
        if f not in rec:
            raise CombineError(f"CCC record for {sec!r} missing field {f!r}")
        out[f] = rec[f]
    if out.get("get_verified") is not True:
        raise CombineError(f"CCC record for {sec!r} get_verified must be true")
    for rec_field, row_field in _CCC_FIELD_MAP.items():
        if out.get(rec_field) != row.get(row_field):
            raise CombineError(
                f"CCC record/row mismatch for {sec!r} on {rec_field}: record "
                f"{out.get(rec_field)!r} != row {row.get(row_field)!r}")
    return out


def _compose_report(*, assembly_run_id, assembled_at_utc, merged_count,
                    board_validated, not_validated, carried_count, fresh_count,
                    locked, source_runs, board_folds,
                    methodology_fully_locked) -> str:
    lines = [
        "# K6 MTF Composite (Carry-Forward) Validation Proof",
        "",
        "This is a COMPOSITE / carry-forward validation proof. It is NOT a "
        "single validation run of every row on the board.",
        "",
        f"- Assembly run id: {assembly_run_id}",
        f"- Assembled at (UTC): {assembled_at_utc}",
        f"- Board rows (merged): {merged_count}",
        f"- board_validated: {board_validated}; not_validated: {not_validated}",
        f"- Carried rows (prior verdicts retained): {carried_count}",
        f"- Freshly validated rows (this run): {fresh_count}",
        "",
        "## Honesty statement",
        "",
        "Carried rows retain the validation verdicts from their prior "
        "validation run(s); they were NOT re-validated in this run. Only the "
        "fresh rows were validated in the current validation run. Per-row "
        "provenance (validation_run_id, validation_artifact_sha256, "
        "validated_as_of_utc) is preserved on every row, and the composite "
        "sidecar carries a top-level source_validation_runs inventory.",
        "",
        "The composite sidecar rng_seed is null: every source cohort is "
        "unseeded, so the single top-level seed is literally true for all rows.",
        "",
        "Methodology lock: "
        + ("fully verified against the prior validation sidecar."
           if methodology_fully_locked
           else "verified against the prior board's fixture metadata "
                "(prior validation sidecar not supplied; promote-unbound "
                "honesty fields assumed equal to the fresh run)."),
        "",
        "## Locked methodology",
        "",
        f"- contract_version: {locked['validation_contract_version']}",
        f"- methodology_version: {locked['validation_methodology_version']}",
        f"- alpha: {locked['multiple_comparisons_control_alpha']}",
        f"- mc_method: {locked['multiple_comparisons_control_method']}",
        f"- supplementary: {locked['multiple_comparisons_supplementary']}",
        f"- n_permutations: {locked['n_permutations']}",
        f"- n_bootstrap_samples: {locked['n_bootstrap_samples']}",
        f"- bootstrap_ci_level: {locked['bootstrap_ci_level']}",
        f"- borderline_tolerance_multiplier: {locked['borderline_tolerance_multiplier']}",
        "- walk_forward_n_folds (advisory, data-derived): "
        + ("composite (mixed by validation cohort)" if board_folds is None
           else str(board_folds)),
        f"- baseline_method: {locked['baseline_method']}",
        "",
        "## Source validation runs",
        "",
    ]
    for sr in source_runs:
        lines.append(
            f"- [{sr['role']}] run_id={sr['run_id']} rows={sr['row_count']} "
            f"source={sr['provenance_source']}")
    lines.append("")
    return "\n".join(lines)


def _promote_self_check(*, fixture_payload, sidecar_path, sidecar_sha,
                        report_path, report_sha, manifest_path, ccc_manifest,
                        project_root) -> dict:
    """Run the UNCHANGED promote validators against the assembled artifacts.
    Read-only: validators only read files; no write/network/CLI path. Returns
    a per-validator pass/fail dict. Raises CombineError if any validator
    rejects the assembled shape (would mean unchanged promote cannot accept it)."""
    try:
        import importlib
        promote = importlib.import_module(
            "utils.react_publish.promote_k6_mtf_artifact")
    except Exception as exc:  # noqa: BLE001 - import optional
        return {"ran": False, "reason": f"promote import unavailable: {type(exc).__name__}"}
    results: dict = {"ran": True}
    try:
        promote.validate_k6_mtf_ranking_v2_payload(
            fixture_payload, validation_sidecar_path=Path(sidecar_path),
            expected_sidecar_sha256=sidecar_sha, for_public_promotion=True)
        results["validate_k6_mtf_ranking_v2_payload"] = "pass"
        promote.verify_v2_promotion_binding(
            fixture_payload=fixture_payload, report_path=Path(report_path),
            report_sha256=report_sha, manifest_path=Path(manifest_path),
            validation_sidecar_path=Path(sidecar_path),
            validation_sidecar_sha256=sidecar_sha,
            project_root=Path(project_root))
        results["verify_v2_promotion_binding"] = "pass"
        promote.validate_ccc_verification_against_fixture(
            fixture_payload, ccc_manifest)
        results["validate_ccc_verification_against_fixture"] = "pass"
    except promote.PromotionError as exc:
        raise CombineError(
            f"assembled artifact rejected by unchanged promote validator: {exc}"
        ) from exc
    return results
