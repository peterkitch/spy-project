"""Phase 6H-5 tests for daily_board_automation_writer.

Pins the guarded write-capable executor contract:

  - Two-key auth: --write CLI flag AND
    PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit. Either
    missing -> rc=2 + no writes.
  - Without --write, default dry-run path computes the
    plan and emits a write_authorized=False record with
    every outcome field None and no commands recorded.
  - refresh_source_cache_then_pipeline EXECUTES refresh,
    then re-runs the watcher, then EXECUTES pipeline only
    when watcher returns ready_for_pipeline_write.
  - refresh_source_cache_then_pipeline STOPS after refresh
    when watcher returns pipeline_output_lags_persist_skip;
    final_recommended_action becomes
    refresh_executed_pipeline_withheld; the ticker appears
    in skipped_pipeline_after_refresh_tickers.
  - run_pipeline_only executes the pipeline runner once and
    records the readiness verdict.
  - already_current / wait / manual / blocked actions never
    execute writes.
  - Execution log is JSONL append-only and contains the
    per-ticker stage sequence (commands + functions).
  - No production cache / output / signal_library /
    stackbuilder paths are touched in tests; all writes go
    to operator-supplied temp directories.
  - Forbidden-imports static guard: yfinance, dash,
    daily_signal_board, subprocess at the module top
    level. (signal_engine_cache_refresher and
    confluence_pipeline_runner are imported LAZILY inside
    helper resolvers -- the top-level AST must not
    reference them.)
"""
from __future__ import annotations

import ast
import json
import os
import pickle
import sys
import time
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import daily_board_automation_preflight as dap  # noqa: E402
import daily_board_automation_writer as dbw  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers (shape-aligned with the rest of the Phase 6H suites)
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    stack_dir = tmp_path / "stackbuilder"
    sig_dir = tmp_path / "siglib"
    for d in (cache_dir, artifact_root, stack_dir, sig_dir):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache_dir,
        "artifact_root": artifact_root,
        "stackbuilder_root": stack_dir,
        "signal_library_dir": sig_dir,
    }


def _safe_filename(ticker: str) -> str:
    return f"{ticker.upper().replace('^', '_')}_precomputed_results.pkl"


def _write_cache_pkl(
    cache_dir: Path,
    ticker: str,
    *,
    last_date: str = "2026-05-08",
) -> Path:
    import datetime as _dt

    cache_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.strptime(last_date, "%Y-%m-%d")
    payload = {"_last_date": ts, "last_date": ts}
    path = cache_dir / _safe_filename(ticker)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_stackbuilder_run(
    stack_root: Path,
    target: str,
    *,
    seed: str = "seedTC__AAA-D_BBB-D",
) -> Path:
    import pandas as pd

    run_dir = stack_root / target.upper() / seed
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = [{
        "K": k,
        "Trigger Days": 100 + k,
        "Total Capture (%)": 10.0 + k,
        "Sharpe Ratio": 0.1,
        "p-Value": 0.05,
        "Members": "['AAA[D]', 'BBB[D]']",
    } for k in range(1, 13)]
    pd.DataFrame(rows, columns=[
        "K", "Trigger Days", "Total Capture (%)",
        "Sharpe Ratio", "p-Value", "Members",
    ]).to_excel(run_dir / "combo_leaderboard.xlsx", index=False)
    return run_dir


def _write_multitimeframe_libs(
    sig_dir: Path, ticker: str, intervals: list[str],
) -> None:
    safe = ticker.upper().replace("^", "_")
    for interval in intervals:
        (sig_dir / f"{safe}_stable_v1_0_0_{interval}.pkl"
         ).write_bytes(b"placeholder")


def _snapshot_tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# Fake callables for dependency injection
# ---------------------------------------------------------------------------


class _FakeRefreshResult:
    def __init__(
        self,
        *,
        refreshed: bool,
        old: str,
        new: str,
        stale_before: bool = True,
        current_after: bool = True,
        issue_codes: tuple[str, ...] = (),
    ):
        self.refreshed = refreshed
        self.old_cache_date_range_end = old
        self.new_cache_date_range_end = new
        self.stale_before = stale_before
        self.current_after = current_after
        self.issue_codes = issue_codes


