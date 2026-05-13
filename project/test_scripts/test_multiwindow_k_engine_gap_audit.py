"""Phase 6I-20 tests for multiwindow_k_engine_gap_audit.

Pins the audit's gap contract:

  - No forbidden top-level imports (writer / refresher /
    pipeline runner / yfinance / dash / live engines /
    subprocess).
  - A daily-K-only fixture does NOT pass as a true multi-
    window engine.
  - An MTF-bridge / Confluence-projection fixture alone
    does NOT pass as a true multi-window engine unless
    the future-shape per-window K metric fields exist on
    the Confluence artifact.
  - A full future-shaped fixture (all K=1..12 + all 5
    canonical windows + per_window_k_metrics +
    build_wide_window_alignment) DOES pass as a true
    multi-window engine.
  - Incomplete K coverage is detected.
  - Incomplete timeframe coverage is detected.
  - Missing build-wide all-members/all-windows alignment
    fields is detected.
  - CLI returns JSON and rc=0 / rc=2 / rc=3 without
    SystemExit leak.
"""
from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import multiwindow_k_engine_gap_audit as audit  # noqa: E402


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeValidation:
    """Duck-typed stand-in for
    TickerRankingContractValidation. Only the attributes
    the audit reads are required."""

    ticker: str = "SPY"
    stackbuilder_contract_ok: bool = True
    selected_stackbuilder_run_id: Optional[str] = "run_001"
    daily_k_contract_ok: bool = True
    daily_k_coverage: tuple[int, ...] = tuple(range(1, 13))
    mtf_contract_ok: bool = True
    mtf_k_coverage: tuple[int, ...] = tuple(range(1, 13))
    confluence_contract_ok: bool = True
    confluence_last_date: Optional[str] = "2026-05-08"
    issue_codes: tuple[str, ...] = ()


def _validator_returning(
    val: _FakeValidation,
):
    def fn(ticker, **kwargs):
        return val
    return fn


def _inspector_returning(
    payload: Mapping[str, Any],
):
    def fn(ticker, *, artifact_root=None, **kwargs):
        return dict(payload)
    return fn


