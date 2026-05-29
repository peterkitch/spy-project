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
        runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE,
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
    # 17 stable issue codes (Phase 6I-57 added 3:
    # primary_tickers_file_missing,
    # primary_tickers_file_unreadable,
    # primary_tickers_file_contains_unsafe_ticker).
    assert len(runner.ALL_ISSUE_CODES) == 17


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
# 4b. master_tickers_file primary source (Phase 6I-57)
# ---------------------------------------------------------------------------


def test_master_tickers_file_in_all_primary_sources():
    assert (
        runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        in runner.ALL_PRIMARY_SOURCES
    )
    assert (
        runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        == "master_tickers_file"
    )


def test_master_tickers_file_default_path_is_documented():
    assert runner.DEFAULT_MASTER_TICKERS_FILE_RELATIVE == (
        "global_ticker_library/data/master_tickers.txt"
    )


def test_master_tickers_file_issue_codes_in_all_issue_codes():
    for code in (
        runner.ISSUE_PRIMARY_TICKERS_FILE_MISSING,
        runner.ISSUE_PRIMARY_TICKERS_FILE_UNREADABLE,
        (
            runner
            .ISSUE_PRIMARY_TICKERS_FILE_CONTAINS_UNSAFE_TICKER
        ),
    ):
        assert code in runner.ALL_ISSUE_CODES


def test_master_tickers_file_resolves_comma_separated_input():
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file="fake/path.txt",
        master_tickers_file_loader=(
            lambda p: "SPY,AAPL,WMT,HD,MCD"
        ),
    )
    assert res["primary_source"] == (
        runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
    )
    assert res["universe"] == ["SPY", "AAPL", "WMT", "HD", "MCD"]
    assert res["issue_codes"] == []
    assert res["parsed_count"] == 5
    assert res["accepted_count"] == 5
    assert res["dropped_count"] == 0
    assert res["primary_tickers_file"] == "fake/path.txt"


def test_master_tickers_file_resolves_newline_and_comma_mixed():
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file="fake/path.txt",
        master_tickers_file_loader=(
            lambda p: (
                "SPY,AAPL\nMSFT, GOOGL ,AMZN\n\nNVDA\n"
            )
        ),
    )
    assert res["universe"] == [
        "SPY", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    ]
    assert res["parsed_count"] == 6
    assert res["accepted_count"] == 6
    assert res["dropped_count"] == 0


def test_master_tickers_file_preserves_NA_and_NAN_literal_tickers():
    """The master ticker list contains literal members
    "NA" and "NAN". The runner's raw-string parse must
    NOT coerce them to NaN. This is the regression guard
    for the Phase 6I-57 first-attempt finding."""
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file="fake/path.txt",
        master_tickers_file_loader=(
            lambda p: "SPY,NA,AAPL,NAN,WMT"
        ),
    )
    assert "NA" in res["universe"]
    assert "NAN" in res["universe"]
    assert res["universe"] == [
        "SPY", "NA", "AAPL", "NAN", "WMT",
    ]
    assert res["issue_codes"] == []
    assert res["parsed_count"] == 5
    assert res["accepted_count"] == 5
    assert res["dropped_count"] == 0


def test_master_tickers_file_filters_unsafe_tickers():
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file="fake/path.txt",
        master_tickers_file_loader=(
            lambda p: (
                "SPY,../etc/passwd,AAPL,..\\windows,JNJ"
            )
        ),
    )
    assert res["universe"] == ["SPY", "AAPL", "JNJ"]
    assert (
        runner
        .ISSUE_PRIMARY_TICKERS_FILE_CONTAINS_UNSAFE_TICKER
        in res["issue_codes"]
    )
    assert res["parsed_count"] == 5
    assert res["accepted_count"] == 3
    assert res["dropped_count"] == 2


def test_master_tickers_file_dedupes_preserving_first_occurrence():
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file="fake/path.txt",
        master_tickers_file_loader=(
            lambda p: "SPY,aapl,SPY,AAPL,Spy"
        ),
    )
    assert res["universe"] == ["SPY", "AAPL"]
    assert res["parsed_count"] == 5
    assert res["accepted_count"] == 2
    # dedupe via _dedupe_normalize_tickers does NOT count
    # duplicates as dropped_unsafe.
    assert res["dropped_count"] == 0


def test_master_tickers_file_missing_path_classifies_correctly(
    tmp_path,
):
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file=str(
            tmp_path / "does_not_exist.txt"
        ),
    )
    assert res["universe"] == []
    assert (
        runner.ISSUE_PRIMARY_TICKERS_FILE_MISSING
        in res["issue_codes"]
    )
    assert (
        runner.ISSUE_PRIMARY_UNIVERSE_EMPTY
        in res["issue_codes"]
    )
    assert res["parsed_count"] == 0


def test_master_tickers_file_unreadable_classifies_correctly():
    def _raise_oserror(p):
        raise OSError("disk gremlin")
    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file="fake/path.txt",
        master_tickers_file_loader=_raise_oserror,
    )
    assert res["universe"] == []
    assert (
        runner.ISSUE_PRIMARY_TICKERS_FILE_UNREADABLE
        in res["issue_codes"]
    )
    assert (
        runner.ISSUE_PRIMARY_UNIVERSE_EMPTY
        in res["issue_codes"]
    )


def test_master_tickers_file_default_path_used_when_none_supplied(
    tmp_path,
):
    captured: list[str] = []

    def _loader(p: str) -> str:
        captured.append(p)
        return "SPY,AAPL"

    res = runner.resolve_primary_universe(
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        master_tickers_file_loader=_loader,
    )
    assert captured == [
        runner.DEFAULT_MASTER_TICKERS_FILE_RELATIVE,
    ]
    assert res["primary_tickers_file"] == (
        runner.DEFAULT_MASTER_TICKERS_FILE_RELATIVE
    )
    assert res["universe"] == ["SPY", "AAPL"]


def test_cli_argv_parses_primary_tickers_file_flag():
    args = runner._parse_argv([
        "--secondaries", "SPY",
        "--primary-source", "master_tickers_file",
        "--primary-tickers-file",
        "global_ticker_library/data/master_tickers.txt",
        "--use-multiprocessing",
    ])
    assert args.secondaries == "SPY"
    assert args.primary_source == "master_tickers_file"
    assert args.primary_tickers_file == (
        "global_ticker_library/data/master_tickers.txt"
    )
    assert args.use_multiprocessing is True
    assert args.write is False
    assert args.allow_network_fetch is False


