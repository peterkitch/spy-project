# Phase 6I-50: Large-universe Confluence launch planner + StackBuilder automation policy planner

**Date:** 2026-05-15 (amendment-1 same day)
**Base commit (main):** `5fc50f3` (Phase 6I-49 squash-merge)
**Branch:** `phase-6i-50-large-universe-launch-planner`
**Status:** Read-only planner. No production writes. **Do not merge** until operator approval.

---

## Amendment-1: corrected StackBuilder CLI / default facts

Codex audit of the original Phase 6I-50 commit (`51ff504`) flagged three material errors in the planner's StackBuilder policy section. Amendment-1 corrects them at module + test + evidence levels.

| Field | Original (wrong) | Corrected |
|---|---|---|
| Command-template entry argument | `--ticker <TICKER>` | `--secondary <TICKER>` (the actual `stackbuilder.py` argparse name; `--secondaries` is the comma-separated variant) |
| `observed_defaults_from_source.k_patience` | `1` | `0` (matches `p.add_argument('--k-patience', type=int, default=0, ...)` in `stackbuilder.py` ~L3348) |
| `combine_mode` exposure claim | "`stackbuilder.py` defines `COMBINE_INTERSECTION` as a private constant but does NOT expose `combine_mode` as a CLI argument" | **It IS exposed.** `stackbuilder.py` ~L3350 declares `p.add_argument('--combine-mode', choices=['intersection','union'], default='intersection', ...)`. `observed_defaults_from_source` now records `combine_mode: 'intersection'` directly. The unresolved-question entry was reworded to affirm CLI exposure and ask the operator only whether the launch should keep `intersection` or switch to `union`. |

Files changed in amendment-1:

- `project/confluence_large_universe_launch_planner.py` — `STACKBUILDER_OBSERVED_DEFAULTS` (k_patience=0, combine_mode='intersection', entry_argument='--secondary', seed_by surfaced); `STACKBUILDER_UNRESOLVED_POLICY_QUESTIONS` (combine_mode entry reworded); `documented_stackbuilder_command_template` (--ticker → --secondary, --combine-mode intersection added).
- `project/test_scripts/test_confluence_large_universe_launch_planner.py` — 3 new tests: `test_command_template_uses_secondary_not_ticker`, `test_observed_defaults_match_stackbuilder_parse_args_defaults` (compares against `stackbuilder.parse_args([])` defaults at runtime), `test_unresolved_questions_no_longer_claim_combine_mode_missing`. **16 / 16 tests pass.**
- `project/md_library/shared/2026-05-15_PHASE_6I50_LAUNCH_PLANNER_EVIDENCE.json` — regenerated.
- `project/md_library/shared/2026-05-15_PHASE_6I50_LARGE_UNIVERSE_LAUNCH_PLANNER.md` (this doc) — amendment-1 section + Section 5 updates.

Re-running `--all-artifacts` against production for amendment-1 verification: pre/post file counts identical (3239 / 1634 / 35 / 5228 / 72899) across all 5 production roots. **No production activity** — no `--write`, no `PRJCT9_AUTOMATION_WRITE_AUTH`, no yfinance, no source refresh, no pipeline runner, no batch engine.

---

## 1. Purpose

Phase 6I-49 closed the single-ticker SPY pilot: SPY now renders on the live Confluence website as a `partial_multiwindow` rank-eligible row with the honest `!` warning. The pilot pattern (cache → signal-library → StackBuilder → MTF Confluence artifact → website) is proven end-to-end. Single-ticker exceptions are not the steady-state mode.

Phase 6I-50 introduces the **first planning surface scoped beyond SPY**: a read-only planner that inspects a ticker universe and emits a structured per-ticker + aggregate readiness report. Tickers are classified across four axes — artifact / cache / signal-library / StackBuilder — and the planner picks a stable `recommended_next_action` code for each. Aggregate counts plus a four-batch rollout proposal plus a StackBuilder automation policy section round out the report.

This phase is a **planning** phase, not a write phase. The module never runs StackBuilder, the source-cache refresher, yfinance, the stable-promotion writer, the Confluence patch writer, or any pipeline runner. Production roots remain `0/0/0`.

## 2. What was added

### Module

`project/confluence_large_universe_launch_planner.py`

