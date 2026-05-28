"""K=6 MTF launch-path local-PKL source-mode tests.

Pins the contract-compliant K=6 MTF launch path. Design authority:

  - md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md
    "Raw Price Source" and "Per-Timeframe Signal Generation"

The locked behavior:

  - source_mode = SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED reads
    the daily Close history from
    cache/results/<TICKER>_precomputed_results.pkl
    (obj["preprocessed_data"]["Close"]) and generates all five
    canonical timeframes (1d, 1wk, 1mo, 3mo, 1y) by locally
    resampling that single daily series.
  - No vendor fetch is performed.
  - The daily save-protection guard in save_signal_library remains
    in force regardless of source_mode; the caller must explicitly
    opt in (force_overwrite=True or CONFLUENCE_ALLOW_DAILY_OVERWRITE
    env var) to persist a 1d library.
"""
from __future__ import annotations

import os
import pickle
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


from signal_library import multi_timeframe_builder as builder  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_synthetic_daily_df(
    n_days: int = 500, start: str = "2024-01-01",
) -> pd.DataFrame:
    """Synthetic daily Close DataFrame on a business-day index."""
    idx = pd.date_range(start=start, periods=n_days, freq="B")
    rng = np.random.RandomState(11)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n_days))
    return pd.DataFrame({"Close": close.astype(np.float64)}, index=idx)


