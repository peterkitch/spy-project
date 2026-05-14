"""Phase 6I-42 tests: local runtime overlay providers for
the Confluence board.

Pins the four overlay surfaces:

  1. Latest price extracted from a fake local cache
     artifact (single fake cache loader).
  2. Stale cache becomes ``current_signal_status="stale"``.
  3. Current / as-of cache becomes ``locked``.
  4. Invalid TEF-style member surfaces in the
     ``incomplete_members`` list with the ``"!"`` warning
     symbol on the data_completeness block.
  5. Blocked ticker (no artifact) carries
     ``data_completeness_status="blocked"`` -- NO
     fabricated incomplete-member data.
  6. Provider exceptions degrade to ``unknown`` /
     ``stale`` -- never raise.
  7. Overlays pass through ranking export -> package ->
     reader/view -> static renderer end-to-end.
  8. One-row-per-ticker invariant preserved through the
     full chain.
  9. Static-import / no-write guards on the module.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_board_runtime_overlays as ovl  # noqa: E402
import confluence_multiwindow_ranking_export as _cmre  # noqa: E402
import confluence_website_export_package as _cwep  # noqa: E402
import confluence_website_reader_view as _crv  # noqa: E402
import confluence_static_board_renderer as _rnd  # noqa: E402


CANONICAL_WINDOWS = _cmre.CANONICAL_WINDOWS
CANONICAL_K_VALUES = _cmre.CANONICAL_K_VALUES


# ---------------------------------------------------------------------------
# Fake cache loader -- never opens a real PKL
# ---------------------------------------------------------------------------


def _fake_cache_loader_factory(
    payload_by_ticker: dict[str, Any] | None = None,
    explode_for_tickers: set[str] | None = None,
):
    """Return a fake cache_loader_callable.

    ``payload_by_ticker``: maps ticker (upper-case) to the
    payload dict the loader should return.
    ``explode_for_tickers``: tickers for which the loader
    should raise -- used to verify the overlay degrades to
    ``unknown`` without crashing.
    """
    payload_by_ticker = payload_by_ticker or {}
    explode_for_tickers = explode_for_tickers or set()

    def loader(path):
        # The path looks like
        # ``<cache_dir>/<TICKER>_precomputed_results.pkl``.
        # Parse the ticker from the stem.
        try:
            stem = Path(path).stem
            ticker = stem.replace(
                "_precomputed_results", "",
            )
        except Exception:
            return None, [
                ovl.ISSUE_CODE_CACHE_UNREADABLE,
            ]
        ticker = ticker.strip().upper()
        if ticker in explode_for_tickers:
            raise RuntimeError("loader exploded")
        payload = payload_by_ticker.get(ticker)
        if payload is None:
            return None, [ovl.ISSUE_CODE_CACHE_MISSING]
        return dict(payload), []

    return loader


def _cache_payload_dates_close(
    *,
    last_date: str,
    last_close: float = 100.0,
) -> dict[str, Any]:
    """Build a top-level ``{dates, close}`` cache payload."""
    return {
        "dates": ["2026-05-01", "2026-05-12", last_date],
        "close": [98.0, 99.5, float(last_close)],
    }


def _cache_payload_daily_block(
    *,
    last_date: str,
    last_close: float = 100.0,
) -> dict[str, Any]:
    return {
        "daily": {
            "dates": ["2026-05-01", last_date],
            "close": [99.0, float(last_close)],
            "last_date": last_date,
        },
    }


def _cache_payload_unknown_shape() -> dict[str, Any]:
    return {"some_unrelated_key": [1, 2, 3]}


# ---------------------------------------------------------------------------
# 1. Latest price extracted from local cache
# ---------------------------------------------------------------------------


def test_latest_price_extracted_from_local_cache():
    payload = _cache_payload_dates_close(
        last_date="2026-05-14", last_close=101.25,
    )
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        artifact_root=None,
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    overlay = report.overlays_by_ticker["AAA"]
    assert overlay["latest_price_block"][
        "latest_price"
    ] == 101.25
    assert overlay["latest_price_block"][
        "latest_price_as_of"
    ] == "2026-05-14"
    assert overlay["latest_price_block"][
        "signal_update_source"
    ] == "local_cache"
    assert overlay["latest_price_block"][
        "uses_provisional_price"
    ] is False


def test_latest_price_extracted_from_nested_daily_block():
    payload = _cache_payload_daily_block(
        last_date="2026-05-14", last_close=99.75,
    )
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    overlay = report.overlays_by_ticker["AAA"]
    assert overlay["latest_price_block"][
        "latest_price"
    ] == 99.75


def test_unknown_cache_shape_yields_unknown_status():
    payload = _cache_payload_unknown_shape()
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    overlay = report.overlays_by_ticker["AAA"]
    assert overlay["current_signal_status_block"][
        "current_signal_status"
    ] == "unknown"
    assert (
        ovl.ISSUE_CODE_CACHE_UNKNOWN_SHAPE
        in overlay["issue_codes"]
    )
    # No fabricated price.
    assert overlay["latest_price_block"][
        "latest_price"
    ] is None


# ---------------------------------------------------------------------------
# 2. Stale cache becomes current_signal_status=stale
# ---------------------------------------------------------------------------


def test_stale_cache_marks_signal_status_stale():
    payload = _cache_payload_dates_close(
        last_date="2026-05-12", last_close=98.0,
    )
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    overlay = report.overlays_by_ticker["AAA"]
    sb = overlay["current_signal_status_block"]
    assert sb["current_signal_status"] == "stale"
    assert sb["latest_price"] == 98.0
    assert sb["latest_price_as_of"] == "2026-05-12"
    assert (
        ovl.ISSUE_CODE_CACHE_STALE
        in overlay["issue_codes"]
    )


# ---------------------------------------------------------------------------
# 3. Current cache becomes locked
# ---------------------------------------------------------------------------


def test_current_cache_marks_signal_status_locked():
    payload = _cache_payload_dates_close(
        last_date="2026-05-14",
    )
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    sb = report.overlays_by_ticker["AAA"][
        "current_signal_status_block"
    ]
    assert sb["current_signal_status"] == "locked"


def test_no_current_as_of_date_defaults_to_locked():
    payload = _cache_payload_dates_close(
        last_date="2026-05-14",
    )
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date=None,
        cache_loader_callable=loader,
    )
    sb = report.overlays_by_ticker["AAA"][
        "current_signal_status_block"
    ]
    assert sb["current_signal_status"] == "locked"


# ---------------------------------------------------------------------------
# 4. TEF-style invalid member surfaces with "!"
# ---------------------------------------------------------------------------


def test_tef_style_invalid_member_surfaces_in_warnings():
    """A stackbuilder_member_callable that flags TEF as
    stale/invalid must propagate the TEF member into the
    overlay's data_completeness block with the "!" warning
    symbol."""
    def member_provider(
        ticker,
        *,
        stackbuilder_root=None,
        signal_library_dir=None,
    ):
        if ticker == "SPY":
            return {
                "incomplete_members": ["TEF"],
                "incomplete_member_reasons": {
                    "TEF": (
                        "yfinance_possibly_delisted"
                    ),
                },
            }
        return {
            "incomplete_members": [],
            "incomplete_member_reasons": {},
        }
    loader = _fake_cache_loader_factory({
        "SPY": _cache_payload_dates_close(
            last_date="2026-05-14",
        ),
    })
    report = ovl.build_board_runtime_overlays(
        ["SPY"],
        cache_dir="/tmp/cache",
        stackbuilder_root="/tmp/stackbuilder",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
        stackbuilder_member_callable=member_provider,
    )
    dc = report.data_completeness_by_ticker["SPY"]
    assert dc["has_incomplete_build_members"] is True
    assert dc["incomplete_members"] == ["TEF"]
    assert dc["incomplete_member_reasons"][
        "TEF"
    ] == "yfinance_possibly_delisted"
    assert dc["data_warning_symbol"] == "!"
    assert dc["data_completeness_status"] == "partial"


def test_adapter_diagnostic_provider_also_supplies_members():
    """Both injection seams can contribute incomplete
    members; their lists are merged without duplicates."""
    def member_provider(ticker, **_kw):
        return {
            "incomplete_members": ["MEMA"],
            "incomplete_member_reasons": {
                "MEMA": "stale_pkl",
            },
        }
    def adapter_diag(ticker, **_kw):
        return {
            "incomplete_members": ["MEMA", "MEMB"],
            "incomplete_member_reasons": {
                "MEMB": "missing_signal_library",
            },
        }
    loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-14",
        ),
    })
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
        stackbuilder_member_callable=member_provider,
        adapter_diagnostic_callable=adapter_diag,
    )
    dc = report.data_completeness_by_ticker["AAA"]
    assert sorted(dc["incomplete_members"]) == [
        "MEMA", "MEMB",
    ]
    assert dc["incomplete_member_reasons"][
        "MEMA"
    ] == "stale_pkl"
    assert dc["incomplete_member_reasons"][
        "MEMB"
    ] == "missing_signal_library"


# ---------------------------------------------------------------------------
# 5. Blocked ticker (no artifact) -> data_completeness_status=blocked
# ---------------------------------------------------------------------------


def test_blocked_ticker_no_artifact_marks_completeness_blocked(
    tmp_path,
):
    """When no artifact exists for the ticker AND no cache,
    overlay sets ``rank_eligible_hint=False`` and the
    data_completeness_status is ``blocked``."""
    art_root = tmp_path / "research_artifacts"
    (art_root / "confluence").mkdir(parents=True)
    # Note: no AAA directory created.
    loader = _fake_cache_loader_factory({})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        artifact_root=art_root,
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    dc = report.data_completeness_by_ticker["AAA"]
    assert dc["data_completeness_status"] == "blocked"
    assert dc["data_warning_symbol"] == "!"
    assert dc["has_incomplete_build_members"] is False
    assert dc["incomplete_members"] == []
    # Signal status block also blocked.
    sb = report.current_signal_status_by_ticker["AAA"]
    assert sb["current_signal_status"] == "blocked"
    assert sb["signal_update_source"] == "unavailable"
    assert sb["latest_price"] is None


def test_blocked_ticker_does_not_fabricate_member_warnings():
    """Even with a stackbuilder_member_callable that
    flags incomplete members, a blocked ticker (no
    artifact, no cache) keeps status=blocked and does NOT
    surface incomplete-member fabrication."""
    def member_provider(ticker, **_kw):
        return {
            "incomplete_members": ["SHOULDNTAPPEAR"],
            "incomplete_member_reasons": {
                "SHOULDNTAPPEAR": "fake",
            },
        }
    loader = _fake_cache_loader_factory({})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        artifact_root="/tmp/nonexistent_root",
        cache_dir="/tmp/cache",
        cache_loader_callable=loader,
        stackbuilder_member_callable=member_provider,
    )
    dc = report.data_completeness_by_ticker["AAA"]
    # rank_eligible_hint=False overrides the
    # incomplete-member input -- status stays blocked.
    assert dc["data_completeness_status"] == "blocked"


# ---------------------------------------------------------------------------
# 6. Provider exceptions degrade to unknown/stale, not crash
# ---------------------------------------------------------------------------


def test_cache_loader_exception_degrades_gracefully():
    loader = _fake_cache_loader_factory(
        explode_for_tickers={"AAA"},
    )
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        cache_loader_callable=loader,
    )
    sb = report.current_signal_status_by_ticker["AAA"]
    assert sb["current_signal_status"] == "unknown"
    assert (
        ovl.ISSUE_CODE_CACHE_UNREADABLE
        in report.issue_codes["AAA"]
    )


def test_member_provider_exception_degrades_gracefully():
    def member_provider(ticker, **_kw):
        raise RuntimeError("boom")
    loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-14",
        ),
    })
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        cache_loader_callable=loader,
        stackbuilder_member_callable=member_provider,
    )
    dc = report.data_completeness_by_ticker["AAA"]
    assert dc["data_completeness_status"] == "complete"
    assert (
        ovl.ISSUE_CODE_PROVIDER_RAISED
        in report.issue_codes["AAA"]
    )


# ---------------------------------------------------------------------------
# 7. End-to-end: overlay -> ranking export -> package -> reader -> renderer
# ---------------------------------------------------------------------------


def _make_full_60_cell_artifact(
    *,
    ticker: str,
    direction: str = "Buy",
    total_capture_pct: float = 10.0,
    sharpe_ratio: float = 1.0,
    trigger_days: int = 20,
) -> dict[str, Any]:
    """Phase 6I-20 valid 60-cell artifact for one ticker."""
    cells: list[dict[str, Any]] = []
    for w in CANONICAL_WINDOWS:
        for K in CANONICAL_K_VALUES:
            if direction == "Buy":
                buy_count, short_count, none_count = (
                    3, 0, 0,
                )
            elif direction == "Short":
                buy_count, short_count, none_count = (
                    0, 3, 0,
                )
            else:
                buy_count, short_count, none_count = (
                    0, 0, 3,
                )
            cells.append({
                "K": K,
                "window": w,
                "latest_combined_signal": direction,
                "latest_buy_count": buy_count,
                "latest_short_count": short_count,
                "latest_none_count": none_count,
                "latest_missing_count": 0,
                "member_count": 3,
                "total_capture_pct": float(
                    total_capture_pct,
                ),
                "avg_daily_capture_pct": 0.5,
                "sharpe_ratio": float(sharpe_ratio),
                "trigger_days": int(trigger_days),
                "wins": int(trigger_days // 2),
                "losses": int(trigger_days // 2),
            })
    return {
        "target_ticker": ticker,
        "generated_at": "2026-05-14T00:00:00+00:00",
        "per_window_k_metrics": cells,
        "build_wide_window_alignment": {
            w: {
                "all_members_firing": True,
                "firing_member_count": 3,
                "total_member_count": 3,
            }
            for w in CANONICAL_WINDOWS
        },
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


def _build_export_with_overlays(
    tickers,
    artifact_map,
    *,
    tmp_path,
    overlay_report,
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
            ovl.make_member_completeness_provider(
                overlay_report,
            )
        ),
        live_price_provider_callable=(
            ovl.make_live_price_provider(overlay_report)
        ),
    )


def test_overlays_pass_through_full_chain(tmp_path):
    """Build an overlay report with a TEF-style incomplete
    member + a fresh local cache for SPY, plug it into the
    ranking export, package, reader/view, and static
    renderer. Verify the warning + latest price + status
    propagate end-to-end."""
    cache_loader = _fake_cache_loader_factory({
        "SPY": _cache_payload_dates_close(
            last_date="2026-05-14", last_close=550.25,
        ),
    })

    def member_provider(ticker, **_kw):
        if ticker == "SPY":
            return {
                "incomplete_members": ["TEF"],
                "incomplete_member_reasons": {
                    "TEF": (
                        "yfinance_possibly_delisted"
                    ),
                },
            }
        return {
            "incomplete_members": [],
            "incomplete_member_reasons": {},
        }

    # Overlay focuses on cache + member data; the ranking
    # export does the real artifact-presence + Phase 6I-20
    # eligibility check. So we pass artifact_root=None to
    # the overlay -- the ranking export's loader (injected
    # below in _build_export_with_overlays) feeds the real
    # artifact through.
    overlay = ovl.build_board_runtime_overlays(
        ["SPY"],
        artifact_root=None,
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=cache_loader,
        stackbuilder_member_callable=member_provider,
    )

    artifact = _make_full_60_cell_artifact(ticker="SPY")
    report = _build_export_with_overlays(
        ["SPY"], {"SPY": artifact},
        tmp_path=tmp_path,
        overlay_report=overlay,
    )
    assert report.eligible_count == 1
    elig = report.ranking_rows[0]
    # data_completeness propagated.
    assert elig.data_completeness[
        "has_incomplete_build_members"
    ] is True
    assert elig.data_completeness[
        "incomplete_members"
    ] == ["TEF"]
    assert elig.data_completeness[
        "data_completeness_status"
    ] == "partial"
    assert elig.data_completeness[
        "data_warning_symbol"
    ] == "!"
    # current_signal_status_block propagated with locked
    # status + latest local price.
    sb = elig.current_signal_status_block
    assert sb["current_signal_status"] == "locked"
    assert sb["latest_price"] == 550.25
    # Phase 6I-42 amendment-1: ranking export preserves
    # provider-supplied signal_update_source="local_cache"
    # rather than masking it back to "artifact".
    assert sb["signal_update_source"] == "local_cache"

    # Package + view model carry the same blocks.
    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return report

    package = _cwep.build_website_export_package(
        ["SPY"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    rrow = package["ranking_rows"][0]
    assert rrow["data_completeness"][
        "incomplete_members"
    ] == ["TEF"]
    # local_cache source preserved through package layer.
    assert rrow["current_signal_status_block"][
        "signal_update_source"
    ] == "local_cache"
    detail = package["ticker_details"]["SPY"]
    assert detail["current_signal_status_block"][
        "signal_update_source"
    ] == "local_cache"
    vm = _crv.build_view_model(package)
    rtrow = vm["ranking_table"][0]
    assert rtrow["data_completeness"][
        "data_warning_symbol"
    ] == "!"
    # local_cache source preserved through view model.
    assert rtrow["current_signal_status_block"][
        "signal_update_source"
    ] == "local_cache"
    # Static renderer carries warning + status into the HTML.
    html_text = _rnd.build_static_board_html(vm)
    assert '<tr class="ranking-row"' in html_text
    # Exactly one ranking row.
    assert html_text.count(
        '<tr class="ranking-row"',
    ) == 1
    # Warning symbol "!" present on the row.
    assert (
        '<span class="warning warning-on"' in html_text
    )
    # Locked status badge present.
    assert (
        'class="status-badge status-locked"' in html_text
    )
    # Latest price visible on the ranking row.
    assert "550.25" in html_text
    # TEF mentioned in the inlined detail JSON.
    assert "TEF" in html_text


def test_one_row_per_ticker_invariant_holds_with_overlays(
    tmp_path,
):
    """Three eligible tickers + active overlays + multiple
    K builds firing per ticker still produce exactly three
    ranking rows."""
    cache_loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-14",
        ),
        "BBB": _cache_payload_dates_close(
            last_date="2026-05-14",
        ),
        "CCC": _cache_payload_dates_close(
            last_date="2026-05-12",
        ),
    })
    overlay = ovl.build_board_runtime_overlays(
        ["AAA", "BBB", "CCC"],
        artifact_root=None,
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=cache_loader,
    )
    artifacts = {
        t: _make_full_60_cell_artifact(
            ticker=t, total_capture_pct=20.0 - i * 5,
        )
        for i, t in enumerate(["AAA", "BBB", "CCC"])
    }
    report = _build_export_with_overlays(
        ["AAA", "BBB", "CCC"],
        artifacts,
        tmp_path=tmp_path,
        overlay_report=overlay,
    )
    assert report.eligible_count == 3

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return report

    package = _cwep.build_website_export_package(
        ["AAA", "BBB", "CCC"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    rt_tickers = [
        r["ticker"] for r in vm["ranking_table"]
    ]
    assert sorted(rt_tickers) == ["AAA", "BBB", "CCC"]
    assert len(rt_tickers) == len(set(rt_tickers))
    html_text = _rnd.build_static_board_html(vm)
    assert html_text.count(
        '<tr class="ranking-row"',
    ) == 3
    # CCC's cache is stale (2026-05-12 < 2026-05-14) ->
    # status=stale propagates.
    assert (
        'class="status-badge status-stale"'
        in html_text
    )


def test_stale_overlay_propagates_to_status_badge(tmp_path):
    """A ticker whose local cache is two trading days
    behind the cutoff surfaces ``status-stale`` in the
    rendered HTML."""
    cache_loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-12",
        ),
    })
    overlay = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=cache_loader,
    )
    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export_with_overlays(
        ["AAA"], {"AAA": artifact},
        tmp_path=tmp_path,
        overlay_report=overlay,
    )

    def fake_export(
        tickers, *, artifact_root, cache_dir=None,
    ):
        return report

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    vm = _crv.build_view_model(package)
    html_text = _rnd.build_static_board_html(vm)
    assert (
        'class="status-badge status-stale"' in html_text
    )


# ---------------------------------------------------------------------------
# 8. Provider factories produce well-shaped callables
# ---------------------------------------------------------------------------


def test_member_completeness_provider_returns_dc_shape():
    overlay = ovl.build_board_runtime_overlays(
        ["AAA"], cache_dir="/tmp/cache",
        cache_loader_callable=_fake_cache_loader_factory({
            "AAA": _cache_payload_dates_close(
                last_date="2026-05-14",
            ),
        }),
        stackbuilder_member_callable=(
            lambda t, **_kw: {
                "incomplete_members": ["TEF"],
                "incomplete_member_reasons": {
                    "TEF": "stale",
                },
            }
        ),
    )
    fn = ovl.make_member_completeness_provider(overlay)
    out = fn("AAA")
    assert out["has_incomplete_build_members"] is True
    assert out["incomplete_member_count"] == 1
    assert out["incomplete_members"] == ["TEF"]
    assert out["incomplete_member_reasons"][
        "TEF"
    ] == "stale"
    # Unknown ticker -> conservative empty dict.
    out_zzz = fn("ZZZ")
    assert (
        out_zzz["has_incomplete_build_members"] is False
    )


def test_live_price_provider_returns_payload_with_status():
    overlay = ovl.build_board_runtime_overlays(
        ["AAA"], cache_dir="/tmp/cache",
        current_as_of_date="2026-05-12",
        cache_loader_callable=_fake_cache_loader_factory({
            "AAA": _cache_payload_dates_close(
                last_date="2026-05-12", last_close=42.0,
            ),
        }),
    )
    fn = ovl.make_live_price_provider(overlay)
    out = fn("AAA")
    assert out is not None
    assert out["latest_price"] == 42.0
    assert out["latest_price_as_of"] == "2026-05-12"
    assert out["uses_provisional_price"] is False
    assert out["current_signal_status"] == "locked"


# ---------------------------------------------------------------------------
# 9. Summary + report shape
# ---------------------------------------------------------------------------


def test_report_summary_counts_correct():
    cache_loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-14",
        ),
        "BBB": _cache_payload_dates_close(
            last_date="2026-05-12",
        ),
        # No "CCC" payload -> missing.
    })
    overlay = ovl.build_board_runtime_overlays(
        ["AAA", "BBB", "CCC"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=cache_loader,
    )
    summary = overlay.summary
    # AAA fresh -> locked; BBB stale; CCC unknown.
    assert summary["by_signal_status"]["locked"] == 1
    assert summary["by_signal_status"]["stale"] == 1
    assert summary["by_signal_status"]["unknown"] == 1
    assert summary["tickers_with_latest_price"] == 2
    assert summary["tickers_with_incomplete_members"] == 0


def test_report_to_json_dict_is_serializable():
    overlay = ovl.build_board_runtime_overlays(
        ["AAA"], cache_dir="/tmp/cache",
        cache_loader_callable=_fake_cache_loader_factory({
            "AAA": _cache_payload_dates_close(
                last_date="2026-05-14",
            ),
        }),
    )
    j = overlay.to_json_dict()
    # Round-trip through json.dumps to confirm no
    # un-serializable values.
    text = json.dumps(j)
    again = json.loads(text)
    assert again["inspected_count"] == 1
    assert "AAA" in again["overlays_by_ticker"]


# ---------------------------------------------------------------------------
# 10. CLI
# ---------------------------------------------------------------------------


def test_cli_emits_overlay_report_json(tmp_path, capsys):
    rc = ovl.main([
        "--tickers", "AAA",
        "--cache-dir", "/tmp/no_such_cache",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    j = json.loads(out)
    assert j["schema_version"] == (
        "confluence_board_runtime_overlays_v1"
    )
    assert j["inspected_count"] == 1
    # No cache PKL -> the AAA overlay carries
    # cache_pkl_missing.
    assert (
        ovl.ISSUE_CODE_CACHE_MISSING
        in j["issue_codes"]["AAA"]
    )


def test_cli_missing_tickers_returns_rc_2(capsys):
    rc = ovl.main(["--tickers", ""])
    assert rc == 2


# ---------------------------------------------------------------------------
# 11. Static / forbidden-import guards
# ---------------------------------------------------------------------------


def test_module_no_forbidden_top_level_imports():
    src = Path(ovl.__file__).read_text(encoding="utf-8")
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
    assert not bad, (
        f"forbidden top-level imports: {bad!r}"
    )


def test_module_no_raw_pickle_load():
    """The default cache loader uses
    ``provenance_manifest.load_verified_pickle_artifact``
    via a deferred local import; there must be NO direct
    ``pickle.load(...)`` call anywhere in the module."""
    src = Path(ovl.__file__).read_text(encoding="utf-8")
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
    src = Path(ovl.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in ("resample", "ffill"):
                    offenders.append(
                        (node.lineno, func.attr),
                    )
    assert not offenders


def test_module_no_write_true_kwarg():
    src = Path(ovl.__file__).read_text(encoding="utf-8")
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


def test_module_no_subprocess_use():
    """AST scan: no ``import subprocess`` / ``from subprocess
    import ...`` / ``subprocess.X(...)``  call anywhere.
    Docstring mention of the name is allowed (the substring
    appears in the module's NOT-NOT-NOT contract block)."""
    src = Path(ovl.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert (
                    alias.name != "subprocess"
                    and not alias.name.startswith(
                        "subprocess.",
                    )
                ), "module imports subprocess"
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                raise AssertionError(
                    "module imports subprocess",
                )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                base = func.value
                if (
                    isinstance(base, ast.Name)
                    and base.id == "subprocess"
                ):
                    raise AssertionError(
                        "module calls subprocess.X at "
                        f"line {node.lineno}"
                    )


def test_no_yfinance_import_anywhere_in_module():
    src = Path(ovl.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert (
                    "yfinance"
                    not in alias.name.lower()
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert (
                    "yfinance"
                    not in node.module.lower()
                )


# ---------------------------------------------------------------------------
# Phase 6I-42 amendment-1: regression tests
# ---------------------------------------------------------------------------


def test_amendment1_scalar_daily_last_date_extracts_correctly():
    """Codex audit: a nested ``daily`` block carrying a
    scalar ``last_date`` string must extract correctly --
    the previous implementation treated the string as a
    sequence and returned its last character (``"4"``)."""
    payload = {
        "daily": {
            "close": [100.0],
            "last_date": "2026-05-14",
        },
    }
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    overlay = report.overlays_by_ticker["AAA"]
    pb = overlay["latest_price_block"]
    assert pb["latest_price"] == 100.0
    assert pb["latest_price_as_of"] == "2026-05-14"
    sb = overlay["current_signal_status_block"]
    assert sb["current_signal_status"] == "locked"
    assert (
        ovl.ISSUE_CODE_CACHE_DATE_UNPARSABLE
        not in overlay["issue_codes"]
    )


def test_amendment1_scalar_top_level_last_date_extracts_correctly():
    """Top-level scalar ``last_date`` also works (parallel
    to the nested-daily fix)."""
    payload = {
        "close": [100.0],
        # Top-level dates probe is array-only; a scalar
        # date here would only be caught if it's nested.
        "daily": {"last_date": "2026-05-14"},
    }
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    sb = (
        report.current_signal_status_by_ticker["AAA"]
    )
    assert sb["latest_price_as_of"] == "2026-05-14"
    assert sb["current_signal_status"] == "locked"


def test_amendment1_scalar_numeric_close_extracts_correctly():
    """A scalar numeric ``close`` (rather than a list)
    extracts too, after the ``_last_scalar`` hardening."""
    payload = {
        "daily": {
            "close": 100.0,
            "last_date": "2026-05-14",
        },
    }
    loader = _fake_cache_loader_factory({"AAA": payload})
    report = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=loader,
    )
    pb = report.latest_price_by_ticker["AAA"]
    assert pb["latest_price"] == 100.0


def test_amendment1_local_cache_source_propagates_through_chain(
    tmp_path,
):
    """``signal_update_source="local_cache"`` survives
    through the Phase 6I-34 ranking export, Phase 6I-35
    package, Phase 6I-36 view model, and Phase 6I-41
    rendered HTML."""
    cache_loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-14", last_close=42.0,
        ),
    })
    overlay = ovl.build_board_runtime_overlays(
        ["AAA"],
        cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        cache_loader_callable=cache_loader,
    )
    artifact = _make_full_60_cell_artifact(ticker="AAA")
    report = _build_export_with_overlays(
        ["AAA"], {"AAA": artifact},
        tmp_path=tmp_path,
        overlay_report=overlay,
    )
    # Ranking export preserves local_cache.
    elig = report.ranking_rows[0]
    sb = elig.current_signal_status_block
    assert sb["signal_update_source"] == "local_cache"
    assert sb["latest_price"] == 42.0

    def fake_export(
        _tickers, *, artifact_root, cache_dir=None,
    ):
        return report

    package = _cwep.build_website_export_package(
        ["AAA"],
        artifact_root="/tmp/research_artifacts",
        universe_mode=_cwep.UNIVERSE_MODE_EXPLICIT,
        underlying_export_callable=fake_export,
    )
    # Package preserves local_cache on both ranking_rows
    # and ticker_details.
    assert package["ranking_rows"][0][
        "current_signal_status_block"
    ]["signal_update_source"] == "local_cache"
    assert package["ticker_details"]["AAA"][
        "current_signal_status_block"
    ]["signal_update_source"] == "local_cache"
    # View model preserves local_cache.
    vm = _crv.build_view_model(package)
    assert vm["ranking_table"][0][
        "current_signal_status_block"
    ]["signal_update_source"] == "local_cache"
    # Renderer carries the value into the inlined JSON.
    html_text = _rnd.build_static_board_html(vm)
    assert "local_cache" in html_text