- Public entry `build_large_universe_launch_plan(tickers, *, artifact_root, cache_dir, signal_library_dir, stackbuilder_root, universe_mode, invalid_members, ...)`.
- CLI with four universe-discovery modes (`--tickers`, `--all-artifacts`, `--from-stackbuilder-universe`, `--universe-file`).
- Per-ticker classification across four axes:
  - **artifact_status** ∈ `{strict_full_60_cell, partial_multiwindow, incomplete_multiwindow, daily_only, artifact_missing, unreadable}`.
  - **cache_status** ∈ `{cache_ready, cache_stale, cache_missing, unknown}` (disk existence + mtime probe; the planner does NOT crack open the PKL).
  - **signal_library_status** ∈ `{stable_ready, stable_missing, staged_possible, unknown}` (disk existence for base + interval PKLs).
  - **stackbuilder_status** ∈ `{run_available, run_missing, run_stale_or_ambiguous, contains_invalid_members, unknown}` (directory listing + seed-run-dir regex parse; the planner does NOT open any pickle).
- A `current_board_status` derived field ∈ `{rank_eligible_strict, rank_eligible_partial, blocked}` and a paired `ranking_eligibility_basis` ∈ `{strict_full_60_cell, partial_effective_members, null}` (matches the Phase 6I-48 contract).
- A `recommended_next_action` code per ticker ∈ `{already_board_ranked, write_partial_artifact, write_strict_artifact, refresh_source_cache, rebuild_signal_libraries, promote_signal_libraries, rerun_stackbuilder, manual_review, blocked_missing_inputs}`.
- An aggregate `counts` block, `counts_by_recommended_next_action`, `top_blocker_issue_codes`, and a `proposed_next_batches` four-bucket rollout proposal:
  - Batch 1: tickers already board-ranked (no write needed).
  - Batch 2: partial-write-ready tickers (one supervised Phase 6I-49-style write).
  - Batch 3: refresh / rebuild / promote-needed tickers.
  - Batch 4: StackBuilder-rerun-needed tickers.
  - Plus a `remaining_manual_or_missing_inputs` bucket for `manual_review` / `blocked_missing_inputs` / `write_strict_artifact` cases that need ticker-by-ticker decisions.
- A `stackbuilder_policy` block reporting (a) **observed defaults** discovered from on-disk `stackbuilder.py` source, (b) **proposed launch defaults** from the operator's Phase 6I-50 prompt (clearly labelled as PROPOSALS, NOT decisions), and (c) **six unresolved policy questions** the operator must answer before a large-universe StackBuilder rerun is authorized (`both_modes`, `combine_mode`, `seed_by`/`optimize_by`, member-universe sizing, re-run cadence, invalid-member rotation).
- A `DEFAULT_KNOWN_INVALID_MEMBERS` fallback that classifies `TEF` as `invalid_or_delisted` (per Phase 6I-43 / 6I-44 / 6I-49 evidence). The operator can override via `--invalid-members-json` (or the function-level `invalid_members` kwarg) without a code change.
- All external probes (artifact resolver / loader / classifier / member-completeness / cache / signal-library / StackBuilder) are **injectable** for test isolation. The defaults use deferred imports against the existing Phase 6I-34 / 6I-46 / 6I-47 surfaces (`confluence_multiwindow_ranking_export._resolve_artifact_path` / `_default_artifact_loader` / `_classify_artifact_data_status` / `_default_member_completeness_provider`).

### Tests

`project/test_scripts/test_confluence_large_universe_launch_planner.py` — 13 focused tests, all passing under the pinned interpreter:

1. Schema-version + taxonomy constants are stable.
2. Strict-full-60-cell artifact → `rank_eligible_strict` + `already_board_ranked`.
3. Phase 6I-47 partial-multiwindow artifact → `rank_eligible_partial` + `already_board_ranked` + propagates `incomplete_members=['TEF']`.
4. Phase 6C daily-only artifact → blocked + cascade picks `blocked_missing_inputs`.
5. Artifact-missing + chain ready → cascade picks `write_strict_artifact`.
6. Unreadable artifact → `manual_review`.
7. StackBuilder seed-run-dir parser finds `TEF` by string-match → cascade picks `write_partial_artifact` when chain is ready.
8. Seed-run invalid-member but cache missing → cascade picks `rerun_stackbuilder` (higher-leverage action when the chain isn't ready).
9. Everything-missing → `blocked_missing_inputs`.
10. Cache-ready + signal-library MISSING → `rebuild_signal_libraries`; staged-possible variant → `promote_signal_libraries`.
11. Ambiguous StackBuilder selection (>1 seed-run dirs) → `manual_review` even when otherwise ready.
12. Aggregate report shape + StackBuilder policy block stability.
13. Static guard: no forbidden top-level imports (no `yfinance` / `subprocess` / `dash` / `signal_engine_cache_refresher` / `signal_library_stable_promotion_writer` / `multiwindow_k_confluence_patch_writer` / `confluence_pipeline_runner` / `daily_board_automation_writer` / `daily_board_automation_executor` / engine modules).

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m pytest \
    test_scripts/test_confluence_large_universe_launch_planner.py -v
... 13 passed in 0.24s
```

## 3. Strict read-only contract

The planner module:

- Imports no forbidden top-level modules (the Phase 6I-50 test 12 enforces this statically).
- Reads only from `<artifact_root>/confluence/`, `cache/results/`, `signal_library/data/stable/`, and `output/stackbuilder/`. Each probe uses `Path.exists()` / `Path.is_dir()` / `Path.iterdir()` / `Path.stat().st_mtime_ns` — no `pickle.load`, no `subprocess`, no network.
- Has no `--write` argument and no `PRJCT9_AUTOMATION_WRITE_AUTH` reference.
- Defers imports of `confluence_multiwindow_ranking_export` to call time inside per-function wrappers so the module-import surface stays small and side-effect-free.

## 4. Production-roots evidence pass

The CLI was exercised against production with `--all-artifacts`:

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
    confluence_large_universe_launch_planner.py \
    --all-artifacts \
    --artifact-root output/research_artifacts \
    --cache-dir cache/results \
    --signal-library-dir signal_library/data/stable \
    --stackbuilder-root output/stackbuilder \
    > md_library/shared/2026-05-15_PHASE_6I50_LAUNCH_PLANNER_EVIDENCE.json \
    2> md_library/shared/2026-05-15_PHASE_6I50_LAUNCH_PLANNER_EVIDENCE_STDERR.txt
```

Universe inspected: 2 tickers (the current contents of `output/research_artifacts/confluence/`) — `SPY` and `_GSPC`. **Per-ticker verdict:**

| Ticker | artifact_status | current_board_status | recommended_next_action |
|---|---|---|---|
| `SPY` | `partial_multiwindow` | `rank_eligible_partial` | `already_board_ranked` |
| `_GSPC` | `daily_only` | `blocked` | `blocked_missing_inputs` |

**Aggregate counts:** `inspected=2`, `rank_eligible_strict=0`, `rank_eligible_partial=1`, `blocked=1`, `invalid_member_count=1`. **By-action breakdown:** `already_board_ranked=1`, `blocked_missing_inputs=1`. **Batch buckets:** `batch_1_no_write_board_render=['SPY']`, `remaining_manual_or_missing_inputs=['_GSPC']`; all other batches empty. The verdict is consistent with the Phase 6I-49 live website state: SPY is partial-rank-eligible with the `!` warning; `_GSPC` remains the daily-only control ticker that was deliberately not written.

**Production-roots untouched:**

| Root | Pre-run | Post-run | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5228 | 5228 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |

The planner's JSON evidence (`2026-05-15_PHASE_6I50_LAUNCH_PLANNER_EVIDENCE.json`) and the empty stderr file land in `md_library/shared/` — **not** in a guarded production root.

## 5. StackBuilder policy section (post amendment-1)

The planner reports three blocks side-by-side. Amendment-1 corrected the bolded items.

- **Observed defaults from source (verified against `stackbuilder.parse_args([])`):** `top_n=20`, `bottom_n=20`, `max_k=6`, `search='beam'`, `beam_width=12`, `exhaustive_k=4`, `both_modes=False`, `alpha=0.05`, `min_marginal_capture=0.0`, **`k_patience=0`** (amendment-1 corrected from `1`), **`combine_mode='intersection'`** (amendment-1 added; the CLI does expose `--combine-mode`), `seed_by='total_capture'`, `optimize_by=None_resolves_to_seed_by_when_unset`, **`entry_argument='--secondary'`** (amendment-1 added).
- **Proposed launch defaults:** `search='beam'`, `beam_width=12`, `max_k=6`, `seed_by='total_capture'`, `optimize_by='total_capture'`, `min_trigger_days=30`, `combine_mode='intersection'`, `top_n=20`, `bottom_n=20`. Clearly labelled as PROPOSALS, NOT decisions.
- **Documented launch command template (post amendment-1):**

  ```
  "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
      stackbuilder.py --secondary <TICKER> --top-n 20 --bottom-n 20 \
      --max-k 6 --search beam --beam-width 12 --seed-by total_capture \
      --min-trigger-days 30 --combine-mode intersection
  ```

- **Unresolved policy questions (6 — same count as the original block; the `combine_mode` entry was reworded, not removed):**
  1. `both_modes`: observed default `False`; large-universe should-it-be-True needs an operator decision.
  2. `combine_mode` (amendment-1 reworded): **the CLI exposes `--combine-mode choices=['intersection','union'] default='intersection'`**. The observed default is `intersection`. The operator must confirm whether the large-universe launch should KEEP `intersection` (conservative all-members-agree path) or switch to `union` (any-member-agree), and verify the Phase 6I-22 multi-window K input adapter respects the chosen combine mode.
  3. `seed_by` / `optimize_by`: `total_capture` proposal needs operator confirmation.
  4. Per-ticker member-universe sizing (fixed 12 / fixed N / market-cap-tuned / other) — operator decision.
  5. Re-run cadence (daily / weekly / on-invalid-member-detected) — operator decision.
  6. Invalid-member rotation policy when a member is flagged `invalid_or_delisted` (Phase 6I-43) — operator decision.

These policy items are flagged in the planner output but NOT auto-decided. A future supervisor prompt would resolve them before any large-universe StackBuilder rerun is authorized.

## 6. What this phase does NOT do

- **No production write.** No `--write`, no `PRJCT9_AUTOMATION_WRITE_AUTH`, no Phase 6I-49-style two-key authorization path. The two-key writer surfaces (`multiwindow_k_confluence_patch_writer.py`, `signal_library_stable_promotion_writer.py`, `daily_board_automation_writer.py`) are NOT invoked.
- **No source-cache refresh.** `signal_engine_cache_refresher.py` is NOT invoked. The planner does NOT call yfinance directly OR transitively.
- **No StackBuilder rerun.** `stackbuilder.py` is read for its observed defaults but never executed.
- **No pipeline runner.** `confluence_pipeline_runner.py` is NOT invoked. No `confluence` / `cross_ticker_confluence` / `daily_signal_board` / TrafficFlow / Spymaster / OnePass / ImpactSearch import at any layer.
- **No PKL crack-open at the planner layer.** Cache + signal-library + StackBuilder probes are pure disk metadata reads (existence / mtime / directory listing).

## 7. Known limitations carried forward to a future phase

- **Cache freshness vs cutoff:** `cache_ready` here means the PKL exists on disk; it does NOT confirm that `cache_date_range_end` matches an intended `current_as_of_date`. A future phase can integrate Phase 6I-43 source-refresh policy v2 to refine `cache_ready` into stale-vs-ready against a cutoff. (The Phase 6I-43 planner internally probes yfinance through `signal_engine_cache_refresher`; wiring it through would require careful authorization scoping so the launch planner does not become a yfinance entry point itself.)
- **Selected StackBuilder run:** the planner picks lexicographic-last seed-run dir for **documentation purposes**. The Phase 6I-22 multi-window K adapter uses mtime ordering at engine runtime; the two choices may diverge when a ticker has more than one seed-run dir. The planner explicitly surfaces `stackbuilder_ambiguous_selection=True` in that case so the operator sees the ambiguity rather than implicitly trusting either choice.
- **Member-universe sizing per ticker:** unresolved policy item; the planner has no opinion on what's correct.
- **Re-run cadence:** unresolved policy item; the planner has no opinion on what's correct.

## 8. Next step

Phase 6I-51 (or whatever the next operator phase is named) will likely either:

- Resolve one or more of the six StackBuilder policy questions and produce a Phase 6I-50.1-style amendment to the proposed defaults, OR
- Wire Phase 6I-43 source-refresh policy v2 verdicts into the planner's `cache_status` axis (so `cache_ready` / `cache_stale` reflect the Phase 6I-43 classification instead of pure on-disk presence), OR
- Stage a large-universe seed → cache → signal-library → StackBuilder → MTF Confluence pipeline rollout that consumes this planner's `proposed_next_batches` as the rollout order.

Whichever path the operator chooses, the planner's read-only output is the input. The planner does not authorize any of those future actions — it only describes the on-disk state.
