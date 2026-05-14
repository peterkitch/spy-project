# Phase 6I-37: Current build signal surface + North Star refresh

**Branch:** `phase-6i-37-current-build-signal-surface`

## 1. Why this phase exists

The Phase 6I-34 / 6I-35 / 6I-36 chain already shipped a multi-
ticker ranking export, a website export package, and a read-
only reader/view layer. But that chain was a **payload-only**
contract — it summarized what windows had at least one firing
cell, what cells had ever fired historically, and roughly how
strong the best historical capture was. It did **not** make
it easy for the eventual website renderer to answer the
single question the operator asks every day:

> *"What tickers/builds are firing now across multiple
>  windows, and what has historically happened when they
>  fired?"*

That question requires both:

1. **Current signal state per cell** — what is the latest
   combined Buy / Short / None signal on this `(K, window)`
   cell, how many members agree with it, is every member
   aligned with it.
2. **Historical performance per cell** — what is the
   capture, Sharpe, trigger-day count, win/loss count for
   this same cell over the build's history.

Phase 6I-37 threads both through the entire read-only chain
without weakening Phase 6I-20 strict validation and without
fabricating any data on blocked tickers.

This phase also refreshes the **Product North Star** in
`project/CLAUDE.md` and in sprint memory so the next phase
cannot drift back into "SPY-only proof path is the launch."

## 2. Architecture summary

Three layers, all strictly read-only:

| Layer | Module | New fields |
|---|---|---|
| Phase 6I-34 (ranking export) | `confluence_multiwindow_ranking_export.py` | `current_build_signals` (tuple of 60 cell dicts) + `current_build_signal_summary` (aggregate dict) on `PerTickerRankingRow`; emitted via `to_json_dict()`; blocked rows → empty tuple + null summary |
| Phase 6I-35 (website export package) | `confluence_website_export_package.py` | `current_build_signal_summary` on `ranking_rows`; full `current_build_signals` + `current_build_signal_summary` on eligible `ticker_details`; blocked rows → empty list + null summary |
| Phase 6I-36 (reader/view) | `confluence_website_reader_view.py` | compact `current_signal_summary` on `ranking_table` rows; full `current_build_signals` + `current_build_signal_summary` on eligible `ticker_cards`; blocked cards → empty list + null summary |

No new modules. All work is plumbing + transformation of
existing Phase 6I-23 cell fields through the existing read-
only chain.

## 3. Field-level schema

### 3.1 Per-cell row in `current_build_signals` (60 rows / eligible ticker)

Canonical `(window, K)` order: windows in canonical order
`1d / 1wk / 1mo / 3mo / 1y`, K ascending 1..12 within each
window. The renderer can iterate deterministically.

| Field | Type | Source |
|---|---|---|
| `ticker` | str | repeated for renderer convenience |
| `K` | int | canonical 1..12 |
| `window` | str | canonical |
| `latest_combined_signal` | str | from Phase 6I-23 cell |
| `latest_buy_count` | int | from Phase 6I-23 cell |
| `latest_short_count` | int | from Phase 6I-23 cell |
| `latest_none_count` | int | from Phase 6I-23 cell |
| `latest_missing_count` | int | from Phase 6I-23 cell |
| `member_count` | int | from Phase 6I-23 cell |
| `alignment_ratio` | float | aligned-direction count / member_count (0..1); 0.0 when signal is None/missing or member_count=0 |
| `all_members_aligned` | bool | alignment_ratio==1.0 AND member_count>0 AND signal in Buy/Short |
| `currently_signaling` | bool | **CURRENT state**: latest_combined_signal in Buy/Short |
| `currently_firing` | bool | **CURRENT state**, alias of `currently_signaling` for UI clarity (amendment-1) |
| `historically_fired` | bool | **HISTORICAL**: `trigger_days > 0` — cell has fired at least once in the build's history. Renamed from `firing` (amendment-1) because the old name read like "is firing now" but meant "has historically fired." |
| `total_capture_pct` | float | from Phase 6I-23 cell |
| `avg_daily_capture_pct` | float / null | from Phase 6I-23 cell |
| `sharpe_ratio` | float / null | from Phase 6I-23 cell |
| `trigger_days` | int | from Phase 6I-23 cell |
| `wins` | int / null | from Phase 6I-23 cell when present |
| `losses` | int / null | from Phase 6I-23 cell when present |

