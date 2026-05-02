"""
Phase 2A: static regression guards.

Each guard scans production code for a banned pattern that surfaced
as a real bug class during Phase 1B. Allowlists are explicit and
linked to ledger entries so a future regression cannot land silently.

Production files in scope: six engines, canonical_scoring,
stale_check, signal_library/. QC clone is excluded by ledger scope.
Test files, docs, and __pycache__ are also excluded.

Each failure message lists file:line, source line, the canonical
rule, and the related ledger entry.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

import pytest

from phase2_test_utils import (
    PROJECT_DIR,
    ScanRule,
    format_hits,
    iter_python_files,
    production_python_files,
    scan_for_pattern,
)


def _format_failure(hits, rule_name: str, expected: str, ledger: str) -> str:
    body = format_hits(hits, header=f"[{rule_name}] {len(hits)} unallowlisted hit(s):")
    return (
        f"{body}\n\n"
        f"Expected: {expected}\n"
        f"Ledger: {ledger}\n"
    )


# ---------------------------------------------------------------------------
# B1: Sentinel literals ((1,2),0.0) / ((2,1),0.0)
# ---------------------------------------------------------------------------


def test_b1_no_legacy_sentinel_literals():
    rule = ScanRule(
        name="B1-sentinel-literals",
        pattern=re.compile(
            r"\(\s*\(\s*1\s*,\s*2\s*\)\s*,\s*0\.0\s*\)"
            r"|\(\s*\(\s*2\s*,\s*1\s*\)\s*,\s*0\.0\s*\)"
        ),
        # Comments only -- no production code may carry these literals.
        allow_comments=True,
    )
    hits = scan_for_pattern(rule, production_python_files())
    assert not hits, _format_failure(
        hits,
        rule.name,
        "no `((1, 2), 0.0)` / `((2, 1), 0.0)` sentinel literals in "
        "production code (comments only)",
        "Entry 8 (sentinel pair standardization)",
    )


# ---------------------------------------------------------------------------
# B2: daily_top_*_pairs.get fallback canonicalization
# ---------------------------------------------------------------------------


def test_b2_daily_top_pairs_fallbacks_are_canonical():
    """Every daily_top_buy_pairs.get(...) / daily_top_short_pairs.get(...)
    fallback default must be a canonical sentinel that gates to no-trade.

    Canonical:
      buy:   (MAX_SMA_DAY, MAX_SMA_DAY - 1)  or _BUY_SENTINEL
      short: (MAX_SMA_DAY - 1, MAX_SMA_DAY)  or _SHORT_SENTINEL

    Allowlist:
      - Defensive `.get(key)` (no default) where the result is
        explicitly None-checked before use. None can never gate to
        a tradable signal; the surrounding code shows the guard.
      - `.get(key, (None, 0))` where only `[1]` (the capture) is
        ever read. None-as-pair would crash if dereferenced as a
        pair; only the float capture is consumed.
      Both patterns appear in spymaster's display/diagnostic paths.
      Documented allowlist below.
    """
    files = production_python_files()

    site_pat = re.compile(
        r"daily_top_(?P<kind>buy|short)_pairs\.get\("
        r"|(?P<tf_kind>[bs])dict\.get\(",
    )
    canonical_buy_pat = re.compile(
        r"_BUY_SENTINEL"
        r"|\(\s*MAX_SMA_DAY\s*,\s*MAX_SMA_DAY\s*-\s*1\s*\)"
    )
    canonical_short_pat = re.compile(
        r"_SHORT_SENTINEL"
        r"|\(\s*MAX_SMA_DAY\s*-\s*1\s*,\s*MAX_SMA_DAY\s*\)"
    )

    # Defensive non-canonical fallback patterns that are safe by
    # construction:
    #   .get(key)                 -> None on miss; surrounding code
    #                                explicitly None-checks.
    #   .get(key, (None, 0))      -> only [1] (capture float) read;
    #                                None-as-pair would crash on [0].
    safe_defensive_pat = re.compile(
        r"\.get\([^,]+\)\s*$"       # no default (closes paren immediately after key)
        r"|\.get\([^,]+,\s*\(None,\s*0\)\)\[1\]"
    )

    # Per-line allowlist (file:line) for patterns the regex above can't
    # cleanly express. Sourced from manual review of current main.
    allowlisted_lines = {
        # Defensive .get() with explicit None-check at line 7789-7790.
        ("spymaster.py", 7784),
        ("spymaster.py", 7785),
        # Defensive .get() with explicit None-check at line 8283.
        ("spymaster.py", 8280),
        ("spymaster.py", 8281),
        # .get(last_date, (None, 0))[1] -- only capture float read.
        ("spymaster.py", 7820),
        ("spymaster.py", 7821),
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

            m = site_pat.search(line)
            if not m:
                continue

            if (rel, lineno) in allowlisted_lines:
                continue
            if safe_defensive_pat.search(line):
                continue

            kind = m.group("kind") or m.group("tf_kind")
            # 'b' / 'buy' -> buy; 's' / 'short' -> short
            is_buy = kind in ("b", "buy")
            checker = canonical_buy_pat if is_buy else canonical_short_pat
            if checker.search(line):
                continue

            failures.append((path, lineno, line))

    if failures:
        rel = lambda p: str(p.relative_to(PROJECT_DIR)).replace("\\", "/")
        body = "\n".join(
            f"{rel(p)}:{ln}: {line.rstrip()}" for p, ln, line in failures
        )
        pytest.fail(
            f"[B2-daily_top_*_pairs] {len(failures)} non-canonical fallback(s):\n"
            f"{body}\n\n"
            "Expected: daily_top_buy_pairs.get(..., ((MAX_SMA_DAY, MAX_SMA_DAY-1), 0.0))\n"
            "          daily_top_short_pairs.get(..., ((MAX_SMA_DAY-1, MAX_SMA_DAY), 0.0))\n"
            "          (or _BUY_SENTINEL / _SHORT_SENTINEL constants)\n"
            "Ledger: Entry 8 (sentinel pair standardization)\n"
        )


# ---------------------------------------------------------------------------
# B3: stats.t.cdf reintroduction
# ---------------------------------------------------------------------------


def test_b3_no_t_cdf_in_production():
    rule = ScanRule(
        name="B3-stats-t-cdf",
        pattern=re.compile(r"\b(?:scipy\.)?stats\.t\.cdf\b|\b_st\.t\.cdf\b|\bt\.cdf\("),
        # Comments allowed (B3 is intentionally about live calls).
        allow_comments=True,
    )
    hits = scan_for_pattern(rule, production_python_files())
    assert not hits, _format_failure(
        hits,
        rule.name,
        "production scoring paths must use stats.t.sf, not stats.t.cdf "
        "(spec §17, numerically stable)",
        "Entry 3 (cdf -> sf p-value)",
    )


# ---------------------------------------------------------------------------
# B4: PRICE_BASIS / price_basis reintroduction
# ---------------------------------------------------------------------------


def test_b4_no_price_basis_selector():
    """Ban env-var price-basis selectors and Adj/raw toggles.

    Allowed: comments, docstrings, boot-log text mentioning raw Close,
    error strings for legacy mismatch, MultiIndex field-set
    memberships including 'Adj Close' (parsing-only), and the
    confluence kwargs.pop('price_basis', None) compatibility shim.
    """
    files = production_python_files()
    # Match active selector patterns:
    #   os.environ.get('PRICE_BASIS', ...)
    #   = 'Adj Close' if ... else 'Close'  (Adj/raw toggle)
    #   if price_basis == 'adj'             (Adj/raw branch)
    selector_pat = re.compile(
        r"os\.environ\.get\(\s*['\"]PRICE_BASIS['\"]"
        r"|=\s*['\"]Adj Close['\"]\s+if\s+.+?else\s+['\"]Close['\"]"
        r"|if\s+price_basis\s*==\s*['\"]adj['\"]"
    )
    allow_line_patterns = [
        re.compile(r"^\s*#"),  # comment line
        re.compile(r"\bkwargs\.pop\(\s*['\"]price_basis['\"]"),
    ]
    rule = ScanRule(
        name="B4-price-basis-selector",
        pattern=selector_pat,
        allowed_line_patterns=allow_line_patterns,
        allow_comments=True,
    )
    hits = scan_for_pattern(rule, files)
    assert not hits, _format_failure(
        hits,
        rule.name,
        "no env-var PRICE_BASIS selectors or Adj/raw toggles "
        "(spec §3, raw Close only)",
        "Entry 1 (Adj Close removal)",
    )


# ---------------------------------------------------------------------------
# B5: functional Adj Close selector
# ---------------------------------------------------------------------------


def test_b5_no_functional_adj_close_selector():
    """Ban functional reads of the 'Adj Close' column.

    Allowlist keeps:
      - field-set memberships (`fields = {'Adj Close', 'Close', ...}`)
        used only for MultiIndex orientation parsing; these don't
        SELECT Adj Close.
      - rescale-cols passthrough lists in onepass that scale all
        present price columns harmlessly.
      - boot-log text and error strings mentioning Adj Close.
    """
    files = production_python_files()

    # Functional read pattern: indexing into df by 'Adj Close' or
    # similar. We approximate by `'Adj Close'` appearing inside a
    # subscript or attribute chain that would actually fetch the
    # column.
    functional_pat = re.compile(
        r"\bdf\s*\[\s*['\"]Adj Close['\"]\s*\]"
        r"|\.iloc\[\s*[^\]]*['\"]Adj Close['\"]"
        r"|\.loc\[\s*[^\]]*['\"]Adj Close['\"]"
        r"|adj_close_col\s*=\s*['\"]Adj Close['\"]"
        r"|preferred\s*=\s*['\"]Adj Close['\"]"
        r"|price_source\s*=\s*['\"]Adj Close['\"]"
    )
    # Exception: function default value of price_source remains in
    # legacy callers but is hard-coded to 'Close' post-1B-2A. We
    # scan for the specific Adj-Close default.

    allow_line_patterns = [
        # Field-set membership for MultiIndex orientation parsing
        re.compile(r"fields\s*=\s*\{[^}]*['\"]Adj Close['\"]"),
        # Rescale-cols passthrough
        re.compile(r"rescale_cols\s*=\s*\[[^\]]*['\"]Adj Close['\"]"),
        # Comment lines
        re.compile(r"^\s*#"),
        # Error / log strings (text only, no selection)
        re.compile(r"f?['\"][^'\"]*Adj Close[^'\"]*['\"]"),
    ]

    rule = ScanRule(
        name="B5-adj-close-selector",
        pattern=functional_pat,
        allowed_line_patterns=allow_line_patterns,
        allow_comments=True,
    )
    hits = scan_for_pattern(rule, files)
    assert not hits, _format_failure(
        hits,
        rule.name,
        "no functional Adj Close selectors (spec §3, raw Close only)",
        "Entry 1 (Adj Close removal)",
    )


# ---------------------------------------------------------------------------
# B6: implicit np.std without ddof in canonical metric contexts
# ---------------------------------------------------------------------------


def test_b6_no_implicit_np_std_in_canonical_contexts():
    """Ban np.std(...) without an explicit ddof= argument in
    canonical metric contexts.

    Allowlist: documented utility / guard sites where population std
    (ddof=0) is the correct semantic, not the canonical-scoring
    sample std (ddof=1):
      - signal_library/shared_integrity.py:330 — vendor-rebase ratio
        coefficient-of-variation check, describes observed
        distribution rather than inferring from a sample.
      - signal_library/shared_integrity.py:545-546 — near-zero
        variance guards before correlation, only compared to EPS.
    """
    files = production_python_files()
    np_std_call = re.compile(r"np\.std\s*\(")

    # Per-line allowlist (file:line) for utility / guard sites.
    allowlisted_lines = {
        ("signal_library/shared_integrity.py", 330),
        ("signal_library/shared_integrity.py", 545),
        ("signal_library/shared_integrity.py", 546),
    }

    failures = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(PROJECT_DIR)).replace("\\", "/")
        for lineno, line in enumerate(lines, start=1):
            if not np_std_call.search(line):
                continue
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Look at this line and the next 2 to handle multi-line
            # calls.
            window = "\n".join(lines[lineno - 1: min(lineno + 2, len(lines))])
            if "ddof" in window:
                continue
            if (rel, lineno) in allowlisted_lines:
                continue
            failures.append((path, lineno, line))

    if failures:
        rel = lambda p: str(p.relative_to(PROJECT_DIR)).replace("\\", "/")
        body = "\n".join(f"{rel(p)}:{ln}: {ln_.rstrip()}" for p, ln, ln_ in failures)
        pytest.fail(
            f"[B6-implicit-np_std] {len(failures)} hit(s):\n"
            f"{body}\n\n"
            "Expected: np.std(arr, ddof=1) in canonical metric contexts; "
            "ddof must be explicit (spec §16).\n"
            "Ledger: Entry 2 (ddof=1)\n"
        )
