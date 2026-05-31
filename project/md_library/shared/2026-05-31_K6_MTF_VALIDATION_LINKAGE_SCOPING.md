# K=6 MTF Validation Linkage Scoping

**Date:** 2026-05-31

**Status:** Docs-only scoping document. Defines the linkage
between the public React K=6 MTF claim surface and Phase 5C
honest-validation evidence so that later evidence-production
work can be sized against a defined contract instead of
producing sidecars that do not bind to the public claim.

**Anchor documents:**

- `project/md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md`
  -- `validation_contract_v1` and `validation_methodology_v1`,
  including the Section 13 per-app current-state mapping.
- `project/md_library/shared/2026-05-08_PHASE_5D_1_OPERATIONAL_ONBOARDING.md`
  -- the dry-run / real-run / honest-validation-ledger
  regeneration operator sequence and the
  sidecar-discovery contract.
- `project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
  -- the `k6_mtf_ranking_v1` schema and the K=6 MTF
  combined-signal-and-match-rule construction.
- `project/md_library/shared/2026-05-31_REACT_PUBLISH_DEPLOY_CONTRACT.md`
  -- the `validation_results` public-promotion object and
  the Phase 5 public-launch gate.
- `project/utils/react_publish/promote_k6_mtf_artifact.py`
  -- the operator-run promotion helper that hard-refuses
  public mode without a verified Phase 5 report and records
  the report path project-relative.
- `project/md_library/shared/2026-05-23_PHASE_6I_79_STACKBUILDER_PRODUCTION_RUN_EVIDENCE.md`
  -- the production StackBuilder run that feeds the live
  K=6 MTF ranking artifact and the `--skip-durable-validation`
  decision recorded there.
- `project/md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md`
  -- the Phase 5 honest-validation requirement.
- `project/md_library/shared/2026-04-30_PRJCT9_SPRINT_PLAN.md`
  -- Section 5 Phase 5 detail.
- `project/CLAUDE.md` Section 6 -- live operating contract
  and Phase 5 public-launch gate restatement.

---

## 1. Status and Scope

- This is a **docs-only scoping document**.
- It **defines** the K=6 MTF -> Phase 5C validation linkage:
  what the public claim is, what evidence must validate it,
  and how that evidence binds to the artifact that the public
  React app serves.
- It does **NOT** produce evidence, run compute, run
  `validation_engine.py`, run `honest_validation_ledger.py`,
  run `controlled_compute.py`, run any pipeline stage, change
  source, change the `k6_mtf_ranking_v1` schema, alter React
  runtime, promote artifacts, or deploy.
- It **unblocks** later evidence-production work; it does
  **NOT itself satisfy** the Phase 5 honest-validation gate.
- Public deployment of the React K=6 MTF MVP board remains
  blocked by the Phase 5 honest-validation gate. This doc
  does NOT clear that gate.

---

## 2. Public Claim of Record

The public React K=6 MTF board fetches one `k6_mtf_ranking_v1`
JSON ranking artifact and renders the per-secondary records
for the 8 Tier 1 secondaries (AAPL, AMZN, GOOGL, META, MSFT,
NVDA, SPY, TSLA). The fields it surfaces, classified by claim
type, are:

### Phase-5-gated performance / ranking claims

These are the public claims that require Phase 5C
honest-validation evidence before public launch:

- `rank` (table column "Rank") -- the headline ordering of
  the 8 secondaries. This is the headline ranking claim.
- `sharpe_k6_mtf` (table column "Sharpe Score") -- the per-
  secondary Sharpe over matched bars; the headline historical-
  performance scalar.
- `total_capture_pct` (modal metric) -- cumulative captured
  return summed over `capture_count` bars.
- `avg_capture_pct` (modal metric) -- average per-trade
  capture across the directional-trade subset.
- `stddev_pct` (modal metric) -- sample standard deviation of
  per-trade captures.
- `win_pct` (modal metric) -- `win_count / trade_count * 100`
  when `trade_count > 0`.
- `win_count`, `loss_count` (modal counts) -- per-trade
  win / loss tallies.
- Count taxonomy: `match_count`, `capture_count`,
  `trade_count`, `no_trade_count`, `skipped_capture_count`.
- `ccc_series` (rendered as the CCC step chart in the modal)
  -- cumulative capture over matching bars; the headline
  historical-performance time series.

### Provenance / freshness fields (NOT Phase-5-gated by themselves)

These fields are part of the manifest-stamped data layer and
are not credibility claims in their own right:

- `generated_at_utc` -- when the ranking artifact was produced.
- `run_id` -- the producing K=6 MTF run identifier.
- `history_as_of_date` -- per-secondary as-of date.
- `history_artifact_path` -- per-secondary history artifact
  reference (display-only in the React modal).
- `current_snapshot` -- the 5-timeframe `(1d, 1wk, 1mo, 3mo,
  1y)` tuple at the as-of date.
- `k6_stack.members` and their `[D]/[I]` protocols -- the
  K=6 stack provenance.

### Honesty / coverage signals (NOT Phase-5-gated by themselves)

These are inherent transparency signals; their presence does
not substitute for a Phase 5 report:

- `low_sample_warning` -- per-secondary low-sample flag.
- Per-secondary `issues` array.
- Top-level `issues` array.
- Failed / unranked records informational section.

The Phase 5 public-launch gate fires on the first group; the
second and third groups are honest signals but do not satisfy
the gate by themselves.

---

## 3. Validation Target Decision -- BOTH, Layered

The operator has decided: **the public Phase 5 validation
claim validates BOTH the StackBuilder selected builds AND the
K=6 MTF ranking rows themselves, as a layered claim.**

This is recorded as decided. It is not an open question in
this document.

### StackBuilder layer

The StackBuilder selected build for each of the 8 Tier 1
secondaries must survive `validation_methodology_v1`,
including:

- walk-forward fold construction per Section 4 of the
  Phase 5C methodology,
- outcome-window grid `{1, 5, 21, 63, 252}` trading days per
  Section 5,
- same-ticker buy-and-hold baseline per Section 6,
- BH primary + Bonferroni supplementary multiple-comparisons
  control per Section 7,
- empirical permutation / bootstrap layer where applicable.

This is the upstream-selection layer. It validates the K
choice, the member set, and the `[D]/[I]` protocol assignment
that the K=6 MTF history producer consumes via each
secondary's `selected_build.json`.

### K=6 MTF ranking-row layer

The K=6 MTF combined-signal-and-match-rule construction that
yields the publicly displayed `rank`, `sharpe_k6_mtf`,
capture metrics, win / loss tallies, count taxonomy, and CCC
display must survive the same honest-validation standard.

The K=6 MTF ranking row is a different statistical object
from the StackBuilder selected build: it composes six
`[D]/[I]`-adjusted member signals through the TrafficFlow-
style active-signal-unanimity combine, forward-fills across
five timeframes, applies the match-rule wildcard pass against
the current snapshot, and computes the per-trade Sharpe over
matched directional bars. The Phase 5C methodology must apply
to the ranking-row construction itself, not only to the
upstream selection.

### Rationale

The StackBuilder selected build and the K=6 MTF ranking row
are different statistical objects. Validating only
StackBuilder would validate inputs while leaving the
public-facing K=6 MTF ranking construction unvalidated.
Validating only K=6 MTF rows would leave upstream selection
unexamined. The honest public claim validates the presented
pattern (the K=6 MTF ranking row) and its upstream selected
components (the StackBuilder selected build).

---

## 4. Linkage Design -- Scoping, Not Implementation

This section evaluates how `validation_contract_v1` evidence
attaches to the public claim. It does NOT implement any
linkage; it scopes the contract that a later implementation
PR will follow.

### Options considered

- **Option L1 -- top-level validation fields on
  `k6_mtf_ranking_v1`.** Add fields such as
  `validation_status`, `validation_artifact_path`,
  `validation_artifact_hash` at the artifact root. Pros:
  React can see validation status in a single fetch. Cons:
  schema change to a locked artifact; React would need to
  decide how to render validation state and what to do when
  status is missing; couples validation-evidence flow to the
  artifact-boundary surface React already consumes.
- **Option L2 -- per-secondary validation fields on
  `k6_mtf_ranking_v1`.** Same as L1 but per-row. Same
  trade-offs at higher granularity.
- **Option L3 -- separate validation manifest sibling.** A
  JSON manifest next to the ranking artifact carrying
  validation evidence references for both layers. Pros: keeps
  the ranking artifact schema unchanged. Cons: introduces a
  second runtime input for React if React reads it; if React
  does NOT read it, the manifest is a publish-side artifact
  only and serves the same role as Option L4.
- **Option L4 -- report-level honest-validation package
  referenced by the PR #367 / #368 promotion manifest
  `validation_results.phase_5_validation_report_path`.**
  The launch-universe sidecars (StackBuilder layer plus K=6
  MTF layer when that producer support exists) feed
  `honest_validation_ledger.py`, which produces a JSON +
  Markdown report under `<PROJECT_DIR>` with a SHA-256. The
  promotion helper's public-mode hard-refusal already
  requires that report path and SHA. No React runtime input
  changes.

### Recommendation -- Option L4 as the minimal-change linkage

The recommended linkage is **Option L4**: a report-level
honest-validation package produced from the launch-universe
sidecars, referenced from the PR #367 / #368 promotion
manifest's
`validation_results.phase_5_validation_report_path`.

Reasons:

- The PR #367 contract already names this field as the
  structural public-launch gate hook. PR #368 already
  implements the hard-refusal on missing / wrong / outside-
  project Phase 5 inputs. Using L4 takes the linkage that
  already exists at the publish gate and points it at the
  ledger output.
- The `k6_mtf_ranking_v1` schema does not change. No
  amendment to the K=6 MTF launch-path contract is needed.
- React still fetches exactly one ranking JSON artifact at
  runtime. No second runtime input. No runtime boundary
  change.
- The ledger output is the natural "review artifact" surface
  the operator and any future reviewer can inspect alongside
  the report; it is also the surface a future site can link
  to as the documented Phase 5 report.

### Optional later metadata (NOT scoped by this doc)

Per-artifact or per-secondary metadata on `k6_mtf_ranking_v1`
(Options L1 or L2 in narrowed form) MAY be added later if a
future contract amendment decides browser-visible validation
status on each row is desirable. That decision is OUT OF
SCOPE here. Any such amendment must amend the K=6 MTF
launch-path contract first and must preserve the React
artifact-boundary rule.

### React runtime-boundary statement

React MUST continue to fetch exactly one `k6_mtf_ranking_v1`
JSON artifact for the board. The Phase 5 report and any
promotion manifest sidecar are public-promotion gate
artifacts and operator-review artifacts; they are NOT React
runtime inputs unless a future contract explicitly amends the
React Migration Declaration and the K=6 MTF launch-path
contract together. This document does not amend either.

---

## 5. Phase 5C Tier Classification

The Phase 5C methodology classifies producers into three
tiers (durable, interactive, exploratory) per the methodology
document Input contract section. Given the layered validation
decision in Section 3, the classification is:

- **StackBuilder selected builds**: durable-tier upstream
  evidence. The StackBuilder producer is already integrated
  with `validation_engine.write_validation_sidecar`. This
  classification is unchanged; the operator must run the
  launch-universe StackBuilder evidence campaign with
  `--skip-durable-validation` NOT set so that this tier
  actually emits the sidecars it is contracted to emit.
- **K=6 MTF ranking rows**: durable-tier public-claim
  producer in its own right for the public launch. The K=6
  MTF ranking artifact carries the headline public
  performance and ranking claims; under 5C Section 3 a
  producer of a persisted public-claim artifact is
  durable-tier and MUST emit validation evidence on the
  persistence transaction.
- **The public report itself**: layered. It composes the
  StackBuilder durable-tier evidence (upstream selection)
  and the K=6 MTF durable-tier evidence (public-claim
  construction) into one honest-validation report referenced
  by the promotion manifest.

This classification creates a **future implementation
requirement, not an existing capability** (see Section 6).

---

## 6. Current Implementation Capability

Recording what exists today, separated cleanly from what
this doc defines for future work:

- **Phase 5C validation engine exists.** `validation_engine.py`
  is implemented (walk-forward folds, outcome-windows,
  BH / Bonferroni, empirical layer, contract validation, run
  IDs, sidecar writer, artifact hashing).
- **`honest_validation_ledger` exists.** `honest_validation_ledger.py`
  discovers sidecars, ingests them, builds the run / strategy
  / app summaries, and emits the ledger JSON + Markdown.
- **`controlled_compute` exists.** `controlled_compute.py`
  implements the dry-run / real-run / sidecar-discovery /
  contract-validation / hash-recording orchestrator.
- **StackBuilder validation support EXISTS today.**
  `stackbuilder.py` imports `validate_strategy_set` and
  `write_validation_sidecar` from `validation_engine`,
  registers `producer_engine='stackbuilder'`, defines a
  `VALIDATION_CONTRACT_VERSION` constant, and includes a
  `_build_failed_validation_contract` fallback. The 5D-1
  onboarding runbook documents the exact operator command
  sequence that exercises this path through `controlled_compute`.
  StackBuilder evidence can be produced today by running the
  campaign WITHOUT `--skip-durable-validation`.
- **K=6 MTF validation support DOES NOT EXIST today.**
  `k6_mtf_ranking_engine.py` and `k6_mtf_history_producer.py`
  contain ZERO references to any of `validation_contract`,
  `validation_engine`, `validate_strategy_set`, or
  `write_validation_sidecar`. `validation_engine.py`,
  `honest_validation_ledger.py`, and `controlled_compute.py`
  contain ZERO references to `k6_mtf` / `K6_MTF`. The K=6 MTF
  ranking artifact carries no validation fields. No spec
  defines how a K=6 MTF ranking-row producer maps to the
  `validation_contract_v1` schema (what counts as a strategy
  candidate, what the strategy fold result is, what
  selection adapter applies, how walk-forward folds compose
  with the K=6 MTF match-rule construction).

### Consequences

- A later **K=6 MTF validation adapter / producer
  implementation PR** is required before the K=6 MTF
  ranking-row layer can be produced. That PR must define how
  K=6 MTF ranking rows compose with `validation_contract_v1`
  (strategy unit, walk-forward fit / refit semantics,
  baseline coherence, selection adapter interface) and add
  the producer integration (`producer_engine='k6_mtf'` or
  equivalent, sidecar writer call site, run-id generation).
- **A controlled-compute campaign alone is not sufficient.**
  Running `controlled_compute` against StackBuilder for the
  8 Tier 1 launch tickers would produce the StackBuilder
  layer of the report; it would NOT produce the K=6 MTF
  ranking-row layer. The layered decision in Section 3
  requires both layers before the report can be considered
  complete for public launch.

---

## 7. Evidence Production Path -- Later Work Only

This section names the launch-universe campaign that this
scoping unblocks once the prerequisites in Section 6 are met.
**This document does not run the campaign.**

### Launch universe (Tier 1)

- AAPL
- AMZN
- GOOGL
- META
- MSFT
- NVDA
- SPY
- TSLA

### Required producer prerequisites

- StackBuilder layer: producer support already exists (Section
  6). The launch-universe StackBuilder campaign must run
  **without** `--skip-durable-validation` so the durable-tier
  sidecars are actually emitted.
- K=6 MTF layer: producer support DOES NOT exist (Section 6).
  A future implementation PR must add the K=6 MTF validation
  adapter / producer before the K=6 MTF layer can be run.

### Expected outputs

- `validation_contract_v1` sidecars under
  `output/validation/<run_id>/validation.json` for both
  layers, once both producers are supported.
- `honest_validation_ledger` JSON and Markdown report under
  `output/validation_ledger/` covering BOTH layers for the 8
  Tier 1 secondaries.
- A final hashable report path under `<PROJECT_DIR>` (the
  Markdown ledger report, the JSON ledger, or a packaged
  honest-validation report referencing them) that the PR #368
  public-mode promotion can verify and record in
  `validation_results.phase_5_validation_report_path` with
  the report's SHA-256 in
  `validation_results.phase_5_validation_report_sha256`.

### Phase 5D-1 runbook re-spec

The Phase 5D-1 operational onboarding runbook documents the
dry-run / real-run / ledger-regeneration sequence against a
SPY + 5-ticker smoke universe (`AAPL, MSFT, NVDA, QQQ, IWM`).
The launch-universe campaign requires re-speccing the runbook
to the 8 Tier 1 secondaries (AAPL, AMZN, GOOGL, META, MSFT,
NVDA, SPY, TSLA) and to both producer layers (StackBuilder
and K=6 MTF). That re-spec is a separate later PR, not this
scoping doc.

### Operator supervision

The campaign remains operator-supervised end-to-end:

- The operator authorizes the StackBuilder launch-universe
  campaign (no `--skip-durable-validation`).
- The operator authorizes the K=6 MTF launch-universe
  campaign after the K=6 MTF validation producer lands.
- The operator regenerates the ledger from the produced
  sidecars.
- The operator reviews the ledger report (BH survivors AND
  non-survivors, baseline coherence, coverage gaps, skipped
  / failed counts) and authorizes the public-launch
  acknowledgment.
- Only then can a public-mode promotion attempt succeed
  (PR #368 helper still enforces hard refusal on any missing
  or unverified input).

---

## 8. Known Current Evidence State

Recording the preceding Phase 5 audit findings as
audit-state facts. Local-output findings reflect the local
working tree at audit time; they are not permanent repo
facts.

- No accepted public-launch honest-validation report exists.
- No launch-universe `validation_results` target exists.
- Local audit found `output/validation/` absent.
- Local audit found `output/validation_ledger/` absent.
- Local audit found `output/controlled_compute/` absent.
- The StackBuilder production runs feeding the live K=6 MTF
  artifact were operated with `--skip-durable-validation`
  (per the Phase 6I-79 production-run evidence document). 5
  of 8 selected builds carry
  `durable_validation_status='skipped'`; 3 have the field
  absent; 0 of 8 carry a `validation_artifact_path` or
  `validation_artifact_hash`.
- The K=6 MTF ranking artifact at
  `output/k6_mtf/20260528T083411Z_post_fix/k6_mtf_ranking.json`
  exists and is operator-authorized for the current React
  fixture, but carries no validation linkage in its top-level
  or per-secondary fields.

PR #368 (the operator-run promotion helper) **correctly
refuses public promotion today** because the Phase 5 report
input does not exist. That hard refusal is the safety
property doing its job. This scoping doc does not change that
behavior; it names what the eventual Phase 5 report input
must be so the helper can eventually succeed for the operator-
acknowledged public launch.

---

## 9. Public-Launch Implications

This doc aligns the private / helper work in PRs #367 and
#368 back to the public launch path by defining what the
Phase 5 report must validate (Section 2 and Section 3) and
how that report binds to the promotion manifest (Section 4).

**This doc does NOT satisfy Phase 5.**

Sequence after this doc:

1. Implement or specify K=6 MTF validation producer support.
   The implementation PR adds the adapter so that
   `controlled_compute` plus the K=6 MTF ranking-row producer
   can together emit `validation_contract_v1` sidecars for the
   K=6 MTF layer.
2. Scope and run the controlled-compute campaign for the 8
   Tier 1 launch tickers, covering BOTH layers.
   `--skip-durable-validation` MUST NOT be set.
3. Generate and review the `honest_validation_ledger` JSON +
   Markdown report covering both layers.
4. Use the report path and SHA-256 in PR #368 public-mode
   promotion (`--public --phase5-report ... --phase5-sha256
   ... --write --operator-approved`).
5. Keep public deployment blocked until the operator
   deliberately clears the gate. Public deployment is a
   separate operator act even after the Phase 5 report
   exists.

**Phase 5G data licensing remains a separate public-launch
gate.** This document flags it as a parallel gate to keep
visibility honest, but does NOT scope it here.

---

## 10. Non-Goals

The following are explicitly OUT OF SCOPE of this scoping
document:

- No compute.
- No validation sidecar production.
- No honest-validation ledger run.
- No source change.
- No test change.
- No React change.
- No `k6_mtf_ranking_v1` schema change in this PR.
- No deploy.
- No artifact promotion.
- No private deployment-target selection.
- No Tier 2 growth-queue scoping.
- No CI or deploy configuration change.
- No `output/` mutation.
- No `.claude/` change.
- No public-launch acknowledgment. Even after evidence
  exists, public launch is a deliberate operator act.

---

## 11. Next Implementation Options

The next public-aligned PR depends on whether K=6 MTF
validation producer support exists.

- **K=6 MTF validation producer support DOES NOT exist
  today** (per Section 6). The next public-aligned PR is
  therefore a **K=6 MTF validation producer / adapter spec
  or implementation plan**. That PR may itself be docs-only
  (a spec that names the strategy unit, walk-forward fit /
  refit semantics, baseline coherence, selection-adapter
  interface, sidecar emission point, and per-secondary
  contract) before any source change; or it may be a source
  PR that adds the adapter alongside targeted tests, still
  with no compute unless the operator explicitly authorizes
  it.
- **After K=6 MTF validation producer support lands**, the
  next public-aligned PR is the launch-universe controlled-
  compute run spec for the 8 Tier 1 tickers, still operator-
  supervised and still with no compute until the operator
  explicitly authorizes the run.
- **Phase 5G data licensing** runs in parallel as its own
  gate.

In all cases, the eventual evidence production remains
operator-supervised and public-launch aligned. Public
deployment remains blocked until the operator deliberately
clears the Phase 5 honest-validation gate and the Phase 5G
data-licensing gate.

---

## Amendment History

- 2026-05-31 (initial). Records the layered validation target
  (StackBuilder selected builds AND K=6 MTF ranking rows) as
  decided. Recommends Option L4 (report-level honest-
  validation package referenced from the PR #367 / #368
  promotion manifest) as the minimal-change linkage.
  Documents that K=6 MTF validation producer support does
  not exist today and names the future implementation step
  required before the K=6 MTF layer can be produced.
  Records the current evidence state and the launch-universe
  campaign that this scoping unblocks. No compute. No source
  change. No schema change. No React change. No deploy.
