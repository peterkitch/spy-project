# K=6 MTF Launch Path Contract

**Date:** 2026-05-27

**Status:** Authoritative for the K=6 MTF MVP launch path. Locks the math, the data sources, the artifact schemas, the freshness policy, and the deferred items so future implementation prompts have a single reference. Does NOT initiate implementation; the implementation chain is sketched at the end of this document and each step is a separate future PR.

**Anchor documents:**

- `md_library/shared/2026-05-25_CONFLUENCE_TERMINOLOGY_GLOSSARY.md` (PR #323) - Concept 4.
- `md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md` (PR #325 + PR #330 + PR #335 Identity Correction).
- `md_library/shared/2026-05-26_MVP_V1_HISTORY_ARTIFACT_CONTRACT.md` (PR #331 + PR #335 Identity Correction).
- `md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md` (PR #329).
- `md_library/shared/2026-05-25_KNOWN_BUGS_LOG.md` (PR #324; this PR appends one entry).

---

## Identity and Scope

This document is the launch-path contract for K=6 MTF. K=6 MTF ranks the 8 MVP secondaries (**AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA**). Each secondary is analyzed against its StackBuilder-selected K=6 stack across the five canonical timeframes (`1d`, `1wk`, `1mo`, `3mo`, `1y`). The output ranking uses honest Sharpe over those historical bars whose K=6 MTF snapshot matched the current K=6 MTF snapshot under the match rule defined below.

This is the launch-path interpretation of Concept 4 in `md_library/shared/2026-05-25_CONFLUENCE_TERMINOLOGY_GLOSSARY.md` (L50-L56: "Stack-aware multi-window K-build confluence"; also L65 summary-table "On the launch path? Yes" and L87-L113 mapping the Concept 4 launch-path script chain; and L128 "The launch path is Concept 4"). The K=6 MTF launch path operationalizes that concept into a self-contained MVP pipeline.

This is **NOT** OnePass Multi-Timeframe. OnePass-MTF analyzes the secondary's own per-timeframe signals (per the Identity Correction in `md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md` L18-L40 and `md_library/shared/2026-05-26_MVP_V1_HISTORY_ARTIFACT_CONTRACT.md` L18-L36). K=6 MTF analyzes the K=6 stack's combined per-timeframe signals. The two surfaces are distinct and may coexist; this contract does not retire OnePass-MTF.

Universe expansion beyond the 8 MVP secondaries is **future work**, deferred to the large-universe planning cluster (`confluence_large_universe_launch_planner.py` / `confluence_large_universe_rollout_batch_planner.py`).

---

## Definitions

### Domain terms

- **Secondary.** One of the 8 MVP target tickers (AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA). The price series being analyzed and ranked.
- **K=6 stack.** The six primary tickers (and their `[D]/[I]` protocol assignments) named in the K=6 row of the secondary's `selected_run_dir/combo_k=6.json`.
- **Member.** One of the six tickers in a K=6 stack.
- **Protocol assignment `[D]` and `[I]`.** Per-member modifier. `[D]` (Direct) preserves the member's raw signal. `[I]` (Inverse) swaps `BUY` and `SHORT`. Encoded in `combo_k=6.json` as part of the members list. The same convention is used today by the multi-window K engine core (`multiwindow_k_engine_core.py:273` `_apply_protocol`) and is consistent with TrafficFlow's `_signals_from_pkl_for_mode` at `trafficflow.py:1360`.
- **Member raw signal.** A `BUY` / `SHORT` / `NONE` value for one member on one timeframe bar before protocol is applied.
- **Protocol-adjusted signal.** The member raw signal after `[D]` / `[I]` is applied.
- **K=6 combined signal.** The TrafficFlow-style active-signal unanimity combine of the six protocol-adjusted signals for one timeframe bar. "K=6" identifies the six-member StackBuilder build being evaluated; it does NOT impose a six-active-vote threshold on every bar. Neutral member values (`NONE` / `Cash` / `UNAVAILABLE` / `missing` / blank / null / unrecognized) abstain and do not count as dissent. Active values (`BUY` / `SHORT`) determine the combined slot. See "K=6 Combined Signal Per Timeframe" below.
- **Timeframe slot.** One of `1d`, `1wk`, `1mo`, `3mo`, `1y` carrying the K=6 combined signal at a given date.
- **MTF snapshot.** The 5-tuple of K=6 combined signals across the five timeframe slots on one date.
- **Current snapshot.** The MTF snapshot on `history_as_of_date` (see "Current Snapshot and As-Of Date" below).
- **Historical snapshot.** The MTF snapshot on any historical date strictly before `history_as_of_date`.
- **Match bar.** A historical bar whose snapshot passes the match rule against the current snapshot.
- **Trade bar.** A match bar whose own 1d slot is `BUY` or `SHORT`.
- **No-trade bar.** A match bar whose own 1d slot is `NONE` or `UNAVAILABLE`. Contributes zero capture but counts toward CCC continuity.
- **CCC.** Cumulative Combined Capture; the running sum of per-bar capture across match bars in chronological order.
- **Honest Sharpe.** `(avg_capture_pct / stddev_pct) * sqrt(252)`, with sample stddev `ddof=1`, computed directly over match-bar captures. NOT TrafficFlow's subset-averaged UI Sharpe.
- **OnePass-MTF.** The pre-existing per-secondary five-timeframe analysis surface (`trafficflow_v1_history_writer.py`, `mvp_ranking_v1.py`, `mvp_signal_board.py` v1 dispatch arm) that reads each secondary's own signal libraries. Identity-corrected in PR #335.

### Signal vocabulary

- `BUY`
- `SHORT`
- `NONE`
- `UNAVAILABLE`

`NONE` indicates a known no-signal value at a covered date (combine produced none / member is genuinely flat). `UNAVAILABLE` indicates the slot could not be evaluated for that date (missing library / missing member / pre-coverage date). Both are **preserved in the emitted artifact for auditability**; both are treated as **wildcards** at match time (see the Match Rule). Producers must not collapse `UNAVAILABLE` into `NONE` upstream; the audit trail depends on the distinction.

---

## Locked Design

### Raw Price Source

Daily close history is the single raw price source for all five canonical timeframes. The launch path does **not** require Yahoo-native weekly, monthly, quarterly, or yearly downloads.

#### For K=6 members

- Primary source: `cache/results/<MEMBER>_precomputed_results.pkl`.
- Daily close path inside the object: `obj["preprocessed_data"]["Close"]` (pandas `Series`, `float64`, indexed by `DatetimeIndex`).
- TrafficFlow loads this PKL via `load_spymaster_pkl(ticker)` at `trafficflow.py:1442` and uses the same `preprocessed_data` access pattern in `_processed_signals_from_pkl(primary)` at `trafficflow.py:1562`. The K=6 MTF launch path uses the same PKL and the same accessor; it does not invent a parallel reader.

#### For secondaries

In order:

1. `price_cache/daily/<SEC>.parquet`.
2. `price_cache/daily/<SEC>.csv`.
3. `cache/results/<SEC>_precomputed_results.pkl` (launch-path-specific fallback) when neither price-cache file exists.

TrafficFlow chooses `.parquet` over `.csv` at `trafficflow.py:1065` (`_choose_price_cache_path(symbol)`) and loads the resulting file at `trafficflow.py:1117` (`_load_secondary_prices`). StackBuilder uses the same convention: `stackbuilder.py:227` defines `DEFAULT_PRICE_CACHE_DIR = os.environ.get('PRICE_CACHE_DIR', 'price_cache/daily')` and `stackbuilder.py:536` defines `load_secondary_prices(secondary)`.

The final fallback to `cache/results/<SEC>_precomputed_results.pkl` is **launch-path-specific**. TrafficFlow's existing secondary path normally refreshes from the provider if the price cache is missing; this contract does NOT invoke a provider fetch in the K=6 MTF producer. The PKL fallback exists because 2 of the 8 MVP secondaries (AAPL, SPY) carry that PKL today and 6 (AMZN, GOOGL, META, MSFT, NVDA, TSLA) rely on `price_cache/daily/`. The producer must accept either source.

### Per-Timeframe Signal Generation

Locked pipeline for every K=6 member and every timeframe:

1. Start with the member's daily close history (from `cache/results/<MEMBER>_precomputed_results.pkl["preprocessed_data"]["Close"]`).
2. For `1d`, use the daily close series as-is.
3. For `1wk`, `1mo`, `3mo`, and `1y`, locally resample daily closes to the timeframe bar series.
4. Compute SMA crossover signals on the timeframe-specific bar series.
5. Use the existing `MAX_SMA_DAY = 114` ordered-pair search.
6. Persist per-timeframe signal libraries under `signal_library/data/stable/`.

Cited current lines in `signal_library/multi_timeframe_builder.py`:

- `MAX_SMA_DAY = 114` at L62.
- `SIGNAL_LIBRARY_DIR = os.environ.get('SIGNAL_LIBRARY_DIR', 'signal_library/data/stable')` at L63.
- `fetch_interval_data(ticker, interval)` at L100 - the existing daily/non-daily branch (resample-from-daily already used for `3mo` and `1y` per current code; this contract extends the same convention to `1wk` and `1mo`).
- `apply_t1_skip(df, interval)` at L196.
- `generate_signals_for_interval(...)` at L279.
- `find_optimal_pairs(df, interval)` at L421 (and `find_optimal_pairs_vectorized` at L562) - the MAX_SMA_DAY ordered-pair search.
- `generate_signal_series_dynamic(df, ...)` at L708.
- `save_signal_library(library, interval, force_overwrite)` at L843 - persists `<TICKER>_stable_v1_0_0.pkl` for daily and `<TICKER>_stable_v1_0_0_<INTERVAL>.pkl` for non-daily.

This is **resample-prices-then-compute-signals**. It is NOT compute-daily-signals-then-project-signals. `trafficflow_multitimeframe_bridge.py`'s projection approach (`project_signal_to_timeframes` using `resample(<freq>).last() + ffill`) is **NOT** the launch-path producer. Sampling a daily signal at month-end or year-end is not the same as computing SMA crossover signals on month-end or year-end bars, because SMA crossover output depends on the bar series used to compute the moving averages.

The locked launch-path rule is daily-resampled for consistency across timeframes. Future PRs do not need to consider Yahoo-native weekly or monthly intervals as equivalent.

### K=6 Stack Selection

For each secondary:

1. Read `output/stackbuilder/<SEC>/selected_build.json`.
2. Use `selected_run_dir` as the selected StackBuilder run directory.
3. Read `combo_k=6.json` inside `selected_run_dir`.
4. Parse exactly six members and their `[D]/[I]` protocol assignments.
5. The launch path uses K=6 even if `selected_build.json`'s `selected_k` field points to another K.

If `combo_k=6.json` is missing, malformed, or does not contain exactly six members, the secondary fails closed per "Fail-Closed Behavior" below.

Prefer `combo_k=6.json` as the launch-path source. Do NOT require parsing `combo_leaderboard.xlsx` for MVP unless a future implementation PR explicitly justifies it. (The audit-time leaderboard inspection used in earlier diagnostics is acceptable as a sanity-check, not as the runtime source.)

### Protocol Application

For each member signal:

- `[D]` preserves `BUY` and `SHORT`.
- `[I]` inverts `BUY` and `SHORT`.
- `NONE` remains `NONE`.
- `UNAVAILABLE` remains `UNAVAILABLE`.

Reference implementation precedent: `multiwindow_k_engine_core.py:273` `_apply_protocol(raw_signal, protocol)` already implements this rule and is publicly visible.

If any member signal is missing at a (timeframe, date) after alignment, treat that member as `UNAVAILABLE` for that slot. A stricter fail-closed rule (e.g., refuse the date entirely) is allowed at the producer level only if a future implementation contract defines it. The default rule for this MVP is `UNAVAILABLE`.

### K=6 Combined Signal Per Timeframe

The launch-path combine is **TrafficFlow-style active-signal unanimity**: neutral member values abstain, active members must not conflict, and one or more aligned active members can carry the combined slot.

For each secondary, timeframe, and aligned date:

1. Apply `[D]/[I]` to all six member signals first.
2. Normalize each protocol-adjusted member signal into one of:
   - **Active `BUY`** or **Active `SHORT`** (a directional vote);
   - **Neutral**: `NONE`, `Cash`, `UNAVAILABLE`, `missing`, blank, `null`, or any unrecognized value.

   Active values are `BUY` and `SHORT` only. Everything else is neutral. Neutral members abstain; they do NOT block a signal and they do NOT count as dissent.

3. Count the six members into three buckets:
   - `active_buy_count` = number of active `BUY` votes (0..6).
   - `active_short_count` = number of active `SHORT` votes (0..6).
   - `neutral_count` = number of neutral members (0..6).

   `active_buy_count + active_short_count + neutral_count == 6` for every K=6 slot at every bar.

4. The combined signal is:
   - `BUY` if `active_buy_count > 0` and `active_short_count == 0` (one or more aligned `BUY` votes, no `SHORT` dissent; remaining members are neutral).
   - `SHORT` if `active_short_count > 0` and `active_buy_count == 0`.
   - `NONE` if `active_buy_count > 0` and `active_short_count > 0` (active members conflict).
   - `NONE` if `active_buy_count == 0` and `active_short_count == 0` (no active members).

A single active `BUY` with five neutral members returns `BUY`. A single active `SHORT` with five neutral members returns `SHORT`. A mixture of `BUY` and neutral yields `BUY`; a mixture of `SHORT` and neutral yields `SHORT`. Conflict still cancels: any active `BUY` plus any active `SHORT` returns `NONE`.

#### Matching implementation citation

This is the same combine semantics as `trafficflow.py:1963` `_combine_positions_unanimity(pos_df)`:

- TrafficFlow maps `Buy` to `+1`, `Short` to `-1`, and `None` / `Cash` to `0` (verified at `trafficflow.py:1987`).
- It counts only non-zero (active) signals via `c = (m != 0).sum(axis=1)` (verified at `trafficflow.py:1991`).
- It emits `Buy` when the active signals are all positive (`np.where(s == c, 'Buy', ...)`, verified at `trafficflow.py:1998`).
- It emits `Short` when the active signals are all negative (`np.where(s == -c, 'Short', ...)`, verified at `trafficflow.py:1999`).
- It emits the neutral value (`None` / `Cash`) when active signals conflict or when there are no active signals (the inner branch at `trafficflow.py:1997` plus the final fallback at `trafficflow.py:1999`).

The K=6 MTF launch-path combine extends those semantics over six protocol-adjusted member signals, applied independently per timeframe stream.

#### Rationale to preserve

1. **Signal availability.** Strict all-six agreement can produce too few active combined signals across the multi-timeframe resampled bars; the lenient rule keeps the signal series populated enough for the match rule to find historical matches.
2. **`NONE` is absence of a directional view, not dissent.** Neutral members should abstain so they don't silently veto the combined signal.
3. **Conflict still cancels.** Active `BUY` plus active `SHORT` returns `NONE`. The combined signal is never "majority wins."

#### Contrast with the strict K-threshold mechanism

The launch path does NOT use `research_artifacts.combine_member_signals(member_signals, K=6)` thresholding for the combined signal. The K-threshold combine is a **different mechanism** with stricter semantics:

- `research_artifacts.py:620` `combine_member_signals(...)` requires at least `K` agreeing active members to fire. With `K=6` against a 6-member stack, that effectively requires **all six active and aligned**; a single neutral member breaks the agreement.
- `multiwindow_k_engine_core.py:422` calls `_ra.combine_member_signals(active, K=K)` inside `evaluate_cell`. That code path is a different mechanism (K-thresholded strict unanimity over the active set) and is **NOT adopted** for the K=6 MTF launch-path combined signal.

Future implementation may reuse parsing or adapter helpers from the multiwindow cluster (e.g., `multiwindow_k_input_adapter.prepare_multiwindow_k_inputs` for library resolution; `multiwindow_k_engine_core._apply_protocol` for `[D]/[I]` application). It must NOT import the K-threshold combine rule as the launch-path combine.

### Snapshot Alignment

The history artifact uses the secondary's daily trading calendar.

At each secondary daily bar:

- The `1d` slot uses the K=6 combined `1d` signal for that daily date.
- The `1wk` slot uses the most recent K=6 combined `1wk` signal whose timeframe bar date is `<=` the secondary daily date.
- The `1mo` slot uses the most recent K=6 combined `1mo` signal whose timeframe bar date is `<=` the secondary daily date.
- The `3mo` slot uses the most recent K=6 combined `3mo` signal whose timeframe bar date is `<=` the secondary daily date.
- The `1y` slot uses the most recent K=6 combined `1y` signal whose timeframe bar date is `<=` the secondary daily date.

This is the **locked forward-fill rule**: each non-daily slot carries the previous-closed-period combined signal until a new bar closes.

This mirrors the OnePass-MTF forward-fill precedent in `trafficflow_v1_history_writer.py` (`_forward_fill_signal_at(...)` at L253; the rule "Otherwise: forward-fill (latest library bar at or before the date)" at L268; the per-timeframe walk at L441-L442 over `TIMEFRAMES_COVERED` defined at L51). The K=6 MTF producer reuses the same forward-fill semantics but operates on the K=6 combined signal stream per timeframe, not on the secondary's own signal stream.

### Current Snapshot and As-Of Date

Locked strictly to prevent mixing historical scoring with non-capturable future rows:

- **`history_as_of_date`** is the latest daily date that can be evaluated **without lookahead** using the secondary daily close source and all six K=6 member daily close sources.
- **`current_snapshot`** is the 5-tuple on `history_as_of_date`.
- The history artifact does NOT create a synthetic future bar.
- Ranking and capture computation never use a bar without a known next secondary close.
- Any future "next-day forward signal" or projection display must be recorded **separately** from the historical scoring surface unless this contract is amended.

This section is intentionally strict. TrafficFlow has a single-next-day forward-signal extension at `trafficflow.py:1829` (`_next_signal_from_pkl_raw`); that extension is not part of the K=6 MTF historical scoring surface.

### Match Rule

The ranking engine walks historical daily bars **excluding the current-snapshot bar** (the last bar has no next close).

A historical bar matches `current_snapshot` when each timeframe slot passes:

- If current slot is `BUY`, historical slot must be `BUY` unless historical slot is `NONE` or `UNAVAILABLE`.
- If current slot is `SHORT`, historical slot must be `SHORT` unless historical slot is `NONE` or `UNAVAILABLE`.
- If current slot is `NONE` or `UNAVAILABLE`, that slot is unconstrained.
- If historical slot is `NONE` or `UNAVAILABLE`, that slot passes regardless of current slot.

This matches the OnePass-MTF wildcard precedent in `mvp_ranking_v1.py:327` `_bar_matches_alignment(bar_signals, current_alignment)`. The function implements exactly this rule: per timeframe, if either side is in `WILDCARD_SIGNAL_VALUES = frozenset({SIGNAL_NONE, SIGNAL_UNAVAILABLE})`, pass; else require exact equality.

### Trade Direction

Trade direction is **per matching historical bar**. It is read from that matching bar's own `1d` slot after K=6 combine and protocol application:

- historical `1d` `BUY` -> `BUY` trade
- historical `1d` `SHORT` -> `SHORT` trade
- historical `1d` `NONE` or `UNAVAILABLE` -> no-trade bar

Do **NOT** use:

- today's `1d` slot as a global direction scalar;
- K=6 total capture sign (this is the wrong rule that the OnePass-MTF bug `mvp_ranking_v1.py:303` `_step_trade_direction(k6_total_capture_pct)` currently uses; see the KNOWN_BUGS_LOG entry created by this PR);
- historical aggregate sign;
- `board_rows_k=6.json` direction.

Because the `1d` slot participates in the match rule, most `BUY` / `SHORT` current-snapshot cases will share their direction with the matching bar's `1d`. However, historical-side wildcards (`NONE` / `UNAVAILABLE`) can still pass, so the engine must read direction from the matching bar itself.

### Capture

For each matching bar with valid current and next secondary daily close:

- `raw_return_pct = (next_close / current_close - 1.0) * 100.0`
- `BUY` capture = `raw_return_pct`
- `SHORT` capture = `-raw_return_pct`
- no-trade capture = `0.0`

If current close or next close is missing, invalid, or non-positive, **skip that bar for capture metrics** and record a per-secondary issue.

Counts:

- `match_count`: all historical bars passing the match rule.
- `capture_count`: matched bars with valid current and next close. Used as the basis for `total_capture_pct` and `ccc_series`. Per the 2026-05-28 amendment, per-trade metric denominators (`avg_capture_pct`, `stddev_pct`, `sharpe_k6_mtf`, `win_count`, `loss_count`, `win_pct`, `low_sample_warning`) use `trade_count` (the directional-trade subset), not `capture_count`.
- `trade_count`: `capture_count` bars with `BUY` or `SHORT` direction.
- `no_trade_count`: `capture_count` bars with `NONE` or `UNAVAILABLE` direction.
- `skipped_capture_count`: matched bars skipped due to invalid close data.

### Honest Sharpe

**Metric basis split (locked as of 2026-05-28 amendment).** Per-trade metrics use the directional-trade subset only; `total_capture_pct` and CCC use the full captured-matching-bar set.

- **`total_capture_pct`** = `sum(per_bar_capture)` over `capture_count` bars (no-trade bars contribute `0.0`). Preserves cumulative-capture semantics.
- **Per-trade metric basis.** `avg_capture_pct`, `stddev_pct`, `sharpe_k6_mtf`, `win_count`, `loss_count`, `win_pct`, and `low_sample_warning` use the **directional-trade subset**: captured matching bars whose own `1d` slot is `BUY` or `SHORT`. `NONE` / `UNAVAILABLE` no-position bars are **excluded** from these per-trade denominators, even when they enter the matched set through wildcard matching of the current snapshot.
- **`avg_capture_pct`** = `sum(trade_captures) / trade_count`.
- **`stddev_pct`** = sample standard deviation of `trade_captures`, **`ddof=1`**.
- **`sharpe_k6_mtf`** = `(avg_capture_pct / stddev_pct) * sqrt(252)`.

#### Win, loss, and win_pct predicate

- `win_count` = number of `trade_count` bars with `per_bar_capture_pct > 0`.
- `loss_count` = `trade_count - win_count`. Directional-trade captures **exactly equal to `0.0`** are losses.
- `win_pct` = `win_count / trade_count * 100.0` when `trade_count > 0`; `null` otherwise.
- **Invariant: `win_count + loss_count == trade_count` exactly.** There is no third zero-return bucket and no `zero_trade_count` field.

This matches the project-wide convention at `canonical_scoring.py:207-209` (`wins = (trigger_caps > 0).sum()`, `losses = trigger_days - wins`). The K=6 MTF ranking engine implements the equivalent predicate **locally** because the engine must remain self-contained and preserve the artifact-as-boundary rule; it does not import `canonical_scoring`.

#### Undefined Sharpe policy

- If `trade_count < 2`, `sharpe_k6_mtf` is `null` (not `0.0`).
- If `stddev_pct == 0`, `sharpe_k6_mtf` is `null`.
- If `trade_count == 0`, all per-trade metrics (`avg_capture_pct`, `stddev_pct`, `sharpe_k6_mtf`, `win_pct`) are `null`; `win_count = 0`, `loss_count = 0`.
- Undefined-Sharpe records sort **below** numeric-Sharpe records in the ranking.
- Record a per-secondary issue such as `sharpe_undefined`.

This intentionally follows the OnePass-MTF undefined-Sharpe behavior in `mvp_ranking_v1.py:417-449` `_compute_v1_metrics(captures)`: ddof=1 sample stddev (L401-L414), sqrt(252) annualization (`TRADING_DAYS_PER_YEAR = 252` at L54, used at L448), and null Sharpe when n<2 or stddev==0 with a `sharpe_undefined_reason` field. The K=6 MTF launch path reuses the same convention, with the per-trade basis substitution above. **Do NOT use `0.0` for undefined Sharpe.**

This is **NOT** TrafficFlow's displayed subset-averaged Sharpe. TrafficFlow's UI Sharpe averages subset metrics: `trafficflow.py:2690` `compute_build_metrics_spymaster_parity(...)` produces the displayed UI metrics, and the explicit comments at `trafficflow.py:2842` ("Create snapshot with AVERAGES Sharpe (not unanimous combination Sharpe)") and L2865-L2866 ("`# Use AVERAGES Sharpe`") confirm that the displayed Sharpe is a mean across subsets. K=6 MTF Sharpe is computed directly over the same matching-bar captures whose directional subset produces the metric basis; the two values are not interchangeable.

### CCC Time Series

CCC is the cumulative capture over matching bars with valid capture data.

Each CCC point includes:

- `date_utc`
- `cumulative_capture_pct`
- `per_bar_capture_pct`
- `trade_direction`

No-trade bars (matching bars whose `1d` slot is `NONE` / `UNAVAILABLE`) are included with `per_bar_capture_pct = 0.0`. This intentionally creates **flat CCC segments** when the matched state is all-cash. CCC continues to render `capture_count` points regardless of the per-trade metric-basis split established in "Honest Sharpe" above; CCC length therefore equals `capture_count`, not `trade_count`.

The Dash chart should render CCC as a step plot, following the PR #334 visual precedent (`line.shape = "hv"` and the modal calendar note in `mvp_signal_board.py`).

### Ranking

Rank the 8 MVP secondaries by:

1. `sharpe_k6_mtf` descending, numeric values first.
2. `total_capture_pct` descending.
3. `secondary` alphabetically.

Failed secondaries and null-Sharpe secondaries remain in `per_secondary` but are excluded from `secondaries_ranked`. Within `per_secondary`, the ranking engine sorts numeric-Sharpe records first by the rule above, then null-Sharpe records alphabetically, then failed records alphabetically. This mirrors the OnePass-MTF ranking precedent at `mvp_ranking_v1.py:473` `_rank_records(records)`.

#### Low-sample warning

- `low_sample_warning = True` when `trade_count < 30`.
- `False` otherwise.

Threshold reuses the OnePass-MTF value at `mvp_ranking_v1.py:53` `LOW_SAMPLE_THRESHOLD = 30`. The basis is `trade_count` (directional-trade subset), not `capture_count`, consistent with the per-trade metric-basis rule in "Honest Sharpe" above: a record with hundreds of matched bars but only a handful of directional trades is genuinely under-sampled for per-trade statistics.

---

## Artifact Contracts

### History Artifact

- **schema_version:** `k6_mtf_history_v1`.
- **Path:** `output/k6_mtf/<RUN_TIMESTAMP>/<SEC>/k6_mtf_history.json`.

#### Top-level required fields

- `schema_version`
- `generated_at_utc`
- `run_id`
- `secondary`
- `history_as_of_date`
- `source_paths` (object naming the resolved secondary close source plus per-member source paths and per-(member, timeframe) library paths)
- `k6_stack` (see below)
- `timeframe_set` (the canonical list `["1d", "1wk", "1mo", "3mo", "1y"]`)
- `bars` (chronologically ascending; the last bar is `history_as_of_date`'s bar)
- `issues` (array; empty when none)

#### `k6_stack` fields

- `selected_build_path` (the `selected_build.json` path)
- `selected_run_dir`
- `combo_k6_path` (the `combo_k=6.json` path)
- `members`: array of `{ ticker, protocol }` objects (exactly 6)

#### Per-bar fields

- `date_utc`
- `secondary_close`
- `snapshot`: object with keys `1d`, `1wk`, `1mo`, `3mo`, `1y`; each value in `{BUY, SHORT, NONE, UNAVAILABLE}`
- `source_dates`: object with the same five keys; each value is the per-timeframe bar date used by the forward-fill rule
- `availability`: object with the same five keys. Each value is itself an object that includes:
  - `status` (one of `computed`, `forward_filled`, `unavailable`, `missing`);
  - `active_buy_count` (integer 0..6): number of active protocol-adjusted member signals that were `BUY` for that timeframe slot;
  - `active_short_count` (integer 0..6): number of active protocol-adjusted member signals that were `SHORT` for that timeframe slot;
  - `neutral_count` (integer 0..6): number of members that were neutral (`NONE` / `Cash` / `UNAVAILABLE` / `missing` / blank / null / unrecognized) for that timeframe slot.

  Invariant: `active_buy_count + active_short_count + neutral_count == 6` for every timeframe slot at every bar.

  These three counts are **descriptive provenance only** for the MVP. The combined signal in the `snapshot` object is derivable from these counts via the K=6 combined-signal lenient rule, but the counts are recorded explicitly so future participation-depth filtering can run against existing history artifacts without regeneration. The counts do NOT change the combine rule, the match rule, the trade-direction rule, the capture rule, the Sharpe computation, the ranking, or any MVP filtering behavior. Any future participation-depth filtering is a separate, explicitly-scoped enhancement.

  For `forward_filled` slots, the counts reflect the combined signal of the most recent closed period at or before the secondary daily bar (i.e., the counts of the period being forward-filled, not a recount on the daily date).

  The invariant `active_buy_count + active_short_count + neutral_count == 6` applies to every timeframe slot at every bar, **including** `computed`, `forward_filled`, `unavailable`, and `missing` slots. For `unavailable` or `missing` slots, unavailable / missing member states are counted as **neutral**: if no member has usable directional information for the slot, the producer records `active_buy_count = 0`, `active_short_count = 0`, and `neutral_count = 6`. If partial member information exists, the producer counts known active `BUY` and `SHORT` members and assigns every remaining member (including those whose state is unknown or could not be resolved) to `neutral_count`. **The three counts must never be all zero for a six-member K=6 slot.** Producers may still use `status` to describe whether the slot was computed, forward-filled, unavailable, or missing, but the count invariant always holds.

#### Optional but recommended (auditability)

- `member_signals_by_timeframe`: per-timeframe object carrying raw and protocol-adjusted member signals per bar.

If `member_signals_by_timeframe` is omitted for file-size reasons, the producer **must retain enough provenance** (e.g., paths in `source_paths`, per-bar `source_dates`, and the library version tags on those paths) to reproduce each combined slot from inputs.

### Ranking Artifact

- **schema_version:** `k6_mtf_ranking_v1`.
- **Path:** `output/k6_mtf/<RUN_TIMESTAMP>/k6_mtf_ranking.json`.

#### Top-level required fields

- `schema_version`
- `generated_at_utc`
- `run_id`
- `secondaries_requested`
- `secondaries_ranked`
- `per_secondary`
- `issues`

#### Per-secondary required fields

- `secondary`
- `rank` (integer or `null`)
- `status` (`"ranked"`, `"unranked"`, or `"failed"`)
- `history_artifact_path`
- `history_as_of_date`
- `current_snapshot` (5-tuple object)
- `k6_stack` (same shape as in the history artifact)
- `sharpe_k6_mtf`
- `total_capture_pct`
- `avg_capture_pct`
- `stddev_pct`
- `match_count`
- `capture_count`
- `trade_count`
- `no_trade_count`
- `skipped_capture_count`
- `win_count`
- `loss_count`
- `win_pct`
- `low_sample_warning`
- `ccc_series` (array of `{date_utc, cumulative_capture_pct, per_bar_capture_pct, trade_direction}`)
- `issues`

**Field semantics under the per-trade metric basis (2026-05-28 amendment).** Field **names** and the `k6_mtf_ranking_v1` schema label are unchanged from the initial contract; only the semantics of `avg_capture_pct`, `stddev_pct`, `sharpe_k6_mtf`, `win_count`, `loss_count`, `win_pct`, and `low_sample_warning` are clarified to use the directional-trade subset (see "Honest Sharpe" above). `total_capture_pct` continues to sum over `capture_count` bars and `ccc_series` continues to carry `capture_count` points including no-trade `0.0` flat segments. `match_count`, `capture_count`, `trade_count`, `no_trade_count`, and `skipped_capture_count` retain their original taxonomic meaning.

The ranking artifact is the **only** Dash input for the K=6 MTF board. Dash MUST NOT read history artifacts directly unless a future contract says otherwise. This is the same stable-boundary discipline established for OnePass-MTF in `md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md` L146 ("The artifact is the stable boundary.").

---

## Fail-Closed Behavior

Required failure behavior:

- Missing `selected_build.json`: per-secondary failure.
- Missing `selected_run_dir` (file references but directory does not exist): per-secondary failure.
- Missing `combo_k=6.json`: per-secondary failure.
- K=6 file does not contain exactly six members: per-secondary failure.
- Missing member PKL (`cache/results/<MEMBER>_precomputed_results.pkl`): per-secondary failure.
- Missing secondary close source (all three of `price_cache/daily/<SEC>.parquet`, `.csv`, and `cache/results/<SEC>_precomputed_results.pkl` absent): per-secondary failure.
- Missing per-timeframe member library after the required producer step has been run: per-secondary failure.
- Malformed history artifact: per-secondary failure inside the ranking engine.
- All secondaries fail: ranking engine exits non-zero and writes **no** ranking artifact.
- At least one secondary succeeds: ranking artifact IS written and includes failure records for the failed secondaries (per the OnePass-MTF precedent at `mvp_ranking_v1.py:473` `_rank_records`).

Exact exit codes are NOT specified in this contract; the ranking engine implementation PR locks them.

---

## Freshness Policy

The launch path can run with per-secondary `as_of` dates.

- For sprint-internal smoke tests, a small freshness lag is allowed if documented in the artifact.
- For public-facing or operator-live ranking, all six members and the secondary close source should be refreshed to the intended `as_of` date.
- SPY-only smoke is allowed when SPY's six K=6 member PKLs and SPY's secondary close source are fresh (the daily-close audit at `2026-05-27` confirmed all six SPY K=6 members refreshed to 2026-05-26 and SPY's own PKL fresh to 2026-05-26; the per-timeframe member libraries are a separate freshness axis that must be satisfied by the producer step).
- All-8 smoke requires source resolution for all 8 secondaries and all unique K=6 members. As of 2026-05-27, the K=6 member union across the 8 secondaries was 47 distinct tickers (CP appears in both SPY and MSFT stacks). If some member PKLs lag, the run may proceed only if the artifact records the lag and the operator accepts the `as_of` policy.

---

## Relationship to Existing Surfaces

- **TrafficFlow v0.** Daily K-build metrics with subset-averaged UI Sharpe (`trafficflow.py:2842`). Not the K=6 MTF ranking metric; the two should not be conflated in display surfaces.
- **OnePass-MTF.** Per-secondary five-timeframe signal analysis surface (`trafficflow_v1_history_writer.py`, `mvp_ranking_v1.py`, `mvp_signal_board.py` v1 dispatch). Already identity-corrected in PR #335 (`md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md` L18-L40 and `md_library/shared/2026-05-26_MVP_V1_HISTORY_ARTIFACT_CONTRACT.md` L18-L36). K=6 MTF is a separate, parallel surface.
- **K=6 MTF.** Stack-derived five-timeframe analysis; this launch path.
- **React migration.** `schema_version` is the stable boundary (`md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md` L146). K=6 MTF uses new schema labels (`k6_mtf_history_v1`, `k6_mtf_ranking_v1`) and does NOT reclaim existing OnePass-MTF schema labels (`mvp_v1_history_v1`, `mvp_ranking_v1`) in this PR. Schema-label cleanup is a separate future effort (see Deferred Items).

---

## Constellation Reuse

### Producer Layer

- `signal_library/multi_timeframe_builder.py`: source for per-timeframe signal library generation. Future PR may adjust `1wk` and `1mo` to daily-resampled source per this contract (the current code already resamples `3mo` and `1y` from daily; extending the same pattern to `1wk` and `1mo` is the smallest possible change). Cited current lines: `MAX_SMA_DAY = 114` at L62; `SIGNAL_LIBRARY_DIR` at L63; `fetch_interval_data(ticker, interval)` at L100; `apply_t1_skip(df, interval)` at L196; `generate_signals_for_interval(...)` at L279; ordered-pair search at L421 and L562; `generate_signal_series_dynamic` at L708; `save_signal_library(library, interval, force_overwrite)` at L843.
- `trafficflow_multitimeframe_bridge.py`: reference only, NOT the launch producer, because it projects daily signals (`resample(<freq>).last() + ffill`) instead of recomputing signals on resampled prices.

### Stack and Adapter Layer

- `multiwindow_k_input_adapter.py`: candidate reuse for resolving selected builds and per-(member, timeframe) libraries. Cited current lines: docstring at L37 (`signal_library/data/stable/<TICKER>_stable_v1_0_0[_<interval>].pkl` layout); skipped-cell reason constants `REASON_MISSING_TARGET_LIBRARY = "missing_target_library"` at L199 and `REASON_INCOMPLETE_MEMBER_COVERAGE` at L214; `prepare_multiwindow_k_inputs(...)` public entry at L1062.
- `multiwindow_k_engine_core.py`: candidate reuse for per-(K, window) combine + capture math. Cited current lines: `CANONICAL_WINDOWS` at L192; `CANONICAL_K_VALUES = tuple(range(1, 13))` at L195; `_apply_protocol(raw_signal, protocol)` at L273; `evaluate_cell(...)` at L358; `evaluate_k_window_grid(...)` at L595.

Future implementation may choose a simpler direct producer if that is lower risk, but it must preserve this contract's semantics (resample-prices-then-compute-signals; TrafficFlow-style active-signal unanimity combine across the six protocol-adjusted member signals, with neutral members abstaining and active conflict canceling to `NONE` per the rule in "K=6 Combined Signal Per Timeframe" above; per-slot `active_buy_count` / `active_short_count` / `neutral_count` recorded in the history artifact's `availability` object; forward-fill onto the secondary's daily calendar; per-matching-bar direction from the 1d slot; honest Sharpe ddof=1 * sqrt(252); null Sharpe policy). It must NOT adopt the `research_artifacts.combine_member_signals(..., K=6)` K-threshold combine as the launch-path combine rule.

### Ranking Layer

- `mvp_ranking_v1.py` provides reusable OnePass-MTF patterns:
  - match rule at L327 `_bar_matches_alignment(bar_signals, current_alignment)`
  - sample stddev + Sharpe at L401-L449 (`_sample_stddev` and `_compute_v1_metrics`)
  - CCC series at L457 `_compute_ccc_series(captures, dates)`
  - ranking sort at L473 `_rank_records(records)`
- The K=6 MTF ranking engine must **adapt capture collection** because direction is per matching bar's own 1d slot, not a global direction scalar (the current `_collect_matching_captures` at L363 takes a single global `direction` argument).
- The K=6 MTF ranking engine **must NOT reuse `mvp_ranking_v1.py`'s Step v1.1** (`_step_trade_direction(k6_total_capture_pct)` at L303). That function is the bug recorded in the KNOWN_BUGS_LOG entry created by this PR.

### Dash Layer

- `mvp_signal_board.py` has the PR #334 schema-dispatch precedent (v0 `mvp_ranking_v0` and v1 `mvp_ranking_v1`).
- A future Dash PR adds a third schema arm for `k6_mtf_ranking_v1`. The architecture supports the addition without refactoring; only a new dispatch branch is required.

---

## Implementation Chain

The minimum implementation sequence is five steps. Each step is a future PR; this contract does NOT introduce any of them.

1. **Producer source alignment PR.**
   - Adjust or verify `signal_library/multi_timeframe_builder.py` so `1wk` and `1mo` also use daily-resampled source per this contract (the current code already resamples `3mo` and `1y` from daily; extending the same convention to `1wk` and `1mo` is the smallest possible change).
   - Complexity: small/medium.
   - Tests: targeted producer tests proving resample-prices-then-compute-signals at all four non-daily timeframes.

2. **Data batch refresh.**
   - Operational task, not necessarily a code PR.
   - Build per-timeframe libraries for all unique K=6 members across the 8 MVP secondaries.
   - The 47-member count is observed as of 2026-05-27 and MUST be recomputed from current selected builds at run time.

3. **K=6 MTF history producer PR.**
   - New producer for `k6_mtf_history_v1`.
   - Inputs: selected K=6 stack (`combo_k=6.json`), per-(member, timeframe) signal libraries, secondary daily close source.
   - Output: per-secondary `k6_mtf_history.json` under `output/k6_mtf/<RUN_TIMESTAMP>/<SEC>/`.

4. **K=6 MTF ranking engine PR.**
   - New ranking engine for `k6_mtf_ranking_v1`.
   - Consumes only `k6_mtf_history_v1` artifacts (no Phase E reads at runtime).
   - Implements match rule, per-bar direction from the 1d slot, capture, honest Sharpe, CCC, ranking.
   - Does NOT alter the launch-path combine rule or run participation-depth filtering against the recorded `active_buy_count` / `active_short_count` / `neutral_count` provenance; those counts are descriptive only for the MVP.

5. **Dash third-schema PR.**
   - Extend `mvp_signal_board.py` to render `k6_mtf_ranking_v1`.
   - Preserve v0 (`mvp_ranking_v0`) and OnePass-MTF (`mvp_ranking_v1`) behavior.

---

## Deferred Items

### K-Ladder Ranking

Future enhancement. Walk `K=1` through `K=12`, capture each K's MTF snapshot under the same TrafficFlow-style active-signal unanimity combine the launch path uses (one or more active aligned members, no active dissent; otherwise `NONE`), and define ladder height as the highest K where the snapshot remains consistent with an anchor. Anchor policy is unresolved (fixed `K=1` anchor vs. sequential `K_N` vs. `K_(N-1)`) and the K-ladder definition of "consistent" is itself unresolved. The K-ladder enhancement does NOT switch the launch path's combine to a K-threshold mechanism. Deferred until after K=6 MTF MVP validates.

### OnePass-MTF Step v1.1 Trade-Direction Bug

`mvp_ranking_v1.py:303` `_step_trade_direction(k6_total_capture_pct)` currently derives OnePass-MTF direction from `sign(K=6 total capture %)`. That rule is wrong for OnePass-MTF because OnePass-MTF analyzes the secondary's own signals and the correct rule is the secondary's own most recent 1d signal. Recorded in `md_library/shared/2026-05-25_KNOWN_BUGS_LOG.md` (BUG-003, appended by this PR) and scheduled after K=6 MTF launch ships.

### Parallel BUY/SHORT Performance Visualization

Future investigation. The launch path displays the direction implied by the matched bar's 1d slot. Full parallel BUY/SHORT equity-curve visualization across direction flips is out of scope for the MVP and needs a separate design pass.

### All-Cash CCC Segments

Intentional behavior. When matched bars have 1d `NONE` or `UNAVAILABLE`, per-bar capture is `0.0` and CCC stays flat. The UI must NOT hide this; flat segments are honest.

### Universe Scope Expansion

Post-MVP. Expansion beyond the 8 secondaries is deferred to the large-universe planning cluster (`confluence_large_universe_launch_planner.py`, `confluence_large_universe_rollout_batch_planner.py`, `confluence_stackbuilder_rollout_policy.py`, `confluence_stackbuilder_pilot_preflight.py`, `confluence_impactsearch_primary_universe_readiness_planner.py`).

### Schema Label Cleanup

Future cleanup. OnePass-MTF labels (`mvp_v1_history_v1`, `mvp_ranking_v1`) may be renamed later for clarity. K=6 MTF uses new labels (`k6_mtf_history_v1`, `k6_mtf_ranking_v1`) from day one. This contract does NOT rename existing OnePass-MTF schemas.

---

## Required Future Contracts and Tests

Future implementation PRs must add focused tests for:

- daily-close source resolution (cache/results PKL vs price_cache parquet/csv vs PKL fallback for the secondary)
- per-window resample-prices-then-compute-signals behavior across `1wk` / `1mo` / `3mo` / `1y`
- K=6 member and protocol parsing from `combo_k=6.json`
- `[D]/[I]` protocol application
- TrafficFlow-style active-signal unanimity combine across the six protocol-adjusted member signals (neutral abstain, active conflict cancels to `NONE`, one or more aligned active members carry the slot)
- per-slot `active_buy_count` / `active_short_count` / `neutral_count` invariant (sums to six at every bar) recorded in the history artifact's `availability` object
- explicit confirmation that the launch path does NOT invoke the `research_artifacts.combine_member_signals(..., K=6)` K-threshold rule as the launch-path combine
- forward-fill snapshot alignment onto the secondary's daily calendar
- match-rule wildcard semantics for `NONE` and `UNAVAILABLE` on both sides
- per-bar direction from the historical 1d slot (NOT a global scalar)
- no-trade zero captures
- undefined Sharpe (`trade_count < 2`; `stddev_pct == 0`)
- low-sample warning at `trade_count < 30`
- zero-return BUY / SHORT directional trades count as losses (`losses = trade_count - wins`; loses-includes-zero rule per `canonical_scoring.py:207-209`)
- `win_count + loss_count == trade_count` exactly (no third zero-return bucket; no `zero_trade_count` field)
- `NONE` / `UNAVAILABLE` no-position bars are excluded from per-trade metric denominators (`avg_capture_pct`, `stddev_pct`, `sharpe_k6_mtf`, `win_count`, `loss_count`, `win_pct`, `low_sample_warning`) and are retained as no-trade `0.0` flat segments in `ccc_series`
- CCC step-series data shape (including no-trade flat segments)
- ranking tie-breaks (Sharpe desc -> total_capture_pct desc -> alphabetical)
- fail-closed missing inputs (each entry in "Fail-Closed Behavior" above)
- SPY-only real-data smoke
- all-8 smoke when sources are available

---

## References

Current line numbers verified at this PR's HEAD; future edits to the cited files may drift these numbers and any drift should be re-cited.

- `md_library/shared/2026-05-25_CONFLUENCE_TERMINOLOGY_GLOSSARY.md` Concept 4: L50-L56 ("Stack-aware multi-window K-build confluence"); summary table L60-L65; mapping L87-L113; "The launch path is Concept 4" at L128.
- `md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md` Identity Correction: L18-L40.
- `md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md` Amendment History entries at L407 (section-local) and L491 (document-level).
- `md_library/shared/2026-05-26_MVP_V1_HISTORY_ARTIFACT_CONTRACT.md` Identity Correction: L18-L36.
- `md_library/shared/2026-05-26_MVP_V1_HISTORY_ARTIFACT_CONTRACT.md` Amendment History at L324.
- `md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md` schema boundary language at L146 ("The artifact is the stable boundary.").
- `md_library/shared/2026-05-25_KNOWN_BUGS_LOG.md` (this PR appends BUG-003).
- `trafficflow.py`: `_choose_price_cache_path(symbol)` at L1065; `_load_secondary_prices(secondary, ...)` at L1117; `load_spymaster_pkl(ticker)` at L1442; `_processed_signals_from_pkl(primary)` at L1562; `_combine_positions_unanimity(pos_df)` at L1963 (the active-signal unanimity combine reused by the launch-path rule); the Buy/Short/None/Cash -> +1/-1/0 mapping at L1987; the active-count expression `c = (m != 0).sum(axis=1)` at L1991; the `np.where(s == c, 'Buy', ...)` arm at L1998 and `np.where(s == -c, 'Short', ...)` arm at L1999; the neutral fallback within the same `np.where` chain at L1997; `compute_build_metrics_spymaster_parity(secondary, members, ...)` at L2690; AVERAGES Sharpe comment at L2842; "Use AVERAGES Sharpe" at L2865 and L2866.
- `stackbuilder.py`: `DEFAULT_PRICE_CACHE_DIR` at L227; `load_secondary_prices(secondary)` at L536.
- `signal_library/multi_timeframe_builder.py`: `MAX_SMA_DAY = 114` at L62; `SIGNAL_LIBRARY_DIR` at L63; `fetch_interval_data(ticker, interval)` at L100; `apply_t1_skip(df, interval)` at L196; `generate_signals_for_interval(...)` at L279; `find_optimal_pairs(df, interval)` at L421; `find_optimal_pairs_vectorized(df, interval)` at L562; `generate_signal_series_dynamic(df, ...)` at L708; `save_signal_library(library, interval, force_overwrite)` at L843.
- `trafficflow_v1_history_writer.py`: `TIMEFRAMES_COVERED` at L51; `_forward_fill_signal_at(...)` at L253; forward-fill rule wording at L268; `build_v1_history_artifact(...)` at L319; `write_v1_history_artifact(...)` at L546.
- `multiwindow_k_engine_core.py`: `CANONICAL_WINDOWS` at L192; `CANONICAL_K_VALUES` at L195; `_apply_protocol(raw_signal, protocol)` at L273; `evaluate_cell(...)` at L358; the K-threshold combine call site `combined = _ra.combine_member_signals(active, K=K)` at L422 (NOT the launch-path combine); `evaluate_k_window_grid(...)` at L595.
- `research_artifacts.py`: `combine_member_signals(member_signals, K=...)` at L620 (the K-threshold combine mechanism; NOT the launch-path combine).
- `multiwindow_k_input_adapter.py`: stable-path comment at L37; `REASON_MISSING_TARGET_LIBRARY` at L199; `REASON_INCOMPLETE_MEMBER_COVERAGE` at L214; `prepare_multiwindow_k_inputs(...)` at L1062.
- `mvp_ranking_v1.py`: `LOW_SAMPLE_THRESHOLD = 30` at L53; `TRADING_DAYS_PER_YEAR = 252` at L54; `_step_trade_direction(k6_total_capture_pct)` at L303 (the bug recorded in BUG-003); `_bar_matches_alignment(bar_signals, current_alignment)` at L327; `_collect_matching_captures(v1_hist, direction, current_alignment)` at L363; `_compute_v1_metrics(captures)` at L417 (sqrt(252) at L448); `_compute_ccc_series(captures, dates)` at L457; `_rank_records(records)` at L473.

---

## Amendment History

2026-05-27 (initial): Launch-path contract created for the 8-secondary K=6 MTF MVP. Locks daily-close raw source, per-timeframe resample-prices-then-compute-signals behavior, K=6 unanimity combine, forward-fill snapshot alignment, per-bar direction from the historical 1d slot, honest Sharpe, CCC, ranking, artifacts, freshness policy, and deferred items.

2026-05-27 (amendment, combine-rule clarification): Clarified the K=6 combined-signal rule to be TrafficFlow-style active-signal unanimity: neutral member values (`NONE` / `Cash` / `UNAVAILABLE` / `missing` / blank / null / unrecognized) abstain, active members must not conflict, and one or more aligned active members can carry `BUY` or `SHORT`. "K=6" identifies the six-member StackBuilder build being evaluated; it does NOT impose a six-active-vote threshold on every bar. The contract now explicitly states that `research_artifacts.combine_member_signals(..., K=6)` thresholding is NOT the launch-path combine rule. The `k6_mtf_history_v1` per-bar `availability` object now records per-slot `active_buy_count` / `active_short_count` / `neutral_count` as descriptive provenance only (sums to six per slot per bar). The provenance counts do not change the combine rule, match rule, trade-direction rule, capture rule, Sharpe computation, ranking, or any MVP filtering behavior; participation-depth filtering is a separate future enhancement.

2026-05-28 (amendment, per-trade metric basis correction): Corrected the metric basis for `avg_capture_pct`, `stddev_pct`, `sharpe_k6_mtf`, `win_count`, `loss_count`, `win_pct`, and `low_sample_warning`. These per-trade metrics now use the directional-trade subset only (matched bars whose own `1d` slot is `BUY` or `SHORT`), not `capture_count`. `NONE` / `UNAVAILABLE` no-position bars are excluded from per-trade denominators. The loss predicate is locked as `losses = trade_count - wins`, so directional captures exactly equal to `0.0` are losses (matching the project-wide convention at `canonical_scoring.py:207-209`). `win_count + loss_count == trade_count` is now an invariant; there is no third zero-return bucket. `total_capture_pct` continues to sum over `capture_count` bars and `ccc_series` continues to render `capture_count` points including no-trade `0.0` flat segments. The undefined-Sharpe policy is updated to use `trade_count < 2` (instead of `capture_count < 2`). `low_sample_warning` is updated to use `trade_count < 30`. Schema label `k6_mtf_ranking_v1` and all per-secondary field names are unchanged; only the semantics of the affected fields are clarified. This amendment is implemented in PR A; PR B propagates the same predicate fix to other divergent project surfaces (`mvp_ranking_v1.py`, `multiwindow_k_engine_core.py`, `confluence_mtf_artifact_builder.py`, `trafficflow_multitimeframe_bridge.py`, `research_artifacts.py`, and two Spymaster fallback paths).
