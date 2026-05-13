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
    """Phase 6I-22 Codex amendment (strict default): a member
    library whose ``signals`` length disagrees with the
    target's ``dates`` length results in the WHOLE cell being
    SKIPPED (incomplete_member_coverage), not a partial
    cell. The adapter never resamples / ffills to align AND
    never silently downgrades a K=N build to a K=(N-1) one
    over a length mismatch."""
    run_dir = tmp_path / "run_len_mismatch"
    run_dir.mkdir()
    rows = [_FakeKRow(K=2, members_str="AAA[D], BBB[D]")]
    target_libs = {
        "1d": _full_target_lib("1d", 3),
    }
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
        # BBB has 5 bars, doesn't match target's 3.
        ("BBB", "1d"): _full_member_lib("1d", 5),
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
