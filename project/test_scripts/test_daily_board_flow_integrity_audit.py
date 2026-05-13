"""Phase 6I-10 tests for daily_board_flow_integrity_audit.

Pins:

  - Forbidden-imports static guard (writer / refresher /
    pipeline runner / live engines / yfinance /
    subprocess).
  - No-StackBuilder-age-window static guard.
  - CLI mutual exclusion + rc=0/2/3 + no SystemExit
    leak.
  - Report JSON shape + stable keys.
  - Stage-check aggregation: all pass -> all_passed True.
  - One failed stage -> all_passed False.
  - Advisory commands remain strings only.
  - Positive / negative / low_buy tails preserved.
  - Writer static audit catches missing validator
    marker if removed.
  - Temp-root rehearsal with production root snapshots
    unchanged.
"""
from __future__ import annotations

import ast
import io
import json
import pickle
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import daily_board_flow_integrity_audit as audit  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers (mirror Phase 6I-4/-5/-6/-8 patterns)
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
    path = cache_dir / _safe_filename(ticker)
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
) -> None:
    import pandas as pd

    safe = ticker.upper().replace("^", "_")
    p = impact_dir / f"{safe}_analysis.xlsx"
    pd.DataFrame({
        "Primary Ticker": [ticker.upper()],
        "Resolved/Fetched": [ticker.upper()],
    }).to_excel(p, index=False)


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
            "buy_count": 1, "short_count": 0,
            "none_count": 0, "missing_count": 0,
            "active_count": 1, "available_count": 1,
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
) -> Path:
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
        metric_basis="Close",
        persist_skip_bars=0,
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
        buy_votes=buy_votes, short_votes=short_votes,
    )


# ---------------------------------------------------------------------------
# 1. Forbidden-imports static guard
# ---------------------------------------------------------------------------


