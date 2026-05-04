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


# ===========================================================================
# Phase 3B-1: content_hash performance cache
# ===========================================================================


@pytest.fixture
def cache_lib_on_disk(tmp_path, sample_library, sample_close):
    """Manifested library written to disk; returns (path, lib)."""
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        params={"engine_version": "1.0.0", "MAX_SMA_DAY": 114},
        source_close=sample_close,
    )
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    return p, sample_library


def test_3b1_cache_uncached_path_detects_in_memory_mutation(sample_library):
    """verify_manifest with cache_path=None must still recompute, so
    in-memory mutations between attach and verify are caught.
    """
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
    )
    pm.manifest_hash_cache_clear()
    sample_library["primary_signals"] = ["Short"] * len(
        sample_library["primary_signals"]
    )
    result = pm.verify_manifest(sample_library)  # no cache_path
    assert result.ok is False
    assert any(m[0] == "content_hash" for m in result.mismatches)


def test_3b1_cache_repeat_load_hits(cache_lib_on_disk):
    p, _ = cache_lib_on_disk
    pm.manifest_hash_cache_clear()
    pm.load_verified_signal_library(p)
    pm.load_verified_signal_library(p)
    info = pm.manifest_hash_cache_info()
    assert info["hits"] >= 1
    assert info["misses"] == 1
    assert info["current_size"] == 1


def test_3b1_cache_aliases_resolve_to_same_key(cache_lib_on_disk, monkeypatch):
    p, _ = cache_lib_on_disk
    pm.manifest_hash_cache_clear()
    pm.load_verified_signal_library(p)
    # Same file via a relative path that resolves to the same absolute path.
    monkeypatch.chdir(p.parent)
    pm.load_verified_signal_library(Path(p.name))
    info = pm.manifest_hash_cache_info()
    assert info["hits"] >= 1
    assert info["misses"] == 1


def test_3b1_cache_different_paths_miss_separately(tmp_path, sample_library, sample_close):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    p1 = tmp_path / "a.pkl"
    p2 = tmp_path / "b.pkl"
    with open(p1, "wb") as f:
        pickle.dump(sample_library, f)
    with open(p2, "wb") as f:
        pickle.dump(sample_library, f)
    pm.manifest_hash_cache_clear()
    pm.load_verified_signal_library(p1)
    pm.load_verified_signal_library(p2)
    info = pm.manifest_hash_cache_info()
    assert info["misses"] == 2
    assert info["hits"] == 0
    assert info["current_size"] == 2


def test_3b1_cache_size_change_invalidates(tmp_path, sample_library, sample_close):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    pm.manifest_hash_cache_clear()
    pm.load_verified_signal_library(p)
    # Append bytes -> size changes -> cache key changes -> miss again.
    with open(p, "ab") as f:
        f.write(b"\x00")
    pm.load_verified_signal_library(p)  # will fail unpickle but still attempts load
    info = pm.manifest_hash_cache_info()
    # Expect a second miss (or unchanged hits because the second load
    # errored before content_hash). Either way: hits did not grow.
    assert info["hits"] == 0


def test_3b1_cache_mtime_change_invalidates(tmp_path, sample_library, sample_close):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    pm.manifest_hash_cache_clear()
    pm.load_verified_signal_library(p)
    # Bump mtime; size unchanged.
    st = os.stat(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    pm.load_verified_signal_library(p)
    info = pm.manifest_hash_cache_info()
    assert info["misses"] == 2
    assert info["hits"] == 0


def test_3b1_cache_atomic_replace_invalidates(tmp_path, sample_library, sample_close):
    """Atomic ``os.replace`` invalidates the LRU cache via mtime delta.

    Phase 3B-2A tightening: explicit ``os.utime`` controls mtime instead
    of ``time.sleep(1.05)`` past the FS mtime resolution. The replacement
    payload is the *same bytes* as the original so size is guaranteed
    unchanged — the cache miss therefore depends on the mtime key
    component, not the size key component.
    """
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    original_size = p.stat().st_size
    original_mtime_ns = p.stat().st_mtime_ns
    pm.manifest_hash_cache_clear()
    pm.load_verified_signal_library(p)
    # Same bytes -> same size -> only mtime can drive invalidation.
    payload_bytes = p.read_bytes()
    tmp_replacement = tmp_path / "lib.pkl.tmp"
    tmp_replacement.write_bytes(payload_bytes)
    os.replace(tmp_replacement, p)
    # Bump mtime explicitly past the original; same-size guarantee
    # ensures the cache key only differs in the mtime component.
    new_mtime_ns = original_mtime_ns + 2_000_000_000
    os.utime(p, ns=(new_mtime_ns, new_mtime_ns))
    assert p.stat().st_size == original_size
    pm.load_verified_signal_library(p)
    info = pm.manifest_hash_cache_info()
    assert info["misses"] == 2  # original + post-replace
    assert info["hits"] == 0


def test_3b1_cache_inplace_rewrite_invalidates(tmp_path, sample_library, sample_close):
    """In-place rewrite invalidates the LRU cache via mtime delta.

    Phase 3B-2A tightening: the in-place rewrite writes the same bytes
    back, then ``os.utime`` bumps mtime explicitly. Size unchanged, so
    the cache miss is unambiguously driven by the mtime key.
    """
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    original_size = p.stat().st_size
    original_mtime_ns = p.stat().st_mtime_ns
    pm.manifest_hash_cache_clear()
    pm.load_verified_signal_library(p)
    # Same bytes again, same size; only mtime drives invalidation.
    payload_bytes = p.read_bytes()
    with open(p, "wb") as f:
        f.write(payload_bytes)
    new_mtime_ns = original_mtime_ns + 2_000_000_000
    os.utime(p, ns=(new_mtime_ns, new_mtime_ns))
    assert p.stat().st_size == original_size
    pm.load_verified_signal_library(p)
    info = pm.manifest_hash_cache_info()
    assert info["misses"] == 2
    assert info["hits"] == 0


def test_3b1_cache_lru_eviction(tmp_path, sample_library, sample_close, monkeypatch):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    # Shrink the LRU bound for the duration of the test.
    monkeypatch.setattr(pm, "_MANIFEST_HASH_CACHE_MAX", 3)
    pm.manifest_hash_cache_clear()
    paths = []
    for i in range(4):
        p = tmp_path / f"lib_{i}.pkl"
        with open(p, "wb") as f:
            pickle.dump(sample_library, f)
        paths.append(p)
        pm.load_verified_signal_library(p)
    info = pm.manifest_hash_cache_info()
    assert info["current_size"] == 3
    assert info["evictions"] >= 1
    assert info["max_size"] == 3


def test_3b1_cache_env_var_disable(tmp_path, sample_library, sample_close, monkeypatch):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    monkeypatch.setenv("PRJCT9_DISABLE_MANIFEST_HASH_CACHE", "1")
    pm.manifest_hash_cache_clear()
    pm.load_verified_signal_library(p)
    pm.load_verified_signal_library(p)
    info = pm.manifest_hash_cache_info()
    # No insertions while disabled, no hits either.
    assert info["hits"] == 0
    assert info["misses"] == 0
    assert info["current_size"] == 0
    assert info["enabled"] is False


def test_3b1_cache_threaded_smoke(cache_lib_on_disk):
    """Concurrent loads from many threads must not raise; stats remain sane."""
    import threading
    p, _ = cache_lib_on_disk
    pm.manifest_hash_cache_clear()
    errs = []

    def worker():
        try:
            for _ in range(20):
                pm.load_verified_signal_library(p)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errs, errs
    info = pm.manifest_hash_cache_info()
    assert info["misses"] >= 1
    assert info["hits"] >= 1
    # Same path -> at most one entry.
    assert info["current_size"] == 1


# ===========================================================================
# Phase 3B-1: central verified loader
# ===========================================================================


def test_3b1_loader_success_and_cache(tmp_path, sample_library, sample_close):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        params={"engine_version": "1.0.0", "MAX_SMA_DAY": 114},
        source_close=sample_close,
    )
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    pm.manifest_hash_cache_clear()
    lib, result = pm.load_verified_signal_library(
        p,
        requested_params={"engine_version": "1.0.0", "MAX_SMA_DAY": 114},
    )
    assert lib is not None
    assert result.ok is True
    assert not result.legacy
    info = pm.manifest_hash_cache_info()
    assert info["misses"] == 1


