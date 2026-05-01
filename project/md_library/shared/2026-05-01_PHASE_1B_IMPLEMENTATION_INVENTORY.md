# Phase 1B Implementation Inventory

Document date: 2026-05-01
Branch: phase-1b-1-inventory-canonical-module
Sources of truth:
  - project/md_library/shared/2026-04-30_PRJCT9_SPRINT_PLAN.md
  - project/md_library/shared/2026-04-30_PRJCT9_ALGORITHM_SPEC_v0_5.md
  - project/md_library/shared/2026-04-30_PHASE_1A_BASELINE_COVERAGE.md

All file:line citations are against `origin/main` at commit `a43d8ac`
(Phase 1A merge commit). Discovery was done via `git grep` over
tracked files.

## 1. Purpose

Phase 1B-1 produces a complete, exact discovery of every site that
the canonical-scoring rewire (Phase 1B-2) must touch or be aware of.
This document does NOT propose a 1B-2 implementation order; that
sequencing is the 1B-2 plan author's call after this inventory is
merged and audited.

Phase 1B-1 itself does not modify any engine code. The inventory
items below are findings, not change proposals.

## 2. Adj Close / price_basis removal inventory

The v0.5 spec §3 says raw `Close` is the only allowed price basis
and `Adj Close` is eliminated.

### 2a. Functional code paths (must change in 1B-2)

| File | Line | Site |
|---|---|---|
| `project/spymaster.py` | 80–83 | `_PRICE_BASIS = os.environ.get('PRICE_BASIS', 'adj').lower()` and `PRICE_COLUMN = 'Adj Close' if _PRICE_BASIS == 'adj' else 'Close'`. Default is `adj`. |
| `project/spymaster.py` | 93–95 | Refusal-to-guess column-presence check, dependent on `PRICE_COLUMN`. |
| `project/spymaster.py` | 3949 | yfinance call uses `auto_adjust=False` to keep both Close & Adj Close. |
| `project/spymaster.py` | 3994, 4014, 4019 | Fallback / preferred-basis branching. |
| `project/spymaster.py` | 5015 | `results['price_basis'] = PRICE_COLUMN` recorded into the result pkl. |
| `project/spymaster.py` | 5100 | `results['last_adj_close'] = last_adj` writes Adj-Close-derived value. |
| `project/spymaster.py` | 11858 | Reads `last_adj_close` from prior results. |
| `project/onepass.py` | 933 | `def compute_parity_hash(price_source='Adj Close', ...)`. |
| `project/onepass.py` | 977 | `def ... primary_signals, df, accumulator_state=None, price_source='Adj Close', resolved_symbol=None)`. |
| `project/onepass.py` | 1268–1274 | `price_basis = os.environ.get('PRICE_BASIS', 'adj').lower(); preferred = 'Adj Close' if price_basis == 'adj' else 'Close'`. |
| `project/onepass.py` | 1319–1325 | Same env-driven price basis resolution. |
| `project/onepass.py` | 1835–1836 | `env_basis = os.environ.get('PRICE_BASIS', 'adj').lower(); env_price_source = 'Adj Close' if env_basis == 'adj' else 'Close'`. |
| `project/onepass.py` | 2417–2419 | UI banner: `_BASIS = os.environ.get('PRICE_BASIS', 'adj').lower(); _BASIS_TEXT = 'Adj Close' if _BASIS == 'adj' else 'Close'`. |
| `project/impactsearch.py` | 305 | Boot log echoes `PRICE_BASIS`. |
| `project/impactsearch.py` | 1122 | `basis = os.environ.get('PRICE_BASIS', 'adj').lower()`. |
| `project/impactsearch.py` | 1359–1365 | Env-driven price basis selection in metrics path. |
| `project/impactsearch.py` | 1414–1423 | Same pattern, second site. |
| `project/impactsearch.py` | 2056–2067 | Env-driven Adj/raw switch and warning when library basis is overridden. |
| `project/impactsearch.py` | 2437–2438 | Same env-driven price source resolution. |
| `project/impactsearch.py` | 2602–2604 | UI banner: `_BASIS = os.environ.get('PRICE_BASIS', 'adj').lower(); _BASIS_TEXT = 'Adj Close' if _BASIS == 'adj' else 'Close'`. |
| `project/stackbuilder.py` | 223 | `def _fetch_secondary_from_yf(secondary, price_basis)`. |
| `project/stackbuilder.py` | 241–250 | Adj-Close column detection / rename branch. |
| `project/stackbuilder.py` | 258 | `def load_secondary_prices(secondary, price_basis)`. |
| `project/stackbuilder.py` | 281–286 | Adj/raw branching at on-disk cache load. |
| `project/stackbuilder.py` | 487 | `sec_df = load_secondary_prices(vendor_secondary, args.price_basis)`. |
| `project/stackbuilder.py` | 1275 | UI default `args.price_basis='adj'`. |
| `project/stackbuilder.py` | 1540 | `'price_basis': args.price_basis` recorded in run metadata. |
| `project/confluence.py` | 165 | Cache-key normalizer: "Normalizes cache key by ensuring price_basis is always explicit". |
| `project/confluence.py` | 169 | `kwargs.setdefault('price_basis', 'close')`. |
| `project/confluence.py` | 1356, 2181, 2460 | `_cached_fetch_interval_data(ticker, ..., price_basis='close')` callers. |
| `project/signal_library/multi_timeframe_builder.py` | 47 | `PRICE_BASIS = os.environ.get('PRICE_BASIS', 'close').lower()`. |
| `project/signal_library/multi_timeframe_builder.py` | 84–91 | `def fetch_interval_data(ticker, interval, price_basis: str = 'close')`. |
| `project/signal_library/multi_timeframe_builder.py` | 149–150 | `if price_basis == 'adj' and 'Adj Close' in df.columns: df = df[['Adj Close']].rename(...)`. |
| `project/signal_library/multi_timeframe_builder.py` | 279, 333 | Default `price_basis=PRICE_BASIS` and `'price_source'` field selection. |
| `project/signal_library/impact_fastpath.py` | 155 | Returns `False, "price_basis_mismatch (...)"`. |
| `project/signal_library/impact_fastpath.py` | 236 | `env_basis = "Adj Close" if os.environ.get("PRICE_BASIS", "adj").lower() == "adj" else "Close"`. |
| `project/signal_library/impact_fastpath.py` | 249 | Conditional bypass: `if (not ok) and why.startswith("price_basis_mismatch") and ALLOW_LIB_BASIS:`. |
| `project/onepass.py` | 674 | `rescale_cols = [c for c in ['Adj Close', 'Close', ...] if c in new_df.columns]`. |
| `project/onepass.py` | 1215 | `fields = {'Adj Close', 'Close', 'Open', 'High', 'Low', 'Volume'}`. |
| `project/impactsearch.py` | 1274, 1497 | Same field-set membership. |
| `project/stale_check.py` | 79 | `for col in ["Close", "Adj Close", "Volume"]:`. |

