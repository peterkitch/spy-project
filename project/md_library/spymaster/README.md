# Spymaster Documentation

Main trading analysis dashboard documentation.

## Categories

### [adaptive_interval/](adaptive_interval/)
- [ADAPTIVE_FIXES_SUMMARY.md](adaptive_interval/ADAPTIVE_FIXES_SUMMARY.md) - Summary of all adaptive interval fixes
- [ADAPTIVE_INTERVAL_REPORT.md](adaptive_interval/ADAPTIVE_INTERVAL_REPORT.md) - Comprehensive test report

### [performance/](performance/)
Performance optimization notes and strategies.

### [bugs/](bugs/)
Bug reports and fixes.

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