"""Phase 6I-46 tests: TrafficFlow-compatible invalid-member
handling across the multi-window Confluence path.

Pins:

  1. ``invalid_members=None`` / empty preserves the existing
     strict complete-coverage contract byte-for-byte (the
     adapter / diagnostic / payload / planner / writer
     behaviour is unchanged when no exclusions are
     supplied).
  2. A SPY-like fixture (one K row encoding TEF as a member)
     with ``invalid_members={'TEF': {...}}`` produces:
       - ``original_members`` preserving TEF.
       - ``effective_members`` excluding TEF.
       - ``excluded_members`` carrying the structured
         reason + telemetry_reason + source_classification.
       - per-cell ``skipped_reason=
         'unprepared_due_to_excluded_members'`` for the
         affected (K, window) cells.
       - report-level ``data_completeness_status='partial'``
         when at least one cell still prepares; ``'blocked'``
         when no cells prepare.
       - ``data_warning_symbol='!'`` whenever status is
         partial or blocked.
       - Strict ``can_evaluate_full_60_cell_grid=False`` so
         the strict Phase 6I-20 complete-payload contract is
         NOT silently weakened.
  3. The payload builder mirrors the new fields onto its
     report. ``payload_ready`` stays False for partial
     payloads; ``partial_payload_available=True`` is set so
     a downstream consumer can render the warning.
  4. The patch planner emits the new
     ``ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE`` issue code +
     ``ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE`` next-action
     when the upstream payload reports partial / blocked.
     ``patch_ready`` stays False; ``planned_payload`` stays
     empty.
  5. The patch writer continues to refuse writes when the
     planner reports ``patch_ready=False`` (the partial
     surface NEVER causes a mutation).
  6. The ranking export's default member-completeness
     provider surfaces ``has_incomplete_build_members=True``
     when a Confluence artifact carries
     ``data_completeness_status='partial'`` +
     ``incomplete_member_detail``. The row preserves one
     row per ticker and carries the warning symbol; TEF is
     not silently dropped.
  7. TEF as ``invalid_or_delisted`` with
     ``provider_fetch_failed_zero_rows`` is the
     load-bearing fixture used end-to-end.

All tests are read-only. No writer is exercised with
``--write``. No production root is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import multiwindow_k_engine_core as core  # noqa: E402
import multiwindow_k_input_adapter as adapter  # noqa: E402
import multiwindow_k_input_adapter_diagnostic as diag  # noqa: E402
import multiwindow_k_engine_payload_builder as pb  # noqa: E402
import multiwindow_k_confluence_patch_planner as pp  # noqa: E402
import multiwindow_k_confluence_patch_writer as pw  # noqa: E402
import confluence_multiwindow_ranking_export as cre  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


_TEF_EXCLUSION_INPUT: dict[str, dict[str, Any]] = {
    "TEF": {
        "reason": "invalid_or_delisted",
        "telemetry_reason": (
            "provider_fetch_failed_zero_rows"
        ),
        "source_classification": (
            "phase_6i_43_invalid_or_delisted"
        ),
    },
}


class _FakeKRow:
    """Minimal stand-in for trafficflow_k_artifact_builder.
    KBuildRow.  Only K and members_str are read by the adapter."""

    def __init__(
        self,
        K: int,
        members_str: str,
        *,
        target_ticker: str = "SPY",
        run_id: str = "phase_6i_46_fake_run",
    ) -> None:
        self.K = K
        self.members_str = members_str
        self.target_ticker = target_ticker
        self.run_id = run_id


def _bars(window: str, n: int = 3) -> list[Any]:
    return [f"2026-01-{i+1:02d}_{window}" for i in range(n)]


def _make_lib(
    *,
    dates: list[Any],
    signals: Optional[list[str]] = None,
    close: Optional[list[float]] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"dates": list(dates)}
    if signals is not None:
        out["signals"] = list(signals)
    if close is not None:
        out["close"] = list(close)
    return out


def _target_lib(window: str, n: int = 3) -> dict[str, Any]:
    return _make_lib(
        dates=_bars(window, n),
        signals=["None"] * n,
        close=[100.0 + i for i in range(n)],
    )


def _member_lib(window: str, n: int = 3) -> dict[str, Any]:
    return _make_lib(
        dates=_bars(window, n),
        signals=["Buy"] * n,
    )


def _spy_tef_fixture():
    """SPY-like fixture: 12 ranked members including TEF.

    Mirrors the Phase 6I-45 structural-blocker shape:
    K=1..6 cells reference subsets that DO NOT include TEF
    (so under strict coverage they prepare cleanly);
    K=7..12 cells reference subsets that DO include TEF
    (so under strict coverage they skip with
    ``incomplete_member_coverage`` -- under Phase 6I-46
    invalid-member handling they instead get
    ``unprepared_due_to_excluded_members``).
    """
    # 11 non-TEF members (alphabetical for stable output).
    non_tef = [
        ("AROW", "D"), ("AWR", "D"), ("CLH", "D"),
        ("CP", "I"), ("EXPO", "D"), ("FCFS", "D"),
        ("GBCI", "D"), ("HCSG", "D"), ("JNJ", "I"),
        ("LLY", "I"), ("MO", "I"),
    ]
    tef = ("TEF", "I")
    rows: list[_FakeKRow] = []
    # K=1..6 -- top-K non-TEF members (TEF NOT in row).
    for K in range(1, 7):
        members = non_tef[:K]
        members_str = ", ".join(
            f"{t}[{p}]" if p else t for t, p in members
        )
        rows.append(_FakeKRow(K, members_str))
    # K=7..12 -- top-(K-1) non-TEF + TEF (TEF IS in row).
    for K in range(7, 13):
        members = non_tef[:K - 1] + [tef]
        members_str = ", ".join(
            f"{t}[{p}]" if p else t for t, p in members
        )
        rows.append(_FakeKRow(K, members_str))

    def discovery(target_ticker, *, stackbuilder_root=None):
        return Path("fake_run_dir")

    def leaderboard_loader(run_dir):
        return {"__sentinel__": "phase_6i_46_lb"}

    wanted_rows = list(rows)

    def k_rows_iter(
        leaderboard, *, target_ticker, run_id, expected_k,
    ):
        wanted = set(int(k) for k in expected_k)
        return [
            r for r in wanted_rows
            if int(r.K) in wanted
        ]

    # Library loader: target has every (window) entry;
    # every non-TEF member has every (window) entry; TEF
    # has NONE (mirroring the Phase 6I-44 untouched
    # invalid-cache state).
    target_libs = {
        w: _target_lib(w)
        for w in core.CANONICAL_WINDOWS
    }
    member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    for t, _p in non_tef:
        for w in core.CANONICAL_WINDOWS:
            member_libs[(t.upper(), w)] = _member_lib(w)

    def loader(
        ticker, interval, *, signal_library_dir=None,
    ):
        tk = (ticker or "").strip().upper()
        if tk == "SPY":
            return target_libs.get(interval)
        return member_libs.get((tk, interval))

    return discovery, leaderboard_loader, k_rows_iter, loader


def _run_adapter_with_invalid(
    invalid_members: Optional[Mapping[str, Any]] = None,
):
    (
        discovery,
        leaderboard_loader,
        k_rows_iter,
        loader,
    ) = _spy_tef_fixture()
    return adapter.prepare_multiwindow_k_inputs(
        "SPY",
        stackbuilder_root="fake_root",
        signal_library_dir="fake_sig_dir",
        K_values=core.CANONICAL_K_VALUES,
        windows=core.CANONICAL_WINDOWS,
        library_loader=loader,
        stackbuilder_run_discovery_callable=discovery,
        leaderboard_loader_callable=leaderboard_loader,
        k_rows_iter_callable=k_rows_iter,
        invalid_members=invalid_members,
    )


# ---------------------------------------------------------------------------
# 1. Pin: complete behaviour is byte-identical when invalid_members is absent
# ---------------------------------------------------------------------------


def test_invariant_no_exclusions_means_complete():
    """No ``invalid_members`` -> the new report fields take
    their default values; pre-Phase 6I-46 contract holds."""
    report = _run_adapter_with_invalid(None)
    assert report.data_completeness_status == "complete"
    assert report.data_warning_symbol == ""
    assert report.original_members_by_K == {}
    assert report.effective_members_by_K == {}
    assert report.excluded_members_by_K == {}
    assert report.incomplete_member_detail == ()
    # K=1..6 prepared, K=7..12 skipped via legacy
    # incomplete_member_coverage (because TEF has no
    # library and is NOT marked invalid here).
    assert report.prepared_cell_count == 30
    assert (
        report.can_evaluate_full_60_cell_grid is False
    )
    # And the per-cell ``excluded_members`` tuples are
    # empty everywhere.
    for s in report.per_cell_states:
        assert s.excluded_members == ()


def test_empty_invalid_members_mapping_is_same_as_absent():
    """An empty mapping is treated identically to None."""
    report = _run_adapter_with_invalid({})
    assert report.data_completeness_status == "complete"
    assert report.excluded_members_by_K == {}


# ---------------------------------------------------------------------------
# 2. SPY/TEF: structured exclusion + skipped reason + completeness status
# ---------------------------------------------------------------------------


def test_spy_tef_fixture_marks_excluded_rows_unprepared():
    """SPY/TEF: K=7..12 rows include TEF; under invalid-
    member handling they must surface as
    ``unprepared_due_to_excluded_members`` with the
    exclusion records carried per cell."""
    report = _run_adapter_with_invalid(_TEF_EXCLUSION_INPUT)
    # K=1..6 still prepare (TEF NOT in their member set).
    # K=7..12 are now skipped under the new reason.
    skipped_under_new_reason = [
        s for s in report.per_cell_states
        if s.skipped_reason == (
            "unprepared_due_to_excluded_members"
        )
    ]
    # 6 K rows × 5 windows = 30 cells.
    assert len(skipped_under_new_reason) == 30
    for s in skipped_under_new_reason:
        assert 7 <= s.K <= 12
        assert len(s.excluded_members) == 1
        rec = s.excluded_members[0]
        assert rec.ticker == "TEF"
        assert rec.reason == "invalid_or_delisted"
        assert rec.telemetry_reason == (
            "provider_fetch_failed_zero_rows"
        )
        assert rec.source_classification == (
            "phase_6i_43_invalid_or_delisted"
        )
        # members_missing surfaces the excluded tickers.
        assert s.members_missing == ("TEF",)


def test_spy_tef_fixture_preserves_original_members():
    """``original_members_by_K`` preserves the authored K-row
    member list verbatim (including TEF)."""
    report = _run_adapter_with_invalid(_TEF_EXCLUSION_INPUT)
    # Only K rows that had exclusions populate the maps.
    assert set(report.original_members_by_K.keys()) == set(
        range(7, 13),
    )
    for K in range(7, 13):
        original = report.original_members_by_K[K]
        # Authored K-row had K members and TEF was the last.
        assert len(original) == K
        member_tickers = [t for (t, _p) in original]
        assert "TEF" in member_tickers


def test_spy_tef_fixture_derives_effective_members():
    """``effective_members_by_K`` excludes TEF; the order
    of remaining members is preserved."""
    report = _run_adapter_with_invalid(_TEF_EXCLUSION_INPUT)
    for K in range(7, 13):
        original = report.original_members_by_K[K]
        effective = report.effective_members_by_K[K]
        # Effective set is original minus TEF only.
        assert len(effective) == len(original) - 1
        for (t, _p) in effective:
            assert t != "TEF"


def test_spy_tef_fixture_carries_structured_exclusions():
    """``excluded_members_by_K`` carries ``ExclusionRecord``
    tuples with the structured reason fields."""
    report = _run_adapter_with_invalid(_TEF_EXCLUSION_INPUT)
    for K in range(7, 13):
        records = report.excluded_members_by_K[K]
        assert len(records) == 1
        rec = records[0]
        assert isinstance(rec, adapter.ExclusionRecord)
        assert rec.ticker == "TEF"
        assert rec.reason == "invalid_or_delisted"
        assert rec.telemetry_reason == (
            "provider_fetch_failed_zero_rows"
        )


def test_spy_tef_fixture_reports_partial_completeness_status():
    """``data_completeness_status='partial'`` when at least
    one cell still prepares."""
    report = _run_adapter_with_invalid(_TEF_EXCLUSION_INPUT)
    assert (
        report.data_completeness_status == "partial"
    )
    assert report.data_warning_symbol == "!"
    # K=1..6 cells (30 of 60) still prepared.
    assert report.prepared_cell_count == 30


def test_spy_tef_fixture_strict_60_cell_gate_unchanged():
    """The strict ``can_evaluate_full_60_cell_grid=True``
    verdict requires ALL 60 cells prepared. With 30 cells
    skipped under the new reason, the strict gate must
    refuse."""
    report = _run_adapter_with_invalid(_TEF_EXCLUSION_INPUT)
    assert (
        report.can_evaluate_full_60_cell_grid is False
    )


def test_spy_tef_fixture_emits_excluded_invalid_member_issue():
    """The report-level ``issue_codes`` carries the new
    Phase 6I-46 aggregate code."""
    report = _run_adapter_with_invalid(_TEF_EXCLUSION_INPUT)
    assert (
        "excluded_invalid_member" in report.issue_codes
    )


def test_blocked_status_when_no_cells_prepare():
    """If every K row includes TEF, every cell is
    ``unprepared_due_to_excluded_members`` and the report
    status is ``blocked`` (not partial)."""
    # Build a fixture where TEF is in EVERY K row.
    rows = [
        _FakeKRow(
            K=K,
            members_str=", ".join(
                ["TEF[I]"] + [
                    f"NONTEF{i}[D]"
                    for i in range(K - 1)
                ]
            ),
        )
        for K in core.CANONICAL_K_VALUES
    ]

    def discovery(target_ticker, *, stackbuilder_root=None):
        return Path("blocked_fixture_run")

    def leaderboard_loader(run_dir):
        return {"__sentinel__": "blocked_lb"}

    def k_rows_iter(
        leaderboard, *, target_ticker, run_id, expected_k,
    ):
        wanted = set(int(k) for k in expected_k)
        return [r for r in rows if int(r.K) in wanted]

    target_libs = {
        w: _target_lib(w)
        for w in core.CANONICAL_WINDOWS
    }

    def loader(
        ticker, interval, *, signal_library_dir=None,
    ):
        tk = (ticker or "").strip().upper()
        if tk == "SPY":
            return target_libs.get(interval)
        return None
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        stackbuilder_root="fake_root",
        signal_library_dir="fake_sig_dir",
        K_values=core.CANONICAL_K_VALUES,
        windows=core.CANONICAL_WINDOWS,
        library_loader=loader,
        stackbuilder_run_discovery_callable=discovery,
        leaderboard_loader_callable=leaderboard_loader,
        k_rows_iter_callable=k_rows_iter,
        invalid_members=_TEF_EXCLUSION_INPUT,
    )
    assert report.data_completeness_status == "blocked"
    assert report.data_warning_symbol == "!"
    assert report.prepared_cell_count == 0
    # All 60 cells must be unprepared_due_to_excluded_members.
    skipped = [
        s for s in report.per_cell_states
        if s.skipped_reason == (
            "unprepared_due_to_excluded_members"
        )
    ]
    assert len(skipped) == 60


# ---------------------------------------------------------------------------
# 3. Diagnostic serializes the new fields
# ---------------------------------------------------------------------------


def test_diagnostic_surfaces_partial_completeness_fields():
    """The diagnostic JSON carries the new schema fields."""
    (
        discovery,
        leaderboard_loader,
        k_rows_iter,
        loader,
    ) = _spy_tef_fixture()

    def adapter_callable(target_ticker, **kwargs):
        kwargs.setdefault(
            "library_loader", loader,
        )
        kwargs.setdefault(
            "stackbuilder_run_discovery_callable",
            discovery,
        )
        kwargs.setdefault(
            "leaderboard_loader_callable",
            leaderboard_loader,
        )
        kwargs.setdefault(
            "k_rows_iter_callable", k_rows_iter,
        )
        return adapter.prepare_multiwindow_k_inputs(
            target_ticker, **kwargs,
        )

    result = diag.run_adapter_diagnostic(
        "SPY",
        stackbuilder_root="fake_root",
        signal_library_dir="fake_sig_dir",
        adapter_callable=adapter_callable,
        invalid_members=_TEF_EXCLUSION_INPUT,
    )
    assert (
        result["data_completeness_status"] == "partial"
    )
    assert result["data_warning_symbol"] == "!"
    # Map keys are stringified ints for JSON friendliness.
    assert set(
        result["excluded_members_by_K"].keys()
    ) == {"7", "8", "9", "10", "11", "12"}
    for K_str, exclusions in (
        result["excluded_members_by_K"].items()
    ):
        assert exclusions[0]["ticker"] == "TEF"
        assert exclusions[0]["reason"] == (
            "invalid_or_delisted"
        )
    # incomplete_member_detail carries one record per K row
    # that referenced TEF (6 K rows × 1 invalid member = 6).
    assert len(result["incomplete_member_detail"]) == 6
    # Per-cell ``excluded_members`` is populated on
    # skipped-under-new-reason cells.
    cells_with_exclusion = [
        c for c in result["per_cell_diagnostics"]
        if c.get("excluded_members")
    ]
    assert len(cells_with_exclusion) == 30


def test_diagnostic_no_invalid_members_keeps_default_shape():
    """Calling the diagnostic without ``invalid_members``
    keeps the new fields at their default (empty / 'complete'
    / no warning) values."""
    (
        discovery,
        leaderboard_loader,
        k_rows_iter,
        loader,
    ) = _spy_tef_fixture()

    def adapter_callable(target_ticker, **kwargs):
        kwargs.setdefault(
            "library_loader", loader,
        )
        kwargs.setdefault(
            "stackbuilder_run_discovery_callable",
            discovery,
        )
        kwargs.setdefault(
            "leaderboard_loader_callable",
            leaderboard_loader,
        )
        kwargs.setdefault(
            "k_rows_iter_callable", k_rows_iter,
        )
        return adapter.prepare_multiwindow_k_inputs(
            target_ticker, **kwargs,
        )

    result = diag.run_adapter_diagnostic(
        "SPY",
        stackbuilder_root="fake_root",
        signal_library_dir="fake_sig_dir",
        adapter_callable=adapter_callable,
    )
    assert (
        result["data_completeness_status"] == "complete"
    )
    assert result["data_warning_symbol"] == ""
    assert result["excluded_members_by_K"] == {}
    assert result["incomplete_member_detail"] == []


# ---------------------------------------------------------------------------
# 4. Payload builder threads completeness fields through
# ---------------------------------------------------------------------------


def _build_partial_adapter_report():
    """Run the adapter through ``_spy_tef_fixture`` and
    return the result so payload builder tests can inject
    it via a fake adapter_callable."""
    return _run_adapter_with_invalid(_TEF_EXCLUSION_INPUT)


def test_payload_builder_partial_status_keeps_payload_ready_false():
    """Payload builder reports partial status without
    flipping ``payload_ready=True``."""
    cached_report = _build_partial_adapter_report()

    def fake_adapter(target_ticker, **kwargs):
        return cached_report

    report = pb.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=fake_adapter,
        invalid_members=_TEF_EXCLUSION_INPUT,
    )
    # Strict gate refuses partial.
    assert report.payload_ready is False
    assert report.data_completeness_status == "partial"
    assert report.data_warning_symbol == "!"
    # 30 prepared cells > 0, so partial-payload is
    # AVAILABLE (the new flag) even though the strict
    # payload is not ready.
    assert report.partial_payload_available is True


def test_payload_builder_blocked_status_no_partial_available():
    """When status is ``blocked`` (zero prepared cells) the
    partial-payload-available flag stays False."""
    # Build a "blocked" adapter result by stubbing
    # data_completeness_status='blocked' and prepared=0.
    class _StubReport:
        target_ticker = "SPY"
        selected_run_dir = None
        selected_run_id = None
        attempted_cell_count = 60
        prepared_cell_count = 0
        missing_cell_count = 60
        can_evaluate_full_60_cell_grid = False
        skipped_cells: tuple = ()
        issue_codes: tuple = (
            "excluded_invalid_member",
        )
        per_cell_inputs: dict = {}
        per_cell_states: tuple = ()
        missing_libraries_by_ticker_window: dict = {}
        unparseable_member_strings: tuple = ()
        original_members_by_K: dict = {}
        effective_members_by_K: dict = {}
        excluded_members_by_K: dict = {}
        incomplete_member_detail: tuple = ()
        data_completeness_status = "blocked"
        data_warning_symbol = "!"

    def fake_adapter(target_ticker, **kwargs):
        return _StubReport()

    report = pb.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=fake_adapter,
        invalid_members=_TEF_EXCLUSION_INPUT,
    )
    assert report.payload_ready is False
    assert report.data_completeness_status == "blocked"
    assert report.partial_payload_available is False


def test_payload_builder_complete_status_unchanged():
    """When status is ``complete`` (default) the new fields
    take their default values."""
    # Stub a "complete" adapter report so we test the
    # field plumbing without needing a real engine run.
    class _StubReport:
        target_ticker = "SPY"
        selected_run_dir = None
        selected_run_id = None
        attempted_cell_count = 60
        prepared_cell_count = 60
        missing_cell_count = 0
        can_evaluate_full_60_cell_grid = True
        skipped_cells: tuple = ()
        issue_codes: tuple = ()
        per_cell_inputs: dict = {(1, "1d"): {"_x": True}}
        per_cell_states: tuple = ()
        missing_libraries_by_ticker_window: dict = {}
        unparseable_member_strings: tuple = ()
        original_members_by_K: dict = {}
        effective_members_by_K: dict = {}
        excluded_members_by_K: dict = {}
        incomplete_member_detail: tuple = ()
        data_completeness_status = "complete"
        data_warning_symbol = ""

    def fake_adapter(target_ticker, **kwargs):
        return _StubReport()
    # Stub the core grid: it never gets called when
    # _core_cells_cover_full_canonical_60 is False, but we
    # need it to short-circuit cleanly.

    def fake_core(*, target_ticker, per_cell_inputs):
        return []  # No cells -> ISSUE_NO_CELLS_EVALUATED.

    report = pb.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=fake_adapter,
        core_grid_callable=fake_core,
    )
    assert report.data_completeness_status == "complete"
    assert report.data_warning_symbol == ""
    assert report.partial_payload_available is False


# ---------------------------------------------------------------------------
# 5. Patch planner refuses partial payloads
# ---------------------------------------------------------------------------


def test_patch_planner_emits_partial_not_promotable_issue():
    """Planner emits the new issue code + next-action when
    upstream reports partial."""
    # Stub a payload report with data_completeness_status=
    # 'partial' so we test the planner logic directly.

    class _PartialPayloadStub:
        target_ticker = "SPY"
        payload_ready = False
        K_values: tuple = (1, 2, 3)
        windows: tuple = ("1d",)
        cell_count = 3
        per_window_k_metrics: list = []
        build_wide_window_alignment: dict = {}
        adapter_summary = None
        issue_codes: tuple = ("adapter_not_ready",)
        remaining_limitations: tuple = ()
        data_completeness_status = "partial"
        data_warning_symbol = "!"
        incomplete_member_detail: tuple = (
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
        )
        partial_payload_available = True

    def fake_payload_builder(target_ticker, **kwargs):
        return _PartialPayloadStub()

    def fake_artifact_locator(
        target_ticker, *, artifact_root=None,
    ):
        return None  # artifact missing is OK here

    plan = pp.plan_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root="fake_artifact_root",
        payload_builder_callable=fake_payload_builder,
        artifact_locator_callable=fake_artifact_locator,
    )
    assert plan.patch_ready is False
    assert (
        "partial_payload_not_promotable" in plan.issue_codes
    )
    assert plan.recommended_next_action == (
        "partial_payload_not_promotable"
    )
    # Planned payload stays empty.
    assert plan.planned_payload == {}
    assert plan.fields_to_add == ()
    assert plan.fields_to_replace == ()
    # Completeness fields mirrored onto the plan.
    assert plan.data_completeness_status == "partial"
    assert plan.data_warning_symbol == "!"
    assert (
        len(plan.incomplete_member_detail) == 1
        and plan.incomplete_member_detail[0]["ticker"]
        == "TEF"
    )
    assert plan.partial_payload_available is True


def test_patch_planner_complete_path_unchanged_when_no_exclusions():
    """When upstream reports complete status, planner
    behaviour matches the pre-Phase 6I-46 contract."""

    class _CompletePayloadStub:
        target_ticker = "AAA"
        payload_ready = False  # the artifact won't exist
        K_values: tuple = (1,)
        windows: tuple = ("1d",)
        cell_count = 1
        per_window_k_metrics: list = []
        build_wide_window_alignment: dict = {}
        adapter_summary = None
        issue_codes: tuple = ("adapter_not_ready",)
        remaining_limitations: tuple = ()
        data_completeness_status = "complete"
        data_warning_symbol = ""
        incomplete_member_detail: tuple = ()
        partial_payload_available = False

    def fake_payload_builder(target_ticker, **kwargs):
        return _CompletePayloadStub()

    def fake_artifact_locator(
        target_ticker, *, artifact_root=None,
    ):
        return None

    plan = pp.plan_multiwindow_k_confluence_patch(
        "AAA",
        artifact_root="fake",
        payload_builder_callable=fake_payload_builder,
        artifact_locator_callable=fake_artifact_locator,
    )
    # Complete status routes through the legacy
    # build_payload_first action (because payload_ready
    # is False AND status is complete).
    assert plan.recommended_next_action == (
        "build_payload_first"
    )
    assert (
        "partial_payload_not_promotable"
        not in plan.issue_codes
    )
    # Plan-level Phase 6I-46 fields take their defaults.
    assert plan.data_completeness_status == "complete"
    assert plan.partial_payload_available is False


# ---------------------------------------------------------------------------
# 6. Patch writer continues to refuse writes for partial plans
# ---------------------------------------------------------------------------


def test_patch_writer_dry_run_refuses_when_plan_not_ready():
    """The writer continues to refuse writes when planner
    says patch_ready=False (covers the partial-not-
    promotable cascade)."""

    class _PartialPlanStub:
        target_ticker = "SPY"
        current_as_of_date = "2026-05-15"
        artifact_path = None
        artifact_exists = False
        payload_ready = False
        patch_ready = False
        fields_to_add: tuple = ()
        fields_to_replace: tuple = ()
        existing_field_summary: dict = {}
        payload_summary: dict = {}
        planned_payload_keys: tuple = ()
        planned_payload: dict = {}
        issue_codes: tuple = (
            "payload_not_ready",
            "partial_payload_not_promotable",
        )
        recommended_next_action = (
            "partial_payload_not_promotable"
        )
        remaining_limitations: tuple = ()

    def fake_planner(target_ticker, **kwargs):
        return _PartialPlanStub()

    result = pw.apply_multiwindow_k_confluence_patch(
        "SPY",
        artifact_root="fake",
        patch_planner_callable=fake_planner,
    )
    assert result.wrote_artifact is False
    assert result.write_requested is False
    assert result.planner_patch_ready is False


# ---------------------------------------------------------------------------
# 7. Ranking export surfaces partial state from an artifact carrying the new fields
# ---------------------------------------------------------------------------


def test_ranking_export_provider_reads_partial_from_artifact():
    """The default member-completeness provider reads the
    new fields from the artifact and surfaces them as
    incomplete-build-members so the row picks up the
    warning."""
    artifact = {
        "multiwindow_k_engine_payload_metadata": {
            "data_completeness_status": "partial",
            "data_warning_symbol": "!",
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
        },
    }
    block = (
        cre._default_member_completeness_provider(
            "SPY", artifact=artifact,
        )
    )
    assert block["has_incomplete_build_members"] is True
    assert block["incomplete_member_count"] == 1
    assert block["incomplete_members"] == ["TEF"]
    # The provider preserves the structured reason via
    # the ``reason:telemetry_reason`` join.
    assert (
        "invalid_or_delisted"
        in block["incomplete_member_reasons"]["TEF"]
    )
    assert (
        "provider_fetch_failed_zero_rows"
        in block["incomplete_member_reasons"]["TEF"]
    )


def test_ranking_export_provider_keeps_empty_for_artifact_without_new_fields():
    """An on-disk artifact that does NOT carry the new
    fields (the current production state) keeps the
    provider on its honest empty shape."""
    artifact = {
        "multiwindow_k_engine_payload_metadata": {
            "schema_version": "phase_6i_20",
        },
    }
    block = (
        cre._default_member_completeness_provider(
            "AAA", artifact=artifact,
        )
    )
    assert block["has_incomplete_build_members"] is False
    assert block["incomplete_member_count"] == 0
    assert block["incomplete_members"] == []


def test_ranking_export_provider_handles_top_level_fields():
    """Fields may also live at the top of the artifact (the
    in-memory payload report shape); the provider picks
    them up either way."""
    artifact = {
        "data_completeness_status": "partial",
        "incomplete_member_detail": [
            {
                "ticker": "TEF",
                "reason": "invalid_or_delisted",
            },
        ],
    }
    block = (
        cre._default_member_completeness_provider(
            "SPY", artifact=artifact,
        )
    )
    assert block["has_incomplete_build_members"] is True
    assert "TEF" in block["incomplete_members"]


# ---------------------------------------------------------------------------
# 8. Partial payload cannot be mistaken for strict complete coverage
# ---------------------------------------------------------------------------


def test_partial_payload_never_mistaken_for_complete():
    """Pin: under any partial scenario, ``payload_ready`` is
    False AND ``can_evaluate_full_60_cell_grid`` is False."""
    cached_report = _build_partial_adapter_report()

    def fake_adapter(target_ticker, **kwargs):
        return cached_report

    report = pb.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=fake_adapter,
        invalid_members=_TEF_EXCLUSION_INPUT,
    )
    assert report.payload_ready is False
    # JSON shape must NOT silently promote partial to
    # complete.
    j = report.to_json_dict()
    assert j["payload_ready"] is False
    assert j["data_completeness_status"] == "partial"
    assert j["data_warning_symbol"] == "!"
    assert j["partial_payload_available"] is True


# ---------------------------------------------------------------------------
# 9. Static / forbidden guards still pass
# ---------------------------------------------------------------------------


def test_adapter_invalid_member_constants_are_stable():
    """The new constants live on the adapter module and
    surface in ``ALL_*`` aggregations."""
    assert (
        adapter.REASON_UNPREPARED_DUE_TO_EXCLUDED_MEMBERS
        in adapter.ALL_SKIPPED_REASON_CODES
    )
    assert (
        adapter.ISSUE_EXCLUDED_INVALID_MEMBER
        in adapter.ALL_ISSUE_CODES
    )
    assert set(
        adapter.ALL_DATA_COMPLETENESS_STATUSES
    ) == {"complete", "partial", "blocked"}
    assert (
        adapter.DATA_WARNING_SYMBOL_INCOMPLETE == "!"
    )
    assert (
        adapter.DATA_WARNING_SYMBOL_NONE == ""
    )


def test_planner_partial_not_promotable_action_is_listed():
    """The new planner action / issue codes are in the
    public aggregations."""
    assert (
        pp.ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE
        in pp.ALL_ISSUE_CODES
    )
    assert (
        pp.ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE
        in pp.ALL_ACTIONS
    )