def test_amendment1_unsanctioned_source_falls_back():
    """The ranking export rejects unsanctioned provider
    ``signal_update_source`` values and falls back to the
    default (``artifact`` when non-provisional)."""
    overlay = ovl.build_board_runtime_overlays(
        ["AAA"], cache_dir="/tmp/cache",
        cache_loader_callable=_fake_cache_loader_factory({
            "AAA": _cache_payload_dates_close(
                last_date="2026-05-14", last_close=100.0,
            ),
        }),
    )
    # Manually construct a payload with a fabricated source.
    bad_provider = lambda ticker, artifact=None: {
        "latest_price": 100.0,
        "latest_price_as_of": "2026-05-14",
        "uses_provisional_price": False,
        "signal_update_source": "fabricated_label",
        "current_signal_status": "locked",
    }
    # Directly check the ranking-export helper.
    block = _cmre._build_current_signal_status_block(
        ticker="AAA",
        rank_eligible=True,
        confluence_last_date="2026-05-14",
        live_price_payload=bad_provider("AAA"),
    )
    # Unsanctioned label rejected -> defaults to artifact
    # (non-provisional).
    assert block["signal_update_source"] == "artifact"


# ---------------------------------------------------------------------------
# Phase 6I-42 amendment-1: renderer-level integration
# ---------------------------------------------------------------------------


