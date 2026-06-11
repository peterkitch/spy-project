"""One-question Stage 9 publish launcher.

This is the thin operator launcher for an autonomous Build-and-Rank publish run.
It contains NO publish logic. It only:

  1. preflights the Blob token (presence only -- never the value),
  2. runs a read-only backlog scan (live fixture + ImpactSearch workbooks),
  3. asks EXACTLY ONE question ("which tickers?"),
  4. validates the answer against the orchestrator's known universe,
  5. classifies NEW vs REFRESH,
  6. writes a timestamped (gitignored) operator secondaries file, and
  7. composes + runs the orchestrator publish command, streaming its output.

The actual publish tail lives in stage9_publish.py and is reached only through
crunch_rebuild_orchestrator.py --publish / --operator-approved-publish. This
launcher never runs engines, Blob, promote, deploy, or git itself.

All runtime output is ASCII-only (cp1252 console substrate; CLAUDE.md C9).

Every external surface (input, environment, filesystem roots, subprocess runner,
clock, output streams) is injectable so the module is hermetically testable
without a real orchestrator, network, Blob, or token value.
"""
from __future__ import annotations

import os
import re
import sys
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, TextIO

# Reuse the orchestrator's OWN universe parsing so launcher validation has exact
# source parity with the run. The import is side-effect-free (the module guards
# all execution behind ``if __name__ == "__main__"``; its own 148-test suite
# imports it the same way). load_symbol_file/load_master_universe/normalize_ticker
# are the single source of truth for how master and blocked lists are parsed.
import crunch_rebuild_orchestrator as _orch

# --- fixed launch surface ---------------------------------------------------

# CLAUDE.md PART C1 pinned interpreter (forward slashes; argv array, no shell).
PINNED_PYTHON = "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe"
ORCHESTRATOR_SCRIPT = "crunch_rebuild_orchestrator.py"

TOKEN_ENV = "BLOB_READ_WRITE_TOKEN"

# Repo-relative defaults (resolved against repo_root). These mirror the
# orchestrator's own defaults so the launcher validates against the SAME
# authoritative universe the OnePass/orchestrator flow builds against.
DEFAULT_FIXTURE = "frontend/public/fixtures/k6_mtf_ranking.json"
DEFAULT_IMPACTSEARCH_DIR = "output/impactsearch"
DEFAULT_MASTER_TICKERS = "global_ticker_library/data/master_tickers.txt"
DEFAULT_BLOCKED_TICKERS = "operator_inputs/crunch_blocked_tickers.txt"
DEFAULT_OPERATOR_INPUTS_DIR = "operator_inputs"

WORKBOOK_SUFFIX = "_analysis.xlsx"
SECONDARIES_PREFIX = "crunch_rebuild_secondaries_"

QUESTION = "Which tickers? (comma or space separated): "

# The repo-root .gitignore rule that covers the generated secondaries file.
GITIGNORE_RULE = "*.txt (repository-root .gitignore)"


# --- small helpers ----------------------------------------------------------


def _norm(value: object) -> str:
    """Normalize a ticker exactly as the orchestrator does (strip + upper).
    Delegates to the orchestrator's normalize_ticker so parsing stays in lock
    step with the run."""
    return _orch.normalize_ticker(value)


# --- step building blocks (importable, testable) ----------------------------


def load_ranked_fixture(fixture_path: Path) -> tuple[set[str], set[str]]:
    """Read the committed live fixture. Returns (ranked, stage_a_disclosed).

    ranked = secondaries with a board row (per_secondary[].secondary).
    stage_a_disclosed = secondaries in stage_a_excluded_secondaries, supporting
    BOTH dict entries (keyed 'secondary') and bare string entries.
    """
    data = json.loads(Path(fixture_path).read_text(encoding="utf-8"))

    ranked: set[str] = set()
    for row in data.get("per_secondary") or []:
        if isinstance(row, dict):
            sec = row.get("secondary")
        else:
            sec = row
        if sec:
            ranked.add(_norm(sec))

    stage_a: set[str] = set()
    for entry in data.get("stage_a_excluded_secondaries") or []:
        if isinstance(entry, dict):
            sec = entry.get("secondary")
        else:
            sec = entry
        if sec:
            stage_a.add(_norm(sec))

    return ranked, stage_a