class _FakeWatcherState:
    def __init__(
        self,
        *,
        action: str,
        cache_date_range_end: str | None,
        current_as_of_date: str,
    ):
        self.recommended_operator_action = action
        self.cache_date_range_end = cache_date_range_end
        self.current_as_of_date = current_as_of_date


class _FakeReadiness:
    def __init__(
        self,
        *,
        leader_eligible: bool,
        issue_codes: tuple[str, ...] = (),
        current_as_of_date: str = "2026-05-08",
    ):
        self.leader_eligible = leader_eligible
        self.issue_codes = issue_codes
        self.current_as_of_date = current_as_of_date


class _FakePipelineRunResult:
    def __init__(
        self,
        *,
        leader_eligible: bool,
        ranking_blocked_reason: str = "",
        issue_codes: tuple[str, ...] = (),
        readiness: _FakeReadiness | None = None,
    ):
        self.leader_eligible = leader_eligible
        self.ranking_blocked_reason = ranking_blocked_reason
        self.issue_codes = issue_codes
        self.readiness = readiness


class _CallRecorder:
    """Records every call into a fake so the test can assert
    nothing fired or assert the exact call ordering."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def record(self, name: str, **kwargs):
        self.calls.append((name, kwargs))


def _refresher_factory(
    recorder: _CallRecorder, result: _FakeRefreshResult,
):
    def fake_refresher(ticker, **kwargs):
        recorder.record(
            "refresher", ticker=ticker, **kwargs,
        )
        return result
    return fake_refresher


def _pipeline_runner_factory(
    recorder: _CallRecorder, result: _FakePipelineRunResult,
):
    def fake_pipeline(ticker, **kwargs):
        recorder.record(
            "pipeline_runner", ticker=ticker, **kwargs,
        )
        return result
    return fake_pipeline


def _watcher_factory(
    recorder: _CallRecorder, state: _FakeWatcherState,
):
    def fake_watcher(ticker, **kwargs):
        recorder.record(
            "watcher", ticker=ticker, **kwargs,
        )
        return state
    return fake_watcher


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_writer_module_has_no_forbidden_top_level_imports():
    """The top-level module must not import the writer
    Python modules; their CLIs are referenced as strings only
    and their callables are resolved lazily inside helper
    functions. ``yfinance``, ``dash``, ``daily_signal_board``,
    and ``subprocess`` must also be absent."""
    tree = ast.parse(
        Path(dbw.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance",
        "trafficflow",
        "spymaster",
        "impactsearch",
        "onepass",
        "confluence",
        "cross_ticker_confluence",
        "dash",
        "daily_signal_board",
        "subprocess",
    }
    top_level_imports: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_imports.append(node.module)
    bad = [
        m for m in top_level_imports
        if m.split(".")[0] in forbidden
    ]
    assert not bad, (
        "forbidden top-level import in "
        f"daily_board_automation_writer: {bad!r}"
    )
    # Also assert that signal_engine_cache_refresher and
    # confluence_pipeline_runner are not at module top
    # level; they must be lazy imports.
    lazy_only = {
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
    }
    leaked = [
        m for m in top_level_imports
        if m.split(".")[0] in lazy_only
    ]
    assert not leaked, (
        f"{leaked!r} must be lazy-imported, not top-level"
    )


# ---------------------------------------------------------------------------
# 2. Two-key write authorization
# ---------------------------------------------------------------------------


def test_resolve_auth_no_cli_write_no_env_is_dry_run():
    auth = dbw.resolve_write_authorization(
        cli_write_requested=False, env={},
    )
    assert auth.cli_write_requested is False
    assert auth.authorized is False
    assert auth.env_var_value is None


def test_resolve_auth_cli_write_no_env_is_not_authorized():
    auth = dbw.resolve_write_authorization(
        cli_write_requested=True, env={},
    )
    assert auth.cli_write_requested is True
    assert auth.authorized is False
    assert "not equal" in auth.reason


def test_resolve_auth_cli_write_wrong_env_value_is_not_authorized():
    auth = dbw.resolve_write_authorization(
        cli_write_requested=True,
        env={dbw.ENV_VAR_NAME: "wrong_value"},
    )
    assert auth.cli_write_requested is True
    assert auth.authorized is False
    assert auth.env_var_value == "wrong_value"


def test_resolve_auth_both_keys_authorizes():
    auth = dbw.resolve_write_authorization(
        cli_write_requested=True,
        env={dbw.ENV_VAR_NAME: dbw.ENV_VAR_REQUIRED_VALUE},
    )
    assert auth.cli_write_requested is True
    assert auth.authorized is True
    assert auth.env_var_value == dbw.ENV_VAR_REQUIRED_VALUE


def test_resolve_auth_env_var_alone_is_dry_run_not_error():
    """Env var set but --write not requested: still dry-run.
    The two-key gate is enforced only when the operator
    explicitly asks for the live path."""
    auth = dbw.resolve_write_authorization(
        cli_write_requested=False,
        env={dbw.ENV_VAR_NAME: dbw.ENV_VAR_REQUIRED_VALUE},
    )
    assert auth.cli_write_requested is False
    assert auth.authorized is False


# ---------------------------------------------------------------------------
# 3. Dry-run path (default; no --write)
# ---------------------------------------------------------------------------


def test_dry_run_records_plan_but_calls_no_writers(
    tmp_path: Path,
):
    """When write_authorized=False the executor must NOT
    resolve or call refresher / pipeline runner. The
    per-ticker record carries the plan verdict and every
    outcome field is None."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    recorder = _CallRecorder()

    def _no_call_refresher(*a, **k):
        recorder.record("refresher", **k)
        raise AssertionError(
            "refresher must not be called on the dry-run path"
        )

    def _no_call_pipeline(*a, **k):
        recorder.record("pipeline_runner", **k)
        raise AssertionError(
            "pipeline_runner must not be called on the dry-run path"
        )

    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=False,
        current_as_of_date="2026-05-08",
        refresher=_no_call_refresher,
        pipeline_runner=_no_call_pipeline,
        **dirs,
    )
    assert report.dry_run is True
    assert report.write_authorized is False
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE
    )
    assert exec_state.final_recommended_action == (
        dbw.FINAL_WRITE_NOT_AUTHORIZED
    )
    assert exec_state.refresh_result is None
    assert exec_state.pipeline_result is None
    assert exec_state.post_refresh_watcher_action is None
    assert exec_state.final_readiness is None
    assert exec_state.commands_executed == ()
    assert exec_state.functions_executed == ()
    assert exec_state.write_authorized is False
    assert exec_state.skipped_reason == (
        dbw.SKIP_WRITE_NOT_AUTHORIZED
    )
    # No fake was called.
    assert recorder.calls == []


