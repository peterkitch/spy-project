"""SPRINT 500 K=6 recook driver (additive sprint orchestrator).

Recooks already-built, frozen K=6 StackBuilder builds under
``output/stackbuilder`` to a target close date and, only when explicitly
executed, produces refreshed caches, refreshed price-cache closes,
refreshed member multi-timeframe stable libraries, a candidate
``output/k6_mtf/<RUN_ID>/k6_mtf_ranking.json`` artifact, and a
private/internal DRY-RUN promote summary.

This module is ADDITIVE. It imports existing engine/helper functions as a
library (lazily, after the global refusal checks) and never modifies the
engines, scoring logic, validation logic, React, or public deploy
behavior. StackBuilder / ImpactSearch / OnePass engines are never invoked;
the frozen builds are upstream preconditions.

Contract highlights (see project/CLAUDE.md and the K=6 MTF launch-path
contract):

  - Dry-run default. No writes or network unless ``--execute`` (and, for
    the network long pole, ``--allow-network-fetch``).
  - Exactly one JSON object (schema ``k6_recook_summary_v1``) is written to
    stdout at process end. Everything else (logs, progress, tracebacks,
    child output) goes to stderr.
  - Fail closed. Stage barriers are mandatory: Stage A fully completes
    before Aprime; Aprime before B; B before E.
  - ProcessPool workers are module-level picklable functions (Windows
    spawn cannot pickle closures/lambdas).

The chain is Stage 0 -> A -> Aprime -> B -> E -> F -> H(dry-run).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "k6_recook_summary_v1"
DEFAULT_TARGET_AS_OF = "2026-06-03"

# Canonical stage names in dependency order.
STAGE_ORDER: Tuple[str, ...] = ("stage0", "A", "Aprime", "B", "E", "F", "H")

# Non-daily timeframes always (re)built in Stage B; 1d is created only when
# missing and is never overwritten.
NON_DAILY_TIMEFRAMES: Tuple[str, ...] = ("1wk", "1mo", "3mo", "1y")
DAILY_TIMEFRAME = "1d"

# multi_timeframe_builder source mode for the contract-compliant offline
# resample path (reads local cache PKL Close; no vendor fetch).
LAUNCH_PATH_SOURCE_MODE = "launch_path_local_pkl_resampled"

# Stage Aprime price-cache on-disk format. CSV is the default because the
# repo's price_cache/daily convention is CSV and the pinned spyproject2 env
# ships no parquet engine; parquet is an explicit opt-in.
PRICE_CACHE_FORMATS: Tuple[str, ...] = ("csv", "parquet")
DEFAULT_PRICE_CACHE_FORMAT = "csv"

# Stable-library engine version embedded in filenames
# (<TICKER>_stable_v1_0_0[_<interval>].pkl). Mirrors
# signal_library.multi_timeframe_builder.ENGINE_VERSION; verified at build
# time, not hard-coded into any write path.
STABLE_ENGINE_VERSION_TAG = "1_0_0"

# Refresher issue codes that mean "no usable history" (dead ticker) rather
# than a transient/structural failure. Values mirror
# signal_engine_cache_refresher ISSUE_* string constants.
#
# NOTE: ``data_fetch_failed`` is intentionally EXCLUDED. The refresher emits
# it for any provider/fetcher exception (e.g. a yfinance outage or
# rate-limit), so treating it as dead/no-history would silently convert a
# transient provider problem into dependent-secondary exclusions and a
# partial candidate. It is therefore a Stage A refresh FAILURE that halts
# Stage A. ``data_empty`` (provider returned an empty series) and
# ``data_no_close_column`` (provider returned data with no Close) reflect
# an unusable history for that symbol and remain dead/no-history; no
# structured terminal/delisted signal exists, so nothing further is
# inferred.
DEAD_NO_HISTORY_ISSUE_CODES = frozenset(
    {"data_empty", "data_no_close_column"}
)

# Stage A issue codes that are network / provider / systemic and must ALWAYS
# halt Stage A, even under --allow-stage-a-exclusions. These are never
# convertible into an allowable dependent-secondary exclusion. ``optimizer_failed``
# is intentionally NOT here: it is allowable ONLY when paired with
# ``insufficient_history`` (see _classify_stage_a_outcome); optimizer_failed in
# any other combination falls through to the conservative blocking branch.
STAGE_A_BLOCKING_ISSUE_CODES = frozenset(
    {
        "data_fetch_failed",
        "invalid_ticker",
        "invalid_max_sma_day",
        "provenance_manifest_failed",
        "data_only_write_blocked",
        "worker_exception",
    }
)

# Allowable Stage A unavailability kinds (only excludable under the explicit
# --allow-stage-a-exclusions flag).
STAGE_A_ALLOWABLE_KINDS = frozenset(
    {"dead_no_history", "not_current", "insufficient_history"}
)

LOCK_FILENAME = ".recook.lock"
DEFAULT_LOCK_TTL_SECONDS = 6 * 3600

PROGRESS_DIR_NAME = "_progress"
COMBO_FILENAME = "combo_k=6.json"
SELECTED_BUILD_FILENAME = "selected_build.json"
SELECTED_BUILD_PINNED_FILENAME = "selected_build.pinned.json"


# ---------------------------------------------------------------------------
# Logging (stderr only) and stdout discipline
# ---------------------------------------------------------------------------


def log(message: str) -> None:
    """Emit one diagnostic line to stderr (never stdout)."""
    sys.stderr.write(str(message) + "\n")
    sys.stderr.flush()


# Absolute local-path token matcher (Windows drive-letter paths and POSIX
# /Users|/home|/mnt paths). Used to redact any local filesystem detail out of
# exception text before it enters the JSON envelope or logs.
_ABS_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:Users|home|mnt)/)[^\s'\"]*")


def _redact_local_paths(text: Any) -> Any:
    """Replace any absolute local-path token in ``text`` with
    ``<redacted-path>``. Relative project paths (cache/results,
    signal_library/data/stable, ...) are left intact."""
    if text is None:
        return None
    return _ABS_PATH_RE.sub("<redacted-path>", str(text))


@contextmanager
def stdout_to_stderr():
    """Redirect any stdout produced by imported helpers to stderr so the
    only thing on stdout is the single final JSON envelope."""
    old = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = old


# ---------------------------------------------------------------------------
# Small parsing utilities
# ---------------------------------------------------------------------------


def parse_target_date(value: str) -> str:
    """Validate a YYYY-MM-DD target date and return it normalized.

    Raises ValueError on malformed input.
    """
    text = str(value or "").strip()
    parts = text.split("-")
    if len(parts) != 3:
        raise ValueError(f"target-as-of must be YYYY-MM-DD: {value!r}")
    y, m, d = parts
    if not (len(y) == 4 and len(m) == 2 and len(d) == 2):
        raise ValueError(f"target-as-of must be YYYY-MM-DD: {value!r}")
    iy, im, idd = int(y), int(m), int(d)
    if not (1 <= im <= 12 and 1 <= idd <= 31):
        raise ValueError(f"target-as-of out of range: {value!r}")
    return f"{iy:04d}-{im:02d}-{idd:02d}"


def parse_lock_ttl(value: str) -> int:
    """Parse a lock TTL. Accepts ``6h``, ``360m``, ``21600s`` or a raw
    integer number of seconds. Returns seconds."""
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_LOCK_TTL_SECONDS
    try:
        if text.endswith("h"):
            return int(float(text[:-1]) * 3600)
        if text.endswith("m"):
            return int(float(text[:-1]) * 60)
        if text.endswith("s"):
            return int(float(text[:-1]))
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"invalid --lock-ttl: {value!r}") from exc


def parse_stages(value: Optional[str]) -> List[str]:
    """Parse the optional --stages list. Returns the canonical-ordered
    subset. Raises ValueError on unknown names or out-of-dependency-order
    input."""
    if not value:
        return list(STAGE_ORDER)
    tokens = [t.strip() for t in value.split(",") if t.strip()]
    unknown = [t for t in tokens if t not in STAGE_ORDER]
    if unknown:
        raise ValueError(f"unknown stage name(s): {unknown!r}")
    indices = [STAGE_ORDER.index(t) for t in tokens]
    if indices != sorted(indices):
        raise ValueError(
            f"stages out of dependency order: {tokens!r} "
            f"(canonical order is {list(STAGE_ORDER)!r})"
        )
    # Preserve canonical order, dedupe.
    seen: set[str] = set()
    ordered: List[str] = []
    for name in STAGE_ORDER:
        if name in tokens and name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered


def is_contiguous_prefix(stages: Sequence[str]) -> bool:
    """True iff ``stages`` is a non-empty contiguous prefix of the canonical
    chain starting at ``stage0`` (e.g. [stage0], [stage0, A], ...,
    [stage0..H]). Used to forbid execute subsets that skip a freshness
    prerequisite (e.g. E,F,H or stage0,A,B)."""
    stages = list(stages)
    if not stages:
        return False
    return stages == list(STAGE_ORDER[: len(stages)])


def utc_run_id() -> str:
    """Default driver run id: ``<UTC>_recook``."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "_recook"


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_member_token(token: Any) -> Tuple[str, str, str]:
    """Parse a combo member token into (ticker, protocol, raw_token).

    Semantics mirror k6_mtf_history_producer._parse_member_token exactly:
    the token must contain ``[D]`` or ``[I]``; bare ``AWR-D`` / ``CP-I``
    directory-name forms are invalid in the combo JSON and fail closed.
    """
    if not isinstance(token, str):
        raise ValueError(f"non-string K=6 member: {token!r}")
    if "[D]" in token:
        return token.split("[D]", 1)[0], "D", token
    if "[I]" in token:
        return token.split("[I]", 1)[0], "I", token
    raise ValueError(f"K=6 member missing [D]/[I] protocol marker: {token!r}")


def to_posix(path_str: str) -> str:
    return str(path_str).replace("\\", "/")


# ---------------------------------------------------------------------------
# Stage 0 helpers: discovery, seed selection, combo parsing
# ---------------------------------------------------------------------------


