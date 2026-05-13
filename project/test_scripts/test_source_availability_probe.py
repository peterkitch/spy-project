"""Phase 6I-15 tests for source_availability_probe.

Pins:

  - Forbidden-imports static guard (no writer / pipeline
    runner / live engine / yfinance / dash / subprocess).
  - Per-ticker verdict for ahead / equal / behind / missing
    / unparseable.
  - Dry-run exception returns a structured manual-review
    outcome with no exception leak.
  - provider_fetch_telemetry pass-through from refresher
    result (both dict and to_json_dict() shapes).
  - Many-ticker report shape + source_ready_tickers.
  - CLI rc=0 / rc=2 / no SystemExit leak.
"""
from __future__ import annotations

import ast
import json
import sys
from io import StringIO
from pathlib import Path
from typing import Any, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import source_availability_probe as sap  # noqa: E402


# ---------------------------------------------------------------------------
# Fake refresher result
# ---------------------------------------------------------------------------


class _FakeRefreshResult:
    """Minimal stand-in for SignalEngineRefreshResult that
    only exposes the fields source_availability_probe
    actually reads."""

    def __init__(
        self,
        *,
        old: Optional[str] = None,
        new: Optional[str] = None,
        provider_fetch_telemetry: Any = None,
    ):
        self.old_cache_date_range_end = old
        self.new_cache_date_range_end = new
        self.provider_fetch_telemetry = (
            provider_fetch_telemetry
        )


def _fake_refresher_factory(result: _FakeRefreshResult):
    def fn(ticker, *, cache_dir=None, status_dir=None,
           write=False, current_as_of_date=None, **_):
        assert write is False, (
            "source_availability_probe must always call "
            "the refresher with write=False"
        )
        return result
    return fn


