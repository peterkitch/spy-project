# 2025-09-26 STACKBUILDER SELENIUM TEST DOCUMENTATION

## Overview

Comprehensive Selenium test suite for the StackBuilder application, following the guidelines established in `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`.

## Test Location

```
test_scripts/stackbuilder/test_stackbuilder_selenium.py
```

## Prerequisites

1. **StackBuilder must be running**
   ```bash
   python stackbuilder.py
   ```
   The app should be accessible at http://localhost:8054

2. **Chrome browser and ChromeDriver**
   - Chrome browser must be installed
   - ChromeDriver must be in PATH or same directory

3. **Clean environment**
   - The test includes nuclear cache clearing for stackbuilder output

## Test Coverage

### Test 1: UI Elements Verification
- Verifies all UI components are present:
  - Secondary ticker input
  - Primary tickers textarea
  - Parameter inputs (Top N, Bottom N, Max K, Alpha)
  - ImpactSearch checkbox
  - Run button
  - Results table

### Test 2: Simple Stack Build
- Tests basic functionality with SPY as secondary
- Multiple primary tickers (AAPL, MSFT, GOOGL, NVDA, META, AMZN)
- Unchecks ImpactSearch to force computation
- Verifies successful completion and table population

### Test 3: Parameter Adjustment
- Tests with QQQ as secondary
- Adjusted parameters:
  - Top N: 10
  - Bottom N: 10
  - Max K: 3
  - Alpha: 0.10
- Verifies parameter changes are respected

### Test 4: Validation Checks
- Tests error handling:
  - Missing secondary ticker
  - Empty primary tickers
- Verifies appropriate error messages

### Test 5: Index Tickers
- Tests special characters in tickers:
  - ^VIX as secondary
  - ^GSPC in primaries
- Verifies proper handling of special characters in filesystem

## Running the Test

### Basic Execution
```bash
python test_scripts/stackbuilder/test_stackbuilder_selenium.py
```

### With Full Cache Clear (Recommended)
```bash
# Step 1: Kill any running Python processes
taskkill /F /IM python.exe

# Step 2: Clear stackbuilder output
rmdir /S /Q output\stackbuilder

# Step 3: Start stackbuilder fresh
python stackbuilder.py

# Step 4: In a new terminal, run the test
python test_scripts/stackbuilder/test_stackbuilder_selenium.py
```

## Cache Management

The test includes a `nuclear_cache_clear()` method that:
1. Removes entire `output/stackbuilder` directory
2. Cleans up any temp directories
3. Creates fresh output structure
4. Verifies complete cleanup

## Expected Results

### Success Criteria
- All UI elements load correctly
- Stack builds complete without errors
- Results populate in the table
- Index tickers are handled properly
- Validation messages appear appropriately

### Sample Output
```
[OK] StackBuilder loaded successfully
[OK] Secondary ticker input found
[OK] Primary tickers textarea found
[OK] Stack build completed: Done → output/stackbuilder\SPY\...
[OK] Results table populated with 3 rows
```

## Common Issues and Solutions

### Issue: Test fails to find elements
**Solution**: Ensure stackbuilder is running and accessible at http://localhost:8054

### Issue: Stack build takes too long
**Solution**: The test allows up to 60 seconds for processing. For large ticker sets, this may need adjustment.

### Issue: Cache not clearing properly
**Solution**: Manually clear the output directory and restart stackbuilder

## Element IDs Reference

```python
# Input fields
"secondary-input"       # Secondary ticker input
"primaries-input"       # Primary tickers textarea
"topn"                  # Top N parameter
"bottomn"               # Bottom N parameter
"maxk"                  # Max K parameter
"alpha"                 # Alpha parameter

# Controls
"prefer-xlsx"           # ImpactSearch checkbox wrapper
"xlsx-dir"              # ImpactSearch directory input
"run-btn"               # Run button

# Output
"run-status"            # Status message div
"tbl"                   # Results table
```

## Integration with CI/CD

The test returns:
- Exit code 0: All tests passed
- Exit code 1: One or more tests failed

This allows integration with automated testing pipelines.

## Maintenance Notes

1. **Timeout Adjustments**: Processing times may vary based on:
   - Number of tickers
   - Whether ImpactSearch Excel is used
   - System performance

2. **Element Locators**: Uses ID-based locators for reliability

3. **Wait Strategies**: Combines explicit waits with polling for dynamic content

## Related Documentation

- Main Selenium guide: `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
- StackBuilder implementation: `stackbuilder.py`
- Test script: `test_scripts/stackbuilder/test_stackbuilder_selenium.py`