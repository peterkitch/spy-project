"""Phase 6E-3 tests for signal_engine_cache_refresher.

Pins the refresher contract:

  - dry-run never writes
  - write=True writes only to the supplied temp dirs
  - written cache is loadable by
    primary_signal_engine.load_primary_signal_engine_payload
  - old / new cache_date_range_end is reported
  - stale_before / current_after compute correctly
  - invalid ticker -> ISSUE_INVALID_TICKER
  - data fetch failure -> ISSUE_DATA_FETCH_FAILED, no writes
  - data with no Close column -> ISSUE_DATA_NO_CLOSE_COLUMN
  - empty data -> ISSUE_DATA_EMPTY
  - CLI defaults to dry-run
  - CLI --write writes only to temp dirs
  - CLI invalid args return 2 (no SystemExit leak)
  - no multi-ticker CLI mode
  - provenance manifest sidecar lands when write=True
  - module-level static import audit (no Dash / daily board)
"""
from __future__ import annotations

import ast
import json
import pickle
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import signal_engine_cache_refresher as ser  # noqa: E402


# ---------------------------------------------------------------------------
# Mock data fetcher
# ---------------------------------------------------------------------------


def _make_synthetic_df(
    *, last_date: str, n: int = 30,
) -> pd.DataFrame:
    dates = pd.bdate_range(end=last_date, periods=n)
    df = pd.DataFrame(
        {"Close": [100.0 + i * 0.5 for i in range(n)]},
        index=dates,
    )
    df.index.name = "Date"
    return df


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache_dir = tmp_path / "cache_results"
    status_dir = tmp_path / "cache_status"
    artifact_root = tmp_path / "artifacts"
    other_root = tmp_path / "other"
    for d in (cache_dir, status_dir, artifact_root, other_root):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache_dir,
        "status_dir": status_dir,
        "artifact_root": artifact_root,
        "other_root": other_root,
    }


def _make_fetcher(df: pd.DataFrame):
    def fetcher(ticker: str) -> pd.DataFrame:
        return df
    return fetcher


def _failing_fetcher(ticker: str) -> pd.DataFrame:
    raise RuntimeError("network down")


def _empty_fetcher(ticker: str) -> pd.DataFrame:
    return pd.DataFrame()


def _no_close_fetcher(ticker: str) -> pd.DataFrame:
    return pd.DataFrame({"Open": [1.0, 2.0]})


def _seed_existing_cache(
    cache_dir: Path, ticker: str, *,
    last_date: str, n: int = 20,
) -> Path:
    """Write a minimal-but-loadable existing cache so the
    refresher's ``old_cache_date_range_end`` probe has data
    to report."""
    df = _make_synthetic_df(last_date=last_date, n=n)
    payload = {
        "preprocessed_data": df,
        "active_pairs": ["Buy 3,2"] * n,
        "_ticker": ticker,
    }
    safe = ticker.replace("^", "_")
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


# ---------------------------------------------------------------------------
# Forbidden imports
# ---------------------------------------------------------------------------


def test_refresher_module_has_no_forbidden_imports():
    """The refresher must not pull in the Dash app, the
    daily board, or any live engine module at import time.
    yfinance is allowed but only imported lazily inside the
    default fetcher so tests / dry-runs that supply a mock
    never trigger the import."""
    tree = ast.parse(
        Path(ser.__file__).read_text(encoding="utf-8"),
    )
    # Module-level imports only (top of file).
    forbidden_module_level = {
        "yfinance", "dash", "daily_signal_board",
        "trafficflow", "impactsearch", "onepass",
        "confluence", "cross_ticker_confluence",
        "spymaster",
    }
    top_level_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_imports.append(node.module)
    bad = [
        m for m in top_level_imports
        if m.split(".")[0] in forbidden_module_level
    ]
    assert not bad, (
        f"forbidden module-level imports in refresher: {bad}"
    )


# ---------------------------------------------------------------------------
# Dry-run = no writes
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write_cache_or_status(tmp_path: Path):
    dirs = _layout(tmp_path)
    fetcher = _make_fetcher(_make_synthetic_df(
        last_date="2026-05-08", n=20,
    ))
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=False,
        data_fetcher=fetcher,
        current_as_of_date="2026-05-08",
    )
    after = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert before == after, (
        f"dry-run wrote files: added {set(after) - set(before)}"
    )
    assert result.refreshed is False
    assert result.write is False
    assert ser.ISSUE_DRY_RUN in result.issue_codes
    assert result.new_cache_date_range_end == "2026-05-08"
    assert result.current_after is True


