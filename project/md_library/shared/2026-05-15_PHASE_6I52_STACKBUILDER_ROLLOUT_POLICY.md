# Phase 6I-52: Locked StackBuilder rollout policy + first seed-universe manifest

**Date:** 2026-05-15
**Base commit (main):** `30069c2` (Phase 6I-51 squash-merge)
**Branch:** `phase-6i-52-stackbuilder-rollout-policy`
**Status:** Read-only policy artifact. No production writes. **Do not merge** until operator approval.

`<PINNED_PYTHON> = C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`

---

## 1. Purpose

Phase 6I-50 surfaced six unresolved StackBuilder policy questions (`both_modes`, `combine_mode`, `seed_by` / `optimize_by`, per-ticker member-universe sizing, re-run cadence, invalid-member rotation). Phase 6I-51 carried them through as `unresolved_policy_questions` on every stackbuilder rerun candidate. Phase 6I-52 **locks each one as an explicit, versioned, test-pinned policy decision** and emits a first **seed-universe manifest** (25 tickers anchored by SPY) plus a per-ticker candidate StackBuilder command list.

This phase is the prerequisite for Phase 6I-53 (the first supervised StackBuilder batch execution against a real ticker universe larger than SPY). Phase 6I-52 itself **does not run StackBuilder** and does not authorize anything to write — it is the policy + universe lock that Phase 6I-53 will consume.

## 2. What was added

### Module

`project/confluence_stackbuilder_rollout_policy.py`

- **Schema / policy stability:** `SCHEMA_VERSION="confluence_stackbuilder_rollout_policy_v1"`, `POLICY_NAME="phase_6i_52_locked_policy"`, `POLICY_VERSION="v1"`, `POLICY_BASIS=POLICY_NAME`.
- **Six locked policy decisions** as stable constants with rationale strings (see Section 3).
- **First seed-universe manifest** as a committed Python tuple `FIRST_ROLLOUT_PILOT_UNIVERSE_V1` (26 entries with one intentional duplicate, which the normalizer dedupes to 25).
- **Public entry** `build_stackbuilder_rollout_policy_manifest(tickers=None, *, seed_universe_source=None, signal_library_dir=None) -> dict` returns the full policy manifest + per-ticker StackBuilder candidate command list.
- **CLI** with `--tickers` override, `--seed-universe-source` label override, `--signal-library-dir` (threads `--signal-lib-dir <DIR>` through every candidate command), `--output` (production-root path guard).
- **Strict read-only contract:** no top-level imports of `subprocess` / `yfinance` / `dash` / writer modules / engine modules. Statically enforced by `test_no_forbidden_top_level_imports`. No `--write`, no `PRJCT9_AUTOMATION_WRITE_AUTH`, no on-disk write at any layer except the optional `--output` JSON (production-root-guarded).
- **Per-command authorization tagging:** every record carries `authorization_class="stackbuilder_write"`, `requires_separate_operator_authorization=true`, `policy_basis="phase_6i_52_locked_policy"`, `blocked_by_policy_decision=false` (i.e. the policy gate IS the unblocker — but the command STILL is not executed by this module).

### Tests

`project/test_scripts/test_confluence_stackbuilder_rollout_policy.py` — 14 focused tests, all passing under the pinned interpreter:

1. Schema / policy / pinned-interpreter constants are stable.
2. Six locked policy decisions are pinned exactly + each carries a non-empty rationale.
3. Every command uses `--secondary <TICKER>` (NOT `--ticker`) + the locked flags.
4. No command includes `--both-modes` (regression guard).
5. Every command starts with the pinned interpreter.
6. Seed universe deduplicates + uppercases + strips (the committed tuple intentionally includes a duplicate `JPM` to pin the normalizer).
7. Manifest count equals the deduped ticker count.
8. Each command record carries the locked taxonomy (`stackbuilder_write` / `requires_separate_operator_authorization=true` / `policy_basis="phase_6i_52_locked_policy"` / `blocked_by_policy_decision=false`).
9. Generated argv parses against the **real `stackbuilder.parse_args` argparse surface** (deferred-imported at test time; the policy module itself never imports stackbuilder).
10. Static guard: no forbidden top-level imports.
11. `--output` rejects paths inside every production root.
12. `--tickers` CLI override + `--signal-library-dir` threading.
13. SPY appears in the seed universe (continuity with the Phase 6I-49 pilot) AND is the first ticker.
14. `unresolved_or_deferred_policy_items` is present + non-trivial.

Combined regression: **53 / 53 tests pass** across Phase 6I-50 (16) + Phase 6I-51 (23) + Phase 6I-52 (14).

```
"<PINNED_PYTHON>" -m pytest \
    test_scripts/test_confluence_large_universe_launch_planner.py \
    test_scripts/test_confluence_large_universe_rollout_batch_planner.py \
    test_scripts/test_confluence_stackbuilder_rollout_policy.py -q
... 53 passed in 1.59s
```

