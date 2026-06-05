# React MVP Fixtures

This directory holds the committed JSON artifact the React MVP consumes at
runtime. The React app reads `fixtures/k6_mtf_ranking.json` as a static
asset; `output/` remains gitignored and is not a runtime dependency.

## k6_mtf_ranking.json

- **Schema:** `k6_mtf_ranking_v2`
- **Run id:** `20260604T110400Z_recook_full248_clean_csv`
- **Generated at:** `2026-06-04T11:14:17Z`
- **Secondaries:** 205
- **Validation summary:** 88 board-validated, 117 not validated, 43 Stage-A
  excluded.
- **Public fixture SHA-256 / promotion source_sha256:**
  `b19a829794031be0e2674fb1c039aed8cdc95ffa063c9739bcdd2e631f6cb587`
- **Public fixture size:** 710,160 bytes in the working tree, well below the
  GitHub 100 MB per-file limit.

The committed fixture is slim. It keeps ranking metrics, K6 stack fields,
validation disclosure, and per-row Blob sidecar metadata. Inline
`ccc_series` is intentionally empty for every row.

## Full-Resolution CCC Sidecars

Full-resolution CCC series are stored off-repo as immutable public Vercel
Blob sidecars, one sidecar per secondary. The sidecars are not decimated,
truncated, sampled, or compressed in Git. Each fixture row embeds:

- `ccc_series_source="vercel_blob"`
- `ccc_series_sidecar_schema_version="k6_mtf_ccc_series_sidecar_v1"`
- `ccc_series_url`
- `ccc_series_pathname`
- `ccc_series_sha256`
- `ccc_series_byte_size`
- `ccc_series_points`
- first/last CCC dates

The live sidecar namespace is:

`k6-mtf/20260604T110400Z_recook_full248_clean_csv/ccc-series/`

Promotion recorded 205 GET-verified sidecars:

- **Total sidecar bytes:** 122,207,922
- **Largest sidecar bytes:** 2,857,925
- **Total CCC points:** 996,395
- **Allowed Blob URL host pattern:** `*.public.blob.vercel-storage.com`
- **Verification manifest:**
  `output/k6_mtf/20260604T110400Z_recook_full248_clean_csv/k6_mtf_ccc_sidecar_verification.json`
- **Verification manifest SHA-256:**
  `ed7651073e811a05caff4b5826729ddbdbfd93fff2e3c8cd0b8f5a8fe9979948`

The sidecars contain derived CCC fields only:
`date_utc`, `cumulative_capture_pct`, `per_bar_capture_pct`, and
`trade_direction`. They do not contain raw OHLCV, provider price series,
or credentials.

## Public-Promotion Provenance

- **Promotion manifest:**
  `frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json`
- **Manifest schema:** `k6_mtf_promotion_manifest_v1`
- **Promoted fixture schema recorded by manifest:** `k6_mtf_ranking_v2`
- **promoted_at_utc:** `2026-06-05T03:53:18Z`
- **promoted_by:** `the operator`
- **source_artifact_path:**
  `output/k6_mtf/20260604T110400Z_recook_full248_clean_csv/k6_mtf_ranking_v2_blob_sidecar_public_candidate.json`
- **operator_approval_marker:** `true`

The promotion helper verified the Phase 5 report, report manifest,
validation sidecar, CCC verification manifest, and slim v2 fixture before
writing the committed public fixture.

## Validation Binding

- **Phase 5 report:**
  `md_library/shared/2026-06-04_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_205.md`
- **Phase 5 report SHA-256:**
  `1f6e166c7f27dd09b430b4210a885ccebf997865bd3e921bb23e5579516d9c12`
- **Phase 5 report manifest:**
  `md_library/shared/2026-06-04_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_205.manifest.json`
- **Validation sidecar:**
  `output/validation/20260604T120000Z_validation_full205/validation.json`
- **Validation sidecar SHA-256:**
  `8e48fd56dc2c9f4f16598c2c01b71f2b87e691caf855b53c97fc704baf3871ef`
- **Validation run id:** `20260604T120000Z_validation_full205`
- **Methodology:** 10,000 permutations, 10,000 bootstrap samples,
  99 walk-forward folds, BH primary alpha 0.05, Bonferroni supplementary,
  bootstrap CI 0.95, validation/methodology contract v1, `rng_seed=null`.

Leaderboard ordering reflects K=6 MTF ranking metrics. Phase 5 validation
survivorship is disclosed per row and in the validation report; ranking
position is not a claim that a row cleared the validation gate.

## Phase 5G / Mode B Controls

Phase 5G is SATISFIED-BY-ACCEPTED-RISK under:

`md_library/shared/2026-06-01_PHASE_5G_2_OPERATOR_ACCEPTED_RISK_DECISION_RECORD.md`

This is operator accepted-risk documentation for the narrow Mode B
derived-only, non-commercial public surface. It is not legal clearance, and
this fixture does not claim legal clearance.

The binding Mode B controls remain:

- No raw OHLCV.
- No provider price charts.
- No provider price tables.
- No downloadable provider price series.
- No public raw-data API.
- No monetization while yfinance remains in the data pipeline.

Any change that breaches these controls reopens Phase 5G.

## How To Refresh

A future public refresh should use the gated promotion helper. The normal
flow is:

1. Produce or validate the candidate K=6 MTF v2 fixture.
2. Move full-resolution CCC into immutable per-secondary sidecars and write a
   CCC verification manifest.
3. Promote the slim fixture with
   `utils/react_publish/promote_k6_mtf_artifact.py --public --write --operator-approved`.
4. Re-run the fixture-schema smoke test and frontend validation.
5. Update this README with the new run id, SHA, sidecar totals, and
   promotion provenance.

The helper does not deploy and does not change React runtime behavior.
