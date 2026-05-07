"""
Phase 5C-2d prep regression suite: pin the extracted Spymaster
optimization core, the cutoff-aware loader wrappers, and the
refactored ``optimize_signals`` callback's 4-output Dash contract.

The pure core ``compute_spymaster_optimization`` MUST NOT touch any
Dash plumbing or mutable global. The formatter
``format_spymaster_optimization_table`` MUST preserve the historical
DataTable column set, AVERAGES row, and sort behavior. The callback
MUST keep the existing 4-tuple ``(rows, columns, message,
interval_disabled)`` shape and short-circuit cache/sort polling
without re-running compute. The cutoff wrappers MUST default to no-op
behavior so production runs are byte-equivalent.

ASCII-only assertions. No Dash server is started; spymaster.py
imports Dash at module load by design and the Dash callback context
is monkeypatched per test.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import spymaster  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _bdates(n, start="2018-01-02"):
    return pd.bdate_range(start, periods=n)


def _synthetic_close(n, *, seed=11, drift=0.0006):
    rng = np.random.default_rng(seed)
    rets = rng.standard_normal(n) * 0.011 + drift
    close = 100.0 * np.exp(np.cumsum(rets))
    df = pd.DataFrame({"Close": close.astype(float)}, index=_bdates(n))
    df.index.name = "Date"
    return df


def _build_signal_data(ticker, n, *, seed, next_signal="Buy"):
    """Build a synthetic SpymasterPrimarySignalData object with a
    deterministic ``signals_with_next`` series and a known
    next_signal value.
    """
    idx = _bdates(n)
    rng = np.random.default_rng(seed + (sum(ord(c) for c in ticker) % 97))
    pool = rng.choice(["Buy", "Short", "None"], size=n, p=[0.4, 0.3, 0.3])
    series = pd.Series(pool, index=idx, dtype=object)
    return spymaster.SpymasterPrimarySignalData(
        ticker=ticker.upper(),
        signals_with_next=series,
        next_signal=next_signal,
    )


def _synthetic_results_dict(df, *, active_pairs=None, with_top_pairs=True):
    """Build a Spymaster results dict matching the PKL schema enough
    to drive ``build_spymaster_primary_signal_data``.
    """
    n = len(df.index)
    if active_pairs is None:
        active_pairs = ["Buy"] * n
    out = {
        "preprocessed_data": df,
        "active_pairs": active_pairs,
    }
    if with_top_pairs:
        sma_a = max(2, min(5, n // 4))
        sma_b = max(sma_a + 1, sma_a + 2)
        # Make sure SMA columns exist on df.
        if f"SMA_{sma_a}" not in df.columns:
            df[f"SMA_{sma_a}"] = df["Close"].rolling(sma_a, min_periods=1).mean()
        if f"SMA_{sma_b}" not in df.columns:
            df[f"SMA_{sma_b}"] = df["Close"].rolling(sma_b, min_periods=1).mean()
        last_date = df.index[-1]
        out["daily_top_buy_pairs"] = {
            last_date: ((sma_a, sma_b), 0.5),
        }
        out["daily_top_short_pairs"] = {
            last_date: ((sma_a, sma_b), 0.3),
        }
    return out


# ---------------------------------------------------------------------------
# 1. Pure core ranks combinations correctly
# ---------------------------------------------------------------------------


def test_pure_core_ranks_combinations_correctly_for_deterministic_fixture():
    n = 90
    sec = _synthetic_close(n, seed=21)
    psig_a = _build_signal_data("AAA", n, seed=11, next_signal="Buy")
    psig_b = _build_signal_data("BBB", n, seed=23, next_signal="Short")
    psig_c = _build_signal_data("CCC", n, seed=37, next_signal="None")
    primary_signal_data = {"AAA": psig_a, "BBB": psig_b, "CCC": psig_c}

    result = spymaster.compute_spymaster_optimization(
        primary_tickers=["AAA", "BBB", "CCC"],
        secondary_ticker="ZZZ",
        secondary_data=sec,
        primary_signal_data=primary_signal_data,
        progress_callback=None,
    )
    assert isinstance(result, spymaster.SpymasterOptimizationResult)
    assert result.records, "expected at least one valid combination record"
    # Records sorted by Sharpe descending.
    sharpes = [r["Sharpe"] for r in result.records]
    assert sharpes == sorted(sharpes, reverse=True), (
        f"records must be Sharpe-sorted descending: {sharpes}"
    )
    # total_combinations matches the pre-evaluation product:
    # AAA: 2 (Buy), BBB: 2 (Short), CCC: 1 (None-only-mute) -> 4
    assert result.total_combinations == 4


# ---------------------------------------------------------------------------
# 2. Pure core records carry helper fields
# ---------------------------------------------------------------------------


def test_pure_core_records_carry_helper_fields():
    n = 80
    sec = _synthetic_close(n, seed=21)
    primary_signal_data = {
        "AAA": _build_signal_data("AAA", n, seed=11, next_signal="Buy"),
        "BBB": _build_signal_data("BBB", n, seed=23, next_signal="Short"),
    }
    result = spymaster.compute_spymaster_optimization(
        primary_tickers=["AAA", "BBB"],
        secondary_ticker="ZZZ",
        secondary_data=sec,
        primary_signal_data=primary_signal_data,
        progress_callback=None,
    )
    assert result.records
    for rec in result.records:
        assert "id" in rec, "visible integer id must be preserved"
        assert isinstance(rec["id"], int)
        assert "state_by_ticker" in rec
        assert isinstance(rec["state_by_ticker"], dict)
        assert "unmuted_tickers" in rec
        assert isinstance(rec["unmuted_tickers"], list)
        assert "strategy_id" in rec
        assert isinstance(rec["strategy_id"], str)
        assert rec["strategy_id"].startswith("SPYMASTER(")
        assert rec["strategy_id"].endswith("__ZZZ")


# ---------------------------------------------------------------------------
# 3. Pure core progress callback invoked
# ---------------------------------------------------------------------------


def test_pure_core_progress_callback_invoked():
    n = 60
    sec = _synthetic_close(n, seed=21)
    primary_signal_data = {
        "AAA": _build_signal_data("AAA", n, seed=11, next_signal="Buy"),
        "BBB": _build_signal_data("BBB", n, seed=23, next_signal="Short"),
    }
    calls: List[tuple] = []

    def _cb(current, total):
        calls.append((int(current), int(total)))

    result = spymaster.compute_spymaster_optimization(
        primary_tickers=["AAA", "BBB"],
        secondary_ticker="ZZZ",
        secondary_data=sec,
        primary_signal_data=primary_signal_data,
        progress_callback=_cb,
    )
    assert calls, "progress_callback must be invoked at least once"
    # Final tick must be (total, total) when total > 0.
    if calls:
        last = calls[-1]
        assert last[0] == last[1] == result.total_combinations
    # All totals match.
    for cur, tot in calls:
        assert tot == result.total_combinations


# ---------------------------------------------------------------------------
# 4. Pure core has no Dash or mutable global references (AST inspection)
# ---------------------------------------------------------------------------


_FORBIDDEN_NAMES = frozenset({
    "dash",
    "callback_context",
    "optimization_results_cache",
    "optimization_lock",
    "optimization_in_progress",
    "optimization_progress",
    "pending_optimization",
    "_precomputed_results_cache",
    "_loading_in_progress",
    "_loading_lock",
    "_secondary_df_cache",
    "status_lock",
})


def test_pure_core_has_no_dash_or_mutable_global_references():
    src = inspect.getsource(spymaster.compute_spymaster_optimization)
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            bad.append(node.id)
        if isinstance(node, ast.Attribute):
            # Cover ``dash.callback_context`` / ``dash.<X>``
            if isinstance(node.value, ast.Name) and node.value.id in _FORBIDDEN_NAMES:
                bad.append(node.value.id)
    assert not bad, (
        f"compute_spymaster_optimization references forbidden names: {bad}"
    )


# ---------------------------------------------------------------------------
# 5. Formatter preserves existing DataTable columns
# ---------------------------------------------------------------------------


_EXPECTED_COLUMNS = [
    {'name': 'Combination', 'id': 'Combination', 'presentation': 'markdown'},
    {'name': 'Triggers', 'id': 'Triggers', 'type': 'numeric'},
    {'name': 'Wins', 'id': 'Wins', 'type': 'numeric'},
    {'name': 'Losses', 'id': 'Losses', 'type': 'numeric'},
    {'name': 'Win %', 'id': 'Win %', 'type': 'numeric'},
    {'name': 'StdDev %', 'id': 'StdDev %', 'type': 'numeric'},
    {'name': 'Sharpe', 'id': 'Sharpe', 'type': 'numeric'},
    {'name': 't', 'id': 't'},
    {'name': 'p', 'id': 'p'},
    {'name': 'Sig 90%', 'id': 'Sig 90%'},
    {'name': 'Sig 95%', 'id': 'Sig 95%'},
    {'name': 'Sig 99%', 'id': 'Sig 99%'},
    {'name': 'Avg Cap %', 'id': 'Avg Cap %', 'type': 'numeric'},
    {'name': 'Total %', 'id': 'Total %', 'type': 'numeric'},
]


def _make_record(idx, sharpe, *, ticker="AAA", mode="B"):
    return {
        "id": idx,
        "Combination": f"<span style='color:#80ff00'>{ticker}</span>",
        "Triggers": 50,
        "Wins": 30,
        "Losses": 20,
        "Win %": 60.0,
        "StdDev %": 1.2345,
        "Sharpe": float(sharpe),
        "t": 1.234,
        "p": 0.045,
        "Sig 90%": "Yes",
        "Sig 95%": "Yes",
        "Sig 99%": "No",
        "Avg Cap %": 0.0123,
        "Total %": 12.3456,
        "state_by_ticker": {ticker: {"invert_signals": mode == "I", "mute": False}},
        "unmuted_tickers": [ticker],
        "strategy_id": f"SPYMASTER({ticker}[{mode}])__ZZZ",
    }


def test_formatter_preserves_existing_datatable_columns():
    result = spymaster.SpymasterOptimizationResult(
        records=[_make_record(0, 1.5)],
        last_contract_issue="",
        total_combinations=1,
    )
    rows, columns, message = spymaster.format_spymaster_optimization_table(result)
    assert columns == _EXPECTED_COLUMNS, (
        f"DataTable columns must match historical layout exactly\n"
        f"got: {columns}\nwant: {_EXPECTED_COLUMNS}"
    )


# ---------------------------------------------------------------------------
# 6. Formatter places AVERAGES row at index zero
# ---------------------------------------------------------------------------


def test_formatter_averages_row_at_index_zero():
    recs = [
        _make_record(0, 2.0),
        _make_record(1, 1.0),
        _make_record(2, 0.5),
    ]
    # Differentiate Triggers values so the average is verifiable.
    recs[0]["Triggers"] = 60
    recs[1]["Triggers"] = 30
    recs[2]["Triggers"] = 0
    result = spymaster.SpymasterOptimizationResult(
        records=recs, last_contract_issue="", total_combinations=3,
    )
    rows, _cols, _msg = spymaster.format_spymaster_optimization_table(result)
    assert rows[0]["Combination"] == "AVERAGES"
    assert rows[0]["Triggers"] == round((60 + 30 + 0) / 3)
    assert rows[0]["Sharpe"] == round((2.0 + 1.0 + 0.5) / 3, 2)
    # Visible rows must NOT carry helper fields.
    for r in rows:
        for hf in ("state_by_ticker", "unmuted_tickers", "strategy_id"):
            assert hf not in r, f"helper field {hf} leaked into visible row"


# ---------------------------------------------------------------------------
# 7. Formatter sort path matches current behavior
# ---------------------------------------------------------------------------


def test_formatter_sort_path_matches_current_behavior():
    recs = [
        _make_record(0, 1.5, ticker="CCC"),
        _make_record(1, 2.0, ticker="AAA"),
        _make_record(2, 0.5, ticker="BBB"),
    ]
    result = spymaster.SpymasterOptimizationResult(
        records=recs, last_contract_issue="", total_combinations=3,
    )
    # Numeric ascending sort by Sharpe.
    rows_num, _c, _m = spymaster.format_spymaster_optimization_table(
        result, sort_by=[{"column_id": "Sharpe", "direction": "asc"}],
    )
    assert rows_num[0]["Combination"] == "AVERAGES"
    body_sharpes = [r["Sharpe"] for r in rows_num[1:]]
    assert body_sharpes == sorted(body_sharpes), (
        f"ascending Sharpe sort mismatch: {body_sharpes}"
    )

    # String descending sort by Combination.
    rows_str, _c2, _m2 = spymaster.format_spymaster_optimization_table(
        result, sort_by=[{"column_id": "Combination", "direction": "desc"}],
    )
    body_str = [r["Combination"] for r in rows_str[1:]]
    assert body_str == sorted(body_str, reverse=True)


# ---------------------------------------------------------------------------
# 8. Callback returns 4-output tuple for the ready-data path
# ---------------------------------------------------------------------------


class _FakeCtx:
    """Mimic the dash.callback_context surface used in optimize_signals."""

    def __init__(self, prop_id, *, sort_by=None):
        self.triggered = [{"prop_id": prop_id}]
        # ``ctx.triggered_id`` is a string in our codebase.
        self.triggered_id = prop_id.split(".")[0]
        self.inputs = SimpleNamespace()
        if sort_by is not None:
            setattr(
                self.inputs,
                'optimization-results-table.sort_by',
                sort_by,
            )


def _patch_callback_dependencies(
    monkeypatch,
    *,
    triggered_prop_id,
    cache_state=None,
    sort_by=None,
    primary_results=None,
    primary_dfs=None,
    secondary=None,
    statuses=None,
    pending=None,
    queue_capture=None,
):
    monkeypatch.setattr(spymaster.dash, "callback_context",
                        _FakeCtx(triggered_prop_id, sort_by=sort_by))
    cache_state = cache_state if cache_state is not None else {}
    monkeypatch.setattr(spymaster, "optimization_results_cache", cache_state)
    monkeypatch.setattr(spymaster, "optimization_in_progress", False)
    monkeypatch.setattr(spymaster, "pending_optimization", pending)
    # rate_limit always allows the callback through.
    monkeypatch.setattr(spymaster, "rate_limit", lambda *a, **kw: True)
    # _enforce_cache_limits no-op.
    monkeypatch.setattr(spymaster, "_enforce_cache_limits", lambda: None)

    statuses = statuses or {}

    def _fake_read_status(t):
        return statuses.get(str(t).upper(), {"status": "complete"})

    monkeypatch.setattr(spymaster, "read_status", _fake_read_status)

    queue_calls = queue_capture if queue_capture is not None else []

    def _fake_queue(missing):
        queue_calls.append(list(missing))

    monkeypatch.setattr(spymaster, "_queue_missing_primaries", _fake_queue)

    primary_results = primary_results or {}
    primary_dfs = primary_dfs or {}

    def _fake_load(t, **kw):
        return primary_results.get(str(t).upper())

    def _fake_ensure_df(t, results=None):
        return primary_dfs.get(str(t).upper())

    monkeypatch.setattr(spymaster, "load_precomputed_results", _fake_load)
    monkeypatch.setattr(spymaster, "ensure_df_available", _fake_ensure_df)

    def _fake_fetch_secondary_window(t, start, end):
        return secondary

    def _fake_fetch_data(t, is_secondary=False, max_retries=4):
        return secondary

    monkeypatch.setattr(
        spymaster, "fetch_secondary_window", _fake_fetch_secondary_window,
    )
    monkeypatch.setattr(spymaster, "fetch_data", _fake_fetch_data)

    return cache_state, queue_calls


def test_callback_returns_four_outputs_for_ready_data_path(monkeypatch):
    n = 90
    sec = _synthetic_close(n, seed=33)
    df_aaa = sec.copy()
    df_aaa["SMA_3"] = df_aaa["Close"].rolling(3, min_periods=1).mean()
    df_aaa["SMA_5"] = df_aaa["Close"].rolling(5, min_periods=1).mean()
    df_bbb = df_aaa.copy()
    res_aaa = _synthetic_results_dict(df_aaa)
    res_bbb = _synthetic_results_dict(df_bbb)

    _patch_callback_dependencies(
        monkeypatch,
        triggered_prop_id="optimize-signals-button.n_clicks",
        primary_results={"AAA": res_aaa, "BBB": res_bbb},
        primary_dfs={"AAA": df_aaa, "BBB": df_bbb},
        secondary=sec,
        statuses={"AAA": {"status": "complete"},
                  "BBB": {"status": "complete"}},
    )
    out = spymaster.optimize_signals(
        n_clicks=1, n_intervals=None, sort_by=None,
        primary_tickers_input="AAA, BBB",
        secondary_ticker_input="ZZZ",
    )
    assert isinstance(out, tuple)
    assert len(out) == 4
    rows, columns, message, interval_disabled = out
    assert isinstance(rows, list)
    assert isinstance(columns, list)
    assert isinstance(interval_disabled, bool)
    # Success case: AVERAGES row pinned at index 0 (when results exist).
    if rows:
        assert rows[0]["Combination"] == "AVERAGES"
        assert interval_disabled is True


# ---------------------------------------------------------------------------
# 9. Cache/sort path returns cached data without recomputation
# ---------------------------------------------------------------------------


def test_callback_cache_sort_path_returns_cached_data_without_recomputation(
    monkeypatch,
):
    sentinel_rows = [
        {"Combination": "AVERAGES", "Sharpe": 1.0, "Triggers": 10},
        {"Combination": "AAA", "Sharpe": 1.5, "Triggers": 20},
        {"Combination": "BBB", "Sharpe": 0.5, "Triggers": 30},
    ]
    sentinel_columns = [{"name": "Combination", "id": "Combination"}]
    sentinel_message = "cached msg"
    cache_key = "AAA, BBB_ZZZ"
    cache = {
        cache_key: (sentinel_rows, sentinel_columns, sentinel_message, None),
    }
    sort_by_spec = [{"column_id": "Sharpe", "direction": "asc"}]

    _patch_callback_dependencies(
        monkeypatch,
        triggered_prop_id="optimization-results-table.sort_by",
        cache_state=cache,
        sort_by=sort_by_spec,
        statuses={"AAA": {"status": "complete"},
                  "BBB": {"status": "complete"}},
    )

    compute_calls = []

    def _compute_spy(*a, **kw):
        compute_calls.append((a, kw))
        raise AssertionError(
            "compute_spymaster_optimization must NOT be called on a "
            "cache/sort polling tick"
        )

    monkeypatch.setattr(spymaster, "compute_spymaster_optimization", _compute_spy)

    out = spymaster.optimize_signals(
        n_clicks=None, n_intervals=1, sort_by=sort_by_spec,
        primary_tickers_input="AAA, BBB",
        secondary_ticker_input="ZZZ",
    )
    assert isinstance(out, tuple) and len(out) == 4
    rows, columns, message, interval_disabled = out
    assert columns is sentinel_columns
    assert message == sentinel_message
    # Sort applied to non-AVERAGES rows; AVERAGES still pinned to index 0.
    assert rows[0]["Combination"] == "AVERAGES"
    body_sharpes = [r["Sharpe"] for r in rows[1:]]
    assert body_sharpes == sorted(body_sharpes), (
        f"cached sort path must apply ascending Sharpe: {body_sharpes}"
    )
    # Polling path keeps interval ALIVE (False).
    assert interval_disabled is False
    assert not compute_calls, "compute helper must not have been called"


# ---------------------------------------------------------------------------
# 10. Cutoff wrapper slices preprocessed_data
# ---------------------------------------------------------------------------


def test_cutoff_wrapper_slices_preprocessed_data():
    n = 50
    df = _synthetic_close(n, seed=13)
    results = {
        "preprocessed_data": df,
        "active_pairs": ["Buy"] * n,
        "daily_top_buy_pairs": {df.index[-1]: ((2, 3), 0.5)},
        "daily_top_short_pairs": {df.index[-1]: ((2, 3), 0.3)},
    }
    cutoff = df.index[30]
    out = spymaster._slice_spymaster_results_to_cutoff(
        results, data_available_through=cutoff,
    )
    assert isinstance(out["preprocessed_data"], pd.DataFrame)
    sliced = out["preprocessed_data"]
    assert sliced.index.max() <= cutoff
    assert len(sliced) == 31
    # Original results not mutated.
    assert len(results["preprocessed_data"]) == n


# ---------------------------------------------------------------------------
# 11. Cutoff wrapper slices daily_top_buy_pairs and short_pairs
# ---------------------------------------------------------------------------


def test_cutoff_wrapper_slices_daily_top_buy_pairs_and_short_pairs():
    n = 30
    df = _synthetic_close(n, seed=19)
    buy_pairs = {df.index[i]: ((2, 3), 0.5 + i * 0.01) for i in range(n)}
    short_pairs = {df.index[i]: ((2, 3), 0.3 + i * 0.01) for i in range(n)}
    results = {
        "preprocessed_data": df,
        "active_pairs": ["Buy"] * n,
        "daily_top_buy_pairs": buy_pairs,
        "daily_top_short_pairs": short_pairs,
    }
    cutoff = df.index[10]
    out = spymaster._slice_spymaster_results_to_cutoff(
        results, data_available_through=cutoff,
    )
    for k in out["daily_top_buy_pairs"].keys():
        assert pd.Timestamp(k) <= cutoff
    for k in out["daily_top_short_pairs"].keys():
        assert pd.Timestamp(k) <= cutoff
    assert len(out["daily_top_buy_pairs"]) == 11
    assert len(out["daily_top_short_pairs"]) == 11


# ---------------------------------------------------------------------------
# 12. Cutoff wrapper aligns active_pairs with sliced dates
# ---------------------------------------------------------------------------


def test_cutoff_wrapper_active_pairs_stays_aligned_with_sliced_dates():
    n = 40
    df = _synthetic_close(n, seed=31)
    active_pairs = [f"AP_{i}" for i in range(n)]  # len == len(df)
    results = {
        "preprocessed_data": df,
        "active_pairs": active_pairs,
        "daily_top_buy_pairs": {df.index[-1]: ((2, 3), 0.5)},
        "daily_top_short_pairs": {df.index[-1]: ((2, 3), 0.3)},
    }
    cutoff = df.index[20]
    out = spymaster._slice_spymaster_results_to_cutoff(
        results, data_available_through=cutoff,
    )
    sliced_df = out["preprocessed_data"]
    sliced_active = out["active_pairs"]
    # active_pairs aligned to len(sliced_df) since original len == len(df.index).
    assert len(sliced_active) == len(sliced_df)
    # Positional order preserved.
    assert sliced_active[:5] == active_pairs[:5]
    # Length-mismatch case: original len == len(df) - 1.
    short_active = [f"AP_{i}" for i in range(n - 1)]
    results2 = dict(results, active_pairs=short_active)
    out2 = spymaster._slice_spymaster_results_to_cutoff(
        results2, data_available_through=cutoff,
    )
    sliced2 = out2["preprocessed_data"]
    sliced_active2 = out2["active_pairs"]
    assert len(sliced_active2) == max(0, len(sliced2) - 1)


# ---------------------------------------------------------------------------
# 13. Cutoff wrapper default-None preserves field-equivalent behavior
# ---------------------------------------------------------------------------


def test_cutoff_wrapper_default_none_preserves_field_equivalent_behavior():
    n = 20
    df = _synthetic_close(n, seed=7)
    results = {
        "preprocessed_data": df,
        "active_pairs": ["Buy"] * n,
        "daily_top_buy_pairs": {df.index[-1]: ((2, 3), 0.5)},
        "daily_top_short_pairs": {df.index[-1]: ((2, 3), 0.3)},
        "extra_metadata": {"x": 1, "y": [1, 2, 3]},
    }
    out = spymaster._slice_spymaster_results_to_cutoff(
        results, data_available_through=None,
    )
    assert isinstance(out, dict)
    # active_pairs not converted to a Series.
    assert isinstance(out["active_pairs"], list)
    # DataFrames compare via pd.testing for dtype/index/value parity.
    pd.testing.assert_frame_equal(
        out["preprocessed_data"], results["preprocessed_data"],
    )
    assert out["daily_top_buy_pairs"] == results["daily_top_buy_pairs"]
    assert out["daily_top_short_pairs"] == results["daily_top_short_pairs"]
    assert out["extra_metadata"] == results["extra_metadata"]
    # And the wrapper produces a separate dict object so mutations don't
    # leak back.
    out["new_key"] = 1
    assert "new_key" not in results


# ---------------------------------------------------------------------------
# Amendment regressions: active_pairs len(df)-1 alignment + no silent drop
# ---------------------------------------------------------------------------


def _build_results_for_alignment_test(df, active_pairs):
    """Synthesize a Spymaster results dict whose top-pair entries are
    populated for every date so the build helper has a valid lookup
    at any cutoff midpoint.
    """
    sma_a = 2
    sma_b = 3
    if f"SMA_{sma_a}" not in df.columns:
        df[f"SMA_{sma_a}"] = df["Close"].rolling(sma_a, min_periods=1).mean()
    if f"SMA_{sma_b}" not in df.columns:
        df[f"SMA_{sma_b}"] = df["Close"].rolling(sma_b, min_periods=1).mean()
    return {
        "preprocessed_data": df,
        "active_pairs": list(active_pairs),
        "daily_top_buy_pairs": {d: ((sma_a, sma_b), 0.5) for d in df.index},
        "daily_top_short_pairs": {d: ((sma_a, sma_b), 0.3) for d in df.index},
    }


def test_build_primary_signal_data_cutoff_preserves_len_minus_one_alignment():
    n = 10
    df = _synthetic_close(n, seed=41)
    sec = df.copy()
    # Historical len(df)-1 PKL shape: 9 active_pairs aligned to df.index[1:].
    active_pairs = [
        "Buy", "Short", "None", "Buy", "Buy",
        "Short", "None", "Buy", "Short",
    ]
    assert len(active_pairs) == n - 1
    results = _build_results_for_alignment_test(df, active_pairs)
    cutoff = df.index[6]
    psig = spymaster.build_spymaster_primary_signal_data(
        ticker="AAA",
        results=results,
        df=df,
        secondary_data=sec,
        data_available_through=cutoff,
    )
    series = psig.signals_with_next
    assert series.index[0] == df.index[1], (
        f"first signal date must be df.index[1] under len(df)-1 alignment; "
        f"got {series.index[0]} vs expected {df.index[1]}"
    )
    assert series.index[0] != df.index[0]
    assert series.index.max() <= cutoff
    # The aligned series must carry the exact active_pairs values in
    # the right slot (active_pairs[0] -> df.index[1]).
    assert str(series.iloc[0]) == "Buy"


def test_slice_results_cutoff_len_minus_one_active_pairs_alignment():
    n = 20
    df = _synthetic_close(n, seed=43)
    active_pairs = [f"AP_{i}" for i in range(n - 1)]
    assert len(active_pairs) == n - 1
    results = _build_results_for_alignment_test(df, active_pairs)
    cutoff = df.index[10]
    out = spymaster._slice_spymaster_results_to_cutoff(
        results, data_available_through=cutoff,
    )
    sliced_pre = out["preprocessed_data"]
    sliced_active = out["active_pairs"]
    assert isinstance(sliced_active, list)
    assert len(sliced_active) == max(0, len(sliced_pre) - 1)
    assert sliced_active[:5] == active_pairs[:5], (
        f"positional order must be preserved: {sliced_active[:5]} vs "
        f"{active_pairs[:5]}"
    )

    # Cutoff before the first aligned signal date returns empty.
    early_cutoff = df.index[0]
    out_early = spymaster._slice_spymaster_results_to_cutoff(
        results, data_available_through=early_cutoff,
    )
    assert out_early["active_pairs"] == [], (
        f"cutoff before first aligned date must return empty active_pairs; "
        f"got {out_early['active_pairs']}"
    )


def test_pure_core_rejects_missing_primary_signal_data():
    n = 60
    sec = _synthetic_close(n, seed=51)
    primary_signal_data = {
        "AAA": _build_signal_data("AAA", n, seed=11, next_signal="Buy"),
    }
    with pytest.raises(ValueError) as exc_info:
        spymaster.compute_spymaster_optimization(
            primary_tickers=["AAA", "BBB"],
            secondary_ticker="ZZZ",
            secondary_data=sec,
            primary_signal_data=primary_signal_data,
            progress_callback=None,
        )
    msg = str(exc_info.value)
    assert "BBB" in msg, f"missing-ticker name absent from message: {msg}"
    assert "missing primary_signal_data" in msg

    # Missing two: message must name both.
    with pytest.raises(ValueError) as exc_info2:
        spymaster.compute_spymaster_optimization(
            primary_tickers=["AAA", "BBB", "CCC"],
            secondary_ticker="ZZZ",
            secondary_data=sec,
            primary_signal_data=primary_signal_data,
            progress_callback=None,
        )
    msg2 = str(exc_info2.value)
    assert "BBB" in msg2 and "CCC" in msg2


def test_callback_wires_progress_callback_without_touching_global_progress(
    monkeypatch,
):
    """Phase 5C-2d prep amendment: optimize_signals MUST pass a real
    progress_callback into compute_spymaster_optimization on the
    ready-data path so the historical 'Combos metrics' tqdm bar is
    restored. The module-level optimization_progress global stays
    unchanged from current production behavior.
    """
    n = 60
    sec = _synthetic_close(n, seed=63)
    df_aaa = sec.copy()
    df_aaa["SMA_3"] = df_aaa["Close"].rolling(3, min_periods=1).mean()
    df_aaa["SMA_5"] = df_aaa["Close"].rolling(5, min_periods=1).mean()
    res_aaa = _synthetic_results_dict(df_aaa)

    _patch_callback_dependencies(
        monkeypatch,
        triggered_prop_id="optimize-signals-button.n_clicks",
        primary_results={"AAA": res_aaa},
        primary_dfs={"AAA": df_aaa},
        secondary=sec,
        statuses={"AAA": {"status": "complete"}},
    )

    received: Dict[str, Any] = {}

    def _spy_compute(*, primary_tickers, secondary_ticker, secondary_data,
                     primary_signal_data, progress_callback):
        received["progress_callback"] = progress_callback
        # Return a non-empty result so the callback proceeds through the
        # cache-write/format path on the ready-data branch.
        rec = _make_record(0, 1.5)
        return spymaster.SpymasterOptimizationResult(
            records=[rec], last_contract_issue="", total_combinations=1,
        )

    monkeypatch.setattr(
        spymaster, "compute_spymaster_optimization", _spy_compute,
    )

    initial_progress = spymaster.optimization_progress
    out = spymaster.optimize_signals(
        n_clicks=1, n_intervals=None, sort_by=None,
        primary_tickers_input="AAA",
        secondary_ticker_input="ZZZ",
    )
    assert isinstance(out, tuple) and len(out) == 4
    assert received.get("progress_callback") is not None, (
        "optimize_signals must pass a real progress_callback to "
        "compute_spymaster_optimization"
    )
    assert callable(received["progress_callback"])
    # Module-level progress global unchanged from production.
    assert spymaster.optimization_progress == initial_progress


# ---------------------------------------------------------------------------
# 14. Missing PKL queues precompute and bypasses pure core
# ---------------------------------------------------------------------------


def test_missing_pkl_path_still_queues_precompute_and_does_not_enter_pure_core(
    monkeypatch,
):
    n = 60
    sec = _synthetic_close(n, seed=53)
    df_present = sec.copy()
    res_present = _synthetic_results_dict(df_present)

    queue_capture: List[List[str]] = []
    _patch_callback_dependencies(
        monkeypatch,
        triggered_prop_id="optimize-signals-button.n_clicks",
        primary_results={"AAA": res_present},  # BBB intentionally missing
        primary_dfs={"AAA": df_present},
        secondary=sec,
        statuses={
            "AAA": {"status": "complete"},
            "BBB": {"status": "not started"},
        },
        queue_capture=queue_capture,
    )

    compute_calls = []

    def _spy_compute(*a, **kw):
        compute_calls.append((a, kw))
        raise AssertionError(
            "compute_spymaster_optimization must NOT run when a primary "
            "PKL is missing"
        )

    monkeypatch.setattr(spymaster, "compute_spymaster_optimization", _spy_compute)

    out = spymaster.optimize_signals(
        n_clicks=1, n_intervals=None, sort_by=None,
        primary_tickers_input="AAA, BBB",
        secondary_ticker_input="ZZZ",
    )
    assert isinstance(out, tuple) and len(out) == 4
    # Missing-ticker queueing populates pending_optimization.
    assert spymaster.pending_optimization is not None
    assert spymaster.pending_optimization["primary"] == "AAA, BBB"
    # The queue helper recorded the missing tickers.
    assert any("BBB" in batch for batch in queue_capture), (
        f"_queue_missing_primaries not invoked with BBB: {queue_capture}"
    )
    # Compute helper never reached.
    assert not compute_calls
