# Phase 6I-4 — Upstream research-input audit

**Status:** read-only audit module + tests + this doc.
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
    Phase 6E-5 `signal_engine_cache_refresher`, the
    Phase 6D-4 `confluence_pipeline_runner`, the Phase
    6H-5 `daily_board_automation_writer` are NOT
    imported.
  - **No subprocess. No Dash.**
  - The Phase 6I-1 contract validator IS imported
    read-only for downstream contract comparison. Its
    own no-writes contract carries forward.
  - **The audit does not introduce any StackBuilder
    age-based stale rule.** Phase 6H-3 contract carried
    forward verbatim; saved variants remain durable
    regardless of mtime; ambiguous-tied-mtime remains
    the only mtime-related block.

## 1. Why this exists

Spymaster will eventually be the master upstream audit
UI. Before that work begins, the codebase needs a clean
JSON / reporting backend that confirms the upstream trio
(OnePass / ImpactSearch / StackBuilder) is shaped
correctly to feed the downstream chain (TrafficFlow daily
K → MTF projection → Confluence). Phase 6I-1 / 6I-3
already check the *downstream* contract; Phase 6I-4 is
the matching *upstream* audit.

The audit answers per ticker:

  1. Are the OnePass libraries (daily + intervals) in
     place for the target and for every StackBuilder
     member?
  2. Is the ImpactSearch saved-research output present?
     (Reported but not promoted to a fake Confluence
     failure.)
  3. How many saved StackBuilder variants exist? Which
     is selected by Phase 6H-3's newest-mtime policy?
     Is the selection ambiguous?
  4. Does the selected leaderboard parse cleanly? Does
     it carry K=1..12 coverage? Which member tickers
     does it name?
  5. Is the Spymaster signal-engine cache present for
     the target AND every member named in the
     leaderboard?
  6. Predict each downstream handoff: can_build_daily_
     trafficflow_k, can_project_multitimeframe,
     can_build_confluence.
  7. Compare against the Phase 6I-1 validator's
     downstream contract verdict; surface
     `downstream_contract_invalid` if the chain is not
     present.
  8. Derive one `primary_blocker` string per ticker so
     an operator (or a future scheduler) can route the
     next action without re-deriving the verdict.

## 2. Public API

```python
from upstream_research_input_audit import (
    UpstreamResearchInputAuditState,
    UpstreamResearchInputAuditReport,
    audit_upstream_research_inputs,
    audit_upstream_research_inputs_many,
    main,
    ISSUE_MISSING_ONEPASS_TARGET_LIBRARY,
    ISSUE_MISSING_ONEPASS_MEMBER_LIBRARY,
    ISSUE_MISSING_IMPACTSEARCH_ARTIFACT,
    ISSUE_MISSING_STACKBUILDER_RUN,
    ISSUE_AMBIGUOUS_STACKBUILDER_SELECTION,
    ISSUE_UNREADABLE_STACKBUILDER_LEADERBOARD,
    ISSUE_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
    ISSUE_MISSING_MEMBER_SIGNAL_ENGINE_CACHE,
    ISSUE_MISSING_TARGET_SIGNAL_ENGINE_CACHE,
    ISSUE_DOWNSTREAM_CONTRACT_INVALID,
    BLOCKER_NONE,
    BLOCKER_UPSTREAM_MISSING_ONEPASS_TARGET_LIBRARY,
    BLOCKER_UPSTREAM_MISSING_STACKBUILDER_RUN,
    BLOCKER_UPSTREAM_AMBIGUOUS_STACKBUILDER_SELECTION,
    BLOCKER_UPSTREAM_UNREADABLE_STACKBUILDER_LEADERBOARD,
    BLOCKER_UPSTREAM_INSUFFICIENT_STACKBUILDER_K_COVERAGE,
    BLOCKER_MISSING_TARGET_SIGNAL_ENGINE_CACHE,
    BLOCKER_MISSING_MEMBER_SIGNAL_ENGINE_CACHE,
    BLOCKER_MISSING_MEMBER_ONEPASS_LIBRARY,
    BLOCKER_DOWNSTREAM_ARTIFACT_GAP,
)

state = audit_upstream_research_inputs(
    "SPY",
    cache_dir=None,             # production default
    artifact_root=None,
    stackbuilder_root=None,
    signal_library_dir=None,
    impactsearch_output_dir=None,
    current_as_of_date=None,
)

report = audit_upstream_research_inputs_many(
    ["SPY", "AAPL"],
    current_as_of_date="2026-05-08",
)
```

### CLI

