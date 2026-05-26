"""Tests for the MVP v0 ranking engine (mvp_ranking_v0.py).

All tests construct fake TrafficFlow Phase E canonical run roots
under pytest tmp_path. No real Phase E output is read, no pipeline
component is run, and no Dash app is launched.
"""
from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ENGINE_PATH = PROJECT_ROOT / "mvp_ranking_v0.py"

import mvp_ranking_v0 as engine  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Phase E fixture helpers
# ---------------------------------------------------------------------------


def _make_board_row(*, k=6, sharpe=1.0, trigs=100, wins=60, losses=40,
                    members="AAA, BBB, CCC", total_pct=12.5,
                    avg_pct=0.125, stddev_pct=1.2, win_pct=60.0,
                    p_value=0.001,
                    today="2026-05-22", now=1.0, nxt=1.05,
                    tmrw="2026-05-25", mix="1/1",
                    extra=None):
    row = {
        "Ticker": "WILL_BE_IGNORED",
        "K": k,
        "Members": members,
        "Trigs": trigs,
        "Wins": wins,
        "Losses": losses,
        "Win %": win_pct,
        "StdDev %": stddev_pct,
        "Sharpe": sharpe,
        "p": p_value,
        "Avg %": avg_pct,
        "Total %": total_pct,
        "Today": today,
        "Now": now,
        "NEXT": nxt,
        "TMRW": tmrw,
        "MIX": mix,
    }
    if extra:
        row.update(extra)
    return row


def _seed_secondary(run_root: Path, sec: str, *,
                    board_rows=None,
                    manifest_extras=None,
                    write_manifest=True,
                    write_board=True,
                    board_invalid_json=False,
                    manifest_invalid_json=False):
    sec_dir = run_root / sec
    sec_dir.mkdir(parents=True, exist_ok=True)
    if write_manifest:
        manifest = {
            "schema_version": "trafficflow_runner_phase_e_v1",
            "secondary": sec,
            "invocation_id": "FAKE-INV",
            "k_requested": [1, 2, 3, 4, 5, 6],
            "per_k_summary": [],
            "selected_build_path": f"output/stackbuilder/{sec}/selected_build.json",
            "selected_build_sha256": "deadbeef",
            "selected_run_dir": f"output/stackbuilder/{sec}/RUN_FAKE",
            "combo_leaderboard_path":
                f"output/stackbuilder/{sec}/RUN_FAKE/combo_leaderboard.csv",
            "explicit_build_override": False,
            "canonical_write_mode": "complete",
            "artifacts_written": [],
        }
        if manifest_extras:
            manifest.update(manifest_extras)
        if manifest_invalid_json:
            (sec_dir / "secondary_manifest.json").write_text(
                "{not json", encoding="utf-8")
        else:
            (sec_dir / "secondary_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8")
    if write_board:
        if board_rows is None:
            board_rows = [_make_board_row()]
        if board_invalid_json:
            (sec_dir / "board_rows_k=6.json").write_text(
                "[not, json", encoding="utf-8")
        else:
            (sec_dir / "board_rows_k=6.json").write_text(
                json.dumps(board_rows), encoding="utf-8")


