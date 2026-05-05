"""
Phase 4B regression tests for ``cross_ticker_confluence_dash``.

Synthetic Phase 4A run-directory fixtures are built inline; the engine
module ``cross_ticker_confluence`` is intentionally NOT imported. The
canonical artifact shapes are reproduced here so this test suite is
self-contained (the AST static guard test asserts the same boundary on
the dash module itself).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import cross_ticker_confluence_dash as ctcd
import provenance_manifest as pm


# ---------------------------------------------------------------------------
# Local synthetic Phase 4A fixture builder (no engine import)
# ---------------------------------------------------------------------------


_SCHEMA_VERSION = 1
_INTERVALS = ("1d", "1wk", "1mo", "3mo", "1y")
_RUN_DATE = "2024-12-30"


def _write_canonical_with_sidecar(
    path: Path, payload: Mapping[str, Any], *, artifact_type: str,
) -> None:
    """Write a canonical JSON artifact + Phase 3 sidecar manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    manifest = pm.build_output_manifest(
        artifact_type=artifact_type,
        producer_engine="cross_ticker_confluence",
        engine_version="1.0.0",
        params={"intervals": list(_INTERVALS)},
        content_obj=payload,
    )
    pm.write_output_manifest(path, manifest, include_file_sha256=True)


def _build_coverage_record(
    series_id: str, *,
    top_level_status: str,
    eligible: bool,
    issue_codes: Sequence[str] = (),
    onepass_status: str = "loaded_verified",
    spymaster_status: str = "not_applicable",
    stackbuilder_status: str = "loaded_verified",
    interval_signal: str = "Buy",
    interval_status: str = "loaded_verified",
) -> Dict[str, Any]:
    per_interval: Dict[str, Any] = {}
    for iv in _INTERVALS:
        per_interval[iv] = {
            "status": interval_status,
            "signal": interval_signal,
            "source": "onepass_daily" if iv == "1d"
            else "multi_timeframe_library",
        }
    return {
        "series_id": series_id,
        "series_kind": "yfinance_ticker",
        "series_metadata": {"ticker": series_id, "source": "yfinance"},
        "top_level_status": top_level_status,
        "eligible_for_rankings": bool(eligible),
        "issue_codes": list(issue_codes),
        "per_source_status": {
            "onepass_daily": {"status": onepass_status},
            "spymaster_fallback": {"status": spymaster_status},
            "stackbuilder_run": {"status": stackbuilder_status},
        },
        "per_interval_status": per_interval,
    }


def _build_ranking_record(
    series_id: str, *,
    rank: int = 1,
    rank_group: str = "full_unanimity_buy",
    signal_direction: str = "Buy",
    interval_signal: str = "Buy",
    alignment_pct: float = 100.0,
    active_count: int = 5,
) -> Dict[str, Any]:
    return {
        "series_id": series_id,
        "series_metadata": {"ticker": series_id, "source": "yfinance"},
        "rank": rank,
        "rank_group": rank_group,
        "signal_direction": signal_direction,
        "run_date_signal": signal_direction,
        "producer_next_session_signal": None,
        "confluence": {
            "active_count": active_count,
            "total_count": 5,
            "buy_count": 5 if signal_direction == "Buy" else 0,
            "short_count": 5 if signal_direction == "Short" else 0,
            "none_count": 5 if signal_direction == "None" else 0,
            "alignment_pct": alignment_pct,
        },
        "interval_signals": {iv: interval_signal for iv in _INTERVALS},
        "stackbuilder": {
            "status": "loaded_verified",
            "run_id": "STK_RUN",
            "selected_stack": ["SPY"],
            "metrics": {},
        },
    }


def _build_overlay_record(series_id: str, *, signal: str = "Buy") -> Dict[str, Any]:
    return {
        "series_id": series_id,
        "series_metadata": {"ticker": series_id, "source": "yfinance"},
        "intervals": {
            iv: [{"date": _RUN_DATE, "signal": signal,
                  "source": "onepass_daily" if iv == "1d"
                  else "multi_timeframe_library"}]
            for iv in _INTERVALS
        },
    }


