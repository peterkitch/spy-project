# K=6 MTF Phase 5 Validation Campaign Specification

**Date:** 2026-06-01

**Status:** DOCS-ONLY CAMPAIGN SPECIFICATION (not a live config; runs nothing; produces no evidence)

**Author:** PRJCT9 sprint

**Scope:** Operator-supervised controlled_compute evidence-production campaign for the K=6 MTF layer of the layered Phase 5 honest-validation report.

---

## 1. Status and scope

This document specifies the operator-supervised controlled_compute campaign that will, at some later supervised step, drive the merged K=6 MTF validation adapter against the 8 Tier 1 launch-universe secondaries to produce the K=6 MTF layer of the layered Phase 5 honest-validation report.

This PR is docs-only. It:

- Does NOT run compute.
- Does NOT produce real validation evidence.
- Does NOT create real validation sidecars or controlled_compute manifests.
- Does NOT run `honest_validation_ledger`.
- Does NOT deploy. No private deployment, no public deployment.
- Does NOT change React, the K=6 MTF runtime modules, the validation engine, the ledger, or any pipeline source.
- Does NOT itself satisfy Phase 5. The Phase 5 honest-validation report still requires the operator-supervised campaign described here to run, plus the operator-reviewed packaging of the resulting ledger output into a report consumable by the PR #368 promotion helper's `validation_results` field.

Public deployment of the React K=6 MTF MVP board remains BLOCKED by the Phase 5 honest-validation report. PR #368's promotion helper hard-refuses public-mode promotion without a verified report file. This spec does not unlock public deployment; it defines the predecessor campaign.

## 2. Relationship to prior work

