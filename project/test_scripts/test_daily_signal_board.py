"""Phase 6C-7 tests for daily_signal_board.

Pin the public contract, not pixel choices:

  - discovery filters to available cache payloads only
  - coverage status priority order is enforced
  - ranking is confluence-agreement desc -> ticker asc
  - SPY default; first alphabetical otherwise; empty when no rows
  - row click updates both featured and evidence trail
  - seven evidence-trail stations render in the documented order
  - missing stations use the documented placeholder copy
  - BOARD_COPY owns visible strings, DESIGN_TOKENS owns colors
  - the module never imports live-engine / yfinance code
  - disclaimer string is exact
  - empty-cache boot renders all five sections
  - the module makes no disk-write calls
  - build_app() returns a Dash app with all five section IDs
"""
from __future__ import annotations

import ast
import json
import os
import pickle
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

# Make sure the bare-name imports inside daily_signal_board resolve
# the same way they do when the module is run as ``python
# daily_signal_board.py`` from ``project/``. This mirrors the path
# bootstrap used by the existing preview test suite.
_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import daily_signal_board as board  # noqa: E402
import primary_signal_engine as pse  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------


def _write_min_spymaster_cache(
    cache_dir: Path, ticker: str, *,
    last_date: str = "2026-05-04",
    final_signal: str = "Buy 3,2",
) -> Path:
    """Write the minimal Spymaster-cache PKL shape that
    ``primary_signal_engine.load_primary_signal_engine_payload``
    accepts. The shape uses ``preprocessed_data`` + ``active_pairs``
    aligned to the price index. ``final_signal`` lets callers vary
    the *current* (last-row) active pair so two ticker fixtures
    produce distinguishable payloads."""
    import pandas as pd

    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range(end=last_date, periods=10, freq="D")
    df = pd.DataFrame(
        {"Close": [100.0 + i for i in range(10)]},
        index=dates,
    )
    active_pairs = [
        "Buy 3,2", "Buy 3,2", "Buy 3,2", "Buy 3,2", "Buy 3,2",
        "Short 5,1", "Short 5,1", "Short 5,1", "Short 5,1",
        final_signal,
    ]
    payload = {
        "preprocessed_data": df,
        "active_pairs": active_pairs,
    }
    safe = ticker.replace("^", "_")
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_research_day_artifact(
    artifact_root: Path,
    *,
    engine: str,
    target: str,
    last_date: str,
    timeframes: Optional[list[str]] = None,
    daily_extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Create a saved ``*.research_day.json`` under
    ``output/research_artifacts/<engine>/<TARGET>/``. Uses
    ``research_artifacts.write_research_day_artifact`` so the on-disk
    schema stays in lockstep with the producer."""
    engine_dir = artifact_root / engine / target.replace("^", "_")
    engine_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "total_capture_pct": 42.5,
        "sharpe_ratio": 0.07,
        "trigger_days": 5,
    }
    daily = [
        {
            "date": last_date,
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 12.34,
            "is_trigger_day": True,
        },
    ]
    if daily_extra:
        daily[-1].update(daily_extra)
    artifact = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine=engine,
        target_ticker=target,
        signal_source="" if engine != "impactsearch" else "SPY",
        run_id="test",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-10T00:00:00+00:00",
        summary=summary,
        daily=daily,
        timeframes=list(timeframes or []),
    )
    out_path = engine_dir / f"{target.replace('^', '_')}.research_day.json"
    return ra.write_research_day_artifact(artifact, out_path)


def _empty_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    sig_lib_dir = tmp_path / "siglib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    sig_lib_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir, artifact_root, sig_lib_dir


@pytest.fixture(autouse=True)
def _reset_board_cache_each_test():
    board.reset_board_cache()
    yield
    board.reset_board_cache()


# ---------------------------------------------------------------------------
# 1. Discovery
# ---------------------------------------------------------------------------


def test_catalogue_discovery_returns_only_cached_tickers(tmp_path: Path):
    """Discovery enumerates ``*_precomputed_results.pkl`` filenames.

    Per the Phase 6C-7 perf amendment, the PKL is NOT opened during
    discovery, so a malformed PKL still produces a row (the user
    sees the cache-only fallback). Filename-pattern mismatches are
    still excluded silently.
    """
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    _write_min_spymaster_cache(cache_dir, "ACME")
    # Malformed file: filename matches the pattern, so it still
    # appears as a row. The cached signal falls back to "None"
    # because no research_day_v1 artifact exists for BAD.
    bad = cache_dir / "BAD_precomputed_results.pkl"
    bad.write_bytes(b"not a pickle")
    # Filename pattern mismatch: never enters the catalogue.
    (cache_dir / "ignore_me.txt").write_text("noop")

    rows = board.discover_board_catalogue(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
        use_cache=False,
    )
    tickers = {r.ticker for r in rows}
    assert "SPY" in tickers
    assert "ACME" in tickers
    assert "BAD" in tickers, (
        "filename-pattern match must produce a row even when the "
        "PKL is malformed; the PKL is not opened during discovery"
    )
    assert "ignore_me" not in tickers
    # Cache-only ticker with no artifacts -> None / 0 / Partial.
    for row in rows:
        assert row.signal in {"Buy", "Short", "None"}
        assert row.signal_value in {-1, 0, 1}
        assert row.coverage == board.COVERAGE_PARTIAL, (
            f"{row.ticker}: cache-only ticker should be Partial; "
            f"got {row.coverage}"
        )


# ---------------------------------------------------------------------------
# 2. Coverage status priority
# ---------------------------------------------------------------------------


def _make_ref(last_date: str, artifact: Any = None) -> Any:
    """Tiny stand-in for board._ArtifactRef. Coverage code only
    reads ``.last_date`` / ``.artifact`` / ``.path`` / ``.mtime`` so
    a SimpleNamespace works without coupling to the dataclass."""
    return SimpleNamespace(
        path=Path("/tmp/fake.json"),
        artifact=artifact,
        last_date=last_date,
        mtime=0.0,
    )


def test_coverage_status_full_partial_stale_under_review():
    """Phase 6C-7 perf amendment: stale is driven by artifact dates
    only (the cache PKL is never opened during scoreboard
    discovery, so its date_range.end can't feed staleness)."""
    fresh = "2026-05-09"
    stale = (datetime(2026, 5, 9, tzinfo=timezone.utc)
             - timedelta(days=400)).strftime("%Y-%m-%d")
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    fresh_ref = _make_ref(fresh, artifact=SimpleNamespace(
        timeframes=["1d", "1wk", "1mo"], daily=[
            {"active_count": 3, "available_count": 3},
        ],
    ))

    # 1. Under-review beats everything when health flags the ticker,
    #    even with full + fresh evidence.
    coverage = board.coverage_status_for_ticker(
        "SPY",
        has_engine_cache=True,
        impactsearch_ref=fresh_ref,
        stackbuilder_ref=fresh_ref,
        trafficflow_ref=fresh_ref,
        confluence_ref=fresh_ref,
        calendar_timeframes=["1wk", "1mo"],
        health_blocked=["SPY"],
        now=now,
    )
    assert coverage == board.COVERAGE_UNDER_REVIEW

    # 2. Stale beats Full when the newest artifact date is older
    #    than STALE_DAYS, regardless of artifact completeness.
    stale_ref = _make_ref(stale, artifact=SimpleNamespace(
        timeframes=["1d", "1wk", "1mo"], daily=[
            {"active_count": 2, "available_count": 3},
        ],
    ))
    coverage = board.coverage_status_for_ticker(
        "SPY",
        has_engine_cache=True,
        impactsearch_ref=stale_ref,
        stackbuilder_ref=stale_ref,
        trafficflow_ref=stale_ref,
        confluence_ref=stale_ref,
        calendar_timeframes=["1wk", "1mo"],
        health_blocked=[],
        now=now,
    )
    assert coverage == board.COVERAGE_STALE

    # 3. Full: fresh evidence in every engine + 2+ Calendar timeframes.
    coverage = board.coverage_status_for_ticker(
        "SPY",
        has_engine_cache=True,
        impactsearch_ref=fresh_ref,
        stackbuilder_ref=fresh_ref,
        trafficflow_ref=fresh_ref,
        confluence_ref=fresh_ref,
        calendar_timeframes=["1wk", "1mo"],
        health_blocked=[],
        now=now,
    )
    assert coverage == board.COVERAGE_FULL

    # 4. Partial: only the engine cache is present.
    coverage = board.coverage_status_for_ticker(
        "SPY",
        has_engine_cache=True,
        impactsearch_ref=None,
        stackbuilder_ref=None,
        trafficflow_ref=None,
        confluence_ref=None,
        calendar_timeframes=[],
        health_blocked=[],
        now=now,
    )
    assert coverage == board.COVERAGE_PARTIAL

    # 5. Cache-only with no artifacts is NOT stale even when the
    #    cache itself would be ancient on disk: staleness requires
    #    an artifact-date signal under the perf contract.
    assert board.coverage_status_for_ticker(
        "SPY",
        has_engine_cache=True,
        impactsearch_ref=None, stackbuilder_ref=None,
        trafficflow_ref=None, confluence_ref=None,
        calendar_timeframes=[],
        health_blocked=[],
        now=now,
    ) == board.COVERAGE_PARTIAL

    # 6. Priority order is documented + canonical. Phase 6C-8
    #    audit-tighten: the new ``Pipeline incomplete`` label slots
    #    between Stale and Full so a row blocked on a missing
    #    bridge / K-coverage never reads as a Full-coverage row.
    assert board.COVERAGE_PRIORITY == (
        board.COVERAGE_UNDER_REVIEW,
        board.COVERAGE_STALE,
        board.COVERAGE_PIPELINE_INCOMPLETE,
        board.COVERAGE_FULL,
        board.COVERAGE_PARTIAL,
    )


# ---------------------------------------------------------------------------
# 3. Ranking
# ---------------------------------------------------------------------------


def test_ranking_sorts_by_confluence_then_alphabetical():
    """Phase 6C-8: ranking still sorts by agreement DESC then
    ticker, but the rank BADGE is now gated on
    ``leader_eligible=True`` per the audit. The four rows in
    this fixture are marked leader_eligible to isolate the
    sort-order assertion from the eligibility gate (which is
    covered separately by
    ``test_only_leader_eligible_rows_receive_rank_badges``)."""
    rows = [
        board.BoardRow(
            ticker="BBB", signal="Buy", signal_value=1,
            agreement_active=3, agreement_total=5,
            coverage=board.COVERAGE_PARTIAL, as_of="2026-05-09",
            leader_eligible=True,
        ),
        board.BoardRow(
            ticker="AAA", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of="2026-05-09",
            leader_eligible=True,
        ),
        board.BoardRow(
            ticker="CCC", signal="Short", signal_value=-1,
            agreement_active=3, agreement_total=5,
            coverage=board.COVERAGE_PARTIAL, as_of="2026-05-09",
            leader_eligible=True,
        ),
        board.BoardRow(
            ticker="DDD", signal="Buy", signal_value=1,
            agreement_active=5, agreement_total=5,
            coverage=board.COVERAGE_FULL, as_of="2026-05-09",
            leader_eligible=True,
        ),
    ]
    ranked = board.rank_board_rows(rows)
    order = [r.ticker for r in ranked]
    # Active counts: DDD=5, BBB=3, CCC=3, AAA=None (-1).
    # Descending: 5, then 3-tie alphabet (BBB < CCC), then None last.
    assert order == ["DDD", "BBB", "CCC", "AAA"]
    # Top 3 carry rank labels; AAA does not.
    ranks = {r.ticker: r.rank for r in ranked}
    assert ranks == {"DDD": 1, "BBB": 2, "CCC": 3, "AAA": None}


def test_ranking_skips_cache_only_rows_for_top_3_badges():
    """Phase 6C-7 audit fix carried forward: only rows with
    ``agreement_active is not None`` AND ``leader_eligible=True``
    are eligible for a rank=1|2|3 badge."""
    rows = [
        board.BoardRow(
            ticker="SPY", signal="Buy", signal_value=1,
            agreement_active=5, agreement_total=5,
            coverage=board.COVERAGE_FULL, as_of="2026-05-09",
            leader_eligible=True,
        ),
        board.BoardRow(
            ticker="GSPC_VARIANT", signal="None", signal_value=0,
            agreement_active=1, agreement_total=1,
            coverage=board.COVERAGE_PARTIAL, as_of="2026-05-09",
            leader_eligible=True,
        ),
        board.BoardRow(
            ticker="AAA", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
        ),
        board.BoardRow(
            ticker="BBB", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
        ),
        board.BoardRow(
            ticker="000157.KS", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
        ),
    ]
    ranked = board.rank_board_rows(rows)
    ranks = {r.ticker: r.rank for r in ranked}
    assert ranks == {
        "SPY": 1,
        "GSPC_VARIANT": 2,
        "AAA": None,
        "BBB": None,
        "000157.KS": None,
    }, f"unexpected rank assignment: {ranks}"
    assigned = [r for r in ranked if r.rank is not None]
    assert len(assigned) == 2


def test_only_leader_eligible_rows_receive_rank_badges():
    """Phase 6C-8 contract: a row may have a strong confluence
    agreement count but still be ineligible (stale, partial,
    under review, pipeline-incomplete). It MUST NOT receive a
    podium badge in that case. This test fixes the audit finding
    where SPY would otherwise rank #1 despite a stale Confluence
    artifact."""
    rows = [
        # Ineligible row with the highest agreement (stale).
        board.BoardRow(
            ticker="SPY", signal="Buy", signal_value=1,
            agreement_active=5, agreement_total=5,
            coverage=board.COVERAGE_STALE, as_of="2026-01-21",
            leader_eligible=False,
            ranking_blocked_reason=(
                "stale_confluence_day_artifact"
            ),
        ),
        # Eligible row with a lower agreement.
        board.BoardRow(
            ticker="ACME", signal="Buy", signal_value=1,
            agreement_active=2, agreement_total=5,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True,
        ),
        # Cache-only row.
        board.BoardRow(
            ticker="OTHER", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
            leader_eligible=False,
            ranking_blocked_reason=(
                "missing_confluence_day_artifact"
            ),
        ),
    ]
    ranked = board.rank_board_rows(rows)
    ranks = {r.ticker: r.rank for r in ranked}
    # Only ACME, the leader-eligible row, gets a badge - even
    # though SPY has a higher raw agreement.
    assert ranks == {"SPY": None, "ACME": 1, "OTHER": None}, ranks
    # Sort order: ACME first (eligible), SPY second (ineligible
    # with agreement), OTHER last (no agreement).
    assert [r.ticker for r in ranked] == ["ACME", "SPY", "OTHER"]


def test_no_podium_when_all_rows_are_stale_or_partial():
    """A board with zero eligible rows must not award any rank
    badges, even though every row may have a sortable agreement
    count."""
    rows = [
        board.BoardRow(
            ticker=t, signal="None", signal_value=0,
            agreement_active=3, agreement_total=5,
            coverage=board.COVERAGE_STALE, as_of="2026-01-01",
            leader_eligible=False,
            ranking_blocked_reason=(
                "stale_confluence_day_artifact"
            ),
        )
        for t in ("AAA", "BBB", "CCC", "DDD")
    ]
    ranked = board.rank_board_rows(rows)
    assert all(r.rank is None for r in ranked), [
        (r.ticker, r.rank) for r in ranked
    ]


def test_scoreboard_renders_empty_data_rank_for_cache_only_rows():
    """A cache-only row in the rendered scoreboard tr must carry
    ``data-rank=""`` so the visual layer never paints a podium
    badge onto it."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="Buy", signal_value=1,
            agreement_active=5, agreement_total=5,
            coverage=board.COVERAGE_FULL, as_of="2026-05-09",
            leader_eligible=True,
            rank=1,
        ),
        board.BoardRow(
            ticker="AAA", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
            rank=None,
        ),
    ]
    table = board.render_scoreboard(rows, selected_ticker="SPY")
    body_rows = _tbody_tr_props(table)
    by_ticker = {props.get("data-ticker"): props for props in body_rows}
    assert by_ticker["SPY"].get("data-rank") == "1"
    assert by_ticker["AAA"].get("data-rank") == "", (
        "cache-only row must render data-rank=\"\"; got "
        + repr(by_ticker["AAA"].get("data-rank"))
    )
    # New Phase 6C-8 data attrs.
    assert (
        by_ticker["SPY"].get("data-leader-eligible") == "true"
    )
    assert (
        by_ticker["AAA"].get("data-leader-eligible") == "false"
    )
    # The blocked-reason attribute is present on both rows
    # (empty string on the eligible one, populated on the
    # ineligible one when the BoardRow carries a reason).
    assert "data-ranking-blocked-reason" in by_ticker["SPY"]
    assert "data-ranking-blocked-reason" in by_ticker["AAA"]


def test_scoreboard_data_ranking_method_reflects_current_leader_gate(
    tmp_path: Path,
):
    """``section-scoreboard``'s ``data-ranking-method`` attribute
    advertises the Phase 6C-8 gate so audit tooling can detect
    that the public board ranks only Confluence-current leaders."""
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    method = _find_data_ranking_method(app.layout)
    assert (
        method
        == "current_confluence_leaders_only_then_"
           "agreement_desc_then_ticker_asc"
    ), f"unexpected data-ranking-method: {method!r}"


def test_board_renders_no_current_leaders_banner_when_zero_eligible(
    tmp_path: Path,
):
    """When the cache holds saved research but zero tickers pass
    the leader gate, the board must render the BOARD_COPY
    ``no_current_leaders`` banner so the public surface stays
    honest."""
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    _write_min_spymaster_cache(cache_dir, "ACME")
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    text = _component_text(app.layout)
    assert board.BOARD_COPY["no_current_leaders"] in text


def test_coverage_label_reconciles_with_readiness_blocked_reason():
    """Phase 6C-8 audit-tighten: the visible Coverage column must
    not contradict the readiness verdict. Stale-confluence forces
    Stale; the bridge / K-coverage codes force ``Pipeline
    incomplete``; the health-block code forces ``Under review``."""
    overrides = {
        "stale_confluence_day_artifact": board.COVERAGE_STALE,
        "missing_multitimeframe_trafficflow_bridge": (
            board.COVERAGE_PIPELINE_INCOMPLETE
        ),
        "insufficient_trafficflow_k_coverage": (
            board.COVERAGE_PIPELINE_INCOMPLETE
        ),
        "health_report_blocked": board.COVERAGE_UNDER_REVIEW,
    }
    for code, expected in overrides.items():
        assert board._reconcile_coverage_with_readiness(
            board.COVERAGE_FULL, code,
        ) == expected, (
            f"reconciled coverage for {code} should be "
            f"{expected!r}; got "
            f"{board._reconcile_coverage_with_readiness(board.COVERAGE_FULL, code)!r}"
        )
    # No override -> original coverage is preserved.
    assert board._reconcile_coverage_with_readiness(
        board.COVERAGE_FULL, "",
    ) == board.COVERAGE_FULL


def test_board_row_with_missing_bridge_shows_pipeline_incomplete(
    tmp_path: Path,
):
    """End-to-end: a ticker with a present + current confluence
    artifact whose multi-timeframe TrafficFlow bridge is missing
    renders ``Coverage = Pipeline incomplete`` AND never receives
    a rank badge."""
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    target_dir = artifact_root / "confluence" / "SPY"
    target_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="confluence",
        target_ticker="SPY",
        signal_source="",
        run_id="current",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 12.0,
            "sharpe_ratio": 0.2,
            "trigger_days": 5,
        },
        daily=[{
            "date": "2099-12-31",  # always-future -> always current
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "confluence_tier": "strong_buy",
            "confluence_signal": "Buy",
            "timeframe_signals": {},
            "alignment_pct": 1.0,
            "buy_count": 5,
            "short_count": 0,
            "none_count": 0,
            "active_count": 5,
            "available_count": 5,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 12.0,
            "is_trigger_day": True,
        }],
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    ra.write_research_day_artifact(
        art, target_dir / "SPY.research_day.json",
    )
    # NO multi-timeframe TrafficFlow artifact -> bridge missing.

    rows = board.discover_board_catalogue(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
        use_cache=False,
    )
    spy = next(r for r in rows if r.ticker == "SPY")
    # Confluence is current; agreement is 5/5; cache exists. The
    # pre-readiness coverage_status_for_ticker would return Full.
    # But the readiness layer blocks ranking on the missing
    # bridge, and the board reconciles the visible coverage
    # accordingly.
    assert spy.leader_eligible is False
    assert (
        spy.ranking_blocked_reason
        == "missing_multitimeframe_trafficflow_bridge"
    )
    assert spy.coverage == board.COVERAGE_PIPELINE_INCOMPLETE, (
        f"expected Pipeline incomplete; got {spy.coverage!r}"
    )
    ranked = board.rank_board_rows(rows)
    spy_ranked = next(r for r in ranked if r.ticker == "SPY")
    assert spy_ranked.rank is None


def test_spy_like_fixture_with_stale_confluence_is_not_rankable(
    tmp_path: Path,
):
    """End-to-end product gate: an SPY-like ticker with a full
    5/5 confluence verdict but a stale Confluence artifact date
    must NOT be rankable on the public board. Reproduces the
    audit finding."""
    pytest.importorskip("dash")
    pytest.importorskip("pandas")
    import pandas as pd
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    # Stale confluence artifact (last_date 4 months ago).
    target_dir = artifact_root / "confluence" / "SPY"
    target_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="confluence",
        target_ticker="SPY",
        signal_source="",
        run_id="stale",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-01-21T00:00:00+00:00",
        summary={
            "total_capture_pct": 50.0,
            "sharpe_ratio": 0.1,
            "trigger_days": 5,
        },
        daily=[{
            "date": "2026-01-21",
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "confluence_tier": "strong_buy",
            "confluence_signal": "Buy",
            "timeframe_signals": {},
            "alignment_pct": 1.0,
            "buy_count": 5,
            "short_count": 0,
            "none_count": 0,
            "active_count": 5,
            "available_count": 5,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 50.0,
            "is_trigger_day": True,
        }],
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    ra.write_research_day_artifact(
        art, target_dir / "SPY.research_day.json",
    )
    rows = board.discover_board_catalogue(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
        use_cache=False,
    )
    spy = next(r for r in rows if r.ticker == "SPY")
    # Agreement is still discoverable from the saved artifact ...
    assert spy.agreement_active == 5
    assert spy.agreement_total == 5
    # ... but the leader gate refuses to rank SPY because
    # Confluence is stale relative to the resolved current-as-of
    # date.
    assert spy.leader_eligible is False
    assert (
        spy.ranking_blocked_reason
        == "stale_confluence_day_artifact"
    )
    ranked = board.rank_board_rows(rows)
    spy_ranked = next(r for r in ranked if r.ticker == "SPY")
    assert spy_ranked.rank is None, (
        f"SPY received rank {spy_ranked.rank} despite stale "
        f"confluence"
    )


# ---------------------------------------------------------------------------
# 4. Default selected ticker
# ---------------------------------------------------------------------------


def test_default_selected_ticker_is_spy():
    def _row(t):
        return board.BoardRow(
            ticker=t, signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
        )
    assert board.default_selected_ticker(
        [_row("SPY"), _row("AAA")],
    ) == "SPY"
    assert board.default_selected_ticker(
        [_row("BBB"), _row("AAA")],
    ) == "AAA"
    assert board.default_selected_ticker([]) == ""


# ---------------------------------------------------------------------------
# 5. Row click updates featured + evidence trail
# ---------------------------------------------------------------------------


def test_clicking_row_updates_featured_and_evidence_trail(
    monkeypatch, tmp_path: Path,
):
    """Multi-output callback variant of the click-updates contract.

    Phase 6C-7 audit fix: the two render callbacks were collapsed
    into one multi-output callback so the selected ticker hydrates
    exactly once per click instead of once per panel. This test
    invokes the combined callback and asserts both outputs reflect
    the new selection.
    """
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(
        cache_dir, "SPY", final_signal="Buy 3,2",
    )
    _write_min_spymaster_cache(
        cache_dir, "ACME", final_signal="Short 5,1",
    )

    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    sel_key = "selected-ticker-store.data"
    assert sel_key in app.callback_map, (
        f"expected {sel_key} in app.callback_map; got "
        f"{list(app.callback_map)[:8]}"
    )

    # The Featured + Evidence outputs are now wired by a single
    # multi-output callback. Find it by substring match on the
    # synthetic Dash key (e.g.
    # "..section-featured-body.children...section-evidence-trail-body.children..").
    combined_key = next(
        (k for k in app.callback_map
         if "section-featured-body.children" in k
         and "section-evidence-trail-body.children" in k),
        None,
    )
    assert combined_key is not None, (
        "expected a single multi-output callback wiring both "
        "section-featured-body and section-evidence-trail-body; "
        f"got keys: {list(app.callback_map)[:8]}"
    )
    combined_cb = app.callback_map[combined_key]["callback"]
    combined_inner = getattr(combined_cb, "__wrapped__", combined_cb)

    feat_acme, evid_acme = combined_inner("ACME")
    feat_spy, evid_spy = combined_inner("SPY")

    # The featured / evidence renders depend on the selected ticker -
    # the rendered tree must mention the new ticker name when the
    # selection changes.
    assert _component_contains_id(feat_acme, "featured-ticker-name")
    assert _component_contains_id(feat_spy, "featured-ticker-name")
    feat_acme_text = _component_text(feat_acme)
    feat_spy_text = _component_text(feat_spy)
    assert "ACME" in feat_acme_text
    assert "SPY" in feat_spy_text
    assert feat_acme_text != feat_spy_text

    evid_text_acme = _component_text(evid_acme)
    evid_text_spy = _component_text(evid_spy)
    # Both renders must include all seven station IDs.
    for sid in board.STATION_IDS:
        assert _component_contains_id(evid_acme, sid)
        assert _component_contains_id(evid_spy, sid)
    # And the rendered text must change when the selection changes
    # (the seed-field summary embeds the ticker payload).
    assert evid_text_acme != evid_text_spy


# ---------------------------------------------------------------------------
# 6. Seven stations in fixed order
# ---------------------------------------------------------------------------


def test_evidence_trail_renders_seven_stations_in_fixed_order(tmp_path: Path):
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    payload = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=cache_dir,
    )
    component = board.render_evidence_trail(
        "SPY",
        payload=payload,
        impactsearch_ref=None,
        stackbuilder_ref=None,
        trafficflow_ref=None,
        confluence_ref=None,
        calendar_timeframes=[],
        health_report=None,
    )
    found = _ordered_station_ids(component)
    assert found == list(board.STATION_IDS), (
        f"stations rendered in {found}, expected {board.STATION_IDS}"
    )


# ---------------------------------------------------------------------------
# 7. Missing station placeholder
# ---------------------------------------------------------------------------


def test_missing_station_renders_placeholder_text(tmp_path: Path):
    pytest.importorskip("dash")
    component = board.render_evidence_trail(
        "ZZZ",
        payload=None,
        impactsearch_ref=None,
        stackbuilder_ref=None,
        trafficflow_ref=None,
        confluence_ref=None,
        calendar_timeframes=[],
        health_report=None,
    )
    text = _component_text(component)
    assert "Not yet built for this ticker." in text


# ---------------------------------------------------------------------------
# 8. BOARD_COPY owns visible copy
# ---------------------------------------------------------------------------


def test_board_copy_dict_owns_visible_copy():
    """Pins every visible string the public board renders. Phase
    6C-7 audit extension: the Plotly chart's trace names and axis
    titles are now included so a future tweak cannot drift outside
    BOARD_COPY."""
    expected_visible = {
        # Section + scoreboard copy
        "No saved tickers yet.",
        "Not yet built for this ticker.",
        # Phase 6G-4: softened disclaimer cadence.
        "Historical research output. Not investment advice; "
        "saved research, not a live signal feed.",
        (
            "PRJCT9 is a pattern-discovery engine. It studies saved "
            "historical signal behavior, ranks current signal alignment, "
            "and exposes coverage gaps instead of hiding them."
        ),
        "Not investment advice.",
        "Not a live trading signal feed.",
        "Not a guarantee of future performance.",
        "Saved research only.",
        "Town Hall Scoreboard",
        "Featured High Score",
        "Evidence Trail",
        "What PRJCT9 Is",
        "What It Is Not",
        # Phase 6G-1: confluence_status_fmt now reads
        # "alignment checks active" so the count's units are
        # honest (60 = K-builds x timeframes, not 60
        # distinct timeframes).
        "{active} of {total} alignment checks active",
        "Confluence data unavailable",
        # Phase 6G-1 additions:
        "Today's Board Status",
        "Saved Research Archive",
        # Phase 6G-4: chart trace labels read as "research"
        # rather than "engine cockpit"; the close-price line
        # no longer interpolates the ticker (it's already in
        # the Featured panel scope).
        "Saved cumulative capture (research)",
        "Close price",
        "Date",
        "Cumulative Capture (%)",
        "Close Price",
    }
    flat = _flatten_board_copy_values()
    missing = expected_visible - flat
    assert not missing, (
        "expected visible strings missing from BOARD_COPY: " + repr(missing)
    )


def test_chart_figure_strings_come_from_board_copy(tmp_path: Path):
    """Build a chart figure and confirm every visible string on it
    (trace names + axis titles) comes from ``BOARD_COPY`` rather
    than a hardcoded literal."""
    pytest.importorskip("plotly")
    payload = {
        "schema": "primary_signal_engine_payload_v1",
        "ticker": "SPY",
        "available": True,
        "chart_rows": [
            {"date": "2024-01-02", "close": 100.0,
             "cumulative_capture_pct": 0.0},
            {"date": "2024-01-03", "close": 120.0,
             "cumulative_capture_pct": 5.0},
            {"date": "2024-01-04", "close": 110.0,
             "cumulative_capture_pct": 3.0},
        ],
    }
    fig = board._build_signal_engine_figure("SPY", payload)
    assert fig is not None
    # Trace names
    trace_names = [t.name for t in fig.data]
    assert (
        board.BOARD_COPY["chart_trace_engine_capture"] in trace_names
    )
    assert (
        board.BOARD_COPY["chart_trace_close_price_fmt"].format(
            ticker="SPY",
        ) in trace_names
    )
    # Axis titles
    assert (
        fig.layout.xaxis.title.text
        == board.BOARD_COPY["chart_axis_date"]
    )
    assert (
        fig.layout.yaxis.title.text
        == board.BOARD_COPY["chart_axis_cumulative_capture"]
    )
    assert (
        fig.layout.yaxis2.title.text
        == board.BOARD_COPY["chart_axis_close_price"]
    )


# ---------------------------------------------------------------------------
# 9. DESIGN_TOKENS owns colors
# ---------------------------------------------------------------------------


_HEX_OR_RGB_LITERAL = re.compile(
    r"""(?xi)
    (?:"|')                         # opening quote
    (
        \#[0-9a-f]{3,8}              # hex literal
        |
        rgba?\([^)]*\)               # rgb or rgba literal
    )
    (?:"|')                         # closing quote
    """,
)


def test_design_tokens_dict_owns_all_colors():
    src_path = Path(board.__file__)
    raw = src_path.read_text(encoding="utf-8").splitlines()

    # Find the DESIGN_TOKENS dict line range so its literals are
    # allowed; any color literal outside that range is a violation.
    start_idx = None
    end_idx = None
    depth = 0
    for i, line in enumerate(raw):
        if start_idx is None and line.startswith("DESIGN_TOKENS"):
            start_idx = i
            depth = line.count("{") - line.count("}")
            if depth == 0:
                end_idx = i
                break
            continue
        if start_idx is not None and end_idx is None:
            depth += line.count("{") - line.count("}")
            if depth == 0:
                end_idx = i
                break
    assert start_idx is not None and end_idx is not None, (
        "could not locate DESIGN_TOKENS block in daily_signal_board.py"
    )

    violations: list[tuple[int, str]] = []
    for i, line in enumerate(raw):
        if start_idx <= i <= end_idx:
            continue
        if _HEX_OR_RGB_LITERAL.search(line):
            violations.append((i + 1, line.rstrip()))
    assert not violations, (
        "color literals outside DESIGN_TOKENS: "
        + repr(violations[:8])
    )


# ---------------------------------------------------------------------------
# 10. No live engine / yfinance imports
# ---------------------------------------------------------------------------


def test_no_live_engine_or_yfinance_imports():
    src_path = Path(board.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    forbidden = {
        "yfinance", "onepass", "impactsearch", "stackbuilder",
        "trafficflow", "confluence", "cross_ticker_confluence",
        "spymaster",
    }
    allowed = {
        "primary_signal_engine",
        "research_artifacts",
        "research_catalogue_health",
    }
    found_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_modules.append(node.module)
    bad = [m for m in found_modules if m.split(".")[0] in forbidden]
    assert not bad, (
        "forbidden live-engine import in daily_signal_board: "
        + repr(bad)
    )
    # Sanity: at least one of the allowed helpers is referenced.
    assert any(
        m.split(".")[0] in allowed for m in found_modules
    ), (
        "daily_signal_board does not import any of the documented "
        "read-only helpers: " + repr(allowed)
    )


# ---------------------------------------------------------------------------
# 11. Disclaimer exact
# ---------------------------------------------------------------------------


def test_disclaimer_string_is_present_and_exact():
    # Phase 6G-4: softened to a notice-board cadence without
    # losing the "not investment advice / saved research /
    # not a live signal feed" meaning. ASCII semicolon used
    # for portability (no em dash).
    expected = (
        "Historical research output. Not investment advice; "
        "saved research, not a live signal feed."
    )
    assert board.BOARD_COPY["featured_disclaimer"] == expected


# ---------------------------------------------------------------------------
# 12. Empty cache renders all sections
# ---------------------------------------------------------------------------


def test_empty_cache_renders_all_sections_without_exception(tmp_path: Path):
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    layout = app.layout
    for sid in (
        "section-scoreboard",
        "section-featured",
        "section-evidence-trail",
        "section-what-prjct9-is",
        "section-what-it-is-not",
    ):
        assert _component_contains_id(layout, sid), (
            f"section {sid!r} missing from layout"
        )
    text = _component_text(layout)
    assert board.BOARD_COPY["empty_scoreboard"] in text


# ---------------------------------------------------------------------------
# 13. No disk-write calls
# ---------------------------------------------------------------------------


def test_board_module_has_no_disk_write_calls():
    src = Path(board.__file__).read_text(encoding="utf-8")
    forbidden_patterns = [
        r"\.write_text\(",
        r"\.write_bytes\(",
        r"pickle\.dump\(",
        # json.dump( with no s -> writes to file. ``json.dumps`` is fine.
        r"json\.dump\(",
        r"_rch\.write_",
        r"_ra\.write_",
        r"research_catalogue_health\.write_",
        r"research_artifacts\.write_",
    ]
    for pat in forbidden_patterns:
        if re.search(pat, src):
            pytest.fail(
                f"daily_signal_board.py contains disk-write call "
                f"matching /{pat}/"
            )
    # Sanity: also block obvious ``open(path, "w")`` style writes.
    if re.search(r"open\([^)]*['\"]w", src):
        pytest.fail(
            "daily_signal_board.py opens a file in write mode"
        )


# ---------------------------------------------------------------------------
# 14. build_app() returns a Dash app with all five section IDs
# ---------------------------------------------------------------------------


def test_app_boots_with_layout(tmp_path: Path):
    pytest.importorskip("dash")
    import dash
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    assert isinstance(app, dash.Dash)
    for sid in (
        "section-scoreboard",
        "section-featured",
        "section-evidence-trail",
        "section-what-prjct9-is",
        "section-what-it-is-not",
    ):
        assert _component_contains_id(app.layout, sid)


# ---------------------------------------------------------------------------
# 15. Cold-boot does not hydrate every cached ticker (Phase 6C-7 audit)
# ---------------------------------------------------------------------------


def test_cold_boot_does_not_call_payload_loader_per_ticker(
    monkeypatch, tmp_path: Path,
):
    """Phase 6C-7 audit: ``build_app()`` must not open every cache
    PKL on cold boot. The scoreboard is built from filenames +
    saved artifacts; the only payload load allowed at cold boot is
    the single hydration for the default selected ticker (shared
    by Featured + Evidence via the per-state payload cache)."""
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    # 12 fake cache filenames; one is SPY so the default selector
    # finds a meaningful seed without alphabetical drift.
    fake_tickers = [
        "SPY", "AAA", "BBB", "CCC", "DDD", "EEE",
        "FFF", "GGG", "HHH", "III", "JJJ", "KKK",
    ]
    _write_min_spymaster_cache(cache_dir, "SPY")
    for t in fake_tickers:
        if t == "SPY":
            continue
        # The other 11 are just touched empty - discovery must not
        # try to open them.
        (cache_dir / f"{t}_precomputed_results.pkl").write_bytes(b"")

    real_loader = pse.load_primary_signal_engine_payload
    call_log: list[str] = []

    def _spy_loader(ticker, *args, **kwargs):
        call_log.append(str(ticker))
        return real_loader(ticker, *args, **kwargs)

    monkeypatch.setattr(
        pse, "load_primary_signal_engine_payload", _spy_loader,
    )
    # daily_signal_board.py imports pse as `_pse`; rebinding the
    # attribute on the original module is enough because the import
    # is `import primary_signal_engine as _pse` (module reference).
    monkeypatch.setattr(
        board._pse, "load_primary_signal_engine_payload", _spy_loader,
        raising=False,
    )

    board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    # The contract: at most ONE hydration call (the default selected
    # ticker, shared by Featured + Evidence via the per-state cache).
    # The other 11 tickers must never be opened.
    assert len(call_log) <= 1, (
        f"build_app() hydrated {len(call_log)} tickers on cold "
        f"boot; expected <=1: {call_log[:8]}"
    )
    # And whatever was hydrated must be the default selected ticker
    # (SPY here), never a random other entry.
    if call_log:
        assert call_log[0] == "SPY"
    # The other cache filenames must not have been opened.
    for t in fake_tickers:
        if t == "SPY":
            continue
        assert t not in call_log, (
            f"build_app() opened non-selected ticker {t!r} "
            f"during cold boot"
        )


# ---------------------------------------------------------------------------
# 16. Perf: 200 fake cached filenames -> fast discovery, no hydration
# ---------------------------------------------------------------------------


def test_discovery_handles_200_fixtures_without_hydrating(
    monkeypatch, tmp_path: Path,
):
    """Phase 6C-7 audit: 200 fake cache filenames must not produce
    200 PKL hydrations during discovery, and discovery itself must
    finish well under the documented 2-second budget on Peter's
    hardware. We measure wall time and the payload-loader call
    count."""
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    for i in range(200):
        (cache_dir / f"FAKE{i:03d}_precomputed_results.pkl").write_bytes(b"")

    call_log: list[str] = []

    def _spy_loader(ticker, *args, **kwargs):
        call_log.append(str(ticker))
        return {"available": False, "ticker": ticker, "reason": "no_data"}

    monkeypatch.setattr(
        pse, "load_primary_signal_engine_payload", _spy_loader,
    )
    monkeypatch.setattr(
        board._pse, "load_primary_signal_engine_payload", _spy_loader,
        raising=False,
    )

    t0 = time.perf_counter()
    rows = board.discover_board_catalogue(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
        use_cache=False,
    )
    elapsed = time.perf_counter() - t0

    assert len(rows) == 200, (
        f"expected 200 BoardRows for 200 fixtures; got {len(rows)}"
    )
    assert not call_log, (
        f"discover_board_catalogue called the payload loader "
        f"{len(call_log)} times; expected 0: {call_log[:8]}"
    )
    # 2-second budget per the Phase 6C-7 spec on Peter's hardware.
    # Empty fake files + no artifacts is the fastest case; the real
    # bound is set generously here so the test stays stable across
    # CI hardware while still catching catastrophic regressions.
    assert elapsed < 5.0, (
        f"discover_board_catalogue took {elapsed:.2f}s on 200 fake "
        f"filenames; expected < 5s (2s target on Peter's hardware)"
    )


# ---------------------------------------------------------------------------
# Phase 6F-7: mobile launch polish
# ---------------------------------------------------------------------------


def _to_props(node: Any) -> dict[str, Any]:
    try:
        return node.to_plotly_json().get("props", {}) or {}
    except Exception:
        return {}


def test_scoreboard_returns_horizontal_overflow_wrapper():
    """Phase 6F-7: ``render_scoreboard`` must return a Div
    keyed ``scoreboard-table-wrapper`` whose CSS allows
    horizontal scroll INSIDE the wrapper (``overflowX: auto``).
    Mobile viewports at 390x844 push the table past the page
    width; this wrapper absorbs that overflow so the page
    never grows a horizontal scrollbar."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows, selected_ticker="SPY")
    # Wrapper layer is now a Div with the stable contract id.
    assert type(wrapper).__name__ == "Div"
    props = _to_props(wrapper)
    assert props.get("id") == "scoreboard-table-wrapper"
    # The stable test-facing data attribute pins the
    # behavior contract.
    assert props.get("data-mobile-overflow") == "contained"
    style = props.get("style") or {}
    assert style.get("overflowX") == "auto", (
        "scoreboard-table-wrapper must enable horizontal "
        "overflow; got style="
        + repr(style)
    )
    assert style.get("width") == "100%"


def test_scoreboard_table_id_preserved_inside_wrapper():
    """The wrapper Div must contain the existing
    ``scoreboard-table`` Table — the existing id is part of
    the stable DOM contract."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows, selected_ticker="SPY")
    inner = _to_props(wrapper).get("children")
    assert inner is not None
    assert type(inner).__name__ == "Table"
    assert _to_props(inner).get("id") == "scoreboard-table"


def test_scoreboard_as_of_header_is_nowrap():
    """The ``AS OF`` header cell must carry
    ``whiteSpace: nowrap`` so the column label never breaks
    onto two lines on narrow viewports."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows, selected_ticker="SPY")
    table = _to_props(wrapper).get("children")
    # Find the Thead -> Tr -> last Th
    headers = _to_props(table).get("children")
    thead = headers[0]
    thead_tr = _to_props(thead).get("children")
    th_list = _to_props(thead_tr).get("children")
    as_of_th = th_list[-1]
    as_of_style = _to_props(as_of_th).get("style") or {}
    assert as_of_style.get("whiteSpace") == "nowrap", (
        "AS OF header must be nowrap; got "
        + repr(as_of_style)
    )


def test_scoreboard_as_of_cell_is_nowrap():
    """The ``AS OF`` data cell on every body row must carry
    ``whiteSpace: nowrap`` so ISO dates ("2026-05-08") never
    break to fragments like ``202`` / ``05-``."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
        board.BoardRow(
            ticker="AAA", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
            rank=None,
        ),
    ]
    wrapper = board.render_scoreboard(rows, selected_ticker="SPY")
    table = _to_props(wrapper).get("children")
    children = _to_props(table).get("children")
    tbody = children[1]
    body_tr_list = _to_props(tbody).get("children")
    assert len(body_tr_list) == 2
    for tr in body_tr_list:
        td_list = _to_props(tr).get("children")
        # AS OF is the last Td in each row.
        as_of_td = td_list[-1]
        as_of_style = _to_props(as_of_td).get("style") or {}
        assert as_of_style.get("whiteSpace") == "nowrap", (
            "AS OF cell must be nowrap; got "
            + repr(as_of_style)
        )


def test_spy_row_data_attributes_preserved_after_polish():
    """Phase 6F-7 must not regress the public-board data
    contract: SPY's row attributes (data-ticker,
    data-leader-eligible, data-rank, data-ranking-blocked-reason,
    data-coverage, data-signal) must round-trip through the
    new wrapper unchanged."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows, selected_ticker="SPY")
    body_rows = _tbody_tr_props(wrapper)
    assert len(body_rows) == 1
    p = body_rows[0]
    assert p.get("data-ticker") == "SPY"
    assert p.get("data-leader-eligible") == "true"
    assert p.get("data-rank") == "1"
    assert p.get("data-ranking-blocked-reason") == ""
    assert p.get("data-coverage") == board.COVERAGE_FULL
    assert p.get("data-signal") == "None"


def test_no_current_leaders_banner_hidden_when_spy_is_eligible(
    tmp_path: Path,
):
    """When the live state contains at least one leader-
    eligible row, the ``scoreboard-no-current-leaders``
    banner must remain hidden. Phase 6F-7 must not touch
    that semantic."""
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    # Inject a leader-eligible BoardRow by monkeypatching
    # the discovery helper so we don't need a full pipeline.
    real_discover = board.discover_board_catalogue

    def _fake_discover(**kw):
        original = real_discover(**kw)
        leader = board.BoardRow(
            ticker="SPY", signal="Buy", signal_value=1,
            agreement_active=5, agreement_total=5,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        )
        return tuple([leader] + [
            r for r in original if r.ticker != "SPY"
        ])

    import unittest.mock as mock
    with mock.patch.object(
        board, "discover_board_catalogue", _fake_discover,
    ):
        app = board.build_app(
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            sig_lib_dir=sig_lib_dir,
        )

    def _find(node, target_id):
        if node is None or isinstance(node, str):
            return None
        if isinstance(node, (list, tuple)):
            for c in node:
                r = _find(c, target_id)
                if r is not None:
                    return r
            return None
        if getattr(node, "id", None) == target_id:
            return node
        return _find(getattr(node, "children", None), target_id)

    banner = _find(app.layout, "scoreboard-no-current-leaders")
    assert banner is not None
    props = _to_props(banner)
    assert props.get("data-leader-count") == "1"
    assert (props.get("style") or {}).get("display") == "none"


# ---------------------------------------------------------------------------
# Phase 6G-1: public meaning + information hierarchy
# ---------------------------------------------------------------------------


def test_scoreboard_signal_cell_renders_public_friendly_label_for_none():
    """The visible Consensus cell renders "No consensus" when
    the underlying signal is "None"; the data-signal attribute
    on the row is unchanged ("None") so audit / automation
    still sees the canonical value."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows, selected_ticker="SPY")
    text = _component_text(wrapper)
    # Public-friendly visible text.
    assert "No consensus" in text, (
        "scoreboard Consensus cell must show 'No consensus' "
        "for signal=None; got text=" + repr(text[:200])
    )
    # Raw "None" must NOT leak into the visible cell text.
    # (the data attribute below carries it instead).
    body_rows = _tbody_tr_props(wrapper)
    assert len(body_rows) == 1
    assert body_rows[0].get("data-signal") == "None"
    assert body_rows[0].get("data-signal-value") == "0"


def test_scoreboard_consensus_cells_show_buy_and_short_unchanged():
    """Buy / Short signals continue to render as 'Buy' / 'Short'
    in the visible cell (public-friendly mapping only changes
    the None case)."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="AAA", signal="Buy", signal_value=1,
            agreement_active=5, agreement_total=5,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
        board.BoardRow(
            ticker="BBB", signal="Short", signal_value=-1,
            agreement_active=4, agreement_total=5,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=2,
        ),
    ]
    wrapper = board.render_scoreboard(rows)
    text = _component_text(wrapper)
    assert "Buy" in text
    assert "Short" in text
    # And the data attributes match.
    by_ticker = {
        p.get("data-ticker"): p for p in _tbody_tr_props(wrapper)
    }
    assert by_ticker["AAA"].get("data-signal") == "Buy"
    assert by_ticker["BBB"].get("data-signal") == "Short"


def test_scoreboard_column_header_reads_consensus():
    """The public column header is "Consensus", not the legacy
    "Signal", so a first-time visitor doesn't conflate the
    Confluence consensus with the Signal Engine's own current
    signal in the Featured panel."""
    pytest.importorskip("dash")
    assert board.BOARD_COPY["col_signal"] == "Consensus"
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows)
    text = _component_text(wrapper)
    assert "Consensus" in text


