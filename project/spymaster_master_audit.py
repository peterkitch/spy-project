"""Phase 6I-7: Spymaster master-audit surface helper.

Read-only audit panel that Spymaster embeds as its
master audit surface. Consumes the existing Phase 6I
read-only planning stack:

  - Phase 6I-6 ``daily_board_execution_queue_planner``
    (the canonical entry point).
  - Phase 6I-5 ``daily_board_universe_planner``
    (consumed transitively).
  - Phase 6I-4 ``upstream_research_input_audit``
    (consumed transitively).
  - Phase 6I-3 ``confluence_ranking_emitter`` (consumed
    transitively).
  - Phase 6I-1 ``confluence_ranking_contract_validator``
    (consumed transitively).

The surface is **strictly read-only**. The helper:

  - Renders a Dash layout section with stable IDs that
    tests can locate.
  - Provides a single load function that defensively
    invokes the queue planner and returns a verdict
    tuple ``(report, error_message)``.
  - Provides a single render function that converts a
    verdict into a Dash component tree (counts table,
    ranking-tail summaries, advisory commands as
    plain text).
  - Emits NO buttons that perform writes. NO callbacks
    register here.
  - NEVER imports the Phase 6H-5 writer, the Phase
    6E-5 refresher, the Phase 6D-4 pipeline runner,
    yfinance, dash live-engine modules, or subprocess.

Spymaster itself wires the layout section into its
container and registers the load button's callback.
That callback path is the only execution surface, and
it only calls ``load_audit_report`` + ``render_audit_panel``
from this module -- both read-only.

Public surface
--------------

    MASTER_AUDIT_*                       # stable IDs
    AUDIT_UNAVAILABLE_TEXT               # error copy
    READ_ONLY_NOTICE_TEXT                # surface copy

    build_audit_layout_section() -> dash component
    render_audit_panel(report, error) -> dash component
    load_audit_report(
        *, tickers=None,
        from_stackbuilder_universe=True,
        max_refresh=10, max_pipeline=10,
        include_blocked=True, top_n=5,
    ) -> tuple[Optional[Report], Optional[str]]
"""

from __future__ import annotations

from typing import Any, Optional

from dash import html


# ---------------------------------------------------------------------------
# Stable IDs (tests pin these; do NOT rename without
# updating the corresponding tests + Spymaster wiring)
# ---------------------------------------------------------------------------

MASTER_AUDIT_SECTION_ID = "section-master-audit"
MASTER_AUDIT_DETAILS_ID = "master-audit-details"
MASTER_AUDIT_SUMMARY_ID = "master-audit-summary"
MASTER_AUDIT_LOAD_BUTTON_ID = "master-audit-load-button"
MASTER_AUDIT_STATUS_ID = "master-audit-status"
MASTER_AUDIT_PANEL_ID = "master-audit-panel"
MASTER_AUDIT_COUNTS_ID = "master-audit-counts"
MASTER_AUDIT_TAILS_ID = "master-audit-tails"
MASTER_AUDIT_ADVISORY_ID = "master-audit-advisory-commands"


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

READ_ONLY_NOTICE_TEXT = (
    "This panel is READ-ONLY. It summarizes the "
    "Phase 6I-6 execution queue + Phase 6I-5 universe "
    "plan. Advisory writer commands are shown as plain "
    "text for operator reference and are NOT executed "
    "here. The Phase 6H-5 writer still requires two-key "
    "authorization (--write CLI flag + the "
    "PRJCT9_AUTOMATION_WRITE_AUTH env var). Saved "
    "StackBuilder variants are durable inputs and do "
    "NOT expire by age. Both the positive (Buy-leaning) "
    "and the bottom (Short / low-buy / inverse "
    "confirmation) ranking tails are meaningful; this "
    "surface exposes both."
)

