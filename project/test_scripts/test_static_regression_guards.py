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

import ast
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


def test_b7_daily_top_pairs_write_init_canonical():
    """Phase 2A amendment: write-init counterpart to B2.

    B2 covers the read-fallback shape:
        daily_top_*_pairs.get(key, ((..., ...), 0.0))
    B7 covers the write-init shape:
        daily_top_*_pairs[key] = ((..., ...), 0.0)

    The C3 sparse-cache test at the multi_timeframe_builder day-0
    init site (415-417) and the Codex audit at impactsearch.py:2218-2219
    both surfaced production sites where the WRITE side stamped a
    non-canonical sentinel pair. Read fallbacks alone don't cover
    this — a write-side seed of (114, 113) for short is just as
    bad: once it's in the dict, every subsequent read of that
    date returns the buy-form-for-short bug.

    Failures must use canonical literal forms or constants:
        buy:   (MAX_SMA_DAY, MAX_SMA_DAY - 1)  or _BUY_SENTINEL
        short: (MAX_SMA_DAY - 1, MAX_SMA_DAY)  or _SHORT_SENTINEL
    """
    files = production_python_files()

    # Match `daily_top_<KIND>_pairs[<key>] = ((<i>, <j>), <cap>)`
    # capturing the kind. We deliberately match only literal
    # numeric inner pairs to avoid flagging cases where the
    # right-hand side is a previously-validated variable.
    site_pat = re.compile(
        r"daily_top_(?P<kind>buy|short)_pairs\[[^\]]+\]\s*=\s*"
        r"\(\s*\(\s*(?P<i>\w+(?:\s*-\s*\w+)?)\s*,\s*(?P<j>\w+(?:\s*-\s*\w+)?)\s*\)"
    )

    canonical_buy_inner = re.compile(
        r"^\s*(?:MAX_SMA_DAY|114)\s*$"  # left index
    )
    # We accept canonical forms by checking that both inner
    # tokens form one of the two canonical pairs.
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
            kind = m.group("kind")
            i_tok = m.group("i").strip()
            j_tok = m.group("j").strip()
            # Normalize whitespace inside the tokens.
            i_norm = re.sub(r"\s+", "", i_tok)
            j_norm = re.sub(r"\s+", "", j_tok)
            is_buy = kind == "buy"
            if is_buy:
                ok = (i_norm == "MAX_SMA_DAY" and j_norm == "MAX_SMA_DAY-1")
                # Numeric canonical (114, 113) acceptable only when
                # MAX_SMA_DAY is not in scope. We reject it
                # uniformly to push toward constants.
            else:
                ok = (i_norm == "MAX_SMA_DAY-1" and j_norm == "MAX_SMA_DAY")
            if not ok:
                failures.append((path, lineno, line))

    if failures:
        rel = lambda p: str(p.relative_to(PROJECT_DIR)).replace("\\", "/")
        body = "\n".join(f"{rel(p)}:{ln}: {ln_.rstrip()}" for p, ln, ln_ in failures)
        pytest.fail(
            f"[B7-write-init-sentinels] {len(failures)} hit(s):\n"
            f"{body}\n\n"
            "Expected:\n"
            "  daily_top_buy_pairs[key]   = ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)\n"
            "  daily_top_short_pairs[key] = ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)\n"
            "Use the constant; do not hardcode numeric pairs.\n"
            "Ledger: Entry 8 (sentinel pair standardization)\n"
        )


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


# ---------------------------------------------------------------------------
# B12: signal-library consumers must verify provenance manifests
# ---------------------------------------------------------------------------


