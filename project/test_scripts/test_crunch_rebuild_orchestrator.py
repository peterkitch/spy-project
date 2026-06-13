"""Hermetic tests for crunch_rebuild_orchestrator.

The stage invoker and process-conflict check are stubbed; no real engines,
no real registry/cache/output mutation, no network. All paths under tmp_path.
"""
from __future__ import annotations

import json
import os
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import crunch_rebuild_orchestrator as cro  # noqa: E402


NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)

SCRIPT_TO_STAGE = {
    "onepass_workbook_runner.py": "onepass",
    "impactsearch_workbook_runner.py": "impactsearch",
    "stackbuilder_workbook_runner.py": "stackbuilder",
    "k6_recook.py": "k6_recook",
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_lines(path: Path, items) -> None:
    _write(path, "".join(str(x) + "\n" for x in items))


def _make_master(tmp_path: Path, tickers) -> Path:
    p = tmp_path / "global_ticker_library" / "data" / "master_tickers.txt"
    _write(p, ",".join(tickers))
    return p


def _make_secondary_dir(sb_root: Path, sec: str, *, members=None,
                        pinned=False) -> None:
    run_dir = sb_root / sec / ("seed_" + sec)
    run_dir.mkdir(parents=True, exist_ok=True)
    sb = {"secondary": sec, "selected_run_dir": run_dir.as_posix(),
          "operator_pinned": pinned}
    _write(sb_root / sec / "selected_build.json", json.dumps(sb))
    if members is not None:
        _write(run_dir / "combo_k=6.json",
               json.dumps({"K": 6, "Members": members}))


def _xlsx(path: Path, primaries) -> None:
    from openpyxl import Workbook
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append(["Primary Ticker", "Total Capture"])
    for t in primaries:
        ws.append([t, 1.0])
    wb.save(str(path))


def _make_invoker(results, writers=None):
    writers = writers or {}
    calls = []
    env_by_stage = {}

    def inv(script, argv, stage_env=None):
        stage = SCRIPT_TO_STAGE[script]
        env = dict(stage_env or {})
        calls.append((stage, list(argv), env))
        env_by_stage[stage] = env
        w = writers.get(stage)
        if w:
            w()
        return results.get(stage, {"status": "ok"})

    inv.calls = calls
    inv.env_by_stage = env_by_stage
    return inv


def _impactsearch_ok(rebuild):
    """A parity-clean ImpactSearch result: every rebuild secondary reports the
    optimized-runner profile (legacy_fast, no durable validation, zero primary
    yfinance fetches)."""
    return {
        "status": "ok",
        "per_ticker_results": [
            {"secondary": sec, "status": "ok",
             "validation_mode": "legacy_fast",
             "durable_validation_ran": False,
             "primary_yfinance_fetch_count": 0}
            for sec in rebuild
        ],
    }


def _stackbuilder_cfg(**over):
    """The full optimized (Phase 6I-79) effective_config; override individual
    keys via kwargs (use a sentinel to drop a key in tests)."""
    cfg = {
        "skip_durable_validation": True,
        "k_max": 12,
        "exhaustive_k": 4,
        "search": "beam",
        "beam_width": 12,
        "top_n": 20,
        "bottom_n": 20,
        "k_patience": 1,
        "allow_decreasing": True,
        "jobs": 1,
    }
    cfg.update(over)
    return cfg


def _stackbuilder_ok(rebuild, *, cfg=None, summary=None, per=None):
    """A parity-clean StackBuilder result envelope: full optimized
    effective_config, summary.error == 0, every per-secondary status 'ok'."""
    return {
        "status": "ok",
        "effective_config": _stackbuilder_cfg() if cfg is None else cfg,
        "summary": ({"ok": len(rebuild), "error": 0, "total": len(rebuild)}
                    if summary is None else summary),
        "per_secondary_results": ([{"secondary": sec, "status": "ok"}
                                   for sec in rebuild]
                                  if per is None else per),
    }


def _ok_conflict(_patterns):
    return {"status": "ok", "conflicts": []}


def _make_orch(tmp_path, **over):
    """Build an orchestrator with sensible test defaults; override via kwargs."""
    sb_root = tmp_path / "output" / "stackbuilder"
    sb_root.mkdir(parents=True, exist_ok=True)
    defaults = dict(
        project_root=tmp_path,
        run_dir=tmp_path / "output" / "crunch_runs" / "RID",
        blocked_file=tmp_path / "blocked.txt",
        rebuild_file=tmp_path / "rebuild.txt",
        stackbuilder_root=sb_root,
        impactsearch_root=tmp_path / "output" / "impactsearch",
        onepass_root=tmp_path / "output" / "onepass",
        k6_output_root=tmp_path / "output" / "k6_mtf",
        master_tickers_file=_make_master(tmp_path, ["AAA", "BBB", "CCC"]),
        target_as_of="2026-06-03",
        duration_budget_minutes=30,
        operator_budget_label="test-budget",
        allow_network_fetch=True,
        execute=False,
        reclaim_stale_lock=False,
        now=NOW,
        invoker=_make_invoker({}),
        conflict_check=_ok_conflict,
    )
    defaults.update(over)
    return cro.CrunchOrchestrator(**defaults)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_malformed_symbol_stops(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["AAA", "bad ticker!"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    env = _make_orch(tmp_path).run()
    assert env["status"] == "halted"
    assert env["halted_at"] == "preflight"


def test_blank_and_comment_handling(tmp_path):
    _write(tmp_path / "blocked.txt", "# header\nAAA\n\n  \nBBB # inline\n")
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path)
    pre = o.preflight()
    assert pre["exclusion_set"] == ["AAA", "BBB"]


def test_empty_file_stops(tmp_path):
    _write(tmp_path / "blocked.txt", "# only a comment\n")
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    env = _make_orch(tmp_path).run()
    assert env["status"] == "halted" and env["halted_at"] == "preflight"


# ---------------------------------------------------------------------------
# Preflight computations
# ---------------------------------------------------------------------------


def test_unrebuildable_dropped_and_logged(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    _make_secondary_dir(sb_root, "CDTX", members=["AAA[D]"] * 6)
    _make_secondary_dir(sb_root, "AAPB", members=["AAA[D]"] * 6)
    _write_lines(tmp_path / "blocked.txt", ["CDTX", "DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB", "CDTX", "SCHG"])
    o = _make_orch(tmp_path, stackbuilder_root=sb_root)
    pre = o.preflight()
    assert pre["unrebuildable_set"] == ["CDTX"]
    assert "CDTX" not in pre["effective_rebuild_set"]
    assert pre["effective_rebuild_set"] == ["AAPB", "SCHG"]


def test_allowed_universe_is_master_minus_exclusion(tmp_path):
    _make_master(tmp_path, ["AAA", "BBB", "CCC", "DR8A.F"])
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path,
                   master_tickers_file=tmp_path / "global_ticker_library"
                   / "data" / "master_tickers.txt")
    pre = o.preflight()
    allowed = (o.run_dir / "allowed_universe.txt").read_text("utf-8").split()
    assert "DR8A.F" not in allowed
    assert set(allowed) == {"AAA", "BBB", "CCC"}
    assert pre["allowed_universe_size"] == 3


def test_unrebuildable_against_supplied_lists(tmp_path):
    # The real 22 blocked + 41 rebuild: CDTX and MIDZ are current secondaries.
    sb_root = tmp_path / "output" / "stackbuilder"
    for sec in ("CDTX", "MIDZ", "AAPB"):
        _make_secondary_dir(sb_root, sec, members=["AAA[D]"] * 6)
    _write_lines(tmp_path / "blocked.txt",
                 ["CDTX", "MIDZ", "DR8A.F", "CTRA"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB", "CDTX", "MIDZ"])
    o = _make_orch(tmp_path, stackbuilder_root=sb_root)
    pre = o.preflight()
    assert pre["unrebuildable_set"] == ["CDTX", "MIDZ"]
    assert pre["effective_rebuild_set"] == ["AAPB"]


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


def test_lock_acquired_in_dry_run(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path)
    o.run()
    # released at end of dry-run
    assert not o.lock_path.exists()


def test_live_lock_conflict_stops(tmp_path, monkeypatch):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path)
    o.lock_path.parent.mkdir(parents=True, exist_ok=True)
    o.lock_path.write_text(json.dumps({"pid": 4242, "run_id": "OTHER"}),
                           encoding="utf-8")
    monkeypatch.setattr(cro, "_pid_alive", lambda pid: True)
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "lock"


def test_stale_lock_needs_reclaim_flag(tmp_path, monkeypatch):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    monkeypatch.setattr(cro, "_pid_alive", lambda pid: False)
    o = _make_orch(tmp_path)
    o.lock_path.parent.mkdir(parents=True, exist_ok=True)
    o.lock_path.write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "lock"
    # with reclaim flag it proceeds
    o2 = _make_orch(tmp_path, reclaim_stale_lock=True)
    o2.lock_path.parent.mkdir(parents=True, exist_ok=True)
    o2.lock_path.write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    env2 = o2.run()
    assert env2["status"] == "dry_run_planned"


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_writes_plan_and_invokes_nothing(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA", "SECB"])
    inv = _make_invoker({})
    o = _make_orch(tmp_path, invoker=inv)
    env = o.run()
    assert env["status"] == "dry_run_planned"
    assert (o.run_dir / "00_preflight.json").is_file()
    assert (o.run_dir / "run_plan.json").is_file()
    assert inv.calls == []  # no stage invoked


def test_dry_run_stage_commands_carry_exclusion_inputs(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB", "SCHG"])
    o = _make_orch(tmp_path)
    pre = o.preflight()
    cmds = {c["stage"]: c for c in pre["stage_commands"]}
    allowed_file = pre["allowed_universe_file"]
    assert allowed_file in cmds["onepass"]["argv"]
    assert "--tickers-file" in cmds["onepass"]["argv"]
    assert allowed_file in cmds["impactsearch"]["argv"]
    assert "AAPB,SCHG" in cmds["impactsearch"]["argv"]
    assert "AAPB,SCHG" in cmds["stackbuilder"]["argv"]
    assert "--restage-all" in cmds["k6_recook"]["argv"]
    assert "AAPB,SCHG" in cmds["k6_recook"]["argv"]


def test_deterministic_preflight(tmp_path):
    # Same inputs + same run dir + same clock -> byte-identical preflight
    # (sorted keys, stable ticker ordering).
    _write_lines(tmp_path / "blocked.txt", ["BBB", "AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECB", "SECA"])
    o = _make_orch(tmp_path)
    o.preflight()
    a = (o.run_dir / "00_preflight.json").read_text("utf-8")
    o2 = _make_orch(tmp_path)  # default run_dir == same RID
    o2.preflight()
    b = (o2.run_dir / "00_preflight.json").read_text("utf-8")
    assert a == b
    # exclusion + rebuild sets are sorted regardless of input order
    pre = json.loads(a)
    assert pre["exclusion_set"] == ["AAA", "BBB"]


# ---------------------------------------------------------------------------
# Execute gates / conflict
# ---------------------------------------------------------------------------


def test_execute_missing_network_refuses(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path, execute=True, allow_network_fetch=False)
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute_gates"


def test_execute_missing_budget_refuses(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path, execute=True, duration_budget_minutes=None)
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute_gates"


def test_execute_missing_label_refuses(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path, execute=True, operator_budget_label=None)
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute_gates"


def test_process_conflict_blocked_stops(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path,
                   conflict_check=lambda p: {"status": "blocked",
                                            "conflicts": ["onepass.py"]})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "process_conflict"


def test_process_conflict_insufficient_stops_execute(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["AAA"])
    _write_lines(tmp_path / "rebuild.txt", ["SECA"])
    o = _make_orch(tmp_path, execute=True,
                   conflict_check=lambda p: {"status": "insufficient",
                                            "conflicts": []})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "process_conflict"


# ---------------------------------------------------------------------------
# Execute happy path + boundary checks
# ---------------------------------------------------------------------------


def _execute_setup(tmp_path, *, rebuild, blocked, members_clean,
                   writers_override=None, results_override=None,
                   extra_orch=None):
    sb_root = tmp_path / "output" / "stackbuilder"
    imp_root = tmp_path / "output" / "impactsearch"
    op_root = tmp_path / "output" / "onepass"
    k6_root = tmp_path / "output" / "k6_mtf"
    _write_lines(tmp_path / "blocked.txt", blocked)
    _write_lines(tmp_path / "rebuild.txt", rebuild)

    def w_onepass():
        _write(op_root / "onepass.xlsx.manifest.json",
               json.dumps({"current_run_keys": ["AAA", "BBB"]}))

    def w_impactsearch():
        for sec in rebuild:
            _xlsx(imp_root / f"{sec}_analysis.xlsx", ["AAA", "BBB"])

    def w_stackbuilder():
        for sec in rebuild:
            _make_secondary_dir(sb_root, sec, members=members_clean)

    # The orchestrator passes --driver-run-id <run_id>; the default run_dir
    # name is "RID", so k6 writes to k6_root/RID and echoes that path.
    rid = "RID"

    def w_k6():
        rd = k6_root / rid
        _write(rd / "k6_mtf_ranking.json",
               json.dumps({"secondaries_ranked": rebuild}))

    writers = {"onepass": w_onepass, "impactsearch": w_impactsearch,
               "stackbuilder": w_stackbuilder, "k6_recook": w_k6}
    if writers_override:
        writers.update(writers_override)
    results = {"onepass": {"status": "ok"},
               "impactsearch": _impactsearch_ok(rebuild),
               "stackbuilder": _stackbuilder_ok(rebuild),
               "k6_recook": {
                   "status": "ok", "driver_run_id": rid,
                   "stageF": {"ranking_artifact_path":
                              (k6_root / rid / "k6_mtf_ranking.json").as_posix()},
                   "stageA": {}}}
    if results_override:
        results.update(results_override)
    inv = _make_invoker(results, writers)
    o = _make_orch(
        tmp_path, execute=True, stackbuilder_root=sb_root,
        impactsearch_root=imp_root, onepass_root=op_root, k6_output_root=k6_root,
        invoker=inv,
        master_tickers_file=_make_master(tmp_path, ["AAA", "BBB", "CCC"]),
        **(extra_orch or {}),
    )
    return o, inv


def test_execute_happy_path(tmp_path):
    o, inv = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                            members_clean=["AAA[D]", "BBB[I]", "AAA[D]",
                                           "BBB[I]", "AAA[D]", "BBB[I]"])
    env = o.run()
    assert env["status"] == "completed_no_publish"
    assert [s for s, *_ in inv.calls] == ["onepass", "impactsearch",
                                          "stackbuilder", "k6_recook"]
    assert env["publish_attempted"] is False
    assert env["blob_attempted"] is False
    assert env["promotion_attempted"] is False
    for f in ("01_onepass.json", "02_impactsearch.json",
              "03_stackbuilder.json", "04_k6_recook.json", "RUN_SUMMARY.json"):
        assert (o.run_dir / f).is_file()


def test_boundary_onepass_excluded_stops(tmp_path):
    op_root = tmp_path / "output" / "onepass"

    def bad():
        _write(op_root / "onepass.xlsx.manifest.json",
               json.dumps({"current_run_keys": ["AAA", "DR8A.F"]}))

    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          writers_override={"onepass": bad})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_boundary_onepass_per_ticker_results_excluded_stops(tmp_path):
    # Manifest clean, but the runner result's per_ticker_results carries an
    # excluded ticker -> must STOP.
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"onepass": {
            "status": "ok",
            "per_ticker_results": [{"ticker": "AAA", "status": "ok"},
                                   {"ticker": "DR8A.F", "status": "ok"}]}})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_boundary_onepass_result_workbook_manifest_scanned(tmp_path):
    # The runner-reported workbook_path / manifest_path are scanned; an
    # excluded ticker there (canonical manifest clean) must STOP.
    op_root = tmp_path / "output" / "onepass"
    bad_manifest = op_root / "alt_manifest.json"

    def writer():
        _write(op_root / "onepass.xlsx.manifest.json",
               json.dumps({"current_run_keys": ["AAA"]}))  # canonical clean
        _write(bad_manifest, json.dumps({"current_run_keys": ["DR8A.F"]}))

    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        writers_override={"onepass": writer},
        results_override={"onepass": {
            "status": "ok",
            "per_ticker_results": [{"ticker": "AAA", "status": "ok"}],
            "manifest_path": bad_manifest.as_posix()}})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_boundary_impactsearch_excluded_stops(tmp_path):
    imp_root = tmp_path / "output" / "impactsearch"

    def bad():
        _xlsx(imp_root / "AAPB_analysis.xlsx", ["AAA", "DR8A.F"])

    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          writers_override={"impactsearch": bad})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_boundary_stackbuilder_combo_excluded_stops(tmp_path):
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]", "DR8A.F[I]", "AAA[D]", "AAA[D]", "AAA[D]",
                       "AAA[D]"])
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_boundary_stackbuilder_selected_build_excluded_stops(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"

    def bad():
        rd = sb_root / "AAPB" / "seed_AAPB"
        rd.mkdir(parents=True, exist_ok=True)
        _write(sb_root / "AAPB" / "selected_build.json",
               json.dumps({"secondary": "AAPB",
                           "selected_run_dir": rd.as_posix(),
                           "members_note": "DR8A.F"}))
        _write(rd / "combo_k=6.json", json.dumps({"K": 6,
               "Members": ["AAA[D]"] * 6}))

    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          writers_override={"stackbuilder": bad})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_boundary_k6_member_union_excluded_stops(tmp_path):
    k6_root = tmp_path / "output" / "k6_mtf"

    def bad():
        rd = k6_root / "RID"  # the exact current run (matches stageF path)
        _write(rd / "k6_mtf_ranking.json",
               json.dumps({"secondaries_ranked": ["AAPB"],
                           "member_union": ["DR8A.F"]}))

    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          writers_override={"k6_recook": bad})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_k6_resolves_exact_run_via_stagef_not_latest(tmp_path):
    # Real-shaped envelope: stageF.ranking_artifact_path points at the EXACT
    # current run (with an excluded ticker). A lexicographically-later clean
    # dir must NOT mask it (no latest-run fallback).
    k6_root = tmp_path / "output" / "k6_mtf"
    # stale clean dir, lexicographically after "RID"
    _write(k6_root / "ZZZZ_LATER" / "k6_mtf_ranking.json",
           json.dumps({"secondaries_ranked": ["AAPB"]}))

    def bad():
        _write(k6_root / "RID" / "k6_mtf_ranking.json",
               json.dumps({"secondaries_ranked": ["AAPB"],
                           "member_union": ["DR8A.F"]}))

    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          writers_override={"k6_recook": bad})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_k6_missing_artifact_after_ok_stops(tmp_path):
    # k6 returns ok but writes no artifact -> fail-closed STOP (no silent pass).
    def nothing():
        pass

    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          writers_override={"k6_recook": nothing})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_k6_exact_run_via_stagef_passes(tmp_path):
    # driver_run_id == run id AND stageF.ranking_artifact_path == expected
    # file -> resolution passes, clean artifact -> completed.
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6)
    env = o.run()
    assert env["status"] == "completed_no_publish"