def test_featured_confluence_copy_no_longer_says_60_timeframes():
    """Phase 6G-1: the agreement count is 12 K-builds x 5
    timeframes = 60 alignment CHECKS, not 60 distinct
    TIMEFRAMES. Old wording over-claimed the timeframe
    dimension."""
    fmt = board.BOARD_COPY["confluence_status_fmt"]
    assert "timeframes agree" not in fmt, (
        "Old over-claiming wording leaked: " + repr(fmt)
    )
    assert "alignment checks active" in fmt, (
        "Expected new wording 'alignment checks active'; got "
        + repr(fmt)
    )


def test_two_signal_explainer_is_sourced_from_board_copy(
    tmp_path: Path,
):
    """The short explainer that defuses the
    'scoreboard No consensus vs Featured Short' confusion
    must live in BOARD_COPY and must render under the
    Featured panel."""
    pytest.importorskip("dash")
    assert "two_signal_explainer" in board.BOARD_COPY
    explainer = board.BOARD_COPY["two_signal_explainer"]
    assert "Board consensus" in explainer
    assert "Signal Engine" in explainer
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    text = _component_text(app.layout)
    assert explainer in text, (
        "two_signal_explainer string must render somewhere in "
        "the layout; it did not"
    )


def test_evidence_trail_intro_is_sourced_from_board_copy(
    tmp_path: Path,
):
    """Phase 6G-1: the seven-station Evidence Trail now opens
    with a short intro paragraph that frames how to read
    'stale' upstream stations."""
    pytest.importorskip("dash")
    assert "evidence_trail_intro" in board.BOARD_COPY
    intro = board.BOARD_COPY["evidence_trail_intro"]
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    text = _component_text(app.layout)
    assert intro in text


