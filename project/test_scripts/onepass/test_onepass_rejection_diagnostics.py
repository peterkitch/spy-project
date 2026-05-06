"""
Phase 5B Item 7 regression tests: OnePass structured rejection
diagnostics.

Pins the new ``rejection_out`` opt-in kwarg added to four bounded
OnePass functions: ``load_signal_library``, ``fetch_data_raw``,
``_coerce_to_close_frame``, and ``save_signal_library``. Each test
triggers one specific failure path and asserts:

  * the function still returns its original sentinel (None / empty
    DataFrame / (DataFrame(), ticker) tuple / False)
  * the caller-supplied ``rejection_out`` dict was populated with a
    matching ``reason`` code and the stable schema fields

The pattern mirrors the Phase 3B-2B StackBuilder
``test_stackbuilder_stale_xlsx_message.py`` precedent.

ASCII-only assertion messages per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path
from unittest import mock

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
    """Mirror ``test_provenance_manifest._write_lib_for_consumer`` for
    onepass-style daily libraries (``<ticker>_stable_v1_0_0.pkl``)."""
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
        # Mutate the in-memory library after manifest attach, then write
        # to disk -- recomputed content_hash will differ from the
        # embedded manifest hash.
        lib["primary_signals"] = ["Short"] * len(lib["primary_signals"])
    with open(path, "wb") as f:
        pickle.dump(lib, f)
    return path


def _assert_rejection_shape(rec: dict, *, expected_reason: str,
                             expected_stage: str,
                             expected_ticker: str | None = None):
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
    """No library on disk: returns None, reason == missing_library."""
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    rejection: dict = {}
    out = onepass.load_signal_library("NOSUCH", rejection_out=rejection)
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.LOAD_MISSING_LIBRARY,
        expected_stage="load",
        expected_ticker="NOSUCH",
    )


def test_load_signal_library_corrupt_library_populates_rejection(
    tmp_path, monkeypatch,
):
    """Corrupt pickle bytes: returns None, reason == corrupt_library,
    file is quarantined to .corrupt (preserves existing behavior)."""
    stable = tmp_path / "stable"
    stable.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    corrupt_path = stable / "AAA_stable_v1_0_0.pkl"
    corrupt_path.write_bytes(b"this is not a valid pickle")

    rejection: dict = {}
    out = onepass.load_signal_library("AAA", rejection_out=rejection)
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.LOAD_CORRUPT_LIBRARY,
        expected_stage="load",
        expected_ticker="AAA",
    )
    # Corrupt-file quarantine: original path should now have .corrupt
    # alongside (preserve existing onepass behavior).
    assert (corrupt_path.parent / (corrupt_path.name + ".corrupt")).exists()


def test_load_signal_library_manifest_failed_populates_rejection(
    tmp_path, monkeypatch,
):
    """Manifest mismatch (content_hash != recorded): returns None,
    reason == manifest_failed."""
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    parity = onepass.compute_parity_hash("Close", "ticker")
    _write_valid_lib(
        tmp_path / "stable", "BBB",
        parity_hash=parity,
        mutate_after_attach=True,  # tamper -> hash mismatch
    )
    rejection: dict = {}
    out = onepass.load_signal_library("BBB", rejection_out=rejection)
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.LOAD_MANIFEST_FAILED,
        expected_stage="load",
        expected_ticker="BBB",
    )
    assert "details" in rejection
    assert "mismatches" in rejection["details"]


def test_load_signal_library_version_mismatch_populates_rejection(
    tmp_path, monkeypatch,
):
    """Library whose embedded ``engine_version`` differs from the
    module constant while its manifest still verifies cleanly: returns
    None, reason == version_mismatch.

    The post-load equality check fires only after the manifest has
    verified successfully, so we must construct a lib whose manifest
    params record the CURRENT module engine_version (matching
    requested_params at load time) but whose payload field
    ``engine_version`` was set to something different just before
    manifest attach. The canonical content_hash captures that payload
    value so manifest verification passes round-trip.
    """
    stable = tmp_path / "stable"
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    parity = onepass.compute_parity_hash("Close", "ticker")

    stable.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(start="2024-01-02", periods=20)
    closes = make_synthetic_close_prices(dates)
    sigs = ["None"] * len(dates)
    lib = make_signal_library_dict(
        dates,
        engine_version="1.0.1",  # payload says 1.0.1...
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
            # ...but manifest params record the CURRENT engine_version
            # so requested_params subset check at load time passes.
            "engine_version": onepass.ENGINE_VERSION,
            "MAX_SMA_DAY": 114,
            "price_source": "Close",
            "parity_hash": parity,
            "interval": "1d",
        },
        source_close=closes,
        engine_version=onepass.ENGINE_VERSION,
    )
    with open(path, "wb") as f:
        pickle.dump(lib, f)

    rejection: dict = {}
    out = onepass.load_signal_library("CCC", rejection_out=rejection)
    assert out is None
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.LOAD_VERSION_MISMATCH,
        expected_stage="load",
        expected_ticker="CCC",
    )


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


def test_fetch_data_raw_rate_limited_populates_rejection(monkeypatch):
    monkeypatch.setattr(onepass, "yf", _RateLimitedYf)
    # Avoid backoff sleeps slowing the test.
    monkeypatch.setattr(onepass.time, "sleep", lambda *_a, **_k: None)
    rejection: dict = {}
    df, resolved = onepass.fetch_data_raw("ZZZ", max_retries=2,
                                          rejection_out=rejection)
    assert df.empty and resolved == "ZZZ"
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.FETCH_RATE_LIMITED,
        expected_stage="fetch",
        expected_ticker="ZZZ",
    )
    assert rejection["retryable"] is True


def test_fetch_data_raw_generic_yfinance_exception_populates_rejection(
    monkeypatch,
):
    monkeypatch.setattr(onepass, "yf", _GenericYfFailure)
    monkeypatch.setattr(onepass.time, "sleep", lambda *_a, **_k: None)
    rejection: dict = {}
    df, resolved = onepass.fetch_data_raw("ZZZ", max_retries=2,
                                          rejection_out=rejection)
    assert df.empty and resolved == "ZZZ"
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.FETCH_YFINANCE_EXCEPTION,
        expected_stage="fetch",
        expected_ticker="ZZZ",
    )
    assert rejection["retryable"] is False


def test_fetch_data_raw_no_data_populates_rejection(monkeypatch):
    monkeypatch.setattr(onepass, "yf", _EmptyYf)
    monkeypatch.setattr(onepass.time, "sleep", lambda *_a, **_k: None)
    rejection: dict = {}
    df, resolved = onepass.fetch_data_raw("ZZZ", max_retries=2,
                                          rejection_out=rejection)
    assert df.empty and resolved == "ZZZ"
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.FETCH_NO_DATA,
        expected_stage="fetch",
        expected_ticker="ZZZ",
    )


# ---------------------------------------------------------------------------
# _coerce_to_close_frame reason codes
# ---------------------------------------------------------------------------


def test_coerce_to_close_frame_empty_input_populates_rejection():
    rejection: dict = {}
    out = onepass._coerce_to_close_frame(
        pd.DataFrame(), rejection_out=rejection, ticker="QQQ",
    )
    assert out.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.COERCE_EMPTY_INPUT,
        expected_stage="coerce",
        expected_ticker="QQQ",
    )


def test_coerce_to_close_frame_missing_close_column_populates_rejection():
    df = pd.DataFrame({"Volume": [1, 2, 3]},
                      index=pd.bdate_range("2024-01-02", periods=3))
    rejection: dict = {}
    out = onepass._coerce_to_close_frame(
        df, rejection_out=rejection, ticker="QQQ",
    )
    assert out.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.COERCE_MISSING_CLOSE_COLUMN,
        expected_stage="coerce",
        expected_ticker="QQQ",
    )
    assert "details" in rejection
    assert rejection["details"].get("available_columns") == ["Volume"]


def test_coerce_to_close_frame_ambiguous_columns_populates_rejection():
    """MultiIndex with multiple tickers must trigger
    ambiguous_price_columns."""
    idx = pd.bdate_range("2024-01-02", periods=3)
    cols = pd.MultiIndex.from_product([["Close"], ["AAA", "BBB"]])
    df = pd.DataFrame(np.arange(6, dtype=float).reshape(3, 2),
                      index=idx, columns=cols)
    rejection: dict = {}
    out = onepass._coerce_to_close_frame(
        df, rejection_out=rejection, ticker="?",
    )
    assert out.empty
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.COERCE_AMBIGUOUS_PRICE_COLUMNS,
        expected_stage="coerce",
    )


# ---------------------------------------------------------------------------
# save_signal_library reason code
# ---------------------------------------------------------------------------


def test_save_signal_library_save_failed_populates_rejection(
    tmp_path, monkeypatch,
):
    """Force save_signal_library to fail by monkeypatching pickle.dump
    inside onepass to raise OSError. The function must return False
    and populate rejection with reason == save_failed."""
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))
    dates = pd.bdate_range("2024-01-02", periods=10)
    df = pd.DataFrame({"Close": np.linspace(100.0, 110.0, 10)}, index=dates)
    primary_signals = ["None"] * len(dates)

    def _boom(*_args, **_kwargs):
        raise OSError("simulated disk full")
    monkeypatch.setattr(onepass.pickle, "dump", _boom)

    rejection: dict = {}
    ok = onepass.save_signal_library(
        "AAA", {}, {}, primary_signals, df,
        accumulator_state={"buy_cum_vector": np.zeros(1),
                           "short_cum_vector": np.zeros(1),
                           "last_date_processed": "2024-01-15",
                           "num_pairs": 1},
        price_source="Close", resolved_symbol="AAA",
        rejection_out=rejection,
    )
    assert ok is False
    _assert_rejection_shape(
        rejection,
        expected_reason=onepass.SAVE_FAILED,
        expected_stage="save",
        expected_ticker="AAA",
    )


# ---------------------------------------------------------------------------
# process_onepass_tickers integration: error string lands in run report
# ---------------------------------------------------------------------------


def test_fetch_data_raw_blank_ticker_populates_rejection():
    """Phase 5B Item 7 amendment: blank/None ticker is an explicit
    invalid-input case. The early return must populate rejection_out
    with reason ``invalid_ticker`` so callers can distinguish
    "you passed nothing" from "yfinance returned nothing for a
    valid symbol."
    """
    # Empty string.
    rejection_a: dict = {}
    df_a, resolved_a = onepass.fetch_data_raw("", rejection_out=rejection_a)
    assert df_a.empty
    assert resolved_a == ""
    _assert_rejection_shape(
        rejection_a,
        expected_reason=onepass.FETCH_INVALID_TICKER,
        expected_stage="fetch",
        expected_ticker="",
    )
    assert rejection_a["retryable"] is False

    # Whitespace-only.
    rejection_b: dict = {}
    df_b, resolved_b = onepass.fetch_data_raw("   ", rejection_out=rejection_b)
    assert df_b.empty
    assert resolved_b == "   "
    _assert_rejection_shape(
        rejection_b,
        expected_reason=onepass.FETCH_INVALID_TICKER,
        expected_stage="fetch",
        expected_ticker="   ",
    )
    assert rejection_b["retryable"] is False

    # None ticker.
    rejection_c: dict = {}
    df_c, resolved_c = onepass.fetch_data_raw(None, rejection_out=rejection_c)
    assert df_c.empty
    assert resolved_c is None
    _assert_rejection_shape(
        rejection_c,
        expected_reason=onepass.FETCH_INVALID_TICKER,
        expected_stage="fetch",
    )
    assert rejection_c["retryable"] is False
    assert rejection_c.get("ticker") is None


# ---------------------------------------------------------------------------
# Edit 2 amendment: clear rejection_out on successful fallback load
# ---------------------------------------------------------------------------


def test_load_signal_library_first_candidate_rejected_fallback_succeeds_clears_rejection_out(
    tmp_path, monkeypatch,
):
    """Phase 5B Item 7 amendment: when load_signal_library tries
    multiple naming-convention candidates and the FIRST candidate fails
    (corrupt -> populates rejection_out, gets quarantined) but a later
    candidate succeeds, the caller's rejection_out dict must be empty
    on return — operators should not see a "rejected" diagnostic
    alongside a successful library load.

    Trigger: a ticker with a dot (e.g. ``BRK.A``). load_signal_library
    builds candidates ``["BRK.A", "BRK-A"]``. We seed the dot-named
    file with corrupt bytes (so it fails and gets quarantined) and the
    dash-named file with a valid library (so the fallback iteration
    succeeds).
    """
    stable = tmp_path / "stable"
    stable.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(onepass, "SIGNAL_LIBRARY_DIR", str(tmp_path))

    # Candidate 1: dot-named file is corrupt. load_signal_library will
    # populate rejection_out with corrupt_library and continue.
    corrupt_path = stable / "BRK.A_stable_v1_0_0.pkl"
    corrupt_path.write_bytes(b"this is not a valid pickle")

    # Candidate 2: dash-named file is a valid library that survives
    # manifest verification. load_signal_library should return it and
    # clear the prior corrupt rejection from rejection_out.
    parity = onepass.compute_parity_hash("Close", "ticker")
    fallback_path = _write_valid_lib(stable, "BRK-A", parity_hash=parity)
    assert fallback_path.exists()

    rejection: dict = {}
    out = onepass.load_signal_library("BRK.A", rejection_out=rejection)
    assert out is not None, (
        "expected fallback dash-named library to load successfully"
    )
    assert out.get("ticker") == "BRK-A"
    assert rejection == {}, (
        f"expected rejection_out to be cleared on successful fallback, "
        f"got {rejection!r}"
    )
    # Sanity: corrupt candidate was quarantined to .corrupt as part
    # of the existing quarantine policy.
    assert (corrupt_path.parent / (corrupt_path.name + ".corrupt")).exists()


# ---------------------------------------------------------------------------
# Edit 1 amendment: update_progress renders recent_errors
# ---------------------------------------------------------------------------


def test_update_progress_renders_recent_errors():
    """Phase 5B Item 7 amendment: update_progress must consume
    ``progress_tracker['recent_errors']`` and surface the formatted
    error strings through its existing return surface. Without this,
    the worker-side population of recent_errors is invisible to the
    operator.

    No Dash server is started; the callback function is invoked
    directly with a processing-state dict so the early
    ``PreventUpdate`` is bypassed and the body executes.
    """
    # Snapshot the global progress_tracker so the test can restore it.
    saved = dict(onepass.progress_tracker)
    try:
        with onepass.progress_lock:
            onepass.progress_tracker.update({
                'status': 'complete',
                'current_ticker': 'BRK.A',
                'current_index': 5,
                'total': 5,
                'start_time': 1700000000.0,
                'created_count': 2,
                'updated_count': 2,
                'failed_count': 1,
                'elapsed_time': 12.5,
                'results': [],
                'recent_errors': [
                    "ZZZ: [ONEPASS:no_data] ZZZ: yfinance returned "
                    "empty data for ZZZ. Action: Confirm the ticker "
                    "is valid."
                ],
            })

        # state['status'] must be 'processing' to bypass the early
        # PreventUpdate; the function then reads progress_tracker and
        # branches on its 'complete' status to produce the summary
        # output we want to inspect.
        result = onepass.update_progress(
            n_intervals=1, state={'status': 'processing'},
        )
    finally:
        with onepass.progress_lock:
            onepass.progress_tracker.clear()
            onepass.progress_tracker.update(saved)

    # The return is a 9-tuple from update_progress. Stringify the
    # whole thing -- Dash html components include their children in
    # repr/str, so the formatted error string will appear if it was
    # rendered into ANY part of the surface.
    rendered = str(result)
    assert "[ONEPASS:no_data]" in rendered, (
        f"expected formatted [ONEPASS:no_data] error string in "
        f"update_progress return surface; got rendered repr (truncated):\n"
        f"{rendered[:2000]}"
    )


def test_process_onepass_tickers_no_data_populates_run_report_error(
    tmp_path, monkeypatch,
):
    """A failing-ticker scenario (yfinance returns empty) must surface
    a formatted error string in RUN_REPORT.to_dict()['outcomes'][...].
    No silent skip allowed."""
    monkeypatch.setattr(
        onepass, "SIGNAL_LIBRARY_DIR",
        str(tmp_path / "signal_library" / "data"),
    )
    # Force an empty-data fetch so the SKIPPED_NO_DATA branch fires.
    monkeypatch.setattr(
        onepass, "fetch_data_raw",
        lambda ticker, *a, **kw: (pd.DataFrame(), ticker),
    )
    monkeypatch.setattr(onepass, "tqdm", lambda iterable, **kw: iterable)
    monkeypatch.setattr(onepass, "RUN_REPORT", None)

    metrics = onepass.process_onepass_tickers(
        ["NODATA"],
        use_existing_signals=False,
        emit_summary=False,
        write_report_json=False,
    )
    assert metrics == [] or metrics is None or isinstance(metrics, list)
    rpt = onepass.RUN_REPORT.to_dict()
    outcomes = rpt.get("outcomes", [])
    assert outcomes, "expected at least one outcome in run report"
    matching = [o for o in outcomes if o.get("ticker") == "NODATA"]
    assert matching, "expected outcome for NODATA ticker"
    err = matching[0].get("error") or ""
    # Empty fetch with default monkeypatch returns ('', empty); the
    # process_onepass_tickers SKIPPED_NO_DATA branch builds its own
    # rejection diagnostic when the caller did not supply one.
    # Either path populates 'error' with a structured ONEPASS string.
    assert err and "[ONEPASS:" in err, (
        f"expected ONEPASS-tagged error string in run report, got {err!r}"
    )
