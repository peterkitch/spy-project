"""Phase 6I-41: read-only static Confluence website board
renderer / UI shell.

Consumes a Phase 6I-36 view model (or a Phase 6I-35 package
which the reader/view converts) and emits a self-contained
HTML document with:

  * a header / status banner driven by the view model;
  * inline CSS for a dense operational dashboard layout
    (NOT a landing page);
  * sort controls using the Phase 6I-40 sort metadata
    (Sharpe, Total Capture %, Trigger Days, Rank, Ticker
    with asc/desc options);
  * a ranking table -- ONE ROW PER TICKER, never exploded
    by K or window (the Phase 6I-39 invariant);
  * a blocked-ticker table -- one line per blocked ticker
    with honest blocker + warning fields;
  * a ticker detail panel that renders the full
    primary_build_summary + current_build_signal_summary +
    60-cell current_build_signals matrix +
    data_completeness + current_signal_status_block +
    flip_risk placeholder block + chart readiness;
  * inline JavaScript for client-side sort / filter /
    search; NO external CDN required.

What this module IS NOT
-----------------------

  * NOT a writer / refresher / pipeline runner / batch
    engine.
  * NOT a chart drawer -- if the view model does not embed
    chart rows, the detail panel surfaces a clean chart
    placeholder with the source / blocker fields and NEVER
    fabricates a chart.
  * NOT a producer of new Phase 6I-20 fields -- it only
    renders what the upstream view model already carries.
  * NOT a route to source refresh or any production write.

Strictly read-only contract pins
--------------------------------

  * No top-level imports of yfinance / dash / subprocess /
    signal_engine_cache_refresher /
    signal_library_stable_promotion_writer /
    multiwindow_k_confluence_patch_writer /
    confluence_pipeline_runner /
    daily_board_automation_writer /
    daily_board_automation_executor / spymaster /
    trafficflow / stackbuilder / onepass / impactsearch /
    confluence / cross_ticker_confluence /
    daily_signal_board.
  * No raw ``pickle.load`` (B12 scope).
  * No ``.resample()`` / ``.ffill()``.
  * No ``write=True`` kwarg passed to any callable.
  * CLI ``--output`` writes ONLY when the resolved target
    path does not contain a known production root segment.
    A test that mistakenly aims for ``cache/results`` etc.
    is rejected with ``ValueError`` BEFORE the file is
    opened for writing.

Public surface
--------------

    PAGE_TITLE
    PRODUCTION_ROOT_SEGMENTS
    SORT_COLUMN_LABELS

    build_static_board_html(view_model) -> str
    load_view_model_from_path(path) -> dict[str, Any]
    load_view_model_from_stdin(stream=None) -> dict[str, Any]
    load_view_model_from_builder(
        builder_callable=None, **kwargs,
    ) -> dict[str, Any]

    main(argv=None) -> int            # CLI entry
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


import confluence_website_reader_view as _crv


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

PAGE_TITLE: str = "Confluence Multi-Ticker Ranking Board"

# Production roots the CLI ``--output`` MUST refuse. A test
# aiming any file under any of these segments is rejected
# with ValueError before the file is opened for writing.
PRODUCTION_ROOT_SEGMENTS: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
)


# Sort column labels exposed in the UI control. Wired to the
# Phase 6I-40 ``sort_options`` block by ``column_id``.
SORT_COLUMN_LABELS: dict[str, str] = {
    "rank": "Rank",
    "total_capture_pct": "Total Capture %",
    "sharpe_ratio": "Sharpe Ratio",
    "trigger_days": "Trigger Days",
    "ticker": "Ticker",
}


# Canonical windows for the 60-cell matrix grid.
_CANONICAL_WINDOWS: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)
_CANONICAL_K_VALUES: tuple[int, ...] = tuple(range(1, 13))


# ---------------------------------------------------------------------------
# Output-path guard
# ---------------------------------------------------------------------------


def _refuse_production_root(path: Any) -> None:
    """Raise ValueError if ``path`` resolves into a known
    production root segment. Used to harden the CLI
    ``--output`` flag.

    The check is intentionally string-based so a renamed or
    symlinked-into directory under a production root still
    trips the guard.
    """
    p = Path(path)
    # Inspect both the raw user-supplied path and the
    # resolved path so a relative path like
    # ``cache/results/foo.html`` is rejected without needing
    # to actually exist on disk.
    candidates: list[str] = []
    raw = str(p).replace("\\", "/").lower()
    candidates.append(raw)
    try:
        resolved = p.resolve()
        candidates.append(
            str(resolved).replace("\\", "/").lower(),
        )
    except Exception:
        # ``resolve()`` can fail on Windows for paths whose
        # parents don't exist; falling back to the raw string
        # is conservative.
        pass
    for prod in PRODUCTION_ROOT_SEGMENTS:
        for cand in candidates:
            if prod in cand:
                raise ValueError(
                    "refuses to write under production "
                    f"root segment: {prod}"
                )


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


def load_view_model_from_path(path: Any) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    obj = json.loads(text)
    if not isinstance(obj, Mapping):
        raise ValueError(
            f"view model at {path} did not parse to a "
            "JSON object"
        )
    return dict(obj)


def load_view_model_from_stdin(
    stream: Optional[Any] = None,
) -> dict[str, Any]:
    src = stream if stream is not None else sys.stdin
    obj = json.loads(src.read())
    if not isinstance(obj, Mapping):
        raise ValueError(
            "view model on stdin did not parse to a JSON "
            "object"
        )
    return dict(obj)


def load_view_model_from_builder(
    builder_callable: Optional[Callable[..., Any]] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Invoke the Phase 6I-36 reader/view builder in-process.

    Default delegates to
    ``confluence_website_reader_view.build_view_model``;
    tests inject a fake. The caller supplies a pre-built
    Phase 6I-35 ``package`` mapping via ``package=...``.
    """
    fn = builder_callable or _crv.build_view_model
    result = fn(**kwargs)
    if not isinstance(result, Mapping):
        raise TypeError(
            "builder_callable must return a Mapping; got "
            f"{type(result).__name__}"
        )
    return dict(result)


# ---------------------------------------------------------------------------
# Phase 6I-42 amendment-1: from-tickers + local-overlays integration path
# ---------------------------------------------------------------------------


