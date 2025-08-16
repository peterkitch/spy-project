# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a quantitative trading analysis web application built with Python and Dash. It implements an adaptive simple moving average (SMA) pair optimization system for systematic trading analysis and mean reversion strategies.

## Development Commands

### Environment Setup
```bash
# Create and activate conda environment
conda env create -f environment.yml
conda activate spyproject2
```

### Running Applications
```bash
# Main trading analysis dashboard (default port 8050)
python spymaster.py

# Impact search analysis tool (port 8051)
python impactsearch.py

# Single-pass analysis
python onepass.py
```

### Building Executable
```bash
# Create standalone executable using PyInstaller
pyinstaller spymaster.spec
```

## Architecture

### Core Structure
- **spymaster.py** (4,971 lines): Main Dash web application serving the trading analysis dashboard
- **impactsearch.py**: Statistical relationship analysis between different tickers
- **onepass.py**: Single-pass analysis module for quick computations

### Data Flow
1. **Market Data**: Fetched via yfinance API into pandas DataFrames
2. **Signal Processing**: SMA calculations with configurable windows and thresholds
3. **Statistical Analysis**: Computation of Sharpe ratios, capture ratios, win/loss statistics
4. **Caching Layer**: Results stored as pickle files (`{TICKER}_precomputed_results.pkl`)
5. **Status Tracking**: JSON files track processing progress (`{TICKER}_status.json`)

### Key Technologies
- **Web Framework**: Dash with Bootstrap Components on Flask backend
- **Data Processing**: pandas, numpy, scipy for vectorized calculations
- **Visualization**: Plotly for interactive charts
- **Concurrency**: Threading for parallel ticker processing
- **Caching**: joblib Memory and pickle serialization

### UI Components
- Dark theme with green text on black background
- Multi-section input forms for batch processing
- Interactive result tables and charts
- Built-in help modal system
- Real-time progress tracking

### Performance Considerations
- Heavy use of caching to avoid redundant calculations
- Vectorized operations using scipy for speed
- Multi-threaded processing for concurrent ticker analysis
- Progress bars (tqdm) for long-running operations
- Optimized interval updates from 5 seconds to 3 seconds for faster chart loading

### Data Files
- **Input**: Market data fetched from yfinance
- **Cache**: `*.pkl` files for precomputed results
- **Status**: `*.json` files tracking analysis progress
- **Output**: Excel files for detailed analysis exports
- **Logs**: `debug.log`, `analysis.log` for troubleshooting

## Recent Updates (Session: 2025-01-08)

### Phase 2 Enhancements Completed

#### 1. Position Configuration Dictionary
- Added `POSITION_CONFIGS` dictionary to centralize styling
- Consistent icons, colors, and symbols across all position types
- Simplified maintenance and updates to position display

#### 2. Risk Metrics Integration
- Added `calculate_risk_metrics()` method to PerformanceMetrics class
- Shows expected return, max potential loss/gain, risk/reward ratio
- Based on 60-day historical data percentiles (5th/95th)
- Integrated into position status cards

#### 3. Signal Strength Visualization
- Calculates percentage divergence between SMAs
- Visual progress bar showing signal strength (0-100%)
- Color-coded: green (>5%), yellow (2-5%), red (<2%)
- Added to action required card

#### 4. Position Performance Tracking
- Session-based position history storage with `dcc.Store`
- Tracks entry/exit prices, holding days, and P&L
- Automatic P&L calculation for closed positions
- Maintains rolling 30-position history

#### 5. Position History Table
- New `create_position_history_table()` method
- Shows last 10 positions with entry/exit prices
- Color-coded P&L display
- Integrated into Performance Overview section

## Recent Updates (Session: 2025-01-07)

### Major Improvements Completed

#### 1. Time-Weighted Performance Rating System
- Implemented annualized return calculations for fair comparison across time periods
- Applied to both AI-Optimized and Manual SMA sections
- Grades now based on annualized performance rather than absolute returns

#### 2. Individual Leader Metrics
- Added Sharpe Ratio and Max Drawdown calculations for Buy Leader and Short Leader
- Fixed pandas Series vs numpy array handling issues
- Metrics now display in Strategy Comparison table

#### 3. Complete UI Overhaul of Dynamic Master Trading Strategy Section
- **Restructured into 3 clear sections:**
  - Position Status & Required Action (what to do NOW)
  - Performance Overview (consolidated metrics)
  - Signal Change Thresholds (clear price levels)
