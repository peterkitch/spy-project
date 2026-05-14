# Phase 6I-36: Confluence website reader/view layer

**Branch:** `phase-6i-36-confluence-website-reader-view`

## 1. Purpose

Phase 6I-35 closed the **data contract** for the
website-facing Confluence multi-ticker ranking/export
package (`schema_version="confluence_website_export_v1"`).
Phase 6I-36 closes the **read-only reader/view layer** that
consumes that package and produces a flat *view model*
shaped for a future website renderer (static HTML,
server-side template, Dash component tree, or static
single-page app).

This phase is **not the website UI**. It is the read-only
contract between the Phase 6I-35 package and the eventual
renderer. The view model has stable keys and four
mutually-exclusive `status_banner.kind` values. The
renderer can branch on `status_banner.kind` and consume
`ranking_table` / `blocked_table` / `ticker_cards` directly
as the rendering data source.

## 2. What this phase DOES

- Adds `project/confluence_website_reader_view.py`, a
  strictly read-only module that:
  - **Loads** a Phase 6I-35 package from one of three
    sources:
    1. a supplied JSON path (`--package`);
    2. stdin (`--stdin`);
    3. an on-the-fly invocation of the Phase 6I-35
       builder (`--tickers` / `--all-artifacts` /
       `--from-stackbuilder-universe`).
  - **Validates** `schema_version`. Accepts only
    `confluence_website_export_v1`. Missing /
    non-matching / non-mapping packages produce a
    structured **error view model** with
    `status_banner.kind == "schema_error"` and a stable
    `error_code` field
    (`package_unreadable` / `schema_version_missing` /
    `schema_version_mismatch`).
  - **Transforms** the package into a flat view model
    with stable keys (see § 3).
  - **Emits** the view model JSON to stdout via the CLI.

- Adds
  `project/test_scripts/test_confluence_website_reader_view.py`
  with 30 pinned tests covering every required scenario
  (see § 4).

## 3. View model schema

### 3.1 Top-level (success path)

| Field | Type | Notes |
|---|---|---|
| `schema_version` | str | Always `confluence_website_export_v1`. |
| `view_model_version` | str | `confluence_website_reader_view_v1`. |
| `generated_at` | str / null | Pass-through from the package. |
| `rendered_at` | str | Reader-side ISO-8601 UTC timestamp. |
| `page_title` | str | `"Confluence Multi-Ticker Ranking Board"`. |
| `has_eligible_rankings` | bool | True iff `eligible_count > 0`. |
| `eligible_count` | int | Pass-through. |
| `blocked_count` | int | Pass-through. |
| `inspected_count` | int | Pass-through. |
| `empty_state` | object / null | Pass-through from the package. |
| `ranking_table` | list[row] | One row per Phase 6I-35 normalized ranking row. |
| `blocked_table` | list[row] | One row per Phase 6I-35 blocked row. |
| `ticker_cards` | list[card] | One card per `ticker_details` entry (sorted by ticker). |
| `chart_readiness_summary` | object / null | Pass-through. |
| `freshness_summary` | object / null | Pass-through. |
| `issue_summary` | object / null | Pass-through. |
| `status_banner` | object | `{kind, headline, body}`. |
| `remaining_limitations` | list[str] | Pass-through. |

### 3.2 `ranking_table` row

| Field | Type | Notes |
|---|---|---|
| `rank` | int | From normalized ranking row. |
| `ticker` | str | Falls back to `"unknown"` if absent. |
| `direction` | str | Falls back to `"unknown"`. |
| `windows` | str | `"firing/total"`, e.g. `"5/5"`; `"unknown"` if either side is null. |
| `k_cells` | str | `"firing/total"`, e.g. `"60/60"`. |
| `strongest` | str / null | `"<window> K=<K> (cap <X.YZ%>, Sharpe <X.YZ>)"`. |
| `capture` | str / null | `total_capture_pct_sum` formatted as `<X.YZ%>`. |
| `sharpe` | str / null | `avg_sharpe_ratio` formatted as `<X.YZ>`. |
| `trigger_days` | int | From `trigger_days_sum`. |
| `chart_ready` | bool | `chart_ready_available` straight through. |
| `freshness` | str | `freshness_status` or `"unknown"`. |
| `issues` | list[str] | `issue_codes` pass-through. |

### 3.3 `blocked_table` row

| Field | Type | Notes |
|---|---|---|
| `ticker` | str | `"unknown"` fallback. |
| `reason` | str | `ranking_blocked_reason`; `"unknown_blocker"` fallback. |
| `data_status` | str | `"unknown"` fallback. |
| `freshness` | str | `"unknown"` fallback. |
| `chart_status` | str | `"ready"` if `chart_ready_available`; else `chart_blocker` (fallback `"unavailable"`). |
| `issues` | list[str] | `issue_codes` pass-through. |

