# Phase 7+ Scoping: Universe-Wide Beam K-Search

## Status

Scoping only. Not implementation-ready. Not blocking any current Phase 6 sprint work. Captured here so the idea survives until Phase 7+ prioritization.

This document is not an authorization to implement universe-wide beam search. It is a parking-lot note for later design review.

## Background

The current StackBuilder phase3 K-search operates inside a fixed cohort of `top_n + bottom_n` primaries selected from each secondary's ImpactSearch XLSX. The common current configuration uses a 40-row cohort: top 20 direct plus bottom 20 inverse.

That cohort is locked before phase3 search begins. Once a ticker is excluded from the cohort, it cannot enter the stack at any later K level. This creates a possible structural blind spot. A ticker that is mediocre as a standalone signal, but exceptional in combination with one of the cohort winners, would not be discovered if it never enters the candidate set.

The current sprint remains focused on recovering and validating the LEGACY-compatible StackBuilder workflow. Universe-wide beam search is a later research direction, not a replacement for the current sprint.

## The Idea

Investigate replacing, augmenting, or comparing the fixed-cohort beam search at higher K levels with a universe-wide beam expansion that can consider candidates outside the original 40-row cohort.

The search universe would be the available signal-library universe, approximately tens of thousands of yfinance tickers. The exact count must be remeasured at Phase 7+ planning time.

The diagnostic question:

Does ImpactSearch's singleton-based cohort selection leave material combinatorial value undiscovered?

## Conceptual Mechanism

One possible version:

- K=1: use the best ImpactSearch singleton candidate as the seed. ImpactSearch has already ranked singleton direct/inverse behavior.
- K=2: evaluate adding each available universe ticker to the K=1 seed, selecting the best K=2 candidates by Total Capture.
- K=3: for each carried K=2 beam state, evaluate adding each available universe ticker as the third member.
- K=4: continue universe-wide beam expansion.
- K=5..K=12: switch back to cohort-bounded beam or an accumulated expanded cohort.

The exact mechanism is not decided. Phase 7+ must decide whether candidates are evaluated as direct-only, inverse-only, both modes, or mode-selected by an upstream ranking rule.

## Illustrative Cost Estimate

These numbers are illustrative only and must be remeasured before implementation.

Assume:

- search universe: approximately 38,000 candidate tickers;
- beam_width: 12;
- approximate evaluation cost: 10 ms per candidate add / score operation after caching;
- no network fetches;
- signal libraries already exist locally;
- loader and alignment caches are designed correctly.

Illustrative full universe-wide beam:

- K=2 universe scan: 38,000 x 10 ms = about 6 minutes per secondary.
- K=3 universe beam: 12 x 38,000 x 10 ms = about 76 minutes per secondary.
- K=4 universe beam: about 76 minutes per secondary.
- K=5..K=12 universe beam: 8 x 76 minutes = about 10 hours per secondary.

Pure universe-wide beam through K=12 is likely too expensive for routine use.

## Practical Hybrid Candidate

A more practical research variant:

- K=1: use ImpactSearch singleton seed.
- K=2: universe-wide scan.
- K=3: universe-wide beam.
- K=4: universe-wide beam.
- K=5..K=12: switch back to cohort-bounded beam, using either the original cohort or an expanded cohort that includes universe-discovered additions.

Illustrative cost:

- K=1: no new universe-wide compute.
- K=2 universe scan: about 6 minutes.
- K=3 universe-wide beam: about 76 minutes.
- K=4 universe-wide beam: about 76 minutes.
- K=5..K=12 cohort-bounded beam: expected to be much smaller than universe-wide expansion, but must be remeasured.

Illustrative total: about 2.5 to 3 hours per secondary.

This may be overnight-runnable for a small secondary set on dedicated hardware, but it is not appropriate for routine operation until measured.

## Diagnostic Value

The approach is valuable if it cleanly answers this question:

Does expanding beyond the ImpactSearch-selected cohort materially improve StackBuilder results?

Possible outcomes:

1. Universe-wide K=2..K=4 repeatedly selects tickers already present in the original 40-row cohort.

   Interpretation: ImpactSearch cohort selection is empirically supported. Further universe-wide beam work may not be worth the cost.