### 3.2 `current_build_signal_summary` aggregate (per eligible ticker)

**Amendment-1 split** (Codex audit): the summary now
distinguishes the loose **any-K** "every window has at
least one currently-signaling cell" predicate from the
strict **same-K** "the SAME K value is currently signaling
in every canonical window" predicate. The strict same-K
fields are what the TrafficFlow-style Confluence North
Star actually requires when the user asks "which K builds
are firing now across all five windows."

#### Cell counts

| Field | Type | Notes |
|---|---|---|
| `cells_total` | int | 60 when grid is complete |
| `cells_currently_buy` | int | latest_combined_signal == "Buy" |
| `cells_currently_short` | int | latest_combined_signal == "Short" |
| `cells_currently_none` | int | latest_combined_signal == "None" |
| `cells_currently_missing` | int | else |
| `cells_with_all_members_aligned` | int | |
| `cells_historically_fired` | int | trigger_days > 0 (renamed from `cells_historically_firing` in amendment-1) |

#### Any-K (loose) cross-window

| Field | Type | Notes |
|---|---|---|
| `windows_with_any_currently_signaling` | list[str] | canonical-ordered windows with at least one Buy/Short cell |
| `all_windows_have_any_current_signal` | bool | renamed from `all_five_windows_currently_signaling`; every canonical window has ≥1 currently-signaling cell, regardless of K |

#### Same-K (strict) cross-window — NEW in amendment-1

| Field | Type | Notes |
|---|---|---|
| `k_builds_currently_signaling_all_windows` | list[int] | K values where the same K has `currently_signaling=True` in EVERY canonical window |
| `k_builds_all_members_aligned_all_windows` | list[int] | K values where the same K has `all_members_aligned=True` in every canonical window (strict subset of above) |
| `all_five_windows_same_k_currently_signaling` | bool | `len(k_builds_currently_signaling_all_windows) > 0` |
| `all_five_windows_same_k_all_members_aligned` | bool | `len(k_builds_all_members_aligned_all_windows) > 0` |
| `strongest_cross_window_k_build` | object / null | strongest K from `k_builds_currently_signaling_all_windows`, picked by descending `total_capture_pct` summed across the five windows; carries `K`, `total_capture_pct_sum`, `avg_sharpe_ratio` (None when undefined), `trigger_days_sum`, `buy_window_count`, `short_window_count`, `all_members_aligned_window_count`. Null when the same-K list is empty. |

#### Build-wide alignment & loose strongest cell

| Field | Type | Notes |
|---|---|---|
| `windows_with_all_members_firing` | list[str] | echoed from `build_wide_window_alignment` |
| `strongest_currently_signaling_cell` | object / null | (loose, any-K) Buy/Short cell with highest total_capture_pct — kept for the "show me the single hottest cell" UI element |

### 3.3 Phase 6I-36 ranking-table row compact summary

The reader/view's `ranking_table[i].current_signal_summary`
mirrors the package summary minus the heavy matrix, plus a
pre-formatted strongest-cell label for the renderer:

| Field | Source |
|---|---|
| `cells_currently_buy / _short / _none / _missing` | package summary |
| `cells_with_all_members_aligned` | package summary |
| `cells_historically_fired` | package summary (renamed in amendment-1) |
| `windows_with_any_currently_signaling` | package summary (loose any-K) |
| `all_windows_have_any_current_signal` | package summary (loose any-K, renamed) |
| `k_builds_currently_signaling_all_windows` | package summary (strict same-K) |
| `k_builds_all_members_aligned_all_windows` | package summary (strict same-K) |
| `all_five_windows_same_k_currently_signaling` | package summary (strict same-K) |
| `all_five_windows_same_k_all_members_aligned` | package summary (strict same-K) |
| `strongest_cross_window_k_build` | package summary (strict same-K) |
| `strongest_currently_signaling_cell_label` | `_format_strongest(window, K, capture, sharpe)` over `strongest_currently_signaling_cell` |

The full 60-cell matrix is exposed on
`ticker_cards[i].current_build_signals`; the renderer can
flatten / pivot / chart it.

## 4. How the fields are derived

All inputs come from the existing Phase 6I-23
`PerWindowKCell` (already validated by the Phase 6I-20
strict gate on the upstream side). For each canonical
cell on an eligible ticker the matrix builder emits a
record using:

