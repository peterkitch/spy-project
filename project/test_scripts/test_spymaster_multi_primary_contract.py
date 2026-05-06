"""
Phase 5B-MP-2a regression suite: Spymaster's multi-primary consensus
must route through the canonical helper
``canonical_scoring.combine_consensus_signals`` and surface
``multi_primary_contract_v1`` status + ``[SPYMASTER:...]`` reason
codes through existing UI return channels.

Tests construct the synthetic per-bar fixtures directly against
``spymaster._spymaster_multi_primary_contract_result`` and
``spymaster._spymaster_multi_primary_input_result``. The Dash app is
imported (Spymaster instantiates the app at module load) but no
server is started.

ASCII-only assertion messages per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import spymaster  # noqa: E402
from canonical_scoring import combine_consensus_signals  # noqa: E402


# ---------------------------------------------------------------------------
# A. Helper output matches the canonical reference
# ---------------------------------------------------------------------------


def test_spymaster_local_helper_matches_canonical_consensus():
    """A 3-primary aligned DataFrame produces the same aggregate via
    the Spymaster local wrapper as via a direct call to
    ``combine_consensus_signals``. This is the parity anchor: any drift
    between Spymaster and the canonical helper fails this test.
    """
    idx = pd.bdate_range("2024-01-02", periods=6)
    p1 = pd.Series(["Buy", "Buy", "Buy", "None", "Short", "None"], index=idx)
    p2 = pd.Series(["Buy", "Short", "None", "None", "Short", "None"], index=idx)
    p3 = pd.Series(["Buy", "Buy", "None", "None", "Short", "None"], index=idx)
    signals_df = pd.DataFrame({"p1": p1, "p2": p2, "p3": p3})

    result = spymaster._spymaster_multi_primary_contract_result(
        signals_df, context="multi-primary",
    )

    assert isinstance(result, dict)
    expected = combine_consensus_signals([p1, p2, p3])
    pd.testing.assert_series_equal(
        result["aggregate_signal"].astype(object),
        expected.astype(object),
        check_names=False,
    )

    # Status must be either valid (Buy/Short present in aggregate) or
    # no_triggers (all-None aggregate). The fixture above has triggers,
    # so it must be valid. Either way, the helper never silently emits
    # status that disagrees with the actual aggregate content.
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
# B. Single-primary degradation
# ---------------------------------------------------------------------------


def test_spymaster_single_primary_degradation():
    """When N=1 the aggregate equals the lone direction-tagged signal.
    multi_primary_contract_v1 explicitly degrades to single-primary
    semantics in this case (see canonical contract doc Section 3).
    """
    idx = pd.bdate_range("2024-02-01", periods=4)
    only = pd.Series(["Buy", "None", "Short", "Buy"], index=idx)
    signals_df = pd.DataFrame({"solo": only})

    result = spymaster._spymaster_multi_primary_contract_result(
        signals_df, context="multi-primary",
    )

    assert result["status"] == "valid"
    assert result["issues"] == []
    pd.testing.assert_series_equal(
        result["aggregate_signal"].astype(object),
        only.astype(object),
        check_names=False,
    )


# ---------------------------------------------------------------------------
# C. All-muted is invalid_input
# ---------------------------------------------------------------------------


def test_spymaster_all_muted_invalid_input():
    tickers = ["AAA", "BBB", "CCC"]
    mute_flags = [True, True, True]

    result = spymaster._spymaster_multi_primary_input_result(
        tickers, mute_flags=mute_flags, context="multi-primary",
    )

    assert result["status"] == "invalid_input"
    assert result["active_tickers"] == []
    assert result["issues"], "Expected at least one issue line"
    issue = result["issues"][0]
    assert "[SPYMASTER:multi_primary_input_invalid]" in issue, (
        "Missing [SPYMASTER:multi_primary_input_invalid] tag in issue: "
        + repr(issue)
    )


# ---------------------------------------------------------------------------
# D. Duplicate active primary is invalid_input
# ---------------------------------------------------------------------------


def test_spymaster_duplicate_active_primary_invalid_input():
    # 'aaa' and 'AAA' normalize to the same ticker; the duplicate must
    # be flagged before the contract is invoked.
    tickers = ["aaa", "BBB", "AAA"]
    mute_flags = [False, False, False]

    result = spymaster._spymaster_multi_primary_input_result(
        tickers, mute_flags=mute_flags, context="multi-primary",
    )

    assert result["status"] == "invalid_input"
    assert result["issues"], "Expected at least one issue line"
    issue = result["issues"][0]
    assert "[SPYMASTER:multi_primary_input_invalid]" in issue, (
        "Missing [SPYMASTER:multi_primary_input_invalid] tag in issue: "
        + repr(issue)
    )
    assert "AAA" in issue, (
        "Issue should name the duplicated normalized ticker: " + repr(issue)
    )


# ---------------------------------------------------------------------------
# D2. Whitespace-only inputs are invalid_input (no silent active inclusion)
# ---------------------------------------------------------------------------


def test_spymaster_whitespace_only_inputs_invalid_input():
    """A whitespace-only ticker slot must NOT survive into the active
    list. The helper must strip and skip such inputs, leaving the
    active list empty and surfacing the standard invalid_input
    reason code so operators do not see a silently 'valid' run with
    zero active primaries.
    """
    result = spymaster._spymaster_multi_primary_input_result(
        ["   "], mute_flags=[False], context="multi-primary",
    )

    assert result["status"] == "invalid_input"
    assert result["active_tickers"] == []
    assert result["issues"], "Expected at least one issue line"
    assert any(
        "[SPYMASTER:multi_primary_input_invalid]" in issue
        for issue in result["issues"]
    ), (
        "Expected at least one issue tagged "
        "[SPYMASTER:multi_primary_input_invalid]; got: "
        + repr(result["issues"])
    )


# ---------------------------------------------------------------------------
# E. Empty aligned DataFrame is no_overlap
# ---------------------------------------------------------------------------


def test_spymaster_no_common_dates_no_overlap():
    """An aligned DataFrame with columns but zero rows represents the
    case where primaries' evaluation grids do not overlap. The contract
    must surface no_overlap rather than silently returning an empty
    aggregate marked 'valid'.
    """
    empty_idx = pd.DatetimeIndex([])
    signals_df = pd.DataFrame(
        {"p1": pd.Series(dtype=object), "p2": pd.Series(dtype=object)},
        index=empty_idx,
    )

    result = spymaster._spymaster_multi_primary_contract_result(
        signals_df, context="multi-primary",
    )

    assert result["status"] == "no_overlap"
    assert result["issues"], "Expected at least one issue line"
    issue = result["issues"][0]
    assert "[SPYMASTER:multi_primary_no_overlap]" in issue, (
        "Missing [SPYMASTER:multi_primary_no_overlap] tag in issue: "
        + repr(issue)
    )


# ---------------------------------------------------------------------------
# F. UI text reflects the canonical-contract wording
# ---------------------------------------------------------------------------


def test_spymaster_ui_text_consensus_wording():
    """Phase 5B-MP-2a updates the operator-facing multi-primary text per
    Item 3 pattern. This guard pins the new wording and the absence of
    the prior generic 'aggregated trading strategy' phrasing so future
    PRs cannot regress the user-visible language.
    """
    spymaster_path = PROJECT_DIR / "spymaster.py"
    text = spymaster_path.read_text(encoding="utf-8")

    assert "non-None unanimous" in text, (
        "Expected 'non-None unanimous' in spymaster.py UI/Help text "
        "after Phase 5B-MP-2a migration."
    )
    assert "Consensus Signals from Multiple Primary Tickers" in text, (
        "Expected 'Consensus Signals from Multiple Primary Tickers' as "
        "the migrated card-header label."
    )
    assert "Aggregate Signals from Multiple Primary Tickers" not in text, (
        "Old 'Aggregate Signals from Multiple Primary Tickers' label "
        "must be replaced by the canonical-contract wording."
    )
    assert (
        "Combine signals from multiple primary tickers to create an "
        "aggregated trading strategy"
    ) not in text, (
        "Old generic aggregation sentence must not remain in spymaster.py."
    )
