"""
Phase 5B Item 8 regression tests: TrafficFlow refresh-callback error
surface.

Three previously-silent paths were wired through to the operator-
visible status output:

  1. ``refresh_secondary_caches(...)`` raising an exception ->
     ``[TRAFFICFLOW:refresh_exception]`` on the status line.
  2. ``_load_secondary_prices(sec)`` raising during the run-cutoff
     loop -> ``[TRAFFICFLOW:price_load_failed]`` on the status line.
  3. Per-symbol failures inside ``refresh_secondary_caches`` ->
     returned as ``[TRAFFICFLOW:refresh_no_data]`` /
     ``[TRAFFICFLOW:refresh_symbol_failed]`` formatted strings.
     Successful per-symbol outcomes (up-to-date / merged / replaced /
     kept existing) are NOT included in the returned list.

Tests monkeypatch the module-level helpers ``_refresh`` invokes, then
extract the callback from ``app.callback_map`` and invoke it directly.
No Dash server is started.

ASCII-only assertion messages per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, List

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import trafficflow  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: locate the _refresh callback inside the registered Dash app
# ---------------------------------------------------------------------------


def _get_refresh_callback(app) -> Callable:
    """Find the _refresh closure registered against
    Output('board','data') / Output('status','children'). Robust to
    Dash version differences in ``callback_map`` key formatting.

    Returns the underlying user function (``__wrapped__`` of Dash's
    ``add_context`` decorator) so we can call it with positional
    inputs without supplying Dash's internal ``outputs_list`` kwarg.
    """
    cbmap = getattr(app, "callback_map", None)
    assert cbmap, "Dash app exposes no callback_map (Dash unavailable?)"
    for key, entry in cbmap.items():
        s = str(key)
        if "board.data" in s and "status.children" in s:
            cb = entry.get("callback")
            assert cb is not None, (
                f"callback_map entry has no 'callback' field: {entry!r}"
            )
            # Dash wraps user callbacks with add_context; unwrap to
            # the original user function so positional args are
            # honored without outputs_list machinery.
            inner = getattr(cb, "__wrapped__", cb)
            return inner
    raise AssertionError(
        f"_refresh callback not found in app.callback_map; keys: "
        f"{list(cbmap.keys())!r}"
    )


def _no_op(*args, **kwargs):
    return None


def _empty_list(*args, **kwargs):
    return []


def _make_minimal_helpers(monkeypatch, *, secs=("AAA",)):
    """Replace every helper ``_refresh`` calls with a deterministic
    stub so tests stay hermetic. Returns the resolved ``app`` after
    ``make_app()`` registers the callback against the patched globals.
    """
    monkeypatch.setattr(trafficflow, "list_secondaries", lambda: list(secs))
    monkeypatch.setattr(trafficflow, "_clear_runtime", _no_op)
    monkeypatch.setattr(trafficflow, "preload_pkl_cache", _no_op)
    # Minimal valid return shape for compute_run_cutoff and the rest.
    monkeypatch.setattr(
        trafficflow, "compute_run_cutoff",
        lambda universe_prices: (None, {}),
    )
    monkeypatch.setattr(
        trafficflow, "scan_missing_stale_pkls",
        lambda secs, k_limit=None, include_stale=True, verbose=False: {},
    )
    monkeypatch.setattr(
        trafficflow, "build_board_rows",
        lambda sec, k, run_fence, missing_map: [],
    )
    monkeypatch.setattr(trafficflow, "_jsonify_row", lambda r: r)
    # Disable the optional preload path's env trigger so test stays
    # deterministic regardless of operator env.
    monkeypatch.setattr(
        trafficflow, "TRAFFICFLOW_PRELOAD_CACHE", False, raising=False,
    )
    # Force the price-refresh branch to fire even on first-load
    # invocations so the refresh diagnostic surface is exercised.
    monkeypatch.setattr(
        trafficflow, "TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD", True,
        raising=False,
    )
    monkeypatch.setattr(
        trafficflow, "TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK", False,
        raising=False,
    )

    app = trafficflow.make_app()
    assert app is not None, (
        "trafficflow.make_app() returned None (Dash unavailable?)"
    )
    return app


# ---------------------------------------------------------------------------
# A: refresh_secondary_caches exception surfaces in status
# ---------------------------------------------------------------------------


def test_refresh_callback_surfaces_refresh_secondary_caches_exception(
    monkeypatch,
):
    def _boom_refresh(symbols, force=False):
        raise RuntimeError("simulated yfinance outage")

    monkeypatch.setattr(
        trafficflow, "refresh_secondary_caches", _boom_refresh,
    )
    app = _make_minimal_helpers(monkeypatch, secs=("AAA",))
    cb = _get_refresh_callback(app)
    rows, status_children, last_update, missing_msg, missing_style = cb(1, 1)
    status_str = str(status_children)
    assert "[TRAFFICFLOW:refresh_exception]" in status_str, (
        f"expected [TRAFFICFLOW:refresh_exception] tag in status; "
        f"got {status_str!r}"
    )
    # Refresh failure must NOT cause hard failure: the callback still
    # returns the standard 5-tuple shape with rows + status.
    assert isinstance(rows, list)
    assert isinstance(last_update, str)
    assert "Refresh issues:" in status_str
    # The previously-silent code path is now visible; "simulated
    # yfinance outage" message text should be present somewhere.
    assert "simulated yfinance outage" in status_str


# ---------------------------------------------------------------------------
# B: _load_secondary_prices failure surfaces per-secondary in status
# ---------------------------------------------------------------------------


def test_refresh_callback_surfaces_price_load_failure(monkeypatch):
    monkeypatch.setattr(
        trafficflow, "refresh_secondary_caches",
        lambda symbols, force=False: [],  # no refresh issues
    )

    def _boom_load(sec):
        raise OSError(f"simulated cache read failure for {sec}")

    monkeypatch.setattr(
        trafficflow, "_load_secondary_prices", _boom_load,
    )
    monkeypatch.setattr(
        trafficflow, "_infer_quote_type", lambda sec: "EQUITY",
    )
    app = _make_minimal_helpers(monkeypatch, secs=("BBB", "CCC"))
    cb = _get_refresh_callback(app)
    _, status_children, *_ = cb(1, 1)
    status_str = str(status_children)
    assert "[TRAFFICFLOW:price_load_failed]" in status_str, (
        f"expected [TRAFFICFLOW:price_load_failed] tag in status; "
        f"got {status_str!r}"
    )
    # Both failing secondaries should produce a tagged entry; the
    # status segment is bounded to the first 10 so both fit.
    assert "BBB" in status_str
    assert "CCC" in status_str
    assert "Refresh issues:" in status_str


# ---------------------------------------------------------------------------
# C: refresh_secondary_caches returns failures-only, with stable codes
# ---------------------------------------------------------------------------


def test_refresh_secondary_caches_returns_symbol_failures(monkeypatch):
    """Per-symbol no-data and update-failure produce tagged returns;
    successful outcomes are absent from the returned list (None-
    filtered)."""

    # Drive the worker through two distinct failure modes plus one
    # success path. We patch the yfinance-touching helpers so the
    # worker takes the forced-refresh branch (force=True).
    def _fake_choose_path(sym):
        return PROJECT_DIR / "test_scripts" / "_tf_unused_path.pkl"

    def _fake_fetch(sym):
        # NO_DATA case for "ND_SYM" -> empty frame
        if sym == "ND_SYM":
            import pandas as pd
            return pd.DataFrame()
        # Failure case for "BOOM_SYM" -> raise inside the try block
        if sym == "BOOM_SYM":
            raise RuntimeError("yfinance simulated worker exception")
        # Success case for "OK_SYM" -> small valid frame
        import pandas as pd
        idx = pd.bdate_range("2024-01-02", periods=10)
        return pd.DataFrame({"Close": list(range(100, 110))}, index=idx)

    def _fake_write(*args, **kwargs):
        return None  # don't actually touch disk

    monkeypatch.setattr(trafficflow, "_choose_price_cache_path",
                        _fake_choose_path)
    monkeypatch.setattr(trafficflow, "_fetch_secondary_from_yf",
                        _fake_fetch)
    monkeypatch.setattr(trafficflow, "_write_cache_file",
                        _fake_write)

    # Ensure yf is non-None at entry so we don't trigger
    # refresh_unavailable instead of per-symbol behavior.
    if trafficflow.yf is None:
        pytest.skip("yfinance import unavailable; refresh_unavailable "
                    "branch covered separately")

    issues = trafficflow.refresh_secondary_caches(
        ["OK_SYM", "ND_SYM", "BOOM_SYM"], force=True,
    )

    # Returned list contains only failures.
    assert isinstance(issues, list)
    assert len(issues) == 2, (
        f"expected 2 issues (no_data + update_failed); got {issues!r}"
    )

    joined = " | ".join(issues)
    assert "[TRAFFICFLOW:refresh_no_data]" in joined, (
        f"missing refresh_no_data tag in {joined!r}"
    )
    assert "[TRAFFICFLOW:refresh_symbol_failed]" in joined, (
        f"missing refresh_symbol_failed tag in {joined!r}"
    )
    # The OK symbol's outcome strings must NOT leak into the issue
    # list (failures-only contract).
    for forbidden in ("up-to-date", "merged", "replaced", "kept existing"):
        assert forbidden not in joined, (
            f"successful-outcome substring {forbidden!r} leaked into "
            f"refresh_secondary_caches issue list: {joined!r}"
        )
    # Each tagged line names the offending symbol.
    assert "ND_SYM" in joined
    assert "BOOM_SYM" in joined
    # OK_SYM did not fail; it must NOT appear as an issue.
    assert "OK_SYM" not in joined
