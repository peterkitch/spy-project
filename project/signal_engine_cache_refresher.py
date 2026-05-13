"""Phase 6E-5: Signal Engine cache refresher (optimizer-backed).

The Phase 6E-2 audit established that the only path that
writes a Spymaster ``<TICKER>_precomputed_results.pkl`` today
is the Dash app (``project/spymaster.py``). Phase 6E-3
shipped the data-fetch + cache-shape build path with a hard
``data_only_v1`` write guard. Phase 6E-4 extracted the
Spymaster SMA-pair optimizer into a pure, offline helper at
``project/signal_engine_sma_optimizer.py``. Phase 6E-5 (this
module) wires those two pieces together so the refresher can
produce a production-safe Signal Engine cache payload:

  - Fetches fresh OHLC price data from a pluggable data
    source (default: yfinance; mockable via the
    ``data_fetcher`` parameter so tests never hit the
    network).
  - Calls ``signal_engine_sma_optimizer.optimize_signal_engine_sma_pairs``
    to compute real ``daily_top_buy_pairs`` /
    ``daily_top_short_pairs`` / ``cumulative_combined_captures``
    / ``active_pairs`` and the headline ``top_buy_pair`` /
    ``top_short_pair`` fields. The math is byte-equivalent to
    the SPY parity baseline pinned in
    ``test_scripts/test_signal_engine_sma_optimizer.py``.
  - Stamps the payload with
    ``signal_engine_cache_refresher_scope = "optimizer_v1"``
    and writes it atomically with a provenance manifest +
    status JSON (``write=True``).

**The data_only_v1 write guard is preserved.** A payload
that arrives at the write check still carrying the
``data_only_v1`` scope is refused with
``issue_codes=("data_only_write_blocked",)``. The guard now
acts as a defensive check rather than a primary block,
because the refresher's happy path emits ``optimizer_v1``
payloads. Any future code path that would re-introduce
placeholder ``active_pairs`` still gets caught.

Strictly offline by default in tests:

  - No ``spymaster``, ``dash``, ``plotly``, or
    ``daily_signal_board`` import.
  - ``yfinance`` is only imported lazily inside the default
    fetcher, so tests that supply their own callable never
    trigger the network.
  - Single-ticker only; no universe sweep.

Read-only contract:

  - ``write=False`` (dry-run) fetches data, runs the
    optimizer in memory, and writes nothing.
  - ``write=True`` writes ONLY when the payload scope is
    ``optimizer_v1`` AND the optimizer returned no issue
    codes. Otherwise the request is refused with a stable
    issue code and no disk side effects.

Public surface
--------------

    ISSUE_*                                        # str constants
    SignalEngineRefreshResult                      # dataclass

    refresh_signal_engine_cache(ticker, *, ...)
        -> SignalEngineRefreshResult
    main(argv=None) -> int                         # CLI entry point

CLI examples
------------

    python signal_engine_cache_refresher.py --ticker SPY --dry-run
    python signal_engine_cache_refresher.py --ticker SPY --write

Exit codes:

    0  refresh completed (dry-run or write); JSON emitted
    2  invalid CLI arguments
    3  unexpected unhandled exception
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import numpy as np
import pandas as pd

import confluence_pipeline_readiness as _cpr
import primary_signal_engine as _pse
import provenance_manifest as _pm
import signal_engine_sma_optimizer as _seo


# ---------------------------------------------------------------------------
# Issue codes
# ---------------------------------------------------------------------------

ISSUE_INVALID_TICKER = "invalid_ticker"
ISSUE_DATA_FETCH_FAILED = "data_fetch_failed"
ISSUE_DATA_NO_CLOSE_COLUMN = "data_no_close_column"
ISSUE_DATA_EMPTY = "data_empty"
ISSUE_DRY_RUN = "dry_run_only"
ISSUE_ALREADY_CURRENT = "already_current"
ISSUE_PROVENANCE_MANIFEST_FAILED = "provenance_manifest_failed"
ISSUE_DATA_ONLY_WRITE_BLOCKED = "data_only_write_blocked"
ISSUE_OPTIMIZER_FAILED = "optimizer_failed"
# Surfaced when an explicit ``max_sma_day`` argument is
# unparseable or < 2. Named to match the optimizer's own
# ``invalid_max_sma_day`` so the launch-readiness stack
# can switch on a single stable string. Distinct from the
# CLI's argparse-level rc=2 path, which still rejects
# bad ``--max-sma-day`` before the function is even
# called.
ISSUE_INVALID_MAX_SMA_DAY = "invalid_max_sma_day"


# Scope markers stamped onto every cache payload this module
# builds. The ``signal_engine_cache_refresher_scope`` field
# is the authority the write guard keys off:
#
#   - ``DATA_ONLY_V1_SCOPE`` ("data_only_v1") was the Phase
#     6E-3 placeholder. Payloads carrying this scope have
#     ``active_pairs = ["None", ...]`` and load as
#     ``current_signal=None``. The guard refuses every
#     ``write=True`` call that produces this scope.
#   - ``OPTIMIZER_V1_SCOPE`` ("optimizer_v1") is the Phase
#     6E-5 happy path. Payloads carrying this scope contain
#     real Spymaster-equivalent SMA-pair optimization output
#     from ``signal_engine_sma_optimizer``. Only these
#     payloads reach the atomic write branch.
DATA_ONLY_V1_SCOPE = "data_only_v1"
OPTIMIZER_V1_SCOPE = "optimizer_v1"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


DEFAULT_PROVIDER_NAME = "yfinance"


@dataclass
class ProviderFetchTelemetry:
    """Phase 6I-12: fetch-attempt/result telemetry for the
    source-data fetcher invocation.

    This is **fetch-call telemetry**, not HTTP / wire-level
    telemetry. It records whether the refresher entered the
    fetcher call, whether the call returned cleanly, the
    shape of the returned data (row count + index date range
    when available), the elapsed time **inside the fetcher
    callable**, the provider name, and the short error class
    name when the call raised. It does NOT capture HTTP
    status codes, request/response identifiers, or any
    provider-side telemetry that lives below the
    ``data_fetcher`` callable boundary.

    Populated when the refresher actually invokes the
    ``data_fetcher`` callable. Refresh paths that exit before
    the fetcher call (invalid ticker, invalid ``max_sma_day``)
    do not produce telemetry; ``provider_fetch_telemetry`` is
    ``None`` on those results.
    """

    provider_name: str
    fetch_attempted: bool
    fetch_succeeded: bool
    ticker: str
    rows: int
    date_range_start: Optional[str]
    date_range_end: Optional[str]
    elapsed_seconds: float
    error: Optional[str]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "provider_name": str(self.provider_name),
            "fetch_attempted": bool(self.fetch_attempted),
            "fetch_succeeded": bool(self.fetch_succeeded),
            "ticker": str(self.ticker),
            "rows": int(self.rows),
            "date_range_start": self.date_range_start,
            "date_range_end": self.date_range_end,
            "elapsed_seconds": float(self.elapsed_seconds),
            "error": self.error,
        }


@dataclass
class SignalEngineRefreshResult:
    """One refresh attempt's outcome. ``refreshed`` is True
    only when ``write=True`` AND the writer actually replaced
    the cache file on disk."""

    ticker: str
    write: bool
    cache_path: Optional[str]
    manifest_path: Optional[str]
    status_path: Optional[str]
    old_cache_date_range_end: Optional[str]
    new_cache_date_range_end: Optional[str]
    refreshed: bool
    stale_before: bool
    current_after: bool
    issue_codes: tuple[str, ...]
    elapsed_seconds: float
    # Phase 6I-12 additive field. ``None`` on refresh paths
    # that never entered the fetcher call (invalid ticker /
    # invalid max_sma_day); a populated
    # ``ProviderFetchTelemetry`` on every path that reached
    # the fetcher invocation, including the fetcher-
    # exception path. Existing fields are unchanged.
    provider_fetch_telemetry: Optional[ProviderFetchTelemetry] = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "write": bool(self.write),
            "cache_path": self.cache_path,
            "manifest_path": self.manifest_path,
            "status_path": self.status_path,
            "old_cache_date_range_end": self.old_cache_date_range_end,
            "new_cache_date_range_end": self.new_cache_date_range_end,
            "refreshed": bool(self.refreshed),
            "stale_before": bool(self.stale_before),
            "current_after": bool(self.current_after),
            "issue_codes": list(self.issue_codes),
            "elapsed_seconds": float(self.elapsed_seconds),
            "provider_fetch_telemetry": (
                self.provider_fetch_telemetry.to_json_dict()
                if self.provider_fetch_telemetry is not None
                else None
            ),
        }


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _default_status_dir() -> Path:
    return _project_dir() / "cache" / "status"


def _path_or_default(value: Any, default_fn) -> Path:
    if value is None:
        return default_fn()
    return Path(value)


def _normalize_ticker(ticker: str) -> str:
    """Light-touch ticker normalization. Mirrors Spymaster's
    convention (``project/spymaster.py:3948``) for the leading
    ``^`` rewrite that determines the on-disk filename stem,
    but keeps the in-payload ``_ticker`` field as the original
    symbol so the writer's contamination guard is satisfied."""
    return str(ticker or "").strip().upper()


