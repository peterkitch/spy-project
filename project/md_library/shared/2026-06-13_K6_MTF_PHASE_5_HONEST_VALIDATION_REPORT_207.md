# K6 MTF Composite (Carry-Forward) Validation Proof

This is a COMPOSITE / carry-forward validation proof. It is NOT a single validation run of every row on the board.

- Assembly run id: 20260613T050111Z
- Assembled at (UTC): 20260613T050111Z
- Board rows (merged): 207
- board_validated: 78; not_validated: 129
- Carried rows (prior verdicts retained): 1
- Freshly validated rows (this run): 206

## Honesty statement

Carried rows retain the validation verdicts from their prior validation run(s); they were NOT re-validated in this run. Only the fresh rows were validated in the current validation run. Per-row provenance (validation_run_id, validation_artifact_sha256, validated_as_of_utc) is preserved on every row, and the composite sidecar carries a top-level source_validation_runs inventory.

The composite sidecar rng_seed is null: every source cohort is unseeded, so the single top-level seed is literally true for all rows.

Methodology lock: fully verified against the prior validation sidecar.

## Locked methodology

- contract_version: v1
- methodology_version: v1
- alpha: 0.05
- mc_method: benjamini_hochberg
- supplementary: bonferroni
- n_permutations: 10000
- n_bootstrap_samples: 10000
- bootstrap_ci_level: 0.95
- borderline_tolerance_multiplier: 2.0
- walk_forward_n_folds (advisory, data-derived): composite (mixed by validation cohort)
- baseline_method: same_ticker_buy_and_hold

## Source validation runs

- [carried] run_id=20260612T223250Z rows=1 source=prior_validation_sidecar
- [fresh] run_id=20260613T050111Z rows=206 source=fresh_validation_run