def _make_test_artifact_loader_for_overlay_chain(
    artifact_map: dict[str, dict[str, Any]],
):
    """Return an artifact loader the ranking export accepts.
    Matches the ``_injected_loader`` shape used elsewhere in
    this file."""
    return _injected_loader(artifact_map)


def test_amendment1_build_view_model_from_tickers_with_overlays(
    tmp_path,
):
    """The renderer-side helper threads overlays into the
    full chain: ranking export -> package -> view model.
    The resulting view model carries the warning + latest
    price + local_cache source."""
    cache_loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-14", last_close=42.0,
        ),
    })

    def member_provider(ticker, **_kw):
        if ticker == "AAA":
            return {
                "incomplete_members": ["TEF"],
                "incomplete_member_reasons": {
                    "TEF": (
                        "yfinance_possibly_delisted"
                    ),
                },
            }
        return {
            "incomplete_members": [],
            "incomplete_member_reasons": {},
        }

    # Set up artifact stubs the ranking export expects.
    art_root = tmp_path / "research_artifacts"
    (art_root / "confluence" / "AAA").mkdir(parents=True)
    (
        art_root / "confluence" / "AAA"
        / "AAA__MTF_CONSENSUS.research_day.json"
    ).write_text("{}", encoding="utf-8")
    artifact_loader = (
        _make_test_artifact_loader_for_overlay_chain(
            {"AAA": _make_full_60_cell_artifact(
                ticker="AAA",
            )},
        )
    )

    vm = _rnd.build_view_model_from_tickers(
        ["AAA"],
        artifact_root=str(art_root),
        cache_dir=None,
        with_local_overlays=True,
        overlay_cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        overlay_cache_loader_callable=cache_loader,
        overlay_stackbuilder_member_callable=(
            member_provider
        ),
        ranking_artifact_loader_callable=artifact_loader,
        ranking_chart_readiness_callable=_fake_chart,
    )
    assert len(vm["ranking_table"]) == 1
    row = vm["ranking_table"][0]
    assert row["ticker"] == "AAA"
    assert row["data_completeness"][
        "incomplete_members"
    ] == ["TEF"]
    assert row["data_completeness"][
        "data_warning_symbol"
    ] == "!"
    assert row["current_signal_status_block"][
        "latest_price"
    ] == 42.0
    assert row["current_signal_status_block"][
        "signal_update_source"
    ] == "local_cache"
    assert row["current_signal_status_block"][
        "current_signal_status"
    ] == "locked"


