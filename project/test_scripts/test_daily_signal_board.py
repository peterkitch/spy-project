"""Phase 6C-7 tests for daily_signal_board.

Pin the public contract, not pixel choices:

  - discovery filters to available cache payloads only
  - coverage status priority order is enforced
  - ranking is confluence-agreement desc -> ticker asc
  - SPY default; first alphabetical otherwise; empty when no rows
  - row click updates both featured and evidence trail
  - seven evidence-trail stations render in the documented order
  - missing stations use the documented placeholder copy
  - BOARD_COPY owns visible strings, DESIGN_TOKENS owns colors
  - the module never imports live-engine / yfinance code
  - disclaimer string is exact
  - empty-cache boot renders all five sections
  - the module makes no disk-write calls
  - build_app() returns a Dash app with all five section IDs
"""
from __future__ import annotations

import ast
import json
import os
import pickle
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

# Make sure the bare-name imports inside daily_signal_board resolve
# the same way they do when the module is run as ``python
# daily_signal_board.py`` from ``project/``. This mirrors the path
# bootstrap used by the existing preview test suite.
_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import daily_signal_board as board  # noqa: E402
import primary_signal_engine as pse  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------


def _write_min_spymaster_cache(
    cache_dir: Path, ticker: str, *,
    last_date: str = "2026-05-04",
    final_signal: str = "Buy 3,2",
) -> Path:
    """Write the minimal Spymaster-cache PKL shape that
    ``primary_signal_engine.load_primary_signal_engine_payload``
    accepts. The shape uses ``preprocessed_data`` + ``active_pairs``
    aligned to the price index. ``final_signal`` lets callers vary
    the *current* (last-row) active pair so two ticker fixtures
    produce distinguishable payloads."""
    import pandas as pd

    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range(end=last_date, periods=10, freq="D")
    df = pd.DataFrame(
        {"Close": [100.0 + i for i in range(10)]},
        index=dates,
    )
    active_pairs = [
        "Buy 3,2", "Buy 3,2", "Buy 3,2", "Buy 3,2", "Buy 3,2",
        "Short 5,1", "Short 5,1", "Short 5,1", "Short 5,1",
        final_signal,
    ]
    payload = {
        "preprocessed_data": df,
        "active_pairs": active_pairs,
    }
    safe = ticker.replace("^", "_")
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_research_day_artifact(
    artifact_root: Path,
    *,
    engine: str,
    target: str,
    last_date: str,
    timeframes: Optional[list[str]] = None,
    daily_extra: Optional[dict[str, Any]] = None,
) -> Path:
    """Create a saved ``*.research_day.json`` under
    ``output/research_artifacts/<engine>/<TARGET>/``. Uses
    ``research_artifacts.write_research_day_artifact`` so the on-disk
    schema stays in lockstep with the producer."""
    engine_dir = artifact_root / engine / target.replace("^", "_")
    engine_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "total_capture_pct": 42.5,
        "sharpe_ratio": 0.07,
        "trigger_days": 5,
    }
    daily = [
        {
            "date": last_date,
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 12.34,
            "is_trigger_day": True,
        },
    ]
    if daily_extra:
        daily[-1].update(daily_extra)
    artifact = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine=engine,
        target_ticker=target,
        signal_source="" if engine != "impactsearch" else "SPY",
        run_id="test",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-10T00:00:00+00:00",
        summary=summary,
        daily=daily,
        timeframes=list(timeframes or []),
    )
    out_path = engine_dir / f"{target.replace('^', '_')}.research_day.json"
    return ra.write_research_day_artifact(artifact, out_path)


def _empty_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    sig_lib_dir = tmp_path / "siglib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    sig_lib_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir, artifact_root, sig_lib_dir


@pytest.fixture(autouse=True)
def _reset_board_cache_each_test():
    board.reset_board_cache()
    yield
    board.reset_board_cache()


# ---------------------------------------------------------------------------
# 1. Discovery
# ---------------------------------------------------------------------------


def test_catalogue_discovery_returns_only_cached_tickers(tmp_path: Path):
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    _write_min_spymaster_cache(cache_dir, "ACME")
    # Malformed cache file: should be excluded.
    bad = cache_dir / "BAD_precomputed_results.pkl"
    bad.write_bytes(b"not a pickle")
    # Filename pattern mismatch: should be skipped silently.
    (cache_dir / "ignore_me.txt").write_text("noop")

    rows = board.discover_board_catalogue(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
        use_cache=False,
    )
    tickers = {r.ticker for r in rows}
    assert "SPY" in tickers
    assert "ACME" in tickers
    assert "BAD" not in tickers
    # And the payload semantics flow through.
    spy = next(r for r in rows if r.ticker == "SPY")
    assert spy.signal in {"Buy", "Short", "None"}
    assert spy.signal_value in {-1, 0, 1}


