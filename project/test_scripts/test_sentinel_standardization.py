"""
Phase 1B-2B: sentinel pair standardization.

Per spec §appendix the canonical sentinel pairs are:
  buy:   (MAX_SMA_DAY,     MAX_SMA_DAY - 1)
  short: (MAX_SMA_DAY - 1, MAX_SMA_DAY)

These tests assert:
  - Spymaster has no remaining (1, 2) / (2, 1) sentinel literals
    in its dead streaming path (already verified separately, but
    re-asserted here so the sentinel coverage is one place).
  - OnePass short-sentinel sites use the canonical (msd-1, msd)
    form, not the buy-sentinel (msd, msd-1).
  - TrafficFlow defines _BUY_SENTINEL / _SHORT_SENTINEL constants
    in canonical form and uses them at the daily-top-pair fallback
    sites.
"""

from __future__ import annotations

import re
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SPYMASTER_TEXT = (PROJECT_DIR / "spymaster.py").read_text(encoding="utf-8")
ONEPASS_TEXT = (PROJECT_DIR / "onepass.py").read_text(encoding="utf-8")
TRAFFICFLOW_TEXT = (PROJECT_DIR / "trafficflow.py").read_text(encoding="utf-8")
IMPACTSEARCH_TEXT = (PROJECT_DIR / "impactsearch.py").read_text(encoding="utf-8")


def test_spymaster_no_streaming_sentinels():
    # Re-asserted from test_dead_streaming_path_removed.py for unified
    # coverage of the sentinel-pair invariant.
    assert not re.search(r"\(\s*\(\s*1\s*,\s*2\s*\)\s*,\s*0\.0\s*\)", SPYMASTER_TEXT)
    assert not re.search(r"\(\s*\(\s*2\s*,\s*1\s*\)\s*,\s*0\.0\s*\)", SPYMASTER_TEXT)


def test_onepass_short_sentinel_is_canonical():
    # Find every occurrence of `prev_short_pair = (...)` and assert
    # the canonical (MAX_SMA_DAY - 1, MAX_SMA_DAY) form. The
    # pre-1B-2B code used (MAX_SMA_DAY, MAX_SMA_DAY - 1) (the buy
    # form) in three places.
    assignments = re.findall(
        r"prev_short_pair\s*=\s*\(([^)]+)\)",
        ONEPASS_TEXT,
    )
    assert assignments, "expected at least one prev_short_pair assignment"
    for body in assignments:
        # Ignore tuple-unpack reads from dicts (those don't have
        # MAX_SMA_DAY literals; they pull from the dict).
        if "MAX_SMA_DAY" not in body:
            continue
        normalized = re.sub(r"\s+", "", body)
        assert "MAX_SMA_DAY-1,MAX_SMA_DAY" in normalized, (
            f"prev_short_pair assignment uses non-canonical form: ({body})"
        )

    # Same check for the daily_top_short_pairs day-0 store.
    short_store_lines = re.findall(
        r"daily_top_short_pairs\[\w+\]\s*=\s*\(\(([^)]+)\)\s*,\s*0\.0\)",
        ONEPASS_TEXT,
    )
    for body in short_store_lines:
        if "MAX_SMA_DAY" not in body:
            continue
        normalized = re.sub(r"\s+", "", body)
        assert "MAX_SMA_DAY-1,MAX_SMA_DAY" in normalized, (
            f"daily_top_short_pairs assignment uses non-canonical form: ({body})"
        )


def test_trafficflow_defines_canonical_sentinels():
    # Constants exist and use the canonical forms.
    assert re.search(r"^MAX_SMA_DAY\s*=\s*114\b", TRAFFICFLOW_TEXT, re.MULTILINE)
    assert re.search(
        r"_BUY_SENTINEL\s*=\s*\(\s*MAX_SMA_DAY\s*,\s*MAX_SMA_DAY\s*-\s*1\s*\)",
        TRAFFICFLOW_TEXT,
    )
    assert re.search(
        r"_SHORT_SENTINEL\s*=\s*\(\s*MAX_SMA_DAY\s*-\s*1\s*,\s*MAX_SMA_DAY\s*\)",
        TRAFFICFLOW_TEXT,
    )


def test_trafficflow_no_legacy_sentinel_literals():
    # No `((1, 2), 0.0)` or `((2, 1), 0.0)` remaining in trafficflow.
    assert not re.search(r"\(\s*\(\s*1\s*,\s*2\s*\)\s*,\s*0\.0\s*\)", TRAFFICFLOW_TEXT)
    assert not re.search(r"\(\s*\(\s*2\s*,\s*1\s*\)\s*,\s*0\.0\s*\)", TRAFFICFLOW_TEXT)


def test_impactsearch_no_legacy_sentinel_literals():
    # 1B-2B amendment: impactsearch.py:2272 had a (1, 2) fallback
    # sentinel inside the per-date gating loop that builds primary
    # signals from cached daily_top_*_pairs dicts. Same class of bug
    # as TrafficFlow had -- (1, 2) is unsafe because SMA_1 / SMA_2 are
    # finite most days. No (1, 2) / (2, 1) sentinel literals should
    # remain in impactsearch.py.
    assert not re.search(r"\(\s*\(\s*1\s*,\s*2\s*\)\s*,\s*0\.0\s*\)", IMPACTSEARCH_TEXT)
    assert not re.search(r"\(\s*\(\s*2\s*,\s*1\s*\)\s*,\s*0\.0\s*\)", IMPACTSEARCH_TEXT)


def test_impactsearch_uses_canonical_maxsma_sentinels():
    # The two `daily_top_*_pairs.get(...)` calls inside the per-date
    # gating loop must default to canonical MAX-SMA sentinel tuples.
    # Buy: (MAX_SMA_DAY, MAX_SMA_DAY - 1). Short: (MAX_SMA_DAY - 1, MAX_SMA_DAY).
    buy_default = re.search(
        r"daily_top_buy_pairs\.get\([^,]+,\s*\(\s*\(\s*MAX_SMA_DAY\s*,\s*MAX_SMA_DAY\s*-\s*1\s*\)\s*,\s*0\.0\s*\)\s*\)",
        IMPACTSEARCH_TEXT,
    )
    short_default = re.search(
        r"daily_top_short_pairs\.get\([^,]+,\s*\(\s*\(\s*MAX_SMA_DAY\s*-\s*1\s*,\s*MAX_SMA_DAY\s*\)\s*,\s*0\.0\s*\)\s*\)",
        IMPACTSEARCH_TEXT,
    )
    assert buy_default is not None, (
        "impactsearch.py daily_top_buy_pairs.get() default does not "
        "match the canonical (MAX_SMA_DAY, MAX_SMA_DAY - 1) form"
    )
    assert short_default is not None, (
        "impactsearch.py daily_top_short_pairs.get() default does not "
        "match the canonical (MAX_SMA_DAY - 1, MAX_SMA_DAY) form"
    )
