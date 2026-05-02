"""
Phase 1B-2B: Spymaster dead streaming path removal.

The streaming SMA-pair selector was always disabled (``use_streaming
= False``) and inserted non-canonical ``(1, 2)`` / ``(2, 1)`` sentinel
pairs per the v0.5 spec appendix. This test asserts:

  - the streaming function definition no longer exists
  - the ``use_streaming`` flag no longer exists
  - the vectorized path is still called (the only remaining path)
  - no ``(1, 2)`` / ``(2, 1)`` sentinel literals remain as
    fallback values in the daily-top-pairs assignment region
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SPYMASTER_TEXT = (PROJECT_DIR / "spymaster.py").read_text(encoding="utf-8")


def test_streaming_function_removed():
    # Function definition gone.
    assert "def _compute_daily_top_pairs_streaming" not in SPYMASTER_TEXT
    # Function call gone.
    assert "_compute_daily_top_pairs_streaming()" not in SPYMASTER_TEXT


def test_use_streaming_flag_removed():
    # No `use_streaming = ` assignment, and no `if use_streaming` branch.
    assert not re.search(r"^\s*use_streaming\s*=", SPYMASTER_TEXT, re.MULTILINE)
    assert "if use_streaming" not in SPYMASTER_TEXT


def test_vectorized_path_still_called():
    # The vectorized helper is now the only path; its call must remain.
    assert "_compute_daily_top_pairs_vectorized()" in SPYMASTER_TEXT


def test_no_streaming_sentinel_assignments():
    # No ((1, 2), 0.0) or ((2, 1), 0.0) sentinel-tuple assignments.
    # Use a relaxed regex that catches whitespace variations.
    assert not re.search(r"\(\s*\(\s*1\s*,\s*2\s*\)\s*,\s*0\.0\s*\)", SPYMASTER_TEXT)
    assert not re.search(r"\(\s*\(\s*2\s*,\s*1\s*\)\s*,\s*0\.0\s*\)", SPYMASTER_TEXT)
    # And no `or (1, 2)` / `or (2, 1)` fallback sentinels.
    assert not re.search(r"\bor\s*\(\s*1\s*,\s*2\s*\)", SPYMASTER_TEXT)
    assert not re.search(r"\bor\s*\(\s*2\s*,\s*1\s*\)", SPYMASTER_TEXT)