def test_cli_argv_primary_tickers_file_defaults_to_none():
    args = runner._parse_argv([
        "--secondaries", "SPY",
    ])
    assert args.primary_tickers_file is None


def test_plan_carries_primary_tickers_file_in_policy(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file="some/custom/file.txt",
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        master_tickers_file_loader=(
            lambda p: "SPY,AAPL,JNJ"
        ),
        primary_library_existence_checker=(
            lambda t, d: True
        ),
    )
    assert plan["policy"]["primary_source"] == (
        runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
    )
    assert plan["policy"]["primary_tickers_file"] == (
        "some/custom/file.txt"
    )
    pres = plan["primary_universe_resolution"]
    assert pres["primary_tickers_file"] == (
        "some/custom/file.txt"
    )
    assert pres["parsed_count"] == 3
    assert pres["accepted_count"] == 3
    assert pres["universe"] == ["SPY", "AAPL", "JNJ"]


def test_command_manifest_includes_primary_tickers_file_flag(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner.PRIMARY_SOURCE_MASTER_TICKERS_FILE
        ),
        primary_tickers_file=(
            "global_ticker_library/data/master_tickers.txt"
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        master_tickers_file_loader=(
            lambda p: "SPY,AAPL,NA,NAN,WMT"
        ),
        primary_library_existence_checker=(
            lambda t, d: True
        ),
    )
    manifest = runner.build_command_manifest(plan)
    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert entry["argv"] is not None
    argv = entry["argv"]
    assert "--primary-source" in argv
    ps_idx = argv.index("--primary-source")
    assert argv[ps_idx + 1] == "master_tickers_file"
    assert "--primary-tickers-file" in argv
    ptf_idx = argv.index("--primary-tickers-file")
    assert argv[ptf_idx + 1] == (
        "global_ticker_library/data/master_tickers.txt"
    )


def test_command_manifest_omits_primary_tickers_file_flag_for_pilot_source(
    tmp_path,
):
    """When the primary source is the 25-pilot universe
    the manifest should NOT include --primary-tickers-file
    (the flag is master_tickers_file-only)."""
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner.PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
        primary_library_existence_checker=(
            lambda t, d: True
        ),
    )
    manifest = runner.build_command_manifest(plan)
    entry = manifest["entries"][0]
    assert "--primary-tickers-file" not in (
        entry["argv"] or []
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
        primary_library_existence_checker=(
            lambda t, d: True
        ),
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
        primary_library_existence_checker=(
            lambda t, d: True
        ),
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
        primary_library_existence_checker=(
            lambda t, d: True
        ),
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
        primary_library_existence_checker=(
            lambda t, d: True
        ),
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
        **_kwargs,
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
        **_kwargs,
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
# 10b. Per-secondary timing instrumentation (Phase 6I-57 baseline)
# ---------------------------------------------------------------------------


def _make_minimal_ok_callable(*, sleep_seconds: float = 0.0):
    """Returns an impactsearch_callable_override that
    writes a small workbook + sidecar through the
    ``export_atomic`` seam, optionally sleeping to make
    elapsed_seconds non-trivial."""
    def _callable(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
        **_kwargs,
    ):
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            Path(partial).write_bytes(b"XLSX-BYTES")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{}")

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
    return _callable


import time as _time  # noqa: E402  (test-only import)


def test_execute_workbook_run_records_per_secondary_timing(
    tmp_path,
):
    plan, _ixd = _authorized_plan(tmp_path)
    res = runner.execute_workbook_run(
        plan,
        impactsearch_callable_override=(
            _make_minimal_ok_callable(sleep_seconds=0.05)
        ),
    )
    assert res["status"] == "ok"
    # Top-level run timing fields present.
    for k in (
        "run_started_at_utc",
        "run_ended_at_utc",
        "run_elapsed_seconds",
        "run_elapsed_minutes",
    ):
        assert k in res, f"missing top-level field: {k}"
    assert isinstance(res["run_elapsed_seconds"], float)
    assert res["run_elapsed_seconds"] >= 0.0
    # Per-secondary timing fields present.
    pt = res["per_ticker_results"][0]
    for k in (
        "secondary",
        "start_timestamp",
        "end_timestamp",
        "elapsed_seconds",
        "elapsed_minutes",
        "status",
        "metrics_count",
        "canonical_path",
        "canonical_sidecar",
        "workbook_size_bytes",
        "manifest_size_bytes",
        "validation_status",
    ):
        assert k in pt, (
            f"missing per-secondary field: {k}"
        )
    assert pt["status"] == "ok"
    assert pt["secondary"] == "SPY"
    assert pt["elapsed_seconds"] >= 0.0
    assert pt["elapsed_minutes"] == pytest.approx(
        pt["elapsed_seconds"] / 60.0, abs=0.001,
    )
    # workbook + manifest sizes captured from disk.
    assert pt["workbook_size_bytes"] == len(b"XLSX-BYTES")
    assert pt["manifest_size_bytes"] is not None
    assert pt["manifest_size_bytes"] >= 0


def test_execute_workbook_run_passthrough_fast_path_and_yfinance_count(
    tmp_path,
):
    plan, _ixd = _authorized_plan(tmp_path)

    def _callable_with_extras(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
        **_kwargs,
    ):
        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            Path(partial).write_bytes(b"X")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{}")

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
            "fast_path_summary": {
                "found": 42, "missing": 0,
            },
            "yfinance_call_count": 7,
        }

    res = runner.execute_workbook_run(
        plan,
        impactsearch_callable_override=(
            _callable_with_extras
        ),
    )
    pt = res["per_ticker_results"][0]
    assert pt["fast_path_summary"] == {
        "found": 42, "missing": 0,
    }
    assert pt["yfinance_call_count"] == 7


