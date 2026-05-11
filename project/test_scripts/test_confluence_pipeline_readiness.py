"""Phase 6C-8 tests for confluence_pipeline_readiness.

Pins the readiness contract that the Daily Signal Board's leader
gate consumes:

  - current_as_of_date is honored exactly when explicit
  - PRJCT9_RESEARCH_AS_OF_DATE env var is honored when present
  - default_research_as_of_date falls back to the most recent
    weekday strictly before UTC ``now``
  - confluence stale -> leader_eligible=False even when TrafficFlow
    is newer
  - missing multi-timeframe TrafficFlow bridge surfaces as an
    issue code
  - under-review health blocks leader eligibility
  - ticker with current confluence + required evidence + usable
    agreement fields is leader_eligible=True
  - no yfinance import in the module
  - no live engine import in the module
  - no disk writes during inspection
"""
from __future__ import annotations

import ast
import json
import os
import re
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


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache = tmp_path / "cache"
    artifact = tmp_path / "artifacts"
    stack = tmp_path / "stackbuilder"
    sig = tmp_path / "siglib"
    for d in (cache, artifact, stack, sig):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache,
        "artifact_root": artifact,
        "stackbuilder_root": stack,
        "signal_library_dir": sig,
    }


def _write_cache_filename(cache_dir: Path, ticker: str) -> Path:
    safe = ticker.replace("^", "_")
    p = cache_dir / f"{safe}_precomputed_results.pkl"
    p.write_bytes(b"placeholder")  # readiness never opens this
    return p


def _write_artifact(
    artifact_root: Path,
    *,
    engine: str,
    ticker: str,
    last_date: str,
    timeframes: Optional[list[str]] = None,
    K: Optional[int] = None,
    members: Optional[list[str]] = None,
    summary_overrides: Optional[dict[str, Any]] = None,
    daily_extra: Optional[dict[str, Any]] = None,
    name_suffix: str = "",
) -> Path:
    """Write a real ``research_day_v1`` artifact under
    ``output/research_artifacts/<engine>/<TARGET>/`` via the
    canonical research_artifacts writer. Readiness reads these
    by parsing the JSON; using the real writer keeps the
    fixture schema in lockstep with production."""
    safe = ticker.replace("^", "_")
    target_dir = artifact_root / engine / safe
    target_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "total_capture_pct": 10.0,
        "sharpe_ratio": 0.05,
        "trigger_days": 3,
    }
    if summary_overrides:
        summary.update(summary_overrides)
    daily = [
        {
            "date": last_date,
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 1.0,
            "is_trigger_day": True,
        },
    ]
    if engine == "confluence":
        daily[-1].update({
            "confluence_signal": "Buy",
            "confluence_tier": "buy",
            "timeframe_signals": {},
            "alignment_pct": 1.0,
            "buy_count": 3,
            "short_count": 0,
            "none_count": 2,
            "active_count": 3,
            "available_count": 5,
        })
    if daily_extra:
        daily[-1].update(daily_extra)
    artifact = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine=engine,
        target_ticker=ticker,
        signal_source="" if engine != "impactsearch" else "SRC",
        run_id=f"test{name_suffix}",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-11T00:00:00+00:00",
        summary=summary,
        daily=daily,
        K=K,
        members=members or [],
        timeframes=list(timeframes) if timeframes else [],
    )
    name = (
        f"{safe}{name_suffix}.research_day.json"
        if name_suffix
        else f"{safe}.research_day.json"
    )
    out = target_dir / name
    return ra.write_research_day_artifact(artifact, out)


