"""Phase 6I-39 tests for the one-row-per-ticker primary
build selector.

Pins the contract that a ticker NEVER explodes into
multiple ranking rows / ticker cards just because it has
multiple K builds active. Multiple active K builds are
surfaced via the primary-build summary's
``other_active_k_builds`` field on the SAME single row.

Selection rule pinned by these tests:

  1. Tier 1 (preferred): ``same_k_all_windows_same_direction``
     — pick the strongest K whose every canonical window
     shows the SAME ``latest_combined_signal`` direction.
  2. Tier 2 (lower confidence):
     ``same_k_all_windows_mixed_direction`` — pick the
     strongest K whose every canonical window currently
     signals (Buy or Short) but with mixed directions.
     ``direction_conflict=True``.
  3. Tier 3 (fallback): ``strongest_current_cell`` — single
     (K, window) cell with highest current capture.
  4. Tier 4: ``none`` — ``primary_build_available=False``.

"Strongest" cascade for tiers 1/2:
``total_capture_pct_sum`` DESC -> ``avg_sharpe_ratio`` DESC
-> ``trigger_days_sum`` DESC -> K ASC.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_multiwindow_ranking_export as _cmre  # noqa: E402
import confluence_website_export_package as _cwep  # noqa: E402
import confluence_website_reader_view as _crv  # noqa: E402


CANONICAL_WINDOWS = _cmre.CANONICAL_WINDOWS
CANONICAL_K_VALUES = _cmre.CANONICAL_K_VALUES


# ---------------------------------------------------------------------------
# Cell + artifact fixture helpers
# ---------------------------------------------------------------------------


def _cell(
    *,
    K: int,
    window: str,
    direction: str = "Buy",
    member_count: int = 3,
    aligned_count: int | None = None,
    total_capture_pct: float = 10.0,
    sharpe_ratio: float = 1.0,
    trigger_days: int = 20,
) -> dict[str, Any]:
    if direction == "Buy":
        latest_buy = (
            aligned_count
            if aligned_count is not None
            else member_count
        )
        latest_short = 0
        latest_none = member_count - latest_buy
    elif direction == "Short":
        latest_short = (
            aligned_count
            if aligned_count is not None
            else member_count
        )
        latest_buy = 0
        latest_none = member_count - latest_short
    elif direction == "None":
        latest_buy = 0
        latest_short = 0
        latest_none = member_count
    else:
        raise ValueError(
            f"unknown direction {direction!r}"
        )
    return {
        "K": K,
        "window": window,
        "latest_combined_signal": direction,
        "latest_buy_count": int(latest_buy),
        "latest_short_count": int(latest_short),
        "latest_none_count": int(latest_none),
        "latest_missing_count": 0,
        "member_count": int(member_count),
        "total_capture_pct": float(total_capture_pct),
        "avg_daily_capture_pct": 0.5,
        "sharpe_ratio": float(sharpe_ratio),
        "trigger_days": int(trigger_days),
        "wins": int(trigger_days // 2),
        "losses": int(trigger_days // 2),
    }


def _none_cell(*, K: int, window: str) -> dict[str, Any]:
    return _cell(
        K=K, window=window, direction="None",
        trigger_days=0, total_capture_pct=0.0,
        sharpe_ratio=0.0,
    )


def _bwwa_all_firing() -> dict[str, dict[str, Any]]:
    return {
        w: {
            "all_members_firing": True,
            "firing_member_count": 3,
            "total_member_count": 3,
        }
        for w in CANONICAL_WINDOWS
    }


def _wrap_artifact(
    *, ticker: str, cells: Iterable[dict[str, Any]],
    bwwa: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "target_ticker": ticker,
        "generated_at": "2026-05-14T00:00:00+00:00",
        "per_window_k_metrics": list(cells),
        "build_wide_window_alignment": (
            bwwa if bwwa is not None else _bwwa_all_firing()
        ),
        "multiwindow_k_engine_payload_metadata": {
            "schema_version": "v1",
        },
        "timeframes": list(CANONICAL_WINDOWS),
        "daily": {
            "dates": ["2026-05-14"],
            "close": [100.0],
            "last_date": "2026-05-14",
        },
        "chart_rows": [
            {"date": "2026-05-14", "close": 100.0},
        ],
    }


def _make_artifact_same_K_all_windows(
    *,
    ticker: str,
    K_target: int,
    direction: str = "Buy",
    aligned_count: int = 3,
    member_count: int = 3,
    target_capture_pct: float = 10.0,
    other_K_default_total_capture: float = 1.0,
) -> dict[str, Any]:
    """Every canonical window has the same K=K_target firing
    ``direction``; every other (K, window) is None."""
    cells: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        for K in CANONICAL_K_VALUES:
            if K == K_target:
                cells.append(
                    _cell(
                        K=K, window=w,
                        direction=direction,
                        aligned_count=aligned_count,
                        member_count=member_count,
                        total_capture_pct=(
                            target_capture_pct
                        ),
                    )
                )
            else:
                # Other K cells are "None" so they don't
                # qualify as current signals. We still
                # carry historical capture so the cell row
                # is well-formed.
                cells.append(
                    _none_cell(K=K, window=w)
                )
    return _wrap_artifact(
        ticker=ticker, cells=cells,
    )


def _make_artifact_multiple_same_K_all_windows_same_dir(
    *,
    ticker: str,
    K_set: Iterable[int],
    direction: str = "Buy",
    capture_per_K: dict[int, float] | None = None,
) -> dict[str, Any]:
    """Multiple K values each fire ``direction`` in every
    canonical window. Used to test the "strongest" pick
    inside tier 1."""
    K_set = list(K_set)
    capture_per_K = capture_per_K or {
        K: 10.0 for K in K_set
    }
    cells: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        for K in CANONICAL_K_VALUES:
            if K in K_set:
                cells.append(
                    _cell(
                        K=K, window=w,
                        direction=direction,
                        total_capture_pct=(
                            capture_per_K[K]
                        ),
                    )
                )
            else:
                cells.append(
                    _none_cell(K=K, window=w)
                )
    return _wrap_artifact(
        ticker=ticker, cells=cells,
    )


def _make_artifact_same_K_mixed_directions(
    *,
    ticker: str,
    K_target: int = 6,
    buy_windows: Iterable[str] = ("1d", "1wk", "1mo"),
    short_windows: Iterable[str] = ("3mo", "1y"),
) -> dict[str, Any]:
    """The same K=K_target currently signals in every
    canonical window but with MIXED Buy/Short directions
    (the configured split). Other K values are None."""
    buy_set = set(buy_windows)
    short_set = set(short_windows)
    assert buy_set | short_set == set(CANONICAL_WINDOWS), (
        "fixture: every canonical window must be either "
        "buy or short"
    )
    cells: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        for K in CANONICAL_K_VALUES:
            if K == K_target:
                if w in buy_set:
                    cells.append(
                        _cell(
                            K=K, window=w,
                            direction="Buy",
                        )
                    )
                else:
                    cells.append(
                        _cell(
                            K=K, window=w,
                            direction="Short",
                        )
                    )
            else:
                cells.append(
                    _none_cell(K=K, window=w)
                )
    return _wrap_artifact(
        ticker=ticker, cells=cells,
    )


def _make_artifact_different_K_per_window() -> dict[str, Any]:
    """Each window has a different K firing -- no single K
    fires in every window. The fallback to
    ``strongest_current_cell`` must engage."""
    signaling_map = {
        "1d": (1, "Buy", 8.0),
        "1wk": (4, "Buy", 12.0),
        "1mo": (7, "Short", 6.0),
        "3mo": (2, "Buy", 5.0),
        "1y": (12, "Buy", 18.0),
    }
    cells: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        sig_K, sig_dir, sig_cap = signaling_map[w]
        for K in CANONICAL_K_VALUES:
            if K == sig_K:
                cells.append(
                    _cell(
                        K=K, window=w,
                        direction=sig_dir,
                        total_capture_pct=sig_cap,
                    )
                )
            else:
                cells.append(
                    _none_cell(K=K, window=w)
                )
    return _wrap_artifact(
        ticker="AAA", cells=cells,
        bwwa={
            w: {
                "all_members_firing": False,
                "firing_member_count": 0,
                "total_member_count": 3,
            }
            for w in CANONICAL_WINDOWS
        },
    )


def _make_artifact_blocked_daily_only() -> dict[str, Any]:
    return {
        "target_ticker": "BLK",
        "generated_at": "2026-05-14T00:00:00+00:00",
        "timeframes": ["1d"],
        "summary": {"some_legacy_field": True},
        "daily": {
            "dates": ["2026-05-14"],
            "last_date": "2026-05-14",
        },
    }


def _injected_loader(
    artifact_map: dict[str, dict[str, Any]],
):
    def loader(path):
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


# ---------------------------------------------------------------------------
# 1. one-ticker-one-row contract
# ---------------------------------------------------------------------------


def test_ticker_with_many_active_k_builds_still_produces_one_row(
    tmp_path,
):
    """A ticker with MULTIPLE same-K-all-window builds must
    still produce exactly ONE ranking row, ONE blocked row
    if blocked, and ONE ticker card on the website surface.
    """
    # Eight active K builds (K=1..8), each Buy across all
    # five canonical windows. Different capture so the
    # selector can pick a primary cleanly.
    artifact = _make_artifact_multiple_same_K_all_windows_same_dir(
        ticker="AAA",
        K_set=list(range(1, 9)),
        direction="Buy",
        capture_per_K={
            K: 10.0 + K for K in range(1, 9)
        },
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"AAA": artifact}, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    assert package["eligible_count"] == 1
    assert len(package["ranking_rows"]) == 1
    assert (
        len(package["ranking_rows"]) == 1
        and package["ranking_rows"][0]["ticker"] == "AAA"
    )
    assert len(package["ticker_details"]) == 1
    # No ranking row exploded by K -- exactly one card.
    vm = _crv.build_view_model(package)
    assert len(vm["ranking_table"]) == 1
    assert vm["ranking_table"][0]["ticker"] == "AAA"
    assert len(vm["ticker_cards"]) == 1
    # The package envelope advertises the display
    # contract.
    assert package["display_row_cardinality"] == (
        "one_row_per_ticker"
    )
    assert vm["display_row_cardinality"] == (
        "one_row_per_ticker"
    )


def test_ranking_export_summary_advertises_one_row_per_ticker(
    tmp_path,
):
    artifact = _make_artifact_same_K_all_windows(
        ticker="AAA", K_target=6, direction="Buy",
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    j = report.to_json_dict()
    assert j["summary"]["display_row_cardinality"] == (
        "one_row_per_ticker"
    )


# ---------------------------------------------------------------------------
# 2. Tier 1: same_k_all_windows_same_direction
# ---------------------------------------------------------------------------


def test_same_K_buy_all_windows_selects_tier1(
    tmp_path,
):
    artifact = _make_artifact_same_K_all_windows(
        ticker="AAA", K_target=6, direction="Buy",
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    elig = report.ranking_rows[0]
    pbs = elig.primary_build_summary
    assert pbs is not None
    assert pbs["primary_build_available"] is True
    assert pbs["selection_tier"] == (
        "same_k_all_windows_same_direction"
    )
    assert pbs["K"] == 6
    assert pbs["signal_direction"] == "Buy"
    assert pbs["windows_signaling_count"] == 5
    assert pbs["buy_window_count"] == 5
    assert pbs["short_window_count"] == 0
    assert pbs["direction_conflict"] is False
    assert pbs["explanation"] == (
        "all_windows_same_direction"
    )
    assert pbs["same_direction_k_builds_all_windows"] == [
        6,
    ]
    assert pbs["mixed_direction_k_builds_all_windows"] == [
    ]
    # 5 windows * 10 capture each = 50.0 sum.
    assert pbs["total_capture_pct_sum"] == 50.0
    assert pbs["other_active_k_builds"] == []
    assert pbs["display_row_cardinality"] == (
        "one_row_per_ticker"
    )


def test_tier1_strongest_pick_within_set(tmp_path):
    """Multiple same-K-all-window same-direction builds:
    pick the K with highest total_capture_pct_sum."""
    capture_per_K = {1: 5.0, 4: 15.0, 7: 10.0, 12: 12.0}
    artifact = _make_artifact_multiple_same_K_all_windows_same_dir(
        ticker="AAA",
        K_set=list(capture_per_K.keys()),
        direction="Buy",
        capture_per_K=capture_per_K,
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    pbs = report.ranking_rows[0].primary_build_summary
    assert pbs is not None
    # K=4 has the highest per-window capture (15.0 each),
    # so it wins the strongest pick.
    assert pbs["K"] == 4
    assert pbs["selection_tier"] == (
        "same_k_all_windows_same_direction"
    )
    # The other 3 K values appear in
    # other_active_k_builds (sorted ascending).
    other = pbs["other_active_k_builds"]
    other_Ks = [o["K"] for o in other]
    assert other_Ks == [1, 7, 12]


def test_tier1_short_direction_picked_when_all_windows_short(
    tmp_path,
):
    artifact = _make_artifact_same_K_all_windows(
        ticker="AAA", K_target=3, direction="Short",
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    pbs = report.ranking_rows[0].primary_build_summary
    assert pbs["signal_direction"] == "Short"
    assert pbs["short_window_count"] == 5
    assert pbs["buy_window_count"] == 0
    assert pbs["selection_tier"] == (
        "same_k_all_windows_same_direction"
    )


# ---------------------------------------------------------------------------
# 3. Tier 2: same_k_all_windows_mixed_direction
# ---------------------------------------------------------------------------


def test_same_K_mixed_direction_selects_tier2(tmp_path):
    """Same K=6 currently signals in every canonical window
    but with mixed Buy/Short directions -> tier 2 with
    direction_conflict=True."""
    artifact = _make_artifact_same_K_mixed_directions(
        ticker="AAA",
        K_target=6,
        buy_windows=("1d", "1wk", "1mo"),
        short_windows=("3mo", "1y"),
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    pbs = report.ranking_rows[0].primary_build_summary
    assert pbs["primary_build_available"] is True
    assert pbs["selection_tier"] == (
        "same_k_all_windows_mixed_direction"
    )
    assert pbs["K"] == 6
    assert pbs["signal_direction"] == "Mixed"
    assert pbs["direction_conflict"] is True
    assert pbs["windows_signaling_count"] == 5
    assert pbs["buy_window_count"] == 3
    assert pbs["short_window_count"] == 2
    assert pbs["explanation"] == (
        "all_windows_mixed_direction"
    )
    assert pbs["same_direction_k_builds_all_windows"] == [
    ]
    assert pbs["mixed_direction_k_builds_all_windows"] == [
        6,
    ]


# ---------------------------------------------------------------------------
# 4. Tier 3: strongest_current_cell fallback
# ---------------------------------------------------------------------------


def test_different_K_per_window_falls_back_to_tier3(
    tmp_path,
):
    """No single K fires in every window -> fallback to the
    strongest currently-signaling cell."""
    artifact = _make_artifact_different_K_per_window()
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    pbs = report.ranking_rows[0].primary_build_summary
    assert pbs is not None
    assert pbs["selection_tier"] == (
        "strongest_current_cell"
    )
    # The strongest currently-signaling cell is K=12 / 1y
    # / Buy / capture=18.0.
    assert pbs["K"] == 12
    assert pbs["signal_direction"] == "Buy"
    assert pbs["strongest_cell_window"] == "1y"
    assert pbs["windows_signaling_count"] == 1
    assert pbs["windows_signaling"] == ["1y"]
    assert pbs["total_capture_pct_sum"] == 18.0
    assert pbs["direction_conflict"] is False
    assert pbs["explanation"] == "single_cell_fallback"
    assert pbs["same_direction_k_builds_all_windows"] == [
    ]
    assert pbs["mixed_direction_k_builds_all_windows"] == [
    ]
    # other_active_k_builds lists OTHER non-primary K
    # values that are currently signaling somewhere
    # (excluding K=12 itself).
    other_Ks = [
        o["K"] for o in pbs["other_active_k_builds"]
    ]
    assert sorted(other_Ks) == [1, 2, 4, 7]


# ---------------------------------------------------------------------------
# 5. Tier 4: no current signal
# ---------------------------------------------------------------------------


def test_all_none_pattern_primary_build_not_available(
    tmp_path,
):
    """If no cell currently signals, the primary build is
    not available."""
    cells: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        for K in CANONICAL_K_VALUES:
            cells.append(_none_cell(K=K, window=w))
    artifact = _wrap_artifact(
        ticker="AAA", cells=cells,
        bwwa={
            w: {
                "all_members_firing": False,
                "firing_member_count": 0,
                "total_member_count": 3,
            }
            for w in CANONICAL_WINDOWS
        },
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    pbs = report.ranking_rows[0].primary_build_summary
    assert pbs["primary_build_available"] is False
    assert pbs["selection_tier"] == "none"
    assert pbs["K"] is None
    assert pbs["signal_direction"] is None
    assert pbs["explanation"] == "no_current_signal"
    assert pbs["other_active_k_builds"] == []


# ---------------------------------------------------------------------------
# 6. Multiple active K builds expose other_active_k_builds
# ---------------------------------------------------------------------------


def test_multiple_active_k_builds_listed_in_other_active(
    tmp_path,
):
    """Three same-K-all-window same-direction builds (K=2,
    K=5, K=8): the strongest is the primary; the other two
    appear under ``other_active_k_builds`` with their own
    direction / window-count summary."""
    capture_per_K = {2: 10.0, 5: 20.0, 8: 14.0}
    artifact = _make_artifact_multiple_same_K_all_windows_same_dir(
        ticker="AAA",
        K_set=[2, 5, 8],
        direction="Buy",
        capture_per_K=capture_per_K,
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    pbs = report.ranking_rows[0].primary_build_summary
    # K=5 wins (highest per-window capture).
    assert pbs["K"] == 5
    other = pbs["other_active_k_builds"]
    other_dict = {o["K"]: o for o in other}
    assert set(other_dict.keys()) == {2, 8}
    for K in (2, 8):
        o = other_dict[K]
        assert o["signal_direction"] == "Buy"
        assert o["windows_signaling_count"] == 5
        assert o["buy_window_count"] == 5
        assert o["short_window_count"] == 0
        # capture_per_K[K] * 5 windows
        assert o["total_capture_pct_sum"] == (
            capture_per_K[K] * 5
        )


# ---------------------------------------------------------------------------
# 7. Blocked / daily-only ticker -> no primary build
# ---------------------------------------------------------------------------


def test_blocked_daily_only_ticker_has_no_primary_build(
    tmp_path,
):
    blocked = _make_artifact_blocked_daily_only()
    report = _build_export(
        ["BLK"], {"BLK": blocked}, tmp_path=tmp_path,
    )
    assert report.eligible_count == 0
    assert report.blocked_count == 1
    blk = report.blocked_rows[0]
    assert blk.rank_eligible is False
    assert blk.primary_build_summary is None


def test_package_blocked_ticker_detail_has_null_primary_build(
    tmp_path,
):
    blocked = _make_artifact_blocked_daily_only()

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"BLK": blocked}, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["BLK"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    detail = package["ticker_details"]["BLK"]
    assert detail["rank_eligible"] is False
    assert detail["primary_build_summary"] is None
    vm = _crv.build_view_model(package)
    assert vm["ticker_cards"][0][
        "primary_build_summary"
    ] is None
    # The ranking_table is empty -- nothing to project.
    assert vm["ranking_table"] == []


# ---------------------------------------------------------------------------
# 8. Package + reader/view carry primary_build_summary through
# ---------------------------------------------------------------------------


def test_package_ranking_row_carries_primary_build_summary(
    tmp_path,
):
    artifact = _make_artifact_same_K_all_windows(
        ticker="AAA", K_target=6, direction="Buy",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"AAA": artifact}, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    pbs = package["ranking_rows"][0][
        "primary_build_summary"
    ]
    assert pbs is not None
    assert pbs["K"] == 6
    assert pbs["selection_tier"] == (
        "same_k_all_windows_same_direction"
    )
    # ticker_details also carries it.
    detail = package["ticker_details"]["AAA"]
    assert detail["primary_build_summary"] is not None
    assert detail["primary_build_summary"]["K"] == 6


def test_reader_view_ranking_table_carries_primary_build_compact(
    tmp_path,
):
    artifact = _make_artifact_same_K_all_windows(
        ticker="AAA", K_target=6, direction="Buy",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"AAA": artifact}, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    rt = vm["ranking_table"][0]
    pb = rt["primary_build"]
    assert pb is not None
    assert pb["primary_build_available"] is True
    assert pb["K"] == 6
    assert pb["signal_direction"] == "Buy"
    assert pb["windows_signaling_count"] == 5
    assert pb["selection_tier"] == (
        "same_k_all_windows_same_direction"
    )
    assert pb["direction_conflict"] is False
    assert pb["other_active_k_count"] == 0
    # Pre-formatted label visible to the renderer.
    assert pb["label"] is not None
    assert "K=6" in pb["label"]
    assert "Buy" in pb["label"]
    # The ticker card carries the full summary.
    card = vm["ticker_cards"][0]
    assert card["primary_build_summary"] is not None
    assert card["primary_build_summary"]["K"] == 6


def test_reader_view_primary_build_compact_handles_no_signal(
    tmp_path,
):
    cells: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        for K in CANONICAL_K_VALUES:
            cells.append(_none_cell(K=K, window=w))
    artifact = _wrap_artifact(
        ticker="AAA", cells=cells,
        bwwa={
            w: {
                "all_members_firing": False,
                "firing_member_count": 0,
                "total_member_count": 3,
            }
            for w in CANONICAL_WINDOWS
        },
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"AAA": artifact}, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    pb = vm["ranking_table"][0]["primary_build"]
    assert pb is not None
    assert pb["primary_build_available"] is False
    assert pb["K"] is None
    assert pb["label"] is None
    assert pb["explanation"] == "no_current_signal"


# ---------------------------------------------------------------------------
# 9. Existing matrix remains available for the detail drawer
# ---------------------------------------------------------------------------


def test_current_build_signals_matrix_still_available_alongside_primary(
    tmp_path,
):
    """The Phase 6I-37 60-cell matrix MUST remain available
    on the ticker_details / ticker_card for the detail
    drawer. Adding the primary build summary does not
    remove or override the matrix."""
    artifact = _make_artifact_same_K_all_windows(
        ticker="AAA", K_target=6, direction="Buy",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"AAA": artifact}, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    detail = package["ticker_details"]["AAA"]
    assert len(detail["current_build_signals"]) == 60
    assert detail["current_build_signal_summary"] is not None
    assert detail["primary_build_summary"] is not None
    vm = _crv.build_view_model(package)
    card = vm["ticker_cards"][0]
    assert len(card["current_build_signals"]) == 60
    assert card["current_build_signal_summary"] is not None
    assert card["primary_build_summary"] is not None


# ---------------------------------------------------------------------------
# 10. Top-level display_row_cardinality contract
# ---------------------------------------------------------------------------


def test_display_row_cardinality_constants_pinned():
    assert _cmre.DISPLAY_ROW_CARDINALITY == (
        "one_row_per_ticker"
    )
    assert _crv.DISPLAY_ROW_CARDINALITY == (
        "one_row_per_ticker"
    )


def test_view_model_error_path_still_advertises_display_contract():
    """Even on schema-error paths the view model should
    advertise the display contract so the renderer never
    sees the key missing."""
    err = _crv.build_error_view_model(
        error_code=_crv.ERROR_CODE_SCHEMA_MISMATCH,
        schema_version_seen="something_else",
    )
    assert err["display_row_cardinality"] == (
        "one_row_per_ticker"
    )


# ---------------------------------------------------------------------------
# 11. Multi-ticker fixture preserves one-row-per-ticker
# ---------------------------------------------------------------------------


def test_multi_ticker_each_gets_exactly_one_row_and_card(
    tmp_path,
):
    artifact_map: dict[str, dict[str, Any]] = {
        "AAA": _make_artifact_multiple_same_K_all_windows_same_dir(
            ticker="AAA",
            K_set=[2, 5, 8],
            direction="Buy",
            capture_per_K={2: 10.0, 5: 20.0, 8: 14.0},
        ),
        "BBB": _make_artifact_same_K_mixed_directions(
            ticker="BBB", K_target=6,
        ),
        "CCC": _make_artifact_different_K_per_window(),
        "DDD": _make_artifact_blocked_daily_only(),
    }
    # Build_export uses the ticker -> artifact map; CCC's
    # fixture was generated under ticker AAA but the loader
    # only checks by directory name, so we patch it.
    artifact_map["CCC"]["target_ticker"] = "CCC"

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, artifact_map, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA", "BBB", "CCC", "DDD"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_ALL_ARTIFACTS,
        underlying_export_callable=fake_export,
    )
    # Three eligible + one blocked -> three ranking rows
    # + one blocked row -> four ticker cards total.
    assert package["eligible_count"] == 3
    assert package["blocked_count"] == 1
    elig_tickers = [
        r["ticker"] for r in package["ranking_rows"]
    ]
    assert sorted(elig_tickers) == ["AAA", "BBB", "CCC"]
    # Each eligible ticker appears EXACTLY once.
    assert len(elig_tickers) == len(set(elig_tickers))
    # Tier selection per ticker.
    by_ticker = {
        r["ticker"]: r["primary_build_summary"]
        for r in package["ranking_rows"]
    }
    assert by_ticker["AAA"]["selection_tier"] == (
        "same_k_all_windows_same_direction"
    )
    assert by_ticker["BBB"]["selection_tier"] == (
        "same_k_all_windows_mixed_direction"
    )
    assert by_ticker["CCC"]["selection_tier"] == (
        "strongest_current_cell"
    )
    # View model: one row per eligible ticker, exactly one
    # card per inspected ticker.
    vm = _crv.build_view_model(package)
    rt_tickers = [r["ticker"] for r in vm["ranking_table"]]
    assert sorted(rt_tickers) == ["AAA", "BBB", "CCC"]
    assert len(rt_tickers) == len(set(rt_tickers))
    card_tickers = [c["ticker"] for c in vm["ticker_cards"]]
    assert sorted(card_tickers) == [
        "AAA", "BBB", "CCC", "DDD",
    ]
    assert len(card_tickers) == len(set(card_tickers))
