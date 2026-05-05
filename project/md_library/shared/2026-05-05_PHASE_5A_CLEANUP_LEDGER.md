# Phase 5A Cleanup Ledger

**Date:** 2026-05-05

**Status:** Locked classification; Phase 5B PRs reference this ledger for batching and preflight scope

**Author:** PRJCT9 sprint

**Phase:** 5A

## Purpose

Phase 5A is a triage step that classifies every cleanup item destined for Phase 5B (or a dedicated sub-phase) so Phase 5B PRs are auditable in advance. The Phase 5 Pre-Flight (`2026-05-05_PHASE_5_PRE_FLIGHT.md`) states the locked rules: anything that deletes behavior, changes CLI surface, touches environment files, or changes cross-app semantics requires its own preflight; the tiny-cleanup escape hatch applies only when NONE of those properties hold.

This ledger fills in classification per item. It does NOT delete behavior, change CLI, or modify any code. The companion deliverable in this PR is the surgical CLAUDE.md sprint-state drift fix.

## How to read each entry

Each item carries the field set required by the Phase 5A scope:

- **Source files** — the modules implicated.
- **Current behavior** — one sentence on what's there now (read from the actual code state at HEAD).
- **Intended outcome** — what the cleanup achieves.
- **Classification** — one of: `delete` / `deprecate-with-warning` / `rename` / `behavior-change` / `cross-app-reconciliation` / `doc-only`.
- **Code touch required** — yes / no.
- **Preflight required for 5B** — yes / no, per the Phase 5 Pre-Flight rule.
- **PR batching recommendation** — single dedicated PR / batched with named items / dedicated sub-phase.
- **Tests that pin the change** — existing tests that must continue to pass + any new tests needed.
- **Risk notes** — anything an operator should know before drafting the 5B PR.

## Item 1 — StackBuilder vestigial CLI pruning

- **Source files:** `project/stackbuilder.py` (argparse block at ~lines 2382–2449).
- **Current behavior:** `parse_args` exposes a long flag list including arguments whose runtime use is now narrow or absent (e.g., `--allow-decreasing`, `--exhaustive-k`, `--both-modes`, `--k-patience`, `--save-stats`, `--serve`, `--port`, `--min-marginal-capture`, the optional `--optimize-by` distinct from `--seed-by`). Some of these are still wired through; others are residual from earlier search modes. A small subset are actually used in production runs.
- **Intended outcome:** Identify which CLI flags are still load-bearing vs vestigial; remove or deprecate the vestigial ones with a deprecation warning so existing operator scripts keep working through one release cycle.
- **Classification:** `deprecate-with-warning` (preferred over outright `delete` because operator scripts may still pass these flags).
- **Code touch required:** yes.
- **Preflight required for 5B:** **yes** — this changes CLI surface (the locked rule explicitly names "changes CLI surface" as preflight-triggering).
- **PR batching recommendation:** single dedicated PR. Each removed/deprecated flag should be enumerated in the PR description so the audit trail is explicit.
- **Tests that pin the change:** existing `test_within_engine_parity.py` and `test_provenance_manifest.py` exercise StackBuilder via SimpleNamespace args; they don't go through argparse. New CLI-level test (small) should pin the deprecation-warning text for each flag and confirm the kept flags still parse cleanly.
- **Risk notes:** automation scripts (e.g., LAUNCH_*.bat / scheduled jobs) may reference the deprecated flags; the preflight scope should call out a search across `*.bat`, scheduled jobs, and md_library docs for any flag references before deletion is final.

## Item 2 — TrafficFlow disabled-matrix code removal

