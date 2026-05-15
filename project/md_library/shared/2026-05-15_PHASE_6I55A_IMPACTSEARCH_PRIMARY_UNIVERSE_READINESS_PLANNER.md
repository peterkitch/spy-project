# Phase 6I-55a: ImpactSearch / primary-universe readiness planner

**Date:** 2026-05-15
**Base commit (main):** `4e0b42f` (Phase 6I-55 squash-merge)
**Branch:** `phase-6i-55a-impactsearch-primary-universe-readiness-planner`
**Status:** Read-only planner. No production writes. **Do not merge** until operator approval.

`<PINNED_PYTHON> = C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`

---

## 1. ELI5 — why this phase exists

The Confluence rollout has a real four-step chain:

1. **OnePass** computes signals for each *primary* ticker and writes them as a "signal library" PKL under `signal_library/data/stable/`.
2. **ImpactSearch** loads many primary signal libraries, ranks them against a chosen *secondary* ticker (e.g. SPY), and writes a per-secondary workbook at `output/impactsearch/<SECONDARY>_analysis.xlsx`.
3. **StackBuilder** takes that workbook (via `--prefer-impact-xlsx`) and builds stacked-signal configurations for the secondary.
4. **Confluence** consumes the StackBuilder outputs to build the multi-window ranking board.

Phase 6I-55 tried to skip step 2/3 and just run StackBuilder directly with the Phase 6I-52 locked command. StackBuilder refused (`FATAL: Primary tickers field is empty`), because its `phase1_preflight` requires either `--primaries <CSV>` or `--prefer-impact-xlsx` — and the Phase 6I-52 lock had neither.

Phase 6I-55a is the **read-only readiness inspector** for that ImpactSearch bridge. For each of the 6 price-cache-ready tickers from Phase 6I-54b, it asks: *"is there a fresh, verifiable ImpactSearch workbook on disk that StackBuilder could consume right now?"* — and returns one of three answers:

- `ready_for_stackbuilder_with_impact_xlsx` — workbook present, fresh, manifest-verified, primary universe extractable, signal-library coverage adequate.
- `needs_impactsearch_run` — workbook missing or stale; ImpactSearch must be run for this ticker first.
- `manual_review` — workbook present but ambiguous (load error, manifest rejected, required columns missing, price cache missing, coverage incomplete, etc.).

It does NOT run StackBuilder, ImpactSearch, OnePass, yfinance, or any other engine. It does NOT load signal-library PKLs (existence check only). It does NOT use `subprocess` or raw `pickle.load`. It uses the **approved provenance/loader path** (`provenance_manifest.load_verified_xlsx_artifact`) to verify each candidate workbook, mirroring StackBuilder's own `try_load_rank_from_impact_xlsx` cascade exactly.

## 2. What was added

### Module: `project/confluence_impactsearch_primary_universe_readiness_planner.py`

- Default ticker set: **SPY, AAPL, JNJ, WMT, HD, MCD** (the Phase 6I-54a/b 6 ready tickers).
- Workbook discovery filter, freshness gate, manifest verification, column standardization, numeric coercion, row-drop, primary-universe extraction — all 1:1 mirrors of the corresponding `stackbuilder.py` code paths cited in `upstream_chain_citations`.
- Secondary price-cache check + primary signal-library coverage check (existence-only).
- Three-value classification taxonomy + 10 stable issue codes (`ALL_ISSUE_CODES`).
- Command-manifest emitter: for each `ready_for_stackbuilder_with_impact_xlsx` ticker, builds the Phase 6I-52 locked command plus the ImpactSearch bridge (`--prefer-impact-xlsx`, `--impact-xlsx-dir`, `--impact-xlsx-max-age-days`). `--strict-manifests` propagates when the planner was run with it. Authorization tagged `stackbuilder_write`; `requires_separate_operator_authorization=true`. **The planner never executes the command.**
- For `needs_impactsearch_run` tickers, the planner emits a comment-only record (no executable argv). The ImpactSearch CLI surface has not been audited by this phase; emitting an unverified argv would mirror the Phase 6I-55 stackbuilder-locked-shape gap.
- CLI with `--tickers`, `--impact-xlsx-dir`, `--impact-xlsx-max-age-days`, `--strict-manifests`, `--signal-lib-dir`, `--price-cache-dir`, `--bottom-n-coverage-threshold`, `--output`. The `--output` path is guarded against every documented production root + `output/impactsearch`.

