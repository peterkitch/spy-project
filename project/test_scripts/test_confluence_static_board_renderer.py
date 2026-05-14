"""Phase 6I-41 tests: static Confluence board renderer / UI
shell.

Pins:

  * Ranking rows render one-per-ticker; no per-K explosion.
  * Blocked rows render one-per-ticker honestly.
  * Sort controls expose Total Capture %, Sharpe Ratio,
    Trigger Days, Rank, Ticker -- with asc/desc selectable.
  * row_sort_values render as ``data-sort-*`` attributes
    on each row (safe numeric/text payload for JS sorting).
  * data_completeness="partial" surfaces the ``!`` warning
    symbol; blocked rows surface ``!`` with the
    "blocked: ..." message.
  * current_signal_status badge renders for locked /
    provisional / stale.
  * primary_build compact label renders.
  * Same-K all-window vs single-cell-fallback render
    distinctly (CSS class + tier data attribute).
  * 60-cell matrix renders inside the inlined detail JSON
    so the JS detail panel can paint it.
  * eligible_count=0 renders empty_state HTML, NOT fake
    rows.
  * HTML escaping prevents ticker / text injection.
  * CLI stdout path emits HTML; rc=2 on missing source;
    rc=2 on production-root --output; rc=3 on schema /
    unreadable input.
  * Schema-error view model renders the error shell.
  * Static forbidden-import / write guards on the module.
"""
from __future__ import annotations

import ast
import io
import json
import re
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_static_board_renderer as rnd  # noqa: E402


# ---------------------------------------------------------------------------
# View-model fixtures
# ---------------------------------------------------------------------------


def _row_sort_values(
    *,
    rank: int = 1,
    capture: float | None = 100.0,
    sharpe: float | None = 1.5,
    trigger_days: int = 200,
    ticker: str = "AAA",
) -> dict[str, Any]:
    return {
        "total_capture_pct_sort": capture,
        "sharpe_ratio_sort": sharpe,
        "trigger_days_sort": trigger_days,
        "rank_sort": rank,
        "ticker_sort": ticker,
    }


def _data_completeness(
    *,
    status: str = "complete",
    incomplete_members: list[str] | None = None,
    incomplete_reasons: dict[str, str] | None = None,
) -> dict[str, Any]:
    incomplete_members = incomplete_members or []
    incomplete_reasons = incomplete_reasons or {}
    is_partial = bool(incomplete_members)
    sym = (
        "!" if status in ("partial", "blocked")
        or is_partial else None
    )
    return {
        "has_incomplete_build_members": is_partial,
        "incomplete_member_count": len(incomplete_members),
        "incomplete_members": list(incomplete_members),
        "incomplete_member_reasons": dict(
            incomplete_reasons,
        ),
        "data_warning_symbol": sym,
        "data_completeness_status": status,
        "data_completeness_message": (
            f"{status}: msg here"
        ),
    }


def _current_signal_status_block(
    *,
    status: str = "locked",
    latest_price: float | None = None,
    provisional: bool = False,
    source: str = "artifact",
) -> dict[str, Any]:
    return {
        "current_signal_status": status,
        "current_signal_as_of": "2026-05-14",
        "latest_price": latest_price,
        "latest_price_as_of": (
            "2026-05-14T15:55:00" if latest_price is not None
            else None
        ),
        "uses_provisional_price": provisional,
        "signal_update_source": source,
    }


def _flip_risk(
    *,
    available: bool = False,
    label: str | None = None,
) -> dict[str, Any]:
    return {
        "flip_risk_available": available,
        "flip_risk_label": label,
        "nearest_flip_price": None,
        "nearest_flip_pct": None,
        "flip_to_signal": None,
    }


def _primary_build_compact(
    *,
    tier: str = "same_k_all_windows_same_direction",
    K: int = 6,
    direction: str = "Buy",
    label: str = (
        "K=6 Buy in 5 window(s) (cap 50.00%, Sharpe 1.50)"
    ),
    conflict: bool = False,
    other_active_count: int = 0,
) -> dict[str, Any]:
    return {
        "primary_build_available": True,
        "selection_tier": tier,
        "K": K,
        "signal_direction": direction,
        "windows_signaling_count": 5,
        "direction_conflict": conflict,
        "explanation": (
            "all_windows_same_direction"
            if tier == "same_k_all_windows_same_direction"
            else (
                "all_windows_mixed_direction"
                if tier == "same_k_all_windows_mixed_direction"
                else "single_cell_fallback"
            )
        ),
        "label": label,
        "other_active_k_count": other_active_count,
    }