def test_k6_stagef_pointing_to_stale_run_stops(tmp_path):
    # stageF path points at a DIFFERENT (stale, existing) run -> STOP even
    # though that file exists and is clean.
    k6_root = tmp_path / "output" / "k6_mtf"
    _write(k6_root / "STALE" / "k6_mtf_ranking.json",
           json.dumps({"secondaries_ranked": ["AAPB"]}))
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"k6_recook": {
            "status": "ok", "driver_run_id": "RID",
            "stageF": {"ranking_artifact_path":
                       (k6_root / "STALE" / "k6_mtf_ranking.json").as_posix()},
            "stageA": {}}})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_k6_run_dir_pointing_to_stale_run_stops(tmp_path):
    k6_root = tmp_path / "output" / "k6_mtf"
    _write(k6_root / "STALE" / "k6_mtf_ranking.json",
           json.dumps({"secondaries_ranked": ["AAPB"]}))
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"k6_recook": {
            "status": "ok", "driver_run_id": "RID",
            "output_run_dir": (k6_root / "STALE").as_posix(), "stageA": {}}})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_k6_driver_run_id_mismatch_stops(tmp_path):
    k6_root = tmp_path / "output" / "k6_mtf"
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"k6_recook": {
            "status": "ok", "driver_run_id": "OTHER",
            "stageF": {"ranking_artifact_path":
                       (k6_root / "RID" / "k6_mtf_ranking.json").as_posix()},
            "stageA": {}}})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_k6_command_includes_driver_run_id(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB"])
    o = _make_orch(tmp_path)
    pre = o.preflight()
    k6 = {c["stage"]: c for c in pre["stage_commands"]}["k6_recook"]
    assert "--driver-run-id" in k6["argv"]
    idx = k6["argv"].index("--driver-run-id")
    assert k6["argv"][idx + 1] == o.run_id


def test_k6_stage_a_exclusion_stops(tmp_path):
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"k6_recook": {
            "status": "ok", "stageA": {"excluded_secondaries": ["AAPB"]}}})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_partial_stage_status_stops(tmp_path):
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"impactsearch": {"status": "partial"}})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------


