"""Phase 6C-7: PRJCT9 Daily Signal Board (public read-only).

Structural foundation for the PRJCT9.com one-day MVP front door.
This module renders five sections top-to-bottom:

    1. Town Hall Scoreboard      (section-scoreboard)
    2. Featured High Score       (section-featured)
    3. Evidence Trail            (section-evidence-trail)
    4. What PRJCT9 Is            (section-what-prjct9-is)
    5. What It Is Not            (section-what-it-is-not)

Visual styling is deliberately minimal - Claude Design will replace
the chrome later. Stable IDs, centralized copy in ``BOARD_COPY``,
centralized colors in ``DESIGN_TOKENS``, and a documented data
contract are the deliverables.

Public read-only by design:

  - No yfinance.
  - No live engine call (impactsearch, stackbuilder, confluence,
    trafficflow, onepass, spymaster, cross_ticker_confluence).
  - No producer rebuilds, no disk writes from the web tier.
  - No new dependencies.
  - No hardcoded ticker list, no build / refresh buttons.

Data sources (all read-only and offline):

  - ``primary_signal_engine.load_primary_signal_engine_payload``
    for each ticker discovered in ``cache/results``.
  - ``research_artifacts.discover_research_artifacts`` +
    ``research_artifacts.read_research_day_artifact`` for per-engine
    research_day_v1 artifacts under
    ``output/research_artifacts/<engine>/<TARGET>/``.
  - ``research_catalogue_health.read_catalogue_health_report`` for
    the existing on-disk audit report (only if present).
  - Filesystem reads of
    ``signal_library/data/stable/<TICKER>_stable_v1_0_0_<INTERVAL>.pkl``
    for non-daily interval libraries (Calendar House evidence).

Public surface:

    BOARD_COPY                         # dict[str, ...]
    DESIGN_TOKENS                      # dict[str, str]
    SIGNAL_TO_VALUE                    # dict[str, int]
    STATION_IDS                        # tuple[str, ...]
    COVERAGE_*                         # str constants
    BoardRow, EvidenceStation          # dataclasses
    reset_board_cache()
    discover_board_catalogue(...) -> list[BoardRow]
    rank_board_rows(rows) -> list[BoardRow]
    coverage_status_for_ticker(...) -> str
    default_selected_ticker(rows) -> str
    render_scoreboard(rows, selected_ticker)
    render_featured(ticker, *, payload=None, confluence=None)
    render_evidence_trail(ticker, *, ...)
    build_app() -> Dash
    main(port=None) -> None
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import confluence_pipeline_readiness as _cpr
import primary_signal_engine as _pse
import research_artifacts as _ra
import research_catalogue_health as _rch


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8061
STALE_DAYS = 30

# Order matters: each row carries one of these coverage labels.
COVERAGE_UNDER_REVIEW = "Under review"
COVERAGE_STALE = "Stale"
# Phase 6C-8 audit-tighten: a row whose Confluence verdict is
# present + current but whose readiness layer blocks ranking on
# the missing multi-timeframe TrafficFlow / K-build bridge (or
# the related insufficient_trafficflow_k_coverage issue) renders
# this label so the Coverage column never contradicts the
# leader-eligibility gate.
COVERAGE_PIPELINE_INCOMPLETE = "Pipeline incomplete"
COVERAGE_FULL = "Full"
COVERAGE_PARTIAL = "Partial"

# Coverage priority order. Lower index wins. Referenced by tests.
COVERAGE_PRIORITY: tuple[str, ...] = (
    COVERAGE_UNDER_REVIEW,
    COVERAGE_STALE,
    COVERAGE_PIPELINE_INCOMPLETE,
    COVERAGE_FULL,
    COVERAGE_PARTIAL,
)

# Buy / None / Short -> +1 / 0 / -1. Anything else maps to 0.
SIGNAL_TO_VALUE: dict[str, int] = {
    "Buy": 1,
    "None": 0,
    "Short": -1,
}

# Per-engine artifact discovery: fixed engine keys mirrored from
# research_artifacts. Kept here so the board never depends on
# implementation-side string drift.
ENGINE_IMPACTSEARCH = "impactsearch"
ENGINE_STACKBUILDER = "stackbuilder"
ENGINE_TRAFFICFLOW = "trafficflow"
ENGINE_CONFLUENCE = "confluence"

# Non-daily interval suffixes Calendar House looks for. Mirrors the
# 7-tier Confluence default timeframes minus the daily base (which
# has no suffix in the filename).
CALENDAR_HOUSE_INTERVALS: tuple[str, ...] = ("1wk", "1mo", "3mo", "1y")

# Evidence-station presence states.
PRESENCE_PRESENT = "present"
PRESENCE_MISSING = "missing"
PRESENCE_STALE = "stale"
PRESENCE_UNDER_REVIEW = "under_review"

# Fixed station order and IDs.
STATION_ID_SEED_FIELD = "station-seed-field"
STATION_ID_TRADING_POST = "station-trading-post"
STATION_ID_WORKSHOP = "station-workshop"
STATION_ID_RAIL_YARD = "station-rail-yard"
STATION_ID_CALENDAR_HOUSE = "station-calendar-house"
STATION_ID_TOWN_HALL = "station-town-hall"
STATION_ID_WATCHTOWER = "station-watchtower"

STATION_IDS: tuple[str, ...] = (
    STATION_ID_SEED_FIELD,
    STATION_ID_TRADING_POST,
    STATION_ID_WORKSHOP,
    STATION_ID_RAIL_YARD,
    STATION_ID_CALENDAR_HOUSE,
    STATION_ID_TOWN_HALL,
    STATION_ID_WATCHTOWER,
)


# ---------------------------------------------------------------------------
# Design tokens (single owner of every color used in inline styles)
# ---------------------------------------------------------------------------

DESIGN_TOKENS: dict[str, str] = {
    # ------------------------------------------------------------------
    # Phase 6G-4 - "Town Notice Board" palette.
    # ------------------------------------------------------------------
    # The page is a warm-dark notice board with pinned-paper
    # section cards. ``color_green`` is the everyday brand
    # sage/moss; the legacy neon green moves to its own
    # ``color_leader_highlight`` token reserved for the
    # current-leader accent (SPY row highlight + Today's
    # Board Status pilot chip). Nothing else on the page
    # should use the neon - brightness now consistently
    # signals "this is the current pick".
    # ------------------------------------------------------------------

    # Page surface (warm dark, replaces the legacy pure
    # black). ``color_black`` is aliased to this so any
    # caller still passing the legacy token sits on the
    # same surface and we don't get a hard contrast jump.
    "color_warm_dark": "#1c1814",
    "color_black": "#1c1814",
    # Paper / card surface (sections sit on this; slightly
    # warmer than ``color_warm_dark`` so cards read as
    # papers pinned to the board).
    "color_paper": "#23201a",
    # Warm dim used for hover / selected row bg.
    "color_dim": "#26211a",
    # Wood-grain border for sections + scoreboard / archive
    # dividers.
    "color_border": "#3a322a",
    # Mustard wood-tone used for the small "pin" / wax-seal
    # accents (the pin glyph at the top of the Today's
    # Board Status card and the row-rank chip).
    "color_pin": "#c9a86a",
    # ------------------------------------------------------------------
    # Brand colors
    # ------------------------------------------------------------------
    # Primary sage / moss - the everyday brand green.
    "color_green": "#8fbf6f",
    # Legacy neon green, NOW SCOPED to the current-leader
    # accent only (SPY row highlight + Today's Pilot chip).
    # Tests pin that this token is distinct from
    # ``color_green``.
    "color_leader_highlight": "#80ff00",
    # ------------------------------------------------------------------
    # Text
    # ------------------------------------------------------------------
    "color_text": "#e6e0d0",
    "color_muted": "#9b9388",
    # Legacy red token, softened to a warm rust.
    "color_red": "#c46a4d",
    # ------------------------------------------------------------------
    # Signal-state accents (Confluence consensus + Signal Engine)
    # ------------------------------------------------------------------
    "color_buy": "#8fbf6f",
    "color_short": "#c46a4d",
    "color_none": "#9b9388",
    # ------------------------------------------------------------------
    # Coverage wax-seal pill accents
    # ------------------------------------------------------------------
    "color_full": "#7fa766",
    "color_partial": "#c89b3e",
    "color_stale": "#9b9388",
    "color_under_review": "#4f6b8a",
    "color_pipeline_incomplete": "#c89b3e",
    # ------------------------------------------------------------------
    # Top-3 rank accents (unchanged).
    # ------------------------------------------------------------------
    "color_rank_1": "#ffd166",
    "color_rank_2": "#cfd2cd",
    "color_rank_3": "#c08552",
}

PRJCT9_GREEN = DESIGN_TOKENS["color_green"]
PRJCT9_BLACK = DESIGN_TOKENS["color_black"]
PRJCT9_TEXT = DESIGN_TOKENS["color_text"]
PRJCT9_MUTED = DESIGN_TOKENS["color_muted"]
PRJCT9_BORDER = DESIGN_TOKENS["color_border"]
PRJCT9_RED = DESIGN_TOKENS["color_red"]
PRJCT9_DIM = DESIGN_TOKENS["color_dim"]


# ---------------------------------------------------------------------------
# Centralized user-facing copy
# ---------------------------------------------------------------------------

BOARD_COPY: dict[str, Any] = {
    "page_title": "PRJCT9 Daily Signal Board",
    "page_subtitle": (
        "Saved historical signal alignment across studied tickers."
    ),
    "section_scoreboard_title": "Town Hall Scoreboard",
    "section_featured_title": "Featured High Score",
    "section_evidence_trail_title": "Evidence Trail",
    "section_what_prjct9_is_title": "What PRJCT9 Is",
    "section_what_it_is_not_title": "What It Is Not",
    # Section 1 - Scoreboard
    "col_ticker": "Ticker",
    # Phase 6G-1: the visible header reads "Consensus" so a
    # first-time visitor doesn't conflate the Confluence
    # consensus (this column) with the Signal Engine's own
    # current signal (rendered in the Featured panel).
    # The underlying ``data-signal`` attribute on each row
    # is unchanged ("None" / "Buy" / "Short") - only the
    # visible cell text differs.
    "col_signal": "Consensus",
    "col_agreement": "Agreement",
    "col_coverage": "Coverage",
    "col_as_of": "As of",
    "empty_scoreboard": "No saved tickers yet.",
    "agreement_unavailable": "Unavailable",
    "as_of_unavailable": "-",
    # Phase 6G-1: public-friendly visible cell labels for the
    # scoreboard Consensus column. data-signal on the Tr is
    # still "Buy" / "Short" / "None" - these strings drive
    # only the rendered cell text.
    "scoreboard_consensus_buy": "Buy",
    "scoreboard_consensus_short": "Short",
    "scoreboard_consensus_none": "No consensus",
    # Phase 6G-1: archive of saved-research-only rows
    # (Partial / Stale / Under review / Pipeline incomplete
    # coverage). Collapsed by default; not part of the
    # current-leader board.
    "section_archive_title": "Saved Research Archive",
    "section_archive_intro": (
        "Saved research that hasn't been promoted to a "
        "current board pick. Browse for context; not a "
        "current signal."
    ),
    # Phase 6G-4: warmer notice-board phrasing for the
    # archive disclosure.
    "section_archive_summary_fmt": (
        "Open the saved-research drawer ({count} tickers)"
    ),
    "section_archive_empty": (
        "No archived rows."
    ),
    # Phase 6G-1: "Today's Board Status" hero card. Pulls
    # from the rank-1 leader-eligible row + the Signal
    # Engine cache for the same ticker. Avoids any
    # directional / investment-advice framing.
    "section_current_pilot_title": "Today's Board Status",
    "current_pilot_no_leader": (
        "No current pilot today. Saved research is below; "
        "leaderboard picks need a current Confluence verdict."
    ),
    "current_pilot_intro_fmt": (
        "{ticker} is the current full-pipeline pilot."
    ),
    "current_pilot_consensus_buy": (
        "Board consensus: Buy direction."
    ),
    "current_pilot_consensus_short": (
        "Board consensus: Short direction."
    ),
    "current_pilot_consensus_none": (
        "Board consensus: No directional consensus today."
    ),
    "current_pilot_signal_engine_fmt": (
        "Signal Engine state: {pair}."
    ),
    "current_pilot_signal_engine_unavailable": (
        "Signal Engine state: not available."
    ),
    "current_pilot_as_of_fmt": (
        "As of {consensus_date} (board consensus) / "
        "{se_date} (Signal Engine cache)."
    ),
    "current_pilot_as_of_partial_consensus_fmt": (
        "As of {consensus_date} (board consensus)."
    ),
    "current_pilot_as_of_partial_se_fmt": (
        "Signal Engine cache through {se_date}."
    ),
    # Section 2 - Featured
    # Phase 6G-4: short prefix that demotes the giant
    # ticker glyph - shipped above ``featured-ticker-name``
    # so the panel reads "Today's pilot - SPY" instead of
    # a Bloomberg ticker block. Stable id:
    # ``featured-pilot-prefix``.
    "featured_pilot_prefix": "Today's pilot",
    "featured_label_current_signal": "Current Signal",
    "featured_label_confluence": "Confluence",
    "featured_label_total_capture": "Total Capture (%)",
    "featured_label_sharpe": "Sharpe Ratio",
    "featured_label_signal_days": "Signal Days",
    "featured_label_as_of": "As of",
    "featured_empty_no_ticker": "No ticker selected.",
    "featured_empty_no_data": "No saved Signal Engine data for this ticker.",
    "featured_empty_chart": "No saved chart rows for this ticker yet.",
    "featured_chart_caption": (
        "Green line is Signal Engine cumulative capture, not portfolio "
        "return. Dotted line is the ticker's raw historical close on the "
        "right axis."
    ),
    # Phase 6G-4: disclaimer reads less like legal boilerplate
    # without losing the "not investment advice / not a live
    # feed" meaning. ASCII semicolon used (not em dash) so the
    # exact-string test stays portable.
    "featured_disclaimer": (
        "Historical research output. Not investment advice; "
        "saved research, not a live signal feed."
    ),
    # Phase 6G-1: ``total`` is K-builds (1..12) x timeframes
    # (1d/1wk/1mo/3mo/1y) = 60 alignment checks, NOT 60
    # distinct timeframes. The prior wording over-claimed
    # the timeframe dimension; the new wording is honest
    # about what the count measures.
    "confluence_status_fmt": (
        "{active} of {total} alignment checks active"
    ),
    "confluence_status_unavailable": "Confluence data unavailable",
    # Phase 6G-1: short explainer that defuses the two-signal
    # confusion ("scoreboard says No consensus but Featured
    # says Short"). Sourced from BOARD_COPY so the
    # copy-centralization test catches it.
    "two_signal_explainer": (
        "Board consensus combines K-build and timeframe "
        "alignment checks. Signal Engine state is the "
        "ticker's standalone SMA engine readout. The two "
        "can disagree."
    ),
    # Phase 6G-1: Evidence Trail intro framing.
    "evidence_trail_intro": (
        "How today's board pick was discovered. Stale "
        "upstream stations are historical reference; they "
        "do not block the current leader gate unless flagged "
        "explicitly."
    ),
    # Section 3 - Evidence Trail
    "station_label_seed_field": "Seed Field",
    "station_label_trading_post": "Trading Post",
    "station_label_workshop": "Workshop",
    "station_label_rail_yard": "Rail Yard",
    "station_label_calendar_house": "Calendar House",
    "station_label_town_hall": "Town Hall",
    "station_label_watchtower": "Watchtower",
    "station_missing": "Not yet built for this ticker.",
    "station_presence_present": "Present",
    "station_presence_missing": "Missing",
    "station_presence_stale": "Stale",
    "station_presence_under_review": "Under review",
    "station_summary_fmt_artifact": (
        "Latest day {date}; cumulative {cum_pct}%."
    ),
    "station_summary_fmt_signal_engine": (
        "Current signal {signal}, active pair {pair}."
    ),
    "station_summary_fmt_calendar_house": (
        "Saved timeframes: {timeframes}."
    ),
    "station_summary_fmt_watchtower": (
        "Health report present (schema {schema})."
    ),
    # Signal labels (also the canonical Buy/Short/None strings).
    "signal_buy": "Buy",
    "signal_short": "Short",
    "signal_none": "None",
    # Coverage labels.
    "coverage_full": COVERAGE_FULL,
    "coverage_partial": COVERAGE_PARTIAL,
    "coverage_stale": COVERAGE_STALE,
    "coverage_under_review": COVERAGE_UNDER_REVIEW,
    "coverage_pipeline_incomplete": COVERAGE_PIPELINE_INCOMPLETE,
    # Section 4
    "what_prjct9_is": (
        "PRJCT9 is a pattern-discovery engine. It studies saved "
        "historical signal behavior, ranks current signal alignment, "
        "and exposes coverage gaps instead of hiding them."
    ),
    # Section 5
    "what_it_is_not_bullets": (
        "Not investment advice.",
        "Not a live trading signal feed.",
        "Not a guarantee of future performance.",
        "Saved research only.",
    ),
    # Phase 6C-8 leader-eligibility banner. Shown above the
    # scoreboard table when zero rows pass the leader gate.
    "no_current_leaders": (
        "No current leaderboard-qualified tickers are available "
        "from saved research."
    ),
    # Human-readable mappings for ``ranking_blocked_reason`` codes.
    # Visible in tooltips / audit tools; the data attribute itself
    # stays as the stable issue code.
    "ranking_block_health_report_blocked": (
        "Catalogue health report flags this ticker as blocked."
    ),
    "ranking_block_missing_confluence": (
        "No saved Confluence verdict for this ticker yet."
    ),
    "ranking_block_stale_confluence": (
        "Confluence verdict is older than the current "
        "leaderboard cutoff."
    ),
    "ranking_block_confluence_agreement_unavailable": (
        "Saved Confluence verdict is missing agreement fields."
    ),
    "ranking_block_missing_multitimeframe_trafficflow_bridge": (
        "Multi-timeframe TrafficFlow / K-build bridge is not yet "
        "built for this ticker."
    ),
    "ranking_block_insufficient_trafficflow_k_coverage": (
        "TrafficFlow saved K-build coverage is incomplete."
    ),
    # Chart copy (Plotly trace names + axis titles). Centralized so
    # the copy-centralization test catches them along with section
    # copy. Trace name for the close-price line is a format string
    # because the ticker is interpolated.
    # Phase 6G-4: trace labels read as "research" rather
    # than terminal cockpit. The ticker is already
    # established by the Featured panel scope so the close
    # price line drops the {ticker} placeholder.
    "chart_trace_engine_capture": (
        "Saved cumulative capture (research)"
    ),
    "chart_trace_close_price_fmt": "Close price",
    "chart_axis_date": "Date",
    "chart_axis_cumulative_capture": "Cumulative Capture (%)",
    "chart_axis_close_price": "Close Price",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BoardRow:
    """One row of the Town Hall Scoreboard.

    ``agreement_active`` / ``agreement_total`` come from the latest
    confluence research_day_v1 artifact for this ticker when present;
    both stay ``None`` if no confluence artifact exists.
    ``coverage`` is one of the COVERAGE_* labels. ``rank`` is set to
    1 / 2 / 3 for the top three rows after ranking; otherwise ``None``.

    Phase 6C-8 audit-gate fields:

      * ``leader_eligible`` (bool) - the strict gate
        ``confluence_pipeline_readiness.inspect_ticker_pipeline``
        computes. Only ``True`` rows are eligible for a top-3 rank
        badge. Defaults to ``False`` so callers building rows by
        hand never accidentally promote an un-checked ticker.
      * ``ranking_blocked_reason`` (str) - the single most
        important issue code blocking eligibility, surfaced as
        a ``data-ranking-blocked-reason`` attribute on the
        rendered ``<tr>``. Empty string when the row is
        eligible.
    """

    ticker: str
    signal: str
    signal_value: int
    agreement_active: Optional[int]
    agreement_total: Optional[int]
    coverage: str
    as_of: Optional[str]
    rank: Optional[int] = None
    leader_eligible: bool = False
    ranking_blocked_reason: str = ""


@dataclass
class EvidenceStation:
    """One station on the Evidence Trail. ``presence`` is one of
    PRESENCE_PRESENT, PRESENCE_MISSING, PRESENCE_STALE,
    PRESENCE_UNDER_REVIEW. ``as_of`` and ``summary`` may be ``None``
    when the station has no saved artifact."""

    station_id: str
    label: str
    presence: str
    as_of: Optional[str] = None
    summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _default_sig_lib_dir() -> Path:
    return _project_dir() / "signal_library" / "data" / "stable"


def _ticker_from_cache_filename(name: str) -> Optional[str]:
    """Reverse the ``<TICKER>_precomputed_results.pkl`` convention.

    Filenames preserve a ``^GSPC`` index as ``_GSPC_...``; we map the
    leading underscore back to ``^`` so the display ticker matches
    user expectations.
    """
    suffix = "_precomputed_results.pkl"
    if not name.endswith(suffix):
        return None
    stem = name[: -len(suffix)].strip()
    if not stem:
        return None
    if stem.startswith("_"):
        return "^" + stem[1:]
    return stem


def _filename_safe_ticker(ticker: str) -> str:
    """Mirror the ``_FILENAME_SAFE_RX`` rewrite in research_artifacts
    (we don't import the private regex to avoid coupling)."""
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    # ^GSPC -> _GSPC. Other special chars normalized to underscore.
    s = s.replace("^", "_")
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
    )
    return "".join(c if c in allowed else "_" for c in s)