### 2b. Doc / comment / historical mentions (no functional change)

| File | Line | Note |
|---|---|---|
| `project/trafficflow.py` | 14, 83, 232 | Comment-only: "Always uses raw Close prices (no adjusted close)" / "PRICE_BASIS removed" / "Enforce raw Close only (never Adj Close)". |
| `project/test_scripts/test_phase1a_baseline_lock.py` | 19 | Doc-only mention. |
| `project/QC/Clone of Project 9/main.py` | 103, 918, 1509 | QC live-execution material. **Out of scope** — Phase -1 left this directory ignored. |
| `project/signal_library/shared_integrity.py` | 273 | Comment-only mention. |

## 3. Canonical scoring call-site inventory

For each engine, the call sites that compute (or wrap) any subset of
`{trigger_days, wins, losses, win_rate, avg_daily_capture,
total_capture, std_dev, sharpe, t_statistic, p_value}`.

### 3a. spymaster

| Lines | Site |
|---|---|
| `9080–9093` | Inline metrics block: ddof=1 std, t/p via `stats.t.cdf`, Sharpe. |
| `11115–11130` | Inline metrics block: `signal_captures.std(ddof=1)`, t/p, Sharpe. |
| `11668–11679` | Inline metrics block: `cap[trigger_mask].std()` (implicit ddof=0), t/p via cdf. |
| `12601–12614` | Inline metrics block: `trigger_captures.std()` (implicit ddof=0), t/p via cdf. |

### 3b. onepass

| Lines | Site |
|---|---|
| `1468–1568` | `_metrics_from_ccc` — full canonical-shaped output with ddof=1, t/p via cdf. |
| `1570–...` | `calculate_metrics_from_signals` — same shape, computes returns from prices. |
| `1658` | Standalone `signal_captures.std(ddof=1)`. |
| `1665` | `p_value = (2 * (1 - stats.t.cdf(...)))`. |