```
python upstream_research_input_audit.py --ticker SPY
python upstream_research_input_audit.py --tickers SPY,AAPL
```

Emits a JSON-serialized
`UpstreamResearchInputAuditReport` to stdout. Exit
codes:

  - `0` audit emitted.
  - `2` invalid CLI arguments (no tickers, unknown flag).
  - `3` unexpected unhandled exception.

`SystemExit` is never propagated from `main()`; argparse
errors are converted to `rc=2`.

## 3. Per-ticker state schema

`UpstreamResearchInputAuditState` carries:

### 3.1 OnePass libraries
  - `onepass_target_library_present` — `{TICKER}_stable_v1_0_0.pkl` under `signal_library/data/stable/`.
  - `onepass_target_library_path` — absolute path or `None`.
  - `onepass_target_interval_libraries_present` —
    intervals out of `("1wk", "1mo", "3mo", "1y")` whose
    `{TICKER}_stable_v1_0_0_<interval>.pkl` is on disk.
  - `onepass_target_interval_libraries_missing` —
    complement of the above.

### 3.2 ImpactSearch saved outputs
  - `impactsearch_xlsx_present` — `{TICKER}_analysis.xlsx` under `output/impactsearch/`.
  - `impactsearch_xlsx_path` — absolute path or `None`.
  - `impactsearch_manifest_sidecar_present` — `<XLSX>.manifest.json` adjacent to the workbook.

### 3.3 StackBuilder run discovery + selection
  - `stackbuilder_run_count` — number of saved seed-run directories under `output/stackbuilder/<TARGET>/`.
  - `stackbuilder_run_ids` — full tuple of seed-run names.
  - `stackbuilder_selected_run_id` — name selected by Phase 6H-3 policy (or `None` when no run / ambiguous tied-mtime).
  - `stackbuilder_selection_policy` — one of `no_stack_available` / `single_available_stack` / `latest_mtime_existing_pipeline_default` / `ambiguous_tied_mtime`.
  - `stackbuilder_selection_warning` — diagnostic string from the preflight selector or `None`.

### 3.4 Selected leaderboard shape
  - `leaderboard_readable` — `combo_leaderboard.xlsx` parses cleanly.
  - `leaderboard_k_coverage` — tuple of K values present (subset of `(1, ..., 12)`).
  - `leaderboard_members` — union of member ticker names across all K rows (in first-seen order).

### 3.5 Target / member coverage
  - `target_signal_engine_cache_present` — `cache/results/{TARGET}_precomputed_results.pkl`.
  - `members_missing_signal_engine_cache` — tuple of leaderboard members whose cache PKL is absent.
  - `members_missing_onepass_library` — tuple of leaderboard members whose OnePass daily library is absent.

### 3.6 Downstream handoff readiness (predictive)
  - `can_build_daily_trafficflow_k` — true iff selection unambiguous + leaderboard readable + K coverage non-empty + target cache present + every leaderboard member cache present + member list non-empty.
  - `can_project_multitimeframe` — `can_build_daily_trafficflow_k` AND OnePass daily library present AND ≥ 1 interval library present.
  - `can_build_confluence` — `can_project_multitimeframe` AND leaderboard K coverage == `set(1..12)`.

### 3.7 Downstream contract verdict
  - `downstream_contract_verdict` — Phase 6I-1 validator's `recommended_next_operator_action` (e.g. `contract_valid_no_action`, `contract_valid_but_not_leader_eligible`, `fix_pipeline_artifacts_contract`, ...).
  - `downstream_contract_valid` — boolean (every contract OK).

### 3.8 Aggregate
  - `issue_codes` — tuple of stable codes (see § 4).
  - `upstream_trio_ready` — true iff none of the upstream-trio-blocking codes fired (member-cache / member-library / downstream-artifact gaps are reported separately).
  - `primary_blocker` — single string in cascade order (see § 5).

## 4. Stable issue codes