def scan_workbook_tickers(impactsearch_dir: Path) -> set[str]:
    """Derive workbook tickers from output/impactsearch/<TICKER>_analysis.xlsx
    filenames (the existing repository naming convention). Read-only."""
    out: set[str] = set()
    d = Path(impactsearch_dir)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*" + WORKBOOK_SUFFIX)):
        name = p.name[: -len(WORKBOOK_SUFFIX)]
        if name:
            out.add(_norm(name))
    return out


def scan_backlog(*, fixture_path: Path, impactsearch_dir: Path) -> dict:
    """Read-only ledger scan. Computes the two backlog buckets. Nothing here is
    ever auto-added; the buckets are informational only.

    Bucket A "never attempted": workbook ticker that is NOT ranked and NOT
    Stage-A-disclosed.
    Bucket B "previously Stage-A-excluded": workbook ticker that is NOT ranked
    and IS Stage-A-disclosed.
    """
    ranked, stage_a = load_ranked_fixture(fixture_path)
    workbooks = scan_workbook_tickers(impactsearch_dir)
    bucket_a = sorted(w for w in workbooks if w not in ranked and w not in stage_a)
    bucket_b = sorted(w for w in workbooks if w not in ranked and w in stage_a)
    return {
        "ranked": ranked,
        "stage_a": stage_a,
        "workbooks": workbooks,
        "bucket_a": bucket_a,
        "bucket_b": bucket_b,
        "board_row_count": len(ranked),
    }


def parse_tickers(answer: str) -> tuple[list[str], list[str]]:
    """Normalize the typed answer: split on commas/whitespace, strip, uppercase,
    dedupe preserving first occurrence. Returns (resolved, collapsed) where
    collapsed lists the duplicate tokens that were dropped."""
    resolved: list[str] = []
    collapsed: list[str] = []
    seen: set[str] = set()
    for tok in re.split(r"[,\s]+", answer.strip()):
        t = _norm(tok)
        if not t:
            continue
        if t in seen:
            collapsed.append(t)
            continue
        seen.add(t)
        resolved.append(t)
    return resolved, collapsed


def load_master_set(master_file: Path) -> set[str]:
    """The master universe, parsed by the orchestrator's own load_master_universe
    (returns an EMPTY set if the master file is absent or empty -- the same
    []-master semantics the run uses, under which it would reject every
    ticker)."""
    return set(_orch.load_master_universe(Path(master_file)))


def load_blocked_set(blocked_file: Path) -> set[str]:
    """The blocked (exclusion) set, parsed by the orchestrator's own
    load_symbol_file. Like the run's preflight, this RAISES _orch.CrunchError
    when the blocked file is missing, empty, malformed, non-ASCII, or
    unreadable -- the launcher does not tolerate a missing blocked file."""
    return set(_orch.load_symbol_file(Path(blocked_file), label="blocked-tickers"))


def load_known_universe(*, master_file: Path, blocked_file: Path) -> set[str]:
    """The authoritative known universe used by the orchestrator/OnePass flow:
    the master ticker list MINUS the blocked (exclusion) set -- derived with the
    orchestrator's OWN functions, exactly as preflight() does
    (``allowed_universe = [t for t in master if t not in excl_set]``). Raises
    _orch.CrunchError on a missing/empty/malformed blocked file (parity with the
    run)."""
    return load_master_set(master_file) - load_blocked_set(blocked_file)