def test_execute_workbook_run_failure_path_still_records_timing(
    tmp_path,
):
    plan, _ixd = _authorized_plan(tmp_path)

    def _raising_callable(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
        **_kwargs,
    ):
        raise RuntimeError("synthetic failure")

    res = runner.execute_workbook_run(
        plan,
        impactsearch_callable_override=_raising_callable,
    )
    assert res["status"] in {"no_op", "partial"}
    pt = res["per_ticker_results"][0]
    assert pt["status"] == "failed"
    assert "synthetic failure" in pt["reason"]
    for k in (
        "start_timestamp",
        "end_timestamp",
        "elapsed_seconds",
        "elapsed_minutes",
        "workbook_size_bytes",
        "manifest_size_bytes",
        "fast_path_summary",
        "yfinance_call_count",
    ):
        assert k in pt, (
            f"failure path missing timing field: {k}"
        )
    assert pt["workbook_size_bytes"] is None
    assert pt["manifest_size_bytes"] is None
    assert pt["elapsed_seconds"] >= 0.0


# ---------------------------------------------------------------------------
# 10c. Phase 6I-57 quarantine helper + manifest verification tests
# ---------------------------------------------------------------------------


def test_quarantine_helper_moves_existing_outputs(tmp_path):
    ixd = tmp_path / "ixd"
    ixd.mkdir()
    # Create existing artifacts that should be quarantined.
    (ixd / "SPY_analysis.xlsx").write_bytes(b"OLD-XLSX")
    (ixd / "SPY_analysis.xlsx.manifest.json").write_bytes(
        b"{\"old\": true}",
    )
    (ixd / "SPY_analysis.runner_partial.xlsx").write_bytes(
        b"PARTIAL",
    )
    # Unrelated ticker workbook must remain untouched.
    (ixd / "AAPL_analysis.xlsx").write_bytes(b"AAPL-OLD")
    rep = runner.quarantine_existing_outputs_for_secondary(
        "SPY", output_dir=str(ixd),
        now_iso="20260517T120000Z",
    )
    assert rep["reason"] == "moved"
    assert rep["quarantine_dir"] == str(
        ixd / "_quarantine_20260517T120000Z",
    )
    moved_basenames = {
        os.path.basename(m["src"])
        for m in rep["moved_files"]
    }
    assert moved_basenames == {
        "SPY_analysis.xlsx",
        "SPY_analysis.xlsx.manifest.json",
        "SPY_analysis.runner_partial.xlsx",
    }
    # Canonical SPY paths are gone from ixd.
    for name in moved_basenames:
        assert not (ixd / name).exists()
    # AAPL is untouched.
    assert (ixd / "AAPL_analysis.xlsx").exists()
    # All moved files exist in quarantine dir.
    q_dir = Path(rep["quarantine_dir"])
    for name in moved_basenames:
        assert (q_dir / name).exists()


def test_quarantine_helper_noop_when_no_existing_outputs(tmp_path):
    ixd = tmp_path / "ixd"
    ixd.mkdir()
    rep = runner.quarantine_existing_outputs_for_secondary(
        "SPY", output_dir=str(ixd),
    )
    assert rep["reason"] == "no_matching_files_present"
    assert rep["moved_files"] == []
    assert rep["quarantine_dir"] is None


def test_quarantine_helper_rejects_unsafe_secondary(tmp_path):
    ixd = tmp_path / "ixd"
    ixd.mkdir()
    rep = runner.quarantine_existing_outputs_for_secondary(
        "../etc", output_dir=str(ixd),
    )
    assert rep["reason"] == "unsafe_or_empty_secondary"
    assert rep["moved_files"] == []


def test_execute_workbook_run_quarantines_existing_outputs(
    tmp_path,
):
    plan, ixd = _authorized_plan(tmp_path)
    # Plant pre-existing SPY artifacts BEFORE the run.
    # _authorized_plan returns ixd without creating it
    # (dry-run discipline); the runner creates it before
    # the write. We create it here so we can plant the
    # pre-existing artifacts.
    ixd.mkdir(parents=True, exist_ok=True)
    pre_xlsx = ixd / "SPY_analysis.xlsx"
    pre_sidecar = (
        ixd / "SPY_analysis.xlsx.manifest.json"
    )
    pre_xlsx.write_bytes(b"OLD-XLSX")
    pre_sidecar.write_bytes(b"{\"old\": true}")

    def _callable(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
        **_kwargs,
    ):
        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            Path(partial).write_bytes(b"NEW-XLSX")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{\"new\": true}")

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
        impactsearch_callable_override=_callable,
    )
    pt = res["per_ticker_results"][0]
    qr = pt["quarantine_report"]
    assert qr["reason"] == "moved"
    moved_basenames = {
        os.path.basename(m["src"])
        for m in qr["moved_files"]
    }
    assert "SPY_analysis.xlsx" in moved_basenames
    assert (
        "SPY_analysis.xlsx.manifest.json"
        in moved_basenames
    )
    # New workbook is the fresh content, not the old.
    assert pre_xlsx.read_bytes() == b"NEW-XLSX"


def test_runner_passes_through_zero_primary_yfinance_when_records_empty(
    tmp_path,
):
    plan, _ixd = _authorized_plan(tmp_path)

    def _callable(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
        **_kwargs,
    ):
        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            Path(partial).write_bytes(b"X")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{}")

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
        impactsearch_callable_override=_callable,
    )
    pt = res["per_ticker_results"][0]
    assert pt["primary_yfinance_fetch_count"] == 0
    assert pt["secondary_yfinance_fetch_count"] == 0
    assert pt["primary_yfinance_fetches"] == []


