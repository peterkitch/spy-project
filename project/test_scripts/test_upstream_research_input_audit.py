"""Phase 6I-4 tests for upstream_research_input_audit.

Pins:

  - Forbidden-imports static guard: no yfinance / dash /
    spymaster / onepass execution / impactsearch execution /
    stackbuilder execution / trafficflow execution /
    confluence runner / writer / refresher / subprocess.
  - No-age-window static guard: the audit's own source
    text MUST NOT carry "STALE_DAYS", "AGE_DAYS",
    "30 days", or "thirty days" substrings (mirrors the
    Phase 6I-1 validator's contract).
  - Valid temp fixture passes (every flag green, no
    issues, upstream_trio_ready=True).
  - Multiple StackBuilder variants are allowed (single
    variant + two clearly-staggered variants both OK).
  - Tied newest-mtime StackBuilder variants ->
    ``ambiguous_stackbuilder_selection`` blocks
    automation; primary_blocker reflects.
  - Missing OnePass target library is surfaced as
    ``missing_onepass_target_library`` and blocks the MTF
    projection prediction.
  - Missing member OnePass library is surfaced as
    ``missing_onepass_member_library``.
  - Missing member Signal Engine cache is a SEPARATE
    issue from missing member OnePass library.
  - ImpactSearch missing is reported as
    ``missing_impactsearch_artifact`` BUT by itself does
    NOT promote ``downstream_contract_invalid``.
  - Downstream contract invalid (no Confluence artifact)
    surfaces as ``downstream_contract_invalid`` +
    primary_blocker ``downstream_artifact_gap``.
  - No-writes guard: tmp_path byte-identical before/after.
  - CLI: blank ticker / no tickers / unknown flag ->
    rc=2 without SystemExit leak.
  - CLI happy path emits valid JSON, rc=0.
  - ``to_json_dict`` round-trips.
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

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import research_artifacts as ra  # noqa: E402
import upstream_research_input_audit as urai  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
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


def _safe_filename(ticker: str) -> str:
    return f"{ticker.upper().replace('^', '_')}_precomputed_results.pkl"


def _write_realistic_cache_pkl(
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
    path = cache_dir / _safe_filename(ticker)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_onepass_daily_library(
    sig_dir: Path, ticker: str,
) -> Path:
    safe = ticker.upper().replace("^", "_")
    p = sig_dir / f"{safe}_stable_v1_0_0.pkl"
    # The audit only checks file presence; the payload
    # need not match a real OnePass library shape.
    p.write_bytes(b"placeholder")
    return p


def _write_onepass_interval_libraries(
    sig_dir: Path, ticker: str,
    intervals: list[str] = ["1wk", "1mo", "3mo", "1y"],
) -> None:
    safe = ticker.upper().replace("^", "_")
    for interval in intervals:
        (sig_dir / f"{safe}_stable_v1_0_0_{interval}.pkl"
         ).write_bytes(b"placeholder")


def _write_impactsearch_xlsx(
    impact_dir: Path, ticker: str,
    *, with_manifest: bool = True,
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


def _write_stackbuilder_run(
    stack_root: Path,
    target: str,
    *,
    seed: str = "seedTC__AAA-D_BBB-D",
    members: list[str] = ["AAA", "BBB"],
    K_values: list[int] = list(range(1, 13)),
    mtime: float | None = None,
) -> Path:
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
        import os
        os.utime(run_dir, (mtime, mtime))
        os.utime(lb_path, (mtime, mtime))
    return run_dir


def _write_daily_k_artifact(
    artifact_root: Path, target: str, K: int,
    *, seed_run_id: str = "seedTC__AAA-D_BBB-D",
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
    artifact_root: Path, target: str, K: int,
    *, seed_run_id: str = "seedTC__AAA-D_BBB-D",
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
    artifact_root: Path, target: str,
    *, last_date: str = "2026-05-08",
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
) -> Path:
    safe = target.upper().replace("^", "_")
    conf_dir = artifact_root / "confluence" / safe
    conf_dir.mkdir(parents=True, exist_ok=True)
    K_values = list(range(1, 13))
    timeframes = ["1d", "1wk", "1mo", "3mo", "1y"]
    total_cells = len(K_values) * len(timeframes)
    buy_votes = 5
    short_votes = 0
    missing_votes = 0
    none_votes = (
        total_cells - buy_votes - short_votes - missing_votes
    )
    active_count = buy_votes + short_votes
    available_count = active_count + none_votes
    if buy_votes > 0 and short_votes == 0:
        agreement_active = buy_votes
    else:
        agreement_active = 0
    run_ids = [
        f"{seed_run_id}__K{k}__MTF" for k in K_values
    ]
    row = {
        "date": last_date,
        "target": target,
        "target_ticker": target,
        "target_close": 100.0,
        "target_return_pct": 0.0,
        "confluence_signal": "None",
        "signal": "None",
        "signal_value": 0,
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
    target: str = "SPY",
    *,
    members: list[str] = ["AAA", "BBB"],
    last_date: str = "2026-05-08",
    include_impactsearch: bool = True,
    include_downstream: bool = True,
) -> None:
    """Build the full upstream + downstream artifact chain
    against tmp_path. Used by tests that want every flag
    green by default, then mutate ONE input to test that
    mutation alone."""
    # Spymaster caches for target and all members.
    _write_realistic_cache_pkl(
        dirs["cache_dir"], target, last_date=last_date,
    )
    for m in members:
        _write_realistic_cache_pkl(
            dirs["cache_dir"], m, last_date=last_date,
        )
    # OnePass libraries (daily + intervals).
    _write_onepass_daily_library(
        dirs["signal_library_dir"], target,
    )
    _write_onepass_interval_libraries(
        dirs["signal_library_dir"], target,
    )
    for m in members:
        _write_onepass_daily_library(
            dirs["signal_library_dir"], m,
        )
    # ImpactSearch.
    if include_impactsearch:
        _write_impactsearch_xlsx(
            dirs["impactsearch_output_dir"], target,
        )
    # StackBuilder.
    _write_stackbuilder_run(
        dirs["stackbuilder_root"], target, members=members,
    )
    # Downstream artifact chain so Phase 6I-1 validator
    # reports contract_valid.
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
        )


def _snapshot_tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# 1. Forbidden-imports static guard
# ---------------------------------------------------------------------------


def test_audit_has_no_forbidden_imports():
    """Phase 6I-4 audit must not import any live engine,
    writer, refresher, runner, or subprocess. The
    validator + ``trafficflow_k_artifact_builder``
    (load-only helpers) + preflight (discovery /
    selection helpers) are the only allowed couplings."""
    tree = ast.parse(
        Path(urai.__file__).read_text(encoding="utf-8"),
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
        "forbidden import in upstream_research_input_audit: "
        f"{bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. No-age-window static guard
# ---------------------------------------------------------------------------


def test_audit_carries_no_stackbuilder_age_window():
    """The audit must not introduce any StackBuilder age-
    based stale rule. Phase 6H-3 contract carried forward
    verbatim by Phase 6I-1; Phase 6I-4 inherits it."""
    text = Path(urai.__file__).read_text(encoding="utf-8")
    forbidden_substrings = [
        "STACKBUILDER_AGE_DAYS",
        "STACKBUILDER_STALE_DAYS",
        "STALE_DAYS",
        "AGE_DAYS",
        "30 days",
        "thirty days",
    ]
    found = [s for s in forbidden_substrings if s in text]
    assert not found, (
        "Phase 6I-4 audit must not introduce a "
        f"StackBuilder age window; found: {found}"
    )


# ---------------------------------------------------------------------------
# 3. Full valid fixture passes every flag
# ---------------------------------------------------------------------------


def test_full_valid_fixture_passes_every_flag(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.onepass_target_library_present is True
    assert tuple(
        state.onepass_target_interval_libraries_present,
    ) == ("1wk", "1mo", "3mo", "1y")
    assert state.onepass_target_interval_libraries_missing == ()
    assert state.impactsearch_xlsx_present is True
    assert state.stackbuilder_run_count == 1
    assert state.stackbuilder_selected_run_id is not None
    assert state.stackbuilder_selection_policy == (
        "single_available_stack"
    )
    assert state.leaderboard_readable is True
    assert tuple(state.leaderboard_k_coverage) == tuple(
        range(1, 13),
    )
    assert "AAA" in state.leaderboard_members
    assert "BBB" in state.leaderboard_members
    assert state.target_signal_engine_cache_present is True
    assert state.members_missing_signal_engine_cache == ()
    assert state.members_missing_onepass_library == ()
    assert state.can_build_daily_trafficflow_k is True
    assert state.can_project_multitimeframe is True
    assert state.can_build_confluence is True
    assert state.downstream_contract_valid is True
    assert state.issue_codes == ()
    assert state.upstream_trio_ready is True
    assert state.primary_blocker == urai.BLOCKER_NONE


# ---------------------------------------------------------------------------
# 4. Multiple StackBuilder variants are allowed
# ---------------------------------------------------------------------------


def test_multiple_stackbuilder_variants_clear_newest_mtime_ok(
    tmp_path: Path,
):
    """Two saved variants with clearly-staggered mtimes:
    the audit must select the newest WITHOUT flagging
    ambiguity, AND must NOT introduce an age-based stale
    rule on the older one."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    # Write an older variant 1 hour back.
    older_mtime = time.time() - 3600
    _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedOLD__AAA-D_BBB-D",
        mtime=older_mtime,
    )
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.stackbuilder_run_count == 2
    # The clear newest by mtime is the seedTC variant
    # written in _write_full_valid_fixture (no mtime
    # override).
    assert state.stackbuilder_selected_run_id == (
        "seedTC__AAA-D_BBB-D"
    )
    assert state.stackbuilder_selection_policy == (
        "latest_mtime_existing_pipeline_default"
    )
    assert (
        urai.ISSUE_AMBIGUOUS_STACKBUILDER_SELECTION
        not in state.issue_codes
    )
    assert state.upstream_trio_ready is True


