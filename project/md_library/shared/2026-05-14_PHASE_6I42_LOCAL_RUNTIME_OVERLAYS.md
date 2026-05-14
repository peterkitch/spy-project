# Phase 6I-42: local runtime overlay providers for the Confluence board

**Branch:** `phase-6i-42-local-runtime-overlays`

## 1. ELI5

The Phase 6I-40 board contract added stable per-row sub-
dicts for `data_completeness`, `current_signal_status_block`,
and `flip_risk` placeholders. The Phase 6I-41 renderer
already paints them. **But until this phase, those three
sub-dicts shipped as conservative defaults: every ticker
looked "complete + locked + no price"** because nothing
was actually scanning local data.

This phase ships **the actual local providers** — a
strictly read-only scanner that:

- reads each ticker's local `cache/results/<TICKER>_precomputed_results.pkl` (via the central provenance
  loader; **no raw `pickle.load`**) and extracts the
  latest close + as-of date;
- accepts injected provider callables (StackBuilder /
  signal-library scanners + adapter diagnostics) that flag
  TEF-style invalid or stale members;
- emits a per-ticker overlay report;
- exposes adapter factories
  (`make_member_completeness_provider`,
  `make_live_price_provider`) so the Phase 6I-34 ranking
  export's existing injection seams pick the overlay data
  up — and the eventual HTML carries warning symbols,
  current-status badges, and latest-price values.

**No `yfinance`, no subprocess, no source refresh, no
production write.** The default cache loader uses
`provenance_manifest.load_verified_pickle_artifact` via a
deferred local import so the module's import surface stays
small AND the B12 raw-pickle ban is preserved.

## 2. How TEF / invalid members are surfaced

Invalid members are surfaced via two complementary
injection seams that **both contribute** to the same
overlay (lists are merged without duplicates):

1. **`stackbuilder_member_callable(ticker, *, stackbuilder_root, signal_library_dir)`** — returns a
   dict with `incomplete_members: list[str]` +
   `incomplete_member_reasons: dict[str, str]`. The
   intended production wiring scans the StackBuilder
   leaderboard / signal-library directory for stale or
   missing member PKLs and flags them. The Phase 6I-42
   default is `None` (no detection); tests inject a fake
   that returns TEF flagged with
   `"yfinance_possibly_delisted"`.
2. **`adapter_diagnostic_callable(ticker, *, artifact_root, cache_dir)`** — returns the same shape
   from the Phase 6I-27-style multi-window adapter
   diagnostic. The intended production wiring inspects
   per-cell skip reasons and surfaces member-level data
   issues.

When at least one member is flagged the overlay's
`data_completeness` block carries:

- `has_incomplete_build_members=True`
- `incomplete_member_count`, `incomplete_members`,
  `incomplete_member_reasons`
- `data_warning_symbol="!"`
- `data_completeness_status="partial"`
- `data_completeness_message="partial: N member(s) incomplete or stale"`

**These are NOT silently dropped.** TEF stays in the
`incomplete_members` list with its reason, and the renderer
shows the `!` warning symbol on the ranking row.

When `rank_eligible_hint=False` (artifact missing) the
status flips to `"blocked"` with the `!` symbol; the
incomplete-member input is suppressed in this branch so
blocked rows do NOT carry fabricated member data.

## 3. How latest price / as-of is derived

The default path probes the local cache PKL via the
central provenance loader. The price-extraction helper
sweeps known top-level keys then nested `daily` block:

- Price keys (first match wins): `target_close`, `close`,
  `Close`, `Adj Close`, `adjusted_close`.
- Date keys (first match wins): `dates`, `date_index`,
  `Date`, `index`, plus `last_date` inside `daily`.

The helper is fully defensive:

- A missing PKL → `current_signal_status=unknown` +
  `issue_code=cache_pkl_missing`.
- A central-loader load error →
  `issue_code=cache_pkl_unreadable`.
