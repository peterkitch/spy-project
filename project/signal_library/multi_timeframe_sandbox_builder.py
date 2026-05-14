#!/usr/bin/env python3
"""Phase 6I-30 sandbox-only multi-timeframe signal library builder.

Reads OHLCV from the local Spymaster precomputed-results cache
(``cache/results/<TICKER>_precomputed_results.pkl``) via the central
provenance loader, resamples to each requested interval inside this
builder (the calendar contract that owns interval construction), and
produces interval signal libraries with a **native** per-interval
``close`` series alongside the existing ``dates`` / ``signals``.

The Phase 6I-30 production change in ``multi_timeframe_builder`` adds
the ``close`` field to every persisted library; this sandbox builder
is the proof harness: it exists so the Phase 6I-30 60-cell readiness
proof can be run **without** yfinance, against an explicit sandbox
output directory that is NOT inside production roots.

Strictly sandbox-only
---------------------
- The default output directory is a sandbox path and the script
  REQUIRES an explicit ``--output-dir`` that is NOT under
  ``signal_library/data/stable``. If the supplied output dir
  resolves under the production stable directory, the script
  refuses and exits with rc=2.
- No yfinance import / no network.
- No source refresh / no pipeline / no batch engine execution.
- Reads only from ``cache/results/<TICKER>_precomputed_results.pkl``
  via the central provenance-verified loader.
- No raw ``pickle.load`` in this module -- the cache PKL is loaded
  through ``provenance_manifest.load_verified_pickle_artifact``.

CLI
---

    python -m signal_library.multi_timeframe_sandbox_builder \\
        --tickers SPY,PRGO,AWR,... \\
        --intervals 1d,1wk,1mo,3mo,1y \\
        --cache-dir cache/results \\
        --output-dir <SANDBOX_DIR>

Returns rc=0 on full success, rc=2 on argument / safety failure, rc=3
on per-ticker unexpected error (continues other tickers / intervals).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd


_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


import provenance_manifest as _pm  # noqa: E402
from signal_library import multi_timeframe_builder as _mtf  # noqa: E402


logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


# ---------------------------------------------------------------------------
# Stable interval -> pandas-frequency map
# ---------------------------------------------------------------------------

# Mirrors multi_timeframe_builder.fetch_interval_data:
#   - 1wk: yfinance returns Monday-anchored weekly bars (W-MON).
#   - 1mo: month-start (MS).
#   - 3mo: quarter-start (QS).
#   - 1y:  year-end December (YE-DEC).
# Daily (1d) is passed through without resampling.
_INTERVAL_FREQ_MAP: dict = {
    "1wk": ("W-MON", "last"),
    "1mo": ("MS", "first"),
    "3mo": ("QS", "first"),
    "1y": ("YE-DEC", "last"),
}


# ---------------------------------------------------------------------------
# Local-cache OHLCV fetcher
# ---------------------------------------------------------------------------


def load_daily_close_from_cache(
    ticker: str, cache_dir: Path,
) -> Optional[pd.DataFrame]:
    """Load the daily ``Close`` series for ``ticker`` from
    ``<cache_dir>/<TICKER>_precomputed_results.pkl`` via the central
    provenance-verified loader.

    Returns a DataFrame with a single ``Close`` column and a tz-naive
    ``DatetimeIndex`` on success, or ``None`` if the cache PKL is
    absent / unreadable / lacks the expected ``preprocessed_data``
    shape.
    """
    candidate = cache_dir / f"{ticker}_precomputed_results.pkl"
    if not candidate.exists():
        logger.warning(
            f"cache miss for {ticker}: {candidate}",
        )
        return None
    try:
        data, vresult = _pm.load_verified_pickle_artifact(candidate)
    except Exception as exc:
        logger.warning(
            f"cache load failed for {ticker}: {exc!r}",
        )
        return None
    if data is None:
        return None
    if not (vresult.ok or vresult.legacy):
        logger.warning(
            f"cache provenance mismatch for {ticker}",
        )
        return None
    pre = data.get("preprocessed_data") if hasattr(
        data, "get",
    ) else None
    if pre is None or not hasattr(pre, "columns"):
        logger.warning(
            f"cache lacks preprocessed_data for {ticker}",
        )
        return None
    if "Close" not in list(pre.columns):
        return None
    out = pre[["Close"]].copy()
    if hasattr(out.index, "tz") and out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    else:
        out.index = pd.to_datetime(out.index).tz_localize(None)
    out = out.sort_index()
    out["Close"] = out["Close"].astype(np.float64)
    return out


def build_interval_df_from_daily(
    daily_df: pd.DataFrame, interval: str,
) -> pd.DataFrame:
    """Resample a daily ``Close`` DataFrame to ``interval`` using the
    pandas frequency contract the production builder already uses
    (W-MON / MS / QS / YE-DEC). Daily passes through unchanged.

    This is the only resampling site in the Phase 6I-30 sandbox
    harness; it lives inside the builder layer (where interval
    construction already belongs) and is NEVER called from the
    multi-window K adapter.
    """
    if interval == "1d":
        return daily_df
    if interval not in _INTERVAL_FREQ_MAP:
        raise ValueError(f"unsupported interval: {interval!r}")
    freq, agg = _INTERVAL_FREQ_MAP[interval]
    rs = daily_df["Close"].resample(freq)
    if agg == "last":
        series = rs.last()
    else:
        series = rs.first()
    out = series.to_frame(name="Close").dropna()
    out = out.sort_index()
    if hasattr(out.index, "tz") and out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    return out


# ---------------------------------------------------------------------------
# Public sandbox entry point
# ---------------------------------------------------------------------------


def build_sandbox_library(
    ticker: str,
    interval: str,
    *,
    cache_dir: Path,
    output_dir: Path,
    end_date: Optional[str] = None,
) -> Optional[Path]:
    """Build one sandbox interval library and write it to
    ``<output_dir>``. Returns the written path on success or ``None``
    on failure.

    ``end_date`` is a sandbox-only cutoff. When supplied (ISO
    ``YYYY-MM-DD``), the daily DataFrame is truncated to rows on
    or before that date BEFORE resampling. This lets the sandbox
    proof harness pick a common cutoff across all tickers so the
    multi-window K adapter's strict full-member-coverage gate can
    actually evaluate, even when production tickers' cache PKLs
    have heterogeneous last-dates (a real condition observed for
    SPY's K=1..12 universe -- TEF stops at 2026-01-28 while most
    other members run to 2026-05-04).
    """
    daily_df = load_daily_close_from_cache(ticker, cache_dir)
    if daily_df is None:
        return None
    if end_date is not None:
        try:
            cutoff = pd.Timestamp(end_date)
            daily_df = daily_df.loc[daily_df.index <= cutoff].copy()
        except Exception as exc:
            logger.error(
                f"end_date parse failed for {ticker}: {exc!r}",
            )
            return None
        if len(daily_df) < 2:
            logger.warning(
                f"{ticker}: too few bars after end_date "
                f"cutoff ({len(daily_df)})",
            )
            return None
    try:
        interval_df = build_interval_df_from_daily(
            daily_df, interval,
        )
    except Exception as exc:
        logger.error(
            f"resample failed for {ticker} {interval}: {exc!r}",
        )
        return None
    if interval_df is None or len(interval_df) < 2:
        logger.warning(
            f"insufficient bars for {ticker} {interval} "
            f"({0 if interval_df is None else len(interval_df)})",
        )
        return None

    # Inject the resampled DataFrame into the production builder's
    # generate_signals_for_interval via its Phase 6I-30 ``df=`` seam.
    # The builder computes SMAs + signals + entry dates + native
    # close exactly as it would for a yfinance-backed fetch, but
    # against this sandbox-resampled DataFrame.
    library = _mtf.generate_signals_for_interval(
        ticker, interval, df=interval_df,
    )
    if library is None:
        return None

    # Point save_signal_library at the sandbox output_dir via the
    # builder's existing SIGNAL_LIBRARY_DIR module-level constant
    # (the builder reads this at save time, not at import). We also
    # set force_overwrite=True for the 1d case because the production
    # builder's daily-protection guard would otherwise refuse the
    # write -- here we are explicitly writing to a sandbox path, not
    # the production stable directory.
    original_dir = _mtf.SIGNAL_LIBRARY_DIR
    try:
        _mtf.SIGNAL_LIBRARY_DIR = str(output_dir)
        saved_path = _mtf.save_signal_library(
            library, interval, force_overwrite=True,
        )
    finally:
        _mtf.SIGNAL_LIBRARY_DIR = original_dir
    return Path(saved_path)


def build_sandbox_libraries_for_ticker(
    ticker: str,
    intervals: Iterable[str],
    *,
    cache_dir: Path,
    output_dir: Path,
    end_date: Optional[str] = None,
) -> dict[str, Optional[Path]]:
    """Build sandbox libraries for one ticker across all requested
    intervals. Returns a ``{interval: path_or_None}`` map."""
    out: dict[str, Optional[Path]] = {}
    for interval in intervals:
        path = build_sandbox_library(
            ticker, interval,
            cache_dir=cache_dir,
            output_dir=output_dir,
            end_date=end_date,
        )
        out[interval] = path
        if path is None:
            logger.warning(
                f"sandbox build skipped: {ticker} {interval}",
            )
    return out


# ---------------------------------------------------------------------------
# Safety check
# ---------------------------------------------------------------------------


_PRODUCTION_STABLE_SUFFIX = os.path.join(
    "signal_library", "data", "stable",
)


def _is_inside_production_stable_dir(output_dir: Path) -> bool:
    """Return True iff ``output_dir`` resolves to a path under the
    production ``signal_library/data/stable`` tree.

    Phase 6I-30 hard rule: the sandbox builder MUST NOT write to
    production. We refuse any output dir whose resolved path ends in
    or contains ``signal_library/data/stable``.
    """
    try:
        resolved = output_dir.resolve()
    except Exception:
        return False
    txt = str(resolved).replace("\\", "/").lower()
    suffix_norm = (
        _PRODUCTION_STABLE_SUFFIX.replace("\\", "/").lower()
    )
    return suffix_norm in txt


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multi_timeframe_sandbox_builder",
        description=(
            "Phase 6I-30 sandbox-only interval signal-library "
            "builder. Reads daily OHLCV from the local Spymaster "
            "cache (cache/results/<TICKER>_precomputed_results.pkl) "
            "via the central provenance loader, resamples to each "
            "requested interval inside this builder, and writes "
            "the resulting interval libraries to an explicit "
            "sandbox --output-dir. STRICTLY SANDBOX-ONLY -- "
            "refuses to write to signal_library/data/stable."
        ),
    )
    parser.add_argument(
        "--tickers", required=True,
        help="Comma-separated ticker list (e.g. SPY,PRGO,AWR).",
    )
    parser.add_argument(
        "--intervals",
        default="1d,1wk,1mo,3mo,1y",
        help=(
            "Comma-separated intervals. Default: "
            "1d,1wk,1mo,3mo,1y (all five canonical windows)."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        default="cache/results",
        help=(
            "Path to the Spymaster precomputed-results cache "
            "directory (read-only). Default: cache/results."
        ),
    )
    parser.add_argument(
        "--output-dir", required=True,
        help=(
            "Sandbox output directory (REQUIRED). Must NOT be "
            "under signal_library/data/stable."
        ),
    )
    parser.add_argument(
        "--end-date", default=None,
        help=(
            "Optional ISO YYYY-MM-DD end-date cutoff applied to "
            "every ticker's daily DataFrame BEFORE resampling. "
            "Lets the sandbox proof pick a common cutoff across "
            "tickers whose production cache PKLs have "
            "heterogeneous last-dates."
        ),
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    tickers = [
        t.strip() for t in args.tickers.split(",") if t.strip()
    ]
    intervals = [
        i.strip() for i in args.intervals.split(",") if i.strip()
    ]
    cache_dir = Path(args.cache_dir).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not tickers:
        print("error: no tickers", file=sys.stderr)
        return 2
    if not intervals:
        print("error: no intervals", file=sys.stderr)
        return 2

    if _is_inside_production_stable_dir(output_dir):
        print(
            "error: refusing to write to production "
            "signal_library/data/stable",
            file=sys.stderr,
        )
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    written = 0
    for ticker in tickers:
        per_interval = build_sandbox_libraries_for_ticker(
            ticker, intervals,
            cache_dir=cache_dir,
            output_dir=output_dir,
            end_date=args.end_date,
        )
        for interval, path in per_interval.items():
            if path is None:
                failures += 1
            else:
                written += 1
    logger.info(
        f"sandbox build summary: written={written} "
        f"failures={failures}",
    )
    return 0 if failures == 0 else 3


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