def test_dry_run_writes_nothing_to_any_root(tmp_path: Path):
    """Belt-and-suspenders: snapshot every operator-supplied
    root before/after the dry-run; no file may have been
    added, modified, or removed."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    before = {
        name: _snapshot_tree(p) for name, p in dirs.items()
    }
    dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=False,
        current_as_of_date="2026-05-08",
        refresher=lambda *a, **k: pytest.fail("refresher called"),
        pipeline_runner=lambda *a, **k: pytest.fail(
            "pipeline_runner called",
        ),
        **dirs,
    )
    after = {
        name: _snapshot_tree(p) for name, p in dirs.items()
    }
    for name in dirs:
        assert after[name] == before[name], (
            f"dry-run wrote to {name}: "
            f"{after[name] - before[name]}"
        )


# ---------------------------------------------------------------------------
# 4. Write path: refresh -> recheck -> pipeline sequencing
# ---------------------------------------------------------------------------


def test_refresh_then_pipeline_runs_pipeline_when_watcher_ready(
    tmp_path: Path,
):
    """Initial plan = refresh_source_cache_then_pipeline.
    Refresh fake reports refreshed=True with new cache date
    strictly past cutoff. Post-refresh watcher fake returns
    ready_for_pipeline_write -> pipeline must execute."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )

    recorder = _CallRecorder()
    refresher = _refresher_factory(
        recorder,
        _FakeRefreshResult(
            refreshed=True,
            old="2024-01-31",
            new="2026-05-12",
            stale_before=True,
            current_after=True,
            issue_codes=(),
        ),
    )
    watcher = _watcher_factory(
        recorder,
        _FakeWatcherState(
            action="ready_for_pipeline_write",
            cache_date_range_end="2026-05-12",
            current_as_of_date="2026-05-08",
        ),
    )
    pipeline_runner = _pipeline_runner_factory(
        recorder,
        _FakePipelineRunResult(
            leader_eligible=True,
            ranking_blocked_reason="",
            issue_codes=(),
            readiness=_FakeReadiness(
                leader_eligible=True,
                issue_codes=(),
                current_as_of_date="2026-05-08",
            ),
        ),
    )

    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-08",
        refresher=refresher,
        watcher=watcher,
        pipeline_runner=pipeline_runner,
        **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.write_authorized is True
    assert exec_state.refresh_result is not None
    assert exec_state.refresh_result.attempted is True
    assert exec_state.refresh_result.succeeded is True
    assert exec_state.post_refresh_watcher_action == (
        "ready_for_pipeline_write"
    )
    assert exec_state.post_refresh_watcher_result is not None
    assert (
        exec_state.post_refresh_watcher_result
        .ready_for_pipeline is True
    )
    assert exec_state.pipeline_result is not None
    assert exec_state.pipeline_result.attempted is True
    assert exec_state.pipeline_result.leader_eligible is True
    assert exec_state.final_recommended_action == (
        dbw.FINAL_REFRESH_THEN_PIPELINE_EXECUTED
    )
    assert exec_state.final_readiness is not None
    assert exec_state.final_readiness.leader_eligible is True
    # Call ordering: refresher first, then watcher, then
    # pipeline_runner.
    call_names = [c[0] for c in recorder.calls]
    assert call_names == [
        "refresher", "watcher", "pipeline_runner",
    ]
    # The refresher and the pipeline both got write=True.
    refresher_kwargs = recorder.calls[0][1]
    pipeline_kwargs = recorder.calls[2][1]
    assert refresher_kwargs.get("write") is True
    assert pipeline_kwargs.get("write") is True
    # Report-level aggregates.
    assert report.refreshed_tickers == ("SPY",)
    assert report.pipeline_ran_tickers == ("SPY",)
    assert report.skipped_pipeline_after_refresh_tickers == ()