def _primary_build_full_summary(
    *,
    K: int = 6,
    direction: str = "Buy",
    tier: str = "same_k_all_windows_same_direction",
) -> dict[str, Any]:
    return {
        "primary_build_available": True,
        "selection_tier": tier,
        "K": K,
        "signal_direction": direction,
        "windows_signaling_count": 5,
        "windows_signaling": [
            "1d", "1wk", "1mo", "3mo", "1y",
        ],
        "buy_window_count": 5 if direction == "Buy" else 0,
        "short_window_count": (
            5 if direction == "Short" else 0
        ),
        "all_members_aligned_window_count": 5,
        "total_capture_pct_sum": 50.0,
        "avg_sharpe_ratio": 1.5,
        "trigger_days_sum": 200,
        "strongest_cell_window": "1d",
        "direction_conflict": False,
        "explanation": "all_windows_same_direction",
        "same_direction_k_builds_all_windows": [K],
        "mixed_direction_k_builds_all_windows": [],
        "other_active_k_builds": [],
        "display_row_cardinality": "one_row_per_ticker",
    }


def _build_60_cell_matrix(ticker: str = "AAA") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for w in ("1d", "1wk", "1mo", "3mo", "1y"):
        for K in range(1, 13):
            out.append({
                "ticker": ticker,
                "K": K,
                "window": w,
                "latest_combined_signal": "Buy",
                "latest_buy_count": 3,
                "latest_short_count": 0,
                "latest_none_count": 0,
                "latest_missing_count": 0,
                "member_count": 3,
                "alignment_ratio": 1.0,
                "all_members_aligned": True,
                "currently_signaling": True,
                "currently_firing": True,
                "historically_fired": True,
                "total_capture_pct": 10.0,
                "avg_daily_capture_pct": 0.5,
                "sharpe_ratio": 1.5,
                "trigger_days": 20,
                "wins": 12,
                "losses": 8,
            })
    return out


def _ranking_table_row(
    *,
    ticker: str = "AAA",
    rank: int = 1,
    direction: str = "Buy",
    capture_label: str = "100.00%",
    sharpe_label: str = "1.50",
    trigger_days: int = 200,
    chart_ready: bool = True,
    freshness: str = "fresh",
    completeness_status: str = "complete",
    incomplete_members: list[str] | None = None,
    current_signal_status: str = "locked",
    latest_price: float | None = None,
    provisional: bool = False,
    update_source: str = "artifact",
    pb_tier: str = "same_k_all_windows_same_direction",
    pb_K: int = 6,
    pb_conflict: bool = False,
    pb_label: str | None = None,
    flip_available: bool = False,
    flip_label: str | None = None,
) -> dict[str, Any]:
    if pb_label is None:
        pb_label = (
            f"K={pb_K} {direction} in 5 window(s) "
            f"(cap {capture_label}, Sharpe {sharpe_label})"
        )
    return {
        "rank": rank,
        "ticker": ticker,
        "direction": direction,
        "windows": "5/5",
        "k_cells": "60/60",
        "strongest": (
            f"1d K=12 (cap {capture_label}, "
            f"Sharpe {sharpe_label})"
        ),
        "capture": capture_label,
        "sharpe": sharpe_label,
        "trigger_days": trigger_days,
        "chart_ready": chart_ready,
        "freshness": freshness,
        "issues": [],
        "current_signal_summary": None,
        "primary_build": _primary_build_compact(
            tier=pb_tier,
            K=pb_K,
            direction=direction,
            label=pb_label,
            conflict=pb_conflict,
        ),
        "row_sort_values": _row_sort_values(
            rank=rank,
            capture=float(
                capture_label.rstrip("%")
            ) if capture_label.endswith("%") else None,
            sharpe=float(sharpe_label) if (
                sharpe_label
                and sharpe_label != "—"
            ) else None,
            trigger_days=trigger_days,
            ticker=ticker,
        ),
        "data_completeness": _data_completeness(
            status=completeness_status,
            incomplete_members=incomplete_members,
        ),
        "current_signal_status_block": (
            _current_signal_status_block(
                status=current_signal_status,
                latest_price=latest_price,
                provisional=provisional,
                source=update_source,
            )
        ),
        "flip_risk": _flip_risk(
            available=flip_available, label=flip_label,
        ),
    }


def _blocked_table_row(
    *,
    ticker: str = "BLK",
    reason: str = "daily_only",
    data_status: str = "daily_only",
    freshness: str = "unknown",
    chart_status: str = "no_chart_data_source",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "reason": reason,
        "data_status": data_status,
        "freshness": freshness,
        "chart_status": chart_status,
        "issues": [],
        "data_completeness": _data_completeness(
            status="blocked",
        ),
        "current_signal_status_block": (
            _current_signal_status_block(
                status="blocked",
                source="unavailable",
            )
        ),
    }


