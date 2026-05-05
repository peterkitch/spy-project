# Phase 4 Scoping Document

**Date:** 2026-05-04

**Status:** Locked scope, ready for Phase 4A preflight

**Author:** PRJCT9 sprint

**Phase:** 4 (Cross-Ticker Multi-Timeframe Confluence Engine)

## North Star alignment

Phase 4 is a step toward the PRJCT9 North Star (see
2026-05-04_PRJCT9_NORTH_STAR.md). Specifically, Phase 4
produces the manifest-stamped data layer that the eventual
public site will read.

Phase 4 implementation decisions should be evaluated against
North Star direction:

- Choices that preserve the engine/presentation separation
  are aligned
- Choices that make manual-entry, accessibility, or future
  crowdsourcing/BYO-data goals harder to reach should be
  flagged and reconsidered
- Choices that pull engine output toward presentation
  concerns (UI-specific shapes, hard-coded display logic)
  should be deferred or rejected

Phase 4 outputs should be shareable, reproducible, and
inspectable. Run directories should include stable run
identity, universe mode, intervals, parameters, input
artifact references, manifest hashes, coverage counts, and
enough metadata for a future public page to link to or cite
a result without recomputing it.

Phase 4 explicitly does NOT implement:

- The public site (Phase 6)
- Manual ticker entry on-demand stackbuild flow (Phase 5
  or 6)
