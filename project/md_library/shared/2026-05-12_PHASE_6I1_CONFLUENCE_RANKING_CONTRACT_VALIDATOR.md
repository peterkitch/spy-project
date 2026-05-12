# Phase 6I-1 — Confluence ranking data-contract validator

**Status:** read-only data-contract validator landed.
**No production writes were performed in this PR.**

**Last updated:** 2026-05-12.

The Phase 6H train shipped the read-only → planning →
dry-run → guarded-write → temp-dir-rehearsal → operator
runbook + manifest foundation. Phase 6I-1 adds the
orthogonal safety layer the scheduler will need next: a
read-only validator that walks the full saved-research
artifact chain and proves the data is correctly formulated
for the Daily Signal Board ranking system.

## 1. Why this exists

The Phase 6H stack answers "did the authorized writer
exit cleanly?". That is necessary but not sufficient. A
scheduler that trusted only the writer's exit code would
be blind to:

  - schema drift (a new Confluence builder version that
    silently changes field names),
  - double persist-skip trims (Phase 6F-4 regression),
  - cross-seed K mixing inside the Confluence consensus,
  - signal-alias incoherence (`confluence_signal` vs
    `signal` vs `signal_value`),
  - readiness verdicts that disagree with on-disk state.

Phase 6I-1 closes that gap as a SEPARATE read-only auditor
an operator (or a future scheduler) can invoke between
authorized runs. The validator never imports the writer
surfaces, never imports yfinance / dash / engines, never
runs subprocess, and never writes to any operator root.

## 2. What this delivers

**Code (read-only):**

  - `project/confluence_ranking_contract_validator.py` —
    new module. Public surface: `validate_confluence_ranking_contract`,
    `validate_confluence_ranking_contracts`, `main`
    (CLI). Seven per-contract check helpers; stable
    `ISSUE_*` issue-code namespace; stable
    `RECOMMENDED_*` next-operator-action namespace;
    `TickerRankingContractValidation` and
    `RankingContractReport` dataclasses.
  - `project/test_scripts/test_confluence_ranking_contract_validator.py`
    — 42 tests across 13 sections covering every
    per-contract failure mode plus aggregate report, CLI,
    no-writes, forbidden-imports static guard, and the
    no-StackBuilder-age-window enforcement. (Originally
    shipped at 35 tests; +7 added by the post-audit
    amendment that closed the Confluence count-coherence
    + board-row preview drift -- see § 4.5 and § 4.7.)

**Explicit non-goals:**

  - No source refresh.
  - No Phase 6D pipeline write.
  - No StackBuilder run.
  - No OnePass run.
  - No yfinance fetch.
  - No subprocess.
  - No write to `cache/`, `output/`, `signal_library/`,
    `stackbuilder/`, or any operator root.
  - No StackBuilder age/stale window. Saved stack variants
    remain durable; ambiguous tied-mtime remains manual.

## 3. The full artifact chain validated

The validator walks the chain in the order each stage
consumes the previous stage's outputs:

```
1. Signal Engine cache
       ↓ load_primary_signal_engine_payload
2. StackBuilder saved variant
       ↓ discover_latest_stackbuilder_run / preflight inventory
3. TrafficFlow daily K (K=1..12)
       ↓ list_daily_k_trafficflow_artifacts (strict filename filter)
4. MTF bridge (K=1..12 __MTF)
       ↓ list_mtf_trafficflow_artifacts (strict filename filter)
5. Confluence MTF artifact
       ↓ confluence_pipeline_readiness.inspect_ticker_pipeline
6. Readiness verdict
       ↓ board row preview
7. Daily Signal Board ranking row
```

Each stage's check produces a boolean OK flag plus zero or
more stable `ISSUE_*` codes. The validator never repairs
anything; it only reports.

## 4. The seven contract checks

### 4.1 Cache contract

  - Cache PKL exists at the operator-supplied
    `cache_dir/<TICKER>_precomputed_results.pkl`.
  - `primary_signal_engine.load_primary_signal_engine_payload`
    returns `available=True`.
  - `payload["date_range"]` has both `start` and `end`.
  - `payload["current_signal"]` is present (so the public
    board can render the Signal Engine state).
  - Optimizer scope (`optimizer_v1`) is accepted; the
    legacy `data_only_v1` writer-guard scope is also
    accepted (the validator does not enforce a particular
    scope; it just exercises the loader surface).

Issue codes: `cache_missing`, `cache_unreadable`,
`cache_no_date_range`, `cache_no_current_signal`.

