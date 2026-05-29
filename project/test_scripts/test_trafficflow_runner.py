"""Phase B tests for the TrafficFlow headless runner scaffold.

All tests run without network, without importing trafficflow,
without invoking any engine, and without writing outside ``tmp_path``.
"""
from __future__ import annotations

import ast
import importlib
import io
import json
import os
import pickle
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUNNER_PATH = PROJECT_ROOT / "trafficflow_runner.py"

import trafficflow_runner as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Dynamic fixture-date helpers. trafficflow_runner.PRICE_CACHE_STALE_DAYS
# is 7 and classify_price_cache uses a strict greater-than comparison
# against datetime.now(timezone.utc). A hardcoded YYYY-MM-DD literal in
# a tail_date= argument decays past the threshold N days after the test
# was written; the helpers below derive tail dates from the test-run
# "today" so the freshness semantics survive wall-clock drift. The fresh
# helper sits well inside the 7-day window; the stale helper sits well
# past it. The 12-day skew between the two matches the original
# hardcoded "2026-05-22" minus "2026-05-10" skew, so STALE-vs-fresh
# tests retain their intended relationship.
_FRESH_TAIL_DAYS_OLD = 2
_STALE_TAIL_DAYS_OLD = 14


def _today_utc_date():
    return datetime.now(timezone.utc).date()


def _fresh_tail_date():
    return (_today_utc_date()
            - timedelta(days=_FRESH_TAIL_DAYS_OLD)).isoformat()


def _stale_tail_date():
    return (_today_utc_date()
            - timedelta(days=_STALE_TAIL_DAYS_OLD)).isoformat()


def _no_conflict(write_requested=False):
    return {"status": "ok", "conflicts": [], "queried_via": "fake", "error": None}


def _blocked_conflict(write_requested=False):
    return {
        "status": "blocked",
        "conflicts": [{"pid": 99999, "cmdline": "python trafficflow.py",
                       "matched_pattern": "trafficflow.py"}],
        "queried_via": "fake",
        "error": None,
    }


def _capture_main(argv, **kwargs):
    """Run runner.main(argv, ...) and capture stdout JSON + stderr."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = -1
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        rc = runner.main(argv, **kwargs)
    out_text = out_buf.getvalue()
    try:
        payload = json.loads(out_text) if out_text.strip() else None
    except json.JSONDecodeError:
        payload = None
    return rc, payload, err_buf.getvalue(), out_text


def _write_selected_build(stackbuilder_root, secondary, *, selected_run_dir,
                          selected_k=6, payload_extras=None):
    sb_dir = Path(stackbuilder_root) / secondary
    sb_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "secondary": secondary,
        "selected_run_id": "FAKE-RUN-0001",
        "selected_run_dir": str(selected_run_dir),
        "selected_k": selected_k,
        "selected_metric": "total_capture",
        "total_capture": 1.0,
        "sharpe_ratio": 0.5,
        "row_count": 12,
        "created_at": "2026-05-23T00:00:00Z",
        "selected_at": "2026-05-23T00:00:01Z",
        "selection_policy": "v2.total_capture_then_latest",
        "operator_pinned": False,
        "source_manifest_path": "n/a",
        "runner_version": "0.0.0",
        "selection_policy_context": {},
    }
    if payload_extras:
        payload.update(payload_extras)
    (sb_dir / "selected_build.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _make_fake_leaderboard(run_dir, *, k_to_members):
    """Write a fake leaderboard CSV the runner can read without openpyxl."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = ["K,Members"]
    for k, members in sorted(k_to_members.items()):
        members_repr = "[" + ", ".join(repr(m) for m in members) + "]"
        # Quote the entire Members cell so commas inside the list don't split rows.
        cell = members_repr.replace('"', '""')
        lines.append(f'{k},"{cell}"')
    (run_dir / "combo_leaderboard.csv").write_text("\n".join(lines),
                                                     encoding="utf-8")
    return run_dir / "combo_leaderboard.csv"


def _write_price_cache(price_cache_dir, secondary, *, tail_date=None):
    """Write a minimal CSV price cache.

    ``tail_date`` defaults to a freshness-helper value so the cache is
    classified OK by trafficflow_runner.classify_price_cache regardless
    of wall-clock drift.
    """
    if tail_date is None:
        tail_date = _fresh_tail_date()
    d = Path(price_cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{secondary}.csv").write_text(
        f"Date,Close\n2026-05-01,100.0\n{tail_date},123.45\n", encoding="utf-8")


class _FakeDF:
    def __init__(self, columns):
        self.columns = columns


class _TailIdxStub:
    """Picklable stub whose .max() returns a stored tail value."""
    def __init__(self, tail):
        self._tail = tail
    def __len__(self):
        return 1
    def max(self):
        return self._tail


class _TailDFStub:
    """Picklable stub for preprocessed_data with a .columns list and
    a .index whose .max() returns the configured tail date string."""
    def __init__(self, columns, tail):
        self.columns = columns
        self.index = _TailIdxStub(tail)


def _write_pkl(cache_results_dir, member, *, declared_max_sma_day,
               has_sma_114=True, with_required_fields=True,
               with_manifest=True, manifest_schema="new"):
    """Write a fake PKL + optional manifest sidecar."""
    d = Path(cache_results_dir)
    d.mkdir(parents=True, exist_ok=True)
    cols = []
    if has_sma_114:
        cols = ["Close", "SMA_30", "SMA_114"]
    else:
        cols = ["Close", "SMA_30"]
    payload = {}
    if with_required_fields:
        payload = {
            "preprocessed_data": _FakeDF(columns=cols),
            "active_pairs": [],
            "daily_top_buy_pairs": {},
            "daily_top_short_pairs": {},
        }
    with open(d / f"{member}_precomputed_results.pkl", "wb") as fh:
        pickle.dump(payload, fh)
    if with_manifest and declared_max_sma_day is not None:
        if manifest_schema == "new":
            man = {"params": {"max_sma_day": declared_max_sma_day,
                              "ticker": member}}
        else:
            man = {"max_sma_day": declared_max_sma_day}
        (d / f"{member}_precomputed_results.pkl.manifest.json").write_text(
            json.dumps(man), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. AST guard: no top-level imports of engine/Dash/yfinance/etc.
# ---------------------------------------------------------------------------


def test_no_toplevel_dangerous_imports():
    forbidden = {
        "trafficflow",
        "signal_engine_cache_refresher",
        "stackbuilder",
        "onepass",
        "impactsearch",
        "spymaster",
        "confluence",
        "multi_timeframe_builder",
        "dash",
        "dash_bootstrap_components",
        "yfinance",
        "plotly",
        "pandas",
    }
    src = RUNNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(RUNNER_PATH))
    bad: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in forbidden:
                    bad.append(f"L{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".", 1)[0]
            if mod in forbidden:
                bad.append(f"L{node.lineno}: from {node.module} import ...")
    assert not bad, f"forbidden top-level imports in trafficflow_runner.py: {bad}"


def test_no_trafficflow_import_during_main(tmp_path, monkeypatch):
    """Ensure dry-run main() does not import trafficflow as a side effect."""
    # Build a minimal valid fixture
    sb_root = tmp_path / "output" / "stackbuilder"
    run_dir = sb_root / "SPY" / "RUN_A"
    _write_selected_build(sb_root, "SPY", selected_run_dir=run_dir)
    _make_fake_leaderboard(run_dir, k_to_members={1: ["AAA[D]"]})

    # Sentinel-block trafficflow imports. Any attempt -> ImportError.
    class _BlockTraffic:
        def find_module(self, name, path=None):
            if name in ("trafficflow", "signal_engine_cache_refresher"):
                return self
            return None
        def load_module(self, name):
            raise ImportError(f"forbidden import in dry-run path: {name}")
    monkeypatch.setattr(sys, "meta_path", [_BlockTraffic()] + sys.meta_path)
    # Also remove any already-imported sentinel just in case
    sys.modules.pop("trafficflow", None)
    sys.modules.pop("signal_engine_cache_refresher", None)

    argv = [
        "--secondaries", "SPY",
        "--stackbuilder-root", str(sb_root),
        "--k", "1",
    ]
    rc, payload, _, _ = _capture_main(argv,
                                       process_conflict_checker=_no_conflict)
    assert rc == 0
    assert payload is not None
    # And no trafficflow import side effect occurred.
    assert "trafficflow" not in sys.modules
    assert "signal_engine_cache_refresher" not in sys.modules


# ---------------------------------------------------------------------------
# 2. Dry-run JSON shape
# ---------------------------------------------------------------------------


def test_dry_run_json_shape(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    run_dir = sb_root / "SPY" / "RUN_A"
    _write_selected_build(sb_root, "SPY", selected_run_dir=run_dir)
    _make_fake_leaderboard(run_dir, k_to_members={1: ["AAA[D]"]})

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    rc, payload, _, raw = _capture_main(argv,
                                         process_conflict_checker=_no_conflict)
    assert rc == 0
    assert payload is not None, f"stdout was not valid JSON: {raw!r}"
    for key in (
        "schema_version", "stage", "run_id", "status", "started_at",
        "ended_at", "elapsed_seconds", "cwd", "git_head", "inputs",
        "effective_config", "process_conflict_result",
        "input_readiness_summary", "per_secondary_results",
        "selected_build_consumed", "benchmark_eligibility",
        "would_refresh_pkls", "would_refresh_prices",
        "artifacts_written", "warnings", "errors", "next_stage_ready",
        "verdict",
    ):
        assert key in payload, f"missing field: {key}"
    assert payload["stage"] == "trafficflow"
    assert payload["status"] == "dry_run"
    assert payload["cwd"] == "<PROJECT_ROOT>"
    assert payload["artifacts_written"] == []
    assert payload["next_stage_ready"] is False
    assert isinstance(payload["per_secondary_results"], list)


# ---------------------------------------------------------------------------
# 3. Missing selected_build.json refusal
# ---------------------------------------------------------------------------


def test_missing_selected_build_refusal(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    sb_root.mkdir(parents=True, exist_ok=True)
    (sb_root / "SPY").mkdir(parents=True, exist_ok=True)
    # No selected_build.json written.
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    rc, payload, stderr_text, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert payload is not None
    secs = payload["per_secondary_results"]
    assert len(secs) == 1
    assert secs[0]["verdict"] == "REFUSED"
    assert secs[0]["reason"] == "selected_build_missing"


# ---------------------------------------------------------------------------
# 4. No latest-by-ctime fallback
# ---------------------------------------------------------------------------


def test_no_latest_directory_fallback(tmp_path):
    """When selected_build.json is absent but run dirs exist, refuse."""
    sb_root = tmp_path / "output" / "stackbuilder"
    spy_dir = sb_root / "SPY"
    spy_dir.mkdir(parents=True, exist_ok=True)
    # Two run dirs, both contain a leaderboard. NO selected_build.json.
    for run_name in ("RUN_OLD", "RUN_NEW"):
        run_dir = spy_dir / run_name
        _make_fake_leaderboard(run_dir, k_to_members={1: ["AAA[D]"]})

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    secs = payload["per_secondary_results"]
    assert secs[0]["verdict"] == "REFUSED"
    assert secs[0]["reason"] == "selected_build_missing"
    assert secs[0]["combo_leaderboard_path"] is None
    # The runner did not surface either RUN_OLD or RUN_NEW.
    assert "RUN_OLD" not in json.dumps(payload)
    assert "RUN_NEW" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# 5. selected_build.json exact consumption
# ---------------------------------------------------------------------------


def test_selected_build_exact_consumption(tmp_path):
    """The chosen leaderboard's members appear in JSON; the decoy's do not.

    Verification by content (members) rather than path because the
    sanitizer redacts the absolute tmp_path. The decoy and chosen
    leaderboards have disjoint member sets so leakage is detectable.
    """
    sb_root = tmp_path / "output" / "stackbuilder"
    spy_dir = sb_root / "SPY"
    chosen = spy_dir / "RUN_CHOSEN"
    decoy = spy_dir / "RUN_DECOY"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"], 2: ["AAA[D]", "BBB[I]"]})
    _make_fake_leaderboard(decoy, k_to_members={1: ["ZZZ[D]"]})
    # Make decoy "newer" by ctime, then point selected_build.json at chosen.
    os.utime(decoy, None)
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen, selected_k=2)

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "2"]
    rc, payload, _, _ = _capture_main(argv,
                                       process_conflict_checker=_no_conflict)
    sec = payload["per_secondary_results"][0]
    # The selected_run_dir field is sanitized (tmp_path is outside cwd),
    # so the runner consumed the chosen build but exposes a placeholder.
    assert sec["selected_run_dir"] in ("<ABSOLUTE_PATH_REDACTED>", None) or \
           isinstance(sec["selected_run_dir"], str)
    # The chosen build's members must appear; the decoy's must not.
    assert "BBB" in json.dumps(sec)
    assert "ZZZ" not in json.dumps(payload)


# ---------------------------------------------------------------------------
# 6. Defaults / effective_config
# ---------------------------------------------------------------------------


def test_effective_config_defaults(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload, _, _ = _capture_main(argv,
                                      process_conflict_checker=_no_conflict)
    ec = payload["effective_config"]
    assert ec["jobs"] == 1
    assert ec["write"] is False
    assert ec["allow_network_fetch"] is False
    assert ec["refresh_missing_pkls"] is False
    assert ec["refresh_stale_prices"] is False
    assert ec["max_sma_day"] == 114
    assert ec["use_selected_build"] is True
    # The stackbuilder_root in tests is an absolute tmp_path outside the
    # project root, so the sanitizer redacts it.
    assert ec["stackbuilder_root"] == "<ABSOLUTE_PATH_REDACTED>"
    assert ec["output_dir"] == "output/trafficflow"
    assert ec["parallel_subsets"] == 0
    assert ec["subset_workers"] == 4
    assert ec["tf_bitmask_fastpath"] == 1


# ---------------------------------------------------------------------------
# 7. --write canonical refusal (Phase C guardrail)
# ---------------------------------------------------------------------------


def test_write_to_default_canonical_output_is_refused(tmp_path):
    """Phase C canonical guardrail: --write without an explicit
    isolated --output-dir defaults to ``output/trafficflow`` and must
    be refused with ``canonical_write_forbidden_in_phase_c`` before any
    compute or trafficflow import."""
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write"]
    rc, payload, stderr_text, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "refused"
    assert "canonical_write_forbidden_in_phase_c" in payload["warnings"]
    assert "canonical_write_forbidden_in_phase_c" in payload["errors"]
    assert payload["artifacts_written"] == []
    assert payload["verdict"] == "REFUSED"
    ec = payload["effective_config"]
    assert ec["canonical_write_blocked"] is True
    assert ec["write_authorized"] is False
    assert ec["output_dir_isolated"] is False
    assert ec["write_mode"] == "refused"
    # Canonical refusal path must not import real trafficflow.
    assert "trafficflow" not in sys.modules


# ---------------------------------------------------------------------------
# 8. Process-conflict refusal
# ---------------------------------------------------------------------------


def test_process_conflict_refuses(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_blocked_conflict)
    assert rc == runner.EXIT_PROCESS_CONFLICT
    assert payload["status"] == "refused"
    assert payload["process_conflict_result"]["status"] == "blocked"
    assert "process_conflict_blocked" in payload["errors"]


# ---------------------------------------------------------------------------
# 9. Repair report without execution
# ---------------------------------------------------------------------------


def test_repair_report_without_execution(tmp_path, monkeypatch):
    """Missing PKL + --refresh-missing-pkls -> would_refresh_pkls, no invoke.

    Missing/stale price + --refresh-stale-prices -> would_refresh_prices,
    no invoke; and trafficflow is not imported.
    """
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)

    # Redirect price-cache + PKL roots into empty tmp dirs so we test
    # MISSING classification rather than any real on-disk artifact.
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))

    # No price cache file written -> MISSING.
    # No PKL written -> MISSING.

    # Fail if subprocess.run is invoked at all.
    invoked = {"count": 0, "calls": []}
    def _no_subproc(*a, **kw):
        invoked["count"] += 1
        invoked["calls"].append((a, kw))
        raise AssertionError("subprocess.run must not be invoked in Phase B")
    monkeypatch.setattr(runner.subprocess, "run", _no_subproc)

    # Override the helper that internally uses subprocess (git_head) by
    # making it harmless. We patch subprocess.run AFTER the runner already
    # imported it, so the patched function is used by _git_head() too -
    # but _git_head() catches Exception, so AssertionError won't bubble up.
    # We still want to assert _no_subproc was the only path used.

    # Also ensure trafficflow is not in sys.modules afterwards.
    sys.modules.pop("trafficflow", None)
    sys.modules.pop("signal_engine_cache_refresher", None)

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--refresh-missing-pkls",
            "--refresh-stale-prices"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert payload is not None
    assert any(p["ticker"] == "AAA" for p in payload["would_refresh_pkls"])
    assert any(p["secondary"] == "SPY" for p in payload["would_refresh_prices"])
    assert "trafficflow" not in sys.modules
    assert "signal_engine_cache_refresher" not in sys.modules
    # Command-shape strings use placeholders, no local paths
    for p in payload["would_refresh_pkls"]:
        assert "<PINNED_INTERPRETER>" in p["command_shape"]
        assert "--max-sma-day 114" in p["command_shape"]
        assert "--write" in p["command_shape"]