def _ticker_card_eligible(
    *,
    ticker: str = "AAA",
    completeness_status: str = "complete",
    incomplete_members: list[str] | None = None,
    current_signal_status: str = "locked",
    latest_price: float | None = None,
    provisional: bool = False,
    update_source: str = "artifact",
    flip_available: bool = False,
    flip_label: str | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "rank_eligible": True,
        "detail_available": True,
        "detail_source": f"/tmp/research/{ticker}.json",
        "detail_blocker": None,
        "summary": {
            "windows_firing": [
                "1d", "1wk", "1mo", "3mo", "1y",
            ],
            "windows_firing_count": 5,
            "windows_total": 5,
            "all_windows_firing": True,
            "k_cells_firing": 60,
            "k_cells_total": 60,
        },
        "all_members_firing_windows": [
            "1d", "1wk", "1mo", "3mo", "1y",
        ],
        "chart_ready_available": True,
        "chart_ready_source": "confluence_artifact",
        "chart_row_count": 100,
        "chart_blocker": None,
        "freshness_status": "fresh",
        "data_status": "full_60_cell",
        "issue_codes": [],
        "blocker_text": None,
        "current_build_signals": _build_60_cell_matrix(
            ticker=ticker,
        ),
        "current_build_signal_summary": {
            "cells_total": 60,
            "cells_currently_buy": 60,
            "cells_currently_short": 0,
            "cells_currently_none": 0,
            "cells_currently_missing": 0,
            "cells_with_all_members_aligned": 60,
            "cells_historically_fired": 60,
            "windows_with_any_currently_signaling": [
                "1d", "1wk", "1mo", "3mo", "1y",
            ],
            "all_windows_have_any_current_signal": True,
            "k_builds_currently_signaling_all_windows": (
                list(range(1, 13))
            ),
            "k_builds_all_members_aligned_all_windows": (
                list(range(1, 13))
            ),
            "all_five_windows_same_k_currently_signaling": (
                True
            ),
            "all_five_windows_same_k_all_members_aligned": (
                True
            ),
            "windows_with_all_members_firing": [
                "1d", "1wk", "1mo", "3mo", "1y",
            ],
            "strongest_currently_signaling_cell": None,
            "strongest_cross_window_k_build": None,
        },
        "primary_build_summary": (
            _primary_build_full_summary()
        ),
        "data_completeness": _data_completeness(
            status=completeness_status,
            incomplete_members=incomplete_members,
        ),
        "current_signal_status_block": (
            _current_signal_status_block(
                status=current_signal_status,
                latest_price=latest_price,
                provisional=provisional,
                source=update_source,
            )
        ),
        "flip_risk": _flip_risk(
            available=flip_available, label=flip_label,
        ),
    }


def _ticker_card_blocked(
    *, ticker: str = "BLK",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "rank_eligible": False,
        "detail_available": False,
        "detail_source": None,
        "detail_blocker": "daily_only",
        "summary": None,
        "all_members_firing_windows": [],
        "chart_ready_available": False,
        "chart_ready_source": "unavailable",
        "chart_row_count": None,
        "chart_blocker": "no_chart_data_source",
        "freshness_status": "unknown",
        "data_status": "daily_only",
        "issue_codes": [],
        "blocker_text": "daily_only",
        "current_build_signals": [],
        "current_build_signal_summary": None,
        "primary_build_summary": None,
        "data_completeness": _data_completeness(
            status="blocked",
        ),
        "current_signal_status_block": (
            _current_signal_status_block(
                status="blocked",
                source="unavailable",
            )
        ),
        "flip_risk": _flip_risk(),
    }


def _make_view_model(
    *,
    ranking_table: list[dict[str, Any]] | None = None,
    blocked_table: list[dict[str, Any]] | None = None,
    ticker_cards: list[dict[str, Any]] | None = None,
    eligible_count: int | None = None,
    blocked_count: int | None = None,
    empty_state: dict[str, Any] | None = None,
    status_banner_kind: str = "has_eligible_rankings",
) -> dict[str, Any]:
    ranking_table = ranking_table or []
    blocked_table = blocked_table or []
    ticker_cards = ticker_cards or []
    if eligible_count is None:
        eligible_count = len(ranking_table)
    if blocked_count is None:
        blocked_count = len(blocked_table)
    headline_by_kind = {
        "has_eligible_rankings": (
            "Eligible Confluence rankings available."
        ),
        "no_eligible_production_blocked": (
            "No tickers are rank-eligible yet."
        ),
        "no_tickers_inspected": "No tickers inspected.",
        "schema_error": (
            "Confluence export package was unreadable."
        ),
    }
    return {
        "schema_version": "confluence_website_export_v1",
        "view_model_version": (
            "confluence_website_reader_view_v1"
        ),
        "generated_at": "2026-05-14T00:00:00+00:00",
        "rendered_at": "2026-05-14T00:00:01+00:00",
        "page_title": "Confluence Multi-Ticker Ranking Board",
        "display_row_cardinality": "one_row_per_ticker",
        "sortable_columns": [
            "total_capture_pct", "sharpe_ratio",
            "trigger_days", "rank", "ticker",
        ],
        "default_sort": [
            {
                "column_id": "sharpe_ratio",
                "direction": "desc",
            },
            {
                "column_id": "total_capture_pct",
                "direction": "desc",
            },
            {
                "column_id": "trigger_days",
                "direction": "desc",
            },
        ],
        "sort_options": [
            {
                "column_id": "total_capture_pct",
                "label": "Total Capture %",
                "row_sort_value_key": (
                    "total_capture_pct_sort"
                ),
                "directions": ["desc", "asc"],
                "value_type": "number",
            },
            {
                "column_id": "sharpe_ratio",
                "label": "Sharpe Ratio",
                "row_sort_value_key": "sharpe_ratio_sort",
                "directions": ["desc", "asc"],
                "value_type": "number",
            },
            {
                "column_id": "trigger_days",
                "label": "Trigger Days",
                "row_sort_value_key": "trigger_days_sort",
                "directions": ["desc", "asc"],
                "value_type": "number",
            },
            {
                "column_id": "rank",
                "label": "Rank",
                "row_sort_value_key": "rank_sort",
                "directions": ["asc", "desc"],
                "value_type": "number",
            },
            {
                "column_id": "ticker",
                "label": "Ticker",
                "row_sort_value_key": "ticker_sort",
                "directions": ["asc", "desc"],
                "value_type": "string",
            },
        ],
        "has_eligible_rankings": eligible_count > 0,
        "eligible_count": eligible_count,
        "blocked_count": blocked_count,
        "inspected_count": eligible_count + blocked_count,
        "empty_state": empty_state,
        "ranking_table": ranking_table,
        "blocked_table": blocked_table,
        "ticker_cards": ticker_cards,
        "chart_readiness_summary": None,
        "freshness_summary": None,
        "issue_summary": None,
        "data_completeness_summary": {
            "tickers_with_incomplete_members": 0,
            "ticker_list": [],
            "by_data_completeness_status": {},
        },
        "status_banner": {
            "kind": status_banner_kind,
            "headline": (
                headline_by_kind.get(
                    status_banner_kind, "unknown",
                )
            ),
            "body": "test body",
        },
        "remaining_limitations": [
            "Test limitation note.",
        ],
    }


