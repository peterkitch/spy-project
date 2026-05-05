"""
Phase 4A regression tests for ``cross_ticker_confluence``.

Synthetic universe of nine series (AAA-III, plus a duplicate stale
fixture) drives every locked behavioral rule from the Phase 4 Scoping
document into a deterministic outcome. No live data, no network, no
producer paths invoked — verified loaders only.

Test cases:

  * AAA: full daily + all non-daily + StackBuilder run -> scored_full,
    rank_group ``full_unanimity_*``.
  * BBB: daily only, some non-daily missing -> scored_partial.
  * CCC: non-daily exists but no daily source -> skipped_no_daily_source.
  * DDD: nothing loads -> skipped_no_signal_libraries.
  * EEE: manifest mismatch -> manifest_failed under both modes.
  * FFF: legacy manifest (no sidecar) -> default loaded_legacy +
    legacy_manifest_used; strict rejected.
  * GGG: scored ticker but no StackBuilder run -> ranks; coverage
    flags missing_stackbuilder_run.
  * HHH: invalid universe row.
  * III: stale daily source -> stale component status + stale
    issue_code; not used for rankings.
"""

from __future__ import annotations

import json
import os
import pickle
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import cross_ticker_confluence as ctc
import provenance_manifest as pm


# ---------------------------------------------------------------------------
# Local fixture helpers
# ---------------------------------------------------------------------------


_SIG_INT = {"Buy": 1, "Short": -1, "None": 0}


def _make_signal_library_dict(
    *,
    ticker: str,
    interval: str,
    n_bars: int = 30,
    end_date: str = "2024-12-30",
    pattern: Sequence[str] = ("Buy",),
    engine_version: str = "1.0.0",
) -> dict:
    """Tiny synthetic signal-library dict in the OnePass / multi-timeframe
    shape consumed by ``cross_ticker_confluence._signal_at_run_date``.
    """
    if interval == "1d":
        dates = pd.bdate_range(end=end_date, periods=n_bars, freq="B")
    else:
        # Use business days for all intervals; the engine only consumes
        # the trailing bar's signal so freq is a presentation detail.
        dates = pd.bdate_range(end=end_date, periods=n_bars, freq="B")
    sigs = [pattern[i % len(pattern)] for i in range(n_bars)]
    sigs_int8 = np.array([_SIG_INT[s] for s in sigs], dtype=np.int8)
    lib = {
        "engine_version": engine_version,
        "ticker": ticker,
        "interval": interval,
        "dates": list(pd.DatetimeIndex(dates)),
        "date_index": list(pd.DatetimeIndex(dates)),
        "primary_signals": list(sigs),
        "signals": list(sigs),
        "primary_signals_int8": sigs_int8,
        "num_days": n_bars,
        "build_timestamp": pd.Timestamp.utcnow().isoformat(),
        "end_date": str(dates[-1].date()),
    }
    return lib


def _write_signal_library(
    library_dir: Path, ticker: str, interval: str,
    *, signals: Sequence[str], with_manifest: bool = True,
    tamper_after_manifest: bool = False,
    age_days: float = 0.0,
    engine_version: str = "1.0.0",
) -> Path:
    library_dir.mkdir(parents=True, exist_ok=True)
    ver_tag = engine_version.replace(".", "_")
    if interval == "1d":
        fname = f"{ticker}_stable_v{ver_tag}.pkl"
    else:
        fname = f"{ticker}_stable_v{ver_tag}_{interval}.pkl"
    path = library_dir / fname

    lib = _make_signal_library_dict(
        ticker=ticker, interval=interval,
        pattern=signals, engine_version=engine_version,
    )
    if with_manifest:
        # attach_manifest writes the sidecar atomically.
        pm.attach_manifest(
            lib, path,
            artifact_type="signal_library",
            ticker=ticker,
            interval=interval,
            engine_version=engine_version,
        )
    with open(path, "wb") as fh:
        pickle.dump(lib, fh)
    if tamper_after_manifest:
        # Mutate the on-disk pickle so its content_hash no longer matches
        # the embedded/sidecar manifest. We flip one signal to a different
        # value so the canonical-blob bytes definitely change (a pattern
        # like all-Buy is invariant under simple reversal).
        tampered = dict(lib)
        flipped = list(lib["primary_signals"])
        flipped[0] = "Short" if flipped[0] != "Short" else "None"
        tampered["primary_signals"] = flipped
        tampered["signals"] = list(flipped)
        tampered["primary_signals_int8"] = np.array(
            [_SIG_INT[s] for s in tampered["primary_signals"]],
            dtype=np.int8,
        )
        with open(path, "wb") as fh:
            pickle.dump(tampered, fh)
    if age_days > 0:
        past = time.time() - (age_days * 86400.0)
        os.utime(path, (past, past))
        sidecar = path.with_name(path.name + ".manifest.json")
        if sidecar.exists():
            os.utime(sidecar, (past, past))
    return path