def _ticker_form_candidates(ticker: str) -> list[str]:
    """Real form first (``^GSPC``) then filename-safe form
    (``_GSPC``). Some artifacts persist one form, some the other."""
    real = str(ticker or "").strip().upper()
    if not real:
        return []
    safe = _filename_safe_ticker(real)
    out: list[str] = []
    for cand in (real, safe):
        if cand and cand not in out:
            out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Process-lifetime cache
# ---------------------------------------------------------------------------

# Caching keyed by (cache_dir, artifact_root, sig_lib_dir). Always
# reset in tests via ``reset_board_cache()``.
_BOARD_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}


def reset_board_cache() -> None:
    _BOARD_CACHE.clear()


def _cache_key(
    cache_dir: Path, artifact_root: Path, sig_lib_dir: Path,
) -> tuple[str, str, str]:
    return (
        str(cache_dir.resolve()),
        str(artifact_root.resolve()),
        str(sig_lib_dir.resolve()),
    )


# ---------------------------------------------------------------------------
# Artifact indexing
# ---------------------------------------------------------------------------


def _artifact_last_date(art: Any) -> Optional[str]:
    """Latest daily-row date in ISO YYYY-MM-DD form, or ``None`` if
    the artifact has no daily rows."""
    daily = getattr(art, "daily", None) or []
    if not daily:
        return None
    row = daily[-1]
    if not isinstance(row, dict):
        return None
    date = row.get("date")
    return str(date) if date else None


def _parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except Exception:
        return None


@dataclass
class _ArtifactRef:
    path: Path
    artifact: Any
    last_date: Optional[str]
    mtime: float


def _index_artifacts_by_engine_target(
    artifact_root: Path,
) -> dict[tuple[str, str], _ArtifactRef]:
    """Group every saved research_day_v1 artifact by
    ``(engine, target_ticker)`` and keep the newest one for each pair.

    Newest = latest daily-row date when available, else newest file
    mtime. Ticker keys are upper-cased real-form (``^GSPC``).
    """
    out: dict[tuple[str, str], _ArtifactRef] = {}
    try:
        paths = _ra.discover_research_artifacts(base_dir=artifact_root)
    except Exception:
        return out
    for path in paths:
        try:
            art = _ra.read_research_day_artifact(path)
        except Exception:
            art = None
        if art is None:
            continue
        engine = (getattr(art, "engine", "") or "").strip()
        target = (getattr(art, "target_ticker", "") or "").strip().upper()
        if not engine or not target:
            continue
        last_date = _artifact_last_date(art)
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        ref = _ArtifactRef(
            path=path, artifact=art, last_date=last_date, mtime=mtime,
        )
        key = (engine, target)
        existing = out.get(key)
        if existing is None or _ref_is_newer(ref, existing):
            out[key] = ref
    return out


def _ref_is_newer(a: _ArtifactRef, b: _ArtifactRef) -> bool:
    """Order rule: latest daily date wins; ties / both-missing fall
    back to file mtime."""
    da = _parse_iso_date(a.last_date)
    db = _parse_iso_date(b.last_date)
    if da is not None and db is not None:
        if da != db:
            return da > db
        return a.mtime > b.mtime
    if da is not None and db is None:
        return True
    if da is None and db is not None:
        return False
    return a.mtime > b.mtime


def _normalize_signal_label(value: Any) -> str:
    """Coerce an artifact-stored signal label to one of the canonical
    Buy / Short / None strings. Anything we don't recognize collapses
    to None (signal_value 0)."""
    if value is None:
        return "None"
    s = str(value).strip()
    if not s:
        return "None"
    head = s.split()[0].lower()
    if head == "buy":
        return "Buy"
    if head == "short":
        return "Short"
    return "None"