# ---------------------------------------------------------------------------
# 1. Renders one row per ticker
# ---------------------------------------------------------------------------


def test_renders_eligible_rows_one_row_per_ticker():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(ticker="AAA", rank=1),
            _ranking_table_row(
                ticker="BBB", rank=2,
                direction="Short",
                capture_label="80.00%",
                sharpe_label="1.20",
            ),
            _ranking_table_row(
                ticker="CCC", rank=3,
                capture_label="60.00%",
                sharpe_label="0.90",
            ),
        ],
        ticker_cards=[
            _ticker_card_eligible(ticker="AAA"),
            _ticker_card_eligible(ticker="BBB"),
            _ticker_card_eligible(ticker="CCC"),
        ],
    )
    html_text = rnd.build_static_board_html(vm)
    # Each ticker appears EXACTLY once in a ranking row.
    for t in ("AAA", "BBB", "CCC"):
        matches = re.findall(
            r'tr class="ranking-row"[^>]*data-ticker="'
            + t + r'"',
            html_text,
        )
        assert len(matches) == 1, (
            f"ticker {t} appears in "
            f"{len(matches)} ranking rows; expected exactly 1"
        )
    # Ranking table has exactly 3 ranking rows.
    rows = re.findall(
        r'<tr class="ranking-row"',
        html_text,
    )
    assert len(rows) == 3


def test_renders_blocked_rows_one_line_per_ticker():
    vm = _make_view_model(
        ranking_table=[],
        blocked_table=[
            _blocked_table_row(ticker="SPY"),
            _blocked_table_row(ticker="_GSPC"),
        ],
        ticker_cards=[
            _ticker_card_blocked(ticker="SPY"),
            _ticker_card_blocked(ticker="_GSPC"),
        ],
        status_banner_kind=(
            "no_eligible_production_blocked"
        ),
        empty_state={
            "headline": "No tickers are rank-eligible yet.",
            "reason": "Production daily-only.",
            "next_action": "...",
            "blocked_count": 2,
            "sample_blockers": [
                {
                    "ticker": "SPY",
                    "ranking_blocked_reason": "daily_only",
                    "data_status": "daily_only",
                },
            ],
        },
    )
    html_text = rnd.build_static_board_html(vm)
    blocked_rows = re.findall(
        r'<tr class="blocked-row"', html_text,
    )
    assert len(blocked_rows) == 2
    for t in ("SPY", "_GSPC"):
        assert f'data-ticker="{t}"' in html_text


# ---------------------------------------------------------------------------
# 2. Sort controls
# ---------------------------------------------------------------------------


def test_sort_controls_expose_all_required_columns():
    vm = _make_view_model(
        ranking_table=[_ranking_table_row()],
        ticker_cards=[_ticker_card_eligible()],
    )
    html_text = rnd.build_static_board_html(vm)
    # Sort-column select must carry all five required columns.
    assert 'id="sort-column"' in html_text
    for col, label in [
        ("total_capture_pct", "Total Capture %"),
        ("sharpe_ratio", "Sharpe Ratio"),
        ("trigger_days", "Trigger Days"),
        ("rank", "Rank"),
        ("ticker", "Ticker"),
    ]:
        assert f'value="{col}"' in html_text
        assert label in html_text
    # Direction select must offer asc + desc.
    assert 'id="sort-direction"' in html_text
    assert 'value="desc"' in html_text
    assert 'value="asc"' in html_text


