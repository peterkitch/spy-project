"""Re-rank driver v1 -- autonomous nightly whole-board re-score.

Zero-question driver: the live board IS the selection. It re-scores every board
secondary to one current as-of date and republishes, reusing the merged
run_rerank_publish seam (crunch_rebuild_orchestrator.py) as the ONLY publish
entry point. Per the ratified phase decisions:

  - Pipeline shape: ONE batch k6_recook restage only (NO OnePass / ImpactSearch /
    StackBuilder). Member discovery + selection are a separate periodic rebuild.
  - Quarantine v1: k6_recook runs with --allow-stage-a-exclusions; allowable
    per-secondary exclusions quarantine those secondaries (their prior row
    carries through combine); network/provider/systemic failures HALT with no
    partial publish.
  - Validation: D1 v1 ratified -- every run performs FULL validation over the
    surviving fresh set. No metrics-only mode, no validation-stamp preservation.
  - Dry-run disclosure: --publish-dry-run is publication-closed but NOT
    network/Blob-closed; it performs real fresh CCC Blob upload/GET through
    Stage 9, then stops at the promote dry-run gate.

The driver is a thin caller: it provides the engine seams (validator / joiner /
stage9_runner) to run_rerank_publish; the publish glue (sidecar assertion,
fresh-row normalization, Stage9PublishInputs, combine) lives inside the seam.

All runtime output is ASCII-only. The Blob token is checked for PRESENCE only;
its value is never read into output. Operator-facing terminal/status output may
include local run paths; public fixture/report artifacts never do (those are
written by the unchanged promote helper inside Stage 9).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, TextIO

try:  # stdlib on the pinned 3.12 env
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - defensive
    ZoneInfo = None  # type: ignore

# The seam is imported at module load (side-effect-free: it pulls in no engines;
# stage9_publish / combine / the join are lazily imported inside the seam).
import crunch_rebuild_orchestrator as _cro
from crunch_rebuild_orchestrator import (  # noqa: F401 - re-exported for callers
    run_rerank_publish, RerankPublishSeams)
# AUTHORITY SHIFT (pilot 20260612T002302Z forensics): the recook-outcome
# classifier no longer re-derives per-record allowability from k6_recook's
# STAGE_A_ALLOWABLE_KINDS. The engine itself decides blocking-vs-allowable at the
# RUN level (blocking => status='failed'/exit_code=1/non-empty failures; an
# allowable partial => status='partial'/exit_code=3/failures empty/
# partial_reasons=['stage_a_allowed_exclusions'], k6_recook.py:2492,:2731-2744).
# The per-record gate wrongly halted on legitimate Stage-Aprime caret exclusions
# that the engine folds under the same allowable partial, so it is removed and
# the constant is no longer imported.


# --- fixed surface ----------------------------------------------------------

# CLAUDE.md PART C1 pinned interpreter (MACHINE-SPECIFIC; argv array, no shell).
PINNED_PYTHON = "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe"
K6_RECOOK_SCRIPT = "k6_recook.py"

TOKEN_ENV = "BLOB_READ_WRITE_TOKEN"

DEFAULT_FIXTURE = "frontend/public/fixtures/k6_mtf_ranking.json"
DEFAULT_PROMOTION_MANIFEST = (
    "frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json")
DEFAULT_STACKBUILDER_ROOT = "output/stackbuilder"
DEFAULT_K6_OUTPUT_ROOT = "output/k6_mtf"
DEFAULT_CRUNCH_RUNS_ROOT = "output/crunch_runs"
LOCK_NAME = ".crunch.lock"

STATUS_REL = "output/rerank/latest_status.json"
# The status pointer + the run dir live under output/, ignored by the project
# .gitignore rule ``output/`` (so nothing the driver writes is ever tracked).
STATUS_GITIGNORE_RULE = "output/ (project .gitignore)"

DEFAULT_OPERATOR_BUDGET_LABEL = "rerank-nightly"

ET_ZONE = "America/New_York"
MARKET_CLOSE_HOUR_ET = 16

# 2026 NYSE full-day holidays (injectable/overridable via main(holidays=...)).
# Used only to walk the derived target back to the latest completed trading
# close; k6_recook's fresh_enough gate is the authoritative data-shortfall check.
# MAINTENANCE: this table is 2026-only and MUST be extended with the 2027 NYSE
# holidays before 2027-01-01 (or supply main(holidays=...) from a calendar
# source); without it, a 2027 holiday would derive a non-trading target.
_US_MARKET_HOLIDAYS_2026 = frozenset({
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Washington's Birthday
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # Independence Day (observed)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
})


class RerankError(Exception):
    """Driver-level fail-closed error."""


def _norm(value: object) -> str:
    return str(value).strip().upper()


# --- board enumeration ------------------------------------------------------


def enumerate_board(fixture_path: Path) -> list[str]:
    """The full current board secondary set (sorted, de-duped) from the live
    fixture. The board IS the selection -- there is no ticker question."""
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    out: set[str] = set()
    for row in data.get("per_secondary") or []:
        if isinstance(row, dict):
            sec = row.get("secondary")
        else:
            sec = row
        if sec:
            out.add(_norm(sec))
    return sorted(out)


# --- target-as-of derivation ------------------------------------------------


def _is_trading_day(d: date, holidays: Iterable[date]) -> bool:
    return d.weekday() < 5 and d not in set(holidays)


def derive_target_as_of(*, now: datetime,
                        holidays: Iterable[date] = (),
                        close_hour: int = MARKET_CLOSE_HOUR_ET) -> str:
    """Latest COMPLETED US trading close as YYYY-MM-DD, in US/Eastern. If the
    current ET day is a trading day and it is at/after the market close hour,
    that day's close is complete; otherwise walk back to the most recent prior
    trading day (skipping weekends + holidays). ``now`` must be tz-aware."""
    if ZoneInfo is None:  # pragma: no cover - defensive
        raise RerankError("zoneinfo unavailable; cannot derive ET target")
    et = now.astimezone(ZoneInfo(ET_ZONE))
    today = et.date()
    after_close = et.hour >= close_hour
    if _is_trading_day(today, holidays) and after_close:
        cand = today
    else:
        cand = today - timedelta(days=1)
    while not _is_trading_day(cand, holidays):
        cand -= timedelta(days=1)
    return cand.isoformat()


# --- recook argv + outcome --------------------------------------------------


def build_recook_argv(*, secondaries: Sequence[str], target_as_of: str,
                      stackbuilder_root: Path, output_root: Path,
                      driver_run_id: str, python: str = PINNED_PYTHON,
                      script: str = K6_RECOOK_SCRIPT) -> list[str]:
    """Compose the ONE batch k6_recook restage command (argv array, no shell).
    Re-score existing selected stacks only -- explicitly NO OnePass /
    ImpactSearch / StackBuilder. --allow-stage-a-exclusions makes an allowable
    per-secondary Stage-A unavailability a quarantine instead of a hard stop.
    --allow-aprime-caret-cache-alias lets the Stage Aprime caret bridge build a
    caret/index secondary from its current underscore-alias cache PKL (e.g.
    '_GSPC') when the raw '^GSPC' source is missing/stale -- local-only, no
    extra fetch, caret rows only, no effect on non-caret secondaries."""
    return [
        python,
        script,
        "--execute",
        "--allow-network-fetch",
        "--allow-stage-a-exclusions",
        "--allow-aprime-caret-cache-alias",
        "--restage-all",
        "--secondaries", ",".join(secondaries),
        "--target-as-of", target_as_of,
        "--driver-run-id", driver_run_id,
        "--stackbuilder-root", str(stackbuilder_root),
        "--output-root", str(output_root),
    ]


def read_ranking_kept_secondaries(ranking_path: Path) -> Optional[list]:
    """The engine's AUTHORITATIVE final kept set: the secondaries written to the
    k6 ranking artifact (post Stage A/Aprime/B/E). Returns a normalized list, or
    None if the artifact is absent/unreadable (e.g. a blocking halt wrote none).
    The envelope exposes no top-level kept list -- only counts -- so the written
    ranking is the source of truth for what survived."""
    p = Path(ranking_path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = data.get("per_secondary")
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        sec = r.get("secondary") if isinstance(r, dict) else r
        if sec:
            out.append(_norm(sec))
    return out


def parse_recook_outcome(envelope: Any, board: Sequence[str], *,
                         kept_secondaries: Optional[Sequence[str]] = None) -> dict:
    """Classify a k6_recook batch envelope by the ENGINE's own run-level
    authority -- the engine already decides blocking-vs-allowable, so the driver
    trusts that verdict instead of re-deriving allowability per exclusion record.

    Two publishable shapes:
      1. CLEAN full board: status == 'ok' AND exit_code == 0 AND no failures AND
         no exclusions AND halted_at is None (k6_recook.py:2741-2744).
      2. ALLOWABLE partial: status == 'partial' AND exit_code == 3 AND failures
         empty AND halted_at is None AND at least one exclusion
         (k6_recook.py:2492,:2731-2744). The cumulative exclusions
         (envelope['exclusions'] == driver.exclusions, k6_recook.py:1766) are NOT
         a halt authority here -- they are the QUARANTINE LIST. They may include
         Stage-Aprime caret_source_unavailable_or_stale (:1396), Stage-B
         member-library, and other allowable per-secondary drops the engine
         folded under this partial; those quarantine, they do not halt.
         partial_reasons does NOT gate acceptance (it is open-ended and
         descriptive); it is recorded verbatim downstream for disclosure.

    Everything else HALTS (no publish): non-empty failures, status='failed'/
    exit_code=1, halted_at set, partial/3 with zero exclusions, or any unknown
    status/exit combination. Unknown means halt.

    Survivors come from the engine's kept set when ``kept_secondaries`` is
    supplied (the written ranking rows), cross-checked against board-minus-
    quarantined; a mismatch is an unknown shape and HALTS fail-closed. Without
    a kept set the survivors fall back to board-minus-quarantined."""
    env = envelope if isinstance(envelope, dict) else {}
    status = env.get("status")
    exit_code = env.get("exit_code")
    halted_at = env.get("halted_at")
    failures = env.get("failures") or []
    exclusions = env.get("exclusions") or []
    partial_reasons = env.get("partial_reasons") or []

    # Quarantine list = the cumulative exclusions, deduped by secondary with ALL
    # {stage, kind, reason} causes preserved. No longer a halt authority.
    qmap: dict[str, list] = {}
    excluded: set[str] = set()
    for e in exclusions:
        if not isinstance(e, dict):
            continue
        sec = _norm(e.get("secondary"))
        if not sec:
            continue
        excluded.add(sec)
        qmap.setdefault(sec, []).append(
            {"stage": e.get("stage"), "kind": e.get("ticker_classification"),
             "reason": e.get("reason")})
    quarantined = [{"secondary": s, "causes": c} for s, c in sorted(qmap.items())]

    board_set = {_norm(s) for s in board}
    survivors = [s for s in board if _norm(s) not in excluded]

    clean_full_board = (
        status == "ok" and exit_code == 0 and not failures
        and not exclusions and halted_at is None)
    # Acceptance gates the BLOCKING INVARIANT, not the reason words. The
    # complete-vocabulary census (k6_recook source) proved partial_reasons is
    # descriptive and open-ended -- emitters at k6_recook.py:2612 (Stage-A
    # allowed), :2755-2757 (Stage-B member-library), :2857 (excluded-secondaries
    # finalization), :2859 (failures-present) -- and that every DANGEROUS outcome
    # routes to status='failed'/exit_code=1/halted_at set OR a non-empty failures
    # list, while every benign per-secondary drop yields partial/3/halted_at
    # None/failures empty through the exclusions (quarantine) list. So accept on
    # the triple + at least one exclusion; partial/3 with zero exclusions stays
    # unknown-shape and halts. Do NOT reintroduce a partial_reasons whitelist --
    # a new engine stage can mint a new reason string, and three pilots halted on
    # exactly that. partial_reasons is still recorded verbatim downstream for
    # disclosure; it just no longer gates.
    allowable_partial = (
        status == "partial" and exit_code == 3 and not failures
        and halted_at is None and bool(exclusions))
    ok = clean_full_board or allowable_partial

    halt_reason = None
    if not ok:
        halt_reason = (
            "recook not publishable: status=%r exit_code=%r halted_at=%r "
            "blocking_failures=%d exclusions=%d partial_reasons=%r"
            % (status, exit_code, halted_at, len(failures), len(exclusions),
               list(partial_reasons)))
    elif kept_secondaries is not None:
        # Cross-check the engine's authoritative kept set against board-minus-
        # quarantined. A mismatch is an unknown shape -> halt fail-closed.
        kept_set = {_norm(k) for k in kept_secondaries}
        expected_set = board_set - excluded
        if kept_set != expected_set:
            ok = False
            halt_reason = (
                "engine kept set != board minus quarantined (unknown shape): "
                "kept=%d expected=%d only_in_kept=%r only_in_expected=%r"
                % (len(kept_set), len(expected_set),
                   sorted(kept_set - expected_set)[:8],
                   sorted(expected_set - kept_set)[:8]))
        else:
            survivors = [s for s in board if _norm(s) in kept_set]

    return {
        "ok": ok,
        "survivors": survivors,
        "quarantined": quarantined,
        "halt_reason": halt_reason,
        "status": status,
        "exit_code": exit_code,
        # Recorded verbatim for disclosure (the engine's run-level partial
        # reasons); no longer an acceptance gate. The driver writes this into
        # the status pointer so an operator sees WHY the run was partial even
        # though the reason words do not control publishability.
        "partial_reasons": list(partial_reasons),
        "recook_reported_total_seconds": (
            (env.get("timings") or {}).get("total_seconds")),
        # k6_recook's batch envelope exposes no clean per-secondary recook
        # wall-clock; record that fact rather than inventing one.
        "per_secondary_recook_timing_available": False,
    }


# --- prior-board inputs (bound to the CURRENT live board) -------------------


def resolve_prior_inputs(repo_root: Path, *, fixture_path: Path,
                         promotion_manifest_path: Path,
                         sidecar_override: Optional[Path] = None,
                         ccc_override: Optional[Path] = None) -> dict:
    """Resolve the four prior-board inputs bound to the CURRENT live board. The
    prior validation sidecar and prior CCC verification manifest are derived
    from the live board's OWN committed metadata -- the fixture's
    validation_metadata.source_sidecar_path and the promotion manifest's
    ccc_series_storage.verification_manifest_path -- so they are ALWAYS the
    currently-published board's artifacts, never tonight's run-dir output.
    Explicit CLI overrides win when provided."""
    repo = Path(repo_root)
    sidecar = sidecar_override
    ccc = ccc_override
    if sidecar is None:
        fx = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        sp = (fx.get("validation_metadata") or {}).get("source_sidecar_path")
        if not sp:
            raise RerankError(
                "cannot derive prior validation sidecar: live fixture "
                "validation_metadata.source_sidecar_path is missing")
        sidecar = repo / sp
    if ccc is None:
        pm = json.loads(Path(promotion_manifest_path).read_text(encoding="utf-8"))
        cp = (pm.get("ccc_series_storage") or {}).get("verification_manifest_path")
        if not cp:
            raise RerankError(
                "cannot derive prior CCC verification manifest: promotion "
                "manifest ccc_series_storage.verification_manifest_path is missing")
        ccc = repo / cp
    return {
        "fixture": Path(fixture_path),
        "promotion_manifest": Path(promotion_manifest_path),
        "validation_sidecar": Path(sidecar),
        "ccc_verification_manifest": Path(ccc),
    }


# --- default engine seams (operator runs only; never exercised by tests) ----


def _default_recook_runner(argv: Sequence[str], *, cwd: str) -> dict:
    """Run k6_recook and parse its stdout JSON envelope. Argv array, no shell.
    Never run in tests (which inject a fake returning a crafted envelope)."""
    proc = subprocess.run(list(argv), cwd=str(cwd),  # noqa: S603 - argv, no shell
                          capture_output=True, text=True)
    try:
        env = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        env = {"status": "failed",
               "exit_code": proc.returncode if proc.returncode else 1,
               "stdout_unparseable": True}
    if isinstance(env, dict):
        env.setdefault("exit_code", proc.returncode)
    return env


def _resolve_git_toplevel(project_dir: Path) -> Path:
    """Resolve the git repository toplevel for the publication seam's repo_root.

    The Stage 9 publication allowlist compares against ``git status --porcelain``
    output, which git ALWAYS renders relative to the repository toplevel. The
    re-rank ``project_root`` is the ``project/`` SUBDIR, so the publish seam's
    repo_root MUST be the toplevel; otherwise every legitimate publication path
    is rejected as out-of-allowlist (the run-20260612T101155Z defect, where
    ``frontend/...`` allowlist entries never matched ``project/frontend/...``
    porcelain paths).

    STRICT, fail-fast: on ANY failure -- git missing, not a repo, nonzero exit,
    or empty output -- this RAISES RerankError with NO fallback. This is a
    DELIBERATE divergence from crunch_rebuild_orchestrator._git_toplevel, which
    falls back to ``project_dir.parent``: a git-less run cannot commit or push at
    Stage 9 anyway, so guessing the toplevel would only defer the inevitable
    refusal by a full recook + validation (~55 min) and silently guess the
    publication paths -- the exact bug class this fix removes.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True)
    except (OSError, ValueError) as exc:  # git binary absent / bad invocation
        raise RerankError(
            "git toplevel resolution could not launch git ("
            + type(exc).__name__ + ")") from exc
    if proc.returncode != 0:
        raise RerankError(
            "git rev-parse --show-toplevel exited %d (not a git repository?)"
            % proc.returncode)
    top = (proc.stdout or "").strip()
    if not top:
        raise RerankError(
            "git rev-parse --show-toplevel returned empty output")
    return Path(top).resolve()