def _full_future_shaped_payload() -> dict[str, Any]:
    """A Confluence artifact whose top level carries the
    Phase 6I-20-defined future-engine fields. This is the
    only fixture that passes
    ``has_true_multiwindow_k_engine_outputs=True``."""
    per_window_k_metrics: list[dict[str, Any]] = []
    for k in range(1, 13):
        for window in ("1d", "1wk", "1mo", "3mo", "1y"):
            per_window_k_metrics.append({
                "K": k,
                "window": window,
                "total_capture_pct": 5.0 + k * 0.1,
                "sharpe_ratio": 0.05,
                "trigger_days": 100,
            })
    build_wide_window_alignment: dict[str, Any] = {
        "1d": {
            "all_members_firing": True,
            "firing_member_count": 12,
            "total_member_count": 12,
        },
        "1wk": {
            "all_members_firing": True,
            "firing_member_count": 12,
            "total_member_count": 12,
        },
        "1mo": {
            "all_members_firing": True,
            "firing_member_count": 12,
            "total_member_count": 12,
        },
        "3mo": {
            "all_members_firing": True,
            "firing_member_count": 12,
            "total_member_count": 12,
        },
        "1y": {
            "all_members_firing": True,
            "firing_member_count": 12,
            "total_member_count": 12,
        },
    }
    return {
        "timeframes": ["1d", "1wk", "1mo", "3mo", "1y"],
        "K_values": list(range(1, 13)),
        "per_window_k_metrics": per_window_k_metrics,
        "build_wide_window_alignment": (
            build_wide_window_alignment
        ),
    }


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_audit_module_has_no_forbidden_imports():
    """The audit module must not import any writer /
    refresher / pipeline runner / live engine / yfinance
    / dash / subprocess at top level. The Phase 6I-1
    contract validator (read-only by contract) IS allowed.
    The Phase 6I-5 universe planner helper is lazy-
    imported only inside the aggregate audit function
    when --from-stackbuilder-universe is set."""
    tree = ast.parse(
        Path(audit.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
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
    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad = [
        m for m in found if m.split(".")[0] in forbidden
    ]
    assert not bad, (
        f"forbidden import in audit: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. Daily-K-only fixture does NOT pass as true engine
# ---------------------------------------------------------------------------


def test_daily_only_fixture_does_not_pass_as_true_engine():
    """A ticker with only daily-K artifacts (no MTF, no
    Confluence, no per-window K metrics, no build-wide
    alignment) must report
    ``has_true_multiwindow_k_engine_outputs=False`` and
    surface the relevant missing-capability codes."""
    val = _FakeValidation(
        ticker="DAILY",
        stackbuilder_contract_ok=True,
        daily_k_contract_ok=True,
        daily_k_coverage=tuple(range(1, 13)),
        mtf_contract_ok=False,
        mtf_k_coverage=(),
        confluence_contract_ok=False,
        confluence_last_date=None,
    )
    state = audit.audit_multiwindow_k_engine_gap(
        "DAILY",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning({})
        ),
    )
    assert state.daily_k_artifacts_present is True
    assert state.mtf_bridge_artifacts_present is False
    assert state.confluence_artifact_present is False
    assert state.has_per_window_k_metrics is False
    assert (
        state.has_build_wide_all_members_all_windows_signal
        is False
    )
    assert (
        state.has_true_multiwindow_k_engine_outputs is False
    )
    missing = set(state.missing_capabilities)
    assert audit.MISSING_MTF_BRIDGE_ARTIFACTS in missing
    assert audit.MISSING_CONFLUENCE_ARTIFACT in missing
    assert (
        audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE in missing
    )
    assert audit.MISSING_PER_WINDOW_K_METRICS in missing
    assert (
        audit.MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS
        in missing
    )


# ---------------------------------------------------------------------------
# 3. MTF-bridge / Confluence fixture alone does NOT pass
# ---------------------------------------------------------------------------


def test_mtf_bridge_only_does_not_pass_as_true_engine():
    """A ticker with daily K + MTF bridge + Confluence
    artifacts present BUT no per-window K metric fields
    on the Confluence artifact must NOT pass as the true
    multi-window engine. This is the load-bearing
    distinction: existing MTF artifacts project daily
    signals onto resampled windows; they are NOT per-
    window K evaluations."""
    val = _FakeValidation(
        ticker="MTF",
        stackbuilder_contract_ok=True,
        daily_k_contract_ok=True,
        daily_k_coverage=tuple(range(1, 13)),
        mtf_contract_ok=True,
        mtf_k_coverage=tuple(range(1, 13)),
        confluence_contract_ok=True,
        confluence_last_date="2026-05-08",
    )
    # Inspector returns the existing artifact shape:
    # timeframes + K_values populated (as the
    # Phase 6D-3 builder emits) but NO
    # per_window_k_metrics / build_wide_window_alignment.
    inspector_payload = {
        "timeframes": [
            "1d", "1wk", "1mo", "3mo", "1y",
        ],
        "K_values": list(range(1, 13)),
    }
    state = audit.audit_multiwindow_k_engine_gap(
        "MTF",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(inspector_payload)
        ),
    )
    assert state.daily_k_artifacts_present is True
    assert state.mtf_bridge_artifacts_present is True
    assert state.confluence_artifact_present is True
    # All five canonical windows + all twelve K values
    # are surfaced from the upstream artifact -- BUT the
    # future-engine fields are absent, so the audit must
    # still report no true engine.
    assert (
        set(state.observed_timeframes)
        == set(audit.CANONICAL_WINDOWS)
    )
    assert (
        set(state.observed_k_values)
        == set(audit.CANONICAL_K_VALUES)
    )
    assert state.has_per_window_k_metrics is False
    assert (
        state.has_build_wide_all_members_all_windows_signal
        is False
    )
    assert (
        state.has_true_multiwindow_k_engine_outputs is False
    )
    missing = set(state.missing_capabilities)
    assert (
        audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE in missing
    )
    assert audit.MISSING_PER_WINDOW_K_METRICS in missing
    assert (
        audit.MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS
        in missing
    )
    # Daily / MTF / Confluence layers are present so
    # those codes must NOT fire.
    assert audit.MISSING_DAILY_K_ARTIFACTS not in missing
    assert (
        audit.MISSING_MTF_BRIDGE_ARTIFACTS not in missing
    )
    assert (
        audit.MISSING_CONFLUENCE_ARTIFACT not in missing
    )


def test_bridge_with_daily_only_per_window_metrics_does_not_pass():
    """If a future engine emits ``per_window_k_metrics``
    but every entry has ``window == "1d"``, the audit
    must still reject it as a true multi-window engine.
    A daily-only per-window list is not multi-window."""
    val = _FakeValidation(ticker="DAILYPW")
    daily_only_per_window: list[dict[str, Any]] = []
    for k in range(1, 13):
        daily_only_per_window.append({
            "K": k,
            "window": "1d",
            "total_capture_pct": 5.0,
            "sharpe_ratio": 0.05,
            "trigger_days": 100,
        })
    inspector_payload = {
        "timeframes": ["1d", "1wk", "1mo", "3mo", "1y"],
        "K_values": list(range(1, 13)),
        "per_window_k_metrics": daily_only_per_window,
    }
    state = audit.audit_multiwindow_k_engine_gap(
        "DAILYPW",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(inspector_payload)
        ),
    )
    assert state.has_per_window_k_metrics is False
    assert (
        state.has_true_multiwindow_k_engine_outputs is False
    )
    assert (
        audit.MISSING_PER_WINDOW_K_METRICS
        in state.missing_capabilities
    )