def test_quarantine_impactsearch_moves_rebuild_only(tmp_path):
    imp_root = tmp_path / "output" / "impactsearch"
    _xlsx(imp_root / "AAPB_analysis.xlsx", ["AAA"])      # rebuild secondary
    _xlsx(imp_root / "OTHER_analysis.xlsx", ["AAA"])     # not rebuilt
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6)
    env = o.run()
    assert env["status"] == "completed_no_publish"
    q = o.run_dir / "quarantine" / "impactsearch" / "AAPB"
    assert (q / "AAPB_analysis.xlsx").is_file()          # moved
    assert (imp_root / "OTHER_analysis.xlsx").is_file()  # untouched


def test_quarantine_stackbuilder_moves_rebuild_only(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    _make_secondary_dir(sb_root, "AAPB", members=["OLD[D]"] * 6)   # rebuild
    _make_secondary_dir(sb_root, "OTHER", members=["AAA[D]"] * 6)  # not rebuilt
    marker = sb_root / "AAPB" / "OLD_MARKER.txt"
    _write(marker, "old")
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6)
    env = o.run()
    assert env["status"] == "completed_no_publish"
    moved_marker = (o.run_dir / "quarantine" / "stackbuilder" / "AAPB"
                    / "OLD_MARKER.txt")
    assert moved_marker.is_file()                       # old dir moved
    assert (sb_root / "OTHER" / "selected_build.json").is_file()  # untouched


def test_pin_blocked_rebuild_stops_before_quarantine(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    _make_secondary_dir(sb_root, "AAPB", members=["AAA[D]"] * 6)
    # pin marker present -> rebuild must refuse BEFORE any quarantine move.
    _write(sb_root / "AAPB" / "selected_build.pinned.json",
           json.dumps({"secondary": "AAPB", "pinned": True}))
    marker = sb_root / "AAPB" / "PIN_MARKER.txt"
    _write(marker, "pinned-do-not-move")
    o, inv = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                            members_clean=["AAA[D]"] * 6)
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    # pin refusal happens at the stackbuilder stage, BEFORE quarantine:
    # the secondary dir is NOT moved and stackbuilder was never invoked.
    assert marker.is_file()
    assert not (o.run_dir / "quarantine" / "stackbuilder" / "AAPB").exists()
    assert "stackbuilder" not in [s for s, *_ in inv.calls]


def test_pin_via_selected_build_flag_stops(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    _make_secondary_dir(sb_root, "AAPB", members=["AAA[D]"] * 6, pinned=True)
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6)
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


def test_run_summary_and_manual_edit_outputs(tmp_path):
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F", "CTRA"],
                          members_clean=["AAA[D]"] * 6)
    o.run()
    manual_txt = (o.run_dir
                  / "broken_tickers_for_manual_master_ticker_edit.txt"
                  ).read_text("utf-8").split()
    assert "DR8A.F" in manual_txt and "CTRA" in manual_txt
    mj = json.loads((o.run_dir
                     / "broken_tickers_for_manual_master_ticker_edit.json"
                     ).read_text("utf-8"))
    assert set(mj["exclusion_set"]) == {"DR8A.F", "CTRA"}
    summ = json.loads((o.run_dir / "RUN_SUMMARY.json").read_text("utf-8"))
    assert summ["publish_attempted"] is False
    assert summ["promotion_attempted"] is False


def test_scanner_catches_embedded_member_form(tmp_path):
    # A seed-dir-name string embeds members as TICKER-D/-I joined by '_'.
    p = tmp_path / "sb.json"
    _write(p, json.dumps({
        "selected_run_dir": "output/stackbuilder/AAPB/"
        "seedTC__KSB.DE-I_SGMR.F-I_VISM-I_VPTDF-D_600509.SS-D_DR8A.F-I"}))
    found = cro.scan_artifact_for_excluded(p, {"DR8A.F"})
    assert "DR8A.F" in found
    # bracket form too
    p2 = tmp_path / "combo.json"
    _write(p2, json.dumps({"Members": ["DR8A.F[I]", "AAA[D]"]}))
    assert "DR8A.F" in cro.scan_artifact_for_excluded(p2, {"DR8A.F"})


def test_scanner_no_substring_false_positive(tmp_path):
    # FORD must NOT match inside FORWARD; DX-Y.NYB must not be split on hyphens.
    p = tmp_path / "x.json"
    _write(p, json.dumps({"name": "FORWARD INDUSTRIES",
                          "members": ["FORWARD[D]", "DX-Y.NYB[I]"]}))
    assert cro.scan_artifact_for_excluded(p, {"FORD"}) == set()
    # but a genuine FORD member IS caught
    p2 = tmp_path / "y.json"
    _write(p2, json.dumps({"seed": "seedTC__FORD-D_AAA-I"}))
    assert "FORD" in cro.scan_artifact_for_excluded(p2, {"FORD"})
    # DX-Y.NYB matched whole, not its hyphen fragments
    assert cro.scan_artifact_for_excluded(p, {"DX"}) == set()
    assert "DX-Y.NYB" in cro.scan_artifact_for_excluded(p, {"DX-Y.NYB"})


# ---------------------------------------------------------------------------
# Optimized-runner parity: stage argv, per-stage env, boundary checks
# ---------------------------------------------------------------------------


def test_impactsearch_stage_argv_and_env_optimized(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB"])
    o = _make_orch(tmp_path)
    o.preflight()
    cmd = o._stage_cmd("impactsearch")
    argv = cmd["argv"]
    # One batch (single --secondaries), full-master primary universe, exclusion
    # by input-withholding -- unchanged.
    assert argv.count("--secondaries") == 1
    assert "master_tickers_file" in argv
    assert argv[argv.index("--primary-source") + 1] == "master_tickers_file"
    # New optimized flags.
    assert "--use-multiprocessing" in argv
    assert argv[argv.index("--validation-mode") + 1] == "legacy_fast"
    # Exact injected env (the six non-secret IMPACT_* config values).
    assert cmd["stage_env"] == {
        "IMPACT_REQUIRE_ZERO_PRIMARY_YF": "1",
        "IMPACT_INSTRUMENT_YF_CALLS": "1",
        "IMPACT_TRUST_LIBRARY": "1",
        "IMPACT_TRUST_MAX_AGE_HOURS": "720",
        "IMPACT_CALENDAR_GRACE_DAYS": "30",
        "IMPACT_MAX_WORKERS": "8",
    }
    assert cmd["stage_env"] == cro.IMPACTSEARCH_STAGE_ENV


def test_non_impactsearch_stages_have_empty_env(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB"])
    o = _make_orch(tmp_path)
    o.preflight()
    for stage in ("onepass", "stackbuilder", "k6_recook"):
        assert o._stage_cmd(stage)["stage_env"] == {}


def test_stackbuilder_stage_argv_k12_parity(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB"])
    o = _make_orch(tmp_path)
    o.preflight()
    cmd = o._stage_cmd("stackbuilder")
    argv = cmd["argv"]
    assert "--skip-durable-validation" in argv
    assert argv[argv.index("--jobs") + 1] == "1"
    assert argv[argv.index("--k-max") + 1] == "12"
    assert argv[argv.index("--exhaustive-k") + 1] == "4"
    assert argv[argv.index("--search") + 1] == "beam"
    assert argv[argv.index("--beam-width") + 1] == "12"
    assert argv[argv.index("--top-n") + 1] == "20"
    assert argv[argv.index("--bottom-n") + 1] == "20"
    assert "--allow-decreasing" in argv
    assert argv[argv.index("--k-patience") + 1] == "1"
    assert "--no-progress" in argv
    # Rationale recorded for operator review.
    assert "K1-K12" in cmd["parity_rationale"]
    assert cmd["parity_rationale"] == cro.STACKBUILDER_PARITY_RATIONALE


def test_run_plan_records_env_and_rationale(tmp_path):
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB"])
    o = _make_orch(tmp_path)
    o.run()  # dry-run (execute defaults False) -> writes run_plan.json
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    by_stage = {c["stage"]: c for c in plan["stage_commands"]}
    assert by_stage["impactsearch"]["stage_env"]["IMPACT_MAX_WORKERS"] == "8"
    assert "--use-multiprocessing" in by_stage["impactsearch"]["argv"]
    assert "--k-max" in by_stage["stackbuilder"]["argv"]
    assert by_stage["stackbuilder"]["parity_rationale"] == \
        cro.STACKBUILDER_PARITY_RATIONALE
    # No publication / promotion / commit / push / deploy path in the plan.
    blob = json.dumps(plan).lower()
    for word in ("publish", "promot", "blob", "commit", "push", "deploy"):
        assert word not in blob


def test_impactsearch_env_injected_at_execute(tmp_path):
    o, inv = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                            members_clean=["AAA[D]"] * 6)
    env = o.run()
    assert env["status"] == "completed_no_publish"
    # The injected env reached the invoker for ImpactSearch only.
    assert inv.env_by_stage["impactsearch"] == cro.IMPACTSEARCH_STAGE_ENV
    assert inv.env_by_stage["onepass"] == {}
    assert inv.env_by_stage["stackbuilder"] == {}
    assert inv.env_by_stage["k6_recook"] == {}


def test_default_invoker_merges_stage_env_over_parent(tmp_path, monkeypatch):
    """Direct hermetic regression for the real _default_invoker env merge:
    env = {**os.environ, **stage_env}. subprocess.run is stubbed so NO real
    subprocess/engine is launched; os.environ is a controlled fixture and must
    not be mutated by the merge."""
    o = _make_orch(tmp_path)  # project_root == tmp_path

    # Controlled parent environment (a plain dict standing in for os.environ).
    parent_env = {"PARENT_ONLY": "parent", "OVERRIDE_ME": "parent_value"}
    monkeypatch.setattr(cro.os, "environ", parent_env)

    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(
            stdout='noise\n{"status": "ok", "echo": 7}\n', returncode=0)

    monkeypatch.setattr(cro.subprocess, "run", fake_run)

    stage_env = {"OVERRIDE_ME": "stage_value", "STAGE_ONLY": "stage"}
    result = o._default_invoker("some_runner.py", ["--flag", "val"], stage_env)

    # --- subprocess.run was the stub; no real process launched ---------------
    assert "kwargs" in captured  # fake_run ran exactly via our stub
    kwargs = captured["kwargs"]

    # --- env= mapping present and correctly merged ---------------------------
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["PARENT_ONLY"] == "parent"        # parent key preserved
    assert env["OVERRIDE_ME"] == "stage_value"   # stage overrides parent
    assert env["STAGE_ONLY"] == "stage"          # stage adds a new key

    # --- os.environ (the parent fixture) is NOT mutated ----------------------
    assert env is not parent_env
    assert parent_env["OVERRIDE_ME"] == "parent_value"
    assert "STAGE_ONLY" not in parent_env
    assert cro.os.environ is parent_env  # same object, untouched

    # --- argv/script plumbing remains correct --------------------------------
    cmd = captured["args"][0]
    assert cmd[0] == cro.sys.executable
    assert cmd[1] == str(tmp_path / "some_runner.py")
    assert cmd[2:] == ["--flag", "val"]

    # --- capture_output/text contract preserved ------------------------------
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True

    # --- stdout JSON parsing contract preserved (last JSON object) -----------
    assert result == {"status": "ok", "echo": 7}


def test_default_invoker_empty_stage_env_is_plain_parent(tmp_path, monkeypatch):
    """An empty/None stage_env yields exactly os.environ (a copy, not a
    mutation) -- the unchanged path for non-ImpactSearch stages."""
    o = _make_orch(tmp_path)
    parent_env = {"PARENT_ONLY": "parent", "OVERRIDE_ME": "parent_value"}
    monkeypatch.setattr(cro.os, "environ", parent_env)

    captured = {}

    def fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(stdout='{"status": "ok"}', returncode=0)

    monkeypatch.setattr(cro.subprocess, "run", fake_run)

    result = o._default_invoker("runner.py", [], {})
    env = captured["kwargs"]["env"]
    assert env == parent_env       # same contents
    assert env is not parent_env   # but a fresh dict (no aliasing)
    assert result == {"status": "ok"}


def _impact_bad(rebuild, **bad):
    res = _impactsearch_ok(rebuild)
    res["per_ticker_results"][0].update(bad)
    return res


@pytest.mark.parametrize("bad", [
    {"validation_mode": "durable"},
    {"durable_validation_ran": True},
    {"primary_yfinance_fetch_count": 3},
])
def test_impactsearch_parity_boundary_stops(tmp_path, bad):
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB", "AAPU"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"impactsearch": _impact_bad(["AAPB", "AAPU"], **bad)})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_impactsearch_parity_missing_field_stops(tmp_path):
    res = _impactsearch_ok(["AAPB"])
    del res["per_ticker_results"][0]["primary_yfinance_fetch_count"]
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"impactsearch": res})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_impactsearch_parity_empty_results_stops(tmp_path):
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"impactsearch": {"status": "ok",
                                           "per_ticker_results": []}})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_impactsearch_parity_bad_aggregate_stops(tmp_path):
    # A top-level aggregate field present but inconsistent -> STOP, even when
    # the per-secondary entries are clean.
    res = _impactsearch_ok(["AAPB"])
    res["validation_mode"] = "durable"
    res["durable_validation_ran"] = True
    res["primary_yfinance_fetch_count"] = 1
    o, _ = _execute_setup(
        tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
        members_clean=["AAA[D]"] * 6,
        results_override={"impactsearch": res})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"