def make_publish_seams(*, repo_root: Path, project_root: Path, run_dir: Path,
                       stackbuilder_root: Path,
                       excluded_tickers: Iterable[str] = ()) -> RerankPublishSeams:
    """Build the RerankPublishSeams with default engine seams from the same
    PUBLIC functions the orchestrator's defaults use (lazy imports keep this
    module side-effect-free). Tests inject their own seams/publish_func, so
    these defaults run only in a real operator pilot."""

    def validator(secs, run_id):
        from utils.k6_mtf_validation.adapter import (  # noqa: PLC0415
            build_adapter_inputs, K6MtfValidationAdapter, run_validation)
        secs = list(secs)
        inputs = build_adapter_inputs(
            secs, stackbuilder_root=Path(stackbuilder_root).as_posix())
        adapter = K6MtfValidationAdapter(secondaries=secs,
                                         secondary_inputs=inputs)
        result = run_validation(
            adapter, run_id=run_id,
            output_dir=Path(run_dir) / "publish_candidate" / "validation")
        if not isinstance(result, dict) or not isinstance(
                result.get("contract"), dict):
            raise RerankError("validation adapter returned no contract")
        return result["contract"]

    def joiner(ranking_path, sidecar_path, sidecar_sha):
        from utils.react_publish.k6_mtf_validation_join import (  # noqa: PLC0415
            load_and_build_k6_mtf_ranking_v2)
        return load_and_build_k6_mtf_ranking_v2(
            ranking_path, sidecar_path,
            expected_validation_sidecar_sha256=sidecar_sha)

    def stage9_runner(inputs):
        from stage9_publish import run_stage9_publish  # noqa: PLC0415
        return run_stage9_publish(inputs)

    return RerankPublishSeams(
        validator=validator, joiner=joiner, stage9_runner=stage9_runner,
        project_root=Path(project_root), repo_root=Path(repo_root),
        excluded_tickers=tuple(excluded_tickers))


