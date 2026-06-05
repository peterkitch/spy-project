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
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import urllib.request
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
    # PR-2a v2 binding inputs (optional; required only for a v2 public
    # promotion). v1 promotion ignores these entirely.
    phase5_report_manifest_path: Path | None = None
    validation_sidecar_path: Path | None = None
    validation_sidecar_sha256: str | None = None


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

# ---------------------------------------------------------------------------
# CCC Blob-sidecar constants (full-resolution CCC stored off-repo as
# immutable per-secondary Vercel Blob objects; the committed fixture stays
# slim). The fixture CCC fidelity is NEVER decimated/truncated -- the full
# series lives in the sidecar; the row just points at it.
# ---------------------------------------------------------------------------

# Promotion-manifest format marker. Distinct from the promoted fixture's
# own schema_version (which the manifest records separately).
PROMOTION_MANIFEST_SCHEMA_VERSION = "k6_mtf_promotion_manifest_v1"

CCC_SERIES_SOURCE_BLOB = "vercel_blob"
CCC_SIDECAR_SCHEMA_VERSION = "k6_mtf_ccc_series_sidecar_v1"
CCC_STORAGE_MODE_BLOB = "vercel_blob_sidecars"

# Derived CCC point fields the sidecar is allowed to carry (Mode B:
# derived capture only, never raw OHLCV / reconstructable provider price).
CCC_POINT_REQUIRED_FIELDS = (
    "cumulative_capture_pct",
    "date_utc",
    "per_bar_capture_pct",
    "trade_direction",
)
# Raw-price keys that must NEVER appear in a CCC sidecar point (Mode B).
CCC_OHLCV_FORBIDDEN_KEYS = frozenset(
    {"open", "high", "low", "close", "adj_close", "adjclose", "adjusted_close",
     "volume"}
)

# Per-row slim-fixture sidecar metadata keys (present only when
# ccc_series_source == "vercel_blob").
CCC_SIDECAR_METADATA_REQUIRED = (
    "ccc_series_source",
    "ccc_series_sidecar_schema_version",
    "ccc_series_url",
    "ccc_series_pathname",
    "ccc_series_sha256",
    "ccc_series_byte_size",
    "ccc_series_points",
    "ccc_series_first_date",
    "ccc_series_last_date",
)

# Public Vercel Blob host allowlist. Public Blob HTTPS URLs are allowed
# ONLY in the ccc_series_url field and ONLY on this host pattern.
_VERCEL_BLOB_URL_RE = re.compile(
    r"^https://[A-Za-z0-9][A-Za-z0-9.-]*\.public\.blob\.vercel-storage\.com/"
    r"[A-Za-z0-9._~%/+-]+$"
)
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# Immutable sidecar pathname prefix (run-scoped under a k6-mtf namespace).
_CCC_SIDECAR_PATHNAME_RE = re.compile(
    r"^k6-mtf/[A-Za-z0-9._-]+/ccc-series/[A-Za-z0-9._-]+\.[0-9a-f]{64}\.json$"
)


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