# ---------------------------------------------------------------------------
# 2. Coverage status priority
# ---------------------------------------------------------------------------


def _make_ref(last_date: str, artifact: Any = None) -> Any:
    """Tiny stand-in for board._ArtifactRef. Coverage code only
    reads ``.last_date`` / ``.artifact`` / ``.path`` / ``.mtime`` so
    a SimpleNamespace works without coupling to the dataclass."""
    return SimpleNamespace(
        path=Path("/tmp/fake.json"),
        artifact=artifact,
        last_date=last_date,
        mtime=0.0,
    )


def test_coverage_status_full_partial_stale_under_review():
    fresh = "2026-05-09"
    stale = (datetime(2026, 5, 9, tzinfo=timezone.utc)
             - timedelta(days=400)).strftime("%Y-%m-%d")
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)
    base_payload = {
        "available": True,
        "date_range": {"start": "2020-01-01", "end": fresh},
    }
    fresh_ref = _make_ref(fresh, artifact=SimpleNamespace(
        timeframes=["1d", "1wk", "1mo"], daily=[
            {"active_count": 3, "available_count": 3},
        ],
    ))

    # 1. Under-review beats everything when health flags the ticker,
    #    even with full + fresh evidence.
    coverage = board.coverage_status_for_ticker(
        "SPY",
        payload=base_payload,
        impactsearch_ref=fresh_ref,
        stackbuilder_ref=fresh_ref,
        trafficflow_ref=fresh_ref,
        confluence_ref=fresh_ref,
        calendar_timeframes=["1wk", "1mo"],
        health_blocked=["SPY"],
        now=now,
    )
    assert coverage == board.COVERAGE_UNDER_REVIEW

    # 2. Stale beats Full when the newest evidence date is older than
    #    STALE_DAYS, regardless of artifact completeness.
    stale_payload = {
        "available": True,
        "date_range": {"start": "2018-01-01", "end": stale},
    }
    stale_ref = _make_ref(stale, artifact=SimpleNamespace(
        timeframes=["1d", "1wk", "1mo"], daily=[
            {"active_count": 2, "available_count": 3},
        ],
    ))
    coverage = board.coverage_status_for_ticker(
        "SPY",
        payload=stale_payload,
        impactsearch_ref=stale_ref,
        stackbuilder_ref=stale_ref,
        trafficflow_ref=stale_ref,
        confluence_ref=stale_ref,
        calendar_timeframes=["1wk", "1mo"],
        health_blocked=[],
        now=now,
    )
    assert coverage == board.COVERAGE_STALE

    # 3. Full: fresh evidence in every engine + 2+ Calendar timeframes.
    coverage = board.coverage_status_for_ticker(
        "SPY",
        payload=base_payload,
        impactsearch_ref=fresh_ref,
        stackbuilder_ref=fresh_ref,
        trafficflow_ref=fresh_ref,
        confluence_ref=fresh_ref,
        calendar_timeframes=["1wk", "1mo"],
        health_blocked=[],
        now=now,
    )
    assert coverage == board.COVERAGE_FULL

    # 4. Partial: only the engine cache is present.
    coverage = board.coverage_status_for_ticker(
        "SPY",
        payload=base_payload,
        impactsearch_ref=None,
        stackbuilder_ref=None,
        trafficflow_ref=None,
        confluence_ref=None,
        calendar_timeframes=[],
        health_blocked=[],
        now=now,
    )
    assert coverage == board.COVERAGE_PARTIAL

    # 5. Priority order is documented + canonical.
    assert board.COVERAGE_PRIORITY == (
        board.COVERAGE_UNDER_REVIEW,
        board.COVERAGE_STALE,
        board.COVERAGE_FULL,
        board.COVERAGE_PARTIAL,
    )


# ---------------------------------------------------------------------------
# 3. Ranking
# ---------------------------------------------------------------------------


