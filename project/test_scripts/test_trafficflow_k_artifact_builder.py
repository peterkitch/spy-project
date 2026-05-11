"""Phase 6D-1 tests for trafficflow_k_artifact_builder.

Pins the contract:

  - discovers the latest StackBuilder seed-run dir for a target
  - loads combo_leaderboard.xlsx in the schema the existing
    StackBuilder writer emits
  - iterates K=1..12 rows
  - returns clean issue codes for missing run / missing leaderboard
    / malformed leaderboard
  - write=False builds in memory and writes nothing
  - write=True writes one artifact per K row at K-distinguished
    paths (no collision)
  - readiness sees full K coverage once 12 artifacts exist
  - readiness still emits missing_multitimeframe_trafficflow_bridge
    after this PR (the multi-timeframe projection is Phase 6D-2)
  - no yfinance import, no trafficflow.py import, no
    spymaster.py import in the builder module
"""
from __future__ import annotations

import ast
import json
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import confluence_pipeline_readiness as cpr  # noqa: E402
import research_artifacts as ra  # noqa: E402
import trafficflow_k_artifact_builder as tkb  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    stack_root = tmp_path / "stackbuilder"
    sig_dir = tmp_path / "siglib"
    for d in (cache_dir, artifact_root, stack_root, sig_dir):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache_dir,
        "artifact_root": artifact_root,
        "stackbuilder_root": stack_root,
        "signal_library_dir": sig_dir,
    }


def _write_cache_pkl(
    cache_dir: Path, ticker: str, *,
    last_date: str = "2026-05-08",
    closes: Optional[list[float]] = None,
    active_pairs: Optional[list[str]] = None,
) -> Path:
    """Write a minimal Spymaster-cache PKL with the
    ``preprocessed_data`` + ``active_pairs`` shape the readers
    accept."""
    import pandas as pd

    n = len(closes) if closes is not None else 10
    if closes is None:
        closes = [100.0 + i for i in range(n)]
    if active_pairs is None:
        active_pairs = ["Buy 3,2"] * (n - 1) + ["Short 5,1"]
    dates = pd.date_range(end=last_date, periods=n, freq="D")
    df = pd.DataFrame({"Close": closes}, index=dates)
    payload = {
        "preprocessed_data": df,
        "active_pairs": active_pairs,
    }
    safe = ticker.replace("^", "_")
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    cache_dir.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_leaderboard(
    run_dir: Path,
    *,
    rows: list[dict[str, Any]],
    columns: Optional[list[str]] = None,
) -> Path:
    """Write a combo_leaderboard.xlsx fixture using the same
    column shape the existing StackBuilder writer emits."""
    import pandas as pd

    run_dir.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = [
            "K", "Trigger Days", "Total Capture (%)",
            "Sharpe Ratio", "p-Value", "Members",
        ]
    df = pd.DataFrame(rows, columns=columns)
    out = run_dir / tkb.LEADERBOARD_FILENAME
    df.to_excel(out, index=False)
    return out


def _full_k_leaderboard_rows(
    members_str: str = "['AAA[D]', 'BBB[D]']",
) -> list[dict[str, Any]]:
    """12 rows, K=1..12, all sharing the same members for
    fixture simplicity."""
    return [
        {
            "K": k,
            "Trigger Days": 100 + k,
            "Total Capture (%)": 10.0 + k,
            "Sharpe Ratio": 0.1 + k * 0.01,
            "p-Value": 0.05,
            "Members": members_str,
        }
        for k in range(1, 13)
    ]


def _make_seed_run(
    stack_root: Path, target: str, *,
    seed_name: str = "seedTC__AAA-D_BBB-D",
    rows: Optional[list[dict[str, Any]]] = None,
    columns: Optional[list[str]] = None,
    write_leaderboard: bool = True,
) -> Path:
    """Create ``stack_root/<TARGET>/<seed_name>/`` and (optionally)
    drop a combo_leaderboard.xlsx fixture into it."""
    safe_target = target.replace("^", "_")
    target_dir = stack_root / safe_target
    target_dir.mkdir(parents=True, exist_ok=True)
    run_dir = target_dir / seed_name
    run_dir.mkdir(parents=True, exist_ok=True)
    if write_leaderboard:
        _write_leaderboard(
            run_dir,
            rows=rows if rows is not None else _full_k_leaderboard_rows(),
            columns=columns,
        )
    return run_dir


