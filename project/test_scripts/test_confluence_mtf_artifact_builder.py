"""Phase 6D-3 tests for confluence_mtf_artifact_builder.

Pins the Phase 6D-3 contract:

  - discovers only __K<K>__MTF.research_day.json artifacts
  - ignores legacy unsuffixed TrafficFlow files
  - ignores Phase 6D-1 daily __K<K>.research_day.json files
  - K metadata mismatch is reported and skipped
  - missing K coverage reports an issue and writes nothing
  - all-Buy / all-Short / mixed-Buy-Short / None-only votes
    combine per the strict-unanimity rule, with vote counts
    preserved on the row
  - write=False performs no disk writes
  - output artifact is readable by the existing readiness +
    Daily Signal Board confluence consumers
  - a fresh full fixture clears both
    missing_confluence_day_artifact and
    stale_confluence_day_artifact in readiness
  - no yfinance / live engine imports in the builder module
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

import confluence_mtf_artifact_builder as cmab  # noqa: E402
import confluence_pipeline_readiness as cpr  # noqa: E402
import daily_signal_board as board  # noqa: E402
import research_artifacts as ra  # noqa: E402


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


def _build_dates(n: int, end: str = "2026-05-08") -> list[str]:
    import pandas as pd
    return [
        d.strftime("%Y-%m-%d")
        for d in pd.bdate_range(end=end, periods=n)
    ]


def _write_mtf_artifact(
    artifact_root: Path,
    *,
    target: str,
    seed_run_id: str,
    K: int,
    dates: list[str],
    per_day_per_tf: dict[str, dict[str, str]],
    timeframes: list[str],
    target_close_base: float = 100.0,
    persist_skip_bars: int = 1,
    K_in_artifact: Optional[int] = None,
) -> Path:
    """Write a Phase 6D-2-style MTF TrafficFlow artifact.

    ``per_day_per_tf`` maps date -> {timeframe -> signal_string};
    each row's pressure_signal is the strict-unanimity combine
    over the active timeframes. The fixture deliberately does
    NOT replicate research_artifacts' internal capture math -
    the daily ``daily_capture_pct`` and ``cumulative_capture_pct``
    are written as zero placeholders so the test can focus on
    Phase 6D-3 aggregation semantics.
    """
    safe = target.replace("^", "_")
    engine_dir = artifact_root / "trafficflow" / safe
    engine_dir.mkdir(parents=True, exist_ok=True)
    daily: list[dict[str, Any]] = []
    for i, d in enumerate(dates):
        tf_map = dict(per_day_per_tf.get(d, {}))
        for tf in timeframes:
            tf_map.setdefault(tf, "missing")
        active = [
            v for v in tf_map.values() if v in ("Buy", "Short")
        ]
        if not active:
            pressure = "None"
        elif all(v == "Buy" for v in active):
            pressure = "Buy"
        elif all(v == "Short" for v in active):
            pressure = "Short"
        else:
            pressure = "None"
        buy = sum(1 for v in tf_map.values() if v == "Buy")
        short = sum(1 for v in tf_map.values() if v == "Short")
        none = sum(1 for v in tf_map.values() if v == "None")
        miss = sum(1 for v in tf_map.values() if v == "missing")
        daily.append({
            "date": d,
            "target_close": target_close_base + i,
            "target_return_pct": 0.0,
            "pressure_signal": pressure,
            "timeframe_pressure_signals": tf_map,
            "buy_count": buy,
            "short_count": short,
            "none_count": none,
            "missing_count": miss,
            "active_count": buy + short,
            "available_count": buy + short + none,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 0.0,
            "is_trigger_day": pressure in ("Buy", "Short"),
        })
    K_meta = K if K_in_artifact is None else K_in_artifact
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow",
        target_ticker=target,
        signal_source="",
        run_id=f"{seed_run_id}__K{K}__MTF",
        metric_basis="Close",
        persist_skip_bars=int(persist_skip_bars),
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 0.0,
            "sharpe_ratio": 0.0,
            "trigger_days": 0,
        },
        daily=daily,
        K=K_meta,
        members=["AAA", "BBB"],
        protocol_per_member={"AAA": "D", "BBB": "D"},
        timeframes=list(timeframes),
    )
    name = f"{seed_run_id}__K{K}__MTF.research_day.json"
    return ra.write_research_day_artifact(art, engine_dir / name)


def _write_full_mtf_pipeline(
    artifact_root: Path,
    target: str,
    *,
    dates: list[str],
    per_day_per_tf: dict[str, dict[str, str]],
    timeframes: Optional[list[str]] = None,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
) -> list[Path]:
    """Phase 6D-3 happy-path fixture: write K=1..12 MTF
    artifacts that all agree on a per-day timeframe signal map.
    All 12 K artifacts contribute identical votes."""
    tfs = list(timeframes or cmab.DEFAULT_EXPECTED_TIMEFRAMES)
    paths: list[Path] = []
    for k in cmab.DEFAULT_EXPECTED_K:
        paths.append(_write_mtf_artifact(
            artifact_root,
            target=target, seed_run_id=seed_run_id, K=k,
            dates=dates, per_day_per_tf=per_day_per_tf,
            timeframes=tfs,
        ))
    return paths


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_builder_module_has_no_forbidden_imports():
    tree = ast.parse(
        Path(cmab.__file__).read_text(encoding="utf-8"),
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
        "forbidden import in confluence_mtf_artifact_builder: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# 2. Discovery: filter to __K<K>__MTF, ignore legacy + daily-K
# ---------------------------------------------------------------------------


def test_discovery_accepts_only_phase_6d2_mtf_filenames(
    tmp_path: Path,
):
    """The discovery helper must filter strictly to Phase 6D-2
    __K<K>__MTF.research_day.json files. Phase 6D-1 daily K
    files and legacy unsuffixed files must be excluded even
    when they sit in the same engine directory."""
    dirs = _layout(tmp_path)
    safe = "SPY"
    engine_dir = dirs["artifact_root"] / "trafficflow" / safe
    engine_dir.mkdir(parents=True, exist_ok=True)
    # Write a proper MTF file (K=1), a Phase 6D-1 daily file
    # (K=2, no __MTF), and a legacy unsuffixed file. Only the
    # MTF file should be discovered.
    dates = _build_dates(10)
    per_day = {d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
               for d in dates}
    _write_mtf_artifact(
        dirs["artifact_root"], target="SPY",
        seed_run_id="seed", K=1, dates=dates,
        per_day_per_tf=per_day,
        timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
    )
    # Phase 6D-1 daily-K file (no __MTF suffix).
    daily_art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow",
        target_ticker="SPY",
        signal_source="",
        run_id="seed__K2",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 0.0,
            "sharpe_ratio": 0.0,
            "trigger_days": 0,
        },
        daily=[{"date": "2026-05-08", "target_close": 100.0,
                "pressure_signal": "Buy",
                "timeframe_pressure_signals": {"1d": "Buy"}}],
        K=2,
        timeframes=[],
    )
    ra.write_research_day_artifact(
        daily_art, engine_dir / "seed__K2.research_day.json",
    )
    # Legacy unsuffixed.
    legacy_art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow", target_ticker="SPY",
        signal_source="", run_id="legacy_seed",
        metric_basis="Close", persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={"total_capture_pct": 0.0, "sharpe_ratio": 0.0,
                 "trigger_days": 0},
        daily=[], K=1, timeframes=[],
    )
    ra.write_research_day_artifact(
        legacy_art, engine_dir / "legacy_seed.research_day.json",
    )

    discovered = cmab.list_mtf_trafficflow_artifacts(
        dirs["artifact_root"], "SPY",
    )
    assert [k for _, k in discovered] == [1]
    assert all(
        "__MTF.research_day.json" in p.name for p, _ in discovered
    )


# ---------------------------------------------------------------------------
# 3. K mismatch
# ---------------------------------------------------------------------------


def test_k_metadata_mismatch_is_reported_and_skipped(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day = {d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
               for d in dates}
    # Write 11 honest K artifacts (K=1..11) plus one mismatched
    # K=12 file whose internal K is 99.
    for k in range(1, 12):
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=dates,
            per_day_per_tf=per_day,
            timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        )
    _write_mtf_artifact(
        dirs["artifact_root"], target="SPY",
        seed_run_id="seed", K=12, dates=dates,
        per_day_per_tf=per_day,
        timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        K_in_artifact=99,  # mismatch
    )

    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"],
    )
    assert cmab.ISSUE_INPUT_ARTIFACT_K_MISMATCH in res.issue_codes
    # K=12 must NOT count toward coverage; the build refuses
    # because expected_k=1..12 is not fully met.
    assert cmab.ISSUE_MISSING_MTF_K_COVERAGE in res.issue_codes
    assert res.built is False
    assert res.artifact_path is None


# ---------------------------------------------------------------------------
# 4. Missing K coverage refuses to write
# ---------------------------------------------------------------------------


def test_missing_k_coverage_refuses_to_write(tmp_path: Path):
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day = {d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
               for d in dates}
    # Only K=1..5; K=6..12 are missing.
    for k in (1, 2, 3, 4, 5):
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=dates,
            per_day_per_tf=per_day,
            timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    assert cmab.ISSUE_MISSING_MTF_K_COVERAGE in res.issue_codes
    assert res.built is False
    assert res.artifact_path is None
    # And nothing under the confluence dir on disk.
    conf_root = dirs["artifact_root"] / "confluence"
    assert (
        not conf_root.exists()
        or not list(conf_root.rglob("*.research_day.json"))
    )


# ---------------------------------------------------------------------------
# 5. No MTF inputs at all
# ---------------------------------------------------------------------------


def test_no_mtf_inputs_returns_clean_issue(tmp_path: Path):
    dirs = _layout(tmp_path)
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"],
    )
    assert res.issue_codes == (cmab.ISSUE_NO_MTF_TRAFFICFLOW_ARTIFACTS,)
    assert res.built is False


# ---------------------------------------------------------------------------
# 6. Combine rule: all Buy / all Short / mixed / None
# ---------------------------------------------------------------------------


def test_all_buy_votes_produce_buy_with_correct_counts(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    _write_full_mtf_pipeline(
        dirs["artifact_root"], "SPY",
        dates=dates, per_day_per_tf=per_day,
    )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    assert res.built
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1] if art.daily else None
    assert last is not None
    assert last["confluence_signal"] == "Buy"
    # 12 K × 5 timeframes = 60 total cells, all Buy.
    assert last["buy_votes"] == 60
    assert last["short_votes"] == 0
    assert last["none_votes"] == 0
    assert last["agreement_active"] == 60
    assert last["agreement_total"] == 60
    assert last["active_count"] == 60
    assert last["available_count"] == 60


def test_all_short_votes_produce_short(tmp_path: Path):
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day = {
        d: {tf: "Short" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    _write_full_mtf_pipeline(
        dirs["artifact_root"], "SPY",
        dates=dates, per_day_per_tf=per_day,
    )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1]
    assert last["confluence_signal"] == "Short"
    assert last["short_votes"] == 60
    assert last["agreement_active"] == 60


def test_mixed_buy_short_votes_produce_none_with_preserved_counts(
    tmp_path: Path,
):
    """A single mixed-direction day must collapse to None and
    keep buy_votes / short_votes visible so the operator can
    audit why the day flipped."""
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    # Even K -> Buy on all timeframes; odd K -> Short on all.
    paths = []
    for k in cmab.DEFAULT_EXPECTED_K:
        signal = "Buy" if k % 2 == 0 else "Short"
        per_day = {
            d: {tf: signal for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
            for d in dates
        }
        paths.append(_write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=dates,
            per_day_per_tf=per_day,
            timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        ))
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1]
    assert last["confluence_signal"] == "None"
    # 6 Buy K × 5 timeframes = 30 buy votes.
    # 6 Short K × 5 timeframes = 30 short votes.
    assert last["buy_votes"] == 30
    assert last["short_votes"] == 30
    assert last["agreement_active"] == 0  # mixed -> None
    assert last["agreement_total"] == 60


def test_none_only_votes_produce_none_and_reduce_agreement(
    tmp_path: Path,
):
    """All cells voting None should produce confluence None
    with agreement_active=0. The agreement_total still reflects
    available (non-missing) slots so audit tooling can see the
    breakdown."""
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day = {
        d: {tf: "None" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    _write_full_mtf_pipeline(
        dirs["artifact_root"], "SPY",
        dates=dates, per_day_per_tf=per_day,
    )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1]
    assert last["confluence_signal"] == "None"
    assert last["buy_votes"] == 0
    assert last["short_votes"] == 0
    assert last["none_votes"] == 60
    assert last["agreement_active"] == 0
    assert last["agreement_total"] == 60  # 60 available slots


# ---------------------------------------------------------------------------
# 7. write=False writes nothing
# ---------------------------------------------------------------------------


def test_write_false_performs_no_disk_writes(tmp_path: Path):
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    _write_full_mtf_pipeline(
        dirs["artifact_root"], "SPY",
        dates=dates, per_day_per_tf=per_day,
    )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=False,
    )
    assert res.built is True
    assert res.artifact_path is None
    # No confluence artifact written to disk.
    conf_root = dirs["artifact_root"] / "confluence"
    assert (
        not conf_root.exists()
        or not list(conf_root.rglob("*.research_day.json"))
    )


# ---------------------------------------------------------------------------
# 8. Output schema readable by existing consumers
# ---------------------------------------------------------------------------


def test_output_artifact_is_readable_by_existing_readiness(
    tmp_path: Path,
):
    """The readiness layer reads ``active_count`` /
    ``available_count`` on the last daily row. The 6D-3
    artifact must populate those fields so the existing
    consumer keeps working."""
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    _write_full_mtf_pipeline(
        dirs["artifact_root"], "SPY",
        dates=dates, per_day_per_tf=per_day,
    )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1]
    # The readiness helper consumes these directly.
    assert isinstance(last.get("active_count"), int)
    assert isinstance(last.get("available_count"), int)
    assert art.engine == "confluence"
    # Timeframes must surface as a non-empty list >= 2 entries
    # so the bridge-coverage logic stays consistent.
    assert len(list(art.timeframes)) >= 2


def test_output_artifact_is_readable_by_daily_signal_board_logic(
    tmp_path: Path,
):
    """``daily_signal_board._signal_from_refs`` reads
    ``confluence_signal`` from the latest daily row. The 6D-3
    artifact must carry that field directly."""
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    _write_full_mtf_pipeline(
        dirs["artifact_root"], "SPY",
        dates=dates, per_day_per_tf=per_day,
    )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    # Build a transient _ArtifactRef-like SimpleNamespace and
    # feed it through the board's helper to confirm the daily-
    # row schema is consumable.
    art = ra.read_research_day_artifact(res.artifact_path)
    from types import SimpleNamespace
    ref = SimpleNamespace(
        path=res.artifact_path, artifact=art,
        last_date=art.daily[-1].get("date"), mtime=0.0,
    )
    signal = board._signal_from_refs(ref, None)
    assert signal == "Buy"
    active, total = board._confluence_active_total(ref)
    assert active == 60
    assert total == 60


# ---------------------------------------------------------------------------
# 9. Readiness integration: full fixture clears confluence gates
# ---------------------------------------------------------------------------


def test_full_pipeline_fixture_clears_missing_and_stale_confluence(
    tmp_path: Path,
):
    """End-to-end: with all upstream stages in place AND fresh
    Phase 6D-2 MTF artifacts for K=1..12, the Phase 6D-3
    builder produces a Confluence artifact that clears BOTH
    missing_confluence_day_artifact AND
    stale_confluence_day_artifact in the readiness verdict.

    The leader gate still requires the catalogue health report
    to be permissive and the upstream impactsearch / stackbuilder
    day artifacts to exist - we lay all of that down in the
    fixture so the test focuses on the confluence stage."""
    dirs = _layout(tmp_path)
    # Cache filename.
    (dirs["cache_dir"] / "SPY_precomputed_results.pkl").write_bytes(b"p")
    # Multi-timeframe libraries (>= 2 of 1wk / 1mo / 3mo / 1y).
    for interval in ("1wk", "1mo"):
        (dirs["signal_library_dir"]
         / f"SPY_stable_v1_0_0_{interval}.pkl").write_bytes(b"x")
    # ImpactSearch + StackBuilder day artifacts (fresh).
    for engine in ("impactsearch", "stackbuilder"):
        target_dir = dirs["artifact_root"] / engine / "SPY"
        target_dir.mkdir(parents=True, exist_ok=True)
        art = ra.ResearchDayArtifact(
            artifact_version=ra.ARTIFACT_VERSION,
            engine=engine, target_ticker="SPY",
            signal_source="SPY" if engine == "impactsearch" else "",
            run_id=f"fresh_{engine}",
            metric_basis="Close", persist_skip_bars=1,
            generated_at="2026-05-08T00:00:00+00:00",
            summary={"total_capture_pct": 10.0,
                     "sharpe_ratio": 0.1, "trigger_days": 3},
            daily=[{"date": "2026-05-08",
                    "target_close": 100.0,
                    "target_return_pct": 0.0,
                    "daily_capture_pct": 0.0,
                    "cumulative_capture_pct": 1.0,
                    "is_trigger_day": True}],
            K=1, members=["AAA"],
            timeframes=[],
        )
        ra.write_research_day_artifact(
            art, target_dir / "SPY.research_day.json",
        )
    # StackBuilder leaderboard directory (presence-only).
    (dirs["stackbuilder_root"] / "SPY" / "seedTC").mkdir(
        parents=True, exist_ok=True,
    )
    (dirs["stackbuilder_root"] / "SPY" / "seedTC"
     / "combo_leaderboard.xlsx").write_bytes(b"placeholder")
    # 12 fresh MTF artifacts for K=1..12.
    dates = _build_dates(10, end="2026-05-08")
    per_day = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    _write_full_mtf_pipeline(
        dirs["artifact_root"], "SPY",
        dates=dates, per_day_per_tf=per_day,
    )
    # Health report - empty blocks list.
    (dirs["artifact_root"] / "catalogue_health_report.json"
     ).write_text(json.dumps({
        "schema": "catalogue_health_v1",
        "generated_at": "2026-05-08T00:00:00+00:00",
        "by_target": [{
            "target_ticker": "SPY", "engines_blocked": [],
        }],
    }), encoding="utf-8")

    # Drive Phase 6D-3 to write the Confluence artifact.
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    assert res.built, res.issue_codes
    assert res.artifact_path is not None
    # The Confluence artifact's last_date matches the fixture
    # data, not "today" - the builder must not stamp a fresh
    # date.
    assert res.last_date == dates[-1], (
        f"last_date must come from source rows; got "
        f"{res.last_date!r}, expected {dates[-1]!r}"
    )

    # Readiness against the same artifact_root with
    # current_as_of_date set BEFORE the fixture's last date so
    # the artifact is "current".
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date=dates[-1],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT
        not in r.issue_codes
    ), r.issue_codes
    assert (
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT
        not in r.issue_codes
    ), r.issue_codes
    # And the bridge issue is also gone given the full MTF
    # coverage written by the fixture.
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        not in r.issue_codes
    ), r.issue_codes


# ---------------------------------------------------------------------------
# 10. Stale sources stay stale
# ---------------------------------------------------------------------------


def test_stale_mtf_sources_produce_stale_confluence(tmp_path: Path):
    """If every MTF source carries last_date = 2024-01-21,
    the Confluence artifact also reports last_date = 2024-01-21.
    The builder must not stamp a fresh date onto stale data."""
    dirs = _layout(tmp_path)
    stale_dates = _build_dates(10, end="2024-01-21")
    per_day = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in stale_dates
    }
    _write_full_mtf_pipeline(
        dirs["artifact_root"], "SPY",
        dates=stale_dates, per_day_per_tf=per_day,
    )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    assert res.built
    art = ra.read_research_day_artifact(res.artifact_path)
    assert art.daily[-1]["date"] == stale_dates[-1]
    # And readiness reports stale, NOT missing.
    (dirs["cache_dir"] / "SPY_precomputed_results.pkl").write_bytes(b"p")
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT in r.issue_codes
    )
    assert (
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT
        not in r.issue_codes
    )


# ---------------------------------------------------------------------------
# 11. Run id helper is deterministic + tolerates empty input
# ---------------------------------------------------------------------------


def test_artifact_run_id_helper_defaults_and_overrides():
    assert cmab.artifact_run_id_for_mtf_consensus() == "mtf_consensus"
    assert cmab.artifact_run_id_for_mtf_consensus(
        run_id="custom",
    ) == "custom"
    assert cmab.artifact_run_id_for_mtf_consensus(
        run_id="",
    ) == "mtf_consensus"
    assert cmab.artifact_run_id_for_mtf_consensus(
        seed_run_id="seed", run_id="custom",
    ) == "custom__from__seed"