- **Source files:** `project/trafficflow.py:2362, 2795–2806`. Other `_members_signals_df_and_returns` / `_averages_via_matrix` helpers in the same file are part of the same disabled path.
- **Current behavior:** The matrix alternative path is hard-disabled with `if False and TF_MATRIX_PATH and ...:` at line 2797 and an inline comment "Matrix path hard-off (kept only as commented reference)" at line 2796. The dead branch and its supporting helpers (`_members_signals_df_and_returns`, `_averages_via_matrix`) remain in the file. The fallback per-subset path at line 2807 is the actual production path.
- **Intended outcome:** Delete the hard-off matrix branch + its supporting helpers, leaving the per-subset fallback as the only path. Keep a one-line ledger reference to the historical matrix experiment.
- **Classification:** `delete`.
- **Code touch required:** yes.
- **Preflight required for 5B:** **yes** — this deletes behavior (even dead behavior, since `TF_MATRIX_PATH` env-flag handling is part of the surface today).
- **PR batching recommendation:** single dedicated PR. Should NOT batch with Item 4 (matrix concept naming) — the disabled-matrix removal is a TrafficFlow-only delete, while naming consolidation crosses modules.
- **Tests that pin the change:** existing `test_lookahead_guards.py` static guards already exclude TrafficFlow's matrix helpers from B8 (negative-shift) by allowlist position. A static guard test that asserts `TF_MATRIX_PATH` no longer appears in `trafficflow.py` would pin the deletion.
- **Risk notes:** confirm `TF_MATRIX_PATH` and `TF_MATRIX_MAX_K` are not referenced from any `.bat` launcher or env-setup doc before removal.

## Item 3 — Spymaster Help UI matrix.py reference fix

- **Source files:** `project/spymaster.py:5427–5436, 5487–5497` (Help modal: Quick Start "Step 3: Test Multi-Primary Effects (matrix.py)" card and Workflow Guide "Phase 3: Multi-Primary (matrix.py, under development)" accordion item).
- **Current behavior:** Help UI references a `matrix.py` module that does not exist anywhere in the codebase (no file by that name in `project/` or any subdirectory). The user-visible Help text labels Step 3 / Phase 3 with a parenthetical "(matrix.py, under development)" that misleads operators. The actual multi-primary functionality is implemented inside spymaster.py / impactsearch.py / confluence.py — not in a standalone `matrix.py`.
- **Intended outcome:** Replace the dangling `matrix.py` references in the Help UI with accurate language pointing operators to the real multi-primary surface (whichever module 5B/Item 10 lands on as the canonical home).
- **Classification:** `doc-only` (the Help UI text is operator-facing documentation embedded in code; no code logic changes).
- **Code touch required:** yes (string edits in spymaster.py).
- **Preflight required for 5B:** **no** — single-file edit; no behavior deletion, no public CLI change, no environment-contract change, no cross-app semantic impact. Qualifies for the tiny-cleanup escape hatch.
- **PR batching recommendation:** **batched** with Item 10 (multi-primary reconciliation) so the Help-text update reflects the reconciled module location/wording. If Item 10 is dedicated to its own sub-phase, this Help-text fix should land in that sub-phase rather than in 5B-proper.
- **Tests that pin the change:** new tiny static test asserting the string "matrix.py" does not appear in spymaster.py's Help UI accordion content (search-and-assert).
- **Risk notes:** none — purely cosmetic operator UI text. The "Phase 3 / Phase 4" labels in this Help UI are SPYMASTER feature-development labels (not PRJCT9 sprint phases) and should be left as-is unless Item 10 reconciliation explicitly renames them.

## Item 4 — Matrix concept naming consolidation across modules

- **Source files:** `project/trafficflow.py` (matrix path internals — overlaps with Item 2), `project/spymaster.py` (Help UI references — overlaps with Item 3), and any md_library docs that reference "matrix" as a concept.
- **Current behavior:** "Matrix" is used inconsistently: TrafficFlow internals use it for a vectorized scoring path, Spymaster's Help UI uses it for the imaginary `matrix.py` multi-primary tool, and several md_library docs reference a "matrix" concept that doesn't have a single owner.
- **Intended outcome:** Decide on a single canonical use of "matrix" terminology (or eliminate it as a concept) and update references consistently. The Phase 5 Pre-Flight already routes any cross-app semantic decisions through preflight.
- **Classification:** `cross-app-reconciliation`.
- **Code touch required:** yes (rename / wording edits across at least 2 modules).
- **Preflight required for 5B:** **yes** — explicit cross-app semantic reconciliation; the locked rule names this as preflight-triggering.
- **PR batching recommendation:** single dedicated PR after Items 2 and 3 land. Sequencing matters: TrafficFlow's matrix code must be deleted (Item 2) before naming consolidation can proceed, and Spymaster Help UI fix (Item 3) should reflect the consolidation decision.
- **Tests that pin the change:** new static guard test asserting the chosen canonical "matrix" usage (or absence thereof) across `production_python_files()` and md_library docs.
- **Risk notes:** could surface md_library docs that reference matrix functionality no longer in the code. Those docs should be flagged for archival rather than rewritten in this PR.