## 3. The six locked policy decisions

| # | Item | Locked value | Rationale |
|---|---|---|---|
| 1 | `both_modes` | `False` | Keep the first large-universe pass aligned with the observed StackBuilder default (`--both-modes` is a store_true flag, default = False). Do not double-compute Buy + Short candidates until the multi-ticker board path is proven on at least one supervised batch. |
| 2 | `combine_mode` | `"intersection"` | The CLI exposes `--combine-mode choices=['intersection','union'] default='intersection'`. Phase 6I-52 keeps the conservative all-members-agree path; `union` requires its own evaluation pass. |
| 3 | `seed_by` / `optimize_by` | `"total_capture"` / `"total_capture"` | Pinned to `total_capture` (stackbuilder.py default and the existing TrafficFlow-style sort axis). Other axes (`sharpe`) require their own validation. The Phase 6I-52 policy pins both explicitly for auditability (stackbuilder.py auto-resolves `--optimize-by` to `--seed-by` when unset). |
| 4 | `member_universe_size` | `12` | Matches the existing SPY seed-run shape `seedTC__<T1>-<M1>_..._<T12>-<M12>`. The first rollout does NOT introduce per-ticker sizing policy (market-cap-tuned, liquidity-tuned, etc.) — that's a future-phase decision. |
| 5 | `rerun_cadence` | `"manual_supervised"` | No scheduler / cron / automation runner. Phase 6I-53 will be the FIRST supervised batch execution; each ticker is a separate, explicitly authorized invocation. |
| 6 | `invalid_member_rotation` | `"partial_effective_members_with_warning"` | When a member is flagged `invalid_or_delisted` (Phase 6I-43), the downstream partial-payload contract (Phase 6I-46 / 6I-47 / 6I-48 / 6I-49) carries the partial result honestly with the visible `!` warning, exactly as SPY does today. No auto-substitution in the first rollout. |

Each decision is exposed as a Python constant (`POLICY_BOTH_MODES`, `POLICY_COMBINE_MODE`, `POLICY_SEED_BY`, `POLICY_OPTIMIZE_BY`, `POLICY_MEMBER_UNIVERSE_SIZE`, `POLICY_RERUN_CADENCE`, `POLICY_INVALID_MEMBER_ROTATION`) AND as a `(value, rationale)` mapping under `LOCKED_POLICY_DECISIONS`.

## 4. First seed-universe manifest (v1)

The first rollout pilot universe is 25 tickers (deduplicated from a 26-entry source tuple). SPY is first for continuity with the proven Phase 6I-49 pilot. The remaining 24 are large-cap equities across tech / financials / consumer / industrial-adjacent:

```
SPY, AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, AVGO, ORCL, ADBE, CRM,
AMD, QCOM, CSCO, JPM, BRK-B, V, MA, JNJ, WMT, PG, HD, KO, MCD
```

Source label: `seed_universe_source="phase_6i_52_first_rollout_pilot_universe_v1"`.

**This is the FIRST rollout pilot universe, NOT the final universe.** The repository contains no canonical curated ticker list to reuse (the GTL has 72,735 auto-discovered names; `output/stackbuilder/` has 249 organic existing-run subdirs; neither is a thoughtful pilot selection). Universe expansion (50 / 100 / 250 / full StackBuilder-existing-runs / full GTL) is a deferred policy item.

The committed Python tuple `FIRST_ROLLOUT_PILOT_UNIVERSE_V1` includes one intentional duplicate (`JPM` appears twice) to pin that the public-entry normalizer dedupes. The evidence JSON shows 25 unique tickers and 25 manifest commands.

## 5. Example generated StackBuilder command

Every command uses the Phase 6I-50-amendment-1 corrected `--secondary <TICKER>` entry flag + the six locked policy flags. No `--both-modes`. The pinned interpreter is at position 0 of every argv.

```
<PINNED_PYTHON> stackbuilder.py \
    --secondary SPY \
    --top-n 20 --bottom-n 20 --max-k 6 \
    --search beam --beam-width 12 \
    --seed-by total_capture --optimize-by total_capture \
    --min-trigger-days 30 \
    --combine-mode intersection \
    --signal-lib-dir signal_library/data/stable
```

Per-record tagging:

```
authorization_class                            = stackbuilder_write
requires_separate_operator_authorization       = True
policy_basis                                   = phase_6i_52_locked_policy
blocked_by_policy_decision                     = False
command_label                                  = stackbuilder_first_rollout_run
```

The argv parses cleanly against the real `stackbuilder.parse_args` argparse surface (pinned by `test_generated_argv_parses_against_real_stackbuilder_cli` — the test runs once per manifest row to catch future stackbuilder.py CLI drift the moment it lands).

