"""
Post Phase 3 cleanup: pin the confluence equity-build duplicate-index bug.

Partial multi-interval runs can yield duplicated index labels in either
the price series or the confluence DataFrame. Before the fix,
``_build_confluence_strategy_equity`` and the master chart's
``price.reindex(conf_df.index)`` call assumed unique indexes and raised
``ValueError: cannot reindex on an axis with duplicate labels`` on
that input, taking out the whole master chart render.

This test exercises the helper directly with synthetic inputs that
have deliberately duplicated daily indexes, then asserts:

  * No ``ValueError`` is raised.
  * The resulting equity curve has a unique, sorted index.

No Dash, no network, synthetic fixtures only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def _make_conf_df_with_duplicate_index(idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Tiny conf_df with the columns ``_build_confluence_strategy_equity``
    consumes (``dir``). Other columns are filled to mirror real shape."""
    n = len(idx)
    rng = np.random.default_rng(seed=42)
    dirs = rng.choice(np.array(["Buy", "Short", "Flat"], dtype=object), size=n)
    return pd.DataFrame(
        {
            "tier": np.array(["Buy"] * n, dtype=object),
            "alignment_pct": np.linspace(50.0, 75.0, n),
            "active_count": np.full(n, 2, dtype=int),
            "dir": dirs,
        },
        index=idx,
    )


def test_build_confluence_strategy_equity_handles_duplicate_index():
    import confluence

    base = pd.date_range("2024-01-02", periods=10, freq="D")
    # Inject duplicates: D3 and D7 each appear twice.
    dup_idx = pd.DatetimeIndex(
        list(base[:2])
        + [base[2], base[2]]
        + list(base[3:6])
        + [base[6], base[6]]
        + list(base[7:])
    )

    price = pd.Series(
        np.linspace(100.0, 110.0, len(dup_idx)), index=dup_idx, name="Close"
    )
    conf_df = _make_conf_df_with_duplicate_index(dup_idx)

    # Pre-fix this raised ValueError because pct_change/cumsum on a
    # duplicated DatetimeIndex doesn't reindex cleanly. Post-fix the
    # helper dedupes both inputs defensively.
    eq = confluence._build_confluence_strategy_equity(price, conf_df)

    assert isinstance(eq, pd.Series)
    assert eq.index.is_unique, (
        "equity curve index has duplicates; helper failed to dedupe"
    )
    assert eq.index.is_monotonic_increasing, (
        "equity curve index is not sorted; helper failed to sort after dedupe"
    )
    # And the dedupe must have actually shrunk the index — every duplicate
    # collapsed into a single row.
    assert len(eq.index) == len(set(dup_idx)), (
        "equity curve length does not match the unique-index length; "
        "either dedupe did not happen or extra rows were produced"
    )


def test_build_confluence_strategy_equity_unique_input_unchanged():
    """Sanity: the dedupe path must be a no-op when inputs are already
    unique — the cleanup must not change normal-path behavior."""
    import confluence

    idx = pd.date_range("2024-02-01", periods=8, freq="D")
    price = pd.Series(np.linspace(50.0, 58.0, len(idx)), index=idx, name="Close")
    conf_df = _make_conf_df_with_duplicate_index(idx)

    eq = confluence._build_confluence_strategy_equity(price, conf_df)
    assert eq.index.equals(idx)
