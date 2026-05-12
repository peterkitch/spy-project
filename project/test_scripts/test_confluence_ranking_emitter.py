"""Phase 6I-3 tests for confluence_ranking_emitter.

Pins:

  - Forbidden-imports static guard (no yfinance / dash /
    live engine / writer / refresher / runner / subprocess).
  - Valid fixture with 12 K x 5 timeframes produces
    expected_cell_count = 60 and rank_eligible=True.
  - Positive-tail orders Buy-heavy ticker above weaker
    positives, with Buy-consensus and stronger performance
    above weaker.
  - Negative-tail orders a Short-heavy ticker / inverse-
    style ticker into the bottom of the ranking.
  - Low-buy tail surfaces a buy_votes=0 row whose
    consensus_signal is None (QQQ-vs-SQQQ-style inverse
    confirmation).
  - p_value=None does not crash sort.
  - Deterministic tie-break by ticker.
  - Contract-invalid rows appear in ``rows`` but are
    excluded from the three tails.
  - CLI: empty / blank ticker argument returns rc=2
    without leaking SystemExit.
"""
from __future__ import annotations

import ast
import io
import json
import pickle
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import confluence_ranking_emitter as cre  # noqa: E402
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


def _safe_filename(ticker: str) -> str:
    return f"{ticker.upper().replace('^', '_')}_precomputed_results.pkl"


def _write_realistic_cache_pkl(
    cache_dir: Path,
    ticker: str,
    *,
    last_date: str = "2026-05-08",
    n: int = 60,
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


def _write_stackbuilder_run(
    stack_root: Path,
    target: str,
    *,
    seed: str = "seedTC__AAA-D_BBB-D",
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


def _write_daily_k_artifact(
    artifact_root: Path,
    target: str,
    K: int,
    *,
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
        art, tf_dir / f"{seed_run_id}__K{K}.research_day.json",
    )


def _write_mtf_artifact(
    artifact_root: Path,
    target: str,
    K: int,
    *,
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
    artifact_root: Path,
    target: str,
    *,
    last_date: str = "2026-05-08",
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    confluence_signal: str = "None",
    buy_votes: int | None = None,
    short_votes: int | None = None,
    none_votes: int | None = None,
    missing_votes: int = 0,
    summary_overrides: dict | None = None,
) -> Path:
    """Write a Phase 6D-3 Confluence MTF artifact with
    overridable vote counts and summary fields. Defaults
    are coherent (active_count, available_count,
    agreement_total all derived; agreement_active follows
    strict-unanimity)."""
    safe = target.upper().replace("^", "_")
    conf_dir = artifact_root / "confluence" / safe
    conf_dir.mkdir(parents=True, exist_ok=True)
    K_values = list(range(1, 13))
    timeframes = ["1d", "1wk", "1mo", "3mo", "1y"]
    expected_cells = len(K_values) * len(timeframes)  # 60
    signal_value = {"Buy": 1, "Short": -1, "None": 0}.get(
        confluence_signal, 0,
    )
    if buy_votes is None:
        buy_votes = 50 if confluence_signal == "Buy" else 0
    if short_votes is None:
        short_votes = 50 if confluence_signal == "Short" else 0
    if none_votes is None:
        none_votes = (
            expected_cells - buy_votes - short_votes - missing_votes
        )
    active_count = buy_votes + short_votes
    available_count = active_count + none_votes
    if buy_votes == 0 and short_votes == 0:
        agreement_active = 0
    elif buy_votes > 0 and short_votes == 0:
        agreement_active = buy_votes
    elif buy_votes == 0 and short_votes > 0:
        agreement_active = short_votes
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
        "confluence_signal": confluence_signal,
        "signal": confluence_signal,
        "signal_value": signal_value,
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
    summary = {
        "total_capture_pct": 50.0,
        "avg_daily_capture_pct": 0.05,
        "sharpe_ratio": 0.1,
        "trigger_days": 5,
        "wins": 3,
        "losses": 2,
        "p_value": None,
    }
    if summary_overrides:
        summary.update(summary_overrides)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="confluence",
        target_ticker=target,
        signal_source="",
        run_id="mtf_consensus",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary=summary,
        daily=[row],
        timeframes=timeframes,
        min_active=1,
    )
    return ra.write_research_day_artifact(
        art,
        conf_dir / f"{safe}__MTF_CONSENSUS.research_day.json",
    )


def _write_full_valid_ticker(
    dirs: dict[str, Path],
    target: str,
    *,
    last_date: str = "2026-05-08",
    confluence_signal: str = "None",
    buy_votes: int | None = None,
    short_votes: int | None = None,
    none_votes: int | None = None,
    missing_votes: int = 0,
    summary_overrides: dict | None = None,
) -> None:
    """Write the complete artifact chain for one ticker:
    cache PKL, StackBuilder run, MTF libs, K=1..12 daily K,
    K=1..12 MTF, and Confluence MTF artifact. All seven
    validator contracts should pass against this fixture."""
    _write_realistic_cache_pkl(
        dirs["cache_dir"], target, last_date=last_date,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], target)
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], target, ["1wk", "1mo"],
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
        confluence_signal=confluence_signal,
        buy_votes=buy_votes,
        short_votes=short_votes,
        none_votes=none_votes,
        missing_votes=missing_votes,
        summary_overrides=summary_overrides,
    )