def _validate_int(value: Any, *, positive: bool) -> bool:
    """True iff ``value`` is a real int (not bool) and meets the sign rule
    (``positive`` -> strictly > 0; otherwise >= 0)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value > 0 if positive else value >= 0


def _validate_ccc_blob_sidecar_metadata(sec: str, row: dict) -> None:
    """Validate a slim row's Vercel-Blob CCC sidecar metadata. Called only
    when ``ccc_series_source`` is present. Fail-closed (PromotionError).

    Enforces: empty inline ``ccc_series``; the known source value; the
    sidecar schema marker; an HTTPS Vercel Blob public URL (only here);
    an immutable run-scoped pathname containing the sha; a 64-hex sha;
    sane point/byte counts; null-or-string date metadata; and rejects any
    local filesystem path leaking into the url/pathname fields.
    """
    source = row.get("ccc_series_source")
    if source != CCC_SERIES_SOURCE_BLOB:
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_source must be "
            f"{CCC_SERIES_SOURCE_BLOB!r} when present; got {source!r}"
        )
    # ccc_series must be an EMPTY inline list for a blob-sourced row (the
    # full-resolution series lives in the sidecar, never inline).
    inline = row.get("ccc_series")
    if not isinstance(inline, list) or inline:
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series must be an empty list when "
            f"ccc_series_source=={CCC_SERIES_SOURCE_BLOB!r}; got {inline!r}"
        )
    missing = [k for k in CCC_SIDECAR_METADATA_REQUIRED if k not in row]
    if missing:
        raise PromotionError(
            f"per_secondary {sec!r} missing CCC sidecar metadata: {missing!r}"
        )
    if row.get("ccc_series_sidecar_schema_version") != CCC_SIDECAR_SCHEMA_VERSION:
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_sidecar_schema_version must be "
            f"{CCC_SIDECAR_SCHEMA_VERSION!r}; got "
            f"{row.get('ccc_series_sidecar_schema_version')!r}"
        )
    url = row.get("ccc_series_url")
    if not isinstance(url, str) or not _VERCEL_BLOB_URL_RE.match(url):
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_url must be an HTTPS Vercel "
            f"Blob public URL (*.public.blob.vercel-storage.com); got {url!r}"
        )
    # Defence in depth: no local-path leak tokens in the URL/pathname.
    if _BACKSLASH in url or _DRIVE_LETTER_RE.match(url):
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_url contains a local-path token: {url!r}"
        )
    pathname = row.get("ccc_series_pathname")
    if not isinstance(pathname, str) or not _CCC_SIDECAR_PATHNAME_RE.match(pathname):
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_pathname must match the "
            f"immutable k6-mtf/<run>/ccc-series/<slug>.<sha>.json scheme; "
            f"got {pathname!r}"
        )
    sha = row.get("ccc_series_sha256")
    if not isinstance(sha, str) or not _SHA256_HEX_RE.match(sha):
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_sha256 must be a 64-char "
            f"lowercase hex digest; got {sha!r}"
        )
    if sha not in pathname:
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_pathname must embed its sha256"
        )
    if not _validate_int(row.get("ccc_series_points"), positive=False):
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_points must be a non-negative "
            f"int; got {row.get('ccc_series_points')!r}"
        )
    if not _validate_int(row.get("ccc_series_byte_size"), positive=True):
        raise PromotionError(
            f"per_secondary {sec!r} ccc_series_byte_size must be a positive "
            f"int; got {row.get('ccc_series_byte_size')!r}"
        )
    for date_field in ("ccc_series_first_date", "ccc_series_last_date"):
        dv = row.get(date_field)
        if dv is not None and not isinstance(dv, str):
            raise PromotionError(
                f"per_secondary {sec!r} {date_field} must be null or a string; "
                f"got {dv!r}"
            )


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
        # CCC Blob-sidecar rows: validate the off-repo sidecar metadata.
        # A row WITHOUT ccc_series_source is the legacy inline form and is
        # left to the existing ccc_series key-presence check above.
        if row.get("ccc_series_source") is not None:
            _validate_ccc_blob_sidecar_metadata(sec, row)

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
# PR-2a: v2 promotion binding (report-manifest <-> sidecar <-> fixture)
# ---------------------------------------------------------------------------

PHASE5_REPORT_MANIFEST_SCHEMA = "k6_mtf_phase5_report_manifest"
PHASE5_REPORT_MANIFEST_VERSION = "v1"

# Path prefixes allowed for cited paths in the report manifest. These are
# the project-relative roots the report/sidecar/fixture live under.
_ALLOWED_MANIFEST_PATH_PREFIXES = ("output/", "md_library/", "frontend/")


def _assert_no_local_abs(value: Any, *, where: str) -> None:
    """Reject any local absolute path / leak token in a manifest path
    field (drive letter, leading slash, backslash, users/home markers)."""
    if not isinstance(value, str) or not value:
        raise PromotionError(f"{where} must be a non-empty string")
    if _DRIVE_LETTER_RE.match(value):
        raise PromotionError(f"{where} contains a drive letter: {value!r}")
    if value.startswith("/"):
        raise PromotionError(f"{where} starts with absolute slash: {value!r}")
    if _BACKSLASH in value:
        raise PromotionError(f"{where} contains a backslash: {value!r}")
    low = value.lower()
    for marker in _USERNAME_MARKERS:
        if marker in low:
            raise PromotionError(f"{where} contains a local marker {marker!r}: {value!r}")


def _load_validation_sidecar_for_gate(path: Path) -> dict:
    """Load + parse the validation sidecar JSON for the gate's semantic
    binding checks. Fail closed (PromotionError) on any read/parse/shape
    problem."""
    if not path.is_file():
        raise PromotionError(f"validation sidecar file does not exist: {path!s}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(
            f"validation sidecar unreadable/invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PromotionError("validation sidecar root is not a JSON object")
    return payload


def _derive_sidecar_counts(sidecar: Mapping[str, Any]) -> dict:
    """Derive outcome counts from the SIDECAR contents (not the fixture
    or manifest). Fail closed (PromotionError) on non-'valid' status,
    missing alpha, a malformed strategies list, or a 'validated' strategy
    lacking a real finite bh_q_value (same finite-q rule as PR-1)."""
    if sidecar.get("validation_status") != "valid":
        raise PromotionError(
            "validation sidecar validation_status must be 'valid'; got "
            f"{sidecar.get('validation_status')!r}"
        )
    alpha = sidecar.get("multiple_comparisons_control_alpha")
    if not isinstance(alpha, (int, float)):
        raise PromotionError(
            "validation sidecar missing multiple_comparisons_control_alpha"
        )
    alpha = float(alpha)
    strategies = sidecar.get("strategies")
    if not isinstance(strategies, list) or not strategies:
        raise PromotionError(
            "validation sidecar 'strategies' must be a non-empty list"
        )
    tested = len(strategies)
    board = 0
    validated = 0
    empirical_not_run = 0
    empirical_failed = 0
    for s in strategies:
        if not isinstance(s, dict):
            raise PromotionError("validation sidecar strategy is not an object")
        status = s.get("empirical_validation_status")
        if status == "validated":
            validated += 1
            bh_q = s.get("bh_q_value")
            if not _is_finite_number(bh_q):
                raise PromotionError(
                    "validation sidecar has a validated strategy with a "
                    f"non-finite bh_q_value: {s.get('strategy_id')!r} -> {bh_q!r}"
                )
            if float(bh_q) <= alpha:
                board += 1
        elif status == "empirical_not_run":
            empirical_not_run += 1
        elif status == "empirical_failed":
            empirical_failed += 1
    return {
        "tested": tested,
        "board_validated": board,
        "not_validated": tested - board,
        "empirical_validated": validated,
        "empirical_not_run": empirical_not_run,
        "empirical_failed": empirical_failed,
        "validated_but_not_bh": validated - board,
    }


def verify_v2_promotion_binding(
    *,
    fixture_payload: Any,
    report_path: Path,
    report_sha256: str,
    manifest_path: Path,
    validation_sidecar_path: Path,
    validation_sidecar_sha256: str,
    project_root: Path,
) -> dict:
    """Verify report <-> manifest <-> sidecar <-> fixture binding for a v2
    public promotion. Reads the JSON manifest (never Markdown prose).
    Fail-closed: raises ``PromotionError`` (used the same way in dry-run).
    Returns the parsed manifest on success.
    """
    # (1) fixture must be v2 and pass the full PR-1 v2 validator with the
    #     sidecar hash bound to validation_metadata.artifact_sha256.
    if not isinstance(fixture_payload, dict) or (
        fixture_payload.get("schema_version") != SCHEMA_VERSION_V2
    ):
        raise PromotionError(
            "v2 promotion binding requires a k6_mtf_ranking_v2 fixture; got "
            f"{fixture_payload.get('schema_version') if isinstance(fixture_payload, dict) else type(fixture_payload).__name__!r}"
        )
    sidecar_declared = str(validation_sidecar_sha256).strip().lower()
    validate_k6_mtf_ranking_v2_payload(
        fixture_payload,
        validation_sidecar_path=validation_sidecar_path,
        expected_sidecar_sha256=sidecar_declared,
        for_public_promotion=True,
    )

    # (2) report file exists, raw SHA matches supplied, under project root.
    declared_report_sha = str(report_sha256).strip().lower()
    if len(declared_report_sha) != 64 or any(
        c not in "0123456789abcdef" for c in declared_report_sha
    ):
        raise PromotionError(
            f"report SHA-256 is not a 64-char lowercase hex digest: {report_sha256!r}"
        )
    if not report_path.is_file():
        raise PromotionError(f"Phase 5 report file does not exist: {report_path!s}")
    actual_report_sha = _compute_sha256(report_path)
    if actual_report_sha != declared_report_sha:
        raise PromotionError(
            "Phase 5 report SHA-256 mismatch: supplied "
            f"{declared_report_sha}, computed {actual_report_sha}"
        )
    try:
        report_path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise PromotionError(
            "Phase 5 report must live under <PROJECT_DIR>"
        ) from exc

    # (3) manifest exists/readable; expected schema + version.
    if not manifest_path.is_file():
        raise PromotionError(f"report manifest does not exist: {manifest_path!s}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"report manifest unreadable/invalid: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PromotionError("report manifest root is not a JSON object")
    if manifest.get("report_manifest_schema") != PHASE5_REPORT_MANIFEST_SCHEMA:
        raise PromotionError(
            "report manifest schema mismatch: expected "
            f"{PHASE5_REPORT_MANIFEST_SCHEMA!r}, got "
            f"{manifest.get('report_manifest_schema')!r}"
        )
    if manifest.get("version") != PHASE5_REPORT_MANIFEST_VERSION:
        raise PromotionError(
            "report manifest version mismatch: expected "
            f"{PHASE5_REPORT_MANIFEST_VERSION!r}, got {manifest.get('version')!r}"
        )

    # path-privacy on manifest path fields.
    for f in ("report_path", "ranking_artifact_path", "validation_sidecar_path"):
        _assert_no_local_abs(manifest.get(f), where=f"manifest.{f}")
        if not str(manifest.get(f)).startswith(_ALLOWED_MANIFEST_PATH_PREFIXES):
            raise PromotionError(
                f"manifest.{f} is not under an allowed project-relative root: "
                f"{manifest.get(f)!r}"
            )

    # (4) manifest report_sha256 == computed report file SHA.
    if str(manifest.get("report_sha256")).strip().lower() != actual_report_sha:
        raise PromotionError(
            "manifest report_sha256 does not match the report file SHA-256: "
            f"manifest {manifest.get('report_sha256')!r}, file {actual_report_sha}"
        )

    # (5) manifest validation_sidecar_sha256 == supplied/recomputed sidecar SHA.
    actual_sidecar_sha = _compute_sha256(Path(validation_sidecar_path))
    if actual_sidecar_sha != sidecar_declared:
        raise PromotionError(
            "supplied validation sidecar SHA-256 does not match the sidecar "
            f"file: supplied {sidecar_declared}, file {actual_sidecar_sha}"
        )
    if str(manifest.get("validation_sidecar_sha256")).strip().lower() != actual_sidecar_sha:
        raise PromotionError(
            "manifest validation_sidecar_sha256 does not match the sidecar "
            f"file SHA-256: manifest {manifest.get('validation_sidecar_sha256')!r}, "
            f"file {actual_sidecar_sha}"
        )

    # (5b) Parse the sidecar JSON and bind the manifest + fixture to its
    #      ACTUAL contents (semantic binding, not just the file SHA). The
    #      gate reads the JSON sidecar -- never Markdown prose.
    sidecar = _load_validation_sidecar_for_gate(Path(validation_sidecar_path))
    sidecar_counts = _derive_sidecar_counts(sidecar)

    # (5b-i) manifest validation_run_id == sidecar run_id (catches a
    #        manifest that matches the fixture but not the real sidecar).
    if manifest.get("validation_run_id") != sidecar.get("run_id"):
        raise PromotionError(
            "manifest validation_run_id does not match the sidecar run_id: "
            f"manifest {manifest.get('validation_run_id')!r}, sidecar "
            f"{sidecar.get('run_id')!r}"
        )

    # (5b-ii) manifest counts == sidecar-derived counts (carried fields).
    mcounts_chk = manifest.get("counts") or {}
    if not isinstance(mcounts_chk, dict):
        raise PromotionError("manifest.counts must be a JSON object")
    _SIDECAR_COUNT_KEYS = (
        "tested", "board_validated", "not_validated", "empirical_validated",
        "empirical_not_run", "validated_but_not_bh",
    )
    for key in _SIDECAR_COUNT_KEYS:
        if key in mcounts_chk and mcounts_chk.get(key) != sidecar_counts[key]:
            raise PromotionError(
                f"manifest.counts.{key} ({mcounts_chk.get(key)!r}) does not "
                f"match the sidecar-derived count ({sidecar_counts[key]!r})"
            )
    if "empirical_failed" in mcounts_chk and (
        mcounts_chk.get("empirical_failed") != sidecar_counts["empirical_failed"]
    ):
        raise PromotionError(
            "manifest.counts.empirical_failed "
            f"({mcounts_chk.get('empirical_failed')!r}) does not match the "
            f"sidecar-derived count ({sidecar_counts['empirical_failed']!r})"
        )

    # (5b-iii) fixture counts == sidecar-derived counts (overlapping fields).
    fmeta = fixture_payload.get("validation_metadata") or {}
    fsummary = fixture_payload.get("validation_summary") or {}
    fixture_sidecar_pairs = [
        ("validation_metadata.n_strategies_tested",
         fmeta.get("n_strategies_tested"), sidecar_counts["tested"]),
        ("validation_metadata.n_strategies_reported",
         fmeta.get("n_strategies_reported"), sidecar_counts["board_validated"]),
        ("validation_summary.board_validated_count",
         fsummary.get("board_validated_count"), sidecar_counts["board_validated"]),
        ("validation_summary.not_validated_count",
         fsummary.get("not_validated_count"), sidecar_counts["not_validated"]),
        ("validation_summary.displayed_ranked_count",
         fsummary.get("displayed_ranked_count"), sidecar_counts["tested"]),
    ]
    for label, fixture_val, sidecar_val in fixture_sidecar_pairs:
        if fixture_val != sidecar_val:
            raise PromotionError(
                f"fixture {label} ({fixture_val!r}) does not match the "
                f"sidecar-derived count ({sidecar_val!r})"
            )
    esc = fsummary.get("empirical_status_counts")
    if isinstance(esc, dict):
        esc_pairs = [
            ("validated", sidecar_counts["empirical_validated"]),
            ("empirical_not_run", sidecar_counts["empirical_not_run"]),
            ("empirical_failed", sidecar_counts["empirical_failed"]),
        ]
        for key, sidecar_val in esc_pairs:
            if key in esc and esc.get(key) != sidecar_val:
                raise PromotionError(
                    f"fixture validation_summary.empirical_status_counts[{key!r}] "
                    f"({esc.get(key)!r}) does not match the sidecar-derived "
                    f"count ({sidecar_val!r})"
                )

    # (5b-iv) manifest methodology == sidecar methodology fields.
    method = manifest.get("methodology")
    if not isinstance(method, dict):
        raise PromotionError("manifest.methodology must be a JSON object")
    method_pairs = [
        ("n_permutations", sidecar.get("n_permutations")),
        ("n_bootstrap_samples", sidecar.get("n_bootstrap_samples")),
        ("walk_forward_n_folds", sidecar.get("walk_forward_n_folds")),
        ("mc_method", sidecar.get("multiple_comparisons_control_method")),
        ("alpha", sidecar.get("multiple_comparisons_control_alpha")),
        ("contract_version", sidecar.get("validation_contract_version")),
        ("methodology_version", sidecar.get("validation_methodology_version")),
    ]
    for key, sidecar_val in method_pairs:
        if method.get(key) != sidecar_val:
            raise PromotionError(
                f"manifest.methodology.{key} ({method.get(key)!r}) does not "
                f"match the sidecar ({sidecar_val!r})"
            )
    if "bootstrap_ci_level" in sidecar and (
        method.get("bootstrap_ci_level") != sidecar.get("bootstrap_ci_level")
    ):
        raise PromotionError(
            "manifest.methodology.bootstrap_ci_level "
            f"({method.get('bootstrap_ci_level')!r}) does not match the "
            f"sidecar ({sidecar.get('bootstrap_ci_level')!r})"
        )
    # rng_seed: an absent sidecar rng_seed is treated as null.
    if method.get("rng_seed") != sidecar.get("rng_seed"):
        raise PromotionError(
            "manifest.methodology.rng_seed "
            f"({method.get('rng_seed')!r}) does not match the sidecar "
            f"({sidecar.get('rng_seed')!r}; absent treated as null)"
        )

    # (7) manifest validation_run_id == fixture validation_metadata.run_id.
    meta = fixture_payload.get("validation_metadata") or {}
    if manifest.get("validation_run_id") != meta.get("run_id"):
        raise PromotionError(
            "manifest validation_run_id does not match fixture "
            "validation_metadata.run_id: manifest "
            f"{manifest.get('validation_run_id')!r}, fixture {meta.get('run_id')!r}"
        )

    # (8) manifest ranking_run_id == fixture top-level run_id.
    if manifest.get("ranking_run_id") != fixture_payload.get("run_id"):
        raise PromotionError(
            "manifest ranking_run_id does not match fixture run_id: manifest "
            f"{manifest.get('ranking_run_id')!r}, fixture {fixture_payload.get('run_id')!r}"
        )

    # (9) manifest counts == fixture validation_summary / metadata counts.
    summary = fixture_payload.get("validation_summary") or {}
    mcounts = manifest.get("counts") or {}
    if not isinstance(mcounts, dict):
        raise PromotionError("manifest.counts must be a JSON object")
    expected_pairs = [
        ("tested", meta.get("n_strategies_tested")),
        ("board_validated", summary.get("board_validated_count")),
        ("not_validated", summary.get("not_validated_count")),
        ("stage_a_excluded", summary.get("stage_a_excluded_count")),
    ]
    for key, fixture_val in expected_pairs:
        if mcounts.get(key) != fixture_val:
            raise PromotionError(
                f"manifest.counts.{key} ({mcounts.get(key)!r}) does not match "
                f"fixture count ({fixture_val!r})"
            )
    # fixture_schema_version_expected sanity.
    if manifest.get("fixture_schema_version_expected") != SCHEMA_VERSION_V2:
        raise PromotionError(
            "manifest fixture_schema_version_expected must be "
            f"{SCHEMA_VERSION_V2!r}; got "
            f"{manifest.get('fixture_schema_version_expected')!r}"
        )
    return manifest


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
# CCC Blob sidecar extraction + client boundary
# ---------------------------------------------------------------------------


class BlobClientError(PromotionError):
    """Raised when the Blob client boundary refuses (missing object, GET
    failure, hash mismatch, unexpected content, overwrite requirement,
    ambiguous client behavior, or a URL host outside the Vercel Blob public
    host pattern). A subclass of PromotionError so fail-closed callers and
    the CLI surface treat it identically."""


def _secondary_slug(secondary: str) -> str:
    """Deterministic URL-safe slug for a secondary. The original secondary
    is preserved verbatim INSIDE the sidecar JSON; this slug only shapes the
    immutable pathname. Non ``[A-Za-z0-9._-]`` characters collapse to ``_``.
    """
    s = re.sub(r"[^A-Za-z0-9._-]", "_", str(secondary))
    return s or "_"


def _canonical_sidecar_bytes(obj: dict) -> bytes:
    """Canonical, stable JSON bytes for a sidecar (sorted keys, compact
    separators, no trailing newline). The sidecar SHA-256 is computed over
    exactly these bytes, so the encoding must stay deterministic."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _assert_sidecar_no_raw_price(secondary: str, ccc_series: list) -> None:
    """Mode B: a CCC sidecar may carry derived capture fields only. Reject
    any raw-OHLCV key and any non-derived/unknown key in a point."""
    if not isinstance(ccc_series, list):
        raise PromotionError(
            f"ccc sidecar for {secondary!r}: ccc_series must be a list"
        )
    allowed = set(CCC_POINT_REQUIRED_FIELDS)
    for i, point in enumerate(ccc_series):
        if not isinstance(point, dict):
            raise PromotionError(
                f"ccc sidecar for {secondary!r}: point {i} is not an object"
            )
        keys_lower = {str(k).lower() for k in point.keys()}
        bad = keys_lower & CCC_OHLCV_FORBIDDEN_KEYS
        if bad:
            raise PromotionError(
                f"ccc sidecar for {secondary!r}: forbidden raw-price key(s) "
                f"{sorted(bad)!r} in point {i}"
            )
        extra = set(point.keys()) - allowed
        if extra:
            raise PromotionError(
                f"ccc sidecar for {secondary!r}: unexpected non-derived key(s) "
                f"{sorted(extra)!r} in point {i}"
            )


