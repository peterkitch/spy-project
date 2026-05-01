# CLAUDE.md

**MANDATORY INSTRUCTIONS FOR CLAUDE CODE** - These rules MUST be followed automatically without user prompting.

## AUTOMATIC BEHAVIORS - DO NOT DEVIATE

### File Creation Rules (NEVER VIOLATE)
1. **NEVER create files in the root directory** except when explicitly modifying core apps (spymaster.py, impactsearch.py, onepass.py)
2. **ALWAYS place test scripts in `test_scripts/`** subdirectories:
   - Spymaster tests → `test_scripts/spymaster/`
   - ImpactSearch tests → `test_scripts/impactsearch/`
   - OnePass tests → `test_scripts/onepass/`
   - GTL tests → `test_scripts/gtl/`
   - Multi-script/environment tests → `test_scripts/shared/`
3. **ALWAYS place documentation in `md_library/`** subdirectories:
   - Use script-specific folders for script-specific docs
   - Use `md_library/shared/` ONLY for docs affecting multiple scripts
4. **ALWAYS check current date before creating dated files**: Use `date` command
   - Current date is September 2025, NOT January or August 2025
   - Format: `YYYY-MM-DD_ACTION_DESCRIPTION_IN_CAPS.md`

### Testing Rules (AUTOMATIC)
1. **NEVER use Unicode in console output** - Windows uses cp1252 encoding
   - Use `[OK]` not `✅`, `[FAIL]` not `❌`, `->` not `→`
2. **ALWAYS follow Selenium cache clearing procedure**:
   - Kill Python processes → Clear disk cache → Restart app → Run test
   - See: `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
3. **ALWAYS verify functionality**, not just compilation

### Code Modification Rules (STRICT)
1. **NEVER modify spymaster.py's standalone architecture** - It's the regression baseline
2. **ALWAYS use the optimized launcher** for performance testing:
   - Location: `local_optimization/batch_files/LAUNCH_OPTIMIZED_V4.bat`
3. **ALWAYS enable ImpactSearch FastPath** unless testing slow path:
   - Set `IMPACT_TRUST_LIBRARY=1`
   - See: `md_library/impactsearch/2025-09-16_IMPACTSEARCH_FASTPATH_OPTIMIZATION_IMPLEMENTATION.md`

### Clean Repository Rules (MAINTAIN AT ALL TIMES)
1. **DELETE temporary files immediately** after use
2. **MOVE misplaced files immediately** when discovered
3. **FOLLOW naming conventions strictly** - no exceptions

## Project Overview

This is a quantitative trading analysis web application built with Python and Dash. It implements an adaptive simple moving average (SMA) pair optimization system for systematic trading analysis and mean reversion strategies.

## Important Principles

### Symbol Validity
- **There is no such thing as a "junk symbol" if it has a 'max' period that we can download**
- Any symbol that returns data from Yahoo Finance is valid and valuable
- Symbols ending in MM (money market), X (mutual funds), or with dots are legitimate
- Do not dismiss symbols as "obscure" or "junk" based on their format
- The system should give all symbols equal opportunity for validation

## Development Environment

**Operating System**: Windows (platform: win32) — Linux/macOS clones should also work; the Windows-specific notes below only apply on win32.
**Recommended Shell**: PowerShell 7+ (`pwsh`). CMD and Git Bash are supported but PowerShell is the canonical contributor shell. Older Git Bash workarounds are preserved as historical notes in `md_library/shared/2025-11-13_CONDA_ACTIVATION_IN_BASH_TOOL_SOLUTION.md` and `md_library/shared/2025-10-22_CLAUDE_TESTING_WINDOWS_PATH_SOLUTION.md`.
**Python Environments**:
  - **spyproject2** (Primary) - Has Intel MKL, NumPy 1.26.4, optimized BLAS
  - **spyproject2_basic** (Alternative) - Generic BLAS, NumPy 2.2.6, no MKL
    - Note: This was formerly named `spyproject2_mkl` (misleading name has been corrected)
**Python environment setup**: create from `project/environment.yml` using Conda, Mamba, or Micromamba (`conda env create -f project/environment.yml`). Activate with `conda activate spyproject2`. Do not assume any particular Conda install location; the activate command works once your shell has Conda initialized. A pip-only path is available via `project/requirements.txt`.

### CRITICAL DATE AWARENESS ISSUE
**IMPORTANT**: The system often shows incorrect dates. When creating MD files with date prefixes:
- **ALWAYS verify the actual current date** using `date` command or checking system clock
- **As of this writing**: The actual date is September 16, 2025 (NOT January or August 2025)
- **Common date confusion**: System may think it's January or August 2025 when it's actually September
- **Before creating any dated file**: Double-check the current date to avoid mislabeled files
- **Existing mislabeled files**: Many MD files are incorrectly dated as "2025-01-*" or "2025-08-*" when they were actually created in September 2025

### Windows shell notes:
- Environment variables (PowerShell): `$env:VAR = "value"; command` (or `;` between statements)
- Environment variables (CMD legacy): `set VAR=value && command`
- File paths: Use backslashes (escape in Python strings) or forward slashes; both resolve under Windows
- Console encoding: cp1252 (avoid Unicode characters in console output; see CLAUDE.md Unicode rule)
- Working directory: `<your local spy-project clone>/project`

## Development Commands

### Environment Setup
```bash
# Create and activate conda environment
conda env create -f environment.yml
conda activate spyproject2
```

### Running Applications

#### Using the Optimized Launcher (Recommended)
```bash
# Navigate to launcher directory
cd local_optimization\batch_files