def _default_lock_acquire(lock_path: Path, run_id: str, now: datetime) -> None:
    _cro.acquire_lock(Path(lock_path), run_id=run_id, stage="rerank",
                      reclaim_stale=False, now=now)


def _default_lock_release(lock_path: Path) -> None:
    _cro.release_lock(Path(lock_path))


# --- status pointer + token guidance ----------------------------------------


def write_status(status_path: Path, payload: dict) -> None:
    p = Path(status_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")


def _print_token_guidance(err: TextIO) -> None:
    for line in [
        "[FAIL] " + TOKEN_ENV + " is not set (or is blank) in this environment.",
        "The re-rank publish cannot start without it. Set it ONCE in your",
        "Windows user environment, then open a NEW terminal:",
        "",
        '    setx ' + TOKEN_ENV + ' "<your-token-value>"',
        "",
        "setx does NOT update already-open shells -- open a NEW terminal",
        "(or restart the scheduled task) afterward.",
    ]:
        print(line, file=err)


DRY_RUN_DISCLOSURE = (
    "[DISCLOSURE] --publish-dry-run is publication-closed but NOT "
    "network/Blob-closed: real fresh CCC Blob upload + GET occur through "
    "Stage 9, then it stops at the promote dry-run gate. No public fixture "
    "write, no commit, no push, no deploy.")


# --- argument parsing -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rerank_driver.py",
        description="Autonomous nightly whole-board re-rank (zero questions; "
                    "the live board is the selection).")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--publish", action="store_true",
                      help="real publish (requires --operator-approved-publish)")
    mode.add_argument("--publish-dry-run", action="store_true",
                      help="dry run: uploads fresh CCC to Blob (network), then "
                           "stops at the promote dry-run gate; no fixture "
                           "write/commit/push/deploy")
    p.add_argument("--operator-approved-publish", action="store_true",
                   help="required companion for --publish")
    # Execute-gate discipline mirrored from the orchestrator: required, no
    # default guessing for the duration budget; a labelled run for audit.
    p.add_argument("--duration-budget-minutes", type=int, required=True,
                   help="required run duration budget (audit; no default)")
    p.add_argument("--operator-budget-label", default=DEFAULT_OPERATOR_BUDGET_LABEL,
                   help="operator budget label (default %(default)s)")
    p.add_argument("--target-as-of", default=None,
                   help="override the derived latest-close target (YYYY-MM-DD); "
                        "default is the derived latest completed US close")
    p.add_argument("--max-quarantine-fraction", type=float, default=0.25,
                   help="halt for operator review if the quarantined fraction of "
                        "the board exceeds this ceiling, range (0,1]; "
                        "default %(default)s. A mass quarantine is a data/target "
                        "problem to review, never an auto-publish.")
    p.add_argument("--fixture", default=None,
                   help="override the live fixture path (repo-relative or abs)")
    p.add_argument("--prior-promotion-manifest", default=None)
    p.add_argument("--prior-validation-sidecar", default=None)
    p.add_argument("--prior-ccc-verification-manifest", default=None)
    return p


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.publish and not args.operator_approved_publish:
        parser.error("--publish requires --operator-approved-publish")
    if not (0.0 < args.max_quarantine_fraction <= 1.0):
        parser.error("--max-quarantine-fraction must be in the range (0, 1]")
    if args.target_as_of is not None:
        try:
            datetime.strptime(args.target_as_of, "%Y-%m-%d")
        except ValueError:
            parser.error("--target-as-of must be a valid YYYY-MM-DD date")
    return args