def _signal_from_refs(
    confluence_ref: Optional[_ArtifactRef],
    impactsearch_ref: Optional[_ArtifactRef],
) -> str:
    """Derive a Buy/Short/None label for a scoreboard row from
    saved artifacts only - never opens the Spymaster cache PKL.

    Priority:
      1. Confluence research_day_v1 artifact's last daily
         ``confluence_signal`` (the engine that's already the
         scoreboard's high-score signal).
      2. ImpactSearch research_day_v1 artifact's last daily
         ``signal`` (a single-source fallback when no confluence
         artifact has been built for the ticker).
      3. Otherwise ``"None"`` - the documented placeholder for
         cache-only tickers that haven't been hydrated yet.
    """
    for ref, signal_field in (
        (confluence_ref, "confluence_signal"),
        (impactsearch_ref, "signal"),
    ):
        if ref is None or ref.artifact is None:
            continue
        daily = getattr(ref.artifact, "daily", None) or []
        if not daily:
            continue
        row = daily[-1]
        if not isinstance(row, dict):
            continue
        label = _normalize_signal_label(row.get(signal_field))
        if label in {"Buy", "Short", "None"}:
            return label
    return "None"


def _confluence_active_total(ref: Optional[_ArtifactRef]) -> tuple[
    Optional[int], Optional[int],
]:
    """Pull ``(active_count, total)`` from the latest daily row of a
    confluence artifact. ``total`` falls back to the artifact's
    timeframes list when the row does not embed it."""
    if ref is None or ref.artifact is None:
        return None, None
    art = ref.artifact
    daily = getattr(art, "daily", None) or []
    if not daily:
        return None, None
    row = daily[-1]
    if not isinstance(row, dict):
        return None, None
    active = row.get("active_count")
    total = row.get("available_count")
    if total is None:
        total = row.get("total_count")
    if total is None:
        timeframes = getattr(art, "timeframes", None) or []
        if timeframes:
            total = len(timeframes)
    try:
        a = int(active) if active is not None else None
    except Exception:
        a = None
    try:
        t = int(total) if total is not None else None
    except Exception:
        t = None
    if a is None or t is None:
        return None, None
    return a, t


def _calendar_house_timeframes_present(
    ticker: str, sig_lib_dir: Path,
) -> list[str]:
    """Saved non-daily interval libraries present on disk for this
    ticker (``1wk`` / ``1mo`` / ``3mo`` / ``1y``)."""
    if not sig_lib_dir.exists() or not sig_lib_dir.is_dir():
        return []
    present: list[str] = []
    for form in _ticker_form_candidates(ticker):
        for interval in CALENDAR_HOUSE_INTERVALS:
            p = sig_lib_dir / f"{form}_stable_v1_0_0_{interval}.pkl"
            if p.exists() and p.is_file() and interval not in present:
                present.append(interval)
    return present


# ---------------------------------------------------------------------------
# Health-report probes (read-only)
# ---------------------------------------------------------------------------


def _read_health_report(
    artifact_root: Optional[Path] = None,
) -> Optional[dict]:
    """Best-effort read of the existing on-disk health report. Returns
    ``None`` when no report exists or it is unreadable."""
    try:
        return _rch.read_catalogue_health_report(base_dir=artifact_root)
    except Exception:
        return None


def _health_blocked_targets(report: Optional[Mapping[str, Any]]) -> set[str]:
    """Set of upper-cased tickers the health report flags as having
    blocked / gap / issue evidence. Empty set if the report is absent
    or shapeless."""
    if not isinstance(report, Mapping):
        return set()
    out: set[str] = set()
    for entry in report.get("by_target", []) or []:
        if not isinstance(entry, Mapping):
            continue
        target = entry.get("target_ticker")
        if not target:
            continue
        blocked = entry.get("engines_blocked") or []
        if blocked:
            out.add(str(target).strip().upper())
    return out


def _health_schema_version(
    report: Optional[Mapping[str, Any]],
) -> Optional[str]:
    if not isinstance(report, Mapping):
        return None
    v = report.get("schema")
    return str(v) if v else None


# ---------------------------------------------------------------------------
# Coverage status
# ---------------------------------------------------------------------------


def _is_stale_iso(date_iso: Optional[str], *, now: datetime) -> bool:
    parsed = _parse_iso_date(date_iso)
    if parsed is None:
        return False
    delta = now - parsed.replace(tzinfo=parsed.tzinfo or now.tzinfo)
    return delta.days > STALE_DAYS


def coverage_status_for_ticker(
    ticker: str,
    *,
    has_engine_cache: bool,
    impactsearch_ref: Optional[_ArtifactRef],
    stackbuilder_ref: Optional[_ArtifactRef],
    trafficflow_ref: Optional[_ArtifactRef],
    confluence_ref: Optional[_ArtifactRef],
    calendar_timeframes: Sequence[str],
    health_blocked: Iterable[str],
    now: Optional[datetime] = None,
) -> str:
    """Resolve one of the four coverage labels for a ticker, applying
    the priority order under-review > stale > full > partial.

    ``has_engine_cache`` reflects only whether the Spymaster cache
    PKL file exists on disk (filename presence). The Spymaster PKL
    is NOT opened during scoreboard discovery, so the cache's
    ``date_range.end`` does not feed the staleness check; only
    research_day_v1 artifact dates do.

    Cache-only tickers (no saved research_day_v1 artifacts) therefore
    cannot be flagged ``Stale`` from this entrypoint - they fall
    through to ``Partial``. The Featured panel still hydrates the
    selected ticker's PKL and surfaces fresh / stale numbers there.
    """
    now = now or datetime.now(timezone.utc)
    blocked = {str(t).strip().upper() for t in (health_blocked or [])}

    if ticker.strip().upper() in blocked:
        return COVERAGE_UNDER_REVIEW

    # Staleness uses research_day_v1 artifact dates only. The cache
    # PKL stays unopened during discovery (perf contract).
    candidate_dates: list[str] = []
    for ref in (
        impactsearch_ref, stackbuilder_ref,
        trafficflow_ref, confluence_ref,
    ):
        if ref is None:
            continue
        if ref.last_date:
            candidate_dates.append(ref.last_date)

    if candidate_dates:
        parsed = [
            d for d in (_parse_iso_date(c) for c in candidate_dates)
            if d is not None
        ]
        if parsed:
            newest = max(parsed)
            delta = (now - newest.replace(
                tzinfo=newest.tzinfo or now.tzinfo,
            )).days
            if delta > STALE_DAYS:
                return COVERAGE_STALE

    has_is = impactsearch_ref is not None
    has_sb = stackbuilder_ref is not None
    has_tf = trafficflow_ref is not None
    has_conf = confluence_ref is not None

    timeframe_evidence_count = len(set(calendar_timeframes))
    if confluence_ref is not None:
        _, total = _confluence_active_total(confluence_ref)
        if total and total > timeframe_evidence_count:
            timeframe_evidence_count = int(total)

    is_full = (
        has_engine_cache
        and has_is and has_sb and has_tf and has_conf
        and timeframe_evidence_count >= 2
    )
    if is_full:
        return COVERAGE_FULL
    if has_engine_cache:
        return COVERAGE_PARTIAL
    # Defensive: should not happen because discovery only emits rows
    # for tickers whose cache filename exists. Leave as Partial so a
    # caller-side bug never causes the row to silently disappear.
    return COVERAGE_PARTIAL


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_board_catalogue(
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
    health_report: Optional[Mapping[str, Any]] = None,
    use_cache: bool = True,
    now: Optional[datetime] = None,
) -> list[BoardRow]:
    """Build a BoardRow per cached ticker from fast saved metadata.

    Data contract (Phase 6C-7 perf amendment):

      * The Spymaster cache PKL is NOT opened during discovery. The
        cache contributes only its FILENAME presence; opening every
        PKL through ``load_primary_signal_engine_payload`` on real
        disk takes ~50 seconds for ~200 tickers and breaks the
        public-MVP cold-boot contract.
      * The selected ticker's PKL is still hydrated for the Featured
        panel via ``_ticker_payload`` (one call per selection).
      * Per-row ``signal`` is derived from saved research_day_v1
        artifacts only - the latest confluence artifact's
        ``confluence_signal`` (priority 1) then the latest
        impactsearch artifact's ``signal`` (priority 2). Cache-only
        tickers with no artifacts report ``signal="None"`` /
        ``signal_value=0`` until either artifact lands. This is an
        explicit limitation, not an error condition.
      * ``agreement_active`` / ``agreement_total`` come from the
        latest confluence artifact's last daily row when present.
      * ``coverage`` follows ``coverage_status_for_ticker``; cache
        staleness cannot be inferred without opening the PKL, so
        cache-only tickers stay ``Partial`` regardless of cache
        mtime.
      * ``as_of`` is the newest research_day_v1 daily row date
        across all engines for the ticker; falls back to ``None``
        (rendered as ``"-"``) when no artifact has been built.

      * No live engine call. No yfinance. No disk writes.
      * Process-lifetime cached on ``(cache_dir, artifact_root,
        sig_lib_dir)``. ``reset_board_cache()`` clears.

    The cached entry also keeps the indexed artifact map so the
    Featured / Evidence callbacks reuse it without re-walking
    artifact JSON. ``reset_board_cache()`` busts both.
    """
    cache_d = Path(cache_dir) if cache_dir else _default_cache_dir()
    artifact_d = (
        Path(artifact_root) if artifact_root else _default_artifact_root()
    )
    sig_d = (
        Path(sig_lib_dir) if sig_lib_dir else _default_sig_lib_dir()
    )
    key = _cache_key(cache_d, artifact_d, sig_d)
    if use_cache:
        cached = _BOARD_CACHE.get(key)
        if cached is not None and "rows" in cached:
            return list(cached["rows"])

    now = now or datetime.now(timezone.utc)
    rows: list[BoardRow] = []

    if not cache_d.exists() or not cache_d.is_dir():
        if use_cache:
            _BOARD_CACHE[key] = {
                "rows": rows, "artifact_index": {},
            }
        return rows

    artifact_index = _index_artifacts_by_engine_target(artifact_d)
    report = (
        health_report if health_report is not None
        else _read_health_report(artifact_d)
    )
    blocked = _health_blocked_targets(report)
    # Pre-compute the set of tickers with any confluence artifact
    # on disk. The leader gate REQUIRES a confluence artifact, so
    # any ticker outside this set is provably ineligible and the
    # board can synthesize the verdict without calling the full
    # readiness layer. With ~1,600 cached tickers and ~2 confluence
    # artifacts on disk today, this is the dominant perf win.
    confluence_tickers = _cpr.list_tickers_with_confluence_artifacts(
        artifact_d,
    )

    tickers_seen: set[str] = set()
    for entry in sorted(cache_d.iterdir()):
        if not entry.is_file():
            continue
        ticker = _ticker_from_cache_filename(entry.name)
        if not ticker:
            continue
        norm = ticker.strip().upper()
        if norm in tickers_seen:
            continue
        tickers_seen.add(norm)

        impactsearch_ref = artifact_index.get(
            (ENGINE_IMPACTSEARCH, norm),
        )
        stackbuilder_ref = artifact_index.get(
            (ENGINE_STACKBUILDER, norm),
        )
        trafficflow_ref = artifact_index.get(
            (ENGINE_TRAFFICFLOW, norm),
        )
        confluence_ref = artifact_index.get(
            (ENGINE_CONFLUENCE, norm),
        )

        signal = _signal_from_refs(confluence_ref, impactsearch_ref)
        signal_value = SIGNAL_TO_VALUE.get(signal, 0)

        agreement_active, agreement_total = _confluence_active_total(
            confluence_ref,
        )

        calendar_tfs = _calendar_house_timeframes_present(ticker, sig_d)

        coverage = coverage_status_for_ticker(
            ticker,
            has_engine_cache=True,
            impactsearch_ref=impactsearch_ref,
            stackbuilder_ref=stackbuilder_ref,
            trafficflow_ref=trafficflow_ref,
            confluence_ref=confluence_ref,
            calendar_timeframes=calendar_tfs,
            health_blocked=blocked,
            now=now,
        )

        as_of = _row_as_of_date(artifact_index, norm)

        if norm in confluence_tickers or norm in blocked:
            # The expensive readiness path is reserved for the
            # tickers that could plausibly be leader-eligible
            # (have a confluence artifact) and for tickers that
            # are explicitly health-blocked (so the data attribute
            # surfaces the right blocked_reason). Everything else
            # short-circuits to "missing_confluence_day_artifact".
            readiness = _cpr.inspect_ticker_pipeline(
                ticker,
                cache_dir=cache_d,
                artifact_root=artifact_d,
                signal_library_dir=sig_d,
                health_report=report,
                now=now,
            )
            leader_eligible = bool(readiness.leader_eligible)
            ranking_blocked_reason = _primary_ranking_block_code(
                readiness,
            )
        else:
            leader_eligible = False
            ranking_blocked_reason = (
                _cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT
            )

        # Phase 6C-8 audit-tighten: reconcile the Coverage label
        # with the readiness verdict so the visible column never
        # contradicts what the readiness layer is enforcing. A
        # stale-confluence block forces Stale; a missing-bridge or
        # K-coverage block forces Pipeline incomplete; a health
        # block forces Under review.
        coverage = _reconcile_coverage_with_readiness(
            coverage, ranking_blocked_reason,
        )

        rows.append(BoardRow(
            ticker=ticker,
            signal=signal,
            signal_value=signal_value,
            agreement_active=agreement_active,
            agreement_total=agreement_total,
            coverage=coverage,
            as_of=as_of,
            leader_eligible=leader_eligible,
            ranking_blocked_reason=ranking_blocked_reason,
        ))

    if use_cache:
        _BOARD_CACHE[key] = {
            "rows": rows, "artifact_index": artifact_index,
        }
    return rows


