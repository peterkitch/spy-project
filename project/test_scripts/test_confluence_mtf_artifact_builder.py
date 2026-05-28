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


# ---------------------------------------------------------------------------
# 12. Audit Issue 1: source seed-run group selection
# ---------------------------------------------------------------------------


def test_two_complete_source_groups_picks_freshest(tmp_path: Path):
    """PR #198 audit (Issue 1): when two complete Phase 6D-2
    seed-run groups exist for the same ticker, the builder
    must pick the FRESHEST group by source last_date - not
    the alphabetically-first prefix.

    Fixture:
      - Group ``aaa_old``: K=1..12, dates 2024-01..,
        timeframe signals all "Short". Alphabetically FIRST
        (would have won under the buggy implementation).
      - Group ``zzz_new``: K=1..12, dates 2026-05..,
        timeframe signals all "Buy". Alphabetically LAST
        but freshest.

    Expected: the resulting Confluence artifact carries the
    fresh group's last_date and confluence_signal=Buy on the
    final day.
    """
    dirs = _layout(tmp_path)
    old_dates = _build_dates(10, end="2024-01-31")
    new_dates = _build_dates(10, end="2026-05-08")
    old_per_day = {
        d: {tf: "Short" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in old_dates
    }
    new_per_day = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in new_dates
    }
    for k in cmab.DEFAULT_EXPECTED_K:
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="aaa_old", K=k, dates=old_dates,
            per_day_per_tf=old_per_day,
            timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        )
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="zzz_new", K=k, dates=new_dates,
            per_day_per_tf=new_per_day,
            timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        )

    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY",
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    assert res.built, res.issue_codes
    assert res.last_date == new_dates[-1], (
        f"builder picked the older group; last_date={res.last_date!r} "
        f"vs expected {new_dates[-1]!r}"
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1]
    assert last["date"] == new_dates[-1]
    assert last["confluence_signal"] == "Buy", (
        f"builder picked old (Short) group; got {last!r}"
    )
    assert last["buy_votes"] == 60
    assert last["short_votes"] == 0
    # source_trafficflow_mtf_run_ids must come ONLY from the
    # selected (newer) group; no run id should start with the
    # older "aaa_old" prefix.
    src_ids = last["source_trafficflow_mtf_run_ids"]
    assert all("aaa_old" not in s for s in src_ids), src_ids
    assert all("zzz_new" in s for s in src_ids), src_ids
    assert len(src_ids) == 12


def test_split_k_across_groups_refuses_to_write(tmp_path: Path):
    """PR #198 audit (Issue 1): K=1 exists only in an older
    group; K=2..12 exist only in a newer group. No single
    group has full K coverage, so the builder must return
    ``missing_mtf_k_coverage`` and write nothing - even though
    the UNION across groups would superficially cover K=1..12.
    """
    dirs = _layout(tmp_path)
    old_dates = _build_dates(10, end="2024-01-31")
    new_dates = _build_dates(10, end="2026-05-08")
    per_day_old = {
        d: {tf: "Short" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in old_dates
    }
    per_day_new = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in new_dates
    }
    # Old group: K=1 only.
    _write_mtf_artifact(
        dirs["artifact_root"], target="SPY",
        seed_run_id="old_seed", K=1, dates=old_dates,
        per_day_per_tf=per_day_old,
        timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
    )
    # New group: K=2..12 only.
    for k in range(2, 13):
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="new_seed", K=k, dates=new_dates,
            per_day_per_tf=per_day_new,
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
    # No confluence file written.
    conf_root = dirs["artifact_root"] / "confluence"
    assert (
        not conf_root.exists()
        or not list(conf_root.rglob("*.research_day.json"))
    )


def test_duplicate_k_within_same_group_is_deterministic(
    tmp_path: Path,
):
    """The Phase 6D-2 writer produces ``<prefix>__K<K>__MTF.research_day.json``
    which is filename-unique per (prefix, K). A real intra-
    group K duplicate cannot occur on disk through the
    documented writer path. This test pins the deterministic
    behavior on a contrived two-distinct-prefix-but-shared-K
    fixture: each prefix represents one coherent group, and
    the builder must pick exactly ONE group rather than
    crossing them. The chosen group's source run ids must be
    a single coherent set."""
    dirs = _layout(tmp_path)
    dates = _build_dates(10, end="2026-05-08")
    per_day = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    # Two complete groups, identical freshness. Tie-breaker
    # falls to alphabetic prefix (asc).
    for k in cmab.DEFAULT_EXPECTED_K:
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="aa_first", K=k, dates=dates,
            per_day_per_tf=per_day,
            timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        )
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="zz_second", K=k, dates=dates,
            per_day_per_tf=per_day,
            timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    assert res.built
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1]
    src_ids = last["source_trafficflow_mtf_run_ids"]
    # All 12 run ids share a single prefix - never a mix.
    prefixes = {s.rsplit("__K", 1)[0] for s in src_ids}
    assert len(prefixes) == 1, (
        f"selected group must be single-prefix; got {prefixes}"
    )