def test_dry_run_reports_old_and_new_end(tmp_path: Path):
    dirs = _layout(tmp_path)
    _seed_existing_cache(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-04", n=20,
    )
    fetcher = _make_fetcher(_make_synthetic_df(
        last_date="2026-05-08", n=20,
    ))
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=False,
        data_fetcher=fetcher,
        current_as_of_date="2026-05-08",
    )
    assert result.old_cache_date_range_end == "2026-05-04"
    assert result.new_cache_date_range_end == "2026-05-08"
    assert result.stale_before is True
    assert result.current_after is True


# ---------------------------------------------------------------------------
# write=True is guarded — Phase 6E-3 ships a data-only build
# path; writing such a payload over a real Spymaster cache
# would replace ``current_signal=Buy/Short`` with ``=None``.
# Until the SMA optimizer is extracted, --write is refused.
# ---------------------------------------------------------------------------


def test_write_is_refused_for_data_only_payload(tmp_path: Path):
    dirs = _layout(tmp_path)
    fetcher = _make_fetcher(_make_synthetic_df(
        last_date="2026-05-08", n=20,
    ))
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=True,
        data_fetcher=fetcher,
        current_as_of_date="2026-05-08",
    )
    assert result.write is True
    # Guard fires: the data_only_v1 payload must not land.
    assert result.refreshed is False
    assert (
        ser.ISSUE_DATA_ONLY_WRITE_BLOCKED in result.issue_codes
    )
    # The result still reports the dates so the operator can
    # see what a future write would have advanced to.
    assert result.new_cache_date_range_end == "2026-05-08"
    after = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert before == after, (
        f"blocked write touched disk: added {set(after) - set(before)}"
    )


def test_blocked_write_does_not_overwrite_existing_valid_cache(
    tmp_path: Path,
):
    """An operator running --write against a ticker that
    already has a real Spymaster cache must NOT lose that
    cache. The Phase 6E-3 guard is the only thing standing
    between this CLI and a public-board regression, so the
    test pins the exact behavior."""
    dirs = _layout(tmp_path)
    seeded = _seed_existing_cache(
        dirs["cache_dir"], "SPY",
        last_date="2026-05-04", n=20,
    )
    before_bytes = seeded.read_bytes()
    fetcher = _make_fetcher(_make_synthetic_df(
        last_date="2026-05-08", n=20,
    ))
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=True,
        data_fetcher=fetcher,
        current_as_of_date="2026-05-08",
    )
    assert result.refreshed is False
    assert (
        ser.ISSUE_DATA_ONLY_WRITE_BLOCKED in result.issue_codes
    )
    assert seeded.exists(), "existing cache file vanished"
    assert seeded.read_bytes() == before_bytes, (
        "existing valid cache was overwritten by blocked --write"
    )


def test_blocked_write_emits_no_status_or_manifest(tmp_path: Path):
    dirs = _layout(tmp_path)
    fetcher = _make_fetcher(_make_synthetic_df(
        last_date="2026-05-08", n=20,
    ))
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=True,
        data_fetcher=fetcher,
        current_as_of_date="2026-05-08",
    )
    assert result.refreshed is False
    assert result.status_path is None
    assert result.manifest_path is None
    # Confirm structurally: no JSON / manifest files were
    # created anywhere under the supplied dirs.
    json_files = list(dirs["status_dir"].rglob("*.json"))
    manifest_files = list(dirs["cache_dir"].rglob("*.manifest.json"))
    assert json_files == []
    assert manifest_files == []


# ---------------------------------------------------------------------------
# Stale arithmetic
# ---------------------------------------------------------------------------


