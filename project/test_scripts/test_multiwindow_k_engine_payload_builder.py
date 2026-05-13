"""Phase 6I-23 tests for multiwindow_k_engine_payload_builder.

Pins the in-memory payload builder's contract:

  - No forbidden top-level imports.
  - No projection logic anywhere in the source.
  - Adapter-not-ready short-circuits the builder: no core
    call, no fabricated payload, payload_ready=False.
  - Full happy-path: adapter says ready -> builder calls
    core -> builds 60 per_window_k_metrics + 5 entries on
    build_wide_window_alignment -> Phase 6I-20 gap audit's
    validators accept both shapes.
  - Strict member coverage propagates: a partial-member
    adapter report (60 structural cells but
    can_evaluate_full_60_cell_grid=False) NEVER yields
    payload_ready=True.
  - JSON round-trip.
  - CLI rc=0 / rc=2 / rc=3 / no SystemExit leak.
"""
from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import multiwindow_k_engine_core as core  # noqa: E402
import multiwindow_k_engine_gap_audit as gap_audit  # noqa: E402
import multiwindow_k_engine_payload_builder as builder  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeAdapterReport:
    """Duck-typed stand-in for the Phase 6I-22 adapter
    report. Only the attributes the builder reads are
    required."""

    selected_run_dir: Optional[str] = None
    selected_run_id: Optional[str] = None
    attempted_cell_count: int = 60
    prepared_cell_count: int = 60
    missing_cell_count: int = 0
    can_evaluate_full_60_cell_grid: bool = True
    per_cell_inputs: dict[
        tuple[int, str], dict[str, Any],
    ] = field(default_factory=dict)
    skipped_cells: tuple[
        tuple[int, str, str], ...
    ] = ()
    issue_codes: tuple[str, ...] = ()


def _simple_dates(n: int) -> list[str]:
    return [f"2026-01-{i+1:02d}" for i in range(n)]


def _full_canonical_per_cell_inputs() -> dict[
    tuple[int, str], dict[str, Any],
]:
    """Build a real full-canonical per-cell input map (12 K
    rows * 5 canonical windows = 60 cells) suitable for
    feeding Phase 6I-21's evaluate_k_window_grid. Each
    cell has its own K-sized member set so the cells from
    the core round-trip carry member_count == K."""
    n = 3
    closes = [100.0, 105.0, 110.25]
    dates = _simple_dates(n)
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for window in core.CANONICAL_WINDOWS:
        for k in core.CANONICAL_K_VALUES:
            members = {
                f"M{i}": ["Buy"] * n for i in range(k)
            }
            out[(k, window)] = {
                "dates": list(dates),
                "target_close": list(closes),
                "member_signal_columns": members,
            }
    return out


def _fake_adapter_returning(report: _FakeAdapterReport):
    def fn(target_ticker, **kwargs):
        # Pin: builder must NEVER pass
        # allow_partial_members to the adapter.
        assert "allow_partial_members" not in kwargs, (
            "Phase 6I-23 builder must NEVER forward "
            "allow_partial_members to the adapter"
        )
        return report
    return fn


# ---------------------------------------------------------------------------
# 1. Forbidden imports + no-projection guards
# ---------------------------------------------------------------------------


def test_builder_module_has_no_forbidden_imports():
    """The builder must not import any writer / refresher /
    pipeline runner / live engine / yfinance / dash /
    subprocess at top level."""
    src = Path(builder.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_first = {
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_executor",
        "yfinance",
        "dash",
        "spymaster",
        "trafficflow",
        "stackbuilder",
        "onepass",
        "impactsearch",
        "confluence",
        "cross_ticker_confluence",
        "daily_signal_board",
        "subprocess",
    }
    forbidden_exact = {
        "trafficflow_multitimeframe_bridge",
        "trafficflow_k_artifact_builder",
        "multiwindow_k_engine_gap_audit",
        "confluence_decision_brief",
    }
    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad_first = [
        m for m in found
        if m.split(".")[0] in forbidden_first
    ]
    bad_exact = [m for m in found if m in forbidden_exact]
    assert not bad_first, (
        f"forbidden first-segment import: {bad_first!r}"
    )
    assert not bad_exact, (
        f"forbidden exact-module import: {bad_exact!r}"
    )


def test_builder_makes_no_projection_calls():
    src = Path(builder.__file__).read_text(encoding="utf-8")
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
                f"builder calls forbidden {name!r}() -- "
                "projection logic belongs to the Phase "
                "6D-2 bridge, not this builder"
            )


