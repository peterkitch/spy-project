# Phase 5B-MP Pre-Flight Document

**Date:** 2026-05-06

**Status:** LOCKED

**Author:** PRJCT9 sprint

**Phase:** 5B-MP (Multi-Primary Reconciliation — dedicated sub-phase elevated from 5A ledger Item 10)

## North Star alignment

5B-MP closes the cross-app reconciliation loop the 5A cleanup ledger explicitly elevated to a dedicated sub-phase. Three apps currently implement multi-primary semantics in three structurally divergent shapes:

- **Spymaster** exposes a Multi-Primary Signal Aggregator UI surface (per the 2025-08-28 Spymaster fix doc).
- **ImpactSearch** accepts multiple primary tickers and evaluates them independently against secondary ticker returns — a different shape from aggregation.
- **Confluence** has a separate multi-interval virtual-library implementation shape documented as SpyMaster parity; 5B-MP verifies whether semantic divergence remains beyond the documented structural difference.

The three apps do not yet have a canonical multi-primary contract pinned in shared methodology. The canonical multi-primary consensus semantics ARE already defined in `project/md_library/shared/2026-04-30_PRJCT9_ALGORITHM_SPEC_v0_5.md` §18: per-bar consensus across primaries, all non-None primary signals must agree on direction (`[D]` direct or `[I]` inverse), disagreement collapses to `None` (mute). 5B-MP-1 codifies §18 as `multi_primary_contract_v1` and maps each app's existing implementation to it; 5B-MP-2 builds parity-suite test infrastructure and migrates per-app surfaces onto the canonical contract (or deprecates per-app divergent surfaces with explicit operator-facing migration paths).

5B-MP implementation decisions are evaluated against North Star direction:

- Choices that preserve research-honesty are aligned (canonical contract is documented; divergence becomes visible rather than hidden).
- Choices that preserve engine/presentation separation are aligned (each app keeps its presentation surface; only the underlying multi-primary computation contract is canonicalized).
- Choices that pull 5B-MP toward Phase 6 polish (public-facing multi-primary UX, accessibility branding) defer to Phase 6.

## Note on prior sprint state