### 3c. impactsearch

| Lines | Site |
|---|---|
| `1561–...` | `calculate_metrics_from_signals` — full canonical-shaped output with ddof=1, t/p via cdf. |
| `1773–1872` | `_metrics_from_ccc` — full canonical-shaped output with ddof=1, t/p via cdf. |
| `1641, 1647` | Standalone `std_dev = signal_captures.std(ddof=1)` and cdf p-value. |
| `1819, 1833` | Equivalent inside `_metrics_from_ccc`. |

### 3d. trafficflow

| Lines | Site |
|---|---|
| `1553–1643` | `_metrics_like_spymaster` — canonical-shaped output. ddof=1; uses `daily_captures.to_numpy() != 0.0` as trigger mask (drops zero-capture trigger days). |
| `2046–2090` (around `2074, 2082`) | Second metrics block, ddof=1, cdf p-value. |
| `2440–2470` (around `2446, 2458`) | Vectorized variant, ddof=1, cdf p-value. |
| `2570–2590` (around `2575, 2582`) | Another metrics block, ddof=1, cdf p-value. |
| `2720–2740` (around `2723, 2731`) | Another metrics block, ddof=1, cdf p-value. |

### 3e. stackbuilder

| Lines | Site |
|---|---|
| `439–482` | `metrics_from_captures` — ddof=1, cdf p-value, uses `mask = captures.ne(0.0)` for triggers (drops zero-capture trigger days). |
| `674–689` | `_combined_metrics(member_caps)` averages member captures and routes through `metrics_from_captures`. |
| `750–771` | `_combined_metrics_signals` — combines signals first, then captures, then `metrics_from_captures`. |

### 3f. confluence

| Lines | Site |
|---|---|
| `364–408` | `_mp_metrics(captures, trig_mask, bars_per_year)` — ddof=1, cdf p-value, takes explicit trigger mask (does not drop zero-capture days from the mask). |
| `314–332` | `_mp_combine_unanimity_vectorized` — consensus combiner, no metrics. |

## 4. Confluence price_basis plumbing inventory

| File | Line | Site |
|---|---|---|
| `project/confluence.py` | 165 | Comment: "Normalizes cache key by ensuring price_basis is always explicit". |
| `project/confluence.py` | 169 | `kwargs.setdefault('price_basis', 'close')` inside the cache-key normalizer. |
| `project/confluence.py` | 1356 | `df_prices = _cached_fetch_interval_data(ticker, interval, price_basis='close')`. |
| `project/confluence.py` | 2181 | `px = _cached_fetch_interval_data(ticker, '1d', price_basis='close')`. |
| `project/confluence.py` | 2460 | `px = _cached_fetch_interval_data(ticker, '1d', price_basis='close')`. |

`signal_library/multi_timeframe_builder.py` is the call target for
the cached fetch and carries its own `price_basis` parameter (see
section 2a above).

The Phase 1A coverage report's planning note records the decision:
remove the `price_basis` arg/plumbing entirely rather than preserve
a constant `'close'` compatibility marker; existing caches should be
rebuilt if needed.

## 5. ddof inventory

Explicit `ddof=1` (matches v0.5 spec §16):

| File | Line | Site |
|---|---|---|
| `project/stackbuilder.py` | 452 | `metrics_from_captures`: `std = float(vals.std(ddof=1)) if n > 1 else 0.0`. |
| `project/onepass.py` | 1514 | `_metrics_from_ccc`: `std = float(np.std(signal_caps, ddof=1))`. |
| `project/onepass.py` | 1658 | `calculate_metrics_from_signals`: `std_dev = signal_captures.std(ddof=1)`. |
| `project/impactsearch.py` | 1641 | `calculate_metrics_from_signals`: `std_dev = signal_captures.std(ddof=1)`. |
| `project/impactsearch.py` | 1819 | `_metrics_from_ccc`: `std = float(np.std(signal_caps..., ddof=1)) if len(signal_caps) > 1 else 0.0`. |
| `project/confluence.py` | 377 | `_mp_metrics`: `std = float(vals.std(ddof=1)) if n > 1 else 0.0`. |
| `project/trafficflow.py` | 1616 | `_metrics_like_spymaster`: `std = float(trigger_caps.std(ddof=1)) if trigger_days > 1 else 0.0`. |
| `project/trafficflow.py` | 2074 | `std = float(np.std(tc, ddof=1)) if n_trig > 1 else 0.0`. |
| `project/trafficflow.py` | 2575 | `std = float(tc.std(ddof=1)) if trig_n > 1 else 0.0`. |
| `project/trafficflow.py` | 2723 | `std = float(np.std(tc, ddof=1)) if n_trig > 1 else 0.0`. |
| `project/spymaster.py` | 9082 | `std_dev = np.std(signal_captures, ddof=1)` (preceded by an explicit comment about sample-std intent). |
| `project/spymaster.py` | 11116 | `raw_std_dev = signal_captures.std(ddof=1) if trigger_days > 1 else 0.0`. |

