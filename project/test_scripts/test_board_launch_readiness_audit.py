"""Phase 6E-1 tests for board_launch_readiness_audit.

Pins the launch-readiness audit contract:

  - explicit ticker list limits inspection to those tickers
  - cache discovery respects max_tickers
  - no production writes occur during the audit
  - full + fresh temp fixture is recommended
    ``ready_for_pipeline_write`` (or
    ``already_leader_eligible`` when a fresh Confluence is
    already saved)
  - stale source cache is recommended
    ``needs_fresh_source_cache``
  - missing StackBuilder run is recommended
    ``needs_stackbuilder_run``
  - missing multi-timeframe libraries is recommended
    ``needs_multitimeframe_libraries``
  - health-blocked ticker is recommended
    ``blocked_by_health_report``
  - counts_by_recommended_action add up
  - CLI emits valid JSON, defaults to dry-run, validates args
    without raising SystemExit
  - module has no yfinance / live engine imports
"""
from __future__ import annotations

import ast
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import board_launch_readiness_audit as audit  # noqa: E402
import confluence_pipeline_readiness as cpr  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
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


def _write_cache_pkl(
    cache_dir: Path, ticker: str, *,
    last_date: str = "2026-05-08",
    n: int = 20,
) -> Path:
    """Write a minimal Spymaster cache PKL with a known
    date_range so the audit's staleness probe is testable."""
    import pandas as pd
    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(end=last_date, periods=n)
    df = pd.DataFrame(
        {"Close": [100.0 + i for i in range(n)]},
        index=dates,
    )
    active_pairs = ["Buy 3,2"] * n
    payload = {
        "preprocessed_data": df,
        "active_pairs": active_pairs,
    }
    safe = ticker.replace("^", "_")
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_stackbuilder_run(
    stack_root: Path, target: str, *,
    seed: str = "seedTC__AAA-D_BBB-D",
    members_str: str = "['AAA[D]', 'BBB[D]']",
) -> Path:
    """Write a minimal combo_leaderboard.xlsx covering K=1..12."""
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
    for interval in intervals:
        (sig_dir / f"{ticker}_stable_v1_0_0_{interval}.pkl"
         ).write_bytes(b"placeholder")


def _write_health_report(
    artifact_root: Path,
    *,
    blocked_targets: dict[str, list[str]],
) -> Path:
    by_target = [
        {"target_ticker": t, "engines_blocked": engines}
        for t, engines in blocked_targets.items()
    ]
    payload = {
        "schema": "catalogue_health_v1",
        "generated_at": "2026-05-11T00:00:00+00:00",
        "by_target": by_target,
    }
    p = artifact_root / "catalogue_health_report.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _write_confluence_artifact(
    artifact_root: Path,
    *,
    target: str,
    last_date: str,
) -> Path:
    """Write a Confluence research_day_v1 artifact directly so
    the audit's already_leader_eligible test can verify the
    happy path without going through the full pipeline."""
    safe = target.replace("^", "_")
    target_dir = artifact_root / "confluence" / safe
    target_dir.mkdir(parents=True, exist_ok=True)
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
    return ra.write_research_day_artifact(
        art, target_dir / f"{safe}__mtf_consensus.research_day.json",
    )


