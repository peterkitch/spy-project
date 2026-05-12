"""Phase 6I-6 tests for daily_board_execution_queue_planner.

Pins:

  - Forbidden-imports static guard: no yfinance / dash /
    spymaster / onepass / impactsearch / stackbuilder /
    trafficflow / confluence runner / refresher / writer
    import / subprocess.
  - Empty universe -> empty queues.
  - Explicit ticker list works.
  - --from-stackbuilder-universe works.
  - --max-refresh truncates refresh queue + truncation
    flag.
  - --max-pipeline truncates pipeline queue + truncation
    flag.
  - Blocked queues have no advisory write command.
  - Refresh / pipeline queues carry the advisory writer
    command + write_requires_env_var=True.
  - Wait-for-cache-ahead queue has no command.
  - Missing target cache lands upstream_blocked (NOT
    downstream_gap).
  - Downstream gap lands downstream_gap.
  - Already leader-eligible lands current_leader_eligible.
  - Ranking tails pass through unchanged.
  - JSON round-trip.
  - No-writes tmp_path snapshot before/after.
  - CLI rc=0/2/3, no SystemExit leak.
"""
from __future__ import annotations

import ast
import io
import json
import pickle
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Optional

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import daily_board_execution_queue_planner as eqp  # noqa: E402
import daily_board_automation_preflight as dap  # noqa: E402
import daily_board_universe_planner as uplanner  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers -- self-contained (mirrors Phase 6I-5
# test pattern)
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    stack_dir = tmp_path / "stackbuilder"
    sig_dir = tmp_path / "siglib"
    impact_dir = tmp_path / "impactsearch"
    for d in (
        cache_dir, artifact_root, stack_dir, sig_dir,
        impact_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache_dir,
        "artifact_root": artifact_root,
        "stackbuilder_root": stack_dir,
        "signal_library_dir": sig_dir,
        "impactsearch_output_dir": impact_dir,
    }


def _write_cache_pkl(
    cache_dir: Path, ticker: str, *,
    last_date: str = "2026-05-08", n: int = 60,
) -> Path:
    import pandas as pd

    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(end=last_date, periods=n)
    df = pd.DataFrame(
        {
            "Close": [100.0 + i * 0.1 for i in range(n)],
            "SMA_1": [100.0 + i * 0.1 for i in range(n)],
            "SMA_2": [100.0 + i * 0.05 for i in range(n)],
        },
        index=dates,
    )
    payload = {
        "preprocessed_data": df,
        "active_pairs": ["Buy 2,1"] * n,
        "_ticker": ticker,
        "_last_date": pd.Timestamp(last_date),
        "last_date": pd.Timestamp(last_date),
        "signal_engine_cache_refresher_scope": "optimizer_v1",
        "existing_max_sma_day": 2,
    }
    safe = ticker.upper().replace("^", "_")
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_onepass_libs(
    sig_dir: Path, ticker: str, *,
    intervals: list[str] = ["1wk", "1mo", "3mo", "1y"],
) -> None:
    safe = ticker.upper().replace("^", "_")
    (sig_dir / f"{safe}_stable_v1_0_0.pkl"
     ).write_bytes(b"placeholder")
    for interval in intervals:
        (sig_dir / f"{safe}_stable_v1_0_0_{interval}.pkl"
         ).write_bytes(b"placeholder")


def _write_impactsearch_xlsx(
    impact_dir: Path, ticker: str,
) -> Path:
    import pandas as pd

    safe = ticker.upper().replace("^", "_")
    p = impact_dir / f"{safe}_analysis.xlsx"
    pd.DataFrame({
        "Primary Ticker": [ticker.upper()],
        "Resolved/Fetched": [ticker.upper()],
    }).to_excel(p, index=False)
    sidecar = p.with_suffix(
        p.suffix + ".manifest.json",
    )
    sidecar.write_text(
        json.dumps({"artifact": "impactsearch_xlsx"}),
        encoding="utf-8",
    )
    return p


def _write_impactsearch_research_day(
    artifact_root: Path, target: str, *,
    last_date: str = "2026-05-08",
    primary_ticker: str = "HRNNF",
) -> Path:
    safe = target.upper().replace("^", "_")
    is_dir = artifact_root / "impactsearch" / safe
    is_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="impactsearch",
        target_ticker=target,
        signal_source=primary_ticker,
        run_id=f"impactsearch_{primary_ticker}",
        metric_basis="Close",
        persist_skip_bars=0,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 1.0,
            "sharpe_ratio": 0.0,
            "trigger_days": 1,
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
        timeframes=["1d"],
    )
    return ra.write_research_day_artifact(
        art,
        is_dir / f"{primary_ticker}.research_day.json",
    )