- A payload that doesn't match any known shape →
  `issue_code=cache_pkl_unknown_shape` and
  `latest_price=None`.
- A payload that yields `(price, date)` →
  `signal_update_source="local_cache"` +
  `latest_price=<float>` +
  `latest_price_as_of=<str>`.

**No `yfinance` fetch, no live-quote poll, no HTTP.** Live
price values are exclusively whatever has been written to
local cache already.

## 4. Locked / provisional / stale / blocked / unknown

The Phase 6I-40 taxonomy values are mapped from the cache
freshness:

| Condition | `current_signal_status` |
|---|---|
| Cache date `>= current_as_of_date`, OR `current_as_of_date` not supplied | `locked` |
| Cache date strictly behind `current_as_of_date` | `stale` |
| Cache load failed / shape unknown / date unparsable | `unknown` |
| `rank_eligible_hint=False` (no artifact AND no cache) | `blocked` (standalone overlay only) |
| Live overlay future seam | `provisional` (NOT exercised in Phase 6I-42 default) |

The `provisional` status is reserved for a future live-
quote injection (a hypothetical
`live_price_provider_callable` that overlays an intraday
or post-close quote). This phase deliberately does NOT
ship a live-quote feed.

When the standalone overlay says `blocked`, the
`make_live_price_provider` adapter returns `None` so the
Phase 6I-34 ranking export's own strict Phase 6I-20 gate
is the source of truth for blocked-row classification.
The overlay's data_completeness still tells the renderer
"blocked + !" on the standalone report; but it never
overrides the ranking export's eligibility decision
through the live-price seam.

## 5. Why this does NOT weaken strict multi-window truth

- The overlay populates Phase 6I-40 sub-dicts only. It
  **never** writes `per_window_k_metrics`,
  `build_wide_window_alignment`, or
  `multiwindow_k_engine_payload_metadata` fields.
- Rank eligibility STILL requires the Phase 6I-20 strict
  validator upstream. A daily-only artifact is still
  blocked regardless of how fresh the local cache PKL is.
- The overlay's data_completeness contract honors
  `rank_eligible=False` from the ranking export: blocked
  rows do NOT receive fabricated incomplete-member fields.
- The overlay's `make_live_price_provider` returns `None`
  on blocked rows so the ranking export's own blocked-
  state handling is preserved.

## 6. Current production preview

Production state (carried forward from Phase 6I-38):

- Production Confluence artifacts (SPY + `_GSPC`) classify
  `daily_only`; the multi-window K-engine payload is not
  yet written.
- Local cache PKLs exist for ~3,239 tickers under
  `cache/results/` (per the Phase 6I-38 snapshot). The
  overlay can extract latest-price values for any ticker
  with a cache PKL.

When the Phase 6I-42 overlay is wired into the production
website chain today:

- `eligible_count=0` (Phase 6I-20 strict gate still wins).
- Blocked rows for SPY + `_GSPC` carry
  `current_signal_status="blocked"` + warning `!`.
- The latest-price overlay would populate `latest_price`
  for any cache-hit ticker once the ranking-export's
  upstream eligibility flips — but until production
  artifacts acquire the Phase 6I-20 fields, the overlay
  contributes mainly to the *blocked-rows* honesty + the
  detail panel's member-warning text.

**Production preview still has zero eligible rows.** The
overlay's value today is that it can be enabled WITHOUT
flipping the eligibility verdict — it makes the existing
blocked / partial rows more honest, without weakening
strict multi-window truth.

## 7. Public API summary

### 7.1 `BoardRuntimeOverlayReport` (dataclass)

Fields: `schema_version`, `generated_at`,
`inspected_count`, `overlays_by_ticker`,
`data_completeness_by_ticker`, `latest_price_by_ticker`,
`current_signal_status_by_ticker`, `issue_codes`,
`summary`, `remaining_limitations`. `to_json_dict()`
emits a JSON-serializable copy.