### Tests: `project/test_scripts/test_confluence_impactsearch_primary_universe_readiness_planner.py`

18 focused tests, all passing under the pinned interpreter:

| # | Test | Pins |
|---|---|---|
| 1 | `test_schema_and_taxonomy_constants_are_stable` | Schema + 3-classification + 10-issue-code taxonomies. |
| 2 | `test_rank_colmap_matches_stackbuilder` | Local `_RANK_COLMAP` mirror matches `stackbuilder._RANK_COLMAP` (drift guard). |
| 3 | `test_missing_workbook_routes_to_needs_impactsearch` | `impact_xlsx_missing`. |
| 4 | `test_stale_workbook_routes_to_needs_impactsearch` | `impact_xlsx_stale` against an `mtime_age_days=60` fixture + `max_age_days=45`. |
| 5 | `test_fresh_verified_workbook_routes_to_ready` | Full happy path: workbook + price cache + 25-primary signal-library coverage → `ready_for_stackbuilder_with_impact_xlsx`. |
| 6 | `test_missing_required_columns_routes_to_manual_review` | `impact_xlsx_required_columns_missing` + `manual_review`. |
| 7 | `test_strict_manifest_rejection_routes_to_manual_review` | `legacy=True` + `strict_manifests=True` → `impact_xlsx_manifest_rejected`. |
| 8 | `test_signal_library_coverage_incomplete_routes_to_manual_review` | 5 of 25 primary signal libraries → `primary_signal_library_coverage_incomplete`. |
| 9 | `test_missing_secondary_price_cache_routes_to_manual_review` | `secondary_price_cache_missing` (Phase 6I-53 invariant). |
| 10 | `test_ready_command_includes_impact_xlsx_bridge_flags` | `--prefer-impact-xlsx` + `--impact-xlsx-dir` + `--impact-xlsx-max-age-days`; NO `--primaries`, NO `--both-modes`, NO `--strict-manifests` (unless requested). |
| 11 | `test_ready_command_parses_against_real_stackbuilder` | The emitted argv parses cleanly against `stackbuilder.parse_args(...)`. |
| 12 | `test_strict_manifests_flag_propagates_to_command` | `--strict-manifests` propagates from planner to emitted argv. |
| 13 | `test_no_forbidden_top_level_imports` | No `pickle` / `subprocess` / `yfinance` / `stackbuilder` / engine / writer imports. |
| 14 | `test_module_source_has_no_raw_pickle_load_call` | AST guard against `pickle.load(...)` or `pickle_load_compat(...)`. |
| 15 | `test_output_path_guard_rejects_guarded_roots` | `--output` rejects all 7 guarded roots (5 documented + `price_cache/daily` + `output/impactsearch`). |
| 16 | `test_production_state_smoke_skips_when_impact_xlsx_dir_absent` | Production smoke skips cleanly in cacheless worktree; runs informationally otherwise. |
| 17 | `test_no_production_activity_contract_present` | Every plan output carries `no_production_activity_contract` with `no_raw_pickle_load=true` + the `never_invokes` and `never_writes_to` lists. |
| 18 | `test_upstream_chain_citations_present` | All 9 code-path citations (`onepass.py:1154` ... `provenance_manifest.py:1821`) appear in every plan output. |

## 3. Production classification (verdict today)

```
<PINNED_PYTHON> confluence_impactsearch_primary_universe_readiness_planner.py \
    --output md_library/shared/2026-05-15_PHASE_6I55A_IMPACTSEARCH_PRIMARY_UNIVERSE_READINESS_EVIDENCE.json
```

**Verdict:** `0 ready, 6 needs_impactsearch_run, 0 manual_review.`