Implicit-ddof (defaults to ddof=0 for numpy / pandas):

| File | Line | Site |
|---|---|---|
| `project/spymaster.py` | 1481 | `std_return = daily_returns.std()` — utility / position metric, not in the canonical scoring chain that feeds the result pkl. |
| `project/spymaster.py` | 1542 | `std_daily_move = recent_30d.std() if len(recent_30d) > 1 else avg_daily_move_30d` — position display. |
| `project/spymaster.py` | 8873, 8920 | `daily_vol = float(returns_in_position.std())` — position-level volatility display. |
| `project/spymaster.py` | 10605 | `annualized_std = combined_returns.std() * np.sqrt(252)` — combined-strategy display, kept in decimal form. |
| `project/spymaster.py` | 11668 | `std_dev = cap[trigger_mask].std() if trigger_days > 0 else 0` — **canonical-scoring site, implicit ddof=0**. |
| `project/spymaster.py` | 12601 | `std_dev = trigger_captures.std() if trigger_days > 0 else 0` — **canonical-scoring site, implicit ddof=0**. |

The two sites at `spymaster.py:11668` and `spymaster.py:12601`
silently use ddof=0 (population std) inside what is shaped like a
canonical-scoring block. The spec mandates ddof=1.

## 6. cdf p-value inventory

Every canonical-shaped p-value call site uses
`2 * (1 - stats.t.cdf(...))`. The v0.5 spec §17 says implementations
should use the numerically stable equivalent `2 * stats.t.sf(...)`.

| File | Line |
|---|---|
| `project/confluence.py` | 387 |
| `project/impactsearch.py` | 1647, 1833 |
| `project/onepass.py` | 1528, 1665 |
| `project/spymaster.py` | 9093, 11130, 11679, 12614 |
| `project/stackbuilder.py` | 458 |
| `project/trafficflow.py` | 1627, 2082, 2458, 2582, 2731 |

No `stats.t.sf` usage exists anywhere in tracked code.

## 7. Zero-capture trigger-day inventory

Sites that drop zero-capture trigger days by computing the trigger
mask from the capture series rather than from signal state:

| File | Line | Site |
|---|---|---|
| `project/stackbuilder.py` | 442 | `metrics_from_captures`: `mask = captures.ne(0.0)`. |
| `project/trafficflow.py` | 1600 | `_metrics_like_spymaster`: `trig_mask = daily_captures.to_numpy() != 0.0`. |
| `project/onepass.py` | 1487 | `_metrics_from_ccc`: legacy non-`active_pairs` fallback `trig_mask = np.abs(caps) > 0`. |
| `project/impactsearch.py` | 1792 | `_metrics_from_ccc`: legacy non-`active_pairs` fallback `trig_mask = np.abs(caps) > 0`. |

Sites that count triggers from explicit signal state (spec-aligned):

| File | Line | Site |
|---|---|---|
| `project/onepass.py` | 1483 | `trig_mask = np.array([p.startswith('Buy') or p.startswith('Short') ...])`. |
| `project/impactsearch.py` | 1788 | Same convention. |
| `project/impactsearch.py` | 1622 | `calculate_metrics_from_signals`: `trigger_mask = buy_mask | short_mask`. |
| `project/confluence.py` | 365 | `_mp_metrics` accepts an explicit trigger mask. |

The v0.5 spec §15 says zero-capture trigger days count as losses.

## 8. Sentinel pair inventory