def test_default_sort_is_sharpe_desc():
    vm = _make_view_model(
        ranking_table=[_ranking_table_row()],
        ticker_cards=[_ticker_card_eligible()],
    )
    html_text = rnd.build_static_board_html(vm)
    # Default-selected option in the column dropdown.
    assert re.search(
        r'<option value="sharpe_ratio"\s*selected>',
        html_text,
    )
    assert re.search(
        r'<option value="desc"\s*selected>',
        html_text,
    )


# ---------------------------------------------------------------------------
# 3. row_sort_values embedded safely for JS sort
# ---------------------------------------------------------------------------


def test_row_sort_values_embedded_as_data_attributes():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker="AAA", rank=1,
                capture_label="100.00%",
                sharpe_label="1.50",
                trigger_days=200,
            ),
        ],
        ticker_cards=[_ticker_card_eligible()],
    )
    html_text = rnd.build_static_board_html(vm)
    assert 'data-sort-rank="1"' in html_text
    assert 'data-sort-capture="100.0"' in html_text
    assert 'data-sort-sharpe="1.5"' in html_text
    assert 'data-sort-trigger="200"' in html_text
    assert 'data-sort-ticker="AAA"' in html_text


# ---------------------------------------------------------------------------
# 4. Incomplete-member warning ("!") renders for partial rows
# ---------------------------------------------------------------------------


def test_partial_data_completeness_renders_warning_symbol():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker="AAA",
                completeness_status="partial",
                incomplete_members=["DEADCO"],
            ),
        ],
        ticker_cards=[
            _ticker_card_eligible(
                ticker="AAA",
                completeness_status="partial",
                incomplete_members=["DEADCO"],
            ),
        ],
    )
    html_text = rnd.build_static_board_html(vm)
    # Warning symbol "!" present.
    assert (
        '<span class="warning warning-on"' in html_text
    )
    # Status attribute on the warning span.
    assert 'data-status="partial"' in html_text


def test_blocked_row_warning_renders_with_blocker_message():
    vm = _make_view_model(
        ranking_table=[],
        blocked_table=[
            _blocked_table_row(ticker="SPY"),
        ],
        ticker_cards=[_ticker_card_blocked(ticker="SPY")],
        status_banner_kind=(
            "no_eligible_production_blocked"
        ),
        empty_state={
            "headline": "No eligible.",
            "reason": "...",
            "next_action": "...",
            "blocked_count": 1,
            "sample_blockers": [],
        },
    )
    html_text = rnd.build_static_board_html(vm)
    # Blocked-row warning span carries "!" and status=blocked.
    assert (
        '<span class="warning warning-on"' in html_text
    )
    assert 'data-status="blocked"' in html_text


# ---------------------------------------------------------------------------
# 5. Current-signal status badges
# ---------------------------------------------------------------------------


def test_current_signal_status_locked_renders():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker="AAA",
                current_signal_status="locked",
            ),
        ],
        ticker_cards=[_ticker_card_eligible(ticker="AAA")],
    )
    html_text = rnd.build_static_board_html(vm)
    assert (
        'class="status-badge status-locked"' in html_text
    )


def test_current_signal_status_provisional_renders():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker="AAA",
                current_signal_status="provisional",
                latest_price=101.25,
                provisional=True,
                update_source="live_price_overlay",
            ),
        ],
        ticker_cards=[
            _ticker_card_eligible(
                ticker="AAA",
                current_signal_status="provisional",
                latest_price=101.25,
                provisional=True,
                update_source="live_price_overlay",
            ),
        ],
    )
    html_text = rnd.build_static_board_html(vm)
    assert (
        'class="status-badge status-provisional"'
        in html_text
    )
    # Latest price renders with provisional CSS class.
    assert 'latest-price provisional' in html_text
    assert '101.25' in html_text


def test_current_signal_status_stale_renders():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker="AAA",
                current_signal_status="stale",
            ),
        ],
        ticker_cards=[
            _ticker_card_eligible(
                ticker="AAA",
                current_signal_status="stale",
            ),
        ],
    )
    html_text = rnd.build_static_board_html(vm)
    assert (
        'class="status-badge status-stale"' in html_text
    )


# ---------------------------------------------------------------------------
# 6. Primary build label renders + same-K vs single-cell
# ---------------------------------------------------------------------------


def test_primary_build_label_renders():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker="AAA",
                pb_label="K=6 Buy in 5 window(s) (cap 50.00%, Sharpe 1.50)",
            ),
        ],
        ticker_cards=[_ticker_card_eligible()],
    )
    html_text = rnd.build_static_board_html(vm)
    assert "K=6 Buy in 5 window(s)" in html_text
    # The primary-build span carries the tier data-attr.
    assert (
        'data-tier="same_k_all_windows_same_direction"'
        in html_text
    )