def test_refresh_then_pipeline_withholds_pipeline_when_watcher_blocks(
    tmp_path: Path,
):
    """**Central safety contract.** Refresh executes
    (would-be-real), but the post-refresh watcher returns
    pipeline_output_lags_persist_skip. The pipeline must
    NOT run -- the executor must stop after refresh and
    surface ``refresh_executed_pipeline_withheld``."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )

    recorder = _CallRecorder()
    refresher = _refresher_factory(
        recorder,
        _FakeRefreshResult(
            refreshed=True,
            old="2024-01-31",
            new="2026-05-08",
            stale_before=True,
            current_after=True,
            issue_codes=(),
        ),
    )
    # The refresh advanced the cache to 2026-05-08, but
    # current_as_of_date is also 2026-05-08 -> watcher
    # returns persist-skip-lag.
    watcher = _watcher_factory(
        recorder,
        _FakeWatcherState(
            action="pipeline_output_lags_persist_skip",
            cache_date_range_end="2026-05-08",
            current_as_of_date="2026-05-08",
        ),
    )

    def _no_call_pipeline(*a, **k):
        recorder.record("pipeline_runner", **k)
        raise AssertionError(
            "pipeline must NOT run when watcher blocks"
        )

    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-08",
        refresher=refresher,
        watcher=watcher,
        pipeline_runner=_no_call_pipeline,
        **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.refresh_result is not None
    assert exec_state.refresh_result.attempted is True
    assert exec_state.post_refresh_watcher_action == (
        "pipeline_output_lags_persist_skip"
    )
    assert exec_state.pipeline_result is None
    assert exec_state.final_recommended_action == (
        dbw.FINAL_REFRESH_EXECUTED_PIPELINE_WITHHELD
    )
    assert exec_state.skipped_reason == (
        dbw.SKIP_WATCHER_BLOCKED_AFTER_REFRESH
    )
    assert report.pipeline_ran_tickers == ()
    assert "SPY" in report.skipped_pipeline_after_refresh_tickers
    assert "SPY" in report.blocked_tickers
    # Only refresher + watcher fired; no pipeline call.
    call_names = [c[0] for c in recorder.calls]
    assert call_names == ["refresher", "watcher"]


def test_refresh_then_pipeline_withholds_when_watcher_says_refresh_again(
    tmp_path: Path,
):
    """Belt-and-suspenders: any non-ready watcher verdict
    blocks the pipeline, not just persist-skip-lag.
    Simulate a refresh that did not actually advance the
    cache past the cutoff (watcher returns
    refresh_source_cache again)."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    recorder = _CallRecorder()
    refresher = _refresher_factory(
        recorder,
        _FakeRefreshResult(
            refreshed=False,
            old="2024-01-31",
            new="2024-01-31",
            stale_before=True,
            current_after=False,
            issue_codes=("data_fetch_failed",),
        ),
    )
    watcher = _watcher_factory(
        recorder,
        _FakeWatcherState(
            action="refresh_source_cache",
            cache_date_range_end="2024-01-31",
            current_as_of_date="2026-05-08",
        ),
    )

    def _no_call_pipeline(*a, **k):
        raise AssertionError(
            "pipeline must NOT run when watcher still says "
            "refresh_source_cache"
        )

    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-08",
        refresher=refresher,
        watcher=watcher,
        pipeline_runner=_no_call_pipeline,
        **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.pipeline_result is None
    assert exec_state.final_recommended_action == (
        dbw.FINAL_REFRESH_EXECUTED_PIPELINE_WITHHELD
    )