# ---------------------------------------------------------------------------
# 10. Max-SMA classification
# ---------------------------------------------------------------------------


def test_classify_pkl_match_explicit_114(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl(cache_dir, "AAA", declared_max_sma_day=114, has_sma_114=True)
    r = runner.classify_pkl("AAA", str(cache_dir))
    assert r["classification"] == "OK"
    assert r["max_sma_class"] == "MATCH"
    assert r["manifest_max_sma_day"] == 114
    assert r["has_SMA_114"] is True
    assert r["declared_inferred"] is False


def test_classify_pkl_mismatch_30(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl(cache_dir, "BBB", declared_max_sma_day=30, has_sma_114=False)
    r = runner.classify_pkl("BBB", str(cache_dir))
    assert r["classification"] == "MISMATCH_MAX_SMA"
    assert r["max_sma_class"] == "MISMATCH_MAX_SMA"


def test_classify_pkl_conflicting_metadata_30_but_sma_114(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl(cache_dir, "CCC", declared_max_sma_day=30, has_sma_114=True)
    r = runner.classify_pkl("CCC", str(cache_dir))
    assert r["classification"] == "CONFLICTING_MAX_SMA"
    assert r["max_sma_class"] == "CONFLICTING_MAX_SMA"


def test_classify_pkl_no_metadata_but_sma_114(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl(cache_dir, "DDD", declared_max_sma_day=None, has_sma_114=True,
               with_manifest=False)
    r = runner.classify_pkl("DDD", str(cache_dir))
    assert r["classification"] == "UNKNOWN_USABLE"
    assert r["max_sma_class"] == "MATCH"
    assert r["declared_inferred"] is True


def test_classify_pkl_legacy_top_level_manifest(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl(cache_dir, "EEE", declared_max_sma_day=114, has_sma_114=True,
               manifest_schema="old")
    r = runner.classify_pkl("EEE", str(cache_dir))
    assert r["classification"] == "OK"
    assert r["manifest_max_sma_day"] == 114


def test_classify_pkl_missing(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    cache_dir.mkdir(parents=True, exist_ok=True)
    r = runner.classify_pkl("ZZZ", str(cache_dir))
    assert r["classification"] == "MISSING"


# ---------------------------------------------------------------------------
# 11. JSON cwd is placeholder, no local paths
# ---------------------------------------------------------------------------


def test_json_cwd_is_placeholder(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload, _, _ = _capture_main(argv,
                                      process_conflict_checker=_no_conflict)
    assert payload["cwd"] == "<PROJECT_ROOT>"


# ---------------------------------------------------------------------------
# 12. K range parsing
# ---------------------------------------------------------------------------


def test_k_range_simple_range():
    args = runner.parse_args(["--secondaries", "SPY", "--k-range", "1-3"])
    sel = runner.parse_k_selection(args)
    assert sel["mode"] == "list"
    assert sel["ks"] == [1, 2, 3]


def test_k_range_comma_list():
    args = runner.parse_args(["--secondaries", "SPY", "--k-range", "1,2,4,6"])
    sel = runner.parse_k_selection(args)
    assert sel["ks"] == [1, 2, 4, 6]


def test_k_single():
    args = runner.parse_args(["--secondaries", "SPY", "--k", "3"])
    sel = runner.parse_k_selection(args)
    assert sel["mode"] == "single"
    assert sel["ks"] == [3]


# ---------------------------------------------------------------------------
# 13. Explicit-build refuses with >1 secondary
# ---------------------------------------------------------------------------


def test_explicit_build_requires_single_secondary(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    argv = ["--secondaries", "SPY,AAPL",
            "--stackbuilder-root", str(sb_root),
            "--explicit-build", str(chosen),
            "--k", "1"]
    rc, payload, _, _ = _capture_main(argv,
                                       process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "refused"
    assert "explicit_build_requires_single_secondary" in payload["errors"]


# ---------------------------------------------------------------------------
# 14. Cell eligibility helper
# ---------------------------------------------------------------------------


def test_cell_eligibility_all_ok():
    assert runner.cell_eligibility("OK", ["OK", "OK"]) == "ELIGIBLE"


def test_cell_eligibility_data_gated_when_price_missing():
    assert runner.cell_eligibility("MISSING", ["OK"]) == "DATA-GATED"


def test_cell_eligibility_pkl_gated():
    assert runner.cell_eligibility("OK", ["MISSING"]) == "PKL-GATED"


def test_cell_eligibility_max_sma_gated():
    assert runner.cell_eligibility("OK", ["MISMATCH_MAX_SMA"]) == "MAX-SMA-GATED"


def test_cell_eligibility_with_notes_when_unknown_usable():
    assert runner.cell_eligibility("OK", ["UNKNOWN_USABLE"]) == "ELIGIBLE_WITH_NOTES"


# ---------------------------------------------------------------------------
# 15. Argparse refuses missing secondaries
# ---------------------------------------------------------------------------


def test_missing_secondaries_refused(tmp_path):
    argv = ["--stackbuilder-root", str(tmp_path)]
    rc, payload, _, _ = _capture_main(argv,
                                       process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "refused"
    assert "secondaries_required" in payload["errors"]


# ===========================================================================
# Amendment Part 1 - JSON sanitization / path redaction
# ===========================================================================


def test_sanitizer_helpers_redact_drive_letter_paths():
    """Drive-letter paths under known private tokens collapse to the
    redaction placeholder; constructed in pieces to keep this test file
    itself free of any real local path string."""
    fake = chr(90) + ":" + chr(92) + "PRIVATE" + chr(92) + "trafficflow.py"
    assert runner.is_absolute_path_like(fake)
    assert runner.path_for_output(fake) == "<ABSOLUTE_PATH_REDACTED>"


def test_sanitizer_keeps_relative_paths_normalized():
    assert runner.path_for_output("output/trafficflow/_progress") == \
        "output/trafficflow/_progress"
    # Backslash-relative -> POSIX-style.
    rel = "output" + chr(92) + "trafficflow"
    assert runner.path_for_output(rel) == "output/trafficflow"


def test_sanitizer_resolves_paths_under_project_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "subdir").mkdir()
    out = runner.path_for_output(str(tmp_path / "subdir"))
    assert out == "subdir"


def test_dry_run_json_does_not_leak_absolute_tmp_path(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload, _, raw_stdout = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    blob = json.dumps(payload)
    assert str(tmp_path) not in blob
    assert str(sb_root) not in blob
    assert str(chosen) not in blob


def test_dry_run_json_does_not_leak_explicit_build_absolute_path(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--explicit-build", str(chosen),
            "--k", "1"]
    _, payload, _, _ = _capture_main(argv,
                                      process_conflict_checker=_no_conflict)
    blob = json.dumps(payload)
    assert str(chosen) not in blob


def test_process_conflict_json_redacts_raw_cmdline(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)

    # Build a fake cmdline containing a synthetic private absolute path.
    fake_cmdline = (
        chr(90) + ":" + chr(92) + "PRIVATE" + chr(92) + "x" + chr(92)
        + "trafficflow.py --secret"
    )

    def _conflict_with_path(write_requested=False):
        return {
            "status": "blocked",
            "conflicts": [
                {"pid": 12345, "cmdline": fake_cmdline,
                 "matched_pattern": "trafficflow.py"},
            ],
            "queried_via": "fake",
            "error": None,
        }

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_conflict_with_path)
    blob = json.dumps(payload)
    assert fake_cmdline not in blob
    assert "PRIVATE" not in blob
    pc = payload["process_conflict_result"]
    assert pc["conflicts"][0]["command_line_redacted"] is True
    assert pc["conflicts"][0]["matched_pattern"] == "trafficflow.py"
    assert "cmdline" not in pc["conflicts"][0]


def test_would_refresh_pkls_command_shape_uses_pinned_interpreter_placeholder(tmp_path, monkeypatch):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--refresh-missing-pkls"]
    _, payload, _, _ = _capture_main(argv,
                                      process_conflict_checker=_no_conflict)
    assert payload["would_refresh_pkls"], "expected at least one would_refresh entry"
    for entry in payload["would_refresh_pkls"]:
        assert "<PINNED_INTERPRETER>" in entry["command_shape"]
        # No leaking of the real interpreter or absolute paths.
        # Construct the forbidden substring from pieces so the test
        # file itself stays free of any literal interpreter token.
        forbidden_interpreter = "python" + chr(46) + "exe"
        assert forbidden_interpreter not in entry["command_shape"]


# ===========================================================================
# Amendment Part 2 - PKL STALE classification
# ===========================================================================


def _write_pkl_with_tail(cache_results_dir, member, *, tail_date,
                          declared_max_sma_day=114, has_sma_114=True):
    """Write a fake PKL whose preprocessed_data.index.max() returns
    ``tail_date``. Uses module-scope picklable stubs.
    """
    d = Path(cache_results_dir)
    d.mkdir(parents=True, exist_ok=True)
    cols = ["Close", "SMA_30"] + (["SMA_114"] if has_sma_114 else [])
    payload = {
        "preprocessed_data": _TailDFStub(columns=cols, tail=tail_date),
        "active_pairs": [],
        "daily_top_buy_pairs": {},
        "daily_top_short_pairs": {},
    }
    with open(d / f"{member}_precomputed_results.pkl", "wb") as fh:
        pickle.dump(payload, fh)
    man = {"params": {"max_sma_day": declared_max_sma_day, "ticker": member}}
    (d / f"{member}_precomputed_results.pkl.manifest.json").write_text(
        json.dumps(man), encoding="utf-8")


def test_classify_pkl_stale_when_tail_before_benchmark(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl_with_tail(cache_dir, "STALE1", tail_date="2026-05-10")
    r = runner.classify_pkl("STALE1", str(cache_dir),
                            benchmark_as_of_date="2026-05-22")
    assert r["classification"] == "STALE"
    assert r["data_tail_date"] == "2026-05-10"
    assert r["benchmark_as_of_date"] == "2026-05-22"
    assert r["freshness_class"] == "STALE"


def test_classify_pkl_ok_when_tail_equals_benchmark(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl_with_tail(cache_dir, "FRESH1", tail_date="2026-05-22")
    r = runner.classify_pkl("FRESH1", str(cache_dir),
                            benchmark_as_of_date="2026-05-22")
    assert r["classification"] == "OK"
    assert r["freshness_class"] == "OK"


def test_classify_pkl_ok_when_tail_after_benchmark(tmp_path):
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl_with_tail(cache_dir, "FRESH2", tail_date="2026-05-23")
    r = runner.classify_pkl("FRESH2", str(cache_dir),
                            benchmark_as_of_date="2026-05-22")
    assert r["classification"] == "OK"


def test_classify_pkl_no_tail_no_stale(tmp_path):
    """When tail cannot be determined, STALE must NOT fire purely on
    that basis. UNKNOWN_USABLE remains the inference path."""
    cache_dir = tmp_path / "cache" / "results"
    # No manifest, no tail-bearing dataframe -> declared_inferred path.
    _write_pkl(cache_dir, "NOTAIL", declared_max_sma_day=None,
               has_sma_114=True, with_manifest=False)
    r = runner.classify_pkl("NOTAIL", str(cache_dir),
                            benchmark_as_of_date="2026-05-22")
    assert r["classification"] in ("UNKNOWN_USABLE", "OK")
    assert r["classification"] != "STALE"


def test_max_sma_mismatch_outranks_stale(tmp_path):
    """A PKL with stale tail AND max_sma_day=30 must remain
    MISMATCH_MAX_SMA, not STALE."""
    cache_dir = tmp_path / "cache" / "results"
    _write_pkl_with_tail(cache_dir, "MIXED",
                         tail_date="2026-05-10",
                         declared_max_sma_day=30,
                         has_sma_114=False)
    r = runner.classify_pkl("MIXED", str(cache_dir),
                            benchmark_as_of_date="2026-05-22")
    assert r["classification"] == "MISMATCH_MAX_SMA"


def test_stale_pkl_gates_cell(tmp_path, monkeypatch):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["STALEM"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    _write_pkl_with_tail(tmp_path / "cache" / "results", "STALEM",
                         tail_date=_stale_tail_date())
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload, _, _ = _capture_main(argv,
                                      process_conflict_checker=_no_conflict)
    sec = payload["per_secondary_results"][0]
    assert sec["k_eligibility"]["K1"] == "STALE-GATED"


def test_refresh_missing_pkls_includes_stale(tmp_path, monkeypatch):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["STALEM"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    _write_pkl_with_tail(tmp_path / "cache" / "results", "STALEM",
                         tail_date=_stale_tail_date())
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--refresh-missing-pkls"]
    _, payload, _, _ = _capture_main(argv,
                                      process_conflict_checker=_no_conflict)
    tickers = [e["ticker"] for e in payload["would_refresh_pkls"]]
    classes = [e["classification"] for e in payload["would_refresh_pkls"]]
    assert "STALEM" in tickers
    assert "STALE" in classes


# ===========================================================================
# Amendment Part 3 - selected_build.json schema validation
# ===========================================================================


def test_selected_build_missing_schema_version_refuses(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    sb_dir = sb_root / "SPY"
    sb_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        # schema_version intentionally omitted
        "secondary": "SPY",
        "selected_k": 1,
        "selection_policy": "v2",
        "operator_pinned": False,
        "selected_run_dir": str(chosen),
    }
    (sb_dir / "selected_build.json").write_text(json.dumps(payload),
                                                 encoding="utf-8")
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload_out, _, _ = _capture_main(argv,
                                          process_conflict_checker=_no_conflict)
    sec = payload_out["per_secondary_results"][0]
    assert sec["verdict"] == "REFUSED"
    assert sec["reason"] == "selected_build_missing_required_fields"


def test_selected_build_missing_selected_k_refuses(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    sb_dir = sb_root / "SPY"
    sb_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "secondary": "SPY",
        # selected_k intentionally omitted
        "selection_policy": "v2",
        "operator_pinned": False,
        "selected_run_dir": str(chosen),
    }
    (sb_dir / "selected_build.json").write_text(json.dumps(payload),
                                                 encoding="utf-8")
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload_out, _, _ = _capture_main(argv,
                                          process_conflict_checker=_no_conflict)
    sec = payload_out["per_secondary_results"][0]
    assert sec["verdict"] == "REFUSED"
    assert sec["reason"] in (
        "selected_build_missing_required_fields",
        "selected_build_invalid_selected_k",
    )


def test_selected_build_secondary_mismatch_refuses(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    # The selected_build.json declares "AAPL" but the file is under SPY/.
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen,
                          payload_extras={"secondary": "AAPL"})
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload_out, _, _ = _capture_main(argv,
                                          process_conflict_checker=_no_conflict)
    sec = payload_out["per_secondary_results"][0]
    assert sec["verdict"] == "REFUSED"
    assert sec["reason"] == "selected_build_secondary_mismatch"


def test_selected_build_invalid_selected_k_refuses(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen,
                          payload_extras={"selected_k": 0})
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload_out, _, _ = _capture_main(argv,
                                          process_conflict_checker=_no_conflict)
    sec = payload_out["per_secondary_results"][0]
    assert sec["verdict"] == "REFUSED"
    assert sec["reason"] == "selected_build_invalid_selected_k"


def test_selected_build_valid_payload_passes(tmp_path, monkeypatch):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    rc, payload_out, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    sec = payload_out["per_secondary_results"][0]
    # Valid selected_build.json + missing PKL/price -> still passes
    # schema gate; verdict surfaces a readiness gate, NOT REFUSED.
    assert sec["verdict"] != "REFUSED"
    assert sec["reason"] is None or "selected_build" not in sec["reason"]


# ===========================================================================
# Phase C - isolated-output --write support
# ===========================================================================


def _eligible_fixture(tmp_path, monkeypatch, *, secondary="SPY",
                      member="AAA", k=1):
    """Build a fully-eligible single-cell fixture in tmp_path.

    Returns (sb_root_path, output_dir_path).
    """
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / secondary / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={k: [f"{member}[D]"]})
    _write_selected_build(sb_root, secondary, selected_run_dir=chosen,
                          selected_k=k)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", secondary,
                       tail_date=_fresh_tail_date())
    _write_pkl(tmp_path / "cache" / "results", member,
               declared_max_sma_day=114, has_sma_114=True)
    out_dir = tmp_path / "smoke_out"
    return sb_root, out_dir


def test_phase_c_is_isolated_output_dir_helper():
    # Canonical and descendants are not isolated.
    assert runner.is_isolated_output_dir("output/trafficflow") is False
    assert runner.is_isolated_output_dir("output/trafficflow/foo") is False
    # Relative paths outside the canonical root are isolated.
    assert runner.is_isolated_output_dir("output/trafficflow_smoke") is True
    assert runner.is_isolated_output_dir("output/something_else") is True
    # Logs are isolated.
    assert runner.is_isolated_output_dir("logs/trafficflow_phase_c_smoke") is True


def test_phase_c_isolated_write_succeeds_with_mock_compute(tmp_path, monkeypatch):
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)

    captured = {"calls": []}
    def _fake_compute(sec, k, *, run_fence=None, missing_map=None, combo_leaderboard_path=None):
        captured["calls"].append((sec, k))
        return [
            {"Ticker": sec, "K": k, "Members": "AAA",
             "Trigs": 10, "Wins": 6, "Losses": 4, "Avg %": 1.25},
        ]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    assert rc == runner.EXIT_OK
    assert payload["status"] == "ok"
    assert payload["effective_config"]["write_authorized"] is True
    assert payload["effective_config"]["output_dir_isolated"] is True
    assert payload["effective_config"]["write_mode"] == "isolated"
    # Compute was called exactly once for the eligible cell.
    assert captured["calls"] == [("SPY", 1)]
    # Artifact files exist on disk under the isolated output dir.
    assert (out_dir / "SPY" / "board_rows_k=1.json").exists()
    assert (out_dir / "SPY" / "board_rows_k=1.csv").exists()
    assert (out_dir / "run_manifest.json").exists()
    assert (out_dir / "run.stdout.json").exists()
    # No canonical output/trafficflow writes occurred.
    canonical = Path("output") / "trafficflow"
    assert not (canonical.exists() and any(canonical.iterdir())) \
        or all("smoke_out" not in str(p) for p in canonical.iterdir())


def test_phase_c_isolated_write_skips_ineligible_cell(tmp_path, monkeypatch):
    """Cells that are STALE-GATED / PKL-GATED / DATA-GATED must be
    skipped without invoking compute."""
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    # Two K cells: K=1 eligible, K=2 PKL-GATED (member BBB has no PKL).
    _make_fake_leaderboard(chosen, k_to_members={
        1: ["AAA[D]"],
        2: ["BBB[D]"],
    })
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    _write_pkl(tmp_path / "cache" / "results", "AAA",
               declared_max_sma_day=114, has_sma_114=True)
    # BBB intentionally NOT written -> K=2 cell is PKL-GATED.
    out_dir = tmp_path / "smoke_out"

    captured = {"calls": []}
    def _fake_compute(sec, k, *, run_fence=None, missing_map=None, combo_leaderboard_path=None):
        captured["calls"].append((sec, k))
        return [{"K": k, "Members": "AAA"}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    # Compute fired only for K=1, not K=2.
    assert captured["calls"] == [("SPY", 1)]
    cells = payload.get("per_cell_summary") or []
    by_k = {c["k"]: c for c in cells}
    assert by_k[1]["status"] == "ok"
    assert by_k[2]["status"] == "skipped"
    assert "non_eligible:" in by_k[2]["skip_reason"]
    # K=1 wrote, K=2 did not.
    assert (out_dir / "SPY" / "board_rows_k=1.json").exists()
    assert not (out_dir / "SPY" / "board_rows_k=2.json").exists()


def test_phase_c_isolated_write_emits_sanitized_paths(tmp_path, monkeypatch):
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)

    def _fake_compute(sec, k, *, run_fence=None, missing_map=None, combo_leaderboard_path=None):
        return [{"K": k, "Members": "AAA"}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    # On-disk artifacts must not contain the raw tmp_path string.
    raw_stdout = (out_dir / "run.stdout.json").read_text(encoding="utf-8")
    raw_manifest = (out_dir / "run_manifest.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in raw_stdout
    assert str(tmp_path) not in raw_manifest
    # No drive-letter pattern in either.
    drive_pat = re.compile(r"[A-Z]:[\\/]")
    assert not drive_pat.search(raw_stdout)
    assert not drive_pat.search(raw_manifest)
    # Payload artifacts list is sanitized.
    for art in payload["artifacts_written"]:
        assert str(tmp_path) not in art
        assert not drive_pat.search(art)


def test_phase_c_refresh_flags_remain_report_only_with_write(tmp_path, monkeypatch):
    """Even with --write authorized for isolated output, the refresh
    flags must remain report-only: would_refresh_pkls populated,
    signal_engine_cache_refresher.py never invoked, the PKL-gated cell
    skipped (compute never called for it)."""
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["MISSINGM[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    # MISSINGM's PKL intentionally not written.
    invoked = {"count": 0}
    def _no_subproc(*a, **kw):
        invoked["count"] += 1
        raise AssertionError("subprocess.run must not be invoked in Phase C")
    monkeypatch.setattr(runner.subprocess, "run", _no_subproc)

    captured = {"calls": []}
    def _fake_compute(sec, k, *, run_fence=None, missing_map=None, combo_leaderboard_path=None):
        captured["calls"].append((sec, k))
        return [{"K": k}]

    out_dir = tmp_path / "smoke_out"
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir),
            "--refresh-missing-pkls"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    assert any(e["ticker"] == "MISSINGM"
               for e in payload["would_refresh_pkls"])
    # Compute was NOT invoked (cell is PKL-GATED).
    assert captured["calls"] == []
    # No cache writes happened.
    assert not (tmp_path / "cache" / "results" /
                "MISSINGM_precomputed_results.pkl").exists()


def test_phase_c_compute_exception_handled_gracefully(tmp_path, monkeypatch):
    """Compute raises for one cell; another succeeds; run status is
    partial; errored cell recorded with sanitized exception detail."""
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={
        1: ["AAA[D]"],
        2: ["AAA[D]", "BBB[D]"],
    })
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen,
                          selected_k=2)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    _write_pkl(tmp_path / "cache" / "results", "AAA",
               declared_max_sma_day=114, has_sma_114=True)
    _write_pkl(tmp_path / "cache" / "results", "BBB",
               declared_max_sma_day=114, has_sma_114=True)
    out_dir = tmp_path / "smoke_out"

    def _fake_compute(sec, k, *, run_fence=None, missing_map=None, combo_leaderboard_path=None):
        if k == 2:
            raise RuntimeError("synthetic compute failure for K=2")
        return [{"K": k, "Members": "AAA"}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    # Partial outcome: 1 cell wrote, 1 cell errored.
    assert payload["status"] == "partial"
    by_k = {c["k"]: c for c in payload["per_cell_summary"]}
    assert by_k[1]["status"] == "ok"
    assert by_k[2]["status"] == "error"
    assert by_k[2]["error_class"] == "RuntimeError"
    assert "synthetic compute failure" in by_k[2]["error_message"]
    # K=1 wrote artifacts; K=2 did not.
    assert (out_dir / "SPY" / "board_rows_k=1.json").exists()
    assert not (out_dir / "SPY" / "board_rows_k=2.json").exists()
    summary = payload["write_summary"]
    assert summary["cells_written"] == 1
    assert summary["cells_errored"] == 1


def test_phase_c_no_tmp_files_remain_after_isolated_write(tmp_path, monkeypatch):
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)

    def _fake_compute(sec, k, *, run_fence=None, missing_map=None, combo_leaderboard_path=None):
        return [{"K": k}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    # Walk the output dir; no .tmp files may remain.
    tmps = [p for p in out_dir.rglob("*.tmp")]
    assert tmps == [], f"unexpected .tmp remnants: {tmps}"


def test_phase_c_lazy_compute_loader_not_invoked_in_dry_run(tmp_path, monkeypatch):
    """Dry-run path must not resolve the compute callable. With the
    sys.meta_path sentinel blocking trafficflow import, a dry-run still
    succeeds. The compute_callable kwarg is irrelevant in dry-run."""
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    sys.modules.pop("signal_engine_cache_refresher", None)

    class _BlockTraffic:
        def find_module(self, name, path=None):
            if name in ("trafficflow", "signal_engine_cache_refresher"):
                return self
            return None
        def load_module(self, name):
            raise ImportError(
                f"forbidden import in dry-run path: {name}")
    monkeypatch.setattr(sys, "meta_path", [_BlockTraffic()] + sys.meta_path)

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_OK
    assert payload["status"] == "dry_run"
    assert payload["effective_config"]["write_mode"] == "dry_run"
    assert "trafficflow" not in sys.modules


def test_phase_c_effective_config_dry_run_fields():
    args = runner.parse_args(["--secondaries", "SPY"])
    ec = runner.build_effective_config(args)
    assert ec["write_authorized"] is False
    assert ec["canonical_write_blocked"] is False
    assert ec["write_mode"] == "dry_run"


def test_phase_c_effective_config_canonical_refused_fields():
    args = runner.parse_args(["--secondaries", "SPY", "--write"])
    ec = runner.build_effective_config(args)
    assert ec["write_authorized"] is False
    assert ec["canonical_write_blocked"] is True
    assert ec["output_dir_isolated"] is False
    assert ec["write_mode"] == "refused"


def test_phase_c_effective_config_isolated_authorized_fields(tmp_path):
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--write",
        "--output-dir", str(tmp_path / "smoke_out"),
    ])
    ec = runner.build_effective_config(args)
    assert ec["write_authorized"] is True
    assert ec["canonical_write_blocked"] is False
    assert ec["output_dir_isolated"] is True
    assert ec["write_mode"] == "isolated"


def test_phase_c_no_writes_when_all_cells_ineligible(tmp_path, monkeypatch):
    """All requested cells are PKL-GATED (missing members). Compute
    must not be called; no board_rows files; status must clearly
    report zero cells_written."""
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["GONE[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    # No PKL for GONE -> K=1 cell is PKL-GATED.
    out_dir = tmp_path / "smoke_out"

    captured = {"calls": []}
    def _fake_compute(sec, k, *, run_fence=None, missing_map=None, combo_leaderboard_path=None):
        captured["calls"].append((sec, k))
        return [{"K": k}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    assert captured["calls"] == []
    summary = payload["write_summary"]
    assert summary["cells_written"] == 0
    assert payload["status"] == "failed"
    assert not (out_dir / "SPY" / "board_rows_k=1.json").exists()


# ===========================================================================
# Phase C amendment - selected-build enforcement, process-conflict fail-
# closed, complete artifact list, docstring/contract reaffirmations.
# ===========================================================================


def test_phase_c_compute_callable_receives_combo_leaderboard_path(
        tmp_path, monkeypatch):
    """The runner threads the preflight-resolved combo_leaderboard_path
    into the compute callable."""
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)
    seen: dict = {}

    def _fake_compute(sec, k, *, run_fence=None, missing_map=None,
                       combo_leaderboard_path=None):
        seen["combo_leaderboard_path"] = combo_leaderboard_path
        return [{"K": k}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    assert rc == runner.EXIT_OK
    # The compute callable receives the RAW (unsanitized) leaderboard
    # path so the engine can actually open the file. The sanitized
    # equivalent in payload["per_secondary_results"] is redacted when
    # the tmp_path is outside the project root, so we can only assert
    # structural properties on what compute saw.
    assert seen["combo_leaderboard_path"], "compute did not receive the path"
    assert "combo_leaderboard" in str(seen["combo_leaderboard_path"])
    # The resolved file must actually exist on disk where the runner
    # said it did - that proves the path is the preflight-resolved
    # path, not None or a decoy.
    assert Path(seen["combo_leaderboard_path"]).exists()


def test_phase_c_selected_build_enforced_during_default_compute(
        tmp_path, monkeypatch):
    """Inject a fake trafficflow into sys.modules and verify the
    _default_compute_loader wrapper pins _find_latest_combo_table to
    the selected combo_leaderboard path during build_board_rows and
    restores the original finder after the call."""
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)

    decoy_path = tmp_path / "decoy" / "combo_leaderboard.xlsx"
    decoy_path.parent.mkdir(parents=True, exist_ok=True)
    decoy_path.write_text("decoy", encoding="utf-8")

    seen: dict = {"finder_calls": []}

    fake_tf = SimpleNamespace()
    def _original_finder(sec):
        seen["finder_calls"].append(("ORIGINAL", sec))
        return Path(decoy_path)
    fake_tf._find_latest_combo_table = _original_finder

    def _fake_build_board_rows(sec, k, *, run_fence=None, missing_map=None):
        # The engine would normally call _find_latest_combo_table here.
        # The wrapper must have pinned this attribute to a function
        # returning the SELECTED path BEFORE this call.
        path_via_finder = fake_tf._find_latest_combo_table(sec)
        seen["finder_used_by_build"] = path_via_finder
        return [{"K": k, "Members": "AAA"}]
    fake_tf.build_board_rows = _fake_build_board_rows

    sys.modules["trafficflow"] = fake_tf
    try:
        argv = ["--secondaries", "SPY",
                "--stackbuilder-root", str(sb_root),
                "--k", "1",
                "--write",
                "--output-dir", str(out_dir)]
        # NO compute_callable -> _default_compute_loader is used.
        rc, payload, _, _ = _capture_main(
            argv, process_conflict_checker=_no_conflict)
    finally:
        del sys.modules["trafficflow"]

    assert rc == runner.EXIT_OK
    assert payload["status"] == "ok"
    pinned_used = seen.get("finder_used_by_build")
    assert pinned_used is not None
    assert "combo_leaderboard" in str(pinned_used)
    assert "decoy" not in str(pinned_used)
    # Original finder restored AFTER compute - verifiable by calling
    # it now and observing the ORIGINAL decoy-returning behavior.
    restored_result = fake_tf._find_latest_combo_table("SPY")
    assert "decoy" in str(restored_result)
    assert ("ORIGINAL", "SPY") in seen["finder_calls"]


def test_phase_c_write_refuses_on_process_conflict_enumeration_error(
        tmp_path, monkeypatch):
    """Write mode fails closed when process-conflict enumeration
    fails (status='error')."""
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)

    def _conflict_error(write_requested=False):
        return {
            "status": "error",
            "conflicts": [],
            "queried_via": "fake",
            "error": "enumeration_unavailable",
        }

    captured = {"calls": 0}
    def _fake_compute(sec, k, *, run_fence=None, missing_map=None,
                       combo_leaderboard_path=None):
        captured["calls"] += 1
        return [{"K": k}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_conflict_error,
        compute_callable=_fake_compute)
    assert rc == runner.EXIT_PROCESS_CONFLICT
    assert payload["status"] == "refused"
    assert "process_conflict_enumeration_unavailable" in payload["errors"]
    assert captured["calls"] == 0
    assert not (out_dir / "SPY" / "board_rows_k=1.json").exists()
    assert not (out_dir / "run_manifest.json").exists()
    assert not (out_dir / "run.stdout.json").exists()


def test_phase_c_on_disk_artifact_lists_are_complete(tmp_path, monkeypatch):
    """run_manifest.json and run.stdout.json on disk both enumerate
    the complete artifact list including themselves and the per-cell
    board-row JSON + CSV.

    The output dir is placed UNDER the project root so the sanitizer
    converts artifact paths to repo-relative POSIX strings rather than
    redacting them to ``<ABSOLUTE_PATH_REDACTED>``. The repo-relative
    rendering is what the test asserts on by substring.
    """
    # Build the stackbuilder fixture under tmp_path but emit artifacts
    # to a project-root-relative directory so the sanitizer can map
    # them back to readable repo-relative POSIX strings. The test
    # cleans up after itself.
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    _write_pkl(tmp_path / "cache" / "results", "AAA",
               declared_max_sma_day=114, has_sma_114=True)
    out_dir = PROJECT_ROOT / f"logs/_pytest_phase_c_artifact_list_{os.getpid()}"
    # Defensive cleanup if a prior aborted run left this dir behind.
    if out_dir.exists():
        import shutil as _sh
        _sh.rmtree(out_dir, ignore_errors=True)

    def _fake_compute(sec, k, *, run_fence=None, missing_map=None,
                       combo_leaderboard_path=None):
        return [{"K": k}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir)]
    try:
        rc, payload, _, _ = _capture_main(
            argv, process_conflict_checker=_no_conflict,
            compute_callable=_fake_compute)
        assert rc == runner.EXIT_OK

        manifest = json.loads(
            (out_dir / "run_manifest.json").read_text(encoding="utf-8"))
        stdout_file = json.loads(
            (out_dir / "run.stdout.json").read_text(encoding="utf-8"))

        def _has(art_list, needle):
            return any(needle in str(a) for a in art_list)

        for art_list in (manifest["artifacts_written"],
                         stdout_file["artifacts_written"]):
            assert _has(art_list, "board_rows_k=1.json"), art_list
            assert _has(art_list, "board_rows_k=1.csv"), art_list
            assert _has(art_list, "run_manifest.json"), art_list
            assert _has(art_list, "run.stdout.json"), art_list
        # Both on-disk files reference the same final list.
        assert sorted(manifest["artifacts_written"]) == \
            sorted(stdout_file["artifacts_written"])
        # write_summary.artifacts_written_count matches the final count.
        assert (manifest["write_summary"]["artifacts_written_count"]
                == len(manifest["artifacts_written"]))
        assert (stdout_file["write_summary"]["artifacts_written_count"]
                == len(stdout_file["artifacts_written"]))
    finally:
        if out_dir.exists():
            import shutil as _sh
            _sh.rmtree(out_dir, ignore_errors=True)


def test_phase_c_manifest_uses_actual_selected_build_path_not_default_root(
        tmp_path, monkeypatch):
    """Provenance fix: ``run_manifest.json`` must cite the actual
    ``selected_build.json`` that preflight consumed (under the
    operator-supplied ``--stackbuilder-root``), not a recomputed path
    rooted at ``DEFAULT_STACKBUILDER_ROOT``.

    Layout:
      * ``custom_stackbuilder/SPY/selected_build.json`` (the one the
        runner is told to use via ``--stackbuilder-root``).
      * ``output/stackbuilder/SPY/selected_build.json`` (DECOY at the
        default root with different contents/SHA). If the bug were
        present, the manifest would cite this decoy's SHA.

    The test runs under ``monkeypatch.chdir(tmp_path)`` so the
    sanitizer can map both paths to readable repo-relative POSIX
    strings.
    """
    import hashlib

    monkeypatch.chdir(tmp_path)

    # Custom (real) StackBuilder root the operator points at.
    custom_root = tmp_path / "custom_stackbuilder"
    custom_chosen = custom_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(custom_chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(custom_root, "SPY", selected_run_dir=custom_chosen,
                          payload_extras={"selected_run_id": "CUSTOM-1"})
    custom_sb = custom_root / "SPY" / "selected_build.json"

    # Decoy default root with DIFFERENT contents so the SHA differs.
    decoy_root = tmp_path / "output" / "stackbuilder"
    decoy_chosen = decoy_root / "SPY" / "RUN_DECOY"
    _make_fake_leaderboard(decoy_chosen, k_to_members={1: ["DECOY[D]"]})
    _write_selected_build(decoy_root, "SPY", selected_run_dir=decoy_chosen,
                          payload_extras={"selected_run_id": "DECOY-1"})
    decoy_sb = decoy_root / "SPY" / "selected_build.json"

    def _sha(p):
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()

    custom_sha = _sha(custom_sb)
    decoy_sha = _sha(decoy_sb)
    assert custom_sha != decoy_sha, (
        "Test fixture must produce distinct SHAs for the custom "
        "vs decoy selected_build.json; otherwise the regression "
        "guard cannot fire."
    )

    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    _write_pkl(tmp_path / "cache" / "results", "AAA",
               declared_max_sma_day=114, has_sma_114=True)

    out_dir = tmp_path / "smoke_out"

    def _fake_compute(sec, k, *, run_fence=None, missing_map=None,
                       combo_leaderboard_path=None):
        return [{"K": k}]

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", "custom_stackbuilder",
            "--k", "1",
            "--write",
            "--output-dir", str(out_dir)]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=_fake_compute)
    assert rc == runner.EXIT_OK

    manifest = json.loads(
        (out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    refs = manifest["canonical_artifacts_referenced"]
    assert len(refs) == 1
    ref = refs[0]
    assert ref["secondary"] == "SPY"
    # The provenance entry must cite the CUSTOM selected_build.json
    # SHA and a path that includes the custom_stackbuilder root, not
    # the default output/stackbuilder root.
    assert ref["selected_build_sha256"] == custom_sha
    assert ref["selected_build_sha256"] != decoy_sha
    sb_path_str = str(ref["selected_build_path"])
    assert "custom_stackbuilder/SPY/selected_build.json" in sb_path_str, (
        f"manifest cites unexpected selected_build path: "
        f"{sb_path_str!r}"
    )
    assert "output/stackbuilder" not in sb_path_str, (
        f"manifest leaked the default stackbuilder root: "
        f"{sb_path_str!r}"
    )
    assert ref["explicit_build_override"] is False

    # The manifest must not contain the raw absolute tmp_path string.
    raw_manifest = (out_dir / "run_manifest.json").read_text(
        encoding="utf-8")
    assert str(tmp_path) not in raw_manifest


# ===========================================================================
# Engine network/price-cache surface block (PR #307 follow-up amendment)
#
# These tests inject a fake ``trafficflow`` module into ``sys.modules`` so
# the lazy ``_default_compute_loader`` resolves to the fake. The fake
# captures call counts on the engine-internal network / price-cache
# functions the runner is supposed to pin when ``--allow-network-fetch``
# is not passed. No real trafficflow import.
# ===========================================================================


def _make_engine_surface_fake(tmp_path, *, decoy_path=None):
    """Build a SimpleNamespace masquerading as the trafficflow module
    with original implementations for the network / price-cache
    surface and ``build_board_rows`` that exercises them through the
    module's current attributes (so wrapper-applied pins are observed).
    """
    import pandas as pd
    state = {
        "needs_refresh_calls": 0,
        "fetch_calls": 0,
        "write_cache_calls": 0,
        "persist_cache_calls": 0,
        "finder_calls": [],
        "compute_calls": [],
        "compute_saw": {},
    }
    fake_cache_target = tmp_path / "fake_price_cache" / "FAKE.csv"
    state["fake_cache_target"] = str(fake_cache_target)

    def _orig_needs_refresh(sym, df, cache_path):
        state["needs_refresh_calls"] += 1
        return True  # original would say "yes, refresh"

    def _orig_fetch_secondary_from_yf(secondary):
        state["fetch_calls"] += 1
        # Would fetch real data; for the test we return a deterministic
        # non-empty DataFrame so the caller proceeds to write if not
        # blocked.
        return pd.DataFrame({"Close": [1.0, 2.0, 3.0]})

    def _orig_write_cache_file(path, df):
        state["write_cache_calls"] += 1
        # If reached, actually write to the tracked tmp_path target
        # so the test can prove the write did NOT happen when blocked.
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("WROTE", encoding="utf-8")

    def _orig_persist_cache(path, df):
        state["persist_cache_calls"] += 1
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("PERSISTED", encoding="utf-8")

    fake = SimpleNamespace()
    fake._needs_refresh = _orig_needs_refresh
    fake._fetch_secondary_from_yf = _orig_fetch_secondary_from_yf
    fake._write_cache_file = _orig_write_cache_file
    fake._persist_cache = _orig_persist_cache

    decoy = decoy_path or (tmp_path / "decoy_combo_leaderboard.xlsx")
    if decoy_path is None:
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text("decoy", encoding="utf-8")

    def _orig_find_latest_combo_table(sec):
        state["finder_calls"].append(("ORIGINAL", sec))
        return Path(decoy)
    fake._find_latest_combo_table = _orig_find_latest_combo_table

    def _build_board_rows(sec, k, *, run_fence=None, missing_map=None):
        # Snapshot the engine-internal surface as the runner wrapper
        # has it at the moment build_board_rows is invoked.
        state["compute_calls"].append((sec, k))
        nr_seen = fake._needs_refresh(sec, None, Path(fake_cache_target))
        state["compute_saw"]["needs_refresh_result"] = nr_seen
        if nr_seen:
            fetched = fake._fetch_secondary_from_yf(sec)
            state["compute_saw"]["fetched_empty"] = bool(fetched.empty)
            if not fetched.empty:
                # If reached while pinned this raises
                # ``engine_price_cache_write_blocked``; the engine
                # would normally try/except around the write but our
                # fake propagates so the test can observe.
                fake._write_cache_file(Path(fake_cache_target), fetched)
        try:
            path_via_finder = fake._find_latest_combo_table(sec)
            state["compute_saw"]["finder_returned"] = str(path_via_finder)
        except Exception as exc:
            state["compute_saw"]["finder_error"] = repr(exc)[:200]
        return [{"K": k, "Members": "FAKE"}]
    fake.build_board_rows = _build_board_rows

    return fake, state


def test_engine_network_surface_blocked_when_flag_not_passed(
        tmp_path, monkeypatch):
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)
    fake, state = _make_engine_surface_fake(tmp_path)

    # Save originals so we can assert restoration after main returns.
    orig_finder = fake._find_latest_combo_table
    orig_needs_refresh = fake._needs_refresh
    orig_fetch = fake._fetch_secondary_from_yf
    orig_write_cache = fake._write_cache_file
    orig_persist = fake._persist_cache

    sys.modules["trafficflow"] = fake
    try:
        argv = ["--secondaries", "SPY",
                "--stackbuilder-root", str(sb_root),
                "--k", "1",
                "--write",
                "--output-dir", str(out_dir)]
        # NO compute_callable -> uses _default_compute_loader.
        # NO --allow-network-fetch -> network/cache surfaces must
        # be pinned.
        rc, payload, _, _ = _capture_main(
            argv, process_conflict_checker=_no_conflict)
    finally:
        del sys.modules["trafficflow"]

    assert rc == runner.EXIT_OK
    assert payload["status"] == "ok"

    # Compute fired once for the eligible cell.
    assert state["compute_calls"] == [("SPY", 1)]
    # The ORIGINAL network/cache helpers must never have been called.
    assert state["needs_refresh_calls"] == 0
    assert state["fetch_calls"] == 0
    assert state["write_cache_calls"] == 0
    assert state["persist_cache_calls"] == 0
    # During compute the pinned _needs_refresh returned False.
    assert state["compute_saw"]["needs_refresh_result"] is False
    # The tracked would-be cache file must not exist.
    assert not Path(state["fake_cache_target"]).exists()
    # All patched attributes restored.
    assert fake._find_latest_combo_table is orig_finder
    assert fake._needs_refresh is orig_needs_refresh
    assert fake._fetch_secondary_from_yf is orig_fetch
    assert fake._write_cache_file is orig_write_cache
    assert fake._persist_cache is orig_persist


def test_engine_network_surface_not_pinned_when_flag_passed(
        tmp_path, monkeypatch):
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)
    fake, state = _make_engine_surface_fake(tmp_path)

    orig_finder = fake._find_latest_combo_table
    orig_needs_refresh = fake._needs_refresh
    orig_fetch = fake._fetch_secondary_from_yf
    orig_write_cache = fake._write_cache_file
    orig_persist = fake._persist_cache

    sys.modules["trafficflow"] = fake
    try:
        argv = ["--secondaries", "SPY",
                "--stackbuilder-root", str(sb_root),
                "--k", "1",
                "--write",
                "--output-dir", str(out_dir),
                "--allow-network-fetch"]
        rc, payload, _, _ = _capture_main(
            argv, process_conflict_checker=_no_conflict)
    finally:
        del sys.modules["trafficflow"]

    assert rc == runner.EXIT_OK
    # Compute ran and observed the ORIGINAL (un-pinned) network surface.
    assert state["needs_refresh_calls"] >= 1
    assert state["fetch_calls"] >= 1
    assert state["write_cache_calls"] >= 1
    # The fake write target now exists under tmp_path only.
    assert Path(state["fake_cache_target"]).exists()
    assert str(tmp_path) in state["fake_cache_target"]
    # All originals restored after wrapper exit (no leaked patches).
    assert fake._find_latest_combo_table is orig_finder
    assert fake._needs_refresh is orig_needs_refresh
    assert fake._fetch_secondary_from_yf is orig_fetch
    assert fake._write_cache_file is orig_write_cache
    assert fake._persist_cache is orig_persist


def test_engine_network_surface_restored_on_exception(tmp_path, monkeypatch):
    """When build_board_rows raises, every patched attribute must be
    restored to its original reference."""
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)
    fake, state = _make_engine_surface_fake(tmp_path)

    orig_finder = fake._find_latest_combo_table
    orig_needs_refresh = fake._needs_refresh
    orig_fetch = fake._fetch_secondary_from_yf
    orig_write_cache = fake._write_cache_file
    orig_persist = fake._persist_cache

    def _raising_build_board_rows(sec, k, *, run_fence=None, missing_map=None):
        # Confirm pin is active at the moment of raise.
        state["compute_saw"]["needs_refresh_result"] = fake._needs_refresh(
            sec, None, None)
        raise RuntimeError("synthetic engine failure")
    fake.build_board_rows = _raising_build_board_rows

    sys.modules["trafficflow"] = fake
    try:
        argv = ["--secondaries", "SPY",
                "--stackbuilder-root", str(sb_root),
                "--k", "1",
                "--write",
                "--output-dir", str(out_dir)]
        rc, payload, _, _ = _capture_main(
            argv, process_conflict_checker=_no_conflict)
    finally:
        del sys.modules["trafficflow"]

    # Runner's per-cell exception handling: cell records as error;
    # status is "failed" since no cell wrote.
    assert payload["status"] == "failed"
    cells = payload.get("per_cell_summary") or []
    assert any(c["status"] == "error"
               and c.get("error_class") == "RuntimeError"
               for c in cells)
    assert state["compute_saw"].get("needs_refresh_result") is False
    # All patched attributes restored.
    assert fake._find_latest_combo_table is orig_finder
    assert fake._needs_refresh is orig_needs_refresh
    assert fake._fetch_secondary_from_yf is orig_fetch
    assert fake._write_cache_file is orig_write_cache
    assert fake._persist_cache is orig_persist


def test_engine_network_surface_restored_on_keyboard_interrupt(
        tmp_path, monkeypatch):
    """The compute wrapper restores patched attributes even on
    BaseException (including KeyboardInterrupt). Tested directly
    against the wrapper rather than via ``main`` to avoid race-y
    test aborts."""
    fake, state = _make_engine_surface_fake(tmp_path)

    orig_finder = fake._find_latest_combo_table
    orig_needs_refresh = fake._needs_refresh
    orig_fetch = fake._fetch_secondary_from_yf
    orig_write_cache = fake._write_cache_file
    orig_persist = fake._persist_cache

    def _interrupting_build_board_rows(sec, k, *, run_fence=None,
                                        missing_map=None):
        state["compute_saw"]["needs_refresh_result"] = fake._needs_refresh(
            sec, None, None)
        raise KeyboardInterrupt("synthetic ^C")
    fake.build_board_rows = _interrupting_build_board_rows

    sys.modules["trafficflow"] = fake
    try:
        compute = runner._default_compute_loader(allow_network_fetch=False)
        with pytest.raises(KeyboardInterrupt):
            compute("SPY", 1, run_fence={"global": None, "by_sec": {}},
                    missing_map=None,
                    combo_leaderboard_path=str(tmp_path / "fake.xlsx"))
    finally:
        del sys.modules["trafficflow"]

    assert state["compute_saw"].get("needs_refresh_result") is False
    # All patched attributes restored even on BaseException.
    assert fake._find_latest_combo_table is orig_finder
    assert fake._needs_refresh is orig_needs_refresh
    assert fake._fetch_secondary_from_yf is orig_fetch
    assert fake._write_cache_file is orig_write_cache
    assert fake._persist_cache is orig_persist


def test_engine_network_surface_block_preserves_selected_build_pin(
        tmp_path, monkeypatch):
    """Selected-build pinning must still operate alongside the
    network/cache block."""
    sb_root, out_dir = _eligible_fixture(tmp_path, monkeypatch)
    fake, state = _make_engine_surface_fake(tmp_path)

    sys.modules["trafficflow"] = fake
    try:
        argv = ["--secondaries", "SPY",
                "--stackbuilder-root", str(sb_root),
                "--k", "1",
                "--write",
                "--output-dir", str(out_dir)]
        rc, payload, _, _ = _capture_main(
            argv, process_conflict_checker=_no_conflict)
    finally:
        del sys.modules["trafficflow"]

    assert rc == runner.EXIT_OK
    finder_returned = state["compute_saw"].get("finder_returned")
    assert finder_returned is not None
    assert "combo_leaderboard" in finder_returned
    assert "decoy" not in finder_returned
    assert state["needs_refresh_calls"] == 0
    assert state["fetch_calls"] == 0
    assert state["write_cache_calls"] == 0
    assert state["persist_cache_calls"] == 0


# ---------------------------------------------------------------------------
# Phase E PR Alpha: canonical-write CLI guardrails
# ---------------------------------------------------------------------------
#
# These tests cover the new --canonical-write and --heavy-stage CLI
# flags, the PHASE_E_RUN_MANIFEST_SCHEMA constant, the canonical
# output-dir helper, the fail-closed validation refusal codes, and
# the Phase Alpha not-implemented boundary. PR Alpha implements CLI
# guards and refusals only - no canonical-write mechanics. All tests
# refuse before any preflight or compute work; trafficflow.py is
# never imported by these tests.


def _alpha_argv_canonical(sb_root, *, secondaries="SPY", k_range="1,2,3,4,5,6",
                            output_dir_under_cwd="output/trafficflow",
                            write=True, canonical_write=True,
                            heavy_stage=False):
    """Build an argv for a Phase E PR Alpha canonical-write request.

    The output dir is given as a repo-relative path so the runner's
    canonical helper resolves it under the current working directory
    (which tests typically swap to tmp_path via monkeypatch.chdir).
    """
    argv = ["--secondaries", secondaries,
            "--stackbuilder-root", str(sb_root),
            "--k-range", k_range,
            "--output-dir", output_dir_under_cwd]
    if write:
        argv.append("--write")
    if canonical_write:
        argv.append("--canonical-write")
    if heavy_stage:
        argv.append("--heavy-stage")
    return argv


def test_phase_e_alpha_schema_constant_locked():
    """PHASE_E_RUN_MANIFEST_SCHEMA is locked to phase_e_v1."""
    assert runner.PHASE_E_RUN_MANIFEST_SCHEMA == "trafficflow_runner_phase_e_v1"


def test_phase_e_alpha_existing_phase_c_canonical_refusal_preserved(
    tmp_path, monkeypatch
):
    """--write into output/trafficflow without --canonical-write must
    still refuse with canonical_write_forbidden_in_phase_c. The Phase
    Alpha guardrails must not weaken the existing Phase C refusal."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--output-dir", "output/trafficflow",
            "--write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "refused"
    assert "canonical_write_forbidden_in_phase_c" in payload["errors"]
    # No board-row files written under the fake output/trafficflow root.
    fake_canonical = tmp_path / "output" / "trafficflow"
    if fake_canonical.exists():
        assert list(fake_canonical.rglob("board_rows_*")) == []
        assert list(fake_canonical.rglob("run_manifest.json")) == []
    assert "trafficflow" not in sys.modules


def test_phase_e_alpha_canonical_write_without_write_refuses(
    tmp_path, monkeypatch
):
    """--canonical-write without --write refuses with
    canonical_write_requires_write, exit 2, before any compute."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--output-dir", "output/trafficflow",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert rc == 2
    assert payload["status"] == "refused"
    assert "canonical_write_requires_write" in payload["errors"]
    assert "trafficflow" not in sys.modules
    ec = payload["effective_config"]
    assert ec["canonical_write_requested"] is True
    assert ec["heavy_stage_requested"] is False


def test_phase_e_alpha_heavy_stage_without_canonical_write_refuses(
    tmp_path, monkeypatch
):
    """--heavy-stage without --canonical-write refuses with
    heavy_stage_requires_canonical_write, exit 2, before any compute."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    iso_out = tmp_path / "iso"
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--output-dir", str(iso_out),
            "--write",
            "--heavy-stage"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert rc == 2
    assert payload["status"] == "refused"
    assert "heavy_stage_requires_canonical_write" in payload["errors"]
    assert "trafficflow" not in sys.modules
    ec = payload["effective_config"]
    assert ec["canonical_write_requested"] is False
    assert ec["heavy_stage_requested"] is True


def test_phase_e_alpha_canonical_write_wrong_output_dir_refuses(
    tmp_path, monkeypatch
):
    """--canonical-write with output_dir not under output/trafficflow
    refuses with canonical_write_output_dir_must_be_output_trafficflow."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    iso_out = tmp_path / "iso"
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--output-dir", str(iso_out),
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert rc == 2
    assert payload["status"] == "refused"
    assert (
        "canonical_write_output_dir_must_be_output_trafficflow"
        in payload["errors"]
    )
    assert "trafficflow" not in sys.modules
    ec = payload["effective_config"]
    assert ec["canonical_write_requested"] is True


def test_phase_e_alpha_canonical_write_multi_secondary_refuses(
    tmp_path, monkeypatch
):
    """--canonical-write with multiple secondaries refuses with
    canonical_write_multi_secondary_unsupported_use_orchestrator."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    for sec in ("SPY", "AAPL"):
        chosen = sb_root / sec / "RUN_A"
        _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
        _write_selected_build(sb_root, sec, selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    argv = ["--secondaries", "SPY,AAPL",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--output-dir", "output/trafficflow",
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert rc == 2
    assert payload["status"] == "refused"
    assert (
        "canonical_write_multi_secondary_unsupported_use_orchestrator"
        in payload["errors"]
    )
    assert "trafficflow" not in sys.modules
    # No board-row files anywhere under the fake canonical root.
    fake_canonical = tmp_path / "output" / "trafficflow"
    if fake_canonical.exists():
        assert list(fake_canonical.rglob("board_rows_*")) == []


def test_phase_e_alpha_canonical_write_k_above_6_without_heavy_stage_refuses(
    tmp_path, monkeypatch
):
    """--canonical-write with K > 6 and no --heavy-stage refuses with
    canonical_write_heavy_stage_requires_flag."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"], 7: ["BBB[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,7",
            "--output-dir", "output/trafficflow",
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert rc == 2
    assert payload["status"] == "refused"
    assert (
        "canonical_write_heavy_stage_requires_flag"
        in payload["errors"]
    )
    assert "trafficflow" not in sys.modules
    ec = payload["effective_config"]
    assert ec["canonical_write_requested"] is True
    assert ec["heavy_stage_requested"] is False


# NOTE: PR Beta retired the Phase Alpha not-implemented refusal for
# valid canonical-write requests. The two tests that previously
# asserted ``canonical_write_not_implemented_in_phase_alpha`` (one
# for K > 6 with --heavy-stage, one for K=1..6 single-secondary) are
# now PR Beta execution-path tests under the
# ``test_phase_e_beta_canonical_writer_*`` family below.


def test_phase_e_alpha_default_invocation_effective_config_fields_present(
    tmp_path, monkeypatch
):
    """Non-canonical default invocations surface the new Phase E
    discriminators in effective_config. canonical_write_requested and
    heavy_stage_requested are False; canonical_write_mode is None."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1"]
    _, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    ec = payload["effective_config"]
    assert ec["canonical_write_requested"] is False
    assert ec["heavy_stage_requested"] is False
    assert ec["canonical_write_mode"] is None


# ---------------------------------------------------------------------------
# Phase E PR Beta: canonical writer mechanics
# ---------------------------------------------------------------------------
#
# PR Beta lands the per-secondary canonical writer behind
# --canonical-write. The writer:
#
# - is single-secondary only (multi-secondary is refused upstream by
#   canonical_write_multi_secondary_unsupported_use_orchestrator);
# - requires explicit --k or --k-range (refused with
#   canonical_write_requires_explicit_k_selection);
# - writes per-K board_rows JSON+CSV files via the existing atomic
#   helpers under <output_dir>/<SEC>/;
# - writes <SEC>/secondary_manifest.json (PR #317 Option A);
# - writes a zero-byte <SEC>/.done marker LAST, only after every
#   requested K succeeds;
# - on any compute/write/validation failure, stops processing,
#   leaves .done absent, and writes
#   <output_dir>/.quarantine/<SEC>/failure.json.
#
# Run-level files (progress.json, run_status.json, run_manifest.json,
# run.stdout.json, selected_output.json) are NOT written in PR Beta;
# those are PR Gamma orchestrator scope.
#
# All canonical-write tests use tmp_path + monkeypatch.chdir(tmp_path)
# so the fake "output/trafficflow" tree lives under the test scratch
# dir; trafficflow.py is never imported during refusal paths and a
# mocked compute callable is injected for execution-path tests.


def _canonical_eligible_fixture(tmp_path, monkeypatch, *,
                                  secondary="SPY",
                                  members_by_k=None):
    """Build a fully-eligible canonical-write fixture in tmp_path.

    Writes a multi-K fake leaderboard, a selected_build.json, a
    member PKL per unique member with declared_max_sma_day=114 and
    has_SMA_114=True, and a price cache for the secondary with
    tail_date matching the price-cache classifier expectation.

    Returns (sb_root_path, canonical_root_path) where
    canonical_root_path is repo-relative under tmp_path (used as
    --output-dir).
    """
    if members_by_k is None:
        members_by_k = {k: [f"M{k}[D]"] for k in range(1, 7)}
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / secondary / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members=members_by_k)
    _write_selected_build(sb_root, secondary, selected_run_dir=chosen,
                          selected_k=max(members_by_k))
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", secondary,
                       tail_date=_fresh_tail_date())
    seen = set()
    for ks_members in members_by_k.values():
        for tm in ks_members:
            base = str(tm).split("[", 1)[0].strip().upper()
            if base and base not in seen:
                seen.add(base)
                _write_pkl(tmp_path / "cache" / "results", base,
                           declared_max_sma_day=114, has_sma_114=True)
    # Phase 3a (PR #332): canonical-write now also emits v1_history.json
    # per secondary via trafficflow_v1_history_writer. A "fully-eligible
    # canonical-write fixture" therefore needs per-interval signal
    # libraries on disk so the v1 history writer can build a non-empty
    # bars array. Write a minimal fake library for each canonical
    # timeframe aligned with the price cache dates above.
    sig_lib_dir = tmp_path / "signal_library" / "data" / "stable"
    sig_lib_dir.mkdir(parents=True, exist_ok=True)
    _sig_dates = ["2026-05-01", "2026-05-22"]
    _sig_values = ["Buy", "Buy"]
    for _tf in ("1d", "1wk", "1mo", "3mo", "1y"):
        if _tf == "1d":
            _fname = f"{secondary}_stable_v1_0_0.pkl"
        else:
            _fname = f"{secondary}_stable_v1_0_0_{_tf}.pkl"
        _payload = {
            "ticker": secondary,
            "interval": _tf,
            "engine_version": "1.0.0",
            "dates": list(_sig_dates),
            "signals": list(_sig_values),
        }
        with open(sig_lib_dir / _fname, "wb") as _fh:
            import pickle as _pickle
            _pickle.dump(_payload, _fh)
    monkeypatch.chdir(tmp_path)
    canonical_root = Path("output") / "trafficflow" / "runs" / "RUN_PHASE_E_TEST"
    return sb_root, canonical_root


def _make_canonical_compute_mock(rows_per_k=1, raise_on_k=None):
    """Build a mock compute callable matching the runner's contract.

    Records calls on the returned callable's ``.calls`` attribute. If
    ``raise_on_k`` is set, raises ``RuntimeError`` when the runner
    asks for that K value.
    """
    calls: list[dict] = []

    def _compute(secondary, k, *, run_fence=None, missing_map=None,
                  combo_leaderboard_path=None):
        calls.append({"secondary": secondary, "k": k})
        if raise_on_k is not None and k == raise_on_k:
            raise RuntimeError(f"mock_compute_raised_at_k_{k}")
        return [{"secondary": secondary, "k": k, "row_idx": i}
                for i in range(rows_per_k)]

    _compute.calls = calls  # type: ignore[attr-defined]
    return _compute


def test_phase_e_beta_canonical_write_requires_explicit_k_selection(
    tmp_path, monkeypatch
):
    """--canonical-write with neither --k nor --k-range refuses with
    canonical_write_requires_explicit_k_selection, exit 2, before any
    preflight or compute."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--output-dir", "output/trafficflow",
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert rc == 2
    assert payload["status"] == "refused"
    assert (
        "canonical_write_requires_explicit_k_selection"
        in payload["errors"]
    )
    assert "trafficflow" not in sys.modules
    fake_canonical = tmp_path / "output" / "trafficflow"
    if fake_canonical.exists():
        assert list(fake_canonical.rglob("board_rows_*")) == []
        assert list(fake_canonical.rglob(".done")) == []


def test_phase_e_beta_canonical_write_k1_6_succeeds_with_mock_compute(
    tmp_path, monkeypatch
):
    """A valid K=1..6 single-secondary canonical-write request with
    mocked compute succeeds. Writes 12 board_rows files + .done +
    secondary_manifest.json; no .quarantine; canonical_write_mode is
    'complete'."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock(rows_per_k=2)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3,4,5,6",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_OK
    assert payload["status"] == "ok"
    assert payload["write_mode"] == "canonical"
    # Compute called once per K=1..6.
    seen_ks = {c["k"] for c in compute.calls}
    assert seen_ks == {1, 2, 3, 4, 5, 6}
    # All board-row files exist; .done exists; no .quarantine.
    sec_dir = canonical_root / "SPY"
    for k in range(1, 7):
        assert (sec_dir / f"board_rows_k={k}.json").exists()
        assert (sec_dir / f"board_rows_k={k}.csv").exists()
    assert (sec_dir / ".done").exists()
    assert (sec_dir / ".done").stat().st_size == 0
    assert (sec_dir / "secondary_manifest.json").exists()
    assert not (canonical_root / ".quarantine").exists()
    ec = payload["effective_config"]
    assert ec["canonical_write_requested"] is True
    assert ec["heavy_stage_requested"] is False
    assert ec["canonical_write_mode"] == "complete"


def test_phase_e_beta_canonical_write_no_tmp_residue(
    tmp_path, monkeypatch
):
    """After a successful canonical write, no .tmp residue remains
    under the secondary directory or under .quarantine."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock()
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3,4,5,6",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, _, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_OK
    assert list(canonical_root.rglob("*.tmp")) == []


def test_phase_e_beta_canonical_write_done_only_after_all_k_succeed(
    tmp_path, monkeypatch
):
    """If compute raises on K=4, the canonical writer stops, .done is
    absent, K=1..3 succeed, K=4..6 are skipped, and a quarantine
    failure.json records last_completed_k=3 / failed_at_k=4."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock(raise_on_k=4)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3,4,5,6",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "failed"
    assert payload["write_mode"] == "canonical"
    sec_dir = canonical_root / "SPY"
    for k in (1, 2, 3):
        assert (sec_dir / f"board_rows_k={k}.json").exists()
        assert (sec_dir / f"board_rows_k={k}.csv").exists()
    for k in (4, 5, 6):
        assert not (sec_dir / f"board_rows_k={k}.json").exists()
        assert not (sec_dir / f"board_rows_k={k}.csv").exists()
    assert not (sec_dir / ".done").exists()
    failure_path = canonical_root / ".quarantine" / "SPY" / "failure.json"
    assert failure_path.exists()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["secondary"] == "SPY"
    assert failure["failure_kind"] == "compute_error"
    assert failure["last_completed_k"] == 3
    assert failure["failed_at_k"] == 4
    assert failure["schema_version"] == runner.PHASE_E_RUN_MANIFEST_SCHEMA
    ec = payload["effective_config"]
    assert ec["canonical_write_mode"] == "quarantined"


def test_phase_e_beta_canonical_write_no_run_level_files(
    tmp_path, monkeypatch
):
    """PR Beta does NOT write run-level orchestrator files. After a
    successful canonical write, none of progress.json /
    run_status.json / run_manifest.json / run.stdout.json /
    selected_output.json exist at the run root."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock()
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3,4,5,6",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, _, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_OK
    for name in ("progress.json", "run_status.json", "run_manifest.json",
                 "run.stdout.json"):
        assert not (canonical_root / name).exists(), name
    # selected_output.json lives at the canonical root, not the run
    # root; check both locations for safety.
    assert not (canonical_root / "selected_output.json").exists()
    canonical_root_top = tmp_path / "output" / "trafficflow"
    assert not (canonical_root_top / "selected_output.json").exists()
    # secondary_manifest.json IS written; it's per-secondary, not run-level.
    assert (canonical_root / "SPY" / "secondary_manifest.json").exists()


def test_phase_e_beta_canonical_write_k7_with_heavy_stage_succeeds(
    tmp_path, monkeypatch
):
    """--canonical-write --heavy-stage --k-range 1,7 executes the
    canonical writer for K=1 and K=7. board_rows_k=1 and
    board_rows_k=7 exist; .done exists; canonical_write_mode is
    'complete'."""
    sb_root, canonical_root = _canonical_eligible_fixture(
        tmp_path, monkeypatch,
        members_by_k={1: ["AAA[D]"], 7: ["BBB[D]"]})
    compute = _make_canonical_compute_mock()
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,7",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write",
            "--heavy-stage"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_OK
    assert payload["status"] == "ok"
    sec_dir = canonical_root / "SPY"
    for k in (1, 7):
        assert (sec_dir / f"board_rows_k={k}.json").exists()
        assert (sec_dir / f"board_rows_k={k}.csv").exists()
    assert (sec_dir / ".done").exists()
    seen_ks = {c["k"] for c in compute.calls}
    assert seen_ks == {1, 7}
    ec = payload["effective_config"]
    assert ec["heavy_stage_requested"] is True
    assert ec["canonical_write_mode"] == "complete"


def test_phase_e_beta_canonical_write_write_failure_quarantines(
    tmp_path, monkeypatch
):
    """When _atomic_write_bytes raises on a specific K, the writer
    quarantines: .done absent, K=1..2 ok, K=3 missing, failure.json
    records failure_kind='write_error'."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock()
    real_write_bytes = runner._atomic_write_bytes

    def _selective_write_bytes(path, data):
        # Fail the CSV write of K=3 only.
        if str(path).endswith("board_rows_k=3.csv"):
            raise OSError("simulated_write_failure_k3")
        return real_write_bytes(path, data)

    monkeypatch.setattr(runner, "_atomic_write_bytes", _selective_write_bytes)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3,4,5,6",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "failed"
    sec_dir = canonical_root / "SPY"
    assert not (sec_dir / ".done").exists()
    assert (sec_dir / "board_rows_k=1.json").exists()
    assert (sec_dir / "board_rows_k=2.json").exists()
    # K=3 CSV failed; the JSON for K=3 may have been written (it
    # precedes the CSV write in the writer), but .done is absent and
    # the run is quarantined.
    assert not (sec_dir / "board_rows_k=3.csv").exists()
    failure_path = canonical_root / ".quarantine" / "SPY" / "failure.json"
    assert failure_path.exists()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["failure_kind"] == "write_error"
    assert failure["failed_at_k"] == 3
    assert failure["error_class"] == "OSError"
    ec = payload["effective_config"]
    assert ec["canonical_write_mode"] == "quarantined"


def test_phase_e_beta_canonical_write_multi_secondary_still_refuses(
    tmp_path, monkeypatch
):
    """Multi-secondary --canonical-write still refuses upstream
    (PR Alpha guard); the new writer is never reached."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    for sec in ("SPY", "AAPL"):
        chosen = sb_root / sec / "RUN_A"
        _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
        _write_selected_build(sb_root, sec, selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    argv = ["--secondaries", "SPY,AAPL",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--output-dir", "output/trafficflow",
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert (
        "canonical_write_multi_secondary_unsupported_use_orchestrator"
        in payload["errors"]
    )
    assert "trafficflow" not in sys.modules


def test_phase_e_beta_canonical_write_k_above_6_without_heavy_stage_still_refuses(
    tmp_path, monkeypatch
):
    """K > 6 without --heavy-stage still refuses upstream; the writer
    is never reached."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"], 7: ["BBB[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,7",
            "--output-dir", "output/trafficflow",
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert (
        "canonical_write_heavy_stage_requires_flag"
        in payload["errors"]
    )
    assert "trafficflow" not in sys.modules


def test_phase_e_beta_quarantine_failure_json_is_sanitized(
    tmp_path, monkeypatch
):
    """The quarantine failure.json must not leak the absolute
    tmp_path; failure.json content is plain JSON written into the
    canonical tree, so paths it contains (none in PR Beta) would
    have been sanitized by path_for_output. As a regression, also
    check the stdout payload contains no absolute tmp_path under
    the canonical-write quarantined path."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock(raise_on_k=2)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, payload, _, raw_stdout = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_REFUSED
    abs_tmp = str(tmp_path)
    # stdout payload sanitized.
    assert abs_tmp not in raw_stdout
    # failure.json on disk must not embed the absolute tmp_path.
    failure_path = canonical_root / ".quarantine" / "SPY" / "failure.json"
    assert failure_path.exists()
    raw_fail = failure_path.read_text(encoding="utf-8")
    assert abs_tmp not in raw_fail


def test_phase_e_beta_refusal_paths_still_do_not_import_trafficflow(
    tmp_path, monkeypatch
):
    """All canonical-write refusal paths (explicit-K guard, multi-
    secondary, K > 6 without heavy-stage, Phase C canonical refusal)
    must not import trafficflow."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"], 7: ["BBB[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)

    # Explicit-K refusal.
    sys.modules.pop("trafficflow", None)
    rc, _, _, _ = _capture_main(
        ["--secondaries", "SPY",
         "--stackbuilder-root", str(sb_root),
         "--output-dir", "output/trafficflow",
         "--write", "--canonical-write"],
        process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert "trafficflow" not in sys.modules

    # K > 6 without heavy-stage.
    sys.modules.pop("trafficflow", None)
    rc, _, _, _ = _capture_main(
        ["--secondaries", "SPY",
         "--stackbuilder-root", str(sb_root),
         "--k-range", "1,7",
         "--output-dir", "output/trafficflow",
         "--write", "--canonical-write"],
        process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_CANONICAL_WRITE_REFUSED
    assert "trafficflow" not in sys.modules

    # Phase C canonical refusal: --write to output/trafficflow without
    # --canonical-write (legacy preserved).
    sys.modules.pop("trafficflow", None)
    rc, _, _, _ = _capture_main(
        ["--secondaries", "SPY",
         "--stackbuilder-root", str(sb_root),
         "--k", "1",
         "--output-dir", "output/trafficflow",
         "--write"],
        process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_REFUSED
    assert "trafficflow" not in sys.modules


def test_phase_e_beta_schema_constant_still_locked():
    """PHASE_E_RUN_MANIFEST_SCHEMA remains locked to phase_e_v1."""
    assert runner.PHASE_E_RUN_MANIFEST_SCHEMA == "trafficflow_runner_phase_e_v1"


def test_phase_e_beta_secondary_manifest_provenance_fields(
    tmp_path, monkeypatch
):
    """secondary_manifest.json (Option A) carries the locked schema
    plus provenance and per-K summary fields."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock(rows_per_k=3)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3,4,5,6",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, _, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_OK
    sm_path = canonical_root / "SPY" / "secondary_manifest.json"
    assert sm_path.exists()
    sm = json.loads(sm_path.read_text(encoding="utf-8"))
    assert sm["schema_version"] == runner.PHASE_E_RUN_MANIFEST_SCHEMA
    assert sm["secondary"] == "SPY"
    assert sm["canonical_write_mode"] == "complete"
    assert sm["k_requested"] == [1, 2, 3, 4, 5, 6]
    assert len(sm["per_k_summary"]) == 6
    for entry in sm["per_k_summary"]:
        assert entry["row_count"] == 3
        assert entry["json_path"] is not None
        assert entry["csv_path"] is not None
    # selected_build_sha256 was computed from the on-disk selected_build.json
    # (preflight's path); selected_build_path is sanitized repo-relative
    # POSIX.
    assert sm["selected_build_sha256"] is not None
    assert sm["selected_build_path"] is not None
    assert sm["explicit_build_override"] is False


# ---------------------------------------------------------------------------
# Phase E PR Beta amendments (PR #319 audit fixes)
# ---------------------------------------------------------------------------
#
# Amendment 1: prevent false `.done` on preflight failure or empty K
# eligibility. The canonical writer must validate the preflight
# verdict + requested-K eligibility BEFORE the K loop; an empty
# eligibility map must NOT be promoted to .done.
#
# Amendment 2: treat canonical-write requests as write_requested=True
# for process-conflict checks. Canonical writes must fail closed when
# enumeration errors occur, matching isolated-write semantics.
#
# Amendment 3: sanitize the quarantine failure.json before writing.
# OSError messages can leak absolute filesystem paths through
# error_message; the failure record must pass through the same
# path/privacy layer as stdout.


def test_phase_e_beta_amendment_missing_selected_build_quarantines(
    tmp_path, monkeypatch
):
    """Audit blocker repro: canonical-write with a missing
    selected_build.json must fail-closed, write .quarantine, and NOT
    write .done."""
    monkeypatch.chdir(tmp_path)
    # Build a stackbuilder root that does NOT contain SPY/selected_build.json,
    # so preflight will refuse.
    sb_root = tmp_path / "output" / "stackbuilder"
    sb_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    canonical_root = Path("output") / "trafficflow" / "runs" / "RUN_MISSING_SB"
    compute = _make_canonical_compute_mock()
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3,4,5,6",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "failed"
    # Compute mock was NOT called; the validation refusal fires before
    # the K loop.
    assert compute.calls == []
    # .done must be absent.
    sec_dir = canonical_root / "SPY"
    assert not (sec_dir / ".done").exists()
    # No board-row files should exist.
    if sec_dir.exists():
        assert list(sec_dir.glob("board_rows_*")) == []
    # Quarantine failure.json must exist with validation_error kind.
    failure_path = canonical_root / ".quarantine" / "SPY" / "failure.json"
    assert failure_path.exists()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["secondary"] == "SPY"
    assert failure["failure_kind"] == "validation_error"
    ec = payload["effective_config"]
    assert ec["canonical_write_mode"] == "quarantined"


def test_phase_e_beta_amendment_empty_k_eligibility_quarantines(
    tmp_path, monkeypatch
):
    """When preflight returns an empty k_eligibility map (e.g.
    leaderboard rows missing for every requested K), canonical-write
    must fail-closed and not write .done. The compute callable must
    not be invoked."""
    # Build a fixture where the leaderboard has K=1 but the canonical
    # request asks for K=5, so preflight resolves no eligible K cells.
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen,
                          selected_k=1)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    _write_pkl(tmp_path / "cache" / "results", "AAA",
               declared_max_sma_day=114, has_sma_114=True)
    canonical_root = Path("output") / "trafficflow" / "runs" / "RUN_EMPTY_K"
    compute = _make_canonical_compute_mock()
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "5",  # K=5 is NOT in the leaderboard
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "failed"
    # Compute must not have been called.
    assert compute.calls == []
    # .done absent; no board-row files.
    sec_dir = canonical_root / "SPY"
    assert not (sec_dir / ".done").exists()
    if sec_dir.exists():
        assert list(sec_dir.glob("board_rows_*")) == []
    # Quarantine record present.
    failure_path = canonical_root / ".quarantine" / "SPY" / "failure.json"
    assert failure_path.exists()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["failure_kind"] == "validation_error"
    ec = payload["effective_config"]
    assert ec["canonical_write_mode"] == "quarantined"


def test_phase_e_beta_amendment_requested_k_not_eligible_quarantines(
    tmp_path, monkeypatch
):
    """When a requested K is present in the leaderboard but resolves
    to a non-ELIGIBLE eligibility (e.g. STALE-GATED via missing PKL),
    canonical-write must fail-closed before invoking compute for the
    bad K. Earlier K values that completed successfully may remain on
    disk, but .done must be absent and quarantine must be written."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    # Two K rows but only K=1's member has a PKL; K=2's member is
    # missing, so its eligibility resolves to a non-ELIGIBLE gate.
    _make_fake_leaderboard(chosen,
                            k_to_members={1: ["AAA[D]"], 2: ["MISS[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen,
                          selected_k=2)
    monkeypatch.setattr(runner, "DEFAULT_PRICE_CACHE_DIR",
                        str(tmp_path / "price_cache" / "daily"))
    monkeypatch.setattr(runner, "DEFAULT_CACHE_RESULTS_DIR",
                        str(tmp_path / "cache" / "results"))
    _write_price_cache(tmp_path / "price_cache" / "daily", "SPY",
                       tail_date=_fresh_tail_date())
    _write_pkl(tmp_path / "cache" / "results", "AAA",
               declared_max_sma_day=114, has_sma_114=True)
    # MISS has no PKL written.
    canonical_root = Path("output") / "trafficflow" / "runs" / "RUN_K2_BAD"
    compute = _make_canonical_compute_mock()
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "failed"
    # The pre-loop validation fires before compute is invoked.
    assert compute.calls == []
    sec_dir = canonical_root / "SPY"
    assert not (sec_dir / ".done").exists()
    failure_path = canonical_root / ".quarantine" / "SPY" / "failure.json"
    assert failure_path.exists()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["failure_kind"] == "validation_error"
    ec = payload["effective_config"]
    assert ec["canonical_write_mode"] == "quarantined"


def test_phase_e_beta_amendment_canonical_write_treated_as_write_for_conflict(
    tmp_path, monkeypatch
):
    """The process-conflict checker must be called with
    write_requested=True for canonical-write requests, even though
    Phase C write-mode logic leaves write_authorized=False for
    canonical output paths."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock()
    seen = {"write_requested": None}

    def _checker(write_requested=False):
        seen["write_requested"] = write_requested
        return {"status": "ok", "conflicts": [],
                "queried_via": "fake", "error": None}

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, _, _, _ = _capture_main(
        argv, process_conflict_checker=_checker,
        compute_callable=compute)
    assert rc == runner.EXIT_OK
    # The conflict checker MUST have been called with write_requested=True.
    assert seen["write_requested"] is True


def test_phase_e_beta_amendment_canonical_write_enumeration_error_refuses(
    tmp_path, monkeypatch
):
    """If the process-conflict checker returns status='error' under
    canonical-write, the runner must fail closed (refuse) just as it
    does for isolated-write. Compute must not be invoked and
    trafficflow must not be imported."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)

    def _enum_error(write_requested=False):
        return {"status": "error", "conflicts": [],
                "queried_via": "fake",
                "error": "enumeration_failed"}

    compute = _make_canonical_compute_mock()
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--output-dir", "output/trafficflow",
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_enum_error,
        compute_callable=compute)
    assert rc == runner.EXIT_PROCESS_CONFLICT
    assert payload["status"] == "refused"
    assert "process_conflict_enumeration_unavailable" in payload["errors"]
    assert compute.calls == []
    assert "trafficflow" not in sys.modules


def test_phase_e_beta_amendment_canonical_write_blocked_conflict_refuses(
    tmp_path, monkeypatch
):
    """If the process-conflict checker returns status='blocked' under
    canonical-write, the runner must refuse with process_conflict_blocked
    and exit EXIT_PROCESS_CONFLICT."""
    monkeypatch.chdir(tmp_path)
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    sys.modules.pop("trafficflow", None)
    compute = _make_canonical_compute_mock()
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--output-dir", "output/trafficflow",
            "--write",
            "--canonical-write"]
    rc, payload, _, _ = _capture_main(
        argv, process_conflict_checker=_blocked_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_PROCESS_CONFLICT
    assert payload["status"] == "refused"
    assert "process_conflict_blocked" in payload["errors"]
    assert compute.calls == []
    assert "trafficflow" not in sys.modules


def test_phase_e_beta_amendment_quarantine_failure_json_sanitizes_paths(
    tmp_path, monkeypatch
):
    """Force an OSError whose message contains an absolute tmp_path-
    like path. The quarantine failure.json must NOT contain that raw
    absolute path; sanitization must redact it via the same
    path/privacy layer that processes stdout."""
    sb_root, canonical_root = _canonical_eligible_fixture(tmp_path, monkeypatch)
    compute = _make_canonical_compute_mock()
    real_write_bytes = runner._atomic_write_bytes

    # Craft an absolute-path-bearing OSError message. The path lives
    # under tmp_path (which is outside the chdir'd project root in
    # canonical-write fixtures because chdir(tmp_path) makes tmp_path
    # the project root - so we point at an absolute path OUTSIDE the
    # project root, which sanitize_for_json redacts to
    # <ABSOLUTE_PATH_REDACTED>).
    leaky_abs_path = "/var/some/leaky/abs/path/cache/results/SPY.pkl"

    def _selective_write_bytes(path, data):
        if str(path).endswith("board_rows_k=2.csv"):
            raise OSError(
                f"simulated_oserror referencing {leaky_abs_path}"
            )
        return real_write_bytes(path, data)

    monkeypatch.setattr(runner, "_atomic_write_bytes", _selective_write_bytes)

    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k-range", "1,2,3",
            "--output-dir", str(canonical_root),
            "--write",
            "--canonical-write"]
    rc, _, _, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict,
        compute_callable=compute)
    assert rc == runner.EXIT_REFUSED
    failure_path = canonical_root / ".quarantine" / "SPY" / "failure.json"
    assert failure_path.exists()
    raw_fail = failure_path.read_text(encoding="utf-8")
    # The raw absolute path must NOT appear in failure.json.
    assert leaky_abs_path not in raw_fail
    # And the canonical privacy placeholder must appear instead
    # (sanitize_for_json + path_for_output rewrites any absolute path
    # outside the project root as <ABSOLUTE_PATH_REDACTED>).
    failure = json.loads(raw_fail)
    assert failure["failure_kind"] == "write_error"
    # The error_message field still describes the error class /
    # context but the absolute path has been redacted.
    em = str(failure.get("error_message", ""))
    assert leaky_abs_path not in em
    assert "<ABSOLUTE_PATH_REDACTED>" in em
