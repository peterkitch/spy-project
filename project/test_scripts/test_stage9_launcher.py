"""Hermetic tests for stage9_launcher.

No real orchestrator, engines, Blob, network, promote, deploy, or token value.
Environment, input, filesystem roots, subprocess runner, clock, and output
streams are all injected. The launcher's runner is always a fake that records
the composed argv + cwd and returns a chosen exit code.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import stage9_launcher as L  # noqa: E402
import crunch_rebuild_orchestrator as _orch  # noqa: E402


SENTINEL_TOKEN = "blob_SENTINEL_TOKEN_VALUE_zzz999"
FIXED_CLOCK = lambda: datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)
FIXED_TS = "20260611T120000Z"


# ---------------------------------------------------------------------------
# Fixture repo
# ---------------------------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    """A tmp repo_root with a live fixture, ImpactSearch workbooks, a master
    universe, and a blocked list. Bucket A is deliberately non-empty (ZZNEW)."""
    repo = tmp_path / "repo"

    # Live fixture: ranked IHI/SCHG/AAPL; stage-A disclosure mixes dict + string.
    fixture = {
        "per_secondary": [
            {"secondary": "IHI"},
            {"secondary": "SCHG"},
            {"secondary": "AAPL"},
        ],
        "stage_a_excluded_secondaries": [
            {"secondary": "AAPB"},            # dict form
            {"secondary": "CURE"},            # dict form
            "AAPU",                            # string form
            "DBA",                             # string form
            {"secondary": "^DJT"},            # disclosed but NO workbook
        ],
    }
    _write(repo / L.DEFAULT_FIXTURE, json.dumps(fixture, indent=2) + "\n")

    # ImpactSearch workbooks. AAPL is ranked (ignored). ZZNEW -> bucket A.
    impact = repo / L.DEFAULT_IMPACTSEARCH_DIR
    for t in ("IHI", "SCHG", "AAPL", "AAPB", "AAPU", "CURE", "DBA", "ZZNEW"):
        _write(impact / (t + L.WORKBOOK_SUFFIX), "x")
    # a non-workbook sibling that must be ignored
    _write(impact / "AAPB_analysis.manifest.json", "{}")

    # Master universe (includes every selectable test ticker + a blocked one).
    master = ["IHI", "SCHG", "AAPL", "AAPB", "AAPU", "CURE", "DBA",
              "ZZNEW", "NEWONE", "BLOCKEDX"]
    _write(repo / L.DEFAULT_MASTER_TICKERS, "\n".join(master) + "\n")

    # Blocked list -> BLOCKEDX is withheld from the known universe.
    _write(repo / L.DEFAULT_BLOCKED_TICKERS, "BLOCKEDX\n")

    (repo / L.DEFAULT_OPERATOR_INPUTS_DIR).mkdir(parents=True, exist_ok=True)
    return repo


class _Recorder:
    """A fake runner that records the (argv, cwd) it received and returns a
    code. Mirrors the launcher's runner(argv, cwd) -> int contract."""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list = []   # argv arrays
        self.cwds: list = []    # cwd strings

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        self.cwds.append(cwd)
        return self.code


def _run(repo, *, answer="", token=SENTINEL_TOKEN, runner=None, argv=None,
         input_counter=None):
    out, err = io.StringIO(), io.StringIO()
    env = {} if token is None else {L.TOKEN_ENV: token}

    def input_func(prompt):
        if input_counter is not None:
            input_counter.append(prompt)
        return answer

    rc = L.main(
        argv if argv is not None else [],
        input_func=input_func,
        env=env,
        runner=runner if runner is not None else _Recorder(0),
        stdout=out,
        stderr=err,
        repo_root=repo,
        clock=FIXED_CLOCK,
    )
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# 1. Token preflight
# ---------------------------------------------------------------------------


