"""Phase 6I-37 tests for the current build signal surface.

Pins the per-cell `current_build_signals` matrix and the
aggregate `current_build_signal_summary` across the three
read-only layers:

  * Phase 6I-34 ``confluence_multiwindow_ranking_export``
  * Phase 6I-35 ``confluence_website_export_package``
  * Phase 6I-36 ``confluence_website_reader_view``

Key contracts:

  * Eligible rows expose a 60-entry canonical
    ``current_build_signals`` matrix (one row per
    ``(K, window)`` cell, in canonical ``(window, K)`` order).
  * Each row carries: ticker, K, window, latest_combined_signal,
    latest_buy_count, latest_short_count, latest_none_count,
    latest_missing_count, member_count, alignment_ratio,
    all_members_aligned, currently_signaling, firing,
    total_capture_pct, avg_daily_capture_pct, sharpe_ratio,
    trigger_days, wins, losses.
  * The aggregate summary counts cells by current signal,
    surfaces windows with any currently-signaling cell, and
    reports the strongest currently-signaling cell.
  * Multi-ticker fixtures preserve ranking order.
  * Blocked tickers expose NO fabricated current-signal
    fields (empty matrix + null summary).
  * Partial / malformed payloads do not crash and do not
    fabricate -- they remain blocked.
  * Phase 6I-20 strict validation is preserved (canonical
    60-cell coverage required for eligibility).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_multiwindow_ranking_export as _cmre  # noqa: E402
import confluence_website_export_package as _cwep  # noqa: E402
import confluence_website_reader_view as _crv  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: build a valid 60-cell artifact with caller-controlled
# per-cell current signals.
# ---------------------------------------------------------------------------


def _make_cell(
    *,
    K: int,
    window: str,
    latest_combined_signal: str = "Buy",
    latest_buy_count: int = 3,
    latest_short_count: int = 0,
    latest_none_count: int = 0,
    latest_missing_count: int = 0,
    member_count: int = 3,
    total_capture_pct: float = 10.0,
    avg_daily_capture_pct: float = 0.5,
    sharpe_ratio: float = 1.2,
    trigger_days: int = 20,
    wins: int = 12,
    losses: int = 8,
) -> dict[str, Any]:
    return {
        "K": K,
        "window": window,
        "total_capture_pct": float(total_capture_pct),
        "avg_daily_capture_pct": float(
            avg_daily_capture_pct,
        ),
        "sharpe_ratio": float(sharpe_ratio),
        "trigger_days": int(trigger_days),
        "wins": int(wins),
        "losses": int(losses),
        "latest_combined_signal": latest_combined_signal,
        "latest_buy_count": int(latest_buy_count),
        "latest_short_count": int(latest_short_count),
        "latest_none_count": int(latest_none_count),
        "latest_missing_count": int(latest_missing_count),
        "member_count": int(member_count),
    }


def _make_full_60_cell_artifact(
    *,
    ticker: str,
    pattern: str = "all_buy",
) -> dict[str, Any]:
    """Build a full 60-cell artifact for ``ticker``.

    ``pattern`` controls the latest-signal distribution:

      * ``"all_buy"``    : every cell currently signaling Buy with
                           full member alignment.
      * ``"all_short"``  : every cell currently signaling Short
                           with full member alignment.
      * ``"all_none"``   : every cell currently signaling None
                           (zero alignment).
      * ``"mixed"``      : 1d cells Buy, 1wk cells Short,
                           1mo cells None, 3mo cells Buy with
                           partial alignment (2/3 members),
                           1y cells Buy with full alignment.
    """
    windows = ("1d", "1wk", "1mo", "3mo", "1y")
    Ks = tuple(range(1, 13))
    cells: list[dict[str, Any]] = []
    for w in windows:
        for K in Ks:
            if pattern == "all_buy":
                cells.append(
                    _make_cell(
                        K=K, window=w,
                        latest_combined_signal="Buy",
                        latest_buy_count=3,
                        member_count=3,
                    )
                )
            elif pattern == "all_short":
                cells.append(
                    _make_cell(
                        K=K, window=w,
                        latest_combined_signal="Short",
                        latest_buy_count=0,
                        latest_short_count=3,
                        member_count=3,
                    )
                )
            elif pattern == "all_none":
                cells.append(
                    _make_cell(
                        K=K, window=w,
                        latest_combined_signal="None",
                        latest_buy_count=0,
                        latest_short_count=0,
                        latest_none_count=3,
                        member_count=3,
                        trigger_days=0,
                    )
                )
            elif pattern == "mixed":
                if w == "1d":
                    cells.append(
                        _make_cell(
                            K=K, window=w,
                            latest_combined_signal="Buy",
                            latest_buy_count=3,
                            member_count=3,
                            total_capture_pct=15.0,
                        )
                    )
                elif w == "1wk":
                    cells.append(
                        _make_cell(
                            K=K, window=w,
                            latest_combined_signal="Short",
                            latest_buy_count=0,
                            latest_short_count=3,
                            member_count=3,
                            total_capture_pct=12.0,
                        )
                    )
                elif w == "1mo":
                    cells.append(
                        _make_cell(
                            K=K, window=w,
                            latest_combined_signal="None",
                            latest_buy_count=0,
                            latest_short_count=0,
                            latest_none_count=3,
                            member_count=3,
                            trigger_days=0,
                            total_capture_pct=0.0,
                        )
                    )
                elif w == "3mo":
                    cells.append(
                        _make_cell(
                            K=K, window=w,
                            latest_combined_signal="Buy",
                            latest_buy_count=2,
                            latest_short_count=0,
                            latest_none_count=1,
                            member_count=3,
                            total_capture_pct=8.0,
                        )
                    )
                else:  # 1y
                    cells.append(
                        _make_cell(
                            K=K, window=w,
                            latest_combined_signal="Buy",
                            latest_buy_count=3,
                            member_count=3,
                            total_capture_pct=20.0,
                        )
                    )
            else:
                raise ValueError(
                    f"unknown pattern {pattern!r}",
                )
    bwwa: dict[str, Any] = {}
    for w in windows:
        if pattern in ("all_buy", "all_short"):
            bwwa[w] = {
                "all_members_firing": True,
                "firing_member_count": 3,
                "total_member_count": 3,
            }
        elif pattern == "all_none":
            bwwa[w] = {
                "all_members_firing": False,
                "firing_member_count": 0,
                "total_member_count": 3,
            }
        elif pattern == "mixed":
            if w == "1mo":
                bwwa[w] = {
                    "all_members_firing": False,
                    "firing_member_count": 0,
                    "total_member_count": 3,
                }
            elif w == "3mo":
                bwwa[w] = {
                    "all_members_firing": False,
                    "firing_member_count": 2,
                    "total_member_count": 3,
                }
            else:
                bwwa[w] = {
                    "all_members_firing": True,
                    "firing_member_count": 3,
                    "total_member_count": 3,
                }
    return {
        "target_ticker": ticker,
        "generated_at": "2026-05-14T00:00:00+00:00",
        "per_window_k_metrics": cells,
        "build_wide_window_alignment": bwwa,
        "multiwindow_k_engine_payload_metadata": {
            "schema_version": "v1",
            "evaluator": "multiwindow_k_engine_core",
        },
        "daily": {
            "dates": ["2026-05-14"],
            "close": [100.0],
            "last_date": "2026-05-14",
        },
        "timeframes": list(windows),
        "chart_rows": [
            {"date": "2026-05-14", "close": 100.0},
        ],
    }


def _make_blocked_daily_only_artifact(
    *,
    ticker: str,
) -> dict[str, Any]:
    """Pre-Phase-6I-20 daily-only artifact -- carries the
    Phase 6C timeframes + summary shape but NOT the multi-
    window K fields. Must classify as ``daily_only`` and
    NOT receive a fabricated current-signal matrix."""
    return {
        "target_ticker": ticker,
        "generated_at": "2026-05-14T00:00:00+00:00",
        "timeframes": ["1d"],
        "summary": {"some_legacy_field": True},
        "daily": {
            "dates": ["2026-05-14"],
            "last_date": "2026-05-14",
        },
    }


# ---------------------------------------------------------------------------
# Cell-level matrix builder
# ---------------------------------------------------------------------------


def test_full_60_cell_payload_yields_60_row_matrix():
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    assert len(matrix) == 60
    # Canonical (window, K) order pinned.
    expected_order = [
        (K, w) for w in (
            "1d", "1wk", "1mo", "3mo", "1y",
        ) for K in range(1, 13)
    ]
    got_order = [(r["K"], r["window"]) for r in matrix]
    assert got_order == expected_order


def test_matrix_row_carries_required_phase_6i37_fields():
    """Phase 6I-37 amendment-1: per-cell schema must carry
    every required field, including the renamed
    historically_fired flag and the currently_firing alias."""
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    row = matrix[0]
    required = {
        "ticker", "K", "window",
        "latest_combined_signal",
        "latest_buy_count", "latest_short_count",
        "latest_none_count", "latest_missing_count",
        "member_count",
        "alignment_ratio", "all_members_aligned",
        # Phase 6I-37 amendment-1 naming:
        # currently_signaling + currently_firing (alias)
        # for current state; historically_fired for
        # historical (trigger_days > 0).
        "currently_signaling", "currently_firing",
        "historically_fired",
        "total_capture_pct", "avg_daily_capture_pct",
        "sharpe_ratio", "trigger_days",
        "wins", "losses",
    }
    assert required.issubset(set(row.keys()))
    # The ambiguous old "firing" name MUST be gone.
    assert "firing" not in row, (
        "Phase 6I-37 amendment-1: per-cell 'firing' flag "
        "must be renamed to 'historically_fired' for "
        "current-vs-historical clarity."
    )


def test_all_buy_pattern_alignment_ratios_are_one():
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    for row in matrix:
        assert row["latest_combined_signal"] == "Buy"
        assert row["alignment_ratio"] == 1.0
        assert row["all_members_aligned"] is True
        assert row["currently_signaling"] is True
        # Phase 6I-37 amendment-1: currently_firing is an
        # alias of currently_signaling for UI clarity;
        # historically_fired is the trigger_days>0 flag.
        assert row["currently_firing"] is True
        assert row["historically_fired"] is True
        assert row["ticker"] == "AAA"


def test_all_none_pattern_no_currently_signaling_cells():
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_none",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    for row in matrix:
        assert row["latest_combined_signal"] == "None"
        assert row["alignment_ratio"] == 0.0
        assert row["all_members_aligned"] is False
        assert row["currently_signaling"] is False
        assert row["currently_firing"] is False
        assert row["historically_fired"] is False


def test_mixed_pattern_per_window_signal_distribution():
    """Mixed: 1d=Buy, 1wk=Short, 1mo=None, 3mo=Buy(partial),
    1y=Buy."""
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="mixed",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    by_window = {}
    for row in matrix:
        by_window.setdefault(row["window"], []).append(row)
    # 1d: all Buy, alignment_ratio=1.0
    assert all(
        r["latest_combined_signal"] == "Buy"
        and r["alignment_ratio"] == 1.0
        for r in by_window["1d"]
    )
    # 1wk: all Short, alignment_ratio=1.0
    assert all(
        r["latest_combined_signal"] == "Short"
        and r["alignment_ratio"] == 1.0
        for r in by_window["1wk"]
    )
    # 1mo: all None, alignment_ratio=0.0
    assert all(
        r["latest_combined_signal"] == "None"
        and r["alignment_ratio"] == 0.0
        and r["currently_signaling"] is False
        for r in by_window["1mo"]
    )
    # 3mo: Buy with 2/3 alignment, not all aligned
    assert all(
        r["latest_combined_signal"] == "Buy"
        and abs(r["alignment_ratio"] - (2 / 3)) < 1e-9
        and r["all_members_aligned"] is False
        and r["currently_signaling"] is True
        for r in by_window["3mo"]
    )
    # 1y: all Buy, alignment 1.0
    assert all(
        r["latest_combined_signal"] == "Buy"
        and r["alignment_ratio"] == 1.0
        and r["all_members_aligned"] is True
        for r in by_window["1y"]
    )


def test_zero_member_count_does_not_div_by_zero():
    cell = _make_cell(
        K=1, window="1d",
        latest_combined_signal="Buy",
        latest_buy_count=0,
        member_count=0,
    )
    matrix = _cmre._build_current_signal_matrix(
        [cell], ticker="AAA",
    )
    assert len(matrix) == 1
    assert matrix[0]["alignment_ratio"] == 0.0
    assert matrix[0]["all_members_aligned"] is False


def test_matrix_skips_non_canonical_extras():
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    # Inject one non-canonical K=13 cell and one
    # non-canonical window=6mo cell. Both must be silently
    # skipped by the matrix builder.
    artifact["per_window_k_metrics"].append(
        _make_cell(K=13, window="1d"),
    )
    artifact["per_window_k_metrics"].append(
        _make_cell(K=1, window="6mo"),
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    assert len(matrix) == 60
    for row in matrix:
        assert row["K"] in range(1, 13)
        assert row["window"] in (
            "1d", "1wk", "1mo", "3mo", "1y",
        )


# ---------------------------------------------------------------------------
# Aggregate summary
# ---------------------------------------------------------------------------


def test_summary_all_buy_pattern_counts_correctly():
    """Phase 6I-37 amendment-1: all-Buy fixture exercises
    both the loose any-K predicates and the strict same-K
    predicates -- every K from 1..12 has Buy in every
    window."""
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    sumry = _cmre._build_current_signal_summary(
        matrix,
        bwwa=artifact["build_wide_window_alignment"],
    )
    assert sumry["cells_total"] == 60
    assert sumry["cells_currently_buy"] == 60
    assert sumry["cells_currently_short"] == 0
    assert sumry["cells_currently_none"] == 0
    assert sumry["cells_currently_missing"] == 0
    assert sumry["cells_with_all_members_aligned"] == 60
    # Amendment-1: cells_historically_firing renamed.
    assert sumry["cells_historically_fired"] == 60
    assert "cells_historically_firing" not in sumry
    # Any-K (loose).
    assert sumry["windows_with_any_currently_signaling"] == [
        "1d", "1wk", "1mo", "3mo", "1y",
    ]
    assert sumry["all_windows_have_any_current_signal"] is (
        True
    )
    assert "all_five_windows_currently_signaling" not in sumry
    # Same-K (strict). All K=1..12 signal in every window.
    assert (
        sumry["k_builds_currently_signaling_all_windows"]
        == list(range(1, 13))
    )
    assert (
        sumry["k_builds_all_members_aligned_all_windows"]
        == list(range(1, 13))
    )
    assert (
        sumry["all_five_windows_same_k_currently_signaling"]
        is True
    )
    assert (
        sumry["all_five_windows_same_k_all_members_aligned"]
        is True
    )
    assert (
        sumry["windows_with_all_members_firing"]
        == ["1d", "1wk", "1mo", "3mo", "1y"]
    )
    strongest = sumry["strongest_currently_signaling_cell"]
    assert strongest is not None
    assert strongest["latest_combined_signal"] == "Buy"
    cross = sumry["strongest_cross_window_k_build"]
    assert cross is not None
    assert cross["K"] in range(1, 13)
    # 5 windows * 10.0 capture each in the all_buy fixture
    # = 50.0 per K.
    assert cross["total_capture_pct_sum"] == 50.0
    assert cross["buy_window_count"] == 5
    assert cross["short_window_count"] == 0
    assert cross["all_members_aligned_window_count"] == 5


def test_summary_all_none_pattern_no_signaling_cells():
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_none",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    sumry = _cmre._build_current_signal_summary(
        matrix,
        bwwa=artifact["build_wide_window_alignment"],
    )
    assert sumry["cells_currently_buy"] == 0
    assert sumry["cells_currently_short"] == 0
    assert sumry["cells_currently_none"] == 60
    assert sumry["cells_historically_fired"] == 0
    # Loose any-K predicate AND strict same-K predicate
    # both False when nothing is signaling.
    assert (
        sumry["all_windows_have_any_current_signal"]
        is False
    )
    assert (
        sumry[
            "all_five_windows_same_k_currently_signaling"
        ]
        is False
    )
    assert (
        sumry["k_builds_currently_signaling_all_windows"]
        == []
    )
    assert (
        sumry["k_builds_all_members_aligned_all_windows"]
        == []
    )
    assert (
        sumry["strongest_cross_window_k_build"] is None
    )
    assert (
        sumry["strongest_currently_signaling_cell"] is None
    )
    # bwwa carries all_members_firing=False per window in
    # this fixture.
    assert sumry["windows_with_all_members_firing"] == []


def test_summary_mixed_pattern_strongest_currently_signaling():
    """Strongest currently-signaling cell must come from a
    cell with latest_combined_signal in Buy/Short and the
    highest total_capture_pct. In the mixed fixture, the 1y
    Buy cells have total_capture_pct=20.0 (highest), so the
    strongest currently-signaling cell is a 1y Buy."""
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="mixed",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    sumry = _cmre._build_current_signal_summary(
        matrix,
        bwwa=artifact["build_wide_window_alignment"],
    )
    strongest = sumry["strongest_currently_signaling_cell"]
    assert strongest is not None
    assert strongest["window"] == "1y"
    assert strongest["latest_combined_signal"] == "Buy"
    assert strongest["total_capture_pct"] == 20.0
    assert strongest["all_members_aligned"] is True
    # 1mo None cells are NOT currently signaling -> only 4
    # windows currently signaling. Both the any-K loose
    # predicate AND the strict same-K predicate must
    # therefore be False.
    assert (
        sumry["all_windows_have_any_current_signal"]
        is False
    )
    assert set(
        sumry["windows_with_any_currently_signaling"]
    ) == {"1d", "1wk", "3mo", "1y"}
    assert (
        sumry[
            "all_five_windows_same_k_currently_signaling"
        ]
        is False
    )
    assert (
        sumry["k_builds_currently_signaling_all_windows"]
        == []
    )
    assert (
        sumry["strongest_cross_window_k_build"] is None
    )


# ---------------------------------------------------------------------------
# Ranking-export plumbing: rank_eligible ticker carries the
# current-signal surface; blocked ticker does not.
# ---------------------------------------------------------------------------


def _injected_loader(
    artifact_map: dict[str, dict[str, Any]],
):
    def loader(path):
        # Use the trailing dir name as the ticker key.
        # ``path`` looks like:
        #   <root>/confluence/<TICKER>/<TICKER>__MTF...json
        parts = Path(path).parts
        for p in reversed(parts):
            if p in artifact_map:
                return artifact_map[p]
        return None
    return loader


def _fake_chart_readiness(
    ticker, artifact, *, cache_dir=None,
):
    return {
        "chart_ready_available": True,
        "chart_ready_source": "confluence_artifact",
        "chart_row_count": 100,
        "chart_blocker": None,
    }


def _build_export(
    tickers, artifact_map, *, tmp_path,
):
    """Build a real Phase 6I-34 export using an injected
    loader so we don't need on-disk artifacts."""
    # _resolve_artifact_path requires <root>/confluence/
    # <TICKER>/... to exist on disk -- create stubs.
    art_root = tmp_path / "research_artifacts"
    (art_root / "confluence").mkdir(parents=True)
    for t in tickers:
        td = art_root / "confluence" / t
        td.mkdir(parents=True)
        (
            td
            / f"{t}__MTF_CONSENSUS.research_day.json"
        ).write_text("{}", encoding="utf-8")
    return _cmre.build_multiwindow_ranking_export(
        tickers,
        artifact_root=art_root,
        cache_dir=None,
        artifact_loader_callable=_injected_loader(
            artifact_map,
        ),
        chart_readiness_callable=_fake_chart_readiness,
    )


