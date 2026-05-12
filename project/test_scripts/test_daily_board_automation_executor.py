"""Phase 6H-4 tests for daily_board_automation_executor.

Pins the dry-run executor contract:

  - no_action_already_current -> zero steps, would_write=false
  - wait_for_cache_ahead_of_cutoff -> zero steps, blocked,
    skipped_reason=waiting_for_cache_ahead_of_cutoff
  - run_pipeline_only -> exactly one pipeline command step,
    would_write=true, safe_to_execute_pipeline_after_recheck
    =true; no command was executed
  - refresh_source_cache_then_pipeline -> exactly two steps:
    refresher command + recheck step. **No pipeline command
    string appears in the dry-run output.**
    safe_to_execute_pipeline_after_recheck=false because the
    refresh was not actually executed.
  - manual StackBuilder action -> zero steps, blocked,
    skipped_reason=manual
  - manual MTF action -> zero steps, blocked,
    skipped_reason=manual
  - blocked_manual_review -> zero steps, blocked,
    skipped_reason=blocked
  - counts_by_final_recommended_action sums to inspected_count
  - would_write_tickers carries only tickers with a
    would_write=true step
  - blocked_tickers includes blocked / manual / waiting /
    awaiting-recheck tickers
  - CLI single + CSV emit JSON
  - CLI --write returns rc=2 (production writes are not
    authorized in this phase) without SystemExit leak
  - CLI mutually-exclusive args return rc=2 without
    SystemExit leak
  - module has no forbidden imports (yfinance, dash,
    daily_signal_board, signal_engine_cache_refresher
    module, confluence_pipeline_runner module, subprocess,
    plus the live engines and refresher Python imports)
  - module performs zero writes against any of the four
    operator-supplied roots
  - explicit test: refresh_source_cache_then_pipeline does
    NOT emit a pipeline write command in the dry-run output
"""
from __future__ import annotations

import ast
import json
import pickle
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import daily_board_automation_executor as dbe  # noqa: E402
import daily_board_automation_preflight as dap  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers (shape-aligned with the Phase 6H-3 fixture set)
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
    safe = str(ticker).strip().upper().replace("^", "_")
    return f"{safe}_precomputed_results.pkl"


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
    members_str: str = "['AAA[D]', 'BBB[D]']",
) -> Path:
    import pandas as pd

    safe = target.replace("^", "_")
    run_dir = stack_root / safe / seed
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = [{
        "K": k,
        "Trigger Days": 100 + k,
        "Total Capture (%)": 10.0 + k,
        "Sharpe Ratio": 0.1,
        "p-Value": 0.05,
        "Members": members_str,
    } for k in range(1, 13)]
    pd.DataFrame(rows, columns=[
        "K", "Trigger Days", "Total Capture (%)",
        "Sharpe Ratio", "p-Value", "Members",
    ]).to_excel(run_dir / "combo_leaderboard.xlsx", index=False)
    return run_dir


def _write_multitimeframe_libs(
    sig_dir: Path, ticker: str, intervals: list[str],
) -> None:
    safe = ticker.replace("^", "_")
    for interval in intervals:
        (sig_dir / f"{safe}_stable_v1_0_0_{interval}.pkl"
         ).write_bytes(b"placeholder")