def test_current_pilot_card_renders_when_leader_exists(
    tmp_path: Path,
):
    """When at least one row is leader_eligible, the new
    Today's Board Status panel renders a card with the
    pilot ticker, consensus framing, Signal Engine state,
    and an as-of stamp. Avoids any directional copy on
    signal=None ('No directional consensus today')."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    card = board.render_current_pilot_card(
        rows,
        signal_engine_pair="Short 11,5",
        signal_engine_as_of="2026-05-11",
    )
    props = card.to_plotly_json().get("props", {})
    assert props.get("id") == "current-pilot-card"
    assert (
        props.get("data-current-pilot-ticker") == "SPY"
    )
    assert (
        props.get("data-current-pilot-leader-eligible")
        == "true"
    )
    assert (
        props.get("data-current-pilot-consensus-signal")
        == "None"
    )
    text = _component_text(card)
    assert "SPY is the current full-pipeline pilot." in text
    assert (
        "No directional consensus today" in text
    ), (
        "card must use the non-directional copy when "
        "Confluence consensus is None"
    )
    assert "Short 11,5" in text
    assert "2026-05-08" in text
    assert "2026-05-11" in text


def test_current_pilot_card_uses_non_directional_copy_when_none():
    """Even with a Buy / Short Confluence consensus elsewhere,
    a signal='None' pilot row must never render 'Buy direction'
    or 'Short direction'."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=1, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    card = board.render_current_pilot_card(rows)
    text = _component_text(card)
    assert "Buy direction" not in text
    assert "Short direction" not in text