def _write_fake_cache_pkl(
    cache_dir: Path,
    ticker: str,
    daily_df: pd.DataFrame,
) -> Path:
    """Persist a minimal Spymaster-shaped cache PKL containing the
    daily DataFrame at the contract-mandated key.

    The cache PKL convention is ``preprocessed_data`` with a DataFrame
    carrying a ``Close`` column. The provenance loader treats this as
    a legacy artifact (no embedded manifest), which the local-PKL
    helper accepts.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"preprocessed_data": daily_df.copy()}
    pkl_path = cache_dir / f"{ticker}_precomputed_results.pkl"
    with open(pkl_path, "wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return pkl_path


@pytest.fixture
def local_pkl_cache(tmp_path):
    """Build a temp cache directory containing a single ticker PKL
    and disable T-1 trimming so synthetic data does not lose its
    tail bar."""
    cache_dir = tmp_path / "cache_results"
    daily_df = _make_synthetic_daily_df(n_days=500)
    _write_fake_cache_pkl(cache_dir, "TEST", daily_df)
    os.environ["CONFLUENCE_SKIP_LAST_BAR"] = "0"
    try:
        yield cache_dir, daily_df
    finally:
        os.environ.pop("CONFLUENCE_SKIP_LAST_BAR", None)


@pytest.fixture
def banned_vendor_fetch(monkeypatch):
    """Monkeypatch ``_yf_download_with_retry`` to fail loudly. The
    local-PKL mode MUST NOT call the vendor for any of its five
    timeframes."""
    def _bad(*args, **kwargs):
        raise AssertionError(
            "local-PKL launch path must NOT call "
            "_yf_download_with_retry; args=" + repr(args)
        )
    monkeypatch.setattr(builder, "_yf_download_with_retry", _bad)


# ---------------------------------------------------------------------------
# 1. Local-PKL mode reads cache PKL and does not fetch
# ---------------------------------------------------------------------------


def test_local_pkl_mode_reads_cache_and_does_not_fetch(
    local_pkl_cache, banned_vendor_fetch,
):
    cache_dir, daily_df = local_pkl_cache
    out = builder.fetch_interval_data(
        "TEST", "1wk",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
        cache_dir=str(cache_dir),
    )
    assert out is not None and not out.empty
    assert "Close" in out.columns


def test_local_pkl_missing_pkl_raises(tmp_path, banned_vendor_fetch):
    empty_cache = tmp_path / "empty_cache_results"
    empty_cache.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="local PKL missing"):
        builder.fetch_interval_data(
            "TEST", "1wk",
            source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
            cache_dir=str(empty_cache),
        )


def test_local_pkl_malformed_pkl_raises(tmp_path, banned_vendor_fetch):
    cache_dir = tmp_path / "bad_cache_results"
    cache_dir.mkdir(parents=True)
    bad_payload = {"not_preprocessed_data": pd.DataFrame()}
    bad_path = cache_dir / "TEST_precomputed_results.pkl"
    with open(bad_path, "wb") as fh:
        pickle.dump(bad_payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    with pytest.raises(ValueError, match="preprocessed_data"):
        builder.fetch_interval_data(
            "TEST", "1wk",
            source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
            cache_dir=str(cache_dir),
        )


# ---------------------------------------------------------------------------
# 2. All five intervals derive from the same local daily series
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "interval,freq,agg",
    [
        ("1wk", "W-MON", "last"),
        ("1mo", "MS", "first"),
        ("3mo", "QS", "first"),
        ("1y", "YE-DEC", "last"),
    ],
)
def test_local_pkl_non_daily_intervals_resample_from_same_daily(
    local_pkl_cache, banned_vendor_fetch, interval, freq, agg,
):
    cache_dir, daily_df = local_pkl_cache
    out = builder.fetch_interval_data(
        "TEST", interval,
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
        cache_dir=str(cache_dir),
    )
    rs = daily_df[["Close"]].resample(freq)
    if agg == "last":
        expected = rs.last().dropna()
    else:
        expected = rs.first().dropna()
    common_idx = out.index.intersection(expected.index)
    assert len(common_idx) > 0
    np.testing.assert_allclose(
        out.loc[common_idx, "Close"].to_numpy(),
        expected.loc[common_idx, "Close"].to_numpy(),
        rtol=0, atol=1e-9,
    )


def test_local_pkl_1d_passes_through_local_daily(
    local_pkl_cache, banned_vendor_fetch,
):
    cache_dir, daily_df = local_pkl_cache
    out = builder.fetch_interval_data(
        "TEST", "1d",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
        cache_dir=str(cache_dir),
    )
    # The local-PKL 1d branch returns the local daily Close as-is.
    # T-1 skip is a no-op for 1d. The shared tail performs
    # tz-normalize / sort / float64 / final index normalize but does
    # not change the row count.
    assert len(out) == len(daily_df)
    np.testing.assert_allclose(
        out["Close"].to_numpy(),
        daily_df["Close"].to_numpy(),
        rtol=0, atol=1e-12,
    )


def test_local_pkl_all_five_intervals_share_daily_source(
    local_pkl_cache, banned_vendor_fetch,
):
    """All five timeframes for a given member MUST derive from one
    daily Close series. This is the K=6 MTF launch-path contract's
    "one source per member" rule.

    Verification strategy:
      - 1d library values must equal the daily source values exactly.
      - Each non-daily library's close values must equal the
        pandas-resampled-from-source values exactly (no projection
        past the source). pandas resample labels can land one bucket
        period to the right of the daily source's last date for W-MON
        / MS / QS / YE-DEC conventions; that is a labeling artifact,
        not data projection.
    """
    cache_dir, daily_df = local_pkl_cache
    libraries = {}
    for interval in ("1d", "1wk", "1mo", "3mo", "1y"):
        lib = builder.generate_signals_for_interval(
            "TEST", interval,
            source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
            cache_dir=str(cache_dir),
        )
        assert lib is not None, f"library is None for {interval}"
        libraries[interval] = lib

    # 1d library exactly matches the daily source values.
    one_d_close = libraries["1d"]["close"]
    np.testing.assert_allclose(
        np.asarray(one_d_close, dtype=np.float64),
        daily_df["Close"].to_numpy(),
        rtol=0, atol=1e-12,
    )

    # Each non-daily library's close values match the expected
    # resample-from-source values exactly on shared labels.
    expected_by_interval = {
        "1wk": daily_df[["Close"]].resample("W-MON").last().dropna(),
        "1mo": daily_df[["Close"]].resample("MS").first().dropna(),
        "3mo": daily_df[["Close"]].resample("QS").first().dropna(),
        "1y":  daily_df[["Close"]].resample("YE-DEC").last().dropna(),
    }
    for interval, expected in expected_by_interval.items():
        lib = libraries[interval]
        got_dates = pd.DatetimeIndex(lib["dates"])
        got_close = np.asarray(lib["close"], dtype=np.float64)
        # Align by label and compare values.
        common = got_dates.intersection(expected.index)
        assert len(common) > 0, (
            f"{interval} produced no overlapping labels with the "
            f"daily-source resample expectation"
        )
        # Build value-aligned views.
        got_series = pd.Series(got_close, index=got_dates)
        np.testing.assert_allclose(
            got_series.loc[common].to_numpy(),
            expected.loc[common, "Close"].to_numpy(),
            rtol=0, atol=1e-9,
        )


# ---------------------------------------------------------------------------
# 3. Local-PKL key-set parity with other modes (non-daily interval)
# ---------------------------------------------------------------------------


def test_local_pkl_library_key_set_matches_legacy(
    local_pkl_cache, monkeypatch,
):
    """For the same non-daily interval, the local-PKL library must
    carry the same canonical key set as a legacy_native library.
    Schema labels and persisted shape are stable across modes."""
    cache_dir, daily_df = local_pkl_cache

    # Build a legacy-native library using the df= injection seam so we
    # do not need yfinance. This produces the exact key set the legacy
    # path produces because generate_signals_for_interval is the same
    # downstream path regardless of how df was sourced.
    weekly_df = (
        daily_df[["Close"]].resample("W-MON").last().dropna()
    )
    legacy = builder.generate_signals_for_interval(
        "TEST", "1wk", df=weekly_df.copy(),
    )
    local = builder.generate_signals_for_interval(
        "TEST", "1wk",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
        cache_dir=str(cache_dir),
    )
    assert legacy is not None and local is not None
    assert set(legacy.keys()) == set(local.keys()), (
        f"key-set drift between legacy and local-PKL modes - "
        f"legacy-only={set(legacy) - set(local)!r}, "
        f"local-only={set(local) - set(legacy)!r}"
    )
    # Sanity: per-bar arrays are 1:1 aligned in both libraries.
    for k in ("dates", "signals", "close"):
        assert len(legacy[k]) == len(legacy["dates"])
        assert len(local[k]) == len(local["dates"])


# ---------------------------------------------------------------------------
# 4. Daily save-protection unchanged regardless of source_mode
# ---------------------------------------------------------------------------


def test_local_pkl_1d_save_blocked_by_default(
    local_pkl_cache, monkeypatch,
):
    """Even when the local-PKL launch path generates a 1d library,
    save_signal_library MUST refuse to persist it without explicit
    opt-in. The two-factor guard (force_overwrite or
    CONFLUENCE_ALLOW_DAILY_OVERWRITE=1) is not weakened by the new
    source mode."""
    cache_dir, _daily_df = local_pkl_cache
    # Make sure the env-var opt-in is NOT set.
    monkeypatch.delenv("CONFLUENCE_ALLOW_DAILY_OVERWRITE", raising=False)
    lib = builder.generate_signals_for_interval(
        "TEST", "1d",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
        cache_dir=str(cache_dir),
    )
    assert lib is not None
    with pytest.raises(ValueError, match="overwrite daily library"):
        builder.save_signal_library(lib, "1d")


def test_local_pkl_1d_save_allowed_with_force_overwrite(
    local_pkl_cache, monkeypatch, tmp_path,
):
    """The force_overwrite opt-in is the documented bypass. With it
    set, save_signal_library writes the 1d library to disk."""
    cache_dir, _daily_df = local_pkl_cache
    out_dir = tmp_path / "stable_out"
    monkeypatch.setattr(builder, "SIGNAL_LIBRARY_DIR", str(out_dir))
    lib = builder.generate_signals_for_interval(
        "TEST", "1d",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
        cache_dir=str(cache_dir),
    )
    assert lib is not None
    saved = builder.save_signal_library(
        lib, "1d", force_overwrite=True,
    )
    assert Path(saved).exists()
    assert Path(saved).name == "TEST_stable_v1_0_0.pkl"


def test_local_pkl_1d_save_allowed_with_env_var(
    local_pkl_cache, monkeypatch, tmp_path,
):
    """The env-var opt-in is the second documented bypass."""
    cache_dir, _daily_df = local_pkl_cache
    out_dir = tmp_path / "stable_out_env"
    monkeypatch.setattr(builder, "SIGNAL_LIBRARY_DIR", str(out_dir))
    monkeypatch.setenv("CONFLUENCE_ALLOW_DAILY_OVERWRITE", "1")
    lib = builder.generate_signals_for_interval(
        "TEST", "1d",
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
        cache_dir=str(cache_dir),
    )
    assert lib is not None
    saved = builder.save_signal_library(lib, "1d")
    assert Path(saved).exists()


# ---------------------------------------------------------------------------
# 5. Output filename compatibility
# ---------------------------------------------------------------------------


def test_local_pkl_nondaily_filename_matches_legacy_convention(
    local_pkl_cache, monkeypatch, tmp_path,
):
    """Non-daily local-PKL output must use the same filename as the
    legacy-native non-daily output: <TICKER>_stable_v1_0_0_<INTERVAL>.pkl."""
    cache_dir, _daily_df = local_pkl_cache
    out_dir = tmp_path / "stable_out_nd"
    monkeypatch.setattr(builder, "SIGNAL_LIBRARY_DIR", str(out_dir))
    for interval in ("1wk", "1mo", "3mo", "1y"):
        lib = builder.generate_signals_for_interval(
            "TEST", interval,
            source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
            cache_dir=str(cache_dir),
        )
        assert lib is not None
        saved = builder.save_signal_library(lib, interval)
        assert Path(saved).name == f"TEST_stable_v1_0_0_{interval}.pkl"


# ---------------------------------------------------------------------------
# 6. df= injection seam still bypasses fetch
# ---------------------------------------------------------------------------


def test_df_injection_skips_fetch_under_local_pkl_mode(
    banned_vendor_fetch, tmp_path,
):
    """When df= is supplied, no fetch path runs. The local-PKL helper
    must not be exercised either (no cache_dir, no PKL on disk)."""
    weekly_df = pd.DataFrame(
        {"Close": np.arange(20, dtype=np.float64)},
        index=pd.date_range("2024-01-01", periods=20, freq="W-MON"),
    )
    # No cache_dir is supplied; the df= seam must short-circuit the
    # entire source-resolution path including the local-PKL loader.
    lib = builder.generate_signals_for_interval(
        "TEST", "1wk", df=weekly_df.copy(),
        source_mode=builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED,
    )
    assert lib is not None
    assert len(lib["dates"]) == len(weekly_df)


# ---------------------------------------------------------------------------
# 7. CLI flag wiring
# ---------------------------------------------------------------------------


def test_cli_accepts_local_pkl_mode_and_cache_dir(local_pkl_cache, monkeypatch, tmp_path):
    """The CLI must accept ``--source-mode launch_path_local_pkl_resampled``
    and ``--cache-dir <path>`` and route them through to the builder.
    We exercise the argparse choices wiring by invoking main() with a
    monkeypatched generate/save pipeline to avoid disk writes."""
    cache_dir, daily_df = local_pkl_cache
    captured = {}

    def _fake_generate(
        ticker, interval, *,
        source_mode=builder.SOURCE_MODE_LEGACY_NATIVE,
        cache_dir=None,
        df=None,
    ):
        captured.setdefault("calls", []).append({
            "ticker": ticker, "interval": interval,
            "source_mode": source_mode, "cache_dir": cache_dir,
        })
        return {"ticker": ticker, "interval": interval}

    def _fake_save(library, interval, force_overwrite=False):
        return f"<saved {interval}>"

    monkeypatch.setattr(
        builder, "generate_signals_for_interval", _fake_generate,
    )
    monkeypatch.setattr(builder, "save_signal_library", _fake_save)
    monkeypatch.setattr(
        sys, "argv",
        [
            "multi_timeframe_builder",
            "--ticker", "TEST",
            "--intervals", "1wk,1mo",
            "--source-mode", "launch_path_local_pkl_resampled",
            "--cache-dir", str(cache_dir),
        ],
    )
    builder.main()
    assert captured["calls"], "main() did not invoke generate"
    for call in captured["calls"]:
        assert (
            call["source_mode"]
            == builder.SOURCE_MODE_LAUNCH_PATH_LOCAL_PKL_RESAMPLED
        )
        assert call["cache_dir"] == str(cache_dir)


def test_cli_rejects_old_pr337_mode_value(monkeypatch, tmp_path):
    """The CLI's --source-mode choices must NOT include the old
    pre-rename PR #337 value. argparse raises SystemExit on invalid
    choice."""
    monkeypatch.setattr(
        sys, "argv",
        [
            "multi_timeframe_builder",
            "--ticker", "TEST",
            "--intervals", "1wk",
            "--source-mode", "launch_path_daily_resampled",
        ],
    )
    with pytest.raises(SystemExit):
        builder.main()


# ---------------------------------------------------------------------------
# 8. Local-PKL helpers exist and are isolated from sandbox builder
# ---------------------------------------------------------------------------


def test_local_pkl_helpers_live_inside_production_builder():
    """The helpers must live in the production builder, not be
    imported from the sandbox module."""
    assert hasattr(builder, "_load_daily_close_from_local_pkl")
    assert hasattr(builder, "_resample_local_daily_to_interval")
    assert hasattr(builder, "DEFAULT_CACHE_RESULTS_DIR")
    import ast
    src = Path(builder.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found_modules: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found_modules.append(node.module)
    assert not any(
        "multi_timeframe_sandbox_builder" in m for m in found_modules
    ), (
        f"production builder must not import sandbox: {found_modules!r}"
    )
