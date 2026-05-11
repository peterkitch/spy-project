"""Phase 6E-4 tests for signal_engine_sma_optimizer.

Pins the extracted optimizer's contract:

  - Static import audit: no spymaster / dash / plotly /
    yfinance / daily_signal_board imports.
  - Synthetic monotonic uptrend produces a Buy verdict on
    the last day.
  - Synthetic monotonic downtrend produces a Short verdict
    on the last day.
  - Mixed regime produces honest ``"Buy a,b"`` /
    ``"Short a,b"`` / ``"None"`` active_pair strings.
  - No (0, 0) sentinels survive in
    ``daily_top_buy_pairs`` / ``daily_top_short_pairs`` or
    in ``top_buy_pair`` / ``top_short_pair``.
  - ``active_pairs`` length matches the cache contract
    (``preprocessed_data`` row count) so
    ``primary_signal_engine.load_primary_signal_engine_payload``
    aligns it correctly.
  - The result is directly convertible into the cache
    payload shape ``primary_signal_engine`` reads.
  - SPY parity smoke: refitting the optimizer against
    SPY's existing saved cache reproduces the cached
    ``top_buy_pair`` / ``top_short_pair``, the last
    ``active_pair`` string, the full active_pairs list,
    and the final cumulative-capture value.
  - All tests run without network and without writing
    production files.
"""
from __future__ import annotations

import ast
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


_PROJECT = Path(__file__).resolve().parent.parent
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

import primary_signal_engine as pse  # noqa: E402
import signal_engine_sma_optimizer as seo  # noqa: E402


# ---------------------------------------------------------------------------
# Static import audit
# ---------------------------------------------------------------------------


