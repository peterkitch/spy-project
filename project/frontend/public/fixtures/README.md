# React MVP fixtures

This directory holds the committed JSON artifact the React MVP
consumes at runtime. The first React PR is fixture-driven; the
live `output/` artifact path is gitignored and is NOT wired to
the React app.

## k6_mtf_ranking.json

- **Schema:** `k6_mtf_ranking_v1`
- **Provenance:** byte-identical copy of the operator-authorized
  live artifact at
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
  asserts shape; future PRs that refresh this fixture should
  also re-record the SHA in this README.

## Status

This is a **representative artifact for first-React-PR
development**. It is **NOT the published production artifact**.
The publish step that would point the React app at a real
served artifact URL is deferred per the React Migration
Declaration ("publish step is deferred and not specified
here"). When a real publish step lands, the React app's
`loadArtifact.ts` fetch URL is the single point of swap.

## Why this lives here and not in output/

- `output/` is gitignored at `<PROJECT_DIR>/.gitignore` and is
  local-only. A clean checkout, a CI deploy, or a fresh dev
  environment will not have the live artifact on disk.
- The React Migration Declaration's "artifact is the stable
  boundary" rule is satisfied: this fixture is a JSON-only,
  schema-stamped, read-only artifact identical in shape to the
  live ranking artifact.
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

That PR does NOT change React app code unless the schema
itself changes; if the schema changes, the contract at
`project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
must be amended in the same chain.