def test_current_pilot_card_falls_back_when_no_leader_eligible():
    """No leader-eligible rows -> the card stays in the DOM
    with a clear "no current pilot" copy and
    data-current-pilot-leader-eligible='false'."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="AAA", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
            rank=None,
        ),
    ]
    card = board.render_current_pilot_card(rows)
    props = card.to_plotly_json().get("props", {})
    assert props.get("id") == "current-pilot-card"
    assert (
        props.get("data-current-pilot-leader-eligible")
        == "false"
    )
    text = _component_text(card)
    assert "No current pilot today" in text


def test_spy_data_attributes_preserved_under_6g1(tmp_path: Path):
    """Phase 6G-1 must not regress the public DOM contract:
    when SPY is leader-eligible, the SPY scoreboard row keeps
    data-rank='1', data-leader-eligible='true',
    data-ranking-blocked-reason=''."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows, selected_ticker="SPY")
    body_rows = _tbody_tr_props(wrapper)
    assert len(body_rows) == 1
    p = body_rows[0]
    assert p.get("data-ticker") == "SPY"
    assert p.get("data-leader-eligible") == "true"
    assert p.get("data-rank") == "1"
    assert p.get("data-ranking-blocked-reason") == ""


def test_partition_separates_current_from_archive():
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
        board.BoardRow(
            ticker="AAA", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
            rank=None,
        ),
        board.BoardRow(
            ticker="^GSPC", signal="None", signal_value=0,
            agreement_active=1, agreement_total=1,
            coverage=board.COVERAGE_STALE, as_of="2026-05-01",
            rank=None,
        ),
    ]
    current, archive = board._partition_rows_for_board(rows)
    assert [r.ticker for r in current] == ["SPY"]
    assert {r.ticker for r in archive} == {"AAA", "^GSPC"}


