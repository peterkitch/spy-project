"""Phase 6I-48 tests: partial multi-window artifacts may
rank-eligible with an explicit ``partial_effective_members``
basis + a visible ``!`` warning. Strict Phase 6I-20
complete-payload contract is preserved verbatim.

Pins:

  1. New schema fields exist on the partial namespaced
     block + on the payload builder report:
     ``effective_per_window_k_metrics``,
     ``effective_build_wide_window_alignment``,
     ``effective_cell_count``.

  2. New ranking-eligibility-basis taxonomy is exposed:
     ``strict_full_60_cell`` /
     ``partial_effective_members``.

  3. A strict complete artifact still ranks under
     ``rank_eligible=True`` +
     ``data_status='full_60_cell'`` +
     ``ranking_eligibility_basis='strict_full_60_cell'``.

  4. A partial-only artifact carrying effective metrics
     produces a rank-eligible row:
       - ``rank_eligible=True``
       - ``data_status='partial_multiwindow'``
       - ``data_completeness.data_completeness_status='partial'``
       - ``data_completeness.data_warning_symbol='!'``
       - ``ranking_eligibility_basis='partial_effective_members'``
       - excluded / incomplete member detail present
       - strict gates NOT touched
       - ranking_blocked_reason is None

  5. A partial-only artifact carrying zero prepared cells
     (or missing ``effective_per_window_k_metrics``)
     remains blocked with
     ``ranking_blocked_reason='partial_multiwindow_only'``
     (Phase 6I-47 behaviour preserved).

  6. One ticker -> one row in the export.

  7. Sort values on a partial-ranked row carry real
     numeric data for Total Capture / Sharpe / Trigger
     Days so the website sort works.

  8. The partial block in the artifact MUST NOT carry the
     strict Phase 6I-20 keys
     (``per_window_k_metrics`` /
     ``build_wide_window_alignment`` /
     ``multiwindow_k_engine_payload_metadata``); if it
     does, the ranking export refuses to promote the
     ticker.

  9. The Phase 6I-47 partial-block writer-side validator
     still rejects strict keys inside the partial block.

 10. Static guard: pre-Phase-6I-48 ``data_warning_symbol``
     plumbing (Phase 6I-40) continues to flow through the
     row's ``data_completeness`` block so the renderer
     auto-shows the warning.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_multiwindow_ranking_export as cre  # noqa: E402
import multiwindow_k_confluence_patch_planner as pp  # noqa: E402
import multiwindow_k_engine_payload_builder as pb  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _effective_cell(
    *, K: int, window: str, capture: float,
    sharpe: float, trigger_days: int,
    latest: str = "Buy",
) -> dict[str, Any]:
    return {
        "K": K,
        "window": window,
        "total_capture_pct": capture,
        "sharpe_ratio": sharpe,
        "trigger_days": trigger_days,
        "wins": max(trigger_days // 2, 0),
        "losses": 0,
        "avg_daily_capture_pct": 0.5,
        "latest_combined_signal": latest,
        "latest_buy_count": (
            K if latest == "Buy" else 0
        ),
        "latest_short_count": (
            K if latest == "Short" else 0
        ),
        "latest_none_count": 0,
        "latest_missing_count": 0,
        "member_count": K,
    }


def _good_partial_block_with_effective_metrics():
    """Partial namespaced block carrying effective metrics
    for K=1..6 across all 5 canonical windows (30 cells)."""
    effective_cells: list[dict[str, Any]] = []
    for w in cre.CANONICAL_WINDOWS:
        for K in (1, 2, 3, 4, 5, 6):
            effective_cells.append(
                _effective_cell(
                    K=K, window=w,
                    capture=1.5,
                    sharpe=0.4,
                    trigger_days=5,
                    latest="Buy",
                )
            )
    effective_alignment = {
        w: {
            "all_members_firing": False,
            "firing_member_count": 6,
            "total_member_count": 11,
        }
        for w in cre.CANONICAL_WINDOWS
    }
    return {
        "schema_version": (
            pp.PARTIAL_PAYLOAD_SCHEMA_VERSION
        ),
        "generated_at": "2026-05-15T00:00:00Z",
        "target_ticker": "SPY",
        "current_as_of_date": "2026-05-14",
        "data_completeness_status": "partial",
        "data_warning_symbol": "!",
        "original_members_by_K": {},
        "effective_members_by_K": {},
        "excluded_members_by_K": {},
        "incomplete_member_detail": [
            {
                "K": 7,
                "ticker": "TEF",
                "reason": "invalid_or_delisted",
                "telemetry_reason": (
                    "provider_fetch_failed_zero_rows"
                ),
                "source_classification": (
                    "phase_6i_43_invalid_or_delisted"
                ),
            },
        ],
        "prepared_cell_count": 30,
        "skipped_cell_count": 30,
        "expected_canonical_cell_count": 60,
        "counts_by_skipped_reason": {
            "unprepared_due_to_excluded_members": 30,
        },
        "skipped_cells": [],
        "partial_payload_available": True,
        "strict_payload_ready": False,
        "strict_patch_ready": False,
        "reason": pp.PARTIAL_PAYLOAD_REASON,
        "effective_per_window_k_metrics": (
            effective_cells
        ),
        "effective_build_wide_window_alignment": (
            effective_alignment
        ),
        "effective_cell_count": len(effective_cells),
    }


def _partial_only_artifact_with_effective_metrics():
    return {
        "ticker": "SPY",
        "generated_at": "2026-05-15T00:00:00Z",
        "multiwindow_k_partial_payload_metadata": (
            _good_partial_block_with_effective_metrics()
        ),
    }


def _partial_only_artifact_without_effective_metrics():
    """Phase 6I-47 baseline: partial block lacks
    ``effective_per_window_k_metrics``."""
    block = (
        _good_partial_block_with_effective_metrics()
    )
    block.pop("effective_per_window_k_metrics")
    block["prepared_cell_count"] = 0
    return {
        "ticker": "SPY",
        "generated_at": "2026-05-15T00:00:00Z",
        "multiwindow_k_partial_payload_metadata": block,
    }


def _partial_only_artifact_with_strict_key_smuggle():
    """Defensive fixture: partial block carries a strict
    key. The ranking export must refuse to promote."""
    block = (
        _good_partial_block_with_effective_metrics()
    )
    block["per_window_k_metrics"] = []  # forbidden!
    return {
        "ticker": "SPY",
        "generated_at": "2026-05-15T00:00:00Z",
        "multiwindow_k_partial_payload_metadata": block,
    }


def _write_artifact(
    tmp_path: Path, payload: Mapping[str, Any],
) -> Path:
    ticker_dir = tmp_path / "confluence" / "SPY"
    ticker_dir.mkdir(parents=True)
    artifact_path = (
        ticker_dir
        / "SPY__MTF_CONSENSUS.research_day.json"
    )
    artifact_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return artifact_path


# ---------------------------------------------------------------------------
# 1. Schema constants exported
# ---------------------------------------------------------------------------


def test_ranking_eligibility_basis_constants_exposed():
    assert cre.RANKING_ELIGIBILITY_BASIS_STRICT_FULL_60_CELL == (
        "strict_full_60_cell"
    )
    assert (
        cre.RANKING_ELIGIBILITY_BASIS_PARTIAL_EFFECTIVE_MEMBERS
        == "partial_effective_members"
    )
    assert set(
        cre.ALL_RANKING_ELIGIBILITY_BASES,
    ) == {
        "strict_full_60_cell",
        "partial_effective_members",
    }


def test_payload_report_effective_fields_exist():
    """Default report has the new effective fields at
    their empty defaults (no behaviour change for callers
    that ignore them)."""
    report = pb.MultiWindowKEnginePayloadReport(
        generated_at="now",
        target_ticker="SPY",
        payload_ready=False,
        K_values=(),
        windows=(),
        cell_count=0,
    )
    assert report.effective_per_window_k_metrics == []
    assert (
        report.effective_build_wide_window_alignment == {}
    )
    assert report.effective_cell_count == 0


def test_payload_report_json_carries_effective_fields():
    """``to_json_dict`` surfaces the new effective fields."""
    report = pb.MultiWindowKEnginePayloadReport(
        generated_at="now",
        target_ticker="SPY",
        payload_ready=False,
        K_values=(),
        windows=(),
        cell_count=0,
        effective_per_window_k_metrics=[{"K": 1, "x": 1}],
        effective_build_wide_window_alignment={"1d": {"a": 1}},
        effective_cell_count=1,
    )
    j = report.to_json_dict()
    assert (
        j["effective_per_window_k_metrics"]
        == [{"K": 1, "x": 1}]
    )
    assert (
        j["effective_build_wide_window_alignment"]
        == {"1d": {"a": 1}}
    )
    assert j["effective_cell_count"] == 1


# ---------------------------------------------------------------------------
# 2. Strict complete row still ranks under strict basis
# ---------------------------------------------------------------------------


def _strict_full_artifact():
    """60 cells across the canonical grid."""
    pwk: list[dict[str, Any]] = []
    for w in cre.CANONICAL_WINDOWS:
        for K in cre.CANONICAL_K_VALUES:
            pwk.append({
                "K": K,
                "window": w,
                "total_capture_pct": 5.0,
                "sharpe_ratio": 0.5,
                "trigger_days": 2,
                "wins": 2,
                "losses": 0,
                "avg_daily_capture_pct": 2.5,
                "latest_combined_signal": "Buy",
                "latest_buy_count": K,
                "latest_short_count": 0,
                "latest_none_count": 0,
                "latest_missing_count": 0,
                "member_count": K,
            })
    bwwa = {
        w: {
            "all_members_firing": True,
            "firing_member_count": sum(
                cre.CANONICAL_K_VALUES,
            ),
            "total_member_count": sum(
                cre.CANONICAL_K_VALUES,
            ),
        }
        for w in cre.CANONICAL_WINDOWS
    }
    return {
        "artifact_version": 1,
        "engine": "confluence",
        "generated_at": "2026-05-15T00:00:00Z",
        "target_ticker": "SPY",
        "run_id": "phase_6i48_strict_fixture",
        "timeframes": list(cre.CANONICAL_WINDOWS),
        "summary": {
            "total_capture_pct": 50.0,
            "trigger_days": 100,
            "sharpe_ratio": 0.5,
        },
        "daily": {
            "last_date": "2026-05-14",
            "dates": [
                f"2026-05-{d:02d}" for d in range(1, 15)
            ],
        },
        "per_window_k_metrics": pwk,
        "build_wide_window_alignment": bwwa,
        "multiwindow_k_engine_payload_metadata": {
            "generated_at": "2026-05-15T00:00:00Z",
            "target_ticker": "SPY",
            "cell_count": 60,
            "K_values": list(cre.CANONICAL_K_VALUES),
            "windows": list(cre.CANONICAL_WINDOWS),
            "current_as_of_date": "2026-05-14",
            "phase": "6I-23",
        },
    }


def test_strict_complete_row_basis_is_strict_full_60_cell(
    tmp_path,
):
    """Strict complete artifact still ranks; basis carries
    the new strict-full-60-cell tag."""
    art_root = tmp_path / "research_artifacts"
    ticker_dir = art_root / "confluence" / "SPY"
    ticker_dir.mkdir(parents=True)
    (
        ticker_dir
        / "SPY__MTF_CONSENSUS.research_day.json"
    ).write_text(
        json.dumps(_strict_full_artifact()),
        encoding="utf-8",
    )
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    assert report.eligible_count == 1
    assert report.blocked_count == 0
    row = report.ranking_rows[0]
    assert row.rank_eligible is True
    assert row.data_status == (
        cre.DATA_STATUS_FULL_60_CELL
    )
    assert row.ranking_eligibility_basis == (
        cre.RANKING_ELIGIBILITY_BASIS_STRICT_FULL_60_CELL
    )
    # The strict completeness block carries the standard
    # complete status (no warning).
    assert row.data_completeness.get(
        "data_completeness_status",
    ) == "complete"
    assert row.data_completeness.get(
        "data_warning_symbol",
    ) in (None, "")


# ---------------------------------------------------------------------------
# 3. Partial-only artifact with effective metrics -> rank-eligible
# ---------------------------------------------------------------------------


def test_partial_with_effective_metrics_is_rank_eligible(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    _write_artifact(
        art_root,
        _partial_only_artifact_with_effective_metrics(),
    )
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    assert report.inspected_count == 1
    # One row, in the eligible list (not blocked).
    assert report.eligible_count == 1
    assert report.blocked_count == 0
    assert len(report.ranking_rows) == 1
    assert len(report.blocked_rows) == 0
    row = report.ranking_rows[0]
    # Required row contract.
    assert row.ticker == "SPY"
    assert row.rank_eligible is True
    assert row.data_status == (
        cre.DATA_STATUS_PARTIAL_MULTIWINDOW
    )
    assert row.ranking_eligibility_basis == (
        cre.RANKING_ELIGIBILITY_BASIS_PARTIAL_EFFECTIVE_MEMBERS
    )
    assert row.ranking_blocked_reason is None
    # Data-completeness surface carries the partial /
    # warning markers.
    completeness = row.data_completeness
    assert completeness["data_completeness_status"] == (
        "partial"
    )
    assert completeness["data_warning_symbol"] == "!"
    # Excluded / incomplete member detail surfaces TEF.
    assert (
        completeness["has_incomplete_build_members"]
        is True
    )
    assert "TEF" in completeness["incomplete_members"]


def test_partial_rank_eligible_row_does_not_claim_strict_completeness(
    tmp_path,
):
    """Pin: a partial-rankable row MUST NOT carry markers
    that imply strict 60/60 / full-grid completeness."""
    art_root = tmp_path / "research_artifacts"
    _write_artifact(
        art_root,
        _partial_only_artifact_with_effective_metrics(),
    )
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    row = report.ranking_rows[0]
    assert row.data_status != (
        cre.DATA_STATUS_FULL_60_CELL
    )
    # The fixture carries 30 effective cells -- the row's
    # k_cells_available should track the prepared count,
    # not the canonical 60.
    assert row.k_cells_available == 30
    # The completeness block must NOT report status=
    # "complete" on a partial-ranked row.
    assert row.data_completeness[
        "data_completeness_status"
    ] != "complete"


def test_partial_rank_eligible_sort_values_are_numeric(
    tmp_path,
):
    """Pin: the sortable fields (total_capture_pct,
    sharpe_ratio, trigger_days) carry real numbers so the
    website sort works for partial rows."""
    art_root = tmp_path / "research_artifacts"
    _write_artifact(
        art_root,
        _partial_only_artifact_with_effective_metrics(),
    )
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    row = report.ranking_rows[0]
    sort_values = row.row_sort_values
    # 30 cells × 1.5 capture = 45.0
    assert (
        sort_values[
            cre.SORT_VALUE_KEY_TOTAL_CAPTURE_PCT
        ]
        == 45.0
    )
    # avg Sharpe over 30 cells of 0.4 = 0.4
    assert (
        sort_values[
            cre.SORT_VALUE_KEY_SHARPE_RATIO
        ]
        == 0.4
    )
    # trigger_days sum = 30 × 5 = 150
    assert (
        sort_values[
            cre.SORT_VALUE_KEY_TRIGGER_DAYS
        ]
        == 150
    )


def test_partial_rank_eligible_keeps_one_row_per_ticker(
    tmp_path,
):
    art_root = tmp_path / "research_artifacts"
    _write_artifact(
        art_root,
        _partial_only_artifact_with_effective_metrics(),
    )
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    assert (
        len(report.ranking_rows)
        + len(report.blocked_rows)
    ) == 1


def test_partial_rank_eligible_row_json_serializes_basis(
    tmp_path,
):
    """``ranking_eligibility_basis`` is present in the
    JSON serialization."""
    art_root = tmp_path / "research_artifacts"
    _write_artifact(
        art_root,
        _partial_only_artifact_with_effective_metrics(),
    )
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    j = report.to_json_dict()
    assert (
        j["ranking_rows"][0]["ranking_eligibility_basis"]
        == cre.RANKING_ELIGIBILITY_BASIS_PARTIAL_EFFECTIVE_MEMBERS
    )


# ---------------------------------------------------------------------------
# 4. Partial-only artifact without effective metrics -> still blocked
# ---------------------------------------------------------------------------


def test_partial_without_effective_metrics_remains_blocked(
    tmp_path,
):
    """Phase 6I-47 behaviour preserved: partial block
    without ``effective_per_window_k_metrics`` (or with
    prepared_cell_count=0) classifies as blocked."""
    art_root = tmp_path / "research_artifacts"
    _write_artifact(
        art_root,
        _partial_only_artifact_without_effective_metrics(),
    )
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    assert report.eligible_count == 0
    assert report.blocked_count == 1
    row = report.blocked_rows[0]
    assert row.rank_eligible is False
    assert row.ranking_blocked_reason == (
        cre.RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY
    )
    assert row.data_status == (
        cre.DATA_STATUS_PARTIAL_MULTIWINDOW
    )
    assert row.ranking_eligibility_basis is None


def test_partial_with_zero_prepared_cells_remains_blocked(
    tmp_path,
):
    """Pin: prepared_cell_count=0 stays blocked even if
    effective_per_window_k_metrics is technically present
    but empty."""
    block = _good_partial_block_with_effective_metrics()
    block["effective_per_window_k_metrics"] = []
    block["prepared_cell_count"] = 0
    artifact = {
        "ticker": "SPY",
        "generated_at": "2026-05-15T00:00:00Z",
        "multiwindow_k_partial_payload_metadata": block,
    }
    art_root = tmp_path / "research_artifacts"
    _write_artifact(art_root, artifact)
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    assert report.eligible_count == 0
    assert report.blocked_count == 1


def test_partial_with_strict_key_smuggled_in_remains_blocked(
    tmp_path,
):
    """Defensive guard: even if a malformed partial block
    smuggles strict per_window_k_metrics inside, the
    ranking export refuses to promote it."""
    art_root = tmp_path / "research_artifacts"
    _write_artifact(
        art_root,
        _partial_only_artifact_with_strict_key_smuggle(),
    )
    report = cre.build_multiwindow_ranking_export(
        ["SPY"],
        artifact_root=art_root,
        cache_dir=None,
    )
    # The classifier sees strict per_window_k_metrics
    # present + the (still-present) partial block; it
    # routes through the strict-keys-present branch and
    # ends as incomplete_multiwindow (NOT partial-rankable).
    # Either way the row stays blocked (rank_eligible
    # False).
    assert report.eligible_count == 0
    assert report.blocked_count == 1


# ---------------------------------------------------------------------------
# 5. Phase 6I-47 partial-block writer-side validator still rejects strict keys
# ---------------------------------------------------------------------------


import multiwindow_k_confluence_patch_writer as pw  # noqa: E402


def test_writer_partial_consistency_still_rejects_strict_keys():
    """Pin: Phase 6I-47's _writer_partial_payload_is_consistent
    refuses a partial block that carries strict keys. The
    Phase 6I-48 effective_* fields are NOT strict keys."""
    bad_block = (
        _good_partial_block_with_effective_metrics()
    )
    bad_block["per_window_k_metrics"] = []  # forbidden

    class _Plan:
        partial_patch_ready = True
        partial_planned_payload = {
            pp.PARTIAL_PAYLOAD_METADATA_KEY: bad_block,
        }
        partial_planned_payload_keys = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_add = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_replace: tuple = ()
    assert (
        pw._writer_partial_payload_is_consistent(_Plan())
        is False
    )


def test_writer_partial_consistency_accepts_effective_metrics():
    """Pin: a partial block WITH effective_* fields but
    NO strict keys is accepted."""
    block = _good_partial_block_with_effective_metrics()
    assert "per_window_k_metrics" not in block
    assert "build_wide_window_alignment" not in block
    assert (
        "multiwindow_k_engine_payload_metadata"
        not in block
    )

    class _Plan:
        partial_patch_ready = True
        partial_planned_payload = {
            pp.PARTIAL_PAYLOAD_METADATA_KEY: block,
        }
        partial_planned_payload_keys = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_add = (
            pp.PARTIAL_PAYLOAD_METADATA_KEY,
        )
        partial_fields_to_replace: tuple = ()
    assert (
        pw._writer_partial_payload_is_consistent(_Plan())
        is True
    )


# ---------------------------------------------------------------------------
# 6. Static guards
# ---------------------------------------------------------------------------


def test_partial_rank_eligible_blocked_reasons_taxonomy_unchanged():
    """Phase 6I-48 does not add or remove blocked reasons --
    partial_multiwindow_only is still the right code when
    the partial block lacks effective metrics."""
    assert len(cre.ALL_RANKING_BLOCKED_REASONS) == 10
    assert (
        cre.RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY
        in cre.ALL_RANKING_BLOCKED_REASONS
    )


def test_data_status_partial_multiwindow_in_taxonomy():
    assert (
        cre.DATA_STATUS_PARTIAL_MULTIWINDOW
        in cre.ALL_DATA_STATUSES
    )
