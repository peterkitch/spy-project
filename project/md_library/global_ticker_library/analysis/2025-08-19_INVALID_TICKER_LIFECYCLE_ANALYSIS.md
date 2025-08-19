# Invalid Ticker Lifecycle & Flushing Process

## Date: 2025-08-19

## Overview
The Global Ticker Library maintains a list of invalid tickers to avoid repeatedly trying to validate symbols that don't exist. Here's the complete lifecycle and flushing process.

## How Symbols Become Invalid

### 1. **During Validation** (validator_yahoo.py)
Symbols are marked invalid when:
- **HTTP 404 errors** - Symbol not found
- **Empty DataFrames** - No data exists for symbol
- **"Symbol may be delisted"** messages
- **No price data after MAX_RETRIES** (3 attempts)

```python
# Example from validator_yahoo.py:
if not isinstance(hist, pd.DataFrame) or hist.empty:
    return _result_invalid(sym, code="not_found", msg="no history")
```

### 2. **Through Cleanup Scripts**
Manual/automated cleanup when symbols:
- Start with numbers (except crypto pairs)
- Contain invalid characters
- Are expired futures (-F suffix)
- Are expired warrants (-W suffix)
- Fail validation multiple times

## Current Invalid Ticker Stats
```
Total Invalid Symbols: 13,655
```

## Retention Period

### Configuration (gl_config.py):
```python
INVALID_RECHECK_DAYS = 30  # Retry invalid status after 30 days
```

**This means:** Invalid symbols are kept for 30 days before being eligible for re-validation.

## The Flushing Process

### 1. **Automatic Re-validation (After 30 Days)**
When a symbol has been invalid for 30+ days:
- Becomes eligible for re-validation
- If validation requested, will be checked again
- If still invalid, timer resets for another 30 days
- If now valid, status changes to "active"

### 2. **Manual Removal Process**
Currently, there is **NO automatic deletion** of invalid symbols. They remain in the database indefinitely but are excluded from exports.

To permanently remove invalid symbols:

```bash
# Option 1: Delete all invalid symbols older than 90 days
sqlite3 data/registry.db "
DELETE FROM tickers 
WHERE status = 'invalid' 
AND julianday('now') - julianday(invalidated_utc) > 90
"

# Option 2: Delete specific invalid patterns
sqlite3 data/registry.db "
DELETE FROM tickers 
WHERE status = 'invalid' 
AND (symbol LIKE '%-F' OR symbol LIKE '%-W')  -- Expired futures/warrants
"
```

### 3. **Export Filtering**
Invalid symbols are **automatically excluded** from exports:

```python
# From registry.py export_active():
cur.execute("""
    SELECT symbol FROM tickers 
    WHERE status = 'active'
    ORDER BY symbol
""")
# Invalid symbols are NOT included in master_tickers.txt
```

## Why Keep Invalid Symbols?

### Benefits:
1. **Prevents Re-scraping** - Won't collect same bad symbols again
2. **Historical Record** - Know what's been tried
3. **Pattern Detection** - Identify common invalid patterns
4. **Potential Recovery** - Some may become valid later (new IPOs using old tickers)

### Drawbacks:
1. **Database Size** - 13,655 records take space
2. **Dashboard Clutter** - Shows in statistics
3. **No Auto-Cleanup** - Requires manual intervention

## Recommended Maintenance Schedule

### Monthly:
1. Review invalid symbols older than 90 days
2. Delete obvious garbage (numeric strings, test data)
3. Keep recently invalidated symbols (< 90 days)

### Quarterly:
1. Full invalid list review
2. Pattern analysis for bulk removal
3. Database optimization after deletions

## Example Cleanup Commands

### Safe Cleanup (Remove obvious garbage):
```bash
# Remove pure numeric symbols
sqlite3 data/registry.db "
DELETE FROM tickers 
WHERE status = 'invalid' 
AND symbol REGEXP '^[0-9]+$'
"

# Remove expired futures older than 6 months
sqlite3 data/registry.db "
DELETE FROM tickers 
WHERE status = 'invalid' 
AND symbol LIKE '%-F'
AND julianday('now') - julianday(invalidated_utc) > 180
"
```

### Aggressive Cleanup (Remove all old invalids):
```bash
# Remove all invalid symbols older than 1 year
sqlite3 data/registry.db "
DELETE FROM tickers 
WHERE status = 'invalid' 
AND julianday('now') - julianday(invalidated_utc) > 365
"
```

## Current Invalid Composition

Based on recent analysis:
- **4,859** - Expired futures (-F suffix)
- **1,000+** - Numeric/garbage from scraping
- **800+** - Expired warrants/units (-W, -U)
- **500+** - Delisted companies
- **300+** - Canonicalization damage (now fixed)
- **Rest** - Various invalid formats

## Impact on onepass.py

Invalid symbols are **completely excluded** from onepass.py:
- master_tickers.txt only contains active symbols
- Invalid symbols never processed
- Zero performance impact

## Future Improvements

Consider implementing:
1. **Auto-deletion** after 365 days
2. **Archival table** for historical invalids
3. **Pattern-based auto-removal** (e.g., expired futures)
4. **Dashboard cleanup interface**

## Summary

**Current Process:**
1. Symbols marked invalid during validation
2. Kept in database for 30 days minimum
3. Can be re-validated after 30 days
4. Never automatically deleted
5. Excluded from all exports
6. Manual cleanup recommended quarterly

**Key Point:** Invalid symbols don't impact performance (excluded from exports) but do accumulate over time, requiring periodic manual cleanup.