def test_3b1_loader_legacy(tmp_path, sample_library):
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)  # no manifest
    lib, result = pm.load_verified_signal_library(p)
    assert lib is not None
    assert result.ok is True
    assert result.legacy is True


def test_3b1_loader_mismatch(tmp_path, sample_library, sample_close):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    sample_library["primary_signals"] = ["Short"] * len(
        sample_library["primary_signals"]
    )
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    lib, result = pm.load_verified_signal_library(p)
    assert lib is not None
    assert result.ok is False
    assert any(m[0] == "content_hash" for m in result.mismatches)


def test_3b1_loader_load_error_corrupt(tmp_path):
    p = tmp_path / "corrupt.pkl"
    p.write_bytes(b"\x80\x04not a real pickle")
    lib, result = pm.load_verified_signal_library(p)
    assert lib is None
    assert result.ok is False
    assert result.legacy is False
    assert any(m[0] == "load_error" for m in result.mismatches)


def test_3b1_loader_type_error_non_dict(tmp_path):
    p = tmp_path / "string.pkl"
    with open(p, "wb") as f:
        pickle.dump("not-a-dict", f)
    lib, result = pm.load_verified_signal_library(p)
    assert lib is None
    assert result.ok is False
    assert any(m[0] == "type_error" for m in result.mismatches)


def test_3b1_loader_strict_runtime_mismatch(tmp_path, sample_library, sample_close):
    pm.attach_manifest(
        sample_library, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="SPY",
        source_close=sample_close,
    )
    # Forge a runtime mismatch by editing the embedded manifest's
    # package_versions before persist.
    sample_library["_manifest"]["package_versions"]["numpy"] = "0.0.0"
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    # strict=False: warn-only.
    lib, result = pm.load_verified_signal_library(p, strict=False)
    assert lib is not None
    assert result.ok is True
    assert any("numpy" in str(w) for w in result.warnings)
    # strict=True: same drift escalates to a mismatch.
    lib2, result2 = pm.load_verified_signal_library(p, strict=True, cache=False)
    assert lib2 is not None
    assert result2.ok is False
    assert any("numpy" in str(m[0]) for m in result2.mismatches)


def test_3b1_pickle_load_compat_smoke(tmp_path, sample_library):
    p = tmp_path / "lib.pkl"
    with open(p, "wb") as f:
        pickle.dump(sample_library, f)
    with open(p, "rb") as f:
        loaded = pm.pickle_load_compat(f)
    assert isinstance(loaded, dict)
    assert loaded.get("primary_signals") == sample_library["primary_signals"]


# ---------------------------------------------------------------------------
# Phase 3B-1: B12 raw-pickle-load scanner contract
# ---------------------------------------------------------------------------


def test_3b1_b12_synthetic_bad_raw_load_is_caught(tmp_path):
    """Feed _scan_raw_pickle_loads a file with an unallowlisted raw
    ``pickle.load(...)`` call and confirm the scanner reports it.
    Symmetric: a file that uses ``pickle_load_compat`` instead is clean.
    """
    sys.path.insert(0, str(PROJECT_DIR / "test_scripts"))
    from test_static_regression_guards import _scan_raw_pickle_loads  # type: ignore

    bad = tmp_path / "bad_consumer.py"
    bad.write_text(
        "import pickle\n"
        "def load_lib(p):\n"
        "    with open(p, 'rb') as f:\n"
        "        return pickle.load(f)\n",
        encoding="utf-8",
    )
    hits = _scan_raw_pickle_loads(bad)
    assert hits, "Expected scanner to flag the raw pickle.load call"
    assert "pickle.load" in hits[0][1]

    good = tmp_path / "good_consumer.py"
    good.write_text(
        "from provenance_manifest import pickle_load_compat\n"
        "def load_lib(p):\n"
        "    with open(p, 'rb') as f:\n"
        "        return pickle_load_compat(f)\n",
        encoding="utf-8",
    )
    assert not _scan_raw_pickle_loads(good), (
        "Expected pickle_load_compat to NOT trip the raw-load scanner"
    )


# ===========================================================================
# Phase 3B-2A: output manifest helper + verified loaders
# ===========================================================================


def _build_sample_output_pickle(tmp_path, *, content):
    """Producer-side helper: build a manifested pickle artifact + sidecar.

    Returns (path, manifest). Mirrors the producer pattern:
      1. build core manifest (content_obj = the dict to pickle, sans _manifest)
      2. embed manifest in dict
      3. pickle.dump
      4. compute file_sha256 of the final pickle
      5. write sidecar with artifact_file_sha256
    """
    p = tmp_path / "output.pkl"
    manifest = pm.build_output_manifest(
        artifact_type="spymaster_precomputed_results",
        producer_engine="spymaster",
        engine_version="1.0.0",
        params={"MAX_SMA_DAY": 114},
        content_obj=content,
    )
    payload = dict(content)
    payload["_manifest"] = manifest
    with open(p, "wb") as f:
        pickle.dump(payload, f)
    pm.write_output_manifest(p, manifest, include_file_sha256=True)
    return p, manifest


def test_3b2a_output_manifest_required_fields():
    m = pm.build_output_manifest(
        artifact_type="stackbuilder_run",
        producer_engine="stackbuilder",
        engine_version="1.0.0",
        params={"max_combo_size": 3},
        cli_args={"primary": "SPY"},
        content_obj={"rows": [1, 2, 3]},
    )
    expected = {
        "schema_version", "artifact_kind", "artifact_type",
        "producer_engine", "engine_version", "params", "cli_args",
        "ui_args", "input_manifest_hashes", "input_secondary_hash",
        "output_schema", "git_commit", "git_dirty", "package_versions",
        "build_timestamp", "builder_identity", "host_platform",
        "content_hash",
    }
    assert expected <= set(m.keys())
    assert m["artifact_kind"] == pm.ARTIFACT_KIND_OUTPUT
    assert m["producer_engine"] == "stackbuilder"
    assert m["content_hash"] is not None


def test_3b2a_file_sha256_sidecar_verification(tmp_path):
    content = {"rows": [1, 2, 3], "ticker": "SPY"}
    p, manifest = _build_sample_output_pickle(tmp_path, content=content)
    sidecar = pm._sidecar_path_for(p)
    assert sidecar.exists()
    sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_data["artifact_file_sha256"]
    assert sidecar_data["artifact_file_sha256"] == pm.file_sha256(p)
    # The embedded manifest must NOT include the final pickle file SHA.
    with open(p, "rb") as f:
        loaded = pickle.load(f)
    assert "artifact_file_sha256" not in loaded["_manifest"], (
        "Embedded manifest must not self-reference its own file SHA"
    )


def test_3b2a_embedded_pickle_manifest_verifies(tmp_path):
    content = {"rows": [1, 2, 3], "ticker": "SPY"}
    p, manifest = _build_sample_output_pickle(tmp_path, content=content)
    data, result = pm.load_verified_pickle_artifact(p)
    assert data is not None
    assert result.ok is True
    assert not result.legacy


def test_3b2a_legacy_pickle_loads_ok_legacy(tmp_path):
    p = tmp_path / "legacy.pkl"
    with open(p, "wb") as f:
        pickle.dump({"foo": "bar"}, f)  # no manifest
    data, result = pm.load_verified_pickle_artifact(p)
    assert data == {"foo": "bar"}
    assert result.ok is True
    assert result.legacy is True


def test_3b2a_pickle_content_hash_mismatch(tmp_path):
    content = {"rows": [1, 2, 3]}
    p, _ = _build_sample_output_pickle(tmp_path, content=content)
    # Tamper: rewrite the pickle with mutated content but the OLD manifest.
    with open(p, "rb") as f:
        loaded = pickle.load(f)
    loaded["rows"] = [9, 9, 9]
    with open(p, "wb") as f:
        pickle.dump(loaded, f)
    # Refresh the sidecar's artifact_file_sha256 so the file-byte check
    # passes; we want to isolate the logical content_hash mismatch.
    sidecar_path = pm._sidecar_path_for(p)
    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar_data["artifact_file_sha256"] = pm.file_sha256(p)
    sidecar_path.write_text(json.dumps(sidecar_data), encoding="utf-8")
    data, result = pm.load_verified_pickle_artifact(p, cache=False)
    assert result.ok is False
    fields = [m[0] for m in result.mismatches]
    assert "content_hash" in fields