def _filename_stem(ticker: str) -> str:
    return ticker.replace("^", "_")


def _is_valid_ticker(ticker: str) -> bool:
    """Stable validation rule for the operator CLI. Tickers
    are uppercase, may include ``^`` (e.g. ``^GSPC``),
    digits, ``.``, ``-``, and ``=`` (FX / futures). Anything
    else is rejected with ``ISSUE_INVALID_TICKER``."""
    if not ticker:
        return False
    return bool(re.fullmatch(r"[A-Z0-9.\-^=]+", ticker))


def _cache_path(ticker: str, cache_dir: Path) -> Path:
    return cache_dir / f"{_filename_stem(ticker)}_precomputed_results.pkl"


def _manifest_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(cache_path.suffix + ".manifest.json")


def _status_path(ticker: str, status_dir: Path) -> Path:
    return status_dir / f"{_filename_stem(ticker)}_status.json"


# ---------------------------------------------------------------------------
# Default yfinance-backed fetcher (mockable via the
# ``data_fetcher`` parameter; only imported when actually
# called so tests that supply a mock never trip the import).
# ---------------------------------------------------------------------------


def _default_yfinance_fetcher(ticker: str) -> pd.DataFrame:
    """Real-network fetcher. Tests must supply their own
    callable via ``data_fetcher`` to avoid the network."""
    import yfinance as yf  # lazy; never imported by tests
    df = yf.download(
        ticker,
        period="max",
        auto_adjust=False,
        progress=False,
    )
    if isinstance(df, pd.DataFrame) and not df.empty:
        # yfinance returns a MultiIndex columns frame when a
        # single ticker is passed under newer versions; flatten
        # to the simple shape the rest of this module expects.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
    return df


