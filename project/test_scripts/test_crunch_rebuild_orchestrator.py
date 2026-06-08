"""Hermetic tests for crunch_rebuild_orchestrator.

The stage invoker and process-conflict check are stubbed; no real engines,
no real registry/cache/output mutation, no network. All paths under tmp_path.
"""
from __future__ import annotations

import json
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
                   writers_override=None, results_override=None):
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
               "stackbuilder": {"status": "ok"},
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