# Priority order for collapsing readiness.issue_codes into a single
# ``data-ranking-blocked-reason`` string. The first matching code
# wins. Codes not listed here never surface on the data attribute,
# even when they appear in the readiness verdict.
_RANKING_BLOCK_PRIORITY: tuple[str, ...] = (
    _cpr.ISSUE_HEALTH_REPORT_BLOCKED,
    _cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,
    _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT,
    _cpr.ISSUE_CONFLUENCE_AGREEMENT_UNAVAILABLE,
    _cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
    _cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE,
)


# Phase 6C-8 audit-tighten: reconcile the visible Coverage label
# with the readiness verdict so the scoreboard never shows a
# row as "Full" while the readiness layer is blocking its rank.
_COVERAGE_OVERRIDE_BY_BLOCKED_REASON: dict[str, str] = {
    _cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT: COVERAGE_STALE,
    _cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE: (
        COVERAGE_PIPELINE_INCOMPLETE
    ),
    _cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE: (
        COVERAGE_PIPELINE_INCOMPLETE
    ),
    _cpr.ISSUE_HEALTH_REPORT_BLOCKED: COVERAGE_UNDER_REVIEW,
}


def _reconcile_coverage_with_readiness(
    coverage: str, ranking_blocked_reason: str,
) -> str:
    """Override the standalone coverage label when the readiness
    layer has blocked ranking on a stronger signal. Returns the
    original coverage when no override applies."""
    override = _COVERAGE_OVERRIDE_BY_BLOCKED_REASON.get(
        ranking_blocked_reason,
    )
    return override if override is not None else coverage


def _primary_ranking_block_code(
    readiness: _cpr.TickerPipelineReadiness,
) -> str:
    """Return the single highest-priority issue code blocking
    leader eligibility for this row, or ``""`` when the row is
    eligible. Used to populate ``data-ranking-blocked-reason``."""
    if readiness.leader_eligible:
        return ""
    codes = set(readiness.issue_codes)
    for code in _RANKING_BLOCK_PRIORITY:
        if code in codes:
            return code
    # No priority code matched but eligibility was still denied -
    # fall back to the first issue code we did see so the data
    # attribute is never silently empty when the row is blocked.
    return readiness.issue_codes[0] if readiness.issue_codes else ""


def _cached_artifact_index(
    cache_dir: Path, artifact_root: Path, sig_lib_dir: Path,
) -> dict[tuple[str, str], _ArtifactRef]:
    """Return the artifact index produced by the most recent
    ``discover_board_catalogue`` call for the same directory triple,
    or build one if discovery has not yet run for this triple.

    Callbacks reuse this so per-click handlers never re-walk the
    artifact tree.
    """
    key = _cache_key(cache_dir, artifact_root, sig_lib_dir)
    entry = _BOARD_CACHE.get(key)
    if entry and "artifact_index" in entry:
        return entry["artifact_index"]
    idx = _index_artifacts_by_engine_target(artifact_root)
    _BOARD_CACHE.setdefault(key, {})["artifact_index"] = idx
    return idx


def _row_as_of_date(
    artifact_index: Mapping[tuple[str, str], _ArtifactRef],
    norm_ticker: str,
) -> Optional[str]:
    """Scoreboard row as-of date. Sources research_day_v1 artifacts
    only; cache-only tickers return ``None`` (rendered as ``"-"``).

    The Spymaster cache's ``date_range.end`` is intentionally NOT
    consulted here because that requires opening the PKL, which the
    perf contract forbids during cold-boot discovery."""
    candidates: list[str] = []
    for engine in (
        ENGINE_IMPACTSEARCH, ENGINE_STACKBUILDER,
        ENGINE_TRAFFICFLOW, ENGINE_CONFLUENCE,
    ):
        ref = artifact_index.get((engine, norm_ticker))
        if ref and ref.last_date:
            candidates.append(ref.last_date)
    if not candidates:
        return None
    parsed = [
        d for d in (_parse_iso_date(c) for c in candidates)
        if d is not None
    ]
    if not parsed:
        return None
    return max(parsed).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Ranking + default selection
# ---------------------------------------------------------------------------


def rank_board_rows(rows: Sequence[BoardRow]) -> list[BoardRow]:
    """Sort the scoreboard and assign top-3 rank badges.

    Sort order (documented):
      * Leader-eligible rows first (see Phase 6C-8 readiness gate).
      * Within leader-eligible rows, descending by confluence
        agreement count, alphabetical ticker for ties.
      * Within non-eligible rows, the same ordering with agreement-
        less rows sinking last.

    Rank badges (Phase 6C-8 gate):

      * ``rank=1|2|3`` is assigned ONLY to rows whose
        ``leader_eligible`` is True AND ``agreement_active`` is not
        ``None``. A leader-eligible row without a usable agreement
        count is malformed and is therefore not awarded a rank.
      * Stale / partial / under-review / cache-only / pipeline-
        incomplete rows NEVER receive a top-3 badge, even when
        their agreement count is the highest on the board.
      * If fewer than three rankable rows exist, fewer than three
        rows receive ``rank``. Empty board -> nobody gets a badge.

    This keeps the public "current leaders" semantics honest: only
    tickers whose Confluence verdict is fresh, present, and
    health-clean can sit on the podium.
    """
    def key(r: BoardRow) -> tuple[int, int, str]:
        # 0 for eligible rows, 1 for ineligible. Lower wins under
        # ascending sort, which gives eligible rows the front of
        # the scoreboard.
        eligibility_bucket = 0 if r.leader_eligible else 1
        agreement = (
            -int(r.agreement_active) if r.agreement_active is not None
            else 1  # rows without agreement sort last
        )
        return (eligibility_bucket, agreement, r.ticker)

    ordered = sorted(rows, key=key)
    for r in ordered:
        r.rank = None
    next_rank = 1
    for row in ordered:
        if next_rank > 3:
            break
        if not row.leader_eligible:
            # Sort order pushes ineligible rows to the back; once
            # we hit one, no later row can be eligible.
            break
        if row.agreement_active is None:
            # Defensive: an eligible row should always carry an
            # agreement count. Skip the badge if not.
            continue
        row.rank = next_rank
        next_rank += 1
    return list(ordered)


def default_selected_ticker(rows: Sequence[BoardRow]) -> str:
    """SPY when present; otherwise the first alphabetical available
    ticker; otherwise an empty string for the empty-state path."""
    if not rows:
        return ""
    tickers = sorted({r.ticker for r in rows if r.ticker})
    if "SPY" in tickers:
        return "SPY"
    return tickers[0] if tickers else ""


# ---------------------------------------------------------------------------
# Chart helper
# ---------------------------------------------------------------------------


def _chart_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_signal_engine_figure(
    ticker: str, payload: Mapping[str, Any],
) -> Optional[Any]:
    """Return a Plotly Figure with two traces (cumulative capture on
    y, raw close on yaxis2) or ``None`` when the payload lacks chart
    rows. Plotly is imported inside the helper so module load works
    even in stripped envs."""
    try:
        import plotly.graph_objects as go
    except Exception:
        return None
    chart_rows = payload.get("chart_rows") if payload else []
    if not chart_rows:
        return None
    dates = [r.get("date") for r in chart_rows]
    cum = [
        _chart_float(r.get("cumulative_capture_pct")) or 0.0
        for r in chart_rows
    ]
    closes = [_chart_float(r.get("close")) for r in chart_rows]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        name=BOARD_COPY["chart_trace_engine_capture"],
        x=dates, y=cum, mode="lines", yaxis="y",
        line={"color": DESIGN_TOKENS["color_green"], "width": 1.6},
    ))
    if any(c is not None for c in closes):
        fig.add_trace(go.Scatter(
            name=BOARD_COPY["chart_trace_close_price_fmt"].format(
                ticker=ticker,
            ),
            x=dates, y=closes, mode="lines", yaxis="y2",
            line={
                "color": DESIGN_TOKENS["color_text"],
                "width": 1.0,
                "dash": "dot",
            },
            opacity=0.62,
        ))
    fig.update_layout(
        paper_bgcolor=DESIGN_TOKENS["color_black"],
        plot_bgcolor=DESIGN_TOKENS["color_black"],
        font={"color": DESIGN_TOKENS["color_text"], "size": 11},
        xaxis={
            "gridcolor": DESIGN_TOKENS["color_border"],
            "title": BOARD_COPY["chart_axis_date"],
        },
        yaxis={
            "gridcolor": DESIGN_TOKENS["color_border"],
            "title": BOARD_COPY["chart_axis_cumulative_capture"],
        },
        yaxis2={
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
            "zeroline": False,
            "title": BOARD_COPY["chart_axis_close_price"],
        },
        legend={
            "orientation": "h",
            "yanchor": "bottom", "y": 1.02,
            "xanchor": "left", "x": 0,
        },
        margin={"l": 64, "r": 64, "t": 44, "b": 36},
        height=280,
    )
    return fig


# ---------------------------------------------------------------------------
# Render helpers (Dash). Imports are lazy so module load never
# requires Dash to be installed.
# ---------------------------------------------------------------------------


def _dash_modules():
    """Return (dash, dcc, html) or raise the underlying ImportError.
    Centralized so callers don't repeat the import dance."""
    import dash  # noqa: F401
    from dash import dcc, html
    return dash, dcc, html


def _fmt_metric(value: Any, kind: str) -> str:
    if value is None:
        return BOARD_COPY["as_of_unavailable"]
    try:
        if kind == "pct2":
            return f"{float(value):.2f}"
        if kind == "ratio2":
            return f"{float(value):.2f}"
        if kind == "int":
            return f"{int(value)}"
    except Exception:
        pass
    return str(value)


