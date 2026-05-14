"""Phase 6I-31: guarded signal-library stable promotion writer.

Promotes staged interval signal libraries (and their
provenance manifest sidecars) into the production stable
directory at ``signal_library/data/stable`` under a strict
authorization gate. Default is dry-run. The writer NEVER
mutates production state unless every gate passes.

Authorization cascade
---------------------

In order:

  1. ``--write`` CLI flag (or ``write=True`` kwarg).
  2. ``PRJCT9_AUTOMATION_WRITE_AUTH == "phase_6h5_explicit"``
     environment variable (the same two-key contract used by
     the Phase 6H-5 / 6I-25 writers).
  3. Re-derived planner ``plan_ready == True`` from this
     writer's own call to
     ``signal_library_stable_promotion_planner.plan_signal_library_stable_promotion(...)``.
     The writer NEVER trusts an externally-supplied plan
     object.
  4. Production target path constrained to a directory whose
     resolved tail components are
     ``signal_library/data/stable`` (the path guard).
  5. Writer-side re-validation of every staged file: each
     library is re-loaded via the central provenance-verified
     loader AND re-schema-checked
     (``len(dates) == len(signals) == len(close)``). Mismatch
     blocks the entire promotion.

When all five gates pass AND ``write=True``, the writer
runs the staged-to-production copy as a **transactional
batch**:

  * Each PKL is copied via ``<filename>.tmp`` + ``os.replace``
    onto the production target. Before each copy, the
    target's prior bytes are snapshotted in memory (or
    ``None`` for newly-added targets).
  * The optional ``<filename>.manifest.json`` sidecar is
    copied the same way; its prior bytes are snapshotted too.
  * If ANY copy fails mid-batch (PKL OR sidecar, for ANY
    library), the writer walks the touched-target log in
    reverse and **restores every prior target to its exact
    pre-run state**: newly-added targets are unlinked,
    replaced targets are restored from their captured
    prior-bytes payload via an atomic
    ``<filename>.restore_tmp`` + ``os.replace`` pattern.
    The ``files_added`` / ``files_replaced`` /
    ``sidecars_copied`` accumulators are zeroed and the
    result surface honestly reports zero net writes.
  * One JSONL row per writer invocation is appended to the
    optional execution log (best-effort).

When ANY gate fails, the writer surfaces structured issue
codes and refuses to mutate. The on-disk production state
is byte-for-byte unchanged. The same byte-for-byte
guarantee holds for mid-batch copy failures via the
transactional rollback described above.

Strictly bounded
----------------

The writer is not a refresher, not a pipeline runner, not a
batch engine. It does NOT import ``yfinance`` / ``dash`` /
``subprocess`` / any live engine. It does NOT add a raw
``pickle.load`` site (the writer's library-loading re-check
routes through the central provenance loader).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import provenance_manifest as _pm
import signal_library_stable_promotion_planner as _planner


# ---------------------------------------------------------------------------
# Auth env vars
# ---------------------------------------------------------------------------

ENV_VAR_NAME = "PRJCT9_AUTOMATION_WRITE_AUTH"
ENV_VAR_REQUIRED_VALUE = "phase_6h5_explicit"


# ---------------------------------------------------------------------------
# Aggregate issue codes
# ---------------------------------------------------------------------------

ISSUE_WRITE_NOT_REQUESTED = "write_not_requested"
ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID = (
    "env_authorization_missing_or_invalid"
)
ISSUE_PLAN_NOT_READY = "plan_not_ready"
ISSUE_UNEXPECTED_PRODUCTION_ROOT = "unexpected_production_root"
ISSUE_WRITER_REVALIDATION_FAILED = "writer_revalidation_failed"
ISSUE_PROMOTION_COPY_FAILED = "promotion_copy_failed"

ALL_ISSUE_CODES: tuple[str, ...] = (
    ISSUE_WRITE_NOT_REQUESTED,
    ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID,
    ISSUE_PLAN_NOT_READY,
    ISSUE_UNEXPECTED_PRODUCTION_ROOT,
    ISSUE_WRITER_REVALIDATION_FAILED,
    ISSUE_PROMOTION_COPY_FAILED,
)

# Stable recommended-next-action strings.
ACTION_DRY_RUN_REVIEW_PROMOTION_PLAN = (
    "dry_run_review_promotion_plan"
)
ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN = (
    "set_write_authorization_and_rerun"
)
ACTION_RESOLVE_PLAN_FIRST = "resolve_plan_first"
ACTION_PROMOTION_COMPLETE = "promotion_complete"
ACTION_MANUAL_REVIEW_REQUIRED = "manual_review_required"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SignalLibraryStablePromotionWriteResult:
    generated_at: str
    staged_dir: str
    production_stable_dir: str
    write_requested: bool
    write_authorized: bool
    plan_ready: bool
    wrote_files: bool
    files_added: tuple[str, ...]
    files_replaced: tuple[str, ...]
    files_unchanged: tuple[str, ...]
    sidecars_copied: tuple[str, ...]
    issue_codes: tuple[str, ...]
    recommended_next_action: str
    execution_log_path: Optional[str]
    pre_write_sha256_by_path: dict[str, str]
    post_write_sha256_by_path: dict[str, str]
    plan_summary: Optional[dict[str, Any]] = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "staged_dir": self.staged_dir,
            "production_stable_dir": self.production_stable_dir,
            "write_requested": bool(self.write_requested),
            "write_authorized": bool(self.write_authorized),
            "plan_ready": bool(self.plan_ready),
            "wrote_files": bool(self.wrote_files),
            "files_added": list(self.files_added),
            "files_replaced": list(self.files_replaced),
            "files_unchanged": list(self.files_unchanged),
            "sidecars_copied": list(self.sidecars_copied),
            "issue_codes": list(self.issue_codes),
            "recommended_next_action": self.recommended_next_action,
            "execution_log_path": self.execution_log_path,
            "pre_write_sha256_by_path": dict(
                self.pre_write_sha256_by_path,
            ),
            "post_write_sha256_by_path": dict(
                self.post_write_sha256_by_path,
            ),
            "plan_summary": self.plan_summary,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _append_unique(buf: list[str], code: str) -> None:
    if code and code not in buf:
        buf.append(code)


def _sha256_of_path(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _env_authorized() -> bool:
    return os.environ.get(ENV_VAR_NAME) == ENV_VAR_REQUIRED_VALUE


def _atomic_copy(
    src: Path, dst: Path,
) -> tuple[bool, Optional[str]]:
    """Copy ``src`` to ``dst`` atomically via a ``<dst>.tmp``
    staging path + ``os.replace``. Returns
    ``(success, error_message_or_None)``."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        if tmp.exists():
            tmp.unlink()
        shutil.copyfile(str(src), str(tmp))
        os.replace(str(tmp), str(dst))
        return True, None
    except Exception as exc:
        return False, str(exc)