def test_archive_section_collapses_partial_and_stale_rows(
    tmp_path: Path,
):
    """When leader-eligible rows exist, the default
    section-scoreboard contains only those rows; the
    Partial / Stale / Under review / Pipeline incomplete
    rows live in section-archive (collapsed by default).
    """
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    # Three fixtures: SPY (will be leader-eligible after we
    # write the Confluence artifact); AAA + BBB stay cache-
    # only (Partial coverage).
    _write_min_spymaster_cache(cache_dir, "SPY")
    _write_min_spymaster_cache(cache_dir, "AAA")
    _write_min_spymaster_cache(cache_dir, "BBB")

    # Monkey-patch discovery to inject a deterministic
    # leader-eligible SPY plus two archive rows. Uses the
    # public BoardRow contract.
    real_discover = board.discover_board_catalogue

    def _fake_discover(**kw):
        return (
            board.BoardRow(
                ticker="SPY", signal="None", signal_value=0,
                agreement_active=7, agreement_total=60,
                coverage=board.COVERAGE_FULL, as_of="2026-05-08",
                leader_eligible=True, rank=1,
            ),
            board.BoardRow(
                ticker="AAA", signal="None", signal_value=0,
                agreement_active=None, agreement_total=None,
                coverage=board.COVERAGE_PARTIAL, as_of=None,
                rank=None,
            ),
            board.BoardRow(
                ticker="BBB", signal="None", signal_value=0,
                agreement_active=None, agreement_total=None,
                coverage=board.COVERAGE_PARTIAL, as_of=None,
                rank=None,
            ),
        )

    import unittest.mock as mock
    with mock.patch.object(
        board, "discover_board_catalogue", _fake_discover,
    ):
        app = board.build_app(
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            sig_lib_dir=sig_lib_dir,
        )

    def _find(node, target_id):
        if node is None or isinstance(node, str):
            return None
        if isinstance(node, (list, tuple)):
            for c in node:
                r = _find(c, target_id)
                if r is not None:
                    return r
            return None
        if getattr(node, "id", None) == target_id:
            return node
        return _find(getattr(node, "children", None), target_id)

    # section-archive exists and reports the row count.
    archive_section = _find(app.layout, "section-archive")
    assert archive_section is not None
    arch_props = archive_section.to_plotly_json().get("props", {})
    assert arch_props.get("data-archive-row-count") == "2"

    # section-archive-details is collapsed (open=False).
    details = _find(app.layout, "section-archive-details")
    assert details is not None
    details_props = details.to_plotly_json().get("props", {})
    assert details_props.get("open") is False

    # And the archive table carries the archive tickers.
    archive_text = _component_text(archive_section)
    assert "AAA" in archive_text
    assert "BBB" in archive_text

    # The MAIN scoreboard (section-scoreboard) does NOT
    # carry the archive tickers.
    main_scoreboard = _find(app.layout, "section-scoreboard")
    assert main_scoreboard is not None
    main_text = _component_text(main_scoreboard)
    assert "SPY" in main_text
    assert "AAA" not in main_text, (
        "section-scoreboard must not include archive rows "
        "when leader-eligible rows exist"
    )
    assert "BBB" not in main_text