def test_token_absent_guidance_nonzero_input_never_called(tmp_path):
    repo = _make_repo(tmp_path)
    called = []

    def input_func(prompt):
        called.append(prompt)
        return "IHI"

    out, err = io.StringIO(), io.StringIO()
    rc = L.main([], input_func=input_func, env={}, runner=_Recorder(0),
                stdout=out, stderr=err, repo_root=repo, clock=FIXED_CLOCK)
    assert rc != 0
    assert called == []  # the question is never reached
    guidance = out.getvalue() + err.getvalue()
    assert L.TOKEN_ENV in guidance
    assert "setx" in guidance
    assert "NEW terminal" in guidance


def test_token_blank_is_treated_as_absent(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err = _run(repo, answer="IHI", token="   ")
    assert rc == 2
    assert "setx" in (out + err)


def test_token_sentinel_never_appears_in_output(tmp_path):
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="IHI, ZZNEW", runner=rec)
    assert rc == 0
    assert SENTINEL_TOKEN not in out
    assert SENTINEL_TOKEN not in err
    # also never embedded into the composed command
    assert all(SENTINEL_TOKEN not in part for part in rec.calls[0])


# ---------------------------------------------------------------------------
# 2. Universe-file preflight (F1: parity, fail-closed before the question)
# ---------------------------------------------------------------------------


def test_blocked_file_absent_fails_before_question(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / L.DEFAULT_BLOCKED_TICKERS).unlink()
    rec = _Recorder(0)
    counter = []
    rc, out, err = _run(repo, answer="IHI", runner=rec, input_counter=counter)
    assert rc != 0
    assert counter == []  # question never asked
    assert rec.calls == []  # nothing launched
    assert L.DEFAULT_BLOCKED_TICKERS.split("/")[-1] in err
    # no secondaries file written
    assert list((repo / L.DEFAULT_OPERATOR_INPUTS_DIR).glob(
        L.SECONDARIES_PREFIX + "*.txt")) == []


def test_master_file_absent_fails_before_question(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / L.DEFAULT_MASTER_TICKERS).unlink()
    rec = _Recorder(0)
    counter = []
    rc, out, err = _run(repo, answer="IHI", runner=rec, input_counter=counter)
    assert rc != 0
    assert counter == []  # question never asked
    assert rec.calls == []
    assert "master" in err.lower()
    assert "every ticker" in err.lower()  # names the []-master consequence


def test_master_file_empty_fails_before_question(tmp_path):
    repo = _make_repo(tmp_path)
    _write(repo / L.DEFAULT_MASTER_TICKERS, "\n")  # present but empty
    counter = []
    rc, out, err = _run(repo, answer="IHI", input_counter=counter)
    assert rc != 0
    assert counter == []


def test_malformed_blocked_file_fails_before_question(tmp_path):
    repo = _make_repo(tmp_path)
    # a token the orchestrator's load_symbol_file rejects (leading '*')
    _write(repo / L.DEFAULT_BLOCKED_TICKERS, "GOOD\n*BAD\n")
    # confirm the orchestrator itself rejects it (parity anchor)
    with pytest.raises(_orch.CrunchError):
        _orch.load_symbol_file(repo / L.DEFAULT_BLOCKED_TICKERS,
                               label="blocked-tickers")
    counter = []
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="IHI", runner=rec, input_counter=counter)
    assert rc != 0
    assert counter == []
    assert rec.calls == []


def test_known_universe_is_master_minus_blocked(tmp_path):
    repo = _make_repo(tmp_path)
    known = L.load_known_universe(
        master_file=repo / L.DEFAULT_MASTER_TICKERS,
        blocked_file=repo / L.DEFAULT_BLOCKED_TICKERS,
    )
    assert "BLOCKEDX" not in known  # master minus blocked
    assert "IHI" in known


