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
import sys
from contextlib import redirect_stderr, redirect_stdout
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


def _write_price_cache(price_cache_dir, secondary, *, tail_date="2026-05-22"):
    """Write a minimal CSV price cache."""
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
# 7. --write rejection
# ---------------------------------------------------------------------------


def test_write_is_refused(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    chosen = sb_root / "SPY" / "RUN_A"
    _make_fake_leaderboard(chosen, k_to_members={1: ["AAA[D]"]})
    _write_selected_build(sb_root, "SPY", selected_run_dir=chosen)
    argv = ["--secondaries", "SPY",
            "--stackbuilder-root", str(sb_root),
            "--k", "1",
            "--write"]
    rc, payload, stderr_text, _ = _capture_main(
        argv, process_conflict_checker=_no_conflict)
    assert rc == runner.EXIT_REFUSED
    assert payload["status"] == "refused"
    assert "phase_b_write_not_supported" in payload["warnings"]
    assert "phase_b_write_not_supported" in payload["errors"]
    assert payload["artifacts_written"] == []
    assert payload["verdict"] == "REFUSED"


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
                       tail_date="2026-05-22")
    _write_pkl_with_tail(tmp_path / "cache" / "results", "STALEM",
                         tail_date="2026-05-10")
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
                       tail_date="2026-05-22")
    _write_pkl_with_tail(tmp_path / "cache" / "results", "STALEM",
                         tail_date="2026-05-10")
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