# ---------------------------------------------------------------------------
# Cache payload builders
# ---------------------------------------------------------------------------


def _cache_payload_common(
    ticker: str,
    preprocessed_data: pd.DataFrame,
    *,
    top_buy_pair: tuple[int, int],
    top_short_pair: tuple[int, int],
    top_buy_capture: float,
    top_short_capture: float,
    active_pairs: list[str],
    cumulative_combined_captures: Optional[pd.Series],
    daily_top_buy_pairs: dict,
    daily_top_short_pairs: dict,
    existing_max_sma_day: int,
    last_processed_date: Any,
    scope: str,
) -> dict[str, Any]:
    n_rows = len(preprocessed_data.index)
    last_day = (
        preprocessed_data.index[-1] if n_rows else None
    )
    first_day = (
        preprocessed_data.index[0] if n_rows else None
    )
    last_close = (
        float(preprocessed_data["Close"].iloc[-1])
        if n_rows else None
    )
    return {
        "preprocessed_data": preprocessed_data,
        "active_pairs": list(active_pairs),
        "cumulative_combined_captures": (
            cumulative_combined_captures
        ),
        "daily_top_buy_pairs": dict(daily_top_buy_pairs),
        "daily_top_short_pairs": dict(daily_top_short_pairs),
        # Self-check tokens mirror Spymaster's writer guards
        # (project/spymaster.py:4616 onward). They prevent
        # cross-ticker contamination if the cache file is
        # ever loaded by a different ticker context.
        "_ticker": ticker,
        "_row_count": n_rows,
        "_first_date": first_day,
        "_last_date": last_day,
        "top_buy_pair": top_buy_pair,
        "top_short_pair": top_short_pair,
        "top_buy_capture": float(top_buy_capture),
        "top_short_capture": float(top_short_capture),
        "existing_max_sma_day": int(existing_max_sma_day),
        "last_processed_date": last_processed_date,
        "last_date": last_day,
        "start_date": first_day,
        "last_close": last_close,
        "last_price": last_close,
        "total_trading_days": n_rows,
        # The scope marker the on-disk write guard keys off.
        # Phase 6E-5 only writes payloads stamped
        # ``OPTIMIZER_V1_SCOPE``.
        "signal_engine_cache_refresher_scope": scope,
    }


def _build_optimizer_v1_payload(
    ticker: str,
    opt_result: _seo.SignalEngineSmaOptimizationResult,
) -> dict[str, Any]:
    """Build a production-safe cache payload from an
    ``optimize_signal_engine_sma_pairs`` result. Stamps
    ``OPTIMIZER_V1_SCOPE`` so the write guard lets it
    through."""
    return _cache_payload_common(
        ticker=ticker,
        preprocessed_data=opt_result.preprocessed_data,
        top_buy_pair=opt_result.top_buy_pair or (
            opt_result.existing_max_sma_day,
            opt_result.existing_max_sma_day - 1,
        ),
        top_short_pair=opt_result.top_short_pair or (
            opt_result.existing_max_sma_day - 1,
            opt_result.existing_max_sma_day,
        ),
        top_buy_capture=opt_result.top_buy_capture,
        top_short_capture=opt_result.top_short_capture,
        active_pairs=list(opt_result.active_pairs),
        cumulative_combined_captures=(
            opt_result.cumulative_combined_captures
        ),
        daily_top_buy_pairs=opt_result.daily_top_buy_pairs,
        daily_top_short_pairs=opt_result.daily_top_short_pairs,
        existing_max_sma_day=opt_result.existing_max_sma_day,
        last_processed_date=opt_result.last_processed_date,
        scope=OPTIMIZER_V1_SCOPE,
    )