# ---------------------------------------------------------------------------
# 5. Tied newest-mtime StackBuilder blocks as ambiguous
# ---------------------------------------------------------------------------


def test_tied_newest_mtime_stackbuilder_blocks_ambiguous(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    # Force-tie the seedTC run's mtime against a second
    # variant.
    same_mtime = time.time() - 120
    seed_tc_dir = (
        dirs["stackbuilder_root"]
        / "SPY"
        / "seedTC__AAA-D_BBB-D"
    )
    import os
    os.utime(seed_tc_dir, (same_mtime, same_mtime))
    os.utime(
        seed_tc_dir / "combo_leaderboard.xlsx",
        (same_mtime, same_mtime),
    )
    _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTIED__CCC-D_DDD-D",
        mtime=same_mtime,
    )
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.stackbuilder_run_count == 2
    assert state.stackbuilder_selection_policy == (
        "ambiguous_tied_mtime"
    )
    assert (
        urai.ISSUE_AMBIGUOUS_STACKBUILDER_SELECTION
        in state.issue_codes
    )
    assert state.upstream_trio_ready is False
    assert state.primary_blocker == (
        urai.BLOCKER_UPSTREAM_AMBIGUOUS_STACKBUILDER_SELECTION
    )
    # Downstream predictions all blocked.
    assert state.can_build_daily_trafficflow_k is False
    assert state.can_project_multitimeframe is False
    assert state.can_build_confluence is False