def test_ranking_sorts_by_confluence_then_alphabetical():
    rows = [
        board.BoardRow(
            ticker="BBB", signal="Buy", signal_value=1,
            agreement_active=3, agreement_total=5,
            coverage=board.COVERAGE_PARTIAL, as_of="2026-05-09",
        ),
        board.BoardRow(
            ticker="AAA", signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of="2026-05-09",
        ),
        board.BoardRow(
            ticker="CCC", signal="Short", signal_value=-1,
            agreement_active=3, agreement_total=5,
            coverage=board.COVERAGE_PARTIAL, as_of="2026-05-09",
        ),
        board.BoardRow(
            ticker="DDD", signal="Buy", signal_value=1,
            agreement_active=5, agreement_total=5,
            coverage=board.COVERAGE_FULL, as_of="2026-05-09",
        ),
    ]
    ranked = board.rank_board_rows(rows)
    order = [r.ticker for r in ranked]
    # Active counts: DDD=5, BBB=3, CCC=3, AAA=None (-1).
    # Descending: 5, then 3-tie alphabet (BBB < CCC), then None last.
    assert order == ["DDD", "BBB", "CCC", "AAA"]
    # Top 3 carry rank labels; AAA does not.
    ranks = {r.ticker: r.rank for r in ranked}
    assert ranks == {"DDD": 1, "BBB": 2, "CCC": 3, "AAA": None}


# ---------------------------------------------------------------------------
# 4. Default selected ticker
# ---------------------------------------------------------------------------


def test_default_selected_ticker_is_spy():
    def _row(t):
        return board.BoardRow(
            ticker=t, signal="None", signal_value=0,
            agreement_active=None, agreement_total=None,
            coverage=board.COVERAGE_PARTIAL, as_of=None,
        )
    assert board.default_selected_ticker(
        [_row("SPY"), _row("AAA")],
    ) == "SPY"
    assert board.default_selected_ticker(
        [_row("BBB"), _row("AAA")],
    ) == "AAA"
    assert board.default_selected_ticker([]) == ""


# ---------------------------------------------------------------------------
# 5. Row click updates featured + evidence trail
# ---------------------------------------------------------------------------


def test_clicking_row_updates_featured_and_evidence_trail(
    monkeypatch, tmp_path: Path,
):
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(
        cache_dir, "SPY", final_signal="Buy 3,2",
    )
    _write_min_spymaster_cache(
        cache_dir, "ACME", final_signal="Short 5,1",
    )

    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    feat_key = "section-featured-body.children"
    evid_key = "section-evidence-trail-body.children"
    sel_key = "selected-ticker-store.data"

    for key in (feat_key, evid_key, sel_key):
        assert key in app.callback_map, (
            f"expected {key} in app.callback_map; got "
            f"{list(app.callback_map)[:8]}"
        )

    # Peel off Dash's add_context wrapper so the callback body can be
    # invoked directly; mirrors the existing preview suite's pattern.
    feat_cb = app.callback_map[feat_key]["callback"]
    evid_cb = app.callback_map[evid_key]["callback"]
    feat_inner = getattr(feat_cb, "__wrapped__", feat_cb)
    evid_inner = getattr(evid_cb, "__wrapped__", evid_cb)

    feat_acme = feat_inner("ACME")
    evid_acme = evid_inner("ACME")
    feat_spy = feat_inner("SPY")
    evid_spy = evid_inner("SPY")

    # The featured / evidence renders depend on the selected ticker -
    # the rendered tree must mention the new ticker name when the
    # selection changes.
    assert _component_contains_id(feat_acme, "featured-ticker-name")
    assert _component_contains_id(feat_spy, "featured-ticker-name")
    feat_acme_text = _component_text(feat_acme)
    feat_spy_text = _component_text(feat_spy)
    assert "ACME" in feat_acme_text
    assert "SPY" in feat_spy_text
    assert feat_acme_text != feat_spy_text

    evid_text_acme = _component_text(evid_acme)
    evid_text_spy = _component_text(evid_spy)
    # Both renders must include all seven station IDs.
    for sid in board.STATION_IDS:
        assert _component_contains_id(evid_acme, sid)
        assert _component_contains_id(evid_spy, sid)
    # And the rendered text must change when the selection changes
    # (the seed-field summary embeds the ticker payload).
    assert evid_text_acme != evid_text_spy


# ---------------------------------------------------------------------------
# 6. Seven stations in fixed order
# ---------------------------------------------------------------------------