def discover_secondary_dirs(stackbuilder_root: Path) -> List[Path]:
    """Return candidate secondary directories under the stackbuilder root,
    excluding the ``_progress`` bookkeeping dir and dot-dirs."""
    if not stackbuilder_root.exists() or not stackbuilder_root.is_dir():
        return []
    out: List[Path] = []
    for child in sorted(stackbuilder_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name == PROGRESS_DIR_NAME or child.name.startswith("."):
            continue
        out.append(child)
    return out


def seed_dirs_with_combo(secondary_dir: Path) -> List[Path]:
    """Return immediate child directories of ``secondary_dir`` that contain
    a ``combo_k=6.json`` file, sorted by name for determinism."""
    out: List[Path] = []
    if not secondary_dir.is_dir():
        return out
    for child in sorted(secondary_dir.iterdir()):
        if child.is_dir() and (child / COMBO_FILENAME).is_file():
            out.append(child)
    return out


def choose_seed_dir(seed_dirs: Sequence[Path]) -> Tuple[Optional[Path], str]:
    """Choose the seed dir for pointer synthesis.

    Returns (chosen_dir_or_None, status):
      - exactly one        -> (the dir, "ok")
      - unique newest mtime -> (newest, "ok")
      - tied newest mtime   -> (None, "ambiguous_seed_mtime_tie")
      - zero                -> (None, "zero_combo_dir")
    """
    if not seed_dirs:
        return None, "zero_combo_dir"
    if len(seed_dirs) == 1:
        return seed_dirs[0], "ok"
    mtimes = [(d, d.stat().st_mtime) for d in seed_dirs]
    max_mtime = max(m for _, m in mtimes)
    newest = [d for d, m in mtimes if m == max_mtime]
    if len(newest) != 1:
        return None, "ambiguous_seed_mtime_tie"
    return newest[0], "ok"


def parse_combo_members(
    combo_path: Path,
) -> Tuple[Optional[List[Tuple[str, str, str]]], Optional[str]]:
    """Parse the six members of a combo_k=6.json using producer semantics.

    Returns (members_or_None, error_code). ``members`` is a list of exactly
    six (ticker, protocol, raw_token) tuples. Any structural problem yields
    ``(None, <error_code>)`` and the caller fails the secondary closed.
    """
    try:
        raw = json.loads(combo_path.read_text(encoding="utf-8"))
    except Exception:
        return None, "combo_unreadable"
    if not isinstance(raw, dict):
        return None, "combo_not_object"
    members_raw = (
        raw.get("Members") or raw.get("members") or raw.get("member_list")
    )
    if not isinstance(members_raw, list):
        return None, "members_not_list"
    if len(members_raw) != 6:
        return None, "members_not_six"
    parsed: List[Tuple[str, str, str]] = []
    for token in members_raw:
        try:
            parsed.append(parse_member_token(token))
        except ValueError:
            return None, "member_token_invalid"
    return parsed, None


def _selection_args():
    """Build the lightweight args object expected by
    stackbuilder_workbook_runner.build_selected_build_payload. Only
    ``optimize_by`` and ``pin_build`` are read (via getattr)."""
    import types

    # Lazy import so the constant resolves to the real runner default.
    from stackbuilder_workbook_runner import DEFAULT_OPTIMIZE_BY

    return types.SimpleNamespace(optimize_by=DEFAULT_OPTIMIZE_BY, pin_build=None)


def build_pointer_payload(secondary: str, run_dir_posix: str) -> dict:
    """Build a selected_build.json payload by reusing the existing
    StackBuilder selection payload builder (no hand-rolled schema)."""
    from stackbuilder_workbook_runner import build_selected_build_payload

    run_summary = {
        "run_dir": run_dir_posix,
        "summary": {},
        "manifest": {},
        "created_at": None,
    }
    return build_selected_build_payload(secondary, run_summary, _selection_args())


def write_pointer_atomic(target: Path, payload: dict) -> Path:
    """Write selected_build.json atomically using the existing helper.

    Note: we deliberately target ``target`` directly (the RAW secondary
    directory) instead of routing through default_selection_updater, which
    sanitizes ``.`` to ``_`` (:1185) and would mis-target dotted tickers
    such as DX-Y.NYB.
    """
    from stackbuilder_workbook_runner import _atomic_write_json

    return _atomic_write_json(target, payload)


# ---------------------------------------------------------------------------
# Stage 0 + union resolution (planning; writes only under execute)
# ---------------------------------------------------------------------------


@dataclass
class SecondaryPlan:
    secondary: str
    secondary_dir: str
    status: str  # existing | synthesize | blocked_by_pin | excluded
    reason: Optional[str] = None
    chosen_run_dir: Optional[str] = None
    combo_path: Optional[str] = None
    members: Optional[List[Tuple[str, str, str]]] = None
    pinned: bool = False
    would_write_pointer: bool = False


def _existing_pointer_run_dir(sec_dir: Path) -> Tuple[Optional[Path], Optional[str]]:
    """Read selected_run_dir from an existing selected_build.json.

    Returns (run_dir_path_or_None, error_or_None). The path is resolved
    relative to CWD when relative (mirrors the producer's
    ``Path(selected_run_dir)`` semantics)."""
    sel_path = sec_dir / SELECTED_BUILD_FILENAME
    try:
        sb = json.loads(sel_path.read_text(encoding="utf-8"))
    except Exception:
        return None, "selected_build_unreadable"
    run_dir = sb.get("selected_run_dir") if isinstance(sb, dict) else None
    if not run_dir or not isinstance(run_dir, str):
        return None, "selected_run_dir_missing"
    p = Path(run_dir)
    if not p.exists():
        return None, "selected_run_dir_absent"
    if not (p / COMBO_FILENAME).is_file():
        return None, "combo_missing_at_selected_run_dir"
    return p, None


def plan_secondary(
    sec_dir: Path,
    *,
    stackbuilder_root: str,
    restage_all: bool,
    unpin: bool,
) -> SecondaryPlan:
    """Resolve one secondary's Stage 0 plan and member list."""
    secondary = sec_dir.name
    base = SecondaryPlan(
        secondary=secondary,
        secondary_dir=to_posix(str(sec_dir)),
        status="excluded",
    )
    seed_dirs = seed_dirs_with_combo(sec_dir)
    if not seed_dirs:
        base.reason = "zero_combo_dir"
        return base

    has_existing = (sec_dir / SELECTED_BUILD_FILENAME).is_file()
    has_pin = (sec_dir / SELECTED_BUILD_PINNED_FILENAME).is_file()
    base.pinned = has_pin

    # Existing pointer, keep untouched unless --restage-all.
    if has_existing and not restage_all:
        run_dir, err = _existing_pointer_run_dir(sec_dir)
        if err is not None:
            base.reason = err
            return base
        members, merr = parse_combo_members(run_dir / COMBO_FILENAME)
        if merr is not None:
            base.reason = merr
            return base
        base.status = "existing"
        base.chosen_run_dir = to_posix(str(run_dir))
        base.combo_path = to_posix(str(run_dir / COMBO_FILENAME))
        base.members = members
        return base

    # Synthesis path (no pointer, or --restage-all).
    if has_pin and not unpin:
        # Pinned: blocked from auto (re)write. If a usable existing pointer
        # is present we can still build from it; otherwise excluded.
        if has_existing:
            run_dir, err = _existing_pointer_run_dir(sec_dir)
            if err is None:
                members, merr = parse_combo_members(run_dir / COMBO_FILENAME)
                if merr is None:
                    base.status = "blocked_by_pin"
                    base.reason = "existing_pin_blocks_auto_update"
                    base.chosen_run_dir = to_posix(str(run_dir))
                    base.combo_path = to_posix(str(run_dir / COMBO_FILENAME))
                    base.members = members
                    return base
        base.status = "blocked_by_pin"
        base.reason = "existing_pin_blocks_auto_update"
        return base

    chosen, status = choose_seed_dir(seed_dirs)
    if chosen is None:
        base.reason = status
        return base
    combo_path = chosen / COMBO_FILENAME
    members, merr = parse_combo_members(combo_path)
    if merr is not None:
        base.reason = merr
        return base
    run_dir_posix = to_posix(f"{stackbuilder_root}/{secondary}/{chosen.name}")
    base.status = "synthesize"
    base.chosen_run_dir = run_dir_posix
    base.combo_path = to_posix(str(combo_path))
    base.members = members
    base.would_write_pointer = True
    return base


def resolve_union(plans: Sequence[SecondaryPlan]) -> Dict[str, str]:
    """Build the case-deduped refresh union (members + rankable
    secondaries). Returns an ordered dict mapping uppercased key -> first
    seen display ticker."""
    union: Dict[str, str] = {}

    def add(ticker: str) -> None:
        key = ticker.strip().upper()
        if key and key not in union:
            union[key] = ticker.strip()

    for plan in plans:
        if plan.members is None:
            continue
        add(plan.secondary)
        for ticker, _protocol, _raw in plan.members:
            add(ticker)
    return union


# ---------------------------------------------------------------------------
# Stage A: cache/results refresh (offline skip gate + refresher)
# ---------------------------------------------------------------------------


def _is_fresh_state(state: Any) -> bool:
    return bool(
        getattr(state, "cache_ahead_of_cutoff", False)
        or getattr(state, "cache_equal_to_cutoff", False)
    )


# Bounds on the retry-error audit trail kept per ticker (sanitized).
_RETRY_ERROR_MAX = 5
_RETRY_ERROR_STR_MAX = 200


@dataclass
class RetryConfig:
    """Bounded retry/backoff settings for the Stage A fetch seam.

    ``retries`` is the number of ADDITIONAL attempts after the first, so the
    total number of fetch attempts is ``retries + 1``.
    """

    retries: int = 2
    base_seconds: float = 1.0
    max_seconds: float = 8.0

    @property
    def max_attempts(self) -> int:
        return int(self.retries) + 1

    def as_dict(self) -> dict:
        return {
            "retries": int(self.retries),
            "base_seconds": float(self.base_seconds),
            "max_seconds": float(self.max_seconds),
        }


def compute_backoff_delay(
    failed_attempt_index: int,
    *,
    base_seconds: float,
    max_seconds: float,
    jitter: float = 0.0,
) -> float:
    """Exponential backoff for the delay AFTER a failed attempt.

    ``failed_attempt_index`` is 0-based for the attempt that just failed
    (0 after the first failure). The exponential term ``base * 2**i`` is
    capped at ``max_seconds``; a small non-negative ``jitter`` is then added
    and the TOTAL is still capped at ``max_seconds`` so the returned delay
    never exceeds ``--fetch-backoff-max-seconds``.
    """
    expo = float(base_seconds) * (2 ** int(failed_attempt_index))
    capped = min(expo, float(max_seconds))
    delayed = capped + max(0.0, float(jitter))
    return min(delayed, float(max_seconds))


class RetryingFetcher:
    """Module-level, ProcessPool-spawn-safe retrying wrapper around a fetch
    callable with signature ``fetch(ticker) -> DataFrame``.

    Retries ONLY exceptions raised by the underlying fetcher. A successful
    call is returned untouched -- including one that returns an empty
    DataFrame or a DataFrame missing Close -- so the refresher keeps its
    existing ``data_empty`` / ``data_no_close_column`` classification. On
    retry exhaustion the final exception is re-raised so
    ``refresh_signal_engine_cache`` returns ``data_fetch_failed`` and the
    driver stays fail-closed. No terminal/delisted semantics are invented.

    The instance is created INSIDE the worker process (never pickled across
    the pool boundary) and accumulates audit metadata that the caller reads
    back after the refresh call returns.
    """

    def __init__(
        self,
        *,
        underlying: Callable[[str], Any],
        config: RetryConfig,
        sleep_fn: Callable[[float], Any] = time.sleep,
        jitter_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self._underlying = underlying
        self._config = config
        self._sleep_fn = sleep_fn
        self._jitter_fn = jitter_fn
        self.attempts = 0
        self.errors: List[str] = []
        self.exhausted = False
        self.delays: List[float] = []

    def __call__(self, ticker: str) -> Any:
        last_exc: Optional[BaseException] = None
        for i in range(self._config.max_attempts):
            self.attempts += 1
            try:
                return self._underlying(ticker)
            except Exception as exc:  # only fetch exceptions are retried
                last_exc = exc
                if len(self.errors) < _RETRY_ERROR_MAX:
                    self.errors.append(
                        f"{type(exc).__name__}: {exc}"[:_RETRY_ERROR_STR_MAX]
                    )
                if i < self._config.max_attempts - 1:
                    jitter = 0.0
                    if self._jitter_fn is not None:
                        jitter = float(self._jitter_fn())
                    delay = compute_backoff_delay(
                        i,
                        base_seconds=self._config.base_seconds,
                        max_seconds=self._config.max_seconds,
                        jitter=jitter,
                    )
                    self.delays.append(delay)
                    self._sleep_fn(delay)
        self.exhausted = True
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("retrying fetcher exhausted with no exception")

    @property
    def retries_done(self) -> int:
        return max(0, self.attempts - 1)

    def metadata(self) -> dict:
        return {
            "fetch_attempts": self.attempts,
            "fetch_retries": self.retries_done,
            "retry_exhausted": bool(self.exhausted),
            "retry_error_count": len(self.errors),
            "retry_errors": list(self.errors),
        }


def stage_a_process_ticker(
    ticker: str,
    *,
    cache_dir: Optional[str],
    status_dir: Optional[str],
    write: bool,
    target_as_of: str,
    evaluate_fn: Callable[..., Any],
    refresh_fn: Callable[..., Any],
    fetcher: Optional["RetryingFetcher"] = None,
    provider_name: Optional[str] = None,
) -> dict:
    """Process one ticker for Stage A. Offline freshness gate first; the
    refresher is called ONLY when the ticker is not already fresh.

    ``evaluate_fn`` is the offline cache-cutoff evaluator (no network).
    ``refresh_fn`` is the cache refresher. Both are injected so the unit
    tests can substitute fakes and assert the refresher is never called on
    a fresh ticker.

    When ``fetcher`` is supplied it is threaded into the refresher via
    ``data_fetcher=`` (with ``provider_name``) and its audit metadata is
    merged into the result. An already-fresh ticker never touches the
    fetcher (the offline skip gate is preserved).
    """
    result: dict = {"ticker": ticker, "classification": None, "issue_codes": []}
    state = evaluate_fn(
        ticker, cache_dir=cache_dir, current_as_of_date=target_as_of
    )
    result["cache_date_range_end"] = getattr(state, "cache_date_range_end", None)
    if _is_fresh_state(state):
        result["classification"] = "skipped_fresh"
        result["refresh_called"] = False
        if fetcher is not None:
            # Never invoked: metadata confirms zero fetch attempts.
            result.update(fetcher.metadata())
        return result

    result["refresh_called"] = True
    refresh_kwargs: Dict[str, Any] = dict(
        cache_dir=cache_dir,
        status_dir=status_dir,
        write=write,
        max_sma_day=None,
        current_as_of_date=target_as_of,
    )
    if fetcher is not None:
        refresh_kwargs["data_fetcher"] = fetcher
        if provider_name is not None:
            refresh_kwargs["provider_name"] = provider_name
    rr = refresh_fn(ticker, **refresh_kwargs)
    if fetcher is not None:
        # Present on every path, including the data_fetch_failed path where
        # the refresher caught the final re-raised exception.
        result.update(fetcher.metadata())
    issue_codes = list(getattr(rr, "issue_codes", ()) or ())
    result["issue_codes"] = issue_codes
    result["new_cache_date_range_end"] = getattr(
        rr, "new_cache_date_range_end", None
    )
    result["current_after"] = bool(getattr(rr, "current_after", False))
    result["refreshed"] = bool(getattr(rr, "refreshed", False))
    # Provenance paths for exclusion evidence (additive; None when absent).
    result["cache_path"] = getattr(rr, "cache_path", None)
    result["status_path"] = getattr(rr, "status_path", None)

    if any(code in DEAD_NO_HISTORY_ISSUE_CODES for code in issue_codes):
        result["classification"] = "dead_no_history"
        return result

    new_end = getattr(rr, "new_cache_date_range_end", None)
    current_after = bool(getattr(rr, "current_after", False))
    fresh_enough = bool(new_end) and str(new_end) >= str(target_as_of)
    if write:
        ok = (not issue_codes) and current_after and fresh_enough
    else:
        # write=False path still returns dry_run_only issue code; treat a
        # would-be-current result as success-equivalent for planning.
        ok = current_after and fresh_enough
    result["classification"] = "refreshed" if ok else "failed"
    return result


def _classify_stage_a_outcome(result: Any, *, target_as_of: str) -> dict:
    """Classify one Stage A worker result for the unavailable-data policy.

    Returns a dict with:
      - is_unavailable: bool  (True for any non-ok, non-skipped outcome)
      - allowable: bool       (True only for dead_no_history / not_current /
                               insufficient_history with no blocking signal)
      - kind: str             ("ok" | "skipped_fresh" | "dead_no_history" |
                               "not_current" | "insufficient_history" |
                               "blocking")
      - reason: str
      - issue_codes: list[str]

    Precedence: any network/provider/systemic signal (retry exhaustion, a
    blocking issue code, an unknown classification, or a missing/invalid
    payload) wins and is classified ``blocking`` -- so it halts Stage A even
    under --allow-stage-a-exclusions. data_fetch_failed and retry_exhausted
    are never allowable. No freshness tolerance and no terminal/delisted
    semantics are introduced.
    """
    if not isinstance(result, dict):
        return {
            "is_unavailable": True, "allowable": False, "kind": "blocking",
            "reason": "invalid_worker_payload", "issue_codes": [],
        }
    cls = result.get("classification")
    codes = list(result.get("issue_codes") or [])
    code_set = set(codes)

    if cls in ("refreshed", "skipped_fresh"):
        return {
            "is_unavailable": False, "allowable": False, "kind": cls,
            "reason": "", "issue_codes": codes,
        }

    # --- Blocking precedence (always halt) ---
    if cls not in ("dead_no_history", "failed"):
        return {
            "is_unavailable": True, "allowable": False, "kind": "blocking",
            "reason": f"unknown_classification:{cls!r}", "issue_codes": codes,
        }
    if bool(result.get("retry_exhausted")):
        return {
            "is_unavailable": True, "allowable": False, "kind": "blocking",
            "reason": "retry_exhausted", "issue_codes": codes,
        }
    if "worker_exception" in code_set or result.get("error"):
        return {
            "is_unavailable": True, "allowable": False, "kind": "blocking",
            "reason": "systemic_worker_error", "issue_codes": codes,
        }
    blocking_hit = code_set & STAGE_A_BLOCKING_ISSUE_CODES
    if blocking_hit:
        return {
            "is_unavailable": True, "allowable": False, "kind": "blocking",
            "reason": f"blocking_issue_codes:{sorted(blocking_hit)}",
            "issue_codes": codes,
        }

    # --- Allowable kinds (no blocking signal present) ---
    if cls == "dead_no_history":
        return {
            "is_unavailable": True, "allowable": True, "kind": "dead_no_history",
            "reason": "dead_no_history", "issue_codes": codes,
        }
    # cls == "failed" with no blocking codes:
    if "optimizer_failed" in code_set and "insufficient_history" in code_set:
        return {
            "is_unavailable": True, "allowable": True,
            "kind": "insufficient_history",
            "reason": "optimizer_insufficient_history", "issue_codes": codes,
        }
    if not code_set:
        new_end = result.get("new_cache_date_range_end")
        current_after = bool(result.get("current_after"))
        not_current = (
            (not current_after)
            or (not new_end)
            or (str(new_end) < str(target_as_of))
        )
        if not_current:
            return {
                "is_unavailable": True, "allowable": True, "kind": "not_current",
                "reason": "not_current", "issue_codes": codes,
            }
        # Empty codes but reportedly current+fresh yet classified failed:
        # unexpected -> conservative blocking.
        return {
            "is_unavailable": True, "allowable": False, "kind": "blocking",
            "reason": "failed_without_issue_codes_unexpected", "issue_codes": codes,
        }
    # Any other non-empty issue-code combination on a failed result (e.g.
    # optimizer_failed alone) -> conservative blocking.
    return {
        "is_unavailable": True, "allowable": False, "kind": "blocking",
        "reason": f"unrecognized_issue_codes:{sorted(code_set)}",
        "issue_codes": codes,
    }


def _stage_a_worker(payload: dict) -> dict:
    """Module-level ProcessPool worker for Stage A. Lazy-imports the real
    helpers, applies per-worker jitter before any network fetch, and
    captures any helper stdout/stderr."""
    _ensure_project_on_path()
    captured = StringIO()
    out: dict
    try:
        with _redirect_streams(captured):
            import random

            from cache_cutoff_watcher import evaluate_cache_cutoff_state
            from signal_engine_cache_refresher import (
                refresh_signal_engine_cache,
                _default_yfinance_fetcher,
            )

            # Deterministic-but-spread jitter keyed by ticker hash so spawned
            # workers do not all hit the provider on the same tick. Bounded.
            if payload.get("write"):
                jitter = (abs(hash(payload["ticker"])) % 250) / 1000.0
                time.sleep(jitter)

            # Bounded retry/backoff wrapper around the real yfinance fetcher.
            config = RetryConfig(
                retries=int(payload.get("fetch_retries", 2)),
                base_seconds=float(payload.get("fetch_backoff_base", 1.0)),
                max_seconds=float(payload.get("fetch_backoff_max", 8.0)),
            )
            jitter_cap = min(0.25 * config.base_seconds, 0.5)

            def _retry_jitter() -> float:
                if jitter_cap <= 0:
                    return 0.0
                return random.uniform(0.0, jitter_cap)

            fetcher = RetryingFetcher(
                underlying=_default_yfinance_fetcher,
                config=config,
                sleep_fn=time.sleep,
                jitter_fn=_retry_jitter,
            )
            out = stage_a_process_ticker(
                payload["ticker"],
                cache_dir=payload.get("cache_dir"),
                status_dir=payload.get("status_dir"),
                write=bool(payload.get("write")),
                target_as_of=payload["target_as_of"],
                evaluate_fn=evaluate_cache_cutoff_state,
                refresh_fn=refresh_signal_engine_cache,
                fetcher=fetcher,
                provider_name="yfinance_retry",
            )
    except Exception as exc:  # pragma: no cover - defensive
        out = {
            "ticker": payload.get("ticker"),
            "classification": "failed",
            "issue_codes": ["worker_exception"],
            "error": f"{type(exc).__name__}: {exc}",
        }
    out["_captured"] = captured.getvalue()
    return out


# ---------------------------------------------------------------------------
# Stage B: offline multi-timeframe resample
# ---------------------------------------------------------------------------


def stage_b_process_member(
    member: str,
    *,
    intervals: Sequence[str],
    stable_dir: str,
    cache_dir: Optional[str],
    generate_fn: Callable[..., Any],
    save_fn: Callable[..., Any],
    validate_fn: Callable[[str, str], dict],
    set_library_dir_fn: Callable[[str], None],
    repair: bool = False,
) -> dict:
    """Build offline MTF libraries for one member.

    Always (re)builds the non-daily intervals. For 1d, the existing stable
    library is validated with the PRODUCER loader (not a bare file-existence
    check) so a malformed-but-present 1d library cannot pass Stage B and
    then fail Stage E:

      - missing 1d            -> build it (force_overwrite required by the
                                 builder's daily guard);
      - existing + producer-valid 1d  -> skip;
      - existing + producer-INVALID 1d:
          * default (repair=False)  -> record an error (member fails Stage B
            so dependent secondaries are excluded before Stage E);
          * repair=True             -> rebuild it offline from the local
            cache Close source, then RE-VALIDATE with the producer loader;
            a producer-valid existing 1d is never overwritten.

    All callables are injected so the unit tests run without the real
    builder/producer and assert no vendor fetch occurs. ``repair`` is
    offline-only (no network).
    """
    set_library_dir_fn(stable_dir)
    built: List[str] = []
    skipped: List[str] = []
    errors: List[dict] = []
    repaired = False
    daily_status: Optional[dict] = None

    plan_intervals = list(intervals)
    want_daily = DAILY_TIMEFRAME in plan_intervals
    non_daily = [i for i in plan_intervals if i != DAILY_TIMEFRAME]

    for interval in non_daily:
        try:
            lib = generate_fn(
                member,
                interval,
                source_mode=LAUNCH_PATH_SOURCE_MODE,
                cache_dir=cache_dir,
            )
            if not lib:
                errors.append({"interval": interval, "reason": "no_library"})
                continue
            save_fn(lib, interval, force_overwrite=False)
            built.append(interval)
        except Exception as exc:
            errors.append({
                "interval": interval,
                "reason": _redact_local_paths(f"{type(exc).__name__}: {exc}"),
            })

    if want_daily:
        daily_status = validate_fn(member, stable_dir)
        if not daily_status.get("exists"):
            # Missing -> build (unchanged behavior).
            built_ok = _build_daily(
                member, stable_dir, cache_dir, generate_fn, save_fn,
                validate_fn, errors, force=True, label="build",
            )
            if built_ok:
                built.append(DAILY_TIMEFRAME)
        elif daily_status.get("producer_valid"):
            skipped.append(DAILY_TIMEFRAME)
        else:
            # Exists but producer-invalid.
            if not repair:
                errors.append({
                    "interval": DAILY_TIMEFRAME,
                    "reason": "producer_invalid_daily_library",
                    "error_class": daily_status.get("error_class"),
                    "detail": daily_status.get("message"),
                })
            else:
                rebuilt = _build_daily(
                    member, stable_dir, cache_dir, generate_fn, save_fn,
                    validate_fn, errors, force=True, label="repair",
                )
                if rebuilt:
                    revalid = validate_fn(member, stable_dir)
                    daily_status = revalid
                    if revalid.get("producer_valid"):
                        built.append(DAILY_TIMEFRAME)
                        repaired = True
                    else:
                        errors.append({
                            "interval": DAILY_TIMEFRAME,
                            "reason": "repair_revalidation_failed",
                            "error_class": revalid.get("error_class"),
                            "detail": revalid.get("message"),
                        })

    return {
        "member": member,
        "built": built,
        "skipped": skipped,
        "errors": errors,
        "ok": not errors,
        "repaired": repaired,
        "daily_status": daily_status,
    }


def _build_daily(member, stable_dir, cache_dir, generate_fn, save_fn,
                 validate_fn, errors, *, force, label) -> bool:
    """Build (or repair) the member's 1d library offline. Returns True when a
    save was performed. Never overwrites a producer-valid existing 1d: a
    last-moment re-validation guards the save. Appends a redacted error on
    failure."""
    try:
        lib = generate_fn(
            member, DAILY_TIMEFRAME,
            source_mode=LAUNCH_PATH_SOURCE_MODE, cache_dir=cache_dir,
        )
        if not lib:
            errors.append({
                "interval": DAILY_TIMEFRAME,
                "reason": f"no_library:{label}",
            })
            return False
        # Guard: never clobber a now-producer-valid 1d (race / repaired
        # elsewhere). For 'build' a present-and-valid 1d means skip.
        recheck = validate_fn(member, stable_dir)
        if recheck.get("exists") and recheck.get("producer_valid") and label == "build":
            return False
        save_fn(lib, DAILY_TIMEFRAME, force_overwrite=force)
        return True
    except Exception as exc:
        errors.append({
            "interval": DAILY_TIMEFRAME,
            "reason": _redact_local_paths(f"{label}:{type(exc).__name__}: {exc}"),
        })
        return False


def daily_stable_exists(member: str, stable_dir: str) -> bool:
    name = f"{member}_stable_v{STABLE_ENGINE_VERSION_TAG}.pkl"
    return (Path(stable_dir) / name).is_file()


def daily_stable_is_producer_valid(
    member: str, stable_dir: str, *, loader_fn: Optional[Callable[..., Any]] = None,
) -> dict:
    """Producer-compatible validation of a member's existing 1d stable
    library. Calls k6_mtf_history_producer.load_member_library(member, '1d',
    stable_dir=...) and returns a structured status dict (never raises).

    Any path-bearing exception text is redacted before it enters the status,
    and ``path`` is reported as the bare library basename so no absolute
    local path can leak into JSON/logs.
    """
    name = f"{member}_stable_v{STABLE_ENGINE_VERSION_TAG}.pkl"
    status: dict = {
        "member": member, "interval": DAILY_TIMEFRAME,
        "exists": (Path(stable_dir) / name).is_file(),
        "producer_valid": False, "reason": None, "error_class": None,
        "message": None, "path": name, "dates_count": None,
        "signals_count": None, "can_repair": False, "action": None,
    }
    if not status["exists"]:
        status["reason"] = "missing"
        status["action"] = "build"
        return status
    if loader_fn is None:
        from k6_mtf_history_producer import load_member_library as loader_fn
    try:
        lib = loader_fn(member, DAILY_TIMEFRAME, stable_dir=stable_dir)
        dates = lib.get("dates") if hasattr(lib, "get") else None
        signals = lib.get("signals") if hasattr(lib, "get") else None
        status["producer_valid"] = True
        status["reason"] = "ok"
        status["action"] = "skip"
        try:
            status["dates_count"] = len(dates) if dates is not None else None
            status["signals_count"] = len(signals) if signals is not None else None
        except Exception:
            pass
        return status
    except Exception as exc:
        status["producer_valid"] = False
        status["reason"] = "producer_invalid"
        status["error_class"] = type(exc).__name__
        status["message"] = _redact_local_paths(str(exc))
        # Repairable from the local cache/results Close source (offline).
        status["can_repair"] = True
        status["action"] = "repair_or_exclude"
        return status


def _set_builder_library_dir(stable_dir: str) -> None:
    """Point the builder module's SIGNAL_LIBRARY_DIR at the requested
    stable dir, localized to the worker process."""
    import signal_library.multi_timeframe_builder as mtb

    mtb.SIGNAL_LIBRARY_DIR = stable_dir


def _stage_b_worker(payload: dict) -> dict:
    """Module-level ProcessPool worker for Stage B (offline)."""
    _ensure_project_on_path()
    captured = StringIO()
    out: dict
    try:
        with _redirect_streams(captured):
            import signal_library.multi_timeframe_builder as mtb

            out = stage_b_process_member(
                payload["member"],
                intervals=payload["intervals"],
                stable_dir=payload["stable_dir"],
                cache_dir=payload.get("cache_dir"),
                generate_fn=mtb.generate_signals_for_interval,
                save_fn=mtb.save_signal_library,
                validate_fn=daily_stable_is_producer_valid,
                set_library_dir_fn=_set_builder_library_dir,
                repair=bool(payload.get("repair")),
            )
    except Exception as exc:  # pragma: no cover - defensive
        out = {
            "member": payload.get("member"),
            "built": [],
            "skipped": [],
            "errors": [{"reason": f"{type(exc).__name__}: {exc}"}],
            "ok": False,
        }
    out["_captured"] = captured.getvalue()
    return out


# ---------------------------------------------------------------------------
# Process / path helpers for spawned workers
# ---------------------------------------------------------------------------


def _ensure_project_on_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


@contextmanager
def _redirect_streams(buffer: StringIO):
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = buffer
    sys.stderr = buffer
    try:
        yield
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


def _parallel_map(
    worker: Callable[[dict], dict], payloads: Sequence[dict], workers: int
) -> List[dict]:
    """Run ``worker`` over ``payloads`` in a process pool and return the
    results. Factored out so tests can substitute a serial in-process map
    (spawned children would not see monkeypatched module globals)."""
    if not payloads:
        return []
    from concurrent.futures import ProcessPoolExecutor

    results: List[dict] = []
    with ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        for res in ex.map(worker, list(payloads)):
            results.append(res)
    return results


def stage_aprime_rebuild(
    secs: Sequence[str],
    *,
    cache_dir: Optional[str],
    price_cache_dir: Optional[str],
    write: bool,
    fmt: str = DEFAULT_PRICE_CACHE_FORMAT,
    report_fn: Optional[Callable[..., dict]] = None,
) -> Tuple[dict, List[str], List[str]]:
    """Rebuild price_cache/daily for ``secs`` from their fresh cache PKLs.

    ``fmt`` selects the on-disk price-cache format ("csv" by default, or
    "parquet" when explicitly requested). CSV is the default because the
    repo's price_cache/daily convention is CSV and the pinned spyproject2
    env ships no parquet engine; parquet remains available as an explicit
    opt-in via --price-cache-format.

    Uses ``overwrite=True`` so a stale csv/parquet cannot shadow a freshly
    refreshed PKL inside the producer. NEVER deletes/clears a price-cache
    file: a secondary that lacks a usable PKL surfaces an issue code and is
    excluded, not cleared.

    Returns (report, kept_secondaries, excluded_secondaries).
    """
    if report_fn is None:
        from stackbuilder_price_cache_writer import (
            build_price_cache_write_report as report_fn,
        )
    with stdout_to_stderr():
        report = report_fn(
            list(secs),
            signal_cache_dir=cache_dir,
            stackbuilder_price_cache_dir=price_cache_dir,
            format=fmt,
            write=write,
            overwrite=True,
        )
    rows = report.get("rows", [])
    excluded = [r.get("ticker") for r in rows if r.get("issue_codes")]
    excluded_set = set(excluded)
    kept = [s for s in secs if s not in excluded_set]
    return report, kept, excluded


# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except Exception:
        pass
    if os.name == "nt":
        # Without psutil we cannot cheaply probe liveness on Windows;
        # be conservative (assume alive) and rely on TTL-based reclaim.
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@dataclass
class LockHandle:
    path: Path
    acquired: bool
    reclaimed_stale: bool = False
    holder: Optional[dict] = None


def acquire_lock(
    lock_path: Path, *, driver_run_id: str, ttl_seconds: int
) -> LockHandle:
    """Acquire the execute lock via atomic O_CREAT|O_EXCL. Reclaims a stale
    lock (dead pid or age > ttl). Raises RuntimeError if held by a live,
    non-expired holder."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at_utc": utc_now_iso(),
        "driver_run_id": driver_run_id,
        "stage": "init",
    }

    def _create() -> LockHandle:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps(payload).encode("utf-8"))
        finally:
            os.close(fd)
        return LockHandle(path=lock_path, acquired=True)

    try:
        return _create()
    except FileExistsError:
        pass

    holder: Optional[dict] = None
    try:
        holder = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        holder = None

    stale = False
    if holder is None:
        stale = True
    else:
        pid = int(holder.get("pid", -1) or -1)
        if not _pid_alive(pid):
            stale = True
        else:
            started = holder.get("started_at_utc")
            age = _iso_age_seconds(started)
            if age is not None and age > ttl_seconds:
                stale = True

    if not stale:
        raise RuntimeError(
            f"recook lock held by pid={holder.get('pid') if holder else '?'} "
            f"host={holder.get('host') if holder else '?'}"
        )

    # Reclaim: overwrite atomically.
    tmp = lock_path.with_name(lock_path.name + ".reclaim")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(str(tmp), str(lock_path))
    return LockHandle(
        path=lock_path, acquired=True, reclaimed_stale=True, holder=holder
    )


def update_lock_stage(handle: LockHandle, stage: str) -> None:
    if not handle.acquired:
        return
    try:
        data = json.loads(handle.path.read_text(encoding="utf-8"))
        data["stage"] = stage
        tmp = handle.path.with_name(handle.path.name + ".stage")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(str(tmp), str(handle.path))
    except Exception:
        pass


def release_lock(handle: LockHandle) -> None:
    if not handle.acquired:
        return
    try:
        handle.path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _iso_age_seconds(started_at_utc: Optional[str]) -> Optional[float]:
    if not started_at_utc:
        return None
    try:
        import calendar

        # The lock timestamp is UTC; interpret it as UTC (calendar.timegm)
        # rather than local time (time.mktime) so the computed age is not
        # skewed by the local timezone offset.
        t = time.strptime(str(started_at_utc), "%Y-%m-%dT%H:%M:%SZ")
        return time.time() - calendar.timegm(t)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Envelope + exit-code helpers
# ---------------------------------------------------------------------------


@dataclass
class Driver:
    args: argparse.Namespace
    stages: List[str]
    executed: bool
    target_as_of: str
    driver_run_id: str
    project_root: Path
    exclusions: List[dict] = field(default_factory=list)
    failures: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)
    # Set True when allowed Stage A exclusions were tolerated under
    # --allow-stage-a-exclusions; drives partial status/exit code.
    partial: bool = False
    partial_reasons: List[str] = field(default_factory=list)


def emit_envelope(envelope: dict) -> None:
    """Write exactly one JSON object to stdout."""
    sys.stdout.write(json.dumps(envelope, indent=2, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="k6_recook",
        description=(
            "SPRINT 500 K=6 recook driver. Dry-run by default. Emits one "
            "k6_recook_summary_v1 JSON object to stdout; all logs go to "
            "stderr. Does NOT run StackBuilder/ImpactSearch/OnePass, does "
            "NOT promote publicly, does NOT push or deploy."
        ),
    )
    p.add_argument("--execute", action="store_true", default=False)
    p.add_argument("--allow-network-fetch", action="store_true", default=False)
    p.add_argument(
        "--allow-stage-a-exclusions", action="store_true", default=False,
        help=(
            "Operator policy: allow the chain to continue past NON-NETWORK "
            "Stage A ticker unavailability (dead_no_history / not_current / "
            "optimizer insufficient_history) by excluding the dependent "
            "secondaries and producing a PARTIAL private candidate. Default "
            "false = fail-closed (any Stage A failure or dependent-secondary "
            "exclusion halts at Stage A). Network/provider/systemic failures "
            "(data_fetch_failed, retry exhaustion, etc.) ALWAYS halt, even "
            "with this flag. Never enables public promotion."
        ),
    )
    p.add_argument("--target-as-of", default=DEFAULT_TARGET_AS_OF)
    p.add_argument("--driver-run-id", default=None)
    p.add_argument("--stackbuilder-root", default="output/stackbuilder")
    p.add_argument("--cache-dir", default="cache/results")
    p.add_argument("--status-dir", default="cache/status")
    p.add_argument("--price-cache-dir", default="price_cache/daily")
    p.add_argument("--stable-dir", default="signal_library/data/stable")
    p.add_argument("--output-root", default="output/k6_mtf")
    p.add_argument("--secondaries", default=None)
    p.add_argument("--a-workers", type=int, default=10)
    p.add_argument(
        "--b-workers", type=int, default=(os.cpu_count() or 1)
    )
    p.add_argument(
        "--fetch-retries", type=int, default=2,
        help=(
            "Stage A transient-fetch retries AFTER the first attempt "
            "(nonnegative; total attempts = retries + 1). Default 2."
        ),
    )
    p.add_argument(
        "--fetch-backoff-base-seconds", type=float, default=1.0,
        help="Stage A exponential backoff base seconds (nonnegative). Default 1.0.",
    )
    p.add_argument(
        "--fetch-backoff-max-seconds", type=float, default=8.0,
        help="Stage A backoff ceiling seconds (nonnegative). Default 8.0.",
    )
    p.add_argument(
        "--repair-invalid-daily-stable", action="store_true", default=False,
        help=(
            "Stage B policy: when an existing 1d stable library exists but "
            "FAILS producer validation, rebuild it OFFLINE from the local "
            "cache Close source (no network) and re-validate before use. "
            "Default false = a producer-invalid existing 1d library fails the "
            "member in Stage B and excludes its dependent secondaries before "
            "Stage E. Producer-valid 1d libraries are never overwritten."
        ),
    )
    p.add_argument(
        "--price-cache-format",
        choices=list(PRICE_CACHE_FORMATS),
        default=DEFAULT_PRICE_CACHE_FORMAT,
        help=(
            "Stage Aprime price_cache/daily on-disk format. Default 'csv' "
            "(repo convention; spyproject2 has no parquet engine). 'parquet' "
            "is an explicit opt-in and requires pyarrow/fastparquet."
        ),
    )
    p.add_argument("--stages", default=None)
    p.add_argument("--restage-all", action="store_true", default=False)
    p.add_argument("--unpin", action="store_true", default=False)
    p.add_argument(
        "--lock-ttl",
        default="6h",
        help="Lock staleness TTL. Accepts 6h, 360m, 21600s, or raw seconds.",
    )
    promote_group = p.add_mutually_exclusive_group()
    promote_group.add_argument(
        "--promote-dry-run", dest="promote_dry_run", action="store_true",
        default=True,
    )
    promote_group.add_argument(
        "--no-promote-dry-run", dest="promote_dry_run", action="store_false",
    )
    return p


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _new_envelope(driver: Driver) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "driver_run_id": driver.driver_run_id,
        "executed": driver.executed,
        "target_as_of": driver.target_as_of,
        "exit_code": 0,
        "status": "dry_run",
        "halted_at": None,
        "lock": {"acquired": False, "reclaimed_stale": False},
        "stage0": {},
        "union": {},
        "stageA": {},
        "stageAprime": {},
        "stageB": {},
        "stageE": {},
        "stageF": {},
        "stageH": {},
        "exclusions": driver.exclusions,
        "failures": driver.failures,
        "warnings": driver.warnings,
        "timings": driver.timings,
    }


def _refuse(envelope: dict, reason: str, *, halted_at: Optional[str] = None) -> dict:
    envelope["status"] = "refused"
    envelope["exit_code"] = 2
    envelope["halted_at"] = halted_at
    envelope.setdefault("warnings", []).append(f"refused: {reason}")
    log(f"REFUSED: {reason}")
    return envelope


def run_stage0_and_union(driver: Driver, envelope: dict) -> Optional[List[SecondaryPlan]]:
    """Run Stage 0 planning + union resolution. Returns the included plans
    (those with members), or None if a structural refusal occurred (the
    envelope is updated in place)."""
    args = driver.args
    sb_root = Path(args.stackbuilder_root)
    sec_dirs = discover_secondary_dirs(sb_root)

    requested: Optional[set[str]] = None
    if args.secondaries:
        requested = {
            s.strip() for s in args.secondaries.split(",") if s.strip()
        }
        sec_dirs = [d for d in sec_dirs if d.name in requested]

    combo_file_count = 0
    for d in sec_dirs:
        combo_file_count += len(seed_dirs_with_combo(d))

    plans: List[SecondaryPlan] = []
    for d in sec_dirs:
        plans.append(
            plan_secondary(
                d,
                stackbuilder_root=args.stackbuilder_root,
                restage_all=args.restage_all,
                unpin=args.unpin,
            )
        )

    buildable = [p for p in plans if seed_dirs_with_combo(Path(p.secondary_dir))]
    included = [p for p in plans if p.members is not None]
    existing = [p for p in plans if p.status == "existing"]
    synth = [p for p in plans if p.status == "synthesize"]
    blocked = [p for p in plans if p.status == "blocked_by_pin"]
    zero_combo = [p for p in plans if p.reason == "zero_combo_dir"]
    malformed = [
        p
        for p in plans
        if p.reason
        in {
            "members_not_list",
            "members_not_six",
            "member_token_invalid",
            "combo_unreadable",
            "combo_not_object",
        }
    ]

    # Record exclusions (anything buildable but not included).
    for p in plans:
        if p.members is None and p.reason != "zero_combo_dir":
            driver.exclusions.append(
                {"secondary": p.secondary, "stage": "stage0", "reason": p.reason}
            )

    # Exact, bounded-but-complete plan of the pointers Stage 0 would (or
    # did) write. Synthesize plans only; existing pointers are counted but
    # not listed unless --restage-all turned them into synthesize plans.
    pointer_write_plan = [
        {
            "secondary": p.secondary,
            "target": to_posix(
                f"{args.stackbuilder_root}/{p.secondary}/{SELECTED_BUILD_FILENAME}"
            ),
            "chosen_run_dir": p.chosen_run_dir,
            "combo_path": p.combo_path,
        }
        for p in synth
    ]

    # Actually write synthesized pointers only under execute.
    synth_written = 0
    synth_planned = 0
    pointer_write_failures: List[dict] = []
    resolve_failures: List[dict] = []
    for p in synth:
        synth_planned += 1
        if driver.executed:
            try:
                payload = build_pointer_payload(p.secondary, p.chosen_run_dir)
                target = Path(args.stackbuilder_root) / p.secondary / SELECTED_BUILD_FILENAME
                with stdout_to_stderr():
                    write_pointer_atomic(target, payload)
                synth_written += 1
            except Exception as exc:
                pointer_write_failures.append(
                    {
                        "secondary": p.secondary,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
                driver.failures.append(
                    {
                        "secondary": p.secondary,
                        "stage": "stage0",
                        "reason": f"pointer_write_failed: {exc}",
                    }
                )

    # Under execute, verify every synthesized secondary resolves to six
    # members via the real producer entrypoint.
    if driver.executed and synth_written:
        from k6_mtf_history_producer import resolve_k6_stack, K6StackResolutionError

        for p in synth:
            try:
                with stdout_to_stderr():
                    stack = resolve_k6_stack(
                        p.secondary, stackbuilder_root=args.stackbuilder_root
                    )
                if len(stack.members) != 6:
                    raise K6StackResolutionError("member count != 6")
            except Exception as exc:
                rec = {
                    "secondary": p.secondary,
                    "stage": "stage0",
                    "reason": f"resolve_k6_stack_failed: {exc}",
                }
                resolve_failures.append(rec)
                driver.failures.append(rec)

    union = resolve_union(included)
    member_keys = set()
    for p in included:
        for ticker, _proto, _raw in (p.members or []):
            member_keys.add(ticker.strip().upper())

    envelope["stage0"] = {
        "stackbuilder_root": to_posix(str(sb_root)),
        "secondary_dirs_scanned": len(sec_dirs),
        "combo_file_count": combo_file_count,
        "buildable_secondaries": len(buildable),
        "selected_build_existing_count": len(existing),
        "selected_build_synthesized_count": (
            synth_written if driver.executed else 0
        ),
        "selected_build_would_synthesize_count": synth_planned,
        "selected_build_blocked_count": len(blocked),
        "malformed_combo_count": len(malformed),
        "skipped_zero_combo_count": len(zero_combo),
        "pointer_write_failures": pointer_write_failures,
        "resolve_failures": resolve_failures,
        "pointer_write_plan": pointer_write_plan,
    }
    envelope["union"] = {
        "included_secondaries": len(included),
        "buildable_secondaries": len(buildable),
        "combo_file_count": combo_file_count,
        "selected_build_existing_count": len(existing),
        "selected_build_synthesized_count": (
            synth_written if driver.executed else 0
        ),
        "selected_build_blocked_count": len(blocked),
        "malformed_combo_count": len(malformed),
        "skipped_zero_combo_count": len(zero_combo),
        "unique_member_count": len(member_keys),
        "refresh_union_count": len(union),
        "rankable_secondary_count": len(included),
        "excluded_secondary_count": len(driver.exclusions),
    }
    driver.timings["stage0_done"] = time.monotonic()

    if not buildable:
        _refuse(envelope, "no buildable secondaries found", halted_at="stage0")
        return None
    if not included:
        _refuse(
            envelope,
            "no rankable secondaries remain after preflight exclusions",
            halted_at="stage0",
        )
        return None

    # Under execute, any Stage 0 pointer-write or resolve failure halts
    # immediately at stage0; do NOT proceed to Stage A / Aprime / B / E /
    # F / H. Detailed failure records are preserved in the envelope.
    if driver.executed and (pointer_write_failures or resolve_failures):
        envelope["status"] = "failed"
        envelope["exit_code"] = 1
        envelope["halted_at"] = "stage0"
        envelope.setdefault("warnings", []).append(
            "halted at stage0: "
            f"{len(pointer_write_failures)} pointer-write failure(s), "
            f"{len(resolve_failures)} resolve failure(s); "
            "not proceeding to Stage A"
        )
        log("HALT stage0: pointer-write/resolve failures; Stage A not run")
        return None

    return included


def _plan_stage_counts(
    driver: Driver, envelope: dict, included: List[SecondaryPlan]
) -> None:
    """Populate stageA..stageH with dry-run planning counts (no PKL loads,
    no engine calls, no network)."""
    union = resolve_union(included)
    member_keys = {
        ticker.strip().upper()
        for p in included
        for (ticker, _pr, _rw) in (p.members or [])
    }
    rankable = [p.secondary for p in included]

    # Stage B planned 1d-create count via cheap filesystem stat.
    members_missing_daily = 0
    for key in member_keys:
        # Use the display ticker for the filename check.
        disp = union.get(key, key)
        if not daily_stable_exists(disp, driver.args.stable_dir):
            members_missing_daily += 1

    envelope["stageA"] = {
        "ran": False,
        "planned": True,
        "would_refresh_union": len(union),
        "skipped_fresh": 0,
        "note": "freshness gate runs offline at execute time, not in dry-run",
        "workers": driver.args.a_workers,
        "allow_stage_a_exclusions": bool(driver.args.allow_stage_a_exclusions),
        "strict_default_fail_closed": not bool(
            driver.args.allow_stage_a_exclusions
        ),
        "fetch_retry_config": {
            "retries": driver.args.fetch_retries,
            "base_seconds": driver.args.fetch_backoff_base_seconds,
            "max_seconds": driver.args.fetch_backoff_max_seconds,
        },
    }
    envelope["stageAprime"] = {
        "ran": False,
        "planned": True,
        "mode": "rebuild",
        "format": driver.args.price_cache_format,
        "planned_writes": len(rankable),
        "note": "price_cache rebuilt from fresh PKLs at execute time",
    }
    envelope["stageB"] = {
        "ran": False,
        "planned": True,
        "unique_members": len(member_keys),
        "non_daily_jobs": len(member_keys) * len(NON_DAILY_TIMEFRAMES),
        "planned_daily_creates": members_missing_daily,
        "repair_invalid_daily_stable": bool(
            driver.args.repair_invalid_daily_stable
        ),
        "note": (
            "1d libraries are producer-validated at execute time; invalid "
            "ones fail the member (or are repaired with "
            "--repair-invalid-daily-stable)"
        ),
        "workers": driver.args.b_workers,
    }
    envelope["stageE"] = {
        "ran": False,
        "planned": True,
        "secondaries": len(rankable),
    }
    envelope["stageF"] = {
        "ran": False,
        "planned": True,
        "run_dir": to_posix(f"{driver.args.output_root}/{driver.driver_run_id}"),
    }
    envelope["stageH"] = {
        "ran": False,
        "planned": True,
        "mode": "private_internal_dry_run",
        "promotion_blocked_by_failures": bool(
            driver.exclusions or driver.failures
        ),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Parse / validate driver-level inputs.
    try:
        target_as_of = parse_target_date(args.target_as_of)
    except ValueError as exc:
        env = {
            "schema_version": SCHEMA_VERSION,
            "driver_run_id": args.driver_run_id or "unset",
            "executed": bool(args.execute),
            "target_as_of": args.target_as_of,
            "exit_code": 2,
            "status": "refused",
            "halted_at": "preflight",
            "lock": {"acquired": False, "reclaimed_stale": False},
            "stage0": {}, "union": {}, "stageA": {}, "stageAprime": {},
            "stageB": {}, "stageE": {}, "stageF": {}, "stageH": {},
            "exclusions": [], "failures": [], "warnings": [f"refused: {exc}"],
            "timings": {},
        }
        emit_envelope(env)
        return env["exit_code"]

    driver_run_id = args.driver_run_id or utc_run_id()
    project_root = Path(os.path.abspath(os.path.dirname(__file__)))

    driver = Driver(
        args=args,
        stages=list(STAGE_ORDER),
        executed=bool(args.execute),
        target_as_of=target_as_of,
        driver_run_id=driver_run_id,
        project_root=project_root,
    )

    envelope = _new_envelope(driver)
    envelope["status"] = "dry_run" if not driver.executed else "ok"
    start = time.monotonic()

    # --- Global refusal checks (before any write, before lock) ---
    try:
        driver.stages = parse_stages(args.stages)
    except ValueError as exc:
        emit_envelope(_refuse(envelope, str(exc), halted_at="preflight"))
        return envelope["exit_code"]
    envelope["stages_selected"] = driver.stages

    # In execute mode the selected stages must be a contiguous prefix of the
    # canonical chain starting at stage0. This forbids unsafe subsets (e.g.
    # E,F,H or stage0,A,B) that would produce downstream artifacts while
    # skipping a freshness prerequisite. Refused before any write.
    if driver.executed and not is_contiguous_prefix(driver.stages):
        emit_envelope(
            _refuse(
                envelope,
                "--execute requires --stages to be a contiguous prefix of "
                f"{list(STAGE_ORDER)!r} starting at stage0; got "
                f"{driver.stages!r}",
                halted_at="preflight",
            )
        )
        return envelope["exit_code"]

    # Retry/backoff config must be nonnegative (both modes).
    if args.fetch_retries < 0:
        emit_envelope(_refuse(
            envelope,
            f"--fetch-retries must be >= 0; got {args.fetch_retries}",
            halted_at="preflight",
        ))
        return envelope["exit_code"]
    if args.fetch_backoff_base_seconds < 0:
        emit_envelope(_refuse(
            envelope,
            "--fetch-backoff-base-seconds must be >= 0; got "
            f"{args.fetch_backoff_base_seconds}",
            halted_at="preflight",
        ))
        return envelope["exit_code"]
    if args.fetch_backoff_max_seconds < 0:
        emit_envelope(_refuse(
            envelope,
            "--fetch-backoff-max-seconds must be >= 0; got "
            f"{args.fetch_backoff_max_seconds}",
            halted_at="preflight",
        ))
        return envelope["exit_code"]

    try:
        lock_ttl_seconds = parse_lock_ttl(args.lock_ttl)
    except ValueError as exc:
        emit_envelope(_refuse(envelope, str(exc), halted_at="preflight"))
        return envelope["exit_code"]

    sb_root = Path(args.stackbuilder_root)
    if not sb_root.exists() or not sb_root.is_dir():
        emit_envelope(
            _refuse(
                envelope,
                f"stackbuilder root missing: {args.stackbuilder_root}",
                halted_at="preflight",
            )
        )
        return envelope["exit_code"]

    network_stage_selected = ("A" in driver.stages) or (
        driver.stages == list(STAGE_ORDER)
    )
    if driver.executed and network_stage_selected and not args.allow_network_fetch:
        emit_envelope(
            _refuse(
                envelope,
                "--execute with Stage A/full chain requires "
                "--allow-network-fetch (refused before any write)",
                halted_at="preflight",
            )
        )
        return envelope["exit_code"]

    # --- Stage 0 + union (planning always; writes only under execute) ---
    lock_handle: Optional[LockHandle] = None
    try:
        if driver.executed:
            lock_path = Path(args.output_root) / LOCK_FILENAME
            try:
                lock_handle = acquire_lock(
                    lock_path,
                    driver_run_id=driver_run_id,
                    ttl_seconds=lock_ttl_seconds,
                )
            except RuntimeError as exc:
                emit_envelope(
                    _refuse(envelope, str(exc), halted_at="lock")
                )
                return envelope["exit_code"]
            envelope["lock"] = {
                "acquired": True,
                "reclaimed_stale": lock_handle.reclaimed_stale,
                "path": to_posix(str(lock_handle.path)),
            }
            update_lock_stage(lock_handle, "stage0")

        included = run_stage0_and_union(driver, envelope)
        if included is None:
            envelope["timings"]["total_seconds"] = round(
                time.monotonic() - start, 3
            )
            emit_envelope(envelope)
            return envelope["exit_code"]

        if not driver.executed:
            # Pure planning for the downstream stages.
            _plan_stage_counts(driver, envelope, included)
            envelope["status"] = "dry_run"
            envelope["exit_code"] = 0
            envelope["timings"]["total_seconds"] = round(
                time.monotonic() - start, 3
            )
            emit_envelope(envelope)
            return envelope["exit_code"]

        # --- EXECUTE path (not exercised by this sprint's authorized run) ---
        # The execute path is implemented for completeness and unit-tested at
        # the seam level; this prompt authorizes only a dry-run.
        envelope["status"] = "ok"
        envelope["warnings"].append(
            "execute path requested; running staged chain with barriers"
        )
        rc = _run_execute_chain(driver, envelope, included, lock_handle)
        envelope["timings"]["total_seconds"] = round(time.monotonic() - start, 3)
        emit_envelope(envelope)
        return rc
    except Exception as exc:  # pragma: no cover - top-level safety net
        log("UNHANDLED ERROR:\n" + traceback.format_exc())
        envelope["status"] = "failed"
        envelope["exit_code"] = 1
        envelope["halted_at"] = envelope.get("halted_at") or "unknown"
        envelope.setdefault("failures", []).append(
            {"stage": "driver", "reason": f"{type(exc).__name__}: {exc}"}
        )
        envelope["timings"]["total_seconds"] = round(time.monotonic() - start, 3)
        emit_envelope(envelope)
        return 1
    finally:
        if lock_handle is not None:
            release_lock(lock_handle)


def _run_execute_chain(
    driver: Driver,
    envelope: dict,
    included: List[SecondaryPlan],
    lock_handle: Optional[LockHandle],
) -> int:
    """Run A -> Aprime -> B -> E -> F -> H with mandatory barriers.

    Implemented for completeness; per the sprint prompt no production
    execute run is performed by the authorized validation. Returns an exit
    code and fills the envelope.
    """
    args = driver.args
    union = resolve_union(included)
    rankable = {p.secondary: p for p in included}

    # ---- Stage A barrier ----
    if "A" in driver.stages:
        if lock_handle:
            update_lock_stage(lock_handle, "A")
        payloads = [
            {
                "ticker": disp,
                "cache_dir": args.cache_dir,
                "status_dir": args.status_dir,
                "write": True,
                "target_as_of": driver.target_as_of,
                "fetch_retries": args.fetch_retries,
                "fetch_backoff_base": args.fetch_backoff_base_seconds,
                "fetch_backoff_max": args.fetch_backoff_max_seconds,
            }
            for disp in union.values()
        ]
        a_results = _parallel_map(_stage_a_worker, payloads, args.a_workers)
        for res in a_results:
            if res.get("_captured"):
                log(res["_captured"].rstrip())
        import collections as _collections

        refreshed = [
            r for r in a_results if r.get("classification") == "refreshed"
        ]
        skipped = [
            r for r in a_results if r.get("classification") == "skipped_fresh"
        ]

        # Classify every outcome under the unavailable-data policy.
        classified = [
            (r, _classify_stage_a_outcome(r, target_as_of=driver.target_as_of))
            for r in a_results
        ]
        unavailable = [(r, i) for (r, i) in classified if i["is_unavailable"]]
        blocking = [(r, i) for (r, i) in unavailable if not i["allowable"]]
        allowed = [(r, i) for (r, i) in unavailable if i["allowable"]]

        kind_counts = _collections.Counter(i["kind"] for (_r, i) in unavailable)
        issue_dist = _collections.Counter(
            ",".join(sorted(set(r.get("issue_codes") or []))) or "<none>"
            for (r, _i) in unavailable
        )
        data_fetch_failed_count = sum(
            1 for (r, _i) in classified
            if "data_fetch_failed" in set(r.get("issue_codes") or [])
        )
        retry_exhausted_count = sum(
            1 for r in a_results if r.get("retry_exhausted")
        )

        def _depends_roles(plan, plan_sec, tkr_upper):
            roles = []
            if str(plan_sec).strip().upper() == tkr_upper:
                roles.append(("secondary", None, None))
            for (mt, mp, mraw) in (plan.members or []):
                if str(mt).strip().upper() == tkr_upper:
                    roles.append(("member", mraw, mp))
            return roles

        unavailable_keys = {
            str(r.get("ticker")).strip().upper() for (r, _i) in unavailable
        }
        dependency_map = {}
        for key in sorted(unavailable_keys):
            deps = [
                sec for sec, plan in rankable.items()
                if _depends_roles(plan, sec, key)
            ]
            dependency_map[key] = sorted(deps)

        fetch_retries_attempted = sum(
            int(r.get("fetch_retries", 0) or 0) for r in a_results
        )
        fetch_retry_successes = sum(
            1 for r in a_results
            if int(r.get("fetch_retries", 0) or 0) > 0
            and not r.get("retry_exhausted")
            and r.get("classification") == "refreshed"
        )

        envelope["stageA"] = {
            "ran": True,
            "submitted": len(payloads),
            "refreshed": len(refreshed),
            "skipped_fresh": len(skipped),
            "failed": len([
                r for r in a_results if r.get("classification") == "failed"
            ]),
            "workers": args.a_workers,
            "allow_stage_a_exclusions": bool(args.allow_stage_a_exclusions),
            "strict_default_fail_closed": not bool(args.allow_stage_a_exclusions),
            "unavailable_ticker_count": len(unavailable),
            "allowed_unavailable_ticker_count": len(allowed),
            "blocked_unavailable_ticker_count": len(blocking),
            "data_fetch_failed": data_fetch_failed_count,
            "retry_exhausted": retry_exhausted_count,
            "not_current": kind_counts.get("not_current", 0),
            "insufficient_history": kind_counts.get("insufficient_history", 0),
            "dead_no_history": kind_counts.get("dead_no_history", 0),
            "issue_code_distribution": dict(issue_dist),
            "unavailable_tickers": [
                {
                    "ticker": r.get("ticker"),
                    "ticker_classification": i["kind"],
                    "allowable": i["allowable"],
                    "reason": i["reason"],
                    "issue_codes": r.get("issue_codes") or [],
                    "current_after": r.get("current_after"),
                    "new_cache_date_range_end": r.get("new_cache_date_range_end"),
                }
                for (r, i) in unavailable
            ],
            "dependency_map_by_ticker": dependency_map,
            "fetch_retries_attempted": fetch_retries_attempted,
            "fetch_retry_successes": fetch_retry_successes,
            "fetch_retry_exhausted": retry_exhausted_count,
            "fetch_retry_config": {
                "retries": args.fetch_retries,
                "base_seconds": args.fetch_backoff_base_seconds,
                "max_seconds": args.fetch_backoff_max_seconds,
            },
            "retry_details": [
                {
                    "ticker": r.get("ticker"),
                    "fetch_attempts": r.get("fetch_attempts"),
                    "fetch_retries": r.get("fetch_retries"),
                    "retry_exhausted": bool(r.get("retry_exhausted")),
                    "retry_error_count": r.get("retry_error_count"),
                }
                for r in a_results
                if int(r.get("fetch_retries", 0) or 0) > 0
                or r.get("retry_exhausted")
            ],
            # Filled by the decision logic below.
            "excluded_secondary_count": 0,
            "remaining_rankable_secondary_count": len(rankable),
            "excluded_secondaries": [],
        }

        def _record_unavail_failure(r, i, reason):
            key = str(r.get("ticker")).strip().upper()
            driver.failures.append({
                "ticker": r.get("ticker"), "stage": "A", "reason": reason,
                "ticker_classification": i["kind"],
                "issue_codes": r.get("issue_codes") or [],
                "current_after": r.get("current_after"),
                "new_cache_date_range_end": r.get("new_cache_date_range_end"),
                "target_as_of": driver.target_as_of,
                "dependent_secondaries": dependency_map.get(key, []),
            })

        def _halt_stage_a():
            envelope["halted_at"] = "A"
            envelope["status"] = "failed"
            envelope["exit_code"] = 1

        # --- Decision: blocking (network/provider/systemic) ALWAYS halts ---
        if blocking:
            for (r, i) in blocking:
                _record_unavail_failure(r, i, f"stage_a_blocking:{i['reason']}")
            _halt_stage_a()
            return 1

        # --- Allowable-only unavailability ---
        if allowed:
            if not args.allow_stage_a_exclusions:
                # Strict default fail-closed: do not exclude, halt at A.
                for (r, i) in allowed:
                    _record_unavail_failure(
                        r, i, "stage_a_unavailable_strict_fail_closed"
                    )
                _halt_stage_a()
                return 1

            # Operator-authorized: exclude dependent secondaries (dedup),
            # preserving every cause in evidence.
            excluded_secs: Dict[str, List[dict]] = {}
            for (r, i) in allowed:
                tkr = r.get("ticker")
                key = str(tkr).strip().upper()
                for sec in list(rankable.keys()):
                    plan = rankable[sec]
                    for (role, member_token, member_protocol) in _depends_roles(
                        plan, sec, key
                    ):
                        rec = {
                            "secondary": sec,
                            "stage": "A",
                            "reason": f"stage_a_unavailable:{i['kind']}",
                            "ticker": tkr,
                            "ticker_classification": i["kind"],
                            "issue_codes": r.get("issue_codes") or [],
                            "current_after": r.get("current_after"),
                            "new_cache_date_range_end": r.get(
                                "new_cache_date_range_end"
                            ),
                            "target_as_of": driver.target_as_of,
                            "dependent_role": role,
                            "cache_path": r.get("cache_path"),
                            "status_path": r.get("status_path"),
                            "message": (
                                f"{tkr} unavailable ({i['kind']}: {i['reason']}); "
                                f"excludes secondary {sec} as {role}."
                            ),
                            "action": (
                                "Refresh/repair the ticker source or remove it "
                                "from the K=6 stack, then rerun."
                            ),
                        }
                        if role == "member":
                            rec["member_token"] = member_token
                            rec["member_protocol"] = member_protocol
                        excluded_secs.setdefault(sec, []).append(rec)

            for sec, recs in excluded_secs.items():
                if sec in rankable:
                    del rankable[sec]
                for rec in recs:
                    driver.exclusions.append(rec)

            driver.partial = True
            driver.partial_reasons.append("stage_a_allowed_exclusions")
            envelope["stageA"]["excluded_secondary_count"] = len(excluded_secs)
            envelope["stageA"]["remaining_rankable_secondary_count"] = len(rankable)
            envelope["stageA"]["excluded_secondaries"] = [
                {
                    "secondary": s,
                    "causes": [
                        {
                            "ticker": c["ticker"],
                            "ticker_classification": c["ticker_classification"],
                            "dependent_role": c["dependent_role"],
                        }
                        for c in recs
                    ],
                }
                for s, recs in excluded_secs.items()
            ]

        if not rankable:
            _halt_stage_a()
            return 1

    # ---- Stage Aprime barrier ----
    if "Aprime" in driver.stages:
        if lock_handle:
            update_lock_stage(lock_handle, "Aprime")
        secs = list(rankable.keys())
        report, kept, excluded = stage_aprime_rebuild(
            secs,
            cache_dir=args.cache_dir,
            price_cache_dir=args.price_cache_dir,
            write=True,
            fmt=args.price_cache_format,
        )
        for sec in excluded:
            if sec in rankable:
                del rankable[sec]
            driver.exclusions.append(
                {"secondary": sec, "stage": "Aprime", "reason": "price_cache_unbuildable"}
            )
        envelope["stageAprime"] = {
            "ran": True,
            "mode": "rebuild",
            "format": report.get("format", args.price_cache_format),
            "write_count": report.get("write_count"),
            "verification_pass_count": report.get("verification_pass_count"),
            "excluded": excluded,
        }
        if not rankable:
            envelope["halted_at"] = "Aprime"
            envelope["status"] = "failed"
            envelope["exit_code"] = 1
            return 1

    # ---- Stage B barrier ----
    if "B" in driver.stages:
        if lock_handle:
            update_lock_stage(lock_handle, "B")
        member_keys: Dict[str, str] = {}
        for sec, plan in rankable.items():
            for (ticker, _p, _r) in (plan.members or []):
                member_keys.setdefault(ticker.strip().upper(), ticker.strip())
        intervals = list(NON_DAILY_TIMEFRAMES) + [DAILY_TIMEFRAME]
        payloads = [
            {
                "member": disp,
                "intervals": intervals,
                "stable_dir": args.stable_dir,
                "cache_dir": args.cache_dir,
                "repair": bool(args.repair_invalid_daily_stable),
            }
            for disp in member_keys.values()
        ]
        b_results = _parallel_map(_stage_b_worker, payloads, args.b_workers)
        for res in b_results:
            if res.get("_captured"):
                log(res["_captured"].rstrip())
        b_failed = {
            r["member"].upper(): r for r in b_results if not r.get("ok")
        }
        b_repaired = [r["member"] for r in b_results if r.get("repaired")]
        for sec, plan in list(rankable.items()):
            needed = {
                t.strip().upper(): (t, p, raw)
                for (t, p, raw) in (plan.members or [])
            }
            hits = [k for k in needed if k in b_failed]
            if hits:
                del rankable[sec]
                for k in hits:
                    r = b_failed[k]
                    t, p, raw = needed[k]
                    driver.exclusions.append({
                        "secondary": sec,
                        "stage": "B",
                        "reason": "member_library_unavailable",
                        "ticker": t,
                        "member_token": raw,
                        "member_protocol": p,
                        "dependent_role": "member",
                        "member_errors": r.get("errors"),
                        "daily_status": r.get("daily_status"),
                        "message": (
                            f"member {t} 1d stable library invalid/unbuildable; "
                            f"excludes secondary {sec}."
                        ),
                        "action": (
                            "Rerun with --repair-invalid-daily-stable to rebuild "
                            "the 1d library offline, or drop the affected stack."
                        ),
                    })
                driver.partial = True
                if "stage_b_member_library_exclusions" not in driver.partial_reasons:
                    driver.partial_reasons.append(
                        "stage_b_member_library_exclusions"
                    )
        envelope["stageB"] = {
            "ran": True,
            "members": len(payloads),
            "repair_invalid_daily_stable": bool(args.repair_invalid_daily_stable),
            "repaired_members": sorted(b_repaired),
            "failed_members": sorted(b_failed.keys()),
            "excluded_secondary_count": sum(
                1 for e in driver.exclusions if e.get("stage") == "B"
            ),
            "workers": args.b_workers,
        }
        if not rankable:
            envelope["halted_at"] = "B"
            envelope["status"] = "failed"
            envelope["exit_code"] = 1
            return 1

    # ---- Stage E ----
    if "E" in driver.stages:
        if lock_handle:
            update_lock_stage(lock_handle, "E")
        from k6_mtf_history_producer import run as producer_run

        secs = list(rankable.keys())
        with stdout_to_stderr():
            summary = producer_run(
                secs,
                run_id=driver.driver_run_id,
                output_root=args.output_root,
                stackbuilder_root=args.stackbuilder_root,
                stable_dir=args.stable_dir,
                cache_dir=args.cache_dir,
                price_cache_dir=args.price_cache_dir,
            )
        e_failures = summary.get("failures", [])
        for f in e_failures:
            sec = f.get("secondary")
            if sec in rankable:
                del rankable[sec]
            driver.exclusions.append(
                {"secondary": sec, "stage": "E", "reason": f.get("error_class")}
            )
        envelope["stageE"] = {
            "ran": True,
            "run_id": summary.get("run_id"),
            "written": len(summary.get("written_paths", [])),
            "failed_secondaries": [f.get("secondary") for f in e_failures],
        }
        if not rankable:
            envelope["halted_at"] = "E"
            envelope["status"] = "failed"
            envelope["exit_code"] = 1
            return 1

    # ---- Stage F ----
    ranking_path = None
    if "F" in driver.stages:
        if lock_handle:
            update_lock_stage(lock_handle, "F")
        from k6_mtf_ranking_engine import run as ranking_run

        run_dir = f"{args.output_root}/{driver.driver_run_id}"
        with stdout_to_stderr():
            fsummary = ranking_run(run_dir, secondaries=list(rankable.keys()))
        ranking_path = fsummary.get("ranking_artifact_path")
        failed_records = fsummary.get("failed_records", []) or []
        envelope["stageF"] = {
            "ran": True,
            "ranking_artifact_path": ranking_path,
            "all_failed": bool(fsummary.get("all_failed")),
            "failed_records": [r.get("secondary") for r in failed_records],
        }
        if fsummary.get("all_failed") or not ranking_path:
            envelope["halted_at"] = "F"
            envelope["status"] = "failed"
            envelope["exit_code"] = 1
            return 1

    # ---- Stage H: PRIVATE DRY-RUN promote only ----
    if "H" in driver.stages and driver.args.promote_dry_run:
        if lock_handle:
            update_lock_stage(lock_handle, "H")
        blocked = bool(driver.exclusions or driver.failures)
        envelope["stageH"] = _stage_h_dry_run(
            driver, ranking_path, blocked
        )

    # Final status: partial (nonzero) if anything was excluded/dropped after
    # the chain otherwise completed through F/H. halted_at stays null because
    # the chain did complete; the partial_reasons field records why this is
    # not a clean publish candidate. Partial is for operator review, never a
    # public-automation success.
    envelope["exclusion_summary"] = _exclusion_summary(driver)
    if driver.exclusions or driver.failures:
        envelope["status"] = "partial"
        envelope["exit_code"] = 3
        reasons = list(driver.partial_reasons)
        if driver.exclusions and "stage_a_allowed_exclusions" not in reasons:
            reasons.append("excluded_secondaries_present")
        if driver.failures:
            reasons.append("failures_present")
        envelope["partial_reasons"] = reasons
        return 3
    envelope["status"] = "ok"
    envelope["exit_code"] = 0
    envelope["partial_reasons"] = []
    return 0


def _exclusion_summary(driver: Driver) -> dict:
    """Distinct top-level breakdown of why secondaries were dropped, keyed by
    stage so the four causes are never conflated."""
    by_stage: Dict[str, int] = {}
    for e in driver.exclusions:
        by_stage[e.get("stage")] = by_stage.get(e.get("stage"), 0) + 1
    return {
        "stage_a_allowed_exclusions": by_stage.get("A", 0),
        "stage_aprime_price_cache_exclusions": by_stage.get("Aprime", 0),
        "stage_b_member_library_exclusions": by_stage.get("B", 0),
        "stage_e_failures": by_stage.get("E", 0),
    }


def _stage_h_dry_run(
    driver: Driver, ranking_path: Optional[str], blocked: bool
) -> dict:
    """Run the promote helper in PRIVATE / INTERNAL DRY-RUN mode only.

    Never passes public_mode / write / operator_approved. If the helper is
    absent, report skipped_helper_missing (not a blocker)."""
    out: dict = {
        "ran": True,
        "mode": "private_internal_dry_run",
        "promotion_blocked_by_failures": blocked,
        "candidate_ranking_path": ranking_path,
    }
    if not ranking_path:
        out["status"] = "skipped_no_candidate"
        return out
    try:
        from utils.react_publish.promote_k6_mtf_artifact import (
            PromotionInputs,
            promote,
            _default_destination,
            _default_manifest_destination,
        )
    except Exception as exc:
        out["status"] = "skipped_helper_missing"
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out
    try:
        inputs = PromotionInputs(
            source_path=Path(ranking_path),
            destination_path=_default_destination(driver.project_root),
            manifest_destination_path=_default_manifest_destination(
                driver.project_root
            ),
            project_root=driver.project_root,
            public_mode=False,
            phase5_report_path=None,
            phase5_report_sha256=None,
            write=False,
            operator_approved=False,
        )
        with stdout_to_stderr():
            summary = promote(inputs)
        out["status"] = "dry_run_ok"
        out["dry_run"] = bool(summary.get("dry_run"))
        out["promote_mode"] = summary.get("mode")
        out["wrote_destination"] = bool(summary.get("wrote_destination"))
        out["per_secondary_count"] = summary.get("per_secondary_count")
    except Exception as exc:
        out["status"] = "promote_dry_run_error"
        out["detail"] = f"{type(exc).__name__}: {exc}"
    return out


if __name__ == "__main__":
    sys.exit(main())
