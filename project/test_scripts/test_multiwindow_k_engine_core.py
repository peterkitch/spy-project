"""Phase 6I-21 tests for multiwindow_k_engine_core.

Pins the engine-core contract:

  - No forbidden top-level imports (writer / refresher /
    pipeline runner / live engines / yfinance / dash /
    subprocess).
  - This module is NOT a projection / bridge: no
    ``trafficflow_multitimeframe_bridge`` import, no
    ``pandas`` / ``numpy`` import, no ``resample`` /
    ``ffill`` token in the source.
  - Direct vs Inverse protocol applied per member before
    combine.
  - K threshold gates the combined signal.
  - Buy / Short / mixed / None combine rule.
  - Capture math: Buy = +pct_change, Short = -pct_change,
    None = 0.
  - Grid evaluator emits exactly 60 cells when supplied
    all 12 canonical K values AND all 5 canonical windows
    with complete per-window inputs.
  - Missing windows / K inputs do NOT silently fabricate
    cells.
"""
from __future__ import annotations

import ast
import math
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import multiwindow_k_engine_core as core  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_dates(n: int) -> list[str]:
    return [f"2026-01-{i+1:02d}" for i in range(n)]


def _closes_5_5pct() -> list[float]:
    """Five bars: 100 -> 105 -> 110.25 -> 115.7625 -> 121.550625.
    Each bar is +5% on the previous. pct_change in percent
    terms: 0.0, 5.0, 5.0, 5.0, 5.0."""
    closes = [100.0]
    for _ in range(4):
        closes.append(closes[-1] * 1.05)
    return closes


# ---------------------------------------------------------------------------
# 1. Forbidden imports + no-projection-module proof
# ---------------------------------------------------------------------------


def test_core_module_has_no_forbidden_imports():
    """The core must not import any writer / refresher /
    pipeline runner / live engine / yfinance / dash /
    subprocess at top level. Critically it also must NOT
    import the Phase 6D-2 ``trafficflow_multitimeframe_bridge``
    — this is engine math, NOT projection."""
    src = Path(core.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_first_segment = {
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
        "yfinance",
        "dash",
        "spymaster",
        "trafficflow",
        "stackbuilder",
        "onepass",
        "impactsearch",
        "confluence",
        "cross_ticker_confluence",
        "daily_signal_board",
        "subprocess",
    }
    forbidden_exact_modules = {
        # The Phase 6D-2 bridge is a projection module; we
        # are explicitly not it.
        "trafficflow_multitimeframe_bridge",
        # The Phase 6I-20 audit + Phase 6I-19 brief are
        # consumer surfaces; the core must not depend on
        # them.
        "multiwindow_k_engine_gap_audit",
        "confluence_decision_brief",
    }
    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad_first = [
        m for m in found
        if m.split(".")[0] in forbidden_first_segment
    ]
    bad_exact = [
        m for m in found if m in forbidden_exact_modules
    ]
    assert not bad_first, (
        f"forbidden first-segment import in core: "
        f"{bad_first!r}"
    )
    assert not bad_exact, (
        f"forbidden exact-module import in core: "
        f"{bad_exact!r}"
    )


def test_core_is_not_projection_no_pandas_or_resample():
    """The engine core operates on in-memory window-specific
    inputs and does NOT resample / ffill. To prove that
    discipline at the source-code level: the module must
    NOT import ``pandas`` or ``numpy`` at top level AND must
    NOT contain ``resample`` / ``ffill`` tokens anywhere in
    the source."""
    src = Path(core.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_imports: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_imports.append(node.module)
    assert "pandas" not in top_imports, (
        "core must not import pandas at top level"
    )
    assert "numpy" not in top_imports, (
        "core must not import numpy at top level"
    )
    # Use AST-level scanning so the module docstring can
    # discuss what this module is NOT (i.e. it can use the
    # words "resample" and "ffill" inside string literals)
    # without false-flagging. The actual rule is "no call
    # to .resample() or .ffill() anywhere in code".
    forbidden_call_names = {"resample", "ffill"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name in forbidden_call_names:
                raise AssertionError(
                    f"core makes a forbidden call to "
                    f"{name!r}() -- projection logic "
                    "belongs to the Phase 6D-2 bridge, "
                    "not this engine core"
                )


# ---------------------------------------------------------------------------
# 2. Direct vs Inverse protocol
# ---------------------------------------------------------------------------


def test_direct_protocol_passes_buy_through():
    """One member, Direct protocol, raw Buy on every bar
    -> combined Buy on every bar (K=1)."""
    n = 5
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=_closes_5_5pct(),
        member_signal_columns={"A": ["Buy"] * n},
        member_protocols={"A": core.PROTOCOL_DIRECT},
    )
    assert cell.latest_combined_signal == core.SIGNAL_BUY
    assert cell.trigger_days == n - 1 + 1  # all bars fire


def test_inverse_protocol_flips_buy_to_short():
    """One member, Inverse protocol, raw Buy on every bar
    -> combined Short on every bar (K=1)."""
    n = 5
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=_closes_5_5pct(),
        member_signal_columns={"A": ["Buy"] * n},
        member_protocols={"A": core.PROTOCOL_INVERSE},
    )
    assert cell.latest_combined_signal == core.SIGNAL_SHORT


def test_inverse_protocol_flips_short_to_buy():
    n = 5
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=_closes_5_5pct(),
        member_signal_columns={"A": ["Short"] * n},
        member_protocols={"A": core.PROTOCOL_INVERSE},
    )
    assert cell.latest_combined_signal == core.SIGNAL_BUY


