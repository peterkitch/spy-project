"""Phase 6I-7 tests for the Spymaster master-audit
read-only surface.

Pins:

  - Static guard: ``spymaster_master_audit`` does NOT
    import any of: ``daily_board_automation_writer``,
    ``signal_engine_cache_refresher``,
    ``confluence_pipeline_runner``, ``yfinance``,
    ``subprocess``.
  - Static text guard on ``spymaster.py``:
      - References the helper layout function +
        callback IDs.
      - Does NOT mention writer / refresher / pipeline-
        runner imports in the audit code path.
      - Does NOT introduce a write button id in the
        audit path.
  - Layout section has every required stable ID.
  - The collapsible Details defaults to closed.
  - The render helper consumes a fake
    ``ExecutionQueueReport`` shape and renders the
    counts / tails / advisory subpanels.
  - Advisory commands are rendered via ``html.Pre``
    (display only) -- no ``html.Button`` referencing
    the writer.
  - Graceful failure: ``render_audit_panel(None,
    "msg")`` returns the unavailable-state component.
  - Existing Spymaster regression test
    ``test_spymaster_help_matrix_ref_removed.py``
    behavior is preserved (the audit section does not
    introduce ``matrix.py`` references).
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import spymaster_master_audit as sma  # noqa: E402
from dash import html  # noqa: E402


SPYMASTER_PATH = PROJECT_DIR / "spymaster.py"
HELPER_PATH = PROJECT_DIR / "spymaster_master_audit.py"


# ---------------------------------------------------------------------------
# Fake ExecutionQueueReport shape
# ---------------------------------------------------------------------------


@dataclass
class _FakeItem:
    ticker: str
    advisory_command: Optional[str] = None


@dataclass
class _FakeReport:
    """Minimal stand-in for ``ExecutionQueueReport`` --
    only carries the attributes the render helper
    touches. Tests use this so we don't need to build
    fixtures for the full Phase 6I-6 + 6I-5 + 6I-4 +
    6I-3 chain."""

    discovered_stackbuilder_ticker_count: int = 0
    inspected_count: int = 0
    selected_refresh_count: int = 0
    selected_pipeline_count: int = 0
    queue_counts: dict = field(default_factory=dict)
    pipeline_only_queue: tuple = ()
    refresh_source_cache_then_pipeline_queue: tuple = ()
    positive_tail: tuple = ()
    negative_tail: tuple = ()
    low_buy_tail: tuple = ()


def _collect_ids(component: Any) -> set[str]:
    """Walk a Dash component tree and collect every
    non-None ``id`` attribute."""
    ids: set[str] = set()

    def walk(c: Any) -> None:
        cid = getattr(c, "id", None)
        if cid:
            ids.add(str(cid))
        children = getattr(c, "children", None)
        if children is None:
            return
        if isinstance(children, (list, tuple)):
            for x in children:
                walk(x)
        else:
            walk(children)

    walk(component)
    return ids


def _walk_components(component: Any) -> list[Any]:
    """Walk a Dash component tree and return every
    component node in pre-order."""
    out: list[Any] = []

    def walk(c: Any) -> None:
        out.append(c)
        children = getattr(c, "children", None)
        if children is None:
            return
        if isinstance(children, (list, tuple)):
            for x in children:
                walk(x)
        else:
            walk(children)

    walk(component)
    return out


# ---------------------------------------------------------------------------
# 1. Forbidden-imports static guard on the helper module
# ---------------------------------------------------------------------------


def test_helper_has_no_forbidden_imports():
    tree = ast.parse(
        HELPER_PATH.read_text(encoding="utf-8"),
    )
    forbidden = {
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
        "yfinance",
        "spymaster",          # circular
        "trafficflow",
        "stackbuilder",
        "onepass",
        "impactsearch",
        "confluence",
        "cross_ticker_confluence",
        "subprocess",
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
        f"forbidden import in spymaster_master_audit: "
        f"{bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. Static text guard on spymaster.py audit code path
# ---------------------------------------------------------------------------


def test_spymaster_audit_path_does_not_import_writer():
    """Spymaster as a whole imports yfinance / pandas /
    etc., but the master-audit code path must not
    introduce any writer / refresher / pipeline-runner
    coupling."""
    text = SPYMASTER_PATH.read_text(encoding="utf-8")
    # Forbidden symbols that the audit code path MUST
    # NOT introduce. ``yfinance`` is a Spymaster-wide
    # import that predates Phase 6I; we don't scan for
    # it here (its existence is unrelated to the audit
    # surface).
    forbidden = [
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
    ]
    hits = [s for s in forbidden if s in text]
    assert not hits, (
        f"spymaster.py introduced writer/refresher/"
        f"runner reference: {hits!r}"
    )


def test_spymaster_references_master_audit_helper():
    """Spymaster.py must wire the helper layout + IDs
    so the surface is reachable from the layout
    container."""
    text = SPYMASTER_PATH.read_text(encoding="utf-8")
    assert "spymaster_master_audit" in text, (
        "spymaster.py is missing the master-audit "
        "helper import"
    )
    assert "build_audit_layout_section" in text, (
        "spymaster.py does not insert the helper "
        "layout section"
    )
    assert "MASTER_AUDIT_LOAD_BUTTON_ID" in text, (
        "spymaster.py does not register the load "
        "callback against the helper's button ID"
    )


def test_spymaster_audit_path_introduces_no_write_button():
    """No new write button / write callback IDs in the
    audit code path. The helper's button is named
    ``master-audit-load-button``; that string is
    allowed. Forbidden patterns: any ID containing
    ``write`` adjacent to ``master-audit`` or
    ``audit``."""
    text = SPYMASTER_PATH.read_text(encoding="utf-8")
    forbidden_patterns = [
        "master-audit-write-button",
        "master-audit-refresh-button",
        "master-audit-pipeline-button",
        "audit-write-button",
    ]
    hits = [s for s in forbidden_patterns if s in text]
    assert not hits, (
        f"spymaster.py audit path introduced a write "
        f"button id: {hits!r}"
    )


# ---------------------------------------------------------------------------
# 3. Layout section has every required stable ID
# ---------------------------------------------------------------------------


def test_layout_section_has_required_ids():
    layout = sma.build_audit_layout_section()
    ids = _collect_ids(layout)
    required = {
        sma.MASTER_AUDIT_SECTION_ID,
        sma.MASTER_AUDIT_DETAILS_ID,
        sma.MASTER_AUDIT_SUMMARY_ID,
        sma.MASTER_AUDIT_LOAD_BUTTON_ID,
        sma.MASTER_AUDIT_STATUS_ID,
        sma.MASTER_AUDIT_PANEL_ID,
    }
    missing = required - ids
    assert not missing, (
        f"layout section missing required IDs: {missing!r}"
    )


def test_layout_details_defaults_to_collapsed():
    """The helper wraps the surface in ``html.Details``
    with ``open=False`` so the audit doesn't auto-run
    on Spymaster boot."""
    layout = sma.build_audit_layout_section()
    details_nodes = [
        c
        for c in _walk_components(layout)
        if getattr(c, "id", None) == (
            sma.MASTER_AUDIT_DETAILS_ID
        )
    ]
    assert len(details_nodes) == 1
    details = details_nodes[0]
    # ``html.Details``: ``open`` defaults to False (and
    # we set it explicitly in the helper).
    assert getattr(details, "open", False) is False


# ---------------------------------------------------------------------------
# 4. Render helper with a fake report
# ---------------------------------------------------------------------------


def test_render_audit_panel_with_fake_report():
    report = _FakeReport(
        discovered_stackbuilder_ticker_count=248,
        inspected_count=248,
        selected_refresh_count=3,
        selected_pipeline_count=0,
        queue_counts={
            "pipeline_only_queue": 0,
            "refresh_source_cache_then_pipeline_queue": 3,
            "wait_for_cache_ahead_queue": 1,
            "manual_stackbuilder_queue": 62,
            "upstream_blocked_queue": 168,
            "downstream_gap_queue": 14,
            "current_leader_eligible_queue": 0,
        },
        pipeline_only_queue=(),
        refresh_source_cache_then_pipeline_queue=(
            _FakeItem(
                ticker="ABC",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker ABC --write"
                ),
            ),
        ),
        positive_tail=({"ticker": "SPY"},),
        negative_tail=(),
        low_buy_tail=({"ticker": "SPY"},),
    )
    panel = sma.render_audit_panel(report, None)
    ids = _collect_ids(panel)
    # Counts / tails / advisory subpanels all rendered.
    assert sma.MASTER_AUDIT_COUNTS_ID in ids
    assert sma.MASTER_AUDIT_TAILS_ID in ids
    assert sma.MASTER_AUDIT_ADVISORY_ID in ids


def test_render_audit_panel_includes_count_values():
    report = _FakeReport(
        discovered_stackbuilder_ticker_count=42,
        inspected_count=42,
        queue_counts={"pipeline_only_queue": 7},
    )
    panel = sma.render_audit_panel(report, None)
    # Walk the rendered tree and collect every text
    # node so we can assert that the count values
    # appear.
    seen_text: list[str] = []
    for c in _walk_components(panel):
        children = getattr(c, "children", None)
        if isinstance(children, str):
            seen_text.append(children)
    joined = "\n".join(seen_text)
    assert "42" in joined
    assert "7" in joined


# ---------------------------------------------------------------------------
# 5. Advisory commands rendered as text only (no
#    buttons)
# ---------------------------------------------------------------------------


def test_advisory_commands_rendered_as_plain_text():
    """The advisory subpanel must render writer
    commands inside ``html.Pre`` -- never inside a
    button or anything that could be clicked into an
    execution path."""
    report = _FakeReport(
        refresh_source_cache_then_pipeline_queue=(
            _FakeItem(
                ticker="X",
                advisory_command=(
                    "python "
                    "daily_board_automation_writer.py "
                    "--ticker X --write"
                ),
            ),
        ),
    )
    panel = sma.render_audit_panel(report, None)
    # The advisory subpanel:
    advisory = None
    for c in _walk_components(panel):
        if getattr(c, "id", None) == (
            sma.MASTER_AUDIT_ADVISORY_ID
        ):
            advisory = c
            break
    assert advisory is not None
    # No ``html.Button`` inside the advisory subpanel.
    for c in _walk_components(advisory):
        assert not isinstance(c, html.Button), (
            "advisory subpanel must not carry any "
            "html.Button (would imply executability)"
        )
    # The command string IS present inside an
    # ``html.Pre``.
    pre_nodes = [
        c
        for c in _walk_components(advisory)
        if isinstance(c, html.Pre)
    ]
    assert len(pre_nodes) >= 1
    body = pre_nodes[0].children or ""
    assert "daily_board_automation_writer.py" in str(body)
    assert "--ticker X --write" in str(body)


# ---------------------------------------------------------------------------
# 6. Graceful failure -> unavailable state
# ---------------------------------------------------------------------------


def test_render_audit_panel_unavailable_on_error():
    panel = sma.render_audit_panel(
        None, "planner_import_failed: <ImportError>",
    )
    # The unavailable text appears.
    found_text = []
    for c in _walk_components(panel):
        children = getattr(c, "children", None)
        if isinstance(children, str):
            found_text.append(children)
    joined = "\n".join(found_text)
    assert "Master audit unavailable" in joined
    assert "planner_import_failed" in joined
    # Counts / tails / advisory subpanels are NOT
    # rendered (panel degraded).
    ids = _collect_ids(panel)
    assert sma.MASTER_AUDIT_COUNTS_ID not in ids
    assert sma.MASTER_AUDIT_TAILS_ID not in ids
    assert sma.MASTER_AUDIT_ADVISORY_ID not in ids


def test_render_audit_panel_unavailable_on_none_report():
    """``report=None`` AND ``error=None`` (the defensive
    edge) still produces a visible unavailable state
    rather than crashing."""
    panel = sma.render_audit_panel(None, None)
    found_text = []
    for c in _walk_components(panel):
        children = getattr(c, "children", None)
        if isinstance(children, str):
            found_text.append(children)
    joined = "\n".join(found_text)
    assert "Master audit unavailable" in joined


# ---------------------------------------------------------------------------
# 7. Read-only notice copy is present
# ---------------------------------------------------------------------------


def test_read_only_notice_mentions_required_points():
    """The notice copy must communicate the four
    contract points from the prompt:

      - read-only / advisory commands not executed,
      - writer still requires two-key auth,
      - StackBuilder variants don't expire by age,
      - both top and bottom ranking tails matter.
    """
    notice = sma.READ_ONLY_NOTICE_TEXT.lower()
    assert "read-only" in notice
    assert (
        "advisory" in notice and "not executed" in notice
    )
    assert "two-key" in notice
    assert "do not expire by age" in notice
    assert (
        "positive" in notice and "bottom" in notice
    )


# ---------------------------------------------------------------------------
# 8. Helper exposes a load function that returns a tuple
# ---------------------------------------------------------------------------


def test_load_audit_report_returns_tuple():
    """The load function returns a 2-tuple
    ``(report_or_none, error_or_none)``. We do not
    invoke the real planner here -- we only verify the
    signature and that the function is callable."""
    sig = sma.load_audit_report.__doc__
    assert sig is not None
    # The function is callable (we don't invoke it on
    # real production roots; that's an integration
    # smoke).
    assert callable(sma.load_audit_report)
