"""Phase 6D-2 tests for trafficflow_multitimeframe_bridge.

Pins the bridge contract:

  - no yfinance / trafficflow.py / Dash imports
  - write=False writes nothing
  - write=True writes K-distinguished __MTF artifacts
  - output artifacts preserve K and top-level timeframes
  - weekly / monthly projection does not use a future period
    value before that period's last available date
  - mixed Buy/Short across timeframes combines to None
  - None-only / no-active combines to None
  - daily all-K artifacts without MTF still leave the bridge
    issue open
  - one MTF K artifact only still leaves the bridge issue open
  - MTF K=1..12 clears the bridge issue
  - readiness still blocks leader eligibility when confluence is
    missing or stale, even after the bridge clears
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import confluence_pipeline_readiness as cpr  # noqa: E402
import research_artifacts as ra  # noqa: E402
import trafficflow_multitimeframe_bridge as mtfb  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    sig_dir = tmp_path / "siglib"
    stack_dir = tmp_path / "stackbuilder"
    for d in (cache_dir, artifact_root, sig_dir, stack_dir):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache_dir,
        "artifact_root": artifact_root,
        "signal_library_dir": sig_dir,
        "stackbuilder_root": stack_dir,
    }


def _write_daily_k_artifact(
    artifact_root: Path,
    *,
    target: str,
    seed_run_id: str,
    K: int,
    daily_rows: list[dict[str, Any]],
    members: Optional[list[str]] = None,
    protocol_per_member: Optional[dict[str, Optional[str]]] = None,
) -> Path:
    """Write one Phase 6D-1-style daily TrafficFlow artifact."""
    safe = target.replace("^", "_")
    engine_dir = artifact_root / "trafficflow" / safe
    engine_dir.mkdir(parents=True, exist_ok=True)
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
            "total_capture_pct": 10.0 + K,
            "sharpe_ratio": 0.1,
            "trigger_days": 3,
        },
        daily=list(daily_rows),
        K=K,
        members=list(members or ["AAA", "BBB"]),
        protocol_per_member=dict(
            protocol_per_member or {"AAA": "D", "BBB": "D"},
        ),
    )
    name = f"{seed_run_id}__K{K}.research_day.json"
    return ra.write_research_day_artifact(art, engine_dir / name)


def _daily_row(
    date: str, close: float, signal: str = "Buy",
) -> dict[str, Any]:
    return {
        "date": date,
        "target_close": close,
        "target_return_pct": 0.0,
        "member_signals": {},
        "pressure_signal": signal,
        "buy_count": 1 if signal == "Buy" else 0,
        "short_count": 1 if signal == "Short" else 0,
        "none_count": 0 if signal in ("Buy", "Short") else 1,
        "missing_count": 0,
        "active_count": 1 if signal in ("Buy", "Short") else 0,
        "daily_capture_pct": 0.0,
        "cumulative_capture_pct": 0.0,
        "is_trigger_day": signal in ("Buy", "Short"),
    }


def _full_daily_k_pipeline(
    artifact_root: Path,
    target: str,
    *,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    last_date: str = "2026-05-08",
) -> list[Path]:
    """Write 12 daily Phase 6D-1 artifacts (K=1..12) for the
    target. The fixture uses 30 business days of "Buy" signals
    so every K row produces a valid projection."""
    import pandas as pd

    dates = pd.bdate_range(end=last_date, periods=30)
    rows = [
        _daily_row(d.strftime("%Y-%m-%d"), 100.0 + i, "Buy")
        for i, d in enumerate(dates)
    ]
    paths: list[Path] = []
    for k in range(1, 13):
        paths.append(_write_daily_k_artifact(
            artifact_root,
            target=target,
            seed_run_id=seed_run_id,
            K=k,
            daily_rows=rows,
        ))
    return paths


# ---------------------------------------------------------------------------
# Static-import sanity checks
# ---------------------------------------------------------------------------


def test_bridge_module_has_no_forbidden_imports():
    tree = ast.parse(
        Path(mtfb.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance", "trafficflow", "spymaster", "impactsearch",
        "onepass", "confluence", "cross_ticker_confluence",
        "dash",
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
        "forbidden import in trafficflow_multitimeframe_bridge: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# Projection: no future-period leak
# ---------------------------------------------------------------------------


def test_weekly_projection_does_not_use_future_period_values():
    """The 1wk projection for a daily row must use the MOST
    RECENT CLOSED week's signal, never the current still-open
    week. Days within the same week as a future close cannot see
    that close's signal until the week ends.

    Fixture (Mon..Fri = a single trading week):
        2024-01-08 Mon: Buy
        2024-01-09 Tue: Buy
        2024-01-10 Wed: Buy
        2024-01-11 Thu: Buy
        2024-01-12 Fri: Short   <- week-1 closes here
        2024-01-15 Mon: Short
        2024-01-16 Tue: Short
        ...

    The week-1 signal (last available value within the week)
    is Friday's "Short". The 1wk projection for every day in
    week 1 must NOT be "Short" - those days are inside the
    still-open week. They should show ``missing`` (no closed
    week yet). Week 2's rows (starting Monday 2024-01-15) see
    week-1's closed "Short" signal.
    """
    daily_dates = [
        "2024-01-08", "2024-01-09", "2024-01-10",
        "2024-01-11", "2024-01-12",  # week 1
        "2024-01-15", "2024-01-16",  # week 2 start
    ]
    daily_signals = ["Buy"] * 4 + ["Short"] * 3
    proj = mtfb.project_signal_to_timeframes(
        daily_dates, daily_signals,
        timeframes=("1d", "1wk"),
    )
    # 1d identity check.
    assert proj["1d"] == ["Buy"] * 4 + ["Short"] * 3
    # 1wk: every day in week 1 reports "missing" because no
    # week has closed before them.
    assert proj["1wk"][:5] == [mtfb.PRESSURE_SIGNAL_MISSING] * 5, (
        f"week-1 rows must not see future week's signal; got "
        f"{proj['1wk'][:5]}"
    )
    # Week 2 rows see the closed week-1 signal "Short" (the
    # final value on Friday 2024-01-12).
    assert proj["1wk"][5] == "Short"
    assert proj["1wk"][6] == "Short"


def test_monthly_projection_does_not_use_future_period_values():
    daily_dates = [
        "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-28",
        "2024-02-29", "2024-03-01",
    ]
    daily_signals = ["Buy", "Short", "Short", "Short", "Buy", "None"]
    proj = mtfb.project_signal_to_timeframes(
        daily_dates, daily_signals,
        timeframes=("1mo",),
    )
    # January day before month close -> missing.
    # The Jan close-day itself (2024-01-31) is exactly the month
    # close, so its row sees the closed Jan signal "Buy".
    # February rows before Feb's close see Jan's "Buy".
    # March's row (2024-03-01) sees Feb's last value "Buy".
    assert proj["1mo"][0] == "Buy"  # Jan close day reads Jan close
    assert proj["1mo"][1] == "Buy"  # Feb-01 reads Jan close
    assert proj["1mo"][2] == "Buy"  # Feb-02 reads Jan close
    # Feb-28 still pre-close (Feb 29 is the leap-year final).
    assert proj["1mo"][3] == "Buy"
    # Feb-29 IS the leap-year Feb close day -> reads Feb's last
    # available value "Buy".
    assert proj["1mo"][4] == "Buy"
    # March-01 reads Feb close.
    assert proj["1mo"][5] == "Buy"


# ---------------------------------------------------------------------------
# Combine rule
# ---------------------------------------------------------------------------


def test_combine_unanimous_buy_returns_buy():
    assert mtfb.combine_timeframe_signals({
        "1d": "Buy", "1wk": "Buy", "1mo": "Buy",
    }) == "Buy"


def test_combine_mixed_buy_short_returns_none():
    assert mtfb.combine_timeframe_signals({
        "1d": "Buy", "1wk": "Short", "1mo": "Buy",
    }) == "None"


def test_combine_all_none_or_missing_returns_none():
    assert mtfb.combine_timeframe_signals({
        "1d": "None", "1wk": "None", "1mo": "missing",
    }) == "None"
    assert mtfb.combine_timeframe_signals({
        "1d": "missing", "1wk": "missing",
    }) == "None"


def test_combine_buy_with_none_filler_returns_buy():
    """``None`` does not contribute to the active set, so a
    single active Buy still wins."""
    assert mtfb.combine_timeframe_signals({
        "1d": "Buy", "1wk": "None", "1mo": "missing",
    }) == "Buy"


# ---------------------------------------------------------------------------
# Per-artifact build
# ---------------------------------------------------------------------------


def test_build_per_artifact_preserves_k_and_timeframes(
    tmp_path: Path,
):
    import pandas as pd

    dates = pd.bdate_range(end="2026-05-08", periods=30)
    rows = [
        _daily_row(d.strftime("%Y-%m-%d"), 100.0 + i, "Buy")
        for i, d in enumerate(dates)
    ]
    daily_path = _write_daily_k_artifact(
        tmp_path,
        target="SPY",
        seed_run_id="seedTC__AAA-D_BBB-D",
        K=7,
        daily_rows=rows,
        members=["AAA", "BBB"],
        protocol_per_member={"AAA": "D", "BBB": "D"},
    )
    art_in = ra.read_research_day_artifact(daily_path)
    art_out = mtfb.build_multitimeframe_bridge_for_artifact(art_in)
    assert art_out.engine == "trafficflow"
    assert art_out.K == 7
    assert art_out.target_ticker == "SPY"
    assert list(art_out.members) == ["AAA", "BBB"]
    assert dict(art_out.protocol_per_member) == {
        "AAA": "D", "BBB": "D",
    }
    assert list(art_out.timeframes) == list(mtfb.DEFAULT_TIMEFRAMES)
    assert art_out.run_id.endswith("__K7__MTF")
    # Every output daily row carries the new MTF schema fields.
    assert art_out.daily, "expected non-empty daily output"
    row = art_out.daily[0]
    for key in (
        "pressure_signal", "timeframe_pressure_signals",
        "buy_count", "short_count", "none_count",
        "active_count", "available_count",
        "daily_capture_pct", "cumulative_capture_pct",
        "is_trigger_day",
    ):
        assert key in row, f"output row missing key {key!r}"


# ---------------------------------------------------------------------------
# Top-level sweep: discovery, write=False / True, paths
# ---------------------------------------------------------------------------


def _write_legacy_unsuffixed_trafficflow_artifact(
    artifact_root: Path,
    *,
    target: str,
    run_id: str,
    K: Optional[int],
    daily_rows: list[dict[str, Any]],
) -> Path:
    """Write a legacy-shaped TrafficFlow artifact whose filename
    does NOT carry the Phase 6D-1 ``__K<K>`` suffix. Used by the
    PR #197 audit tests to prove the bridge filter rejects such
    inputs."""
    safe = target.replace("^", "_")
    engine_dir = artifact_root / "trafficflow" / safe
    engine_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow",
        target_ticker=target,
        signal_source="",
        run_id=run_id,
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 5.0,
            "sharpe_ratio": 0.05,
            "trigger_days": 1,
        },
        daily=list(daily_rows),
        K=K,
        members=["AAA", "BBB"],
        protocol_per_member={"AAA": "D", "BBB": "D"},
    )
    return ra.write_research_day_artifact(
        art, engine_dir / f"{run_id}.research_day.json",
    )


def _write_mismatched_k_artifact(
    artifact_root: Path,
    *,
    target: str,
    seed_run_id: str,
    filename_K: int,
    artifact_K: int,
    daily_rows: list[dict[str, Any]],
) -> Path:
    """Write an artifact whose filename suffix declares one K but
    whose internal ``K`` field declares another. Audit tests use
    this to prove the bridge skips conflicting inputs."""
    safe = target.replace("^", "_")
    engine_dir = artifact_root / "trafficflow" / safe
    engine_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow",
        target_ticker=target,
        signal_source="",
        run_id=f"{seed_run_id}__K{filename_K}",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 7.0,
            "sharpe_ratio": 0.05,
            "trigger_days": 1,
        },
        daily=list(daily_rows),
        K=artifact_K,
        members=["AAA", "BBB"],
        protocol_per_member={"AAA": "D", "BBB": "D"},
    )
    return ra.write_research_day_artifact(
        art,
        engine_dir / f"{seed_run_id}__K{filename_K}.research_day.json",
    )


def test_legacy_unsuffixed_artifact_is_ignored_as_mtf_input(
    tmp_path: Path,
):
    """PR #197 audit fix: a legacy TrafficFlow artifact named
    ``<seed>.research_day.json`` (no ``__K<K>`` suffix) must NOT
    be treated as a Phase 6D-2 input, even if it has ``K`` set
    internally. Builders must report
    ``no_daily_k_artifacts`` in that case."""
    import pandas as pd
    dirs = _layout(tmp_path)
    dates = pd.bdate_range(end="2026-05-08", periods=10)
    rows = [
        _daily_row(d.strftime("%Y-%m-%d"), 100.0 + i, "Buy")
        for i, d in enumerate(dates)
    ]
    _write_legacy_unsuffixed_trafficflow_artifact(
        dirs["artifact_root"],
        target="SPY",
        run_id="legacy_seed",  # NB: no __K1 suffix on filename
        K=1,
        daily_rows=rows,
    )
    res = mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "SPY",
        artifact_root=dirs["artifact_root"],
    )
    assert res.issue_codes == (mtfb.ISSUE_NO_DAILY_K_ARTIFACTS,), (
        "legacy unsuffixed artifact must not satisfy the bridge "
        "input filter; got " + repr(res.issue_codes)
    )
    assert res.attempted_k == ()
    assert res.built_k == ()


def test_legacy_artifact_plus_proper_k_uses_proper_k_source(
    tmp_path: Path,
):
    """When both a legacy unsuffixed K=1 artifact and a proper
    Phase 6D-1 ``__K1`` artifact exist, the bridge must use the
    proper one. The legacy artifact is filtered out before the
    builder even reads it. Adding ``__K2`` keeps the test honest
    about partial-K coverage messaging."""
    import pandas as pd
    dirs = _layout(tmp_path)
    dates = pd.bdate_range(end="2026-05-08", periods=20)
    rows = [
        _daily_row(d.strftime("%Y-%m-%d"), 100.0 + i, "Buy")
        for i, d in enumerate(dates)
    ]
    # The legacy artifact is sorted BEFORE the proper one because
    # ``legacy_seed.research_day.json`` < ``proper_seed__K1...`` in
    # filename order. Under the audit fix the legacy file is
    # filtered out by the regex.
    _write_legacy_unsuffixed_trafficflow_artifact(
        dirs["artifact_root"], target="SPY",
        run_id="aaa_legacy_seed", K=1, daily_rows=rows,
    )
    _write_daily_k_artifact(
        dirs["artifact_root"], target="SPY",
        seed_run_id="zzz_proper_seed", K=1, daily_rows=rows,
    )
    _write_daily_k_artifact(
        dirs["artifact_root"], target="SPY",
        seed_run_id="zzz_proper_seed", K=2, daily_rows=rows,
    )

    res = mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    # Built K should be {1, 2} from the proper __K1 / __K2 files;
    # the legacy file never reaches the builder loop.
    assert set(res.built_k) == {1, 2}
    # Partial coverage flag is correct given expected_k = 1..12.
    assert mtfb.ISSUE_PARTIAL_K_COVERAGE in res.issue_codes
    # No K-mismatch issue from any input.
    assert (
        mtfb.ISSUE_INPUT_ARTIFACT_K_MISMATCH not in res.issue_codes
    )
    # And the persisted MTF artifacts trace back to the PROPER
    # seed run id, not the legacy one.
    for p in res.artifact_paths:
        art = ra.read_research_day_artifact(p)
        assert art is not None
        assert art.run_id.startswith("zzz_proper_seed"), (
            "MTF artifact must derive from the proper Phase 6D-1 "
            f"source; got run_id={art.run_id!r}"
        )


def test_k_metadata_mismatch_is_ignored_and_reported(
    tmp_path: Path,
):
    """A file whose filename suffix says ``__K3`` but whose
    artifact ``K`` field says 5 must NOT contribute to coverage
    AND must surface the mismatch issue code."""
    import pandas as pd
    dirs = _layout(tmp_path)
    dates = pd.bdate_range(end="2026-05-08", periods=20)
    rows = [
        _daily_row(d.strftime("%Y-%m-%d"), 100.0 + i, "Buy")
        for i, d in enumerate(dates)
    ]
    _write_mismatched_k_artifact(
        dirs["artifact_root"], target="SPY",
        seed_run_id="seed", filename_K=3, artifact_K=5,
        daily_rows=rows,
    )

    res = mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=False,
    )
    assert mtfb.ISSUE_INPUT_ARTIFACT_K_MISMATCH in res.issue_codes
    # Neither K=3 (filename) nor K=5 (artifact) should count as
    # built coverage.
    assert res.attempted_k == ()
    assert res.built_k == ()


def test_target_sweep_returns_no_daily_k_when_input_absent(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    res = mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "NOPE",
        artifact_root=dirs["artifact_root"],
    )
    assert res.issue_codes == (mtfb.ISSUE_NO_DAILY_K_ARTIFACTS,)
    assert res.built_k == ()


def test_target_sweep_write_false_writes_nothing(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _full_daily_k_pipeline(dirs["artifact_root"], "SPY")
    res = mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=False,
    )
    assert set(res.built_k) == set(range(1, 13))
    assert res.issue_codes == ()
    # write=False must not leave any __MTF file on disk.
    mtf_files = list(
        (dirs["artifact_root"] / "trafficflow" / "SPY").glob(
            "*__MTF.research_day.json",
        ),
    )
    assert mtf_files == []


def test_target_sweep_write_true_persists_one_mtf_per_k(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _full_daily_k_pipeline(dirs["artifact_root"], "SPY")
    res = mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    assert set(res.built_k) == set(range(1, 13))
    assert len(res.artifact_paths) == 12
    # Paths are K-distinguished and carry the __MTF suffix.
    names = {p.name for p in res.artifact_paths}
    assert all("__MTF" in n for n in names)
    for k in range(1, 13):
        assert any(f"__K{k}__MTF" in n for n in names), (
            f"no MTF artifact named with __K{k}__MTF; got {names}"
        )
    # Round-trip each MTF artifact and confirm K + timeframes
    # survived the JSON serialization.
    for k in range(1, 13):
        match = next(
            p for p in res.artifact_paths
            if f"__K{k}__MTF" in p.name
        )
        art = ra.read_research_day_artifact(match)
        assert art is not None
        assert art.engine == "trafficflow"
        assert art.K == k
        assert list(art.timeframes) == list(mtfb.DEFAULT_TIMEFRAMES)


def test_mtf_run_id_suffix_helper_is_idempotent_safe():
    assert mtfb.artifact_run_id_for_multitimeframe("") == ""
    assert mtfb.artifact_run_id_for_multitimeframe("seedX__K3") == (
        "seedX__K3__MTF"
    )


# ---------------------------------------------------------------------------
# Readiness integration
# ---------------------------------------------------------------------------


def test_daily_only_artifacts_still_leave_bridge_open(
    tmp_path: Path,
):
    """Phase 6D-1 artifacts (no ``timeframes``) clear
    insufficient_trafficflow_k_coverage but do NOT clear the
    bridge issue. The fixture writes 12 daily-K artifacts with
    empty timeframes; readiness must still report the bridge as
    missing."""
    dirs = _layout(tmp_path)
    _full_daily_k_pipeline(dirs["artifact_root"], "SPY")
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE
        not in r.issue_codes
    ), r.issue_codes
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        in r.issue_codes
    )


def test_single_mtf_k_artifact_still_leaves_bridge_open(
    tmp_path: Path,
):
    """A single MTF K=1 artifact (timeframes set, K=1 only)
    leaves the bridge issue open. Phase 6D-2 requires the MTF
    coverage to span the full expected K range."""
    dirs = _layout(tmp_path)
    safe = "SPY"
    engine_dir = dirs["artifact_root"] / "trafficflow" / safe
    engine_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow",
        target_ticker="SPY",
        signal_source="",
        run_id="seed__K1__MTF",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 10.0,
            "sharpe_ratio": 0.1,
            "trigger_days": 3,
        },
        daily=[{
            "date": "2026-05-08", "target_close": 100.0,
            "target_return_pct": 0.0, "pressure_signal": "Buy",
            "buy_count": 5, "short_count": 0,
            "none_count": 0, "active_count": 5,
            "available_count": 5,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 0.0,
            "is_trigger_day": True,
        }],
        K=1,
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    ra.write_research_day_artifact(
        art, engine_dir / "seed__K1__MTF.research_day.json",
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        in r.issue_codes
    )


def test_full_mtf_k1_to_k12_clears_bridge_issue(tmp_path: Path):
    """Phase 6D-2 happy path: 12 MTF artifacts spanning K=1..12
    must clear missing_multitimeframe_trafficflow_bridge in the
    readiness verdict."""
    dirs = _layout(tmp_path)
    _full_daily_k_pipeline(dirs["artifact_root"], "SPY")
    mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        not in r.issue_codes
    ), r.issue_codes


def test_bridge_present_but_confluence_missing_still_blocks_leader(
    tmp_path: Path,
):
    """The bridge clearing does NOT promote a ticker on its own.
    A ticker with full MTF K=1..12 but no Confluence artifact
    must still be leader_eligible=False, blocked on the missing
    confluence stage."""
    dirs = _layout(tmp_path)
    _full_daily_k_pipeline(dirs["artifact_root"], "SPY")
    mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert r.leader_eligible is False
    assert (
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT in r.issue_codes
    )


def test_bridge_present_but_confluence_stale_still_blocks_leader(
    tmp_path: Path,
):
    """Even with the bridge cleared and a present Confluence
    artifact, a stale Confluence verdict still fails the gate."""
    dirs = _layout(tmp_path)
    _full_daily_k_pipeline(dirs["artifact_root"], "SPY")
    mtfb.build_multitimeframe_bridge_artifacts_for_target(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    # Write a stale confluence artifact (last_date well before
    # the resolved as-of cutoff).
    safe = "SPY"
    conf_dir = dirs["artifact_root"] / "confluence" / safe
    conf_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="confluence",
        target_ticker="SPY",
        signal_source="",
        run_id="stale",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2024-01-21T00:00:00+00:00",
        summary={
            "total_capture_pct": 1.0, "sharpe_ratio": 0.0,
            "trigger_days": 1,
        },
        daily=[{
            "date": "2024-01-21", "target_close": 100.0,
            "target_return_pct": 0.0,
            "confluence_tier": "buy",
            "confluence_signal": "Buy",
            "timeframe_signals": {},
            "alignment_pct": 1.0,
            "buy_count": 3, "short_count": 0,
            "none_count": 2, "active_count": 3,
            "available_count": 5,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 1.0,
            "is_trigger_day": True,
        }],
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    ra.write_research_day_artifact(
        art, conf_dir / "SPY.research_day.json",
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert r.leader_eligible is False
    assert (
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT in r.issue_codes
    )
    # Bridge is satisfied here, so its issue must NOT appear.
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        not in r.issue_codes
    )