### 4.2 StackBuilder contract

  - At least one saved variant with a valid leaderboard
    file (`combo_leaderboard.xlsx` OR `combo_k=*.json`).
  - Multiple variants ARE allowed.
  - The selected variant matches the Phase 6H-3
    `latest_mtime_existing_pipeline_default` policy.
  - Tied newest-mtime is `ambiguous_tied_mtime` and
    blocks (manual review).
  - **No age-based stale window.** The validator's source
    is statically checked for the strings `STACKBUILDER_AGE_DAYS`,
    `STACKBUILDER_STALE_DAYS`, `30 days`, and `thirty days`;
    any future PR that introduces an age threshold fails
    this contract regression test.

Issue codes: `stackbuilder_missing`,
`stackbuilder_selection_ambiguous`.

### 4.3 TrafficFlow daily-K contract

  - K=1..12 daily artifacts present on disk.
  - Filenames follow `<seed_run_id>__K<K>.research_day.json`
    (the Phase 6F-4 strict filter; legacy unsuffixed
    artifacts are excluded by the bridge helper before
    they reach the validator).
  - The artifact's internal `K` field matches the K parsed
    from the filename.

Issue codes: `daily_k_missing`,
`daily_k_incomplete_coverage`,
`daily_k_internal_k_mismatch`.

### 4.4 MTF bridge contract

  - K=1..12 `<seed_run_id>__K<K>__MTF.research_day.json`
    artifacts present.
  - `last_date` is coherent across K (all artifacts share
    the same daily last-row date).
  - `persist_skip_bars == 0` on every MTF artifact (the
    Phase 6F-4 contract: the daily-K stage owns the single
    persist trim; anything else flags the double-trim
    regression).

Issue codes: `mtf_missing`, `mtf_incomplete_coverage`,
`mtf_last_date_incoherent`, `mtf_double_persist_skip_trim`.

### 4.5 Confluence contract

  - Confluence MTF artifact exists at
    `<artifact_root>/confluence/<TICKER>/...research_day.json`.
  - Latest is picked by daily last-row date.
  - Required last-row fields all present: `date`,
    `agreement_active`, `agreement_total`, `active_count`,
    `available_count`, `buy_votes`, `short_votes`,
    `none_votes`, `missing_votes`, `K_values`,
    `timeframes`, `confluence_signal`, `signal`,
    `signal_value`, `source_trafficflow_mtf_run_ids`.
  - **Signal vocabulary:** both `signal` and
    `confluence_signal` MUST be one of
    `{"Buy", "Short", "None"}`. Anything else flags
    `confluence_invalid_signal_vocabulary`. The vocab
    check fires first; if it triggers, the alias-mismatch
    check is suppressed so the two issue codes do not
    stack for the same root cause.
  - **Signal alias coherence:** `confluence_signal ==
    signal`; `signal_value` follows the canonical mapping
    `Buy=1 / Short=-1 / None=0`.
  - **Vote tally per-cell:** `buy_votes + short_votes +
    none_votes + missing_votes == len(K_values) *
    len(timeframes)`. `missing_votes` are per-CELL, NOT
    per-LABEL.
  - **Full count coherence** (Phase 6I-1 amendment) -- the
    Daily Signal Board renders its agreement display from
    `active_count` / `available_count` (via
    `daily_signal_board._confluence_active_total`), so
    those fields and their siblings must agree exactly:
      * `active_count == buy_votes + short_votes`
      * `available_count == active_count + none_votes`
      * `agreement_total == available_count`
      * `available_count + missing_votes == expected_cells`
    Any drift among these flags
    `confluence_count_incoherent`.
  - **Strict-unanimity rule for `agreement_active`**
    (Phase 6I-1 amendment) -- the Phase 6D-3 builder's
    rule for the final-signal count must hold:
      * `buy=0, short=0`         -> `agreement_active == 0`
      * `buy>0, short=0`         -> `agreement_active == buy_votes`
      * `buy=0, short>0`         -> `agreement_active == short_votes`
      * `buy>0, short>0` (mixed) -> `agreement_active == 0`
    The rule is checked independently of `expected_cells`
    so it pins even when `K_values` / `timeframes` are
    unavailable. Drift flags
    `confluence_agreement_active_inconsistent`.
  - **No cross-seed K mixing:** every
    `source_trafficflow_mtf_run_ids` entry must share the
    same seed prefix (i.e. the same StackBuilder variant
    sourced every K).

