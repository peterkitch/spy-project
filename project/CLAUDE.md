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

## Phase 3 IN PROGRESS (Session: 2025-01-08)

### Partially Implemented (Code exists but needs testing/fixes):

1. **Market Close Countdown Timer** ⏰ 🚧
   - ✅ Basic structure created with `create_market_countdown_timer()`
   - ✅ Countdown interval component added
   - ✅ Callback `update_countdown_timer()` implemented
   - ❌ Need to verify real-time updates work correctly
   - ❌ Test with actual market hours/weekends
   - ❌ Verify display updates properly in UI

2. **Position Sizing Calculator** 📊 🚧
   - ✅ Basic calculator function `create_position_sizing_calculator()`
   - ✅ UI inputs for account value, risk %, stop-loss %
   - ❌ Interactive updates not fully tested
   - ❌ Need to verify calculations with real ticker prices
   - ❌ Kelly Criterion calculation needs validation
   - ❌ Position sizing callback needs proper testing

3. **Interactive Price Threshold Slider** 🎯 🚧
   - ✅ Fixed AttributeError with list/dict handling
   - ✅ Basic slider component created
   - ❌ Interactive functionality not verified
   - ❌ Distance calculations need testing
   - ❌ Slider marks and styling need refinement
   - ❌ Callback for slider updates needs testing

### Known Issues to Fix:

1. **Risk Management Tools Section**
   - Components created but not properly displaying with real data
   - Need to verify integration with existing ticker processing
   - Layout and styling adjustments needed

2. **Position History Report**
   - Still showing issues with dates/prices
   - Need to verify calculations are correct
   - Performance metrics need validation

3. **Market Timer Integration**
   - Countdown timer needs proper testing during market hours
   - Verify timezone handling is correct
   - Test weekend/holiday logic

### Bug Fixes Applied:
- Fixed `create_interactive_threshold_slider()` to handle list format from threshold_data
- Added proper parsing for price ranges in various formats ("$X - $Y", "above $X", "below $X")

### Still TODO in Phase 3:
- [ ] Test all components with live ticker data
- [ ] Verify countdown timer updates correctly
- [ ] Test position sizing calculator interactivity
- [ ] Validate threshold slider functionality
- [ ] Fix any display/layout issues
- [ ] Ensure proper error handling
- [ ] Test during actual market hours
- [ ] Verify all calculations are accurate

## Next Iteration TODO (Phase 4 - Future Session)

### Signal Change Probability Indicator 🎲
   ```python
   # Calculate likelihood of signal changing tomorrow
   # Based on proximity to threshold prices
   # Example: "85% chance signal remains BUY"
   # Warning alerts: "65% chance of flip if price drops 0.5%"
   # Color-coded probability display
   # Location: Add to Action Required card
   ```

4. **Interactive Price Threshold Slider** 🎚️
   ```python
   # Visual slider showing current price position
   # Price ranges directly mapped to Buy/Short/Cash zones
   # Draggable to see "what-if" scenarios
   # Shows exact prices where signals change
   # Color-coded zones (green=buy, red=short, yellow=cash)
   # Real-time signal update as slider moves
   # Location: Replace or enhance current threshold visual
   ```

### Deferred Phase 3 Items (Lower Priority)
- Confidence intervals around predictions
- Monte Carlo simulation results
- Backtesting parameter sensitivity
- Alternative risk metrics (VaR, CVaR)
- Live price updates during market hours
- WebSocket integration for real-time data
- Multi-ticker portfolio optimization
- Correlation matrix between positions

### Future Considerations (Phase 4)
1. **Machine Learning Integration**
   - LSTM/GRU models for price prediction
   - Reinforcement learning for strategy optimization
   - Feature engineering for technical indicators
   
2. **Advanced Analytics**
   - Regime detection and switching models
   - Market microstructure analysis
   - Order flow and volume analysis
   
3. **Institutional Features**
   - Multi-account management
   - Compliance and risk reporting
   - Audit trail and position reconciliation

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

### Testing Guidelines
- **NEVER use Unicode characters in test scripts or console output**
  - Causes `UnicodeEncodeError` on Windows systems
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