"""Tests for the SPRINT 500 K=6 recook driver (project/k6_recook.py).

All tests use tmp_path and injected/fake callables. No network, no real
output/cache/signal_library roots. Fast-suite compatible.

Covers the locked behaviors:
  - Stage 0 pointer synthesis + producer round-trip.
  - Dotted-secondary routing (DX-Y.NYB) preserved (no '.'->'_').
  - Seed selection: one / unique-newest / tied / zero.
  - Pin awareness and existing pointer untouched without --restage-all.
  - [D]/[I] vs -D/-I member-token parsing (parity with producer).
  - Union dedupe.
  - Stage Aprime rebuild policy (overwrite, never clear; exclude PKL-less).
  - Offline skip gate (skip equal/ahead, never call refresher).
  - Active-stale refusal (2026-05-26 vs target 2026-06-03).
  - Dead/no-history exclusion (no invented terminal semantics).
  - Stage A/B structured result inspection.
  - Stage B no-vendor-fetch (launch-path local PKL).
  - Daily stable rule: existing 1d never overwritten; missing 1d created.
  - Barrier order A -> Aprime -> B -> E -> F -> H.
  - Single-instance lock: held refuses, stale reclaimed, dry-run no lock.
  - Envelope/stdout: exactly one JSON object, schema_version.
  - Stage H private dry-run only (no public/write/operator-approved).
  - Global refusals.
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import types
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parents[2]  # project/
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import k6_recook as drv  # noqa: E402


SIX = ["AAA[D]", "BBB[I]", "CCC[D]", "DDD[I]", "EEE[D]", "FFF[I]"]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_combo(seed_dir: Path, members=SIX) -> None:
    seed_dir.mkdir(parents=True, exist_ok=True)
    (seed_dir / "combo_k=6.json").write_text(
        json.dumps({"Members": members}), encoding="utf-8"
    )


def _make_secondary(root: Path, sec: str, seed_name="seedTC__x", members=SIX) -> Path:
    seed = root / sec / seed_name
    _make_combo(seed, members)
    return seed


def _run_main(argv):
    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = drv.main(argv)
    finally:
        sys.stdout = old
    return rc, buf.getvalue()


def _must_not_run(*a, **k):
    raise AssertionError("this stage must not run after the prior halt")


def _boom_write(target, payload):
    raise OSError("simulated pointer write failure")


# ---------------------------------------------------------------------------
# 1. Stage 0 payload + producer round-trip
# ---------------------------------------------------------------------------


def test_stage0_payload_and_producer_round_trip(tmp_path):
    root = tmp_path / "stackbuilder"
    sec_dir = root / "FOO"
    _make_secondary(root, "FOO")
    plan = drv.plan_secondary(
        sec_dir, stackbuilder_root=str(root), restage_all=False, unpin=False
    )
    assert plan.status == "synthesize"
    assert plan.would_write_pointer is True
    assert plan.members is not None and len(plan.members) == 6

    payload = drv.build_pointer_payload(plan.secondary, plan.chosen_run_dir)
    assert payload["selected_run_dir"] == plan.chosen_run_dir
    target = sec_dir / "selected_build.json"
    drv.write_pointer_atomic(target, payload)
    assert target.is_file()

    import k6_mtf_history_producer as producer

    stack = producer.resolve_k6_stack("FOO", stackbuilder_root=str(root))
    assert len(stack.members) == 6


# ---------------------------------------------------------------------------
# 2. Dotted-secondary routing (DX-Y.NYB)
# ---------------------------------------------------------------------------


def test_dotted_secondary_routing(tmp_path):
    root = tmp_path / "stackbuilder"
    sec = "DX-Y.NYB"
    sec_dir = root / sec
    _make_secondary(root, sec)
    plan = drv.plan_secondary(
        sec_dir, stackbuilder_root=str(root), restage_all=False, unpin=False
    )
    assert plan.status == "synthesize"
    payload = drv.build_pointer_payload(plan.secondary, plan.chosen_run_dir)
    target = root / sec / "selected_build.json"
    drv.write_pointer_atomic(target, payload)

    # Dots preserved; no sanitized DX-Y_NYB directory created.
    assert (root / "DX-Y.NYB" / "selected_build.json").is_file()
    assert not (root / "DX-Y_NYB").exists()

    import k6_mtf_history_producer as producer

    stack = producer.resolve_k6_stack("DX-Y.NYB", stackbuilder_root=str(root))
    assert len(stack.members) == 6


# ---------------------------------------------------------------------------
# 3. Seed selection: one / unique-newest / tied / zero
# ---------------------------------------------------------------------------


def test_choose_seed_dir_variants(tmp_path):
    assert drv.choose_seed_dir([]) == (None, "zero_combo_dir")

    d1 = tmp_path / "a"
    d1.mkdir()
    assert drv.choose_seed_dir([d1]) == (d1, "ok")

    d2 = tmp_path / "b"
    d2.mkdir()
    os.utime(d1, (1000, 1000))
    os.utime(d2, (2000, 2000))
    chosen, status = drv.choose_seed_dir([d1, d2])
    assert status == "ok" and chosen == d2

    os.utime(d1, (3000, 3000))
    os.utime(d2, (3000, 3000))
    chosen, status = drv.choose_seed_dir([d1, d2])
    assert chosen is None and status == "ambiguous_seed_mtime_tie"


def test_plan_secondary_two_rundirs_picks_newest(tmp_path):
    root = tmp_path / "stackbuilder"
    sec_dir = root / "FOO"
    older = _make_secondary(root, "FOO", seed_name="seedTC__old")
    newer = _make_secondary(root, "FOO", seed_name="seedTC__new")
    os.utime(older, (1000, 1000))
    os.utime(newer, (5000, 5000))
    plan = drv.plan_secondary(
        sec_dir, stackbuilder_root=str(root), restage_all=False, unpin=False
    )
    assert plan.status == "synthesize"
    assert plan.chosen_run_dir.endswith("seedTC__new")


def test_plan_secondary_zero_combo(tmp_path):
    root = tmp_path / "stackbuilder"
    sec_dir = root / "EMPTY"
    sec_dir.mkdir(parents=True)
    plan = drv.plan_secondary(
        sec_dir, stackbuilder_root=str(root), restage_all=False, unpin=False
    )
    assert plan.status == "excluded"
    assert plan.reason == "zero_combo_dir"


# ---------------------------------------------------------------------------
# 4. Pin awareness + existing pointer untouched
# ---------------------------------------------------------------------------


def test_existing_pointer_untouched_without_restage(tmp_path):
    root = tmp_path / "stackbuilder"
    sec_dir = root / "FOO"
    seed = _make_secondary(root, "FOO")
    sel = sec_dir / "selected_build.json"
    sel.write_text(json.dumps({"selected_run_dir": str(seed)}), encoding="utf-8")
    before = sel.read_text(encoding="utf-8")

    plan = drv.plan_secondary(
        sec_dir, stackbuilder_root=str(root), restage_all=False, unpin=False
    )
    assert plan.status == "existing"
    assert plan.would_write_pointer is False
    assert plan.members is not None and len(plan.members) == 6
    # plan_secondary never writes; existing pointer is byte-identical.
    assert sel.read_text(encoding="utf-8") == before


def test_pin_blocks_synthesis_unless_unpin(tmp_path):
    root = tmp_path / "stackbuilder"
    sec_dir = root / "FOO"
    _make_secondary(root, "FOO")
    (sec_dir / "selected_build.pinned.json").write_text("{}", encoding="utf-8")

    plan = drv.plan_secondary(
        sec_dir, stackbuilder_root=str(root), restage_all=False, unpin=False
    )
    assert plan.status == "blocked_by_pin"

    plan_unpin = drv.plan_secondary(
        sec_dir, stackbuilder_root=str(root), restage_all=False, unpin=True
    )
    assert plan_unpin.status == "synthesize"


# ---------------------------------------------------------------------------
# 5. [D]/[I] vs -D/-I parsing (parity with producer)
# ---------------------------------------------------------------------------


def test_member_token_parsing_and_parity():
    assert drv.parse_member_token("AWR[D]") == ("AWR", "D", "AWR[D]")
    assert drv.parse_member_token("CP[I]") == ("CP", "I", "CP[I]")
    assert drv.parse_member_token("600058.SS[I]") == ("600058.SS", "I", "600058.SS[I]")

    with pytest.raises(ValueError):
        drv.parse_member_token("AWR-D")
    with pytest.raises(ValueError):
        drv.parse_member_token("CP-I")
    with pytest.raises(ValueError):
        drv.parse_member_token("PLAIN")

    import k6_mtf_history_producer as producer

    for tok in ["AWR[D]", "CP[I]", "600058.SS[I]"]:
        m = producer._parse_member_token(tok)
        ticker, protocol, _raw = drv.parse_member_token(tok)
        assert (ticker, protocol) == (m.ticker, m.protocol)


def test_parse_combo_members_rejects_dash_form(tmp_path):
    seed = tmp_path / "seed"
    _make_combo(seed, members=["AAA-D", "BBB-I", "CCC-D", "DDD-I", "EEE-D", "FFF-I"])
    members, err = drv.parse_combo_members(seed / "combo_k=6.json")
    assert members is None
    assert err == "member_token_invalid"


def test_parse_combo_members_requires_six(tmp_path):
    seed = tmp_path / "seed"
    _make_combo(seed, members=["AAA[D]", "BBB[I]"])
    members, err = drv.parse_combo_members(seed / "combo_k=6.json")
    assert members is None and err == "members_not_six"


# ---------------------------------------------------------------------------
# 6. Union resolution dedupe
# ---------------------------------------------------------------------------


def test_resolve_union_dedupe():
    P = drv.SecondaryPlan
    plan_a = P(
        secondary="aaa",
        secondary_dir="x",
        status="synthesize",
        members=[("MMM", "D", "MMM[D]"), ("nnn", "I", "nnn[I]")],
    )
    plan_b = P(
        secondary="BBB",
        secondary_dir="y",
        status="synthesize",
        members=[("mmm", "D", "mmm[D]"), ("ZZZ", "I", "ZZZ[I]")],
    )
    union = drv.resolve_union([plan_a, plan_b])
    assert set(union.keys()) == {"AAA", "MMM", "NNN", "BBB", "ZZZ"}
    # First-seen display preserved (plan_a's "MMM" before plan_b's "mmm").
    assert union["MMM"] == "MMM"


# ---------------------------------------------------------------------------
# 7 + 8. Stage Aprime rebuild policy (overwrite, never clear; exclude PKL-less)
# ---------------------------------------------------------------------------


def _fake_aprime_report(calls):
    def fake_report(tickers, *, signal_cache_dir, stackbuilder_price_cache_dir,
                    format, write, overwrite):
        calls["overwrite"] = overwrite
        calls["write"] = write
        calls["format"] = format
        calls["tickers"] = list(tickers)
        rows = [{"ticker": t, "issue_codes": []} for t in tickers]
        return {"write_count": len(rows), "verification_pass_count": len(rows),
                "rows": rows, "format": format}
    return fake_report


def test_aprime_rebuild_default_csv_and_overwrite():
    calls = {}
    report, kept, excluded = drv.stage_aprime_rebuild(
        ["SPY", "AAPL"], cache_dir="c", price_cache_dir="p",
        write=True, report_fn=_fake_aprime_report(calls),
    )
    # Default format is CSV (repo convention; no parquet engine in env).
    assert calls["format"] == "csv"
    # Rebuild overwrites stale csv/parquet so it cannot shadow a fresh PKL.
    assert calls["overwrite"] is True
    assert calls["write"] is True
    assert kept == ["SPY", "AAPL"]
    assert excluded == []


def test_aprime_rebuild_explicit_parquet_passthrough():
    calls = {}
    drv.stage_aprime_rebuild(
        ["SPY"], cache_dir="c", price_cache_dir="p", write=True,
        fmt="parquet", report_fn=_fake_aprime_report(calls),
    )
    assert calls["format"] == "parquet"
    assert calls["overwrite"] is True
    assert calls["write"] is True


def test_aprime_rebuild_no_clear_delete_mode():
    # The seam only ever calls the writer with write/overwrite; it never
    # deletes/clears. Assert the report_fn is invoked without any
    # clear/delete-style kwarg and that exclusions (not deletions) handle
    # unbuildable secondaries.
    seen_kwargs = {}

    def fake_report(tickers, **kwargs):
        seen_kwargs.update(kwargs)
        return {"write_count": 0, "verification_pass_count": 0,
                "rows": [{"ticker": t, "issue_codes": ["x"]} for t in tickers],
                "format": kwargs.get("format")}

    _report, kept, excluded = drv.stage_aprime_rebuild(
        ["SPY"], cache_dir="c", price_cache_dir="p", write=True,
        report_fn=fake_report,
    )
    assert set(seen_kwargs.keys()) == {
        "signal_cache_dir", "stackbuilder_price_cache_dir",
        "format", "write", "overwrite",
    }
    assert "clear" not in seen_kwargs and "delete" not in seen_kwargs
    assert excluded == ["SPY"] and kept == []


def test_aprime_excludes_pkl_less_secondary_never_clears():
    def fake_report(tickers, *, signal_cache_dir, stackbuilder_price_cache_dir,
                    format, write, overwrite):
        rows = []
        for t in tickers:
            if t == "NOPKL":
                rows.append({"ticker": t, "issue_codes": ["source_pkl_missing"]})
            else:
                rows.append({"ticker": t, "issue_codes": []})
        return {"write_count": 1, "verification_pass_count": 1, "rows": rows}

    report, kept, excluded = drv.stage_aprime_rebuild(
        ["SPY", "NOPKL"], cache_dir="c", price_cache_dir="p",
        write=True, report_fn=fake_report,
    )
    assert "NOPKL" in excluded
    assert "SPY" in kept
    # The driver never deletes a price-cache file; it only excludes.


# ---------------------------------------------------------------------------
# 9. Offline skip gate skips equal/ahead, never calls refresher
# ---------------------------------------------------------------------------


class _FakeCutoffState:
    def __init__(self, ahead=False, equal=False, end=None):
        self.cache_ahead_of_cutoff = ahead
        self.cache_equal_to_cutoff = equal
        self.cache_date_range_end = end


def _boom_refresh(*a, **k):
    raise AssertionError("refresher must not be called on a fresh ticker")


def test_skip_gate_skips_ahead_and_equal_never_refreshes():
    for state in (
        _FakeCutoffState(ahead=True, end="2026-06-03"),
        _FakeCutoffState(equal=True, end="2026-06-03"),
    ):
        res = drv.stage_a_process_ticker(
            "SPY", cache_dir=None, status_dir=None, write=True,
            target_as_of="2026-06-03",
            evaluate_fn=lambda *a, **k: state,
            refresh_fn=_boom_refresh,
        )
        assert res["classification"] == "skipped_fresh"
        assert res["refresh_called"] is False


# ---------------------------------------------------------------------------
# 10. Active-stale refusal (2026-05-26 vs target 2026-06-03)
# ---------------------------------------------------------------------------


class _FakeRefreshResult:
    def __init__(self, issue_codes=(), new_end=None, current_after=False, refreshed=False):
        self.issue_codes = tuple(issue_codes)
        self.new_cache_date_range_end = new_end
        self.current_after = current_after
        self.refreshed = refreshed


def test_active_stale_cache_is_not_fresh_and_fails_if_unrefreshed():
    calls = {"n": 0}

    def refresh_fn(ticker, **k):
        calls["n"] += 1
        # Refresh produced no newer data: still 2026-05-26, not current.
        return _FakeRefreshResult(new_end="2026-05-26", current_after=False)

    res = drv.stage_a_process_ticker(
        "SPY", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03",
        evaluate_fn=lambda *a, **k: _FakeCutoffState(end="2026-05-26"),
        refresh_fn=refresh_fn,
    )
    assert calls["n"] == 1  # stale cache is NOT skipped as fresh
    assert res["classification"] == "failed"


def test_refresh_to_target_is_success():
    res = drv.stage_a_process_ticker(
        "SPY", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03",
        evaluate_fn=lambda *a, **k: _FakeCutoffState(end="2026-05-26"),
        refresh_fn=lambda ticker, **k: _FakeRefreshResult(
            new_end="2026-06-03", current_after=True, refreshed=True
        ),
    )
    assert res["classification"] == "refreshed"


# ---------------------------------------------------------------------------
# 11. Dead/no-history exclusion (no invented terminal semantics)
# ---------------------------------------------------------------------------


def _classify_with_issue(code):
    return drv.stage_a_process_ticker(
        "DEAD", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03",
        evaluate_fn=lambda *a, **k: _FakeCutoffState(end=None),
        refresh_fn=lambda ticker, **k: _FakeRefreshResult(issue_codes=(code,)),
    )["classification"]


def test_transient_fetch_failure_is_not_dead_no_history():
    # data_fetch_failed is a transient provider failure -> Stage A failure,
    # NOT a dead/no-history dependent-secondary exclusion.
    assert "data_fetch_failed" not in drv.DEAD_NO_HISTORY_ISSUE_CODES
    assert _classify_with_issue("data_fetch_failed") == "failed"


def test_data_empty_and_no_close_are_dead_no_history():
    assert _classify_with_issue("data_empty") == "dead_no_history"
    assert _classify_with_issue("data_no_close_column") == "dead_no_history"


# ---------------------------------------------------------------------------
# 12. Stage A structured-result inspection
# ---------------------------------------------------------------------------


def test_stage_a_result_carries_structured_fields():
    res = drv.stage_a_process_ticker(
        "SPY", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03",
        evaluate_fn=lambda *a, **k: _FakeCutoffState(end="2026-05-26"),
        refresh_fn=lambda ticker, **k: _FakeRefreshResult(
            new_end="2026-06-03", current_after=True, refreshed=True
        ),
    )
    assert res["ticker"] == "SPY"
    assert res["new_cache_date_range_end"] == "2026-06-03"
    assert res["current_after"] is True
    assert res["refresh_called"] is True
    assert "issue_codes" in res


# ---------------------------------------------------------------------------
# 13 + 15. Stage B worker result + daily stable rule
# ---------------------------------------------------------------------------


def _b_generate(member, interval, *, source_mode, cache_dir):
    assert source_mode == drv.LAUNCH_PATH_SOURCE_MODE  # never vendor mode
    return {"ticker": member, "interval": interval}


def _vstatus(member, *, exists, valid):
    return {"member": member, "interval": "1d", "exists": exists,
            "producer_valid": valid, "reason": None, "error_class": None,
            "message": None, "path": f"{member}_stable_v1_0_0.pkl",
            "dates_count": None, "signals_count": None,
            "can_repair": not valid and exists, "action": None}


def test_stage_b_builds_nondaily_and_creates_missing_daily():
    saved = []

    def save(lib, interval, *, force_overwrite):
        saved.append((interval, force_overwrite))

    res = drv.stage_b_process_member(
        "MMM",
        intervals=list(drv.NON_DAILY_TIMEFRAMES) + [drv.DAILY_TIMEFRAME],
        stable_dir="s",
        cache_dir="c",
        generate_fn=_b_generate,
        save_fn=save,
        validate_fn=lambda m, s: _vstatus(m, exists=False, valid=False),  # missing
        set_library_dir_fn=lambda d: None,
    )
    assert set(res["built"]) == {"1wk", "1mo", "3mo", "1y", "1d"}
    assert ("1d", True) in saved
    assert res["ok"] is True


def test_stage_b_existing_valid_daily_rebuilt():
    # A producer-valid (but possibly stale) 1d library is now REBUILT from the
    # refreshed cache every run, not skipped, so history_as_of_date advances.
    saved = []
    generated = []

    def gen(member, interval, *, source_mode, cache_dir):
        assert source_mode == drv.LAUNCH_PATH_SOURCE_MODE  # never vendor mode
        assert cache_dir == "c"  # rebuild reads the cache Stage A refreshed
        generated.append(interval)
        return {"ticker": member, "interval": interval}

    res = drv.stage_b_process_member(
        "MMM",
        intervals=list(drv.NON_DAILY_TIMEFRAMES) + [drv.DAILY_TIMEFRAME],
        stable_dir="s",
        cache_dir="c",
        generate_fn=gen,
        save_fn=lambda lib, interval, *, force_overwrite: saved.append(
            (interval, force_overwrite)),
        validate_fn=lambda m, s: _vstatus(m, exists=True, valid=True),  # valid
        set_library_dir_fn=lambda d: None,
    )
    # _build_daily ran for 1d (generated + forced-overwrite save from cache),
    # rebuilt not skipped.
    assert "1d" in generated                 # _build_daily called generate(1d)
    assert ("1d", True) in saved             # forced overwrite of the present lib
    assert "1d" in res["built"]
    assert "1d" not in res["skipped"]
    # non-daily intervals unchanged (all rebuilt as before)
    assert set(res["built"]) == {"1wk", "1mo", "3mo", "1y", "1d"}
    assert res["ok"] is True


def test_daily_stable_exists_path(tmp_path):
    stable = tmp_path / "stable"
    stable.mkdir()
    assert drv.daily_stable_exists("MMM", str(stable)) is False
    (stable / "MMM_stable_v1_0_0.pkl").write_text("x", encoding="utf-8")
    assert drv.daily_stable_exists("MMM", str(stable)) is True


# ---------------------------------------------------------------------------
# 14. Stage B no-vendor-fetch (launch-path local PKL)
# ---------------------------------------------------------------------------


def test_launch_path_reads_local_pkl_without_vendor_fetch(tmp_path, monkeypatch):
    import provenance_manifest as pm
    import signal_library.multi_timeframe_builder as mtb

    idx = pd.date_range("2018-01-01", periods=1200, freq="D")
    close = pd.Series(
        100.0 + np.sin(np.arange(1200) / 15.0) * 5 + np.arange(1200) * 0.05,
        index=idx,
    )
    obj = {"preprocessed_data": pd.DataFrame({"Close": close})}
    cache = tmp_path / "cache"
    cache.mkdir()
    with open(cache / "MMM_precomputed_results.pkl", "wb") as fh:
        pickle.dump(obj, fh)

    # Accept the test PKL via the verified loader seam (legacy-ok shape).
    monkeypatch.setattr(
        pm,
        "load_verified_pickle_artifact",
        lambda p: (obj, types.SimpleNamespace(ok=True, legacy=False, mismatches=[])),
    )
    # Any vendor fetch must raise; the launch path must never reach it.
    def _no_vendor(*a, **k):
        raise AssertionError("vendor fetch attempted on launch path")

    monkeypatch.setattr(mtb, "_yf_download_with_retry", _no_vendor)

    df = mtb.fetch_interval_data(
        "MMM",
        "1wk",
        source_mode=mtb.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
        cache_dir=str(cache),
    )
    assert df is not None and "Close" in df.columns and len(df) > 0


# ---------------------------------------------------------------------------
# 16. Barrier order A -> Aprime -> B -> E -> F -> H
# ---------------------------------------------------------------------------


def test_execute_chain_barrier_order(tmp_path, monkeypatch):
    order = []

    monkeypatch.setattr(
        drv, "_parallel_map",
        lambda worker, payloads, workers: [worker(p) for p in payloads],
    )
    monkeypatch.setattr(
        drv, "_stage_a_worker",
        lambda p: (order.append(("A", p["ticker"])),
                   {"ticker": p["ticker"], "classification": "refreshed"})[1],
    )
    monkeypatch.setattr(
        drv, "stage_aprime_rebuild",
        lambda secs, *, cache_dir, price_cache_dir, write, fmt="csv": (
            order.append(("Aprime", tuple(secs))),
            ({"write_count": len(secs), "verification_pass_count": len(secs),
              "format": fmt}, list(secs), []),
        )[1],
    )
    monkeypatch.setattr(
        drv, "_stage_b_worker",
        lambda p: (order.append(("B", p["member"])),
                   {"member": p["member"], "ok": True})[1],
    )

    import k6_mtf_history_producer as producer
    import k6_mtf_ranking_engine as ranking

    monkeypatch.setattr(
        producer, "run",
        lambda secs, **k: (order.append(("E", tuple(secs))),
                           {"run_id": k.get("run_id"),
                            "written_paths": list(secs), "failures": []})[1],
    )
    monkeypatch.setattr(
        ranking, "run",
        lambda run_dir, **k: (order.append(("F", run_dir)),
                              {"ranking_artifact_path": str(tmp_path / "r.json"),
                               "all_failed": False, "failed_records": []})[1],
    )
    monkeypatch.setattr(
        drv, "_stage_h_dry_run",
        lambda driver, rp, blocked: (order.append(("H", rp)),
                                     {"ran": True, "status": "dry_run_ok"})[1],
    )

    P = drv.SecondaryPlan
    included = [
        P(secondary="FOO", secondary_dir="x", status="synthesize",
          members=[("M1", "D", "M1[D]"), ("M2", "I", "M2[I]")]),
        P(secondary="BAR", secondary_dir="y", status="synthesize",
          members=[("M2", "D", "M2[D]"), ("M3", "I", "M3[I]")]),
    ]
    args = types.SimpleNamespace(
        cache_dir="c", status_dir="cs", price_cache_dir="p", stable_dir="s",
        output_root=str(tmp_path / "out"), stackbuilder_root="sb",
        a_workers=2, b_workers=2, promote_dry_run=True,
        fetch_retries=2, fetch_backoff_base_seconds=1.0,
        fetch_backoff_max_seconds=8.0, price_cache_format="csv",
        allow_stage_a_exclusions=False, repair_invalid_daily_stable=False,
        allow_aprime_caret_cache_alias=False,
    )
    driver = drv.Driver(
        args=args, stages=list(drv.STAGE_ORDER), executed=True,
        target_as_of="2026-06-03", driver_run_id="rid",
        project_root=tmp_path,
    )
    envelope = drv._new_envelope(driver)
    rc = drv._run_execute_chain(driver, envelope, included, None)
    assert rc == 0

    kinds = [k for k, _ in order]
    a_idx = [i for i, (k, _) in enumerate(order) if k == "A"]
    ap_idx = [i for i, (k, _) in enumerate(order) if k == "Aprime"]
    b_idx = [i for i, (k, _) in enumerate(order) if k == "B"]
    e_idx = [i for i, (k, _) in enumerate(order) if k == "E"]
    f_idx = [i for i, (k, _) in enumerate(order) if k == "F"]
    h_idx = [i for i, (k, _) in enumerate(order) if k == "H"]
    # All Stage A before Aprime; all Aprime before B; all B before E.
    assert max(a_idx) < min(ap_idx)
    assert max(ap_idx) < min(b_idx)
    assert max(b_idx) < min(e_idx)
    assert max(e_idx) < min(f_idx)
    assert max(f_idx) < min(h_idx)
    assert kinds.count("E") == 1 and kinds.count("F") == 1 and kinds.count("H") == 1
    # Execute Stage Aprime summary reports the selected (default csv) format.
    assert envelope["stageAprime"]["format"] == "csv"


# ---------------------------------------------------------------------------
# 17. Single-instance lock
# ---------------------------------------------------------------------------


def test_lock_held_refuses_then_releases(tmp_path):
    lp = tmp_path / ".recook.lock"
    h = drv.acquire_lock(lp, driver_run_id="r1", ttl_seconds=3600)
    assert h.acquired and lp.is_file()
    with pytest.raises(RuntimeError):
        drv.acquire_lock(lp, driver_run_id="r2", ttl_seconds=3600)
    drv.release_lock(h)
    assert not lp.is_file()


def test_lock_stale_by_ttl_reclaimed(tmp_path):
    lp = tmp_path / ".recook.lock"
    h = drv.acquire_lock(lp, driver_run_id="r1", ttl_seconds=3600)
    data = json.loads(lp.read_text(encoding="utf-8"))
    data["started_at_utc"] = "2000-01-01T00:00:00Z"  # ancient
    lp.write_text(json.dumps(data), encoding="utf-8")
    h2 = drv.acquire_lock(lp, driver_run_id="r2", ttl_seconds=1)
    assert h2.reclaimed_stale is True
    drv.release_lock(h2)


def test_lock_dead_pid_reclaimed(tmp_path):
    lp = tmp_path / ".recook.lock"
    drv.acquire_lock(lp, driver_run_id="r1", ttl_seconds=3600)
    data = json.loads(lp.read_text(encoding="utf-8"))
    data["pid"] = -1  # never-alive pid
    lp.write_text(json.dumps(data), encoding="utf-8")
    h2 = drv.acquire_lock(lp, driver_run_id="r2", ttl_seconds=3600)
    assert h2.reclaimed_stale is True
    drv.release_lock(h2)


# ---------------------------------------------------------------------------
# 18. Envelope / stdout discipline + dry-run creates no lock
# ---------------------------------------------------------------------------


def test_dry_run_one_json_object_and_no_lock(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    _make_secondary(root, "BAR")
    out = tmp_path / "out"
    rc, stdout = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(out),
        "--stable-dir", str(tmp_path / "stable"),
        "--target-as-of", "2026-06-03",
    ])
    parsed = json.loads(stdout)  # exactly one JSON object, parseable
    assert parsed["schema_version"] == "k6_recook_summary_v1"
    assert parsed["status"] == "dry_run"
    assert parsed["executed"] is False
    assert rc == 0
    assert parsed["stage0"]["buildable_secondaries"] == 2
    assert parsed["stage0"]["selected_build_would_synthesize_count"] == 2
    assert parsed["union"]["refresh_union_count"] >= 2
    # Dry-run never acquires the execute lock.
    assert not (out / ".recook.lock").exists()
    assert parsed["lock"]["acquired"] is False


def test_dry_run_writes_no_pointer(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    rc, stdout = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    assert rc == 0
    assert not (root / "FOO" / "selected_build.json").exists()


# ---------------------------------------------------------------------------
# 19. Stage H private dry-run only
# ---------------------------------------------------------------------------


def test_stage_h_dry_run_private_only(tmp_path, monkeypatch):
    import utils.react_publish.promote_k6_mtf_artifact as ph

    captured = {}

    def fake_promote(inputs, **k):
        captured["inputs"] = inputs
        return {
            "dry_run": True,
            "mode": "private_internal",
            "wrote_destination": False,
            "per_secondary_count": 3,
        }

    monkeypatch.setattr(ph, "promote", fake_promote)

    rp = tmp_path / "k6_mtf_ranking.json"
    rp.write_text("{}", encoding="utf-8")
    driver = drv.Driver(
        args=types.SimpleNamespace(), stages=[], executed=True,
        target_as_of="2026-06-03", driver_run_id="rid", project_root=tmp_path,
    )
    out = drv._stage_h_dry_run(driver, str(rp), blocked=False)
    inp = captured["inputs"]
    assert inp.public_mode is False
    assert inp.write is False
    assert inp.operator_approved is False
    assert inp.phase5_report_path is None
    assert out["status"] == "dry_run_ok"
    assert out["wrote_destination"] is False


def test_stage_h_blocked_flag_when_failures(tmp_path, monkeypatch):
    import utils.react_publish.promote_k6_mtf_artifact as ph

    monkeypatch.setattr(
        ph, "promote",
        lambda inputs, **k: {"dry_run": True, "mode": "private_internal",
                             "wrote_destination": False, "per_secondary_count": 1},
    )
    rp = tmp_path / "k6_mtf_ranking.json"
    rp.write_text("{}", encoding="utf-8")
    driver = drv.Driver(
        args=types.SimpleNamespace(), stages=[], executed=True,
        target_as_of="2026-06-03", driver_run_id="rid", project_root=tmp_path,
    )
    out = drv._stage_h_dry_run(driver, str(rp), blocked=True)
    assert out["promotion_blocked_by_failures"] is True


# ---------------------------------------------------------------------------
# Global refusals
# ---------------------------------------------------------------------------


def test_execute_without_network_flag_refuses_no_writes(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    out = tmp_path / "out"
    rc, stdout = _run_main([
        "--execute",
        "--stackbuilder-root", str(root),
        "--output-root", str(out),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    parsed = json.loads(stdout)
    assert parsed["status"] == "refused"
    assert rc != 0
    # Refused before any write: no lock, no synthesized pointer.
    assert not (out / ".recook.lock").exists()
    assert not (root / "FOO" / "selected_build.json").exists()


def test_unknown_stage_refused(tmp_path):
    rc, stdout = _run_main([
        "--stages", "bogus",
        "--stackbuilder-root", str(tmp_path / "stackbuilder"),
    ])
    assert json.loads(stdout)["status"] == "refused"
    assert rc != 0


def test_stages_out_of_order_refused(tmp_path):
    rc, stdout = _run_main([
        "--stages", "B,A",
        "--stackbuilder-root", str(tmp_path / "stackbuilder"),
    ])
    assert json.loads(stdout)["status"] == "refused"
    assert rc != 0


def test_invalid_target_date_refused(tmp_path):
    rc, stdout = _run_main([
        "--target-as-of", "06-03-2026",
        "--stackbuilder-root", str(tmp_path / "stackbuilder"),
    ])
    assert json.loads(stdout)["status"] == "refused"
    assert rc != 0


def test_missing_stackbuilder_root_refused(tmp_path):
    rc, stdout = _run_main([
        "--stackbuilder-root", str(tmp_path / "does_not_exist"),
    ])
    assert json.loads(stdout)["status"] == "refused"
    assert rc != 0


def test_no_buildable_secondaries_refused(tmp_path):
    root = tmp_path / "stackbuilder"
    root.mkdir()
    rc, stdout = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
    ])
    parsed = json.loads(stdout)
    assert parsed["status"] == "refused"
    assert rc != 0


# ---------------------------------------------------------------------------
# Parsing utilities
# ---------------------------------------------------------------------------


def test_parse_lock_ttl_forms():
    assert drv.parse_lock_ttl("6h") == 6 * 3600
    assert drv.parse_lock_ttl("360m") == 360 * 60
    assert drv.parse_lock_ttl("21600s") == 21600
    assert drv.parse_lock_ttl("21600") == 21600
    with pytest.raises(ValueError):
        drv.parse_lock_ttl("later")


def test_parse_stages_default_and_validation():
    assert drv.parse_stages(None) == list(drv.STAGE_ORDER)
    assert drv.parse_stages("A,B,E") == ["A", "B", "E"]
    with pytest.raises(ValueError):
        drv.parse_stages("E,A")
    with pytest.raises(ValueError):
        drv.parse_stages("nope")


def test_parse_target_date():
    assert drv.parse_target_date("2026-06-03") == "2026-06-03"
    with pytest.raises(ValueError):
        drv.parse_target_date("2026/06/03")


# ---------------------------------------------------------------------------
# Amendment 1: safe --stages semantics (contiguous prefix under execute)
# ---------------------------------------------------------------------------


def test_is_contiguous_prefix():
    assert drv.is_contiguous_prefix(["stage0"]) is True
    assert drv.is_contiguous_prefix(["stage0", "A"]) is True
    assert drv.is_contiguous_prefix(["stage0", "A", "Aprime"]) is True
    assert drv.is_contiguous_prefix(list(drv.STAGE_ORDER)) is True
    # Non-prefixes:
    assert drv.is_contiguous_prefix([]) is False
    assert drv.is_contiguous_prefix(["A", "Aprime"]) is False
    assert drv.is_contiguous_prefix(["stage0", "A", "B"]) is False
    assert drv.is_contiguous_prefix(["E", "F", "H"]) is False
    assert drv.is_contiguous_prefix(["stage0", "A", "Aprime", "E"]) is False


@pytest.mark.parametrize(
    "stages", ["E,F,H", "A,B,E", "stage0,A,B", "stage0,A,Aprime,E", "B"]
)
def test_execute_refuses_unsafe_stage_subsets(tmp_path, stages):
    rc, out = _run_main([
        "--execute", "--allow-network-fetch",
        "--stages", stages,
        "--stackbuilder-root", str(tmp_path / "sb"),
        "--output-root", str(tmp_path / "out"),
    ])
    j = json.loads(out)
    assert j["status"] == "refused"
    assert j["halted_at"] == "preflight"
    assert rc != 0
    assert any("contiguous prefix" in w for w in j["warnings"])
    # Refused before any write: no lock, no pointer.
    assert not (tmp_path / "out" / ".recook.lock").exists()


@pytest.mark.parametrize(
    "stages",
    ["stage0", "stage0,A", "stage0,A,Aprime", "stage0,A,Aprime,B",
     "stage0,A,Aprime,B,E", "stage0,A,Aprime,B,E,F",
     "stage0,A,Aprime,B,E,F,H"],
)
def test_execute_allowed_prefixes_pass_prefix_check(tmp_path, stages):
    # A valid prefix that includes Stage A must get PAST the prefix check
    # and be refused (if at all) for the NETWORK reason, never the prefix
    # reason. (stage0-only proceeds past both and is not asserted here.)
    root = tmp_path / "sb"
    _make_secondary(root, "FOO")
    rc, out = _run_main([
        "--execute", "--stages", stages,
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert not any("contiguous prefix" in w for w in j["warnings"])
    if "A" in stages.split(","):
        assert j["status"] == "refused"
        assert any("allow-network-fetch" in w for w in j["warnings"])


def test_dry_run_allows_noncontiguous_stages(tmp_path):
    root = tmp_path / "sb"
    _make_secondary(root, "FOO")
    rc, out = _run_main([
        "--stages", "E,F,H",
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert j["status"] == "dry_run"
    assert rc == 0


# ---------------------------------------------------------------------------
# Amendment 2: halt after Stage 0 write / resolve failures (execute)
# ---------------------------------------------------------------------------


def test_execute_halts_on_pointer_write_failure(tmp_path, monkeypatch):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    monkeypatch.setattr(drv, "write_pointer_atomic", _boom_write)
    monkeypatch.setattr(drv, "_run_execute_chain", _must_not_run)
    rc, out = _run_main([
        "--execute", "--allow-network-fetch",
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert j["status"] == "failed"
    assert j["halted_at"] == "stage0"
    assert rc != 0
    assert j["stage0"]["pointer_write_failures"]


def test_execute_halts_on_resolve_failure(tmp_path, monkeypatch):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    import k6_mtf_history_producer as producer

    def boom_resolve(secondary, *, stackbuilder_root):
        raise producer.K6StackResolutionError("simulated resolve failure")

    monkeypatch.setattr(producer, "resolve_k6_stack", boom_resolve)
    monkeypatch.setattr(drv, "_run_execute_chain", _must_not_run)
    rc, out = _run_main([
        "--execute", "--allow-network-fetch",
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert j["status"] == "failed"
    assert j["halted_at"] == "stage0"
    assert rc != 0
    assert j["stage0"]["resolve_failures"]


# ---------------------------------------------------------------------------
# Amendment 3: execute chain halts Stage A on data_fetch_failed
# ---------------------------------------------------------------------------


def test_execute_chain_halts_on_stage_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        drv, "_parallel_map",
        lambda worker, payloads, workers: [worker(p) for p in payloads],
    )
    monkeypatch.setattr(
        drv, "_stage_a_worker",
        lambda p: {"ticker": p["ticker"], "classification": "failed",
                   "issue_codes": ["data_fetch_failed"]},
    )
    # Stage Aprime must never run after a Stage A failure.
    monkeypatch.setattr(drv, "stage_aprime_rebuild", _must_not_run)

    P = drv.SecondaryPlan
    included = [
        P(secondary="FOO", secondary_dir="x", status="synthesize",
          members=[("M1", "D", "M1[D]"), ("M2", "I", "M2[I]")]),
    ]
    args = types.SimpleNamespace(
        cache_dir="c", status_dir="cs", price_cache_dir="p", stable_dir="s",
        output_root=str(tmp_path / "out"), stackbuilder_root="sb",
        a_workers=2, b_workers=2, promote_dry_run=True,
        fetch_retries=2, fetch_backoff_base_seconds=1.0,
        fetch_backoff_max_seconds=8.0, price_cache_format="csv",
        allow_stage_a_exclusions=False, repair_invalid_daily_stable=False,
        allow_aprime_caret_cache_alias=False,
    )
    driver = drv.Driver(
        args=args, stages=list(drv.STAGE_ORDER), executed=True,
        target_as_of="2026-06-03", driver_run_id="rid", project_root=tmp_path,
    )
    envelope = drv._new_envelope(driver)
    rc = drv._run_execute_chain(driver, envelope, included, None)
    assert rc == 1
    assert envelope["halted_at"] == "A"
    assert envelope["status"] == "failed"
    assert envelope["stageA"]["failed"] >= 1


# ---------------------------------------------------------------------------
# Amendment 4: exact Stage 0 dry-run pointer plan
# ---------------------------------------------------------------------------


def test_dry_run_pointer_write_plan_complete_and_raw_paths(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    _make_secondary(root, "DX-Y.NYB")
    # An existing-pointer secondary should be counted but NOT listed.
    seed_baz = _make_secondary(root, "BAZ")
    (root / "BAZ" / "selected_build.json").write_text(
        json.dumps({"selected_run_dir": str(seed_baz)}), encoding="utf-8"
    )

    rc, out = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    plan = j["stage0"]["pointer_write_plan"]
    secs = {e["secondary"] for e in plan}
    # All synthesize candidates present; existing BAZ excluded from writes.
    assert secs == {"FOO", "DX-Y.NYB"}
    assert j["stage0"]["selected_build_existing_count"] == 1

    by = {e["secondary"]: e for e in plan}
    # Raw dotted directory preserved (no '.'->'_').
    assert by["DX-Y.NYB"]["target"].endswith("/DX-Y.NYB/selected_build.json")
    assert "DX-Y_NYB" not in by["DX-Y.NYB"]["target"]
    assert by["FOO"]["chosen_run_dir"] and by["FOO"]["combo_path"]

    # Dry-run wrote nothing.
    assert not (root / "FOO" / "selected_build.json").exists()
    assert not (root / "DX-Y.NYB" / "selected_build.json").exists()


# ---------------------------------------------------------------------------
# Amendment: Stage A transient-fetch retry / backoff
# ---------------------------------------------------------------------------


def _no_sleep(_delay):
    raise AssertionError("sleep must not be called on a successful fetch")


def _fake_refresh_via_fetcher(
    ticker, *, cache_dir, status_dir, write, max_sma_day,
    current_as_of_date, data_fetcher=None, provider_name=None,
):
    """Mirror the real refresher's fetcher-driven classification: a fetch
    exception -> data_fetch_failed; empty DataFrame -> data_empty; missing
    Close -> data_no_close_column; otherwise success."""
    try:
        df = data_fetcher(ticker)
    except Exception:
        return _FakeRefreshResult(
            issue_codes=("data_fetch_failed",), new_end=None, current_after=False
        )
    if df is None or (hasattr(df, "empty") and df.empty):
        return _FakeRefreshResult(issue_codes=("data_empty",))
    if "Close" not in list(getattr(df, "columns", [])):
        return _FakeRefreshResult(issue_codes=("data_no_close_column",))
    return _FakeRefreshResult(
        new_end="2026-06-03", current_after=True, refreshed=True
    )


def _stale_eval(*a, **k):
    return _FakeCutoffState(end="2026-05-26")


def _good_close_df():
    idx = pd.date_range("2020-01-01", periods=2, freq="D")
    return pd.DataFrame({"Close": [1.0, 2.0]}, index=idx)


def test_compute_backoff_delay_growth_and_cap():
    assert drv.compute_backoff_delay(0, base_seconds=1.0, max_seconds=8.0) == 1.0
    assert drv.compute_backoff_delay(1, base_seconds=1.0, max_seconds=8.0) == 2.0
    assert drv.compute_backoff_delay(2, base_seconds=1.0, max_seconds=8.0) == 4.0
    # Capped at max even with large exponent + jitter.
    assert drv.compute_backoff_delay(
        10, base_seconds=10.0, max_seconds=3.0, jitter=5.0
    ) == 3.0


def test_stage_a_retry_success_counts_and_backoff():
    df = _good_close_df()
    calls = {"n": 0}

    def underlying(ticker):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(f"transient {calls['n']}")
        return df

    sleeps = []
    fetcher = drv.RetryingFetcher(
        underlying=underlying,
        config=drv.RetryConfig(retries=2, base_seconds=1.0, max_seconds=8.0),
        sleep_fn=lambda d: sleeps.append(d),
        jitter_fn=lambda: 0.0,
    )
    res = drv.stage_a_process_ticker(
        "SPY", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03", evaluate_fn=_stale_eval,
        refresh_fn=_fake_refresh_via_fetcher, fetcher=fetcher,
        provider_name="yfinance_retry",
    )
    assert res["classification"] == "refreshed"
    assert "data_fetch_failed" not in res["issue_codes"]
    assert res["fetch_attempts"] == 3
    assert res["fetch_retries"] == 2
    assert res["retry_exhausted"] is False
    # Injected sleep -> no real sleep; exponential 1,2 (jitter 0).
    assert sleeps == [1.0, 2.0]


def test_stage_a_retry_exhaustion_is_failed_and_fail_closed():
    def underlying(ticker):
        raise RuntimeError("persistent provider outage")

    sleeps = []
    fetcher = drv.RetryingFetcher(
        underlying=underlying,
        config=drv.RetryConfig(retries=2, base_seconds=1.0, max_seconds=8.0),
        sleep_fn=lambda d: sleeps.append(d),
        jitter_fn=lambda: 0.0,
    )
    res = drv.stage_a_process_ticker(
        "SPY", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03", evaluate_fn=_stale_eval,
        refresh_fn=_fake_refresh_via_fetcher, fetcher=fetcher,
    )
    assert res["classification"] == "failed"
    assert "data_fetch_failed" in res["issue_codes"]
    assert res["retry_exhausted"] is True
    assert res["fetch_attempts"] == 3
    assert res["fetch_retries"] == 2
    assert res["retry_error_count"] == 3
    assert len(sleeps) == 2  # delays between 3 attempts


def test_stage_a_no_retry_on_empty_dataframe():
    calls = {"n": 0}

    def underlying(ticker):
        calls["n"] += 1
        return pd.DataFrame()

    fetcher = drv.RetryingFetcher(
        underlying=underlying,
        config=drv.RetryConfig(retries=3, base_seconds=1.0, max_seconds=8.0),
        sleep_fn=_no_sleep,
        jitter_fn=lambda: 0.0,
    )
    res = drv.stage_a_process_ticker(
        "SPY", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03", evaluate_fn=_stale_eval,
        refresh_fn=_fake_refresh_via_fetcher, fetcher=fetcher,
    )
    assert calls["n"] == 1  # exactly one fetch, no retry
    assert res["fetch_attempts"] == 1
    assert res["retry_exhausted"] is False
    assert res["classification"] == "dead_no_history"  # via data_empty


def test_stage_a_no_retry_on_missing_close():
    calls = {"n": 0}

    def underlying(ticker):
        calls["n"] += 1
        return pd.DataFrame({"Open": [1.0]})

    fetcher = drv.RetryingFetcher(
        underlying=underlying,
        config=drv.RetryConfig(retries=3, base_seconds=1.0, max_seconds=8.0),
        sleep_fn=_no_sleep,
        jitter_fn=lambda: 0.0,
    )
    res = drv.stage_a_process_ticker(
        "SPY", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03", evaluate_fn=_stale_eval,
        refresh_fn=_fake_refresh_via_fetcher, fetcher=fetcher,
    )
    assert calls["n"] == 1
    assert res["fetch_attempts"] == 1
    assert res["classification"] == "dead_no_history"  # via data_no_close_column


def test_offline_skip_does_not_call_retrying_fetcher():
    calls = {"n": 0}

    def underlying(ticker):
        calls["n"] += 1
        raise AssertionError("fresh ticker must not fetch")

    fetcher = drv.RetryingFetcher(
        underlying=underlying, config=drv.RetryConfig(), sleep_fn=_no_sleep,
    )
    res = drv.stage_a_process_ticker(
        "SPY", cache_dir=None, status_dir=None, write=True,
        target_as_of="2026-06-03",
        evaluate_fn=lambda *a, **k: _FakeCutoffState(ahead=True, end="2026-06-03"),
        refresh_fn=_boom_refresh, fetcher=fetcher,
    )
    assert res["classification"] == "skipped_fresh"
    assert res["refresh_called"] is False
    assert res["fetch_attempts"] == 0
    assert calls["n"] == 0


def test_backoff_never_exceeds_max_in_fetcher():
    def underlying(ticker):
        raise RuntimeError("x")

    sleeps = []
    fetcher = drv.RetryingFetcher(
        underlying=underlying,
        config=drv.RetryConfig(retries=5, base_seconds=1.0, max_seconds=4.0),
        sleep_fn=lambda d: sleeps.append(d),
        jitter_fn=lambda: 0.5,  # injected jitter
    )
    with pytest.raises(RuntimeError):
        fetcher("SPY")
    assert fetcher.exhausted is True
    assert len(sleeps) == 5  # 6 attempts -> 5 backoff sleeps
    assert all(s <= 4.0 for s in sleeps)


@pytest.mark.parametrize(
    "flag", [
        ["--fetch-retries", "-1"],
        ["--fetch-backoff-base-seconds", "-0.5"],
        ["--fetch-backoff-max-seconds", "-2"],
    ],
)
def test_negative_fetch_flags_refused(tmp_path, flag):
    rc, out = _run_main(flag + [
        "--stackbuilder-root", str(tmp_path / "sb"),
        "--output-root", str(tmp_path / "out"),
    ])
    j = json.loads(out)
    assert j["status"] == "refused"
    assert rc != 0


def test_stage_a_summary_has_retry_fields_and_halts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        drv, "_parallel_map",
        lambda worker, payloads, workers: [worker(p) for p in payloads],
    )

    def fake_worker(payload):
        return {
            "ticker": payload["ticker"], "classification": "failed",
            "issue_codes": ["data_fetch_failed"], "fetch_attempts": 3,
            "fetch_retries": 2, "retry_exhausted": True,
            "retry_error_count": 3, "retry_errors": ["RuntimeError: x"],
        }

    monkeypatch.setattr(drv, "_stage_a_worker", fake_worker)
    monkeypatch.setattr(drv, "stage_aprime_rebuild", _must_not_run)

    P = drv.SecondaryPlan
    included = [
        P(secondary="FOO", secondary_dir="x", status="synthesize",
          members=[("M1", "D", "M1[D]"), ("M2", "I", "M2[I]")]),
    ]
    args = types.SimpleNamespace(
        cache_dir="c", status_dir="cs", price_cache_dir="p", stable_dir="s",
        output_root=str(tmp_path / "out"), stackbuilder_root="sb",
        a_workers=2, b_workers=2, promote_dry_run=True,
        fetch_retries=2, fetch_backoff_base_seconds=1.0,
        fetch_backoff_max_seconds=8.0, price_cache_format="csv",
        allow_stage_a_exclusions=False, repair_invalid_daily_stable=False,
        allow_aprime_caret_cache_alias=False,
    )
    driver = drv.Driver(
        args=args, stages=list(drv.STAGE_ORDER), executed=True,
        target_as_of="2026-06-03", driver_run_id="rid", project_root=tmp_path,
    )
    envelope = drv._new_envelope(driver)
    rc = drv._run_execute_chain(driver, envelope, included, None)
    # Persistent data_fetch_failed after retry exhaustion halts at A.
    assert rc == 1
    assert envelope["halted_at"] == "A"
    sa = envelope["stageA"]
    assert sa["fetch_retries_attempted"] >= 2
    assert sa["fetch_retry_exhausted"] >= 1
    assert sa["fetch_retry_config"]["retries"] == 2
    assert sa["fetch_retry_config"]["base_seconds"] == 1.0
    assert sa["fetch_retry_config"]["max_seconds"] == 8.0
    assert sa["retry_details"]
    assert all("fetch_attempts" in d for d in sa["retry_details"])


def test_dry_run_does_not_run_stage_a(tmp_path, monkeypatch):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    monkeypatch.setattr(drv, "_stage_a_worker", _must_not_run)
    monkeypatch.setattr(
        drv, "_parallel_map",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no parallel map / fetch / sleep in dry-run")
        ),
    )
    rc, out = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert j["status"] == "dry_run"
    assert rc == 0
    assert j["schema_version"] == "k6_recook_summary_v1"


# ---------------------------------------------------------------------------
# Amendment: Stage Aprime price-cache format selection (csv default)
# ---------------------------------------------------------------------------


def test_dry_run_stage_aprime_format_defaults_csv(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    rc, out = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert j["status"] == "dry_run" and rc == 0
    assert j["stageAprime"]["format"] == "csv"


def test_dry_run_stage_aprime_format_explicit_parquet(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    rc, out = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
        "--price-cache-format", "parquet",
    ])
    j = json.loads(out)
    assert j["status"] == "dry_run" and rc == 0
    assert j["stageAprime"]["format"] == "parquet"


def test_invalid_price_cache_format_rejected(tmp_path):
    # argparse choices rejection -> SystemExit (standard CLI usage error).
    with pytest.raises(SystemExit):
        _run_main([
            "--price-cache-format", "feather",
            "--stackbuilder-root", str(tmp_path / "sb"),
        ])


def test_price_cache_format_default_constant():
    assert drv.DEFAULT_PRICE_CACHE_FORMAT == "csv"
    assert "csv" in drv.PRICE_CACHE_FORMATS
    assert "parquet" in drv.PRICE_CACHE_FORMATS


# ---------------------------------------------------------------------------
# Amendment: Stage A unavailable-data policy (--allow-stage-a-exclusions)
# ---------------------------------------------------------------------------


def _plan(sec, members):
    P = drv.SecondaryPlan
    return P(secondary=sec, secondary_dir="x", status="synthesize",
             members=[(t, p, f"{t}[{p}]") for (t, p) in members])


def _mk_args(tmp_path, **over):
    base = dict(
        cache_dir="c", status_dir="cs", price_cache_dir="p", stable_dir="s",
        output_root=str(tmp_path / "out"), stackbuilder_root="sb",
        a_workers=2, b_workers=2, promote_dry_run=True,
        fetch_retries=2, fetch_backoff_base_seconds=1.0,
        fetch_backoff_max_seconds=8.0, price_cache_format="csv",
        allow_stage_a_exclusions=False, repair_invalid_daily_stable=False,
        allow_aprime_caret_cache_alias=False,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def _run_chain(tmp_path, monkeypatch, included, *, a_by_ticker=None,
               allow=False, target="2026-06-03", b_fail_members=None,
               repair=False, caret_exclude=None, caret_alias=False):
    calls = {"aprime": [], "b_members": [], "e_secs": [], "f_secs": [], "h": [],
             "caret": []}
    monkeypatch.setattr(
        drv, "_parallel_map",
        lambda worker, payloads, workers: [worker(p) for p in payloads],
    )
    arb = {k.upper(): v for k, v in (a_by_ticker or {}).items()}

    def a_worker(p):
        t = p["ticker"]
        r = dict(arb.get(t.upper(), {"classification": "refreshed"}))
        r.setdefault("ticker", t)
        r.setdefault("classification", "refreshed")
        return r

    monkeypatch.setattr(drv, "_stage_a_worker", a_worker)

    def fake_aprime(secs, *, cache_dir, price_cache_dir, write, fmt="csv"):
        calls["aprime"].append(list(secs))
        return ({"write_count": len(secs), "verification_pass_count": len(secs),
                 "format": fmt}, list(secs), [])

    monkeypatch.setattr(drv, "stage_aprime_rebuild", fake_aprime)

    cexcl = set(caret_exclude or set())

    def fake_caret_bridge(caret_secs, *, cache_dir, price_cache_dir,
                          target_as_of, alias_enabled, currentness_cutoff=None):
        calls["caret"].append(list(caret_secs))
        kept = [s for s in caret_secs if s not in cexcl]
        excl = [
            {"secondary": s, "stage": "Aprime",
             "reason": "caret_source_unavailable_or_stale", "ticker": s,
             "dependent_role": "secondary"}
            for s in caret_secs if s in cexcl
        ]
        summary = {
            "caret_secondary_count": len(caret_secs),
            "caret_alias_bridge_enabled": bool(alias_enabled),
            "caret_secondaries_built_from_raw": len(kept),
            "caret_secondaries_built_from_alias": 0,
            "caret_secondaries_excluded": len(excl),
        }
        return kept, excl, summary

    monkeypatch.setattr(drv, "stage_aprime_caret_bridge", fake_caret_bridge)

    bfm = {m.upper() for m in (b_fail_members or set())}

    def b_worker(p):
        calls["b_members"].append(p["member"])
        if p["member"].upper() in bfm:
            return {"member": p["member"], "ok": False, "built": [],
                    "skipped": [], "repaired": False,
                    "errors": [{"interval": "1d",
                                "reason": "producer_invalid_daily_library"}],
                    "daily_status": {"member": p["member"], "exists": True,
                                     "producer_valid": False,
                                     "path": f"{p['member']}_stable_v1_0_0.pkl"}}
        return {"member": p["member"], "ok": True, "repaired": False}

    monkeypatch.setattr(drv, "_stage_b_worker", b_worker)

    import k6_mtf_history_producer as producer
    import k6_mtf_ranking_engine as ranking

    def prod_run(secs, **k):
        calls["e_secs"].append(list(secs))
        return {"run_id": k.get("run_id"), "written_paths": list(secs),
                "failures": []}

    monkeypatch.setattr(producer, "run", prod_run)

    def rank_run(run_dir, **k):
        calls["f_secs"].append(list(k.get("secondaries") or []))
        return {"ranking_artifact_path": str(tmp_path / "r.json"),
                "all_failed": False, "failed_records": []}

    monkeypatch.setattr(ranking, "run", rank_run)

    def fake_h(driver, rp, blocked):
        calls["h"].append({"rp": rp, "blocked": blocked})
        return {"ran": True, "status": "dry_run_ok",
                "mode": "private_internal_dry_run", "dry_run": True,
                "wrote_destination": False,
                "promotion_blocked_by_failures": blocked}

    monkeypatch.setattr(drv, "_stage_h_dry_run", fake_h)

    args = _mk_args(tmp_path, allow_stage_a_exclusions=allow,
                    repair_invalid_daily_stable=repair,
                    allow_aprime_caret_cache_alias=caret_alias)
    driver = drv.Driver(args=args, stages=list(drv.STAGE_ORDER), executed=True,
                        target_as_of=target, driver_run_id="rid",
                        project_root=tmp_path)
    envelope = drv._new_envelope(driver)
    rc = drv._run_execute_chain(driver, envelope, included, None)
    return rc, envelope, driver, calls


# Result-dict factories for the fake Stage A worker.
_NOT_CURRENT = {"classification": "failed", "issue_codes": [],
                "current_after": False, "new_cache_date_range_end": "2026-05-30"}
_DEAD = {"classification": "dead_no_history", "issue_codes": ["data_empty"]}
_INSUFF = {"classification": "failed",
           "issue_codes": ["optimizer_failed", "insufficient_history"]}
_DFF = {"classification": "failed", "issue_codes": ["data_fetch_failed"]}
_RETRY_EXH = {"classification": "failed", "issue_codes": [],
              "retry_exhausted": True, "fetch_retries": 2}
_MIXED = {"classification": "failed",
          "issue_codes": ["optimizer_failed", "insufficient_history",
                          "data_fetch_failed"]}


# --- 1. Strict default fail-closed ---


def test_strict_default_failed_ticker_halts(tmp_path, monkeypatch):
    inc = [_plan("FOO", [("M1", "D"), ("M2", "I")])]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"M1": _NOT_CURRENT}, allow=False)
    assert rc == 1 and env["halted_at"] == "A" and env["status"] == "failed"
    assert calls["aprime"] == [] and calls["e_secs"] == []
    assert env["stageA"]["strict_default_fail_closed"] is True


def test_strict_default_dead_no_history_halts(tmp_path, monkeypatch):
    inc = [_plan("FOO", [("M1", "D"), ("M2", "I")])]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"M2": _DEAD}, allow=False)
    assert rc == 1 and env["halted_at"] == "A"
    assert calls["aprime"] == []


# --- 2/3/4. Allowed exclusions continue under the flag ---


@pytest.mark.parametrize("bad", [_NOT_CURRENT, _DEAD, _INSUFF])
def test_allowed_exclusion_continues_with_flag(tmp_path, monkeypatch, bad):
    inc = [
        _plan("KEEP", [("G1", "D"), ("G2", "I")]),
        _plan("DROP", [("BAD", "D"), ("G2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BAD": bad}, allow=True)
    # DROP excluded; KEEP continues through downstream.
    assert env["status"] == "partial" and rc != 0 and env["halted_at"] is None
    assert calls["aprime"] == [["KEEP"]]
    assert calls["e_secs"] == [["KEEP"]]
    assert calls["f_secs"] == [["KEEP"]]
    secs_excluded = {e["secondary"] for e in drv_.exclusions}
    assert secs_excluded == {"DROP"}
    # Same scenario WITHOUT the flag halts.
    rc2, env2, _d2, calls2 = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BAD": bad}, allow=False)
    assert rc2 == 1 and env2["halted_at"] == "A" and calls2["aprime"] == []


# --- 5/6/7. Network/systemic always halt even with flag ---


@pytest.mark.parametrize("bad", [_DFF, _RETRY_EXH, _MIXED])
def test_blocking_outcomes_halt_even_with_flag(tmp_path, monkeypatch, bad):
    inc = [
        _plan("KEEP", [("G1", "D"), ("G2", "I")]),
        _plan("DROP", [("BAD", "D"), ("G2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BAD": bad}, allow=True)
    assert rc == 1 and env["halted_at"] == "A" and env["status"] == "failed"
    assert calls["aprime"] == [] and calls["e_secs"] == []
    assert env["stageA"]["blocked_unavailable_ticker_count"] >= 1


# --- 8. Exclusions remove all secondaries -> halt ---


def test_all_excluded_halts_at_a(tmp_path, monkeypatch):
    inc = [_plan("FOO", [("BAD", "D"), ("M2", "I")])]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BAD": _NOT_CURRENT}, allow=True)
    assert rc == 1 and env["halted_at"] == "A" and env["status"] == "failed"
    assert calls["aprime"] == []
    assert env["stageA"]["remaining_rankable_secondary_count"] == 0


# --- 9. Downstream scoping ---


def test_downstream_scoped_to_remaining(tmp_path, monkeypatch):
    inc = [
        _plan("KEEP", [("K1", "D"), ("K2", "I")]),
        _plan("DROP", [("BADONLY", "D"), ("D2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BADONLY": _DEAD}, allow=True)
    assert calls["aprime"] == [["KEEP"]]
    assert calls["e_secs"] == [["KEEP"]] and calls["f_secs"] == [["KEEP"]]
    built = set(calls["b_members"])
    # Only KEEP's members built; DROP-only members not built.
    assert built == {"K1", "K2"}
    assert "BADONLY" not in built and "D2" not in built


# --- 10. Multi-hit secondary keeps every cause, removed once ---


def test_multi_cause_secondary_dedup_with_evidence(tmp_path, monkeypatch):
    inc = [
        _plan("KEEP", [("K1", "D"), ("K2", "I")]),
        _plan("DROP", [("BAD1", "D"), ("BAD2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc,
        a_by_ticker={"BAD1": _NOT_CURRENT, "BAD2": _DEAD}, allow=True)
    drop_recs = [e for e in drv_.exclusions if e["secondary"] == "DROP"]
    assert len(drop_recs) == 2  # both causes preserved
    assert {r["ticker"] for r in drop_recs} == {"BAD1", "BAD2"}
    # Removed once from rankable -> only one DROP entry in stageA list.
    es = [x for x in env["stageA"]["excluded_secondaries"]
          if x["secondary"] == "DROP"]
    assert len(es) == 1 and len(es[0]["causes"]) == 2
    assert env["stageA"]["excluded_secondary_count"] == 1
    assert calls["aprime"] == [["KEEP"]]


# --- 11. Stage H private/blocked on partial ---


def test_partial_stage_h_private_and_blocked(tmp_path, monkeypatch):
    inc = [
        _plan("KEEP", [("G1", "D"), ("G2", "I")]),
        _plan("DROP", [("BAD", "D"), ("G2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BAD": _NOT_CURRENT}, allow=True)
    assert len(calls["h"]) == 1
    assert calls["h"][0]["blocked"] is True
    assert env["stageH"]["promotion_blocked_by_failures"] is True
    assert env["stageH"]["mode"] == "private_internal_dry_run"
    assert env["stageH"]["wrote_destination"] is False


# --- 12. Exit/status policy ---


def test_clean_run_is_ok_zero(tmp_path, monkeypatch):
    inc = [_plan("FOO", [("M1", "D"), ("M2", "I")])]
    rc, env, drv_, calls = _run_chain(tmp_path, monkeypatch, inc, allow=True)
    assert rc == 0 and env["status"] == "ok" and env["halted_at"] is None
    assert drv_.exclusions == [] and drv_.failures == []
    assert calls["aprime"] == [["FOO"]]


def test_partial_is_nonzero(tmp_path, monkeypatch):
    inc = [
        _plan("KEEP", [("G1", "D"), ("G2", "I")]),
        _plan("DROP", [("BAD", "D"), ("G2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BAD": _NOT_CURRENT}, allow=True)
    assert env["status"] == "partial" and rc != 0
    assert env.get("partial_reasons")


# --- 13. JSON reporting on a partial run ---


def test_stage_a_policy_json_fields(tmp_path, monkeypatch):
    inc = [
        _plan("KEEP", [("G1", "D"), ("G2", "I")]),
        _plan("DROP", [("BAD", "D"), ("G2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BAD": _NOT_CURRENT}, allow=True)
    sa = env["stageA"]
    for key in ("allow_stage_a_exclusions", "strict_default_fail_closed",
                "unavailable_ticker_count", "allowed_unavailable_ticker_count",
                "blocked_unavailable_ticker_count", "excluded_secondary_count",
                "remaining_rankable_secondary_count", "unavailable_tickers",
                "dependency_map_by_ticker", "excluded_secondaries",
                "issue_code_distribution", "data_fetch_failed",
                "retry_exhausted", "not_current", "insufficient_history",
                "dead_no_history"):
        assert key in sa, f"missing stageA.{key}"
    assert sa["allow_stage_a_exclusions"] is True
    assert sa["not_current"] == 1
    assert "BAD" in sa["dependency_map_by_ticker"]
    assert "DROP" in sa["dependency_map_by_ticker"]["BAD"]
    # Top-level exclusions populated; record has full evidence fields.
    assert drv_.exclusions
    rec = drv_.exclusions[0]
    for f in ("secondary", "stage", "reason", "ticker", "ticker_classification",
              "issue_codes", "current_after", "new_cache_date_range_end",
              "target_as_of", "dependent_role", "message", "action"):
        assert f in rec, f"missing exclusion field {f}"
    assert rec["dependent_role"] == "member"
    assert rec["member_token"] == "BAD[D]" and rec["member_protocol"] == "D"


def test_secondary_self_dependency_role(tmp_path, monkeypatch):
    # When the unavailable ticker IS the secondary's own price ticker.
    inc = [
        _plan("KEEP", [("G1", "D"), ("G2", "I")]),
        _plan("BADSEC", [("G1", "D"), ("G2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, a_by_ticker={"BADSEC": _NOT_CURRENT},
        allow=True)
    recs = [e for e in drv_.exclusions if e["secondary"] == "BADSEC"]
    assert any(r["dependent_role"] == "secondary" for r in recs)
    assert calls["aprime"] == [["KEEP"]]


# --- new-flag CLI dry-run smoke ---


def test_dry_run_reports_policy_flag(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    rc, out = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
        "--allow-stage-a-exclusions",
    ])
    j = json.loads(out)
    assert j["status"] == "dry_run" and rc == 0
    assert j["stageA"]["allow_stage_a_exclusions"] is True
    assert j["stageA"]["strict_default_fail_closed"] is False


def test_classify_stage_a_outcome_precedence():
    # Allowable + blocking issue together -> blocking wins.
    mixed = {"classification": "failed",
             "issue_codes": ["insufficient_history", "data_fetch_failed"]}
    info = drv._classify_stage_a_outcome(mixed, target_as_of="2026-06-03")
    assert info["is_unavailable"] and not info["allowable"]
    assert info["kind"] == "blocking"
    # retry_exhausted with empty codes -> blocking.
    info2 = drv._classify_stage_a_outcome(
        {"classification": "failed", "issue_codes": [], "retry_exhausted": True},
        target_as_of="2026-06-03")
    assert info2["kind"] == "blocking"
    # optimizer_failed alone (no insufficient_history) -> blocking (conservative).
    info3 = drv._classify_stage_a_outcome(
        {"classification": "failed", "issue_codes": ["optimizer_failed"]},
        target_as_of="2026-06-03")
    assert info3["kind"] == "blocking"
    # refreshed -> not unavailable.
    info4 = drv._classify_stage_a_outcome(
        {"classification": "refreshed", "issue_codes": []},
        target_as_of="2026-06-03")
    assert info4["is_unavailable"] is False


# ---------------------------------------------------------------------------
# Amendment: Stage B producer-valid 1d library validation + repair
# ---------------------------------------------------------------------------


# Known dot-suffix member names pinned by these tests (fixture names only;
# never special-cased in production logic): MHRIL.BO, ZEELEARN.BO, AXISCADES.BO.


def test_daily_validator_status_shape_and_redaction(tmp_path):
    stable = tmp_path / "stable"
    stable.mkdir()
    # Missing.
    miss = drv.daily_stable_is_producer_valid("MHRIL.BO", str(stable))
    assert miss["exists"] is False and miss["producer_valid"] is False
    assert miss["reason"] == "missing" and miss["action"] == "build"
    # Valid (injected loader succeeds).
    (stable / "ZEELEARN.BO_stable_v1_0_0.pkl").write_text("x", encoding="utf-8")
    ok = drv.daily_stable_is_producer_valid(
        "ZEELEARN.BO", str(stable),
        loader_fn=lambda m, i, *, stable_dir: {
            "dates": [1, 2, 3], "signals": ["Buy", "Short", "None"]},
    )
    assert ok["producer_valid"] is True and ok["dates_count"] == 3
    assert ok["signals_count"] == 3 and ok["action"] == "skip"
    # Invalid: loader raises with an absolute path -> message redacted.
    # The absolute path is built from tmp_path at RUNTIME so no literal local
    # path is committed in this source file.
    (stable / "AXISCADES.BO_stable_v1_0_0.pkl").write_text("x", encoding="utf-8")
    abs_path = str(tmp_path / "AXISCADES.BO_stable_v1_0_0.pkl")

    def boom(m, i, *, stable_dir):
        raise ValueError(
            f"member library {abs_path} dates/signals length mismatch: 100 vs 99"
        )

    bad = drv.daily_stable_is_producer_valid(
        "AXISCADES.BO", str(stable), loader_fn=boom)
    assert bad["producer_valid"] is False and bad["can_repair"] is True
    assert bad["error_class"] == "ValueError"
    assert "<redacted-path>" in bad["message"]
    assert abs_path not in bad["message"]  # real absolute path stripped
    assert str(tmp_path) not in bad["message"]
    assert bad["path"] == "AXISCADES.BO_stable_v1_0_0.pkl"  # basename only


def test_stage_b_invalid_daily_default_fails_member():
    saved = []

    res = drv.stage_b_process_member(
        "MHRIL.BO",
        intervals=list(drv.NON_DAILY_TIMEFRAMES) + [drv.DAILY_TIMEFRAME],
        stable_dir="s", cache_dir="c",
        generate_fn=_b_generate,
        save_fn=lambda lib, interval, *, force_overwrite: saved.append(interval),
        validate_fn=lambda m, s: _vstatus(m, exists=True, valid=False),
        set_library_dir_fn=lambda d: None,
        repair=False,
    )
    assert res["ok"] is False
    assert "1d" not in saved  # invalid 1d not silently overwritten/skipped
    assert any(e.get("reason") == "producer_invalid_daily_library"
               for e in res["errors"])


def test_stage_b_invalid_daily_repaired():
    state = {"saved_1d": False}
    saved = []

    def save(lib, interval, *, force_overwrite):
        saved.append(interval)
        if interval == "1d":
            state["saved_1d"] = True

    def validate(m, s):
        return _vstatus(m, exists=True, valid=state["saved_1d"])

    res = drv.stage_b_process_member(
        "ZEELEARN.BO",
        intervals=list(drv.NON_DAILY_TIMEFRAMES) + [drv.DAILY_TIMEFRAME],
        stable_dir="s", cache_dir="c",
        generate_fn=_b_generate, save_fn=save, validate_fn=validate,
        set_library_dir_fn=lambda d: None, repair=True,
    )
    assert res["ok"] is True and res["repaired"] is True
    assert "1d" in res["built"] and "1d" in saved


def test_stage_b_repair_failure_fails_member():
    res = drv.stage_b_process_member(
        "AXISCADES.BO",
        intervals=list(drv.NON_DAILY_TIMEFRAMES) + [drv.DAILY_TIMEFRAME],
        stable_dir="s", cache_dir="c",
        generate_fn=_b_generate,
        save_fn=lambda lib, interval, *, force_overwrite: None,
        validate_fn=lambda m, s: _vstatus(m, exists=True, valid=False),  # never valid
        set_library_dir_fn=lambda d: None, repair=True,
    )
    assert res["ok"] is False
    assert any(e.get("reason") == "repair_revalidation_failed"
               for e in res["errors"])


def test_stage_b_valid_daily_rebuilt_regardless_of_repair_flag():
    # A producer-valid 1d is REBUILT from the refreshed cache on every run, and
    # that rebuild is the normal path -- independent of the --repair flag (which
    # gates only the producer-INVALID branch). Here repair=True and the library
    # is valid, yet it is still rebuilt (force overwrite), not skipped.
    saved = []
    res = drv.stage_b_process_member(
        "MMM",
        intervals=list(drv.NON_DAILY_TIMEFRAMES) + [drv.DAILY_TIMEFRAME],
        stable_dir="s", cache_dir="c",
        generate_fn=_b_generate,
        save_fn=lambda lib, interval, *, force_overwrite: saved.append(
            (interval, force_overwrite)),
        validate_fn=lambda m, s: _vstatus(m, exists=True, valid=True),
        set_library_dir_fn=lambda d: None, repair=True,
    )
    assert "1d" in res["built"]
    assert "1d" not in res["skipped"]
    assert ("1d", True) in saved
    assert res["ok"] is True
    # rebuilt via the normal rebuild path, NOT the repair path
    assert res["repaired"] is False


def test_stage_b_exclusion_chain_distinct_and_scoped(tmp_path, monkeypatch):
    inc = [
        _plan("KEEP", [("K1", "D"), ("K2", "I")]),
        _plan("DROP", [("BADM", "D"), ("K2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, allow=True, b_fail_members={"BADM"})
    # Chain completes for KEEP; partial + nonzero.
    assert env["status"] == "partial" and rc != 0 and env["halted_at"] is None
    assert calls["e_secs"] == [["KEEP"]] and calls["f_secs"] == [["KEEP"]]
    assert calls["aprime"] == [["KEEP", "DROP"]]  # B excludes AFTER Aprime
    # Stage B exclusion recorded distinctly from Stage A.
    bstage = [e for e in drv_.exclusions if e.get("stage") == "B"]
    assert bstage and bstage[0]["secondary"] == "DROP"
    assert bstage[0]["ticker"] == "BADM"
    assert bstage[0]["dependent_role"] == "member"
    es = env["exclusion_summary"]
    assert es["stage_b_member_library_exclusions"] >= 1
    assert es["stage_a_allowed_exclusions"] == 0
    assert es["stage_aprime_price_cache_exclusions"] == 0
    assert "stage_b_member_library_exclusions" in env["partial_reasons"]
    # Stage H private + blocked.
    assert calls["h"][0]["blocked"] is True
    assert env["stageH"]["promotion_blocked_by_failures"] is True
    assert env["stageB"]["failed_members"] == ["BADM"]
    assert env["stageB"]["repair_invalid_daily_stable"] is False


def test_repair_flag_dry_run(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    rc, out = _run_main([
        "--allow-stage-a-exclusions", "--repair-invalid-daily-stable",
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert j["status"] == "dry_run" and rc == 0
    assert j["stageB"]["repair_invalid_daily_stable"] is True
    assert j["stageA"]["allow_stage_a_exclusions"] is True
    assert j["stageA"]["ran"] is False  # no execution stages in dry-run
    assert j["lock"]["acquired"] is False


def test_redact_local_paths_helper(tmp_path):
    assert drv._redact_local_paths(None) is None
    # Real absolute path from tmp_path (runtime; not a committed literal).
    abs_win = str(tmp_path / "f.pkl")
    red = drv._redact_local_paths(f"x {abs_win} y")
    assert "<redacted-path>" in red and abs_win not in red
    # POSIX synthetic tokens assembled at runtime so no forbidden literal is
    # committed in this source file.
    sep = "/"
    posix_user = sep + "U" "sers" + sep + "u" + sep + "f.pkl"
    assert "<redacted-path>" in drv._redact_local_paths(posix_user)
    posix_home = sep + "h" "ome" + sep + "u" + sep + "f.pkl"
    assert "<redacted-path>" in drv._redact_local_paths(posix_home)
    # Relative project paths are NOT redacted.
    rel = "signal_library/data/stable/MHRIL.BO_stable_v1_0_0.pkl"
    assert drv._redact_local_paths(rel) == rel


# ---------------------------------------------------------------------------
# Amendment: Stage Aprime caret cache-alias bridge
# ---------------------------------------------------------------------------


CARET_ALL = [
    "^DJI", "^DJT", "^FTSE", "^FVX", "^GDAXI", "^GSPC", "^IRX", "^IXIC",
    "^MID", "^NDX", "^NYA", "^RUA", "^RUI", "^RUT", "^STOXX50E", "^TNX",
    "^VIX", "^W5000", "^XAU",
]


class _FakeSeries:
    def __init__(self, last):
        self.last = last


def _bridge(caret_secs, *, alias_enabled, sources, target="2026-06-03",
            malformed=None, verify_override=None):
    """Run stage_aprime_caret_bridge with injected pure callables.

    ``sources`` maps a cache name (raw '^X' or alias '_X') -> last_date_str.
    Absent key => missing. ``malformed`` names read as unusable.
    """
    malformed = set(malformed or set())
    reads = []
    written = {}

    def read_source(name, cache_dir):
        reads.append(name)
        if name in malformed:
            return None, None, "malformed"
        last = sources.get(name)
        if last is None:
            return None, None, "missing"
        return last, _FakeSeries(last), "ok"

    def write_csv(secondary, series, price_cache_dir):
        written[secondary] = series.last
        return f"price_cache/daily/{secondary}.csv"

    def verify(secondary, price_cache_dir, cache_dir):
        if verify_override is not None and secondary in verify_override:
            return verify_override[secondary]
        if secondary not in written:
            return False, None, "not_written"
        return True, written[secondary], "csv"

    kept, excluded, summary = drv.stage_aprime_caret_bridge(
        caret_secs, cache_dir="cache/results",
        price_cache_dir="price_cache/daily", target_as_of=target,
        alias_enabled=alias_enabled, read_source_fn=read_source,
        write_csv_fn=write_csv, verify_fn=verify,
    )
    return kept, excluded, summary, written, reads


def test_caret_detection_and_alias_name():
    for s in CARET_ALL:
        assert drv.is_caret_secondary(s) is True
    assert drv.is_caret_secondary("DX-Y.NYB") is False
    assert drv.is_caret_secondary("AAPL") is False
    assert drv._caret_alias_name("^GSPC") == "_GSPC"
    assert drv._caret_alias_name("^STOXX50E") == "_STOXX50E"


def test_caret_all_viable_built_from_raw():
    sources = {s: "2026-06-03" for s in CARET_ALL}  # all raw current
    kept, excl, summ, written, _ = _bridge(
        CARET_ALL, alias_enabled=True, sources=sources)
    assert set(kept) == set(CARET_ALL) and excl == []
    assert summ["caret_secondaries_built_from_raw"] == len(CARET_ALL)
    assert all(summ["caret_source_by_secondary"][s] == "raw" for s in CARET_ALL)
    # Output written under the RAW caret spelling.
    assert set(written) == set(CARET_ALL)
    assert summ["caret_price_cache_path_by_secondary"]["^GSPC"].endswith(
        "/^GSPC.csv")


def test_caret_raw_missing_alias_current_uses_alias():
    kept, excl, summ, written, _ = _bridge(
        ["^DJI"], alias_enabled=True, sources={"_DJI": "2026-06-03"})
    assert kept == ["^DJI"] and excl == []
    assert summ["caret_source_by_secondary"]["^DJI"] == "alias"
    assert summ["caret_secondaries_built_from_alias"] == 1
    assert "^DJI" in written  # raw-spelled output path


def test_caret_raw_stale_alias_current_selects_alias():
    kept, excl, summ, written, _ = _bridge(
        ["^GSPC"], alias_enabled=True,
        sources={"^GSPC": "2026-05-04", "_GSPC": "2026-06-03"})
    assert kept == ["^GSPC"] and excl == []
    assert summ["caret_source_by_secondary"]["^GSPC"] == "alias"
    assert summ["caret_source_last_date_by_secondary"]["^GSPC"] == "2026-06-03"
    assert written["^GSPC"] == "2026-06-03"  # never the stale raw


def test_caret_vix_stale_raw_current_alias():
    kept, excl, summ, _w, _r = _bridge(
        ["^VIX"], alias_enabled=True,
        sources={"^VIX": "2026-05-04", "_VIX": "2026-06-03"})
    assert kept == ["^VIX"] and summ["caret_source_by_secondary"]["^VIX"] == "alias"


def test_caret_djt_raw_missing_alias_current():
    kept, excl, summ, _w, _r = _bridge(
        ["^DJT"], alias_enabled=True, sources={"_DJT": "2026-06-03"})
    assert kept == ["^DJT"] and summ["caret_source_by_secondary"]["^DJT"] == "alias"


def test_caret_raw_current_preferred_over_alias():
    kept, excl, summ, _w, reads = _bridge(
        ["^GSPC"], alias_enabled=True,
        sources={"^GSPC": "2026-06-03", "_GSPC": "2026-06-03"})
    assert kept == ["^GSPC"] and summ["caret_source_by_secondary"]["^GSPC"] == "raw"
    assert "_GSPC" not in reads  # alias not read when raw is current


def test_caret_all_stale_excluded():
    kept, excl, summ, _w, _r = _bridge(
        ["^GSPC"], alias_enabled=True,
        sources={"^GSPC": "2026-05-04", "_GSPC": "2026-05-01"})
    assert kept == [] and len(excl) == 1
    assert excl[0]["stage"] == "Aprime"
    assert excl[0]["reason"] == "caret_source_unavailable_or_stale"
    assert summ["caret_source_by_secondary"]["^GSPC"] == "excluded"


def test_caret_all_missing_excluded():
    kept, excl, summ, _w, _r = _bridge(
        ["^XAU"], alias_enabled=True, sources={})
    assert kept == [] and len(excl) == 1
    assert excl[0]["reason"] == "caret_source_unavailable_or_stale"


def test_caret_flag_off_no_alias_read():
    # Raw missing, alias current, but flag OFF -> excluded; alias never read.
    kept, excl, summ, _w, reads = _bridge(
        ["^DJI"], alias_enabled=False, sources={"_DJI": "2026-06-03"})
    assert kept == [] and len(excl) == 1
    assert "_DJI" not in reads
    assert summ["caret_alias_bridge_enabled"] is False
    # Raw current, flag OFF -> built from raw.
    kept2, _e, summ2, _w2, _r2 = _bridge(
        ["^DJI"], alias_enabled=False, sources={"^DJI": "2026-06-03"})
    assert kept2 == ["^DJI"] and summ2["caret_source_by_secondary"]["^DJI"] == "raw"


def test_caret_malformed_alias_excluded():
    kept, excl, summ, _w, _r = _bridge(
        ["^GSPC"], alias_enabled=True, sources={}, malformed={"_GSPC"})
    assert kept == [] and len(excl) == 1
    assert excl[0]["reason"] == "caret_source_unavailable_or_stale"


def test_caret_verification_gate_excludes_unreadable_output():
    # Source current, but the written CSV fails producer verification.
    kept, excl, summ, _w, _r = _bridge(
        ["^GSPC"], alias_enabled=True, sources={"^GSPC": "2026-06-03"},
        verify_override={"^GSPC": (False, None, "unreadable")})
    assert kept == [] and len(excl) == 1
    assert excl[0]["reason"] == "caret_output_unverified_or_stale"
    assert summ["caret_output_verified_by_producer_loader"]["^GSPC"] is False


def test_caret_verification_gate_excludes_stale_output():
    # Source reads current, but the verified output is stale (e.g. shadow).
    kept, excl, summ, _w, _r = _bridge(
        ["^GSPC"], alias_enabled=True, sources={"^GSPC": "2026-06-03"},
        verify_override={"^GSPC": (True, "2026-05-04", "csv")})
    assert kept == [] and len(excl) == 1
    assert excl[0]["reason"] == "caret_output_unverified_or_stale"


def test_caret_exclusion_bucketed_as_aprime_distinct():
    P = drv.SecondaryPlan
    driver = drv.Driver(
        args=types.SimpleNamespace(), stages=[], executed=True,
        target_as_of="2026-06-03", driver_run_id="r", project_root=Path("."))
    driver.exclusions = [
        {"secondary": "X", "stage": "A"},
        {"secondary": "Y", "stage": "B"},
        {"secondary": "^GSPC", "stage": "Aprime",
         "reason": "caret_source_unavailable_or_stale"},
    ]
    es = drv._exclusion_summary(driver)
    assert es["stage_aprime_price_cache_exclusions"] == 1
    assert es["stage_a_allowed_exclusions"] == 1
    assert es["stage_b_member_library_exclusions"] == 1
    assert es["stage_e_failures"] == 0


def test_caret_exclusion_chain_partial_and_h_blocked(tmp_path, monkeypatch):
    inc = [
        _plan("KEEP", [("K1", "D"), ("K2", "I")]),
        _plan("^GSPC", [("G1", "D"), ("G2", "I")]),
    ]
    rc, env, drv_, calls = _run_chain(
        tmp_path, monkeypatch, inc, allow=True, caret_alias=True,
        caret_exclude={"^GSPC"})
    assert env["status"] == "partial" and rc != 0 and env["halted_at"] is None
    # caret bridge was invoked with the caret secondary only.
    assert calls["caret"] == [["^GSPC"]]
    # KEEP continues; ^GSPC excluded.
    assert calls["e_secs"] == [["KEEP"]] and calls["f_secs"] == [["KEEP"]]
    ap = [e for e in drv_.exclusions if e.get("stage") == "Aprime"]
    assert any(e["secondary"] == "^GSPC" for e in ap)
    assert env["exclusion_summary"]["stage_aprime_price_cache_exclusions"] >= 1
    assert env["stageH"]["promotion_blocked_by_failures"] is True
    assert env["stageAprime"]["caret_alias_bridge_enabled"] is True


def test_dry_run_caret_bridge_flag_fields(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "^GSPC")
    _make_secondary(root, "FOO")
    # default: bridge disabled
    rc, out = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert j["status"] == "dry_run" and rc == 0
    assert j["stageAprime"]["caret_alias_bridge_enabled"] is False
    assert j["stageAprime"]["caret_secondary_count"] >= 1
    # policy: bridge enabled
    rc2, out2 = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
        "--allow-stage-a-exclusions", "--repair-invalid-daily-stable",
        "--allow-aprime-caret-cache-alias",
    ])
    j2 = json.loads(out2)
    assert j2["status"] == "dry_run" and rc2 == 0
    assert j2["stageAprime"]["caret_alias_bridge_enabled"] is True
    assert j2["stageA"]["ran"] is False and j2["lock"]["acquired"] is False


# ---------------------------------------------------------------------------
# Amendment: bounded T-1 currentness acceptance (--currency-grace-sessions)
#
# Policy basis (code + tests): the provider serves NaN/placeholder latest rows
# (no usable Close) for thin foreign EOD bars whose latest close stays
# undisseminated for many hours and is unrecoverable by any client call shape.
# v1 keeps fetching the newest bar but accepts a bounded one-trading-session
# usable-cache lag as CURRENT after a refresh attempt. NOT trafficflow
# ok_tminus1 semantics; the justification is the provider-placeholder evidence.
# ---------------------------------------------------------------------------


# --- Session-arithmetic helper ---


def test_currentness_cutoff_grace_zero_is_target():
    # N=0 reproduces strict same-day-only behavior.
    assert drv.acceptable_currentness_cutoff("2026-06-03", 0) == "2026-06-03"


def test_currentness_cutoff_one_session_midweek():
    # A midweek target (Tue-Fri) steps back exactly one calendar weekday.
    wed = date(2026, 6, 1)
    while wed.weekday() != 2:  # roll forward to a real Wednesday
        wed += timedelta(days=1)
    cut = date.fromisoformat(
        drv.acceptable_currentness_cutoff(wed.isoformat(), 1))
    assert cut.weekday() == 1 and (wed - cut).days == 1  # prior Tuesday


def test_currentness_cutoff_monday_weekend_boundary():
    # Target Monday, grace 1 -> the previous Friday counts as one session.
    monday = date(2026, 6, 1)
    while monday.weekday() != 0:  # roll forward to a real Monday
        monday += timedelta(days=1)
    cut = drv.acceptable_currentness_cutoff(monday.isoformat(), 1)
    cutd = date.fromisoformat(cut)
    assert cutd.weekday() == 4  # Friday
    assert (monday - cutd).days == 3  # Mon -> prior Fri skips Sat+Sun
    # grace 2 from the same Monday lands on the prior Thursday.
    cut2 = date.fromisoformat(
        drv.acceptable_currentness_cutoff(monday.isoformat(), 2))
    assert cut2.weekday() == 3 and (monday - cut2).days == 4


def test_currentness_cutoff_negative_raises():
    with pytest.raises(ValueError):
        drv.acceptable_currentness_cutoff("2026-06-03", -1)


# --- Stage A post-refresh acceptance ---


_GT = "2026-06-03"  # grace-test target


def _grace_stage_a(*, new_end, grace, target=_GT, issue_codes=(),
                   current_after=False):
    """Run stage_a_process_ticker on a stale cache that refreshes to
    ``new_end``, applying the bounded cutoff for ``grace`` sessions."""
    cutoff = drv.acceptable_currentness_cutoff(target, grace)
    return drv.stage_a_process_ticker(
        "FORGN.DE", cache_dir=None, status_dir=None, write=True,
        target_as_of=target,
        evaluate_fn=lambda *a, **k: _FakeCutoffState(end="2026-05-20"),
        refresh_fn=lambda ticker, **k: _FakeRefreshResult(
            issue_codes=issue_codes, new_end=new_end,
            current_after=current_after, refreshed=True),
        currentness_cutoff=cutoff,
    )


def test_grace_usable_end_equal_target_is_current():
    res = _grace_stage_a(new_end=_GT, grace=1, current_after=True)
    assert res["classification"] == "refreshed"


def test_grace_usable_end_target_minus_one_current_by_default():
    t1 = drv.acceptable_currentness_cutoff(_GT, 1)
    res = _grace_stage_a(new_end=t1, grace=1, current_after=False)
    assert res["classification"] == "refreshed"


def test_grace_zero_usable_end_target_minus_one_not_current():
    t1 = drv.acceptable_currentness_cutoff(_GT, 1)
    res = _grace_stage_a(new_end=t1, grace=0, current_after=False)
    assert res["classification"] == "failed"


def test_grace_usable_end_target_minus_two_not_current_default():
    t2 = drv.acceptable_currentness_cutoff(_GT, 2)
    res = _grace_stage_a(new_end=t2, grace=1, current_after=False)
    assert res["classification"] == "failed"


def test_offline_skip_gate_remains_ungraced_target_minus_one():
    # A cache at target-1 is NOT fresh to the strict gate: the refresher is
    # still called. Grace applies only to the post-refresh verdict.
    t1 = drv.acceptable_currentness_cutoff(_GT, 1)
    calls = {"n": 0}

    def refresh_fn(ticker, **k):
        calls["n"] += 1
        return _FakeRefreshResult(new_end=t1, current_after=False,
                                  refreshed=True)

    res = drv.stage_a_process_ticker(
        "FORGN.DE", cache_dir=None, status_dir=None, write=True,
        target_as_of=_GT,
        # ahead/equal both False -> a target-1 cache is not strictly fresh.
        evaluate_fn=lambda *a, **k: _FakeCutoffState(
            ahead=False, equal=False, end=t1),
        refresh_fn=refresh_fn,
        currentness_cutoff=t1,
    )
    assert res["refresh_called"] is True and calls["n"] == 1
    assert res["classification"] == "refreshed"  # accepted only post-refresh


def test_grace_placeholder_shape_preserves_honesty_dates():
    # Provider placeholder: refresh attempt lands the usable end at target-1
    # with the strict current_after flag False. Grace accepts currency but the
    # recorded data-end date and strict flag stay actual (no backfill/relabel).
    t1 = drv.acceptable_currentness_cutoff(_GT, 1)
    res = _grace_stage_a(new_end=t1, grace=1, current_after=False)
    assert res["classification"] == "refreshed"
    assert res["new_cache_date_range_end"] == t1  # actual usable end, not _GT
    assert res["current_after"] is False           # strict flag untouched


# --- Classifier ---


def test_classify_not_current_respects_grace_cutoff():
    t1 = drv.acceptable_currentness_cutoff(_GT, 1)
    t2 = drv.acceptable_currentness_cutoff(_GT, 2)
    # Under grace 0 a target-1 usable end is not_current (allowable).
    info0 = drv._classify_stage_a_outcome(
        {"classification": "failed", "issue_codes": [],
         "current_after": False, "new_cache_date_range_end": t1},
        target_as_of=_GT, currentness_cutoff=_GT)
    assert info0["kind"] == "not_current" and info0["allowable"] is True
    # Under grace 1 a target-2 usable end is still not_current (allowable).
    info1 = drv._classify_stage_a_outcome(
        {"classification": "failed", "issue_codes": [],
         "current_after": False, "new_cache_date_range_end": t2},
        target_as_of=_GT, currentness_cutoff=t1)
    assert info1["kind"] == "not_current" and info1["allowable"] is True


def test_classify_grace_does_not_mask_blocking():
    # A generous cutoff must never turn a network/systemic failure current.
    lenient = drv.acceptable_currentness_cutoff(_GT, 5)
    dff = drv._classify_stage_a_outcome(
        {"classification": "failed", "issue_codes": ["data_fetch_failed"],
         "current_after": False, "new_cache_date_range_end": None},
        target_as_of=_GT, currentness_cutoff=lenient)
    assert dff["kind"] == "blocking" and dff["allowable"] is False
    rex = drv._classify_stage_a_outcome(
        {"classification": "failed", "issue_codes": [],
         "retry_exhausted": True, "new_cache_date_range_end": None},
        target_as_of=_GT, currentness_cutoff=lenient)
    assert rex["kind"] == "blocking" and rex["allowable"] is False


# --- Aprime caret bridge ---


def _grace_caret(sec, *, source_date, currentness_cutoff, target=_GT):
    written = {}

    def read_source(name, cache_dir):
        return source_date, _FakeSeries(source_date), "ok"

    def write_csv(secondary, series, price_cache_dir):
        written[secondary] = series.last
        return f"price_cache/daily/{secondary}.csv"

    def verify(secondary, price_cache_dir, cache_dir):
        return True, written.get(secondary), "csv"

    return drv.stage_aprime_caret_bridge(
        [sec], cache_dir="c", price_cache_dir="p", target_as_of=target,
        alias_enabled=False, read_source_fn=read_source,
        write_csv_fn=write_csv, verify_fn=verify,
        currentness_cutoff=currentness_cutoff)


def test_caret_bridge_target_minus_one_passes_by_default():
    t1 = drv.acceptable_currentness_cutoff(_GT, 1)
    kept, excl, _summ = _grace_caret("^GSPC", source_date=t1,
                                     currentness_cutoff=t1)
    assert kept == ["^GSPC"] and excl == []


def test_caret_bridge_target_minus_one_fails_with_grace_zero():
    t1 = drv.acceptable_currentness_cutoff(_GT, 1)
    # grace 0 -> cutoff is the strict target; a target-1 source is stale.
    kept, excl, _summ = _grace_caret("^GSPC", source_date=t1,
                                     currentness_cutoff=_GT)
    assert kept == [] and len(excl) == 1
    assert excl[0]["reason"] == "caret_source_unavailable_or_stale"
    # Honesty: the record discloses the actual source date and target.
    assert excl[0]["raw_source_last_date"] == t1
    assert excl[0]["target_as_of"] == _GT


# --- CLI / envelope plumbing ---


def test_negative_currency_grace_rejected(tmp_path):
    rc, out = _run_main([
        "--stackbuilder-root", str(tmp_path / "sb"),
        "--currency-grace-sessions=-1",
    ])
    j = json.loads(out)
    assert rc == 2 and j["status"] == "refused"
    assert j["halted_at"] == "preflight"
    assert any("currency-grace-sessions" in w for w in j["warnings"])


def test_envelope_records_currency_grace_sessions(tmp_path):
    root = tmp_path / "stackbuilder"
    _make_secondary(root, "FOO")
    # Default grace 1: top-level + stageA planning disclose the policy.
    rc, out = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
    ])
    j = json.loads(out)
    assert rc == 0 and j["status"] == "dry_run"
    assert j["currency_grace_sessions"] == 1
    assert j["acceptable_currentness_cutoff"] == \
        drv.acceptable_currentness_cutoff(j["target_as_of"], 1)
    assert j["stageA"]["currency_grace_sessions"] == 1
    assert j["stageA"]["acceptable_currentness_cutoff"] == \
        j["acceptable_currentness_cutoff"]
    # Strict mode: grace 0 -> cutoff equals the target exactly.
    rc0, out0 = _run_main([
        "--stackbuilder-root", str(root),
        "--output-root", str(tmp_path / "out"),
        "--stable-dir", str(tmp_path / "stable"),
        "--currency-grace-sessions", "0",
    ])
    j0 = json.loads(out0)
    assert rc0 == 0 and j0["currency_grace_sessions"] == 0
    assert j0["acceptable_currentness_cutoff"] == j0["target_as_of"]
    assert j0["stageA"]["acceptable_currentness_cutoff"] == j0["target_as_of"]
