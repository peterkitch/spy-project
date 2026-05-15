"""Phase 6I-55a tests for the ImpactSearch / primary-
universe readiness planner.

Pins:

  * Schema version + classification taxonomy + 10
    stable issue codes.
  * Missing workbook -> needs_impactsearch_run +
    impact_xlsx_missing.
  * Stale workbook -> needs_impactsearch_run +
    impact_xlsx_stale.
  * Fresh verified workbook with usable rows + price
    cache + enough signal-library coverage -> ready.
  * Workbook missing required columns -> manual_review +
    impact_xlsx_required_columns_missing.
  * Strict-manifest rejection -> manual_review +
    impact_xlsx_manifest_rejected.
  * Primary signal-library coverage incomplete ->
    manual_review +
    primary_signal_library_coverage_incomplete.
  * Generated ready command includes --prefer-impact-
    xlsx, --impact-xlsx-dir, --impact-xlsx-max-age-days,
    no --primaries, no --both-modes; parses against
    stackbuilder.parse_args.
  * No forbidden top-level imports (no pickle / yfinance
    / subprocess / engine / writer modules); no raw
    pickle.load call.
  * --output path guard rejects all 7 production-root
    paths (including output/impactsearch).
  * Production-state smoke skips cleanly when
    output/impactsearch is absent.
"""
from __future__ import annotations

import ast
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_impactsearch_primary_universe_readiness_planner as pln  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    ok: bool = True
    legacy: bool = False
    mismatches: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _make_loader_returning(
    df_per_path: dict[str, Any],
    *,
    result_per_path: dict[str, _FakeResult] | None = None,
) -> Callable[..., Any]:
    """Fake verified_loader that returns canned (df,
    result) pairs keyed by the resolved string path."""
    result_per_path = result_per_path or {}

    def _loader(path, *, strict=False):
        key = str(path)
        df = df_per_path.get(key)
        if df is None:
            return None, _FakeResult(
                ok=False, legacy=False,
                mismatches=[("load_error", "FNF", key)],
            )
        result = result_per_path.get(
            key, _FakeResult(ok=True, legacy=False),
        )
        return df, result

    return _loader


def _good_rank_df(n_primaries: int = 25):
    """Build a standardized-ish ImpactSearch rank
    DataFrame. The planner re-runs
    ``_standardize_rank_columns`` on it; that helper
    accepts the canonical names as-is."""
    import pandas as pd
    rows = []
    for i in range(n_primaries):
        rows.append({
            "Primary Ticker": f"PRIM{i:03d}",
            "Total Capture (%)": 50.0 - i,
            "Trigger Days": 30,
            "Sharpe Ratio": 1.0,
            "Win Ratio (%)": 55.0,
            "Std Dev (%)": 1.0,
            "Avg Daily Capture (%)": 0.1,
        })
    return pd.DataFrame(rows)


def _make_workbook_file(
    ixd: Path, ticker: str, *, mtime_age_days: float = 1.0,
) -> Path:
    """Create a placeholder xlsx file (zero-byte is fine
    because we inject a fake verified_loader)."""
    ixd.mkdir(parents=True, exist_ok=True)
    p = ixd / f"{ticker}_analysis.xlsx"
    p.write_bytes(b"")
    new_mtime = time.time() - mtime_age_days * 86400.0
    try:
        import os as _os
        _os.utime(p, (new_mtime, new_mtime))
    except OSError:
        pass
    return p


def _make_price_cache(
    pcd: Path, ticker: str, *, suffix: str = ".csv",
) -> Path:
    pcd.mkdir(parents=True, exist_ok=True)
    p = pcd / f"{ticker.upper()}{suffix}"
    p.write_bytes(b"Date,Close\n2026-01-01,100\n")
    return p


def _make_signal_lib(
    sld: Path, primary: str,
) -> Path:
    sld.mkdir(parents=True, exist_ok=True)
    p = sld / f"{primary}_stable_v1_0_0.pkl"
    p.write_bytes(b"\x80\x04N.")  # minimal pickle of None
    return p


# ---------------------------------------------------------------------------
# 1. Schema + taxonomy + issue-code stability
# ---------------------------------------------------------------------------