def _write_health_report(
    artifact_root: Path,
    *,
    blocked_targets: dict[str, list[str]],
) -> Path:
    payload = {
        "schema": "catalogue_health_v1",
        "generated_at": "2026-05-11T00:00:00+00:00",
        "by_target": [
            {"target_ticker": t, "engines_blocked": engines}
            for t, engines in blocked_targets.items()
        ],
    }
    p = artifact_root / "catalogue_health_report.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _write_full_pipeline_artifacts(
    artifact_root: Path,
    target: str,
    *,
    last_date: str,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
) -> None:
    safe = target.replace("^", "_")
    tf_dir = artifact_root / "trafficflow" / safe
    tf_dir.mkdir(parents=True, exist_ok=True)
    for k in range(1, 13):
        daily_art = ra.ResearchDayArtifact(
            artifact_version=ra.ARTIFACT_VERSION,
            engine="trafficflow",
            target_ticker=target,
            signal_source="",
            run_id=f"{seed_run_id}__K{k}",
            metric_basis="Close",
            persist_skip_bars=1,
            generated_at="2026-05-08T00:00:00+00:00",
            summary={
                "total_capture_pct": 5.0,
                "sharpe_ratio": 0.05,
                "trigger_days": 3,
            },
            daily=[{
                "date": last_date,
                "target_close": 100.0,
                "target_return_pct": 0.0,
                "pressure_signal": "Buy",
                "buy_count": 1,
                "short_count": 0,
                "none_count": 0,
                "missing_count": 0,
                "active_count": 1,
                "available_count": 1,
                "daily_capture_pct": 0.0,
                "cumulative_capture_pct": 0.0,
                "is_trigger_day": True,
            }],
            K=k,
            members=["AAA", "BBB"],
            protocol_per_member={"AAA": "D", "BBB": "D"},
            timeframes=["1d"],
        )
        ra.write_research_day_artifact(
            daily_art,
            tf_dir / f"{seed_run_id}__K{k}.research_day.json",
        )
        mtf_art = ra.ResearchDayArtifact(
            artifact_version=ra.ARTIFACT_VERSION,
            engine="trafficflow",
            target_ticker=target,
            signal_source="",
            run_id=f"{seed_run_id}__K{k}__MTF",
            metric_basis="Close",
            persist_skip_bars=1,
            generated_at="2026-05-08T00:00:00+00:00",
            summary={
                "total_capture_pct": 5.0,
                "sharpe_ratio": 0.05,
                "trigger_days": 3,
            },
            daily=[{
                "date": last_date,
                "target_close": 100.0,
                "target_return_pct": 0.0,
                "pressure_signal": "Buy",
                "timeframe_pressure_signals": {
                    tf: "Buy" for tf in (
                        "1d", "1wk", "1mo", "3mo", "1y",
                    )
                },
                "buy_count": 5,
                "short_count": 0,
                "none_count": 0,
                "missing_count": 0,
                "active_count": 5,
                "available_count": 5,
                "daily_capture_pct": 0.0,
                "cumulative_capture_pct": 1.0,
                "is_trigger_day": True,
            }],
            K=k,
            members=["AAA", "BBB"],
            protocol_per_member={"AAA": "D", "BBB": "D"},
            timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
        )
        ra.write_research_day_artifact(
            mtf_art,
            tf_dir / f"{seed_run_id}__K{k}__MTF.research_day.json",
        )
    conf_dir = artifact_root / "confluence" / safe
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf_art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="confluence",
        target_ticker=target,
        signal_source="",
        run_id="mtf_consensus",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 50.0,
            "sharpe_ratio": 0.1,
            "trigger_days": 5,
        },
        daily=[{
            "date": last_date,
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "confluence_signal": "Buy",
            "confluence_tier": "buy",
            "timeframe_signals": {},
            "alignment_pct": 1.0,
            "buy_count": 5,
            "short_count": 0,
            "none_count": 0,
            "active_count": 5,
            "available_count": 5,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 1.0,
            "is_trigger_day": True,
        }],
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    ra.write_research_day_artifact(
        conf_art,
        conf_dir / f"{safe}__mtf_consensus.research_day.json",
    )


