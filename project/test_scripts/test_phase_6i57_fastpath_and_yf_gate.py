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


# ---------------------------------------------------------------------------
# Phase 6I-57: skip primary on fastpath fallback when zero-yf gate is armed
# ---------------------------------------------------------------------------


def _build_minimal_sec_df():
    import pandas as _pd
    idx = _pd.date_range(
        start="2024-01-02", end="2026-05-14", freq="B",
    )
    return _pd.DataFrame({"Close": [100.0] * len(idx)}, index=idx)


def test_fastpath_fallback_skipped_when_zero_yf_gate_armed(
    monkeypatch,
):
    """When `get_primary_signals_fast` returns None with a
    reason like ``incomplete_calendar:...`` and the
    zero-primary-yfinance gate is armed, the helper must
    return ``(None, rejection)`` BEFORE calling
    fetch_data_raw -- no primary yfinance attempted, no
    contaminated row produced."""
    import impactsearch as _is
    reset = _is.reset_yf_records
    reset()

    monkeypatch.setattr(_is, "FASTPATH_AVAILABLE", True)
    monkeypatch.setattr(_is, "IMPACT_TRUST_LIBRARY", True)
    monkeypatch.setattr(
        _is, "_YF_REQUIRE_ZERO_PRIMARY", True,
    )
    fp_reason = (
        "incomplete_calendar:insufficient "
        "(lib_end=2026-02-20 + 30d < sec_eff_end=2026-05-13)"
    )
    monkeypatch.setattr(
        _is, "get_primary_signals_fast",
        lambda t, idx: (None, fp_reason),
    )
    # Fail loudly if anything below the skip ever calls
    # fetch_data_raw -- the whole point of the gate is to
    # prevent that.
    calls: list[str] = []

    def _trap_fetch(*args, **kwargs):
        calls.append(args[0] if args else "?")
        raise AssertionError(
            "fetch_data_raw must not be called when the "
            "zero-primary-yf gate suppresses fallback"
        )

    monkeypatch.setattr(_is, "fetch_data_raw", _trap_fetch)
    sec_df = _build_minimal_sec_df()
    aligned, rejection = (
        _is._impactsearch_primary_signal_series_for_secondary(
            "U1P.F", sec_df, rejection_out={},
        )
    )
    assert aligned is None
    assert isinstance(rejection, dict)
    # The rejection must use the new structured reason code.
    reason = (
        rejection.get("reason")
        or (rejection.get("payload") or {}).get("reason")
    )
    # The helper passes structured rejection via _populate_rejection
    # which sets standardized keys; allow either flat or nested.
    full = repr(rejection)
    assert (
        _is.PROCESS_FASTPATH_FALLBACK_SKIPPED_ZERO_YF_GATE
        in full
    ), full
    assert "U1P.F" in full
    assert calls == []
    # No primary yfinance records appeared either.
    recs = _is.get_yf_records()
    primary_recs = [
        r for r in recs if r.get("role") == "primary"
    ]
    assert primary_recs == []


def test_fastpath_fallback_not_skipped_when_zero_yf_gate_disarmed(
    monkeypatch,
):
    """Control: when the gate is NOT armed, fastpath fallback
    proceeds to the slow path (fetch_data_raw IS called).
    Verifies the existing slow-path behavior is preserved."""
    import impactsearch as _is
    _is.reset_yf_records()

    monkeypatch.setattr(_is, "FASTPATH_AVAILABLE", True)
    monkeypatch.setattr(_is, "IMPACT_TRUST_LIBRARY", True)
    monkeypatch.setattr(
        _is, "_YF_REQUIRE_ZERO_PRIMARY", False,
    )
    monkeypatch.setattr(
        _is, "get_primary_signals_fast",
        lambda t, idx: (None, "incomplete_calendar:t1"),
    )
    seen: list[str] = []

    def _stub_fetch(prim, **kwargs):
        seen.append(prim)
        return (None, prim)  # Empty -> downstream early-returns.

    monkeypatch.setattr(_is, "fetch_data_raw", _stub_fetch)
    sec_df = _build_minimal_sec_df()
    aligned, _rej = (
        _is._impactsearch_primary_signal_series_for_secondary(
            "DELISTED", sec_df, rejection_out={},
        )
    )
    # Slow path WAS reached; aligned is None (no data) and
    # the test passes if fetch_data_raw was attempted.
    assert seen == ["DELISTED"]
    assert aligned is None


def test_threaded_fastpath_fallback_skip_no_primary_yf_records(
    monkeypatch,
):
    """Spin up 8 worker threads calling the helper
    concurrently with the gate armed and a faked fastpath
    fallback. Every call must skip; no role=primary
    record may appear; fetch_data_raw must not be called."""
    import impactsearch as _is
    _is.reset_yf_records()

    monkeypatch.setattr(_is, "FASTPATH_AVAILABLE", True)
    monkeypatch.setattr(_is, "IMPACT_TRUST_LIBRARY", True)
    monkeypatch.setattr(
        _is, "_YF_REQUIRE_ZERO_PRIMARY", True,
    )
    monkeypatch.setattr(
        _is, "get_primary_signals_fast",
        lambda t, idx: (None, f"incomplete_calendar:t-{t}"),
    )

    def _trap_fetch(*args, **kwargs):
        raise AssertionError(
            "fetch_data_raw must not be called under "
            "threaded skip"
        )

    monkeypatch.setattr(_is, "fetch_data_raw", _trap_fetch)

    sec_df = _build_minimal_sec_df()
    results: list[tuple] = []
    lock = threading.Lock()

    def _worker(t: str):
        rej = {}
        aligned, rejection = (
            _is._impactsearch_primary_signal_series_for_secondary(
                t, sec_df, rejection_out=rej,
            )
        )
        with lock:
            results.append((t, aligned, rejection))

    tickers = [f"TKR{i}" for i in range(8)]
    threads = [
        threading.Thread(target=_worker, args=(t,))
        for t in tickers
    ]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(results) == 8
    for t, aligned, rejection in results:
        assert aligned is None
        assert (
            _is.PROCESS_FASTPATH_FALLBACK_SKIPPED_ZERO_YF_GATE
            in repr(rejection)
        ), repr(rejection)
        assert t in repr(rejection)
    # No primary yfinance records appeared from any worker.
    recs = _is.get_yf_records()
    primary_recs = [
        r for r in recs if r.get("role") == "primary"
    ]
    assert primary_recs == [], primary_recs
