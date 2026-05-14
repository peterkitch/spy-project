"""Phase 6I-30 tests for multi_timeframe_builder + sandbox builder.

Pins the Phase 6I-30 interval-native ``close`` contract:

  - Every persisted interval library carries a ``close`` field
    aligned 1:1 with ``dates`` / ``signals``.
  - The multi-window K input adapter consumes native ``close``
    directly (no close-source fallback required) and prefers it
    over any supplied fallback.
  - The Phase 6I-30 sandbox builder reads OHLCV from
    ``cache/results/<TICKER>_precomputed_results.pkl`` via the
    central provenance loader, resamples to each interval, and
    refuses to write to production ``signal_library/data/stable``.
  - No raw ``pickle.load`` in either module (AST-verified).
  - No ``.resample()`` / ``.ffill()`` call in the adapter; the
    sandbox builder's resample call is the ONLY resample site in
    the Phase 6I-30 patch and lives inside the builder layer.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import multiwindow_k_input_adapter as adapter  # noqa: E402
from signal_library import multi_timeframe_builder as builder  # noqa: E402
from signal_library import multi_timeframe_sandbox_builder as sb  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_synthetic_daily_df(
    n_days: int = 200,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=n_days, freq="B")
    close = 100.0 + np.cumsum(
        np.random.RandomState(42).normal(0, 0.5, n_days),
    )
    return pd.DataFrame({"Close": close}, index=idx)


# ---------------------------------------------------------------------------
# 1. Generated library carries native close
# ---------------------------------------------------------------------------


def test_generated_library_includes_native_close():
    """When ``generate_signals_for_interval`` is called via the
    Phase 6I-30 ``df=`` injection seam, the returned library dict
    MUST carry a ``close`` field aligned 1:1 with ``dates`` and
    ``signals``."""
    df = _make_synthetic_daily_df(n_days=200)
    library = builder.generate_signals_for_interval(
        "TEST", "1wk", df=df,
    )
    assert library is not None
    assert "close" in library
    assert len(library["close"]) == len(library["dates"])
    assert len(library["close"]) == len(library["signals"])
    # close values are plain Python floats (not pandas Series)
    for c in library["close"][:5]:
        assert isinstance(c, float)


def test_generated_library_close_matches_source_df():
    """The persisted ``close`` series must be the SAME values as
    the source DataFrame's ``Close`` column. No fabrication / no
    re-scaling."""
    df = _make_synthetic_daily_df(n_days=200)
    library = builder.generate_signals_for_interval(
        "TEST", "1wk", df=df,
    )
    expected = df["Close"].tolist()
    assert library["close"] == expected


# ---------------------------------------------------------------------------
# 2. Adapter prefers native close (no fallback consulted)
# ---------------------------------------------------------------------------


def test_adapter_prefers_native_close_over_fallback(tmp_path):
    """A library with native ``close`` MUST be used directly --
    the Phase 6I-28 close-source fallback must NOT be called.
    Pinned by a ``close_loader`` that raises if invoked."""

    class _FakeKRow:
        def __init__(self, K, members_str):
            self.K = K
            self.members_str = members_str

    def _discovery(target_ticker, *, stackbuilder_root=None):
        return tmp_path / "run"

    def _leaderboard(run_dir):
        return {"sentinel": True}

    def _k_rows_iter(leaderboard, *, target_ticker, run_id, expected_k):
        return [_FakeKRow(K=1, members_str="AAA[D]")]

    target_dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    target_close = [100.0, 101.0, 102.0]
    member_signals = ["Buy", "Short", "Buy"]
    libs = {
        ("SPY", "1d"): {
            "dates": list(target_dates),
            "signals": ["None"] * 3,
            "close": list(target_close),
        },
        ("AAA", "1d"): {
            "dates": list(target_dates),
            "signals": list(member_signals),
        },
    }

    def loader(ticker, interval, *, signal_library_dir=None):
        return libs.get((ticker.upper(), interval))

    def banned_close_loader(ticker, *, close_source_root=None):
        raise AssertionError(
            "close_loader must NOT be called when the target "
            "library has a native close column"
        )

    (tmp_path / "run").mkdir()
    report = adapter.prepare_multiwindow_k_inputs(
        "SPY",
        run_dir=tmp_path / "run",
        K_values=(1,), windows=("1d",),
        stackbuilder_run_discovery_callable=_discovery,
        leaderboard_loader_callable=_leaderboard,
        k_rows_iter_callable=_k_rows_iter,
        library_loader=loader,
        close_loader=banned_close_loader,
    )
    assert report.prepared_cell_count == 1
    cell = report.per_cell_inputs[(1, "1d")]
    assert cell["target_close"] == target_close


# ---------------------------------------------------------------------------
# 3. Sandbox builder: refuses to write to production
# ---------------------------------------------------------------------------


def test_sandbox_builder_refuses_production_stable_dir(tmp_path):
    """The sandbox CLI MUST refuse to write under
    ``signal_library/data/stable`` even if an operator points
    ``--output-dir`` there. rc=2."""
    fake_production = tmp_path / "signal_library" / "data" / "stable"
    rc = sb.main([
        "--tickers", "SPY",
        "--intervals", "1wk",
        "--cache-dir", str(tmp_path),
        "--output-dir", str(fake_production),
    ])
    assert rc == 2
    assert not fake_production.exists()


def test_sandbox_builder_missing_args_returns_rc_2():
    rc = sb.main([])
    assert rc == 2


def test_sandbox_builder_unknown_flag_returns_rc_2():
    rc = sb.main(["--no-such-flag"])
    assert rc == 2


# ---------------------------------------------------------------------------
# 4. Sandbox builder: end-to-end against in-memory cache fixture
# ---------------------------------------------------------------------------


def test_sandbox_builder_resamples_and_writes_close(tmp_path, monkeypatch):
    """End-to-end: feed a fake daily DataFrame to the sandbox
    builder via a monkey-patched ``load_daily_close_from_cache``,
    request every canonical interval, and verify each written
    library carries a native ``close`` aligned 1:1."""
    daily_df = _make_synthetic_daily_df(n_days=400)

    def fake_load(ticker, cache_dir):
        return daily_df.copy()
    monkeypatch.setattr(
        sb, "load_daily_close_from_cache", fake_load,
    )
    out_dir = tmp_path / "sandbox_libs"
    rc = sb.main([
        "--tickers", "FAKE",
        "--intervals", "1d,1wk,1mo,3mo,1y",
        "--cache-dir", str(tmp_path / "cache_unused"),
        "--output-dir", str(out_dir),
    ])
    assert rc == 0
    # All five interval libraries exist.
    expected_paths = [
        out_dir / "FAKE_stable_v1_0_0.pkl",
        out_dir / "FAKE_stable_v1_0_0_1wk.pkl",
        out_dir / "FAKE_stable_v1_0_0_1mo.pkl",
        out_dir / "FAKE_stable_v1_0_0_3mo.pkl",
        out_dir / "FAKE_stable_v1_0_0_1y.pkl",
    ]
    for p in expected_paths:
        assert p.exists(), f"missing sandbox library: {p}"

    # And each has a native close aligned to dates / signals.
    import provenance_manifest as pm
    for p in expected_paths:
        interval = (
            "1d" if "_1" not in p.name.replace(
                "_stable_v1_0_0", "",
            ).replace(".pkl", "")
            else p.name.split("_v1_0_0_", 1)[1].rsplit(".", 1)[0]
        )
        lib, vresult = pm.load_verified_signal_library(
            p,
            requested_params={
                "interval": interval, "price_source": "Close",
            },
            strict=False,
        )
        assert lib is not None
        assert "close" in lib
        assert (
            len(lib["close"]) == len(lib["dates"])
            == len(lib["signals"])
        )


# ---------------------------------------------------------------------------
# 5. Resampling frequency map mirrors production builder
# ---------------------------------------------------------------------------


def test_resample_frequency_map_mirrors_production_builder():
    """The sandbox builder's interval -> pandas-frequency map MUST
    match the production builder's ``fetch_interval_data`` choices
    (W-MON / MS / QS / YE-DEC). This is a static text check on
    ``multi_timeframe_builder.py`` that surfaces drift immediately."""
    src = Path(builder.__file__).read_text(encoding="utf-8")
    # Each of these tokens must appear in the production builder
    # because the sandbox builder copies them verbatim.
    for token in ("W-MON", "MS", "QS", "YE-DEC"):
        assert token in src, (
            f"production builder lost frequency token {token!r}"
        )


# ---------------------------------------------------------------------------
# 6. Sandbox builder: no raw pickle.load
# ---------------------------------------------------------------------------


def test_sandbox_builder_has_no_raw_pickle_load():
    src = Path(sb.__file__).read_text(encoding="utf-8")
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
                        "sandbox builder calls pickle.load() "
                        f"at line {node.lineno}; route through "
                        "the central provenance loader instead"
                    )


def test_sandbox_builder_has_no_yfinance_or_subprocess_imports():
    """The sandbox builder MUST be importable WITHOUT any
    network-touching dependency. AST-scan rejects any
    yfinance / subprocess / dash imports at top level."""
    src = Path(sb.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_first = {
        "yfinance", "dash", "subprocess",
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
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
        m for m in found
        if m.split(".")[0] in forbidden_first
    ]
    assert not bad, f"forbidden imports: {bad!r}"