# ---------------------------------------------------------------------------
# 5. run_pipeline_only path
# ---------------------------------------------------------------------------


def test_run_pipeline_only_executes_pipeline_once(
    tmp_path: Path,
):
    """Initial plan = run_pipeline_only (cache strictly
    ahead of cutoff). Executor calls the pipeline once,
    captures readiness, no refresher and no watcher
    recheck."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    recorder = _CallRecorder()
    pipeline_runner = _pipeline_runner_factory(
        recorder,
        _FakePipelineRunResult(
            leader_eligible=True,
            ranking_blocked_reason="",
            issue_codes=(),
            readiness=_FakeReadiness(
                leader_eligible=True,
                issue_codes=(),
                current_as_of_date="2026-05-07",
            ),
        ),
    )

    def _no_call_refresher(*a, **k):
        raise AssertionError(
            "refresher must not be called on run_pipeline_only"
        )

    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-07",
        refresher=_no_call_refresher,
        pipeline_runner=pipeline_runner,
        **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_RUN_PIPELINE_ONLY
    )
    assert exec_state.refresh_result is None
    assert exec_state.post_refresh_watcher_action is None
    assert exec_state.pipeline_result is not None
    assert exec_state.pipeline_result.attempted is True
    assert exec_state.pipeline_result.leader_eligible is True
    assert exec_state.final_recommended_action == (
        dbw.FINAL_PIPELINE_EXECUTED
    )
    assert exec_state.final_readiness is not None
    assert report.pipeline_ran_tickers == ("SPY",)
    # Only the pipeline runner was called.
    assert [c[0] for c in recorder.calls] == ["pipeline_runner"]


# ---------------------------------------------------------------------------
# 6. Manual / blocked / waiting actions never execute
# ---------------------------------------------------------------------------


def test_manual_stackbuilder_action_executes_no_writes(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # No StackBuilder run -> manual StackBuilder action.

    def _no_call(*a, **k):
        raise AssertionError(
            "no writer should be called for a manual "
            "StackBuilder action"
        )

    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-08",
        refresher=_no_call,
        pipeline_runner=_no_call,
        **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL
    )
    assert exec_state.skipped_reason == dbw.SKIP_MANUAL
    assert exec_state.refresh_result is None
    assert exec_state.pipeline_result is None
    assert "SPY" in report.blocked_tickers


def test_ambiguous_stackbuilder_action_executes_no_writes(
    tmp_path: Path,
):
    """Multi-stack tied-mtime ambiguity must keep the
    executor's hands off the writers."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    a = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__TIE_A-D_RUN-D",
    )
    b = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__TIE_B-D_RUN-D",
    )
    tied = time.time()
    os.utime(a, (tied, tied))
    os.utime(b, (tied, tied))
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )

    def _no_call(*a, **k):
        raise AssertionError(
            "no writer may run when StackBuilder selection "
            "is ambiguous"
        )

    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-08",
        refresher=_no_call,
        pipeline_runner=_no_call,
        **dirs,
    )
    exec_state = report.executions[0]
    # The planner returns "select_or_create..." here too,
    # but with the ambiguity-specific blocking reason.
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL
    )
    assert exec_state.skipped_reason == dbw.SKIP_MANUAL
    assert exec_state.refresh_result is None
    assert exec_state.pipeline_result is None