# Run the optimized launcher with system detection
LAUNCH_OPTIMIZED_V4.bat
```

The launcher provides:
- Automatic CPU core detection (P-cores vs E-cores for Intel 13th gen)
- Performance profiles: Conservative (25%), Balanced (50%), Performance (75%), Maximum (100%)
- MKL threading optimization (MKL_NUM_THREADS, OMP_NUM_THREADS, etc.)
- ImpactSearch FastPath configurations

#### Direct Application Launch
```bash
# Main trading analysis dashboard (default port 8050)
python spymaster.py

# Impact search analysis tool (port 8051)
python impactsearch.py

# Single-pass analysis
python onepass.py

# Global Ticker Library validation
cd global_ticker_library
python run.py --validate-manual
```

#### ImpactSearch FastPath Configuration
Critical environment variables for ImpactSearch optimization:
- `IMPACT_TRUST_LIBRARY=1` - Enable fastpath (reduces API calls from 73,000+ to 1)
- `IMPACT_TRUST_MAX_AGE_HOURS=720` - Production: 30 days cache validity
- `IMPACT_TRUST_MAX_AGE_HOURS=168` - Conservative: 7 days cache validity
- `IMPACT_CALENDAR_GRACE_DAYS=10` - Grace period for calendar adjustments
- `IMPACTSEARCH_ALLOW_LIB_BASIS=1` - Allow library-based calculations

**Note**: See `md_library/impactsearch/2025-09-16_IMPACTSEARCH_FASTPATH_OPTIMIZATION_IMPLEMENTATION.md` for fastpath gate mismatch fix details

### Building Executable
```bash
# Create standalone executable using PyInstaller
pyinstaller spymaster.spec
```

## Architecture

### CRITICAL: Spymaster.py Standalone Design (Regression Testing Baseline)

**IMPORTANT**: Spymaster.py is **intentionally standalone** by design. This is a FEATURE, not a bug!

#### Key Architectural Principles
1. **Complete Independence**
   - NO dependencies on other project modules (signal_library, global_ticker_library, onepass, impactsearch)
   - Direct yfinance calls for all data fetching
   - Isolated caching system in `cache/results/` and `cache/status/`
   - Self-contained calculations for all metrics

2. **Regression Testing Role**
   - Serves as the **gold standard** for metric verification
   - Provides baseline metrics for comparison
   - Ensures new implementations match expected results
   - Acts as the "source of truth" for trading metrics

3. **Why This Matters**
   - **Stability**: Changes to signal_library or other modules don't affect spymaster
   - **Reliability**: Known-good implementation for testing against
   - **Verification**: Can cross-check results from integrated scripts
   - **Independence**: Can run without any other project components

#### Development Rules for Spymaster.py
**DO NOT:**
- Add imports from signal_library to spymaster.py
- Integrate global_ticker_library into spymaster.py
- Create dependencies between spymaster and other scripts
- Share cache files between spymaster and other modules

**DO:**
- Keep spymaster.py completely self-contained
- Use spymaster.py to verify metrics from new scripts
- Maintain spymaster's direct yfinance implementation
- Preserve the isolated caching system

#### Testing Workflow
1. Run analysis in spymaster.py → Get baseline metrics
2. Run same analysis in new/modified script → Get test metrics
3. Compare results → Verify accuracy
4. If discrepancies found → Debug the new script (not spymaster)

### Core Structure
- **spymaster.py** (4,971 lines): Main Dash web application serving the trading analysis dashboard (STANDALONE - Regression Testing Baseline)
- **impactsearch.py**: Statistical relationship analysis between different tickers (Integrated with signal_library)
- **onepass.py**: Single-pass analysis module for quick computations (Integrated with signal_library)

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

## Recent Updates (Session: 2025-01-23)

### Secondary Ticker Signal Following Analysis Fixes

#### Issues Fixed:
1. **Missing Last Day Data**: Secondary analysis was missing the most recent trading day
   - Root cause: yfinance's `end` parameter is exclusive
   - Fix: Added `pd.Timedelta(days=1)` to make end date inclusive (line 2714-2715)

2. **Mismatched Cumulative Captures**: Primary and secondary showed different results for same ticker
   - Root cause: Secondary used 'Close' while primary used 'Adj Close' prices
   - Fix: Modified price column selection to prioritize 'Adj Close' (lines 8511-8535)

#### Testing Results:
All tickers now show perfect matching between primary and secondary analyses:
- SPY: Primary = 192.80%, Secondary = 192.80% ✅
- QQQ: Primary = 213.52%, Secondary = 213.52% ✅
- ^GSPC: Primary = 665.57%, Secondary = 665.57% ✅
- MSFT: Primary = 216.92%, Secondary = 216.92% ✅
- AAPL: Primary = 688.01%, Secondary = 688.01% ✅

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

### Understanding SMA Pair Signal Logic
**CRITICAL**: The same pair (e.g., 114,113) can be used for both buy and short signals with opposite comparison operators:
- **Buy signal for pair (A,B)**: Triggered when SMA_A > SMA_B
- **Short signal for pair (A,B)**: Triggered when SMA_A < SMA_B
- Example: Pair (114,113) on day 0:
  - Buy (114,113): Buy when SMA_114 > SMA_113
  - Short (114,113): Short when SMA_114 < SMA_113
  - These are opposite conditions using the same pair!
- The "top" buy/short pair is the one with the highest cumulative capture for its respective signal type
- Sentinel initialization values:
  - Spymaster uses (MAX_SMA_DAY, MAX_SMA_DAY-1) = (114, 113) for both buy and short on day 0
  - This represents impossible conditions initially (SMA_114 can't be both > and < SMA_113 simultaneously)

## Development Guidelines & Best Practices

### MANDATORY Repository Organization

**YOU MUST automatically enforce these rules WITHOUT being asked:**

#### File Placement (ENFORCE IMMEDIATELY)
- **Root directory = FORBIDDEN for new files** (only modify existing core apps)
- **Test scripts = MUST go in `test_scripts/[appropriate_subfolder]/`**
- **Documentation = MUST go in `md_library/[appropriate_subfolder]/`**
- **Temporary files = DELETE IMMEDIATELY after use**
- **Utilities = MUST go in `utils/[appropriate_subfolder]/`**

#### When Creating ANY File, You MUST:
1. **CHECK**: Is this a test? → `test_scripts/[app_name]/`
2. **CHECK**: Is this documentation? → `md_library/[app_name]/`
3. **CHECK**: Is this temporary? → Create, use, DELETE immediately
4. **CHECK**: Current date with `date` command before naming
5. **NEVER**: Place in root unless modifying existing core files

#### Automatic Cleanup Actions:
- **If you see a test file in root** → Move it immediately
- **If you see an MD file in root** → Move it immediately
- **If you create a temporary file** → Delete it before session ends
- **If you see wrongly dated files** → Note in response and fix if possible

### Documentation Organization & Reference Guide

#### Where to Store Documentation
- **NEVER place new markdown files in the root project folder** (except CLAUDE.md)
- **ALWAYS use date prefix and descriptive uppercase title for markdown filenames**: `YYYY-MM-DD_DESCRIPTION_IN_CAPS.md`
  - Date format: YYYY-MM-DD (ISO 8601) - **VERIFY CURRENT DATE FIRST**
  - Description: Use UPPERCASE with underscores, be specific about the content
  - Include action words like: INVESTIGATION, FIX, ENHANCEMENT, REFACTOR, IMPLEMENTATION, ANALYSIS
  - Good examples:
    - `2025-09-16_UNICODE_AND_SELENIUM_TEST_ISSUE_INVESTIGATIONS.md` (investigation into problems)
    - `2025-09-14_ADAPTIVE_INTERVAL_PERFORMANCE_6X_FASTER.md` (performance improvement)
    - `2025-09-15_CODE_CLEANUP_667_LINES_REMOVED.md` (refactoring work)
  - Avoid vague terms like: FINDINGS, NOTES, CHANGES, UPDATE
- All documentation should be organized in the `md_library/` directory structure:
  - **IMPORTANT: Store MD files directly in their associated directories - NO SUBDIRECTORIES**
  - `md_library/spymaster/` - Spymaster-specific documentation
  - `md_library/impactsearch/` - ImpactSearch documentation
  - `md_library/onepass/` - OnePass documentation
  - `md_library/shared/` - Documentation that affects multiple scripts:
    - Signal library fixes (used by both ImpactSearch and OnePass)
    - Environment/MKL optimization (affects all scripts)
    - NumPy compatibility issues (cross-cutting)
    - Testing procedures (general testing guidelines)
  - `md_library/global_ticker_library/` - GTL documentation
- Text files (.txt) for quick changes/notes can remain in root temporarily but should be cleaned up promptly

#### Documentation Quick Access Map
**USE THE "MANDATORY DOCUMENTATION LOOKUPS" SECTION ABOVE** for detailed guidance on:
- Which documents to read BEFORE starting any task
- Exact file paths for critical documentation
- Search commands to find relevant docs
- Automatic documentation check protocol

**Key principle**: NEVER start a task without checking for existing documentation first

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

### Git Diff Request Guidelines
When handling git diff requests, pay attention to whether the user wants a file or just output:

**CREATE a .txt file when user says:**
- "Create a git diff file"
- "Provide a git diff .txt file"  
- "Generate a git diff and save it"
- "Produce a git diff text file"
- Any request explicitly mentioning ".txt", "file", or "document"

**DO NOT create files when user says:**
- "Run a git diff and provide a summary"
- "Show me the git diff"
- "What are the changes?"
- "Run git diff" (without mentioning a file)

**If creating a file:**
1. **Always create ONE single .txt file** - No multiple attempts or versions
2. **Include full file contents** - Use standard git diff format showing all changes
3. **For untracked files** - Use `git add -N` temporarily to include them in diff, then `git reset HEAD` after
4. **Naming convention** - Use descriptive names like `global_ticker_library_full_diff.txt`

**If just displaying output:**
- Run git diff and show results in terminal/chat
- Provide summary or highlights as requested
- No files should be created

### Testing Guidelines (MANDATORY PROCEDURES)

#### YOU MUST AUTOMATICALLY:
1. **Place ALL test scripts in `test_scripts/` subdirectories** - NO EXCEPTIONS
2. **Use ASCII characters in console output** - NO UNICODE
3. **Clear both cache layers for Selenium** - disk AND session
4. **Verify actual functionality** - compilation is not enough

#### Unicode Handling (AUTOMATIC REPLACEMENT)
**When writing ANY console output, you MUST automatically use**:
  - Windows console uses cp1252 encoding which cannot display Unicode characters
  - This causes `UnicodeEncodeError` when Python tries to print Unicode to the Windows terminal
  - Use ASCII alternatives: [OK], [FAIL], [WARNING] instead of ✅, ❌, ⚠️
  - Use simple separators: ===, ---, ### instead of fancy Unicode boxes
  - Use ASCII arrows: -> instead of → (U+2192)
- **Unicode IS safe to use in:**
  - Dash web interfaces (HTML/browser handles Unicode perfectly)
  - Log files written with UTF-8 encoding
  - Internal Python string processing
  - Web-based outputs (JSON, HTML, etc.)
- **The issue is ONLY with Windows console output (cmd.exe, PowerShell)**

#### Selenium Testing Procedures
- **CRITICAL: Two-Layer Cache System** - See `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
- **Before running Selenium tests, you MUST**:
  1. Kill all running Python processes: `taskkill /F /IM python.exe`
  2. Clear disk cache completely: `rmdir /S /Q cache` and `del *.pkl *.json`
  3. Restart spymaster fresh: `python spymaster.py`
  4. Only then run Selenium test: `python utils\spymaster\selenium_tests\test_spymaster_comprehensive.py`