### 3.4 `ticker_cards` entry

| Field | Type | Notes |
|---|---|---|
| `ticker` | str | Card key from `ticker_details`. |
| `rank_eligible` | bool | Pass-through. |
| `detail_available` | bool | Pass-through from Phase 6I-35 amendment-1 semantics: "the website has a path to detail". |
| `detail_source` | str / null | `full_60_cell_detail_source` (the artifact path the renderer can fetch full detail from). |
| `detail_blocker` | str / null | Phase 6I-35 `detail_blocker` (e.g. `daily_only` / `no_phase_6i20_payload`). |
| `summary` | object / null | `per_window_summary` block when present; null when the underlying row is blocked / has no firing summary. |
| `all_members_firing_windows` | list[str] | Summary list pass-through. |
| `chart_ready_available` | bool | Pass-through. |
| `chart_ready_source` | str / null | Pass-through. |
| `chart_row_count` | int / null | Pass-through. |
| `chart_blocker` | str / null | Pass-through. |
| `freshness_status` | str / null | Pass-through. |
| `data_status` | str / null | Pass-through. |
| `issue_codes` | list[str] | Pass-through. |
| `blocker_text` | str / null | `ranking_blocked_reason` (or `detail_blocker` fallback) for blocked tickers; null for eligible. |

### 3.5 `status_banner.kind` taxonomy (four mutually-exclusive values)

| Kind | Trigger | Headline |
|---|---|---|
| `has_eligible_rankings` | `eligible_count > 0` | "Eligible Confluence rankings available." |
| `no_eligible_production_blocked` | `eligible_count == 0` AND `inspected_count > 0` | "No tickers are rank-eligible yet." |
| `no_tickers_inspected` | `inspected_count == 0` | "No tickers inspected." |
| `schema_error` | Package unreadable / missing / wrong `schema_version` | "Confluence export package was unreadable." |

### 3.6 Error view model

When the package fails validation, the reader returns an
error view model with the same top-level shape but:

- `schema_version = null`;
- `schema_version_seen = <whatever was in the package>` (or
  null when missing entirely / non-mapping);
- `ranking_table = []`, `blocked_table = []`,
  `ticker_cards = []`;
- `chart_readiness_summary`, `freshness_summary`,
  `issue_summary` all null;
- `status_banner.kind = "schema_error"`;
- `status_banner.error_code` ∈
  `{package_unreadable, schema_version_missing,
  schema_version_mismatch}`.

The CLI returns rc=3 in this case.

## 4. Tests (30 total, all pass)

| # | Name | Pins |
|---|---|---|
| 1 | `test_empty_state_package_renders_empty_state_banner` | Banner = `no_eligible_production_blocked`; `empty_state` passes through verbatim. |
| 2 | `test_no_inspected_package_banner_is_no_tickers_inspected` | Banner = `no_tickers_inspected` when nothing inspected. |
| 3 | `test_eligible_rows_render_ranking_table_rows_in_order` | Ranks preserved; direction / windows / k_cells / strongest / capture / sharpe formatted. |
| 4 | `test_blocked_rows_render_blocked_table_rows` | Reason / data_status / chart_status / issues preserved per row. |
| 5 | `test_ticker_cards_preserve_detail_semantics_for_eligible` | Card carries `detail_source=<artifact_path>`, `detail_available=True`, no blocker text. |
| 6 | `test_ticker_cards_preserve_detail_semantics_for_blocked` | Card carries `detail_available=False`, `detail_blocker=<reason>`, `summary=None`. |
| 7 | `test_schema_version_mismatch_returns_error_view_model` | Wrong `schema_version` → error view model with `error_code=schema_version_mismatch`. |
| 8 | `test_schema_version_missing_returns_error_view_model` | Missing field → `error_code=schema_version_missing`. |
| 9 | `test_non_mapping_package_returns_error_view_model` | Non-dict input → `error_code=package_unreadable`. |
| 10 | `test_missing_optional_fields_render_as_unknown` | Minimal `{schema_version: ...}` → no crash; banner = `no_tickers_inspected`. |
| 11 | `test_partial_ranking_row_fields_render_as_unknown` | Partial ranking row → `"unknown"` / null fallbacks. |
| 12 | `test_partial_blocked_row_fields_render_as_unknown` | Partial blocked row → `"unknown"` / `"unavailable"` fallbacks. |
| 13 | `test_summaries_pass_through_verbatim` | `chart_readiness_summary` / `freshness_summary` / `issue_summary` survive without mutation. |
| 14 | `test_eligible_count_zero_does_not_fabricate_ranking_rows` | No ranking rows are invented when there are no eligible rows. |
| 15 | `test_eligible_count_zero_does_not_fabricate_ticker_cards` | No cards invented when `ticker_details = {}`. |
| 16 | `test_status_banner_kinds_are_exactly_four` | The four-kind taxonomy is locked. |
| 17 | `test_load_package_from_path` | File-path loader. |
| 18 | `test_load_package_from_stdin` | stdin loader. |
| 19 | `test_load_package_from_builder_with_injected_fake` | Builder-callable injection works. |
| 20 | `test_cli_happy_path_from_package_file_returns_rc_0` | rc=0 on a valid package file. |
| 21 | `test_cli_missing_universe_args_returns_rc_2` | rc=2 when nothing supplied. |
| 22 | `test_cli_unknown_flag_returns_rc_2` | rc=2 on unknown flag. |
| 23 | `test_cli_schema_mismatch_returns_rc_3` | rc=3 on wrong `schema_version`. |
| 24 | `test_cli_missing_package_file_returns_rc_3` | rc=3 + error view model on missing file. |
| 25 | `test_cli_unreadable_json_returns_rc_3` | rc=3 + `package_unreadable` on invalid JSON. |
| 26 | `test_cli_stdin_path_returns_rc_0` | stdin happy path. |
| 27 | `test_module_no_raw_pickle_load` | B12: no raw `pickle.load`. |
| 28 | `test_module_no_forbidden_top_level_imports` | No forbidden imports. |
| 29 | `test_module_no_disk_write_calls` | No `write_text` / `write_bytes` / `json.dump`. |
| 30 | `test_module_ast_has_no_write_true_kwarg` | No `write=True` kwarg anywhere. |

