"""Phase 6I-50 tests for the large-universe Confluence
launch planner + StackBuilder automation policy planner.

Pins:

  * Schema-version + classification taxonomies are stable
    string constants.
  * Strict-full-60-cell artifact -> ``rank_eligible_strict``
    + ``already_board_ranked``.
  * Phase 6I-47 partial-multiwindow artifact ->
    ``rank_eligible_partial`` +
    ``ranking_eligibility_basis=partial_effective_members``
    + ``already_board_ranked``.
  * Phase 6C daily-only artifact -> blocked + cascade picks
    the right "missing piece" recommended action.
  * Artifact missing entirely / unreadable -> the planner
    falls through to the disk-only ingredient cascade.
  * StackBuilder seed-run-dir parsing finds an invalid
    member (TEF) by string match against
    ``DEFAULT_KNOWN_INVALID_MEMBERS`` without opening any
    pickle.
  * Recommended-next-action cascade picks
    ``write_partial_artifact`` when the rest of the chain
    is ready, ``rerun_stackbuilder`` otherwise.
  * Recommended-next-action cascade picks the right
    missing-piece action for the cache / signal-library /
    StackBuilder-run gaps.
  * Ambiguous StackBuilder seed-run-dir selection
    (multiple seed-run dirs) -> ``manual_review`` even
    when otherwise-ready.
  * Aggregate counts + ``counts_by_recommended_next_action``
    + ``proposed_next_batches`` shape is stable.
  * StackBuilder policy block exposes observed defaults +
    proposed defaults + unresolved questions.
  * No forbidden top-level imports in the planner module.
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


import confluence_large_universe_launch_planner as lup  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_artifact_file(
    artifact_root: Path,
    ticker: str,
    payload: dict[str, Any],
) -> Path:
    ticker_dir = artifact_root / "confluence" / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)
    path = (
        ticker_dir
        / f"{ticker}__MTF_CONSENSUS.research_day.json"
    )
    path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )
    return path


def _canonical_windows() -> list[str]:
    return ["1d", "1wk", "1mo", "3mo", "1y"]


def _canonical_k_values() -> list[int]:
    return [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]


def _full_per_window_k_metrics() -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for w in _canonical_windows():
        for k in _canonical_k_values():
            cells.append({
                "K": k,
                "window": w,
                "total_capture_pct": 5.0,
                "sharpe_ratio": 0.5,
                "trigger_days": 2,
                "wins": 2,
                "losses": 0,
                "avg_daily_capture_pct": 2.5,
                "latest_combined_signal": "Buy",
                "latest_buy_count": k,
                "latest_short_count": 0,
                "latest_none_count": 0,
                "latest_missing_count": 0,
                "member_count": k,
            })
    return cells


def _full_build_wide_window_alignment() -> dict[str, Any]:
    s = sum(_canonical_k_values())
    return {
        w: {
            "all_members_firing": True,
            "firing_member_count": s,
            "total_member_count": s,
        }
        for w in _canonical_windows()
    }


def _strict_full_artifact(
    *, target_ticker: str,
) -> dict[str, Any]:
    return {
        "artifact_version": 1,
        "engine": "confluence",
        "generated_at": "2026-05-15T00:00:00+00:00",
        "target_ticker": target_ticker,
        "run_id": "phase_6i50_strict_fixture",
        "timeframes": _canonical_windows(),
        "summary": {
            "total_capture_pct": 50.0,
            "trigger_days": 100,
            "sharpe_ratio": 0.5,
        },
        "daily": {
            "last_date": "2026-05-14",
            "dates": [
                f"2026-05-{d:02d}"
                for d in range(1, 15)
            ],
        },
        "per_window_k_metrics": (
            _full_per_window_k_metrics()
        ),
        "build_wide_window_alignment": (
            _full_build_wide_window_alignment()
        ),
        "multiwindow_k_engine_payload_metadata": {
            "generated_at": "2026-05-15T00:00:00+00:00",
            "target_ticker": target_ticker,
            "cell_count": 60,
            "K_values": _canonical_k_values(),
            "windows": _canonical_windows(),
            "current_as_of_date": "2026-05-14",
            "phase": "6I-23",
        },
    }


def _partial_multiwindow_artifact(
    *, target_ticker: str,
) -> dict[str, Any]:
    """Phase 6I-47 partial-payload artifact contract --
    namespaced top-level key ONLY; strict keys absent."""
    # Only K∈{1..6} effective members for example, so 30
    # cells in the partial effective grid (matches the
    # Phase 6I-49 SPY production state).
    eff: list[dict[str, Any]] = []
    for w in _canonical_windows():
        for k in [1, 2, 3, 4, 5, 6]:
            eff.append({
                "K": k,
                "window": w,
                "total_capture_pct": 5.0,
                "sharpe_ratio": 0.5,
                "trigger_days": 2,
                "wins": 2,
                "losses": 0,
                "avg_daily_capture_pct": 2.5,
                "latest_combined_signal": "Buy",
                "latest_buy_count": k,
                "latest_short_count": 0,
                "latest_none_count": 0,
                "latest_missing_count": 0,
                "member_count": k,
            })
    return {
        "artifact_version": 1,
        "engine": "confluence",
        "generated_at": "2026-05-15T00:00:00+00:00",
        "target_ticker": target_ticker,
        "run_id": "phase_6i50_partial_fixture",
        "timeframes": _canonical_windows(),
        "daily": {
            "last_date": "2026-05-14",
            "dates": [
                f"2026-05-{d:02d}"
                for d in range(1, 15)
            ],
        },
        "multiwindow_k_partial_payload_metadata": {
            "schema_version": (
                "phase_6i_47_partial_multiwindow_v1"
            ),
            "data_completeness_status": "partial",
            "data_warning_symbol": "!",
            "strict_payload_ready": False,
            "strict_patch_ready": False,
            "partial_payload_available": True,
            "prepared_cell_count": 30,
            "effective_cell_count": 30,
            "effective_per_window_k_metrics": eff,
            "original_members": [
                "AWR", "CP", "EXPO", "LLY", "CLH",
                "GBCI", "HCSG", "TEF", "JNJ", "MO",
                "AROW", "PRA",
            ],
            "effective_members": [
                "AWR", "CP", "EXPO", "LLY", "CLH",
                "GBCI", "HCSG", "JNJ", "MO", "AROW",
                "PRA",
            ],
            "excluded_members": ["TEF"],
            "incomplete_member_detail": [
                {
                    "ticker": "TEF",
                    "reason": "invalid_or_delisted",
                    "telemetry_reason": (
                        "provider_fetch_failed_zero_rows"
                    ),
                    "source_classification": (
                        "phase_6i_43_invalid_or_delisted"
                    ),
                    "K": k,
                }
                for k in (7, 8, 9, 10, 11, 12)
            ],
        },
    }


def _daily_only_artifact(
    target_ticker: str,
) -> dict[str, Any]:
    """Phase 6C baseline shape -- no Phase 6I-20 fields."""
    return {
        "artifact_version": 1,
        "engine": "confluence",
        "generated_at": "2026-05-15T00:00:00+00:00",
        "target_ticker": target_ticker,
        "timeframes": _canonical_windows(),
        "summary": {
            "total_capture_pct": 42.4,
            "trigger_days": 870,
            "sharpe_ratio": 0.034,
        },
        "daily": {
            "last_date": "2026-05-08",
        },
    }


def _touch_cache(
    cache_dir: Path, ticker: str,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / f"{ticker}_precomputed_results.pkl"
    p.write_bytes(b"\x80\x04N.")  # minimal pickle of None
    return p


def _touch_full_signal_library(
    signal_library_dir: Path, ticker: str,
) -> None:
    signal_library_dir.mkdir(
        parents=True, exist_ok=True,
    )
    (
        signal_library_dir
        / f"{ticker}_stable_v1_0_0.pkl"
    ).write_bytes(b"\x80\x04N.")
    for interval in ("1wk", "1mo", "3mo", "1y"):
        (
            signal_library_dir
            / f"{ticker}_stable_v1_0_0_{interval}.pkl"
        ).write_bytes(b"\x80\x04N.")


def _touch_stackbuilder_run(
    stackbuilder_root: Path,
    ticker: str,
    seed_run_id: str,
) -> Path:
    ticker_dir = stackbuilder_root / ticker
    ticker_dir.mkdir(parents=True, exist_ok=True)
    seed_dir = ticker_dir / seed_run_id
    seed_dir.mkdir(parents=True, exist_ok=True)
    return seed_dir


# ---------------------------------------------------------------------------
# 0. Schema-version + taxonomy stability
# ---------------------------------------------------------------------------


def test_schema_version_and_taxonomy_constants_are_stable():
    assert (
        lup.SCHEMA_VERSION
        == "confluence_large_universe_launch_planner_v1"
    )
    # Every artifact status used by the cascade must
    # appear in the public taxonomy.
    assert lup.CLASS_STRICT_FULL_60_CELL in (
        lup.ALL_ARTIFACT_STATUSES
    )
    assert lup.CLASS_PARTIAL_MULTIWINDOW in (
        lup.ALL_ARTIFACT_STATUSES
    )
    assert lup.CLASS_DAILY_ONLY in (
        lup.ALL_ARTIFACT_STATUSES
    )
    assert lup.CLASS_ARTIFACT_MISSING in (
        lup.ALL_ARTIFACT_STATUSES
    )
    assert lup.CLASS_UNREADABLE in (
        lup.ALL_ARTIFACT_STATUSES
    )
    # Recommended-action taxonomy includes every action
    # the cascade can pick.
    for action in (
        lup.ACTION_ALREADY_BOARD_RANKED,
        lup.ACTION_WRITE_PARTIAL_ARTIFACT,
        lup.ACTION_WRITE_STRICT_ARTIFACT,
        lup.ACTION_REFRESH_SOURCE_CACHE,
        lup.ACTION_REBUILD_SIGNAL_LIBRARIES,
        lup.ACTION_PROMOTE_SIGNAL_LIBRARIES,
        lup.ACTION_RERUN_STACKBUILDER,
        lup.ACTION_MANUAL_REVIEW,
        lup.ACTION_BLOCKED_MISSING_INPUTS,
    ):
        assert action in lup.ALL_RECOMMENDED_ACTIONS
    # TEF is the canonical default-invalid member.
    assert "TEF" in lup.DEFAULT_KNOWN_INVALID_MEMBERS


# ---------------------------------------------------------------------------
# 1. Strict full-60-cell artifact -> already_board_ranked
# ---------------------------------------------------------------------------


def test_strict_full_artifact_already_board_ranked(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    _write_artifact_file(
        art_root, "AAA",
        _strict_full_artifact(target_ticker="AAA"),
    )
    report = lup.build_large_universe_launch_plan(
        ["AAA"],
        artifact_root=art_root,
        cache_dir=tmp_path / "cache_results",
        signal_library_dir=(
            tmp_path / "signal_library_stable"
        ),
        stackbuilder_root=tmp_path / "stackbuilder",
    )
    assert report["counts"]["inspected_count"] == 1
    row = report["rows"][0]
    assert row["ticker"] == "AAA"
    assert (
        row["artifact_status"]
        == lup.CLASS_STRICT_FULL_60_CELL
    )
    assert (
        row["current_board_status"]
        == lup.BOARD_STATUS_RANK_ELIGIBLE_STRICT
    )
    assert (
        row["ranking_eligibility_basis"]
        == "strict_full_60_cell"
    )
    assert row["data_warning_symbol"] == ""
    assert row["k_cells_available"] == 60
    assert (
        row["recommended_next_action"]
        == lup.ACTION_ALREADY_BOARD_RANKED
    )
    assert (
        report["counts"]["rank_eligible_strict_count"]
        == 1
    )


# ---------------------------------------------------------------------------
# 2. Phase 6I-47 partial artifact -> already_board_ranked
# ---------------------------------------------------------------------------


def test_partial_multiwindow_artifact_already_board_ranked(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    _write_artifact_file(
        art_root, "SPY",
        _partial_multiwindow_artifact(target_ticker="SPY"),
    )
    report = lup.build_large_universe_launch_plan(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=tmp_path / "cache_results",
        signal_library_dir=(
            tmp_path / "signal_library_stable"
        ),
        stackbuilder_root=tmp_path / "stackbuilder",
    )
    row = report["rows"][0]
    assert (
        row["artifact_status"]
        == lup.CLASS_PARTIAL_MULTIWINDOW
    )
    assert (
        row["current_board_status"]
        == lup.BOARD_STATUS_RANK_ELIGIBLE_PARTIAL
    )
    assert (
        row["ranking_eligibility_basis"]
        == "partial_effective_members"
    )
    assert row["data_warning_symbol"] == "!"
    assert row["k_cells_available"] == 30
    assert "TEF" in row["incomplete_members"]
    assert (
        row["recommended_next_action"]
        == lup.ACTION_ALREADY_BOARD_RANKED
    )
    assert (
        report["counts"]["rank_eligible_partial_count"]
        == 1
    )


# ---------------------------------------------------------------------------
# 3. Daily-only artifact: blocked + cascade picks the right
#    missing-piece action
# ---------------------------------------------------------------------------


def test_daily_only_artifact_classifies_blocked_and_cascades(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    _write_artifact_file(
        art_root, "BBB", _daily_only_artifact("BBB"),
    )
    # No cache, no signal library, no stackbuilder run.
    report = lup.build_large_universe_launch_plan(
        ["BBB"],
        artifact_root=art_root,
        cache_dir=tmp_path / "cache_results",
        signal_library_dir=(
            tmp_path / "signal_library_stable"
        ),
        stackbuilder_root=tmp_path / "stackbuilder",
    )
    row = report["rows"][0]
    assert (
        row["artifact_status"]
        == lup.CLASS_DAILY_ONLY
    )
    assert (
        row["current_board_status"]
        == lup.BOARD_STATUS_BLOCKED
    )
    assert row["ranking_eligibility_basis"] is None
    assert (
        row["recommended_next_action"]
        == lup.ACTION_BLOCKED_MISSING_INPUTS
    )
    assert report["counts"]["blocked_count"] == 1
    assert report["counts"]["daily_only_count"] == 1


# ---------------------------------------------------------------------------
# 4. Artifact missing entirely -> blocked + the cascade
#    surfaces the highest-leverage next action
# ---------------------------------------------------------------------------


def test_artifact_missing_blocked_and_cache_refresh_action(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    art_root.mkdir(parents=True, exist_ok=True)
    # Cache + signal library + StackBuilder run all ready
    # so the cascade can surface write_strict_artifact.
    cache_dir = tmp_path / "cache_results"
    sl_dir = tmp_path / "signal_library_stable"
    sb_root = tmp_path / "stackbuilder"
    _touch_cache(cache_dir, "CCC")
    _touch_full_signal_library(sl_dir, "CCC")
    _touch_stackbuilder_run(
        sb_root, "CCC", "seedTC__AAA-D_BBB-I",
    )
    report = lup.build_large_universe_launch_plan(
        ["CCC"],
        artifact_root=art_root,
        cache_dir=cache_dir,
        signal_library_dir=sl_dir,
        stackbuilder_root=sb_root,
    )
    row = report["rows"][0]
    assert (
        row["artifact_status"]
        == lup.CLASS_ARTIFACT_MISSING
    )
    assert (
        row["current_board_status"]
        == lup.BOARD_STATUS_BLOCKED
    )
    assert (
        row["recommended_next_action"]
        == lup.ACTION_WRITE_STRICT_ARTIFACT
    )
    assert (
        report["counts"]["missing_artifact_count"] == 1
    )


# ---------------------------------------------------------------------------
# 5. Unreadable artifact -> manual_review
# ---------------------------------------------------------------------------


def test_unreadable_artifact_routes_to_manual_review(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    ticker_dir = art_root / "confluence" / "DDD"
    ticker_dir.mkdir(parents=True, exist_ok=True)
    (
        ticker_dir
        / "DDD__MTF_CONSENSUS.research_day.json"
    ).write_text("not json :(", encoding="utf-8")
    report = lup.build_large_universe_launch_plan(
        ["DDD"],
        artifact_root=art_root,
        cache_dir=tmp_path / "cache_results",
        signal_library_dir=(
            tmp_path / "signal_library_stable"
        ),
        stackbuilder_root=tmp_path / "stackbuilder",
    )
    row = report["rows"][0]
    assert (
        row["artifact_status"]
        == lup.CLASS_UNREADABLE
    )
    assert (
        row["recommended_next_action"]
        == lup.ACTION_MANUAL_REVIEW
    )


# ---------------------------------------------------------------------------
# 6. Seed-run-dir parser finds TEF without opening any
#    pickle. The cascade picks write_partial_artifact when
#    cache + signal-library are ready.
# ---------------------------------------------------------------------------


def test_seed_run_invalid_member_routes_to_partial_write(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    art_root.mkdir(parents=True, exist_ok=True)
    cache_dir = tmp_path / "cache_results"
    sl_dir = tmp_path / "signal_library_stable"
    sb_root = tmp_path / "stackbuilder"
    _touch_cache(cache_dir, "EEE")
    _touch_full_signal_library(sl_dir, "EEE")
    # The seed-run name carries TEF as a member.
    _touch_stackbuilder_run(
        sb_root,
        "EEE",
        "seedTC__AWR-D_CP-I_TEF-I_JNJ-I",
    )
    report = lup.build_large_universe_launch_plan(
        ["EEE"],
        artifact_root=art_root,
        cache_dir=cache_dir,
        signal_library_dir=sl_dir,
        stackbuilder_root=sb_root,
    )
    row = report["rows"][0]
    assert (
        row["stackbuilder_status"]
        == (
            lup.STACKBUILDER_STATUS_CONTAINS_INVALID_MEMBERS
        )
    )
    assert "TEF" in row["invalid_or_delisted_members"]
    assert (
        row["recommended_next_action"]
        == lup.ACTION_WRITE_PARTIAL_ARTIFACT
    )
    # And the parser produced the (ticker, mode) shape we
    # documented.
    members = row["selected_stackbuilder_run_members"]
    assert ["AWR", "D"] in members
    assert ["TEF", "I"] in members


# ---------------------------------------------------------------------------
# 7. Seed-run invalid-member but cache missing ->
#    rerun_stackbuilder (cascade picks the higher-leverage
#    action when the chain isn't ready)
# ---------------------------------------------------------------------------


def test_seed_run_invalid_member_with_missing_cache_reroutes_to_rerun(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    art_root.mkdir(parents=True, exist_ok=True)
    cache_dir = tmp_path / "cache_results"
    sl_dir = tmp_path / "signal_library_stable"
    sb_root = tmp_path / "stackbuilder"
    # NO cache + NO signal library.
    _touch_stackbuilder_run(
        sb_root,
        "FFF",
        "seedTC__AWR-D_TEF-I_JNJ-I",
    )
    report = lup.build_large_universe_launch_plan(
        ["FFF"],
        artifact_root=art_root,
        cache_dir=cache_dir,
        signal_library_dir=sl_dir,
        stackbuilder_root=sb_root,
    )
    row = report["rows"][0]
    assert (
        row["stackbuilder_status"]
        == (
            lup.STACKBUILDER_STATUS_CONTAINS_INVALID_MEMBERS
        )
    )
    assert (
        row["recommended_next_action"]
        == lup.ACTION_RERUN_STACKBUILDER
    )


# ---------------------------------------------------------------------------
# 8. Everything missing -> blocked_missing_inputs (artifact
#    + cache + stackbuilder run all absent).
# ---------------------------------------------------------------------------


def test_no_ingredients_routes_to_blocked_missing_inputs(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    art_root.mkdir(parents=True, exist_ok=True)
    report = lup.build_large_universe_launch_plan(
        ["GGG"],
        artifact_root=art_root,
        cache_dir=tmp_path / "cache_results",
        signal_library_dir=(
            tmp_path / "signal_library_stable"
        ),
        stackbuilder_root=tmp_path / "stackbuilder",
    )
    row = report["rows"][0]
    assert (
        row["artifact_status"]
        == lup.CLASS_ARTIFACT_MISSING
    )
    assert (
        row["cache_status"] == lup.CACHE_STATUS_MISSING
    )
    assert (
        row["stackbuilder_status"]
        == lup.STACKBUILDER_STATUS_RUN_MISSING
    )
    assert (
        row["recommended_next_action"]
        == lup.ACTION_BLOCKED_MISSING_INPUTS
    )


# ---------------------------------------------------------------------------
# 9. Cache ready + stable signal library MISSING +
#    StackBuilder run ready -> rebuild_signal_libraries.
#    Same fixture in staged-possible state ->
#    promote_signal_libraries.
# ---------------------------------------------------------------------------


def test_signal_library_missing_routes_to_rebuild_then_promote(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    art_root.mkdir(parents=True, exist_ok=True)
    cache_dir = tmp_path / "cache_results"
    sl_dir = tmp_path / "signal_library_stable"
    sb_root = tmp_path / "stackbuilder"
    _touch_cache(cache_dir, "HHH")
    _touch_stackbuilder_run(
        sb_root, "HHH", "seedTC__AAA-D_BBB-I",
    )
    # No signal library at all -> rebuild.
    report = lup.build_large_universe_launch_plan(
        ["HHH"],
        artifact_root=art_root,
        cache_dir=cache_dir,
        signal_library_dir=sl_dir,
        stackbuilder_root=sb_root,
    )
    row = report["rows"][0]
    assert (
        row["signal_library_status"]
        == lup.SIGNAL_LIBRARY_STATUS_STABLE_MISSING
    )
    assert (
        row["recommended_next_action"]
        == lup.ACTION_REBUILD_SIGNAL_LIBRARIES
    )
    # Now stage only the base + 3 of 4 intervals -> staged_possible.
    sl_dir.mkdir(parents=True, exist_ok=True)
    (sl_dir / "HHH_stable_v1_0_0.pkl").write_bytes(
        b"\x80\x04N.",
    )
    for interval in ("1wk", "1mo", "3mo"):
        (
            sl_dir
            / f"HHH_stable_v1_0_0_{interval}.pkl"
        ).write_bytes(b"\x80\x04N.")
    # Missing the "1y" -> staged_possible.
    report2 = lup.build_large_universe_launch_plan(
        ["HHH"],
        artifact_root=art_root,
        cache_dir=cache_dir,
        signal_library_dir=sl_dir,
        stackbuilder_root=sb_root,
    )
    row2 = report2["rows"][0]
    assert (
        row2["signal_library_status"]
        == lup.SIGNAL_LIBRARY_STATUS_STAGED_POSSIBLE
    )
    assert (
        row2["recommended_next_action"]
        == lup.ACTION_PROMOTE_SIGNAL_LIBRARIES
    )


# ---------------------------------------------------------------------------
# 10. Ambiguous StackBuilder selection (>1 seed-run dirs)
#     even with everything ready -> manual_review.
# ---------------------------------------------------------------------------


def test_ambiguous_stackbuilder_selection_routes_to_manual_review(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    art_root.mkdir(parents=True, exist_ok=True)
    cache_dir = tmp_path / "cache_results"
    sl_dir = tmp_path / "signal_library_stable"
    sb_root = tmp_path / "stackbuilder"
    _touch_cache(cache_dir, "III")
    _touch_full_signal_library(sl_dir, "III")
    # TWO seed-run dirs (no invalid members in either).
    _touch_stackbuilder_run(
        sb_root, "III", "seedTC__AAA-D_BBB-I",
    )
    _touch_stackbuilder_run(
        sb_root, "III", "seedTC__CCC-D_DDD-I",
    )
    report = lup.build_large_universe_launch_plan(
        ["III"],
        artifact_root=art_root,
        cache_dir=cache_dir,
        signal_library_dir=sl_dir,
        stackbuilder_root=sb_root,
    )
    row = report["rows"][0]
    assert (
        row["stackbuilder_status"]
        == (
            lup.STACKBUILDER_STATUS_RUN_STALE_OR_AMBIGUOUS
        )
    )
    assert row["stackbuilder_ambiguous_selection"] is True
    assert (
        row["recommended_next_action"]
        == lup.ACTION_MANUAL_REVIEW
    )


# ---------------------------------------------------------------------------
# 11. Aggregate counts + proposed_next_batches +
#     StackBuilder policy block shape stability.
# ---------------------------------------------------------------------------


def test_aggregate_report_shape_and_stackbuilder_policy(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    cache_dir = tmp_path / "cache_results"
    sl_dir = tmp_path / "signal_library_stable"
    sb_root = tmp_path / "stackbuilder"
    # Three tickers across a few states.
    _write_artifact_file(
        art_root, "AAA",
        _strict_full_artifact(target_ticker="AAA"),
    )
    _write_artifact_file(
        art_root, "BBB",
        _partial_multiwindow_artifact(target_ticker="BBB"),
    )
    # CCC has no artifact, but a partial-write-ready chain.
    _touch_cache(cache_dir, "CCC")
    _touch_full_signal_library(sl_dir, "CCC")
    _touch_stackbuilder_run(
        sb_root, "CCC",
        "seedTC__AAA-D_TEF-I_JNJ-I",
    )
    report = lup.build_large_universe_launch_plan(
        ["AAA", "BBB", "CCC"],
        artifact_root=art_root,
        cache_dir=cache_dir,
        signal_library_dir=sl_dir,
        stackbuilder_root=sb_root,
    )
    # Aggregate counts: 1 strict, 1 partial, 1 blocked.
    assert (
        report["counts"]["rank_eligible_strict_count"]
        == 1
    )
    assert (
        report["counts"]["rank_eligible_partial_count"]
        == 1
    )
    assert report["counts"]["blocked_count"] == 1
    # counts_by_recommended_next_action present + sums.
    by_action = report[
        "counts_by_recommended_next_action"
    ]
    assert sum(by_action.values()) == 3
    assert (
        by_action.get(lup.ACTION_ALREADY_BOARD_RANKED, 0)
        == 2
    )
    assert (
        by_action.get(
            lup.ACTION_WRITE_PARTIAL_ARTIFACT, 0,
        )
        == 1
    )
    # proposed_next_batches bucketing.
    batches = report["proposed_next_batches"]
    assert (
        sorted(batches["batch_1_no_write_board_render"])
        == ["AAA", "BBB"]
    )
    assert batches["batch_2_partial_writes"] == ["CCC"]
    assert batches[
        "batch_3_signal_library_refresh_or_promotion"
    ] == []
    assert batches["batch_4_stackbuilder_reruns"] == []
    # StackBuilder policy block exposes the documented
    # sections.
    policy = report["stackbuilder_policy"]
    assert (
        policy["observed_defaults_from_source"]["top_n"]
        == 20
    )
    assert (
        policy["observed_defaults_from_source"]["max_k"]
        == 6
    )
    assert (
        policy["proposed_launch_defaults"]["search"]
        == "beam"
    )
    # All six launch-policy questions are now settled at
    # the launch planner; the unresolved-questions list
    # is empty. Settlement does NOT auto-authorize the
    # large-universe launch -- the rollout planner's
    # launch-authorization gate remains fail-closed by
    # default (verified separately in
    # test_confluence_large_universe_rollout_batch_planner).
    assert (
        len(policy["unresolved_policy_questions"]) == 0
    )
    assert "CCC" in (
        policy["tickers_with_invalid_members_in_run"]
    )
    # Schema version + universe_mode + invalid_members
    # carry through.
    assert (
        report["schema_version"]
        == "confluence_large_universe_launch_planner_v1"
    )
    assert (
        report["universe_mode"]
        == lup.UNIVERSE_MODE_EXPLICIT_TICKERS
    )
    assert "TEF" in report["invalid_members"]


# ---------------------------------------------------------------------------
# 12. Static guard: no forbidden top-level imports.
# ---------------------------------------------------------------------------


_FORBIDDEN_TOP_LEVEL_IMPORTS = frozenset({
    "yfinance",
    "subprocess",
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
        here / "confluence_large_universe_launch_planner.py"
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
        f"Forbidden top-level imports in planner: "
        f"{sorted(leaked)}"
    )


# ---------------------------------------------------------------------------
# Phase 6I-50 amendment-1 tests: pin the corrected
# StackBuilder CLI facts that Codex flagged.
#
#   * The documented command template MUST use ``--secondary
#     <TICKER>`` (NOT ``--ticker <TICKER>`` -- which is not
#     a real stackbuilder.py argument).
#   * Observed defaults k_patience=1 and
#     combine_mode='intersection' MUST match the actual
#     argparse defaults in stackbuilder.parse_args.
#   * Unresolved policy questions MUST NOT claim that
#     stackbuilder.py lacks a ``--combine-mode`` CLI
#     argument (since the original Phase 6I-50 block did
#     make that claim, and it was wrong).
# ---------------------------------------------------------------------------


def test_command_template_uses_secondary_not_ticker():
    """The documented command template must use the real
    StackBuilder entry argument ``--secondary <TICKER>``,
    NOT the fabricated ``--ticker <TICKER>`` that the
    original Phase 6I-50 block used."""
    report = lup.build_large_universe_launch_plan(
        [],
        artifact_root=None,
        cache_dir=None,
        signal_library_dir=None,
        stackbuilder_root=None,
    )
    template = (
        report["stackbuilder_policy"][
            "documented_stackbuilder_command_template"
        ]
    )
    assert "--secondary <TICKER>" in template, (
        f"command template should reference "
        f"--secondary <TICKER>; got: {template!r}"
    )
    # Hard guard against regressions to the wrong flag.
    assert "--ticker " not in template, (
        f"command template should NOT use --ticker; "
        f"got: {template!r}"
    )
    # Phase 6I-50 amendment-1 also pins that the
    # ``--combine-mode intersection`` default is
    # explicitly surfaced in the template (the operator
    # asked for it to be present when the proposed launch
    # default keeps ``intersection``).
    assert "--combine-mode intersection" in template, (
        f"command template should pin "
        f"--combine-mode intersection; got: {template!r}"
    )


def test_observed_defaults_match_stackbuilder_parse_args_defaults():
    """Compare the planner's
    ``STACKBUILDER_OBSERVED_DEFAULTS`` block against the
    actual argparse defaults emitted by
    ``stackbuilder.parse_args([])``. Pins the
    Phase 6I-50 amendment-1 corrections so a future drift
    between the planner doc-block and the real CLI is
    caught by this test rather than by the next Codex
    audit."""
    # Deferred import to keep the planner module's
    # top-level import surface clean (the planner itself
    # NEVER imports stackbuilder; this test does, in test
    # code).
    import stackbuilder

    ns = stackbuilder.parse_args([])
    observed = lup.STACKBUILDER_OBSERVED_DEFAULTS

    # Phase 6I-50 amendment-1 pins, with k_patience updated
    # to 1 per the operator-decided carryforward item #3
    # engine-to-runner alignment.
    assert ns.k_patience == 1
    assert observed["k_patience"] == ns.k_patience
    assert ns.combine_mode == "intersection"
    assert observed["combine_mode"] == ns.combine_mode

    # And the unchanged defaults from the original block.
    assert observed["top_n"] == ns.top_n == 20
    assert observed["bottom_n"] == ns.bottom_n == 20
    assert observed["max_k"] == ns.max_k == 6
    assert observed["search"] == ns.search == "beam"
    assert observed["beam_width"] == ns.beam_width == 12
    assert (
        observed["exhaustive_k"] == ns.exhaustive_k == 4
    )
    assert observed["both_modes"] == ns.both_modes is False
    assert observed["alpha"] == ns.alpha == 0.05
    assert (
        observed["min_marginal_capture"]
        == ns.min_marginal_capture == 0.0
    )
    assert (
        observed["seed_by"] == ns.seed_by
        == "total_capture"
    )

    # The entry argument is ``--secondary`` (not
    # ``--ticker``). The planner records this explicitly
    # so an aggregate consumer can read it without parsing
    # the command template string.
    assert observed["entry_argument"] == "--secondary"


def test_combine_mode_is_settled_to_intersection_not_unresolved():
    """Phase 6I-50 amendment-1 reworded the combine_mode
    unresolved-policy entry to no longer incorrectly
    claim the CLI lacks a ``--combine-mode`` argument.
    The operator has now ratified combine_mode as
    intersection; the entry must live in
    STACKBUILDER_SETTLED_POLICY_DECISIONS rather than the
    unresolved list, and the recorded value must be
    ``intersection`` with rationale and evidence
    fields."""
    # No combine_mode entry remains in the (now-empty)
    # unresolved-questions tuple.
    questions = list(
        lup.STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS,
    )
    combine_entries = [
        q for q in questions
        if q.startswith("combine_mode")
    ]
    assert combine_entries == []
    # Settled-policy entry records the ratified value.
    settled = lup.STACKBUILDER_SETTLED_POLICY_DECISIONS
    assert "combine_mode" in settled
    entry = settled["combine_mode"]
    assert entry["value"] == "intersection"
    # Rationale must not regress to the historical
    # ``does NOT expose`` / ``lacks --combine-mode``
    # wording the original Phase 6I-50 block carried.
    rationale = entry["rationale"]
    assert "does NOT expose" not in rationale
    assert "lacks ``--combine-mode``" not in rationale
    # Evidence must reference the engine default and the
    # rollout-policy lock so future readers can audit.
    evidence = entry["evidence"]
    assert "intersection" in evidence
    assert "POLICY_COMBINE_MODE" in evidence


# ---------------------------------------------------------------------------
# Carryforward item #4 reconciliation: re-run cadence is settled, not open.
# ---------------------------------------------------------------------------


def test_rerun_cadence_is_no_longer_unresolved_and_appears_as_settled_policy():
    """Carryforward item #4 resolved re-run cadence as operator-managed
    (manual_supervised) with no scheduler. The launch planner must no
    longer list re-run cadence as an unresolved policy question and
    must record it as a settled policy decision instead."""
    questions = list(
        lup.STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS,
    )
    # No remaining entry starts with the cadence wording.
    cadence_entries = [
        q for q in questions if q.startswith("Re-run cadence")
    ]
    assert cadence_entries == []
    # After the five-question settlement PR, all launch-
    # policy questions are settled; the unresolved-list
    # is empty.
    assert len(questions) == 0
    # Settled-policy block now records cadence with the
    # locked value and an evidence pointer that future
    # readers can audit.
    settled = lup.STACKBUILDER_SETTLED_POLICY_DECISIONS
    assert isinstance(settled, dict)
    assert "rerun_cadence" in settled
    cadence_entry = settled["rerun_cadence"]
    assert cadence_entry["value"] == "manual_supervised"
    assert "carryforward item #4" in cadence_entry["evidence"].lower()
    assert (
        "POLICY_RERUN_CADENCE" in cadence_entry["evidence"]
    )


def test_settled_policy_decisions_appear_in_stackbuilder_policy_block():
    """The build_large_universe_launch_plan report exposes the
    settled_policy_decisions key alongside observed defaults, proposed
    defaults, and the (now-empty) unresolved-policy-questions list.
    The launch-authorization gate is verified separately in the
    rollout-batch-planner tests; this test only pins the launch
    planner's report shape."""
    report = lup.build_large_universe_launch_plan(
        [],
        artifact_root=None,
        cache_dir=None,
        signal_library_dir=None,
        stackbuilder_root=None,
    )
    policy = report["stackbuilder_policy"]
    assert "settled_policy_decisions" in policy
    settled = policy["settled_policy_decisions"]
    assert isinstance(settled, dict)
    assert "rerun_cadence" in settled
    assert settled["rerun_cadence"]["value"] == "manual_supervised"
    # All six launch-policy questions are now settled; the
    # unresolved-questions list is present, still a list,
    # and empty.
    assert isinstance(policy["unresolved_policy_questions"], list)
    assert len(policy["unresolved_policy_questions"]) == 0