# ---------------------------------------------------------------------------
# 13. Audit Issue 2: missing-vote count is per cell, not per timeframe
# ---------------------------------------------------------------------------


def test_all_k_omit_one_timeframe_records_k_missing_votes(
    tmp_path: Path,
):
    """PR #198 audit (Issue 2): all 12 K artifacts omit a
    single expected timeframe (e.g. ``1y``). The honest
    missing-vote count is K=12 per day, not 1.
    ``partial_timeframe_coverage`` must also surface."""
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    # Each artifact only carries 4 of the 5 expected timeframes.
    partial_tfs = ["1d", "1wk", "1mo", "3mo"]
    per_day = {
        d: {tf: "Buy" for tf in partial_tfs} for d in dates
    }
    for k in cmab.DEFAULT_EXPECTED_K:
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=dates,
            per_day_per_tf=per_day,
            timeframes=list(partial_tfs),
        )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    assert res.built
    assert (
        cmab.ISSUE_PARTIAL_TIMEFRAME_COVERAGE in res.issue_codes
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1]
    # 12 K * 4 buy-carrying timeframes = 48 Buy votes.
    # 12 K * 1 missing timeframe (1y) = 12 missing votes.
    assert last["buy_votes"] == 48
    assert last["short_votes"] == 0
    assert last["none_votes"] == 0
    assert last["missing_votes"] == 12, (
        "missing_votes must count cells, not timeframe labels; "
        f"got {last['missing_votes']}"
    )
    assert last["active_count"] == 48
    assert last["available_count"] == 48  # excludes 12 missing
    assert last["agreement_total"] == 48