def _write_full_trafficflow_pipeline(
    artifact_root: Path,
    ticker: str,
    *,
    last_date: str,
    timeframes: Optional[list[str]] = None,
) -> list[Path]:
    """Write the full happy-path TrafficFlow fixture: one
    multi-timeframe artifact per K in 1..12. This is the only way
    a ticker can clear both the bridge gate
    (``missing_multitimeframe_trafficflow_bridge``) and the
    K-coverage gate (``insufficient_trafficflow_k_coverage``)
    under the Phase 6C-8 tightened eligibility rules.
    """
    tfs = list(timeframes or ["1d", "1wk", "1mo", "3mo", "1y"])
    out = []
    for k in cpr.EXPECTED_TRAFFICFLOW_K_RANGE:
        out.append(_write_artifact(
            artifact_root, engine="trafficflow", ticker=ticker,
            last_date=last_date, K=k, timeframes=tfs,
            name_suffix=f"__K{k}",
        ))
    return out


def _write_health_report(
    artifact_root: Path,
    *,
    blocked_targets: dict[str, list[str]],
) -> Path:
    """Write a minimal catalogue_health_v1 report with the given
    blocked-engine map. ``readiness`` reads only ``by_target``."""
    by_target = []
    for ticker, engines in blocked_targets.items():
        by_target.append({
            "target_ticker": ticker,
            "engines_blocked": engines,
        })
    payload = {
        "schema": "catalogue_health_v1",
        "generated_at": "2026-05-11T00:00:00+00:00",
        "by_target": by_target,
    }
    p = artifact_root / "catalogue_health_report.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. current_as_of_date resolution
# ---------------------------------------------------------------------------


def test_explicit_current_as_of_date_is_honored_exactly():
    assert cpr.resolve_current_as_of_date(
        explicit="2026-05-08",
    ) == "2026-05-08"


def test_env_var_current_as_of_date_is_honored():
    assert cpr.resolve_current_as_of_date(
        env={cpr.ENV_RESEARCH_AS_OF_DATE: "2026-04-30"},
    ) == "2026-04-30"


def test_default_research_as_of_date_is_previous_weekday():
    # Saturday: previous weekday is Friday.
    sat = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    assert cpr.default_research_as_of_date(sat) == "2026-05-08"
    # Sunday: previous weekday is still Friday.
    sun = datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc)
    assert cpr.default_research_as_of_date(sun) == "2026-05-08"
    # Monday: previous weekday is Friday (strictly-before rule).
    mon = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    assert cpr.default_research_as_of_date(mon) == "2026-05-08"
    # Tuesday: previous weekday is Monday.
    tue = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
    assert cpr.default_research_as_of_date(tue) == "2026-05-11"


def test_resolve_falls_back_to_default_when_env_malformed():
    # Bad env date string falls back to the conservative default,
    # which depends on ``now`` - explicit ``now`` keeps this test
    # deterministic.
    mon = datetime(2026, 5, 11, tzinfo=timezone.utc)
    assert cpr.resolve_current_as_of_date(
        env={cpr.ENV_RESEARCH_AS_OF_DATE: "not-a-date"},
        now=mon,
    ) == "2026-05-08"


# ---------------------------------------------------------------------------
# 2. Stale confluence -> not leader-eligible (even with newer TF)
# ---------------------------------------------------------------------------


def test_confluence_stale_blocks_leader_even_when_trafficflow_is_newer(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    # Confluence: STALE.
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-01-21", timeframes=["1d", "1wk", "1mo"],
    )
    # TrafficFlow: NEWER (recent).
    _write_artifact(
        dirs["artifact_root"], engine="trafficflow", ticker="SPY",
        last_date="2026-05-08", K=1,
    )
    # Stackbuilder + impactsearch: present.
    _write_artifact(
        dirs["artifact_root"], engine="stackbuilder", ticker="SPY",
        last_date="2026-05-08", K=1, members=["AAA"],
    )
    _write_artifact(
        dirs["artifact_root"], engine="impactsearch", ticker="SPY",
        last_date="2026-05-08",
    )

    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert r.leader_eligible is False
    assert cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT in r.issue_codes
    conf_stage = next(
        s for s in r.stages
        if s.stage == cpr.STAGE_CONFLUENCE_DAY_ARTIFACT
    )
    assert conf_stage.present is True
    assert conf_stage.current is False
    tf_stage = next(
        s for s in r.stages
        if s.stage == cpr.STAGE_TRAFFICFLOW_DAY_ARTIFACTS
    )
    # The freshness of TrafficFlow does NOT promote the verdict.
    assert tf_stage.current is True
    assert r.leader_eligible is False


