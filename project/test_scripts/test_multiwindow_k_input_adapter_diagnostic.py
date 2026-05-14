"""Phase 6I-27 tests for multiwindow_k_input_adapter_diagnostic.

Pins the read-only diagnostic's contract:

  - No forbidden top-level imports.
  - No projection / no raw pickle.load.
  - Full canonical fixture produces 60 prepared / 0 skipped.
  - Missing target close surfaces as per-cell
    `missing_target_close` skipped reason.
  - Missing member library surfaces as per-cell
    `incomplete_member_coverage` (strict full-member
    coverage enforced).
  - Length mismatch on a member's signals surfaces as
    `incomplete_member_coverage` per-cell.
  - Diagnostic JSON includes ALL 60 canonical cells, not
    only failures.
  - counts_by_skipped_reason matches the per-cell
    diagnostics counts.
  - dominant_skipped_reason reflects the most-frequent
    non-prepared reason.
  - recommended_next_action stable codes.
  - CLI rc=0 / rc=2 / rc=3 / no SystemExit leak.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import multiwindow_k_engine_core as core  # noqa: E402
import multiwindow_k_input_adapter as adapter  # noqa: E402
import multiwindow_k_input_adapter_diagnostic as diagnostic  # noqa: E402


# ---------------------------------------------------------------------------
# Test fixture helpers (mirror the Phase 6I-22 test pattern)
# ---------------------------------------------------------------------------


class _FakeKRow:
    def __init__(
        self, K: int, members_str: str,
        *, target_ticker: str = "SPY",
        run_id: str = "fake_run",
    ) -> None:
        self.K = K
        self.members_str = members_str
        self.target_ticker = target_ticker
        self.run_id = run_id


def _fake_discovery_returning(run_dir):
    def fn(target_ticker, *, stackbuilder_root=None):
        return run_dir
    return fn


def _fake_leaderboard_loader_ok():
    def fn(run_dir):
        return {"__sentinel__": "leaderboard"}
    return fn


def _fake_k_rows_iter(rows):
    def fn(
        leaderboard, *, target_ticker, run_id, expected_k,
    ):
        wanted = set(int(k) for k in expected_k)
        return [r for r in rows if int(r.K) in wanted]
    return fn


def _bars(window: str, n: int = 3) -> list[Any]:
    return [
        f"2026-01-{i+1:02d}_{window}" for i in range(n)
    ]


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


def _full_target_lib(window: str, n: int = 3):
    return _make_lib(
        dates=_bars(window, n),
        signals=["None"] * n,
        close=[100.0 + i for i in range(n)],
    )


def _full_member_lib(window: str, n: int = 3):
    return _make_lib(
        dates=_bars(window, n),
        signals=["Buy"] * n,
    )


def _library_factory(
    *,
    target_libs,
    member_libs,
    target_ticker: str = "SPY",
):
    def loader(
        ticker, interval, *, signal_library_dir=None,
    ):
        key_upper = (ticker or "").strip().upper()
        if key_upper == target_ticker.upper():
            return target_libs.get(interval)
        return member_libs.get((key_upper, interval))
    return loader


def _full_canonical_fixture(
    *, k_member_sets, bars_per_window: int = 3,
):
    """Build (rows, loader) where every canonical (K,
    window) pair is fully prepared."""
    def _fmt(t, p):
        if p is None:
            return t
        return f"{t}[{p}]"
    rows = [
        _FakeKRow(
            K=K,
            members_str=", ".join(
                _fmt(t, p) for t, p in members
            ),
        )
        for K, members in k_member_sets.items()
    ]
    target_libs = {
        window: _full_target_lib(window, bars_per_window)
        for window in core.CANONICAL_WINDOWS
    }
    member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    all_members: set[str] = set()
    for members in k_member_sets.values():
        for t, _p in members:
            all_members.add(t.upper())
    for member in all_members:
        for window in core.CANONICAL_WINDOWS:
            member_libs[(member, window)] = (
                _full_member_lib(window, bars_per_window)
            )
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    return rows, loader


def _all_canonical_full_k_member_sets():
    return {
        k: [(f"M{i}", "D") for i in range(k)]
        for k in core.CANONICAL_K_VALUES
    }


def _adapter_invocation_kwargs(
    *, run_dir, rows, loader,
):
    return {
        "stackbuilder_run_discovery_callable":
            _fake_discovery_returning(run_dir),
        "leaderboard_loader_callable":
            _fake_leaderboard_loader_ok(),
        "k_rows_iter_callable":
            _fake_k_rows_iter(rows),
        "library_loader": loader,
    }


def _wrap_adapter_with_seams(
    rows, loader, run_dir,
):
    """Return a wrapped adapter callable that has the
    Phase 6I-22 seams pre-injected so the diagnostic
    can call it via its single adapter_callable
    parameter."""
    real = adapter.prepare_multiwindow_k_inputs
    base_kwargs = _adapter_invocation_kwargs(
        run_dir=run_dir, rows=rows, loader=loader,
    )

    def wrapped(target_ticker, **kwargs):
        # Force the run_dir; pass through other adapter
        # kwargs.
        merged = dict(base_kwargs)
        merged["run_dir"] = run_dir
        for k, v in kwargs.items():
            if k in base_kwargs:
                continue
            merged[k] = v
        return real(target_ticker, **merged)

    return wrapped


# ---------------------------------------------------------------------------
# 1. Forbidden imports / no projection / no raw pickle
# ---------------------------------------------------------------------------


def test_diagnostic_module_has_no_forbidden_imports():
    src = Path(diagnostic.__file__).read_text(
        encoding="utf-8",
    )
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
        "multiwindow_k_engine_payload_builder",
        "multiwindow_k_confluence_patch_planner",
        "multiwindow_k_confluence_patch_writer",
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
        f"forbidden first-segment: {bad_first!r}"
    )
    assert not bad_exact, (
        f"forbidden exact-module: {bad_exact!r}"
    )


def test_diagnostic_makes_no_projection_calls():
    src = Path(diagnostic.__file__).read_text(
        encoding="utf-8",
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
                f"diagnostic calls forbidden {name!r}()"
            )


def test_diagnostic_module_has_no_raw_pickle_load():
    src = Path(diagnostic.__file__).read_text(
        encoding="utf-8",
    )
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
                        "diagnostic calls pickle.load() "
                        f"at line {node.lineno}"
                    )


def test_diagnostic_module_has_no_artifact_writes():
    """Defensive: the diagnostic must NOT write any
    on-disk artifact. AST scan rejects
    Path.write_text / Path.write_bytes / json.dump
    call sites."""
    src = Path(diagnostic.__file__).read_text(
        encoding="utf-8",
    )
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in {
                    "write_text",
                    "write_bytes",
                }:
                    raise AssertionError(
                        f"diagnostic calls {func.attr}() "
                        f"at line {node.lineno}"
                    )
                if (
                    func.attr == "dump"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "json"
                ):
                    raise AssertionError(
                        "diagnostic calls json.dump() at "
                        f"line {node.lineno}"
                    )


# ---------------------------------------------------------------------------
# 2. Full canonical fixture: 60 prepared / 0 skipped
# ---------------------------------------------------------------------------


def test_full_canonical_fixture_yields_60_prepared(
    tmp_path,
):
    run_dir = tmp_path / "run_full"
    run_dir.mkdir()
    rows, loader = _full_canonical_fixture(
        k_member_sets=_all_canonical_full_k_member_sets(),
    )
    wrapped = _wrap_adapter_with_seams(
        rows, loader, run_dir,
    )
    result = diagnostic.run_adapter_diagnostic(
        "SPY",
        adapter_callable=wrapped,
    )
    assert result["expected_canonical_cell_count"] == 60
    assert result["prepared_cell_count"] == 60
    assert result["skipped_cell_count"] == 0
    assert (
        result["can_evaluate_full_60_cell_grid"] is True
    )
    assert len(result["per_cell_diagnostics"]) == 60
    assert result["counts_by_skipped_reason"] == {}
    assert result["dominant_skipped_reason"] is None
    assert (
        result["recommended_next_action"]
        == "adapter_ready_for_writer_evidence_run"
    )
    # Every per-cell row reports prepared=True with
    # K members in members_prepared.
    for d in result["per_cell_diagnostics"]:
        assert d["prepared"] is True
        assert (
            len(d["members_prepared"])
            == d["K"]
        )


# ---------------------------------------------------------------------------
# 3. Missing target close surfaces per-cell
# ---------------------------------------------------------------------------


def test_missing_target_close_surfaces_per_cell(tmp_path):
    """Build a fixture where every member library is
    complete BUT the target libraries lack a `close`
    key. The adapter must skip every canonical cell with
    `missing_target_close`."""
    run_dir = tmp_path / "run_no_close"
    run_dir.mkdir()
    rows, _ = _full_canonical_fixture(
        k_member_sets=_all_canonical_full_k_member_sets(),
    )
    # Build target libs WITHOUT close.
    target_libs = {
        window: _make_lib(
            dates=_bars(window, 3),
            signals=["None"] * 3,
            close=None,  # explicitly absent
        )
        for window in core.CANONICAL_WINDOWS
    }
    # Member libs full.
    member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    for k in core.CANONICAL_K_VALUES:
        for i in range(k):
            for window in core.CANONICAL_WINDOWS:
                member_libs[(f"M{i}", window)] = (
                    _full_member_lib(window, 3)
                )
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    wrapped = _wrap_adapter_with_seams(
        rows, loader, run_dir,
    )
    result = diagnostic.run_adapter_diagnostic(
        "SPY",
        adapter_callable=wrapped,
    )
    assert result["prepared_cell_count"] == 0
    assert result["skipped_cell_count"] == 60
    assert (
        result["can_evaluate_full_60_cell_grid"] is False
    )
    # Dominant reason: missing_target_close.
    assert (
        result["dominant_skipped_reason"]
        == adapter.REASON_MISSING_TARGET_CLOSE
    )
    # counts_by_skipped_reason consistent.
    counts = result["counts_by_skipped_reason"]
    assert counts.get(
        adapter.REASON_MISSING_TARGET_CLOSE,
    ) == 60
    # Every per-cell row reports prepared=False with
    # this reason.
    for d in result["per_cell_diagnostics"]:
        assert d["prepared"] is False
        assert (
            d["skipped_reason"]
            == adapter.REASON_MISSING_TARGET_CLOSE
        )
    # Recommended action references the reason.
    assert (
        result["recommended_next_action"]
        == f"resolve_{adapter.REASON_MISSING_TARGET_CLOSE}"
    )


# ---------------------------------------------------------------------------
# 4. Missing member library surfaces per-cell
# ---------------------------------------------------------------------------


def test_missing_member_library_strict_skip_per_cell(
    tmp_path,
):
    """One member library is missing for one window. The
    strict full-member coverage gate skips those cells
    with `incomplete_member_coverage`."""
    run_dir = tmp_path / "run_missing_member"
    run_dir.mkdir()
    rows = [
        _FakeKRow(K=2, members_str="AAA[D], BBB[D]"),
    ]
    target_libs = {
        window: _full_target_lib(window, 3)
        for window in core.CANONICAL_WINDOWS
    }
    # BBB missing for "1y" only.
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
    wrapped = _wrap_adapter_with_seams(
        rows, loader, run_dir,
    )
    result = diagnostic.run_adapter_diagnostic(
        "SPY",
        adapter_callable=wrapped,
    )
    # K=2 prepares 1d / 1wk / 1mo / 3mo (4 cells); 1y
    # skipped with incomplete_member_coverage; every
    # other K row has no K row in the leaderboard -> 11
    # K rows * 5 windows = 55 cells skipped with
    # no_k_row_in_leaderboard.
    assert result["prepared_cell_count"] == 4
    # Find the K=2 / 1y cell and confirm reason.
    k2_1y = next(
        d for d in result["per_cell_diagnostics"]
        if d["K"] == 2 and d["window"] == "1y"
    )
    assert k2_1y["prepared"] is False
    assert (
        k2_1y["skipped_reason"]
        == adapter.REASON_INCOMPLETE_MEMBER_COVERAGE
    )
    assert (
        adapter.REASON_INCOMPLETE_MEMBER_COVERAGE
        in result["counts_by_skipped_reason"]
    )
    # K=2 cells for 1d / 1wk / 1mo / 3mo are prepared
    # with BOTH AAA and BBB in members_prepared.
    for window in ("1d", "1wk", "1mo", "3mo"):
        cell = next(
            d for d in result["per_cell_diagnostics"]
            if d["K"] == 2 and d["window"] == window
        )
        assert cell["prepared"] is True
        assert set(cell["members_prepared"]) == {
            "AAA", "BBB",
        }


# ---------------------------------------------------------------------------
# 5. Length mismatch surfaces per-cell
# ---------------------------------------------------------------------------


def test_member_signal_length_mismatch_strict_skip(
    tmp_path,
):
    run_dir = tmp_path / "run_len_mismatch"
    run_dir.mkdir()
    rows = [_FakeKRow(K=2, members_str="AAA[D], BBB[D]")]
    target_libs = {
        "1d": _full_target_lib("1d", 3),
    }
    member_libs = {
        ("AAA", "1d"): _full_member_lib("1d", 3),
        # BBB has 5 bars, target has 3.
        ("BBB", "1d"): _full_member_lib("1d", 5),
    }
    loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    wrapped = _wrap_adapter_with_seams(
        rows, loader, run_dir,
    )
    result = diagnostic.run_adapter_diagnostic(
        "SPY",
        adapter_callable=wrapped,
    )
    # Find the K=2 / 1d cell.
    k2_1d = next(
        d for d in result["per_cell_diagnostics"]
        if d["K"] == 2 and d["window"] == "1d"
    )
    assert k2_1d["prepared"] is False
    assert (
        k2_1d["skipped_reason"]
        == adapter.REASON_INCOMPLETE_MEMBER_COVERAGE
    )


# ---------------------------------------------------------------------------
# 6. Strict full-member coverage still enforced
# ---------------------------------------------------------------------------


def test_diagnostic_never_forwards_allow_partial_members(
    tmp_path,
):
    """The diagnostic must NEVER allow partial-member
    mode. Pinned by an assertion inside the fake
    adapter callable that fires if the kwarg ever
    appears."""
    captured_kwargs: list[dict] = []

    def spy_adapter(target_ticker, **kwargs):
        captured_kwargs.append(dict(kwargs))
        # Return a minimal not-ready report so the
        # diagnostic completes.
        return adapter.MultiWindowKInputAdapterReport(
            generated_at="2026-05-13T00:00:00+00:00",
            target_ticker="SPY",
            selected_run_dir=None,
            selected_run_id=None,
            K_values=core.CANONICAL_K_VALUES,
            windows=core.CANONICAL_WINDOWS,
            attempted_cell_count=60,
            prepared_cell_count=0,
            missing_cell_count=60,
            can_evaluate_full_60_cell_grid=False,
        )

    diagnostic.run_adapter_diagnostic(
        "SPY",
        adapter_callable=spy_adapter,
    )
    assert len(captured_kwargs) == 1
    assert "allow_partial_members" not in captured_kwargs[0], (
        "diagnostic must NEVER forward "
        "allow_partial_members to the adapter"
    )


# ---------------------------------------------------------------------------
# 7. Diagnostic JSON includes all 60 canonical cells
# ---------------------------------------------------------------------------


def test_diagnostic_includes_all_60_canonical_cells_even_on_failure(
    tmp_path,
):
    """Failure path: no stackbuilder run -> every
    canonical (K, window) pair short-circuits with
    no_stackbuilder_run. The per_cell_diagnostics
    output must still carry all 60 cells, not only
    failures."""
    result = diagnostic.run_adapter_diagnostic(
        "SPY",
        adapter_callable=lambda t, **kw: (
            adapter.prepare_multiwindow_k_inputs(
                t,
                stackbuilder_run_discovery_callable=(
                    _fake_discovery_returning(None)
                ),
                library_loader=lambda *a, **kw: None,
                **{k: v for k, v in kw.items()
                   if k not in (
                       "stackbuilder_run_discovery_callable",
                       "library_loader",
                   )},
            )
        ),
    )
    assert len(result["per_cell_diagnostics"]) == 60
    observed_pairs = {
        (d["K"], d["window"])
        for d in result["per_cell_diagnostics"]
    }
    expected_pairs = {
        (K, w)
        for K in core.CANONICAL_K_VALUES
        for w in core.CANONICAL_WINDOWS
    }
    assert observed_pairs == expected_pairs


# ---------------------------------------------------------------------------
# 8. counts_by_skipped_reason matches per-cell counts
# ---------------------------------------------------------------------------


def test_counts_by_skipped_reason_matches_per_cell(
    tmp_path,
):
    """Build a fixture mixing two failure modes. Confirm
    counts_by_skipped_reason matches the per-cell sum."""
    run_dir = tmp_path / "run_mixed"
    run_dir.mkdir()
    rows = [
        _FakeKRow(K=1, members_str="AAA[D]"),
        _FakeKRow(K=2, members_str="AAA[D], BBB[D]"),
    ]
    target_libs = {
        window: _full_target_lib(window, 3)
        for window in core.CANONICAL_WINDOWS
    }
    # BBB missing for 1y; K=1 only has AAA (always
    # present) so K=1 prepares everywhere.
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
    wrapped = _wrap_adapter_with_seams(
        rows, loader, run_dir,
    )
    result = diagnostic.run_adapter_diagnostic(
        "SPY",
        adapter_callable=wrapped,
    )
    # Recompute counts from per_cell_diagnostics.
    from collections import Counter
    recomputed = Counter()
    for d in result["per_cell_diagnostics"]:
        if not d["prepared"] and d["skipped_reason"]:
            recomputed[d["skipped_reason"]] += 1
    assert (
        dict(recomputed)
        == result["counts_by_skipped_reason"]
    )


# ---------------------------------------------------------------------------
# 9. CLI behavior
# ---------------------------------------------------------------------------


def test_cli_missing_ticker_returns_rc_2(capsys):
    rc = diagnostic.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "missing_ticker" in captured.err


def test_cli_unknown_flag_returns_rc_2():
    rc = diagnostic.main(["--no-such-flag"])
    assert rc == 2


def test_cli_no_systemexit_leak_on_argparse_error():
    rc_seen = None
    try:
        rc_seen = diagnostic.main(["--ticker"])
    except SystemExit:
        rc_seen = "leaked"
    assert rc_seen == 2


def test_cli_happy_path_emits_json(monkeypatch, capsys):
    def fake_run(*args, **kwargs):
        return {
            "ticker": "SPY",
            "generated_at": "2026-05-13T00:00:00+00:00",
            "canonical_k_values_inspected": list(
                core.CANONICAL_K_VALUES,
            ),
            "canonical_windows_inspected": list(
                core.CANONICAL_WINDOWS,
            ),
            "expected_canonical_cell_count": 60,
            "prepared_cell_count": 0,
            "skipped_cell_count": 60,
            "can_evaluate_full_60_cell_grid": False,
            "adapter_issue_codes": [
                "missing_target_close",
            ],
            "selected_run_dir": None,
            "selected_run_id": None,
            "missing_libraries_by_ticker_window": {},
            "unparseable_member_strings": [],
            "per_cell_diagnostics": [],
            "counts_by_skipped_reason": {
                "missing_target_close": 60,
            },
            "dominant_skipped_reason": (
                "missing_target_close"
            ),
            "recommended_next_action": (
                "resolve_missing_target_close"
            ),
        }
    monkeypatch.setattr(
        diagnostic,
        "run_adapter_diagnostic",
        fake_run,
    )
    rc = diagnostic.main(["--ticker", "SPY"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ticker"] == "SPY"
    assert (
        payload["dominant_skipped_reason"]
        == "missing_target_close"
    )


def test_cli_unhandled_exception_returns_rc_3(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(
        diagnostic,
        "run_adapter_diagnostic",
        boom,
    )
    rc = diagnostic.main(["--ticker", "SPY"])
    assert rc == 3


# ---------------------------------------------------------------------------
# 8. Phase 6I-28 close-source threading + happy path
# ---------------------------------------------------------------------------


def test_close_source_root_threaded_through_diagnostic_to_adapter():
    """The diagnostic must forward ``close_source_root`` to
    the adapter so the new fallback path is reachable from
    the CLI surface."""
    captured: dict[str, Any] = {}

    def spy_adapter(target_ticker, **kwargs):
        captured.update(kwargs)
        # Return an empty report-like object that the
        # diagnostic can serialize.
        return adapter.MultiWindowKInputAdapterReport(
            generated_at="2026-05-13T00:00:00",
            target_ticker=target_ticker,
            selected_run_dir=None,
            selected_run_id=None,
            K_values=tuple(core.CANONICAL_K_VALUES),
            windows=tuple(core.CANONICAL_WINDOWS),
            attempted_cell_count=60,
            prepared_cell_count=0,
            missing_cell_count=60,
            can_evaluate_full_60_cell_grid=False,
            issue_codes=(),
        )
    result = diagnostic.run_adapter_diagnostic(
        "SPY",
        close_source_root="/tmp/explicit_close_root",
        adapter_callable=spy_adapter,
    )
    assert (
        captured.get("close_source_root")
        == "/tmp/explicit_close_root"
    )
    assert result["ticker"] == "SPY"


def test_cache_dir_threaded_to_adapter_when_no_close_source_root():
    """When the operator passes ``--cache-dir`` (the
    established convention) and no explicit
    ``--close-source-root``, the diagnostic must use
    ``cache_dir`` as the close-source root."""
    captured: dict[str, Any] = {}

    def spy_adapter(target_ticker, **kwargs):
        captured.update(kwargs)
        return adapter.MultiWindowKInputAdapterReport(
            generated_at="2026-05-13T00:00:00",
            target_ticker=target_ticker,
            selected_run_dir=None,
            selected_run_id=None,
            K_values=tuple(core.CANONICAL_K_VALUES),
            windows=tuple(core.CANONICAL_WINDOWS),
            attempted_cell_count=60,
            prepared_cell_count=0,
            missing_cell_count=60,
            can_evaluate_full_60_cell_grid=False,
            issue_codes=(),
        )
    diagnostic.run_adapter_diagnostic(
        "SPY",
        cache_dir="/tmp/cache_results_alias",
        adapter_callable=spy_adapter,
    )
    assert (
        captured.get("close_source_root")
        == "/tmp/cache_results_alias"
    )


def test_close_source_root_takes_precedence_over_cache_dir():
    """If both ``cache_dir`` and ``close_source_root`` are
    supplied, the explicit ``close_source_root`` wins."""
    captured: dict[str, Any] = {}

    def spy_adapter(target_ticker, **kwargs):
        captured.update(kwargs)
        return adapter.MultiWindowKInputAdapterReport(
            generated_at="2026-05-13T00:00:00",
            target_ticker=target_ticker,
            selected_run_dir=None,
            selected_run_id=None,
            K_values=tuple(core.CANONICAL_K_VALUES),
            windows=tuple(core.CANONICAL_WINDOWS),
            attempted_cell_count=60,
            prepared_cell_count=0,
            missing_cell_count=60,
            can_evaluate_full_60_cell_grid=False,
            issue_codes=(),
        )
    diagnostic.run_adapter_diagnostic(
        "SPY",
        cache_dir="/tmp/cache_results_alias",
        close_source_root="/tmp/explicit_close_root",
        adapter_callable=spy_adapter,
    )
    assert (
        captured.get("close_source_root")
        == "/tmp/explicit_close_root"
    )


def test_close_source_join_makes_diagnostic_report_60_prepared(
    tmp_path,
):
    """End-to-end happy path through the diagnostic with the
    real Phase 6I-22 adapter and a fixture where the target
    libraries lack close but the close-source supplies every
    canonical date -> diagnostic reports prepared=60 /
    skipped=0 / can_evaluate_full_60_cell_grid=True / no
    missing_target_close anywhere."""
    run_dir = tmp_path / "run_close_join_diagnostic_60"
    run_dir.mkdir()
    rows, _ = _full_canonical_fixture(
        k_member_sets=_all_canonical_full_k_member_sets(),
    )
    # Target libs with NO close.
    target_libs = {
        window: _make_lib(
            dates=_bars(window, 3),
            signals=["None"] * 3,
            close=None,
        )
        for window in core.CANONICAL_WINDOWS
    }
    # Members are still complete.
    member_libs: dict[tuple[str, str], dict[str, Any]] = {}
    for k in core.CANONICAL_K_VALUES:
        for i in range(k):
            for window in core.CANONICAL_WINDOWS:
                member_libs[(f"M{i}", window)] = (
                    _full_member_lib(window, 3)
                )
    lib_loader = _library_factory(
        target_libs=target_libs,
        member_libs=member_libs,
    )
    # Close source covers every canonical date.
    close_by_date: dict[str, Any] = {}
    for window in core.CANONICAL_WINDOWS:
        for d in _bars(window, 3):
            key = adapter._normalize_date_key(d)
            if key is not None:
                close_by_date.setdefault(key, 200.0)
    resolution = adapter.CloseSourceResolution(
        status=adapter.CLOSE_SOURCE_STATUS_OK,
        close_by_date=close_by_date,
    )

    def close_loader_fn(ticker, *, close_source_root=None):
        return resolution

    # Wrap the adapter with seams pre-injected, AND forward
    # the new close_loader / close_source_root.
    real_adapter = adapter.prepare_multiwindow_k_inputs

    def wrapped(target_ticker, **kwargs):
        merged = {
            "run_dir": run_dir,
            "stackbuilder_run_discovery_callable":
                _fake_discovery_returning(run_dir),
            "leaderboard_loader_callable":
                _fake_leaderboard_loader_ok(),
            "k_rows_iter_callable":
                _fake_k_rows_iter(rows),
            "library_loader": lib_loader,
            "close_loader": close_loader_fn,
        }
        for k, v in kwargs.items():
            if k in merged:
                continue
            merged[k] = v
        return real_adapter(target_ticker, **merged)
    result = diagnostic.run_adapter_diagnostic(
        "SPY",
        adapter_callable=wrapped,
    )
    assert result["prepared_cell_count"] == 60
    assert result["skipped_cell_count"] == 0
    assert (
        result["can_evaluate_full_60_cell_grid"] is True
    )
    assert result["counts_by_skipped_reason"] == {}
    assert result["dominant_skipped_reason"] is None
    assert (
        result["recommended_next_action"]
        == "adapter_ready_for_writer_evidence_run"
    )
    # And missing_target_close MUST NOT appear in adapter
    # issue codes -- it was resolved via the close-source
    # fallback.
    assert (
        "missing_target_close"
        not in result["adapter_issue_codes"]
    )