def write_secondaries_file(tickers: Sequence[str], *, operator_inputs_dir: Path,
                           timestamp: str) -> Path:
    """Write the resolved ticker list (one per line, LF) to a timestamped file
    under the gitignored operator inputs directory. Returns the path."""
    d = Path(operator_inputs_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / (SECONDARIES_PREFIX + timestamp + ".txt")
    text = "".join(t + "\n" for t in tickers)
    # newline="" so the explicit LF terminators are written verbatim (LF on
    # every platform; the *.txt files are not eol-pinned but stay LF here).
    path.write_text(text, encoding="utf-8", newline="")
    return path


def build_orchestrator_argv(orchestrator_path: str, secondaries_file: str,
                            extra_args: Iterable[str], *,
                            python: str = PINNED_PYTHON) -> list[str]:
    """Compose the orchestrator publish command as an argv ARRAY (no shell).
    Both the orchestrator script and the secondaries file are passed as ABSOLUTE
    paths so the command is independent of the caller's working directory. Extra
    launcher args are appended verbatim AFTER the launcher's own args."""
    return [
        python,
        str(orchestrator_path),
        "--execute",
        "--publish",
        "--operator-approved-publish",
        "--rebuild-secondaries-file",
        str(secondaries_file),
        *list(extra_args),
    ]


def _default_runner(argv: Sequence[str], cwd: str) -> int:
    """Run the composed command with cwd=repo_root (the orchestrator resolves
    output/ and other relative paths from its working directory), inheriting
    parent stdio so the orchestrator's output streams live. Argv array only;
    never shell=True."""
    proc = subprocess.run(list(argv), cwd=str(cwd))  # noqa: S603 - argv array, no shell
    return int(proc.returncode)


# --- token preflight guidance (never prints the value) ----------------------


def _print_token_guidance(err: TextIO) -> None:
    lines = [
        "[FAIL] " + TOKEN_ENV + " is not set (or is blank) in this environment.",
        "Publish cannot start without it. Set it ONCE in your Windows user",
        "environment, then open a NEW terminal:",
        "",
        '    setx ' + TOKEN_ENV + ' "<your-token-value>"',
        "",
        "setx does NOT update already-open shells -- open a NEW terminal",
        "afterward, then re-run this launcher.",
    ]
    for line in lines:
        print(line, file=err)


def _print_blocked_file_guidance(err: TextIO, blocked_path: Path,
                                 reason: str) -> None:
    lines = [
        "[FAIL] blocked-tickers file is unusable: " + blocked_path.as_posix(),
        "Reason: " + reason,
        "The orchestrator's preflight requires this file (it is never optional);",
        "the launcher refuses rather than approve tickers the run would not see.",
        "Create/repair the file (one symbol per line) and re-run.",
    ]
    for line in lines:
        print(line, file=err)


def _print_master_file_guidance(err: TextIO, master_path: Path) -> None:
    lines = [
        "[FAIL] master ticker universe is empty or absent: "
        + master_path.as_posix(),
        "With an empty master universe the run would reject EVERY ticker",
        "(allowed = master minus blocked = empty). Refusing before the prompt.",
        "Restore the master ticker list and re-run.",
    ]
    for line in lines:
        print(line, file=err)


# --- entry point ------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None, *,
         input_func: Callable[[str], str] = input,
         env: Optional[dict] = None,
         runner: Optional[Callable[[Sequence[str], str], int]] = None,
         stdout: Optional[TextIO] = None,
         stderr: Optional[TextIO] = None,
         repo_root: Optional[Path] = None,
         clock: Optional[Callable[[], datetime]] = None) -> int:
    """Run the launcher. Returns a process exit code.

    On the publish path the return code IS the orchestrator process exit code.
    Token-absent / empty-answer / unknown-symbol paths return their own codes
    and never launch anything.
    """
    env = os.environ if env is None else env
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    runner = runner if runner is not None else _default_runner
    clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
    # Resolve repo_root to an ABSOLUTE path so the composed command and the
    # subprocess cwd are independent of the caller's working directory.
    repo = (Path(repo_root) if repo_root is not None
            else Path(__file__).resolve().parent).resolve()
    extra_args = list(sys.argv[1:] if argv is None else argv)

    def emit(line: str = "") -> None:
        print(line, file=out)

    # 1. TOKEN PREFLIGHT FIRST -- before any question or scan side effects.
    token = str(env.get(TOKEN_ENV, "") or "")
    if not token.strip():
        _print_token_guidance(err)
        return 2

    # 2. UNIVERSE FILES PREFLIGHT -- load the SAME files the run validates
    # against, BEFORE the question, so an environment problem fails before the
    # operator answers. master uses load_master_universe ([]-on-absent); blocked
    # uses load_symbol_file (RAISES on missing/empty/malformed). The launcher is
    # never looser than the run: it refuses here on either problem.
    master_path = repo / DEFAULT_MASTER_TICKERS
    blocked_path = repo / DEFAULT_BLOCKED_TICKERS
    master_set = load_master_set(master_path)
    if not master_set:
        _print_master_file_guidance(err, master_path)
        return 4
    try:
        blocked_set = load_blocked_set(blocked_path)
    except _orch.CrunchError as exc:
        _print_blocked_file_guidance(err, blocked_path, type(exc).__name__)
        return 5
    allowed_set = master_set - blocked_set

    # 3. READ-ONLY LEDGER SCAN.
    backlog = scan_backlog(
        fixture_path=repo / DEFAULT_FIXTURE,
        impactsearch_dir=repo / DEFAULT_IMPACTSEARCH_DIR,
    )

    # 4. DISPLAY BACKLOG, then ask EXACTLY ONE question.
    emit("Live board rows: %d" % backlog["board_row_count"])
    emit("Backlog bucket A (never attempted): "
         + (", ".join(backlog["bucket_a"]) if backlog["bucket_a"] else "none"))
    emit("Backlog bucket B (previously Stage-A-excluded): "
         + (", ".join(backlog["bucket_b"]) if backlog["bucket_b"] else "none"))
    emit("Informational only -- include any ticker by typing it; "
         "nothing is auto-added.")

    # Exactly one prompt. There is no loop and no second call site, so a second
    # question is structurally impossible.
    answer = input_func(QUESTION)

    # 5. PARSE TICKERS.
    resolved, collapsed = parse_tickers(answer or "")
    if not resolved:
        emit("No tickers selected.")
        return 0
    if collapsed:
        emit("Note: collapsed duplicate(s): " + ", ".join(sorted(set(collapsed))))

    # 6. PER-TICKER VALIDATION against the already-loaded allowed set. A typed
    # ticker that is not allowed is rejected under the fix-or-drop flow, labeled
    # BLOCKED (present in the exclusion set) or UNKNOWN (not in the master
    # universe) so the operator knows why. Blocked takes precedence in labeling.
    rejected = [(t, "blocked" if t in blocked_set else "unknown")
                for t in resolved if t not in allowed_set]
    if rejected:
        emit("Cannot start -- the following tickers are not in the allowed "
             "universe (fix or drop):")
        for t, why in rejected:
            emit("  %s (%s)" % (t, why))
        return 3

    # 7. CLASSIFY SELECTION.
    ranked = backlog["ranked"]
    bucket_a = set(backlog["bucket_a"])
    bucket_b = set(backlog["bucket_b"])
    new = [t for t in resolved if t not in ranked]
    refresh = [t for t in resolved if t in ranked]
    from_a = [t for t in resolved if t in bucket_a]
    from_b = [t for t in resolved if t in bucket_b]
    emit("Selected (resolved order): " + ", ".join(resolved))
    emit("NEW (%d): %s" % (len(new), ", ".join(new) if new else "none"))
    emit("REFRESH (%d): %s" % (len(refresh), ", ".join(refresh) if refresh else "none"))
    if from_a:
        emit("From backlog bucket A (never attempted): " + ", ".join(from_a))
    if from_b:
        emit("From backlog bucket B (previously Stage-A-excluded): "
             + ", ".join(from_b))

    # 8. WRITE RESOLVED SECONDARIES FILE (timestamped, gitignored).
    timestamp = clock().strftime("%Y%m%dT%H%M%SZ")
    secondaries_file = write_secondaries_file(
        resolved,
        operator_inputs_dir=repo / DEFAULT_OPERATOR_INPUTS_DIR,
        timestamp=timestamp,
    )
    emit("Wrote secondaries file: " + secondaries_file.as_posix())
    emit("Covered by .gitignore rule: " + GITIGNORE_RULE)

    # 9. LAUNCH ORCHESTRATOR. Absolute script + secondaries paths, executed with
    # cwd=repo_root so the orchestrator resolves output/ and other relative paths
    # correctly regardless of where the launcher was invoked from. Argv array,
    # no shell. Exit with the orchestrator's process code.
    orchestrator_path = (repo / ORCHESTRATOR_SCRIPT).as_posix()
    cmd = build_orchestrator_argv(orchestrator_path, secondaries_file.as_posix(),
                                  extra_args)
    emit("Launching orchestrator (working directory: " + repo.as_posix() + "):")
    emit("  " + " ".join(cmd))
    return int(runner(cmd, repo.as_posix()))


if __name__ == "__main__":  # pragma: no cover - thin CLI guard
    sys.exit(main())