def test_schema_and_taxonomy_constants_are_stable():
    assert (
        pln.SCHEMA_VERSION
        == "confluence_impactsearch_primary_universe_readiness_planner_v1"
    )
    assert pln.DEFAULT_INSPECTED_TICKERS == (
        "SPY", "AAPL", "JNJ", "WMT", "HD", "MCD",
    )
    assert (
        pln.DEFAULT_IMPACT_XLSX_DIR_RELATIVE
        == "output/impactsearch"
    )
    assert (
        pln.DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS == 45
    )
    assert (
        pln.DEFAULT_BOTTOM_N_COVERAGE_THRESHOLD == 20
    )
    for c in (
        pln.CLASSIFICATION_READY,
        pln.CLASSIFICATION_NEEDS_IMPACTSEARCH,
        pln.CLASSIFICATION_MANUAL_REVIEW,
    ):
        assert c in pln.ALL_CLASSIFICATIONS
    # The 10 stable issue codes.
    assert len(pln.ALL_ISSUE_CODES) == 10
    for code in (
        pln.ISSUE_IMPACT_XLSX_MISSING,
        pln.ISSUE_IMPACT_XLSX_STALE,
        pln.ISSUE_IMPACT_XLSX_LOAD_ERROR,
        pln.ISSUE_IMPACT_XLSX_MANIFEST_REJECTED,
        pln.ISSUE_IMPACT_XLSX_REQUIRED_COLUMNS_MISSING,
        pln.ISSUE_IMPACT_XLSX_NO_USABLE_PRIMARY_ROWS,
        pln.ISSUE_SECONDARY_PRICE_CACHE_MISSING,
        pln.ISSUE_PRIMARY_SIGNAL_LIBRARY_COVERAGE_INCOMPLETE,
        pln.ISSUE_MANUAL_REVIEW_REQUIRED,
        pln.ISSUE_UNKNOWN_ERROR,
    ):
        assert code in pln.ALL_ISSUE_CODES


# ---------------------------------------------------------------------------
# 2. Rank-colmap matches stackbuilder.py (drift guard).
# ---------------------------------------------------------------------------


def test_rank_colmap_matches_stackbuilder():
    """The local _RANK_COLMAP mirror must match
    stackbuilder.py:562-568. AST-level drift catch so
    future stackbuilder edits surface here."""
    import stackbuilder
    assert pln._RANK_COLMAP == stackbuilder._RANK_COLMAP


# ---------------------------------------------------------------------------
# 3. Missing workbook -> needs_impactsearch_run.
# ---------------------------------------------------------------------------


def test_missing_workbook_routes_to_needs_impactsearch(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    ixd.mkdir(parents=True, exist_ok=True)
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    _make_price_cache(pcd, "FOO")
    loader = _make_loader_returning({})
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
        )
    )
    row = plan["rows_by_ticker"][0]
    assert (
        row["classification"]
        == pln.CLASSIFICATION_NEEDS_IMPACTSEARCH
    )
    assert (
        pln.ISSUE_IMPACT_XLSX_MISSING in row["issue_codes"]
    )
    assert row["workbook_path"] is None


# ---------------------------------------------------------------------------
# 4. Stale workbook -> needs_impactsearch_run.
# ---------------------------------------------------------------------------


def test_stale_workbook_routes_to_needs_impactsearch(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=60.0,
    )
    _make_price_cache(pcd, "FOO")
    loader = _make_loader_returning(
        {str(p): _good_rank_df()},
    )
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
            impact_xlsx_max_age_days=45,
            now_seconds=time.time(),
        )
    )
    row = plan["rows_by_ticker"][0]
    assert (
        row["classification"]
        == pln.CLASSIFICATION_NEEDS_IMPACTSEARCH
    )
    assert (
        pln.ISSUE_IMPACT_XLSX_STALE in row["issue_codes"]
    )
    assert row["workbook_age_days"] >= 60.0


# ---------------------------------------------------------------------------
# 5. Fresh verified workbook + price cache + enough
#    signal-library coverage -> ready.
# ---------------------------------------------------------------------------


