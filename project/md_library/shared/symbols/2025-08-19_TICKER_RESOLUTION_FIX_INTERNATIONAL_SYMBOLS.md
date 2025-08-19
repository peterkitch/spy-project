# Ticker Resolution Fix for International Symbols

## Date: 2025-08-19

## Problem Identified
International ticker symbols (like AHT.L for London Stock Exchange) were being incorrectly transformed to AHT-L, causing Yahoo Finance API calls to fail with "symbol may be delisted" errors.

## Root Cause
The `normalize_ticker()` function in `shared_symbols.py` was applying a broad regex pattern that converted ALL dots to dashes, not distinguishing between:
- **Share class suffixes** (BRK.B → BRK-B, which is correct for US symbols)
- **International exchange suffixes** (AHT.L should stay AHT.L for London)

## Investigation Findings
- Master list contains **72,917 verified tickers** with correct formatting
- Found **55 unique international exchange suffixes** (.L, .TO, .AX, .BO, .MX, .SG, etc.)
- US share classes already use dash format in master (BRK-B, BF-A, PSA-F)
- The regex pattern `^[A-Z]{1,5}[./][A-Z0-9]{1,3}$` was incorrectly matching all international suffixes

## Solution Implemented

### 1. Master-Driven Resolution
Replaced regex-based normalization with master list lookup:
- Uses `master_tickers.txt` (72,917 symbols) as source of truth
- Creates smart aliases (BRK.B → BRK-B) only when master confirms
- Preserves exact Yahoo Finance format for all symbols

### 2. Files Modified

#### signal_library/shared_symbols.py
- Complete rewrite with `resolve_symbol()` function
- Loads master list with thread-safe locking
- Handles comma/newline/mixed formats robustly
- Returns `(vendor_symbol, library_key)` tuple

#### onepass.py
- Replaced all `normalize_ticker()` with `resolve_symbol()`
- Fixed logging to use `vendor_symbol` consistently
- Added backward compatibility for library loading

#### impactsearch.py
- Updated to use `resolve_symbol()` throughout
- Fixed PeriodCapabilityCache to use vendor symbols
- Maintains parity with onepass.py

### 3. Hardening Improvements
- Thread-safe master loading with `Lock()`
- Robust file parsing (handles various formats)
- Logging instead of print statements
- Backward compatibility for existing libraries
- Clear API contract documentation

## Test Results
All tests pass:
- **International suffixes**: AHT.L, SHOP.TO, BHP.AX preserved correctly
- **US share classes**: BRK.B → BRK-B aliasing works
- **Crypto normalization**: BTC → BTC-USD
- **Index symbols**: ^GSPC pass through unchanged

## Impact
- Fixes data fetching for all international tickers
- Prevents future ticker mangling issues
- Maintains backward compatibility
- No rebuilding of existing signal libraries required

## Related Files
- `signal_library/shared_symbols.py` - Core resolution logic
- `onepass.py` - Updated to use resolution
- `impactsearch.py` - Updated to use resolution
- `master_tickers.txt` - Source of truth for symbols