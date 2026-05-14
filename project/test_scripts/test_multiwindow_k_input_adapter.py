"""Phase 6I-22 tests for multiwindow_k_input_adapter.

Pins the adapter's read-only contract:

  - No forbidden top-level imports (writer / refresher /
    pipeline runner / live engines / yfinance / dash /
    subprocess / trafficflow_multitimeframe_bridge).
  - Adapter is NOT a projection: no ``.resample()`` /
    ``.ffill()`` call anywhere in code (AST-verified).
  - Each StackBuilder K row's own member set is carried
    through; K rows are NOT collapsed into one shared
    bundle.
  - Missing member interval library skips only the affected
    cells; the cell can still prepare with the remaining
    members.
  - Missing target interval library skips affected window
    cells across all K (every K row needs the target).
  - Full canonical fixture produces 60 prepared cells, and
    the resulting ``per_cell_inputs`` can be passed directly
    to ``evaluate_k_window_grid`` to produce 60 cells.
  - Missing target ``close`` does NOT fabricate close
    prices; the cell is skipped with a precise reason code.
  - Direct / Inverse protocols are preserved through to the
    per-cell ``member_protocols``.
  - Unparseable members short-circuit the K row across all
    windows.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, Mapping, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import multiwindow_k_engine_core as core  # noqa: E402
import multiwindow_k_input_adapter as adapter  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _FakeKRow:
    """Minimal stand-in for trafficflow_k_artifact_builder.KBuildRow.
    Only ``K`` and ``members_str`` are read by the adapter."""

    def __init__(
        self,
        K: int,
        members_str: str,
        *,
        target_ticker: str = "SPY",
        run_id: str = "fake_run",
    ) -> None:
        self.K = K
        self.members_str = members_str
        self.target_ticker = target_ticker
        self.run_id = run_id


def _fake_discovery_returning(
    run_dir: Optional[Path],
):
    def fn(target_ticker, *, stackbuilder_root=None):
        return run_dir
    return fn


def _fake_leaderboard_loader_ok():
    def fn(run_dir):
        return {"__sentinel__": "leaderboard"}
    return fn


def _fake_k_rows_iter(rows: list[_FakeKRow]):
    def fn(leaderboard, *, target_ticker, run_id, expected_k):
        wanted = set(int(k) for k in expected_k)
        return [r for r in rows if int(r.K) in wanted]
    return fn


def _bars_for_window(window: str, n: int = 3) -> list[Any]:
    """Return ``n`` synthetic bar dates for the given window
    (the window string is just a label here; the adapter does
    not parse dates)."""
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


def _full_member_signals(n: int) -> list[str]:
    return ["Buy"] * n


def _full_target_lib(window: str, n: int = 3) -> dict[str, Any]:
    return _make_lib(
        dates=_bars_for_window(window, n),
        signals=["None"] * n,
        close=[100.0 + i * 1.0 for i in range(n)],
    )


def _full_member_lib(window: str, n: int = 3) -> dict[str, Any]:
    # Member libraries don't need a close column -- the
    # adapter only pulls signals from members.
    return _make_lib(
        dates=_bars_for_window(window, n),
        signals=_full_member_signals(n),
    )


def _library_factory(
    *,
    target_libs: Mapping[str, dict[str, Any]],
    member_libs: Mapping[
        tuple[str, str], dict[str, Any],
    ],
    target_ticker: str = "SPY",
):
    """Build a loader function that returns the supplied per-
    window library for the target AND the supplied per-(ticker,
    window) library for members. Anything not in either map
    returns None (missing library)."""
    def loader(ticker, interval, *, signal_library_dir=None):
        key_upper = (ticker or "").strip().upper()
        if key_upper == target_ticker.upper():
            return target_libs.get(interval)
        return member_libs.get((key_upper, interval))
    return loader


def _full_canonical_fixture(
    *,
    k_member_sets: dict[int, list[tuple[str, Optional[str]]]],
    bars_per_window: int = 3,
):
    """Build (rows, loader) that prepare a full canonical
    grid: every K value × every canonical window has a
    library for both the target and every member.
    """
    def _format_member(t: str, p: Optional[str]) -> str:
        # Real StackBuilder member strings use SQUARE
        # brackets for the protocol code: AAA[D], BBB[I],
        # or just AAA when protocol is None.
        if p is None:
            return str(t)
        return f"{t}[{p}]"
    rows = [
        _FakeKRow(
            K=K,
            members_str=", ".join(
                _format_member(t, p) for t, p in members
            ),
        )
        for K, members in k_member_sets.items()
    ]
    target_libs = {
        window: _full_target_lib(window, bars_per_window)
        for window in core.CANONICAL_WINDOWS
    }
    member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    all_member_tickers: set[str] = set()
    for members in k_member_sets.values():
        for t, _p in members:
            all_member_tickers.add(t.upper())
    for ticker in all_member_tickers:
        for window in core.CANONICAL_WINDOWS:
            member_libs[(ticker, window)] = (
                _full_member_lib(window, bars_per_window)
            )
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    return rows, loader


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_adapter_module_has_no_forbidden_imports():
    src = Path(adapter.__file__).read_text(encoding="utf-8")
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
    # trafficflow_k_artifact_builder is allowed because its
    # name starts with "trafficflow_" not "trafficflow"; the
    # split(".")[0] check correctly distinguishes them.
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


def test_adapter_module_has_no_raw_pickle_load():
    """Phase 6I-22 Codex amendment: this module-local
    regression test pins that the adapter routes through the
    central provenance loader and never falls back to a raw
    ``pickle.load(...)``. The repo-wide B12 guard
    (``test_b12_no_raw_pickle_load_outside_central_loader``)
    enforces the same rule across all production files;
    this test makes the constraint visible inside the
    adapter's own test file so future contributors see the
    contract directly when reading the adapter's tests."""
    src = Path(adapter.__file__).read_text(encoding="utf-8")
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
                        "adapter calls pickle.load() at "
                        f"line {node.lineno} -- route "
                        "through provenance_manifest."
                        "load_verified_signal_library "
                        "(or load_verified_pickle_artifact) "
                        "instead. Raw pickle.load is "
                        "banned in production code by the "
                        "B12 static guard."
                    )


def test_adapter_makes_no_projection_calls():
    src = Path(adapter.__file__).read_text(encoding="utf-8")
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
                f"adapter calls forbidden {name!r}() -- "
                "projection logic belongs to the Phase 6D-2 "
                "bridge, not this adapter"
            )


# ---------------------------------------------------------------------------
# 2. No stackbuilder run / leaderboard short-circuits
# ---------------------------------------------------------------------------


def test_no_stackbuilder_run_short_circuits_all_cells():
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(None)
        ),
        library_loader=lambda *a, **kw: None,
    )
    assert report.selected_run_dir is None
    assert report.selected_run_id is None
    assert report.prepared_cell_count == 0
    assert report.missing_cell_count == 60
    assert report.attempted_cell_count == 60
    assert (
        adapter.ISSUE_NO_STACKBUILDER_RUN
        in report.issue_codes
    )
    for _, _, reason in report.skipped_cells:
        assert reason == adapter.REASON_NO_STACKBUILDER_RUN
    assert (
        report.can_evaluate_full_60_cell_grid is False
    )