def test_k6_recook_stage_argv_unchanged(tmp_path):
    # Change 4: k6_recook still uses --secondaries / --driver-run-id /
    # --restage-all and is unaffected by the StackBuilder K12 build.
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB"])
    o = _make_orch(tmp_path)
    o.preflight()
    argv = o._stage_cmd("k6_recook")["argv"]
    assert "--secondaries" in argv
    assert "--driver-run-id" in argv
    assert "--restage-all" in argv


def test_k6_recook_argv_has_caret_alias_not_stage_a_exclusions(tmp_path):
    # Caret-secondary parity: Build & Rank recook passes the LOCAL Aprime
    # caret-cache-alias bridge flag (matching the re-rank driver) so an
    # existing-board caret secondary (e.g. ^DJT) can build, while the
    # orchestrator KEEPS its stricter Stage-A hard-stop -- it must NOT pass
    # --allow-stage-a-exclusions. No other recook flags change.
    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB"])
    o = _make_orch(tmp_path)
    o.preflight()
    argv = o._stage_cmd("k6_recook")["argv"]
    # the one added flag, placed adjacent to --restage-all
    assert "--allow-aprime-caret-cache-alias" in argv
    assert argv[argv.index("--restage-all") + 1] == \
        "--allow-aprime-caret-cache-alias"
    # the intentional omission is preserved (no silent member shrink)
    assert "--allow-stage-a-exclusions" not in argv
    # no unrelated argv changes: every prior recook flag still present, once
    for flag in ("--execute", "--allow-network-fetch", "--secondaries",
                 "--driver-run-id", "--stackbuilder-root", "--output-root",
                 "--restage-all"):
        assert argv.count(flag) == 1


# ---------------------------------------------------------------------------
# OnePass reuse (opt-in, fail-closed)
# ---------------------------------------------------------------------------


def _write_prior_run(prior_dir, *, allowed_lines=("AAA", "BBB", "CCC"),
                     status="ok", summary="auto", per="auto",
                     end_ts="2026-06-06T00:00:00Z", with_allowed=True,
                     with_json=True, extra=None):
    """Build a tmp prior crunch-run dir with a 01_onepass.json + matching
    allowed_universe.txt, shaped to the real field contract."""
    prior_dir.mkdir(parents=True, exist_ok=True)
    if with_allowed:
        _write(prior_dir / "allowed_universe.txt",
               "".join(t + "\n" for t in allowed_lines))
    if summary == "auto":
        summary = {"error": 0, "ok": len(allowed_lines),
                   "total": len(allowed_lines)}
    if per == "auto":
        per = [{"ticker": t, "status": "ok", "metrics_count": 1}
               for t in allowed_lines]
    doc = {"status": status, "summary": summary, "per_ticker_results": per,
           "start_timestamp_utc": "2026-06-05T00:00:00Z"}
    if end_ts is not None:
        doc["end_timestamp_utc"] = end_ts
    if extra:
        doc.update(extra)
    if with_json:
        _write(prior_dir / "01_onepass.json", json.dumps(doc))
    return prior_dir


def _reuse_dry(tmp_path, prior, *, blocked=("DR8A.F",), rebuild=("AAPB",),
               max_age=168, inv=None):
    _write_lines(tmp_path / "blocked.txt", list(blocked))
    _write_lines(tmp_path / "rebuild.txt", list(rebuild))
    over = dict(reuse_onepass_run_dir=prior, reuse_onepass_max_age_hours=max_age)
    if inv is not None:
        over["invoker"] = inv
    o = _make_orch(tmp_path, **over)
    return o, o.run()


def plan_reuse(o):
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    return plan["onepass_reuse"]


