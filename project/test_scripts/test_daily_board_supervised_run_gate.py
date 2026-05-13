"""Phase 6I-9 tests for daily_board_supervised_run_gate.

Pins:

  - Static forbidden-imports guard: writer, refresher,
    pipeline runner, yfinance, dash, live engines,
    subprocess.
  - No-StackBuilder-age-window static guard.
  - CLI mutual exclusion + rc=0/2/3 + no SystemExit
    leak.
  - Read-only behavior with a fake queue planner.
  - safe=True only when the write-ready set is
    non-empty AND not truncated.
  - safe=False for: wait-only / manual / upstream-
    blocked / downstream-gap / already-current /
    empty / truncated.
  - Decision cascade priority.
  - Advisory commands are strings only.
  - Positive / negative / low_buy tails pass through.
  - Persist-skip-lag wait routes to
    wait_for_cache_ahead_of_cutoff (not refresh).
  - JSON serialization stable.
"""
from __future__ import annotations

import ast
import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import daily_board_supervised_run_gate as gate  # noqa: E402


# ---------------------------------------------------------------------------
# Fake queue-report fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeQueueItem:
    ticker: str
    queue_name: str = ""
    recommended_action: str = ""
    advisory_command: Optional[str] = None
    write_requires_env_var: bool = False
    upstream_primary_blocker: str = ""
    primary_blocker: str = ""
    automation_blocking_reasons: tuple = ()
    upstream_issue_codes: tuple = ()
    cache_cutoff_action: Optional[str] = None
    source_cache_date: Optional[str] = None
    downstream_contract_verdict: Optional[str] = (
        "contract_valid_no_action"
    )
    current_leader_eligible: bool = False
    ranking_blocked_reason: str = ""
    consensus_signal: Optional[str] = None
    agreement_ratio: Optional[float] = None
    signed_vote_score: Optional[float] = None
    total_capture_pct: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    p_value: Optional[float] = None


@dataclass
class _FakeQueueReport:
    """Minimal ExecutionQueueReport stand-in. Only the
    fields the gate actually reads are populated."""

    current_as_of_date: str = "2026-05-08"
    inspected_count: int = 0
    discovered_stackbuilder_ticker_count: int = 0
    top_n: int = 5
    queue_counts: dict = field(default_factory=dict)
    queue_truncation: dict = field(default_factory=dict)
    selected_refresh_count: int = 0
    selected_pipeline_count: int = 0
    pipeline_only_queue: tuple = ()
    refresh_source_cache_then_pipeline_queue: tuple = ()
    wait_for_cache_ahead_queue: tuple = ()
    manual_stackbuilder_queue: tuple = ()
    upstream_blocked_queue: tuple = ()
    downstream_gap_queue: tuple = ()
    current_leader_eligible_queue: tuple = ()
    positive_tail: tuple = ()
    negative_tail: tuple = ()
    low_buy_tail: tuple = ()


def _planner_returning(report: _FakeQueueReport):
    """Build a fake queue-planner callable that returns
    the given fake report. The gate accepts this via
    its ``queue_planner_callable`` injection point."""

    def fake_planner(**kwargs):
        return report
    return fake_planner


def _truncation_dict(
    refresh: bool = False, pipeline: bool = False,
    **rest: bool,
) -> dict:
    base = {
        "pipeline_only_queue": pipeline,
        "refresh_source_cache_then_pipeline_queue": refresh,
        "wait_for_cache_ahead_queue": False,
        "manual_stackbuilder_queue": False,
        "upstream_blocked_queue": False,
        "downstream_gap_queue": False,
        "current_leader_eligible_queue": False,
    }
    base.update(rest)
    return base


# ---------------------------------------------------------------------------
# 1. Forbidden-imports static guard
# ---------------------------------------------------------------------------


