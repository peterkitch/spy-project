# K=6 MTF Phase 5 Honest-Validation Report

**Date:** 2026-06-01

**Status:** OPERATOR-REVIEW REPORT PACKAGE (docs-only; derived from the empirical-only ledger; no compute, no promotion, no deploy)

**Author:** PRJCT9 sprint

**Scope:** Phase 5 honest-validation evidence package for the K=6 MTF MVP launch over the 8 Tier 1 secondaries (AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA).

---

## 1. Status and scope

This document is the operator-reviewable Phase 5 honest-validation report package for the K=6 MTF launch-current evidence. It is derived from the launch-current honest validation ledger generated from ONLY the valid empirical sidecar produced by the operator-authorized rerun of 2026-06-01.

What this report does:

- Packages the empirical-rerun ledger and per-strategy evidence into a single reviewable artifact.
- Records the chain of custody from upstream inputs through the merged K=6 MTF validation adapter, the controlled_compute job, the validation engine, and the honest validation ledger to this report.
- Records the project-relative paths and SHA-256 values of the evidence inputs so future promotion and audit steps can verify the inputs were the ones reviewed here.

What this report does NOT do:

- Does NOT run compute. Does NOT produce new evidence.
- Does NOT promote artifacts. Does NOT change `frontend/public/fixtures/k6_mtf_ranking.json` or any other React runtime input.
- Does NOT deploy. Does NOT enable public deployment.
- Does NOT resolve Phase 5G data licensing. That remains a separate public-launch gate per `md_library/shared/2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md`.
- Public promotion remains a separate explicit operator action through the PR #368 promotion helper at `utils/react_publish/promote_k6_mtf_artifact.py`. Until the operator runs that helper with `--public --phase5-report <path> --phase5-sha256 <SHA> --write --operator-approved`, the React MVP board remains on the private / internal posture per `md_library/shared/2026-05-31_REACT_PUBLISH_DEPLOY_CONTRACT.md`.

This report does NOT carry its own SHA-256 inside its body. The operator computes the file SHA after the report is finalized at merge time and passes that SHA to the promotion helper as `--phase5-sha256` when (and only when) public promotion is later authorized.

## 2. Evidence sources

All paths are project-relative.

**Empirical sidecar (launch-current evidence; `validation_status="valid"`):**