def _build_data_only_v1_payload(
    ticker: str,
    preprocessed_data: pd.DataFrame,
    max_sma_day: int,
) -> dict[str, Any]:
    """Build a Phase 6E-3-style ``data_only_v1`` payload.

    Retained so the write guard's "data_only_v1 still
    blocked" behavior can be exercised by tests, and as a
    defensive fallback if a future code path tries to skip
    the optimizer. The refresher's production happy path
    no longer reaches this helper — it goes through
    ``_build_optimizer_v1_payload``.
    """
    n_rows = len(preprocessed_data.index)
    last_day = (
        preprocessed_data.index[-1] if n_rows else None
    )
    msd = max(2, int(max_sma_day))
    return _cache_payload_common(
        ticker=ticker,
        preprocessed_data=preprocessed_data,
        top_buy_pair=(msd, msd - 1),
        top_short_pair=(msd - 1, msd),
        top_buy_capture=0.0,
        top_short_capture=0.0,
        active_pairs=["None"] * n_rows,
        cumulative_combined_captures=None,
        daily_top_buy_pairs={},
        daily_top_short_pairs={},
        existing_max_sma_day=msd,
        last_processed_date=last_day,
        scope=DATA_ONLY_V1_SCOPE,
    )


# ---------------------------------------------------------------------------
# Atomic write (replicates Spymaster's writer semantics)
# ---------------------------------------------------------------------------


def _atomic_pickle_write(
    target: Path, payload: dict[str, Any],
) -> None:
    """Spymaster's atomic-write recipe in miniature:
    tempfile.NamedTemporaryFile in the same directory ->
    pickle.dump -> flush -> os.fsync -> os.replace. This
    matches the semantics described at
    ``project/spymaster.py:4675-4680``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".pkl",
        dir=str(target.parent),
    ) as tf:
        pickle.dump(payload, tf, protocol=pickle.HIGHEST_PROTOCOL)
        tf.flush()
        os.fsync(tf.fileno())
        temp_name = tf.name
    os.replace(temp_name, str(target))


def _write_status(
    status_path: Path,
    ticker: str,
    cache_status: str,
    provider_fetch_telemetry: Optional[ProviderFetchTelemetry] = None,
) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    # Phase 6I-12: additive ``provider_fetch_telemetry``
    # key on the status JSON. Carries the same JSON shape
    # the refresher result + writer stdout / JSONL row
    # already carry; ``null`` when the status was written
    # before the fetcher call could be observed. The new
    # key is appended after the existing fields so any
    # downstream consumer that ignores unknown keys keeps
    # working.
    payload = {
        "ticker": ticker,
        "status": "complete",
        "progress": 100,
        "cache_status": cache_status,
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        "producer": "signal_engine_cache_refresher",
        "provider_fetch_telemetry": (
            provider_fetch_telemetry.to_json_dict()
            if provider_fetch_telemetry is not None
            else None
        ),
    }
    tmp = status_path.with_suffix(status_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(status_path))


def _build_manifest(
    payload: dict[str, Any],
) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Build the logical manifest and embed it into the
    payload BEFORE the cache pickle is finalized (so the
    embedded ``_manifest`` is identical to what the sidecar
    will describe). The sidecar is written AFTER the cache
    file is on disk so its ``artifact_file_sha256`` reflects
    the final bytes."""
    issues: list[str] = []
    try:
        manifest = _pm.build_output_manifest(
            artifact_type="spymaster_precomputed_results",
            producer_engine="signal_engine_cache_refresher",
            engine_version="6E-5.0.0",
            params={
                "ticker": payload.get("_ticker"),
                "max_sma_day": payload.get("existing_max_sma_day"),
                "price_source": "Close",
                "scope": payload.get(
                    "signal_engine_cache_refresher_scope",
                ),
            },
            content_obj=payload,
            artifact_kind=_pm.ARTIFACT_KIND_OUTPUT,
        )
    except Exception:
        issues.append(ISSUE_PROVENANCE_MANIFEST_FAILED)
        return None, issues

    payload[_pm.MANIFEST_FIELD] = manifest
    return manifest, issues


# ---------------------------------------------------------------------------
# Staleness probe
# ---------------------------------------------------------------------------


