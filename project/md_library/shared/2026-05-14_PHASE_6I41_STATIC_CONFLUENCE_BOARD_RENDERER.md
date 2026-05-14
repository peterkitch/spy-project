# Phase 6I-41: static Confluence website board renderer / UI shell

**Branch:** `phase-6i-41-static-confluence-board-renderer`

## 1. What this phase renders

A self-contained read-only HTML document that consumes the
Phase 6I-36 view model (or a Phase 6I-35 package via the
reader/view builder) and emits a dense operational
ranking-board UI shell. New module:
`project/confluence_static_board_renderer.py`.

The rendered document has:

- **Header / status banner** driven by the view model
  (`status_banner.kind` + `headline` + `body` + summary
  strip with eligible / blocked / inspected / generated_at /
  rendered_at / schema_version / view_model_version /
  display_row_cardinality).
- **Sort controls** wired to the Phase 6I-40 sort metadata.
  Default sort mirrors `trafficflow.py:3111-3112`:
  Sharpe desc → Total Capture % desc → Trigger Days desc.
  Both **ascending** and **descending** are selectable in
  the same control, so the renderer can bring bottom /
  negative / short-candidate rows to the top WITHOUT
  duplicating ticker rows.
- **Filter controls**: ticker search (text), signal-status
  dropdown, completeness dropdown.
- **Data-completeness summary panel** (TrafficFlow
  `:3346` analog) summarizing tickers with incomplete
  members + breakdown by completeness status.
- **Ranking table** — exactly **one row per ticker**
  (Phase 6I-39 invariant pinned). Columns:
  Rank · Ticker · Primary Build · Direction · Windows ·
  Same-K Status · Total Capture % · Sharpe · Trigger Days ·
  Current Status · Last Price · Warning · Chart · Freshness.
- **Blocked table** — one line per blocked ticker
  (Ticker · Reason · Data Status · Freshness · Chart
  Status · Warning · Issues).
- **Ticker detail panel** revealed when a ranking or
  blocked row is clicked. Renders the full
  `primary_build_summary`, `current_build_signal_summary`,
  the 60-cell `current_build_signals` matrix in a 12×5
  grid, `data_completeness`, `current_signal_status_block`,
  `flip_risk` placeholder block, and a chart-readiness
  panel (**no fabricated charts** — when `chart_rows`
  aren't embedded the panel shows source/blocker fields
  and a clean placeholder).
- **Empty state** rendered honestly when `eligible_count==0`
  using the view model's `empty_state` block (NO fake
  rows).
- **Remaining-limitations** footer list pass-through.

## 2. What this phase does NOT do

- **NO writer / refresher / pipeline runner / batch engine
  invocation.** Module imports are limited to the reader/
  view (which is itself strictly read-only).
- **NO chart drawing.** When the view model does not embed
  chart rows, the detail panel surfaces a clean
  placeholder + the source/blocker fields, never a
  fabricated chart.
- **NO production write.** The `--output` flag is guarded:
  any path containing a known production-root segment
  (`cache/results`, `cache/status`,
  `output/research_artifacts`, `output/stackbuilder`,
  `signal_library/data/stable`) is refused with
  `ValueError` BEFORE any file open occurs. The default
  CLI path emits HTML to stdout only.
- **NO external CDN required.** All CSS + JS is inline.
- **NO source refresh / yfinance fetch / Confluence patch
  writer / confluence_pipeline_runner / StackBuilder /
  OnePass / ImpactSearch / TrafficFlow / Spymaster batch
  execution.** All forbidden top-level imports verified by
  `test_module_no_forbidden_top_level_imports`.

## 3. Current production expectation

Production Confluence artifacts (SPY + `_GSPC`) still do
not carry the Phase 6I-20 multi-window fields. When the
renderer is pointed at the production view model today:

- `eligible_count == 0` → the **empty-state section**
  renders honestly with the production-blocked headline
  + sample blockers (the renderer never fabricates
  ranking rows).
- The **blocked table** renders both production tickers
  with `data_warning_symbol="!"` and the "blocked: ..."
  message.
- The **ticker detail panel** for each blocked ticker
  shows `data_completeness_status="blocked"`,
  `current_signal_status="blocked"`,
  `signal_update_source="unavailable"`, empty 60-cell
  matrix, null flip-risk placeholders.

When the SPY pilot eventually flips through refresh →
promote → Confluence-patch-write, the SAME renderer will
emit the eligible ranking row and full ticker detail
WITHOUT FURTHER CODE WORK.

## 4. One-row-per-ticker invariant preserved

Two tests pin the invariant on the rendered HTML:

- `test_renders_eligible_rows_one_row_per_ticker`: three
  eligible tickers → exactly three
  `<tr class="ranking-row">` opens; each ticker matches
  exactly once via the `data-ticker` attribute.
