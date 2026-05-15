"""Phase 6I-54b tests for the StackBuilder secondary-
price-cache writer.

Pins:

  * Schema-version + format taxonomy + issue-code
    constants are stable.
  * Dry-run (default) produces a per-ticker report but
    writes NO file. Authorized write (--write) writes
    one file per pass-verification ticker, atomically.
  * --overwrite default is False; existing files are NOT
    overwritten. --overwrite=True allows overwrite.
  * Verification cascade catches: missing PKL, missing
    manifest, non-Close price_source, missing
    preprocessed_data, missing Close column, non-
    DatetimeIndex, non-numeric Close, null in Close,
    unsorted index.
  * CSV write produces a Date + Close two-column file
    readable by stackbuilder.load_secondary_prices
    contract.
  * Parquet write surfaces ``parquet_engine_unavailable``
    cleanly when no engine is installed (this
    environment) -- does NOT crash, sets wrote_file=
    False.
  * Mixed provenance is recorded honestly via the
    provenance_summary block in the report.
  * No raw ``pickle.load(`` call anywhere in the writer
    source.
  * No forbidden top-level imports (subprocess, yfinance,
    StackBuilder, engines, writer modules).
  * --execution-log production-root path guard rejects
    paths inside the five OTHER production roots
    (price_cache/daily is the writer's intended output
    so it's deliberately NOT in the guarded set).
  * Atomic write: a temp file is used and replaced.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Callable, Tuple
from dataclasses import dataclass


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import stackbuilder_price_cache_writer as pcw  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    ok: bool = True
    legacy: bool = False
    mismatches: list = None  # type: ignore[assignment]


def _make_loader(
    data_by_ticker: dict[str, Any],
    *,
    ok: bool = True,
    legacy: bool = False,
) -> Callable[..., Tuple[Any, _FakeResult]]:
    """Returns a fake verified_loader that returns one of
    the supplied per-ticker payloads. The path argument
    is inspected to look up which ticker is being loaded.
    """
    def _loader(pkl_path, **kwargs):
        ticker = (
            Path(pkl_path).stem.replace(
                "_precomputed_results", "",
            ).upper()
        )
        payload = data_by_ticker.get(ticker)
        if payload is None:
            return None, _FakeResult(
                ok=False, legacy=False, mismatches=[],
            )
        return payload, _FakeResult(
            ok=ok, legacy=legacy, mismatches=[],
        )
    return _loader


def _make_pkl_fixture(
    scd: Path,
    ticker: str,
    *,
    price_source: str = "Close",
    producer_engine: str = (
        "signal_engine_cache_refresher"
    ),
    engine_version: str = "6E-5.0.0",
    write_pkl: bool = True,
    write_manifest: bool = True,
) -> dict[str, Path]:
    """Create a zero-byte PKL + sidecar manifest in the
    fake signal-cache dir. The actual PKL bytes don't
    matter because tests inject a fake verified loader.
    """
    scd.mkdir(parents=True, exist_ok=True)
    pkl_path = (
        scd / f"{ticker}_precomputed_results.pkl"
    )
    if write_pkl:
        pkl_path.write_bytes(b"")
    manifest_path = Path(
        str(pkl_path) + ".manifest.json",
    )
    if write_manifest:
        manifest_path.write_text(
            json.dumps({
                "artifact_kind": "output",
                "artifact_type": (
                    "spymaster_precomputed_results"
                ),
                "params": {
                    "price_source": price_source,
                    "ticker": ticker,
                },
                "producer_engine": producer_engine,
                "engine_version": engine_version,
                "build_timestamp": (
                    "2026-05-15T00:00:00+00:00"
                ),
                "schema_version": 1,
            }),
            encoding="utf-8",
        )
    return {
        "pkl_path": pkl_path,
        "manifest_path": manifest_path,
    }


def _good_payload(rows: int = 10):
    import pandas as pd
    idx = pd.date_range(
        "2026-01-01", periods=rows, freq="D",
    )
    df = pd.DataFrame(
        {
            "Close": [
                100.0 + i for i in range(rows)
            ],
            "SMA_1": [0.0] * rows,
        },
        index=idx,
    )
    return {"preprocessed_data": df}


# ---------------------------------------------------------------------------
# 1. Schema + taxonomy stability
# ---------------------------------------------------------------------------


def test_schema_format_and_issue_codes_are_stable():
    assert (
        pcw.SCHEMA_VERSION
        == "stackbuilder_price_cache_writer_v1"
    )
    assert pcw.FORMAT_PARQUET == "parquet"
    assert pcw.FORMAT_CSV == "csv"
    assert pcw.ALL_FORMATS == (
        pcw.FORMAT_PARQUET, pcw.FORMAT_CSV,
    )
    # A few stable issue codes (the full list is in the
    # module; this checks representative entries).
    for code in (
        pcw.ISSUE_PKL_MISSING,
        pcw.ISSUE_MANIFEST_MISSING,
        pcw.ISSUE_LOADER_FAILED,
        pcw.ISSUE_PRICE_SOURCE_NOT_CLOSE,
        pcw.ISSUE_NO_CLOSE_COLUMN,
        pcw.ISSUE_INDEX_NOT_DATETIME,
        pcw.ISSUE_CLOSE_NOT_NUMERIC,
        pcw.ISSUE_CLOSE_EMPTY,
        pcw.ISSUE_CLOSE_HAS_NULLS,
        pcw.ISSUE_INDEX_NOT_SORTED,
        pcw.ISSUE_OUTPUT_ALREADY_EXISTS,
        pcw.ISSUE_PARQUET_ENGINE_UNAVAILABLE,
        pcw.ISSUE_WRITE_FAILED,
    ):
        assert isinstance(code, str) and code


# ---------------------------------------------------------------------------
# 2. Dry-run produces a report but writes NO file.
# ---------------------------------------------------------------------------


def test_dry_run_writes_no_file(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "SPY")
    loader = _make_loader({"SPY": _good_payload()})
    report = pcw.build_price_cache_write_report(
        ["SPY"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=False,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["ticker"] == "SPY"
    assert row["rows_read"] == 10
    assert row["wrote_file"] is False
    assert row["rows_written"] == 0
    assert row["issue_codes"] == []
    # No file landed in the output dir.
    assert (
        not (pcd / "SPY.csv").exists()
    ), "dry-run must not create output file"


# ---------------------------------------------------------------------------
# 3. Authorized CSV write creates the file.
# ---------------------------------------------------------------------------


def test_authorized_csv_write_creates_file(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "SPY")
    loader = _make_loader({"SPY": _good_payload(rows=5)})
    report = pcw.build_price_cache_write_report(
        ["SPY"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["wrote_file"] is True
    assert row["rows_written"] == 5
    assert row["issue_codes"] == []
    # File exists + has Date + Close columns.
    output_path = pcd / "SPY.csv"
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert lines[0] == "Date,Close"
    assert len(lines) == 6  # header + 5 data rows
    assert lines[1].startswith("2026-01-01,")


def test_csv_write_is_atomic_no_temp_left(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "AAA")
    loader = _make_loader({"AAA": _good_payload()})
    pcw.build_price_cache_write_report(
        ["AAA"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    assert (pcd / "AAA.csv").exists()
    # The .tmp sibling should not survive a successful
    # atomic write.
    assert not (pcd / "AAA.csv.tmp").exists()


# ---------------------------------------------------------------------------
# 4. --overwrite default False; --overwrite=True allows.
# ---------------------------------------------------------------------------


def test_no_overwrite_default(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    pcd.mkdir(parents=True, exist_ok=True)
    # Pre-existing file.
    (pcd / "BBB.csv").write_text(
        "preexisting\n", encoding="utf-8",
    )
    _make_pkl_fixture(scd, "BBB")
    loader = _make_loader({"BBB": _good_payload()})
    report = pcw.build_price_cache_write_report(
        ["BBB"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        overwrite=False,  # explicit default
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["wrote_file"] is False
    assert (
        pcw.ISSUE_OUTPUT_ALREADY_EXISTS
        in row["issue_codes"]
    )
    # Original content untouched.
    assert (
        (pcd / "BBB.csv").read_text(encoding="utf-8")
        == "preexisting\n"
    )


def test_overwrite_explicit_allows_replacement(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    pcd.mkdir(parents=True, exist_ok=True)
    (pcd / "CCC.csv").write_text(
        "preexisting\n", encoding="utf-8",
    )
    _make_pkl_fixture(scd, "CCC")
    loader = _make_loader(
        {"CCC": _good_payload(rows=3)},
    )
    report = pcw.build_price_cache_write_report(
        ["CCC"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        overwrite=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["wrote_file"] is True
    assert row["rows_written"] == 3
    assert row["issue_codes"] == []
    content = (
        (pcd / "CCC.csv").read_text(encoding="utf-8")
    )
    assert content.startswith("Date,Close\n")
    assert "preexisting" not in content


# ---------------------------------------------------------------------------
# 5. Parquet engine unavailable -> clean issue code.
# ---------------------------------------------------------------------------


def test_parquet_unavailable_surfaces_clean_issue_code(
    tmp_path,
):
    """In the spyproject2 environment, neither pyarrow
    nor fastparquet is installed; pandas raises
    ImportError. The writer must catch this and emit a
    clean issue code rather than crashing."""
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "PARQ")
    loader = _make_loader({"PARQ": _good_payload()})
    report = pcw.build_price_cache_write_report(
        ["PARQ"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_PARQUET,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["wrote_file"] is False
    assert (
        pcw.ISSUE_PARQUET_ENGINE_UNAVAILABLE
        in row["issue_codes"]
    )
    # No partial .tmp file left behind.
    assert (
        not (pcd / "PARQ.parquet.tmp").exists()
    )
    assert not (pcd / "PARQ.parquet").exists()


# ---------------------------------------------------------------------------
# 6. Verification cascade — each failure mode.
# ---------------------------------------------------------------------------


def test_missing_pkl_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    # No PKL fixture written.
    loader = _make_loader({})
    report = pcw.build_price_cache_write_report(
        ["NOPE"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["wrote_file"] is False
    assert pcw.ISSUE_PKL_MISSING in row["issue_codes"]


def test_missing_manifest_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "NOMAN", write_manifest=False)
    loader = _make_loader({"NOMAN": _good_payload()})
    report = pcw.build_price_cache_write_report(
        ["NOMAN"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["wrote_file"] is False
    assert (
        pcw.ISSUE_MANIFEST_MISSING
        in row["issue_codes"]
    )


def test_non_close_price_source_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(
        scd, "ADJ", price_source="AdjClose",
    )
    loader = _make_loader({"ADJ": _good_payload()})
    report = pcw.build_price_cache_write_report(
        ["ADJ"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["wrote_file"] is False
    assert (
        pcw.ISSUE_PRICE_SOURCE_NOT_CLOSE
        in row["issue_codes"]
    )


def test_loader_failure_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "FAIL")
    # Loader returns ok=False.
    loader = _make_loader(
        {"FAIL": _good_payload()},
        ok=False,
    )
    report = pcw.build_price_cache_write_report(
        ["FAIL"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert row["wrote_file"] is False
    assert (
        pcw.ISSUE_LOADER_FAILED in row["issue_codes"]
    )


def test_no_preprocessed_data_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "NOPRE")
    loader = _make_loader({"NOPRE": {"_ticker": "NOPRE"}})
    report = pcw.build_price_cache_write_report(
        ["NOPRE"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert (
        pcw.ISSUE_NO_PREPROCESSED_DATA
        in row["issue_codes"]
    )


def test_no_close_column_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "NOCLOSE")
    import pandas as pd
    bad = pd.DataFrame(
        {"NotClose": [1.0, 2.0]},
        index=pd.date_range("2026-01-01", periods=2),
    )
    loader = _make_loader(
        {"NOCLOSE": {"preprocessed_data": bad}},
    )
    report = pcw.build_price_cache_write_report(
        ["NOCLOSE"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert (
        pcw.ISSUE_NO_CLOSE_COLUMN in row["issue_codes"]
    )


def test_non_datetime_index_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "BADIDX")
    import pandas as pd
    bad = pd.DataFrame(
        {"Close": [1.0, 2.0]},
        index=[0, 1],  # not DatetimeIndex
    )
    loader = _make_loader(
        {"BADIDX": {"preprocessed_data": bad}},
    )
    report = pcw.build_price_cache_write_report(
        ["BADIDX"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert (
        pcw.ISSUE_INDEX_NOT_DATETIME
        in row["issue_codes"]
    )


def test_non_numeric_close_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "STRCLOSE")
    import pandas as pd
    bad = pd.DataFrame(
        {"Close": ["a", "b"]},
        index=pd.date_range("2026-01-01", periods=2),
    )
    loader = _make_loader(
        {"STRCLOSE": {"preprocessed_data": bad}},
    )
    report = pcw.build_price_cache_write_report(
        ["STRCLOSE"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert (
        pcw.ISSUE_CLOSE_NOT_NUMERIC
        in row["issue_codes"]
    )


def test_close_with_nulls_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "NULL")
    import pandas as pd
    import numpy as np
    bad = pd.DataFrame(
        {"Close": [1.0, np.nan, 3.0]},
        index=pd.date_range("2026-01-01", periods=3),
    )
    loader = _make_loader(
        {"NULL": {"preprocessed_data": bad}},
    )
    report = pcw.build_price_cache_write_report(
        ["NULL"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert (
        pcw.ISSUE_CLOSE_HAS_NULLS in row["issue_codes"]
    )


def test_unsorted_index_skips(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "UNSORT")
    import pandas as pd
    idx = pd.DatetimeIndex([
        "2026-01-03", "2026-01-01", "2026-01-02",
    ])
    bad = pd.DataFrame({"Close": [3.0, 1.0, 2.0]}, index=idx)
    loader = _make_loader(
        {"UNSORT": {"preprocessed_data": bad}},
    )
    report = pcw.build_price_cache_write_report(
        ["UNSORT"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    row = report["rows"][0]
    assert (
        pcw.ISSUE_INDEX_NOT_SORTED in row["issue_codes"]
    )


# ---------------------------------------------------------------------------
# 7. Mixed provenance grouping.
# ---------------------------------------------------------------------------


def test_mixed_provenance_grouping(tmp_path):
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    for ticker in ("SPY", "JNJ"):
        _make_pkl_fixture(
            scd, ticker,
            producer_engine=(
                "signal_engine_cache_refresher"
            ),
            engine_version="6E-5.0.0",
        )
    for ticker in ("AAPL", "HD"):
        _make_pkl_fixture(
            scd, ticker,
            producer_engine="spymaster",
            engine_version="1.0.0",
        )
    loader = _make_loader({
        "SPY": _good_payload(),
        "JNJ": _good_payload(),
        "AAPL": _good_payload(),
        "HD": _good_payload(),
    })
    report = pcw.build_price_cache_write_report(
        ["SPY", "JNJ", "AAPL", "HD"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=loader,
    )
    assert report["write_count"] == 4
    summary = report["provenance_summary"]
    assert summary["distinct_provenance_count"] == 2
    groups = {
        (g["producer_engine"], g["engine_version"]): (
            g["tickers"]
        )
        for g in summary["groups"]
    }
    assert sorted(groups[
        ("signal_engine_cache_refresher", "6E-5.0.0")
    ]) == ["JNJ", "SPY"]
    assert sorted(groups[
        ("spymaster", "1.0.0")
    ]) == ["AAPL", "HD"]


# ---------------------------------------------------------------------------
# 8. Static guards — no forbidden top-level imports, no
#    raw pickle.load anywhere in the module source.
# ---------------------------------------------------------------------------


_FORBIDDEN_TOP_LEVEL_IMPORTS = frozenset({
    "pickle",
    "subprocess",
    "yfinance",
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
        here / "stackbuilder_price_cache_writer.py"
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
        f"Forbidden top-level imports in writer: "
        f"{sorted(leaked)}"
    )


def test_module_source_has_no_raw_pickle_load():
    """AST-based guard: no actual ``pickle.load(...)`` or
    ``pickle_load_compat(...)`` call expression in the
    writer module source. Docstrings may MENTION those
    names in prose (e.g. "no raw pickle.load(") -- only
    real call expressions are forbidden. Top-level
    ``import pickle`` is also forbidden."""
    here = Path(__file__).resolve().parent.parent
    src = (
        here / "stackbuilder_price_cache_writer.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # Forbidden direct top-level pickle import.
        if isinstance(node, ast.Import):
            for n in node.names:
                assert (
                    n.name.split(".")[0] != "pickle"
                ), f"unexpected ``import pickle``: {n.name}"
        # Forbidden Attribute-style call: pickle.load(...)
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                if (
                    isinstance(fn.value, ast.Name)
                    and fn.value.id == "pickle"
                    and fn.attr == "load"
                ):
                    raise AssertionError(
                        "unexpected pickle.load(...) "
                        "call in writer module"
                    )
            # Forbidden bare call: pickle_load_compat(...)
            if isinstance(fn, ast.Name):
                assert fn.id != "pickle_load_compat", (
                    "unexpected pickle_load_compat(...) "
                    "call in writer module"
                )


# ---------------------------------------------------------------------------
# 9. --execution-log path guard.
# ---------------------------------------------------------------------------


def test_execution_log_rejects_other_production_roots(
    tmp_path, capsys,
):
    forbidden_logs = [
        "cache/results/run.jsonl",
        "cache\\status\\run.jsonl",
        "output/research_artifacts/run.jsonl",
        "output/stackbuilder/run.jsonl",
        "signal_library/data/stable/run.jsonl",
    ]
    for forbidden in forbidden_logs:
        rc = pcw.main([
            "--tickers", "SPY",
            "--execution-log", forbidden,
        ])
        err = capsys.readouterr().err
        assert rc == 2, (
            f"Expected rc=2 for {forbidden!r}; got "
            f"rc={rc}"
        )
        assert (
            "execution_log_inside_other_production_root"
            in err
        )


def test_execution_log_allows_md_library_path(
    tmp_path, capsys,
):
    """Non-production-root execution-log paths (e.g.
    md_library/shared/...) are allowed."""
    scd = tmp_path / "scd"
    pcd = tmp_path / "pcd"
    _make_pkl_fixture(scd, "AAA")
    log_path = tmp_path / "logs" / "writer.jsonl"
    report = pcw.build_price_cache_write_report(
        ["AAA"],
        signal_cache_dir=scd,
        stackbuilder_price_cache_dir=pcd,
        format=pcw.FORMAT_CSV,
        write=True,
        verified_loader=_make_loader(
            {"AAA": _good_payload()},
        ),
        execution_log_path=log_path,
    )
    assert log_path.exists()
    # JSONL line is well-formed.
    lines = log_path.read_text(
        encoding="utf-8",
    ).splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["schema_version"] == pcw.SCHEMA_VERSION
    assert parsed["write"] is True
    assert parsed["write_count"] == 1


# ---------------------------------------------------------------------------
# 10. No-tickers CLI rejects with rc=2.
# ---------------------------------------------------------------------------


def test_cli_rejects_empty_tickers(capsys):
    rc = pcw.main(["--tickers", "  ,  "])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no_tickers_supplied" in err