## 6. Integration with Phase 6I-51

Phase 6I-51's read-only rollout batch planner has its own StackBuilder rerun candidate emitter; it gates those candidates on `--accept-proposed-stackbuilder-defaults`. Phase 6I-52 is the **upstream policy artifact** that formally accepts those defaults and emits an authoritative per-ticker command manifest with a stable `policy_basis="phase_6i_52_locked_policy"` tag.

Phase 6I-52 deliberately does NOT modify the Phase 6I-51 rollout batch planner. The 6I-51 planner remains the authoritative *per-ticker classification* surface; this module is the *policy lock* and *seed universe* that Phase 6I-53 (the supervised batch execution phase) will consume.

Two consumption paths for downstream phases:

1. **Direct consumption:** load `2026-05-15_PHASE_6I52_STACKBUILDER_ROLLOUT_POLICY_EVIDENCE.json` and execute commands sequentially under operator supervision.
2. **Via Phase 6I-51:** feed the seed universe as a `--tickers` / `--universe-file` argument to the Phase 6I-50 launch planner, then run the Phase 6I-51 rollout batch planner with `--accept-proposed-stackbuilder-defaults`. The 6I-51 planner will emit equivalent stackbuilder rerun candidates with `blocked_by_policy_decision=false` + `policy_basis="proposed_defaults"`. The Phase 6I-52 evidence JSON is the **authoritative policy record**; the 6I-51 chain is the live operational reproduction.

## 7. Production-roots evidence pass

```
<PINNED_PYTHON> confluence_stackbuilder_rollout_policy.py \
    --signal-library-dir signal_library/data/stable \
    --output md_library/shared/2026-05-15_PHASE_6I52_STACKBUILDER_ROLLOUT_POLICY_EVIDENCE.json
```

**Production-roots untouched:**

| Root | Pre-run | Post-run | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5229 | 5229 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |
| **Combined** | **83036** | **83036** | **0** |

Evidence JSON lands at `md_library/shared/` — outside every production root.

## 8. What this PR does NOT do

- Does NOT run StackBuilder. The candidate command STRINGS live in the JSON output.
- Does NOT pre-authorize Phase 6I-53. The two-key writer authorization (`--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`) remains the operator's responsibility at invocation time.
- Does NOT modify any production root.
- Does NOT run yfinance, the source-cache refresher, the stable-promotion writer, the Confluence patch writer, the pipeline runner, OnePass, ImpactSearch, TrafficFlow, or Spymaster.
- Does NOT modify the Phase 6I-50 launch planner or the Phase 6I-51 rollout batch planner. Both remain authoritative for their own concerns.

## 9. Deferred policy items (`unresolved_or_deferred_policy_items`)

The Phase 6I-52 policy lock is FIRST-ROLLOUT-SCOPED. The following items are deliberately deferred:

1. **`per_ticker_member_universe_sizing`** — fixed 12 per ticker; market-cap-tuned / liquidity-tuned policy is a future decision once we have supervised-batch evidence.
2. **`automated_rerun_cadence`** — manual / supervised only; scheduler (daily / weekly / on-invalid-member-detected) is a future decision.
3. **`invalid_member_auto_substitution`** — partial-effective-members + `!` warning path; auto-replacement is a future decision pending supervised-batch evidence at scale.
4. **`combine_mode_union_evaluation`** — `intersection` locked; future A/B evaluation against `union` may follow.
5. **`seed_by_sharpe_evaluation`** — `total_capture` locked; future A/B evaluation against `sharpe` may follow.
6. **`second_rollout_universe_size`** — 25 names locked for the first rollout; second-rollout expansion (50 / 100 / 250 / full StackBuilder-existing-runs / full GTL) is a future-phase decision.

Each future revision should bump `POLICY_VERSION` (`v1` → `v2`, etc.) so a downstream audit can tell apart "first rollout state" from "policy revision N".

## 10. Next step

**Phase 6I-53 — first supervised StackBuilder batch execution using this locked policy.** Concretely:

1. Operator loads `2026-05-15_PHASE_6I52_STACKBUILDER_ROLLOUT_POLICY_EVIDENCE.json` and reviews the 25 candidate commands.
2. Operator runs each command (or a chosen subset) in a separate, explicitly authorized session, under the existing two-key gate where the StackBuilder script's own writer surface applies.
3. Each run produces a per-ticker `output/stackbuilder/<TICKER>/seedTC__<...>/` directory.
4. The Phase 6I-50 launch planner + Phase 6I-51 rollout batch planner are then re-run against the expanded universe to confirm classification + cascade and to surface next-action candidates for the Confluence side of the chain.

Phase 6I-52 does NOT authorize Phase 6I-53; Phase 6I-53 will be a separate explicit prompt with its own evidence pass. The next step is **execution against this policy lock**, NOT another policy-planning phase.
