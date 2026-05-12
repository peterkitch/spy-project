"""Phase 6I-5 tests for daily_board_universe_planner.

Pins:

  - Forbidden-imports static guard: no yfinance / dash /
    spymaster / onepass / impactsearch / stackbuilder /
    trafficflow / confluence runner / refresher / writer /
    subprocess.
  - Empty universe (no tickers + no --from-universe)
    returns a valid empty report.
  - Explicit ticker list bypasses universe discovery.
  - Universe discovery finds saved StackBuilder ticker
    directories (including the per-target dir created by
    the test fixture).
  - Multiple StackBuilder variants per ticker are allowed
    (the planner inherits Phase 6H-3 / 6I-4 semantics).
  - Tied newest-mtime StackBuilder selection routes to
    manual (upstream_trio_ready=False; primary_blocker
    surfaces the upstream cascade verdict).
  - Upstream trio ready but downstream Confluence chain
    missing -> downstream_gap classification +
    downstream_gap_tickers bucket carries the ticker;
    primary_blocker = "downstream_artifact_gap".
  - Cache PKL last_date equal to cutoff -> action ==
    wait_for_cache_ahead_of_cutoff (strict-inequality
    contract).
  - Ranking tails include positive AND low_buy cases
    when applicable. Low-buy buy_votes=0 ticker surfaces
    in low_buy_tail.
  - Deterministic tie-break: identical fixtures sort
    alphabetically by ticker.
  - JSON serialization round-trips.
  - No-writes guard: tmp_path snapshot before/after.
  - CLI: no ticker source -> rc=2; unknown flag -> rc=2;
    happy path emits valid JSON with rc=0.
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

import daily_board_universe_planner as planner  # noqa: E402
import daily_board_automation_preflight as dap  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers (self-contained; mirror the Phase 6I-4
# test pattern so the planner test is independent of any
# other test module's helpers)
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
    cache_dir: Path, ticker: str,
    *,
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
    impact_dir: Path, ticker: str, *,
    with_manifest: bool = True,
) -> Path:
    import pandas as pd

    safe = ticker.upper().replace("^", "_")
    p = impact_dir / f"{safe}_analysis.xlsx"
    pd.DataFrame({
        "Primary Ticker": [ticker.upper()],
        "Resolved/Fetched": [ticker.upper()],
    }).to_excel(p, index=False)
    if with_manifest:
        sidecar = p.with_suffix(
            p.suffix + ".manifest.json",
        )
        sidecar.write_text(
            json.dumps({"artifact": "impactsearch_xlsx"}),
            encoding="utf-8",
        )
    return p


def _write_impactsearch_research_day(
    artifact_root: Path, target: str,
    *,
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
        "K": k,
        "Trigger Days": 100 + k,
        "Total Capture (%)": 10.0 + k,
        "Sharpe Ratio": 0.1,
        "p-Value": 0.05,
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
        engine="trafficflow",
        target_ticker=target,
        signal_source="",
        run_id=f"{seed_run_id}__K{K}",
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
        K=K,
        members=["AAA", "BBB"],
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
        engine="trafficflow",
        target_ticker=target,
        signal_source="",
        run_id=f"{seed_run_id}__K{K}__MTF",
        metric_basis="Close",
        persist_skip_bars=0,
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
        K=K,
        members=["AAA", "BBB"],
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
    buy_votes: int = 5,
    short_votes: int = 0,
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
        "date": last_date,
        "target": target,
        "target_ticker": target,
        "target_close": 100.0,
        "target_return_pct": 0.0,
        "confluence_signal": sig,
        "signal": sig,
        "signal_value": sig_value,
        "agreement_active": agreement_active,
        "agreement_total": available_count,
        "active_count": active_count,
        "available_count": available_count,
        "buy_votes": buy_votes,
        "short_votes": short_votes,
        "none_votes": none_votes,
        "missing_votes": missing_votes,
        "K_values": K_values,
        "timeframes": timeframes,
        "source_trafficflow_mtf_run_ids": run_ids,
        "daily_capture_pct": 0.0,
        "is_trigger_day": False,
        "cumulative_capture_pct": 0.0,
    }
    art = ra.ResearchDayArtifact(
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
        daily=[row],
        timeframes=timeframes,
        min_active=1,
    )
    return ra.write_research_day_artifact(
        art,
        conf_dir / f"{safe}__MTF_CONSENSUS.research_day.json",
    )


def _write_full_valid_fixture(
    dirs: dict[str, Path],
    target: str,
    *,
    members: list[str] = ["AAA", "BBB"],
    last_date: str = "2026-05-08",
    cache_last_date: Optional[str] = None,
    include_downstream: bool = True,
    buy_votes: int = 5,
    short_votes: int = 0,
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


def test_planner_has_no_forbidden_imports():
    tree = ast.parse(
        Path(planner.__file__).read_text(encoding="utf-8"),
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
        f"forbidden import in planner: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. Empty universe -> valid empty report
# ---------------------------------------------------------------------------


def test_empty_universe_returns_empty_report(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    # No tickers, no --from-universe.
    report = planner.plan_daily_board_universe(
        tickers=[], from_stackbuilder_universe=False,
        current_as_of_date="2026-05-08", **dirs,
    )
    assert report.inspected_count == 0
    assert report.tickers == ()
    assert report.states == ()
    assert report.counts_by_automation_action == {}
    assert report.ready_for_pipeline_only_tickers == ()
    assert report.upstream_blocked_tickers == ()
    assert report.downstream_gap_tickers == ()
    # Universe count reflects the empty stackbuilder dir.
    assert (
        report.discovered_stackbuilder_ticker_count == 0
    )


def test_from_universe_with_empty_tree_returns_empty(
    tmp_path: Path,
):
    """``--from-stackbuilder-universe`` over an empty tree
    is valid (no tickers to inspect); the report is
    well-formed."""
    dirs = _layout(tmp_path)
    report = planner.plan_daily_board_universe(
        tickers=None, from_stackbuilder_universe=True,
        current_as_of_date="2026-05-08", **dirs,
    )
    assert report.inspected_count == 0
    assert (
        report.discovered_stackbuilder_ticker_count == 0
    )


# ---------------------------------------------------------------------------
# 3. Explicit ticker list bypasses discovery
# ---------------------------------------------------------------------------


def test_explicit_ticker_bypasses_universe_discovery(
    tmp_path: Path,
):
    """An explicit ticker list inspects exactly those
    tickers regardless of what is under the stackbuilder
    root. The universe count is still reported."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    # Universe has SPY only. Inspect AAPL (a ticker NOT
    # under stackbuilder/) explicitly.
    report = planner.plan_daily_board_universe(
        tickers=["AAPL"],
        from_stackbuilder_universe=False,
        current_as_of_date="2026-05-08", **dirs,
    )
    assert report.inspected_count == 1
    assert report.tickers == ("AAPL",)
    assert (
        report.discovered_stackbuilder_ticker_count == 1
    )
    # AAPL has no fixtures -> upstream missing OnePass +
    # missing StackBuilder + others; trio not ready.
    aapl = report.states[0]
    assert aapl.ticker == "AAPL"
    assert aapl.upstream_trio_ready is False


