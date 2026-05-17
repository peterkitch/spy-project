"""Phase 6I-57 focused tests for:

  * the fastpath manifest-verification fix (top-level
    engine_version accepted; the buggy
    ``params.engine_version`` requested-params check was
    removed in ``signal_library/impact_fastpath.py`` and
    ``impactsearch.py``).
  * thread-safe primary/secondary yfinance role
    attribution and the ``IMPACT_REQUIRE_ZERO_PRIMARY_YF``
    hard-fail gate.

The fastpath smoke test reads a real fresh signal-library
file from ``signal_library/data/stable/SPY_stable_v1_0_0.pkl``;
if that artifact is absent (clean checkout), the test
SKIPs rather than failing. CI environments without
operational artifacts must not be broken by this file.

The yfinance-gate tests exercise ``_YfRoleContext``,
``_record_yf_call``, ``get_yf_records``, and
``reset_yf_records`` directly under ``threading``-based
concurrency. They do NOT make any real network call;
``_record_yf_call`` is the call-time hook the
``_wrapped_download`` function calls before delegating
to ``yfinance.download``, so testing it directly is
representative.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


# ---------------------------------------------------------------------------
# Fastpath manifest verification (top-level engine_version)
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_spy_library_present():
    p = (
        _HERE
        / "signal_library"
        / "data"
        / "stable"
        / "SPY_stable_v1_0_0.pkl"
    )
    if not p.exists():
        pytest.skip(
            "fresh SPY signal-library not present in this "
            "checkout; skipping integration smoke."
        )
    return p


def test_fastpath_loads_fresh_spy_library(
    fresh_spy_library_present, monkeypatch,
):
    """Regression: the fastpath previously rejected every
    OnePass-fresh library because it asked the provenance
    verifier for ``params.engine_version`` (which does not
    exist in the manifest). After the Phase 6I-57 fix the
    fastpath loads cleanly with no mismatch warnings."""
    monkeypatch.setenv("IMPACT_TRUST_LIBRARY", "1")
    monkeypatch.setenv("IMPACT_TRUST_MAX_AGE_HOURS", "720")
    monkeypatch.setenv("IMPACT_CALENDAR_GRACE_DAYS", "30")
    monkeypatch.setenv(
        "SIGNAL_LIBRARY_DIR", str(_HERE / "signal_library/data"),
    )
    # Reload the fastpath module so it picks up env.
    if "signal_library.impact_fastpath" in sys.modules:
        del sys.modules["signal_library.impact_fastpath"]
    from signal_library.impact_fastpath import (
        get_primary_signals_fast,
        _load_signal_library_quick,
        _is_compatible,
    )
    import pandas as _pd
    # Build a simple secondary calendar with end-date close
    # to the library end-date so the calendar check passes.
    sec_idx = _pd.date_range(
        start="2024-01-01", end="2026-05-14", freq="B",
    )
    sigs, reason = get_primary_signals_fast("SPY", sec_idx)
    assert sigs is not None, (
        f"fastpath returned None for SPY; reason={reason!r}"
    )
    assert len(sigs) > 1000
    assert "fastpath_success" in reason
    # The underlying loader returns a non-None lib dict.
    lib = _load_signal_library_quick("SPY")
    assert lib is not None
    # Top-level engine_version is what _is_compatible checks.
    ok, why = _is_compatible(lib)
    assert ok, f"_is_compatible rejected fresh SPY lib: {why}"


def test_is_compatible_rejects_wrong_top_level_engine_version():
    """Pins that the top-level engine_version integrity
    guard still works after the params.engine_version
    requested-params check was removed."""
    if "signal_library.impact_fastpath" in sys.modules:
        del sys.modules["signal_library.impact_fastpath"]
    from signal_library.impact_fastpath import _is_compatible
    bad = {
        "engine_version": "0.9.9",  # wrong on purpose
        "max_sma_day": 114,
        "price_source": "Close",
    }
    ok, why = _is_compatible(bad)
    assert ok is False
    assert "engine_version_mismatch" in why


def test_is_compatible_rejects_missing_top_level_engine_version():
    if "signal_library.impact_fastpath" in sys.modules:
        del sys.modules["signal_library.impact_fastpath"]
    from signal_library.impact_fastpath import _is_compatible
    bad = {
        "max_sma_day": 114,
        "price_source": "Close",
        # no engine_version at all
    }
    ok, why = _is_compatible(bad)
    assert ok is False
    assert "engine_version_mismatch" in why


# ---------------------------------------------------------------------------
# Thread-safe yfinance role attribution + zero-primary-yf gate
# ---------------------------------------------------------------------------


def _import_impactsearch_role_pieces():
    """Lazy-import the small role pieces from impactsearch
    so the test only loads the heavy module once."""
    import impactsearch as _is
    return (
        _is,
        _is._YfRoleContext,
        _is._record_yf_call,
        _is.get_yf_records,
        _is.reset_yf_records,
    )


def test_yf_role_context_sets_thread_local():
    (
        _is, _YfRoleContext,
        _record_yf_call, get_yf_records, reset_yf_records,
    ) = _import_impactsearch_role_pieces()
    reset_yf_records()
    with _YfRoleContext(
        "secondary", ticker="SPY", stage="unit_test",
    ):
        _record_yf_call("SPY")
    with _YfRoleContext(
        "primary", ticker="AAPL", stage="unit_test",
    ):
        _record_yf_call("AAPL")
    recs = get_yf_records()
    assert len(recs) == 2
    roles = [r["role"] for r in recs]
    assert roles == ["secondary", "primary"]
    tickers = [r["ticker"] for r in recs]
    assert tickers == ["SPY", "AAPL"]
    assert all(r["stage"] == "unit_test" for r in recs)


def test_yf_role_context_thread_local_isolated_under_concurrency():
    """Two threads enter different role contexts at the
    same time; the role recorded for each must reflect
    that thread's context, not be cross-contaminated."""
    (
        _is, _YfRoleContext,
        _record_yf_call, get_yf_records, reset_yf_records,
    ) = _import_impactsearch_role_pieces()
    reset_yf_records()
    barrier = threading.Barrier(2)

    def _worker(role: str, ticker: str, n: int):
        for i in range(n):
            with _YfRoleContext(
                role, ticker=f"{ticker}{i}",
                stage="thread_worker",
            ):
                # Inside the context, record a fake call.
                _record_yf_call(f"{ticker}{i}")
                # Yield to encourage interleaving.
                time.sleep(0.0)
        barrier.wait()

    t1 = threading.Thread(
        target=_worker, args=("primary", "P", 50),
    )
    t2 = threading.Thread(
        target=_worker, args=("secondary", "S", 50),
    )
    t1.start(); t2.start()
    t1.join(); t2.join()

    recs = get_yf_records()
    assert len(recs) == 100, len(recs)
    p_recs = [r for r in recs if r["role"] == "primary"]
    s_recs = [r for r in recs if r["role"] == "secondary"]
    assert len(p_recs) == 50
    assert len(s_recs) == 50
    # All primary ticker labels start with "P"; all secondary with "S".
    for r in p_recs:
        assert r["ticker"].startswith("P")
    for r in s_recs:
        assert r["ticker"].startswith("S")