def test_one_k_omits_one_timeframe_records_single_missing_vote(
    tmp_path: Path,
):
    """PR #198 audit (Issue 2): exactly one K artifact omits a
    timeframe; the rest carry it. The honest missing count is
    1, not the K total."""
    dirs = _layout(tmp_path)
    dates = _build_dates(10)
    per_day_partial = {
        d: {tf: "Buy" for tf in ("1d", "1wk", "1mo", "3mo")}
        for d in dates
    }
    per_day_full = {
        d: {tf: "Buy" for tf in cmab.DEFAULT_EXPECTED_TIMEFRAMES}
        for d in dates
    }
    # K=1 omits the 1y timeframe.
    _write_mtf_artifact(
        dirs["artifact_root"], target="SPY",
        seed_run_id="seed", K=1, dates=dates,
        per_day_per_tf=per_day_partial,
        timeframes=["1d", "1wk", "1mo", "3mo"],
    )
    # K=2..12 carry all five timeframes.
    for k in range(2, 13):
        _write_mtf_artifact(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=dates,
            per_day_per_tf=per_day_full,
            timeframes=list(cmab.DEFAULT_EXPECTED_TIMEFRAMES),
        )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    assert res.built
    # K=2..12 all carry 1y, so the union of seen timeframes
    # covers the expected set and partial_timeframe_coverage
    # should NOT fire.
    assert (
        cmab.ISSUE_PARTIAL_TIMEFRAME_COVERAGE
        not in res.issue_codes
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    last = art.daily[-1]
    # K=1: 4 Buy + 1 missing = 5 cells
    # K=2..12: 5 Buy * 11 = 55 cells
    # Total: 59 Buy, 1 missing.
    assert last["buy_votes"] == 59
    assert last["missing_votes"] == 1
    assert last["available_count"] == 59  # active + none
    assert last["agreement_total"] == 59


def test_full_kxtf_happy_path_still_60_votes(tmp_path: Path):
    """Regression guard: with all 12 K * 5 expected timeframes
    present and all-Buy, the previous "60 votes total"
    happy-path remains unchanged under the new
    per-cell counting."""
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
    assert last["buy_votes"] == 60
    assert last["short_votes"] == 0
    assert last["none_votes"] == 0
    assert last["missing_votes"] == 0
    assert last["available_count"] == 60
    assert last["agreement_total"] == 60


# ---------------------------------------------------------------------------
# 14. Audit Issue 3: common-date group selection
# ---------------------------------------------------------------------------


def _write_mtf_with_explicit_dates(
    artifact_root: Path,
    *,
    target: str,
    seed_run_id: str,
    K: int,
    dates: list[str],
    signal: str = "Buy",
    timeframes: Optional[list[str]] = None,
) -> Path:
    """Write a Phase 6D-2 MTF artifact whose daily rows cover an
    explicit caller-supplied date list. Convenience wrapper
    around ``_write_mtf_artifact``."""
    tfs = list(timeframes or cmab.DEFAULT_EXPECTED_TIMEFRAMES)
    per_day = {d: {tf: signal for tf in tfs} for d in dates}
    return _write_mtf_artifact(
        artifact_root,
        target=target,
        seed_run_id=seed_run_id,
        K=K,
        dates=dates,
        per_day_per_tf=per_day,
        timeframes=tfs,
    )


def test_one_fresh_k_does_not_make_group_current(tmp_path: Path):
    """PR #198 audit Issue 3: K=1 has rows through 2026-05-08
    while K=2..12 only have rows through 2024-01-31. The builder
    must NOT use K=1's fresh-only dates; the artifact's
    last_date is the newest date where every expected K has a
    row (2024-01-31). ``partial_mtf_date_coverage`` should
    surface since K=1 had rows beyond the common-date set."""
    dirs = _layout(tmp_path)
    short_dates = _build_dates(10, end="2024-01-31")
    fresh_extension = _build_dates(8, end="2026-05-08")
    # K=1: short_dates UNION fresh_extension (covers everything
    # plus the 2026 dates).
    k1_dates = sorted(set(short_dates) | set(fresh_extension))
    _write_mtf_with_explicit_dates(
        dirs["artifact_root"], target="SPY",
        seed_run_id="seed", K=1, dates=k1_dates,
    )
    # K=2..12: only short_dates.
    for k in range(2, 13):
        _write_mtf_with_explicit_dates(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=short_dates,
        )

    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    assert res.built
    expected_last = short_dates[-1]
    assert res.last_date == expected_last, (
        "Confluence last_date must be the newest common full-K "
        f"date ({expected_last}), not K=1's max ({k1_dates[-1]}); "
        f"got {res.last_date}"
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    assert art.daily[-1]["date"] == expected_last
    # K=1 had rows beyond the common date set -> soft issue must
    # surface.
    assert (
        cmab.ISSUE_PARTIAL_MTF_DATE_COVERAGE in res.issue_codes
    ), res.issue_codes
    # Every emitted row has full K coverage (no per-row
    # missing_votes from absent K rows).
    last = art.daily[-1]
    assert last["missing_votes"] == 0, last
    assert last["buy_votes"] == 60  # 12 K * 5 tfs all Buy


def test_group_with_no_common_k_dates_writes_nothing(tmp_path: Path):
    """PR #198 audit Issue 3: a full-K group whose K artifacts
    cover entirely disjoint date sets must NOT produce a
    Confluence artifact. ``no_common_mtf_k_dates`` surfaces and
    no file is written."""
    dirs = _layout(tmp_path)
    old_dates = _build_dates(10, end="2024-01-31")
    new_dates = _build_dates(10, end="2026-05-08")
    # K=1..6: old_dates only.
    for k in (1, 2, 3, 4, 5, 6):
        _write_mtf_with_explicit_dates(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=old_dates,
        )
    # K=7..12: new_dates only.
    for k in (7, 8, 9, 10, 11, 12):
        _write_mtf_with_explicit_dates(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=new_dates,
        )
    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    assert cmab.ISSUE_NO_COMMON_MTF_K_DATES in res.issue_codes
    assert res.built is False
    assert res.artifact_path is None
    conf_root = dirs["artifact_root"] / "confluence"
    assert (
        not conf_root.exists()
        or not list(conf_root.rglob("*.research_day.json"))
    )


def test_group_selection_uses_freshest_common_full_k_date(
    tmp_path: Path,
):
    """PR #198 audit Issue 3: group A has K=1 with a 2026 fresh
    extension but K=2..12 only through 2024-01-31 (common date
    max = 2024-01-31). Group B has K=1..12 all through 2024-03-15
    (common date max = 2024-03-15). The builder must select
    group B because its NEWEST COMMON full-K date is fresher,
    NOT group A whose max per-K last_date is later but whose
    common coverage is older."""
    dirs = _layout(tmp_path)
    a_short = _build_dates(10, end="2024-01-31")
    a_fresh_ext = _build_dates(5, end="2026-05-08")
    a_k1_dates = sorted(set(a_short) | set(a_fresh_ext))
    b_dates = _build_dates(10, end="2024-03-15")

    # Group A: K=1 fresh extension, K=2..12 short.
    _write_mtf_with_explicit_dates(
        dirs["artifact_root"], target="SPY",
        seed_run_id="aa_groupA", K=1, dates=a_k1_dates,
    )
    for k in range(2, 13):
        _write_mtf_with_explicit_dates(
            dirs["artifact_root"], target="SPY",
            seed_run_id="aa_groupA", K=k, dates=a_short,
        )
    # Group B: K=1..12 all cover b_dates (max common = 2024-03-15).
    for k in range(1, 13):
        _write_mtf_with_explicit_dates(
            dirs["artifact_root"], target="SPY",
            seed_run_id="bb_groupB", K=k, dates=b_dates,
        )

    res = cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    assert res.built
    # Group B's common max is 2024-03-15, beating group A's
    # 2024-01-31. Builder must pick B.
    expected = b_dates[-1]
    assert res.last_date == expected, (
        f"builder picked the group with the later single-K "
        f"date instead of the freshest COMMON date; got "
        f"{res.last_date!r}, expected {expected!r}"
    )
    art = ra.read_research_day_artifact(res.artifact_path)
    src_ids = art.daily[-1]["source_trafficflow_mtf_run_ids"]
    assert all("bb_groupB" in s for s in src_ids), src_ids
    assert all("aa_groupA" not in s for s in src_ids), src_ids


def test_partial_date_group_does_not_clear_stale_readiness(
    tmp_path: Path,
):
    """Readiness integration: even though K=1 carries fresh
    2026 rows, the Confluence artifact's last_date is clamped
    to the common-full-K date (2024-01-31 in this fixture).
    Readiness with the default cutoff therefore reports
    stale_confluence_day_artifact - the partial-date group
    cannot accidentally clear current-leader status."""
    dirs = _layout(tmp_path)
    (dirs["cache_dir"] / "SPY_precomputed_results.pkl").write_bytes(b"p")
    short_dates = _build_dates(10, end="2024-01-31")
    fresh_extension = _build_dates(5, end="2026-05-08")
    k1_dates = sorted(set(short_dates) | set(fresh_extension))
    _write_mtf_with_explicit_dates(
        dirs["artifact_root"], target="SPY",
        seed_run_id="seed", K=1, dates=k1_dates,
    )
    for k in range(2, 13):
        _write_mtf_with_explicit_dates(
            dirs["artifact_root"], target="SPY",
            seed_run_id="seed", K=k, dates=short_dates,
        )
    cmab.build_confluence_from_mtf_trafficflow(
        "SPY", artifact_root=dirs["artifact_root"], write=True,
    )
    # Readiness with default (today) cutoff: 2024-01-31 is far
    # in the past -> stale_confluence_day_artifact must fire.
    r = cpr.inspect_ticker_pipeline(
        "SPY",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT in r.issue_codes
    ), r.issue_codes
    assert (
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT
        not in r.issue_codes
    )
    assert r.leader_eligible is False


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


# ---------------------------------------------------------------------------
# PR B (zero-return-loss convention) canonical-equivalence test for
# confluence_mtf_artifact_builder. The wins/losses predicate is pinned
# to canonical_scoring.py:207-209: losses = n_trigger - wins so
# zero-return BUY / SHORT triggers count as losses, and
# wins + losses == trigger_days at the summary level.
# ---------------------------------------------------------------------------


def test_canonical_equivalence_wins_plus_losses_equals_trigger_days(
    tmp_path: Path,
):
    """All-Buy fixture produces wins = trigger_days and losses = 0
    (every BUY day has a strictly positive return), but the invariant
    wins + losses == trigger_days must hold by construction under
    the local canonical-equivalent predicate."""
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
    summary = art.summary
    assert summary["wins"] + summary["losses"] == summary["trigger_days"]
    # Sanity: count BUY trigger days from the emitted daily rows and
    # confirm the canonical equivalence at the row level.
    trigger_caps = [
        float(r["daily_capture_pct"]) for r in art.daily
        if r["is_trigger_day"]
        and r.get("cumulative_capture_pct") is not None
    ]
    canonical_wins = sum(1 for v in trigger_caps if v > 0)
    canonical_losses = len(trigger_caps) - canonical_wins
    assert summary["wins"] == canonical_wins
    assert summary["losses"] == canonical_losses


def test_canonical_equivalence_all_short_and_mixed(tmp_path: Path):
    """All-Short fixture: every SHORT day produces a strictly negative
    SHORT capture (since target_close strictly increases, raw_return
    > 0 and SHORT capture = -raw_return < 0). All trigger days are
    losses; wins == 0; the invariant wins + losses == trigger_days
    holds."""
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
    summary = art.summary
    assert summary["wins"] == 0
    assert summary["wins"] + summary["losses"] == summary["trigger_days"]