def test_unknown_protocol_defaults_to_direct():
    """An unknown protocol string is treated as Direct
    (no-flip pass-through)."""
    n = 3
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 101.0, 102.0],
        member_signal_columns={"A": ["Buy"] * n},
        member_protocols={"A": "X"},
    )
    assert cell.latest_combined_signal == core.SIGNAL_BUY


# ---------------------------------------------------------------------------
# 3. K threshold behavior
# ---------------------------------------------------------------------------


def test_k_threshold_below_threshold_returns_none():
    """Two members, both Buy; K=3 -> below threshold -> None."""
    n = 4
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=3,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 101.0, 102.0, 103.0],
        member_signal_columns={
            "A": ["Buy"] * n,
            "B": ["Buy"] * n,
        },
    )
    assert (
        cell.latest_combined_signal == core.SIGNAL_NONE
    )
    assert cell.trigger_days == 0
    assert cell.total_capture_pct == 0.0


def test_k_threshold_meets_threshold_emits_signal():
    """Three members, all Buy; K=3 -> meets threshold -> Buy."""
    n = 4
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=3,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 101.0, 102.0, 103.0],
        member_signal_columns={
            "A": ["Buy"] * n,
            "B": ["Buy"] * n,
            "C": ["Buy"] * n,
        },
    )
    assert cell.latest_combined_signal == core.SIGNAL_BUY
    assert cell.trigger_days == n  # all bars fire


# ---------------------------------------------------------------------------
# 4. Combine rule: Buy / Short / mixed / None
# ---------------------------------------------------------------------------


def test_combine_all_buy_yields_buy():
    n = 2
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 101.0],
        member_signal_columns={
            "A": ["Buy", "Buy"],
            "B": ["Buy", "Buy"],
        },
    )
    assert cell.latest_combined_signal == core.SIGNAL_BUY


def test_combine_all_short_yields_short():
    n = 2
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 101.0],
        member_signal_columns={
            "A": ["Short", "Short"],
            "B": ["Short", "Short"],
        },
    )
    assert cell.latest_combined_signal == core.SIGNAL_SHORT


def test_combine_mixed_buy_and_short_yields_none():
    """Mixed Buy and Short on the same bar -> None
    (canonical PRJCT9 combine rule; strict-unanimity over
    active members)."""
    n = 2
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 101.0],
        member_signal_columns={
            "A": ["Buy", "Buy"],
            "B": ["Short", "Short"],
        },
    )
    assert cell.latest_combined_signal == core.SIGNAL_NONE
    assert cell.trigger_days == 0


