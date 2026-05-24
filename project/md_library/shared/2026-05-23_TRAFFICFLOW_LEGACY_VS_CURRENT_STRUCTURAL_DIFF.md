# TrafficFlow LEGACY vs Current Structural Diff

## 1. Scope and Non-Goals

This is a static structural diff of `trafficflow.py` between a LEGACY reference commit and current `main`. It is read-only inspection only.

- LEGACY is defined as `main` HEAD as of approximately 30 calendar days before the task date. The exact resolved commit is recorded in section 3.
- The diff informs upcoming TrafficFlow headless scoping work but does not authorize implementation.
- No `trafficflow.py` code was modified.
- `trafficflow.py` was not imported or executed.
- No engine was run.
- The deliverable is documentation only.

## 2. Methodology and Coverage

Comparison performed against the full source text of `trafficflow.py` at both commits, using:

- `git show <LEGACY_SHA>:project/trafficflow.py` and `git show main:project/trafficflow.py` for source extraction.
- `git diff --stat` and `git log --oneline <LEGACY>..main -- trafficflow.py` for the commit trail.
- Python `ast.parse` over the source text at each commit, extracted as bytes and decoded UTF-8 with replacement, to enumerate top-level functions, classes, and module-level assignments.
- Regex scans over the source text for Dash callback decorator blocks (`@app.callback`, `@dash.callback`, `@callback`), and for canonical-scoring / provenance / TF_MATRIX / refresh-callback / price-cache reference counts.

Coverage:

- Full raw `git diff` reviewed.
- Full top-level AST inventory compared (82 LEGACY defs vs 83 current defs).
- Full callback inventory compared (2 LEGACY vs 2 current).
- Default-value surfaces reviewed (46 LEGACY module-level assigns vs 53 current; 3 removed, 10 new, 1 changed).
- **Dash component-default sweep over `make_app` completed via AST walk** of every `html.* / dcc.* / dbc.* / dash_table.* / Dash(...)` constructor call inside the `make_app` body: 12 constructors in LEGACY, 12 in current, all matched by `(ctor, id)` or positional-within-ctor; every keyword argument byte-identical across versions (see section 9.4).
- Imports compared (28 LEGACY vs 31 current; 3 new, 0 removed).
- Data-flow surfaces reviewed (StackBuilder consumption keywords, price_cache references, refresh-callback issue-code references, canonical-scoring delegation, provenance-manifest verification, TF_MATRIX removal).
- `trafficflow.py` was not imported or executed.
- No engine was run.

## 3. LEGACY Reference Commit

- **Task date**: 2026-05-23.
- **Cutoff used**: 30 days before task date, i.e., commits at or before `2026-04-23 23:59:59` local.
- **Window used**: 30 days (no widening needed; a commit resolved within the 30-day window).
- **Resolved LEGACY SHA**: `5361445e1c16ddf26cbe7a381716919d0b9778a0`.
- **LEGACY commit date**: `2025-11-30 17:07:36 -0800`.
- **LEGACY subject**: `Merge pull request #126 from peterkitch/confluence-enhancements`.
- **Note on the gap**: there were no commits on `main` between the LEGACY commit (2025-11-30) and the Phase 6I sprint resumption in early 2026, so the "30 days before today" resolution rule lands on a much-older commit. This is the most recent commit on `main` at or before the 30-day cutoff per the spec's resolution rule.
- **Ancestor check**: `git merge-base --is-ancestor 5361445 main` returns true; LEGACY is a real ancestor of current `main`.

## 4. Current Main Reference Commit

- **Current main HEAD SHA**: `2cb3e538643b33710c40304254e0a7872ba502a4`.
- **Commit date**: `2026-05-23 18:57:47 -0700`.
- **Subject**: `Update CLAUDE.md to reflect post-Phase-6I state`.

## 5. File-Level Summary

