"""Phase 6I-56 tests for the ImpactSearch workbook runner.

Pins:

  * Schema + constant + taxonomy stability.
  * Ticker safety rejects path-unsafe inputs before any
    filesystem access.
  * Primary-universe resolution (3 sources).
  * Secondary data-source classifier honestly reports
    ``yfinance_required`` for every secondary today (per
    ``impactsearch.py:1753 fetch_data_raw`` /
    ``impactsearch.py:2002 fetch_data``); local cache
    presence is noted only.
  * Workbook action classifier mirrors
    ``stackbuilder.try_load_rank_from_impact_xlsx``
    behavior across missing / stale / fresh / load-error /
    manifest-rejected.
  * Eligibility classifier: BLOCKED on unsafe / empty /
    fresh / write-without-network-when-network-needed.
  * Plan dry-run does not write; unsafe-only ``--write``
    does not create output dir.
  * Command manifest argv[0] is the pinned interpreter
    AND uses ``<SECONDARY>_analysis.xlsx`` naming inside
    ``output/impactsearch``.
  * Authorized execute path is fully exercised inside
    ``tmp_path`` via the injected callable override.
  * ``_atomic_export_workbook`` renames partials on
    success and removes them on failure.
  * AST guards: no forbidden top-level imports
    (yfinance / subprocess / dash / impactsearch /
    stackbuilder / writer / engine modules); no raw
    ``pickle.load(...)``; no ``yf.download(...)`` /
    ``subprocess.run(...)`` call anywhere in source.
  * Expected workbook columns matched against
    ``stackbuilder._RANK_COLMAP``.
  * Production-state smoke skips cleanly when
    ``output/impactsearch`` is absent.
  * Evidence-doc precision-wording guard for Section 7
    test counts.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import impactsearch_workbook_runner as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeManifestResult:
    ok: bool = True
    legacy: bool = False
    mismatches: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _make_loader_returning(
    df_per_path: dict[str, Any],
    *,
    result_per_path: dict[str, _FakeManifestResult] | None = None,
) -> Callable[..., Any]:
    """Fake verified_loader that returns canned ``(df,
    result)`` pairs keyed by the resolved string path."""
    result_per_path = result_per_path or {}

    def _loader(path, *, strict=False):
        key = str(path)
        df = df_per_path.get(key)
        if df is None:
            return None, _FakeManifestResult(
                ok=False, legacy=False,
                mismatches=[("load_error", "FNF", key)],
            )
        result = result_per_path.get(
            key,
            _FakeManifestResult(ok=True, legacy=False),
        )
        return df, result

    return _loader


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
        os.utime(p, (new_mtime, new_mtime))
    except OSError:
        pass
    return p


def _make_price_cache_csv(
    pcd: Path, ticker: str,
) -> Path:
    pcd.mkdir(parents=True, exist_ok=True)
    p = pcd / f"{ticker.upper()}.csv"
    p.write_bytes(b"Date,Close\n2026-01-01,100\n")
    return p


def _good_rank_df(n: int = 5):
    import pandas as pd
    return pd.DataFrame(
        [
            {
                "Primary Ticker": f"PRIM{i:03d}",
                "Total Capture (%)": 50.0 - i,
                "Trigger Days": 30,
                "Sharpe Ratio": 1.0,
                "Win Ratio (%)": 55.0,
                "Std Dev (%)": 1.0,
                "Avg Daily Capture (%)": 0.1,
            }
            for i in range(n)
        ]
    )


# ---------------------------------------------------------------------------
# 1. Schema / taxonomy / authorization-class stability
# ---------------------------------------------------------------------------


def test_schema_and_constants_are_stable():
    assert runner.SCHEMA_VERSION == (
        "impactsearch_workbook_runner_v1"
    )
    assert runner.PINNED_INTERPRETER == (
        "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/"
        "envs/spyproject2/python.exe"
    )
    assert runner.DEFAULT_SECONDARIES == (
        "SPY", "AAPL", "JNJ", "WMT", "HD", "MCD",
    )
    assert runner.DEFAULT_IMPACT_XLSX_DIR_RELATIVE == (
        "output/impactsearch"
    )
    assert runner.DEFAULT_PRICE_CACHE_DIR_RELATIVE == (
        "price_cache/daily"
    )
    assert runner.DEFAULT_SIGNAL_LIB_DIR_RELATIVE == (
        "signal_library/data/stable"
    )
    assert runner.DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS == 45
    for s in (
        runner.PRIMARY_SOURCE_EXPLICIT_CSV,
        runner.PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE,
        runner.PRIMARY_SOURCE_SIGNAL_LIBRARY_DIR,
    ):
        assert s in runner.ALL_PRIMARY_SOURCES
    for s in (
        runner.WORKBOOK_ACTION_ALREADY_FRESH,
        runner.WORKBOOK_ACTION_STALE_NEEDS_REGENERATION,
        runner.WORKBOOK_ACTION_MISSING_NEEDS_GENERATION,
        runner.WORKBOOK_ACTION_MANUAL_REVIEW,
    ):
        assert s in runner.ALL_WORKBOOK_ACTIONS
    for s in (
        runner.SECONDARY_SOURCE_LOCAL_PRICE_CACHE,
        runner.SECONDARY_SOURCE_SIGNAL_CACHE,
        runner.SECONDARY_SOURCE_YFINANCE_REQUIRED,
        runner.SECONDARY_SOURCE_UNAVAILABLE,
    ):
        assert s in runner.ALL_SECONDARY_SOURCES
    for s in (
        runner.ELIGIBILITY_READY_TO_RUN_OFFLINE,
        runner.ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK,
        runner.ELIGIBILITY_BLOCKED,
    ):
        assert s in runner.ALL_ELIGIBILITIES
    for s in (
        runner.AUTH_CLASS_READ_ONLY,
        runner.AUTH_CLASS_IMPACTSEARCH_WORKBOOK_WRITE,
        runner.AUTH_CLASS_IMPACTSEARCH_NETWORK_WRITE,
        runner.AUTH_CLASS_MANUAL_REVIEW,
    ):
        assert s in runner.ALL_AUTH_CLASSES
    # 14 stable issue codes.
    assert len(runner.ALL_ISSUE_CODES) == 14


# ---------------------------------------------------------------------------
# 2. Rank-colmap drift guard against stackbuilder.py
# ---------------------------------------------------------------------------


def test_expected_rank_colmap_matches_stackbuilder():
    """``_STACKBUILDER_RANK_COLMAP_EXPECTED`` must match
    ``stackbuilder._RANK_COLMAP`` (catches future
    stackbuilder column drift)."""
    import stackbuilder
    assert (
        runner._STACKBUILDER_RANK_COLMAP_EXPECTED
        == stackbuilder._RANK_COLMAP
    )


def test_expected_rank_columns_match_required_pair():
    """Required-column pair (Primary Ticker + Total
    Capture (%)) appears in the expected column tuple."""
    cols = (
        runner
        .IMPACTSEARCH_WORKBOOK_RUNNER_EXPECTED_RANK_COLUMNS
    )
    assert "Primary Ticker" in cols
    assert "Total Capture (%)" in cols
    # The full canonical 8-column set must be a subset
    # of the stackbuilder colmap's standardized targets.
    targets = set(
        runner._STACKBUILDER_RANK_COLMAP_EXPECTED.values()
    )
    for col in cols:
        assert col in targets


# ---------------------------------------------------------------------------
# 3. Ticker safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ticker, ok",
    [
        ("SPY", True),
        ("AAPL", True),
        ("BRK-B", True),         # hyphen mid-name allowed (Phase 6I-52 universe)
        ("BRK.B", True),          # single dot mid-name allowed
        ("", False),
        ("   ", False),
        (None, False),
        ("../etc/passwd", False),
        ("..\\bad", False),
        ("foo/bar", False),
        ("foo\\bar", False),
        ("C:\\Windows", False),
        ("foo:bar", False),
        (".hidden", False),
        ("-flag", False),
        ("foo bar", False),
        ("foo\tbar", False),
        ("foo\nbar", False),
        ("foo*", False),
        ("foo?", False),
        ("foo<", False),
        ("foo|x", False),
        ("foo..bar", False),      # double-dot rejected
    ],
)
def test_is_safe_ticker_rejects_path_unsafe_inputs(
    ticker, ok,
):
    assert runner.is_safe_ticker(ticker) == ok


def test_is_safe_ticker_accepts_pure_alpha_uppercase():
    for s in ("SPY", "AAPL", "JNJ", "WMT", "HD", "MCD"):
        assert runner.is_safe_ticker(s)


# ---------------------------------------------------------------------------
# 4. Primary-universe resolution
# ---------------------------------------------------------------------------


def test_resolve_primary_universe_phase_6i_52_pilot():
    res = runner.resolve_primary_universe(
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        pilot_universe_loader=lambda: (
            "SPY", "AAPL", "JPM", "JPM", "WMT",
        ),
    )
    assert res["primary_source"] == (
        runner.PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
    )
    # Dedup + normalize.
    assert res["universe"] == ["SPY", "AAPL", "JPM", "WMT"]
    assert res["issue_codes"] == []


def test_resolve_primary_universe_explicit_csv():
    res = runner.resolve_primary_universe(
        primary_source=runner.PRIMARY_SOURCE_EXPLICIT_CSV,
        primary_csv="spy, aapl ,wmt,wmt",
    )
    assert res["universe"] == ["SPY", "AAPL", "WMT"]
    assert res["issue_codes"] == []


def test_resolve_primary_universe_explicit_csv_missing():
    res = runner.resolve_primary_universe(
        primary_source=runner.PRIMARY_SOURCE_EXPLICIT_CSV,
        primary_csv=None,
    )
    assert res["universe"] == []
    assert (
        runner.ISSUE_PRIMARY_CSV_REQUIRED_BUT_MISSING
        in res["issue_codes"]
    )


def test_resolve_primary_universe_explicit_csv_rejects_unsafe():
    res = runner.resolve_primary_universe(
        primary_source=runner.PRIMARY_SOURCE_EXPLICIT_CSV,
        primary_csv="SPY,../etc/passwd,AAPL",
    )
    assert res["universe"] == ["SPY", "AAPL"]
    assert (
        runner.ISSUE_PRIMARY_CSV_CONTAINS_UNSAFE_TICKER
        in res["issue_codes"]
    )


def test_resolve_primary_universe_signal_library_dir(
    tmp_path,
):
    sld = tmp_path / "sld"
    sld.mkdir()
    (sld / "FOO_stable_v0_5.pkl").write_bytes(b"")
    (sld / "BAR_stable_v1_0_0.pkl").write_bytes(b"")
    (sld / "irrelevant.txt").write_bytes(b"x")
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_SIGNAL_LIBRARY_DIR
        ),
        signal_lib_dir=str(sld),
    )
    assert sorted(res["universe"]) == ["BAR", "FOO"]
    assert res["issue_codes"] == []


def test_resolve_primary_universe_signal_library_dir_empty(
    tmp_path,
):
    sld = tmp_path / "empty_sld"
    sld.mkdir()
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_SIGNAL_LIBRARY_DIR
        ),
        signal_lib_dir=str(sld),
    )
    assert res["universe"] == []
    assert (
        runner.ISSUE_PRIMARY_UNIVERSE_EMPTY
        in res["issue_codes"]
    )


def test_resolve_primary_universe_unknown_source():
    with pytest.raises(ValueError):
        runner.resolve_primary_universe(
            primary_source="not_a_real_source",
        )


# ---------------------------------------------------------------------------
# 5. Secondary data-source classification
# ---------------------------------------------------------------------------


def test_secondary_data_source_yfinance_required_even_with_local_cache(
    tmp_path,
):
    pcd = tmp_path / "pcd"
    _make_price_cache_csv(pcd, "SPY")
    res = runner.classify_secondary_data_source(
        "SPY", price_cache_dir=str(pcd),
    )
    # Honest verdict: even with the local CSV present,
    # ImpactSearch goes to yfinance today.
    assert res["secondary_source"] == (
        runner.SECONDARY_SOURCE_YFINANCE_REQUIRED
    )
    assert res["local_price_cache_exists"] is True
    # And the note explains why.
    assert any(
        "yfinance" in n for n in res["notes"]
    )


def test_secondary_data_source_missing_local_cache(
    tmp_path,
):
    pcd = tmp_path / "pcd"
    pcd.mkdir()
    res = runner.classify_secondary_data_source(
        "JNJ", price_cache_dir=str(pcd),
    )
    assert res["secondary_source"] == (
        runner.SECONDARY_SOURCE_YFINANCE_REQUIRED
    )
    assert res["local_price_cache_exists"] is False


def test_secondary_data_source_unsafe_ticker():
    res = runner.classify_secondary_data_source(
        "../etc",
        price_cache_dir="price_cache/daily",
    )
    assert res["secondary_source"] == (
        runner.SECONDARY_SOURCE_UNAVAILABLE
    )


# ---------------------------------------------------------------------------
# 6. Workbook action classification
# ---------------------------------------------------------------------------


def test_workbook_action_missing(tmp_path):
    ixd = tmp_path / "ixd"
    ixd.mkdir()
    res = runner.classify_workbook_action(
        "SPY",
        output_dir=str(ixd),
        impact_xlsx_max_age_days=45,
    )
    assert res["workbook_action"] == (
        runner.WORKBOOK_ACTION_MISSING_NEEDS_GENERATION
    )
    assert res["workbook_path"] is None


def test_workbook_action_stale(tmp_path):
    ixd = tmp_path / "ixd"
    _make_workbook_file(
        ixd, "SPY", mtime_age_days=60.0,
    )
    res = runner.classify_workbook_action(
        "SPY",
        output_dir=str(ixd),
        impact_xlsx_max_age_days=45,
        now_seconds=time.time(),
    )
    assert res["workbook_action"] == (
        runner.WORKBOOK_ACTION_STALE_NEEDS_REGENERATION
    )
    assert res["age_days"] >= 60.0
    assert res["manifest_status"] == "not_inspected_stale"


def test_workbook_action_already_fresh_verified(tmp_path):
    ixd = tmp_path / "ixd"
    p = _make_workbook_file(
        ixd, "SPY", mtime_age_days=1.0,
    )
    loader = _make_loader_returning(
        {str(p): _good_rank_df()},
    )
    res = runner.classify_workbook_action(
        "SPY",
        output_dir=str(ixd),
        impact_xlsx_max_age_days=45,
        now_seconds=time.time(),
        verified_loader=loader,
    )
    assert res["workbook_action"] == (
        runner.WORKBOOK_ACTION_ALREADY_FRESH
    )
    assert res["manifest_status"] == "verified_ok"


def test_workbook_action_load_error(tmp_path):
    ixd = tmp_path / "ixd"
    p = _make_workbook_file(ixd, "SPY")

    def _broken_loader(path, *, strict=False):
        raise RuntimeError("synthetic load error")

    res = runner.classify_workbook_action(
        "SPY",
        output_dir=str(ixd),
        impact_xlsx_max_age_days=45,
        now_seconds=time.time(),
        verified_loader=_broken_loader,
    )
    assert res["workbook_action"] == (
        runner.WORKBOOK_ACTION_MANUAL_REVIEW
    )
    assert (
        runner.ISSUE_WORKBOOK_LOAD_ERROR
        in res["issue_codes"]
    )


def test_workbook_action_strict_legacy_rejected(tmp_path):
    ixd = tmp_path / "ixd"
    p = _make_workbook_file(ixd, "SPY")
    loader = _make_loader_returning(
        {str(p): _good_rank_df()},
        result_per_path={
            str(p): _FakeManifestResult(
                ok=True, legacy=True,
            ),
        },
    )
    res = runner.classify_workbook_action(
        "SPY",
        output_dir=str(ixd),
        impact_xlsx_max_age_days=45,
        strict_manifests=True,
        verified_loader=loader,
    )
    assert res["workbook_action"] == (
        runner.WORKBOOK_ACTION_MANUAL_REVIEW
    )
    assert (
        runner.ISSUE_WORKBOOK_MANIFEST_REJECTED
        in res["issue_codes"]
    )


def test_workbook_action_manifest_mismatch_rejected(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    p = _make_workbook_file(ixd, "SPY")
    loader = _make_loader_returning(
        {str(p): _good_rank_df()},
        result_per_path={
            str(p): _FakeManifestResult(
                ok=False, legacy=False,
                mismatches=[("hash", "x", "y")],
            ),
        },
    )
    res = runner.classify_workbook_action(
        "SPY",
        output_dir=str(ixd),
        impact_xlsx_max_age_days=45,
        verified_loader=loader,
    )
    assert res["workbook_action"] == (
        runner.WORKBOOK_ACTION_MANUAL_REVIEW
    )
    assert (
        runner.ISSUE_WORKBOOK_MANIFEST_REJECTED
        in res["issue_codes"]
    )


# ---------------------------------------------------------------------------
# 7. Eligibility classifier
# ---------------------------------------------------------------------------


def test_eligibility_unsafe_ticker_blocked():
    res = runner.classify_eligibility(
        secondary="../etc",
        workbook_action=(
            runner.WORKBOOK_ACTION_MISSING_NEEDS_GENERATION
        ),
        secondary_source=(
            runner.SECONDARY_SOURCE_YFINANCE_REQUIRED
        ),
        primary_universe_size=10,
        write_requested=True,
        network_fetch_authorized=True,
        issue_codes_so_far=[],
    )
    assert res["eligibility"] == (
        runner.ELIGIBILITY_BLOCKED
    )
    assert runner.ISSUE_UNSAFE_TICKER in res["issue_codes"]


def test_eligibility_empty_primary_universe_blocked():
    res = runner.classify_eligibility(
        secondary="SPY",
        workbook_action=(
            runner.WORKBOOK_ACTION_MISSING_NEEDS_GENERATION
        ),
        secondary_source=(
            runner.SECONDARY_SOURCE_YFINANCE_REQUIRED
        ),
        primary_universe_size=0,
        write_requested=True,
        network_fetch_authorized=True,
        issue_codes_so_far=[],
    )
    assert res["eligibility"] == (
        runner.ELIGIBILITY_BLOCKED
    )


def test_eligibility_already_fresh_blocked():
    res = runner.classify_eligibility(
        secondary="SPY",
        workbook_action=(
            runner.WORKBOOK_ACTION_ALREADY_FRESH
        ),
        secondary_source=(
            runner.SECONDARY_SOURCE_YFINANCE_REQUIRED
        ),
        primary_universe_size=10,
        write_requested=True,
        network_fetch_authorized=True,
        issue_codes_so_far=[],
    )
    assert res["eligibility"] == (
        runner.ELIGIBILITY_BLOCKED
    )
    assert (
        runner.ISSUE_WORKBOOK_ALREADY_FRESH_NO_ACTION
        in res["issue_codes"]
    )


def test_eligibility_yfinance_required_write_without_network_blocked():
    res = runner.classify_eligibility(
        secondary="SPY",
        workbook_action=(
            runner.WORKBOOK_ACTION_MISSING_NEEDS_GENERATION
        ),
        secondary_source=(
            runner.SECONDARY_SOURCE_YFINANCE_REQUIRED
        ),
        primary_universe_size=10,
        write_requested=True,
        network_fetch_authorized=False,
        issue_codes_so_far=[],
    )
    assert res["eligibility"] == (
        runner.ELIGIBILITY_BLOCKED
    )
    assert (
        runner.ISSUE_NETWORK_FETCH_REQUIRED_BUT_NOT_AUTHORIZED
        in res["issue_codes"]
    )


def test_eligibility_yfinance_required_dry_run_classifies_explicit_network():
    res = runner.classify_eligibility(
        secondary="SPY",
        workbook_action=(
            runner.WORKBOOK_ACTION_MISSING_NEEDS_GENERATION
        ),
        secondary_source=(
            runner.SECONDARY_SOURCE_YFINANCE_REQUIRED
        ),
        primary_universe_size=10,
        write_requested=False,
        network_fetch_authorized=False,
        issue_codes_so_far=[],
    )
    assert res["eligibility"] == (
        runner.ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK
    )


def test_eligibility_yfinance_required_both_flags_classifies_explicit_network():
    res = runner.classify_eligibility(
        secondary="SPY",
        workbook_action=(
            runner.WORKBOOK_ACTION_MISSING_NEEDS_GENERATION
        ),
        secondary_source=(
            runner.SECONDARY_SOURCE_YFINANCE_REQUIRED
        ),
        primary_universe_size=10,
        write_requested=True,
        network_fetch_authorized=True,
        issue_codes_so_far=[],
    )
    assert res["eligibility"] == (
        runner.ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK
    )


# ---------------------------------------------------------------------------
# 8. Plan builder — dry-run and unsafe handling
# ---------------------------------------------------------------------------


def test_build_plan_dry_run_does_not_write(tmp_path):
    ixd = tmp_path / "ixd"
    sld = tmp_path / "sld"
    pcd = tmp_path / "pcd"
    # NB: do NOT pre-create ixd; the plan must not create it.
    pcd.mkdir()
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY", "AAPL"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(ixd),
        signal_lib_dir=str(sld),
        price_cache_dir=str(pcd),
        pilot_universe_loader=lambda: (
            "SPY", "AAPL", "JPM",
        ),
    )
    assert plan["schema_version"] == (
        "impactsearch_workbook_runner_v1"
    )
    assert plan["pinned_interpreter"] == (
        runner.PINNED_INTERPRETER
    )
    assert plan["policy"]["write_requested"] is False
    assert (
        plan["policy"]["network_fetch_authorized"]
        is False
    )
    assert plan["summary"]["primary_universe_size"] == 3
    assert len(plan["per_ticker"]) == 2
    # CRITICAL: dry-run did not create the output dir.
    assert not ixd.exists()


def test_build_plan_unsafe_ticker_does_not_create_output_dir(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["../etc", "SPY"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(ixd),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    unsafe = [
        row for row in plan["per_ticker"]
        if row["requested_secondary"] == "../etc"
    ][0]
    assert unsafe["eligibility"] == (
        runner.ELIGIBILITY_BLOCKED
    )
    assert (
        runner.ISSUE_UNSAFE_TICKER
        in unsafe["issue_codes"]
    )
    assert not ixd.exists()


def test_build_plan_per_ticker_carries_canonical_output_path(
    tmp_path,
):
    ixd = tmp_path / "ixd"
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["spy"],  # lowercase to verify normalization
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(ixd),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    row = plan["per_ticker"][0]
    assert row["normalized_secondary"] == "SPY"
    assert row["output_path"].endswith(
        "SPY_analysis.xlsx"
    )


def test_build_plan_summary_counts_consistent(tmp_path):
    ixd = tmp_path / "ixd"
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY", "AAPL", "../etc"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(ixd),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    counts = plan["summary"]["eligibility_counts"]
    assert sum(counts.values()) == 3


# ---------------------------------------------------------------------------
# 9. Command manifest emitter
# ---------------------------------------------------------------------------


def test_command_manifest_uses_pinned_interpreter(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    manifest = runner.build_command_manifest(plan)
    assert manifest["pinned_interpreter"] == (
        runner.PINNED_INTERPRETER
    )
    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert entry["secondary"] == "SPY"
    assert entry["argv"][0] == runner.PINNED_INTERPRETER
    assert (
        entry["argv"][1]
        == "impactsearch_workbook_runner.py"
    )
    # Per-ticker --secondaries SPY (not the whole list).
    assert "--secondaries" in entry["argv"]
    assert entry["argv"][
        entry["argv"].index("--secondaries") + 1
    ] == "SPY"


def test_command_manifest_blocked_has_null_argv(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["../etc"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY",),
    )
    manifest = runner.build_command_manifest(plan)
    entry = manifest["entries"][0]
    assert entry["argv"] is None
    assert entry["authorization_class"] == (
        runner.AUTH_CLASS_MANUAL_REVIEW
    )
    assert (
        entry["requires_separate_operator_authorization"]
        is True
    )


def test_command_manifest_explicit_network_class_includes_both_flags(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    # Dry-run plan -> per-ticker eligibility is
    # READY_TO_RUN_WITH_EXPLICIT_NETWORK; the emitted
    # command for it is the *full* network_write
    # command (since that's the next-step command the
    # operator must ultimately run).
    manifest = runner.build_command_manifest(plan)
    entry = manifest["entries"][0]
    assert entry["authorization_class"] == (
        runner.AUTH_CLASS_IMPACTSEARCH_NETWORK_WRITE
    )
    assert "--write" in entry["argv"]
    assert "--allow-network-fetch" in entry["argv"]


def test_command_manifest_summary_counts_correct(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY", "AAPL", "../etc"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    manifest = runner.build_command_manifest(plan)
    by_class = manifest["summary"]["by_authorization_class"]
    assert (
        by_class[runner.AUTH_CLASS_MANUAL_REVIEW] == 1
    )
    assert (
        by_class[
            runner.AUTH_CLASS_IMPACTSEARCH_NETWORK_WRITE
        ]
        == 2
    )


# ---------------------------------------------------------------------------
# 10. Authorized execution path
# ---------------------------------------------------------------------------


def _authorized_plan(
    tmp_path,
) -> tuple[dict[str, Any], Path]:
    ixd = tmp_path / "ixd"
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(ixd),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        write=True,
        allow_network_fetch=True,
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    return plan, ixd


def test_execute_workbook_run_refuses_without_write(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        write=False,
        allow_network_fetch=True,
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    res = runner.execute_workbook_run(plan)
    assert res["status"] == "refused"
    assert "write_requested=False" in res["reason"]


def test_execute_workbook_run_refuses_without_network(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        write=True,
        allow_network_fetch=False,
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
    )
    res = runner.execute_workbook_run(plan)
    assert res["status"] == "refused"
    assert (
        "network_fetch_authorized=False" in res["reason"]
    )


def test_execute_workbook_run_with_fake_callable_writes_to_tmp(
    tmp_path,
):
    plan, ixd = _authorized_plan(tmp_path)
    # Confirm the per-ticker eligibility is exactly the
    # value the executor expects.
    row = plan["per_ticker"][0]
    assert row["eligibility"] == (
        runner.ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK
    )

    calls: list[dict[str, Any]] = []

    def _fake_callable(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
    ):
        calls.append(
            {
                "secondary": secondary,
                "primary_tickers": list(primary_tickers),
                "output_path": output_path,
            }
        )

        def _fake_export(
            partial_path, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            # Mirror impactsearch's effect: write the XLSX
            # bytes (placeholder) and a sidecar manifest.
            Path(partial_path).write_bytes(b"XLSX-OK")
            Path(
                partial_path + ".manifest.json"
            ).write_text(
                json.dumps(
                    {
                        "validation_summary": dict(
                            validation_summary
                            or {}
                        ),
                    },
                ),
                encoding="utf-8",
            )

        atomic = export_atomic(
            output_path,
            [{"Primary Ticker": "AAPL"}],
            validation_summary={
                "validation_status": "ok",
            },
            per_strategy_validation={},
            export_callable=_fake_export,
        )
        return {
            "status": "ok",
            "metrics_count": 1,
            "validation_sidecar_path": str(
                atomic["canonical_sidecar"]
            ),
            "validation_status": "ok",
            "canonical_path": atomic["canonical_path"],
            "canonical_sidecar": atomic[
                "canonical_sidecar"
            ],
        }

    res = runner.execute_workbook_run(
        plan,
        impactsearch_callable_override=_fake_callable,
    )
    assert res["status"] == "ok"
    assert len(calls) == 1
    final_xlsx = ixd / "SPY_analysis.xlsx"
    final_sidecar = (
        ixd / "SPY_analysis.xlsx.manifest.json"
    )
    assert final_xlsx.exists()
    assert final_sidecar.exists()
    # No partial files left behind.
    partials = list(ixd.glob("*.runner_partial.xlsx*"))
    assert partials == []
    # And the runner did NOT touch any other directory:
    # the tmp_path tree only contains the ixd entries.
    other_files = [
        p for p in tmp_path.rglob("*")
        if p.is_file() and p.parent != ixd
    ]
    assert other_files == []


def test_execute_workbook_run_fake_callable_failure_cleans_up_partials(
    tmp_path,
):
    plan, ixd = _authorized_plan(tmp_path)

    def _failing_callable(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
    ):
        def _fake_export(
            partial_path, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            Path(partial_path).write_bytes(
                b"PARTIAL-XLSX",
            )
            Path(
                partial_path + ".manifest.json"
            ).write_bytes(b"{}")
            raise RuntimeError(
                "synthetic export failure"
            )

        try:
            export_atomic(
                output_path,
                [{"Primary Ticker": "AAPL"}],
                validation_summary={
                    "validation_status": "ok",
                },
                per_strategy_validation={},
                export_callable=_fake_export,
            )
        except RuntimeError as exc:
            return {
                "status": "failed",
                "reason": str(exc),
            }
        return {"status": "ok"}

    res = runner.execute_workbook_run(
        plan,
        impactsearch_callable_override=(
            _failing_callable
        ),
    )
    assert res["status"] in {"no_op", "partial"}
    # Partial XLSX + partial sidecar both cleaned up.
    leftovers = list(ixd.glob("*"))
    # Canonical names absent.
    assert not (ixd / "SPY_analysis.xlsx").exists()
    assert not (
        ixd / "SPY_analysis.xlsx.manifest.json"
    ).exists()
    # No runner_partial leftovers.
    runner_partial_left = [
        p for p in leftovers
        if "runner_partial" in p.name
    ]
    assert runner_partial_left == []


# ---------------------------------------------------------------------------
# 11. _atomic_export_workbook unit tests
# ---------------------------------------------------------------------------


def test_atomic_export_workbook_renames_on_success(
    tmp_path,
):
    out_path = tmp_path / "ixd" / "SPY_analysis.xlsx"
    out_path.parent.mkdir(parents=True)

    def _ok_export(
        partial, metrics, *,
        validation_summary,
        per_strategy_validation,
    ):
        Path(partial).write_bytes(b"X")
        Path(partial + ".manifest.json").write_bytes(
            b"{}"
        )

    res = runner._atomic_export_workbook(
        str(out_path),
        [],
        validation_summary={},
        per_strategy_validation={},
        export_callable=_ok_export,
    )
    assert out_path.exists()
    assert (
        out_path.parent / "SPY_analysis.xlsx.manifest.json"
    ).exists()
    assert res["atomic"] is True
    # No partials left.
    leftover = [
        p
        for p in out_path.parent.glob("*")
        if "runner_partial" in p.name
    ]
    assert leftover == []


def test_atomic_export_workbook_cleans_partials_on_failure(
    tmp_path,
):
    out_path = tmp_path / "ixd" / "SPY_analysis.xlsx"
    out_path.parent.mkdir(parents=True)

    def _failing_export(
        partial, metrics, *,
        validation_summary,
        per_strategy_validation,
    ):
        Path(partial).write_bytes(b"PARTIAL")
        Path(partial + ".manifest.json").write_bytes(
            b"{}"
        )
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        runner._atomic_export_workbook(
            str(out_path),
            [],
            validation_summary={},
            per_strategy_validation={},
            export_callable=_failing_export,
        )
    # Canonical absent.
    assert not out_path.exists()
    assert not (
        out_path.parent
        / "SPY_analysis.xlsx.manifest.json"
    ).exists()
    # Partials absent.
    leftover = list(out_path.parent.glob("*"))
    assert leftover == []


def test_atomic_export_workbook_rejects_non_xlsx_path():
    with pytest.raises(ValueError):
        runner._atomic_export_workbook(
            "/tmp/not_an_xlsx.txt",
            [],
            validation_summary={},
            per_strategy_validation={},
            export_callable=lambda *a, **k: None,
        )


# ---------------------------------------------------------------------------
# 12. CLI smoke + production-root guard
# ---------------------------------------------------------------------------


def test_main_dry_run_returns_zero(
    tmp_path, capsys,
):
    rc = runner.main(
        [
            "--secondaries", "SPY",
            "--primary-source",
            runner.PRIMARY_SOURCE_EXPLICIT_CSV,
            "--primaries", "AAPL,JPM",
            "--output-dir", str(tmp_path / "ixd"),
            "--signal-library-dir", str(tmp_path / "sld"),
            "--price-cache-dir", str(tmp_path / "pcd"),
        ],
    )
    assert rc == 0
    # No files written anywhere by default.
    assert list(tmp_path.rglob("*")) == []


def test_main_emit_command_manifest_outside_production_roots(
    tmp_path,
):
    manifest_path = tmp_path / "outside" / "manifest.json"
    rc = runner.main(
        [
            "--secondaries", "SPY",
            "--primary-source",
            runner.PRIMARY_SOURCE_EXPLICIT_CSV,
            "--primaries", "AAPL",
            "--output-dir", str(tmp_path / "ixd"),
            "--emit-command-manifest", str(manifest_path),
        ],
    )
    assert rc == 0
    body = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )
    assert (
        body["pinned_interpreter"]
        == runner.PINNED_INTERPRETER
    )


@pytest.mark.parametrize(
    "forbidden",
    [
        "cache/results/x.json",
        "cache/status/x.json",
        "output/research_artifacts/x.json",
        "output/stackbuilder/x.json",
        "signal_library/data/stable/x.json",
        "price_cache/daily/x.json",
        "output/impactsearch/x.json",
    ],
)
def test_main_emit_command_manifest_refuses_production_root(
    forbidden,
):
    with pytest.raises(PermissionError):
        runner.main(
            [
                "--secondaries", "SPY",
                "--primary-source",
                runner.PRIMARY_SOURCE_EXPLICIT_CSV,
                "--primaries", "AAPL",
                "--emit-command-manifest", forbidden,
            ],
        )


@pytest.mark.parametrize(
    "forbidden",
    [
        "cache/results/p.json",
        "cache/status/p.json",
        "output/research_artifacts/p.json",
        "output/stackbuilder/p.json",
        "signal_library/data/stable/p.json",
        "price_cache/daily/p.json",
        "output/impactsearch/p.json",
    ],
)
def test_main_output_refuses_production_root(
    forbidden,
):
    with pytest.raises(PermissionError):
        runner.main(
            [
                "--secondaries", "SPY",
                "--primary-source",
                runner.PRIMARY_SOURCE_EXPLICIT_CSV,
                "--primaries", "AAPL",
                "--output", forbidden,
            ],
        )


# ---------------------------------------------------------------------------
# 13. AST guards
# ---------------------------------------------------------------------------


_FORBIDDEN_TOP_LEVEL_IMPORTS = frozenset({
    "yfinance",
    "subprocess",
    "dash",
    "dash_bootstrap_components",
    "plotly",
    "impactsearch",
    "stackbuilder",
    "onepass",
    "trafficflow",
    "trafficflow_k_artifact_builder",
    "trafficflow_multitimeframe_bridge",
    "spymaster",
    "confluence",
    "confluence_pipeline_runner",
    "confluence_mtf_artifact_builder",
    "multiwindow_k_confluence_patch_writer",
    "signal_engine_cache_refresher",
    "signal_library_stable_promotion_writer",
    "daily_board_automation_writer",
    "pickle",
})


def _runner_source() -> str:
    here = Path(__file__).resolve().parent.parent
    return (
        here / "impactsearch_workbook_runner.py"
    ).read_text(encoding="utf-8")


def test_no_forbidden_top_level_imports():
    tree = ast.parse(_runner_source())
    top_level: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for n in node.names:
                top_level.add(
                    n.name.split(".")[0],
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level.add(
                    node.module.split(".")[0],
                )
    leaked = (
        top_level & _FORBIDDEN_TOP_LEVEL_IMPORTS
    )
    assert not leaked, (
        f"Forbidden top-level imports in runner: "
        f"{sorted(leaked)}"
    )


def test_no_raw_pickle_load_or_yfinance_call_in_source():
    """No ``pickle.load(...)``, ``pickle_load_compat(...)``,
    ``yf.download(...)``, or ``subprocess.run(...)`` call
    anywhere in the runner source."""
    tree = ast.parse(_runner_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                base = fn.value
                if isinstance(base, ast.Name):
                    if base.id == "pickle" and fn.attr == "load":
                        raise AssertionError(
                            "unexpected pickle.load(...) "
                            "call in runner"
                        )
                    if base.id == "yf" and fn.attr in {
                        "download", "Ticker", "Tickers",
                    }:
                        raise AssertionError(
                            f"unexpected yf.{fn.attr}(...) "
                            "call in runner"
                        )
                    if (
                        base.id == "subprocess"
                        and fn.attr in {
                            "run", "Popen", "call",
                            "check_output",
                        }
                    ):
                        raise AssertionError(
                            f"unexpected subprocess."
                            f"{fn.attr}(...) "
                            "call in runner"
                        )
            if isinstance(fn, ast.Name):
                assert fn.id != "pickle_load_compat"


# ---------------------------------------------------------------------------
# 14. Production-state smoke
# ---------------------------------------------------------------------------


def test_production_state_smoke_skips_when_output_impactsearch_dir_absent():
    """Skips when ``output/impactsearch`` is absent in a
    clean Codex worktree; otherwise asserts the plan
    schema is the canonical one and that we can build a
    manifest. Does NOT assert specific verdicts because
    those depend on operator state."""
    here = Path(__file__).resolve().parent.parent
    ixd = here / "output" / "impactsearch"
    pcd = here / "price_cache" / "daily"
    if not ixd.exists() or not pcd.exists():
        pytest.skip(
            "output/impactsearch or price_cache/daily "
            "absent; production smoke skipped (fixture "
            "tests above pin the cascade)."
        )
    # Use the default pilot-universe loader (no override).
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=list(runner.DEFAULT_SECONDARIES),
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(ixd),
        signal_lib_dir=str(
            here / "signal_library" / "data" / "stable"
        ),
        price_cache_dir=str(pcd),
    )
    assert plan["schema_version"] == (
        "impactsearch_workbook_runner_v1"
    )
    assert len(plan["per_ticker"]) == 6
    manifest = runner.build_command_manifest(plan)
    assert manifest["summary"]["total"] == 6


# ---------------------------------------------------------------------------
# 15. Evidence-doc precision-wording guard
# ---------------------------------------------------------------------------


def test_evidence_doc_carries_runner_precision_wording():
    """Pin the Section 7 + chain-section wording so any
    future edit to the evidence doc surfaces in
    review."""
    here = Path(__file__).resolve().parent.parent
    doc = (
        here / "md_library" / "shared" / (
            "2026-05-15_PHASE_6I56_IMPACTSEARCH_"
            "WORKBOOK_EXECUTION_SURFACE.md"
        )
    )
    assert doc.exists(), (
        f"6I-56 evidence doc missing at {doc}"
    )
    body = doc.read_text(encoding="utf-8")
    required = (
        "impactsearch_workbook_runner.py",
        "process_primary_tickers",
        "_prepare_impactsearch_durable_validation_for_export",
        "export_results_to_excel",
        "try_load_rank_from_impact_xlsx",
        "yfinance_required",
        "--write",
        "--allow-network-fetch",
        "ready_for_stackbuilder_with_impact_xlsx",
        "OnePass / signal_library",
        "TrafficFlow K artifacts",
        "Confluence MTF artifact",
        "website board",
        "Files added (4)",
        "ImpactSearch",
        "StackBuilder",
        "active core scripts",
    )
    missing = [
        phrase for phrase in required
        if phrase not in body
    ]
    assert not missing, (
        f"6I-56 evidence doc missing required wording: "
        f"{missing!r}"
    )