def test_rank_eligible_row_carries_current_build_signals(
    tmp_path,
):
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    report = _build_export(
        ["AAA"],
        {"AAA": artifact},
        tmp_path=tmp_path,
    )
    assert report.eligible_count == 1
    elig = report.ranking_rows[0]
    assert elig.rank_eligible is True
    assert len(elig.current_build_signals) == 60
    assert elig.current_build_signal_summary is not None
    assert (
        elig.current_build_signal_summary[
            "cells_currently_buy"
        ]
        == 60
    )


def test_blocked_daily_only_row_has_no_current_signals(
    tmp_path,
):
    """A blocked daily-only ticker must NOT receive a
    fabricated current-signal matrix or summary."""
    blocked = _make_blocked_daily_only_artifact(
        ticker="BBB",
    )
    report = _build_export(
        ["BBB"],
        {"BBB": blocked},
        tmp_path=tmp_path,
    )
    assert report.eligible_count == 0
    assert report.blocked_count == 1
    blk = report.blocked_rows[0]
    assert blk.rank_eligible is False
    assert (
        blk.ranking_blocked_reason == "daily_only"
    )
    assert blk.current_build_signals == ()
    assert blk.current_build_signal_summary is None


def test_partial_payload_does_not_fabricate_current_signals(
    tmp_path,
):
    """An artifact carrying ``per_window_k_metrics`` but no
    ``build_wide_window_alignment`` is incomplete and must
    classify as blocked WITHOUT a current-signal matrix."""
    artifact = _make_full_60_cell_artifact(
        ticker="CCC", pattern="all_buy",
    )
    artifact.pop("build_wide_window_alignment", None)
    report = _build_export(
        ["CCC"],
        {"CCC": artifact},
        tmp_path=tmp_path,
    )
    assert report.eligible_count == 0
    assert report.blocked_count == 1
    blk = report.blocked_rows[0]
    assert blk.rank_eligible is False
    assert blk.current_build_signals == ()
    assert blk.current_build_signal_summary is None