# ---------------------------------------------------------------------------
# 3b. Partial per_window_k_metrics coverage does NOT pass
# (Phase 6I-20 Codex amendment: full 60-cell grid required)
# ---------------------------------------------------------------------------


def test_per_window_metrics_one_k_across_all_windows_does_not_pass():
    """Codex audit (Phase 6I-20 amendment): a partial
    ``per_window_k_metrics`` covering ONLY K=1 across all
    five canonical windows must NOT pass as the true
    multi-window engine even when
    ``build_wide_window_alignment`` is valid AND observed
    K_values / timeframes look full from the existing
    upstream artifact. A single K is not the full
    canonical 12-K cross-section."""
    val = _FakeValidation(ticker="ONEK")
    one_k_only: list[dict[str, Any]] = []
    for window in ("1d", "1wk", "1mo", "3mo", "1y"):
        one_k_only.append({
            "K": 1,
            "window": window,
            "total_capture_pct": 5.0,
            "sharpe_ratio": 0.05,
            "trigger_days": 100,
        })
    payload = _full_future_shaped_payload()
    payload["per_window_k_metrics"] = one_k_only
    state = audit.audit_multiwindow_k_engine_gap(
        "ONEK",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(payload)
        ),
    )
    # observed_k_values + observed_timeframes still look
    # "full" because they reflect the existing upstream
    # ranking row, not per_window_k_metrics' coverage.
    assert (
        set(state.observed_k_values)
        == set(audit.CANONICAL_K_VALUES)
    )
    assert (
        set(state.observed_timeframes)
        == set(audit.CANONICAL_WINDOWS)
    )
    # But per_window_k_metrics is too thin -> rejected.
    assert state.has_per_window_k_metrics is False
    assert (
        state.has_true_multiwindow_k_engine_outputs is False
    )
    missing = set(state.missing_capabilities)
    assert audit.MISSING_PER_WINDOW_K_METRICS in missing
    assert (
        audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE in missing
    )
    # build_wide_window_alignment is valid in this fixture
    # so its capability MUST NOT be flagged as missing --
    # the per_window grid is the only gap.
    assert (
        audit.MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS
        not in missing
    )


