# K=6 MTF Validation Producer Adapter Spec

**Date:** 2026-05-31

**Status:** Docs-only specification. Defines how a K=6 MTF
ranking-row producer maps onto the locked `validation_contract_v1`
schema, following the StackBuilder validation-integration
pattern where applicable. Does NOT implement the adapter,
does NOT produce evidence, does NOT change source / schema /
React runtime, does NOT run pipeline compute, and does NOT
mutate `output/`.

**Anchor documents (stable section references; no line numbers):**

- `project/md_library/shared/2026-05-31_K6_MTF_VALIDATION_LINKAGE_SCOPING.md`
  -- PR #369 linkage doc; layered validation target and
  Option L4 minimal-change linkage.
- `project/md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md`
  -- `validation_contract_v1`, `validation_methodology_v1`,
  walk-forward defaults, baseline contract, multiple-
  comparisons contract, empirical layer contract, status
  taxonomy, durable / interactive / exploratory tiers, the
  Section 13 per-app current-state mapping.
- `project/md_library/shared/2026-05-08_PHASE_5D_1_OPERATIONAL_ONBOARDING.md`
  -- the dry-run / real-run / ledger regeneration operator
  sequence and the sidecar-discovery contract.
- `project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
  -- the `k6_mtf_ranking_v1` schema, the Match Rule, the
  Trade Direction rule, the Capture and Honest Sharpe
  definitions, the CCC Time Series semantics, the Ranking
  and Fail-Closed Behavior sections.
- `project/md_library/shared/2026-05-31_REACT_PUBLISH_DEPLOY_CONTRACT.md`
  -- the `validation_results` public-promotion object and
  the Phase 5 public-launch gate.
- `project/utils/react_publish/promote_k6_mtf_artifact.py`
  -- the operator-run promotion helper that hard-refuses
  public mode without a verified Phase 5 report.
- `project/validation_engine.py` -- the engine that this
  adapter spec follows: `SelectionAdapter` Protocol,
  `validate_strategy_set`, `StrategyCandidate`,
  `StrategyFoldResult`, `FoldContext`, `BaselineFoldMetrics`,
  `write_validation_sidecar`, `validate_validation_contract_v1`,
  `compute_validation_artifact_hash`, `generate_run_id`,
  `compute_walk_forward_folds`, `outcome_returns_at_horizon`.
- `project/stackbuilder.py` -- the existing validation-
  integration template this adapter mirrors.
- `project/controlled_compute.py` -- the orchestrator that
  will discover the future K=6 MTF sidecars.
- `project/honest_validation_ledger.py` -- the ledger
  generator that will ingest the future K=6 MTF sidecars.

---

## 1. Status and Scope

- This is a **docs-only specification**.
- It is **public-launch aligned**: it specifies the K=6 MTF
  validation producer / adapter that PR #369 identified as
  missing. Closing that gap is a prerequisite for producing
  the K=6 MTF layer of the public Phase 5 honest-validation
  report.
- It does **NOT** implement source, tests, schema changes,
  evidence production, compute, React changes, deployment,
  artifact promotion, `output/` mutation, or `.claude/`
  changes.
- It does **NOT** satisfy the Phase 5 honest-validation
  public-launch gate. PR #368 will still hard-refuse
  public-mode promotion until the report input exists.
- Public deployment of the React K=6 MTF MVP board remains
  blocked.

---

## 2. Relationship to PR #369 and the StackBuilder Template

PR #369 (the linkage scoping doc) decided that the public
Phase 5 claim validates BOTH layers (StackBuilder selected
builds AND K=6 MTF ranking rows), recommended Option L4
(report-level honest-validation package referenced from the
PR #367 / #368 promotion manifest), and recorded that K=6 MTF
producer support **does not exist today**. PR #369 names "a
later K=6 MTF validation adapter / producer implementation
PR" as the next public-aligned source step.

**This document specifies that adapter contract.** It does
NOT amend PR #369. It does not re-open the layered-target
decision. It does not change the recommended Option L4
linkage.

### StackBuilder template extracted from real code

The existing StackBuilder validation integration in
`project/stackbuilder.py` is the precedent. Concrete
properties this adapter mirrors:

- **Producer identity (StackBuilder).** `producer_engine="stackbuilder"`,
  `app_surface="run_directory"`. The producer string is
  surfaced into the contract and into the run-id namespace.
- **Adapter implements `SelectionAdapter`.** StackBuilder
  registers a class that implements the engine's
  `SelectionAdapter` Protocol; the engine drives
  `select_for_fold(ctx)`, `evaluate_candidate(candidate,
  ctx)`, and `baseline_for_fold(ctx)` per fold.
- **Single `validate_strategy_set` call.** StackBuilder
  passes the adapter, the history `DatetimeIndex`, the
  `run_id`, the producer / app strings, and the
  methodology-default parameters. The engine returns the
  fully-formed contract dictionary.
- **`write_validation_sidecar` writes the JSON sidecar.**
  StackBuilder writes the returned contract into
  `output/validation/<run_id>/validation.json` using the
  engine helper.
- **`--skip-durable-validation` opt-in.** When set, the
  StackBuilder run manifest records
  `durable_validation_status="skipped"` and a
  `durable_validation_skip_reason` string, with
  `validation_artifact_path` and `validation_artifact_hash`
  deliberately `None` (the spec does not fabricate them).
  When unset, the run runs the durable validation path; if
  validation raises, a `_build_failed_validation_contract`
  fallback emits a `FAILED`-status contract instead of
  silently swallowing the failure.
- **Ledger discoverability.** Sidecars land under
  `output/validation/<run_id>/validation.json`; the standard
  `discover_validation_sidecars` `rglob("validation.json")`
  finds them; `_build_app_summary` groups by
  `producer_engine`.

The K=6 MTF adapter mirrors every one of these properties.
Any divergence is named and justified explicitly in the
sections below.

---

## 3. Producer Identity

PROPOSED producer identity for the K=6 MTF adapter:

- `producer_engine`: **PROPOSED value `"k6_mtf"`.** The
  current `validation_engine.py` does not enforce a closed
  set of `producer_engine` strings at the engine level; the
  string is passed through into the contract. The current
  `validation_contract_v1` `producer_engine` list names
  `spymaster`, `impactsearch`, `confluence`, and
  `stackbuilder`; the Phase 5C methodology Section 13 per-app
  mapping covers those producers and does NOT list K=6 MTF.
  The string `"k6_mtf"` is therefore **NOT yet valid at the
  methodology contract level**. The later implementation PR
  MUST amend Section 13 of the Phase 5C methodology document
  to add K=6 MTF as a producer before the adapter emits any
  sidecars labelled `"k6_mtf"`. No `validation_engine.py`
  code change is required for the string itself, unless a
  later implementation chooses to add stricter engine-level
  enforcement.
- `app_surface`: **PROPOSED value `"run_directory"`.**
  Mirrors the StackBuilder value. Justification: like
  StackBuilder, a K=6 MTF run produces a per-secondary
  output directory under `output/k6_mtf/<run_id>/<SEC>/`
  containing `k6_mtf_history.json` plus the run-level
  `k6_mtf_ranking.json`. The validation sidecar describes a
  whole run-directory's worth of per-secondary rows, not a
  single XLSX or single Dash session. `"run_directory"`
  reuses the existing value rather than introducing a
  snowflake.
- `run_id`: **PROPOSED to use `generate_run_id(producer_engine="k6_mtf",
  app_surface="run_directory")`** from `validation_engine`.
  This mirrors StackBuilder's use of the same engine helper
  and keeps run-id formatting consistent across producers.

### What this means for the implementation PR

- The implementation PR amends Phase 5C Section 13 to add
  K=6 MTF.
- It does NOT need to amend `validation_engine.py` itself
  for the producer string.
- The amendment lands in the same PR as the adapter source,
  or in a small precursor docs-only methodology amendment
  PR if the operator prefers contract amendments to land
  alone.

---

## 4. Strategy Unit -- Candidate Model

A K=6 MTF validation candidate represents the **per-
secondary K=6 MTF ranking-row construction**: the natural
unit of the public claim is one ranking row per secondary.
This is one `StrategyCandidate` per secondary, not one
candidate per StackBuilder primary or per K=6 stack member.

### Mapping onto the engine's `StrategyCandidate` fields

The engine's `StrategyCandidate` dataclass has three fields:
`strategy_id: str`, `strategy_label: str`, and
`app_payload: Mapping[str, Any]`. The K=6 MTF adapter
populates them as follows.

- `strategy_id`: PROPOSED format
  `"k6_mtf:<SECONDARY>:<HISTORY_AS_OF_DATE>"`, where
  `<SECONDARY>` is the secondary ticker (e.g. `AAPL`) and
  `<HISTORY_AS_OF_DATE>` is the per-secondary
  `history_as_of_date` from the K=6 MTF history artifact
  (ISO `YYYY-MM-DD`). This makes the identifier stable
  across runs of the same secondary on the same as-of date
  and distinguishable across as-of dates.
- `strategy_label`: PROPOSED format
  `"K=6 MTF <SECONDARY> as_of=<HISTORY_AS_OF_DATE>"`. The
  human-readable label that the ledger surfaces.
- `app_payload`: PROPOSED to carry the K=6 MTF identifying
  inputs verbatim so the contract is auditable end-to-end:
  - `secondary` (string).
  - `k6_stack.members` (array of `{ticker, protocol}`
    objects with `protocol` in `{"D", "I"}`).
  - `k6_stack.selected_build_path` (string, project-relative).
  - `k6_stack.selected_run_dir` (string, project-relative).
  - `k6_stack.combo_k6_path` (string, project-relative).
  - `history_artifact_path` (string, project-relative).
    **Provenance / audit metadata only.** The string records
    which live history artifact this candidate corresponds
    to for traceability; the adapter MUST NOT open or read
    that file as per-fold evaluation input. See Section 5
    for the load-bearing no-lookahead rule.
  - `history_as_of_date` (string).
  - `current_snapshot` (object with `1d` / `1wk` / `1mo` /
    `3mo` / `1y` keys). **Provenance / audit metadata
    only.** This is the live `current_snapshot` captured at
    the artifact's `history_as_of_date` and is recorded for
    traceability; it is NOT a per-fold evaluation input.
    The per-fold synthetic `current_snapshot` is generated
    from `ctx.train_end` inside `evaluate_candidate(ctx)`
    per Section 5.

### Divergence from StackBuilder, justified

StackBuilder candidates represent stack-build alternatives
explored within a single secondary's universe search; the
StackBuilder adapter typically emits many candidates per
run. The K=6 MTF candidate emits **one candidate per
secondary** because the K=6 MTF ranking row IS the public
claim per secondary; there are no alternative ranking-row
hypotheses being explored within a single secondary at
runtime. With 8 Tier 1 secondaries the K=6 MTF candidate
family per run is 8 candidates. This is a smaller-N family
than StackBuilder typically produces. See Section 8 for the
multiple-comparisons consequences.

---

## 5. Walk-Forward Semantics -- No Lookahead

This is the highest-risk section. The adapter MUST NOT use
the live, full-history K=6 MTF ranking artifact as its own
evidence.

### The fit / refit boundary

In the engine's walk-forward model, each `FoldContext`
carries `train_start`, `train_end`, `test_start`,
`test_end`, `selection_cutoff`, and `evaluation_cutoff`.
The adapter MUST:

- In `select_for_fold(ctx)`, return the per-secondary
  candidates whose identifying inputs (the K=6 stack
  members + `[D]/[I]` protocols + history_as_of_date
  semantics) are determinable from `[history_start,
  ctx.selection_cutoff]` only. The K=6 stack is fixed by the
  upstream StackBuilder selected build; the selection-time
  question this adapter answers is "for the
  ctx-selection-cutoff state of upstream evidence, which
  per-secondary K=6 MTF candidates are eligible?"
- In `evaluate_candidate(candidate, ctx)`, recompute the
  K=6 MTF combined-signal-and-match-rule construction
  **inside the fold**, using only historical bars in
  `(ctx.train_end, ctx.evaluation_cutoff]` after a
  forward-projected `current_snapshot` derived from
  `ctx.train_end`. The per-fold `current_snapshot` MUST be
  evaluated as of `ctx.train_end`, not as of the live
  artifact's `history_as_of_date`. The match-rule wildcard
  pass (per the K=6 MTF launch-path contract Match Rule
  section) applies between the per-fold current snapshot
  and historical bars within the fold.
- In `baseline_for_fold(ctx)`, supply same-secondary
  buy-and-hold metrics restricted to the fold OOS window
  (see Section 6).

### Hard prohibition

The adapter MUST NOT read
`output/k6_mtf/<RUN_TIMESTAMP>/k6_mtf_ranking.json` or any
sibling `k6_mtf_history.json` as the per-fold evidence
source. Those files carry the live, full-history evaluation
and are explicitly post-`history_as_of_date`. Using them as
evidence would re-evaluate the public claim against itself
and produce a self-fulfilling validation. The
`history_as_of_date` rule from the K=6 MTF launch-path
contract Honest Sharpe / Trade Direction sections (no
lookahead at evaluation time) propagates verbatim: per-fold
evaluation MUST stop at `ctx.evaluation_cutoff` and MUST NOT
look past it.

### Engine defaults

The methodology-default walk-forward parameters from
`validation_engine.py` (`DEFAULT_INITIAL_TRAIN_DAYS`,
`DEFAULT_TEST_WINDOW_DAYS`, `DEFAULT_STEP_DAYS`) apply
unless the launch-universe run spec amends them; the
adapter does NOT override them by default. `FoldContext`
objects come from `compute_walk_forward_folds(history_index,
initial_train_days=..., test_window_days=..., step_days=...)`
called inside `validate_strategy_set`. The adapter does NOT
build its own fold runner.

---

## 6. Baseline Coherence

The baseline contract for K=6 MTF mirrors the Phase 5C
methodology default and the StackBuilder pattern.

- **Baseline:** same-secondary buy-and-hold over the same
  fold OOS window. For each fold and each per-secondary
  candidate, baseline metrics come from holding the
  secondary's daily close from `ctx.test_start` to
  `ctx.test_end` and scoring through the engine's
  baseline-coherence path (the same
  `canonical_scoring.score_captures` path the methodology
  Baseline Contract section names).
- **Mapping onto `BaselineFoldMetrics`:** the engine's
  dataclass has `fold_index`, `n_observations`,
  `baseline_sharpe`, `baseline_total_return`,
  `baseline_mean_return`, `baseline_std`, and `issues`. The
  K=6 MTF adapter populates them per fold per secondary,
  with `n_observations` reflecting the count of valid daily
  bars in the OOS window.
- **No additional baselines in this spec.** The Phase 5C
  methodology default is the locked v1 floor. Any
  additional baseline (risk-adjusted index, equal-weight
  portfolio, sector ETF) is a **future methodology
  amendment**, not part of this spec.

### Divergence from StackBuilder

None. StackBuilder uses the same-secondary buy-and-hold
baseline for the secondary being traded; K=6 MTF uses the
same baseline for the per-secondary ranking row. Same
contract floor.

---

## 7. Outcome Metric Mapping

The K=6 MTF launch-path contract is explicit about the
metric basis: per-trade Sharpe / win / loss / win_pct /
low_sample_warning use `trade_count` (the directional-trade
subset); `total_capture_pct` and CCC use `capture_count`
(all matched bars including no-trade flat segments). This
spec preserves that split through to the
`StrategyFoldResult` and the contract surface.

### `StrategyFoldResult` field mapping

The engine's `StrategyFoldResult` carries `fold_index`,
`strategy_id`, `strategy_label`, `daily_capture: pd.Series`,
`trigger_mask: pd.Series`, `issues`, and `metadata`. The
adapter populates them:

- `daily_capture`: per-bar capture series over the fold OOS
  window, indexed by bar date. Per the K=6 MTF Capture
  section: `BUY` capture = `raw_return_pct`; `SHORT` capture
  = `-raw_return_pct`; no-trade bars carry `0.0`; bars with
  missing or invalid close are excluded entirely (consistent
  with `skipped_capture_count` semantics in the launch-path
  contract).
- `trigger_mask`: a boolean series, `True` for bars that
  count as **directional trades** under the K=6 MTF metric-
  basis split (per the launch-path contract Honest Sharpe
  section: matched bars whose own `1d` slot is `BUY` or
  `SHORT`). The engine consumes `trigger_mask` for per-trade
  metrics; setting it to the directional-trade subset
  preserves the K=6 MTF "trade_count denominator" rule
  through the engine.
- `metadata`: PROPOSED to record per-fold provenance for
  audit: the per-fold synthetic `current_snapshot` (the
  5-tuple evaluated at `ctx.train_end`), the
  `match_count` / `capture_count` / `trade_count` /
  `no_trade_count` / `skipped_capture_count` counters from
  the K=6 MTF contract count taxonomy, and the per-fold
  `low_sample_warning` flag if `trade_count < 30` (the
  launch-path contract's threshold).
- `issues`: surface any per-fold problems consistent with
  the engine's status taxonomy. Examples: `no_triggers`
  (the fold has zero directional matches), `stddev_zero`
  (sample std is zero so Sharpe is undefined),
  `history_underflow` (the fold OOS window has insufficient
  history).

### Contract surface

The engine builds the `validation_contract_v1` payload from
the per-fold results. The K=6 MTF adapter does NOT invent
new contract fields. The Honest Sharpe / per-trade metrics
flow through the `StrategyFoldResult.trigger_mask`-gated
engine path; `total_capture_pct` and CCC-equivalent
cumulative-capture summaries flow through
`StrategyFoldResult.daily_capture`. **No
`k6_mtf_ranking_v1` schema change in this PR; no contract
schema change in the engine.** Optional later browser-
visible per-secondary or top-level validation metadata on
the ranking artifact is OUT OF SCOPE for this spec and is
the same parked work named in the PR #369 Linkage Design
Option L1 / L2 evaluation.

---

## 8. Multiple-Comparisons Family

The K=6 MTF candidate family per run is 8 candidates (one
per Tier 1 secondary) when the launch-universe run spec
covers exactly the 8 Tier 1 secondaries. If a broader
launch-universe is later authorized, `N` rises to the size
of that tested family. The adapter MUST disclose the full
tested set, not only the reported survivors.

- **Primary control:** BH (Benjamini-Hochberg) per
  validation_methodology_v1.
- **Supplementary control:** Bonferroni, reported alongside
  BH.
- **Required disclosure:** every tested candidate's
  `parametric_p_value`, `bh_q_value`, `bonferroni_p_value`,
  and the run-level `n_strategies_tested` and
  `n_strategies_reported`. This mirrors the methodology
  Section 7 contract verbatim. Full survivorship disclosure
  is a hard requirement.
- **Run-spec parameter:** the exact tested family for a
  given launch-universe run is a parameter the later
  launch-universe run spec PR fixes. This adapter spec does
  NOT pre-commit to N = 8; it locks the contract surface so
  whatever family the operator authorizes for evidence
  production is disclosed faithfully.

### Divergence from StackBuilder

The candidate-family sizes differ in scale: StackBuilder
typically explores many stack-build alternatives per
secondary, producing a larger `N` per run; K=6 MTF produces
one candidate per secondary. Both adapters follow the same
BH primary / Bonferroni supplementary / full-survivorship
disclosure contract; only the cardinality of the family
differs.

---

## 9. Layering with StackBuilder

The two layers coexist in the later `honest_validation_ledger`
report:

- **Both layers discoverable by the same mechanism.**
  `discover_validation_sidecars(<root>)` rglobs for
  `validation.json`. Both StackBuilder sidecars and K=6 MTF
  sidecars land at the standard
  `output/validation/<run_id>/validation.json` path and are
  picked up by the same call.
- **Distinguished by `producer_engine` and `app_surface`.**
  `_build_app_summary` groups by `producer_engine`; the
  ledger naturally surfaces a `"stackbuilder"` group and a
  `"k6_mtf"` group. The operator review reads both groups
  in the same ledger output.
- **Joined at the REPORT level.** The PR #369 Option L4
  linkage joins the layers at the honest-validation report
  produced by `honest_validation_ledger` -- one Markdown
  report plus one JSON ledger that the PR #368 promotion
  manifest can verify-and-sign via
  `validation_results.phase_5_validation_report_path` and
  `validation_results.phase_5_validation_report_sha256`.
  The React app does NOT fetch a second runtime artifact.
- **Public launch requires both layers present.** A K=6 MTF
  layer without a StackBuilder layer would leave upstream
  selection unexamined. A StackBuilder layer without a K=6
  MTF layer would leave the public-facing ranking-row
  construction unvalidated. Per PR #369 Section 3 the
  layered public claim REQUIRES both.

---

## 10. Sidecar Emission Point

### Options evaluated

- **Option E1 -- emit inside `k6_mtf_ranking_engine.py`.**
  The ranking engine writes the sidecar as part of its run.
  Pros: every K=6 MTF run is durable-tier per Phase 5C
  Section 3 and writes its sidecar by construction. Cons:
  couples the ranking-engine module to `validation_engine`
  imports; the K=6 MTF runner becomes responsible for fold
  bookkeeping; harder to keep validation evidence
  reproducible if the ranking engine evolves.
- **Option E2 -- separate `k6_mtf` validation adapter
  module invoked by `controlled_compute`.** A new module
  (PROPOSED location
  `<PROJECT_DIR>/k6_mtf_validation_adapter.py` or
  `<PROJECT_DIR>/utils/k6_mtf_validation/adapter.py`)
  imports `validation_engine`, implements the
  `SelectionAdapter` Protocol, derives per-fold synthetic
  histories from cutoff-safe raw / member inputs (member
  signal libraries, secondary daily close source, and the
  upstream StackBuilder selected build's frozen member set
  and `[D]/[I]` protocols), and writes the sidecar.
  **The adapter MUST NOT read live K=6 MTF output
  artifacts (`output/k6_mtf/<RUN>/k6_mtf_ranking.json` or
  sibling `k6_mtf_history.json`) as per-fold evaluation
  input.** Those artifacts are not validation inputs; they
  are the live public claim being validated and would
  reopen the lookahead path Section 5 forbids.
  `controlled_compute` job-specs invoke this module as the
  producer command. Pros: keeps the ranking-engine code
  path clean; mirrors the separation-of-concerns the
  StackBuilder integration achieves (StackBuilder's
  adapter class is wired through the run but is
  structurally separable); easiest to test with `tmp_path`
  fixtures; easiest to evolve the adapter contract without
  disturbing the production ranking engine.
- **Option E3 -- `controlled_compute` wrapper only.** The
  orchestrator wraps a ranking-engine invocation and
  expects the sidecar to appear. Pros: minimal-touch. Cons:
  contradicts E1 only if the engine is changed; without an
  adapter module there is no place for the candidate /
  fold / baseline mapping logic. Not viable on its own.

### Recommendation

**PROPOSED Option E2 -- separate `k6_mtf` validation
adapter module invoked by `controlled_compute`.** This is
the most consistent with the StackBuilder template (where
the validation integration is encapsulated in a separable
adapter class) while preserving the K=6 MTF launch-path
contract's artifact-boundary discipline (the ranking engine
keeps writing `k6_mtf_ranking.json` only; the adapter
writes the sidecar). Side effects:

- The adapter module imports `validation_engine`.
- The adapter module may reuse K=6 MTF helper logic and
  input-reading primitives used by the K=6 MTF history
  producer / ranking engine (member signal libraries,
  secondary daily close source, K=6 stack member parsing,
  the `[D]/[I]` protocol application, the timeframe
  resample, the active-signal-unanimity combine, the
  forward-fill rule, the match-rule wildcard pass).
  Whether the adapter reuses helper functions from
  `k6_mtf_history_producer.py` / `k6_mtf_ranking_engine.py`
  directly or copies the minimum subset is a design
  decision for the implementation PR. **In all cases the
  adapter must derive per-fold synthetic histories /
  evaluation rows from cutoff-safe raw or member inputs
  (or `tmp_path` synthetic fixtures in tests), NOT from
  live K=6 MTF output artifacts under `output/k6_mtf/`.**
  Live ranking / history artifacts are not validation
  inputs; they are the public claim being validated.
- The adapter writes `output/validation/<run_id>/validation.json`
  via the engine's `write_validation_sidecar` helper, so
  discovery by `controlled_compute` and
  `honest_validation_ledger` is automatic.

### Sidecar destination

`output/validation/<run_id>/validation.json` (PROPOSED;
mirrors StackBuilder). Discoverable by the default
`controlled_compute` glob `"**/validation.json"` and by
`honest_validation_ledger.discover_validation_sidecars`.
**This PR does NOT implement the emission point.**

---

## 11. `controlled_compute` Integration

The later launch-universe campaign drives the K=6 MTF
producer through `controlled_compute`. This spec defines
what the job spec needs.

- **Producer command.** PROPOSED to invoke the K=6 MTF
  validation adapter (Option E2 above), with the launch-
  universe secondaries and a frozen StackBuilder evidence
  context (the StackBuilder selected builds whose layer of
  the report is being layered with the K=6 MTF layer).
- **Sidecar discovery.** The job spec sets
  `validation_sidecar_search_root` to
  `<PROJECT_DIR>/output/validation` and
  `validation_sidecar_glob` to the default
  `"**/validation.json"` (or relies on the orchestrator's
  default).
- **Manifest job-entry fields.** After a successful run
  `controlled_compute` records `validation_sidecar_path`,
  `validation_sidecar_sha256`, `validation_run_id`, and
  `validation_status` on the job entry, per its existing
  behavior. No new manifest field is required.

### Hard prerequisites

`controlled_compute` cannot produce the K=6 MTF layer until
ALL of:

1. The methodology contract amendment lands (Section 3) so
   `producer_engine="k6_mtf"` is valid at the contract
   level.
2. The K=6 MTF validation adapter module exists (Section 10
   Option E2).
3. The sidecar emission point is wired through the engine's
   `write_validation_sidecar` helper (Section 10).
4. The adapter's targeted tests pass under the pinned
   `spyproject2` interpreter (Section 13).

Until all four are met, any `controlled_compute` job spec
that targets K=6 MTF will fail to produce a valid sidecar
and the operator should expect refusal.

---

## 12. Fail-Closed Rules

The K=6 MTF adapter is durable-tier (per PR #369 Section 5
classification and Phase 5C Section 3) and inherits the
durable-tier MUST-emit rule. Specific fail-closed cases:

- **Missing K=6 stack inputs for a secondary -- visible,
  not silently dropped.** The run-spec launch family is
  fixed before validation begins, and the candidate family
  delivered to `validate_strategy_set` MUST match that
  launch family unless the run-spec explicitly defines a
  narrower eligible universe before execution. If a
  secondary's `selected_build.json` / `combo_k=6.json` /
  member PKL set / per-(member, timeframe) library is
  missing, the missing-input secondary MUST remain visible
  in the validation contract / report rather than being
  silently removed from `n_strategies_tested`. The required
  posture is to emit an unavailable / failed candidate
  outcome (per the engine status taxonomy, e.g. a per-fold
  `StrategyFoldResult` whose `daily_capture` and
  `trigger_mask` are empty with a recorded
  `StrategyFoldResult.issues` entry such as
  `"missing_member_pkl"` or `"missing_selected_build"`, or
  a run-level per-candidate disclosure with the same issue
  code). `n_strategies_tested` reflects the input family,
  not the silently shrunk output. Public-launch report
  completion is blocked until missing-input candidates are
  resolved or explicitly disclosed as non-survivors /
  unavailable under the methodology. Silently dropping a
  launch-family secondary from the tested family
  contradicts the full-survivorship-disclosure rule in
  Section 8 and the `validation_methodology_v1` posture.
- **Engine-level evaluation failure.** If
  `evaluate_candidate` raises, the adapter MUST surface the
  failure rather than swallow it. Mirroring StackBuilder's
  `_build_failed_validation_contract` pattern, the adapter
  emits a contract with `validation_status="FAILED"`
  (consistent with the engine's status taxonomy) plus a
  diagnostic reason.
- **Skipped StackBuilder upstream evidence.** If any of the
  Tier 1 secondaries' StackBuilder selected builds carry
  `durable_validation_status="skipped"` or are missing the
  StackBuilder validation sidecar, the K=6 MTF layered
  report **cannot be public-complete**. The K=6 MTF adapter
  itself may still emit its own sidecar (its layer can be
  produced independently); the operator review at the
  ledger report level surfaces the upstream-gap and
  prevents declaring public-readiness.
- **`--skip-durable-validation` MUST NOT be used for
  public-launch evidence.** The opt-in flag is preserved
  for operator-supervised non-production scenarios (matching
  StackBuilder's behavior) but if any layer's sidecars
  carry `durable_validation_status="skipped"` the layered
  public report is incomplete.
- **Missing candidate-family disclosure blocks public
  report completion.** The full `n_strategies_tested` and
  per-candidate p-values / q-values MUST be present in the
  K=6 MTF sidecar. Truncating to survivors only is a
  contract violation.

---

## 13. Required Tests for the Later Implementation PR

The implementation PR adds these tests. **This spec PR adds
none.** All tests use `tmp_path` only; none touch the real
`output/` tree or the live fixture.

- **Adapter unit tests for candidate construction.**
  Confirm `select_for_fold(ctx)` returns one candidate per
  eligible secondary; confirm `strategy_id` / `strategy_label`
  format; confirm `app_payload` carries the expected
  identifying inputs verbatim.
- **No-lookahead fold tests.** Inject a `FoldContext` and
  assert that `evaluate_candidate` returns
  `StrategyFoldResult` objects whose `daily_capture` and
  `trigger_mask` are sourced strictly from
  `(ctx.train_end, ctx.evaluation_cutoff]`. Assert the
  adapter does NOT read `output/k6_mtf/**` at runtime.
- **Sidecar schema-validation tests.** After a synthetic
  end-to-end fold-evaluation pass, write the contract via
  `write_validation_sidecar` and assert that
  `validate_validation_contract_v1` accepts it without
  raising.
- **Candidate-family / full-survivorship tests.** Run a
  small synthetic family of 3-4 candidates; assert
  `n_strategies_tested` equals the input family size; assert
  every candidate appears in the contract's per-strategy
  rows; assert BH q-values and Bonferroni p-values are
  populated for every candidate.
- **Baseline-coherence tests.** Inject known synthetic
  prices for a secondary; assert
  `BaselineFoldMetrics.baseline_sharpe` matches the
  hand-computed same-secondary buy-and-hold Sharpe to
  expected precision.
- **Fail-closed missing-input tests.** Remove a member PKL
  for one secondary in a synthetic launch family of N
  secondaries; assert the missing-input secondary remains
  visible in the contract / report (it is NOT silently
  dropped from `n_strategies_tested`); assert
  `n_strategies_tested` equals the input family size N;
  assert the missing-input candidate carries an
  unavailable / failed outcome with a recorded
  `StrategyFoldResult.issues` entry naming the missing
  input (e.g. `"missing_member_pkl"`); assert the run
  contract still validates with the engine's
  `validate_validation_contract_v1`.
- **Ledger-ingestion test.** Write the synthetic sidecar
  under a `tmp_path` root; call
  `discover_validation_sidecars(root)` and
  `load_validation_sidecar(path)`; assert the contract
  loads and the `_build_app_summary` output carries a
  `"k6_mtf"` group.
- **`controlled_compute` discovery test.** Write the
  synthetic sidecar under a `tmp_path`; configure a job
  spec with `validation_sidecar_search_root` pointing at
  that root and assert the orchestrator's
  pre-run-snapshot / post-run-discovery / hash-recording
  path records the sidecar's path and SHA-256 correctly.
- **No production-root tests.** All synthetic data lives in
  `tmp_path`. No test reads the live
  `output/k6_mtf/20260528T083411Z_post_fix/k6_mtf_ranking.json`
  fixture or the real `output/validation/` tree. Any future
  test that touches real operator state must be marked
  `production_smoke` per CLAUDE.md Section 5b.

---

## 14. Non-Goals

Out of scope for this specification PR:

- No compute.
- No validation sidecar production.
- No honest-validation ledger run.
- No source change.
- No `k6_mtf_ranking_v1` schema change.
- No test change.
- No React change.
- No deploy.
- No artifact promotion.
- No private deployment-target selection.
- No Tier 2 growth-queue scoping.
- No CI or deploy configuration change.
- No `output/` mutation.
- No `.claude/` change.
- No public-launch acknowledgment.

---

## 15. Next PR

The next public-aligned PR is the **K=6 MTF validation
adapter source implementation** plus the targeted
`tmp_path` tests enumerated in Section 13. That PR also
amends Phase 5C methodology Section 13 to add K=6 MTF as a
producer (per Section 3 of this spec). The methodology
amendment MAY land as a small precursor docs-only PR if the
operator prefers contract amendments to land alone.

The implementation PR does NOT include compute or evidence
production. The launch-universe controlled-compute campaign
is the PR after that (per PR #369 Section 7), and remains
operator-supervised and explicitly authorized at run time.

Phase 5G data licensing remains a separate parallel
public-launch gate. Not scoped here.

Public deployment of the React K=6 MTF MVP board remains
blocked until the operator deliberately clears both the
Phase 5 honest-validation gate and the Phase 5G data-
licensing gate.

---

## Amendment History

- 2026-05-31 (initial). Specifies the K=6 MTF validation
  producer / adapter contract following the StackBuilder
  template. Records `producer_engine="k6_mtf"` and
  `app_surface="run_directory"` as PROPOSED values pending
  a Phase 5C methodology Section 13 amendment in the later
  implementation PR. Defines the per-secondary candidate
  model, the no-lookahead walk-forward fit / refit
  boundary, the same-secondary buy-and-hold baseline, the
  outcome metric mapping preserving the K=6 MTF metric-
  basis split, the BH primary / Bonferroni supplementary /
  full-survivorship multiple-comparisons family, the
  StackBuilder-K=6 MTF layering at the ledger report level
  per PR #369 Option L4, the PROPOSED Option E2 sidecar
  emission point (separate adapter module invoked by
  `controlled_compute`), the `controlled_compute` job-spec
  expectations, the durable-tier fail-closed rules, and
  the required tests for the later implementation PR. No
  source change. No schema change. No React change. No
  deploy.
