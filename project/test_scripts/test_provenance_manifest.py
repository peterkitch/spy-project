"""
Phase 3A: provenance manifest helper + consumer-hook tests.

Helper-only tests (F1-F10) cover the central ``provenance_manifest``
module. Consumer-hook tests (F11-F15) and the metadata-repair
preservation test (F16) follow the helper section. The B12 static
guard (F17) is asserted in ``test_static_regression_guards.py``.
"""

from __future__ import annotations

import json
import os
import pickle
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import provenance_manifest as pm
from phase2_test_utils import make_signal_library_dict, make_synthetic_close_prices


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_library():
    dates = pd.bdate_range(start="2024-01-02", periods=20)
    sigs = [["Buy", "Short", "None"][i % 3] for i in range(len(dates))]
    lib = make_signal_library_dict(dates, primary_signals=sigs)
    lib["build_timestamp"] = "2025-01-01T00:00:00"
    return lib


@pytest.fixture
def sample_close():
    dates = pd.bdate_range(start="2024-01-02", periods=20)
    return make_synthetic_close_prices(dates)


# ---------------------------------------------------------------------------
# F1: manifest schema fields present
# ---------------------------------------------------------------------------


def test_f1_manifest_schema_fields_present(sample_library, sample_close):
    manifest = pm.build_manifest(
        sample_library,
        artifact_type="signal_library_daily",
        ticker="SPY",
        interval="1d",
        params={"MAX_SMA_DAY": 114, "price_source": "Close"},
        source_close=sample_close,
        engine_version="1.0.0",
    )
    expected_stable = {
        "schema_version", "artifact_type", "ticker", "resolved_symbol",
        "interval", "date_range_start", "date_range_end", "row_count",
        "source_data", "params", "engine_version", "git_commit", "git_dirty",
        "package_versions", "content_hash",
    }
    expected_volatile = {"build_timestamp", "builder_identity", "host_platform"}
    missing = (expected_stable | expected_volatile) - set(manifest.keys())
    assert not missing, f"Missing manifest fields: {missing}"
    # Source block populated when source_close is supplied
    assert manifest["source_data"]["source_close_hash"]
    assert manifest["source_data"]["row_count"] == len(sample_close)
    assert manifest["package_versions"]["python"]
    assert manifest["package_versions"]["numpy"]


# ---------------------------------------------------------------------------
# F2: deterministic content hash ignores manifest build_timestamp
# ---------------------------------------------------------------------------


def test_f2_content_hash_independent_of_manifest_timestamp(sample_library):
    h1 = pm.content_hash(sample_library)
    # Sleep enough to guarantee a wall-clock difference in the manifest
    # timestamps but not in the underlying payload.
    time.sleep(0.01)
    lib_a = dict(sample_library)
    lib_b = dict(sample_library)
    pm.attach_manifest(
        lib_a, sidecar_path=None, artifact_type="signal_library_daily",
        ticker="SPY",
    )
    time.sleep(0.01)
    pm.attach_manifest(
        lib_b, sidecar_path=None, artifact_type="signal_library_daily",
        ticker="SPY",
    )
    # Different volatile build_timestamps...
    assert lib_a["_manifest"]["build_timestamp"] != lib_b["_manifest"]["build_timestamp"]
    # ...same content hash.
    assert lib_a["_manifest"]["content_hash"] == lib_b["_manifest"]["content_hash"] == h1


# ---------------------------------------------------------------------------
# F3: content hash excludes _manifest and top-level build_timestamp
# ---------------------------------------------------------------------------


def test_f3_content_hash_excludes_volatile_keys(sample_library):
    base = pm.content_hash(sample_library)
    lib_with_ts = dict(sample_library)
    lib_with_ts["build_timestamp"] = "2099-12-31T23:59:59"
    assert pm.content_hash(lib_with_ts) == base
    lib_with_manifest = dict(sample_library)
    lib_with_manifest["_manifest"] = {"content_hash": "FAKE", "schema_version": 1}
    assert pm.content_hash(lib_with_manifest) == base


# ---------------------------------------------------------------------------
# F4: sidecar + embedded round trip
# ---------------------------------------------------------------------------


