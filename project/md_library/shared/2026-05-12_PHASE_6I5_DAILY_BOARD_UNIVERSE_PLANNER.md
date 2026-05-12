# Phase 6I-5 — Daily Signal Board universe automation planner

**Status:** read-only planner module + tests + this doc.
No production data refreshed. No production code modified.

**Last updated:** 2026-05-12.

## 0. Scope statement

  - **No production writes.** No `cache/`, `output/`,
    `signal_library/`, or `stackbuilder/` byte changed.
  - **No source refresh.** No yfinance fetch.
  - **No engine execution.** OnePass, ImpactSearch,
    StackBuilder, TrafficFlow, Spymaster, Confluence
    runner are NOT imported.
  - **No writer / refresher / pipeline runner.** The
    Phase 6E-5 refresher, the Phase 6D-4 pipeline
    runner, the Phase 6H-5 writer are NOT imported.
  - **No subprocess. No Dash.**
  - **No Spymaster edit.** This PR ships only the
    backend JSON. The Spymaster master-audit UI is a
    later UI-layer change that consumes this JSON.
  - The planner joins existing read-only layers:
    Phase 6I-4 `upstream_research_input_audit`,
    Phase 6H-3 `daily_board_automation_preflight`,
    Phase 6I-3 `confluence_ranking_emitter`
    (which internally consults the Phase 6I-1 validator).
    Each layer's no-writes contract carries forward.

## 1. Why this exists

The old manual workflow was: force-delete every PKL →
TrafficFlow auto-discovery → paste batch into Spymaster
→ inspect one K table → AI summarization. Phase 6I-2
mapped that chain onto the new automation modules; Phase
6I-3 (ranking emitter) replaced step 6; Phase 6I-4
(upstream audit) replaced steps 1–3 of input audit. The
load-bearing **planning layer** between an upstream
verdict and a supervised production write was still
missing: an operator (or future scheduler) needs to know,
*before* invoking any writer, what the universe of saved
StackBuilder tickers looks like and which buckets each
ticker falls into.

Phase 6I-5 closes that gap. It walks every saved
StackBuilder ticker (or an explicit operator list),
joins the upstream + automation + ranking layers, and
emits one machine-readable JSON plan keyed by ticker +
aggregate bucket lists.

Spymaster will eventually surface this plan as a master
audit panel. **This PR does NOT edit `spymaster.py`.**

## 2. Public API

```python
from daily_board_universe_planner import (
    DailyBoardUniversePlanState,
    DailyBoardUniversePlanReport,
    discover_stackbuilder_universe,
    plan_daily_board_universe,
    main,
    BLOCKER_NONE,
    BLOCKER_DOWNSTREAM_ARTIFACT_GAP,
)

universe = discover_stackbuilder_universe(
    stackbuilder_root=None,  # production default
)
# -> ("AAPL", "AMD", "QQQ", "SPY", ...) sorted alpha

report = plan_daily_board_universe(
    tickers=["SPY", "AAPL"],         # explicit list, OR
    from_stackbuilder_universe=True, # discover, OR both (union)
    cache_dir=None, artifact_root=None,
    stackbuilder_root=None, signal_library_dir=None,
    impactsearch_output_dir=None,
    current_as_of_date=None,
    top_n=10,
)
```

`tickers` and `from_stackbuilder_universe` are
**combinable**: when both are supplied, the universe set
is unioned with the explicit list (duplicates removed,
explicit ordering preserved at the head). The CLI flags
remain mutually exclusive.

### CLI

```
python daily_board_universe_planner.py --ticker SPY
python daily_board_universe_planner.py --tickers SPY,AAPL,QQQ
python daily_board_universe_planner.py --from-stackbuilder-universe
```

JSON to stdout. Exit codes:

  - `0` plan emitted.
  - `2` invalid CLI args (no ticker source, conflicting
    flags, blank ticker).
  - `3` unexpected unhandled exception.

`SystemExit` is never propagated from `main()`.

The CLI does **not** write any file; output is JSON to
stdout only. The spec's "no file output unless
explicitly scoped" rule is followed verbatim — this PR
does not add a `--output-file` flag.

## 3. Per-ticker state schema

`DailyBoardUniversePlanState` (one row per inspected
ticker):