def test_stale_before_current_after_when_fresh(tmp_path: Path):
    """Stale-before / current-after reflect the fetched
    data's date_range_end vs the resolved cutoff, NOT
    whether the write actually landed. The Phase 6E-3
    guard refuses the write but the result still surfaces
    the staleness arithmetic so operators can plan."""
    dirs = _layout(tmp_path)
    _seed_existing_cache(
        dirs["cache_dir"], "SPY",
        last_date="2024-01-31", n=20,
    )
    fetcher = _make_fetcher(_make_synthetic_df(
        last_date="2026-05-08", n=20,
    ))
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=True,
        data_fetcher=fetcher,
        current_as_of_date="2026-05-08",
    )
    assert result.stale_before is True
    assert result.current_after is True
    # And the guard still fires under write=True so refreshed
    # stays False even when the fetch was successful.
    assert result.refreshed is False
    assert (
        ser.ISSUE_DATA_ONLY_WRITE_BLOCKED in result.issue_codes
    )


def test_current_after_false_when_fetch_does_not_advance(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _seed_existing_cache(
        dirs["cache_dir"], "SPY",
        last_date="2024-01-31", n=20,
    )
    fetcher = _make_fetcher(_make_synthetic_df(
        last_date="2024-01-31", n=20,
    ))
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=False,
        data_fetcher=fetcher,
        current_as_of_date="2026-05-08",
    )
    assert result.stale_before is True
    assert result.current_after is False


# ---------------------------------------------------------------------------
# Issue codes
# ---------------------------------------------------------------------------