def test_combine_all_none_yields_none():
    n = 2
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 101.0],
        member_signal_columns={
            "A": ["None", "None"],
            "B": ["None", "None"],
        },
    )
    assert cell.latest_combined_signal == core.SIGNAL_NONE
    assert cell.trigger_days == 0


def test_combine_missing_members_ignored_in_agreement():
    """A member marked 'missing' must NOT count toward the
    combine. Two members: A=Buy, B=missing, K=1 -> active
    set {A: Buy} -> Buy."""
    n = 2
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 101.0],
        member_signal_columns={
            "A": ["Buy", "Buy"],
            "B": ["missing", "missing"],
        },
    )
    assert cell.latest_combined_signal == core.SIGNAL_BUY
    assert cell.latest_buy_count == 1
    assert cell.latest_missing_count == 1
    assert cell.member_count == 2


# ---------------------------------------------------------------------------
# 5. Capture math
# ---------------------------------------------------------------------------


def test_capture_math_buy_takes_positive_pct_change():
    """All-Buy member, K=1, target close 100 -> 110 (one
    bar +10%): combined Buy on the second bar; first bar's
    pct_change is 0; trigger_days=2 (both bars fire because
    combined is Buy on both, but bar 0 has 0% return).
    total_capture_pct = 0.0 (bar 0) + 10.0 (bar 1) = 10.0;
    avg = 5.0; wins = 1; losses = 0."""
    n = 2
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 110.0],
        member_signal_columns={"A": ["Buy", "Buy"]},
    )
    assert cell.trigger_days == 2
    assert math.isclose(cell.total_capture_pct, 10.0)
    assert math.isclose(cell.avg_daily_capture_pct, 5.0)
    assert cell.wins == 1
    assert cell.losses == 0


def test_capture_math_short_inverts_pct_change():
    """All-Short combined: capture = -pct_change. Bar 0=100
    -> bar 1=110 (a +10% return on the target) maps to
    Short capture -10. total = -10; wins = 0; losses = 1."""
    n = 2
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 110.0],
        member_signal_columns={"A": ["Short", "Short"]},
    )
    assert cell.trigger_days == 2
    assert math.isclose(cell.total_capture_pct, -10.0)
    assert math.isclose(cell.avg_daily_capture_pct, -5.0)
    assert cell.wins == 0
    assert cell.losses == 1


def test_capture_math_none_contributes_zero():
    """None bars are not trigger days and contribute 0
    capture."""
    n = 2
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 110.0],
        member_signal_columns={"A": ["None", "None"]},
    )
    assert cell.trigger_days == 0
    assert cell.total_capture_pct == 0.0
    assert cell.avg_daily_capture_pct == 0.0
    assert cell.wins == 0
    assert cell.losses == 0


def test_sharpe_zero_when_only_one_trigger_day():
    """Sharpe is 0.0 when there are fewer than 2 trigger
    days (insufficient data for sample std)."""
    n = 3
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 100.0, 105.0],
        member_signal_columns={
            "A": ["None", "Buy", "None"],
        },
    )
    assert cell.trigger_days == 1
    assert cell.sharpe_ratio == 0.0


def test_sharpe_positive_when_consistent_winning_trigger_days():
    """Sharpe > 0 when trigger captures have positive mean
    and non-zero std. Two-bar +5% return both bars, all-Buy
    -> trigger captures [0.0, 5.0]; mean=2.5; std=sqrt
    (12.5)=3.5355; sharpe ~ 0.707."""
    n = 3
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 105.0, 110.25],
        member_signal_columns={"A": ["Buy"] * n},
    )
    assert cell.trigger_days == 3
    assert math.isclose(
        cell.avg_daily_capture_pct,
        (0.0 + 5.0 + 5.0) / 3.0,
    )
    # Three trigger captures: 0, 5, 5. Mean = 10/3.
    # Variance (ddof=1) = sum((x - mean)^2) / 2.
    mean = (0.0 + 5.0 + 5.0) / 3.0
    var = (
        (0.0 - mean) ** 2 + (5.0 - mean) ** 2
        + (5.0 - mean) ** 2
    ) / 2.0
    std = math.sqrt(var)
    expected_sharpe = mean / std
    assert math.isclose(
        cell.sharpe_ratio, expected_sharpe,
    )


