"""Phase 6I-31 tests for the signal-library stable promotion path.

Pins the planner + writer contract:

Planner
  - plan_ready=True when every required staged file exists +
    passes schema (dates / signals / close all present and
    equal length).
  - plan_ready=False when a required interval is missing,
    when staged close is missing, when dates/signals/close
    lengths disagree, or when the staged artifact is
    unloadable.
  - Path guard fires when production_stable_dir is NOT under
    signal_library/data/stable.
  - No raw pickle.load anywhere in the module (AST + B12).
  - No yfinance / dash / subprocess imports.

Writer
  - Dry-run (write=False) never mutates.
  - write=True without env var never mutates.
  - env var without --write never mutates.
  - Plan not_ready blocks even with full auth.
  - Production-path guard blocks even with full auth + ready
    plan.
  - Authorized + ready + guard-pass actually mutates a
    tmp_path stable root, copying both PKL and the
    .pkl.manifest.json sidecar atomically.
  - Writer-side revalidation independently rejects a stale
    plan whose staged file was tampered with between
    planning and writing.
  - Execution log JSONL appends one row per invocation.
  - No raw pickle.load anywhere in the module.
  - No yfinance / dash / subprocess imports.
"""
from __future__ import annotations

import ast
import json
import os
import pickle
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import provenance_manifest as pm  # noqa: E402
import signal_library_stable_promotion_planner as planner  # noqa: E402
import signal_library_stable_promotion_writer as promoter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_library(
    *, ticker: str, interval: str, n: int = 5,
) -> dict[str, Any]:
    """Build a Phase 6I-30-shape library dict: dates / signals /
    close all present + equal length."""
    dates = [f"2026-01-{i+1:02d}" for i in range(n)]
    signals = ["Buy" if i % 2 == 0 else "Short" for i in range(n)]
    close = [100.0 + i for i in range(n)]
    return {
        "ticker": ticker,
        "interval": interval,
        "engine_version": "1.0.0",
        "price_source": "Close",
        "dates": list(dates),
        "date_index": list(dates),
        "signals": list(signals),
        "primary_signals": list(signals),
        "close": list(close),
    }


def _make_staged_dir(
    tmp_path: Path,
    *,
    tickers: list[str],
    intervals: list[str],
    library_factory=_make_library,
    attach_sidecar: bool = True,
) -> Path:
    """Build a staged signal-library directory containing one
    library per (ticker, interval). Uses the central
    ``provenance_manifest.attach_manifest`` helper so the
    written PKL carries an embedded manifest AND (when
    ``attach_sidecar=True``) a .manifest.json sidecar."""
    staged = tmp_path / "staged"
    staged.mkdir()
    for ticker in tickers:
        for interval in intervals:
            lib = library_factory(ticker=ticker, interval=interval)
            if interval == "1d":
                filename = f"{ticker}_stable_v1_0_0.pkl"
            else:
                filename = f"{ticker}_stable_v1_0_0_{interval}.pkl"
            filepath = staged / filename
            params = {
                "MAX_SMA_DAY": 114,
                "price_source": "Close",
                "interval": interval,
                "auto_adjust": False,
                "t1_skip_policy": "fetch_t1_skip",
            }
            if attach_sidecar:
                pm.attach_manifest(
                    lib, filepath,
                    artifact_type="interval_signal_library",
                    ticker=ticker,
                    interval=interval,
                    params=params,
                    engine_version="1.0.0",
                )
            with open(filepath, "wb") as fh:
                pickle.dump(lib, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return staged


def _make_production_stable_root(tmp_path: Path) -> Path:
    """Build a tmp_path-rooted production stable directory whose
    tail matches the Phase 6I-31 path guard suffix."""
    root = tmp_path / "signal_library" / "data" / "stable"
    root.mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# 1. Planner: plan_ready=True when all staged files present + schema-valid
# ---------------------------------------------------------------------------


def test_planner_ready_when_all_staged_present(tmp_path):
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d", "1wk"],
    )
    prod = _make_production_stable_root(tmp_path)
    plan = planner.plan_signal_library_stable_promotion(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d", "1wk"),
    )
    assert plan.plan_ready is True
    assert plan.expected_file_count == 2
    assert plan.staged_files_found == 2
    assert plan.staged_files_missing == 0
    assert plan.libraries_to_add == 2
    assert plan.issue_codes == ()


# ---------------------------------------------------------------------------
# 2. Planner: missing interval blocks plan_ready
# ---------------------------------------------------------------------------


def test_planner_blocks_missing_interval(tmp_path):
    # Only build 1d; planner is asked about 1d AND 1wk.
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    prod = _make_production_stable_root(tmp_path)
    plan = planner.plan_signal_library_stable_promotion(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d", "1wk"),
    )
    assert plan.plan_ready is False
    assert plan.staged_files_missing == 1
    assert (
        planner.ISSUE_STAGED_FILE_MISSING
        in plan.issue_codes
    )