# ---------------------------------------------------------------------------
# Multi-ticker fixture: ranking order + multi-ticker plumbing.
# ---------------------------------------------------------------------------


def test_multi_ticker_export_with_two_eligible_and_one_blocked(
    tmp_path,
):
    """Two eligible tickers + one blocked daily-only ticker
    -- ranking order preserved; eligible rows carry current-
    signal surface; blocked row does not."""
    art_AAA = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    art_BBB = _make_full_60_cell_artifact(
        ticker="BBB", pattern="mixed",
    )
    art_CCC = _make_blocked_daily_only_artifact(
        ticker="CCC",
    )
    report = _build_export(
        ["AAA", "BBB", "CCC"],
        {
            "AAA": art_AAA,
            "BBB": art_BBB,
            "CCC": art_CCC,
        },
        tmp_path=tmp_path,
    )
    assert report.eligible_count == 2
    assert report.blocked_count == 1
    elig_tickers = [
        r.ticker for r in report.ranking_rows
    ]
    # AAA is "all_buy" (all 5 windows currently firing
    # AND all members aligned) so it ranks above BBB.
    assert elig_tickers == ["AAA", "BBB"]
    # AAA carries 60-cell matrix.
    assert len(report.ranking_rows[0].current_build_signals) == 60
    # BBB carries 60-cell matrix.
    assert len(report.ranking_rows[1].current_build_signals) == 60
    # CCC blocked.
    blk = report.blocked_rows[0]
    assert blk.ticker == "CCC"
    assert blk.current_build_signals == ()


