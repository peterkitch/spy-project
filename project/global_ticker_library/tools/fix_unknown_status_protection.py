#!/usr/bin/env python3
"""
Fix for protecting active symbols from being marked as unknown during rate limits.

The Problem:
- When validation hits rate limits or timeouts, symbols get marked as "unknown"
- This happens even if they were previously "active" 
- This causes active symbols to disappear from the master file!

The Solution:
- Only mark symbols as "unknown" if they were NOT previously "active"
- Active symbols should remain active when we hit transient errors
- Only change active status when we have definitive information
"""

def show_fix():
    print("""
CURRENT PROBLEMATIC CODE in registry.py (line 313-323):
============================================================
if status == "unknown" or error_code in ("rate_limit", "timeout"):
    # Transient error - mark as unknown for retry
    retry_count += 1
    cur.execute(
        \"\"\"UPDATE tickers
           SET status='unknown', retry_count=?, last_error_code=?, 
               last_error_msg=?, last_verified_utc=?
           WHERE symbol=?\"\"\",
        (retry_count, error_code, error_msg, now, original)
    )
    n_unknown += 1

FIXED CODE:
============================================================
if status == "unknown" or error_code in ("rate_limit", "timeout"):
    # Transient error - but DON'T change active symbols to unknown!
    retry_count += 1
    
    # Only update status to unknown if NOT currently active
    if old_status == 'active':
        # Keep it active but record the error for monitoring
        cur.execute(
            \"\"\"UPDATE tickers
               SET retry_count=?, last_error_code=?, 
                   last_error_msg=?, last_verified_utc=?
               WHERE symbol=?\"\"\",
            (retry_count, error_code, error_msg, now, original)
        )
        # Don't count as unknown - it's still active
    else:
        # Not active, safe to mark as unknown for retry
        cur.execute(
            \"\"\"UPDATE tickers
               SET status='unknown', retry_count=?, last_error_code=?, 
                   last_error_msg=?, last_verified_utc=?
               WHERE symbol=?\"\"\",
            (retry_count, error_code, error_msg, now, original)
        )
        n_unknown += 1

ALTERNATIVE ELEGANT SOLUTION (using SQL CASE):
============================================================
if status == "unknown" or error_code in ("rate_limit", "timeout"):
    # Transient error - preserve active status
    retry_count += 1
    cur.execute(
        \"\"\"UPDATE tickers
           SET status = CASE 
                          WHEN status = 'active' THEN 'active'  -- Preserve active
                          ELSE 'unknown'                         -- Others become unknown
                        END,
               retry_count=?, 
               last_error_code=?, 
               last_error_msg=?, 
               last_verified_utc=?
           WHERE symbol=?\"\"\",
        (retry_count, error_code, error_msg, now, original)
    )
    # Only count as unknown if it wasn't active
    if old_status != 'active':
        n_unknown += 1
    """)

if __name__ == "__main__":
    show_fix()
    print("\nThis fix ensures that:")
    print("1. Active symbols stay active during rate limits")
    print("2. Only non-active symbols get marked as unknown")
    print("3. We still track retry counts and errors for all symbols")
    print("4. The master file won't lose active symbols due to transient errors")