# ---------------------------------------------------------------------------
# 4. Universe discovery finds saved StackBuilder dirs
# ---------------------------------------------------------------------------


def test_universe_discovery_finds_stackbuilder_dirs(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    _write_full_valid_fixture(dirs, "AAPL")
    universe = planner.discover_stackbuilder_universe(
        dirs["stackbuilder_root"],
    )
    assert set(universe) == {"AAPL", "SPY"}
    # The planner with --from-stackbuilder-universe
    # inspects both.
    report = planner.plan_daily_board_universe(
        tickers=None, from_stackbuilder_universe=True,
        current_as_of_date="2026-05-08", **dirs,
    )
    assert report.inspected_count == 2
    assert set(report.tickers) == {"AAPL", "SPY"}
    assert (
        report.discovered_stackbuilder_ticker_count == 2
    )


def test_universe_discovery_skips_hidden_dirs(
    tmp_path: Path,
):
    """``_progress`` / dot-prefix entries are
    bookkeeping, not tickers; discovery skips them."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    # Add dot-prefixed and underscore-prefixed entries.
    (dirs["stackbuilder_root"] / "_progress").mkdir()
    (dirs["stackbuilder_root"] / ".tmp").mkdir()
    universe = planner.discover_stackbuilder_universe(
        dirs["stackbuilder_root"],
    )
    assert universe == ("SPY",)


# ---------------------------------------------------------------------------
# 5. Multiple StackBuilder variants are allowed
# ---------------------------------------------------------------------------


def test_multiple_stackbuilder_variants_allowed(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    older = time.time() - 3600
    _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedOLD__AAA-D_BBB-D", mtime=older,
    )
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    spy = report.states[0]
    assert spy.stackbuilder_run_count == 2
    assert spy.stackbuilder_selection_policy == (
        "latest_mtime_existing_pipeline_default"
    )
    # NOT ambiguous; upstream trio ready (mtime gap is
    # clear, no age window is applied).
    assert spy.upstream_trio_ready is True


# ---------------------------------------------------------------------------
# 6. Tied newest-mtime routes to manual
# ---------------------------------------------------------------------------


def test_ambiguous_tied_stackbuilder_routes_to_manual(
    tmp_path: Path,
):
    import os
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    same_mtime = time.time() - 120
    seed_tc_dir = (
        dirs["stackbuilder_root"]
        / "SPY"
        / "seedTC__AAA-D_BBB-D"
    )
    os.utime(seed_tc_dir, (same_mtime, same_mtime))
    os.utime(
        seed_tc_dir / "combo_leaderboard.xlsx",
        (same_mtime, same_mtime),
    )
    _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTIED__CCC-D_DDD-D", mtime=same_mtime,
    )
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    spy = report.states[0]
    assert spy.stackbuilder_selection_policy == (
        "ambiguous_tied_mtime"
    )
    # Upstream trio not ready (ambiguous is in 6I-4
    # blocking set).
    assert spy.upstream_trio_ready is False
    # SPY appears in upstream_blocked_tickers AND the
    # automation action surfaces as the preflight's
    # manual-review routing.
    assert "SPY" in report.upstream_blocked_tickers


# ---------------------------------------------------------------------------
# 7. Upstream ready but downstream missing => downstream_gap
# ---------------------------------------------------------------------------


def test_upstream_ready_downstream_missing_is_downstream_gap(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY", include_downstream=False,
    )
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    spy = report.states[0]
    assert spy.upstream_trio_ready is True
    assert spy.downstream_contract_valid is False
    # Codex amendment: upstream_primary_blocker is the
    # SANITIZED form (downstream-gap stripped) so this
    # ticker reports an empty upstream blocker; the
    # composite primary_blocker still surfaces the gap.
    assert spy.upstream_primary_blocker == ""
    assert spy.primary_blocker == (
        planner.BLOCKER_DOWNSTREAM_ARTIFACT_GAP
    )
    assert "SPY" in report.downstream_gap_tickers
    # And NOT in upstream_blocked (sanitized blocker is
    # empty).
    assert "SPY" not in report.upstream_blocked_tickers


# ---------------------------------------------------------------------------
# 7a / 7b / 7c -- Codex amendment: non-trio upstream
# blockers (missing target cache / missing member cache /
# missing member OnePass library) must NOT pollute
# downstream_gap_tickers.
# ---------------------------------------------------------------------------


def test_missing_target_cache_is_upstream_blocked_not_downstream_gap(
    tmp_path: Path,
):
    """A ticker with missing target Signal Engine cache
    must land in ``upstream_blocked_tickers`` and NOT in
    ``downstream_gap_tickers``. Phase 6I-4 keeps
    ``upstream_trio_ready=True`` (the narrow trio --
    OnePass + StackBuilder + leaderboard -- is intact),
    but the audit's primary blocker is
    ``missing_target_signal_engine_cache``; the planner
    must classify by the sanitized upstream blocker,
    NOT by the narrow trio flag."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    (dirs["cache_dir"]
     / "SPY_precomputed_results.pkl").unlink()
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    spy = report.states[0]
    assert spy.upstream_primary_blocker == (
        "missing_target_signal_engine_cache"
    )
    assert spy.primary_blocker == (
        "missing_target_signal_engine_cache"
    )
    assert "SPY" in report.upstream_blocked_tickers
    # The bug fix: SPY must NOT be classified as a
    # downstream gap when the real blocker is a cache
    # miss.
    assert "SPY" not in report.downstream_gap_tickers


