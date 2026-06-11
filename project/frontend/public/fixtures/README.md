# React MVP Fixtures

GENERATED FILE -- do not hand-edit. promote_k6_mtf_artifact.py rewrites
this README from the committed promotion manifest + fixture at --write
time, so it can never go stale. The React app reads
`fixtures/k6_mtf_ranking.json` as a static asset; `output/` is gitignored.

## k6_mtf_ranking.json

- **Schema:** `k6_mtf_ranking_v2`
- **Run id:** `20260611T105546Z`
- **Generated at:** `20260611T105546Z`
- **Promoted at:** `2026-06-11T13:14:49Z`
- **Secondaries:** 207
- **Validation summary:** 90 board-validated, 117 not validated, 41 Stage-A
  excluded.
- **Public fixture SHA-256 / promotion source_sha256:**
  `1bc633863b1b7552c94440f86ee534a4db9c989127302a022e660eb9624f1b84`
- **Public fixture size:** 711052 LF bytes.

The committed fixture is slim: ranking metrics, K6 stack fields, validation
disclosure, and per-row Blob sidecar metadata. Inline `ccc_series` is empty
for every row.

## Full-Resolution CCC Sidecars

- **Storage mode:** `vercel_blob_sidecars`
- **Sidecar count:** 207
- **Sidecar prefixes (mixed-prefix carry-forward; no single prefix):**
  - 205 under `k6-mtf/20260604T110400Z_recook_full248_clean_csv/ccc-series/`
  - 2 under `k6-mtf/20260611T105546Z/ccc-series/`
- **Total sidecar bytes:** 123328431
- **Largest sidecar bytes:** 2857925
- **Total CCC points:** 1005575
- **All sidecars GET-verified:** True
- **Allowed Blob URL host pattern:** `*.public.blob.vercel-storage.com`
- **Verification manifest:** `output/crunch_runs/20260611T105546Z/publish_candidate/combined_ccc_sidecar_verification.json`
- **Verification manifest SHA-256:** `658b2fd98fe2e2a969c6358161ab89f6205ce3ac0410fb22f169020b9e160814`

The sidecars carry derived CCC fields only (`date_utc`,
`cumulative_capture_pct`, `per_bar_capture_pct`, `trade_direction`); no raw
OHLCV, no provider price series, no credentials.

## Validation Binding

- **Phase 5 report:** `md_library/shared/2026-06-11_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md`
- **Phase 5 report SHA-256:** `db6b077a92ef684a672193ecf0f1fc0ccfb2870be046419f2cba3c76ee958b5b`
- **Validation sidecar:** `output/crunch_runs/20260611T105546Z/publish_candidate/composite_validation_sidecar.json`
- **Validation sidecar SHA-256:** `628f9758374e2fdd6521530676e8c5308fe65a804d78bed577177aabb6143b16`
- **Validation run id:** `20260611T105546Z`
- **Methodology:** 10000 permutations, 10000 bootstrap samples,
  BH alpha 0.05, bonferroni supplementary, bootstrap CI 0.95, contract v1,
  methodology v1, rng_seed None, walk_forward_n_folds composite/advisory (null).

Leaderboard ordering reflects K=6 MTF ranking metrics. Phase 5 validation
survivorship is disclosed per row; ranking position is not a validation
claim.

## Mode B Controls

No raw OHLCV; no provider price charts or tables; no downloadable provider
price series; no public raw-data API; no monetization while yfinance remains
in the data pipeline.

