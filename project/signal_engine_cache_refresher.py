"""Phase 6E-3: source-data refresh probe + cache-shape builder.

The Phase 6E-2 audit established that the only path that
writes a Spymaster ``<TICKER>_precomputed_results.pkl`` today
is the Dash app (``project/spymaster.py``). Phase 6E-3 adds
the *first half* of a non-interactive operator path: a
controlled CLI that:

  - Fetches fresh OHLC price data from a pluggable data
    source (default: yfinance; mockable via the
    ``data_fetcher`` parameter so tests never hit the
    network).
  - Builds a ``preprocessed_data`` DataFrame with a ``Close``
    column plus ``SMA_1`` … ``SMA_max_sma_day`` columns —
    the same column layout Spymaster's cache uses.
  - Reports the would-be new cache date_range_end + the
    Phase-6-consistent stale_before / current_after flags.

**It is not yet a production-safe Signal Engine cache
refresher.** The Spymaster daily best-buy / best-short
SMA-pair optimizer is a closure inside the Dash callback at
``spymaster.py:5050-5117`` and has not been extracted into a
non-interactive helper. Until that work ships, every payload
this module builds carries placeholder ``active_pairs`` (the
literal string ``"None"`` repeated for each row), which would
load as ``current_signal=None``,
``current_sma_pair=None``, ``total_capture_pct=0``,
``sharpe_ratio=0``, ``signal_days=0``. Replacing a valid
Spymaster cache with such a payload would be a strict
regression for the Daily Signal Board and the Primary Signal
Engine front door — worse than the staleness it would fix.

Phase 6E-3 therefore **refuses every ``write=True`` call**
while the SMA optimizer is unavailable. The guard fires on
the payload's ``signal_engine_cache_refresher_scope`` marker
(``data_only_v1``), regardless of the operator's intent. The
issue code ``data_only_write_blocked`` is surfaced in the
result; no cache PKL, no status JSON, and no manifest sidecar
are written. The CLI keeps the ``--write`` flag so the
contract is exercised, but it is functionally a no-op until
the next sub-phase extracts the SMA optimizer.

Explicitly out of scope for Phase 6E-3 (would require a much
larger refactor of ``project/spymaster.py``):

  - Running Spymaster's full daily best-buy / best-short
    SMA-pair optimization. That logic is a closure inside
    a Dash callback (``spymaster.py:5050-5117``); reusing
    it from a CLI requires either importing the entire
    14k-line Spymaster module (which would import
    ``dash``, ``plotly``, and instantiate the Dash app
    object as a module-level side effect at line 2811) or
    refactoring Spymaster to expose the math as a
    standalone function. Neither fits in one PR.
  - Daily Signal Board styling.
  - Public web tier usage.
  - Multi-ticker mode, scheduling, or universe sweeps.

Read-only / offline contract (current Phase 6E-3 reality):

  - ``write=False`` (dry-run) fetches data but writes
    nothing. Reports the new vs old ``date_range_end``.
  - ``write=True`` ALSO writes nothing while the SMA
    optimizer is unavailable — the data_only_v1 guard
    fires, the result reports
    ``data_only_write_blocked``, and the operator is
    instructed to wait for the SMA-optimizer extraction
    sub-phase before attempting a production write.

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


# Phase 6E-3 ships the data-fetch + cache-shape build path
# only. Every payload this module produces carries the
# ``signal_engine_cache_refresher_scope`` marker
# ``DATA_ONLY_V1_SCOPE``. Writing such a payload over a real
# Spymaster cache would replace a working Signal Engine view
# with one that loads as ``current_signal=None``. The
# refresher therefore refuses ``write=True`` for any payload
# carrying this scope marker.
#
# A future sub-phase that extracts Spymaster's SMA pair
# optimizer is the gate that flips the on-disk write path
# from blocked to active.
DATA_ONLY_V1_SCOPE = "data_only_v1"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


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
# Cache shape builder
# ---------------------------------------------------------------------------


def _build_preprocessed_data(
    raw_df: pd.DataFrame, max_sma_day: int,
) -> pd.DataFrame:
    """Build the ``preprocessed_data`` DataFrame the Signal
    Engine loader expects: a single ``Close`` column plus
    ``SMA_1`` … ``SMA_max_sma_day`` columns. The index is
    datetime-coerced and de-duplicated."""
    if not isinstance(raw_df, pd.DataFrame) or raw_df.empty:
        return pd.DataFrame()
    if "Close" not in raw_df.columns:
        return pd.DataFrame()
    closes = pd.to_numeric(raw_df["Close"], errors="coerce")
    closes = closes.dropna()
    if closes.empty:
        return pd.DataFrame()
    closes.index = pd.to_datetime(closes.index, errors="coerce")
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    if closes.empty:
        return pd.DataFrame()
    out = pd.DataFrame({"Close": closes.astype(float)})
    n_days = max(0, int(max_sma_day))
    for w in range(1, n_days + 1):
        out[f"SMA_{w}"] = out["Close"].rolling(
            window=w, min_periods=1,
        ).mean()
    return out


def _build_active_pairs(n_rows: int) -> list[str]:
    """Placeholder ``active_pairs`` aligned to
    ``preprocessed_data``. Phase 6E-3 does NOT run the
    Spymaster SMA pair optimization, so the placeholder is
    the literal string ``"None"`` — honest about the
    cache's empty-signal state while still satisfying the
    loader's alignment check."""
    return ["None"] * int(n_rows)