def _full_pipeline_fixtures(
    tmp_path: Path,
    *,
    target: str = "SPY",
    members: tuple[str, ...] = ("AAA", "BBB"),
) -> dict[str, Path]:
    """Build a complete fixture set: target cache + per-member
    caches + a seed-run leaderboard with K=1..12. Returns the
    directory bundle."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(dirs["cache_dir"], target)
    for m in members:
        _write_cache_pkl(dirs["cache_dir"], m)
    members_str = (
        "[" + ", ".join(f"'{m}[D]'" for m in members) + "]"
    )
    _make_seed_run(
        dirs["stackbuilder_root"], target,
        rows=_full_k_leaderboard_rows(members_str=members_str),
    )
    return dirs


# ---------------------------------------------------------------------------
# Static-import sanity checks
# ---------------------------------------------------------------------------


def test_builder_module_has_no_forbidden_imports():
    """The Phase 6D-1 builder is offline / read-only by design.
    It must NOT import yfinance or any of the live engine
    modules (trafficflow.py / spymaster.py / impactsearch.py /
    onepass.py / dash)."""
    tree = ast.parse(
        Path(tkb.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance", "trafficflow", "spymaster", "impactsearch",
        "onepass", "confluence", "cross_ticker_confluence",
        "dash",
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
        "forbidden import in trafficflow_k_artifact_builder: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_latest_stackbuilder_run_returns_newest_seed_dir(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    older = _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__OLD",
        write_leaderboard=False,
    )
    newer = _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__NEW",
        write_leaderboard=False,
    )
    # Bump newer's mtime so it wins the "most recent" check
    # regardless of OS-dependent default ordering.
    import os
    os.utime(newer, (1700000000, 1800000000))
    os.utime(older, (1600000000, 1600000000))
    run = tkb.discover_latest_stackbuilder_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
    )
    assert run == newer
    # Dot/underscore-prefixed bookkeeping dirs (e.g. ``_progress``)
    # are skipped.
    bookkeeping = dirs["stackbuilder_root"] / "SPY" / "_progress"
    bookkeeping.mkdir()
    run = tkb.discover_latest_stackbuilder_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
    )
    assert run == newer


def test_discover_returns_none_when_no_run_exists(tmp_path: Path):
    dirs = _layout(tmp_path)
    assert (
        tkb.discover_latest_stackbuilder_run(
            "NOPE", stackbuilder_root=dirs["stackbuilder_root"],
        ) is None
    )


# ---------------------------------------------------------------------------
# Leaderboard load + K iteration
# ---------------------------------------------------------------------------


def test_load_stackbuilder_leaderboard_reads_xlsx_schema(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    run = _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
    )
    df = tkb.load_stackbuilder_leaderboard(run)
    assert list(df.columns) == [
        "K", "Trigger Days", "Total Capture (%)",
        "Sharpe Ratio", "p-Value", "Members",
    ]
    assert len(df) == 12


def test_load_stackbuilder_leaderboard_raises_when_absent(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    run = _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__NO_FILE", write_leaderboard=False,
    )
    with pytest.raises(FileNotFoundError):
        tkb.load_stackbuilder_leaderboard(run)


def test_iter_k_build_rows_yields_one_per_k(tmp_path: Path):
    dirs = _layout(tmp_path)
    run = _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
    )
    df = tkb.load_stackbuilder_leaderboard(run)
    rows = tkb.iter_k_build_rows(
        df, target_ticker="SPY", run_id=run.name,
    )
    assert [r.K for r in rows] == list(range(1, 13))
    assert all(r.target_ticker == "SPY" for r in rows)
    assert all(r.run_id == run.name for r in rows)
    # Summary fields propagate from the saved leaderboard.
    one = next(r for r in rows if r.K == 5)
    assert one.trigger_days == 105
    assert one.total_capture_pct == pytest.approx(15.0)
    assert one.sharpe_ratio == pytest.approx(0.15)
    # Significant 95% absent -> None.
    assert one.significant_95 is None


def test_iter_k_build_rows_raises_for_missing_k_column(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    rows = [{"Trigger Days": 1, "Members": "['AAA[D]']"}]
    run = _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__BAD",
        rows=rows,
        columns=["Trigger Days", "Members"],
    )
    df = tkb.load_stackbuilder_leaderboard(run)
    with pytest.raises(KeyError) as excinfo:
        tkb.iter_k_build_rows(
            df, target_ticker="SPY", run_id=run.name,
        )
    assert tkb.ISSUE_MISSING_K_COLUMN in str(excinfo.value)


def test_iter_k_build_rows_raises_for_missing_members_column(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    rows = [{"K": 1, "Trigger Days": 1}]
    run = _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__BAD_M",
        rows=rows,
        columns=["K", "Trigger Days"],
    )
    df = tkb.load_stackbuilder_leaderboard(run)
    with pytest.raises(KeyError) as excinfo:
        tkb.iter_k_build_rows(
            df, target_ticker="SPY", run_id=run.name,
        )
    assert tkb.ISSUE_MISSING_MEMBERS_COLUMN in str(excinfo.value)


# ---------------------------------------------------------------------------
# Top-level build (write=False)
# ---------------------------------------------------------------------------


def test_build_returns_no_stackbuilder_run_when_target_missing(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "NOPE",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
    )
    assert res.issue_codes == (tkb.ISSUE_NO_STACKBUILDER_RUN,)
    assert res.built_k == ()
    assert res.attempted_k == ()


def test_build_returns_missing_combo_leaderboard(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(dirs["cache_dir"], "SPY")
    _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__NO_LB",
        write_leaderboard=False,
    )
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
    )
    assert res.issue_codes == (tkb.ISSUE_MISSING_COMBO_LEADERBOARD,)


def test_build_surfaces_missing_k_column(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_cache_pkl(dirs["cache_dir"], "SPY")
    _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__BAD",
        rows=[{"Trigger Days": 1, "Members": "['AAA[D]']"}],
        columns=["Trigger Days", "Members"],
    )
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
    )
    assert res.issue_codes == (tkb.ISSUE_MISSING_K_COLUMN,)


def test_build_surfaces_missing_target_cache(tmp_path: Path):
    """Target cache PKL absent. The leaderboard exists; the
    builder reports every K as skipped with the
    missing_target_cache issue."""
    dirs = _layout(tmp_path)
    # No target PKL written.
    _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
    )
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
    )
    assert tkb.ISSUE_MISSING_TARGET_CACHE in res.issue_codes
    assert res.built_k == ()
    assert set(res.skipped_k) == set(range(1, 13))


def test_build_skips_rows_without_member_caches(tmp_path: Path):
    """Target cache present, but the member tickers have no
    saved caches. The from-local builder returns None per row;
    we collect every skip under no_member_caches."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(dirs["cache_dir"], "SPY")
    # No AAA / BBB caches written.
    _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
    )
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
    )
    assert tkb.ISSUE_NO_MEMBER_CACHES in res.issue_codes
    assert res.built_k == ()
    assert set(res.skipped_k) == set(range(1, 13))