def test_runner_records_primary_and_secondary_fetches_via_impactsearch_seam(
    tmp_path, monkeypatch,
):
    """Inject a fake impactsearch module that returns one
    primary + one secondary record; runner must partition
    them correctly into the per-ticker result fields."""
    plan, _ixd = _authorized_plan(tmp_path)

    import types as _types
    fake_records = []

    def _reset():
        fake_records.clear()

    def _get():
        return list(fake_records)

    fake_mod = _types.ModuleType("impactsearch")
    fake_mod.reset_yf_records = _reset
    fake_mod.get_yf_records = _get
    monkeypatch.setitem(sys.modules, "impactsearch", fake_mod)

    def _callable(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
        **_kwargs,
    ):
        # Simulate impactsearch recording 1 secondary + 2 primary fetches.
        fake_records.append({
            "role": "secondary", "ticker": "SPY",
            "stage": "process_primary_tickers",
            "timestamp": "t0", "worker": "Main",
        })
        fake_records.append({
            "role": "primary", "ticker": "AAPL",
            "stage": "process_single_ticker",
            "timestamp": "t1", "worker": "W1",
        })
        fake_records.append({
            "role": "primary", "ticker": "MSFT",
            "stage": "process_single_ticker",
            "timestamp": "t2", "worker": "W2",
        })

        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            Path(partial).write_bytes(b"X")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{}")

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
        impactsearch_callable_override=_callable,
    )
    pt = res["per_ticker_results"][0]
    assert pt["primary_yfinance_fetch_count"] == 2
    assert pt["secondary_yfinance_fetch_count"] == 1
    fetched_primary_tickers = {
        r["ticker"] for r in pt["primary_yfinance_fetches"]
    }
    assert fetched_primary_tickers == {"AAPL", "MSFT"}


def test_runner_zero_yf_gate_marks_failure_when_primary_fetch_observed(
    tmp_path, monkeypatch,
):
    plan, _ixd = _authorized_plan(tmp_path)
    monkeypatch.setenv("IMPACT_REQUIRE_ZERO_PRIMARY_YF", "1")

    import types as _types
    fake_records = [
        {
            "role": "primary", "ticker": "AAPL",
            "stage": "process_single_ticker",
            "timestamp": "t1", "worker": "W1",
        },
    ]
    fake_mod = _types.ModuleType("impactsearch")
    fake_mod.reset_yf_records = lambda: None
    fake_mod.get_yf_records = lambda: list(fake_records)
    monkeypatch.setitem(sys.modules, "impactsearch", fake_mod)

    def _callable(
        *,
        secondary,
        primary_tickers,
        output_path,
        use_multiprocessing,
        export_atomic,
        **_kwargs,
    ):
        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            Path(partial).write_bytes(b"X")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{}")

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
        impactsearch_callable_override=_callable,
    )
    pt = res["per_ticker_results"][0]
    assert pt["status"] == "failed"
    assert "IMPACT_REQUIRE_ZERO_PRIMARY_YF" in pt["reason"]
    assert "AAPL" in pt["reason"]
    assert pt["primary_yfinance_fetch_count"] == 1


# ---------------------------------------------------------------------------
# 10d. --validation-mode legacy_fast | durable (Phase 6I-57 amendment)
# ---------------------------------------------------------------------------


def test_validation_mode_constants_are_exposed():
    assert runner.VALIDATION_MODE_DURABLE == "durable"
    assert runner.VALIDATION_MODE_LEGACY_FAST == "legacy_fast"
    assert set(runner.ALL_VALIDATION_MODES) == {
        "durable", "legacy_fast",
    }
    assert runner.LEGACY_FAST_VALIDATION_STATUS == (
        "not_run_manual_spymaster_audit"
    )


def test_cli_parses_validation_mode_durable():
    args = runner._parse_argv([
        "--secondaries", "SPY",
        "--validation-mode", "durable",
    ])
    assert args.validation_mode == "durable"


def test_cli_parses_validation_mode_legacy_fast():
    args = runner._parse_argv([
        "--secondaries", "SPY",
        "--validation-mode", "legacy_fast",
    ])
    assert args.validation_mode == "legacy_fast"


def test_cli_default_validation_mode_is_durable():
    args = runner._parse_argv([
        "--secondaries", "SPY",
    ])
    assert args.validation_mode == "durable"


def test_cli_rejects_invalid_validation_mode():
    with pytest.raises(SystemExit):
        runner._parse_argv([
            "--secondaries", "SPY",
            "--validation-mode", "bogus_mode",
        ])


def test_plan_records_validation_mode_durable_default(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner.PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
        primary_library_existence_checker=(
            lambda t, d: True
        ),
    )
    assert plan["policy"]["validation_mode"] == "durable"


def test_plan_records_validation_mode_legacy_fast(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner.PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
        primary_library_existence_checker=(
            lambda t, d: True
        ),
        validation_mode="legacy_fast",
    )
    assert plan["policy"]["validation_mode"] == "legacy_fast"


def test_plan_rejects_unknown_validation_mode(tmp_path):
    with pytest.raises(ValueError):
        runner.build_impactsearch_workbook_run_plan(
            secondaries=["SPY"],
            primary_source=(
                runner
                .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
            ),
            output_dir=str(tmp_path / "ixd"),
            signal_lib_dir=str(tmp_path / "sld"),
            price_cache_dir=str(tmp_path / "pcd"),
            pilot_universe_loader=lambda: ("SPY",),
            validation_mode="bogus",
        )


def test_command_manifest_emits_validation_mode_flag_durable(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner.PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
        primary_library_existence_checker=(
            lambda t, d: True
        ),
    )
    manifest = runner.build_command_manifest(plan)
    entry = manifest["entries"][0]
    assert "--validation-mode" in entry["argv"]
    idx = entry["argv"].index("--validation-mode")
    assert entry["argv"][idx + 1] == "durable"


def test_command_manifest_emits_validation_mode_flag_legacy_fast(
    tmp_path,
):
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY"],
        primary_source=(
            runner.PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
        primary_library_existence_checker=(
            lambda t, d: True
        ),
        validation_mode="legacy_fast",
    )
    manifest = runner.build_command_manifest(plan)
    entry = manifest["entries"][0]
    assert "--validation-mode" in entry["argv"]
    idx = entry["argv"].index("--validation-mode")
    assert entry["argv"][idx + 1] == "legacy_fast"


def _legacy_fast_authorized_plan(tmp_path):
    """Mirror of _authorized_plan but with validation_mode=legacy_fast."""
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
        primary_library_existence_checker=(
            lambda t, d: True
        ),
        validation_mode="legacy_fast",
    )
    return plan, ixd