# ---------------------------------------------------------------------------
# Five-question settlement: each new settled-policy entry exists with the
# expected operator-ratified value, plus the member_universe_sizing guard.
# ---------------------------------------------------------------------------


def test_unresolved_policy_questions_is_empty_tuple_after_full_settlement():
    """All six launch-policy questions are now settled; the unresolved
    tuple is empty and the symbol is still defined for downstream code
    that iterates over it."""
    assert isinstance(
        lup.STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS, tuple,
    )
    assert lup.STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS == ()


def test_both_modes_settled_to_false():
    """Operator-ratified: large-universe launch stays single-direction."""
    entry = lup.STACKBUILDER_SETTLED_POLICY_DECISIONS["both_modes"]
    assert entry["value"] is False
    assert "single-direction" in entry["rationale"]
    assert "2^K" in entry["rationale"]
    assert "POLICY_BOTH_MODES" in entry["evidence"]


def test_seed_by_settled_to_total_capture():
    """Operator-ratified: total_capture is the only engine-supported axis
    after Phase 6I-73."""
    entry = lup.STACKBUILDER_SETTLED_POLICY_DECISIONS["seed_by"]
    assert entry["value"] == "total_capture"
    assert "Phase 6I-73" in entry["rationale"]
    assert "choices=['total_capture']" in entry["evidence"]


