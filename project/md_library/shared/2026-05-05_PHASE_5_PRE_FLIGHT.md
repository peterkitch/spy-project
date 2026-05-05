# Phase 5 Pre-Flight Document

**Date:** 2026-05-05

**Status:** Locked; sub-phases ready to proceed

**Author:** PRJCT9 sprint

**Phase:** 5 (Validation, Cleanups, Controlled Compute + Path 2 Backend, Pre-Launch Hardening)

## North Star alignment

Phase 5 is the credibility-and-maintenance phase between the Phase 4 engine/operator-UI layer and the Phase 6 public launch. The North Star names Phase 5 as "honest validation report, cleanups, controlled compute infrastructure, curated universe maintenance, and pre-launch hardening."

Phase 5 implementation decisions are evaluated against North Star direction:

- Choices that preserve research-honesty are aligned (validation methodology, exposed coverage gaps, no future-period leakage).
- Choices that preserve engine/presentation separation are aligned (Path 2 queues compute via the engine layer; Dash never invokes producers directly).
- Choices that pull Phase 5 toward Phase 6 polish (UI accessibility, public-facing branding, soccer-mom-and-quant landing experience) defer to Phase 6.

## Note on prior sprint state

This document reflects the post-Phase-4 state. Phase 4 (engine + operator Dash) is merged. The Phase 4 pre-flight doc (committed as `2026-05-04_PHASE_4_SCOPING.md`) referenced Phase 5+ items in its Out-of-scope and Implementation-phasing sections; this document is the authoritative breakdown.

## Locked Phase 5 behavioral rules

These rules apply to all Phase 5 sub-phases without amendment:

- No future-period leakage in validation, compute refresh, or on-demand results.
- Every new durable output gets a manifest or is represented in a run manifest (Phase 3 contract participation).
- Dash and operator surfaces queue or read; they do not directly invoke producers.
- Coverage and failure states surface as data, not hidden.
- Source precedence (OnePass library -> Spymaster fallback -> skipped) and Phase 4 canonical artifact semantics (series_id + series_metadata schema isolation toward future BYO) are not changed by Phase 5.
- Controlled compute refreshes producer artifacts and emits Phase 4A-compatible run directories. Phase 6 consumes Phase 4A / Phase 5 manifest-stamped outputs, not ad hoc compute logs.

## Purpose

Phase 5 produces the validation methodology + report, cleanups, controlled compute infrastructure (including Path 2 on-demand backend), and pre-launch licensing review that Phase 6 needs as prerequisites. Phase 5 is NOT the public site (still Phase 6). Phase 5 IS what makes Phase 6 credible.

## Scope locked

**In scope:**

1. **Phase 5A — Sprint-state hygiene + cleanup triage** (doc/config only):
   - CLAUDE.md sprint-state drift fix (stale "Next: Phase 3" wording flagged in the Phase 4 pre-flight)
   - Deferred-item ledger documenting all Phase 5+ items by classification (delete / deprecate / rename / behavior-change / cross-app reconciliation)
   - Cleanup-queue classification: which items are doc-only, which require code changes, which need separate preflights, which can batch into 5B, which warrant their own dedicated sub-phase
   - Multi-primary mode reconciliation across Spymaster / ImpactSearch / Confluence is one of the items 5A classifies (5A decides whether it batches into 5B or warrants a dedicated sub-phase with its own preflight)
   - No code deletion or behavior change in 5A; 5A is the triage step that makes 5B and beyond auditable in advance

2. **Phase 5B — Targeted cleanup PRs**:
   - Runs from 5A classification; multiple small PRs allowed
   - Preflight required for any PR that deletes behavior, changes CLI surface, touches environment files, or changes cross-app semantics
   - Tiny single-file cleanups may skip a separate preflight only when 5A classifies them as: no behavior deletion, no public CLI change, no environment-contract change, AND no cross-app semantic impact