def render_scoreboard(
    rows: Sequence[BoardRow],
    selected_ticker: Optional[str] = None,
    *,
    id_prefix: str = "",
) -> Any:
    """Build the Town Hall Scoreboard table. Each tr is clickable
    via a pattern-matching id ``{"type": "scoreboard-row", "ticker": ...}``.
    Empty state shows the BOARD_COPY ``empty_scoreboard`` string.

    ``id_prefix`` (Phase 6G-1) lets the caller render a second
    instance of this table in the same layout (e.g. the
    Saved Research Archive section) without Dash's
    ``DuplicateIdError`` — the wrapper Div and inner Table
    pick up the prefix. The row Tr ids stay as pattern-
    matching dicts which are unique per ticker regardless.
    """
    _, _, html = _dash_modules()

    if not rows:
        return html.Div(
            BOARD_COPY["empty_scoreboard"],
            id="scoreboard-empty-message",
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "padding": "12px",
                "border": (
                    "1px dashed " + DESIGN_TOKENS["color_border"]
                ),
                "borderRadius": "6px",
            },
        )

    header_cells = [
        html.Th(
            BOARD_COPY["col_ticker"], scope="col",
            style=_th_style(),
        ),
        html.Th(
            BOARD_COPY["col_signal"], scope="col",
            style=_th_style(),
        ),
        html.Th(
            BOARD_COPY["col_agreement"], scope="col",
            style=_th_style(),
        ),
        html.Th(
            BOARD_COPY["col_coverage"], scope="col",
            style=_th_style(),
        ),
        # Phase 6F-7: the AS OF column carries ISO dates
        # (``2026-05-08``). On narrow viewports the column was
        # being squeezed by the table-fit-to-width pass and the
        # cell content wrapped to fragments like ``202`` / ``05-``.
        # ``whiteSpace=nowrap`` keeps the date glyphs glued
        # together; the parent scroll wrapper (see
        # ``scoreboard-table-wrapper`` below) absorbs the
        # resulting overflow so the page itself never grows
        # horizontal scroll.
        html.Th(
            BOARD_COPY["col_as_of"], scope="col",
            style=_th_style(nowrap=True),
        ),
    ]

    body_rows: list[Any] = []
    for r in rows:
        agreement_text = (
            f"{r.agreement_active}/{r.agreement_total}"
            if r.agreement_active is not None
            and r.agreement_total is not None
            else BOARD_COPY["agreement_unavailable"]
        )
        signal_color = _signal_color(r.signal)
        is_selected = (
            selected_ticker is not None
            and r.ticker == selected_ticker
        )
        body_rows.append(html.Tr(
            id={"type": "scoreboard-row", "ticker": r.ticker},
            n_clicks=0,
            role="button",
            **{
                "data-ticker": r.ticker,
                "data-signal": r.signal,
                "data-signal-value": str(r.signal_value),
                "data-coverage": r.coverage,
                "data-rank": str(r.rank) if r.rank is not None else "",
                "data-leader-eligible": (
                    "true" if r.leader_eligible else "false"
                ),
                "data-ranking-blocked-reason": (
                    r.ranking_blocked_reason or ""
                ),
            },
            style=_tr_style(
                is_selected, leader_eligible=r.leader_eligible,
            ),
            children=[
                html.Td(r.ticker, style=_td_style(weight="bold")),
                # Phase 6G-1: render a public-friendly
                # consensus label ("No consensus") in place
                # of the raw "None" string. ``data-signal``
                # on the Tr above is still the canonical
                # "None" / "Buy" / "Short" value, so the
                # data contract is unchanged.
                html.Td(
                    _visible_consensus_label(r.signal),
                    style=_td_style(color=signal_color),
                ),
                html.Td(agreement_text, style=_td_style()),
                # Phase 6G-4: render coverage as a wax-seal
                # style pill (rounded filled badge with a
                # leading dot) so first-time visitors read
                # it as a stamped status rather than a bare
                # colored word. The underlying ``r.coverage``
                # text and ``data-coverage`` attribute on
                # the Tr are unchanged - only the cell's
                # presentation differs.
                html.Td(
                    _render_coverage_pill(r.coverage),
                    style=_td_style(),
                ),
                html.Td(
                    r.as_of or BOARD_COPY["as_of_unavailable"],
                    style=_td_style(nowrap=True),
                ),
            ],
        ))

    # Phase 6F-7: the table itself is unchanged
    # (``width: 100%``), but we wrap it in a horizontally
    # scrollable container so any overflow on narrow
    # viewports (e.g. 390x844 mobile) is contained INSIDE
    # the wrapper rather than producing page-level
    # horizontal scroll. ``data-mobile-overflow="contained"``
    # is the stable contract test_daily_signal_board pins.
    return html.Div(
        id=f"{id_prefix}scoreboard-table-wrapper",
        **{"data-mobile-overflow": "contained"},
        style={
            "overflowX": "auto",
            "WebkitOverflowScrolling": "touch",
            "width": "100%",
        },
        children=html.Table(
            id=f"{id_prefix}scoreboard-table",
            style={
                "width": "100%",
                "borderCollapse": "collapse",
                "backgroundColor": DESIGN_TOKENS["color_black"],
                "color": DESIGN_TOKENS["color_text"],
            },
            children=[
                html.Thead(html.Tr(header_cells)),
                html.Tbody(body_rows),
            ],
        ),
    )


def _signal_color(signal: str) -> str:
    if signal == "Buy":
        return DESIGN_TOKENS["color_buy"]
    if signal == "Short":
        return DESIGN_TOKENS["color_short"]
    return DESIGN_TOKENS["color_none"]


def _visible_consensus_label(signal: str) -> str:
    """Phase 6G-1: map the canonical Confluence ``signal``
    value to the public-friendly scoreboard cell text.

    ``data-signal`` on the row stays as the canonical
    ``Buy`` / ``Short`` / ``None`` string for any audit /
    automation that switches on it; only the rendered cell
    text changes."""
    if signal == "Buy":
        return BOARD_COPY["scoreboard_consensus_buy"]
    if signal == "Short":
        return BOARD_COPY["scoreboard_consensus_short"]
    return BOARD_COPY["scoreboard_consensus_none"]


def _partition_rows_for_board(
    rows: Sequence["BoardRow"],
) -> tuple[list["BoardRow"], list["BoardRow"]]:
    """Phase 6G-1: split discovered rows into the
    "current board" set (leader-eligible) and the
    "archive" set (everything else - Partial / Stale /
    Under review / Pipeline incomplete coverage).

    Order is preserved from the input list so the existing
    ranking call upstream still drives what's at the top
    of each subset.
    """
    current: list[BoardRow] = []
    archive: list[BoardRow] = []
    for r in rows:
        if r.leader_eligible:
            current.append(r)
        else:
            archive.append(r)
    return current, archive


def render_archive_scoreboard(
    rows: Sequence["BoardRow"], *,
    selected_ticker: Optional[str] = None,
) -> Any:
    """Phase 6G-1: render the saved-research archive as a
    ``<details>`` collapsible. The inner table reuses the
    same row markup / data attributes the main scoreboard
    uses, so any audit tool that walks ``[data-ticker]``
    nodes still sees every row regardless of which section
    it lives in.

    Collapsed by default. The summary advertises the row
    count so a visitor knows how much research is hiding.
    """
    _, _, html = _dash_modules()
    if not rows:
        return html.Div(
            BOARD_COPY["section_archive_empty"],
            id="section-archive-empty",
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "12px",
                "padding": "8px 0",
            },
        )
    inner_table = render_scoreboard(
        rows, selected_ticker=selected_ticker,
        # Phase 6G-1: distinct DOM ids so the archive
        # table doesn't collide with the main scoreboard's
        # ``scoreboard-table-wrapper`` / ``scoreboard-table``.
        id_prefix="archive-",
    )
    summary_text = BOARD_COPY[
        "section_archive_summary_fmt"
    ].format(count=len(rows))
    return html.Details(
        id="section-archive-details",
        **{"data-archive-row-count": str(len(rows))},
        # Collapsed by default; visitors opt in to the long
        # tail of saved-research rows.
        open=False,
        children=[
            html.Summary(
                summary_text,
                id="section-archive-summary",
                style={
                    "cursor": "pointer",
                    "color": DESIGN_TOKENS["color_muted"],
                    "fontSize": "12px",
                    "padding": "4px 0",
                },
            ),
            html.Div(
                BOARD_COPY["section_archive_intro"],
                id="section-archive-intro",
                style={
                    "color": DESIGN_TOKENS["color_muted"],
                    "fontSize": "12px",
                    "marginBottom": "8px",
                },
            ),
            inner_table,
        ],
    )


def _row_consensus_copy(signal: str) -> str:
    if signal == "Buy":
        return BOARD_COPY["current_pilot_consensus_buy"]
    if signal == "Short":
        return BOARD_COPY["current_pilot_consensus_short"]
    return BOARD_COPY["current_pilot_consensus_none"]


def render_current_pilot_card(
    rows: Sequence["BoardRow"],
    *,
    signal_engine_pair: Optional[str] = None,
    signal_engine_as_of: Optional[str] = None,
) -> Any:
    """Phase 6G-1: render the "Today's Board Status" hero
    card from the top leader-eligible row.

    ``signal_engine_pair`` and ``signal_engine_as_of`` are
    the caller-resolved Signal Engine state for the pilot
    ticker (so the card shows both the Confluence consensus
    and the standalone Signal Engine readout side-by-side).
    Either can be ``None`` if the data isn't available;
    the card falls back to honest "not available" copy.
    """
    _, _, html = _dash_modules()
    leader = next((r for r in rows if r.leader_eligible), None)
    if leader is None:
        body = html.Div(
            BOARD_COPY["current_pilot_no_leader"],
            id="current-pilot-empty",
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "13px",
            },
        )
        return html.Div(
            id="current-pilot-card",
            **{
                "data-current-pilot-ticker": "",
                "data-current-pilot-leader-eligible": "false",
            },
            children=body,
            style={
                "padding": "10px",
                "marginBottom": "10px",
                "border": (
                    "1px dashed " + DESIGN_TOKENS["color_border"]
                ),
                "borderRadius": "4px",
            },
        )

    intro = BOARD_COPY["current_pilot_intro_fmt"].format(
        ticker=leader.ticker,
    )
    consensus = _row_consensus_copy(leader.signal)
    if signal_engine_pair:
        se_line = BOARD_COPY[
            "current_pilot_signal_engine_fmt"
        ].format(pair=signal_engine_pair)
    else:
        se_line = BOARD_COPY[
            "current_pilot_signal_engine_unavailable"
        ]
    consensus_date = leader.as_of or ""
    if consensus_date and signal_engine_as_of:
        as_of_line = BOARD_COPY[
            "current_pilot_as_of_fmt"
        ].format(
            consensus_date=consensus_date,
            se_date=signal_engine_as_of,
        )
    elif consensus_date:
        as_of_line = BOARD_COPY[
            "current_pilot_as_of_partial_consensus_fmt"
        ].format(consensus_date=consensus_date)
    elif signal_engine_as_of:
        as_of_line = BOARD_COPY[
            "current_pilot_as_of_partial_se_fmt"
        ].format(se_date=signal_engine_as_of)
    else:
        as_of_line = ""

    # Phase 6G-4: render the current-pilot card with a
    # pinned-paper feel - a small CSS-drawn "pin" disc at
    # the top-left of the card and a leader-highlight
    # pilot chip at the top-right carrying the ticker
    # symbol. Both are pure CSS / HTML; no image assets
    # introduced. The existing
    # ``data-current-pilot-*`` attributes are preserved
    # verbatim.
    return html.Div(
        id="current-pilot-card",
        **{
            "data-current-pilot-ticker": leader.ticker,
            "data-current-pilot-leader-eligible": "true",
            "data-current-pilot-consensus-signal": leader.signal,
        },
        style={
            "position": "relative",
            "padding": "18px 12px 12px 12px",
            "marginBottom": "10px",
            "border": (
                "1px solid " + DESIGN_TOKENS["color_border"]
            ),
            "borderRadius": "6px",
            "backgroundColor": DESIGN_TOKENS["color_paper"],
            "boxShadow": (
                "inset 0 1px 0 0 " + DESIGN_TOKENS["color_dim"]
            ),
        },
        children=[
            # CSS-drawn "pin" disc anchoring the paper to
            # the notice board.
            html.Span(
                id="current-pilot-pin",
                **{"aria-hidden": "true"},
                style={
                    "position": "absolute",
                    "top": "-6px",
                    "left": "16px",
                    "width": "12px",
                    "height": "12px",
                    "borderRadius": "50%",
                    "backgroundColor": DESIGN_TOKENS["color_pin"],
                    "boxShadow": (
                        "0 0 0 2px " + DESIGN_TOKENS["color_warm_dark"]
                    ),
                },
            ),
            # Leader-highlight pilot chip in the top-right
            # corner; the only place on the page (besides
            # the SPY scoreboard row's left-edge accent)
            # that uses the legacy neon green.
            html.Span(
                leader.ticker,
                id="current-pilot-chip",
                **{"data-pilot-chip-ticker": leader.ticker},
                style={
                    "position": "absolute",
                    "top": "10px",
                    "right": "12px",
                    "padding": "2px 8px",
                    "borderRadius": "10px",
                    "backgroundColor": DESIGN_TOKENS[
                        "color_leader_highlight"
                    ],
                    "color": DESIGN_TOKENS["color_warm_dark"],
                    "fontSize": "11px",
                    "fontWeight": "bold",
                    "letterSpacing": "0.05em",
                },
            ),
            html.Div(
                intro,
                id="current-pilot-intro",
                style={
                    "color": DESIGN_TOKENS["color_text"],
                    "fontSize": "14px",
                    "fontWeight": "bold",
                    "marginBottom": "4px",
                },
            ),
            html.Div(
                consensus,
                id="current-pilot-consensus",
                style={
                    "color": _signal_color(leader.signal),
                    "fontSize": "13px",
                    "marginBottom": "2px",
                },
            ),
            html.Div(
                se_line,
                id="current-pilot-signal-engine",
                style={
                    "color": DESIGN_TOKENS["color_text"],
                    "fontSize": "13px",
                    "marginBottom": "2px",
                },
            ),
            html.Div(
                as_of_line,
                id="current-pilot-as-of",
                style={
                    "color": DESIGN_TOKENS["color_muted"],
                    "fontSize": "12px",
                },
            ),
        ],
    )