def test_missing_member_cache_is_upstream_blocked(
    tmp_path: Path,
):
    """Same separation for missing member cache."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY", members=["AAA", "BBB"],
    )
    (dirs["cache_dir"]
     / "AAA_precomputed_results.pkl").unlink()
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    spy = report.states[0]
    assert spy.upstream_primary_blocker == (
        "missing_member_signal_engine_cache"
    )
    assert spy.primary_blocker == (
        "missing_member_signal_engine_cache"
    )
    assert "SPY" in report.upstream_blocked_tickers
    assert "SPY" not in report.downstream_gap_tickers


def test_missing_member_onepass_library_is_upstream_blocked(
    tmp_path: Path,
):
    """Same separation for missing member OnePass
    library."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY", members=["AAA", "BBB"],
    )
    (dirs["signal_library_dir"]
     / "AAA_stable_v1_0_0.pkl").unlink()
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    spy = report.states[0]
    assert spy.upstream_primary_blocker == (
        "missing_member_onepass_library"
    )
    assert spy.primary_blocker == (
        "missing_member_onepass_library"
    )
    assert "SPY" in report.upstream_blocked_tickers
    assert "SPY" not in report.downstream_gap_tickers


# ---------------------------------------------------------------------------
# 8. Cache last_date == cutoff -> wait_for_cache_ahead
# ---------------------------------------------------------------------------