def test_f4_sidecar_and_embedded_roundtrip(tmp_path, sample_library, sample_close):
    pkl_path = tmp_path / "SPY_stable.pkl"
    lib, manifest = pm.attach_manifest(
        sample_library,
        sidecar_path=pkl_path,
        artifact_type="signal_library_daily",
        ticker="SPY",
        source_close=sample_close,
    )
    # Persist pickle so we can roundtrip read.
    with open(pkl_path, "wb") as f:
        pickle.dump(lib, f)
    sidecar = tmp_path / (pkl_path.name + pm.SIDECAR_SUFFIX)
    assert sidecar.exists()
    sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_data["content_hash"] == manifest["content_hash"]
    assert sidecar_data["build_timestamp"] == manifest["build_timestamp"]

    with open(pkl_path, "rb") as f:
        loaded = pickle.load(f)
    embedded = pm.read_manifest(loaded, sidecar_path=pkl_path)
    assert embedded["content_hash"] == manifest["content_hash"]
    # Drift: corrupt the sidecar but keep embedded; read_manifest prefers embedded.
    sidecar.write_text(json.dumps({"content_hash": "WRONG"}), encoding="utf-8")
    embedded2 = pm.read_manifest(loaded, sidecar_path=pkl_path)
    assert embedded2["content_hash"] == manifest["content_hash"]


# ---------------------------------------------------------------------------
# F5: legacy missing manifest loads with warning and legacy=True
# ---------------------------------------------------------------------------


def test_f5_legacy_missing_manifest_is_ok(sample_library):
    # No manifest attached at all.
    result = pm.verify_manifest(sample_library)
    assert result.ok is True
    assert result.legacy is True
    assert any("legacy" in str(w).lower() or "no_manifest" in str(w)
               for w in result.warnings)


# ---------------------------------------------------------------------------
# F6: source hash mismatch detected only when current_source_close supplied
# ---------------------------------------------------------------------------


def test_f6_source_hash_check_optional(sample_library, sample_close):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    # No source supplied -> ok regardless of underlying drift.
    result = pm.verify_manifest(sample_library)
    assert result.ok is True
    assert not result.legacy
    # Same source -> ok.
    result_same = pm.verify_manifest(
        sample_library, current_source_close=sample_close
    )
    assert result_same.ok is True
    # Different source -> mismatch reported.
    drift_close = sample_close.copy()
    drift_close.iloc[0] = drift_close.iloc[0] * 2
    result_drift = pm.verify_manifest(
        sample_library, current_source_close=drift_close
    )
    assert result_drift.ok is False
    fields = [m[0] for m in result_drift.mismatches]
    assert "source_close_hash" in fields


# ---------------------------------------------------------------------------
# F7: param mismatch detected via requested_params subset comparison
# ---------------------------------------------------------------------------


def test_f7_requested_params_subset(sample_library):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        params={
            "MAX_SMA_DAY": 114,
            "price_source": "Close",
            "tiebreak_rule": "buy_first",
            "extra_field": "extra_value",
        },
    )
    # Subset that matches -> ok. Caller doesn't ask about extra_field.
    result_ok = pm.verify_manifest(
        sample_library,
        requested_params={"MAX_SMA_DAY": 114, "price_source": "Close"},
    )
    assert result_ok.ok is True
    # Subset where caller's MAX_SMA_DAY differs -> mismatch.
    result_bad = pm.verify_manifest(
        sample_library,
        requested_params={"MAX_SMA_DAY": 100},
    )
    assert result_bad.ok is False
    paths = [m[0] for m in result_bad.mismatches]
    assert any("MAX_SMA_DAY" in p for p in paths)
    # Numeric int/float equivalence does not flag.
    result_eq = pm.verify_manifest(
        sample_library,
        requested_params={"MAX_SMA_DAY": 114.0},
    )
    assert result_eq.ok is True


# ---------------------------------------------------------------------------
# F8: content hash mismatch detected when signal payload mutates
# ---------------------------------------------------------------------------


def test_f8_content_hash_mismatch_on_payload_mutation(sample_library):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
    )
    # Mutate non-volatile payload (signals) -> hash mismatch.
    sample_library["primary_signals"] = ["Short"] * len(
        sample_library["primary_signals"]
    )
    result = pm.verify_manifest(sample_library)
    assert result.ok is False
    fields = [m[0] for m in result.mismatches]
    assert "content_hash" in fields


# ---------------------------------------------------------------------------
# F9: dynamic version capture matches runtime numpy/pandas/scipy/python
# ---------------------------------------------------------------------------


