"""Phase 6I-19 tests for confluence_decision_brief.

Pins the brief module contract:

  - No forbidden top-level imports (writer / refresher /
    pipeline runner / live engines / yfinance / dash /
    subprocess).
  - Both top AND bottom tails surfaced (positive_tail
    plus negative_tail plus low_buy_tail) — the brief
    does NOT hide the bottom of the list.
  - Group A + Group B fields passed through verbatim
    from the Phase 6I-3 emitter; no transformation.
  - MTF-breadth classification (daily_only / mixed /
    broad_multi_timeframe / none).
  - K-coverage flag (k_count + k_coverage_complete).
  - Inverse-confirmation notes fire only when BOTH sides
    of a known pair are in the inspected set.
  - Blocked-or-unrankable summary aggregates by
    ranking_blocked_reason.
  - Missing-data summary aggregates issue_codes.
  - CLI rc=0 / rc=2; no SystemExit leak.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import confluence_decision_brief as brief  # noqa: E402
import confluence_ranking_emitter as cre  # noqa: E402


# ---------------------------------------------------------------------------
# Fake ranking row + report fixtures
# ---------------------------------------------------------------------------


def _make_row(
    *,
    ticker: str,
    contract_valid: bool = True,
    rank_eligible: bool = True,
    issue_codes: tuple[str, ...] = (),
    recommended_next_operator_action: str = (
        "contract_valid_no_action"
    ),
    ranking_blocked_reason: str = "",
    confluence_last_date: Optional[str] = "2026-05-08",
    consensus_signal: Optional[str] = "None",
    consensus_signal_value: Optional[int] = 0,
    agreement_active: Optional[int] = 7,
    agreement_total: Optional[int] = 60,
    agreement_ratio: Optional[float] = 7.0 / 60.0,
    buy_votes: Optional[int] = 5,
    short_votes: Optional[int] = 2,
    none_votes: Optional[int] = 53,
    missing_votes: Optional[int] = 0,
    active_count: Optional[int] = 7,
    available_count: Optional[int] = 60,
    buy_ratio: Optional[float] = 5.0 / 60.0,
    short_ratio: Optional[float] = 2.0 / 60.0,
    none_ratio: Optional[float] = 53.0 / 60.0,
    missing_ratio: Optional[float] = 0.0,
    signed_vote_score: Optional[float] = 0.05,
    zero_buy_flag: bool = False,
    timeframes: tuple[str, ...] = (
        "1d", "1wk", "1mo", "3mo", "1y",
    ),
    K_values: tuple[int, ...] = tuple(range(1, 13)),
    expected_cell_count: int = 60,
    total_capture_pct: Optional[float] = 42.44,
    avg_daily_capture_pct: Optional[float] = 0.05,
    sharpe_ratio: Optional[float] = 0.03,
    trigger_days: Optional[int] = 870,
    wins: Optional[int] = 437,
    losses: Optional[int] = 418,
    p_value: Optional[float] = None,
) -> cre.ConfluenceRankingRow:
    return cre.ConfluenceRankingRow(
        ticker=ticker,
        contract_valid=contract_valid,
        issue_codes=tuple(issue_codes),
        recommended_next_operator_action=(
            recommended_next_operator_action
        ),
        rank_eligible=rank_eligible,
        ranking_blocked_reason=ranking_blocked_reason,
        confluence_last_date=confluence_last_date,
        consensus_signal=consensus_signal,
        consensus_signal_value=consensus_signal_value,
        agreement_active=agreement_active,
        agreement_total=agreement_total,
        agreement_ratio=agreement_ratio,
        buy_votes=buy_votes,
        short_votes=short_votes,
        none_votes=none_votes,
        missing_votes=missing_votes,
        active_count=active_count,
        available_count=available_count,
        buy_ratio=buy_ratio,
        short_ratio=short_ratio,
        none_ratio=none_ratio,
        missing_ratio=missing_ratio,
        signed_vote_score=signed_vote_score,
        zero_buy_flag=zero_buy_flag,
        timeframes=timeframes,
        K_values=K_values,
        expected_cell_count=expected_cell_count,
        total_capture_pct=total_capture_pct,
        avg_daily_capture_pct=avg_daily_capture_pct,
        sharpe_ratio=sharpe_ratio,
        trigger_days=trigger_days,
        wins=wins,
        losses=losses,
        p_value=p_value,
    )


def _make_report(
    *,
    rows: tuple[cre.ConfluenceRankingRow, ...] = (),
    positive_tail: tuple[
        cre.ConfluenceRankingRow, ...
    ] = (),
    negative_tail: tuple[
        cre.ConfluenceRankingRow, ...
    ] = (),
    low_buy_tail: tuple[
        cre.ConfluenceRankingRow, ...
    ] = (),
    top_n: int = 10,
    inspected_count: int = 0,
    current_as_of_date: str = "2026-05-12",
) -> cre.ConfluenceRankingReport:
    return cre.ConfluenceRankingReport(
        generated_at="2026-05-13T00:00:00+00:00",
        current_as_of_date=current_as_of_date,
        inspected_count=inspected_count or len(rows),
        tickers=tuple(r.ticker for r in rows),
        top_n=top_n,
        rows=rows,
        positive_tail=positive_tail,
        negative_tail=negative_tail,
        low_buy_tail=low_buy_tail,
        counts_by_contract_validity={
            "valid": sum(
                1 for r in rows if r.contract_valid
            ),
            "invalid": sum(
                1 for r in rows if not r.contract_valid
            ),
        },
        counts_by_consensus_signal={},
    )


def _ranking_returning(
    report: cre.ConfluenceRankingReport,
):
    def fn(tickers, **kwargs):
        return report
    return fn


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_brief_module_has_no_forbidden_imports():
    """The brief must not import any writer / refresher /
    pipeline runner / live engine / yfinance / dash /
    subprocess at module top level. The Phase 6I-3
    emitter and Phase 6I-5 universe planner helpers
    are allowed (the planner via lazy import inside the
    entry function)."""
    tree = ast.parse(
        Path(brief.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
        "yfinance",
        "dash",
        "spymaster",
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
    bad = [
        m for m in found if m.split(".")[0] in forbidden
    ]
    assert not bad, (
        f"forbidden import in brief: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. Both top AND bottom tails surfaced
# ---------------------------------------------------------------------------


def test_both_top_and_bottom_tails_surfaced():
    """The brief must NOT hide the bottom of the list.
    Positive_tail / negative_tail / low_buy_tail all
    pass through from the Phase 6I-3 emitter."""
    spy = _make_row(
        ticker="SPY",
        consensus_signal="Buy",
        signed_vote_score=0.30,
    )
    short_candidate = _make_row(
        ticker="ZZZ",
        consensus_signal="Short",
        signed_vote_score=-0.40,
        buy_votes=2,
        short_votes=26,
    )
    no_buy = _make_row(
        ticker="DULL",
        consensus_signal="None",
        signed_vote_score=-0.05,
        buy_votes=0,
        short_votes=3,
        zero_buy_flag=True,
    )
    fake = _make_report(
        rows=(spy, short_candidate, no_buy),
        positive_tail=(spy,),
        negative_tail=(short_candidate,),
        low_buy_tail=(no_buy,),
        top_n=5,
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY", "ZZZ", "DULL"],
        top_n=5,
        ranking_callable=_ranking_returning(fake),
    )
    assert [r.ticker for r in report.top_positive_candidates] == [
        "SPY",
    ]
    assert [
        r.ticker for r in report.top_negative_candidates
    ] == ["ZZZ"]
    assert [
        r.ticker for r in report.low_buy_candidates
    ] == ["DULL"]


# ---------------------------------------------------------------------------
# 3. Group A + Group B fields pass through verbatim
# ---------------------------------------------------------------------------


def test_group_a_and_group_b_fields_pass_through():
    """The brief row must carry Group A signal-breadth +
    Group B performance-quality fields with the SAME
    values the Phase 6I-3 emitter produced."""
    source = _make_row(
        ticker="SPY",
        consensus_signal="Buy",
        consensus_signal_value=1,
        agreement_active=21,
        agreement_total=60,
        agreement_ratio=0.35,
        buy_votes=20,
        short_votes=1,
        none_votes=39,
        missing_votes=0,
        signed_vote_score=0.3167,
        total_capture_pct=53.21,
        avg_daily_capture_pct=0.0612,
        sharpe_ratio=0.041,
        trigger_days=900,
        wins=470,
        losses=420,
        p_value=0.03,
    )
    fake = _make_report(
        rows=(source,),
        positive_tail=(source,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY"],
        top_n=5,
        ranking_callable=_ranking_returning(fake),
    )
    out = report.top_positive_candidates[0]
    # Group A
    assert out.consensus_signal == "Buy"
    assert out.consensus_signal_value == 1
    assert out.agreement_active == 21
    assert out.agreement_total == 60
    assert out.agreement_ratio == 0.35
    assert out.buy_votes == 20
    assert out.short_votes == 1
    assert out.none_votes == 39
    assert out.missing_votes == 0
    assert out.signed_vote_score == 0.3167
    # Group B
    assert out.total_capture_pct == 53.21
    assert out.avg_daily_capture_pct == 0.0612
    assert out.sharpe_ratio == 0.041
    assert out.trigger_days == 900
    assert out.wins == 470
    assert out.losses == 420
    assert out.p_value == 0.03


# ---------------------------------------------------------------------------
# 4. MTF-breadth classification
# ---------------------------------------------------------------------------


def test_mtf_breadth_broad_when_three_or_more_timeframes():
    row = _make_row(
        ticker="SPY",
        timeframes=("1d", "1wk", "1mo", "3mo", "1y"),
    )
    fake = _make_report(
        rows=(row,),
        positive_tail=(row,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY"],
        ranking_callable=_ranking_returning(fake),
    )
    out = report.top_positive_candidates[0]
    assert out.mtf_breadth == brief.MTF_BREADTH_BROAD


def test_mtf_breadth_daily_only_when_only_one_d():
    row = _make_row(ticker="SPY", timeframes=("1d",))
    fake = _make_report(
        rows=(row,),
        positive_tail=(row,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY"],
        ranking_callable=_ranking_returning(fake),
    )
    out = report.top_positive_candidates[0]
    assert out.mtf_breadth == brief.MTF_BREADTH_DAILY_ONLY


def test_mtf_breadth_mixed_when_two_timeframes():
    row = _make_row(ticker="SPY", timeframes=("1d", "1wk"))
    fake = _make_report(
        rows=(row,),
        positive_tail=(row,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY"],
        ranking_callable=_ranking_returning(fake),
    )
    out = report.top_positive_candidates[0]
    assert out.mtf_breadth == brief.MTF_BREADTH_MIXED


def test_mtf_breadth_none_when_no_timeframes():
    row = _make_row(ticker="SPY", timeframes=())
    fake = _make_report(
        rows=(row,),
        positive_tail=(row,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY"],
        ranking_callable=_ranking_returning(fake),
    )
    out = report.top_positive_candidates[0]
    assert out.mtf_breadth == brief.MTF_BREADTH_NONE


def test_k_coverage_complete_only_when_k_set_is_1_to_12():
    full = _make_row(
        ticker="SPY",
        K_values=tuple(range(1, 13)),
    )
    partial = _make_row(
        ticker="QQQ",
        K_values=(1, 2, 3),
    )
    fake = _make_report(
        rows=(full, partial),
        positive_tail=(full, partial),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY", "QQQ"],
        ranking_callable=_ranking_returning(fake),
    )
    rows_by_ticker = {
        r.ticker: r
        for r in report.top_positive_candidates
    }
    assert rows_by_ticker["SPY"].k_count == 12
    assert rows_by_ticker["SPY"].k_coverage_complete is True
    assert rows_by_ticker["QQQ"].k_count == 3
    assert (
        rows_by_ticker["QQQ"].k_coverage_complete is False
    )


# ---------------------------------------------------------------------------
# 5. Inverse-confirmation notes
# ---------------------------------------------------------------------------


def test_inverse_pair_note_fires_when_both_sides_present():
    """When QQQ and SQQQ both appear in the inspected
    set, the brief emits exactly one pair annotation
    note carrying both rows' consensus signals + agreement
    ratios."""
    qqq = _make_row(
        ticker="QQQ",
        consensus_signal="Buy",
        agreement_ratio=0.40,
    )
    sqqq = _make_row(
        ticker="SQQQ",
        consensus_signal="Short",
        agreement_ratio=0.42,
    )
    fake = _make_report(
        rows=(qqq, sqqq),
        positive_tail=(qqq,),
        negative_tail=(sqqq,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["QQQ", "SQQQ"],
        ranking_callable=_ranking_returning(fake),
    )
    assert len(report.inverse_confirmation_notes) == 1
    note = report.inverse_confirmation_notes[0]
    assert note.primary == "QQQ"
    assert note.inverse == "SQQQ"
    assert note.primary_consensus_signal == "Buy"
    assert note.inverse_consensus_signal == "Short"
    assert note.primary_agreement_ratio == 0.40
    assert note.inverse_agreement_ratio == 0.42
    assert "known inverse" in note.note.lower()
    # Brief NEVER draws a conclusion in the note.
    lower = note.note.lower()
    assert "confirms" not in lower
    assert "contradicts" not in lower


def test_inverse_pair_note_omitted_when_inverse_missing():
    """When only one side of a known pair is present,
    no annotation note fires."""
    qqq = _make_row(ticker="QQQ", consensus_signal="Buy")
    fake = _make_report(
        rows=(qqq,),
        positive_tail=(qqq,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["QQQ"],
        ranking_callable=_ranking_returning(fake),
    )
    assert report.inverse_confirmation_notes == ()


def test_inverse_pair_is_symmetric_per_pair():
    """Even when both QQQ and SQQQ are in the inspected
    set AND SQQQ has its own KNOWN_INVERSE_PAIRS entry,
    the brief emits the pair exactly ONCE (not twice)."""
    qqq = _make_row(ticker="QQQ")
    sqqq = _make_row(ticker="SQQQ")
    fake = _make_report(
        rows=(qqq, sqqq),
        positive_tail=(qqq,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["QQQ", "SQQQ"],
        ranking_callable=_ranking_returning(fake),
    )
    pair_keys = [
        frozenset({n.primary, n.inverse})
        for n in report.inverse_confirmation_notes
    ]
    assert len(pair_keys) == len(set(pair_keys)), (
        f"duplicate pair: {pair_keys!r}"
    )


# ---------------------------------------------------------------------------
# 6. Blocked-or-unrankable summary
# ---------------------------------------------------------------------------


def test_blocked_summary_aggregates_by_blocked_reason():
    """The blocked/unrankable summary groups by
    ``ranking_blocked_reason``; the ticker tuple lists
    every blocked ticker alphabetically."""
    healthy = _make_row(ticker="SPY")
    stale = _make_row(
        ticker="AAA",
        rank_eligible=False,
        ranking_blocked_reason=(
            "stale_confluence_day_artifact"
        ),
    )
    invalid = _make_row(
        ticker="BAD",
        contract_valid=False,
        rank_eligible=False,
        issue_codes=("missing_target_signal_engine_cache",),
        ranking_blocked_reason="",
    )
    fake = _make_report(
        rows=(healthy, stale, invalid),
        positive_tail=(healthy,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY", "AAA", "BAD"],
        ranking_callable=_ranking_returning(fake),
    )
    assert report.blocked_or_unrankable_summary[
        "stale_confluence_day_artifact"
    ] == 1
    assert report.blocked_or_unrankable_summary[
        "contract_invalid"
    ] == 1
    assert "SPY" not in report.blocked_or_unrankable_tickers
    assert "AAA" in report.blocked_or_unrankable_tickers
    assert "BAD" in report.blocked_or_unrankable_tickers


def test_missing_data_summary_aggregates_issue_codes():
    a = _make_row(
        ticker="A",
        issue_codes=("missing_target_signal_engine_cache",),
    )
    b = _make_row(
        ticker="B",
        issue_codes=(
            "missing_target_signal_engine_cache",
            "missing_stackbuilder_run",
        ),
    )
    fake = _make_report(rows=(a, b))
    report = brief.evaluate_confluence_decision_brief(
        tickers=["A", "B"],
        ranking_callable=_ranking_returning(fake),
    )
    assert report.missing_data_summary[
        "missing_target_signal_engine_cache"
    ] == 2
    assert report.missing_data_summary[
        "missing_stackbuilder_run"
    ] == 1


# ---------------------------------------------------------------------------
# 7. Remaining limitations carry-forward
# ---------------------------------------------------------------------------


def test_remaining_limitations_names_pipeline_writer_gaps():
    """The brief's ``remaining_limitations`` must name
    the load-bearing carry-forward gaps from Phase 6I-17
    / 6I-18: pipeline-runner-write still open, post-
    pipeline validation still open, writer-surface
    provider telemetry still pending."""
    fake = _make_report(rows=())
    report = brief.evaluate_confluence_decision_brief(
        tickers=[],
        ranking_callable=_ranking_returning(fake),
    )
    joined = " ".join(report.remaining_limitations)
    assert "real_confluence_pipeline_runner_write" in joined
    assert (
        "real_post_pipeline_validation_on_writer_path"
        in joined
    )
    assert "writer stdout" in joined
    # Brief also disclaims aggregate p_value.
    assert "Aggregate Confluence p_value" in joined


def test_remaining_limitations_names_missing_multi_window_engine():
    """Phase 6I-19 amendment (operator product
    correction): the brief's ``remaining_limitations``
    must explicitly name that the true TrafficFlow-style
    multi-window K engine is NOT built by this brief.
    The named windows ``1d / 1wk / 1mo / 3mo / 1y`` must
    appear in the limitation, alongside an explicit
    acknowledgement that the brief is a presentation
    adapter and does not create missing MTF data."""
    fake = _make_report(rows=())
    report = brief.evaluate_confluence_decision_brief(
        tickers=[],
        ranking_callable=_ranking_returning(fake),
    )
    joined = " ".join(report.remaining_limitations)
    # Load-bearing words: multi-window engine,
    # StackBuilder K build, the five named windows, and
    # the explicit "presentation adapter / never
    # creates" disclaimer.
    assert (
        "True TrafficFlow-style multi-window K "
        "evaluation"
    ) in joined
    assert "StackBuilder K build" in joined
    assert "1d / 1wk / 1mo / 3mo / 1y" in joined
    assert "presentation adapter" in joined
    assert "never creates the missing" in joined


def test_module_docstring_and_doc_do_not_overclaim():
    """Phase 6I-19 amendment (operator product
    correction): the module docstring and the Phase 6I-19
    markdown doc must NOT carry the original overclaim
    phrases that read as if the legacy workflow was
    fully replaced or as if Phase 6I-1 / 6I-3 / 6I-5
    fixed the multi-window generation problem.

    The brief is a presentation adapter; the actual
    multi-window engine is still future work. Test
    enforces both files do not contain the flagged
    phrases."""
    module_src = Path(brief.__file__).read_text(
        encoding="utf-8",
    )
    doc_path = (
        Path(brief.__file__).resolve().parent
        / "md_library" / "shared"
        / (
            "2026-05-13_PHASE_6I19_MTF_CONFLUENCE_"
            "DECISION_BRIEF.md"
        )
    )
    doc_src = doc_path.read_text(encoding="utf-8")
    overclaim_phrases = [
        # Original module docstring overclaim.
        "That chain is now obsolete",
        # Original doc Section 1 overclaim header.
        "How this replaces the old manual workflow",
        # Original doc Section 1 overclaim closure
        # phrase.
        "Phase 6I-1 / 6I-3 / 6I-5 fixed all three",
        # Original doc Section 5 overclaim header.
        "Why multi-timeframe is the key upgrade",
        # Cross-doc overclaim variants.
        "old chain is now obsolete",
    ]
    found_in_module: list[str] = [
        p for p in overclaim_phrases if p in module_src
    ]
    found_in_doc: list[str] = [
        p for p in overclaim_phrases if p in doc_src
    ]
    assert not found_in_module, (
        "Phase 6I-19 module docstring still carries "
        f"overclaim phrase(s): {found_in_module!r}. "
        "Per operator product correction, the brief is "
        "a presentation adapter on existing artifacts "
        "and must not claim to have replaced the "
        "legacy workflow or to have built the still-"
        "missing multi-window engine."
    )
    assert not found_in_doc, (
        "Phase 6I-19 markdown doc still carries "
        f"overclaim phrase(s): {found_in_doc!r}. Same "
        "operator product correction applies."
    )


# ---------------------------------------------------------------------------
# 8. JSON serialization
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips():
    row = _make_row(ticker="SPY")
    fake = _make_report(
        rows=(row,),
        positive_tail=(row,),
    )
    report = brief.evaluate_confluence_decision_brief(
        tickers=["SPY"],
        ranking_callable=_ranking_returning(fake),
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored["top_n"] == 10
    assert restored["top_positive_candidates"][0][
        "ticker"
    ] == "SPY"
    assert (
        "mtf_breadth"
        in restored["top_positive_candidates"][0]
    )
    assert "remaining_limitations" in restored


# ---------------------------------------------------------------------------
# 9. CLI
# ---------------------------------------------------------------------------


def test_cli_no_ticker_source_returns_rc_2(capsys):
    rc = brief.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "no_ticker_source_supplied" in captured.err


def test_cli_unknown_flag_returns_rc_2():
    rc = brief.main(["--no-such-flag"])
    assert rc == 2


def test_cli_happy_path_emits_json(monkeypatch, capsys):
    """Run the CLI through main(argv=...) with a monkey-
    patched evaluator so no real ranking emitter / live
    artifacts / yfinance is touched."""

    def fake_evaluate(*args, **kwargs):
        return brief.DecisionBriefReport(
            generated_at="2026-05-13T00:00:00+00:00",
            current_as_of_date="2026-05-12",
            inspected_count=1,
            top_n=3,
            top_positive_candidates=(
                brief.DecisionBriefRow(
                    ticker="SPY",
                    contract_valid=True,
                    rank_eligible=True,
                    issue_codes=(),
                    recommended_next_operator_action=(
                        "contract_valid_no_action"
                    ),
                    ranking_blocked_reason="",
                    confluence_last_date="2026-05-08",
                    consensus_signal="Buy",
                    consensus_signal_value=1,
                    agreement_active=21,
                    agreement_total=60,
                    agreement_ratio=0.35,
                    buy_votes=20,
                    short_votes=1,
                    none_votes=39,
                    missing_votes=0,
                    signed_vote_score=0.317,
                    timeframes=("1d", "1wk", "1mo"),
                    K_values=tuple(range(1, 13)),
                    total_capture_pct=50.0,
                    avg_daily_capture_pct=0.06,
                    sharpe_ratio=0.04,
                    trigger_days=900,
                    wins=470,
                    losses=420,
                    p_value=0.03,
                    mtf_breadth=(
                        brief.MTF_BREADTH_BROAD
                    ),
                    k_count=12,
                    k_coverage_complete=True,
                ),
            ),
            top_negative_candidates=(),
            low_buy_candidates=(),
            inverse_confirmation_notes=(),
            blocked_or_unrankable_summary={},
            blocked_or_unrankable_tickers=(),
            missing_data_summary={},
            remaining_limitations=(),
        )

    monkeypatch.setattr(
        brief,
        "evaluate_confluence_decision_brief",
        fake_evaluate,
    )
    rc = brief.main(["--ticker", "SPY", "--top-n", "3"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["top_n"] == 3
    assert payload["top_positive_candidates"][0][
        "ticker"
    ] == "SPY"
    assert payload["top_positive_candidates"][0][
        "mtf_breadth"
    ] == brief.MTF_BREADTH_BROAD


def test_cli_no_systemexit_leak_on_argparse_error():
    rc_seen = None
    try:
        rc_seen = brief.main(["--top-n"])
    except SystemExit:
        rc_seen = "leaked"
    assert rc_seen == 2
