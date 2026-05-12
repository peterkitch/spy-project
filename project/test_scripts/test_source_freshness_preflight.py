"""Phase 6E-2 tests for source_freshness_preflight.

Pins the preflight contract:

  - stale cache -> ``refresh_source_cache``
  - fresh cache + StackBuilder run -> ``run_pipeline_after_refresh``
  - fresh cache + StackBuilder + MTF libs + fresh Confluence
    on disk -> ``already_current``
  - missing cache -> ``missing_cache`` (or
    ``insufficient_saved_inputs``)
  - cache present but no StackBuilder ->
    ``missing_stackbuilder_run``
  - health-blocked -> ``blocked_by_health_report``
  - module has no yfinance / live engine imports
  - module performs no writes
  - CLI emits valid JSON for both ``--ticker`` and
    ``--tickers``
  - CLI rejects invalid args with rc=2 (no SystemExit leak)
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

import board_launch_readiness_audit as bla  # noqa: E402
import research_artifacts as ra  # noqa: E402
import source_freshness_preflight as sfp  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers (same shape as the launch-audit tests so the
# two test suites stay drift-aligned).
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
    import pandas as pd
    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(end=last_date, periods=n)
    df = pd.DataFrame(
        {"Close": [100.0 + i for i in range(n)]},
        index=dates,
    )
    payload = {
        "preprocessed_data": df,
        "active_pairs": ["Buy 3,2"] * n,
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


def _write_full_mtf_pipeline_outputs(
    artifact_root: Path, target: str, *,
    last_date: str,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
) -> None:
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
    conf_dir = artifact_root / "confluence" / safe
    conf_dir.mkdir(parents=True, exist_ok=True)
    art_c = ra.ResearchDayArtifact(
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
        art_c, conf_dir / f"{safe}__mtf_consensus.research_day.json",
    )


def _snapshot_tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# Forbidden imports
# ---------------------------------------------------------------------------


def test_preflight_module_has_no_forbidden_imports():
    tree = ast.parse(
        Path(sfp.__file__).read_text(encoding="utf-8"),
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
        "forbidden import in source_freshness_preflight: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# Recommended-action classification
# ---------------------------------------------------------------------------


def test_stale_cache_recommends_refresh_source_cache(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2024-01-31", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert entry.stale is True
    assert entry.cache_exists is True
    assert (
        entry.recommended_next_action
        == sfp.ACTION_REFRESH_SOURCE_CACHE
    )
    assert entry.safe_to_attempt_refresh is True
    assert entry.safe_to_run_pipeline_after_refresh is True


def test_current_cache_with_stackbuilder_recommends_pipeline(
    tmp_path: Path,
):
    """Cache last_date is one trading day past the cutoff (the
    realistic "right after market close, UTC has not yet
    rolled" window). The Phase 6D-1 persist_skip_bars=1 trim
    will land Confluence at the cutoff exactly, so a pipeline
    rerun WILL make this ticker leader-eligible. The preflight
    must recommend ``run_pipeline_after_refresh`` here -
    Phase 6G-5's persist-skip-lag override only fires when the
    cache equals the cutoff."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_cache_pkl(dirs["cache_dir"], "AAA", n=20)
    _write_cache_pkl(dirs["cache_dir"], "BBB", n=20)
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-07", **dirs,
    )
    assert entry.stale is False
    assert entry.has_stackbuilder_run is True
    assert (
        entry.recommended_next_action
        == sfp.ACTION_RUN_PIPELINE_AFTER_REFRESH
    )
    assert entry.safe_to_attempt_refresh is True
    assert entry.safe_to_run_pipeline_after_refresh is True


# ---------------------------------------------------------------------------
# Phase 6G-5: persist_skip_bars structural-lag pass-through
# ---------------------------------------------------------------------------