5B-MP's elevation from "Item 10 in the 5A ledger" to "dedicated sub-phase" was made by the 5A ledger itself with rationale: structural divergence between the three apps' multi-primary shapes is too large to hide inside a routine 5B cleanup PR. Items 7 (OnePass error UX, PR #155) and 9 (ImpactSearch error taxonomy, PR #156) established the per-app rejection_out pattern that this sub-phase inherits where applicable. Item 4 (matrix concept naming consolidation, PR #153) preserved Spymaster's Multi-Primary Signal Aggregator label as a legitimate qualified usage; that label survives 5B-MP unchanged unless the canonical contract explicitly migrates it.

## Locked Phase 5B-MP behavioral rules

These rules apply to all 5B-MP sub-phases without amendment:

- The canonical multi-primary contract `multi_primary_contract_v1` codifies Algorithm Spec §18 (per-bar consensus; all non-None primary signals must agree on direction; disagreement → None). 5B-MP-1 adopts §18 as the canonical surface and documents per-app mapping; it does NOT invent new aggregation semantics (no weights, no union, no voting).
- Engine/presentation separation strictly preserved. Each app keeps its UI surface; only the underlying computation contract converges.
- Backward compatibility during transition. Existing operator scripts must keep working through the 5B-MP migration window. Per-app divergent surfaces deprecate with operator-facing warnings (analogous to Item 1's StackBuilder CLI deprecation pattern). Hard removal is OUT OF SCOPE for 5B-MP unless a later amendment explicitly authorizes it.
- Phase 4A manifest contract preserved. Any new durable 5B-MP artifact or run output participates in the Phase 3/Phase 4A manifest contract (no future-period leakage; series_id + series_metadata schema isolation toward BYO). Existing UI-only surfaces are NOT converted into ad-hoc durable outputs by 5B-MP work.
- Cross-app reason-code taxonomy aligns with Items 7+9 patterns where applicable. Confluence and Spymaster do not yet have the same rejection_out surface as OnePass and ImpactSearch; introducing parallel rejection_out infrastructure to those apps is OUT OF SCOPE for 5B-MP unless its preflight explicitly scopes it. Where 5B-MP-2 does emit operator diagnostics, the prefix shape follows the per-app convention: `[SPYMASTER:...]` / `[IMPACTSEARCH:...]` / `[CONFLUENCE:...]`.
- Parity-suite test infrastructure analogous to project/test_scripts/test_within_engine_parity.py is mandatory. The canonical contract is verified by tests that drive all three apps with the same multi-primary inputs and assert convergent outputs (modulo documented per-app cosmetic differences).

## Purpose

5B-MP produces the canonical multi-primary contract `multi_primary_contract_v1` (codifying Algorithm Spec §18), parity-suite test infrastructure, and per-app convergence/deprecation that closes the cross-app divergence the 5A ledger flagged. 5B-MP is NOT new multi-primary functionality. 5B-MP IS the reconciliation that makes existing multi-primary surfaces audit-traceable, parity-verified, and operator-honest.

## Scope locked

**In scope:**

1. **Phase 5B-MP-1 — Canonical contract methodology** (doc PR):
   - Adopt Algorithm Spec §18 as `multi_primary_contract_v1`. Document the canonical contract's input shape, output shape, consensus semantics, and failure semantics directly from §18.
   - Map each of the three apps' current shapes to the canonical contract, identifying any structural-vs-semantic divergence per-app.
   - Specify per-app migration plan: which app surfaces converge to the canonical computation, which deprecate, which stay as divergent-but-documented (with explicit rationale).
   - Lock the contract version (`multi_primary_contract_v1`) before implementation begins.
   - Codex preflight REQUIRED for 5B-MP-1 because the §18-adoption questions and per-app mapping must be resolved before the methodology doc PR is drafted (see Open questions below).
   - Output location: `project/md_library/shared/<DATE>_PHASE_5B_MP_CANONICAL_CONTRACT.md`

2. **Phase 5B-MP-2 — Parity-suite + per-app convergence** (implementation track):
   - Build the parity-suite test infrastructure (analogous to project/test_scripts/test_within_engine_parity.py) that drives all three apps with identical multi-primary inputs and asserts convergent outputs.
   - Migrate each app's multi-primary computation to the canonical contract.
   - Deprecate per-app divergent surfaces with operator-facing warnings; hard removal is OUT OF SCOPE unless a later amendment explicitly authorizes it.
   - Add reason-code taxonomy for multi-primary-specific invalid/failure states where applicable (e.g., `multi_primary_input_invalid`, `multi_primary_parity_violation`, `multi_primary_aggregation_failed`). Consensus disagreement under Algorithm Spec §18 is normal mute behavior, not an error, unless a later preflight explicitly scopes operator diagnostics for it. Reason codes use per-app prefix `[SPYMASTER:...]` / `[IMPACTSEARCH:...]` / `[CONFLUENCE:...]`.
   - Update Spymaster Help UI / ImpactSearch UI / Confluence UI text to reference the canonical contract (per Item 3's pattern).
   - May split into 5B-MP-2a (parity-suite + first-app migration), 5B-MP-2b (second-app migration), 5B-MP-2c (third-app migration) if scope warrants — preflight resolves the split.
   - Codex preflight REQUIRED for each 5B-MP-2 split.

**Out of scope:**

- Phase 5C validation methodology (separate sub-phase; multi-primary outputs participate in 5C validation but 5B-MP doesn't define validation).
- Phase 5D controlled compute / Path 2 backend (separate sub-phase).
- Phase 5G licensing review (parallel sub-phase).
- Public-facing multi-primary UX (Phase 6).
- New multi-primary aggregation semantics (weighted, union, voting, etc.) — §18 defines consensus; 5B-MP adopts §18 as-is and does not invent alternatives.
- New multi-primary capabilities of any kind — 5B-MP reconciles existing semantics; new features are post-Phase-6.
- Hard removal of per-app divergent multi-primary surfaces — deprecation only; removal requires explicit later amendment.
- Introducing rejection_out infrastructure to Spymaster or Confluence beyond what's required for multi-primary diagnostic surfacing.
- QC clone integration (parked indefinitely).
- Bring-your-own-data multi-primary ingestion (Phase 7+).

## Design principles

1. **§18 is the contract.** Algorithm Spec §18 already defines multi-primary consensus. 5B-MP-1 adopts it; 5B-MP does not invent a new contract.
2. **One canonical contract.** Three apps, one contract, locked at `multi_primary_contract_v1`. Deviation is the bug; convergence is the goal.
3. **Parity-suite is the lock.** Without parity tests asserting cross-app convergence on identical inputs, the contract isn't real.
4. **Deprecation, not removal.** Operator scripts continue to work through the migration window. Per-app divergent surfaces emit warnings; hard removal is post-5B-MP work.
5. **Documented divergence.** If an app's surface is intentionally different from canonical (for legitimate UX reasons), the divergence is documented in 5B-MP-1, not silently preserved.
6. **No new functionality.** 5B-MP reconciles; it doesn't extend. New multi-primary features are post-Phase-6.
7. **Honest UI.** Each app's user-facing multi-primary text reflects the canonical contract semantics, not legacy per-app interpretations.

## Implementation phasing

**Phase 5B-MP-1: Canonical contract methodology.**
Doc-only. Single PR. Codifies §18 as `multi_primary_contract_v1`, documents per-app mapping, locks the migration plan. Codex preflight REQUIRED before methodology drafting begins.

**Phase 5B-MP-2: Parity-suite + per-app convergence.**
Implementation. May split into 5B-MP-2a / 5B-MP-2b / 5B-MP-2c if per-app scope is large enough. Codex preflight required for each split. Each implementation PR follows the established 5B per-item workflow: web Claude drafts → Codex sign-off → Claude Code implements → Codex audits → merge.

5B-MP-2 depends on 5B-MP-1 lock. Otherwise sub-phase ordering within 5B-MP-2 (which app to migrate first) is at Peter's discretion.

## User-facing question 5B-MP answers

For Peter and future operators: "Does 'multi-primary' mean the same thing across Spymaster, ImpactSearch, and Confluence? If I run the same multi-primary inputs through all three, do I get the same answer (modulo presentation)? What does the operator-facing label 'Multi-Primary' actually compute?"

For Phase 6 prerequisites: a single canonical multi-primary contract grounded in Algorithm Spec §18 that public-facing surfaces can rely on without operator confusion about which app's interpretation is in effect.

## Open questions deferred to specific sub-phase preflights

**Phase 5B-MP-1 (canonical contract methodology) preflight questions:**
- Does `multi_primary_contract_v1` adopt Algorithm Spec §18 as-is, or are minor codification refinements needed (e.g., explicit handling of edge cases §18 leaves implicit)?
- For each of Spymaster, ImpactSearch, and Confluence: how does the app's current multi-primary implementation map to §18? Where is the divergence structural (different code path achieving same semantics) versus semantic (different actual behavior)?
- Which app's current implementation is closest to §18 (anchor for migration order in 5B-MP-2)?
- Does ImpactSearch's independent-evaluation-of-multiple-primaries shape get folded into the canonical aggregate path, stays as a documented alternate mode, or both (canonical for aggregate use; alternate for batch-evaluation use)?
- Does Confluence's documented SpyMaster-parity claim hold against §18 once the contract is codified, or does the verification surface a semantic gap?
- Output shape question: does the canonical contract require per-primary detail in addition to the aggregate signal, or aggregate only?

**Phase 5B-MP-2 (parity-suite + per-app convergence) preflight questions:**
- Parity-suite test fixture design (how to drive all three apps with identical inputs without app-specific setup leaking).
- Acceptable cosmetic divergence (UI labels, ordering) vs unacceptable semantic divergence.
- Per-app deprecation message templates (mirror Item 1's StackBuilder pattern? or distinct?).
- Migration order: which app converges first.
- Risk tolerance for behavioral change in Spymaster's Multi-Primary Signal Aggregator UI (the most operator-visible surface).
- Whether multi-primary diagnostic reason codes get plumbed into Spymaster/Confluence at the same fidelity as OnePass/ImpactSearch, or only the minimum needed for parity-suite assertions.

## Decisions captured

From Peter (May 5 2026, via 5A ledger Item 10 elevation):

- Multi-primary reconciliation is too large for a routine 5B cleanup PR.
- Dedicated sub-phase (5B-MP) with its own preflight scoping.
- Canonical-contract preflight required before implementation.
- Parity-suite test infrastructure analogous to test_within_engine_parity.py is mandatory.
- Per-app deprecation paths preserve backward compatibility through migration; hard removal is out of scope.

From the merged 5B per-item track (PRs #151–#159 closing all per-item cleanup work):

- Algorithm Spec §18 is the canonical multi-primary consensus definition.
- Reason-code taxonomy patterns from Items 7+9 inherit into 5B-MP where applicable (Spymaster/Confluence do not yet have parallel rejection_out surfaces; introducing them broadly is out of scope).
- Static guard test patterns from Items 2/3/4 inherit into 5B-MP (parity-suite is the analog).
- Deprecation-with-warning pattern from Item 1 inherits into 5B-MP (per-app divergent surface deprecation).

## Document status

**LOCKED 2026-05-06 after Codex review.** This document is the locked authority for all 5B-MP sub-phase work. Any changes require explicit amendment with date and rationale.

End of document.