def _build_universe_record(
    position: int, sym: str, *, valid: bool = True,
    invalid_reason: str = None,
) -> Dict[str, Any]:
    return {
        "position": position,
        "series_id": sym,
        "series_kind": "yfinance_ticker",
        "source_symbol": sym,
        "normalized_symbol": sym,
        "valid_symbol": valid,
        "invalid_reason": invalid_reason,
    }


def _write_run_directory(
    out_root: Path, run_id: str, *,
    cov_records: Sequence[Mapping[str, Any]],
    rank_records: Sequence[Mapping[str, Any]],
    overlay_records: Sequence[Mapping[str, Any]] = (),
    universe_records: Sequence[Mapping[str, Any]] = (),
    run_date: str = _RUN_DATE,
    finished_at: str = "2024-12-30T01:00:00",
    universe_mode: str = "tickers",
    status: str = "complete",
    omit_field: str = None,
    artifact_type: str = "cross_ticker_confluence_run",
) -> Path:
    """Write a real-shape Phase 4A run directory."""
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    coverage_counts: Dict[str, int] = {}
    issue_counts: Dict[str, int] = {}
    for r in cov_records:
        coverage_counts[r["top_level_status"]] = coverage_counts.get(
            r["top_level_status"], 0,
        ) + 1
        for ic in r.get("issue_codes") or []:
            issue_counts[ic] = issue_counts.get(ic, 0) + 1

    coverage_payload = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "cross_ticker_confluence_coverage",
        "run_id": run_id,
        "run_date": run_date,
        "universe_mode": universe_mode,
        "universe_hash": "sha256:" + ("0" * 64),
        "intervals": list(_INTERVALS),
        "records": list(cov_records),
    }
    rankings_payload = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "cross_ticker_confluence_rankings",
        "run_id": run_id,
        "run_date": run_date,
        "universe_mode": universe_mode,
        "universe_hash": "sha256:" + ("0" * 64),
        "intervals": list(_INTERVALS),
        "records": list(rank_records),
    }
    overlay_payload = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "cross_ticker_confluence_overlay",
        "run_id": run_id,
        "run_date": run_date,
        "history_days": 365,
        "records": list(overlay_records),
    }
    snapshot_payload = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "cross_ticker_confluence_universe_snapshot",
        "run_id": run_id,
        "universe_mode": universe_mode,
        "resolved_at": "2024-12-30T00:00:00",
        "source": {
            "kind": "cli_tickers", "path": "<cli>", "file_sha256": None,
        },
        "universe_hash": "sha256:" + ("0" * 64),
        "counts": {
            "requested": len(universe_records) or len(cov_records),
            "valid": len(universe_records) or len(cov_records),
            "invalid": 0,
        },
        "series": list(universe_records) if universe_records else [
            _build_universe_record(i, r["series_id"])
            for i, r in enumerate(cov_records)
        ],
    }

    _write_canonical_with_sidecar(
        run_dir / "coverage.json", coverage_payload,
        artifact_type="cross_ticker_confluence_coverage",
    )
    _write_canonical_with_sidecar(
        run_dir / "rankings.json", rankings_payload,
        artifact_type="cross_ticker_confluence_rankings",
    )
    _write_canonical_with_sidecar(
        run_dir / "overlay.json", overlay_payload,
        artifact_type="cross_ticker_confluence_overlay",
    )
    _write_canonical_with_sidecar(
        run_dir / "universe_snapshot.json", snapshot_payload,
        artifact_type="cross_ticker_confluence_universe_snapshot",
    )

    rm = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_kind": "output",
        "artifact_type": artifact_type,
        "producer_engine": "cross_ticker_confluence",
        "engine_version": "1.0.0",
        "run_id": run_id,
        "run_date": run_date,
        "started_at": "2024-12-30T00:30:00",
        "finished_at": finished_at,
        "status": status,
        "params": {
            "universe_mode": universe_mode,
            "intervals": list(_INTERVALS),
            "history_days": 365,
            "max_input_age_days": 45,
            "strict_manifests": False,
            "max_workers": 1,
        },
        "universe": {
            "universe_hash": "sha256:" + ("0" * 64),
            "universe_mode": universe_mode,
            "snapshot_path": "universe_snapshot.json",
            "source": {
                "kind": "cli_tickers", "path": "<cli>",
                "file_sha256": None,
            },
            "counts": snapshot_payload["counts"],
        },
        "coverage_counts": coverage_counts,
        "issue_counts": issue_counts,
        "input_artifacts": [],
        "input_manifest_hashes": [],
        "output_artifacts": [
            {
                "name": "coverage", "filename": "coverage.json",
                "format": "json", "file_sha256": "deadbeef",
            },
        ],
    }
    if omit_field:
        rm.pop(omit_field, None)
    with open(run_dir / "run_manifest.json", "w", encoding="utf-8") as fh:
        json.dump(rm, fh, indent=2, sort_keys=True)
    return run_dir