def test_f9_dynamic_version_capture():
    pkgs = pm._capture_package_versions()
    assert pkgs["python"].startswith(
        f"{sys.version_info.major}.{sys.version_info.minor}."
    )
    assert pkgs["numpy"] == np.__version__
    assert pkgs["pandas"] == pd.__version__
    import scipy
    assert pkgs["scipy"] == scipy.__version__


# ---------------------------------------------------------------------------
# F10: git capture in repo, unknown gracefully outside
# ---------------------------------------------------------------------------


def test_f10_git_capture_in_repo_returns_sha():
    info = pm._capture_git_info(repo_root=PROJECT_DIR)
    assert info["commit"] != "unknown", (
        "Expected a real SHA when invoked inside the repo"
    )
    assert re.fullmatch(r"[0-9a-f]{7,40}", info["commit"])
    assert info["dirty"] in (True, False, None)


def test_f10b_git_capture_outside_repo_returns_unknown(tmp_path):
    info = pm._capture_git_info(repo_root=tmp_path)
    # Either the system has no git (returncode != 0) or the directory is
    # not in a worktree; both produce "unknown" gracefully.
    assert info["commit"] == "unknown"
    assert info["dirty"] is None


# ---------------------------------------------------------------------------
# F4-companion: refresh_or_attach preserves source_data when no source given
# ---------------------------------------------------------------------------


def test_f_refresh_preserves_source_data_when_no_source(sample_library, sample_close):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    original_source = dict(sample_library["_manifest"]["source_data"])
    # Mutate a non-volatile field so content_hash will change.
    sample_library["primary_signals"] = ["Buy"] * len(
        sample_library["primary_signals"]
    )
    _, new_manifest, was_refresh = pm.refresh_or_attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        # No source_close supplied -> existing source_data preserved.
    )
    assert was_refresh is True
    assert new_manifest["source_data"] == original_source
    # And content_hash was refreshed against the mutated payload.
    assert new_manifest["content_hash"] == pm.content_hash(sample_library)


# ---------------------------------------------------------------------------
# F16: metadata-repair persist preserves existing manifest
# ---------------------------------------------------------------------------


def test_f16_metadata_repair_persist_preserves_manifest(tmp_path, monkeypatch):
    """When _persist_library_metadata runs over a library that already
    carries a manifest (set at the original save), the repair must
    refresh content_hash but preserve the existing source_data block —
    no source_close is in scope at the repair site.
    """
    sys.path.insert(0, str(PROJECT_DIR))
    import onepass

    dates = pd.bdate_range(start="2024-01-02", periods=20)
    closes = make_synthetic_close_prices(dates)
    sigs = ["Buy", "Short", "None"] * (len(dates) // 3) + ["None"] * (
        len(dates) % 3
    )
    lib = make_signal_library_dict(dates, primary_signals=sigs)
    lib["build_timestamp"] = "2025-01-01T00:00:00"
    # Simulate the producer-attached manifest (with real source_data).
    pm.attach_manifest(
        lib, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="AAA",
        params={"MAX_SMA_DAY": 114, "price_source": "Close"},
        source_close=closes,
    )
    original_source = dict(lib["_manifest"]["source_data"])

    # Point onepass's library dir at tmp_path so the persist writes
    # land there. _lib_path_for is a module-level helper; monkey-patch
    # SIGNAL_LIBRARY_DIR to tmp_path.
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))

    # Mutate a non-volatile metadata field so content_hash will change.
    lib.setdefault("meta", {})["persist_skip_bars"] = 99
    onepass._persist_library_metadata("AAA", lib)

    # Source preserved, content_hash refreshed.
    assert lib["_manifest"]["source_data"] == original_source
    assert lib["_manifest"]["content_hash"] == pm.content_hash(lib)
    # Pickle landed on disk with the manifest embedded.
    saved_path = Path(onepass._lib_path_for("AAA"))
    assert saved_path.exists()
    with open(saved_path, "rb") as f:
        loaded = pickle.load(f)
    assert loaded["_manifest"]["source_data"] == original_source


# ---------------------------------------------------------------------------
# Consumer hook helpers
# ---------------------------------------------------------------------------


