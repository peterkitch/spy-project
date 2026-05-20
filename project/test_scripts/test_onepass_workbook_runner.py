"""Phase 6I-64 — tests for the headless OnePass runner scaffold.

All tests run without network, without importing real ``onepass``,
without invoking any engine, and without writing outside ``tmp_path``.
No local filesystem path literals are embedded — the project root is
derived from ``Path(__file__).resolve().parents[1]``.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest


# Project layout: this test lives at project/test_scripts/test_*.py;
# project root is parents[1].
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUNNER_PATH = PROJECT_ROOT / "onepass_workbook_runner.py"

import onepass_workbook_runner as runner  # noqa: E402


# ---------------------------------------------------------------------------
# Project-state snapshot fixture
# ---------------------------------------------------------------------------


_PROTECTED_ROOTS = ("output", "signal_library", "cache", "price_cache")


def _snapshot_roots() -> dict[str, set[str]]:
    snap: dict[str, set[str]] = {}
    for root_name in _PROTECTED_ROOTS:
        root = PROJECT_ROOT / root_name
        if not root.exists():
            snap[root_name] = set()
            continue
        snap[root_name] = {
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file()
        }
    return snap


@pytest.fixture(autouse=True)
def _no_production_writes():
    """Fail if any new file appears under the protected roots."""
    before = _snapshot_roots()
    yield
    after = _snapshot_roots()
    for root_name in _PROTECTED_ROOTS:
        new_files = after[root_name] - before[root_name]
        assert not new_files, (
            f"production write detected under {root_name}/: {sorted(new_files)}"
        )


# ---------------------------------------------------------------------------
# 1. AST guard — no dangerous top-level imports
# ---------------------------------------------------------------------------


def test_no_toplevel_dangerous_imports():
    forbidden = {
        "onepass",
        "dash",
        "yfinance",
        "plotly",
        "dash_bootstrap_components",
        "impactsearch",
    }
    src = RUNNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(RUNNER_PATH))
    bad: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden:
                    bad.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in forbidden:
                bad.append(f"from {node.module} import …")
    assert not bad, f"forbidden top-level imports: {bad}"


# ---------------------------------------------------------------------------
# 2-4. CLI / ticker resolution
# ---------------------------------------------------------------------------


def test_cli_parses_defaults():
    args = runner.parse_args([])
    assert args.tickers_file == runner.DEFAULT_TICKERS_FILE
    assert args.tickers is None
    assert args.output_dir == runner.DEFAULT_OUTPUT_DIR
    assert args.output_file == runner.DEFAULT_OUTPUT_FILE
    assert args.force_rebuild is False
    assert args.write is False
    assert args.allow_network_fetch is False


def test_cli_parses_explicit_tickers():
    args = runner.parse_args(["--tickers", "AAPL,MSFT,NA,NAN"])
    out = runner.resolve_tickers(args)
    assert out == ["AAPL", "MSFT", "NA", "NAN"]


def test_master_ticker_file_parsing_preserves_na(tmp_path: Path):
    f = tmp_path / "tickers.txt"
    f.write_text(
        "AAPL\nMSFT\nNA\nNAN\n#comment\n\nMSFT\n",
        encoding="utf-8",
    )
    args = runner.parse_args(["--tickers-file", str(f)])
    out = runner.resolve_tickers(args)
    assert out == ["AAPL", "MSFT", "NA", "NAN"]


# ---------------------------------------------------------------------------
# 5-8. main() behavior with fakes
# ---------------------------------------------------------------------------


def _make_engine_recorder():
    """Fake engine that records every call and returns mock metrics."""
    calls: list[tuple[str, bool]] = []

    def fake(ticker: str, use_existing_signals: bool):
        calls.append((ticker, use_existing_signals))
        return [{"Primary Ticker": ticker, "Trigger Days": 1}]

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def _make_export_writer():
    """Fake export that writes a small workbook + manifest sidecar."""
    writes: list[tuple[str, int]] = []

    def fake_export(output_path: str, metrics_list):
        p = Path(output_path)
        p.write_bytes(b"FAKE_XLSX_BYTES")
        manifest = p.with_name(p.name + ".manifest.json")
        manifest.write_text(
            json.dumps({"rows": len(metrics_list)}),
            encoding="utf-8",
        )
        writes.append((str(p), len(metrics_list)))

    fake_export.writes = writes  # type: ignore[attr-defined]
    return fake_export


def test_dry_run_emits_plan_and_does_not_write(
    tmp_path: Path, capsys, monkeypatch,
):
    monkeypatch.setattr(runner, "check_process_conflicts", lambda: [])
    tf = tmp_path / "tickers.txt"
    tf.write_text("AAPL\nMSFT\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    engine = _make_engine_recorder()

    rc = runner.main(
        argv=[
            "--tickers-file", str(tf),
            "--output-dir", str(out_dir),
        ],
        engine_callable=engine,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload["status"] == "dry_run"
    assert payload["tickers_count"] == 2
    assert payload["dry_run"] is True
    assert payload["use_existing_signals"] is True
    assert engine.calls == []
    # No workbook created.
    assert not out_dir.exists() or not any(out_dir.iterdir())


@pytest.mark.parametrize(
    "flags",
    [
        ["--write"],
        ["--allow-network-fetch"],
    ],
)
def test_write_requires_allow_network_fetch(
    flags, tmp_path: Path, capsys, monkeypatch,
):
    monkeypatch.setattr(runner, "check_process_conflicts", lambda: [])
    tf = tmp_path / "tickers.txt"
    tf.write_text("AAPL\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    engine = _make_engine_recorder()

    rc = runner.main(
        argv=[
            "--tickers-file", str(tf),
            "--output-dir", str(out_dir),
        ] + flags,
        engine_callable=engine,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["status"] == "dry_run"
    assert engine.calls == []


def test_process_conflict_blocks_run(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr(
        runner, "check_process_conflicts",
        lambda: ["pid=12345 cmd=python onepass.py"],
    )
    tf = tmp_path / "tickers.txt"
    tf.write_text("AAPL\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    engine = _make_engine_recorder()
    fake_export = _make_export_writer()

    rc = runner.main(
        argv=[
            "--tickers-file", str(tf),
            "--output-dir", str(out_dir),
            "--write", "--allow-network-fetch",
        ],
        engine_callable=engine,
        export_callable=fake_export,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["status"] == "blocked_process_conflict"
    assert engine.calls == []
    assert fake_export.writes == []
    assert not out_dir.exists() or not any(out_dir.iterdir())


def test_stdout_is_pure_json_despite_engine_noise(
    tmp_path: Path, capsys, monkeypatch,
):
    monkeypatch.setattr(runner, "check_process_conflicts", lambda: [])
    tf = tmp_path / "tickers.txt"
    tf.write_text("AAPL\nMSFT\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    def noisy_engine(ticker, use_existing_signals):
        sys.stdout.write(
            f"[onepass-BOOT] noisy import-time print from onepass for {ticker}\n"
        )
        sys.stdout.write("[onepass] per-ticker chatter line\n")
        return [{"Primary Ticker": ticker, "Trigger Days": 1}]

    rc = runner.main(
        argv=[
            "--tickers-file", str(tf),
            "--output-dir", str(out_dir),
            "--write", "--allow-network-fetch",
        ],
        engine_callable=noisy_engine,
        export_callable=_make_export_writer(),
    )
    captured = capsys.readouterr()
    # stdout must be exactly one JSON object + a single trailing newline.
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["status"] in (
        "ok", "completed_with_ticker_errors",
    )
    # Engine noise must NOT appear in stdout.
    assert "noisy import-time print" not in captured.out
    assert "per-ticker chatter line" not in captured.out


# ---------------------------------------------------------------------------
# 9-11. execute_run behavior
# ---------------------------------------------------------------------------


def _make_args(tmp_path: Path, output_dir: Path, *, force_rebuild=False):
    return argparse.Namespace(
        tickers_file="<unused>",
        tickers=None,
        output_dir=str(output_dir),
        output_file="onepass.xlsx",
        force_rebuild=force_rebuild,
        write=True,
        allow_network_fetch=True,
    )


def test_per_ticker_continuation_on_error(tmp_path: Path):
    out_dir = tmp_path / "out"
    args = _make_args(tmp_path, out_dir)
    tickers = ["AAPL", "BAD", "MSFT"]

    def flaky_engine(ticker, use_existing_signals):
        if ticker == "BAD":
            raise RuntimeError("simulated mid-batch failure")
        return [{"Primary Ticker": ticker}]

    fake_export = _make_export_writer()
    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        result = runner.execute_run(
            args, tickers,
            engine_callable=flaky_engine,
            export_callable=fake_export,
        )

    assert result["summary"] == {"ok": 2, "error": 1, "total": 3}
    statuses = [r["status"] for r in result["per_ticker_results"]]
    assert statuses == ["ok", "error", "ok"]
    assert result["status"] == "completed_with_ticker_errors"
    assert result["metrics_count"] == 2


def test_atomic_partial_replacement(tmp_path: Path):
    out_dir = tmp_path / "out"
    args = _make_args(tmp_path, out_dir)
    tickers = ["AAPL"]

    engine = _make_engine_recorder()
    fake_export = _make_export_writer()

    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        result = runner.execute_run(
            args, tickers,
            engine_callable=engine,
            export_callable=fake_export,
        )

    canonical_workbook = out_dir / "onepass.xlsx"
    canonical_manifest = out_dir / "onepass.xlsx.manifest.json"
    partial_workbook = out_dir / "onepass.runner_partial.xlsx"
    partial_manifest = out_dir / "onepass.runner_partial.xlsx.manifest.json"

    assert result["status"] == "ok"
    assert canonical_workbook.exists()
    assert canonical_manifest.exists()
    assert not partial_workbook.exists()
    assert not partial_manifest.exists()
    # All paths confined to tmp_path.
    for p in (
        canonical_workbook, canonical_manifest,
        partial_workbook, partial_manifest,
    ):
        assert str(tmp_path) in str(p.resolve()) or not p.exists()
    assert result["workbook_path"] == str(canonical_workbook)
    assert result["manifest_path"] == str(canonical_manifest)


def test_no_quarantine_directory_created(tmp_path: Path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    canonical_workbook = out_dir / "onepass.xlsx"
    canonical_workbook.write_bytes(b"PRE_EXISTING_CONTENT")

    args = _make_args(tmp_path, out_dir)
    tickers = ["AAPL"]

    engine = _make_engine_recorder()
    fake_export = _make_export_writer()

    err_buf = io.StringIO()
    with redirect_stderr(err_buf):
        result = runner.execute_run(
            args, tickers,
            engine_callable=engine,
            export_callable=fake_export,
        )

    assert result["status"] == "ok"
    # Canonical replaced.
    assert canonical_workbook.read_bytes() == b"FAKE_XLSX_BYTES"
    # No quarantine dir.
    quarantines = [
        p for p in out_dir.iterdir()
        if p.is_dir() and p.name.startswith("_quarantine")
    ]
    assert quarantines == []
    # Only canonical workbook + manifest in out_dir.
    file_names = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    assert file_names == [
        "onepass.xlsx", "onepass.xlsx.manifest.json",
    ]


# ---------------------------------------------------------------------------
# 12. (covered by the autouse fixture `_no_production_writes`)
# ---------------------------------------------------------------------------


def test_no_production_output_writes():
    """The autouse fixture asserts no protected-root files appeared.

    This test exists to make the contract visible in the test list.
    No body work needed; the fixture wraps every test in the file.
    """
    assert True