def test_leaderboard_load_failure_short_circuits_all_cells(tmp_path):
    run_dir = tmp_path / "fake_run"
    run_dir.mkdir()
    def boom_loader(rd):
        raise FileNotFoundError("fake_missing_xlsx")
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=boom_loader,
        library_loader=lambda *a, **kw: None,
    )
    assert report.selected_run_id == "fake_run"
    assert report.prepared_cell_count == 0
    assert (
        adapter.ISSUE_LEADERBOARD_LOAD_FAILED
        in report.issue_codes
    )
    for _, _, reason in report.skipped_cells:
        assert (
            reason
            == adapter.REASON_LEADERBOARD_LOAD_FAILED
        )


def test_empty_k_rows_short_circuits_all_cells(tmp_path):
    run_dir = tmp_path / "run_empty"
    run_dir.mkdir()
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter([]),
        library_loader=lambda *a, **kw: None,
    )
    assert report.prepared_cell_count == 0
    assert adapter.ISSUE_NO_K_ROWS in report.issue_codes


# ---------------------------------------------------------------------------
# 3. Per-K member sets carried through
# ---------------------------------------------------------------------------


def test_k_rows_with_different_member_sets_produce_different_columns(tmp_path):
    """Codex-amendment invariant: each StackBuilder K row's
    own member set must flow into the per-cell input. K=1
    has one member; K=2 has two members; the adapter must
    NOT collapse them."""
    run_dir = tmp_path / "run_diff_members"
    run_dir.mkdir()
    rows, loader = _full_canonical_fixture(
        k_member_sets={
            1: [("AAA", "D")],
            2: [("AAA", "D"), ("BBB", "I")],
        },
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1, 2),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 2
    cell_1d_k1 = report.per_cell_inputs[(1, "1d")]
    cell_1d_k2 = report.per_cell_inputs[(2, "1d")]
    assert set(cell_1d_k1["member_signal_columns"].keys()) == {"AAA"}
    assert set(cell_1d_k2["member_signal_columns"].keys()) == {"AAA", "BBB"}
    # Protocols flow through.
    assert cell_1d_k1["member_protocols"] == {"AAA": "D"}
    assert cell_1d_k2["member_protocols"] == {"AAA": "D", "BBB": "I"}


def test_per_cell_protocols_preserved_into_evaluator_inputs(tmp_path):
    """Protocols on each per-cell input must reach
    ``evaluate_cell`` / ``evaluate_k_window_grid`` unchanged
    (Direct + Inverse + None protocol entries)."""
    run_dir = tmp_path / "run_protos"
    run_dir.mkdir()
    rows, loader = _full_canonical_fixture(
        k_member_sets={
            1: [("DIR", "D"), ("INV", "I"), ("ANY", None)],
        },
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    cell = report.per_cell_inputs[(1, "1d")]
    assert cell["member_protocols"] == {
        "DIR": "D", "INV": "I", "ANY": None,
    }


# ---------------------------------------------------------------------------
# 4. Missing target library / missing member library
# ---------------------------------------------------------------------------


def test_missing_target_library_skips_affected_window_cells(tmp_path):
    """If the target's signal library is absent for one
    window, every K cell for that window must be skipped
    with reason ``missing_target_library``. Other windows
    must still prepare."""
    run_dir = tmp_path / "run_missing_target"
    run_dir.mkdir()
    rows, _ = _full_canonical_fixture(
        k_member_sets={
            1: [("AAA", "D")],
            2: [("AAA", "D"), ("BBB", "D")],
        },
    )
    # Build a loader where the target is missing for "1mo"
    # but present for "1d" / "1wk" / "3mo" / "1y", and every
    # member is present everywhere.
    target_libs = {
        window: _full_target_lib(window, 3)
        for window in core.CANONICAL_WINDOWS
    }
    del target_libs["1mo"]
    member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    for ticker in ("AAA", "BBB"):
        for window in core.CANONICAL_WINDOWS:
            member_libs[(ticker, window)] = (
                _full_member_lib(window, 3)
            )
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1, 2),
        windows=core.CANONICAL_WINDOWS,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    # 2 K values * 4 present windows = 8 prepared cells.
    assert report.prepared_cell_count == 8
    skipped_pairs = {
        (k, w) for (k, w, _) in report.skipped_cells
    }
    # Both K rows for 1mo must be skipped with
    # missing_target_library.
    assert (1, "1mo") in skipped_pairs
    assert (2, "1mo") in skipped_pairs
    skipped_reasons_for_1mo = {
        reason for (k, w, reason) in report.skipped_cells
        if w == "1mo"
    }
    assert (
        skipped_reasons_for_1mo
        == {adapter.REASON_MISSING_TARGET_LIBRARY}
    )
    assert (
        adapter.ISSUE_MISSING_TARGET_LIBRARY
        in report.issue_codes
    )
    # 1mo target missing for SPY must appear in the missing-
    # libraries map.
    assert "1mo" in (
        report.missing_libraries_by_ticker_window.get(
            "SPY", [],
        )
    )


def test_strict_default_missing_member_skips_cell(tmp_path):
    """Phase 6I-22 Codex amendment (strict member coverage,
    default): when even one member of a K row has no library
    for a given window, the cell is SKIPPED with reason
    ``incomplete_member_coverage`` -- NOT silently prepared
    with the surviving subset. A K=2 ``AAA + BBB`` build with
    BBB missing for ``1y`` does NOT become a K=1 evaluation
    on AAA alone."""
    run_dir = tmp_path / "run_strict_missing_member"
    run_dir.mkdir()
    rows = [
        _FakeKRow(K=1, members_str="AAA[D]"),
        _FakeKRow(K=2, members_str="AAA[D], BBB[D]"),
    ]
    target_libs = {
        window: _full_target_lib(window, 3)
        for window in core.CANONICAL_WINDOWS
    }
    # BBB has libraries for every window EXCEPT 1y; AAA has
    # libraries everywhere.
    member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    for window in core.CANONICAL_WINDOWS:
        member_libs[("AAA", window)] = (
            _full_member_lib(window, 3)
        )
        if window != "1y":
            member_libs[("BBB", window)] = (
                _full_member_lib(window, 3)
            )
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1, 2),
        windows=core.CANONICAL_WINDOWS,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    # K=1 (one member AAA) prepares all 5 windows.
    # K=2 (AAA + BBB) prepares 4 windows (1d/1wk/1mo/3mo
    # where BBB is present); the 1y cell is SKIPPED with
    # reason incomplete_member_coverage because BBB is
    # missing for 1y -- the cell is NOT prepared with only
    # AAA. prepared_cell_count = 5 + 4 = 9.
    assert report.prepared_cell_count == 9
    assert (2, "1y") not in report.per_cell_inputs
    skipped_1y_k2 = [
        (k, w, r) for (k, w, r) in report.skipped_cells
        if k == 2 and w == "1y"
    ]
    assert skipped_1y_k2 == [
        (2, "1y", adapter.REASON_INCOMPLETE_MEMBER_COVERAGE),
    ]
    assert (
        adapter.ISSUE_INCOMPLETE_MEMBER_COVERAGE
        in report.issue_codes
    )
    # The per-cell state for the skipped cell still records
    # the partial coverage so an operator can audit what
    # the cell would have been with allow_partial_members.
    state_1y_k2 = next(
        s for s in report.per_cell_states
        if s.K == 2 and s.window == "1y"
    )
    assert state_1y_k2.prepared is False
    assert state_1y_k2.members_prepared == ("AAA",)
    assert state_1y_k2.members_missing == ("BBB",)
    # Every prepared K=2 cell must carry BOTH members in its
    # member_signal_columns (the full K-row member set, not
    # a surviving subset).
    for window in ("1d", "1wk", "1mo", "3mo"):
        cell = report.per_cell_inputs[(2, window)]
        assert (
            set(cell["member_signal_columns"].keys())
            == {"AAA", "BBB"}
        )