def test_fresh_verified_workbook_routes_to_ready(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=1.0,
    )
    _make_price_cache(pcd, "FOO")
    df = _good_rank_df(n_primaries=25)
    # Stage 25 signal-library candidates (covers all 25
    # primaries -> coverage 100%, above threshold 20).
    for i in range(25):
        _make_signal_lib(sld, f"PRIM{i:03d}")
    loader = _make_loader_returning({str(p): df})
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
            impact_xlsx_max_age_days=45,
            now_seconds=time.time(),
        )
    )
    row = plan["rows_by_ticker"][0]
    assert (
        row["classification"]
        == pln.CLASSIFICATION_READY
    )
    assert row["issue_codes"] == []
    assert row["primary_count"] == 25
    assert (
        row["primary_signal_library_coverage"][
            "enough_for_bottom_n"
        ]
        is True
    )
    # Command manifest gets a ready row.
    manifest = plan["command_manifest"]
    ready = [c for c in manifest if c["ticker"] == "FOO"][0]
    assert (
        ready["authorization_class"]
        == "stackbuilder_write"
    )
    assert (
        ready["requires_separate_operator_authorization"]
        is True
    )


# ---------------------------------------------------------------------------
# 6. Workbook missing required columns -> manual_review.
# ---------------------------------------------------------------------------


def test_missing_required_columns_routes_to_manual_review(
    tmp_path,
):
    import pandas as pd
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=1.0,
    )
    _make_price_cache(pcd, "FOO")
    # Missing Primary Ticker + Total Capture (%).
    bad_df = pd.DataFrame(
        {"Something Else": [1, 2, 3]},
    )
    loader = _make_loader_returning({str(p): bad_df})
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
        )
    )
    row = plan["rows_by_ticker"][0]
    assert (
        row["classification"]
        == pln.CLASSIFICATION_MANUAL_REVIEW
    )
    assert (
        pln.ISSUE_IMPACT_XLSX_REQUIRED_COLUMNS_MISSING
        in row["issue_codes"]
    )
    assert (
        pln.ISSUE_MANUAL_REVIEW_REQUIRED
        in row["issue_codes"]
    )


# ---------------------------------------------------------------------------
# 7. Strict-manifest rejection -> manual_review.
# ---------------------------------------------------------------------------


def test_strict_manifest_rejection_routes_to_manual_review(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=1.0,
    )
    _make_price_cache(pcd, "FOO")
    df = _good_rank_df()
    # Loader returns legacy=True (no sidecar manifest).
    # Under strict_manifests=True the planner should
    # reject this.
    loader = _make_loader_returning(
        {str(p): df},
        result_per_path={
            str(p): _FakeResult(
                ok=False, legacy=True,
                mismatches=[(
                    "xlsx_no_manifest",
                    "expected sidecar", "missing",
                )],
            ),
        },
    )
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
            strict_manifests=True,
        )
    )
    row = plan["rows_by_ticker"][0]
    assert (
        row["classification"]
        == pln.CLASSIFICATION_MANUAL_REVIEW
    )
    assert (
        pln.ISSUE_IMPACT_XLSX_MANIFEST_REJECTED
        in row["issue_codes"]
    )
    assert (
        row["workbook_manifest_status"]
        == "rejected_strict_legacy"
    )


# ---------------------------------------------------------------------------
# 8. Primary signal-library coverage incomplete ->
#    manual_review.
# ---------------------------------------------------------------------------


def test_signal_library_coverage_incomplete_routes_to_manual_review(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=1.0,
    )
    _make_price_cache(pcd, "FOO")
    df = _good_rank_df(n_primaries=25)
    # Stage only 5 of the 25 primaries -> coverage = 5 <
    # threshold 20.
    for i in range(5):
        _make_signal_lib(sld, f"PRIM{i:03d}")
    loader = _make_loader_returning({str(p): df})
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
        )
    )
    row = plan["rows_by_ticker"][0]
    assert (
        row["classification"]
        == pln.CLASSIFICATION_MANUAL_REVIEW
    )
    assert (
        pln.ISSUE_PRIMARY_SIGNAL_LIBRARY_COVERAGE_INCOMPLETE
        in row["issue_codes"]
    )
    assert (
        row["primary_signal_library_coverage"][
            "enough_for_bottom_n"
        ]
        is False
    )


# ---------------------------------------------------------------------------
# 9. Missing secondary price cache -> manual_review.
# ---------------------------------------------------------------------------


def test_missing_secondary_price_cache_routes_to_manual_review(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=1.0,
    )
    df = _good_rank_df()
    for i in range(25):
        _make_signal_lib(sld, f"PRIM{i:03d}")
    loader = _make_loader_returning({str(p): df})
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,  # empty
            verified_loader=loader,
        )
    )
    row = plan["rows_by_ticker"][0]
    assert (
        row["classification"]
        == pln.CLASSIFICATION_MANUAL_REVIEW
    )
    assert (
        pln.ISSUE_SECONDARY_PRICE_CACHE_MISSING
        in row["issue_codes"]
    )


