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

## Phase 7+ Carry-Forward Items

These items were identified during a Codex classification audit of accumulated technical debt and architectural ideas. Each item is parked for Phase 7+ prioritization. None are implementation-ready. Capture here ensures the ideas survive outside of conversation memory.

### 1. B11 compute_signals cleanup

Description: Deferred technical debt in the Spymaster `compute_signals` path, carried forward from the Post-Phase-3 sprint. The exact cleanup scope was not fully specified and requires source inspection at Phase 7+ planning time.

Why deferred: Not blocking current TrafficFlow headless work unless Spymaster cleanup is pulled forward by operator decision.

Open questions:

- What specifically needs cleanup in `compute_signals`?
- Does it affect Spymaster headless conversion if one is ever pursued?
- Is it a correctness issue, a clarity issue, or a performance issue?

### 2. environment.yml / requirements.txt hygiene

Description: Dependency manifest cleanup. CLAUDE.md notes that the existing manifests do not match the pinned audit environment. This item covers future hygiene work to bring dependency documentation and rebuild paths into alignment.

Why deferred: Important for environment rebuild and deployment scenarios, but not sprint-critical until cloud compute or fresh-environment provisioning becomes active work.

Open questions:

- What is the canonical dependency set that should be reflected?
- Should `environment.yml` and `requirements.txt` both be maintained, or should one be the source of truth?
- Are there pinned versions that need to be relaxed for portability?

### 3. OnePass error UX

Description: Deferred UI/operational issue from the Post-Phase-3 Codex audit. OnePass error messaging and handling behavior needs review for clarity and actionability.

Why deferred: Not needed for current StackBuilder outputs or imminent TrafficFlow headless consumption. Improves operational experience but is not a correctness blocker.

Open questions:

- Which OnePass error paths produce the most confusing or unhelpful messaging?
- Is the issue surfaced in Dash UI, in the headless runner, or both?
- What is the desired error contract for downstream consumers?

### 4. ImpactSearch error taxonomy

Description: Error categorization in the ImpactSearch pipeline. Future robustness work should give ImpactSearch a structured error taxonomy.

Why deferred: ImpactSearch inputs are already available for the current pipeline path. Taxonomy cleanup is future work, not a current blocker.

Open questions:

- What error categories does ImpactSearch currently emit?
- Which categories should be retryable, fatal, or operator-actionable?
- Does the taxonomy need to align with OnePass or StackBuilder error conventions?

### 5. StackBuilder progress JSON

Description: Progress JSON schema or behavior refinements in StackBuilder. This is future observability work.

Why deferred: StackBuilder production runs have completed successfully under current progress JSON behavior. Refinements are deferred unless a concrete downstream blocker appears.

Open questions:

- What specifically needs refinement in the progress JSON?
- Is the issue schema, frequency, completeness, or something else?
- Is this needed before headless TrafficFlow consumption of StackBuilder outputs?

### 6. TickerDash global single-job model

Description: TickerDash currently uses a global single-job model for processing. This is a broader UI/concurrency model issue.

Why deferred: Not part of current StackBuilder or imminent TrafficFlow headless work. Concurrency refactor is a larger investment that should wait until the headless pipeline is stable.

Open questions:

- What specific limitations does the single-job model impose?
- Would a multi-job model require schema or storage changes?
- How does this interact with the broader headless architecture?

### 7. Pre-computed closing-price threshold caching

Description: Operator-proposed architecture for the daily trading pipeline. Instead of running TrafficFlow / MTF / Confluence during market hours, run them post-close on confirmed close data, then compute the closing-price ranges that would produce Buy / Short / None signals for the next trading day. Cache those ranges. During market hours, look up live price against cached ranges instead of recomputing the pipeline.

Rationale: This sidesteps the gray-zone-close-data problem where intraday queries to a price feed can return a current daily bar that is not yet the actual close. By doing all heavy math on confirmed close data and reducing market-hours computation to range lookups, the pre-close trading decision becomes a fast comparison rather than a full pipeline run.