# ---------------------------------------------------------------------------
# Phase 6I-35 plumbing: package surfaces the matrix on
# eligible ticker_details and the summary on the ranking row.
# ---------------------------------------------------------------------------


def test_package_eligible_ticker_detail_carries_full_matrix(
    tmp_path,
):
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )

    # Inject the report into the package builder via
    # underlying_export_callable.
    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"AAA": artifact},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    detail = package["ticker_details"]["AAA"]
    assert detail["rank_eligible"] is True
    assert len(detail["current_build_signals"]) == 60
    sumry = detail["current_build_signal_summary"]
    assert sumry is not None
    assert sumry["cells_currently_buy"] == 60
    # The ranking_row also surfaces the summary.
    rrow = package["ranking_rows"][0]
    assert rrow["current_build_signal_summary"] is not None
    assert (
        rrow["current_build_signal_summary"][
            "cells_currently_buy"
        ]
        == 60
    )


def test_package_blocked_ticker_detail_has_no_current_signals(
    tmp_path,
):
    blocked = _make_blocked_daily_only_artifact(
        ticker="BBB",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"BBB": blocked},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["BBB"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    detail = package["ticker_details"]["BBB"]
    assert detail["rank_eligible"] is False
    assert detail["current_build_signals"] == []
    assert detail["current_build_signal_summary"] is None


# ---------------------------------------------------------------------------
# Phase 6I-36 plumbing: view model exposes the matrix on
# eligible ticker_cards and the compact summary on the
# ranking_table row.
# ---------------------------------------------------------------------------


def test_view_model_ranking_table_row_carries_signal_summary(
    tmp_path,
):
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"AAA": artifact},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    rt = vm["ranking_table"][0]
    cs = rt["current_signal_summary"]
    assert cs is not None
    assert cs["cells_currently_buy"] == 60
    # Phase 6I-37 amendment-1: the loose any-K predicate
    # was renamed; both loose and strict same-K predicates
    # are exposed on the compact ranking-table summary.
    assert (
        cs["all_windows_have_any_current_signal"] is True
    )
    assert "all_five_windows_currently_signaling" not in cs
    assert (
        cs["all_five_windows_same_k_currently_signaling"]
        is True
    )
    assert (
        cs["k_builds_currently_signaling_all_windows"]
        == list(range(1, 13))
    )
    assert (
        cs["strongest_currently_signaling_cell_label"]
        is not None
    )
    assert (
        cs["strongest_cross_window_k_build"] is not None
    )