def build_view_model_from_tickers(
    tickers: Sequence[str],
    *,
    artifact_root: Any,
    cache_dir: Optional[Any] = None,
    universe_mode: Optional[str] = None,
    with_local_overlays: bool = False,
    overlay_cache_dir: Optional[Any] = None,
    overlay_artifact_root: Optional[Any] = None,
    overlay_stackbuilder_root: Optional[Any] = None,
    overlay_signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    overlay_cache_loader_callable: Optional[
        Callable[..., Any]
    ] = None,
    overlay_stackbuilder_member_callable: Optional[
        Callable[..., Any]
    ] = None,
    overlay_adapter_diagnostic_callable: Optional[
        Callable[..., Any]
    ] = None,
    ranking_artifact_loader_callable: Optional[
        Callable[..., Any]
    ] = None,
    ranking_chart_readiness_callable: Optional[
        Callable[..., Any]
    ] = None,
) -> dict[str, Any]:
    """Build a Phase 6I-36 view model from raw tickers,
    optionally enriching with Phase 6I-42 local overlays.

    Read-only. Threads through::

        Phase 6I-42 overlay scan (when with_local_overlays)
          -> Phase 6I-34 build_multiwindow_ranking_export()
          -> Phase 6I-35 build_website_export_package()
          -> Phase 6I-36 build_view_model()

    All deeper imports are deferred so the renderer's
    top-level import surface stays small.
    """
    # Deferred imports.
    import confluence_board_runtime_overlays as _ovl
    import confluence_multiwindow_ranking_export as _cmre
    import confluence_website_export_package as _cwep

    ticker_list = [
        str(t).strip().upper()
        for t in tickers if str(t).strip()
    ]
    seen: set[str] = set()
    deduped: list[str] = []
    for t in ticker_list:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    ticker_list = deduped

    member_provider: Optional[Callable[..., Any]] = None
    live_price_provider: Optional[
        Callable[..., Any]
    ] = None
    if with_local_overlays:
        overlay_report = (
            _ovl.build_board_runtime_overlays(
                ticker_list,
                artifact_root=overlay_artifact_root,
                cache_dir=(
                    overlay_cache_dir
                    if overlay_cache_dir is not None
                    else cache_dir
                ),
                stackbuilder_root=(
                    overlay_stackbuilder_root
                ),
                signal_library_dir=(
                    overlay_signal_library_dir
                ),
                current_as_of_date=current_as_of_date,
                cache_loader_callable=(
                    overlay_cache_loader_callable
                ),
                stackbuilder_member_callable=(
                    overlay_stackbuilder_member_callable
                ),
                adapter_diagnostic_callable=(
                    overlay_adapter_diagnostic_callable
                ),
            )
        )
        member_provider = (
            _ovl.make_member_completeness_provider(
                overlay_report,
            )
        )
        live_price_provider = (
            _ovl.make_live_price_provider(overlay_report)
        )

    ranking_report = _cmre.build_multiwindow_ranking_export(
        ticker_list,
        artifact_root=artifact_root,
        cache_dir=cache_dir,
        artifact_loader_callable=(
            ranking_artifact_loader_callable
        ),
        chart_readiness_callable=(
            ranking_chart_readiness_callable
        ),
        member_completeness_provider_callable=(
            member_provider
        ),
        live_price_provider_callable=(
            live_price_provider
        ),
    )

    def _stub_underlying_export(
        _tickers,
        *,
        artifact_root=None,
        cache_dir=None,
    ):
        return ranking_report

    package = _cwep.build_website_export_package(
        ticker_list,
        artifact_root=artifact_root,
        cache_dir=cache_dir,
        universe_mode=(
            universe_mode
            or _cwep.UNIVERSE_MODE_EXPLICIT
        ),
        underlying_export_callable=(
            _stub_underlying_export
        ),
    )
    return _crv.build_view_model(package)


# ---------------------------------------------------------------------------
# HTML / JSON escaping helpers
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    """HTML-escape a value for embedding in element text /
    attribute. ``None`` renders as an em-dash placeholder
    so the UI never shows the literal string "None".
    """
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _json_for_html(value: Any) -> str:
    """Serialize ``value`` to JSON for embedding inside a
    ``<script>`` tag.

    Escapes every ``<`` as the JSON unicode escape
    ``\\u003c`` so no substring resembling an HTML tag
    (including ``</script>``) can appear inside the
    JSON body, regardless of provider-supplied content.
    Browser JSON parsers turn ``\\u003c`` back into ``<``
    on read.
    """
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text.replace("<", "\\u003c")


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "—"
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}%"
    return _esc(value)


def _fmt_sharpe(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "—"
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}"
    return _esc(value)