# ---------------------------------------------------------------------------
# 3. Planner: missing close field blocks plan_ready
# ---------------------------------------------------------------------------


def test_planner_blocks_missing_close(tmp_path):
    def _no_close(*, ticker, interval, n=5):
        lib = _make_library(
            ticker=ticker, interval=interval, n=n,
        )
        lib.pop("close")
        return lib
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1wk"],
        library_factory=_no_close,
    )
    prod = _make_production_stable_root(tmp_path)
    plan = planner.plan_signal_library_stable_promotion(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1wk",),
    )
    assert plan.plan_ready is False
    state = plan.per_library_states[0]
    assert state.schema_ok is False
    assert (
        planner.REASON_STAGED_FILE_SCHEMA_INVALID
        in state.schema_issue_codes
    )
    assert (
        planner.ISSUE_STAGED_FILE_SCHEMA_INVALID
        in plan.issue_codes
    )


# ---------------------------------------------------------------------------
# 4. Planner: dates / signals / close length mismatch blocks
# ---------------------------------------------------------------------------


def test_planner_blocks_length_mismatch(tmp_path):
    def _length_mismatch(*, ticker, interval, n=5):
        lib = _make_library(
            ticker=ticker, interval=interval, n=n,
        )
        # Trim signals by one.
        lib["signals"] = lib["signals"][:-1]
        lib["primary_signals"] = lib["primary_signals"][:-1]
        return lib
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1mo"],
        library_factory=_length_mismatch,
    )
    prod = _make_production_stable_root(tmp_path)
    plan = planner.plan_signal_library_stable_promotion(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1mo",),
    )
    assert plan.plan_ready is False
    assert (
        planner.ISSUE_STAGED_FILE_SCHEMA_INVALID
        in plan.issue_codes
    )


# ---------------------------------------------------------------------------
# 5. Planner: unloadable artifact blocks
# ---------------------------------------------------------------------------


def test_planner_blocks_unloadable_artifact(tmp_path):
    # Write garbage bytes to a staged path so the central loader
    # cannot parse it.
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "SPY_stable_v1_0_0.pkl").write_bytes(
        b"not a real pickle",
    )
    prod = _make_production_stable_root(tmp_path)
    plan = planner.plan_signal_library_stable_promotion(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d",),
    )
    assert plan.plan_ready is False
    # The central loader returns None on a parse failure
    # without raising, so it surfaces as "unreadable" rather
    # than "load_failed" -- both are accepted here.
    assert any(
        code in plan.issue_codes
        for code in (
            planner.ISSUE_STAGED_FILE_UNREADABLE,
            planner.ISSUE_STAGED_FILE_LOAD_FAILED,
        )
    )


# ---------------------------------------------------------------------------
# 6. Planner: path guard fires when production_stable_dir wrong
# ---------------------------------------------------------------------------


def test_planner_path_guard_fires_outside_stable_suffix(tmp_path):
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    # production_stable_dir points to a path that does NOT end
    # in signal_library/data/stable.
    bad_prod = tmp_path / "bogus_root"
    bad_prod.mkdir()
    plan = planner.plan_signal_library_stable_promotion(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=bad_prod,
        intervals=("1d",),
    )
    assert plan.plan_ready is False
    assert (
        planner.ISSUE_UNEXPECTED_PRODUCTION_ROOT
        in plan.issue_codes
    )


# ---------------------------------------------------------------------------
# 7. Writer: dry-run never mutates
# ---------------------------------------------------------------------------


def test_writer_dry_run_never_mutates(tmp_path, monkeypatch):
    monkeypatch.delenv(promoter.ENV_VAR_NAME, raising=False)
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d", "1wk"],
    )
    prod = _make_production_stable_root(tmp_path)
    # Sanity: prod is empty before.
    assert list(prod.iterdir()) == []
    result = promoter.promote_signal_libraries(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d", "1wk"),
        write=False,
    )
    assert result.write_requested is False
    assert result.write_authorized is False
    assert result.wrote_files is False
    assert promoter.ISSUE_WRITE_NOT_REQUESTED in result.issue_codes
    # Prod still empty.
    assert list(prod.iterdir()) == []


# ---------------------------------------------------------------------------
# 8. Writer: --write without env var refuses
# ---------------------------------------------------------------------------


def test_writer_write_without_env_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv(promoter.ENV_VAR_NAME, raising=False)
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    prod = _make_production_stable_root(tmp_path)
    result = promoter.promote_signal_libraries(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d",),
        write=True,
    )
    assert result.write_requested is True
    assert result.write_authorized is False
    assert result.wrote_files is False
    assert (
        promoter.ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID
        in result.issue_codes
    )
    assert list(prod.iterdir()) == []


