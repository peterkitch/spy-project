# Phase 5C-1 Validation Methodology

**Date:** 2026-05-06

**Status:** LOCKED

**Author:** PRJCT9 sprint

**Phase:** 5C-1 (Validation methodology - first sub-phase of 5C; codifies `validation_contract_v1` and `validation_methodology_v1`)

## 1. Scope and source authority

This document defines `validation_contract_v1` (the validation artifact schema) and `validation_methodology_v1` (the canonical methodology that produces it) for PRJCT9. It is the locked authority for all 5C-2 implementation work: shared validation engine, validation-suite tests, manifest integration, and per-app convergence.

Source authorities:

- Locked 5C scoping at `project/md_library/shared/2026-05-06_PHASE_5C_PRE_FLIGHT.md` (PR #165).
- Locked Phase 5 Pre-Flight at `project/md_library/shared/2026-05-05_PHASE_5_PRE_FLIGHT.md`: outcome windows `{1, 5, 21, 63, 252}` trading days; same-ticker buy-and-hold MVP baseline; validation artifact location `project/output/validation/<run_id>/`.
- Algorithm Spec v0.5 at `project/md_library/shared/2026-04-30_PRJCT9_ALGORITHM_SPEC_v0_5.md`: Section 13 daily capture; Section 16 Sharpe ratio; Section 17 parametric p-value / t-statistic; Section 21 open decision on permutation checks.
- Phase 4A manifest contract: locked future-period-leakage rules; `series_id` + `series_metadata` schema isolation.
- 5B-MP precedents: cross-app parity-suite design (PR #164); behavioral-isolation pattern (PR #163); per-app reason-code prefix taxonomy (PRs #162-#164).

This document creates the canonical validation contract by extending existing Algorithm Spec statistical primitives. OOS evaluation, multiple-comparisons control, survivorship disclosure, empirical validation, and validation artifact persistence were not previously canonical.

## 2. `validation_contract_v1` definition

`validation_contract_v1` is the artifact schema produced by every meaningful run with validation enabled. The contract has seven parts:

1. Input threshold: what counts as a meaningful run.
2. OOS evaluation: how out-of-sample performance is measured.
3. Outcome window grid: which forward windows are measured.
4. Baseline: what strategy performance is compared against.
5. Multiple-comparisons control: how N-tested vs K-reported is accounted for.
6. Empirical validation: when permutation p-values and bootstrap confidence intervals are required.
7. Output artifact: persisted schema, status taxonomy, issue taxonomy, and manifest participation.

Methodology version is `validation_methodology_v1`. Contract version is `validation_contract_v1`. Either version changes only by dated amendment with rationale.

## 3. Input contract - meaningful-run threshold

The validation engine produces artifacts on a tiered basis.

- **Durable runs MUST emit validation artifacts.** A durable run is one that produces a persisted artifact, including XLSX export, library write, leaderboard save, persistent run directory, or manifest emission. Validation artifacts are part of the persistence transaction.
- **Interactive UI runs SHOULD emit in-memory validation summaries** when the operator surfaces results in a user-facing component. Summaries follow the same `validation_contract_v1` shape but may be in-memory only.
- **Exploratory UI tinkering** that does not meet the durable or interactive thresholds MUST be labeled non-canonical. The operator-facing surface MUST display "exploratory - not validated" or equivalent. The result MUST NOT be treated as canonical evidence.

Per-app determination of which threshold applies is in Section 13. Implementations MUST NOT silently degrade from durable to interactive tier without operator-visible indication.

## 4. OOS contract - walk-forward

Out-of-sample evaluation uses **walk-forward** as the canonical v1 design.

Walk-forward mechanics:

- The available history is partitioned into a sequence of train/test pairs.
- Default initial-train minimum: 5 years, or 1,260 trading days.
- Default test window: 1 year, or 252 trading days.
- Default step: 1 year.
- For each step `k`, the strategy is refit, or selection criteria are reapplied, using only data from `[history_start, train_end_k]`.
- The refit strategy is evaluated on `(train_end_k, train_end_k + 252]`.
- The aggregate OOS metric is computed across all walk-forward test folds.
- Strategies MUST NOT use information from `(train_end_k, ...]` to make selection decisions for the test window starting at `train_end_k`.

Walk-forward operationalizes the long-varied-window thesis: a strategy that survives consecutive OOS test years across regime variety is the strategy `validation_contract_v1` reports as validated.

Edge cases:

- **Insufficient history:** if `history_length < initial_train + test_window`, status is `validation_in_sample_only`; aggregate OOS metrics are null and the operator-facing surface MUST flag this.
- **Test fold with no triggers:** fold-level status is `no_triggers`; the fold is excluded from aggregate OOS metrics and counted in `issues`.
- **Refit instability:** if refit or selection produces no usable strategy for a fold, that fold is reported as `no_triggers` or `validation_partial_folds`, as appropriate, and excluded from aggregate metrics.

Methodology amendment may upgrade walk-forward to time-series CV or combinatorial cross-validated backtest in a future version. Walk-forward is the v1 floor.

## 5. Outcome window contract

Per Phase 5 Pre-Flight, outcome windows are `{1, 5, 21, 63, 252}` trading days: 1d, 1w, 1m, 1q, 1y.

For each walk-forward test fold, the strategy's signals at day `t` produce outcome metrics on each window: what the secondary returned over the next 1, 5, 21, 63, and 252 trading days following each signal day.

Outcome windows are forward-looking by definition because they measure returns after the signal day. They are valid only when used as outcome measurements. They MUST NOT be used as inputs to signal selection, strategy ranking, refit decisions, or any other pre-signal computation.

Any new forward-return outcome computation added by 5C-2 MUST be covered by the lookahead guard suite. If an allowlist entry is required, it must be narrowly scoped and documented using the maintenance-comment pattern established in PR #163.

## 6. Baseline contract

Per Phase 5 Pre-Flight, the v1 baseline is **same-ticker buy-and-hold** on the secondary over the same evaluation grid.

For each strategy reported, validation MUST report strategy metrics alongside baseline metrics on the same fold and window. Reporting strategy metrics in isolation is forbidden.

Baseline computation:

- Buy-and-hold returns the secondary's price-relative over each outcome window on every eligible grid bar.
- Baseline scoring uses the same canonical scoring path, `canonical_scoring.score_captures`, with an all-eligible trigger mask for the baseline grid.
- Baseline-vs-strategy delta is reported per fold and aggregated.

Methodology amendment may add additional baselines such as risk-adjusted index, equal-weight portfolio, or sector ETF. Same-ticker buy-and-hold is the v1 floor.

## 7. Multiple-comparisons contract

When N strategies are tested and K are reported, validation MUST disclose N and apply explicit multiple-comparisons control.

**Primary control: Benjamini-Hochberg (BH).** BH controls false discovery rate. It is applied to the full set of N parametric p-values. The BH-adjusted p-value, or q-value, is the canonical significance metric.

**Supplementary disclosure: Bonferroni.** Bonferroni-adjusted p-values are computed alongside BH and reported in a secondary column. The conservative reading is available without additional compute cost.

Rules:

- Raw parametric p-values come from `canonical_scoring.score_captures`, per Algorithm Spec Section 17.
- Null or unavailable p-values are treated as non-significant for adjustment and reporting.
- The full N tested must be preserved even when only K strategies are displayed.

Required fields in every reported strategy:

- `parametric_p_value`: raw t-test p-value.
- `bh_q_value`: Benjamini-Hochberg adjusted q-value across the N tested in this run.
- `bonferroni_p_value`: Bonferroni-adjusted p-value, computed as `min(raw_p * N, 1.0)`.
- `n_strategies_tested`: the N for this run.
- `n_strategies_reported`: the K reported as significant under primary BH control at the configured alpha.

Default alpha: 0.05. Alpha is configurable per run and appears in the validation artifact.

## 8. Empirical validation contract - HYBRID

Real PRJCT9 return data is severely fat-tailed. The 2026-05-06 Codex investigation found excess kurtosis roughly 7-19 across representative series including SPY, QQQ, AAPL, TQQQ, and BTC-JPY. Parametric p-values are directionally useful but disagreed with empirical permutation p-values on a measurable fraction of borderline strategies: 1 in 10 at alpha 0.05 in the investigation sample.

The v1 empirical validation layer is hybrid:

- **Parametric layer, full universe:** every tested strategy gets `parametric_p_value`, `bh_q_value`, and `bonferroni_p_value`.
- **Empirical layer, BH survivors plus borderline:** strategies that pass BH at the configured alpha, plus borderline candidates within the configured tolerance of the BH threshold, get `empirical_p_value` and bootstrap Sharpe confidence interval fields.
- **Transparency:** strategies outside the empirical subset get `empirical_validation_status: "empirical_not_run"`. They are never silently mixed with empirically validated strategies in operator-facing reporting.

Default empirical method:

- `empirical_p_value`: 10,000 permutation reassignments preserving Buy/Short trigger counts where directional labels exist.
- `bootstrap_sharpe_ci_lower` and `bootstrap_sharpe_ci_upper`: 10,000 resamples with replacement using the canonical Sharpe definition.
- Default permutation count: 10,000.
- Default bootstrap count: 10,000.
- Both counts are configurable per run and appear in the artifact.

Default borderline tolerance: q-values within `2.0 * alpha` are included in the empirical layer. For alpha 0.05, the default borderline cutoff is `q <= 0.10`.

Full empirical validation for every tested strategy is staged for v2. A v2 amendment review becomes mandatory if a future audit on representative PRJCT9 data finds either:

- Parametric-vs-empirical disagreement at alpha 0.05 above 10% among reported BH-surviving strategies.
- Parametric-vs-empirical disagreement at alpha 0.01 above 2% among reported BH-surviving strategies.

Behavioral-isolation rule: the canonical scoring path, `canonical_scoring.score_captures`, MUST remain byte-identical for the same inputs. The empirical layer wraps the canonical path; it does not modify it. Phase 1A baselines and canonical-scoring tests must continue to pass without modification.

## 9. Output artifact schema

`validation_contract_v1` artifact, per run.

Required top-level fields:

- `validation_contract_version`: string `"v1"`
- `validation_methodology_version`: string `"v1"`
- `validation_status`: one of `valid`, `in_sample_only`, `oos_skipped`, `partial`, `unavailable`, `failed`
- `run_id`: producer-supplied unique identifier
- `producer_engine`: one of `spymaster`, `impactsearch`, `confluence`, `stackbuilder`
- `app_surface`: human-readable surface name
- `evaluation_time`: ISO 8601 UTC timestamp
- `data_available_through`: ISO 8601 date of latest data used
- `in_sample_window_start`, `in_sample_window_end`: ISO 8601 dates
- `oos_window_start`, `oos_window_end`: ISO 8601 dates, or null if `in_sample_only`
- `walk_forward_n_folds`: integer count of test folds, or null if `in_sample_only`
- `outcome_windows`: list of integer day counts; v1 default `[1, 5, 21, 63, 252]`
- `baseline_method`: string; v1 default `"same_ticker_buy_and_hold"`
- `n_strategies_tested`: integer
- `n_strategies_reported`: integer, post-BH at alpha
- `n_strategies_survived_empirical`: integer, post-empirical p-value at alpha for the empirical-layer subset
- `multiple_comparisons_control_method`: string `"benjamini_hochberg"`
- `multiple_comparisons_control_alpha`: float; default 0.05
- `multiple_comparisons_supplementary`: string `"bonferroni"`
- `n_permutations`: integer; default 10000
- `n_bootstrap_samples`: integer; default 10000
- `borderline_tolerance_multiplier`: float; default 2.0
- `survivorship_summary`: object with counts
- `issues`: list of formatted reason-code strings

Per-strategy detail is optional but recommended for operator UI surfaces:

- `strategy_id`, `strategy_label`
- `parametric_p_value`, `bh_q_value`, `bonferroni_p_value`
- `empirical_p_value`, null if `empirical_not_run`
- `bootstrap_sharpe_ci_lower`, `bootstrap_sharpe_ci_upper`, null if `empirical_not_run`
- `empirical_validation_status`: one of `validated`, `empirical_not_run`, `empirical_failed`
- `per_fold_metrics`: list when walk-forward applied
- `per_window_metrics`: object keyed by outcome window

## 10. Status and failure semantics

`validation_status` precedence, highest to lowest:

1. `failed`: validation engine raised; canonical scoring path or empirical layer threw an exception.
2. `unavailable`: input data missing; no validation could run.
3. `oos_skipped`: OOS was configured or required but did not run at runtime.
4. `in_sample_only`: OOS was not configured for this run, or history was known before execution to be too short for walk-forward. Aggregate metrics are in-sample and operator-facing surfaces MUST flag this.
5. `partial`: walk-forward ran but at least one fold failed or was excluded; aggregate metrics computed across surviving folds.
6. `valid`: walk-forward ran across all configured folds and all components produced expected outputs.

`partial`, `failed`, `unavailable`, `oos_skipped`, and `in_sample_only` MUST surface specific reason codes in `issues`.

## 11. Survivorship reporting contract

Validation reports MUST disclose both surviving and non-surviving strategies. Survivorship-only reporting is forbidden.

Required disclosures in `survivorship_summary`:

- Total tested
- Total reported as significant, post-BH
- Total empirically validated, within the BH-survivor plus borderline subset
- Count with empirical validation not run
- Counts by non-survival reason:
  - `did_not_survive_bh`
  - `did_not_survive_empirical`
  - `did_not_survive_no_triggers`
  - `did_not_survive_insufficient_history`

Operator-facing UI surfaces SHOULD render N tested as visibly as K reported. Hiding N only in hover text or drill-down is discouraged.

## 12. Manifest participation

Per Phase 5 Pre-Flight, validation artifacts live at:

`project/output/validation/<run_id>/`

Persistence model:

- **JSON sidecar:** the full `validation_contract_v1` artifact persists as `validation.json` in the run directory.
- **Manifest first-class fields:** a subset of summary fields is embedded in the run's Phase 4A manifest:
  - `validation_contract_version`
  - `validation_status`
  - `n_strategies_tested`
  - `n_strategies_reported`
  - `multiple_comparisons_control_method`
  - `multiple_comparisons_control_alpha`
  - `walk_forward_n_folds`
  - `validation_artifact_path`
- **Hash linkage:** the manifest's `validation_artifact_hash` is the SHA-256 of `validation.json`.

The full per-strategy detail stays in the JSON sidecar. The manifest carries summary fields and the hash link.

Phase 4A schema isolation: manifest fields use generic naming compatible with future BYO-data scenarios. The validation artifact MUST NOT embed yfinance-specific metadata in the canonical schema. Provider-specific metadata lives in a separate `provider_metadata` sub-object.

## 13. Per-app current-state mapping

### 13.1 Spymaster

Current state: parametric metrics delegate to `canonical_scoring.score_captures` across trading, optimization, and multi-primary surfaces. Optimization ranks combinations by Sharpe.

Validation surface: absent. No OOS, no multiple-comparisons control, no empirical layer, no durable validation artifact. Optimization surfaces ranked survivors but does not yet disclose full N-tested vs K-reported survivorship.

Tier:

- Durable for optimization-result persistence.
- Interactive for Multi-Primary Aggregator UI.
- Exploratory for ad-hoc parameter sweeps.

Migration depth: add canonical validation engine integration and manifest hooks; preserve UI; surface survivorship summary in optimization results.

### 13.2 ImpactSearch

Current state: `calculate_metrics_from_signals(...)` delegates to canonical scoring. XLSX exports and sidecar manifests already exist. The 5B-MP-2c aggregate-mode helper remains UI/in-memory only.

Validation surface: partial. Batch rows already expose per-primary metrics. No OOS, no multiple-comparisons control, no empirical layer.

Tier:

- Durable for batch XLSX exports.
- Interactive for aggregate-mode display.
- Exploratory for unsaved batch runs.

Migration depth: add canonical validation engine; extend XLSX exports with validation columns; extend sidecar manifests with `validation_contract_v1` artifact; preserve batch and aggregate UI.

### 13.3 Confluence

Current state: `_mp_metrics(...)` delegates to canonical scoring. 5B-MP-2b added multi-primary partial-coverage diagnostics. Forward-return display helpers are covered by the lookahead guard suite.

Validation surface: absent for strategy search. The multi-interval virtual library is interval reporting, not a full strategy-search validation surface.

Tier:

- Durable for persisted multi-primary analysis runs.
- Interactive for multi-interval table display.
- Exploratory for unsaved interval reviews.

Migration depth: add canonical validation engine integration; preserve the multi-primary UI; extend the existing diagnostic surface with validation status where appropriate.

### 13.4 StackBuilder

Current state: `metrics_from_captures(...)` delegates to canonical scoring. StackBuilder already records search-count-like data such as primaries tested and combinations tested in run outputs/manifests.

Validation surface: partial and closest to canonical. It already has durable run directories and survivorship-adjacent counts. No OOS, no BH/Bonferroni control, no empirical layer.

Tier:

- Durable for all leaderboard and run-directory outputs.
- No interactive tier.
- No exploratory tier.

Migration depth: add OOS, BH/Bonferroni, and empirical layer through the shared validation engine; extend run manifest to include `validation_contract_v1` summary; preserve leaderboard ranking.

## 14. Divergence classification and migration order

Migration order for 5C-2:

1. **Shared validation engine first.** A new shared module, such as `project/validation_engine.py`, implements `validation_contract_v1`: walk-forward orchestration, BH/Bonferroni control, hybrid empirical layer, JSON sidecar emission, and manifest hook. No app integration in this PR. Validation-suite test infrastructure lands here.
2. **ImpactSearch.** Existing XLSX plus sidecar pattern is the cleanest first app integration.
3. **StackBuilder.** Existing durable run directories and search counts make it the closest survivorship fit.
4. **Spymaster.** Most operator-visible interactive surface; survivorship summary needs careful UI integration.
5. **Confluence.** Participates for persisted multi-primary analysis runs; less central to high-N strategy search.

Codex preflight is required for each 5C-2 implementation split.

## 15. Reason-code taxonomy

Validation-specific reason codes use the per-app prefix established in 5B-MP scoping:

- `[SPYMASTER:...]`
- `[IMPACTSEARCH:...]`
- `[CONFLUENCE:...]`
- `[STACKBUILDER:...]`

Validation reason codes:

- `validation_in_sample_only`: OOS not run; aggregate is in-sample.
- `validation_oos_skipped`: OOS configured but skipped.
- `validation_partial_folds`: walk-forward ran; one or more folds failed or were excluded.
- `validation_unavailable`: input data missing; no validation could run.
- `validation_failed`: engine raised during validation.
- `validation_empirical_not_run`: strategy outside BH-survivor plus borderline subset; parametric only.
- `validation_empirical_failed`: empirical layer raised for this strategy; parametric value preserved.
- `validation_baseline_unavailable`: baseline metrics could not be computed; strategy metrics preserved without baseline delta.
- `validation_outcome_window_truncated`: outcome window extends past `data_available_through`; partial window metrics returned.

Operator diagnostic example:

`[STACKBUILDER:validation_partial_folds] run-2026-05-06-1432: 2 of 9 walk-forward folds excluded due to no_triggers. Action: review fold-level metrics in validation.json.`

## 16. 5C-2 validation-suite requirements

Validation-suite test infrastructure is mandatory for 5C-2.

Required:

- Cross-app validation contract conformance tests: drive the shared validation engine from each app's call site with the same canonical input; assert `validation_contract_v1` artifacts are semantically identical, modulo cosmetic per-app metadata.
- Walk-forward correctness tests: verify no information leakage from fold `k+1` into fold `k` selection.
- BH/Bonferroni control tests: N-strategy fixtures with known expected q-values; verify outputs match a reference implementation.
- Hybrid empirical layer tests: verify `empirical_not_run` application; verify empirical p-value and bootstrap CI determinism with fixed RNG seed.
- Behavioral-isolation test: `canonical_scoring.score_captures` output remains byte-identical for same inputs before and after validation engine integration.
- Lookahead-bias audit: verify new forward-return outcome computations are covered by bounded lookahead guard tests.

## 17. Risks and amendment triggers

Methodology choices in this document can change only by dated amendment with rationale. Amendments bump `validation_methodology_version`, and bump `validation_contract_version` if schema changes.

Identified amendment triggers:

- **Empirical staging trigger:** if a future audit finds parametric-vs-empirical disagreement above 10% at alpha 0.05, or above 2% at alpha 0.01, among reported BH-surviving strategies, a v2 empirical-validation amendment review becomes mandatory.
- **OOS upgrade trigger:** if walk-forward proves insufficient, such as consistent overfit-by-fold-selection, upgrade review to time-series CV or combinatorial cross-validated backtest becomes mandatory.
- **Outcome window expansion:** additional windows, such as 504-day / 2-year, require amendment.
- **Baseline expansion:** additional baselines require amendment.
- **Multiple-comparisons method change:** alternatives such as Holm, Hommel, or Romano-Wolf require amendment with rationale.

Risks tracked:

- **Hidden lookahead in current code:** validation engine activation may surface previously hidden lookahead bugs. A pre-activation lookahead audit is recommended before 5C-2 implementation begins.
- **Behavioral drift:** validation MUST NOT change existing computation outputs for the same inputs. Behavioral-isolation tests are required.
- **Manifest schema churn:** validation fields are referenced by sidecar path and hash where possible to avoid embedding evolving schemas deeply in existing manifests.
- **Compute cost on full empirical:** 100,000 strategies x 10,000 permutations is expensive; v1 scopes empirical validation to reported and borderline strategies.
- **Operator confusion on tiers:** durable, interactive, and exploratory labeling MUST be visible.

## 18. Open questions deferred to 5C-2 sub-preflights

- Per-app integration boundaries: which call sites receive validation-engine wiring; which UI surfaces render survivorship summary.
- Walk-forward parameter defaults per app.
- Borderline tolerance multiplier per app.
- JSON sidecar atomic-write protocol.
- `provider_metadata` schema for provider-specific fields.
- Pre-activation lookahead audit scope and execution.
- Whether the shared validation engine PR is 5C-2a and app integrations begin at 5C-2b, or whether numbering is adjusted during 5C-2 preflight.

## 19. Decisions captured

From the 2026-05-06 Codex investigations and Peter sign-off:

- **OOS = walk-forward** over the longest available history; rolling train / rolling 1-year test; default 5-year initial train.
- **Multiple-comparisons = BH primary + Bonferroni supplementary disclosure.** BH controls FDR for high-N pattern discovery; Bonferroni provides a conservative reading at no extra compute cost.
- **Run threshold = app-specific hybrid.** Durable runs MUST emit; interactive runs SHOULD emit; exploratory tinkering is labeled non-canonical.
- **Empirical validation = HYBRID.** Parametric for full universe; empirical permutation plus bootstrap CI mandatory for BH survivors and borderline strategies; full empirical-for-all staged to v2 with disagreement-rate triggers.

From the locked Phase 5 Pre-Flight (PR #149):

- Outcome windows `{1, 5, 21, 63, 252}` trading days.
- Same-ticker buy-and-hold MVP baseline.
- Validation artifact location `project/output/validation/<run_id>/`.

From the locked 5C scoping (PR #165):

- 5C-1 codifies `validation_contract_v1`; 5C-2 implements the engine.
- Validation is built in, not bolted on.
- Phase 4A manifest contract participation is required.
- Behavioral-isolation rule inherited from 5B-MP-2b.
- Per-app prefix taxonomy inherited from 5B-MP scoping.

## 20. Document status

**LOCKED 2026-05-06.** This document is the locked authority for all 5C-2 implementation work. Any change requires explicit dated amendment with rationale and version bump. Empirical staging triggers in Sections 8 and 17 require amendment review when triggered.

End of document.