## 5. Validation run

All from the pinned conda interpreter
(`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`):

- `py_compile project/confluence_website_reader_view.py` →
  clean.
- `pytest test_scripts/test_confluence_website_reader_view.py
  -q` → **30 passed in 0.14s**.
- `pytest test_scripts/test_confluence_website_reader_view.py
  test_scripts/test_confluence_website_export_package.py
  test_scripts/test_confluence_multiwindow_ranking_export.py
  test_scripts/test_static_regression_guards.py -q` →
  **100 passed in 3.30s** (30 reader/view + 25 Phase 6I-35
  package + 36 Phase 6I-34 ranking export + 9 static
  regression guards).
- `git diff --check` → clean.

The Phase 2A B12 static regression guard
(`test_b12_no_raw_pickle_load_outside_central_loader`)
still passes WITHOUT a new allowlist entry for the new
module — the new file contains no raw `pickle.load`.

## 6. Current production expected rendering

When the reader is pointed at production
(`--artifact-root output/research_artifacts`,
`--all-artifacts`, no `--package`):

- The Phase 6I-35 builder runs in process and discovers
  the two production Confluence artifacts (SPY and
  `_GSPC`). Both classify as
  `data_status=daily_only` /
  `ranking_blocked_reason=daily_only` because production
  artifacts do not yet carry the Phase 6I-20 multi-window
  fields.
- The package's `empty_state` is populated with the
  honest production-blocked reason.
- The reader produces a view model with:
  - `has_eligible_rankings = False`;
  - `status_banner.kind =
    "no_eligible_production_blocked"`;
  - `ranking_table = []`;
  - `blocked_table` with two rows (SPY + `_GSPC`),
    `reason="daily_only"`, `data_status="daily_only"`,
    `chart_status="no_chart_data_source"`;
  - `ticker_cards` with two cards (SPY + `_GSPC`),
    `rank_eligible=False`, `detail_available=False`,
    `detail_source=None`,
    `detail_blocker="daily_only"`,
    `summary=None`,
    `blocker_text="daily_only"`.

When the SPY pilot eventually flips through
refresh → promote → Confluence-patch-write, the same
reader will produce one rank-eligible card for SPY plus
the existing blocked row(s) for any remaining tickers
without further code work; the broader universe follows
once each ticker's Confluence artifact acquires the
Phase 6I-20 fields.

## 7. What this phase does NOT do

- **No styling finalization.** The view model is data, not
  CSS / HTML / a component tree. The eventual renderer is
  a separate phase.
- **No production writes.** Zero writer surfaces are
  touched; the new module imports
  `confluence_website_export_package` (and that module's
  dependency `confluence_multiwindow_ranking_export`) only.
- **No source refresh.** No yfinance / cache fetch /
  promotion / Confluence-patch-write.
- **No SPY refresh resume.** The SPY pilot remains
  PARKED until the Phase 6I-33 source-readiness
  predicate flips.
- **No final researched scoring model.** The reader
  consumes whatever ranking order the Phase 6I-34
  first-pass rule produces. Replacing the first-pass rule
  is a separate future phase.

## 8. No-production-activity confirmation

- No writer `--write` (any writer).
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