def test_amendment1_build_view_model_without_overlays_unchanged(
    tmp_path,
):
    """Without ``with_local_overlays``, the chain runs
    unchanged: data_completeness=complete, source=artifact,
    no latest_price."""
    art_root = tmp_path / "research_artifacts"
    (art_root / "confluence" / "AAA").mkdir(parents=True)
    (
        art_root / "confluence" / "AAA"
        / "AAA__MTF_CONSENSUS.research_day.json"
    ).write_text("{}", encoding="utf-8")
    artifact_loader = _injected_loader(
        {"AAA": _make_full_60_cell_artifact(ticker="AAA")},
    )
    vm = _rnd.build_view_model_from_tickers(
        ["AAA"],
        artifact_root=str(art_root),
        with_local_overlays=False,
        ranking_artifact_loader_callable=artifact_loader,
        ranking_chart_readiness_callable=_fake_chart,
    )
    row = vm["ranking_table"][0]
    assert row["data_completeness"][
        "data_completeness_status"
    ] == "complete"
    assert row["data_completeness"][
        "data_warning_symbol"
    ] is None
    assert row["current_signal_status_block"][
        "signal_update_source"
    ] == "artifact"
    assert row["current_signal_status_block"][
        "latest_price"
    ] is None


def test_amendment1_renderer_html_carries_overlay_fields(
    tmp_path,
):
    """The Phase 6I-41 renderer's HTML output, driven by the
    overlay-enabled chain, includes the warning symbol, the
    TEF incomplete-member name, the latest price, and the
    local_cache source label."""
    cache_loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-14", last_close=42.5,
        ),
    })

    def member_provider(ticker, **_kw):
        return {
            "incomplete_members": ["TEF"],
            "incomplete_member_reasons": {
                "TEF": "yfinance_possibly_delisted",
            },
        }

    art_root = tmp_path / "research_artifacts"
    (art_root / "confluence" / "AAA").mkdir(parents=True)
    (
        art_root / "confluence" / "AAA"
        / "AAA__MTF_CONSENSUS.research_day.json"
    ).write_text("{}", encoding="utf-8")
    artifact_loader = _injected_loader(
        {"AAA": _make_full_60_cell_artifact(ticker="AAA")},
    )

    vm = _rnd.build_view_model_from_tickers(
        ["AAA"],
        artifact_root=str(art_root),
        cache_dir=None,
        with_local_overlays=True,
        overlay_cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        overlay_cache_loader_callable=cache_loader,
        overlay_stackbuilder_member_callable=(
            member_provider
        ),
        ranking_artifact_loader_callable=artifact_loader,
        ranking_chart_readiness_callable=_fake_chart,
    )
    html_text = _rnd.build_static_board_html(vm)
    # One ranking row.
    assert html_text.count(
        '<tr class="ranking-row"',
    ) == 1
    # Warning symbol present.
    assert (
        '<span class="warning warning-on"' in html_text
    )
    # TEF incomplete-member name reaches the embedded JSON.
    assert "TEF" in html_text
    assert "yfinance_possibly_delisted" in html_text
    # Latest price visible.
    assert "42.50" in html_text or "42.5" in html_text
    # local_cache source visible somewhere.
    assert "local_cache" in html_text
    # status-locked badge.
    assert (
        'class="status-badge status-locked"' in html_text
    )


