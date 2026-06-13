# React MVP Fixtures

GENERATED FILE -- do not hand-edit. promote_k6_mtf_artifact.py rewrites
this README from the committed promotion manifest + fixture at --write
time, so it can never go stale. The React app reads
`fixtures/k6_mtf_ranking.json` as a static asset; `output/` is gitignored.

## k6_mtf_ranking.json

- **Schema:** `k6_mtf_ranking_v2`
- **Run id:** `20260613T050111Z`
- **Generated at:** `20260613T050111Z`
- **Promoted at:** `2026-06-13T06:09:26Z`
- **Secondaries:** 207
- **Validation summary:** 78 board-validated, 129 not validated, 41 Stage-A
  excluded.
- **Public fixture SHA-256 / promotion source_sha256:**
  `9b6514f04553a007730a7f4118c065b176ebdbd14423698392ecf43adcd5499e`
- **Public fixture size:** 692479 LF bytes.

The committed fixture is slim: ranking metrics, K6 stack fields, validation
disclosure, and per-row Blob sidecar metadata. Inline `ccc_series` is empty
for every row.

## Full-Resolution CCC Sidecars

- **Storage mode:** `vercel_blob_sidecars`
- **Sidecar count:** 207
- **Sidecar prefixes (mixed-prefix carry-forward; no single prefix):**
  - 1 under `k6-mtf/20260604T110400Z_recook_full248_clean_csv/ccc-series/`
  - 206 under `k6-mtf/20260613T050111Z/ccc-series/`
- **Total sidecar bytes:** 122771246
- **Largest sidecar bytes:** 2858060
- **Total CCC points:** 1002142
- **All sidecars GET-verified:** True
- **Allowed Blob URL host pattern:** `*.public.blob.vercel-storage.com`
- **Verification manifest:** `output/crunch_runs/20260613T050111Z/publish_candidate/combined_ccc_sidecar_verification.json`
- **Verification manifest SHA-256:** `6f2e0f7924b370434585f7bee2e88c6e674cdee40ac704c44d2cc726283bbfd6`

The sidecars carry derived CCC fields only (`date_utc`,
`cumulative_capture_pct`, `per_bar_capture_pct`, `trade_direction`); no raw
OHLCV, no provider price series, no credentials.

## Validation Binding

- **Phase 5 report:** `md_library/shared/2026-06-13_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md`
- **Phase 5 report SHA-256:** `2719d6890fea01f495d090db583b006a85f64622b6de4e738dd244b5b1215dcf`
- **Validation sidecar:** `output/crunch_runs/20260613T050111Z/publish_candidate/composite_validation_sidecar.json`
- **Validation sidecar SHA-256:** `a93c54b4a8487f58b546cbe29e524d9ef9312c5b662fa97dda2e1e0797dcc8b2`
- **Validation run id:** `20260613T050111Z`
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

