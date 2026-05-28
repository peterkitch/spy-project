"""Tests for the K=6 MTF history producer.

Pins the locked rules from
md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md:

  - K=6 stack resolution (six members + [D]/[I] protocols, K=6 used
    even if selected_build.selected_k differs).
  - [D] / [I] protocol application.
  - Lenient active-signal unanimity combine across exactly six
    protocol-adjusted member signals.
  - Participation-depth provenance counts sum to 6.
  - Secondary source resolution: parquet -> csv -> cache/results PKL
    fallback; no provider fetch.
  - history_as_of_date = min(secondary close end, six member 1d ends).
  - 1d slot is not forward-filled (missing exact-date member 1d
    signals count UNAVAILABLE / neutral).
  - Non-daily slots forward-fill the combined stream onto the capped
    secondary daily calendar.
  - No emitted bar after history_as_of_date.
  - No source_date exceeds the bar date or history_as_of_date.
  - Artifact schema carries all required top-level / k6_stack /
    per-bar fields plus cap metadata.
  - Fail-closed on missing/malformed inputs.
  - CLI continues across secondaries, exits non-zero on any failure.
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Tuple

import numpy as np
import pandas as pd
import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import k6_mtf_history_producer as producer  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


_SECONDARY = "TGT"
_MEMBERS_RAW = [
    "AAA[D]", "BBB[I]", "CCC[D]", "DDD[I]", "EEE[D]", "FFF[I]",
]
_MEMBER_TICKERS = [m.replace("[D]", "").replace("[I]", "") for m in _MEMBERS_RAW]


def _make_calendar(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq="B")


def _write_member_library(
    stable_dir: Path, ticker: str, interval: str,
    dates: pd.DatetimeIndex, signals: List[str],
) -> Path:
    if interval == "1d":
        name = f"{ticker}_stable_v1_0_0.pkl"
    else:
        name = f"{ticker}_stable_v1_0_0_{interval}.pkl"
    path = stable_dir / name
    payload = {
        "ticker": ticker,
        "interval": interval,
        "engine_version": "1.0.0",
        "max_sma_day": 114,
        "dates": list(dates),
        "date_index": list(dates),
        "signals": list(signals),
        "primary_signals": list(signals),
        "primary_signals_int8": [
            1 if s == "Buy" else -1 if s == "Short" else 0
            for s in signals
        ],
        "close": [100.0 + i for i in range(len(dates))],
        "daily_top_buy_pairs": {},
        "daily_top_short_pairs": {},
        "top_buy_pair": (1, 2),
        "top_short_pair": (2, 1),
    }
    with open(path, "wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def _write_secondary_csv(
    price_cache_dir: Path, secondary: str,
    dates: pd.DatetimeIndex, closes: List[float],
) -> Path:
    path = price_cache_dir / f"{secondary}.csv"
    df = pd.DataFrame({"Date": [d.strftime("%Y-%m-%d") for d in dates],
                       "Close": closes})
    df.to_csv(path, index=False)
    return path


def _write_secondary_parquet(
    price_cache_dir: Path, secondary: str,
    dates: pd.DatetimeIndex, closes: List[float],
) -> Path:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow not available; parquet test skipped")
    path = price_cache_dir / f"{secondary}.parquet"
    df = pd.DataFrame({"Date": list(dates), "Close": closes})
    df.to_parquet(path, index=False)
    return path


def _write_secondary_pkl(
    cache_dir: Path, secondary: str,
    dates: pd.DatetimeIndex, closes: List[float],
) -> Path:
    path = cache_dir / f"{secondary}_precomputed_results.pkl"
    pre = pd.DataFrame({"Close": closes}, index=dates)
    payload = {"preprocessed_data": pre}
    with open(path, "wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def _write_stackbuilder_inputs(
    stackbuilder_root: Path, secondary: str,
    members_raw: List[str],
    *, selected_k: int = 6,
) -> Tuple[Path, Path]:
    sec_dir = stackbuilder_root / secondary
    sec_dir.mkdir(parents=True, exist_ok=True)
    run_dir = sec_dir / "runs" / "run_synthetic"
    run_dir.mkdir(parents=True, exist_ok=True)
    selected_build_path = sec_dir / "selected_build.json"
    selected_build_path.write_text(
        json.dumps({
            "selected_k": selected_k,
            "selected_run_dir": str(run_dir).replace("\\", "/"),
        }),
        encoding="utf-8",
    )
    combo_path = run_dir / "combo_k=6.json"
    combo_path.write_text(
        json.dumps({"K": 6, "Members": members_raw}),
        encoding="utf-8",
    )
    return selected_build_path, combo_path


def _build_full_fixture(
    tmp_path: Path,
    *,
    n_days: int = 60,
    secondary_extra_tail: int = 0,
    member_short_tail: int = 0,
    member_signal_pattern: str = "Buy",
    secondary_source: str = "csv",
):
    """Create a complete synthetic fixture (stackbuilder + member libs
    + secondary close) under ``tmp_path``. Returns a dict of paths and
    expected values used by tests."""
    stable_dir = tmp_path / "stable"
    cache_dir = tmp_path / "cache_results"
    price_cache_dir = tmp_path / "price_cache_daily"
    stackbuilder_root = tmp_path / "stackbuilder"
    for d in (stable_dir, cache_dir, price_cache_dir, stackbuilder_root):
        d.mkdir(parents=True, exist_ok=True)

    base_dates = _make_calendar("2024-01-01", n_days)

    # Secondary calendar: base + extra_tail days. Tests use extra_tail to
    # force history_as_of_date to be capped by member 1d ends rather
    # than by secondary close end.
    sec_dates = _make_calendar("2024-01-01", n_days + secondary_extra_tail)
    sec_closes = [100.0 + i * 0.1 for i in range(len(sec_dates))]

    # Member calendar: base - member_short_tail (so all six members
    # share the same 1d end). Member non-daily uses the same trimmed
    # daily span resampled by the test fixture builder.
    member_dates = _make_calendar("2024-01-01", n_days - member_short_tail)

    # Build per-member per-timeframe signals.
    sigs = {
        "1d": [member_signal_pattern] * len(member_dates),
        "1wk": ["Buy"] * 12,
        "1mo": ["Buy"] * 3,
        "3mo": ["Buy"] * 2,
        "1y": ["Buy"] * 1,
    }
    member_paths: dict = {}
    for ticker in _MEMBER_TICKERS:
        member_paths[ticker] = {}
        for tf in producer.TIMEFRAME_SET:
            if tf == "1d":
                d = member_dates
                s = sigs["1d"]
            else:
                if tf == "1wk":
                    d = pd.date_range("2024-01-01", periods=12, freq="W-MON")
                    s = sigs["1wk"]
                elif tf == "1mo":
                    d = pd.date_range("2024-01-01", periods=3, freq="MS")
                    s = sigs["1mo"]
                elif tf == "3mo":
                    d = pd.date_range("2024-01-01", periods=2, freq="QS")
                    s = sigs["3mo"]
                else:  # 1y
                    d = pd.date_range("2024-12-31", periods=1, freq="YE-DEC")
                    s = sigs["1y"]
            p = _write_member_library(stable_dir, ticker, tf, d, s)
            member_paths[ticker][tf] = p

    if secondary_source == "csv":
        _write_secondary_csv(price_cache_dir, _SECONDARY, sec_dates, sec_closes)
    elif secondary_source == "parquet":
        _write_secondary_parquet(price_cache_dir, _SECONDARY, sec_dates, sec_closes)
    elif secondary_source == "pkl":
        _write_secondary_pkl(cache_dir, _SECONDARY, sec_dates, sec_closes)
    else:
        raise ValueError(secondary_source)

    selected_build, combo = _write_stackbuilder_inputs(
        stackbuilder_root, _SECONDARY, _MEMBERS_RAW,
    )

    return {
        "stable_dir": stable_dir,
        "cache_dir": cache_dir,
        "price_cache_dir": price_cache_dir,
        "stackbuilder_root": stackbuilder_root,
        "selected_build_path": selected_build,
        "combo_path": combo,
        "sec_dates": sec_dates,
        "sec_closes": sec_closes,
        "member_dates": member_dates,
        "member_paths": member_paths,
    }


# ---------------------------------------------------------------------------
# 1. K=6 stack resolution
# ---------------------------------------------------------------------------


def test_k6_stack_resolution_parses_six_members(tmp_path):
    fx = _build_full_fixture(tmp_path)
    stack = producer.resolve_k6_stack(
        _SECONDARY,
        stackbuilder_root=str(fx["stackbuilder_root"]),
    )
    assert stack.secondary == _SECONDARY
    assert len(stack.members) == 6
    tickers = [m.ticker for m in stack.members]
    protocols = [m.protocol for m in stack.members]
    assert tickers == _MEMBER_TICKERS
    assert protocols == ["D", "I", "D", "I", "D", "I"]


def test_k6_stack_uses_k6_even_when_selected_k_differs(tmp_path):
    fx = _build_full_fixture(tmp_path)
    # Override selected_build to point at a different selected_k while
    # the same combo_k=6.json remains in the run dir.
    sb = json.loads(fx["selected_build_path"].read_text())
    sb["selected_k"] = 3
    fx["selected_build_path"].write_text(json.dumps(sb))
    stack = producer.resolve_k6_stack(
        _SECONDARY,
        stackbuilder_root=str(fx["stackbuilder_root"]),
    )
    assert len(stack.members) == 6  # K=6 honored regardless


def test_k6_stack_fails_closed_on_missing_selected_build(tmp_path):
    (tmp_path / "stackbuilder").mkdir(parents=True, exist_ok=True)
    with pytest.raises(
        producer.K6StackResolutionError,
        match="selected_build.json missing",
    ):
        producer.resolve_k6_stack(
            "GHOST",
            stackbuilder_root=str(tmp_path / "stackbuilder"),
        )


def test_k6_stack_fails_closed_on_missing_combo(tmp_path):
    sb_root = tmp_path / "stackbuilder"
    sec_dir = sb_root / _SECONDARY
    run_dir = sec_dir / "runs" / "run_x"
    run_dir.mkdir(parents=True)
    (sec_dir / "selected_build.json").write_text(json.dumps({
        "selected_k": 6,
        "selected_run_dir": str(run_dir).replace("\\", "/"),
    }))
    # combo_k=6.json deliberately absent
    with pytest.raises(
        producer.K6StackResolutionError, match="combo_k=6.json missing",
    ):
        producer.resolve_k6_stack(
            _SECONDARY, stackbuilder_root=str(sb_root),
        )


def test_k6_stack_fails_closed_on_wrong_member_count(tmp_path):
    sb_root = tmp_path / "stackbuilder"
    sec_dir = sb_root / _SECONDARY
    run_dir = sec_dir / "runs" / "run_x"
    run_dir.mkdir(parents=True)
    (sec_dir / "selected_build.json").write_text(json.dumps({
        "selected_k": 6,
        "selected_run_dir": str(run_dir).replace("\\", "/"),
    }))
    (run_dir / "combo_k=6.json").write_text(json.dumps({
        "K": 6,
        "Members": ["AAA[D]", "BBB[I]", "CCC[D]"],  # only 3
    }))
    with pytest.raises(
        producer.K6StackResolutionError,
        match="exactly six members",
    ):
        producer.resolve_k6_stack(
            _SECONDARY, stackbuilder_root=str(sb_root),
        )


def test_k6_stack_fails_closed_on_member_missing_protocol(tmp_path):
    sb_root = tmp_path / "stackbuilder"
    sec_dir = sb_root / _SECONDARY
    run_dir = sec_dir / "runs" / "run_x"
    run_dir.mkdir(parents=True)
    (sec_dir / "selected_build.json").write_text(json.dumps({
        "selected_k": 6,
        "selected_run_dir": str(run_dir).replace("\\", "/"),
    }))
    (run_dir / "combo_k=6.json").write_text(json.dumps({
        "K": 6,
        "Members": ["AAA[D]", "BBB", "CCC[D]", "DDD[I]", "EEE[D]", "FFF[I]"],
    }))
    with pytest.raises(
        producer.K6StackResolutionError,
        match=r"missing \[D\]/\[I\]",
    ):
        producer.resolve_k6_stack(
            _SECONDARY, stackbuilder_root=str(sb_root),
        )


# ---------------------------------------------------------------------------
# 2. Protocol application
# ---------------------------------------------------------------------------


def test_protocol_D_preserves():
    assert producer.apply_protocol("BUY", "D") == "BUY"
    assert producer.apply_protocol("SHORT", "D") == "SHORT"
    assert producer.apply_protocol("NONE", "D") == "NONE"
    assert producer.apply_protocol("UNAVAILABLE", "D") == "UNAVAILABLE"


def test_protocol_I_inverts_active_only():
    assert producer.apply_protocol("BUY", "I") == "SHORT"
    assert producer.apply_protocol("SHORT", "I") == "BUY"
    assert producer.apply_protocol("NONE", "I") == "NONE"
    assert producer.apply_protocol("UNAVAILABLE", "I") == "UNAVAILABLE"


def test_protocol_rejects_unknown():
    with pytest.raises(ValueError, match="unknown protocol"):
        producer.apply_protocol("BUY", "X")


# ---------------------------------------------------------------------------
# 3. Lenient combine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "six,expected,expected_counts",
    [
        (["BUY"] * 6, "BUY", (6, 0, 0)),
        (["SHORT"] * 6, "SHORT", (0, 6, 0)),
        (["BUY", "BUY", "NONE", "NONE", "NONE", "NONE"], "BUY", (2, 0, 4)),
        (["SHORT", "SHORT", "NONE", "NONE", "NONE", "NONE"], "SHORT", (0, 2, 4)),
        (["BUY", "NONE", "NONE", "NONE", "NONE", "NONE"], "BUY", (1, 0, 5)),
        (["SHORT", "NONE", "NONE", "NONE", "NONE", "NONE"], "SHORT", (0, 1, 5)),
        (["BUY", "SHORT", "NONE", "NONE", "NONE", "NONE"], "NONE", (1, 1, 4)),
        (["NONE"] * 6, "NONE", (0, 0, 6)),
        (["UNAVAILABLE"] * 6, "NONE", (0, 0, 6)),
        (
            ["BUY", "BUY", "SHORT", "UNAVAILABLE", "NONE", "NONE"],
            "NONE", (2, 1, 3),
        ),
    ],
)
def test_combine_six_cases(six, expected, expected_counts):
    combined, buy, short, neutral = producer.combine_six(six)
    assert combined == expected
    assert (buy, short, neutral) == expected_counts
    # Invariant: sums to 6
    assert buy + short + neutral == 6


def test_combine_six_rejects_wrong_arity():
    with pytest.raises(ValueError, match="requires 6 signals"):
        producer.combine_six(["BUY"] * 5)


def test_normalize_signal_vocabulary():
    assert producer._normalize_signal("Buy") == "BUY"
    assert producer._normalize_signal("BUY") == "BUY"
    assert producer._normalize_signal("short") == "SHORT"
    assert producer._normalize_signal("None") == "NONE"
    assert producer._normalize_signal("Cash") == "NONE"
    assert producer._normalize_signal("") == "NONE"
    assert producer._normalize_signal(None) == "NONE"
    assert producer._normalize_signal(1) == "BUY"
    assert producer._normalize_signal(-1) == "SHORT"
    assert producer._normalize_signal(0) == "NONE"
    assert producer._normalize_signal("unavailable") == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 4. Secondary source resolution chain
# ---------------------------------------------------------------------------


def test_secondary_resolution_prefers_csv_when_parquet_absent(tmp_path):
    pcd = tmp_path / "price_cache_daily"
    cd = tmp_path / "cache_results"
    pcd.mkdir()
    cd.mkdir()
    dates = _make_calendar("2024-01-01", 30)
    _write_secondary_csv(pcd, _SECONDARY, dates, list(range(30)))
    series, path, kind = producer.load_secondary_close(
        _SECONDARY,
        price_cache_dir=str(pcd),
        cache_dir=str(cd),
    )
    assert kind == "csv"
    assert path.endswith(f"{_SECONDARY}.csv")
    assert len(series) == 30


def test_secondary_resolution_prefers_parquet_over_csv(tmp_path):
    pyarrow = pytest.importorskip("pyarrow")
    pcd = tmp_path / "price_cache_daily"
    cd = tmp_path / "cache_results"
    pcd.mkdir()
    cd.mkdir()
    dates = _make_calendar("2024-01-01", 30)
    _write_secondary_parquet(pcd, _SECONDARY, dates, list(range(30)))
    _write_secondary_csv(pcd, _SECONDARY, dates, list(range(30, 60)))
    series, path, kind = producer.load_secondary_close(
        _SECONDARY,
        price_cache_dir=str(pcd),
        cache_dir=str(cd),
    )
    assert kind == "parquet"


def test_secondary_resolution_falls_back_to_pkl(tmp_path):
    pcd = tmp_path / "price_cache_daily"
    cd = tmp_path / "cache_results"
    pcd.mkdir()
    cd.mkdir()
    dates = _make_calendar("2024-01-01", 30)
    _write_secondary_pkl(cd, _SECONDARY, dates, list(range(30)))
    series, path, kind = producer.load_secondary_close(
        _SECONDARY,
        price_cache_dir=str(pcd),
        cache_dir=str(cd),
    )
    assert kind == "pkl_fallback"
    assert len(series) == 30


def test_secondary_resolution_fails_closed_when_no_source(tmp_path):
    pcd = tmp_path / "price_cache_daily"
    cd = tmp_path / "cache_results"
    pcd.mkdir()
    cd.mkdir()
    with pytest.raises(producer.SecondarySourceError):
        producer.load_secondary_close(
            "GHOST",
            price_cache_dir=str(pcd),
            cache_dir=str(cd),
        )


def test_producer_does_not_read_secondary_signal_library(tmp_path):
    """Producer must NOT read signal_library/data/stable/<SEC>_*.pkl.

    We assert structurally: build a full fixture for the secondary
    TGT, and confirm the resolved source path is in price_cache or
    cache/results, never in the stable signal-library directory.
    """
    fx = _build_full_fixture(tmp_path)
    series, path, kind = producer.load_secondary_close(
        _SECONDARY,
        price_cache_dir=str(fx["price_cache_dir"]),
        cache_dir=str(fx["cache_dir"]),
    )
    assert "signal_library" not in path
    assert kind in ("csv", "parquet", "pkl_fallback")


# ---------------------------------------------------------------------------
# 5. As-of cap
# ---------------------------------------------------------------------------


def test_compute_history_as_of_picks_min_of_secondary_and_members():
    sec = pd.Timestamp("2024-05-20")
    members = [
        pd.Timestamp("2024-05-21"),
        pd.Timestamp("2024-05-19"),  # smallest
        pd.Timestamp("2024-05-22"),
        pd.Timestamp("2024-05-20"),
        pd.Timestamp("2024-05-21"),
        pd.Timestamp("2024-05-20"),
    ]
    assert producer.compute_history_as_of(sec, members) == pd.Timestamp("2024-05-19")


def test_compute_history_as_of_can_be_capped_by_secondary():
    sec = pd.Timestamp("2024-05-15")
    members = [pd.Timestamp("2024-05-20")] * 6
    assert producer.compute_history_as_of(sec, members) == sec


# ---------------------------------------------------------------------------
# 6. End-to-end build: emits no bar after history_as_of_date
# ---------------------------------------------------------------------------


def test_build_no_bar_after_history_as_of_date(tmp_path):
    """Secondary calendar extends 10 days past member 1d ends ->
    history_as_of_date must equal the member 1d end and bars must stop
    there."""
    fx = _build_full_fixture(tmp_path, secondary_extra_tail=10)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        generated_at_utc="2026-05-28T00:00:00Z",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    member_1d_end = fx["member_dates"][-1]
    sec_end = fx["sec_dates"][-1]
    assert artifact["history_as_of_date"] == member_1d_end.strftime("%Y-%m-%d")
    last_bar_date = pd.Timestamp(artifact["bars"][-1]["date_utc"])
    assert last_bar_date == member_1d_end
    assert last_bar_date < sec_end
    # Cap metadata exposes the truncation count.
    assert artifact["source_paths"]["as_of_truncation"][
        "trimmed_secondary_bars"
    ] == 10


def test_build_capped_by_secondary_close_end(tmp_path):
    """Secondary calendar ends before all six member 1d ends ->
    history_as_of_date is secondary close end."""
    fx = _build_full_fixture(
        tmp_path, secondary_extra_tail=0, member_short_tail=0,
    )
    # Manually trim the secondary CSV to a shorter horizon.
    csv_path = fx["price_cache_dir"] / f"{_SECONDARY}.csv"
    df = pd.read_csv(csv_path)
    df = df.iloc[: len(df) - 5]
    df.to_csv(csv_path, index=False)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    expected_end = pd.Timestamp(df["Date"].iloc[-1])
    assert artifact["history_as_of_date"] == expected_end.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 7. 1d not forward-filled; non-daily forward-fill semantics
# ---------------------------------------------------------------------------


def test_one_d_slot_not_forward_filled(tmp_path):
    """Create a member 1d library that is missing a date in the middle.
    The 1d slot on that date must reflect UNAVAILABLE for that member,
    not the previous-date carry-forward."""
    fx = _build_full_fixture(tmp_path)
    # Hole out the middle date on every member's 1d library.
    hole_date = fx["member_dates"][10]
    for ticker in _MEMBER_TICKERS:
        path = fx["member_paths"][ticker]["1d"]
        with open(path, "rb") as fh:
            lib = pickle.load(fh)
        dates = list(lib["dates"])
        signals = list(lib["signals"])
        idx = dates.index(hole_date)
        del dates[idx]
        del signals[idx]
        lib["dates"] = dates
        lib["date_index"] = dates
        lib["signals"] = signals
        lib["primary_signals"] = signals
        lib["primary_signals_int8"] = [
            1 if s == "Buy" else -1 if s == "Short" else 0
            for s in signals
        ]
        lib["close"] = lib["close"][:len(dates)]
        with open(path, "wb") as fh:
            pickle.dump(lib, fh, protocol=pickle.HIGHEST_PROTOCOL)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    bar_for_hole = next(
        b for b in artifact["bars"]
        if b["date_utc"] == hole_date.strftime("%Y-%m-%d")
    )
    avail = bar_for_hole["availability"]["1d"]
    # No member has a signal on this date -> neutral_count must be 6.
    assert avail["active_buy_count"] == 0
    assert avail["active_short_count"] == 0
    assert avail["neutral_count"] == 6
    assert avail["status"] == "unavailable"
    # And the combined 1d is NONE.
    assert bar_for_hole["snapshot"]["1d"] == "NONE"


def test_non_daily_forward_fill_uses_most_recent_at_or_before(tmp_path):
    """For non-daily slots, every bar's source_date must be the most
    recent timeframe source date <= bar date, and never exceed the
    bar or history_as_of_date."""
    fx = _build_full_fixture(tmp_path)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    as_of = pd.Timestamp(artifact["history_as_of_date"])
    for bar in artifact["bars"]:
        bd = pd.Timestamp(bar["date_utc"])
        for tf in ("1wk", "1mo", "3mo", "1y"):
            sd = bar["source_dates"][tf]
            if sd is None:
                # status must be unavailable and counts (0,0,6).
                avail = bar["availability"][tf]
                assert avail["status"] == "unavailable"
                assert avail["active_buy_count"] == 0
                assert avail["active_short_count"] == 0
                assert avail["neutral_count"] == 6
                continue
            sd_ts = pd.Timestamp(sd)
            assert sd_ts <= bd, (
                f"{tf} source_date {sd_ts} exceeds bar {bd}"
            )
            assert sd_ts <= as_of, (
                f"{tf} source_date {sd_ts} exceeds as_of {as_of}"
            )


# ---------------------------------------------------------------------------
# 8. Participation counts always sum to six
# ---------------------------------------------------------------------------


def test_participation_counts_sum_to_six_everywhere(tmp_path):
    fx = _build_full_fixture(tmp_path)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    for bar in artifact["bars"]:
        for tf, avail in bar["availability"].items():
            total = (
                avail["active_buy_count"]
                + avail["active_short_count"]
                + avail["neutral_count"]
            )
            assert total == 6, (
                f"bar {bar['date_utc']} tf {tf} counts sum to "
                f"{total}, expected 6"
            )


# ---------------------------------------------------------------------------
# 9. Artifact schema
# ---------------------------------------------------------------------------


def test_artifact_schema_top_level_fields(tmp_path):
    fx = _build_full_fixture(tmp_path)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    required = [
        "schema_version", "generated_at_utc", "run_id", "secondary",
        "history_as_of_date", "source_paths", "k6_stack",
        "timeframe_set", "bars", "issues",
    ]
    for k in required:
        assert k in artifact, f"missing top-level field {k!r}"
    assert artifact["schema_version"] == "k6_mtf_history_v1"
    assert artifact["secondary"] == _SECONDARY
    assert artifact["timeframe_set"] == list(producer.TIMEFRAME_SET)
    assert isinstance(artifact["issues"], list)


def test_artifact_k6_stack_fields(tmp_path):
    fx = _build_full_fixture(tmp_path)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    stack = artifact["k6_stack"]
    for k in (
        "selected_build_path", "selected_run_dir", "combo_k6_path",
        "members",
    ):
        assert k in stack
    assert len(stack["members"]) == 6
    for m in stack["members"]:
        assert set(m.keys()) == {"ticker", "protocol"}
        assert m["protocol"] in ("D", "I")


def test_artifact_per_bar_fields_complete(tmp_path):
    fx = _build_full_fixture(tmp_path)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    sample = artifact["bars"][len(artifact["bars"]) // 2]
    for k in ("date_utc", "secondary_close", "snapshot",
              "source_dates", "availability"):
        assert k in sample
    for tf in producer.TIMEFRAME_SET:
        assert tf in sample["snapshot"]
        assert tf in sample["source_dates"]
        assert tf in sample["availability"]
        avail = sample["availability"][tf]
        for k in (
            "status", "active_buy_count", "active_short_count",
            "neutral_count",
        ):
            assert k in avail
        assert avail["status"] in (
            "computed", "forward_filled", "unavailable", "missing",
        )


def test_artifact_cap_metadata_present(tmp_path):
    fx = _build_full_fixture(tmp_path, secondary_extra_tail=10)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    sp = artifact["source_paths"]
    assert "secondary_close_end_date" in sp
    assert "member_1d_end_dates" in sp
    assert len(sp["member_1d_end_dates"]) == 6
    truncation = sp["as_of_truncation"]
    for k in (
        "secondary_close_end_date",
        "member_1d_end_dates",
        "selected_history_as_of_date",
        "trimmed_secondary_bars",
    ):
        assert k in truncation


# ---------------------------------------------------------------------------
# 10. Fail-closed: missing member library / malformed library
# ---------------------------------------------------------------------------


def test_fail_closed_missing_member_library(tmp_path):
    fx = _build_full_fixture(tmp_path)
    # Delete one member's 1wk library.
    fx["member_paths"]["AAA"]["1wk"].unlink()
    with pytest.raises(producer.MemberLibraryError, match="missing"):
        producer.build_history_for_secondary(
            _SECONDARY,
            run_id="run_test",
            stackbuilder_root=str(fx["stackbuilder_root"]),
            stable_dir=str(fx["stable_dir"]),
            cache_dir=str(fx["cache_dir"]),
            price_cache_dir=str(fx["price_cache_dir"]),
        )


def test_fail_closed_malformed_member_library(tmp_path):
    fx = _build_full_fixture(tmp_path)
    bad_path = fx["member_paths"]["BBB"]["1mo"]
    bad_payload = {"ticker": "BBB", "interval": "1mo"}  # no dates/signals
    with open(bad_path, "wb") as fh:
        pickle.dump(bad_payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    with pytest.raises(
        producer.MemberLibraryError, match="missing dates/signals",
    ):
        producer.build_history_for_secondary(
            _SECONDARY,
            run_id="run_test",
            stackbuilder_root=str(fx["stackbuilder_root"]),
            stable_dir=str(fx["stable_dir"]),
            cache_dir=str(fx["cache_dir"]),
            price_cache_dir=str(fx["price_cache_dir"]),
        )


def test_fail_closed_missing_secondary_source(tmp_path):
    fx = _build_full_fixture(tmp_path)
    # Remove the secondary csv.
    (fx["price_cache_dir"] / f"{_SECONDARY}.csv").unlink()
    with pytest.raises(
        producer.SecondarySourceError, match="no usable",
    ):
        producer.build_history_for_secondary(
            _SECONDARY,
            run_id="run_test",
            stackbuilder_root=str(fx["stackbuilder_root"]),
            stable_dir=str(fx["stable_dir"]),
            cache_dir=str(fx["cache_dir"]),
            price_cache_dir=str(fx["price_cache_dir"]),
        )


# ---------------------------------------------------------------------------
# 11. Combine behavior with [I] protocol via end-to-end build
# ---------------------------------------------------------------------------


def test_lenient_combine_with_protocol_applied(tmp_path):
    """Three [D] members say BUY, three [I] members say SHORT. After
    protocol, all six are BUY. Combined signal must be BUY with counts
    (6, 0, 0)."""
    fx = _build_full_fixture(
        tmp_path, member_signal_pattern="Buy",
    )
    # Override the [I] members' 1d signals to "Short" so that after
    # inversion they become BUY.
    for ticker in ("BBB", "DDD", "FFF"):
        path = fx["member_paths"][ticker]["1d"]
        with open(path, "rb") as fh:
            lib = pickle.load(fh)
        lib["signals"] = ["Short"] * len(lib["dates"])
        lib["primary_signals"] = lib["signals"]
        lib["primary_signals_int8"] = [-1] * len(lib["dates"])
        with open(path, "wb") as fh:
            pickle.dump(lib, fh, protocol=pickle.HIGHEST_PROTOCOL)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_test",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    last_bar = artifact["bars"][-1]
    avail = last_bar["availability"]["1d"]
    assert avail["active_buy_count"] == 6
    assert avail["active_short_count"] == 0
    assert avail["neutral_count"] == 0
    assert last_bar["snapshot"]["1d"] == "BUY"


# ---------------------------------------------------------------------------
# 12. Write + CLI runner
# ---------------------------------------------------------------------------


def test_write_artifact_path_shape(tmp_path):
    fx = _build_full_fixture(tmp_path)
    artifact = producer.build_history_for_secondary(
        _SECONDARY,
        run_id="run_xyz",
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    out_root = tmp_path / "out_k6_mtf"
    path = producer.write_history_artifact(
        artifact, output_root=str(out_root), run_id="run_xyz",
    )
    assert path == out_root / "run_xyz" / _SECONDARY / "k6_mtf_history.json"
    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "k6_mtf_history_v1"


def test_run_continues_across_failures_and_exits_nonzero(tmp_path):
    """One good secondary plus one missing secondary. The good one
    must write its artifact; the missing one must be recorded as a
    failure; run() returns the summary."""
    fx = _build_full_fixture(tmp_path)
    out_root = tmp_path / "out"
    summary = producer.run(
        [_SECONDARY, "GHOST"],
        run_id="run_x",
        output_root=str(out_root),
        stackbuilder_root=str(fx["stackbuilder_root"]),
        stable_dir=str(fx["stable_dir"]),
        cache_dir=str(fx["cache_dir"]),
        price_cache_dir=str(fx["price_cache_dir"]),
    )
    assert summary["results"][_SECONDARY]["status"] == "ok"
    assert summary["results"]["GHOST"]["status"] == "failed"
    assert len(summary["failures"]) == 1
    assert (out_root / "run_x" / _SECONDARY / "k6_mtf_history.json").exists()
    assert not (out_root / "run_x" / "GHOST" / "k6_mtf_history.json").exists()


def test_cli_exits_zero_on_full_success(tmp_path, monkeypatch):
    fx = _build_full_fixture(tmp_path)
    out_root = tmp_path / "out_cli"
    monkeypatch.setattr(
        sys, "argv",
        [
            "k6_mtf_history_producer",
            "--secondaries", _SECONDARY,
            "--output-root", str(out_root),
            "--stackbuilder-root", str(fx["stackbuilder_root"]),
            "--stable-dir", str(fx["stable_dir"]),
            "--cache-dir", str(fx["cache_dir"]),
            "--price-cache-dir", str(fx["price_cache_dir"]),
            "--run-id", "run_cli",
        ],
    )
    rc = producer.main()
    assert rc == 0
    assert (
        out_root / "run_cli" / _SECONDARY / "k6_mtf_history.json"
    ).exists()


def test_cli_exits_nonzero_when_any_secondary_fails(tmp_path, monkeypatch):
    fx = _build_full_fixture(tmp_path)
    out_root = tmp_path / "out_cli_fail"
    monkeypatch.setattr(
        sys, "argv",
        [
            "k6_mtf_history_producer",
            "--secondaries", f"{_SECONDARY},GHOST",
            "--output-root", str(out_root),
            "--stackbuilder-root", str(fx["stackbuilder_root"]),
            "--stable-dir", str(fx["stable_dir"]),
            "--cache-dir", str(fx["cache_dir"]),
            "--price-cache-dir", str(fx["price_cache_dir"]),
            "--run-id", "run_cli_fail",
        ],
    )
    rc = producer.main()
    assert rc == 1


# ---------------------------------------------------------------------------
# 13. Import side effect freedom
# ---------------------------------------------------------------------------


def test_producer_import_has_no_side_effects():
    """Re-importing the module should not raise or perform IO. We just
    re-import here and assert the module attributes are intact."""
    import importlib
    importlib.reload(producer)
    assert hasattr(producer, "SCHEMA_VERSION")
    assert producer.SCHEMA_VERSION == "k6_mtf_history_v1"