def test_invalid_ticker_returns_issue_code(tmp_path: Path):
    dirs = _layout(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    result = ser.refresh_signal_engine_cache(
        "not a ticker!",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=True,
        data_fetcher=_make_fetcher(_make_synthetic_df(
            last_date="2026-05-08",
        )),
    )
    assert ser.ISSUE_INVALID_TICKER in result.issue_codes
    assert result.refreshed is False
    after = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    # Nothing should be written when the ticker is rejected.
    assert before == after


def test_fetch_failure_returns_issue_and_writes_nothing(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=True,
        data_fetcher=_failing_fetcher,
        current_as_of_date="2026-05-08",
    )
    assert ser.ISSUE_DATA_FETCH_FAILED in result.issue_codes
    assert result.refreshed is False
    after = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert before == after


def test_empty_data_returns_issue_and_writes_nothing(tmp_path: Path):
    dirs = _layout(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=True,
        data_fetcher=_empty_fetcher,
        current_as_of_date="2026-05-08",
    )
    assert ser.ISSUE_DATA_EMPTY in result.issue_codes
    assert result.refreshed is False
    after = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert before == after


def test_no_close_column_returns_issue(tmp_path: Path):
    dirs = _layout(tmp_path)
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=True,
        data_fetcher=_no_close_fetcher,
        current_as_of_date="2026-05-08",
    )
    assert ser.ISSUE_DATA_NO_CLOSE_COLUMN in result.issue_codes
    assert result.refreshed is False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_no_ticker_returns_2(capsys):
    try:
        rc = ser.main([])
    except SystemExit as exc:
        pytest.fail(
            "main() raised SystemExit when --ticker missing; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_unknown_flag_returns_2(capsys):
    try:
        rc = ser.main(["--ticker", "SPY", "--no-such-flag"])
    except SystemExit as exc:
        pytest.fail(
            "main() raised SystemExit on unknown flag; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_negative_max_sma_day_returns_2(capsys):
    try:
        rc = ser.main([
            "--ticker", "SPY", "--max-sma-day", "0",
        ])
    except SystemExit as exc:
        pytest.fail(
            "main() raised SystemExit on bad --max-sma-day; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2


def test_cli_does_not_offer_multi_ticker_mode():
    """The Phase 6E-3 CLI is intentionally single-ticker.
    Adding a multi-ticker option requires a fresh phase, so
    lock it now by asserting the absence of a multi-ticker
    flag in the parser."""
    parser = ser._build_arg_parser()
    actions = {a.dest for a in parser._actions}
    assert "tickers" not in actions, (
        "Phase 6E-3 CLI must not expose a --tickers option; "
        "single-ticker mode is part of the contract."
    )


def test_cli_default_is_dry_run(tmp_path: Path, capsys, monkeypatch):
    """Without --write, the CLI must not invoke the
    write=True path. We assert this by monkeypatching the
    refresher to record the ``write`` flag it was called
    with."""
    dirs = _layout(tmp_path)
    df = _make_synthetic_df(last_date="2026-05-08", n=20)

    calls: list[bool] = []
    real = ser.refresh_signal_engine_cache

    def spy(ticker, **kw):
        calls.append(bool(kw.get("write")))
        kw.setdefault("data_fetcher", _make_fetcher(df))
        return real(ticker, **kw)

    monkeypatch.setattr(
        ser, "refresh_signal_engine_cache", spy,
    )

    argv = [
        "--ticker", "SPY",
        "--cache-dir", str(dirs["cache_dir"]),
        "--status-dir", str(dirs["status_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = ser.main(argv)
    assert rc == 0
    assert calls == [False], (
        f"default invocation passed write={calls}; "
        "must be False (dry-run)"
    )


def test_cli_write_flag_is_refused_under_data_only_guard(
    tmp_path: Path, capsys, monkeypatch,
):
    """Phase 6E-3 keeps ``--write`` in the CLI but the
    data-only guard refuses every actual write while the
    SMA optimizer is unavailable. The CLI must report this
    via the JSON result and write nothing."""
    dirs = _layout(tmp_path)
    df = _make_synthetic_df(last_date="2026-05-08", n=20)

    def stub_fetch(*args, **kw):  # avoid yfinance entirely
        return df

    monkeypatch.setattr(
        ser, "_default_yfinance_fetcher", stub_fetch,
    )

    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    argv = [
        "--ticker", "SPY",
        "--write",
        "--cache-dir", str(dirs["cache_dir"]),
        "--status-dir", str(dirs["status_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = ser.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["write"] is True
    assert payload["refreshed"] is False
    assert "data_only_write_blocked" in payload["issue_codes"]
    after = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert before == after, (
        "CLI --write under guard touched disk; expected zero "
        f"file changes, got {set(after) - set(before)}"
    )


def test_cli_dry_run_emits_valid_json(
    tmp_path: Path, capsys, monkeypatch,
):
    dirs = _layout(tmp_path)
    df = _make_synthetic_df(last_date="2026-05-08", n=20)
    monkeypatch.setattr(
        ser, "_default_yfinance_fetcher",
        lambda t: df,
    )
    argv = [
        "--ticker", "SPY",
        "--dry-run",
        "--cache-dir", str(dirs["cache_dir"]),
        "--status-dir", str(dirs["status_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = ser.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["write"] is False
    assert payload["refreshed"] is False
    assert "dry_run_only" in payload["issue_codes"]


def test_cutoff_uses_resolve_current_as_of_date_monday_2026_05_11(
    tmp_path: Path, monkeypatch,
):
    """The refresher must use the same most-recent-weekday
    cutoff resolver as the Phase 6 readiness / preflight
    tools so a freshly-fetched SPY cache ending 2026-05-08
    is current_after=True when run on Monday 2026-05-11.

    Without this fix the refresher defaulted to today's UTC
    date and reported current_after=False on Mondays, which
    drifted from the rest of the launch-readiness stack."""
    import confluence_pipeline_readiness as cpr
    from datetime import datetime, timezone

    monday = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)
    resolved = cpr.resolve_current_as_of_date(None, now=monday)
    assert resolved == "2026-05-08", (
        f"resolver returned {resolved}; Monday 2026-05-11 must "
        "resolve to the previous Friday 2026-05-08"
    )

    dirs = _layout(tmp_path)
    fetcher = _make_fetcher(_make_synthetic_df(
        last_date="2026-05-08", n=20,
    ))

    # Pin the resolver's view of "now" so the refresher's
    # default-cutoff path resolves deterministically to
    # 2026-05-08.
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return monday if tz is None else monday.astimezone(tz)

    monkeypatch.setattr(cpr, "datetime", _FixedDatetime)
    result = ser.refresh_signal_engine_cache(
        "SPY",
        cache_dir=dirs["cache_dir"],
        status_dir=dirs["status_dir"],
        write=False,
        data_fetcher=fetcher,
    )
    assert result.new_cache_date_range_end == "2026-05-08"
    assert result.current_after is True, (
        "fresh fetch ending 2026-05-08 must be current_after=True "
        "when the cutoff resolves to 2026-05-08"
    )


def test_daily_signal_board_is_not_imported_by_refresher():
    """daily_signal_board must NOT be a transitive import of
    the refresher; that would break the read-only contract
    of the public web tier."""
    forbidden = "daily_signal_board"
    text = Path(ser.__file__).read_text(encoding="utf-8")
    assert forbidden not in text, (
        f"refresher source mentions {forbidden}"
    )