@pytest.fixture
def synthetic_runs(tmp_path):
    """Build three runs: two valid (different finished_at), one
    invalid (malformed run_manifest.json)."""
    out_root = tmp_path / "ctc"
    cov = [
        _build_coverage_record("AAA", top_level_status="scored_full",
                               eligible=True),
        _build_coverage_record("BBB", top_level_status="scored_partial",
                               eligible=True,
                               issue_codes=("missing_stackbuilder_run",)),
        _build_coverage_record(
            "CCC", top_level_status="skipped_no_daily_source",
            eligible=False,
            interval_signal="Buy", interval_status="loaded_verified",
        ),
        _build_coverage_record(
            "DDD", top_level_status="skipped_no_signal_libraries",
            eligible=False,
            onepass_status="missing", stackbuilder_status="missing",
        ),
        _build_coverage_record(
            "EEE", top_level_status="invalid_universe_symbol",
            eligible=False,
            onepass_status="missing", stackbuilder_status="missing",
        ),
    ]
    rank = [
        _build_ranking_record("AAA", rank=1,
                              rank_group="full_unanimity_buy",
                              signal_direction="Buy"),
        _build_ranking_record("BBB", rank=2,
                              rank_group="partial_buy",
                              signal_direction="Buy",
                              alignment_pct=80.0, active_count=4),
    ]
    overlay = [
        _build_overlay_record("AAA"),
        _build_overlay_record("BBB"),
    ]
    universe = [
        _build_universe_record(i, r["series_id"])
        for i, r in enumerate(cov[:-1])
    ] + [_build_universe_record(
        4, "EEE", valid=False, invalid_reason="symbol_chars_invalid",
    )]

    _write_run_directory(
        out_root, "20260101T000000Z-aaaaaaaa",
        cov_records=cov, rank_records=rank,
        overlay_records=overlay, universe_records=universe,
        finished_at="2026-01-01T01:00:00",
    )
    _write_run_directory(
        out_root, "20260102T000000Z-bbbbbbbb",
        cov_records=cov, rank_records=rank,
        overlay_records=overlay, universe_records=universe,
        finished_at="2026-01-02T01:00:00",
    )
    # Invalid run: malformed run_manifest.json.
    bad = out_root / "20260103T000000Z-cccccccc"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "run_manifest.json").write_text("{ this is not valid",
                                           encoding="utf-8")
    return out_root


# ---------------------------------------------------------------------------
# Public-helper tests
# ---------------------------------------------------------------------------


def test_discover_runs_sorts_latest_first(synthetic_runs):
    runs = ctcd.discover_runs(synthetic_runs)
    valid = [r for r in runs if r.valid]
    assert len(valid) == 2
    # Newer finished_at first.
    assert valid[0].run_id == "20260102T000000Z-bbbbbbbb"
    assert valid[1].run_id == "20260101T000000Z-aaaaaaaa"