def test_cache_equal_cutoff_routes_to_wait_for_cache_ahead(
    tmp_path: Path,
):
    """The preflight's cache-vs-cutoff rule is strict:
    cache must be > cutoff to clear. Equal -> wait. The
    downstream chain is deliberately absent so the
    preflight does NOT short-circuit to ``already
    current``."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY",
        cache_last_date="2026-05-08",
        last_date="2026-05-08",
        include_downstream=False,
    )
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    spy = report.states[0]
    assert spy.automation_recommended_action == (
        dap.RECOMMENDED_WAIT_FOR_CACHE_AHEAD_OF_CUTOFF
    )
    assert spy.cache_cutoff_action == (
        "pipeline_output_lags_persist_skip"
    )
    assert spy.source_cache_date == "2026-05-08"
    assert "SPY" in report.wait_for_cache_ahead_tickers


# ---------------------------------------------------------------------------
# 9. Ranking tails include positive + low_buy
# ---------------------------------------------------------------------------


def test_ranking_tails_include_positive_and_low_buy(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    # BUYHI: 50 buy + 5 short + 5 none = 60 cells; strong
    # positive.
    _write_full_valid_fixture(
        dirs, "BUYHI", buy_votes=50, short_votes=5,
    )
    # NOBUY: 0 buy + 10 short + 50 none = 60 cells;
    # low_buy candidate (buy_ratio=0 <= 0.10).
    _write_full_valid_fixture(
        dirs, "NOBUY", buy_votes=0, short_votes=10,
    )
    report = planner.plan_daily_board_universe(
        tickers=["BUYHI", "NOBUY"],
        current_as_of_date="2026-05-08", **dirs,
    )
    positive_tickers = [r["ticker"] for r in report.positive_tail]
    low_buy_tickers = [r["ticker"] for r in report.low_buy_tail]
    assert "BUYHI" in positive_tickers
    assert "NOBUY" in low_buy_tickers


# ---------------------------------------------------------------------------
# 10. Deterministic tie-break alphabetical
# ---------------------------------------------------------------------------


def test_top_tail_deterministic_alphabetical_tie_break(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    for t in ("CCCC", "AAAA", "BBBB"):
        _write_full_valid_fixture(
            dirs, t, buy_votes=30, short_votes=5,
        )
    report = planner.plan_daily_board_universe(
        tickers=["CCCC", "AAAA", "BBBB"],
        current_as_of_date="2026-05-08", **dirs,
    )
    pos = [r["ticker"] for r in report.positive_tail]
    assert pos == ["AAAA", "BBBB", "CCCC"]


# ---------------------------------------------------------------------------
# 11. JSON round-trip
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    reparsed = json.loads(serialized)
    assert reparsed["inspected_count"] == 1
    assert reparsed["states"][0]["ticker"] == "SPY"
    # Tail rows preserve the full ranking row schema.
    if reparsed["positive_tail"]:
        first = reparsed["positive_tail"][0]
        assert "signed_vote_score" in first
        assert "total_capture_pct" in first
        assert "p_value" in first


# ---------------------------------------------------------------------------
# 12. No-writes guard
# ---------------------------------------------------------------------------


def test_planner_does_not_mutate_tree(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    before = _snapshot_tree(tmp_path)
    before_bytes = {p: p.read_bytes() for p in before}
    report = planner.plan_daily_board_universe(
        tickers=["SPY"], current_as_of_date="2026-05-08",
        **dirs,
    )
    after = _snapshot_tree(tmp_path)
    assert before == after
    for p, payload in before_bytes.items():
        assert p.read_bytes() == payload
    assert report.inspected_count == 1


# ---------------------------------------------------------------------------
# 13. CLI: no ticker source / unknown flag / happy path
# ---------------------------------------------------------------------------


def test_cli_no_ticker_source_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = planner.main([])
    assert rc == 2
    parsed = json.loads(err.getvalue().strip())
    assert parsed.get("error") == "no_ticker_source_supplied"


def test_cli_blank_ticker_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = planner.main(["--ticker", "   "])
    assert rc == 2


def test_cli_unknown_flag_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = planner.main(["--not-a-flag", "x"])
    assert rc == 2


def test_cli_happy_path_emits_json(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = planner.main([
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
    assert parsed["top_n"] == 5
    assert parsed["states"][0]["ticker"] == "SPY"
    json.dumps(parsed)


# ---------------------------------------------------------------------------
# 14. From-universe + explicit list union semantics
# ---------------------------------------------------------------------------


def test_from_universe_union_with_explicit_list(
    tmp_path: Path,
):
    """``--ticker NEWBIE --from-stackbuilder-universe``
    inspects NEWBIE PLUS every saved StackBuilder
    ticker, deduplicated."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, "SPY")
    _write_full_valid_fixture(dirs, "AAPL")
    report = planner.plan_daily_board_universe(
        tickers=["NEWBIE"],
        from_stackbuilder_universe=True,
        current_as_of_date="2026-05-08", **dirs,
    )
    assert set(report.tickers) == {"AAPL", "NEWBIE", "SPY"}
    assert report.inspected_count == 3
    # NEWBIE is not in the universe so the discovered
    # count is still 2.
    assert (
        report.discovered_stackbuilder_ticker_count == 2
    )