3. **Phase 5C — Honest validation program** (combines methodology + engine):

   **5C-1 (methodology doc PR):** Locks methodology before 5C-2 implementation begins.

   - **Outcome windows (MVP):** Forward-return validation windows of 1, 5, 21, 63, and 252 trading days, measured from each signal date. These outcome windows are conceptually distinct from the Phase 4A signal intervals (1d, 1wk, 1mo, 3mo, 1y); the Phase 4A intervals are signal-computation timeframes, while validation outcome windows measure realized returns following a signal. Past/backward performance may be reported as descriptive context but is not the primary validation outcome.
   - **Baseline (MVP):** Same-ticker buy-and-hold over the same validation window. For ^GSPC signals: compare against ^GSPC buy-and-hold for that exact window. For stack/secondary flows: compare against buy-and-hold of the traded secondary. Random, momentum, and sector-matched baselines are deferred to later validation versions.
   - **Claims explicitly NOT made:** No investment advice. No trading signals or recommendations. No predictions of future returns. No guarantees. No causal claims. No "best ticker" rankings. The report describes observed historical/forward-realized behavior under documented inputs, coverage, and caveats.
   - **Versioning:** Methodology starts at validation_methodology_v1. The version is recorded in the methodology doc, the validation artifact payload, and the validation run manifest. Any change to metrics, baselines, leakage rules, outcome windows, or claims-not-made increments the version with date and rationale.
   - **Canonical vs exploratory:** Canonical validation outputs are locked, reproducible, manifest-stamped metrics used for public claims. Exploratory diagnostics are internal charts/tables for investigation and must be labeled non-canonical unless promoted by methodology amendment to a new version.

   **5C-2 (validation engine + report PR):** Implements the locked methodology and emits manifest-stamped validation artifacts.

   - **Inputs:** Phase 4A run outputs + the source artifacts / price or outcome data required by the locked methodology, with no future leakage.
   - **Output locations (dual):**
     - Machine-readable: manifest-stamped validation run under `project/output/validation/<run_id>/` following the Phase 4A run-directory pattern.
     - Human-readable: methodology + summary document under `project/md_library/shared/`.
     - Phase 6 cites/reads the artifact; humans read the md_library explanation.
   - **Minimum validation report content (Phase 6 launch gate):**
     - Latest successful status / last-updated timestamp for each producer (OnePass daily libraries, MultiTimeframeBuilder interval libraries, StackBuilder runs, Spymaster fallback artifacts).
     - Phase 4A confluence outputs available + verification that confluence/performance logic is in play.
     - Phase 4A coverage counts and issue counts.
     - Validation metrics computed for the locked outcome windows.
     - Baseline comparison.
     - Explicit stale / missing / failed coverage reported as data.

4. **Phase 5D — Controlled compute + Path 2 backend** (combined; one architecture track):

   - **Orchestration target (MVP):** Local-first controlled compute. Local jobs/CLI/scheduler first; cloud is a later option after the local job contract is proven. Volunteer/distributed compute remains Phase 7+.
   - **Run-status surfacing:** Durable job/run status files plus an operator view that extends Phase 4B's `cross_ticker_confluence_dash` (or mirrors its run-discovery pattern). State machine: queued, running, succeeded, failed, skipped, partial. Logs are secondary detail, not the primary status source.
   - **Job-type taxonomy:** Schedule producer refresh and aggregation jobs, not every possible request. MVP job types: `gtl_snapshot`, `onepass_daily_refresh`, `multitimeframe_refresh`, `stackbuilder_refresh`, `phase4a_aggregate`, `validation_support_run`, `path2_mini_run`. Cadence:
     - Daily OnePass refresh + Phase 4A aggregation: nightly.
     - Non-daily interval refresh: after each interval closes.
     - StackBuilder refresh: curated/queued tickers, not full 73K (per Phase 4 compute conclusion).
     - Path 2 mini-runs: queue-triggered, not scheduled.
   - **Path 2 cache staleness rule:** A verified cached result of any age may be displayed if it includes last-updated/staleness information. Default behavior: show the latest cached Phase 4A-compatible mini-run; mark stale when older than 45 days (aligned with Phase 4A `max_input_age_days`); the operator/user explicitly requests fresh compute. No automatic recompute on cache miss or staleness; cache misses show no cached result and offer/record an explicit queue request.
   - **GTL master interaction:** Scheduled refresh snapshots the GTL active universe at job start, recording source path, source.file_sha256, universe_hash, counts, and ordered series — the same reproducibility model Phase 4A uses for its universe_snapshot. Child jobs use the frozen snapshot. Operator override ticker lists are separate job inputs with their own snapshot/hash. Refresh does not read a changing GTL source mid-flight.
   - **Path 2 (operator-only):** Phase 5D MAY expose an operator-only Dash control for testing on-demand requests; the public manual-entry user experience is Phase 6.
   - **Output contract:** Controlled compute refreshes producer artifacts and emits Phase 4A-compatible run directories. No parallel output contract.

5. **Phase 5G — Pre-launch data licensing gate** (parallel; doc/process work; not a numbered implementation sub-phase):
   - Document yfinance constraints
   - Evaluate alternate data providers for Phase 6 (Polygon, EODHD, Bloomberg, derived-research posture)
   - Output: a decision memo md_library/ document; not code
   - Phase 6 final launch scoping cannot lock until 5G is complete because 5G may constrain public launch scope, data provider posture, or redistribution claims