def test_manual_mtf_action_executes_no_writes(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    # No MTF libs -> refresh_multitimeframe_libraries_manual.
    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-07",
        refresher=lambda *a, **k: pytest.fail("refresher called"),
        pipeline_runner=lambda *a, **k: pytest.fail(
            "pipeline called",
        ),
        **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL
    )
    assert exec_state.skipped_reason == dbw.SKIP_MANUAL


def test_wait_action_executes_no_writes(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    report = dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-08",
        refresher=lambda *a, **k: pytest.fail("refresher called"),
        pipeline_runner=lambda *a, **k: pytest.fail(
            "pipeline called",
        ),
        **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF
    )
    assert exec_state.skipped_reason == dbw.SKIP_WAITING
    assert exec_state.refresh_result is None
    assert exec_state.pipeline_result is None


# ---------------------------------------------------------------------------
# 7. Execution log
# ---------------------------------------------------------------------------


def test_execution_log_is_appended_jsonl(tmp_path: Path):
    """The execution log must be JSONL with one record per
    ticker per call, and a second invocation must APPEND
    (not overwrite) the file."""
    dirs = _layout(tmp_path)
    log = tmp_path / "exec_log.jsonl"
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=False,
        current_as_of_date="2026-05-08",
        execution_log_path=log,
        **dirs,
    )
    dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=False,
        current_as_of_date="2026-05-08",
        execution_log_path=log,
        **dirs,
    )
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        record = json.loads(line)
        assert record["ticker"] == "SPY"
        assert "logged_at" in record
        assert "initial_recommended_action" in record


def test_execution_log_records_stage_sequence(tmp_path: Path):
    """A write-authorized refresh_source_cache_then_pipeline
    execution must log both the refresher and pipeline-runner
    function names plus their CLI advisory strings in the
    order they fired."""
    dirs = _layout(tmp_path)
    log = tmp_path / "exec_log.jsonl"
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    refresher = _refresher_factory(
        _CallRecorder(),
        _FakeRefreshResult(
            refreshed=True,
            old="2024-01-31",
            new="2026-05-12",
            issue_codes=(),
        ),
    )
    watcher = _watcher_factory(
        _CallRecorder(),
        _FakeWatcherState(
            action="ready_for_pipeline_write",
            cache_date_range_end="2026-05-12",
            current_as_of_date="2026-05-08",
        ),
    )
    pipeline_runner = _pipeline_runner_factory(
        _CallRecorder(),
        _FakePipelineRunResult(
            leader_eligible=True,
            issue_codes=(),
            readiness=_FakeReadiness(
                leader_eligible=True,
                current_as_of_date="2026-05-08",
            ),
        ),
    )
    dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=True,
        current_as_of_date="2026-05-08",
        refresher=refresher,
        watcher=watcher,
        pipeline_runner=pipeline_runner,
        execution_log_path=log,
        **dirs,
    )
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["ticker"] == "SPY"
    assert record["functions_executed"] == [
        "signal_engine_cache_refresher.refresh_signal_engine_cache",
        "cache_cutoff_watcher.evaluate_cache_cutoff_state",
        "confluence_pipeline_runner.run_confluence_pipeline_for_ticker",
    ]
    assert record["commands_executed"] == [
        "python signal_engine_cache_refresher.py --ticker SPY --write",
        "python confluence_pipeline_runner.py --ticker SPY --write",
    ]


