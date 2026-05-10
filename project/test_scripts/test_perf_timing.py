"""Phase 6C-4: tests for the lightweight perf timing module."""

from __future__ import annotations

import sys
import time as _time
from pathlib import Path

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import perf_timing  # noqa: E402


def test_record_appends_entry_with_required_fields():
    perf_timing.reset()
    out = perf_timing.record("op_a", 0.05)
    assert out["name"] == "op_a"
    assert out["elapsed_seconds"] == pytest.approx(0.05)
    assert "recorded_at" in out
    history = perf_timing.recent()
    assert len(history) == 1
    assert history[0]["name"] == "op_a"


def test_record_clamps_negative_or_nan_to_zero():
    perf_timing.reset()
    perf_timing.record("op_neg", -0.1)
    perf_timing.record("op_nan", float("nan"))
    perf_timing.record("op_str", "not-a-number")
    for entry in perf_timing.recent():
        assert entry["elapsed_seconds"] >= 0.0


def test_history_capped_at_history_cap():
    perf_timing.reset()
    n = perf_timing.HISTORY_CAP + 25
    for i in range(n):
        perf_timing.record(f"op_{i}", 0.001)
    hist = perf_timing.recent()
    assert len(hist) == perf_timing.HISTORY_CAP
    # Newest entries first - the very last record should be at index 0.
    assert hist[0]["name"] == f"op_{n - 1}"


def test_recent_with_limit():
    perf_timing.reset()
    for i in range(10):
        perf_timing.record(f"op_{i}", 0.001)
    out = perf_timing.recent(3)
    assert len(out) == 3
    assert out[0]["name"] == "op_9"


def test_last_returns_newest():
    perf_timing.reset()
    perf_timing.record("first", 0.001)
    perf_timing.record("second", 0.002)
    last = perf_timing.last()
    assert last is not None
    assert last["name"] == "second"


def test_slowest_recent_picks_max_elapsed():
    perf_timing.reset()
    perf_timing.record("fast", 0.001)
    perf_timing.record("slow", 0.500)
    perf_timing.record("medium", 0.050)
    s = perf_timing.slowest_recent()
    assert s is not None
    assert s["name"] == "slow"
    assert s["elapsed_seconds"] == pytest.approx(0.500)


def test_slowest_recent_with_limit():
    """When limit < history, slowest_recent must only consider
    the most recent N entries."""
    perf_timing.reset()
    perf_timing.record("ancient_slow", 0.500)
    perf_timing.record("recent_fast_1", 0.001)
    perf_timing.record("recent_fast_2", 0.002)
    s = perf_timing.slowest_recent(limit=2)
    assert s is not None
    assert s["name"] == "recent_fast_2"


def test_timed_context_manager_records_elapsed():
    perf_timing.reset()
    with perf_timing.timed("ctx_op") as state:
        _time.sleep(0.01)
        state["cache_hit"] = False
    history = perf_timing.recent()
    assert len(history) == 1
    entry = history[0]
    assert entry["name"] == "ctx_op"
    assert entry["elapsed_seconds"] >= 0.005
    assert entry["cache_hit"] is False


def test_timed_records_even_on_exception():
    perf_timing.reset()
    with pytest.raises(RuntimeError):
        with perf_timing.timed("ctx_failing"):
            raise RuntimeError("boom")
    history = perf_timing.recent()
    assert len(history) == 1
    assert history[0]["name"] == "ctx_failing"


def test_record_with_extra_dict():
    perf_timing.reset()
    perf_timing.record(
        "op_with_extra", 0.02, extra={"target": "SPY", "rows": 17},
    )
    entry = perf_timing.last()
    assert entry["extra"] == {"target": "SPY", "rows": 17}


def test_reset_clears_history():
    perf_timing.reset()
    perf_timing.record("op", 0.001)
    perf_timing.reset()
    assert perf_timing.recent() == []
    assert perf_timing.last() is None
    assert perf_timing.slowest_recent() is None