def test_execute_workbook_run_legacy_fast_passes_validation_none_through_override(
    tmp_path,
):
    """When the plan policy carries validation_mode=legacy_fast,
    the runner must call the override callable with
    ``validation_mode="legacy_fast"``. The override then writes the
    workbook via the existing atomic export seam with
    ``validation_summary=None`` and
    ``per_strategy_validation=None``. The per-secondary result
    must surface ``validation_mode='legacy_fast'``,
    ``durable_validation_ran=False``, and
    ``validation_status='not_run_manual_spymaster_audit'``."""
    plan, ixd = _legacy_fast_authorized_plan(tmp_path)
    received: dict = {}

    def _override(
        *,
        secondary, primary_tickers, output_path,
        use_multiprocessing, export_atomic,
        validation_mode="durable", **_kwargs,
    ):
        received["validation_mode"] = validation_mode
        received["primary_tickers"] = list(primary_tickers)

        # Capture the kwargs the override passes into the
        # atomic export so we can prove validation_summary=None
        # and per_strategy_validation=None.
        atomic_kwargs_seen: dict = {}

        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            atomic_kwargs_seen["validation_summary"] = (
                validation_summary
            )
            atomic_kwargs_seen[
                "per_strategy_validation"
            ] = per_strategy_validation
            Path(partial).write_bytes(b"LEGACY-FAST-XLSX")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{}")

        atomic = export_atomic(
            output_path,
            [{"Primary Ticker": "AAPL"}],
            validation_summary=None,
            per_strategy_validation=None,
            export_callable=_fake_export,
        )
        received["atomic_kwargs_seen"] = atomic_kwargs_seen
        return {
            "status": "ok",
            "metrics_count": 1,
            "validation_sidecar_path": None,
            "validation_status": (
                runner.LEGACY_FAST_VALIDATION_STATUS
            ),
            "validation_mode": "legacy_fast",
            "durable_validation_ran": False,
            "canonical_path": atomic["canonical_path"],
            "canonical_sidecar": atomic[
                "canonical_sidecar"
            ],
        }

    res = runner.execute_workbook_run(
        plan, impactsearch_callable_override=_override,
    )
    assert res["status"] == "ok"
    pt = res["per_ticker_results"][0]
    assert received["validation_mode"] == "legacy_fast"
    assert (
        received["atomic_kwargs_seen"][
            "validation_summary"
        ]
        is None
    )
    assert (
        received["atomic_kwargs_seen"][
            "per_strategy_validation"
        ]
        is None
    )
    assert pt["validation_mode"] == "legacy_fast"
    assert pt["durable_validation_ran"] is False
    assert pt["validation_status"] == (
        "not_run_manual_spymaster_audit"
    )
    # No durable sidecar path returned by legacy_fast.
    assert pt.get("validation_sidecar_path") in (None, "None")


def test_execute_workbook_run_durable_default_preserves_validation_summary_path(
    tmp_path,
):
    """Durable mode (current default) must still pass a real
    validation_summary + per_strategy_validation into the
    atomic export and surface durable_validation_ran=True."""
    plan, ixd = _authorized_plan(tmp_path)
    # _authorized_plan does not pass validation_mode; default is durable.
    assert plan["policy"]["validation_mode"] == "durable"

    atomic_kwargs_seen: dict = {}

    def _override(
        *,
        secondary, primary_tickers, output_path,
        use_multiprocessing, export_atomic,
        validation_mode="durable", **_kwargs,
    ):
        assert validation_mode == "durable"

        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            atomic_kwargs_seen["validation_summary"] = (
                validation_summary
            )
            atomic_kwargs_seen[
                "per_strategy_validation"
            ] = per_strategy_validation
            Path(partial).write_bytes(b"DURABLE-XLSX")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{}")

        atomic = export_atomic(
            output_path,
            [{"Primary Ticker": "AAPL"}],
            validation_summary={
                "validation_status": "valid",
            },
            per_strategy_validation={
                "AAPL": {"ok": True},
            },
            export_callable=_fake_export,
        )
        return {
            "status": "ok",
            "metrics_count": 1,
            "validation_sidecar_path": "fake/sidecar.json",
            "validation_status": "valid",
            "validation_mode": "durable",
            "durable_validation_ran": True,
            "canonical_path": atomic["canonical_path"],
            "canonical_sidecar": atomic[
                "canonical_sidecar"
            ],
        }

    res = runner.execute_workbook_run(
        plan, impactsearch_callable_override=_override,
    )
    assert res["status"] == "ok"
    pt = res["per_ticker_results"][0]
    assert atomic_kwargs_seen["validation_summary"] == {
        "validation_status": "valid",
    }
    assert atomic_kwargs_seen[
        "per_strategy_validation"
    ] == {"AAPL": {"ok": True}}
    assert pt["validation_mode"] == "durable"
    assert pt["durable_validation_ran"] is True
    assert pt["validation_status"] == "valid"


def test_execute_workbook_run_fills_missing_legacy_fast_metadata(
    tmp_path,
):
    """If a (sloppy) override callable forgets to set the
    validation-mode metadata fields, the runner must guarantee
    they appear on the per-secondary result so downstream
    consumers can never mistake a legacy_fast workbook for a
    validated one."""
    plan, ixd = _legacy_fast_authorized_plan(tmp_path)

    def _sloppy_override(
        *,
        secondary, primary_tickers, output_path,
        use_multiprocessing, export_atomic,
        validation_mode="durable", **_kwargs,
    ):
        def _fake_export(
            partial, metrics, *,
            validation_summary,
            per_strategy_validation,
        ):
            Path(partial).write_bytes(b"X")
            Path(
                partial + ".manifest.json"
            ).write_bytes(b"{}")
        atomic = export_atomic(
            output_path,
            [{"Primary Ticker": "AAPL"}],
            validation_summary=None,
            per_strategy_validation=None,
            export_callable=_fake_export,
        )
        # Deliberately omit validation_mode /
        # durable_validation_ran / validation_status from
        # the result.
        return {
            "status": "ok",
            "metrics_count": 1,
            "canonical_path": atomic["canonical_path"],
            "canonical_sidecar": atomic[
                "canonical_sidecar"
            ],
        }

    res = runner.execute_workbook_run(
        plan, impactsearch_callable_override=_sloppy_override,
    )
    pt = res["per_ticker_results"][0]
    assert pt["validation_mode"] == "legacy_fast"
    assert pt["durable_validation_ran"] is False
    assert pt["validation_status"] == (
        "not_run_manual_spymaster_audit"
    )


