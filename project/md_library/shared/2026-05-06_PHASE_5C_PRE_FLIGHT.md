# Phase 5C Pre-Flight Document

**Date:** 2026-05-06

**Status:** LOCKED

**Author:** PRJCT9 sprint

**Phase:** 5C (Honest Validation Program - major sub-phase of Phase 5; splits into 5C-1 methodology + 5C-2 engine)

## North Star alignment

5C is the validation program that turns PRJCT9's empirical-pattern-discovery thesis into auditable evidence. The North Star ("noise does not exist - only patterns not yet understood") is meaningful only if the system's claims about which patterns it has discovered survive honest scrutiny: out-of-sample testing, multiple-comparisons control, parity-suite verification, and explicit accounting of the strategies that did NOT survive.

5C is NOT a one-time validation event. It is a methodology + engine that produces validation artifacts on every meaningful run, so that any operator (Peter, future collaborators, eventual external readers) can trace from a claimed result back through the validation evidence that supports it.

5C builds directly on the Phase 5B foundation:

- The canonical multi-primary contract (`multi_primary_contract_v1`, PR #161) is the first contract surface 5C validates.
- The Phase 4A manifest contract (locked future-period-leakage rules) is the persistence substrate 5C validation artifacts participate in.
- The 5B-MP-2c cross-app parity test bootstrapped the parity-suite lock; 5C extends parity-suite design into validation-suite design.

5C decisions are evaluated against North Star direction:

- Choices that surface what the system actually delivers, rather than what it claims, are aligned.
- Choices that respect engine/presentation separation and preserve Phase 4A contract participation are aligned.
- Choices that pull 5C toward Phase 6 marketing polish (selection bias, survivorship-only reporting, hidden negative results) are explicitly out of scope.

## Note on prior sprint state

Phase 5B is complete. Sprint state at the start of 5C:

- Phase 5B per-item cleanup CLOSED (PRs #151-#159).
- Phase 5B-MP scoping LOCKED (PR #160).
- Phase 5B-MP-1 canonical contract methodology LOCKED (PR #161).
- Phase 5B-MP-2 implementation track CLOSED (PRs #162 / #163 / #164).
- 368 tests passing; cross-app parity test pinning canonical-helper convergence.

5C inherits validated patterns from 5B work:

- Reason-code taxonomy (Items 7+9 + 5B-MP-2 per-app prefixes).
- Static-guard test design (Items 2/3/4).
- Behavioral-isolation test design (5B-MP-2b's `aggregate_signal_unchanged_when_partial_status_added` pattern).
- Cross-app parity-suite design (5B-MP-2c's `test_multi_primary_contract_parity_across_*`).
- Doc-PR review/lock workflow (5B-MP scoping + 5B-MP-1 canonical contract).

## Locked Phase 5C behavioral rules

These rules apply to all 5C sub-phases without amendment:

- **Validation is built-in, not bolted-on.** The validation engine produces artifacts as part of every meaningful run, not as a separate retrospective audit. Validation participates in Phase 4A manifest contracts.
- **Honest scope.** Validation reports BOTH the strategies that survived AND the strategies that did not. Survivorship-only reporting is forbidden.
- **No future-period leakage.** Phase 4A's locked rule applies: validation artifacts MUST NOT include forward-looking information that was not available at the validation time-step. Look-ahead bias is a SEVERE violation.
- **Multiple-comparisons accountability.** When the system tests N strategies and reports the K best, validation MUST surface N, not just K, and apply explicit multiple-comparisons control (e.g., Bonferroni, Benjamini-Hochberg, or a documented alternative). The choice of control method is part of 5C-1 methodology.
- **Out-of-sample discipline.** Validation MUST distinguish in-sample fitting from out-of-sample evaluation. Mixing them is a SEVERE violation. The OOS-test design is part of 5C-1 methodology.
- **Parity-suite extension.** Validation extends the cross-app parity suite established in 5B-MP-2c. Where validation outputs differ across apps, divergence is documented (cosmetic) or treated as a violation (semantic).
- **Engine/presentation separation preserved.** Each app keeps its presentation surface; only the validation computation contract converges.
- **Backward compatibility during transition.** Existing operator workflows continue to work through the 5C migration window. Per-app divergent validation surfaces deprecate with operator-facing warnings (Item 1 pattern); hard removal is OUT OF SCOPE for 5C.
- **Reason-code taxonomy aligned.** Validation diagnostics use the established `[SPYMASTER:...]` / `[IMPACTSEARCH:...]` / `[CONFLUENCE:...]` per-app prefixes. New validation-specific reason codes (`validation_failed`, `validation_oos_window_invalid`, etc.) are documented in 5C-1.
- **Phase 6 dependency.** 5C signoff is a prerequisite for Phase 6 launch readiness. Validation gaps surfaced by 5C must close before public-facing surfaces are exposed.

## Purpose

5C produces:

1. **5C-1 - Validation methodology** (doc PR): the canonical specification of what "honest validation" means for PRJCT9 - out-of-sample design, multiple-comparisons control, validation-artifact contract, per-app mapping.

2. **5C-2 - Validation engine** (implementation track): the code that produces validation artifacts on every meaningful run, integrated with Phase 4A manifests.

5C is the foundation that lets 5D (Path 2 backend) and 5G (licensing review) operate against verified claims, and that lets Phase 6 expose any public-facing surface without research-honesty compromise.

## Scope locked

**In scope:**

1. **Phase 5C-1 - Validation methodology** (doc PR):

   - Define "honest validation" operationally: what artifacts every meaningful run produces, how out-of-sample is delineated, what multiple-comparisons control is applied, what parity assertions hold across apps.
   - Validation-artifact contract: `validation_contract_v1` schema (analogous to `multi_primary_contract_v1`). Output shape, status taxonomy, issue taxonomy, manifest schema participation.
   - Per-app mapping: how each of Spymaster, ImpactSearch, Confluence, and StackBuilder produces validation artifacts. Identify divergences and migration needs.
   - Validation reason-code taxonomy stub.
   - Codex preflight REQUIRED for 5C-1 before methodology drafting begins.
   - Output location: `project/md_library/shared/<DATE>_PHASE_5C_VALIDATION_METHODOLOGY.md`.

2. **Phase 5C-2 - Validation engine** (implementation track):

   - Build the validation-artifact engine that produces `validation_contract_v1`-compliant output on every meaningful run.
   - Integrate with Phase 4A manifest contract (validation artifacts are durable).
   - Build the validation-suite test infrastructure (extends 5B-MP-2c's cross-app parity suite into validation-suite).
   - Migrate each app's existing validation surface (if any) to the canonical engine; preserve existing surfaces with deprecation warnings.
   - Add validation-specific reason codes.
   - May split into 5C-2a / 5C-2b / 5C-2c per-app convergence if scope warrants; preflight resolves the split.
   - Codex preflight REQUIRED for each 5C-2 split.

**Out of scope:**

- Phase 5D controlled compute / Path 2 backend (separate sub-phase; depends on 5C-1 methodology lock).
- Phase 5G licensing review (parallel sub-phase).
- Phase 6 public-facing UX (5C signoff is prerequisite, but UX itself is Phase 6).
- New strategy-discovery capabilities (5C validates existing capabilities; new features are post-Phase-6).
- Hard removal of per-app divergent validation surfaces - deprecation only.
- Bring-your-own-data validation ingestion (Phase 7+).
- QC clone integration (parked indefinitely).

## Design principles

1. **Honest > flattering.** Validation reports surface what the system actually delivers, including failures.
2. **Built-in > bolted-on.** Validation artifacts are produced on every meaningful run, not retrospectively.
3. **Manifest contract participation.** Validation artifacts are durable and Phase-4A-tracked.
4. **Multiple-comparisons control is non-negotiable.** N tested / K reported requires an explicit control method.
5. **OOS discipline.** In-sample fitting and out-of-sample evaluation are distinct; mixing is a violation.
6. **Cross-app parity for validation.** Validation contracts converge across apps; divergence is documented or violation.
7. **Deprecation, not removal.** Existing validation surfaces survive through migration with operator-facing warnings.
8. **No new functionality.** 5C validates existing capabilities; new features are post-Phase-6.

## Implementation phasing

**Phase 5C-1: Validation methodology.**

Doc-only. Single PR. Codex preflight REQUIRED before methodology drafting begins. Defines `validation_contract_v1`, OOS design, multiple-comparisons control, validation-artifact manifest schema, and per-app mapping.

**Phase 5C-2: Validation engine.**

Implementation. May split into 5C-2a / 5C-2b / 5C-2c per-app if scope warrants. Codex preflight required for each split. Each implementation PR follows the established 5B-MP-2 per-app workflow: web Claude drafts, Codex signs off, Claude Code implements, Codex audits, merge.

5C-2 depends on 5C-1 lock. Sub-phase ordering within 5C-2 is at Peter's discretion, anchored by which app's existing validation surface is closest to canonical.

## User-facing question 5C answers

For Peter and future operators: "When PRJCT9 reports that strategy X delivered Sharpe ratio Y, what evidence supports that claim? Is it in-sample? Out-of-sample? How many strategies were tested before X was reported? What multiple-comparisons control was applied? What did the strategies that did NOT survive look like?"

For Phase 6 prerequisites: a validation contract grounded in research-honesty principles that public-facing surfaces can rely on without misrepresenting what the system actually delivers.

## Open questions deferred to specific sub-phase preflights

**Phase 5C-1 (validation methodology) preflight questions:**

- What constitutes a "meaningful run" that produces validation artifacts? Every operator-initiated analysis? Only persisted-result runs? Configurable threshold?
- Out-of-sample design: walk-forward? Held-out test set? Time-series cross-validation? Combinatorial Cross-Validated Backtest? Recommend ONE with rationale.
- Multiple-comparisons control method: Bonferroni? Benjamini-Hochberg? Romano-Wolf? Documented alternative? Recommend ONE.
- Validation artifact persistence: on-disk JSON sidecar (analogous to ImpactSearch XLSX manifests)? In-manifest first-class field? Both?
- Survivorship reporting: how does each app surface "K of N strategies survived" in operator-visible UI?
- Existing app validation surfaces: inventory each app's current validation-related code (e.g., Sharpe ratios, p-values, t-statistics already computed) and identify gaps vs `validation_contract_v1`.
- Per-app mapping: which app's existing validation shape is closest to canonical? Anchor for migration order.

**Phase 5C-2 (validation engine) preflight questions:**

- Validation-suite test fixture design (extends 5B-MP-2c parity suite).
- Per-app deprecation message templates for divergent validation surfaces.
- Migration order across apps.
- Manifest schema update: validation artifact fields and Phase 4A schema-isolation considerations.
- Whether the validation engine bootstraps inside the first per-app PR or as a separate infrastructure PR.

## Decisions captured

From the locked Phase 5 Pre-Flight (PR #149):

- 5C splits into 5C-1 methodology + 5C-2 engine.
- Both require Codex preflights.
- 5C is a major sub-phase requiring its own scoping (this document).
- 5C signoff is a Phase 6 launch prerequisite.

From the merged Phase 5B-MP track:

- Multi-primary contract `multi_primary_contract_v1` (PR #161) is the first contract surface 5C validates.
- Cross-app parity-suite design (5B-MP-2c) is the foundation 5C validation-suite extends.
- Per-app prefix taxonomy (`[SPYMASTER:...]` / `[IMPACTSEARCH:...]` / `[CONFLUENCE:...]`) inherits into 5C diagnostic surfacing.
- Behavioral-isolation test pattern (5B-MP-2b) inherits into 5C: validation additions must not change existing computation outputs for the same inputs.
- Deprecation-with-warning pattern (Item 1) inherits into 5C for divergent validation surfaces.

From the merged Phase 4A foundation:

- No future-period leakage rule applies to validation artifacts.
- Manifest contract (series_id + series_metadata schema isolation toward BYO) is the persistence substrate validation artifacts participate in.

## Document status

**LOCKED 2026-05-06 after Codex review.** This document is the locked authority for all Phase 5C sub-phase work. Any changes require explicit amendment with date and rationale.

End of document.