- **Cache contamination warning**: Spymaster maintains both disk cache and session cache - clearing disk alone is insufficient
- **Test coverage includes all 7 ticker input locations** in spymaster

#### Test Verification Requirements
- All tests should include verification of newly implemented metrics, visuals, functions, or other components
- It is not enough that the app compiles - verify actual functionality
- Use regression testing with spymaster.py as the baseline (it's intentionally standalone)

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

## QUICK REFERENCE - AUTOMATIC ACTIONS

### When Starting ANY Task:
1. **CHECK root directory** - Move any misplaced files immediately
2. **CHECK date** - Run `date` command before creating dated files
3. **CHECK file placement** - Never save to root

### When Creating Files:
- **Test script?** → `test_scripts/[app]/test_*.py`
- **Documentation?** → `md_library/[app]/YYYY-MM-DD_*.md`
- **Temporary?** → Create, use, delete immediately
- **In root?** → STOP, move to correct location

### When Testing:
- **Console output** → Use [OK], [FAIL], [WARNING] - NO Unicode
- **Selenium test** → Kill processes, clear cache, restart app
- **Performance test** → Use LAUNCH_OPTIMIZED_V4.bat

### When Documenting:
- **Script-specific** → `md_library/[script_name]/`
- **Affects multiple** → `md_library/shared/`
- **Date format** → YYYY-MM-DD (verify current date first!)

### Common Locations:
- **Launcher**: `local_optimization/batch_files/LAUNCH_OPTIMIZED_V4.bat`
- **Selenium guide**: `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
- **FastPath docs**: `md_library/impactsearch/2025-09-16_IMPACTSEARCH_FASTPATH_OPTIMIZATION_IMPLEMENTATION.md`

### FORBIDDEN ACTIONS - NEVER DO THESE:
- **NEVER save test files to root** - Always use `test_scripts/`
- **NEVER save MD files to root** - Always use `md_library/`
- **NEVER use Unicode in console** - Always use ASCII
- **NEVER skip cache clearing for Selenium** - Always do full clear
- **NEVER modify spymaster.py's standalone nature** - It's the baseline
- **NEVER trust the system date** - Always verify with `date` command
- **NEVER leave temporary files** - Always clean up immediately
- **NEVER place files randomly** - Always follow structure

## MANDATORY DOCUMENTATION LOOKUPS

### Before ANY Task, You MUST Check These Resources:

#### For Selenium Testing:
**MUST READ FIRST**: `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
- Contains: Two-layer cache system details, nuclear clear procedure, all 7 ticker input locations
- Critical: Session cache vs disk cache distinction
- Element IDs: All 9 test coverage areas documented
- **DO NOT attempt Selenium testing without reading this first**

#### For ImpactSearch FastPath:
**MUST READ FIRST**: `md_library/impactsearch/2025-09-16_IMPACTSEARCH_FASTPATH_OPTIMIZATION_IMPLEMENTATION.md`
- Contains: Gate mismatch fix, module flag propagation solution
- Critical: Environment variable requirements
- Performance: Reduces API calls from 73,000+ to 1
- **DO NOT modify ImpactSearch without understanding fastpath**

#### For Performance/Threading:
**MUST READ FIRST**:
- `md_library/shared/2025-01-15_MKL_THREAD_OPTIMIZATION_BASELINE_TESTS.md`
- `md_library/shared/2025-01-15_COMPLETE_P8_OPTIMIZATION_ALL_SCRIPTS.md`
- Contains: P-core vs E-core detection, MKL threading configurations
- Critical: Intel 13th gen optimization settings

#### For NumPy Compatibility Issues:
**MUST READ FIRST**:
- `md_library/shared/2025-09-16_NUMPY_PICKLE_COMPATIBILITY_SHIMS_IMPLEMENTATION.md`
- `md_library/shared/2025-09-16_ROBUST_NUMPY_SHIMS_VERIFIED_WORKING.md`
- Contains: Cross-version pickle loading fixes
- Critical: numpy.core vs numpy._core aliasing

#### For Unicode/Console Issues:
**MUST READ FIRST**: `md_library/shared/2025-08-16_UNICODE_AND_SELENIUM_TEST_ISSUE_INVESTIGATIONS.md`
- Contains: cp1252 encoding details, ASCII replacement patterns
- Critical: Why Unicode fails in Windows console
- Solutions: Complete ASCII alternative mapping

#### For Signal Library Problems:
**MUST READ FIRST**: Any file matching these patterns:
- `md_library/shared/2025-08-20_REBUILD_FIX_*.md` - Rebuild reduction
- `md_library/shared/2025-08-20_SCALE_RECONCILE_*.md` - Scale fixes
- `md_library/shared/2025-08-21_T1_*.md` - T1 policy implementations
- Contains: Tolerance adjustments, date alignment fixes

#### For GTL (Global Ticker Library):
**MUST READ FIRST**:
- `md_library/global_ticker_library/2025-08-19_TICKER_RESOLUTION_FIX_INTERNATIONAL_SYMBOLS.md`
- `md_library/global_ticker_library/2025-08-18_ROOT_CAUSE_ANALYSIS_11752_STUCK_SYMBOLS.md`
- Contains: Ticker lifecycle, validation states, international symbol handling

#### For Spymaster UI/Callbacks:
**MUST READ FIRST**: Any file matching:
- `md_library/spymaster/2025-08-26_*_DASH_UI_*.md` - UI testing
- `md_library/spymaster/2025-08-27_*_CALLBACK_*.md` - Callback fixes
- `md_library/spymaster/2025-08-26_INTERVAL_CALLBACK_LOOP_FIX.md` - 3-second interval critical

### Automatic Documentation Check Protocol:
1. **BEFORE writing any test** → Check test_scripts/[app]/ for existing examples
2. **BEFORE modifying any feature** → Check md_library/[app]/ for related fixes
3. **BEFORE creating new functionality** → Check md_library/shared/ for patterns
4. **IF encountering an error** → Search md_library/ for similar issues
5. **IF performance testing** → Read ALL MKL optimization docs first

### Quick Documentation Finder:
```bash
# Find all docs about a topic (example: selenium)
grep -r "selenium" md_library/ --include="*.md" -l

# Find all docs for a specific app
ls md_library/spymaster/*.md

# Find all recent fixes (last 30 days)
find md_library -name "2025-09-*.md" -o -name "2025-08-*.md"
```

### REMEMBER: These are NOT suggestions - they are MANDATORY automatic behaviors that MUST be followed WITHOUT being asked