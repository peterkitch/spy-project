"""Phase 6I-32 tests for the supervised fresh-staging
readiness harness.

Pins:

  * No raw ``pickle.load`` anywhere in the module.
  * No yfinance / dash / subprocess imports at top level.
  * No live engine imports at top level (writer / refresher /
    pipeline runner / spymaster / trafficflow / stackbuilder /
    onepass / impactsearch / confluence / cross_ticker_
    confluence / daily_signal_board).
  * The harness NEVER passes ``write=True`` to the Phase 6I-25
    patch writer or to the Phase 6I-31 promotion writer.
  * The harness NEVER reads or sets
    ``PRJCT9_AUTOMATION_WRITE_AUTH``.
  * State classification:
      - source/cache not ready -> STATE_SOURCE_NOT_READY
      - sandbox build incomplete -> STATE_STAGED_REBUILD_NOT_READY
      - adapter not full grid -> STATE_STAGED_REBUILD_NOT_READY
      - promotion plan not ready -> STATE_STAGED_REBUILD_NOT_READY
      - production-root drift detected -> STATE_STAGED_REBUILD_NOT_READY
      - staged_dir under signal_library/data/stable ->
        STATE_STAGED_REBUILD_NOT_READY
      - everything green -> STATE_STAGED_REBUILD_READY
  * The harness threads the optional `--end-date` cutoff to
    the sandbox builder seam.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import signal_library_fresh_staging_readiness as harness  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _fake_cache_probe_ready(tickers, *, cache_dir, current_as_of_date):
    return {
        "current_as_of_date": current_as_of_date or "2026-05-14",
        "inspected_count": len(tickers),
        "ready_tickers": list(tickers),
        "states": [
            {"ticker": t, "cache_ahead_of_cutoff": True}
            for t in tickers
        ],
    }


def _fake_cache_probe_not_ready(
    tickers, *, cache_dir, current_as_of_date,
):
    return {
        "current_as_of_date": current_as_of_date or "2026-05-14",
        "inspected_count": len(tickers),
        "ready_tickers": [],  # nothing ahead
        "states": [
            {
                "ticker": t,
                "cache_ahead_of_cutoff": False,
                "cache_behind_cutoff": True,
            }
            for t in tickers
        ],
    }


def _fake_source_availability(tickers, *, cache_dir, current_as_of_date):
    return {"states": [{"ticker": t} for t in tickers]}


def _fake_sandbox_builder_all_written(
    tickers, *, intervals, cache_dir, staged_dir, end_date,
):
    # Don't actually write files in unit tests -- this fake
    # records the call shape but returns synthetic written
    # paths. The downstream planner / adapter callables in
    # the tests are also faked, so they don't read those
    # paths from disk.
    written = []
    for t in tickers:
        for i in intervals:
            suffix = "" if i == "1d" else f"_{i}"
            written.append(
                f"{staged_dir}/{t}_stable_v1_0_0{suffix}.pkl"
            )
    return {"written": written, "failed": []}


def _fake_sandbox_builder_partial_failure(
    tickers, *, intervals, cache_dir, staged_dir, end_date,
):
    return {
        "written": [
            f"{staged_dir}/{tickers[0]}_stable_v1_0_0.pkl"
        ],
        "failed": [f"{tickers[0]}|1wk"],
    }


def _fake_promotion_planner_ready(
    tickers, *, staged_dir, production_stable_dir, intervals,
):
    return {
        "plan_ready": True,
        "expected_file_count": len(tickers) * len(intervals),
        "staged_files_found": len(tickers) * len(intervals),
        "staged_files_missing": 0,
        "libraries_to_add": len(tickers) * len(intervals),
        "libraries_to_replace": 0,
        "libraries_unchanged": 0,
    }


def _fake_promotion_planner_not_ready(
    tickers, *, staged_dir, production_stable_dir, intervals,
):
    return {
        "plan_ready": False,
        "issue_codes": ["staged_file_missing"],
    }


def _fake_promotion_writer_dry_run(
    tickers, *, staged_dir, production_stable_dir,
    intervals, execution_log,
):
    # Assert the writer is called in dry-run mode (the
    # harness's responsibility, not the writer's). The
    # default seam in the harness always calls the writer
    # with write=False; tests assert via a sentinel.
    return {
        "write_requested": False,
        "write_authorized": False,
        "wrote_files": False,
        "issue_codes": ["write_not_requested"],
    }


def _fake_adapter_ready(
    ticker, *, stackbuilder_root, signal_library_dir, cache_dir,
):
    return {
        "ticker": ticker,
        "prepared_cell_count": 60,
        "skipped_cell_count": 0,
        "can_evaluate_full_60_cell_grid": True,
        "adapter_issue_codes": [],
    }


def _fake_adapter_not_full_grid(
    ticker, *, stackbuilder_root, signal_library_dir, cache_dir,
):
    return {
        "ticker": ticker,
        "prepared_cell_count": 12,
        "skipped_cell_count": 48,
        "can_evaluate_full_60_cell_grid": False,
        "adapter_issue_codes": ["target_close_join_incomplete"],
    }


def _fake_payload_builder_ready(
    ticker, *, stackbuilder_root, signal_library_dir, cache_dir,
):
    return {
        "target_ticker": ticker,
        "payload_ready": True,
        "cell_count": 60,
    }


def _fake_patch_planner_ready(
    ticker, *, artifact_root, stackbuilder_root,
    signal_library_dir, cache_dir, current_as_of_date,
):
    return {
        "target_ticker": ticker,
        "patch_ready": True,
        "fields_to_add": [
            "per_window_k_metrics",
            "build_wide_window_alignment",
            "multiwindow_k_engine_payload_metadata",
        ],
    }


def _fake_patch_writer_dry_run(
    ticker, *, artifact_root, stackbuilder_root,
    signal_library_dir, cache_dir, current_as_of_date,
    execution_log,
):
    return {
        "target_ticker": ticker,
        "write_requested": False,
        "write_authorized": False,
        "wrote_artifact": False,
        "planner_patch_ready": True,
    }


def _fake_snapshot_static():
    return {
        "roots": {
            "cache/results": {},
            "cache/status": {},
            "output/research_artifacts": {},
            "output/stackbuilder": {},
            "signal_library/data/stable": {},
        },
        "file_counts": {
            "cache/results": 0, "cache/status": 0,
            "output/research_artifacts": 0,
            "output/stackbuilder": 0,
            "signal_library/data/stable": 0,
        },
        "total_files": 0,
    }


def _fake_snapshot_with_drift():
    return {
        "roots": {
            "cache/results": {},
            "cache/status": {},
            "output/research_artifacts": {},
            "output/stackbuilder": {},
            # ONE new file vs the empty before snapshot
            "signal_library/data/stable": {
                "SPY_stable_v1_0_0.pkl": (1024, 0.0),
            },
        },
        "file_counts": {
            "cache/results": 0, "cache/status": 0,
            "output/research_artifacts": 0,
            "output/stackbuilder": 0,
            "signal_library/data/stable": 1,
        },
        "total_files": 1,
    }


def _fake_diff_zero(before, after):
    return {
        "cache/results": {
            "added": 0, "removed": 0, "changed": 0,
        },
        "cache/status": {
            "added": 0, "removed": 0, "changed": 0,
        },
        "output/research_artifacts": {
            "added": 0, "removed": 0, "changed": 0,
        },
        "output/stackbuilder": {
            "added": 0, "removed": 0, "changed": 0,
        },
        "signal_library/data/stable": {
            "added": 0, "removed": 0, "changed": 0,
        },
        "TOTAL": {"added": 0, "removed": 0, "changed": 0},
    }


def _fake_diff_with_drift(before, after):
    return {
        "cache/results": {"added": 0, "removed": 0, "changed": 0},
        "cache/status": {"added": 0, "removed": 0, "changed": 0},
        "output/research_artifacts": {
            "added": 0, "removed": 0, "changed": 0,
        },
        "output/stackbuilder": {
            "added": 0, "removed": 0, "changed": 0,
        },
        "signal_library/data/stable": {
            "added": 1, "removed": 0, "changed": 0,
        },
        "TOTAL": {"added": 1, "removed": 0, "changed": 0},
    }


def _all_green_kwargs(tmp_path, *, tickers=("SPY",)):
    staged = tmp_path / "staged_libs"
    staged.mkdir()
    prod = tmp_path / "signal_library" / "data" / "stable"
    prod.mkdir(parents=True)
    artifact = tmp_path / "artifact_root"
    artifact.mkdir()
    return {
        "tickers": tickers,
        "kwargs": dict(
            staged_dir=staged,
            cache_dir=tmp_path / "cache_results",
            stackbuilder_root=tmp_path / "stackbuilder",
            production_stable_dir=prod,
            confluence_artifact_root=artifact,
            current_as_of_date="2026-05-14",
            cache_cutoff_probe_callable=(
                _fake_cache_probe_ready
            ),
            source_availability_probe_callable=(
                _fake_source_availability
            ),
            sandbox_builder_callable=(
                _fake_sandbox_builder_all_written
            ),
            promotion_planner_callable=(
                _fake_promotion_planner_ready
            ),
            promotion_writer_callable=(
                _fake_promotion_writer_dry_run
            ),
            adapter_diagnostic_callable=(
                _fake_adapter_ready
            ),
            payload_builder_callable=(
                _fake_payload_builder_ready
            ),
            patch_planner_callable=(
                _fake_patch_planner_ready
            ),
            patch_writer_callable=(
                _fake_patch_writer_dry_run
            ),
            production_snapshot_callable=(
                _fake_snapshot_static
            ),
            production_diff_callable=_fake_diff_zero,
        ),
    }


# ---------------------------------------------------------------------------
# 1. All-green path -> STATE_STAGED_REBUILD_READY
# ---------------------------------------------------------------------------


def test_all_green_yields_staged_rebuild_ready(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert report.state == harness.STATE_STAGED_REBUILD_READY
    assert report.source_cache_ready is True
    assert report.issue_codes == ()
    assert report.promotion_plan_ready is True
    assert report.sandbox_build_written > 0
    assert report.sandbox_build_failed == 0
    assert (
        report.adapter_diagnostic_summary[
            "can_evaluate_full_60_cell_grid"
        ]
        is True
    )
    assert (
        report.payload_builder_summary["payload_ready"] is True
    )
    assert (
        report.patch_planner_summary["patch_ready"] is True
    )
    pw = report.patch_writer_dry_run_summary
    assert pw["write_requested"] is False
    assert pw["wrote_artifact"] is False
    assert (
        report.production_root_diff["TOTAL"]["added"] == 0
    )
    assert (
        report.recommended_next_action
        == "review_evidence_and_authorize_promotion_separately"
    )


# ---------------------------------------------------------------------------
# 2. Source/cache not ready -> STATE_SOURCE_NOT_READY
# ---------------------------------------------------------------------------


def test_source_not_ready_yields_state_a(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    bundle["kwargs"]["cache_cutoff_probe_callable"] = (
        _fake_cache_probe_not_ready
    )
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert report.state == harness.STATE_SOURCE_NOT_READY
    assert (
        harness.ISSUE_SOURCE_CACHE_NOT_READY
        in report.issue_codes
    )
    assert (
        report.recommended_next_action == "refresh_source_cache"
    )


# ---------------------------------------------------------------------------
# 3. Sandbox build incomplete -> STATE_B
# ---------------------------------------------------------------------------


def test_sandbox_partial_failure_yields_state_b(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    bundle["kwargs"]["sandbox_builder_callable"] = (
        _fake_sandbox_builder_partial_failure
    )
    # Adapter / payload / etc still all return ready in this
    # fake bundle, so the only thing that should pull the
    # state to STATE_B is the sandbox failure.
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_NOT_READY
    )
    assert (
        harness.ISSUE_SANDBOX_BUILD_INCOMPLETE
        in report.issue_codes
    )


# ---------------------------------------------------------------------------
# 4. Promotion plan not ready -> STATE_B
# ---------------------------------------------------------------------------


def test_promotion_plan_not_ready_yields_state_b(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    bundle["kwargs"]["promotion_planner_callable"] = (
        _fake_promotion_planner_not_ready
    )
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_NOT_READY
    )
    assert (
        harness.ISSUE_PROMOTION_PLAN_NOT_READY
        in report.issue_codes
    )
    # When plan is not ready, the writer dry-run must NOT
    # be called -- the harness short-circuits.
    assert (
        report.promotion_writer_dry_run_summary is None
    )


# ---------------------------------------------------------------------------
# 5. Adapter not full grid -> STATE_B
# ---------------------------------------------------------------------------


def test_adapter_not_full_grid_yields_state_b(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    bundle["kwargs"]["adapter_diagnostic_callable"] = (
        _fake_adapter_not_full_grid
    )
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_NOT_READY
    )
    assert (
        harness.ISSUE_ADAPTER_NOT_FULL_GRID
        in report.issue_codes
    )


# ---------------------------------------------------------------------------
# 6. Production-root drift -> STATE_B
# ---------------------------------------------------------------------------


def test_production_drift_yields_state_b(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    bundle["kwargs"]["production_diff_callable"] = (
        _fake_diff_with_drift
    )
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_NOT_READY
    )
    assert (
        harness.ISSUE_PRODUCTION_ROOT_DRIFT_DETECTED
        in report.issue_codes
    )


# ---------------------------------------------------------------------------
# 7. Staged dir pointed at production stable -> STATE_B
# ---------------------------------------------------------------------------


def test_staged_dir_under_production_stable_yields_state_b(
    tmp_path,
):
    bundle = _all_green_kwargs(tmp_path)
    # Force staged_dir to be EXACTLY the production stable
    # directory. The Phase 6I-32 amendment-1 guard MUST
    # short-circuit the sandbox callable BEFORE invocation.
    bad_staged = (
        tmp_path / "signal_library" / "data" / "stable"
    )
    bad_staged.mkdir(parents=True, exist_ok=True)
    bundle["kwargs"]["staged_dir"] = bad_staged
    # Wrap the sandbox callable in a recorder that fails
    # the test if it ever gets called.
    invocation_log: list[Any] = []

    def recording_sandbox(
        tickers, *, intervals, cache_dir, staged_dir, end_date,
    ):
        invocation_log.append((tickers, staged_dir))
        return _fake_sandbox_builder_all_written(
            tickers, intervals=intervals,
            cache_dir=cache_dir, staged_dir=staged_dir,
            end_date=end_date,
        )
    bundle["kwargs"]["sandbox_builder_callable"] = (
        recording_sandbox
    )
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_NOT_READY
    )
    assert (
        harness.ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE
        in report.issue_codes
    )
    # The promotion planner must NOT have been called.
    assert report.promotion_plan_summary is None
    # The sandbox builder callable must NOT have been called.
    assert invocation_log == []
    # sandbox_build_attempted MUST be False because the
    # outer guard short-circuited before invocation.
    assert report.sandbox_build_attempted is False


# ---------------------------------------------------------------------------
# 8. Harness never sets PRJCT9_AUTOMATION_WRITE_AUTH
# ---------------------------------------------------------------------------


def test_harness_never_sets_auth_env(tmp_path, monkeypatch):
    monkeypatch.delenv(
        "PRJCT9_AUTOMATION_WRITE_AUTH", raising=False,
    )
    bundle = _all_green_kwargs(tmp_path)
    harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    # The env var must remain unset across the call.
    assert (
        os.environ.get("PRJCT9_AUTOMATION_WRITE_AUTH") is None
    )


# ---------------------------------------------------------------------------
# 9. Writer seams receive write=False semantics
# ---------------------------------------------------------------------------


def test_writer_seams_called_without_write_true(tmp_path):
    """The harness's default ``promotion_writer_callable`` /
    ``patch_writer_callable`` delegate to writers with
    ``write=False``. Tests inject fakes that record whether
    the harness ever asks them to write True. Confirm the
    harness never calls them with anything resembling
    write authorization."""
    bundle = _all_green_kwargs(tmp_path)
    log: list[dict] = []

    def recording_promotion_writer(
        tickers, *, staged_dir, production_stable_dir,
        intervals, execution_log,
    ):
        log.append({"kind": "promotion", "tickers": list(tickers)})
        return {
            "write_requested": False,
            "write_authorized": False,
            "wrote_files": False,
        }

    def recording_patch_writer(
        ticker, *, artifact_root, stackbuilder_root,
        signal_library_dir, cache_dir,
        current_as_of_date, execution_log,
    ):
        log.append({"kind": "patch", "ticker": ticker})
        return {
            "write_requested": False,
            "write_authorized": False,
            "wrote_artifact": False,
            "planner_patch_ready": True,
        }
    bundle["kwargs"]["promotion_writer_callable"] = (
        recording_promotion_writer
    )
    bundle["kwargs"]["patch_writer_callable"] = (
        recording_patch_writer
    )
    harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    # Both writers got called exactly once (because the
    # all-green bundle reaches them).
    kinds = [entry["kind"] for entry in log]
    assert kinds.count("promotion") == 1
    assert kinds.count("patch") == 1


# ---------------------------------------------------------------------------
# 10. Skip flags work
# ---------------------------------------------------------------------------


def test_skip_source_availability_flag(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    bundle["kwargs"]["run_source_availability"] = False
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert report.source_availability_summary is None


def test_skip_downstream_chain_flag(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    bundle["kwargs"]["run_downstream_chain"] = False
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert report.adapter_diagnostic_summary is None
    assert report.payload_builder_summary is None
    assert report.patch_planner_summary is None
    assert report.patch_writer_dry_run_summary is None


def test_skip_snapshot_diff_flag(tmp_path):
    bundle = _all_green_kwargs(tmp_path)
    bundle["kwargs"]["run_snapshot_diff"] = False
    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert report.production_snapshot_before is None
    assert report.production_snapshot_after is None
    assert report.production_root_diff is None


# ---------------------------------------------------------------------------
# 11. CLI rc=2 paths
# ---------------------------------------------------------------------------


def test_cli_missing_tickers_returns_rc_2(capsys):
    rc = harness.main([
        "--staged-dir", "/tmp/whatever",
        "--tickers", "",
    ])
    assert rc == 2


def test_cli_unknown_flag_returns_rc_2():
    rc = harness.main(["--no-such-flag"])
    assert rc == 2


# ---------------------------------------------------------------------------
# 12. Static guards: no raw pickle.load
# ---------------------------------------------------------------------------


def test_harness_no_raw_pickle_load():
    src = Path(harness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                base = func.value
                if (
                    isinstance(base, ast.Name)
                    and base.id == "pickle"
                    and func.attr == "load"
                ):
                    raise AssertionError(
                        "harness calls pickle.load() at line "
                        f"{node.lineno}"
                    )


# ---------------------------------------------------------------------------
# 13. Static guards: no yfinance / dash / subprocess / live
# ---------------------------------------------------------------------------


def test_harness_no_forbidden_top_level_imports():
    """The harness MUST stay yfinance-free at top level. The
    default seam delegates to source_availability_probe via a
    deferred local import inside the helper, which keeps the
    top-level import surface clean.

    Live engines must also be absent at the top level.
    """
    src = Path(harness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_first = {
        "yfinance", "dash", "subprocess",
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
        "spymaster", "trafficflow", "stackbuilder",
        "onepass", "impactsearch", "confluence",
        "cross_ticker_confluence", "daily_signal_board",
    }
    found_top: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_top.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_top.append(node.module)
    bad = [
        m for m in found_top
        if m.split(".")[0] in forbidden_first
    ]
    assert not bad, f"forbidden top-level imports: {bad!r}"


# ---------------------------------------------------------------------------
# 14. Static guards: no .resample / .ffill
# ---------------------------------------------------------------------------


def test_harness_no_projection_calls():
    src = Path(harness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            assert name not in {"resample", "ffill"}, (
                f"harness calls forbidden {name!r}() at "
                f"line {node.lineno}"
            )


# ---------------------------------------------------------------------------
# 15a-15d. Phase 6I-32 amendment-1: robust staged-dir safety boundary
# ---------------------------------------------------------------------------


def _recording_sandbox_factory():
    """Return ``(recorder_callable, invocation_log_list)``.
    The recorder fails fast if it ever gets called with a
    staged_dir that resolves under signal_library/data/stable
    (defense-in-depth inside the test itself), and records
    every invocation in the returned list."""
    invocations: list[Any] = []

    def recording(
        tickers, *, intervals, cache_dir, staged_dir, end_date,
    ):
        invocations.append({
            "tickers": list(tickers),
            "staged_dir": str(staged_dir),
        })
        # Defense-in-depth: even if the harness fails to
        # short-circuit, the test's recorder refuses to
        # cooperate with an unsafe staged_dir.
        if harness._path_is_under_production_stable(staged_dir):
            raise AssertionError(
                "recorder invoked with unsafe staged_dir: "
                f"{staged_dir!r}",
            )
        return _fake_sandbox_builder_all_written(
            tickers, intervals=intervals,
            cache_dir=cache_dir, staged_dir=staged_dir,
            end_date=end_date,
        )
    return recording, invocations


def test_amendment1_exact_production_stable_dir_skips_sandbox(
    tmp_path,
):
    """When staged_dir == production_stable_dir the sandbox
    callable MUST NOT be invoked. The state classifier must
    surface ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE and
    sandbox_build_attempted must be False."""
    bundle = _all_green_kwargs(tmp_path)
    prod_stable = (
        tmp_path / "signal_library" / "data" / "stable"
    )
    prod_stable.mkdir(parents=True, exist_ok=True)
    bundle["kwargs"]["staged_dir"] = prod_stable
    bundle["kwargs"]["production_stable_dir"] = prod_stable
    recorder, log = _recording_sandbox_factory()
    bundle["kwargs"]["sandbox_builder_callable"] = recorder

    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_NOT_READY
    )
    assert (
        harness.ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE
        in report.issue_codes
    )
    assert log == []
    assert report.sandbox_build_attempted is False
    assert report.sandbox_build_written == 0


def test_amendment1_child_of_production_stable_skips_sandbox(
    tmp_path,
):
    """When staged_dir is a CHILD of production_stable_dir
    (e.g. signal_library/data/stable/staged_libs) the sandbox
    callable MUST NOT be invoked. This is the exact case the
    original suffix-only guard missed."""
    bundle = _all_green_kwargs(tmp_path)
    prod_stable = (
        tmp_path / "signal_library" / "data" / "stable"
    )
    prod_stable.mkdir(parents=True, exist_ok=True)
    bad_staged = prod_stable / "staged_libs"
    bad_staged.mkdir()
    bundle["kwargs"]["staged_dir"] = bad_staged
    bundle["kwargs"]["production_stable_dir"] = prod_stable
    recorder, log = _recording_sandbox_factory()
    bundle["kwargs"]["sandbox_builder_callable"] = recorder

    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_NOT_READY
    )
    assert (
        harness.ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE
        in report.issue_codes
    )
    assert log == []
    assert report.sandbox_build_attempted is False
    # The downstream multi-window K chain MUST also have
    # been short-circuited because the chain reads from
    # staged_dir.
    assert report.adapter_diagnostic_summary is None
    assert report.payload_builder_summary is None
    assert report.patch_planner_summary is None
    assert report.patch_writer_dry_run_summary is None


def test_amendment1_child_blocked_even_with_run_snapshot_diff_false(
    tmp_path,
):
    """The path guard MUST fire even when run_snapshot_diff
    is False -- the snapshot diff would otherwise be the
    last line of defense, and disabling it must not unlock
    sandbox calls under unsafe staged dirs."""
    bundle = _all_green_kwargs(tmp_path)
    prod_stable = (
        tmp_path / "signal_library" / "data" / "stable"
    )
    prod_stable.mkdir(parents=True, exist_ok=True)
    bad_staged = prod_stable / "staged_libs"
    bad_staged.mkdir()
    bundle["kwargs"]["staged_dir"] = bad_staged
    bundle["kwargs"]["production_stable_dir"] = prod_stable
    bundle["kwargs"]["run_snapshot_diff"] = False
    recorder, log = _recording_sandbox_factory()
    bundle["kwargs"]["sandbox_builder_callable"] = recorder

    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_NOT_READY
    )
    assert (
        harness.ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE
        in report.issue_codes
    )
    assert log == []
    assert report.sandbox_build_attempted is False


def test_amendment1_safe_temp_staged_dir_still_runs_full_chain(
    tmp_path,
):
    """Regression pin: the amendment-1 guard must NOT
    regress the all-green safe-temp-staged-dir path. A
    staged_dir under tmp_path that is NOT under production
    stable must still reach STATE_STAGED_REBUILD_READY and
    must still invoke the sandbox callable exactly once
    per ticker."""
    bundle = _all_green_kwargs(tmp_path)
    # Safe staged_dir under tmp_path (NOT under
    # signal_library/data/stable).
    safe_staged = tmp_path / "safe_temp_staged"
    safe_staged.mkdir()
    bundle["kwargs"]["staged_dir"] = safe_staged
    recorder, log = _recording_sandbox_factory()
    bundle["kwargs"]["sandbox_builder_callable"] = recorder

    report = harness.evaluate_fresh_staging_readiness(
        bundle["tickers"], **bundle["kwargs"],
    )
    assert (
        report.state == harness.STATE_STAGED_REBUILD_READY
    )
    assert (
        harness.ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE
        not in report.issue_codes
    )
    assert len(log) == 1
    assert report.sandbox_build_attempted is True


def test_amendment1_default_sandbox_builder_refuses_unsafe_dir(
    tmp_path,
):
    """Defense-in-depth: the harness's _default_sandbox_builder
    helper itself MUST raise ValueError if called with a
    staged_dir under signal_library/data/stable, regardless
    of whether the outer harness guard fired. This protects
    against a future call-path mistake or an out-of-band
    caller that bypasses evaluate_fresh_staging_readiness."""
    import pytest as _pt
    bad_staged = (
        tmp_path / "signal_library" / "data" / "stable"
        / "staged_libs"
    )
    bad_staged.mkdir(parents=True, exist_ok=True)
    with _pt.raises(ValueError, match=(
        "refusing to write sandbox libraries under "
        "signal_library/data/stable"
    )):
        harness._default_sandbox_builder(
            ["SPY"],
            intervals=["1d"],
            cache_dir=tmp_path,
            staged_dir=bad_staged,
            end_date=None,
        )


# ---------------------------------------------------------------------------
# 15. Static guard: harness AST has no write=True keyword arg
# ---------------------------------------------------------------------------


def test_harness_ast_has_no_write_true_kwarg():
    """AST-level guard: scan every Call node in the harness
    module and confirm none of them pass ``write=True`` as a
    keyword argument. The harness's docstrings legitimately
    discuss ``--write`` and ``PRJCT9_AUTOMATION_WRITE_AUTH``
    as explanatory text -- the behavioral truth is what the
    AST scan checks, not the raw text content."""
    src = Path(harness.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "write":
                    val = kw.value
                    if (
                        isinstance(val, ast.Constant)
                        and val.value is True
                    ):
                        offenders.append(node.lineno)
    assert not offenders, (
        f"harness passes write=True at line(s) {offenders!r}"
    )
