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

# Chunked validation settings
_VALIDATE_CHUNK = 2000  # Commit every 2000 symbols to avoid losing work

class _SilentIO(io.StringIO):
    def write(self, *args, **kwargs):
        # swallow
        return 0

def _silence_yf():
    return redirect_stdout(_SilentIO())

def _chunked(seq, n):
    """Split sequence into chunks of size n"""
    for i in range(0, len(seq), n):
        yield seq[i:i+n]

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

def validate_and_commit_chunked(symbols: List[str], gentle: bool = False) -> None:
    """
    Validate in chunks and commit after each chunk so work is never lost.
    Also emits progress that the Dash UI can consume.
    """
    import time
    total = len(symbols)
    if total == 0:
        print("No symbols to validate.", flush=True)
        return

    print(f"\nValidating {total} symbols via Yahoo Finance (chunked {_VALIDATE_CHUNK}s)...", flush=True)

    # running totals for the CLI summary and progress
    agg_total = {"rate_limit": 0, "timeout": 0, "no_price_data": 0, "not_found": 0, "other": 0}
    n_active = n_stale = n_invalid = n_unknown = 0
    committed = 0
    chunks = list(_chunked(symbols, _VALIDATE_CHUNK))
    start_time = time.time()
    
    # Get initial DB counts
    from global_ticker_library.registry import counts
    initial_counts = counts()

    try:
        for idx, chunk in enumerate(chunks, start=1):
            chunk_start = time.time()
            
            # Validate chunk
            results, agg = validate_symbols(chunk, gentle=gentle, progress=False)
            
            # merge aggregates
            for k, v in (agg or {}).items():
                agg_total[k] = agg_total.get(k, 0) + int(v or 0)

            # commit results immediately
            a, s, i, u, additions, removals = upsert_validation_results(results)
            n_active += a
            n_stale += s
            n_invalid += i
            n_unknown += u
            committed += len(chunk)
            
            # Get current DB counts after commit
            current_counts = counts()
            
            # Calculate progress and time estimates
            percent = (committed / total) * 100
            elapsed = time.time() - start_time
            if committed > 0:
                rate = committed / elapsed  # symbols per second
                remaining = total - committed
                est_remaining_secs = remaining / rate if rate > 0 else 0
                est_remaining_mins = int(est_remaining_secs / 60)
            else:
                est_remaining_mins = 0

            # Write comprehensive progress
            if write_progress:
                write_progress({
                    "status": "running",
                    "phase": "validation",
                    # Overall progress
                    "overall_total": total,
                    "overall_done": committed,
                    "current_chunk": idx,
                    "total_chunks": len(chunks),
                    # Cumulative validation results
                    "cumulative_active": n_active,
                    "cumulative_stale": n_stale,
                    "cumulative_invalid": n_invalid,
                    "cumulative_unknown": n_unknown,
                    # Current DB state
                    "db_candidates": current_counts.get('candidate', 0),
                    "db_active": current_counts.get('active', 0),
                    "db_stale": current_counts.get('stale', 0),
                    "db_invalid": current_counts.get('invalid', 0),
                    "db_unknown": current_counts.get('unknown', 0),
                    # Progress metrics
                    "percent_complete": round(percent, 1),
                    "estimated_time_remaining": f"{est_remaining_mins} minutes" if est_remaining_mins > 0 else "calculating...",
                    # Clear message
                    "message": f"Chunk {idx}/{len(chunks)}: Validated {committed:,}/{total:,} symbols ({percent:.1f}%)",
                    # Error tracking
                    "rate_limits": agg_total.get("rate_limit", 0),
                    "timeouts": agg_total.get("timeout", 0),
                    "no_price_data": agg_total.get("no_price_data", 0),
                    "other_errors": agg_total.get("other", 0),
                })
            
            # Better console output
            chunk_time = time.time() - chunk_start
            print(f"  Chunk {idx}/{len(chunks)} complete ({chunk_time:.1f}s): "
                  f"{committed:,}/{total:,} symbols ({percent:.1f}%) | "
                  f"This chunk: {a} active, {s} stale, {i} invalid, {u} unknown | "
                  f"DB now has {current_counts.get('unknown', 0):,} unknown", 
                  flush=True)

        # success summary
        print("\n" + "="*60, flush=True)
        print("Validation Results:", flush=True)
        print(f"  Active:  {n_active}", flush=True)
        print(f"  Stale:   {n_stale}", flush=True)
        print(f"  Invalid: {n_invalid}", flush=True)
        if n_unknown:
            print(f"  Unknown: {n_unknown}", flush=True)
        print("="*60, flush=True)

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Stopping after last committed chunk.", flush=True)
        # fall through to finally; progress will say 'complete' with partial results

    except Exception as e:
        import traceback
        traceback.print_exc()
        if write_progress:
            write_progress({"status": "error", "message": str(e)[:200]})
        raise

    finally:
        # Always mark completion/cancel so UI never shows 'running' forever
        if write_progress:
            final_counts = counts()
            write_progress({
                "status": "complete",
                "phase": "done",
                "overall_total": total,
                "overall_done": committed,
                "cumulative_active": n_active,
                "cumulative_stale": n_stale,
                "cumulative_invalid": n_invalid,
                "cumulative_unknown": n_unknown,
                "db_candidates": final_counts.get('candidate', 0),
                "db_active": final_counts.get('active', 0),
                "db_stale": final_counts.get('stale', 0),
                "db_invalid": final_counts.get('invalid', 0),
                "db_unknown": final_counts.get('unknown', 0),
                "rate_limits": agg_total.get("rate_limit", 0),
                "timeouts": agg_total.get("timeout", 0),
                "message": f"Validation complete: {n_active} active, {n_stale} stale, {n_invalid} invalid, {n_unknown} unknown",
            })
        
        # Export active symbols
        n_exported = export_active()
        print(f"\nExported {n_exported} active symbols to {MASTER_FILE.name}", flush=True)

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
        
        # Validate using chunked approach
        if to_validate:
            validate_and_commit_chunked(to_validate, gentle=False)
        
        # Export active list
        n = export_active()
        tqdm.write(f"\n{'='*60}")
        tqdm.write(f"Exported {n} ACTIVE symbols to {MASTER_FILE.name}")
        
    except Exception as e:
        tqdm.write(f"\n[ERROR] Validation failed: {str(e)[:200]}")
        import traceback
        traceback.print_exc()
    
    finally:
        print("="*60)
        
        # Always show final summary
        c = counts()
        print("\nFINAL REGISTRY STATUS:")
        print(f"  Active:     {c['active']:,}")
        print(f"  Stale:      {c['stale']:,}")
        print(f"  Invalid:    {c['invalid']:,}")
        if c.get('unknown', 0) > 0:
            print(f"  Unknown:    {c['unknown']:,}")
        print(f"  Candidates: {c['candidate']:,}")
        print(f"  Total:      {c['total']:,}")
        
        # Show master file status
        if MASTER_FILE.exists():
            master_count = len(MASTER_FILE.read_text().split(','))
            print(f"\n✓ Master file contains {master_count:,} active symbols")
        
        print("="*60)
        
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
    
    # Get initial counts for comparison
    before_counts = counts()
    
    # Get all candidates that need validation
    to_validate = get_symbols_to_validate()
    
    if not to_validate:
        print("\nNo symbols need validation")
        print("\nCurrent Registry Status:")
        print(f"  Active:     {before_counts['active']:,}")
        print(f"  Stale:      {before_counts['stale']:,}")
        print(f"  Invalid:    {before_counts['invalid']:,}")
        print(f"  Unknown:    {before_counts.get('unknown', 0):,}")
        print(f"  Candidates: {before_counts['candidate']:,}")
        print(f"  Total:      {before_counts['total']:,}")
        return
    
    print(f"\nFound {len(to_validate):,} symbols to validate")
    
    # Use chunked validation (it will print progress)
    validate_and_commit_chunked(to_validate, gentle=True)
    
    # Get final counts for summary
    after_counts = counts()
    
    # Calculate changes
    active_change = after_counts['active'] - before_counts['active']
    stale_change = after_counts['stale'] - before_counts['stale']
    invalid_change = after_counts['invalid'] - before_counts['invalid']
    unknown_change = after_counts.get('unknown', 0) - before_counts.get('unknown', 0)
    candidate_change = after_counts['candidate'] - before_counts['candidate']
    
    # Show changes summary
    print("\n" + "="*60)
    print("VALIDATION SUMMARY - Database Changes:")
    print("="*60)
    print(f"  Active:     {before_counts['active']:,} → {after_counts['active']:,} ({active_change:+,})")
    print(f"  Stale:      {before_counts['stale']:,} → {after_counts['stale']:,} ({stale_change:+,})")
    print(f"  Invalid:    {before_counts['invalid']:,} → {after_counts['invalid']:,} ({invalid_change:+,})")
    print(f"  Unknown:    {before_counts.get('unknown', 0):,} → {after_counts.get('unknown', 0):,} ({unknown_change:+,})")
    print(f"  Candidates: {before_counts['candidate']:,} → {after_counts['candidate']:,} ({candidate_change:+,})")
    print(f"  Total:      {before_counts['total']:,} → {after_counts['total']:,}")
    
    # Export active symbols
    n = export_active()
    print(f"\n✓ Exported {n:,} ACTIVE symbols to {MASTER_FILE.name}")
    print("="*60)

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
    print(f"Unknown symbols: {c.get('unknown', 0):,}")
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