def _fmt_price(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "—"
    if isinstance(value, (int, float)):
        return f"${float(value):,.2f}"
    return _esc(value)


def _fmt_int(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "—"
    try:
        return f"{int(value):,d}"
    except (TypeError, ValueError):
        return _esc(value)


# ---------------------------------------------------------------------------
# Header / banner
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _build_head(view_model: Mapping[str, Any]) -> str:
    """Build ``<head>`` block with inline CSS."""
    title = _esc(view_model.get("page_title") or PAGE_TITLE)
    css = _INLINE_CSS
    return (
        f'<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, '
        f'initial-scale=1">\n'
        f'<title>{title}</title>\n'
        f'<style>{css}</style>'
    )


def _build_header(view_model: Mapping[str, Any]) -> str:
    title = _esc(view_model.get("page_title") or PAGE_TITLE)
    banner = view_model.get("status_banner")
    if not isinstance(banner, Mapping):
        banner = {}
    kind = _esc(banner.get("kind") or "unknown")
    headline = _esc(banner.get("headline") or "")
    body = _esc(banner.get("body") or "")
    generated_at = _esc(view_model.get("generated_at"))
    rendered_at = _esc(
        view_model.get("rendered_at") or _iso_now(),
    )
    eligible = _fmt_int(
        view_model.get("eligible_count", 0),
    )
    blocked = _fmt_int(view_model.get("blocked_count", 0))
    inspected = _fmt_int(
        view_model.get("inspected_count", 0),
    )
    display = _esc(
        view_model.get(
            "display_row_cardinality", "one_row_per_ticker",
        ),
    )
    view_model_version = _esc(
        view_model.get("view_model_version") or "",
    )
    schema_version = _esc(
        view_model.get("schema_version") or "",
    )
    return (
        f'<header class="board-header">\n'
        f'  <h1>{title}</h1>\n'
        f'  <div class="status-banner kind-{kind}" '
        f'data-kind="{kind}">\n'
        f'    <strong class="banner-headline">{headline}'
        f'</strong>\n'
        f'    <p class="banner-body">{body}</p>\n'
        f'  </div>\n'
        f'  <ul class="summary-strip">\n'
        f'    <li>Eligible: <span data-stat="eligible">'
        f'{eligible}</span></li>\n'
        f'    <li>Blocked: <span data-stat="blocked">'
        f'{blocked}</span></li>\n'
        f'    <li>Inspected: <span data-stat="inspected">'
        f'{inspected}</span></li>\n'
        f'    <li>Generated: <span data-stat="generated">'
        f'{generated_at}</span></li>\n'
        f'    <li>Rendered: <span data-stat="rendered">'
        f'{rendered_at}</span></li>\n'
        f'    <li>Schema: <code>{schema_version}</code> '
        f'· View: <code>{view_model_version}</code></li>\n'
        f'    <li>Display: <code>{display}</code></li>\n'
        f'  </ul>\n'
        f'</header>'
    )


# ---------------------------------------------------------------------------
# Sort + filter controls
# ---------------------------------------------------------------------------


def _build_controls(view_model: Mapping[str, Any]) -> str:
    sort_options = view_model.get("sort_options")
    if not isinstance(sort_options, list):
        sort_options = []
    default_sort = view_model.get("default_sort")
    if not isinstance(default_sort, list):
        default_sort = []
    primary_default_column = ""
    primary_default_direction = "desc"
    if default_sort and isinstance(
        default_sort[0], Mapping,
    ):
        primary_default_column = str(
            default_sort[0].get("column_id") or "",
        )
        primary_default_direction = str(
            default_sort[0].get("direction") or "desc",
        )

    sort_column_options: list[str] = []
    for opt in sort_options:
        if not isinstance(opt, Mapping):
            continue
        col = str(opt.get("column_id") or "")
        if not col:
            continue
        label = str(
            opt.get("label")
            or SORT_COLUMN_LABELS.get(col, col),
        )
        selected = (
            " selected" if col == primary_default_column
            else ""
        )
        sort_column_options.append(
            f'<option value="{_esc(col)}"{selected}>'
            f'{_esc(label)}</option>'
        )
    if not sort_column_options:
        # Conservative fallback so the renderer still emits
        # a usable control set even when the view model is
        # missing sort metadata.
        for col, label in SORT_COLUMN_LABELS.items():
            selected = (
                " selected"
                if col == "sharpe_ratio" else ""
            )
            sort_column_options.append(
                f'<option value="{_esc(col)}"{selected}>'
                f'{_esc(label)}</option>'
            )

    direction_asc_selected = (
        " selected"
        if primary_default_direction == "asc" else ""
    )
    direction_desc_selected = (
        " selected"
        if primary_default_direction != "asc" else ""
    )

    return (
        '<section class="controls" aria-label="Board controls">\n'
        '  <label class="control-search">Ticker search:\n'
        '    <input type="search" id="ticker-search" '
        'placeholder="filter by ticker">\n'
        '  </label>\n'
        '  <label class="control-sort-column">Sort column:\n'
        '    <select id="sort-column" '
        'data-default-column="'
        f'{_esc(primary_default_column)}">\n'
        '      '
        + '\n      '.join(sort_column_options)
        + '\n    </select>\n'
        '  </label>\n'
        '  <label class="control-sort-direction">Direction:\n'
        '    <select id="sort-direction">\n'
        f'      <option value="desc"{direction_desc_selected}>'
        'Descending</option>\n'
        f'      <option value="asc"{direction_asc_selected}>'
        'Ascending</option>\n'
        '    </select>\n'
        '  </label>\n'
        '  <label class="control-filter-signal">Signal status:\n'
        '    <select id="filter-signal-status">\n'
        '      <option value="">all</option>\n'
        '      <option value="locked">locked</option>\n'
        '      <option value="provisional">provisional</option>\n'
        '      <option value="stale">stale</option>\n'
        '      <option value="blocked">blocked</option>\n'
        '      <option value="unknown">unknown</option>\n'
        '    </select>\n'
        '  </label>\n'
        '  <label class="control-filter-completeness">Completeness:\n'
        '    <select id="filter-completeness">\n'
        '      <option value="">all</option>\n'
        '      <option value="complete">complete</option>\n'
        '      <option value="partial">partial</option>\n'
        '      <option value="blocked">blocked</option>\n'
        '      <option value="unknown">unknown</option>\n'
        '    </select>\n'
        '  </label>\n'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Ranking table
# ---------------------------------------------------------------------------


def _empty_state_html(
    view_model: Mapping[str, Any],
) -> str:
    es = view_model.get("empty_state")
    if not isinstance(es, Mapping):
        return (
            '<p class="empty-state">No eligible rows. No '
            'empty_state block in view model.</p>'
        )
    headline = _esc(es.get("headline") or "")
    reason = _esc(es.get("reason") or "")
    next_action = _esc(es.get("next_action") or "")
    blocked_count = _fmt_int(es.get("blocked_count", 0))
    sample_blockers = es.get("sample_blockers") or []
    sample_html_parts: list[str] = []
    if isinstance(sample_blockers, list):
        for b in sample_blockers[:5]:
            if not isinstance(b, Mapping):
                continue
            t = _esc(b.get("ticker"))
            r = _esc(b.get("ranking_blocked_reason"))
            ds = _esc(b.get("data_status"))
            sample_html_parts.append(
                f'<li><code>{t}</code>: '
                f'{r} (data_status=<code>{ds}</code>)</li>'
            )
    samples = "".join(sample_html_parts)
    return (
        '<section class="empty-state">\n'
        '  <h2>Empty state</h2>\n'
        f'  <p class="empty-state-headline"><strong>{headline}'
        f'</strong></p>\n'
        f'  <p class="empty-state-reason">{reason}</p>\n'
        f'  <p class="empty-state-next-action">Next action: '
        f'{next_action}</p>\n'
        f'  <p class="empty-state-blocked-count">Blocked: '
        f'<code>{blocked_count}</code></p>\n'
        + (
            f'  <ul class="empty-state-samples">{samples}</ul>'
            if samples else ""
        )
        + '\n</section>'
    )


def _render_warning_cell(
    completeness: Mapping[str, Any],
) -> str:
    sym = completeness.get("data_warning_symbol")
    status = _esc(
        completeness.get("data_completeness_status")
        or "unknown",
    )
    msg = _esc(
        completeness.get("data_completeness_message")
        or "",
    )
    if sym == "!":
        return (
            f'<span class="warning warning-on" '
            f'title="{msg}" data-status="{status}">!</span>'
        )
    return (
        f'<span class="warning warning-off" '
        f'data-status="{status}"></span>'
    )


def _render_chart_cell(row: Mapping[str, Any]) -> str:
    available = bool(row.get("chart_ready", False))
    if available:
        return (
            '<span class="chart-cell chart-ready">ready'
            '</span>'
        )
    return (
        '<span class="chart-cell chart-unavailable">—</span>'
    )


def _render_primary_build_cell(
    pb: Optional[Mapping[str, Any]],
) -> str:
    if not isinstance(pb, Mapping):
        return '<span class="primary-build na">—</span>'
    if not pb.get("primary_build_available"):
        return (
            '<span class="primary-build na" '
            'data-tier="none">—</span>'
        )
    K = pb.get("K")
    direction = pb.get("signal_direction")
    label = pb.get("label")
    tier = _esc(pb.get("selection_tier") or "")
    conflict = bool(pb.get("direction_conflict", False))
    label_text = label if isinstance(label, str) and label else None
    if label_text is None:
        # Construct a fallback label if the reader/view
        # didn't pre-format one.
        K_str = (
            f"K={int(K)}" if isinstance(K, int)
            and not isinstance(K, bool) else "K=?"
        )
        dir_str = (
            str(direction) if direction else "?"
        )
        label_text = f"{K_str} {dir_str}"
    cls = (
        "primary-build primary-build-conflict"
        if conflict else "primary-build"
    )
    return (
        f'<span class="{cls}" data-tier="{tier}" '
        f'data-direction-conflict="{str(conflict).lower()}">'
        f'{_esc(label_text)}</span>'
    )


def _render_same_k_status_cell(
    pb: Optional[Mapping[str, Any]],
) -> str:
    """Distinguish same-K aligned, same-K mixed, single
    cell, and none from the primary_build_summary tier."""
    if not isinstance(pb, Mapping):
        return (
            '<span class="same-k-status na">—</span>'
        )
    tier = pb.get("selection_tier")
    if tier == "same_k_all_windows_same_direction":
        return (
            '<span class="same-k-status same-k-aligned" '
            'data-tier="same_k_all_windows_same_direction">'
            'Same-K aligned</span>'
        )
    if tier == "same_k_all_windows_mixed_direction":
        return (
            '<span class="same-k-status same-k-mixed" '
            'data-tier="same_k_all_windows_mixed_direction">'
            'Same-K mixed</span>'
        )
    if tier == "strongest_current_cell":
        return (
            '<span class="same-k-status single-cell" '
            'data-tier="strongest_current_cell">'
            'Single cell</span>'
        )
    return (
        '<span class="same-k-status none" '
        'data-tier="none">—</span>'
    )


def _render_status_badge(
    block: Optional[Mapping[str, Any]],
) -> str:
    if not isinstance(block, Mapping):
        return (
            '<span class="status-badge status-unknown" '
            'data-status="unknown">unknown</span>'
        )
    status = str(
        block.get("current_signal_status") or "unknown",
    )
    return (
        f'<span class="status-badge status-{_esc(status)}" '
        f'data-status="{_esc(status)}">{_esc(status)}</span>'
    )


def _render_latest_price_cell(
    block: Optional[Mapping[str, Any]],
) -> str:
    if not isinstance(block, Mapping):
        return '<span class="latest-price na">—</span>'
    price = block.get("latest_price")
    as_of = block.get("latest_price_as_of")
    provisional = bool(
        block.get("uses_provisional_price", False),
    )
    if price is None:
        return '<span class="latest-price na">—</span>'
    cls = (
        "latest-price provisional"
        if provisional else "latest-price locked"
    )
    title = _esc(as_of or "")
    return (
        f'<span class="{cls}" title="{title}">'
        f'{_esc(_fmt_price(price))}</span>'
    )


def _ranking_row_html(
    row: Mapping[str, Any],
    *,
    detail_index: int,
) -> str:
    rank = row.get("rank") or detail_index + 1
    ticker = row.get("ticker") or "unknown"
    direction = _esc(row.get("direction") or "—")
    windows = _esc(row.get("windows") or "—")
    capture = _esc(row.get("capture") or "—")
    sharpe = _esc(row.get("sharpe") or "—")
    trigger_days = _fmt_int(row.get("trigger_days"))
    freshness = _esc(row.get("freshness") or "—")
    pb = row.get("primary_build")
    cs_block = row.get("current_signal_status_block")
    completeness = row.get("data_completeness")
    if not isinstance(completeness, Mapping):
        completeness = {}
    if not isinstance(cs_block, Mapping):
        cs_block = {}

    sort_values = row.get("row_sort_values")
    if not isinstance(sort_values, Mapping):
        sort_values = {}
    # Phase 6I-48 amendment-1: ranking-eligibility-basis
    # badge. ``strict_full_60_cell`` -> empty label (no
    # visual clutter for the common case);
    # ``partial_effective_members`` -> a visible "Partial
    # (effective members)" badge so a user reading the
    # leaderboard never confuses a partial-ranked row
    # with a strict-complete one. ``None`` -> empty.
    basis = row.get("ranking_eligibility_basis")
    if basis == "partial_effective_members":
        basis_badge_html = (
            '<span class="basis-badge basis-partial" '
            'title="Ranked on effective (non-excluded) '
            'members; not strict full 60-cell coverage.">'
            'Partial (effective members)</span>'
        )
    elif basis == "strict_full_60_cell":
        basis_badge_html = (
            '<span class="basis-badge basis-strict" '
            'title="Ranked on the strict Phase 6I-20 '
            'full 60-cell complete payload.">'
            'Strict 60-cell</span>'
        )
    else:
        basis_badge_html = ""

    data_attrs = (
        f' data-ticker="{_esc(ticker)}"'
        f' data-signal-status="'
        f'{_esc(cs_block.get("current_signal_status") or "unknown")}"'
        f' data-completeness="'
        f'{_esc(completeness.get("data_completeness_status") or "unknown")}"'
        f' data-sort-rank="{_esc(sort_values.get("rank_sort"))}"'
        f' data-sort-capture="'
        f'{_esc(sort_values.get("total_capture_pct_sort"))}"'
        f' data-sort-sharpe="'
        f'{_esc(sort_values.get("sharpe_ratio_sort"))}"'
        f' data-sort-trigger="'
        f'{_esc(sort_values.get("trigger_days_sort"))}"'
        f' data-sort-ticker="'
        f'{_esc(sort_values.get("ticker_sort") or ticker)}"'
        f' data-ranking-eligibility-basis="'
        f'{_esc(basis or "")}"'
    )

    return (
        f'<tr class="ranking-row" data-detail-key="'
        f'{_esc(ticker)}"{data_attrs}>\n'
        f'  <td class="col-rank">{_fmt_int(rank)}</td>\n'
        f'  <td class="col-ticker"><code>{_esc(ticker)}'
        f'</code>{basis_badge_html}</td>\n'
        f'  <td class="col-primary-build">'
        f'{_render_primary_build_cell(pb)}</td>\n'
        f'  <td class="col-direction">{direction}</td>\n'
        f'  <td class="col-windows">{windows}</td>\n'
        f'  <td class="col-same-k">'
        f'{_render_same_k_status_cell(pb)}</td>\n'
        f'  <td class="col-capture num">{capture}</td>\n'
        f'  <td class="col-sharpe num">{sharpe}</td>\n'
        f'  <td class="col-trigger num">{trigger_days}</td>\n'
        f'  <td class="col-status">'
        f'{_render_status_badge(cs_block)}</td>\n'
        f'  <td class="col-price num">'
        f'{_render_latest_price_cell(cs_block)}</td>\n'
        f'  <td class="col-warning">'
        f'{_render_warning_cell(completeness)}</td>\n'
        f'  <td class="col-chart">'
        f'{_render_chart_cell(row)}</td>\n'
        f'  <td class="col-freshness">{freshness}</td>\n'
        '</tr>'
    )


def _build_ranking_table(
    view_model: Mapping[str, Any],
) -> str:
    rows = view_model.get("ranking_table")
    if not isinstance(rows, list):
        rows = []
    if not rows:
        return _empty_state_html(view_model)
    row_html: list[str] = []
    for idx, r in enumerate(rows):
        if not isinstance(r, Mapping):
            continue
        row_html.append(_ranking_row_html(
            r, detail_index=idx,
        ))
    return (
        '<section class="ranking-board">\n'
        '  <h2>Ranking</h2>\n'
        '  <table id="ranking-table" '
        'class="data-table">\n'
        '    <thead><tr>\n'
        '      <th data-col="rank">Rank</th>\n'
        '      <th data-col="ticker">Ticker</th>\n'
        '      <th data-col="primary_build">Primary Build</th>\n'
        '      <th data-col="direction">Direction</th>\n'
        '      <th data-col="windows">Windows</th>\n'
        '      <th data-col="same_k_status">Same-K Status</th>\n'
        '      <th data-col="total_capture_pct">Total Capture %</th>\n'
        '      <th data-col="sharpe_ratio">Sharpe</th>\n'
        '      <th data-col="trigger_days">Trigger Days</th>\n'
        '      <th data-col="current_status">Current Status</th>\n'
        '      <th data-col="latest_price">Last Price</th>\n'
        '      <th data-col="warning">Warning</th>\n'
        '      <th data-col="chart">Chart</th>\n'
        '      <th data-col="freshness">Freshness</th>\n'
        '    </tr></thead>\n'
        '    <tbody id="ranking-tbody">\n'
        + "\n".join(row_html)
        + '\n    </tbody>\n'
        '  </table>\n'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Blocked table
# ---------------------------------------------------------------------------


def _blocked_row_html(row: Mapping[str, Any]) -> str:
    ticker = row.get("ticker") or "unknown"
    reason = _esc(row.get("reason") or "—")
    data_status = _esc(row.get("data_status") or "—")
    freshness = _esc(row.get("freshness") or "—")
    chart_status = _esc(row.get("chart_status") or "—")
    completeness = row.get("data_completeness")
    if not isinstance(completeness, Mapping):
        completeness = {}
    cs_block = row.get("current_signal_status_block")
    if not isinstance(cs_block, Mapping):
        cs_block = {}
    issues = row.get("issues") or []
    issues_text = (
        ", ".join(_esc(i) for i in issues if i)
        if isinstance(issues, list) and issues else "—"
    )
    return (
        f'<tr class="blocked-row" data-detail-key="'
        f'{_esc(ticker)}" data-ticker="{_esc(ticker)}"'
        f' data-completeness="'
        f'{_esc(completeness.get("data_completeness_status") or "blocked")}"'
        f' data-signal-status="'
        f'{_esc(cs_block.get("current_signal_status") or "blocked")}">\n'
        f'  <td class="col-ticker"><code>{_esc(ticker)}'
        f'</code></td>\n'
        f'  <td class="col-reason">{reason}</td>\n'
        f'  <td class="col-data-status">{data_status}</td>\n'
        f'  <td class="col-freshness">{freshness}</td>\n'
        f'  <td class="col-chart-status">{chart_status}</td>\n'
        f'  <td class="col-warning">'
        f'{_render_warning_cell(completeness)}</td>\n'
        f'  <td class="col-issues">{issues_text}</td>\n'
        '</tr>'
    )


def _build_blocked_table(
    view_model: Mapping[str, Any],
) -> str:
    rows = view_model.get("blocked_table")
    if not isinstance(rows, list) or not rows:
        return (
            '<section class="blocked-board empty">\n'
            '  <h2>Blocked</h2>\n'
            '  <p class="hint">No blocked tickers.</p>\n'
            '</section>'
        )
    row_html: list[str] = []
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        row_html.append(_blocked_row_html(r))
    return (
        '<section class="blocked-board">\n'
        '  <h2>Blocked tickers</h2>\n'
        '  <table id="blocked-table" class="data-table">\n'
        '    <thead><tr>\n'
        '      <th>Ticker</th><th>Reason</th>'
        '<th>Data Status</th><th>Freshness</th>'
        '<th>Chart Status</th><th>Warning</th>'
        '<th>Issues</th>\n'
        '    </tr></thead>\n'
        '    <tbody>\n'
        + "\n".join(row_html)
        + '\n    </tbody>\n'
        '  </table>\n'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Data-completeness summary panel (TrafficFlow analog)
# ---------------------------------------------------------------------------


def _build_completeness_summary_panel(
    view_model: Mapping[str, Any],
) -> str:
    s = view_model.get("data_completeness_summary")
    if not isinstance(s, Mapping):
        return ""
    incomplete_count = _fmt_int(
        s.get("tickers_with_incomplete_members", 0),
    )
    incomplete_list = s.get("ticker_list") or []
    list_html = ""
    if isinstance(incomplete_list, list) and incomplete_list:
        items = "".join(
            f'<li><code>{_esc(t)}</code></li>'
            for t in incomplete_list if t
        )
        list_html = f'<ul class="incomplete-list">{items}</ul>'
    by_status = s.get("by_data_completeness_status")
    status_html = ""
    if isinstance(by_status, Mapping) and by_status:
        cells = "".join(
            f'<li><code>{_esc(k)}</code>: '
            f'<span>{_fmt_int(v)}</span></li>'
            for k, v in by_status.items()
        )
        status_html = (
            f'<ul class="completeness-status-counts">'
            f'{cells}</ul>'
        )
    return (
        '<section class="completeness-summary-panel">\n'
        '  <h2>Data completeness summary</h2>\n'
        f'  <p>Tickers with incomplete members: '
        f'<code>{incomplete_count}</code></p>\n'
        f'  {list_html}\n'
        f'  {status_html}\n'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Ticker detail panel
# ---------------------------------------------------------------------------


def _build_detail_panel_shell(
    view_model: Mapping[str, Any],
) -> str:
    """Shell HTML for the ticker detail panel; the actual
    content is injected by the inline JS when a row /
    card is clicked."""
    return (
        '<section class="ticker-detail-panel" '
        'aria-label="Ticker detail">\n'
        '  <h2>Ticker detail</h2>\n'
        '  <div id="ticker-detail" data-current="">\n'
        '    <p class="hint">Select a ticker row '
        '(ranking or blocked) above to view detail.</p>\n'
        '  </div>\n'
        '</section>'
    )


def _build_remaining_limitations(
    view_model: Mapping[str, Any],
) -> str:
    items = view_model.get("remaining_limitations") or []
    if not isinstance(items, list) or not items:
        return ""
    lis = "".join(
        f'<li>{_esc(i)}</li>'
        for i in items if i
    )
    return (
        '<section class="remaining-limitations">\n'
        '  <h2>Remaining limitations</h2>\n'
        f'  <ul>{lis}</ul>\n'
        '</section>'
    )


# ---------------------------------------------------------------------------
# Inline JS
# ---------------------------------------------------------------------------


def _build_script(
    view_model: Mapping[str, Any],
) -> str:
    """Embed the ticker-detail map as JSON and the inline
    JS that powers sort / filter / search / detail-open."""
    cards = view_model.get("ticker_cards") or []
    if not isinstance(cards, list):
        cards = []
    detail_map: dict[str, Any] = {}
    for c in cards:
        if not isinstance(c, Mapping):
            continue
        t = c.get("ticker")
        if isinstance(t, str):
            detail_map[t] = c
    detail_json = _json_for_html(detail_map)
    return (
        '<script id="ticker-detail-data" '
        f'type="application/json">{detail_json}</script>\n'
        '<script>'
        + _INLINE_JS
        + '</script>'
    )


# ---------------------------------------------------------------------------
# Top-level build entry
# ---------------------------------------------------------------------------


def build_static_board_html(
    view_model: Mapping[str, Any],
) -> str:
    """Render the Phase 6I-36 view model into a self-
    contained HTML document.

    Renders an error shell when the view model carries
    ``status_banner.kind == "schema_error"`` (or is not a
    Mapping). The error shell still contains the header /
    banner so the user can see the diagnostic.
    """
    if not isinstance(view_model, Mapping):
        return _build_error_shell_html(
            error_code="non_mapping_view_model",
            detail="The view model passed to the renderer "
            "was not a mapping.",
        )
    banner = view_model.get("status_banner") or {}
    if (
        isinstance(banner, Mapping)
        and banner.get("kind") == "schema_error"
    ):
        return _build_error_shell_html(
            error_code=str(
                banner.get("error_code") or "schema_error",
            ),
            detail=str(banner.get("body") or ""),
            view_model=view_model,
        )

    head = _build_head(view_model)
    header = _build_header(view_model)
    controls = _build_controls(view_model)
    completeness_panel = _build_completeness_summary_panel(
        view_model,
    )
    ranking = _build_ranking_table(view_model)
    blocked = _build_blocked_table(view_model)
    detail_panel = _build_detail_panel_shell(view_model)
    limitations = _build_remaining_limitations(view_model)
    script = _build_script(view_model)
    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        f'{head}\n'
        '</head>\n'
        '<body class="confluence-board">\n'
        f'{header}\n'
        f'{controls}\n'
        f'{completeness_panel}\n'
        f'{ranking}\n'
        f'{blocked}\n'
        f'{detail_panel}\n'
        f'{limitations}\n'
        f'{script}\n'
        '</body>\n'
        '</html>\n'
    )


def _build_error_shell_html(
    *,
    error_code: str,
    detail: str,
    view_model: Optional[Mapping[str, Any]] = None,
) -> str:
    css = _INLINE_CSS
    title = PAGE_TITLE
    code_html = _esc(error_code)
    detail_html = _esc(detail)
    extra = ""
    if isinstance(view_model, Mapping):
        seen = view_model.get("schema_version_seen")
        if seen is not None:
            extra = (
                f'<p class="schema-version-seen">'
                f'Schema version seen: <code>'
                f'{_esc(seen)}</code></p>'
            )
    return (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        f'<head><meta charset="utf-8">'
        f'<title>{title}</title><style>{css}</style></head>\n'
        '<body class="confluence-board error-shell">\n'
        f'<header class="board-header">\n'
        f'  <h1>{title}</h1>\n'
        f'  <div class="status-banner kind-schema_error" '
        f'data-kind="schema_error">\n'
        f'    <strong class="banner-headline">Renderer could '
        f'not load the view model.</strong>\n'
        f'    <p class="banner-body">Error code: <code>'
        f'{code_html}</code></p>\n'
        f'    <p class="banner-body">{detail_html}</p>\n'
        f'    {extra}\n'
        f'  </div>\n'
        '</header>\n'
        '</body></html>\n'
    )


# ---------------------------------------------------------------------------
# Inline CSS / JS
# ---------------------------------------------------------------------------


_INLINE_CSS = """
:root { color-scheme: light dark; }
body.confluence-board {
  font-family: -apple-system, "Segoe UI", system-ui,
    sans-serif;
  font-size: 13px;
  margin: 0;
  padding: 12px;
  background: #f5f6f8;
  color: #111;
}
header.board-header { margin-bottom: 8px; }
header.board-header h1 {
  font-size: 18px;
  margin: 0 0 4px 0;
}
.status-banner {
  padding: 6px 10px;
  border-left: 4px solid #888;
  background: #fff;
  margin: 4px 0;
}
.status-banner.kind-has_eligible_rankings {
  border-left-color: #2e7d32;
}
.status-banner.kind-no_eligible_production_blocked {
  border-left-color: #ed6c02;
}
.status-banner.kind-no_tickers_inspected {
  border-left-color: #757575;
}
.status-banner.kind-schema_error {
  border-left-color: #c62828;
  background: #fdecea;
}
.banner-headline { font-size: 14px; }
.banner-body { margin: 2px 0; font-size: 12px; color: #444; }
ul.summary-strip {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 6px 0;
  margin: 0;
  border-top: 1px solid #e0e0e0;
  border-bottom: 1px solid #e0e0e0;
  font-size: 12px;
  color: #333;
}
ul.summary-strip li code { background: #eef0f3; padding: 0 4px; }
section.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 6px 0;
  align-items: center;
}
section.controls label { font-size: 12px; }
section.controls input,
section.controls select {
  font-size: 12px;
  padding: 2px 4px;
}
table.data-table {
  border-collapse: collapse;
  width: 100%;
  background: #fff;
  font-size: 12px;
}
table.data-table th,
table.data-table td {
  text-align: left;
  padding: 4px 6px;
  border-bottom: 1px solid #e8e8e8;
  vertical-align: top;
}
table.data-table th { background: #ececef; }
table.data-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
table.data-table tr.ranking-row:hover,
table.data-table tr.blocked-row:hover { background: #f0f4ff; cursor: pointer; }
table.data-table tr.ranking-row.selected,
table.data-table tr.blocked-row.selected { background: #dde6ff; }
.status-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 8px;
  background: #ccc;
  color: #fff;
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
}
.status-badge.status-locked { background: #2e7d32; }
.status-badge.status-provisional { background: #ed6c02; }
.status-badge.status-stale { background: #6d4c41; }
.status-badge.status-blocked { background: #c62828; }
.status-badge.status-unknown { background: #757575; }
.warning.warning-on {
  display: inline-block;
  width: 16px; height: 16px; line-height: 16px;
  text-align: center;
  background: #c62828;
  color: #fff;
  border-radius: 50%;
  font-weight: 700;
}
.warning.warning-off { display: inline-block; width: 16px; height: 16px; }
.primary-build { font-family: ui-monospace, Consolas, monospace; }
.primary-build-conflict { color: #c62828; }
.same-k-status.same-k-aligned { color: #2e7d32; font-weight: 600; }
.same-k-status.same-k-mixed { color: #ed6c02; font-weight: 600; }
.same-k-status.single-cell { color: #6d4c41; }
.same-k-status.none { color: #999; }
.latest-price.provisional { color: #ed6c02; font-weight: 600; }
.latest-price.locked { color: #2e7d32; }
.chart-cell.chart-ready { color: #2e7d32; }
.chart-cell.chart-unavailable { color: #999; }
section.ranking-board,
section.blocked-board,
section.ticker-detail-panel,
section.completeness-summary-panel,
section.remaining-limitations,
section.empty-state {
  background: #fff;
  border: 1px solid #e0e0e0;
  padding: 8px 10px;
  margin: 8px 0;
}
section.blocked-board.empty p.hint { color: #777; }
section.ticker-detail-panel #ticker-detail {
  min-height: 60px;
}
section.ticker-detail-panel .hint { color: #777; }
.detail-section { margin: 8px 0; }
.detail-section h3 { margin: 4px 0; font-size: 13px; }
.detail-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 2px;
  border: 1px solid #ddd;
}
.detail-cell {
  padding: 2px 4px;
  font-size: 11px;
  background: #fff;
  border: 1px solid #f0f0f0;
}
.detail-cell.dir-Buy { background: #e8f5e9; }
.detail-cell.dir-Short { background: #ffebee; }
.detail-cell.dir-None { background: #f5f5f5; color: #888; }
.detail-cell.dir-missing { background: #fff3e0; color: #888; }
.detail-cell .K { font-weight: 600; }
.detail-cell .meta { display: block; color: #555; }
.detail-section dl {
  display: grid;
  grid-template-columns: max-content auto;
  gap: 2px 8px;
  font-size: 12px;
}
.detail-section dt { color: #555; }
.detail-section dd { margin: 0; }
.flip-risk.placeholder { color: #999; font-style: italic; }
.empty-state-headline { color: #ed6c02; }
""".strip()


# IMPORTANT: The inline JS below must NOT contain any
# unescaped `</script>` substring. Any embedded JSON goes
# through ``_json_for_html`` which escapes `</`.
_INLINE_JS = r"""
(function () {
  "use strict";
  var detailEl = document.getElementById("ticker-detail-data");
  var DETAILS = {};
  if (detailEl) {
    try {
      DETAILS = JSON.parse(detailEl.textContent);
    } catch (err) {
      DETAILS = {};
    }
  }
  var tbody = document.getElementById("ranking-tbody");
  var rankingTable = document.getElementById("ranking-table");
  var blockedTable = document.getElementById("blocked-table");
  var search = document.getElementById("ticker-search");
  var sortColumn = document.getElementById("sort-column");
  var sortDirection = document.getElementById("sort-direction");
  var filterSignal = document.getElementById("filter-signal-status");
  var filterCompleteness = document.getElementById("filter-completeness");
  var detailPanel = document.getElementById("ticker-detail");

  function escapeHtml(s) {
    if (s === null || s === undefined) return "&mdash;";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  function fmtPct(v) {
    if (v === null || v === undefined || typeof v !== "number") return "&mdash;";
    return v.toFixed(2) + "%";
  }
  function fmtNum(v) {
    if (v === null || v === undefined || typeof v !== "number") return "&mdash;";
    return v.toFixed(2);
  }
  function fmtInt(v) {
    if (v === null || v === undefined || typeof v !== "number") return "&mdash;";
    return Math.round(v).toLocaleString();
  }

  function rowKey(row, col) {
    var attr = {
      rank: "data-sort-rank",
      total_capture_pct: "data-sort-capture",
      sharpe_ratio: "data-sort-sharpe",
      trigger_days: "data-sort-trigger",
      ticker: "data-sort-ticker"
    }[col];
    if (!attr) return null;
    return row.getAttribute(attr);
  }

  function parseNumericKey(v) {
    if (v === null || v === undefined || v === "" || v === "None" || v === "—") return null;
    var n = parseFloat(v);
    return isNaN(n) ? null : n;
  }

  function sortRanking() {
    if (!tbody || !sortColumn) return;
    var col = sortColumn.value;
    var dir = (sortDirection && sortDirection.value) || "desc";
    var rows = Array.prototype.slice.call(
      tbody.querySelectorAll("tr.ranking-row")
    );
    rows.sort(function (a, b) {
      var av = rowKey(a, col);
      var bv = rowKey(b, col);
      if (col === "ticker") {
        av = av || "";
        bv = bv || "";
        if (av < bv) return dir === "asc" ? -1 : 1;
        if (av > bv) return dir === "asc" ? 1 : -1;
        return 0;
      }
      var an = parseNumericKey(av);
      var bn = parseNumericKey(bv);
      if (an === null && bn === null) return 0;
      if (an === null) return 1;     // null sinks
      if (bn === null) return -1;
      return dir === "asc" ? an - bn : bn - an;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  }

  function applyFilters() {
    var q = (search && search.value ? search.value : "").toUpperCase().trim();
    var sig = filterSignal ? filterSignal.value : "";
    var comp = filterCompleteness ? filterCompleteness.value : "";
    var rows = [tbody, blockedTable].filter(function (e) { return e; });
    var tableRows = [];
    rows.forEach(function (rt) {
      Array.prototype.forEach.call(
        rt.querySelectorAll("tr.ranking-row, tr.blocked-row"),
        function (r) { tableRows.push(r); }
      );
    });
    tableRows.forEach(function (r) {
      var t = (r.getAttribute("data-ticker") || "").toUpperCase();
      var rowSig = r.getAttribute("data-signal-status") || "";
      var rowComp = r.getAttribute("data-completeness") || "";
      var show = true;
      if (q && t.indexOf(q) === -1) show = false;
      if (sig && rowSig !== sig) show = false;
      if (comp && rowComp !== comp) show = false;
      r.style.display = show ? "" : "none";
    });
  }

  function clearSelected() {
    Array.prototype.forEach.call(
      document.querySelectorAll("tr.selected"),
      function (r) { r.classList.remove("selected"); }
    );
  }

  function renderDetail(ticker) {
    if (!detailPanel) return;
    var d = DETAILS[ticker];
    if (!d) {
      detailPanel.innerHTML = (
        "<p class='hint'>No detail card available for <code>"
        + escapeHtml(ticker) + "</code>.</p>"
      );
      return;
    }
    var rankElig = d.rank_eligible === true;
    var pieces = [];
    pieces.push(
      "<h3>" + escapeHtml(ticker) +
      " <small>(rank_eligible=" + (rankElig ? "true" : "false") +
      ")</small></h3>"
    );

    // Primary build summary.
    var pb = d.primary_build_summary;
    if (pb && pb.primary_build_available) {
      pieces.push(
        "<div class='detail-section primary-build-section'>" +
        "<h3>Primary build</h3><dl>" +
        "<dt>Selection tier</dt><dd>" +
          escapeHtml(pb.selection_tier) + "</dd>" +
        "<dt>K</dt><dd>" + escapeHtml(pb.K) + "</dd>" +
        "<dt>Direction</dt><dd>" +
          escapeHtml(pb.signal_direction) +
          (pb.direction_conflict ? " (conflict)" : "") +
          "</dd>" +
        "<dt>Windows signaling</dt><dd>" +
          escapeHtml((pb.windows_signaling || []).join(", ")) +
          " (" + escapeHtml(pb.windows_signaling_count) + ")" +
          "</dd>" +
        "<dt>Total capture sum</dt><dd>" +
          fmtPct(pb.total_capture_pct_sum) + "</dd>" +
        "<dt>Avg Sharpe</dt><dd>" + fmtNum(pb.avg_sharpe_ratio) + "</dd>" +
        "<dt>Trigger days sum</dt><dd>" +
          fmtInt(pb.trigger_days_sum) + "</dd>" +
        "<dt>Same-direction K (all 5 windows)</dt><dd><code>" +
          escapeHtml(JSON.stringify(
            pb.same_direction_k_builds_all_windows || []
          )) + "</code></dd>" +
        "<dt>Mixed-direction K (all 5 windows)</dt><dd><code>" +
          escapeHtml(JSON.stringify(
            pb.mixed_direction_k_builds_all_windows || []
          )) + "</code></dd>" +
        "<dt>Explanation</dt><dd>" +
          escapeHtml(pb.explanation) + "</dd>" +
        "</dl></div>"
      );
    } else {
      pieces.push(
        "<div class='detail-section primary-build-section'>" +
        "<h3>Primary build</h3>" +
        "<p>No primary build available (" +
          escapeHtml(pb && pb.explanation || "no_current_signal") +
        ").</p></div>"
      );
    }

    // Current build signal summary.
    var cbs = d.current_build_signal_summary;
    if (cbs) {
      pieces.push(
        "<div class='detail-section current-build-summary'>" +
        "<h3>Current build signal summary</h3><dl>" +
        "<dt>Cells currently Buy</dt><dd>" +
          fmtInt(cbs.cells_currently_buy) + "</dd>" +
        "<dt>Cells currently Short</dt><dd>" +
          fmtInt(cbs.cells_currently_short) + "</dd>" +
        "<dt>Cells currently None</dt><dd>" +
          fmtInt(cbs.cells_currently_none) + "</dd>" +
        "<dt>All members aligned (cells)</dt><dd>" +
          fmtInt(cbs.cells_with_all_members_aligned) + "</dd>" +
        "<dt>Cells historically fired</dt><dd>" +
          fmtInt(cbs.cells_historically_fired) + "</dd>" +
        "<dt>Windows with any current signal</dt><dd><code>" +
          escapeHtml(JSON.stringify(
            cbs.windows_with_any_currently_signaling || []
          )) + "</code></dd>" +
        "<dt>All-windows-any-signal (loose)</dt><dd>" +
          (cbs.all_windows_have_any_current_signal ? "yes" : "no") +
          "</dd>" +
        "<dt>All-5-windows-same-K signaling (strict)</dt><dd>" +
          (cbs.all_five_windows_same_k_currently_signaling
             ? "yes" : "no") + "</dd>" +
        "<dt>All-5-windows-same-K aligned</dt><dd>" +
          (cbs.all_five_windows_same_k_all_members_aligned
             ? "yes" : "no") + "</dd>" +
        "</dl></div>"
      );
    }

    // 60-cell matrix grid (5 windows x 12 K).
    var matrix = d.current_build_signals;
    if (matrix && matrix.length) {
      var rowsByWindow = {};
      matrix.forEach(function (c) {
        var w = c.window;
        if (!rowsByWindow[w]) rowsByWindow[w] = {};
        rowsByWindow[w][c.K] = c;
      });
      var WINDOWS = ["1d", "1wk", "1mo", "3mo", "1y"];
      var Ks = [1,2,3,4,5,6,7,8,9,10,11,12];
      var grid = "<div class='detail-section matrix-section'>";
      grid += "<h3>Current build signals matrix (60 cells)</h3>";
      grid += "<div class='matrix-table-wrap'>";
      grid += "<table class='matrix-table data-table'>";
      grid += "<thead><tr><th>K</th>";
      WINDOWS.forEach(function (w) {
        grid += "<th>" + escapeHtml(w) + "</th>";
      });
      grid += "</tr></thead><tbody>";
      Ks.forEach(function (K) {
        grid += "<tr><th>" + K + "</th>";
        WINDOWS.forEach(function (w) {
          var cell = rowsByWindow[w] && rowsByWindow[w][K];
          if (!cell) {
            grid += "<td class='detail-cell dir-missing'>&mdash;</td>";
            return;
          }
          var dir = cell.latest_combined_signal || "missing";
          var cls = "detail-cell dir-" + escapeHtml(dir);
          grid += "<td class='" + cls + "'>" +
            "<span class='K'>K=" + escapeHtml(cell.K) +
            " " + escapeHtml(dir) + "</span>" +
            "<span class='meta'>cap " + fmtPct(cell.total_capture_pct) +
            " · Sh " + fmtNum(cell.sharpe_ratio) +
            " · trig " + fmtInt(cell.trigger_days) +
            "</span></td>";
        });
        grid += "</tr>";
      });
      grid += "</tbody></table></div></div>";
      pieces.push(grid);
    }

    // Phase 6I-48 amendment-1: ranking-eligibility-basis
    // panel. Surfaces strict_full_60_cell vs
    // partial_effective_members so a user reading the
    // ticker card can never confuse a partial-ranked
    // ticker with a strict-complete one. Hidden when
    // basis is null/undefined (blocked rows).
    var basis = d.ranking_eligibility_basis;
    if (basis) {
      var basisLabel;
      if (basis === "strict_full_60_cell") {
        basisLabel = "Strict (full 60-cell)";
      } else if (basis === "partial_effective_members") {
        basisLabel = "Partial (effective members)";
      } else {
        basisLabel = String(basis);
      }
      pieces.push(
        "<div class='detail-section ranking-eligibility-basis'>" +
        "<h3>Ranking eligibility basis</h3><dl>" +
        "<dt>Basis</dt><dd>" +
          escapeHtml(basisLabel) + "</dd>" +
        "<dt>Internal code</dt><dd><code>" +
          escapeHtml(basis) + "</code></dd>" +
        "</dl></div>"
      );
    }

    // Data completeness block.
    var dc = d.data_completeness;
    if (dc) {
      pieces.push(
        "<div class='detail-section data-completeness'>" +
        "<h3>Data completeness</h3><dl>" +
        "<dt>Status</dt><dd>" +
          escapeHtml(dc.data_completeness_status) + "</dd>" +
        "<dt>Message</dt><dd>" +
          escapeHtml(dc.data_completeness_message) + "</dd>" +
        "<dt>Has incomplete members</dt><dd>" +
          (dc.has_incomplete_build_members ? "yes" : "no") +
          "</dd>" +
        "<dt>Incomplete members</dt><dd><code>" +
          escapeHtml(JSON.stringify(
            dc.incomplete_members || []
          )) + "</code></dd>" +
        "<dt>Incomplete member reasons</dt><dd><code>" +
          escapeHtml(JSON.stringify(
            dc.incomplete_member_reasons || {}
          )) + "</code></dd>" +
        "<dt>Warning symbol</dt><dd>" +
          escapeHtml(dc.data_warning_symbol || "—") + "</dd>" +
        "</dl></div>"
      );
    }

    // Current signal status block.
    var cs = d.current_signal_status_block;
    if (cs) {
      pieces.push(
        "<div class='detail-section current-signal-status-block'>" +
        "<h3>Current signal status</h3><dl>" +
        "<dt>Status</dt><dd>" +
          escapeHtml(cs.current_signal_status) + "</dd>" +
        "<dt>Signal as of</dt><dd>" +
          escapeHtml(cs.current_signal_as_of) + "</dd>" +
        "<dt>Latest price</dt><dd>" +
          escapeHtml(cs.latest_price) + "</dd>" +
        "<dt>Latest price as of</dt><dd>" +
          escapeHtml(cs.latest_price_as_of) + "</dd>" +
        "<dt>Provisional?</dt><dd>" +
          (cs.uses_provisional_price ? "yes" : "no") + "</dd>" +
        "<dt>Update source</dt><dd>" +
          escapeHtml(cs.signal_update_source) + "</dd>" +
        "</dl></div>"
      );
    }

    // Flip-risk placeholder block.
    var fr = d.flip_risk;
    pieces.push(
      "<div class='detail-section flip-risk " +
      (fr && fr.flip_risk_available ? "available" : "placeholder") +
      "'>" +
      "<h3>Flip risk (Spymaster-style placeholder)</h3>" +
      (fr && fr.flip_risk_available
        ? "<dl>" +
          "<dt>Label</dt><dd>" +
            escapeHtml(fr.flip_risk_label) + "</dd>" +
          "<dt>Nearest flip price</dt><dd>" +
            escapeHtml(fr.nearest_flip_price) + "</dd>" +
          "<dt>Nearest flip %</dt><dd>" +
            escapeHtml(fr.nearest_flip_pct) + "</dd>" +
          "<dt>Flip-to signal</dt><dd>" +
            escapeHtml(fr.flip_to_signal) + "</dd>" +
          "</dl>"
        : "<p>Placeholder block. Real Spymaster flip-risk wiring " +
          "is still future work; values are null/false.</p>")
      + "</div>"
    );

    // Chart panel placeholder (NO fabricated charts).
    var chartReady = d.chart_ready_available;
    var chartSrc = d.chart_ready_source;
    var chartBlocker = d.chart_blocker;
    var chartRowCount = d.chart_row_count;
    pieces.push(
      "<div class='detail-section chart-panel'>" +
      "<h3>Chart panel</h3>" +
      "<p>chart_ready_available: <code>" +
        (chartReady ? "true" : "false") + "</code></p>" +
      "<p>chart_ready_source: <code>" +
        escapeHtml(chartSrc) + "</code></p>" +
      "<p>chart_row_count: <code>" +
        escapeHtml(chartRowCount) + "</code></p>" +
      "<p>chart_blocker: <code>" +
        escapeHtml(chartBlocker) + "</code></p>" +
      "<div class='chart-placeholder' style='height:120px;" +
        "border:1px dashed #bbb;background:#fafafa;" +
        "color:#999;display:flex;align-items:center;" +
        "justify-content:center;'>Chart not rendered " +
        "(no embedded rows; renderer never fabricates).</div>" +
      "</div>"
    );

    detailPanel.innerHTML = pieces.join("\n");
  }

  function attachRowHandlers() {
    var allRows = document.querySelectorAll(
      "tr.ranking-row, tr.blocked-row"
    );
    Array.prototype.forEach.call(allRows, function (r) {
      r.addEventListener("click", function () {
        clearSelected();
        r.classList.add("selected");
        var t = r.getAttribute("data-detail-key") || "";
        renderDetail(t);
      });
    });
  }

  if (sortColumn) sortColumn.addEventListener("change", sortRanking);
  if (sortDirection) sortDirection.addEventListener("change", sortRanking);
  if (search) search.addEventListener("input", applyFilters);
  if (filterSignal) filterSignal.addEventListener("change", applyFilters);
  if (filterCompleteness)
    filterCompleteness.addEventListener("change", applyFilters);
  sortRanking();
  applyFilters();
  attachRowHandlers();
})();
""".strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_static_board_renderer",
        description=(
            "Phase 6I-41 read-only static Confluence board "
            "renderer. Consumes a Phase 6I-36 view model "
            "(or a Phase 6I-35 package + in-process "
            "reader/view builder) and emits a "
            "self-contained HTML board to stdout (or to a "
            "non-production --output path). "
            "STRICTLY READ-ONLY."
        ),
    )
    src = parser.add_mutually_exclusive_group(required=False)
    src.add_argument(
        "--view-model",
        default=None,
        help=(
            "Path to a Phase 6I-36 view model JSON file."
        ),
    )
    src.add_argument(
        "--package",
        default=None,
        help=(
            "Path to a Phase 6I-35 package JSON file. The "
            "reader/view builder is invoked in-process to "
            "produce the view model."
        ),
    )
    src.add_argument(
        "--stdin",
        action="store_true",
        help=(
            "Read a Phase 6I-36 view model JSON from "
            "stdin."
        ),
    )
    # Phase 6I-42 amendment-1: from-tickers source.
    src.add_argument(
        "--from-tickers",
        default=None,
        help=(
            "Comma-separated explicit ticker list. Drives "
            "the full Phase 6I-34 ranking export -> "
            "6I-35 package -> 6I-36 view-model chain in-"
            "process. Combine with --with-local-overlays "
            "to thread Phase 6I-42 local overlays through."
        ),
    )
    parser.add_argument(
        "--artifact-root",
        default=None,
        help=(
            "Artifact root for the Phase 6I-34 ranking "
            "export (used with --from-tickers)."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help=(
            "Cache directory for the Phase 6I-34 chart-"
            "readiness fallback (used with "
            "--from-tickers)."
        ),
    )
    parser.add_argument(
        "--with-local-overlays",
        action="store_true",
        help=(
            "Enable Phase 6I-42 local overlays "
            "(member-completeness + latest local price + "
            "current-signal status). Read-only -- the "
            "default cache loader uses the central "
            "provenance loader; no yfinance fetch, no "
            "subprocess, no production write."
        ),
    )
    parser.add_argument(
        "--overlay-cache-dir", default=None,
        help=(
            "Cache directory for the Phase 6I-42 overlay "
            "loader (defaults to --cache-dir)."
        ),
    )
    parser.add_argument(
        "--overlay-artifact-root", default=None,
    )
    parser.add_argument(
        "--overlay-stackbuilder-root", default=None,
    )
    parser.add_argument(
        "--overlay-signal-library-dir", default=None,
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
        help=(
            "ISO date (YYYY-MM-DD). Drives the Phase "
            "6I-42 overlay's locked/stale classification."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional output file. The renderer REFUSES "
            "any path under a known production root: "
            "cache/results, cache/status, "
            "output/research_artifacts, "
            "output/stackbuilder, signal_library/data/"
            "stable."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    try:
        if args.view_model:
            view_model = load_view_model_from_path(
                args.view_model,
            )
        elif args.package:
            pkg = load_view_model_from_path(args.package)
            view_model = _crv.build_view_model(pkg)
        elif args.stdin:
            view_model = load_view_model_from_stdin()
        elif args.from_tickers:
            if not args.artifact_root:
                print(
                    json.dumps({
                        "error": (
                            "missing_artifact_root"
                        ),
                        "detail": (
                            "--from-tickers requires "
                            "--artifact-root."
                        ),
                    }),
                    file=sys.stderr,
                )
                return 2
            tickers = [
                t.strip()
                for t in args.from_tickers.split(",")
                if t.strip()
            ]
            if not tickers:
                print(
                    json.dumps({
                        "error": "empty_ticker_list",
                        "detail": (
                            "--from-tickers parsed to "
                            "zero tickers."
                        ),
                    }),
                    file=sys.stderr,
                )
                return 2
            view_model = build_view_model_from_tickers(
                tickers,
                artifact_root=args.artifact_root,
                cache_dir=args.cache_dir,
                with_local_overlays=(
                    args.with_local_overlays
                ),
                overlay_cache_dir=(
                    args.overlay_cache_dir
                ),
                overlay_artifact_root=(
                    args.overlay_artifact_root
                ),
                overlay_stackbuilder_root=(
                    args.overlay_stackbuilder_root
                ),
                overlay_signal_library_dir=(
                    args.overlay_signal_library_dir
                ),
                current_as_of_date=(
                    args.current_as_of_date
                ),
            )
        else:
            print(
                json.dumps({
                    "error": "missing_source",
                    "detail": (
                        "Provide --view-model, --package, "
                        "--stdin, or --from-tickers."
                    ),
                }),
                file=sys.stderr,
            )
            return 2
    except json.JSONDecodeError as exc:
        print(
            json.dumps({
                "error": "json_decode_error",
                "detail": exc.msg,
            }),
            file=sys.stderr,
        )
        return 3
    except FileNotFoundError as exc:
        print(
            json.dumps({
                "error": "file_not_found",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 3
    except ValueError as exc:
        print(
            json.dumps({
                "error": "invalid_input",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 2

    html_text = build_static_board_html(view_model)

    if args.output:
        try:
            _refuse_production_root(args.output)
        except ValueError as exc:
            print(
                json.dumps({
                    "error": "production_root_refused",
                    "detail": str(exc),
                }),
                file=sys.stderr,
            )
            return 2
        Path(args.output).write_text(
            html_text, encoding="utf-8",
        )
        return 0

    print(html_text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
