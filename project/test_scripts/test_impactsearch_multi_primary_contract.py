"""
Phase 5B-MP-2c regression suite: ImpactSearch hybrid multi-primary
migration.

Pins the locked ``multi_primary_contract_v1`` semantics for the
ImpactSearch aggregate path while preserving batch-mode behavior:

- aggregate-mode helper outputs match ``canonical_scoring.combine_consensus_signals``
- batch mode is unchanged (does not call the aggregate worker)
- ``start_processing`` defaults to batch
- partial / unavailable / no_overlap / invalid_input statuses surface
  with ``[IMPACTSEARCH:...]`` reason codes
- UI text distinguishes the two modes
- bootstrap cross-app parity lock for Spymaster / Confluence /
  ImpactSearch contract wrappers vs. the canonical reference

ASCII-only assertion messages per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import impactsearch  # noqa: E402
from canonical_scoring import combine_consensus_signals  # noqa: E402


def _three_primary_sig_df():
    idx = pd.bdate_range("2024-01-02", periods=6)
    p1 = pd.Series(["Buy", "Buy", "None", "Short", "None", "Buy"], index=idx)
    p2 = pd.Series(["Buy", "None", "None", "Short", "Buy", "Buy"], index=idx)
    p3 = pd.Series(["Buy", "Buy", "None", "Short", "Short", "Buy"], index=idx)
    return pd.DataFrame({"AAA": p1, "BBB": p2, "CCC": p3})


# ---------------------------------------------------------------------------
# 1. Aggregate-mode contract helper matches the canonical reference
# ---------------------------------------------------------------------------


def test_impactsearch_aggregate_mode_matches_canonical_consensus():
    sig_df = _three_primary_sig_df()
    requested = list(sig_df.columns)

    result = impactsearch._impactsearch_multi_primary_contract_result(
        sig_df,
        requested_members=requested,
        contributed_members=requested,
        context="multi-primary",
    )

    expected = combine_consensus_signals([sig_df[c] for c in sig_df.columns])
    pd.testing.assert_series_equal(
        result["aggregate_signal"].astype(object),
        expected.astype(object),
        check_names=False,
    )

    has_trigger = bool((expected.isin(["Buy", "Short"])).any())
    if has_trigger:
        assert result["status"] == "valid", (
            "Aggregate has Buy/Short but status is not 'valid': "
            + str(result["status"])
        )
        assert result["issues"] == []
    else:
        assert result["status"] == "no_triggers"


# ---------------------------------------------------------------------------
# 2. Batch mode is unchanged (no aggregate worker calls in the batch path)
# ---------------------------------------------------------------------------


def test_impactsearch_batch_mode_unchanged(monkeypatch):
    """Drive process_primary_tickers in batch mode with mocked
    fetch / coerce / process_single_ticker dependencies and verify:
      * row shape and values match the existing batch contract
      * the new aggregate worker is never called from the batch path
    """
    sec_idx = pd.bdate_range("2024-01-02", periods=8)
    sec_df = pd.DataFrame(
        {"Close": np.linspace(100.0, 107.0, len(sec_idx))},
        index=sec_idx,
    )

    def _fake_resolve(t):
        return (str(t).strip().upper(), str(t).strip().upper())

    def _fake_fetch_data_raw(ticker, *a, **kw):
        rej = kw.get("rejection_out")
        if isinstance(rej, dict):
            rej.clear()
        return sec_df.copy(), str(ticker).strip().upper()

    def _fake_coerce(df, *a, **kw):
        rej = kw.get("rejection_out")
        if isinstance(rej, dict):
            rej.clear()
        return df.copy()

    def _fake_session_complete(*a, **kw):
        return True

    def _fake_apply_strict_parity(df):
        return df

    def _fake_detect_ticker_type(t):
        return "EQUITY"

    aggregate_call_count = {"n": 0}
    real_aggregate = impactsearch.process_primary_tickers_aggregate_mode

    def _spy_aggregate(*a, **kw):
        aggregate_call_count["n"] += 1
        return real_aggregate(*a, **kw)

    process_single_calls = []

    def _fake_process_single(prim, sec, sma_cache=None, analysis_clock=None,
                              *, rejection_out=None):
        process_single_calls.append(prim)
        if isinstance(rejection_out, dict):
            rejection_out.clear()
        return {
            "Total Capture (%)": 1.5,
            "Avg Daily Capture (%)": 0.01,
            "Trigger Days": 4,
            "Wins": 3,
            "Losses": 1,
            "Win Ratio (%)": 75.0,
            "Std Dev (%)": 0.5,
            "Sharpe Ratio": 0.8,
            "t-Statistic": 1.2,
            "p-Value": 0.2,
            "Significant 90%": "No",
            "Significant 95%": "No",
            "Significant 99%": "No",
            "Primary Ticker": prim,
            "Resolved/Fetched": prim,
            "Library Source": prim,
            "Data Source": "SLOW_PATH",
        }

    monkeypatch.setattr(impactsearch, "resolve_symbol", _fake_resolve)
    monkeypatch.setattr(impactsearch, "fetch_data_raw", _fake_fetch_data_raw)
    monkeypatch.setattr(impactsearch, "_coerce_to_close_frame", _fake_coerce)
    monkeypatch.setattr(impactsearch, "is_session_complete", _fake_session_complete)
    monkeypatch.setattr(impactsearch, "apply_strict_parity", _fake_apply_strict_parity)
    monkeypatch.setattr(impactsearch, "detect_ticker_type", _fake_detect_ticker_type)
    monkeypatch.setattr(impactsearch, "deduplicate_tickers", lambda lst: list(lst))
    monkeypatch.setattr(impactsearch, "process_single_ticker", _fake_process_single)
    monkeypatch.setattr(
        impactsearch, "process_primary_tickers_aggregate_mode", _spy_aggregate,
    )

    impactsearch.progress_tracker = {
        "total_tickers": 0,
        "current_index": 0,
        "results": [],
        "recent_errors": [],
        "current_ticker": "",
        "start_time": 0,
        "status": "starting",
    }

    out = impactsearch.process_primary_tickers(
        "SPY", ["AAA", "BBB"], use_multiprocessing=False, mark_complete=False,
    )

    assert isinstance(out, list)
    assert len(out) == 2
    for row in out:
        assert "Primary Ticker" in row
        assert "Total Capture (%)" in row
        assert "Sharpe Ratio" in row
        assert row["Secondary Ticker"] == "SPY"
        assert row["Data Source"] == "SLOW_PATH"

    assert aggregate_call_count["n"] == 0, (
        "Aggregate worker must NOT be invoked from the batch path; "
        "got call count " + str(aggregate_call_count["n"])
    )
    assert sorted(process_single_calls) == ["AAA", "BBB"]


# ---------------------------------------------------------------------------
# 3. Default mode in start_processing routes to batch
# ---------------------------------------------------------------------------


def test_impactsearch_start_processing_default_routes_to_batch(monkeypatch):
    """``multi_primary_mode=None`` must default to batch and call
    ``process_primary_tickers``, not the aggregate worker.
    """
    batch_calls = []
    agg_calls = []

    def _fake_batch(sec, primaries, use_multiprocessing=False, mark_complete=True, *, rejection_out=None):
        batch_calls.append((sec, list(primaries)))
        return [{"Primary Ticker": "AAA", "Secondary Ticker": sec}]

    def _fake_aggregate(sec, primaries, *, use_multiprocessing=False,
                        mark_complete=True, rejection_out=None):
        agg_calls.append((sec, list(primaries)))
        return {
            "row": {"Primary Ticker": "AGGREGATE(AAA)", "Secondary Ticker": sec},
            "aggregate_signal": pd.Series([], dtype=object),
            "status": "valid",
            "issues": [],
            "formatted_issues": [],
        }

    monkeypatch.setattr(impactsearch, "process_primary_tickers", _fake_batch)
    monkeypatch.setattr(
        impactsearch, "process_primary_tickers_aggregate_mode", _fake_aggregate,
    )

    class _SyncThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            if self._target is not None:
                self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(impactsearch.threading, "Thread", _SyncThread)

    cb = None
    for entry in impactsearch.app.callback_map.values():
        s = str(entry)
        if "process-button" in s and "interval-component" in s and "secondary-ticker-store" in s:
            inner = entry.get("callback")
            cb = getattr(inner, "__wrapped__", inner)
            break
    assert cb is not None, "Could not locate start_processing in callback_map"

    cb(1, "AAA", "SPY", [], None)

    assert len(batch_calls) >= 1, (
        "Default mode (None) must route to process_primary_tickers; "
        "batch_calls=" + repr(batch_calls)
    )
    assert agg_calls == [], (
        "Default mode must NOT call the aggregate worker; "
        "agg_calls=" + repr(agg_calls)
    )


# ---------------------------------------------------------------------------
# 4. Partial coverage surfaces with [IMPACTSEARCH:multi_primary_partial_coverage]
# ---------------------------------------------------------------------------


def test_impactsearch_aggregate_partial_coverage_surfaces():
    sig_df = _three_primary_sig_df()
    requested = list(sig_df.columns) + ["DDD"]

    result = impactsearch._impactsearch_multi_primary_contract_result(
        sig_df,
        requested_members=requested,
        contributed_members=list(sig_df.columns),
        context="multi-primary",
    )

    assert result["status"] == "partial"
    assert any(
        "[IMPACTSEARCH:multi_primary_partial_coverage]" in f
        for f in result["formatted_issues"]
    ), (
        "Expected [IMPACTSEARCH:multi_primary_partial_coverage] in "
        "formatted_issues; got " + repr(result["formatted_issues"])
    )
    assert any("DDD" in f for f in result["formatted_issues"])


# ---------------------------------------------------------------------------
# 5. Unavailable when all requested fail to contribute
# ---------------------------------------------------------------------------


def test_impactsearch_aggregate_unavailable_when_all_failed():
    requested = ["AAA", "BBB", "CCC"]

    result = impactsearch._impactsearch_multi_primary_contract_result(
        None,
        requested_members=requested,
        contributed_members=[],
        missing_members=requested,
        context="multi-primary",
    )

    assert result["status"] == "unavailable"
    assert isinstance(result["aggregate_signal"], pd.Series)
    assert result["aggregate_signal"].empty
    assert any(
        "[IMPACTSEARCH:multi_primary_unavailable]" in f
        for f in result["formatted_issues"]
    ), (
        "Expected [IMPACTSEARCH:multi_primary_unavailable] in "
        "formatted_issues; got " + repr(result["formatted_issues"])
    )


# ---------------------------------------------------------------------------
# 6. Valid status when all requested == contributed and triggers exist
# ---------------------------------------------------------------------------


def test_impactsearch_aggregate_valid_status_when_all_present():
    sig_df = _three_primary_sig_df()
    requested = list(sig_df.columns)

    result = impactsearch._impactsearch_multi_primary_contract_result(
        sig_df,
        requested_members=requested,
        contributed_members=requested,
        context="multi-primary",
    )

    assert result["status"] == "valid"
    assert result["issues"] == []
    assert result["formatted_issues"] == []


# ---------------------------------------------------------------------------
# 7. Duplicate active normalized ticker is invalid_input.
#     Path is BEFORE deduplicate_tickers (worker boundary).
# ---------------------------------------------------------------------------


def test_impactsearch_aggregate_input_invalid_for_duplicates(monkeypatch):
    requested = ["AAA", "AAA"]

    result = impactsearch._impactsearch_multi_primary_contract_result(
        None,
        requested_members=requested,
        contributed_members=[],
        context="multi-primary",
    )
    assert result["status"] == "invalid_input"
    assert any(
        "[IMPACTSEARCH:multi_primary_input_invalid]" in f
        for f in result["formatted_issues"]
    )

    dedup_calls = []
    real_dedup = impactsearch.deduplicate_tickers

    def _spy_dedup(lst):
        dedup_calls.append(list(lst))
        return real_dedup(lst)

    monkeypatch.setattr(impactsearch, "deduplicate_tickers", _spy_dedup)

    impactsearch.progress_tracker = {
        "total_tickers": 0,
        "current_index": 0,
        "results": [],
        "recent_errors": [],
        "current_ticker": "",
        "start_time": 0,
        "status": "starting",
    }

    worker_result = impactsearch.process_primary_tickers_aggregate_mode(
        "SPY", ["aaa", "AAA"], mark_complete=False,
    )
    assert worker_result["status"] == "invalid_input"
    assert any(
        "[IMPACTSEARCH:multi_primary_input_invalid]" in f
        for f in worker_result["formatted_issues"]
    )
    assert dedup_calls == [], (
        "deduplicate_tickers must NOT be called when duplicates are "
        "detected pre-dedupe in the aggregate worker; got " + repr(dedup_calls)
    )


# ---------------------------------------------------------------------------
# 7b. Alias duplicates (BRK.B + BRK-B) resolve to invalid_input
# ---------------------------------------------------------------------------


def test_impactsearch_aggregate_input_invalid_for_normalized_alias_duplicates(monkeypatch):
    """Two distinct operator-visible aliases that resolve to the same
    vendor symbol must be flagged as invalid_input. Worker boundary and
    contract helper layers both apply alias-aware detection BEFORE
    deduplicate_tickers (which itself stays unmodified).
    """

    def _fake_resolve(t):
        s = str(t).strip().upper()
        if s in ("BRK.B", "BRK-B"):
            return ("BRK-B", "BRK-B")
        return (s, s)

    monkeypatch.setattr(impactsearch, "resolve_symbol", _fake_resolve)

    # Direct contract helper invocation must surface invalid_input via
    # alias-aware duplicate detection.
    helper_result = impactsearch._impactsearch_multi_primary_contract_result(
        None,
        requested_members=["BRK.B", "BRK-B"],
        contributed_members=[],
        context="multi-primary",
    )
    assert helper_result["status"] == "invalid_input", (
        "Expected contract helper to return invalid_input for alias "
        "duplicates; got " + str(helper_result["status"])
    )
    assert any(
        "[IMPACTSEARCH:multi_primary_input_invalid]" in f
        for f in helper_result["formatted_issues"]
    ), (
        "Expected [IMPACTSEARCH:multi_primary_input_invalid] tag in "
        "helper formatted_issues; got "
        + repr(helper_result["formatted_issues"])
    )
    helper_msg = " ".join(helper_result["formatted_issues"])
    assert "BRK.B" in helper_msg and "BRK-B" in helper_msg, (
        "Expected both alias forms BRK.B and BRK-B in the helper issue "
        "message; got " + repr(helper_msg)
    )

    # Worker boundary must also flag the duplicate before
    # deduplicate_tickers is reached. Spy on deduplicate_tickers to
    # confirm it stays uninvoked on this rejection path.
    dedup_calls = []
    real_dedup = impactsearch.deduplicate_tickers

    def _spy_dedup(lst):
        dedup_calls.append(list(lst))
        return real_dedup(lst)

    monkeypatch.setattr(impactsearch, "deduplicate_tickers", _spy_dedup)

    impactsearch.progress_tracker = {
        "total_tickers": 0,
        "current_index": 0,
        "results": [],
        "recent_errors": [],
        "current_ticker": "",
        "start_time": 0,
        "status": "starting",
    }

    worker_result = impactsearch.process_primary_tickers_aggregate_mode(
        "SPY", ["BRK.B", "BRK-B"], mark_complete=False,
    )
    assert worker_result["status"] == "invalid_input"
    worker_msg = " ".join(worker_result["formatted_issues"])
    assert "[IMPACTSEARCH:multi_primary_input_invalid]" in worker_msg
    assert "BRK.B" in worker_msg and "BRK-B" in worker_msg, (
        "Expected both alias forms BRK.B and BRK-B in the worker issue "
        "message; got " + repr(worker_msg)
    )
    assert dedup_calls == [], (
        "deduplicate_tickers must NOT be called when alias duplicates "
        "are detected pre-dedupe in the aggregate worker; got "
        + repr(dedup_calls)
    )


# ---------------------------------------------------------------------------
# 8. no_overlap surfaces when sig_df has zero rows over contributed members
# ---------------------------------------------------------------------------


def test_impactsearch_aggregate_no_overlap_surfaces():
    empty_idx = pd.DatetimeIndex([])
    sig_df = pd.DataFrame(
        {"AAA": pd.Series(dtype=object), "BBB": pd.Series(dtype=object)},
        index=empty_idx,
    )

    result = impactsearch._impactsearch_multi_primary_contract_result(
        sig_df,
        requested_members=["AAA", "BBB"],
        contributed_members=["AAA", "BBB"],
        context="multi-primary",
    )

    assert result["status"] == "no_overlap"
    assert any(
        "[IMPACTSEARCH:multi_primary_no_overlap]" in f
        for f in result["formatted_issues"]
    ), (
        "Expected [IMPACTSEARCH:multi_primary_no_overlap] in "
        "formatted_issues; got " + repr(result["formatted_issues"])
    )


# ---------------------------------------------------------------------------
# 9. UI text distinguishes the two modes and the new help text
# ---------------------------------------------------------------------------


def test_impactsearch_ui_text_distinguishes_aggregate_and_batch_modes():
    impactsearch_path = PROJECT_DIR / "impactsearch.py"
    text = impactsearch_path.read_text(encoding="utf-8")

    assert "Batch evaluation across primaries" in text, (
        "Expected the batch-mode RadioItems label "
        "'Batch evaluation across primaries' in impactsearch.py."
    )
    assert "Canonical multi-primary aggregate" in text, (
        "Expected the aggregate-mode RadioItems label "
        "'Canonical multi-primary aggregate' in impactsearch.py."
    )
    assert (
        "Analyze batch primary effects or opt into canonical "
        "multi-primary consensus"
    ) in text, (
        "Expected the new header descriptive text about batch + "
        "canonical aggregate modes."
    )
    assert (
        "Batch mode evaluates each primary independently; canonical "
        "aggregate mode combines non-None unanimous primary signals "
        "under Algorithm Spec 18."
    ) in text, (
        "Expected the new Primary Tickers help paragraph that "
        "differentiates the two modes."
    )


# ---------------------------------------------------------------------------
# 10. Cross-app parity bootstrap for Spymaster / Confluence / ImpactSearch
# ---------------------------------------------------------------------------


def test_multi_primary_contract_parity_across_spymaster_confluence_impactsearch():
    """Bootstrap the locked 5B-MP parity contract: all three apps'
    contract wrappers MUST produce the same aggregate_signal as a
    direct call to canonical_scoring.combine_consensus_signals on
    the same member series.
    """
    import spymaster
    import confluence

    sig_df = _three_primary_sig_df()
    members = list(sig_df.columns)
    canonical = combine_consensus_signals([sig_df[c] for c in members])

    sm = spymaster._spymaster_multi_primary_contract_result(
        sig_df, context="multi-primary",
    )
    cf = confluence._mp_multi_primary_contract_result(
        sig_df,
        requested_members=members,
        contributed_members=members,
        context="multi-primary",
    )
    impct = impactsearch._impactsearch_multi_primary_contract_result(
        sig_df,
        requested_members=members,
        contributed_members=members,
        context="multi-primary",
    )

    pd.testing.assert_series_equal(
        sm["aggregate_signal"].astype(object),
        canonical.astype(object),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        cf["aggregate_signal"].astype(object),
        canonical.astype(object),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        impct["aggregate_signal"].astype(object),
        canonical.astype(object),
        check_names=False,
    )
