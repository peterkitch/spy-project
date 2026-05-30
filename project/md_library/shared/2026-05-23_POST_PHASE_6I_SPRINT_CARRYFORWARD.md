# Sprint Carry-Forward Items From Phase 6I

## Status

Active tracking. These items were identified during a Codex classification audit as SPRINT-RELEVANT. Each item should be scoped and addressed in near-term work, separate from the Phase 7+ research items captured in the universe-wide beam scoping doc.

This document is the durable record of sprint-relevant work that should not be lost between sessions. Once an item is completed, its entry should be updated with the resolution date and PR reference, but the entry should remain in this doc as a record.

## Background

After the Phase 6I sprint, which closed with Phase 6I-79 production StackBuilder run evidence merged on main, a Codex classification audit reviewed accumulated carry-forward items from prior sessions plus new items identified during the Phase 6I work.

Items were classified into:

- SPRINT-RELEVANT: imminent work, captured in this doc.
- PHASE-7-PLUS: research and future work, captured in the Phase 7+ universe-wide beam scoping doc.
- ALREADY-RESOLVED: dropped.

This doc covers only the SPRINT-RELEVANT bucket.

## Items

### 1. CLAUDE.md sprint-state drift

Status: RESOLVED 2026-05-30. CLAUDE.md sprint-state drift surface was resolved by PR #352 (merge commit 8a73cad6f6349934cf8421f0093689c0bd71a38a, 2026-05-29). Remaining sub-bullet (d) closes by operator judgment as a likely non-issue, explicitly not by verified or completed audit; no resolving PR exists for strand (d). See Progress note below. Touches every future Claude Code session.