def test_discover_runs_surfaces_invalid(synthetic_runs):
    runs = ctcd.discover_runs(synthetic_runs)
    invalid = [r for r in runs if not r.valid]
    assert len(invalid) == 1
    assert "malformed" in (invalid[0].invalid_reason or "")


def test_discover_runs_empty_root(tmp_path):
    empty = tmp_path / "missing_root"
    assert ctcd.discover_runs(empty) == []


def test_discover_runs_invalid_kinds(tmp_path):
    """Each invalid kind (missing fields, wrong artifact_type, missing
    file) surfaces without crashing the scan."""
    out = tmp_path / "ctc"
    cov = [_build_coverage_record(
        "AAA", top_level_status="scored_full", eligible=True,
    )]
    rank = [_build_ranking_record("AAA")]
    # Wrong artifact_type
    _write_run_directory(
        out, "WRONG_TYPE",
        cov_records=cov, rank_records=rank,
        artifact_type="not_a_run",
    )
    # Missing required field
    _write_run_directory(
        out, "MISSING_FIELD",
        cov_records=cov, rank_records=rank,
        omit_field="coverage_counts",
    )
    # Missing run_manifest.json entirely
    (out / "NO_MANIFEST").mkdir(parents=True, exist_ok=True)
    runs = ctcd.discover_runs(out)
    bad = {r.run_id: r for r in runs if not r.valid}
    assert "WRONG_TYPE" in bad
    assert "MISSING_FIELD" in bad
    assert "NO_MANIFEST" in bad


def test_load_run_bundle_loads_all_five_payloads(synthetic_runs):
    bundle = ctcd.load_run_bundle(
        synthetic_runs / "20260102T000000Z-bbbbbbbb",
    )
    assert bundle.coverage["artifact_type"] == "cross_ticker_confluence_coverage"
    assert bundle.rankings["artifact_type"] == "cross_ticker_confluence_rankings"
    assert bundle.overlay["artifact_type"] == "cross_ticker_confluence_overlay"
    assert bundle.universe_snapshot["artifact_type"] == "cross_ticker_confluence_universe_snapshot"
    assert bundle.run_manifest["artifact_type"] == "cross_ticker_confluence_run"


def test_load_run_bundle_rejects_missing_artifact(tmp_path):
    out = tmp_path / "ctc"
    cov = [_build_coverage_record(
        "AAA", top_level_status="scored_full", eligible=True,
    )]
    rank = [_build_ranking_record("AAA")]
    rd = _write_run_directory(out, "RUN1", cov_records=cov, rank_records=rank)
    (rd / "rankings.json").unlink()
    with pytest.raises(FileNotFoundError):
        ctcd.load_run_bundle(rd)


def test_load_run_bundle_rejects_wrong_artifact_type(tmp_path):
    out = tmp_path / "ctc"
    cov = [_build_coverage_record(
        "AAA", top_level_status="scored_full", eligible=True,
    )]
    rank = [_build_ranking_record("AAA")]
    rd = _write_run_directory(out, "RUN1", cov_records=cov, rank_records=rank)
    # Rewrite coverage.json with the wrong artifact_type.
    cov_path = rd / "coverage.json"
    payload = json.loads(cov_path.read_text("utf-8"))
    payload["artifact_type"] = "not_a_coverage"
    _write_canonical_with_sidecar(
        cov_path, payload,
        artifact_type="not_a_coverage",  # tampered sidecar matches body
    )
    with pytest.raises(RuntimeError):
        ctcd.load_run_bundle(rd)


def test_load_run_bundle_rejects_legacy(tmp_path):
    """Legacy artifacts (no Phase 3 sidecar) must be rejected."""
    out = tmp_path / "ctc"
    cov = [_build_coverage_record(
        "AAA", top_level_status="scored_full", eligible=True,
    )]
    rank = [_build_ranking_record("AAA")]
    rd = _write_run_directory(out, "RUN1", cov_records=cov, rank_records=rank)
    sidecar = rd / "coverage.json.manifest.json"
    sidecar.unlink()
    with pytest.raises(RuntimeError):
        ctcd.load_run_bundle(rd)