def test_3b2a_pickle_file_sha256_mismatch(tmp_path):
    content = {"rows": [1, 2, 3]}
    p, _ = _build_sample_output_pickle(tmp_path, content=content)
    sidecar_path = pm._sidecar_path_for(p)
    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar_data["artifact_file_sha256"] = "0" * 64
    sidecar_path.write_text(json.dumps(sidecar_data), encoding="utf-8")
    data, result = pm.load_verified_pickle_artifact(p)
    assert data is not None
    assert result.ok is False
    fields = [m[0] for m in result.mismatches]
    assert "artifact_file_sha256" in fields


def test_3b2a_json_artifact_verification(tmp_path):
    """A non-self JSON artifact with a sidecar manifest verifies cleanly."""
    payload = {"top_buy_pair": [10, 1], "top_short_pair": [1, 10]}
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    manifest = pm.build_output_manifest(
        artifact_type="stackbuilder_summary",
        producer_engine="stackbuilder",
        engine_version="1.0.0",
        content_obj=payload,
    )
    pm.write_output_manifest(p, manifest, include_file_sha256=True)
    data, result = pm.load_verified_json_artifact(p)
    assert data == payload
    assert result.ok is True
    # Tamper: rewrite the JSON file with a different object.
    p.write_text(json.dumps({"different": True}), encoding="utf-8")
    data, result = pm.load_verified_json_artifact(p)
    assert result.ok is False
    fields = [m[0] for m in result.mismatches]
    assert "artifact_file_sha256" in fields or "content_hash" in fields


def test_3b2a_strict_runtime_mismatch_pickle(tmp_path):
    content = {"rows": [1, 2, 3]}
    p, _ = _build_sample_output_pickle(tmp_path, content=content)
    # Forge a runtime mismatch in the embedded manifest.
    with open(p, "rb") as f:
        loaded = pickle.load(f)
    loaded["_manifest"]["package_versions"]["numpy"] = "0.0.0"
    with open(p, "wb") as f:
        pickle.dump(loaded, f)
    # Refresh sidecar so file_sha256 matches and only runtime drift is seen.
    sidecar_path = pm._sidecar_path_for(p)
    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar_data["package_versions"]["numpy"] = "0.0.0"
    sidecar_data["artifact_file_sha256"] = pm.file_sha256(p)
    sidecar_path.write_text(json.dumps(sidecar_data), encoding="utf-8")
    # Refresh embedded content_hash so we isolate the runtime drift.
    with open(p, "rb") as f:
        loaded2 = pickle.load(f)
    embedded_manifest = loaded2["_manifest"]
    raw_content = {k: v for k, v in loaded2.items() if k != "_manifest"}
    embedded_manifest["content_hash"] = pm.content_hash(loaded2)
    loaded2["_manifest"] = embedded_manifest
    with open(p, "wb") as f:
        pickle.dump(loaded2, f)
    sidecar_data["artifact_file_sha256"] = pm.file_sha256(p)
    sidecar_path.write_text(json.dumps(sidecar_data), encoding="utf-8")
    # strict=False: warn-only.
    pm.manifest_hash_cache_clear()
    data, result = pm.load_verified_pickle_artifact(p, strict=False, cache=False)
    assert data is not None
    assert result.ok is True
    assert any("numpy" in str(w) for w in result.warnings)
    # strict=True: fail.
    pm.manifest_hash_cache_clear()
    data2, result2 = pm.load_verified_pickle_artifact(p, strict=True, cache=False)
    assert result2.ok is False
    assert any("numpy" in str(m[0]) for m in result2.mismatches)


# ===========================================================================
# Phase 3B-2A: StackBuilder run_manifest enrichment
# ===========================================================================


def test_3b2a_stackbuilder_input_manifest_collector(tmp_path):
    """Collector accumulates content_hashes from manifested loads,
    counts legacy/missing inputs, and resets when finalized."""
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder

    stackbuilder._start_input_manifest_collection()
    # Manifested libs -> content_hash should land in the collector. The
    # two libs MUST differ so their content_hashes differ; ticker is in
    # the manifest, not in the canonical content body.
    dates = pd.bdate_range(start="2024-01-02", periods=10)
    lib_a = make_signal_library_dict(
        dates, primary_signals=["Buy"] * len(dates),
    )
    pm.attach_manifest(
        lib_a, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="AAA",
    )
    lib_b = make_signal_library_dict(
        dates, primary_signals=["Short"] * len(dates),
    )
    pm.attach_manifest(
        lib_b, sidecar_path=None,
        artifact_type="signal_library_daily", ticker="BBB",
    )
    legacy = make_signal_library_dict(dates)  # no manifest

    stackbuilder._record_input_lib(lib_a)
    stackbuilder._record_input_lib(lib_b)
    stackbuilder._record_input_lib(lib_a)  # duplicate -> set dedupes
    stackbuilder._record_input_lib(legacy)
    stackbuilder._record_input_lib(None)
    snap = stackbuilder._finalize_input_manifest_collection()
    assert len(snap["input_manifest_hashes"]) == 2  # deduped
    assert snap["input_legacy_count"] == 1
    assert snap["input_missing_manifest_count"] == 1
    # After finalize, recording is a no-op until next start.
    stackbuilder._record_input_lib(lib_a)
    snap2 = stackbuilder._finalize_input_manifest_collection()
    assert snap2["input_manifest_hashes"] == []
    assert snap2["input_legacy_count"] == 0
    assert snap2["input_missing_manifest_count"] == 0


def test_3b2a_stackbuilder_output_artifact_entry(tmp_path):
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder

    # CSV: row_count and column_schema should be populated.
    csv_path = tmp_path / "rank_direct.csv"
    csv_path.write_text(
        "Primary Ticker,Total Capture (%)\n"
        "SPY,123.4\n"
        "QQQ,210.5\n",
        encoding="utf-8",
    )
    entry = stackbuilder._output_artifact_entry(str(tmp_path), "rank_direct")
    assert entry is not None
    assert entry["filename"] == "rank_direct.csv"
    assert entry["format"] == "csv"
    assert entry["row_count"] == 2
    assert entry["column_schema"] == [
        {"name": "Primary Ticker"},
        {"name": "Total Capture (%)"},
    ]
    assert entry["file_sha256"] == pm.file_sha256(csv_path)
    # Missing artifact -> None.
    assert stackbuilder._output_artifact_entry(str(tmp_path), "missing") is None