2. Universe-wide K=2..K=4 repeatedly selects tickers outside the original 40-row cohort and materially improves Total Capture or other downstream review metrics.

   Interpretation: the cohort-bounded search may be leaving combinatorial value undiscovered. Cohort construction should be revisited.

3. Universe-wide candidates improve backtest metrics but introduce unstable, low-trigger-day, stale, or operationally unattractive stacks.

   Interpretation: wider search may need additional guardrails, stability scoring, freshness rules, or UI disclosure.

## What This Is NOT

This is not a replacement for ImpactSearch.

ImpactSearch remains the source-target singleton discovery layer. Universe-wide beam would be a later StackBuilder search-depth / search-breadth experiment.

This is not a Phase 6 item.

The current sprint remains focused on restoring and validating the LEGACY-compatible StackBuilder workflow: consumer-only loading, bounded validation behavior, fast combine, runner controls, smoke evidence, and canonical safety.

This is not implementation-scoped.

Phase 7+ must first answer:

- exact candidate universe;
- direct/inverse mode policy;
- cache strategy;
- memory limits;
- progress reporting;
- checkpointing;
- interruption and resume behavior;
- output schema;
- UI disclosure;
- comparison methodology against cohort-bounded StackBuilder.

## Open Questions for Phase 7+

1. What is the correct candidate universe?

   All stable signal-library tickers, all ImpactSearch-eligible tickers, all locally cached price tickers, or a filtered liquidity / freshness subset?

2. Should universe-wide beam evaluate Direct and Inverse modes separately?

   Treating `(ticker, mode)` as the candidate doubles the effective universe but may be necessary for parity with StackBuilder's current direct/inverse semantics.

3. Can universe-wide beam reuse `_combine_signals_fast` directly?

   The combine helper is fast for already-loaded aligned signals, but universe-wide search may be dominated by library loading, alignment, cache misses, and candidate iteration.

4. What cache design is required?

   Loading and aligning tens of thousands of signal libraries repeatedly is likely unacceptable without aggressive in-run caching and possibly precomputed aligned arrays.

5. What beam_width is appropriate?

   The existing default beam_width=12 may be too narrow for a universe-wide candidate space. But increasing it multiplies runtime directly.

6. Should universe-wide beam be a diagnostic mode only?

   It may be more useful as an occasional research pass than as a routine production StackBuilder mode.

7. How should results be presented?

   A K=12 stack containing tickers discovered outside the original cohort tells a different story from a stack entirely drawn from the original ImpactSearch cohort. The UI may need to show original-cohort versus universe-discovered membership.

8. How does this interact with future post-close / market-hours compute plans?

   Larger discovered cohorts may increase the number of tickers that must be monitored, refreshed, or displayed in downstream systems.

9. What is the success metric?

   Total Capture remains the current StackBuilder selection metric, but Phase 7+ may need additional review metrics for stability, trigger-day adequacy, freshness, and operational usefulness.

## Minimal Phase 7+ Research Plan

Before implementation, run a measured design spike:

1. Pick one secondary, likely SPY.
2. Freeze a small candidate universe sample, such as 500 to 2,000 tickers.
3. Implement no production code initially; use a disposable measurement harness if authorized.
4. Measure:
   - load cost;
   - alignment cost;
   - combine cost;
   - score cost;
   - memory footprint;
   - cache hit rate;
   - best outside-cohort candidate frequency.
5. Compare against the existing 40-cohort StackBuilder output.
6. Decide whether full universe-wide K=2..K=4 is worth a real implementation PR.

## Decision Point

At Phase 7+ prioritization, decide whether universe-wide K=2..K=4 is worth the additional compute cost to test cohort selection methodology.

The decision should be based on measured evidence, not assumption:

- Does the universe-wide pass discover materially better tickers outside the original cohort?
- Are improvements stable and operationally meaningful?
- Can runtime and memory be bounded?
- Can results be explained clearly to the operator and downstream UI users?

For now: parked. Captured here so the idea survives without disrupting the current sprint.