def test_invalid_k_leaderboard_emits_partial_k_coverage(
    tmp_path: Path,
):
    """Audit repro: K column exists but every value is
    unparseable. The builder previously returned a clean
    BuildResult; under the audit fix it must emit
    partial_k_coverage so no audit tooling treats an empty K
    set as a successful sweep."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(dirs["cache_dir"], "SPY")
    _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__BADK",
        rows=[
            {"K": "bad", "Trigger Days": 1,
             "Total Capture (%)": 0.0, "Sharpe Ratio": 0.0,
             "p-Value": 1.0, "Members": "['AAA[D]']"},
            {"K": "also-bad", "Trigger Days": 2,
             "Total Capture (%)": 0.0, "Sharpe Ratio": 0.0,
             "p-Value": 1.0, "Members": "['AAA[D]']"},
        ],
    )
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
    )
    assert tkb.ISSUE_PARTIAL_K_COVERAGE in res.issue_codes
    assert res.built_k == ()
    assert res.attempted_k == ()


def test_k_values_outside_expected_emit_partial_k_coverage(
    tmp_path: Path,
):
    """Leaderboard parses cleanly but every K is outside the
    requested expected_k range. The iter helper filters them
    out, attempted_k stays empty - and the builder must still
    flag partial_k_coverage rather than reporting silent
    success."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(dirs["cache_dir"], "SPY")
    _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__OUTSIDE",
        rows=[
            {"K": 42, "Trigger Days": 1,
             "Total Capture (%)": 0.0, "Sharpe Ratio": 0.0,
             "p-Value": 1.0, "Members": "['AAA[D]']"},
            {"K": 99, "Trigger Days": 2,
             "Total Capture (%)": 0.0, "Sharpe Ratio": 0.0,
             "p-Value": 1.0, "Members": "['AAA[D]']"},
        ],
    )
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
    )
    assert tkb.ISSUE_PARTIAL_K_COVERAGE in res.issue_codes
    assert res.attempted_k == ()
    assert res.built_k == ()


