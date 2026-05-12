"""Phase 6H-3 tests for daily_board_automation_preflight.

Pins the automation-preflight contract:

  - leader-eligible ticker -> no_action_already_current
  - cache equal to cutoff -> wait_for_cache_ahead_of_cutoff
  - cache behind cutoff + prerequisites ->
    refresh_source_cache_then_pipeline with advisory commands
  - cache ahead of cutoff + prerequisites ->
    run_pipeline_only with advisory pipeline command
  - missing cache PKL -> blocked_manual_review + cache_missing
  - no StackBuilder runs -> select_or_create_stackbuilder_stack_manual
  - one StackBuilder run -> single_available_stack policy
  - multiple StackBuilder runs with distinct mtimes ->
    latest_mtime_existing_pipeline_default policy with warning
  - multiple StackBuilder runs with tied newest mtime ->
    ambiguous_tied_mtime, blocked
  - missing MTF libraries -> refresh_multitimeframe_libraries_manual
  - health report blocked -> blocked_manual_review
  - report counts add up; ``ready_for_pipeline_tickers``
    + ``blocked_tickers`` partition correctly
  - CLI single + CSV emit JSON; mutually-exclusive args
    return rc=2 without SystemExit leak
  - module has no forbidden imports (yfinance, dash,
    daily_signal_board, spymaster, onepass, pipeline writer,
    StackBuilder execution helper)
  - module performs zero writes against cache_dir,
    artifact_root, stackbuilder_root, or signal_library_dir
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
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers (shape-aligned with the other Phase 6 test suites
# so a fixture drift in one place reads obviously against another)
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
    """Write a Spymaster-shaped cache PKL with just the date
    metadata fields the watcher inspects."""
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
    """Write a minimal combo_leaderboard.xlsx covering K=1..12,
    matching the existing fixtures used by Phase 6E-1 / 6E-2
    test suites."""
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
    """Daily-K + MTF-K + Confluence so leader_eligible can hold
    against the artifact-side date pin. Uses the same shapes
    the launch-audit tests use."""
    safe = target.replace("^", "_")
    tf_dir = artifact_root / "trafficflow" / safe
    tf_dir.mkdir(parents=True, exist_ok=True)
    for k in range(1, 13):
        # Phase 6D-1 daily K artifact (required by the audit's
        # daily-K probe).
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
        # Phase 6D-2 MTF artifact.
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


def test_preflight_module_has_no_forbidden_imports():
    tree = ast.parse(
        Path(dap.__file__).read_text(encoding="utf-8"),
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
        # The pipeline runner and the StackBuilder/OnePass
        # writers are explicitly forbidden -- this module
        # plans only.
        "confluence_pipeline_runner",
        "signal_engine_cache_refresher",
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
        "forbidden import in daily_board_automation_preflight: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# 2. Decision-tree classification
# ---------------------------------------------------------------------------


def test_leader_eligible_returns_no_action_already_current(
    tmp_path: Path,
):
    """When the readiness layer says SPY is already leader-
    eligible (e.g. inside the 3-hour post-close UTC window),
    automation has nothing to do."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # Confluence is at the cutoff -> leader-eligible.
    _write_full_pipeline_artifacts(
        dirs["artifact_root"], "SPY", last_date="2026-05-08",
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.current_leader_eligible is True
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_NO_ACTION_ALREADY_CURRENT
    )
    assert state.blocking_reasons == ()
    assert state.would_run_commands == ()