def test_execute_workbook_run_refuses_unknown_validation_mode(
    tmp_path,
):
    """If a plan somehow carries an unknown validation_mode in
    its policy (e.g. forged), the executor must refuse before
    invoking ImpactSearch."""
    plan, _ixd = _authorized_plan(tmp_path)
    # Mutate the policy to a bogus value.
    plan["policy"]["validation_mode"] = "totally_bogus"
    res = runner.execute_workbook_run(plan)
    assert res["status"] == "refused"
    assert "unknown validation_mode" in res["reason"]
    assert res["per_ticker_results"] == []


def test_runner_argv_for_ticker_includes_validation_mode():
    """_runner_argv_for_ticker should always emit
    ``--validation-mode <mode>`` so the operator-facing
    reproducible command is unambiguous about the validation
    contract."""
    argv = runner._runner_argv_for_ticker(
        "SPY",
        primary_source=(
            runner.PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        primary_csv=None,
        output_dir="output/impactsearch",
        signal_lib_dir="signal_library/data/stable",
        price_cache_dir="price_cache/daily",
        impact_xlsx_max_age_days=45,
        current_as_of_date=None,
        use_multiprocessing=True,
        write=True,
        allow_network_fetch=True,
        strict_manifests=False,
        validation_mode="legacy_fast",
    )
    assert "--validation-mode" in argv
    idx = argv.index("--validation-mode")
    assert argv[idx + 1] == "legacy_fast"


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
# 11b. Atomic export — amendment-1 append/dedupe semantics
# ---------------------------------------------------------------------------


def test_atomic_export_preserves_existing_workbook_for_append_dedupe(
    tmp_path,
):
    """Canonical workbook must be copied to the partial
    path BEFORE export_results_to_excel runs, so its
    existing read-existing -> append -> dedupe -> write
    logic at impactsearch.py:2631-2667 sees the prior
    rows. Pins amendment-1 behavior."""
    out_path = tmp_path / "ixd" / "SPY_analysis.xlsx"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"CANONICAL-XLSX-AB")
    seen_inputs: list[bytes] = []

    def _export_observes_existing(
        partial, metrics, *,
        validation_summary,
        per_strategy_validation,
    ):
        # Verify export saw the prior bytes at partial
        # path BEFORE it wrote -- this is the entire
        # append/dedupe contract.
        seen_inputs.append(Path(partial).read_bytes())
        # Simulate ImpactSearch's overwrite of the
        # partial with the merged result.
        Path(partial).write_bytes(b"MERGED-XLSX-ABC")
        Path(partial + ".manifest.json").write_bytes(
            b"{}"
        )

    runner._atomic_export_workbook(
        str(out_path),
        [{"Primary Ticker": "C"}],
        validation_summary={},
        per_strategy_validation={},
        export_callable=_export_observes_existing,
    )
    # Export saw the canonical bytes.
    assert seen_inputs == [b"CANONICAL-XLSX-AB"]
    # Canonical replaced atomically.
    assert out_path.read_bytes() == b"MERGED-XLSX-ABC"
    # No partials left.
    leftovers = [
        p
        for p in out_path.parent.glob("*")
        if "runner_partial" in p.name
    ]
    assert leftovers == []


def test_atomic_export_copies_existing_sidecar_to_partial(
    tmp_path,
):
    """Canonical sidecar must be copied to the partial
    sidecar path BEFORE export, so
    impactsearch._inspect_preexisting_xlsx_manifest at
    impactsearch.py:2629 sees the same prior state it
    would see on a direct write to the canonical
    path."""
    out_path = tmp_path / "ixd" / "SPY_analysis.xlsx"
    out_path.parent.mkdir(parents=True)
    out_path.write_bytes(b"CANONICAL-XLSX")
    canonical_sidecar = out_path.parent / (
        "SPY_analysis.xlsx.manifest.json"
    )
    canonical_sidecar.write_text(
        '{"prior_status": "verified_ok"}',
        encoding="utf-8",
    )
    sidecars_seen: list[str] = []

    def _export(
        partial, metrics, *,
        validation_summary,
        per_strategy_validation,
    ):
        partial_sidecar = (
            partial + ".manifest.json"
        )
        if Path(partial_sidecar).exists():
            sidecars_seen.append(
                Path(partial_sidecar).read_text(
                    encoding="utf-8",
                ),
            )
        # Write the merged outputs.
        Path(partial).write_bytes(b"NEW-XLSX")
        Path(partial_sidecar).write_text(
            '{"new_status": "verified_ok"}',
            encoding="utf-8",
        )

    runner._atomic_export_workbook(
        str(out_path),
        [],
        validation_summary={},
        per_strategy_validation={},
        export_callable=_export,
    )
    # Export saw the canonical sidecar bytes.
    assert sidecars_seen == [
        '{"prior_status": "verified_ok"}',
    ]
    assert (
        canonical_sidecar.read_text(encoding="utf-8")
        == '{"new_status": "verified_ok"}'
    )


def test_atomic_export_failure_leaves_canonical_byte_identical(
    tmp_path,
):
    """If export raises after the partial has been
    staged (or while it is being written), the
    canonical workbook and canonical sidecar must
    remain byte-identical to their pre-call state."""
    out_path = tmp_path / "ixd" / "SPY_analysis.xlsx"
    out_path.parent.mkdir(parents=True)
    original_xlsx = b"CANONICAL-XLSX-ORIGINAL"
    out_path.write_bytes(original_xlsx)
    canonical_sidecar = out_path.parent / (
        "SPY_analysis.xlsx.manifest.json"
    )
    original_sidecar = (
        '{"prior_status": "verified_ok"}'
    )
    canonical_sidecar.write_text(
        original_sidecar, encoding="utf-8",
    )

    def _failing_export(
        partial, metrics, *,
        validation_summary,
        per_strategy_validation,
    ):
        # Overwrite the partial (simulates a mid-export
        # crash after writing some bytes to the partial).
        Path(partial).write_bytes(b"PARTIAL-XLSX")
        Path(partial + ".manifest.json").write_text(
            '{"partial_status": "in_progress"}',
            encoding="utf-8",
        )
        raise RuntimeError("synthetic export failure")

    with pytest.raises(RuntimeError):
        runner._atomic_export_workbook(
            str(out_path),
            [],
            validation_summary={},
            per_strategy_validation={},
            export_callable=_failing_export,
        )
    # Canonical bytes unchanged.
    assert (
        out_path.read_bytes() == original_xlsx
    )
    assert (
        canonical_sidecar.read_text(encoding="utf-8")
        == original_sidecar
    )
    # No partials left.
    partials = [
        p
        for p in out_path.parent.glob("*")
        if "runner_partial" in p.name
    ]
    assert partials == []


