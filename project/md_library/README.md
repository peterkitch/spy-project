# MD Library - Project Documentation

This library contains all documentation for the SPY Project trading analysis platform.

## Structure

### Core Applications

#### **[spymaster/](spymaster/)** - Main Trading Dashboard
- `bugs/` - Bug fixes and issue resolutions
- `performance/` - Performance optimizations
- `refactoring/` - Code cleanup and refactoring

#### **[impactsearch/](impactsearch/)** - Impact Analysis Tool
- Statistical relationship analysis between tickers

#### **[onepass/](onepass/)** - Single-Pass Analysis
- Rapid ticker analysis and signal generation

#### **[global_ticker_library/](global_ticker_library/)** - Ticker Database System
- `analysis/` - System analysis and investigations
- `cleanup/` - Database cleanup operations  
- `features/` - New features and enhancements

### Cross-Script Documentation
- **[shared/](shared/)** - Issues affecting multiple scripts
  - `symbols/` - Symbol and ticker handling
  - `testing/` - Testing issues and investigations

### QuantConnect Algorithms
- **[qc/](qc/)** - QuantConnect algorithm documentation
  - [Clone of Project 9](qc/Clone%20of%20Project%209/) - SMA optimization strategy

## Recent Major Work (August 2025)

### Ticker Resolution Fix
- [International Symbol Fix](shared/symbols/2025-08-19_TICKER_RESOLUTION_FIX_INTERNATIONAL_SYMBOLS.md) - Fixed dot/dash handling for international tickers

### Global Ticker Library Cleanup
- [Root Cause Analysis](global_ticker_library/analysis/2025-08-18_ROOT_CAUSE_ANALYSIS_11752_STUCK_SYMBOLS.md) - Why 11,752 symbols were stuck
- [Final Cleanup](global_ticker_library/cleanup/2025-08-18_FINAL_CLEANUP_ZERO_UNKNOWN_SYMBOLS_ACHIEVED.md) - Achieved 0 unknown symbols

### Key Topics
- Performance optimization strategies
- Bug fixes and resolutions
- Implementation notes
- Backtest results and analysis

## Documentation Standards

All documentation files should include a metadata header:
```yaml
---
script: [spymaster|impactsearch|onepass|qc/Clone of Project 9]
category: [feature|bug|performance|strategy]
date: YYYY-MM-DD
version: 1.0
status: [draft|review|final]
---
```

## Contributing

When adding new documentation:
1. Place files in the appropriate script folder
2. Use descriptive filenames with dates for time-sensitive docs
3. Update the relevant README.md index
4. Include metadata headers
5. Link related documentation