- `test_renders_blocked_rows_one_line_per_ticker`: two
  blocked tickers → exactly two
  `<tr class="blocked-row">` opens.

The renderer NEVER explodes a ticker by K or window.
Multiple active K builds surface through the primary-
build compact label + `other_active_k_count` on the
SAME single row, with the full `other_active_k_builds`
list in the ticker detail panel.

## 5. How sorting / warnings / current-status / flip-risk appear

### 5.1 Sort metadata

- Top-level select element ``<select id="sort-column">``
  lists every column from the view model's
  `sort_options`. Default selection is `sharpe_ratio`.
- Direction select element ``<select id="sort-direction">``
  defaults to `desc`. Both directions selectable.
- Each ranking row carries
  `data-sort-rank`, `data-sort-capture`,
  `data-sort-sharpe`, `data-sort-trigger`,
  `data-sort-ticker` attributes — sourced directly from
  the Phase 6I-40 `row_sort_values` block.
- Inline JS reads the active column + direction and
  reorders the rows in place. Null sort values sink to
  the bottom (renderer convention; the view model is
  null-safe).

### 5.2 Incomplete-member warning

- The `Warning` column on each ranking row + blocked row
  renders a `<span class="warning warning-on" title="..."
  data-status="...">!</span>` when
  `data_completeness.data_warning_symbol == "!"` (either
  `partial` or `blocked`).
- The TrafficFlow `:3346` analog
  `data_completeness_summary` panel renders above the
  ranking table with the breakdown.
- The detail panel surfaces the full `data_completeness`
  block (`incomplete_members`, `incomplete_member_reasons`,
  status, message).

### 5.3 Current signal status

- The `Current Status` column renders a CSS-classed
  badge: `status-locked` / `status-provisional` /
  `status-stale` / `status-blocked` / `status-unknown`.
- The `Last Price` column renders the latest_price when
  `uses_provisional_price=True` (with the `provisional`
  CSS class) OR null/em-dash placeholder when the live
  overlay is unavailable.
- The detail panel surfaces the full
  `current_signal_status_block` (`current_signal_as_of`,
  `latest_price`, `latest_price_as_of`,
  `uses_provisional_price`, `signal_update_source`).

### 5.4 Flip-risk placeholder

- No column on the ranking row by default (flip-risk is
  not yet a per-row leading column).
- The detail panel renders the `flip_risk` block. When
  `flip_risk_available=False`, an explicit "Placeholder
  block. Real Spymaster flip-risk wiring is still future
  work" message renders. When a future phase wires real
  values through the provider, the placeholder switches
  to a key/value table without a schema change.

## 6. HTML escaping / XSS hardening

- All view-model text is escaped via `html.escape(..., quote=True)` before being rendered into element
  text or attribute values.
- The inlined detail JSON is serialized via
  `_json_for_html`, which escapes every `<` as the JSON
  unicode escape `<`. This makes any provider-
  supplied content with a `<script>` substring
  impossible to break out of the `<script>` body —
  there are literally NO `<` characters inside the JSON
  block. The browser's JSON parser turns `<` back
  into `<` on read.
- Two explicit XSS tests pin both layers:
  `test_html_escaping_prevents_ticker_injection` (malicious
  ticker name with `<script>alert(...)</script>`) and
  `test_html_escaping_inside_detail_json_handles_close_tag`
  (ticker name with `</script>` substring).

## 7. CLI

```
python confluence_static_board_renderer.py \
    [--view-model PATH | --package PATH | --stdin] \
    [--output PATH]
```

- `--view-model` reads a Phase 6I-36 view model JSON.
- `--package` reads a Phase 6I-35 package JSON and
  invokes the Phase 6I-36 reader/view builder in-process.
- `--stdin` reads a Phase 6I-36 view model JSON from stdin.
- `--output` writes the HTML to a file. **Refused** if
  the resolved path contains any of the five known
  production-root segments. Default = stdout.

CLI rc codes:

- rc=0: happy path.
- rc=2: missing source flag / production-root `--output` /
  invalid input.
- rc=3: JSON decode error / file not found.

## 8. Tests (32 new + 170 existing focused tests = 202 total)

New file:
`project/test_scripts/test_confluence_static_board_renderer.py`.