def test_universe_parity_smoke_matches_orchestrator_directly(tmp_path):
    # For a tmp master+blocked pair, the launcher's derived allowed set must
    # equal calling the orchestrator functions directly on the same files,
    # including comma/newline tokens and a full-line '#' comment (which
    # load_master_universe skips only when the token STARTS with '#').
    repo = tmp_path / "u"
    master = repo / "master.txt"
    blocked = repo / "blocked.txt"
    _write(master, "IHI\nSCHG, AAPL\n# a comment line\nZZNEW,NEWONE\n")
    _write(blocked, "AAPL\n")
    launcher_allowed = L.load_known_universe(master_file=master,
                                             blocked_file=blocked)
    direct = (set(_orch.load_master_universe(master))
              - set(_orch.load_symbol_file(blocked, label="blocked-tickers")))
    assert launcher_allowed == direct
    assert "AAPL" not in launcher_allowed  # blocked
    assert {"IHI", "SCHG", "ZZNEW", "NEWONE"} <= launcher_allowed


# ---------------------------------------------------------------------------
# 3. Backlog bucket math
# ---------------------------------------------------------------------------


def test_scan_backlog_bucket_math(tmp_path):
    repo = _make_repo(tmp_path)
    b = L.scan_backlog(
        fixture_path=repo / L.DEFAULT_FIXTURE,
        impactsearch_dir=repo / L.DEFAULT_IMPACTSEARCH_DIR,
    )
    assert b["board_row_count"] == 3
    # AAPL workbook is ranked -> ignored from both buckets
    assert "AAPL" not in b["bucket_a"] and "AAPL" not in b["bucket_b"]
    # Stage-A disclosed workbooks (dict AND string forms) -> bucket B
    assert b["bucket_b"] == ["AAPB", "AAPU", "CURE", "DBA"]
    # non-ranked / non-disclosed workbook -> bucket A
    assert b["bucket_a"] == ["ZZNEW"]
    # ^DJT is disclosed but has no workbook -> appears in neither bucket
    assert "^DJT" not in b["bucket_a"] and "^DJT" not in b["bucket_b"]


def test_stage_a_dict_and_string_forms_supported(tmp_path):
    repo = _make_repo(tmp_path)
    ranked, stage_a = L.load_ranked_fixture(repo / L.DEFAULT_FIXTURE)
    assert {"AAPB", "CURE", "AAPU", "DBA", "^DJT"} <= stage_a
    assert ranked == {"IHI", "SCHG", "AAPL"}


# ---------------------------------------------------------------------------
# 4. Display-then-one-question
# ---------------------------------------------------------------------------


def test_backlog_displayed_before_the_one_question(tmp_path):
    repo = _make_repo(tmp_path)
    out = io.StringIO()
    snapshot = {}

    def input_func(prompt):
        snapshot["text"] = out.getvalue()
        snapshot["prompt"] = prompt
        return ""

    L.main([], input_func=input_func, env={L.TOKEN_ENV: SENTINEL_TOKEN},
           runner=_Recorder(0), stdout=out, stderr=io.StringIO(),
           repo_root=repo, clock=FIXED_CLOCK)
    # by the time the question is asked, the backlog is already on screen
    assert "Live board rows: 3" in snapshot["text"]
    assert "bucket A" in snapshot["text"]
    assert "bucket B" in snapshot["text"]
    assert "ZZNEW" in snapshot["text"]
    assert "AAPB" in snapshot["text"]
    assert snapshot["prompt"] == L.QUESTION


def test_input_called_exactly_once_on_question_path(tmp_path):
    repo = _make_repo(tmp_path)
    counter = []
    rc, out, err = _run(repo, answer="IHI", input_counter=counter)
    assert len(counter) == 1


def test_empty_answer_exit_zero_no_write_no_launch(tmp_path):
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="   ", runner=rec)
    assert rc == 0
    assert "No tickers selected." in out
    assert rec.calls == []  # nothing launched
    written = list((repo / L.DEFAULT_OPERATOR_INPUTS_DIR).glob(
        L.SECONDARIES_PREFIX + "*.txt"))
    assert written == []  # no secondaries file


# ---------------------------------------------------------------------------
# 5. Parsing / normalization / dedupe
# ---------------------------------------------------------------------------


