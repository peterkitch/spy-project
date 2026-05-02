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