| File | Line | Sentinel form |
|---|---|---|
| `project/spymaster.py` | 4827 | `daily_top_buy_pairs[dates[day_idx]] = ((1, 2), 0.0)` (streaming day-0 fallback). |
| `project/spymaster.py` | 4828 | `daily_top_short_pairs[dates[day_idx]] = ((2, 1), 0.0)` (streaming day-0 fallback). |
| `project/spymaster.py` | 4865 | `daily_top_buy_pairs[dates[day_idx]] = (best_buy_pair or (1, 2), ...)` (streaming pair-not-found fallback). |
| `project/spymaster.py` | 4866 | `daily_top_short_pairs[dates[day_idx]] = (best_short_pair or (2, 1), ...)` (streaming pair-not-found fallback). |
| `project/spymaster.py` | 3323, 3331 | `pair = (MAX_SMA_DAY, MAX_SMA_DAY - 1)` — utility-function sentinel. |
| `project/spymaster.py` | 4972–4984 | Vectorized post-pass: replaces `(0, 0)` days with `(max_sma_day, max_sma_day - 1)` for buy and `(max_sma_day - 1, max_sma_day)` for short, logged as MAX-SMA sentinels. |
| `project/spymaster.py` | 5046–5057 | Leader-on-last-day buy fallback to `(msd, msd - 1)`. |
| `project/spymaster.py` | 5070–5073 | Leader-on-last-day short fallback to `(msd - 1, msd)`. |
| `project/spymaster.py` | 7469–7500 | Comment + alignment fallback referencing MAX-SMA sentinels. |
| `project/spymaster.py` | 7552 | Sim block: "Treat (0,0) as invalid → swap to MAX-SMA sentinels for simulation". |

The v0.5 spec appendix calls out this exact inconsistency: the dead
streaming path uses `(1, 2)` / `(2, 1)`; live vectorized / leader
fallback uses `(MAX_SMA_DAY, MAX_SMA_DAY - 1)` / `(MAX_SMA_DAY - 1,
MAX_SMA_DAY)`. Removing the streaming path (section 16) eliminates
the `(1, 2)` / `(2, 1)` pair as a side effect.

## 9. Calendar grace days inventory

| File | Line | Site |
|---|---|---|
| `project/impactsearch.py` | 307 | Boot log echoes `IMPACT_CALENDAR_GRACE_DAYS` with default `'7'`. |
| `project/impactsearch.py` | 2013–2016 | `grace_days = int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '7') or 7)` then `reindex(method='pad', tolerance=...)`. |
| `project/impactsearch.py` | 2371–2375 | Same pattern (default `'7'`). |
| `project/stackbuilder.py` | 69 | `DEFAULT_GRACE_DAYS = int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '7') or 7)`. |
| `project/stackbuilder.py` | 412–428 | `align_signals_to_calendar`-style block: uses `DEFAULT_GRACE_DAYS`, optional `IMPACT_DEBUG_ALIGN` log line. |
| `project/stackbuilder.py` | 715–717 | `_signals_aligned_and_mask`: `grace_days = int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '0') or 0)` — **default `0`, not `7`**. |
| `project/stackbuilder.py` | 1509 | `os.environ['IMPACT_CALENDAR_GRACE_DAYS'] = str(getattr(args, 'grace_days', 0) or 0)` — sets env var to args value before sub-calls. |
| `project/signal_library/impact_fastpath.py` | 86 | `IMPACT_CALENDAR_GRACE_DAYS = int(os.environ.get(..., "7"))`. |
| `project/trafficflow.py` | 85, 1305, 1396 | Comments only: "GRACE_DAYS removed - SpyMaster doesn't use grace periods". |
| `project/QC/Clone of Project 9/main.py` | 549, 857 | QC live-execution: `self._grace_days = int(self.GetParameter("grace_days", "3"))` — out of scope. |

The defaults are split: `7` in impactsearch + stackbuilder + impact_fastpath, `0` at one stackbuilder site, `3` in QC. The spec §20 says the default is `10`. The non-QC engine defaults are inconsistent with the spec.

## 10. Engine log handler inventory

Sites that open log files at module import time using a relative
path:

| File | Line | Site |
|---|---|---|
| `project/spymaster.py` | 2856 | `has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)`. |
| `project/spymaster.py` | 2869 | `file_handler = logging.FileHandler('logs/spymaster.log', encoding='utf-8')`. |
| `project/spymaster.py` | 2879–2884 | Subsequent handler-class type checks. |
| `project/onepass.py` | 81–83 | `# Create logs directory before FileHandler` then `file_handler = logging.FileHandler('logs/onepass.log', mode='w')`. |
| `project/impactsearch.py` | 346–348 | Same pattern: `file_handler = logging.FileHandler('logs/impactsearch.log', mode='w')`. |
| `project/global_ticker_library/tickerdash.py` | 8, 24 | `RotatingFileHandler` — separate concern. |