- Bring-your-own-data ingestion (Phase 7+)
- Volunteer-contributed compute (Phase 7+)
- Accessibility/UX work (Phase 6)
- StackBuilder coverage across the full 73K universe (out
  of scope; Phase 4 reads what exists, surfaces what
  doesn't)

Phase 4's job is to ensure the data exists in a form those
phases can consume cleanly.

## Note on superseded sprint state

This document supersedes any stale "Next: Phase 3" wording
in project/CLAUDE.md or other sprint references when planning
Phase 4. CLAUDE.md sprint-state drift will be cleaned up in
a separate doc PR; for Phase 4 planning purposes, this
document is authoritative.

## Purpose

Phase 4 produces the durable, manifest-stamped cross-ticker
multi-timeframe confluence dataset that Phase 6's public
research website will consume. Phase 4 is the engine and data
layer. Phase 4 is not a website, not a UI, not a public-
facing surface.

## Scope locked

**In scope:**

1. A new aggregation engine module that produces a daily
   manifest-stamped output run directory containing:
   - Per-secondary signal data across the requested ticker
     universe (TrafficFlow-equivalent core data)
   - Multi-timeframe confluence overlay (1d + 1wk + 1mo +
     3mo + 1y per secondary, where libraries exist)
   - Universe coverage report with one row per requested
     universe ticker
   - Stack composition and ranking per secondary, where
     existing StackBuilder run outputs are available.
     Missing or ambiguous StackBuilder outputs are
     represented in coverage metadata.
   - Per-secondary signal direction as of the run date,
     plus any producer-exposed next-session/carry-forward
     signal available without future price data. No
     future-period signal is computed from unavailable
     data.

2. Phase 4A is an aggregation/read engine by default. It
   consumes existing verified signal libraries, StackBuilder
   run outputs, and Spymaster PKLs. It does NOT rebuild
   missing signal libraries, run StackBuilder for missing
   secondaries, or fetch live Yahoo data during the
   aggregation run. Producer rebuild is a separately scoped
   Phase 5+ feature. Full-universe StackBuilder coverage is
   not required for Phase 6 launch and remains a future
   scaling decision.

3. Daily signal source precedence per secondary:
   1. Verified OnePass daily signal library
   2. Verified Spymaster PKL fallback (source-tagged
      explicitly as fallback, not as a peer primary
      source)
   3. Missing/skipped with recorded status reason

   Source precedence summarized: `OnePass library -> Spymaster
   fallback -> skipped`.

4. "Verified" and manifest-mode semantics:

   "Verified" means loaded through the Phase 3 verified-
   loader path (load_verified_signal_library,
   load_verified_pickle_artifact, load_verified_xlsx_artifact).

   Strict-manifests mode rejects legacy or mismatched
   manifests for audit runs. Default mode may accept legacy
   inputs where existing Phase 3 loaders allow it, but must
   mark that fact in coverage metadata via the
   legacy_manifest_used issue code.

5. Phase 3 manifest contract participation from day one. New
   artifact types (named for Phase 3 consistency):
   - cross_ticker_confluence_rankings
   - cross_ticker_confluence_overlay
   - cross_ticker_confluence_coverage
   - cross_ticker_confluence_run

   Plus a required universe snapshot, either as its own
   artifact (cross_ticker_confluence_universe_snapshot) or
   explicitly embedded in the run manifest and coverage
   metadata. Phase 4A preflight chooses storage; the
   requirement is locked.

6. Universe modes (CLI-selectable):
   - gtl-active (default): all GTL active symbols, with
     missing-artifact tickers surfaced in coverage
   - tickers: explicit user-provided list (CLI argument)
   - scorable: scorable subset for fast research runs,
     defined as GTL active intersected with tickers that
     have an available daily source under the daily source
     precedence rules. Non-daily interval presence still
     affects full vs partial coverage within this subset.

   Default stays gtl-active because coverage transparency
   is the point.

7. Failure-mode contract:

   Per-ticker partial scoring rules:
   - If daily exists and one or more non-daily intervals
     are missing, the ticker is partially scored. Daily
     fields populate; missing interval fields are null/
     statused; confluence active_count and coverage fields
     reflect reduced coverage.
   - If one or more non-daily interval libraries exist but
     no daily source exists, the ticker is not eligible for
     daily rankings or TrafficFlow-equivalent signal fields.
     It remains in coverage with status
     skipped_no_daily_source. Available non-daily interval
     statuses may still be recorded for diagnostics, but
     the ticker is absent from ranking artifacts.
   - If no interval libraries or fallback sources load, the
     ticker remains in the coverage report with status
     skipped_no_signal_libraries and is absent from ranking
     artifacts.

   Run-level failures:
   - If the universe source is unreadable or empty, the run
     is fatal.
   - If individual symbols inside the universe are
     malformed, those symbols are recorded as
     invalid_universe_symbol and the run continues.

8. Universe coverage report shape:

   The coverage report contains one row/object per ticker
   in the requested universe. Every ticker receives a
   top_level_status (mutually exclusive) and may carry zero
   or more issue_codes (additive component-level
   diagnostics). Ranking artifacts may contain only scored
   tickers, but the coverage report must contain the full
   universe.

   top_level_status (mutually exclusive, exactly one):
   - scored_full
   - scored_partial
   - skipped_no_daily_source
   - skipped_no_signal_libraries
   - invalid_universe_symbol

   issue_codes (additive, zero or more per ticker):
   - missing_stackbuilder_run
   - manifest_failed
   - stale
   - schema_failed
   - producer_output_missing
   - legacy_manifest_used

   Plus per_source_status and per_interval_status fields
   with implementation-defined names chosen in Phase 4A
   preflight.

9. Schema isolation principle:

   Phase 4 remains ticker-first for this sprint, but
   canonical output schema should isolate yfinance/ticker-
   specific metadata from the generic confluence/ranking/
   coverage records where practical. Future non-financial
   series (per North Star bring-your-own-data direction)
   should be able to map into the same conceptual contract
   through adapters rather than requiring a new engine
   shape.

   This does not force BYO-data implementation in Phase 4.
   It prevents Phase 4A from baking in finance-only
   assumptions everywhere they aren't required.

**Out of scope (Phase 5+ or later):**

- Public website or dashboard UI (Phase 6)
- Manual ticker entry on-demand stackbuild flow (Phase 5
  or 6 - see North Star Path 2)
- Bring-your-own-data ingestion (Phase 7+ - see North Star)
- Volunteer-contributed compute (Phase 7+ - see North Star
  crowdsourcing direction)
- Live data refresh / streaming
- Producer rebuild from inside the aggregation engine
- Full-universe StackBuilder coverage refresh
- Alternate data source integration (post-sprint decision)
- QC clone / live trading wiring (parked indefinitely)
- Confluence engine UI completion (separate decision)
- Spymaster_confluence_bridge revival (separate decision)
- Matrix concept naming consolidation (separate cleanup PR)
- Vestigial CLI pruning in StackBuilder (separate cleanup
  PR)
- TrafficFlow disabled-matrix code removal (separate
  cleanup PR)
- Deferred items from Post Phase 3 Sprint Bug Cleanup audit
  (OnePass error UX, TrafficFlow refresh callback,
  ImpactSearch error taxonomy, etc.)

## Design principles

1. **Engine, not UI.** Phase 4 produces files. Phase 6
   produces the website that reads those files.

2. **Aggregate, don't rebuild.** Existing engines (OnePass,
   MultiTimeframeBuilder, StackBuilder, Spymaster
   precompute) produce inputs. Phase 4 reads, aggregates,
   and stamps. It does not run producers.

