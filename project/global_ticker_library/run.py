"""
Global Ticker Library CLI Orchestrator
Main entry point for ticker discovery, validation, and management
"""
import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Set
from datetime import datetime
from contextlib import redirect_stdout, redirect_stderr
import io

# Ensure the project root (the parent of the package folder) is importable.
PKG_ROOT = Path(__file__).resolve().parents[1]  # .../project
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from global_ticker_library.gl_config import (
    DATA_DIR, MANUAL_FILE, MASTER_FILE, STAGING_FILE, REMOVALS_LOG_FILE, DB_PATH
)
from global_ticker_library.registry import (
    cleanup_stale, counts, export_active, init_db,
    upsert_candidates, upsert_validation_results,
    get_symbols_to_validate, write_removal_log, to_validate_breakdown,
    write_progress, clear_progress
)
from global_ticker_library.sources import gather_all, gather_optional
from global_ticker_library.validator_yahoo import validate_symbols
from tqdm.auto import tqdm

TOKEN_RE = re.compile(r"[A-Z0-9.^=\-]+", re.ASCII | re.IGNORECASE)

class _SilentIO(io.StringIO):
    def write(self, *args, **kwargs):
        # swallow
        return 0

def _silence_yf():
    return redirect_stdout(_SilentIO())

def _normalize_tokens(text: str) -> List[str]:
    """Extract and normalize potential ticker symbols from text"""
    raw = TOKEN_RE.findall((text or "").upper())
    # Allow up to 22 chars to cover global suffixes / funds / structured notes
    return [t.strip().upper() for t in raw
            if t.strip() and 1 <= len(t.strip()) <= 22]

def _write_staging(symbols: Iterable[str]) -> None:
    """Write symbols to staging file"""
    STAGING_FILE.write_text("\n".join(sorted(set(symbols))), encoding="utf-8")

def scrape_sources(include_optional: bool = False) -> Set[str]:
    """Scrape all configured sources"""
    tqdm.write("\n" + "="*60)
    tqdm.write("Starting ticker discovery...")
    tqdm.write("="*60)
    
    # Gather from core sources
    syms = gather_all()
    
    # Add optional sources if requested
    if include_optional:
        optional = gather_optional()
        if optional:
            syms.update(optional)
            tqdm.write(f"Added {len(optional)} symbols from optional sources")
    
    # Write to staging
    _write_staging(syms)
    tqdm.write(f"\nTotal candidates collected: {len(syms)}")
    return syms

def load_manual() -> List[str]:
    """Load symbols from manual input file"""
    if not MANUAL_FILE.exists():
        return []
    text = MANUAL_FILE.read_text(encoding="utf-8")
    return _normalize_tokens(text)

def validate_and_commit(symbols: List[str]) -> None:
    """Validate symbols and update registry with robust summary."""
    if not symbols:
        print("No symbols to validate.", flush=True)
        return

    print(f"\nValidating {len(symbols)} symbols via Yahoo Finance...", flush=True)
    print("This may take several minutes...", flush=True)

    # Write initial progress
    write_progress({
        "status": "running",
        "phase": "validation",
        "total": len(symbols),
        "done": 0,
        "message": f"Validating {len(symbols)} symbols..."
    })

    recs = []
    agg  = {"rate_limit": 0, "timeout": 0, "no_price_data": 0, "other": 0}
    err  = None

    try:
        with _silence_yf(), redirect_stderr(_SilentIO()):
            recs, agg = validate_symbols(symbols)
    except Exception as e:
        err = e
        # Convert all to unknown on catastrophic failure
        recs = [{
            "symbol": s, "original": s, "status": "unknown",
            "error_code": "other", "error_msg": str(e)[:200],
            "meta_exists": False, "has_price": False
        } for s in symbols]

    # Update progress - validation complete, now committing
    write_progress({
        "status": "running",
        "phase": "committing",
        "total": len(symbols),
        "done": len(symbols),
        "message": "Updating database..."
    })

    # upsert_validation_results returns 6 values
    try:
        n_act, n_stale, n_inv, n_unk, additions, removals = upsert_validation_results(recs)
    except Exception as e:
        # If the DB write fails, report and re-raise after printing
        print("\n[ERROR] Failed to persist validation results:", str(e)[:200], flush=True)
        write_progress({
            "status": "error",
            "message": f"Database error: {str(e)[:100]}"
        })
        raise

    print("\n" + "="*60, flush=True)
    print("Validation Results:", flush=True)
    print(f"  Active:   {n_act}", flush=True)
    print(f"  Stale:    {n_stale}", flush=True)
    print(f"  Invalid:  {n_inv}", flush=True)
    if n_unk:
        print(f"  Unknown:  {n_unk}", flush=True)
    if additions:
        print(f"  New additions: {len(additions)}", flush=True)
    if removals:
        print(f"  Removed:       {len(removals)}", flush=True)

    # Diagnostics
    rl = agg.get("rate_limit", 0)
    to = agg.get("timeout", 0)
    npd = agg.get("no_price_data", 0)
    oth = agg.get("other", 0)
    if any([rl, to, npd, oth]) or err:
        print("  Diagnostics:", flush=True)
        if rl: print(f"    Rate limited: {rl}", flush=True)
        if to: print(f"    Timeouts:     {to}", flush=True)
        if npd: print(f"    No price data:{npd}", flush=True)
        if oth: print(f"    Other:        {oth}", flush=True)
        if err: print(f"    Exception:    {type(err).__name__}: {err}", flush=True)
    print("="*60, flush=True)

    # Write final progress with results
    write_progress({
        "status": "complete",
        "phase": "done",
        "total": len(symbols),
        "done": len(symbols),
        "active": n_act,
        "stale": n_stale,
        "invalid": n_inv,
        "unknown": n_unk,
        "rate_limit": rl,
        "timeouts": to,
        "message": f"Validation complete: {n_act} active, {n_stale} stale, {n_inv} invalid, {n_unk} unknown"
    })

    if removals:
        write_removal_log(removals)