The relative `logs/` path resolves against the importer's cwd. When
pytest runs from the repo root, this places `logs/` at the repo root
where the root `.gitignore` does not cover it; running from
`project/` keeps it under `project/logs/` where `*.log` is ignored.
The Phase 0 PR added a `cd project` guard to the documented test
command. The v0.5 spec does not own this; the Phase 1A planning
note explicitly recommended Phase 1 anchor handlers to
`project/logs/`.

## 11. TrafficFlow `_PRICE_CACHE` inventory

Module-level declaration:

| File | Line | Site |
|---|---|---|
| `project/trafficflow.py` | 240 | `_PRICE_CACHE: Dict[str, pd.DataFrame] = {}  # secondary -> Close df (Spymaster parity cache)`. |
| `project/trafficflow.py` | 270 | `_PRICE_CACHE.clear()`. |

Read sites:

| Line | Symbol form used in lookup |
|---|---|
| `1066–1067` | `sec` (uppercase: `(secondary or "").upper()` at line 1063 inside `_load_secondary_prices`). |
| `1507` | `secondary` as-passed (no normalization). |
| `1564` | `secondary` as-passed (no normalization). Read by `_metrics_like_spymaster`. |
| `1929`, `2261`, `2352`, `2499`, `2626`, `2794`, `2845` | `secondary` as-passed (no normalization). |

Write sites:

| Line | Key form used in write |
|---|---|
| `1079`, `1092` | `sec` (uppercase, inside `_load_secondary_prices`). |
| `1115`, `1132`, `1135`, `1146`, `1152` | `sym` (uppercase, inside cache-prime helpers). |
| `1567` | `secondary` as-passed. Write by `_metrics_like_spymaster` after fetch. |
| `1932`, `2264`, `2355`, `2502`, `2629`, `2797`, `2848` | `secondary` as-passed. |

`_load_secondary_prices` writes only with the uppercase form. Most
direct-cache callers (including `_metrics_like_spymaster`) read and
write with the literal `secondary` argument. If a caller passes a
mixed-case symbol after `_load_secondary_prices` has populated the
cache with the uppercase form, the lookup misses and falls through
to a fetch.

## 12. StackBuilder closure bug inventory

Site: `project/stackbuilder.py` lines `1260–1301`.

```text
for sec in secondaries:
    ...
    args = SimpleNamespace(secondary=sec, ..., progress_path=ppath)

    def _job():
        try:
            run_for_secondary(args, sec, specified_primaries=primaries if primaries else None)
        except BaseException as e:
            ...
            print(f"[ERROR] Job failed for {sec}:\n{full_trace}")
            _write_progress(ppath, ...)

    threading.Thread(target=_job, daemon=True).start()
```

`_job` is a closure over `sec`, `args`, `ppath`, and `primaries`. All
four names are reassigned by each iteration of the outer
`for sec in secondaries:` loop, so any thread that has not yet
called `run_for_secondary` ends up reading whatever the LAST loop
iteration's bindings were. Threads started early in the loop can
therefore execute against the wrong secondary's `args`/`ppath`.

The classic fix is to pass loop-iteration values as default
arguments (`def _job(sec=sec, args=args, ppath=ppath, primaries=primaries):`)
or to wrap the body in a small factory; either approach is
behavior-fixing in 1B-2.

The Phase 1A coverage report and the v0.5 spec name this as a Phase
1 fix.

## 13. StackBuilder `--outdir` inventory

CLI flag declared at:

| Line | Site |
|---|---|
| `project/stackbuilder.py` | 1717 | `p.add_argument('--outdir', default=RUNS_ROOT)`. |
| `project/stackbuilder.py` | 1739 | `ensure_dir(args.outdir)`. |

Writer paths that ignore `args.outdir`:

| Line | Site |
|---|---|
| `project/stackbuilder.py` | 568–569 | `write_table(rank_direct, os.path.join(outdir, 'rank_direct'))` — uses local `outdir` parameter. |
| `project/stackbuilder.py` | 654–655 | Same pattern in another `phase2_rank_all` flow. |
| `project/stackbuilder.py` | 869 | `write_json(os.path.join(outdir, 'combo_k=1.json'), leaderboard[0])` — uses local `outdir` parameter. |
| `project/stackbuilder.py` | 1528, 1550, 1556, 1570, 1579, 1616 | Calls passing `outdir=temp_outdir` — Dash UI path uses a `temp_outdir` (per-run tmp) instead of `args.outdir`. |
| `project/stackbuilder.py` | 1275 | UI builds `args` with `outdir=RUNS_ROOT` regardless of CLI. |