# ---------------------------------------------------------------------------
# 6. Missing OnePass target library
# ---------------------------------------------------------------------------


def test_missing_onepass_target_library_surfaces(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    # Remove ONLY the daily library file (intervals + the
    # rest of the chain stay intact).
    target_lib = (
        dirs["signal_library_dir"] / "SPY_stable_v1_0_0.pkl"
    )
    target_lib.unlink()
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.onepass_target_library_present is False
    assert (
        urai.ISSUE_MISSING_ONEPASS_TARGET_LIBRARY
        in state.issue_codes
    )
    assert state.upstream_trio_ready is False
    assert state.primary_blocker == (
        urai.BLOCKER_UPSTREAM_MISSING_ONEPASS_TARGET_LIBRARY
    )
    # can_project_multitimeframe requires the target's
    # OnePass daily library; must be False.
    assert state.can_project_multitimeframe is False
    assert state.can_build_confluence is False


# ---------------------------------------------------------------------------
# 7. Missing member OnePass library
# ---------------------------------------------------------------------------


def test_missing_member_onepass_library_surfaces(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    # Remove AAA's OnePass library only. AAA's cache PKL
    # stays so this isolates the library-vs-cache concern.
    (dirs["signal_library_dir"]
     / "AAA_stable_v1_0_0.pkl").unlink()
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert "AAA" in state.members_missing_onepass_library
    assert (
        urai.ISSUE_MISSING_ONEPASS_MEMBER_LIBRARY
        in state.issue_codes
    )
    # The target's own library is still present; trio
    # upstream is OK; only member-library blocker fires.
    assert state.onepass_target_library_present is True
    assert state.upstream_trio_ready is True
    # AAA's cache is still present so member-cache
    # blocker MUST NOT fire (the two are separate codes).
    assert (
        urai.ISSUE_MISSING_MEMBER_SIGNAL_ENGINE_CACHE
        not in state.issue_codes
    )


# ---------------------------------------------------------------------------
# 8. Missing member Signal Engine cache (separate code)
# ---------------------------------------------------------------------------


def test_missing_member_signal_engine_cache_is_separate_code(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    # Remove AAA's cache PKL only. AAA's library stays
    # so this isolates the cache-vs-library concern.
    (dirs["cache_dir"]
     / "AAA_precomputed_results.pkl").unlink()
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert "AAA" in state.members_missing_signal_engine_cache
    assert (
        urai.ISSUE_MISSING_MEMBER_SIGNAL_ENGINE_CACHE
        in state.issue_codes
    )
    # Library-missing code MUST NOT fire (separate
    # concerns).
    assert (
        urai.ISSUE_MISSING_ONEPASS_MEMBER_LIBRARY
        not in state.issue_codes
    )
    # Member-cache gap blocks daily-K and downstream
    # predictions.
    assert state.can_build_daily_trafficflow_k is False


# ---------------------------------------------------------------------------
# 9. Missing target Signal Engine cache
# ---------------------------------------------------------------------------


def test_missing_target_signal_engine_cache_surfaces(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    (dirs["cache_dir"]
     / "SPY_precomputed_results.pkl").unlink()
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.target_signal_engine_cache_present is False
    assert (
        urai.ISSUE_MISSING_TARGET_SIGNAL_ENGINE_CACHE
        in state.issue_codes
    )
    assert state.primary_blocker == (
        urai.BLOCKER_MISSING_TARGET_SIGNAL_ENGINE_CACHE
    )
    assert state.can_build_daily_trafficflow_k is False


# ---------------------------------------------------------------------------
# 10. ImpactSearch missing reported but doesn't fake
#     Confluence failure
# ---------------------------------------------------------------------------


def test_impactsearch_missing_reports_but_does_not_fake_confluence(
    tmp_path: Path,
):
    """The audit's downstream contract verdict must come
    from the Phase 6I-1 validator -- NOT from ImpactSearch
    presence. A missing ImpactSearch artifact emits its
    own issue code but the downstream chain stays valid
    if every Phase 6D artifact is present."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, target="SPY", include_impactsearch=False,
    )
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.impactsearch_xlsx_present is False
    assert (
        urai.ISSUE_MISSING_IMPACTSEARCH_ARTIFACT
        in state.issue_codes
    )
    # But the downstream contract is still valid because
    # the Confluence artifact + chain are present.
    assert state.downstream_contract_valid is True
    assert (
        urai.ISSUE_DOWNSTREAM_CONTRACT_INVALID
        not in state.issue_codes
    )
    # Trio upstream readiness is also true -- ImpactSearch
    # is not in the upstream-trio-blocking set.
    assert state.upstream_trio_ready is True


# ---------------------------------------------------------------------------
# 11. Downstream contract invalid surfaces correctly
# ---------------------------------------------------------------------------


def test_downstream_contract_invalid_surfaces_when_chain_missing(
    tmp_path: Path,
):
    """No Confluence / MTF / daily-K artifacts written ->
    Phase 6I-1 validator returns contract-invalid ->
    audit emits ``downstream_contract_invalid`` +
    primary_blocker = downstream_artifact_gap."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, target="SPY", include_downstream=False,
    )
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.downstream_contract_valid is False
    assert (
        urai.ISSUE_DOWNSTREAM_CONTRACT_INVALID
        in state.issue_codes
    )
    assert state.primary_blocker == (
        urai.BLOCKER_DOWNSTREAM_ARTIFACT_GAP
    )
    # But the upstream trio itself IS ready; downstream
    # artifact gap is a separate axis.
    assert state.upstream_trio_ready is True


# ---------------------------------------------------------------------------
# 12. Missing StackBuilder run
# ---------------------------------------------------------------------------


def test_missing_stackbuilder_run_surfaces(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    # Wipe the entire StackBuilder tree.
    import shutil
    shutil.rmtree(dirs["stackbuilder_root"])
    dirs["stackbuilder_root"].mkdir(parents=True)
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.stackbuilder_run_count == 0
    assert (
        urai.ISSUE_MISSING_STACKBUILDER_RUN
        in state.issue_codes
    )
    assert state.primary_blocker == (
        urai.BLOCKER_UPSTREAM_MISSING_STACKBUILDER_RUN
    )
    assert state.upstream_trio_ready is False


# ---------------------------------------------------------------------------
# 13. Insufficient StackBuilder K coverage
# ---------------------------------------------------------------------------


def test_insufficient_stackbuilder_k_coverage_surfaces(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    # Write everything except StackBuilder leaderboard
    # (writing it manually with reduced K coverage).
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    for m in ("AAA", "BBB"):
        _write_realistic_cache_pkl(dirs["cache_dir"], m)
    _write_onepass_daily_library(
        dirs["signal_library_dir"], "SPY",
    )
    _write_onepass_interval_libraries(
        dirs["signal_library_dir"], "SPY",
    )
    for m in ("AAA", "BBB"):
        _write_onepass_daily_library(
            dirs["signal_library_dir"], m,
        )
    _write_impactsearch_xlsx(
        dirs["impactsearch_output_dir"], "SPY",
    )
    # Only K=1..6 instead of 1..12.
    _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        K_values=list(range(1, 7)),
    )
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert tuple(state.leaderboard_k_coverage) == tuple(
        range(1, 7),
    )
    assert (
        urai.ISSUE_INSUFFICIENT_STACKBUILDER_K_COVERAGE
        in state.issue_codes
    )
    assert state.primary_blocker == (
        urai.BLOCKER_UPSTREAM_INSUFFICIENT_STACKBUILDER_K_COVERAGE
    )
    # Confluence prediction requires full K coverage.
    assert state.can_build_confluence is False


# ---------------------------------------------------------------------------
# 14. Unreadable StackBuilder leaderboard
# ---------------------------------------------------------------------------


def test_unreadable_stackbuilder_leaderboard_surfaces(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    # Corrupt the leaderboard xlsx into garbage.
    lb_path = (
        dirs["stackbuilder_root"]
        / "SPY"
        / "seedTC__AAA-D_BBB-D"
        / "combo_leaderboard.xlsx"
    )
    lb_path.write_bytes(b"not an xlsx")
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert state.leaderboard_readable is False
    assert (
        urai.ISSUE_UNREADABLE_STACKBUILDER_LEADERBOARD
        in state.issue_codes
    )
    assert state.primary_blocker == (
        urai.BLOCKER_UPSTREAM_UNREADABLE_STACKBUILDER_LEADERBOARD
    )


# ---------------------------------------------------------------------------
# 15. No-writes guard
# ---------------------------------------------------------------------------


def test_audit_does_not_mutate_tree(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    before = _snapshot_tree(tmp_path)
    before_bytes = {p: p.read_bytes() for p in before}
    state = urai.audit_upstream_research_inputs(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    after = _snapshot_tree(tmp_path)
    assert before == after
    for p, payload in before_bytes.items():
        assert p.read_bytes() == payload
    assert state.upstream_trio_ready is True


# ---------------------------------------------------------------------------
# 16. Aggregate report counts
# ---------------------------------------------------------------------------


def test_aggregate_report_counts(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    # AAPL: missing stackbuilder.
    _write_realistic_cache_pkl(dirs["cache_dir"], "AAPL")
    _write_onepass_daily_library(
        dirs["signal_library_dir"], "AAPL",
    )
    report = urai.audit_upstream_research_inputs_many(
        ["SPY", "AAPL"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    assert report.inspected_count == 2
    assert "SPY" in report.upstream_trio_ready_tickers
    assert "AAPL" in report.blocked_tickers
    assert report.counts_by_primary_blocker.get(
        urai.BLOCKER_NONE, 0,
    ) == 1
    assert report.counts_by_primary_blocker.get(
        urai.BLOCKER_UPSTREAM_MISSING_STACKBUILDER_RUN, 0,
    ) == 1


# ---------------------------------------------------------------------------
# 17. CLI: blank / empty / unknown-flag -> rc=2
# ---------------------------------------------------------------------------


def test_cli_blank_ticker_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = urai.main(["--ticker", "   "])
    assert rc == 2
    parsed = json.loads(err.getvalue().strip())
    assert parsed.get("error") == "no_tickers_supplied"


def test_cli_no_arg_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = urai.main([])
    assert rc == 2


def test_cli_unknown_flag_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = urai.main(["--not-a-flag", "x"])
    assert rc == 2


# ---------------------------------------------------------------------------
# 18. CLI happy path emits valid JSON
# ---------------------------------------------------------------------------


def test_cli_happy_path_emits_json(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = urai.main([
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
        ])
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert parsed["inspected_count"] == 1
    assert parsed["states"][0]["ticker"] == "SPY"
    assert parsed["states"][0]["upstream_trio_ready"] is True
    # Sanity: JSON is fully serializable (no NaN/Inf).
    json.dumps(parsed)


# ---------------------------------------------------------------------------
# 19. to_json_dict round-trips
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(dirs, target="SPY")
    report = urai.audit_upstream_research_inputs_many(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    reparsed = json.loads(serialized)
    assert reparsed["states"][0]["ticker"] == "SPY"
    assert (
        reparsed["states"][0]["primary_blocker"]
        == urai.BLOCKER_NONE
    )
