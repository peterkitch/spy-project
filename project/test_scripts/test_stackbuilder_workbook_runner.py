"""Phase 6I-70 — tests for the StackBuilder headless runner scaffold.

All tests run without network, without importing real ``stackbuilder``,
without invoking any engine, and without writing outside ``tmp_path``.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUNNER_PATH = PROJECT_ROOT / "stackbuilder_workbook_runner.py"

import stackbuilder_workbook_runner as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FAKE_RUN_SUMMARY = {
    "run_dir": None,
    "exists": True,
    "issues": [],
    "manifest": {
        "run_id": "FAKE-RUN-0001",
        "started_at": "2026-05-21T00:00:00Z",
        "finished_at": "2026-05-21T00:01:00Z",
        "params": {"max_k": 3},
    },
    "summary": {
        "best_sharpe": 1.23,
        "best_capture": 4.56,
        "primaries_tested": 42,
        "parameters": {"max_k": 3},
    },
    "artifacts": {},
    "row_counts": {},
    "k_level_counts": {},
    "best_total_capture": 4.56,
    "best_sharpe": 1.23,
    "created_at": "2026-05-21T00:00:00Z",
    "finished_at": "2026-05-21T00:01:00Z",
}


def _args_with(**overrides):
    """Build a runner CLI args Namespace via the real parser, then patch."""
    args = runner.parse_args([
        "--secondaries", overrides.pop("secondaries", "SPY"),
    ])
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _no_conflict(write_requested=False):  # match check_process_conflicts shape
    return {
        "status": "ok",
        "conflicts": [],
        "queried_via": "fake",
        "error": None,
    }


# ---------------------------------------------------------------------------
# 1. AST guard
# ---------------------------------------------------------------------------


def test_no_toplevel_dangerous_imports():
    forbidden = {
        "stackbuilder",
        "onepass",
        "impactsearch",
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
                root = alias.name.split(".")[0]
                if root in forbidden:
                    bad.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in forbidden:
                bad.append(f"from {node.module} import …")
    assert not bad, f"forbidden top-level imports: {bad}"


# ---------------------------------------------------------------------------
# 2. Locked v1 defaults
# ---------------------------------------------------------------------------


def test_parse_args_defaults_match_locked_v1():
    args = runner.parse_args(["--secondaries", "SPY"])
    assert args.primary_source == "impact_xlsx"
    assert args.outdir == "output/stackbuilder"
    assert args.output_format == "xlsx"
    assert args.k_max == 6
    assert args.top_n == 20
    assert args.bottom_n == 20
    assert args.search == "beam"
    assert args.beam_width == 12
    assert args.exhaustive_k == 4
    assert args.min_trigger_days == 30
    assert args.sharpe_eps == pytest.approx(0.01)
    assert args.seed_by == "total_capture"
    assert args.optimize_by == "auto"
    assert args.allow_decreasing is False
    assert args.jobs == 1
    assert args.write is False
    assert args.allow_network_fetch is False
    assert args.update_selected is False
    assert args.pin_build is None
    # Phase 6I-71: progress_dir defaults to None and resolves at use-
    # site to ``<outdir>/_progress``.
    assert args.progress_dir is None


# ---------------------------------------------------------------------------
# 3. Secondaries resolution
# ---------------------------------------------------------------------------


def test_resolve_secondaries_preserves_order_and_punctuation():
    args = runner.parse_args([
        "--secondaries", "SPY,^GSPC,BRK-B,BRK.B,SPY",
    ])
    res = runner.resolve_secondaries(args)
    assert res["status"] == "ok"
    assert res["secondaries"] == ["SPY", "^GSPC", "BRK-B", "BRK.B"]


def test_resolve_secondaries_empty_refused():
    args = runner.parse_args(["--secondaries", "   "])
    args.secondaries = ""
    res = runner.resolve_secondaries(args)
    assert res["status"] == "refused"
    assert res["secondaries"] == []


# ---------------------------------------------------------------------------
# 4. Primaries resolution preserves NA / NAN
# ---------------------------------------------------------------------------


def test_resolve_primaries_preserves_na_nan_explicit_csv():
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primary-source", "explicit_csv",
        "--primaries", "AAPL,NA,MSFT,NAN,#comment,AAPL",
    ])
    res = runner.resolve_primaries(args)
    assert res["status"] == "ok"
    assert res["primary_source"] == "explicit_csv"
    assert res["primaries"] == ["AAPL", "NA", "MSFT", "NAN"]


def test_resolve_primaries_preserves_na_nan_file(tmp_path: Path):
    f = tmp_path / "p.txt"
    f.write_text("AAPL\nNA\nMSFT\nNAN\n#hidden\nAAPL\n", encoding="utf-8")
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primary-source", "file",
        "--primaries-file", str(f),
    ])
    res = runner.resolve_primaries(args)
    assert res["status"] == "ok"
    assert res["primaries"] == ["AAPL", "NA", "MSFT", "NAN"]


# ---------------------------------------------------------------------------
# 5. No hidden full-universe fallback
# ---------------------------------------------------------------------------


def test_no_hidden_full_universe_fallback_explicit_csv():
    args = runner.parse_args([
        "--secondaries", "SPY", "--primary-source", "explicit_csv",
    ])
    res = runner.resolve_primaries(args)
    assert res["status"] == "refused"
    assert res["primaries"] == []


def test_no_hidden_full_universe_fallback_file():
    args = runner.parse_args([
        "--secondaries", "SPY", "--primary-source", "file",
    ])
    res = runner.resolve_primaries(args)
    assert res["status"] == "refused"


def test_no_hidden_full_universe_fallback_signal_library_dir():
    args = runner.parse_args([
        "--secondaries", "SPY", "--primary-source", "signal_library_dir",
    ])
    res = runner.resolve_primaries(args)
    assert res["status"] == "refused"


def test_signal_library_dir_uses_suffix_regex(tmp_path: Path):
    # Tickers with underscores in their name (e.g., BRK_B) must parse cleanly.
    (tmp_path / "AAPL_stable_v1_0_0.pkl").write_bytes(b"x")
    (tmp_path / "BRK_B_stable_v1_2_3.pkl").write_bytes(b"x")
    (tmp_path / "not_a_library.txt").write_bytes(b"x")
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primary-source", "signal_library_dir",
        "--signal-library-dir", str(tmp_path),
    ])
    res = runner.resolve_primaries(args)
    assert res["status"] == "ok"
    assert "AAPL" in res["primaries"]
    assert "BRK_B" in res["primaries"]


def test_impact_xlsx_does_not_require_primaries(tmp_path: Path):
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primary-source", "impact_xlsx",
        "--impact-xlsx-dir", str(tmp_path),
    ])
    res = runner.resolve_primaries(args)
    assert res["status"] == "ok"
    assert res["primaries"] == []
    assert res["primary_source"] == "impact_xlsx"


# ---------------------------------------------------------------------------
# 6-8. Write-gate refusals
# ---------------------------------------------------------------------------


def _refused_main(argv):
    """Run main with conflict-free env and assert refused without engine call."""
    engine_calls: list = []

    def fake_engine(*a, **k):
        engine_calls.append((a, k))
        return {"status": "ok", "secondary": a[1], "run_dir": None}

    rc = runner.main(
        argv=argv,
        engine_callable=fake_engine,
        process_conflict_checker=_no_conflict,
    )
    return rc, engine_calls


def test_write_requires_allow_network_fetch(capsys):
    rc, engine_calls = _refused_main([
        "--secondaries", "SPY",
        "--primary-source", "impact_xlsx",
        "--write",
        "--duration-budget-minutes", "240",
        "--operator-budget-label", "smoke",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc != 0
    assert payload["status"] == "refused"
    assert "network_fetch_required_but_not_authorized" in payload[
        "preflight_issues"
    ]
    assert engine_calls == []


def test_write_requires_duration_budget(capsys):
    rc, engine_calls = _refused_main([
        "--secondaries", "SPY",
        "--primary-source", "impact_xlsx",
        "--write",
        "--allow-network-fetch",
        "--operator-budget-label", "smoke",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc != 0
    assert payload["status"] == "refused"
    assert "duration_budget_required" in payload["preflight_issues"]
    assert engine_calls == []


def test_write_requires_operator_budget_label(capsys):
    rc, engine_calls = _refused_main([
        "--secondaries", "SPY",
        "--primary-source", "impact_xlsx",
        "--write",
        "--allow-network-fetch",
        "--duration-budget-minutes", "240",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc != 0
    assert payload["status"] == "refused"
    assert "operator_budget_label_required" in payload["preflight_issues"]
    assert engine_calls == []


# ---------------------------------------------------------------------------
# 9. Dry-run does not call engine
# ---------------------------------------------------------------------------


def test_dry_run_does_not_call_engine(capsys):
    def boom_engine(*a, **k):
        raise AssertionError("engine must not be called in dry-run")

    rc = runner.main(
        argv=[
            "--secondaries", "SPY", "--primary-source", "impact_xlsx",
        ],
        engine_callable=boom_engine,
        process_conflict_checker=_no_conflict,
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 0
    assert payload["status"] == "dry_run"
    assert payload["would_call_engine"] is False


# ---------------------------------------------------------------------------
# 10. Process-conflict blocks write
# ---------------------------------------------------------------------------


def test_process_conflict_blocks_write(capsys, tmp_path: Path):
    def fake_engine(*a, **k):
        raise AssertionError("engine must not be called when blocked")

    def conflict_checker(write_requested=False):
        return {
            "status": "blocked",
            "conflicts": ["pid=12345 cmd=python stackbuilder.py"],
            "queried_via": "fake",
            "error": None,
        }

    rc = runner.main(
        argv=[
            "--secondaries", "SPY",
            "--primary-source", "impact_xlsx",
            "--outdir", str(tmp_path / "out"),
            "--write",
            "--allow-network-fetch",
            "--duration-budget-minutes", "240",
            "--operator-budget-label", "smoke",
        ],
        engine_callable=fake_engine,
        process_conflict_checker=conflict_checker,
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc != 0
    assert payload["status"] == "blocked_process_conflict"


# ---------------------------------------------------------------------------
# 11. Stdout is a single JSON object
# ---------------------------------------------------------------------------


def test_stdout_is_single_json_object(capsys):
    rc = runner.main(
        argv=[
            "--secondaries", "SPY", "--primary-source", "impact_xlsx",
        ],
        engine_callable=lambda *a, **k: {"status": "ok"},
        process_conflict_checker=_no_conflict,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    # JSON-parseable, single object, single trailing newline
    assert isinstance(payload, dict)
    assert captured.out.rstrip("\n").startswith("{")
    assert captured.out.endswith("\n")
    assert rc == 0


# ---------------------------------------------------------------------------
# 12. Engine stdout is captured (does not pollute runner stdout)
# ---------------------------------------------------------------------------


def test_engine_stdout_is_captured_with_fake_callable(tmp_path: Path):
    """execute_run is called directly with a fake engine that prints to
    stdout; the captured output must end up in the per-secondary record,
    NOT on sys.stdout.
    """
    args = _args_with(
        secondaries="SPY",
        primary_source="impact_xlsx",
        impact_xlsx_dir=str(tmp_path),
        outdir=str(tmp_path / "out"),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="fake-c-smoke",
        update_selected=False,
        no_progress=True,
    )

    def noisy_engine(args_ns, secondary, primaries=None):
        print("RAW_ENGINE_NOISE")
        return {
            "status": "ok",
            "secondary": secondary,
            "run_dir": None,
            "elapsed_seconds": 0.1,
            "captured_stdout_tail": "ENGINE_NOISE_LINE\n[PHASE2] noise\n",
            "error": None,
        }

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf):
        with redirect_stdout(out_buf):
            result = runner.execute_run(
                args, ["SPY"],
                primaries_resolution={
                    "status": "ok", "primary_source": "impact_xlsx",
                    "primaries": [], "primary_count": 0,
                    "source_path": str(tmp_path), "issues": [],
                },
                engine_callable=noisy_engine,
                selection_updater=lambda *a, **k: {
                    "status": "skipped",
                    "selected_build_path": None,
                    "pinned_path": None,
                    "payload": None,
                    "issues": [],
                },
            )
    assert result["status"] == "ok"
    assert out_buf.getvalue() == ""
    assert "RAW_ENGINE_NOISE" in err_buf.getvalue()
    assert result["per_secondary_results"][0]["captured_stdout_tail"] == (
        "ENGINE_NOISE_LINE\n[PHASE2] noise\nRAW_ENGINE_NOISE\n"
    )


# ---------------------------------------------------------------------------
# 13. SystemExit → per-secondary error, not process exit
# ---------------------------------------------------------------------------


def test_systemexit_becomes_per_secondary_error(tmp_path: Path):
    args = _args_with(
        secondaries="SPY",
        primary_source="impact_xlsx",
        impact_xlsx_dir=str(tmp_path),
        outdir=str(tmp_path / "out"),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="fake",
        no_progress=True,
    )

    def exit_engine(args_ns, secondary, primaries=None):
        raise SystemExit("[FATAL] simulated fatal in engine")

    err = io.StringIO()
    with redirect_stderr(err):
        result = runner.execute_run(
            args, ["SPY"],
            primaries_resolution={
                "status": "ok", "primary_source": "impact_xlsx",
                "primaries": [], "primary_count": 0,
                "source_path": None, "issues": [],
            },
            engine_callable=exit_engine,
        )
    assert result["status"] == "failed"
    assert result["per_secondary_results"][0]["status"] == "error"
    assert "SystemExit" in (result["per_secondary_results"][0]["error"] or "")


# ---------------------------------------------------------------------------
# 14. Per-secondary continuation
# ---------------------------------------------------------------------------


def test_per_secondary_continuation(tmp_path: Path):
    args = _args_with(
        secondaries="AAA,BBB",
        primary_source="impact_xlsx",
        impact_xlsx_dir=str(tmp_path),
        outdir=str(tmp_path / "out"),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="fake",
        no_progress=True,
    )

    seen: list[str] = []

    def flaky_engine(args_ns, secondary, primaries=None):
        seen.append(secondary)
        if secondary == "AAA":
            raise RuntimeError("simulated mid-batch failure")
        return {
            "status": "ok",
            "secondary": secondary,
            "run_dir": None,
            "elapsed_seconds": 0.05,
            "captured_stdout_tail": "",
            "error": None,
        }

    err = io.StringIO()
    with redirect_stderr(err):
        result = runner.execute_run(
            args, ["AAA", "BBB"],
            primaries_resolution={
                "status": "ok", "primary_source": "impact_xlsx",
                "primaries": [], "primary_count": 0,
                "source_path": None, "issues": [],
            },
            engine_callable=flaky_engine,
        )
    assert seen == ["AAA", "BBB"]
    assert result["status"] == "partial"
    statuses = [r["status"] for r in result["per_secondary_results"]]
    assert statuses == ["error", "ok"]


# ---------------------------------------------------------------------------
# 15. selected_build.json atomic write
# ---------------------------------------------------------------------------


def test_selected_build_json_atomic_write(tmp_path: Path):
    args = _args_with(
        secondaries="SPY",
        outdir=str(tmp_path),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="fake",
        update_selected=True,
    )

    res = runner.default_selection_updater(
        args, "SPY", _FAKE_RUN_SUMMARY, dry_run=False,
    )
    assert res["status"] == "written"
    selected_path = Path(res["selected_build_path"])
    assert selected_path.exists()
    partial = selected_path.with_name(
        f"{selected_path.stem}.runner_partial{selected_path.suffix}"
    )
    assert not partial.exists()
    payload = json.loads(selected_path.read_text(encoding="utf-8"))
    # Required schema keys.
    for key in (
        "schema_version", "secondary", "selected_run_id", "selected_run_dir",
        "selected_k", "total_capture", "sharpe_ratio", "row_count",
        "created_at", "selected_at", "selection_policy", "operator_pinned",
        "source_manifest_path", "runner_version",
    ):
        assert key in payload, f"missing {key!r}"


# ---------------------------------------------------------------------------
# 16. selected_build.pinned.json blocks auto-update
# ---------------------------------------------------------------------------


def test_selected_build_pinned_blocks_auto_update(tmp_path: Path):
    args = _args_with(
        secondaries="SPY",
        outdir=str(tmp_path),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="fake",
        update_selected=True,
    )
    secondary_dir = tmp_path / "SPY"
    secondary_dir.mkdir(parents=True, exist_ok=True)
    pinned = secondary_dir / "selected_build.pinned.json"
    pinned.write_text(json.dumps({"operator_pinned": True}), encoding="utf-8")

    res = runner.default_selection_updater(
        args, "SPY", _FAKE_RUN_SUMMARY, dry_run=False,
    )
    assert res["status"] == "blocked_by_pin"
    selected = secondary_dir / "selected_build.json"
    assert not selected.exists()


# ---------------------------------------------------------------------------
# 17. --pin-build writes pinned manifest
# ---------------------------------------------------------------------------


def test_pin_build_writes_pinned_manifest(tmp_path: Path):
    args = _args_with(
        secondaries="SPY",
        outdir=str(tmp_path),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="fake",
        update_selected=False,
        pin_build="phase-c-smoke-pin",
    )
    res = runner.default_selection_updater(
        args, "SPY", _FAKE_RUN_SUMMARY, dry_run=False,
    )
    assert res["status"] == "written"
    pinned_path = Path(res["pinned_path"])
    assert pinned_path.exists()
    partial = pinned_path.with_name(
        f"{pinned_path.stem}.runner_partial{pinned_path.suffix}"
    )
    assert not partial.exists()
    payload = json.loads(pinned_path.read_text(encoding="utf-8"))
    assert payload["operator_pinned"] is True
    assert payload["operator_pin_label"] == "phase-c-smoke-pin"


# ---------------------------------------------------------------------------
# 18. Selection policy comparator (Phase 6I-73: total_capture → latest)
# Sharpe is no longer a tiebreaker.
# ---------------------------------------------------------------------------


def test_selected_build_policy_total_capture_then_latest_no_sharpe_tiebreaker():
    same_k_old = {
        "selected_k": 3, "total_capture": 99.0, "sharpe_ratio": 9.0,
        "created_at": "2026-05-19T00:00:00Z",
    }
    same_k_new = {
        "selected_k": 3, "total_capture": 4.0, "sharpe_ratio": 1.0,
        "created_at": "2026-05-20T00:00:00Z",
    }
    # Same K across dates: latest run wins before cross-K comparison.
    assert runner.select_build_per_policy([same_k_old, same_k_new]) is same_k_new

    a = {
        "selected_k": 3, "total_capture": 4.0, "sharpe_ratio": 1.0,
        "created_at": "2026-05-19T00:00:00Z",
    }
    b = {
        "selected_k": 6, "total_capture": 5.0, "sharpe_ratio": 0.8,
        "created_at": "2026-05-20T00:00:00Z",
    }
    # Different K, highest total_capture wins.
    assert runner.select_build_per_policy([a, b]) is b

    # Phase 6I-73: with total_capture tied between b (2026-05-20) and
    # c (2026-05-18), the LATEST candidate wins. Sharpe is NOT used
    # as a tiebreaker; c has a much higher Sharpe but loses because
    # b is more recent.
    c = {
        "selected_k": 4, "total_capture": 5.0, "sharpe_ratio": 1.5,
        "created_at": "2026-05-18T00:00:00Z",
    }
    assert runner.select_build_per_policy([b, c]) is b

    # Within tolerance + latest beats earlier: d (latest) wins over b.
    d = {
        "selected_k": 5, "total_capture": 5.0, "sharpe_ratio": 1.5,
        "created_at": "2026-05-21T00:00:00Z",
    }
    assert runner.select_build_per_policy([b, d]) is d

    # Operator pin wins unconditionally even with worse metrics.
    pinned = {
        "selected_k": 1, "total_capture": -100.0, "sharpe_ratio": -5.0,
        "created_at": "2026-05-15T00:00:00Z",
        "operator_pinned": True,
    }
    assert runner.select_build_per_policy([a, b, c, d, pinned]) is pinned


def test_selected_build_policy_does_not_use_sharpe_tiebreaker():
    """Phase 6I-73 regression: when two candidates tie on
    total_capture and only Sharpe differs, the candidate with the
    higher Sharpe must NOT be preferred; latest-wins applies.
    """
    earlier_high_sharpe = {
        "selected_k": 4, "total_capture": 5.0, "sharpe_ratio": 9.99,
        "created_at": "2026-05-15T00:00:00Z",
    }
    later_low_sharpe = {
        "selected_k": 4, "total_capture": 5.0, "sharpe_ratio": 0.01,
        "created_at": "2026-05-20T00:00:00Z",
    }
    # Same K so the K-collapse picks the latest one anyway. Use
    # different K to exercise the cross-K branch.
    earlier_high_sharpe["selected_k"] = 3
    later_low_sharpe["selected_k"] = 5
    assert runner.select_build_per_policy(
        [earlier_high_sharpe, later_low_sharpe]
    ) is later_low_sharpe


# ---------------------------------------------------------------------------
# 19. build_stackbuilder_args_namespace mirrors Phase A defaults
# ---------------------------------------------------------------------------


def test_build_stackbuilder_args_namespace_matches_dash_defaults():
    args = runner.parse_args(["--secondaries", "SPY"])
    primaries_res = {
        "status": "ok", "primary_source": "impact_xlsx",
        "primaries": [], "primary_count": 0,
        "source_path": None, "issues": [],
    }
    ns = runner.build_stackbuilder_args_namespace(args, "SPY", primaries_res)
    assert ns.top_n == 20
    assert ns.bottom_n == 20
    assert ns.max_k == 6
    assert ns.exhaustive_k == 4
    assert ns.min_trigger_days == 30
    assert ns.sharpe_eps == pytest.approx(0.01)
    assert ns.seed_by == "total_capture"
    # optimize_by="auto" → resolves to seed_by ("total_capture")
    assert ns.optimize_by == "total_capture"
    assert ns.search == "beam"
    assert ns.beam_width == 12
    assert ns.allow_decreasing is False
    assert ns.prefer_impact_xlsx is True


# ---------------------------------------------------------------------------
# 20. stackbuilder is lazy-imported
# ---------------------------------------------------------------------------


def test_stackbuilder_is_lazy_imported(capsys):
    # The runner module's AST guard (test_no_toplevel_dangerous_imports)
    # already proves stackbuilder is not a top-level import. This
    # behavioral test additionally confirms that the runner's dry-run
    # main does not TRANSITIVELY pull stackbuilder into sys.modules.
    # We test the delta rather than the absolute presence because
    # other test files in the session (e.g. cli_deprecations) may
    # legitimately import stackbuilder for their own coverage.
    before = "stackbuilder" in sys.modules

    rc = runner.main(
        argv=["--secondaries", "SPY", "--primary-source", "impact_xlsx"],
        engine_callable=lambda *a, **k: {"status": "ok"},
        process_conflict_checker=_no_conflict,
    )
    capsys.readouterr()
    assert rc == 0
    after = "stackbuilder" in sys.modules
    # Dry-run main MUST NOT introduce stackbuilder into sys.modules
    # if it wasn't already there.
    assert (after and before) or (not after and not before), (
        "runner dry-run main triggered a transitive stackbuilder import"
    )


# ---------------------------------------------------------------------------
# Extras: refused-on-no-secondaries + summarize_stackbuilder_run_dir basics
# ---------------------------------------------------------------------------


def test_main_refuses_without_secondaries(capsys):
    rc = runner.main(
        argv=["--secondaries", ""],
        engine_callable=lambda *a, **k: {"status": "ok"},
        process_conflict_checker=_no_conflict,
    )
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc != 0
    assert payload["status"] == "refused"
    assert "secondaries_resolution_refused" in payload["preflight_issues"]


def test_summarize_stackbuilder_run_dir_handles_missing(tmp_path: Path):
    res = runner.summarize_stackbuilder_run_dir(str(tmp_path / "nope"))
    assert res["exists"] is False
    assert "run_dir_missing" in res["issues"]


def test_summarize_stackbuilder_run_dir_parses_manifest(tmp_path: Path):
    rd = tmp_path / "FAKE"
    rd.mkdir()
    (rd / "run_manifest.json").write_text(
        json.dumps({
            "started_at": "2026-05-21T00:00:00Z",
            "finished_at": "2026-05-21T00:05:00Z",
            "run_id": "FAKE-RUN",
        }),
        encoding="utf-8",
    )
    (rd / "summary.json").write_text(
        json.dumps({
            "best_sharpe": 1.0, "best_capture": 2.0,
            "primaries_tested": 7,
        }),
        encoding="utf-8",
    )
    (rd / "combo_k=1.json").write_text("{}", encoding="utf-8")
    (rd / "combo_k=2.json").write_text("{}", encoding="utf-8")
    res = runner.summarize_stackbuilder_run_dir(str(rd))
    assert res["exists"] is True
    assert res["best_sharpe"] == 1.0
    assert res["best_total_capture"] == 2.0
    assert res["k_level_counts"] == {"1": 1, "2": 1}


# ---------------------------------------------------------------------------
# Phase 6I-71: progress-path isolation
# ---------------------------------------------------------------------------


def _isolated_run_plan(tmp_path: Path, **arg_overrides) -> dict:
    """Build a runner plan with all preconditions for ``status="ready"``
    so the per-secondary progress path is observable in the plan
    output. Tests must still NOT call any engine.
    """
    argv = [
        "--secondaries", arg_overrides.pop("secondaries", "SPY"),
        "--primary-source", "impact_xlsx",
        "--impact-xlsx-dir", str(tmp_path),
        "--outdir", str(tmp_path / "out"),
    ]
    for k, v in arg_overrides.items():
        if v is None or v is False:
            continue
        flag = "--" + k.replace("_", "-")
        if v is True:
            argv.append(flag)
        else:
            argv.extend([flag, str(v)])
    args = runner.parse_args(argv)
    secondaries_resolution = runner.resolve_secondaries(args)
    primaries_resolution = runner.resolve_primaries(args)
    conflict = {
        "status": "ok", "conflicts": [], "queried_via": "fake",
        "error": None,
    }
    return runner.build_run_plan(
        args,
        secondaries_resolution=secondaries_resolution,
        primaries_resolution=primaries_resolution,
        process_conflict_result=conflict,
    )


def test_plan_per_secondary_includes_progress_path_under_outdir_progress(
    tmp_path: Path,
):
    plan = _isolated_run_plan(tmp_path, secondaries="SPY,QQQ")
    assert plan["status"] == "dry_run"
    per = plan["per_secondary_plan"]
    assert [p["secondary"] for p in per] == ["SPY", "QQQ"]
    expected_progress_dir = str(tmp_path / "out" / "_progress")
    for entry in per:
        assert entry["effective_progress_dir"] == expected_progress_dir
        # Planned per-secondary progress path lives under the
        # effective progress dir.
        assert entry["planned_progress_path"].startswith(
            expected_progress_dir + os.sep
        ) or entry["planned_progress_path"].startswith(
            expected_progress_dir + "/"
        )
    # Dry-run must NOT have created the progress directory.
    assert not (tmp_path / "out" / "_progress").exists()
    # Effective config carries the resolved dir explicitly.
    assert plan["effective_config"]["effective_progress_dir"] == (
        expected_progress_dir
    )
    assert plan["effective_config"]["progress_dir"] is None


def test_phase_c_safety_default_progress_not_in_canonical_root(tmp_path: Path):
    """Explicit negative assertion: when --outdir is set to a non-
    canonical isolated dir, the default planned progress_path must
    NOT default into ``output/stackbuilder/_progress``.
    """
    plan = _isolated_run_plan(tmp_path, secondaries="SPY")
    per = plan["per_secondary_plan"]
    assert per, "plan must include at least one secondary entry"
    # Hard guard against the historical engine default leaking through.
    canonical_default = os.path.join("output", "stackbuilder", "_progress")
    for entry in per:
        assert canonical_default not in entry["planned_progress_path"], (
            f"isolated --outdir leaked progress to canonical: "
            f"{entry['planned_progress_path']!r}"
        )
        assert canonical_default not in entry["effective_progress_dir"], (
            f"effective_progress_dir leaked to canonical: "
            f"{entry['effective_progress_dir']!r}"
        )


def test_execute_run_passes_isolated_progress_path_to_namespace(
    tmp_path: Path,
):
    captured: list[SimpleNamespace] = []

    def recording_engine(args_ns, secondary, primaries=None):
        captured.append(args_ns)
        return {
            "status": "ok",
            "secondary": secondary,
            "run_dir": None,
            "elapsed_seconds": 0.01,
            "captured_stdout_tail": "",
            "error": None,
        }

    args = _args_with(
        secondaries="SPY",
        primary_source="impact_xlsx",
        impact_xlsx_dir=str(tmp_path),
        outdir=str(tmp_path / "out"),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="phase-c-fake",
        no_progress=True,
    )

    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        result = runner.execute_run(
            args, ["SPY"],
            primaries_resolution={
                "status": "ok", "primary_source": "impact_xlsx",
                "primaries": [], "primary_count": 0,
                "source_path": str(tmp_path), "issues": [],
            },
            engine_callable=recording_engine,
            selection_updater=lambda *a, **k: {
                "status": "skipped",
                "selected_build_path": None,
                "pinned_path": None,
                "payload": None,
                "issues": [],
            },
        )
    assert result["status"] == "ok"
    assert len(captured) == 1
    ns = captured[0]
    expected_progress_root = str(tmp_path / "out" / "_progress")
    assert ns.progress_path
    assert ns.progress_path.startswith(expected_progress_root + os.sep) or \
        ns.progress_path.startswith(expected_progress_root + "/")
    # Canonical-root negative assertion.
    assert "output" + os.sep + "stackbuilder" + os.sep + "_progress" not in (
        ns.progress_path
    )
    # Per-secondary result records the same path.
    rec = result["per_secondary_results"][0]
    assert rec["progress_path"] == ns.progress_path


def test_explicit_progress_dir_overrides_default(tmp_path: Path):
    captured: list[SimpleNamespace] = []

    def recording_engine(args_ns, secondary, primaries=None):
        captured.append(args_ns)
        return {
            "status": "ok",
            "secondary": secondary,
            "run_dir": None,
            "elapsed_seconds": 0.01,
            "captured_stdout_tail": "",
            "error": None,
        }

    explicit_dir = tmp_path / "custom" / "progress"
    args = _args_with(
        secondaries="SPY",
        primary_source="impact_xlsx",
        impact_xlsx_dir=str(tmp_path),
        outdir=str(tmp_path / "out"),
        progress_dir=str(explicit_dir),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="fake",
        no_progress=True,
    )

    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        runner.execute_run(
            args, ["SPY"],
            primaries_resolution={
                "status": "ok", "primary_source": "impact_xlsx",
                "primaries": [], "primary_count": 0,
                "source_path": str(tmp_path), "issues": [],
            },
            engine_callable=recording_engine,
        )
    assert len(captured) == 1
    pp = captured[0].progress_path
    assert pp.startswith(str(explicit_dir) + os.sep) or \
        pp.startswith(str(explicit_dir) + "/")
    # Not under <outdir>/_progress when --progress-dir overrides.
    assert (str(tmp_path / "out" / "_progress")) not in pp


def test_repeated_same_secondary_execute_run_yields_distinct_progress_paths(
    tmp_path: Path,
):
    seen: list[str] = []

    def recording_engine(args_ns, secondary, primaries=None):
        seen.append(args_ns.progress_path)
        return {
            "status": "ok", "secondary": secondary, "run_dir": None,
            "elapsed_seconds": 0.0, "captured_stdout_tail": "", "error": None,
        }

    args = _args_with(
        secondaries="SPY",
        primary_source="impact_xlsx",
        impact_xlsx_dir=str(tmp_path),
        outdir=str(tmp_path / "out"),
        write=True,
        allow_network_fetch=True,
        duration_budget_minutes=10,
        operator_budget_label="fake",
        no_progress=True,
    )
    primaries_res = {
        "status": "ok", "primary_source": "impact_xlsx",
        "primaries": [], "primary_count": 0,
        "source_path": str(tmp_path), "issues": [],
    }
    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        runner.execute_run(
            args, ["SPY", "SPY", "SPY"],
            primaries_resolution=primaries_res,
            engine_callable=recording_engine,
        )
    assert len(seen) == 3
    assert len(set(seen)) == 3, f"expected distinct paths, got {seen!r}"


def test_main_write_path_emits_single_json_with_noisy_engine(
    tmp_path: Path, capsys,
):
    """Phase 6I-71 regression for the stdout contract: even when the
    engine_callable prints raw bytes to its (now-redirected) stdout,
    the runner's own stdout must still be exactly one JSON object —
    no engine noise before, inside, or after the JSON.
    """
    def noisy_engine(args_ns, secondary, primaries=None):
        # The execute_run wrapper redirects stdout into a StringIO;
        # this print goes there, then is mirrored to stderr.
        print("RAW_ENGINE_NOISE_PRE_JSON")
        return {
            "status": "ok",
            "secondary": secondary,
            "run_dir": None,
            "elapsed_seconds": 0.01,
            "captured_stdout_tail": "",
            "error": None,
        }

    rc = runner.main(
        argv=[
            "--secondaries", "SPY",
            "--primary-source", "impact_xlsx",
            "--impact-xlsx-dir", str(tmp_path),
            "--outdir", str(tmp_path / "out"),
            "--write",
            "--allow-network-fetch",
            "--duration-budget-minutes", "10",
            "--operator-budget-label", "fake",
            "--no-progress",
        ],
        engine_callable=noisy_engine,
        process_conflict_checker=_no_conflict,
    )
    captured = capsys.readouterr()
    assert rc == 0
    # Stdout must parse as exactly one JSON object with no bare-text
    # contamination before / between / after.
    out = captured.out
    assert out.rstrip("\n").startswith("{")
    assert out.endswith("\n")
    payload = json.loads(out)
    assert isinstance(payload, dict)
    rec = payload["per_secondary_results"][0]
    # The engine's print MUST land in the JSON-quoted
    # captured_stdout_tail field (the documented capture surface),
    # NOT as bare text outside the JSON.
    assert "RAW_ENGINE_NOISE_PRE_JSON" in (rec["captured_stdout_tail"] or "")
    # And the bare-text guard: scrubbing the captured tail value from
    # raw stdout leaves a parseable JSON shell only — no stray noise.
    tail_value = rec["captured_stdout_tail"] or ""
    bare_stdout_without_tail = out.replace(
        json.dumps(tail_value)[1:-1], ""  # tail's JSON-escaped body
    )
    assert "RAW_ENGINE_NOISE_PRE_JSON" not in bare_stdout_without_tail
    # Engine noise mirrored to stderr for live observability.
    assert "RAW_ENGINE_NOISE_PRE_JSON" in captured.err
    # And the per-secondary progress_path must be under the isolated
    # outdir, not canonical output/stackbuilder/_progress.
    assert "output" + os.sep + "stackbuilder" + os.sep + "_progress" not in (
        rec["progress_path"]
    )


def test_safe_secondary_filename_preserves_caret_and_normalizes_dot():
    assert runner._safe_secondary_filename("BRK.B") == "BRK_B"
    assert runner._safe_secondary_filename("^GSPC") == "^GSPC"
    assert runner._safe_secondary_filename("a/b") == "a_b"
    assert runner._safe_secondary_filename("a b") == "a_b"


# ---------------------------------------------------------------------------
# Phase 6I-73: Sharpe is no longer a supported selection metric
# ---------------------------------------------------------------------------


def test_runner_cli_refuses_seed_by_sharpe():
    with pytest.raises(SystemExit):
        runner.parse_args(["--secondaries", "SPY", "--seed-by", "sharpe"])


def test_runner_cli_refuses_optimize_by_sharpe():
    with pytest.raises(SystemExit):
        runner.parse_args(["--secondaries", "SPY", "--optimize-by", "sharpe"])


def test_runner_cli_accepts_only_total_capture_for_seed_and_optimize():
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--seed-by", "total_capture",
        "--optimize-by", "total_capture",
    ])
    assert args.seed_by == "total_capture"
    assert args.optimize_by == "total_capture"


def test_build_stackbuilder_args_namespace_resolves_optimize_to_total_capture():
    args = runner.parse_args(["--secondaries", "SPY"])
    # default optimize_by is "auto" → must resolve to total_capture
    ns = runner.build_stackbuilder_args_namespace(
        args, "SPY",
        primaries_resolution={
            "status": "ok", "primary_source": "impact_xlsx",
            "primaries": [], "primary_count": 0,
            "source_path": None, "issues": [],
        },
    )
    assert ns.seed_by == "total_capture"
    assert ns.optimize_by == "total_capture"


def test_runner_selection_policy_label_is_total_capture_then_latest():
    """Selection policy label must not advertise Sharpe."""
    assert "sharpe" not in runner.SELECTION_POLICY.lower()
    assert "total_capture" in runner.SELECTION_POLICY.lower()


# ---------------------------------------------------------------------------
# Phase 6I-78: runner --k-patience pass-through
#
# The runner previously hardcoded ``k_patience=1`` in the engine
# Namespace without exposing it on the CLI surface. Phase 6I-78 adds
# the explicit ``--k-patience`` flag so the operator can pin the
# traversal-stop behavior from the runner without modifying
# stackbuilder.py.
# ---------------------------------------------------------------------------


def test_parse_args_k_patience_default_is_one():
    """Default ``args.k_patience`` is ``1``, preserving the runner's
    prior hardcoded engine-namespace value when the flag is omitted."""
    args = runner.parse_args(["--secondaries", "SPY"])
    assert args.k_patience == 1


def test_parse_args_k_patience_accepts_explicit_one():
    """Explicit ``--k-patience 1`` round-trips through argparse."""
    args = runner.parse_args(["--secondaries", "SPY", "--k-patience", "1"])
    assert args.k_patience == 1


def test_parse_args_k_patience_accepts_higher_value():
    """An explicit larger value (3) round-trips."""
    args = runner.parse_args(["--secondaries", "SPY", "--k-patience", "3"])
    assert args.k_patience == 3


def test_effective_config_includes_k_patience_default():
    """``_effective_config`` reports the default k_patience value."""
    args = runner.parse_args(["--secondaries", "SPY"])
    cfg = runner._effective_config(args)
    assert cfg["k_patience"] == 1


def test_effective_config_includes_k_patience_explicit():
    """``_effective_config`` reports the explicit k_patience value."""
    args = runner.parse_args(["--secondaries", "SPY", "--k-patience", "3"])
    cfg = runner._effective_config(args)
    assert cfg["k_patience"] == 3


def test_build_stackbuilder_args_namespace_passes_k_patience_default():
    """The engine Namespace receives the default k_patience=1."""
    args = runner.parse_args(["--secondaries", "SPY"])
    ns = runner.build_stackbuilder_args_namespace(
        args, "SPY",
        primaries_resolution={
            "status": "ok", "primary_source": "impact_xlsx",
            "primaries": [], "primary_count": 0,
            "source_path": None, "issues": [],
        },
    )
    assert ns.k_patience == 1


def test_build_stackbuilder_args_namespace_passes_k_patience_explicit():
    """The engine Namespace receives the operator-passed k_patience."""
    args = runner.parse_args(["--secondaries", "SPY", "--k-patience", "3"])
    ns = runner.build_stackbuilder_args_namespace(
        args, "SPY",
        primaries_resolution={
            "status": "ok", "primary_source": "impact_xlsx",
            "primaries": [], "primary_count": 0,
            "source_path": None, "issues": [],
        },
    )
    assert ns.k_patience == 3


def test_dry_run_plan_effective_config_includes_k_patience_and_allow_decreasing():
    """``build_run_plan`` exposes both traversal controls in the
    dry-run JSON's ``effective_config`` block."""
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--allow-decreasing",
        "--k-patience", "1",
        "--duration-budget-minutes", "1440",
        "--operator-budget-label", "phase-6i-78-test",
    ])
    plan = runner.build_run_plan(
        args,
        secondaries_resolution={
            "status": "ok",
            "secondaries": ["SPY"],
        },
        primaries_resolution={
            "status": "ok",
            "primary_source": "impact_xlsx",
            "primary_count": 1,
            "primaries": ["AAPL"],
        },
    )
    cfg = plan["effective_config"]
    assert cfg["k_patience"] == 1
    assert cfg["allow_decreasing"] is True


def test_allow_decreasing_default_unchanged_by_k_patience_addition():
    """Adding --k-patience must not change --allow-decreasing default
    behavior."""
    args_default = runner.parse_args(["--secondaries", "SPY"])
    assert args_default.allow_decreasing is False
    args_set = runner.parse_args(["--secondaries", "SPY", "--allow-decreasing"])
    assert args_set.allow_decreasing is True