# ---------------------------------------------------------------------------
# 10. Generated ready command shape + parses against
#     stackbuilder.parse_args.
# ---------------------------------------------------------------------------


def test_ready_command_includes_impact_xlsx_bridge_flags(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=1.0,
    )
    _make_price_cache(pcd, "FOO")
    df = _good_rank_df(n_primaries=25)
    for i in range(25):
        _make_signal_lib(sld, f"PRIM{i:03d}")
    loader = _make_loader_returning({str(p): df})
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
            impact_xlsx_max_age_days=45,
        )
    )
    cmd = [
        c for c in plan["command_manifest"]
        if c["ticker"] == "FOO"
    ][0]
    argv = cmd["argv"]
    # Required ImpactSearch bridge flags.
    assert "--prefer-impact-xlsx" in argv
    assert "--impact-xlsx-dir" in argv
    assert "--impact-xlsx-max-age-days" in argv
    # The bridge flag values point at the test ixd dir
    # + the max-age value 45.
    ix_idx = argv.index("--impact-xlsx-dir")
    assert argv[ix_idx + 1] == str(ixd)
    age_idx = argv.index("--impact-xlsx-max-age-days")
    assert argv[age_idx + 1] == "45"
    # NO --primaries (manual override only -- planner
    # never auto-emits it).
    assert "--primaries" not in argv
    # NO --both-modes (Phase 6I-52 locked policy).
    assert "--both-modes" not in argv
    # NO --strict-manifests when not requested.
    assert "--strict-manifests" not in argv
    # Pinned interpreter + locked policy flags pass
    # through.
    assert argv[0] == pln.PINNED_INTERPRETER
    for flag, val in (
        ("--secondary", "FOO"),
        ("--top-n", "20"),
        ("--bottom-n", "20"),
        ("--max-k", "6"),
        ("--search", "beam"),
        ("--beam-width", "12"),
        ("--seed-by", "total_capture"),
        ("--optimize-by", "total_capture"),
        ("--min-trigger-days", "30"),
        ("--combine-mode", "intersection"),
    ):
        assert argv[argv.index(flag) + 1] == val


def test_ready_command_parses_against_real_stackbuilder(
    tmp_path,
):
    """The emitted argv must parse cleanly against the
    real ``stackbuilder.parse_args`` namespace."""
    import stackbuilder
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=1.0,
    )
    _make_price_cache(pcd, "FOO")
    df = _good_rank_df()
    for i in range(25):
        _make_signal_lib(sld, f"PRIM{i:03d}")
    loader = _make_loader_returning({str(p): df})
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
        )
    )
    cmd = [
        c for c in plan["command_manifest"]
        if c["ticker"] == "FOO"
    ][0]
    argv = cmd["argv"]
    # Drop interpreter + script name; argparse only sees
    # the flags.
    ns = stackbuilder.parse_args(argv[2:])
    assert ns.secondary == "FOO"
    assert ns.prefer_impact_xlsx is True
    assert ns.impact_xlsx_dir == str(ixd)
    assert ns.impact_xlsx_max_age_days == 45
    assert ns.top_n == 20
    assert ns.bottom_n == 20
    assert ns.max_k == 6
    assert ns.search == "beam"
    assert ns.beam_width == 12
    assert ns.seed_by == "total_capture"
    assert ns.optimize_by == "total_capture"
    assert ns.combine_mode == "intersection"
    assert ns.both_modes is False
    assert ns.strict_manifests is False


def test_strict_manifests_flag_propagates_to_command(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    p = _make_workbook_file(
        ixd, "FOO", mtime_age_days=1.0,
    )
    _make_price_cache(pcd, "FOO")
    df = _good_rank_df()
    for i in range(25):
        _make_signal_lib(sld, f"PRIM{i:03d}")
    loader = _make_loader_returning({str(p): df})
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=loader,
            strict_manifests=True,
        )
    )
    # Note: when strict_manifests=True, the workbook will
    # have its legacy=True path treated under strict ->
    # rejection. But the fake loader returns legacy=False
    # by default, so the FOO row should be ready and the
    # command should propagate --strict-manifests.
    cmd = [
        c for c in plan["command_manifest"]
        if c["ticker"] == "FOO"
    ][0]
    assert "--strict-manifests" in cmd["argv"]