def _coverage_color(coverage: str) -> str:
    if coverage == COVERAGE_FULL:
        return DESIGN_TOKENS["color_full"]
    if coverage == COVERAGE_PARTIAL:
        return DESIGN_TOKENS["color_partial"]
    if coverage == COVERAGE_STALE:
        return DESIGN_TOKENS["color_stale"]
    if coverage == COVERAGE_UNDER_REVIEW:
        return DESIGN_TOKENS["color_under_review"]
    if coverage == COVERAGE_PIPELINE_INCOMPLETE:
        return DESIGN_TOKENS["color_pipeline_incomplete"]
    return DESIGN_TOKENS["color_text"]


def _th_style(*, nowrap: bool = False) -> dict:
    style: dict[str, Any] = {
        "textAlign": "left",
        "padding": "8px 10px",
        "borderBottom": (
            "1px solid " + DESIGN_TOKENS["color_border"]
        ),
        "color": DESIGN_TOKENS["color_muted"],
        "fontWeight": "normal",
        "fontSize": "11px",
        "textTransform": "uppercase",
        "letterSpacing": "0.05em",
    }
    if nowrap:
        # Phase 6F-7: prevent multi-word headers (e.g. "AS OF")
        # from wrapping on narrow viewports. The horizontal
        # scroll wrapper (``scoreboard-table-wrapper``) absorbs
        # any overflow this causes.
        style["whiteSpace"] = "nowrap"
    return style


def _td_style(
    *, color: Optional[str] = None,
    weight: Optional[str] = None,
    nowrap: bool = False,
) -> dict:
    style: dict[str, Any] = {
        "padding": "8px 10px",
        "borderBottom": (
            "1px solid " + DESIGN_TOKENS["color_border"]
        ),
        "fontSize": "13px",
        "color": color or DESIGN_TOKENS["color_text"],
    }
    if weight:
        style["fontWeight"] = weight
    if nowrap:
        # Phase 6F-7: prevent ISO dates ("2026-05-08") from
        # wrapping to broken fragments like ``202`` / ``05-``
        # on narrow viewports.
        style["whiteSpace"] = "nowrap"
    return style


def _tr_style(
    is_selected: bool, *, leader_eligible: bool = False,
) -> dict:
    """Scoreboard row style. Selected rows get a dim
    background; the *current leader* row additionally gets
    a left-border accent in the leader-highlight (neon)
    token so brightness consistently signals "this is
    today's pilot" everywhere it appears."""
    bg = (
        DESIGN_TOKENS["color_dim"] if is_selected
        else DESIGN_TOKENS["color_paper"]
    )
    style: dict[str, Any] = {
        "cursor": "pointer",
        "backgroundColor": bg,
    }
    if leader_eligible:
        # Phase 6G-4: a 3-px leader-highlight accent on the
        # row's left edge. The brightness is now exclusively
        # tied to "this row is today's full-pipeline pilot",
        # so a Bloomberg-trained eye still spots the active
        # row instantly without the rest of the page
        # carrying that color.
        style["borderLeft"] = (
            "3px solid " + DESIGN_TOKENS["color_leader_highlight"]
        )
    return style


def _render_coverage_pill(coverage: str) -> Any:
    """Phase 6G-4: render the scoreboard Coverage cell as a
    small wax-seal style pill (rounded filled badge with a
    leading dot) instead of a bare colored word. The
    underlying coverage string is unchanged; the row's
    ``data-coverage`` attribute is still the canonical
    ``Full`` / ``Partial`` / ``Stale`` / ``Under review`` /
    ``Pipeline incomplete``."""
    _, _, html = _dash_modules()
    accent = _coverage_color(coverage)
    return html.Span(
        children=[
            # Leading dot.
            html.Span(style={
                "display": "inline-block",
                "width": "8px",
                "height": "8px",
                "borderRadius": "50%",
                "backgroundColor": accent,
                "marginRight": "6px",
                "verticalAlign": "middle",
            }),
            html.Span(coverage, style={
                "color": accent,
                "verticalAlign": "middle",
            }),
        ],
        style={
            "display": "inline-flex",
            "alignItems": "center",
            "padding": "2px 8px",
            "borderRadius": "10px",
            "border": "1px solid " + DESIGN_TOKENS["color_border"],
            "backgroundColor": DESIGN_TOKENS["color_paper"],
            "fontSize": "12px",
        },
    )


def render_featured(
    ticker: Optional[str],
    *,
    payload: Optional[Mapping[str, Any]] = None,
    confluence_active: Optional[int] = None,
    confluence_total: Optional[int] = None,
) -> Any:
    """Render the Featured High Score panel. ``payload`` may be
    ``None`` (renders empty state). Confluence numbers come from the
    confluence artifact for the same ticker; ``None`` -> "unavailable".
    """
    _, dcc, html = _dash_modules()

    if not ticker:
        return _featured_empty_wrapper(
            BOARD_COPY["featured_empty_no_ticker"],
        )
    if payload is None or not payload.get("available"):
        return _featured_empty_wrapper(
            BOARD_COPY["featured_empty_no_data"], ticker=ticker,
        )

    signal = str(payload.get("current_signal") or "None") or "None"
    signal_value = SIGNAL_TO_VALUE.get(signal, 0)
    metric_basis = (
        BOARD_COPY["featured_label_total_capture"]
    )

    confluence_text = (
        BOARD_COPY["confluence_status_fmt"].format(
            active=confluence_active, total=confluence_total,
        )
        if confluence_active is not None and confluence_total is not None
        else BOARD_COPY["confluence_status_unavailable"]
    )

    fig = _build_signal_engine_figure(ticker, payload)
    chart_component: Any
    if fig is None:
        chart_component = html.Div(
            BOARD_COPY["featured_empty_chart"],
            style={
                "padding": "24px",
                "color": DESIGN_TOKENS["color_muted"],
                "border": (
                    "1px dashed " + DESIGN_TOKENS["color_border"]
                ),
                "borderRadius": "6px",
                "textAlign": "center",
            },
        )
    else:
        chart_component = dcc.Graph(
            figure=fig,
            config={"displayModeBar": False},
            style={"width": "100%"},
        )

    date_range = payload.get("date_range") or {}
    as_of = (
        str(date_range.get("end"))
        if isinstance(date_range, Mapping) and date_range.get("end")
        else BOARD_COPY["as_of_unavailable"]
    )

    return html.Div(children=[
        # Phase 6G-4: small muted "Today's pilot" prefix
        # so the panel reads as "Today's pilot - SPY"
        # rather than a Bloomberg ticker glyph in giant
        # neon green. ``featured-ticker-name`` retains its
        # stable id; the styling demotes the glyph from
        # neon to brand sage and shrinks the size.
        html.Div(
            BOARD_COPY["featured_pilot_prefix"],
            id="featured-pilot-prefix",
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "11px",
                "textTransform": "uppercase",
                "letterSpacing": "0.08em",
                "marginBottom": "2px",
            },
        ),
        html.H3(
            ticker,
            id="featured-ticker-name",
            style={
                "color": DESIGN_TOKENS["color_green"],
                "marginBottom": "4px",
                "fontSize": "16px",
                "letterSpacing": "0.02em",
            },
        ),
        html.Div([
            html.Span(
                BOARD_COPY["featured_label_current_signal"] + ": ",
                style={"color": DESIGN_TOKENS["color_muted"]},
            ),
            html.Span(
                signal,
                id="featured-current-signal",
                **{"data-signal-value": str(signal_value)},
                style={"color": _signal_color(signal)},
            ),
        ], style={"marginBottom": "4px"}),
        html.Div(
            confluence_text,
            id="featured-confluence-status",
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "12px",
                "marginBottom": "4px",
            },
        ),
        # Phase 6G-1: defuse the "scoreboard says No
        # consensus but Featured says Short" first-time
        # confusion in one line of plain text. Sourced from
        # BOARD_COPY so the copy-centralization test catches
        # any drift.
        html.Div(
            BOARD_COPY["two_signal_explainer"],
            id="featured-two-signal-explainer",
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "11px",
                "fontStyle": "italic",
                "marginBottom": "8px",
            },
        ),
        html.Div(
            chart_component,
            id="featured-signal-engine-chart",
            style={"marginBottom": "8px"},
        ),
        html.Div(
            BOARD_COPY["featured_chart_caption"],
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "11px",
                "marginBottom": "8px",
            },
        ),
        html.Div([
            _featured_metric(
                BOARD_COPY["featured_label_total_capture"],
                _fmt_metric(payload.get("total_capture_pct"), "pct2"),
                cell_id="featured-total-capture",
            ),
            _featured_metric(
                BOARD_COPY["featured_label_sharpe"],
                _fmt_metric(payload.get("sharpe_ratio"), "ratio2"),
                cell_id="featured-sharpe",
            ),
            _featured_metric(
                BOARD_COPY["featured_label_signal_days"],
                _fmt_metric(payload.get("signal_days"), "int"),
                cell_id="featured-signal-days",
            ),
            _featured_metric(
                BOARD_COPY["featured_label_as_of"],
                as_of,
                cell_id="featured-as-of-date",
            ),
        ], style={
            "display": "flex",
            "gap": "16px",
            "flexWrap": "wrap",
            "marginBottom": "8px",
        }),
        html.Div(
            BOARD_COPY["featured_disclaimer"],
            id="featured-disclaimer",
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "11px",
                "borderTop": (
                    "1px solid " + DESIGN_TOKENS["color_border"]
                ),
                "paddingTop": "6px",
            },
        ),
    ])


def _featured_empty_wrapper(message: str, *, ticker: str = "") -> Any:
    _, _, html = _dash_modules()
    return html.Div(children=[
        html.H3(
            ticker or "-",
            id="featured-ticker-name",
            style={
                "color": DESIGN_TOKENS["color_green"],
                "fontSize": "18px",
            },
        ),
        html.Span(
            "None",
            id="featured-current-signal",
            **{"data-signal-value": "0"},
            style={"color": DESIGN_TOKENS["color_muted"]},
        ),
        html.Div(
            BOARD_COPY["confluence_status_unavailable"],
            id="featured-confluence-status",
            style={"color": DESIGN_TOKENS["color_muted"]},
        ),
        html.Div(
            message,
            id="featured-signal-engine-chart",
            style={
                "padding": "24px",
                "color": DESIGN_TOKENS["color_muted"],
                "border": (
                    "1px dashed " + DESIGN_TOKENS["color_border"]
                ),
                "borderRadius": "6px",
                "textAlign": "center",
            },
        ),
        html.Div(
            "-", id="featured-total-capture",
            style={"color": DESIGN_TOKENS["color_muted"]},
        ),
        html.Div(
            "-", id="featured-sharpe",
            style={"color": DESIGN_TOKENS["color_muted"]},
        ),
        html.Div(
            "-", id="featured-signal-days",
            style={"color": DESIGN_TOKENS["color_muted"]},
        ),
        html.Div(
            BOARD_COPY["as_of_unavailable"],
            id="featured-as-of-date",
            style={"color": DESIGN_TOKENS["color_muted"]},
        ),
        html.Div(
            BOARD_COPY["featured_disclaimer"],
            id="featured-disclaimer",
            style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "11px",
                "borderTop": (
                    "1px solid " + DESIGN_TOKENS["color_border"]
                ),
                "paddingTop": "6px",
            },
        ),
    ])


def _featured_metric(label: str, value: str, *, cell_id: str) -> Any:
    _, _, html = _dash_modules()
    return html.Div(children=[
        html.Div(label, style={
            "color": DESIGN_TOKENS["color_muted"],
            "fontSize": "10px",
            "textTransform": "uppercase",
            "letterSpacing": "0.06em",
        }),
        html.Div(
            value, id=cell_id,
            style={
                "color": DESIGN_TOKENS["color_text"],
                "fontSize": "14px",
            },
        ),
    ], style={
        "minWidth": "120px",
        "padding": "6px 10px",
        "border": (
            "1px solid " + DESIGN_TOKENS["color_border"]
        ),
        "borderRadius": "4px",
    })