def test_only_k1_with_expected_1_to_12_emits_partial_k_coverage(
    tmp_path: Path,
):
    """A leaderboard that only carries K=1 against the default
    expected_k=1..12 must surface partial_k_coverage even when
    K=1 builds successfully."""
    dirs = _layout(tmp_path)
    _write_cache_pkl(dirs["cache_dir"], "SPY")
    _write_cache_pkl(dirs["cache_dir"], "AAA")
    _write_cache_pkl(dirs["cache_dir"], "BBB")
    _make_seed_run(
        dirs["stackbuilder_root"], "SPY",
        seed_name="seedTC__ONLY_K1",
        rows=[{
            "K": 1, "Trigger Days": 100,
            "Total Capture (%)": 10.0, "Sharpe Ratio": 0.1,
            "p-Value": 0.05,
            "Members": "['AAA[D]', 'BBB[D]']",
        }],
    )
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
    )
    assert tkb.ISSUE_PARTIAL_K_COVERAGE in res.issue_codes
    # K=1 itself built successfully - the partial flag is about
    # the K range, not about the row that did succeed.
    assert res.attempted_k == (1,)
    assert res.built_k == (1,)


def test_full_k_pipeline_remains_clean_after_audit_fix(
    tmp_path: Path,
):
    """Regression guard: the happy path that built K=1..12
    cleanly under the original behavior must still report
    zero issue codes after the partial_k_coverage detection
    is tightened. This pairs with the audit fix above to make
    sure we did not accidentally flag a successful sweep."""
    dirs = _full_pipeline_fixtures(tmp_path)
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        write=False,
    )
    assert set(res.built_k) == set(range(1, 13))
    assert res.skipped_k == ()
    assert res.issue_codes == (), (
        "full K=1..12 happy path must stay clean; got "
        + repr(res.issue_codes)
    )


def test_build_with_write_false_builds_in_memory_writes_nothing(
    tmp_path: Path,
):
    dirs = _full_pipeline_fixtures(tmp_path)
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        write=False,
    )
    assert set(res.built_k) == set(range(1, 13))
    assert res.skipped_k == ()
    assert res.issue_codes == ()
    # write=False must not have produced any on-disk artifact.
    tf_root = dirs["artifact_root"] / "trafficflow"
    json_files: list[Path] = []
    if tf_root.exists():
        json_files = sorted(tf_root.rglob("*.research_day.json"))
    assert json_files == [], (
        "write=False must not persist any artifact; found "
        + repr(json_files)
    )