| Ticker | Classification | Issue codes | Workbook mtime | Age (days) |
|---|---|---|---|---|
| SPY | `needs_impactsearch_run` | `impact_xlsx_stale` | 2026-01-10 | **125.16** |
| AAPL | `needs_impactsearch_run` | `impact_xlsx_stale` | 2026-01-10 | **125.16** |
| JNJ | `needs_impactsearch_run` | `impact_xlsx_missing` | — | — |
| WMT | `needs_impactsearch_run` | `impact_xlsx_missing` | — | — |
| HD | `needs_impactsearch_run` | `impact_xlsx_missing` | — | — |
| MCD | `needs_impactsearch_run` | `impact_xlsx_missing` | — | — |

**Reading:**

- **SPY + AAPL:** ImpactSearch workbooks exist at `output/impactsearch/<T>_analysis.xlsx` but are dated 2026-01-10 — about 4 months old vs the 45-day default cap. The planner emits a `needs_impactsearch_run` comment-only record explaining the staleness (the operator could also override `--impact-xlsx-max-age-days` upward, but that decision is the operator's; Phase 6I-55a does not silently raise the cap).
- **JNJ, WMT, HD, MCD:** No matching workbook at all in `output/impactsearch/`. ImpactSearch must run for each before StackBuilder can.

This matches the Phase 6I-55 amendment-1 evidence exactly: even option B (`--prefer-impact-xlsx`) on the locked StackBuilder command cannot serve any of the 6 today.

## 4. Production-roots evidence pass — no production activity

| Root | Pre-run | Post-run | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5229 | 5229 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |
| `price_cache/daily` | 6 | 6 | 0 |
| `output/impactsearch` | 247 | 247 | 0 |

No `--write`. No `PRJCT9_AUTOMATION_WRITE_AUTH`. No yfinance fetch. No StackBuilder invocation. No ImpactSearch invocation. No OnePass invocation. No source-cache refresh. No signal-library promotion. No Confluence patch writer. No pipeline runner. No `subprocess` call (statically enforced). No raw `pickle.load` (statically enforced).

## 5. Verified upstream chain (citations carried in the JSON)

The planner output's `upstream_chain_citations` field lists every code path the planner mirrors. Reviewers can cross-reference each citation against the actual source:

| Stage | File:line |
|---|---|
| OnePass writes signal libraries | `onepass.py:1154 save_signal_library` |
| ImpactSearch reads signal libraries | `impactsearch.py:1525 load_signal_library` |
| ImpactSearch writes per-secondary workbook | `impactsearch.py:2491 export_results_to_excel` (output_dir at `:1355`) |
| StackBuilder consumes workbook | `stackbuilder.py:583 try_load_rank_from_impact_xlsx` |
| StackBuilder workbook column standardization | `stackbuilder.py:570 _standardize_rank_columns` + `_RANK_COLMAP` at `:562` |
| StackBuilder FATAL guard | `stackbuilder.py:889 phase1_preflight` |
| StackBuilder K-build | `stackbuilder.py:1487 phase3_build_stacks` |
| StackBuilder CLI bridge flags | `stackbuilder.py:3361` (`--prefer-impact-xlsx`) + `:3362` (`--impact-xlsx-dir`) + `:3363` (`--impact-xlsx-max-age-days`) |
| StackBuilder signal-library candidate paths | `stackbuilder.py:702 list_signal_library_candidates` |
| Verified XLSX loader (provenance) | `provenance_manifest.py:1821 load_verified_xlsx_artifact` |

## 6. Generated command-manifest examples

### Ready row (example shape — not a current production ticker; all 6 are `needs_impactsearch_run` today)

When a ticker is `ready_for_stackbuilder_with_impact_xlsx`, the planner emits:

```
<PINNED_PYTHON> stackbuilder.py \
    --secondary <TICKER> \
    --top-n 20 --bottom-n 20 --max-k 6 \
    --search beam --beam-width 12 \
    --seed-by total_capture --optimize-by total_capture \
    --min-trigger-days 30 \
    --combine-mode intersection \
    --signal-lib-dir signal_library/data/stable \
    --prefer-impact-xlsx \
    --impact-xlsx-dir output/impactsearch \
    --impact-xlsx-max-age-days 45
```

`--strict-manifests` appears at the end iff the planner was invoked with `--strict-manifests`. The argv parses cleanly against `stackbuilder.parse_args` (pinned by `test_ready_command_parses_against_real_stackbuilder`). Authorization tagged `stackbuilder_write` + `requires_separate_operator_authorization=true` + `policy_basis="phase_6i_52_locked_policy_plus_phase_6i_55a_impactsearch_bridge"`. **Planner does not execute.**

### needs_impactsearch_run row (today's verdict for all 6 tickers)

```
# SPY: workbook present but stale (age_days=125.16 > max_age_days=45). Phase 6I-55a
# recommends running ImpactSearch in a separate, explicitly-authorized phase to
# produce a fresh output/impactsearch/SPY_<...>.xlsx workbook. This planner does
# NOT emit an ImpactSearch CLI invocation -- the ImpactSearch entry surface has
# not been audited by Phase 6I-55a, so a fabricated argv would risk repeating the
# Phase 6I-55 stackbuilder-locked-shape gap.
```

For JNJ/WMT/HD/MCD the comment is the same except the reason is `"no matching workbook on disk"` instead of stale.

## 7. Test commands and results

```
"<PINNED_PYTHON>" -m pytest \
    test_scripts/test_confluence_impactsearch_primary_universe_readiness_planner.py -v
... 18 passed in 1.36s
```

Combined Phase 6I planner/policy/preflight/rebuild-planner/writer/readiness regression:

```
"<PINNED_PYTHON>" -m pytest \
    test_scripts/test_confluence_large_universe_launch_planner.py \
    test_scripts/test_confluence_large_universe_rollout_batch_planner.py \
    test_scripts/test_confluence_stackbuilder_rollout_policy.py \
    test_scripts/test_confluence_stackbuilder_pilot_preflight.py \
    test_scripts/test_stackbuilder_price_cache_rebuild_planner.py \
    test_scripts/test_stackbuilder_price_cache_writer.py \
    test_scripts/test_phase_6i_55_evidence_doc_guard.py \
    test_scripts/test_confluence_impactsearch_primary_universe_readiness_planner.py -q
... see Phase 6I-55a PR description for the green total
```

`py_compile` clean on the new module + test file. `git diff --check` clean.

## 8. Files added (3)

- `project/confluence_impactsearch_primary_universe_readiness_planner.py` — read-only planner.
- `project/test_scripts/test_confluence_impactsearch_primary_universe_readiness_planner.py` — 18 focused tests.
- `project/md_library/shared/2026-05-15_PHASE_6I55A_IMPACTSEARCH_PRIMARY_UNIVERSE_READINESS_PLANNER.md` (this doc).
- `project/md_library/shared/2026-05-15_PHASE_6I55A_IMPACTSEARCH_PRIMARY_UNIVERSE_READINESS_EVIDENCE.json` — production-state plan JSON.

## 9. Exact next recommended phase

**Phase 6I-55b — supervised ImpactSearch batch for the 6 ready secondary tickers.** ImpactSearch is its own explicitly-authorized phase (the CLI surface still needs to be audited the way StackBuilder was audited for Phase 6I-52; doing that audit + producing the locked ImpactSearch policy is Phase 6I-55b's prerequisite). The operator decides whether to run all 6 or a subset.

After Phase 6I-55b lands and the workbooks are fresh, the operator re-runs Phase 6I-55a; the verdict should flip from `6 needs_impactsearch_run` to `6 ready_for_stackbuilder_with_impact_xlsx`. Then:

**Phase 6I-52 amendment-3** locks `--prefer-impact-xlsx` (+ `--impact-xlsx-dir` + `--impact-xlsx-max-age-days`) into the StackBuilder command shape so future StackBuilder runs cite the ImpactSearch bridge by default.

**Phase 6I-55c** retries the supervised StackBuilder batch using the amended locked command, against only the `ready_for_stackbuilder_with_impact_xlsx` subset.

The Phase 6I-50/51/52/53/54a/54b/55/55a chain remains valid building blocks; only the workbook freshness gate is preventing forward progress today.
