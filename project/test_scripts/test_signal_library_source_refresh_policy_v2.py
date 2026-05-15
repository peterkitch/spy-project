"""Phase 6I-43 tests: source-refresh policy v2 + invalid-
member handling plan for the SPY K-universe.

Pins:

  1. source_date > cutoff -> source_strictly_ahead_refreshable.
  2. source_date == cutoff -> source_equal_cutoff_wait by
     default.
  3. source_date == cutoff + allow_equal_cutoff_after_close
     -> source_equal_cutoff_publishable.
  4. TEF-style null source date / "possibly delisted"
     telemetry -> invalid_or_delisted under
     warn_and_exclude.
  5. Invalid ticker excluded from candidate command.
  6. Invalid ticker appears in warning_members /
     invalid_tickers.
  7. refresh_candidate_ready=False when equal-cutoff not
     allowed.
  8. refresh_candidate_ready=True when equal-cutoff
     allowed AND only invalid tickers are excluded.
  9. Candidate command includes ONLY non-invalid tickers.
 10. Candidate command carries NO
     PRJCT9_AUTOMATION_WRITE_AUTH wording.
 11. No source refresh write, no production data write.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import signal_library_source_refresh_policy_v2 as pv2  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers -- inject probe outputs the planner consumes
# ---------------------------------------------------------------------------


def _cache_state(
    *,
    ticker: str,
    cache_exists: bool = True,
    cache_date_range_end: str | None = "2026-05-12",
    cache_ahead_of_cutoff: bool = False,
    cache_equal_to_cutoff: bool = False,
    cache_behind_cutoff: bool = True,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "cache_exists": cache_exists,
        "cache_date_range_end": cache_date_range_end,
        "current_as_of_date": "2026-05-14",
        "cache_ahead_of_cutoff": cache_ahead_of_cutoff,
        "cache_equal_to_cutoff": cache_equal_to_cutoff,
        "cache_behind_cutoff": cache_behind_cutoff,
    }


def _source_state(
    *,
    ticker: str,
    source_ahead_of_cutoff: bool = False,
    source_equal_to_cutoff: bool = False,
    source_behind_cutoff: bool = False,
    new_cache_date_range_end: str | None = None,
    provider_fetch_telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "source_ahead_of_cutoff": source_ahead_of_cutoff,
        "source_equal_to_cutoff": source_equal_to_cutoff,
        "source_behind_cutoff": source_behind_cutoff,
        "new_cache_date_range_end": (
            new_cache_date_range_end
        ),
        "provider_fetch_telemetry": (
            provider_fetch_telemetry
        ),
        "issue_codes": [],
    }


def _fake_probes(
    *,
    cache_states: list[dict[str, Any]],
    source_states: list[dict[str, Any]],
):
    """Return (cache_probe, source_probe) callables that
    inject the supplied probe outputs."""
    def cache_probe(tickers, *, cache_dir, current_as_of_date):
        return {
            "current_as_of_date": current_as_of_date,
            "states": list(cache_states),
        }
    def source_probe(tickers, *, cache_dir, current_as_of_date):
        return {
            "states": list(source_states),
        }
    return cache_probe, source_probe


def _telemetry_ok(
    *,
    rows: int = 8000,
    date_range_end: str = "2026-05-14",
) -> dict[str, Any]:
    return {
        "provider_name": "yfinance",
        "fetch_attempted": True,
        "fetch_succeeded": True,
        "rows": rows,
        "date_range_start": "1993-01-29",
        "date_range_end": date_range_end,
        "elapsed_seconds": 0.5,
        "error": None,
    }


def _telemetry_delisted() -> dict[str, Any]:
    return {
        "provider_name": "yfinance",
        "fetch_attempted": True,
        "fetch_succeeded": False,
        "rows": 0,
        "date_range_start": None,
        "date_range_end": None,
        "elapsed_seconds": 0.25,
        "error": None,
        "warning": (
            "possibly delisted; no price data found"
        ),
    }


# ---------------------------------------------------------------------------
# 1. source > cutoff -> source_strictly_ahead_refreshable
# ---------------------------------------------------------------------------


def test_source_strictly_ahead_classifies_refreshable():
    cache_probe, source_probe = _fake_probes(
        cache_states=[
            _cache_state(ticker="AAA"),
        ],
        source_states=[
            _source_state(
                ticker="AAA",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-15",
                provider_fetch_telemetry=_telemetry_ok(
                    date_range_end="2026-05-15",
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.per_ticker_states[
        0
    ].classification == "source_strictly_ahead_refreshable"
    assert report.refresh_candidate_ready is True


# ---------------------------------------------------------------------------
# 2. source == cutoff stays wait by default
# ---------------------------------------------------------------------------


def test_source_equal_cutoff_defaults_to_wait():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[
            _source_state(
                ticker="AAA",
                source_equal_to_cutoff=True,
                new_cache_date_range_end="2026-05-14",
                provider_fetch_telemetry=_telemetry_ok(),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.per_ticker_states[
        0
    ].classification == "source_equal_cutoff_wait"
    assert report.refresh_candidate_ready is False
    assert (
        "AAA:source_equal_cutoff_wait"
        in report.blocker_reasons
    )


# ---------------------------------------------------------------------------
# 3. source == cutoff -> publishable with policy switch
# ---------------------------------------------------------------------------


def test_source_equal_cutoff_with_policy_becomes_publishable():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[
            _source_state(
                ticker="AAA",
                source_equal_to_cutoff=True,
                new_cache_date_range_end="2026-05-14",
                provider_fetch_telemetry=_telemetry_ok(),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.per_ticker_states[
        0
    ].classification == "source_equal_cutoff_publishable"
    assert report.refresh_candidate_ready is True


# ---------------------------------------------------------------------------
# 4. TEF-style invalid telemetry -> invalid_or_delisted
# ---------------------------------------------------------------------------


def test_tef_style_delisted_telemetry_classifies_invalid():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="TEF")],
        source_states=[
            _source_state(
                ticker="TEF",
                new_cache_date_range_end=None,
                provider_fetch_telemetry=(
                    _telemetry_delisted()
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["TEF"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    state = report.per_ticker_states[0]
    assert state.classification == "invalid_or_delisted"
    assert state.is_invalid is True
    assert state.invalid_reason is not None
    assert "TEF" in report.invalid_tickers
    assert any(
        w["ticker"] == "TEF"
        for w in report.warning_members
    )


def test_invalid_telemetry_zero_rows_no_new_date_classifies_invalid():
    """A simpler invalid signal: provider attempted +
    failed + rows=0 + new_cache_date_range_end=None
    (without an explicit "delisted" warning string)."""
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="ZZZ")],
        source_states=[
            _source_state(
                ticker="ZZZ",
                new_cache_date_range_end=None,
                provider_fetch_telemetry={
                    "fetch_attempted": True,
                    "fetch_succeeded": False,
                    "rows": 0,
                    "error": None,
                    "warning": None,
                },
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["ZZZ"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    state = report.per_ticker_states[0]
    assert state.classification == "invalid_or_delisted"


# ---------------------------------------------------------------------------
# 5. Invalid excluded from candidate command
# ---------------------------------------------------------------------------


def test_invalid_ticker_excluded_from_candidate_command():
    cache_probe, source_probe = _fake_probes(
        cache_states=[
            _cache_state(ticker="AAA"),
            _cache_state(ticker="BBB"),
            _cache_state(ticker="TEF"),
        ],
        source_states=[
            _source_state(
                ticker="AAA",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-15",
                provider_fetch_telemetry=_telemetry_ok(
                    date_range_end="2026-05-15",
                ),
            ),
            _source_state(
                ticker="BBB",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-15",
                provider_fetch_telemetry=_telemetry_ok(
                    date_range_end="2026-05-15",
                ),
            ),
            _source_state(
                ticker="TEF",
                new_cache_date_range_end=None,
                provider_fetch_telemetry=(
                    _telemetry_delisted()
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA", "BBB", "TEF"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.refresh_candidate_ready is True
    # Phase 6I-44: candidate commands are now per-ticker
    # singular. The plural list is authoritative; singular
    # carries only the FIRST command.
    commands = report.refresh_candidate_commands
    argvs = report.refresh_candidate_command_argvs
    assert len(commands) == 2
    assert len(argvs) == 2
    joined = " || ".join(commands)
    assert "TEF" not in joined
    # Each command refers to exactly one ticker via the
    # singular --ticker flag.
    for cmd_str, argv in zip(commands, argvs):
        assert "--tickers" not in cmd_str
        assert "--ticker" in cmd_str
        assert "--tickers" not in argv
    # The set of tickers across commands matches the
    # non-invalid set.
    tickers_in_argvs = []
    for argv in argvs:
        idx = list(argv).index("--ticker")
        tickers_in_argvs.append(argv[idx + 1])
    assert sorted(tickers_in_argvs) == ["AAA", "BBB"]
    # Singular field is the first command (deprecated
    # backward-compat).
    assert report.refresh_candidate_command == commands[0]
    # Refresh candidate ticker list excludes TEF.
    assert "TEF" not in report.refresh_candidate_tickers
    assert sorted(
        report.refresh_candidate_tickers,
    ) == ["AAA", "BBB"]


# ---------------------------------------------------------------------------
# 6. Invalid surfaces in warning_members / invalid_tickers
# ---------------------------------------------------------------------------


def test_invalid_surfaces_in_warning_members():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="TEF")],
        source_states=[
            _source_state(
                ticker="TEF",
                new_cache_date_range_end=None,
                provider_fetch_telemetry=(
                    _telemetry_delisted()
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["TEF"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.invalid_tickers == ("TEF",)
    assert len(report.warning_members) == 1
    w = report.warning_members[0]
    assert w["ticker"] == "TEF"
    assert (
        w["classification"] == "invalid_or_delisted"
    )
    assert isinstance(w["reason"], str) and w["reason"]


# ---------------------------------------------------------------------------
# 7. refresh_candidate_ready=False when only equal-cutoff
# ---------------------------------------------------------------------------


def test_ready_false_when_equal_cutoff_not_allowed():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[
            _source_state(
                ticker="AAA",
                source_equal_to_cutoff=True,
                new_cache_date_range_end="2026-05-14",
                provider_fetch_telemetry=_telemetry_ok(),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=False,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.refresh_candidate_ready is False
    assert report.refresh_candidate_command is None
    assert report.refresh_candidate_command_argv is None


# ---------------------------------------------------------------------------
# 8. refresh_candidate_ready=True with policy + only invalid excluded
# ---------------------------------------------------------------------------


def test_ready_true_with_policy_and_invalid_excluded():
    """14 non-TEF tickers source-equal-cutoff + TEF
    invalid + allow_equal_cutoff_after_close=True ->
    refresh_candidate_ready=True. Mirrors the Phase 6I-38
    SPY-K-universe scenario."""
    non_tef = [
        "SPY", "AROW", "AWR", "CLH", "CP", "EXPO", "FCFS",
        "GBCI", "HCSG", "JNJ", "LLY", "MO", "PRA", "PRGO",
    ]
    universe = non_tef + ["TEF"]
    cache_states = [
        _cache_state(ticker=t) for t in universe
    ]
    source_states = [
        _source_state(
            ticker=t,
            source_equal_to_cutoff=True,
            new_cache_date_range_end="2026-05-14",
            provider_fetch_telemetry=_telemetry_ok(),
        )
        for t in non_tef
    ] + [
        _source_state(
            ticker="TEF",
            new_cache_date_range_end=None,
            provider_fetch_telemetry=_telemetry_delisted(),
        ),
    ]
    cache_probe, source_probe = _fake_probes(
        cache_states=cache_states,
        source_states=source_states,
    )
    report = pv2.plan_source_refresh_policy_v2(
        universe,
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.refresh_candidate_ready is True
    assert report.invalid_tickers == ("TEF",)
    # All 14 non-TEF tickers classify as publishable.
    assert (
        report.counts_by_classification[
            "source_equal_cutoff_publishable"
        ]
        == 14
    )
    assert (
        report.counts_by_classification[
            "invalid_or_delisted"
        ]
        == 1
    )
    # Phase 6I-44: per-ticker commands. One command per
    # non-invalid candidate ticker; TEF excluded entirely.
    commands = report.refresh_candidate_commands
    argvs = report.refresh_candidate_command_argvs
    assert len(commands) == 14
    assert len(argvs) == 14
    joined = " || ".join(commands)
    assert "TEF" not in joined
    tickers_in_argvs = []
    for cmd_str, argv in zip(commands, argvs):
        assert "--tickers" not in cmd_str
        assert "--ticker" in cmd_str
        idx = list(argv).index("--ticker")
        tickers_in_argvs.append(argv[idx + 1])
    assert sorted(tickers_in_argvs) == sorted(non_tef)
    assert report.refresh_candidate_command == commands[0]


# ---------------------------------------------------------------------------
# 9. Candidate command shape -- non-invalid tickers only,
#    no PRJCT9_AUTOMATION_WRITE_AUTH wording
# ---------------------------------------------------------------------------


def test_candidate_command_has_no_auth_env_var_wording():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[
            _source_state(
                ticker="AAA",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-15",
                provider_fetch_telemetry=_telemetry_ok(
                    date_range_end="2026-05-15",
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    cmd = report.refresh_candidate_command
    assert cmd is not None
    # The Phase 6E-5 refresher CLI uses --write + internal
    # provenance/optimizer guards. It does NOT use
    # PRJCT9_AUTOMATION_WRITE_AUTH. The candidate command
    # must NOT carry the env-var wording.
    assert "PRJCT9_AUTOMATION_WRITE_AUTH" not in cmd
    assert "phase_6h5_explicit" not in cmd
    # But --write IS expected.
    assert "--write" in cmd
    assert "signal_engine_cache_refresher.py" in cmd
    # argv mirrors the string and also carries no env-var
    # wording.
    argv = report.refresh_candidate_command_argv
    assert argv is not None
    joined = " ".join(argv)
    assert "PRJCT9_AUTOMATION_WRITE_AUTH" not in joined


def test_candidate_command_includes_cache_dir_and_cutoff():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[
            _source_state(
                ticker="AAA",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-15",
                provider_fetch_telemetry=_telemetry_ok(
                    date_range_end="2026-05-15",
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    cmd = report.refresh_candidate_command
    assert "--cache-dir" in cmd
    assert "cache/results" in cmd or (
        "cache\\results" in cmd
    )
    assert "--current-as-of-date" in cmd
    assert "2026-05-14" in cmd


# ---------------------------------------------------------------------------
# Phase 6I-43 amendment-1: pinned interpreter
# ---------------------------------------------------------------------------


_PINNED = (
    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/"
    "spyproject2/python.exe"
)


def test_amendment1_pinned_interpreter_constant_exposed():
    """Phase 6I-43 amendment-1: the module exposes a stable
    PINNED_PYTHON_INTERPRETER constant that names the
    spyproject2 audit interpreter."""
    assert hasattr(pv2, "PINNED_PYTHON_INTERPRETER")
    assert pv2.PINNED_PYTHON_INTERPRETER == _PINNED


def test_amendment1_candidate_command_uses_pinned_interpreter():
    """The candidate command MUST start with the pinned
    interpreter path. It MUST NOT start with bare
    ``python signal_engine_cache_refresher.py`` because
    bare ``python`` on this machine can resolve to a wrong
    environment."""
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[
            _source_state(
                ticker="AAA",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-15",
                provider_fetch_telemetry=_telemetry_ok(
                    date_range_end="2026-05-15",
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    cmd = report.refresh_candidate_command
    assert cmd is not None
    # Pinned interpreter path appears in the command and
    # is the FIRST token.
    assert _PINNED in cmd
    assert cmd.startswith(_PINNED + " ")
    # Negative check: command does NOT start with bare
    # ``python signal_engine_cache_refresher.py``.
    assert not cmd.startswith(
        "python signal_engine_cache_refresher.py",
    )
    # argv[0] is the pinned interpreter path.
    argv = report.refresh_candidate_command_argv
    assert argv is not None
    assert argv[0] == _PINNED
    # argv[1] is the refresher script (sanity).
    assert argv[1] == "signal_engine_cache_refresher.py"


def test_amendment1_pinned_interpreter_with_invalid_excluded():
    """Combined check: pinned interpreter + invalid-ticker
    exclusion + no env-var wording -- all three contracts
    hold simultaneously on the realistic SPY-K-universe
    fixture."""
    non_tef = [
        "SPY", "AROW", "AWR", "CLH", "CP", "EXPO", "FCFS",
        "GBCI", "HCSG", "JNJ", "LLY", "MO", "PRA", "PRGO",
    ]
    universe = non_tef + ["TEF"]
    cache_states = [
        _cache_state(ticker=t) for t in universe
    ]
    source_states = [
        _source_state(
            ticker=t,
            source_equal_to_cutoff=True,
            new_cache_date_range_end="2026-05-14",
            provider_fetch_telemetry=_telemetry_ok(),
        )
        for t in non_tef
    ] + [
        _source_state(
            ticker="TEF",
            new_cache_date_range_end=None,
            provider_fetch_telemetry=_telemetry_delisted(),
        ),
    ]
    cache_probe, source_probe = _fake_probes(
        cache_states=cache_states,
        source_states=source_states,
    )
    report = pv2.plan_source_refresh_policy_v2(
        universe,
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.refresh_candidate_ready is True
    # Phase 6I-44: combined check across the plural list.
    commands = report.refresh_candidate_commands
    argvs = report.refresh_candidate_command_argvs
    assert len(commands) == 14
    assert len(argvs) == 14
    tickers_in_argvs = []
    for cmd_str, argv in zip(commands, argvs):
        # Pinned interpreter first.
        assert cmd_str.startswith(_PINNED + " ")
        assert argv[0] == _PINNED
        # Refresher script.
        assert argv[1] == "signal_engine_cache_refresher.py"
        # --ticker (singular) present; --tickers (plural)
        # never present.
        assert "--ticker" in cmd_str
        assert "--tickers" not in cmd_str
        assert "--tickers" not in argv
        # No env-var wording.
        assert "PRJCT9_AUTOMATION_WRITE_AUTH" not in cmd_str
        assert "phase_6h5_explicit" not in cmd_str
        # --write present.
        assert " --write" in cmd_str or (
            cmd_str.endswith("--write")
        )
        # No bare ``python`` prefix.
        assert not cmd_str.startswith("python ")
        idx = list(argv).index("--ticker")
        tickers_in_argvs.append(argv[idx + 1])
    # TEF excluded; full non-TEF set covered.
    assert "TEF" not in tickers_in_argvs
    assert sorted(tickers_in_argvs) == sorted(non_tef)


# ---------------------------------------------------------------------------
# Cache-already-ready short-circuit
# ---------------------------------------------------------------------------


def test_cache_already_ahead_classifies_short_circuit():
    cache_probe, source_probe = _fake_probes(
        cache_states=[
            _cache_state(
                ticker="AAA",
                cache_ahead_of_cutoff=True,
                cache_behind_cutoff=False,
                cache_date_range_end="2026-05-16",
            ),
        ],
        source_states=[
            _source_state(
                ticker="AAA",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-16",
                provider_fetch_telemetry=_telemetry_ok(),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.per_ticker_states[
        0
    ].classification == "cache_already_ready"
    # Cache-ready ticker is excluded from the candidate
    # command (refreshing would be a no-op).
    assert report.refresh_candidate_command is None
    assert report.refresh_candidate_tickers == ()
    # Still ready -- nothing to do.
    assert report.refresh_candidate_ready is True


# ---------------------------------------------------------------------------
# Defensive paths
# ---------------------------------------------------------------------------


def test_source_state_missing_yields_manual_review():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[],  # missing for AAA
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.per_ticker_states[
        0
    ].classification == "manual_review_required"
    assert report.refresh_candidate_ready is False


def test_unknown_invalid_policy_raises_value_error():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[_source_state(ticker="AAA")],
    )
    import pytest
    with pytest.raises(ValueError):
        pv2.plan_source_refresh_policy_v2(
            ["AAA"],
            cache_dir="cache/results",
            current_as_of_date="2026-05-14",
            invalid_ticker_policy="totally_made_up",
            cache_cutoff_callable=cache_probe,
            source_readiness_callable=source_probe,
        )


def test_probe_exception_degrades_gracefully():
    def boom_cache(tickers, *, cache_dir, current_as_of_date):
        raise RuntimeError("cache probe exploded")
    def boom_source(tickers, *, cache_dir, current_as_of_date):
        raise RuntimeError("source probe exploded")
    # Both probes raise -- planner should still construct
    # without crashing; all tickers fall through to
    # manual_review_required.
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=boom_cache,
        source_readiness_callable=boom_source,
    )
    assert report.per_ticker_states[
        0
    ].classification == "manual_review_required"
    assert report.refresh_candidate_ready is False


# ---------------------------------------------------------------------------
# Report shape / JSON serializability
# ---------------------------------------------------------------------------


def test_report_to_json_dict_round_trips():
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[
            _source_state(
                ticker="AAA",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-15",
                provider_fetch_telemetry=_telemetry_ok(
                    date_range_end="2026-05-15",
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    j = report.to_json_dict()
    text = json.dumps(j)
    again = json.loads(text)
    assert again["schema_version"] == "source_refresh_policy_v2"
    assert again["refresh_candidate_ready"] is True
    assert "AAA" in again["refresh_candidate_command"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_emits_report_json(capsys):
    # Use a deliberately-nonexistent cache dir so the
    # default cache_cutoff_probe + source_availability
    # probe likely fail; the planner still emits a JSON
    # report (states either empty or manual_review).
    rc = pv2.main([
        "--tickers", "AAA",
        "--cache-dir", "/tmp/no_such_dir",
        "--current-as-of-date", "2026-05-14",
    ])
    # rc may be 0 (planner returned cleanly with manual
    # review) -- defensive probes never crash.
    assert rc == 0
    out = capsys.readouterr().out
    j = json.loads(out)
    assert j["schema_version"] == (
        "source_refresh_policy_v2"
    )
    assert j["target_tickers"] == ["AAA"]


def test_cli_missing_tickers_returns_rc_2(capsys):
    rc = pv2.main(["--tickers", ""])
    assert rc == 2


def test_cli_unknown_invalid_policy_returns_rc_2(capsys):
    """argparse rejects an unknown --invalid-ticker-policy
    value at parse time (choices=...)."""
    rc = pv2.main([
        "--tickers", "AAA",
        "--invalid-ticker-policy", "totally_made_up",
    ])
    assert rc == 2


# ---------------------------------------------------------------------------
# Static / forbidden-import guards
# ---------------------------------------------------------------------------


def test_module_no_forbidden_top_level_imports():
    src = Path(pv2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_first = {
        "yfinance", "dash", "subprocess",
        "signal_engine_cache_refresher",
        "signal_library_stable_promotion_writer",
        "multiwindow_k_confluence_patch_writer",
        "confluence_pipeline_runner",
        "daily_board_automation_writer",
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
    assert not bad, (
        f"forbidden top-level imports: {bad!r}"
    )


def test_module_no_raw_pickle_load():
    src = Path(pv2.__file__).read_text(encoding="utf-8")
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
                        "module calls pickle.load() at "
                        f"line {node.lineno}"
                    )


def test_module_no_write_true_kwarg_anywhere():
    """The planner never passes ``write=True`` to any
    callable. Refresh authorization is the operator's
    responsibility via a separate invocation of the
    refresher CLI."""
    src = Path(pv2.__file__).read_text(encoding="utf-8")
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
    assert not offenders


def test_module_no_yfinance_import_anywhere():
    src = Path(pv2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert (
                    "yfinance"
                    not in alias.name.lower()
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert (
                    "yfinance"
                    not in node.module.lower()
                )


# ---------------------------------------------------------------------------
# Phase 6I-44: per-ticker command contract
# ---------------------------------------------------------------------------


def _spy_k_universe_publishable_fixture():
    """Reusable 14-non-TEF + TEF fixture for the Phase 6I-44
    per-ticker contract tests."""
    non_tef = [
        "SPY", "AROW", "AWR", "CLH", "CP", "EXPO", "FCFS",
        "GBCI", "HCSG", "JNJ", "LLY", "MO", "PRA", "PRGO",
    ]
    universe = non_tef + ["TEF"]
    cache_states = [
        _cache_state(ticker=t) for t in universe
    ]
    source_states = [
        _source_state(
            ticker=t,
            source_equal_to_cutoff=True,
            new_cache_date_range_end="2026-05-14",
            provider_fetch_telemetry=_telemetry_ok(),
        )
        for t in non_tef
    ] + [
        _source_state(
            ticker="TEF",
            new_cache_date_range_end=None,
            provider_fetch_telemetry=_telemetry_delisted(),
        ),
    ]
    cache_probe, source_probe = _fake_probes(
        cache_states=cache_states,
        source_states=source_states,
    )
    return non_tef, universe, cache_probe, source_probe


def test_phase_6i44_commands_use_singular_ticker_not_plural():
    """The Phase 6I-44 contract: each emitted command uses
    ``--ticker <T>`` (singular), NEVER ``--tickers <CSV>``.
    The plural shape was the Phase 6I-43 emitter bug that
    failed live with argparse rc=2 at the refresher CLI."""
    non_tef, universe, cache_probe, source_probe = (
        _spy_k_universe_publishable_fixture()
    )
    report = pv2.plan_source_refresh_policy_v2(
        universe,
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.refresh_candidate_ready is True
    commands = report.refresh_candidate_commands
    argvs = report.refresh_candidate_command_argvs
    assert len(commands) == len(non_tef)
    assert len(argvs) == len(non_tef)
    for cmd_str, argv in zip(commands, argvs):
        assert "--tickers" not in cmd_str
        assert "--ticker" in cmd_str
        assert "--tickers" not in argv
        assert "--ticker" in argv
        # Exactly one ticker per argv.
        idx = list(argv).index("--ticker")
        # Next token is the literal ticker, not a CSV.
        assert "," not in argv[idx + 1]


def test_phase_6i44_one_command_per_non_invalid_candidate():
    """One command per non-invalid candidate ticker;
    invalid tickers (TEF) excluded from the command list
    AND from the candidate ticker list."""
    non_tef, universe, cache_probe, source_probe = (
        _spy_k_universe_publishable_fixture()
    )
    report = pv2.plan_source_refresh_policy_v2(
        universe,
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    commands = report.refresh_candidate_commands
    argvs = report.refresh_candidate_command_argvs
    assert len(commands) == 14
    assert len(argvs) == 14
    # TEF appears nowhere.
    joined = " || ".join(commands)
    assert "TEF" not in joined
    assert "TEF" not in report.refresh_candidate_tickers
    # The exact 14 non-TEF tickers each appear once.
    tickers_seen: list[str] = []
    for argv in argvs:
        idx = list(argv).index("--ticker")
        tickers_seen.append(argv[idx + 1])
    assert sorted(tickers_seen) == sorted(non_tef)
    assert len(set(tickers_seen)) == 14


def test_phase_6i44_each_command_starts_with_pinned_interpreter():
    """Each command must begin with the pinned interpreter
    path; the corresponding argv[0] must equal the pinned
    interpreter constant. Bare ``python`` is forbidden."""
    non_tef, universe, cache_probe, source_probe = (
        _spy_k_universe_publishable_fixture()
    )
    report = pv2.plan_source_refresh_policy_v2(
        universe,
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    commands = report.refresh_candidate_commands
    argvs = report.refresh_candidate_command_argvs
    assert len(commands) == 14
    assert len(argvs) == 14
    for cmd_str, argv in zip(commands, argvs):
        assert cmd_str.startswith(_PINNED + " ")
        assert argv[0] == _PINNED
        assert not cmd_str.startswith("python ")
        assert argv[0] != "python"


def test_phase_6i44_each_argv_includes_refresher_script_and_ticker():
    """argv must include ``signal_engine_cache_refresher.py``
    and ``--ticker <T>``."""
    non_tef, universe, cache_probe, source_probe = (
        _spy_k_universe_publishable_fixture()
    )
    report = pv2.plan_source_refresh_policy_v2(
        universe,
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    argvs = report.refresh_candidate_command_argvs
    assert len(argvs) == 14
    for argv in argvs:
        assert (
            "signal_engine_cache_refresher.py" in argv
        )
        assert "--ticker" in argv
        idx = list(argv).index("--ticker")
        assert argv[idx + 1] in non_tef


def test_phase_6i44_no_env_var_or_phase_6h5_wording_in_any_command():
    """No emitted command may carry
    ``PRJCT9_AUTOMATION_WRITE_AUTH`` or
    ``phase_6h5_explicit``. The Phase 6E-5 refresher CLI
    does not use that env-var gate."""
    non_tef, universe, cache_probe, source_probe = (
        _spy_k_universe_publishable_fixture()
    )
    report = pv2.plan_source_refresh_policy_v2(
        universe,
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    for cmd_str in report.refresh_candidate_commands:
        assert "PRJCT9_AUTOMATION_WRITE_AUTH" not in cmd_str
        assert "phase_6h5_explicit" not in cmd_str
    for argv in report.refresh_candidate_command_argvs:
        for token in argv:
            assert (
                "PRJCT9_AUTOMATION_WRITE_AUTH" not in token
            )
            assert "phase_6h5_explicit" not in token
    # The to_json_dict() emission carries the plural lists
    # too -- they must be present and carry no env-var
    # wording either.
    j = report.to_json_dict()
    assert "refresh_candidate_commands" in j
    assert "refresh_candidate_command_argvs" in j
    assert len(j["refresh_candidate_commands"]) == 14
    assert len(j["refresh_candidate_command_argvs"]) == 14
    for cmd_str in j["refresh_candidate_commands"]:
        assert "PRJCT9_AUTOMATION_WRITE_AUTH" not in cmd_str
    for argv in j["refresh_candidate_command_argvs"]:
        for token in argv:
            assert (
                "PRJCT9_AUTOMATION_WRITE_AUTH" not in token
            )


def test_phase_6i44_singular_field_is_first_plural_command():
    """The deprecated singular ``refresh_candidate_command``
    must equal the first element of the plural list (for
    backward compatibility)."""
    non_tef, universe, cache_probe, source_probe = (
        _spy_k_universe_publishable_fixture()
    )
    report = pv2.plan_source_refresh_policy_v2(
        universe,
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        allow_equal_cutoff_after_close=True,
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    commands = report.refresh_candidate_commands
    argvs = report.refresh_candidate_command_argvs
    assert report.refresh_candidate_command == commands[0]
    assert (
        report.refresh_candidate_command_argv == argvs[0]
    )


def test_module_no_prjct9_automation_write_auth_in_emitted_command():
    """The Phase 6E-5 refresher CLI does NOT use
    PRJCT9_AUTOMATION_WRITE_AUTH. The emitted
    refresh-candidate command / argv MUST NEVER contain
    that env-var name. Functional check (rather than
    AST-spelunking) so docstring prose explaining the
    correction does not trigger false positives."""
    cache_probe, source_probe = _fake_probes(
        cache_states=[_cache_state(ticker="AAA")],
        source_states=[
            _source_state(
                ticker="AAA",
                source_ahead_of_cutoff=True,
                new_cache_date_range_end="2026-05-15",
                provider_fetch_telemetry=_telemetry_ok(
                    date_range_end="2026-05-15",
                ),
            ),
        ],
    )
    report = pv2.plan_source_refresh_policy_v2(
        ["AAA"],
        cache_dir="cache/results",
        current_as_of_date="2026-05-14",
        cache_cutoff_callable=cache_probe,
        source_readiness_callable=source_probe,
    )
    assert report.refresh_candidate_command is not None
    assert (
        "PRJCT9_AUTOMATION_WRITE_AUTH"
        not in report.refresh_candidate_command
    )
    assert (
        "phase_6h5_explicit"
        not in report.refresh_candidate_command
    )
    argv = report.refresh_candidate_command_argv
    assert argv is not None
    for token in argv:
        assert "PRJCT9_AUTOMATION_WRITE_AUTH" not in token
        assert "phase_6h5_explicit" not in token
    # Same guarantee on the to_json_dict serialization.
    j = report.to_json_dict()
    assert (
        "PRJCT9_AUTOMATION_WRITE_AUTH"
        not in (j["refresh_candidate_command"] or "")
    )
    assert all(
        "PRJCT9_AUTOMATION_WRITE_AUTH" not in tok
        for tok in (
            j["refresh_candidate_command_argv"] or []
        )
    )