def test_gate_module_has_no_forbidden_imports():
    """The gate must not import any writer / refresher
    / pipeline runner / live engine / yfinance /
    subprocess."""
    tree = ast.parse(
        Path(gate.__file__).read_text(encoding="utf-8"),
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
    bad = [m for m in found if m.split(".")[0] in forbidden]
    assert not bad, (
        f"forbidden import in gate: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. No StackBuilder age-window strings in the module
# ---------------------------------------------------------------------------


def test_gate_carries_no_stackbuilder_age_window():
    text = Path(gate.__file__).read_text(encoding="utf-8")
    forbidden = [
        "STACKBUILDER_AGE_DAYS",
        "STACKBUILDER_STALE_DAYS",
        "STALE_DAYS",
        "AGE_DAYS",
        "30 days",
        "thirty days",
    ]
    found = [s for s in forbidden if s in text]
    assert not found, (
        f"gate must not introduce a StackBuilder age "
        f"window; found: {found}"
    )


# ---------------------------------------------------------------------------
# 3. Safe = True when write-ready set non-empty AND
#    NOT truncated
# ---------------------------------------------------------------------------


def test_safe_true_when_refresh_queue_has_tickers(
    tmp_path: Path,
):
    fake = _FakeQueueReport(
        inspected_count=1,
        discovered_stackbuilder_ticker_count=1,
        selected_refresh_count=1,
        refresh_source_cache_then_pipeline_queue=(
            _FakeQueueItem(
                ticker="SPY",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker SPY --write"
                ),
                write_requires_env_var=True,
            ),
        ),
        queue_counts={
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 1,
            "wait_for_cache_ahead_queue": 0,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is True
    assert report.recommended_operator_action == (
        gate.ACTION_AUTHORIZE_GUARDED_WRITER
    )
    assert report.authorization_candidate_tickers == ("SPY",)
    assert report.advisory_commands == (
        "python daily_board_automation_writer.py --ticker SPY --write",
    )


def test_safe_true_when_pipeline_only_queue_has_tickers(
    tmp_path: Path,
):
    fake = _FakeQueueReport(
        inspected_count=1,
        discovered_stackbuilder_ticker_count=1,
        selected_pipeline_count=1,
        pipeline_only_queue=(
            _FakeQueueItem(
                ticker="SPY",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker SPY --write"
                ),
                write_requires_env_var=True,
            ),
        ),
        queue_counts={
            "pipeline_only_queue": 1,
            "refresh_source_cache_then_pipeline_queue": 0,
            "wait_for_cache_ahead_queue": 0,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is True
    assert report.recommended_operator_action == (
        gate.ACTION_AUTHORIZE_GUARDED_WRITER
    )
    assert report.pipeline_only_tickers == ("SPY",)


# ---------------------------------------------------------------------------
# 4. Safe = False on each blocked-only scenario
# ---------------------------------------------------------------------------


def test_safe_false_wait_for_cache_ahead_only():
    """Persist-skip-lag pure-wait case: SPY in
    wait_for_cache_ahead_queue, everything else empty.
    Must route to wait_for_cache_ahead_of_cutoff (NOT a
    refresh/rerun recommendation)."""
    fake = _FakeQueueReport(
        inspected_count=1,
        discovered_stackbuilder_ticker_count=1,
        wait_for_cache_ahead_queue=(
            _FakeQueueItem(
                ticker="SPY",
                cache_cutoff_action=(
                    "pipeline_output_lags_persist_skip"
                ),
            ),
        ),
        queue_counts={
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 0,
            "wait_for_cache_ahead_queue": 1,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    # MUST be the wait action, not a refresh/rerun.
    assert report.recommended_operator_action == (
        gate.ACTION_WAIT_FOR_CACHE_AHEAD
    )
    assert "SPY" in report.wait_for_cache_ahead_tickers
    assert (
        gate.BLOCKING_WAITING_FOR_CACHE_AHEAD_OF_CUTOFF
        in report.blocking_reasons
    )
    assert report.advisory_commands == ()


def test_safe_false_manual_stackbuilder_only():
    fake = _FakeQueueReport(
        inspected_count=1,
        manual_stackbuilder_queue=(
            _FakeQueueItem(
                ticker="ZZZ",
                upstream_primary_blocker=(
                    "upstream_trio_missing_stackbuilder_run"
                ),
            ),
        ),
        queue_counts={
            "manual_stackbuilder_queue": 1,
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 0,
            "wait_for_cache_ahead_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["ZZZ"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_RESOLVE_STACKBUILDER_INPUTS
    )


def test_safe_false_upstream_blocked_only():
    fake = _FakeQueueReport(
        inspected_count=1,
        upstream_blocked_queue=(
            _FakeQueueItem(
                ticker="QQQ",
                upstream_primary_blocker=(
                    "missing_target_signal_engine_cache"
                ),
            ),
        ),
        queue_counts={
            "upstream_blocked_queue": 1,
            "manual_stackbuilder_queue": 0,
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 0,
            "wait_for_cache_ahead_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["QQQ"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_FIX_UPSTREAM_INPUTS
    )


def test_safe_false_downstream_gap_only():
    fake = _FakeQueueReport(
        inspected_count=1,
        downstream_gap_queue=(
            _FakeQueueItem(
                ticker="GAP",
                primary_blocker="downstream_artifact_gap",
            ),
        ),
        queue_counts={
            "downstream_gap_queue": 1,
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 0,
            "wait_for_cache_ahead_queue": 0,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["GAP"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_BUILD_MISSING_DOWNSTREAM_ARTIFACTS
    )


def test_safe_false_already_current_only():
    fake = _FakeQueueReport(
        inspected_count=1,
        current_leader_eligible_queue=(
            _FakeQueueItem(
                ticker="LEAD",
                current_leader_eligible=True,
            ),
        ),
        queue_counts={
            "current_leader_eligible_queue": 1,
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 0,
            "wait_for_cache_ahead_queue": 0,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["LEAD"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_ALREADY_CURRENT
    )


def test_safe_false_empty_inspection():
    """No tickers inspected -> safe=False, MANUAL_REVIEW
    with the no_inspected_tickers blocker."""
    fake = _FakeQueueReport(inspected_count=0)
    fake.queue_counts = _truncation_dict_counts()
    fake.queue_truncation = _truncation_dict()
    report = gate.evaluate_supervised_run_gate(
        tickers=[],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_MANUAL_REVIEW
    )
    assert (
        gate.BLOCKING_NO_INSPECTED_TICKERS
        in report.blocking_reasons
    )


def _truncation_dict_counts():
    return {
        "pipeline_only_queue": 0,
        "refresh_source_cache_then_pipeline_queue": 0,
        "wait_for_cache_ahead_queue": 0,
        "manual_stackbuilder_queue": 0,
        "upstream_blocked_queue": 0,
        "downstream_gap_queue": 0,
        "current_leader_eligible_queue": 0,
    }


# ---------------------------------------------------------------------------
# 5. Truncation behavior
# ---------------------------------------------------------------------------


def test_truncated_write_ready_set_blocks_authorization():
    """Even when the write-ready set is non-empty, if
    truncation hid candidates from the operator, the
    gate refuses to authorize blindly."""
    fake = _FakeQueueReport(
        inspected_count=10,
        discovered_stackbuilder_ticker_count=10,
        selected_refresh_count=3,
        refresh_source_cache_then_pipeline_queue=(
            _FakeQueueItem(
                ticker="AA",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker AA --write"
                ),
                write_requires_env_var=True,
            ),
            _FakeQueueItem(
                ticker="BB",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker BB --write"
                ),
                write_requires_env_var=True,
            ),
            _FakeQueueItem(
                ticker="CC",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker CC --write"
                ),
                write_requires_env_var=True,
            ),
        ),
        queue_counts={
            "refresh_source_cache_then_pipeline_queue": 3,
            "pipeline_only_queue": 0,
            "wait_for_cache_ahead_queue": 0,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(refresh=True),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["AA", "BB", "CC", "DD"],
        max_refresh=3,
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_MANUAL_REVIEW
    )
    assert (
        gate.BLOCKING_WRITE_READY_QUEUE_TRUNCATED
        in report.blocking_reasons
    )


# ---------------------------------------------------------------------------
# 6. Decision cascade priority
# ---------------------------------------------------------------------------


def test_write_ready_wins_when_other_buckets_have_tickers(
    tmp_path: Path,
):
    """When some tickers are write-ready AND others
    are in blocked buckets, the gate still authorizes
    (the operator handles the blocked subset
    separately)."""
    fake = _FakeQueueReport(
        inspected_count=3,
        selected_refresh_count=1,
        refresh_source_cache_then_pipeline_queue=(
            _FakeQueueItem(
                ticker="GOOD",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker GOOD --write"
                ),
                write_requires_env_var=True,
            ),
        ),
        manual_stackbuilder_queue=(
            _FakeQueueItem(ticker="MAN"),
        ),
        upstream_blocked_queue=(
            _FakeQueueItem(ticker="UPS"),
        ),
        queue_counts={
            "refresh_source_cache_then_pipeline_queue": 1,
            "manual_stackbuilder_queue": 1,
            "upstream_blocked_queue": 1,
            "pipeline_only_queue": 0,
            "wait_for_cache_ahead_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["GOOD", "MAN", "UPS"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is True
    assert report.recommended_operator_action == (
        gate.ACTION_AUTHORIZE_GUARDED_WRITER
    )
    # All blocked reasons still surfaced.
    assert (
        gate.BLOCKING_STACKBUILDER_SELECTION_OR_INPUTS_MANUAL
        in report.blocking_reasons
    )
    assert (
        gate.BLOCKING_UPSTREAM_INPUTS_BLOCKED
        in report.blocking_reasons
    )


def test_active_fix_priority_over_passive_wait():
    """No write-ready tickers, mix of manual-SB +
    wait: priority is manual-SB (operator can act now)
    over wait (passive)."""
    fake = _FakeQueueReport(
        inspected_count=2,
        manual_stackbuilder_queue=(
            _FakeQueueItem(ticker="MAN"),
        ),
        wait_for_cache_ahead_queue=(
            _FakeQueueItem(ticker="WAIT"),
        ),
        queue_counts={
            "manual_stackbuilder_queue": 1,
            "wait_for_cache_ahead_queue": 1,
            "refresh_source_cache_then_pipeline_queue": 0,
            "pipeline_only_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["MAN", "WAIT"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    # Active fix wins over passive wait.
    assert report.recommended_operator_action == (
        gate.ACTION_RESOLVE_STACKBUILDER_INPUTS
    )
    # Both blockers still surfaced.
    assert (
        gate.BLOCKING_STACKBUILDER_SELECTION_OR_INPUTS_MANUAL
        in report.blocking_reasons
    )
    assert (
        gate.BLOCKING_WAITING_FOR_CACHE_AHEAD_OF_CUTOFF
        in report.blocking_reasons
    )


# ---------------------------------------------------------------------------
# 7. Advisory commands are display-only strings
# ---------------------------------------------------------------------------


def test_advisory_commands_are_strings():
    fake = _FakeQueueReport(
        inspected_count=2,
        selected_pipeline_count=1,
        selected_refresh_count=1,
        pipeline_only_queue=(
            _FakeQueueItem(
                ticker="P1",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker P1 --write"
                ),
            ),
        ),
        refresh_source_cache_then_pipeline_queue=(
            _FakeQueueItem(
                ticker="R1",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker R1 --write"
                ),
            ),
        ),
        queue_counts={
            "pipeline_only_queue": 1,
            "refresh_source_cache_then_pipeline_queue": 1,
            "wait_for_cache_ahead_queue": 0,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["P1", "R1"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert all(
        isinstance(s, str) for s in report.advisory_commands
    )
    # Pipeline-only command appears before refresh
    # command (the queue planner orders pipeline_only
    # first).
    assert report.advisory_commands == (
        "python daily_board_automation_writer.py --ticker P1 --write",
        "python daily_board_automation_writer.py --ticker R1 --write",
    )


# ---------------------------------------------------------------------------
# 8. Ranking tails pass through
# ---------------------------------------------------------------------------


def test_ranking_tails_pass_through():
    positive = ({"ticker": "BUYHI"},)
    negative = ({"ticker": "SHORTHI"},)
    low_buy = ({"ticker": "NOBUY"},)
    fake = _FakeQueueReport(
        inspected_count=3,
        positive_tail=positive,
        negative_tail=negative,
        low_buy_tail=low_buy,
        queue_counts=_truncation_dict_counts(),
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["BUYHI", "SHORTHI", "NOBUY"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.positive_tail == positive
    assert report.negative_tail == negative
    assert report.low_buy_tail == low_buy


# ---------------------------------------------------------------------------
# 9. JSON serialization stable
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips():
    fake = _FakeQueueReport(
        inspected_count=1,
        discovered_stackbuilder_ticker_count=1,
        selected_refresh_count=1,
        refresh_source_cache_then_pipeline_queue=(
            _FakeQueueItem(
                ticker="SPY",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker SPY --write"
                ),
            ),
        ),
        queue_counts=_truncation_dict_counts(),
        queue_truncation=_truncation_dict(),
    )
    # Bump the count manually for this fixture so it
    # matches the array we set above.
    fake.queue_counts[
        "refresh_source_cache_then_pipeline_queue"
    ] = 1
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake),
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    reparsed = json.loads(serialized)
    assert (
        reparsed["safe_to_authorize_writer_now"] is True
    )
    assert reparsed["recommended_operator_action"] == (
        gate.ACTION_AUTHORIZE_GUARDED_WRITER
    )
    assert reparsed["authorization_candidate_tickers"] == (
        ["SPY"]
    )
    assert reparsed["advisory_commands"] == [
        "python daily_board_automation_writer.py --ticker SPY --write",
    ]


# ---------------------------------------------------------------------------
# 10. Contract-invalid-or-unknown tickers surfaced
# ---------------------------------------------------------------------------


def test_contract_invalid_tickers_surfaced():
    """Tickers whose downstream_contract_verdict is
    NOT one of the two healthy values are flagged."""
    fake = _FakeQueueReport(
        inspected_count=3,
        wait_for_cache_ahead_queue=(
            _FakeQueueItem(
                ticker="HEALTHY",
                downstream_contract_verdict=(
                    "contract_valid_but_not_leader_eligible"
                ),
            ),
        ),
        upstream_blocked_queue=(
            _FakeQueueItem(
                ticker="BLOCKED1",
                downstream_contract_verdict="fix_cache_contract",
            ),
            _FakeQueueItem(
                ticker="BLOCKED2",
                downstream_contract_verdict=None,
            ),
        ),
        queue_counts={
            "wait_for_cache_ahead_queue": 1,
            "upstream_blocked_queue": 2,
            "refresh_source_cache_then_pipeline_queue": 0,
            "pipeline_only_queue": 0,
            "manual_stackbuilder_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["HEALTHY", "BLOCKED1", "BLOCKED2"],
        queue_planner_callable=_planner_returning(fake),
    )
    # HEALTHY's verdict is contract_valid_*; not flagged.
    # BLOCKED1 has fix_cache_contract; flagged.
    # BLOCKED2 has None; flagged.
    assert "HEALTHY" not in (
        report.contract_invalid_or_unknown_tickers
    )
    assert "BLOCKED1" in (
        report.contract_invalid_or_unknown_tickers
    )
    assert "BLOCKED2" in (
        report.contract_invalid_or_unknown_tickers
    )
    assert (
        gate.BLOCKING_CONTRACT_INVALID_OR_UNKNOWN
        in report.blocking_reasons
    )


# ---------------------------------------------------------------------------
# 11. CLI: rc=0/2/3, no SystemExit leak
# ---------------------------------------------------------------------------


def test_cli_no_ticker_source_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = gate.main([])
    assert rc == 2
    parsed = json.loads(err.getvalue().strip())
    assert parsed.get("error") == "no_ticker_source_supplied"


def test_cli_unknown_flag_returns_rc_2():
    err = io.StringIO()
    with redirect_stderr(err):
        rc = gate.main(["--not-a-flag", "x"])
    assert rc == 2


def test_cli_mutual_exclusion():
    """The CLI's argparse layer rejects multiple ticker-
    source flags as a mutually-exclusive-group error."""
    err = io.StringIO()
    with redirect_stderr(err):
        rc = gate.main([
            "--ticker", "SPY",
            "--from-stackbuilder-universe",
        ])
    assert rc == 2


def test_cli_happy_path_emits_json(tmp_path: Path):
    """CLI happy path with a stubbed planner: rc=0 and
    valid JSON to stdout. We monkey the real planner
    via the public function for this end-to-end CLI
    check by substituting an empty universe so the
    planner returns quickly without touching real
    artifacts.

    The real-cache smoke is covered by the universe
    + SPY smokes in the PR doc; CLI parsing is the
    contract under test here."""
    # We use --tickers without --from-universe so the
    # planner stays in explicit-list mode and the
    # ticker list is the one we control. The real
    # planner is called against the empty tmp_path
    # roots, so it short-circuits with empty queues.
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = gate.main([
            "--tickers", "ZZZ",
            "--cache-dir", str(tmp_path / "cache"),
            "--artifact-root", str(tmp_path / "artifacts"),
            "--stackbuilder-root",
            str(tmp_path / "stackbuilder"),
            "--signal-library-dir", str(tmp_path / "siglib"),
            "--impactsearch-output-dir",
            str(tmp_path / "impactsearch"),
            "--current-as-of-date", "2026-05-08",
        ])
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert (
        "safe_to_authorize_writer_now" in parsed
    )
    assert (
        "recommended_operator_action" in parsed
    )
    # The real planner against an empty tmp tree returns
    # an empty universe with no actionable tickers ->
    # safe=False (upstream_blocked + downstream_gap +
    # contract_invalid_or_unknown will fire for ZZZ).
    assert (
        parsed["safe_to_authorize_writer_now"] is False
    )


# ---------------------------------------------------------------------------
# 12. queue_counts and queue_truncation echoed
# ---------------------------------------------------------------------------


def test_queue_counts_and_truncation_echoed():
    qc = {
        "pipeline_only_queue": 0,
        "refresh_source_cache_then_pipeline_queue": 2,
        "wait_for_cache_ahead_queue": 1,
        "manual_stackbuilder_queue": 0,
        "upstream_blocked_queue": 0,
        "downstream_gap_queue": 0,
        "current_leader_eligible_queue": 0,
    }
    qt = _truncation_dict()
    fake = _FakeQueueReport(
        inspected_count=3,
        selected_refresh_count=2,
        refresh_source_cache_then_pipeline_queue=(
            _FakeQueueItem(
                ticker="A",
                advisory_command="cmd A",
            ),
            _FakeQueueItem(
                ticker="B",
                advisory_command="cmd B",
            ),
        ),
        wait_for_cache_ahead_queue=(
            _FakeQueueItem(ticker="W"),
        ),
        queue_counts=qc,
        queue_truncation=qt,
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["A", "B", "W"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.queue_counts == qc
    assert report.queue_truncation == qt


# ---------------------------------------------------------------------------
# 13. Persist-skip-lag pure-wait routes to wait,
#     not refresh
# ---------------------------------------------------------------------------


def test_persist_skip_lag_routes_to_wait_not_refresh():
    """SPY-shaped persist-skip-lag scenario: wait_for_
    cache_ahead_queue contains a ticker whose
    cache_cutoff_action is 'pipeline_output_lags_persist_skip'.
    Recommended action MUST be ACTION_WAIT_FOR_CACHE_AHEAD
    and MUST NOT be any refresh-related action."""
    fake = _FakeQueueReport(
        inspected_count=1,
        discovered_stackbuilder_ticker_count=248,
        wait_for_cache_ahead_queue=(
            _FakeQueueItem(
                ticker="SPY",
                recommended_action=(
                    "wait_for_cache_ahead_of_cutoff"
                ),
                cache_cutoff_action=(
                    "pipeline_output_lags_persist_skip"
                ),
                source_cache_date="2026-05-11",
            ),
        ),
        queue_counts={
            "wait_for_cache_ahead_queue": 1,
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 0,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
    )
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake),
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_WAIT_FOR_CACHE_AHEAD
    )
    # Negative pin: NOT routed to a refresh-related
    # action.
    refresh_actions = {
        gate.ACTION_AUTHORIZE_GUARDED_WRITER,
        gate.ACTION_FIX_UPSTREAM_INPUTS,
        gate.ACTION_BUILD_MISSING_DOWNSTREAM_ARTIFACTS,
    }
    assert (
        report.recommended_operator_action
        not in refresh_actions
    )


# ---------------------------------------------------------------------------
# Phase 6I-15: source-availability integration
# ---------------------------------------------------------------------------


def _wait_only_fake_report(ticker: str = "SPY") -> _FakeQueueReport:
    """Build a fake queue report that puts the given
    ticker in the wait_for_cache_ahead bucket and nothing
    else, mirroring the post-Phase-6I-13 equal-cache
    state."""
    return _FakeQueueReport(
        current_as_of_date="2026-05-12",
        inspected_count=1,
        queue_counts={
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 0,
            "wait_for_cache_ahead_queue": 1,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
        wait_for_cache_ahead_queue=(
            _FakeQueueItem(ticker=ticker),
        ),
    )


def _fake_source_callable_ready_for(*ready_tickers: str):
    """Return a fake ``source_availability_callable`` that
    emits a ``SourceAvailabilityReport``-shaped dataclass
    with the given tickers in the source-ready set."""
    # Import lazily so this helper does not force the
    # probe module to load at gate-test import time.
    import source_availability_probe as sap  # noqa: PLC0415

    def fn(tickers, *, cache_dir=None,
           current_as_of_date=None, **_):
        states_list: list[sap.SourceAvailabilityState] = []
        counts: dict[str, int] = {}
        ready: list[str] = []
        for t in tickers:
            tu = str(t).upper()
            if tu in {x.upper() for x in ready_tickers}:
                action = sap.ACTION_SOURCE_READY_FOR_REFRESH
                ready.append(tu)
            else:
                action = (
                    sap.ACTION_SOURCE_EQUAL_CUTOFF_WAIT
                )
            counts[action] = counts.get(action, 0) + 1
            states_list.append(
                sap.SourceAvailabilityState(
                    ticker=tu,
                    current_as_of_date=(
                        current_as_of_date
                        or "2026-05-12"
                    ),
                    old_cache_date_range_end="2026-05-12",
                    new_cache_date_range_end=(
                        "2026-05-13"
                        if action == sap.ACTION_SOURCE_READY_FOR_REFRESH
                        else "2026-05-12"
                    ),
                    source_ahead_of_cutoff=(
                        action == sap.ACTION_SOURCE_READY_FOR_REFRESH
                    ),
                    source_equal_to_cutoff=(
                        action == sap.ACTION_SOURCE_EQUAL_CUTOFF_WAIT
                    ),
                    source_behind_cutoff=False,
                    dry_run_attempted=True,
                    dry_run_succeeded=True,
                    provider_fetch_telemetry=None,
                    recommended_source_action=action,
                    issue_codes=(),
                ),
            )
        return sap.SourceAvailabilityReport(
            generated_at="2026-05-13T00:00:00+00:00",
            current_as_of_date=(
                current_as_of_date or "2026-05-12"
            ),
            inspected_count=len(states_list),
            states=tuple(states_list),
            counts_by_recommended_source_action=counts,
            source_ready_tickers=tuple(ready),
        )

    return fn


def test_source_availability_default_off_leaves_fields_empty():
    """Default ``include_source_availability=False`` must
    not invoke the probe; the new fields stay empty even
    when the gate is in the wait state."""
    fake_q = _wait_only_fake_report("SPY")
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake_q),
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_WAIT_FOR_CACHE_AHEAD
    )
    assert report.source_availability_checked is False
    assert report.source_ready_tickers == ()
    assert report.source_wait_tickers == ()
    assert report.source_manual_review_tickers == ()
    assert report.source_availability_by_ticker == {}


def test_equal_cache_plus_source_ready_advisory_action():
    """When the gate would otherwise emit
    ``wait_for_cache_ahead_of_cutoff`` AND the source-
    availability probe reports the wait ticker as
    ``source_ready_for_refresh``, the gate's
    recommended_operator_action upgrades to
    ``source_ready_for_supervised_refresh`` -- BUT
    ``safe_to_authorize_writer_now`` STAYS False (the
    source-availability surface NEVER flips safety on
    its own).

    Phase 6I-15 amendment also pins that the new
    advisory action is included in ``ALL_ACTIONS``."""
    fake_q = _wait_only_fake_report("SPY")
    source_fn = _fake_source_callable_ready_for("SPY")
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake_q),
        include_source_availability=True,
        source_availability_callable=source_fn,
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_SOURCE_READY_FOR_SUPERVISED_REFRESH
    )
    # The emitted action must be in ALL_ACTIONS so
    # downstream consumers (flow audit, ops tooling)
    # can enumerate every possible verdict.
    assert (
        report.recommended_operator_action
        in gate.ALL_ACTIONS
    )
    assert report.source_availability_checked is True
    assert report.source_ready_tickers == ("SPY",)
    assert report.source_wait_tickers == ()
    assert report.source_manual_review_tickers == ()
    assert (
        report.source_availability_by_ticker["SPY"]
        == "source_ready_for_refresh"
    )


def test_all_actions_includes_every_action_constant():
    """``ALL_ACTIONS`` must enumerate every
    ``ACTION_*`` string the gate can emit, including the
    Phase 6I-15
    ``ACTION_SOURCE_READY_FOR_SUPERVISED_REFRESH``
    advisory. Discover the action constants reflectively
    (any module-level name starting with ``ACTION_``
    whose value is a string is in scope) so a future
    contributor cannot add a new action constant without
    also updating ``ALL_ACTIONS``."""
    discovered: dict[str, str] = {}
    for name in dir(gate):
        if not name.startswith("ACTION_"):
            continue
        value = getattr(gate, name)
        if isinstance(value, str):
            discovered[name] = value

    # Sanity: the discovery must include the Phase 6I-9
    # baseline actions + the Phase 6I-15 advisory.
    expected_subset = {
        "ACTION_AUTHORIZE_GUARDED_WRITER",
        "ACTION_WAIT_FOR_CACHE_AHEAD",
        "ACTION_RESOLVE_STACKBUILDER_INPUTS",
        "ACTION_FIX_UPSTREAM_INPUTS",
        "ACTION_BUILD_MISSING_DOWNSTREAM_ARTIFACTS",
        "ACTION_ALREADY_CURRENT",
        "ACTION_MANUAL_REVIEW",
        "ACTION_SOURCE_READY_FOR_SUPERVISED_REFRESH",
    }
    missing = expected_subset - set(discovered.keys())
    assert not missing, (
        "Discovery test expected these ACTION_* "
        f"constants but did not find them: {missing!r}"
    )

    # Every discovered ACTION_* value must be in
    # ALL_ACTIONS. This is the load-bearing assertion
    # the Codex audit asked for.
    for name, value in discovered.items():
        assert value in gate.ALL_ACTIONS, (
            f"{name}={value!r} is missing from "
            "ALL_ACTIONS; ALL_ACTIONS must enumerate "
            "every action constant the gate can emit"
        )

    # Phase 6I-15 advisory pin: the new constant is
    # specifically the one the prior Codex audit caught
    # as missing.
    assert (
        gate.ACTION_SOURCE_READY_FOR_SUPERVISED_REFRESH
        in gate.ALL_ACTIONS
    )


def test_equal_cache_plus_source_not_ready_keeps_wait_action():
    """When source-availability says NONE of the wait
    tickers is ready, the gate keeps the original
    ``wait_for_cache_ahead_of_cutoff`` action; the
    advisory upgrade does NOT fire."""
    fake_q = _wait_only_fake_report("SPY")
    # Source probe returns nobody ready.
    source_fn = _fake_source_callable_ready_for()
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake_q),
        include_source_availability=True,
        source_availability_callable=source_fn,
    )
    assert report.safe_to_authorize_writer_now is False
    assert report.recommended_operator_action == (
        gate.ACTION_WAIT_FOR_CACHE_AHEAD
    )
    assert report.source_availability_checked is True
    assert report.source_ready_tickers == ()
    assert report.source_wait_tickers == ("SPY",)


def test_safe_gate_path_is_not_reduced_by_source_availability():
    """When the gate is already safe (write-ready
    candidates exist), enabling the source-availability
    probe must NOT downgrade safety. The probe is not
    even consulted when ``wait_for_cache_ahead_tickers``
    is empty (no wait tickers to probe)."""
    fake_q = _FakeQueueReport(
        current_as_of_date="2026-05-12",
        inspected_count=1,
        selected_refresh_count=1,
        queue_counts={
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 1,
            "wait_for_cache_ahead_queue": 0,
            "manual_stackbuilder_queue": 0,
            "upstream_blocked_queue": 0,
            "downstream_gap_queue": 0,
            "current_leader_eligible_queue": 0,
        },
        queue_truncation=_truncation_dict(),
        refresh_source_cache_then_pipeline_queue=(
            _FakeQueueItem(
                ticker="SPY",
                advisory_command=(
                    "python daily_board_automation_writer.py"
                    " --ticker SPY --write"
                ),
            ),
        ),
    )

    def must_not_be_called(*a, **k):
        raise AssertionError(
            "source-availability probe must NOT be "
            "consulted when there are no "
            "wait_for_cache_ahead_tickers"
        )

    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake_q),
        include_source_availability=True,
        source_availability_callable=must_not_be_called,
    )
    assert report.safe_to_authorize_writer_now is True
    assert report.recommended_operator_action == (
        gate.ACTION_AUTHORIZE_GUARDED_WRITER
    )
    # Probe was never called -> empty source-availability
    # fields (still emitted for backward-compatible JSON
    # shape).
    assert report.source_availability_checked is False
    assert report.source_ready_tickers == ()


def test_source_availability_fields_serialize_in_json():
    """The new fields must appear in to_json_dict()
    output so downstream consumers (flow audit, ops
    tooling) can read them without inspecting the
    dataclass directly."""
    fake_q = _wait_only_fake_report("SPY")
    source_fn = _fake_source_callable_ready_for("SPY")
    report = gate.evaluate_supervised_run_gate(
        tickers=["SPY"],
        queue_planner_callable=_planner_returning(fake_q),
        include_source_availability=True,
        source_availability_callable=source_fn,
    )
    payload = report.to_json_dict()
    assert payload["source_availability_checked"] is True
    assert payload["source_ready_tickers"] == ["SPY"]
    assert payload["source_wait_tickers"] == []
    assert payload["source_manual_review_tickers"] == []
    assert (
        payload["source_availability_by_ticker"]["SPY"]
        == "source_ready_for_refresh"
    )
    # Round-trip via json.dumps to confirm no
    # non-serializable types snuck in.
    json.loads(json.dumps(payload))