def test_persist_skip_lag_recommends_pipeline_output_lags_action(
    tmp_path: Path,
):
    """SPY-shape: cache last_date equals the as-of cutoff and
    the full upstream chain is in place. The launch audit emits
    ``RECOMMENDED_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP``. The
    preflight must mirror that verdict via its own action
    constant so an operator scanning the freshness preflight
    sees the same structural-lag answer."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert entry.stale is False
    assert (
        entry.board_launch_recommended_action
        == bla.RECOMMENDED_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
    )
    assert (
        entry.recommended_next_action
        == sfp.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
    )


def test_persist_skip_lag_action_is_not_safe_to_refresh_or_pipeline(
    tmp_path: Path,
):
    """Neither attempting a refresh nor running the pipeline
    today will close the persist-skip lag. The preflight's
    safety flags must reflect that honestly: both must be
    ``False`` so the operator knows the structural-lag verdict
    is not a "rerun the pipeline" suggestion."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert (
        entry.recommended_next_action
        == sfp.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
    )
    assert entry.safe_to_attempt_refresh is False
    assert entry.safe_to_run_pipeline_after_refresh is False


def test_persist_skip_lag_action_constant_is_in_preflight_actions(
):
    """The new pass-through constant must register in the
    ``PREFLIGHT_ACTIONS`` namespace so consumers that enumerate
    the preflight's action set see it."""
    assert (
        sfp.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
        in sfp.PREFLIGHT_ACTIONS
    )
    assert (
        sfp.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
        == "pipeline_output_lags_persist_skip"
    )