# ---------------------------------------------------------------------------
# 11. No forbidden top-level imports + no raw pickle.load.
# ---------------------------------------------------------------------------


_FORBIDDEN_TOP_LEVEL_IMPORTS = frozenset({
    "pickle",
    "subprocess",
    "yfinance",
    "dash",
    "signal_engine_cache_refresher",
    "signal_library_stable_promotion_writer",
    "multiwindow_k_confluence_patch_writer",
    "confluence_pipeline_runner",
    "daily_board_automation_writer",
    "daily_board_automation_executor",
    "spymaster",
    "trafficflow",
    "stackbuilder",
    "onepass",
    "impactsearch",
    "confluence",
    "cross_ticker_confluence",
    "daily_signal_board",
})


def test_no_forbidden_top_level_imports():
    here = Path(__file__).resolve().parent.parent
    src = (
        here
        / "confluence_impactsearch_primary_universe_readiness_planner.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for n in node.names:
                top_level.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level.add(
                    node.module.split(".")[0],
                )
    leaked = (
        top_level & _FORBIDDEN_TOP_LEVEL_IMPORTS
    )
    assert not leaked, (
        f"Forbidden top-level imports in planner: "
        f"{sorted(leaked)}"
    )


def test_module_source_has_no_raw_pickle_load_call():
    """AST-level guard: no actual pickle.load(...) or
    pickle_load_compat(...) call expression."""
    here = Path(__file__).resolve().parent.parent
    src = (
        here
        / "confluence_impactsearch_primary_universe_readiness_planner.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                assert n.name.split(".")[0] != "pickle"
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                if (
                    isinstance(fn.value, ast.Name)
                    and fn.value.id == "pickle"
                    and fn.attr == "load"
                ):
                    raise AssertionError(
                        "unexpected pickle.load(...) "
                        "call in planner"
                    )
            if isinstance(fn, ast.Name):
                assert (
                    fn.id != "pickle_load_compat"
                )


# ---------------------------------------------------------------------------
# 12. --output path guard rejects all 7 guarded roots.
# ---------------------------------------------------------------------------


def test_output_path_guard_rejects_guarded_roots(
    tmp_path, capsys,
):
    forbidden_outputs = [
        "cache/results/plan.json",
        "cache\\status\\plan.json",
        "output/research_artifacts/plan.json",
        "output/stackbuilder/plan.json",
        "signal_library/data/stable/plan.json",
        "price_cache/daily/plan.json",
        "output/impactsearch/plan.json",
    ]
    for forbidden in forbidden_outputs:
        rc = pln.main(["--output", forbidden])
        err = capsys.readouterr().err
        assert rc == 2, (
            f"Expected rc=2 for {forbidden!r}; got {rc}"
        )
        assert (
            "output_path_inside_guarded_root" in err
        )


# ---------------------------------------------------------------------------
# 13. Production-state smoke: skips cleanly when
#     output/impactsearch is absent in a clean worktree.
# ---------------------------------------------------------------------------


def test_production_state_smoke_skips_when_impact_xlsx_dir_absent():
    """Informational production smoke. Skips when
    ``output/impactsearch`` is absent (clean Codex
    worktree); otherwise reports the actual
    classification without asserting specific counts."""
    import pytest
    here = Path(__file__).resolve().parent.parent
    ixd = here / "output" / "impactsearch"
    if not ixd.exists():
        pytest.skip(
            "output/impactsearch absent in this "
            "worktree; production smoke skipped (the "
            "fixture-based tests above pin the cascade "
            "deterministically)."
        )
    # Don't inject a fake loader -- use the real one to
    # exercise the production path read-only. The smoke
    # asserts only that the call completes + returns the
    # canonical schema.
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan()
    )
    assert (
        plan["schema_version"]
        == "confluence_impactsearch_primary_universe_readiness_planner_v1"
    )
    assert plan["inspected_tickers"] == [
        "SPY", "AAPL", "JNJ", "WMT", "HD", "MCD",
    ]
    # Counts sum to 6.
    assert sum(
        plan["counts_by_classification"].values()
    ) == 6


# ---------------------------------------------------------------------------
# 14. No production activity contract carried through.
# ---------------------------------------------------------------------------


def test_no_production_activity_contract_present(
    tmp_path,
):
    """Every plan output must carry the
    no_production_activity_contract block as an
    operator-facing invariant."""
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=_make_loader_returning({}),
        )
    )
    contract = plan["no_production_activity_contract"]
    assert contract["no_raw_pickle_load"] is True
    for never in (
        "stackbuilder", "impactsearch", "onepass",
        "yfinance", "subprocess",
    ):
        assert never in contract["never_invokes"]
    for root in (
        "cache/results",
        "cache/status",
        "output/research_artifacts",
        "output/stackbuilder",
        "signal_library/data/stable",
        "price_cache/daily",
        "output/impactsearch",
    ):
        assert root in contract["never_writes_to"]