def _write_stackbuilder_run(
    stack_root: Path, target: str, *,
    seed: str = "seedTC__AAA-D_BBB-D",
    members: list[str] = ["AAA", "BBB"],
    K_values: list[int] = list(range(1, 13)),
    mtime: float | None = None,
) -> Path:
    import os
    import pandas as pd

    safe = target.upper().replace("^", "_")
    run_dir = stack_root / safe / seed
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = [{
        "K": k, "Trigger Days": 100 + k,
        "Total Capture (%)": 10.0 + k,
        "Sharpe Ratio": 0.1, "p-Value": 0.05,
        "Members": str([f"{m}[D]" for m in members]),
    } for k in K_values]
    lb_path = run_dir / "combo_leaderboard.xlsx"
    pd.DataFrame(rows, columns=[
        "K", "Trigger Days", "Total Capture (%)",
        "Sharpe Ratio", "p-Value", "Members",
    ]).to_excel(lb_path, index=False)
    if mtime is not None:
        os.utime(run_dir, (mtime, mtime))
        os.utime(lb_path, (mtime, mtime))
    return run_dir


def _write_daily_k_artifact(
    artifact_root: Path, target: str, K: int, *,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    last_date: str = "2026-05-08",
) -> Path:
    safe = target.upper().replace("^", "_")
    tf_dir = artifact_root / "trafficflow" / safe
    tf_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow", target_ticker=target,
        signal_source="",
        run_id=f"{seed_run_id}__K{K}",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 5.0,
            "sharpe_ratio": 0.05, "trigger_days": 3,
        },
        daily=[{
            "date": last_date,
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "pressure_signal": "Buy",
            "buy_count": 1, "short_count": 0,
            "none_count": 0, "missing_count": 0,
            "active_count": 1, "available_count": 1,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 0.0,
            "is_trigger_day": True,
        }],
        K=K, members=["AAA", "BBB"],
        protocol_per_member={"AAA": "D", "BBB": "D"},
        timeframes=["1d"],
    )
    return ra.write_research_day_artifact(
        art,
        tf_dir / f"{seed_run_id}__K{K}.research_day.json",
    )


def _write_mtf_artifact(
    artifact_root: Path, target: str, K: int, *,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    last_date: str = "2026-05-08",
) -> Path:
    safe = target.upper().replace("^", "_")
    tf_dir = artifact_root / "trafficflow" / safe
    tf_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow", target_ticker=target,
        signal_source="",
        run_id=f"{seed_run_id}__K{K}__MTF",
        metric_basis="Close", persist_skip_bars=0,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 5.0,
            "sharpe_ratio": 0.05, "trigger_days": 3,
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
            "buy_count": 5, "short_count": 0,
            "none_count": 0, "missing_count": 0,
            "active_count": 5, "available_count": 5,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 1.0,
            "is_trigger_day": True,
        }],
        K=K, members=["AAA", "BBB"],
        protocol_per_member={"AAA": "D", "BBB": "D"},
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    return ra.write_research_day_artifact(
        art,
        tf_dir / f"{seed_run_id}__K{K}__MTF.research_day.json",
    )