def test_optimize_by_settled_to_total_capture():
    """Operator-ratified: total_capture; matches seed_by; explicit pin
    for audit clarity even though the engine auto-resolves an unset
    --optimize-by to --seed-by."""
    entry = lup.STACKBUILDER_SETTLED_POLICY_DECISIONS["optimize_by"]
    assert entry["value"] == "total_capture"
    assert "seed_by" in entry["rationale"]
    assert "choices=['total_capture']" in entry["evidence"]


def test_invalid_member_rotation_settled_to_partial_effective_with_warning():
    """Operator-ratified: keep the current Phase 6I-43 + Phase 6I-46/47/48/49
    partial-payload contract; auto-substitute deferred because it requires
    fresh design and build."""
    entry = lup.STACKBUILDER_SETTLED_POLICY_DECISIONS[
        "invalid_member_rotation"
    ]
    assert (
        entry["value"] == "partial_effective_members_with_warning"
    )
    assert "Auto-substitution is deferred" in entry["rationale"]
    assert (
        "POLICY_INVALID_MEMBER_ROTATION" in entry["evidence"]
    )


def test_member_universe_sizing_settled_to_k6_mtf_member_structure_without_pin():
    """Operator-ratified: launch uses the K=6 MTF launch-path member
    structure. The entry must NOT pin the legacy '12' member-count
    observation and must NOT pin any 'member_universe_size' number;
    variable per-ticker sizing is deferred to Phase 7+."""
    entry = lup.STACKBUILDER_SETTLED_POLICY_DECISIONS[
        "member_universe_sizing"
    ]
    assert entry["value"] == "fixed_k6_mtf_member_structure"
    # Phase 7+ deferral wording.
    assert "Phase 7+" in entry["rationale"]
    # Evidence cites the K=6 MTF launch path contract.
    assert (
        "2026-05-27_K6_MTF_LAUNCH" in entry["evidence"]
    )
    # Guard: no literal "12" and no "member_universe_size"
    # appear anywhere in this entry. The Phase 6I-52
    # amendment-1 background-only classification of the
    # legacy SPY 12-member observation must not be
    # re-elevated into a locked guarantee here.
    flat = " ".join(str(v) for v in entry.values())
    assert "12" not in flat
    assert "member_universe_size" not in flat