def _make_fake_run(tmp_path: Path, *, run_id="RUN_FAKE_TS"):
    """Build a minimal canonical Phase E run root structure.

    Returns (run_root, selected_output_path).
    """
    run_root = tmp_path / "output" / "trafficflow" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    run_manifest = {
        "schema_version": "trafficflow_runner_phase_e_v1",
        "orchestrator_invocation_id": "FAKE-ORCH-INV",
        "started_at_utc": "2026-05-25T00:00:00.000000Z",
        "ended_at_utc": "2026-05-25T00:00:10.000000Z",
        "elapsed_seconds": 10.0,
        "run_status": "complete",
        "inputs": {"workers": 4, "k_range": [1, 2, 3, 4, 5, 6]},
        "canonical_artifacts_referenced": [],
        "per_secondary_summary": [],
        "quarantined_secondaries": [],
        "artifacts_written": [],
    }
    (run_root / "run_manifest.json").write_text(
        json.dumps(run_manifest), encoding="utf-8")
    selected_output = {
        "schema_version": "trafficflow_canonical_orchestrator_v1",
        "selected_run_root_path":
            f"output/trafficflow/runs/{run_id}",
        "selected_run_id": run_id,
        "run_completed_at_utc": "2026-05-25T00:00:10.000000Z",
        "run_status": "complete",
        "totals": {"total_secondaries": 0, "complete": 0},
    }
    sel_path = tmp_path / "output" / "trafficflow" / "selected_output.json"
    sel_path.write_text(json.dumps(selected_output), encoding="utf-8")
    return run_root, sel_path


def _run_engine(tmp_path: Path, *, secondaries, sharpes=None,
                 sel_path=None, out_dir=None,
                 monkeypatch=None,
                 board_overrides=None):
    """Run the engine via its in-process API against a fresh fake run.

    Returns (exit_code, artifact_dict_or_summary, artifact_path).
    """
    if monkeypatch is not None:
        monkeypatch.chdir(tmp_path)
    run_root, default_sel = _make_fake_run(tmp_path)
    sel_path = sel_path or default_sel
    if sharpes is None:
        sharpes = [1.0] * len(secondaries)
    for sec, sh in zip(secondaries, sharpes):
        overrides = (board_overrides or {}).get(sec, {})
        _seed_secondary(run_root, sec, board_rows=[
            _make_board_row(sharpe=sh, **overrides)
        ])
    out_dir = out_dir or (tmp_path / "output" / "mvp" / "runs" / "MVP_TS")
    exit_code, summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=out_dir,
        secondaries=secondaries,
        project_root=tmp_path,
    )
    artifact_path = out_dir / engine.ARTIFACT_FILENAME
    return exit_code, summary, artifact_path


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_eight_secondaries(tmp_path, monkeypatch):
    secs = list(engine.MVP_V0_DEFAULT_SECONDARIES)
    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=secs, sharpes=[1.0, 1.1, 1.2, 1.3,
                                              1.4, 1.5, 1.6, 1.7],
        monkeypatch=monkeypatch,
    )
    assert exit_code == engine.EXIT_OK
    assert artifact_path.is_file()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "mvp_ranking_v0"
    assert payload["ranking_status"] == "complete"
    assert len(payload["per_secondary"]) == 8
    assert set(payload["secondaries_ranked"]) == set(secs)


# ---------------------------------------------------------------------------
# 2. Ranks by Sharpe descending as emitted
# ---------------------------------------------------------------------------


def test_ranks_by_sharpe_descending(tmp_path, monkeypatch):
    secs = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
    sharpes = [2.5, 1.0, -0.5, 3.0, 0.0, -1.5, 1.8, 2.2]
    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=secs, sharpes=sharpes, monkeypatch=monkeypatch,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    observed_sharpes = [r["sharpe"] for r in payload["per_secondary"]]
    assert observed_sharpes == [3.0, 2.5, 2.2, 1.8, 1.0, 0.0, -0.5, -1.5]
    assert [r["rank"] for r in payload["per_secondary"]] == [1, 2, 3, 4, 5, 6, 7, 8]


# ---------------------------------------------------------------------------
# 3. Negative Sharpe stays negative
# ---------------------------------------------------------------------------


def test_negative_sharpe_stays_negative_and_sorts_lower(tmp_path, monkeypatch):
    secs = ["NEG", "POS"]
    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=secs, sharpes=[-2.0, 1.5],
        monkeypatch=monkeypatch,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    rows = {r["secondary"]: r for r in payload["per_secondary"]}
    assert rows["NEG"]["sharpe"] == -2.0
    assert rows["POS"]["sharpe"] == 1.5
    assert rows["POS"]["rank"] < rows["NEG"]["rank"]