def test_same_k_vs_single_cell_render_distinctly():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker="AAA",
                pb_tier="same_k_all_windows_same_direction",
            ),
            _ranking_table_row(
                ticker="BBB",
                rank=2,
                pb_tier="strongest_current_cell",
                pb_label="K=12 Buy",
            ),
        ],
        ticker_cards=[
            _ticker_card_eligible(ticker="AAA"),
            _ticker_card_eligible(ticker="BBB"),
        ],
    )
    html_text = rnd.build_static_board_html(vm)
    assert "same-k-status same-k-aligned" in html_text
    assert (
        'data-tier="same_k_all_windows_same_direction"'
        in html_text
    )
    assert "same-k-status single-cell" in html_text
    assert (
        'data-tier="strongest_current_cell"' in html_text
    )


def test_same_k_mixed_renders_with_conflict_marker():
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker="AAA",
                pb_tier=(
                    "same_k_all_windows_mixed_direction"
                ),
                pb_conflict=True,
                pb_label="K=6 Mixed in 5 window(s)",
            ),
        ],
        ticker_cards=[_ticker_card_eligible()],
    )
    html_text = rnd.build_static_board_html(vm)
    assert "same-k-status same-k-mixed" in html_text
    assert "primary-build-conflict" in html_text
    assert 'data-direction-conflict="true"' in html_text


# ---------------------------------------------------------------------------
# 7. 60-cell matrix is available in the inlined detail JSON
# ---------------------------------------------------------------------------


def test_current_build_signals_matrix_embedded_in_detail_json():
    vm = _make_view_model(
        ranking_table=[_ranking_table_row(ticker="AAA")],
        ticker_cards=[_ticker_card_eligible(ticker="AAA")],
    )
    html_text = rnd.build_static_board_html(vm)
    # The JSON-embedded detail map carries the 60-cell
    # matrix. Pull the JSON out of the <script> block.
    m = re.search(
        r'<script id="ticker-detail-data" '
        r'type="application/json">(.*?)</script>',
        html_text,
        re.DOTALL,
    )
    assert m is not None
    embedded = json.loads(
        m.group(1).replace("<\\/", "</"),
    )
    assert "AAA" in embedded
    matrix = embedded["AAA"]["current_build_signals"]
    assert isinstance(matrix, list)
    assert len(matrix) == 60
    # Each entry carries the Phase 6I-37 fields.
    sample = matrix[0]
    for key in (
        "K", "window", "latest_combined_signal",
        "total_capture_pct", "sharpe_ratio",
        "trigger_days",
    ):
        assert key in sample
    # Also primary_build_summary + data_completeness +
    # current_signal_status_block + flip_risk.
    assert "primary_build_summary" in embedded["AAA"]
    assert "data_completeness" in embedded["AAA"]
    assert (
        "current_signal_status_block" in embedded["AAA"]
    )
    assert "flip_risk" in embedded["AAA"]


# ---------------------------------------------------------------------------
# 8. No eligible rows -> empty_state, NOT fake rows
# ---------------------------------------------------------------------------


def test_no_eligible_rows_renders_empty_state_not_fake_rows():
    vm = _make_view_model(
        ranking_table=[],
        blocked_table=[
            _blocked_table_row(ticker="SPY"),
            _blocked_table_row(ticker="_GSPC"),
        ],
        ticker_cards=[
            _ticker_card_blocked(ticker="SPY"),
            _ticker_card_blocked(ticker="_GSPC"),
        ],
        status_banner_kind=(
            "no_eligible_production_blocked"
        ),
        empty_state={
            "headline": (
                "No tickers are rank-eligible yet."
            ),
            "reason": (
                "Production Confluence artifacts do not "
                "yet carry the Phase 6I-20 multi-window "
                "fields."
            ),
            "next_action": "Wait for refresh.",
            "blocked_count": 2,
            "sample_blockers": [
                {
                    "ticker": "SPY",
                    "ranking_blocked_reason": "daily_only",
                    "data_status": "daily_only",
                },
                {
                    "ticker": "_GSPC",
                    "ranking_blocked_reason": "daily_only",
                    "data_status": "daily_only",
                },
            ],
        },
    )
    html_text = rnd.build_static_board_html(vm)
    # No ranking rows at all.
    assert (
        len(re.findall(r'<tr class="ranking-row"', html_text))
        == 0
    )
    # Empty-state section rendered.
    assert 'class="empty-state"' in html_text
    assert "No tickers are rank-eligible yet." in html_text
    # Blocked table still rendered.
    assert (
        len(re.findall(r'<tr class="blocked-row"', html_text))
        == 2
    )


# ---------------------------------------------------------------------------
# 9. HTML escaping prevents ticker / text injection
# ---------------------------------------------------------------------------