# ---------------------------------------------------------------------------
# 6. Grid evaluator: 60 canonical cells
# ---------------------------------------------------------------------------


def _full_per_window_inputs() -> dict[str, dict[str, Any]]:
    """Build a per_window_inputs dict covering all five
    canonical windows. Each window uses the same shape (two
    members, three bars, +5% target close every bar) so the
    test focuses on coverage, not per-window-specific math."""
    n = 3
    closes = [100.0, 105.0, 110.25]
    dates = _simple_dates(n)
    member_cols = {
        "A": ["Buy"] * n,
        "B": ["Buy"] * n,
    }
    out: dict[str, dict[str, Any]] = {}
    for window in core.CANONICAL_WINDOWS:
        out[window] = {
            "dates": list(dates),
            "target_close": list(closes),
            "member_signal_columns": {
                m: list(v)
                for m, v in member_cols.items()
            },
        }
    return out


def test_evaluate_grid_emits_60_cells_for_full_canonical_inputs():
    """All 12 canonical K values × all 5 canonical windows
    × complete per-window inputs -> exactly 60 cells, one
    per ``(K, window)`` pair."""
    cells = core.evaluate_grid(
        target_ticker="SPY",
        K_values=core.CANONICAL_K_VALUES,
        windows=core.CANONICAL_WINDOWS,
        per_window_inputs=_full_per_window_inputs(),
    )
    assert len(cells) == 60
    observed = {(c.K, c.window) for c in cells}
    expected = {
        (k, w)
        for k in core.CANONICAL_K_VALUES
        for w in core.CANONICAL_WINDOWS
    }
    assert observed == expected


def test_evaluate_grid_payload_matches_phase_6i20_required_fields():
    """The Phase 6I-20 audit's
    ``_REQUIRED_PER_WINDOW_K_METRIC_FIELDS`` are K / window
    / total_capture_pct / sharpe_ratio / trigger_days. The
    cells_to_per_window_k_metrics_payload helper must emit
    those five keys on every cell (plus extras)."""
    cells = core.evaluate_grid(
        target_ticker="SPY",
        K_values=core.CANONICAL_K_VALUES,
        windows=core.CANONICAL_WINDOWS,
        per_window_inputs=_full_per_window_inputs(),
    )
    payload = core.cells_to_per_window_k_metrics_payload(
        cells,
    )
    assert len(payload) == 60
    required = {
        "K",
        "window",
        "total_capture_pct",
        "sharpe_ratio",
        "trigger_days",
    }
    for entry in payload:
        assert required.issubset(set(entry.keys()))


# ---------------------------------------------------------------------------
# 7. Missing window or K input does NOT fabricate cells
# ---------------------------------------------------------------------------


def test_grid_skips_window_without_inputs():
    """A window listed in ``windows`` but not present in
    ``per_window_inputs`` is silently skipped — no
    fabricated cell."""
    inputs = _full_per_window_inputs()
    # Remove one window entirely.
    inputs.pop("1y")
    cells = core.evaluate_grid(
        target_ticker="SPY",
        K_values=core.CANONICAL_K_VALUES,
        windows=core.CANONICAL_WINDOWS,
        per_window_inputs=inputs,
    )
    assert len(cells) == 48  # 12 K * 4 windows
    observed_windows = {c.window for c in cells}
    assert "1y" not in observed_windows


def test_grid_skips_window_with_incomplete_inputs_block():
    """A per-window inputs dict that omits any of the three
    required input keys is silently skipped — no fabricated
    cell."""
    inputs = _full_per_window_inputs()
    # Drop the target_close from one window.
    del inputs["1mo"]["target_close"]
    cells = core.evaluate_grid(
        target_ticker="SPY",
        K_values=core.CANONICAL_K_VALUES,
        windows=core.CANONICAL_WINDOWS,
        per_window_inputs=inputs,
    )
    assert len(cells) == 48  # 12 K * 4 windows
    observed_windows = {c.window for c in cells}
    assert "1mo" not in observed_windows


