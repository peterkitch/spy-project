"""Phase 6I-40 tests: sortable leaderboard contract +
incomplete-member warnings + live-current signal status
surface + Spymaster-style flip-risk placeholders.

Pins the four contracts:

  1. Sort metadata at top level (sortable_columns,
     default_sort, sort_options) PLUS per-row numeric
     ``row_sort_values`` (total_capture_pct_sort,
     sharpe_ratio_sort, trigger_days_sort, rank_sort,
     ticker_sort). Ascending+descending options exist so
     bottom/negative rows can be brought to the top WITHOUT
     duplicating ticker rows.

  2. Incomplete-member warnings propagate to ranking row,
     ticker_details, package, and view model. Blocked
     tickers do NOT fabricate incomplete member details
     (status="blocked" instead).

  3. Latest-price / provisional current-signal status is
     "locked" on eligible rows by default (no live fetch),
     flips to "provisional" when a fake live_price_provider
     overlays data, and falls back to "blocked" on blocked
     rows.

  4. Spymaster-style flip-risk placeholder fields are
     present and null/false unless a fake flip_risk
     provider supplies data.

Multi-ticker fixtures throughout -- no SPY-only paths.
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
# Fixture helpers
# ---------------------------------------------------------------------------


def _cell(
    *,
    K: int,
    window: str,
    direction: str = "Buy",
    total_capture_pct: float = 10.0,
    sharpe_ratio: float = 1.0,
    trigger_days: int = 20,
    member_count: int = 3,
) -> dict[str, Any]:
    if direction == "Buy":
        buy_count = member_count
        short_count = 0
        none_count = 0
    elif direction == "Short":
        buy_count = 0
        short_count = member_count
        none_count = 0
    else:
        buy_count = 0
        short_count = 0
        none_count = member_count
    return {
        "K": K,
        "window": window,
        "latest_combined_signal": direction,
        "latest_buy_count": int(buy_count),
        "latest_short_count": int(short_count),
        "latest_none_count": int(none_count),
        "latest_missing_count": 0,
        "member_count": int(member_count),
        "total_capture_pct": float(total_capture_pct),
        "avg_daily_capture_pct": 0.5,
        "sharpe_ratio": float(sharpe_ratio),
        "trigger_days": int(trigger_days),
        "wins": int(trigger_days // 2),
        "losses": int(trigger_days // 2),
    }


def _bwwa_all_firing() -> dict[str, dict[str, Any]]:
    return {
        w: {
            "all_members_firing": True,
            "firing_member_count": 3,
            "total_member_count": 3,
        }
        for w in CANONICAL_WINDOWS
    }


def _make_full_60_cell_artifact(
    *,
    ticker: str,
    direction: str = "Buy",
    total_capture_pct: float = 10.0,
    sharpe_ratio: float = 1.0,
    trigger_days: int = 20,
) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        for K in CANONICAL_K_VALUES:
            cells.append(_cell(
                K=K, window=w,
                direction=direction,
                total_capture_pct=total_capture_pct,
                sharpe_ratio=sharpe_ratio,
                trigger_days=trigger_days,
            ))
    return {
        "target_ticker": ticker,
        "generated_at": "2026-05-14T00:00:00+00:00",
        "per_window_k_metrics": cells,
        "build_wide_window_alignment": _bwwa_all_firing(),
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


def _make_blocked_daily_only_artifact(
    *, ticker: str,
) -> dict[str, Any]:
    return {
        "target_ticker": ticker,
        "generated_at": "2026-05-14T00:00:00+00:00",
        "timeframes": ["1d"],
        "summary": {"legacy": True},
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


def _fake_chart(ticker, artifact, *, cache_dir=None):
    return {
        "chart_ready_available": True,
        "chart_ready_source": "confluence_artifact",
        "chart_row_count": 100,
        "chart_blocker": None,
    }


def _build_export(
    tickers, artifact_map, *, tmp_path,
    member_completeness_provider_callable=None,
    live_price_provider_callable=None,
    flip_risk_provider_callable=None,
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
        chart_readiness_callable=_fake_chart,
        member_completeness_provider_callable=(
            member_completeness_provider_callable
        ),
        live_price_provider_callable=(
            live_price_provider_callable
        ),
        flip_risk_provider_callable=(
            flip_risk_provider_callable
        ),
    )


# ---------------------------------------------------------------------------
# 1. Sortable leaderboard top-level metadata
# ---------------------------------------------------------------------------


def test_sortable_columns_default_sort_and_options_pinned(
    tmp_path,
):
    """The top-level sort metadata advertises Total Capture
    %, Sharpe Ratio, Trigger Days, Rank, Ticker; the default
    sort mirrors trafficflow.py:3111-3112; ascending +
    descending directions are available for each
    numeric/string column."""
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", direction="Buy",
        total_capture_pct=10.0, sharpe_ratio=1.0,
        trigger_days=20,
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    j = report.to_json_dict()
    s = j["summary"]
    assert s["sortable_columns"] == [
        "total_capture_pct",
        "sharpe_ratio",
        "trigger_days",
        "rank",
        "ticker",
    ]
    assert s["default_sort"] == [
        {"column_id": "sharpe_ratio", "direction": "desc"},
        {
            "column_id": "total_capture_pct",
            "direction": "desc",
        },
        {"column_id": "trigger_days", "direction": "desc"},
    ]
    opts_by_col = {
        o["column_id"]: o for o in s["sort_options"]
    }
    # Every numeric column exposes both directions.
    for col in (
        "total_capture_pct", "sharpe_ratio", "trigger_days",
    ):
        assert set(opts_by_col[col]["directions"]) == {
            "asc", "desc",
        }
        assert opts_by_col[col]["value_type"] == "number"
    # Rank + ticker also offer both directions.
    assert set(opts_by_col["rank"]["directions"]) == {
        "asc", "desc",
    }
    assert set(opts_by_col["ticker"]["directions"]) == {
        "asc", "desc",
    }
    # The per-column row_sort_value_key wiring is exposed
    # so the renderer knows which row field to read.
    assert opts_by_col[
        "total_capture_pct"
    ]["row_sort_value_key"] == "total_capture_pct_sort"
    assert opts_by_col[
        "sharpe_ratio"
    ]["row_sort_value_key"] == "sharpe_ratio_sort"


def test_package_and_view_model_expose_sort_metadata(
    tmp_path,
):
    artifact = _make_full_60_cell_artifact(ticker="AAA")

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
    assert "sortable_columns" in package
    assert package["sortable_columns"] == [
        "total_capture_pct",
        "sharpe_ratio",
        "trigger_days",
        "rank",
        "ticker",
    ]
    assert package["default_sort"][0]["column_id"] == (
        "sharpe_ratio"
    )
    vm = _crv.build_view_model(package)
    assert vm["sortable_columns"] == (
        package["sortable_columns"]
    )
    assert vm["default_sort"] == package["default_sort"]
    assert vm["sort_options"] == package["sort_options"]
    # Schema-error view model still carries the keys (empty
    # lists).
    err_vm = _crv.build_error_view_model(
        error_code=_crv.ERROR_CODE_SCHEMA_MISMATCH,
        schema_version_seen="other_v2",
    )
    assert err_vm["sortable_columns"] == []
    assert err_vm["default_sort"] == []
    assert err_vm["sort_options"] == []


# ---------------------------------------------------------------------------
# 2. Per-row numeric sort values
# ---------------------------------------------------------------------------


def test_row_sort_values_are_numeric_and_complete(
    tmp_path,
):
    artifact = _make_full_60_cell_artifact(
        ticker="AAA", direction="Buy",
        total_capture_pct=10.0, sharpe_ratio=1.0,
        trigger_days=20,
    )
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    j = report.to_json_dict()
    rsv = j["ranking_rows"][0]["row_sort_values"]
    # Total capture: 12 K * 5 windows * 10.0 = 600.0
    assert rsv["total_capture_pct_sort"] == 600.0
    # Sharpe avg across 60 cells (all 1.0) -> 1.0
    assert rsv["sharpe_ratio_sort"] == 1.0
    # Trigger days sum: 60 cells * 20 = 1200
    assert rsv["trigger_days_sort"] == 1200
    # Ranking export emits rank_sort=None (rank assigned
    # at the package layer).
    assert rsv["rank_sort"] is None
    assert rsv["ticker_sort"] == "AAA"


def test_package_assigns_rank_sort_per_row(tmp_path):
    """Package fills rank_sort using the assigned rank."""
    art_a = _make_full_60_cell_artifact(
        ticker="AAA", total_capture_pct=20.0,
    )
    art_b = _make_full_60_cell_artifact(
        ticker="BBB", total_capture_pct=10.0,
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"AAA": art_a, "BBB": art_b},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA", "BBB"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    rrows = package["ranking_rows"]
    by_ticker = {r["ticker"]: r for r in rrows}
    # AAA has higher capture -> rank 1.
    assert (
        by_ticker["AAA"]["row_sort_values"]["rank_sort"]
        == 1
    )
    assert (
        by_ticker["BBB"]["row_sort_values"]["rank_sort"]
        == 2
    )
    # Numeric sort values stable.
    assert by_ticker["AAA"][
        "row_sort_values"
    ]["total_capture_pct_sort"] == 12 * 5 * 20.0


def test_descending_sort_on_capture_brings_top_row_to_top():
    """Apply descending sort on total_capture_pct_sort and
    verify the row with higher capture is first."""
    rows = [
        {
            "ticker": "LOW",
            "row_sort_values": {
                "total_capture_pct_sort": 10.0,
                "sharpe_ratio_sort": 1.0,
                "trigger_days_sort": 100,
                "rank_sort": 2,
                "ticker_sort": "LOW",
            },
        },
        {
            "ticker": "HI",
            "row_sort_values": {
                "total_capture_pct_sort": 100.0,
                "sharpe_ratio_sort": 2.0,
                "trigger_days_sort": 500,
                "rank_sort": 1,
                "ticker_sort": "HI",
            },
        },
    ]
    sorted_desc = sorted(
        rows,
        key=lambda r: r["row_sort_values"][
            "total_capture_pct_sort"
        ],
        reverse=True,
    )
    assert sorted_desc[0]["ticker"] == "HI"
    sorted_asc = sorted(
        rows,
        key=lambda r: r["row_sort_values"][
            "total_capture_pct_sort"
        ],
    )
    assert sorted_asc[0]["ticker"] == "LOW"


def test_sort_does_not_duplicate_ticker_rows(tmp_path):
    """Sorting by ascending vs descending on the same
    column does NOT duplicate ticker rows in the table."""
    art_a = _make_full_60_cell_artifact(
        ticker="AAA", total_capture_pct=20.0,
    )
    art_b = _make_full_60_cell_artifact(
        ticker="BBB", total_capture_pct=10.0,
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers,
            {"AAA": art_a, "BBB": art_b},
            tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["AAA", "BBB"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    tickers = [r["ticker"] for r in vm["ranking_table"]]
    assert sorted(tickers) == ["AAA", "BBB"]
    assert len(tickers) == len(set(tickers))


# ---------------------------------------------------------------------------
# 3. Incomplete-member warning surface
# ---------------------------------------------------------------------------


def test_default_completeness_is_complete_when_eligible(
    tmp_path,
):
    """Production default: artifact has no member-level
    issue data -> has_incomplete_build_members=False and
    status=complete on an eligible row."""
    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    j = report.to_json_dict()
    cb = j["ranking_rows"][0]["data_completeness"]
    assert cb["has_incomplete_build_members"] is False
    assert cb["incomplete_member_count"] == 0
    assert cb["incomplete_members"] == []
    assert cb["data_completeness_status"] == "complete"
    assert cb["data_warning_symbol"] is None
    assert "complete" in cb["data_completeness_message"]


def test_provider_supplies_incomplete_members_propagates(
    tmp_path,
):
    """Fake provider reports TEF-style incomplete members
    on AAA -> status=partial, warning symbol set,
    incomplete_members list propagates through package +
    view model."""
    def provider(ticker, artifact=None):
        if ticker == "AAA":
            return {
                "has_incomplete_build_members": True,
                "incomplete_member_count": 1,
                "incomplete_members": ["TEF"],
                "incomplete_member_reasons": {
                    "TEF": (
                        "possibly_delisted_or_stale_pkl"
                    ),
                },
            }
        return {
            "has_incomplete_build_members": False,
            "incomplete_member_count": 0,
            "incomplete_members": [],
            "incomplete_member_reasons": {},
        }

    artifact = _make_full_60_cell_artifact(ticker="AAA")

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"AAA": artifact},
            tmp_path=tmp_path,
            member_completeness_provider_callable=provider,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    rrow = package["ranking_rows"][0]
    cb = rrow["data_completeness"]
    assert cb["has_incomplete_build_members"] is True
    assert cb["incomplete_member_count"] == 1
    assert cb["incomplete_members"] == ["TEF"]
    assert cb["incomplete_member_reasons"][
        "TEF"
    ] == "possibly_delisted_or_stale_pkl"
    assert cb["data_completeness_status"] == "partial"
    assert cb["data_warning_symbol"] == "!"
    # ticker_details carry the same block.
    detail = package["ticker_details"]["AAA"]
    assert detail["data_completeness"][
        "has_incomplete_build_members"
    ] is True
    # View model passes through to ranking_table row +
    # ticker card.
    vm = _crv.build_view_model(package)
    rtrow = vm["ranking_table"][0]
    assert rtrow["data_completeness"][
        "has_incomplete_build_members"
    ] is True
    assert rtrow["data_completeness"][
        "data_warning_symbol"
    ] == "!"
    card = vm["ticker_cards"][0]
    assert card["data_completeness"][
        "incomplete_members"
    ] == ["TEF"]
    # Aggregate summary in view model.
    summary = vm["data_completeness_summary"]
    assert (
        summary["tickers_with_incomplete_members"] == 1
    )
    assert "AAA" in summary["ticker_list"]
    assert summary["by_data_completeness_status"][
        "partial"
    ] == 1


def test_blocked_ticker_completeness_status_is_blocked(
    tmp_path,
):
    """Blocked / daily-only tickers carry
    data_completeness_status="blocked" -- no fabricated
    incomplete-member fields."""
    art_blk = _make_blocked_daily_only_artifact(
        ticker="BLK",
    )
    report = _build_export(
        ["BLK"], {"BLK": art_blk}, tmp_path=tmp_path,
    )
    j = report.to_json_dict()
    cb = j["blocked_rows"][0]["data_completeness"]
    assert cb["data_completeness_status"] == "blocked"
    assert cb["has_incomplete_build_members"] is False
    assert cb["incomplete_members"] == []
    assert cb["incomplete_member_reasons"] == {}
    assert cb["data_warning_symbol"] == "!"
    assert "blocked" in cb["data_completeness_message"]


def test_blocked_table_row_carries_completeness_pass_through(
    tmp_path,
):
    art_blk = _make_blocked_daily_only_artifact(
        ticker="BLK",
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"BLK": art_blk}, tmp_path=tmp_path,
        )

    package = _cwep.build_website_export_package(
        ["BLK"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    blk_row = vm["blocked_table"][0]
    cb = blk_row["data_completeness"]
    assert cb["data_completeness_status"] == "blocked"
    # Blocked ticker_cards also carry the block.
    blk_card = vm["ticker_cards"][0]
    assert blk_card["data_completeness"][
        "data_completeness_status"
    ] == "blocked"


# ---------------------------------------------------------------------------
# 4. Live-current signal status surface (locked / provisional)
# ---------------------------------------------------------------------------


def test_default_eligible_signal_status_is_locked(
    tmp_path,
):
    """No live provider -> eligible rows are locked,
    source=artifact, no latest_price, no provisional flag.
    """
    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    j = report.to_json_dict()
    cs = j["ranking_rows"][0]["current_signal_status_block"]
    assert cs["current_signal_status"] == "locked"
    assert cs["current_signal_as_of"] == "2026-05-14"
    assert cs["latest_price"] is None
    assert cs["latest_price_as_of"] is None
    assert cs["uses_provisional_price"] is False
    assert cs["signal_update_source"] == "artifact"


def test_blocked_signal_status_is_blocked_with_unavailable_source(
    tmp_path,
):
    art_blk = _make_blocked_daily_only_artifact(
        ticker="BLK",
    )
    report = _build_export(
        ["BLK"], {"BLK": art_blk}, tmp_path=tmp_path,
    )
    j = report.to_json_dict()
    cs = j["blocked_rows"][0]["current_signal_status_block"]
    assert cs["current_signal_status"] == "blocked"
    assert cs["signal_update_source"] == "unavailable"
    assert cs["uses_provisional_price"] is False
    assert cs["latest_price"] is None


def test_fake_live_provider_flips_status_to_provisional(
    tmp_path,
):
    """Fake live_price_provider returns a provisional
    overlay -> current_signal_status flips to provisional,
    source=live_price_overlay, latest_price populated."""
    def fake_live(ticker, artifact=None):
        return {
            "latest_price": 101.25,
            "latest_price_as_of": (
                "2026-05-14T15:55:00+00:00"
            ),
            "uses_provisional_price": True,
        }

    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
        live_price_provider_callable=fake_live,
    )
    j = report.to_json_dict()
    cs = j["ranking_rows"][0]["current_signal_status_block"]
    assert cs["current_signal_status"] == "provisional"
    assert cs["latest_price"] == 101.25
    assert cs["latest_price_as_of"] == (
        "2026-05-14T15:55:00+00:00"
    )
    assert cs["uses_provisional_price"] is True
    assert cs["signal_update_source"] == (
        "live_price_overlay"
    )


def test_live_provider_can_mark_stale(tmp_path):
    def stale_live(ticker, artifact=None):
        return {
            "latest_price": 99.0,
            "latest_price_as_of": "2026-05-10T12:00:00",
            "uses_provisional_price": False,
            "current_signal_status": "stale",
        }

    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
        live_price_provider_callable=stale_live,
    )
    j = report.to_json_dict()
    cs = j["ranking_rows"][0]["current_signal_status_block"]
    assert cs["current_signal_status"] == "stale"


def test_provisional_propagates_through_package_and_view(
    tmp_path,
):
    def fake_live(ticker, artifact=None):
        return {
            "latest_price": 101.25,
            "latest_price_as_of": "2026-05-14T15:55:00",
            "uses_provisional_price": True,
        }

    artifact = _make_full_60_cell_artifact(ticker="AAA")

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, {"AAA": artifact},
            tmp_path=tmp_path,
            live_price_provider_callable=fake_live,
        )

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    rrow = package["ranking_rows"][0]
    assert rrow["current_signal_status_block"][
        "current_signal_status"
    ] == "provisional"
    detail = package["ticker_details"]["AAA"]
    assert detail["current_signal_status_block"][
        "latest_price"
    ] == 101.25
    vm = _crv.build_view_model(package)
    rtrow = vm["ranking_table"][0]
    assert rtrow["current_signal_status_block"][
        "current_signal_status"
    ] == "provisional"
    card = vm["ticker_cards"][0]
    assert card["current_signal_status_block"][
        "signal_update_source"
    ] == "live_price_overlay"


# ---------------------------------------------------------------------------
# 5. Spymaster flip-risk placeholders
# ---------------------------------------------------------------------------


def test_default_flip_risk_block_is_null_placeholder(
    tmp_path,
):
    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
    )
    j = report.to_json_dict()
    fr = j["ranking_rows"][0]["flip_risk"]
    assert fr["flip_risk_available"] is False
    assert fr["flip_risk_label"] is None
    assert fr["nearest_flip_price"] is None
    assert fr["nearest_flip_pct"] is None
    assert fr["flip_to_signal"] is None


def test_fake_flip_risk_provider_populates_fields(
    tmp_path,
):
    def fake_flip(ticker, artifact=None):
        return {
            "flip_risk_available": True,
            "flip_risk_label": "Medium",
            "nearest_flip_price": 105.0,
            "nearest_flip_pct": 4.95,
            "flip_to_signal": "Short",
        }

    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
        flip_risk_provider_callable=fake_flip,
    )
    j = report.to_json_dict()
    fr = j["ranking_rows"][0]["flip_risk"]
    assert fr["flip_risk_available"] is True
    assert fr["flip_risk_label"] == "Medium"
    assert fr["nearest_flip_price"] == 105.0
    assert fr["nearest_flip_pct"] == 4.95
    assert fr["flip_to_signal"] == "Short"


def test_flip_risk_rejects_unknown_label(tmp_path):
    """A provider returning an unsanctioned label is
    normalized to None rather than silently accepted."""
    def bad_flip(ticker, artifact=None):
        return {
            "flip_risk_available": True,
            "flip_risk_label": "TotallyMadeUp",
            "nearest_flip_price": 100.0,
            "nearest_flip_pct": 1.0,
            "flip_to_signal": "Buy",
        }

    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
        flip_risk_provider_callable=bad_flip,
    )
    j = report.to_json_dict()
    fr = j["ranking_rows"][0]["flip_risk"]
    # flip_risk_label must be one of the sanctioned values
    # or None.
    assert fr["flip_risk_label"] is None


def test_flip_risk_blocked_row_stays_null(tmp_path):
    """Blocked rows do not consult the flip-risk provider --
    placeholder block is always null/false."""
    def fake_flip(ticker, artifact=None):
        return {
            "flip_risk_available": True,
            "flip_risk_label": "Critical",
            "nearest_flip_price": 1.0,
            "nearest_flip_pct": 0.5,
            "flip_to_signal": "Short",
        }

    art_blk = _make_blocked_daily_only_artifact(
        ticker="BLK",
    )
    report = _build_export(
        ["BLK"], {"BLK": art_blk}, tmp_path=tmp_path,
        flip_risk_provider_callable=fake_flip,
    )
    j = report.to_json_dict()
    fr = j["blocked_rows"][0]["flip_risk"]
    assert fr["flip_risk_available"] is False
    assert fr["flip_risk_label"] is None


# ---------------------------------------------------------------------------
# 6. One-row-per-ticker invariant carried forward
# ---------------------------------------------------------------------------


def test_one_ticker_remains_one_row_after_phase_6i40(
    tmp_path,
):
    """Despite all the new Phase 6I-40 fields, one ticker is
    still one row. Even with a fake live provider AND a
    fake flip-risk provider supplying data, the ranking
    table holds exactly one row per ticker."""
    def fake_live(ticker, artifact=None):
        return {
            "latest_price": 100.0 + len(ticker),
            "latest_price_as_of": "2026-05-14T15:55:00",
            "uses_provisional_price": True,
        }

    def fake_flip(ticker, artifact=None):
        return {
            "flip_risk_available": True,
            "flip_risk_label": "Low",
            "nearest_flip_price": 200.0,
            "nearest_flip_pct": 10.0,
            "flip_to_signal": "Short",
        }

    artifact_map = {
        t: _make_full_60_cell_artifact(
            ticker=t,
            total_capture_pct=15.0 + i,
        )
        for i, t in enumerate(
            ["ZULU", "QUOK", "MIDA", "JBLU", "ACME"],
        )
    }

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, artifact_map,
            tmp_path=tmp_path,
            live_price_provider_callable=fake_live,
            flip_risk_provider_callable=fake_flip,
        )

    package = _cwep.build_website_export_package(
        list(artifact_map.keys()),
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    assert len(package["ranking_rows"]) == 5
    tickers = [
        r["ticker"] for r in package["ranking_rows"]
    ]
    assert len(tickers) == len(set(tickers))
    vm = _crv.build_view_model(package)
    assert len(vm["ranking_table"]) == 5
    rt_tickers = [r["ticker"] for r in vm["ranking_table"]]
    assert len(rt_tickers) == len(set(rt_tickers))
    assert len(vm["ticker_cards"]) == 5


# ---------------------------------------------------------------------------
# 7. Large-universe fixture (no SPY)
# ---------------------------------------------------------------------------


def test_large_universe_fixture_no_spy_with_mixed_states(
    tmp_path,
):
    """6-ticker non-SPY universe: 3 eligible (different
    capture levels), 2 eligible-with-incomplete-member
    warning, 1 blocked. Proves the contract handles mixed
    rows and the sort metadata + warning + signal-status
    fields all propagate."""
    def provider(ticker, artifact=None):
        if ticker in ("CCC", "DDD"):
            return {
                "has_incomplete_build_members": True,
                "incomplete_member_count": 1,
                "incomplete_members": ["DEADCO"],
                "incomplete_member_reasons": {
                    "DEADCO": "stale_pkl",
                },
            }
        return {
            "has_incomplete_build_members": False,
            "incomplete_member_count": 0,
            "incomplete_members": [],
            "incomplete_member_reasons": {},
        }

    artifact_map: dict[str, dict[str, Any]] = {
        "AAA": _make_full_60_cell_artifact(
            ticker="AAA", total_capture_pct=20.0,
            sharpe_ratio=1.5,
        ),
        "BBB": _make_full_60_cell_artifact(
            ticker="BBB", total_capture_pct=15.0,
            sharpe_ratio=1.3,
        ),
        "CCC": _make_full_60_cell_artifact(
            ticker="CCC", total_capture_pct=8.0,
            sharpe_ratio=0.7,
        ),
        "DDD": _make_full_60_cell_artifact(
            ticker="DDD", total_capture_pct=5.0,
            sharpe_ratio=0.4,
        ),
        "EEE": _make_full_60_cell_artifact(
            ticker="EEE", total_capture_pct=3.0,
            sharpe_ratio=0.2,
        ),
        "FFF": _make_blocked_daily_only_artifact(
            ticker="FFF",
        ),
    }

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return _build_export(
            tickers, artifact_map,
            tmp_path=tmp_path,
            member_completeness_provider_callable=provider,
        )

    package = _cwep.build_website_export_package(
        ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_ALL_ARTIFACTS,
        underlying_export_callable=fake_export,
    )
    # 5 eligible (AAA..EEE), 1 blocked (FFF).
    assert package["eligible_count"] == 5
    assert package["blocked_count"] == 1
    # Sort metadata propagates.
    assert package["sortable_columns"][:3] == [
        "total_capture_pct",
        "sharpe_ratio",
        "trigger_days",
    ]
    # Two tickers carry the incomplete-member warning.
    cc = [
        r for r in package["ranking_rows"]
        if r["data_completeness"][
            "has_incomplete_build_members"
        ]
    ]
    cc_tickers = sorted(r["ticker"] for r in cc)
    assert cc_tickers == ["CCC", "DDD"]
    # Sort by capture asc on the package's view model
    # should invert leader-vs-laggard.
    vm = _crv.build_view_model(package)
    rt = vm["ranking_table"]
    # By default sort, AAA (highest capture/Sharpe) is rank 1.
    by_rank = {
        r["row_sort_values"]["rank_sort"]: r["ticker"]
        for r in rt
    }
    assert by_rank[1] == "AAA"
    # Renderer can flip via row_sort_values[
    # total_capture_pct_sort] ascending.
    asc_order = sorted(
        rt,
        key=lambda r: r["row_sort_values"][
            "total_capture_pct_sort"
        ],
    )
    assert asc_order[0]["ticker"] == "EEE"
    assert asc_order[-1]["ticker"] == "AAA"
    # Data completeness summary panel.
    summary = vm["data_completeness_summary"]
    assert (
        summary["tickers_with_incomplete_members"] == 2
    )
    assert sorted(summary["ticker_list"]) == [
        "CCC", "DDD",
    ]
    assert summary["by_data_completeness_status"][
        "complete"
    ] == 3
    assert summary["by_data_completeness_status"][
        "partial"
    ] == 2
    assert summary["by_data_completeness_status"][
        "blocked"
    ] == 1
    # Each ticker is exactly one row everywhere.
    rrow_tickers = [
        r["ticker"] for r in package["ranking_rows"]
    ]
    assert len(rrow_tickers) == len(set(rrow_tickers))
    card_tickers = [c["ticker"] for c in vm["ticker_cards"]]
    assert len(card_tickers) == len(set(card_tickers))


# ---------------------------------------------------------------------------
# 8. Defensive: malformed provider does not crash
# ---------------------------------------------------------------------------


def test_provider_raising_exception_falls_back_to_default(
    tmp_path,
):
    def boom(ticker, artifact=None):
        raise RuntimeError("provider exploded")

    artifact = _make_full_60_cell_artifact(ticker="AAA")
    # All three providers raise; the row builder must still
    # construct cleanly with default fallbacks.
    report = _build_export(
        ["AAA"], {"AAA": artifact}, tmp_path=tmp_path,
        member_completeness_provider_callable=boom,
        live_price_provider_callable=boom,
        flip_risk_provider_callable=boom,
    )
    j = report.to_json_dict()
    rrow = j["ranking_rows"][0]
    cb = rrow["data_completeness"]
    assert cb["data_completeness_status"] == "complete"
    cs = rrow["current_signal_status_block"]
    assert cs["current_signal_status"] == "locked"
    fr = rrow["flip_risk"]
    assert fr["flip_risk_available"] is False
    assert fr["flip_risk_label"] is None