def test_evidence_trail_renders_seven_stations_in_fixed_order(tmp_path: Path):
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    _write_min_spymaster_cache(cache_dir, "SPY")
    payload = pse.load_primary_signal_engine_payload(
        "SPY", cache_dir=cache_dir,
    )
    component = board.render_evidence_trail(
        "SPY",
        payload=payload,
        impactsearch_ref=None,
        stackbuilder_ref=None,
        trafficflow_ref=None,
        confluence_ref=None,
        calendar_timeframes=[],
        health_report=None,
    )
    found = _ordered_station_ids(component)
    assert found == list(board.STATION_IDS), (
        f"stations rendered in {found}, expected {board.STATION_IDS}"
    )


# ---------------------------------------------------------------------------
# 7. Missing station placeholder
# ---------------------------------------------------------------------------


def test_missing_station_renders_placeholder_text(tmp_path: Path):
    pytest.importorskip("dash")
    component = board.render_evidence_trail(
        "ZZZ",
        payload=None,
        impactsearch_ref=None,
        stackbuilder_ref=None,
        trafficflow_ref=None,
        confluence_ref=None,
        calendar_timeframes=[],
        health_report=None,
    )
    text = _component_text(component)
    assert "Not yet built for this ticker." in text


# ---------------------------------------------------------------------------
# 8. BOARD_COPY owns visible copy
# ---------------------------------------------------------------------------


def test_board_copy_dict_owns_visible_copy():
    expected_visible = {
        "No saved tickers yet.",
        "Not yet built for this ticker.",
        "Historical research output. Not investment advice. Not a live "
        "signal feed.",
        (
            "PRJCT9 is a pattern-discovery engine. It studies saved "
            "historical signal behavior, ranks current signal alignment, "
            "and exposes coverage gaps instead of hiding them."
        ),
        "Not investment advice.",
        "Not a live trading signal feed.",
        "Not a guarantee of future performance.",
        "Saved research only.",
        "Town Hall Scoreboard",
        "Featured High Score",
        "Evidence Trail",
        "What PRJCT9 Is",
        "What It Is Not",
        "{active} of {total} timeframes agree",
        "Confluence data unavailable",
    }
    flat = _flatten_board_copy_values()
    missing = expected_visible - flat
    assert not missing, (
        "expected visible strings missing from BOARD_COPY: " + repr(missing)
    )


# ---------------------------------------------------------------------------
# 9. DESIGN_TOKENS owns colors
# ---------------------------------------------------------------------------


_HEX_OR_RGB_LITERAL = re.compile(
    r"""(?xi)
    (?:"|')                         # opening quote
    (
        \#[0-9a-f]{3,8}              # hex literal
        |
        rgba?\([^)]*\)               # rgb or rgba literal
    )
    (?:"|')                         # closing quote
    """,
)


def test_design_tokens_dict_owns_all_colors():
    src_path = Path(board.__file__)
    raw = src_path.read_text(encoding="utf-8").splitlines()

    # Find the DESIGN_TOKENS dict line range so its literals are
    # allowed; any color literal outside that range is a violation.
    start_idx = None
    end_idx = None
    depth = 0
    for i, line in enumerate(raw):
        if start_idx is None and line.startswith("DESIGN_TOKENS"):
            start_idx = i
            depth = line.count("{") - line.count("}")
            if depth == 0:
                end_idx = i
                break
            continue
        if start_idx is not None and end_idx is None:
            depth += line.count("{") - line.count("}")
            if depth == 0:
                end_idx = i
                break
    assert start_idx is not None and end_idx is not None, (
        "could not locate DESIGN_TOKENS block in daily_signal_board.py"
    )

    violations: list[tuple[int, str]] = []
    for i, line in enumerate(raw):
        if start_idx <= i <= end_idx:
            continue
        if _HEX_OR_RGB_LITERAL.search(line):
            violations.append((i + 1, line.rstrip()))
    assert not violations, (
        "color literals outside DESIGN_TOKENS: "
        + repr(violations[:8])
    )


# ---------------------------------------------------------------------------
# 10. No live engine / yfinance imports
# ---------------------------------------------------------------------------


def test_no_live_engine_or_yfinance_imports():
    src_path = Path(board.__file__)
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    forbidden = {
        "yfinance", "onepass", "impactsearch", "stackbuilder",
        "trafficflow", "confluence", "cross_ticker_confluence",
        "spymaster",
    }
    allowed = {
        "primary_signal_engine",
        "research_artifacts",
        "research_catalogue_health",
    }
    found_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_modules.append(node.module)
    bad = [m for m in found_modules if m.split(".")[0] in forbidden]
    assert not bad, (
        "forbidden live-engine import in daily_signal_board: "
        + repr(bad)
    )
    # Sanity: at least one of the allowed helpers is referenced.
    assert any(
        m.split(".")[0] in allowed for m in found_modules
    ), (
        "daily_signal_board does not import any of the documented "
        "read-only helpers: " + repr(allowed)
    )