- Path: `output/validation/20260601_k6_mtf_phase5_launch_universe_empirical/validation.json`
- SHA-256: `d6bc196390ed3af7c6be27ec78525784328d22193d5f54e5acc7f396e5e77a3c`
- Produced by: one operator-authorized controlled_compute job invoking the merged K=6 MTF validation adapter at `utils/k6_mtf_validation/adapter.py` (PR #371 + PR #373 empirical-metadata fix).

**Empirical controlled_compute manifest:**

- Path: `output/controlled_compute/20260601_k6_mtf_phase5_controlled_compute_empirical/compute_manifest.json`
- Records: `validation_sidecar_path`, `validation_sidecar_sha256` (matches the empirical sidecar SHA-256 above), `validation_run_id`, `validation_status="valid"`, plus the planned command and job metadata.

**Launch-current honest validation ledger (derived ONLY from the empirical sidecar via the narrow-root mechanism in `honest_validation_ledger.py`):**

- JSON: `output/validation_ledger/honest_validation_ledger.json`
- Markdown: `output/validation_ledger/honest_validation_ledger.md`
- Ledger version: `validation_ledger_v1`.

**Old partial sidecar (SUPERSEDED; AUDIT TRAIL ONLY; NOT used as launch-current evidence):**

- Path: `output/validation/20260601_k6_mtf_phase5_launch_universe/validation.json`
- SHA-256: `c5363bdcf06255a1d9ec64e3ae5e179faaf1e2f7e85bfa729d615f695d033d38`
- Reason for supersession: the empirical layer for the first campaign run could not execute because the K=6 MTF adapter did not yet populate `StrategyFoldResult.metadata['signal_state']` and `StrategyFoldResult.metadata['permutation_return_pool']`. PR #373 closed that adapter-metadata gap; this report is sourced from the rerun that benefitted from the fix.

**Old partial controlled_compute manifest (preserved):**

- Path: `output/controlled_compute/20260601_k6_mtf_phase5_controlled_compute/compute_manifest.json`

The old partial run is excluded from the launch-current ledger via the narrow-root invocation: the ledger was generated with `--validation-root output/validation/20260601_k6_mtf_phase5_launch_universe_empirical` so `Path.rglob("validation.json")` cannot reach the sibling partial run directory. The partial sidecar bytes are unmodified, its SHA is unchanged, and the operator can re-discover it at any time by rerunning the ledger over a broader root if a separate audit of the supersession event is desired.

## 3. Methodology summary

Source: locked methodology at `md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md` (Sections 4, 6, 7, 8, 9, 10, 13.5, 15).

- **Walk-forward OOS evaluation** (Section 4): default initial-train = 1,260 trading days (~5 years), default test window = 252 trading days (~1 year), default step = 252. For each fold the strategy is refit / re-selected using only data through `train_end_k`; OOS is evaluated on the subsequent test window. Strategies MUST NOT use information past `train_end_k` for selection on the fold whose test window begins at `train_end_k`.
- **Same-secondary buy-and-hold baseline** (Section 6): v1 baseline floor. For each fold the secondary's buy-and-hold over the same evaluation grid is the comparison anchor. K=6 MTF persistence: the locked Section 6 contract is honored per-strategy and persisted on disk at `strategies[].per_fold_metrics[].same_secondary_baseline` with the stable six-key schema (`n_observations`, `baseline_sharpe`, `baseline_total_return`, `baseline_mean_return`, `baseline_std`, `issues`); the adapter caches the per-`(strategy_id, fold_index)` baseline during `evaluate_candidate` and `run_validation` enriches the contract dict via `_enrich_contract_with_same_secondary_baseline` before `write_validation_sidecar` serializes it. Adapter `baseline_for_fold` is deliberately neutral (`n_observations=0`, all metric fields `None`, `[K6MTF:validation_baseline_unavailable]` issue) because `validation_engine` v1 carries exactly one `BaselineFoldMetrics` per fold and applies that single baseline to every per-strategy `per_fold_baseline_delta` for that fold; a family-blended fold-level baseline would deliver misleading deltas to every per-secondary strategy. Engine-level `per_fold_baseline_delta` entries therefore surface as `sharpe_delta=null` / `return_delta=null` for K=6 MTF strategies; the actual same-secondary evidence lives at the persisted per-fold metric entry.
- **BH primary multiple-comparisons control** (Section 7): Benjamini-Hochberg adjusted q-values across the full set of N parametric p-values produced by `canonical_scoring.score_captures`. `n_strategies_reported` is the count of strategies with `bh_q_value <= alpha`.
- **Bonferroni supplementary disclosure** (Section 7): conservative reading available at no extra compute cost; reported alongside BH per locked policy.
- **Empirical permutation / bootstrap layer** (Section 8 hybrid policy): direction-preserving permutation p-value and bootstrap Sharpe confidence interval are computed for BH-survivors plus borderline candidates (`bh_q_value <= borderline_tolerance_multiplier * alpha`, i.e. `<= 0.10` at the default alpha). Strategies outside the empirical subset are honestly recorded with `empirical_validation_status="empirical_not_run"` and never silently mixed with empirically validated strategies.
- **No-lookahead rule** (Section 4 + Section 13.5): per-fold `current_snapshot` is synthesized at `ctx.train_end`; OOS candidate-bar snapshots and forward-fill are bounded by `ctx.evaluation_cutoff`; per-target forward-fill uses only source dates `<= target date`; per-bar capture uses close pairs within `ctx.evaluation_cutoff`. The K=6 MTF adapter MUST NOT read `output/k6_mtf/<run>/k6_mtf_history.json` or `output/k6_mtf/<run>/k6_mtf_ranking.json` as validation evidence; static and dynamic test guards in `test_scripts/shared/test_k6_mtf_validation_adapter.py` pin this property.

## 4. Campaign parameters

All values extracted from the empirical sidecar / ledger (not from memory).

| Parameter | Value |
|---|---|
| Adapter validation `run_id` | `20260601_k6_mtf_phase5_launch_universe_empirical` |
| `producer_engine` | `k6_mtf` |
| `app_surface` | `run_directory` |
| `validation_contract_version` | `v1` |
| `validation_methodology_version` | `v1` |
| `validation_status` | `valid` |
| `data_available_through` | 2026-05-26 |
| In-sample window | 1980-12-12 -- 1985-12-05 |
| OOS window | 1985-12-06 -- 2025-12-09 |
| `walk_forward_n_folds` | 40 |
| `multiple_comparisons_control_method` | `benjamini_hochberg` |
| `multiple_comparisons_supplementary` | `bonferroni` |
| `multiple_comparisons_control_alpha` | 0.05 |
| `n_permutations` | 10000 |
| `n_bootstrap_samples` | 10000 |
| `borderline_tolerance_multiplier` | 2.0 (borderline cutoff = `0.10`) |
| `outcome_windows` (trading days) | `[1, 5, 21, 63, 252]` |
| `baseline_method` | `same_ticker_buy_and_hold` |
| `n_strategies_tested` | 8 |
| `n_strategies_reported` | 4 |
| `n_strategies_survived_empirical` | 4 |

Random-number-generator seed used for the rerun: `20260601`. The operator chose this seed and recorded it in the campaign job-spec metadata; re-running the rerun under the same seed reproduces the verdict deterministically subject to the engine's documented use of `np.random.default_rng`.

## 5. Launch universe

The launch family is exactly the 8 Tier 1 secondaries:

- AAPL
- AMZN
- GOOGL
- META
- MSFT
- NVDA
- SPY
- TSLA

This is the same operator-curated launch family bound by the locked K=6 MTF launch-path contract at `md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md` and by the campaign spec at `md_library/shared/2026-06-01_K6_MTF_VALIDATION_CAMPAIGN_SPEC.md`. `n_strategies_tested == 8` confirms the rerun did not silently shrink the family; missing-input disclosures did not apply because all upstream inputs were available at run time.

## 6. Results summary

**Ledger-level (`output/validation_ledger/honest_validation_ledger.json`):**

- `ledger_version`: `validation_ledger_v1`
- `sidecar_count`: 1
- `accepted_count`: 1
- `rejected_count`: 0
- `runs[0].run_id`: `20260601_k6_mtf_phase5_launch_universe_empirical`
- `runs[0].validation_status`: `valid`
- `runs[0].producer_engine`: `k6_mtf`
- `runs[0].sidecar_sha256`: `d6bc196390ed3af7c6be27ec78525784328d22193d5f54e5acc7f396e5e77a3c`
- `app_summary.k6_mtf.status_counts`: `{ "valid": 1 }`
- `app_summary.k6_mtf.total_n_strategies_tested`: 8
- `app_summary.k6_mtf.total_n_strategies_reported`: 4
- `app_summary.k6_mtf.total_n_strategies_survived_empirical`: 4
- Old partial run-id NOT present in `runs`.

**Survivorship (from the empirical sidecar `survivorship_summary`):**

- `total_tested`: 8
- `total_reported_bh`: 4
- `total_empirical_validated`: 7
- `total_empirical_not_run`: 1
- `did_not_survive_bh`: 4
- `did_not_survive_empirical`: 0
- `did_not_survive_no_triggers`: 0
- `did_not_survive_insufficient_history`: 0

**Per-strategy table** (aggregate Sharpe and BH q-value from `canonical_scoring.score_captures` aggregated across all walk-forward folds; empirical p-value from the engine's direction-preserving permutation layer; Bonferroni shown as the supplementary conservative reading):

| `strategy_id` | Aggregate Sharpe | BH q-value | Bonferroni p-value | Empirical p-value | `empirical_validation_status` | BH survivor (q <= 0.05) | Empirical survivor (q <= 0.05 AND emp_p <= 0.05) |
|---|---|---|---|---|---|---|---|
| `k6_mtf:AAPL` | 0.6846 | 0.05915 | 0.2957 | 0.1949 | validated | NO (borderline) | NO |
| `k6_mtf:AMZN` | 1.4983 | 0.001036 | 0.003108 | 0.009799 | validated | YES | YES |
| `k6_mtf:GOOGL` | 2.2382 | 0.001325 | 0.005302 | 0.001400 | validated | YES | YES |
| `k6_mtf:META`  | 1.4704 | 0.1185 | 0.9477 | (not run) | empirical_not_run | NO (outside borderline) | NO |
| `k6_mtf:MSFT`  | 0.7604 | 0.07257 | 0.4430 | 0.02810 | validated | NO (borderline) | NO |
| `k6_mtf:NVDA`  | 2.6056 | 8.299e-06 | 8.299e-06 | 1.000e-04 | validated | YES | YES |
| `k6_mtf:SPY`   | 0.8113 | 0.07257 | 0.5080 | 0.01990 | validated | NO (borderline) | NO |
| `k6_mtf:TSLA`  | 2.9169 | 0.0002335 | 0.0004669 | 0.0007 | validated | YES | YES |

The four empirical-survivors are AMZN, GOOGL, NVDA, TSLA. The three borderline strategies (AAPL, MSFT, SPY) did NOT pass BH at alpha = 0.05 but were included in the empirical layer because their `bh_q_value <= 2.0 * alpha = 0.10`; AAPL also did not pass empirical alpha. META's q-value (0.1185) falls outside the borderline threshold, so empirical was honestly not run for it per locked Section 8 policy.

## 7. Interpretation

Bounded, honest reading:

- **The launch-current K=6 MTF empirical rerun is valid.** `validation_status="valid"` reflects that the walk-forward grid completed across all 40 folds, the BH and Bonferroni adjustments ran on the full N = 8 family, the empirical permutation / bootstrap layer ran for all 7 strategies in the BH-survivor-plus-borderline subset, and no engine-level exception or honest disclosure (other than the methodology-permitted `empirical_not_run` for META) was raised.
- **Four strategies survive the locked BH + empirical criteria** at alpha = 0.05: AMZN, GOOGL, NVDA, TSLA. These four also have Bonferroni p-values strictly below alpha (the conservative supplementary reading agrees with BH).
- **The old partial run is superseded** because its empirical layer could not run before PR #373 closed the K=6 MTF adapter's empirical-metadata gap. The partial sidecar is preserved at `output/validation/20260601_k6_mtf_phase5_launch_universe/` for audit-trail purposes only and is NOT used as launch-current evidence. The narrow-root ledger generation mechanism ensures the partial sidecar is structurally excluded from the launch-current ledger consumed by this report.
- **This report is self-administered validation evidence, not a proof or guarantee of future performance.** The locked methodology operationalizes the long-varied-window thesis (40 walk-forward folds across regime variety) but cannot guarantee that the four empirically validated K=6 MTF strategies will perform similarly out of distribution or in future regimes. The four empirically-validated rows are the operator's honest evidence-base for the launch claim; they are not predictions.
- **Claims must remain bounded to the tested launch universe and the tested run.** This report covers exactly the 8 Tier 1 secondaries listed in Section 5 and exactly the run identified by `run_id = 20260601_k6_mtf_phase5_launch_universe_empirical`. Any claim about other secondaries or other runs would require its own validation evidence and its own report.

## 8. Limitations and gates

This report does not clear public launch by itself. The following gates remain in place and are unaffected by this report:

- **Phase 5G data licensing.** This remains a SEPARATE public-launch gate per `md_library/shared/2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md`. This report does not produce, modify, or evaluate any data-licensing artifact. Public launch remains blocked on the Phase 5G outcome regardless of the Phase 5 evidence packaged here.
- **Public promotion is still a separate operator act.** PR #368's promotion helper at `utils/react_publish/promote_k6_mtf_artifact.py` enforces hard refusal of public-mode promotion unless `--public --phase5-report <path> --phase5-sha256 <SHA> --write --operator-approved` are all supplied AND the helper's pre-write public-mode safety check (`_verify_phase5_inputs` in `utils/react_publish/promote_k6_mtf_artifact.py`) verifies the Phase 5 report file's SHA-256 against the operator-declared value. Merging this report does NOT call the promotion helper.
- **The promotion helper requires this report path plus the report's SHA-256.** Once this report is merged, the operator computes its file SHA-256 and passes it as `--phase5-sha256`. The expected `--phase5-report` value is `md_library/shared/2026-06-01_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT.md` (project-relative; `_verify_phase5_inputs` in `utils/react_publish/promote_k6_mtf_artifact.py` refuses any Phase 5 report that does not resolve under `<PROJECT_DIR>`).
- **The React app must still consume only the promoted `k6_mtf_ranking_v1` fixture and must not recompute metrics.** Public launch presents pre-validated evidence; it does not become a live computation surface. This is the locked posture of `md_library/shared/2026-05-31_REACT_PUBLISH_DEPLOY_CONTRACT.md`.
- **K=6 MTF ledger app-summary baseline aggregate `n/a` is expected.** The locked Section 6 same-secondary baseline contract is honored per-strategy in the sidecar at `strategies[].per_fold_metrics[].same_secondary_baseline`. Engine-level `baseline_aggregate` and `per_fold_baseline_delta` are deliberately null / `n/a` for K=6 MTF because the adapter's `baseline_for_fold` is empty by design (a family-blended fold-level baseline would deliver misleading deltas to every per-secondary strategy). Future ledger-output enhancements may surface the per-strategy baselines in the markdown app-summary; until then the n/a entries are honest disclosure, not missing evidence.

## 9. Promotion-helper inputs (FUTURE; do NOT run from this PR)

When the operator later decides public promotion is appropriate and Phase 5G is separately cleared, the eventual command shape (paths project-relative; interpreter rendered as the CLAUDE.md pinned `spyproject2` interpreter):

- `phase_5_validation_report_path`: `md_library/shared/2026-06-01_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT.md` (this file).
- `phase_5_validation_report_sha256`: computed by the operator AFTER this report's final byte content is fixed (i.e., after this PR is merged). Specifically: `<PINNED_SPYPROJECT2_PYTHON> -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" <path>` against the merged file (using the pinned `spyproject2` interpreter from CLAUDE.md Section 1), or via any equivalent SHA-256 utility.

The report does NOT include its own SHA-256 inside its body to avoid the self-referential hash problem (writing the SHA-256 into the body would change the file content and therefore change the SHA-256). The operator brief for the PR that introduces this report carries the SHA computed against the as-committed file at merge time; the helper's `--phase5-sha256` argument receives that same value.

## 10. Final decision

- Phase 5 honest-validation packaging for the K=6 MTF launch-current evidence is COMPLETE once this report is merged.
- Public promotion remains BLOCKED until the operator explicitly authorizes promotion (by running the PR #368 promotion helper with the required arguments) AND Phase 5G data licensing is separately cleared.

End of report.
