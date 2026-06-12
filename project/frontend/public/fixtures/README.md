# React MVP Fixtures

GENERATED FILE -- do not hand-edit. promote_k6_mtf_artifact.py rewrites
this README from the committed promotion manifest + fixture at --write
time, so it can never go stale. The React app reads
`fixtures/k6_mtf_ranking.json` as a static asset; `output/` is gitignored.

## k6_mtf_ranking.json

- **Schema:** `k6_mtf_ranking_v2`
- **Run id:** `20260612T223250Z`
- **Generated at:** `20260612T223250Z`
- **Promoted at:** `2026-06-12T23:30:30Z`
- **Secondaries:** 207
- **Validation summary:** 90 board-validated, 117 not validated, 41 Stage-A
  excluded.
- **Public fixture SHA-256 / promotion source_sha256:**
  `5f159e8584d003c7d14239ab1ff7c0bdc4c6802a21a5ac9c803da2502ac44265`
- **Public fixture size:** 691913 LF bytes.

The committed fixture is slim: ranking metrics, K6 stack fields, validation
disclosure, and per-row Blob sidecar metadata. Inline `ccc_series` is empty
for every row.

## Full-Resolution CCC Sidecars

- **Storage mode:** `vercel_blob_sidecars`
- **Sidecar count:** 207
- **Sidecar prefixes (mixed-prefix carry-forward; no single prefix):**
  - 1 under `k6-mtf/20260604T110400Z_recook_full248_clean_csv/ccc-series/`
  - 206 under `k6-mtf/20260612T223250Z/ccc-series/`
- **Total sidecar bytes:** 122458049
- **Largest sidecar bytes:** 2676588
- **Total CCC points:** 999086
- **All sidecars GET-verified:** True
- **Allowed Blob URL host pattern:** `*.public.blob.vercel-storage.com`
- **Verification manifest:** `output/crunch_runs/20260612T223250Z/publish_candidate/combined_ccc_sidecar_verification.json`
- **Verification manifest SHA-256:** `ea8435e7d09af70c5e3c3d3be78cf0fd9ee10ad418337f598e273432667d0424`

The sidecars carry derived CCC fields only (`date_utc`,
`cumulative_capture_pct`, `per_bar_capture_pct`, `trade_direction`); no raw
OHLCV, no provider price series, no credentials.

## Validation Binding

- **Phase 5 report:** `md_library/shared/2026-06-12_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md`
- **Phase 5 report SHA-256:** `5d0ae473a2028b6b5dfc33127973be3c55b7d7b15c961b6899291b54b9395e7e`
- **Validation sidecar:** `output/crunch_runs/20260612T223250Z/publish_candidate/composite_validation_sidecar.json`
- **Validation sidecar SHA-256:** `5a8507756b4a6a04315f947c83f69be6c0dd5fb4278c1d6574f5f1f39df71b56`
- **Validation run id:** `20260612T223250Z`
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

