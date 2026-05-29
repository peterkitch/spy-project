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

Status: OPEN. Highest priority. Touches every future Claude Code session.

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

Status: OPEN. Scope determination needed before classification is finalized.

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

Status: OPEN. Architectural integrity question with current evidence.

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

### 4. Monthly StackBuilder rebuild cadence

Status: OPEN. Operational policy decision.

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