| Code | Meaning |
|---|---|
| `missing_onepass_target_library` | Target's OnePass daily library absent. |
| `missing_onepass_member_library` | One or more StackBuilder member tickers lack a daily OnePass library (members listed in `members_missing_onepass_library`). |
| `missing_impactsearch_artifact` | Target's ImpactSearch XLSX absent. **Not promoted into a fake downstream Confluence failure.** Reported as an input gap. |
| `missing_stackbuilder_run` | Zero saved StackBuilder seed-run directories for the target. |
| `ambiguous_stackbuilder_selection` | Newest-mtime tied across multiple variants → Phase 6H-3 policy blocks (operator must pick a variant out of band). |
| `unreadable_stackbuilder_leaderboard` | `combo_leaderboard.xlsx` failed to parse, missing required columns, or all rows had unparseable Members. |
| `insufficient_stackbuilder_k_coverage` | Leaderboard parsed but K coverage ≠ `set(1..12)`. |
| `missing_member_signal_engine_cache` | One or more leaderboard members lack a Spymaster cache PKL. Listed in `members_missing_signal_engine_cache`. |
| `missing_target_signal_engine_cache` | Target's own Spymaster cache PKL absent. Separate from member-cache code. |
| `downstream_contract_invalid` | Phase 6I-1 validator reports at least one downstream contract `False`. The audit's predictive flags may still be `True` (the upstream trio can be ready while the downstream artifacts haven't been built yet). |

## 5. Primary blocker derivation

The audit derives one `primary_blocker` string per ticker
via this cascade (first match wins; the remaining issue
codes are still surfaced in `issue_codes` for full
operator audit):

  1. `missing_onepass_target_library` →
     `upstream_trio_missing_onepass_target_library`.
  2. `missing_stackbuilder_run` →
     `upstream_trio_missing_stackbuilder_run`.
  3. `ambiguous_stackbuilder_selection` →
     `upstream_trio_ambiguous_stackbuilder_selection`.
  4. `unreadable_stackbuilder_leaderboard` →
     `upstream_trio_unreadable_stackbuilder_leaderboard`.
  5. `insufficient_stackbuilder_k_coverage` →
     `upstream_trio_insufficient_stackbuilder_k_coverage`.
  6. `missing_target_signal_engine_cache` →
     `missing_target_signal_engine_cache`.
  7. `missing_member_signal_engine_cache` →
     `missing_member_signal_engine_cache`.
  8. `missing_onepass_member_library` →
     `missing_member_onepass_library`.
  9. `downstream_contract_invalid` →
     `downstream_artifact_gap`.
  10. (no match) → `""` (empty string).

The cascade mirrors the Phase 6H runbook's "fix upstream
before downstream" decision flow. `missing_impactsearch_artifact`
is deliberately absent from the cascade: it appears in
`issue_codes` for operator review but does NOT promote
itself to a `primary_blocker`. ImpactSearch presence is an
input-state observation, not a downstream-chain prereq.

## 6. StackBuilder durability contract (carried forward)

The audit reaffirms the Phase 6H-3 / Phase 6I-1 contract
verbatim:

  - Saved StackBuilder variants under
    `output/stackbuilder/<TARGET>/<seed_run_id>/` are
    durable inputs. They do NOT expire by age.
  - Multiple variants per ticker are first-class.
  - Pipeline default = newest-mtime (preserved by the
    Phase 6H-3 selector and inherited by this audit via
    `daily_board_automation_preflight._discover_stackbuilder_runs`
    + `_resolve_stackbuilder_selection`).
  - A single saved variant is OK.
  - Tied newest-mtime is `ambiguous_tied_mtime` and
    blocks automation. The operator picks; the audit
    refuses to.
  - **No age-based stale rule. No
    `STACKBUILDER_AGE_DAYS` / `STACKBUILDER_STALE_DAYS`
    constant. No "30 days" / "thirty days" threshold.**
    Test `test_audit_carries_no_stackbuilder_age_window`
    enforces this against the audit module's own source.
  - Legacy ImpactSearch XLSX max-age behavior (if any)
    is an ImpactSearch input-path detail and is NOT
    promoted into a StackBuilder stale rule by this
    audit.

## 7. Coupling

The audit imports exactly four project modules
(all read-only):

  - `confluence_pipeline_readiness` (Phase 6C-8) —
    `resolve_current_as_of_date` only.
  - `confluence_ranking_contract_validator` (Phase 6I-1)
    — full validator, invoked read-only for downstream
    contract comparison.
  - `daily_board_automation_preflight` (Phase 6H-3) —
    `_discover_stackbuilder_runs`,
    `_resolve_stackbuilder_selection`, and the
    `SB_POLICY_*` constants.
  - `trafficflow_k_artifact_builder` (Phase 6D-1) —
    `load_stackbuilder_leaderboard` + `iter_k_build_rows`
    (load helpers only; the builder itself is never
    invoked).

Forbidden-imports static guard
(`test_audit_has_no_forbidden_imports`) blocks any
import whose top-level package matches: `yfinance`,
`dash`, `spymaster`, `onepass`, `impactsearch`,
`stackbuilder`, `trafficflow`, `confluence`,
`cross_ticker_confluence`, `daily_signal_board`,
`signal_engine_cache_refresher` (Phase 6E-5),
`confluence_pipeline_runner` (Phase 6D-4),
`daily_board_automation_writer` (Phase 6H-5),
`daily_board_automation_executor` (Phase 6H-4),
`subprocess`.

