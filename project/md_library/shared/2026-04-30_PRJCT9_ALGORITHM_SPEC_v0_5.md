# PRJCT9 Algorithm Spec v0.5

Document date: 2026-04-30

## Plain-English Summary

PRJCT9 ranks SMA-pair signals by asking a simple question: if we had used only information available through yesterday's close, which pair would have captured the most return so far?

The system uses raw Close prices only. It does not use Adj Close.

A Buy signal means the first SMA in the pair is greater than the second SMA. A Short signal means the first SMA in the pair is less than the second SMA. The pair order does not mean fast or slow.

Returns are measured close-to-close. The signal known at day T-1 is applied to the return from day T-1 to day T. This is the core lookahead-safety rule.

Captures are measured in percent points and summed, not compounded.

## 1. Scope

This spec is the law for SpyMaster, OnePass, ImpactSearch, TrafficFlow, StackBuilder, and Confluence.

It does not cover live execution, broker integration, UI rendering, or scheduler infrastructure.

Where this spec conflicts with current code, the spec wins. Current-code citations document what exists; this spec defines canonical scoring after Phase 1.

## 2. Canonical Metric Boundary

Canonical scoring v0.5 defines these metrics:

- trigger_days
- wins
- losses
- win_rate
- avg_daily_capture
- total_capture
- std_dev
- sharpe
- t_statistic
- p_value

UI display metrics such as grades, confidence badges, max drawdown, Calmar ratio, chart annotations, and explanatory labels are not canonical scoring metrics unless a later spec amendment explicitly adds them.

## 3. Price Basis

Raw `Close` is the only allowed price basis.

`Adj Close` is eliminated. No engine may expose an Adj/raw toggle, env var, or hidden fallback.

Known current-code conflict: `spymaster.py` currently reads `PRICE_BASIS` and defaults to `adj`. Phase 1 removes that path.

## 4. SMA Pair Semantics

For pair `(A, B)`:

- Buy signal: `SMA_A > SMA_B`
- Short signal: `SMA_A < SMA_B`

The same pair may be used for both buy and short with opposite comparison operators.

Do not describe pairs as fast/slow. Direction comes only from the buy/short label and comparison operator.

## 5. SMA Window Range

Minimum SMA window: `1`.

Maximum SMA window: `MAX_SMA_DAY = 114` globally.

This is a computational ceiling, not a research claim. Future amendments may raise it or make it adaptive.

Pairs where `A == B` are rejected.

## 6. Pair Generation

For `MAX_SMA_DAY = M`, generate every ordered pair `(i, j)` where:

- `i in [1, M]`
- `j in [1, M]`
- `i != j`

Total pairs: `M * (M - 1)`.

## 7. Signal Timing

The position for day `T` is determined using SMA values observable through close of day `T-1`.

Day 0 has no position.

Captured return for day `T` is close-to-close return from `T-1` to `T`.

Implied backtest convention: position is established at close of `T-1`.

## 8. Return Computation

`daily_return[T] = (Close[T] - Close[T-1]) / Close[T-1]`

NaN and Inf are coerced to `0`.

Day 0 return is `0`.

## 9. Pair Ranking Metric

For every day `T` and every pair `(i, j)`, compute cumulative capture from day 0 through day `T`.

Top buy pair on day `T` is the pair with highest cumulative buy capture as of `T`.

Top short pair on day `T` is the pair with highest cumulative short capture as of `T`.

Capture is percent-based and non-compounded.

## 10. Pair Ranking Tie-Break Rule

If two pairs have equal cumulative capture within `EPS = 1e-12`, the pair with the higher global pair index wins.

This is deterministic and matches the right-most max behavior in SpyMaster.

## 11. Active Position Selection

Each single-primary strategy has a top-buy pair and top-short pair.

For day `T`, evaluate both signals using day `T-1` SMA values:

- Buy active, short inactive: `Buy`
- Short active, buy inactive: `Short`
- Both active: choose the side with higher cumulative capture as of `T-1`
- Both active and tied: `Short`
- Neither active: `None`

The tied case goes to Short for SpyMaster parity. Do not invent a risk rationale unless a later spec amendment changes the rule.

This produces the `active_pairs` series consumed by downstream cross-ticker logic.

## 12. Trigger Days

A trigger day is any day where active position is `Buy` or `Short`.

`None` days do not count and do not contribute to capture, Sharpe, p-value, or win rate.

## 13. Daily Capture

For trigger days:

- Buy: `daily_capture[T] = daily_return[T] * 100`
- Short: `daily_capture[T] = -daily_return[T] * 100`

For `None` days:

- `daily_capture[T] = 0`

## 14. Total Capture

Total Capture is the sum of trigger-day captures.

It is non-compounded.

The cumulative capture series is a running sum across all trading days, with `None` days contributing `0`.

Total Capture equals the final value of that cumulative capture series.

## 15. Win Rate

`wins = count(trigger days where daily_capture > 0)`

`losses = trigger_days - wins`

`win_rate = wins / trigger_days * 100`

A trigger day with `daily_capture == 0` counts as a loss. This is a deliberate conservative convention.

## 16. Sharpe Ratio

Computed over trigger-day captures only.

All units in this section are percent points, not decimals. `daily_capture`, `avg_daily_capture`, `std_dev`, `annualized_return`, `annualized_std`, and `risk_free_rate` are all in percent-point units.

Definitions:

- `avg_daily_capture = mean(trigger_day_captures)`
- `std_dev = sample standard deviation(trigger_day_captures, ddof=1)`
- `annualized_return = avg_daily_capture * 252`
- `annualized_std = std_dev * sqrt(252)`
- `risk_free_rate = 5.0`

Formula:

`Sharpe = (annualized_return - risk_free_rate) / annualized_std`

If `trigger_days <= 1` or `std_dev == 0`, Sharpe is `0`.

## 17. p-Value

Use a two-sided one-sample parametric t-test against zero mean.

Formula:

`t = avg_daily_capture / (std_dev / sqrt(trigger_days))`

`df = trigger_days - 1`

Numerically stable implementation:

`p_value = 2 * scipy.stats.t.sf(abs(t), df)`

This is mathematically equivalent to:

`2 * (1 - scipy.stats.t.cdf(abs(t), df=df))`

If `trigger_days <= 1` or `std_dev == 0`, `p_value = None`.

Caveat: financial returns have fat tails. Phase 5 validation adds permutation-based cross-checks and reports any material disagreement.

## 18. Multi-Primary Consensus and [D]/[I]

For each primary ticker:

1. Compute its `active_pairs` series independently.
2. Apply direction tag:
   - `[D]`: keep Buy/Short as-is.
   - `[I]`: swap Buy and Short.
3. Apply mute tag if present:
   - muted primary contributes `None`.

Tag discovery, optimizer search, and UI auto-generation are not canonical scoring law.

Per-day consensus:

- All non-None signals agree: consensus is that signal.
- Any non-None signals disagree: consensus is `None`.
- All signals are None: consensus is `None`.

Under consensus scoring, trigger days are days where consensus is `Buy` or `Short`.

Apply consensus to secondary ticker returns:

- Consensus Buy: `daily_capture[T] = daily_return_secondary[T] * 100`
- Consensus Short: `daily_capture[T] = -daily_return_secondary[T] * 100`
- Consensus None: `daily_capture[T] = 0`

All metrics then compute identically to single-primary scoring, using `ddof=1`.

## 19. Confluence Tiers

Deferred to Phase 4 spec amendment.

Phase 4 defines the daily scrub across 1y, 3m, 1m, 1w, and 1d timeframes.

## 20. Calendar Grace Days

Calendar grace days are an operational freshness gate, not an algorithm parameter.

Default: `10` days.

Configurable via `IMPACT_CALENDAR_GRACE_DAYS`.

Tests use synthetic fixtures and bypass freshness gates.

Every output manifest records the grace-days setting.

Grace days must never change computed metrics. If a metric changes because of grace days, that is a bug.

## 21. Open Decisions

- Adaptive or higher `MAX_SMA_DAY`: deferred until after Phase 1.
- Risk-free rate: locked at `5.0`.
- p-value method: parametric in canonical scoring; permutation checks in Phase 5.
- Cross-ticker `[D]` / `[I]`: locked as mathematical signal inversion.
- Confluence tiers: deferred to Phase 4.

## Appendix: Phase 1 Fixes Routed From Spec Drafting

- Remove `PRICE_BASIS`; hardcode raw `Close`.
- Enforce `ddof=1` everywhere.
- Resolve sentinel pair inconsistency: legacy/dead streaming path uses `(1, 2)` / `(2, 1)`; live vectorized/fallback paths use `(MAX_SMA_DAY, MAX_SMA_DAY - 1)` / `(MAX_SMA_DAY - 1, MAX_SMA_DAY)`. Phase 1 standardizes on the MAX-SMA sentinel.
- Remove the dead streaming path around `spymaster.py` lines 4815-4867 and rewrite the call site around lines 4990-4994 to call the vectorized path unconditionally. Verify exact line numbers before editing in Phase 1.
- Unify calendar grace-days defaults to `10`.
