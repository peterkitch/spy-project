#!/usr/bin/env python3
"""
Targeted cleanup for U.S. dot share-class symbols (e.g., BRK.A → BRK-A).
- Preserves ALL valid exchange suffix tickers (e.g., BRK.L, 0524.HK, PNA1V.HE, I4P.F).
- Only touches base.CLASS with a SINGLE letter class and NO valid exchange suffix, and ONLY if the dash form exists.
- Writes a TSV report with decisions and skips.

Usage:
  python -m global_ticker_library.tools.cleanup_share_classes --dry-run
  python -m global_ticker_library.tools.cleanup_share_classes --validate
  python -m global_ticker_library.tools.cleanup_share_classes --augment-suffixes-from-db --validate

Safe by default: dry-run unless you pass --validate.
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Make package importable when called as a tool
PKG_ROOT = Path(__file__).resolve().parents[2]  # .../project
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from global_ticker_library.gl_config import DATA_DIR, MASTER_FILE, VALID_SUFFIXES
from global_ticker_library.registry import (
    init_db, export_active, upsert_candidates, upsert_validation_results,
    get_all_symbols, get_symbols_by_status
)
from global_ticker_library.validator_yahoo import validate_symbols

# ---------------- helpers ----------------

_DOT_CLASS_RE = re.compile(r"^[A-Z0-9&\-]{1,20}\.[A-Z]$")  # single-letter class after a single dot

def _split_last_dot(s: str) -> Tuple[str, str | None]:
    if "." not in s:
        return s, None
    base, suf = s.rsplit(".", 1)
    return base, suf

def _dotted_suffix_set() -> Set[str]:
    # Normalize VALID_SUFFIXES to dotted, upper form
    dotted = set()
    for s in VALID_SUFFIXES or []:
        u = (s or "").upper()
        if not u:
            continue
        dotted.add(u if u.startswith(".") else f".{u}")
    return dotted

def _collect_suffixes_from_db(limit: int = 500000) -> Set[str]:
    """Optional: learn suffixes from symbols already in registry (active/candidate/stale)."""
    sufs: Set[str] = set()
    seen: Set[str] = set()
    for status in ("active", "candidate", "stale"):
        syms = get_symbols_by_status(status, limit=limit)
        for s in syms:
            s = (s or "").upper()
            if s in seen:
                continue
            seen.add(s)
            if "." in s:
                base, suf = _split_last_dot(s)
                if suf and suf.isalpha() and 1 <= len(suf) <= 5:
                    sufs.add("." + suf)
    return sufs

def _yahoo_exists_dash(sym: str) -> bool:
    """
    Very light probe: try a 5-day daily history via yfinance.
    If we get any non-empty 'Close' bars, consider it recognized.
    """
    import yfinance as yf
    try:
        df = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=False, prepost=False, actions=False)
        return getattr(df, "empty", True) is False and "Close" in df.columns and df["Close"].dropna().shape[0] > 0
    except Exception:
        return False

# ---------------- main logic ----------------

def find_dot_share_class_violations(valid_suffixes: Set[str]) -> List[Tuple[str, str]]:
    """
    Return list of (dot_form, dash_form) to be fixed, BUT ONLY where the dot_form:
      - matches base.[A-Z] (single letter),
      - has exactly one dot (no trailing exchange suffix),
      - and dash_form appears to exist on Yahoo.
    """
    candidates: List[Tuple[str, str]] = []
    universe = get_all_symbols()  # any status

    for sym in universe:
        s = (sym or "").upper().strip()
        # Already properly dashed (BRK-B) -> skip
        if "-" in s and "." not in s:
            continue

        # If ends with a known exchange suffix (.L, .TO, .F, .HE, ...), it's valid -> skip
        for suf in valid_suffixes:
            if s.endswith(suf):
                # Example: BRK.L ; 0524.HK ; I4P.F ; keep as-is
                break
        else:
            # Not an exchange suffix; check if it's a single-letter class after a single dot
            if s.count(".") == 1 and _DOT_CLASS_RE.match(s):
                base, cls = s.split(".", 1)
                dash = f"{base}-{cls}"

                # Guard: Verify the dash actually exists on Yahoo
                print(f"Checking if {dash} exists on Yahoo...", end=" ")
                if _yahoo_exists_dash(dash):
                    print("YES")
                    candidates.append((s, dash))
                else:
                    print("NO - skipping")

    return candidates

def run_cleanup(dry_run: bool = True, augment_suffixes: bool = False, do_validate: bool = False) -> None:
    init_db()

    # Build suffix set
    valid_sufs = _dotted_suffix_set()
    learned: Set[str] = set()
    if augment_suffixes:
        print("Learning suffixes from database...")
        learned = _collect_suffixes_from_db()
        print(f"Found {len(learned)} additional suffixes in database")
        valid_sufs |= learned

    print(f"Using {len(valid_sufs)} valid exchange suffixes")
    print("Scanning for dot share-class violations...")
    
    todo = find_dot_share_class_violations(valid_sufs)

    report_path = DATA_DIR / "dot_share_cleanup_report.tsv"
    lines = []
    lines.append("dot_form\tdash_form\tdecision\n")

    if not todo:
        lines.append("NA\tNA\tNO_VIOLATIONS_FOUND\n")
        report_path.write_text("".join(lines), encoding="utf-8")
        print("No violations found. Report written:", report_path)
        return

    print(f"\nFound {len(todo):,} dot share-class symbols to fix (verified dash exists).")
    
    # Show what will be fixed
    print("\nSymbols to be fixed:")
    for dot, dash in todo[:20]:  # Show first 20
        print(f"  {dot} → {dash}")
    if len(todo) > 20:
        print(f"  ... and {len(todo) - 20} more")

    if dry_run:
        for dot, dash in todo:
            lines.append(f"{dot}\t{dash}\tWOULD_FIX\n")
        report_path.write_text("".join(lines), encoding="utf-8")
        print(f"\nDry run only. Report written: {report_path}")
        print("To apply changes, run without --dry-run")
        return

    # Commit: mark dot-form invalid, queue dash-form as candidate
    print("\nApplying fixes...")
    
    # 1) Mark DOT forms invalid via upsert_validation_results (so DB status flips immediately)
    invalid_recs = [{
        "symbol": dot,
        "original": dot,
        "status": "invalid",
        "exists": False,
        "meta_exists": False,
        "has_price": False,
        "error_code": "dot_share_class",
        "error_msg": "Replaced by dash share-class variant",
        "quoteType": None, "exchange": None, "currency": None,
        "regularMarketTime": None, "regularMarketTime_iso": None
    } for dot, _ in todo]

    n_act, n_stale, n_inv, n_unk, adds, rems = upsert_validation_results(invalid_recs)
    print(f"Marked {len(invalid_recs):,} dot symbols INVALID in registry.")

    # 2) Queue dash forms as candidates; optionally validate them now
    dash_list = [dash for _, dash in todo]
    n_added = upsert_candidates(dash_list, source="CLEANUP:DOTCLASS")
    print(f"Queued {n_added:,} dash symbols as candidates.")

    for dot, dash in todo:
        lines.append(f"{dot}\t{dash}\tFIXED\n")
    report_path.write_text("".join(lines), encoding="utf-8")
    print(f"Report written: {report_path}")

    if do_validate and dash_list:
        print("\nValidating dash symbols...")
        results, agg = validate_symbols(dash_list, gentle=True)  # canonicalization stays OFF by default
        n_act, n_stale, n_inv, n_unk, adds, rems = upsert_validation_results(results)
        print(f"Validation complete: {n_act} active, {n_stale} stale, {n_inv} invalid, {n_unk} unknown")

    print("\nExporting active symbols...")
    n = export_active()
    print(f"Re-exported {n:,} ACTIVE symbols to {MASTER_FILE.name}")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Fix U.S. dot share-class symbols (BRK.A → BRK-A) safely.")
    p.add_argument("--dry-run", action="store_true", help="Analyze and write report only; do not modify DB.")
    p.add_argument("--validate", action="store_true", help="After fixing, validate the new dash symbols.")
    p.add_argument("--augment-suffixes-from-db", action="store_true",
                   help="Before scanning, add any dotted suffixes found in your registry to the valid suffix set.")
    args = p.parse_args()
    
    # Determine if we're doing a real run
    is_dry = args.dry_run or not (args.validate or any(a.startswith('--') and a != '--dry-run' for a in sys.argv[1:]))
    
    run_cleanup(dry_run=is_dry, augment_suffixes=args.augment_suffixes_from_db, do_validate=args.validate)