def test_builder_module_has_no_raw_pickle_load():
    src = Path(builder.__file__).read_text(encoding="utf-8")
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
                        "builder calls pickle.load() "
                        f"at line {node.lineno}; route "
                        "through the central provenance "
                        "loader instead"
                    )


# ---------------------------------------------------------------------------
# 2. Adapter-not-ready short-circuits the builder
# ---------------------------------------------------------------------------


def test_adapter_not_ready_short_circuits():
    """When the adapter reports
    can_evaluate_full_60_cell_grid=False, the builder must:

      - return payload_ready=False;
      - NOT call the core (the spy core records 0 calls);
      - emit ISSUE_ADAPTER_NOT_READY;
      - keep per_window_k_metrics empty;
      - keep build_wide_window_alignment empty;
      - embed the adapter's issue codes in adapter_summary.
    """
    not_ready = _FakeAdapterReport(
        can_evaluate_full_60_cell_grid=False,
        prepared_cell_count=42,
        missing_cell_count=18,
        skipped_cells=(
            (12, "1y", "missing_target_library"),
        ),
        issue_codes=("missing_target_library",),
    )
    core_call_count: list[int] = []

    def spy_core(**kwargs):
        core_call_count.append(1)
        return ()

    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(not_ready),
        core_grid_callable=spy_core,
    )
    assert report.payload_ready is False
    assert report.per_window_k_metrics == []
    assert report.build_wide_window_alignment == {}
    assert report.cell_count == 0
    assert (
        builder.ISSUE_ADAPTER_NOT_READY in report.issue_codes
    )
    # Critical: the core grid must NOT be invoked when the
    # adapter is not ready.
    assert core_call_count == []
    assert (
        report.adapter_summary
        .can_evaluate_full_60_cell_grid
        is False
    )
    assert (
        "missing_target_library"
        in report.adapter_summary.adapter_issue_codes
    )


def test_partial_member_adapter_report_keeps_payload_not_ready():
    """Codex amendment invariant carried forward: a fake
    adapter report that simulates partial-mode -- 60
    structural cells exist in per_cell_inputs but
    can_evaluate_full_60_cell_grid=False -- must STILL
    yield payload_ready=False. The builder's gate is the
    boolean, not the structural count."""
    partial = _FakeAdapterReport(
        prepared_cell_count=60,  # structurally complete
        missing_cell_count=0,
        can_evaluate_full_60_cell_grid=False,  # but flagged
        per_cell_inputs=_full_canonical_per_cell_inputs(),
        issue_codes=("incomplete_member_coverage",),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(partial),
    )
    assert report.payload_ready is False
    assert report.per_window_k_metrics == []
    assert (
        builder.ISSUE_ADAPTER_NOT_READY in report.issue_codes
    )


# ---------------------------------------------------------------------------
# 3. Full happy path
# ---------------------------------------------------------------------------


def test_full_canonical_inputs_emit_60_per_window_k_metrics():
    """Adapter says ready + 60 per_cell_inputs -> builder
    calls Phase 6I-21 core -> emits 60 per_window_k_metrics
    + 5 build_wide_window_alignment entries."""
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
    )
    assert report.payload_ready is True
    assert report.cell_count == 60
    assert len(report.per_window_k_metrics) == 60
    observed = {
        (entry["K"], entry["window"])
        for entry in report.per_window_k_metrics
    }
    expected = {
        (k, w)
        for k in core.CANONICAL_K_VALUES
        for w in core.CANONICAL_WINDOWS
    }
    assert observed == expected
    # build_wide_window_alignment has one entry per
    # canonical window, no duplicates, no missing windows.
    assert (
        set(report.build_wide_window_alignment.keys())
        == set(core.CANONICAL_WINDOWS)
    )


# ---------------------------------------------------------------------------
# 4. Phase 6I-20 contract compatibility
# ---------------------------------------------------------------------------


def test_payload_satisfies_phase_6i20_per_window_k_metrics_validator():
    """Cross-module integration assertion: the builder's
    ``per_window_k_metrics`` output is accepted by the
    Phase 6I-20 gap audit's
    ``_per_window_k_metrics_are_valid`` validator."""
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
    )
    assert report.payload_ready is True
    assert gap_audit._per_window_k_metrics_are_valid(
        report.per_window_k_metrics,
    )