def test_per_window_metrics_missing_one_canonical_window_does_not_pass():
    """Codex audit (Phase 6I-20 amendment): a
    ``per_window_k_metrics`` list that covers all 12 K
    values across four canonical windows but OMITS one
    (e.g. ``1y``) across all K must NOT pass as the true
    multi-window engine. The 60-cell canonical grid
    requires every (K, window) pair; a missing column
    breaks coverage."""
    val = _FakeValidation(ticker="NO1Y")
    four_window_only: list[dict[str, Any]] = []
    for k in range(1, 13):
        for window in ("1d", "1wk", "1mo", "3mo"):
            four_window_only.append({
                "K": k,
                "window": window,
                "total_capture_pct": 5.0 + k * 0.1,
                "sharpe_ratio": 0.05,
                "trigger_days": 100,
            })
    payload = _full_future_shaped_payload()
    payload["per_window_k_metrics"] = four_window_only
    state = audit.audit_multiwindow_k_engine_gap(
        "NO1Y",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(payload)
        ),
    )
    assert state.has_per_window_k_metrics is False
    assert (
        state.has_true_multiwindow_k_engine_outputs is False
    )
    missing = set(state.missing_capabilities)
    assert audit.MISSING_PER_WINDOW_K_METRICS in missing
    assert (
        audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE in missing
    )


def test_per_window_metrics_noncanonical_windows_do_not_substitute():
    """Codex audit (Phase 6I-20 amendment): noncanonical
    windows like ``2d`` and ``5d`` may appear in
    ``per_window_k_metrics`` as extras, but they MUST
    NOT substitute for missing canonical cells. A
    payload covering only noncanonical windows (no
    canonical cells at all) is rejected even when every
    entry is well-formed."""
    val = _FakeValidation(ticker="NONCAN")
    noncanonical_only: list[dict[str, Any]] = []
    for k in range(1, 13):
        for window in ("2d", "5d"):
            noncanonical_only.append({
                "K": k,
                "window": window,
                "total_capture_pct": 5.0,
                "sharpe_ratio": 0.05,
                "trigger_days": 100,
            })
    payload = _full_future_shaped_payload()
    payload["per_window_k_metrics"] = noncanonical_only
    state = audit.audit_multiwindow_k_engine_gap(
        "NONCAN",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(payload)
        ),
    )
    assert state.has_per_window_k_metrics is False
    assert (
        state.has_true_multiwindow_k_engine_outputs is False
    )
    assert (
        audit.MISSING_PER_WINDOW_K_METRICS
        in state.missing_capabilities
    )


def test_per_window_metrics_extras_on_top_of_canonical_60_pass():
    """Codex audit (Phase 6I-20 amendment): extras (extra
    K values OR extra windows OR extra fields per entry)
    on top of the canonical 60-cell grid do NOT
    invalidate the metric. The contract is "canonical 60
    must be present"; additional cells are tolerated."""
    val = _FakeValidation(ticker="EXTRA")
    payload = _full_future_shaped_payload()
    # Append a noncanonical window on top of the full
    # 60-cell grid that the helper already produces.
    extras = list(payload["per_window_k_metrics"])
    for k in range(1, 13):
        extras.append({
            "K": k,
            "window": "2d",
            "total_capture_pct": 9.9,
            "sharpe_ratio": 0.99,
            "trigger_days": 999,
            # Extra entry field is allowed.
            "extra_diagnostic_field": "ok",
        })
    payload["per_window_k_metrics"] = extras
    state = audit.audit_multiwindow_k_engine_gap(
        "EXTRA",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(payload)
        ),
    )
    assert state.has_per_window_k_metrics is True
    assert (
        state.has_true_multiwindow_k_engine_outputs is True
    )


# ---------------------------------------------------------------------------
# 4. Full future-shaped fixture passes
# ---------------------------------------------------------------------------


