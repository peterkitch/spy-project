# MD Library - Project Documentation

This library contains all documentation for the SPY Project trading analysis platform.

## Structure

### Core Applications
- **[spymaster/](spymaster/)** - Main trading analysis dashboard documentation
- **[impactsearch/](impactsearch/)** - Impact search analysis tool documentation  
- **[onepass/](onepass/)** - Single-pass analysis module documentation

### QuantConnect Algorithms
- **[qc/](qc/)** - QuantConnect algorithm documentation
  - [Clone of Project 9](qc/Clone%20of%20Project%209/) - SMA optimization strategy

### Shared Documentation
- **[shared/](shared/)** - Cross-application documentation and architecture notes

## Quick Links

### Recent Updates
- [Adaptive Interval System](spymaster/adaptive_interval/ADAPTIVE_INTERVAL_REPORT.md)
- [Adaptive Fixes Summary](spymaster/adaptive_interval/ADAPTIVE_FIXES_SUMMARY.md)

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