def test_reuse_valid_dry_run_plan(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run")
    inv = _make_invoker({})
    o, env = _reuse_dry(tmp_path, prior, inv=inv)
    assert env["status"] == "dry_run_planned"
    assert inv.calls == []  # no stage invoked in dry-run
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    reuse = plan["onepass_reuse"]
    assert reuse["valid"] is True
    assert reuse["no_onepass_subprocess"] is True
    assert reuse["universe_match"] is True
    assert reuse["blocked_scan_result"] == "clean"
    assert reuse["timestamp_source"] == "end_timestamp_utc"
    assert reuse["age_hours"] >= 0
    assert reuse["freshness_window_hours"] == 168
    assert "summary_text" in reuse
    assert plan["stage1_onepass"]["action"] == "reused"
    assert plan["stage1_onepass"]["onepass_subprocess_invoked"] is False
    # Stage 2/3/4 optimized commands unchanged.
    by_stage = {c["stage"]: c for c in plan["stage_commands"]}
    imp = by_stage["impactsearch"]["argv"]
    assert "--use-multiprocessing" in imp
    assert imp[imp.index("--validation-mode") + 1] == "legacy_fast"
    assert by_stage["impactsearch"]["stage_env"]["IMPACT_MAX_WORKERS"] == "8"
    sb = by_stage["stackbuilder"]["argv"]
    assert sb[sb.index("--k-max") + 1] == "12"
    assert "--skip-durable-validation" in sb
    k6 = by_stage["k6_recook"]["argv"]
    assert "--restage-all" in k6
    assert "--driver-run-id" in k6


def test_reuse_valid_execute_skips_onepass(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run")
    o, inv = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                            members_clean=["AAA[D]"] * 6,
                            extra_orch={"reuse_onepass_run_dir": prior})
    env = o.run()
    assert env["status"] == "completed_no_publish"
    stages = [s for s, *_ in inv.calls]
    assert "onepass" not in stages
    assert stages == ["impactsearch", "stackbuilder", "k6_recook"]
    assert (o.run_dir / "00_onepass_reuse_proof.json").is_file()
    r1 = json.loads((o.run_dir / "01_onepass.json").read_text("utf-8"))
    assert r1["mode"] == "reused"
    assert r1["status"] == "ok"
    assert r1["proof"]["valid"] is True
    # The prior run dir was not mutated (only the two seed files exist).
    assert sorted(p.name for p in prior.iterdir()) == [
        "01_onepass.json", "allowed_universe.txt"]


def test_reuse_missing_dir_stops(tmp_path):
    _, env = _reuse_dry(tmp_path, tmp_path / "does_not_exist")
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"


def test_reuse_dir_equals_current_run_dir_refuses_before_any_write(tmp_path):
    # FIX 1: prior dir == current run dir must refuse BEFORE any run-dir write,
    # leaving the prior evidence byte-identical and writing nothing.
    rd = tmp_path / "output" / "crunch_runs" / "RID"
    _write_prior_run(rd)
    marker = rd / "OPERATOR_MARKER.txt"
    _write(marker, "do-not-touch")
    au_bytes = (rd / "allowed_universe.txt").read_bytes()
    op_bytes = (rd / "01_onepass.json").read_bytes()
    marker_bytes = marker.read_bytes()
    before = sorted(p.name for p in rd.iterdir())

    _write_lines(tmp_path / "blocked.txt", ["DR8A.F"])
    _write_lines(tmp_path / "rebuild.txt", ["AAPB"])
    inv = _make_invoker({})
    o = _make_orch(tmp_path, run_dir=rd, reuse_onepass_run_dir=rd, invoker=inv)
    env = o.run()

    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    assert "current run dir" in env["reason"]
    # Sentinel files byte-identical.
    assert (rd / "allowed_universe.txt").read_bytes() == au_bytes
    assert (rd / "01_onepass.json").read_bytes() == op_bytes
    assert marker.read_bytes() == marker_bytes
    # Nothing new written into the dir.
    assert sorted(p.name for p in rd.iterdir()) == before
    for generated in ("00_preflight.json", "run_plan.json", "RUN_SUMMARY.json",
                      "broken_tickers_for_manual_master_ticker_edit.txt",
                      "broken_tickers_for_manual_master_ticker_edit.json"):
        assert not (rd / generated).exists()
    # No lock file (lock lives at run_dir.parent / .crunch.lock).
    assert not (rd.parent / ".crunch.lock").exists()
    # No stage invoker call.
    assert inv.calls == []


def test_reuse_missing_onepass_json_stops(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run", with_json=False)
    _, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"


def test_reuse_missing_allowed_universe_stops(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run", with_allowed=False)
    _, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"


def test_reuse_status_not_ok_stops(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run", status="partial")
    _, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"


def test_reuse_summary_error_stops(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run",
                             summary={"error": 2, "ok": 1, "total": 3})
    _, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"


def test_reuse_per_ticker_error_stops(tmp_path):
    # summary.error == 0 but a per-ticker result is non-ok -> STOP.
    per = [{"ticker": "AAA", "status": "ok"},
           {"ticker": "BBB", "status": "error"},
           {"ticker": "CCC", "status": "ok"}]
    prior = _write_prior_run(tmp_path / "prior_run",
                             summary={"error": 0, "ok": 3, "total": 3},
                             per=per)
    _, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"


def test_reuse_coverage_summary_total_too_small_stops(tmp_path):
    # FIX 2: prior allowed_universe matches, error==0, but summary.total < N.
    prior = _write_prior_run(tmp_path / "prior_run",
                             summary={"error": 0, "ok": 3, "total": 2})
    o, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    assert plan["onepass_reuse"]["coverage_match"] is False


def test_reuse_coverage_summary_ok_too_small_stops(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run",
                             summary={"error": 0, "ok": 2, "total": 3})
    o, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    assert plan_reuse(o)["coverage_match"] is False


def test_reuse_coverage_per_ticker_missing_stops_with_delta(tmp_path):
    # per_ticker_results omits an allowed ticker (BBB) -> STOP with delta.
    per = [{"ticker": "AAA", "status": "ok"},
           {"ticker": "CCC", "status": "ok"}]
    prior = _write_prior_run(tmp_path / "prior_run",
                             summary={"error": 0, "ok": 3, "total": 3},
                             per=per)
    o, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    delta = plan_reuse(o)["coverage_delta"]
    assert delta["missing_from_result"] == ["BBB"]
    assert delta["per_ticker_count"] == 2


def test_reuse_coverage_per_ticker_wrong_order_stops(tmp_path):
    # Right set, wrong order -> STOP (ordered allowed_universe contract).
    per = [{"ticker": "BBB", "status": "ok"},
           {"ticker": "AAA", "status": "ok"},
           {"ticker": "CCC", "status": "ok"}]
    prior = _write_prior_run(tmp_path / "prior_run",
                             summary={"error": 0, "ok": 3, "total": 3},
                             per=per)
    o, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    delta = plan_reuse(o)["coverage_delta"]
    assert delta["same_set_order_differs"] is True
    assert delta["missing_from_result"] == []
    assert delta["extra_in_result"] == []


def test_reuse_allowed_universe_read_error_stops(tmp_path, monkeypatch):
    # FIX 3: an OSError reading prior allowed_universe.txt (after the existence
    # check) fails closed rather than leaking an uncaught exception.
    prior = _write_prior_run(tmp_path / "prior_run")
    orig_read_text = cro.Path.read_text

    def fake_read_text(self, *a, **k):
        if self.name == "allowed_universe.txt":
            raise OSError("simulated read failure")
        return orig_read_text(self, *a, **k)

    monkeypatch.setattr(cro.Path, "read_text", fake_read_text)
    o, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    assert "unreadable" in env["reason"]


def test_reuse_universe_mismatch_stops_with_delta(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run",
                             allowed_lines=("AAA", "BBB"))  # missing CCC
    o, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    delta = plan["onepass_reuse"]["universe_delta"]
    assert delta["in_current_not_prior"] == ["CCC"]
    assert plan["onepass_reuse"]["universe_match"] is False


def test_reuse_blocked_in_prior_allowed_universe_stops(tmp_path):
    # Block BBB (a real master member); the prior allowed_universe still lists
    # BBB -> blocked-ticker leak -> STOP.
    prior = _write_prior_run(tmp_path / "prior_run",
                             allowed_lines=("AAA", "BBB", "CCC"))
    o, env = _reuse_dry(tmp_path, prior, blocked=("BBB",))
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    scan = plan["onepass_reuse"]["blocked_ticker_scan"]
    assert "BBB" in scan["in_allowed_universe"]


def test_reuse_blocked_in_onepass_result_stops(tmp_path):
    # Block BBB; prior allowed_universe excludes BBB (matches current), but the
    # reused OnePass per_ticker_results still mentions BBB -> STOP.
    per = [{"ticker": "AAA", "status": "ok"},
           {"ticker": "CCC", "status": "ok"},
           {"ticker": "BBB", "status": "ok"}]
    prior = _write_prior_run(tmp_path / "prior_run",
                             allowed_lines=("AAA", "CCC"),
                             summary={"error": 0, "ok": 3, "total": 3},
                             per=per)
    o, env = _reuse_dry(tmp_path, prior, blocked=("BBB",))
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    scan = plan["onepass_reuse"]["blocked_ticker_scan"]
    assert "BBB" in scan["in_onepass_result"]


def test_reuse_stale_explicit_timestamp_stops(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run",
                             end_ts="2026-04-01T00:00:00Z")  # > 168h before NOW
    o, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    assert plan["onepass_reuse"]["fresh"] is False
    assert plan["onepass_reuse"]["timestamp_source"] == "end_timestamp_utc"


def _set_mtime(path, dt):
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


def test_reuse_unparseable_timestamp_falls_back_to_fresh_mtime(tmp_path):
    # No parseable explicit timestamp -> mtime fallback; fresh mtime PASSES.
    prior = _write_prior_run(tmp_path / "prior_run", end_ts="not-a-timestamp")
    _set_mtime(prior / "01_onepass.json",
               datetime(2026, 6, 6, 9, 0, 0, tzinfo=timezone.utc))  # 3h old
    inv = _make_invoker({})
    o, env = _reuse_dry(tmp_path, prior, inv=inv)
    assert env["status"] == "dry_run_planned"
    assert inv.calls == []
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    reuse = plan["onepass_reuse"]
    assert reuse["valid"] is True
    assert reuse["timestamp_source"] == "file_mtime"


def test_reuse_stale_mtime_fallback_stops(tmp_path):
    prior = _write_prior_run(tmp_path / "prior_run", end_ts=None)  # no explicit
    _set_mtime(prior / "01_onepass.json",
               datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc))  # >168h
    o, env = _reuse_dry(tmp_path, prior)
    assert env["status"] == "halted" and env["halted_at"] == "reuse_onepass"
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    assert plan["onepass_reuse"]["timestamp_source"] == "file_mtime"
    assert plan["onepass_reuse"]["fresh"] is False


def test_non_reuse_default_still_invokes_onepass(tmp_path):
    # Regression: without --reuse-onepass-run-dir, OnePass runs as today and is
    # the first invoked stage; no reuse metadata is emitted.
    o, inv = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                            members_clean=["AAA[D]"] * 6)
    env = o.run()
    assert env["status"] == "completed_no_publish"
    stages = [s for s, *_ in inv.calls]
    assert stages[0] == "onepass"
    plan = json.loads((o.run_dir / "run_plan.json").read_text("utf-8"))
    assert "onepass_reuse" not in plan
    assert "stage1_onepass" not in plan


# ---------------------------------------------------------------------------
# StackBuilder runtime parity assertion (build-shape, result-envelope only)
# ---------------------------------------------------------------------------


def test_stackbuilder_parity_pass(tmp_path):
    o = _make_orch(tmp_path)
    # Full optimized envelope, multiple secondaries -> must NOT raise.
    o._assert_stackbuilder_parity(_stackbuilder_ok(["AAPB", "AAPU"]))


@pytest.mark.parametrize("key,bad", [
    ("skip_durable_validation", False),
    ("k_max", 6),
    ("exhaustive_k", 3),
    ("search", "exhaustive"),
    ("beam_width", 8),
    ("top_n", 10),
    ("bottom_n", 10),
    ("k_patience", 2),
    ("allow_decreasing", False),
    ("jobs", 4),
])
def test_stackbuilder_parity_wrong_config_value_stops(tmp_path, key, bad):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"], cfg=_stackbuilder_cfg(**{key: bad}))
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


@pytest.mark.parametrize("key", [
    "skip_durable_validation", "k_max", "exhaustive_k", "search",
    "beam_width", "top_n", "bottom_n", "k_patience", "allow_decreasing", "jobs",
])
def test_stackbuilder_parity_missing_config_key_stops(tmp_path, key):
    o = _make_orch(tmp_path)
    cfg = _stackbuilder_cfg()
    del cfg[key]
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(_stackbuilder_ok(["AAPB"], cfg=cfg))


def test_stackbuilder_parity_missing_effective_config_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"])
    del r["effective_config"]
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_non_mapping_effective_config_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"])
    r["effective_config"] = ["not", "a", "dict"]
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_missing_summary_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"])
    del r["summary"]
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_non_mapping_summary_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"], summary=["error", 0])
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_missing_summary_error_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"], summary={"ok": 1, "total": 1})
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_summary_error_positive_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"], summary={"ok": 0, "error": 1, "total": 1})
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_missing_per_results_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"])
    del r["per_secondary_results"]
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_non_list_per_results_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"], per={"secondary": "AAPB", "status": "ok"})
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_empty_per_results_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"], per=[])
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_non_object_per_result_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"], per=["AAPB"])
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_per_result_missing_status_stops(tmp_path):
    o = _make_orch(tmp_path)
    r = _stackbuilder_ok(["AAPB"], per=[{"secondary": "AAPB"}])
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(r)


def test_stackbuilder_parity_per_result_non_ok_stops(tmp_path):
    o = _make_orch(tmp_path)
    per = [{"secondary": "AAPB", "status": "ok"},
           {"secondary": "AAPU", "status": "error"}]
    with pytest.raises(cro.CrunchError):
        o._assert_stackbuilder_parity(_stackbuilder_ok(["AAPB", "AAPU"],
                                                       per=per))


def test_execute_stops_on_stackbuilder_parity_before_k6(tmp_path):
    # status == "ok" passes _require_ok, but bad parity must halt the run at
    # the execute stage, before StackBuilder is checkpointed and before k6 runs.
    bad_sb = _stackbuilder_ok(["AAPB"], cfg=_stackbuilder_cfg(k_max=6))
    o, inv = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                            members_clean=["AAA[D]"] * 6,
                            results_override={"stackbuilder": bad_sb})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    stages = [s for s, *_ in inv.calls]
    assert "stackbuilder" in stages       # _require_ok passed (status ok)
    assert "k6_recook" not in stages      # halted before Stage 4
    assert env.get("checkpoints", {}).get("stackbuilder") is None
    assert not (o.run_dir / "03_stackbuilder.json").is_file()


def test_execute_stackbuilder_parity_runs_before_exclusion_scan(tmp_path):
    # Both a parity violation AND an excluded ticker in the StackBuilder
    # artifact: parity is asserted BEFORE the exclusion scan, so the halt
    # reason is the parity failure, not the exclusion failure.
    sb_root = tmp_path / "output" / "stackbuilder"
    bad_sb = _stackbuilder_ok(["AAPB"], cfg=_stackbuilder_cfg(jobs=4))

    def bad_artifact():
        _make_secondary_dir(sb_root, "AAPB", members=["DR8A.F[D]"] * 6)

    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          results_override={"stackbuilder": bad_sb},
                          writers_override={"stackbuilder": bad_artifact})
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert "stackbuilder parity" in env["reason"]   # parity fired first