def _write_stackbuilder_run(
    runs_dir: Path, ticker: str, *, run_id: str = "TEST_RUN",
    selected_stack: Sequence[str] = ("SPY", "QQQ"),
    age_days: float = 0.0,
    incomplete: bool = False,
    malformed: bool = False,
    drop_field: Optional[str] = None,
) -> Path:
    """Write a real-style StackBuilder ``run_manifest.json`` (NO Phase 3
    sidecar — real StackBuilder runs never write one; the manifest is
    self-describing).

    The fixture mirrors the field set that ``stackbuilder.py`` populates
    at the end of ``run_for_secondary`` (secondary, started_at, params,
    finished_at, elapsed_seconds, outputs, plus the Phase 3B-2A
    enrichment fields). ``drop_field`` lets tests intentionally violate
    the schema; ``malformed`` writes invalid JSON; ``incomplete`` writes
    ``status='running'`` so the engine treats the manifest as
    schema_failed.
    """
    rd = runs_dir / ticker / run_id
    rd.mkdir(parents=True, exist_ok=True)
    rm_path = rd / "run_manifest.json"
    if malformed:
        rm_path.write_text("{ this is not valid json", encoding="utf-8")
        if age_days > 0:
            past = time.time() - (age_days * 86400.0)
            os.utime(rm_path, (past, past))
        return rm_path
    payload = {
        "schema_version": pm.MANIFEST_SCHEMA_VERSION,
        "artifact_kind": "output",
        "artifact_type": "stackbuilder_run",
        "producer_engine": "stackbuilder",
        "engine_version": "1.0.0",
        "run_id": run_id,
        "secondary": ticker,
        "status": "running" if incomplete else "complete",
        "started_at": "2024-12-30T00:00:00",
        "finished_at": "2024-12-30T00:00:30",
        "elapsed_seconds": 30.0,
        "params": {
            "alpha": 1.0,
            "max_k": 4,
            "top_n": 5,
            "bottom_n": 0,
        },
        "outputs": {
            "rank_all": "rank_all.csv",
            "leaderboard": "combo_leaderboard.csv",
        },
        "final_members": list(selected_stack),
        "output_artifacts": [],
    }
    if drop_field is not None:
        payload.pop(drop_field, None)
    with open(rm_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    if age_days > 0:
        past = time.time() - (age_days * 86400.0)
        os.utime(rm_path, (past, past))
    return rm_path


def _write_gtl_master(path: Path, symbols: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(symbols) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixture: a fully-populated synthetic environment
# ---------------------------------------------------------------------------


@pytest.fixture
def synthetic_env(tmp_path: Path):
    """Produce nine series across the nine locked behavioral cases."""
    sig_dir = tmp_path / "signal_library"
    spy_dir = tmp_path / "spymaster_cache"
    sb_dir = tmp_path / "stackbuilder_runs"
    out_dir = tmp_path / "output"
    gtl_path = tmp_path / "master_tickers.txt"

    intervals = ("1d", "1wk", "1mo", "3mo", "1y")
    BUY_ALL = ("Buy",)

    # AAA: all intervals + StackBuilder run.
    for iv in intervals:
        _write_signal_library(sig_dir, "AAA", iv, signals=BUY_ALL)
    _write_stackbuilder_run(sb_dir, "AAA")

    # BBB: daily + 1wk + 3mo + 1y; 1mo missing.
    for iv in ("1d", "1wk", "3mo", "1y"):
        _write_signal_library(sig_dir, "BBB", iv, signals=BUY_ALL)

    # CCC: non-daily only (no 1d).
    for iv in ("1wk", "1mo"):
        _write_signal_library(sig_dir, "CCC", iv, signals=BUY_ALL)

    # DDD: nothing.

    # EEE: tampered daily so manifest content_hash mismatches.
    _write_signal_library(
        sig_dir, "EEE", "1d", signals=BUY_ALL,
        tamper_after_manifest=True,
    )

    # FFF: legacy daily (no manifest, no sidecar).
    _write_signal_library(
        sig_dir, "FFF", "1d", signals=BUY_ALL,
        with_manifest=False,
    )

    # GGG: scored ticker, no StackBuilder run.
    for iv in intervals:
        _write_signal_library(sig_dir, "GGG", iv, signals=BUY_ALL)

    # HHH: invalid universe symbol entry — handled at universe parse.

    # III: stale daily (mtime well past max_input_age_days default 45d).
    _write_signal_library(
        sig_dir, "III", "1d", signals=BUY_ALL, age_days=60.0,
    )

    # GTL master file lists every series we've staged plus an invalid one.
    _write_gtl_master(
        gtl_path,
        ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH/INVALID*", "III"],
    )

    cfg_kwargs = dict(
        universe_mode="gtl-active",
        tickers=(),
        intervals=intervals,
        output_dir=out_dir,
        strict_manifests=False,
        max_workers=1,
        history_days=365,
        max_input_age_days=45,
        signal_library_dir=sig_dir,
        spymaster_cache_dir=spy_dir,
        stackbuilder_runs_dir=sb_dir,
        gtl_master_file=gtl_path,
        run_date="2024-12-30",
    )
    return {
        "tmp_path": tmp_path,
        "cfg_kwargs": cfg_kwargs,
        "out_dir": out_dir,
        "sig_dir": sig_dir,
        "sb_dir": sb_dir,
        "gtl_path": gtl_path,
    }


def _by_id(records: List[Mapping]) -> Dict[str, Mapping]:
    return {r["series_id"]: r for r in records}


# ---------------------------------------------------------------------------
# End-to-end run + per-case assertions
# ---------------------------------------------------------------------------


def test_full_run_default_mode(synthetic_env, monkeypatch):
    cfg = ctc.RunConfig(**synthetic_env["cfg_kwargs"])
    monkeypatch.setattr(
        "cross_ticker_confluence._spymaster_signal_history", lambda *a, **k: [],
    )
    result = ctc.run_cross_ticker_confluence(cfg)
    run_dir = ctc.write_run_outputs(result, cfg.output_dir)
    assert run_dir.exists()
    coverage = json.loads((run_dir / "coverage.json").read_text("utf-8"))
    rankings = json.loads((run_dir / "rankings.json").read_text("utf-8"))
    overlay = json.loads((run_dir / "overlay.json").read_text("utf-8"))

    cov_by = _by_id(coverage["records"])
    rank_ids = {r["series_id"] for r in rankings["records"]}

    # AAA: scored_full, full unanimity buy, in rankings.
    assert cov_by["AAA"]["top_level_status"] == ctc.TLS_SCORED_FULL
    assert "AAA" in rank_ids
    aaa_rank = next(r for r in rankings["records"] if r["series_id"] == "AAA")
    assert aaa_rank["rank_group"] == "full_unanimity_buy"
    assert aaa_rank["signal_direction"] == "Buy"
    assert ctc.IC_MISSING_STACKBUILDER_RUN not in cov_by["AAA"]["issue_codes"]

    # BBB: scored_partial; 1mo missing.
    assert cov_by["BBB"]["top_level_status"] == ctc.TLS_SCORED_PARTIAL
    assert "BBB" in rank_ids
    assert cov_by["BBB"]["per_interval_status"]["1mo"]["status"] == ctc.CS_MISSING

    # CCC: skipped_no_daily_source.
    assert cov_by["CCC"]["top_level_status"] == ctc.TLS_SKIPPED_NO_DAILY
    assert "CCC" not in rank_ids

    # DDD: nothing loads.
    assert cov_by["DDD"]["top_level_status"] == ctc.TLS_SKIPPED_NO_LIBS
    assert "DDD" not in rank_ids

    # EEE: manifest mismatch -> manifest_failed; not used as daily.
    assert ctc.IC_MANIFEST_FAILED in cov_by["EEE"]["issue_codes"]
    eee_daily = cov_by["EEE"]["per_interval_status"]["1d"]["status"]
    assert eee_daily == ctc.CS_MANIFEST_FAILED
    assert "EEE" not in rank_ids  # default mode rejects mismatched manifest

    # FFF: legacy manifest used in default mode -> appears in rankings.
    assert ctc.IC_LEGACY_MANIFEST_USED in cov_by["FFF"]["issue_codes"]
    assert cov_by["FFF"]["top_level_status"] in (
        ctc.TLS_SCORED_FULL, ctc.TLS_SCORED_PARTIAL,
    )
    assert "FFF" in rank_ids

    # GGG: scored but no stackbuilder run -> issue_code missing_stackbuilder_run.
    assert "GGG" in rank_ids
    assert ctc.IC_MISSING_STACKBUILDER_RUN in cov_by["GGG"]["issue_codes"]
    ggg_rank = next(r for r in rankings["records"] if r["series_id"] == "GGG")
    assert ggg_rank["stackbuilder"]["status"] == ctc.CS_MISSING

    # HHH: invalid_universe_symbol.
    hhh = next(
        r for r in coverage["records"]
        if r["series_id"].upper().startswith("HHH")
    )
    assert hhh["top_level_status"] == ctc.TLS_INVALID_SYMBOL
    assert hhh["eligible_for_rankings"] is False
    assert hhh["series_id"] not in rank_ids

    # III: stale daily -> stale component + issue_code; not in rankings.
    assert ctc.IC_STALE in cov_by["III"]["issue_codes"]
    assert cov_by["III"]["per_source_status"]["onepass_daily"]["status"] == ctc.CS_STALE
    assert "III" not in rank_ids

    # Coverage record per universe ticker exactly once.
    assert len(coverage["records"]) == coverage["records"].__len__()
    series_ids = [r["series_id"] for r in coverage["records"]]
    assert len(series_ids) == len(set(series_ids))
    # Every requested universe ticker is represented.
    snapshot = json.loads((run_dir / "universe_snapshot.json").read_text("utf-8"))
    assert {s["series_id"] for s in snapshot["series"]} == set(series_ids)

    # rankings count == sum of scored_full + scored_partial-with-usable-daily.
    expected_ranked_ids = {
        rec["series_id"] for rec in coverage["records"]
        if rec["top_level_status"] in (
            ctc.TLS_SCORED_FULL, ctc.TLS_SCORED_PARTIAL,
        )
    }
    assert rank_ids == expected_ranked_ids

    # Top-level status mutually exclusive (each value is exactly one of
    # the locked enum, never multi-valued).
    for rec in coverage["records"]:
        assert rec["top_level_status"] in (
            ctc.TLS_SCORED_FULL, ctc.TLS_SCORED_PARTIAL,
            ctc.TLS_SKIPPED_NO_DAILY, ctc.TLS_SKIPPED_NO_LIBS,
            ctc.TLS_INVALID_SYMBOL,
        )

    # issue_codes additive, all from the locked allowed set.
    allowed = {
        ctc.IC_MISSING_STACKBUILDER_RUN, ctc.IC_MANIFEST_FAILED,
        ctc.IC_STALE, ctc.IC_SCHEMA_FAILED,
        ctc.IC_PRODUCER_OUTPUT_MISSING, ctc.IC_LEGACY_MANIFEST_USED,
    }
    for rec in coverage["records"]:
        for ic in rec["issue_codes"]:
            assert ic in allowed

    # Overlay only for ranked series.
    overlay_ids = {r["series_id"] for r in overlay["records"]}
    assert overlay_ids == rank_ids


def test_strict_mode_rejects_legacy_and_mismatched(synthetic_env):
    kwargs = dict(synthetic_env["cfg_kwargs"])
    kwargs["strict_manifests"] = True
    cfg = ctc.RunConfig(**kwargs)
    result = ctc.run_cross_ticker_confluence(cfg)
    cov_by = _by_id([r.coverage_record for r in result.series_results])
    # FFF's legacy manifest is rejected under strict; daily becomes
    # manifest_failed and the ticker drops out of rankings.
    assert cov_by["FFF"]["per_source_status"]["onepass_daily"]["status"] == ctc.CS_MANIFEST_FAILED
    assert cov_by["FFF"]["top_level_status"] in (
        ctc.TLS_SCORED_PARTIAL, ctc.TLS_SKIPPED_NO_LIBS,
    )
    # If FFF has no other usable interval, it falls to skipped_no_libs;
    # if it has any usable non-daily that should bump to skipped_no_daily.
    # Here FFF only has a 1d library, so it falls to skipped_no_libs.
    assert cov_by["FFF"]["top_level_status"] == ctc.TLS_SKIPPED_NO_LIBS

    # EEE remains rejected under strict (mismatched manifests are always
    # rejected even in default mode).
    assert ctc.IC_MANIFEST_FAILED in cov_by["EEE"]["issue_codes"]


def test_source_precedence_onepass_before_spymaster(tmp_path: Path):
    """Spymaster fallback is only used when verified OnePass daily is
    absent or unusable. With OnePass available, spymaster_fallback gets
    not_applicable."""
    sig_dir = tmp_path / "sl"
    spy_dir = tmp_path / "spy"
    sb_dir = tmp_path / "sb"
    out_dir = tmp_path / "out"
    gtl_path = tmp_path / "gtl.txt"
    spy_dir.mkdir(parents=True, exist_ok=True)
    # Place a Spymaster fallback PKL too — should NOT be selected.
    spy_pkl = spy_dir / "AAA_precomputed_results.pkl"
    df = pd.DataFrame(
        {"Close": np.linspace(100, 110, 10)},
        index=pd.bdate_range(end="2024-12-30", periods=10),
    )
    spy_payload = {
        "preprocessed_data": df,
        "active_pairs": ["Buy"] * 10,
        "daily_top_buy_pairs": {},
        "daily_top_short_pairs": {},
        "max_sma_day": 114,
        "engine_version": "1.0.0",
    }
    pm.attach_manifest(
        spy_payload, spy_pkl,
        artifact_type="spymaster_pkl", ticker="AAA",
        engine_version="1.0.0",
    )
    with open(spy_pkl, "wb") as fh:
        pickle.dump(spy_payload, fh)

    _write_signal_library(sig_dir, "AAA", "1d", signals=("Buy",))
    _write_gtl_master(gtl_path, ["AAA"])
    cfg = ctc.RunConfig(
        universe_mode="gtl-active",
        intervals=("1d",),
        output_dir=out_dir,
        signal_library_dir=sig_dir,
        spymaster_cache_dir=spy_dir,
        stackbuilder_runs_dir=sb_dir,
        gtl_master_file=gtl_path,
        max_workers=1,
        run_date="2024-12-30",
    )
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["onepass_daily"]["status"] == ctc.CS_LOADED_VERIFIED
    assert rec["per_source_status"]["spymaster_fallback"]["status"] == ctc.CS_NOT_APPLICABLE
    assert rec["per_interval_status"]["1d"]["source"] == ctc.SRC_ONEPASS_DAILY


def test_spymaster_used_when_onepass_missing(tmp_path: Path):
    """When OnePass daily is absent, Spymaster fallback fills in."""
    sig_dir = tmp_path / "sl"
    spy_dir = tmp_path / "spy"
    sb_dir = tmp_path / "sb"
    out_dir = tmp_path / "out"
    gtl_path = tmp_path / "gtl.txt"
    sig_dir.mkdir(parents=True, exist_ok=True)
    spy_dir.mkdir(parents=True, exist_ok=True)
    spy_pkl = spy_dir / "BBB_precomputed_results.pkl"
    df = pd.DataFrame(
        {"Close": np.linspace(100, 110, 5)},
        index=pd.bdate_range(end="2024-12-30", periods=5),
    )
    spy_payload = {
        "preprocessed_data": df,
        "active_pairs": ["Short"] * 5,
        "daily_top_buy_pairs": {},
        "daily_top_short_pairs": {},
        "max_sma_day": 114,
        "engine_version": "1.0.0",
    }
    pm.attach_manifest(
        spy_payload, spy_pkl,
        artifact_type="spymaster_pkl", ticker="BBB",
        engine_version="1.0.0",
    )
    with open(spy_pkl, "wb") as fh:
        pickle.dump(spy_payload, fh)
    _write_gtl_master(gtl_path, ["BBB"])
    cfg = ctc.RunConfig(
        universe_mode="gtl-active",
        intervals=("1d",),
        output_dir=out_dir,
        signal_library_dir=sig_dir,
        spymaster_cache_dir=spy_dir,
        stackbuilder_runs_dir=sb_dir,
        gtl_master_file=gtl_path,
        max_workers=1,
        run_date="2024-12-30",
    )
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["onepass_daily"]["status"] == ctc.CS_MISSING
    assert rec["per_source_status"]["spymaster_fallback"]["status"] == ctc.CS_LOADED_VERIFIED
    assert rec["per_interval_status"]["1d"]["source"] == ctc.SRC_SPYMASTER_FALLBACK
    assert rec["per_interval_status"]["1d"]["signal"] == "Short"


# ---------------------------------------------------------------------------
# Run-level fatal: empty universe
# ---------------------------------------------------------------------------


def test_empty_gtl_is_fatal(tmp_path: Path):
    sig_dir = tmp_path / "sl"
    spy_dir = tmp_path / "spy"
    sb_dir = tmp_path / "sb"
    gtl_path = tmp_path / "gtl_empty.txt"
    gtl_path.write_text("", encoding="utf-8")
    out_dir = tmp_path / "out"
    cfg = ctc.RunConfig(
        universe_mode="gtl-active",
        output_dir=out_dir,
        signal_library_dir=sig_dir,
        spymaster_cache_dir=spy_dir,
        stackbuilder_runs_dir=sb_dir,
        gtl_master_file=gtl_path,
        max_workers=1,
    )
    with pytest.raises(RuntimeError):
        ctc.run_cross_ticker_confluence(cfg)
    # No partial run dir is written.
    assert not (out_dir / "cross_ticker_confluence").exists()


# ---------------------------------------------------------------------------
# Manifest verification of output JSON artifacts
# ---------------------------------------------------------------------------


def test_json_artifacts_verify_through_loader(synthetic_env):
    cfg = ctc.RunConfig(**synthetic_env["cfg_kwargs"])
    result = ctc.run_cross_ticker_confluence(cfg)
    run_dir = ctc.write_run_outputs(result, cfg.output_dir)
    for name in ("coverage.json", "rankings.json", "overlay.json",
                 "universe_snapshot.json"):
        path = run_dir / name
        assert path.exists()
        data, vresult = pm.load_verified_json_artifact(path)
        assert data is not None, f"{name} failed to load"
        assert vresult.ok, (
            f"{name} did not verify cleanly: "
            f"mismatches={vresult.mismatches!r}"
        )
        # It must NOT come back legacy: we always write a sidecar.
        assert not vresult.legacy, f"{name} unexpectedly legacy"


# ---------------------------------------------------------------------------
# Parallelism equivalence — TIGHT canonical mask + broader envelope mask
# ---------------------------------------------------------------------------


# Tight canonical mask: only run_id and timestamp/datetime fields.
# Per ITEM 5 of the Codex audit, the canonical JSON payloads
# (coverage.json, rankings.json, overlay.json, universe_snapshot.json)
# must be byte-identical between --max-workers 1 and --max-workers 4
# after stripping ONLY these fields. If the tight mask reveals real
# non-determinism (dict ordering, parallel-write race, time-derived
# numbers in payload), fix the engine, not the mask.
_TIGHT_CANONICAL_MASK = {
    "run_id",
    "resolved_at",
    "build_timestamp",
    "started_at",
    "finished_at",
}


# Broader envelope mask for run_manifest.json: timestamps, file hashes,
# size_bytes, runtime/git fingerprints, and output_artifacts entries
# (whose file_sha256 / size_bytes / produced_at vary because the
# canonical JSON files themselves embed differing run_ids per run).
_RUN_MANIFEST_ENVELOPE_MASK = _TIGHT_CANONICAL_MASK | {
    "produced_at",
    "git_commit",
    "git_dirty",
    "package_versions",
    "host_platform",
    "builder_identity",
    "artifact_file_sha256",
    "file_sha256",
    "size_bytes",
}


def _strip_keys(obj, keys):
    if isinstance(obj, list):
        return [_strip_keys(x, keys) for x in obj]
    if isinstance(obj, dict):
        return {
            k: _strip_keys(v, keys)
            for k, v in obj.items()
            if k not in keys
        }
    return obj


def test_max_workers_1_vs_4_canonical_payloads_byte_identical(synthetic_env):
    """Canonical JSON payloads must be byte-identical between
    --max-workers 1 and --max-workers 4 under the TIGHT mask."""
    kwargs1 = dict(synthetic_env["cfg_kwargs"])
    kwargs1["max_workers"] = 1
    kwargs1["output_dir"] = synthetic_env["out_dir"] / "w1"
    kwargs4 = dict(synthetic_env["cfg_kwargs"])
    kwargs4["max_workers"] = 4
    kwargs4["output_dir"] = synthetic_env["out_dir"] / "w4"

    r1 = ctc.run_cross_ticker_confluence(ctc.RunConfig(**kwargs1))
    r4 = ctc.run_cross_ticker_confluence(ctc.RunConfig(**kwargs4))
    d1 = ctc.write_run_outputs(r1, kwargs1["output_dir"])
    d4 = ctc.write_run_outputs(r4, kwargs4["output_dir"])

    for name in ("coverage.json", "rankings.json", "overlay.json",
                 "universe_snapshot.json"):
        a = json.loads((d1 / name).read_text("utf-8"))
        b = json.loads((d4 / name).read_text("utf-8"))
        a_stripped = _strip_keys(a, _TIGHT_CANONICAL_MASK)
        b_stripped = _strip_keys(b, _TIGHT_CANONICAL_MASK)
        # Byte-identical via canonical JSON serialization.
        a_bytes = json.dumps(a_stripped, sort_keys=True).encode()
        b_bytes = json.dumps(b_stripped, sort_keys=True).encode()
        assert a_bytes == b_bytes, (
            f"{name} canonical payload differs between "
            f"max_workers=1 and =4 under the tight mask"
        )


def test_max_workers_1_vs_4_run_manifest_envelope_equivalent(synthetic_env):
    """Run-manifest envelope equivalence under the broader mask
    (file_sha256/produced_at/runtime fingerprints are legitimately
    per-write)."""
    kwargs1 = dict(synthetic_env["cfg_kwargs"])
    kwargs1["max_workers"] = 1
    kwargs1["output_dir"] = synthetic_env["out_dir"] / "w1"
    kwargs4 = dict(synthetic_env["cfg_kwargs"])
    kwargs4["max_workers"] = 4
    kwargs4["output_dir"] = synthetic_env["out_dir"] / "w4"

    r1 = ctc.run_cross_ticker_confluence(ctc.RunConfig(**kwargs1))
    r4 = ctc.run_cross_ticker_confluence(ctc.RunConfig(**kwargs4))
    d1 = ctc.write_run_outputs(r1, kwargs1["output_dir"])
    d4 = ctc.write_run_outputs(r4, kwargs4["output_dir"])

    rm1 = json.loads((d1 / "run_manifest.json").read_text("utf-8"))
    rm4 = json.loads((d4 / "run_manifest.json").read_text("utf-8"))
    rm1["params"].pop("max_workers", None)
    rm4["params"].pop("max_workers", None)
    assert _strip_keys(rm1, _RUN_MANIFEST_ENVELOPE_MASK) == \
        _strip_keys(rm4, _RUN_MANIFEST_ENVELOPE_MASK)


# ---------------------------------------------------------------------------
# No producer paths invoked during aggregation
# ---------------------------------------------------------------------------


def test_no_producer_paths_invoked(synthetic_env, monkeypatch):
    """Sanity guard: monkeypatch every producer entry-point we know about
    to raise; the engine must run to completion without hitting them.
    """
    import importlib
    forbidden_attrs = []
    for module_name, attr in (
        ("trafficflow", "_processed_signals_from_pkl"),
        ("trafficflow", "_next_signal_from_pkl"),
        ("onepass", "compute_signals"),
        ("stackbuilder", "phase2_rank_all"),
        ("stackbuilder", "phase3_build_stacks"),
        ("stackbuilder", "run_for_secondary"),
        ("spymaster", "process_ticker_data"),
    ):
        try:
            mod = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(mod, attr):
            forbidden_attrs.append((mod, attr))

    def _boom(*a, **k):
        raise AssertionError(
            "cross_ticker_confluence must not invoke producer paths"
        )

    for mod, attr in forbidden_attrs:
        monkeypatch.setattr(mod, attr, _boom)

    # yfinance: also forbidden.
    try:
        import yfinance  # type: ignore
        monkeypatch.setattr(yfinance, "download", _boom, raising=False)
        monkeypatch.setattr(yfinance, "Ticker", _boom, raising=False)
    except ImportError:
        pass

    cfg = ctc.RunConfig(**synthetic_env["cfg_kwargs"])
    result = ctc.run_cross_ticker_confluence(cfg)
    run_dir = ctc.write_run_outputs(result, cfg.output_dir)
    assert run_dir.exists()


# ---------------------------------------------------------------------------
# Run manifest provenance fields
# ---------------------------------------------------------------------------


def test_run_manifest_carries_provenance(synthetic_env):
    cfg = ctc.RunConfig(**synthetic_env["cfg_kwargs"])
    result = ctc.run_cross_ticker_confluence(cfg)
    run_dir = ctc.write_run_outputs(result, cfg.output_dir)
    rm = json.loads((run_dir / "run_manifest.json").read_text("utf-8"))

    # Required fields per Phase 4 scoping acceptance criteria.
    assert rm["schema_version"] == 1
    assert rm["artifact_type"] == ctc.ARTIFACT_TYPE_RUN
    assert rm["producer_engine"] == ctc.ENGINE_NAME
    assert rm["engine_version"] == ctc.ENGINE_VERSION
    assert rm["run_id"] == result.run_id
    assert rm["run_date"] == result.run_date
    assert rm["status"] == "complete"
    params = rm["params"]
    for k in ("universe_mode", "intervals", "history_days",
              "max_input_age_days", "strict_manifests", "max_workers"):
        assert k in params
    assert rm["universe"]["universe_hash"] == result.universe.universe_hash
    assert rm["universe"]["snapshot_path"] == "universe_snapshot.json"
    cov = rm["coverage_counts"]
    assert set(cov.keys()) == {
        ctc.TLS_SCORED_FULL, ctc.TLS_SCORED_PARTIAL,
        ctc.TLS_SKIPPED_NO_DAILY, ctc.TLS_SKIPPED_NO_LIBS,
        ctc.TLS_INVALID_SYMBOL,
    }
    assert isinstance(rm["issue_counts"], dict)
    assert isinstance(rm["input_artifacts"], list)
    assert isinstance(rm["input_manifest_hashes"], list)
    assert isinstance(rm["output_artifacts"], list)
    # Output artifacts include the four JSON canonicals + 2 derived CSVs.
    names = {a["name"] for a in rm["output_artifacts"]}
    assert {
        "coverage", "rankings", "overlay", "universe_snapshot",
        "coverage_csv", "rankings_csv",
    } <= names
    # Derived CSVs reference their canonical JSON.
    for art in rm["output_artifacts"]:
        if art["format"] == "csv":
            assert "derived_from" in art


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_main_exit_code_on_empty_gtl(tmp_path: Path):
    gtl = tmp_path / "empty_gtl.txt"
    gtl.write_text("", encoding="utf-8")
    out = tmp_path / "out"
    rc = ctc.main([
        "--universe-mode", "gtl-active",
        "--gtl-master-file", str(gtl),
        "--output-dir", str(out),
        "--signal-library-dir", str(tmp_path / "sl"),
        "--spymaster-cache-dir", str(tmp_path / "spy"),
        "--stackbuilder-runs-dir", str(tmp_path / "sb"),
    ])
    assert rc == 2


# ---------------------------------------------------------------------------
# ITEM 1: GTL master parser — comma + whitespace + newline mixes
# ---------------------------------------------------------------------------


def _build_minimal_cfg(
    tmp_path: Path, *, intervals=("1d",), universe_mode="gtl-active",
) -> Dict[str, Any]:
    """Common kwargs builder for parser-and-policy unit tests."""
    return dict(
        universe_mode=universe_mode,
        tickers=(),
        intervals=intervals,
        output_dir=tmp_path / "out",
        strict_manifests=False,
        max_workers=1,
        history_days=365,
        max_input_age_days=45,
        signal_library_dir=tmp_path / "sl",
        spymaster_cache_dir=tmp_path / "spy",
        stackbuilder_runs_dir=tmp_path / "sb",
        gtl_master_file=tmp_path / "gtl.txt",
        run_date="2024-12-30",
    )


def test_gtl_parser_comma_separated(tmp_path: Path):
    """GTL exports are comma-separated; the parser must not collapse a
    file like 'AAA,BBB,CCC' into a single invalid token."""
    cfg_kw = _build_minimal_cfg(tmp_path)
    cfg_kw["gtl_master_file"].write_text("AAA,BBB,CCC", encoding="utf-8")
    cfg = ctc.RunConfig(**cfg_kw)
    snap = ctc.resolve_universe(cfg)
    assert [e.normalized_symbol for e in snap.series] == ["AAA", "BBB", "CCC"]
    assert snap.counts["valid"] == 3
    assert snap.counts["invalid"] == 0


def test_gtl_parser_mixed_separators(tmp_path: Path):
    """Mixed comma + whitespace + newline format must yield five
    symbols, lex-sorted under gtl-active."""
    cfg_kw = _build_minimal_cfg(tmp_path)
    cfg_kw["gtl_master_file"].write_text(
        "AAA, BBB,CCC\nDDD EEE\n", encoding="utf-8",
    )
    cfg = ctc.RunConfig(**cfg_kw)
    snap = ctc.resolve_universe(cfg)
    syms = [e.normalized_symbol for e in snap.series]
    assert syms == ["AAA", "BBB", "CCC", "DDD", "EEE"]


def test_gtl_parser_pure_newline_still_works(tmp_path: Path):
    """Backwards compatibility: a pure newline-separated file still
    parses correctly under the new regex."""
    cfg_kw = _build_minimal_cfg(tmp_path)
    cfg_kw["gtl_master_file"].write_text(
        "ZZZ\nYYY\nXXX\n", encoding="utf-8",
    )
    cfg = ctc.RunConfig(**cfg_kw)
    snap = ctc.resolve_universe(cfg)
    syms = [e.normalized_symbol for e in snap.series]
    assert syms == ["XXX", "YYY", "ZZZ"]  # lex-sorted under gtl-active


# ---------------------------------------------------------------------------
# ITEM 2: future-bar regression — no post-run_date leakage
# ---------------------------------------------------------------------------


def _write_signal_library_with_dates(
    library_dir: Path, ticker: str, interval: str,
    *,
    dates: Sequence[pd.Timestamp],
    signals: Sequence[str],
    engine_version: str = "1.0.0",
) -> Path:
    """Write a signal library with explicit (date, signal) pairs and a
    full manifest. Used to construct fixtures whose signal series
    extends past run_date.
    """
    library_dir.mkdir(parents=True, exist_ok=True)
    ver_tag = engine_version.replace(".", "_")
    if interval == "1d":
        fname = f"{ticker}_stable_v{ver_tag}.pkl"
    else:
        fname = f"{ticker}_stable_v{ver_tag}_{interval}.pkl"
    path = library_dir / fname
    sig_int8 = np.array([_SIG_INT[s] for s in signals], dtype=np.int8)
    lib = {
        "engine_version": engine_version,
        "ticker": ticker,
        "interval": interval,
        "dates": list(pd.DatetimeIndex(dates)),
        "date_index": list(pd.DatetimeIndex(dates)),
        "primary_signals": list(signals),
        "signals": list(signals),
        "primary_signals_int8": sig_int8,
        "num_days": len(dates),
        "build_timestamp": pd.Timestamp.utcnow().isoformat(),
        "end_date": str(dates[-1].date()),
    }
    pm.attach_manifest(
        lib, path,
        artifact_type="signal_library",
        ticker=ticker,
        interval=interval,
        engine_version=engine_version,
    )
    with open(path, "wb") as fh:
        pickle.dump(lib, fh)
    return path


def test_no_future_period_signal_daily_and_non_daily(tmp_path: Path):
    """Artifacts whose signal series extend past run_date must NOT leak
    post-run_date data into the engine output, on both daily and
    non-daily intervals.
    """
    sig_dir = tmp_path / "sl"
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d", "1wk"), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("AAA",)
    cfg_kw["run_date"] = "2024-06-15"
    cfg_kw["history_days"] = 30
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    # Daily series spans 2024-06-01..2024-06-30. run_date=2024-06-15.
    # Pre-cutoff signals are all "Buy"; post-cutoff signals flip to
    # "Short" so any leak shows up as a wrong signal at run_date.
    daily_dates = list(pd.date_range("2024-06-01", "2024-06-30", freq="D"))
    daily_sigs = [
        "Buy" if d.strftime("%Y-%m-%d") <= "2024-06-15" else "Short"
        for d in daily_dates
    ]
    _write_signal_library_with_dates(
        sig_dir, "AAA", "1d",
        dates=daily_dates, signals=daily_sigs,
    )
    # Weekly series spans 2024-05-06..2024-07-29. run_date falls in
    # 2024-06-15. Post-cutoff weeks flip.
    weekly_dates = list(pd.date_range("2024-05-06", "2024-07-29", freq="7D"))
    weekly_sigs = [
        "Buy" if d.strftime("%Y-%m-%d") <= "2024-06-15" else "Short"
        for d in weekly_dates
    ]
    _write_signal_library_with_dates(
        sig_dir, "AAA", "1wk",
        dates=weekly_dates, signals=weekly_sigs,
    )

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    rank = result.series_results[0].ranking_record
    overlay = result.series_results[0].overlay_record

    # Per-interval signals at run_date must reflect the pre-cutoff
    # value ("Buy"), never the post-cutoff "Short".
    assert rec["per_interval_status"]["1d"]["signal"] == "Buy"
    assert rec["per_interval_status"]["1wk"]["signal"] == "Buy"
    assert rank is not None
    assert rank["interval_signals"]["1d"] == "Buy"
    assert rank["interval_signals"]["1wk"] == "Buy"
    assert rank["run_date_signal"] == "Buy"

    # Overlay records must not contain any post-run_date dates.
    for entry in overlay["intervals"]["1d"]:
        assert entry["date"] <= "2024-06-15"
    for entry in overlay["intervals"]["1wk"]:
        assert entry["date"] <= "2024-06-15"
    # And they must contain at least one entry within the window.
    assert overlay["intervals"]["1d"], (
        "daily overlay should contain at least one in-window entry"
    )


def test_no_signal_at_run_date_keeps_load_status(tmp_path: Path):
    """If an artifact loads cleanly but has only post-run_date signals,
    the component status stays loaded_verified, the interval signal is
    null, and the ticker drops out of rankings."""
    sig_dir = tmp_path / "sl"
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("BBB",)
    cfg_kw["run_date"] = "2024-06-15"
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    # All dates after run_date.
    future_dates = list(pd.date_range("2024-06-20", "2024-07-10", freq="D"))
    future_sigs = ["Buy"] * len(future_dates)
    _write_signal_library_with_dates(
        sig_dir, "BBB", "1d",
        dates=future_dates, signals=future_sigs,
    )

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    rank = result.series_results[0].ranking_record

    # The library loaded fine; the manifest verifies. But there's no
    # signal at or before run_date, so per-interval signal is None.
    assert rec["per_source_status"]["onepass_daily"]["status"] == ctc.CS_LOADED_VERIFIED
    assert rec["per_interval_status"]["1d"]["signal"] is None
    # No usable daily -> not eligible for rankings.
    assert rec["top_level_status"] == ctc.TLS_SKIPPED_NO_LIBS
    assert rank is None


def test_no_future_period_signal_via_spymaster_fallback(tmp_path: Path):
    """The same upper-bound rule applies when the daily source comes
    from the Spymaster fallback PKL."""
    sig_dir = tmp_path / "sl"
    spy_dir = tmp_path / "spy"
    spy_dir.mkdir(parents=True, exist_ok=True)
    sig_dir.mkdir(parents=True, exist_ok=True)
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("CCC",)
    cfg_kw["run_date"] = "2024-06-15"
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    dates = pd.date_range("2024-06-01", "2024-06-30", freq="D")
    pairs = [
        "Buy" if d.strftime("%Y-%m-%d") <= "2024-06-15" else "Short"
        for d in dates
    ]
    df = pd.DataFrame({"Close": np.linspace(100, 110, len(dates))}, index=dates)
    spy_payload = {
        "preprocessed_data": df,
        "active_pairs": pairs,
        "daily_top_buy_pairs": {},
        "daily_top_short_pairs": {},
        "max_sma_day": 114,
        "engine_version": "1.0.0",
    }
    spy_pkl = spy_dir / "CCC_precomputed_results.pkl"
    pm.attach_manifest(
        spy_payload, spy_pkl,
        artifact_type="spymaster_pkl", ticker="CCC",
        engine_version="1.0.0",
    )
    with open(spy_pkl, "wb") as fh:
        pickle.dump(spy_payload, fh)

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_interval_status"]["1d"]["signal"] == "Buy"
    assert rec["per_interval_status"]["1d"]["source"] == ctc.SRC_SPYMASTER_FALLBACK


# ---------------------------------------------------------------------------
# ITEM 3: StackBuilder run_manifest direct json.load
# ---------------------------------------------------------------------------


def test_stackbuilder_no_sidecar_loaded_verified(tmp_path: Path):
    """A real-style StackBuilder run directory (no Phase 3 sidecar)
    must classify as loaded_verified, and the resulting input_refs
    entry must carry a byte-level ``file_sha256`` that matches the
    on-disk bytes of the run_manifest.json.
    """
    import hashlib

    sb_dir = tmp_path / "sb"
    sig_dir = tmp_path / "sl"
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("AAA",)
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    _write_signal_library(sig_dir, "AAA", "1d", signals=("Buy",))
    rm_path = _write_stackbuilder_run(sb_dir, "AAA")
    # Sanity: no sidecar exists.
    assert not rm_path.with_name(rm_path.name + ".manifest.json").exists()

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["stackbuilder_run"]["status"] == ctc.CS_LOADED_VERIFIED
    rank = result.series_results[0].ranking_record
    assert rank is not None
    assert rank["stackbuilder"]["status"] == ctc.CS_LOADED_VERIFIED
    assert rank["stackbuilder"]["selected_stack"] == ["SPY", "QQQ"]

    # Byte-level provenance: independently compute the sha256 of the
    # fixture run_manifest.json and confirm it matches the engine's
    # input_refs entry.
    expected_sha = hashlib.sha256(rm_path.read_bytes()).hexdigest()

    # Persist the run so we can also assert it via run_manifest.json.
    run_dir = ctc.write_run_outputs(result, cfg.output_dir)
    rm = json.loads((run_dir / "run_manifest.json").read_text("utf-8"))
    sb_refs = [
        ref for ref in rm["input_artifacts"]
        if ref.get("source") == "stackbuilder_run_manifest"
    ]
    assert len(sb_refs) == 1, (
        f"expected exactly one stackbuilder_run_manifest input ref, "
        f"got {len(sb_refs)}: {sb_refs!r}"
    )
    sb_ref = sb_refs[0]
    assert "file_sha256" in sb_ref, (
        "stackbuilder_run_manifest input ref must record file_sha256"
    )
    assert isinstance(sb_ref["file_sha256"], str), (
        f"file_sha256 must be a hex string; got {type(sb_ref['file_sha256'])!r}"
    )
    assert len(sb_ref["file_sha256"]) == 64, (
        f"file_sha256 must be 64 lowercase hex chars; "
        f"got {sb_ref['file_sha256']!r}"
    )
    assert all(c in "0123456789abcdef" for c in sb_ref["file_sha256"]), (
        f"file_sha256 must be lowercase hex; got {sb_ref['file_sha256']!r}"
    )
    assert sb_ref["file_sha256"] == expected_sha, (
        f"file_sha256 mismatch: input_refs={sb_ref['file_sha256']!r} "
        f"vs sha256(rm_path bytes)={expected_sha!r}"
    )


def test_stackbuilder_malformed_json_schema_failed(tmp_path: Path):
    sb_dir = tmp_path / "sb"
    sig_dir = tmp_path / "sl"
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("AAA",)
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    _write_signal_library(sig_dir, "AAA", "1d", signals=("Buy",))
    _write_stackbuilder_run(sb_dir, "AAA", malformed=True)

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["stackbuilder_run"]["status"] == ctc.CS_SCHEMA_FAILED
    assert ctc.IC_SCHEMA_FAILED in rec["issue_codes"]


def test_stackbuilder_missing_field_schema_failed(tmp_path: Path):
    sb_dir = tmp_path / "sb"
    sig_dir = tmp_path / "sl"
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("AAA",)
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    _write_signal_library(sig_dir, "AAA", "1d", signals=("Buy",))
    # Missing 'outputs' field — should map to schema_failed.
    _write_stackbuilder_run(sb_dir, "AAA", drop_field="outputs")

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["stackbuilder_run"]["status"] == ctc.CS_SCHEMA_FAILED


def test_stackbuilder_stale_run_manifest(tmp_path: Path):
    sb_dir = tmp_path / "sb"
    sig_dir = tmp_path / "sl"
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("AAA",)
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    _write_signal_library(sig_dir, "AAA", "1d", signals=("Buy",))
    _write_stackbuilder_run(sb_dir, "AAA", age_days=60.0)

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["stackbuilder_run"]["status"] == ctc.CS_STALE
    assert ctc.IC_STALE in rec["issue_codes"]


# ---------------------------------------------------------------------------
# ITEM 4: Spymaster fallback failure statuses propagate to issue_codes
# ---------------------------------------------------------------------------


def _write_stale_spymaster_pkl(spy_dir: Path, ticker: str) -> Path:
    spy_dir.mkdir(parents=True, exist_ok=True)
    spy_pkl = spy_dir / f"{ticker}_precomputed_results.pkl"
    df = pd.DataFrame(
        {"Close": np.linspace(100, 110, 5)},
        index=pd.bdate_range(end="2024-12-30", periods=5),
    )
    payload = {
        "preprocessed_data": df,
        "active_pairs": ["Buy"] * 5,
        "daily_top_buy_pairs": {},
        "daily_top_short_pairs": {},
        "max_sma_day": 114,
        "engine_version": "1.0.0",
    }
    pm.attach_manifest(
        payload, spy_pkl,
        artifact_type="spymaster_pkl", ticker=ticker,
        engine_version="1.0.0",
    )
    with open(spy_pkl, "wb") as fh:
        pickle.dump(payload, fh)
    past = time.time() - (60.0 * 86400.0)
    os.utime(spy_pkl, (past, past))
    sidecar = spy_pkl.with_name(spy_pkl.name + ".manifest.json")
    if sidecar.exists():
        os.utime(sidecar, (past, past))
    return spy_pkl


def test_spymaster_fallback_stale_propagates_issue_code(tmp_path: Path):
    sig_dir = tmp_path / "sl"
    spy_dir = tmp_path / "spy"
    sig_dir.mkdir(parents=True, exist_ok=True)
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("DDD",)
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    _write_stale_spymaster_pkl(spy_dir, "DDD")

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["spymaster_fallback"]["status"] == ctc.CS_STALE
    assert ctc.IC_STALE in rec["issue_codes"]


def test_spymaster_fallback_manifest_failed_propagates(tmp_path: Path):
    """A spymaster PKL with a tampered embedded manifest fails verify
    and must surface as additive issue_code 'manifest_failed'."""
    sig_dir = tmp_path / "sl"
    spy_dir = tmp_path / "spy"
    sig_dir.mkdir(parents=True, exist_ok=True)
    spy_dir.mkdir(parents=True, exist_ok=True)
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("EEE",)
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    spy_pkl = spy_dir / "EEE_precomputed_results.pkl"
    df = pd.DataFrame(
        {"Close": np.linspace(100, 110, 5)},
        index=pd.bdate_range(end="2024-12-30", periods=5),
    )
    payload = {
        "preprocessed_data": df,
        "active_pairs": ["Buy"] * 5,
        "daily_top_buy_pairs": {},
        "daily_top_short_pairs": {},
        "max_sma_day": 114,
        "engine_version": "1.0.0",
    }
    pm.attach_manifest(
        payload, spy_pkl,
        artifact_type="spymaster_pkl", ticker="EEE",
        engine_version="1.0.0",
    )
    with open(spy_pkl, "wb") as fh:
        pickle.dump(payload, fh)
    # Tamper after manifest attach: change active_pairs so content_hash
    # no longer matches the embedded manifest's expected hash.
    tampered = dict(payload)
    tampered["active_pairs"] = ["Short"] * 5
    with open(spy_pkl, "wb") as fh:
        pickle.dump(tampered, fh)

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["spymaster_fallback"]["status"] == ctc.CS_MANIFEST_FAILED
    assert ctc.IC_MANIFEST_FAILED in rec["issue_codes"]


def test_spymaster_fallback_schema_failed_propagates(tmp_path: Path):
    """A spymaster PKL that fails to unpickle (corrupt bytes) must
    surface schema_failed at component AND issue_code level."""
    sig_dir = tmp_path / "sl"
    spy_dir = tmp_path / "spy"
    sig_dir.mkdir(parents=True, exist_ok=True)
    spy_dir.mkdir(parents=True, exist_ok=True)
    cfg_kw = _build_minimal_cfg(
        tmp_path, intervals=("1d",), universe_mode="tickers",
    )
    cfg_kw["tickers"] = ("FFF",)
    cfg_kw["gtl_master_file"] = tmp_path / "unused.txt"

    spy_pkl = spy_dir / "FFF_precomputed_results.pkl"
    spy_pkl.write_bytes(b"this is not a valid pickle")

    cfg = ctc.RunConfig(**cfg_kw)
    result = ctc.run_cross_ticker_confluence(cfg)
    rec = result.series_results[0].coverage_record
    assert rec["per_source_status"]["spymaster_fallback"]["status"] == ctc.CS_SCHEMA_FAILED
    assert ctc.IC_SCHEMA_FAILED in rec["issue_codes"]


# ---------------------------------------------------------------------------
# ITEM 6: duplicate-symbol policy
# ---------------------------------------------------------------------------


def test_duplicate_symbol_policy(tmp_path: Path):
    """The second occurrence of a normalized symbol within a universe
    must become invalid_universe_symbol with
    invalid_reason='duplicate_symbol'. The coverage row count equals
    the universe-input row count exactly."""
    sig_dir = tmp_path / "sl"
    sig_dir.mkdir(parents=True, exist_ok=True)
    # AAA, BBB, CCC each have a daily library so legitimate occurrences
    # score normally.
    for sym in ("AAA", "BBB", "CCC"):
        _write_signal_library(sig_dir, sym, "1d", signals=("Buy",))

    cfg = ctc.RunConfig(
        universe_mode="tickers",
        tickers=("AAA", "BBB", "AAA", "CCC"),
        intervals=("1d",),
        output_dir=tmp_path / "out",
        signal_library_dir=sig_dir,
        spymaster_cache_dir=tmp_path / "spy",
        stackbuilder_runs_dir=tmp_path / "sb",
        gtl_master_file=tmp_path / "unused.txt",
        max_workers=1,
        run_date="2024-12-30",
    )
    result = ctc.run_cross_ticker_confluence(cfg)
    snapshot = result.universe.series

    # Universe snapshot: four positions, in user-supplied order.
    assert [e.source_symbol for e in snapshot] == ["AAA", "BBB", "AAA", "CCC"]
    assert [e.valid_symbol for e in snapshot] == [True, True, False, True]
    assert snapshot[2].invalid_reason == "duplicate_symbol"
    assert snapshot[2].normalized_symbol == "AAA"

    # Coverage: one row per universe input row, exactly four rows.
    cov = [r.coverage_record for r in result.series_results]
    assert len(cov) == 4
    # The first AAA scores normally; the duplicate is invalid.
    statuses = [r["top_level_status"] for r in cov]
    assert statuses == [
        ctc.TLS_SCORED_FULL,
        ctc.TLS_SCORED_FULL,
        ctc.TLS_INVALID_SYMBOL,
        ctc.TLS_SCORED_FULL,
    ]
    assert cov[2]["invalid_reason"] == "duplicate_symbol"

    # universe_snapshot.json reflects all four positions with
    # valid_symbol flagged appropriately.
    run_dir = ctc.write_run_outputs(result, cfg.output_dir)
    snap_json = json.loads(
        (run_dir / "universe_snapshot.json").read_text("utf-8")
    )
    assert len(snap_json["series"]) == 4
    series = snap_json["series"]
    assert series[2]["valid_symbol"] is False
    assert series[2]["invalid_reason"] == "duplicate_symbol"
    assert series[2]["normalized_symbol"] == "AAA"