def test_full_future_shaped_fixture_passes_as_true_engine():
    """A fixture that exposes all K=1..12 + all five
    canonical windows + a valid per_window_k_metrics list
    + a valid build_wide_window_alignment mapping must
    pass ``has_true_multiwindow_k_engine_outputs=True``
    AND emit no MISSING_* codes for the true-engine
    fields."""
    val = _FakeValidation(ticker="FUTURE")
    state = audit.audit_multiwindow_k_engine_gap(
        "FUTURE",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(_full_future_shaped_payload())
        ),
    )
    assert state.has_per_window_k_metrics is True
    assert (
        state.has_build_wide_all_members_all_windows_signal
        is True
    )
    assert (
        state.has_true_multiwindow_k_engine_outputs is True
    )
    missing = set(state.missing_capabilities)
    assert (
        audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE
        not in missing
    )
    assert (
        audit.MISSING_PER_WINDOW_K_METRICS not in missing
    )
    assert (
        audit.MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS
        not in missing
    )


# ---------------------------------------------------------------------------
# 5. Incomplete K coverage detected
# ---------------------------------------------------------------------------


def test_incomplete_k_coverage_detected():
    """If observed K_values are a proper subset of the
    canonical 1..12 range, the audit must emit
    INCOMPLETE_K_COVERAGE."""
    val = _FakeValidation(
        ticker="PARTIALK",
        daily_k_coverage=(1, 2, 3),
        mtf_k_coverage=(1, 2, 3),
    )
    inspector_payload = {
        "timeframes": ["1d", "1wk", "1mo", "3mo", "1y"],
        "K_values": [1, 2, 3],
    }
    state = audit.audit_multiwindow_k_engine_gap(
        "PARTIALK",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(inspector_payload)
        ),
    )
    assert state.observed_k_values == (1, 2, 3)
    assert (
        audit.INCOMPLETE_K_COVERAGE
        in state.missing_capabilities
    )


# ---------------------------------------------------------------------------
# 6. Incomplete timeframe coverage detected
# ---------------------------------------------------------------------------


def test_incomplete_timeframe_coverage_detected():
    """If observed timeframes are a proper subset of the
    canonical {1d, 1wk, 1mo, 3mo, 1y} set, the audit must
    emit INCOMPLETE_TIMEFRAME_COVERAGE."""
    val = _FakeValidation(ticker="PARTIALTF")
    inspector_payload = {
        "timeframes": ["1d", "1wk"],
        "K_values": list(range(1, 13)),
    }
    state = audit.audit_multiwindow_k_engine_gap(
        "PARTIALTF",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(inspector_payload)
        ),
    )
    assert state.observed_timeframes == ("1d", "1wk")
    assert (
        audit.INCOMPLETE_TIMEFRAME_COVERAGE
        in state.missing_capabilities
    )


# ---------------------------------------------------------------------------
# 7. Missing build-wide alignment fields detected
# ---------------------------------------------------------------------------


def test_missing_build_wide_alignment_fields_detected():
    """When the artifact carries valid
    per_window_k_metrics but omits
    build_wide_window_alignment, the audit must report
    has_build_wide_all_members_all_windows_signal=False,
    emit MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS, and
    still report
    has_true_multiwindow_k_engine_outputs=False (the true
    engine requires BOTH fields)."""
    val = _FakeValidation(ticker="HALF")
    payload = _full_future_shaped_payload()
    payload.pop("build_wide_window_alignment")
    state = audit.audit_multiwindow_k_engine_gap(
        "HALF",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(payload)
        ),
    )
    assert state.has_per_window_k_metrics is True
    assert (
        state.has_build_wide_all_members_all_windows_signal
        is False
    )
    assert (
        state.has_true_multiwindow_k_engine_outputs is False
    )
    missing = set(state.missing_capabilities)
    assert (
        audit.MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS
        in missing
    )
    assert (
        audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE in missing
    )


