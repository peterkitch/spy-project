# SHORT Signal Sharpe Approximation - Mathematical Derivation

**Goal**: Approximate "buy-instead" Sharpe from "short" Sharpe

---

## The Problem

**Given**: SHORT strategy with Sharpe = 3.44
- This means: `Sharpe_short = (avg_short_return - rf) / std_short_return = 3.44`

**Want**: Approximate Sharpe if we had BOUGHT instead
- `Sharpe_buy = (avg_buy_return - rf) / std_buy_return = ?`

---

## Key Insight

When we SHORT:
- Captures = `-returns` on short days
- If returns have mean μ, short captures have mean -μ
- Standard deviation stays same: `std(-X) = std(X)`

**Approximation**:
```
Sharpe_short = (avg_short_cap - rf) / std_short_cap = 3.44

For opposite (buy when we shorted):
avg_buy_cap ≈ -avg_short_cap
std_buy_cap ≈ std_short_cap  (same volatility)

Therefore:
Sharpe_buy ≈ (-avg_short_cap - rf) / std_short_cap
           ≈ -(avg_short_cap + rf) / std_short_cap
           ≈ -Sharpe_short - (2*rf / std_short_cap)
```

**But RF term is tiny** (0.04 annual = 0.00016 daily), so:

**Simple Approximation**: `Sharpe_buy ≈ -Sharpe_short`

For SBIT: `Sharpe_buy ≈ -3.44`

---

## More Accurate Formula (if we have std)

If we have the standard deviation:

```python
# Given from SHORT strategy:
sharpe_short = 3.44
std_short = 6.0071  # We have this!
rf_annual = 0.04
rf_daily = rf_annual / 252 = 0.000159

# Derive avg_short_cap:
# sharpe_short = (avg_short_cap - rf_daily) / std_short
# avg_short_cap = sharpe_short * std_short + rf_daily
avg_short_cap = 3.44 * 6.0071 + 0.000159 = 20.66

# For buy-instead:
avg_buy_cap = -avg_short_cap = -20.66
std_buy_cap = std_short = 6.0071  # Same volatility

# Calculate buy Sharpe:
sharpe_buy = (avg_buy_cap - rf_daily) / std_buy_cap
           = (-20.66 - 0.000159) / 6.0071
           = -20.66 / 6.0071
           = -3.44  # Almost exactly negative!
```

**Conclusion**: Simple negation IS accurate for practical purposes!