# ---------------------------------------------------------------------------
# 4. Alphabetical tie-breaker
# ---------------------------------------------------------------------------


def test_alphabetical_tie_breaker(tmp_path, monkeypatch):
    secs = ["ZZZ", "AAA", "MMM"]
    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=secs, sharpes=[1.5, 1.5, 1.5],
        monkeypatch=monkeypatch,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    ordered = [r["secondary"] for r in payload["per_secondary"]]
    assert ordered == ["AAA", "MMM", "ZZZ"]


# ---------------------------------------------------------------------------
# 5. Low-sample warning
# ---------------------------------------------------------------------------


def test_low_sample_warning_triggers_25(tmp_path, monkeypatch):
    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=["AAA"], sharpes=[1.0],
        monkeypatch=monkeypatch,
        board_overrides={"AAA": {"trigs": 25}},
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["per_secondary"][0]["low_sample_warning"] is True
    assert payload["per_secondary"][0]["triggers"] == 25


def test_low_sample_warning_triggers_30(tmp_path, monkeypatch):
    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=["AAA"], sharpes=[1.0],
        monkeypatch=monkeypatch,
        board_overrides={"AAA": {"trigs": 30}},
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["per_secondary"][0]["low_sample_warning"] is False
    assert payload["per_secondary"][0]["triggers"] == 30


# ---------------------------------------------------------------------------
# 6. Optional status / signal fields missing
# ---------------------------------------------------------------------------


def test_status_fields_missing_yields_empty_dict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    # Build a row WITHOUT Today/Now/NEXT/TMRW/MIX keys.
    row = {
        "Ticker": "WILL_BE_IGNORED",
        "K": 6, "Members": "AAA", "Trigs": 100, "Wins": 55, "Losses": 45,
        "Win %": 55.0, "StdDev %": 1.0, "Sharpe": 1.2, "p": 0.01,
        "Avg %": 0.12, "Total %": 12.0,
    }
    _seed_secondary(run_root, "AAA", board_rows=[row])
    exit_code, summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=["AAA"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    assert summary["per_secondary"][0]["phase_e_status"] == {}


# ---------------------------------------------------------------------------
# 7. Optional status / signal fields present
# ---------------------------------------------------------------------------


def test_status_fields_present_are_preserved(tmp_path, monkeypatch):
    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=["AAA"], sharpes=[1.0],
        monkeypatch=monkeypatch,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    status = payload["per_secondary"][0]["phase_e_status"]
    for key in ("Today", "Now", "NEXT", "TMRW", "MIX"):
        assert key in status


# ---------------------------------------------------------------------------
# 8. Missing --trafficflow-selected-output
# ---------------------------------------------------------------------------


def test_missing_selected_output_global_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"
    exit_code, summary = engine.build_mvp_ranking_v0(
        selected_output_path=tmp_path / "no_such_file.json",
        output_dir=out_dir,
        secondaries=["AAA"],
        project_root=tmp_path,
    )
    assert exit_code != engine.EXIT_OK
    assert summary["error_code"] == "missing_selected_output"
    assert not (out_dir / engine.ARTIFACT_FILENAME).exists()


# ---------------------------------------------------------------------------
# 9. selected_output.json points at a missing run root
# ---------------------------------------------------------------------------


def test_selected_output_points_at_missing_run_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sel = tmp_path / "output" / "trafficflow" / "selected_output.json"
    sel.parent.mkdir(parents=True, exist_ok=True)
    sel.write_text(json.dumps({
        "selected_run_root_path": "output/trafficflow/runs/NOPE",
        "selected_run_id": "NOPE",
    }), encoding="utf-8")
    out_dir = tmp_path / "out"
    exit_code, summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel,
        output_dir=out_dir,
        secondaries=["AAA"],
        project_root=tmp_path,
    )
    assert exit_code != engine.EXIT_OK
    assert summary["error_code"] == "selected_run_root_missing"
    assert not (out_dir / engine.ARTIFACT_FILENAME).exists()