def test_html_escaping_prevents_ticker_injection():
    malicious = '"><script>alert("x")</script>'
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker=malicious, rank=1,
                pb_label=malicious,
            ),
        ],
        ticker_cards=[
            _ticker_card_eligible(ticker=malicious),
        ],
    )
    html_text = rnd.build_static_board_html(vm)
    # No raw <script>alert(...)</script> escaping breach
    # in the rendered HTML.
    assert "<script>alert(" not in html_text
    # The body of the script element must not be closed
    # by the embedded malicious ticker name; check by
    # locating the script tag and confirming the malicious
    # raw substring isn't present.
    assert (
        "</script>alert(" not in html_text
    )
    # Confirm the malicious payload appears only in
    # escaped form somewhere.
    assert "&quot;" in html_text or "&#34;" in html_text


def test_html_escaping_inside_detail_json_handles_close_tag():
    """A ticker named with `</script>` substring must NOT
    be able to break out of the inlined JSON <script>."""
    bad = "AB</script>CD"
    vm = _make_view_model(
        ranking_table=[
            _ranking_table_row(
                ticker=bad, rank=1, pb_label="K=1 Buy",
            ),
        ],
        ticker_cards=[
            _ticker_card_eligible(ticker=bad),
        ],
    )
    html_text = rnd.build_static_board_html(vm)
    m = re.search(
        r'<script id="ticker-detail-data" '
        r'type="application/json">(.*?)</script>',
        html_text,
        re.DOTALL,
    )
    assert m is not None
    # The raw JSON body must NOT contain a literal
    # `</script>` substring -- every `<` was escaped to
    # the JSON unicode escape `<` by
    # _json_for_html. The browser's JSON parser turns
    # `<` back into `<` on read.
    assert "</script>" not in m.group(1)
    assert "<" not in m.group(1), (
        "JSON body must not contain any raw `<` characters"
    )
    assert "\\u003c/script>" in m.group(1)


# ---------------------------------------------------------------------------
# 10. CLI stdout path + output-path guard
# ---------------------------------------------------------------------------


