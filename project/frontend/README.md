# PRJCT9 Daily Signal Board - K=6 MTF (React MVP)

First React cut. Greenfield Vite + React 18 + TypeScript app
that reproduces the read-only rendering of the Dash K=6 MTF
board (`mvp_signal_board.py`) against a committed representative
`k6_mtf_ranking_v1` artifact.

## Status

- **First React MVP. Fixture-driven. Not yet deployed.**
- The Dash board (`mvp_signal_board.py`) remains the operator
  cockpit and the prototype-of-record. The React app and the
  Dash app coexist during transition. Cutover requires
  operator-declared behavioral parity per the React Migration
  Declaration.
- The publish step that would point at a live served artifact
  URL is deferred. This app reads only the committed fixture at
  `public/fixtures/k6_mtf_ranking.json`.

## Architecture

- Vite + React 18 + TypeScript. No SSR, no Next.js, no state
  library, no routing, no charting library, no CSS framework.
- Detail surface is a centered modal (matches Dash for clean
  parity-checking).
- CCC chart is a hand-rolled inline SVG step plot. No Plotly,
  no Recharts.
- Artifact-boundary only: the app reads exactly one JSON
  artifact at runtime and never calls Python, never recomputes
  any metric, never sign-flips, never derives BUY/SHORT, and
  never reads raw signal libraries / caches / PKLs / Phase E
  artifacts. See the React Migration Declaration "Forbidden
  Behaviors" section.

## Data source

- The committed fixture at
  `public/fixtures/k6_mtf_ranking.json` is a verbatim copy of
  the operator-authorized live artifact at
  `output/k6_mtf/20260528T083411Z_post_fix/k6_mtf_ranking.json`
  (dated 2026-05-28).
- See `public/fixtures/README.md` for fixture provenance and
  the non-production label.
- The live `output/` path is gitignored and is NOT wired to the
  React app at runtime.

## Local development

From `<PROJECT_DIR>/frontend/`:

```
npm install
npm run dev
```

- Dev server: Vite default port (5173 unless overridden).
- The board fetches `/fixtures/k6_mtf_ranking.json` on load.

## Build

```
npm run typecheck
npm run build
```

- `npm run build` runs `tsc --noEmit` then `vite build`. The
  static bundle lands in `dist/` and is NOT committed.

## Behavioral parity reference

The Dash K=6 MTF surface lives in `project/mvp_signal_board.py`
(K=6 MTF schema dispatch, `_K6_MTF_BOARD_COLUMNS`,
`_k6_mtf_board_columns_for_visible`, `_format_k6_mtf_sharpe`,
`render_k6_mtf_modal_content`, etc.). The contract that binds
both surfaces is
`project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`.

The PR #364 Status-column hide is reproduced: when every visible
ranked row has `status` in the set `{null, "", "ranked"}`, the
Status column is omitted from the primary table. Status remains
in the modal regardless.

## Out of scope for the first React PR

- Tier 2 growth-queue display.
- Live-output / publish-step wiring (the publish step itself is
  deferred per the React Migration Declaration).
- Deployment to a CDN / hosting platform; CI workflow.
- Authentication.
- Routing / permalinks to a specific ticker's detail surface.
- Charting library replacement (hand-rolled SVG is intentional).
- Auto-refresh / background polling.
- Mobile-responsive design beyond "page loads".
- Accessibility audit beyond keyboard-accessible click handlers.
- Internationalization, dark mode, theming.
- Any change to `mvp_signal_board.py` or any other Python
  source other than the new fixture-schema smoke test under
  `project/test_scripts/shared/`.
