"""
Regression tests pinning the OnePass provenance writer-vs-loader
contract for ``engine_version``.

Background
----------

``save_signal_library`` writes ``engine_version`` as top-level
manifest metadata via ``_attach_manifest(..., engine_version=
ENGINE_VERSION)``. It does NOT include ``engine_version`` inside
``manifest_params``. The central verifier
(``provenance_manifest.verify_manifest`` -> ``_params_subset_diff``)
compares ``requested_params`` against ``manifest["params"]``
specifically, so any key listed in ``requested_params`` that is
absent from ``manifest["params"]`` produces a
``params.<key>: <missing>`` mismatch and forces a full rebuild.

Before this fix, ``load_signal_library`` listed
``'engine_version': ENGINE_VERSION`` inside ``requested_params``.
Every fresh OnePass stable library written by ``save_signal_library``
therefore failed verification on the very next load with
``[('params.engine_version', '<missing>', '1.0.0')]``, forcing a
full rebuild on every run. The first full-universe run after
Phase 3A shipped wrote the defective manifests; every subsequent
run rebuilt them all.

The fix mirrors the Phase 6I-57 ImpactSearch precedent at
``impactsearch.py:1664-1678``: ``engine_version`` is removed from
``requested_params`` because top-level ``engine_version`` is
enforced separately at the post-verify check
(``onepass.py:1485-1517``) against
``signal_data["engine_version"]``.

Why the existing OnePass / provenance test suites missed this
-------------------------------------------------------------

The pre-existing helper ``_write_valid_lib`` in
``test_onepass_rejection_diagnostics.py`` and the
``test_provenance_manifest.py`` fixtures attach manifests with
``params={"engine_version": "1.0.0", "MAX_SMA_DAY": 114, ...}``
(i.e., they duplicate ``engine_version`` into both the top-level
manifest metadata AND ``manifest["params"]``). That fixture shape
masks the production writer's actual omission. These regression
tests deliberately exercise the real ``save_signal_library`` -> real
``load_signal_library`` round-trip (no synthetic fixture writer)
so the contract is pinned end-to-end.

All tests are ``tmp_path``-only. No network. No writes to real
``output/``, ``signal_library/data/stable/``, ``cache/results``,
``price_cache``, or ``output/stackbuilder``.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[2]
TEST_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(TEST_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_SCRIPTS_DIR))

import onepass  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic OnePass save inputs
# ---------------------------------------------------------------------------


def _make_df(n: int = 30) -> pd.DataFrame:
    """Build a deterministic synthetic OHLC-style DataFrame with a
    ``Close`` column and a daily ``DatetimeIndex``. The OnePass writer
    only needs ``Close`` plus the index for fingerprinting; other
    columns are tolerated but not required."""
    rng = np.random.default_rng(seed=1)
    dates = pd.bdate_range(start="2024-01-02", periods=n)
    pct = rng.normal(loc=0.001, scale=0.005, size=n)
    closes = 100.0 * np.cumprod(1.0 + pct)
    return pd.DataFrame({"Close": closes}, index=dates)


def _make_save_inputs(n: int = 30):
    """Construct a minimal but production-shaped argument tuple for
    ``onepass.save_signal_library``. The writer truncates the last
    ``PERSIST_SKIP_BARS`` rows; we feed enough bars to leave a
    non-trivial persisted library."""
    df = _make_df(n=n)
    signals = []
    cycle = ("Buy", "Short", "None")
    for i in range(n):
        signals.append(cycle[i % 3])
    # Per-date pair dicts. The structure mirrors what the real
    # OnePass engine emits; the exact pair payload is not load-
    # bearing for the manifest contract being tested here.
    buy_pairs = {ts: [(2, 5)] for ts in df.index}
    short_pairs = {ts: [(3, 7)] for ts in df.index}
    return df, signals, buy_pairs, short_pairs


# ---------------------------------------------------------------------------
# Test 1: real save -> real load round-trip succeeds
# ---------------------------------------------------------------------------


def test_save_load_round_trip_does_not_reject_for_missing_params_engine_version(
    tmp_path, monkeypatch,
):
    """OnePass ``save_signal_library`` -> ``load_signal_library``
    round-trip must not be rejected by the verifier for a missing
    ``params.engine_version`` field.

    This is the production-writer regression. It is the test the
    pre-existing suite did not have: it exercises the real writer's
    ``manifest_params`` dict (which omits ``engine_version``) against
    the real loader's ``requested_params`` block.
    """
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))

    ticker = "TESTA"
    df, signals, buy_pairs, short_pairs = _make_save_inputs(n=30)

    save_ok = onepass.save_signal_library(
        ticker, buy_pairs, short_pairs, signals, df,
    )
    assert save_ok is True, "save_signal_library must return True on success"

    # Verify the persisted manifest shape: top-level engine_version
    # is present, manifest.params has no engine_version key.
    saved_path = Path(onepass._lib_path_for(ticker))
    assert saved_path.is_file(), f"expected saved PKL at {saved_path!s}"
    with open(saved_path, "rb") as fh:
        saved_library = pickle.load(fh)
    assert isinstance(saved_library, dict)
    embedded_manifest = saved_library.get("_manifest")
    assert isinstance(embedded_manifest, dict), (
        "save_signal_library must embed a provenance manifest"
    )
    assert embedded_manifest.get("engine_version") == onepass.ENGINE_VERSION, (
        "top-level manifest.engine_version must match ENGINE_VERSION"
    )
    manifest_params = embedded_manifest.get("params") or {}
    assert "engine_version" not in manifest_params, (
        "manifest.params must NOT carry engine_version; "
        "engine_version lives at the manifest top level. "
        "If a future writer change adds it inside params, the "
        "Phase 6I-57-style fix in load_signal_library can be "
        "revisited."
    )
    # Sanity: MAX_SMA_DAY and price_source DO live inside params and
    # remain enforced via requested_params after the fix.
    assert manifest_params.get("MAX_SMA_DAY") == onepass.MAX_SMA_DAY
    assert manifest_params.get("price_source") == "Close"

    # Real-loader path: must succeed without a manifest_failed
    # rejection.
    rejection: dict = {}
    loaded = onepass.load_signal_library(ticker, rejection_out=rejection)
    assert loaded is not None, (
        f"load_signal_library returned None; rejection={rejection!r}"
    )
    assert isinstance(loaded, dict)
    assert loaded.get("ticker") == ticker
    assert loaded.get("engine_version") == onepass.ENGINE_VERSION
    # ``rejection_out`` should be cleared on success per the
    # Phase 5B Item 7 contract (load_signal_library clears stale
    # rejections when a candidate ultimately succeeds).
    assert rejection == {}, (
        f"expected empty rejection on successful load, got {rejection!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: incompatible top-level engine_version is still rejected
# ---------------------------------------------------------------------------


def test_incompatible_top_level_engine_version_is_rejected(
    tmp_path, monkeypatch,
):
    """Removing ``engine_version`` from ``requested_params`` must NOT
    silently disable version enforcement. The post-verify top-level
    check at ``onepass.py:1485`` must still reject a library whose
    own ``signal_data['engine_version']`` is incompatible with the
    current ``ENGINE_VERSION``.

    Strategy: save a real library under the current ENGINE_VERSION,
    then monkeypatch ``onepass.ENGINE_VERSION`` to a different value
    before loading. The saved library's top-level engine_version
    will no longer equal the in-memory ENGINE_VERSION, and the
    post-verify check must populate a LOAD_VERSION_MISMATCH
    rejection and return None.
    """
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))

    ticker = "TESTB"
    df, signals, buy_pairs, short_pairs = _make_save_inputs(n=30)
    save_ok = onepass.save_signal_library(
        ticker, buy_pairs, short_pairs, signals, df,
    )
    assert save_ok is True

    # Bump the in-memory engine version to simulate the next release
    # cycle. ``_lib_path_for`` derives the filename from
    # ENGINE_VERSION, so we must also stage the file under the new
    # filename so the loader can find it (otherwise the test would
    # exit via the ``missing_library`` path instead).
    old_path = Path(onepass._lib_path_for(ticker))
    monkeypatch.setattr(onepass, "ENGINE_VERSION", "0.9.0")
    new_path = Path(onepass._lib_path_for(ticker))
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_bytes(old_path.read_bytes())

    rejection: dict = {}
    loaded = onepass.load_signal_library(ticker, rejection_out=rejection)
    assert loaded is None, (
        "load_signal_library must return None when the saved "
        "top-level engine_version does not match ENGINE_VERSION"
    )
    assert rejection.get("reason") == onepass.LOAD_VERSION_MISMATCH, (
        f"expected LOAD_VERSION_MISMATCH rejection, got "
        f"{rejection.get('reason')!r}; full record: {rejection!r}"
    )
    assert rejection.get("stage") == "load"
    details = rejection.get("details") or {}
    # The saved library carries the original engine_version; the
    # current ENGINE_VERSION is the patched value.
    assert details.get("library_engine_version") == "1.0.0"
    assert details.get("expected_engine_version") == "0.9.0"