def test_parse_tickers_comma_space_case_dedupe():
    resolved, collapsed = L.parse_tickers("ihi, SCHG  aapl,ihi\tSCHG")
    assert resolved == ["IHI", "SCHG", "AAPL"]  # first occurrence preserved
    assert sorted(set(collapsed)) == ["IHI", "SCHG"]


def test_duplicate_note_printed_when_duplicates_collapse(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err = _run(repo, answer="IHI ihi SCHG")
    assert "collapsed duplicate" in out
    assert "IHI" in out


# ---------------------------------------------------------------------------
# 6. Per-ticker validation (unknown vs blocked labeling)
# ---------------------------------------------------------------------------


def test_unknown_ticker_listed_nonzero_no_write_no_launch(tmp_path):
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="IHI, NOPE123", runner=rec)
    assert rc != 0
    assert "fix or drop" in out
    assert "NOPE123 (unknown)" in out
    assert rec.calls == []
    written = list((repo / L.DEFAULT_OPERATOR_INPUTS_DIR).glob(
        L.SECONDARIES_PREFIX + "*.txt"))
    assert written == []


def test_blocked_ticker_typed_is_rejected_and_labeled(tmp_path):
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="BLOCKEDX", runner=rec)
    assert rc != 0
    assert "BLOCKEDX (blocked)" in out  # labeled blocked, distinct from unknown
    assert rec.calls == []
    written = list((repo / L.DEFAULT_OPERATOR_INPUTS_DIR).glob(
        L.SECONDARIES_PREFIX + "*.txt"))
    assert written == []


# ---------------------------------------------------------------------------
# 6b. Caret-secondary validation bridge (existing-board carets like ^DJT)
# ---------------------------------------------------------------------------


def test_caret_secondary_on_board_accepted_reaches_runner(tmp_path):
    # ^DJT is Stage-A-disclosed in the fixture and absent from master; it must
    # now be ACCEPTED, launch exactly one run, and reach the generated
    # secondaries file with the caret spelling preserved.
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    counter = []
    rc, out, err = _run(repo, answer="^DJT", runner=rec, input_counter=counter)
    assert rc == 0
    assert len(counter) == 1            # exactly one question on the accept path
    assert len(rec.calls) == 1          # launched once
    assert "--rebuild-secondaries-file" in rec.calls[0]
    written = list((repo / L.DEFAULT_OPERATOR_INPUTS_DIR).glob(
        L.SECONDARIES_PREFIX + "*.txt"))
    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8") == "^DJT\n"


def test_caret_secondary_ranked_row_accepted(tmp_path):
    # A caret known as a RANKED row (not just Stage-A-disclosed) is accepted.
    repo = _make_repo(tmp_path)
    fixture = json.loads((repo / L.DEFAULT_FIXTURE).read_text(encoding="utf-8"))
    fixture["per_secondary"].append({"secondary": "^GSPC"})
    _write(repo / L.DEFAULT_FIXTURE, json.dumps(fixture, indent=2) + "\n")
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="^GSPC", runner=rec)
    assert rc == 0
    assert len(rec.calls) == 1


def test_blocked_caret_secondary_rejected(tmp_path):
    # A caret on the board but ALSO in the blocked set is rejected, no launch.
    repo = _make_repo(tmp_path)
    _write(repo / L.DEFAULT_BLOCKED_TICKERS, "BLOCKEDX\n^DJT\n")
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="^DJT", runner=rec)
    assert rc != 0
    assert "^DJT (blocked)" in out
    assert rec.calls == []


def test_arbitrary_caret_not_on_board_rejected(tmp_path):
    # A syntactically valid caret that is NOT ranked and NOT Stage-A-disclosed
    # is rejected -- syntactic validity alone does not authorize it.
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="^FOO", runner=rec)
    assert rc != 0
    assert "^FOO (unknown)" in out
    assert rec.calls == []
    written = list((repo / L.DEFAULT_OPERATOR_INPUTS_DIR).glob(
        L.SECONDARIES_PREFIX + "*.txt"))
    assert written == []