- `latest_combined_signal`, `latest_buy_count`,
  `latest_short_count`, `latest_none_count`,
  `latest_missing_count`, `member_count` — copied through.
- `alignment_ratio`:
  - `latest_buy_count / member_count` when
    `latest_combined_signal == "Buy"`;
  - `latest_short_count / member_count` when
    `latest_combined_signal == "Short"`;
  - else `0.0`.
  - Returns `0.0` when `member_count <= 0` (no div-by-zero).
- `all_members_aligned`: `alignment_ratio == 1.0 AND
  member_count > 0 AND signal in {Buy, Short}`.
- `currently_signaling` (CURRENT):
  `latest_combined_signal in {Buy, Short}`.
- `currently_firing` (CURRENT, alias): same as
  `currently_signaling`, exposed under a second name so a
  UI built around "firing" terminology stays unambiguous.
- `historically_fired` (HISTORICAL): `trigger_days > 0`
  — the cell has fired at least once in the build's
  history. Renamed from `firing` in amendment-1.
- `total_capture_pct`, `avg_daily_capture_pct`,
  `sharpe_ratio`, `trigger_days`, `wins`, `losses` —
  copied through (None when undefined).

### Same-K cross-window predicates (amendment-1)

For each canonical K in `1..12`, the summary builder
indexes the matrix by `(K, window)` and checks every
canonical window:

- If the same K has `currently_signaling=True` in every
  one of the five canonical windows → K is added to
  `k_builds_currently_signaling_all_windows`.
- If the same K has `all_members_aligned=True` in every
  one of the five canonical windows → K is added to
  `k_builds_all_members_aligned_all_windows` (always a
  subset of the previous list since alignment implies
  signaling).
- `strongest_cross_window_k_build` selects the K from
  `k_builds_currently_signaling_all_windows` with the
  highest sum of `total_capture_pct` across the five
  windows (smaller K wins ties for determinism). It
  carries summary stats `total_capture_pct_sum`,
  `avg_sharpe_ratio`, `trigger_days_sum`,
  `buy_window_count`, `short_window_count`,
  `all_members_aligned_window_count`.

Non-canonical extras (e.g. K=13, window=6mo) are silently
skipped in the matrix builder.

## 5. TrafficFlow parity — honestly documented

The Phase 6I-23 multi-window K engine and legacy
TrafficFlow `compute_build_metrics_spymaster_parity` are
**not** the same K semantics:

| Surface | K means | How it computes per-build metrics |
|---|---|---|
| Legacy TrafficFlow `compute_build_metrics_spymaster_parity` | Subset SIZE | Averages metrics across **all non-empty subsets** of active members (`2^N - 1` subsets) per build |
| Phase 6I-23 multi-window K engine | Combine THRESHOLD (`n`-of-`N` agreement) | One cell per `(K, window)` with capture / Sharpe / trigger-days computed from the combined signal under that threshold |

The Phase 6I-37 `current_build_signals` matrix is a
**combine-threshold surface**, not a subset-average surface.
It answers the user-facing current-signal + per-cell-
historical-performance question, but it does **not**
reproduce legacy TrafficFlow's subset-averaging behavior.
This is now stated explicitly in
`_DEFAULT_REMAINING_LIMITATIONS` so the limitation appears
in every export package emitted from this contract. A future
scoring / parity phase may close the gap; until then the
website surface honestly carries combine-threshold K cells.

## 6. Strict validation preserved

Nothing about the strict Phase 6I-20 validator changed:

- Canonical 60-cell coverage is still required for
  eligibility.
- Duplicate canonical `(K, window)` cells still classify
  the artifact as `incomplete_60_cell_grid`.
- Non-canonical extras are tolerated only on top of
  complete canonical 60 (skipped silently in both the
  validator and the matrix builder).
- Chart-readiness still requires real chartable values,
  not dates alone.
- Partial / malformed payloads (e.g. missing
  `build_wide_window_alignment`) still classify as blocked
  WITHOUT a fabricated current-signal matrix or summary.

Two new regression tests pin this behavior on the new
surface: `test_duplicate_canonical_cells_still_block_eligibility`
and `test_missing_canonical_cell_still_blocks_eligibility`.

## 7. Multi-ticker / large-universe support