# ---------------------------------------------------------------------------
# 3. Missing multi-timeframe TrafficFlow bridge issue code
# ---------------------------------------------------------------------------


def test_missing_multitimeframe_trafficflow_bridge_is_surfaced(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    # Only single-timeframe (K=1) TrafficFlow artifact exists.
    _write_artifact(
        dirs["artifact_root"], engine="trafficflow", ticker="SPY",
        last_date="2026-05-08", K=1,
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        in r.issue_codes
    ), r.issue_codes
    # And the same code is raised even when ALL stages are present
    # but TrafficFlow artifacts still lack multi-timeframe coverage.
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-05-08", timeframes=["1d", "1wk", "1mo"],
    )
    _write_artifact(
        dirs["artifact_root"], engine="stackbuilder", ticker="SPY",
        last_date="2026-05-08", K=1, members=["AAA"],
    )
    _write_artifact(
        dirs["artifact_root"], engine="impactsearch", ticker="SPY",
        last_date="2026-05-08",
    )
    r2 = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        in r2.issue_codes
    )
    # Phase 6C-8 audit-tighten: the bridge-missing code now BLOCKS
    # leader eligibility, even with a present + current Confluence
    # artifact.
    assert r2.leader_eligible is False, r2.issue_codes


def test_missing_bridge_blocks_eligibility_when_confluence_is_current(
    tmp_path: Path,
):
    """Reproduces the audit finding: a current Confluence artifact
    is NOT enough to grant leader eligibility when the multi-
    timeframe TrafficFlow / K-build bridge is missing. Only the
    pipeline as a whole earns a podium spot."""
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-05-08",
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    # Single-timeframe TrafficFlow only - the bridge isn't built.
    _write_artifact(
        dirs["artifact_root"], engine="trafficflow", ticker="SPY",
        last_date="2026-05-08", K=1,
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
        fast_path_when_no_confluence=False,
    )
    assert r.leader_eligible is False
    assert r.ranking_allowed is False
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        in r.issue_codes
    )


def test_insufficient_trafficflow_k_coverage_blocks_eligibility(
    tmp_path: Path,
):
    """The audit specifies that
    insufficient_trafficflow_k_coverage also blocks eligibility:
    a single K-build is not the same pipeline as all 12.

    Phase 6D-2 audit-tighten: the bridge check is now also gated
    on full K coverage. A single MTF K=1 artifact no longer
    clears the bridge for the whole ticker, so both
    insufficient_trafficflow_k_coverage AND
    missing_multitimeframe_trafficflow_bridge fire on this
    fixture. Either one blocks eligibility independently.
    """
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-05-08",
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    # ONE multi-timeframe TrafficFlow artifact, K=1 only.
    _write_artifact(
        dirs["artifact_root"], engine="trafficflow", ticker="SPY",
        last_date="2026-05-08", K=1,
        timeframes=["1d", "1wk", "1mo"],
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE
        in r.issue_codes
    )
    # Phase 6D-2: a single-K MTF artifact no longer clears the
    # bridge for the ticker.
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        in r.issue_codes
    )
    assert r.leader_eligible is False


# ---------------------------------------------------------------------------
# 4. Health-blocked ticker is not leader-eligible
# ---------------------------------------------------------------------------


