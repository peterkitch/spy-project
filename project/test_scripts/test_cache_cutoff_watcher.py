"""Phase 6H-2 tests for cache_cutoff_watcher.

Pins the watcher contract:

  - cache strictly ahead of cutoff -> ready_for_pipeline_write
  - cache equal to cutoff -> pipeline_output_lags_persist_skip
  - cache strictly behind cutoff -> refresh_source_cache
  - missing cache PKL -> missing_cache
  - unparseable cache date -> manual_review
  - cache_unreadable PKL -> manual_review
  - the resolver default matches
    confluence_pipeline_readiness.resolve_current_as_of_date
  - explicit ``current_as_of_date`` overrides the resolver
  - ``ready_tickers`` carries only the strict-inequality wins
  - report counts add up across tickers
  - module has no yfinance / live engine / dash / pipeline
    runner imports
  - module performs zero writes against cache_dir
  - CLI emits valid JSON for both ``--ticker`` and ``--tickers``
  - CLI rejects mutually-exclusive args without leaking
    SystemExit
"""
from __future__ import annotations

import ast
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import cache_cutoff_watcher as ccw  # noqa: E402
import confluence_pipeline_readiness as cpr  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    for d in (cache_dir, artifact_root):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache_dir,
        "artifact_root": artifact_root,
    }


def _safe_filename(ticker: str) -> str:
    safe = str(ticker).strip().upper().replace("^", "_")
    return f"{safe}_precomputed_results.pkl"