The matrix + summary are produced for any rank-eligible
ticker the export inspects — there is **no SPY-only path,
no single-ticker shortcut, no fixture that assumes SPY**.

`test_large_universe_fixture_does_not_assume_spy` builds a
six-ticker universe (`AAA / BBB / CCC / DDD / EEE / FFF`)
with no SPY ticker and verifies:

- 4 eligible tickers each carry a full 60-cell matrix;
- 2 blocked daily-only tickers carry empty matrix + null
  summary;
- ranking order is preserved;
- the reader/view exposes the data through
  `ranking_table` + `ticker_cards` end-to-end.

## 8. Tests (30 new + 100 existing focused tests; total 130)

Amendment-1 adds 7 new tests (24..30 below) and updates
several existing tests to assert the renamed fields.

The new test file is
`project/test_scripts/test_confluence_current_build_signal_surface.py`.

### 8.1 New tests (23)

| # | Name | Pins |
|---|---|---|
| 1 | `test_full_60_cell_payload_yields_60_row_matrix` | 60 entries in canonical `(window, K)` order |
| 2 | `test_matrix_row_carries_required_phase_6i37_fields` | Every required field present on every row |
| 3 | `test_all_buy_pattern_alignment_ratios_are_one` | All-Buy fixture → alignment_ratio=1.0, all_members_aligned, currently_signaling, firing |
| 4 | `test_all_none_pattern_no_currently_signaling_cells` | All-None fixture → no currently signaling, alignment=0 |
| 5 | `test_mixed_pattern_per_window_signal_distribution` | Per-window Buy/Short/None/partial distribution preserved |
| 6 | `test_zero_member_count_does_not_div_by_zero` | member_count=0 → alignment_ratio=0.0 |
| 7 | `test_matrix_skips_non_canonical_extras` | K=13 / window=6mo extras silently skipped |
| 8 | `test_summary_all_buy_pattern_counts_correctly` | Aggregate counts on all-Buy fixture |
| 9 | `test_summary_all_none_pattern_no_signaling_cells` | Aggregate on all-None fixture → no strongest cell |
| 10 | `test_summary_mixed_pattern_strongest_currently_signaling` | Strongest currently-signaling = highest capture Buy/Short cell |
| 11 | `test_rank_eligible_row_carries_current_build_signals` | Eligible PerTickerRankingRow carries matrix + summary |
| 12 | `test_blocked_daily_only_row_has_no_current_signals` | Blocked daily-only → empty matrix + null summary |
| 13 | `test_partial_payload_does_not_fabricate_current_signals` | Missing bwwa → blocked + no fabrication |
| 14 | `test_multi_ticker_export_with_two_eligible_and_one_blocked` | Multi-ticker ranking + mixed eligible/blocked |
| 15 | `test_package_eligible_ticker_detail_carries_full_matrix` | Phase 6I-35 package eligible detail carries 60 cells + summary |
| 16 | `test_package_blocked_ticker_detail_has_no_current_signals` | Phase 6I-35 package blocked detail → empty list + null |
| 17 | `test_view_model_ranking_table_row_carries_signal_summary` | Phase 6I-36 ranking_table row carries compact summary |
| 18 | `test_view_model_ticker_card_eligible_carries_full_matrix` | Phase 6I-36 ticker_cards eligible carries 60 cells |
| 19 | `test_view_model_blocked_card_has_no_current_signals` | Phase 6I-36 blocked card → empty list + null |
| 20 | `test_large_universe_fixture_does_not_assume_spy` | 6-ticker non-SPY fixture; 4 eligible + 2 blocked |
| 21 | `test_duplicate_canonical_cells_still_block_eligibility` | Duplicate canonical → blocked + no fabrication |
| 22 | `test_missing_canonical_cell_still_blocks_eligibility` | 59-cell grid → blocked + no fabrication |
| 23 | `test_cmre_no_forbidden_top_level_imports` | No yfinance / dash / live engines / writers / pipeline_runner |
| 24 | `test_amendment1_different_k_per_window_loose_true_strict_false` | Per-window Buy/Short on different K each → loose any-K=True, strict same-K=False, same-K lists empty |
| 25 | `test_amendment1_same_K6_all_windows_aligned` | K=6 Buy in every window with full alignment → strict same-K lists contain `[6]`, both same-K booleans True, `strongest_cross_window_k_build.K=6` |
| 26 | `test_amendment1_same_K6_signaling_but_partial_alignment` | K=6 Buy in every window with 2/3 alignment → signaling same-K=`[6]`, aligned same-K=`[]` |
| 27 | `test_amendment1_per_cell_naming_clarity` | Per-cell schema exposes `currently_signaling` + `currently_firing` + `historically_fired`; old `firing` key is gone |
| 28 | `test_amendment1_package_carries_same_K_fields_through` | Phase 6I-35 package's `ranking_rows[].current_build_signal_summary` AND `ticker_details[t].current_build_signal_summary` surface the new same-K fields |
| 29 | `test_amendment1_view_model_carries_same_K_fields_through` | Phase 6I-36 view model's compact `ranking_table[].current_signal_summary` AND `ticker_cards[].current_build_signal_summary` surface the new same-K fields (incl. `strongest_cross_window_k_build`) |
| 30 | `test_amendment1_blocked_ticker_still_has_null_summary_under_strict_predicate` | Blocked daily-only tickers carry empty matrix + null summary on both package and view-model surfaces; no fabricated same-K data |