# ---------------------------------------------------------------------------
# Publish-dry-run tail (Stages 5-8) -- publication boundary stays CLOSED
# ---------------------------------------------------------------------------

import crunch_combine_proof as ccp  # noqa: E402


def _valid_fresh_row(sec):
    """A built-only v2 join row with PROJECT-RELATIVE output/ path fields (the
    promote-clean form)."""
    return {
        "secondary": sec,
        "history_artifact_path": f"output/k6_mtf/RID/{sec}/k6_mtf_history.json",
        "k6_stack": {
            "selected_build_path": f"output/stackbuilder/{sec}/selected_build.json",
            "selected_run_dir": f"output/stackbuilder/{sec}/seed_{sec}",
            "combo_k6_path":
                f"output/stackbuilder/{sec}/seed_{sec}/combo_k=6.json",
        },
    }


def _publish_stubs(rebuild, *, sidecar_over=None, make_row=None, join_rows=None):
    eff = sorted(rebuild)
    make_row = make_row or _valid_fresh_row

    def validator(secondaries, run_id):
        validator.calls.append((list(secondaries), run_id))
        sc = {"validation_status": "valid", "run_id": run_id,
              "strategies": [{"strategy_id": f"k6_mtf:{s}"} for s in eff]}
        if sidecar_over:
            sc = sidecar_over(sc)
        return sc
    validator.calls = []

    def joiner(ranking_path, sidecar_path, sidecar_sha):
        joiner.calls.append((str(ranking_path), str(sidecar_path), sidecar_sha))
        rows = join_rows if join_rows is not None else [make_row(s) for s in eff]
        return {"per_secondary": rows}
    joiner.calls = []

    def combiner(**kw):
        combiner.calls.append(kw)
        return {
            "paths": {"merged_fixture": "output/run/publish/merged.json"},
            "merged_row_count": len(eff) + 5,
            "board_validated_count": 3, "not_validated_count": len(eff) + 2,
            "carried_count": 5, "fresh_count": len(eff),
            "net_new_count": len(eff), "stage_a_excluded_count": 0,
            "ccc_record_count": len(eff) + 5,
        }
    combiner.calls = []
    return validator, joiner, combiner


def _ccc_file(tmp_path, rebuild):
    p = tmp_path / "fresh_ccc_records.json"
    _write(p, json.dumps([{"secondary": s, "get_verified": True} for s in rebuild]))
    return p


def _publish_extra(validator, joiner, combiner, *, ccc_file=None, prior=None):
    extra = {"publish_dry_run": True, "validator": validator,
             "joiner": joiner, "combiner": combiner}
    if ccc_file is not None:
        extra["publish_fresh_ccc_records_file"] = ccc_file
    if prior:
        extra.update(prior)
    return extra


def test_publish_dry_run_off_keeps_completed_no_publish(tmp_path):
    val, joi, comb = _publish_stubs(["AAPB"])
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch={"validator": val, "joiner": joi,
                                      "combiner": comb})
    env = o.run()
    assert env["status"] == "completed_no_publish"
    for f in ("05_validation.json", "06_join.json", "07_combine.json",
              "08_publish_gate.json"):
        assert not (o.run_dir / f).exists()
    assert val.calls == [] and joi.calls == [] and comb.calls == []


def test_publish_dry_run_with_ccc_runs_stages_5_to_8(tmp_path):
    ccc = _ccc_file(tmp_path, ["AAPB"])
    val, joi, comb = _publish_stubs(["AAPB"])
    prior = {
        "publish_prior_fixture": tmp_path / "prior" / "fix.json",
        "publish_prior_promotion_manifest": tmp_path / "prior" / "promo.json",
        "publish_prior_validation_sidecar": tmp_path / "prior" / "sidecar.json",
        "publish_prior_ccc_verification_manifest": tmp_path / "prior" / "ccc.json",
    }
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb, ccc_file=ccc,
                                                    prior=prior))
    env = o.run()
    assert env["status"] == "completed_publish_dry_run"
    assert env["publish_attempted"] is False
    assert env["blob_attempted"] is False
    assert env["promotion_attempted"] is False
    for f in ("05_validation.json", "06_join.json", "07_combine.json",
              "08_publish_gate.json"):
        assert (o.run_dir / f).is_file()
    # seams invoked with the right inputs
    assert val.calls == [(["AAPB"], "RID")]
    assert len(comb.calls) == 1
    kw = comb.calls[0]
    assert kw["assembly_run_id"] == "RID"
    assert [r["secondary"] for r in kw["fresh_rows"]] == ["AAPB"]
    assert kw["fresh_validation_sidecar"]["run_id"] == "RID"
    assert kw["fresh_ccc_records"] == [{"secondary": "AAPB", "get_verified": True}]
    assert kw["prior_fixture_path"] == prior["publish_prior_fixture"]
    assert kw["prior_validation_sidecar_path"] == prior[
        "publish_prior_validation_sidecar"]
    assert kw["excluded_tickers"] == ("DR8A.F",)
    assert kw["project_root"] == tmp_path
    assert Path(kw["output_dir"]) == o.run_dir / "publish_candidate"
    # join used the run-id-bound k6 ranking artifact
    ranking = (tmp_path / "output" / "k6_mtf" / "RID" / "k6_mtf_ranking.json")
    assert joi.calls[0][0] == str(ranking)
    # Stage 8 gate explicitly records the closed boundary.
    gate = json.loads((o.run_dir / "08_publish_gate.json").read_text("utf-8"))
    for flag in ("no_blob_upload", "no_blob_get", "no_promote_cli_invoked",
                 "no_promote_write", "no_operator_approved",
                 "no_public_fixture_write", "no_commit", "no_push", "no_deploy"):
        assert gate[flag] is True
    assert gate["status"] == "ok"
    # nothing under frontend/public was created
    assert not (tmp_path / "frontend" / "public").exists()


def test_publish_dry_run_without_ccc_blocks_at_stage_7(tmp_path):
    val, joi, comb = _publish_stubs(["AAPB"])
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb, ccc_file=None))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    # Stage 5/6 ran; Stage 7 blocked before combine; gate written.
    assert (o.run_dir / "05_validation.json").is_file()
    assert (o.run_dir / "06_join.json").is_file()
    blocked = json.loads((o.run_dir / "07_combine.json").read_text("utf-8"))
    assert blocked["status"] == "blocked" and blocked["combine_called"] is False
    assert blocked["no_blob_upload"] is True and blocked["no_blob_get"] is True
    gate = json.loads((o.run_dir / "08_publish_gate.json").read_text("utf-8"))
    assert gate["status"] == "blocked"
    assert comb.calls == []  # combine never called
    assert not (tmp_path / "frontend" / "public").exists()


@pytest.mark.parametrize("mutate", [
    lambda sc: {**sc, "strategies": []},                                  # empty
    lambda sc: {**sc, "strategies": [{"strategy_id": "k6_mtf:OTHER"}]},   # wrong
    lambda sc: {**sc, "strategies": sc["strategies"] + [{"strategy_id":
              "k6_mtf:EXTRA"}]},                                         # extra
    lambda sc: {**sc, "strategies": sc["strategies"] + sc["strategies"]},  # dup
    lambda sc: {**sc, "strategies": [{"strategy_id": "bad_id"}]},         # malformed
    lambda sc: {**sc, "run_id": "WRONG_RUN"},                            # run mismatch
    lambda sc: {**sc, "validation_status": "partial"},                   # not valid
])
def test_publish_dry_run_stage5_sidecar_validation_stops(tmp_path, mutate):
    val, joi, comb = _publish_stubs(["AAPB"], sidecar_over=mutate)
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert comb.calls == []  # never reached combine