# ---------------------------------------------------------------------------
# Top-level build (write=True) and path uniqueness
# ---------------------------------------------------------------------------


def test_build_with_write_true_persists_one_artifact_per_k(
    tmp_path: Path,
):
    dirs = _full_pipeline_fixtures(tmp_path)
    res = tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    assert set(res.built_k) == set(range(1, 13))
    # All artifact paths are unique on disk.
    assert len(res.artifact_paths) == 12
    assert len(set(map(str, res.artifact_paths))) == 12
    # And every K appears in some filename.
    names = {p.name for p in res.artifact_paths}
    for k in range(1, 13):
        assert any(f"__K{k}." in n for n in names), (
            f"no artifact filename carries the __K{k}. suffix; "
            f"got {names}"
        )
    # Each file is valid research_day_v1 with the K metadata
    # preserved internally.
    for k in range(1, 13):
        match = next(
            p for p in res.artifact_paths if f"__K{k}." in p.name
        )
        art = ra.read_research_day_artifact(match)
        assert art is not None
        assert art.engine == "trafficflow"
        assert art.K == k


def test_k_specific_artifact_paths_do_not_collide(tmp_path: Path):
    """Direct probe of ``artifact_run_id_for_k`` + the canonical
    research_artifacts path helper. Two K values for the same
    seed-run must hash to two different on-disk paths."""
    seed_run_id = "seedTC__AAA-D_BBB-D"
    p1 = ra.artifact_path_for_trafficflow(
        "SPY",
        tkb.artifact_run_id_for_k(seed_run_id, 1),
        base_dir=tmp_path,
    )
    p2 = ra.artifact_path_for_trafficflow(
        "SPY",
        tkb.artifact_run_id_for_k(seed_run_id, 2),
        base_dir=tmp_path,
    )
    assert p1 is not None and p2 is not None
    assert p1 != p2
    assert p1.name != p2.name
    assert "K1" in p1.name and "K2" in p2.name


def test_artifact_run_id_for_k_returns_empty_on_empty_seed():
    assert tkb.artifact_run_id_for_k("", 1) == ""
    assert tkb.artifact_run_id_for_k("seed", 4) == "seed__K4"


# ---------------------------------------------------------------------------
# Readiness integration
# ---------------------------------------------------------------------------


def test_readiness_sees_full_k_coverage_after_builder_writes(
    tmp_path: Path,
):
    """End-to-end: a fixture that builds all 12 K artifacts via
    write=True must clear ``insufficient_trafficflow_k_coverage``
    in the Phase 6C-8 readiness verdict for that ticker."""
    dirs = _full_pipeline_fixtures(tmp_path)
    tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_INSUFFICIENT_TRAFFICFLOW_K_COVERAGE
        not in r.issue_codes
    ), r.issue_codes


def test_readiness_still_reports_missing_bridge_after_phase_6d_1(
    tmp_path: Path,
):
    """Phase 6D-1 only ships per-K single-timeframe artifacts.
    The multi-timeframe TrafficFlow / K-build projection is
    Phase 6D-2, so the bridge issue code must still appear after
    a successful all-K build. This guards against accidentally
    promoting tickers before the bridge ships."""
    dirs = _full_pipeline_fixtures(tmp_path)
    tkb.build_trafficflow_artifacts_for_stack_run(
        "SPY",
        stackbuilder_root=dirs["stackbuilder_root"],
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        write=True,
    )
    r = cpr.inspect_ticker_pipeline(
        "SPY", current_as_of_date="2026-05-08",
        cache_dir=dirs["cache_dir"],
        artifact_root=dirs["artifact_root"],
        stackbuilder_root=dirs["stackbuilder_root"],
        signal_library_dir=dirs["signal_library_dir"],
        fast_path_when_no_confluence=False,
    )
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        in r.issue_codes
    )
    # And the row still fails the strict leader gate, because the
    # bridge is the next gate the readiness layer enforces.
    assert r.leader_eligible is False