### 8.2 Existing focused suite (100)

- `test_confluence_website_reader_view.py` — 30 tests
- `test_confluence_website_export_package.py` — 25 tests
- `test_confluence_multiwindow_ranking_export.py` — 36 tests
- `test_static_regression_guards.py` — 9 tests

## 9. Validation run

All from the pinned conda interpreter
(`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`):

- `py_compile project/confluence_multiwindow_ranking_export.py` → clean.
- `py_compile project/confluence_website_export_package.py` → clean.
- `py_compile project/confluence_website_reader_view.py` → clean.
- `pytest test_scripts/test_confluence_current_build_signal_surface.py -q` → **30 passed in 0.20s** (23 original + 7 amendment-1).
- `pytest test_scripts/test_confluence_current_build_signal_surface.py
  test_scripts/test_confluence_website_reader_view.py
  test_scripts/test_confluence_website_export_package.py
  test_scripts/test_confluence_multiwindow_ranking_export.py
  test_scripts/test_static_regression_guards.py -q` → **130 passed in 3.36s** (30 current-signal + 30 reader/view + 25 package + 36 ranking export + 9 static regression).
- `git diff --check` → clean.
- B12 raw-pickle regression guard still passes without a new allowlist entry.

## 10. Current production expected behavior

Production roots carry SPY + `_GSPC` Confluence artifacts.
Both lack the Phase 6I-20 multi-window fields, so both are
classified `data_status=daily_only` /
`ranking_blocked_reason=daily_only`. The Phase 6I-37 chain
will therefore produce:

- `eligible_count = 0`, `blocked_count = 2` (or whatever
  production carries today);
- Phase 6I-35 package: empty_state populated, blocked_rows
  carrying both tickers, **ticker_details for SPY and
  `_GSPC` both surface `current_build_signals=[]` and
  `current_build_signal_summary=null`** (no fabrication);
- Phase 6I-36 view model: `status_banner.kind=
  "no_eligible_production_blocked"`, `ranking_table=[]`,
  blocked_table with both tickers, ticker_cards with both
  tickers showing `current_build_signals=[]` and
  `current_build_signal_summary=null`.

When the SPY pilot eventually flips through refresh →
promote → Confluence-patch-write, the same chain produces
one rank-eligible card for SPY with a full 60-cell current-
signal matrix WITHOUT FURTHER CODE WORK.

## 11. SPY pilot status

**SPY remains PARKED until the Phase 6I-33 source-readiness
predicate flips.** No refresh / promotion / patch-writer
activity in this PR. The SPY pilot is a proof path, not the
launch destination.

## 12. No-production-activity confirmation

- No writer `--write` invocation (any writer).
- `PRJCT9_AUTOMATION_WRITE_AUTH` never read or set.
- No source refresh (`signal_engine_cache_refresher`).
- No `yfinance` fetch.
- No production promotion (`signal_library_stable_promotion_writer`).
- No Confluence patch writer (`multiwindow_k_confluence_patch_writer`).
- No `confluence_pipeline_runner` invocation.
- No StackBuilder / OnePass / ImpactSearch / TrafficFlow /
  Spymaster batch execution.
- No production data write.
- Production `signal_library/data/stable/` untouched.
- Production `output/research_artifacts/` untouched.
