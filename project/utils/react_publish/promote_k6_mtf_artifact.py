"""PRIVATE / INTERNAL K=6 MTF ranking-artifact promotion helper.

Stdlib only. Dry-run by default. Fail-closed. Operator-supervised.

This helper validates an operator-approved
``k6_mtf_ranking_v1`` JSON artifact under ``<PROJECT_DIR>/output/``,
optionally copies it to the React fixture path, and writes
the PR #367 promotion manifest. It does NOT deploy, does NOT
run pipeline compute, does NOT mutate ``output/``, does NOT
change React runtime behavior, and does NOT select a
deployment target.

Modes:

- **Private / internal (default).** Writes the manifest's
  ``validation_results`` field as the exact string
  ``"not_required_for_private_internal_use"`` per the React
  Publish / Deploy Contract (Section 9). Never writes blank,
  null, partial, or omitted ``validation_results``.

- **Public.** Requires ALL of: ``--public``,
  ``--phase5-report``, and ``--phase5-sha256``. Verifies the
  Phase 5 honest-validation report file exists and that its
  recomputed SHA-256 matches the supplied SHA. Public mode
  WITHOUT verified Phase 5 inputs HARD REFUSES with a clear
  message and a non-zero exit. There is no code path that
  produces a public-ready manifest without a verified Phase 5
  report. Even dry-run verifies the Phase 5 inputs or refuses.

Writes:

- Dry-run (default) writes nothing.
- Real writes require BOTH ``--write`` AND
  ``--operator-approved``.
- ``--write`` without ``--operator-approved`` refuses with
  non-zero exit.

Runtime boundary:

- This helper does NOT modify React runtime source.
- The promotion manifest is NOT a React runtime input.
- React still fetches only the ranking JSON.

Source-of-truth references (cited by section / file name, not
by drifting line numbers):

- ``project/md_library/shared/2026-05-31_REACT_PUBLISH_DEPLOY_CONTRACT.md``
  Section 9 Future Promotion Manifest Schema.
- ``project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md``
  Ranking Artifact section (k6_mtf_ranking_v1 schema lock).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "k6_mtf_ranking_v1"
PRIVATE_VALIDATION_RESULTS = "not_required_for_private_internal_use"
PROMOTED_BY_ROLE = "the operator"

ALLOWED_STATUS_VALUES = frozenset({"ranked", "unranked", "failed"})

TOP_LEVEL_REQUIRED = (
    "schema_version",
    "generated_at_utc",
    "run_id",
    "secondaries_requested",
    "secondaries_ranked",
    "per_secondary",
    "issues",
)
PER_SECONDARY_REQUIRED = (
    "secondary",
    "rank",
    "status",
    "history_artifact_path",
    "history_as_of_date",
    "current_snapshot",
    "k6_stack",
    "sharpe_k6_mtf",
    "total_capture_pct",
    "avg_capture_pct",
    "stddev_pct",
    "match_count",
    "capture_count",
    "trade_count",
    "no_trade_count",
    "skipped_capture_count",
    "win_count",
    "loss_count",
    "win_pct",
    "low_sample_warning",
    "ccc_series",
    "issues",
)
K6_STACK_REQUIRED = (
    "selected_build_path",
    "selected_run_dir",
    "combo_k6_path",
    "members",
)

PATH_FIELDS_PER_ROW = ("history_artifact_path",)
PATH_FIELDS_K6_STACK = ("selected_build_path", "selected_run_dir", "combo_k6_path")

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
_BACKSLASH = chr(92)
_USERNAME_MARKERS = ("users/", "home/")


class PromotionError(Exception):
    """Raised when validation or safety checks refuse the promotion.

    Caller code at the CLI surface translates these to non-zero
    exits with a clear message; library callers can catch the
    exception directly. Either way, nothing is written.
    """


@dataclass(frozen=True)
class PromotionInputs:
    source_path: Path
    destination_path: Path
    manifest_destination_path: Path
    project_root: Path
    public_mode: bool
    phase5_report_path: Path | None
    phase5_report_sha256: str | None
    write: bool
    operator_approved: bool


@dataclass(frozen=True)
class ValidationOutcome:
    payload: dict
    source_sha256: str
    source_relative_path: str
    per_secondary_count: int


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise PromotionError(f"source artifact missing: {path!s}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromotionError(f"source artifact unreadable: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PromotionError(f"source artifact not valid JSON: {exc}") from exc


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_project_relative_source(
    source_path: Path, project_root: Path,
) -> str:
    try:
        rel = source_path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise PromotionError(
            f"source artifact must live under <PROJECT_DIR>/output/; got: {source_path!s}"
        ) from exc
    rel_str = rel.as_posix()
    if not rel_str.startswith("output/"):
        raise PromotionError(
            f"source artifact must live under <PROJECT_DIR>/output/; got: {rel_str}"
        )
    return rel_str


def _validate_path_field(secondary: str, field: str, value: Any) -> None:
    if not isinstance(value, str):
        raise PromotionError(
            f"per_secondary {secondary!r}.{field} must be a string; got: {type(value).__name__}"
        )
    if not value.startswith("output/"):
        raise PromotionError(
            f"per_secondary {secondary!r}.{field} must start with 'output/'; got: {value!r}"
        )
    if _DRIVE_LETTER_RE.match(value):
        raise PromotionError(
            f"per_secondary {secondary!r}.{field} contains a drive letter: {value!r}"
        )
    if value.startswith("/"):
        raise PromotionError(
            f"per_secondary {secondary!r}.{field} starts with absolute slash: {value!r}"
        )
    if _BACKSLASH in value:
        raise PromotionError(
            f"per_secondary {secondary!r}.{field} contains a backslash: {value!r}"
        )
    lowered = value.lower()
    for marker in _USERNAME_MARKERS:
        if marker in lowered:
            raise PromotionError(
                f"per_secondary {secondary!r}.{field} contains a local username marker {marker!r}: {value!r}"
            )


def _validate_payload(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise PromotionError("artifact root is not a JSON object")
    schema = payload.get("schema_version")
    if schema != SCHEMA_VERSION:
        raise PromotionError(
            f"schema_version must equal {SCHEMA_VERSION!r}; got: {schema!r}"
        )
    missing_top = [k for k in TOP_LEVEL_REQUIRED if k not in payload]
    if missing_top:
        raise PromotionError(f"missing top-level fields: {missing_top!r}")
    rows = payload.get("per_secondary")
    if not isinstance(rows, list) or not rows:
        raise PromotionError("per_secondary must be a non-empty list")
    ranked_list = payload.get("secondaries_ranked")
    if not isinstance(ranked_list, list):
        raise PromotionError("secondaries_ranked must be a list")
    for row in rows:
        if not isinstance(row, dict):
            raise PromotionError("per_secondary entry is not a JSON object")
        sec = row.get("secondary") or "?"
        missing_row = [k for k in PER_SECONDARY_REQUIRED if k not in row]
        if missing_row:
            raise PromotionError(
                f"per_secondary {sec!r} missing fields: {missing_row!r}"
            )
        if row.get("status") not in ALLOWED_STATUS_VALUES:
            raise PromotionError(
                f"per_secondary {sec!r} status not in {sorted(ALLOWED_STATUS_VALUES)!r}: {row.get('status')!r}"
            )
        stack = row.get("k6_stack")
        if not isinstance(stack, dict):
            raise PromotionError(
                f"per_secondary {sec!r}.k6_stack must be a JSON object"
            )
        missing_stack = [k for k in K6_STACK_REQUIRED if k not in stack]
        if missing_stack:
            raise PromotionError(
                f"per_secondary {sec!r}.k6_stack missing fields: {missing_stack!r}"
            )
        for f in PATH_FIELDS_PER_ROW:
            _validate_path_field(sec, f, row.get(f))
        for f in PATH_FIELDS_K6_STACK:
            _validate_path_field(sec, f"k6_stack.{f}", stack.get(f))
    # secondaries_ranked vs per_secondary coherence (per the K=6 MTF
    # launch-path contract): failed and null-Sharpe secondaries remain
    # in per_secondary but are excluded from secondaries_ranked. The
    # single load-bearing predicate is that secondaries_ranked equals
    # exactly the rank-ordered ranked-record ticker list. This rejects:
    #   - failed or unranked tickers appearing in secondaries_ranked,
    #   - unknown tickers in secondaries_ranked,
    #   - duplicate tickers in secondaries_ranked,
    #   - rank-order mismatches,
    #   - count mismatches.
    expected_ranked_tickers = [
        r.get("secondary") for r in sorted(
            (
                r for r in rows
                if r.get("status") == "ranked"
                and isinstance(r.get("rank"), int)
            ),
            key=lambda r: r.get("rank"),
        )
    ]
    if list(ranked_list) != expected_ranked_tickers:
        raise PromotionError(
            "secondaries_ranked must equal the rank-ordered ranked-record "
            "tickers (status=='ranked' and integer rank, ascending by rank); "
            f"expected {expected_ranked_tickers!r}, got {list(ranked_list)!r}"
        )
    return payload


# ---------------------------------------------------------------------------
# v2 schema validation (k6_mtf_ranking_v2 = ranking + validation join)
# ---------------------------------------------------------------------------

SCHEMA_VERSION_V2 = "k6_mtf_ranking_v2"
VALIDATION_OUTCOME_BOARD = "board_validated"
VALIDATION_OUTCOME_NOT = "not_validated"
ALLOWED_VALIDATION_OUTCOMES = frozenset(
    {VALIDATION_OUTCOME_BOARD, VALIDATION_OUTCOME_NOT}
)

# v2 keeps every v1 top-level field and adds these.
V2_TOP_LEVEL_REQUIRED = (
    "schema_version",
    "generated_at_utc",
    "run_id",
    "secondaries_requested",
    "secondaries_ranked",
    "per_secondary",
    "issues",
    "validation_metadata",
    "validation_summary",
    "stage_a_excluded_secondaries",
)
V2_VALIDATION_METADATA_REQUIRED = (
    "run_id",
    "artifact_sha256",
    "validation_status",
    "validated_as_of_utc",
    "n_strategies_tested",
    "n_strategies_reported",
    "n_permutations",
    "n_bootstrap_samples",
    "walk_forward_n_folds",
    "multiple_comparisons_control_alpha",
    "multiple_comparisons_control_method",
    "validation_contract_version",
    "validation_methodology_version",
    "rng_seed",
)
V2_VALIDATION_SUMMARY_REQUIRED = (
    "board_validated_count",
    "not_validated_count",
    "empirical_status_counts",
    "stage_a_excluded_count",
    "displayed_ranked_count",
    "validation_non_reported_count",
)
# Per-row v2 validation fields (added on top of PER_SECONDARY_REQUIRED).
PER_SECONDARY_V2_VALIDATION_REQUIRED = (
    "validation_outcome",
    "empirical_validation_status",
    "empirical_p_value",
    "parametric_p_value",
    "bh_q_value",
    "bonferroni_p_value",
    "bootstrap_sharpe_ci_lower",
    "bootstrap_sharpe_ci_upper",
    "empirical_not_run_reason",
    "validation_trigger_days",
    "validation_strategy_id",
    "validation_run_id",
    "validation_artifact_sha256",
)
# Project-relative metadata path fields that, when non-null, must pass
# the same privacy check as per-row paths.
V2_METADATA_PATH_FIELDS = ("source_sidecar_path", "source_ranking_path")


def _validate_metadata_path_field(field: str, value: Any) -> None:
    """Like ``_validate_path_field`` but allows ``None`` (these source
    paths are nullable) and accepts the ``output/`` prefix only."""
    if value is None:
        return
    _validate_path_field("validation_metadata", field, value)


def _is_finite_number(value: Any) -> bool:
    """True iff ``value`` is a real finite number (not bool, not None,
    not NaN, not +/-inf)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def validate_k6_mtf_ranking_v2_payload(
    payload: Any,
    *,
    validation_sidecar_path: Path | None = None,
    expected_sidecar_sha256: str | None = None,
    for_public_promotion: bool = False,
) -> dict:
    """Validate a joined ``k6_mtf_ranking_v2`` payload.

    Fail-closed. Preserves v1 path-privacy checks, verifies the
    operator-locked two-outcome validation model, the sidecar SHA (when
    a sidecar path or expected hash is supplied), and the count
    invariants. Does NOT perform any write or promotion.

    When ``for_public_promotion`` is True, a validation sidecar path or
    expected hash is REQUIRED (future public promotion must verify the
    sidecar). This PR does not wire this into ``promote()``; the public
    path remains closed.
    """
    if not isinstance(payload, dict):
        raise PromotionError("v2 artifact root is not a JSON object")
    schema = payload.get("schema_version")
    if schema != SCHEMA_VERSION_V2:
        raise PromotionError(
            f"schema_version must equal {SCHEMA_VERSION_V2!r}; got: {schema!r}"
        )
    missing_top = [k for k in V2_TOP_LEVEL_REQUIRED if k not in payload]
    if missing_top:
        raise PromotionError(f"v2 missing top-level fields: {missing_top!r}")

    meta = payload.get("validation_metadata")
    if not isinstance(meta, dict):
        raise PromotionError("validation_metadata must be a JSON object")
    missing_meta = [k for k in V2_VALIDATION_METADATA_REQUIRED if k not in meta]
    if missing_meta:
        raise PromotionError(f"validation_metadata missing fields: {missing_meta!r}")
    for f in V2_METADATA_PATH_FIELDS:
        _validate_metadata_path_field(f, meta.get(f))

    summary = payload.get("validation_summary")
    if not isinstance(summary, dict):
        raise PromotionError("validation_summary must be a JSON object")
    missing_summary = [k for k in V2_VALIDATION_SUMMARY_REQUIRED if k not in summary]
    if missing_summary:
        raise PromotionError(f"validation_summary missing fields: {missing_summary!r}")

    alpha = meta.get("multiple_comparisons_control_alpha")
    if not isinstance(alpha, (int, float)):
        raise PromotionError(
            "validation_metadata.multiple_comparisons_control_alpha must be numeric"
        )
    alpha = float(alpha)

    # --- sidecar SHA verification ---------------------------------------
    declared_hash = meta.get("artifact_sha256")
    if not isinstance(declared_hash, str) or not declared_hash:
        raise PromotionError("validation_metadata.artifact_sha256 must be a string")
    if validation_sidecar_path is not None:
        actual = _compute_sha256(Path(validation_sidecar_path))
        if actual != declared_hash:
            raise PromotionError(
                "validation sidecar SHA-256 mismatch vs validation_metadata."
                f"artifact_sha256: file {actual}, metadata {declared_hash}"
            )
    if expected_sidecar_sha256 is not None:
        exp = str(expected_sidecar_sha256).strip().lower()
        if exp != declared_hash:
            raise PromotionError(
                "expected sidecar SHA-256 does not match validation_metadata."
                f"artifact_sha256: expected {exp}, metadata {declared_hash}"
            )
    if for_public_promotion and validation_sidecar_path is None and (
        expected_sidecar_sha256 is None
    ):
        raise PromotionError(
            "public promotion of a v2 artifact requires a validation sidecar "
            "path or expected SHA-256; refusing"
        )

    # --- per-row validation ---------------------------------------------
    rows = payload.get("per_secondary")
    if not isinstance(rows, list) or not rows:
        raise PromotionError("per_secondary must be a non-empty list")
    board_count = 0
    for row in rows:
        if not isinstance(row, dict):
            raise PromotionError("per_secondary entry is not a JSON object")
        sec = row.get("secondary") or "?"
        missing_row = [k for k in PER_SECONDARY_REQUIRED if k not in row]
        if missing_row:
            raise PromotionError(
                f"per_secondary {sec!r} missing v1 fields: {missing_row!r}"
            )
        missing_v2 = [
            k for k in PER_SECONDARY_V2_VALIDATION_REQUIRED if k not in row
        ]
        if missing_v2:
            raise PromotionError(
                f"per_secondary {sec!r} missing v2 validation fields: {missing_v2!r}"
            )
        if row.get("status") not in ALLOWED_STATUS_VALUES:
            raise PromotionError(
                f"per_secondary {sec!r} status not in "
                f"{sorted(ALLOWED_STATUS_VALUES)!r}: {row.get('status')!r}"
            )
        outcome = row.get("validation_outcome")
        if outcome not in ALLOWED_VALIDATION_OUTCOMES:
            raise PromotionError(
                f"per_secondary {sec!r} validation_outcome not in "
                f"{sorted(ALLOWED_VALIDATION_OUTCOMES)!r}: {outcome!r}"
            )
        emp_status = row.get("empirical_validation_status")
        bh_q = row.get("bh_q_value")
        # Fail closed: a validated row MUST carry a real finite row-level
        # q-value (not missing/null/non-numeric/NaN/non-finite),
        # regardless of validation_outcome -- otherwise board_validated
        # vs not_validated cannot be honestly derived/verified.
        if emp_status == "validated" and not _is_finite_number(bh_q):
            raise PromotionError(
                f"per_secondary {sec!r} is empirical_validation_status=="
                f"'validated' but bh_q_value is not a finite number: {bh_q!r}"
            )
        q_le_alpha = _is_finite_number(bh_q) and float(bh_q) <= alpha
        if outcome == VALIDATION_OUTCOME_BOARD:
            board_count += 1
            if emp_status != "validated":
                raise PromotionError(
                    f"per_secondary {sec!r} board_validated requires "
                    f"empirical_validation_status=='validated'; got {emp_status!r}"
                )
            if not q_le_alpha:
                raise PromotionError(
                    f"per_secondary {sec!r} board_validated requires "
                    f"bh_q_value<=alpha ({alpha}); got {bh_q!r}"
                )
        else:
            # not_validated must not silently hide a board-eligible row.
            if emp_status == "validated" and q_le_alpha:
                raise PromotionError(
                    f"per_secondary {sec!r} is not_validated but satisfies "
                    "board_validated (validated and bh_q<=alpha); inconsistent"
                )
        # path-privacy (v1 parity)
        stack = row.get("k6_stack")
        if not isinstance(stack, dict):
            raise PromotionError(
                f"per_secondary {sec!r}.k6_stack must be a JSON object"
            )
        for f in PATH_FIELDS_PER_ROW:
            _validate_path_field(sec, f, row.get(f))
        for f in PATH_FIELDS_K6_STACK:
            _validate_path_field(sec, f"k6_stack.{f}", stack.get(f))

    # --- count invariants ------------------------------------------------
    displayed = len(rows)
    not_validated_count = displayed - board_count
    if summary.get("displayed_ranked_count") != displayed:
        raise PromotionError(
            "validation_summary.displayed_ranked_count "
            f"{summary.get('displayed_ranked_count')!r} != per_secondary "
            f"count {displayed}"
        )
    if summary.get("board_validated_count") != board_count:
        raise PromotionError(
            "validation_summary.board_validated_count "
            f"{summary.get('board_validated_count')!r} != derived {board_count}"
        )
    if summary.get("not_validated_count") != not_validated_count:
        raise PromotionError(
            "validation_summary.not_validated_count "
            f"{summary.get('not_validated_count')!r} != derived "
            f"{not_validated_count}"
        )
    if board_count + not_validated_count != displayed:
        raise PromotionError(
            "board_validated_count + not_validated_count != displayed_ranked_count"
        )
    if meta.get("n_strategies_tested") != displayed:
        raise PromotionError(
            "validation_metadata.n_strategies_tested "
            f"{meta.get('n_strategies_tested')!r} != per_secondary count {displayed}"
        )
    if meta.get("n_strategies_reported") != board_count:
        raise PromotionError(
            "validation_metadata.n_strategies_reported "
            f"{meta.get('n_strategies_reported')!r} != derived board_validated "
            f"count {board_count} (row-level bh_q<=alpha derivation)"
        )
    excl = payload.get("stage_a_excluded_secondaries")
    if not isinstance(excl, list):
        raise PromotionError("stage_a_excluded_secondaries must be a list")
    if summary.get("stage_a_excluded_count") != len(excl):
        raise PromotionError(
            "validation_summary.stage_a_excluded_count "
            f"{summary.get('stage_a_excluded_count')!r} != "
            f"len(stage_a_excluded_secondaries) {len(excl)}"
        )
    return payload