def test_optimizer_has_no_forbidden_imports():
    tree = ast.parse(
        Path(seo.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "spymaster", "dash", "plotly", "yfinance",
        "daily_signal_board", "trafficflow", "impactsearch",
        "onepass", "confluence", "cross_ticker_confluence",
        "signal_engine_cache_refresher",
        "phase6_research_preview",
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad = [
        m for m in found if m.split(".")[0] in forbidden
    ]
    assert not bad, (
        f"forbidden imports in optimizer: {bad}"
    )


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------


def _bdates(end: str, n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=end, periods=n)


def _df(close_values: list[float], end: str = "2026-05-08") -> pd.DataFrame:
    idx = _bdates(end, len(close_values))
    return pd.DataFrame(
        {"Close": np.asarray(close_values, dtype=np.float64)},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Buy regime
# ---------------------------------------------------------------------------


def test_uptrend_produces_buy_verdict_on_last_day():
    # 40 strictly increasing closes; the fast (small)
    # SMA stays above the slow SMA throughout the run,
    # so the optimizer should converge on a Buy regime
    # on the last day.
    df = _df([100.0 + i for i in range(40)])
    result = seo.optimize_signal_engine_sma_pairs(
        df, ticker="UPT", max_sma_day=5,
    )
    assert result.issue_codes == ()
    assert result.top_buy_pair is not None
    assert result.top_short_pair is not None
    # Last position should be Buy because every day's
    # daily return is positive and the fast SMA > slow SMA.
    assert result.active_pairs[-1].startswith("Buy "), (
        f"expected Buy on uptrend, got "
        f"{result.active_pairs[-1]!r}"
    )
    # Cumulative capture should be strongly positive.
    assert result.cumulative_combined_captures.iloc[-1] > 0


# ---------------------------------------------------------------------------
# Short regime
# ---------------------------------------------------------------------------


def test_downtrend_produces_short_verdict_on_last_day():
    # 40 strictly decreasing closes; the fast SMA stays
    # below the slow SMA, so the optimizer should pick a
    # Short pair on the last day.
    df = _df([150.0 - i for i in range(40)])
    result = seo.optimize_signal_engine_sma_pairs(
        df, ticker="DWN", max_sma_day=5,
    )
    assert result.issue_codes == ()
    assert result.active_pairs[-1].startswith("Short "), (
        f"expected Short on downtrend, got "
        f"{result.active_pairs[-1]!r}"
    )
    assert result.cumulative_combined_captures.iloc[-1] > 0


# ---------------------------------------------------------------------------
# Mixed regime + honest "None" handling
# ---------------------------------------------------------------------------


def test_mixed_regime_active_pairs_are_honest():
    # 20 up, then 20 down: the optimizer must emit some
    # mixture of Buy / Short / None across the run; the
    # vocabulary must be exactly those three strings.
    up = [100.0 + i for i in range(20)]
    down = [120.0 - i for i in range(20)]
    df = _df(up + down)
    result = seo.optimize_signal_engine_sma_pairs(
        df, ticker="MIX", max_sma_day=5,
    )
    allowed_prefixes = ("Buy ", "Short ", "None")
    bad = [
        p for p in result.active_pairs
        if not (p == "None" or p.startswith(("Buy ", "Short ")))
    ]
    assert not bad, f"unexpected active_pair values: {bad[:5]}"
    # First row always "None" (no shifted-by-one signal on
    # day 0).
    assert result.active_pairs[0] == "None"
    # And the cumulative series length matches the row
    # count.
    assert len(result.cumulative_combined_captures) == len(df)


# ---------------------------------------------------------------------------
# (0, 0) sentinel never leaks out
# ---------------------------------------------------------------------------


def test_no_zero_zero_pairs_in_result():
    # Constant prices: every (i, j) pair has cumulative
    # capture of zero across the entire series. The
    # optimizer must still emit a non-(0, 0) pair on
    # every day (the MAX-SMA sentinel back-fill).
    df = _df([100.0] * 30)
    result = seo.optimize_signal_engine_sma_pairs(
        df, ticker="FLT", max_sma_day=5,
    )
    assert result.top_buy_pair != (0, 0)
    assert result.top_short_pair != (0, 0)
    for date, (pair, _capture) in result.daily_top_buy_pairs.items():
        assert pair != (0, 0), (
            f"(0, 0) buy pair leaked on {date}"
        )
    for date, (pair, _capture) in result.daily_top_short_pairs.items():
        assert pair != (0, 0), (
            f"(0, 0) short pair leaked on {date}"
        )


# ---------------------------------------------------------------------------
# active_pairs alignment matches the loader contract
# ---------------------------------------------------------------------------


def test_active_pairs_alignment_matches_loader_contract(
    tmp_path: Path,
):
    """``primary_signal_engine`` accepts ``active_pairs``
    of length ``len(preprocessed_data)`` OR
    ``len(preprocessed_data) - 1``. Phase 6E-4 emits the
    Spymaster-canonical length (same length as
    ``preprocessed_data``). A round-trip through a pickle
    file confirms the loader reads it back as
    ``available=True`` with a real signal."""
    df = _df([100.0 + i for i in range(40)])
    result = seo.optimize_signal_engine_sma_pairs(
        df, ticker="ROUND", max_sma_day=5,
    )
    assert len(result.active_pairs) == len(
        result.preprocessed_data,
    )

    # Build a cache payload from the result and persist it
    # to a temp dir; verify the Signal Engine loader reads
    # it back as a real signal.
    payload = _result_to_cache_payload(result, "ROUND")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    target = cache_dir / "ROUND_precomputed_results.pkl"
    with target.open("wb") as fh:
        pickle.dump(payload, fh)

    loaded = pse.load_primary_signal_engine_payload(
        "ROUND", cache_dir=cache_dir,
    )
    assert loaded["available"] is True
    # The last day's signal should match what the
    # optimizer's active_pairs reports.
    assert loaded["current_signal"] in ("Buy", "Short", "None")
    assert loaded["current_active_pair_raw"] == result.active_pairs[-1]


def _result_to_cache_payload(
    result: "seo.SignalEngineSmaOptimizationResult",
    ticker: str,
) -> dict:
    """Helper: convert an optimizer result into the dict
    shape Spymaster's writer + the Signal Engine loader
    expect. This is the exact shape a future PR will use
    to wire the optimizer into the refresher. Keeping the
    conversion inside the test confirms the optimizer
    surface is sufficient for the cache contract."""
    df = result.preprocessed_data
    n = len(df)
    last_day = df.index[-1] if n else None
    last_close = (
        float(df["Close"].iloc[-1]) if n else None
    )
    return {
        "preprocessed_data": df,
        "active_pairs": list(result.active_pairs),
        "cumulative_combined_captures": (
            result.cumulative_combined_captures
        ),
        "daily_top_buy_pairs": dict(result.daily_top_buy_pairs),
        "daily_top_short_pairs": dict(
            result.daily_top_short_pairs,
        ),
        "_ticker": ticker,
        "_row_count": n,
        "_first_date": df.index[0] if n else None,
        "_last_date": last_day,
        "top_buy_pair": result.top_buy_pair,
        "top_short_pair": result.top_short_pair,
        "top_buy_capture": result.top_buy_capture,
        "top_short_capture": result.top_short_capture,
        "existing_max_sma_day": result.existing_max_sma_day,
        "last_processed_date": result.last_processed_date,
        "last_date": last_day,
        "start_date": df.index[0] if n else None,
        "last_close": last_close,
        "last_price": last_close,
        "total_trading_days": n,
    }


def test_result_convertible_to_cache_payload(tmp_path: Path):
    """Explicit guard: every cache-payload field the
    Signal Engine loader / future refresher wiring PR
    needs is reachable from the optimizer result without
    re-running the math."""
    df = _df([100.0 + i * 0.5 for i in range(30)])
    result = seo.optimize_signal_engine_sma_pairs(
        df, ticker="CONV", max_sma_day=5,
    )
    payload = _result_to_cache_payload(result, "CONV")
    expected_keys = {
        "preprocessed_data", "active_pairs",
        "cumulative_combined_captures",
        "daily_top_buy_pairs", "daily_top_short_pairs",
        "_ticker", "_row_count", "_first_date",
        "_last_date", "top_buy_pair", "top_short_pair",
        "top_buy_capture", "top_short_capture",
        "existing_max_sma_day", "last_processed_date",
        "last_date", "start_date", "last_close",
        "last_price", "total_trading_days",
    }
    missing = expected_keys - set(payload)
    assert not missing, (
        f"optimizer result cannot build cache payload "
        f"(missing fields: {missing})"
    )


# ---------------------------------------------------------------------------
# Insufficient input cases
# ---------------------------------------------------------------------------


def test_missing_close_column_returns_issue_code():
    df = pd.DataFrame(
        {"Open": [1.0, 2.0, 3.0]},
        index=_bdates("2026-05-08", 3),
    )
    result = seo.optimize_signal_engine_sma_pairs(df)
    assert (
        seo.ISSUE_INVALID_PREPROCESSED_DATA in result.issue_codes
    )


def test_single_row_returns_insufficient_history():
    df = _df([100.0])
    result = seo.optimize_signal_engine_sma_pairs(
        df, max_sma_day=5,
    )
    assert (
        seo.ISSUE_INSUFFICIENT_HISTORY in result.issue_codes
    )


def test_invalid_max_sma_day_returns_issue_code():
    df = _df([100.0, 101.0, 102.0])
    result = seo.optimize_signal_engine_sma_pairs(
        df, max_sma_day=1,
    )
    assert (
        seo.ISSUE_INVALID_MAX_SMA_DAY in result.issue_codes
    )


# ---------------------------------------------------------------------------
# Pair enumeration order matches Spymaster's
# ---------------------------------------------------------------------------


def test_pair_enumeration_matches_spymaster_ordering():
    """Pin Spymaster's exact pair-enumeration order
    (``spymaster.py:5036-5042``); tie-break behavior
    depends on this being correct."""
    pairs = seo._enumerate_pairs(4)
    # N=4 => N*(N-1)=12 pairs. The Spymaster recipe:
    #   for pc in range(12):
    #       i = (pc // 3) + 1
    #       j = (pc % 3) + 1
    #       j = j if j < i else j + 1
    expected = []
    for pc in range(12):
        i = (pc // 3) + 1
        j = (pc % 3) + 1
        if j >= i:
            j += 1
        expected.append((i, j))
    assert pairs == expected


# ---------------------------------------------------------------------------
# SPY parity (uses the existing saved cache; no network)
# ---------------------------------------------------------------------------


_SPY_CACHE = (
    _PROJECT / "cache" / "results" / "SPY_precomputed_results.pkl"
)


@pytest.mark.skipif(
    not _SPY_CACHE.exists(),
    reason="SPY cache not present; parity smoke skipped",
)
def test_spy_parity_against_saved_cache():
    """Refitting the extracted optimizer against SPY's saved
    cache must reproduce Spymaster's published output
    bit-for-bit on the published headline fields:

      - top_buy_pair
      - top_short_pair
      - last active_pair string
      - final cumulative_combined_capture
      - every active_pair across the full series

    Any drift would indicate the extraction diverged from
    the Spymaster math the regression baseline depends on,
    and must be investigated before the refresher wiring
    PR ships."""
    with _SPY_CACHE.open("rb") as fh:
        cached = pickle.load(fh)
    df = cached["preprocessed_data"]
    msd = int(cached.get("existing_max_sma_day", 114))

    result = seo.optimize_signal_engine_sma_pairs(
        df, ticker="SPY", max_sma_day=msd,
    )

    assert result.issue_codes == ()
    assert result.top_buy_pair == cached["top_buy_pair"], (
        f"top_buy_pair drift: refit={result.top_buy_pair} "
        f"cached={cached['top_buy_pair']}"
    )
    assert result.top_short_pair == cached["top_short_pair"], (
        f"top_short_pair drift: refit={result.top_short_pair} "
        f"cached={cached['top_short_pair']}"
    )
    assert (
        result.active_pairs[-1] == cached["active_pairs"][-1]
    ), (
        "last active_pair drift: refit="
        f"{result.active_pairs[-1]!r} cached="
        f"{cached['active_pairs'][-1]!r}"
    )

    refit_cum = float(
        result.cumulative_combined_captures.iloc[-1],
    )
    cached_cum = float(
        cached["cumulative_combined_captures"].iloc[-1],
    )
    assert math.isclose(refit_cum, cached_cum, rel_tol=1e-9, abs_tol=1e-6), (
        f"cumulative drift: refit={refit_cum} "
        f"cached={cached_cum}"
    )

    # Full active_pairs sequence must round-trip.
    refit_aps = list(result.active_pairs)
    cached_aps = list(cached["active_pairs"])
    assert refit_aps == cached_aps, (
        f"active_pairs drift: {sum(1 for a, b in zip(refit_aps, cached_aps) if a != b)} "
        f"positions differ"
    )


@pytest.mark.skipif(
    not _SPY_CACHE.exists(),
    reason="SPY cache not present; parity smoke skipped",
)
def test_spy_parity_payload_loads_via_signal_engine(tmp_path: Path):
    """A cache payload built from the refit result must load
    via ``primary_signal_engine`` with the same headline
    fields the cached payload reports."""
    with _SPY_CACHE.open("rb") as fh:
        cached = pickle.load(fh)
    df = cached["preprocessed_data"]
    msd = int(cached.get("existing_max_sma_day", 114))
    result = seo.optimize_signal_engine_sma_pairs(
        df, ticker="SPY", max_sma_day=msd,
    )
    payload = _result_to_cache_payload(result, "SPY")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    target = cache_dir / "SPY_precomputed_results.pkl"
    with target.open("wb") as fh:
        pickle.dump(payload, fh)

    loaded = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=cache_dir,
    )
    cached_loaded = pse.load_primary_signal_engine_payload(
        "SPY",
        cache_dir=_SPY_CACHE.parent,
    )
    assert loaded["available"] is True
    assert loaded["current_signal"] == cached_loaded["current_signal"]
    assert (
        loaded["current_active_pair_raw"]
        == cached_loaded["current_active_pair_raw"]
    )
    assert (
        loaded["date_range"]["end"]
        == cached_loaded["date_range"]["end"]
    )


# ---------------------------------------------------------------------------
# Negative-control: no production writes
# ---------------------------------------------------------------------------


def test_optimizer_does_not_touch_production_cache(tmp_path: Path):
    """The optimizer is pure / offline; running it must not
    create or modify any file under the project's real
    ``cache/results`` directory. Verified by snapshotting
    that directory before and after the call."""
    prod_cache = (
        _PROJECT / "cache" / "results"
    )
    before = (
        sorted(p.name for p in prod_cache.glob("*"))
        if prod_cache.exists() else []
    )
    df = _df([100.0 + i for i in range(20)])
    seo.optimize_signal_engine_sma_pairs(df, max_sma_day=5)
    after = (
        sorted(p.name for p in prod_cache.glob("*"))
        if prod_cache.exists() else []
    )
    assert before == after, (
        f"optimizer mutated production cache: "
        f"added={set(after) - set(before)}, removed={set(before) - set(after)}"
    )
