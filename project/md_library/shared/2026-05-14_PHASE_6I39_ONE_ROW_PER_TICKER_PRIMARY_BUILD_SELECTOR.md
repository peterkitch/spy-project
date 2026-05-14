# Phase 6I-39: one-row-per-ticker display contract + primary K-build selector

**Branch:** `phase-6i-39-one-row-per-ticker-primary-build-selector`

## 1. Product rule pinned

A ticker is **one row** in the website ranking board. A
ticker MUST NOT appear multiple times just because
multiple K builds are currently active. Multiple active
K builds are surfaced via the **primary build** plus
compact `other_active_k_builds` on the SAME single row.
The detail drawer keeps the full 60-cell matrix from
Phase 6I-37.

This phase wires the contract end-to-end:

| Layer | Contract |
|---|---|
| Phase 6I-34 ranking export | `summary.display_row_cardinality="one_row_per_ticker"`; `PerTickerRankingRow.primary_build_summary` set on eligible rows |
| Phase 6I-35 website export package | Top-level `display_row_cardinality`; `ranking_rows[*].primary_build_summary`; `ticker_details[*].primary_build_summary` (eligible only; blocked → null) |
| Phase 6I-36 reader/view | Top-level `display_row_cardinality` (success + error view models); `ranking_table[*].primary_build` (compact); `ticker_cards[*].primary_build_summary` (eligible only) |

`DISPLAY_ROW_CARDINALITY` is a module-level constant on
both `confluence_multiwindow_ranking_export` and
`confluence_website_reader_view`.

## 2. Selection rule

The selector operates on the Phase 6I-37 60-cell matrix.
For each canonical `K ∈ {1..12}` it aggregates the K's
five (K, window) cells across the canonical windows
`1d / 1wk / 1mo / 3mo / 1y`.

### 2.1 Tiers (in priority order)

1. **Tier 1 — `same_k_all_windows_same_direction`** —
   `direction_conflict=False`. A K is in tier 1 iff every
   canonical window has the SAME
   `latest_combined_signal` value (Buy or Short). Pick the
   strongest K (see § 2.2).
2. **Tier 2 — `same_k_all_windows_mixed_direction`** —
   `direction_conflict=True`,
   `signal_direction="Mixed"`. A K is in tier 2 iff
   every canonical window has `currently_signaling=True`
   but the directions are not all the same (at least one
   Buy AND at least one Short across the five windows).
   Pick the strongest K.
3. **Tier 3 — `strongest_current_cell`** — fallback when
   no K fires across all five windows. Pick the single
   `currently_signaling` cell with the highest
   `total_capture_pct`. Direction is the cell's
   direction; `windows_signaling_count=1`.
4. **Tier 4 — `none`** — no cell currently signals.
   `primary_build_available=False`.

### 2.2 Strongest cascade for tiers 1 / 2

```
total_capture_pct_sum DESC   (sum across the five windows for this K)
avg_sharpe_ratio       DESC   (None treated as -inf so any defined Sharpe wins)
trigger_days_sum       DESC
K                       ASC   (tie-break)
```

The first row of the sorted list is the primary K.

### 2.3 Important honesty note

The existing
`current_build_signal_summary.k_builds_currently_signaling_all_windows`
only certifies that **some** Buy/Short signal fires in
every canonical window for that K — it does **NOT**
require the direction to be uniform across windows. The
Phase 6I-39 tier 1 / tier 2 split adds the direction-
consistent fields so the website does not overstate
confluence. Tier 1's `same_direction_k_builds_all_windows`
∪ Tier 2's `mixed_direction_k_builds_all_windows` is
exactly the same K set as the old
`k_builds_currently_signaling_all_windows`; the split is
direction-consistency.

## 3. Schema added

### 3.1 `primary_build_summary` (per ticker, on eligible rows; `None` otherwise)

| Field | Type | Notes |
|---|---|---|
| `primary_build_available` | bool | False only when no cell currently signals |
| `selection_tier` | str | One of `same_k_all_windows_same_direction` / `same_k_all_windows_mixed_direction` / `strongest_current_cell` / `none` |
| `K` | int / null | Primary K; null when tier=none |
| `signal_direction` | str / null | `Buy` / `Short` / `Mixed` / cell direction / null |
| `windows_signaling_count` | int | 5 for tiers 1+2; 1 for tier 3; 0 for tier 4 |
| `windows_signaling` | list[str] | Canonical-ordered windows where the primary K currently signals |
| `buy_window_count` | int | Count of (primary K, window) cells with Buy |
| `short_window_count` | int | Count of (primary K, window) cells with Short |
| `all_members_aligned_window_count` | int | Windows where all members aligned (for the primary K) |
| `total_capture_pct_sum` | float / null | Sum across the five windows for tiers 1+2; single-cell capture for tier 3; null for tier 4 |
| `avg_sharpe_ratio` | float / null | Avg across the five windows for tiers 1+2; single-cell Sharpe for tier 3 |
| `trigger_days_sum` | int | Sum across the five windows for tiers 1+2; single-cell trigger days for tier 3 |
| `strongest_cell_window` | str / null | For tiers 1+2: window with the highest `total_capture_pct` for the primary K. For tier 3: the cell's window. |
| `direction_conflict` | bool | True only on tier 2 |
| `explanation` | str | Stable string: `all_windows_same_direction` / `all_windows_mixed_direction` / `single_cell_fallback` / `no_current_signal` |
| `same_direction_k_builds_all_windows` | list[int] | Direction-uniform tier-1 K set |
| `mixed_direction_k_builds_all_windows` | list[int] | Direction-mixed tier-2 K set |
| `other_active_k_builds` | list[object] | Non-primary active K records (see § 3.2) |
| `display_row_cardinality` | str | `"one_row_per_ticker"` |