def _existing_cache_end(
    ticker: str, cache_dir: Path,
) -> Optional[str]:
    """Reuse the Signal Engine loader to read the existing
    cache's ``date_range.end``. Returns ``None`` when no
    cache is present or readable."""
    try:
        payload = _pse.load_primary_signal_engine_payload(
            ticker, cache_dir=cache_dir,
        )
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not payload.get("available"):
        return None
    dr = payload.get("date_range") or {}
    end = dr.get("end") if isinstance(dr, dict) else None
    return str(end) if end else None


def _existing_cache_max_sma_day(
    ticker: str, cache_dir: Path,
) -> Optional[int]:
    """Read the existing cache's ``existing_max_sma_day``
    field directly so a refresh doesn't silently downgrade
    a 114-wide SPY cache to the 30-wide module default.

    Returns ``None`` when no cache is present / readable or
    when the field is missing / unparseable / < 2. Reuses
    the Signal Engine loader's ticker resolution so case
    quirks and ``^``-prefix tickers work the same as
    everywhere else in the refresh stack."""
    path = _cache_pse_resolved_path(ticker, cache_dir)
    if path is None or not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            obj = pickle.load(fh)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    val = obj.get("existing_max_sma_day")
    try:
        ival = int(val)
    except Exception:
        return None
    if ival < 2:
        return None
    return ival


def _cache_pse_resolved_path(
    ticker: str, cache_dir: Path,
) -> Optional[Path]:
    """Resolve the on-disk cache path for ``ticker`` via the
    Signal Engine loader's helper. Returns ``None`` when no
    candidate file exists."""
    try:
        return _pse._resolve_cache_path(  # type: ignore[attr-defined]
            ticker, cache_dir,
        )
    except Exception:
        return _cache_path(ticker, cache_dir)


def _parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except Exception:
        return None


