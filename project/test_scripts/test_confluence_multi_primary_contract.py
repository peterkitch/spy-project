"""
Phase 5B-MP-2b regression suite: Confluence's multi-primary
diagnostic surface must conform to the locked
``multi_primary_contract_v1`` failure semantics.

Confluence already delegates per-bar consensus to
``canonical_scoring.combine_consensus_signals`` via the byte-identical
``_mp_combine_unanimity_vectorized``. This phase only adds operator-
visible classification (``valid`` / ``partial`` / ``unavailable`` /
``no_overlap`` / ``no_triggers``) and ``[CONFLUENCE:...]`` reason
codes; the aggregate signal MUST remain unchanged for any given
contributed-member input.

Helpers are called directly. The Dash app is imported (Confluence
constructs the app at module load) but no server is started.

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

import confluence  # noqa: E402
from canonical_scoring import combine_consensus_signals  # noqa: E402


def _make_three_primary_sig_df():
    idx = pd.bdate_range("2024-01-02", periods=6)
    p1 = pd.Series(["Buy", "Buy", "None", "Short", "None", "Buy"], index=idx)
    p2 = pd.Series(["Buy", "None", "None", "Short", "Buy", "Buy"], index=idx)
    p3 = pd.Series(["Buy", "Buy", "None", "Short", "Short", "Buy"], index=idx)
    return pd.DataFrame({"AAA": p1, "BBB": p2, "CCC": p3})


# ---------------------------------------------------------------------------
# A. Wrapper aggregate matches the canonical reference
# ---------------------------------------------------------------------------


def test_confluence_local_helper_matches_canonical_consensus():
    sig_df = _make_three_primary_sig_df()
    result = confluence._mp_multi_primary_contract_result(
        sig_df,
        requested_members=list(sig_df.columns),
        contributed_members=list(sig_df.columns),
        context="multi-primary[1d]",
    )

    expected_via_helper = confluence._mp_combine_unanimity_vectorized(sig_df)
    expected_via_canonical = combine_consensus_signals(
        [sig_df[c] for c in sig_df.columns]
    )
    pd.testing.assert_series_equal(
        expected_via_helper.astype(object),
        expected_via_canonical.astype(object),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        result["aggregate_signal"].astype(object),
        expected_via_helper.astype(object),
        check_names=False,
    )

    assert result["status"] == "valid", (
        "Expected status='valid' for a fully-contributed sig_df with triggers; "
        "got " + repr(result["status"])
    )
    assert result["issues"] == []


# ---------------------------------------------------------------------------
# B. Partial-coverage surfaces with [CONFLUENCE:multi_primary_partial_coverage]
# ---------------------------------------------------------------------------


def test_confluence_partial_coverage_surfaces():
    sig_df = _make_three_primary_sig_df()
    requested = list(sig_df.columns) + ["DDD"]
    result = confluence._mp_multi_primary_contract_result(
        sig_df,
        requested_members=requested,
        contributed_members=list(sig_df.columns),
        context="multi-primary[1d]",
    )

    assert result["status"] == "partial"
    assert result["issues"], "Expected at least one issue line"
    assert any(
        "[CONFLUENCE:multi_primary_partial_coverage]" in issue
        for issue in result["issues"]
    ), (
        "Expected at least one issue tagged "
        "[CONFLUENCE:multi_primary_partial_coverage]; got: "
        + repr(result["issues"])
    )
    assert any("DDD" in issue for issue in result["issues"]), (
        "Expected the missing primary 'DDD' to be named in an issue line; "
        "got: " + repr(result["issues"])
    )


# ---------------------------------------------------------------------------
# C. Unavailable when all requested primaries failed to contribute
# ---------------------------------------------------------------------------


def test_confluence_unavailable_when_all_missing():
    requested = ["AAA", "BBB", "CCC"]
    result = confluence._mp_multi_primary_contract_result(
        None,
        requested_members=requested,
        contributed_members=[],
        missing_members=requested,
        context="multi-primary[1d]",
    )

    assert result["status"] == "unavailable"
    assert isinstance(result["aggregate_signal"], pd.Series)
    assert result["aggregate_signal"].empty
    assert any(
        "[CONFLUENCE:multi_primary_unavailable]" in issue
        for issue in result["issues"]
    ), (
        "Expected [CONFLUENCE:multi_primary_unavailable] in issues; got: "
        + repr(result["issues"])
    )


# ---------------------------------------------------------------------------
# D. Valid when all requested == all contributed and triggers exist
# ---------------------------------------------------------------------------


def test_confluence_valid_status_when_all_present():
    sig_df = _make_three_primary_sig_df()
    requested = list(sig_df.columns)
    result = confluence._mp_multi_primary_contract_result(
        sig_df,
        requested_members=requested,
        contributed_members=requested,
        context="multi-primary[1d]",
    )

    assert result["status"] == "valid"
    assert result["issues"] == []


# ---------------------------------------------------------------------------
# E. Aggregate is identical when partial-coverage status is added
#     (behavioral isolation: only the diagnostic changes; aggregate is
#     byte-identical for the same contributed-member sig_df).
# ---------------------------------------------------------------------------


def test_confluence_aggregate_signal_unchanged_when_partial_status_added():
    sig_df = _make_three_primary_sig_df()
    reference = confluence._mp_combine_unanimity_vectorized(sig_df)

    full = confluence._mp_multi_primary_contract_result(
        sig_df,
        requested_members=list(sig_df.columns),
        contributed_members=list(sig_df.columns),
        context="multi-primary[1d]",
    )
    partial = confluence._mp_multi_primary_contract_result(
        sig_df,
        requested_members=list(sig_df.columns) + ["DDD"],
        contributed_members=list(sig_df.columns),
        context="multi-primary[1d]",
    )

    pd.testing.assert_series_equal(
        full["aggregate_signal"].astype(object),
        reference.astype(object),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        partial["aggregate_signal"].astype(object),
        reference.astype(object),
        check_names=False,
    )
    assert partial["status"] == "partial"
    assert any(
        "[CONFLUENCE:multi_primary_partial_coverage]" in issue
        for issue in partial["issues"]
    )
    assert any("DDD" in issue for issue in partial["issues"])


# ---------------------------------------------------------------------------
# F. UI text reflects the new contract wording
# ---------------------------------------------------------------------------


def test_confluence_ui_text_consensus_wording():
    confluence_path = PROJECT_DIR / "confluence.py"
    text = confluence_path.read_text(encoding="utf-8")

    assert "non-None unanimous primary signals" in text, (
        "Expected new descriptive text about non-None unanimous "
        "primary signals in confluence.py near the Multi-Primary "
        "Signal Aggregator UI."
    )
    assert "missing active primaries are surfaced as partial coverage" in text, (
        "Expected explanatory text noting that missing active "
        "primaries surface as partial coverage."
    )
    assert "Multi-Primary Signal Aggregator" in text, (
        "Qualified label 'Multi-Primary Signal Aggregator' must remain "
        "(Item 4 allowlist)."
    )


# ---------------------------------------------------------------------------
# G. Recommended end-to-end: run_multi_primary_analysis status carries
#     [CONFLUENCE:...] when at least one primary is missing
# ---------------------------------------------------------------------------


def test_run_multi_primary_analysis_status_column_includes_confluence_prefix(monkeypatch):
    """Drive run_multi_primary_analysis with one contributing primary
    and one missing primary; assert the returned table payload's row
    Status column carries a [CONFLUENCE:multi_primary_partial_coverage]
    tag. Dash is not started; we extract the underlying user function
    from app.callback_map and call it directly.
    """
    secondary = "SPY"
    contributing_primary = "AAA"
    missing_primary = "BBB"

    sec_idx = pd.bdate_range("2024-01-02", periods=8)
    sec_df = pd.DataFrame(
        {"Close": np.linspace(100.0, 107.0, len(sec_idx))},
        index=sec_idx,
    )

    def _fake_fetch(ticker, interval):
        if ticker == secondary:
            return sec_df.copy()
        return None

    contributing_lib = {
        "dates": list(sec_idx),
        "primary_signals": ["Buy"] * len(sec_idx),
    }

    def _fake_load(ticker, interval):
        if ticker == contributing_primary:
            return contributing_lib
        return None

    monkeypatch.setattr(confluence, "_cached_fetch_interval_data", _fake_fetch)
    monkeypatch.setattr(
        confluence, "_cached_load_signal_library_interval", _fake_load,
    )

    cb = None
    for entry in confluence.app.callback_map.values():
        s = str(entry)
        if "run-multi-primary" in s and "mp-last-run" in s:
            inner = entry.get("callback")
            cb = getattr(inner, "__wrapped__", inner)
            break
    if cb is None:
        # Locate by output id pair as a fallback.
        for key, entry in confluence.app.callback_map.items():
            if "multi-primary-results" in str(key) and "mp-last-run" in str(key):
                inner = entry.get("callback")
                cb = getattr(inner, "__wrapped__", inner)
                break
    assert cb is not None, (
        "Could not locate run_multi_primary_analysis callback in "
        "confluence.app.callback_map"
    )

    result = cb(
        1,
        secondary,
        [contributing_primary, missing_primary],
        [[], []],
        [[], []],
        ["1d"],
    )
    assert isinstance(result, tuple) and len(result) == 2, (
        "run_multi_primary_analysis must return a (children, mp_ctx) tuple; "
        "got: " + repr(type(result))
    )
    children, mp_ctx = result
    payload = str(children)

    assert "[CONFLUENCE:" in payload, (
        "Expected at least one [CONFLUENCE:...] tag in the rendered "
        "multi-primary results payload after a partial-coverage run; "
        "did not find one."
    )
    assert "[CONFLUENCE:multi_primary_partial_coverage]" in payload, (
        "Expected the partial-coverage tag specifically; got payload "
        "without it."
    )
    assert isinstance(mp_ctx, dict)
    assert mp_ctx.get("partial_coverage_issues"), (
        "Expected mp_ctx to carry partial_coverage_issues for the "
        "Apply-to-Analyze banner to consume."
    )