### 3.1 Upstream verdict (Phase 6I-4)
  - `upstream_trio_ready` — bool.
  - `upstream_primary_blocker` — the Phase 6I-4
    cascade verdict (empty string when healthy).
  - `upstream_issue_codes` — tuple of stable issue
    codes from the upstream audit.

### 3.2 StackBuilder selection
  - `stackbuilder_run_count`, `stackbuilder_selected_run_id`,
    `stackbuilder_selection_policy`.

### 3.3 Downstream-handoff predictive flags (Phase 6I-4)
  - `can_build_daily_trafficflow_k`,
    `can_project_multitimeframe`,
    `can_build_confluence`.

### 3.4 Automation preflight (Phase 6H-3)
  - `automation_recommended_action` — one of the
    Phase 6H-3 canonical action strings (`no_action_already_current`,
    `wait_for_cache_ahead_of_cutoff`,
    `refresh_source_cache_then_pipeline`,
    `run_pipeline_only`,
    `select_or_create_stackbuilder_stack_manual`,
    `refresh_multitimeframe_libraries_manual`,
    `blocked_manual_review`).
  - `automation_blocking_reasons` — tuple of reason
    strings carried verbatim from the preflight.
  - `cache_cutoff_action` — what the cache-vs-cutoff
    watcher (Phase 6H-2) reports.
  - `source_cache_date` — the Signal Engine cache's
    last_date (string or `None` when the cache is
    absent / unreadable).

### 3.5 Downstream contract verdict (Phase 6I-1 via 6I-4)
  - `downstream_contract_valid` — bool (all seven
    Phase 6I-1 contracts pass).
  - `downstream_contract_verdict` — the validator's
    `recommended_next_operator_action` string
    (e.g. `contract_valid_no_action`,
    `contract_valid_but_not_leader_eligible`,
    `fix_pipeline_artifacts_contract`).

### 3.6 Leader / ranking surface
  - `current_leader_eligible` — bool from the preflight.
  - `ranking_blocked_reason` — string from the
    Phase 6I-3 row (or `""` when no row).

### 3.7 Phase 6I-3 ranking fields (when available)

Each field is `None` when the Confluence artifact has
not been built yet:

  - `consensus_signal`, `signal_value` (= the row's
    `consensus_signal_value`).
  - `agreement_active`, `agreement_total`,
    `agreement_ratio`.
  - `buy_votes`, `short_votes`, `none_votes`,
    `missing_votes`.
  - `signed_vote_score = (buy_votes − short_votes) /
    available_count`.
  - Performance summary: `total_capture_pct`,
    `sharpe_ratio`, `trigger_days`, `wins`, `losses`,
    `p_value`.

### 3.8 Composite primary blocker
  - `primary_blocker` — upstream primary blocker when
    present; otherwise `downstream_artifact_gap` when
    the downstream contract is invalid; otherwise `""`
    (healthy at every layer).

## 4. Aggregate report schema

`DailyBoardUniversePlanReport`:

  - `generated_at` — UTC ISO-8601 timestamp.
  - `current_as_of_date` — resolved cutoff string.
  - `discovered_stackbuilder_ticker_count` — count of
    ticker directories under `output/stackbuilder/`,
    reported regardless of whether the operator used
    `--from-stackbuilder-universe` (so an explicit
    `--tickers` run still surfaces universe size).
  - `inspected_count` — number of states in `states`.
  - `tickers` — input order (post union + dedup).
  - `top_n` — clamp applied to each ranking tail.
  - `states` — per-ticker rows.
  - `counts_by_automation_action` — `{action: N}`.
  - `counts_by_upstream_primary_blocker` —
    `{blocker: N}`; healthy tickers count under `""`.
  - `counts_by_downstream_contract_verdict` —
    `{verdict: N}`; missing verdicts count under
    `"unknown"`.
  - **Bucket lists** (each preserves input order):
    - `ready_for_pipeline_only_tickers` — action ==
      `run_pipeline_only`.
    - `refresh_source_cache_then_pipeline_tickers` —
      action == `refresh_source_cache_then_pipeline`.
    - `wait_for_cache_ahead_tickers` — action ==
      `wait_for_cache_ahead_of_cutoff`.
    - `stackbuilder_manual_tickers` — action ==
      `select_or_create_stackbuilder_stack_manual`.
    - `upstream_blocked_tickers` —
      `upstream_trio_ready == False`.
    - `downstream_gap_tickers` —
      `upstream_trio_ready == True` AND
      `downstream_contract_valid == False`.
    - `current_leader_eligible_tickers` —
      `current_leader_eligible == True`.
  - **Ranking tails** (full Phase 6I-3 row JSON dicts):
    `positive_tail`, `negative_tail`, `low_buy_tail`.

