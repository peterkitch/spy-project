# Spymaster Documentation

Main trading analysis dashboard documentation.

## Categories

### [bugs/](bugs/)
- **2025-01-14_ADAPTIVE_INTERVAL_FIXES_6_ISSUES.md** - Fixed 6 issues with adaptive interval system
- **2025-01-15_LOGGER_MODULE_FIX_DETAILS.md** - Logger module configuration fixes
- **2025-01-15_LOGGING_AND_FETCH_FIXES.md** - Logging and data fetching improvements

### [performance/](performance/)
- **2025-01-14_ADAPTIVE_INTERVAL_PERFORMANCE_6X_FASTER.md** - 6x performance improvement via adaptive intervals

### [refactoring/](refactoring/)
- **2025-01-15_CODE_CLEANUP_667_LINES_REMOVED.md** - Major code cleanup removing 667 lines

## Key Features

- **Adaptive Interval System**: Dynamic polling intervals based on ticker complexity
- **SMA Pair Optimization**: Systematic trading analysis with configurable windows
- **Secondary Ticker Analysis**: Multi-ticker comparison capabilities
- **Real-time Performance Metrics**: Sharpe ratios, capture ratios, win/loss statistics

## Recent Changes

### 2025-01-15
- Fixed secondary ticker array shape mismatch error
- Increased MIN_INTERVAL_MS to 1000ms to prevent flickering
- Implemented windowed fetch for secondary tickers
- Simplified SMA reset logic with context checking