# ---------------------------------------------------------------------------
# 10. Missing run_manifest.json
# ---------------------------------------------------------------------------


def test_missing_run_manifest_global_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    (run_root / "run_manifest.json").unlink()
    out_dir = tmp_path / "out"
    exit_code, summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=out_dir,
        secondaries=["AAA"],
        project_root=tmp_path,
    )
    assert exit_code != engine.EXIT_OK
    assert summary["error_code"] == "missing_run_manifest"
    assert not (out_dir / engine.ARTIFACT_FILENAME).exists()


# ---------------------------------------------------------------------------
# 11. Missing secondary_manifest.json for one secondary -> partial
# ---------------------------------------------------------------------------


def test_partial_when_one_secondary_manifest_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    secs = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]
    for s in secs[:-1]:
        _seed_secondary(run_root, s)
    # HHH has board rows but no secondary_manifest.json
    _seed_secondary(run_root, "HHH", write_manifest=False)
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=secs,
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(
        (tmp_path / "out" / engine.ARTIFACT_FILENAME).read_text(
            encoding="utf-8"))
    assert payload["ranking_status"] == "partial"
    assert len(payload["per_secondary"]) == 7
    issue_codes = {i["error_code"] for i in payload["issues"]}
    assert "missing_secondary_manifest" in issue_codes


# ---------------------------------------------------------------------------
# 12. Missing board_rows_k=6.json
# ---------------------------------------------------------------------------


def test_missing_board_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    _seed_secondary(run_root, "AAA", write_board=False)
    _seed_secondary(run_root, "BBB")
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(
        (tmp_path / "out" / engine.ARTIFACT_FILENAME).read_text(
            encoding="utf-8"))
    assert payload["ranking_status"] == "partial"
    issue_codes = {i["error_code"] for i in payload["issues"]}
    assert "missing_board_rows" in issue_codes


# ---------------------------------------------------------------------------
# 13. No K=6 row
# ---------------------------------------------------------------------------


