"""Phase 6I-33 tests for the source-refresh readiness module.

Pins:

  * No raw ``pickle.load`` anywhere in the module.
  * No yfinance / dash / subprocess / production-writer
    imports at top level.
  * The module never sets ``PRJCT9_AUTOMATION_WRITE_AUTH``.
  * No ``--write`` or ``write=True`` ever passed to the
    callable seams.
  * Five-class classification taxonomy:
      already_cache_ready / source_ready_for_refresh /
      source_equal_cutoff_wait / source_behind_or_error /
      manual_blocker.
  * Aggregate ``refresh_candidate_ready=True`` ONLY when
    every ticker classifies into the first two classes.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import signal_library_source_refresh_readiness as readiness  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _make_cache_state(
    ticker: str,
    *,
    ahead=False, equal=False, behind=False,
    cache_date_range_end="2026-05-12",
    current_as_of_date="2026-05-14",
):
    return {
        "ticker": ticker,
        "cache_exists": True,
        "cache_date_range_end": cache_date_range_end,
        "current_as_of_date": current_as_of_date,
        "cache_ahead_of_cutoff": bool(ahead),
        "cache_equal_to_cutoff": bool(equal),
        "cache_behind_cutoff": bool(behind),
        "recommended_operator_action": "refresh_source_cache",
        "issue_codes": [],
    }


def _make_source_state(
    ticker: str,
    *,
    ahead=False, equal=False, behind=False,
    new_end=None,
    fetch_attempted=True,
    fetch_succeeded=True,
    issue_codes=(),
):
    return {
        "ticker": ticker,
        "source_ahead_of_cutoff": bool(ahead),
        "source_equal_to_cutoff": bool(equal),
        "source_behind_cutoff": bool(behind),
        "new_cache_date_range_end": new_end,
        "issue_codes": list(issue_codes),
        "provider_fetch_telemetry": {
            "fetch_attempted": bool(fetch_attempted),
            "fetch_succeeded": bool(fetch_succeeded),
            "ticker": ticker,
        },
    }


def _build_cache_probe(states_by_ticker):
    def probe(tickers, *, cache_dir, current_as_of_date):
        return {
            "current_as_of_date": current_as_of_date or "2026-05-14",
            "states": [
                states_by_ticker[t] for t in tickers
                if t in states_by_ticker
            ],
            "ready_tickers": [
                t for t in tickers
                if states_by_ticker.get(t, {}).get(
                    "cache_ahead_of_cutoff", False,
                )
            ],
        }
    return probe


def _build_source_probe(states_by_ticker):
    def probe(tickers, *, cache_dir, current_as_of_date):
        return {
            "states": [
                states_by_ticker[t] for t in tickers
                if t in states_by_ticker
            ],
        }
    return probe


# ---------------------------------------------------------------------------
# 1. All tickers source_ready_for_refresh -> aggregate ready
# ---------------------------------------------------------------------------


def test_all_tickers_source_ready_yields_aggregate_ready():
    tickers = ["SPY", "PRGO"]
    cache_states = {
        t: _make_cache_state(t, behind=True) for t in tickers
    }
    source_states = {
        t: _make_source_state(
            t, ahead=True, new_end="2026-05-14",
        )
        for t in tickers
    }
    report = readiness.evaluate_source_refresh_readiness(
        tickers,
        cache_cutoff_probe_callable=_build_cache_probe(
            cache_states,
        ),
        source_availability_probe_callable=_build_source_probe(
            source_states,
        ),
    )
    assert report.refresh_candidate_ready is True
    assert (
        report.counts_by_classification.get(
            readiness.CLASS_SOURCE_READY_FOR_REFRESH, 0,
        ) == 2
    )
    assert (
        report.recommended_next_action
        == "ready_for_supervised_refresh"
    )
    assert report.aggregate_blocker_reasons == ()


# ---------------------------------------------------------------------------
# 2. All tickers already_cache_ready -> aggregate ready, no refresh needed
# ---------------------------------------------------------------------------


def test_all_already_cache_ready_yields_no_refresh_needed():
    tickers = ["SPY"]
    cache_states = {
        "SPY": _make_cache_state("SPY", ahead=True),
    }
    source_states = {
        # source-availability probe still runs; it's allowed
        # to disagree (e.g. cache is ahead from a fresh
        # standalone refresh while yfinance is mid-day).
        "SPY": _make_source_state(
            "SPY", equal=True, new_end="2026-05-14",
        ),
    }
    report = readiness.evaluate_source_refresh_readiness(
        tickers,
        cache_cutoff_probe_callable=_build_cache_probe(
            cache_states,
        ),
        source_availability_probe_callable=_build_source_probe(
            source_states,
        ),
    )
    assert report.refresh_candidate_ready is True
    assert (
        report.counts_by_classification.get(
            readiness.CLASS_ALREADY_CACHE_READY, 0,
        ) == 1
    )
    assert (
        report.recommended_next_action == "no_refresh_needed"
    )


# ---------------------------------------------------------------------------
# 3. One ticker source_equal_cutoff_wait -> aggregate NOT ready
# ---------------------------------------------------------------------------


def test_one_source_equal_cutoff_wait_demotes_aggregate():
    tickers = ["SPY", "PRGO"]
    cache_states = {
        t: _make_cache_state(t, behind=True) for t in tickers
    }
    source_states = {
        "SPY": _make_source_state(
            "SPY", ahead=True, new_end="2026-05-14",
        ),
        "PRGO": _make_source_state(
            "PRGO", equal=True, new_end="2026-05-13",
        ),
    }
    report = readiness.evaluate_source_refresh_readiness(
        tickers,
        cache_cutoff_probe_callable=_build_cache_probe(
            cache_states,
        ),
        source_availability_probe_callable=_build_source_probe(
            source_states,
        ),
    )
    assert report.refresh_candidate_ready is False
    assert any(
        s.classification == readiness.CLASS_SOURCE_EQUAL_CUTOFF_WAIT
        for s in report.per_ticker_states
    )
    assert (
        "PRGO:source_equal_cutoff_wait"
        in report.aggregate_blocker_reasons
    )
    assert (
        report.recommended_next_action == "wait_or_resolve_blockers"
    )


# ---------------------------------------------------------------------------
# 4. One provider error -> aggregate NOT ready
# ---------------------------------------------------------------------------


def test_one_provider_fetch_failed_demotes_aggregate():
    tickers = ["SPY", "TEF"]
    cache_states = {
        t: _make_cache_state(t, behind=True) for t in tickers
    }
    source_states = {
        "SPY": _make_source_state(
            "SPY", ahead=True, new_end="2026-05-14",
        ),
        "TEF": _make_source_state(
            "TEF",
            fetch_attempted=True,
            fetch_succeeded=False,
            issue_codes=["provider_fetch_failed"],
        ),
    }
    report = readiness.evaluate_source_refresh_readiness(
        tickers,
        cache_cutoff_probe_callable=_build_cache_probe(
            cache_states,
        ),
        source_availability_probe_callable=_build_source_probe(
            source_states,
        ),
    )
    assert report.refresh_candidate_ready is False
    tef_state = next(
        s for s in report.per_ticker_states if s.ticker == "TEF"
    )
    assert (
        tef_state.classification
        == readiness.CLASS_SOURCE_BEHIND_OR_ERROR
    )
    assert "provider_fetch_failed" in tef_state.notes


# ---------------------------------------------------------------------------
# 5. Cache-ahead + source-state-missing -> still classifies as cache ready
# ---------------------------------------------------------------------------


def test_cache_ahead_overrides_missing_source_state():
    """A ticker whose cache is already strictly ahead of the
    cutoff must classify as already_cache_ready regardless
    of whether the source-availability probe returned a
    state for it. This protects against transient source-
    probe failures masking a perfectly fresh cache."""
    tickers = ["SPY"]
    cache_states = {
        "SPY": _make_cache_state("SPY", ahead=True),
    }
    source_states: dict[str, Any] = {}  # empty -> no SPY state
    report = readiness.evaluate_source_refresh_readiness(
        tickers,
        cache_cutoff_probe_callable=_build_cache_probe(
            cache_states,
        ),
        source_availability_probe_callable=_build_source_probe(
            source_states,
        ),
    )
    assert report.refresh_candidate_ready is True
    assert (
        report.per_ticker_states[0].classification
        == readiness.CLASS_ALREADY_CACHE_READY
    )


# ---------------------------------------------------------------------------
# 6. Cache-behind + source-state-missing -> manual_blocker
# ---------------------------------------------------------------------------


def test_source_state_missing_yields_manual_blocker():
    tickers = ["SPY"]
    cache_states = {
        "SPY": _make_cache_state("SPY", behind=True),
    }
    source_states: dict[str, Any] = {}
    report = readiness.evaluate_source_refresh_readiness(
        tickers,
        cache_cutoff_probe_callable=_build_cache_probe(
            cache_states,
        ),
        source_availability_probe_callable=_build_source_probe(
            source_states,
        ),
    )
    assert report.refresh_candidate_ready is False
    state = report.per_ticker_states[0]
    assert (
        state.classification == readiness.CLASS_MANUAL_BLOCKER
    )
    assert "source_state_missing" in state.notes


# ---------------------------------------------------------------------------
# 7. Module never sets PRJCT9_AUTOMATION_WRITE_AUTH
# ---------------------------------------------------------------------------


def test_module_never_sets_auth_env(monkeypatch):
    monkeypatch.delenv(
        "PRJCT9_AUTOMATION_WRITE_AUTH", raising=False,
    )
    tickers = ["SPY"]
    cache_states = {
        "SPY": _make_cache_state("SPY", behind=True),
    }
    source_states = {
        "SPY": _make_source_state(
            "SPY", ahead=True, new_end="2026-05-14",
        ),
    }
    readiness.evaluate_source_refresh_readiness(
        tickers,
        cache_cutoff_probe_callable=_build_cache_probe(
            cache_states,
        ),
        source_availability_probe_callable=_build_source_probe(
            source_states,
        ),
    )
    assert (
        os.environ.get("PRJCT9_AUTOMATION_WRITE_AUTH") is None
    )


# ---------------------------------------------------------------------------
# 8. Module passes neither --write nor write=True to any seam
# ---------------------------------------------------------------------------


def test_module_does_not_call_seams_with_write_true():
    log: list[dict[str, Any]] = []

    def recording_cache(tickers, *, cache_dir, current_as_of_date):
        log.append({
            "name": "cache_probe",
            "tickers": list(tickers),
            "kwargs": {
                "cache_dir": cache_dir,
                "current_as_of_date": current_as_of_date,
            },
        })
        return {"current_as_of_date": "2026-05-14", "states": []}

    def recording_source(
        tickers, *, cache_dir, current_as_of_date,
    ):
        log.append({
            "name": "source_probe",
            "tickers": list(tickers),
            "kwargs": {
                "cache_dir": cache_dir,
                "current_as_of_date": current_as_of_date,
            },
        })
        return {"states": []}

    readiness.evaluate_source_refresh_readiness(
        ["SPY"],
        cache_cutoff_probe_callable=recording_cache,
        source_availability_probe_callable=recording_source,
    )
    assert len(log) == 2
    for entry in log:
        # Neither writer-related kwarg should ever appear.
        assert "write" not in entry["kwargs"]
        assert "--write" not in str(entry)


# ---------------------------------------------------------------------------
# 9. CLI rc=2 paths
# ---------------------------------------------------------------------------


def test_cli_missing_tickers_returns_rc_2(capsys):
    rc = readiness.main(["--tickers", ""])
    assert rc == 2


def test_cli_unknown_flag_returns_rc_2():
    rc = readiness.main(["--no-such-flag"])
    assert rc == 2


# ---------------------------------------------------------------------------
# 10. Static guards: no raw pickle.load
# ---------------------------------------------------------------------------


def test_module_no_raw_pickle_load():
    src = Path(readiness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                base = func.value
                if (
                    isinstance(base, ast.Name)
                    and base.id == "pickle"
                    and func.attr == "load"
                ):
                    raise AssertionError(
                        "module calls pickle.load() "
                        f"at line {node.lineno}"
                    )


# ---------------------------------------------------------------------------
# 11. Static guards: no forbidden top-level imports
# ---------------------------------------------------------------------------


def test_module_no_forbidden_top_level_imports():
    src = Path(readiness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_first = {
        "yfinance", "dash", "subprocess",
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
        "spymaster", "trafficflow", "stackbuilder",
        "onepass", "impactsearch", "confluence",
        "cross_ticker_confluence", "daily_signal_board",
    }
    found_top: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_top.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_top.append(node.module)
    bad = [
        m for m in found_top
        if m.split(".")[0] in forbidden_first
    ]
    assert not bad, f"forbidden top-level imports: {bad!r}"


# ---------------------------------------------------------------------------
# 12. Static guard: AST has no write=True keyword arg
# ---------------------------------------------------------------------------


def test_module_ast_has_no_write_true_kwarg():
    src = Path(readiness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "write":
                    val = kw.value
                    if (
                        isinstance(val, ast.Constant)
                        and val.value is True
                    ):
                        offenders.append(node.lineno)
    assert not offenders, (
        f"module passes write=True at line(s) {offenders!r}"
    )