3. **Coverage transparency.** The output represents every
   requested ticker as data, not as a hidden absence.
   Equivalent of TrafficFlow's "!" warning surfaced as
   structured status.

4. **Multi-timeframe is core.** Daily + weekly + monthly +
   quarterly + yearly overlays are part of the headline
   output, not a Phase 5 add-on.

5. **Manifest contract from day one.** Every durable output
   artifact is either individually manifest-stamped or
   represented as an output_artifacts entry in the Phase
   3-style run manifest. No retrofitting.

6. **Universe is configurable.** Default to gtl-active, but
   the engine must run on user-provided ticker lists and on
   the scorable subset mode for ad-hoc research.

7. **No future knowledge.** Signal direction is reported as
   of the run date. Any next-session/carry-forward signal
   must be producer-exposed and computable without future
   price data.

8. **Schema isolation toward future BYO.** Where practical,
   isolate financial-specific metadata from the generic
   confluence/ranking/coverage shape. The engine shouldn't
   need to be rewritten when non-financial time series
   enter the picture.

## User-facing question Phase 4 answers

"For each ticker in the requested universe, what is the
multi-timeframe confluence picture as of the run date, what
stacks are available, and what is each ticker's coverage
status?"

This is the data Phase 6's website will present, and the
data Peter can consume directly for ad-hoc research once
Phase 4 ships.

## Implementation phasing

**Phase 4A: Aggregation engine + outputs + manifests +
tests.**

- Module name: TBD by Codex Phase 4A preflight
- CLI: `--universe-mode {gtl-active,tickers,scorable}`,
  `--tickers`, `--intervals`, `--output-dir`,
  `--strict-manifests`
- Outputs: manifest-stamped JSON for canonical artifact;
  CSV/XLSX summaries derived from canonical
- Synthetic-fixture test coverage
- No UI

**Phase 4B: Optional operator Dash view (single PR after
4A).**

- Reads Phase 4A run-directory outputs
- Operator-facing exploration view
- Not the public website (still Phase 6)

**Phase 5+: Cleanups, validation, controlled compute
infrastructure, producer maintenance.**

- Honest validation report and validation methodology for
  public research claims
- StackBuilder vestigial CLI pruning
- TrafficFlow matrix-path code removal
- Spymaster Help UI matrix.py reference fix
- Multi-primary mode reconciliation across Spymaster,
  ImpactSearch, Confluence
- Deferred items from Post Phase 3 Sprint Bug Cleanup audit
- B11 compute_signals cleanup
- environment.yml / requirements.txt env hygiene
- CLAUDE.md sprint-state drift fixes
- Controlled compute infrastructure for refreshing curated
  universe (local/cloud, not volunteer)
- Manual ticker entry on-demand stackbuild flow (North Star
  Path 2)
- Pre-launch data licensing review

**Phase 6: Public research website.**

- Consumes Phase 4 outputs
- Renders multi-timeframe confluence for public use
- Allows user-provided ticker exploration via Phase 5
  on-demand flow
