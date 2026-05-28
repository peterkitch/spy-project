"""K=6 MTF launch-path source-mode tests for multi_timeframe_builder.

Pins the opt-in source-mode contract added in PR 1 of the K=6 MTF
launch-path implementation chain. Design authority:

  - md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md
    "Per-Timeframe Signal Generation"

Required default behavior:

  - Existing callers that do not pass source_mode observe the
    historic Yahoo-native interval fetch for 1wk and 1mo.

Required launch-path behavior:

  - source_mode="launch_path_daily_resampled" routes 1wk and 1mo
    through daily-Close fetch + local resample to W-MON last
    (1wk) or MS first (1mo). SMA crossover signals are then
    computed on those resampled timeframe bars.

Required invariants in both modes:

  - 3mo continues to resample daily Close to QS first.
  - 1y continues to resample daily Close to YE-DEC last.
  - 1d behavior and daily-protection behavior are unchanged.
  - No projection of daily signals onto longer windows; the
    trafficflow_multitimeframe_bridge projection module is not
    imported by the builder.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, List

import numpy as np
import pandas as pd
import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


from signal_library import multi_timeframe_builder as builder  # noqa: E402


# ---------------------------------------------------------------------------
# Recorder fixture: capture _yf_download_with_retry calls without network
# ---------------------------------------------------------------------------


def _make_daily_df(n_days: int = 500, start: str = "2024-01-01") -> pd.DataFrame:
    """Synthetic daily Close DataFrame, business-day index."""
    idx = pd.date_range(start=start, periods=n_days, freq="B")
    rng = np.random.RandomState(7)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n_days))
    return pd.DataFrame({"Close": close}, index=idx)


def _make_weekly_df(n_weeks: int = 100, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=n_weeks, freq="W-MON")
    rng = np.random.RandomState(8)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n_weeks))
    return pd.DataFrame({"Close": close}, index=idx)


def _make_monthly_df(n_months: int = 60, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=n_months, freq="MS")
    rng = np.random.RandomState(9)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n_months))
    return pd.DataFrame({"Close": close}, index=idx)


class _CallRecorder:
    """Capture every _yf_download_with_retry invocation and return a
    pre-canned DataFrame indexed by the ``interval`` kwarg.

    This avoids any network access while still exercising the real
    builder fetch_interval_data branch logic, including MultiIndex
    flattening, tz-normalize, sort, T-1 skip, float64 cast, and final
    index normalization.
    """

    def __init__(self, payload_by_interval: dict):
        self.calls: List[dict] = []
        self._payload_by_interval = payload_by_interval

    def __call__(self, *args, **kwargs):
        interval = kwargs.get("interval")
        self.calls.append({
            "args": args, "kwargs": dict(kwargs), "interval": interval,
        })
        df = self._payload_by_interval.get(interval)
        if df is None:
            raise AssertionError(
                f"unexpected _yf_download_with_retry call for "
                f"interval={interval!r}; pre-canned payloads: "
                f"{sorted(self._payload_by_interval)}"
            )
        return df.copy()


@pytest.fixture
def recorder(monkeypatch):
    payloads = {
        "1d": _make_daily_df(n_days=500),
        "1wk": _make_weekly_df(n_weeks=120),
        "1mo": _make_monthly_df(n_months=60),
    }
    rec = _CallRecorder(payloads)
    monkeypatch.setattr(builder, "_yf_download_with_retry", rec)
    # Bypass T-1 trimming so synthetic data does not get a tail bar
    # dropped for being "current" against wall-clock now().
    monkeypatch.setenv("CONFLUENCE_SKIP_LAST_BAR", "0")
    return rec


# ---------------------------------------------------------------------------
# 1. Default (legacy_native) 1wk behavior is unchanged
# ---------------------------------------------------------------------------


def test_default_1wk_uses_yahoo_native_interval(recorder):
    out = builder.fetch_interval_data("TEST", "1wk")
    assert out is not None and not out.empty
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1wk"], (
        f"default mode must call yfinance with interval='1wk', got "
        f"{intervals_seen!r}"
    )


def test_default_1wk_via_generate_signals_uses_native_interval(recorder):
    library = builder.generate_signals_for_interval("TEST", "1wk")
    assert library is not None
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1wk"]


# ---------------------------------------------------------------------------
# 2. Default (legacy_native) 1mo behavior is unchanged
# ---------------------------------------------------------------------------


def test_default_1mo_uses_yahoo_native_interval(recorder):
    out = builder.fetch_interval_data("TEST", "1mo")
    assert out is not None and not out.empty
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1mo"]


def test_default_1mo_via_generate_signals_uses_native_interval(recorder):
    library = builder.generate_signals_for_interval("TEST", "1mo")
    assert library is not None
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1mo"]


# ---------------------------------------------------------------------------
# 3. Launch-path 1wk uses daily-resampled source
# ---------------------------------------------------------------------------


def test_launch_path_1wk_fetches_daily_not_native(recorder):
    out = builder.fetch_interval_data(
        "TEST", "1wk",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    )
    assert out is not None and not out.empty
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1d"], (
        f"launch-path 1wk must call yfinance with interval='1d', got "
        f"{intervals_seen!r}"
    )


def test_launch_path_1wk_resamples_W_MON_last(recorder):
    daily_df = recorder._payload_by_interval["1d"]
    expected = (
        daily_df[["Close"]].resample("W-MON").last().dropna()
    )
    out = builder.fetch_interval_data(
        "TEST", "1wk",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    )
    # Compare on the overlapping date range (T-1 skip is bypassed in
    # the recorder fixture).
    assert "Close" in out.columns
    common_idx = out.index.intersection(expected.index)
    assert len(common_idx) > 0
    np.testing.assert_allclose(
        out.loc[common_idx, "Close"].to_numpy(),
        expected.loc[common_idx, "Close"].to_numpy(),
        rtol=0, atol=1e-9,
    )


def test_launch_path_1wk_signals_computed_on_resampled_bars(recorder):
    """SMA crossover signals must be aligned to weekly bars, not
    daily bars. We assert this structurally: the returned library's
    ``dates`` axis matches the weekly-resample axis, not the daily
    one."""
    daily_df = recorder._payload_by_interval["1d"]
    weekly_expected = (
        daily_df[["Close"]].resample("W-MON").last().dropna()
    )
    library = builder.generate_signals_for_interval(
        "TEST", "1wk",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    )
    assert library is not None
    n_dates = len(library["dates"])
    assert n_dates == len(library["signals"])
    assert n_dates == len(library["close"])
    # Strictly fewer than the daily count (200+ daily -> tens of
    # weekly bars) -- proves SMA inputs were the weekly series.
    assert n_dates < len(daily_df)
    # Date count is within one of the weekly-resample axis (T-1 skip
    # is disabled in the fixture, so they should match exactly).
    assert n_dates == len(weekly_expected)


# ---------------------------------------------------------------------------
# 4. Launch-path 1mo uses daily-resampled source
# ---------------------------------------------------------------------------


def test_launch_path_1mo_fetches_daily_not_native(recorder):
    out = builder.fetch_interval_data(
        "TEST", "1mo",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    )
    assert out is not None and not out.empty
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1d"], (
        f"launch-path 1mo must call yfinance with interval='1d', got "
        f"{intervals_seen!r}"
    )


def test_launch_path_1mo_resamples_MS_first(recorder):
    daily_df = recorder._payload_by_interval["1d"]
    expected = (
        daily_df[["Close"]].resample("MS").first().dropna()
    )
    out = builder.fetch_interval_data(
        "TEST", "1mo",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    )
    assert "Close" in out.columns
    common_idx = out.index.intersection(expected.index)
    assert len(common_idx) > 0
    np.testing.assert_allclose(
        out.loc[common_idx, "Close"].to_numpy(),
        expected.loc[common_idx, "Close"].to_numpy(),
        rtol=0, atol=1e-9,
    )


def test_launch_path_1mo_signals_computed_on_resampled_bars(recorder):
    daily_df = recorder._payload_by_interval["1d"]
    monthly_expected = (
        daily_df[["Close"]].resample("MS").first().dropna()
    )
    library = builder.generate_signals_for_interval(
        "TEST", "1mo",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    )
    assert library is not None
    n_dates = len(library["dates"])
    assert n_dates == len(library["signals"])
    assert n_dates == len(library["close"])
    assert n_dates < len(daily_df)
    assert n_dates == len(monthly_expected)


# ---------------------------------------------------------------------------
# 5. 3mo and 1y unchanged in both modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_mode",
    [
        builder.SOURCE_MODE_LEGACY_NATIVE,
        builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    ],
)
def test_3mo_uses_daily_QS_first_in_both_modes(recorder, source_mode):
    out = builder.fetch_interval_data(
        "TEST", "3mo", source_mode=source_mode,
    )
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1d"], (
        f"3mo must always resample from daily, got {intervals_seen!r}"
    )
    daily_df = recorder._payload_by_interval["1d"]
    expected = (
        daily_df[["Close"]].resample("QS").first().dropna()
    )
    common_idx = out.index.intersection(expected.index)
    assert len(common_idx) > 0
    np.testing.assert_allclose(
        out.loc[common_idx, "Close"].to_numpy(),
        expected.loc[common_idx, "Close"].to_numpy(),
        rtol=0, atol=1e-9,
    )


@pytest.mark.parametrize(
    "source_mode",
    [
        builder.SOURCE_MODE_LEGACY_NATIVE,
        builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    ],
)
def test_1y_uses_daily_YE_DEC_last_in_both_modes(recorder, source_mode):
    out = builder.fetch_interval_data(
        "TEST", "1y", source_mode=source_mode,
    )
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1d"], (
        f"1y must always resample from daily, got {intervals_seen!r}"
    )
    daily_df = recorder._payload_by_interval["1d"]
    expected = (
        daily_df[["Close"]].resample("YE-DEC").last().dropna()
    )
    common_idx = out.index.intersection(expected.index)
    assert len(common_idx) > 0
    np.testing.assert_allclose(
        out.loc[common_idx, "Close"].to_numpy(),
        expected.loc[common_idx, "Close"].to_numpy(),
        rtol=0, atol=1e-9,
    )


# ---------------------------------------------------------------------------
# 6. 1d unchanged in both modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_mode",
    [
        builder.SOURCE_MODE_LEGACY_NATIVE,
        builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    ],
)
def test_1d_uses_yahoo_native_in_both_modes(recorder, source_mode):
    out = builder.fetch_interval_data(
        "TEST", "1d", source_mode=source_mode,
    )
    intervals_seen = [c["interval"] for c in recorder.calls]
    assert intervals_seen == ["1d"]
    assert "Close" in out.columns
    assert len(out) > 0


def test_1d_save_protection_unchanged_in_both_modes(tmp_path):
    """Saving a fake 1d library must still raise unless explicitly
    allowed, regardless of source_mode. Daily protection is enforced
    in save_signal_library, which is independent of source_mode."""
    fake_library = {
        "ticker": "TEST",
        "interval": "1d",
        "engine_version": builder.ENGINE_VERSION,
    }
    with pytest.raises(ValueError, match="overwrite daily library"):
        builder.save_signal_library(fake_library, "1d")


# ---------------------------------------------------------------------------
# 7. Output shape compatibility across modes
# ---------------------------------------------------------------------------


def test_library_key_set_matches_across_modes(recorder):
    """Both source modes must produce a library dict with the same
    canonical key set for a non-daily interval. The transient
    source-close key is popped only at save time, so it is allowed
    here in both libraries."""
    legacy = builder.generate_signals_for_interval(
        "TEST", "1wk",
        source_mode=builder.SOURCE_MODE_LEGACY_NATIVE,
    )
    launch = builder.generate_signals_for_interval(
        "TEST", "1wk",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED,
    )
    assert legacy is not None and launch is not None
    assert set(legacy.keys()) == set(launch.keys()), (
        f"key-set drift between modes:\n"
        f"  legacy-only: {set(legacy) - set(launch)!r}\n"
        f"  launch-only: {set(launch) - set(legacy)!r}"
    )
    for k in ("dates", "signals", "close"):
        assert len(launch[k]) == len(launch["dates"])
        assert len(legacy[k]) == len(legacy["dates"])


# ---------------------------------------------------------------------------
# 8. No projection path: trafficflow_multitimeframe_bridge is not used
# ---------------------------------------------------------------------------


def test_builder_does_not_import_trafficflow_multitimeframe_bridge():
    """AST-scan: the builder MUST NOT import the daily-signal
    projection helper. The K=6 MTF launch path is
    resample-prices-then-compute-signals, never
    project-daily-signals-onto-windows."""
    src = Path(builder.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad = [
        m for m in found
        if "trafficflow_multitimeframe_bridge" in m
    ]
    assert not bad, (
        f"builder imports projection bridge: {bad!r}; the launch "
        f"path is resample-prices-then-compute-signals, not "
        f"project-daily-signals-onto-windows"
    )


def test_unsupported_source_mode_raises():
    with pytest.raises(ValueError, match="Unsupported source_mode"):
        builder.fetch_interval_data(
            "TEST", "1wk", source_mode="not_a_real_mode",
        )
    with pytest.raises(ValueError, match="Unsupported source_mode"):
        builder.generate_signals_for_interval(
            "TEST", "1wk", source_mode="not_a_real_mode",
        )


def test_source_mode_constants_are_distinct_strings():
    assert builder.SOURCE_MODE_LEGACY_NATIVE == "legacy_native"
    assert (
        builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED
        == "launch_path_daily_resampled"
    )
    assert (
        builder.SOURCE_MODE_LEGACY_NATIVE
        != builder.SOURCE_MODE_LAUNCH_PATH_DAILY_RESAMPLED
    )