def test_full_pipeline_outputs_already_current(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_cache_pkl(dirs["cache_dir"], "AAA", n=20)
    _write_cache_pkl(dirs["cache_dir"], "BBB", n=20)
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    _write_full_mtf_pipeline_outputs(
        dirs["artifact_root"], "SPY", last_date="2026-05-08",
    )
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert entry.recommended_next_action == sfp.ACTION_ALREADY_CURRENT
    # Refresh is not needed for an already-current ticker.
    assert entry.safe_to_attempt_refresh is False
    # Re-running the pipeline against an already-current
    # ticker is safe (it is idempotent).
    assert entry.safe_to_run_pipeline_after_refresh is True


def test_missing_cache_recommends_missing_cache(tmp_path: Path):
    dirs = _layout(tmp_path)
    entry = sfp.evaluate_ticker_freshness(
        "GHOST", current_as_of_date="2026-05-08", **dirs,
    )
    assert entry.cache_exists is False
    assert entry.recommended_next_action in (
        sfp.ACTION_MISSING_CACHE,
        sfp.ACTION_INSUFFICIENT_SAVED_INPUTS,
    )
    # When no PKL is on disk, ``missing_cache`` is the
    # actionable label.
    assert entry.recommended_next_action == sfp.ACTION_MISSING_CACHE


def test_missing_stackbuilder_run(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    # No StackBuilder leaderboard.
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert entry.cache_exists is True
    assert entry.has_stackbuilder_run is False
    assert (
        entry.recommended_next_action
        == sfp.ACTION_MISSING_STACKBUILDER_RUN
    )
    # No refresh is the right next step when the upstream
    # gate is StackBuilder.
    assert entry.safe_to_attempt_refresh is False
    assert entry.safe_to_run_pipeline_after_refresh is False


def test_stale_source_without_stackbuilder_prioritizes_stackbuilder(
    tmp_path: Path,
):
    """Stale source + missing StackBuilder must return
    ``missing_stackbuilder_run``, NOT
    ``refresh_source_cache``. The launch audit's classifier
    short-circuits on stale source first; the preflight's
    decision tree puts structural blockers (StackBuilder,
    MTF libs) ahead of staleness so the pilot-flow halt
    rule in the Phase 6E-2 doc remains honest."""
    dirs = _layout(tmp_path)
    # Source is a year stale AND there is no StackBuilder
    # run anywhere - the SNOW shape we saw on the real-cache
    # smoke.
    _write_cache_pkl(
        dirs["cache_dir"], "SNOW",
        last_date="2024-01-31", n=20,
    )
    entry = sfp.evaluate_ticker_freshness(
        "SNOW", current_as_of_date="2026-05-08", **dirs,
    )
    assert entry.stale is True
    assert entry.has_stackbuilder_run is False
    assert (
        entry.recommended_next_action
        == sfp.ACTION_MISSING_STACKBUILDER_RUN
    )
    assert entry.safe_to_attempt_refresh is False
    # Refresh alone does not make this ticker safe to run
    # through the pipeline.
    assert entry.safe_to_run_pipeline_after_refresh is False


def test_stale_source_without_mtf_libs_routes_to_manual_review(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2024-01-31", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    # No multi-timeframe libraries on disk.
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert entry.recommended_next_action == sfp.ACTION_MANUAL_REVIEW
    assert entry.safe_to_attempt_refresh is False
    assert entry.safe_to_run_pipeline_after_refresh is False


def test_health_blocked(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    _write_health_report(
        dirs["artifact_root"],
        blocked_targets={"SPY": ["confluence"]},
    )
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert (
        entry.recommended_next_action
        == sfp.ACTION_BLOCKED_BY_HEALTH_REPORT
    )
    assert entry.safe_to_attempt_refresh is False
    assert entry.safe_to_run_pipeline_after_refresh is False


def test_board_launch_action_field_round_trips(tmp_path: Path):
    """The preflight must expose the launch audit's own
    classification so an operator can cross-reference the two
    tools without re-running the audit by hand."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2024-01-31", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    entry = sfp.evaluate_ticker_freshness(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert (
        entry.board_launch_recommended_action
        == bla.RECOMMENDED_NEEDS_FRESH_SOURCE_CACHE
    )


# ---------------------------------------------------------------------------
# No writes
# ---------------------------------------------------------------------------


def test_preflight_does_not_write(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    before_artifact = _snapshot_tree(dirs["artifact_root"])
    before_cache = _snapshot_tree(dirs["cache_dir"])
    before_stack = _snapshot_tree(dirs["stackbuilder_root"])
    sfp.build_source_freshness_preflight(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    assert _snapshot_tree(dirs["artifact_root"]) == before_artifact
    assert _snapshot_tree(dirs["cache_dir"]) == before_cache
    assert _snapshot_tree(dirs["stackbuilder_root"]) == before_stack


# ---------------------------------------------------------------------------
# Aggregate report shape
# ---------------------------------------------------------------------------


def test_build_report_counts_and_pilot_shape(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2024-01-31", n=20,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    report = sfp.build_source_freshness_preflight(
        ["SPY", "GHOST"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    assert report.inspected_count == 2
    assert sum(
        report.counts_by_recommended_action.values(),
    ) == 2
    assert (
        report.counts_by_recommended_action.get(
            sfp.ACTION_REFRESH_SOURCE_CACHE,
        ) == 1
    )
    assert (
        report.counts_by_recommended_action.get(
            sfp.ACTION_MISSING_CACHE,
        ) == 1
    )


def test_to_json_dict_is_serializable(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    report = sfp.build_source_freshness_preflight(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    payload = json.dumps(report.to_json_dict())
    assert "SPY" in payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_ticker_single_emits_json(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    argv = [
        "--ticker", "SPY",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = sfp.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 1
    assert payload["candidates"][0]["ticker"] == "SPY"


def test_cli_tickers_csv_emits_json(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_cache_pkl(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-08", n=20,
    )
    _write_cache_pkl(
        dirs["cache_dir"], "AAPL",
        last_date="2026-05-08", n=20,
    )
    argv = [
        "--tickers", "SPY,AAPL",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = sfp.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    tickers = [c["ticker"] for c in payload["candidates"]]
    assert tickers == ["SPY", "AAPL"]


def test_cli_unknown_flag_returns_2_without_system_exit(capsys):
    try:
        rc = sfp.main(["--definitely-not-a-flag"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on unknown flag; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_mutually_exclusive_ticker_args_return_2(capsys):
    try:
        rc = sfp.main(["--ticker", "SPY", "--tickers", "AAPL,GOOG"])
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
    ]
    rc = sfp.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 0
    assert payload["candidates"] == []
    # Helpful note for the operator.
    assert any("supply --ticker" in n for n in payload["notes"])