def test_build_wide_alignment_missing_canonical_window_rejected():
    """The build-wide alignment dict must carry an entry
    for EVERY canonical window. If even one canonical
    window is missing from the mapping, the audit must
    reject it."""
    val = _FakeValidation(ticker="MISSWIN")
    payload = _full_future_shaped_payload()
    # Drop the 1y window from the alignment mapping.
    payload["build_wide_window_alignment"].pop("1y")
    state = audit.audit_multiwindow_k_engine_gap(
        "MISSWIN",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(payload)
        ),
    )
    assert (
        state.has_build_wide_all_members_all_windows_signal
        is False
    )
    assert (
        state.has_true_multiwindow_k_engine_outputs is False
    )
    assert (
        audit.MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS
        in state.missing_capabilities
    )


# ---------------------------------------------------------------------------
# 8. Recommended next build step text
# ---------------------------------------------------------------------------


def test_recommended_next_build_step_names_future_engine():
    """The recommended_next_build_step text must, in the
    common current-pipeline case (Confluence artifact
    present + no future fields), name building the future
    multi-window K engine + the specific artifact field
    'per_window_k_metrics'."""
    val = _FakeValidation(ticker="SPY")
    inspector_payload = {
        "timeframes": ["1d", "1wk", "1mo", "3mo", "1y"],
        "K_values": list(range(1, 13)),
    }
    state = audit.audit_multiwindow_k_engine_gap(
        "SPY",
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(inspector_payload)
        ),
    )
    step = state.recommended_next_build_step
    assert "future TrafficFlow-style multi-window K engine" in step
    assert "per_window_k_metrics" in step
    assert "does NOT exist yet" in step


# ---------------------------------------------------------------------------
# 9. JSON round-trip
# ---------------------------------------------------------------------------


def test_report_to_json_dict_round_trips():
    val = _FakeValidation(ticker="SPY")
    report = audit.audit_multiwindow_k_engine_gaps(
        tickers=["SPY"],
        validator_callable=_validator_returning(val),
        confluence_artifact_inspector_callable=(
            _inspector_returning(
                _full_future_shaped_payload(),
            )
        ),
    )
    payload = report.to_json_dict()
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored["inspected_count"] == 1
    assert restored["states"][0]["ticker"] == "SPY"
    assert (
        restored["states"][0][
            "has_true_multiwindow_k_engine_outputs"
        ]
        is True
    )
    assert "remaining_limitations" in restored
    assert "counts_by_missing_capability" in restored


def test_aggregate_buckets_tickers_correctly():
    """tickers_with / tickers_missing_true_multiwindow_k
    _engine buckets must partition the inspected set."""
    full_val = _FakeValidation(ticker="FUTURE")
    bare_val = _FakeValidation(
        ticker="DAILY",
        mtf_contract_ok=False,
        mtf_k_coverage=(),
        confluence_contract_ok=False,
        confluence_last_date=None,
    )

    def mixed_validator(ticker, **kwargs):
        if ticker == "FUTURE":
            return full_val
        return bare_val

    def mixed_inspector(ticker, *, artifact_root=None, **kwargs):
        if ticker == "FUTURE":
            return _full_future_shaped_payload()
        return {}

    report = audit.audit_multiwindow_k_engine_gaps(
        tickers=["FUTURE", "DAILY"],
        validator_callable=mixed_validator,
        confluence_artifact_inspector_callable=(
            mixed_inspector
        ),
    )
    assert (
        report.tickers_with_true_multiwindow_k_engine
        == ("FUTURE",)
    )
    assert (
        report.tickers_missing_true_multiwindow_k_engine
        == ("DAILY",)
    )


# ---------------------------------------------------------------------------
# 10. CLI
# ---------------------------------------------------------------------------


def test_cli_no_ticker_source_returns_rc_2(capsys):
    rc = audit.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "no_ticker_source_supplied" in captured.err


def test_cli_unknown_flag_returns_rc_2():
    rc = audit.main(["--no-such-flag"])
    assert rc == 2


