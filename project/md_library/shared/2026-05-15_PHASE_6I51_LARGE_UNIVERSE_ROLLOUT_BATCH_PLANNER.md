# Phase 6I-51: Large-universe rollout batch planner + board preview command manifest

**Date:** 2026-05-15
**Base commit (main):** `c668922` (Phase 6I-50 squash-merge)
**Branch:** `phase-6i-51-large-universe-rollout-batch-planner`
**Status:** Read-only planner. No production writes. **Do not merge** until operator approval.

---

## 1. Purpose

Phase 6I-50 closed the *classification* surface: every ticker in the universe gets a per-axis state (artifact / cache / signal-library / StackBuilder) plus a stable `recommended_next_action` code. Phase 6I-51 closes the *next-step* surface: it converts that per-ticker action code into the **exact candidate command** the operator would run next, grouped into seven rollout-batch categories, each command tagged with an `authorization_class` and (where applicable) a `policy_basis`.

The module is the bridge between "what state is every ticker in?" (6I-50) and "what exact command would move each ticker forward?" (6I-51). It does NOT run any candidate command, does NOT write to any production root, and does NOT invoke yfinance / the source-cache refresher / the stable-promotion writer / the Confluence patch writer / the pipeline runner / StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster.

## 2. What was added

### Module

`project/confluence_large_universe_rollout_batch_planner.py`

- Public entry `build_rollout_batch_plan(launch_plan, *, accept_proposed_stackbuilder_defaults, invalid_members_json_path, artifact_root, cache_dir, status_dir, signal_library_dir, stackbuilder_root, current_as_of_date) -> dict`.
- CLI with two universe-input modes (mutually exclusive):
  - `--planner-json <path>`: consume a saved Phase 6I-50 launch-planner JSON evidence file.
  - `--tickers` / `--all-artifacts` / `--from-stackbuilder-universe` / `--universe-file`: invoke the Phase 6I-50 planner inline.
- Seven rollout-batch categories (`ALL_BATCHES`): `board_render_now`, `partial_artifact_write_candidates`, `strict_artifact_write_candidates`, `source_refresh_candidates`, `signal_library_rebuild_or_promotion_candidates`, `stackbuilder_rerun_candidates`, `blocked_or_manual_review`.
- Per-command `authorization_class` taxonomy: `read_only`, `source_cache_write`, `confluence_artifact_write`, `signal_library_promotion_write`, `stackbuilder_write`, `manual_review`.
- StackBuilder policy gate via `--accept-proposed-stackbuilder-defaults`: when absent, stackbuilder rerun candidates carry `blocked_by_policy_decision=true` + `policy_basis="unresolved_questions"` + `operator_policy_required=true`; when present, those flip to `false` / `"proposed_defaults"` / `false`. **Even when the flag is set, the planner STILL does not execute any StackBuilder command** — the flag only adjusts the tag on the candidate record.
- `--output` / `--emit-shell-script` paths are guarded against landing inside any of the five documented production roots (`cache/results`, `cache/status`, `output/research_artifacts`, `output/stackbuilder`, `signal_library/data/stable`).
- Optional `--emit-shell-script` writes a shell-script preview where every candidate command is **commented out by default**.

### Tests

`project/test_scripts/test_confluence_large_universe_rollout_batch_planner.py` — 15 focused tests, all passing under the pinned interpreter:

1. Schema-version + batch + authorization-class + policy-basis taxonomies are stable.
2. `already_board_ranked` → `board_render_now` (SPY case).
3. `daily_only` / `blocked_missing_inputs` → `blocked_or_manual_review` (_GSPC case).
4. `write_partial_artifact` → partial writer candidate with `--allow-partial-payload-plan` + `requires_separate_operator_authorization=True`.
5. `write_strict_artifact` → strict writer candidate (no `--allow-partial-payload-plan`).
6. `refresh_source_cache` → per-ticker `signal_engine_cache_refresher.py --ticker <T>` (NOT CSV — pins that the comma-separated form never leaks in).
7. `rerun_stackbuilder` → `stackbuilder.py --secondary <TICKER>` (NOT `--ticker` — pins the Phase 6I-50-amendment-1 corrected entry flag); `--combine-mode intersection` explicitly surfaced; `--both-modes` NOT auto-added.
8. StackBuilder rerun is `blocked_by_policy_decision=True` by default; `--accept-proposed-stackbuilder-defaults` flips it.
9. Every generated command's `command` field starts with the pinned interpreter.
10. Static guard: no `subprocess` (or other forbidden) top-level imports.
11. `--output` + `--emit-shell-script` reject paths inside every production root.
12. Batch counts sum to inspected count + per-batch ticker distribution is stable.
13. Unresolved policy questions carry through from input launch plan (6 entries).
14. CLI `--planner-json` round-trips a saved Phase 6I-50 evidence file.
15. `--emit-shell-script` writes a script with every command commented out.

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m pytest \
    test_scripts/test_confluence_large_universe_rollout_batch_planner.py \
    test_scripts/test_confluence_large_universe_launch_planner.py -q