def test_under_review_health_blocks_leader_eligibility(tmp_path: Path):
    """Health-blocked tickers fail the gate even when every
    upstream stage is fresh + the multi-timeframe TrafficFlow
    bridge is in place + every K=1..12 is covered. We build the
    full happy-path fixture and then layer the health block on
    top so the test isolates the health gate from the bridge /
    K-coverage gates."""
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-05-08",
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    _write_full_trafficflow_pipeline(
        dirs["artifact_root"], "SPY", last_date="2026-05-08",
    )
    _write_artifact(
        dirs["artifact_root"], engine="stackbuilder", ticker="SPY",
        last_date="2026-05-08", K=1, members=["AAA"],
    )
    _write_artifact(
        dirs["artifact_root"], engine="impactsearch", ticker="SPY",
        last_date="2026-05-08",
    )
    _write_health_report(
        dirs["artifact_root"],
        blocked_targets={"SPY": ["confluence"]},
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert r.leader_eligible is False
    assert cpr.ISSUE_HEALTH_REPORT_BLOCKED in r.issue_codes


# ---------------------------------------------------------------------------
# 5. Happy path: leader-eligible
# ---------------------------------------------------------------------------


def test_ticker_with_full_pipeline_is_leader_eligible(
    tmp_path: Path,
):
    """Happy path under the Phase 6C-8 tightened gate. Requires:
    cache filename, ImpactSearch + StackBuilder + multi-timeframe
    TrafficFlow per K=1..12 + Confluence artifacts all current,
    multi-timeframe libraries, no health block."""
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-05-08",
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    _write_full_trafficflow_pipeline(
        dirs["artifact_root"], "SPY", last_date="2026-05-08",
    )
    _write_artifact(
        dirs["artifact_root"], engine="stackbuilder", ticker="SPY",
        last_date="2026-05-08", K=1, members=["AAA"],
    )
    _write_artifact(
        dirs["artifact_root"], engine="impactsearch", ticker="SPY",
        last_date="2026-05-08",
    )
    sig = dirs["signal_library_dir"]
    for interval in ("1wk", "1mo"):
        (sig / f"SPY_stable_v1_0_0_{interval}.pkl").write_bytes(b"x")

    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert r.leader_eligible is True, r.issue_codes
    assert r.ranking_allowed is True
    for blocking_code in (
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
        cpr.ISSUE_HEALTH_REPORT_BLOCKED,
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
        cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE,
        cpr.ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE,
    ):
        assert blocking_code not in r.issue_codes, (
            f"unexpected blocking issue code in happy-path "
            f"verdict: {blocking_code}"
        )


# ---------------------------------------------------------------------------
# 6. Missing confluence -> not leader-eligible
# ---------------------------------------------------------------------------


def test_missing_confluence_artifact_blocks_eligibility(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    # All upstream present, no Confluence.
    _write_artifact(
        dirs["artifact_root"], engine="impactsearch", ticker="SPY",
        last_date="2026-05-08",
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert r.leader_eligible is False
    assert (
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT in r.issue_codes
    )


# ---------------------------------------------------------------------------
# 7. Confluence agreement unavailable -> not leader-eligible
# ---------------------------------------------------------------------------


def test_confluence_missing_agreement_fields_blocks_eligibility(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    # Confluence present + current but daily row strips out the
    # active_count / available_count fields so agreement can't be
    # rendered.
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-05-08", timeframes=[],
        daily_extra={"active_count": None, "available_count": None},
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert r.leader_eligible is False
    assert (
        cpr.ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE
        in r.issue_codes
    )


# ---------------------------------------------------------------------------
# 8. No yfinance / engine imports
# ---------------------------------------------------------------------------


def test_readiness_module_has_no_live_engine_imports():
    tree = ast.parse(
        Path(cpr.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance", "onepass", "impactsearch", "stackbuilder",
        "trafficflow", "confluence", "cross_ticker_confluence",
        "spymaster", "primary_signal_engine",
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
        "forbidden import in confluence_pipeline_readiness: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# 9. No disk-write call in the readiness module
# ---------------------------------------------------------------------------


def test_readiness_module_has_no_disk_writes():
    src = Path(cpr.__file__).read_text(encoding="utf-8")
    forbidden_patterns = [
        r"\.write_text\(",
        r"\.write_bytes\(",
        r"pickle\.dump\(",
        r"json\.dump\(",
        r"_ra\.write_",
        r"research_artifacts\.write_",
        r"_rch\.write_",
        r"research_catalogue_health\.write_",
    ]
    for pat in forbidden_patterns:
        if re.search(pat, src):
            pytest.fail(
                f"readiness module contains a disk-write call "
                f"matching /{pat}/"
            )
    if re.search(r"open\([^)]*['\"]w", src):
        pytest.fail("readiness module opens a file in write mode")


# ---------------------------------------------------------------------------
# 10. Universe inspection
# ---------------------------------------------------------------------------


def test_inspect_universe_walks_cache_filenames(tmp_path: Path):
    dirs = _layout(tmp_path)
    for t in ("SPY", "AAPL", "MSFT"):
        _write_cache_filename(dirs["cache_dir"], t)
    rs = cpr.inspect_universe_pipeline(
        current_as_of_date="2026-05-08", **dirs,
    )
    tickers = sorted(r.ticker for r in rs)
    assert tickers == ["AAPL", "MSFT", "SPY"]
    # None of these have confluence artifacts -> none are eligible.
    assert all(r.leader_eligible is False for r in rs)


# ---------------------------------------------------------------------------
# 11. list_tickers_with_confluence_artifacts helper
# ---------------------------------------------------------------------------


def test_presence_only_stages_report_current_false_with_flag(
    tmp_path: Path,
):
    """Phase 6C-8 StageStatus contract: stages that cannot derive
    a last_date from filename / directory inspection must report
    ``current=False`` and set ``presence_only=True`` so callers
    can distinguish "no inspectable freshness signal" from "stage
    failed a freshness check"."""
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    sig = dirs["signal_library_dir"]
    for interval in ("1wk", "1mo"):
        (sig / f"SPY_stable_v1_0_0_{interval}.pkl").write_bytes(b"x")
    _write_health_report(
        dirs["artifact_root"], blocked_targets={},
    )

    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
        fast_path_when_no_confluence=False,
    )
    presence_only_stages = {
        cpr.STAGE_SIGNAL_ENGINE_CACHE,
        cpr.STAGE_MULTITIMEFRAME_LIBRARIES,
        cpr.STAGE_CATALOGUE_HEALTH,
    }
    for s in r.stages:
        if s.stage in presence_only_stages and s.present:
            assert s.presence_only is True, (
                f"expected presence_only=True on {s.stage}; "
                f"got {s}"
            )
            assert s.current is False, (
                f"presence-only stage {s.stage} reported "
                f"current=True; got {s}"
            )
            assert s.last_date is None


def test_list_confluence_tickers_returns_uppercase_set(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-05-08",
    )
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="^GSPC",
        last_date="2026-05-08",
    )
    out = cpr.list_tickers_with_confluence_artifacts(
        dirs["artifact_root"],
    )
    assert "SPY" in out
    assert "^GSPC" in out


# ---------------------------------------------------------------------------
# 12. Inspection does not write to the artifact tree
# ---------------------------------------------------------------------------


def test_inspect_ticker_pipeline_does_not_write_anything(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_filename(dirs["cache_dir"], "SPY")
    _write_artifact(
        dirs["artifact_root"], engine="confluence", ticker="SPY",
        last_date="2026-05-08", timeframes=["1d", "1wk"],
    )
    before = _snapshot_tree(tmp_path)
    cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    after = _snapshot_tree(tmp_path)
    assert before == after, (
        "readiness inspection mutated the on-disk tree"
    )


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(root))] = (
                st.st_size, int(st.st_mtime_ns),
            )
    return out