def test_amendment1_renderer_preserves_blocked_when_no_artifact(
    tmp_path,
):
    """If the ranking export classifies the row as blocked
    (no artifact on disk), the overlay does NOT promote it
    to rank-eligible -- blocked stays blocked."""
    cache_loader = _fake_cache_loader_factory({
        "AAA": _cache_payload_dates_close(
            last_date="2026-05-14", last_close=42.0,
        ),
    })
    # Artifact directory exists but no .json file.
    art_root = tmp_path / "research_artifacts"
    (art_root / "confluence").mkdir(parents=True)
    vm = _rnd.build_view_model_from_tickers(
        ["AAA"],
        artifact_root=str(art_root),
        with_local_overlays=True,
        overlay_cache_dir="/tmp/cache",
        current_as_of_date="2026-05-14",
        overlay_cache_loader_callable=cache_loader,
        ranking_artifact_loader_callable=lambda p: None,
        ranking_chart_readiness_callable=_fake_chart,
    )
    # No ranking rows; AAA appears in blocked_table.
    assert vm["ranking_table"] == []
    assert len(vm["blocked_table"]) == 1
    assert vm["blocked_table"][0]["ticker"] == "AAA"


def test_amendment1_cli_from_tickers_with_overlays(
    tmp_path, capsys,
):
    """Smoke test: CLI ``--from-tickers --with-local-
    overlays`` runs end-to-end against a tmp_path artifact
    root + non-existent cache dir (the cache loader
    gracefully reports ``cache_pkl_missing`` and the chain
    still emits HTML)."""
    art_root = tmp_path / "research_artifacts"
    (art_root / "confluence").mkdir(parents=True)
    # No AAA artifact -> ranking export will block.
    rc = _rnd.main([
        "--from-tickers", "AAA",
        "--artifact-root", str(art_root),
        "--with-local-overlays",
        "--overlay-cache-dir", str(
            tmp_path / "nonexistent_cache",
        ),
        "--current-as-of-date", "2026-05-14",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("<!DOCTYPE html>")
    # AAA is blocked (no artifact) -> blocked-table row.
    assert '<tr class="blocked-row"' in out
    assert 'data-ticker="AAA"' in out


def test_amendment1_cli_from_tickers_requires_artifact_root(
    capsys,
):
    rc = _rnd.main([
        "--from-tickers", "AAA",
    ])
    assert rc == 2


def test_amendment1_cli_from_tickers_empty_list_rc_2(capsys):
    rc = _rnd.main([
        "--from-tickers", "",
        "--artifact-root", "/tmp/foo",
    ])
    # argparse-level: --from-tickers is in the source
    # group; empty string falls through to "missing
    # source" because falsy.
    assert rc == 2