def cmd_full(force_revalidate: bool = False, only_status: List[str] = None):
    """Full pipeline: scrape, validate, export"""
    print("\n" + "="*60)
    print("GLOBAL TICKER LIBRARY - FULL SCAN")
    print("="*60)

    # Banner: which DB & master file are we using?
    print(f"DB file:      {DB_PATH}")
    print(f"Master file:  {MASTER_FILE}")
    print(f"Data dir:     {DATA_DIR}\n")

    # Clear any previous progress
    clear_progress()
    
    init_db()
    
    try:
        # Scrape sources
        candidates = scrape_sources(include_optional=False)
        
        # Add to registry as candidates
        upserted = upsert_candidates(candidates, "SCRAPE:CURATED")
        tqdm.write(f"\nAdded {upserted} new candidates to registry")
        
        # Add manual tokens if present
        manual = load_manual()
        if manual:
            manual_added = upsert_candidates(manual, "MANUAL:FILE")
            tqdm.write(f"Added {manual_added} manual candidates")
            candidates.update(manual)
        
        # Show who WOULD be validated under current policy
        bd = to_validate_breakdown(force=force_revalidate)
        print("\nEligible for validation (by status):")
        print(f"  candidate: {bd['candidate']}")
        print(f"  unknown:   {bd['unknown']}")
        print(f"  stale:     {bd['stale']}")
        print(f"  invalid:   {bd['invalid']}")
        print(f"  active(>TTL): {bd['active_ttl']}")

        # Build the final validation list
        only_status_set = set(only_status) if only_status else None
        to_validate = get_symbols_to_validate(force=force_revalidate, only_status=only_status_set)
        tqdm.write(f"\nSymbols requiring validation: {len(to_validate)}")
        
        # Validate
        if to_validate:
            validate_and_commit(to_validate)
        
        # Export active list
        n = export_active()
        tqdm.write(f"\n{'='*60}")
        tqdm.write(f"Exported {n} ACTIVE symbols to {MASTER_FILE.name}")
        
    except Exception as e:
        tqdm.write(f"\n[ERROR] Validation failed: {str(e)[:200]}")
        import traceback
        traceback.print_exc()
    
    finally:
        tqdm.write(f"{'='*60}\n")
        
        # Always show summary
        c = counts()
        print("\nRegistry Summary:")
        print(f"  Active: {c['active']}")
        print(f"  Stale: {c['stale']}")
        print(f"  Invalid: {c['invalid']}")
        print(f"  Candidates: {c['candidate']}")
        if c.get('unknown', 0) > 0:
            print(f"  Unknown: {c['unknown']}")
        print(f"  Total: {c['total']}")
        
        # Clear progress after delay so Dashboard can see final status
        import time
        time.sleep(5)  # Give Dashboard 5 seconds to see completion
        clear_progress()

