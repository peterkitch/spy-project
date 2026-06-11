# React MVP Fixtures

This directory holds the committed JSON artifact the React MVP consumes at
runtime. The React app reads `fixtures/k6_mtf_ranking.json` as a static
asset; `output/` remains gitignored and is not a runtime dependency.

## k6_mtf_ranking.json

- **Schema:** `k6_mtf_ranking_v2`
- **Run id:** `20260610T221108Z`
- **Generated at:** `2026-06-11T01:00:00Z`
- **Secondaries:** 207
- **Validation summary:** 90 board-validated, 117 not validated, 43 Stage-A
  excluded.
- **Public fixture SHA-256 / promotion source_sha256:**
  `6067d79b1c51a4d6dfef1b0673da3a0a728c130b5c7d09526b3fde6b6722e0cf`
- **Public fixture size:** 712,254 LF bytes, well below the
  GitHub 100 MB per-file limit.

This board is a composite carry-forward promotion: 205 carried rows retain
their prior validation verdicts, and 2 freshly validated rows (IHI, SCHG)
were added this run. The committed fixture is slim. It keeps ranking metrics,
K6 stack fields, validation disclosure, and per-row Blob sidecar metadata.
Inline `ccc_series` is intentionally empty for every row.

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

This is a **mixed-prefix carry-forward board**: carried sidecars keep their
original build-run namespace and fresh sidecars use this run's namespace, so
there is no single sidecar prefix. The promotion manifest records
`sidecar_prefix: null` and an itemized `sidecar_prefixes` list:

- 205 carried sidecars under
  `k6-mtf/20260604T110400Z_recook_full248_clean_csv/ccc-series/`
- 2 fresh sidecars (IHI, SCHG) under
  `k6-mtf/20260610T221108Z/ccc-series/`

Promotion recorded 207 GET-verified sidecars:

- **Total sidecar bytes:** 123,328,431
- **Largest sidecar bytes:** 2,857,925
- **Total CCC points:** 1,005,575
- **Allowed Blob URL host pattern:** `*.public.blob.vercel-storage.com`
- **Verification manifest:**
  `output/crunch_runs/20260610T221108Z/publish_candidate_samerun_ccc/combined_ccc_sidecar_verification.json`
- **Verification manifest SHA-256:**
  `dfa4edfc16b0356b519faea15a416c32f8e4be5b484760a74927d38deeee6bdd`

The sidecars contain derived CCC fields only:
`date_utc`, `cumulative_capture_pct`, `per_bar_capture_pct`, and
`trade_direction`. They do not contain raw OHLCV, provider price series,
or credentials.

## Public-Promotion Provenance

- **Promotion manifest:**
  `frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json`
- **Manifest schema:** `k6_mtf_promotion_manifest_v1`
- **Promoted fixture schema recorded by manifest:** `k6_mtf_ranking_v2`
- **promoted_at_utc:** `2026-06-11T02:24:26Z`
- **promoted_by:** `the operator`
- **source_artifact_path:**
  `output/crunch_runs/20260610T221108Z/publish_candidate_samerun_ccc/merged_k6_mtf_ranking_v2.json`
- **operator_approval_marker:** `true`

The promotion helper verified the Phase 5 report, report manifest,
validation sidecar, CCC verification manifest, and slim v2 fixture before
writing the committed public fixture.

## Validation Binding

- **Phase 5 report:**
  `md_library/shared/2026-06-11_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md`
- **Phase 5 report SHA-256:**
  `9c975f4ebc3587d8bb72028d866d7ee9494d684ba1f449a491ad6eca12a9499c`
- **Phase 5 report manifest:**
  `md_library/shared/2026-06-11_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.manifest.json`
- **Validation sidecar:**
  `output/crunch_runs/20260610T221108Z/publish_candidate_samerun_ccc/composite_validation_sidecar.json`
- **Validation sidecar SHA-256:**
  `9ac6ac6349fa54994a50f894675bcb9bdc058538641ae0758b18e63e4574f499`
- **Validation run id:** `20260610T221108Z`
- **Methodology:** 10,000 permutations, 10,000 bootstrap samples,
  BH primary alpha 0.05, Bonferroni supplementary, bootstrap CI 0.95,
  validation/methodology contract v1, `rng_seed=null`.
  `walk_forward_n_folds` is composite/advisory and recorded as `null`: the
  fold count is data-derived per validation cohort (carried and fresh cohorts
  span different histories), so no single board-wide fold count is asserted.

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