- **New Visual Components:**
  - `create_position_status_card()` - Current position display
  - `create_action_required_card()` - Prominent action needed
  - `create_price_threshold_visual()` - Clear threshold ladder
  - `create_position_timeline()` - Visual position progression
- **Eliminated redundancies:** Removed 6 duplicate sections

#### 4. Critical Fixes
- Fixed "Hold" → "Cash" terminology for None positions
- Fixed signal strength calculations (no fast/slow SMA assumptions)
- Fixed position return calculation (actual P&L from entry)
- Enhanced confidence calculation (multi-factor weighting)
- Robust price threshold parsing with error handling
- Added position transition warnings

#### 5. Trading Mechanics Clarification
- All trades execute at market CLOSE (4:00 PM ET)
- Positions held from close to close (minimum)
- Clear date/time stamps on all recommendations

## Recent Updates (Session: 2025-01-12)

### Current Uncommitted Enhancements (Ready to Commit)

#### Signal Flip Probability Calculator ✅ (Accelerated from Phase 4)
- **`calculate_signal_flip_probability()`**: New method calculating likelihood of signal changes
- Uses 10-day (70% weight) and 30-day (30% weight) volatility analysis
- 5-level risk assessment: Very Low (<20%), Low (20-40%), Medium (40-60%), High (60-80%), Very High (>80%)
- Proximity-based threshold distance calculations
- Integrated into action_required_card with color-coded warnings
- Clear messaging about signal stability and flip risks

#### Enhanced Visual Effects System ✅
- **5-tier signal strength system**: EXTREME/STRONG/MODERATE/WEAK/VERY WEAK
- Dynamic glow effects (up to 25px for EXTREME signals)
- Pulse animations for signals ≥60% strength
- Enhanced emoji indicators (🔥🔥🔥 for EXTREME)
- Variable border widths (1-3px) based on signal strength
- Gradient backgrounds and animated progress bars

#### Risk/Reward Matrix Redesign ✅
- Quality ratings with emoji indicators (🎯 Excellent, ✅ Good, ⚠️ Fair, ⛔ Poor)
- Visual progress bars (0-100 scale) for risk and reward
- Dynamic glow effects based on positioning quality
- New `_get_risk_reward_interpretation()` helper method
- Clear English summaries for investment context

#### Performance Optimizations ✅
- Added `from_callback` and `should_log` parameters to control logging
- Fixed 3-second interval update console spam issue
- Improved callback context detection using dash.callback_context
- Better chart loading state management

## Recent Updates (Session: 2025-01-08)

### Phase 2 Completed ✅

#### Infrastructure & Architecture
- **Added POSITION_CONFIGS dictionary** for centralized position styling
- **Implemented session storage** for ticker-specific position history tracking
- **Fixed callback architecture** (20→21 outputs) to support position history store

#### Critical Bug Fixes
- **Fixed position timing logic**: Positions now correctly enter at dates[i-1]
- **Fixed complex number crashes**: Added comprehensive validation for NaN/infinite values
- **Fixed position history persistence**: Ticker-specific storage prevents data mixing
- **Removed duplicate position update logic**: Eliminated conflicting code sections

#### New Features & Methods
- **`calculate_risk_metrics()`**: Calculates expected return, max loss/gain, risk/reward ratio
- **`create_position_history_table()`**: Comprehensive trade history with performance metrics
- **Enhanced signal strength calculation**: Percentage divergence between SMAs (0-100 scale)

#### Visual Enhancements
- **Signal Strength Meters**:
  - Emoji indicators (🔥 Strong, ⚡ Moderate, ⚠️ Weak, ❄️ Very Weak)
  - Animated progress bars with color coding
  - Glowing effects for strong signals
- **Risk/Reward Display**:
  - Visual quality ratings (🎯 Excellent, ✅ Good, ⚠️ Fair, ⛔ Poor)
  - Color-coded risk/reward bar visualization
  - Detailed metrics with emoji icons
- **Position Performance Summary**:
  - Current streak tracking (e.g., "🔥 3 wins in a row")
  - Best/worst trade display
  - Average hold time
  - Position-specific success rates (Buy vs Short)

## Phase 3 TESTING & VALIDATION (Session: 2025-01-12)

### Implementation Complete - In Testing Phase ✅

All Phase 3 components have been implemented and are undergoing validation:

1. **Market Close Countdown Timer** ⏰ ✅
   - ✅ Basic structure created with `create_market_countdown_timer()`
   - ✅ Countdown interval component added
   - ✅ Callback `update_countdown_timer()` implemented
   - 🧪 Testing real-time updates with market hours
   - 🧪 Validating weekend/holiday handling
   - 🧪 Verifying display updates in UI

2. **Position Sizing Calculator** 📊 ✅
   - ✅ Basic calculator function `create_position_sizing_calculator()`
   - ✅ UI inputs for account value, risk %, stop-loss %
   - 🧪 Testing interactive updates
   - 🧪 Validating calculations with real ticker prices
   - 🧪 Kelly Criterion calculation validation
   - 🧪 Position sizing callback testing

3. **Interactive Price Threshold Slider** 🎯 ✅
   - ✅ Fixed AttributeError with list/dict handling
   - ✅ Basic slider component created
   - 🧪 Testing interactive functionality
   - 🧪 Validating distance calculations
   - 🧪 Refining slider marks and styling
   - 🧪 Testing callback for slider updates

### Active Testing Checklist:
- [x] Components created and integrated
- [ ] Live ticker data validation
- [ ] Market hours countdown verification
- [ ] Position sizing accuracy check
- [ ] Threshold slider responsiveness
- [ ] Cross-browser compatibility
- [ ] Error handling verification
- [ ] Performance under load

### Bug Fixes Applied:
- Fixed `create_interactive_threshold_slider()` to handle list format from threshold_data
- Added proper parsing for price ranges in various formats ("$X - $Y", "above $X", "below $X")
- Resolved layout issues in Risk Management Tools section
- Fixed position history date/price display

## Phase 4 - Future Enhancements (Optional)

### Potential Future Features (Not Currently Planned)

These items are noted for potential future development but are not essential components:

#### Data & Analytics Enhancements
- Confidence intervals around predictions
- Monte Carlo simulation results  
- Backtesting parameter sensitivity analysis
- Alternative risk metrics (VaR, CVaR)
- Correlation matrix between positions

#### Real-Time Features
- Live price updates during market hours
- WebSocket integration for streaming data
- Multi-ticker portfolio optimization

#### Advanced Visualizations
- Enhanced interactive price threshold slider with what-if scenarios
- 3D volatility surface plots
- Heatmap correlation matrices

Note: Machine learning integration, advanced analytics, and institutional features have been deprioritized as they are not essential for the core trading strategy functionality.

## Known Issues to Address
- Position return calculation needs actual entry price tracking
- Confidence calculation could incorporate more factors
- Price threshold visualization could be more interactive

## Code Quality Notes
- PerformanceMetrics class successfully modularized (750+ lines)
- Visual components are reusable and consistent
- Boolean flags reduce redundant calculations
- Error handling improved throughout

## Testing Checklist for Next Session
- [ ] Verify position return calculations with real data
- [ ] Test threshold parsing with edge cases
- [ ] Confirm position transition warnings appear correctly
- [ ] Validate confidence scores across different scenarios
- [ ] Check all visual components render properly

## SMA Pair Optimization Notes

### Important Considerations for SMA Pair Analysis
- The script should not rely on phrases like "fast" or "slow" when discussing SMA properties
- SMA 1 and SMA 2 refer to the first and second inputs for buy pairs
  - A top buy pair can have various SMA configurations (e.g., 10,1 or 1,10)
  - SMA 1 and SMA 2 cannot be the same value
- SMA 3 and SMA 4 refer to the short pair
  - Similar flexibility applies to short pair configurations (e.g., 10,1 or 1,10)
  - SMA 3 and SMA 4 cannot be the same value
- Verify that metric reporting and dashboard visuals accurately reflect these flexible SMA pair configurations

## Development Guidelines & Best Practices

### Documentation Organization
- **NEVER place new markdown files in the root project folder** (except CLAUDE.md)
- **ALWAYS use date prefix and descriptive uppercase title for markdown filenames**: `YYYY-MM-DD_DESCRIPTION_IN_CAPS.md`
  - Date format: YYYY-MM-DD (ISO 8601)
  - Description: Use UPPERCASE with underscores, be specific about the content
  - Include action words like: INVESTIGATION, FIX, ENHANCEMENT, REFACTOR, IMPLEMENTATION, ANALYSIS
  - Good examples:
    - `2025-01-16_UNICODE_AND_SELENIUM_TEST_ISSUE_INVESTIGATIONS.md` (investigation into problems)
    - `2025-01-14_ADAPTIVE_INTERVAL_PERFORMANCE_6X_FASTER.md` (performance improvement)
    - `2025-01-15_CODE_CLEANUP_667_LINES_REMOVED.md` (refactoring work)
  - Avoid vague terms like: FINDINGS, NOTES, CHANGES, UPDATE