# ---------------------------------------------------------------------------
# Public-mode safety: Phase 5 verification (load-bearing)
# ---------------------------------------------------------------------------


def _verify_phase5_inputs(
    public_mode: bool,
    report_path: Path | None,
    declared_sha256: str | None,
    project_root: Path,
) -> str | None:
    """Hard refuse if public mode is requested without a verified
    Phase 5 honest-validation report.

    Same predicate applies in dry-run; there is no code path that
    produces a public-ready manifest without a verified Phase 5 report.
    On success in public mode, returns the project-relative POSIX path
    of the verified report file. The PR #367 contract requires
    ``phase_5_validation_report_path`` to be project-relative (or an
    artifact URL; artifact-URL support is intentionally not in this
    helper, so the local-file form must be project-relative). Reports
    living outside ``<PROJECT_DIR>`` are refused.

    Returns ``None`` when not in public mode.
    """
    if not public_mode:
        return None
    if report_path is None:
        raise PromotionError(
            "public mode requires --phase5-report; refusing"
        )
    if declared_sha256 is None or not declared_sha256.strip():
        raise PromotionError(
            "public mode requires --phase5-sha256; refusing"
        )
    declared = declared_sha256.strip().lower()
    if len(declared) != 64 or any(c not in "0123456789abcdef" for c in declared):
        raise PromotionError(
            f"--phase5-sha256 is not a 64-char lowercase hex digest: {declared_sha256!r}"
        )
    if not report_path.is_file():
        raise PromotionError(
            f"Phase 5 report file does not exist: {report_path!s}"
        )
    actual = _compute_sha256(report_path)
    if actual != declared:
        raise PromotionError(
            "Phase 5 report SHA-256 mismatch: expected "
            f"{declared}, computed {actual}"
        )
    try:
        rel = report_path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise PromotionError(
            "Phase 5 report must live under <PROJECT_DIR> so the manifest "
            "can record a project-relative phase_5_validation_report_path; "
            "artifact-URL forms are not supported by this helper"
        ) from exc
    return rel.as_posix()


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def _build_manifest(
    inputs: PromotionInputs,
    outcome: ValidationOutcome,
    promoted_at_utc: str,
    phase5_report_relative_path: str | None,
) -> dict:
    payload = outcome.payload
    if inputs.public_mode:
        if phase5_report_relative_path is None:
            # Refused earlier in _verify_phase5_inputs; defensive guard.
            raise PromotionError(
                "public mode reached manifest build without a verified, "
                "project-relative Phase 5 report path; refusing"
            )
        validation_results: Any = {
            "phase_5_validation_report_path": phase5_report_relative_path,
            "phase_5_validation_report_sha256": (
                (inputs.phase5_report_sha256 or "").strip().lower()
            ),
            "operator_acknowledgment_of_public_launch_gate": True,
        }
    else:
        validation_results = PRIVATE_VALIDATION_RESULTS
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": payload.get("run_id"),
        "source_generated_at_utc": payload.get("generated_at_utc"),
        "source_artifact_path": outcome.source_relative_path,
        "source_sha256": outcome.source_sha256,
        "promoted_at_utc": promoted_at_utc,
        "promoted_by": PROMOTED_BY_ROLE,
        "operator_approval_marker": bool(inputs.operator_approved),
        "secondaries_ranked": payload.get("secondaries_ranked"),
        "per_secondary_count": outcome.per_secondary_count,
        "validation_results": validation_results,
    }