def test_grid_empty_k_values_yields_no_cells():
    """Empty ``K_values`` yields zero cells even when every
    canonical window is supplied."""
    cells = core.evaluate_grid(
        target_ticker="SPY",
        K_values=(),
        windows=core.CANONICAL_WINDOWS,
        per_window_inputs=_full_per_window_inputs(),
    )
    assert cells == ()


def test_grid_empty_windows_yields_no_cells():
    """Empty ``windows`` yields zero cells even when
    per_window_inputs is fully populated."""
    cells = core.evaluate_grid(
        target_ticker="SPY",
        K_values=core.CANONICAL_K_VALUES,
        windows=(),
        per_window_inputs=_full_per_window_inputs(),
    )
    assert cells == ()


# ---------------------------------------------------------------------------
# 8. Length-alignment safety
# ---------------------------------------------------------------------------


def test_evaluate_cell_raises_on_length_mismatch_target():
    import pytest
    with pytest.raises(ValueError):
        core.evaluate_cell(
            target_ticker="SPY",
            K=1,
            window="1d",
            dates=_simple_dates(3),
            target_close=[100.0, 101.0],  # length 2 != 3
            member_signal_columns={"A": ["Buy"] * 3},
        )


def test_evaluate_cell_raises_on_length_mismatch_member():
    import pytest
    with pytest.raises(ValueError):
        core.evaluate_cell(
            target_ticker="SPY",
            K=1,
            window="1d",
            dates=_simple_dates(3),
            target_close=[100.0, 101.0, 102.0],
            # length 2 != 3
            member_signal_columns={"A": ["Buy", "Buy"]},
        )


# ---------------------------------------------------------------------------
# 9. Latest signal counts mirror final bar
# ---------------------------------------------------------------------------


def test_latest_counts_reflect_final_bar_counts():
    """The latest_* counts must match the per-member shape
    at the final bar after protocol is applied."""
    n = 3
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=1,
        window="1d",
        dates=_simple_dates(n),
        target_close=[100.0, 100.0, 100.0],
        member_signal_columns={
            "A": ["None", "None", "Buy"],
            "B": ["None", "Buy", "Short"],
            "C": ["missing", "Short", "missing"],
            "D": ["Buy", "None", "None"],
        },
        member_protocols={"D": core.PROTOCOL_INVERSE},
    )
    # Final-bar raw signals after protocol:
    #   A (Direct): Buy
    #   B (Direct): Short
    #   C (Direct): missing
    #   D (Inverse): None -> None
    assert cell.latest_buy_count == 1
    assert cell.latest_short_count == 1
    assert cell.latest_none_count == 1
    assert cell.latest_missing_count == 1
    assert cell.member_count == 4


# ---------------------------------------------------------------------------
# 10. Round-trip into Phase 6I-20 contract shape
# ---------------------------------------------------------------------------


def test_to_dict_round_trip_keys_match_required_fields():
    cell = core.evaluate_cell(
        target_ticker="SPY",
        K=6,
        window="1wk",
        dates=_simple_dates(3),
        target_close=[100.0, 105.0, 110.25],
        member_signal_columns={"A": ["Buy", "Buy", "Buy"]},
    )
    d = cell.to_dict()
    for key in (
        "K", "window", "total_capture_pct",
        "sharpe_ratio", "trigger_days",
        "avg_daily_capture_pct", "wins", "losses",
        "latest_combined_signal", "latest_buy_count",
        "latest_short_count", "latest_none_count",
        "latest_missing_count", "member_count",
    ):
        assert key in d


# ---------------------------------------------------------------------------
# 11. Pinned constants
# ---------------------------------------------------------------------------


def test_canonical_constants_pinned():
    assert core.CANONICAL_WINDOWS == (
        "1d", "1wk", "1mo", "3mo", "1y",
    )
    assert core.CANONICAL_K_VALUES == tuple(range(1, 13))
    assert core.PROTOCOL_DIRECT == "D"
    assert core.PROTOCOL_INVERSE == "I"
    assert core.SIGNAL_BUY == "Buy"
    assert core.SIGNAL_SHORT == "Short"
    assert core.SIGNAL_NONE == "None"
    assert core.SIGNAL_MISSING == "missing"