# ---------------------------------------------------------------------------
# 9. Writer: env var without --write refuses
# ---------------------------------------------------------------------------


def test_writer_env_without_write_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv(
        promoter.ENV_VAR_NAME,
        promoter.ENV_VAR_REQUIRED_VALUE,
    )
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    prod = _make_production_stable_root(tmp_path)
    result = promoter.promote_signal_libraries(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d",),
        write=False,
    )
    assert result.write_requested is False
    assert result.write_authorized is False
    assert result.wrote_files is False
    assert (
        promoter.ISSUE_WRITE_NOT_REQUESTED in result.issue_codes
    )
    assert list(prod.iterdir()) == []


# ---------------------------------------------------------------------------
# 10. Writer: plan not_ready blocks even with full auth
# ---------------------------------------------------------------------------


def test_writer_blocks_on_plan_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv(
        promoter.ENV_VAR_NAME,
        promoter.ENV_VAR_REQUIRED_VALUE,
    )
    # Only build 1d; ask for 1d AND 1wk so plan is missing 1wk.
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    prod = _make_production_stable_root(tmp_path)
    result = promoter.promote_signal_libraries(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d", "1wk"),
        write=True,
    )
    assert result.write_requested is True
    assert result.write_authorized is True
    assert result.plan_ready is False
    assert result.wrote_files is False
    assert promoter.ISSUE_PLAN_NOT_READY in result.issue_codes
    # No prod files written.
    prod_files = sorted(p.name for p in prod.iterdir())
    assert prod_files == []


# ---------------------------------------------------------------------------
# 11. Writer: production-path guard blocks even with full auth
# ---------------------------------------------------------------------------


def test_writer_production_path_guard_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv(
        promoter.ENV_VAR_NAME,
        promoter.ENV_VAR_REQUIRED_VALUE,
    )
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    bad_prod = tmp_path / "not_signal_library_stable"
    bad_prod.mkdir()
    result = promoter.promote_signal_libraries(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=bad_prod,
        intervals=("1d",),
        write=True,
    )
    assert result.wrote_files is False
    assert (
        promoter.ISSUE_UNEXPECTED_PRODUCTION_ROOT
        in result.issue_codes
    )
    assert list(bad_prod.iterdir()) == []


# ---------------------------------------------------------------------------
# 12. Writer: authorized + ready + guard-pass actually mutates tmp_path
# ---------------------------------------------------------------------------


def test_writer_authorized_promotes_files_and_sidecars(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(
        promoter.ENV_VAR_NAME,
        promoter.ENV_VAR_REQUIRED_VALUE,
    )
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d", "1wk"],
        attach_sidecar=True,
    )
    prod = _make_production_stable_root(tmp_path)
    # Confirm staged sidecars exist.
    assert (
        staged / "SPY_stable_v1_0_0.pkl.manifest.json"
    ).exists()
    result = promoter.promote_signal_libraries(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d", "1wk"),
        write=True,
    )
    assert result.write_requested is True
    assert result.write_authorized is True
    assert result.plan_ready is True
    assert result.wrote_files is True
    # Both PKLs landed.
    assert (prod / "SPY_stable_v1_0_0.pkl").exists()
    assert (prod / "SPY_stable_v1_0_0_1wk.pkl").exists()
    # Both sidecars landed.
    assert (
        prod / "SPY_stable_v1_0_0.pkl.manifest.json"
    ).exists()
    assert (
        prod / "SPY_stable_v1_0_0_1wk.pkl.manifest.json"
    ).exists()
    assert len(result.files_added) == 2
    assert len(result.sidecars_copied) == 2
    assert (
        result.recommended_next_action
        == promoter.ACTION_PROMOTION_COMPLETE
    )


# ---------------------------------------------------------------------------
# 13. Writer-side revalidation catches stale plan
# ---------------------------------------------------------------------------