def test_malformed_caret_rejected(tmp_path):
    # A caret token that fails the orchestrator's ticker regex is rejected as
    # malformed (never silently treated as on-board).
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="^DJ@T", runner=rec)
    assert rc != 0
    assert "^DJ@T (malformed)" in out
    assert rec.calls == []


def test_bare_master_token_does_not_authorize_caret(tmp_path):
    # Bare DJT in master must NOT by itself authorize ^DJT. Add bare DJT to
    # master, REMOVE ^DJT from the board disclosure -> ^DJT is rejected.
    repo = _make_repo(tmp_path)
    master = ["IHI", "SCHG", "AAPL", "AAPB", "AAPU", "CURE", "DBA",
              "ZZNEW", "NEWONE", "BLOCKEDX", "DJT"]
    _write(repo / L.DEFAULT_MASTER_TICKERS, "\n".join(master) + "\n")
    fixture = json.loads((repo / L.DEFAULT_FIXTURE).read_text(encoding="utf-8"))
    fixture["stage_a_excluded_secondaries"] = [
        e for e in fixture["stage_a_excluded_secondaries"]
        if not (isinstance(e, dict) and e.get("secondary") == "^DJT")
    ]
    _write(repo / L.DEFAULT_FIXTURE, json.dumps(fixture, indent=2) + "\n")
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="^DJT", runner=rec)
    assert rc != 0                       # bare DJT does NOT authorize ^DJT
    assert "^DJT (unknown)" in out
    assert rec.calls == []


def test_noncaret_unknown_still_rejected_alongside_caret_accept(tmp_path):
    # Mixing an accepted caret with a non-caret unknown still aborts the whole
    # launch -- non-caret typo/unknown protection is unchanged.
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="^DJT, NOPE123", runner=rec)
    assert rc != 0
    assert "NOPE123 (unknown)" in out
    assert rec.calls == []


# ---------------------------------------------------------------------------
# 7. Classification + bucket-source reporting
# ---------------------------------------------------------------------------


def test_new_vs_refresh_classification(tmp_path):
    repo = _make_repo(tmp_path)
    # IHI is ranked (REFRESH); NEWONE is a known, non-ranked symbol (NEW)
    rc, out, err = _run(repo, answer="IHI, NEWONE")
    assert rc == 0
    assert "REFRESH (1): IHI" in out
    assert "NEW (1): NEWONE" in out


def test_selected_bucket_b_reported(tmp_path):
    repo = _make_repo(tmp_path)
    # AAPB is a bucket B ticker (and is in the known universe)
    rc, out, err = _run(repo, answer="AAPB")
    assert rc == 0
    assert "From backlog bucket B" in out
    assert "AAPB" in out