def test_no_current_leaders_banner_still_works_when_zero_eligible(
    tmp_path: Path,
):
    """Phase 6G-1 partitioning must preserve the existing
    Phase 6C-8 no-current-leaders banner: when no rows pass
    the leader gate, the banner renders inside section-scoreboard
    with data-leader-count='0'."""
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    _write_min_spymaster_cache(cache_dir, "AAA")

    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )

    def _find(node, target_id):
        if node is None or isinstance(node, str):
            return None
        if isinstance(node, (list, tuple)):
            for c in node:
                r = _find(c, target_id)
                if r is not None:
                    return r
            return None
        if getattr(node, "id", None) == target_id:
            return node
        return _find(getattr(node, "children", None), target_id)

    banner = _find(app.layout, "scoreboard-no-current-leaders")
    assert banner is not None
    props = banner.to_plotly_json().get("props", {})
    # Real cache-only fixtures -> zero leader-eligible rows
    # -> the banner is visible and reports count 0.
    assert props.get("data-leader-count") == "0"
    style = props.get("style") or {}
    assert style.get("display") != "none"


# ---------------------------------------------------------------------------
# Phase 6G-4: Town Notice Board visual polish
# ---------------------------------------------------------------------------


def test_design_tokens_have_leader_highlight_distinct_from_brand_green():
    """Phase 6G-4: the legacy neon green moves into its own
    ``color_leader_highlight`` token, while ``color_green``
    is re-anchored to a sage / moss brand color. The two
    must be different values so brightness consistently
    signals "this is the current pick" rather than leaking
    everywhere on the page."""
    assert "color_leader_highlight" in board.DESIGN_TOKENS
    assert "color_green" in board.DESIGN_TOKENS
    assert (
        board.DESIGN_TOKENS["color_leader_highlight"]
        != board.DESIGN_TOKENS["color_green"]
    ), (
        "leader-highlight token must be distinct from the "
        "everyday brand green; both currently resolve to "
        + repr(board.DESIGN_TOKENS["color_green"])
    )