def render_evidence_trail(
    ticker: Optional[str],
    *,
    payload: Optional[Mapping[str, Any]] = None,
    impactsearch_ref: Optional[_ArtifactRef] = None,
    stackbuilder_ref: Optional[_ArtifactRef] = None,
    trafficflow_ref: Optional[_ArtifactRef] = None,
    confluence_ref: Optional[_ArtifactRef] = None,
    calendar_timeframes: Optional[Sequence[str]] = None,
    health_report: Optional[Mapping[str, Any]] = None,
    health_under_review: bool = False,
    now: Optional[datetime] = None,
) -> Any:
    """Render the seven Evidence Trail stations in fixed order.

    Missing source -> the canonical placeholder
    ``BOARD_COPY['station_missing']``.
    """
    _, _, html = _dash_modules()
    now = now or datetime.now(timezone.utc)

    stations: list[EvidenceStation] = []

    # 1. Seed Field <- primary_signal_engine payload
    if payload and payload.get("available"):
        date_range = payload.get("date_range") or {}
        end = (
            str(date_range.get("end"))
            if isinstance(date_range, Mapping)
            else None
        )
        signal = str(payload.get("current_signal") or "None")
        active_pair_raw = (
            payload.get("current_active_pair_raw")
            or payload.get("current_sma_pair_raw")
            or "-"
        )
        summary = BOARD_COPY[
            "station_summary_fmt_signal_engine"
        ].format(signal=signal, pair=active_pair_raw)
        presence = (
            PRESENCE_STALE if _is_stale_iso(end, now=now)
            else PRESENCE_PRESENT
        )
        stations.append(EvidenceStation(
            station_id=STATION_ID_SEED_FIELD,
            label=BOARD_COPY["station_label_seed_field"],
            presence=presence,
            as_of=end,
            summary=summary,
        ))
    else:
        stations.append(_missing_station(
            STATION_ID_SEED_FIELD,
            BOARD_COPY["station_label_seed_field"],
        ))

    # 2. Trading Post <- impactsearch research_day_v1
    stations.append(_station_from_ref(
        STATION_ID_TRADING_POST,
        BOARD_COPY["station_label_trading_post"],
        impactsearch_ref, now=now,
    ))
    # 3. Workshop <- stackbuilder
    stations.append(_station_from_ref(
        STATION_ID_WORKSHOP,
        BOARD_COPY["station_label_workshop"],
        stackbuilder_ref, now=now,
    ))
    # 4. Rail Yard <- trafficflow
    stations.append(_station_from_ref(
        STATION_ID_RAIL_YARD,
        BOARD_COPY["station_label_rail_yard"],
        trafficflow_ref, now=now,
    ))

    # 5. Calendar House <- saved non-daily libraries; confluence
    #    timeframes may supplement when no libraries exist.
    tfs = list(calendar_timeframes or [])
    if not tfs and confluence_ref is not None:
        # Fall back to confluence timeframe coverage. Mark presence
        # as PRESENT only when at least two timeframes are recorded
        # in the confluence artifact.
        art = confluence_ref.artifact
        confluence_tfs = list(getattr(art, "timeframes", None) or [])
        # Exclude "1d" because Calendar House is non-daily evidence.
        confluence_tfs = [tf for tf in confluence_tfs if tf != "1d"]
        if confluence_tfs:
            tfs = confluence_tfs
    if tfs:
        summary = BOARD_COPY[
            "station_summary_fmt_calendar_house"
        ].format(timeframes=", ".join(sorted(set(tfs))))
        as_of = (
            confluence_ref.last_date
            if confluence_ref is not None else None
        )
        presence = (
            PRESENCE_STALE if _is_stale_iso(as_of, now=now)
            else PRESENCE_PRESENT
        )
        stations.append(EvidenceStation(
            station_id=STATION_ID_CALENDAR_HOUSE,
            label=BOARD_COPY["station_label_calendar_house"],
            presence=presence,
            as_of=as_of,
            summary=summary,
        ))
    else:
        stations.append(_missing_station(
            STATION_ID_CALENDAR_HOUSE,
            BOARD_COPY["station_label_calendar_house"],
        ))

    # 6. Town Hall <- confluence
    stations.append(_station_from_ref(
        STATION_ID_TOWN_HALL,
        BOARD_COPY["station_label_town_hall"],
        confluence_ref, now=now,
    ))

    # 7. Watchtower <- catalogue health report
    if health_report:
        schema = _health_schema_version(health_report) or "-"
        summary = BOARD_COPY[
            "station_summary_fmt_watchtower"
        ].format(schema=schema)
        presence = (
            PRESENCE_UNDER_REVIEW if health_under_review
            else PRESENCE_PRESENT
        )
        stations.append(EvidenceStation(
            station_id=STATION_ID_WATCHTOWER,
            label=BOARD_COPY["station_label_watchtower"],
            presence=presence,
            as_of=None,
            summary=summary,
        ))
    else:
        stations.append(_missing_station(
            STATION_ID_WATCHTOWER,
            BOARD_COPY["station_label_watchtower"],
        ))

    station_children = [_render_station(s) for s in stations]
    # Phase 6G-1: prefix the seven station cards with a
    # short intro that frames how to read upstream-station
    # state. "Stale" rows are historical reference for the
    # current Confluence-gated leader board; they don't
    # block the leader gate unless explicitly flagged.
    return html.Div(
        children=[
            html.Div(
                BOARD_COPY["evidence_trail_intro"],
                id="evidence-trail-intro",
                style={
                    "color": DESIGN_TOKENS["color_muted"],
                    "fontSize": "12px",
                    "marginBottom": "8px",
                },
            ),
            html.Div(
                children=station_children,
                style={
                    "display": "flex",
                    "flexDirection": "column",
                    "gap": "8px",
                },
            ),
        ],
    )


def _station_from_ref(
    station_id: str, label: str,
    ref: Optional[_ArtifactRef], *,
    now: datetime,
) -> EvidenceStation:
    if ref is None or ref.artifact is None:
        return _missing_station(station_id, label)
    art = ref.artifact
    summary_block = getattr(art, "summary", None) or {}
    cum = (
        summary_block.get("total_capture_pct")
        if isinstance(summary_block, Mapping) else None
    )
    cum_text = (
        f"{float(cum):.2f}" if cum is not None
        else BOARD_COPY["as_of_unavailable"]
    )
    summary = BOARD_COPY["station_summary_fmt_artifact"].format(
        date=ref.last_date or BOARD_COPY["as_of_unavailable"],
        cum_pct=cum_text,
    )
    presence = (
        PRESENCE_STALE if _is_stale_iso(ref.last_date, now=now)
        else PRESENCE_PRESENT
    )
    return EvidenceStation(
        station_id=station_id,
        label=label,
        presence=presence,
        as_of=ref.last_date,
        summary=summary,
    )


def _missing_station(station_id: str, label: str) -> EvidenceStation:
    return EvidenceStation(
        station_id=station_id,
        label=label,
        presence=PRESENCE_MISSING,
        as_of=None,
        summary=BOARD_COPY["station_missing"],
    )


_STATION_GLYPH: dict[str, str] = {
    # Phase 6G-4: two-letter notice-board "stamp" prefixes
    # for each Evidence Trail station. Pure text, no image
    # assets, no emoji (emoji rendering is unpredictable
    # across the user-agent matrix).
    STATION_ID_SEED_FIELD: "SF",
    STATION_ID_TRADING_POST: "TP",
    STATION_ID_WORKSHOP: "WK",
    STATION_ID_RAIL_YARD: "RY",
    STATION_ID_CALENDAR_HOUSE: "CH",
    STATION_ID_TOWN_HALL: "TH",
    STATION_ID_WATCHTOWER: "WT",
}


def _render_station(s: EvidenceStation) -> Any:
    _, _, html = _dash_modules()
    presence_label = {
        PRESENCE_PRESENT: BOARD_COPY["station_presence_present"],
        PRESENCE_MISSING: BOARD_COPY["station_presence_missing"],
        PRESENCE_STALE: BOARD_COPY["station_presence_stale"],
        PRESENCE_UNDER_REVIEW: BOARD_COPY[
            "station_presence_under_review"
        ],
    }.get(s.presence, s.presence)
    presence_color = {
        PRESENCE_PRESENT: DESIGN_TOKENS["color_buy"],
        PRESENCE_MISSING: DESIGN_TOKENS["color_muted"],
        PRESENCE_STALE: DESIGN_TOKENS["color_stale"],
        PRESENCE_UNDER_REVIEW: DESIGN_TOKENS["color_under_review"],
    }.get(s.presence, DESIGN_TOKENS["color_text"])
    glyph = _STATION_GLYPH.get(s.station_id, "")
    return html.Div(
        id=s.station_id,
        **{
            "data-presence": s.presence,
            "data-as-of": s.as_of or "",
        },
        style={
            "padding": "8px 10px",
            "border": (
                "1px solid " + DESIGN_TOKENS["color_border"]
            ),
            "borderRadius": "6px",
            "backgroundColor": DESIGN_TOKENS["color_paper"],
        },
        children=[
            html.Div(
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "8px",
                },
                children=[
                    # Phase 6G-4: leading two-letter station
                    # stamp ("SF", "TP", ...). Color-tinted
                    # by the station's presence so a quick
                    # scan still telegraphs current / stale /
                    # missing without reading labels.
                    html.Span(
                        glyph,
                        **{
                            "data-station-glyph": glyph,
                            "aria-hidden": "true",
                        },
                        style={
                            "display": "inline-block",
                            "minWidth": "24px",
                            "padding": "2px 6px",
                            "borderRadius": "6px",
                            "backgroundColor": DESIGN_TOKENS[
                                "color_dim"
                            ],
                            "color": presence_color,
                            "fontFamily": "monospace",
                            "fontSize": "11px",
                            "fontWeight": "bold",
                            "textAlign": "center",
                            "letterSpacing": "0.05em",
                        },
                    ),
                    html.Span(s.label, style={
                        "color": DESIGN_TOKENS["color_text"],
                        "fontWeight": "bold",
                    }),
                    html.Span(presence_label, style={
                        "color": presence_color,
                        "fontSize": "11px",
                        "textTransform": "uppercase",
                        "letterSpacing": "0.06em",
                    }),
                ],
            ),
            html.Div(s.summary or BOARD_COPY["station_missing"], style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "12px",
                "marginTop": "2px",
            }),
        ],
    )


# ---------------------------------------------------------------------------
# Dash app
# ---------------------------------------------------------------------------


