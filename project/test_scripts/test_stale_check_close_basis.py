"""
Phase 1B-2A: stale_check raw-Close coverage (per spec §3 Adj Close removal).

These tests cover the column-handling helper extracted from
stale_check.last_valid_close_date. The helper must read raw `Close`
only; an `Adj Close` column in the input must not influence the
result, and a missing `Close` must produce the documented
"no_valid_close" / "no_data:empty" outcomes.

No network: tests build synthetic DataFrames and pass them directly
to the pure helper.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from stale_check import _last_valid_close_from_history


def _df_with(close_vals, *, adj_close_vals=None, volume_vals=None, dates=None):
    if dates is None:
        dates = pd.date_range("2024-01-02", periods=len(close_vals), freq="B")
    cols = {"Close": list(close_vals)}
    if adj_close_vals is not None:
        cols["Adj Close"] = list(adj_close_vals)
    if volume_vals is not None:
        cols["Volume"] = list(volume_vals)
    return pd.DataFrame(cols, index=pd.DatetimeIndex(dates))


def test_positive_close_column_drives_result():
    df = _df_with(close_vals=[100.0, 101.0, 102.0])
    date, note = _last_valid_close_from_history(df, require_posvol=False)
    assert date == df.index[-1].date()
    assert note == "ok:max"


def test_negative_adj_close_does_not_substitute_for_missing_close():
    # Close column present but the last row is NaN; an Adj Close
    # value on the same row must NOT rescue the helper.
    df = _df_with(
        close_vals=[100.0, 101.0, np.nan],
        adj_close_vals=[100.0, 101.0, 999.0],
    )
    date, note = _last_valid_close_from_history(df, require_posvol=False)
    # Result is the previous valid Close row, not the day with only Adj Close.
    assert date == df.index[1].date()
    assert note == "ok:max"


def test_negative_adj_close_only_dataframe_returns_no_valid_close():
    # Frame has no Close column at all but does have Adj Close;
    # helper must report no_valid_close, not silently fall back.
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    df = pd.DataFrame({"Adj Close": [100.0, 101.0, 102.0]}, index=dates)
    date, note = _last_valid_close_from_history(df, require_posvol=False)
    assert date is None
    assert note == "no_valid_close"


def test_negative_inf_close_treated_as_invalid():
    df = _df_with(close_vals=[100.0, np.inf, np.nan])
    date, note = _last_valid_close_from_history(df, require_posvol=False)
    assert date == df.index[0].date()
    assert note == "ok:max"


def test_volume_filter_only_when_require_posvol():
    df = _df_with(
        close_vals=[100.0, 101.0, 102.0],
        volume_vals=[10, 20, 0],  # last day has zero volume
    )
    # require_posvol=False: last day still wins.
    date, _note = _last_valid_close_from_history(df, require_posvol=False)
    assert date == df.index[-1].date()
    # require_posvol=True: helper drops the zero-volume day.
    date, _note = _last_valid_close_from_history(df, require_posvol=True)
    assert date == df.index[-2].date()


def test_empty_dataframe_returns_no_data_empty():
    date, note = _last_valid_close_from_history(pd.DataFrame(), require_posvol=False)
    assert date is None
    assert note == "no_data:empty"


def test_none_input_returns_no_data_empty():
    date, note = _last_valid_close_from_history(None, require_posvol=False)
    assert date is None
    assert note == "no_data:empty"