def _snapshot_tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_executor_module_has_no_forbidden_imports():
    tree = ast.parse(
        Path(dbe.__file__).read_text(encoding="utf-8"),
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
        # Production writers — explicitly forbidden:
        # the executor must refer to their CLI strings only,
        # not import the Python modules.
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        # Forbidden std-lib: no subprocess in this phase.
        "subprocess",
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad = [m for m in found if m.split(".")[0] in forbidden]
    assert not bad, (
        "forbidden import in daily_board_automation_executor: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# 2. Per-action sequencing
# ---------------------------------------------------------------------------


def test_leader_eligible_returns_no_steps(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    _write_full_pipeline_artifacts(
        dirs["artifact_root"], "SPY", last_date="2026-05-08",
    )
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    assert report.dry_run is True
    assert report.inspected_count == 1
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_NO_ACTION_ALREADY_CURRENT
    )
    assert exec_state.final_recommended_action == (
        dap.RECOMMENDED_NO_ACTION_ALREADY_CURRENT
    )
    assert exec_state.steps == ()
    assert exec_state.would_write is False
    assert exec_state.write_authorized is False
    assert exec_state.executed_commands == ()
    assert exec_state.skipped_reason is None
    assert exec_state.safe_to_execute_pipeline_after_recheck is False
    assert report.would_write_tickers == ()


def test_wait_for_cache_ahead_returns_no_steps_blocked(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF
    )
    assert exec_state.final_recommended_action == (
        dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF
    )
    assert exec_state.steps == ()
    assert exec_state.would_write is False
    assert exec_state.skipped_reason == dbe.SKIP_WAITING
    assert "SPY" in report.blocked_tickers


def test_run_pipeline_only_emits_one_pipeline_step(
    tmp_path: Path,
):
    """Cache strictly ahead of cutoff. Executor emits exactly
    one step with the pipeline write command. would_write
    is True; safe_to_execute_pipeline_after_recheck is True
    because no refresh is needed."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-07", **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_RUN_PIPELINE_ONLY
    )
    assert exec_state.final_recommended_action == (
        dap.RECOMMENDED_RUN_PIPELINE_ONLY
    )
    assert len(exec_state.steps) == 1
    step = exec_state.steps[0]
    assert step.step_name == dbe.STEP_RUN_PIPELINE
    assert step.would_run is True
    assert step.command is not None
    assert "confluence_pipeline_runner.py" in step.command
    assert "--write" in step.command
    assert "SPY" in step.command
    assert exec_state.would_write is True
    assert exec_state.executed_commands == ()
    assert exec_state.write_authorized is False
    assert exec_state.skipped_reason == dbe.SKIP_DRY_RUN_ONLY
    assert exec_state.safe_to_execute_pipeline_after_recheck is True
    assert "SPY" in report.would_write_tickers


def test_refresh_then_pipeline_emits_refresh_plus_recheck(
    tmp_path: Path,
):
    """The CENTRAL SAFETY CONTRACT. When the plan says
    refresh_source_cache_then_pipeline the executor must
    emit:
      1. a refresher command step (would_run=True), and
      2. a recheck step (would_run=False, command=None).
    The pipeline write command must NOT appear as a runnable
    step in the dry-run output because the refresh was never
    actually executed and the watcher cannot confirm
    ready_for_pipeline_write."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE
    )
    assert exec_state.final_recommended_action == (
        dbe.RECOMMENDED_AWAITING_RECHECK_AFTER_REFRESH
    )
    assert len(exec_state.steps) == 2
    refresh_step, recheck_step = exec_state.steps

    # Step 1: refresher write command.
    assert refresh_step.step_name == dbe.STEP_REFRESH_SOURCE_CACHE
    assert refresh_step.would_run is True
    assert refresh_step.command is not None
    assert "signal_engine_cache_refresher.py" in (
        refresh_step.command
    )
    assert "--write" in refresh_step.command
    assert "SPY" in refresh_step.command
    assert refresh_step.post_action == (
        dbe.STEP_RECHECK_CACHE_CUTOFF_AFTER_REFRESH
    )

    # Step 2: recheck gate. NOT a runnable command.
    assert recheck_step.step_name == (
        dbe.STEP_RECHECK_CACHE_CUTOFF_AFTER_REFRESH
    )
    assert recheck_step.would_run is False
    assert recheck_step.command is None
    assert recheck_step.pre_action == dbe.STEP_REFRESH_SOURCE_CACHE
    assert recheck_step.post_action == dbe.STEP_RUN_PIPELINE

    assert exec_state.would_write is True
    assert exec_state.skipped_reason == dbe.SKIP_AWAITING_RECHECK
    # The refresh was NOT actually executed, so the recheck
    # cannot pass; the pipeline write must remain held.
    assert (
        exec_state.safe_to_execute_pipeline_after_recheck
        is False
    )


def test_refresh_then_pipeline_does_not_emit_pipeline_command(
    tmp_path: Path,
):
    """Belt-and-suspenders: scan every step in the
    refresh-then-pipeline case and assert no step carries a
    pipeline write command. This is the load-bearing
    safety property -- the executor must never present the
    pipeline write as runnable before the recheck passes."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    exec_state = report.executions[0]
    runnable_commands = [
        step.command for step in exec_state.steps
        if step.would_run and step.command is not None
    ]
    for cmd in runnable_commands:
        assert "confluence_pipeline_runner.py" not in cmd, (
            "Phase 6H-4 sequencing violation: the executor "
            "emitted a runnable pipeline command before the "
            "recheck gate passed -- "
            f"command={cmd!r}"
        )


def test_manual_stackbuilder_action_blocks_with_no_commands(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # No StackBuilder runs.
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL
    )
    assert exec_state.steps == ()
    assert exec_state.would_write is False
    assert exec_state.skipped_reason == dbe.SKIP_MANUAL
    assert "SPY" in report.blocked_tickers


def test_manual_mtf_action_blocks_with_no_commands(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    # No MTF libraries.
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-07", **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL
    )
    assert exec_state.steps == ()
    assert exec_state.skipped_reason == dbe.SKIP_MANUAL


def test_blocked_manual_review_blocks_with_no_commands(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    _write_health_report(
        dirs["artifact_root"],
        blocked_targets={"SPY": ["confluence"]},
    )
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    exec_state = report.executions[0]
    assert exec_state.initial_recommended_action == (
        dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW
    )
    assert exec_state.steps == ()
    assert exec_state.skipped_reason == dbe.SKIP_BLOCKED
    assert "SPY" in report.blocked_tickers


# ---------------------------------------------------------------------------
# 3. Aggregate report shape
# ---------------------------------------------------------------------------


def test_report_counts_and_partitions(tmp_path: Path):
    dirs = _layout(tmp_path)
    # RUN: cache 2026-05-08, cutoff 2026-05-07 -> ahead.
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # REFRESH-THEN-RECHECK: cache 2024-01-31.
    _write_cache_pkl(
        dirs["cache_dir"], "OLD", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "OLD")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "OLD", ["1wk", "1mo"],
    )
    # WAIT: cache 2026-05-07 == cutoff 2026-05-07.
    _write_cache_pkl(
        dirs["cache_dir"], "AAA", last_date="2026-05-07",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "AAA")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "AAA", ["1wk", "1mo"],
    )
    # MISSING CACHE -> blocked_manual_review.
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY", "OLD", "AAA", "GHOST"],
        current_as_of_date="2026-05-07",
        **dirs,
    )
    assert report.inspected_count == 4
    counts = report.counts_by_final_recommended_action
    assert sum(counts.values()) == 4
    assert counts.get(
        dap.RECOMMENDED_RUN_PIPELINE_ONLY,
    ) == 1
    assert counts.get(
        dbe.RECOMMENDED_AWAITING_RECHECK_AFTER_REFRESH,
    ) == 1
    assert counts.get(
        dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF,
    ) == 1
    assert counts.get(
        dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW,
    ) == 1

    # would_write_tickers: SPY (pipeline-only) and OLD
    # (refresher write step) -- both have at least one
    # would_run=True command in their step list.
    assert set(report.would_write_tickers) == {"SPY", "OLD"}

    # blocked_tickers: every ticker carrying a non-None
    # skipped_reason other than dry_run_only -- so OLD
    # (awaiting recheck), AAA (waiting), GHOST (blocked
    # manual review). SPY (pipeline-only, dry_run_only) is
    # NOT blocked because its skipped_reason is
    # dry_run_only.
    blocked = set(report.blocked_tickers)
    assert "OLD" in blocked
    assert "AAA" in blocked
    assert "GHOST" in blocked
    assert "SPY" not in blocked


def test_to_json_dict_round_trips(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    d = report.to_json_dict()
    s = json.dumps(d)  # must not raise
    assert "SPY" in s
    assert d["dry_run"] is True
    assert "executions" in d


# ---------------------------------------------------------------------------
# 4. No writes
# ---------------------------------------------------------------------------


def test_executor_does_not_write_to_any_root(tmp_path: Path):
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
    dbe.execute_daily_board_automation_dry_run(
        ["SPY"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    after = {
        name: _snapshot_tree(p) for name, p in dirs.items()
    }
    for name in dirs:
        assert after[name] == before[name], (
            f"executor wrote to {name}: "
            f"{after[name] - before[name]}"
        )


def test_executed_commands_is_always_empty_in_dry_run(
    tmp_path: Path,
):
    """Even when the executor emits a runnable step, the
    executed_commands tuple must remain empty in dry-run
    mode. The executor must not run subprocesses."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    report = dbe.execute_daily_board_automation_dry_run(
        ["SPY"], current_as_of_date="2026-05-07", **dirs,
    )
    for exec_state in report.executions:
        assert exec_state.executed_commands == ()
        assert exec_state.write_authorized is False
    assert report.dry_run is True


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------


