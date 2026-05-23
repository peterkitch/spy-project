"""Phase 6I-76: semantic-parity tests for the StackBuilder-local
combine hot path against ``canonical_scoring.combine_consensus_signals``.

The Phase 6I-76 PR adds ``stackbuilder._combine_signals_fast``, a
vectorized helper that supersedes the canonical Python-loop normalize
path inside ``_combine_signals`` for the K-search hot loop. This file
pins the contract that the fast path produces byte-for-byte the same
labels and index as the canonical reference for every input shape
StackBuilder phase3 can hand it (and for several adversarial shapes
that exercise the normalize edge cases).

These tests do NOT run any engine, do NOT touch canonical output
directories, and do NOT modify ``canonical_scoring.py``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


@pytest.fixture
def sb():
    return importlib.import_module("stackbuilder")


@pytest.fixture
def cs():
    return importlib.import_module("canonical_scoring")


def _idx(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-02", periods=n)


def _series(labels, index=None) -> pd.Series:
    if index is None:
        index = _idx(len(labels))
    return pd.Series(labels, index=index, dtype=object)


def _assert_parity(sb_module, cs_module, members):
    """Assert ``_combine_signals_fast`` matches canonical consensus."""
    fast = sb_module._combine_signals_fast(members)
    ref = cs_module.combine_consensus_signals(members)
    # Same length, same index, same labels.
    assert len(fast) == len(ref)
    assert list(fast.index) == list(ref.index)
    assert list(fast.values) == list(ref.values)
    # Output dtype is acceptable to _captures_from_signals (object/string).
    assert fast.dtype == object


# ---------------------------------------------------------------------------
# Static surface
# ---------------------------------------------------------------------------

def test_module_exports_fast_helper(sb):
    assert hasattr(sb, "_combine_signals_fast")
    assert callable(sb._combine_signals_fast)


def test_module_exports_wired_marker(sb):
    """The ``_COMBINE_SIGNALS_FAST_WIRED`` flag is True so the harness
    can detect wiring without re-importing."""
    assert getattr(sb, "_COMBINE_SIGNALS_FAST_WIRED", False) is True


def test_public_combine_signals_still_exists(sb):
    """Existing public ``_combine_signals`` name is preserved for any
    out-of-tree caller; the body now routes through the fast helper."""
    assert hasattr(sb, "_combine_signals")
    assert callable(sb._combine_signals)


def test_canonical_scoring_unchanged(cs):
    """Canonical reference is unchanged; ``combine_consensus_signals``
    is still importable and callable."""
    assert hasattr(cs, "combine_consensus_signals")
    assert callable(cs.combine_consensus_signals)


# ---------------------------------------------------------------------------
# Basic per-day semantics
# ---------------------------------------------------------------------------

def test_empty_member_list_returns_empty_object_series(sb, cs):
    fast = sb._combine_signals_fast([])
    ref = cs.combine_consensus_signals([])
    assert len(fast) == 0
    assert fast.dtype == object
    assert len(ref) == 0


def test_all_none_returns_none(sb, cs):
    members = [
        _series(["None", "None", "None"]),
        _series(["None", "None", "None"]),
        _series(["None", "None", "None"]),
    ]
    _assert_parity(sb, cs, members)
    assert sb._combine_signals_fast(members).tolist() == ["None"] * 3


def test_single_buy_member_rest_none_yields_buy(sb, cs):
    members = [
        _series(["Buy", "Buy", "Buy"]),
        _series(["None", "None", "None"]),
        _series(["None", "None", "None"]),
    ]
    _assert_parity(sb, cs, members)
    assert sb._combine_signals_fast(members).tolist() == ["Buy"] * 3


def test_single_short_member_rest_none_yields_short(sb, cs):
    members = [
        _series(["None", "None", "None"]),
        _series(["Short", "Short", "Short"]),
        _series(["None", "None", "None"]),
    ]
    _assert_parity(sb, cs, members)
    assert sb._combine_signals_fast(members).tolist() == ["Short"] * 3


def test_buy_plus_short_conflict_resolves_to_none(sb, cs):
    members = [
        _series(["Buy", "Buy", "Buy"]),
        _series(["Short", "Short", "Short"]),
    ]
    _assert_parity(sb, cs, members)
    assert sb._combine_signals_fast(members).tolist() == ["None"] * 3


def test_multiple_buy_members_yield_buy(sb, cs):
    members = [
        _series(["Buy", "Buy", "Buy"]),
        _series(["Buy", "Buy", "Buy"]),
        _series(["Buy", "Buy", "Buy"]),
        _series(["Buy", "Buy", "Buy"]),
    ]
    _assert_parity(sb, cs, members)
    assert sb._combine_signals_fast(members).tolist() == ["Buy"] * 3


def test_multiple_short_members_yield_short(sb, cs):
    members = [
        _series(["Short", "Short", "Short"]),
        _series(["Short", "Short", "Short"]),
    ]
    _assert_parity(sb, cs, members)
    assert sb._combine_signals_fast(members).tolist() == ["Short"] * 3


def test_mixed_per_date_resolution(sb, cs):
    # Day 0: all Buy -> Buy
    # Day 1: one Buy + one Short + one None -> None (conflict)
    # Day 2: all None -> None
    # Day 3: two Short, one None -> Short
    members = [
        _series(["Buy", "Buy",   "None", "Short"]),
        _series(["Buy", "Short", "None", "Short"]),
        _series(["Buy", "None",  "None", "None"]),
    ]
    _assert_parity(sb, cs, members)
    assert sb._combine_signals_fast(members).tolist() == [
        "Buy", "None", "None", "Short",
    ]


# ---------------------------------------------------------------------------
# Type / dtype edge cases
# ---------------------------------------------------------------------------

def test_nan_is_treated_as_none(sb, cs):
    # NaN positions must collapse to None per canonical normalize.
    idx = _idx(4)
    members = [
        pd.Series(["Buy", float("nan"), "Buy",  None], index=idx),
        pd.Series(["Buy", "Buy",        "None", "Buy"], index=idx),
    ]
    _assert_parity(sb, cs, members)


def test_integer_codes_match_label_codes(sb, cs):
    idx = _idx(4)
    # +1/-1/0 should be normalized the same way the canonical does.
    members = [
        pd.Series([1, -1, 0, 1], index=idx, dtype=int),
        pd.Series([1, -1, 0, 0], index=idx, dtype=int),
    ]
    _assert_parity(sb, cs, members)


def test_whitespace_in_labels_is_stripped(sb, cs):
    idx = _idx(3)
    members = [
        pd.Series([" Buy ", "Short ", " None "], index=idx, dtype=object),
        pd.Series(["Buy",   "Short ", "None"],   index=idx, dtype=object),
    ]
    _assert_parity(sb, cs, members)


def test_unknown_label_becomes_none(sb, cs):
    idx = _idx(3)
    # Anything outside Buy/Short/None should collapse to None per
    # canonical contract.
    members = [
        pd.Series(["Buy", "HOLD",  "MAYBE"], index=idx, dtype=object),
        pd.Series(["Buy", "None",  "Buy"],   index=idx, dtype=object),
    ]
    _assert_parity(sb, cs, members)


def test_single_member_passthrough(sb, cs):
    members = [_series(["Buy", "Short", "None", "Buy"])]
    _assert_parity(sb, cs, members)
    assert sb._combine_signals_fast(members).tolist() == [
        "Buy", "Short", "None", "Buy",
    ]


def test_k4_random_deterministic(sb, cs):
    rng = np.random.default_rng(20260523)
    idx = _idx(252 * 5)  # 5 years
    labels = ["Buy", "Short", "None"]

    def _rand_series(seed: int) -> pd.Series:
        rng_local = np.random.default_rng(seed)
        choice = rng_local.choice(labels, size=len(idx), p=[0.10, 0.08, 0.82])
        return pd.Series(choice, index=idx, dtype=object)

    members = [_rand_series(s) for s in (1, 2, 3, 4)]
    _assert_parity(sb, cs, members)


# ---------------------------------------------------------------------------
# Index handling
# ---------------------------------------------------------------------------

def test_shared_index_uses_first_index(sb):
    idx = _idx(5)
    m = _series(["Buy", "Buy", "Buy", "Buy", "Buy"], index=idx)
    fast = sb._combine_signals_fast([m, m])
    assert list(fast.index) == list(idx)


def test_partial_overlap_takes_union(sb, cs):
    # Replicates the legacy pd.concat axis=1 union behavior. A
    # member that's missing for a date is treated as None there.
    idx_a = _idx(5)
    idx_b = _idx(5)[2:]  # only last 3 dates
    m1 = pd.Series(["Buy"] * 5, index=idx_a, dtype=object)
    m2 = pd.Series(["Buy"] * 3, index=idx_b, dtype=object)
    fast = sb._combine_signals_fast([m1, m2])
    # Union index is the same as idx_a (which is a superset of idx_b).
    assert list(fast.index) == list(idx_a)
    # Day 0 and 1: m2 is missing -> None for that member -> consensus
    # = Buy (m1=Buy, m2=None) -> all non-None agree on Buy.
    # Days 2-4: both members Buy -> Buy.
    assert fast.tolist() == ["Buy"] * 5


# ---------------------------------------------------------------------------
# End-to-end equivalence on _combined_metrics_signals
# ---------------------------------------------------------------------------

def test_combined_metrics_signals_matches_canonical_path(sb, cs):
    """Going through the K=4 path, the metrics dict returned by
    ``_combined_metrics_signals`` matches what we'd get if we ran
    canonical consensus directly + reused the same captures + metrics
    helpers.
    """
    rng = np.random.default_rng(424242)
    idx = _idx(252 * 10)
    sec_rets = pd.Series(
        rng.normal(0.0003, 0.012, size=len(idx)),
        index=idx,
        name="ret",
    )
    labels = ["Buy", "Short", "None"]

    def _rand(seed):
        rng_local = np.random.default_rng(seed)
        return pd.Series(
            rng_local.choice(labels, size=len(idx), p=[0.10, 0.08, 0.82]),
            index=idx,
            dtype=object,
        )

    members = [_rand(s) for s in (11, 22, 33, 44)]
    masks = [pd.Series(True, index=idx) for _ in members]

    caps_fast, metrics_fast = sb._combined_metrics_signals(
        [(m, mask) for m, mask in zip(members, masks)],
        sec_rets,
    )

    # Re-derive via the canonical path: same combine, same captures
    # helper, same metrics helper.
    ref_combined = cs.combine_consensus_signals(members)
    ref_caps = sb._captures_from_signals(ref_combined, sec_rets)
    ref_trigger = ref_combined.isin(["Buy", "Short"])
    ref_metrics = sb.metrics_from_captures(ref_caps, trigger_mask=ref_trigger)

    # Captures series must match position by position.
    assert caps_fast.equals(ref_caps), (
        "fast _combined_metrics_signals captures diverged from canonical"
    )
    # Metric scalars must match within float tolerance.
    assert metrics_fast is not None and ref_metrics is not None
    for key in metrics_fast:
        a = metrics_fast[key]
        b = ref_metrics.get(key)
        if isinstance(a, float) and isinstance(b, float):
            assert abs(a - b) < 1e-9, f"metric {key!r}: {a!r} != {b!r}"
        else:
            assert a == b, f"metric {key!r}: {a!r} != {b!r}"