AUDIT_UNAVAILABLE_TEXT = (
    "Master audit unavailable. The execution-queue "
    "planner could not be loaded. Spymaster continues "
    "to function; the audit panel is degraded to a "
    "read-only notice. Cause:"
)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def build_audit_layout_section() -> html.Div:
    """Return the master-audit layout section.

    The section is wrapped in a ``html.Details``
    collapsed-by-default so the heavy planner call only
    runs when the operator explicitly clicks the load
    button (and even then, only via the Spymaster
    callback that wraps ``load_audit_report``).
    """
    inactive_style = {
        "padding": "12px 16px",
        "margin": "16px 0",
        "border": "1px solid rgba(128, 255, 0, 0.25)",
        "borderRadius": "6px",
        "backgroundColor": "rgba(0, 0, 0, 0.4)",
    }
    summary_style = {
        "cursor": "pointer",
        "color": "#80ff00",
        "fontSize": "16px",
        "fontWeight": "600",
        "padding": "4px 0",
    }
    notice_style = {
        "fontSize": "12px",
        "color": "#cccccc",
        "marginTop": "8px",
        "marginBottom": "12px",
        "lineHeight": "1.5",
    }
    button_style = {
        "backgroundColor": "transparent",
        "color": "#80ff00",
        "border": "1px solid #80ff00",
        "borderRadius": "4px",
        "padding": "6px 14px",
        "cursor": "pointer",
        "fontSize": "13px",
    }
    return html.Div(
        id=MASTER_AUDIT_SECTION_ID,
        style=inactive_style,
        children=[
            html.Details(
                id=MASTER_AUDIT_DETAILS_ID,
                open=False,
                children=[
                    html.Summary(
                        id=MASTER_AUDIT_SUMMARY_ID,
                        children=(
                            "Daily Board Automation "
                            "Audit (read-only)"
                        ),
                        style=summary_style,
                    ),
                    html.P(
                        READ_ONLY_NOTICE_TEXT,
                        style=notice_style,
                    ),
                    html.Div(
                        style={"marginBottom": "12px"},
                        children=[
                            html.Button(
                                "Load audit",
                                id=MASTER_AUDIT_LOAD_BUTTON_ID,
                                n_clicks=0,
                                style=button_style,
                            ),
                            html.Span(
                                id=MASTER_AUDIT_STATUS_ID,
                                children="Idle.",
                                style={
                                    "marginLeft": "12px",
                                    "fontSize": "12px",
                                    "color": "#888",
                                },
                            ),
                        ],
                    ),
                    html.Div(id=MASTER_AUDIT_PANEL_ID),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Planner invocation (defensive)
# ---------------------------------------------------------------------------


def load_audit_report(
    *,
    tickers: Optional[list[str]] = None,
    from_stackbuilder_universe: bool = True,
    max_refresh: Optional[int] = 10,
    max_pipeline: Optional[int] = 10,
    include_blocked: bool = True,
    top_n: int = 5,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    impactsearch_output_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
) -> tuple[Optional[Any], Optional[str]]:
    """Invoke the Phase 6I-6 execution-queue planner
    read-only.

    Returns ``(report, None)`` on success, or
    ``(None, error_message)`` on any failure --
    including a planner-import failure (so Spymaster
    can degrade gracefully when the planner module is
    unavailable or broken).

    Strictly read-only: the planner itself emits no
    writes, no engine execution, no subprocess. The
    only output of this function is the
    ``ExecutionQueueReport`` (or the error string)."""
    try:
        # Lazy import: keeps Spymaster's boot-time cost
        # zero even when the operator never opens the
        # audit panel. Also isolates planner-import
        # failures from Spymaster boot.
        from daily_board_execution_queue_planner import (
            build_execution_queue,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"planner_import_failed: {exc!r}"
    try:
        report = build_execution_queue(
            tickers=tickers,
            from_stackbuilder_universe=(
                from_stackbuilder_universe
            ),
            max_refresh=max_refresh,
            max_pipeline=max_pipeline,
            include_blocked=include_blocked,
            top_n=top_n,
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            stackbuilder_root=stackbuilder_root,
            signal_library_dir=signal_library_dir,
            impactsearch_output_dir=(
                impactsearch_output_dir
            ),
            current_as_of_date=current_as_of_date,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"planner_invocation_failed: {exc!r}"
    return report, None


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _label_value_row(label: str, value: Any) -> html.Div:
    return html.Div(
        style={
            "display": "flex",
            "padding": "2px 0",
            "fontSize": "13px",
        },
        children=[
            html.Span(
                f"{label}:",
                style={
                    "width": "260px",
                    "color": "#cccccc",
                },
            ),
            html.Span(
                str(value),
                style={"color": "#80ff00"},
            ),
        ],
    )


def _render_unavailable(error: str) -> html.Div:
    return html.Div(
        id=MASTER_AUDIT_PANEL_ID + "-inner",
        children=[
            html.P(
                AUDIT_UNAVAILABLE_TEXT,
                style={"color": "#ff9900"},
            ),
            html.Pre(
                str(error),
                style={
                    "color": "#ff9900",
                    "fontSize": "12px",
                    "whiteSpace": "pre-wrap",
                },
            ),
        ],
    )


def _render_counts(report: Any) -> html.Div:
    """Counts block: per-queue sizes + the operator-
    relevant aggregates."""
    queue_counts = dict(report.queue_counts)
    return html.Div(
        id=MASTER_AUDIT_COUNTS_ID,
        style={"marginTop": "8px"},
        children=[
            html.H6(
                "Aggregate counts",
                style={
                    "color": "#80ff00",
                    "fontSize": "13px",
                    "marginBottom": "6px",
                },
            ),
            _label_value_row(
                "discovered_stackbuilder_ticker_count",
                report.discovered_stackbuilder_ticker_count,
            ),
            _label_value_row(
                "inspected_count",
                report.inspected_count,
            ),
            _label_value_row(
                "selected_refresh_count",
                report.selected_refresh_count,
            ),
            _label_value_row(
                "selected_pipeline_count",
                report.selected_pipeline_count,
            ),
            html.H6(
                "queue_counts",
                style={
                    "color": "#80ff00",
                    "fontSize": "13px",
                    "marginTop": "10px",
                    "marginBottom": "6px",
                },
            ),
            _label_value_row(
                "pipeline_only_queue",
                queue_counts.get("pipeline_only_queue", 0),
            ),
            _label_value_row(
                "refresh_source_cache_then_pipeline_queue",
                queue_counts.get(
                    "refresh_source_cache_then_pipeline_queue",
                    0,
                ),
            ),
            _label_value_row(
                "wait_for_cache_ahead_queue",
                queue_counts.get(
                    "wait_for_cache_ahead_queue", 0,
                ),
            ),
            _label_value_row(
                "manual_stackbuilder_queue",
                queue_counts.get(
                    "manual_stackbuilder_queue", 0,
                ),
            ),
            _label_value_row(
                "upstream_blocked_queue",
                queue_counts.get(
                    "upstream_blocked_queue", 0,
                ),
            ),
            _label_value_row(
                "downstream_gap_queue",
                queue_counts.get(
                    "downstream_gap_queue", 0,
                ),
            ),
            _label_value_row(
                "current_leader_eligible_queue",
                queue_counts.get(
                    "current_leader_eligible_queue", 0,
                ),
            ),
        ],
    )


def _render_tail_summary(
    label: str, tail: Any,
) -> html.Div:
    tickers = [
        str(row.get("ticker") or "") for row in tail
    ]
    body = (
        ", ".join(tickers) if tickers
        else "(empty)"
    )
    return _label_value_row(label, body)


def _render_tails(report: Any) -> html.Div:
    return html.Div(
        id=MASTER_AUDIT_TAILS_ID,
        style={"marginTop": "10px"},
        children=[
            html.H6(
                "Ranking tails (top-N per tail)",
                style={
                    "color": "#80ff00",
                    "fontSize": "13px",
                    "marginBottom": "6px",
                },
            ),
            html.P(
                (
                    "Both top and bottom tails are "
                    "meaningful: the negative / low-buy "
                    "tail can be sell / short / no-long-"
                    "support evidence (the QQQ-vs-SQQQ "
                    "inverse-confirmation pattern)."
                ),
                style={
                    "fontSize": "11px",
                    "color": "#aaaaaa",
                    "marginBottom": "6px",
                },
            ),
            _render_tail_summary(
                "positive_tail", report.positive_tail,
            ),
            _render_tail_summary(
                "negative_tail", report.negative_tail,
            ),
            _render_tail_summary(
                "low_buy_tail", report.low_buy_tail,
            ),
        ],
    )


def _render_advisory(report: Any) -> html.Div:
    """Advisory writer commands surfaced as PLAIN TEXT
    only. No buttons, no callbacks, no execution."""
    lines: list[str] = []
    for item in report.pipeline_only_queue:
        cmd = getattr(item, "advisory_command", None)
        if cmd:
            lines.append(f"# {item.ticker} (pipeline_only)")
            lines.append(cmd)
    for item in report.refresh_source_cache_then_pipeline_queue:
        cmd = getattr(item, "advisory_command", None)
        if cmd:
            lines.append(
                f"# {item.ticker} (refresh + pipeline)",
            )
            lines.append(cmd)
    body = (
        "\n".join(lines) if lines
        else "(no write-ready tickers in this report)"
    )
    return html.Div(
        id=MASTER_AUDIT_ADVISORY_ID,
        style={"marginTop": "10px"},
        children=[
            html.H6(
                "Advisory writer commands (display only)",
                style={
                    "color": "#80ff00",
                    "fontSize": "13px",
                    "marginBottom": "6px",
                },
            ),
            html.P(
                (
                    "These strings are operator-paste "
                    "reference. They are NOT executed "
                    "here. The Phase 6H-5 writer still "
                    "requires the two-key auth gate "
                    "(--write + PRJCT9_AUTOMATION_WRITE_AUTH)."
                ),
                style={
                    "fontSize": "11px",
                    "color": "#aaaaaa",
                    "marginBottom": "6px",
                },
            ),
            html.Pre(
                body,
                style={
                    "color": "#80ff00",
                    "backgroundColor": "rgba(0, 0, 0, 0.6)",
                    "padding": "8px",
                    "fontSize": "12px",
                    "whiteSpace": "pre-wrap",
                    "border": "1px solid rgba(128, 255, 0, 0.2)",
                    "borderRadius": "4px",
                },
            ),
        ],
    )


def render_audit_panel(
    report: Optional[Any], error: Optional[str],
) -> html.Div:
    """Render the audit panel body from a verdict
    tuple. Either ``report`` (the Phase 6I-6 result)
    OR ``error`` (a string) should be non-None."""
    if error is not None or report is None:
        return _render_unavailable(
            error or "unknown_error",
        )
    return html.Div(
        children=[
            _render_counts(report),
            _render_tails(report),
            _render_advisory(report),
        ],
    )