def test_audit_module_has_no_forbidden_imports():
    tree = ast.parse(
        Path(audit.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
        "yfinance",
        "dash",
        "spymaster",       # full server, not the helper
        "trafficflow",
        "stackbuilder",
        "onepass",
        "impactsearch",
        "confluence",
        "cross_ticker_confluence",
        "daily_signal_board",
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
        f"forbidden import in audit: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. No StackBuilder age-window LOGIC introduced by the
#    audit module
# ---------------------------------------------------------------------------


def test_audit_does_not_define_age_window_constants():
    """The audit module legitimately mentions the
    age-window substrings as DETECTION strings (it
    scans the writer's source for them); we cannot
    forbid those literals from its own source.

    Instead we check that the audit doesn't DEFINE any
    age-window constant of its own at module top-level
    (variable assignment with one of the canonical
    names). The Phase 6I-4 audit's own
    forbidden-substring guard against its own source
    continues to cover that module's contract; the
    Phase 6I-9 supervised gate's static guard covers
    its own source; etc. The audit module exists to
    enforce these elsewhere."""
    text = Path(audit.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    forbidden_names = {
        "STACKBUILDER_AGE_DAYS",
        "STACKBUILDER_STALE_DAYS",
    }
    defined_at_top_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id in forbidden_names:
                        defined_at_top_level.append(
                            target.id,
                        )
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name):
                if target.id in forbidden_names:
                    defined_at_top_level.append(target.id)
    assert not defined_at_top_level, (
        f"audit module defines forbidden age-window "
        f"constants: {defined_at_top_level!r}"
    )


# ---------------------------------------------------------------------------
# 3. CLI rc=0/2/3 and mutual exclusion
# ---------------------------------------------------------------------------


def test_cli_no_ticker_source_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = audit.main([])
    assert rc == 2
    parsed = json.loads(err.getvalue().strip())
    assert parsed.get("error") == "no_ticker_source_supplied"


def test_cli_unknown_flag_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = audit.main(["--not-a-flag", "x"])
    assert rc == 2


def test_cli_mutual_exclusion_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = audit.main([
            "--ticker", "SPY",
            "--from-stackbuilder-universe",
        ])
    assert rc == 2


def test_cli_happy_path_emits_json(tmp_path: Path):
    """End-to-end CLI smoke against tmp_path. The audit
    runs without writes; the writer-static + spymaster-
    helper stages always pass; the upstream / contract /
    ranking / queue+gate stages run against empty tmp
    roots and short-circuit. JSON to stdout, rc=0."""
    dirs = _layout(tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = audit.main([
            "--ticker", "ZZZ",
            "--cache-dir", str(dirs["cache_dir"]),
            "--artifact-root", str(dirs["artifact_root"]),
            "--stackbuilder-root",
            str(dirs["stackbuilder_root"]),
            "--signal-library-dir",
            str(dirs["signal_library_dir"]),
            "--impactsearch-output-dir",
            str(dirs["impactsearch_output_dir"]),
            "--current-as-of-date", "2026-05-08",
        ])
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert parsed["tickers"] == ["ZZZ"]
    # Empty-fixture run: writer-static + spymaster-helper
    # stages pass; upstream/contract/ranking/queue+gate
    # have nothing to inspect but still pass (they have
    # well-formed empty reports).
    assert "stage_checks" in parsed


# ---------------------------------------------------------------------------
# 4. Report JSON shape + stable keys
# ---------------------------------------------------------------------------


def test_report_json_shape_has_stable_keys(tmp_path: Path):
    dirs = _layout(tmp_path)
    report = audit.run_daily_board_flow_integrity_audit(
        tickers=["ZZZ"],
        current_as_of_date="2026-05-08",
        snapshot_production_roots=False,
        **dirs,
    )
    payload = report.to_json_dict()
    required_keys = {
        "generated_at",
        "current_as_of_date",
        "tickers",
        "stage_checks",
        "all_read_only_checks_passed",
        "production_roots_untouched",
        "upstream_summary",
        "contract_summary",
        "ranking_summary",
        "queue_summary",
        "gate_summary",
        "writer_static_summary",
        "spymaster_audit_summary",
        "known_simulated_or_inferred_steps",
        "recommended_next_evidence_step",
        "safe_to_consider_authorized_run_after_review",
    }
    missing = required_keys - set(payload.keys())
    assert not missing, (
        f"report JSON missing required keys: {missing!r}"
    )
    # Serializable round-trip.
    reparsed = json.loads(json.dumps(payload))
    assert reparsed["tickers"] == ["ZZZ"]


# ---------------------------------------------------------------------------
# 5. All stages pass on a full valid fixture
# ---------------------------------------------------------------------------


def test_full_valid_fixture_all_stages_pass(
    tmp_path: Path,
):
    """Full upstream + downstream chain in tmp_path.
    Every stage passes; aggregate flag True;
    production roots untouched."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY",
        cache_last_date="2026-05-12",  # ahead of cutoff
        last_date="2026-05-08",
        buy_votes=40, short_votes=5,
    )
    report = audit.run_daily_board_flow_integrity_audit(
        tickers=["SPY"],
        current_as_of_date="2026-05-08",
        snapshot_production_roots=True,
        **dirs,
    )
    assert report.all_read_only_checks_passed is True
    assert report.production_roots_untouched is True
    for s in report.stage_checks:
        assert s.passed, (
            f"stage {s.stage} failed: {s.detail} "
            f"issues={s.issue_codes}"
        )


# ---------------------------------------------------------------------------
# 6. One failed stage -> aggregate flag False
# ---------------------------------------------------------------------------


def test_failed_stage_flips_aggregate_flag(
    monkeypatch, tmp_path: Path,
):
    """Inject a stage failure by monkeypatching the
    writer-static helper to return a failing
    StageCheck."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY",
        cache_last_date="2026-05-12",
        last_date="2026-05-08",
    )

    def fake_writer_static(*a, **k):
        return (
            audit.StageCheck(
                stage=audit.STAGE_WRITER_STATIC,
                passed=False,
                detail="injected failure",
                issue_codes=("injected_failure",),
                notes=(),
            ),
            {"injected": True},
        )

    monkeypatch.setattr(
        audit, "_stage_writer_static", fake_writer_static,
    )
    report = audit.run_daily_board_flow_integrity_audit(
        tickers=["SPY"],
        current_as_of_date="2026-05-08",
        snapshot_production_roots=False,
        **dirs,
    )
    assert report.all_read_only_checks_passed is False
    assert (
        report.safe_to_consider_authorized_run_after_review
        is False
    )
    failed = [
        s for s in report.stage_checks if not s.passed
    ]
    assert len(failed) == 1
    assert failed[0].stage == audit.STAGE_WRITER_STATIC
    assert "injected_failure" in failed[0].issue_codes
    # The recommended next step should NOT be the
    # supervised-run path when a stage failed.
    assert "Resolve the failing read-only checks" in (
        report.recommended_next_evidence_step
    )


# ---------------------------------------------------------------------------
# 7. Advisory commands strings only
# ---------------------------------------------------------------------------


def test_advisory_commands_strings_only(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY",
        cache_last_date="2026-05-12",
        last_date="2026-05-08",
    )
    report = audit.run_daily_board_flow_integrity_audit(
        tickers=["SPY"],
        current_as_of_date="2026-05-08",
        snapshot_production_roots=False,
        **dirs,
    )
    # The queue_and_gate stage's issue_codes carry the
    # 'advisory_command_not_a_string' check; if any
    # advisory command was not a string, the stage
    # would have failed. Confirm the stage passed.
    qg_check = [
        s for s in report.stage_checks
        if s.stage == audit.STAGE_QUEUE_AND_GATE
    ][0]
    assert qg_check.passed is True
    assert (
        "advisory_command_not_a_string"
        not in qg_check.issue_codes
    )


# ---------------------------------------------------------------------------
# 8. Positive / negative / low_buy tails preserved
# ---------------------------------------------------------------------------


def test_ranking_tails_preserved(tmp_path: Path):
    dirs = _layout(tmp_path)
    # Buy-heavy fixture: positive_tail should carry SPY.
    _write_full_valid_fixture(
        dirs, "SPY",
        cache_last_date="2026-05-12",
        last_date="2026-05-08",
        buy_votes=40, short_votes=5,
    )
    report = audit.run_daily_board_flow_integrity_audit(
        tickers=["SPY"],
        current_as_of_date="2026-05-08",
        snapshot_production_roots=False,
        **dirs,
    )
    ranking_summary = report.ranking_summary
    assert ranking_summary["positive_tail_count"] >= 1
    # Tail counts in queue stage match.
    gate_summary = report.gate_summary
    assert (
        gate_summary["positive_tail_count"]
        == ranking_summary["positive_tail_count"]
    )


# ---------------------------------------------------------------------------
# 9. Writer static audit catches missing validator marker
# ---------------------------------------------------------------------------


def test_writer_static_audit_catches_missing_marker(
    tmp_path: Path,
):
    """Write a faux writer source that's missing the
    Phase 6I-8 CONTRACT_VALIDATOR_FUNCTION_MARKER. The
    static-audit helper must fail the stage AND set the
    appropriate issue code."""
    faux_writer = tmp_path / "faux_writer.py"
    faux_writer.write_text(
        '"""Faux writer for the missing-marker '
        'regression test."""\n'
        'ENV_VAR_NAME = "PRJCT9_AUTOMATION_WRITE_AUTH"\n'
        'ENV_VAR_REQUIRED_VALUE = "phase_6h5_explicit"\n'
        'FINAL_PIPELINE_EXECUTED_CONTRACT_INVALID = '
        '"pipeline_executed_contract_invalid"\n'
        'FINAL_REFRESH_THEN_PIPELINE_EXECUTED_CONTRACT_INVALID = '
        '"refresh_then_pipeline_executed_contract_invalid"\n'
        'ISSUE_POST_PIPELINE_CONTRACT_INVALID = '
        '"post_pipeline_contract_invalid"\n'
        'ISSUE_POST_PIPELINE_CONTRACT_VALIDATION_EXCEPTION = '
        '"post_pipeline_contract_validation_exception"\n'
        'def _default_contract_validator_callable(): pass\n'
        # NOTE: CONTRACT_VALIDATOR_FUNCTION_MARKER is
        # DELIBERATELY OMITTED to exercise the static
        # audit's catch.
        ,
        encoding="utf-8",
    )
    check, summary = audit._stage_writer_static(
        writer_source_path=faux_writer,
    )
    assert check.passed is False
    assert (
        "writer_required_token_missing"
        in check.issue_codes
    )
    assert (
        "CONTRACT_VALIDATOR_FUNCTION_MARKER"
        in summary["missing_required_tokens"]
    )


def test_writer_static_audit_catches_forbidden_import(
    tmp_path: Path,
):
    """Faux writer that imports yfinance at the top
    level -- writer static audit must catch this."""
    faux_writer = tmp_path / "faux_writer.py"
    faux_writer.write_text(
        '"""Faux writer with a forbidden top-level '
        'import."""\n'
        'import yfinance\n'  # forbidden
        'ENV_VAR_NAME = "x"\n'
        'ENV_VAR_REQUIRED_VALUE = "x"\n'
        'phase_6h5_explicit = "x"\n'
        'CONTRACT_VALIDATOR_FUNCTION_MARKER = "x"\n'
        '_default_contract_validator_callable = None\n'
        'FINAL_PIPELINE_EXECUTED_CONTRACT_INVALID = "x"\n'
        'FINAL_REFRESH_THEN_PIPELINE_EXECUTED_CONTRACT_INVALID = "x"\n'
        'ISSUE_POST_PIPELINE_CONTRACT_INVALID = "x"\n'
        'ISSUE_POST_PIPELINE_CONTRACT_VALIDATION_EXCEPTION = "x"\n',
        encoding="utf-8",
    )
    check, summary = audit._stage_writer_static(
        writer_source_path=faux_writer,
    )
    assert check.passed is False
    assert (
        "writer_forbidden_top_level_import"
        in check.issue_codes
    )
    assert "yfinance" in (
        summary["forbidden_top_level_imports_present"]
    )


# ---------------------------------------------------------------------------
# 10. Temp-root rehearsal with production root snapshots
#     unchanged
# ---------------------------------------------------------------------------


def test_temp_root_rehearsal_production_roots_untouched(
    tmp_path: Path,
):
    """Run the audit against a full tmp fixture; the
    production-roots before/after snapshots must match
    (audit module does not write under cache/, output/,
    signal_library/, stackbuilder/)."""
    dirs = _layout(tmp_path)
    _write_full_valid_fixture(
        dirs, "SPY",
        cache_last_date="2026-05-12",
        last_date="2026-05-08",
    )
    # Snapshot real production roots ourselves to
    # double-check the audit's verdict (and to confirm
    # the audit's snapshot helper is doing real work).
    project_dir = Path(audit.__file__).resolve().parent
    production_roots = (
        project_dir / "cache" / "results",
        project_dir / "cache" / "status",
        project_dir / "output" / "research_artifacts",
        project_dir / "signal_library" / "data" / "stable",
        project_dir / "output" / "stackbuilder",
    )
    before = {
        str(p): audit._snapshot_root(p)
        for p in production_roots
    }
    report = audit.run_daily_board_flow_integrity_audit(
        tickers=["SPY"],
        current_as_of_date="2026-05-08",
        snapshot_production_roots=True,
        **dirs,
    )
    after = {
        str(p): audit._snapshot_root(p)
        for p in production_roots
    }
    assert before == after, (
        "audit run mutated production roots: "
        "this is a hard safety violation"
    )
    assert report.production_roots_untouched is True


# ---------------------------------------------------------------------------
# 11. Empty-input safety: missing tickers -> all stages
#     pass with empty summaries
# ---------------------------------------------------------------------------


def test_empty_ticker_list_produces_well_formed_report(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    report = audit.run_daily_board_flow_integrity_audit(
        tickers=[],
        from_stackbuilder_universe=False,
        current_as_of_date="2026-05-08",
        snapshot_production_roots=False,
        **dirs,
    )
    assert report.tickers == ()
    # All stages report passed-with-nothing-to-inspect.
    for s in report.stage_checks:
        assert s.passed
    # The aggregate flag is True (no failing stage), BUT
    # safe_to_consider_authorized_run_after_review
    # depends on gate.safe_to_authorize_writer_now,
    # which is False on empty input. So:
    assert (
        report.safe_to_consider_authorized_run_after_review
        is False
    )


# ---------------------------------------------------------------------------
# 12. Known-simulated-steps list is non-empty
# ---------------------------------------------------------------------------


def test_known_simulated_steps_list_is_populated(
    tmp_path: Path,
):
    """The audit always names the surfaces it cannot
    prove against real production. This list must
    remain non-empty until the supervised first
    authorized run lands."""
    dirs = _layout(tmp_path)
    report = audit.run_daily_board_flow_integrity_audit(
        tickers=["ZZZ"],
        current_as_of_date="2026-05-08",
        snapshot_production_roots=False,
        **dirs,
    )
    simulated = report.known_simulated_or_inferred_steps
    assert len(simulated) >= 4
    # Specific names that must appear.
    assert (
        audit.SIMULATED_REAL_AUTHORIZED_WRITER_RUN
        in simulated
    )
    assert (
        audit.SIMULATED_REAL_YFINANCE_FETCH
        in simulated
    )


# ---------------------------------------------------------------------------
# 13. Static guard on the seven downstream-consumer
#     modules' top-level imports
# ---------------------------------------------------------------------------


def test_seven_downstream_modules_have_no_forbidden_top_imports():
    """Phase 6I-10 prompt Section A: the seven
    downstream modules must NOT carry forbidden
    live/write top-level imports."""
    project_dir = Path(audit.__file__).resolve().parent
    targets = (
        "daily_board_supervised_run_gate.py",
        "daily_board_execution_queue_planner.py",
        "daily_board_universe_planner.py",
        "upstream_research_input_audit.py",
        "confluence_ranking_emitter.py",
        "confluence_ranking_contract_validator.py",
        "spymaster_master_audit.py",
    )
    forbidden = {
        "yfinance",
        "subprocess",
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
    }
    violations: dict[str, list[str]] = {}
    for name in targets:
        path = project_dir / name
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top_level_imports: list[str] = []
        for node in tree.body:
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
        if bad:
            violations[name] = bad
    assert not violations, (
        "downstream modules carry forbidden top-level "
        f"imports: {violations!r}"
    )