def _build_initial_state(
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
    use_cache: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build the per-render state bundle reused by the initial
    layout build and every callback fire.

    Heavy work (discovery + artifact-tree walk) is memoized via
    ``_BOARD_CACHE`` keyed on the directory triple, so re-entry
    from a callback is a dict lookup. Per-state ``payload_cache``
    isolates one PKL hydration per selected ticker per render
    bundle - featured + evidence panels share the same payload."""
    cache_d = Path(cache_dir) if cache_dir else _default_cache_dir()
    artifact_d = (
        Path(artifact_root) if artifact_root else _default_artifact_root()
    )
    sig_d = (
        Path(sig_lib_dir) if sig_lib_dir else _default_sig_lib_dir()
    )
    health_report = _read_health_report(artifact_d)
    rows = discover_board_catalogue(
        cache_dir=cache_d,
        artifact_root=artifact_d,
        sig_lib_dir=sig_d,
        health_report=health_report,
        use_cache=use_cache,
        now=now,
    )
    ranked = rank_board_rows(rows)
    selected = default_selected_ticker(ranked)
    return {
        "rows": ranked,
        "selected": selected,
        "cache_dir": cache_d,
        "artifact_root": artifact_d,
        "sig_lib_dir": sig_d,
        "health_report": health_report,
        # Reuses the cached artifact index discovery built; falls
        # back to a fresh walk only when discovery did not run
        # (e.g. cache_dir missing).
        "artifact_index": _cached_artifact_index(
            cache_d, artifact_d, sig_d,
        ),
        "now": now or datetime.now(timezone.utc),
        # Per-render payload cache. Featured and Evidence both call
        # _ticker_payload for the same selected ticker; this shares
        # the hydration so it costs one PKL load, not two.
        "payload_cache": {},
    }


def _ticker_payload(
    ticker: str,
    state: Optional[Mapping[str, Any]] = None,
    *,
    cache_dir: Optional[Path] = None,
) -> Optional[Mapping[str, Any]]:
    """Hydrate a single ticker's Signal Engine payload, with a
    per-state cache so the same ticker is loaded at most once per
    render bundle. Pass ``state`` (preferred) when calling from
    inside a callback; ``cache_dir`` is the standalone fallback for
    tests / one-off lookups.
    """
    if not ticker:
        return None
    if state is not None:
        cache = state.get("payload_cache")
        if isinstance(cache, dict) and ticker in cache:
            return cache[ticker]
        cache_d = state.get("cache_dir")
    else:
        cache_d = cache_dir
    try:
        payload = _pse.load_primary_signal_engine_payload(
            ticker, cache_dir=cache_d,
        )
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        payload = None
    if state is not None:
        cache = state.setdefault("payload_cache", {})
        cache[ticker] = payload
    return payload


def _render_featured_for(
    ticker: str, state: Mapping[str, Any],
) -> Any:
    payload = _ticker_payload(ticker, state)
    confluence_ref = state["artifact_index"].get(
        (ENGINE_CONFLUENCE, ticker.strip().upper()),
    )
    active, total = _confluence_active_total(confluence_ref)
    return render_featured(
        ticker,
        payload=payload,
        confluence_active=active,
        confluence_total=total,
    )


def _render_evidence_for(
    ticker: str, state: Mapping[str, Any],
) -> Any:
    norm = ticker.strip().upper() if ticker else ""
    payload = _ticker_payload(ticker, state) if ticker else None
    artifact_index = state["artifact_index"]
    impactsearch_ref = artifact_index.get((ENGINE_IMPACTSEARCH, norm))
    stackbuilder_ref = artifact_index.get((ENGINE_STACKBUILDER, norm))
    trafficflow_ref = artifact_index.get((ENGINE_TRAFFICFLOW, norm))
    confluence_ref = artifact_index.get((ENGINE_CONFLUENCE, norm))
    timeframes = _calendar_house_timeframes_present(
        ticker, state["sig_lib_dir"],
    ) if ticker else []
    blocked = _health_blocked_targets(state["health_report"])
    return render_evidence_trail(
        ticker,
        payload=payload,
        impactsearch_ref=impactsearch_ref,
        stackbuilder_ref=stackbuilder_ref,
        trafficflow_ref=trafficflow_ref,
        confluence_ref=confluence_ref,
        calendar_timeframes=timeframes,
        health_report=state["health_report"],
        health_under_review=(norm in blocked),
        now=state["now"],
    )


def build_app(
    *,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    sig_lib_dir: Optional[Path] = None,
) -> Any:
    """Construct the Dash app.

    Cold-boot work:

      * Scoreboard rows are built from cache filenames + saved
        research_day_v1 artifacts. The Spymaster cache PKL is NOT
        hydrated during scoreboard construction.
      * Confluence agreement / row signal / coverage all come from
        the artifact tree (see ``discover_board_catalogue`` data
        contract).
      * The Featured + Evidence panels DO hydrate exactly one
        ticker - the default selected ticker - so the first paint
        carries real Signal Engine numbers. That single PKL load is
        the only per-build call into
        ``primary_signal_engine.load_primary_signal_engine_payload``.

    Callbacks rebuild the state bundle on each fire, but the heavy
    discovery + artifact walk are memoized by ``discover_board_catalogue``
    on the directory triple, so callback re-entry is cheap.

    Selection is wired through a SINGLE multi-output callback
    (Featured + Evidence outputs share one ``_build_initial_state``
    bundle per click), and the per-render ``payload_cache`` on that
    bundle guarantees exactly one PKL load per selection - even
    though two panels read the same payload. Splitting the two
    panels across separate callbacks would double the read.
    """
    dash, dcc, html = _dash_modules()
    from dash import Input, Output, State
    from dash.dependencies import ALL
    from dash import ctx

    state = _build_initial_state(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    rows = state["rows"]
    selected = state["selected"]

    app = dash.Dash(__name__, title=BOARD_COPY["page_title"])

    eligible_count = sum(1 for r in rows if r.leader_eligible)
    no_leaders_banner: Any
    if rows and eligible_count == 0:
        no_leaders_banner = html.Div(
            BOARD_COPY["no_current_leaders"],
            id="scoreboard-no-current-leaders",
            **{"data-leader-count": "0"},
            style={
                "padding": "8px 12px",
                "marginBottom": "10px",
                "border": (
                    "1px dashed "
                    + DESIGN_TOKENS["color_under_review"]
                ),
                "borderRadius": "4px",
                "color": DESIGN_TOKENS["color_text"],
                "fontSize": "12px",
            },
        )
    else:
        no_leaders_banner = html.Div(
            id="scoreboard-no-current-leaders",
            **{"data-leader-count": str(eligible_count)},
            style={"display": "none"},
        )

    # Phase 6G-1: partition the discovered rows into the
    # current-leader board (default visible) and the
    # saved-research archive (collapsed by default). The
    # archive still contains the long alphabetical tail of
    # bare-cache + partial-coverage tickers - it's just
    # tucked under a <details> so the MVP doesn't appear
    # empty when most rows are "Unavailable / Partial / -".
    current_rows, archive_rows = _partition_rows_for_board(rows)

    # Phase 6G-1: pull the Signal Engine state for the top
    # leader-eligible row so the current-pilot card can show
    # both the board consensus AND the standalone Signal
    # Engine readout. Reuses the per-render payload cache so
    # this is at most one extra PKL load when the leader
    # differs from ``selected``.
    pilot_pair: Optional[str] = None
    pilot_se_as_of: Optional[str] = None
    if current_rows:
        leader = current_rows[0]
        leader_payload = _ticker_payload(leader.ticker, state)
        if isinstance(leader_payload, Mapping) and leader_payload.get(
            "available"
        ):
            raw = leader_payload.get("current_active_pair_raw")
            if raw:
                pilot_pair = str(raw)
            dr = leader_payload.get("date_range") or {}
            if isinstance(dr, Mapping) and dr.get("end"):
                pilot_se_as_of = str(dr.get("end"))

    section_current_pilot = html.Section(
        id="section-current-pilot",
        children=[
            html.H2(
                BOARD_COPY["section_current_pilot_title"],
                style=_section_heading_style(),
            ),
            render_current_pilot_card(
                current_rows,
                signal_engine_pair=pilot_pair,
                signal_engine_as_of=pilot_se_as_of,
            ),
        ],
        style=_section_style(),
    )

    section_scoreboard = html.Section(
        id="section-scoreboard",
        **{
            # Phase 6C-8: the public board's ranking method is now
            # gated on Confluence-current leaders only.
            "data-ranking-method": (
                "current_confluence_leaders_only_then_"
                "agreement_desc_then_ticker_asc"
            ),
        },
        children=[
            html.H2(
                BOARD_COPY["section_scoreboard_title"],
                style=_section_heading_style(),
            ),
            no_leaders_banner,
            html.Div(
                id="scoreboard-container",
                # Phase 6G-1: the default-visible scoreboard
                # carries ONLY leader-eligible rows. The
                # saved-research archive (Partial / Stale /
                # Under review / Pipeline incomplete) lives
                # in section-archive below, collapsed by
                # default.
                children=render_scoreboard(
                    current_rows, selected_ticker=selected,
                ),
            ),
        ],
        style=_section_style(),
    )

    section_archive = html.Section(
        id="section-archive",
        **{"data-archive-row-count": str(len(archive_rows))},
        children=[
            html.H2(
                BOARD_COPY["section_archive_title"],
                style=_section_heading_style(),
            ),
            html.Div(
                id="section-archive-container",
                children=render_archive_scoreboard(
                    archive_rows, selected_ticker=selected,
                ),
            ),
        ],
        style=_section_style(),
    )

    section_featured = html.Section(
        id="section-featured",
        children=[
            html.H2(
                BOARD_COPY["section_featured_title"],
                style=_section_heading_style(),
            ),
            html.Div(
                id="section-featured-body",
                children=_render_featured_for(selected, state),
            ),
        ],
        style=_section_style(),
    )

    section_evidence = html.Section(
        id="section-evidence-trail",
        children=[
            html.H2(
                BOARD_COPY["section_evidence_trail_title"],
                style=_section_heading_style(),
            ),
            html.Div(
                id="section-evidence-trail-body",
                children=_render_evidence_for(selected, state),
            ),
        ],
        style=_section_style(),
    )

    section_what_is = html.Section(
        id="section-what-prjct9-is",
        children=[
            html.H2(
                BOARD_COPY["section_what_prjct9_is_title"],
                style=_section_heading_style(),
            ),
            html.P(BOARD_COPY["what_prjct9_is"], style={
                "color": DESIGN_TOKENS["color_text"],
                "fontSize": "13px",
                "lineHeight": "1.5",
            }),
        ],
        style=_section_style(),
    )

    section_what_is_not = html.Section(
        id="section-what-it-is-not",
        children=[
            html.H2(
                BOARD_COPY["section_what_it_is_not_title"],
                style=_section_heading_style(),
            ),
            html.Ul([
                html.Li(line, style={
                    "color": DESIGN_TOKENS["color_text"],
                    "fontSize": "13px",
                    "marginBottom": "4px",
                })
                for line in BOARD_COPY["what_it_is_not_bullets"]
            ]),
        ],
        style=_section_style(),
    )

    app.layout = html.Div(
        style={
            # Phase 6G-4: warm-dark page surface (was pure
            # #000). ``color_black`` is aliased to the same
            # warm dark for backwards compatibility.
            "backgroundColor": DESIGN_TOKENS["color_warm_dark"],
            "color": DESIGN_TOKENS["color_text"],
            "minHeight": "100vh",
            "padding": "24px",
            # Keep monospace as the brand font family for
            # the wordmark + raw data values; the cozy /
            # humanist body type lands in a later sprint.
            "fontFamily": "monospace, sans-serif",
        },
        children=[
            html.H1(
                BOARD_COPY["page_title"],
                style={
                    # Brand sage now (was the neon
                    # green); the legacy neon stays
                    # exclusively on the leader-highlight
                    # accents.
                    "color": DESIGN_TOKENS["color_green"],
                    "fontSize": "22px",
                    "marginBottom": "4px",
                },
            ),
            html.Div(BOARD_COPY["page_subtitle"], style={
                "color": DESIGN_TOKENS["color_muted"],
                "fontSize": "12px",
                "marginBottom": "16px",
            }),
            dcc.Store(id="selected-ticker-store", data=selected),
            section_current_pilot,
            section_scoreboard,
            section_archive,
            section_featured,
            section_evidence,
            section_what_is,
            section_what_is_not,
        ],
    )

    @app.callback(
        Output("selected-ticker-store", "data"),
        Input({"type": "scoreboard-row", "ticker": ALL}, "n_clicks"),
        State("selected-ticker-store", "data"),
        prevent_initial_call=True,
    )
    def _on_row_click(_clicks, current):
        triggered = getattr(ctx, "triggered_id", None)
        if not isinstance(triggered, dict):
            return current
        ticker = triggered.get("ticker")
        if not ticker:
            return current
        return ticker

    # One multi-output callback so Featured + Evidence render from
    # a SINGLE state bundle. The per-render payload_cache on that
    # bundle guarantees exactly one
    # primary_signal_engine.load_primary_signal_engine_payload call
    # per selection - if the two outputs were split across two
    # callbacks, Dash would fire both on the same store change and
    # each would hydrate independently, doubling the PKL read.
    @app.callback(
        Output("section-featured-body", "children"),
        Output("section-evidence-trail-body", "children"),
        Input("selected-ticker-store", "data"),
    )
    def _on_select_render_panels(ticker):
        live_state = _build_initial_state(
            cache_dir=cache_dir,
            artifact_root=artifact_root,
            sig_lib_dir=sig_lib_dir,
            use_cache=True,
        )
        normalized = ticker or ""
        featured = _render_featured_for(normalized, live_state)
        evidence = _render_evidence_for(normalized, live_state)
        return featured, evidence

    return app


def _section_style() -> dict:
    # Phase 6G-4: section cards sit on the warm-paper
    # surface with rounded corners and a subtle inset
    # highlight so they read as papers pinned to the
    # notice board. ``color_black`` is now aliased to
    # ``color_warm_dark`` so legacy callers still resolve
    # to a coherent surface.
    return {
        "border": "1px solid " + DESIGN_TOKENS["color_border"],
        "borderRadius": "8px",
        "padding": "14px",
        "marginBottom": "16px",
        "backgroundColor": DESIGN_TOKENS["color_paper"],
        "boxShadow": (
            "inset 0 1px 0 0 " + DESIGN_TOKENS["color_dim"]
        ),
    }


def _section_heading_style() -> dict:
    return {
        "color": DESIGN_TOKENS["color_green"],
        "fontSize": "14px",
        "textTransform": "uppercase",
        "letterSpacing": "0.08em",
        "marginBottom": "10px",
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(port: Optional[int] = None) -> None:
    """Run the board on ``127.0.0.1:<port>``. Port resolution:
    explicit arg > PRJCT9_BOARD_PORT > DEFAULT_PORT (8061)."""
    if port is None:
        env_port = os.environ.get("PRJCT9_BOARD_PORT")
        port = int(env_port) if env_port else DEFAULT_PORT
    app = build_app()
    print(
        f"PRJCT9 Daily Signal Board\n"
        f"  url:   http://{DEFAULT_HOST}:{port}\n"
        f"  ctrl-c to stop",
    )
    app.run(host=DEFAULT_HOST, port=port, debug=False)


if __name__ == "__main__":
    main()
