"""Hermetic tests for rerank_driver.

No real engines, recook, validation, Blob/network, promote write, push, or
token value. Recook, publish seam, lock, clock, env, and filesystem roots are
injected. The board is the selection -- there is no input().
"""
from __future__ import annotations

import ast
import io
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import rerank_driver as R  # noqa: E402


SENTINEL_TOKEN = "blob_SENTINEL_TOKEN_VALUE_zzz999"
# 2026-06-09 21:00 UTC == Tue 17:00 ET (after the 16:00 close) -> target 2026-06-09
FIXED_NOW = datetime(2026, 6, 9, 21, 0, 0, tzinfo=timezone.utc)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj) -> None:
    _write(path, json.dumps(obj, indent=2) + "\n")


def _make_repo(tmp_path: Path, board=("AAA", "BBB", "CCC")) -> Path:
    repo = tmp_path / "repo"
    _write_json(repo / R.DEFAULT_FIXTURE, {
        "per_secondary": [{"secondary": s} for s in board],
        "validation_metadata": {
            "source_sidecar_path":
                "output/crunch_runs/PRIOR/publish_candidate/"
                "composite_validation_sidecar.json"},
    })
    _write_json(repo / R.DEFAULT_PROMOTION_MANIFEST, {
        "source_run_id": "PRIOR",
        "ccc_series_storage": {
            "verification_manifest_path":
                "output/crunch_runs/PRIOR/publish_candidate/"
                "combined_ccc_sidecar_verification.json"},
    })
    # the prior-board artifacts the metadata points at (live board, run PRIOR)
    _write(repo / "output/crunch_runs/PRIOR/publish_candidate/"
           "composite_validation_sidecar.json", "{}")
    _write(repo / "output/crunch_runs/PRIOR/publish_candidate/"
           "combined_ccc_sidecar_verification.json", "{}")
    return repo


DEFAULT_BOARD = ("AAA", "BBB", "CCC")


def _kept_from(exclusions, board=DEFAULT_BOARD):
    # The engine's kept set = board minus the excluded secondaries (used to
    # exercise parse_recook_outcome's kept-vs-board-minus-quarantine cross-check).
    excl = {str(e.get("secondary")).strip().upper()
            for e in exclusions if isinstance(e, dict) and e.get("secondary")}
    return [s for s in board if s not in excl]


def _stage_a_exclusion(secondary, kind="not_current"):
    # Mirrors a k6_recook Stage-A ALLOWABLE exclusion record
    # (k6_recook.py:2456-2489): stage 'A' + ticker_classification = the kind.
    return {"secondary": secondary, "stage": "A",
            "reason": "stage_a_unavailable:%s" % kind,
            "ticker": "DEP", "ticker_classification": kind}


def _aprime_exclusion(secondary):
    # Mirrors a k6_recook Stage-Aprime caret exclusion record (k6_recook.py:1396):
    # stage 'Aprime', no ticker_classification. Under the engine's authority this
    # is folded into the same allowable partial -- it quarantines, it does not halt.
    return {"secondary": secondary, "stage": "Aprime",
            "reason": "caret_source_unavailable_or_stale"}


def _ok_envelope(exclusions=(), failures=(), status="ok", exit_code=0,
                 board=DEFAULT_BOARD, kept=None, halted_at=None):
    ex = list(exclusions)
    return {"status": status, "exit_code": exit_code,
            "exclusions": ex, "failures": list(failures),
            "partial_reasons": [], "halted_at": halted_at,
            "kept_secondaries": kept if kept is not None else _kept_from(ex, board),
            "timings": {"total_seconds": 12.3}}


def _partial_envelope(exclusions, *, partial_reasons=("stage_a_allowed_exclusions",),
                      failures=(), board=DEFAULT_BOARD, kept=None, halted_at=None):
    # REAL contract: k6_recook returns status='partial', exit_code=3 whenever
    # anything is excluded/dropped after the chain completes (k6_recook.py:2731-2744).
    ex = list(exclusions)
    return {"status": "partial", "exit_code": 3,
            "exclusions": ex, "failures": list(failures),
            "partial_reasons": list(partial_reasons), "halted_at": halted_at,
            "kept_secondaries": kept if kept is not None else _kept_from(ex, board),
            "timings": {"total_seconds": 14.0}}