def test_view_model_ticker_card_eligible_carries_full_matrix(
    tmp_path,
):
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="mixed",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"AAA": artifact},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    card = vm["ticker_cards"][0]
    assert card["rank_eligible"] is True
    assert len(card["current_build_signals"]) == 60
    sumry = card["current_build_signal_summary"]
    assert sumry is not None
    # Mixed pattern: 1mo cells are "None" -> 12 cells.
    assert sumry["cells_currently_none"] == 12


def test_view_model_blocked_card_has_no_current_signals(
    tmp_path,
):
    blocked = _make_blocked_daily_only_artifact(
        ticker="BBB",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"BBB": blocked},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["BBB"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    card = vm["ticker_cards"][0]
    assert card["rank_eligible"] is False
    assert card["current_build_signals"] == []
    assert card["current_build_signal_summary"] is None


# ---------------------------------------------------------------------------
# Large-universe fixture (does NOT assume SPY).
# ---------------------------------------------------------------------------


def test_large_universe_fixture_does_not_assume_spy(
    tmp_path,
):
    """A six-ticker universe with no SPY -- proves the
    current-signal surface is not SPY-specialized."""
    artifact_map: dict[str, dict[str, Any]] = {
        "AAA": _make_full_60_cell_artifact(
            ticker="AAA", pattern="all_buy",
        ),
        "BBB": _make_full_60_cell_artifact(
            ticker="BBB", pattern="all_short",
        ),
        "CCC": _make_full_60_cell_artifact(
            ticker="CCC", pattern="mixed",
        ),
        "DDD": _make_full_60_cell_artifact(
            ticker="DDD", pattern="all_none",
        ),
        "EEE": _make_blocked_daily_only_artifact(
            ticker="EEE",
        ),
        "FFF": _make_blocked_daily_only_artifact(
            ticker="FFF",
        ),
    }

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, artifact_map, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_ALL_ARTIFACTS,
        underlying_export_callable=fake_export,
    )
    assert package["eligible_count"] == 4
    assert package["blocked_count"] == 2
    # AAA + BBB both all-windows-firing-with-aligned-members
    # -> rank above CCC and DDD.
    rrow_tickers = [
        r["ticker"] for r in package["ranking_rows"]
    ]
    assert rrow_tickers[:2] == sorted(rrow_tickers[:2])
    # All eligible cards carry full matrices.
    for t in ("AAA", "BBB", "CCC", "DDD"):
        d = package["ticker_details"][t]
        assert d["rank_eligible"] is True
        assert len(d["current_build_signals"]) == 60
    # Blocked cards do NOT.
    for t in ("EEE", "FFF"):
        d = package["ticker_details"][t]
        assert d["rank_eligible"] is False
        assert d["current_build_signals"] == []
        assert d["current_build_signal_summary"] is None
    # View model also wired.
    vm = _crv.build_view_model(package)
    assert len(vm["ranking_table"]) == 4
    assert len(vm["blocked_table"]) == 2