**Out of scope:**

- Public website / UI accessibility work (Phase 6)
- Public manual-entry UX (Phase 6; 5D Path 2 is operator/backend only)
- Phase 6 universe selection (curated/tiered launch decisions)
- Bring-your-own-data ingestion (Phase 7+)
- Volunteer-contributed compute (Phase 7+)
- Pattern submission / verification / knowledge-base layer (Phase 7+)
- Alternate data source integration is out of scope for Phase 5 implementation; 5G may recommend launch constraints, alternate provider work before Phase 6, or post-sprint provider migration
- QC clone / live trading wiring (parked indefinitely)
- Confluence engine UI completion (separate decision; not Phase 5)
- Spymaster_confluence_bridge revival (separate decision; not Phase 5)

## Design principles

1. **Honesty over polish.** Validation report exposes coverage gaps, methodology limitations, and known unknowns.
2. **Cleanup risk is not assumed low.** Anything that deletes behavior, changes CLI surface, modifies environment assumptions, or changes cross-app semantics requires preflight. 5A triage exists to make 5B PRs auditable in advance.
3. **Engine/presentation separation strictly preserved.** Path 2 (within 5D) queues via the engine layer; Dash never invokes producers directly.
4. **Controlled, not volunteer.** Phase 5D compute is local/cloud orchestration only.
5. **Manifest contract participation.** Validation reports, refresh runs, and on-demand stackbuilds all participate in the Phase 3 manifest contract.
6. **No new Phase 6 features.** If a Phase 5 item starts to look like Phase 6 (public UX, accessibility branding, public-facing styling), defer to Phase 6.
7. **Licensing gates Phase 6.** 5G can block or constrain Phase 6 scope; it runs early/parallel, not last.

## Implementation phasing

**Phase 5A: Sprint-state hygiene + cleanup triage.**
Doc/config-only. Single PR. Produces the cleanup/deferred-item ledger and classifies each cleanup as delete / deprecate / rename / behavior-change / cross-app reconciliation. No code deletion or behavior change.

**Phase 5B: Targeted cleanup PRs.**
Runs from 5A classification. Multiple small PRs are allowed. Preflight is required for any PR that deletes behavior, changes CLI surface, touches environment files, or changes cross-app semantics. Tiny single-file cleanups may skip a separate preflight only when 5A classifies them as no behavior deletion, no public CLI change, no environment-contract change, and no cross-app semantic impact.

**Phase 5C: Honest validation program.**
Preflight required only for residual implementation questions (see Open Questions section). Two-step track: 5C-1 methodology doc PR -> 5C-2 validation engine/report PR. Methodology must be locked before implementation begins.

**Phase 5D: Controlled compute + Path 2 backend.**
Preflight required only for residual implementation questions (see Open Questions section). One architecture track. Implementation may split into multiple PRs, but the queue/job model is designed once.

**Phase 5G: Pre-launch data licensing gate.**
Doc/process work. May proceed in parallel as early as 5A/5C. No code. Phase 6 final launch scoping cannot lock until this review is complete.

5G runs in parallel with implementation sub-phases. 5C-2 depends on 5C-1 lock. Otherwise sub-phase ordering is at Peter's discretion.

## User-facing question Phase 5 answers

For Peter and future operators: "Is the engine's output trustworthy and reproducible? Can the curated universe stay current without manual intervention? Can a ticker outside the curated universe be processed on demand? What are the licensing constraints for going public?"

For Phase 6 prerequisites: validation methodology + report; controlled compute orchestration with on-demand backend; licensing decisions.

## Open questions deferred to specific sub-phase preflights

Sub-phases with NO deferred questions (truly doc-only): 5A, 5G.

Sub-phases with residual deferred questions (high-level scope locked above; preflight refines schema/cadence/implementation specifics):

**Phase 5B (Targeted cleanup PRs) preflight questions:**
- Which items are deletion vs deprecation note vs rename vs behavior change?
- What tests pin each deletion / rename?
- Which items are doc-only vs require code change?
- Which cleanup items batch into one PR vs require separate PRs?

**Phase 5C-2 (validation engine + report) preflight questions:**
- Exact validation report artifact JSON schema (field names, nesting).
- Exact set of Phase 4A output artifacts consumed (which paths, which manifests).
- Specific source artifacts beyond Phase 4A (price source, outcome data, universe-frozen reference).
- How the engine prevents future-period leakage at the data-loading layer (specific guard).
- Backtest determinism + seedability for reproducibility.
- Exact dual-output write paths and naming conventions.