def test_publish_dry_run_stage6_empty_join_stops(tmp_path):
    val, joi, comb = _publish_stubs(["AAPB"])

    def empty_join(ranking_path, sidecar_path, sidecar_sha):
        return {"per_secondary": []}
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, empty_join, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert comb.calls == []


def test_publish_dry_run_combine_error_halts(tmp_path):
    val, joi, comb = _publish_stubs(["AAPB"])

    def boom(**kw):
        raise ccp.CombineError("synthetic combine failure")
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, boom,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert "combine/proof assembly failed" in env["reason"]


def test_publish_dry_run_stage6_validation_join_error_halts(tmp_path):
    # The REAL ValidationJoinError (what the default joiner raises) must route
    # through the existing CrunchError/_halt envelope, not escape.
    from utils.react_publish.k6_mtf_validation_join import ValidationJoinError
    val, joi, comb = _publish_stubs(["AAPB"])

    def bad_join(ranking_path, sidecar_path, sidecar_sha):
        raise ValidationJoinError("validation sidecar SHA-256 mismatch")
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, bad_join, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert (o.run_dir / "RUN_SUMMARY.json").is_file()
    assert "validation join failed" in env["reason"]
    assert comb.calls == []
    assert not (o.run_dir / "07_combine.json").exists()
    assert not (o.run_dir / "08_publish_gate.json").exists()


def test_default_validator_uses_real_adapter_contract(tmp_path, monkeypatch):
    # Prove _default_validator follows build_adapter_inputs ->
    # K6MtfValidationAdapter(secondaries, secondary_inputs) -> run_validation,
    # without loading real inputs or running validation (all monkeypatched).
    import utils.k6_mtf_validation.adapter as adp
    calls = {}
    sentinel_inputs = {"AAPB": object()}

    def fake_build(secs, *, stackbuilder_root):
        calls["build"] = (list(secs), stackbuilder_root)
        return sentinel_inputs

    class FakeAdapter:
        def __init__(self, *, secondaries, secondary_inputs):
            calls["adapter"] = (list(secondaries), secondary_inputs)

    def fake_run(adapter, *, run_id, output_dir, **kw):
        calls["run"] = {"adapter": adapter, "run_id": run_id,
                        "output_dir": output_dir}
        return {"sidecar_path": "x", "artifact_hash": "y",
                "contract": {"validation_status": "valid", "run_id": run_id,
                             "strategies": [{"strategy_id": "k6_mtf:AAPB"}]}}

    monkeypatch.setattr(adp, "build_adapter_inputs", fake_build)
    monkeypatch.setattr(adp, "K6MtfValidationAdapter", FakeAdapter)
    monkeypatch.setattr(adp, "run_validation", fake_run)

    o = _make_orch(tmp_path)
    contract = o._default_validator(["AAPB"], o.run_id)
    assert calls["build"] == (["AAPB"], o.stackbuilder_root.as_posix())
    assert calls["adapter"] == (["AAPB"], sentinel_inputs)
    assert isinstance(calls["run"]["adapter"], FakeAdapter)
    assert calls["run"]["run_id"] == o.run_id
    assert calls["run"]["output_dir"] == (
        o.run_dir / "publish_candidate" / "validation")
    assert contract["run_id"] == o.run_id and contract["validation_status"] == "valid"

    # Fail-closed: a result without a mapping contract -> CrunchError.
    monkeypatch.setattr(adp, "run_validation", lambda *a, **k: {"no": "contract"})
    with pytest.raises(cro.CrunchError):
        o._default_validator(["AAPB"], o.run_id)


# ---------------------------------------------------------------------------
# Publish-dry-run fresh-row path normalization (Stage 6 -> Stage 7)
# ---------------------------------------------------------------------------


def _abs_fresh_row(tmp_path, sec):
    base = (tmp_path / "output").as_posix()  # absolute, e.g. C:/.../output
    return {
        "secondary": sec,
        "history_artifact_path": f"{base}/k6_mtf/RID/{sec}/k6_mtf_history.json",
        "k6_stack": {
            "selected_build_path": f"{base}/stackbuilder/{sec}/selected_build.json",
            "selected_run_dir": f"{base}/stackbuilder/{sec}/seed_{sec}",
            "combo_k6_path":
                f"{base}/stackbuilder/{sec}/seed_{sec}/combo_k=6.json",
        },
    }


def _is_abs_pathstr(s):
    return isinstance(s, str) and (
        (len(s) >= 2 and s[1] == ":") or s.startswith("/") or "\\" in s)


def _row_paths(row):
    ks = row.get("k6_stack") or {}
    return [row.get("history_artifact_path"), ks.get("selected_build_path"),
            ks.get("selected_run_dir"), ks.get("combo_k6_path")]


_NORM_PATH_FIELDS = ["history_artifact_path", "k6_stack.selected_build_path",
                     "k6_stack.selected_run_dir", "k6_stack.combo_k6_path"]


def test_publish_norm_absolute_under_output_normalized(tmp_path):
    val, joi, comb = _publish_stubs(["AAPB"],
                                    make_row=lambda s: _abs_fresh_row(tmp_path, s))
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "completed_publish_dry_run"
    assert len(comb.calls) == 1
    rows = comb.calls[0]["fresh_rows"]
    for r in rows:
        for p in _row_paths(r):
            assert p.startswith("output/"), p
            assert not _is_abs_pathstr(p), p
    # 06_join.json records the normalization summary
    jm = json.loads((o.run_dir / "06_join.json").read_text("utf-8"))
    pn = jm["path_normalization"]
    assert pn["fresh_rows_normalized_count"] == 1
    assert pn["normalized_path_fields_count"] == 4
    assert pn["normalized_path_fields"] == _NORM_PATH_FIELDS


def test_publish_norm_relative_passthrough_and_backslash(tmp_path):
    def make(sec):
        r = _valid_fresh_row(sec)
        r["k6_stack"]["selected_build_path"] = (
            "output" + chr(92) + "stackbuilder" + chr(92) + sec
            + chr(92) + "selected_build.json")  # output\... backslash form
        return r
    val, joi, comb = _publish_stubs(["AAPB"], make_row=make)
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "completed_publish_dry_run"
    r = comb.calls[0]["fresh_rows"][0]
    assert r["k6_stack"]["selected_build_path"] == (
        "output/stackbuilder/AAPB/selected_build.json")  # backslash -> POSIX
    # already-relative top-level path unchanged
    assert r["history_artifact_path"] == "output/k6_mtf/RID/AAPB/k6_mtf_history.json"


@pytest.mark.parametrize("field", _NORM_PATH_FIELDS)
def test_publish_norm_absolute_outside_output_stops(tmp_path, field):
    outside = (tmp_path / "elsewhere" / "AAPB" / "x.json").as_posix()

    def make(sec):
        r = _abs_fresh_row(tmp_path, sec)
        if field.startswith("k6_stack."):
            r["k6_stack"][field.split(".", 1)[1]] = outside
        else:
            r[field] = outside
        return r
    val, joi, comb = _publish_stubs(["AAPB"], make_row=make)
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert comb.calls == []


def test_publish_norm_valid_output_passes_through_after_tightening(tmp_path):
    # Over-rejection guard: a normal output/... fresh row still normalizes
    # clean and reaches combine after the bare-"output" tightening.
    val, joi, comb = _publish_stubs(["AAPB"])  # _valid_fresh_row -> output/...
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "completed_publish_dry_run"
    assert len(comb.calls) == 1  # combine reached
    r = comb.calls[0]["fresh_rows"][0]
    assert r["history_artifact_path"] == "output/k6_mtf/RID/AAPB/k6_mtf_history.json"
    assert r["k6_stack"]["selected_build_path"] == (
        "output/stackbuilder/AAPB/selected_build.json")


@pytest.mark.parametrize("bad", ["tmp/foo.json", "../output/k6_mtf/foo.json",
                                 "stackbuilder/AAPB/x.json",
                                 "output",            # bare 'output' (no slash)
                                 "outputx/foo.json",  # non-segment prefix
                                 "output_evil/foo.json"])
def test_publish_norm_relative_not_under_output_stops(tmp_path, bad):
    def make(sec):
        r = _valid_fresh_row(sec)
        r["history_artifact_path"] = bad
        return r
    val, joi, comb = _publish_stubs(["AAPB"], make_row=make)
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert comb.calls == []


@pytest.mark.parametrize("field,value", [
    ("history_artifact_path", None),
    ("history_artifact_path", ""),
    ("history_artifact_path", 123),
    ("k6_stack.combo_k6_path", None),
    ("k6_stack.selected_run_dir", ""),
])
def test_publish_norm_missing_or_nonstring_field_stops(tmp_path, field, value):
    def make(sec):
        r = _valid_fresh_row(sec)
        if field.startswith("k6_stack."):
            r["k6_stack"][field.split(".", 1)[1]] = value
        else:
            r[field] = value
        return r
    val, joi, comb = _publish_stubs(["AAPB"], make_row=make)
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert comb.calls == []


@pytest.mark.parametrize("ks", [None, [], "nope", 7])
def test_publish_norm_k6_stack_missing_or_nonmapping_stops(tmp_path, ks):
    def make(sec):
        r = _valid_fresh_row(sec)
        if ks is None:
            r.pop("k6_stack", None)
        else:
            r["k6_stack"] = ks
        return r
    val, joi, comb = _publish_stubs(["AAPB"], make_row=make)
    o, _ = _execute_setup(tmp_path, rebuild=["AAPB"], blocked=["DR8A.F"],
                          members_clean=["AAA[D]"] * 6,
                          extra_orch=_publish_extra(val, joi, comb,
                                                    ccc_file=_ccc_file(tmp_path,
                                                                       ["AAPB"])))
    env = o.run()
    assert env["status"] == "halted" and env["halted_at"] == "execute"
    assert comb.calls == []