class _Recook:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = []

    def __call__(self, argv, *, cwd):
        self.calls.append((list(argv), cwd))
        return self.envelope


class _Publish:
    def __init__(self, result):
        self.result = result
        self.kwargs = None
        self.calls = 0

    def __call__(self, **kw):
        self.kwargs = kw
        self.calls += 1
        return self.result


def _run(repo, argv, *, token=SENTINEL_TOKEN, recook=None, publish=None,
         lock_raise=False, now=FIXED_NOW, holidays=(), run_id="RID"):
    out, err = io.StringIO(), io.StringIO()
    env = {} if token is None else {R.TOKEN_ENV: token}
    recook = recook or _Recook(_ok_envelope())
    publish = publish or _Publish({"status": "published"})
    locks = {"acquired": [], "released": []}

    def lock_acq(lock_path, rid, when):
        if lock_raise:
            raise R._cro.CrunchError("crunch lock held by live pid 999")
        locks["acquired"].append((lock_path, rid))

    def lock_rel(lock_path):
        locks["released"].append(lock_path)

    seams = R.RerankPublishSeams(
        validator=lambda secs, rid: {}, joiner=lambda *a: {},
        stage9_runner=lambda inp: {}, project_root=repo, repo_root=repo)
    rc = R.main(argv, env=env, repo_root=repo, clock=lambda: now,
                holidays=holidays, stdout=out, stderr=err, run_id=run_id,
                recook_runner=recook, lock_acquire=lock_acq,
                lock_release=lock_rel, publish_seams=seams, publish_func=publish)
    status_path = repo / R.STATUS_REL
    status = (json.loads(status_path.read_text("utf-8"))
              if status_path.is_file() else None)
    return rc, out.getvalue(), err.getvalue(), status, recook, publish, locks


def _ok_argv(extra=()):
    return ["--publish", "--operator-approved-publish",
            "--duration-budget-minutes", "240", *extra]


# ---------------------------------------------------------------------------
# Zero-question + token preflight
# ---------------------------------------------------------------------------


def test_zero_question_no_input_call_anywhere():
    tree = ast.parse(Path(R.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "input", "rerank_driver must never call input()"


def test_token_absent_guidance_nonzero_before_work(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(
        repo, _ok_argv(), token=None)
    assert rc != 0
    assert recook.calls == []        # no heavy work
    assert publish.calls == 0
    assert locks["acquired"] == []   # lock not even acquired
    guidance = out + err
    assert R.TOKEN_ENV in guidance and "setx" in guidance and "NEW terminal" in guidance
    assert status["status"] == "refused_no_token"


# ---------------------------------------------------------------------------
# Board enumeration
# ---------------------------------------------------------------------------


def test_board_enumeration_from_fixture(tmp_path):
    repo = _make_repo(tmp_path, board=("ccc", "AAA", "bbb", "AAA"))
    board = R.enumerate_board(repo / R.DEFAULT_FIXTURE)
    assert board == ["AAA", "BBB", "CCC"]  # normalized + de-duped + sorted


# ---------------------------------------------------------------------------
# Target derivation
# ---------------------------------------------------------------------------


def test_target_weekday_after_close():
    # Tue 17:00 ET
    assert R.derive_target_as_of(
        now=datetime(2026, 6, 9, 21, 0, tzinfo=timezone.utc)) == "2026-06-09"


def test_target_weekday_before_close():
    # Tue 14:00 ET -> prior trading day (Mon)
    assert R.derive_target_as_of(
        now=datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc)) == "2026-06-08"


def test_target_saturday_and_sunday_roll_back_to_friday():
    sat = R.derive_target_as_of(now=datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc))
    sun = R.derive_target_as_of(now=datetime(2026, 6, 14, 18, 0, tzinfo=timezone.utc))
    assert sat == "2026-06-12" and sun == "2026-06-12"


def test_target_skips_holiday():
    # Tue 14:00 ET (before close); Mon 2026-06-08 injected as a holiday ->
    # walk back past Mon/Sun/Sat to Fri 2026-06-05.
    got = R.derive_target_as_of(
        now=datetime(2026, 6, 9, 18, 0, tzinfo=timezone.utc),
        holidays={date(2026, 6, 8)})
    assert got == "2026-06-05"


def test_target_timezone_utc_maps_to_prior_et_day():
    # 2026-06-09 02:00 UTC == Mon 2026-06-08 22:00 ET (after close) -> Mon
    assert R.derive_target_as_of(
        now=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc)) == "2026-06-08"