## 8. Test coverage

`project/test_scripts/test_upstream_research_input_audit.py`
ships 21 tests:

  1. Forbidden-imports static guard.
  2. No-age-window static guard (audit's own source
     carries no `STALE_DAYS` / `AGE_DAYS` / "30 days" /
     "thirty days" substrings).
  3. Full valid fixture passes every flag green.
  4. Multiple StackBuilder variants with clearly-
     staggered mtimes → newest selected without
     ambiguity flag; older variant NOT flagged as stale.
  5. Tied-newest-mtime → `ambiguous_stackbuilder_selection`;
     all downstream flags False;
     `primary_blocker` = `upstream_trio_ambiguous_stackbuilder_selection`.
  6. Missing OnePass target library → issue + blocker;
     `can_project_multitimeframe = False`.
  7. Missing member OnePass library → issue;
     `members_missing_onepass_library` carries the member;
     member-cache code does NOT fire.
  8. Missing member Signal Engine cache → issue
     (separate code); library code does NOT fire;
     `can_build_daily_trafficflow_k = False`.
  9. Missing target Signal Engine cache → issue +
     `primary_blocker = missing_target_signal_engine_cache`;
     `can_build_daily_trafficflow_k = False`.
  10. ImpactSearch missing → issue, but
      `downstream_contract_valid = True` (the audit does
      NOT fake a Confluence failure); `upstream_trio_ready
      = True` (ImpactSearch not in trio-blocking set).
  11. Downstream chain entirely missing →
      `downstream_contract_invalid` + `primary_blocker =
      downstream_artifact_gap`; upstream trio still ready.
  12. Missing StackBuilder run → issue + blocker.
  13. Insufficient K coverage (e.g. K=1..6) → issue +
      blocker; `can_build_confluence = False`.
  14. Unreadable StackBuilder leaderboard (corrupted
      XLSX) → issue + blocker.
  15. No-writes guard: `tmp_path` byte-identical before
      and after the audit.
  16. Aggregate report counts (two tickers; one ready,
      one blocked).
  17. CLI blank ticker → `rc=2`.
  18. CLI no arg → `rc=2`.
  19. CLI unknown flag → `rc=2` (no `SystemExit` leak).
  20. CLI happy path emits valid JSON, `rc=0`.
  21. `to_json_dict()` round-trips through `json.dumps /
      loads`.

## 9. Validation captured at module land

  - `py_compile` clean on both new files.
  - `test_upstream_research_input_audit.py`: 21 passed
    in 2.38 s.
  - Focused 4-way (audit + emitter + validator +
    preflight): 105 passed in 7.51 s.
  - `git diff --check` clean (LF→CRLF normalization
    warnings only; identical to every other repo
    pattern).

Real-cache SPY smoke (production artifact tree,
read-only; only output is JSON to stdout):

```
$ python upstream_research_input_audit.py --ticker SPY
```

```
states[0]:
  ticker                                 SPY
  current_as_of_date                     "2026-05-11"
  onepass_target_library_present         true
  onepass_target_interval_libraries_present
                                         ["1wk","1mo","3mo","1y"]
  onepass_target_interval_libraries_missing  []
  impactsearch_xlsx_present              true
  impactsearch_manifest_sidecar_present  false
  stackbuilder_run_count                 1
  stackbuilder_selected_run_id           "seedTC__AWR-D_..."
  stackbuilder_selection_policy          "single_available_stack"
  leaderboard_readable                   true
  leaderboard_k_coverage                 [1..12]
  leaderboard_members                    14 entries
  target_signal_engine_cache_present     true
  members_missing_signal_engine_cache    []
  members_missing_onepass_library        []
  can_build_daily_trafficflow_k          true
  can_project_multitimeframe             true
  can_build_confluence                   true
  downstream_contract_valid              true
  downstream_contract_verdict            "contract_valid_but_not_leader_eligible"
  issue_codes                            []
  upstream_trio_ready                    true
  primary_blocker                        ""

counts_by_primary_blocker = {"": 1}
upstream_trio_ready_tickers = ["SPY"]
blocked_tickers = []
```

Every upstream input is in place AND every downstream
prediction is true. `downstream_contract_verdict =
"contract_valid_but_not_leader_eligible"` is the
documented persist-skip-lag verdict (unpinned cutoff
2026-05-11 vs Confluence 2026-05-08); the contract data
is valid, just not leader-eligible against the
unpinned cutoff. The audit's role is to confirm the
upstream trio + downstream contract are intact — both
are.