### 7.2 `build_board_runtime_overlays(...)`

Optional kwargs:
- `artifact_root`, `cache_dir`,
- `stackbuilder_root`, `signal_library_dir`,
- `current_as_of_date`,
- `cache_loader_callable`,
- `adapter_diagnostic_callable`,
- `stackbuilder_member_callable`.

Returns `BoardRuntimeOverlayReport`.

### 7.3 Provider factories

- `make_member_completeness_provider(report) -> Callable` — adapter for
  `confluence_multiwindow_ranking_export.build_multiwindow_ranking_export(member_completeness_provider_callable=...)`.
- `make_live_price_provider(report) -> Callable` — adapter
  for `live_price_provider_callable=...`. Returns `None`
  on rows the overlay classifies as `blocked` so the
  ranking export's own eligibility gate is the source of
  truth.

### 7.4 CLI

```
python confluence_board_runtime_overlays.py \
    --tickers AAA,BBB,CCC \
    [--artifact-root PATH] \
    [--cache-dir PATH] \
    [--stackbuilder-root PATH] \
    [--signal-library-dir PATH] \
    [--current-as-of-date YYYY-MM-DD]
```

Prints the overlay-report JSON to stdout. rc=0 happy, rc=2
missing tickers, rc=3 unhandled exception.

## 8. Static-renderer integration

No code change required on the Phase 6I-41 renderer. The
warning symbol, status badge, and latest-price column
already read from `data_completeness` /
`current_signal_status_block` / `latest_price`. The
end-to-end test
(`test_overlays_pass_through_full_chain`) builds an
overlay with a TEF-style incomplete member + a 2026-05-14
cache for SPY, plugs it into the full Phase 6I-34 → 6I-35
→ 6I-36 → 6I-41 chain, and asserts the rendered HTML
shows:

