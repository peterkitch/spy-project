"""
Phase 5B Item 9 regression tests: ImpactSearch structured rejection
diagnostics.

Mirrors the OnePass Item 7 rejection-diagnostics test pattern with the
``[IMPACTSEARCH:...]`` prefix instead of ``[ONEPASS:...]``. Each test
triggers one specific failure path on a bounded set of ImpactSearch
functions (``load_signal_library``, ``fetch_data_raw``,
``_coerce_to_close_frame``, ``fetch_data``, ``export_results_to_excel``,
``process_single_ticker``, ``process_primary_tickers``) and asserts:

  * the function still returns its original sentinel (None / empty
    DataFrame / empty DataFrame tuple / [] / False)
  * the caller-supplied ``rejection_out`` dict was populated with the
    expected ``reason`` code and the stable schema fields

Threading paths use the per-future wrapper so concurrent workers cannot
race on a shared mutable rejection_out dict.

ASCII-only assertion messages per CLAUDE.md cp1252 discipline.
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

import impactsearch  # noqa: E402
import provenance_manifest as pm  # noqa: E402
from phase2_test_utils import (  # noqa: E402
    make_signal_library_dict,
    make_synthetic_close_prices,
)


# ---------------------------------------------------------------------------
# Local fixture helpers
# ---------------------------------------------------------------------------


def _write_valid_lib(library_dir: Path, ticker: str, *,
                    parity_hash: str,
                    mutate_after_attach: bool = False) -> Path:
    """Write a tiny ImpactSearch-style daily signal library with
    optional manifest tampering."""
    library_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(start="2024-01-02", periods=20)
    closes = make_synthetic_close_prices(dates)
    sigs = ["Buy", "Short", "None"] * (len(dates) // 3) + (
        ["None"] * (len(dates) % 3)
    )
    lib = make_signal_library_dict(
        dates,
        engine_version="1.0.0",
        price_source="Close",
        parity_hash=parity_hash,
        primary_signals=sigs,
    )
    lib["signals"] = list(sigs)
    lib["interval"] = "1d"
    lib["ticker"] = ticker
    lib["build_timestamp"] = "2025-01-01T00:00:00"
    fname = f"{ticker}_stable_v1_0_0.pkl"
    path = library_dir / fname
    pm.attach_manifest(
        lib, path,
        artifact_type="signal_library_daily",
        ticker=ticker,
        interval="1d",
        params={
            "engine_version": "1.0.0",
            "MAX_SMA_DAY": 114,
            "price_source": "Close",
            "parity_hash": parity_hash,
            "interval": "1d",
        },
        source_close=closes,
        engine_version="1.0.0",
    )
    if mutate_after_attach:
        lib["primary_signals"] = ["Short"] * len(lib["primary_signals"])
    with open(path, "wb") as f:
        pickle.dump(lib, f)
    return path


def _assert_rejection_shape(rec: dict, *, expected_reason: str,
                             expected_stage: str,
                             expected_ticker=None):
    """Common shape assertions on a rejection record."""
    assert isinstance(rec, dict) and rec, (
        f"expected populated dict, got {rec!r}"
    )
    assert rec.get("reason") == expected_reason, (
        f"reason mismatch: got {rec.get('reason')!r}, "
        f"expected {expected_reason!r}"
    )
    assert rec.get("stage") == expected_stage, (
        f"stage mismatch: got {rec.get('stage')!r}, "
        f"expected {expected_stage!r}"
    )
    if expected_ticker is not None:
        assert rec.get("ticker") == expected_ticker, (
            f"ticker mismatch: got {rec.get('ticker')!r}, "
            f"expected {expected_ticker!r}"
        )
    assert rec.get("message"), "message field must be non-empty"
    assert rec.get("action"), "action field must be non-empty"
    assert isinstance(rec.get("retryable"), bool), (
        "retryable must be bool"
    )


# ---------------------------------------------------------------------------
# load_signal_library reason codes
# ---------------------------------------------------------------------------


def test_load_signal_library_missing_library_populates_rejection(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(impactsearch, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    rejection: dict = {}
    out = impactsearch.load_signal_library("NOSUCH", rejection_out=rejection)
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.LOAD_MISSING_LIBRARY,
        expected_stage="load",
        expected_ticker="NOSUCH",
    )


def test_load_signal_library_corrupt_library_populates_rejection(
    tmp_path, monkeypatch,
):
    stable = tmp_path / "stable"
    stable.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(impactsearch, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    corrupt_path = stable / "AAA_stable_v1_0_0.pkl"
    corrupt_path.write_bytes(b"this is not a valid pickle")

    rejection: dict = {}
    out = impactsearch.load_signal_library("AAA", rejection_out=rejection)
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.LOAD_CORRUPT_LIBRARY,
        expected_stage="load",
        expected_ticker="AAA",
    )
    assert (corrupt_path.parent / (corrupt_path.name + ".corrupt")).exists()


def test_load_signal_library_manifest_failed_populates_rejection(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(impactsearch, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    # ImpactSearch's load_signal_library does not check parity_hash;
    # a literal sentinel suffices for fixture libraries.
    parity = "PHASE_5B_ITEM9_TEST_PARITY"
    _write_valid_lib(
        tmp_path / "stable", "BBB",
        parity_hash=parity,
        mutate_after_attach=True,
    )
    rejection: dict = {}
    out = impactsearch.load_signal_library("BBB", rejection_out=rejection)
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.LOAD_MANIFEST_FAILED,
        expected_stage="load",
        expected_ticker="BBB",
    )
    assert "details" in rejection
    assert "mismatches" in rejection["details"]


def test_load_signal_library_version_mismatch_populates_rejection(
    tmp_path, monkeypatch,
):
    """A library whose embedded engine_version differs from the module
    constant while its manifest still verifies cleanly: returns None,
    reason == version_mismatch.
    """
    stable = tmp_path / "stable"
    monkeypatch.setattr(impactsearch, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    # ImpactSearch's load_signal_library does not check parity_hash;
    # a literal sentinel suffices for fixture libraries.
    parity = "PHASE_5B_ITEM9_TEST_PARITY"

    stable.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(start="2024-01-02", periods=20)
    closes = make_synthetic_close_prices(dates)
    sigs = ["None"] * len(dates)
    lib = make_signal_library_dict(
        dates,
        engine_version="1.0.1",  # payload says 1.0.1
        price_source="Close",
        parity_hash=parity,
        primary_signals=sigs,
    )
    lib["signals"] = list(sigs)
    lib["interval"] = "1d"
    lib["ticker"] = "CCC"
    lib["build_timestamp"] = "2025-01-01T00:00:00"
    path = stable / "CCC_stable_v1_0_0.pkl"
    pm.attach_manifest(
        lib, path,
        artifact_type="signal_library_daily",
        ticker="CCC",
        interval="1d",
        params={
            # Manifest params record the CURRENT engine_version so the
            # requested_params subset check at load time passes.
            "engine_version": impactsearch.ENGINE_VERSION,
            "MAX_SMA_DAY": 114,
            "price_source": "Close",
            "parity_hash": parity,
            "interval": "1d",
        },
        source_close=closes,
        engine_version=impactsearch.ENGINE_VERSION,
    )
    with open(path, "wb") as f:
        pickle.dump(lib, f)

    rejection: dict = {}
    out = impactsearch.load_signal_library("CCC", rejection_out=rejection)
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.LOAD_VERSION_MISMATCH,
        expected_stage="load",
        expected_ticker="CCC",
    )


def test_load_signal_library_first_candidate_rejected_fallback_succeeds_clears_rejection_out(
    tmp_path, monkeypatch,
):
    """Mirrors OnePass Item 7 amendment: when the first candidate fails
    (corrupt -> rejection populated, quarantined) but a later candidate
    succeeds, rejection_out must be empty on return.
    """
    stable = tmp_path / "stable"
    stable.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(impactsearch, "SIGNAL_LIBRARY_DIR", str(tmp_path))

    corrupt_path = stable / "BRK.A_stable_v1_0_0.pkl"
    corrupt_path.write_bytes(b"this is not a valid pickle")

    # ImpactSearch's load_signal_library does not check parity_hash;
    # a literal sentinel suffices for fixture libraries.
    parity = "PHASE_5B_ITEM9_TEST_PARITY"
    fallback_path = _write_valid_lib(stable, "BRK-A", parity_hash=parity)
    assert fallback_path.exists()

    rejection: dict = {}
    out = impactsearch.load_signal_library("BRK.A", rejection_out=rejection)
    assert out is not None
    assert out.get("ticker") == "BRK-A"
    assert rejection == {}, (
        f"expected rejection_out cleared on fallback success, got {rejection!r}"
    )
    assert (corrupt_path.parent / (corrupt_path.name + ".corrupt")).exists()


# ---------------------------------------------------------------------------
# fetch_data_raw reason codes
# ---------------------------------------------------------------------------


class _RateLimitedYf:
    @staticmethod
    def download(*args, **kwargs):
        raise RuntimeError("yfinance rate limit exceeded (HTTP 429)")


class _GenericYfFailure:
    @staticmethod
    def download(*args, **kwargs):
        raise RuntimeError("connection reset by peer")


class _EmptyYf:
    @staticmethod
    def download(*args, **kwargs):
        return pd.DataFrame()


def test_fetch_data_raw_blank_ticker_populates_rejection():
    rejection_a: dict = {}
    df_a, resolved_a = impactsearch.fetch_data_raw(
        "", rejection_out=rejection_a,
    )
    assert df_a.empty and resolved_a == ""
    _assert_rejection_shape(
        rejection_a,
        expected_reason=impactsearch.FETCH_INVALID_TICKER,
        expected_stage="fetch",
        expected_ticker="",
    )
    assert rejection_a["retryable"] is False

    rejection_b: dict = {}
    df_b, _resolved_b = impactsearch.fetch_data_raw(
        "   ", rejection_out=rejection_b,
    )
    assert df_b.empty
    _assert_rejection_shape(
        rejection_b,
        expected_reason=impactsearch.FETCH_INVALID_TICKER,
        expected_stage="fetch",
    )

    rejection_c: dict = {}
    df_c, resolved_c = impactsearch.fetch_data_raw(
        None, rejection_out=rejection_c,
    )
    assert df_c.empty and resolved_c is None
    _assert_rejection_shape(
        rejection_c,
        expected_reason=impactsearch.FETCH_INVALID_TICKER,
        expected_stage="fetch",
    )


def test_fetch_data_raw_unsupported_period_populates_rejection(monkeypatch):
    """The pre-existing PERIOD_REGISTRY 'no_max' fast-skip must surface
    a structured ``unsupported_period`` rejection."""
    # Force the registry to report 'no_max' for the test ticker.
    monkeypatch.setattr(
        impactsearch.PERIOD_REGISTRY,
        "get_status",
        lambda t: "no_max",
    )
    monkeypatch.setattr(
        impactsearch.PERIOD_REGISTRY,
        "get_last_checked",
        lambda t: "2026-01-01",
    )
    monkeypatch.setattr(impactsearch, "PERIOD_FORCE_RECHECK", False)
    rejection: dict = {}
    df, resolved = impactsearch.fetch_data_raw(
        "ZZZ", rejection_out=rejection,
    )
    assert df.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.FETCH_UNSUPPORTED_PERIOD,
        expected_stage="fetch",
    )
    assert rejection["retryable"] is False


def test_fetch_data_raw_rate_limited_populates_rejection(monkeypatch):
    monkeypatch.setattr(impactsearch, "yf", _RateLimitedYf)
    monkeypatch.setattr(impactsearch.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(impactsearch, "PERIOD_FORCE_RECHECK", True)
    rejection: dict = {}
    df, resolved = impactsearch.fetch_data_raw(
        "ZZZ", max_retries=2, rejection_out=rejection,
    )
    assert df.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.FETCH_RATE_LIMITED,
        expected_stage="fetch",
    )
    assert rejection["retryable"] is True


def test_fetch_data_raw_generic_yfinance_exception_populates_rejection(
    monkeypatch,
):
    monkeypatch.setattr(impactsearch, "yf", _GenericYfFailure)
    monkeypatch.setattr(impactsearch.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(impactsearch, "PERIOD_FORCE_RECHECK", True)
    rejection: dict = {}
    df, resolved = impactsearch.fetch_data_raw(
        "ZZZ", max_retries=2, rejection_out=rejection,
    )
    assert df.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.FETCH_YFINANCE_EXCEPTION,
        expected_stage="fetch",
    )
    assert rejection["retryable"] is False


def test_fetch_data_raw_no_data_populates_rejection(monkeypatch):
    monkeypatch.setattr(impactsearch, "yf", _EmptyYf)
    monkeypatch.setattr(impactsearch.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(impactsearch, "PERIOD_FORCE_RECHECK", True)
    rejection: dict = {}
    df, resolved = impactsearch.fetch_data_raw(
        "ZZZ", max_retries=2, rejection_out=rejection,
    )
    assert df.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.FETCH_NO_DATA,
        expected_stage="fetch",
    )


# ---------------------------------------------------------------------------
# _coerce_to_close_frame reason codes
# ---------------------------------------------------------------------------


def test_coerce_to_close_frame_empty_input_populates_rejection():
    rejection: dict = {}
    out = impactsearch._coerce_to_close_frame(
        pd.DataFrame(), rejection_out=rejection, ticker="QQQ",
    )
    assert out.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.COERCE_EMPTY_INPUT,
        expected_stage="coerce",
        expected_ticker="QQQ",
    )


def test_coerce_to_close_frame_missing_close_column_populates_rejection():
    df = pd.DataFrame({"Volume": [1, 2, 3]},
                      index=pd.bdate_range("2024-01-02", periods=3))
    rejection: dict = {}
    out = impactsearch._coerce_to_close_frame(
        df, rejection_out=rejection, ticker="QQQ",
    )
    assert out.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.COERCE_MISSING_CLOSE_COLUMN,
        expected_stage="coerce",
        expected_ticker="QQQ",
    )
    assert rejection["details"].get("available_columns") == ["Volume"]


def test_coerce_to_close_frame_ambiguous_columns_populates_rejection():
    idx = pd.bdate_range("2024-01-02", periods=3)
    cols = pd.MultiIndex.from_product([["Close"], ["AAA", "BBB"]])
    df = pd.DataFrame(np.arange(6, dtype=float).reshape(3, 2),
                      index=idx, columns=cols)
    rejection: dict = {}
    out = impactsearch._coerce_to_close_frame(
        df, rejection_out=rejection, ticker="?",
    )
    assert out.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.COERCE_AMBIGUOUS_PRICE_COLUMNS,
        expected_stage="coerce",
    )


# ---------------------------------------------------------------------------
# export_results_to_excel reason code
# ---------------------------------------------------------------------------


def test_export_xlsx_manifest_failed_populates_rejection(
    tmp_path, monkeypatch,
):
    """Force the sidecar manifest write to fail by monkeypatching
    json.dump inside impactsearch to raise. Workbook write itself must
    still succeed (warning-only fallback preserved); rejection_out must
    capture the structured reason."""
    out_path = tmp_path / "ZZZ_analysis.xlsx"
    metrics = [{"Primary Ticker": "AAA", "Sharpe Ratio": 1.0}]

    # Force the json.dump call inside the sidecar write block to raise.
    real_json_dump = impactsearch.json.dump

    def _boom_for_sidecar(obj, fh, *a, **kw):
        # Only fail when we look like the sidecar payload (contains
        # 'artifact_type' key from build_xlsx_output_manifest).
        if isinstance(obj, dict) and obj.get("artifact_type") == "impactsearch_xlsx":
            raise OSError("simulated sidecar write failure")
        return real_json_dump(obj, fh, *a, **kw)

    monkeypatch.setattr(impactsearch.json, "dump", _boom_for_sidecar)
    rejection: dict = {}
    impactsearch.export_results_to_excel(
        str(out_path), metrics, rejection_out=rejection,
    )
    # Workbook itself was written.
    assert out_path.exists()
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.EXPORT_XLSX_MANIFEST_FAILED,
        expected_stage="export",
    )


# ---------------------------------------------------------------------------
# process_single_ticker reason codes (insufficient_data, no_metrics)
# ---------------------------------------------------------------------------


def test_process_single_ticker_no_data_forwards_fetch_rejection(
    tmp_path, monkeypatch,
):
    """When the underlying fetch returns empty, process_single_ticker
    forwards the structured fetch rejection up to the caller."""
    # Isolation: disable the FASTPATH so the test cannot read real
    # operational state. A leaked ``ZZZ_stable_*.pkl`` in the real
    # ``signal_library/data/stable`` would otherwise make
    # ``get_primary_signals_fast`` return library signals and bypass the
    # monkeypatched ``fetch_data_raw`` (the impact_fastpath module reads its
    # OWN ``SIGNAL_LIBRARY_DIR``, so patching impactsearch's copy is not
    # enough). Disabling FASTPATH forces the intended slow-path fetch
    # rejection; also point the slow-path library root at an empty tmp dir.
    monkeypatch.setattr(impactsearch, "FASTPATH_AVAILABLE", False)
    monkeypatch.setattr(
        impactsearch, "SIGNAL_LIBRARY_DIR",
        str(tmp_path / "signal_library" / "data"),
    )
    monkeypatch.setattr(
        impactsearch, "fetch_data_raw",
        lambda t, *a, **kw: (pd.DataFrame(), t),
    )
    sec_df = pd.DataFrame(
        {"Close": np.linspace(100.0, 105.0, 5)},
        index=pd.bdate_range("2024-01-02", periods=5),
    )
    rejection: dict = {}
    out = impactsearch.process_single_ticker(
        "ZZZ", sec_df, rejection_out=rejection,
    )
    assert out is None
    # Fetch returned no rejection (mock didn't populate), so
    # process_single_ticker synthesises one. Either way the stage must
    # be 'fetch' and the reason must be a fetch-stage reason.
    assert rejection.get("stage") == "fetch"
    assert rejection.get("reason") in (
        impactsearch.FETCH_NO_DATA,
        impactsearch.FETCH_INVALID_TICKER,
        impactsearch.FETCH_UNSUPPORTED_PERIOD,
    )


def test_process_single_ticker_insufficient_data_populates_rejection(
    tmp_path, monkeypatch,
):
    """A primary that fetches successfully but yields a frame with
    fewer than 2 bars must surface insufficient_data."""
    # Isolation: disable FASTPATH so the leaked real ``ZZZ_stable_*.pkl`` is
    # not consulted (the impact_fastpath module reads its own
    # ``SIGNAL_LIBRARY_DIR``, so the patch below alone does not cover it).
    # The slow path then uses the monkeypatched fetch_data_raw plus the
    # isolated, empty signal-library root.
    monkeypatch.setattr(impactsearch, "FASTPATH_AVAILABLE", False)
    monkeypatch.setattr(
        impactsearch, "SIGNAL_LIBRARY_DIR",
        str(tmp_path / "signal_library" / "data"),
    )
    # Mock fetch + coerce so they return a 1-row frame.
    one_row = pd.DataFrame(
        {"Close": [100.0]},
        index=[pd.Timestamp("2024-01-02")],
    )
    one_row_raw = one_row.copy()
    monkeypatch.setattr(
        impactsearch, "fetch_data_raw",
        lambda t, *a, **kw: (one_row_raw, t),
    )
    sec_df = pd.DataFrame(
        {"Close": np.linspace(100.0, 110.0, 10)},
        index=pd.bdate_range("2024-01-02", periods=10),
    )
    rejection: dict = {}
    out = impactsearch.process_single_ticker(
        "ZZZ", sec_df, rejection_out=rejection,
    )
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.PROCESS_INSUFFICIENT_DATA,
        expected_stage="process",
    )


# ---------------------------------------------------------------------------
# process_primary_tickers integration
# ---------------------------------------------------------------------------


def test_process_primary_tickers_secondary_no_data_records_recent_error(
    monkeypatch,
):
    """When the secondary fetch returns empty, process_primary_tickers
    returns [] AND records a [IMPACTSEARCH:no_data] (or
    invalid_ticker / unsupported_period if the mock surfaces those)
    string in progress_tracker['recent_errors']."""
    # Snapshot/restore tracker.
    saved = dict(impactsearch.progress_tracker)
    try:
        with impactsearch.progress_lock:
            impactsearch.progress_tracker.update({
                'recent_errors': [],
            })
        # Force the secondary fetch to return empty.
        monkeypatch.setattr(
            impactsearch, "fetch_data_raw",
            lambda t, *a, **kw: (pd.DataFrame(), t),
        )
        result = impactsearch.process_primary_tickers(
            "SEC_NO_DATA", ["AAA"], use_multiprocessing=False,
            mark_complete=True,
        )
        assert result == []
        recent = list(impactsearch.progress_tracker.get('recent_errors') or [])
        assert recent, "expected at least one recent_errors entry"
        joined = " | ".join(recent)
        assert "[IMPACTSEARCH:" in joined, (
            f"expected ImpactSearch-tagged error string in recent_errors, "
            f"got {recent!r}"
        )
    finally:
        with impactsearch.progress_lock:
            impactsearch.progress_tracker.clear()
            impactsearch.progress_tracker.update(saved)


def test_process_primary_tickers_threaded_per_future_rejection_isolation(
    tmp_path, monkeypatch,
):
    """The threaded path must use per-future rejection_out dicts so
    concurrent workers cannot race on a shared mutable diagnostic
    surface. We exercise the threaded path with use_multiprocessing=True
    and >3 primaries; each primary fetches empty so each future
    populates its own dict; the main thread aggregates into
    recent_errors."""
    saved = dict(impactsearch.progress_tracker)
    try:
        with impactsearch.progress_lock:
            impactsearch.progress_tracker.update({
                'recent_errors': [],
                'total_tickers': 0,
                'current_index': 0,
                'start_time': None,
            })
        # Secondary fetch: succeeds with a tiny frame.
        sec_df = pd.DataFrame(
            {"Close": np.linspace(100.0, 110.0, 10)},
            index=pd.bdate_range("2024-01-02", periods=10),
        )

        def _fake_fetch(t, *a, **kw):
            # Return success for the secondary (first call), empty for
            # primaries. Use ticker name as discriminator.
            if t == "SEC_OK":
                idx = pd.bdate_range("2024-01-02", periods=10)
                df = pd.DataFrame({"Close": np.linspace(100.0, 110.0, 10)},
                                  index=idx)
                return df, t
            return pd.DataFrame(), t

        monkeypatch.setattr(impactsearch, "fetch_data_raw", _fake_fetch)
        primaries = ["P1", "P2", "P3", "P4"]
        result = impactsearch.process_primary_tickers(
            "SEC_OK", primaries, use_multiprocessing=True,
            mark_complete=True,
        )
        # All primaries failed -> empty metrics list.
        assert result == []
        recent = list(impactsearch.progress_tracker.get('recent_errors') or [])
        # Each primary should have produced at least one [IMPACTSEARCH:*]
        # entry. We don't pin the exact count because thread completion
        # ordering can vary, but every entry must be tagged.
        assert recent, "expected per-primary recent_errors entries"
        for entry in recent:
            assert "[IMPACTSEARCH:" in entry, (
                f"unexpectedly untagged error: {entry!r}"
            )
    finally:
        with impactsearch.progress_lock:
            impactsearch.progress_tracker.clear()
            impactsearch.progress_tracker.update(saved)


# ---------------------------------------------------------------------------
# Edit 1 amendment: process_primary_tickers caller-facing rejection_out
# ---------------------------------------------------------------------------


def test_process_primary_tickers_rejection_out_kwarg_populated_on_failure(
    monkeypatch,
):
    """Phase 5B Item 9 amendment: process_primary_tickers exposes a
    caller-facing ``rejection_out`` kwarg that is populated on terminal
    failure paths returning ``[]``.

    The amendment requires:
      * Empty primary_tickers input does NOT populate rejection_out
        (no failure occurred — nothing to report).
      * Secondary fetch / coerce terminal failure forwards the
        secondary's structured rejection.
      * After processing one or more primaries with no successful
        metrics, populate from the FIRST per-primary structured
        rejection captured in this invocation, OR synthesize
        PROCESS_NO_METRICS if no per-primary rejection was captured.

    The local representative rejection is captured during the
    invocation -- NOT read from progress_tracker['recent_errors'].
    """
    saved = dict(impactsearch.progress_tracker)
    try:
        with impactsearch.progress_lock:
            impactsearch.progress_tracker.update({
                'recent_errors': [],
                'total_tickers': 0,
                'current_index': 0,
                'start_time': None,
            })

        # --- Sub-case 1: secondary fetch fails -> forwarded rejection
        monkeypatch.setattr(
            impactsearch, "fetch_data_raw",
            lambda t, *a, **kw: (pd.DataFrame(), t),
        )
        rejection_a: dict = {}
        result_a = impactsearch.process_primary_tickers(
            "SEC_NO_DATA", ["AAA"],
            use_multiprocessing=False, mark_complete=True,
            rejection_out=rejection_a,
        )
        assert result_a == []
        # Secondary rejection is forwarded to the caller's dict.
        # The reason should be a fetch-stage code (no_data,
        # invalid_ticker, or unsupported_period depending on which
        # path the mock surfaced).
        assert rejection_a.get("stage") == "fetch", (
            f"expected stage='fetch' for secondary fetch failure, "
            f"got {rejection_a!r}"
        )
        assert rejection_a.get("reason") in (
            impactsearch.FETCH_NO_DATA,
            impactsearch.FETCH_INVALID_TICKER,
            impactsearch.FETCH_UNSUPPORTED_PERIOD,
        )
        assert rejection_a.get("message")
        assert rejection_a.get("action")

        # --- Sub-case 2: empty primary list does NOT populate rejection_out.
        # Restore a working secondary fetch so the function reaches
        # the per-primary loop with zero primaries.
        sec_idx = pd.bdate_range("2024-01-02", periods=10)
        sec_df_ok = pd.DataFrame(
            {"Close": np.linspace(100.0, 110.0, 10)}, index=sec_idx,
        )

        def _fake_fetch_ok_secondary(t, *a, **kw):
            if t == "SEC_OK":
                return sec_df_ok.copy(), t
            return pd.DataFrame(), t

        monkeypatch.setattr(
            impactsearch, "fetch_data_raw", _fake_fetch_ok_secondary,
        )
        rejection_b: dict = {}
        result_b = impactsearch.process_primary_tickers(
            "SEC_OK", [],  # empty primaries
            use_multiprocessing=False, mark_complete=True,
            rejection_out=rejection_b,
        )
        assert result_b == []
        assert rejection_b == {}, (
            f"expected empty rejection on empty-primary-input, "
            f"got {rejection_b!r}"
        )

        # --- Sub-case 3: processed primaries with no metrics ->
        # populate from the first per-primary rejection captured.
        # We make every primary's process_single_ticker fail with
        # a known reason by mocking it directly.
        def _failing_process_single(prim, sec_df, sma_cache=None,
                                     analysis_clock=None,
                                     *, rejection_out=None):
            impactsearch._populate_rejection(
                rejection_out, "process",
                impactsearch.PROCESS_INSUFFICIENT_DATA,
                ticker=prim,
                message=f"insufficient data for {prim}",
                action="extend the date range",
            )
            return None

        monkeypatch.setattr(
            impactsearch, "process_single_ticker", _failing_process_single,
        )
        rejection_c: dict = {}
        result_c = impactsearch.process_primary_tickers(
            "SEC_OK", ["P1", "P2"],
            use_multiprocessing=False, mark_complete=True,
            rejection_out=rejection_c,
        )
        assert result_c == []
        # The first per-primary rejection should be in rejection_c.
        assert rejection_c.get("stage") == "process"
        assert rejection_c.get("reason") == \
            impactsearch.PROCESS_INSUFFICIENT_DATA
        assert rejection_c.get("ticker") in ("P1", "P2")
        assert rejection_c.get("message")
        assert rejection_c.get("action")

        # --- Sub-case 4: processed primaries with no captured per-
        # primary rejection (process_single_ticker returns None
        # without populating rejection_out at all) -> synthesize
        # PROCESS_NO_METRICS.
        def _silent_process_single(prim, sec_df, sma_cache=None,
                                    analysis_clock=None,
                                    *, rejection_out=None):
            return None  # no rejection populated

        monkeypatch.setattr(
            impactsearch, "process_single_ticker", _silent_process_single,
        )
        rejection_d: dict = {}
        result_d = impactsearch.process_primary_tickers(
            "SEC_OK", ["P3"],
            use_multiprocessing=False, mark_complete=True,
            rejection_out=rejection_d,
        )
        assert result_d == []
        assert rejection_d.get("stage") == "process"
        assert rejection_d.get("reason") == \
            impactsearch.PROCESS_NO_METRICS, (
                f"expected synthesized PROCESS_NO_METRICS fallback, "
                f"got {rejection_d!r}"
            )
    finally:
        with impactsearch.progress_lock:
            impactsearch.progress_tracker.clear()
            impactsearch.progress_tracker.update(saved)


# ---------------------------------------------------------------------------
# Edit 2 amendment: FASTPATH no_metrics rejection
# ---------------------------------------------------------------------------


def test_process_single_ticker_fastpath_no_metrics_populates_rejection(
    monkeypatch,
):
    """Phase 5B Item 9 amendment: the FASTPATH branch of
    process_single_ticker must populate ``PROCESS_NO_METRICS`` on the
    no-metrics return path. Previously a silent-absence branch.
    """
    # Force the FASTPATH branch by enabling the trust flags and
    # making get_primary_signals_fast return a usable signal series.
    monkeypatch.setattr(impactsearch, "FASTPATH_AVAILABLE", True)
    monkeypatch.setattr(impactsearch, "IMPACT_TRUST_LIBRARY", True)

    sig_idx = pd.bdate_range("2024-01-02", periods=10)
    sig_series = pd.Series(["Buy"] * 10, index=sig_idx)
    monkeypatch.setattr(
        impactsearch, "get_primary_signals_fast",
        lambda prim, sec_index: (sig_series, "FASTPATH:test"),
    )

    # Force calculate_metrics_from_signals to return None so the
    # FASTPATH branch hits its no-metrics fallthrough.
    monkeypatch.setattr(
        impactsearch, "calculate_metrics_from_signals",
        lambda *a, **kw: None,
    )

    sec_df = pd.DataFrame(
        {"Close": np.linspace(100.0, 110.0, 10)}, index=sig_idx,
    )
    rejection: dict = {}
    out = impactsearch.process_single_ticker(
        "FAST_NO_METRICS", sec_df, rejection_out=rejection,
    )
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=impactsearch.PROCESS_NO_METRICS,
        expected_stage="process",
        expected_ticker="FAST_NO_METRICS",
    )


# ---------------------------------------------------------------------------
# update_progress recent_errors rendering
# ---------------------------------------------------------------------------


def test_update_progress_renders_recent_errors():
    """Mirror Item 7 Edit 1 amendment: update_progress consumes
    progress_tracker['recent_errors'] and surfaces the formatted strings
    through its existing return tuple. No Dash server is started."""
    saved = dict(impactsearch.progress_tracker)
    try:
        with impactsearch.progress_lock:
            impactsearch.progress_tracker.update({
                'current_ticker': 'SEC1 . PRIM1',
                'current_index': 1,
                'total_tickers': 1,
                'start_time': 1700000000.0,
                'results': [],
                'status': 'complete',
                'show_metrics': False,
                'excel_path': None,
                'excel_paths': [],
                'excel_paths_updated': [],
                'tickers_not_found': [],
                'secondary_total': 1,
                'secondary_index': 1,
                'current_secondary': 'SEC1',
                'recent_errors': [
                    "[IMPACTSEARCH:no_data] PRIM1: yfinance returned "
                    "empty data. Action: confirm the ticker is valid."
                ],
            })

        # processing_state['status'] must be 'processing' to bypass the
        # early PreventUpdate; the body then reads the tracker (which
        # we set to 'complete') and produces the completion summary.
        result = impactsearch.update_progress(
            n_intervals=1, processing_state={'status': 'processing'},
        )
    finally:
        with impactsearch.progress_lock:
            impactsearch.progress_tracker.clear()
            impactsearch.progress_tracker.update(saved)

    rendered = str(result)
    assert "[IMPACTSEARCH:no_data]" in rendered, (
        f"expected formatted [IMPACTSEARCH:no_data] string in "
        f"update_progress return surface; got rendered repr (truncated):\n"
        f"{rendered[:2000]}"
    )