def test_cli_stdout_path_emits_html(tmp_path, capsys):
    vm = _make_view_model(
        ranking_table=[_ranking_table_row()],
        ticker_cards=[_ticker_card_eligible()],
    )
    vm_path = tmp_path / "view_model.json"
    vm_path.write_text(
        json.dumps(vm), encoding="utf-8",
    )
    rc = rnd.main(["--view-model", str(vm_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("<!DOCTYPE html>")
    assert (
        "Confluence Multi-Ticker Ranking Board" in out
    )


def test_cli_missing_source_returns_rc_2(capsys):
    rc = rnd.main([])
    assert rc == 2


def test_cli_unreadable_json_returns_rc_3(tmp_path, capsys):
    p = tmp_path / "vm.json"
    p.write_text("not valid json {{{", encoding="utf-8")
    rc = rnd.main(["--view-model", str(p)])
    assert rc == 3


def test_cli_output_to_tmp_path_writes_html(tmp_path):
    vm = _make_view_model(
        ranking_table=[_ranking_table_row()],
        ticker_cards=[_ticker_card_eligible()],
    )
    vm_path = tmp_path / "vm.json"
    vm_path.write_text(json.dumps(vm), encoding="utf-8")
    out_path = tmp_path / "board.html"
    rc = rnd.main([
        "--view-model", str(vm_path),
        "--output", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert content.startswith("<!DOCTYPE html>")


def test_cli_output_under_production_root_is_refused(
    tmp_path, capsys,
):
    """The --output guard must reject any path containing a
    production-root segment, regardless of whether the path
    actually exists on disk."""
    vm = _make_view_model(
        ranking_table=[_ranking_table_row()],
        ticker_cards=[_ticker_card_eligible()],
    )
    vm_path = tmp_path / "vm.json"
    vm_path.write_text(json.dumps(vm), encoding="utf-8")
    for forbidden in (
        "cache/results/board.html",
        "cache/status/board.html",
        "output/research_artifacts/board.html",
        "output/stackbuilder/board.html",
        "signal_library/data/stable/board.html",
    ):
        rc = rnd.main([
            "--view-model", str(vm_path),
            "--output", forbidden,
        ])
        assert rc == 2, (
            f"production root {forbidden} should be "
            f"refused with rc=2"
        )
    # Direct helper call also raises.
    import pytest
    for forbidden in (
        "cache/results/foo.html",
        "/abs/path/output/stackbuilder/foo.html",
        "C:/anywhere/signal_library/data/stable/foo.html",
    ):
        with pytest.raises(ValueError):
            rnd._refuse_production_root(forbidden)


# ---------------------------------------------------------------------------
# 11. Schema-error view model -> error shell
# ---------------------------------------------------------------------------


def test_schema_error_view_model_renders_error_shell():
    err = {
        "schema_version": None,
        "view_model_version": (
            "confluence_website_reader_view_v1"
        ),
        "schema_version_seen": "wrong_v2",
        "status_banner": {
            "kind": "schema_error",
            "headline": "Bad schema.",
            "body": "Reader expected ... got 'wrong_v2'.",
            "error_code": "schema_version_mismatch",
        },
    }
    html_text = rnd.build_static_board_html(err)
    assert "error-shell" in html_text
    assert "schema_version_mismatch" in html_text
    assert "wrong_v2" in html_text
    assert "Schema version seen" in html_text
    # Make sure we did NOT render fake ranking-row <tr>
    # elements. (The substring "ranking-row" may appear in
    # inline CSS rules; the actual <tr class="ranking-row"
    # ...> opening tag must be absent.)
    assert '<tr class="ranking-row"' not in html_text
    assert '<tr class="blocked-row"' not in html_text


def test_non_mapping_input_renders_error_shell():
    html_text = rnd.build_static_board_html("not a vm")  # type: ignore[arg-type]
    assert "error-shell" in html_text
    assert "non_mapping_view_model" in html_text


# ---------------------------------------------------------------------------
# 12. CLI stdin path
# ---------------------------------------------------------------------------


def test_cli_stdin_path_works(capsys, monkeypatch):
    vm = _make_view_model(
        ranking_table=[_ranking_table_row()],
        ticker_cards=[_ticker_card_eligible()],
    )
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps(vm)),
    )
    rc = rnd.main(["--stdin"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "<!DOCTYPE html>" in out


# ---------------------------------------------------------------------------
# 13. Loading from a Phase 6I-35 package via reader/view
# ---------------------------------------------------------------------------


def test_cli_package_path_uses_reader_view_builder(
    tmp_path, capsys,
):
    """When --package is supplied, the CLI delegates to the
    Phase 6I-36 reader/view to build the view model. Use a
    minimally valid Phase 6I-35 package."""
    package = {
        "schema_version": "confluence_website_export_v1",
        "generated_at": "2026-05-14T00:00:00+00:00",
        "ranking_rows": [],
        "blocked_rows": [],
        "ticker_details": {},
        "inspected_count": 0,
        "eligible_count": 0,
        "blocked_count": 0,
        "empty_state": {
            "headline": "No tickers inspected.",
            "reason": "...",
            "next_action": "...",
            "blocked_count": 0,
            "sample_blockers": [],
        },
        "sortable_columns": [
            "total_capture_pct", "sharpe_ratio",
            "trigger_days", "rank", "ticker",
        ],
        "default_sort": [
            {"column_id": "sharpe_ratio",
             "direction": "desc"},
        ],
        "sort_options": [],
        "remaining_limitations": [],
    }
    p = tmp_path / "package.json"
    p.write_text(json.dumps(package), encoding="utf-8")
    rc = rnd.main(["--package", str(p)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "<!DOCTYPE html>" in out


# ---------------------------------------------------------------------------
# 14. Static forbidden-import / write guards
# ---------------------------------------------------------------------------


def test_module_no_forbidden_top_level_imports():
    src = Path(rnd.__file__).read_text(encoding="utf-8")
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


def test_module_no_raw_pickle_load():
    src = Path(rnd.__file__).read_text(encoding="utf-8")
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


def test_module_no_resample_or_ffill_calls():
    """AST-scan to confirm no call to ``.resample()`` /
    ``.ffill()``. (A bare substring scan would false-positive
    on the docstring text that mentions these methods by name.)"""
    src = Path(rnd.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in ("resample", "ffill"):
                    offenders.append((node.lineno, func.attr))
    assert not offenders, (
        f"module calls forbidden method(s) at: {offenders!r}"
    )


def test_module_no_write_true_kwarg():
    src = Path(rnd.__file__).read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------
# 15. Smoke -- minimal view model is self-contained
# ---------------------------------------------------------------------------


def test_minimal_view_model_does_not_crash():
    """A view model with only the schema_version key must
    not crash the renderer; everything else falls through
    to safe defaults."""
    minimal = {
        "schema_version": "confluence_website_export_v1",
        "status_banner": {
            "kind": "no_tickers_inspected",
            "headline": "No tickers inspected.",
            "body": "...",
        },
    }
    html_text = rnd.build_static_board_html(minimal)
    assert html_text.startswith("<!DOCTYPE html>")
    assert "No tickers inspected." in html_text


def test_html_is_self_contained_no_external_cdn():
    vm = _make_view_model(
        ranking_table=[_ranking_table_row()],
        ticker_cards=[_ticker_card_eligible()],
    )
    html_text = rnd.build_static_board_html(vm)
    # No external script / stylesheet / image link.
    assert "https://" not in html_text or (
        # Only allowable strings -- the URL inside JSON
        # payloads embedded in the ticker-detail JSON might
        # carry artifact_path values, which are NOT
        # external. Confirm no <script src= or <link rel=
        # stylesheet href= referencing http(s).
        "<script src" not in html_text
        and "stylesheet" not in html_text.lower()
    )
    assert "<script src=" not in html_text
    assert '<link rel="stylesheet"' not in html_text