# ---------------------------------------------------------------------------
# 15. Upstream chain citations present (the amendment-1
#     evidence requirement from Phase 6I-55).
# ---------------------------------------------------------------------------


def test_upstream_chain_citations_present(tmp_path):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    plan = (
        pln.build_impactsearch_primary_universe_readiness_plan(
            ["FOO"],
            impact_xlsx_dir=ixd,
            signal_lib_dir=sld,
            price_cache_dir=pcd,
            verified_loader=_make_loader_returning({}),
        )
    )
    citations = plan["upstream_chain_citations"]
    cited = " ".join(c["file_line"] for c in citations)
    for needle in (
        "onepass.py:1154",
        "impactsearch.py:1525",
        "impactsearch.py:2491",
        "stackbuilder.py:583",
        "stackbuilder.py:889",
        "stackbuilder.py:1487",
        "stackbuilder.py:3361",
        "stackbuilder.py:702",
        "provenance_manifest.py:1821",
    ):
        assert needle in cited, (
            f"missing citation: {needle}"
        )


# ---------------------------------------------------------------------------
# Phase 6I-55a amendment-1: evidence-doc precision guard.
#
# Codex audit asked for three precision fixes to the
# evidence narrative: (a) distinguish production-present
# vs cacheless/audit worktree test counts; (b) replace a
# "see PR description for the green total" placeholder
# with exact totals; (c) fix "Files added (3)" to (4)
# matching the actual PR shape. This guard pins all
# three corrections so a future doc edit cannot regress
# them.
# ---------------------------------------------------------------------------


def test_evidence_doc_carries_amendment_1_precision_wording():
    """Read the 6I-55a evidence doc and assert:

      * the stale 'Files added (3)' heading does NOT
        appear;
      * the new 'Files added (4)' heading IS present;
      * the cacheless / audit worktree expected-skip
        wording IS present (both for the focused suite
        and the combined regression);
      * the original 'see Phase 6I-55a PR description
        for the green total' placeholder is NOT used
        as a substitute for exact results;
      * explicit production-present + cacheless totals
        appear for both the focused suite (18 / 17+1
        skipped) and the combined regression (165 /
        163+2 skipped).
    """
    here = Path(__file__).resolve().parent.parent
    doc = (
        here
        / "md_library"
        / "shared"
        / (
            "2026-05-15_PHASE_6I55A_IMPACTSEARCH_"
            "PRIMARY_UNIVERSE_READINESS_PLANNER.md"
        )
    )
    assert doc.exists(), (
        f"6I-55a evidence doc missing at {doc}"
    )
    body = doc.read_text(encoding="utf-8")

    # Stale wording must be gone.
    forbidden_stale = (
        "Files added (3)",
        (
            "see Phase 6I-55a PR description for the "
            "green total"
        ),
    )
    for phrase in forbidden_stale:
        assert phrase not in body, (
            f"Phase 6I-55a evidence doc still carries "
            f"stale wording: {phrase!r}"
        )

    # New required wording must be present.
    required = (
        "Files added (4)",
        # Focused-suite counts.
        "18 passed",
        "17 passed / 1 skipped",
        # Combined-regression counts.
        "165 passed",
        "163 passed / 2 skipped",
        # Explicit naming of the production-state smoke
        # and its skip condition.
        "test_production_state_smoke_skips_when_"
        "impact_xlsx_dir_absent",
        # The "not a functional regression" framing.
        "not functional regressions",
    )
    missing = [
        phrase for phrase in required
        if phrase not in body
    ]
    assert not missing, (
        "Phase 6I-55a evidence doc missing amendment-1 "
        f"required wording: {missing!r}"
    )
