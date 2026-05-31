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
    # secondaries_ranked vs per_secondary coherence:
    # - same count
    # - same ticker set
    # - secondaries_ranked order coherently matches the ranked
    #   per_secondary order (records with non-null integer rank,
    #   sorted by rank ascending).
    row_tickers = [r.get("secondary") for r in rows]
    if len(ranked_list) != len(rows):
        raise PromotionError(
            f"secondaries_ranked length {len(ranked_list)} != per_secondary length {len(rows)}"
        )
    if set(ranked_list) != set(row_tickers):
        raise PromotionError(
            "secondaries_ranked ticker set does not match per_secondary ticker set"
        )
    ranked_records = [
        r for r in rows if isinstance(r.get("rank"), int)
    ]
    ranked_records_sorted = sorted(
        ranked_records, key=lambda r: r.get("rank"),
    )
    expected_ranked_prefix = [
        r.get("secondary") for r in ranked_records_sorted
    ]
    if ranked_list[: len(expected_ranked_prefix)] != expected_ranked_prefix:
        raise PromotionError(
            "secondaries_ranked order does not match per_secondary rank ordering"
        )
    return payload


# ---------------------------------------------------------------------------
# Public-mode safety: Phase 5 verification (load-bearing)
# ---------------------------------------------------------------------------


def _verify_phase5_inputs(
    public_mode: bool,
    report_path: Path | None,
    declared_sha256: str | None,
) -> None:
    """Hard refuse if public mode is requested without a verified
    Phase 5 honest-validation report. Same predicate applies in
    dry-run; there is no code path that produces a public-ready
    manifest without a verified Phase 5 report.
    """
    if not public_mode:
        return
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


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def _build_manifest(
    inputs: PromotionInputs,
    outcome: ValidationOutcome,
    promoted_at_utc: str,
) -> dict:
    payload = outcome.payload
    if inputs.public_mode:
        rp = inputs.phase5_report_path
        rp_str: str
        if rp is None:
            # Refused earlier; defensive guard.
            raise PromotionError(
                "public mode reached manifest build without phase5 report; refusing"
            )
        try:
            rp_resolved = rp.resolve()
            project_root_resolved = inputs.project_root.resolve()
            rel = rp_resolved.relative_to(project_root_resolved).as_posix()
            rp_str = rel
        except ValueError:
            # Report lives outside <PROJECT_DIR>; record absolute-form
            # POSIX path. The public-mode SHA verification already
            # happened against the file's bytes; this string is a
            # provenance label, not a re-fetch target.
            rp_str = rp.as_posix()
        validation_results: Any = {
            "phase_5_validation_report_path": rp_str,
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
    #    Same predicate applies in dry-run.
    _verify_phase5_inputs(
        public_mode=inputs.public_mode,
        report_path=inputs.phase5_report_path,
        declared_sha256=inputs.phase5_report_sha256,
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
    manifest = _build_manifest(inputs, outcome, promoted_at_utc)
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