- `<tr class="ranking-row"` exactly once,
- the `!` warning symbol,
- `status-locked` badge CSS class,
- `550.25` (the local cache's latest close),
- `TEF` appearing in the inlined detail JSON.

## 9. Tests (27 new + 202 existing focused tests = 229 total)

New file:
`project/test_scripts/test_confluence_board_runtime_overlays.py`.

| # | Name | Pins |
|---|---|---|
| 1 | `test_latest_price_extracted_from_local_cache` | Top-level `{dates, close}` payload → `latest_price`, `latest_price_as_of`, source `local_cache`. |
| 2 | `test_latest_price_extracted_from_nested_daily_block` | Phase-6C `daily` block also handled. |
| 3 | `test_unknown_cache_shape_yields_unknown_status` | Unknown shape → `unknown` + `cache_pkl_unknown_shape` issue. |
| 4 | `test_stale_cache_marks_signal_status_stale` | Cache date < cutoff → `stale` + `cache_stale_vs_cutoff` issue. |
| 5 | `test_current_cache_marks_signal_status_locked` | Cache date == cutoff → `locked`. |
| 6 | `test_no_current_as_of_date_defaults_to_locked` | No cutoff supplied → `locked`. |
| 7 | `test_tef_style_invalid_member_surfaces_in_warnings` | Member provider flags TEF → `data_completeness_status="partial"`, `incomplete_members=["TEF"]`, `data_warning_symbol="!"`. |
| 8 | `test_adapter_diagnostic_provider_also_supplies_members` | Both injection seams merge without duplicates. |
| 9 | `test_blocked_ticker_no_artifact_marks_completeness_blocked` | No artifact + no cache → `data_completeness_status="blocked"` + signal-block blocked. |
| 10 | `test_blocked_ticker_does_not_fabricate_member_warnings` | Blocked row ignores member-incomplete input. |
| 11 | `test_cache_loader_exception_degrades_gracefully` | Loader raises → `unknown` + `cache_pkl_unreadable` issue. |
| 12 | `test_member_provider_exception_degrades_gracefully` | Member provider raises → `complete` + `completeness_provider_raised` issue. |
| 13 | `test_overlays_pass_through_full_chain` | End-to-end: overlay → ranking export → package → reader/view → static renderer; warning `!`, `status-locked`, latest_price=550.25, TEF in detail JSON, exactly 1 ranking row. |
| 14 | `test_one_row_per_ticker_invariant_holds_with_overlays` | 3 eligible tickers + overlays → 3 ranking rows; CCC's stale cache surfaces `status-stale` badge. |
| 15 | `test_stale_overlay_propagates_to_status_badge` | Single-ticker stale cache → renderer shows `status-stale`. |
| 16 | `test_member_completeness_provider_returns_dc_shape` | Provider factory adapter returns the 4-field dict the ranking export expects. |
| 17 | `test_live_price_provider_returns_payload_with_status` | Provider factory adapter passes price + status through. |
| 18 | `test_report_summary_counts_correct` | Summary aggregates: locked / stale / unknown / tickers_with_latest_price / tickers_with_incomplete_members. |
| 19 | `test_report_to_json_dict_is_serializable` | Round-trips through json.dumps cleanly. |
| 20 | `test_cli_emits_overlay_report_json` | CLI rc=0; output JSON has the right schema. |
| 21 | `test_cli_missing_tickers_returns_rc_2` | `--tickers ""` → rc=2. |
| 22 | `test_module_no_forbidden_top_level_imports` | No yfinance / dash / live engine / writer / pipeline_runner. |
| 23 | `test_module_no_raw_pickle_load` | No raw `pickle.load`. |
| 24 | `test_module_no_resample_or_ffill_calls` | AST scan: no `.resample()` / `.ffill()`. |
| 25 | `test_module_no_write_true_kwarg` | No `write=True`. |
| 26 | `test_module_no_subprocess_use` | AST scan: no `import subprocess`, no `subprocess.X(...)`. |
| 27 | `test_no_yfinance_import_anywhere_in_module` | No `yfinance` anywhere. |

## 10. Validation run

Pinned conda interpreter
(`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`):

- `py_compile project/confluence_board_runtime_overlays.py` → clean.
- `py_compile project/test_scripts/test_confluence_board_runtime_overlays.py` → clean.
- `pytest test_scripts/test_confluence_board_runtime_overlays.py -q` → **27 passed in 0.19s**.
- `pytest <full focused suite> -q` → **229 passed in 4.19s** (27 new + 32 renderer + 22 Phase 6I-40 + 18 primary-build + 30 current-signal + 30 reader/view + 25 package + 36 ranking export + 9 static regression).
- `git diff --check`: clean.
- B12 raw-pickle regression guard still passes without a new allowlist entry (the new module uses `provenance_manifest.load_verified_pickle_artifact` via deferred import).

## 11. SPY pilot status

Unchanged. SPY remains **PARKED**. Phase 6I-42 makes
blocked rows more honest (warning `!` + member list when
provider flags one) but does not flip Phase 6I-20
eligibility.

## 12. No-production-activity confirmation

- No writer `--write` (any writer).
- `PRJCT9_AUTOMATION_WRITE_AUTH` never read or set.
- No source refresh (`signal_engine_cache_refresher`).
- **No `yfinance` fetch** (no yfinance import anywhere in the module).
- **No subprocess** (no `import subprocess`, no `subprocess.X` call).
- No production promotion (`signal_library_stable_promotion_writer`).
- No Confluence patch writer (`multiwindow_k_confluence_patch_writer`).
- No `confluence_pipeline_runner` invocation.
- No StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster batch execution.
- No production data write.
- The default cache loader reads via `provenance_manifest.load_verified_pickle_artifact` (deferred import; B12 raw-pickle ban preserved).
- Production `signal_library/data/stable/` and `output/research_artifacts/` untouched.
