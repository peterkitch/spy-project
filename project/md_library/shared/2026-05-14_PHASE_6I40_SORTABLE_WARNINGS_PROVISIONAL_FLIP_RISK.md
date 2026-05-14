# Phase 6I-40: sortable leaderboard + incomplete-member warnings + provisional current-signal status + flip-risk placeholders

**Branch:** `phase-6i-40-sortable-leaderboard-warnings-and-provisional-signal`

## 1. Goals (four read-only contract additions)

Phase 6I-40 layers four product-facing additions on top of
the merged Phase 6I-37 / 6I-39 chain. Each is wired
end-to-end across the Phase 6I-34 ranking export → Phase
6I-35 website package → Phase 6I-36 reader/view, with
injection seams so production paths stay strictly
read-only and conservative.

1. **Sortable leaderboard contract** — `sortable_columns`,
   `default_sort`, `sort_options` at top level; per-row
   numeric `row_sort_values` (`total_capture_pct_sort`,
   `sharpe_ratio_sort`, `trigger_days_sort`, `rank_sort`,
   `ticker_sort`). Mirrors TrafficFlow's default sort
   (`Sharpe desc, Total % desc, Trigs desc`). Both
   directions exposed so the renderer can bring bottom /
   negative / short-candidate rows to the top WITHOUT
   duplicating ticker rows.
2. **Incomplete-member warning surface** —
   `data_completeness` block (`has_incomplete_build_members`,
   `incomplete_member_count`, `incomplete_members`,
   `incomplete_member_reasons`, `data_warning_symbol`,
   `data_completeness_status`, `data_completeness_message`)
   propagated per-row + a top-level
   `data_completeness_summary` panel on the view model
   (TrafficFlow's "missing/stale PKL summary panel"
   analog).
3. **Provisional current-signal status surface** —
   `current_signal_status_block` (`current_signal_status` ∈
   `{locked, provisional, stale, blocked, unknown}`,
   `current_signal_as_of`, `latest_price`,
   `latest_price_as_of`, `uses_provisional_price`,
   `signal_update_source` ∈ `{artifact,
   live_price_overlay, unavailable}`). Injection seam
   `live_price_provider_callable`; default production
   path performs NO live fetch.
4. **Spymaster-style flip-risk placeholders** —
   `flip_risk` block (`flip_risk_available`,
   `flip_risk_label` ∈ `{null, Low, Medium, High,
   Critical}`, `nearest_flip_price`, `nearest_flip_pct`,
   `flip_to_signal`). Injection seam
   `flip_risk_provider_callable`; default returns null /
   false placeholder block.

## 2. TrafficFlow / Spymaster reference points

### TrafficFlow sorting

`trafficflow.py` lines 3111-3112:

```python
sort_action="native",
sort_by=[
    {"column_id": "Sharpe", "direction": "desc"},
    {"column_id": "Total %", "direction": "desc"},
    {"column_id": "Trigs",  "direction": "desc"},
],
```

This is the Phase 6I-40 `DEFAULT_SORT` constant. The
website board's renderer can sort natively against the
per-row numeric `row_sort_values` block AND flip direction
ascending vs descending without duplicating ticker rows.

### TrafficFlow incomplete-member scan

`trafficflow.py` references:

- Line 2906 — `compute_build_metrics_spymaster_parity`
  pre-scan of missing/stale PKLs.
- Line 2935 — `scan_all_secondaries_and_all_rows` for
  missing/stale PKLs (quiet mode).
- Line 3023 / 3031 — per-row check: "if any member in
  this build has missing/stale PKL, add warning icon".
- Line 3346 — `Missing/Stale PKLs (ALL K, {count}):
  {summary} | {sample}` panel.

Phase 6I-40's `data_completeness` block adopts the same
product behavior: incomplete members are surfaced via
stable fields, NOT silently dropped. The top-level
`data_completeness_summary` panel on the view model is
the TrafficFlow "missing/stale PKL summary panel" analog.

### Spymaster price-range / flip-risk

`spymaster.py` carries price-threshold + range logic that
maps a current price to Buy / Short / Cash and computes
proximity to the flip threshold. The Phase 6I-23
multi-window K engine + Phase 6I-20 Confluence artifact
do NOT carry that range data on the current production
surface. Phase 6I-40 adds null/false placeholder fields
so a future phase can wire real flip-risk values without
a schema change. The `flip_risk_provider_callable`
injection seam is read-only; the default production
behavior surfaces only placeholders.

## 3. Provider injection seams (read-only)

All three providers are optional kwargs on
`build_multiwindow_ranking_export()`:

| Provider kwarg | Default | Test seam |
|---|---|---|
| `member_completeness_provider_callable` | `_default_member_completeness_provider` returns `has_incomplete_build_members=False` with empty lists (honest about upstream Phase 6I-20 gap — production artifacts don't yet carry member-level issue data). | Tests pass a fake provider returning incomplete-member dicts. |
| `live_price_provider_callable` | `None`. Eligible rows surface `current_signal_status="locked"`, `signal_update_source="artifact"`, no live fetch. | Tests pass a fake provider returning `{latest_price, latest_price_as_of, uses_provisional_price, [current_signal_status]}`. |
| `flip_risk_provider_callable` | `None`. Rows surface null / false placeholder block. | Tests pass a fake provider returning `{flip_risk_available, flip_risk_label, nearest_flip_price, nearest_flip_pct, flip_to_signal}`. |

All three providers are guarded against exceptions: a
provider raising at runtime falls back to the
conservative default block without crashing the row
build (verified by
`test_provider_raising_exception_falls_back_to_default`).

## 4. Schema fields added

### 4.1 Top-level on ranking export `summary` + package envelope + view model

- `sortable_columns: list[str]` — `["total_capture_pct",
  "sharpe_ratio", "trigger_days", "rank", "ticker"]`.
- `default_sort: list[{column_id, direction}]` — mirrors
  TrafficFlow.
- `sort_options: list[{column_id, label,
  row_sort_value_key, directions, value_type}]` — each
  option exposes both `asc` and `desc` directions.

### 4.2 Per-row blocks (`PerTickerRankingRow` + ranking_row / blocked_row in package + ranking_table / blocked_table in view model)

#### `row_sort_values`

| Field | Type |
|---|---|
| `total_capture_pct_sort` | float / null |
| `sharpe_ratio_sort` | float / null |
| `trigger_days_sort` | int |
| `rank_sort` | int / null (ranking export emits null; package fills in) |
| `ticker_sort` | str (upper-case) |

#### `data_completeness`

| Field | Type | Notes |
|---|---|---|
| `has_incomplete_build_members` | bool | |
| `incomplete_member_count` | int | |
| `incomplete_members` | list[str] | |
| `incomplete_member_reasons` | dict[str, str] | |
| `data_warning_symbol` | str / null | `"!"` when partial or blocked, else null |
| `data_completeness_status` | str | `complete` / `partial` / `blocked` / `unknown` |
| `data_completeness_message` | str | short stable string |

Selection rule:

- `complete` — `rank_eligible=True AND` no incomplete members.
- `partial` — `rank_eligible=True AND` at least one incomplete member.
- `blocked` — `rank_eligible=False`. **Blocked rows do NOT receive fabricated incomplete-member fields.**
- `unknown` — defensive catch-all.

#### `current_signal_status_block`

| Field | Type | Notes |
|---|---|---|
| `current_signal_status` | str | One of `locked / provisional / stale / blocked / unknown`. |
| `current_signal_as_of` | str / null | `confluence_last_date` for eligible rows; null on blocked. |
| `latest_price` | float / null | |
| `latest_price_as_of` | str / null | |
| `uses_provisional_price` | bool | |
| `signal_update_source` | str | `artifact` / `live_price_overlay` / `unavailable`. |

Default: eligible → `locked` / `artifact` / no price. Blocked → `blocked` / `unavailable`. Live overlay → `provisional` / `live_price_overlay`.

#### `flip_risk`

| Field | Type | Notes |
|---|---|---|
| `flip_risk_available` | bool | False by default |
| `flip_risk_label` | str / null | `null / Low / Medium / High / Critical` (unsanctioned labels normalized to `null`) |
| `nearest_flip_price` | float / null | |
| `nearest_flip_pct` | float / null | |
| `flip_to_signal` | str / null | `Buy` / `Short` / `None` / null |

Default: all null / false placeholders.

### 4.3 View-model top-level (Phase 6I-36)

- `sortable_columns / default_sort / sort_options` — pass through.
- `data_completeness_summary` — aggregated across rows:
  - `tickers_with_incomplete_members` (int)
  - `ticker_list` (list[str])
  - `by_data_completeness_status` (`{complete, partial, blocked, unknown}` counts).
- Error view model also advertises empty `sortable_columns / default_sort / sort_options / data_completeness_summary` so the renderer never sees these keys missing.

## 5. Strict no-write contract preserved

- Default `live_price_provider_callable=None` — production path performs NO `yfinance` fetch.
- Default `flip_risk_provider_callable=None` — no Spymaster invocation.
- Default `member_completeness_provider_callable` is the local read-only helper returning the honest `False` baseline.
- No top-level imports added that would violate the existing static regression guards (`yfinance` / `dash` / `subprocess` / live engine modules / writers / pipeline runner all still excluded — verified by the existing
  `test_module_no_forbidden_top_level_imports` tests on each module + the `test_static_regression_guards.py` sweep).

## 6. One-row-per-ticker invariant preserved

`test_one_ticker_remains_one_row_after_phase_6i40`:
five-ticker fixture with active live-price provider AND
flip-risk provider populated → 5 ranking rows, 5 view-model
rows, 5 ticker cards, each ticker appears exactly once.
`len(tickers) == len(set(tickers))` invariant pinned on
every surface.

## 7. Tests (22 new + 148 existing focused tests = 170 total)

New file:
`project/test_scripts/test_confluence_sortable_warnings_and_provisional_signal.py`.

| # | Name | Pins |
|---|---|---|
| 1 | `test_sortable_columns_default_sort_and_options_pinned` | Top-level sort metadata mirrors TrafficFlow:3111-3112; both directions exposed. |
| 2 | `test_package_and_view_model_expose_sort_metadata` | Sort metadata passes through ranking export → package → view model; error view model carries empty placeholders. |
| 3 | `test_row_sort_values_are_numeric_and_complete` | Per-row sort values stable; ranking export emits `rank_sort=None`. |
| 4 | `test_package_assigns_rank_sort_per_row` | Package fills `rank_sort` per the assigned rank. |
| 5 | `test_descending_sort_on_capture_brings_top_row_to_top` | Both `desc` and `asc` orderings work on `total_capture_pct_sort`. |
| 6 | `test_sort_does_not_duplicate_ticker_rows` | Ranking table has unique tickers (no per-K explosion). |
| 7 | `test_default_completeness_is_complete_when_eligible` | Default provider → `status=complete`, no warning symbol. |
| 8 | `test_provider_supplies_incomplete_members_propagates` | Fake TEF-style provider → `partial`, warning symbol, `incomplete_members=["TEF"]`, propagates through package and view model + `data_completeness_summary` counts. |
| 9 | `test_blocked_ticker_completeness_status_is_blocked` | Blocked rows → `status=blocked`, no fabricated incomplete-member fields, warning symbol set. |
| 10 | `test_blocked_table_row_carries_completeness_pass_through` | View model's `blocked_table` row and `ticker_cards` carry the completeness block. |
| 11 | `test_default_eligible_signal_status_is_locked` | No live provider → `locked` / `artifact` / no price. |
| 12 | `test_blocked_signal_status_is_blocked_with_unavailable_source` | Blocked row → `blocked` / `unavailable`. |
| 13 | `test_fake_live_provider_flips_status_to_provisional` | Fake provider with `uses_provisional_price=True` → `provisional` / `live_price_overlay` / populated price. |
| 14 | `test_live_provider_can_mark_stale` | Provider returning `current_signal_status="stale"` propagates. |
| 15 | `test_provisional_propagates_through_package_and_view` | Provisional state passes through all three layers. |
| 16 | `test_default_flip_risk_block_is_null_placeholder` | Default → null / false placeholders. |
| 17 | `test_fake_flip_risk_provider_populates_fields` | Fake provider → populated fields. |
| 18 | `test_flip_risk_rejects_unknown_label` | Unsanctioned label normalized to null (no fabrication). |
| 19 | `test_flip_risk_blocked_row_stays_null` | Blocked rows ignore the flip-risk provider. |
| 20 | `test_one_ticker_remains_one_row_after_phase_6i40` | Multi-ticker invariant holds with all new providers populated. |
| 21 | `test_large_universe_fixture_no_spy_with_mixed_states` | 6-ticker non-SPY universe (3 complete eligible + 2 partial eligible + 1 blocked); sort metadata propagates; `data_completeness_summary` correctly aggregates; ascending vs descending sort inverts leader / laggard. |
| 22 | `test_provider_raising_exception_falls_back_to_default` | All three providers raising at runtime → row builder still constructs cleanly with defaults. |

## 8. Validation run

Pinned conda interpreter
(`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`):

- `py_compile` on all three modified modules: clean.
- `pytest test_scripts/test_confluence_sortable_warnings_and_provisional_signal.py -q` → **22 passed in 0.20s**.
- `pytest <full focused suite> -q` → **170 passed in 4.70s** (22 new + 18 primary-build + 30 current-signal + 30 reader/view + 25 package + 36 ranking export + 9 static regression).
- `git diff --check`: clean.
- B12 raw-pickle static regression guard still passes without a new allowlist entry.

## 9. Honest upstream gaps documented

- **Production Confluence artifacts do NOT yet carry member-level issue details** (which members are missing or stale). The default `_default_member_completeness_provider` honestly reports `has_incomplete_build_members=False` for every ticker today. A future phase that wires the StackBuilder / TrafficFlow PKL scan into the artifact (or into the export builder) will flip this to True for affected rows without a schema change.
- **No live-price feed is wired in production.** The `live_price_provider_callable` injection seam exists for tests and for a future live-overlay phase; production runs return `current_signal_status="locked"` (artifact-derived) for every eligible row.
- **No Spymaster-style price-range / flip-threshold data flows through the Phase 6I-23 multi-window K engine + Phase 6I-20 Confluence artifact yet.** The `flip_risk` block is null / false placeholders. A future scoring / parity phase that wires Spymaster range logic to the cross-window surface would populate these fields.

## 10. SPY pilot status

Unchanged. SPY remains **PARKED** awaiting either:

- the Phase 6I-38 equal-cutoff readiness policy decision +
  independent TEF triage, OR
- the next trading-day rollover + yfinance publication for
  the 14 non-TEF SPY-K-universe tickers + TEF triage.

No source refresh / no production write of any kind was
performed in this PR.

## 11. No-production-activity confirmation

- No writer `--write` invocation (any writer).
- `PRJCT9_AUTOMATION_WRITE_AUTH` never read or set.
- No source refresh (`signal_engine_cache_refresher`).
- No `yfinance` fetch in production mode (default
  `live_price_provider_callable=None`; tests use fake
  providers only).
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