## 5. Ranking semantics preserved

The planner consumes the Phase 6I-3 emitter and surfaces
its three tails verbatim. The contract:

  - **Both top and bottom matter.** A low-buy /
    inverse-style bottom tail is meaningful market
    evidence (the QQQ-vs-SQQQ pattern), not just
    "bad data."
  - **`agreement_ratio` alone is NOT the sort key.** The
    emitter's tails carry both signal-breadth fields
    (`signed_vote_score`, `agreement_ratio`, vote
    ratios) AND performance-quality fields
    (`total_capture_pct`, `sharpe_ratio`, `wins`,
    `losses`, `p_value`).
  - **`p_value = None`** is preserved through JSON
    serialization (the validator's persist-skip-lag and
    aggregate-p-value gaps remain explicit future
    work).

## 6. StackBuilder durability contract carried forward

The planner inherits the Phase 6H-3 / 6I-4 contract
verbatim:

  - Saved StackBuilder variants are durable inputs.
  - Multiple variants per ticker are first-class.
  - Pipeline default = newest-mtime.
  - Tied newest-mtime is `ambiguous_tied_mtime` and
    blocks automation (routes to manual review;
    surfaces in `upstream_blocked_tickers`).
  - **No 30-day stale window. No age-based stale rule.**
    The planner does NOT introduce any new mtime
    threshold; the Phase 6I-4 audit's static guard
    against age substrings continues to enforce the
    contract at the audit layer.

## 7. Coupling

The planner imports exactly four project modules
(all read-only):

  - `confluence_pipeline_readiness` (Phase 6C-8) —
    `resolve_current_as_of_date` only.
  - `confluence_ranking_emitter` (Phase 6I-3) —
    `emit_confluence_ranking` (which internally consults
    the Phase 6I-1 validator).
  - `daily_board_automation_preflight` (Phase 6H-3) —
    `build_daily_board_automation_plan` +
    `RECOMMENDED_*` action constants.
  - `upstream_research_input_audit` (Phase 6I-4) —
    `audit_upstream_research_inputs_many`.

Forbidden-imports static guard test
(`test_planner_has_no_forbidden_imports`) blocks any
future import whose top-level package matches:
`yfinance`, `dash`, `spymaster`, `onepass`,
`impactsearch`, `stackbuilder`, `trafficflow`,
`confluence`, `cross_ticker_confluence`,
`daily_signal_board`, `signal_engine_cache_refresher`,
`confluence_pipeline_runner`,
`daily_board_automation_writer`,
`daily_board_automation_executor`, `subprocess`.

## 8. Test coverage

`project/test_scripts/test_daily_board_universe_planner.py`
ships 19 tests:

  1. Forbidden-imports static guard.
  2. Empty universe + no `--from-universe` → empty
     report (well-formed, every aggregate empty).
  3. `--from-stackbuilder-universe` over empty tree →
     empty report.
  4. Explicit ticker list bypasses universe discovery
     (asserts a ticker NOT in the universe is still
     inspected; `discovered_stackbuilder_ticker_count`
     reports universe size separately).
  5. Universe discovery finds saved StackBuilder ticker
     directories.
  6. Universe discovery skips hidden / underscore /
     dot-prefix entries (`_progress`, `.tmp`).
  7. Multiple StackBuilder variants per ticker allowed
     (clearly-staggered mtimes → newest selected
     unambiguously; **older variant NOT flagged as
     stale**).
  8. Tied newest-mtime → `ambiguous_tied_mtime` →
     `upstream_trio_ready = False`, ticker surfaces in
     `upstream_blocked_tickers`.
  9. Upstream trio ready + downstream chain missing →
     `downstream_contract_valid = False`,
     `primary_blocker = downstream_artifact_gap`,
     ticker surfaces in `downstream_gap_tickers`.
  10. Cache `last_date == cutoff` →
      `automation_recommended_action = wait_for_cache_ahead_of_cutoff`;
      ticker surfaces in `wait_for_cache_ahead_tickers`;
      `cache_cutoff_action = pipeline_output_lags_persist_skip`.
  11. Ranking tails include positive AND low_buy cases
      (BUYHI in positive_tail; NOBUY with `buy_votes = 0`
      in low_buy_tail).
  12. Deterministic alphabetical tie-break: three
      identical fixtures emit `["AAAA", "BBBB", "CCCC"]`
      in positive_tail.
  13. `to_json_dict()` round-trips through `json.dumps /
      loads`; tail rows preserve the full ranking row
      schema (`signed_vote_score`, `total_capture_pct`,
      `p_value`).
  14. No-writes guard: `tmp_path` byte-identical before
      and after `plan_daily_board_universe`.
  15. CLI no ticker source → `rc=2` with structured
      error JSON to stderr.
  16. CLI blank ticker → `rc=2`.
  17. CLI unknown flag → `rc=2` (no `SystemExit` leak).
  18. CLI happy path emits valid JSON, `rc=0`.
  19. From-universe + explicit list union semantics
      (NEWBIE + universe `{SPY, AAPL}` →
      `{NEWBIE, AAPL, SPY}`; discovered count = 2).

