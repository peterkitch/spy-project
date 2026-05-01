# 2025-01-23 SELENIUM TESTING COMPREHENSIVE GUIDE

## Executive Summary

This document provides comprehensive documentation for the Spymaster Selenium test suite, detailing critical discoveries about cache management, session persistence, and testing intricacies that were uncovered during extensive testing sessions.

## Critical Discovery: Two-Layer Cache System

### The Cache Contamination Problem

During testing, we discovered that Spymaster maintains **TWO separate cache layers**:

1. **Disk Cache** (`cache/` directory with `.pkl` and `.json` files)
2. **Session Cache** (In-memory cache maintained by the running Dash application)

**CRITICAL FINDING**: Clearing the disk cache alone is NOT sufficient. The session cache persists in memory and will auto-populate fields with previously processed tickers, leading to inconsistent test results.

### The Solution: Full Restart Required

```bash
# Step 1: Kill all running spymaster processes
taskkill /F /IM python.exe

# Step 2: Clear disk cache completely
rmdir /S /Q cache
del *.pkl *.json

# Step 3: Restart spymaster fresh
python spymaster.py

# Step 4: Run selenium test
python utils\spymaster\selenium_tests\test_spymaster_comprehensive.py
```

## Test Architecture

### Test Coverage: All 7 Ticker Input Locations

The comprehensive test covers all ticker input locations in Spymaster:

1. **Primary Ticker Analysis** (`ticker-input`)
   - Main ticker input with chart generation
   - Tests SPY as default ticker

2. **Manual SMA Inputs** (Tests 2-4)
   - Located in `primary-ticker-collapse` section
   - Verifies auto-population of SMA values

3. **Secondary Ticker Signal Following** (Tests 5-6)
   - `secondary-ticker-input` field
   - Tests multiple tickers: SPY,QQQ

4. **Batch Ticker Processing** (Test 7)
   - `batch-ticker-input` field
   - `batch-process-button` must be clicked
   - Uses small tickers: VIK, MAMO, SMTK

5. **Automated Signal Optimization** (Test 8)
   - `optimization-secondary-ticker`: SPY
   - `optimization-primary-tickers`: XLK,XLF,XLE
   - `optimize-signals-button` must be clicked

6. **Multi-Primary Signal Aggregator** (Test 9)
   - `multi-secondary-ticker-input`: DIA,IWM
   - Dynamic primary inputs: NVDA, TSLA
   - **CRITICAL**: Must fill BOTH fields before pressing Enter

### Element IDs Reference

```python
# Primary sections
"ticker-input"                    # Primary ticker
"primary-ticker-collapse"         # Manual SMA section

# Secondary analysis
"secondary-ticker-input"          # Secondary ticker

# Batch processing
"batch-ticker-input"              # Batch tickers
"batch-process-button"            # Process batch button

# Optimization
"optimization-secondary-ticker"   # Optimization secondary
"optimization-primary-tickers"    # Optimization primary list
"optimize-signals-button"         # Optimize button

# Multi-primary
"multi-secondary-ticker-input"    # Multi-secondary
"//input[contains(@id, 'primary-ticker-input')]"  # Dynamic primaries
```

## Cache Clearing Strategy

### Nuclear Cache Clear Method

The test implements a "nuclear" cache clearing strategy:

```python
def nuclear_cache_clear(self):
    # 1. Remove entire cache directory
    if os.path.exists("cache"):
        shutil.rmtree("cache")
    
    # 2. Remove ALL .pkl and .json files from root
    pkl_files = glob.glob("*.pkl")
    json_files = glob.glob("*.json")
    for f in pkl_files + json_files:
        if f != "package.json":  # Preserve package.json
            os.remove(f)
    
    # 3. Remove ticker-specific files
    ticker_patterns = ["*SPY*", "*QQQ*", "*NVDA*", ...]
    for pattern in ticker_patterns:
        files = glob.glob(pattern + ".pkl") + glob.glob(pattern + ".json")
        for f in files:
            os.remove(f)
    
    # 4. Create fresh directories
    os.makedirs("cache/results", exist_ok=True)
    os.makedirs("cache/status", exist_ok=True)
    
    # 5. Verify complete clearing
    # Check for any remaining cache files
```

## Button Clicking Challenges

### Multi-Method Click Strategy

Many buttons in Dash applications intercept normal clicks. The test uses multiple methods:

```python
def force_click_button(self, button, name):
    try:
        # Method 1: Regular click
        button.click()
    except:
        try:
            # Method 2: JavaScript click
            self.driver.execute_script("arguments[0].click();", button)
        except:
            try:
                # Method 3: Action chains
                ActionChains(self.driver).move_to_element(button).click().perform()
            except:
                # Method 4: Send Enter key
                button.send_keys(Keys.ENTER)
```

### Visual Feedback

Buttons are highlighted before clicking for visual confirmation:

```python
self.driver.execute_script("""
    arguments[0].style.border = '5px solid red';
    arguments[0].style.backgroundColor = 'yellow';
""", button)
```

## WebGL and Chart Rendering Issues

### WebGL Support Detection

```python
webgl = self.driver.execute_script("""
    var canvas = document.createElement('canvas');
    var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    return gl ? true : false;
""")
```

### Chart Loading Verification

Charts may take significant time to render. The test includes:
- 50-second wait for SPY primary chart
- Progressive checking for chart containers
- Height verification to ensure charts are visible

## Test Execution Best Practices

### Pre-Test Checklist

1. **Close all Chrome instances** - Prevents WebDriver conflicts
2. **Kill all Python processes** - Ensures clean slate
3. **Delete test files from previous runs** - Clean project directory
4. **Verify spymaster is NOT running** - Prevents session cache issues

### Running the Test

```bash
# From project root directory
cd <your local spy-project clone>\project

# Run comprehensive test
python utils\spymaster\selenium_tests\test_spymaster_comprehensive.py
```

### Expected Output

```
[START] Spymaster Comprehensive Test
[SETUP] Starting Chrome...
[OK] Chrome started

[CACHE] NUCLEAR CACHE CLEARING...
============================================================
[ACTION] Removing entire cache directory...
[OK] Cache directory removed
[SUCCESS] Cache is COMPLETELY EMPTY!
============================================================

TEST 1: PRIMARY TICKER (SPY)
[OK] Field is empty - cache cleared properly
[OK] Entered SPY and pressed Enter
[WAIT] 50s for SPY processing and chart...
[OK] Found 1 Plotly chart containers
  Chart 1: Displayed=True, Height=450px

[... continues for all 9 tests ...]

############################################################
# TEST SUMMARY
############################################################
[PASS] Primary Ticker: DONE
[PASS] Manual SMA: CHECKED
[PASS] Secondary Ticker: DONE
[PASS] Batch Processing: DONE
[PASS] Optimization: DONE
[PASS] Multi-Primary: DONE
############################################################
```

## Common Issues and Solutions

### Issue 1: Cache Contamination
**Symptom**: Fields auto-populate with previous values
**Solution**: Restart spymaster.py completely

### Issue 2: WebGL Not Supported
**Symptom**: Charts show "WebGL is not supported by your browser"
**Solution**: Chrome flags or use SVG fallback

### Issue 3: Buttons Not Clicking
**Symptom**: Button highlighted but action doesn't trigger
**Solution**: Use JavaScript click method

### Issue 4: Multi-Primary Not Processing
**Symptom**: Multi-primary section doesn't process tickers
**Solution**: Fill BOTH fields before pressing Enter

### Issue 5: Charts Not Loading
**Symptom**: White background with grid but no data
**Solution**: Wait longer (up to 50 seconds) or check for JavaScript errors

## File Organization

```
project/
├── utils/
│   └── spymaster/
│       └── selenium_tests/
│           └── test_spymaster_comprehensive.py  # Main test file
├── md_library/
│   └── spymaster/
│       └── testing/
│           └── 2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md
```

## Key Discoveries Summary

1. **Session cache persists** even after disk cache clearing
2. **Spymaster restart required** for true clean slate
3. **Small tickers (VIK, MAMO, SMTK)** process faster than large-cap
4. **Multi-primary requires specific sequence**: Fill both fields, then Enter
5. **Chart loading can take 50+ seconds** for initial ticker
6. **Button clicks often need JavaScript** execution
7. **WebGL issues can cause chart failures** - SVG fallback available

## Future Improvements

1. Add automated spymaster restart to test setup
2. Implement screenshot capture on failures
3. Add performance timing metrics
4. Create parameterized tests for different ticker sets
5. Add parallel test execution capability
6. Implement retry logic for flaky elements

## Conclusion

The Selenium test suite provides comprehensive coverage of Spymaster's functionality. The critical discovery of the two-layer cache system and the requirement for full application restart ensures consistent and reliable test results. This documentation serves as a reference for maintaining and extending the test suite.