| Field | LEGACY | CURRENT | Delta |
|---|---:|---:|---:|
| Path | `project/trafficflow.py` | `project/trafficflow.py` | same |
| Size (bytes) | 145,888 | 145,940 | +52 |
| Line count | 3,445 | 3,422 | -23 |
| Git blob SHA | `69b6107012080be821187eb8d69c8bcd1c595239` | `24c260a468bbfcb2c8bd6b13bb5bdda5dfe5145a` | differ |
| Raw diff stat | -- | -- | 329 insertions / 352 deletions / 681 lines touched |

The line count shrinks by 23 while the byte count grows by 52: net code reduction with slightly longer remaining lines on average. Consistent with the matrix-removal commit and the canonical-scoring delegation, both of which collapse local code while adding short import shims.

## 6. Commit Trail Touching trafficflow.py

`git log --oneline <LEGACY>..main -- trafficflow.py` (relative to project root):

| Order | SHA | Subject |
|---|---|---|
| 1 | `7a27947` | Phase -1: Public repo security cleanup (#128) |
| 2 | `7406886` | Phase 1B-2A (partial): Adj Close removal in stale_check + signal_library, ddof:11668 fix (#132) |
| 3 | `0768355` | Phase 1B-2B: backlog cleanup (logs, dedupe, cache keys, grace, sentinels, closure, outdir) (#133) |
| 4 | `e31a0c7` | Phase 3B-2A: output manifest helper + StackBuilder run manifests + Spymaster PKLs (#143) |
| 5 | `8081f73` | Phase 3B-2B: XLSX upsert manifests + strict-mode CLI + Phase 3 close (#144) |
| 6 | `ddbdb42` | Phase 5B Item 2: remove TrafficFlow disabled-matrix code path (#151) |
| 7 | `3f3044a` | Phase 5B Item 8: surface TrafficFlow refresh callback errors (#158) |

Seven commits touched the file. The user-visible behavior changes group as follows:

- Phase -1 (#128): security/portability cleanup (eliminated hardcoded absolute paths).
- Phase 1B-2A (#132): Adj Close removal / ddof correctness threads through this file's calendar / price plumbing.
- Phase 1B-2B (#133): broad backlog cleanup including signal sentinels and closure of various Phase 1 edges.
- Phase 3B-2A / 3B-2B (#143 / #144): output manifest helpers and Spymaster PKL provenance verification thread through this file's PKL load path.
- Phase 5B Item 2 (#151): wholesale removal of the disabled-matrix code path.
- Phase 5B Item 8 (#158): refresh callback error surfacing.

## 7. Functions and Classes Added / Removed / Modified

LEGACY top-level definitions: **82**.
CURRENT top-level definitions: **83**.
Net delta: **+1** (3 new minus 2 removed).

### 7.1 REMOVED (in LEGACY, not in CURRENT)

- `def _averages_via_matrix(...)` -- removed in Phase 5B Item 2 along with the rest of the disabled matrix code path.
- `def _members_signals_df_and_returns(...)` -- removed; was a helper feeding the matrix path.

### 7.2 NEW (in CURRENT, not in LEGACY)

- `def _format_trafficflow_issue(reason)` -- new in Phase 5B Item 8; formats a structured reason code into a human-readable refresh-callback issue line.
- `def _price_cache_key(symbol)` -- new helper for price-cache key derivation, consistent with the price_cache reference-count growth (5 -> 23).
- `def _strict_manifests_enabled()` -- new; reads the strict-manifest verification gate that the Phase 3B-2A / 3B-2B work introduced across engines.

### 7.3 MODIFIED (common name, body change; signatures unchanged for all 14)

| Function | LEGACY lines | CURRENT lines | Delta | Signature | Structural summary (inferred surfaces) |
|---|---:|---:|---:|---|---|
| `_combine_signals` | 39 | 15 | -24 | unchanged | Body collapsed onto delegation to `canonical_scoring.combine_consensus_signals` (Phase 1B-2A / 1B-2B). Affects: outputs (numerical results now come from the canonical helper); internal cleanup. |
| `load_spymaster_pkl` | 18 | 31 | +13 | unchanged | Added `load_verified_pickle_artifact` verification (Phase 3B-2A). Affects: manifest/provenance behavior (PKL reads now gated on the strict-manifest contract); error handling (verification-failure paths added). |
| `_load_secondary_prices` | 38 | 38 | 0 | unchanged | Single-line replacement of `sec = (secondary or "").upper()` with `sec = _price_cache_key(secondary)`. Affects: cache behavior (cache-key derivation centralized through the new `_price_cache_key` helper); internal cleanup. |
| `_metrics_like_spymaster` | 91 | 78 | -13 | unchanged | Reduction; consistent with shared scoring delegation. Affects: outputs (metric values now flow through the canonical scoring delegate); internal cleanup. |
| `_session_sanity` | 70 | 70 | 0 | unchanged | Single-line replacement of `_PRICE_CACHE.get(secondary)` with `_PRICE_CACHE.get(_price_cache_key(secondary))`. Affects: cache behavior (lookup key normalized through the new helper); internal cleanup. |
| `_signal_snapshot_for_members` | 87 | 87 | 0 | unchanged | Two-line replacement: both the `_PRICE_CACHE.get(...)` lookup and the subsequent `_PRICE_CACHE[...] = sec_df` write now key on `_price_cache_key(secondary)`. Affects: cache behavior (read and write keys consistently normalized); internal cleanup. |
| `_stream_primary_positions_and_captures` | 81 | 81 | 0 | unchanged | Two-line replacement of magic-number fallback tuples `((1, 2), 0.0)` with `(_BUY_SENTINEL, 0.0)` and `(_SHORT_SENTINEL, 0.0)` (the new module-level SMA sentinel constants pointing at `(MAX_SMA_DAY, MAX_SMA_DAY-1)` and `(MAX_SMA_DAY-1, MAX_SMA_DAY)` respectively). Affects: outputs (the SMA pair used when yesterday's top-pair lookup misses now follows the canonical Spymaster sentinel convention rather than literal `(1,2)`). Static diff shows the literal-to-named-constant swap; downstream pair-impact behavior under a miss may differ, but the behavioral impact is not fully inferable without execution. |
| `_subset_metrics_spymaster` | 202 | 192 | -10 | unchanged | Reduction along with the matrix-path cleanup and canonical delegation. Affects: outputs (canonical scoring delegate); internal cleanup. |
| `_subset_metrics_spymaster_bitmask` | 144 | 132 | -12 | unchanged | Same shape as `_subset_metrics_spymaster`. Affects: outputs (canonical scoring delegate); internal cleanup. |
| `_subset_metrics_spymaster_fast` | 126 | 116 | -10 | unchanged | Same shape as `_subset_metrics_spymaster`. Affects: outputs (canonical scoring delegate); internal cleanup. |
| `compute_build_metrics_spymaster_parity` | 195 | 181 | -14 | unchanged | Same shape as `_subset_metrics_spymaster`. Affects: outputs (canonical scoring delegate); internal cleanup. |
| `main` | 28 | 28 | 0 | unchanged | Single `print()` change to the v1.9 startup banner: the `Matrix=REMOVED` suffix is removed (the matrix path is gone, so advertising its removal is no longer informative). Affects: UI behavior only (operator-visible startup banner cosmetic). |
| `make_app` | 258 | 306 | +48 | unchanged | Largest UI growth; consistent with Phase 5B Item 8 callback-issue plumbing surfacing through `make_app`. **Component-default sweep** (section 9.4) shows zero Dash component default changes at the constructor surface, so the +48 lines live inside the nested callback bodies (`_refresh` and `update_tooltips` are nested defs inside `make_app`) and the layout-wiring imperative code, not in Dash component defaults. Affects: UI behavior (refresh-callback issue surfacing visible to the operator); error handling. |
| `refresh_secondary_caches` | 64 | 119 | +55 | unchanged | Largest function-body growth in the diff; matches Phase 5B Item 8 surfacing of refresh-callback errors and the new structured reason codes. Affects: error handling (failures now classify into the 5 enumerated `REFRESH_*` / `PRICE_LOAD_FAILED` reason codes); UI behavior (those reason codes become visible to the operator through the `_refresh` callback). |

UNCHANGED top-level definitions: **66** (out of 80 names common to both versions). No signature changed in any of the 14 modified functions.

## 8. Dash UI Callback Inventory Comparison

Both LEGACY and current register **exactly 2 Dash callbacks**, by the regex scan of `@app.callback` / `@dash.callback` / `@callback` decorators:

| Callback function | LEGACY line | CURRENT line | Outputs | Inputs | States | `prevent_initial_call` | Change |
|---|---:|---:|---:|---:|---:|---|---|
| `_refresh` | L3226 | L3155 | 5 | 2 | 0 | False | UNCHANGED at surface; body changes are subsumed in the `make_app` body churn |
| `update_tooltips` | L3383 | L3360 | 1 | 1 | 0 | False | UNCHANGED at surface |

No callbacks added, removed, or with changed Output/Input/State counts. No callback acquired or lost `prevent_initial_call`. The surface inventory is stable; the user-facing behavior changes (refresh-callback error surfacing from Phase 5B Item 8) appear inside the `_refresh` body, not in its registered signature.

## 9. Default Value Changes

LEGACY module-level assignments: **46**.
CURRENT module-level assignments: **53**.
UNCHANGED: **42**.

### 9.1 REMOVED module-level assigns (3, all matrix-related)

- `TF_MATRIX_DTYPE = 'int8'`
- `TF_MATRIX_MAX_K = int(os.environ.get('TF_MATRIX_MAX_K', '12'))`
- `TF_MATRIX_PATH = False`

These three vanish together in commit `ddbdb42` (Phase 5B Item 2 "remove TrafficFlow disabled-matrix code path"). TF_MATRIX references in the source text drop from 9 to 0 between versions; no orphan references remain.

### 9.2 NEW module-level assigns (10)

- `MAX_SMA_DAY = 114`
- `_BUY_SENTINEL = (MAX_SMA_DAY, MAX_SMA_DAY - 1)`
- `_SHORT_SENTINEL = (MAX_SMA_DAY - 1, MAX_SMA_DAY)`
- `_PROJECT_DIR = Path(__file__).resolve().parent`
- `PRICE_LOAD_FAILED = 'price_load_failed'`
- `REFRESH_EXCEPTION = 'refresh_exception'`
- `REFRESH_NO_DATA = 'refresh_no_data'`
- `REFRESH_SYMBOL_FAILED = 'refresh_symbol_failed'`
- `REFRESH_UNAVAILABLE = 'refresh_unavailable'`
- `_REFRESH_ISSUES_DISPLAY_LIMIT = 10`

The five `REFRESH_*` / `PRICE_LOAD_FAILED` string codes plus `_REFRESH_ISSUES_DISPLAY_LIMIT` together form the structured-issue surface introduced by Phase 5B Item 8. The two SMA sentinel tuples plus `MAX_SMA_DAY = 114` are added during Phase 1B-2B's signal-sentinel pass. `_PROJECT_DIR` is the new path anchor that replaces hardcoded absolute paths.

### 9.3 CHANGED module-level assign (1, critical for portability/privacy)

- **`SPYMASTER_PKL_DIR`**:
  - LEGACY: a literal absolute path string referencing a specific local development environment (full path redacted per privacy rule; pre-existing in LEGACY source text only; not present in current).
  - CURRENT: `os.environ.get('PRJCT9_SPYMASTER_PKL_DIR', str(_PROJECT_DIR / 'cache' / 'results'))`.

This is the largest single behavior-affecting default change: a hardcoded local absolute path was replaced with an env-var-with-project-relative-default. This is exactly the Phase -1 (#128) "Public repo security cleanup" pattern. Future TrafficFlow headless scoping must use the env-var override (or accept the project-relative default).

### 9.4 Dash UI component defaults

The callback signatures (Output/Input/State counts and `prevent_initial_call`) are unchanged (section 8). Component-level UI defaults (`value=`, `checked=`, `min=`, `max=`, `step=`, `options=`, `multi=`) live inside the `make_app` body, which grew by +48 lines.

**Sweep method.** An AST walk was performed over the `make_app` body at both LEGACY (`5361445e1c16ddf26cbe7a381716919d0b9778a0`) and current `main` (`2cb3e538643b33710c40304254e0a7872ba502a4`). Every `ast.Call` whose callee resolves to `html.*`, `dcc.*`, `dbc.*`, `dash_table.*`, or bare `Dash(...)` was extracted. For each constructor, the `id=` keyword (when present) was used as the match key; for the rare unkeyed constructor, positional pairing within the same Python type was used. For every matched pair, every keyword argument was compared as `ast.unparse` byte-equality across versions.

**Sweep result.**

| Surface | LEGACY count | CURRENT count | Delta |
|---|---:|---:|---:|
| Dash component constructors inside `make_app` | 12 | 12 | 0 |
| `Dash(...)` | 1 | 1 | 0 |
| `dash_table.DataTable(...)` | 1 | 1 | 0 |
| `dcc.Input(...)` | 1 | 1 | 0 |
| `html.Button(...)` | 1 | 1 | 0 |
| `html.Div(...)` | 5 | 5 | 0 |
| `html.H3(...)` | 1 | 1 | 0 |
| `html.Label(...)` | 1 | 1 | 0 |
| `html.Span(...)` | 1 | 1 | 0 |
| `id=`-keyed constructors matched | 6 | 6 | 0 |
| Constructors NEW in CURRENT vs LEGACY | -- | 0 | -- |
| Constructors REMOVED in LEGACY vs CURRENT | -- | 0 | -- |
| Constructors with CHANGED kwargs | -- | 0 | -- |
| Constructors UNCHANGED | -- | 12 | -- |

The six `id=`-keyed constructors matched 1:1 across versions: `board` (`dash_table.DataTable`), `k` (`dcc.Input`), `refresh` (`html.Button`), `missing-pkls` (`html.Div`), `status` (`html.Div`), `last-update` (`html.Span`). All keyword arguments inside each of those constructors (`columns`, `data`, `page_size`, `sort_action`, `sort_by`, `style_cell`, `style_data_conditional`, `style_header`, `style_table`, `tooltip_data`, `value`, `min`, `step`, `type`, `style`, `n_clicks`, etc.) are byte-identical across LEGACY and current. The six unkeyed constructors (1 `Dash`, 3 `html.Div`, 1 `html.H3`, 1 `html.Label`) match by ctor + position within the same constructor type, and their per-call kwargs are also byte-identical across versions.

**Implication.** The +48-line growth in `make_app` is NOT at the Dash-component-default surface. It lives inside the nested callback bodies (`_refresh` and `update_tooltips` are nested defs declared inside `make_app`) and the imperative layout-wiring code that constructs the page tree. **No Dash component default in `make_app` resembles the Phase 6I-77 / 6I-78 class of UI/CLI default-drift bug**: every constructor default in the LEGACY UI is preserved verbatim in the current UI. (The headless-scoping default-drift audit still matters once a runner CLI is added; this finding only certifies the Dash-side defaults are stable across LEGACY and current.)

### 9.5 argparse defaults

No `argparse.ArgumentParser` is registered at module scope in either version, and the AST scan finds no `argparse.add_argument(... default=...)` constructs at top level. TrafficFlow's CLI surface is effectively `main()` deferring to Dash. **Open question for headless scoping** (section 14): the headless TrafficFlow runner will need a new argparse surface, designed with the Phase 6I-77 / 6I-78 default-drift lessons in mind (see also the sprint carry-forward tracking doc's defaults-diff audit item).

### 9.6 Phase 6I-77 class default-drift assessment

The Phase 6I-77 / 6I-78 class of bug was: a Dash UI checkbox default differs from the runner CLI argparse default, so the same engine produces materially different behavior depending on the launch surface. Findings here:

- There is **no runner CLI argparse surface** in `trafficflow.py` yet, so the LEGACY-vs-current default-drift question for TrafficFlow proper is moot until the headless runner is added.
- However, the `SPYMASTER_PKL_DIR` env-var-or-default change (section 9.3) is the same general shape: a previously-hardcoded value is now overridable via environment. Any future headless TrafficFlow runner CLI must align with the Dash UI defaults inside `make_app`, and the defaults-diff audit item in the sprint carry-forward tracking doc should explicitly cover TrafficFlow once the runner exists.

## 10. Imports and Dependency Changes

LEGACY top-level imports: **28**.
CURRENT top-level imports: **31**.

### 10.1 NEW imports (3, all cross-engine shared modules)

- `from canonical_scoring import combine_consensus_signals as _canonical_consensus`
- `from canonical_scoring import score_captures as _canonical_score_captures`
- `from provenance_manifest import load_verified_pickle_artifact as _load_verified_pickle_artifact`

These match the Phase 1B-2A / 1B-2B canonical-scoring rewire and the Phase 3B-2A / 3B-2B PKL provenance verification. Source-text reference counts confirm: `canonical_scoring` references 0 -> 3; `provenance_manifest` references 0 -> 1; `load_verified_pickle` references 0 -> 4.

### 10.2 REMOVED imports

None at the top-level. The matrix-path removal in Phase 5B Item 2 took out internal references but did not remove any top-level imports.

### 10.3 Changed import paths

None. All other imports preserved their module path and alias.

### 10.4 New runtime-dependency implications

`canonical_scoring` and `provenance_manifest` are first-party project modules already in use by other engines (StackBuilder, ImpactSearch, etc.). No new third-party dependencies are introduced.

## 11. Data Flow Changes

### 11.1 StackBuilder input consumption (LEGACY vs current)

Source-text keyword scan:

| Surface | LEGACY occurrences | CURRENT occurrences |
|---|---:|---:|
| `combo_leaderboard` | 8 | 8 |
| `output/stackbuilder` | 4 | 4 |
| `run_dir` | 0 | 0 |
| `selected_build` | 0 | 0 |
| `rank_all` | 0 | 0 |
| `rank_direct` | 0 | 0 |
| `cohort.xlsx` | 0 | 0 |
| `combo_k=` | 0 | 0 |

TrafficFlow consumes StackBuilder outputs via `combo_leaderboard` XLSX files only (and the `output/stackbuilder/` parent path scan). It does **not** consume any of the richer Phase 6I StackBuilder artifacts: `selected_build.json`, the per-secondary fresh run directory, `rank_all` / `rank_direct`, `cohort.xlsx`, or per-K `combo_k=N.json` files. This is an unchanged contract across LEGACY and current, but it is an important finding for headless TrafficFlow scoping: the post-Phase-6I-79 StackBuilder layer now writes a richer artifact surface than TrafficFlow currently consumes. See section 14.

### 11.2 Price-data input path

Source-text `price_cache` references jumped from **5 to 23**, a +18 delta concentrated in `refresh_secondary_caches` (+55 lines) and the new `_price_cache_key` helper. Price-cache reading still happens through `_load_secondary_prices` -> `_read_cache_file` / `_yf_fetch_incremental`; the new code adds structured-failure reporting paths around those calls rather than changing the caching strategy.

### 11.3 Output write path

No top-level changes. TrafficFlow does not write canonical engine outputs (it is a Dash UI consuming StackBuilder outputs and Spymaster PKLs). The `_dump_csv` helper is present in both versions and unchanged.

### 11.4 Cache / intermediate-file behavior

Spymaster PKL loading now routes through `load_verified_pickle_artifact` (Phase 3B-2A) in `load_spymaster_pkl` (+13 lines). This adds provenance-manifest verification at PKL read time. The strict-manifest gate is read via the new `_strict_manifests_enabled` helper; behavior is gated rather than forced.

### 11.5 Schema / data-shape expectations

No top-level schema constants changed except the `SPYMASTER_PKL_DIR` move from absolute string to env-var-with-default (section 9.3). The `combo_leaderboard` XLSX columns expected by TrafficFlow are not declared as module-level constants and so cannot be diffed by AST inspection; they live inside `build_board_rows`, `_find_latest_combo_table`, and `_read_table` bodies. Those three functions are in the UNCHANGED count, so the column-schema expectation appears stable across LEGACY and current.

## 12. Error Handling / Logging / Progress Changes

| Surface | LEGACY | CURRENT | Delta |
|---|---:|---:|---:|
| `try:` blocks | 67 | 62 | -5 |
| `print()` calls | 19 | 19 | 0 |
| `logging.<level>(...)` calls | 0 | 0 | 0 |
| `log.<level>(...)` calls | 0 | 0 | 0 |
| `warnings.warn(...)` | 0 | 0 | 0 |
| Refresh issue codes (`refresh_no_data` / `refresh_exception` / `refresh_unavailable` / `refresh_symbol_failed` / `price_load_failed`) | 0 | 7 | +7 |
| Progress JSON / `progress_path` references | 0 | 0 | 0 |

Net error-handling shape:

- `try:` blocks dropped by 5 -- some defensive scaffolding consolidated, consistent with the cleanup commits.
- The 7 new occurrences of structured refresh issue codes are the user-visible improvement from Phase 5B Item 8 (#158): refresh callback failures now classify into a small enumerated set of reasons rather than being swallowed silently.
- `print()` count unchanged at 19. TrafficFlow does not use the `logging` module nor `warnings.warn` in either version. **Open question for headless scoping** (section 14): headless TrafficFlow likely needs structured logging rather than `print()`-to-stderr; consider whether to introduce a `logging` surface at conversion time.

## 13. Performance-Sensitive Changes

`@lru_cache` decorators: 0 in both. `ThreadPoolExecutor` references: 7 in both (the concurrent secondary load path). No new loops over tickers / windows / builds are visible at the top-level AST surface; the `_subset_metrics_spymaster*` family shrank slightly (10-14 line reductions each) consistent with the matrix-path removal and shared-scoring delegation but the algorithmic shape (bitmask vs fast vs full) is preserved as three distinct functions in both versions.

The largest performance-relevant function-body change is `refresh_secondary_caches` (+55 lines), which is error-reporting growth rather than algorithmic change. Headless TrafficFlow may inherit the refresh-callback semantics or replace them with explicit refresh control; that scoping is out of band for this diff.

## 14. Open Questions for TrafficFlow Headless Scoping

1. **Make_app default sweep (LEGACY-vs-current portion CLOSED; runner-CLI portion remains open).** The LEGACY-vs-current Dash component-default sweep over `make_app` has been completed in section 9.4: 12 constructors in each version, all matched, every kwarg byte-identical. **No Dash component default has drifted between LEGACY and current.** The remaining open question is for the future headless TrafficFlow runner CLI: when that runner is built, its argparse defaults must be enumerated and compared against the (still-stable) `make_app` component-default set in section 9.4, exactly the Phase 6I-77 / 6I-78 defaults-diff audit pattern. The reference list of current `make_app` Dash component defaults lives in this diff doc and the source itself; no separate inventory pass is needed before the runner-CLI build begins.

2. **StackBuilder artifact contract.** TrafficFlow currently consumes only `combo_leaderboard` XLSX files (section 11.1). Post-Phase-6I-79 StackBuilder writes a richer artifact surface: `selected_build.json` (per-secondary parent), `cohort.xlsx`, `rank_all.xlsx`, `rank_direct.xlsx`, per-K `combo_k=N.json`, `combo_leaderboard.xlsx`, `run_manifest.json`, `summary.json`. Should headless TrafficFlow consume `selected_build.json` instead of (or in addition to) `combo_leaderboard.xlsx`? `selected_build.json` carries `selected_run_dir`, `selected_k`, `total_capture`, `sharpe_ratio`, `selection_policy` and points to a fresh run directory with all the K artifacts. This is a contract decision.

3. **SPYMASTER_PKL_DIR override.** The env-var-with-default pattern is in place. The headless runner CLI should add `--spymaster-pkl-dir` (or equivalent) and document the env-var precedence.

4. **Refresh-callback semantics in headless.** TrafficFlow currently surfaces refresh issues through the Dash `_refresh` callback. Headless TrafficFlow either inherits the issue-code surface (5 enumerated reasons + `_REFRESH_ISSUES_DISPLAY_LIMIT` cap) or replaces it with explicit programmatic refresh control. Scoping should pick one before the conversion begins.

5. **Logging surface.** TrafficFlow uses `print()` (19 calls) and no `logging` / `warnings`. Headless conversion likely needs structured logging for operator-visible run progress and failures.

6. **Strict-manifest gate behavior.** The new `_strict_manifests_enabled()` reads a gate (presumably env-var-backed) that controls whether `load_verified_pickle_artifact` failures are fatal. Headless TrafficFlow needs an explicit policy: strict by default, or opt-in?

7. **Provenance and canonical scoring delegation.** `_combine_signals` collapsed from 39 to 15 lines via `canonical_scoring.combine_consensus_signals` delegation. The headless runner should not need to re-implement combine semantics; it should consume the canonical delegate. This is already true; no change needed, but the headless scoping should record that contract.

8. **Process-conflict probe.** The other headless runners (`stackbuilder_workbook_runner.py`, etc.) use a process-conflict probe that scans cmdlines for known engine script names. A new `trafficflow_workbook_runner.py` should follow the same pattern; the existing `PROCESS_CONFLICT_PATTERNS` tuple in `stackbuilder_workbook_runner.py` should be extended.

## 15. Recommended Next Steps

1. ~~Capture the make_app Dash component defaults sweep as a separate read-only Codex inspection task before the TrafficFlow headless conversion begins.~~ **Done** as part of this amendment (section 9.4). The LEGACY-vs-current portion is closed; the runner-CLI defaults-diff comparison will become actionable once a `trafficflow_workbook_runner.py` exists and exposes argparse defaults to compare against.
2. Decide the StackBuilder-artifact consumption contract for headless TrafficFlow (`combo_leaderboard.xlsx` vs `selected_build.json` vs both).
3. Design the headless TrafficFlow runner CLI surface with `--spymaster-pkl-dir`, `--strict-manifests`, refresh-control flags, and a structured logging gate.
4. Extend `stackbuilder_workbook_runner.PROCESS_CONFLICT_PATTERNS` (and equivalent in the new runner) to include `trafficflow_workbook_runner.py` once it exists.
5. Update the defaults-diff audit item in the sprint carry-forward tracking doc once make_app UI defaults are enumerated, so the audit can compare them against the future runner CLI defaults before any headless write run.
6. The seven commits in section 6 should be re-read in commit-message order before the headless conversion begins; each represents a deliberate behavior change that the headless runner must preserve.

### Privacy note

The LEGACY source text of `trafficflow.py` contains pre-existing local-absolute-path strings (the LEGACY `SPYMASTER_PKL_DIR` literal). The current source no longer contains those strings. This diff doc references the pattern only via placeholders; the actual pre-existing private values were not quoted into this doc.