def _write_cache_pkl_with_date(
    cache_dir: Path,
    ticker: str,
    last_date: str,
) -> Path:
    """Write a minimal Spymaster-shaped cache PKL.

    The watcher only inspects the date fields, so we omit the
    heavy ``preprocessed_data`` DataFrame entirely. The
    timestamp is stored as a ``datetime`` so the reader's
    ``isoformat`` path is exercised."""
    import datetime as _dt

    cache_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.strptime(last_date, "%Y-%m-%d")
    payload = {
        "_last_date": ts,
        "last_date": ts,
        "last_processed_date": ts,
    }
    path = cache_dir / _safe_filename(ticker)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_cache_pkl_with_unparseable_date(
    cache_dir: Path, ticker: str,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "_last_date": "not-an-iso-date",
        "last_date": "still-bad",
    }
    path = cache_dir / _safe_filename(ticker)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_cache_pkl_with_no_date_fields(
    cache_dir: Path, ticker: str,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"unrelated_field": 42}
    path = cache_dir / _safe_filename(ticker)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_cache_pkl_corrupt(
    cache_dir: Path, ticker: str,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / _safe_filename(ticker)
    # Random bytes that are not a valid pickle stream.
    path.write_bytes(b"\x80\x04\x95not-a-pickle-stream")
    return path


def _snapshot_tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_watcher_module_has_no_forbidden_imports():
    tree = ast.parse(
        Path(ccw.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance",
        "trafficflow",
        "spymaster",
        "impactsearch",
        "onepass",
        "confluence",
        "cross_ticker_confluence",
        "dash",
        "daily_signal_board",
        "confluence_pipeline_runner",
        "signal_engine_cache_refresher",
        "confluence_mtf_artifact_builder",
        "trafficflow_k_artifact_builder",
        "trafficflow_multitimeframe_bridge",
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
        "forbidden import in cache_cutoff_watcher: " + repr(bad)
    )


# ---------------------------------------------------------------------------
# 2. Per-ticker classification
# ---------------------------------------------------------------------------


def test_cache_strictly_ahead_recommends_ready(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )
    state = ccw.evaluate_cache_cutoff_state(
        "SPY", cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-08",
    )
    assert state.cache_exists is True
    assert state.cache_date_range_end == "2026-05-11"
    assert state.current_as_of_date == "2026-05-08"
    assert state.cache_ahead_of_cutoff is True
    assert state.cache_equal_to_cutoff is False
    assert state.cache_behind_cutoff is False
    assert state.recommended_operator_action == (
        ccw.ACTION_READY_FOR_PIPELINE_WRITE
    )
    assert state.issue_codes == ()


def test_cache_equal_recommends_persist_skip_lag(tmp_path: Path):
    """The SPY-shape live scenario: source cache reaches the
    cutoff exactly. Phase 6D-1 persist_skip_bars=1 will land
    Confluence one bar behind, so a rerun cannot make the
    ticker leader-eligible. Watcher must name that."""
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )
    state = ccw.evaluate_cache_cutoff_state(
        "SPY", cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-11",
    )
    assert state.cache_equal_to_cutoff is True
    assert state.cache_ahead_of_cutoff is False
    assert state.cache_behind_cutoff is False
    assert state.recommended_operator_action == (
        ccw.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
    )
    assert state.issue_codes == ()


def test_cache_strictly_behind_recommends_refresh(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2024-01-31",
    )
    state = ccw.evaluate_cache_cutoff_state(
        "SPY", cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-11",
    )
    assert state.cache_behind_cutoff is True
    assert state.recommended_operator_action == (
        ccw.ACTION_REFRESH_SOURCE_CACHE
    )


def test_missing_cache_recommends_missing_cache(tmp_path: Path):
    dirs = _layout(tmp_path)
    state = ccw.evaluate_cache_cutoff_state(
        "GHOST", cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-11",
    )
    assert state.cache_exists is False
    assert state.cache_date_range_end is None
    assert state.recommended_operator_action == (
        ccw.ACTION_MISSING_CACHE
    )
    assert ccw.ISSUE_MISSING_CACHE in state.issue_codes


def test_unparseable_cache_date_routes_to_manual_review(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_unparseable_date(
        dirs["cache_dir"], "ZZZZ",
    )
    state = ccw.evaluate_cache_cutoff_state(
        "ZZZZ", cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-11",
    )
    assert state.cache_exists is True
    assert state.cache_date_range_end is None
    assert state.recommended_operator_action == (
        ccw.ACTION_MANUAL_REVIEW
    )
    assert ccw.ISSUE_UNPARSEABLE_CACHE_DATE in state.issue_codes


def test_cache_with_no_date_fields_routes_to_manual_review(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_no_date_fields(dirs["cache_dir"], "AAAA")
    state = ccw.evaluate_cache_cutoff_state(
        "AAAA", cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-11",
    )
    assert state.cache_exists is True
    assert state.recommended_operator_action == (
        ccw.ACTION_MANUAL_REVIEW
    )
    assert ccw.ISSUE_NO_CACHE_DATE in state.issue_codes


def test_corrupt_cache_pkl_routes_to_manual_review(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl_corrupt(dirs["cache_dir"], "BBBB")
    state = ccw.evaluate_cache_cutoff_state(
        "BBBB", cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-11",
    )
    assert state.cache_exists is True
    assert state.recommended_operator_action == (
        ccw.ACTION_MANUAL_REVIEW
    )
    assert ccw.ISSUE_CACHE_UNREADABLE in state.issue_codes


# ---------------------------------------------------------------------------
# 3. Cutoff resolver wiring
# ---------------------------------------------------------------------------


def test_default_cutoff_uses_resolve_current_as_of_date(
    tmp_path: Path, monkeypatch,
):
    """When no ``current_as_of_date`` is supplied, the watcher
    must delegate to
    ``confluence_pipeline_readiness.resolve_current_as_of_date``
    so its verdict matches the rest of Phase 6."""
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )

    sentinel = "2026-04-01"

    def _stub_resolver(
        explicit=None, env=None, now=None,
    ):
        # If the caller passed an explicit value, honor it
        # (matches the real resolver's priority).
        if explicit:
            return str(explicit)
        return sentinel

    monkeypatch.setattr(
        ccw._cpr, "resolve_current_as_of_date", _stub_resolver,
    )
    state = ccw.evaluate_cache_cutoff_state(
        "SPY", cache_dir=dirs["cache_dir"],
    )
    assert state.current_as_of_date == sentinel
    assert state.cache_ahead_of_cutoff is True
    assert state.recommended_operator_action == (
        ccw.ACTION_READY_FOR_PIPELINE_WRITE
    )


def test_explicit_current_as_of_date_overrides_default(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )
    state = ccw.evaluate_cache_cutoff_state(
        "SPY", cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-08",
    )
    assert state.current_as_of_date == "2026-05-08"
    assert state.cache_ahead_of_cutoff is True


# ---------------------------------------------------------------------------
# 4. Aggregate report
# ---------------------------------------------------------------------------


def test_report_counts_actions_and_ready_tickers(tmp_path: Path):
    dirs = _layout(tmp_path)
    # Three distinct shapes:
    #   AHEAD - SPY at 2026-05-11 vs cutoff 2026-05-08
    #   EQUAL - AAPL at 2026-05-08 vs cutoff 2026-05-08
    #   BEHIND - OLD at 2024-01-31 vs cutoff 2026-05-08
    # And a missing fourth ticker.
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "AAPL", "2026-05-08",
    )
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "OLD", "2024-01-31",
    )
    report = ccw.build_cache_cutoff_watch_report(
        ["SPY", "AAPL", "OLD", "GHOST"],
        cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-08",
    )
    assert report.inspected_count == 4
    actions = {
        s.ticker: s.recommended_operator_action
        for s in report.states
    }
    assert actions["SPY"] == ccw.ACTION_READY_FOR_PIPELINE_WRITE
    assert actions["AAPL"] == (
        ccw.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
    )
    assert actions["OLD"] == ccw.ACTION_REFRESH_SOURCE_CACHE
    assert actions["GHOST"] == ccw.ACTION_MISSING_CACHE

    counts = report.counts_by_recommended_operator_action
    assert sum(counts.values()) == 4
    assert counts.get(
        ccw.ACTION_READY_FOR_PIPELINE_WRITE,
    ) == 1
    assert counts.get(
        ccw.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP,
    ) == 1
    assert counts.get(ccw.ACTION_REFRESH_SOURCE_CACHE) == 1
    assert counts.get(ccw.ACTION_MISSING_CACHE) == 1

    # ready_tickers = strict-inequality wins only.
    assert report.ready_tickers == ("SPY",)


def test_ready_tickers_excludes_equal_cache(tmp_path: Path):
    """A ticker whose cache equals the cutoff is NOT pilot-ready
    because the persist trim guarantees a stale Confluence
    rewrite. The watcher's ready_tickers must reflect that."""
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-08",
    )
    report = ccw.build_cache_cutoff_watch_report(
        ["SPY"],
        cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-08",
    )
    assert report.ready_tickers == ()
    assert report.counts_by_recommended_operator_action.get(
        ccw.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP,
    ) == 1