def test_design_tokens_include_notice_board_palette():
    """The Town Notice Board palette additions
    (``color_warm_dark`` / ``color_paper`` / ``color_pin``)
    must be available so callers don't need to hand-roll
    these literals outside the DESIGN_TOKENS block."""
    for key in (
        "color_warm_dark", "color_paper", "color_pin",
        "color_leader_highlight",
    ):
        assert key in board.DESIGN_TOKENS, (
            f"missing Phase 6G-4 palette token {key!r}"
        )


def test_current_pilot_card_retains_data_attributes_under_6g4(
    tmp_path: Path,
):
    """Phase 6G-4 layout polish must not regress the
    ``data-current-pilot-*`` contract."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    card = board.render_current_pilot_card(
        rows,
        signal_engine_pair="Short 11,5",
        signal_engine_as_of="2026-05-11",
    )
    props = card.to_plotly_json().get("props", {})
    assert props.get("id") == "current-pilot-card"
    assert props.get("data-current-pilot-ticker") == "SPY"
    assert (
        props.get("data-current-pilot-leader-eligible")
        == "true"
    )
    assert (
        props.get("data-current-pilot-consensus-signal")
        == "None"
    )


def test_current_pilot_card_has_pin_and_chip(tmp_path: Path):
    """Phase 6G-4: the pinned-paper feel requires a small
    CSS-drawn pin (``current-pilot-pin``) and a leader-
    highlight pilot chip (``current-pilot-chip``) carrying
    the ticker symbol. Both are pure HTML/CSS - no image
    assets - and live alongside the existing
    ``data-current-pilot-*`` attributes."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    card = board.render_current_pilot_card(rows)

    def _find(node, target_id):
        if node is None or isinstance(node, str):
            return None
        if isinstance(node, (list, tuple)):
            for c in node:
                r = _find(c, target_id)
                if r is not None:
                    return r
            return None
        if getattr(node, "id", None) == target_id:
            return node
        return _find(getattr(node, "children", None), target_id)

    pin = _find(card, "current-pilot-pin")
    chip = _find(card, "current-pilot-chip")
    assert pin is not None, "current-pilot-pin is missing"
    assert chip is not None, "current-pilot-chip is missing"
    chip_props = chip.to_plotly_json().get("props", {})
    # Chip carries the ticker symbol both as its visible
    # text and as a data attribute.
    assert chip_props.get("children") == "SPY"
    assert chip_props.get("data-pilot-chip-ticker") == "SPY"