The CLI parses `--outdir` but the UI-driven multi-secondary flow
ignores it (uses `RUNS_ROOT` and `temp_outdir`); the CLI-driven
single-secondary flow does honor it. Phase 1A coverage report and
v0.5 spec list this as a Phase 1 fix.

## 14. ImpactSearch xlsx duplicate-row inventory

Site: `project/impactsearch.py` lines `1933–1947`.

```python
if os.path.exists(output_filename):
    existing_df = pd.read_excel(output_filename)
    new_df = pd.DataFrame(normalized_rows)
    combined_df = pd.concat([existing_df, new_df], ignore_index=True)

    # Coerce Sharpe to numeric before sorting to avoid float<->str errors
    if 'Sharpe Ratio' in combined_df.columns:
        combined_df['Sharpe Ratio'] = pd.to_numeric(combined_df['Sharpe Ratio'], errors='coerce')
        combined_df.sort_values(by='Sharpe Ratio', ascending=False, inplace=True, na_position='last')

    # Ensure column order
    combined_df = combined_df.reindex(columns=desired_order +
                                     [col for col in combined_df.columns if col not in desired_order])

    combined_df.to_excel(output_filename, index=False)
```

When the output file already exists, the read-then-concat path adds
the new metrics on top of whatever rows are already there, with no
dedupe by `Primary Ticker`. Calling `export_results_to_excel` twice
with the same `metrics_list` therefore writes both copies. The Phase
1A baseline test
`test_impactsearch_export_writes_duplicates_pending_bug_fix` pins
the current 4-row outcome.

## 15. StackBuilder Phase 2 vs Phase 3 scoring divergence inventory

Phase 2 (`phase2_rank_all`):

| Lines | Site |
|---|---|
| `project/stackbuilder.py` | `561–569` and `645–656` | `rank_direct = rank_all.sort_values(...)` followed by `rank_inverse = rank_all.copy()` with sign-flipping certain columns. Both written via `write_table(...)` to `outdir`. The metrics in `rank_all` come from `_score_primary` → `metrics_from_captures` (which uses `mask = captures.ne(0.0)` for triggers — see section 7). |
| `project/stackbuilder.py` | `518–540` | `_score_primary(primary, sec_rets)` — calls `apply_signals_to_secondary` then `metrics_from_captures` to score one primary against the secondary. |
| `project/stackbuilder.py` | `align_signals_to_calendar`-style block at `412–437` | Uses `DEFAULT_GRACE_DAYS=7` (env-driven). |

Phase 3 (`phase3_build_stacks` / K-search):

| Lines | Site |
|---|---|
| `project/stackbuilder.py` | `773–...` | `phase3_build_stacks(args, rank_direct, rank_inverse, sec_rets, outdir, progress_cb=None)`. |
| `project/stackbuilder.py` | `825–869` | Builds K=1 singles, scores them via `_combined_metrics_signals` (which uses `_signals_aligned_and_mask` at `692–724` with grace_days from `IMPACT_CALENDAR_GRACE_DAYS` defaulting to **`0`** at line `715`). |
| `project/stackbuilder.py` | `869` | Writes `combo_k=1.json`. |

Why the two phases can disagree:

  - Phase 2 takes captures for a primary against the secondary via
    `apply_signals_to_secondary` (line 412-block), which uses
    `DEFAULT_GRACE_DAYS=7` as the calendar tolerance.
  - Phase 3 K=1 takes signals via `_signals_aligned_and_mask`
    (line 715), which reads a separate `IMPACT_CALENDAR_GRACE_DAYS`
    env-var default of `0` and applies a strict-calendar `present`
    mask before computing captures.
  - The two paths therefore can produce different trigger-day sets
    for the same primary against the same secondary.
  - `_combined_metrics_signals` then routes the K=1 captures through
    the same `metrics_from_captures` that Phase 2 uses, but on a
    different (strict-calendar) capture series.

Codex's prior 10-folder artifact verification (sampled
`combo_k=1.json` files disagreeing with `rank_direct` / `rank_inverse`
top-row metrics) is the binding evidence. Canonical-scoring
unification eliminates this divergence by routing both phases
through one calendar-alignment policy and one trigger-mask policy.