def test_payload_satisfies_phase_6i20_build_wide_alignment_validator():
    """Cross-module integration assertion: the builder's
    ``build_wide_window_alignment`` output is accepted by
    the Phase 6I-20 gap audit's
    ``_build_wide_alignment_is_valid`` validator (one
    entry per canonical window, each entry carrying the
    required bool / int fields)."""
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
    )
    assert report.payload_ready is True
    assert gap_audit._build_wide_alignment_is_valid(
        report.build_wide_window_alignment,
    )


def test_build_wide_alignment_has_exactly_one_entry_per_canonical_window():
    """The build_wide_window_alignment mapping must carry
    EXACTLY one entry per canonical window -- no missing,
    no duplicate, no extras outside the canonical set."""
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
    )
    keys = list(report.build_wide_window_alignment.keys())
    assert len(keys) == 5
    assert sorted(keys) == sorted(core.CANONICAL_WINDOWS)
    # Required field types per Phase 6I-20 validator.
    for window in core.CANONICAL_WINDOWS:
        entry = report.build_wide_window_alignment[window]
        assert isinstance(
            entry["all_members_firing"], bool,
        )
        assert isinstance(
            entry["firing_member_count"], int,
        )
        assert isinstance(
            entry["total_member_count"], int,
        )


def test_build_wide_alignment_all_members_firing_reflects_signal_counts():
    """In the full-canonical happy-path fixture every K row
    is all-Buy with K=1 threshold, so the latest combined
    signal at every (K, window) cell is "Buy" -- every
    window must report all_members_firing=True and
    firing_member_count == total_member_count == 12."""
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
    )
    for window in core.CANONICAL_WINDOWS:
        entry = report.build_wide_window_alignment[window]
        assert entry["total_member_count"] == 12
        assert entry["firing_member_count"] == 12
        assert entry["all_members_firing"] is True


def test_build_wide_alignment_records_none_signals_correctly():
    """A K row whose combined signal is None at the latest
    bar must NOT count toward firing_member_count. Built
    via a spy core callable that emits per-cell signals
    of our choosing."""
    ready = _FakeAdapterReport(
        per_cell_inputs={
            (1, "1d"): {
                "dates": ["d0", "d1"],
                "target_close": [100.0, 101.0],
                "member_signal_columns": {"A": ["None", "None"]},
            },
        },
    )

    def spy_core(*, target_ticker, per_cell_inputs):
        # Return a single PerWindowKCell whose latest
        # combined signal is None.
        return (
            core.PerWindowKCell(
                K=1, window="1d",
                total_capture_pct=0.0,
                avg_daily_capture_pct=0.0,
                sharpe_ratio=0.0,
                trigger_days=0,
                wins=0, losses=0,
                latest_combined_signal="None",
                latest_buy_count=0,
                latest_short_count=0,
                latest_none_count=1,
                latest_missing_count=0,
                member_count=1,
            ),
        )

    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        K_values=(1,),
        windows=("1d",),
        adapter_callable=_fake_adapter_returning(ready),
        core_grid_callable=spy_core,
    )
    entry_1d = report.build_wide_window_alignment["1d"]
    assert entry_1d["total_member_count"] == 1
    assert entry_1d["firing_member_count"] == 0
    assert entry_1d["all_members_firing"] is False


# ---------------------------------------------------------------------------
# 5. Core-grid failure paths
# ---------------------------------------------------------------------------


def test_core_grid_exception_yields_payload_not_ready():
    """If the core grid call raises, the builder catches
    it, returns payload_ready=False, and emits
    ISSUE_CORE_GRID_FAILED. The adapter summary still
    reflects the (successful) adapter state."""
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )

    def boom_core(**kwargs):
        raise RuntimeError("synthetic core failure")

    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
        core_grid_callable=boom_core,
    )
    assert report.payload_ready is False
    assert (
        builder.ISSUE_CORE_GRID_FAILED in report.issue_codes
    )
    assert report.per_window_k_metrics == []
    assert report.build_wide_window_alignment == {}


def test_core_grid_empty_result_yields_payload_not_ready():
    """If the core grid returns an empty tuple (logically
    impossible on real data but defensible against
    fakes), the builder must NOT mark payload_ready=True
    and must emit ISSUE_NO_CELLS_EVALUATED."""
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )

    def empty_core(**kwargs):
        return ()

    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
        core_grid_callable=empty_core,
    )
    assert report.payload_ready is False
    assert (
        builder.ISSUE_NO_CELLS_EVALUATED
        in report.issue_codes
    )