def test_3b2a_stackbuilder_build_output_artifacts(tmp_path):
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder

    (tmp_path / "rank_all.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "rank_direct.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "cohort.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "combo_leaderboard.csv").write_text("a\n1\n", encoding="utf-8")
    (tmp_path / "summary.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    artifacts = stackbuilder._build_output_artifacts(str(tmp_path))
    names = sorted(a["name"] for a in artifacts)
    assert names == [
        "cohort", "combo_leaderboard", "rank_all", "rank_direct", "summary",
    ]
    for a in artifacts:
        assert a["file_sha256"]
        assert a["produced_at"]


def test_3b2a_stackbuilder_run_manifest_preserves_legacy_keys():
    """A synthetic enrichment over a known-shaped legacy manifest must
    keep the Phase 3A keys callers depend on (no run_manifest readers
    exist outside stackbuilder, but downstream tooling may grow over
    time). This test pins the legacy keys at the producer site.
    """
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder

    legacy_keys = {"secondary", "started_at", "params", "outputs"}
    enriched_keys = {
        "schema_version", "artifact_kind", "artifact_type",
        "producer_engine", "engine_version", "run_id",
        "git_commit", "git_dirty", "package_versions",
        "build_timestamp", "builder_identity", "host_platform",
        "cli_args", "status", "output_artifacts",
        "input_manifest_hashes", "input_legacy_count",
        "input_missing_manifest_count", "input_secondary_hash",
        "finished_at", "elapsed_seconds",
    }
    # Source-text grep: ensure both blocks of legacy keys + enriched
    # keys appear in stackbuilder's run_for_secondary writer.
    source = (PROJECT_DIR / "stackbuilder.py").read_text(encoding="utf-8")
    for key in legacy_keys | enriched_keys:
        assert f"'{key}'" in source or f'"{key}"' in source, (
            f"Expected stackbuilder.py to set manifest key '{key}'"
        )


# ===========================================================================
# Phase 3B-2A: Spymaster PKL manifest (producer / consumer)
# ===========================================================================


def _build_synthetic_spymaster_pkl(
    path,
    *,
    ticker: str = "AAA",
    with_manifest: bool = True,
    mutate_after_attach: bool = False,
):
    """Build a synthetic Spymaster precomputed-results PKL on disk.

    Mirrors the producer pattern in ``spymaster.save_precomputed_results``:
    embed the manifest, pickle.dump, then write the sidecar with
    artifact_file_sha256 over final bytes. Used by Spymaster + TrafficFlow
    + Confluence consumer tests.
    """
    dates = pd.bdate_range(start="2024-01-02", periods=8)
    payload = {
        "_ticker": ticker,
        "preprocessed_data": pd.DataFrame(
            {"Close": [100.0 + i for i in range(len(dates))]}, index=dates,
        ),
        "daily_top_buy_pairs": {d: ((114, 113), float(i + 1))
                                for i, d in enumerate(dates)},
        "daily_top_short_pairs": {d: ((113, 114), float(i + 1))
                                  for i, d in enumerate(dates)},
        "top_buy_pair": (10, 1),
        "top_short_pair": (1, 10),
        "max_sma_day": 114,
        "engine_version": "1.0.0",
        "price_source": "Close",
        "data_fingerprint": "fp-" + ticker,
    }
    if with_manifest:
        manifest = pm.build_output_manifest(
            artifact_type="spymaster_precomputed_results",
            producer_engine="spymaster",
            engine_version="1.0.0",
            params={"ticker": ticker, "max_sma_day": 114, "price_source": "Close"},
            content_obj=payload,
        )
        payload[pm.MANIFEST_FIELD] = manifest
    if mutate_after_attach and with_manifest:
        payload["top_buy_pair"] = (99, 99)
    with open(path, "wb") as f:
        pickle.dump(payload, f)
    if with_manifest:
        pm.write_output_manifest(
            path, payload[pm.MANIFEST_FIELD], include_file_sha256=True,
        )
    return payload


def test_3b2a_spymaster_producer_writes_embedded_and_sidecar(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import spymaster

    # Redirect cache/results to tmp_path so the producer write is sandboxed.
    cache_dir = tmp_path / "cache" / "results"
    cache_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    dates = pd.bdate_range(start="2024-01-02", periods=8)
    results = {
        "_ticker": "AAA",
        "preprocessed_data": pd.DataFrame(
            {"Close": [100.0 + i for i in range(len(dates))]}, index=dates,
        ),
        "daily_top_buy_pairs": {d: ((114, 113), 1.0) for d in dates},
        "daily_top_short_pairs": {d: ((113, 114), 1.0) for d in dates},
        "top_buy_pair": (10, 1),
        "top_short_pair": (1, 10),
        "max_sma_day": 114,
        "engine_version": "1.0.0",
        "price_source": "Close",
    }
    spymaster.save_precomputed_results("AAA", results)

    pkl_path = cache_dir / "AAA_precomputed_results.pkl"
    assert pkl_path.exists()
    sidecar = pm._sidecar_path_for(pkl_path)
    assert sidecar.exists()
    sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_data["artifact_file_sha256"] == pm.file_sha256(pkl_path)
    with open(pkl_path, "rb") as f:
        loaded = pickle.load(f)
    assert pm.MANIFEST_FIELD in loaded
    embedded = loaded[pm.MANIFEST_FIELD]
    assert "artifact_file_sha256" not in embedded, (
        "Embedded Spymaster manifest must not self-reference its file SHA"
    )
    assert embedded["producer_engine"] == "spymaster"
    assert embedded["artifact_type"] == "spymaster_precomputed_results"


def test_3b2a_spymaster_consumer_loads_manifested(tmp_path):
    p = tmp_path / "AAA_precomputed_results.pkl"
    payload = _build_synthetic_spymaster_pkl(p, ticker="AAA")
    data, result = pm.load_verified_pickle_artifact(p)
    assert data is not None
    assert result.ok is True
    assert not result.legacy
    assert data["top_buy_pair"] == (10, 1)


def test_3b2a_spymaster_legacy_loads_with_warning(tmp_path):
    p = tmp_path / "AAA_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(p, ticker="AAA", with_manifest=False)
    data, result = pm.load_verified_pickle_artifact(p)
    assert data is not None
    assert result.ok is True
    assert result.legacy is True


def test_3b2a_spymaster_mismatch_via_internal_consumer(tmp_path, monkeypatch):
    """Tampered Spymaster PKL must surface as cache miss in
    ``_quick_last_fingerprint`` (representative internal consumer)."""
    sys.path.insert(0, str(PROJECT_DIR))
    import spymaster

    cache_dir = tmp_path / "cache" / "results"
    cache_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    p = cache_dir / "AAA_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(p, ticker="AAA", mutate_after_attach=True)
    pm.manifest_hash_cache_clear()
    fp = spymaster._quick_last_fingerprint("AAA")
    assert fp is None  # mismatch -> cache miss


def test_3b2a_spymaster_atomic_replace_preserves_embedded(tmp_path):
    """Two-file write tolerance: even if the sidecar is missing after a
    torn write, the embedded pickle manifest verifies cleanly."""
    p = tmp_path / "AAA_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(p, ticker="AAA")
    sidecar = pm._sidecar_path_for(p)
    sidecar.unlink()
    data, result = pm.load_verified_pickle_artifact(p)
    assert data is not None
    assert result.ok is True
    assert not result.legacy


# ===========================================================================
# Phase 3B-2A: TrafficFlow Spymaster PKL consumer
# ===========================================================================


def test_3b2a_trafficflow_spymaster_consumer_verifies(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import trafficflow

    pkl_dir = tmp_path / "cache" / "results"
    pkl_dir.mkdir(parents=True)
    monkeypatch.setattr(trafficflow, "SPYMASTER_PKL_DIR", str(pkl_dir))
    # Reset the in-memory cache so each sub-case is independent.
    trafficflow._PKL_CACHE.clear()

    # Manifested -> loads, populates cache.
    p_ok = pkl_dir / "AAA_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(p_ok, ticker="AAA")
    pm.manifest_hash_cache_clear()
    data = trafficflow.load_spymaster_pkl("AAA")
    assert data is not None
    assert "AAA" in trafficflow._PKL_CACHE

    # Tampered -> rejected, NOT cached.
    p_bad = pkl_dir / "BBB_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(
        p_bad, ticker="BBB", mutate_after_attach=True,
    )
    pm.manifest_hash_cache_clear()
    assert trafficflow.load_spymaster_pkl("BBB") is None
    assert "BBB" not in trafficflow._PKL_CACHE

    # Legacy (no manifest) -> loads, populates cache.
    p_legacy = pkl_dir / "CCC_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(
        p_legacy, ticker="CCC", with_manifest=False,
    )
    legacy = trafficflow.load_spymaster_pkl("CCC")
    assert legacy is not None
    assert "CCC" in trafficflow._PKL_CACHE


# ===========================================================================
# Phase 3B-2A: Confluence Spymaster fallback consumer
# ===========================================================================


def test_3b2a_confluence_spymaster_fallback_verifies(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    from signal_library import confluence_analyzer

    # _load_spymaster_cache_fallback resolves the path relative to cwd.
    cache_dir = tmp_path / "cache" / "results"
    cache_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    # Manifested -> returns the rebuilt library structure with signals.
    p_ok = cache_dir / "AAA_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(p_ok, ticker="AAA")
    pm.manifest_hash_cache_clear()
    lib = confluence_analyzer._load_spymaster_cache_fallback("AAA")
    assert lib is not None
    assert lib.get("source") == "spymaster_cache"
    assert lib.get("signals")

    # Tampered -> None.
    p_bad = cache_dir / "BBB_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(
        p_bad, ticker="BBB", mutate_after_attach=True,
    )
    pm.manifest_hash_cache_clear()
    assert confluence_analyzer._load_spymaster_cache_fallback("BBB") is None

    # Legacy -> proceeds.
    p_legacy = cache_dir / "CCC_precomputed_results.pkl"
    _build_synthetic_spymaster_pkl(
        p_legacy, ticker="CCC", with_manifest=False,
    )
    legacy = confluence_analyzer._load_spymaster_cache_fallback("CCC")
    assert legacy is not None
    assert legacy.get("source") == "spymaster_cache"


# ===========================================================================
# Phase 3B-2A amendment: collector isolation across concurrent runs
# ===========================================================================


def test_3b2a_collector_isolation_concurrent_runs():
    """Two concurrent StackBuilder-style runs must collect disjoint sets of
    input-manifest hashes; the second run's collector must not overwrite or
    inherit the first run's state.

    Reproduction strategy: two threads barrier-rendezvous on
    ``_start_input_manifest_collection`` so both runs are active at the
    same time, each records a distinct manifested library, then finalizes.
    Without per-run isolation (the e5f3eb7 module-global pattern), the
    second start overwrites the first run's collector and the first run's
    snapshot returns either empty or the wrong (other run's) hashes.

    The test ALSO exercises the ThreadPoolExecutor wrapping invariant: each
    run hands its recording call off to a worker thread via
    ``ThreadPoolExecutor.submit``. Worker threads are long-lived and do not
    inherit a fresh context per task, so the production code must wrap the
    submission with ``contextvars.copy_context().run`` for the per-run
    ContextVar to flow into the worker.
    """
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder
    import threading
    from concurrent.futures import ThreadPoolExecutor

    dates = pd.bdate_range(start="2024-01-02", periods=8)

    def make_lib(tag: str, idx: int) -> dict:
        # Distinct content per (tag, idx). The "extra" key forces a unique
        # canonical-content hash since both tag character and index land in
        # the canonical body, while ticker only enters the manifest.
        lib = make_signal_library_dict(
            dates,
            extra={"_test_unique_marker": f"{tag}-{idx}"},
        )
        pm.attach_manifest(
            lib, sidecar_path=None,
            artifact_type="signal_library_daily", ticker=f"{tag}{idx}",
        )
        return lib

    libs_a = [make_lib("A", i) for i in range(3)]
    libs_b = [make_lib("B", i) for i in range(3)]
    expected_a = sorted({l["_manifest"]["content_hash"] for l in libs_a})
    expected_b = sorted({l["_manifest"]["content_hash"] for l in libs_b})
    assert set(expected_a).isdisjoint(expected_b), (
        "Test fixture is broken: A and B libs share content_hashes"
    )

    barrier = threading.Barrier(2)
    snapshots: dict = {}
    executor = ThreadPoolExecutor(max_workers=4)

    def run(name, libs):
        token = stackbuilder._start_input_manifest_collection()
        try:
            barrier.wait(timeout=5.0)
            # Hand each record off to the executor via the production
            # _submit_with_context wrapper that phase2_rank_all uses, so
            # the test exercises the same context-propagation invariant
            # the real submit path relies on.
            futures = [
                stackbuilder._submit_with_context(
                    executor, stackbuilder._record_input_lib, lib,
                )
                for lib in libs
            ]
            for f in futures:
                f.result(timeout=5.0)
            snapshots[name] = stackbuilder._finalize_input_manifest_collection(
                token
            )
            token = None
        finally:
            if token is not None:
                try:
                    stackbuilder._finalize_input_manifest_collection(token)
                except Exception:
                    pass

    t_a = threading.Thread(target=run, args=("A", libs_a))
    t_b = threading.Thread(target=run, args=("B", libs_b))
    t_a.start()
    t_b.start()
    t_a.join(timeout=15.0)
    t_b.join(timeout=15.0)
    executor.shutdown(wait=True)

    assert "A" in snapshots and "B" in snapshots
    snap_a = snapshots["A"]
    snap_b = snapshots["B"]
    assert sorted(snap_a["input_manifest_hashes"]) == expected_a, (
        f"Run A collected {snap_a['input_manifest_hashes']}, expected {expected_a}"
    )
    assert sorted(snap_b["input_manifest_hashes"]) == expected_b, (
        f"Run B collected {snap_b['input_manifest_hashes']}, expected {expected_b}"
    )
    assert set(snap_a["input_manifest_hashes"]).isdisjoint(
        snap_b["input_manifest_hashes"]
    ), "Cross-contamination between concurrent run collectors"
    # Legacy / missing counters isolated too.
    assert snap_a["input_legacy_count"] == 0
    assert snap_b["input_legacy_count"] == 0
    assert snap_a["input_missing_manifest_count"] == 0
    assert snap_b["input_missing_manifest_count"] == 0


# ===========================================================================
# Phase 3B-2A amendment: Spymaster save-failure guard
# ===========================================================================


def test_3b2a_spymaster_save_failure_no_orphan_sidecar(tmp_path, monkeypatch):
    """When pickle replacement fails, no new sidecar manifest must be
    written.

    Setup:
      - Pre-populate ``cache/results/AAA_precomputed_results.pkl`` with an
        OLD pickle and a matching OLD sidecar manifest.
      - Force both ``os.replace`` and ``shutil.copy2`` inside spymaster
        to fail for the final pickle replacement.
      - Sentinel ``_write_output_manifest`` so the test can assert it
        was never called.

    Assertions:
      - ``save_precomputed_results`` does not raise.
      - The on-disk pickle still contains OLD content.
      - The on-disk sidecar still contains the OLD manifest bytes.
      - The sentinel write_output_manifest was never invoked.
    """
    sys.path.insert(0, str(PROJECT_DIR))
    import spymaster

    cache_dir = tmp_path / "cache" / "results"
    cache_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    # OLD content + OLD manifest pre-populated on disk.
    pkl_path = cache_dir / "AAA_precomputed_results.pkl"
    old_payload = _build_synthetic_spymaster_pkl(pkl_path, ticker="AAA")
    sidecar_path = pm._sidecar_path_for(pkl_path)
    old_sidecar_bytes = sidecar_path.read_bytes()
    old_pkl_bytes = pkl_path.read_bytes()

    # Force the final replace path to fail. The retry loop in
    # save_precomputed_results sleeps between attempts; we monkeypatch
    # both os.replace and shutil.copy2 to raise PermissionError.
    def _fail_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("AAA_precomputed_results.pkl"):
            raise PermissionError("simulated replace failure")
        # Allow temp-file cleanup paths and other unrelated calls.
        return _real_replace(src, dst, *args, **kwargs)

    def _fail_copy2(src, dst, *args, **kwargs):
        if str(dst).endswith("AAA_precomputed_results.pkl"):
            raise OSError("simulated copy2 failure")
        return _real_copy2(src, dst, *args, **kwargs)

    _real_replace = os.replace
    _real_copy2 = __import__("shutil").copy2
    monkeypatch.setattr(spymaster.os, "replace", _fail_replace)
    monkeypatch.setattr(spymaster.shutil, "copy2", _fail_copy2)

    sidecar_call_count = {"n": 0}

    def _sentinel_write_output_manifest(*args, **kwargs):
        sidecar_call_count["n"] += 1
        raise AssertionError(
            "save_ok gate violated: sidecar write attempted after a "
            "failed pickle replacement"
        )

    monkeypatch.setattr(
        spymaster, "_write_output_manifest", _sentinel_write_output_manifest,
    )

    # NEW content that the failed save would (without the gate) try to
    # advertise via a fresh sidecar.
    dates = pd.bdate_range(start="2024-01-02", periods=8)
    new_results = {
        "_ticker": "AAA",
        "preprocessed_data": pd.DataFrame(
            {"Close": [200.0 + i for i in range(len(dates))]}, index=dates,
        ),
        "daily_top_buy_pairs": {d: ((114, 113), 5.0) for d in dates},
        "daily_top_short_pairs": {d: ((113, 114), 5.0) for d in dates},
        "top_buy_pair": (5, 4),
        "top_short_pair": (4, 5),
        "max_sma_day": 114,
        "engine_version": "1.0.0",
        "price_source": "Close",
    }

    # Should not raise.
    spymaster.save_precomputed_results("AAA", new_results)

    # Sidecar write must not have been attempted.
    assert sidecar_call_count["n"] == 0, (
        "save_ok gate violated: sidecar write attempted after a failed "
        "pickle replacement"
    )
    # On-disk pickle and sidecar bytes must be unchanged.
    assert pkl_path.read_bytes() == old_pkl_bytes
    assert sidecar_path.read_bytes() == old_sidecar_bytes


def test_3b2a_spymaster_save_failure_fallback_copy2_succeeds(tmp_path, monkeypatch):
    """If os.replace fails but shutil.copy2 succeeds, save_ok=True and the
    sidecar write proceeds (proving the gate is not over-tightened)."""
    sys.path.insert(0, str(PROJECT_DIR))
    import spymaster

    cache_dir = tmp_path / "cache" / "results"
    cache_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    _real_replace = os.replace

    def _fail_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("AAA_precomputed_results.pkl"):
            raise PermissionError("simulated replace failure")
        return _real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(spymaster.os, "replace", _fail_replace)

    dates = pd.bdate_range(start="2024-01-02", periods=8)
    new_results = {
        "_ticker": "AAA",
        "preprocessed_data": pd.DataFrame(
            {"Close": [300.0 + i for i in range(len(dates))]}, index=dates,
        ),
        "daily_top_buy_pairs": {d: ((114, 113), 7.0) for d in dates},
        "daily_top_short_pairs": {d: ((113, 114), 7.0) for d in dates},
        "top_buy_pair": (3, 2),
        "top_short_pair": (2, 3),
        "max_sma_day": 114,
        "engine_version": "1.0.0",
        "price_source": "Close",
    }

    spymaster.save_precomputed_results("AAA", new_results)

    pkl_path = cache_dir / "AAA_precomputed_results.pkl"
    assert pkl_path.exists()
    sidecar_path = pm._sidecar_path_for(pkl_path)
    assert sidecar_path.exists(), (
        "Sidecar should be written when fallback copy2 succeeds"
    )
    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar_data["artifact_file_sha256"] == pm.file_sha256(pkl_path)


# ===========================================================================
# Phase 3B-2B: XLSX manifest helper + load_verified_xlsx_artifact
# ===========================================================================


def _write_xlsx_with_manifest(
    path,
    *,
    df,
    artifact_type,
    producer_engine,
    output_columns,
    key_columns,
    current_run_df=None,
    preexisting_status="none",
    preexisting_row_count=0,
    params=None,
):
    """Test helper: write a workbook + sidecar manifest pair."""
    df.to_excel(path, index=False, engine="openpyxl")
    if current_run_df is None:
        current_run_df = df.copy()
    manifest = pm.build_xlsx_output_manifest(
        artifact_type=artifact_type,
        producer_engine=producer_engine,
        engine_version="1.0.0",
        output_columns=output_columns,
        key_columns=key_columns,
        current_run_df=current_run_df,
        final_df=df,
        artifact_path=path,
        preexisting_status=preexisting_status,
        preexisting_row_count=preexisting_row_count,
        params=params or {},
    )
    sidecar = path.with_name(path.name + pm.SIDECAR_SUFFIX)
    sidecar.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def test_3b2b_canonical_workbook_hash_is_deterministic():
    df = pd.DataFrame([
        {"Primary Ticker": "SPY", "Sharpe": 1.2, "Notes": None},
        {"Primary Ticker": "QQQ", "Sharpe": 0.8, "Notes": "ok"},
    ])
    h1 = pm._canonical_workbook_hash(df)
    h2 = pm._canonical_workbook_hash(df.copy())
    assert h1 == h2


def test_3b2b_canonical_workbook_hash_distinguishes_nan_none_empty():
    base = pd.DataFrame([{"A": 1.0, "B": "x"}])
    df_empty = pd.DataFrame([{"A": 1.0, "B": ""}])
    df_none = pd.DataFrame([{"A": 1.0, "B": None}])
    h_base = pm._canonical_workbook_hash(base)
    h_empty = pm._canonical_workbook_hash(df_empty)
    h_none = pm._canonical_workbook_hash(df_none)
    assert h_base != h_empty
    assert h_base != h_none
    assert h_empty != h_none


def test_3b2b_xlsx_key_strings_priority_and_normalization():
    df = pd.DataFrame([
        {"Primary Ticker": "  spy ", "Resolved/Fetched": "spy"},
        {"Primary Ticker": "", "Resolved/Fetched": "qqq"},
        {"Primary Ticker": None, "Resolved/Fetched": None},
        {"Primary Ticker": "AAPL", "Resolved/Fetched": "aapl"},
    ])
    keys = pm._xlsx_key_strings(df, ["Primary Ticker", "Resolved/Fetched"])
    assert keys == ["SPY", "QQQ", "", "AAPL"]


def test_3b2b_compute_legacy_row_count():
    final = pd.DataFrame([
        {"Primary Ticker": "SPY"},
        {"Primary Ticker": "QQQ"},
        {"Primary Ticker": "OLD"},
    ])
    legacy = pm._compute_legacy_row_count(
        final, ["SPY", "QQQ"], ["Primary Ticker"],
    )
    assert legacy == 1
    full = pm._compute_legacy_row_count(
        final, ["SPY", "QQQ", "OLD"], ["Primary Ticker"],
    )
    assert full == 0


def test_3b2b_load_verified_xlsx_fresh_happy_path(tmp_path):
    pkl = tmp_path / "test.xlsx"
    df = pd.DataFrame([
        {"Primary Ticker": "SPY", "Sharpe Ratio": 1.2},
        {"Primary Ticker": "QQQ", "Sharpe Ratio": 0.8},
    ])
    _write_xlsx_with_manifest(
        pkl, df=df, artifact_type="onepass_xlsx",
        producer_engine="onepass",
        output_columns=["Primary Ticker", "Sharpe Ratio"],
        key_columns=["Primary Ticker"],
    )
    loaded, result = pm.load_verified_xlsx_artifact(pkl)
    assert loaded is not None
    assert result.ok is True
    assert not result.legacy


def test_3b2b_load_verified_xlsx_missing_sidecar_legacy_vs_strict(tmp_path):
    pkl = tmp_path / "test.xlsx"
    df = pd.DataFrame([{"Primary Ticker": "SPY"}])
    df.to_excel(pkl, index=False, engine="openpyxl")
    loaded, result = pm.load_verified_xlsx_artifact(pkl)
    assert loaded is not None
    assert result.ok is True
    assert result.legacy is True
    loaded2, result2 = pm.load_verified_xlsx_artifact(pkl, strict=True)
    assert loaded2 is not None
    assert result2.ok is False
    assert result2.legacy is True


def test_3b2b_load_verified_xlsx_workbook_content_mismatch(tmp_path):
    pkl = tmp_path / "test.xlsx"
    df = pd.DataFrame([{"Primary Ticker": "SPY", "Sharpe Ratio": 1.2}])
    _write_xlsx_with_manifest(
        pkl, df=df, artifact_type="onepass_xlsx",
        producer_engine="onepass",
        output_columns=["Primary Ticker", "Sharpe Ratio"],
        key_columns=["Primary Ticker"],
    )
    df_bad = pd.DataFrame([{"Primary Ticker": "SPY", "Sharpe Ratio": 99.9}])
    df_bad.to_excel(pkl, index=False, engine="openpyxl")
    loaded, result = pm.load_verified_xlsx_artifact(pkl)
    fields = [m[0] for m in result.mismatches]
    assert result.ok is False
    assert (
        "full_workbook_content_hash" in fields
        or "artifact_file_sha256" in fields
    )


def test_3b2b_load_verified_xlsx_legacy_row_count_warn_vs_strict(tmp_path):
    pkl = tmp_path / "test.xlsx"
    df = pd.DataFrame([
        {"Primary Ticker": "SPY", "Sharpe Ratio": 1.2},
        {"Primary Ticker": "OLD", "Sharpe Ratio": 0.5},
    ])
    current = pd.DataFrame([
        {"Primary Ticker": "SPY", "Sharpe Ratio": 1.2},
    ])
    _write_xlsx_with_manifest(
        pkl, df=df, artifact_type="onepass_xlsx",
        producer_engine="onepass",
        output_columns=["Primary Ticker", "Sharpe Ratio"],
        key_columns=["Primary Ticker"],
        current_run_df=current,
    )
    _, result = pm.load_verified_xlsx_artifact(pkl)
    assert result.ok is True
    assert any("legacy_row_count" in str(w) for w in result.warnings)
    _, result2 = pm.load_verified_xlsx_artifact(pkl, strict=True)
    assert result2.ok is False
    fields = [m[0] for m in result2.mismatches]
    assert "legacy_row_count" in fields


def test_3b2b_inspect_preexisting_xlsx_manifest(tmp_path):
    pkl = tmp_path / "test.xlsx"
    assert pm.inspect_preexisting_xlsx_manifest(pkl) == "none"
    df = pd.DataFrame([{"Primary Ticker": "SPY"}])
    df.to_excel(pkl, index=False, engine="openpyxl")
    assert pm.inspect_preexisting_xlsx_manifest(pkl) == "none"
    sidecar = pkl.with_name(pkl.name + pm.SIDECAR_SUFFIX)
    sidecar.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    assert pm.inspect_preexisting_xlsx_manifest(pkl) == "legacy"
    sidecar.unlink()
    _write_xlsx_with_manifest(
        pkl, df=df, artifact_type="onepass_xlsx",
        producer_engine="onepass",
        output_columns=["Primary Ticker"], key_columns=["Primary Ticker"],
    )
    assert pm.inspect_preexisting_xlsx_manifest(pkl) == "valid"
    df_bad = pd.DataFrame([{"Primary Ticker": "QQQ"}])
    df_bad.to_excel(pkl, index=False, engine="openpyxl")
    assert pm.inspect_preexisting_xlsx_manifest(pkl) == "mismatched"


# ===========================================================================
# Phase 3B-2B: OnePass XLSX manifest (producer)
# ===========================================================================


def _onepass_metrics(ticker, **overrides):
    """Minimal OnePass-shaped metrics dict for export tests."""
    base = {
        "Primary Ticker": ticker,
        "Trigger Days": 100,
        "Wins": 60,
        "Losses": 40,
        "Win Ratio (%)": 60.0,
        "Std Dev (%)": 1.5,
        "Sharpe Ratio": 1.2,
        "t-Statistic": 2.5,
        "p-Value": 0.01,
        "Significant 90%": "Yes",
        "Significant 95%": "Yes",
        "Significant 99%": "No",
        "Avg Daily Capture (%)": 0.05,
        "Total Capture (%)": 5.0,
    }
    base.update(overrides)
    return base


def test_3b2b_onepass_xlsx_fresh_writes_sidecar(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import onepass

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "fresh.xlsx"
    onepass.export_results_to_excel(
        str(output), [_onepass_metrics("SPY"), _onepass_metrics("QQQ")]
    )
    sidecar = pm._sidecar_path_for(output)
    assert sidecar.exists()
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == "onepass_xlsx"
    assert manifest["producer_engine"] == "onepass"
    assert manifest["preexisting_manifest_status"] == "none"
    assert manifest["preexisting_row_count"] == 0
    assert manifest["legacy_row_count"] == 0
    assert manifest["current_run_row_count"] == 2
    assert sorted(manifest["current_run_keys"]["preview"]) == ["QQQ", "SPY"]
    # End-to-end load_verified_xlsx_artifact verifies clean.
    df, vresult = pm.load_verified_xlsx_artifact(output)
    assert df is not None
    assert vresult.ok is True
    assert not vresult.legacy


def test_3b2b_onepass_xlsx_existing_with_retained_row_legacy_one(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import onepass

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "retained.xlsx"
    # Seed with two tickers (run 1).
    onepass.export_results_to_excel(
        str(output),
        [_onepass_metrics("SPY"), _onepass_metrics("OLD")],
    )
    # Run 2: only update SPY -> OLD should be retained as a legacy row.
    onepass.export_results_to_excel(
        str(output),
        [_onepass_metrics("SPY", **{"Sharpe Ratio": 2.5})],
    )
    sidecar = pm._sidecar_path_for(output)
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["legacy_row_count"] == 1, (
        f"Expected 1 legacy row, got {manifest['legacy_row_count']}"
    )
    assert manifest["current_run_row_count"] == 1
    assert manifest["current_run_keys"]["preview"] == ["SPY"]
    # The preexisting run-1 manifest should classify as valid since the
    # workbook + sidecar pair were written by the same producer.
    assert manifest["preexisting_manifest_status"] == "valid"
    # Workbook should contain both rows.
    final_df = pd.read_excel(output, engine="openpyxl")
    assert sorted(final_df["Primary Ticker"].tolist()) == ["OLD", "SPY"]


def test_3b2b_onepass_xlsx_full_refresh_legacy_zero(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import onepass

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "refresh.xlsx"
    onepass.export_results_to_excel(
        str(output),
        [_onepass_metrics("SPY"), _onepass_metrics("QQQ")],
    )
    # Run 2 touches both keys -> 0 legacy rows.
    onepass.export_results_to_excel(
        str(output),
        [
            _onepass_metrics("SPY", **{"Sharpe Ratio": 2.0}),
            _onepass_metrics("QQQ", **{"Sharpe Ratio": 1.5}),
        ],
    )
    sidecar = pm._sidecar_path_for(output)
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["legacy_row_count"] == 0


def test_3b2b_onepass_xlsx_mismatched_preexisting_sidecar(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import onepass

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "mismatched.xlsx"
    onepass.export_results_to_excel(
        str(output), [_onepass_metrics("SPY")]
    )
    # Tamper with the workbook bytes BEFORE the next run runs.
    df_bad = pd.read_excel(output, engine="openpyxl")
    df_bad.loc[0, "Sharpe Ratio"] = 99.99
    df_bad.to_excel(output, index=False, engine="openpyxl")
    # Run 2 should detect the preexisting workbook+sidecar mismatch.
    onepass.export_results_to_excel(
        str(output), [_onepass_metrics("QQQ")]
    )
    sidecar = pm._sidecar_path_for(output)
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["preexisting_manifest_status"] == "mismatched"


# ===========================================================================
# Phase 3B-2B: StackBuilder --strict-manifests + fast-path verification
# ===========================================================================


def _seed_impactsearch_xlsx(tmp_path, secondary, *, with_manifest=True):
    """Write a synthetic ImpactSearch XLSX (and optional manifest) under
    a folder StackBuilder's try_load_rank_from_impact_xlsx will scan.
    """
    workbook = tmp_path / f"{secondary}_analysis.xlsx"
    df = pd.DataFrame([
        {
            "Primary Ticker": "AAA",
            "Avg Daily Capture (%)": 0.10,
            "Total Capture (%)": 1.0,
            "Sharpe Ratio": 1.5,
            "Win Ratio (%)": 60.0,
            "Std Dev (%)": 0.5,
            "Trigger Days": 8,
            "p-Value": 0.04,
        },
    ])
    df.to_excel(workbook, index=False, engine="openpyxl")
    if with_manifest:
        manifest = pm.build_xlsx_output_manifest(
            artifact_type="impactsearch_xlsx",
            producer_engine="impactsearch",
            engine_version="1.0.0",
            output_columns=list(df.columns),
            key_columns=["Primary Ticker", "Resolved/Fetched"],
            current_run_df=df.copy(),
            final_df=df,
            artifact_path=workbook,
            preexisting_status="none",
            preexisting_row_count=0,
        )
        sidecar = workbook.with_name(workbook.name + pm.SIDECAR_SUFFIX)
        sidecar.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return workbook


def test_3b2b_stackbuilder_fastpath_strict_legacy_rejected(tmp_path):
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder

    _seed_impactsearch_xlsx(tmp_path, "ZZZ", with_manifest=False)
    # Non-strict: legacy is accepted.
    df_ns = stackbuilder.try_load_rank_from_impact_xlsx(
        sec="ZZZ", dirpath=str(tmp_path), max_age_days=45,
        strict_manifests=False,
    )
    assert isinstance(df_ns, pd.DataFrame)
    # Strict: legacy is rejected.
    df_strict = stackbuilder.try_load_rank_from_impact_xlsx(
        sec="ZZZ", dirpath=str(tmp_path), max_age_days=45,
        strict_manifests=True,
    )
    assert df_strict is None


def test_3b2b_stackbuilder_fastpath_mismatched_always_rejected(tmp_path):
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder

    workbook = _seed_impactsearch_xlsx(tmp_path, "ZZZ", with_manifest=True)
    # Tamper the workbook bytes after the manifest was written.
    df_bad = pd.read_excel(workbook, engine="openpyxl")
    df_bad.loc[0, "Sharpe Ratio"] = 99.99
    df_bad.to_excel(workbook, index=False, engine="openpyxl")
    # Mismatch is rejected even under non-strict.
    df_ns = stackbuilder.try_load_rank_from_impact_xlsx(
        sec="ZZZ", dirpath=str(tmp_path), max_age_days=45,
        strict_manifests=False,
    )
    assert df_ns is None
    df_strict = stackbuilder.try_load_rank_from_impact_xlsx(
        sec="ZZZ", dirpath=str(tmp_path), max_age_days=45,
        strict_manifests=True,
    )
    assert df_strict is None


def test_3b2b_stackbuilder_strict_no_primaries_systemexit(monkeypatch):
    """When fast-path is rejected under --strict-manifests AND no
    primaries are provided, phase2_rank_all raises SystemExit."""
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder
    from types import SimpleNamespace

    monkeypatch.setattr(
        stackbuilder, "try_load_rank_from_impact_xlsx",
        lambda *a, **k: None,
    )
    args = SimpleNamespace(
        secondary="ZZZ",
        prefer_impact_xlsx=True,
        impact_xlsx_dir="<unused>",
        impact_xlsx_max_age_days=45,
        strict_manifests=True,
        no_progress=True,
        bottom_n=1,
        signal_lib_dir="<unused>",
    )
    primaries_df = pd.DataFrame(columns=["Primary Ticker"])
    sec_rets = pd.Series(dtype=float)
    with pytest.raises(SystemExit) as exc_info:
        stackbuilder.phase2_rank_all(
            args, primaries_df, sec_rets, outdir=str(PROJECT_DIR / "logs"),
            secondary="ZZZ", progress_path=None,
        )
    assert "--strict-manifests" in str(exc_info.value)


def test_3b2b_stackbuilder_strict_primaries_provided_falls_through(
    tmp_path, monkeypatch
):
    """When fast-path is rejected under --strict-manifests but primaries
    ARE provided, phase2_rank_all does NOT SystemExit; it falls through
    to the slow path. We monkeypatch _score_primary_both_modes to terminate
    the slow path quickly with all-None results.
    """
    sys.path.insert(0, str(PROJECT_DIR))
    import stackbuilder
    from types import SimpleNamespace

    monkeypatch.setattr(
        stackbuilder, "try_load_rank_from_impact_xlsx",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        stackbuilder, "_score_primary_both_modes",
        lambda *a, **k: (None, None),
    )
    args = SimpleNamespace(
        secondary="ZZZ",
        prefer_impact_xlsx=True,
        impact_xlsx_dir="<unused>",
        impact_xlsx_max_age_days=45,
        strict_manifests=True,
        no_progress=True,
        bottom_n=0,
        signal_lib_dir="<unused>",
        threads="auto",
    )
    primaries_df = pd.DataFrame({"Primary Ticker": ["AAA"]})
    sec_rets = pd.Series([0.01, -0.01, 0.02], name="returns")
    out = tmp_path / "out"
    out.mkdir()
    # Should NOT raise SystemExit; slow-path returns empty results.
    try:
        stackbuilder.phase2_rank_all(
            args, primaries_df, sec_rets, outdir=str(out),
            secondary="ZZZ", progress_path=None,
        )
    except SystemExit as exc:
        # The only SystemExit allowed here is the empty-results one
        # downstream; the strict no-primaries SystemExit must NOT fire
        # because primaries WERE provided.
        assert "--strict-manifests" not in str(exc)


# ===========================================================================
# Phase 3B-2B: ImpactSearch XLSX manifest (producer)
# ===========================================================================


def _impactsearch_metrics(ticker, **overrides):
    """Minimal ImpactSearch-shaped metrics dict for export tests."""
    base = {
        "Primary Ticker": ticker,
        "Resolved/Fetched": ticker.lower(),
        "Library Source": "stable",
        "Trigger Days": 80,
        "Wins": 50,
        "Losses": 30,
        "Win Ratio (%)": 62.5,
        "Std Dev (%)": 1.4,
        "Sharpe Ratio": 1.1,
        "t-Statistic": 2.2,
        "p-Value": 0.02,
        "Significant 90%": "Yes",
        "Significant 95%": "Yes",
        "Significant 99%": "No",
        "Avg Daily Capture (%)": 0.04,
        "Total Capture (%)": 4.0,
    }
    base.update(overrides)
    return base


def test_3b2b_impactsearch_xlsx_fresh_writes_sidecar(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import impactsearch

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "fresh.xlsx"
    impactsearch.export_results_to_excel(
        str(output),
        [_impactsearch_metrics("SPY"), _impactsearch_metrics("QQQ")],
    )
    sidecar = pm._sidecar_path_for(output)
    assert sidecar.exists()
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == "impactsearch_xlsx"
    assert manifest["producer_engine"] == "impactsearch"
    assert manifest["preexisting_manifest_status"] == "none"
    assert manifest["legacy_row_count"] == 0
    assert manifest["current_run_row_count"] == 2
    assert sorted(manifest["current_run_keys"]["preview"]) == ["QQQ", "SPY"]
    assert manifest["current_run_keys"]["key_columns"] == [
        "Primary Ticker", "Resolved/Fetched",
    ]
    df, vresult = pm.load_verified_xlsx_artifact(output)
    assert df is not None
    assert vresult.ok is True


def test_3b2b_impactsearch_xlsx_existing_with_retained_row_legacy_one(
    tmp_path, monkeypatch
):
    sys.path.insert(0, str(PROJECT_DIR))
    import impactsearch

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "retained.xlsx"
    impactsearch.export_results_to_excel(
        str(output),
        [_impactsearch_metrics("SPY"), _impactsearch_metrics("OLD")],
    )
    impactsearch.export_results_to_excel(
        str(output),
        [_impactsearch_metrics("SPY", **{"Sharpe Ratio": 2.5})],
    )
    sidecar = pm._sidecar_path_for(output)
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["legacy_row_count"] == 1
    assert manifest["current_run_row_count"] == 1
    assert manifest["current_run_keys"]["preview"] == ["SPY"]
    assert manifest["preexisting_manifest_status"] == "valid"
    final_df = pd.read_excel(output, engine="openpyxl")
    assert sorted(final_df["Primary Ticker"].tolist()) == ["OLD", "SPY"]


def test_3b2b_impactsearch_xlsx_full_refresh_legacy_zero(tmp_path, monkeypatch):
    sys.path.insert(0, str(PROJECT_DIR))
    import impactsearch

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "refresh.xlsx"
    impactsearch.export_results_to_excel(
        str(output),
        [_impactsearch_metrics("SPY"), _impactsearch_metrics("QQQ")],
    )
    impactsearch.export_results_to_excel(
        str(output),
        [
            _impactsearch_metrics("SPY", **{"Sharpe Ratio": 2.0}),
            _impactsearch_metrics("QQQ", **{"Sharpe Ratio": 1.5}),
        ],
    )
    sidecar = pm._sidecar_path_for(output)
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["legacy_row_count"] == 0


def test_3b2b_impactsearch_xlsx_mismatched_preexisting_sidecar(
    tmp_path, monkeypatch
):
    sys.path.insert(0, str(PROJECT_DIR))
    import impactsearch

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "mismatched.xlsx"
    impactsearch.export_results_to_excel(
        str(output), [_impactsearch_metrics("SPY")]
    )
    df_bad = pd.read_excel(output, engine="openpyxl")
    df_bad.loc[0, "Sharpe Ratio"] = 99.99
    df_bad.to_excel(output, index=False, engine="openpyxl")
    impactsearch.export_results_to_excel(
        str(output), [_impactsearch_metrics("QQQ")]
    )
    sidecar = pm._sidecar_path_for(output)
    manifest = json.loads(sidecar.read_text(encoding="utf-8"))
    assert manifest["preexisting_manifest_status"] == "mismatched"