**Phase 5D (Controlled compute + Path 2 backend) preflight questions:**
- Exact local orchestration tool choice (Windows Task Scheduler, cron-equivalent, Python scheduler, custom job runner).
- Exact durable job/run schema fields.
- Exact rate-limit representation (yfinance pacing, retries, backoff).
- Exact dead-letter and alerting mechanics.
- Idempotency mechanics per job type.
- Duplicate-prevention policy for concurrent on-demand requests on the same ticker.
- Queue persistence model (in-process, SQLite, filesystem markers).
- Worker model (long-running daemon vs on-demand process spawn).
- Path 2 polling/notification UX pattern.
- Cancellation/retry semantics for queued jobs.
- Run-status integration: extending Phase 4B `cross_ticker_confluence_dash` vs separate ops dashboard surface (final placement decision).

## Decisions captured

From Peter, May 5 2026:

- Phase 5 is a real new phase, not a sub-phase of Phase 4.
- Phase-level preflights only; per-sub-phase preflights only when this pre-flight doc explicitly defers questions to them.
- Volunteer compute stays Phase 7+; Phase 5D is controlled (local/cloud).
- Path 2 manual entry must preserve engine/presentation separation; public UX is Phase 6.
- yfinance is the data source through Phase 5; licensing review (5G) happens early and may gate Phase 6.
- Phase 4 contract (manifest participation, schema isolation toward BYO, no future-period signal, source precedence OnePass -> Spymaster fallback -> skipped, coverage transparency) extends into Phase 5 work without amendment.

From Codex first-pass review, May 5 2026:

- Phase 5A is doc/config triage only; cleanup risk is not assumed low.
- Multi-primary reconciliation is classified by 5A triage.
- Honest validation cannot consume only Phase 4A outputs; price / outcome data without future leakage is required.
- Licensing (5G) is gatekeeping, runs parallel/early.

From Codex second-pass review, May 5 2026:

- Validation methodology + engine consolidated into single Phase 5C program (two PRs: 5C-1 doc, 5C-2 engine).
- Controlled compute + Path 2 on-demand consolidated into single Phase 5D architecture track.
- Controlled compute emits Phase 4A-compatible run directories; no parallel output contract (locked rule).
- 5G licensing repositioned as explicit Phase 6 launch-scoping gate.
- Tiny-cleanup escape hatch tightened with explicit classification criteria.

From Codex pre-lock fill-in pass, May 5 2026 (12 deferred questions resolved against existing repo and locked-doc context):

- 5C-1 outcome windows: forward-return validation windows of 1, 5, 21, 63, 252 trading days; distinct from Phase 4A signal intervals.
- 5C-2 baseline: same-ticker buy-and-hold over the validation window (MVP).
- 5C-3 claims-NOT-made: no investment advice, no trading signals, no future-return predictions, no guarantees, no causal claims, no "best ticker" rankings.
- 5C-4 minimum validation report content: producer last-updated, Phase 4A coverage/issue counts, validation metrics for locked windows, baseline comparison, explicit stale/missing/failed coverage.
- 5C-5 dual report location: manifest-stamped artifact under `project/output/validation/<run_id>/` + human-readable doc under `project/md_library/shared/`.
- 5C-6 versioning: starts at validation_methodology_v1; version-bumps recorded with date and rationale.
- 5C-7 canonical vs exploratory: canonical outputs are locked/reproducible/public; exploratory diagnostics labeled non-canonical until promoted via methodology amendment.
- 5D-1 orchestration target: local-first MVP; cloud later; volunteer Phase 7+.
- 5D-2 run-status surfacing: durable job/run status files + operator view extending Phase 4B; state machine queued/running/succeeded/failed/skipped/partial.
- 5D-3 job-type taxonomy: gtl_snapshot, onepass_daily_refresh, multitimeframe_refresh, stackbuilder_refresh, phase4a_aggregate, validation_support_run, path2_mini_run; nightly OnePass + Phase 4A; non-daily intervals after close; StackBuilder for curated/queued tickers; Path 2 queue-triggered.
- 5D-4 Path 2 cache: show any cached result with last-updated; >=45-day staleness threshold; operator/user explicitly requests fresh compute; no auto-recompute.
- 5D-5 GTL interaction: snapshot universe at job start (source path, file_sha256, universe_hash, counts, ordered series); operator overrides as separate inputs with their own snapshot.

## Document status

**Locked.** Sub-phases proceed; 5G may run in parallel with any later sub-phase, while 5B cleanup implementation depends on 5A triage classification. Any changes to this document require explicit amendment with date and rationale.