... 31 passed in 1.46s
```

(15 Phase 6I-51 + 16 Phase 6I-50 tests confirmed green together.)

## 3. Strict read-only contract

The module:

- Has zero `subprocess` / `os.system` / `exec` / network surface. The static guard test 10 (`test_no_forbidden_top_level_imports`) pins this against `subprocess`, `yfinance`, `dash`, `signal_engine_cache_refresher`, `signal_library_stable_promotion_writer`, `multiwindow_k_confluence_patch_writer`, `confluence_pipeline_runner`, `daily_board_automation_writer`, `daily_board_automation_executor`, `spymaster`, `trafficflow`, `stackbuilder`, `onepass`, `impactsearch`, `confluence`, `cross_ticker_confluence`, `daily_signal_board`.
- Reads disk only via the optional `--planner-json` JSON load + (when invoked inline) the Phase 6I-50 launch planner's existing disk-only probes.
- Writes disk only via `--output` (rollout JSON) and `--emit-shell-script` (commented-out shell preview), both of which are explicitly guarded against landing inside any production root.
- Has no `PRJCT9_AUTOMATION_WRITE_AUTH` reference.

The candidate commands carry `--write` in their argv strings where they refer to write-capable scripts (`multiwindow_k_confluence_patch_writer.py`, `signal_engine_cache_refresher.py`, `signal_library_stable_promotion_writer.py`), but **the planner emits these as documentation strings only**. The two-key authorization (`--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`) is the operator's responsibility at invocation time, and the operator MUST run those commands in a separate, explicitly authorized session.

## 4. Production-roots evidence pass

The CLI was exercised against production with `--all-artifacts`:

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
    confluence_large_universe_rollout_batch_planner.py \
    --all-artifacts \
    --artifact-root output/research_artifacts \
    --cache-dir cache/results \
    --status-dir cache/status \
    --signal-library-dir signal_library/data/stable \
    --stackbuilder-root output/stackbuilder \
    --output md_library/shared/2026-05-15_PHASE_6I51_ROLLOUT_BATCH_PLAN_EVIDENCE.json
```

Universe inspected: 2 tickers (matching the Phase 6I-50 evidence). **Per-ticker rollout verdict:**

| Ticker | Phase 6I-50 action | Phase 6I-51 batch | Commands emitted |
|---|---|---|---|
| `SPY` | `already_board_ranked` | `board_render_now` | 2 read-only commands (`static_board_render`, `website_export_package`) |
| `_GSPC` | `blocked_missing_inputs` | `blocked_or_manual_review` | 1 manual-review record (no argv, comment-only `command`) |

**Aggregate counts:**
```
input_inspected = 2
manifest_count  = 3
batch_summary:
  board_render_now                                  = 1
  partial_artifact_write_candidates                 = 0
  strict_artifact_write_candidates                  = 0
  source_refresh_candidates                         = 0
  signal_library_rebuild_or_promotion_candidates    = 0
  stackbuilder_rerun_candidates                     = 0
  blocked_or_manual_review                          = 1
unresolved_policy_questions = 6 (carried through from Phase 6I-50)
```

**Production-roots untouched:**

| Root | Pre-run | Post-run | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5228 | 5228 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |

The evidence JSON lands at `md_library/shared/2026-05-15_PHASE_6I51_ROLLOUT_BATCH_PLAN_EVIDENCE.json` — outside every production root.

## 5. Generated candidate command examples (per non-empty batch in production)

### batch_1: `board_render_now` — SPY

```
C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe \
    confluence_static_board_renderer.py \
    --tickers SPY --artifact-root output/research_artifacts \
    --cache-dir cache/results \
    --signal-library-dir signal_library/data/stable \
    --stackbuilder-root output/stackbuilder
```

```
C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe \
    confluence_website_export_package.py \
    --tickers SPY --artifact-root output/research_artifacts \
    --cache-dir cache/results \
    --signal-library-dir signal_library/data/stable \
    --stackbuilder-root output/stackbuilder
```

Both: `authorization_class="read_only"`, `requires_separate_operator_authorization=False`, `blocked_by_policy_decision=False`.

### batch_7: `blocked_or_manual_review` — _GSPC