# ---------------------------------------------------------------------------
# Safe-copy write
# ---------------------------------------------------------------------------


def _safe_copy(source: Path, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=destination.name + ".",
        suffix=".part",
        dir=str(destination.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copyfile(str(source), str(tmp_path))
        tmp_sha = _compute_sha256(tmp_path)
        if tmp_sha != expected_sha256:
            raise PromotionError(
                f"temporary copy SHA-256 mismatch before replace: expected "
                f"{expected_sha256}, got {tmp_sha}"
            )
        os.replace(str(tmp_path), str(destination))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
    final_sha = _compute_sha256(destination)
    if final_sha != expected_sha256:
        raise PromotionError(
            f"destination SHA-256 mismatch after replace: expected "
            f"{expected_sha256}, got {final_sha}"
        )


def _write_manifest(manifest: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=destination.name + ".",
        suffix=".part",
        dir=str(destination.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(str(tmp_path), str(destination))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def promote(
    inputs: PromotionInputs,
    *,
    now_provider=_utcnow_iso,
) -> dict:
    """Run validation, public-mode safety verification, optional
    safe-copy, optional manifest write. Return a summary dict. Raise
    ``PromotionError`` on refusal. Dry-run never writes.
    """
    # 1. Refuse early on --write without --operator-approved.
    if inputs.write and not inputs.operator_approved:
        raise PromotionError(
            "--write requires --operator-approved; refusing"
        )
    # 2. Public-mode safety: verify Phase 5 inputs before any
    #    decision that might produce a public-ready manifest.
    #    Same predicate applies in dry-run. The verifier returns the
    #    project-relative POSIX path of the report; reports outside
    #    <PROJECT_DIR> are refused here.
    phase5_report_relative_path = _verify_phase5_inputs(
        public_mode=inputs.public_mode,
        report_path=inputs.phase5_report_path,
        declared_sha256=inputs.phase5_report_sha256,
        project_root=inputs.project_root,
    )
    # 3. Validate the source artifact and record provenance.
    payload = _validate_payload(_load_json(inputs.source_path))
    source_sha = _compute_sha256(inputs.source_path)
    source_relative = _resolve_project_relative_source(
        inputs.source_path, inputs.project_root,
    )
    outcome = ValidationOutcome(
        payload=payload,
        source_sha256=source_sha,
        source_relative_path=source_relative,
        per_secondary_count=len(payload.get("per_secondary") or []),
    )
    # 4. Build the manifest. Always built (validates the path
    #    even on dry-run) so dry-run output is meaningful.
    promoted_at_utc = now_provider()
    manifest = _build_manifest(
        inputs, outcome, promoted_at_utc, phase5_report_relative_path,
    )
    # 5. Write (or report dry-run).
    summary: dict[str, Any] = {
        "mode": "public" if inputs.public_mode else "private_internal",
        "dry_run": not inputs.write,
        "source_path": str(inputs.source_path),
        "source_relative_path": source_relative,
        "source_sha256": source_sha,
        "destination_path": str(inputs.destination_path),
        "manifest_destination_path": str(inputs.manifest_destination_path),
        "per_secondary_count": outcome.per_secondary_count,
        "secondaries_ranked": payload.get("secondaries_ranked"),
        "validation_results": manifest["validation_results"],
        "promoted_at_utc": promoted_at_utc,
        "wrote_destination": False,
        "wrote_manifest": False,
    }
    if inputs.write:
        _safe_copy(
            inputs.source_path,
            inputs.destination_path,
            expected_sha256=source_sha,
        )
        summary["wrote_destination"] = True
        _write_manifest(manifest, inputs.manifest_destination_path)
        summary["wrote_manifest"] = True
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_destination(project_root: Path) -> Path:
    return (
        project_root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.json"
    )


def _default_manifest_destination(project_root: Path) -> Path:
    return (
        project_root / "frontend" / "public" / "fixtures"
        / "k6_mtf_ranking.promotion_manifest.json"
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="promote_k6_mtf_artifact",
        description=(
            "PRIVATE / INTERNAL K=6 MTF ranking-artifact promotion "
            "helper. Stdlib only. Dry-run by default. Fail-closed. "
            "Operator-supervised. Does NOT deploy, does NOT run "
            "pipeline compute, does NOT mutate output/, does NOT "
            "change React runtime behavior, and does NOT select a "
            "deployment target. Public mode hard-refuses without a "
            "verified Phase 5 honest-validation report."
        ),
    )
    p.add_argument(
        "--source", required=True,
        help=(
            "Path to the operator-approved k6_mtf_ranking_v1 JSON "
            "artifact. Must live under <PROJECT_DIR>/output/."
        ),
    )
    p.add_argument(
        "--destination", default=None,
        help=(
            "Fixture destination. Default: <PROJECT_DIR>/"
            "frontend/public/fixtures/k6_mtf_ranking.json."
        ),
    )
    p.add_argument(
        "--manifest-destination", default=None,
        help=(
            "Manifest destination. Default: <PROJECT_DIR>/"
            "frontend/public/fixtures/"
            "k6_mtf_ranking.promotion_manifest.json."
        ),
    )
    p.add_argument(
        "--project-root", default=None,
        help=(
            "Project root override. Default: the parent of this "
            "helper's utils/react_publish/ package."
        ),
    )
    p.add_argument(
        "--public", action="store_true",
        help=(
            "Public-mode promotion. Requires --phase5-report and "
            "--phase5-sha256. Hard refuses without verified Phase 5 "
            "inputs, including in dry-run."
        ),
    )
    p.add_argument(
        "--phase5-report", default=None,
        help="Path to the Phase 5 honest-validation report file.",
    )
    p.add_argument(
        "--phase5-sha256", default=None,
        help="Expected SHA-256 of the Phase 5 report file.",
    )
    p.add_argument(
        "--write", action="store_true",
        help=(
            "Perform the real write. Default is dry-run. Requires "
            "--operator-approved."
        ),
    )
    p.add_argument(
        "--operator-approved", action="store_true",
        help="Explicit operator approval gate for real writes.",
    )
    return p


def _resolve_project_root(project_root_arg: str | None) -> Path:
    if project_root_arg:
        return Path(project_root_arg).resolve()
    # The package lives at <PROJECT_DIR>/utils/react_publish/.
    return Path(__file__).resolve().parents[2]


def _inputs_from_args(args: argparse.Namespace) -> PromotionInputs:
    project_root = _resolve_project_root(args.project_root)
    source = Path(args.source).resolve()
    destination = (
        Path(args.destination).resolve()
        if args.destination
        else _default_destination(project_root)
    )
    manifest_destination = (
        Path(args.manifest_destination).resolve()
        if args.manifest_destination
        else _default_manifest_destination(project_root)
    )
    phase5_report = (
        Path(args.phase5_report).resolve() if args.phase5_report else None
    )
    return PromotionInputs(
        source_path=source,
        destination_path=destination,
        manifest_destination_path=manifest_destination,
        project_root=project_root,
        public_mode=bool(args.public),
        phase5_report_path=phase5_report,
        phase5_report_sha256=args.phase5_sha256,
        write=bool(args.write),
        operator_approved=bool(args.operator_approved),
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    inputs = _inputs_from_args(args)
    try:
        summary = promote(inputs)
    except PromotionError as exc:
        print(f"PROMOTION REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