## 9. Validation captured at module land

  - `py_compile` clean on both new files.
  - `test_daily_board_universe_planner.py`: 19 passed
    in 3.06 s.
  - Focused 5-way (planner + audit + emitter +
    validator + preflight): 128 passed in 9.57 s.
  - `git diff --check` clean (LF→CRLF normalization
    warnings only; identical to every other repo
    pattern).

Real-cache SPY smoke (production artifact tree,
read-only; the only output is JSON to stdout):

```
$ python daily_board_universe_planner.py --ticker SPY
```

Compact summary of the resulting JSON:

```
discovered_stackbuilder_ticker_count   248
inspected_count                        1

states[SPY]:
  upstream_trio_ready                  true
  upstream_primary_blocker             ""
  stackbuilder_run_count               1
  stackbuilder_selection_policy        "single_available_stack"
  can_build_daily_trafficflow_k        true
  can_project_multitimeframe           true
  can_build_confluence                 true
  automation_recommended_action        "wait_for_cache_ahead_of_cutoff"
  cache_cutoff_action                  "pipeline_output_lags_persist_skip"
  source_cache_date                    "2026-05-11"
  downstream_contract_valid            true
  downstream_contract_verdict          "contract_valid_but_not_leader_eligible"
  current_leader_eligible              false
  consensus_signal                     "None"
  signed_vote_score                    0.05
  total_capture_pct                    42.44
  p_value                              null
  primary_blocker                      ""

positive_tail                          [SPY]
negative_tail                          []
low_buy_tail                           [SPY]
wait_for_cache_ahead_tickers           [SPY]
current_leader_eligible_tickers        []
counts_by_automation_action            {"wait_for_cache_ahead_of_cutoff": 1}
```

The universe carries 248 saved StackBuilder ticker
directories. SPY's verdict is the documented persist-
skip-lag state: upstream trio + downstream contract
both green, but the unpinned cutoff (2026-05-11) is
ahead of the Confluence artifact (2026-05-08) so
`current_leader_eligible = false` and the automation
verdict is `wait_for_cache_ahead_of_cutoff`. SPY's slim
net-Buy posture lands it in both `positive_tail` (signed
> 0) AND `low_buy_tail` (`buy_ratio ≤ 0.10`) — exactly
the Phase 6I-3 inverse-confirmation framing carried
forward.

## 10. Confirmation no production writes were run

Four independent checks:

1. **Forbidden-imports static guard.** Module's top-
   level AST imports only the four read-only modules
   listed in § 7. No writer / refresher / pipeline
   runner / live engine / yfinance / dash / subprocess.
2. **No-writes test.** Snapshots every file under
   `tmp_path` before and after
   `plan_daily_board_universe`; asserts byte-identical
   state.
3. **Real-cache smoke uses default production roots
   read-only.** The only output is JSON to stdout. No
   file write occurs.
4. **No Spymaster edit.** This PR does NOT modify
   `spymaster.py`. Future Spymaster integration is a
   separate UI-layer change that consumes the JSON
   contract pinned here.