### 3.2 `other_active_k_builds[i]` entry

| Field | Type | Notes |
|---|---|---|
| `K` | int | |
| `signal_direction` | str / null | `Buy` / `Short` / `Mixed` / null |
| `windows_signaling_count` | int | |
| `buy_window_count` | int | |
| `short_window_count` | int | |
| `all_members_aligned_window_count` | int | |
| `total_capture_pct_sum` | float / null | |
| `avg_sharpe_ratio` | float / null | |
| `trigger_days_sum` | int | |

For tiers 1 / 2 the `other_active_k_builds` list is the
same-K-all-window set minus the primary K. For tier 3
(fallback) it is every K with at least one currently-
signaling cell, minus the primary K.

### 3.3 Reader/view compact `ranking_table[*].primary_build` block

| Field | Notes |
|---|---|
| `primary_build_available` | bool |
| `selection_tier` | string |
| `K` | int / null |
| `signal_direction` | string / null |
| `windows_signaling_count` | int |
| `direction_conflict` | bool |
| `explanation` | string |
| `label` | Pre-formatted short label, e.g. `"K=6 Buy in 5 window(s) (cap 50.00%, Sharpe 1.00)"`. `None` when no signal. |
| `other_active_k_count` | int |

The full primary-build dict (with all fields above) is
still available on `ticker_cards[*].primary_build_summary`
for the detail drawer.

## 4. Why this matters

The Phase 6I-37 surface alone could be rendered as
"K=2 Buy, K=5 Buy, K=8 Buy — three rows for AAA." That
would lie to the user about how many tickers are firing.
The Phase 6I-39 contract forbids it: AAA gets **one
row**, the primary build is K=5 (the strongest), and
K=2 / K=8 appear as compact `other_active_k_builds`
records on the same row.

## 5. No-fabrication contract preserved

Blocked / daily-only / partial-payload tickers carry:

- `primary_build_summary = None` on
  `PerTickerRankingRow`.
- `primary_build_summary = None` on package
  `ticker_details`.
- `primary_build_summary = None` on view-model
  `ticker_cards`.
- The compact `primary_build` block does NOT appear on
  the blocked_table (it lives on the `ranking_table` row
  only); blocked tickers project to `blocked_table`,
  which keeps its existing Phase 6I-36 shape.

## 6. Tests (18 new + 121 existing focused tests; 148 total)

New file:
`project/test_scripts/test_confluence_primary_build_selector.py`.