def test_load_run_bundle_rejects_mismatched_manifest(tmp_path):
    """A tampered canonical JSON whose content_hash no longer matches
    the sidecar must fail vresult.ok."""
    out = tmp_path / "ctc"
    cov = [_build_coverage_record(
        "AAA", top_level_status="scored_full", eligible=True,
    )]
    rank = [_build_ranking_record("AAA")]
    rd = _write_run_directory(out, "RUN1", cov_records=cov, rank_records=rank)
    cov_path = rd / "coverage.json"
    tampered = json.loads(cov_path.read_text("utf-8"))
    tampered["records"].append(_build_coverage_record(
        "ZZZ", top_level_status="scored_full", eligible=True,
    ))
    cov_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True), encoding="utf-8",
    )
    with pytest.raises(RuntimeError):
        ctcd.load_run_bundle(rd)


def test_flatten_rankings_shape(synthetic_runs):
    bundle = ctcd.load_run_bundle(
        synthetic_runs / "20260102T000000Z-bbbbbbbb",
    )
    rows = ctcd.flatten_rankings(bundle)
    assert len(rows) == 2
    assert rows[0]["rank"] == 1
    assert rows[0]["series_id"] == "AAA"
    assert rows[0]["rank_group"] == "full_unanimity_buy"
    assert rows[0]["signal_direction"] == "Buy"
    assert rows[0]["alignment_pct"] == 100.0
    assert "5/5 Buy" == rows[0]["interval_signals_summary"]


def test_flatten_coverage_shape(synthetic_runs):
    bundle = ctcd.load_run_bundle(
        synthetic_runs / "20260102T000000Z-bbbbbbbb",
    )
    rows = ctcd.flatten_coverage(bundle)
    assert len(rows) == 5
    by_id = {r["series_id"]: r for r in rows}
    assert by_id["AAA"]["top_level_status"] == "scored_full"
    assert by_id["BBB"]["issue_codes"] == "missing_stackbuilder_run"
    assert by_id["DDD"]["onepass_daily_status"] == "missing"
    # Per-interval columns present.
    for iv in _INTERVALS:
        assert f"{iv}_status" in by_id["AAA"]
        assert f"{iv}_signal" in by_id["AAA"]


def test_filter_coverage_by_top_level(synthetic_runs):
    bundle = ctcd.load_run_bundle(
        synthetic_runs / "20260102T000000Z-bbbbbbbb",
    )
    rows = ctcd.flatten_coverage(bundle)
    skipped = ctcd.filter_coverage(
        rows, top_level_status="skipped_no_daily_source",
    )
    assert {r["series_id"] for r in skipped} == {"CCC"}
    invalid = ctcd.filter_coverage(
        rows, top_level_status="invalid_universe_symbol",
    )
    assert {r["series_id"] for r in invalid} == {"EEE"}


def test_paginate_rows():
    rows = [{"i": i} for i in range(10)]
    assert ctcd.paginate_rows(rows, page_current=0, page_size=3) == [
        {"i": 0}, {"i": 1}, {"i": 2},
    ]
    assert ctcd.paginate_rows(rows, page_current=2, page_size=3) == [
        {"i": 6}, {"i": 7}, {"i": 8},
    ]
    assert ctcd.paginate_rows(rows, page_current=99, page_size=3) == []


def test_filter_rankings_by_group_and_direction(synthetic_runs):
    bundle = ctcd.load_run_bundle(
        synthetic_runs / "20260102T000000Z-bbbbbbbb",
    )
    rows = ctcd.flatten_rankings(bundle)
    full = ctcd.filter_rankings(rows, rank_group="full_unanimity_buy")
    assert {r["series_id"] for r in full} == {"AAA"}
    buys = ctcd.filter_rankings(rows, signal_direction="Buy")
    assert len(buys) == 2  # both AAA + BBB direction is Buy