Issue codes: `confluence_missing`,
`confluence_last_row_incomplete`,
`confluence_invalid_signal_vocabulary`,
`confluence_signal_alias_mismatch`,
`confluence_vote_total_mismatch`,
`confluence_count_incoherent`,
`confluence_agreement_active_inconsistent`,
`confluence_cross_seed_k_mixing`.

### 4.6 Readiness contract

  - `confluence_pipeline_readiness.inspect_ticker_pipeline`
    runs without raising against the operator roots.
  - The readiness layer's confluence-presence finding
    matches the validator's confluence-contract finding.

Issue codes: `readiness_verdict_drift`.

### 4.7 Board row contract

For every ticker that clears the confluence + readiness
checks, the validator derives the Daily Signal Board row
preview the public board would render:

```json
{
  "ticker": "SPY",
  "consensus_signal": "None",
  "consensus_signal_value": 0,
  "agreement_active": 7,
  "agreement_total": 60,
  "agreement_ratio": 0.11666666666666667,
  "coverage": "Full",
  "as_of_date": "2026-05-08",
  "rank_eligible": false,
  "ranking_blocked_reason": "stale_confluence_day_artifact"
}
```

**Preview `agreement_active` / `agreement_total` are
sourced from the artifact's `active_count` /
`available_count`** (Phase 6I-1 amendment) -- exactly
what `daily_signal_board._confluence_active_total`
reads -- so the preview's "X of Y" matches the board's
displayed agreement ratio. The artifact's separate
`agreement_active` / `agreement_total` fields are
validated by § 4.5 but are NOT the preview's display
source; using them would have allowed a malformed
artifact to pass while the board rendered a different
number. The preview also honors the same fallback chain
the board uses: `available_count → total_count →
len(timeframes)`.

