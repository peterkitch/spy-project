# Phase 5D-1 Operational Onboarding

## Purpose

This is the first real validation-producing controlled_compute
onboarding path. After 5D-1 (PR #176) merged, the orchestrator was
tested only against synthetic subprocess fixtures. This runbook
shows how to run it against an actual durable-tier app — namely
**StackBuilder** — and verify the produced `validation.json`
sidecar end-to-end through the honest validation report ledger.

StackBuilder was selected as the first onboarding target because:

- StackBuilder has a real CLI in `project/stackbuilder.py`
  (`parse_args` / `main`).
- Every StackBuilder run is a durable-tier validation run per
  locked 5C-1 §13.2 (the run produces a `validation.json` sidecar
  alongside the `run_manifest.json`).
- ImpactSearch durable validation is tied to the Dash / XLSX
  export workflow and is not the clean first onboarding surface.
- Spymaster and Confluence are interactive-tier per locked
  5C-1 §13.1 — they emit no durable sidecar and are out of scope
  for the controlled compute orchestrator.

## Why sidecar discovery exists

`controlled_compute.py`'s original 5D-1 contract required an
exact `expected_validation_sidecar` path on each job. That worked
for synthetic fixtures where the test wrote a known
`validation.json` to a predictable path, but it does **not** work
for real StackBuilder runs:

- StackBuilder generates its `validation_run_id` internally via
  `validation_engine.generate_run_id("stackbuilder", "run_directory")`.
- The sidecar lands at
  `project/output/validation/<generated-run-id>/validation.json`.
- An operator launching the job through `controlled_compute` cannot
  predict that path before the subprocess runs.

The Phase 5D-1 onboarding amendment adds a discovery contract:

- The job spec sets `validation_sidecar_search_root` (and
  optionally `validation_sidecar_glob`).
- `controlled_compute` snapshots all matching sidecars under that
  root **before** running the subprocess.
- After command success, `controlled_compute` searches again,
  isolates exactly one **new** `validation.json` (paths not in
  the snapshot), validates it via
  `validation_engine.validate_validation_contract_v1`, hashes it
  via `validation_engine.compute_validation_artifact_hash`, and
  records the discovered path + SHA-256 in the compute manifest.

`expected_validation_sidecar` and `validation_sidecar_search_root`
are mutually exclusive — one or the other, never both.

## Operator commands

All commands assume `cwd` is the repository root containing the
`project/` directory.

### 1. Dry-run the job spec

Plan only — no subprocess executes; no real StackBuilder run; no
sidecar produced:

    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" project/controlled_compute.py --job-spec project/examples/controlled_compute/stackbuilder_onboarding_job_spec.json --dry-run

Expected stdout:

    [5D-1] controlled compute: run_id=controlled-compute-stackbuilder-onboarding jobs=1 succeeded=0 failed=0 timed_out=0 planned=1 manifest=project\output\controlled_compute\controlled-compute-stackbuilder-onboarding\compute_manifest.json

The dry-run manifest preserves the discovery configuration
(`validation_sidecar_search_root`, `validation_sidecar_glob`,
`validation_sidecar_required`) for audit.

### 2. Real run with strict failure semantics

This actually runs StackBuilder against the smoke-test universe
(`SPY` secondary, `AAPL,MSFT,NVDA,QQQ,IWM` primaries) and verifies
that exactly one new `validation.json` lands under
`project/output/validation`:

    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" project/controlled_compute.py --job-spec project/examples/controlled_compute/stackbuilder_onboarding_job_spec.json --strict

`--strict` causes the CLI to exit nonzero if the job fails or
times out (the manifest is still written either way). The
amendment-cycle commit `3b39afc` ensures the strict-failure stdout
reports the actual generated `run_id` and manifest path.

### 3. Verify the validation envelope through the honest ledger

After the real run produces the new sidecar, regenerate the
honest validation report ledger so it picks up the StackBuilder
run:

    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" project/honest_validation_ledger.py --validation-root project/output/validation --output-dir project/output/validation_ledger

This reads every `validation.json` under
`project/output/validation/` (including the one StackBuilder just
produced) and rewrites
`project/output/validation_ledger/honest_validation_ledger.json`
+ `honest_validation_ledger.md` with full strategy visibility.

## Expected outputs

After step 1 (dry-run):

- `project/output/controlled_compute/controlled-compute-stackbuilder-onboarding/compute_manifest.json`
  — contains `dry_run: true`, `totals.planned: 1`, no
  `validation_sidecar_path`, no SHA-256.

After step 2 (real run, success):

- `project/output/controlled_compute/controlled-compute-stackbuilder-onboarding/compute_manifest.json`
  — `totals.succeeded: 1`,
  `jobs[0].validation_sidecar_path` set to the discovered
  `validation.json`,
  `jobs[0].validation_sidecar_sha256` populated,
  `jobs[0].validation_run_id` populated,
  `jobs[0].validation_status` populated.
- `project/output/validation/<stackbuilder-run-id>/validation.json`
  — the actual durable validation envelope StackBuilder wrote.
- `project/output/stackbuilder_controlled_compute_onboarding/...`
  — the StackBuilder run directory itself
  (`run_manifest.json`, leaderboard, etc.).

After step 3 (ledger regeneration):

- `project/output/validation_ledger/honest_validation_ledger.json`
- `project/output/validation_ledger/honest_validation_ledger.md`

The ledger should now include a row for the StackBuilder
onboarding `run_id` with full strategy visibility (BH survivors
AND non-survivors, including any `empirical_not_run` strategies).

## Operational cautions

- **Pinned interpreter:** every command in this runbook uses
  `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
  Do not run from a different Python; `validation_engine`'s
  byte-identical regression baselines were locked under that
  interpreter (see `project/CLAUDE.md` Section 1).
- **Locked validation defaults:** real StackBuilder validation
  uses the locked 5C empirical defaults
  (`n_permutations`, `n_bootstrap_samples`, `rng_seed`) unless
  StackBuilder's CLI changes in a separate PR. The 5D-1
  onboarding does not expose CLI flags for those.
- **Multi-job specs and discovery ambiguity:** if two jobs in
  the same spec write sidecars under the same
  `validation_sidecar_search_root` in parallel, discovery may
  correctly fail one or both with
  `[CONTROLLED_COMPUTE:validation_sidecar_ambiguous]`. For
  multi-job specs, prefer either serial execution or
  `expected_validation_sidecar` exact paths so each job's sidecar
  is unambiguous.
- **Spymaster + Confluence are out of scope:** these surfaces
  are interactive-tier and do not emit durable sidecars. They
  cannot be onboarded through this controlled compute pattern
  without first adding opt-in durable persistence (which would
  be a separate sub-phase).
- **First-time StackBuilder run is not free:** the smoke-test
  command in the example spec runs against five real primary
  signal libraries against SPY. If those libraries are not on
  disk / are stale, the run will fail or be slow. Verify
  pre-flight that
  `project/signal_library/data/stable/{AAPL,MSFT,NVDA,QQQ,IWM}_*.pkl`
  are present and current before launching the real run.

## Job spec reference

The example spec at
`project/examples/controlled_compute/stackbuilder_onboarding_job_spec.json`
is the canonical onboarding shape. Operators forking this spec
for other StackBuilder workloads should keep:

- `compute_contract_version: "controlled_compute_v1"`
- `producer_engine: "stackbuilder"`
- `app_surface: "run_directory"`
- `validation_sidecar_search_root: "project/output/validation"`
- `validation_sidecar_glob: "**/validation.json"`
- `validation_sidecar_required: true`

and adjust the StackBuilder CLI args (`--secondary`,
`--primaries`, `--top-n`, etc.) to the production workload.