def _build_cache_payload(
    ticker: str, preprocessed_data: pd.DataFrame,
    max_sma_day: int,
) -> dict[str, Any]:
    """Construct the dict that will be pickled to disk. The
    schema mirrors the subset of Spymaster's
    ``save_precomputed_results`` payload required by both
    ``primary_signal_engine.load_primary_signal_engine_payload``
    and the cache writer's own guard clauses (non-(0,0) pair
    sentinels)."""
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
    msd = max(2, int(max_sma_day))
    payload: dict[str, Any] = {
        "preprocessed_data": preprocessed_data,
        "active_pairs": _build_active_pairs(n_rows),
        # Self-check tokens mirror Spymaster's writer guards
        # (project/spymaster.py:4616 onward). They prevent
        # cross-ticker contamination if the cache file is
        # ever loaded by a different ticker context.
        "_ticker": ticker,
        "_row_count": n_rows,
        "_first_date": first_day,
        "_last_date": last_day,
        # Spymaster's writer rejects (0, 0) pair payloads; use
        # MAX-SMA sentinels so the writer accepts a refresh
        # that did not run the pair optimizer.
        "top_buy_pair": (msd, msd - 1),
        "top_short_pair": (msd - 1, msd),
        "top_buy_capture": 0.0,
        "top_short_capture": 0.0,
        "existing_max_sma_day": msd,
        "last_processed_date": last_day,
        "last_date": last_day,
        "start_date": first_day,
        "last_close": last_close,
        "last_price": last_close,
        "total_trading_days": n_rows,
        # Phase 6E-3 provenance: marker the operator (or a
        # future audit) can grep for to confirm a cache was
        # produced by the refresher rather than the full
        # Spymaster pipeline. The on-disk write guard keys
        # off this exact value.
        "signal_engine_cache_refresher_scope": DATA_ONLY_V1_SCOPE,
    }
    return payload


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
    status_path: Path, ticker: str, cache_status: str,
) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticker": ticker,
        "status": "complete",
        "progress": 100,
        "cache_status": cache_status,
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        "producer": "signal_engine_cache_refresher",
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
            engine_version="6E-3.0.0",
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
) -> SignalEngineRefreshResult:
    """Phase 6E-3 source-data refresh probe / cache-shape
    builder for one explicit ticker.

    ``write=False`` (default) performs the fetch + cache-shape
    build and writes nothing to disk; the result reports the
    would-be new ``date_range_end`` plus the
    ``stale_before`` / ``current_after`` flags for the
    operator.

    ``write=True`` is **currently refused** while the payload
    scope is ``data_only_v1`` — i.e. while Spymaster's SMA
    pair optimizer has not been extracted into a
    non-interactive helper. Under the guard the function
    returns ``refreshed=False`` with
    ``issue_codes=("data_only_write_blocked",)`` and **no
    cache PKL, manifest sidecar, or status JSON is written**.
    The guard releases automatically once the future
    SMA-optimizer extraction sub-phase changes the payload
    scope; the atomic write / manifest / status helpers in
    this module are already wired for that work.

    ``data_fetcher`` lets the caller (and tests) inject a
    callable that returns a price ``DataFrame``. The default
    invokes yfinance via a lazy import so test code paths
    never trigger the network.

    See module docstring for the full scope of what Phase
    6E-3 does and does NOT do.
    """
    started = time.monotonic()
    cache_d = _path_or_default(cache_dir, _default_cache_dir)
    status_d = _path_or_default(status_dir, _default_status_dir)
    msd = (
        DEFAULT_MAX_SMA_DAY
        if max_sma_day is None
        else max(2, int(max_sma_day))
    )

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
    issues: list[str] = []
    try:
        raw_df = fetcher(norm_ticker)
    except Exception:
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
        )

    preprocessed = _build_preprocessed_data(raw_df, msd)
    if preprocessed.empty:
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
        )

    new_end = _date_to_iso(preprocessed.index[-1])
    new_dt = _parse_iso_date(new_end)
    current_after = bool(
        cutoff_dt is not None and new_dt is not None
        and new_dt >= cutoff_dt
    )

    payload = _build_cache_payload(
        norm_ticker, preprocessed, msd,
    )

    if not write:
        # Dry-run path: structurally complete refresh, no
        # disk side effects.
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
        )

    # write=True path. BEFORE touching disk, enforce the
    # data_only_v1 guard. The payload built above always
    # carries placeholder ``active_pairs`` because the
    # Spymaster SMA optimizer has not been extracted into a
    # non-interactive helper yet; writing such a payload
    # would replace a valid Signal Engine cache with one
    # that loads as ``current_signal=None``. The Phase 6E-3
    # contract treats that outcome as worse than the
    # staleness it would fix, so we refuse the write.
    payload_scope = payload.get(
        "signal_engine_cache_refresher_scope",
    )
    if payload_scope == DATA_ONLY_V1_SCOPE:
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
            issue_codes=(ISSUE_DATA_ONLY_WRITE_BLOCKED,),
            elapsed_seconds=time.monotonic() - started,
        )

    # Unreachable while ``signal_engine_cache_refresher_scope``
    # is always ``DATA_ONLY_V1_SCOPE``. Preserved for the
    # future sub-phase that extracts the SMA optimizer: at
    # that point the payload carries a different scope marker
    # and the writer path becomes the active production
    # refresh path. Until then, this branch is structurally
    # dead and the atomic write helper exists only as a
    # tested primitive for that future work.
    manifest, manifest_issues = _build_manifest(payload)  # pragma: no cover
    issues.extend(manifest_issues)  # pragma: no cover

    _atomic_pickle_write(target_cache_path, payload)  # pragma: no cover

    manifest_path: Optional[Path] = None  # pragma: no cover
    if manifest is not None:  # pragma: no cover
        try:
            manifest_path = _pm.write_output_manifest(
                target_cache_path, manifest,
            )
        except Exception:
            issues.append(ISSUE_PROVENANCE_MANIFEST_FAILED)

    target_status_path = _status_path(norm_ticker, status_d)  # pragma: no cover
    _write_status(  # pragma: no cover
        target_status_path, norm_ticker,
        cache_status="fresh" if current_after else "stale",
    )

    return SignalEngineRefreshResult(  # pragma: no cover
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
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="signal_engine_cache_refresher",
        description=(
            "Phase 6E-3 source-data refresh probe / "
            "cache-shape builder for a single explicit "
            "ticker. This is NOT a production-safe Signal "
            "Engine cache refresher: --write is currently "
            "refused by the data_only_v1 guard because "
            "Spymaster's SMA pair optimizer has not been "
            "extracted into a non-interactive helper yet. "
            "Use --dry-run today; wait for the next "
            "sub-phase before any production cache write. "
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
            "Reserved for the future SMA-optimizer-backed "
            "writer. Currently refused under the "
            "data_only_v1 guard: the fetch + cache-shape "
            "build still runs, but no cache PKL, manifest "
            "sidecar, or status JSON is written and the "
            "result reports refreshed=false with "
            "issue_codes=[\"data_only_write_blocked\"]."
        ),
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--status-dir", default=None)
    parser.add_argument(
        "--max-sma-day", type=int, default=None,
        help=(
            "SMA matrix width to materialize in "
            "preprocessed_data (default 30)."
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