def _write_full_mtf_pipeline_outputs(
    artifact_root: Path,
    target: str,
    *,
    last_date: str,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
) -> None:
    """Write the full Phase 6D-2 MTF set + Phase 6D-3
    Confluence artifact so the readiness verdict can clear
    every gate."""
    safe = target.replace("^", "_")
    tf_dir = artifact_root / "trafficflow" / safe
    tf_dir.mkdir(parents=True, exist_ok=True)
    for k in range(1, 13):
        art = ra.ResearchDayArtifact(
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
            art,
            tf_dir / f"{seed_run_id}__K{k}__MTF.research_day.json",
        )
    _write_confluence_artifact(
        artifact_root, target=target, last_date=last_date,
    )


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_audit_module_has_no_forbidden_imports():
    tree = ast.parse(
        Path(audit.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance", "trafficflow", "spymaster", "impactsearch",
        "onepass", "confluence", "cross_ticker_confluence",
        "dash", "daily_signal_board",
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
        "forbidden import in board_launch_readiness_audit: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# 2. Discovery + max_tickers
# ---------------------------------------------------------------------------


def test_explicit_ticker_list_limits_inspection(tmp_path: Path):
    dirs = _layout(tmp_path)
    # Cache contains many tickers, but the explicit list
    # restricts the audit to just SPY and AAPL.
    for t in ("SPY", "AAPL", "MSFT", "GOOG"):
        _write_cache_pkl(dirs["cache_dir"], t)
    report = audit.build_launch_pilot_manifest(
        tickers=["SPY", "AAPL"],
        include_dry_run=False,
        **dirs,
    )
    assert report.inspected_count == 2
    tickers = {c.ticker for c in report.candidates}
    assert tickers == {"SPY", "AAPL"}


def test_cache_discovery_respects_max_tickers(tmp_path: Path):
    dirs = _layout(tmp_path)
    for t in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        _write_cache_pkl(dirs["cache_dir"], t)
    report = audit.build_launch_pilot_manifest(
        max_tickers=2, include_dry_run=False, **dirs,
    )
    assert report.inspected_count == 2


def test_default_discovery_caps_at_50(tmp_path: Path):
    dirs = _layout(tmp_path)
    for i in range(75):
        _write_cache_pkl(dirs["cache_dir"], f"T{i:03d}")
    # Default max_tickers = 50 per the audit's launch-vs-sweep
    # contract.
    report = audit.build_launch_pilot_manifest(
        include_dry_run=False, **dirs,
    )
    assert report.inspected_count == audit.DEFAULT_MAX_TICKERS
    # And the notes mention the cap so the operator knows to
    # ask for more if they want a wider audit.
    assert any("max_tickers" in n for n in report.notes)


# ---------------------------------------------------------------------------
# 3. No production writes
# ---------------------------------------------------------------------------


def test_audit_does_not_write_to_artifact_root(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    audit.build_launch_pilot_manifest(
        tickers=["SPY"], include_dry_run=True, **dirs,
    )
    # No artifacts persisted anywhere under artifact_root.
    files = list(
        dirs["artifact_root"].rglob("*.research_day.json"),
    )
    assert files == [], (
        f"audit must be read-only; found {files}"
    )


# ---------------------------------------------------------------------------
# 4. Recommended-action classification
# ---------------------------------------------------------------------------


def test_full_fresh_inputs_recommend_ready_for_pipeline_write(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_cache_pkl(dirs["cache_dir"], "AAA", n=20)
    _write_cache_pkl(dirs["cache_dir"], "BBB", n=20)
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY",
        ["1wk", "1mo"],
    )
    entry = audit.audit_ticker_for_launch(
        "SPY", current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    assert entry.has_signal_engine_cache is True
    assert entry.has_stackbuilder_run is True
    assert entry.stale is False
    assert entry.recommended_action == (
        audit.RECOMMENDED_READY_FOR_PIPELINE_WRITE
    )
    assert entry.can_run_pipeline_now is True


def test_already_leader_eligible_recommendation(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_cache_pkl(dirs["cache_dir"], "AAA", n=20)
    _write_cache_pkl(dirs["cache_dir"], "BBB", n=20)
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY",
        ["1wk", "1mo"],
    )
    _write_full_mtf_pipeline_outputs(
        dirs["artifact_root"], "SPY",
        last_date="2026-05-08",
    )
    entry = audit.audit_ticker_for_launch(
        "SPY", current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    assert entry.current_leader_eligible is True
    assert entry.recommended_action == (
        audit.RECOMMENDED_ALREADY_LEADER_ELIGIBLE
    )


def test_stale_source_recommends_needs_fresh_source_cache(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    # Source cache is from a year before the audit cutoff.
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2024-01-31", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY",
        ["1wk", "1mo"],
    )
    entry = audit.audit_ticker_for_launch(
        "SPY", current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    assert entry.stale is True
    assert entry.recommended_action == (
        audit.RECOMMENDED_NEEDS_FRESH_SOURCE_CACHE
    )


def test_missing_stackbuilder_run_recommendation(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    # No StackBuilder leaderboard.
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY",
        ["1wk", "1mo"],
    )
    entry = audit.audit_ticker_for_launch(
        "SPY", current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    assert entry.has_signal_engine_cache is True
    assert entry.has_stackbuilder_run is False
    assert entry.recommended_action == (
        audit.RECOMMENDED_NEEDS_STACKBUILDER_RUN
    )


def test_missing_multitimeframe_libs_recommendation(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    # No multi-timeframe libraries written.
    entry = audit.audit_ticker_for_launch(
        "SPY", current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    assert entry.has_stackbuilder_run is True
    assert entry.recommended_action == (
        audit.RECOMMENDED_NEEDS_MULTITIMEFRAME_LIBRARIES
    )


def test_health_blocked_recommendation(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY",
        ["1wk", "1mo"],
    )
    _write_health_report(
        dirs["artifact_root"],
        blocked_targets={"SPY": ["confluence"]},
    )
    entry = audit.audit_ticker_for_launch(
        "SPY", current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    assert entry.recommended_action == (
        audit.RECOMMENDED_BLOCKED_BY_HEALTH_REPORT
    )


def test_no_cache_at_all_recommends_insufficient_saved_inputs(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    entry = audit.audit_ticker_for_launch(
        "GHOST", include_dry_run=False, **dirs,
    )
    assert entry.has_signal_engine_cache is False
    assert entry.recommended_action == (
        audit.RECOMMENDED_INSUFFICIENT_SAVED_INPUTS
    )


# ---------------------------------------------------------------------------
# 5. Counts + pilot list aggregation
# ---------------------------------------------------------------------------


def test_counts_by_recommended_action_add_up(tmp_path: Path):
    dirs = _layout(tmp_path)
    # One ticker fully ready, one with no inputs.
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_cache_pkl(dirs["cache_dir"], "AAA", n=20)
    _write_cache_pkl(dirs["cache_dir"], "BBB", n=20)
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY",
        ["1wk", "1mo"],
    )
    report = audit.build_launch_pilot_manifest(
        tickers=["SPY", "GHOST"],
        current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    assert sum(
        report.counts_by_recommended_action.values(),
    ) == 2
    # SPY -> ready_for_pipeline_write,
    # GHOST -> insufficient_saved_inputs.
    assert (
        report.counts_by_recommended_action.get(
            audit.RECOMMENDED_READY_FOR_PIPELINE_WRITE,
        ) == 1
    )
    assert (
        report.counts_by_recommended_action.get(
            audit.RECOMMENDED_INSUFFICIENT_SAVED_INPUTS,
        ) == 1
    )
    # Pilot manifest = ready / already-eligible tickers only.
    assert report.recommended_pilot_tickers == ("SPY",)


def test_to_json_dict_is_json_serializable(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    report = audit.build_launch_pilot_manifest(
        tickers=["SPY"],
        current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    d = report.to_json_dict()
    s = json.dumps(d)
    assert "SPY" in s
    assert "candidates" in d
    assert isinstance(d["candidates"][0]["ticker"], str)


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


def test_cli_returns_valid_json(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    argv = [
        "--tickers", "SPY",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
        "--no-dry-run",
    ]
    rc = audit.main(argv)
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "candidates" in payload
    assert payload["inspected_count"] == 1


def test_cli_invalid_max_tickers_returns_2_without_system_exit(
    capsys,
):
    rc = None
    try:
        rc = audit.main([
            "--max-tickers", "-5",
        ])
    except SystemExit as exc:
        pytest.fail(
            "main() raised SystemExit on invalid --max-tickers; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_unknown_flag_returns_2_without_system_exit(capsys):
    rc = None
    try:
        rc = audit.main(["--definitely-not-a-flag"])
    except SystemExit as exc:
        pytest.fail(
            "main() raised SystemExit on unknown flag; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_empty_run_returns_0(tmp_path: Path, capsys):
    """No --tickers and an empty cache dir -> inspected_count=0
    but the audit completes structurally."""
    dirs = _layout(tmp_path)
    argv = [
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--no-dry-run",
    ]
    rc = audit.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 0
    assert payload["candidates"] == []


# ---------------------------------------------------------------------------
# 7. Likely-after-run prediction
# ---------------------------------------------------------------------------


def test_likely_after_run_clears_runner_owned_codes_on_fresh_inputs(
    tmp_path: Path,
):
    """When can_run_pipeline_now is True and source is fresh,
    the likely_after_run set should EXCLUDE the issue codes
    the runner is documented to clear: insufficient_K,
    missing_bridge, missing_confluence."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_cache_pkl(dirs["cache_dir"], "AAA", n=20)
    _write_cache_pkl(dirs["cache_dir"], "BBB", n=20)
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY",
        ["1wk", "1mo"],
    )
    entry = audit.audit_ticker_for_launch(
        "SPY", current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    after = set(entry.likely_after_run_issue_codes)
    for cleared in (
        cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE,
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
    ):
        assert cleared not in after, (
            f"likely_after_run leaked runner-cleared code {cleared}"
        )


def test_likely_after_run_keeps_stale_when_source_is_stale(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2024-01-31", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY",
        ["1wk", "1mo"],
    )
    entry = audit.audit_ticker_for_launch(
        "SPY", current_as_of_date="2026-05-08",
        include_dry_run=False, **dirs,
    )
    after = set(entry.likely_after_run_issue_codes)
    assert (
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT in after
    ), (
        "the audit must predict stale_confluence after a "
        "would-be runner pass when the source is stale"
    )