def test_to_json_dict_round_trips(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )
    report = ccw.build_cache_cutoff_watch_report(
        ["SPY"],
        cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-08",
    )
    d = report.to_json_dict()
    s = json.dumps(d)  # must not raise
    assert "SPY" in s
    assert d["ready_tickers"] == ["SPY"]
    assert d["states"][0]["recommended_operator_action"] == (
        ccw.ACTION_READY_FOR_PIPELINE_WRITE
    )


# ---------------------------------------------------------------------------
# 5. No writes
# ---------------------------------------------------------------------------


def test_watcher_does_not_write_to_cache_dir(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )
    before_cache = _snapshot_tree(dirs["cache_dir"])
    before_artifact = _snapshot_tree(dirs["artifact_root"])
    ccw.build_cache_cutoff_watch_report(
        ["SPY"],
        cache_dir=dirs["cache_dir"],
        current_as_of_date="2026-05-08",
    )
    assert _snapshot_tree(dirs["cache_dir"]) == before_cache
    assert _snapshot_tree(dirs["artifact_root"]) == before_artifact


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------


def test_cli_ticker_single_emits_json(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )
    argv = [
        "--ticker", "SPY",
        "--cache-dir", str(dirs["cache_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = ccw.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 1
    assert payload["states"][0]["ticker"] == "SPY"
    assert payload["ready_tickers"] == ["SPY"]


def test_cli_tickers_csv_emits_json(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "SPY", "2026-05-11",
    )
    _write_cache_pkl_with_date(
        dirs["cache_dir"], "AAPL", "2026-05-08",
    )
    argv = [
        "--tickers", "SPY,AAPL",
        "--cache-dir", str(dirs["cache_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = ccw.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    tickers = [s["ticker"] for s in payload["states"]]
    assert tickers == ["SPY", "AAPL"]
    assert payload["ready_tickers"] == ["SPY"]


def test_cli_unknown_flag_returns_2_without_system_exit(capsys):
    rc = None
    try:
        rc = ccw.main(["--definitely-not-a-flag"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on unknown flag; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_mutually_exclusive_ticker_args_return_2(capsys):
    rc = None
    try:
        rc = ccw.main([
            "--ticker", "SPY", "--tickers", "AAPL,GOOG",
        ])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on conflicting args; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_empty_invocation_returns_0(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    argv = [
        "--cache-dir", str(dirs["cache_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = ccw.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 0
    assert payload["states"] == []
    assert payload["ready_tickers"] == []


# ---------------------------------------------------------------------------
# 7. Constants registered
# ---------------------------------------------------------------------------


def test_persist_skip_lag_action_constant_matches_audit_layer():
    """The action string must match the literal used by the
    launch audit + freshness preflight so JSON consumers can
    join across the three tools."""
    assert (
        ccw.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP
        == "pipeline_output_lags_persist_skip"
    )


def test_all_actions_listed_in_watcher_actions_tuple():
    expected = {
        ccw.ACTION_READY_FOR_PIPELINE_WRITE,
        ccw.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP,
        ccw.ACTION_REFRESH_SOURCE_CACHE,
        ccw.ACTION_MISSING_CACHE,
        ccw.ACTION_MANUAL_REVIEW,
    }
    assert set(ccw.WATCHER_ACTIONS) == expected