def test_cli_ticker_single_emits_json(tmp_path: Path, capsys):
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
    rc = dbe.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 1
    assert payload["dry_run"] is True
    assert payload["executions"][0]["ticker"] == "SPY"


def test_cli_tickers_csv_emits_json(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_cache_pkl(
        dirs["cache_dir"], "AAPL", last_date="2026-05-08",
    )
    argv = [
        "--tickers", "SPY,AAPL",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = dbe.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    tickers = [e["ticker"] for e in payload["executions"]]
    assert tickers == ["SPY", "AAPL"]


def test_cli_dry_run_flag_is_accepted(tmp_path: Path, capsys):
    """The --dry-run flag is included for explicitness even
    though dry-run is the only supported mode. Passing it
    must not produce an error."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    argv = [
        "--ticker", "SPY",
        "--dry-run",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = dbe.main(argv)
    assert rc == 0


def test_cli_write_flag_returns_2_without_system_exit(capsys):
    """Production writes are not authorized in Phase 6H-4.
    The CLI must reject --write with rc=2 and no
    SystemExit leak."""
    rc = None
    try:
        rc = dbe.main(["--ticker", "SPY", "--write"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on --write; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2
    err = capsys.readouterr().err
    # The error message names the contract.
    assert "production_writes_not_authorized" in err


def test_cli_write_and_dry_run_combo_returns_2(capsys):
    """--write and --dry-run are mutually exclusive at the
    argparse level. Passing both must still return rc=2
    cleanly."""
    rc = None
    try:
        rc = dbe.main([
            "--ticker", "SPY", "--write", "--dry-run",
        ])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on conflicting "
            f"--write/--dry-run; got SystemExit({exc.code})"
        )
    assert rc == 2


def test_cli_unknown_flag_returns_2_without_system_exit(capsys):
    rc = None
    try:
        rc = dbe.main(["--definitely-not-a-flag"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on unknown flag; "
            f"got SystemExit({exc.code})"
        )
    assert rc == 2


def test_cli_mutually_exclusive_ticker_args_return_2(capsys):
    rc = None
    try:
        rc = dbe.main([
            "--ticker", "SPY", "--tickers", "AAPL,GOOG",
        ])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on conflicting "
            "--ticker / --tickers args; "
            f"got SystemExit({exc.code})"
        )
    assert rc == 2


def test_cli_empty_invocation_returns_0(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    argv = [
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = dbe.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 0
    assert payload["executions"] == []
    assert payload["would_write_tickers"] == []
    assert payload["blocked_tickers"] == []


# ---------------------------------------------------------------------------
# 6. Constants
# ---------------------------------------------------------------------------


def test_step_names_match_documented_constants():
    assert dbe.STEP_REFRESH_SOURCE_CACHE == "refresh_source_cache"
    assert dbe.STEP_RECHECK_CACHE_CUTOFF_AFTER_REFRESH == (
        "recheck_cache_cutoff_after_refresh"
    )
    assert dbe.STEP_RUN_PIPELINE == "run_pipeline"
    assert set(dbe.EXECUTOR_STEP_NAMES) == {
        dbe.STEP_REFRESH_SOURCE_CACHE,
        dbe.STEP_RECHECK_CACHE_CUTOFF_AFTER_REFRESH,
        dbe.STEP_RUN_PIPELINE,
    }


def test_awaiting_recheck_constant_string():
    assert dbe.RECOMMENDED_AWAITING_RECHECK_AFTER_REFRESH == (
        "awaiting_recheck_after_refresh"
    )
