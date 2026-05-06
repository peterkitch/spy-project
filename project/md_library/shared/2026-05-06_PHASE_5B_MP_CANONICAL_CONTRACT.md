# Phase 5B-MP-1 Canonical Multi-Primary Contract

**Date:** 2026-05-06

**Status:** LOCKED

**Author:** PRJCT9 sprint

**Phase:** 5B-MP-1 (Canonical contract methodology — first sub-phase of 5B-MP)

## 1. Scope and source authority

This document defines `multi_primary_contract_v1`, the canonical multi-primary signal contract for PRJCT9. It is the locked authority for all 5B-MP-2 implementation work (parity-suite + per-app convergence).

Source authorities:

- Locked 5B-MP scoping at `project/md_library/shared/2026-05-06_PHASE_5B_MP_PRE_FLIGHT.md` (PR #160).
- Algorithm Spec §18 at `project/md_library/shared/2026-04-30_PRJCT9_ALGORITHM_SPEC_v0_5.md:210` (canonical multi-primary consensus).
- Reference implementation: `canonical_scoring.combine_consensus_signals` (the parity-suite anchor; see §12).

Per the locked 5B-MP rules: this doc adopts §18; it does NOT invent new aggregation semantics.

## 2. `multi_primary_contract_v1` Definition

`multi_primary_contract_v1` is the bar-level consensus contract across N primary signal series produced for a shared evaluation grid. The contract has four parts: input, consensus, output, and failure semantics.

## 3. Input Contract

A multi-primary evaluation accepts a set of N >= 1 primary members. Each primary member is:

- A primary ticker (operator-supplied).
- A direction tag: `[D]` (direct, signal kept as-is) or `[I]` (inverse, signal sign-flipped).
- An optional mute flag (operator opts the primary out of consensus while leaving it visible in detail output).
- A signal series produced for the primary on a declared evaluation grid. For multi-interval surfaces, each interval is a separate contract invocation with its own declared grid.

Codified rules:

- **Input shape:** already-aligned per-primary signal series on the declared evaluation grid. Alignment (for example, calendar intersection across primaries) happens before the contract is invoked; the contract does NOT perform alignment.
- **Normalization:** ticker normalization (case, whitespace, alias resolution) happens before contract input. Inputs are normalized identifiers.
- **Duplicate policy:** canonical contract input MUST contain unique active (non-muted) normalized tickers. Duplicate active normalized tickers are `invalid_input` if they reach the contract. UI layers MAY offer a pre-contract de-duplication assist during migration, but only with an explicit operator-visible warning before invoking the contract. Duplicates must NEVER become implicit weights.
- **Single-primary degradation:** when N=1, or all but one primary is muted, the contract degrades to single-primary semantics. The lone active primary's signal IS the aggregate signal after `[D]`/`[I]` direction tagging.
- **Ordering invariance:** primary-list order MUST NOT affect the aggregate signal. Order MAY affect display/detail output ordering only.

## 4. Consensus Contract

Per Algorithm Spec §18, applied per evaluation bar:

1. For each primary, apply its direction tag (`[D]` keeps signal; `[I]` flips sign).
2. If a primary is muted, exclude it from consensus for this evaluation entirely. It is treated as not present.
3. Among the remaining non-muted primaries, take per-bar signals.
4. **Consensus rule:** ignore `None` values. Of the non-`None` signals at this bar:
   - If ALL agree on direction (all `Buy` or all `Short`), emit that signal.
   - If they disagree, emit `None` (mute).
   - If all are `None`, emit `None`.

There is NO tie-breaker. Disagreement always mutes. There is NO weighting, voting, or quorum semantics.

Edge case codification:

- **All-`None` primary as valid abstaining member:** a primary that contributes zero signals across the evaluation grid is a valid abstaining member. Its presence does NOT invalidate the aggregate. Contrast with missing primary below.
- **Single-primary edge:** when N=1 active, the consensus rule degenerates to emitting the lone primary's signal at each bar.

## 5. Output Contract

Required output (every implementation must produce):

- `aggregate_signal`: the per-bar consensus signal series, values in `{Buy, Short, None}`, on the declared evaluation grid.
- `status`: a contract-status indicator. Allowed values:
  - `valid` — all primary inputs resolved; aggregate computed across the full grid.
  - `partial` — aggregate computed with at least one primary missing/failed; operator should treat result with reduced confidence.
  - `invalid_input` — duplicate active primaries, malformed direction tags, all primaries muted, or other input-contract violations.
  - `unavailable` — no active primary signal series could be produced (data fetch failures, library missing, etc.); aggregate is undefined.
  - `no_overlap` — primaries' evaluation grids do not overlap; aggregate is undefined.
  - `no_triggers` — all bars resolved to `None`; no consensus signals exist across the grid.
- `issues`: list of structured issue records (see §11) describing any non-`valid` status.

Optional output:

- `per_primary_detail`: optional detail output. The contract defines its shape so callers can request it consistently, but implementations are not required to emit it by default. 5B-MP-2 per-app preflights decide which UI surfaces request and display it. Shape: for each primary, the post-direction-tag adjusted signal series on the same grid, plus metadata (original ticker, normalized ticker, direction tag, mute flag, data status).

## 6. Failure Semantics

Failure semantics are the most operator-visible differentiator between the three apps (see §8 and §9). The contract codifies:

- **Missing primary:** a primary whose signal series cannot be produced (data fetch fails, library missing, computation errors). This is NOT equivalent to mute. The aggregate output's `status` becomes `partial`, not `valid`, and the missing primary appears in `issues`. The aggregate may still compute across the remaining primaries, but operators MUST see the partial-coverage indicator. Operators may explicitly mute the failing primary to upgrade the status to `valid`; silent exclusion is NEVER acceptable.
- **All active primaries missing/failed:** status becomes `unavailable`; aggregate is undefined; `issues` identify each unavailable primary.
- **No active primaries (all muted):** if every primary is muted, the input is invalid for aggregate computation. Status is `invalid_input`; the operator action is to unmute at least one primary.
- **Duplicate active primaries:** status is `invalid_input` if duplicates reach the contract. The aggregate is undefined; implementations MUST NOT proceed with an implicit-weighting interpretation.

## 7. Metrics Contract

The aggregate signal series is mapped to secondary returns per the existing canonical scoring path. `multi_primary_contract_v1` does NOT redefine secondary-return computation; it produces the aggregate signal that downstream scoring consumes. Reference: `canonical_scoring.combine_consensus_signals` is the upstream helper for consensus computation; existing canonical metric helpers consume its output.

## 8. Per-App Current-State Mapping

### 8.1 Spymaster

- **UI surface:** Multi-Primary Signal Aggregator at `project/spymaster.py:6531`.
- **Callback:** `update_multi_primary_outputs(primary_tickers, invert_signals, mute_signals, secondary_tickers_input, primary_tickers_children, mp_ticks)` at `project/spymaster.py:11399`.
- **Optimization combinations:** use the same consensus shape at `project/spymaster.py:12395`.
- **Current consensus rule:** hand-rolled §18 unanimity at `project/spymaster.py:11510` (per-bar) and vectorized equivalent at `project/spymaster.py:12439`.
- **Current failure semantics:** any missing primary data stops with placeholder/pending text at `project/spymaster.py:11459`; secondary failures are skipped until no valid data remains.
- **Status vs §18:** structural divergence only (hand-rolled vs canonical helper). Semantic match on loaded data. Failure behavior is closer to the contract because Spymaster does not silently drop a requested primary.

### 8.2 ImpactSearch

- **UI surface:** "Primary Tickers" batch textarea + "Secondary Ticker(s)" at `project/impactsearch.py:3391`.
- **Entry points:** `start_processing(...)` at `project/impactsearch.py:3742`; `process_primary_tickers(...)` at `project/impactsearch.py:2975`; `process_single_ticker(...)` at `project/impactsearch.py:2478`.
- **Current consensus rule:** none. There is no multi-primary aggregate. Each primary is evaluated independently against the secondary using `calculate_metrics_from_signals(...)` at `project/impactsearch.py:2026`. The "multi" in ImpactSearch's current surface means batch-of-single-primary, not aggregate.
- **Current failure semantics:** per-primary failures are skipped/diagnosed; no aggregate failure semantics exist because no aggregate exists.
- **Status vs §18:** semantic divergence. ImpactSearch's "multiple primary tickers" produces independent batch rows, not a §18 aggregate.

### 8.3 Confluence

- **UI surface:** Multi-Primary Signal Aggregator at `project/confluence.py:1055`.
- **Entry points:** `_mp_eval_interval(...)` at `project/confluence.py:392`; `_mp_build_combined_signal_series(...)` at `project/confluence.py:537`; `_mp_build_virtual_libraries(...)` at `project/confluence.py:652`; `run_multi_primary_analysis(...)` at `project/confluence.py:1907`.
- **Current consensus rule:** delegates to `canonical_scoring.combine_consensus_signals` via `_mp_combine_unanimity_vectorized(...)` at `project/confluence.py:313`. This IS §18 unanimity.
- **Current failure semantics:** missing primary libraries are skipped at `project/confluence.py:433`; if at least one primary remains, Confluence proceeds without surfacing the missing primary as `partial` status.
- **Status vs §18:** structural match for consensus (correctly delegates to canonical helper). Semantic divergence on failure: silently excludes failed/missing primaries and proceeds, which hides coverage loss from the operator. The 2025-10-22 Confluence implementation doc claims SpyMaster parity; that claim is consensus-correct but failure-incorrect.

## 9. Divergence Classification and Migration Plan

| App | Consensus rule | Failure semantics | Migration depth |
| --- | --- | --- | --- |
| Spymaster | Structural (hand-rolled) | Honest (stop/pend on missing) | Refactor to canonical helper; preserve UI |
| Confluence | Canonical (already delegated) | Silent exclusion (gap) | Add operator-visible partial-coverage diagnostic |
| ImpactSearch | None (no aggregate) | N/A | Add canonical aggregate path; preserve batch mode as documented alternate (see §10) |

**Migration order for 5B-MP-2:**

1. **Spymaster first.** Highest operator visibility, daily-only complexity. Replacing hand-rolled consensus with `canonical_scoring.combine_consensus_signals` is local and low-risk because semantic equivalence is preserved. UI surface text receives Item 3-pattern updates.
2. **Confluence second.** Consensus is already canonical; the migration is failure-semantics correction. Adds `partial` status surfacing and missing-primary diagnostic. Behavioral risk noted in §13.
3. **ImpactSearch last.** Largest semantic divergence. Adds a new canonical aggregate path while preserving the existing batch-evaluation mode per §10's hybrid policy.

The reference implementation for parity-suite assertions is `canonical_scoring.combine_consensus_signals`, NOT any of the three apps' current implementations.

## 10. ImpactSearch Alternate-Mode Policy

ImpactSearch retains a HYBRID multi-primary surface after 5B-MP-2 migration:

- **Aggregate mode:** when ImpactSearch is invoked with multi-primary inputs in aggregate mode, the underlying computation MUST produce a `multi_primary_contract_v1` aggregate signal via `canonical_scoring.combine_consensus_signals`. Output shape follows §5.
- **Batch mode (alternate):** existing independent-evaluation behavior is preserved as a documented alternate mode. Output is a batch of independent single-primary evaluations, not a §18 aggregate.

Operator-facing text MUST distinguish the two modes:

- "Canonical multi-primary aggregate" — aggregate mode, §18 consensus applies.
- "Batch evaluation across primaries" — alternate mode, independent results per primary, NOT a §18 aggregate.

The default mode and the UI control surfacing the mode choice are deferred to the 5B-MP-2 ImpactSearch preflight.

## 11. Reason-Code Taxonomy Stub

Multi-primary diagnostic reason codes (full plumbing deferred to 5B-MP-2 per-app preflight):

- `multi_primary_input_invalid` — duplicate active primaries, malformed direction tags, all primaries muted, or other contract-invalid input.
- `multi_primary_partial_coverage` — at least one primary missing/failed; aggregate computed across remaining primaries with `partial` status.
- `multi_primary_unavailable` — no active primary signal series could be produced; aggregate is undefined.
- `multi_primary_no_overlap` — primaries' evaluation grids do not overlap.
- `multi_primary_no_triggers` — all bars resolved to `None`.
- `multi_primary_aggregation_failed` — internal computation error (canonical helper raised).
- `multi_primary_parity_violation` — used by parity-suite tests when cross-app outputs diverge from canonical reference.

Per locked 5B-MP rules: prefixes are `[SPYMASTER:...]` / `[IMPACTSEARCH:...]` / `[CONFLUENCE:...]` per the emitting app. Consensus disagreement is normal mute behavior, NOT an error, and does NOT have a reason code unless a later preflight explicitly scopes operator diagnostics for it.

## 12. 5B-MP-2 Parity-Suite Requirements

Parity-suite test infrastructure is mandatory for 5B-MP-2, analogous to `project/test_scripts/test_within_engine_parity.py`. The test design is deferred to the 5B-MP-2 preflight, but the contract requires:

- Test fixtures driving all three apps with identical multi-primary inputs.
- Reference implementation: `canonical_scoring.combine_consensus_signals`, NOT any app's current code.
- Assertions on convergent `aggregate_signal` outputs across apps for matched inputs.
- Allowed cosmetic divergence: UI labels, ordering of detail entries, presentation-only text.
- Disallowed divergence: any difference in `aggregate_signal`, `status`, or non-cosmetic `issues` content for the same input.
- Edge-case fixtures: single-primary, all-muted-except-one, all-muted, all-`None` primaries, missing primary, all-missing primaries, duplicate active primaries, no-overlap grids.

## 13. Risk Assessment

- **Confluence behavioral change risk: HIGH.** Confluence currently proceeds with partial primary coverage silently. Migration adds `partial` status surfacing and missing-primary diagnostics. Operator workflows that consume Confluence outputs without inspecting status may notice changed apparent results; specifically, current "valid" outputs may now reveal as `partial`. Mitigation: first add operator-visible partial-coverage diagnostics without changing the consensus rule; later 5B-MP-2 preflight decides whether any stricter behavior is warranted.
- **Spymaster UI wording risk: MODERATE.** The Multi-Primary Signal Aggregator label survives, but supporting text should clarify "non-`None` unanimity consensus" rather than generic aggregation. Updates follow the Item 3 pattern.
- **ImpactSearch naming/expectation risk: MODERATE.** Operators may currently read multiple-primary batch rows as "multi-primary results" and assume aggregate semantics. The hybrid-mode policy and explicit UI text differentiation address this; deprecation period preserves existing batch behavior.
- **Single-primary degradation transparency:** implementations should make clear when N=1 active and the contract is in single-primary mode, so operators do not infer multi-primary consensus from single-primary output.
- **Duplicate-primary input:** the strict-reject-or-pre-contract-warning policy may surface warnings on inputs that previously silently de-duplicated. Mitigation: explicit operator-visible warning on pre-contract de-duplication, or strict reject with clear error message.

## 14. Open Questions Deferred to 5B-MP-2 Sub-Preflights

- Spymaster (5B-MP-2a): exact code-locality boundary for the hand-rolled-to-canonical-helper migration; UI text updates per Item 3 pattern; deprecation message for any divergent surface.
- Confluence (5B-MP-2b): operator-visible diagnostic for partial coverage; whether to emit `[CONFLUENCE:...]` prefixed messages via existing logging or a new surface; whether stricter behavior beyond diagnostics is warranted.
- ImpactSearch (5B-MP-2c): default mode (aggregate vs batch); UI control for mode selection; per-mode UI text; whether batch-mode rows continue using existing per-primary diagnostic surfaces or migrate to the multi-primary reason-code taxonomy.
- Parity-suite (any 5B-MP-2 split or its own dedicated PR): test fixture design; allowed-divergence allowlist; integration with the existing `test_within_engine_parity.py` infrastructure or a new top-level parity test module.

## 15. Document Status

**LOCKED 2026-05-06 after Codex review.** This document is the locked authority for all 5B-MP-2 implementation work. Any changes require explicit amendment with date and rationale.

End of document.