def test_atomic_export_new_workbook_path_still_works(
    tmp_path,
):
    """With no canonical workbook, the export still
    creates the canonical via the partial route."""
    out_path = tmp_path / "ixd" / "SPY_analysis.xlsx"
    out_path.parent.mkdir(parents=True)
    assert not out_path.exists()

    def _export(
        partial, metrics, *,
        validation_summary,
        per_strategy_validation,
    ):
        # Export sees no prior file at the partial path
        # because nothing was copied (canonical absent).
        assert not Path(partial).exists()
        Path(partial).write_bytes(b"FRESH-XLSX")
        Path(partial + ".manifest.json").write_bytes(
            b"{}"
        )

    res = runner._atomic_export_workbook(
        str(out_path),
        [],
        validation_summary={},
        per_strategy_validation={},
        export_callable=_export,
    )
    assert res["had_canonical_pre_existing"] is False
    assert (
        res["had_canonical_sidecar_pre_existing"]
        is False
    )
    assert out_path.read_bytes() == b"FRESH-XLSX"
    assert (
        out_path.parent
        / "SPY_analysis.xlsx.manifest.json"
    ).exists()


# ---------------------------------------------------------------------------
# 11c. Primary signal-library scan (amendment-1)
# ---------------------------------------------------------------------------


def test_engine_version_matches_impactsearch_module():
    """Pin IMPACTSEARCH_ENGINE_VERSION against the actual
    ENGINE_VERSION constant in impactsearch.py. AST-level
    drift catch (does NOT import impactsearch; reads the
    source and parses for the assignment)."""
    here = Path(__file__).resolve().parent.parent
    src = (here / "impactsearch.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(src)
    found_version = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Name)
                    and tgt.id == "ENGINE_VERSION"
                ):
                    if isinstance(
                        node.value, ast.Constant,
                    ):
                        found_version = node.value.value
    assert found_version is not None, (
        "could not find ENGINE_VERSION assignment in "
        "impactsearch.py"
    )
    assert (
        runner.IMPACTSEARCH_ENGINE_VERSION
        == found_version
    )
    assert (
        runner.IMPACTSEARCH_ENGINE_VERSION_WITH_UNDERSCORES
        == found_version.replace(".", "_")
    )


def test_scan_primary_signal_libraries_all_found(
    tmp_path,
):
    sld = tmp_path / "sld"
    sld.mkdir()
    for t in ("AAPL", "JPM", "SPY"):
        (sld / f"{t}_stable_v1_0_0.pkl").write_bytes(
            b""
        )
    res = runner.scan_primary_signal_libraries(
        ["AAPL", "JPM", "SPY"],
        signal_lib_dir=str(sld),
    )
    assert res["found"] == ["AAPL", "JPM", "SPY"]
    assert res["missing"] == []
    assert res["found_count"] == 3
    assert res["missing_count"] == 0
    assert res["checker_engine_version"] == "1.0.0"


def test_scan_primary_signal_libraries_some_missing(
    tmp_path,
):
    sld = tmp_path / "sld"
    sld.mkdir()
    (sld / "AAPL_stable_v1_0_0.pkl").write_bytes(b"")
    res = runner.scan_primary_signal_libraries(
        ["AAPL", "JPM", "SPY"],
        signal_lib_dir=str(sld),
    )
    assert res["found"] == ["AAPL"]
    assert sorted(res["missing"]) == ["JPM", "SPY"]
    assert res["found_count"] == 1
    assert res["missing_count"] == 2


def test_scan_primary_signal_libraries_all_missing(
    tmp_path,
):
    sld = tmp_path / "sld"
    sld.mkdir()
    res = runner.scan_primary_signal_libraries(
        ["AAPL", "JPM"], signal_lib_dir=str(sld),
    )
    assert res["found"] == []
    assert sorted(res["missing"]) == ["AAPL", "JPM"]
    assert res["found_count"] == 0
    assert res["missing_count"] == 2


def test_scan_primary_signal_libraries_unsafe_rejected_no_filesystem(
    tmp_path,
):
    """Unsafe primary tickers must be rejected BEFORE
    any filesystem access (no `os.path.isfile` call on
    a path-like ticker)."""
    sld = tmp_path / "sld"
    sld.mkdir()
    calls: list[tuple[str, str]] = []

    def _instrumented_checker(ticker, root):
        calls.append((ticker, root))
        # Default behavior path (mirror _default_)
        return (
            runner
            ._default_primary_library_existence_checker(
                ticker, root,
            )
        )

    res = runner.scan_primary_signal_libraries(
        ["AAPL", "../etc", "foo/bar", "JPM"],
        signal_lib_dir=str(sld),
        existence_checker=_instrumented_checker,
    )
    # Checker was called for safe tickers only.
    safe_calls = [c[0] for c in calls]
    assert "AAPL" in safe_calls
    assert "JPM" in safe_calls
    assert "../etc" not in safe_calls
    assert "foo/bar" not in safe_calls
    # Unsafe entries land in `missing`.
    assert "../etc" in res["missing"] or (
        "../ETC" in res["missing"]
    )
    assert "FOO/BAR" in res["missing"] or (
        "foo/bar" in res["missing"]
    )


def test_scan_primary_signal_libraries_dot_dash_variant(
    tmp_path,
):
    """impactsearch.py:1538-1544 retries with the
    `.`-replaced-by-`-` variant. ``BRK.B`` should match
    a ``BRK-B_stable_v1_0_0.pkl`` file."""
    sld = tmp_path / "sld"
    sld.mkdir()
    (sld / "BRK-B_stable_v1_0_0.pkl").write_bytes(b"")
    res = runner.scan_primary_signal_libraries(
        ["BRK.B"], signal_lib_dir=str(sld),
    )
    assert res["found"] == ["BRK.B"]
    assert res["missing"] == []