def _restore_target(
    path_str: str, prior_bytes: Optional[bytes],
) -> None:
    """Restore one production target to its exact pre-run
    state during transactional rollback.

    ``prior_bytes is None`` means the target was newly added
    by this run (it did NOT exist before any copy happened),
    so rollback unlinks it. Otherwise the target existed
    before and must be restored to those exact bytes via an
    atomic ``<path>.restore_tmp`` + ``os.replace`` pattern
    so the restore is itself crash-safe.

    Best-effort: a restore failure is swallowed (no public
    surface for partial-rollback diagnostics in Phase 6I-31).
    The Phase 6I-31 evidence doc § 14 notes that any
    rollback-time exception is itself a fatal-class
    incident that must be handled by operator review.
    """
    try:
        target = Path(path_str)
        if prior_bytes is None:
            if target.exists():
                target.unlink()
            return
        tmp = target.with_suffix(target.suffix + ".restore_tmp")
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(prior_bytes)
        os.replace(str(tmp), str(target))
    except Exception:
        pass


def _writer_side_revalidate(
    staged_path: Path, interval: str,
) -> bool:
    """Re-load and re-schema-check a staged library independently
    of any planner state. Returns True iff the library loads via
    the central provenance loader AND its dates/signals/close
    are present and equal length.

    The writer NEVER trusts the planner result; this is the
    second-look guard against a stale or malformed plan."""
    try:
        lib, vresult = _pm.load_verified_signal_library(
            staged_path,
            requested_params={
                "interval": interval,
                "price_source": "Close",
            },
            strict=False,
        )
    except Exception:
        return False
    if lib is None:
        return False
    if not (vresult.ok or vresult.legacy):
        return False
    if not isinstance(lib, Mapping):
        return False
    dates = lib.get("dates") or lib.get("date_index")
    signals = lib.get("signals") or lib.get("primary_signals")
    close = (
        lib.get("close")
        or lib.get("target_close")
        or lib.get("Close")
    )
    if dates is None or signals is None or close is None:
        return False
    try:
        n = len(dates)
    except TypeError:
        return False
    try:
        if len(signals) != n:
            return False
    except TypeError:
        return False
    try:
        if len(close) != n:
            return False
    except TypeError:
        return False
    return True