## Item 5 — environment.yml / requirements.txt env hygiene

- **Source files:** `project/environment.yml`, `project/requirements.txt`.
- **Current behavior:** Per `project/CLAUDE.md` §1 (pinned interpreter), `environment.yml` and `requirements.txt` carry aspirational pins (newer NumPy/pandas) that diverge from the actual `spyproject2` runtime (Python 3.12.2, NumPy 1.26.4 MKL, pandas 2.2.1, SciPy 1.13.1, pytest 8.3.5). Recreating the env from these files is documented as unsafe without explicit revalidation.
- **Intended outcome:** Bring `environment.yml` and `requirements.txt` into alignment with the actual `spyproject2` runtime (or document the intentional divergence with explicit revalidation steps), so a fresh contributor can recreate the env and pass the audit suite.
- **Classification:** `behavior-change` (env-spec change is operationally a behavior change for new contributors).
- **Code touch required:** no (config file edits only) — but the env files are explicitly in the locked rule's "touches env files" trigger list.
- **Preflight required for 5B:** **yes** — explicit "touches env files" trigger.
- **PR batching recommendation:** single dedicated PR. Should include a documented revalidation step (run the full regression suite under the updated env) before merge.
- **Tests that pin the change:** the existing 288-test regression suite must pass under the updated env. Optionally pin the runtime version capture in `provenance_manifest._capture_package_versions` against the env-file pins so future drift fails loudly.
- **Risk notes:** the locked baseline-snapshot tests (`test_phase1a_baseline_lock.py`) depend on the specific runtime stack; an env-file change that bumps NumPy/SciPy could re-trigger the baseline-lock contract. The preflight must explicitly call out whether the baseline is rebaselined or kept frozen against the runtime.

## Item 6 — B11 compute_signals cleanup

- **Source files:** `project/spymaster.py:4335` (`def compute_signals`), `project/test_scripts/test_lookahead_guards.py:208–264` (B11 static guard).
- **Current behavior:** `spymaster.compute_signals` is a same-day SMA-comparison function flagged by Phase 1B as having a shift-correctness question. It is currently uncalled (B11 static guard `test_b11_spymaster_compute_signals_uncalled` enforces zero call sites), but the function body remains. The Phase 1B-INTENTIONAL-DELTA-LEDGER and prior CLAUDE.md noted this as deferred to Phase 3; Phase 3 closed without resolving it; the Phase 4 Pre-Flight re-deferred to Phase 5.
- **Intended outcome:** Delete `compute_signals` outright. The B11 static guard already covers the "function removed" case via `pytest.skip` at `test_lookahead_guards.py:229`, so removal is the lower-risk path that preserves spymaster.py's standalone-baseline role.
- **Classification:** `delete`.
- **Code touch required:** yes.
- **Preflight required for 5B:** **yes** — deletes behavior (even dead-coded behavior); explicit preflight trigger.
- **PR batching recommendation:** single dedicated PR. Should NOT batch with Item 1 (StackBuilder CLI) or Item 2 (TrafficFlow matrix delete) — B11 is a Spymaster regression-baseline concern with its own static-guard test contract.
- **Tests that pin the change:** `test_b11_spymaster_compute_signals_uncalled` already pins the current "uncalled" state. After deletion the guard auto-skips via the `"removed; this guard is no longer applicable"` branch (line 229).
- **Risk notes:** Delete `compute_signals` unless the 5B preflight produces explicit rationale to amend this ledger to `behavior-change` and shift-correct it. Spymaster.py's standalone-baseline role (per CLAUDE.md "Spymaster.py Standalone Design") is preserved by removing dead code. A future shift-correct rewire — if the preflight chooses that path via amendment — would require a re-baseline of Phase 1A snapshots once `compute_signals` enters any active call site.

## Item 7 — OnePass error UX (deferred from Post Phase 3 Sprint Bug Cleanup audit)