# ---------------------------------------------------------------------------
# Phase 6I-37 amendment-1: same-K cross-window semantics.
# ---------------------------------------------------------------------------


def _make_different_k_per_window_artifact() -> dict[str, Any]:
    """Each canonical window has a current Buy/Short signal,
    but on a DIFFERENT K value. The any-K loose predicate is
    True; the strict same-K predicate must be False.

    Layout:
      * 1d  -- K=1  Buy
      * 1wk -- K=4  Buy
      * 1mo -- K=7  Short
      * 3mo -- K=2  Buy
      * 1y  -- K=12 Buy
    Every other cell is "None" with zero alignment.
    """
    windows = ("1d", "1wk", "1mo", "3mo", "1y")
    Ks = tuple(range(1, 13))
    signaling_map: dict[str, tuple[int, str]] = {
        "1d": (1, "Buy"),
        "1wk": (4, "Buy"),
        "1mo": (7, "Short"),
        "3mo": (2, "Buy"),
        "1y": (12, "Buy"),
    }
    cells: list[dict[str, Any]] = []
    for w in windows:
        sig_K, sig_dir = signaling_map[w]
        for K in Ks:
            if K == sig_K:
                cells.append(
                    _make_cell(
                        K=K, window=w,
                        latest_combined_signal=sig_dir,
                        latest_buy_count=(
                            3 if sig_dir == "Buy" else 0
                        ),
                        latest_short_count=(
                            3 if sig_dir == "Short" else 0
                        ),
                        latest_none_count=0,
                        member_count=3,
                        total_capture_pct=10.0,
                    )
                )
            else:
                cells.append(
                    _make_cell(
                        K=K, window=w,
                        latest_combined_signal="None",
                        latest_buy_count=0,
                        latest_short_count=0,
                        latest_none_count=3,
                        member_count=3,
                        trigger_days=0,
                        total_capture_pct=0.0,
                    )
                )
    bwwa = {
        w: {
            "all_members_firing": False,
            "firing_member_count": 0,
            "total_member_count": 3,
        }
        for w in windows
    }
    return {
        "target_ticker": "AAA",
        "generated_at": "2026-05-14T00:00:00+00:00",
        "per_window_k_metrics": cells,
        "build_wide_window_alignment": bwwa,
        "multiwindow_k_engine_payload_metadata": {
            "schema_version": "v1",
        },
        "timeframes": list(windows),
        "daily": {
            "dates": ["2026-05-14"],
            "close": [100.0],
            "last_date": "2026-05-14",
        },
        "chart_rows": [
            {"date": "2026-05-14", "close": 100.0},
        ],
    }