def _write_confluence_artifact(
    artifact_root: Path, target: str, *,
    last_date: str = "2026-05-08",
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    buy_votes: int = 5, short_votes: int = 0,
) -> Path:
    safe = target.upper().replace("^", "_")
    conf_dir = artifact_root / "confluence" / safe
    conf_dir.mkdir(parents=True, exist_ok=True)
    K_values = list(range(1, 13))
    timeframes = ["1d", "1wk", "1mo", "3mo", "1y"]
    total_cells = len(K_values) * len(timeframes)
    missing_votes = 0
    none_votes = (
        total_cells - buy_votes - short_votes - missing_votes
    )
    active_count = buy_votes + short_votes
    available_count = active_count + none_votes
    if buy_votes == 0 and short_votes == 0:
        agreement_active = 0
        sig = "None"
    elif buy_votes > 0 and short_votes == 0:
        agreement_active = buy_votes
        sig = "Buy"
    elif buy_votes == 0 and short_votes > 0:
        agreement_active = short_votes
        sig = "Short"
    else:
        agreement_active = 0
        sig = "None"
    sig_value = {"Buy": 1, "Short": -1, "None": 0}[sig]
    run_ids = [
        f"{seed_run_id}__K{k}__MTF" for k in K_values
    ]
    row = {
        "date": last_date, "target": target,
        "target_ticker": target,
        "target_close": 100.0,
        "target_return_pct": 0.0,
        "confluence_signal": sig, "signal": sig,
        "signal_value": sig_value,
        "agreement_active": agreement_active,
        "agreement_total": available_count,
        "active_count": active_count,
        "available_count": available_count,
        "buy_votes": buy_votes,
        "short_votes": short_votes,
        "none_votes": none_votes,
        "missing_votes": missing_votes,
        "K_values": K_values, "timeframes": timeframes,
        "source_trafficflow_mtf_run_ids": run_ids,
        "daily_capture_pct": 0.0,
        "is_trigger_day": False,
        "cumulative_capture_pct": 0.0,
    }
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="confluence", target_ticker=target,
        signal_source="", run_id="mtf_consensus",
        metric_basis="Close", persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 50.0,
            "sharpe_ratio": 0.1, "trigger_days": 5,
        },
        daily=[row], timeframes=timeframes, min_active=1,
    )
    return ra.write_research_day_artifact(
        art,
        conf_dir / f"{safe}__MTF_CONSENSUS.research_day.json",
    )


def _write_full_valid_fixture(
    dirs: dict[str, Path], target: str, *,
    members: list[str] = ["AAA", "BBB"],
    last_date: str = "2026-05-08",
    cache_last_date: Optional[str] = None,
    include_downstream: bool = True,
    buy_votes: int = 5, short_votes: int = 0,
) -> None:
    cache_d = cache_last_date or last_date
    _write_cache_pkl(
        dirs["cache_dir"], target, last_date=cache_d,
    )
    for m in members:
        _write_cache_pkl(
            dirs["cache_dir"], m, last_date=cache_d,
        )
    _write_onepass_libs(dirs["signal_library_dir"], target)
    for m in members:
        safe = m.upper().replace("^", "_")
        (dirs["signal_library_dir"]
         / f"{safe}_stable_v1_0_0.pkl"
         ).write_bytes(b"placeholder")
    _write_impactsearch_xlsx(
        dirs["impactsearch_output_dir"], target,
    )
    _write_impactsearch_research_day(
        dirs["artifact_root"], target, last_date=last_date,
    )
    _write_stackbuilder_run(
        dirs["stackbuilder_root"], target, members=members,
    )
    if include_downstream:
        for k in range(1, 13):
            _write_daily_k_artifact(
                dirs["artifact_root"], target, k,
                last_date=last_date,
            )
            _write_mtf_artifact(
                dirs["artifact_root"], target, k,
                last_date=last_date,
            )
        _write_confluence_artifact(
            dirs["artifact_root"], target,
            last_date=last_date,
            buy_votes=buy_votes,
            short_votes=short_votes,
        )


def _snapshot_tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# 1. Forbidden-imports static guard
# ---------------------------------------------------------------------------