- **Source files:** `project/onepass.py` (broad — error handling sites scattered through the engine).
- **Current behavior:** OnePass currently has 51 `except Exception | return None` patterns where errors are swallowed and surfaced as silent absences (e.g., a failed library load returns None without a structured rejection reason). Operators see "no library available" without knowing whether it was missing, corrupted, manifest-mismatched, or rate-limited.
- **Intended outcome:** Replace selected swallow-and-None error sites with structured rejection results (the same pattern Phase 3B-2B introduced for StackBuilder's `try_load_rank_from_impact_xlsx` `rejection_out` dict) so operators get actionable feedback.
- **Classification:** `behavior-change` (changes user-visible error messaging and may add new error types to OnePass's surface).
- **Code touch required:** yes.
- **Preflight required for 5B:** **yes** — changes user-visible error messaging counts as behavior change.
- **PR batching recommendation:** single dedicated PR. Should NOT batch with Item 9 (ImpactSearch error taxonomy) — the per-engine error surfaces have different shapes and should be reviewed independently.
- **Tests that pin the change:** new tests in the spirit of `test_stackbuilder_stale_xlsx_message.py` — for each affected error site, a fixture that triggers the failure and asserts the new structured message.
- **Risk notes:** OnePass is consumed by ImpactSearch / Spymaster / Confluence / Phase 4A; new error messaging shouldn't change exception types in ways that break existing callers' try/except blocks. The preflight should enumerate every consumer.

## Item 8 — TrafficFlow refresh callback (deferred from Post Phase 3 Sprint Bug Cleanup audit)

- **Source files:** `project/trafficflow.py` (price-refresh callback path; specific lines to be enumerated in the 5B preflight).
- **Current behavior:** TrafficFlow has a price-refresh callback path that swallows errors silently. The Post Phase 3 Sprint Bug Cleanup audit (PR #145) explicitly listed this as a deferred item.
- **Intended outcome:** Surface refresh callback errors as visible operator-facing messages (similar shape to Item 7 but TrafficFlow-specific).
- **Classification:** `behavior-change`.
- **Code touch required:** yes.
- **Preflight required for 5B:** **yes** — changes user-visible error messaging.
- **PR batching recommendation:** single dedicated PR. May batch with Item 2 (TrafficFlow disabled-matrix removal) since both target the same module — but only if 5B preflight confirms the two changes don't conflict on touched lines.
- **Tests that pin the change:** new test that injects a refresh failure and asserts the operator-visible error surface.
- **Risk notes:** TrafficFlow has user-facing Dash callbacks; an error-message change can ripple into Selenium tests if any exist. Preflight should enumerate test coverage.

## Item 9 — ImpactSearch error taxonomy (deferred from Post Phase 3 Sprint Bug Cleanup audit)

- **Source files:** `project/impactsearch.py` (broad — 48 `except Exception | return None | raise RuntimeError | raise ValueError` sites).
- **Current behavior:** ImpactSearch error handling is a mix of `raise ValueError`, `raise RuntimeError`, swallowed `except Exception` blocks, and silent `return None`. There is no consistent error taxonomy; operators see different shapes for similar failures.
- **Intended outcome:** Define a small error taxonomy (e.g., `ImpactSearchInputError`, `ImpactSearchSourceError`, `ImpactSearchRateLimitError`) and migrate the existing error sites to raise the appropriate type with structured context. Caller-visible behavior should preserve backwards compatibility (existing `except (ValueError, RuntimeError)` blocks should still catch the new types).
- **Classification:** `cross-app-reconciliation` (taxonomy adoption affects how ImpactSearch is consumed by Spymaster's "Open Impact Search" link, by StackBuilder's XLSX fast-path, and by Confluence).
- **Code touch required:** yes.
- **Preflight required for 5B:** **yes** — cross-app semantic change.
- **PR batching recommendation:** single dedicated PR after Item 7 (OnePass error UX) lands, so the structured-error pattern is precedent. Should NOT batch with Item 7 — different engines, different consumer surfaces.
- **Tests that pin the change:** for each new exception type, a fixture that triggers the failure path and asserts the type + structured-context fields.
- **Risk notes:** ImpactSearch's XLSX fast-path is consumed by StackBuilder; new exception types must subclass `ValueError` or `RuntimeError` (or whatever StackBuilder catches today) so existing call sites continue to handle them.

## Item 10 — Multi-primary mode reconciliation across Spymaster / ImpactSearch / Confluence

- **Source files:** `project/spymaster.py` (multi-primary signal aggregator + Help UI references), `project/impactsearch.py` (multi-primary surface), `project/confluence.py` (multi-primary code paths). Reference docs: `md_library/spymaster/2025-08-28_MULTI_PRIMARY_SIGNAL_AGGREGATOR_FIX.md`, `md_library/confluence/2025-10-22_MULTI_PRIMARY_SIGNAL_AGGREGATOR_IMPLEMENTATION.md`.
- **Current behavior:** "Multi-primary" semantics differ across the three apps. Spymaster's Multi-Primary Signal Aggregator is one user-facing surface; ImpactSearch accepts multiple primary tickers and evaluates them independently against secondary ticker returns; that is a different shape from Spymaster/Confluence multi-primary aggregation. Confluence implements multi-primary signal aggregation with yet a third shape (per the 2025-10-22 implementation doc). The three apps don't share a canonical multi-primary contract.
- **Intended outcome:** Define a canonical multi-primary contract (input shape, signal-aggregation rule, output shape) and reconcile all three apps against it. The Phase 5 Pre-Flight explicitly defers to 5A whether this batches into 5B or warrants its own dedicated sub-phase.
- **Classification:** `cross-app-reconciliation`.
- **Code touch required:** yes (substantial — three modules).
- **Preflight required for 5B:** **yes** — explicit cross-app reconciliation, by far the largest item in this ledger by scope and risk.
- **PR batching recommendation:** **dedicated sub-phase**, proposed name **Phase 5B-MP** (Multi-Primary reconciliation). Rationale below.
- **Tests that pin the change:** new parity suite asserting all three apps produce equivalent multi-primary outputs for the same inputs (analogous to the Phase 2B-2A within-engine and cross-engine parity suites). Existing `test_within_engine_parity.py` and `test_cross_engine_parity.py` set the precedent.
- **Risk notes:** see rationale below.

### Multi-primary recommendation: dedicated Phase 5B-MP sub-phase

**Recommendation:** Item 10 should NOT batch into Phase 5B alongside the smaller cleanups. It warrants its own dedicated sub-phase **Phase 5B-MP** with its own preflight scoping, for the following reasons:

1. **Scope.** The other 9 ledger items each touch at most 2 modules. Item 10 touches 3 production modules (`spymaster.py`, `impactsearch.py`, `confluence.py`) plus their associated test suites and at least 2 md_library implementation docs. By line count it's likely larger than several other items combined.

2. **Cross-app semantic divergence is real, not cosmetic.** The 2025-08-28 Spymaster fix doc and the 2025-10-22 Confluence implementation doc describe different aggregation shapes — this isn't a rename, it's a behavior reconciliation. Each of the three current behaviors has consumers; collapsing to one canonical shape requires explicit per-app deprecation paths.

3. **Parity-test infrastructure.** A reconciliation PR needs a parity suite analogous to `test_within_engine_parity.py` / `test_cross_engine_parity.py`. Building that infrastructure is itself non-trivial and should not be hidden inside a "5B cleanup" PR.

4. **Audit trail.** A dedicated sub-phase mirrors how Phase 4A/4B and Phase 5 itself are structured: locked preflight document → implementation PR → audit. The Phase 5 Pre-Flight's per-sub-phase preflight rule applies cleanly.

5. **Risk isolation.** If 5B-MP runs into unexpected blockers, batching it with smaller cleanups would block all of 5B. Isolating it preserves 5B's other work.

**Phase 5B-MP preflight scoping (suggested):**

- Define the canonical multi-primary contract (input shape, aggregation rule, output shape) before any code change.
- Audit each of the three apps' current multi-primary behavior against the canonical contract; classify each per-app divergence as `keep / migrate / deprecate`.
- Define a parity-suite test plan that pins canonical behavior across all three apps.
- Decide which app owns the canonical implementation; reconcile the other two against it.
- Update spymaster.py Help UI (Item 3 dependency) to reflect the reconciled multi-primary surface.
- 5B-MP should run AFTER Items 1–9 land so the cleaner code state simplifies the reconciliation.

## Document status

**Locked.** Phase 5B PRs reference this ledger for batching and preflight scope. Any changes to this document require explicit amendment with date and rationale.