def _make_same_K_artifact(
    *,
    K_target: int,
    direction: str,
    aligned_count: int,
    member_count: int = 3,
) -> dict[str, Any]:
    """Every canonical window has the SAME K=K_target firing
    Buy/Short with ``aligned_count``-of-``member_count``
    members aligned. Other K values in every window are
    "None"."""
    windows = ("1d", "1wk", "1mo", "3mo", "1y")
    Ks = tuple(range(1, 13))
    cells: list[dict[str, Any]] = []
    for w in windows:
        for K in Ks:
            if K == K_target:
                cells.append(
                    _make_cell(
                        K=K, window=w,
                        latest_combined_signal=direction,
                        latest_buy_count=(
                            aligned_count
                            if direction == "Buy" else 0
                        ),
                        latest_short_count=(
                            aligned_count
                            if direction == "Short" else 0
                        ),
                        latest_none_count=(
                            member_count - aligned_count
                        ),
                        member_count=member_count,
                        total_capture_pct=10.0,
                    )
                )
            else:
                cells.append(
                    _make_cell(
                        K=K, window=w,
                        latest_combined_signal="None",
                        latest_buy_count=0,
                        latest_short_count=0,
                        latest_none_count=member_count,
                        member_count=member_count,
                        trigger_days=0,
                        total_capture_pct=0.0,
                    )
                )
    bwwa = {
        w: {
            "all_members_firing": (
                aligned_count == member_count
            ),
            "firing_member_count": aligned_count,
            "total_member_count": member_count,
        }
        for w in windows
    }
    return {
        "target_ticker": "AAA",
        "generated_at": "2026-05-14T00:00:00+00:00",
        "per_window_k_metrics": cells,
        "build_wide_window_alignment": bwwa,
        "multiwindow_k_engine_payload_metadata": {
            "schema_version": "v1",
        },
        "timeframes": list(windows),
        "daily": {
            "dates": ["2026-05-14"],
            "close": [100.0],
            "last_date": "2026-05-14",
        },
        "chart_rows": [
            {"date": "2026-05-14", "close": 100.0},
        ],
    }


def test_amendment1_different_k_per_window_loose_true_strict_false():
    """Codex audit fixture: each window has a current
    Buy/Short signal but on a different K. The loose any-K
    predicate is True; the strict same-K predicate is False
    and the same-K list is empty."""
    artifact = _make_different_k_per_window_artifact()
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    sumry = _cmre._build_current_signal_summary(
        matrix,
        bwwa=artifact["build_wide_window_alignment"],
    )
    # Any-K loose: every window has some current signal.
    assert (
        sumry["all_windows_have_any_current_signal"]
        is True
    )
    assert set(
        sumry["windows_with_any_currently_signaling"]
    ) == {"1d", "1wk", "1mo", "3mo", "1y"}
    # Same-K strict: NO single K fires across all five
    # windows.
    assert (
        sumry[
            "all_five_windows_same_k_currently_signaling"
        ]
        is False
    )
    assert (
        sumry["k_builds_currently_signaling_all_windows"]
        == []
    )
    assert (
        sumry[
            "all_five_windows_same_k_all_members_aligned"
        ]
        is False
    )
    assert (
        sumry["k_builds_all_members_aligned_all_windows"]
        == []
    )
    # No same-K strongest pick.
    assert (
        sumry["strongest_cross_window_k_build"] is None
    )


