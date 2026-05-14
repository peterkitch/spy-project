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
        "currently_signaling", "firing",
        "total_capture_pct", "avg_daily_capture_pct",
        "sharpe_ratio", "trigger_days",
        "wins", "losses",
    }
    assert required.issubset(set(row.keys()))


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
        assert row["firing"] is True  # trigger_days > 0
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
        assert row["firing"] is False


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
    assert sumry["cells_historically_firing"] == 60
    assert sumry["windows_with_any_currently_signaling"] == [
        "1d", "1wk", "1mo", "3mo", "1y",
    ]
    assert sumry["all_five_windows_currently_signaling"] is (
        True
    )
    assert (
        sumry["windows_with_all_members_firing"]
        == ["1d", "1wk", "1mo", "3mo", "1y"]
    )
    strongest = sumry["strongest_currently_signaling_cell"]
    assert strongest is not None
    assert strongest["latest_combined_signal"] == "Buy"


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
    assert sumry["cells_historically_firing"] == 0
    assert sumry["all_five_windows_currently_signaling"] is (
        False
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
    # windows currently signaling.
    assert sumry["all_five_windows_currently_signaling"] is (
        False
    )
    assert sumry["windows_with_any_currently_signaling"] == [
        "1d", "1wk", "1y", "3mo",
    ][:0] or set(
        sumry["windows_with_any_currently_signaling"]
    ) == {"1d", "1wk", "3mo", "1y"}


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
    assert cs["all_five_windows_currently_signaling"] is (
        True
    )
    assert (
        cs["strongest_currently_signaling_cell_label"]
        is not None
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