The signal generation logic is primarily SMA crossovers, which are algebraically solvable for the threshold price that would flip a signal direction. The system already knows the last N-1 close prices for any ticker; computing the close value that would make the SMA cross a level is a direct calculation per condition.

The approach may extend to MTF where a new window is forming on a specific close. For most MTF timeframes on most days, no new window is closing, so threshold computation may apply mainly to the daily timeframe and only occasionally to other timeframes.

Why deferred: Blocked by headless TrafficFlow / MTF / Confluence. Threshold caching cannot be scoped fully until the underlying pipeline runs headless and produces structured output that the threshold computation layer can consume.

Open questions:

- Is the threshold a single value per ticker per timeframe, or a set of conditions with multiple boundaries?
- How are real-time data feeds integrated to provide live-price lookup during market hours?
- What cache format and storage location works best for range data?
- How does the existing Spymaster dashboard surface live signal state during market hours?
- How are MTF windows that are mid-formation on the daily close handled?

### 8. Daily TrafficFlow / MTF / Confluence scheduling

Description: Windows Task Scheduler, or equivalent scheduling, firing a local batch entrypoint at approximately the daily close window, running the three headless engines in sequence on confirmed close data.

Why deferred: Blocked by headless TrafficFlow, MTF, and Confluence existing. The scheduling layer should not be implemented before those engines have stable headless contracts.

Open questions:

- Exact timing: before close, after confirmed close, or another window?
- Failure handling: email, SMS, Slack, local notification, or another alert path?
- Retry logic: re-run on transient failure, or treat as fatal?
- Logging: where do scheduled-run logs live, and how long are they retained?
- How does scheduling interact with the threshold caching layer described in item 7?

### 9. Real-time data feed selection

Description: yfinance is adequate for the public Wikipedia-of-pattern-finding layer but insufficient for pre-close personal trading decisions due to data freshness limitations. Candidate real-time data providers include Polygon.io, IEX Cloud, Alpaca, and Interactive Brokers API.

Why deferred: Selection should wait until daily scheduling and threshold caching designs are closer to implementation, because the choice of provider depends on the latency and coverage requirements of those layers.

Open questions:

- What is the latency budget for live price lookups?
- What ticker universe coverage is required: US equities only, ETFs, foreign listings, or broader coverage?
- What is the cost model: per-query, monthly subscription, bundled with execution, or another model?
- Should the public Wikipedia layer continue using yfinance while the personal trading layer uses a paid feed, or should both consolidate on one provider?

### 10. Cloud compute architecture for ticker expansion

Description: Path from the 500-ticker baseline to 3,000-5,000 tickers and eventually toward the full yfinance universe. StackBuilder monthly refresh is the workload that most clearly needs cloud compute capacity.

Why deferred: Headless runner already exists for StackBuilder. What is missing is the orchestration layer: shipping signal library PKLs to cloud compute, distributing ticker work across workers, and collecting results back. This is significant infrastructure work that should wait until ticker expansion is operator-prioritized.

Open questions:

- Which cloud provider fits best: AWS, GCP, Azure, dedicated on-prem, or another option?
- Is the existing dedicated machine sufficient for the 3,000-5,000 ticker tier, with cloud only needed for larger universes?
- How is signal library data synchronized between local development and cloud workers?
- What orchestration tool fits the current workflow: Apache Airflow, Prefect, custom scripts, or another option?
- How does an eventual crowdsourced volunteer-compute model interact with paid cloud compute?

### 11. Build history UI for Spymaster

Description: UI feature to browse historical StackBuilder run directories per secondary, charting how the best stack composition changed over time.

Why deferred: The underlying data already exists in timestamped run directories. Only UI work is needed. This is an enhancement, not a blocker.

Open questions:

- What is the desired interaction: timeline view, diff view, side-by-side comparison, or another model?
- Should historical builds be loadable for TrafficFlow / MTF / Confluence replay, or read-only browsing?
- How does this interact with the Phase 7+ Wikipedia-of-pattern-finding public-facing site?