def test_no_k6_row(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    rows = [_make_board_row(k=k) for k in range(1, 6)]
    _seed_secondary(run_root, "AAA", board_rows=rows)
    _seed_secondary(run_root, "BBB")
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(
        (tmp_path / "out" / engine.ARTIFACT_FILENAME).read_text(
            encoding="utf-8"))
    issue_codes = {i["error_code"] for i in payload["issues"]}
    assert "missing_k6_row" in issue_codes


# ---------------------------------------------------------------------------
# 14. Malformed numeric metrics
# ---------------------------------------------------------------------------


def test_malformed_metrics_non_numeric_sharpe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    bad_row = _make_board_row(sharpe="not_a_number")
    _seed_secondary(run_root, "AAA", board_rows=[bad_row])
    _seed_secondary(run_root, "BBB")
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(
        (tmp_path / "out" / engine.ARTIFACT_FILENAME).read_text(
            encoding="utf-8"))
    issue_codes = {i["error_code"] for i in payload["issues"]}
    assert "malformed_metrics" in issue_codes


def test_malformed_metrics_non_integer_triggers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    bad_row = _make_board_row(trigs="lots")
    _seed_secondary(run_root, "AAA", board_rows=[bad_row])
    _seed_secondary(run_root, "BBB")
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(
        (tmp_path / "out" / engine.ARTIFACT_FILENAME).read_text(
            encoding="utf-8"))
    issue_codes = {i["error_code"] for i in payload["issues"]}
    assert "malformed_metrics" in issue_codes


# ---------------------------------------------------------------------------
# 15. Unreadable per-secondary JSON
# ---------------------------------------------------------------------------


def test_board_rows_unreadable_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    _seed_secondary(run_root, "AAA", board_invalid_json=True)
    _seed_secondary(run_root, "BBB")
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(
        (tmp_path / "out" / engine.ARTIFACT_FILENAME).read_text(
            encoding="utf-8"))
    issue_codes = {i["error_code"] for i in payload["issues"]}
    assert "board_rows_unreadable" in issue_codes


def test_secondary_manifest_unreadable_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    _seed_secondary(run_root, "AAA", manifest_invalid_json=True)
    _seed_secondary(run_root, "BBB")
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    payload = json.loads(
        (tmp_path / "out" / engine.ARTIFACT_FILENAME).read_text(
            encoding="utf-8"))
    issue_codes = {i["error_code"] for i in payload["issues"]}
    assert "secondary_manifest_unreadable" in issue_codes


# ---------------------------------------------------------------------------
# 16. All secondaries fail
# ---------------------------------------------------------------------------


def test_all_secondaries_fail_no_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    # Every requested secondary has no board_rows file.
    for s in ("AAA", "BBB", "CCC"):
        _seed_secondary(run_root, s, write_board=False)
    out_dir = tmp_path / "out"
    exit_code, summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=out_dir,
        secondaries=["AAA", "BBB", "CCC"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_ALL_SECONDARIES_FAILED
    assert summary["status"] == "all_secondaries_failed"
    assert not (out_dir / engine.ARTIFACT_FILENAME).exists()


# ---------------------------------------------------------------------------
# 17. Privacy sanitization on emitted JSON
# ---------------------------------------------------------------------------


def test_privacy_sanitization_scrubs_embedded_absolute_paths(
    tmp_path, monkeypatch,
):
    """Absolute-path-like strings in inputs must not survive into the
    emitted artifact. Test name and content avoid the project's
    denylist tokens."""
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    # Construct an absolute-path-like fragment at runtime from tmp_path
    # so this test file contains no literal absolute-path strings.
    leaky_path = str(tmp_path / "some" / "leaky" / "place" / "file.log")
    bad_manifest_extras = {
        "leaky_field": leaky_path,
    }
    row = _make_board_row()
    _seed_secondary(run_root, "AAA", board_rows=[row],
                    manifest_extras=bad_manifest_extras)
    # And: a malformed metrics path that surfaces an absolute-pathy
    # error message via the issues channel.
    _seed_secondary(run_root, "BBB",
                    board_rows=[_make_board_row(sharpe=leaky_path)])
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=tmp_path / "out",
        secondaries=["AAA", "BBB"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_OK
    artifact_text = (tmp_path / "out" / engine.ARTIFACT_FILENAME).read_text(
        encoding="utf-8")
    # Absolute-path leak: the original leaky_path string must not appear
    # verbatim anywhere in the emitted JSON.
    assert leaky_path not in artifact_text
    # Drive-letter pattern must not appear in the emitted JSON.
    assert re.search(r"[A-Za-z]:[\\/]", artifact_text) is None


# ---------------------------------------------------------------------------
# 18. Atomic write / no .tmp residue
# ---------------------------------------------------------------------------


def test_no_tmp_residue_after_success(tmp_path, monkeypatch):
    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=["AAA"], sharpes=[1.0],
        monkeypatch=monkeypatch,
    )
    assert exit_code == engine.EXIT_OK
    out_dir = artifact_path.parent
    tmp_residue = list(out_dir.glob("*.tmp"))
    assert tmp_residue == []


def test_no_artifact_after_all_secondaries_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_root, sel_path = _make_fake_run(tmp_path)
    _seed_secondary(run_root, "AAA", write_board=False)
    out_dir = tmp_path / "out"
    exit_code, _summary = engine.build_mvp_ranking_v0(
        selected_output_path=sel_path,
        output_dir=out_dir,
        secondaries=["AAA"],
        project_root=tmp_path,
    )
    assert exit_code == engine.EXIT_ALL_SECONDARIES_FAILED
    assert not (out_dir / engine.ARTIFACT_FILENAME).exists()
    # And no .tmp orphan either.
    assert list(out_dir.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# 19. No raw signal libraries / price caches / PKLs are read
# ---------------------------------------------------------------------------


def test_no_lower_level_reads_attempted(tmp_path, monkeypatch):
    """Block reads from forbidden lower-level input directories and
    confirm the engine completes successfully."""
    monkeypatch.chdir(tmp_path)
    forbidden_prefixes = (
        (tmp_path / "signal_library").as_posix(),
        (tmp_path / "price_cache").as_posix(),
        (tmp_path / "cache").as_posix(),
    )

    real_open = open

    def _guarded_open(file, *args, **kwargs):
        try:
            path_str = Path(file).as_posix()
        except TypeError:
            return real_open(file, *args, **kwargs)
        for prefix in forbidden_prefixes:
            if path_str.startswith(prefix):
                raise AssertionError(
                    f"forbidden lower-level read: {path_str}"
                )
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _guarded_open)

    exit_code, _summary, artifact_path = _run_engine(
        tmp_path, secondaries=["AAA"], sharpes=[1.0],
        # Already chdir'd above.
    )
    assert exit_code == engine.EXIT_OK
    assert artifact_path.is_file()


# ---------------------------------------------------------------------------
# 20. Import guard
# ---------------------------------------------------------------------------


def test_import_guard_no_engine_imports():
    forbidden_roots = {
        "trafficflow",
        "stackbuilder",
        "impactsearch",
        "onepass",
        "confluence",
    }
    src = ENGINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(ENGINE_PATH))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                assert root not in forbidden_roots, (
                    f"forbidden top-level import: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".", 1)[0]
            assert mod not in forbidden_roots, (
                f"forbidden top-level from-import: {node.module}"
            )
    # sys.modules state: importing the engine must not have pulled any
    # forbidden engine into the process.
    for forbidden in forbidden_roots:
        assert forbidden not in sys.modules, (
            f"forbidden engine present in sys.modules: {forbidden}"
        )


# ---------------------------------------------------------------------------
# 21. CLI --help exits 0
# ---------------------------------------------------------------------------


def test_cli_help_exits_zero_subprocess():
    proc = subprocess.run(
        [sys.executable, str(ENGINE_PATH), "--help"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0
    assert "mvp_ranking_v0" in proc.stdout


def test_cli_help_exits_zero_in_process(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc: Optional[int] = None
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = engine.main(["--help"])
        except SystemExit as exc:
            rc = int(exc.code) if isinstance(exc.code, int) else -1
    assert rc == 0


# ---------------------------------------------------------------------------
# 22. Deterministic ranking
# ---------------------------------------------------------------------------


def test_deterministic_content_across_two_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    secs = ["AAA", "BBB", "CCC"]
    sharpes = [1.2, 0.8, 2.0]

    def one_run(out_dir: Path):
        # Each run consumes the same fake selected_output + run root.
        # Build the fake run once, but re-using the same seed twice is
        # equivalent because the engine is deterministic.
        if not (tmp_path / "output" / "trafficflow" /
                "selected_output.json").is_file():
            run_root, _ = _make_fake_run(tmp_path)
            for s, sh in zip(secs, sharpes):
                _seed_secondary(run_root, s,
                                board_rows=[_make_board_row(sharpe=sh)])
        sel = tmp_path / "output" / "trafficflow" / "selected_output.json"
        rc, _ = engine.build_mvp_ranking_v0(
            selected_output_path=sel,
            output_dir=out_dir,
            secondaries=secs,
            project_root=tmp_path,
        )
        assert rc == engine.EXIT_OK
        return json.loads(
            (out_dir / engine.ARTIFACT_FILENAME).read_text(encoding="utf-8")
        )

    payload_a = one_run(tmp_path / "out_a")
    payload_b = one_run(tmp_path / "out_b")
    # Strip the non-deterministic timestamp field before comparing.
    payload_a.pop("generated_at_utc", None)
    payload_b.pop("generated_at_utc", None)
    assert payload_a == payload_b
