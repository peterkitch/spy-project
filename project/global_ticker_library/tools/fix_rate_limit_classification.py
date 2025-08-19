#!/usr/bin/env python3
"""
Fix rate limit misclassification in validator_yahoo.py.
Based on diagnostic results showing invalid symbols return 404, not 429.
"""
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

def analyze_validator():
    """Analyze current error classification logic."""
    
    print("="*60)
    print("RATE LIMIT MISCLASSIFICATION FIX")
    print("="*60)
    
    print("\nDiagnostic findings:")
    print("1. Invalid symbols return HTTP 404 (Not Found)")
    print("2. Yahoo rarely issues true 429 rate limits")
    print("3. Empty DataFrames mean symbol doesn't exist")
    print("4. 'No data found, symbol may be delisted' = invalid")
    
    print("\n" + "="*60)
    print("RECOMMENDED CHANGES TO validator_yahoo.py")
    print("="*60)
    
    print("""
The classify_error function should be updated to:

1. Check for 404 errors and mark as "not_found" (which leads to invalid)
2. Only mark as "rate_limit" for actual 429 status codes
3. Handle "no data found" messages as invalid, not unknown

Current logic (line 155-173):
```python
def classify_error(exc: Optional[Exception], msg: str = "") -> str:
    name = exc.__class__.__name__ if exc else ""
    text = (msg or "").lower()
    
    if "too many requests" in text or "rate limit" in text or "429" in text:
        return "rate_limit"
    if "YFRateLimitError" in name:
        return "rate_limit"
    # ... rest of function
```

RECOMMENDED update:
```python
def classify_error(exc: Optional[Exception], msg: str = "") -> str:
    name = exc.__class__.__name__ if exc else ""
    text = (msg or "").lower()
    
    # Check for 404 errors FIRST (invalid symbols)
    if "404" in text or "http error 404" in text:
        return "not_found"
    
    # Only mark as rate_limit for actual 429 codes
    if "429" in text and "404" not in text:  # Ensure it's really 429, not 404
        return "rate_limit"
    if "too many requests" in text:
        return "rate_limit"
    if "YFRateLimitError" in name:
        return "rate_limit"
        
    # Handle timeout errors
    if "timed out" in text or "timeout" in text or "curl: (28)" in text:
        return "timeout"
        
    # Handle clear delisting messages
    if "symbol may be delisted" in text or "no data found, symbol may be delisted" in text:
        return "not_found"
    if "no data found" in text or "possibly delisted" in text or "no price data" in text:
        return "no_price_data"
        
    # Network issues
    if "connection" in text or "network" in text:
        return "timeout"
        
    return "other"
```

This will ensure:
- 404 errors -> marked as "not_found" -> eventually marked as invalid
- Only real 429 errors -> marked as "rate_limit" -> retried
- Clear invalid symbols are identified immediately
    """)
    
    print("\n" + "="*60)
    print("DATABASE CLEANUP NEEDED")
    print("="*60)
    
    print("""
After fixing the classifier, run this SQL to clean up misclassified symbols:

UPDATE tickers
SET status = 'invalid',
    last_error_code = 'not_found',
    last_error_msg = 'Reclassified from rate_limit - actually 404'
WHERE status = 'unknown'
AND last_error_code = 'rate_limit';

This will immediately mark the 289 misclassified symbols as invalid.
    """)

if __name__ == "__main__":
    analyze_validator()
    print("\nDone!")