def build_ccc_sidecar(
    ranking_run_id: str, secondary: str, ccc_series: Any,
) -> dict:
    """Build the canonical sidecar object + bytes + metadata for ONE
    secondary. Does NOT upload. Full-resolution: the entire ``ccc_series``
    is carried verbatim (never decimated/truncated)."""
    series = list(ccc_series or [])
    _assert_sidecar_no_raw_price(secondary, series)
    points = len(series)
    first_date = series[0].get("date_utc") if points else None
    last_date = series[-1].get("date_utc") if points else None
    sidecar_obj = {
        "schema_version": CCC_SIDECAR_SCHEMA_VERSION,
        "ranking_run_id": ranking_run_id,
        "secondary": secondary,
        "ccc_series_points": points,
        "ccc_series_first_date": first_date,
        "ccc_series_last_date": last_date,
        "ccc_series": series,
    }
    data = _canonical_sidecar_bytes(sidecar_obj)
    sha = _sha256_bytes(data)
    slug = _secondary_slug(secondary)
    pathname = f"k6-mtf/{ranking_run_id}/ccc-series/{slug}.{sha}.json"
    return {
        "secondary": secondary,
        "sidecar_obj": sidecar_obj,
        "sidecar_bytes": data,
        "sha256": sha,
        "pathname": pathname,
        "byte_size": len(data),
        "points": points,
        "first_date": first_date,
        "last_date": last_date,
    }