def test_opt_in_partial_members_prepares_partial_cell(tmp_path):
    """Phase 6I-22 Codex amendment (opt-in partial mode):
    when ``allow_partial_members=True``, a cell with one
    missing member DOES prepare with the surviving subset,
    but the per-cell state records the missing member AND
    ``can_evaluate_full_60_cell_grid`` is False even when
    every canonical pair is structurally present."""
    run_dir = tmp_path / "run_partial_mode"
    run_dir.mkdir()
    rows = [
        _FakeKRow(K=2, members_str="AAA[D], BBB[D]"),
    ]
    target_libs = {
        "1d": _full_target_lib("1d", 3),
    }
    member_libs: dict[tuple[str, str], dict[str, Any]] = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
        # BBB missing.
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(2,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        allow_partial_members=True,
    )
    assert report.prepared_cell_count == 1
    cell = report.per_cell_inputs[(2, "1d")]
    # Partial mode: only AAA survives in
    # member_signal_columns; BBB is recorded as missing.
    assert set(
        cell["member_signal_columns"].keys(),
    ) == {"AAA"}
    state = next(
        s for s in report.per_cell_states
        if s.K == 2 and s.window == "1d"
    )
    assert state.members_prepared == ("AAA",)
    assert state.members_missing == ("BBB",)
    # Even with a "full" 2 K_values * 1 window set, partial
    # cells must NEVER qualify as full canonical coverage.
    assert (
        report.can_evaluate_full_60_cell_grid is False
    )


def test_member_only_cell_with_no_surviving_members_is_skipped(tmp_path):
    """When every member of a K row is missing for a given
    window, the cell is skipped with reason
    ``no_members_available`` (no fabricated signal)."""
    run_dir = tmp_path / "run_all_missing"
    run_dir.mkdir()
    rows = [
        _FakeKRow(K=1, members_str="ONLYME[D]"),
    ]
    target_libs = {
        "1d": _full_target_lib("1d", 3),
    }
    # No member library at all.
    loader = _library_factory(
        target_libs=target_libs,
        member_libs={},
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 0
    assert (
        report.skipped_cells[0][2]
        == adapter.REASON_NO_MEMBERS_AVAILABLE
    )


# ---------------------------------------------------------------------------
# 5. Missing target_close is NOT fabricated
# ---------------------------------------------------------------------------


def test_missing_target_close_does_not_fabricate(tmp_path):
    """If the target's per-window library exposes ``dates``
    and ``signals`` but NO ``close`` / ``target_close``,
    the cell is skipped with reason
    ``missing_target_close``. The adapter never invents
    prices."""
    run_dir = tmp_path / "run_no_close"
    run_dir.mkdir()
    rows = [
        _FakeKRow(K=1, members_str="AAA[D]"),
    ]
    # Target library has dates + signals but no close key.
    target_libs = {
        "1d": _make_lib(
            dates=_bars_for_window("1d", 3),
            signals=["None"] * 3,
            close=None,
        ),
    }
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 0
    assert (
        report.skipped_cells[0][2]
        == adapter.REASON_MISSING_TARGET_CLOSE
    )
    assert (
        adapter.ISSUE_MISSING_TARGET_CLOSE
        in report.issue_codes
    )


# ---------------------------------------------------------------------------
# 6. Unparseable members short-circuits the K row
# ---------------------------------------------------------------------------


def test_unparseable_members_short_circuits_the_k_row(tmp_path):
    """A K row whose ``members_str`` parses to no usable
    members short-circuits every window for that K with
    reason ``unparseable_members``. Other K rows still
    prepare."""
    run_dir = tmp_path / "run_unparseable"
    run_dir.mkdir()
    rows = [
        _FakeKRow(K=1, members_str="AAA[D]"),
        _FakeKRow(K=2, members_str="((not parseable))"),
    ]
    target_libs = {
        "1d": _full_target_lib("1d", 3),
        "1wk": _full_target_lib("1wk", 3),
    }
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
        ("AAA", "1wk"): _full_member_lib("1wk", 3),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1, 2),
        windows=("1d", "1wk"),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    # K=1 prepares (1d + 1wk) = 2 cells. K=2 short-circuits
    # both 1d and 1wk with unparseable_members.
    assert report.prepared_cell_count == 2
    assert (1, "1d") in report.per_cell_inputs
    assert (1, "1wk") in report.per_cell_inputs
    for k, w, reason in report.skipped_cells:
        if k == 2:
            assert (
                reason == adapter.REASON_UNPARSEABLE_MEMBERS
            )
    assert any(
        K == 2 for (K, _) in report.unparseable_member_strings
    )
    assert (
        adapter.ISSUE_UNPARSEABLE_MEMBERS
        in report.issue_codes
    )


# ---------------------------------------------------------------------------
# 7. Full canonical fixture -> 60 prepared cells passable to core
# ---------------------------------------------------------------------------