```
# Manual review needed for _GSPC: artifact_status=daily_only, cache_status=cache_missing,
# signal_library_status=stable_missing, stackbuilder_status=run_missing,
# recommended_next_action=blocked_missing_inputs. The Phase 6I-50 cascade could not pick
# a single highest-leverage action.
```

Comment-only (`argv=None`); `authorization_class="manual_review"`, `requires_separate_operator_authorization=False`.

### Synthetic examples (other batches — empty in production today)

The tests cover candidate command shapes for the other five batches in isolation. Reference shapes:

| Batch | Command shape (representative) |
|---|---|
| `partial_artifact_write_candidates` | `python multiwindow_k_confluence_patch_writer.py --ticker <T> --artifact-root <r> ... --write --allow-partial-payload-plan --invalid-members-json @<path>` |
| `strict_artifact_write_candidates` | Same as above WITHOUT `--allow-partial-payload-plan`. |
| `source_refresh_candidates` | `python signal_engine_cache_refresher.py --ticker <T> --cache-dir <r> --status-dir <r> --write` (one command per ticker; never CSV). |
| `signal_library_rebuild_or_promotion_candidates` | If `signal_library_status=staged_possible`: `python signal_library_stable_promotion_writer.py --ticker <T> --signal-library-dir <r> --write`. If `stable_missing`: documentation-only comment referencing the Phase 6I-30 / 6I-32 staged-rebuild runbook (no single-command rebuild). |
| `stackbuilder_rerun_candidates` | `python stackbuilder.py --secondary <T> --top-n 20 --bottom-n 20 --max-k 6 --search beam --beam-width 12 --seed-by total_capture --min-trigger-days 30 --combine-mode intersection`. Default: `blocked_by_policy_decision=true`. With `--accept-proposed-stackbuilder-defaults`: `blocked_by_policy_decision=false` + `policy_basis="proposed_defaults"`. |

## 6. StackBuilder policy handling

The Phase 6I-50 StackBuilder policy section's 6 unresolved questions (both_modes, combine_mode intersection-vs-union, seed_by/optimize_by, member-universe sizing, rerun cadence, invalid-member rotation) are passed through verbatim into `rollout.unresolved_policy_questions`.

- Default (no `--accept-proposed-stackbuilder-defaults`): every stackbuilder rerun candidate carries `blocked_by_policy_decision=true`, `policy_basis="unresolved_questions"`, `operator_policy_required=true`. The operator cannot run a StackBuilder rerun until they've reviewed the 6 unresolved questions.
- With `--accept-proposed-stackbuilder-defaults`: the candidate flips to `blocked_by_policy_decision=false`, `policy_basis="proposed_defaults"`, `operator_policy_required=false`. The command is marked READY_FOR_AUTHORIZATION but **still is not executed by this planner** — the operator runs it in a separate, explicitly authorized session.

In production today (`--all-artifacts`), no ticker is in `stackbuilder_rerun_candidates`, so the policy gate has no effect on the current rollout. The gate is documented and tested for the future moment when at least one ticker classifies as `rerun_stackbuilder`.

## 7. What this PR does NOT do

- Does NOT execute any candidate command. The candidate command STRINGS live in the JSON output and the optional commented-out shell-script preview.
- Does NOT set `PRJCT9_AUTOMATION_WRITE_AUTH`. The two-key writer authorization is entirely the operator's responsibility at invocation time.
- Does NOT modify any production root. Production-root inventory is preserved (3239 / 1634 / 35 / 5228 / 72899) across the entire phase.
- Does NOT run yfinance, the source-cache refresher (`signal_engine_cache_refresher`), the stable-promotion writer, the Confluence patch writer, the pipeline runner, the daily-board automation writer/executor, StackBuilder, OnePass, ImpactSearch, TrafficFlow, or Spymaster.
- Does NOT auto-decide any of the 6 unresolved StackBuilder policy questions.

## 8. Next step

The operator has two natural follow-ups:

1. **Resolve one or more of the 6 unresolved StackBuilder policy questions** → Phase 6I-52-style policy decisions amendment that updates `STACKBUILDER_OBSERVED_DEFAULTS` / `STACKBUILDER_PROPOSED_LAUNCH_DEFAULTS` / the unresolved-question block in the Phase 6I-50 planner, with the Phase 6I-51 planner consuming the updated values automatically.
2. **Use this rollout plan to authorize a specific batch's command in a separate session**, e.g. the SPY `board_render_now` batch (read-only). The planner does NOT execute the commands; the operator runs them under their own explicit authorization.

The planner does NOT authorize either of those future actions — it only describes the candidate next commands.