- **Producer of validation evidence:** the K=6 MTF validation adapter introduced by PR #371 at `project/utils/k6_mtf_validation/adapter.py`. The adapter implements `SelectionAdapter` and recomputes K=6 MTF ranking-row evidence per walk-forward fold from cutoff-safe upstream inputs. It reads zero bytes from `output/k6_mtf/<run>/k6_mtf_history.json` or `output/k6_mtf/<run>/k6_mtf_ranking.json` as validation evidence.
- **Methodology binding:** the locked Phase 5C methodology at `project/md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md`. Section 9 lists `k6_mtf` in the `producer_engine` allowed list. Section 13.5 binds the K=6 MTF adapter to the locked walk-forward / BH+Bonferroni / hybrid-empirical contract. Section 15 enumerates `[K6MTF:...]` reason codes.
- **Layered report decision:** PR #369 layered-claim decision at `project/md_library/shared/2026-05-31_K6_MTF_VALIDATION_LINKAGE_SCOPING.md`. Both the StackBuilder selected builds AND the K=6 MTF ranking rows are validated; the public claim joins them at the report level via Option L4 (report-level honest-validation package referenced from the PR #368 promotion manifest's `validation_results.phase_5_validation_report_path`).
- **Adapter specification:** PR #370 at `project/md_library/shared/2026-05-31_K6_MTF_VALIDATION_PRODUCER_ADAPTER_SPEC.md`.
- **Promotion gate:** the operator-run private/internal promotion helper at `project/utils/react_publish/promote_k6_mtf_artifact.py` (PR #367 / PR #368). Public-mode promotion requires `validation_results.phase_5_validation_report_path` to point at a verified honest-validation report; until that report exists the helper hard-refuses public mode.

The chain is: this campaign spec defines the operator-supervised compute step; the compute step produces a `validation_contract_v1` sidecar; the ledger aggregates the sidecar (paired with the StackBuilder layer) into `honest_validation_ledger.json` + `.md`; the operator packages a Phase 5 honest-validation report from the ledger output; the PR #368 promotion helper then references that report when (and only when) the operator clears public mode.

## 3. Launch universe

The campaign covers exactly the 8 Tier 1 K=6 MTF launch-family secondaries:

- AAPL
- AMZN
- GOOGL
- META
- MSFT
- NVDA
- SPY
- TSLA

This list is the default `_DEFAULT_LAUNCH_FAMILY` in `project/utils/k6_mtf_validation/adapter.py` and matches the live operator-authorized K=6 MTF ranking artifact universe from the K=6 MTF launch-path contract at `project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`. The campaign MUST NOT silently narrow this universe; missing-input secondaries remain visible as `unavailable` / `failed` candidates with bracketed `[K6MTF:...]` reason codes per the locked methodology Section 13.5 and adapter behavior.

## 4. Producer command

### Interpreter and cwd

The campaign runs the merged K=6 MTF validation adapter as a Python module. Two binding rules:

- **Interpreter:** the pinned `spyproject2` conda environment per `<PROJECT_DIR>/CLAUDE.md` Section 1 (Pinned Python interpreter). Throughout this spec the interpreter is referenced as `<PINNED_SPYPROJECT2_PYTHON>`; the operator substitutes the literal path documented in CLAUDE.md at run time. Do NOT substitute a bare `python` invocation; CLAUDE.md Section 1 explicitly forbids it.
- **Working directory:** `<PROJECT_DIR>`. This is non-negotiable because `python -m utils.k6_mtf_validation.adapter` requires the `utils` package to resolve from `<PROJECT_DIR>` (a `<REPO_ROOT>` cwd would not have a top-level `utils/` sibling and would fail import-time). The adapter's output-base resolver (`resolve_validation_output_base` in `project/utils/k6_mtf_validation/adapter.py`) anchors the repo-root-relative `Path("project/output/validation")` under `<REPO_ROOT>` automatically when invoked from `<PROJECT_DIR>`, so the sidecar lands at `<REPO_ROOT>/project/output/validation/<run_id>/validation.json` with no `project/project` doubling. The PR #371 test `TestCliInvocationPathProof::test_main_cli_writes_to_resolved_base_with_no_project_doubling` pins this property.

### Verified command shape

The verified argparse surface from `project/utils/k6_mtf_validation/adapter.py` `_parse_args` / `main`:

```
cwd: <PROJECT_DIR>
command:
  <PINNED_SPYPROJECT2_PYTHON>
  -m utils.k6_mtf_validation.adapter
  --secondaries AAPL,AMZN,GOOGL,META,MSFT,NVDA,SPY,TSLA
  --stackbuilder-root output/stackbuilder
  --signal-library-dir signal_library/data/stable
  --price-cache-dir price_cache/daily
  --cache-dir cache/results
  --rng-seed <OPERATOR_FIXED_SEED>
  --run-id <OPTIONAL_NAMED_RUN_ID>
```

Notes:

- All five upstream-root arguments use the adapter's documented defaults at `project/utils/k6_mtf_validation/adapter.py` (which import `DEFAULT_STACKBUILDER_ROOT="output/stackbuilder"`, `DEFAULT_STABLE_DIR="signal_library/data/stable"`, `DEFAULT_PRICE_CACHE_DIR="price_cache/daily"`, `DEFAULT_CACHE_DIR="cache/results"` from `project/k6_mtf_history_producer.py`). Passing them explicitly on the command line is recommended for campaign reproducibility even though they are also the defaults.
- The `--secondaries` value above is the default `_DEFAULT_LAUNCH_FAMILY`. Passing it explicitly is recommended so the campaign manifest's command list is self-documenting.
- The campaign DOES NOT pass `--output-dir`. The resolver lands the sidecar correctly without it, and supplying an explicit `--output-dir` here would defeat the resolver path-doubling guard the adapter is responsible for. The operator MAY pass an explicit `--output-dir` only if there is a documented reason; if so, the chosen value MUST live under `project/output/validation/<run_id>/` so `controlled_compute`'s default discovery glob still finds it.
- `--rng-seed` is the only operator-chosen parameter in the default flow (see Section 6).
- `--run-id` is optional; when omitted the adapter calls `generate_run_id("k6_mtf", "run_directory")` and produces a timestamp-based run id. A named run id is recommended for the operator campaign so the produced sidecar path is self-documenting in the controlled_compute manifest.

### Producer identity recorded in the sidecar

The sidecar emitted by this command carries:

- `producer_engine` = `"k6_mtf"` (constant `K6_MTF_PRODUCER_ENGINE` in `project/utils/k6_mtf_validation/adapter.py`).
- `app_surface` = `"run_directory"` (constant `K6_MTF_APP_SURFACE`).
- `validation_contract_version` = `"v1"` (constant `VALIDATION_CONTRACT_VERSION` in `project/validation_engine.py`).
- `validation_methodology_version` = `"v1"` (constant `VALIDATION_METHODOLOGY_VERSION`).
- `validation_status` per the Section 10 status taxonomy of the locked methodology.

## 5. controlled_compute job-spec shape

The campaign is one controlled_compute job covering all 8 secondaries (NOT eight per-secondary jobs). Rationale: the adapter's strategy family and `n_strategies_tested` are defined over the full launch universe (locked methodology Section 13.5; PR #371 adapter contract); `validate_strategy_set` applies BH/Bonferroni control across that family in a single contract; survivorship disclosure is a single artifact; the ledger and the PR #368 promotion helper expect exactly one K=6 MTF sidecar to discover per campaign run. Splitting into per-secondary jobs would produce eight separate single-strategy sidecars with N=1 BH semantics and would defeat the locked multiple-comparisons control.

The verified controlled_compute job-spec field shape from `project/controlled_compute.py` (`validate_compute_job_spec` and `_planned_result`):

```
Top-level (job-spec file):
  compute_contract_version: "controlled_compute_v1"
  run_id:                   <campaign-run-id>
  description:              <campaign-description>
  execution_mode:           "serial"
  max_workers:              1
  budget:
    max_jobs:                 1
    max_wall_seconds_per_job: <operator-decided; large enough for full empirical layer>
    fail_fast:                true
  jobs:
    - job_id:                            "k6-mtf-launch-universe"
      command:                           <see Section 4>
      cwd:                               "project"
      timeout_seconds:                   <operator-decided; <= budget.max_wall_seconds_per_job>
      producer_engine:                   "k6_mtf"
      app_surface:                       "run_directory"
      validation_sidecar_search_root:    "project/output/validation"
      validation_sidecar_glob:           "**/validation.json"
      validation_sidecar_required:       true
      metadata:
        phase:                  "5C-2 K6 MTF campaign"
        launch_universe:        ["AAPL","AMZN","GOOGL","META","MSFT","NVDA","SPY","TSLA"]
        adapter_pr:             371
        methodology_section:    "13.5"
```

Field-by-field notes (cross-referenced with `project/controlled_compute.py`):

- `command` is a list of strings; shell strings are forbidden (`shell=False` is enforced at execution time per `controlled_compute.py` `validate_compute_job_spec`).
- `cwd` is `"project"` so controlled_compute resolves it relative to `<REPO_ROOT>` when the operator invokes controlled_compute from `<REPO_ROOT>`. This satisfies the cwd binding from Section 4 (the worker process ends up with cwd = `<PROJECT_DIR>`).
- `validation_sidecar_search_root` is the K=6 MTF sidecar discovery root and MUST be `"project/output/validation"`, which matches `validation_engine.VALIDATION_OUTPUT_BASE_DIR` and the adapter's output-base resolver target.
- `validation_sidecar_glob` is the default discovery glob `"**/validation.json"` (per `controlled_compute._resolve_sidecar_glob`). Stating it explicitly keeps the controlled_compute manifest audit-complete: the recorded glob always equals the pattern the worker actually used.
- `validation_sidecar_required` is `true` so controlled_compute fails the job if no new sidecar is discovered. With `validation_sidecar_search_root` supplied, the effective `validation_sidecar_required` already defaults to `true` (per `controlled_compute._resolve_sidecar_required`); stating it explicitly is documentation.
- `expected_validation_sidecar` is NOT supplied. The discovery-mode path is correct here because the adapter generates its own validation run id internally via `generate_run_id("k6_mtf", "run_directory")`; the operator cannot know the produced sidecar filename up front. This matches the StackBuilder onboarding precedent at `project/examples/controlled_compute/stackbuilder_onboarding_job_spec.json`.
- `producer_engine` and `app_surface` are echoed into the manifest planned result (per `controlled_compute._planned_result`) so the manifest carries producer identity even before the sidecar is discovered.
- `metadata` is a free-form audit dict; the keys above are recommended for campaign traceability but the only contract-level requirement is that `metadata` (if present) is a dict.

The above is a CONFIG SHAPE described in the doc. This PR does NOT add a runnable job-spec file. The existing controlled_compute precedent for sidecar-discovery jobs is the StackBuilder onboarding example at `project/examples/controlled_compute/stackbuilder_onboarding_job_spec.json`; the operator should mirror that file's structure when assembling the campaign job-spec at run time, substituting the K=6 MTF command and field values above.

Manifest fields the controlled_compute worker records onto the campaign run's planned result (per `controlled_compute._planned_result`), available for audit after the campaign:

- `command`, `cwd`, `producer_engine`, `app_surface`, `metadata`
- `validation_sidecar_path` (filled from the discovered sidecar)
- `validation_sidecar_sha256` (filled by `_validate_and_hash_validation_sidecar`)
- `validation_run_id` (read from `contract.get("run_id")`)
- `validation_status` (read from `contract.get("validation_status")`)
- `validation_sidecar_search_root`, `validation_sidecar_glob`, `validation_sidecar_required`, `validation_sidecar_discovery_candidates`
- `wall_seconds`, `returncode`, `timed_out`, `stdout_tail`, `stderr_tail`, `issues`

## 6. Validation params

The campaign uses methodology / engine defaults verbatim. Every default below is read from `project/validation_engine.py` constants and consumed unchanged by `project/utils/k6_mtf_validation/adapter.py`:

| Parameter | Value | Source constant |
|---|---|---|
| `alpha` | `0.05` | `DEFAULT_ALPHA` |
| `initial_train_days` | `1260` (5 trading years) | `DEFAULT_INITIAL_TRAIN_DAYS` |
| `test_window_days` | `252` (1 trading year) | `DEFAULT_TEST_WINDOW_DAYS` |
| `step_days` | `252` (1 trading year) | `DEFAULT_STEP_DAYS` |
| `outcome_windows` | `(1, 5, 21, 63, 252)` trading days | `DEFAULT_OUTCOME_WINDOWS` |
| `n_permutations` | `10000` | `DEFAULT_N_PERMUTATIONS` |
| `n_bootstrap_samples` | `10000` | `DEFAULT_N_BOOTSTRAP_SAMPLES` |
| `borderline_tolerance_multiplier` | `2.0` (borderline cutoff `2.0 * alpha = 0.10`) | `DEFAULT_BORDERLINE_TOLERANCE_MULTIPLIER` |
| `bootstrap_ci_level` | `0.95` | `DEFAULT_BOOTSTRAP_CI_LEVEL` |
| `multiple_comparisons_control_method` | `"benjamini_hochberg"` (primary) | locked methodology Section 7 |
| `multiple_comparisons_supplementary` | `"bonferroni"` | locked methodology Section 7 |
| `baseline_method` | `"same_ticker_buy_and_hold"` | locked methodology Section 6 |
| Validation output base | `<REPO_ROOT>/project/output/validation` | `VALIDATION_OUTPUT_BASE_DIR` resolved via `resolve_validation_output_base` from `<PROJECT_DIR>` cwd |

Per locked methodology Section 19, any deviation from the values above requires an explicit operator decision before the campaign runs, and the deviation MUST be recorded in the campaign job-spec `metadata` for audit.

### rng_seed policy

The one operator-chosen parameter is `--rng-seed`. The campaign SHOULD pass a fixed integer seed so the BH/Bonferroni order-of-survivorship and the permutation / bootstrap empirical layer are reproducible across re-runs of the same campaign. The locked methodology does not pin a specific seed; the operator chooses, records the chosen seed in the campaign job-spec `metadata`, and re-uses the same seed for any re-run that aims to reproduce the campaign verdict.

`--rng-seed` is otherwise optional. Omitting it leaves the engine's empirical layer with a non-deterministic generator, which is methodologically acceptable but defeats reproducibility-of-verdict.

## 7. Operator preconditions

Before authorizing the compute run, the operator MUST confirm all of the following. Each item is a read-only inspection; none of them runs compute or modifies state. The PR #371 adapter fails closed on any of these conditions and emits a bracketed `[K6MTF:...]` reason code, but the operator should preflight rather than rely on adapter failure to discover problems.

Upstream inputs:

- **StackBuilder `selected_build.json` present for all 8 secondaries** under `<PROJECT_DIR>/output/stackbuilder/<SEC>/selected_build.json`. Missing files map to adapter reason code `[K6MTF:missing_selected_build]`.
- **`combo_k=6.json` present for all 8 secondaries** under the `selected_run_dir` referenced by each `selected_build.json`. Missing files map to `[K6MTF:missing_combo_k6]`.
- **Per-(member, timeframe) signal libraries present** under `<PROJECT_DIR>/signal_library/data/stable/` for every member referenced by every secondary's `combo_k=6.json`, across every K=6 MTF timeframe (1d, 1wk, 1mo, 3mo, 1y). Missing files map to `[K6MTF:missing_member_library]`.
- **Secondary daily close present** for every secondary at `<PROJECT_DIR>/price_cache/daily/<SEC>.parquet` or `.csv`, with the `cache/results/<SEC>_precomputed_results.pkl` fallback covered by the K=6 MTF history producer's documented loader order.

Data currency:

- **Upstream signal libraries and secondary close are current enough** to support a walk-forward grid with `DEFAULT_INITIAL_TRAIN_DAYS + DEFAULT_TEST_WINDOW_DAYS = 1512` trading bars of history. If the available history is shorter, the engine emits a `validation_in_sample_only` status per locked methodology Section 10 and the campaign produces no walk-forward folds; the operator should detect this read-only before authorization rather than via an empty fold grid in the sidecar.
- **Prior supervised work in Phase 6I-13 through 6I-17 was blocked by stale / cache-lag conditions** between the upstream cache and the resolved `current_as_of_date` (per CLAUDE.md Section 6.0). The campaign warrants a separate read-only pre-run check before compute authorization: the operator confirms that the most recent secondary close bar and the most recent per-(member, timeframe) signal library bar across the launch family are both at or beyond the operator-chosen campaign-as-of date. This pre-run check is a separate read-only step recommended as the next item in the follow-on sequence (Section 11) and is not part of this campaign-spec PR.

State snapshots:

- **`<PROJECT_DIR>/output/validation/` state snapshotted** before the run. The snapshot is the list of pre-existing `validation.json` files under the root; controlled_compute's discovery mode finds new sidecars by snapshot-diff (`_snapshot_validation_sidecars` -> `_discover_new_validation_sidecars` in `project/controlled_compute.py`), so the snapshot is needed for unambiguous discovery.
- **`<PROJECT_DIR>/output/controlled_compute/` state snapshotted** before the run. The default controlled_compute output root is `Path("project/output/controlled_compute")` per `controlled_compute.DEFAULT_CONTROLLED_COMPUTE_OUTPUT_ROOT`; controlled_compute writes per-run manifests there.
- **`<PROJECT_DIR>/output/validation_ledger/` state snapshotted** before the eventual ledger step. The default ledger output dir is `output/validation_ledger` per `honest_validation_ledger.py` `_DEFAULT_OUTPUT_DIR` and the documented CLI invocation in the module docstring.

Authorization posture:

- **`--skip-durable-validation` is NOT passed.** This flag does not exist on the K=6 MTF adapter argparse surface; the matching flag on StackBuilder (`stackbuilder.py:3990-3997`) MUST NOT be used in the operator's StackBuilder runs that feed this campaign's upstream inputs. Any `durable_validation_status="skipped"` on the layered report would make the public Phase 5 report incomplete per the PR #370 spec Section 12.
- **No public deployment is authorized by this campaign.** Public-mode promotion still requires a verified honest-validation report file referenced by `validation_results.phase_5_validation_report_path` in the PR #368 promotion helper; that report does not yet exist.

## 8. Expected outputs (produced by the later supervised run, NOT by this PR)

When the operator later runs the campaign defined here, the supervised compute produces:

- **One K=6 MTF validation sidecar** at `<REPO_ROOT>/project/output/validation/<run_id>/validation.json` per the adapter's output-base resolver behavior. The sidecar shape is `validation_contract_v1` per `project/validation_engine.py`, enriched by the adapter's `_enrich_contract_with_same_secondary_baseline` so every `strategies[].per_fold_metrics[]` entry carries `same_secondary_baseline` (n_observations, baseline_sharpe, baseline_total_return, baseline_mean_return, baseline_std, issues) on disk.
- **One controlled_compute manifest** under `<REPO_ROOT>/project/output/controlled_compute/<campaign-run-id>/` recording the planned and executed job result, the discovered sidecar path, the sidecar SHA-256, the discovered validation run id, and the validation status, plus the audited search-root / glob / required fields.
- **Subsequent honest_validation_ledger output** at `<REPO_ROOT>/project/output/validation_ledger/honest_validation_ledger.json` and `honest_validation_ledger.md` when the operator separately runs `python project/honest_validation_ledger.py --validation-root project/output/validation --output-dir project/output/validation_ledger` (per the module's documented CLI invocation). The ledger version is `validation_ledger_v1` (`honest_validation_ledger.py` `_LEDGER_VERSION`).

This campaign-spec PR produces NONE of the above artifacts. The artifacts above are produced ONLY by the later operator-supervised run.

## 9. Acceptance criteria for the later run

The operator's post-run review of the campaign's sidecar and manifest MUST confirm all of the following before the ledger step, the report-packaging step, and any change to `validation_results` on the PR #368 promotion manifest:

- **Exactly one K=6 MTF sidecar is discovered** by the controlled_compute search-root + glob. If zero sidecars or more than one new sidecar are discovered, the campaign halts and the operator inspects (controlled_compute fails the job per `validation_sidecar_required=true`).
- **`validate_validation_contract_v1` passes** on the discovered sidecar. The function lives at `project/validation_engine.py` and is invoked automatically by `controlled_compute._validate_and_hash_validation_sidecar` and by `honest_validation_ledger.load_validation_sidecar`.
- **`producer_engine == "k6_mtf"`**.
- **`app_surface == "run_directory"`**.
- **`n_strategies_tested == 8`** unless missing-input candidates are explicitly disclosed via the adapter contract (per locked methodology Section 13.5: missing-input secondaries remain visible as `unavailable` / `failed` candidates with bracketed `[K6MTF:...]` reason codes; in that case the operator MUST review the per-strategy `issues` and decide whether the missing-input condition is acceptable for the public claim).
- **`strategies[].per_fold_metrics[].same_secondary_baseline` is present** for every strategy and every fold, carrying the stable six-key schema (`n_observations`, `baseline_sharpe`, `baseline_total_return`, `baseline_mean_return`, `baseline_std`, `issues`). This is the persisted location of the locked 5C-1 Section 6 same-secondary buy-and-hold contract; PR #371 tests pin this on disk via `TestPersistedSameSecondaryBaseline`.
- **Engine-level `per_fold_baseline_delta` entries remain not misleading.** Specifically, for every K=6 MTF strategy, every `per_fold_baseline_delta` entry MUST carry `sharpe_delta == null` and `return_delta == null` because the adapter's `baseline_for_fold` is deliberately empty (a family-blended fold-level baseline would deliver misleading deltas to every per-secondary strategy). PR #371 test `TestPersistedSameSecondaryBaseline::test_engine_per_fold_baseline_delta_remains_null` pins this on both the in-memory and on-disk contracts.
- **No `output/k6_mtf/<run>/k6_mtf_history.json` or `k6_mtf_ranking.json` was read as validation evidence by the campaign.** This is statically true for the merged adapter (PR #371 verification: no `score_history_artifact` / `load_and_score` import; sentinel `builtins.open` monkeypatch test in `TestNoLookaheadGuards` pins it). The operator confirms the property by reading the adapter source and the PR #371 verification record, NOT by running anything new.
- **Sidecar SHA-256 is recorded by controlled_compute** on the manifest (`validation_sidecar_sha256`) and matches the SHA-256 of the on-disk sidecar bytes. The operator can independently re-compute the SHA via `validation_engine.compute_validation_artifact_hash`.

## 10. Failure / stop conditions

The campaign halts and does NOT proceed to the ledger step under any of the following conditions:

- Any upstream precondition from Section 7 is missing or stale.
- The data-currency precondition (Section 7) is not satisfied at the operator's chosen campaign-as-of date.
- controlled_compute discovers zero or more than one new `validation.json` under `validation_sidecar_search_root` (sidecar required, ambiguous discovery, or no discovery).
- `validate_validation_contract_v1` raises on the discovered sidecar.
- `validation_sidecar_sha256` cannot be computed (e.g., the sidecar file is unreadable or zero-byte).
- `producer_engine != "k6_mtf"` or `app_surface != "run_directory"` on the discovered sidecar.
- `n_strategies_tested` is not 8 and the missing-input disclosure path was not deliberately accepted by the operator.
- The adapter raises an unrecoverable exception and emits a `validation_status = "failed"` contract per the engine status taxonomy (locked methodology Section 10).
- Engine-level `per_fold_baseline_delta` entries carry non-null `sharpe_delta` or `return_delta` for any K=6 MTF strategy (would indicate a baseline-handling regression).
- Any `strategies[].per_fold_metrics[]` entry is missing `same_secondary_baseline` (would indicate the run_validation post-processor did not run; on-disk evidence loss).

Under ALL conditions, public promotion remains forbidden until the operator-packaged honest-validation report exists, is verified, and is referenced by `validation_results.phase_5_validation_report_path` in the PR #368 promotion manifest. Halting this campaign does NOT close the public-launch gate; it merely defers the K=6 MTF layer of the layered Phase 5 report.

## 11. Follow-on sequence

This campaign-spec PR is item 1 of the sequence below. Items 2 onward are NOT part of this PR.

1. **(This PR.)** Docs-only campaign spec.
2. **Read-only pre-run check** for this campaign. A separate read-only inspection PR or session covering the operator preconditions in Section 7 (upstream-input presence, data currency, snapshot baseline). The pre-run check produces a written verdict, runs no compute, and writes nothing under `output/`.
3. **Operator-run controlled_compute campaign.** Authorized only after items 1 and 2 produce a clean verdict. Runs the verified command from Section 4 via a controlled_compute job-spec matching the shape in Section 5. Produces one K=6 MTF sidecar and one controlled_compute manifest.
4. **Operator-run `honest_validation_ledger`** at `python project/honest_validation_ledger.py --validation-root project/output/validation --output-dir project/output/validation_ledger`. Aggregates the K=6 MTF sidecar from item 3 (paired with the StackBuilder layer per PR #369 Option L4) into `honest_validation_ledger.json` and `honest_validation_ledger.md` under `output/validation_ledger/`.
5. **Operator review and packaging of the honest-validation report.** The operator reviews the ledger output, packages it into the Phase 5 honest-validation report consumable by the PR #368 promotion helper's `validation_results.phase_5_validation_report_path` field, and computes the report's SHA-256.
6. **PR #368 promotion helper public-mode use.** Only after item 5 produces a verified report file. The operator invokes `python project/utils/react_publish/promote_k6_mtf_artifact.py --public --phase5-report <PATH> --phase5-sha256 <SHA>` with `--write --operator-approved`; the helper verifies the SHA, writes the public-mode manifest, and produces the artifact promotion record. Public deployment configuration is then a separate operator step.
7. **Phase 5G data licensing** remains a separate parallel public-launch gate not covered by this sequence.

## 12. Non-goals

- No compute is run.
- No evidence is produced.
- No real validation sidecars are created.
- No real controlled_compute manifests are created.
- No `honest_validation_ledger` is run.
- No K=6 MTF history producer, K=6 MTF ranking engine, StackBuilder, TrafficFlow, OnePass, or ImpactSearch is invoked.
- No source file, test file, schema, or React runtime is changed.
- No CI, deploy config, or package file is touched.
- No live job-spec file is added under `project/examples/controlled_compute/`. The existing `project/examples/controlled_compute/stackbuilder_onboarding_job_spec.json` is the precedent template the operator copies and adapts at run time, substituting the K=6 MTF command and field values from Sections 4 and 5.
- No Tier 2 universe scoping is included.
- No private or public deployment is authorized or enabled by this PR.

End of document.