def test_require_zero_primary_yf_raises_on_primary_call(
    monkeypatch,
):
    """When IMPACT_REQUIRE_ZERO_PRIMARY_YF is armed and a
    primary-role call happens, ``_record_yf_call`` raises
    RuntimeError so the run fails immediately rather than
    silently writing a contaminated workbook."""
    (
        _is, _YfRoleContext,
        _record_yf_call, get_yf_records, reset_yf_records,
    ) = _import_impactsearch_role_pieces()
    reset_yf_records()
    # Arm the gate on the loaded module (env-driven flag
    # captured at import time; monkeypatch the module attr).
    monkeypatch.setattr(
        _is, "_YF_REQUIRE_ZERO_PRIMARY", True, raising=True,
    )
    # Secondary call must NOT raise.
    with _YfRoleContext(
        "secondary", ticker="SPY", stage="t",
    ):
        _record_yf_call("SPY")
    # Primary call MUST raise.
    with pytest.raises(RuntimeError) as excinfo:
        with _YfRoleContext(
            "primary", ticker="AAPL", stage="t",
        ):
            _record_yf_call("AAPL")
    assert "IMPACT_REQUIRE_ZERO_PRIMARY_YF" in str(
        excinfo.value
    )
    assert "AAPL" in str(excinfo.value)


def test_require_zero_primary_yf_raises_under_threaded_workers(
    monkeypatch,
):
    """Spawn 8 worker threads, each in a primary-role
    context, all calling ``_record_yf_call``. With the
    zero-yf gate armed, every worker must surface a
    RuntimeError. (Mirrors the Gate-2 invariant that the
    threaded path also enforces the gate.)"""
    (
        _is, _YfRoleContext,
        _record_yf_call, get_yf_records, reset_yf_records,
    ) = _import_impactsearch_role_pieces()
    reset_yf_records()
    monkeypatch.setattr(
        _is, "_YF_REQUIRE_ZERO_PRIMARY", True, raising=True,
    )
    failures: list[Exception] = []
    lock = threading.Lock()

    def _worker(idx: int):
        try:
            with _YfRoleContext(
                "primary", ticker=f"T{idx}",
                stage="threaded_worker",
            ):
                _record_yf_call(f"T{idx}")
        except RuntimeError as exc:
            with lock:
                failures.append(exc)

    threads = [
        threading.Thread(target=_worker, args=(i,))
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Every one of the 8 primary calls must have raised.
    assert len(failures) == 8, (
        f"expected 8 RuntimeErrors, got {len(failures)}"
    )
    for exc in failures:
        assert "IMPACT_REQUIRE_ZERO_PRIMARY_YF" in str(exc)


def test_reset_yf_records_clears_aggregate():
    (
        _is, _YfRoleContext,
        _record_yf_call, get_yf_records, reset_yf_records,
    ) = _import_impactsearch_role_pieces()
    with _YfRoleContext("secondary", ticker="X", stage="t"):
        _record_yf_call("X")
    assert len(get_yf_records()) >= 1
    reset_yf_records()
    assert get_yf_records() == []