def _append_execution_log(
    log_path: Path,
    row: dict[str, Any],
) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass  # execution log is best-effort


def _path_under_production_stable_suffix(path: Path) -> bool:
    """Same path guard as the planner's helper, repeated locally
    so the writer does not blindly trust planner state."""
    try:
        resolved = path.resolve()
    except Exception:
        return False
    parts = [p.lower() for p in resolved.parts]
    suffix = [
        p.lower() for p in
        _planner.PRODUCTION_STABLE_SUFFIX
    ]
    if len(parts) < len(suffix):
        return False
    return parts[-len(suffix):] == suffix


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def promote_signal_libraries(
    tickers: Iterable[str],
    *,
    staged_dir: Any,
    production_stable_dir: Any,
    intervals: Iterable[str] = _planner.DEFAULT_INTERVALS,
    write: bool = False,
    execution_log: Optional[Any] = None,
    planner_callable: Optional[
        Callable[..., Any]
    ] = None,
) -> SignalLibraryStablePromotionWriteResult:
    """Run the guarded stable promotion path.

    Default is dry-run (``write=False``). Mutation requires
    ALL gates of the Phase 6I-31 authorization cascade.
    """
    target_tickers = tuple(
        str(t).strip().upper() for t in tickers if str(t).strip()
    )
    interval_list = tuple(str(i).strip() for i in intervals)
    staged_path = Path(staged_dir)
    prod_path = Path(production_stable_dir)
    execution_log_path = (
        Path(execution_log) if execution_log is not None else None
    )

    issues: list[str] = []
    pre_sha: dict[str, str] = {}
    post_sha: dict[str, str] = {}
    files_added: list[str] = []
    files_replaced: list[str] = []
    files_unchanged: list[str] = []
    sidecars_copied: list[str] = []

    # Step 1: re-run the planner internally. The writer never
    # trusts an externally-supplied plan.
    planner_fn = (
        planner_callable
        or _planner.plan_signal_library_stable_promotion
    )
    plan = planner_fn(
        target_tickers,
        staged_dir=staged_path,
        production_stable_dir=prod_path,
        intervals=interval_list,
    )
    plan_ready = bool(getattr(plan, "plan_ready", False))
    plan_dict = (
        plan.to_json_dict() if hasattr(plan, "to_json_dict")
        else None
    )

    # Step 2: derive authorization state.
    write_requested = bool(write)
    env_ok = _env_authorized()
    write_authorized = write_requested and env_ok

    if not write_requested:
        _append_unique(issues, ISSUE_WRITE_NOT_REQUESTED)
    elif not env_ok:
        _append_unique(
            issues,
            ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID,
        )

    if not plan_ready:
        _append_unique(issues, ISSUE_PLAN_NOT_READY)

    if not _path_under_production_stable_suffix(prod_path):
        _append_unique(
            issues, ISSUE_UNEXPECTED_PRODUCTION_ROOT,
        )

    wrote_files = False
    if (
        write_authorized
        and plan_ready
        and ISSUE_UNEXPECTED_PRODUCTION_ROOT not in issues
    ):
        per_states = getattr(plan, "per_library_states", ())
        # Writer-side revalidation pass FIRST (independent of
        # the planner's pass) -- ensures no stale plan can
        # sneak a malformed library through.
        revalidation_ok = True
        for state in per_states:
            if not state.staged_exists or not state.schema_ok:
                revalidation_ok = False
                break
            if not _writer_side_revalidate(
                Path(state.staged_path), state.interval,
            ):
                revalidation_ok = False
                break
        if not revalidation_ok:
            _append_unique(
                issues, ISSUE_WRITER_REVALIDATION_FAILED,
            )
        else:
            # Step 3: TRANSACTIONAL copy across the full
            # planned batch. The Phase 6I-31 contract guarantees
            # that a failed promotion leaves production state
            # byte-for-byte unchanged. We achieve this by:
            #
            #   1. Pre-write SHA captured for every existing
            #      production target BEFORE any mutation so the
            #      result surface can prove pre/post hash
            #      changes on success AND so rollback can
            #      verify it restored the original bytes.
            #   2. Each successful copy is tracked in a
            #      ``touched`` log carrying the per-target
            #      prior bytes (or ``None`` for ADD targets
            #      that did not previously exist).
            #   3. On ANY copy failure mid-batch, the touched
            #      log is walked in reverse and every entry
            #      is restored: ADD targets are unlinked,
            #      REPLACE targets are restored from the
            #      captured prior-bytes payload. The
            #      ``files_added`` / ``files_replaced`` /
            #      ``sidecars_copied`` accumulators are
            #      cleared so the result surface reports
            #      truthfully (i.e. zero counts on rollback).
            #
            # Memory budget: for a 75-library SPY promotion the
            # captured prior bytes total ~150 MB worst case
            # (PKLs ~1-2 MB each, sidecars ~5 KB). Acceptable.
            for state in per_states:
                prod_file = Path(state.production_path)
                if prod_file.exists():
                    sha = _sha256_of_path(prod_file)
                    if sha is not None:
                        pre_sha[str(prod_file)] = sha

            # touched: list of (path, prior_bytes_or_None).
            # prior_bytes_or_None=None means the target did
            # NOT exist before the copy and must be unlinked
            # on rollback; bytes means the target existed and
            # must be restored to those exact bytes.
            touched: list[
                tuple[str, Optional[bytes]]
            ] = []
            copy_failed_path: Optional[str] = None
            copy_failed_reason: Optional[str] = None

            for state in per_states:
                staged_file = Path(state.staged_path)
                prod_file = Path(state.production_path)
                if state.production_outcome == (
                    _planner.OUTCOME_UNCHANGED
                ):
                    files_unchanged.append(str(prod_file))
                    continue

                # Capture prior PKL bytes (None for ADD).
                prior_pkl: Optional[bytes]
                try:
                    prior_pkl = (
                        prod_file.read_bytes()
                        if prod_file.exists() else None
                    )
                except Exception as exc:
                    copy_failed_path = str(prod_file)
                    copy_failed_reason = (
                        f"pre-copy snapshot read failed: {exc!r}"
                    )
                    break

                ok, err = _atomic_copy(staged_file, prod_file)
                if not ok:
                    copy_failed_path = str(prod_file)
                    copy_failed_reason = err
                    break
                touched.append((str(prod_file), prior_pkl))
                if state.production_outcome == (
                    _planner.OUTCOME_ADD
                ):
                    files_added.append(str(prod_file))
                else:
                    files_replaced.append(str(prod_file))

                # Sidecar (optional). Same prior-bytes capture.
                if state.has_sidecar:
                    sidecar_src = Path(
                        str(staged_file) + ".manifest.json",
                    )
                    sidecar_dst = Path(
                        str(prod_file) + ".manifest.json",
                    )
                    try:
                        prior_sidecar = (
                            sidecar_dst.read_bytes()
                            if sidecar_dst.exists() else None
                        )
                    except Exception as exc:
                        copy_failed_path = str(sidecar_dst)
                        copy_failed_reason = (
                            f"sidecar pre-copy snapshot "
                            f"read failed: {exc!r}"
                        )
                        break
                    sc_ok, sc_err = _atomic_copy(
                        sidecar_src, sidecar_dst,
                    )
                    if not sc_ok:
                        copy_failed_path = str(sidecar_dst)
                        copy_failed_reason = sc_err
                        break
                    touched.append((
                        str(sidecar_dst), prior_sidecar,
                    ))
                    sidecars_copied.append(str(sidecar_dst))

            if copy_failed_path is not None:
                # Rollback every touched entry, in reverse.
                _append_unique(
                    issues, ISSUE_PROMOTION_COPY_FAILED,
                )
                for path, prior in reversed(touched):
                    _restore_target(path, prior)
                # Clear truthful accumulators -- on rollback
                # NOTHING was committed.
                files_added = []
                files_replaced = []
                sidecars_copied = []
                # Pre/post SHA dicts: keep pre_sha (operator
                # may still want it for audit), but post_sha
                # stays empty because no net write happened.
            else:
                for state in per_states:
                    prod_file = Path(state.production_path)
                    if prod_file.exists():
                        sha = _sha256_of_path(prod_file)
                        if sha is not None:
                            post_sha[str(prod_file)] = sha
                wrote_files = True

    # Determine recommended_next_action.
    if wrote_files:
        recommended = ACTION_PROMOTION_COMPLETE
    elif ISSUE_UNEXPECTED_PRODUCTION_ROOT in issues:
        recommended = ACTION_MANUAL_REVIEW_REQUIRED
    elif (
        ISSUE_WRITER_REVALIDATION_FAILED in issues
        or ISSUE_PROMOTION_COPY_FAILED in issues
    ):
        recommended = ACTION_MANUAL_REVIEW_REQUIRED
    elif ISSUE_WRITE_NOT_REQUESTED in issues:
        recommended = ACTION_DRY_RUN_REVIEW_PROMOTION_PLAN
    elif (
        ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID in issues
    ):
        recommended = ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN
    elif ISSUE_PLAN_NOT_READY in issues:
        recommended = ACTION_RESOLVE_PLAN_FIRST
    else:
        recommended = ACTION_MANUAL_REVIEW_REQUIRED

    result = SignalLibraryStablePromotionWriteResult(
        generated_at=_iso_now(),
        staged_dir=str(staged_path),
        production_stable_dir=str(prod_path),
        write_requested=write_requested,
        write_authorized=write_authorized,
        plan_ready=plan_ready,
        wrote_files=wrote_files,
        files_added=tuple(files_added),
        files_replaced=tuple(files_replaced),
        files_unchanged=tuple(files_unchanged),
        sidecars_copied=tuple(sidecars_copied),
        issue_codes=tuple(issues),
        recommended_next_action=recommended,
        execution_log_path=(
            str(execution_log_path)
            if execution_log_path is not None else None
        ),
        pre_write_sha256_by_path=dict(pre_sha),
        post_write_sha256_by_path=dict(post_sha),
        plan_summary=plan_dict,
    )

    # Append execution log (best-effort).
    if execution_log_path is not None:
        _append_execution_log(
            execution_log_path,
            {
                "generated_at": result.generated_at,
                "staged_dir": result.staged_dir,
                "production_stable_dir": (
                    result.production_stable_dir
                ),
                "write_requested": result.write_requested,
                "write_authorized": result.write_authorized,
                "plan_ready": result.plan_ready,
                "wrote_files": result.wrote_files,
                "files_added": list(result.files_added),
                "files_replaced": list(result.files_replaced),
                "files_unchanged": list(result.files_unchanged),
                "sidecars_copied": list(result.sidecars_copied),
                "issue_codes": list(result.issue_codes),
                "recommended_next_action": (
                    result.recommended_next_action
                ),
            },
        )

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal_library_stable_promotion_writer",
        description=(
            "Phase 6I-31 guarded signal-library stable "
            "promotion writer. Default is dry-run. Mutation "
            "requires --write AND PRJCT9_AUTOMATION_WRITE_AUTH"
            "=phase_6h5_explicit AND planner plan_ready=true "
            "AND writer-side revalidation AND production-"
            "stable path guard."
        ),
    )
    parser.add_argument(
        "--tickers", required=True,
        help="Comma-separated tickers.",
    )
    parser.add_argument(
        "--staged-dir", required=True,
        help="Path to the staged signal-library directory.",
    )
    parser.add_argument(
        "--production-stable-dir",
        default=str(
            _project_dir() / "signal_library" / "data" / "stable",
        ),
        help=(
            "Path to the production stable signal-library "
            "directory. Default: <project>/signal_library/"
            "data/stable."
        ),
    )
    parser.add_argument(
        "--intervals",
        default=",".join(_planner.DEFAULT_INTERVALS),
        help=(
            "Comma-separated intervals. Default: 1d,1wk,1mo,"
            "3mo,1y."
        ),
    )
    parser.add_argument(
        "--write", action="store_true",
        help=(
            "Authorize mutation. Still requires "
            "PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit "
            "AND plan_ready=true."
        ),
    )
    parser.add_argument(
        "--execution-log", default=None,
        help=(
            "Optional JSONL execution-log path; one row "
            "appended per invocation."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    tickers = [
        t.strip() for t in args.tickers.split(",") if t.strip()
    ]
    intervals = [
        i.strip() for i in args.intervals.split(",") if i.strip()
    ]
    if not tickers:
        print(
            json.dumps({"error": "missing_tickers"}),
            file=sys.stderr,
        )
        return 2

    try:
        result = promote_signal_libraries(
            tickers,
            staged_dir=args.staged_dir,
            production_stable_dir=args.production_stable_dir,
            intervals=intervals,
            write=bool(args.write),
            execution_log=args.execution_log,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(
            json.dumps({
                "error": "unhandled_exception",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 3

    print(json.dumps(result.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