def test_get_ticker_detail_present(synthetic_runs):
    bundle = ctcd.load_run_bundle(
        synthetic_runs / "20260102T000000Z-bbbbbbbb",
    )
    detail = ctcd.get_ticker_detail(bundle, "AAA")
    assert detail["present"] is True
    assert detail["coverage"]["top_level_status"] == "scored_full"
    assert detail["ranking"]["rank"] == 1
    assert detail["overlay"]["intervals"]["1d"]


def test_get_ticker_detail_absent(synthetic_runs):
    bundle = ctcd.load_run_bundle(
        synthetic_runs / "20260102T000000Z-bbbbbbbb",
    )
    detail = ctcd.get_ticker_detail(bundle, "ZZZ")
    assert detail["present"] is False
    assert detail["reason"] == "not_present_in_run"


# ---------------------------------------------------------------------------
# Dash app smoke
# ---------------------------------------------------------------------------


def test_build_app_returns_layout(synthetic_runs):
    app = ctcd.build_app(synthetic_runs)
    assert app is not None
    assert app.layout is not None


def test_build_app_layout_has_five_tabs(synthetic_runs):
    app = ctcd.build_app(synthetic_runs)
    rendered = str(app.layout)
    for label in ("Rankings", "Coverage", "Ticker Detail",
                  "Universe", "Provenance"):
        assert label in rendered, (
            f"tab label {label!r} not found in layout"
        )


def test_build_app_no_runs_does_not_crash(tmp_path):
    """An empty run_root must produce a layout with no exception."""
    app = ctcd.build_app(tmp_path / "missing")
    assert app.layout is not None


# ---------------------------------------------------------------------------
# AST static guard — banned imports / calls in the dash module
# ---------------------------------------------------------------------------


_BANNED_IMPORT_PREFIXES = (
    "yfinance",
    "onepass",
    "stackbuilder",
    "spymaster",
    "trafficflow",
    "impactsearch",
    "multi_timeframe_builder",
    "cross_ticker_confluence",
    "project.cross_ticker_confluence",
)

_BANNED_CALL_NAMES = {
    "run_cross_ticker_confluence",
    "resolve_universe",
    "process_series",
    "write_run_outputs",
    "load_verified_signal_library",
    "load_verified_pickle_artifact",
    "load_verified_xlsx_artifact",
    "read_pickle",
    "read_excel",
}


def _module_text() -> str:
    return (PROJECT_DIR / "cross_ticker_confluence_dash.py").read_text(
        encoding="utf-8",
    )


def test_ast_guard_no_banned_imports():
    tree = ast.parse(_module_text(),
                     filename="cross_ticker_confluence_dash.py")
    bad: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                for prefix in _BANNED_IMPORT_PREFIXES:
                    if name == prefix or name.startswith(prefix + "."):
                        bad.append(f"import {name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for prefix in _BANNED_IMPORT_PREFIXES:
                if mod == prefix or mod.startswith(prefix + "."):
                    bad.append(f"from {mod} import ...")
    assert not bad, (
        "banned imports found in cross_ticker_confluence_dash.py:\n"
        + "\n".join(bad)
    )


def test_ast_guard_no_banned_calls():
    tree = ast.parse(_module_text(),
                     filename="cross_ticker_confluence_dash.py")
    bad: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        target = None
        if isinstance(f, ast.Name):
            target = f.id
        elif isinstance(f, ast.Attribute):
            target = f.attr
        if target in _BANNED_CALL_NAMES:
            bad.append(f"line {node.lineno}: call to {target}")
    assert not bad, (
        "banned calls found in cross_ticker_confluence_dash.py:\n"
        + "\n".join(bad)
    )


def test_ast_guard_required_loaders_present():
    """Sanity: the module DOES use the allowed Phase 3 JSON loader."""
    tree = ast.parse(_module_text(),
                     filename="cross_ticker_confluence_dash.py")
    found_load_verified_json = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in (node.names or []):
                if alias.name == "load_verified_json_artifact":
                    found_load_verified_json = True
                    break
    assert found_load_verified_json, (
        "expected `from provenance_manifest import "
        "load_verified_json_artifact`"
    )