def _snapshot_tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_emitter_has_no_forbidden_imports():
    """Phase 6I-3 emitter must not import any live engine,
    writer, refresher, or pipeline runner. The validator
    chain is the only allowed coupling."""
    tree = ast.parse(
        Path(cre.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance",
        "trafficflow",
        "spymaster",
        "impactsearch",
        "onepass",
        "stackbuilder",
        "dash",
        "daily_signal_board",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_writer",
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
        "forbidden import in confluence_ranking_emitter: "
        f"{bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. Valid 12 K x 5 timeframes => expected_cell_count = 60
# ---------------------------------------------------------------------------


def test_full_valid_fixture_expected_cell_count_60(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "SPY",
        confluence_signal="Buy",
        buy_votes=45,
        short_votes=5,
    )
    report = cre.emit_confluence_ranking(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    assert report.inspected_count == 1
    row = report.rows[0]
    assert row.ticker == "SPY"
    assert row.contract_valid is True
    assert row.rank_eligible is True
    assert tuple(row.K_values) == tuple(range(1, 13))
    assert tuple(row.timeframes) == (
        "1d", "1wk", "1mo", "3mo", "1y",
    )
    assert row.expected_cell_count == 60
    # buy=45, short=5, none defaults to 60-45-5-0 = 10,
    # missing=0 -> active_count=50, available_count=60.
    assert row.active_count == 50
    assert row.available_count == 60
    # signed_vote_score = (45 - 5) / 60.
    assert row.signed_vote_score == pytest.approx(40 / 60)
    # board_row_preview's agreement_active/total are sourced
    # from active_count/available_count (Phase 6I-1
    # amendment); the emitter row mirrors that.
    assert row.agreement_active == 50
    assert row.agreement_total == 60


# ---------------------------------------------------------------------------
# 3. Positive tail ranks Buy-heavy above weaker positives
# ---------------------------------------------------------------------------


def test_positive_tail_ranks_buy_heavy_above_weaker(
    tmp_path: Path,
):
    """Three positive-signed tickers: BUYHI (45/5/10),
    BUYMD (30/5/25), BUYLO (15/5/40). Positive tail must
    rank in descending signed_vote_score order."""
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "BUYHI",
        confluence_signal="Buy",
        buy_votes=45, short_votes=5, none_votes=10,
    )
    _write_full_valid_ticker(
        dirs, "BUYMD",
        confluence_signal="Buy",
        buy_votes=30, short_votes=5, none_votes=25,
    )
    _write_full_valid_ticker(
        dirs, "BUYLO",
        confluence_signal="Buy",
        buy_votes=15, short_votes=5, none_votes=40,
    )
    report = cre.emit_confluence_ranking(
        ["BUYHI", "BUYMD", "BUYLO"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    tickers_in_tail = [r.ticker for r in report.positive_tail]
    assert tickers_in_tail == ["BUYHI", "BUYMD", "BUYLO"]
    # All three contract-valid; none excluded.
    assert all(r.contract_valid for r in report.positive_tail)
    # And the negative tail is empty (no negative signed
    # scores in this fixture).
    assert report.negative_tail == ()


# ---------------------------------------------------------------------------
# 4. Negative tail surfaces a Short-heavy / inverse ticker
# ---------------------------------------------------------------------------


def test_negative_tail_surfaces_short_heavy_inverse_ticker(
    tmp_path: Path,
):
    """QQQ (Buy 40/5) appears positive; SQQQ (Short 40/5)
    is the inverse confirmation pattern -- it must land in
    the negative tail at top position."""
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "QQQ",
        confluence_signal="Buy",
        buy_votes=40, short_votes=5, none_votes=15,
    )
    _write_full_valid_ticker(
        dirs, "SQQQ",
        confluence_signal="Short",
        buy_votes=5, short_votes=40, none_votes=15,
    )
    _write_full_valid_ticker(
        dirs, "MIXED",
        confluence_signal="None",
        buy_votes=10, short_votes=20, none_votes=30,
    )
    report = cre.emit_confluence_ranking(
        ["QQQ", "SQQQ", "MIXED"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    # Positive tail: QQQ only (SQQQ and MIXED have
    # negative signed_vote_score).
    pos_tickers = [r.ticker for r in report.positive_tail]
    assert pos_tickers == ["QQQ"]
    # Negative tail: SQQQ first (signed -0.778), then
    # MIXED (signed -0.167).
    neg_tickers = [r.ticker for r in report.negative_tail]
    assert neg_tickers == ["SQQQ", "MIXED"]
    sqqq_row = report.negative_tail[0]
    assert sqqq_row.consensus_signal == "Short"
    assert sqqq_row.signed_vote_score < 0


# ---------------------------------------------------------------------------
# 5. Low-buy tail surfaces buy_votes=0 even when consensus
#    is None
# ---------------------------------------------------------------------------


def test_low_buy_tail_includes_zero_buy_with_none_consensus(
    tmp_path: Path,
):
    """NOBUY has buy_votes=0, short_votes=10, none_votes=50.
    signed_vote_score = -10/60 = -0.167; consensus_signal
    will resolve to None per the strict-unanimity rule
    when none_votes dominates. The low-buy tail must
    include it regardless."""
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "NOBUY",
        confluence_signal="None",
        buy_votes=0, short_votes=10, none_votes=50,
    )
    _write_full_valid_ticker(
        dirs, "REGULAR",
        confluence_signal="Buy",
        buy_votes=40, short_votes=5, none_votes=15,
    )
    report = cre.emit_confluence_ranking(
        ["NOBUY", "REGULAR"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    low_tickers = [r.ticker for r in report.low_buy_tail]
    assert "NOBUY" in low_tickers
    assert "REGULAR" not in low_tickers
    nobuy = next(
        r for r in report.low_buy_tail if r.ticker == "NOBUY"
    )
    assert nobuy.buy_votes == 0
    assert nobuy.zero_buy_flag is True
    # Strict-unanimity yields consensus_signal "None"
    # because the unanimous side (Short) is non-zero but
    # this fixture writes confluence_signal="None"
    # explicitly. The low-buy tail does NOT require
    # consensus_signal to be Short.
    assert nobuy.consensus_signal == "None"


# ---------------------------------------------------------------------------
# 6. p_value=None does not crash sort
# ---------------------------------------------------------------------------


def test_p_value_none_does_not_crash_sort(tmp_path: Path):
    """Mixed-shape fixture: some tickers have
    summary.p_value set, some do not (None). The emitter
    must rank both without raising TypeError."""
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "PVNONE",
        confluence_signal="Buy",
        buy_votes=40, short_votes=5,
        summary_overrides={"p_value": None},
    )
    _write_full_valid_ticker(
        dirs, "PVSET",
        confluence_signal="Buy",
        buy_votes=30, short_votes=5,
        summary_overrides={"p_value": 0.04},
    )
    # Should not raise.
    report = cre.emit_confluence_ranking(
        ["PVNONE", "PVSET"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    assert len(report.positive_tail) == 2
    # PVNONE has a higher signed_vote_score (0.778 vs
    # 0.714) so it ranks above PVSET, regardless of
    # p_value presence.
    assert report.positive_tail[0].ticker == "PVNONE"
    assert report.positive_tail[0].p_value is None
    assert report.positive_tail[1].p_value == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# 7. Deterministic tie-break by ticker
# ---------------------------------------------------------------------------


def test_deterministic_tie_break_by_ticker(tmp_path: Path):
    """Three tickers with IDENTICAL vote counts. They must
    appear in ticker-alphabetical order across the
    positive tail (every prior sort-key element is a tie).
    """
    dirs = _layout(tmp_path)
    for t in ("CCCC", "AAAA", "BBBB"):
        _write_full_valid_ticker(
            dirs, t,
            confluence_signal="Buy",
            buy_votes=30, short_votes=5, none_votes=25,
        )
    report = cre.emit_confluence_ranking(
        ["CCCC", "AAAA", "BBBB"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    tickers_in_tail = [r.ticker for r in report.positive_tail]
    assert tickers_in_tail == ["AAAA", "BBBB", "CCCC"]


# ---------------------------------------------------------------------------
# 8. Invalid contract row appears in rows but is excluded
#    from the tails
# ---------------------------------------------------------------------------


def test_invalid_contract_row_appears_in_rows_but_not_tails(
    tmp_path: Path,
):
    """OK ticker: full chain. BAD ticker: no StackBuilder
    saved variant -> stackbuilder_missing -> contract
    invalid. The bad row must appear in ``rows`` carrying
    its issue codes; every tail must exclude it."""
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "OKBUY",
        confluence_signal="Buy",
        buy_votes=45, short_votes=5, none_votes=10,
    )
    # BAD: cache + libs + pipeline + Confluence written but
    # no StackBuilder run. The stackbuilder contract fails.
    _write_realistic_cache_pkl(dirs["cache_dir"], "BAD")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "BAD", ["1wk", "1mo"],
    )
    for k in range(1, 13):
        _write_daily_k_artifact(
            dirs["artifact_root"], "BAD", k,
        )
        _write_mtf_artifact(
            dirs["artifact_root"], "BAD", k,
        )
    _write_confluence_artifact(
        dirs["artifact_root"], "BAD",
        confluence_signal="Buy",
        buy_votes=45, short_votes=5,
    )
    report = cre.emit_confluence_ranking(
        ["OKBUY", "BAD"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    tickers_in_rows = [r.ticker for r in report.rows]
    assert "BAD" in tickers_in_rows
    bad = next(r for r in report.rows if r.ticker == "BAD")
    assert bad.contract_valid is False
    # Bad row carries the stackbuilder_missing issue code.
    from confluence_ranking_contract_validator import (
        ISSUE_STACKBUILDER_MISSING,
    )
    assert ISSUE_STACKBUILDER_MISSING in bad.issue_codes
    # Bad row excluded from all three tails.
    assert "BAD" not in [r.ticker for r in report.positive_tail]
    assert "BAD" not in [r.ticker for r in report.negative_tail]
    assert "BAD" not in [r.ticker for r in report.low_buy_tail]
    # Counts reflect 1 valid + 1 invalid.
    assert report.counts_by_contract_validity == {
        "valid": 1, "invalid": 1,
    }


# ---------------------------------------------------------------------------
# 9. CLI: empty / blank tickers returns rc=2 without
#    SystemExit leak
# ---------------------------------------------------------------------------


def test_cli_blank_ticker_returns_rc_2():
    """``--ticker '   '`` collapses to an empty list ->
    rc=2, structured error, no SystemExit propagated."""
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cre.main(["--ticker", "   "])
    assert rc == 2
    err_payload = err.getvalue().strip()
    parsed = json.loads(err_payload)
    assert parsed.get("error") == "no_tickers_supplied"


def test_cli_no_arg_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cre.main([])
    assert rc == 2


def test_cli_invalid_flag_returns_rc_2():
    """Argparse error (unknown flag) is converted to rc=2
    rather than allowing SystemExit to escape."""
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cre.main(["--not-a-flag", "x"])
    assert rc == 2


# ---------------------------------------------------------------------------
# 10. No-writes guard
# ---------------------------------------------------------------------------


def test_emit_does_not_mutate_artifact_tree(tmp_path: Path):
    """Snapshot every file under tmp_path before and after
    a ranking call; the file set + content must be byte-
    identical."""
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "SPY",
        confluence_signal="Buy",
        buy_votes=40, short_votes=5,
    )
    before = _snapshot_tree(tmp_path)
    before_bytes = {p: p.read_bytes() for p in before}
    report = cre.emit_confluence_ranking(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    after = _snapshot_tree(tmp_path)
    assert before == after
    for p, payload in before_bytes.items():
        assert p.read_bytes() == payload
    # Sanity: the report still emitted a valid row.
    assert report.rows[0].ticker == "SPY"


# ---------------------------------------------------------------------------
# 11. top_n clamps tails
# ---------------------------------------------------------------------------


def test_top_n_clamps_each_tail(tmp_path: Path):
    """Five positive tickers + top_n=2 -> positive tail of
    length 2; rows still carries all five."""
    dirs = _layout(tmp_path)
    for n, t in enumerate(
        ("PA", "PB", "PC", "PD", "PE"),
    ):
        _write_full_valid_ticker(
            dirs, t,
            confluence_signal="Buy",
            buy_votes=40 - n, short_votes=5, none_votes=15 + n,
        )
    report = cre.emit_confluence_ranking(
        ["PA", "PB", "PC", "PD", "PE"],
        current_as_of_date="2026-05-08",
        top_n=2,
        **dirs,
    )
    assert len(report.rows) == 5
    assert len(report.positive_tail) == 2
    assert [r.ticker for r in report.positive_tail] == [
        "PA", "PB",
    ]


def test_top_n_zero_emits_empty_tails(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "PA",
        confluence_signal="Buy",
        buy_votes=40, short_votes=5,
    )
    report = cre.emit_confluence_ranking(
        ["PA"], current_as_of_date="2026-05-08",
        top_n=0, **dirs,
    )
    assert report.positive_tail == ()
    assert report.negative_tail == ()
    assert report.low_buy_tail == ()
    # rows still emitted.
    assert len(report.rows) == 1


# ---------------------------------------------------------------------------
# 12. Counts by consensus signal
# ---------------------------------------------------------------------------


def test_counts_by_consensus_signal(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "BB",
        confluence_signal="Buy",
        buy_votes=40, short_votes=5,
    )
    _write_full_valid_ticker(
        dirs, "SS",
        confluence_signal="Short",
        buy_votes=5, short_votes=40,
    )
    _write_full_valid_ticker(
        dirs, "NN",
        confluence_signal="None",
        buy_votes=5, short_votes=5,
    )
    report = cre.emit_confluence_ranking(
        ["BB", "SS", "NN"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    assert report.counts_by_consensus_signal["Buy"] == 1
    assert report.counts_by_consensus_signal["Short"] == 1
    assert report.counts_by_consensus_signal["None"] == 1
    assert report.counts_by_consensus_signal["unknown"] == 0


# ---------------------------------------------------------------------------
# 13. CLI happy path: emits valid JSON to stdout, rc=0
# ---------------------------------------------------------------------------


def test_cli_happy_path_emits_json(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "SPY",
        confluence_signal="Buy",
        buy_votes=40, short_votes=5,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cre.main([
            "--ticker", "SPY",
            "--current-as-of-date", "2026-05-08",
            "--cache-dir", str(dirs["cache_dir"]),
            "--artifact-root", str(dirs["artifact_root"]),
            "--stackbuilder-root",
            str(dirs["stackbuilder_root"]),
            "--signal-library-dir",
            str(dirs["signal_library_dir"]),
            "--top-n", "5",
        ])
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert parsed["inspected_count"] == 1
    assert parsed["top_n"] == 5
    assert parsed["rows"][0]["ticker"] == "SPY"
    assert (
        parsed["rows"][0]["expected_cell_count"] == 60
    )
    # The JSON shape is fully serializable (no NaN / Inf).
    json.dumps(parsed)


# ---------------------------------------------------------------------------
# 14. JSON serialization round-trip
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_ticker(
        dirs, "SPY",
        confluence_signal="Buy",
        buy_votes=40, short_votes=5,
        summary_overrides={"p_value": None},
    )
    report = cre.emit_confluence_ranking(
        ["SPY"], current_as_of_date="2026-05-08", **dirs,
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    reparsed = json.loads(serialized)
    assert reparsed["rows"][0]["ticker"] == "SPY"
    assert reparsed["rows"][0]["p_value"] is None
    assert reparsed["rows"][0]["contract_valid"] is True