def cmd_cleanup(dry_run: bool = False):
    """Remove stale symbols meeting removal criteria (supports --dry-run)."""
    print("\n" + "="*60)
    print("CLEANUP - REMOVING STALE SYMBOLS")
    print("="*60)

    init_db()

    # Get current counts
    before = counts()

    # Cleanup (optionally dry-run)
    affected, invalidated = cleanup_stale(dry_run=dry_run)

    if affected > 0:
        header = "DRY RUN: would invalidate" if dry_run else "Invalidated"
        tqdm.write(f"\n{header} {affected} stale symbols:")
        for sym in invalidated[:20]:  # Show first 20
            tqdm.write(f"  - {sym}")
        if len(invalidated) > 20:
            tqdm.write(f"  ... and {len(invalidated) - 20} more")

        # Write to removals log only on real runs
        if not dry_run and invalidated:
            write_removal_log(invalidated)
    else:
        tqdm.write("\nNo symbols met removal criteria")

    # Re-export only on real runs
    if not dry_run:
        n = export_active()
        tqdm.write(f"\nRe-exported {n} ACTIVE symbols to {MASTER_FILE.name}")

    # Show changes
    after = counts()
    print("\nChanges:")
    print(f"  Active: {before['active']} -> {after['active']}")
    print(f"  Invalid: {before['invalid']} -> {after['invalid']}")

def cmd_validate_manual():
    """Validate only manual input symbols"""
    print("\n" + "="*60)
    print("VALIDATING MANUAL INPUT")
    print("="*60)
    
    init_db()
    
    manual = load_manual()
    if not manual:
        tqdm.write("\nNo symbols found in manual_input.txt")
        return
    
    tqdm.write(f"\nFound {len(manual)} symbols in manual input")
    
    # Add as candidates
    upsert_candidates(manual, "MANUAL:CLI")
    
    # Validate
    validate_and_commit(manual)
    
    # Export
    n = export_active()
    tqdm.write(f"\nExported {n} ACTIVE symbols to {MASTER_FILE.name}")

def cmd_stats():
    """Show current statistics"""
    init_db()
    c = counts()
    
    print("\n" + "="*60)
    print("GLOBAL TICKER LIBRARY STATISTICS")
    print("="*60)
    print(f"\nActive symbols: {c['active']:,}")
    print(f"Stale symbols: {c['stale']:,}")
    print(f"Invalid symbols: {c['invalid']:,}")
    print(f"Pending candidates: {c['candidate']:,}")
    print(f"Total in registry: {c['total']:,}")
    
    if MASTER_FILE.exists():
        master_count = len(MASTER_FILE.read_text().split(','))
        print(f"\nSymbols in {MASTER_FILE.name}: {master_count:,}")
    
    # Simplified - no longer tracking source breakdown
    
    print("="*60)

def main():
    parser = argparse.ArgumentParser(
        description="Global Ticker Library - Discover and validate ticker symbols",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py --full                         # Full discovery, validation, and export
  python run.py --full --force-revalidate      # Ignore TTLs; re-check everything
  python run.py --full --only-status stale     # Only re-check stale (honors TTL unless --force-revalidate)
  python run.py --validate-manual              # Process manual_input.txt only
  python run.py --cleanup                      # Remove confirmed stale symbols
  python run.py --cleanup --dry-run            # Preview removals without committing
  python run.py --stats                        # Show current statistics
        """
    )

    parser.add_argument("--full", action="store_true",
                        help="Full pipeline: scrape, validate, export")
    parser.add_argument("--cleanup", action="store_true",
                        help="Remove stale symbols (2-strike rule)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview cleanup without changing the database")
    parser.add_argument("--validate-manual", action="store_true",
                        help="Validate symbols from manual_input.txt")
    parser.add_argument("--stats", action="store_true",
                        help="Show current statistics")
    parser.add_argument("--include-optional", action="store_true",
                        help="Include optional sources (future)")

    # NEW:
    parser.add_argument("--force-revalidate", action="store_true",
                        help="Ignore TTL and re-check all eligible symbols")
    parser.add_argument("--only-status", nargs="*", choices=["candidate","unknown","stale","invalid"],
                        help="Restrict validation to these non-active statuses")

    args = parser.parse_args()

    # Ensure data directory exists
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Run requested command
    if args.full:
        cmd_full(force_revalidate=args.force_revalidate, only_status=args.only_status)
    elif args.cleanup:
        cmd_cleanup(dry_run=args.dry_run)
    elif args.validate_manual:
        cmd_validate_manual()
    elif args.stats:
        cmd_stats()
    else:
        parser.print_help()
        print("\nNo command specified. Use --help for options.")

if __name__ == "__main__":
    main()