def _write_lib_for_consumer(
    library_dir: Path,
    ticker: str,
    *,
    engine_version: str = "1.0.0",
    price_source: str = "Close",
    parity_hash: str = "PHASE3A_PARITY_HASH",
    with_manifest: bool = True,
    mutate_after_attach: bool = False,
    interval: str = "1d",
) -> Path:
    """Build a tiny signal library on disk and (optionally) attach a manifest.

    Used by the C1-C5 consumer hook tests. Filenames mirror the engine
    naming convention: ``<ticker>_stable_v1_0_0.pkl`` for daily,
    ``<ticker>_stable_v1_0_0_<interval>.pkl`` otherwise.
    """
    library_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(start="2024-01-02", periods=20)
    closes = make_synthetic_close_prices(dates)
    sigs = ["Buy", "Short", "None"] * (len(dates) // 3) + ["None"] * (
        len(dates) % 3
    )
    lib = make_signal_library_dict(
        dates,
        engine_version=engine_version,
        price_source=price_source,
        parity_hash=parity_hash,
        primary_signals=sigs,
    )
    lib["signals"] = list(sigs)
    lib["interval"] = interval
    lib["ticker"] = ticker
    lib["build_timestamp"] = "2025-01-01T00:00:00"

    if interval == "1d":
        fname = f"{ticker}_stable_v{engine_version.replace('.', '_')}.pkl"
    else:
        fname = (
            f"{ticker}_stable_v{engine_version.replace('.', '_')}"
            f"_{interval}.pkl"
        )
    path = library_dir / fname

    if with_manifest:
        pm.attach_manifest(
            lib,
            path,
            artifact_type=(
                "signal_library_daily" if interval == "1d"
                else "interval_signal_library"
            ),
            ticker=ticker,
            interval=interval,
            params={
                "engine_version": engine_version,
                "MAX_SMA_DAY": 114,
                "price_source": price_source,
                "parity_hash": parity_hash,
                "interval": interval,
            },
            source_close=closes,
            engine_version=engine_version,
        )
        if mutate_after_attach:
            # Tamper after manifest attach -> content_hash mismatch.
            lib["primary_signals"] = ["Short"] * len(lib["primary_signals"])

    with open(path, "wb") as f:
        pickle.dump(lib, f)
    return path


# ---------------------------------------------------------------------------
# F11: onepass consumer verification hook
# ---------------------------------------------------------------------------


def test_f11_onepass_consumer_verifies(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import onepass

    library_dir = tmp_path / "stable"
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))

    # Valid manifest -> load succeeds.
    _write_lib_for_consumer(
        library_dir, "AAA",
        parity_hash=onepass.compute_parity_hash(),
    )
    lib = onepass.load_signal_library("AAA")
    assert lib is not None
    assert lib["ticker"] == "AAA"

    # Tampered library (manifest mismatch) -> load returns None.
    _write_lib_for_consumer(
        library_dir, "BBB",
        parity_hash=onepass.compute_parity_hash(),
        mutate_after_attach=True,
    )
    assert onepass.load_signal_library("BBB") is None

    # Legacy library (no manifest) -> load still works (warning).
    _write_lib_for_consumer(
        library_dir, "CCC",
        parity_hash=onepass.compute_parity_hash(),
        with_manifest=False,
    )
    legacy = onepass.load_signal_library("CCC")
    assert legacy is not None
    assert legacy["ticker"] == "CCC"


# ---------------------------------------------------------------------------
# F12: impactsearch consumer verification hook
# ---------------------------------------------------------------------------