`agreement_ratio` = `active_count / available_count`,
so the SPY example above (7 active checks out of 60
K×timeframe cells = 0.1167 ≈ "7 of 60 alignment checks
active") matches the Daily Signal Board's Featured-card
text exactly.

`coverage` is `Full` when every upstream contract passes;
`Partial` otherwise. `rank_eligible` mirrors
`readiness.leader_eligible`. The preview is deterministic
across runs against the same fixture (pinned by
`test_board_row_preview_is_deterministic_across_runs`)
and the per-key sourcing is pinned by
`test_board_row_preview_uses_active_count_not_alias_fields`.

Issue codes: `board_row_incomputable`.

## 5. Recommended-next-operator-action

The validator maps the seven per-contract verdicts +
`leader_eligible` to a single stable next-step string:

| Failed contract | Recommended action |
|---|---|
| cache | `fix_cache_contract` |
| StackBuilder missing | `fix_stackbuilder_contract` |
| StackBuilder ambiguous | `manual_review_required` |
| daily K / MTF | `fix_pipeline_artifacts_contract` |
| Confluence | `fix_confluence_contract` |
| readiness drift | `fix_readiness_verdict_drift` |
| board row incomputable | `manual_review_required` |
| all contracts OK, leader-eligible | `contract_valid_no_action` |
| all contracts OK, not leader-eligible (e.g. persist-skip-lag) | `contract_valid_but_not_leader_eligible` |

The cascade order mirrors the Phase 6H-7 runbook: fix
upstream contracts before downstream ones.

## 6. How this relates to the Phase 6H writer + runbook

The Phase 6H stack ships the operator-facing CLIs and the
runbook + manifest that describe authorized commands.
Phase 6I-1 is the orthogonal **data-side** audit layer:

| Question | Tool |
|---|---|
| What command should the operator run next? | Phase 6H-3 `daily_board_automation_preflight.py` |
| Will the cache/cutoff inequality open for a pipeline write? | Phase 6H-2 `cache_cutoff_watcher.py` |
| What would the dry-run executor do, step by step? | Phase 6H-4 `daily_board_automation_executor.py` |
| Run the authorized live write path (two-key gate). | Phase 6H-5 + 6H-6 `daily_board_automation_writer.py` |
| **Are the SAVED artifacts shaped correctly for the board's ranking system?** | **Phase 6I-1 `confluence_ranking_contract_validator.py`** |
| Where are the rules / authorized commands documented? | Phase 6H-7 runbook + manifest |

The validator runs against existing on-disk artifacts, so
the typical operator flow is:

  1. Operate the Phase 6H stack to produce / refresh
     artifacts (authorized writer with the two-key gate).
  2. Run the Phase 6I-1 validator immediately afterward to
     prove the resulting artifact tree is contract-clean.
  3. If the validator's `recommended_next_operator_action`
     is `contract_valid_no_action` or
     `contract_valid_but_not_leader_eligible`, the run is
     considered successful from a data-shape perspective.
  4. Any other recommendation fixes the named contract
     before the next authorized run.

A future scheduler should call the validator between
authorized writer invocations so a silent contract
regression cannot ride the daily cycle for more than one
day.

## 7. Real-cache SPY validator output (read-only smoke)

```
$ python confluence_ranking_contract_validator.py --ticker SPY
```

```
generated_at                          2026-05-12T08:48:22+00:00
current_as_of_date                    2026-05-11
ticker                                SPY
cache_contract_ok                     true
stackbuilder_contract_ok              true
daily_k_contract_ok                   true
mtf_contract_ok                       true
confluence_contract_ok                true
readiness_contract_ok                 true
board_row_contract_ok                 true
leader_eligible                       false
ranking_blocked_reason                "stale_confluence_day_artifact"
issue_codes                           []
blocking_reasons                      []
selected_stackbuilder_run_id          "seedTC__AWR-D_CP-I_EXPO-D_LLY-I_..."
daily_k_coverage                      [1..12]
mtf_k_coverage                        [1..12]
confluence_last_date                  "2026-05-08"
recommended_next_operator_action      "contract_valid_but_not_leader_eligible"
board_row_preview.ticker              "SPY"
board_row_preview.consensus_signal    "None"
board_row_preview.consensus_signal_value  0
board_row_preview.agreement_active    7
board_row_preview.agreement_total     60
board_row_preview.agreement_ratio     0.11666666666666667
board_row_preview.coverage            "Full"
board_row_preview.as_of_date          "2026-05-08"
board_row_preview.rank_eligible       false
board_row_preview.ranking_blocked_reason "stale_confluence_day_artifact"
```

All seven contracts pass. `leader_eligible=false` is the
persist-skip-lag verdict (Confluence at 2026-05-08;
unpinned cutoff 2026-05-11). The contract data is
correctly shaped; the leader gate is held open by the
Phase 6D-1 persist trim, not by any contract regression.

Visible meaning: **7 of 60 alignment checks active**. The
preview's `agreement_active=7` matches what the Daily
Signal Board's Featured-card renders ("7 of 60 alignment
checks active") because the preview now reads from
`active_count` / `available_count` per § 4.7. Under the
pre-amendment Phase 6I-1 the preview's
`agreement_active` was `0` (sourced from the artifact's
separate `agreement_active` field) -- a different number
from what the board displays. The amendment closes that
drift.

## 8. Validation in this PR (no production writes)

  - py_compile clean on the new module + tests.
  - `test_confluence_ranking_contract_validator.py`:
    **42 passed in 3.41 s** (35 originally + 7 added by the
    post-audit amendment that closed the Confluence
    count-coherence + board-row preview drift).
  - Focused 9-way (validator + preflight + writer +
    pipeline runner + readiness + board + trafficflow
    daily K + MTF bridge + MTF builder):
    **266 passed in 35.94 s** (259 originally + 7 new
    amendment-driven tests; no upstream regression).
  - The real-cache SPY validator smoke (§ 7) is
    strictly read-only; no production path was modified.

Full regression NOT rerun: no shared production module
was modified. The validator is a new module that imports
read-only helpers from existing modules; their public
contracts are unchanged.

## 9. Reference paths

  - Validator module (this phase):
    `project/confluence_ranking_contract_validator.py`
  - Validator tests:
    `project/test_scripts/test_confluence_ranking_contract_validator.py`
  - Phase 6H-7 operator runbook + machine-readable manifest:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`,
    `project/md_library/shared/2026-05-12_PHASE_6H7_OPERATOR_COMMAND_MANIFEST.json`
  - Phase 6H-6 root plumbing doc:
    `project/md_library/shared/2026-05-12_PHASE_6H6_LIVE_WRITER_ROOT_PLUMBING.md`
  - Phase 6H-5 guarded-write executor doc:
    `project/md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`
  - Phase 6H-4 dry-run executor doc:
    `project/md_library/shared/2026-05-12_PHASE_6H4_DAILY_BOARD_AUTOMATION_DRY_RUN_EXECUTOR.md`
  - Phase 6H-3 automation preflight doc:
    `project/md_library/shared/2026-05-12_PHASE_6H3_DAILY_BOARD_AUTOMATION_PREFLIGHT.md`
  - Phase 6G-5 persist-skip-lag contract:
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
    § 6.8 and
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md`
    § 7.
  - Phase 6F-4 MTF persist-skip contract:
    `project/trafficflow_multitimeframe_bridge.py`
    (default `persist_skip_bars=0` for MTF artifacts).