# ---------------------------------------------------------------------------
# 6. Builder does NOT forward allow_partial_members
# ---------------------------------------------------------------------------


def test_builder_never_forwards_allow_partial_members():
    """Pinned by the fake adapter's assert. If the builder
    ever forwarded allow_partial_members the assert
    inside _fake_adapter_returning would fire."""
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
    )
    assert report.payload_ready is True  # smoke pin


# ---------------------------------------------------------------------------
# 7. JSON serialization
# ---------------------------------------------------------------------------


def test_to_json_dict_round_trips():
    ready = _FakeAdapterReport(
        per_cell_inputs=_full_canonical_per_cell_inputs(),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(ready),
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored["payload_ready"] is True
    assert restored["target_ticker"] == "SPY"
    assert restored["cell_count"] == 60
    assert len(restored["per_window_k_metrics"]) == 60
    assert (
        set(restored["build_wide_window_alignment"].keys())
        == set(core.CANONICAL_WINDOWS)
    )
    # adapter_summary is included even on ready path.
    assert restored["adapter_summary"] is not None
    assert (
        restored["adapter_summary"][
            "can_evaluate_full_60_cell_grid"
        ]
        is True
    )


def test_to_json_dict_round_trips_on_not_ready_path():
    not_ready = _FakeAdapterReport(
        can_evaluate_full_60_cell_grid=False,
        prepared_cell_count=0,
        missing_cell_count=60,
        issue_codes=("no_stackbuilder_run_for_target",),
    )
    report = builder.build_multiwindow_k_engine_payload(
        "SPY",
        adapter_callable=_fake_adapter_returning(not_ready),
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored["payload_ready"] is False
    assert restored["per_window_k_metrics"] == []
    assert restored["build_wide_window_alignment"] == {}


# ---------------------------------------------------------------------------
# 8. CLI
# ---------------------------------------------------------------------------


def test_cli_missing_ticker_returns_rc_2(capsys):
    rc = builder.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "missing_ticker" in captured.err


def test_cli_unknown_flag_returns_rc_2():
    rc = builder.main(["--no-such-flag"])
    assert rc == 2


def test_cli_no_systemexit_leak_on_argparse_error():
    rc_seen = None
    try:
        rc_seen = builder.main(["--ticker"])
    except SystemExit:
        rc_seen = "leaked"
    assert rc_seen == 2


def test_cli_happy_path_emits_json(monkeypatch, capsys):
    """Patch the public entry function so the CLI exercises
    the JSON serialization path without touching any real
    adapter / core / disk state."""

    def fake_build(*args, **kwargs):
        return builder.MultiWindowKEnginePayloadReport(
            generated_at="2026-05-13T00:00:00+00:00",
            target_ticker="SPY",
            payload_ready=True,
            K_values=core.CANONICAL_K_VALUES,
            windows=core.CANONICAL_WINDOWS,
            cell_count=60,
            per_window_k_metrics=[],
            build_wide_window_alignment={},
            adapter_summary=None,
            issue_codes=(),
            remaining_limitations=(),
        )

    monkeypatch.setattr(
        builder,
        "build_multiwindow_k_engine_payload",
        fake_build,
    )
    rc = builder.main(["--ticker", "SPY"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["target_ticker"] == "SPY"
    assert payload["payload_ready"] is True


def test_cli_unhandled_exception_returns_rc_3(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(
        builder,
        "build_multiwindow_k_engine_payload",
        boom,
    )
    rc = builder.main(["--ticker", "SPY"])
    assert rc == 3


# ---------------------------------------------------------------------------
# 9. Pinned constant surface
# ---------------------------------------------------------------------------


def test_constants_re_exported():
    assert (
        builder.CANONICAL_WINDOWS == core.CANONICAL_WINDOWS
    )
    assert (
        builder.CANONICAL_K_VALUES
        == core.CANONICAL_K_VALUES
    )


def test_all_issue_codes_exposed():
    for code in builder.ALL_ISSUE_CODES:
        matches = [
            n for n in dir(builder)
            if n.startswith("ISSUE_")
            and getattr(builder, n) == code
        ]
        assert matches, (
            f"issue code {code!r} not exported"
        )