def test_no_absolute_paths_in_tracked_source():
    bs = chr(92)
    bad_tokens = (
        "c:" + bs + "users", "c:" + "/" + "users", "/" + "users" + "/",
        "/" + "home" + "/", "app" + "data", "mini" + "conda",
        "spy" + "project2",
    )
    for fname in ("crunch_rebuild_orchestrator.py",):
        src = (PROJECT_ROOT / fname).read_text("utf-8").lower()
        for bad in bad_tokens:
            assert bad not in src, "machine path token in source"


# ---------------------------------------------------------------------------
# run_rerank_publish seam (extracted real-publish tail; importable, dry_run-aware)
# ---------------------------------------------------------------------------


def _rr_fake_validator(secs, run_id):
    return {
        "validation_status": "valid",
        "run_id": run_id,
        "strategies": [{"strategy_id": "k6_mtf:" + s} for s in secs],
    }


def _rr_fake_joiner_for(survivors):
    def joiner(ranking_path, sidecar_path, sidecar_sha):
        return {"per_secondary": [
            {"secondary": s,
             "history_artifact_path": "output/k6_mtf/RID/%s/h.json" % s,
             "k6_stack": {
                 "selected_build_path":
                     "output/stackbuilder/%s/selected_build.json" % s,
                 "selected_run_dir": "output/stackbuilder/%s/run" % s,
                 "combo_k6_path": "output/k6_mtf/RID/%s/combo.json" % s,
             }}
            for s in survivors]}
    return joiner


class _RRStage9:
    """A fake Stage 9 runner that records the Stage9PublishInputs it received."""

    def __init__(self, result):
        self.result = result
        self.inputs = None
        self.calls = 0

    def __call__(self, inputs):
        self.inputs = inputs
        self.calls += 1
        return self.result


# Fail-fast publish preflight reads Blob-token PRESENCE only (never the value);
# a fake sentinel satisfies the boolean check hermetically (no real token).
_RR_FAKE_TOKEN_ENV = {"BLOB_READ_WRITE_TOKEN": "rr_fake_token_presence_only"}


def _rr_call(root, survivors, *, dry_run=False, validator=None, joiner=None,
             runner=None, operator_approved=True, excluded=(), env="default"):
    root = Path(root)
    run_dir = root / "output" / "crunch_runs" / "RID"
    run_dir.mkdir(parents=True, exist_ok=True)
    if runner is None:
        runner = _RRStage9(
            {"status": "dry_run_complete" if dry_run else "published"})
    seams = cro.RerankPublishSeams(
        validator=validator or _rr_fake_validator,
        joiner=joiner or _rr_fake_joiner_for(survivors),
        stage9_runner=runner,
        project_root=root,
        repo_root=root,
        excluded_tickers=excluded,
    )
    result = cro.run_rerank_publish(
        survivors=survivors,
        k6_ranking_path=root / "output" / "k6_mtf" / "RID" / "k6_mtf_ranking.json",
        prior_fixture_path=root / "prior" / "fix.json",
        prior_promotion_manifest_path=root / "prior" / "promo.json",
        prior_validation_sidecar_path=root / "prior" / "sidecar.json",
        prior_ccc_verification_manifest_path=root / "prior" / "ccc.json",
        run_dir=run_dir,
        run_id="RID",
        target_as_of="2026-06-03",
        dry_run=dry_run,
        operator_approved=operator_approved,
        seams=seams,
        env=(_RR_FAKE_TOKEN_ENV if env == "default" else env),
    )
    return result, run_dir, runner


def test_run_rerank_publish_end_to_end_with_injected_seams(tmp_path):
    result, run_dir, runner = _rr_call(tmp_path, ["AAA", "BBB"])
    assert result == {"status": "published"}
    assert (run_dir / "05_validation_sidecar.json").is_file()
    assert (run_dir / "09_stage9_publish.json").is_file()
    inp = runner.inputs
    assert inp.run_id == "RID"
    assert inp.fresh_secondaries == ("AAA", "BBB")
    assert [r["secondary"] for r in inp.fresh_rows] == ["AAA", "BBB"]
    assert inp.fresh_validation_sidecar["run_id"] == "RID"
    assert inp.prior_fixture_path == tmp_path / "prior" / "fix.json"
    assert inp.prior_ccc_verification_manifest_path == tmp_path / "prior" / "ccc.json"
    assert inp.dry_run is False
    # fresh-row path fields normalized to project-relative output/...
    assert inp.fresh_rows[0]["history_artifact_path"].startswith("output/")
    # the 09 artifact equals the stage9 result
    written = json.loads((run_dir / "09_stage9_publish.json").read_text("utf-8"))
    assert written == result


def test_run_rerank_publish_dry_run_true_threads_into_inputs(tmp_path):
    result, run_dir, runner = _rr_call(tmp_path, ["AAA"], dry_run=True)
    assert runner.inputs.dry_run is True
    assert result == {"status": "dry_run_complete"}


# --- F2: fail-fast publish preflight BEFORE the validation seam --------------


def _recording_validator():
    calls = {"n": 0}

    def v(secs, run_id):
        calls["n"] += 1
        return _rr_fake_validator(secs, run_id)
    return v, calls


def test_rerank_seam_fail_fast_missing_approval_refuses_before_validation(tmp_path):
    validator, calls = _recording_validator()
    result, run_dir, runner = _rr_call(
        tmp_path, ["AAA", "BBB"], operator_approved=False, validator=validator)
    assert result["status"] == "refused" and result["stage"] == "preflight"
    assert result["operator_approved"] is False
    assert result["refusal"]["schema"] == "stage9_publish_refusal_v1"
    # The expensive validation + the Stage 9 runner were NEVER invoked.
    assert calls["n"] == 0 and runner.calls == 0
    # No validation sidecar; the 09 refusal artifact IS written.
    assert not (run_dir / "05_validation_sidecar.json").is_file()
    assert (run_dir / "09_stage9_publish.json").is_file()
    written = json.loads((run_dir / "09_stage9_publish.json").read_text("utf-8"))
    assert written == result


def test_rerank_seam_fail_fast_missing_token_refuses(tmp_path):
    validator, calls = _recording_validator()
    # token absent in the injected env -> fail-fast before validation
    result, run_dir, runner = _rr_call(
        tmp_path, ["AAA"], env={}, validator=validator)
    assert result["status"] == "refused" and result["stage"] == "preflight"
    assert calls["n"] == 0 and runner.calls == 0
    assert not (run_dir / "05_validation_sidecar.json").is_file()


def test_rerank_seam_happy_path_proceeds_to_validation(tmp_path):
    validator, calls = _recording_validator()
    result, run_dir, runner = _rr_call(tmp_path, ["AAA"], validator=validator)
    assert calls["n"] == 1 and runner.calls == 1
    assert result == {"status": "published"}
    assert (run_dir / "05_validation_sidecar.json").is_file()


def test_orchestrator_tail_calls_seam_with_dry_run_false(tmp_path, monkeypatch):
    o = _make_orch(tmp_path)
    # the tail resolves the run-id-bound ranking artifact (must exist on disk)
    ranking_dir = o.k6_output_root / o.run_id
    ranking_dir.mkdir(parents=True, exist_ok=True)
    (ranking_dir / "k6_mtf_ranking.json").write_text("{}", "utf-8")
    o._excl_set = set()  # normally populated by preflight() before the tail runs
    cap = {}

    def fake_seam(**kw):
        cap.update(kw)
        return {"status": "published"}
    monkeypatch.setattr(cro, "run_rerank_publish", fake_seam)
    res = o._run_stage9_publish_tail({}, ["AAA"])
    assert res == {"status": "published"}
    assert cap["dry_run"] is False                      # Build-and-Rank preserved
    assert list(cap["survivors"]) == ["AAA"]
    assert cap["run_id"] == o.run_id
    assert isinstance(cap["seams"], cro.RerankPublishSeams)
    assert cap["seams"].project_root == tmp_path


def test_run_rerank_publish_all_fresh_and_carried_shapes_accepted(tmp_path):
    # all-fresh: the whole board is the fresh set (carried set empty downstream)
    _, _, run_af = _rr_call(tmp_path / "af", ["AAA", "BBB", "CCC"])
    assert run_af.inputs.fresh_secondaries == ("AAA", "BBB", "CCC")
    # carried-present: a subset is fresh (the rest carry inside combine)
    _, _, run_cp = _rr_call(tmp_path / "cp", ["AAA"])
    assert run_cp.inputs.fresh_secondaries == ("AAA",)


def test_run_rerank_publish_join_error_wrapped(tmp_path):
    from utils.react_publish.k6_mtf_validation_join import ValidationJoinError

    def bad_join(ranking_path, sidecar_path, sidecar_sha):
        raise ValidationJoinError("validation sidecar SHA-256 mismatch")
    with pytest.raises(cro.CrunchError) as ei:
        _rr_call(tmp_path, ["AAA"], joiner=bad_join)
    assert "validation join failed" in str(ei.value)


def test_run_rerank_publish_empty_join_stops(tmp_path):
    def empty_join(ranking_path, sidecar_path, sidecar_sha):
        return {"per_secondary": []}
    with pytest.raises(cro.CrunchError):
        _rr_call(tmp_path, ["AAA"], joiner=empty_join)


def test_run_rerank_publish_bad_sidecar_stops(tmp_path):
    def bad_validator(secs, run_id):
        return {"validation_status": "partial", "run_id": run_id,
                "strategies": []}
    with pytest.raises(cro.CrunchError):
        _rr_call(tmp_path, ["AAA"], validator=bad_validator)


def test_run_rerank_publish_stage9_nonmapping_stops(tmp_path):
    class _BadRunner:
        def __init__(self):
            self.inputs = None

        def __call__(self, inputs):
            self.inputs = inputs
            return "not-a-dict"
    with pytest.raises(cro.CrunchError):
        _rr_call(tmp_path, ["AAA"], runner=_BadRunner())


def test_run_rerank_publish_import_side_effect_free():
    # Importing the orchestrator must NOT pull in the heavy publish modules
    # (they are imported lazily inside run_rerank_publish), and the seam symbols
    # must be present. Run in a clean subprocess under the pinned interpreter.
    import subprocess
    code = (
        "import crunch_rebuild_orchestrator as c, sys; "
        "assert hasattr(c, 'run_rerank_publish'); "
        "assert hasattr(c, 'RerankPublishSeams'); "
        "print('stage9_publish' in sys.modules, "
        "'crunch_combine_proof' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(PROJECT_ROOT),
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False False"
