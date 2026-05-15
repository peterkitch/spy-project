"""Phase 6I-54a tests for the StackBuilder price-cache
rebuild planner.

Pins:

  * Schema-version + action taxonomy + default cache
    dir constants are stable.
  * Per-ticker expected StackBuilder cache paths mirror
    stackbuilder.load_secondary_prices order exactly.
  * The planner distinguishes cache/results (signal-
    engine cache, source of truth for Phase 6I-54b's
    transformation) from price_cache/daily (StackBuilder
    secondary price cache, the rebuild destination).
  * Missing price_cache/daily AND missing signal-cache
    PKL -> needs_source_refresh.
  * Existing cache/results PKL + manifest with
    price_source='Close' -> use_existing_signal_cache.
  * Existing price_cache/daily file -> manual_review +
    stackbuilder_price_cache_already_present blocker.
  * Missing manifest sidecar -> manual_review +
    signal_cache_manifest_sidecar_missing blocker.
  * price_source other than 'Close' -> manual_review +
    signal_cache_price_source_not_close blocker.
  * Static guard: no forbidden top-level imports
    (especially no ``pickle`` and no ``yfinance`` and no
    StackBuilder / engine modules).
  * --output path guard rejects production-root paths
    (and price_cache/daily).
  * Aggregate counts + tickers-by-action are consistent
    with per-row classification.
  * Default universe matches Phase 6I-52 pilot universe
    via deferred import.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import stackbuilder_price_cache_rebuild_planner as pcp  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_manifest(
    pkl_path: Path,
    *,
    price_source: str = "Close",
    producer_engine: str = (
        "signal_engine_cache_refresher"
    ),
    engine_version: str = "6E-5.0.0",
) -> Path:
    """Create a minimal valid manifest sidecar at
    ``<pkl_path>.manifest.json``."""
    manifest_path = Path(
        str(pkl_path) + ".manifest.json",
    )
    manifest_path.parent.mkdir(
        parents=True, exist_ok=True,
    )
    manifest_path.write_text(
        json.dumps({
            "artifact_kind": "output",
            "artifact_type": (
                "spymaster_precomputed_results"
            ),
            "params": {
                "price_source": price_source,
                "ticker": pkl_path.stem.replace(
                    "_precomputed_results", "",
                ),
            },
            "producer_engine": producer_engine,
            "engine_version": engine_version,
            "build_timestamp": "2026-05-15T00:00:00+00:00",
            "schema_version": 1,
        }),
        encoding="utf-8",
    )
    return manifest_path


def _make_signal_cache(
    scd: Path,
    ticker: str,
    *,
    with_manifest: bool = True,
    price_source: str = "Close",
) -> Path:
    """Create a fake signal-cache PKL (zero-byte) +
    optional manifest sidecar at the given path."""
    scd.mkdir(parents=True, exist_ok=True)
    pkl_path = (
        scd / f"{ticker}_precomputed_results.pkl"
    )
    pkl_path.write_bytes(b"")
    if with_manifest:
        _write_manifest(
            pkl_path, price_source=price_source,
        )
    return pkl_path


# ---------------------------------------------------------------------------
# 1. Schema + taxonomy stability.
# ---------------------------------------------------------------------------


def test_schema_and_taxonomy_constants_are_stable():
    assert (
        pcp.SCHEMA_VERSION
        == "stackbuilder_price_cache_rebuild_planner_v1"
    )
    assert (
        pcp.DEFAULT_SIGNAL_CACHE_DIR_RELATIVE
        == "cache/results"
    )
    assert (
        pcp.DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE
        == "price_cache/daily"
    )
    for action in (
        pcp.ACTION_USE_EXISTING_SIGNAL_CACHE,
        pcp.ACTION_NEEDS_SOURCE_REFRESH,
        pcp.ACTION_NEEDS_NETWORK_FETCH,
        pcp.ACTION_MANUAL_REVIEW,
    ):
        assert action in pcp.ALL_RECOMMENDED_ACTIONS
    # The four actions appear EXACTLY ONCE.
    assert (
        len(set(pcp.ALL_RECOMMENDED_ACTIONS)) == 4
    )


# ---------------------------------------------------------------------------
# 2. Expected StackBuilder cache paths mirror
#    stackbuilder.load_secondary_prices order.
# ---------------------------------------------------------------------------


def test_expected_cache_paths_match_stackbuilder_order(
    tmp_path,
):
    paths = pcp._expected_stackbuilder_cache_paths(
        "SPY",
        stackbuilder_price_cache_dir=tmp_path,
    )
    names = [p.name for p in paths]
    assert names[:4] == [
        "SPY.parquet", "SPY.csv",
        "SPY.parquet", "SPY.csv",
    ]
    assert paths[4] == tmp_path / "SPY" / "daily.parquet"


def test_caret_stripped_variant_for_index_ticker(
    tmp_path,
):
    paths = pcp._expected_stackbuilder_cache_paths(
        "^GSPC",
        stackbuilder_price_cache_dir=tmp_path,
    )
    names = [p.name for p in paths]
    assert names[:4] == [
        "^GSPC.parquet", "^GSPC.csv",
        "GSPC.parquet", "GSPC.csv",
    ]


# ---------------------------------------------------------------------------
# 3. cache/results vs price_cache/daily distinction.
# ---------------------------------------------------------------------------


def test_cache_dirs_are_distinct(tmp_path):
    """The planner must use TWO different directories;
    the rebuild plan converts the first into the
    second."""
    scd = tmp_path / "signal_cache"
    pcd = tmp_path / "stackbuilder_price_cache"
    plan = pcp.build_price_cache_rebuild_plan(
        ["XYZ"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
    )
    assert plan["signal_cache_dir"] == str(scd)
    assert plan["stackbuilder_price_cache_dir"] == (
        str(pcd)
    )
    # Two different directory objects.
    assert (
        plan["signal_cache_dir"]
        != plan["stackbuilder_price_cache_dir"]
    )
    # Per-row paths come from the StackBuilder price-cache
    # dir, NOT the signal-cache dir.
    row = plan["rows"][0]
    for p in row["expected_stackbuilder_cache_paths"]:
        assert str(pcd).replace("\\", "/") in p.replace(
            "\\", "/",
        )
        assert str(scd).replace("\\", "/") not in p.replace(
            "\\", "/",
        )


# ---------------------------------------------------------------------------
# 4. Missing-both classifies as needs_source_refresh.
# ---------------------------------------------------------------------------


def test_missing_both_caches_needs_source_refresh(
    tmp_path,
):
    plan = pcp.build_price_cache_rebuild_plan(
        ["XYZ"],
        signal_cache_dir=tmp_path / "empty_scd",
        stackbuilder_price_cache_dir=(
            tmp_path / "empty_pcd"
        ),
    )
    row = plan["rows"][0]
    assert (
        row["recommended_action"]
        == pcp.ACTION_NEEDS_SOURCE_REFRESH
    )
    assert (
        pcp.BLOCKER_NO_SIGNAL_CACHE_FILE
        in row["blocker_codes"]
    )
    assert (
        row["transformation_possible_without_network"]
        is False
    )
    assert row["current_cache_status"] == "missing"
    assert row["signal_cache_pkl_present"] is False


# ---------------------------------------------------------------------------
# 5. Existing cache/results PKL + valid manifest ->
#    use_existing_signal_cache (the happy path).
# ---------------------------------------------------------------------------


def test_signal_cache_with_close_manifest_routes_to_use_existing(
    tmp_path,
):
    scd = tmp_path / "signal_cache"
    pcd = tmp_path / "stackbuilder_price_cache"
    _make_signal_cache(scd, "AAPL", price_source="Close")
    plan = pcp.build_price_cache_rebuild_plan(
        ["AAPL"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
    )
    row = plan["rows"][0]
    assert (
        row["recommended_action"]
        == pcp.ACTION_USE_EXISTING_SIGNAL_CACHE
    )
    assert row["signal_cache_pkl_present"] is True
    assert row["signal_cache_manifest_present"] is True
    assert row["signal_cache_price_source"] == "Close"
    assert (
        row["transformation_possible_without_network"]
        is True
    )
    assert row["blocker_codes"] == []


# ---------------------------------------------------------------------------
# 6. Existing price_cache/daily file -> manual_review +
#    stackbuilder_price_cache_already_present blocker.
# ---------------------------------------------------------------------------


def test_existing_stackbuilder_cache_routes_to_manual_review(
    tmp_path,
):
    scd = tmp_path / "signal_cache"
    pcd = tmp_path / "stackbuilder_price_cache"
    _make_signal_cache(scd, "BBB")
    pcd.mkdir(parents=True, exist_ok=True)
    (pcd / "BBB.parquet").write_bytes(b"")
    plan = pcp.build_price_cache_rebuild_plan(
        ["BBB"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
    )
    row = plan["rows"][0]
    assert (
        row["recommended_action"]
        == pcp.ACTION_MANUAL_REVIEW
    )
    assert (
        pcp.BLOCKER_PRICE_CACHE_ALREADY_PRESENT
        in row["blocker_codes"]
    )
    assert row["current_cache_status"] == "present"
    assert "BBB.parquet" in (
        row["existing_stackbuilder_cache_path"]
    )


# ---------------------------------------------------------------------------
# 7. Missing manifest -> manual_review.
# ---------------------------------------------------------------------------


def test_missing_manifest_routes_to_manual_review(
    tmp_path,
):
    scd = tmp_path / "signal_cache"
    pcd = tmp_path / "stackbuilder_price_cache"
    _make_signal_cache(
        scd, "CCC", with_manifest=False,
    )
    plan = pcp.build_price_cache_rebuild_plan(
        ["CCC"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
    )
    row = plan["rows"][0]
    assert (
        row["recommended_action"]
        == pcp.ACTION_MANUAL_REVIEW
    )
    assert (
        pcp.BLOCKER_NO_MANIFEST_SIDECAR
        in row["blocker_codes"]
    )
    assert row["signal_cache_pkl_present"] is True
    assert row["signal_cache_manifest_present"] is False


# ---------------------------------------------------------------------------
# 8. price_source != 'Close' -> manual_review.
# ---------------------------------------------------------------------------


def test_non_close_price_source_routes_to_manual_review(
    tmp_path,
):
    scd = tmp_path / "signal_cache"
    pcd = tmp_path / "stackbuilder_price_cache"
    _make_signal_cache(
        scd, "DDD", price_source="AdjClose",
    )
    plan = pcp.build_price_cache_rebuild_plan(
        ["DDD"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
    )
    row = plan["rows"][0]
    assert (
        row["recommended_action"]
        == pcp.ACTION_MANUAL_REVIEW
    )
    assert (
        pcp.BLOCKER_PRICE_SOURCE_NOT_CLOSE
        in row["blocker_codes"]
    )


# ---------------------------------------------------------------------------
# 9. Unreadable manifest -> manual_review.
# ---------------------------------------------------------------------------


def test_unreadable_manifest_routes_to_manual_review(
    tmp_path,
):
    scd = tmp_path / "signal_cache"
    pcd = tmp_path / "stackbuilder_price_cache"
    scd.mkdir(parents=True, exist_ok=True)
    pkl_path = scd / "EEE_precomputed_results.pkl"
    pkl_path.write_bytes(b"")
    # Write an unparseable manifest.
    (
        Path(str(pkl_path) + ".manifest.json")
    ).write_text("{not valid json", encoding="utf-8")
    plan = pcp.build_price_cache_rebuild_plan(
        ["EEE"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
    )
    row = plan["rows"][0]
    assert (
        row["recommended_action"]
        == pcp.ACTION_MANUAL_REVIEW
    )
    assert (
        pcp.BLOCKER_UNREADABLE_MANIFEST
        in row["blocker_codes"]
    )


# ---------------------------------------------------------------------------
# 10. Aggregate counts + tickers-by-action consistency.
# ---------------------------------------------------------------------------


def test_aggregate_counts_consistent_with_rows(tmp_path):
    scd = tmp_path / "signal_cache"
    pcd = tmp_path / "stackbuilder_price_cache"
    # Mix: 1 use_existing, 1 needs_source_refresh, 1
    # manual_review (existing price cache).
    _make_signal_cache(scd, "FFF")
    pcd.mkdir(parents=True, exist_ok=True)
    (pcd / "GGG.parquet").write_bytes(b"")
    plan = pcp.build_price_cache_rebuild_plan(
        ["FFF", "GGG", "HHH"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
    )
    counts = plan["counts_by_recommended_action"]
    assert (
        counts[pcp.ACTION_USE_EXISTING_SIGNAL_CACHE] == 1
    )
    assert (
        counts[pcp.ACTION_MANUAL_REVIEW] == 1
    )
    assert (
        counts[pcp.ACTION_NEEDS_SOURCE_REFRESH] == 1
    )
    by_action = plan["tickers_by_recommended_action"]
    assert by_action[
        pcp.ACTION_USE_EXISTING_SIGNAL_CACHE
    ] == ["FFF"]
    assert by_action[
        pcp.ACTION_MANUAL_REVIEW
    ] == ["GGG"]
    assert by_action[
        pcp.ACTION_NEEDS_SOURCE_REFRESH
    ] == ["HHH"]
    # Sum to ticker count.
    assert sum(counts.values()) == plan["ticker_count"]


# ---------------------------------------------------------------------------
# 11. Default universe = Phase 6I-52 pilot universe.
# ---------------------------------------------------------------------------


def test_default_universe_is_phase_6i_52_pilot(tmp_path):
    plan = pcp.build_price_cache_rebuild_plan(
        signal_cache_dir=tmp_path / "empty",
        stackbuilder_price_cache_dir=(
            tmp_path / "empty2"
        ),
    )
    assert plan["ticker_count"] == 25
    row_tickers = [r["ticker"] for r in plan["rows"]]
    assert row_tickers[0] == "SPY"
    # Spot-check a few more.
    assert "AAPL" in row_tickers
    assert "MCD" in row_tickers
    assert "BRK-B" in row_tickers


# ---------------------------------------------------------------------------
# 12. Static guard: no forbidden top-level imports.
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
        / "stackbuilder_price_cache_rebuild_planner.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for n in node.names:
                top_level_names.add(
                    n.name.split(".")[0],
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_names.add(
                    node.module.split(".")[0],
                )
    leaked = (
        top_level_names & _FORBIDDEN_TOP_LEVEL_IMPORTS
    )
    assert not leaked, (
        f"Forbidden top-level imports in price-cache "
        f"rebuild planner: {sorted(leaked)}"
    )


def test_module_source_has_no_pickle_load_call():
    """Belt-and-braces guard: no actual ``pickle.load(``
    or ``pickle_load_compat(`` call expression anywhere in
    the module source. The docstring may mention them in
    prose ("we do NOT use pickle.load"); only the
    call-expression substring is forbidden."""
    here = Path(__file__).resolve().parent.parent
    src = (
        here
        / "stackbuilder_price_cache_rebuild_planner.py"
    ).read_text(encoding="utf-8")
    # Look for the function-call form specifically.
    assert "pickle.load(" not in src
    assert "pickle_load_compat(" not in src
    # And no ``import pickle`` (already covered by the
    # forbidden-imports guard, but mirrored here for
    # explicitness).
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                assert (
                    n.name.split(".")[0] != "pickle"
                ), f"unexpected ``import pickle``: {n.name}"


# ---------------------------------------------------------------------------
# 13. --output path guard rejects production-root paths
#     AND price_cache/daily.
# ---------------------------------------------------------------------------


def test_output_path_guard_rejects_production_and_pcd(
    capsys,
):
    forbidden_outputs = [
        "cache/results/plan.json",
        "cache\\status\\plan.json",
        "output/research_artifacts/plan.json",
        "output/stackbuilder/plan.json",
        "signal_library/data/stable/plan.json",
        # Phase 6I-54a deliberately adds price_cache/daily
        # to the guarded set even though it is not (yet) a
        # documented production root.
        "price_cache/daily/plan.json",
    ]
    for forbidden in forbidden_outputs:
        rc = pcp.main(["--output", forbidden])
        err = capsys.readouterr().err
        assert rc == 2, (
            f"Expected rc=2 for {forbidden!r}; got "
            f"rc={rc}"
        )
        assert (
            "output_path_inside_production_root" in err
        )


# ---------------------------------------------------------------------------
# 14. Future-write contract is well-formed.
# ---------------------------------------------------------------------------


def test_future_write_contract_is_well_formed(tmp_path):
    plan = pcp.build_price_cache_rebuild_plan(
        ["SPY"],
        signal_cache_dir=tmp_path / "scd",
        stackbuilder_price_cache_dir=(
            tmp_path / "pcd"
        ),
    )
    contract = plan["future_write_contract"]
    assert (
        contract["destination_root"]
        == str(tmp_path / "pcd")
    )
    assert contract["output_format_primary"] == "parquet"
    assert contract["output_format_fallback"] == "csv"
    assert contract["required_columns"] == [
        "Date", "Close",
    ]
    assert contract["files_per_ticker"] == 1
    assert contract["uses_network"] is False
    assert contract["uses_yfinance"] is False


# ---------------------------------------------------------------------------
# 15. Production-state smoke: 6/25 use_existing_signal_cache
#     + 19/25 needs_source_refresh against the real
#     cache/results and absent price_cache/daily.
# ---------------------------------------------------------------------------


def test_production_state_classification_matches_expected():
    """Pins the actual Phase 6I-54a verdict against the
    current production state: SPY/AAPL/JNJ/WMT/HD/MCD
    classify as use_existing_signal_cache; the other 19
    pilot tickers classify as needs_source_refresh."""
    plan = pcp.build_price_cache_rebuild_plan()
    counts = plan["counts_by_recommended_action"]
    by_action = plan["tickers_by_recommended_action"]
    assert plan["ticker_count"] == 25
    assert (
        plan["stackbuilder_price_cache_dir_exists"]
        is False
    )
    assert (
        counts[pcp.ACTION_USE_EXISTING_SIGNAL_CACHE] == 6
    )
    assert (
        counts[pcp.ACTION_NEEDS_SOURCE_REFRESH] == 19
    )
    assert (
        counts[pcp.ACTION_NEEDS_NETWORK_FETCH] == 0
    )
    assert (
        counts[pcp.ACTION_MANUAL_REVIEW] == 0
    )
    assert sorted(
        by_action[pcp.ACTION_USE_EXISTING_SIGNAL_CACHE]
    ) == sorted([
        "SPY", "AAPL", "JNJ", "WMT", "HD", "MCD",
    ])