## 11. Future Spymaster integration

Spymaster will eventually surface this plan as a master
audit panel. The integration path:

  - Spymaster invokes `plan_daily_board_universe(...)`
    via in-process import (no subprocess, no CLI
    round-trip) and consumes the
    `DailyBoardUniversePlanReport` dataclass directly.
  - The UI renders:
    - Per-ticker rows keyed by `automation_recommended_action`
      bucket (Ready for pipeline / Refresh + pipeline /
      Wait for cache ahead / StackBuilder manual /
      Upstream blocked / Downstream gap).
    - The three ranking tails as collapsible panels.
    - Leader-eligible tickers as a separate spotlight.
  - The Spymaster surface remains read-only. Write
    actions stay routed through the Phase 6H-5
    `daily_board_automation_writer` with the two-key
    `--write` + `PRJCT9_AUTOMATION_WRITE_AUTH` gate.

The integration is **out of scope for this PR**. This
doc names the contract; the UI work is a later
UI-layer change.

## 12. Scope cuts (deliberate)

  - **No file output.** JSON to stdout only. An
    optional `--output-file` flag would broaden the
    surface; the spec defers that.
  - **No universe-level summary heuristic.** The
    planner emits structured data; an opinionated
    "what should the operator do next at the universe
    level?" suggestion is downstream.
  - **No fix actions.** The planner reports verdicts
    and buckets; routing a verdict to a writer
    invocation is downstream of this module (Phase
    6H-7 runbook + Phase 6H-5 writer).
  - **No Spymaster UI integration in this PR.**
  - **No new StackBuilder age window.** The audit
    layer's static guard against age substrings
    continues to enforce the contract.
  - **No aggregate p-value yet.** The field passes
    through `p_value = None` from the Phase 6I-3
    emitter; aggregate cross-K/timeframe p-value
    remains a named future gap (Phase 6I-2 § 4.3).

## 13. Proposed downstream phases (named, not implemented here)

  - **Phase 6I-6** — explicit StackBuilder selection
    contract (per-ticker / per-universe config:
    `pinned_run_id` / `highest_combined_capture` /
    `operator_choice`; no age window introduced).
  - **Phase 6I-7** — Spymaster master-audit UI surface
    consuming this planner's JSON. UI-layer.
  - **Phase 6I-8** — supervised first authorized
    production run (operational: Phase 5G licensing +
    Phase 5C validation integration + scheduler +
    alerting wired through the Phase 6H-7 runbook).
  - **Aggregate Confluence p-value** (no phase letter
    yet) — explicit future gap from Phase 6I-2 § 4.3
    / § 6.2.

## 14. Reference paths

### New module + tests + doc (this PR)

  - `project/daily_board_universe_planner.py` (new).
  - `project/test_scripts/test_daily_board_universe_planner.py`
    (19 tests).
  - `project/md_library/shared/2026-05-12_PHASE_6I5_DAILY_BOARD_UNIVERSE_PLANNER.md`
    (this doc).

### Modules consumed (read-only)

  - `project/confluence_pipeline_readiness.py`
    (`resolve_current_as_of_date` only).
  - `project/confluence_ranking_emitter.py`
    (Phase 6I-3).
  - `project/daily_board_automation_preflight.py`
    (Phase 6H-3).
  - `project/upstream_research_input_audit.py`
    (Phase 6I-4).

### Cross-references

  - Phase 6I-4 upstream audit:
    `project/md_library/shared/2026-05-12_PHASE_6I4_UPSTREAM_RESEARCH_INPUT_AUDIT.md`.
  - Phase 6I-3 cross-ticker emitter:
    `project/md_library/shared/2026-05-12_PHASE_6I3_CROSS_TICKER_CONFLUENCE_RANKING_EMITTER.md`.
  - Phase 6I-2 migration map:
    `project/md_library/shared/2026-05-12_PHASE_6I2_MANUAL_WORKFLOW_MIGRATION_MAP.md`
    (§ 6.1 named the read-only universe-coverage gap;
    Phase 6I-5 closes the planner side; § 6.2 named the
    cross-ticker emitter which Phase 6I-3 already
    closed).
  - Phase 6H-7 production runbook:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
    (the operator stack this planner is meant to sit
    alongside as the planning-layer JSON backend).