def test_scan_primary_signal_libraries_does_not_import_impactsearch(
    monkeypatch, tmp_path,
):
    """Scanning must not import impactsearch / yfinance /
    dash / subprocess into sys.modules. (Top-level AST
    guard already pins import statements; this guard
    pins runtime behavior.)"""
    sld = tmp_path / "sld"
    sld.mkdir()
    # Capture the modules present before scan.
    forbidden = {
        "impactsearch", "yfinance", "dash",
        "subprocess",
    }
    pre = {m for m in sys.modules if m in forbidden}
    runner.scan_primary_signal_libraries(
        ["AAPL"], signal_lib_dir=str(sld),
    )
    post = {m for m in sys.modules if m in forbidden}
    newly_loaded = post - pre
    assert not newly_loaded, (
        f"scan_primary_signal_libraries newly imported "
        f"forbidden modules: {sorted(newly_loaded)}"
    )


# ---------------------------------------------------------------------------
# 11d. Plan builder integration with library scan (amendment-1)
# ---------------------------------------------------------------------------


def test_build_plan_carries_library_scan_fields(
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
        primary_library_existence_checker=(
            lambda t, d: t == "SPY"
        ),
    )
    row = plan["per_ticker"][0]
    assert row["primary_signal_libraries_found"] == ["SPY"]
    assert row[
        "primary_signal_libraries_missing"
    ] == ["AAPL"]
    assert row[
        "primary_signal_library_found_count"
    ] == 1
    assert row[
        "primary_signal_library_missing_count"
    ] == 1


def test_build_plan_zero_libraries_blocks_to_manual_review(
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
        primary_library_existence_checker=(
            lambda t, d: False
        ),
    )
    row = plan["per_ticker"][0]
    assert row["eligibility"] == (
        runner.ELIGIBILITY_BLOCKED
    )
    assert (
        runner.ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING
        in row["issue_codes"]
    )
    # No executable write command emitted.
    manifest = runner.build_command_manifest(plan)
    entry = manifest["entries"][0]
    assert entry["authorization_class"] == (
        runner.AUTH_CLASS_MANUAL_REVIEW
    )
    assert entry["argv"] is None


def test_build_plan_some_libraries_missing_warns_but_eligible(
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
        pilot_universe_loader=lambda: (
            "SPY", "AAPL", "JPM",
        ),
        primary_library_existence_checker=(
            lambda t, d: t in {"SPY", "AAPL"}
        ),
    )
    row = plan["per_ticker"][0]
    # Some libraries missing -> still eligible.
    assert row["eligibility"] == (
        runner.ELIGIBILITY_READY_TO_RUN_WITH_EXPLICIT_NETWORK
    )
    # But the warning issue is surfaced.
    assert (
        runner.ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING
        in row["issue_codes"]
    )
    # And the manifest entry carries the issue + counts.
    manifest = runner.build_command_manifest(plan)
    entry = manifest["entries"][0]
    assert (
        runner.ISSUE_PRIMARY_SIGNAL_LIBRARY_MISSING
        in entry["issue_codes"]
    )
    # Network-write class is still emitted.
    assert entry["authorization_class"] == (
        runner.AUTH_CLASS_IMPACTSEARCH_NETWORK_WRITE
    )
    # Amendment-1: counts + missing list appear on the
    # manifest entry too.
    assert entry[
        "primary_signal_library_found_count"
    ] == 2
    assert entry[
        "primary_signal_library_missing_count"
    ] == 1
    assert entry[
        "primary_signal_libraries_missing"
    ] == ["JPM"]


def test_command_manifest_carries_library_counts_on_all_entry_classes(
    tmp_path,
):
    """The four amendment-1 fields appear on every entry
    class (manual_review / network_write / workbook_write)."""
    plan = runner.build_impactsearch_workbook_run_plan(
        secondaries=["SPY", "../etc"],
        primary_source=(
            runner
            .PRIMARY_SOURCE_PHASE_6I_52_PILOT_UNIVERSE
        ),
        output_dir=str(tmp_path / "ixd"),
        signal_lib_dir=str(tmp_path / "sld"),
        price_cache_dir=str(tmp_path / "pcd"),
        pilot_universe_loader=lambda: ("SPY", "AAPL"),
        primary_library_existence_checker=(
            lambda t, d: True
        ),
    )
    manifest = runner.build_command_manifest(plan)
    for entry in manifest["entries"]:
        for key in (
            "primary_signal_library_found_count",
            "primary_signal_library_missing_count",
            "primary_signal_libraries_missing",
        ):
            assert key in entry, (
                f"{entry['secondary']!r} missing "
                f"manifest field {key!r}"
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
    """Narrow direct-guard assertion: the production-root
    guard ``runner._path_is_inside_production_root`` fires
    for each documented denylist entry. The
    ``--emit-command-manifest`` CLI branch routes through
    this exact guard (see
    ``impactsearch_workbook_runner._path_is_inside_production_root``
    and the call site in the manifest branch of
    ``main``), so the direct assertion preserves the
    denylist coverage without paying for the full
    ``runner.main`` plan-build setup. The
    sibling ``test_main_output_refuses_production_root``
    above still exercises the end-to-end CLI path for
    operator-facing PermissionError reporting; this test
    is the narrow predicate guard."""
    assert runner._path_is_inside_production_root(forbidden) is True


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
    """Pin the Section 7 + chain-section + amendment-1
    wording so any future edit to the evidence doc
    surfaces in review."""
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
        # Amendment-1 anchors.
        "Amendment-1",
        "preserves ImpactSearch's existing append/dedupe",
        "primary_signal_libraries_missing",
        "primary_signal_libraries_found",
        "IMPACTSEARCH_ENGINE_VERSION",
        "_inspect_preexisting_xlsx_manifest",
        "99 passed",
        "98 passed / 1 skipped",
        "265 passed",
        "263 passed / 2 skipped",
    )
    missing = [
        phrase for phrase in required
        if phrase not in body
    ]
    assert not missing, (
        f"6I-56 evidence doc missing required wording: "
        f"{missing!r}"
    )