def test_f12_impactsearch_consumer_verifies(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import impactsearch

    library_dir = tmp_path / "stable"
    monkeypatch.setattr(impactsearch, "SIGNAL_LIBRARY_DIR", str(tmp_path))

    _write_lib_for_consumer(library_dir, "AAA")
    lib = impactsearch.load_signal_library("AAA")
    assert lib is not None

    _write_lib_for_consumer(library_dir, "BBB", mutate_after_attach=True)
    assert impactsearch.load_signal_library("BBB") is None

    _write_lib_for_consumer(library_dir, "CCC", with_manifest=False)
    legacy = impactsearch.load_signal_library("CCC")
    assert legacy is not None


# ---------------------------------------------------------------------------
# F13: impact_fastpath consumer verification hook
# ---------------------------------------------------------------------------


def test_f13_impact_fastpath_consumer_verifies(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    from signal_library import impact_fastpath

    library_dir = tmp_path / "stable"
    monkeypatch.setattr(impact_fastpath, "SIGNAL_LIBRARY_DIR", str(tmp_path))

    _write_lib_for_consumer(library_dir, "AAA")
    lib = impact_fastpath._load_signal_library_quick("AAA")
    assert lib is not None

    _write_lib_for_consumer(library_dir, "BBB", mutate_after_attach=True)
    assert impact_fastpath._load_signal_library_quick("BBB") is None

    _write_lib_for_consumer(library_dir, "CCC", with_manifest=False)
    legacy = impact_fastpath._load_signal_library_quick("CCC")
    assert legacy is not None


# ---------------------------------------------------------------------------
# F14: stackbuilder consumer verification hook
# ---------------------------------------------------------------------------


def test_f14_stackbuilder_fallback_load_verifies(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder

    library_dir = tmp_path / "stable"
    monkeypatch.setattr(stackbuilder, "SIGNAL_LIB_DIR_RUNTIME", str(library_dir))

    _write_lib_for_consumer(library_dir, "AAA")
    lib = stackbuilder.fallback_load_signal_library("AAA")
    assert lib is not None

    _write_lib_for_consumer(library_dir, "BBB", mutate_after_attach=True)
    assert stackbuilder.fallback_load_signal_library("BBB") is None

    _write_lib_for_consumer(library_dir, "CCC", with_manifest=False)
    legacy = stackbuilder.fallback_load_signal_library("CCC")
    assert legacy is not None


# ---------------------------------------------------------------------------
# F15: confluence interval consumer verification hook
# ---------------------------------------------------------------------------


def test_f15_confluence_interval_consumer_verifies(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    from signal_library import confluence_analyzer

    library_dir = tmp_path / "stable"
    monkeypatch.setattr(confluence_analyzer, "SIGNAL_LIBRARY_DIR",
                        str(library_dir))

    # Use a non-daily interval so we do not hit the spymaster fallback.
    _write_lib_for_consumer(library_dir, "AAA", interval="1wk")
    lib = confluence_analyzer.load_signal_library_interval("AAA", "1wk")
    assert lib is not None

    _write_lib_for_consumer(
        library_dir, "BBB", interval="1wk", mutate_after_attach=True
    )
    assert confluence_analyzer.load_signal_library_interval("BBB", "1wk") is None

    _write_lib_for_consumer(
        library_dir, "CCC", interval="1wk", with_manifest=False
    )
    legacy = confluence_analyzer.load_signal_library_interval("CCC", "1wk")
    assert legacy is not None


# ---------------------------------------------------------------------------
# F17: B12 static guard catches unverified pickle.load
# ---------------------------------------------------------------------------


def test_f17_b12_guard_catches_unverified_consumer(tmp_path):
    """Reuse the B12 helper from test_static_regression_guards to confirm
    a synthetic consumer that pickle.loads without verify_manifest is
    flagged.
    """
    sys.path.insert(0, str(PROJECT_DIR / "test_scripts"))
    from test_static_regression_guards import (  # type: ignore
        _function_calls_name, _find_function,
    )
    import ast

    # A 'consumer' that loads pickle but never calls verify_manifest
    bad_source = (
        "import pickle\n"
        "def load_signal_library(ticker):\n"
        "    with open('foo.pkl', 'rb') as f:\n"
        "        return pickle.load(f)\n"
    )
    tree = ast.parse(bad_source)
    func = _find_function(tree, "load_signal_library")
    assert func is not None
    assert not _function_calls_name(
        func, ("verify_manifest", "_verify_manifest")
    ), "Expected the bad consumer to be flagged (no verify_manifest call)."

    # A 'consumer' that does call verify_manifest
    good_source = (
        "import pickle\n"
        "from provenance_manifest import verify_manifest as _verify_manifest\n"
        "def load_signal_library(ticker):\n"
        "    with open('foo.pkl', 'rb') as f:\n"
        "        data = pickle.load(f)\n"
        "    _verify_manifest(data)\n"
        "    return data\n"
    )
    tree2 = ast.parse(good_source)
    func2 = _find_function(tree2, "load_signal_library")
    assert func2 is not None
    assert _function_calls_name(
        func2, ("verify_manifest", "_verify_manifest")
    ), "Expected the good consumer to pass the B12 check."