# ---------------------------------------------------------------------------
# Gate composition + recook argv
# ---------------------------------------------------------------------------


def test_duration_budget_required(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(SystemExit):
        _run(repo, ["--publish", "--operator-approved-publish"])


def test_operator_budget_label_default_and_recorded(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, *_ = _run(repo, _ok_argv())
    assert status["operator_budget_label"] == "rerank-nightly"
    assert status["duration_budget_minutes"] == 240


def test_recook_argv_gates_and_no_discovery_stages(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    argv = recook.calls[0][0]
    assert "k6_recook.py" in argv
    assert "--allow-stage-a-exclusions" in argv
    assert "--allow-network-fetch" in argv
    assert "--restage-all" in argv
    assert "--target-as-of" in argv and "2026-06-09" in argv
    i = argv.index("--secondaries")
    assert argv[i + 1] == "AAA,BBB,CCC"
    # explicitly NO discovery / selection stages
    for forbidden in ("onepass_workbook_runner.py",
                      "impactsearch_workbook_runner.py",
                      "stackbuilder_workbook_runner.py"):
        assert forbidden not in argv


# ---------------------------------------------------------------------------
# Quarantine + halt
# ---------------------------------------------------------------------------


def _permissive(extra=()):
    # publish-path argv with the quarantine guard relaxed (so a small test board
    # with one quarantine does not trip the 0.25 default ceiling).
    return _ok_argv(["--max-quarantine-fraction", "1.0", *extra])


def test_partial_allowable_stage_a_quarantines_and_publishes(tmp_path):
    # status 'partial', exit_code 3, allowable partial_reasons -> publish the
    # survivors; the secondary quarantines. Survivors come from the engine kept.
    repo = _make_repo(tmp_path)
    env = _partial_envelope([_stage_a_exclusion("BBB", "not_current")])
    rc, out, err, status, recook, publish, locks = _run(
        repo, _permissive(), recook=_Recook(env))
    assert rc == 0
    assert publish.calls == 1
    assert publish.kwargs["survivors"] == ["AAA", "CCC"]      # from engine kept
    qsecs = [q["secondary"] for q in status["quarantined"]]
    assert qsecs == ["BBB"]
    assert status["quarantined"][0]["causes"][0]["kind"] == "not_current"
    assert status["fresh_secondaries"] == ["AAA", "CCC"]


def test_clean_ok_full_board_publishes(tmp_path):
    # status 'ok', exit_code 0, NO exclusions -> full-board publish.
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    assert rc == 0 and publish.calls == 1
    assert publish.kwargs["survivors"] == ["AAA", "BBB", "CCC"]


def test_real_tonight_shape_aprime_quarantines_without_halting(tmp_path):
    # THE forensics fix: the pilot's real shape -- a partial/3 with Stage-A
    # not_current AND Stage-Aprime caret records, failures empty,
    # partial_reasons=['stage_a_allowed_exclusions'] -- must PUBLISH survivors;
    # the Aprime caret records quarantine, they no longer halt (small fraction).
    board = tuple("S%02d" % i for i in range(12))
    repo = _make_repo(tmp_path, board=board)
    env = _partial_envelope(
        [_stage_a_exclusion("S00", "not_current"),  # Stage-A allowable
         _aprime_exclusion("S01")],                  # Stage-Aprime caret
        board=board)
    rc, out, err, status, recook, publish, locks = _run(
        repo, _ok_argv(), recook=_Recook(env))      # default ceiling; 2/12=0.167
    assert rc == 0
    assert publish.calls == 1
    qsecs = sorted(q["secondary"] for q in status["quarantined"])
    assert qsecs == ["S00", "S01"]                   # Aprime quarantined, no halt
    assert set(publish.kwargs["survivors"]) == set(board) - {"S00", "S01"}


def test_partial_with_failures_halts(tmp_path):
    repo = _make_repo(tmp_path)
    env = _partial_envelope(
        [_stage_a_exclusion("BBB")],
        partial_reasons=["stage_a_allowed_exclusions", "failures_present"],
        failures=[{"ticker": "ZZZ", "reason": "retry_exhausted"}])
    rc, out, err, status, recook, publish, locks = _run(
        repo, _permissive(), recook=_Recook(env))
    assert rc != 0 and publish.calls == 0


def test_partial_unexpected_reason_halts(tmp_path):
    # partial_reasons beyond the allowable Stage-A reason -> halt (unknown=halt).
    repo = _make_repo(tmp_path)
    env = _partial_envelope(
        [_stage_a_exclusion("BBB")],
        partial_reasons=["stage_a_allowed_exclusions", "mystery_reason"])
    rc, out, err, status, recook, publish, locks = _run(
        repo, _permissive(), recook=_Recook(env))
    assert rc != 0 and publish.calls == 0


def test_partial_halted_at_set_halts(tmp_path):
    # halted_at set -> halt, regardless of an otherwise-allowable partial.
    repo = _make_repo(tmp_path)
    env = _partial_envelope([_stage_a_exclusion("BBB")], halted_at="A")
    rc, out, err, status, recook, publish, locks = _run(
        repo, _permissive(), recook=_Recook(env))
    assert rc != 0 and publish.calls == 0
    assert status["status"] == "halted_recook"


def test_systemic_failed_exit_halts_no_publish(tmp_path):
    # status 'failed', exit_code 1 (the _halt_stage_a blocking path) -> halt.
    repo = _make_repo(tmp_path)
    env = _ok_envelope(status="failed", exit_code=1,
                       failures=[{"ticker": "ZZZ", "reason": "retry_exhausted"}])
    rc, out, err, status, recook, publish, locks = _run(
        repo, _ok_argv(), recook=_Recook(env))
    assert rc != 0
    assert publish.calls == 0
    assert status["status"] == "halted_recook"
    assert status["halted_at"] == "recook"


def test_full_validation_runs_over_surviving_set(tmp_path):
    # The seam runs full validation over `survivors` (board minus quarantine).
    repo = _make_repo(tmp_path)
    env = _partial_envelope([_stage_a_exclusion("AAA", "not_current")])
    rc, out, err, status, recook, publish, locks = _run(
        repo, _permissive(), recook=_Recook(env))
    assert rc == 0
    assert publish.kwargs["survivors"] == ["BBB", "CCC"]


def test_survivors_come_from_engine_kept_set(tmp_path):
    # Survivors are the engine's kept set, not just board-minus-quarantined.
    repo = _make_repo(tmp_path)
    env = _partial_envelope([_stage_a_exclusion("CCC", "not_current")])
    rc, out, err, status, recook, publish, locks = _run(
        repo, _permissive(), recook=_Recook(env))
    assert rc == 0
    assert publish.kwargs["survivors"] == ["AAA", "BBB"]


def test_engine_kept_mismatch_halts_unknown_shape(tmp_path):
    # kept set disagrees with board-minus-quarantined -> halt fail-closed.
    repo = _make_repo(tmp_path)
    env = _partial_envelope([_stage_a_exclusion("BBB")], kept=["AAA"])  # expect AAA,CCC
    rc, out, err, status, recook, publish, locks = _run(
        repo, _permissive(), recook=_Recook(env))
    assert rc != 0 and publish.calls == 0
    assert status["status"] == "halted_recook"


# ---------------------------------------------------------------------------
# Quarantine-fraction guard (F2)
# ---------------------------------------------------------------------------


def test_mass_quarantine_trips_default_guard(tmp_path):
    # Tonight's real shape (146/207 = 0.705) MUST trip the default 0.25 ceiling.
    board = tuple("S%03d" % i for i in range(207))
    repo = _make_repo(tmp_path, board=board)
    excl = [_stage_a_exclusion(board[i], "not_current") for i in range(146)]
    env = _partial_envelope(excl, board=board)
    rc, out, err, status, recook, publish, locks = _run(
        repo, _ok_argv(), recook=_Recook(env))   # default ceiling 0.25
    assert rc != 0
    assert publish.calls == 0
    assert status["status"] == "halted_quarantine_guard"
    assert status["halted_at"] == "quarantine_guard"
    assert abs(status["quarantine_fraction"] - 146 / 207) < 1e-9
    assert status["max_quarantine_fraction"] == 0.25
    assert len(status["quarantined"]) == 146
    assert status["quarantined"][0]["causes"][0]["reason"].startswith(
        "stage_a_unavailable")


def test_quarantine_guard_boundary_at_ceiling_passes_just_above_halts(tmp_path):
    board = ("A", "B", "C", "D")
    # 1/4 == 0.25 ceiling -> passes (publishes)
    repo1 = _make_repo(tmp_path / "at", board=board)
    env1 = _partial_envelope([_stage_a_exclusion("A")], board=board)
    rc1, *_rest1 = _run(repo1, _ok_argv(), recook=_Recook(env1))
    assert rc1 == 0
    # 2/4 == 0.5 > 0.25 -> halts
    repo2 = _make_repo(tmp_path / "above", board=board)
    env2 = _partial_envelope(
        [_stage_a_exclusion("A"), _stage_a_exclusion("B")], board=board)
    rc2, out2, err2, status2, recook2, publish2, locks2 = _run(
        repo2, _ok_argv(), recook=_Recook(env2))
    assert rc2 != 0 and publish2.calls == 0
    assert status2["status"] == "halted_quarantine_guard"


def test_quarantine_guard_override_respected(tmp_path):
    # A high quarantine fraction publishes when the operator raises the ceiling.
    board = ("A", "B", "C", "D")
    repo = _make_repo(tmp_path, board=board)
    env = _partial_envelope(
        [_stage_a_exclusion("A"), _stage_a_exclusion("B")], board=board)  # 0.5
    rc, out, err, status, recook, publish, locks = _run(
        repo, _ok_argv(["--max-quarantine-fraction", "0.75"]),
        recook=_Recook(env))
    assert rc == 0 and publish.calls == 1


def test_invalid_max_quarantine_fraction_rejected(tmp_path):
    repo = _make_repo(tmp_path)
    for bad in ("0", "1.5", "-0.1"):
        with pytest.raises(SystemExit):
            _run(repo, _ok_argv(["--max-quarantine-fraction", bad]))


# ---------------------------------------------------------------------------
# Target override (F3)
# ---------------------------------------------------------------------------


def test_target_override_plumbs_to_argv_and_status(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(
        repo, _ok_argv(["--target-as-of", "2026-06-05"]))
    assert rc == 0
    argv = recook.calls[0][0]
    i = argv.index("--target-as-of")
    assert argv[i + 1] == "2026-06-05"
    assert status["target_as_of"] == "2026-06-05"
    assert status["target_source"] == "overridden"


def test_derived_target_records_source(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    assert status["target_as_of"] == "2026-06-09"   # derived from FIXED_NOW
    assert status["target_source"] == "derived"


def test_bad_target_format_rejected(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(SystemExit):
        _run(repo, _ok_argv(["--target-as-of", "06/05/2026"]))


# ---------------------------------------------------------------------------
# Publish seam wiring + CCC chain
# ---------------------------------------------------------------------------


def test_seam_invocation_kwargs_exact(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    kw = publish.kwargs
    assert kw["survivors"] == ["AAA", "BBB", "CCC"]
    assert kw["run_id"] == "RID"
    assert kw["target_as_of"] == "2026-06-09"
    assert kw["dry_run"] is False
    assert kw["operator_approved"] is True
    assert kw["k6_ranking_path"] == repo / "output/k6_mtf/RID/k6_mtf_ranking.json"
    assert kw["run_dir"] == repo / "output/crunch_runs/RID"
    assert kw["prior_fixture_path"] == repo / R.DEFAULT_FIXTURE
    assert kw["prior_promotion_manifest_path"] == repo / R.DEFAULT_PROMOTION_MANIFEST
    assert isinstance(kw["seams"], R.RerankPublishSeams)


def test_ccc_chain_prior_manifest_is_live_board_never_tonight(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    kw = publish.kwargs
    prior_ccc = str(kw["prior_ccc_verification_manifest_path"])
    prior_sidecar = str(kw["prior_validation_sidecar_path"])
    # bound to the live board's source run ("PRIOR"), never tonight's run "RID"
    assert "PRIOR" in prior_ccc and "RID" not in prior_ccc
    assert "PRIOR" in prior_sidecar and "RID" not in prior_sidecar
    assert prior_ccc.endswith("combined_ccc_sidecar_verification.json")


# ---------------------------------------------------------------------------
# Modes + mutual exclusivity + disclosure
# ---------------------------------------------------------------------------


def test_publish_dry_run_threads_dry_run_true_and_prints_disclosure(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(
        repo, ["--publish-dry-run", "--duration-budget-minutes", "240"],
        publish=_Publish({"status": "dry_run_complete"}))
    assert rc == 0
    assert publish.kwargs["dry_run"] is True
    assert publish.kwargs["operator_approved"] is False
    assert "NOT" in out and "Blob" in out  # the accepted disclosure
    assert status["status"] == "dry_run_complete"
    assert status["dry_run"] is True


def test_publish_threads_dry_run_false(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    assert publish.kwargs["dry_run"] is False
    assert publish.kwargs["operator_approved"] is True


def test_publish_and_publish_dry_run_mutually_exclusive(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(SystemExit):
        _run(repo, ["--publish", "--publish-dry-run",
                    "--operator-approved-publish",
                    "--duration-budget-minutes", "240"])


def test_neither_mode_is_rejected(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(SystemExit):
        _run(repo, ["--duration-budget-minutes", "240"])


def test_publish_requires_operator_approval(tmp_path):
    repo = _make_repo(tmp_path)
    with pytest.raises(SystemExit):
        _run(repo, ["--publish", "--duration-budget-minutes", "240"])


# ---------------------------------------------------------------------------
# Lock-busy + status pointer + timing + sentinel safety
# ---------------------------------------------------------------------------


def test_lock_busy_writes_status_and_exits_clean(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(
        repo, _ok_argv(), lock_raise=True)
    assert rc != 0
    assert recook.calls == []   # no work past the lock
    assert publish.calls == 0
    assert status["status"] == "lock_busy"
    assert status["halted_at"] == "lock"


def test_status_pointer_on_success_has_timing_and_artifacts(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    assert status["status"] == "published"
    assert "timing" in status
    for k in ("recook_seconds", "publish_seconds", "total_seconds"):
        assert k in status["timing"]
    assert status["timing"]["per_secondary_recook_timing_available"] is False
    assert "artifacts" in status and "run_dir" in status["artifacts"]
    assert status["status_gitignore_rule"] == "output/ (project .gitignore)"


def test_status_written_under_gitignored_output(tmp_path):
    repo = _make_repo(tmp_path)
    _run(repo, _ok_argv())
    sp = repo / R.STATUS_REL
    assert sp.is_file()
    assert sp.parent == repo / "output" / "rerank"


def test_sentinel_token_never_appears_anywhere(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    blob = out + err + json.dumps(status)
    for p in (repo / "output" / "rerank").rglob("*"):
        if p.is_file():
            blob += p.read_text(encoding="utf-8", errors="replace")
    assert SENTINEL_TOKEN not in blob


def test_lock_released_on_success(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err, status, recook, publish, locks = _run(repo, _ok_argv())
    assert len(locks["acquired"]) == 1
    assert len(locks["released"]) == 1