| # | Name | Pins |
|---|---|---|
| 1 | `test_ticker_with_many_active_k_builds_still_produces_one_row` | 8 same-K-all-window active builds → exactly one ranking row, one ticker card; `display_row_cardinality="one_row_per_ticker"` on both package + view model |
| 2 | `test_ranking_export_summary_advertises_one_row_per_ticker` | Ranking export `summary.display_row_cardinality="one_row_per_ticker"` |
| 3 | `test_same_K_buy_all_windows_selects_tier1` | Tier 1 selected; K=6 Buy; `direction_conflict=False`; `same_direction_k_builds_all_windows=[6]` |
| 4 | `test_tier1_strongest_pick_within_set` | Strongest K wins (K=4 over K=1/7/12 with higher capture); non-primary K's land in `other_active_k_builds` sorted ASC |
| 5 | `test_tier1_short_direction_picked_when_all_windows_short` | Tier 1 with Short across all five windows |
| 6 | `test_same_K_mixed_direction_selects_tier2` | Tier 2 selected; `signal_direction="Mixed"`; `direction_conflict=True` |
| 7 | `test_different_K_per_window_falls_back_to_tier3` | Tier 3 fallback when no K is in every window; primary K = cell with highest current capture (K=12, 1y, 18.0 in the fixture); `other_active_k_builds` lists the other active Ks |
| 8 | `test_all_none_pattern_primary_build_not_available` | Tier 4: `primary_build_available=False`, K=null, `explanation="no_current_signal"` |
| 9 | `test_multiple_active_k_builds_listed_in_other_active` | Three Ks in tier 1 → strongest is primary; the other two appear in `other_active_k_builds` with their own direction / counts / aggregates |
| 10 | `test_blocked_daily_only_ticker_has_no_primary_build` | Blocked daily-only row → `primary_build_summary=None` (no fabrication) |
| 11 | `test_package_blocked_ticker_detail_has_null_primary_build` | Phase 6I-35 package + Phase 6I-36 view model: blocked ticker's detail/card carry `primary_build_summary=None` |
| 12 | `test_package_ranking_row_carries_primary_build_summary` | Phase 6I-35 package `ranking_rows[].primary_build_summary` + `ticker_details[t].primary_build_summary` both populated for eligible rows |
| 13 | `test_reader_view_ranking_table_carries_primary_build_compact` | Phase 6I-36 compact `ranking_table[].primary_build` includes K, direction, windows count, label, `other_active_k_count`, `selection_tier` |
| 14 | `test_reader_view_primary_build_compact_handles_no_signal` | When `primary_build_available=False`, compact block is well-formed (not null) with `label=None`, `K=None`, `explanation="no_current_signal"` |
| 15 | `test_current_build_signals_matrix_still_available_alongside_primary` | Phase 6I-37 60-cell matrix stays available on `ticker_details` and `ticker_cards` alongside the new primary-build fields |
| 16 | `test_display_row_cardinality_constants_pinned` | Module-level constants on both `_cmre` and `_crv` carry `"one_row_per_ticker"` |
| 17 | `test_view_model_error_path_still_advertises_display_contract` | Schema-error view model still carries `display_row_cardinality="one_row_per_ticker"` |
| 18 | `test_multi_ticker_each_gets_exactly_one_row_and_card` | 3 eligible + 1 blocked → 3 ranking rows, 3 view-model rows, 4 ticker cards; each ticker appears EXACTLY once; primary tier selected per ticker (tier 1 / tier 2 / tier 3) |

## 7. Proof that one ticker cannot become multiple ranking rows

Test 1 (`test_ticker_with_many_active_k_builds_still_produces_one_row`) builds a
ticker AAA with **8 active K builds, each firing Buy in
every canonical window**. The assertion sequence:

- `package["eligible_count"] == 1`
- `len(package["ranking_rows"]) == 1`
- `len(package["ticker_details"]) == 1`
- `len(vm["ranking_table"]) == 1`
- `len(vm["ticker_cards"]) == 1`
- `package["display_row_cardinality"] == "one_row_per_ticker"`
- `vm["display_row_cardinality"] == "one_row_per_ticker"`

Test 18 (`test_multi_ticker_each_gets_exactly_one_row_and_card`) builds a 4-ticker
universe with overlapping active K builds and asserts each
ticker appears EXACTLY once in `ranking_rows`,
`ranking_table`, and `ticker_cards` (the test uses
`len(tickers) == len(set(tickers))` checks). The package's
ranking-row builder itself iterates `eligible_count` tickers
once each — there is no per-K explosion path anywhere in the
chain.

## 8. Validation run

Pinned conda interpreter
(`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`):

- `py_compile` on all three modified modules: clean.
- `pytest test_scripts/test_confluence_primary_build_selector.py -q`
  → **18 passed in 0.18s**.
- `pytest test_scripts/test_confluence_primary_build_selector.py
  test_scripts/test_confluence_current_build_signal_surface.py
  test_scripts/test_confluence_website_reader_view.py
  test_scripts/test_confluence_website_export_package.py
  test_scripts/test_confluence_multiwindow_ranking_export.py
  test_scripts/test_static_regression_guards.py -q`
  → **148 passed in 4.64s** (18 new + 30 current-signal +
  30 reader/view + 25 package + 36 ranking export + 9
  static regression).
- `git diff --check`: clean.
- B12 raw-pickle static regression guard still passes
  without a new allowlist entry.

## 9. SPY pilot status

Unchanged. SPY remains **PARKED** awaiting either:

- the equal-cutoff readiness policy decision (Phase 6I-38
  open question), OR
- the next trading-day rollover + yfinance publication for
  the 14 non-TEF SPY-K-universe tickers, plus a separate
  TEF triage path.

No source refresh / no production write of any kind was
performed in this PR.

## 10. No-production-activity confirmation

- No writer `--write` invocation (any writer).
- `PRJCT9_AUTOMATION_WRITE_AUTH` never read or set.
- No source refresh (`signal_engine_cache_refresher`).
- No `yfinance` fetch.
- No production promotion
  (`signal_library_stable_promotion_writer`).
- No Confluence patch writer
  (`multiwindow_k_confluence_patch_writer`).
- No `confluence_pipeline_runner` invocation.
- No StackBuilder / OnePass / ImpactSearch / TrafficFlow /
  Spymaster batch execution.
- No production data write.
- Production `signal_library/data/stable/` untouched.
- Production `output/research_artifacts/` untouched.