| # | Name | Pins |
|---|---|---|
| 1 | `test_renders_eligible_rows_one_row_per_ticker` | 3 tickers → 3 ranking rows; each ticker appears exactly once. |
| 2 | `test_renders_blocked_rows_one_line_per_ticker` | 2 blocked tickers → 2 blocked rows. |
| 3 | `test_sort_controls_expose_all_required_columns` | Sort-column select carries Total Capture %, Sharpe Ratio, Trigger Days, Rank, Ticker; direction select has asc+desc. |
| 4 | `test_default_sort_is_sharpe_desc` | Default selection is Sharpe / desc. |
| 5 | `test_row_sort_values_embedded_as_data_attributes` | Every row has `data-sort-rank/capture/sharpe/trigger/ticker` attributes. |
| 6 | `test_partial_data_completeness_renders_warning_symbol` | Partial row → `<span class="warning warning-on" ...>!</span>` with `data-status="partial"`. |
| 7 | `test_blocked_row_warning_renders_with_blocker_message` | Blocked row warning span carries `data-status="blocked"`. |
| 8 | `test_current_signal_status_locked_renders` | Locked badge CSS class. |
| 9 | `test_current_signal_status_provisional_renders` | Provisional badge + provisional latest-price CSS class + the price text. |
| 10 | `test_current_signal_status_stale_renders` | Stale badge CSS class. |
| 11 | `test_primary_build_label_renders` | Pre-formatted label renders; tier data-attr present. |
| 12 | `test_same_k_vs_single_cell_render_distinctly` | Two different tiers → two different CSS classes + tier data-attrs. |
| 13 | `test_same_k_mixed_renders_with_conflict_marker` | Same-K mixed tier → `primary-build-conflict` CSS class + `data-direction-conflict="true"`. |
| 14 | `test_current_build_signals_matrix_embedded_in_detail_json` | Inlined detail JSON carries 60-cell matrix + primary_build_summary + data_completeness + current_signal_status_block + flip_risk per ticker. |
| 15 | `test_no_eligible_rows_renders_empty_state_not_fake_rows` | eligible_count=0 → empty-state section, 0 ranking rows, 2 blocked rows. |
| 16 | `test_html_escaping_prevents_ticker_injection` | Malicious `<script>alert(...)</script>` payload does NOT survive as raw HTML. |
| 17 | `test_html_escaping_inside_detail_json_handles_close_tag` | Detail JSON body has no raw `<` characters; ticker named `AB</script>CD` appears as `<\/script>` inside the JSON. |
| 18 | `test_cli_stdout_path_emits_html` | rc=0; stdout starts with `<!DOCTYPE html>`. |
| 19 | `test_cli_missing_source_returns_rc_2` | No source flag → rc=2. |
| 20 | `test_cli_unreadable_json_returns_rc_3` | Invalid JSON → rc=3. |
| 21 | `test_cli_output_to_tmp_path_writes_html` | `--output` writes to tmp_path. |
| 22 | `test_cli_output_under_production_root_is_refused` | `--output` to any of the 5 production-root segments → rc=2; direct helper raises ValueError on absolute / Windows / forward-slash variants. |
| 23 | `test_schema_error_view_model_renders_error_shell` | schema_error view model → error shell; no ranking/blocked `<tr>` elements. |
| 24 | `test_non_mapping_input_renders_error_shell` | Non-mapping input → error shell with `non_mapping_view_model`. |
| 25 | `test_cli_stdin_path_works` | `--stdin` → HTML on stdout. |
| 26 | `test_cli_package_path_uses_reader_view_builder` | `--package` invokes Phase 6I-36 reader/view in-process. |
| 27 | `test_module_no_forbidden_top_level_imports` | No yfinance / dash / live engine / writer / pipeline_runner imports. |
| 28 | `test_module_no_raw_pickle_load` | No raw `pickle.load`. |
| 29 | `test_module_no_resample_or_ffill_calls` | AST-scan: no `.resample()` / `.ffill()` call nodes. |
| 30 | `test_module_no_write_true_kwarg` | No `write=True` kwarg anywhere. |
| 31 | `test_minimal_view_model_does_not_crash` | View model with only schema_version + banner does not crash. |
| 32 | `test_html_is_self_contained_no_external_cdn` | No `<script src=...>` or `<link rel="stylesheet"...>` — CSS + JS are all inline. |

## 9. Validation run

Pinned conda interpreter
(`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`):

- `py_compile project/confluence_static_board_renderer.py`
  → clean.
- `py_compile project/test_scripts/test_confluence_static_board_renderer.py`
  → clean.
- `pytest test_scripts/test_confluence_static_board_renderer.py -q`
  → **32 passed in 0.26s**.
- `pytest <full focused suite> -q` → **202 passed in
  4.02s** (32 new + 22 Phase 6I-40 + 18 primary-build +
  30 current-signal + 30 reader/view + 25 package + 36
  ranking export + 9 static regression).
- `git diff --check` → clean.
- B12 raw-pickle regression guard still passes without an
  allowlist entry.

## 10. SPY pilot status

Unchanged. SPY remains **PARKED** awaiting:

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
- The renderer's CLI `--output` is hardened against
  writing under any of the five known production-root
  segments (verified by
  `test_cli_output_under_production_root_is_refused`).