# --- entry point ------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None, *,
         env: Optional[dict] = None,
         repo_root: Optional[Path] = None,
         clock: Optional[Callable[[], datetime]] = None,
         holidays: Optional[Iterable[date]] = None,
         stdout: Optional[TextIO] = None,
         stderr: Optional[TextIO] = None,
         run_id: Optional[str] = None,
         recook_runner: Optional[Callable[..., dict]] = None,
         lock_acquire: Optional[Callable[..., None]] = None,
         lock_release: Optional[Callable[..., None]] = None,
         publish_seams: Optional[RerankPublishSeams] = None,
         publish_func: Optional[Callable[..., dict]] = None,
         toplevel_resolver: Optional[Callable[[Path], Path]] = None) -> int:
    """Run one re-rank. Returns a process exit code. There is NO input() call;
    the board is the selection."""
    env = os.environ if env is None else env
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    repo = (Path(repo_root) if repo_root is not None
            else Path(__file__).resolve().parent).resolve()
    clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
    holidays = _US_MARKET_HOLIDAYS_2026 if holidays is None else holidays
    recook_runner = recook_runner if recook_runner is not None else _default_recook_runner
    lock_acquire = lock_acquire if lock_acquire is not None else _default_lock_acquire
    lock_release = lock_release if lock_release is not None else _default_lock_release
    publish_func = publish_func if publish_func is not None else run_rerank_publish
    toplevel_resolver = (toplevel_resolver if toplevel_resolver is not None
                         else _resolve_git_toplevel)

    def emit(line: str = "") -> None:
        print(line, file=out)

    def emit_err(line: str = "") -> None:
        print(line, file=err)

    args = _parse_args(argv)  # SystemExit on bad flags / missing required gate
    started = clock()
    rid = run_id or started.strftime("%Y%m%dT%H%M%SZ")
    dry_run = bool(args.publish_dry_run)
    publish_mode = "publish-dry-run" if dry_run else "publish"
    status_path = repo / STATUS_REL

    base = {
        "run_id": rid,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publish_mode": publish_mode,
        "dry_run": dry_run,
        "operator_budget_label": args.operator_budget_label,
        "duration_budget_minutes": args.duration_budget_minutes,
        "status_gitignore_rule": STATUS_GITIGNORE_RULE,
    }

    def finalize(rc: int, **fields: Any) -> int:
        payload = dict(base)
        payload.update(fields)
        payload["exit_code"] = rc
        payload["ended_at"] = clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            write_status(status_path, payload)
        except OSError as exc:  # status write must never mask the outcome
            emit_err("[WARNING] could not write status pointer: "
                     + type(exc).__name__)
        return rc

    # 0. GIT TOPLEVEL for the publication seam -- fail-fast BEFORE any recook /
    # validation / publish. The Stage 9 allowlist matches `git status
    # --porcelain` paths, which git roots at the repository toplevel; ``repo``
    # is the project/ SUBDIR, so the seam's repo_root must be the toplevel or
    # every legitimate publication file is rejected as stray (run
    # 20260612T101155Z). Resolved ONCE here so a git-less environment refuses in
    # seconds, never after a ~55 min recook + validation. project_root stays the
    # project/ dir; only the seam's repo_root changes (see step 7).
    try:
        repo_toplevel = toplevel_resolver(repo)
    except RerankError as exc:
        emit_err("[FAIL] cannot resolve git toplevel for publication: "
                 + str(exc))
        return finalize(6, status="refused_no_git_toplevel",
                        halted_at="git_toplevel_preflight", note=str(exc))

    # 1. TOKEN PREFLIGHT (presence only) -- before any heavy work.
    token = str(env.get(TOKEN_ENV, "") or "")
    if not token.strip():
        _print_token_guidance(err)
        return finalize(2, status="refused_no_token", halted_at="token_preflight")

    # 2. LOCK -- respect the single exclusive .crunch.lock (never steals a live
    # peer's lock; that was confirmed in inspection).
    lock_path = repo / DEFAULT_CRUNCH_RUNS_ROOT / LOCK_NAME
    try:
        lock_acquire(lock_path, rid, started)
    except _cro.CrunchError as exc:
        emit_err("[FAIL] crunch lock busy (" + type(exc).__name__
                 + "); another run holds it. Skipping this trigger.")
        return finalize(3, status="lock_busy", halted_at="lock",
                        note="crunch lock held by a peer run")

    try:
        # 3. ENUMERATE THE BOARD (the selection).
        fixture_path = (Path(args.fixture) if args.fixture
                        else repo / DEFAULT_FIXTURE)
        if not fixture_path.is_absolute():
            fixture_path = repo / fixture_path
        board = enumerate_board(fixture_path)
        emit("Re-rank board secondaries: %d" % len(board))

        # 4. TARGET-AS-OF (latest completed US close, or explicit override).
        target = (args.target_as_of if args.target_as_of
                  else derive_target_as_of(now=started, holidays=holidays))
        target_source = "overridden" if args.target_as_of else "derived"
        emit("target-as-of: " + target + " (" + target_source + ")")
        if dry_run:
            emit(DRY_RUN_DISCLOSURE)

        run_dir = repo / DEFAULT_CRUNCH_RUNS_ROOT / rid
        run_dir.mkdir(parents=True, exist_ok=True)
        stackbuilder_root = repo / DEFAULT_STACKBUILDER_ROOT
        k6_output_root = repo / DEFAULT_K6_OUTPUT_ROOT
        k6_ranking_path = k6_output_root / rid / "k6_mtf_ranking.json"

        # 5. RECOOK (batch restage) + quarantine.
        recook_argv = build_recook_argv(
            secondaries=board, target_as_of=target,
            stackbuilder_root=stackbuilder_root, output_root=k6_output_root,
            driver_run_id=rid)
        emit("Recook (batch restage, NO OnePass/ImpactSearch/StackBuilder):")
        emit("  " + " ".join(recook_argv))
        t0 = clock()
        envelope = recook_runner(recook_argv, cwd=str(repo))
        recook_seconds = (clock() - t0).total_seconds()
        # Engine kept-set authority: prefer a kept list carried on the envelope
        # (tests inject it), else read the written ranking rows.
        kept = (envelope.get("kept_secondaries")
                if isinstance(envelope, dict) else None)
        if kept is None:
            kept = read_ranking_kept_secondaries(k6_ranking_path)
        outcome = parse_recook_outcome(envelope, board, kept_secondaries=kept)

        if not outcome["ok"]:
            emit_err("[FAIL] recook halted (systemic/blocking/unknown-shape): "
                     + str(outcome["halt_reason"]))
            return finalize(
                4, status="halted_recook", halted_at="recook",
                target_as_of=target, target_source=target_source,
                fresh_secondaries_count=0,
                quarantined=outcome["quarantined"],
                partial_reasons=outcome["partial_reasons"],
                recook_seconds=recook_seconds,
                recook_reported_total_seconds=outcome["recook_reported_total_seconds"],
                per_secondary_recook_timing_available=outcome[
                    "per_secondary_recook_timing_available"])

        survivors = outcome["survivors"]
        quarantined = outcome["quarantined"]

        # F2: QUARANTINE-FRACTION GUARD. A mass quarantine is a data/target
        # problem for operator review -- never an auto-publish -- even when the
        # engine's partial is fully allowable.
        qfraction = (len(quarantined) / len(board)) if board else 0.0
        if qfraction > args.max_quarantine_fraction:
            emit_err("[FAIL] quarantine fraction %.4f exceeds ceiling %.4f; "
                     "halting for operator review (data/target problem, not an "
                     "auto-publish)."
                     % (qfraction, args.max_quarantine_fraction))
            return finalize(
                7, status="halted_quarantine_guard",
                halted_at="quarantine_guard", target_as_of=target,
                target_source=target_source,
                fresh_secondaries_count=len(survivors),
                quarantine_fraction=qfraction,
                max_quarantine_fraction=args.max_quarantine_fraction,
                quarantined=quarantined,
                partial_reasons=outcome["partial_reasons"],
                recook_seconds=recook_seconds,
                recook_reported_total_seconds=outcome[
                    "recook_reported_total_seconds"])

        if not survivors:
            emit_err("[FAIL] no surviving secondaries after quarantine; refusing")
            return finalize(
                4, status="halted_no_survivors", halted_at="recook",
                target_as_of=target, target_source=target_source,
                fresh_secondaries_count=0,
                quarantined=quarantined,
                partial_reasons=outcome["partial_reasons"],
                recook_seconds=recook_seconds)
        emit("survivors: %d | quarantined: %d" % (len(survivors), len(quarantined)))

        # 6. PRIOR INPUTS bound to the CURRENT live board (never tonight's run).
        promo_path = (Path(args.prior_promotion_manifest)
                      if args.prior_promotion_manifest
                      else repo / DEFAULT_PROMOTION_MANIFEST)
        if not promo_path.is_absolute():
            promo_path = repo / promo_path
        priors = resolve_prior_inputs(
            repo, fixture_path=fixture_path, promotion_manifest_path=promo_path,
            sidecar_override=(Path(args.prior_validation_sidecar)
                              if args.prior_validation_sidecar else None),
            ccc_override=(Path(args.prior_ccc_verification_manifest)
                          if args.prior_ccc_verification_manifest else None))

        # 7. PUBLISH via the run_rerank_publish seam (full validation inside).
        # repo_root is the GIT TOPLEVEL (so the Stage 9 allowlist matches the
        # toplevel-rooted porcelain paths); project_root stays the project/ dir
        # (it locates frontend/public/fixtures, md_library/shared, and the
        # fresh-row path normalization).
        seams = publish_seams if publish_seams is not None else make_publish_seams(
            repo_root=repo_toplevel, project_root=repo, run_dir=run_dir,
            stackbuilder_root=stackbuilder_root)
        t1 = clock()
        result = publish_func(
            survivors=survivors,
            k6_ranking_path=k6_ranking_path,
            prior_fixture_path=priors["fixture"],
            prior_promotion_manifest_path=priors["promotion_manifest"],
            prior_validation_sidecar_path=priors["validation_sidecar"],
            prior_ccc_verification_manifest_path=priors["ccc_verification_manifest"],
            run_dir=run_dir,
            run_id=rid,
            target_as_of=target,
            dry_run=dry_run,
            operator_approved=bool(args.operator_approved_publish),
            seams=seams)
        publish_seconds = (clock() - t1).total_seconds()
        pstatus = result.get("status") if isinstance(result, dict) else None
        total_seconds = (clock() - started).total_seconds()
        emit("Stage 9 status: " + str(pstatus))

        ok_status = pstatus in ("published", "dry_run_complete")
        return finalize(
            0 if ok_status else 5,
            status=str(pstatus),
            halted_at=(None if ok_status
                       else (result.get("stage") if isinstance(result, dict)
                             else "publish")),
            target_as_of=target,
            target_source=target_source,
            fresh_secondaries_count=len(survivors),
            fresh_secondaries=survivors,
            quarantined=quarantined,
            partial_reasons=outcome["partial_reasons"],
            refusal_path=(str(run_dir / "publish_refusal.json")
                          if not ok_status else None),
            artifacts={
                "run_dir": str(run_dir),
                "k6_ranking_path": str(k6_ranking_path),
                "validation_sidecar": str(run_dir / "05_validation_sidecar.json"),
                "stage9_summary": str(run_dir / "09_stage9_publish.json"),
                "prior_validation_sidecar": str(priors["validation_sidecar"]),
                "prior_ccc_verification_manifest": str(
                    priors["ccc_verification_manifest"]),
            },
            timing={
                "recook_seconds": recook_seconds,
                "publish_seconds": publish_seconds,
                "total_seconds": total_seconds,
                "recook_reported_total_seconds": outcome[
                    "recook_reported_total_seconds"],
                "per_secondary_recook_timing_available": outcome[
                    "per_secondary_recook_timing_available"],
            })
    except RerankError as exc:
        emit_err("[FAIL] " + type(exc).__name__ + ": " + str(exc))
        return finalize(6, status="error", halted_at="driver",
                        reason=type(exc).__name__)
    except _cro.CrunchError as exc:
        # run_rerank_publish wraps publish-chain failures as CrunchError.
        emit_err("[FAIL] publish refused: " + type(exc).__name__)
        return finalize(5, status="refused", halted_at="publish",
                        reason=type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - fail-closed, status always written
        emit_err("[FAIL] unexpected " + type(exc).__name__)
        return finalize(6, status="error", halted_at="driver",
                        reason=type(exc).__name__)
    finally:
        try:
            lock_release(lock_path)
        except Exception:  # noqa: BLE001 - lock release best-effort
            pass


if __name__ == "__main__":  # pragma: no cover - thin CLI guard
    sys.exit(main())