def test_amendment1_same_K6_all_windows_aligned():
    """K=6 is the same currently-signaling Buy K across all
    five windows AND all members are aligned. The same-K
    lists both include 6 and both same-K booleans are True."""
    artifact = _make_same_K_artifact(
        K_target=6, direction="Buy", aligned_count=3,
        member_count=3,
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    sumry = _cmre._build_current_signal_summary(
        matrix,
        bwwa=artifact["build_wide_window_alignment"],
    )
    assert (
        sumry["k_builds_currently_signaling_all_windows"]
        == [6]
    )
    assert (
        sumry["k_builds_all_members_aligned_all_windows"]
        == [6]
    )
    assert (
        sumry[
            "all_five_windows_same_k_currently_signaling"
        ]
        is True
    )
    assert (
        sumry[
            "all_five_windows_same_k_all_members_aligned"
        ]
        is True
    )
    cross = sumry["strongest_cross_window_k_build"]
    assert cross is not None
    assert cross["K"] == 6
    # 5 windows * 10.0 capture each = 50.0.
    assert cross["total_capture_pct_sum"] == 50.0
    assert cross["buy_window_count"] == 5
    assert cross["short_window_count"] == 0
    assert cross["all_members_aligned_window_count"] == 5


def test_amendment1_same_K6_signaling_but_partial_alignment():
    """K=6 fires Buy in every window but only 2-of-3
    members agree. The currently-signaling same-K list
    includes 6; the all-members-aligned same-K list does
    NOT."""
    artifact = _make_same_K_artifact(
        K_target=6, direction="Buy", aligned_count=2,
        member_count=3,
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    sumry = _cmre._build_current_signal_summary(
        matrix,
        bwwa=artifact["build_wide_window_alignment"],
    )
    assert (
        sumry["k_builds_currently_signaling_all_windows"]
        == [6]
    )
    assert (
        sumry["k_builds_all_members_aligned_all_windows"]
        == []
    )
    assert (
        sumry[
            "all_five_windows_same_k_currently_signaling"
        ]
        is True
    )
    assert (
        sumry[
            "all_five_windows_same_k_all_members_aligned"
        ]
        is False
    )
    cross = sumry["strongest_cross_window_k_build"]
    assert cross is not None
    assert cross["K"] == 6
    assert cross["all_members_aligned_window_count"] == 0


def test_amendment1_per_cell_naming_clarity():
    """The per-cell schema MUST expose the unambiguous
    name ``historically_fired`` (HISTORICAL: trigger_days > 0)
    and MUST expose the current-state predicate as
    ``currently_signaling`` plus the ``currently_firing``
    alias. The ambiguous bare ``firing`` name is gone."""
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    matrix = _cmre._build_current_signal_matrix(
        artifact["per_window_k_metrics"], ticker="AAA",
    )
    for row in matrix:
        assert "historically_fired" in row
        assert "currently_signaling" in row
        assert "currently_firing" in row
        assert "firing" not in row
        # In the all-Buy fixture, every cell is currently
        # signaling AND has historically fired.
        assert row["currently_signaling"] is True
        assert row["currently_firing"] is True
        assert row["historically_fired"] is True
        # The two current-state spellings must agree.
        assert (
            row["currently_signaling"]
            == row["currently_firing"]
        )


def test_amendment1_package_carries_same_K_fields_through(
    tmp_path,
):
    """Phase 6I-35 package's ranking_rows AND ticker_details
    must surface the new strict same-K fields."""
    artifact = _make_same_K_artifact(
        K_target=6, direction="Buy", aligned_count=3,
        member_count=3,
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"AAA": artifact},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    rrow_summary = package["ranking_rows"][0][
        "current_build_signal_summary"
    ]
    assert rrow_summary is not None
    assert (
        rrow_summary[
            "k_builds_currently_signaling_all_windows"
        ]
        == [6]
    )
    assert (
        rrow_summary[
            "all_five_windows_same_k_currently_signaling"
        ]
        is True
    )
    assert (
        rrow_summary["strongest_cross_window_k_build"][
            "K"
        ]
        == 6
    )
    detail = package["ticker_details"]["AAA"]
    detail_summary = detail["current_build_signal_summary"]
    assert (
        detail_summary[
            "k_builds_all_members_aligned_all_windows"
        ]
        == [6]
    )


def test_amendment1_view_model_carries_same_K_fields_through(
    tmp_path,
):
    """Phase 6I-36 reader/view's compact ranking-table
    summary AND ticker card summary must surface the new
    strict same-K fields, including the
    ``strongest_cross_window_k_build`` payload."""
    artifact = _make_same_K_artifact(
        K_target=6, direction="Buy", aligned_count=3,
        member_count=3,
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"AAA": artifact},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    rt = vm["ranking_table"][0]
    cs = rt["current_signal_summary"]
    assert cs is not None
    # Strict same-K predicate exposed on the compact
    # ranking-table summary.
    assert (
        cs["k_builds_currently_signaling_all_windows"]
        == [6]
    )
    assert (
        cs["k_builds_all_members_aligned_all_windows"]
        == [6]
    )
    assert (
        cs["all_five_windows_same_k_currently_signaling"]
        is True
    )
    assert (
        cs["all_five_windows_same_k_all_members_aligned"]
        is True
    )
    assert (
        cs["strongest_cross_window_k_build"]["K"] == 6
    )
    # Loose any-K predicate also still exposed.
    assert (
        cs["all_windows_have_any_current_signal"] is True
    )
    # Ticker card carries the full summary block too.
    card = vm["ticker_cards"][0]
    card_summary = card["current_build_signal_summary"]
    assert (
        card_summary[
            "k_builds_currently_signaling_all_windows"
        ]
        == [6]
    )


def test_amendment1_blocked_ticker_still_has_null_summary_under_strict_predicate(
    tmp_path,
):
    """Blocked daily-only tickers MUST NOT receive
    fabricated same-K fields. The whole summary stays
    null on the package detail and on the view-model
    card."""
    blocked = _make_blocked_daily_only_artifact(
        ticker="BBB",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"BBB": blocked},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["BBB"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    detail = package["ticker_details"]["BBB"]
    assert detail["rank_eligible"] is False
    assert detail["current_build_signals"] == []
    assert detail["current_build_signal_summary"] is None
    vm = _crv.build_view_model(package)
    card = vm["ticker_cards"][0]
    assert card["rank_eligible"] is False
    assert card["current_build_signals"] == []
    assert card["current_build_signal_summary"] is None
    # The ranking table is empty (the ticker is blocked,
    # not eligible).
    assert vm["ranking_table"] == []


# ---------------------------------------------------------------------------
# Strict Phase 6I-20 validation is preserved.
# ---------------------------------------------------------------------------


def test_duplicate_canonical_cells_still_block_eligibility(
    tmp_path,
):
    """A duplicate canonical (K, window) cell still
    classifies the row as blocked with the
    ``incomplete_60_cell_grid`` reason."""
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    # Add a duplicate canonical cell.
    artifact["per_window_k_metrics"].append(
        _make_cell(K=1, window="1d"),
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    assert report.eligible_count == 0
    blk = report.blocked_rows[0]
    assert (
        blk.ranking_blocked_reason
        == "incomplete_60_cell_grid"
    )
    assert blk.current_build_signals == ()


def test_missing_canonical_cell_still_blocks_eligibility(
    tmp_path,
):
    """An artifact with only 59 canonical cells classifies
    as blocked."""
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", pattern="all_buy",
    )
    artifact["per_window_k_metrics"].pop()  # drop one cell
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    assert report.eligible_count == 0
    blk = report.blocked_rows[0]
    assert (
        blk.ranking_blocked_reason
        == "incomplete_60_cell_grid"
    )
    assert blk.current_build_signals == ()


# ---------------------------------------------------------------------------
# Static guards on the new code.
# ---------------------------------------------------------------------------


def test_cmre_no_forbidden_top_level_imports():
    """Phase 6I-37 must not introduce forbidden imports in
    the ranking-export module."""
    src = Path(_cmre.__file__).read_text(encoding="utf-8")
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
    assert not bad, f"forbidden top-level imports: {bad!r}"
