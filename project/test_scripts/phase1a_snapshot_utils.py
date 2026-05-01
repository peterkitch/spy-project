"""
Phase 1A snapshot utilities.

Deterministic serialization of arbitrary Python / pandas / numpy objects to
nested tuples of strings, integers, and tagged-float-hex values, suitable
for byte-identical snapshot pinning.

Float pinning uses float.hex() so the snapshot survives round-tripping and
is impervious to display rounding. NaN/+inf/-inf are tagged with stable
canonical labels.

This module performs zero file I/O and zero network access. It must
remain importable without engine modules installed.
"""

import math


NAN_TAG = "fn:nan"
POS_INF_TAG = "fn:inf"
NEG_INF_TAG = "fn:-inf"


def _is_pd_series(x):
    try:
        import pandas as pd
        return isinstance(x, pd.Series)
    except ImportError:
        return False


def _is_pd_dataframe(x):
    try:
        import pandas as pd
        return isinstance(x, pd.DataFrame)
    except ImportError:
        return False


def _is_pd_timestamp(x):
    try:
        import pandas as pd
        return isinstance(x, pd.Timestamp)
    except ImportError:
        return False


def _is_pd_na(x):
    try:
        import pandas as pd
        return x is pd.NA
    except (ImportError, AttributeError):
        return False


def freeze(obj):
    """Recursively convert obj to a deterministic, hashable representation.

    Tuples are returned in the form (kind, payload). The kinds are:
      ('n',)              None
      ('na',)             pandas.NA
      ('b', bool)         bool
      ('i', int)          int
      ('f', '0x1.8p+1')   finite float, payload is float.hex()
      'fn:nan' / 'fn:inf' / 'fn:-inf'   non-finite float (bare strings)
      ('s', str)          str
      ('B', '<hex>')      bytes
      ('ts', '<iso>')     pandas.Timestamp
      ('t', tuple)        tuple of frozen items
      ('l', tuple)        list of frozen items (ordered)
      ('d', tuple)        dict, payload is sorted tuple of (frozen_key, frozen_value)
      ('S', tuple)        pandas.Series, payload is tuple of (frozen_index, frozen_value)
      ('D', tuple)        pandas.DataFrame, payload is tuple of (frozen_col_name, frozen_series)
      ('a', tuple)        numpy.ndarray, payload is tuple of frozen elements (after .tolist())
      ('repr', str)       fallback for unrecognized types
    """
    if obj is None:
        return ("n",)
    if _is_pd_na(obj):
        return ("na",)
    # Check bool BEFORE int because bool is a subclass of int.
    if isinstance(obj, bool):
        return ("b", obj)
    if isinstance(obj, int):
        return ("i", int(obj))
    if isinstance(obj, float):
        if math.isnan(obj):
            return NAN_TAG
        if math.isinf(obj):
            return POS_INF_TAG if obj > 0 else NEG_INF_TAG
        return ("f", obj.hex())
    if isinstance(obj, str):
        return ("s", obj)
    if isinstance(obj, (bytes, bytearray)):
        return ("B", bytes(obj).hex())
    if _is_pd_timestamp(obj):
        return ("ts", obj.isoformat())
    if isinstance(obj, tuple):
        return ("t", tuple(freeze(x) for x in obj))
    if isinstance(obj, list):
        return ("l", tuple(freeze(x) for x in obj))
    if isinstance(obj, dict):
        items = sorted(
            ((freeze(k), freeze(v)) for k, v in obj.items()),
            key=lambda kv: repr(kv[0]),
        )
        return ("d", tuple(items))
    if _is_pd_series(obj):
        rows = []
        for idx, val in obj.items():
            rows.append((freeze(idx), freeze(val)))
        return ("S", tuple(rows))
    if _is_pd_dataframe(obj):
        cols = []
        for col in obj.columns:
            cols.append((freeze(col), freeze(obj[col])))
        return ("D", tuple(cols))
    try:
        import numpy as np
        if isinstance(obj, np.bool_):
            return ("b", bool(obj))
        if isinstance(obj, np.integer):
            return ("i", int(obj))
        if isinstance(obj, np.floating):
            return freeze(float(obj))
        if isinstance(obj, np.ndarray):
            return ("a", tuple(freeze(x) for x in obj.tolist()))
    except ImportError:
        pass
    return ("repr", repr(obj))