def put_and_verify_sidecar(
    client: Any, pathname: str, data: bytes, expected_sha256: str,
) -> dict:
    """Upload (or reuse) one immutable public sidecar through ``client`` and
    GET-verify it by SHA. Fail-closed. ``client`` must implement::

        put(pathname, data, *, overwrite=False) -> {"url": str, "reused"?: bool}
        get(url) -> bytes

    No-overwrite is enforced by the client; this orchestrator ADDITIONALLY
    GET-verifies the public URL's bytes hash against ``expected_sha256`` and
    checks the URL host allowlist. ETag is never treated as the SHA.
    Returns ``{"url", "reused"}``.
    """
    try:
        result = client.put(pathname, data, overwrite=False)
    except BlobClientError:
        raise
    except Exception as exc:  # ambiguous client behavior -> fail closed
        raise BlobClientError(
            f"Blob put failed for {pathname!r}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(result, dict):
        raise BlobClientError(f"Blob put returned non-dict for {pathname!r}")
    url = result.get("url")
    if not isinstance(url, str) or not _VERCEL_BLOB_URL_RE.match(url):
        raise BlobClientError(
            f"Blob put returned a non-allowlisted public URL for {pathname!r}: "
            f"{url!r}"
        )
    try:
        fetched = client.get(url)
    except BlobClientError:
        raise
    except Exception as exc:
        raise BlobClientError(
            f"Blob GET-verify failed for {url!r}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(fetched, (bytes, bytearray)):
        raise BlobClientError(f"Blob GET returned non-bytes for {url!r}")
    actual = _sha256_bytes(bytes(fetched))
    if actual != expected_sha256:
        raise BlobClientError(
            f"Blob GET-verify hash mismatch for {url!r}: expected "
            f"{expected_sha256}, got {actual}"
        )
    return {"url": url, "reused": bool(result.get("reused", False))}


def extract_ccc_to_blob_sidecars(
    payload: Any, *, client: Any, ranking_run_id: str | None = None,
) -> tuple[dict, list]:
    """Move full-resolution CCC off the fixture into immutable per-secondary
    Vercel Blob sidecars. Returns ``(slim_payload, records)``. Does NOT
    mutate the input payload. Exactly one sidecar per ``per_secondary`` row;
    each is uploaded (or reused) and GET-verified by SHA before the slim row
    is stamped with the sidecar metadata."""
    if not isinstance(payload, dict):
        raise PromotionError("v2 payload root is not a JSON object")
    if payload.get("schema_version") != SCHEMA_VERSION_V2:
        raise PromotionError(
            "extract_ccc_to_blob_sidecars requires a k6_mtf_ranking_v2 payload"
        )
    run_id = ranking_run_id or payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise PromotionError("v2 payload missing run_id for sidecar pathnames")
    slim = copy.deepcopy(payload)
    rows = slim.get("per_secondary")
    if not isinstance(rows, list) or not rows:
        raise PromotionError("per_secondary must be a non-empty list")
    records: list = []
    seen_paths: set = set()
    for row in rows:
        sec = row.get("secondary") or "?"
        built = build_ccc_sidecar(run_id, sec, row.get("ccc_series"))
        if built["pathname"] in seen_paths:
            raise PromotionError(
                f"duplicate sidecar pathname collision: {built['pathname']!r}"
            )
        seen_paths.add(built["pathname"])
        put = put_and_verify_sidecar(
            client, built["pathname"], built["sidecar_bytes"], built["sha256"],
        )
        row["ccc_series"] = []
        row["ccc_series_source"] = CCC_SERIES_SOURCE_BLOB
        row["ccc_series_sidecar_schema_version"] = CCC_SIDECAR_SCHEMA_VERSION
        row["ccc_series_url"] = put["url"]
        row["ccc_series_pathname"] = built["pathname"]
        row["ccc_series_sha256"] = built["sha256"]
        row["ccc_series_byte_size"] = built["byte_size"]
        row["ccc_series_points"] = built["points"]
        row["ccc_series_first_date"] = built["first_date"]
        row["ccc_series_last_date"] = built["last_date"]
        records.append({
            "secondary": sec,
            "pathname": built["pathname"],
            "url": put["url"],
            "sha256": built["sha256"],
            "byte_size": built["byte_size"],
            "points": built["points"],
            "reused": put["reused"],
            "get_verified": True,
        })
    return slim, records


def _derive_ccc_storage_summary(payload: dict) -> dict | None:
    """Derive the manifest's ``ccc_series_storage`` summary from a slim
    payload. Returns ``None`` when no row uses a Blob sidecar (v1 / inline
    promotions carry no CCC-storage block)."""
    rows = payload.get("per_secondary") or []
    blob_rows = [
        r for r in rows
        if isinstance(r, dict) and r.get("ccc_series_source") == CCC_SERIES_SOURCE_BLOB
    ]
    if not blob_rows:
        return None
    total_bytes = sum(int(r.get("ccc_series_byte_size") or 0) for r in blob_rows)
    total_points = sum(int(r.get("ccc_series_points") or 0) for r in blob_rows)
    largest = max(int(r.get("ccc_series_byte_size") or 0) for r in blob_rows)
    complete = all(
        all(k in r for k in CCC_SIDECAR_METADATA_REQUIRED) for r in blob_rows
    )
    return {
        "mode": CCC_STORAGE_MODE_BLOB,
        "sidecar_schema_version": CCC_SIDECAR_SCHEMA_VERSION,
        "sidecar_count": len(blob_rows),
        "total_sidecar_bytes": total_bytes,
        "total_sidecar_points": total_points,
        "largest_sidecar_bytes": largest,
        "sidecar_prefix": f"k6-mtf/{payload.get('run_id')}/ccc-series/",
        "url_host_allowlist": ["*.public.blob.vercel-storage.com"],
        "all_sidecars_get_verified": bool(complete),
    }


class VercelBlobClient:
    """Thin Blob client boundary. ``get`` is stdlib HTTPS (urllib) and needs
    no SDK; ``put`` requires an injected ``put_callable`` (the official
    Vercel Blob SDK) -- BUILD-ONLY environments have none, so ``put`` raises
    a clear BlobClientError there.

    The ``BLOB_READ_WRITE_TOKEN`` is read lazily from the environment at put
    time only. It is NEVER stored on the instance, NEVER logged, NEVER placed
    in a URL, and NEVER passed on a command line.
    """

    def __init__(
        self,
        *,
        token_env: str = "BLOB_READ_WRITE_TOKEN",
        put_callable: Any = None,
        get_callable: Any = None,
    ) -> None:
        self._token_env = token_env
        self._put_callable = put_callable
        self._get_callable = get_callable

    def _token(self) -> str:
        tok = os.environ.get(self._token_env)
        if not tok:
            raise BlobClientError(
                f"{self._token_env} is not set in the environment; cannot upload"
            )
        return tok

    def put(self, pathname: str, data: bytes, *, overwrite: bool = False) -> dict:
        if self._put_callable is None:
            raise BlobClientError(
                "no approved Vercel Blob put client is wired; inject "
                "put_callable backed by the official vercel_blob SDK "
                "(BUILD-ONLY environments have no upload client)"
            )
        # Token is resolved here and handed in-process to the injected
        # callable; it is never echoed, logged, or placed in argv/URL.
        return self._put_callable(
            pathname, data, overwrite=overwrite, token=self._token(),
        )

    def get(self, url: str) -> bytes:
        if self._get_callable is not None:
            return self._get_callable(url)
        if not _VERCEL_BLOB_URL_RE.match(url):
            raise BlobClientError(f"refusing GET of non-allowlisted URL: {url!r}")
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
                return resp.read()
        except Exception as exc:
            raise BlobClientError(
                f"HTTPS GET failed for {url!r}: {type(exc).__name__}: {exc}"
            ) from exc


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
    manifest = {
        # "schema_version" records the PROMOTED FIXTURE's schema (v1 or v2),
        # not the manifest's own format. The manifest format marker is the
        # distinct "promotion_manifest_schema_version" field below.
        "schema_version": payload.get("schema_version"),
        "promotion_manifest_schema_version": PROMOTION_MANIFEST_SCHEMA_VERSION,
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
    # Summarize the off-repo CCC Blob sidecars when the promoted fixture is
    # slim (Blob-sourced). v1 / inline promotions get no CCC-storage block.
    ccc_storage = _derive_ccc_storage_summary(payload)
    if ccc_storage is not None:
        manifest["ccc_series_storage"] = ccc_storage
    return manifest


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
    raw_payload = _load_json(inputs.source_path)
    is_v2 = (
        isinstance(raw_payload, dict)
        and raw_payload.get("schema_version") == SCHEMA_VERSION_V2
    )
    if is_v2:
        # PR-2a v2 public-promotion binding path. v1 _validate_payload is
        # NOT used for v2 (it would reject the v2 schema); the v2 validator
        # + report-manifest <-> sidecar <-> fixture binding is enforced
        # here. Same predicate applies in dry-run.
        if not inputs.public_mode:
            raise PromotionError(
                "k6_mtf_ranking_v2 fixtures are handled only by the v2 "
                "public-promotion path; supply --public with the v2 binding "
                "inputs (--phase5-report, --phase5-sha256, "
                "--phase5-report-manifest, --validation-sidecar, "
                "--validation-sidecar-sha256)"
            )
        if inputs.phase5_report_manifest_path is None:
            raise PromotionError(
                "v2 public promotion requires --phase5-report-manifest"
            )
        if inputs.validation_sidecar_path is None or not (
            inputs.validation_sidecar_sha256
        ):
            raise PromotionError(
                "v2 public promotion requires --validation-sidecar and "
                "--validation-sidecar-sha256"
            )
        verify_v2_promotion_binding(
            fixture_payload=raw_payload,
            report_path=inputs.phase5_report_path,
            report_sha256=inputs.phase5_report_sha256,
            manifest_path=inputs.phase5_report_manifest_path,
            validation_sidecar_path=inputs.validation_sidecar_path,
            validation_sidecar_sha256=inputs.validation_sidecar_sha256,
            project_root=inputs.project_root,
        )
        payload = raw_payload
    else:
        payload = _validate_payload(raw_payload)
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
        "--phase5-report-manifest", default=None,
        help=(
            "Path to the paired Phase 5 report JSON manifest (required for "
            "a k6_mtf_ranking_v2 public promotion). The gate reads this "
            "manifest for binding checks; it never parses Markdown prose."
        ),
    )
    p.add_argument(
        "--validation-sidecar", default=None,
        help=(
            "Path to the validation sidecar (required for a "
            "k6_mtf_ranking_v2 public promotion)."
        ),
    )
    p.add_argument(
        "--validation-sidecar-sha256", default=None,
        help=(
            "Expected SHA-256 of the validation sidecar (required for a "
            "k6_mtf_ranking_v2 public promotion)."
        ),
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
    phase5_report_manifest = (
        Path(args.phase5_report_manifest).resolve()
        if getattr(args, "phase5_report_manifest", None) else None
    )
    validation_sidecar = (
        Path(args.validation_sidecar).resolve()
        if getattr(args, "validation_sidecar", None) else None
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
        phase5_report_manifest_path=phase5_report_manifest,
        validation_sidecar_path=validation_sidecar,
        validation_sidecar_sha256=getattr(args, "validation_sidecar_sha256", None),
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