def _date_to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        ts = pd.to_datetime(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(ts):
        return None
    return ts.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public refresh entry point
# ---------------------------------------------------------------------------


DEFAULT_MAX_SMA_DAY = 30


def refresh_signal_engine_cache(
    ticker: str,
    *,
    cache_dir: Optional[Path] = None,
    status_dir: Optional[Path] = None,
    write: bool = False,
    max_sma_day: Optional[int] = None,
    data_fetcher: Optional[Callable[[str], pd.DataFrame]] = None,
    current_as_of_date: Optional[str] = None,
    provider_name: Optional[str] = None,
) -> SignalEngineRefreshResult:
    """Phase 6E-5 Signal Engine cache refresher for one
    explicit ticker.

    ``write=False`` (default) performs the fetch + SMA-pair
    optimization in memory and writes nothing to disk; the
    result reports the would-be new ``date_range_end`` plus
    the ``stale_before`` / ``current_after`` flags.

    ``write=True`` writes the optimizer-backed cache PKL,
    its provenance manifest sidecar, and the status JSON —
    but ONLY when the payload scope is
    ``OPTIMIZER_V1_SCOPE`` and the optimizer returned no
    issue codes. A ``data_only_v1`` payload that somehow
    arrives at the write check is still refused with
    ``issue_codes=("data_only_write_blocked",)``; an
    optimizer failure is reported via
    ``issue_codes=("optimizer_failed", <optimizer codes>)``
    and produces no disk side effects.

    ``data_fetcher`` lets the caller (and tests) inject a
    callable that returns a price ``DataFrame``. The default
    invokes yfinance via a lazy import so test code paths
    never trigger the network.

    ``max_sma_day`` default behavior: if an existing cache
    is present for ``ticker`` and exposes a usable
    ``existing_max_sma_day`` field, the refresher reuses
    that value so a refresh never silently downgrades a
    114-wide cache to the module default of 30. An explicit
    ``max_sma_day`` argument (or the ``--max-sma-day`` CLI
    flag) overrides both, as long as it is ``>= 2``.

    See module docstring for the full scope of what Phase
    6E-5 does and does NOT do.
    """
    started = time.monotonic()
    cache_d = _path_or_default(cache_dir, _default_cache_dir)
    status_d = _path_or_default(status_dir, _default_status_dir)

    norm_ticker = _normalize_ticker(ticker)
    if not _is_valid_ticker(norm_ticker):
        return SignalEngineRefreshResult(
            ticker=norm_ticker,
            write=bool(write),
            cache_path=None,
            manifest_path=None,
            status_path=None,
            old_cache_date_range_end=None,
            new_cache_date_range_end=None,
            refreshed=False,
            stale_before=False,
            current_after=False,
            issue_codes=(ISSUE_INVALID_TICKER,),
            elapsed_seconds=time.monotonic() - started,
        )

    # Resolve ``max_sma_day`` AFTER ticker normalization so
    # the existing-cache probe runs against the canonical
    # filename stem. Explicit invalid values are rejected
    # (never silently clamped); the absent / None case
    # reuses the existing cache's ``existing_max_sma_day``
    # when present, or falls back to ``DEFAULT_MAX_SMA_DAY``.
    if max_sma_day is None:
        existing_msd = _existing_cache_max_sma_day(
            norm_ticker, cache_d,
        )
        msd = (
            existing_msd
            if existing_msd is not None
            else DEFAULT_MAX_SMA_DAY
        )
    else:
        try:
            msd_candidate = int(max_sma_day)
        except (TypeError, ValueError):
            return SignalEngineRefreshResult(
                ticker=norm_ticker,
                write=bool(write),
                cache_path=None,
                manifest_path=None,
                status_path=None,
                old_cache_date_range_end=None,
                new_cache_date_range_end=None,
                refreshed=False,
                stale_before=False,
                current_after=False,
                issue_codes=(ISSUE_INVALID_MAX_SMA_DAY,),
                elapsed_seconds=time.monotonic() - started,
            )
        if msd_candidate < 2:
            return SignalEngineRefreshResult(
                ticker=norm_ticker,
                write=bool(write),
                cache_path=None,
                manifest_path=None,
                status_path=None,
                old_cache_date_range_end=None,
                new_cache_date_range_end=None,
                refreshed=False,
                stale_before=False,
                current_after=False,
                issue_codes=(ISSUE_INVALID_MAX_SMA_DAY,),
                elapsed_seconds=time.monotonic() - started,
            )
        msd = msd_candidate

    target_cache_path = _cache_path(norm_ticker, cache_d)
    old_end = _existing_cache_end(norm_ticker, cache_d)

    # Use the same most-recent-weekday cutoff resolver the
    # Phase 6 readiness / preflight tools use, so Phase 6E-3
    # never drifts from the rest of the launch-readiness
    # stack. ``resolve_current_as_of_date`` accepts an
    # explicit override; absent that it falls back to the
    # most recent weekday strictly before UTC now.
    cutoff_iso = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    cutoff_dt = _parse_iso_date(cutoff_iso)
    old_dt = _parse_iso_date(old_end)
    stale_before = bool(
        cutoff_dt is not None and old_dt is not None
        and old_dt < cutoff_dt
    )

    fetcher = data_fetcher or _default_yfinance_fetcher
    # Phase 6I-12: derive provider_name. Explicit kwarg wins;
    # else inspect fetcher attribute; else "yfinance" when
    # using the default fetcher; else "custom_callable".
    if provider_name is not None:
        resolved_provider_name = str(provider_name)
    elif data_fetcher is None:
        resolved_provider_name = DEFAULT_PROVIDER_NAME
    else:
        resolved_provider_name = str(
            getattr(
                data_fetcher,
                "PROVIDER_NAME",
                "custom_callable",
            )
        )

    issues: list[str] = []
    fetch_started = time.monotonic()
    try:
        raw_df = fetcher(norm_ticker)
    except Exception as exc:
        fetch_elapsed = time.monotonic() - fetch_started
        telemetry = ProviderFetchTelemetry(
            provider_name=resolved_provider_name,
            fetch_attempted=True,
            fetch_succeeded=False,
            ticker=norm_ticker,
            rows=0,
            date_range_start=None,
            date_range_end=None,
            elapsed_seconds=fetch_elapsed,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        return SignalEngineRefreshResult(
            ticker=norm_ticker,
            write=bool(write),
            cache_path=str(target_cache_path),
            manifest_path=None,
            status_path=None,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=None,
            refreshed=False,
            stale_before=stale_before,
            current_after=False,
            issue_codes=(ISSUE_DATA_FETCH_FAILED,),
            elapsed_seconds=time.monotonic() - started,
            provider_fetch_telemetry=telemetry,
        )
    fetch_elapsed = time.monotonic() - fetch_started

    # Telemetry shape for the post-call branch. ``rows`` and
    # the index date range are populated only when the call
    # returned a non-empty DataFrame; in that case we treat
    # the fetcher itself as having succeeded (downstream
    # contract checks like ``ISSUE_DATA_NO_CLOSE_COLUMN``
    # still apply on the refresh result, but they are not
    # the fetcher's fault).
    fetch_rows = 0
    fetch_start_date: Optional[str] = None
    fetch_end_date: Optional[str] = None
    fetcher_returned_usable_df = (
        isinstance(raw_df, pd.DataFrame) and not raw_df.empty
    )
    if fetcher_returned_usable_df:
        fetch_rows = int(len(raw_df.index))
        try:
            fetch_start_date = _date_to_iso(raw_df.index[0])
            fetch_end_date = _date_to_iso(raw_df.index[-1])
        except Exception:
            fetch_start_date = None
            fetch_end_date = None
    telemetry = ProviderFetchTelemetry(
        provider_name=resolved_provider_name,
        fetch_attempted=True,
        fetch_succeeded=bool(fetcher_returned_usable_df),
        ticker=norm_ticker,
        rows=fetch_rows,
        date_range_start=fetch_start_date,
        date_range_end=fetch_end_date,
        elapsed_seconds=fetch_elapsed,
        error=None,
    )

    if not isinstance(raw_df, pd.DataFrame) or raw_df.empty:
        return SignalEngineRefreshResult(
            ticker=norm_ticker,
            write=bool(write),
            cache_path=str(target_cache_path),
            manifest_path=None,
            status_path=None,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=None,
            refreshed=False,
            stale_before=stale_before,
            current_after=False,
            issue_codes=(ISSUE_DATA_EMPTY,),
            elapsed_seconds=time.monotonic() - started,
            provider_fetch_telemetry=telemetry,
        )

    if "Close" not in raw_df.columns:
        return SignalEngineRefreshResult(
            ticker=norm_ticker,
            write=bool(write),
            cache_path=str(target_cache_path),
            manifest_path=None,
            status_path=None,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=None,
            refreshed=False,
            stale_before=stale_before,
            current_after=False,
            issue_codes=(ISSUE_DATA_NO_CLOSE_COLUMN,),
            elapsed_seconds=time.monotonic() - started,
            provider_fetch_telemetry=telemetry,
        )

    # Run the Phase 6E-4 SMA-pair optimizer over the
    # fetched data. The optimizer is offline / pure and
    # builds preprocessed_data + SMA columns internally; the
    # refresher trusts it to validate the input shape and
    # returns its issue codes verbatim alongside
    # ``optimizer_failed``.
    opt_result = _seo.optimize_signal_engine_sma_pairs(
        raw_df, ticker=norm_ticker, max_sma_day=msd,
    )
    if opt_result.issue_codes:
        return SignalEngineRefreshResult(
            ticker=norm_ticker,
            write=bool(write),
            cache_path=str(target_cache_path),
            manifest_path=None,
            status_path=None,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=None,
            refreshed=False,
            stale_before=stale_before,
            current_after=False,
            issue_codes=(
                ISSUE_OPTIMIZER_FAILED,
                *opt_result.issue_codes,
            ),
            elapsed_seconds=time.monotonic() - started,
            provider_fetch_telemetry=telemetry,
        )

    preprocessed = opt_result.preprocessed_data
    new_end = _date_to_iso(preprocessed.index[-1])
    new_dt = _parse_iso_date(new_end)
    current_after = bool(
        cutoff_dt is not None and new_dt is not None
        and new_dt >= cutoff_dt
    )

    payload = _build_optimizer_v1_payload(
        norm_ticker, opt_result,
    )

    if not write:
        # Dry-run path: structurally complete refresh +
        # optimizer pass, no disk side effects.
        return SignalEngineRefreshResult(
            ticker=norm_ticker,
            write=False,
            cache_path=str(target_cache_path),
            manifest_path=None,
            status_path=None,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=new_end,
            refreshed=False,
            stale_before=stale_before,
            current_after=current_after,
            issue_codes=(ISSUE_DRY_RUN,),
            elapsed_seconds=time.monotonic() - started,
            provider_fetch_telemetry=telemetry,
        )

    # write=True path. The defensive scope check refuses
    # any payload that did not come through the optimizer.
    # The Phase 6E-3 ``data_only_v1`` guard remains in
    # force — only ``optimizer_v1`` payloads land on disk.
    return _write_optimizer_payload_or_block(
        norm_ticker=norm_ticker,
        payload=payload,
        target_cache_path=target_cache_path,
        status_dir=status_d,
        old_end=old_end,
        new_end=new_end,
        stale_before=stale_before,
        current_after=current_after,
        started=started,
        provider_fetch_telemetry=telemetry,
    )


def _write_optimizer_payload_or_block(
    *,
    norm_ticker: str,
    payload: dict[str, Any],
    target_cache_path: Path,
    status_dir: Path,
    old_end: Optional[str],
    new_end: Optional[str],
    stale_before: bool,
    current_after: bool,
    started: float,
    provider_fetch_telemetry: Optional[ProviderFetchTelemetry] = None,
) -> SignalEngineRefreshResult:
    """Write guard + atomic writer. Refuses every payload
    that is not stamped ``OPTIMIZER_V1_SCOPE``; for the
    happy path, writes the cache PKL atomically, then the
    manifest sidecar, then the status JSON. ``refreshed``
    flips True only after the cache file is on disk."""
    payload_scope = payload.get(
        "signal_engine_cache_refresher_scope",
    )
    if payload_scope != OPTIMIZER_V1_SCOPE:
        # Defensive guard. The Phase 6E-3 ``data_only_v1``
        # scope is still explicitly blocked; any other
        # unexpected scope is also blocked.
        block_code = (
            ISSUE_DATA_ONLY_WRITE_BLOCKED
            if payload_scope == DATA_ONLY_V1_SCOPE
            else ISSUE_DATA_ONLY_WRITE_BLOCKED
        )
        return SignalEngineRefreshResult(
            ticker=norm_ticker,
            write=True,
            cache_path=str(target_cache_path),
            manifest_path=None,
            status_path=None,
            old_cache_date_range_end=old_end,
            new_cache_date_range_end=new_end,
            refreshed=False,
            stale_before=stale_before,
            current_after=current_after,
            issue_codes=(block_code,),
            elapsed_seconds=time.monotonic() - started,
            provider_fetch_telemetry=provider_fetch_telemetry,
        )

    issues: list[str] = []
    # Build + embed the logical manifest BEFORE the cache
    # pickle is finalized so the embedded ``_manifest`` is
    # identical to what the sidecar will describe. The
    # sidecar is written AFTER the cache file lands so its
    # ``artifact_file_sha256`` reflects the final bytes.
    manifest, manifest_issues = _build_manifest(payload)
    issues.extend(manifest_issues)

    _atomic_pickle_write(target_cache_path, payload)

    manifest_path: Optional[Path] = None
    if manifest is not None:
        try:
            manifest_path = _pm.write_output_manifest(
                target_cache_path, manifest,
            )
        except Exception:
            issues.append(ISSUE_PROVENANCE_MANIFEST_FAILED)

    target_status_path = _status_path(norm_ticker, status_dir)
    _write_status(
        target_status_path, norm_ticker,
        cache_status="fresh" if current_after else "stale",
        provider_fetch_telemetry=provider_fetch_telemetry,
    )

    return SignalEngineRefreshResult(
        ticker=norm_ticker,
        write=True,
        cache_path=str(target_cache_path),
        manifest_path=(
            str(manifest_path) if manifest_path else None
        ),
        status_path=str(target_status_path),
        old_cache_date_range_end=old_end,
        new_cache_date_range_end=new_end,
        refreshed=True,
        stale_before=stale_before,
        current_after=current_after,
        issue_codes=tuple(issues),
        elapsed_seconds=time.monotonic() - started,
        provider_fetch_telemetry=provider_fetch_telemetry,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal_engine_cache_refresher",
        description=(
            "Phase 6E-5 Signal Engine cache refresher for a "
            "single explicit ticker. Default is dry-run; "
            "--write must be explicit. Calls the Phase 6E-4 "
            "SMA-pair optimizer to produce real "
            "active_pairs and headline buy/short pair "
            "fields, then writes an optimizer_v1 cache PKL "
            "+ manifest sidecar + status JSON atomically. "
            "Never runs a universe sweep."
        ),
    )
    parser.add_argument(
        "--ticker",
        required=True,
        help="Single ticker symbol (required; no multi-ticker mode).",
    )
    write_group = parser.add_mutually_exclusive_group()
    write_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Default. Fetch + structurally validate; no disk writes.",
    )
    write_group.add_argument(
        "--write",
        action="store_true",
        help=(
            "Run the optimizer and write the resulting "
            "optimizer_v1 cache PKL + manifest sidecar + "
            "status JSON atomically. The legacy "
            "data_only_v1 scope remains explicitly "
            "blocked; an optimizer failure also produces "
            "no writes."
        ),
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--status-dir", default=None)
    parser.add_argument(
        "--max-sma-day", type=int, default=None,
        help=(
            "SMA matrix width to materialize in "
            "preprocessed_data. Default reuses the "
            "existing cache's existing_max_sma_day field "
            "when present so a refresh does not silently "
            "downgrade a 114-wide cache; otherwise falls "
            "back to DEFAULT_MAX_SMA_DAY (30). Must be "
            ">= 2."
        ),
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
        help=(
            "Override the cutoff used for stale_before / "
            "current_after. Default comes from "
            "confluence_pipeline_readiness."
            "resolve_current_as_of_date (the most recent "
            "weekday strictly before UTC now), so the "
            "refresher stays aligned with the rest of the "
            "Phase 6 readiness / preflight stack."
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

    if args.max_sma_day is not None and args.max_sma_day < 2:
        sys.stderr.write(
            "signal_engine_cache_refresher: error: "
            "--max-sma-day must be >= 2\n"
        )
        return 2

    write_flag = bool(args.write)

    try:
        result = refresh_signal_engine_cache(
            args.ticker,
            cache_dir=args.cache_dir,
            status_dir=args.status_dir,
            write=write_flag,
            max_sma_day=args.max_sma_day,
            current_as_of_date=args.current_as_of_date,
        )
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(
            "signal_engine_cache_refresher: unhandled error: "
            f"{exc!r}\n"
        )
        return 3

    json.dump(result.to_json_dict(), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - tests cover main
    sys.exit(main())
