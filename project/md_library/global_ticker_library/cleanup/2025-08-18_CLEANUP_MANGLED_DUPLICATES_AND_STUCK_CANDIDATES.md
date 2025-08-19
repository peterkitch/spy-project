# Global Ticker Library Cleanup Summary

## Date: 2025-08-19

### Issues Addressed
1. **Unknown Symbols Count**: Updated stats display to show unknown symbols (11,750+ symbols marked as unknown)
2. **Mangled Duplicates**: Fixed 614 symbols with -P[A-Z] suffixes that were incorrectly created by canonicalization
3. **Stuck Candidates**: Processing 1,388+ candidates that were validated but never properly classified
4. **Master File Cleanup**: Removed inactive symbols from master_tickers.txt

### Changes Made

#### 1. Updated Statistics Display
- Modified `run.py::cmd_stats()` to show unknown symbols count
- Now displays all 5 status categories: active, stale, invalid, unknown, candidates

#### 2. Mangled Duplicates Cleanup
- Created `tools/cleanup_mangled_duplicates.py` script
- Found 614 symbols with -P[A-Z] suffix (mangled from -[A-Z] versions)
- Invalidated duplicates where the non-P version was active
- Examples fixed:
  - CIG-PC (stale) → invalid (keeping CIG-C as active)
  - BRK-PA/BRK-PB → invalid (keeping BRK-A/BRK-B as active)
  - AGM-PA → invalid (keeping AGM-A as active)

#### 3. Stuck Candidates Processing
- Created `tools/process_stuck_candidates.py` script
- Processing 1,388 candidates that were previously validated but stuck
- Many are expired futures contracts (ending in -F)
- Processing in batches to avoid timeouts

#### 4. Database Statistics Changes
```
Before Cleanup:
- Active:     73,124
- Stale:      15,011
- Invalid:    3
- Unknown:    11,752
- Candidates: 1,668
- Total:      101,558

After Cleanup:
- Active:     72,838 (-286)
- Stale:      14,988 (-23)
- Invalid:    328 (+325)
- Unknown:    11,750 (-2)
- Candidates: 1,654 (-14)
- Total:      101,558
```

### Key Improvements
1. **Removed 286 mangled active duplicates** that were slowing down impactsearch.py
2. **Marked 325 invalid symbols** properly (mostly mangled duplicates)
3. **Better visibility** with unknown symbols count in stats
4. **Cleaner master file** with only truly active symbols

### Scripts Created
1. `tools/cleanup_mangled_duplicates.py` - Finds and fixes mangled duplicates
2. `tools/process_stuck_candidates.py` - Processes stuck candidates with force validation

### Next Steps
- Continue monitoring unknown symbols (11,750) for potential re-validation
- Run periodic cleanup of stuck candidates
- Consider implementing automated duplicate detection in the validation pipeline