# ---------------------------------------------------------------------------
# 11. Disclaimer exact
# ---------------------------------------------------------------------------


def test_disclaimer_string_is_present_and_exact():
    expected = (
        "Historical research output. Not investment advice. Not a live "
        "signal feed."
    )
    assert board.BOARD_COPY["featured_disclaimer"] == expected


# ---------------------------------------------------------------------------
# 12. Empty cache renders all sections
# ---------------------------------------------------------------------------


def test_empty_cache_renders_all_sections_without_exception(tmp_path: Path):
    pytest.importorskip("dash")
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    layout = app.layout
    for sid in (
        "section-scoreboard",
        "section-featured",
        "section-evidence-trail",
        "section-what-prjct9-is",
        "section-what-it-is-not",
    ):
        assert _component_contains_id(layout, sid), (
            f"section {sid!r} missing from layout"
        )
    text = _component_text(layout)
    assert board.BOARD_COPY["empty_scoreboard"] in text


# ---------------------------------------------------------------------------
# 13. No disk-write calls
# ---------------------------------------------------------------------------


def test_board_module_has_no_disk_write_calls():
    src = Path(board.__file__).read_text(encoding="utf-8")
    forbidden_patterns = [
        r"\.write_text\(",
        r"\.write_bytes\(",
        r"pickle\.dump\(",
        # json.dump( with no s -> writes to file. ``json.dumps`` is fine.
        r"json\.dump\(",
        r"_rch\.write_",
        r"_ra\.write_",
        r"research_catalogue_health\.write_",
        r"research_artifacts\.write_",
    ]
    for pat in forbidden_patterns:
        if re.search(pat, src):
            pytest.fail(
                f"daily_signal_board.py contains disk-write call "
                f"matching /{pat}/"
            )
    # Sanity: also block obvious ``open(path, "w")`` style writes.
    if re.search(r"open\([^)]*['\"]w", src):
        pytest.fail(
            "daily_signal_board.py opens a file in write mode"
        )


# ---------------------------------------------------------------------------
# 14. build_app() returns a Dash app with all five section IDs
# ---------------------------------------------------------------------------


def test_app_boots_with_layout(tmp_path: Path):
    pytest.importorskip("dash")
    import dash
    cache_dir, artifact_root, sig_lib_dir = _empty_dirs(tmp_path)
    app = board.build_app(
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        sig_lib_dir=sig_lib_dir,
    )
    assert isinstance(app, dash.Dash)
    for sid in (
        "section-scoreboard",
        "section-featured",
        "section-evidence-trail",
        "section-what-prjct9-is",
        "section-what-it-is-not",
    ):
        assert _component_contains_id(app.layout, sid)


# ---------------------------------------------------------------------------
# Component traversal helpers
# ---------------------------------------------------------------------------


def _flatten_board_copy_values() -> set[str]:
    out: set[str] = set()
    for v in board.BOARD_COPY.values():
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, str):
                    out.add(item)
    return out


def _component_contains_id(component: Any, target_id: str) -> bool:
    if component is None or isinstance(component, str):
        return False
    if isinstance(component, (list, tuple)):
        return any(
            _component_contains_id(c, target_id) for c in component
        )
    if getattr(component, "id", None) == target_id:
        return True
    children = getattr(component, "children", None)
    if children is None:
        return False
    return _component_contains_id(children, target_id)


def _component_text(component: Any) -> str:
    pieces: list[str] = []

    def _walk(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, str):
            pieces.append(node)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)
            return
        cid = getattr(node, "id", None)
        if cid is not None:
            pieces.append(str(cid))
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(component)
    return "\n".join(pieces)


def _ordered_station_ids(component: Any) -> list[str]:
    seen: list[str] = []
    sidset = set(board.STATION_IDS)

    def _walk(node: Any) -> None:
        if node is None or isinstance(node, str):
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)
            return
        cid = getattr(node, "id", None)
        if cid in sidset and cid not in seen:
            seen.append(cid)
        children = getattr(node, "children", None)
        if children is not None:
            _walk(children)

    _walk(component)
    return seen