def test_execution_log_absent_when_no_path_provided(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    before = _snapshot_tree(tmp_path)
    dbw.execute_daily_board_automation(
        ["SPY"],
        write_authorized=False,
        current_as_of_date="2026-05-08",
        execution_log_path=None,
        **dirs,
    )
    after = _snapshot_tree(tmp_path)
    # The only files that exist are the fixtures we wrote
    # beforehand; the executor did not add anything.
    assert after == before


# ---------------------------------------------------------------------------
# 8. CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_returns_0(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    argv = [
        "--ticker", "SPY",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = dbw.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["write_authorized"] is False


def test_cli_write_without_env_returns_2(monkeypatch, capsys):
    """The two-key gate fails when --write is requested
    without the env var. rc=2, stderr error, no SystemExit
    leak."""
    monkeypatch.delenv(dbw.ENV_VAR_NAME, raising=False)
    rc = None
    try:
        rc = dbw.main(["--ticker", "SPY", "--write"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on --write without env; "
            f"got SystemExit({exc.code})"
        )
    assert rc == 2
    err = capsys.readouterr().err
    assert "write_authorization_failed" in err
    assert dbw.ENV_VAR_NAME in err


def test_cli_write_with_wrong_env_value_returns_2(
    monkeypatch, capsys,
):
    monkeypatch.setenv(dbw.ENV_VAR_NAME, "wrong_value")
    rc = None
    try:
        rc = dbw.main(["--ticker", "SPY", "--write"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on --write with wrong "
            f"env value; got SystemExit({exc.code})"
        )
    assert rc == 2


def test_cli_unknown_flag_returns_2_without_system_exit(capsys):
    rc = None
    try:
        rc = dbw.main(["--definitely-not-a-flag"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on unknown flag; "
            f"got SystemExit({exc.code})"
        )
    assert rc == 2


def test_cli_mutually_exclusive_ticker_args_return_2(capsys):
    rc = None
    try:
        rc = dbw.main([
            "--ticker", "SPY", "--tickers", "AAPL,GOOG",
        ])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on conflicting args; "
            f"got SystemExit({exc.code})"
        )
    assert rc == 2


def test_cli_dry_run_flag_accepted(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    argv = [
        "--ticker", "SPY", "--dry-run",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = dbw.main(argv)
    assert rc == 0


def test_cli_empty_invocation_returns_0(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    argv = [
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = dbw.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 0
    assert payload["dry_run"] is True


# ---------------------------------------------------------------------------
# 9. Constants
# ---------------------------------------------------------------------------


def test_env_var_constants_are_exactly_specified():
    assert dbw.ENV_VAR_NAME == "PRJCT9_AUTOMATION_WRITE_AUTH"
    assert dbw.ENV_VAR_REQUIRED_VALUE == "phase_6h5_explicit"


def test_final_action_constants_are_strings():
    assert dbw.FINAL_PIPELINE_EXECUTED == "pipeline_executed"
    assert dbw.FINAL_REFRESH_THEN_PIPELINE_EXECUTED == (
        "refresh_then_pipeline_executed"
    )
    assert dbw.FINAL_REFRESH_EXECUTED_PIPELINE_WITHHELD == (
        "refresh_executed_pipeline_withheld"
    )
    assert dbw.FINAL_WRITE_NOT_AUTHORIZED == (
        "write_not_authorized_dry_run"
    )