def test_spy_row_data_attributes_preserved_under_6g4():
    """Phase 6G-4 must not regress the SPY scoreboard row
    data contract."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows, selected_ticker="SPY")
    body_rows = _tbody_tr_props(wrapper)
    assert len(body_rows) == 1
    p = body_rows[0]
    assert p.get("data-ticker") == "SPY"
    assert p.get("data-leader-eligible") == "true"
    assert p.get("data-rank") == "1"
    assert p.get("data-ranking-blocked-reason") == ""
    assert p.get("data-signal") == "None"
    assert p.get("data-signal-value") == "0"
    assert p.get("data-coverage") == board.COVERAGE_FULL


def test_leader_row_carries_leader_highlight_accent():
    """The current-leader row must visually announce
    itself with the ``color_leader_highlight`` token (a
    left-border accent in the legacy neon). This is the
    only place on the page that uses that token in the
    scoreboard."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
        board.BoardRow(
            ticker="AAA", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
            rank=None,
        ),
    ]
    wrapper = board.render_scoreboard(rows)
    body_rows = _tbody_tr_props(wrapper)
    by_ticker = {
        p.get("data-ticker"): p for p in body_rows
    }
    spy_style = by_ticker["SPY"].get("style") or {}
    aaa_style = by_ticker["AAA"].get("style") or {}
    leader = board.DESIGN_TOKENS["color_leader_highlight"]
    assert leader in str(spy_style.get("borderLeft", "")), (
        "SPY row must carry a left-border accent in the "
        "leader-highlight token; got "
        + repr(spy_style)
    )
    assert "borderLeft" not in aaa_style, (
        "Non-leader row must NOT receive the leader-highlight "
        "accent; got " + repr(aaa_style)
    )


def test_scoreboard_coverage_cell_renders_as_pill_with_dot():
    """Phase 6G-4: the Coverage cell renders a small
    wax-seal pill (rounded filled badge with a leading
    dot) instead of a bare colored word. The underlying
    coverage text is unchanged."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows)
    body_rows = _tbody_tr_props(wrapper)
    # Find the Coverage Td (index 3 of 5).
    row_children = body_rows[0].get("children")
    coverage_td = row_children[3]
    # The cell's children is the pill Span.
    pill = coverage_td.to_plotly_json().get(
        "props", {},
    ).get("children")
    pill_props = pill.to_plotly_json().get("props", {})
    pill_style = pill_props.get("style") or {}
    # The pill is a rounded inline-flex badge sitting on
    # the paper surface.
    assert "borderRadius" in pill_style
    assert pill_style.get("display") == "inline-flex"
    # Its children are a leading colored dot + the
    # coverage label text.
    pill_children = pill_props.get("children")
    assert isinstance(pill_children, list)
    assert len(pill_children) == 2
    dot_style = pill_children[0].to_plotly_json().get(
        "props", {},
    ).get("style") or {}
    assert dot_style.get("borderRadius") == "50%"
    label_text = pill_children[1].to_plotly_json().get(
        "props", {},
    ).get("children")
    assert label_text == board.COVERAGE_FULL


def test_archive_summary_uses_notice_board_drawer_copy():
    """Phase 6G-4: archive disclosure copy reads as a
    notice-board drawer rather than a database row count.
    Sourced from BOARD_COPY so the copy-centralization
    test still catches it."""
    fmt = board.BOARD_COPY["section_archive_summary_fmt"]
    assert "saved-research drawer" in fmt
    assert "{count}" in fmt
    assert "Show " not in fmt


def test_featured_pilot_prefix_is_sourced_from_board_copy(
    tmp_path: Path,
):
    """The Featured panel now leads with a small muted
    "Today's pilot" prefix above the ticker glyph, so the
    panel reads as 'Today's pilot - SPY' instead of a
    Bloomberg ticker block."""
    pytest.importorskip("dash")
    assert "featured_pilot_prefix" in board.BOARD_COPY
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )

    def _find(node, target_id):
        if node is None or isinstance(node, str):
            return None
        if isinstance(node, (list, tuple)):
            for c in node:
                r = _find(c, target_id)
                if r is not None:
                    return r
            return None
        if getattr(node, "id", None) == target_id:
            return node
        return _find(getattr(node, "children", None), target_id)

    prefix = _find(app.layout, "featured-pilot-prefix")
    assert prefix is not None
    text = prefix.to_plotly_json().get("props", {}).get("children")
    assert text == board.BOARD_COPY["featured_pilot_prefix"]


def test_evidence_trail_station_glyphs_render(tmp_path: Path):
    """Phase 6G-4: each Evidence Trail station card now
    carries a small two-letter "stamp" prefix (SF / TP /
    WK / RY / CH / TH / WT) sourced from the
    station-id -> glyph map. No emoji and no external
    image assets."""
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    text = _component_text(app.layout)
    for glyph in ("SF", "TP", "WK", "RY", "CH", "TH", "WT"):
        assert glyph in text, (
            f"missing station glyph {glyph!r} in rendered layout"
        )


def test_scoreboard_table_wrapper_preserves_mobile_overflow_contract():
    """Phase 6G-4 must not regress the Phase 6F-7 mobile
    contained-scroll contract: the scoreboard wrapper still
    declares ``data-mobile-overflow="contained"`` and an
    ``overflowX: auto`` style."""
    pytest.importorskip("dash")
    rows = [
        board.BoardRow(
            ticker="SPY", signal="None", signal_value=0,
            agreement_active=7, agreement_total=60,
            coverage=board.COVERAGE_FULL, as_of="2026-05-08",
            leader_eligible=True, rank=1,
        ),
    ]
    wrapper = board.render_scoreboard(rows)
    props = wrapper.to_plotly_json().get("props", {})
    assert props.get("id") == "scoreboard-table-wrapper"
    assert props.get("data-mobile-overflow") == "contained"
    assert (props.get("style") or {}).get("overflowX") == "auto"


# ---------------------------------------------------------------------------
# Component traversal helpers
# ---------------------------------------------------------------------------


def _flatten_board_copy_values() -> set[str]:
    out: set[str] = set()
    for v in board.BOARD_COPY.values():
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, str):
                    out.add(item)
    return out


def _component_contains_id(component: Any, target_id: str) -> bool:
    if component is None or isinstance(component, str):
        return False
    if isinstance(component, (list, tuple)):
        return any(
            _component_contains_id(c, target_id) for c in component
        )
    if getattr(component, "id", None) == target_id:
        return True
    children = getattr(component, "children", None)
    if children is None:
        return False
    return _component_contains_id(children, target_id)


def _component_text(component: Any) -> str:
    pieces: list[str] = []

    def _walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, str):
            pieces.append(node)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)
            return
        cid = getattr(node, "id", None)
        if cid is not None:
            pieces.append(str(cid))
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(component)
    return "\n".join(pieces)


def _find_data_ranking_method(component: Any) -> Optional[str]:
    """Walk the layout and return the ``data-ranking-method``
    attribute on the ``section-scoreboard`` section, or ``None``
    if no scoreboard section is found."""

    def _walk(node: Any) -> Optional[str]:
        if node is None or isinstance(node, str):
            return None
        if isinstance(node, (list, tuple)):
            for child in node:
                found = _walk(child)
                if found is not None:
                    return found
            return None
        if getattr(node, "id", None) == "section-scoreboard":
            try:
                props = node.to_plotly_json().get("props", {})
            except Exception:
                props = {}
            method = props.get("data-ranking-method")
            return str(method) if method is not None else None
        children = getattr(node, "children", None)
        if children is not None:
            return _walk(children)
        return None

    return _walk(component)


def _tbody_tr_props(table: Any) -> list[dict[str, Any]]:
    """Walk an ``html.Table`` and return the ``props`` dict for every
    ``Tr`` inside the ``Tbody`` (skips header rows). Surfaces
    ``data-*`` attributes via ``to_plotly_json()['props']``."""
    rows: list[dict[str, Any]] = []

    def _walk_tbody(node: Any) -> None:
        if node is None or isinstance(node, str):
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk_tbody(child)
            return
        if type(node).__name__ == "Tr":
            try:
                props = node.to_plotly_json().get("props", {})
            except Exception:
                props = {}
            rows.append(props)
            return
        children = getattr(node, "children", None)
        if children is not None:
            _walk_tbody(children)

    def _find_tbody(node: Any) -> Any:
        if node is None or isinstance(node, str):
            return None
        if isinstance(node, (list, tuple)):
            for child in node:
                found = _find_tbody(child)
                if found is not None:
                    return found
            return None
        if type(node).__name__ == "Tbody":
            return node
        children = getattr(node, "children", None)
        if children is not None:
            return _find_tbody(children)
        return None

    tbody = _find_tbody(table)
    if tbody is None:
        return rows
    _walk_tbody(getattr(tbody, "children", None))
    return rows


def _ordered_station_ids(component: Any) -> list[str]:
    seen: list[str] = []
    sidset = set(board.STATION_IDS)

    def _walk(node: Any) -> None:
        if node is None or isinstance(node, str):
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)
            return
        cid = getattr(node, "id", None)
        if cid in sidset and cid not in seen:
            seen.append(cid)
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(component)
    return seen