def test_writer_side_revalidation_blocks_tampered_staged_file(
    tmp_path, monkeypatch,
):
    """The writer must NOT trust the planner result -- after the
    planner runs, if a staged file has been corrupted, the
    writer's independent re-load must catch it and refuse
    mutation."""
    monkeypatch.setenv(
        promoter.ENV_VAR_NAME,
        promoter.ENV_VAR_REQUIRED_VALUE,
    )
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    prod = _make_production_stable_root(tmp_path)
    # Inject a fake planner_callable that ALWAYS reports
    # plan_ready=True regardless of the staged content. Then
    # corrupt the staged file BEFORE the writer is called.
    fake_state = planner.PerLibraryPromotionState(
        ticker="SPY", interval="1d",
        staged_path=str(staged / "SPY_stable_v1_0_0.pkl"),
        production_path=str(prod / "SPY_stable_v1_0_0.pkl"),
        staged_exists=True,
        schema_ok=True,
        schema_issue_codes=(),
        staged_sha256="fake_hash",
        production_exists=False,
        production_sha256=None,
        production_outcome=planner.OUTCOME_ADD,
        has_sidecar=False,
    )
    fake_plan = planner.SignalLibraryStablePromotionPlan(
        generated_at="2026-05-13T00:00:00+00:00",
        staged_dir=str(staged),
        production_stable_dir=str(prod),
        target_tickers=("SPY",),
        intervals=("1d",),
        expected_file_count=1,
        staged_files_found=1,
        staged_files_missing=0,
        libraries_to_add=1,
        libraries_to_replace=0,
        libraries_unchanged=0,
        plan_ready=True,
        issue_codes=(),
        per_library_states=(fake_state,),
    )
    # Corrupt the staged file.
    (staged / "SPY_stable_v1_0_0.pkl").write_bytes(b"corrupt")

    def fake_planner(*args, **kwargs):
        return fake_plan
    result = promoter.promote_signal_libraries(
        ["SPY"],
        staged_dir=staged,
        production_stable_dir=prod,
        intervals=("1d",),
        write=True,
        planner_callable=fake_planner,
    )
    assert result.wrote_files is False
    assert (
        promoter.ISSUE_WRITER_REVALIDATION_FAILED
        in result.issue_codes
    )
    # No production file written.
    assert not (prod / "SPY_stable_v1_0_0.pkl").exists()


# ---------------------------------------------------------------------------
# 14. Writer: execution log appends one JSONL row per invocation
# ---------------------------------------------------------------------------


def test_writer_execution_log_appends_one_row_per_invocation(
    tmp_path,
):
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    prod = _make_production_stable_root(tmp_path)
    log_path = tmp_path / "promotion_log.jsonl"
    # Two dry-run invocations.
    for _ in range(2):
        promoter.promote_signal_libraries(
            ["SPY"],
            staged_dir=staged,
            production_stable_dir=prod,
            intervals=("1d",),
            write=False,
            execution_log=log_path,
        )
    assert log_path.exists()
    rows = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 2
    for row in rows:
        parsed = json.loads(row)
        assert parsed["wrote_files"] is False
        assert (
            "write_not_requested" in parsed["issue_codes"]
        )


# ---------------------------------------------------------------------------
# 15. Planner has no raw pickle.load
# ---------------------------------------------------------------------------


def test_planner_has_no_raw_pickle_load():
    src = Path(planner.__file__).read_text(encoding="utf-8")
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
                        "planner calls pickle.load() "
                        f"at line {node.lineno}"
                    )


def test_writer_has_no_raw_pickle_load():
    src = Path(promoter.__file__).read_text(encoding="utf-8")
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
                        "writer calls pickle.load() "
                        f"at line {node.lineno}"
                    )


# ---------------------------------------------------------------------------
# 16. No yfinance / dash / subprocess / live engine imports
# ---------------------------------------------------------------------------


_FORBIDDEN_FIRST = {
    "yfinance", "dash", "subprocess",
    "daily_board_automation_writer",
    "signal_engine_cache_refresher",
    "confluence_pipeline_runner",
    "daily_board_automation_executor",
    "spymaster", "trafficflow", "stackbuilder",
    "onepass", "impactsearch", "confluence",
    "cross_ticker_confluence", "daily_signal_board",
}


def _scan_module_for_forbidden(mod):
    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad = [
        m for m in found
        if m.split(".")[0] in _FORBIDDEN_FIRST
    ]
    return bad


def test_planner_forbidden_imports_absent():
    assert _scan_module_for_forbidden(planner) == []


def test_writer_forbidden_imports_absent():
    assert _scan_module_for_forbidden(promoter) == []


# ---------------------------------------------------------------------------
# 17. CLI rc=2/3 + JSON output sanity
# ---------------------------------------------------------------------------


def test_planner_cli_missing_tickers_rc_2(capsys):
    rc = planner.main(["--staged-dir", "/tmp"])
    assert rc == 2


def test_writer_cli_missing_tickers_rc_2(capsys):
    rc = promoter.main(["--staged-dir", "/tmp"])
    assert rc == 2


def test_writer_cli_dry_run_emits_json(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.delenv(promoter.ENV_VAR_NAME, raising=False)
    staged = _make_staged_dir(
        tmp_path, tickers=["SPY"], intervals=["1d"],
    )
    prod = _make_production_stable_root(tmp_path)
    rc = promoter.main([
        "--tickers", "SPY",
        "--staged-dir", str(staged),
        "--production-stable-dir", str(prod),
        "--intervals", "1d",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["wrote_files"] is False
    assert (
        "write_not_requested" in payload["issue_codes"]
    )