def test_cache_equal_cutoff_recommends_wait(tmp_path: Path):
    """The SPY-shape live scenario: cache reaches the cutoff
    exactly, all upstream present but no Confluence yet.
    Automation must wait."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.cache_cutoff_action == (
        "pipeline_output_lags_persist_skip"
    )
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF
    )
    assert dap.BLOCKING_CACHE_EQUAL_CUTOFF_PERSIST_SKIP in (
        state.blocking_reasons
    )
    assert state.would_run_commands == ()


def test_cache_behind_cutoff_recommends_refresh_then_pipeline(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE
    )
    assert dap.BLOCKING_CACHE_BEHIND_CUTOFF in state.blocking_reasons
    # Two advisory commands, refresher then runner.
    assert len(state.would_run_commands) == 2
    assert "signal_engine_cache_refresher.py" in (
        state.would_run_commands[0]
    )
    assert "--write" in state.would_run_commands[0]
    assert "confluence_pipeline_runner.py" in (
        state.would_run_commands[1]
    )
    assert "--write" in state.would_run_commands[1]


def test_cache_ahead_cutoff_recommends_run_pipeline_only(
    tmp_path: Path,
):
    """Cache strictly ahead of cutoff -- the 3-hour
    post-market-close UTC window. Automation can run the
    pipeline without a refresh first."""
    dirs = _layout(tmp_path)
    # Friday 2026-05-08 cache, Thursday 2026-05-07 cutoff:
    # cache > cutoff, persist trim of cache (Friday) ->
    # Thursday = cutoff -> readiness will be current after
    # write, so automation has a real pipeline window.
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-07", **dirs,
    )
    assert state.cache_cutoff_action == (
        "ready_for_pipeline_write"
    )
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_RUN_PIPELINE_ONLY
    )
    assert state.blocking_reasons == ()
    assert len(state.would_run_commands) == 1
    assert "confluence_pipeline_runner.py" in (
        state.would_run_commands[0]
    )
    assert "--write" in state.would_run_commands[0]


def test_missing_cache_blocks_with_manual_review(tmp_path: Path):
    dirs = _layout(tmp_path)
    state = dap.inspect_ticker_automation_readiness(
        "GHOST", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW
    )
    assert dap.BLOCKING_CACHE_MISSING in state.blocking_reasons
    assert state.would_run_commands == ()


def test_health_blocked_routes_to_manual_review(tmp_path: Path):
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
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW
    )
    assert dap.BLOCKING_HEALTH_REPORT_BLOCKED in (
        state.blocking_reasons
    )


def test_missing_multitimeframe_libs_routes_to_manual_refresh(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    # No MTF libraries on disk.
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-07", **dirs,
    )
    assert state.multitimeframe_libraries_present is False
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL
    )
    assert dap.BLOCKING_MULTITIMEFRAME_LIBRARIES_MISSING in (
        state.blocking_reasons
    )


# ---------------------------------------------------------------------------
# 3. StackBuilder inventory
# ---------------------------------------------------------------------------


def test_no_stackbuilder_runs_blocks_with_select_or_create(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.stackbuilder_present is False
    assert state.stackbuilder_run_count == 0
    assert state.stackbuilder_run_ids == ()
    assert state.selected_stackbuilder_run_id is None
    assert state.stackbuilder_selection_policy == (
        dap.SB_POLICY_NO_STACK_AVAILABLE
    )
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL
    )
    assert dap.BLOCKING_STACKBUILDER_MISSING in (
        state.blocking_reasons
    )


def test_single_stackbuilder_run_selects_it(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    run_dir = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__ONLY-D_RUN-D",
    )
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.stackbuilder_present is True
    assert state.stackbuilder_run_count == 1
    assert state.stackbuilder_run_ids == (run_dir.name,)
    assert state.selected_stackbuilder_run_id == run_dir.name
    assert state.stackbuilder_selection_policy == (
        dap.SB_POLICY_SINGLE_AVAILABLE_STACK
    )
    assert state.stackbuilder_selection_warning is None
    # The single-stack case is not a blocker; the automation
    # action follows from the cache verdict.
    assert dap.BLOCKING_STACKBUILDER_MISSING not in (
        state.blocking_reasons
    )
    assert dap.BLOCKING_STACKBUILDER_SELECTION_AMBIGUOUS not in (
        state.blocking_reasons
    )


def test_multiple_stackbuilder_runs_picks_newest_mtime(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    older = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__OLDER-D_RUN-D",
    )
    # Force ``older`` to look older than ``newer``.
    base = time.time() - 86400
    os.utime(older, (base, base))
    newer = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__NEWER-D_RUN-D",
    )
    # Newer's mtime should naturally be greater; force it
    # higher for deterministic ordering on filesystems with
    # second-resolution mtimes.
    os.utime(newer, (base + 3600, base + 3600))
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.stackbuilder_present is True
    assert state.stackbuilder_run_count == 2
    # Newest first.
    assert state.stackbuilder_run_ids[0] == newer.name
    assert newer.name in state.stackbuilder_run_ids
    assert older.name in state.stackbuilder_run_ids
    assert state.selected_stackbuilder_run_id == newer.name
    assert state.stackbuilder_selection_policy == (
        dap.SB_POLICY_LATEST_MTIME_EXISTING_PIPELINE_DEFAULT
    )
    assert state.stackbuilder_selection_warning is not None
    assert "explicit" in state.stackbuilder_selection_warning
    # Not blocked solely because multiple stacks exist.
    assert dap.BLOCKING_STACKBUILDER_SELECTION_AMBIGUOUS not in (
        state.blocking_reasons
    )


def test_ambiguous_tied_mtime_blocks_automation(tmp_path: Path):
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
    # Force identical mtimes so the pipeline default cannot
    # deterministically pick a winner.
    tied = time.time()
    os.utime(a, (tied, tied))
    os.utime(b, (tied, tied))
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.stackbuilder_present is True
    assert state.stackbuilder_run_count == 2
    assert state.selected_stackbuilder_run_id is None
    assert state.stackbuilder_selection_policy == (
        dap.SB_POLICY_AMBIGUOUS_TIED_MTIME
    )
    assert state.stackbuilder_selection_warning is not None
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL
    )
    assert dap.BLOCKING_STACKBUILDER_SELECTION_AMBIGUOUS in (
        state.blocking_reasons
    )


# ---------------------------------------------------------------------------
# 4. Aggregate plan + counts
# ---------------------------------------------------------------------------


def test_plan_counts_and_ready_partition(tmp_path: Path):
    dirs = _layout(tmp_path)
    # READY: cache 2026-05-08, cutoff 2026-05-07 -> ahead.
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # WAIT: cache 2026-05-07 == cutoff 2026-05-07.
    _write_cache_pkl(
        dirs["cache_dir"], "AAA", last_date="2026-05-07",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "AAA")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "AAA", ["1wk", "1mo"],
    )
    # BLOCKED: missing cache.
    plan = dap.build_daily_board_automation_plan(
        ["SPY", "AAA", "GHOST"],
        current_as_of_date="2026-05-07",
        **dirs,
    )
    assert plan.inspected_count == 3
    counts = plan.counts_by_recommended_automation_action
    assert sum(counts.values()) == 3
    assert counts.get(
        dap.RECOMMENDED_RUN_PIPELINE_ONLY,
    ) == 1
    assert counts.get(
        dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF,
    ) == 1
    assert counts.get(
        dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW,
    ) == 1
    assert plan.ready_for_pipeline_tickers == ("SPY",)
    # The wait ticker AAA carries
    # cache_equal_cutoff_persist_skip in blocking_reasons, so
    # it appears in blocked_tickers too. GHOST is blocked
    # via cache_missing. SPY has empty blocking_reasons.
    blocked = set(plan.blocked_tickers)
    assert "AAA" in blocked
    assert "GHOST" in blocked
    assert "SPY" not in blocked


def test_to_json_dict_round_trips(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    plan = dap.build_daily_board_automation_plan(
        ["SPY"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    d = plan.to_json_dict()
    s = json.dumps(d)  # must not raise
    assert "SPY" in s
    assert "states" in d
    assert isinstance(d["states"][0]["ticker"], str)


# ---------------------------------------------------------------------------
# 5. Read-only / no writes
# ---------------------------------------------------------------------------


def test_preflight_does_not_write_to_any_root(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    before = {
        name: _snapshot_tree(p) for name, p in dirs.items()
    }
    dap.build_daily_board_automation_plan(
        ["SPY"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    after = {
        name: _snapshot_tree(p) for name, p in dirs.items()
    }
    for name in dirs:
        assert after[name] == before[name], (
            f"preflight wrote to {name}: "
            f"{after[name] - before[name]}"
        )


# ---------------------------------------------------------------------------
# 6. Advisory-command contract
# ---------------------------------------------------------------------------


def test_manual_actions_have_empty_would_run_commands(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2026-05-08",
    )
    # Health-block forces blocked_manual_review.
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    _write_health_report(
        dirs["artifact_root"],
        blocked_targets={"SPY": ["confluence"]},
    )
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.recommended_automation_action == (
        dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW
    )
    assert state.would_run_commands == ()


def test_would_run_commands_are_advisory_strings_only(
    tmp_path: Path,
):
    """The ``would_run_commands`` field must be a tuple of
    strings the operator can copy-paste -- not a callable,
    not a subprocess invocation. The watcher must NOT have
    executed any of them."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY", last_date="2024-01-31",
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    before = _snapshot_tree(dirs["cache_dir"])
    state = dap.inspect_ticker_automation_readiness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    after = _snapshot_tree(dirs["cache_dir"])
    # No writes despite the recommendation referencing
    # --write commands.
    assert before == after
    for cmd in state.would_run_commands:
        assert isinstance(cmd, str)
        # Sanity: each advisory command starts with python ...
        assert cmd.startswith("python ")