## 10. Confirmation no production writes were run

Four independent checks:

1. **Forbidden-imports static guard.** Module's top-
   level AST imports only the four read-only modules
   listed in § 7. No writer / refresher / pipeline
   runner / live engine.
2. **No-age-window static guard.** Module text does not
   carry any forbidden age-related substring.
3. **No-writes test.** Snapshots every file under
   `tmp_path` before and after
   `audit_upstream_research_inputs`; asserts byte-
   identical state.
4. **Real-cache smoke uses default production roots
   read-only.** The only output is JSON to stdout. No
   file write occurs.

## 11. Scope cuts (deliberate)

  - **No engine execution.** The audit walks files; it
    does not run OnePass / ImpactSearch / StackBuilder /
    TrafficFlow / Confluence / Spymaster batch.
  - **No universe discovery.** Explicit ticker list
    required; the audit does not enumerate the
    StackBuilder tree to derive candidates. (That is
    Phase 6I-5's read-only universe-coverage gap; the
    audit consumes a ticker list, the universe gap
    produces one.)
  - **No fix actions.** The audit emits diagnostics +
    a `primary_blocker` per ticker. Mapping a blocker
    to an operator action (refresh? rerun StackBuilder?
    pick a tied variant?) is downstream of this
    module.
  - **No Spymaster UI integration in this PR.** The
    audit is a clean JSON / dataclass backend. The
    Spymaster master-audit UI will consume it in a
    later phase; that is a UI-layer scope that
    deliberately follows this PR.
  - **No new StackBuilder age window.** Saved variants
    are durable. Tied-mtime is the only mtime-related
    block. The static guard pins this against the
    audit's own source.

## 12. Proposed downstream phases (named, not implemented here)

  - **Phase 6I-5** — read-only execution-log audit
    dashboard (consumes the Phase 6H-5 writer's JSONL
    over a window of dates; surfaces recurring failure
    patterns).
  - **Phase 6I-6** — explicit StackBuilder selection
    contract (per-ticker / per-universe config:
    `pinned_run_id` / `highest_combined_capture` /
    `operator_choice`); no age window introduced.
  - **Phase 6I-7** — Spymaster master-audit UI surface
    consuming this audit's JSON. UI-layer; the JSON
    contract from Phase 6I-4 is the load-bearing
    backend.
  - **Aggregate Confluence p-value** — explicit future
    gap from Phase 6I-2 § 4.3 / § 6.2.

## 13. Reference paths

### New module + tests + doc (this PR)

  - `project/upstream_research_input_audit.py` (new).
  - `project/test_scripts/test_upstream_research_input_audit.py`
    (21 tests).
  - `project/md_library/shared/2026-05-12_PHASE_6I4_UPSTREAM_RESEARCH_INPUT_AUDIT.md`
    (this doc).

### Modules consumed (read-only)

  - `project/confluence_pipeline_readiness.py`
    (`resolve_current_as_of_date` only).
  - `project/confluence_ranking_contract_validator.py`
    (Phase 6I-1; full validator invoked read-only).
  - `project/daily_board_automation_preflight.py`
    (Phase 6H-3 discovery + selection helpers).
  - `project/trafficflow_k_artifact_builder.py`
    (Phase 6D-1 leaderboard load helpers; the builder
    itself is never invoked).

### Cross-references

  - Phase 6I-2 migration map:
    `project/md_library/shared/2026-05-12_PHASE_6I2_MANUAL_WORKFLOW_MIGRATION_MAP.md`
    (§ 6.1 names the read-only universe-coverage gap;
    Phase 6I-4 sits adjacent to that — Phase 6I-4 is
    per-ticker audit, Phase 6I-5 will be the universe
    coverage).
  - Phase 6I-3 cross-ticker emitter:
    `project/md_library/shared/2026-05-12_PHASE_6I3_CROSS_TICKER_CONFLUENCE_RANKING_EMITTER.md`
    (the downstream consumer of validated contracts;
    Phase 6I-4 is the upstream cousin).
  - Phase 6I-1 validator:
    `project/md_library/shared/2026-05-12_PHASE_6I1_CONFLUENCE_RANKING_CONTRACT_VALIDATOR.md`
    (the seven downstream contract checks Phase 6I-4
    consults for `downstream_contract_invalid`).
  - Phase 6H-3 / 6H-7 StackBuilder durability contract:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
    § 11 (the no-age-window rule Phase 6I-4 inherits).