def test_cli_no_systemexit_leak_on_argparse_error():
    rc_seen = None
    try:
        rc_seen = audit.main(["--top-n"])
    except SystemExit:
        rc_seen = "leaked"
    assert rc_seen == 2


def test_cli_happy_path_emits_json(monkeypatch, capsys):
    """Run the CLI via main(argv=...) with a monkey-
    patched aggregate so no real validator / on-disk
    artifact / live engine is touched."""

    def fake_evaluate(*args, **kwargs):
        state = audit.MultiWindowKEngineGapState(
            ticker="SPY",
            current_as_of_date="2026-05-12",
            stackbuilder_contract_ok=True,
            stackbuilder_selected_run_id="run_001",
            stackbuilder_run_count=1,
            stackbuilder_k_coverage=tuple(range(1, 13)),
            daily_k_artifacts_present=True,
            daily_k_coverage=tuple(range(1, 13)),
            mtf_bridge_artifacts_present=True,
            mtf_k_coverage=tuple(range(1, 13)),
            confluence_artifact_present=True,
            confluence_last_date="2026-05-08",
            observed_timeframes=(
                "1d", "1wk", "1mo", "3mo", "1y",
            ),
            observed_k_values=tuple(range(1, 13)),
            has_per_window_k_metrics=False,
            has_build_wide_all_members_all_windows_signal=(
                False
            ),
            has_true_multiwindow_k_engine_outputs=False,
            missing_capabilities=(
                audit.MISSING_PER_WINDOW_K_METRICS,
                audit.MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS,
                audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE,
            ),
            recommended_next_build_step=(
                "Build the future TrafficFlow-style "
                "multi-window K engine."
            ),
            contract_issue_codes=(),
        )
        return audit.MultiWindowKEngineGapReport(
            generated_at="2026-05-13T00:00:00+00:00",
            current_as_of_date="2026-05-12",
            inspected_count=1,
            discovered_stackbuilder_ticker_count=0,
            states=(state,),
            counts_by_missing_capability={
                audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE: 1,
            },
            tickers_with_true_multiwindow_k_engine=(),
            tickers_missing_true_multiwindow_k_engine=(
                "SPY",
            ),
            remaining_limitations=(),
        )

    monkeypatch.setattr(
        audit,
        "audit_multiwindow_k_engine_gaps",
        fake_evaluate,
    )
    rc = audit.main(["--ticker", "SPY"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["inspected_count"] == 1
    assert payload["states"][0]["ticker"] == "SPY"
    assert (
        payload["states"][0][
            "has_true_multiwindow_k_engine_outputs"
        ]
        is False
    )
    assert (
        audit.MISSING_TRUE_MULTIWINDOW_K_ENGINE
        in payload["states"][0]["missing_capabilities"]
    )
    assert (
        "SPY"
        in payload[
            "tickers_missing_true_multiwindow_k_engine"
        ]
    )


def test_cli_unhandled_exception_returns_rc_3(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(
        audit,
        "audit_multiwindow_k_engine_gaps",
        boom,
    )
    rc = audit.main(["--ticker", "SPY"])
    assert rc == 3


# ---------------------------------------------------------------------------
# 11. Stable missing-capability code surface
# ---------------------------------------------------------------------------


def test_all_missing_capability_codes_exposed():
    """Every code listed in the audit's
    ALL_MISSING_CAPABILITY_CODES tuple must be an exported
    module attribute. This pins the audit's public
    contract -- consumers can import the constants by
    name."""
    for code in audit.ALL_MISSING_CAPABILITY_CODES:
        # Find a matching module attribute whose value
        # equals the code (the constant name is the upper-
        # case version of the code).
        attr = code.upper()
        assert hasattr(audit, attr), (
            f"missing module attribute for code: {code!r}"
        )
        assert getattr(audit, attr) == code


def test_canonical_windows_and_k_values_pinned():
    assert audit.CANONICAL_WINDOWS == (
        "1d", "1wk", "1mo", "3mo", "1y",
    )
    assert audit.CANONICAL_K_VALUES == tuple(range(1, 13))
