"""
Phase 2B-1: lookahead static guards.

Spec §7: a day-T signal must depend only on data through day T-1.
Same-day SMA values, same-day returns shifted by negative
amounts, and unshifted SMA comparisons applied to the day's own
return are all forms of lookahead.

These guards scan production code for known lookahead patterns.
Each guard is documented with its rule, allowlist, and the
ledger entry it seals against regression.

Failure messages cite spec §7 and the affected file:line.

Out of scope (best-effort coverage notes):
  A4 (unshifted SMA-comparison-on-same-day-return) is hard to
  detect with pure regex because it requires cross-line semantic
  understanding (which SMA series, which return series, in
  the same function). Codex's preflight identified
  spymaster.compute_signals as the canonical example. A5 below
  pins it as uncalled dead code so a future call-site
  reintroduction fails the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from phase2_test_utils import (
    PROJECT_DIR,
    iter_python_files,
    production_python_files,
)


# ---------------------------------------------------------------------------
# B8: shift(-N) on signal-relevant series
# ---------------------------------------------------------------------------


def test_b8_no_negative_shift_in_signal_paths():
    """Ban .shift(-N) in production signal-construction code.

    Allowlist:
      - confluence._mp_safe_pct_change / _forward_returns and
        _mp_forward_return_on_grid: these compute FORWARD returns
        for display purposes, never used to drive a day-T signal.
        Their shift(-1) is intentional and isolated to display
        helpers.
    """
    files = production_python_files()
    pattern = re.compile(r"\.shift\(\s*-\s*\d+\s*\)|\.shift\(\s*-\s*\w+\s*\)")

    # Per-line allowlist for documented forward-return display helpers.
    allowlisted_lines = {
        # confluence._mp_forward_return_on_grid: forward return helper
        # used by display logic only, not signal construction.
        ("confluence.py", 346),
        # confluence forward_returns dashboard panel: F+k columns are
        # forward-looking by definition; not used to drive any signal.
        ("confluence.py", 2282),
    }

    failures = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(PROJECT_DIR)).replace("\\", "/")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if not pattern.search(line):
                continue
            if (rel, lineno) in allowlisted_lines:
                continue
            failures.append((rel, lineno, line))

    if failures:
        body = "\n".join(f"{r}:{ln}: {ln_.rstrip()}" for r, ln, ln_ in failures)
        pytest.fail(
            f"[B8-negative-shift] {len(failures)} hit(s):\n{body}\n\n"
            "Expected: production signal-construction code must not use "
            ".shift(-N). Day-T signals must depend only on data through "
            "day T-1 (spec §7). Forward-return display helpers are "
            "allowlisted explicitly.\n"
            "Ledger: spec §7 lookahead contract.\n"
        )


# ---------------------------------------------------------------------------
# B9: price.shift(- pattern (negative shift on a price series)
# ---------------------------------------------------------------------------


def test_b9_no_negative_shift_on_price_series():
    """Ban price.shift(-N) where price is a Close/Open/etc. series
    used to drive a signal. (Subset of B8 with a tighter regex
    targeting the price-series naming convention; useful as a
    second seal because B8's allowlist intentionally permits
    confluence forward-display helpers.)
    """
    files = production_python_files()
    pattern = re.compile(
        r"\b(?:close|open|high|low|price|prices|sec_close|sec_returns)"
        r"\s*\.\s*shift\(\s*-\s*\d+\s*\)"
    )

    allowlisted_lines: set[tuple[str, int]] = set()  # none expected

    failures = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(PROJECT_DIR)).replace("\\", "/")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if not pattern.search(line):
                continue
            if (rel, lineno) in allowlisted_lines:
                continue
            failures.append((rel, lineno, line))

    if failures:
        body = "\n".join(f"{r}:{ln}: {ln_.rstrip()}" for r, ln, ln_ in failures)
        pytest.fail(
            f"[B9-negative-price-shift] {len(failures)} hit(s):\n{body}\n\n"
            "Expected: price series must not be shifted with a negative N "
            "in production signal-construction code (spec §7).\n"
        )


# ---------------------------------------------------------------------------
# B10: sma_matrix indexed by current-day idx in signal paths
# ---------------------------------------------------------------------------


def test_b10_no_unshifted_sma_matrix_in_signal_paths():
    """Ban same-day SMA reads inside signal-construction blocks.

    The canonical pattern is sma_matrix[idx - 1] (yesterday's
    SMAs) feeding today's signal. sma_matrix[idx] (today's SMA)
    in a signal-construction block is lookahead.

    This guard targets an exact source pattern. SMA matrix
    assignments via vectorized index arrays (e.g.
    sma_matrix[valid_indices, i-1] = ...) are writes, not
    reads, and are explicitly excluded.
    """
    files = production_python_files()
    # Match `sma_matrix[<single-token>]` where the token is bare
    # idx / t / i (not idx-1, not a slice with comma). Read sites
    # only — exclude assignment LHS.
    pattern = re.compile(
        r"(?<!=\s)sma_matrix\[\s*(?:idx|t|i)\s*\]"
    )

    allowlisted_lines: set[tuple[str, int]] = set()  # none expected

    failures = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(PROJECT_DIR)).replace("\\", "/")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if not pattern.search(line):
                continue
            # Guard against false positives where the bracketed token is
            # actually being WRITTEN (assignment LHS): if the next
            # non-whitespace character after `]` is `=` and not `==`,
            # skip.
            m = pattern.search(line)
            after = line[m.end():].lstrip() if m else ""
            if after.startswith("=") and not after.startswith("=="):
                continue
            if (rel, lineno) in allowlisted_lines:
                continue
            failures.append((rel, lineno, line))

    if failures:
        body = "\n".join(f"{r}:{ln}: {ln_.rstrip()}" for r, ln, ln_ in failures)
        pytest.fail(
            f"[B10-unshifted-sma-matrix] {len(failures)} hit(s):\n{body}\n\n"
            "Expected: signal-construction code reads sma_matrix at the "
            "previous index (idx - 1, t - 1), not the current index. "
            "Day-T signals must depend only on data through day T-1 "
            "(spec §7).\n"
        )


# ---------------------------------------------------------------------------
# B11: spymaster.compute_signals must remain uncalled (dead code).
# ---------------------------------------------------------------------------


def test_b11_spymaster_compute_signals_uncalled():
    """spymaster.compute_signals applies same-day SMA comparison
    directly to same-day returns, which is lookahead by spec §7.
    Codex audit confirmed it has zero call sites on origin/main
    eb5f1a7. This guard pins it as dead code: any future call
    site reintroduces a known lookahead bug.

    If/when 2B-2 or Phase 3 fixes the function (shift the signal
    by 1 before applying), the static guard's rule should change
    from "uncalled" to "shift-corrected" — update the rule then,
    not now.
    """
    spymaster_path = PROJECT_DIR / "spymaster.py"
    spymaster_text = spymaster_path.read_text(encoding="utf-8")

    # Confirm the function still exists (sanity check; otherwise
    # this guard becomes vacuous).
    if "def compute_signals(" not in spymaster_text:
        pytest.skip(
            "spymaster.compute_signals has been removed; this guard is "
            "no longer needed. Delete the test or convert to a "
            "regression seal against re-introduction."
        )

    # Search every production .py file for a CALL site
    # `compute_signals(` (anything besides the def line).
    call_pattern = re.compile(r"\bcompute_signals\s*\(")
    def_pattern = re.compile(r"^\s*def\s+compute_signals\s*\(")

    callers = []
    for path in production_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(PROJECT_DIR)).replace("\\", "/")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if def_pattern.search(line):
                continue
            if call_pattern.search(line):
                callers.append((rel, lineno, line))

    if callers:
        body = "\n".join(f"{r}:{ln}: {ln_.rstrip()}" for r, ln, ln_ in callers)
        pytest.fail(
            f"[B11-compute_signals-uncalled] {len(callers)} call site(s):\n"
            f"{body}\n\n"
            "Expected: spymaster.compute_signals has zero call sites. "
            "It applies same-day SMA comparisons to same-day returns, "
            "which is lookahead by spec §7. If you intend to call it, "
            "first shift the signal by +1 day; then update this guard "
            "to assert shift-correctness instead of uncalled-ness.\n"
        )