- All documentation should be organized in the `md_library/` directory structure:
  - `md_library/spymaster/` - Spymaster-specific documentation
    - `/bugs/` - Bug reports and fixes
    - `/refactoring/` - Refactoring summaries and changes
    - `/performance/` - Performance improvements
    - `/adaptive_interval/` - Adaptive interval related docs
  - `md_library/impactsearch/` - Impactsearch documentation
  - `md_library/onepass/` - Onepass documentation
  - `md_library/shared/` - Shared documentation across scripts (testing, tools, environment)
- Text files (.txt) for quick changes/notes can remain in root temporarily

### Git Branch Naming Conventions
- **Be specific about scope and purpose** - branches should clearly indicate what they affect
- **Use descriptive prefixes** to identify the area of work:
  - `claude-` for CLAUDE.md or Claude behavior updates
  - `spymaster-` for spymaster.py changes
  - `impactsearch-` for impactsearch.py changes
  - `onepass-` for onepass.py changes
  - `docs-` for general documentation (but be specific about which docs)
- **Good branch name examples**:
  - `claude-testing-guidelines` - Updates to Claude's testing behavior
  - `spymaster-unicode-fix` - Fixing Unicode issues in spymaster.py
  - `impactsearch-performance-optimization` - Performance improvements
  - `onepass-sma-calculation-bug` - Specific bug fix in onepass
- **Avoid vague branch names**:
  - `docs/testing-guidelines` - Whose testing guidelines?
  - `fix/bug` - Which bug? Where?
  - `update/readme` - Which readme? What update?
  - `feature/new` - What feature? For which component?
- **Use hyphens**, not underscores or slashes (except for feature/ or bugfix/ prefixes if using git flow)

### Testing Guidelines
- **NEVER use Unicode characters in test scripts or console output**
  - Windows console uses cp1252 encoding which cannot display Unicode characters
  - This causes `UnicodeEncodeError` when Python tries to print Unicode to the Windows terminal
  - Use ASCII alternatives: [OK], [FAIL], [WARNING] instead of ✅, ❌, ⚠️
  - Use simple separators: ===, ---, ### instead of fancy Unicode boxes
- All tests should include verification of newly implemented metrics, visuals, functions, or other components
- It is not enough that the app compiles - verify actual functionality

### Callback & Interval Handling
- **Interval updates are critical for chart loading** (currently 3 seconds)
  - Do NOT block interval callbacks - they enable progressive data loading
  - Charts depend on these intervals to populate properly
  - Optimized from 5 seconds to 3 seconds for better responsiveness
- **Variable scope in callbacks**
  - Variables defined in callback functions are NOT automatically accessible in nested functions
  - Use proper parameter passing or closure patterns
  - The `should_log` pattern requires careful scope management
- **Callback context detection**
  - Use `dash.callback_context` to identify trigger source
  - Distinguish between user actions (ticker changes) and interval updates
  - Apply different logic based on trigger type

### Debugging Dash Applications
- **Console logging control**
  - Separate logging logic from data processing logic
  - Use conditional logging based on callback trigger type
  - Prevent log spam from interval updates while maintaining functionality
- **Data flow understanding**
  - Trace complete execution paths before implementing fixes
  - Understand how data moves through callbacks and updates
  - Consider caching strategies for expensive computations

### Recent Bug Fixes & Lessons Learned

#### Ticker Processing Loop Fix (2025-01-11)
**Problem:** After processing one ticker, entering a new ticker caused repeated logging every 3 seconds (formerly 5 seconds)

**Root Cause:** The `update_dynamic_strategy_display` callback runs on both ticker changes AND interval updates. The `should_log` variable was defined locally but wasn't accessible to all logging code paths.

**Key Lessons:**
1. Variable scope matters - local callback variables don't propagate to nested function calls
2. Interval updates must continue running for charts to load properly
3. Logging should be controlled separately from data processing
4. The 3-second intervals are essential for dashboard functionality

**Solution Applied:**
- Proper callback context detection to identify trigger source
- Conditional logging based on whether ticker changed vs interval update
- Maintained all data processing while controlling console output
- Preserved the critical 3-second refresh cycle for chart updates