# ---------------------------------------------------------------------------
# 7. CLI
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
    rc = dap.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 1
    assert payload["states"][0]["ticker"] == "SPY"


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
    rc = dap.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    tickers = [s["ticker"] for s in payload["states"]]
    assert tickers == ["SPY", "AAPL"]


def test_cli_unknown_flag_returns_2_without_system_exit(capsys):
    rc = None
    try:
        rc = dap.main(["--definitely-not-a-flag"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on unknown flag; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_mutually_exclusive_ticker_args_return_2(capsys):
    rc = None
    try:
        rc = dap.main([
            "--ticker", "SPY", "--tickers", "AAPL,GOOG",
        ])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on conflicting args; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
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
    rc = dap.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 0
    assert payload["states"] == []
    assert payload["ready_for_pipeline_tickers"] == []


# ---------------------------------------------------------------------------
# 8. Constant namespaces register
# ---------------------------------------------------------------------------


def test_recommended_actions_tuple_covers_every_constant():
    expected = {
        dap.RECOMMENDED_NO_ACTION_ALREADY_CURRENT,
        dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF,
        dap.RECOMMENDED_REFRESH_SOURCE_CACHE_THEN_PIPELINE,
        dap.RECOMMENDED_RUN_PIPELINE_ONLY,
        dap.RECOMMENDED_SELECT_OR_CREATE_STACKBUILDER_STACK_MANUAL,
        dap.RECOMMENDED_REFRESH_MULTITIMEFRAME_LIBRARIES_MANUAL,
        dap.RECOMMENDED_BLOCKED_MANUAL_REVIEW,
    }
    assert set(dap.RECOMMENDED_AUTOMATION_ACTIONS) == expected


def test_blocking_reasons_tuple_covers_every_constant():
    expected = {
        dap.BLOCKING_CACHE_MISSING,
        dap.BLOCKING_CACHE_BEHIND_CUTOFF,
        dap.BLOCKING_CACHE_EQUAL_CUTOFF_PERSIST_SKIP,
        dap.BLOCKING_STACKBUILDER_MISSING,
        dap.BLOCKING_STACKBUILDER_SELECTION_AMBIGUOUS,
        dap.BLOCKING_MULTITIMEFRAME_LIBRARIES_MISSING,
        dap.BLOCKING_HEALTH_REPORT_BLOCKED,
        dap.BLOCKING_MANUAL_REVIEW_REQUIRED,
    }
    assert set(dap.BLOCKING_REASONS) == expected


def test_stackbuilder_policies_tuple_covers_every_constant():
    expected = {
        dap.SB_POLICY_NO_STACK_AVAILABLE,
        dap.SB_POLICY_SINGLE_AVAILABLE_STACK,
        dap.SB_POLICY_LATEST_MTIME_EXISTING_PIPELINE_DEFAULT,
        dap.SB_POLICY_AMBIGUOUS_TIED_MTIME,
    }
    assert set(dap.STACKBUILDER_SELECTION_POLICIES) == expected