def test_full_canonical_fixture_prepares_60_cells_passable_to_core(tmp_path):
    """The load-bearing end-to-end test: with full canonical
    inputs (12 K rows × 5 canonical windows × all member +
    target libraries present with close prices), the adapter
    must:

      - emit ``prepared_cell_count == 60``;
      - flag ``can_evaluate_full_60_cell_grid == True``;
      - produce a ``per_cell_inputs`` map the Phase 6I-21
        ``evaluate_k_window_grid`` accepts and turns into
        60 ``PerWindowKCell`` cells.
    """
    run_dir = tmp_path / "run_full_60"
    run_dir.mkdir()
    # Build 12 K rows with K-sized member sets.
    k_member_sets: dict[
        int, list[tuple[str, Optional[str]]],
    ] = {}
    for k in core.CANONICAL_K_VALUES:
        k_member_sets[k] = [
            (f"M{i}", "D") for i in range(k)
        ]
    rows, loader = _full_canonical_fixture(
        k_member_sets=k_member_sets,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 60
    assert report.missing_cell_count == 0
    assert (
        report.can_evaluate_full_60_cell_grid is True
    )
    # Cross-module integration assertion: feed the per-cell
    # input map directly into the Phase 6I-21 core.
    cells = core.evaluate_k_window_grid(
        target_ticker="SPY",
        per_cell_inputs=report.per_cell_inputs,
    )
    assert len(cells) == 60
    observed = {(c.K, c.window) for c in cells}
    expected = {
        (k, w)
        for k in core.CANONICAL_K_VALUES
        for w in core.CANONICAL_WINDOWS
    }
    assert observed == expected
    # member_count must equal K because each cell has K
    # members in _full_canonical_fixture.
    for c in cells:
        assert c.member_count == c.K


def test_full_canonical_payload_matches_phase_6i20_required_five(tmp_path):
    """The cells produced from the adapter's full canonical
    output, when run through Phase 6I-21's payload helper,
    carry the Phase 6I-20 required five fields on every
    cell."""
    run_dir = tmp_path / "run_full_60_p"
    run_dir.mkdir()
    k_member_sets = {
        k: [(f"M{i}", "D") for i in range(k)]
        for k in core.CANONICAL_K_VALUES
    }
    rows, loader = _full_canonical_fixture(
        k_member_sets=k_member_sets,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    cells = core.evaluate_k_window_grid(
        target_ticker="SPY",
        per_cell_inputs=report.per_cell_inputs,
    )
    payload = core.cells_to_per_window_k_metrics_payload(
        cells,
    )
    required = {
        "K", "window", "total_capture_pct",
        "sharpe_ratio", "trigger_days",
    }
    assert len(payload) == 60
    for entry in payload:
        assert required.issubset(set(entry.keys()))


# ---------------------------------------------------------------------------
# 8. Empty signals list / dates mismatch
# ---------------------------------------------------------------------------


def test_empty_dates_skips_cell(tmp_path):
    """A target library whose ``dates`` is missing or empty
    is skipped with reason ``empty_library``."""
    run_dir = tmp_path / "run_empty_dates"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_libs = {
        "1d": _make_lib(
            dates=[],
            signals=[],
            close=[],
        ),
    }
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 0
    assert (
        report.skipped_cells[0][2]
        == adapter.REASON_EMPTY_LIBRARY
    )


def test_strict_member_signal_length_mismatch_skips_cell(tmp_path):
    """Phase 6I-22 Codex amendment (strict default), preserved
    through the Phase 6I-29 exact-date alignment widening: a
    member library whose ``signals`` length disagrees with the
    target's ``dates`` length AND whose dates do NOT exact-
    date-align onto the target axis results in the WHOLE cell
    being SKIPPED (incomplete_member_coverage). The adapter
    never resamples / ffills to align AND never silently
    downgrades a K=N build to a K=(N-1) one over a member
    that cannot be exact-date aligned.

    Phase 6I-29 widens length-mismatch from an immediate skip
    to an alignment-attempt, but the strict-coverage gate
    still fires when alignment fails -- this test pins that
    gate by giving BBB a different bar-count AND a date axis
    that does not include every target date.
    """
    run_dir = tmp_path / "run_len_mismatch"
    run_dir.mkdir()
    rows = [_FakeKRow(K=2, members_str="AAA[D], BBB[D]")]
    target_libs = {
        "1d": _full_target_lib("1d", 3),
    }
    # BBB has 5 bars AND its dates do NOT include every target
    # date -- target uses bars 01..03 and BBB uses bars 04..08,
    # so exact-date alignment is incomplete.
    bbb_dates = [f"2026-01-{i+4:02d}_1d" for i in range(5)]
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
        ("BBB", "1d"): _make_lib(
            dates=bbb_dates, signals=["Buy"] * 5,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(2,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 0
    assert (2, "1d") not in report.per_cell_inputs
    assert (
        report.skipped_cells[0][2]
        == adapter.REASON_INCOMPLETE_MEMBER_COVERAGE
    )
    state = next(
        s for s in report.per_cell_states
        if s.K == 2 and s.window == "1d"
    )
    assert state.members_prepared == ("AAA",)
    assert state.members_missing == ("BBB",)
    # Phase 6I-29: BBB's failure mode is now
    # member_date_alignment_incomplete (alignment was
    # attempted), NOT just empty_library.
    assert (
        adapter.ISSUE_MEMBER_DATE_ALIGNMENT_INCOMPLETE
        in report.issue_codes
    )


def test_strict_member_missing_signals_skips_cell(tmp_path):
    """Phase 6I-22 Codex amendment: a member library whose
    ``signals`` key is absent (only ``dates`` present) is
    treated as a missing member, and the whole K cell is
    skipped with ``incomplete_member_coverage`` -- not
    prepared with only the surviving member."""
    run_dir = tmp_path / "run_missing_signals"
    run_dir.mkdir()
    rows = [_FakeKRow(K=2, members_str="AAA[D], BBB[D]")]
    target_libs = {
        "1d": _full_target_lib("1d", 3),
    }
    # BBB library carries dates but NO signals/primary_signals.
    bbb_lib = _make_lib(
        dates=_bars_for_window("1d", 3),
        signals=None,
    )
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
        ("BBB", "1d"): bbb_lib,
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(2,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 0
    assert (
        report.skipped_cells[0][2]
        == adapter.REASON_INCOMPLETE_MEMBER_COVERAGE
    )


def test_full_60_cell_grid_requires_full_per_cell_member_coverage(tmp_path):
    """Phase 6I-22 Codex amendment: even when every
    canonical ``(K, window)`` cell is structurally prepared,
    if even ONE prepared cell is missing one member,
    ``can_evaluate_full_60_cell_grid`` must remain False.
    The verdict's "full canonical coverage" check requires
    BOTH (a) every canonical pair prepared AND (b) every
    prepared cell carrying its FULL K-row member set.

    Built using the opt-in ``allow_partial_members=True``
    path so the cell is structurally prepared with a
    partial-member subset; the verdict must still refuse
    to flip True."""
    run_dir = tmp_path / "run_full_60_partial"
    run_dir.mkdir()
    k_member_sets = {
        k: [(f"M{i}", "D") for i in range(k)]
        for k in core.CANONICAL_K_VALUES
    }
    rows, full_loader = _full_canonical_fixture(
        k_member_sets=k_member_sets,
    )
    # Drop ONE specific member library (member M11 for
    # window 1y) from the loader. M11 only appears in K=12's
    # member set in _full_canonical_fixture (which builds
    # members M0..M(k-1) for K=k), so other K rows are
    # unaffected. K=12 / 1y now has 11-of-12 member coverage
    # instead of the full 12.
    def partial_loader(
        ticker, interval, *, signal_library_dir=None,
    ):
        if (
            ticker.upper() == "M11"
            and interval == "1y"
        ):
            return None
        return full_loader(
            ticker, interval,
            signal_library_dir=signal_library_dir,
        )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=partial_loader,
        allow_partial_members=True,
    )
    # The cell is structurally prepared (allow_partial_members
    # = True), so prepared_cell_count == 60 -- every canonical
    # (K, window) pair has SOMETHING in per_cell_inputs.
    assert report.prepared_cell_count == 60
    # But the K=12 / 1y cell has only 11 members prepared
    # (M11 is missing for 1y).
    state_k12_1y = next(
        s for s in report.per_cell_states
        if s.K == 12 and s.window == "1y"
    )
    assert state_k12_1y.members_prepared == tuple(
        f"M{i}" for i in range(11)
    )
    assert state_k12_1y.members_missing == ("M11",)
    # The load-bearing assertion: partial-member cells
    # never qualify as full canonical coverage.
    assert (
        report.can_evaluate_full_60_cell_grid is False
    )


# ---------------------------------------------------------------------------
# 9. Stable constant surface
# ---------------------------------------------------------------------------


def test_constants_re_exported_from_core():
    assert (
        adapter.CANONICAL_WINDOWS == core.CANONICAL_WINDOWS
    )
    assert (
        adapter.CANONICAL_K_VALUES
        == core.CANONICAL_K_VALUES
    )


def test_all_skipped_reason_codes_exposed_as_attributes():
    for code in adapter.ALL_SKIPPED_REASON_CODES:
        attr = "REASON_" + code.upper()
        # Try the strict (full upper) form first.
        if not hasattr(adapter, attr):
            # Fallback: search any REASON_* attribute whose
            # value matches.
            matches = [
                name for name in dir(adapter)
                if name.startswith("REASON_")
                and getattr(adapter, name) == code
            ]
            assert matches, (
                f"reason code {code!r} not exported"
            )
        else:
            assert getattr(adapter, attr) == code


def test_all_issue_codes_exposed_as_attributes():
    for code in adapter.ALL_ISSUE_CODES:
        matches = [
            name for name in dir(adapter)
            if name.startswith("ISSUE_")
            and getattr(adapter, name) == code
        ]
        assert matches, (
            f"issue code {code!r} not exported"
        )


# ---------------------------------------------------------------------------
# 11. Phase 6I-28 close-source join
# ---------------------------------------------------------------------------


def _close_source_loader_returning(
    resolution: adapter.CloseSourceResolution,
    *,
    call_log: Optional[list[Any]] = None,
):
    """Build a fake close-source loader that returns the
    supplied resolution. ``call_log`` (if provided) captures
    each invocation's ``(ticker, close_source_root)`` kwargs
    so tests can assert the adapter passed the right values."""
    def fn(ticker, *, close_source_root=None):
        if call_log is not None:
            call_log.append((
                str(ticker).strip().upper(),
                close_source_root,
            ))
        return resolution
    return fn


def _ok_close_resolution_for_full_canonical(
    bars_per_window: int = 3,
) -> adapter.CloseSourceResolution:
    """Build a close-by-date map covering every date in the
    full canonical fixture (every canonical window × every
    bar). The fixture's synthetic bar dates are exactly
    ``2026-01-DD_<window>`` strings, which match
    ``_normalize_date_key`` because the leading 10 chars
    parse as ``YYYY-MM-DD``."""
    close_by_date: dict[str, Any] = {}
    for window in core.CANONICAL_WINDOWS:
        for d in _bars_for_window(window, bars_per_window):
            key = adapter._normalize_date_key(d)
            if key is None:
                continue
            close_by_date.setdefault(key, 100.0)
    return adapter.CloseSourceResolution(
        status=adapter.CLOSE_SOURCE_STATUS_OK,
        close_by_date=close_by_date,
    )


def test_close_source_join_resolves_missing_target_close(tmp_path):
    """Target library has dates+signals but NO close. With
    the close-source enabled and a matching exact-date close
    map, the cell prepares with the joined close sequence."""
    run_dir = tmp_path / "run_close_join"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_libs = {
        "1d": _make_lib(
            dates=_bars_for_window("1d", 3),
            signals=["None"] * 3,
            close=None,  # explicitly absent
        ),
    }
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
    }
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    close_by_date = {
        adapter._normalize_date_key(d): 200.0 + i
        for i, d in enumerate(_bars_for_window("1d", 3))
    }
    close_resolution = adapter.CloseSourceResolution(
        status=adapter.CLOSE_SOURCE_STATUS_OK,
        close_by_date=close_by_date,
    )
    call_log: list[Any] = []
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        close_loader=_close_source_loader_returning(
            close_resolution, call_log=call_log,
        ),
    )
    assert report.prepared_cell_count == 1
    assert report.skipped_cells == ()
    cell = report.per_cell_inputs[(1, "1d")]
    assert cell["target_close"] == [200.0, 201.0, 202.0]
    # Adapter must NOT emit missing_target_close when the
    # fallback supplied a complete join.
    assert (
        adapter.ISSUE_MISSING_TARGET_CLOSE
        not in report.issue_codes
    )
    # The close-source loader was called at most once across
    # all per-cell loops -- per-call caching.
    assert len(call_log) == 1
    assert call_log[0][0] == "SPY"


def test_close_source_missing_surfaces_dedicated_reason(
    tmp_path,
):
    """When the close source resolves to ``status="missing"``,
    cells skip with ``target_close_source_missing`` -- NOT
    the legacy ``missing_target_close`` -- so operators can
    tell the two failure modes apart."""
    run_dir = tmp_path / "run_close_missing"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_libs = {
        "1d": _make_lib(
            dates=_bars_for_window("1d", 3),
            signals=["None"] * 3,
            close=None,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs={
            ("AAA", "1d"): _full_member_lib("1d", 3),
        },
    )
    missing_resolution = adapter.CloseSourceResolution(
        status=adapter.CLOSE_SOURCE_STATUS_MISSING,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        close_loader=_close_source_loader_returning(
            missing_resolution,
        ),
    )
    assert report.prepared_cell_count == 0
    assert report.skipped_cells[0][2] == (
        adapter.REASON_TARGET_CLOSE_SOURCE_MISSING
    )
    assert (
        adapter.ISSUE_TARGET_CLOSE_SOURCE_MISSING
        in report.issue_codes
    )
    assert (
        adapter.ISSUE_MISSING_TARGET_CLOSE
        not in report.issue_codes
    )


def test_close_source_unreadable_surfaces_dedicated_reason(
    tmp_path,
):
    """When the close source resolves to ``status="unreadable"``,
    cells skip with ``target_close_source_unreadable``."""
    run_dir = tmp_path / "run_close_unreadable"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_libs = {
        "1d": _make_lib(
            dates=_bars_for_window("1d", 3),
            signals=["None"] * 3,
            close=None,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs={
            ("AAA", "1d"): _full_member_lib("1d", 3),
        },
    )
    unreadable_resolution = adapter.CloseSourceResolution(
        status=adapter.CLOSE_SOURCE_STATUS_UNREADABLE,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        close_loader=_close_source_loader_returning(
            unreadable_resolution,
        ),
    )
    assert report.prepared_cell_count == 0
    assert report.skipped_cells[0][2] == (
        adapter.REASON_TARGET_CLOSE_SOURCE_UNREADABLE
    )
    assert (
        adapter.ISSUE_TARGET_CLOSE_SOURCE_UNREADABLE
        in report.issue_codes
    )


def test_close_source_partial_dates_surfaces_join_incomplete(
    tmp_path,
):
    """When the close-source map covers SOME but NOT all
    library dates, the cell is skipped with
    ``target_close_join_incomplete``. The adapter MUST NOT
    fabricate / ffill / resample the missing date's close."""
    run_dir = tmp_path / "run_close_partial"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    dates = _bars_for_window("1d", 3)
    target_libs = {
        "1d": _make_lib(
            dates=dates,
            signals=["None"] * 3,
            close=None,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs={
            ("AAA", "1d"): _full_member_lib("1d", 3),
        },
    )
    # Map only contains the FIRST two of the three library
    # dates; the third has no close.
    close_by_date = {
        adapter._normalize_date_key(dates[0]): 100.0,
        adapter._normalize_date_key(dates[1]): 101.0,
    }
    partial_resolution = adapter.CloseSourceResolution(
        status=adapter.CLOSE_SOURCE_STATUS_OK,
        close_by_date=close_by_date,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        close_loader=_close_source_loader_returning(
            partial_resolution,
        ),
    )
    assert report.prepared_cell_count == 0
    assert report.skipped_cells[0][2] == (
        adapter.REASON_TARGET_CLOSE_JOIN_INCOMPLETE
    )
    assert (
        adapter.ISSUE_TARGET_CLOSE_JOIN_INCOMPLETE
        in report.issue_codes
    )
    # No partial close was written to per_cell_inputs.
    assert report.per_cell_inputs == {}


def test_close_source_disabled_preserves_legacy_missing_close(
    tmp_path,
):
    """When BOTH ``close_source_root`` and ``close_loader``
    are None, the adapter MUST preserve the Phase 6I-22
    legacy behaviour: missing-close cells skip with
    ``missing_target_close``. This pins backwards
    compatibility for callers that don't know about the new
    surface."""
    run_dir = tmp_path / "run_legacy_no_close"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_libs = {
        "1d": _make_lib(
            dates=_bars_for_window("1d", 3),
            signals=["None"] * 3,
            close=None,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs={
            ("AAA", "1d"): _full_member_lib("1d", 3),
        },
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        # No close_source_root / no close_loader -- the
        # opt-in fallback path must NOT engage.
    )
    assert report.prepared_cell_count == 0
    assert report.skipped_cells[0][2] == (
        adapter.REASON_MISSING_TARGET_CLOSE
    )
    assert (
        adapter.ISSUE_MISSING_TARGET_CLOSE
        in report.issue_codes
    )
    # And none of the new close-source issue codes fire.
    for code in (
        adapter.ISSUE_TARGET_CLOSE_SOURCE_MISSING,
        adapter.ISSUE_TARGET_CLOSE_SOURCE_UNREADABLE,
        adapter.ISSUE_TARGET_CLOSE_JOIN_INCOMPLETE,
    ):
        assert code not in report.issue_codes


def test_native_close_in_library_still_preferred_over_close_source(
    tmp_path,
):
    """If the target library already has ``close``, the
    adapter MUST use it -- the close-source loader must not
    be invoked at all (the per-call cache stays empty)."""
    run_dir = tmp_path / "run_native_close"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    native_closes = [10.0, 11.0, 12.0]
    target_libs = {
        "1d": _make_lib(
            dates=_bars_for_window("1d", 3),
            signals=["None"] * 3,
            close=list(native_closes),
        ),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs={
            ("AAA", "1d"): _full_member_lib("1d", 3),
        },
    )
    call_log: list[Any] = []
    fallback_resolution = adapter.CloseSourceResolution(
        status=adapter.CLOSE_SOURCE_STATUS_OK,
        close_by_date={
            adapter._normalize_date_key(d): 999.0
            for d in _bars_for_window("1d", 3)
        },
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        close_loader=_close_source_loader_returning(
            fallback_resolution, call_log=call_log,
        ),
    )
    assert report.prepared_cell_count == 1
    cell = report.per_cell_inputs[(1, "1d")]
    # Native close wins. Fallback was NOT consulted.
    assert cell["target_close"] == native_closes
    assert call_log == []


def test_close_source_does_not_relax_strict_member_coverage(
    tmp_path,
):
    """The close-source join only supplies the target close.
    It MUST NOT relax the strict full-member-coverage gate:
    if any member library is missing, the cell still skips
    with ``incomplete_member_coverage`` (the Phase 6I-22
    Codex amendment invariant)."""
    run_dir = tmp_path / "run_strict_unchanged"
    run_dir.mkdir()
    rows = [_FakeKRow(K=2, members_str="AAA[D], BBB[D]")]
    target_libs = {
        "1d": _make_lib(
            dates=_bars_for_window("1d", 3),
            signals=["None"] * 3,
            close=None,
        ),
    }
    # AAA has a 1d library, BBB does NOT.
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
    }
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    close_by_date = {
        adapter._normalize_date_key(d): 100.0
        for d in _bars_for_window("1d", 3)
    }
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(2,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        close_loader=_close_source_loader_returning(
            adapter.CloseSourceResolution(
                status=adapter.CLOSE_SOURCE_STATUS_OK,
                close_by_date=close_by_date,
            ),
        ),
    )
    assert report.prepared_cell_count == 0
    # The cell skipped because BBB had no library -- NOT
    # because of any close-source issue.
    assert report.skipped_cells[0][2] == (
        adapter.REASON_INCOMPLETE_MEMBER_COVERAGE
    )


def test_close_source_full_canonical_fixture_prepares_60_cells(
    tmp_path,
):
    """End-to-end happy path: target libraries lack close,
    members are full, close source supplies exact-date close
    for every canonical date -> all 60 cells prepare AND
    ``can_evaluate_full_60_cell_grid=True``."""
    run_dir = tmp_path / "run_close_join_60"
    run_dir.mkdir()
    rows, lib_loader = _full_canonical_fixture(
        k_member_sets={
            k: [(f"M{i}", "D") for i in range(k)]
            for k in core.CANONICAL_K_VALUES
        },
    )
    # Re-build target libs WITHOUT close; reuse member libs.
    no_close_target_libs = {
        window: _make_lib(
            dates=_bars_for_window(window, 3),
            signals=["None"] * 3,
            close=None,
        )
        for window in core.CANONICAL_WINDOWS
    }
    full_member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    all_members: set[str] = set()
    for K in core.CANONICAL_K_VALUES:
        for i in range(K):
            all_members.add(f"M{i}")
    for member in all_members:
        for window in core.CANONICAL_WINDOWS:
            full_member_libs[(member, window)] = (
                _full_member_lib(window, 3)
            )
    loader = _library_factory(
        target_libs=no_close_target_libs,
        member_libs=full_member_libs,
    )
    resolution = _ok_close_resolution_for_full_canonical()
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        close_loader=_close_source_loader_returning(
            resolution,
        ),
    )
    assert report.prepared_cell_count == 60
    assert report.missing_cell_count == 0
    assert report.can_evaluate_full_60_cell_grid is True
    # And every prepared cell got the joined close.
    for (K, window), cell in report.per_cell_inputs.items():
        assert (
            len(cell["target_close"])
            == len(cell["dates"])
        )


def test_close_source_root_path_threaded_to_loader(tmp_path):
    """``close_source_root`` argument should be forwarded
    intact to the close-source loader so production / tests
    can point at different directories."""
    run_dir = tmp_path / "run_close_root_thread"
    run_dir.mkdir()
    custom_root = tmp_path / "custom_close_root"
    custom_root.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_libs = {
        "1d": _make_lib(
            dates=_bars_for_window("1d", 3),
            signals=["None"] * 3,
            close=None,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs={
            ("AAA", "1d"): _full_member_lib("1d", 3),
        },
    )
    call_log: list[Any] = []
    close_by_date = {
        adapter._normalize_date_key(d): 100.0
        for d in _bars_for_window("1d", 3)
    }
    resolution = adapter.CloseSourceResolution(
        status=adapter.CLOSE_SOURCE_STATUS_OK,
        close_by_date=close_by_date,
    )
    adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,),
        windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        close_source_root=custom_root,
        close_loader=_close_source_loader_returning(
            resolution, call_log=call_log,
        ),
    )
    assert call_log
    received_root = call_log[0][1]
    # The adapter wrapped the str/Path into a Path before
    # forwarding; identity is not required, but path equality
    # under Path is.
    assert Path(received_root) == custom_root


def test_normalize_date_key_handles_common_shapes():
    """The normalizer must handle ``Timestamp``-like /
    ``datetime``-like / plain-string inputs and return ISO
    ``YYYY-MM-DD`` substrings. None / empty inputs return
    None."""
    # datetime / Timestamp-like via duck-typing
    import datetime as _dt
    assert (
        adapter._normalize_date_key(_dt.date(2026, 5, 13))
        == "2026-05-13"
    )
    assert (
        adapter._normalize_date_key(
            _dt.datetime(2026, 5, 13, 16, 0, 0),
        )
        == "2026-05-13"
    )
    # ISO strings: leading 10 chars survive
    assert (
        adapter._normalize_date_key("2026-05-13")
        == "2026-05-13"
    )
    assert (
        adapter._normalize_date_key(
            "2026-05-13T00:00:00+00:00"
        )
        == "2026-05-13"
    )
    # Pre-existing fixture format -- 2026-01-XX_1d -- truncates
    # to the date head.
    assert (
        adapter._normalize_date_key("2026-01-01_1d")
        == "2026-01-01"
    )
    # Null / empty
    assert adapter._normalize_date_key(None) is None
    assert adapter._normalize_date_key("") is None


def test_close_source_helpers_make_no_projection_calls():
    """Defensive AST scan: the Phase 6I-28 close-source
    helpers MUST NOT call ``.resample()`` / ``.ffill()`` --
    the spec is exact-date-only."""
    src = Path(adapter.__file__).read_text(encoding="utf-8")
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
                "adapter calls forbidden "
                f"{name!r}() at line {node.lineno}"
            )


# ---------------------------------------------------------------------------
# 12. Phase 6I-29 exact-date member alignment
# ---------------------------------------------------------------------------


def test_member_alignment_equal_length_uses_fast_path(tmp_path):
    """When member library length equals the target's date
    axis length, the Phase 6I-29 alignment helper is NOT
    invoked -- the fast path preserves the Phase 6I-22
    legacy semantics exactly. Pinned by checking that the
    cell prepares without surfacing any Phase 6I-29 issue
    code."""
    run_dir = tmp_path / "run_equal_len"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_libs = {"1d": _full_target_lib("1d", 3)}
    member_libs = {("AAA", "1d"): _full_member_lib("1d", 3)}
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,), windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 1
    assert (
        adapter.ISSUE_MEMBER_DATE_ALIGNMENT_INCOMPLETE
        not in report.issue_codes
    )
    assert (
        adapter.ISSUE_MEMBER_SIGNAL_DATE_AXIS_MISSING
        not in report.issue_codes
    )


def test_member_alignment_extra_older_dates_succeeds(tmp_path):
    """Member library has extra OLDER dates (member starts
    before the target's first bar). The target's dates form
    a subset of the member's dates -> exact-date alignment
    succeeds -> cell prepares with the aligned member
    signals."""
    run_dir = tmp_path / "run_older_dates"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_dates = _bars_for_window("1d", 3)  # 01,02,03
    # Member has 5 bars: 2 older + the 3 target dates.
    older = ["2025-12-30_1d", "2025-12-31_1d"]
    member_dates = older + list(target_dates)
    target_libs = {
        "1d": _make_lib(
            dates=target_dates,
            signals=["None"] * 3,
            close=[100.0, 101.0, 102.0],
        ),
    }
    member_signals = ["Buy", "Short", "Buy", "Short", "Buy"]
    member_libs = {
        ("AAA", "1d"): _make_lib(
            dates=member_dates, signals=member_signals,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,), windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 1
    cell = report.per_cell_inputs[(1, "1d")]
    # Aligned signals correspond to target dates 01,02,03 ->
    # member positions 2,3,4 -> "Buy", "Short", "Buy".
    assert cell["member_signal_columns"]["AAA"] == [
        "Buy", "Short", "Buy",
    ]


def test_member_alignment_extra_newer_dates_succeeds(tmp_path):
    """Member library has extra NEWER dates after the
    target's last bar. Target dates form a subset of member
    dates -> alignment succeeds."""
    run_dir = tmp_path / "run_newer_dates"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_dates = _bars_for_window("1d", 3)
    newer = ["2026-01-04_1d", "2026-01-05_1d"]
    member_dates = list(target_dates) + newer
    target_libs = {
        "1d": _make_lib(
            dates=target_dates,
            signals=["None"] * 3,
            close=[100.0, 101.0, 102.0],
        ),
    }
    member_signals = ["A", "B", "C", "D", "E"]
    member_libs = {
        ("AAA", "1d"): _make_lib(
            dates=member_dates, signals=member_signals,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,), windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 1
    cell = report.per_cell_inputs[(1, "1d")]
    assert cell["member_signal_columns"]["AAA"] == [
        "A", "B", "C",
    ]


def test_member_alignment_missing_one_target_date_skips_member(
    tmp_path,
):
    """If even ONE target date is missing from the member's
    date axis, alignment is incomplete. The member must NOT
    be fabricated, the strict-coverage gate fires, and the
    cell is skipped with REASON_INCOMPLETE_MEMBER_COVERAGE
    (single-member K=1 -> the cell becomes
    no_members_available).

    Member library uses a DIFFERENT bar count than the
    target so the alignment helper is exercised (the
    length-equal fast path is bypassed)."""
    run_dir = tmp_path / "run_missing_target_date"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_dates = _bars_for_window("1d", 3)  # 01,02,03
    # Member has 4 dates (length-different from target's 3)
    # AND is missing target's middle date (02).
    member_dates = [
        "2026-01-01_1d",
        "2026-01-03_1d",
        "2026-01-04_1d",
        "2026-01-05_1d",
    ]
    target_libs = {
        "1d": _make_lib(
            dates=target_dates,
            signals=["None"] * 3,
            close=[100.0, 101.0, 102.0],
        ),
    }
    member_libs = {
        ("AAA", "1d"): _make_lib(
            dates=member_dates,
            signals=["W", "X", "Y", "Z"],
        ),
    }
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,), windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 0
    # K=1 with only one member missing -> the cell short-
    # circuits with no_members_available.
    assert (
        report.skipped_cells[0][2]
        == adapter.REASON_NO_MEMBERS_AVAILABLE
    )
    assert (
        adapter.ISSUE_MEMBER_DATE_ALIGNMENT_INCOMPLETE
        in report.issue_codes
    )


def test_member_alignment_member_dates_missing_surfaces_axis_missing(
    tmp_path,
):
    """A member library that has signals but no usable
    ``dates`` / ``date_index`` key returns the
    member_signal_date_axis_missing reason on the alignment
    helper, surfaces the corresponding issue code, and skips
    the cell under strict coverage."""
    run_dir = tmp_path / "run_axis_missing"
    run_dir.mkdir()
    rows = [_FakeKRow(K=1, members_str="AAA[D]")]
    target_libs = {"1d": _full_target_lib("1d", 3)}
    # AAA has signals but NO dates field, AND a length that
    # doesn't match the target (so the fast path is bypassed
    # and the alignment helper is consulted).
    member_libs = {
        ("AAA", "1d"): {"signals": ["X", "Y", "Z", "W"]},
    }
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(1,), windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 0
    assert (
        adapter.ISSUE_MEMBER_SIGNAL_DATE_AXIS_MISSING
        in report.issue_codes
    )


def test_member_alignment_mixed_k_row_strict_coverage_holds(
    tmp_path,
):
    """K=2 row where AAA aligns successfully (superset
    dates) but BBB cannot align (missing target date). The
    strict full-member-coverage gate still fires and the
    whole cell skips with incomplete_member_coverage --
    Phase 6I-29 does NOT silently downgrade K=2 to K=1.

    Both members use bar counts DIFFERENT from the target
    so the alignment helper is exercised for both."""
    run_dir = tmp_path / "run_mixed_alignment"
    run_dir.mkdir()
    rows = [_FakeKRow(K=2, members_str="AAA[D], BBB[D]")]
    target_dates = _bars_for_window("1d", 3)
    target_libs = {
        "1d": _make_lib(
            dates=target_dates,
            signals=["None"] * 3,
            close=[100.0, 101.0, 102.0],
        ),
    }
    # AAA: 5 bars, superset of target dates -> aligns.
    aaa_dates = (
        ["2025-12-31_1d"] + list(target_dates)
        + ["2026-01-04_1d"]
    )
    # BBB: 4 bars, missing target date 02 -> alignment
    # incomplete.
    bbb_dates = [
        "2026-01-01_1d",
        "2026-01-03_1d",
        "2026-01-04_1d",
        "2026-01-05_1d",
    ]
    member_libs = {
        ("AAA", "1d"): _make_lib(
            dates=aaa_dates, signals=["a"] * 5,
        ),
        ("BBB", "1d"): _make_lib(
            dates=bbb_dates, signals=["b"] * 4,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(2,), windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 0
    assert (
        report.skipped_cells[0][2]
        == adapter.REASON_INCOMPLETE_MEMBER_COVERAGE
    )
    state = next(
        s for s in report.per_cell_states
        if s.K == 2 and s.window == "1d"
    )
    assert state.members_prepared == ("AAA",)
    assert state.members_missing == ("BBB",)


def test_member_alignment_partial_mode_does_not_unlock_full_grid(
    tmp_path,
):
    """``allow_partial_members=True`` is a diagnostic mode
    only. With Phase 6I-29 alignment widening, AAA may
    align and BBB may fail; partial mode prepares the cell
    with the surviving member ONLY, but
    ``can_evaluate_full_60_cell_grid`` MUST remain False
    because the cell does not carry the FULL K-row member
    set. The Phase 6I-22 invariant is preserved.

    Both members use bar counts DIFFERENT from the target
    so the alignment helper is exercised."""
    run_dir = tmp_path / "run_partial_alignment"
    run_dir.mkdir()
    rows = [_FakeKRow(K=2, members_str="AAA[D], BBB[D]")]
    target_dates = _bars_for_window("1d", 3)
    target_libs = {
        "1d": _make_lib(
            dates=target_dates,
            signals=["None"] * 3,
            close=[100.0, 101.0, 102.0],
        ),
    }
    aaa_dates = (
        ["2025-12-31_1d"] + list(target_dates)
        + ["2026-01-04_1d"]
    )
    # BBB is length-different (4 vs target's 3) AND missing
    # target date 02 -> alignment helper fires and fails.
    bbb_dates = [
        "2026-01-01_1d",
        "2026-01-03_1d",
        "2026-01-04_1d",
        "2026-01-05_1d",
    ]
    member_libs = {
        ("AAA", "1d"): _make_lib(
            dates=aaa_dates, signals=["a"] * 5,
        ),
        ("BBB", "1d"): _make_lib(
            dates=bbb_dates, signals=["b"] * 4,
        ),
    }
    loader = _library_factory(
        target_libs=target_libs, member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        K_values=(2,), windows=("1d",),
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
        allow_partial_members=True,
    )
    # In partial mode the cell prepares with AAA only ...
    assert report.prepared_cell_count == 1
    cell = report.per_cell_inputs[(2, "1d")]
    assert (
        sorted(cell["member_signal_columns"].keys())
        == ["AAA"]
    )
    # ... but the canonical-grid verdict MUST stay False
    # because the prepared cell does NOT carry the full
    # 2-member K-row set.
    assert (
        report.can_evaluate_full_60_cell_grid is False
    )


def test_member_alignment_helper_directly_returns_aligned_signals():
    """Direct unit test of the helper: a superset member
    library returns the correctly-ordered aligned signal
    list. Pins the target-date-order invariant."""
    target_dates = [
        "2026-01-01", "2026-01-02", "2026-01-03",
    ]
    member_dates = [
        "2025-12-31",
        "2026-01-02",
        "2026-01-01",  # duplicate? no -- different from "01"
        "2026-01-03",
        "2026-01-04",
    ]
    member_signals = ["a", "b", "c", "d", "e"]
    aligned, reason = (
        adapter._align_member_signals_to_target_dates(
            target_dates, member_dates, member_signals,
        )
    )
    assert reason is None
    # Target order: 01 -> "c", 02 -> "b", 03 -> "d".
    assert aligned == ["c", "b", "d"]


def test_member_alignment_helper_directly_returns_incomplete():
    target_dates = [
        "2026-01-01", "2026-01-02", "2026-01-03",
    ]
    member_dates = ["2026-01-01", "2026-01-03"]
    member_signals = ["x", "y"]
    aligned, reason = (
        adapter._align_member_signals_to_target_dates(
            target_dates, member_dates, member_signals,
        )
    )
    assert aligned is None
    assert reason == (
        adapter.REASON_MEMBER_DATE_ALIGNMENT_INCOMPLETE
    )


def test_member_alignment_helper_returns_axis_missing_on_none_dates():
    target_dates = ["2026-01-01"]
    aligned, reason = (
        adapter._align_member_signals_to_target_dates(
            target_dates, None, ["x"],
        )
    )
    assert aligned is None
    assert reason == (
        adapter.REASON_MEMBER_SIGNAL_DATE_AXIS_MISSING
    )


def test_member_alignment_full_canonical_daily_prepares_60(
    tmp_path,
):
    """End-to-end happy path: target libs are NATIVE-close
    (no fallback required) AND every K-row member's daily
    signal library has a superset date axis (extra older +
    newer bars). The Phase 6I-29 alignment widening allows
    all 60 canonical cells to prepare; the strict full-
    member-coverage verdict flips to True.

    Tests the per-window canonical fixture for the 1d
    window only because Phase 6I-29 intentionally does NOT
    address the non-daily date-axis blocker; non-daily
    windows continue to use equal-length fast-path member
    libraries (same as the Phase 6I-22 happy-path fixture)."""
    run_dir = tmp_path / "run_full_daily_alignment"
    run_dir.mkdir()
    rows, _ = _full_canonical_fixture(
        k_member_sets={
            k: [(f"M{i}", "D") for i in range(k)]
            for k in core.CANONICAL_K_VALUES
        },
    )
    # Target libs: identical to the canonical fixture's
    # 1d entry plus matching dates for all canonical
    # windows. Use the helper-built target libs (with
    # native close) so the close-source fallback is not
    # required.
    target_libs = {
        window: _full_target_lib(window, 3)
        for window in core.CANONICAL_WINDOWS
    }
    # For 1d members: build superset libraries that
    # include the target's 3 dates plus extra older +
    # newer bars (so they trigger the Phase 6I-29
    # alignment widening). For non-1d members: keep the
    # equal-length fast path so this test isolates the
    # daily-alignment behaviour.
    all_members: set[str] = set()
    for K in core.CANONICAL_K_VALUES:
        for i in range(K):
            all_members.add(f"M{i}")
    member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    target_1d_dates = _bars_for_window("1d", 3)
    superset_1d_dates = (
        ["2025-12-30_1d"] + list(target_1d_dates)
        + ["2026-01-04_1d", "2026-01-05_1d"]
    )
    for member in all_members:
        for window in core.CANONICAL_WINDOWS:
            if window == "1d":
                member_libs[(member, window)] = _make_lib(
                    dates=superset_1d_dates,
                    signals=["Buy"] * len(
                        superset_1d_dates,
                    ),
                )
            else:
                member_libs[(member, window)] = (
                    _full_member_lib(window, 3)
                )
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=run_dir,
        stackbuilder_run_discovery_callable=(
            _fake_discovery_returning(run_dir)
        ),
        leaderboard_loader_callable=(
            _fake_leaderboard_loader_ok()
        ),
        k_rows_iter_callable=_fake_k_rows_iter(rows),
        library_loader=loader,
    )
    assert report.prepared_cell_count == 60
    assert report.missing_cell_count == 0
    assert report.can_evaluate_full_60_cell_grid is True


def test_member_alignment_no_projection_calls_in_helper():
    """Phase 6I-29 helper AST-scanned for absence of
    ``.resample()`` / ``.ffill()``. The existing module-
    wide AST guard already covers this, but a focused
    pin makes the contract obvious."""
    import inspect
    src = inspect.getsource(
        adapter._align_member_signals_to_target_dates,
    )
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
                f"alignment helper calls forbidden "
                f"{name!r}() at line {node.lineno}"
            )