def test_selected_bucket_a_reported(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err = _run(repo, answer="ZZNEW")
    assert rc == 0
    assert "From backlog bucket A" in out
    assert "ZZNEW" in out


# ---------------------------------------------------------------------------
# 8. Secondaries file write
# ---------------------------------------------------------------------------


def test_secondaries_file_written_with_exact_resolved_list(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err = _run(repo, answer="IHI, ZZNEW,  SCHG")
    assert rc == 0
    written = list((repo / L.DEFAULT_OPERATOR_INPUTS_DIR).glob(
        L.SECONDARIES_PREFIX + "*.txt"))
    assert len(written) == 1
    path = written[0]
    assert path.read_text(encoding="utf-8") == "IHI\nZZNEW\nSCHG\n"


def test_written_file_under_intended_gitignored_location(tmp_path):
    repo = _make_repo(tmp_path)
    rc, out, err = _run(repo, answer="IHI")
    written = list((repo / L.DEFAULT_OPERATOR_INPUTS_DIR).glob(
        L.SECONDARIES_PREFIX + "*.txt"))
    assert len(written) == 1
    path = written[0]
    assert path.parent.resolve() == (repo / L.DEFAULT_OPERATOR_INPUTS_DIR).resolve()
    assert path.name == L.SECONDARIES_PREFIX + FIXED_TS + ".txt"
    assert path.suffix == ".txt"  # covered by the repo-root *.txt rule
    assert "Covered by .gitignore rule" in out


# ---------------------------------------------------------------------------
# 9. Composed argv + runner contract (F2: absolute paths + cwd)
# ---------------------------------------------------------------------------


def _expected_paths(tmp_path):
    repo_resolved = (tmp_path / "repo").resolve()
    orch = (repo_resolved / L.ORCHESTRATOR_SCRIPT).as_posix()
    sec = (repo_resolved / L.DEFAULT_OPERATOR_INPUTS_DIR
           / (L.SECONDARIES_PREFIX + FIXED_TS + ".txt")).as_posix()
    return repo_resolved, orch, sec


def test_composed_argv_absolute_with_cwd_and_passthrough(tmp_path):
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    extra = ["--target-as-of", "2026-06-10",
             "--reuse-onepass-run-dir", "output/crunch_runs/20260606T053735Z"]
    rc, out, err = _run(repo, answer="IHI, ZZNEW", runner=rec, argv=extra)
    assert rc == 0
    assert len(rec.calls) == 1
    repo_resolved, orch, sec = _expected_paths(tmp_path)
    assert rec.calls[0] == [
        L.PINNED_PYTHON,
        orch,                         # ABSOLUTE orchestrator script path
        "--execute",
        "--publish",
        "--operator-approved-publish",
        "--rebuild-secondaries-file",
        sec,                          # ABSOLUTE secondaries path
        "--target-as-of", "2026-06-10",
        "--reuse-onepass-run-dir", "output/crunch_runs/20260606T053735Z",
    ]
    # the subprocess runs with cwd == repo_root
    assert rec.cwds[0] == repo_resolved.as_posix()
    # both absolute paths are diagnosable in the printed command + working dir
    assert orch in out and sec in out
    assert repo_resolved.as_posix() in out


def test_launch_from_foreign_cwd_composes_identically(tmp_path, monkeypatch):
    # Invoking the launcher from a foreign working directory must not change the
    # composed command or the cwd handed to the runner (paths are absolute and
    # cwd is the injected repo_root).
    repo = _make_repo(tmp_path)
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()
    monkeypatch.chdir(foreign)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="IHI", runner=rec)
    assert rc == 0
    repo_resolved, orch, sec = _expected_paths(tmp_path)
    assert rec.calls[0][1] == orch        # absolute, independent of cwd
    assert rec.calls[0][6] == sec
    assert rec.cwds[0] == repo_resolved.as_posix()


def test_build_orchestrator_argv_unit():
    argv = L.build_orchestrator_argv("/abs/repo/crunch_rebuild_orchestrator.py",
                                     "/abs/repo/operator_inputs/x.txt",
                                     ["--flag", "v"])
    assert argv[0] == L.PINNED_PYTHON
    assert argv[1] == "/abs/repo/crunch_rebuild_orchestrator.py"
    assert argv[2:7] == ["--execute", "--publish", "--operator-approved-publish",
                         "--rebuild-secondaries-file",
                         "/abs/repo/operator_inputs/x.txt"]
    assert argv[7:] == ["--flag", "v"]


def test_runner_receives_argv_array_and_cwd_not_shell_string(tmp_path):
    repo = _make_repo(tmp_path)
    rec = _Recorder(0)
    rc, out, err = _run(repo, answer="IHI", runner=rec)
    assert isinstance(rec.calls[0], list)
    assert all(isinstance(part, str) for part in rec.calls[0])
    assert isinstance(rec.cwds[0], str)


def test_runner_exit_code_is_returned(tmp_path):
    repo = _make_repo(tmp_path)
    rec = _Recorder(7)
    rc, out, err = _run(repo, answer="IHI", runner=rec)
    assert rc == 7
