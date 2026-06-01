# React MVP fixtures

This directory holds the committed JSON artifact the React MVP
consumes at runtime. The fixture is the public-promoted K=6 MTF
ranking artifact; the live `output/` artifact path is gitignored
and is NOT wired to the React app.

## k6_mtf_ranking.json

- **Schema:** `k6_mtf_ranking_v1`
- **Provenance:** byte-identical copy of the operator-authorized
  K=6 MTF ranking artifact at
  `output/k6_mtf/20260528T083411Z_post_fix/k6_mtf_ranking.json`.
- **Generated at (per artifact `generated_at_utc`):**
  `2026-05-28T21:16:41Z`.
- **Run id (per artifact `run_id`):** `20260528T083411Z`.
- **Secondaries:** AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY,
  TSLA (the 8 Tier 1 K=6 MTF MVP secondaries).
- **Source artifact SHA-256:**
  `cf716b0d1e5ea1d92afb30b6ebe85845a4e19ed276f5fe9f27c58be44f9a5dfa`.
  This fixture must match byte-for-byte; the fixture-schema
  smoke test at
  `project/test_scripts/shared/test_k6_mtf_fixture_schema.py`
  asserts shape; future PRs that refresh this fixture must
  also re-record the SHA in this README.

## Status

This fixture is the public-promoted K=6 MTF MVP board artifact
under Phase 5G-2 Mode B accepted-risk controls. The publish
step that points the React app at this artifact is the static
fixture URL at `fixtures/k6_mtf_ranking.json` resolved through
the configured Vite base; `loadArtifact.ts` is the single point
of swap if a future publish step substitutes a CDN-served
artifact URL.

## Public-promotion provenance

- **Promotion manifest:**
  `frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json`
  (operator_approval_marker=true; per_secondary_count=8;
  schema_version=k6_mtf_ranking_v1).
- **promoted_at_utc (per promotion manifest):**
  `2026-06-01T06:43:56Z`.
- **promoted_by (per promotion manifest):** `the operator`.
- **source_artifact_path (per promotion manifest):**
  `output/k6_mtf/20260528T083411Z_post_fix/k6_mtf_ranking.json`.
- **source_sha256 (per promotion manifest):**
  `cf716b0d1e5ea1d92afb30b6ebe85845a4e19ed276f5fe9f27c58be44f9a5dfa`.
- **Phase 5 honest-validation report (linked):**
  `md_library/shared/2026-06-01_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT.md`.
- **Phase 5 report SHA-256 (verified by promotion helper at
  write time):**
  `48efeb072c11a2abfe10eebfccde01604b74fd25f22392e414c8ab30a422e4bd`.
- **Phase 5G-2 operator accepted-risk decision record:**
  `md_library/shared/2026-06-01_PHASE_5G_2_OPERATOR_ACCEPTED_RISK_DECISION_RECORD.md`.
  Phase 5G-2 records the operator-authorized accepted-risk
  decision for the narrow Mode B derived-only non-commercial
  public surface. It is NOT legal clearance; counsel review
  remains pending.

## Mode B derived-only public surface

This fixture is part of the Phase 5G-2 Mode B derived-only
non-commercial public surface. The public surface controls are
binding:

- No raw OHLCV.
- No price charts.
- No price tables.
- No downloadable price series.
- No public raw-data API.
- No monetization while yfinance remains in the data pipeline.

Any change that breaches these controls reopens Phase 5G and
requires a dated amendment to the Phase 5G-2 record before the
change reaches the public surface.

## Validation disclosure

The K=6 MTF MVP board is a leaderboard ordered by K=6 MTF
performance metrics (Sharpe and total capture over the
per-secondary history window). Leaderboard position does NOT
reflect Phase 5 multiple-comparisons survivorship.

Of the 8 candidates tested in the Phase 5 honest-validation
empirical campaign, 4 cleared the Phase 5 Benjamini-Hochberg
plus empirical-permutation validation gate: AMZN, GOOGL, NVDA,
TSLA. AAPL, META, MSFT, SPY did not clear the BH gate; META
was outside the empirical subset (empirical_not_run). The
Phase 5 honest-validation report carries the per-strategy
verdicts; the leaderboard ordering reflects K=6 MTF ranking
metrics rather than per-row validation survivorship.

## Why this lives here and not in output/

- `output/` is gitignored at `<PROJECT_DIR>/.gitignore` and is
  local-only. A clean checkout, a CI deploy, or a fresh dev
  environment will not have the live artifact on disk.
- The React Migration Declaration's "artifact is the stable
  boundary" rule is satisfied: this fixture is a JSON-only,
  schema-stamped, read-only artifact identical in shape to the
  upstream K=6 MTF ranking artifact.
- The React app reads this file via static-asset URL, never via
  a Python call, never via a `output/` filesystem walk, never
  via raw signal-library / cache / PKL reads.

## How to refresh

When the operator authorizes a fresh K=6 MTF ranking artifact
(e.g., after a future K=6 MTF run is verified), a refresh PR
will:

1. Copy the new live artifact verbatim over this file.
2. Re-record the SHA-256 above.
3. Re-run the fixture-schema smoke test.
4. Update `generated_at_utc` / `run_id` notes here.
5. Re-run the promotion helper in public mode against the new
   Phase 5 honest-validation report path/SHA and re-record the
   public-promotion provenance above.

That PR does NOT change React app code unless the schema
itself changes; if the schema changes, the contract at
`project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
must be amended in the same chain.

The operator-run, stdlib-only helper at
`project/utils/react_publish/promote_k6_mtf_artifact.py`
implements steps 1-3 plus the PR #367 promotion-manifest
write under the React Publish / Deploy Contract Option A.
Dry-run by default. Fail-closed. Public mode hard-refuses
without a verified Phase 5 honest-validation report. The
helper does NOT deploy, does NOT mutate `output/`, and does
NOT change React runtime behavior.
