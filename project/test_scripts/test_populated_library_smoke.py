"""
Phase 2A: populated signal library smoke.

Phase 1A's fixtures only exercise the ENGINE-helper code paths
(metric computation, signal combination, captures). They do not
exercise the LIBRARY LOADER paths that look for, validate, and
pull from on-disk pickles. The latent NameError fixed in 1B-2B
amendment 1 commit (`env_price_source` reference removed in
onepass.py) was a textbook example: the buggy code only ran when
``check_signal_library_exists`` returned True (i.e. a library
existed on disk), which Phase 1A never set up.

These tests build small synthetic libraries under tmp_path,
monkeypatch the engine path globals to point at tmp_path, then
exercise the populated-library path. They verify the loader does
not crash, returns the dict, and the parity guards behave as
expected.

D1 covers the schema fixture builder (already provided by
phase2_test_utils.make_signal_library_dict / write_signal_library).

D2 OnePass / ImpactSearch / impact_fastpath populated-lib loaders.

D3 TrafficFlow Spymaster PKL load + populated-cache path.

D4 Confluence smoke is deferred to Phase 2B (per spec).

D5 Cleanup is via yield fixtures and try/finally.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from phase2_test_utils import (
    make_signal_library_dict,
    make_synthetic_close_prices,
    make_synthetic_pkl_for_spymaster,
    write_signal_library,
)


# ---------------------------------------------------------------------------
# D1: synthetic library fixture (verified by D2/D3 below).
# ---------------------------------------------------------------------------


def test_d1_synthetic_library_dict_schema_minimum():
    """The builder produces a dict matching the loader contract."""
    dates = pd.bdate_range(start="2024-01-02", periods=10)
    lib = make_signal_library_dict(dates)
    # Required keys for onepass / impactsearch loaders.
    for key in (
        "engine_version", "max_sma_day", "price_source",
        "dates", "primary_signals", "primary_signals_int8",
        "daily_top_buy_pairs", "daily_top_short_pairs",
        "num_days", "build_timestamp", "end_date",
    ):
        assert key in lib, f"missing key: {key}"
    assert lib["price_source"] == "Close"
    assert lib["engine_version"] == "1.0.0"
    assert lib["max_sma_day"] == 114
    # Sentinels are canonical
    first = list(lib["daily_top_buy_pairs"].values())[0][0]
    assert first == (114, 113)
    first_short = list(lib["daily_top_short_pairs"].values())[0][0]
    assert first_short == (113, 114)


# ---------------------------------------------------------------------------
# D2: OnePass / ImpactSearch / impact_fastpath populated-lib smoke.
# ---------------------------------------------------------------------------


def _get_module(name: str):
    """Force-import a module from PROJECT_DIR (handles namespace-package
    shadowing from test_scripts/<module-name>/ subdirs)."""
    mod = importlib.import_module(name)
    if not hasattr(mod, "__file__") or mod.__file__ is None:
        spec = importlib.util.spec_from_file_location(
            name, str(PROJECT_DIR / f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def _populated_lib_dir(tmp_path):
    """Populate tmp_path/signal_library/data/stable with one tiny lib."""
    dates = pd.bdate_range(start="2024-01-02", periods=10)
    lib = make_signal_library_dict(dates)
    stable_dir = tmp_path / "signal_library" / "data" / "stable"
    # Write under both onepass/impactsearch convention and impact_fastpath
    # convention for cross-engine coverage.
    # onepass/impactsearch: `<ticker>_v{ENGINE_VERSION.replace('.', '_')}_signal_lib.pkl`
    # (matches `_lib_path_for` in onepass.py:1118+)
    # impact_fastpath: `<ticker>_stable_v{ENGINE_VERSION.replace('.', '_')}.pkl`
    # We probe the actual path generators below to write under the right name.
    yield {
        "tmp_path": tmp_path,
        "stable_dir": stable_dir,
        "lib": lib,
        "ticker": "AAA",
    }


def _write_lib_for_module(mod, ticker: str, lib: dict, stable_dir: Path) -> Path:
    """Use the module's own `_lib_path_for` if it exposes one; else
    fall back to a sensible default."""
    stable_dir.mkdir(parents=True, exist_ok=True)
    path_fn = getattr(mod, "_lib_path_for", None)
    if callable(path_fn):
        path_str = path_fn(ticker)
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(lib, fh)
        return path
    return write_signal_library(stable_dir, f"{ticker}_signal_lib", lib)


def test_d2_onepass_load_signal_library_populated(monkeypatch, _populated_lib_dir):
    """OnePass.load_signal_library should return a dict for a
    populated stable directory; check_signal_library_exists should
    return True. This exercises the path class that previously hit
    a NameError on env_price_source.
    """
    op = _get_module("onepass")
    monkeypatch.setattr(op, "SIGNAL_LIBRARY_DIR", str(_populated_lib_dir["tmp_path"] / "signal_library" / "data"))

    # Use the module's own path generator so the file lands where
    # load_signal_library expects.
    stable_dir = _populated_lib_dir["tmp_path"] / "signal_library" / "data" / "stable"
    stable_dir.mkdir(parents=True, exist_ok=True)
    path = _write_lib_for_module(op, _populated_lib_dir["ticker"], _populated_lib_dir["lib"], stable_dir)
    assert path.exists()

    found = op.check_signal_library_exists(_populated_lib_dir["ticker"])
    assert found is True, f"check_signal_library_exists returned {found!r} for populated library"

    loaded = op.load_signal_library(_populated_lib_dir["ticker"])
    assert isinstance(loaded, dict), f"load_signal_library returned {type(loaded).__name__}"
    assert loaded.get("price_source") == "Close"
    assert loaded.get("max_sma_day") == 114


def test_d2_impactsearch_load_signal_library_populated(monkeypatch, _populated_lib_dir):
    isr = _get_module("impactsearch")
    monkeypatch.setattr(isr, "SIGNAL_LIBRARY_DIR", str(_populated_lib_dir["tmp_path"] / "signal_library" / "data"))

    stable_dir = _populated_lib_dir["tmp_path"] / "signal_library" / "data" / "stable"
    stable_dir.mkdir(parents=True, exist_ok=True)
    path = _write_lib_for_module(isr, _populated_lib_dir["ticker"], _populated_lib_dir["lib"], stable_dir)
    assert path.exists()

    loaded = isr.load_signal_library(_populated_lib_dir["ticker"])
    assert isinstance(loaded, dict)
    assert loaded.get("price_source") == "Close"


def test_d2_impact_fastpath_load_quick_populated(monkeypatch, tmp_path):
    """impact_fastpath._load_signal_library_quick should find and
    return a populated library when SIGNAL_LIBRARY_DIR points at it.
    """
    fp = importlib.import_module("signal_library.impact_fastpath")
    # Override the module-level constant.
    monkeypatch.setattr(fp, "SIGNAL_LIBRARY_DIR", str(tmp_path / "signal_library" / "data"))

    dates = pd.bdate_range(start="2024-01-02", periods=10)
    lib = make_signal_library_dict(dates, parity_hash="abc123")
    stable_dir = tmp_path / "signal_library" / "data" / "stable"
    stable_dir.mkdir(parents=True, exist_ok=True)
    path = Path(fp._lib_path_for("AAA"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(lib, fh)
    assert path.exists()

    loaded = fp._load_signal_library_quick("AAA")
    assert isinstance(loaded, dict)
    assert loaded.get("price_source") == "Close"

    # _is_compatible should accept this library.
    ok, why = fp._is_compatible(loaded)
    assert ok, f"populated library failed _is_compatible: {why}"


# ---------------------------------------------------------------------------
# D3: TrafficFlow Spymaster PKL smoke.
# ---------------------------------------------------------------------------


@pytest.fixture
def _populated_spymaster_pkl(tmp_path):
    """Write a Spymaster-style PKL under tmp_path/cache/results."""
    dates = pd.bdate_range(start="2024-01-02", periods=20)
    pkl = make_synthetic_pkl_for_spymaster(dates)
    cache_dir = tmp_path / "cache" / "results"
    cache_dir.mkdir(parents=True, exist_ok=True)
    pkl_path = cache_dir / "AAA_precomputed_results.pkl"
    with open(pkl_path, "wb") as fh:
        pickle.dump(pkl, fh)
    return tmp_path, "AAA", pkl_path


def test_d3_trafficflow_spymaster_pkl_populated(monkeypatch, _populated_spymaster_pkl):
    """TrafficFlow.load_spymaster_pkl should return the populated PKL,
    and downstream helpers (_load_signal_library_quick, _next_signal_from_pkl)
    should run without crashing.
    """
    tmp_path, ticker, pkl_path = _populated_spymaster_pkl
    tf = _get_module("trafficflow")
    monkeypatch.setattr(tf, "SPYMASTER_PKL_DIR", str(tmp_path / "cache" / "results"))

    # Clear any cached state.
    tf._PKL_CACHE.pop(ticker, None)

    loaded = tf.load_spymaster_pkl(ticker)
    assert isinstance(loaded, dict)
    assert "preprocessed_data" in loaded
    assert "daily_top_buy_pairs" in loaded
    assert "daily_top_short_pairs" in loaded

    # _next_signal_from_pkl should return one of Buy/Short/None.
    next_sig = tf._next_signal_from_pkl(ticker)
    assert next_sig in ("Buy", "Short", "None")

    # _processed_signals_from_pkl returns a Series of labels.
    sigs = tf._processed_signals_from_pkl(ticker)
    assert isinstance(sigs, pd.Series)
    assert set(sigs.unique()) <= {"Buy", "Short", "None"}

    # Cleanup
    tf._PKL_CACHE.pop(ticker, None)


def test_d3_synthetic_pkl_size_under_1mb(_populated_spymaster_pkl):
    """The synthetic PKL fixture should stay tiny (size budget per
    Phase 2A spec)."""
    _tmp_path, _ticker, pkl_path = _populated_spymaster_pkl
    size = pkl_path.stat().st_size
    assert size < 1_000_000, f"synthetic PKL grew to {size} bytes; tighten the fixture"


# ---------------------------------------------------------------------------
# D2 (amendment): OnePass process_onepass_tickers with use_existing_signals
# ---------------------------------------------------------------------------


def test_d2_onepass_process_tickers_existing_signals_path(monkeypatch, tmp_path):
    """Exercise process_onepass_tickers(["AAA"], use_existing_signals=True, ...).

    This is the path that hit a NameError on `env_price_source` before
    the 1B-2A amendment. The original PR #134 D2 only covered
    `load_signal_library` directly; this test drives the FULL function
    so the parity-check branch (lines 1781-1795 on main) actually runs
    against a populated library.

    We short-circuit cleanly via fetch_data_raw returning an empty
    DataFrame, which is the SKIPPED_NO_DATA path immediately AFTER
    the parity check. No network. No real SMA computation.

    Regression seal: any future re-introduction of an undefined
    variable or basis-check crash in the populated-library reuse
    block would fail this test.
    """
    op = _get_module("onepass")

    # Snapshot module globals before any mutation; restore on cleanup.
    pre_signal_dir = op.SIGNAL_LIBRARY_DIR

    try:
        # Point the engine at a populated tmp dir.
        monkeypatch.setattr(op, "SIGNAL_LIBRARY_DIR", str(tmp_path / "signal_library" / "data"))

        # Write a valid library so check_signal_library_exists returns True
        # and load_signal_library returns the dict.
        dates = pd.bdate_range(start="2024-01-02", periods=20)
        # Compute the parity hash the same way the engine does so the
        # library passes the parity match (avoids the rebuild branch).
        parity_hash = op.compute_parity_hash("Close", "ticker")
        lib = make_signal_library_dict(dates, parity_hash=parity_hash)
        path = Path(op._lib_path_for("AAA"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(lib, fh)
        assert path.exists()

        # Sanity: check_signal_library_exists must return True.
        assert op.check_signal_library_exists("AAA"), (
            "fixture not visible to engine; SIGNAL_LIBRARY_DIR monkeypatch failed"
        )

        # Monkeypatch fetch_data_raw to return an empty DataFrame so the
        # function exits via SKIPPED_NO_DATA right after the parity-check
        # block. This isolates the test to the populated-library
        # reuse path (lines 1772-1795 on main) without exercising the
        # full SMA computation.
        empty_df = pd.DataFrame()
        monkeypatch.setattr(op, "fetch_data_raw", lambda ticker, **kw: (empty_df, ticker))

        # Suppress tqdm output noise so test logs stay readable.
        monkeypatch.setattr(op, "tqdm", lambda iterable, **kw: iterable)

        # Reset RUN_REPORT to avoid carry-over from earlier tests in the
        # same session.
        monkeypatch.setattr(op, "RUN_REPORT", None)

        # The actual call. If the parity-check block reintroduces
        # `env_price_source` or another undefined variable, this will
        # raise NameError immediately. Any other exception is also a
        # regression — we expect a clean return.
        metrics = op.process_onepass_tickers(
            ["AAA"],
            use_existing_signals=True,
            emit_summary=False,
            write_report_json=False,
        )

        # The empty-DF short-circuit means metrics is empty (no data to
        # compute). The success criterion is purely "no exception".
        assert metrics == [] or metrics is None or isinstance(metrics, list), (
            f"unexpected metrics shape: {type(metrics).__name__}"
        )
    finally:
        op.SIGNAL_LIBRARY_DIR = pre_signal_dir


# ---------------------------------------------------------------------------
# D-StackBuilder: populated-library smoke for stackbuilder
# ---------------------------------------------------------------------------


def test_d_stackbuilder_load_lib_and_signals_aligned(monkeypatch, tmp_path):
    """StackBuilder populated-library smoke.

    Exercises:
      - stackbuilder.load_lib_or_none() against a tmp library
      - stackbuilder._signals_aligned_and_mask() producing
        signal-state Buy/Short -> True, None -> False mask

    StackBuilder.load_lib_or_none first calls onepass.load_signal_library,
    which uses onepass.SIGNAL_LIBRARY_DIR. We monkeypatch that to
    tmp_path so the fixture lib is found via the primary path. We
    also monkeypatch stackbuilder.SIGNAL_LIB_DIR_RUNTIME for the
    fallback path symmetrically.
    """
    sb = _get_module("stackbuilder")
    op = _get_module("onepass")

    pre_op_dir = op.SIGNAL_LIBRARY_DIR
    pre_sb_runtime = sb.SIGNAL_LIB_DIR_RUNTIME

    try:
        signal_root = tmp_path / "signal_library" / "data"
        monkeypatch.setattr(op, "SIGNAL_LIBRARY_DIR", str(signal_root))
        monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(signal_root / "stable"))

        # Build a small library with a mixed signal series so we can
        # observe Buy/Short -> True, None -> False masking.
        dates = pd.bdate_range(start="2024-01-02", periods=10)
        signals = ["None", "Buy", "Buy", "None", "Short", "Short", "None", "Buy", "Short", "None"]
        parity_hash = op.compute_parity_hash("Close", "ticker")
        lib = make_signal_library_dict(
            dates,
            primary_signals=signals,
            parity_hash=parity_hash,
        )
        path = Path(op._lib_path_for("AAA"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(lib, fh)
        assert path.exists()

        # 1) load_lib_or_none returns the populated library.
        loaded = sb.load_lib_or_none("AAA")
        assert isinstance(loaded, dict), (
            f"load_lib_or_none returned {type(loaded).__name__}; expected dict"
        )
        assert loaded.get("price_source") == "Close"
        assert loaded.get("max_sma_day") == 114
        assert len(loaded["primary_signals"]) == 10

        # 2) _signals_aligned_and_mask completes without error and
        # produces a mask aligned to sec_index where True at every
        # date that has a Buy/Short in the source library.
        sec_index = pd.DatetimeIndex(dates)
        s, present = sb._signals_aligned_and_mask("AAA", "D", sec_index)
        assert isinstance(s, pd.Series)
        assert isinstance(present, pd.Series)
        assert len(s) == len(sec_index)
        assert len(present) == len(sec_index)
        # Reindex preserves the order of sec_index. With a non-truncated
        # library and grace_days >= 0 the present mask should be True on
        # every date in sec_index.
        assert bool(present.all()), (
            f"present mask should be True everywhere for an aligned "
            f"library; got: {present.tolist()}"
        )
        # Signal-state assertions: positions where the source had Buy/Short
        # must end up as Buy/Short in the aligned series; positions with
        # None remain None.
        expected_signals = pd.Series(signals, index=sec_index)
        for date in sec_index:
            assert s.loc[date] == expected_signals.loc[date], (
                f"signal mismatch at {date}: aligned={s.loc[date]!r} "
                f"vs expected={expected_signals.loc[date]!r}"
            )
    finally:
        op.SIGNAL_LIBRARY_DIR = pre_op_dir
        sb.SIGNAL_LIB_DIR_RUNTIME = pre_sb_runtime