- Coverage indicator visual treatment of incomplete data
- Soccer-mom-and-quant accessibility (North Star
  accessibility principle)
- Launches with curated/tiered universe (not full 73K with
  full stackbuilds; per Codex feasibility review)

**Phase 7+: Crowdsourcing and bring-your-own-data.**

- Volunteer-contributed compute infrastructure
- Pattern submission, verification, knowledge-base layer
- Bring-your-own-data ingestion adapters
- Alternate data source integration
- Wikipedia-of-pattern-finding direction

## Phase 4A acceptance criteria

The Phase 4A implementation is considered shipped when:

- One command produces a run directory under a stable
  output root.
- The run writes rankings, overlay, coverage, and run
  manifest artifacts (plus universe snapshot or embedded
  equivalent).
- Coverage includes every requested universe ticker exactly
  once, with mutually exclusive top_level_status and zero-
  or-more issue_codes.
- Every durable output artifact is either individually
  manifest-stamped or represented as an output_artifacts
  entry in the Phase 3-style run manifest.
- Missing/stale/manifest-failed inputs are represented as
  data, not hidden.
- The aggregation run does not call producer rebuild paths
  or live Yahoo fetch paths in default mode.
- StackBuilder outputs are consumed only when already
  present; missing or ambiguous StackBuilder runs are
  represented in coverage metadata.
- The run executes against a small synthetic universe with
  no network access.
- Strict-manifests mode rejects legacy/mismatched inputs
  for audit runs.
- Default mode tolerates legacy/missing inputs by recording
  coverage status, marking legacy use via the
  legacy_manifest_used issue code where applicable.
- The run manifest contains enough provenance for future
  public pages to reproduce, inspect, or cite a run: run
  id, universe mode, intervals, parameters, source artifact
  references/hashes, coverage counts, and strict/default
  manifest mode.

## Decisions captured

From Peter, May 4 2026:

- Phase 4 includes multi-timeframe confluence data (not
  just TrafficFlow-equivalent)
- Multi-timeframe is research-critical, not optional
- Coverage transparency is required (no hidden gaps)
- Phase 4 is engine; Phase 6 is website; do not conflate
- Vision: research-based public site, not a leaderboard
- Both interactive exploration AND daily curated views
  must be supportable from Phase 4's output schema
- Universe scope: gtl-active by default, user-override
  supported, scorable subset mode supported, stale/missing
  tickers explicitly flagged in coverage metadata rather
  than hidden
- Phase 4A consumes existing producer artifacts by default;
  producer rebuild is out of scope for Phase 4
- No future-period signal computed from unavailable data
- Compute strategy: controlled compute first (local/cloud)
  for sprint; volunteer-contributed compute is Phase 7+
- yfinance is the sprint data source; data licensing review
  deferred to pre-launch
- Public site launches with curated/tiered universe, not
  full 73K with full stackbuilds (per Codex feasibility
  review)
- Schema isolation principle: keep canonical shape generic
  enough that future BYO-data adapters can plug in without
  rewriting the engine

## Open questions deferred to Phase 4A preflight

These implementation-level questions are for Codex's Phase
4A preflight to answer with recommendations:

- Exact output schema (JSON structure, field names,
  nesting, per_source_status and per_interval_status field
  naming, ticker-specific vs generic field segregation)
- Module name and location
- Universe snapshot storage (separate artifact vs embedded
  in run manifest and coverage metadata)
- Parallelism approach (workers, batching, ordering)
- Test fixture design and synthetic-universe scale

These behavioral rules are NOT deferred (locked above):

- One coverage row per universe ticker
- top_level_status mutually exclusive plus additive
  issue_codes structure
- Daily/no-daily partial scoring semantics including the
  "non-daily-only excluded from rankings" rule
- No producer rebuild by default
- No live Yahoo fetch in default mode
- Universe snapshot/hash required
- No future-period signal
- Source precedence (OnePass library -> Spymaster fallback
  -> skipped)
- Schema isolation toward future BYO

## Document status

**Locked.** Any changes require explicit amendment with
date and rationale.