## 16. Spymaster dead streaming path inventory

| Lines | Site |
|---|---|
| `project/spymaster.py` | `4815–4816` | `total_pairs = ...; work_estimate = ...; use_streaming = False  # force vectorized path for correctness on small tickers`. |
| `project/spymaster.py` | `4818` | Comment header `# -- Streaming (tiny problems): O(days * SMA^2), tiny only`. |
| `project/spymaster.py` | `4819–4866` | `def _compute_daily_top_pairs_streaming(): ...` function body (the dead path). Includes the `(1, 2)` / `(2, 1)` sentinel writes at 4827–4828 and 4865–4866. |
| `project/spymaster.py` | `4990–4994` | Call site: `if use_streaming: _compute_daily_top_pairs_streaming() else: _compute_daily_top_pairs_vectorized()` — the if-branch is unreachable because `use_streaming` is hardcoded `False` immediately above. |

Removing this path also removes the `(1, 2)` / `(2, 1)` sentinel as
a side effect.

## 17. Risks / review notes

  - **Adj Close removal is the largest surface.** Section 2a
    enumerates ~15 distinct call sites across spymaster, onepass,
    impactsearch, stackbuilder, confluence, signal_library, and
    stale_check, plus boot logs and UI banners that surface
    `PRICE_BASIS`. The `args.price_basis` plumbing in stackbuilder
    is also live (line 1275 default `'adj'`). The v0.5 spec is
    unambiguous — raw Close only — so the change is direction-clear,
    but the call-site count means the Phase 1B-2 PR diff will be
    wide. Cache compatibility (existing `*.pkl` files have
    `'price_basis': PRICE_COLUMN` with the value `'Adj Close'` from
    spymaster:5015) will need a rebuild on first canonical run.
  - **Three `_metrics_from_ccc` legacy fallbacks were already
    tested by Phase 1A but only on the `active_pairs` path.** The
    legacy `np.abs(caps) > 0` fallback is also pinned (the
    `_LEGACY` snapshots), so any regression in the fallback path
    is caught.
  - **Spymaster has two implicit-ddof canonical-shaped sites**
    (`spymaster.py:11668` and `12601`) that the spec says must be
    ddof=1. These are the only canonical-scoring sites where the
    behavior change is *inside* spymaster's metrics logic rather
    than at a sister-engine helper.
  - **Calendar grace days defaults are split.** Spec says `10`;
    impactsearch / stackbuilder / impact_fastpath default to `7`;
    stackbuilder's `_signals_aligned_and_mask` at line 715 defaults
    to `0`; QC defaults to `3`. The `0`/`7` split is itself a major
    contributor to Phase 2 vs Phase 3 divergence (section 15);
    unifying to `10` per spec is therefore part of the StackBuilder
    fix as well as a standalone change.
  - **No tracked tests exist for stale_check.py.** Its Adj Close
    reference at line 79 will be touched in 1B-2; a brief test (or
    a deliberate no-test waiver in the ledger) is worth flagging.
  - **Phase 4 confluence rewrite is not yet scoped.** The
    multi-timeframe scrub work is owned by Phase 4. Phase 1B-2
    should remove `price_basis` plumbing from confluence.py without
    extending it; confluence's existing single-ticker behavior
    must continue to function until Phase 4 replaces it.
  - **TrafficFlow's `_metrics_like_spymaster` uses the
    drop-zero-capture-day trigger mask** (section 7). Bringing it
    into spec compliance changes its `Triggers` count and likely
    every downstream metric on inputs that contain zero-return
    days; the Phase 1A baseline pin
    `test_trafficflow_metrics_like_spymaster_baseline` will flip
    in 1B-2 and needs a ledger entry.
  - **Sentinel pair standardization is largely a side effect of
    removing the dead streaming path** (section 16). The leader
    fallback and vectorized post-pass already use the canonical
    `(MAX_SMA_DAY, MAX_SMA_DAY - 1)` form.
  - **Engine log handler relocation is in scope** for Phase 1B-2
    per the Phase 1A planning note, but it does not change scoring
    behavior. It is captured here so 1B-2 can decide whether to
    bundle it or split it out.

This inventory does not propose a 1B-2 implementation order;
sequencing decisions belong to the 1B-2 plan author after this
document is merged and audited.