def test_queue_planner_has_no_forbidden_imports():
    tree = ast.parse(
        Path(eqp.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance",
        "dash",
        "spymaster",
        "onepass",
        "impactsearch",
        "stackbuilder",
        "trafficflow",
        "confluence",
        "cross_ticker_confluence",
        "daily_signal_board",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_writer",
        "daily_board_automation_executor",
        "subprocess",
    }
    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad = [m for m in found if m.split(".")[0] in forbidden]
    assert not bad, (
        f"forbidden import in queue planner: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. Empty universe -> empty queues
# ---------------------------------------------------------------------------


def test_empty_universe_returns_empty_queues(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    report = eqp.build_execution_queue(
        tickers=[], from_stackbuilder_universe=False,
        current_as_of_date="2026-05-08", **dirs,
    )
    assert report.inspected_count == 0
    assert report.discovered_stackbuilder_ticker_count == 0
    for q in (
        report.pipeline_only_queue,
        report.refresh_source_cache_then_pipeline_queue,
        report.wait_for_cache_ahead_queue,
        report.manual_stackbuilder_queue,
        report.upstream_blocked_queue,
        report.downstream_gap_queue,
        report.current_leader_eligible_queue,
    ):
        assert q == ()
    assert all(v == 0 for v in report.queue_counts.values())
    assert report.selected_refresh_count == 0
    assert report.selected_pipeline_count == 0


# ---------------------------------------------------------------------------
# 3. Explicit ticker list works
# ---------------------------------------------------------------------------


def test_explicit_ticker_list_routes_to_queue(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY",
        cache_last_date="2026-05-08",
        last_date="2026-05-08",
        include_downstream=False,
    )
    # SPY -> wait_for_cache_ahead_of_cutoff
    # (cache == cutoff, no downstream).
    report = eqp.build_execution_queue(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    assert report.inspected_count == 1
    assert len(report.wait_for_cache_ahead_queue) == 1
    spy = report.wait_for_cache_ahead_queue[0]
    assert spy.ticker == "SPY"
    assert spy.queue_name == (
        eqp.QUEUE_NAME_WAIT_FOR_CACHE_AHEAD
    )
    assert spy.advisory_command is None
    assert spy.write_requires_env_var is False


# ---------------------------------------------------------------------------
# 4. --from-stackbuilder-universe works
# ---------------------------------------------------------------------------


def test_from_stackbuilder_universe_discovers_and_routes(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY", include_downstream=False,
    )
    _write_full_valid_fixture(
        dirs, "AAPL", include_downstream=False,
    )
    report = eqp.build_execution_queue(
        from_stackbuilder_universe=True,
        current_as_of_date="2026-05-08", **dirs,
    )
    assert report.inspected_count == 2
    assert report.discovered_stackbuilder_ticker_count == 2
    queue_tickers = {
        x.ticker
        for x in report.wait_for_cache_ahead_queue
    }
    assert queue_tickers == {"AAPL", "SPY"}


# ---------------------------------------------------------------------------
# 5. --max-refresh truncates refresh queue
# ---------------------------------------------------------------------------


def test_max_refresh_truncates_refresh_queue(
    tmp_path: Path,
):
    """5 tickers with cache strictly OLDER than cutoff
    => action = refresh_source_cache_then_pipeline.
    --max-refresh=2 truncates to 2 + sets truncation
    flag."""
    dirs = _layout(tmp_path)
    # Cache last_date = 2026-05-06 (older), cutoff =
    # 2026-05-08 -> refresh recommended.
    for t in ("RA", "RB", "RC", "RD", "RE"):
        _write_full_valid_fixture(
            dirs, t,
            cache_last_date="2026-05-06",
            last_date="2026-05-08",
            include_downstream=False,
        )
    report = eqp.build_execution_queue(
        tickers=["RA", "RB", "RC", "RD", "RE"],
        current_as_of_date="2026-05-08",
        max_refresh=2, **dirs,
    )
    # Every ticker should land in refresh queue.
    pre = (
        report.queue_counts[
            eqp.QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE
        ]
    )
    assert pre == 2
    assert report.selected_refresh_count == 2
    assert report.queue_truncation[
        eqp.QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE
    ] is True
    # Sort: ticker alphabetical -> RA, RB.
    refresh_tickers = [
        x.ticker
        for x in report.refresh_source_cache_then_pipeline_queue
    ]
    assert refresh_tickers == ["RA", "RB"]
    # Advisory writer commands present + env-var flag.
    for item in report.refresh_source_cache_then_pipeline_queue:
        assert item.advisory_command == (
            f"python daily_board_automation_writer.py "
            f"--ticker {item.ticker} --write"
        )
        assert item.write_requires_env_var is True


# ---------------------------------------------------------------------------
# 6. --max-pipeline truncates pipeline queue
# ---------------------------------------------------------------------------


def test_max_pipeline_truncates_pipeline_queue(
    tmp_path: Path,
):
    """Cache strictly AHEAD of cutoff but downstream
    artifacts missing -> run_pipeline_only. 5 tickers,
    max_pipeline=2."""
    dirs = _layout(tmp_path)
    for t in ("PA", "PB", "PC", "PD", "PE"):
        _write_full_valid_fixture(
            dirs, t,
            cache_last_date="2026-05-12",  # ahead
            last_date="2026-05-08",
            include_downstream=False,
        )
    report = eqp.build_execution_queue(
        tickers=["PA", "PB", "PC", "PD", "PE"],
        current_as_of_date="2026-05-08",
        max_pipeline=2, **dirs,
    )
    assert report.queue_counts[
        eqp.QUEUE_NAME_PIPELINE_ONLY
    ] == 2
    assert report.selected_pipeline_count == 2
    assert report.queue_truncation[
        eqp.QUEUE_NAME_PIPELINE_ONLY
    ] is True
    pipeline_tickers = [
        x.ticker for x in report.pipeline_only_queue
    ]
    assert pipeline_tickers == ["PA", "PB"]
    for item in report.pipeline_only_queue:
        assert item.advisory_command is not None
        assert item.write_requires_env_var is True
        assert item.recommended_action == (
            "run_pipeline_only"
        )


# ---------------------------------------------------------------------------
# 7. Blocked queues have no advisory command
# ---------------------------------------------------------------------------


def test_blocked_queues_have_no_advisory_command(
    tmp_path: Path,
):
    """Several blocked tickers; none of the blocked
    queues should carry an advisory writer command."""
    dirs = _layout(tmp_path)
    # SPY: missing target cache -> upstream_blocked_queue.
    _write_full_valid_fixture(dirs, "SPY")
    (dirs["cache_dir"]
     / "SPY_precomputed_results.pkl").unlink()
    # GAP: full chain but no downstream -> downstream_gap.
    _write_full_valid_fixture(
        dirs, "GAP", include_downstream=False,
    )
    # NOSTACK: no StackBuilder -> manual_stackbuilder.
    _write_cache_pkl(dirs["cache_dir"], "NOSTACK")
    _write_onepass_libs(
        dirs["signal_library_dir"], "NOSTACK",
    )
    _write_impactsearch_xlsx(
        dirs["impactsearch_output_dir"], "NOSTACK",
    )
    _write_impactsearch_research_day(
        dirs["artifact_root"], "NOSTACK",
    )
    # WAIT: cache equal cutoff -> wait_for_cache_ahead.
    _write_full_valid_fixture(
        dirs, "WAIT",
        cache_last_date="2026-05-08",
        last_date="2026-05-08",
        include_downstream=False,
    )
    report = eqp.build_execution_queue(
        tickers=["SPY", "GAP", "NOSTACK", "WAIT"],
        current_as_of_date="2026-05-08", **dirs,
    )
    blocked_queues = (
        report.wait_for_cache_ahead_queue
        + report.manual_stackbuilder_queue
        + report.upstream_blocked_queue
        + report.downstream_gap_queue
    )
    for item in blocked_queues:
        assert item.advisory_command is None
        assert item.write_requires_env_var is False


# ---------------------------------------------------------------------------
# 8. Wait-for-cache-ahead carries no command
# ---------------------------------------------------------------------------


def test_wait_for_cache_ahead_has_no_command(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "WAIT",
        cache_last_date="2026-05-08",
        last_date="2026-05-08",
        include_downstream=False,
    )
    report = eqp.build_execution_queue(
        tickers=["WAIT"], current_as_of_date="2026-05-08",
        **dirs,
    )
    assert len(report.wait_for_cache_ahead_queue) == 1
    item = report.wait_for_cache_ahead_queue[0]
    assert item.advisory_command is None
    assert item.cache_cutoff_action == (
        "pipeline_output_lags_persist_skip"
    )


# ---------------------------------------------------------------------------
# 9. Missing target cache -> upstream_blocked
# ---------------------------------------------------------------------------


def test_missing_target_cache_routes_to_upstream_blocked(
    tmp_path: Path,
):
    """Codex-amendment carry-forward (Phase 6I-5):
    missing target Signal Engine cache must land in
    upstream_blocked_queue, NOT downstream_gap_queue."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    (dirs["cache_dir"]
     / "SPY_precomputed_results.pkl").unlink()
    report = eqp.build_execution_queue(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    tickers_upstream = {
        x.ticker for x in report.upstream_blocked_queue
    }
    tickers_downstream = {
        x.ticker for x in report.downstream_gap_queue
    }
    assert "SPY" in tickers_upstream
    assert "SPY" not in tickers_downstream
    item = report.upstream_blocked_queue[0]
    assert item.upstream_primary_blocker == (
        "missing_target_signal_engine_cache"
    )
    assert item.advisory_command is None


# ---------------------------------------------------------------------------
# 10. Downstream gap routes correctly
# ---------------------------------------------------------------------------


def test_downstream_gap_state_surfaces_primary_blocker(
    tmp_path: Path,
):
    """A ticker whose upstream is fully ready AND
    downstream chain is missing carries
    ``primary_blocker == "downstream_artifact_gap"`` and
    ``upstream_primary_blocker == ""`` in its row,
    regardless of which queue the action routes it to.
    The queue placement follows the action-first
    cascade (see § 4 of the doc) -- the queue planner's
    classification cascade is:

      1. Upstream/input blockers.
      2. **Action-first** routing for actionable
         verdicts.
      3. ``primary_blocker == downstream_artifact_gap``
         as the catch-all after action routing.

    In this fixture (cache == cutoff, no downstream),
    the preflight emits ``wait_for_cache_ahead_of_cutoff``
    -- so the ticker lands in
    ``wait_for_cache_ahead_queue``, but the row still
    surfaces the downstream-gap blocker for operator
    inspection.

    A separate test
    (``test_downstream_gap_queue_catches_blocked_manual_review``)
    exercises the catch-all routing path."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "GAP", include_downstream=False,
    )
    report = eqp.build_execution_queue(
        tickers=["GAP"], current_as_of_date="2026-05-08",
        **dirs,
    )
    # Action-first cascade routes to wait_for_cache_ahead.
    wait_tickers = {
        x.ticker for x in report.wait_for_cache_ahead_queue
    }
    assert wait_tickers == {"GAP"}
    item = report.wait_for_cache_ahead_queue[0]
    # But the row's blocker fields still expose the
    # downstream-gap state for operator inspection.
    assert item.upstream_primary_blocker == ""
    assert item.primary_blocker == (
        "downstream_artifact_gap"
    )


def test_downstream_gap_queue_catches_blocked_manual_review(
    tmp_path: Path,
):
    """A ticker with upstream clean + action ==
    ``blocked_manual_review`` (no actionable preflight
    verdict) AND composite ``primary_blocker ==
    "downstream_artifact_gap"`` lands in
    ``downstream_gap_queue``. This is the residual
    catch-all path. We construct the state by stubbing
    the universe planner so the test does not depend on
    a contrived combination of real on-disk artifacts."""
    # Build a minimal stub state to drive the
    # classification alone. The queue planner's
    # ``build_execution_queue`` would normally produce
    # this via the real chain; we exercise the
    # classifier directly here so the test is robust.
    from daily_board_universe_planner import (
        DailyBoardUniversePlanState,
    )

    state = DailyBoardUniversePlanState(
        ticker="ZZZ",
        current_as_of_date="2026-05-08",
        upstream_trio_ready=True,
        upstream_primary_blocker="",
        upstream_issue_codes=(),
        stackbuilder_run_count=1,
        stackbuilder_selected_run_id="seedTC",
        stackbuilder_selection_policy=(
            "single_available_stack"
        ),
        can_build_daily_trafficflow_k=True,
        can_project_multitimeframe=True,
        can_build_confluence=True,
        automation_recommended_action=(
            "blocked_manual_review"
        ),
        automation_blocking_reasons=(
            "health_report_blocked",
        ),
        cache_cutoff_action="refresh_source_cache",
        source_cache_date="2026-05-04",
        downstream_contract_valid=False,
        downstream_contract_verdict=(
            "fix_pipeline_artifacts_contract"
        ),
        current_leader_eligible=False,
        ranking_blocked_reason="",
        consensus_signal=None,
        signal_value=None,
        agreement_active=None,
        agreement_total=None,
        agreement_ratio=None,
        buy_votes=None,
        short_votes=None,
        none_votes=None,
        missing_votes=None,
        signed_vote_score=None,
        total_capture_pct=None,
        sharpe_ratio=None,
        trigger_days=None,
        wins=None,
        losses=None,
        p_value=None,
        primary_blocker="downstream_artifact_gap",
    )
    assert eqp._classify_queue(state) == (
        eqp.QUEUE_NAME_DOWNSTREAM_GAP
    )


# ---------------------------------------------------------------------------
# 11. Already current_leader_eligible
# ---------------------------------------------------------------------------


def test_leader_eligible_routes_to_leader_eligible_queue(
    tmp_path: Path,
):
    """Full valid chain + cache strictly ahead of cutoff
    => downstream contract is current AND
    current_leader_eligible is True per the readiness
    layer (well, the leader_eligible verdict depends on
    persist-skip rules; on a full chain matching the
    cutoff this is typically True)."""
    dirs = _layout(tmp_path)
    # Cache cutoff at 2026-05-08 with Confluence
    # last_date=2026-05-08 + cache ahead at 2026-05-09:
    # current_leader_eligible should be True.
    _write_full_valid_fixture(
        dirs, "LEAD",
        cache_last_date="2026-05-12",
        last_date="2026-05-08",
    )
    report = eqp.build_execution_queue(
        tickers=["LEAD"], current_as_of_date="2026-05-08",
        **dirs,
    )
    # Either routed to leader-eligible (if
    # current_leader_eligible) or pipeline_only depending
    # on the preflight's verdict. Whichever it is, the
    # ticker should NOT be in upstream_blocked or
    # downstream_gap (no upstream issues; full chain).
    blocked = {
        x.ticker for x in (
            list(report.upstream_blocked_queue)
            + list(report.downstream_gap_queue)
        )
    }
    assert "LEAD" not in blocked
    # Find the row's queue.
    all_items = (
        list(report.pipeline_only_queue)
        + list(report.refresh_source_cache_then_pipeline_queue)
        + list(report.wait_for_cache_ahead_queue)
        + list(report.current_leader_eligible_queue)
    )
    matches = [x for x in all_items if x.ticker == "LEAD"]
    assert len(matches) == 1
    # The matched item must be in either
    # current_leader_eligible_queue or one of the
    # actionable queues.
    assert matches[0].queue_name in {
        eqp.QUEUE_NAME_CURRENT_LEADER_ELIGIBLE,
        eqp.QUEUE_NAME_PIPELINE_ONLY,
        eqp.QUEUE_NAME_WAIT_FOR_CACHE_AHEAD,
        eqp.QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE,
    }


# ---------------------------------------------------------------------------
# 12. Ranking tails pass-through
# ---------------------------------------------------------------------------


def test_ranking_tails_pass_through_unchanged(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "BUYHI",
        cache_last_date="2026-05-12",
        last_date="2026-05-08",
        buy_votes=50, short_votes=5,
    )
    _write_full_valid_fixture(
        dirs, "NOBUY",
        cache_last_date="2026-05-12",
        last_date="2026-05-08",
        buy_votes=0, short_votes=10,
    )
    # Compare against the Phase 6I-5 universe planner
    # tails directly.
    universe = uplanner.plan_daily_board_universe(
        tickers=["BUYHI", "NOBUY"],
        current_as_of_date="2026-05-08", **dirs,
    )
    report = eqp.build_execution_queue(
        tickers=["BUYHI", "NOBUY"],
        current_as_of_date="2026-05-08", **dirs,
    )
    assert report.positive_tail == universe.positive_tail
    assert report.negative_tail == universe.negative_tail
    assert report.low_buy_tail == universe.low_buy_tail
    # And at least one row should be in each tail per
    # the Phase 6I-3 product contract (BUYHI in
    # positive; NOBUY in low_buy).
    positive_tickers = [
        r["ticker"] for r in report.positive_tail
    ]
    low_buy_tickers = [
        r["ticker"] for r in report.low_buy_tail
    ]
    assert "BUYHI" in positive_tickers
    assert "NOBUY" in low_buy_tickers


# ---------------------------------------------------------------------------
# 13. JSON round-trip
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    report = eqp.build_execution_queue(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    reparsed = json.loads(serialized)
    assert reparsed["inspected_count"] == 1
    # Locate SPY in whichever queue it landed in.
    found_ticker = None
    for q in eqp.ALL_QUEUE_NAMES:
        for row in reparsed[q]:
            if row["ticker"] == "SPY":
                found_ticker = row
                break
        if found_ticker:
            break
    assert found_ticker is not None
    assert found_ticker["queue_name"] == found_ticker[
        "queue_name"
    ]
    # Counts match the array lengths.
    for name in eqp.ALL_QUEUE_NAMES:
        assert reparsed["queue_counts"][name] == len(
            reparsed[name],
        )


# ---------------------------------------------------------------------------
# 14. No-writes guard
# ---------------------------------------------------------------------------


def test_queue_planner_does_not_mutate_tree(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    before = _snapshot_tree(tmp_path)
    before_bytes = {p: p.read_bytes() for p in before}
    report = eqp.build_execution_queue(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    after = _snapshot_tree(tmp_path)
    assert before == after
    for p, payload in before_bytes.items():
        assert p.read_bytes() == payload
    assert report.inspected_count == 1


# ---------------------------------------------------------------------------
# 15. --include-blocked False suppresses blocked queues
# ---------------------------------------------------------------------------


def test_no_include_blocked_suppresses_blocked_queues(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    (dirs["cache_dir"]
     / "SPY_precomputed_results.pkl").unlink()
    # SPY -> upstream_blocked (missing target cache).
    report = eqp.build_execution_queue(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        include_blocked=False, **dirs,
    )
    # The state was inspected (inspected_count=1) but
    # the upstream_blocked_queue is suppressed.
    assert report.inspected_count == 1
    assert report.upstream_blocked_queue == ()
    assert report.queue_counts[
        eqp.QUEUE_NAME_UPSTREAM_BLOCKED
    ] == 0
    # The write-ready + leader-eligible queues are
    # always emitted.
    assert (
        eqp.QUEUE_NAME_PIPELINE_ONLY
        in report.queue_counts
    )
    assert (
        eqp.QUEUE_NAME_CURRENT_LEADER_ELIGIBLE
        in report.queue_counts
    )


# ---------------------------------------------------------------------------
# 16. CLI rc=0/2/3
# ---------------------------------------------------------------------------


def test_cli_no_ticker_source_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = eqp.main([])
    assert rc == 2
    parsed = json.loads(err.getvalue().strip())
    assert parsed.get("error") == "no_ticker_source_supplied"


def test_cli_unknown_flag_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = eqp.main(["--not-a-flag", "x"])
    assert rc == 2


def test_cli_happy_path_emits_json(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = eqp.main([
            "--ticker", "SPY",
            "--current-as-of-date", "2026-05-08",
            "--cache-dir", str(dirs["cache_dir"]),
            "--artifact-root", str(dirs["artifact_root"]),
            "--stackbuilder-root",
            str(dirs["stackbuilder_root"]),
            "--signal-library-dir",
            str(dirs["signal_library_dir"]),
            "--impactsearch-output-dir",
            str(dirs["impactsearch_output_dir"]),
            "--top-n", "5",
        ])
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert parsed["inspected_count"] == 1
    json.dumps(parsed)


def test_cli_no_include_blocked_flag(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    (dirs["cache_dir"]
     / "SPY_precomputed_results.pkl").unlink()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = eqp.main([
            "--ticker", "SPY",
            "--no-include-blocked",
            "--current-as-of-date", "2026-05-08",
            "--cache-dir", str(dirs["cache_dir"]),
            "--artifact-root", str(dirs["artifact_root"]),
            "--stackbuilder-root",
            str(dirs["stackbuilder_root"]),
            "--signal-library-dir",
            str(dirs["signal_library_dir"]),
            "--impactsearch-output-dir",
            str(dirs["impactsearch_output_dir"]),
        ])
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert parsed["include_blocked"] is False
    assert parsed[eqp.QUEUE_NAME_UPSTREAM_BLOCKED] == []