def _raising_refresher(ticker, **_):
    raise RuntimeError("simulated upstream failure")


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_probe_module_has_no_forbidden_imports():
    """The probe must not pull in any writer / pipeline
    runner / live engine / yfinance / dash / subprocess
    module at top level. The Phase 6E-5 refresher IS
    allowed (the probe lazily delegates to its write=False
    code path)."""
    tree = ast.parse(
        Path(sap.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "daily_board_automation_writer",
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
        f"forbidden import in probe: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. Per-ticker verdicts
# ---------------------------------------------------------------------------


def test_new_cache_strictly_ahead_returns_source_ready():
    fake = _fake_refresher_factory(
        _FakeRefreshResult(
            old="2026-05-12",
            new="2026-05-13",
        ),
    )
    state = sap.evaluate_source_availability(
        "SPY",
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    assert state.ticker == "SPY"
    assert state.current_as_of_date == "2026-05-12"
    assert state.new_cache_date_range_end == "2026-05-13"
    assert state.source_ahead_of_cutoff is True
    assert state.source_equal_to_cutoff is False
    assert state.source_behind_cutoff is False
    assert state.dry_run_attempted is True
    assert state.dry_run_succeeded is True
    assert state.recommended_source_action == (
        sap.ACTION_SOURCE_READY_FOR_REFRESH
    )
    assert state.issue_codes == ()


def test_new_cache_equal_to_cutoff_returns_equal_cutoff_wait():
    fake = _fake_refresher_factory(
        _FakeRefreshResult(
            old="2026-05-11",
            new="2026-05-12",
        ),
    )
    state = sap.evaluate_source_availability(
        "SPY",
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    assert state.source_equal_to_cutoff is True
    assert state.source_ahead_of_cutoff is False
    assert state.source_behind_cutoff is False
    assert state.recommended_source_action == (
        sap.ACTION_SOURCE_EQUAL_CUTOFF_WAIT
    )


def test_new_cache_behind_cutoff_returns_behind_cutoff_wait():
    fake = _fake_refresher_factory(
        _FakeRefreshResult(
            old="2026-05-10",
            new="2026-05-11",
        ),
    )
    state = sap.evaluate_source_availability(
        "SPY",
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    assert state.source_behind_cutoff is True
    assert state.source_ahead_of_cutoff is False
    assert state.source_equal_to_cutoff is False
    assert state.recommended_source_action == (
        sap.ACTION_SOURCE_BEHIND_CUTOFF_WAIT
    )


# ---------------------------------------------------------------------------
# 3. Issue codes
# ---------------------------------------------------------------------------


def test_missing_new_cache_date_returns_manual_review():
    fake = _fake_refresher_factory(
        _FakeRefreshResult(old="2026-05-11", new=None),
    )
    state = sap.evaluate_source_availability(
        "SPY",
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    assert state.new_cache_date_range_end is None
    assert state.dry_run_succeeded is True
    assert state.recommended_source_action == (
        sap.ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW
    )
    assert (
        sap.ISSUE_SOURCE_MISSING_NEW_CACHE_DATE
        in state.issue_codes
    )


def test_unparseable_new_cache_date_returns_manual_review():
    fake = _fake_refresher_factory(
        _FakeRefreshResult(
            old="2026-05-11",
            new="not-a-date",
        ),
    )
    state = sap.evaluate_source_availability(
        "SPY",
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    assert state.new_cache_date_range_end == "not-a-date"
    assert state.recommended_source_action == (
        sap.ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW
    )
    assert (
        sap.ISSUE_SOURCE_UNPARSEABLE_NEW_CACHE_DATE
        in state.issue_codes
    )


def test_dry_run_exception_returns_structured_manual_review():
    """The refresher dry-run raising must produce a
    structured outcome (manual review + dry_run_failed
    issue code), NOT propagate the exception out of the
    probe."""
    state = sap.evaluate_source_availability(
        "SPY",
        current_as_of_date="2026-05-12",
        refresher_callable=_raising_refresher,
    )
    assert state.dry_run_attempted is True
    assert state.dry_run_succeeded is False
    assert state.new_cache_date_range_end is None
    assert state.recommended_source_action == (
        sap.ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW
    )
    assert (
        sap.ISSUE_SOURCE_REFRESH_DRY_RUN_FAILED
        in state.issue_codes
    )


# ---------------------------------------------------------------------------
# 4. Provider telemetry pass-through
# ---------------------------------------------------------------------------


def test_provider_fetch_telemetry_dict_is_preserved():
    """A fake refresher that returns telemetry as a plain
    dict (the shape Phase 6I-12 writer-side tests use)
    should have that dict surfaced verbatim on the
    probe's state."""
    telemetry_dict = {
        "provider_name": "fake_yfinance_test_double",
        "fetch_attempted": True,
        "fetch_succeeded": True,
        "ticker": "SPY",
        "rows": 7,
        "date_range_start": "2024-12-30",
        "date_range_end": "2026-05-13",
        "elapsed_seconds": 0.01,
        "error": None,
    }
    fake = _fake_refresher_factory(
        _FakeRefreshResult(
            old="2026-05-12",
            new="2026-05-13",
            provider_fetch_telemetry=telemetry_dict,
        ),
    )
    state = sap.evaluate_source_availability(
        "SPY",
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    assert state.provider_fetch_telemetry == telemetry_dict
    j = sap._state_to_json_dict(state)
    assert j["provider_fetch_telemetry"] == telemetry_dict


def test_provider_fetch_telemetry_dataclass_is_preserved():
    """A fake refresher that returns telemetry as an
    object exposing ``to_json_dict()`` (the real
    ``ProviderFetchTelemetry`` dataclass's shape) should
    have the same dict surfaced verbatim on the probe's
    state."""
    class _T:
        def to_json_dict(self):
            return {
                "provider_name": "yfinance",
                "fetch_attempted": True,
                "fetch_succeeded": True,
                "ticker": "SPY",
                "rows": 42,
                "date_range_start": "2000-01-03",
                "date_range_end": "2026-05-13",
                "elapsed_seconds": 4.2,
                "error": None,
            }
    fake = _fake_refresher_factory(
        _FakeRefreshResult(
            old="2026-05-12",
            new="2026-05-13",
            provider_fetch_telemetry=_T(),
        ),
    )
    state = sap.evaluate_source_availability(
        "SPY",
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    assert state.provider_fetch_telemetry is not None
    assert state.provider_fetch_telemetry["rows"] == 42
    assert state.provider_fetch_telemetry["error"] is None


# ---------------------------------------------------------------------------
# 5. Many-ticker report shape
# ---------------------------------------------------------------------------


def test_many_ticker_report_aggregates_source_ready_set():
    """A multi-ticker probe should aggregate by-action
    counts AND surface a sorted-by-position
    source_ready_tickers tuple naming exactly the
    tickers whose probe verdict is source_ready_for_refresh."""

    def fake(ticker, **_):
        # Per-ticker outcomes: SPY ahead, AAPL equal,
        # MSFT behind, BAD missing-date.
        if ticker == "SPY":
            return _FakeRefreshResult(
                old="2026-05-12", new="2026-05-13",
            )
        if ticker == "AAPL":
            return _FakeRefreshResult(
                old="2026-05-11", new="2026-05-12",
            )
        if ticker == "MSFT":
            return _FakeRefreshResult(
                old="2026-05-09", new="2026-05-10",
            )
        return _FakeRefreshResult(
            old=None, new=None,
        )

    report = sap.evaluate_source_availability_many(
        ["SPY", "AAPL", "MSFT", "BAD"],
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    assert report.inspected_count == 4
    by_action = report.counts_by_recommended_source_action
    assert by_action[sap.ACTION_SOURCE_READY_FOR_REFRESH] == 1
    assert by_action[sap.ACTION_SOURCE_EQUAL_CUTOFF_WAIT] == 1
    assert by_action[sap.ACTION_SOURCE_BEHIND_CUTOFF_WAIT] == 1
    assert by_action[
        sap.ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW
    ] == 1
    assert report.source_ready_tickers == ("SPY",)


def test_to_json_dict_round_trips():
    fake = _fake_refresher_factory(
        _FakeRefreshResult(
            old="2026-05-12", new="2026-05-13",
        ),
    )
    report = sap.evaluate_source_availability_many(
        ["SPY"],
        current_as_of_date="2026-05-12",
        refresher_callable=fake,
    )
    payload = report.to_json_dict()
    # Round-trip via json.dumps to confirm serialization
    # stays clean.
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored["inspected_count"] == 1
    assert restored["source_ready_tickers"] == ["SPY"]
    assert (
        restored["states"][0]["recommended_source_action"]
        == sap.ACTION_SOURCE_READY_FOR_REFRESH
    )


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


def test_cli_no_ticker_returns_rc_2(capsys):
    """No --ticker / --tickers supplied should return rc=2
    without leaking SystemExit."""
    rc = sap.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "no_ticker_source_supplied" in captured.err


def test_cli_unknown_flag_returns_rc_2(capsys):
    rc = sap.main(["--no-such-flag"])
    assert rc == 2


def test_cli_happy_path_emits_json(monkeypatch, capsys):
    """Drive the CLI through main(argv=...) with the real
    refresher monkeypatched to a fake at the source-probe
    module's import site so no network call happens."""

    def fake_evaluate_many(tickers, **_):
        return sap.SourceAvailabilityReport(
            generated_at="2026-05-13T00:00:00+00:00",
            current_as_of_date="2026-05-12",
            inspected_count=1,
            states=(
                sap.SourceAvailabilityState(
                    ticker="SPY",
                    current_as_of_date="2026-05-12",
                    old_cache_date_range_end="2026-05-12",
                    new_cache_date_range_end="2026-05-13",
                    source_ahead_of_cutoff=True,
                    source_equal_to_cutoff=False,
                    source_behind_cutoff=False,
                    dry_run_attempted=True,
                    dry_run_succeeded=True,
                    provider_fetch_telemetry=None,
                    recommended_source_action=(
                        sap.ACTION_SOURCE_READY_FOR_REFRESH
                    ),
                    issue_codes=(),
                ),
            ),
            counts_by_recommended_source_action={
                sap.ACTION_SOURCE_READY_FOR_REFRESH: 1,
            },
            source_ready_tickers=("SPY",),
        )

    monkeypatch.setattr(
        sap,
        "evaluate_source_availability_many",
        fake_evaluate_many,
    )
    rc = sap.main(["--ticker", "SPY"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["source_ready_tickers"] == ["SPY"]
    assert (
        payload["states"][0]["recommended_source_action"]
        == sap.ACTION_SOURCE_READY_FOR_REFRESH
    )


def test_cli_no_systemexit_leak_on_argparse_error():
    """argparse's SystemExit must be caught inside main();
    callers should observe an int return code, not a raised
    SystemExit."""
    # argparse raises SystemExit on unknown args, but main
    # has a try/except SystemExit -> return code path.
    rc_seen = None
    try:
        rc_seen = sap.main(["--ticker"])
    except SystemExit:
        rc_seen = "leaked"
    # --ticker with no value is an argparse error -> rc=2.
    assert rc_seen == 2