Progress (PR #352, 2026-05-29):

- PR #352 was a documentation-only CLAUDE.md edit (single-commit, single-file). Two surgical edits applied: (1) the opening operational context dropped the stale "post-Phase 5D-1 onboarding / Phase 5C closed / Phase 5D controlled compute in progress" wording and redirected readers to section 6 "Current Sprint State" and the carryforward ledger named in section 8; (2) the "Recent merged phase trail (post Phase 6I-79)" block in section 6 gained a single paragraph forward-referencing item #5 of this ledger for the post-PR-#345 fast-default cleanup chain (PRs #345 through #350, closeout in PR #351). The cleanup chain is documented by reference, not duplicated.
- Sub-bullet (a) "Phase 6I sprint closed, with Phase 6I-77, 6I-78, and 6I-79 merged" - applied via redirect. CLAUDE.md section 6 already describes Phase 6I as closed; PR #352 brought the opening operational context into agreement with it.
- Sub-bullet (b) "Phase 7+ scoping doc exists and has been amended with carry-forward items" - already current in CLAUDE.md section 8 pre-PR #352, which names both this ledger and the Phase 7+ scoping doc as durable tracking docs. No edit was needed.
- Sub-bullet (c) "Next sprint: TrafficFlow headless development" - moot, not pending. Current CLAUDE.md section 6 already supersedes this older wording: TrafficFlow headless development is documented as substantially complete through Phase E PR Epsilon, and the named next direction is "MTF and Confluence integration with canonical TrafficFlow output". Regressing CLAUDE.md to the older sub-bullet wording would be a backward step. PR #352 correctly preserved the newer section 6 wording.
- Sub-bullet (d) "ImpactSearch capture-metric integrity audit is operator-confirmed RESOLVED. Remove or correct the parked-investigation note." - closed by operator judgment, 2026-05-30. The formal ImpactSearch capture-metric integrity audit was never completed and has no citable resolution PR. A 2026-05-30 read-only evidence audit (against the post-PR-#360 main at `d0114b8f67024a57652f83bb2c2db516661f2fc8`) located only one direct artifact: unmerged draft commit `5da1bfe` ("Close phantom ImpactSearch capture audit carry-forward", 2026-05-08) on the local-only branch `impactsearch-phantom-audit-closure`. That branch was never pushed to origin and no PR was ever opened from it; commit `5da1bfe` is recorded as context only and does not meet this ledger's verifiable-resolution bar. The operator does not recall performing the audit and does not vouch for the draft finding. The operator assesses the underlying concern as most likely a non-issue, consistent with the closure having been drafted but never finished. Strand (d) is therefore closed by operator judgment as a likely non-issue, explicitly not as a verified or completed audit. If a new reproducible artifact ever surfaces showing a real capture-integrity problem, it should be opened as fresh work rather than reopening this strand. The parked-investigation note in the historical CLAUDE.md block remains untouched because it is purely historical and does not affect current instructions (its actual current location is CLAUDE.md L997; the earlier reference to L995 in this strand was a two-line drift from later CLAUDE.md edits).

Description: CLAUDE.md still references older Phase 5D and Phase 6I-33 sprint state despite main being past Phase 6I-79. Future Claude Code sessions reading CLAUDE.md will receive inaccurate context about the current state of the project. The doc should be updated to reflect:

- Phase 6I sprint closed, with Phase 6I-77, 6I-78, and 6I-79 merged.
- Phase 7+ scoping doc exists and has been amended with carry-forward items.
- Next sprint: TrafficFlow headless development, gated by a retrospective audit of OnePass / ImpactSearch / StackBuilder headless conversion lessons learned.
- ImpactSearch capture-metric integrity audit is operator-confirmed RESOLVED. Remove or correct the parked-investigation note.

Why sprint-relevant: This is a documentation correctness issue that affects every subsequent Claude Code session. It should be addressed as the final item in the current list-cleanup sequence so the update reflects the most recent state.

Expected scope: Single documentation PR. No code changes. No tests. Source inspection of CLAUDE.md required to identify exact sections needing update.

Open questions:

- Are there other stale references in CLAUDE.md beyond Phase 5D / Phase 6I-33 that should be corrected at the same time?
- Should the doc include a forward-looking "Next sprint: TrafficFlow headless" section, or should sprint planning be kept in a separate doc?

### 2. TrafficFlow refresh callback

Status: RESOLVED 2026-05-30. Original flag resolved by PR #158 (squash 3f3044a, "Phase 5B Item 8: surface TrafficFlow refresh callback errors"). Final classification: OBSOLETE / ALREADY-RESOLVED. See Resolution note below.

Resolution (audit-evidence-based ledger update, 2026-05-30):

- Origin: Phase 5A cleanup ledger item 8 at `md_library/shared/2026-05-05_PHASE_5A_CLEANUP_LEDGER.md:115-125`, deferred from PR #145 (Post Phase 3 Sprint Bug Cleanup audit). The Phase 5A entry explicitly cites PR #145 as the source-of-record for the deferral.
- Original issue: silent error swallowing in the TrafficFlow price-refresh callback path; operators saw absences of expected results without diagnostics. Phase 5A intended outcome: "Surface refresh callback errors as visible operator-facing messages."
- Original resolution: PR #158 (squash `3f3044a`, "Phase 5B Item 8: surface TrafficFlow refresh callback errors"). In-source evidence on current main: `trafficflow.py:200-233` introduces the diagnostic surface (reason-code constants `REFRESH_EXCEPTION` / `REFRESH_SYMBOL_FAILED` / `REFRESH_NO_DATA` / `REFRESH_UNAVAILABLE` / `PRICE_LOAD_FAILED`, bound `_REFRESH_ISSUES_DISPLAY_LIMIT`, and the `_format_trafficflow_issue` helper). The `_refresh` callback collects per-stage failures and appends a bounded `[TRAFFICFLOW:<reason>]` summary to the status text; the previously-silent failure paths are marked by `# Phase 5B Item 8` comments at `trafficflow.py:3173`, `:3202`, and `:3227`, with the bounded-segment construction at `:3322`.
- Headless conclusion: the TrafficFlow headless runner/orchestrator bypass the Dash refresh callback entirely. `trafficflow_runner.py` (module docstring L43-47) makes the repair flags (`--refresh-missing-pkls`, `--refresh-stale-prices`) report-only and explicitly states the runner does not call `trafficflow.refresh_secondary_caches`. `trafficflow_runner._patch_engine_network_surface` (L1750) and `_default_compute_loader` (L1814+) pin the engine's compute-time freshness gate to read-only (`_needs_refresh` returns False, `_fetch_secondary_from_yf` returns empty, `_write_cache_file` and `_persist_cache` raise `engine_price_cache_write_blocked`). `trafficflow_canonical_orchestrator.py` imports `trafficflow_runner` (L48 `import trafficflow_runner as _tfr`; L50 `from trafficflow_runner import (...)`), not `trafficflow`, per its own module docstring at L20 ("does NOT import `trafficflow`").
- Final classification: OBSOLETE / ALREADY-RESOLVED. No precursor PR needed before future TrafficFlow / MTF / Confluence work. The original flag is resolved in source; the headless surface deliberately and fully bypasses the callback's data path with multi-layer fail-closed guards. The open questions captured below were the scope-determination inputs and are now answered by this Resolution note; they are preserved as historical record per the ledger's convention.

Description: A deferred UI / operational issue from a prior audit referencing TrafficFlow's refresh callback behavior. The exact scope of the issue is not documented. Before deciding whether this is current-sprint work or can be re-deferred, a read-only source inspection should determine:

- Is the refresh callback strictly UI-only, such as a Dash callback fired on user interaction?
- Or is it part of data refresh logic that headless TrafficFlow will also rely on?

Why sprint-relevant: TrafficFlow headless development is the next named major sprint. If the refresh callback affects headless behavior, it must be addressed before or during TrafficFlow headless work. If it is strictly UI-only, it can be re-deferred to Phase 7+.

Expected scope: First action is read-only Codex source inspection of `trafficflow.py` to determine which bucket this item belongs in. Implementation work, if any, follows that determination.

Open questions:

- What specific behavior of the refresh callback was flagged in the prior audit?
- Does the headless runner pattern used for OnePass / ImpactSearch / StackBuilder bypass the refresh callback entirely?
- If implementation work is needed, can it be scoped as a precursor PR before the TrafficFlow headless conversion begins?

### 3. Defaults-diff audit

Status: RESOLVED 2026-05-30. Three-strand defaults-diff closeout: allow-decreasing resolved by PR #356 (squash merge commit `74fcc93590f62f4d32ff51c0a25a50fc117c6949`); k-patience resolved by PR #358 (squash merge commit `cf6237486b0d762db5375dc172bd45bfcdca0ef1`); sharpe-eps closed by operator decision Option C (no code change; accepted documented caveats). See Progress note below.

Progress (defaults-diff audit completed, 2026-05-29):

- Defaults-diff audit for StackBuilder is complete. The named Deliverable (a defaults-diff markdown table identifying drift points across `stackbuilder.py` argparse, the Dash UI callback, and `stackbuilder_workbook_runner.py` argparse) was produced from current `main` and recorded against in-source citations.
- The production-relevant comparison is the headless engine argparse defaults vs the `stackbuilder_workbook_runner.py` CLI defaults. Dash UI defaults were inventoried for completeness but are informational only, because the production launch surface is headless.
- `allow-decreasing`: resolved by PR #356 (squash merge commit `74fcc93590f62f4d32ff51c0a25a50fc117c6949`, merged 2026-05-30). The engine argparse in `stackbuilder.py` and the runner CLI in `stackbuilder_workbook_runner.py` now both default `allow_decreasing` to True on the headless path. `--no-allow-decreasing` is the explicit opt-out and resolves to False. The legacy `--allow-decreasing` flag remains parseable and resolves to True for backward compatibility. The Dash UI was already defaulting True and was not changed. This strand is resolved at the source level.
- `k-patience`: resolved by PR #358 (squash merge commit `cf6237486b0d762db5375dc172bd45bfcdca0ef1`, merged 2026-05-30). The engine argparse default in `stackbuilder.py` was aligned from 0 to 1 to match the runner CLI default (already 1 per Phase 6I-78, which exposed the runner's prior hardcoded `k_patience=1` as a CLI flag) and the Dash UI hardcode at `stackbuilder.py:2618` (already 1). The operator decision (Option B in the read-only decision brief) was to align the engine direct CLI to the runner value rather than reverse the Phase 6I-78 preservation choice. The live engine-vs-runner k-patience default divergence is removed. Production runner behavior is unchanged because the runner CLI already defaulted to 1. Engine direct developer/debug runs now tolerate one no-valid-candidate K level before stopping, matching the runner. The engine `k_patience` getattr fallback at `stackbuilder.py:2083` was left unchanged at 0 by design (reached only by programmatic callers that omit the attribute, not by the headless CLI path where argparse always sets the attribute).
- `sharpe-eps`: closed by operator decision Option C (no code change). The engine rejection gate at `stackbuilder.py:2268` (exhaustive search) and `:2343` (beam search) is wrapped in `if not allow_decreasing:`, so under the production default `allow_decreasing=True` (PR #356) the gate is dormant and `sharpe_eps` does not affect algorithm output on default headless runs. Two known divergences are accepted documented caveats, not open work: (a) the `sharpe_eps` value differs across launch surfaces in emitted manifest/summary metadata (runner records `0.01`; engine direct records `1e-6`; persisted at `stackbuilder.py:313`, `:2911`, and `:3094`); (b) in strict opt-out mode with `--no-allow-decreasing`, the gate `if cur_metric <= prev_metric + eps:` is live, and the runner's `0.01` epsilon is stricter than the engine direct `1e-6` (larger eps requires a larger per-K Total Capture improvement to pass; smaller eps lets almost any positive improvement pass). Neither caveat is open work; they are recorded here for future operator awareness.
- `optimize-by`: literal CLI defaults differ (engine None, runner `auto`), but both resolve to `total_capture` before the engine consumes them. No live remediation is needed; any future change is documentation-only.
- Other audited defaults are aligned or are informational-only differences.
- The audit was read-only. No source files, tests, contract docs, generated artifacts, or `.claude/` content were modified.
- Item #3 is RESOLVED. All three defaults-diff strands are settled: allow-decreasing by PR #356; k-patience by PR #358; sharpe-eps by operator decision Option C with the two accepted documented caveats recorded in the sharpe-eps bullet above. The historical Description and Open questions below are preserved as historical record per the ledger's "How This Doc Is Used" convention.

Description: Phase 6I-77 revealed that the LEGACY Dash UI checkbox default for `--allow-decreasing` differs from the `stackbuilder_workbook_runner.py` CLI default. The same engine can produce materially different traversal behavior depending on which interface launched the run. Phase 6I-78 added `--k-patience` runner wiring because that flag was hardcoded in the namespace, not exposed via CLI.

This is likely not the only parameter pair where Dash UI defaults and runner CLI defaults disagree. A systematic audit would compare every parameter pair across:

- `stackbuilder.py` argparse defaults.
- `stackbuilder.py` Dash UI callback defaults.
- `stackbuilder_workbook_runner.py` argparse defaults.

Identifying all drift points proactively prevents future runs from being misconfigured silently.

Why sprint-relevant: This is the same class of bug that caused the Phase 6I-77 8-secondary smoke to stop early across all secondaries. Without an audit, the next similar bug will be caught only after another production run produces unexpected results.

Expected scope: Read-only Codex source inspection task.

Deliverable: a defaults-diff markdown table identifying every drift point between Dash UI defaults, engine argparse defaults, and runner CLI defaults. Remediation prioritization comes after the audit completes.

Open questions:

- Should the audit cover only StackBuilder and its runner, or also OnePass, ImpactSearch, TrafficFlow, Spymaster, Confluence, and MTF as their headless runners come online?
- What is the desired remediation pattern when drift is found: align CLI to UI defaults, align UI to CLI defaults, or document the divergence?
- Should the audit produce a fixture or test that prevents future drift, or is documentation sufficient?

### 4. StackBuilder transparency policy (formerly monthly rebuild cadence)

Status: RESOLVED 2026-05-30. Reframed by operator from cadence-enforcement / scheduling decision to a transparency policy. Current source already implements the policy: missing data is the only hard block; stale-but-present data is surfaced via advisory labels and freshness fields, not blocked; cadence is operator-managed (`manual_supervised`) with no scheduler. No resolving code PR; this closeout cites in-source evidence. See Resolution note below.

Resolution (operator transparency reframe, 2026-05-30):

- Operator reframe: item #4 is a transparency requirement, not a cadence-enforcement or scheduling decision. Do not block a ticker merely because its StackBuilder stack is old; hard-block only when required data is genuinely missing; surface staleness instead of enforcing it; cadence is operator-managed.
- Missing data is the only hard block on the StackBuilder consumer chain. Verified anchors:
  - `trafficflow_runner.py:779` (`selected_build_missing`), `:813` (`selected_build_missing_required_fields`), `:854` (`selected_build_missing_selected_run_dir`) - each is a `status: "refused"` outcome.
  - `trafficflow_runner.py:1184` (`classification: "MISSING"`), `:1225-1226` (`classification: "UNREADABLE"` with `pkl_unreadable` issue), `:1230-1231` (`classification: "INVALID"` with `pkl_top_level_not_dict` issue) - the per-member PKL classification block.
  - `confluence_ranking_contract_validator.py:129` (`ISSUE_STACKBUILDER_MISSING`), `:130` (`ISSUE_STACKBUILDER_SELECTION_AMBIGUOUS`), with the block-return sites at `:471` (missing) and `:478` (ambiguous).
- Stale-but-present data is surfaced, not blocked. Verified anchors:
  - `trafficflow_runner.py:1252-1263` computes `freshness_class` and is explicitly advisory; the inline comment at `:1252-1253` reads "Compute freshness class (advisory; STALE may be overridden by a more severe classification below)".
  - `confluence_ranking_contract_validator.py:464-465` documents the contract as "NO age-based stale window; saved variants are durable regardless of mtime".
  - `daily_signal_board.py:80` (`STALE_DAYS = 30`), `:84` (`COVERAGE_STALE = "Stale"` label), `:930-931` (staleness branch resolves to a label only, not a block) - the board emits the `Stale` label rather than dropping the ticker.
  - `daily_signal_board.py:955` (within the surrounding defensive block read in audit at `:953-956`) - "caller-side bug never causes the row to silently disappear" - the transparency rule expressed in code.
- Cadence is operator-managed; no scheduler exists or is required for this item. Verified anchors:
  - `confluence_stackbuilder_rollout_policy.py:227` (`POLICY_RERUN_CADENCE: str = "manual_supervised"`).
  - `confluence_stackbuilder_rollout_policy.py:219` (locked policy comment: `manual_supervised -- no scheduler`).
- Future public-UI freshness display is Phase 6/7 UI scope, not a current blocker. The underlying freshness signals (build timestamps, `selected_run_id` / `selected_run_dir`, `freshness_class`, `last_updated_at_utc`, `COVERAGE_STALE` labels) are already produced by the StackBuilder, runner, orchestrator, and Daily Signal Board surfaces.
- The historical Description, Why sprint-relevant, Expected scope, and Open questions are preserved below per the ledger's "How This Doc Is Used" convention. The older calendar-month vs trading-day vs rolling-window vs operator-triggered questions are answered as moot by the reframe: cadence is operator-managed, so calendar-form is not enforced anywhere; staleness flowing into a future public UI inherits the transparency rule by design.

Description: Operator stated intent that StackBuilder rebuilds monthly rather than daily. Daily TrafficFlow / MTF / Confluence runs consume StackBuilder's monthly outputs. The existing timestamped run_dir structure already preserves history; only operational policy and scheduling work is needed to enforce the cadence.

Why sprint-relevant: This affects current pipeline planning. The monthly cadence informs decisions about:

- How aggressive the StackBuilder fast-combine optimization needs to be.
- Whether StackBuilder needs cloud compute now or can stay local for the 500-ticker baseline.
- How TrafficFlow / MTF / Confluence headless work consumes StackBuilder outputs through `selected_build.json`.

Expected scope: Documentation work to record the cadence decision. No code work unless the cadence requires explicit scheduling enforcement, which is likely Phase 7+ via Windows Task Scheduler or equivalent.

Open questions:

- Is "monthly" calendar-month-based, such as run on the first trading day of each month, or rolling-30-days based?
- Should the cadence be enforced by tooling, or is it operator-managed?
- Does the cadence interact with the eventual public Wikipedia-of-pattern-finding site, where build refresh frequency may be visible to users?
- How is an off-cadence rebuild triggered when needed?

### 5. Post-PR-#345 fast-default cleanup chain

Status: RESOLVED 2026-05-29. PRs #345 through #350 (squash-merged to main).

Description: PR #345 ("restore fast default test suite with slow and production-smoke discipline") introduced a `pytest.ini` at project root with `slow` and `production_smoke` markers and `addopts = -m "not slow and not production_smoke"`. The first fast-default invocation against the existing test surface revealed 34 residual failures - tests that had drifted out of contract or pinned now-stale behavior while the suite was effectively un-runnable in its prior monolithic form. The cleanup chain remediated each cluster in scope-tight, single-purpose PRs.

Why it qualifies as a carry-forward record: this was not a pre-identified Phase 6I carry-forward item; it emerged as soon as the fast-default sweep ran cleanly. Per CLAUDE.md section 8 the durable record belongs in this doc. Recording the chain here gives future sessions a single citation point linking the residual-fixture / contract-drift / pollution / production-state-dependent buckets to their resolutions.

Per-PR breakdown:

| PR | Squash SHA | Title | Cluster resolved | Delta |
|---|---|---|---|---|
| #345 | 8b45ac8 | restore fast default test suite with slow and production-smoke discipline | infrastructure (markers + addopts); gated 4 hazard tests; new CLAUDE.md section 5b "Test Suite Discipline" | exposed 34 |
| #346 | bd27ea3 | prevent TrafficFlow fixture date decay | hardcoded `tail_date="2026-05-22"` in `_eligible_fixture` / `_canonical_eligible_fixture` decayed past `PRICE_CACHE_STALE_DAYS=7`; replaced with `_fresh_tail_date()` / `_stale_tail_date()` helpers | -23 |
| #347 | f14918f | gate production-state tests behind production_smoke | 3 tests inspecting real `output/`, `cache/`, `signal_library/`, or `price_cache/` operator state gained the production_smoke marker + `PRJCT9_RUN_PRODUCTION_SMOKES=1` env-var gate | -3 |
| #348 | c253dc6 | restore StackBuilder manifest verification and update PR 290 tests | restored Phase 3A consumer-verifies contract by routing `stackbuilder.fallback_load_signal_library` through `provenance_manifest.load_verified_signal_library`; updated PR #290 test surface to pin strict contract; cleared test_f14 + both B12 static guards in one source change | -3 |
| #349 | 13913bc | make import-guard tests resilient to sys.modules pollution | reframed 3 import-guard assertions from global `sys.modules` cleanliness checks to snapshot-before / `importlib.import_module` / diff-after; forbidden sets and audited modules unchanged | -3 |
| #350 | 595c8fb | resolve final fast-default residual failures | (1) redirected dead-since-PR-#289 monkeypatch from `_score_primary_both_modes` to `_score_primary` in `test_3b2b`; (2) added `fp._load_signal_library_quick.cache_clear()` to `test_b3` to isolate from lru-cache pollution that survived `SIGNAL_LIBRARY_DIR` monkeypatch | -2 |

Net: fast-default residual went 34 to 0.

Terminal verification: from `<PROJECT_ROOT>` against pinned `spyproject2` interpreter on 2026-05-29:

    pytest test_scripts/ -q --no-header --tb=short -p no:cacheprovider

Result: 3287 passed, 5 skipped, 5 deselected, 0 failed, 0 errors, 466.61s wall (7m 47s). Reproduced the same green result on the PR #350 validation baseline.

Doctrinal artifacts produced or amended by the chain:

- New `pytest.ini` at project root.
- New CLAUDE.md section 5b "Test Suite Discipline" (verified marker commands documented, including fast default, opt-in `slow or production_smoke`, and full validation via `--override-ini="addopts="`).
- Forensic worktree-comparison pattern established in PR #345 amendment (validating that residual failures are not PR-caused by re-running them against `origin/main` in a temporary worktree).

Operational caveats preserved as carry-forward (not part of this resolution):

- Opt-in `production_smoke` execution can exceed a 300-second bounded validation window on a populated developer machine. PR #345's CLAUDE.md section 5b acknowledges this explicitly; the fast default never selects these tests.
- `signal_library/impact_fastpath.py` retains its `functools.lru_cache` keyed by ticker only. PR #350 isolates `test_b3` from this in the test fixture; the production cache-key shape is unchanged. Future tests exercising the fastpath with monkeypatched `SIGNAL_LIBRARY_DIR` should follow the same `cache_clear()` discipline.
- `stackbuilder.py` still defines `_score_primary_both_modes` (dead since PR #289) as untouched dead code. A future cleanup PR can delete the dead helper alongside any unreached inverse-mode call paths; out of scope for the cleanup chain.

PR references: #345, #346, #347, #348, #349, #350.

## How This Doc Is Used

This doc is the source-of-truth tracker for sprint-relevant work between the Phase 6I sprint and the next named sprint, currently expected to be TrafficFlow headless development.

When an item is being actively worked:

- Its Status field changes from OPEN to IN PROGRESS.
- The PR or PRs associated with the work are linked.

When an item is resolved:

- Its Status field changes to RESOLVED.
- The resolution date and final PR reference are added.
- The entry remains in the doc as historical record.

New sprint-relevant items discovered between now and the next sprint should be appended to this doc rather than tracked in conversation memory.