def _function_calls_name(func_node: ast.AST, target_names: Sequence[str]) -> bool:
    """Return True if ``func_node`` contains a Call whose callable is one
    of the names in ``target_names`` (matched as bare Name, attribute
    suffix, or full ``module.attr`` chain).
    """
    target_set = set(target_names)
    for sub in ast.walk(func_node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if isinstance(f, ast.Name) and f.id in target_set:
            return True
        if isinstance(f, ast.Attribute):
            # match either bare attribute (`.verify_manifest`) or full chain
            if f.attr in target_set:
                return True
            chain = []
            cur = f
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                chain.append(cur.id)
            if ".".join(reversed(chain)) in target_set:
                return True
    return False


def _find_function(tree: ast.AST, name: str) -> "ast.FunctionDef | None":
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# Phase 3B-2A B12 allowlist: each entry is (relative_path, line, reason).
# Lines are matched against the AST ``lineno`` of the ``Call`` node, which
# is the line of the ``pickle.load`` call. Update the line numbers when
# the surrounding code shifts; allowlisting whole files is reserved for
# the central provenance loader.
#
# Retired in Phase 3B-2A:
#   - spymaster.py: all 4 raw-load sites (cache PKL consumers) migrated
#     to load_verified_pickle_artifact via the sanctioned standalone-rule
#     exception (see ledger Phase 3B-2A entry).
#   - trafficflow.py: load_spymaster_pkl migrated.
#   - signal_library/confluence_analyzer.py: _load_spymaster_cache_fallback
#     migrated.
#
# Final 3B-2A state: only the central provenance loader internals are
# allowlisted. Every other production .py file must route through
# load_verified_signal_library / load_verified_pickle_artifact /
# load_verified_json_artifact / pickle_load_compat.
_B12_RAW_LOAD_ALLOWLIST: tuple = (
    ("provenance_manifest.py", None,
     "central pickle_load_compat / load_verified_* — Phase 3B-1+2A"),
    # Below entry retires in commit 6 within Phase 3B-2A:
    #   commit 6: signal_library/confluence_analyzer.py:72
    # Spymaster (4 sites) and TrafficFlow (1 site) are already retired.
    ("signal_library/confluence_analyzer.py", 72,
     "_load_spymaster_cache_fallback — retires in 3B-2A commit 6"),
)


def _production_python_files_for_b12() -> "list[Path]":
    """Production .py files in scope for the raw-pickle-load scan.

    Excludes test_scripts/, the provenance helper itself (allowlisted via
    file entry), and QC clones (already excluded by production_python_files).
    """
    return [p for p in production_python_files()]


def _scan_raw_pickle_loads(path: Path) -> "list[tuple[int, str]]":
    """Return ``(lineno, source_line)`` for every ``pickle.load(...)`` Call
    inside ``path``. Comments are tolerated by AST parsing automatically.
    """
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    lines = text.splitlines()
    hits: list = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "load":
            base = f.value
            if isinstance(base, ast.Name) and base.id == "pickle":
                src = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
                hits.append((node.lineno, src.rstrip()))
    return hits


def test_b12_no_raw_pickle_load_outside_central_loader():
    """Phase 3B-1 B12: ban raw ``pickle.load(...)`` in production code
    outside the central provenance loader and an explicit, line-precise
    allowlist for Phase 3B-2 deferred surfaces.

    Failure message
    ---------------
    Raw pickle.load in production code outside the central provenance
    loader contract. Use ``provenance_manifest.load_verified_signal_library``
    for signal libraries. Output / Spymaster PKL loaders are Phase 3B-2
    scope; allowlist only with explicit reason.

    Allowlist policy
    ----------------
    - ``provenance_manifest.py`` is allowlisted as a whole file (it IS
      the central loader; raw pickle.load lives there by design).
    - Every other entry is line-precise. Update the line number when the
      surrounding code shifts. Do NOT widen to whole-file allowlists.
    """
    files = _production_python_files_for_b12()
    file_allow: dict = {}
    line_allow: dict = {}
    for rel, lineno, reason in _B12_RAW_LOAD_ALLOWLIST:
        rel_norm = rel.replace("\\", "/")
        if lineno is None:
            file_allow[rel_norm] = reason
        else:
            line_allow.setdefault(rel_norm, {})[int(lineno)] = reason

    failures: list = []
    for path in files:
        rel = str(path.relative_to(PROJECT_DIR)).replace("\\", "/")
        if rel in file_allow:
            continue
        for lineno, src in _scan_raw_pickle_loads(path):
            allowed = line_allow.get(rel, {})
            if lineno in allowed:
                continue
            failures.append((rel, lineno, src))

    if failures:
        body = "\n".join(
            f"{rel}:{lineno}: {src}" for rel, lineno, src in failures
        )
        pytest.fail(
            f"[B12-raw-pickle-load] {len(failures)} unallowlisted hit(s):\n"
            f"{body}\n\n"
            "Raw pickle.load in production code outside the central provenance "
            "loader contract. Use provenance_manifest.load_verified_signal_library "
            "for signal libraries. Output/Spymaster PKL loaders are Phase 3B-2 "
            "scope; allowlist only with explicit reason.\n\n"
            "Current allowlist (file:line -> reason):\n"
            + "\n".join(
                f"  {rel}:{lineno or '*'} -> {reason}"
                for rel, lineno, reason in _B12_RAW_LOAD_ALLOWLIST
            )
            + "\n\nLedger: Phase 3B-1 entry — perf cache + central loader + tightened B12\n"
        )


def test_b12_signal_library_consumers_use_verify_manifest():
    """Phase 3A B12 (preserved as a stricter inner gate): every named
    signal-library consumer must still call ``verify_manifest`` directly
    or via ``load_verified_signal_library``.

    The Phase 3B-1 raw-load scan catches anything new; this function-
    scoped check stays as a defense-in-depth assertion that the five
    named consumer functions specifically still satisfy the contract.
    """
    guarded: Sequence[tuple[str, str]] = (
        ("onepass.py", "load_signal_library"),
        ("impactsearch.py", "load_signal_library"),
        ("signal_library/impact_fastpath.py", "_load_signal_library_quick"),
        ("signal_library/confluence_analyzer.py", "load_signal_library_interval"),
        ("stackbuilder.py", "fallback_load_signal_library"),
    )
    verify_targets = (
        "verify_manifest",
        "_verify_manifest",
        "provenance_manifest.verify_manifest",
        "pm.verify_manifest",
        # Phase 3B-1: the central loader bundles open + load + verify, so a
        # consumer that calls it satisfies the "verifies before reuse"
        # contract just as well as a direct verify_manifest call.
        "load_verified_signal_library",
        "_load_verified_signal_library",
        "provenance_manifest.load_verified_signal_library",
        "pm.load_verified_signal_library",
    )

    failures = []
    for rel, fn_name in guarded:
        path = PROJECT_DIR / rel
        if not path.exists():
            failures.append(f"{rel}: file missing")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{rel}: failed to parse — {exc}")
            continue
        target = _find_function(tree, fn_name)
        if target is None:
            failures.append(f"{rel}: function `{fn_name}` not found")
            continue
        if not _function_calls_name(target, verify_targets):
            failures.append(
                f"{rel}:{fn_name} — uses raw pickle.load but does not call "
                f"verify_manifest. Phase 3A requires manifest verification "
                f"immediately after the raw load and before reuse."
            )

    if failures:
        body = "\n".join(failures)
        pytest.fail(
            f"[B12-signal-library-consumer-verify] {len(failures)} hit(s):\n"
            f"{body}\n\n"
            "Expected: each guarded consumer calls verify_manifest after "
            "the raw pickle.load and before returning the library.\n"
            "Allowlist: provenance_manifest.py, tests, non-signal-library "
            "pickle consumers deferred to 3B (Spymaster PKLs, TrafficFlow, "
            "Confluence durable outputs).\n"
            "Ledger: Phase 3A entry — provenance manifests (signal libraries)\n"
        )
