# Phase 6I-33: SPY K-universe source-cache refresh readiness + exact supervised refresh candidate

Sprint date: **2026-05-14**.
Branch: `phase-6i-33-spy-k-universe-source-refresh-readiness`.
Doc: this file.

Phase 6I-32 (PR #249) landed the supervised fresh-staging readiness harness; its real SPY evidence verdict was `STATE_SOURCE_NOT_READY` because the production cache is behind cutoff. Phase 6I-33 asks the obvious follow-up: **is a refresh now actually productive?** This phase runs the read-only source-availability path against the SPY K=1..12 universe (SPY + 14 unique members), classifies each ticker into one of 5 stable categories, and — only if the aggregate predicate passes — prepares the exact supervised refresh command for operator review. **No production write is performed.**

---

## Product scope guard

This PR only checks **source-refresh readiness for the SPY K-universe**. It does **NOT** complete the final Confluence product.

The final Confluence product must include a **cross-ticker multi-window ranking / export layer** for a **large ticker universe** — effectively the TrafficFlow ranking workflow rebuilt with true multi-window support across all five canonical windows (1d / 1wk / 1mo / 3mo / 1y). The current single-ticker SPY chain (Phase 6I-22..33) exists to **prove the per-ticker data contract before scaling** to a multi-ticker universe. A single-ticker SPY pass landing 60/60 cells and `patch_ready=true` is the building block, NOT the website launch.

Required future website-facing data the multi-ticker layer must surface:

- **multi-ticker ranking rows** — one per qualifying ticker, scored on the canonical multi-window grid;
- **per-ticker 60-cell multi-window detail** (the Phase 6I-23 `per_window_k_metrics`);
- **`build_wide_window_alignment`** per ticker (the Phase 6I-23 alignment surface);
- **chart-ready rows** where available;
- **data freshness / blocker fields** so the UI can honestly surface "stale" / "missing window" / "missing K row" / "below-cutoff cache" instead of hiding them.

Stay aligned with the existing script family — **OnePass** / **ImpactSearch** / **StackBuilder** / **TrafficFlow** / **MultiTimeframe** / **Confluence**. Future module names should make it obvious which layer's data they consume. Do not invent vague replacement language that hides the data provenance.

---

## 0. TL;DR

| Check | Result |
|---|---|
| Production roots mutated | **No** — 0/0/0 added/removed/changed across all 5 roots (83,027 files) |
| Aggregate `refresh_candidate_ready` | **false** |
| `recommended_next_action` | **`wait_or_resolve_blockers`** |
| Future supervised refresh command prepared | **No** — the predicate failed; the doc lists the per-ticker blockers instead |
| All 15 K-universe tickers classified as `source_behind_or_error` | **Yes** — yfinance `new_cache_date_range_end` is `2026-05-13` for the 14 equities and `null` for TEF; resolved cutoff is `2026-05-14` |
| Phase 6I-32 fresh-staging harness re-run | `STATE_SOURCE_NOT_READY` — unchanged from Phase 6I-32 |
| Phase 6I-25 / 6I-31 / 6I-32 contracts | All untouched |

---

## 1. What the module does

`project/signal_library_source_refresh_readiness.py` (new, ~480 lines). One public function `evaluate_source_refresh_readiness(tickers, ...)` + CLI. For each ticker:

1. Run the existing `cache_cutoff_watcher.build_cache_cutoff_watch_report` (read-only) to capture `cache_date_range_end`, `cache_ahead_of_cutoff` etc.
2. Run the existing `source_availability_probe.evaluate_source_availability_many` (read-only; calls the Phase 6E-5 refresher with `write=False` — the established Phase 6I-15 / 6I-16 / 6I-17 pattern).
3. Classify the result into one of five stable categories:
   * `already_cache_ready` — cache strictly ahead of cutoff;
   * `source_ready_for_refresh` — source dry-run reports `new_cache_date_range_end > current_as_of_date` strictly;
   * `source_equal_cutoff_wait` — source dry-run reports equality; refresh would NOT advance the predicate (Phase 6I-15 / 6I-17 operator discipline);
   * `source_behind_or_error` — source behind cutoff OR provider-fetch failure OR error;
   * `manual_blocker` — catch-all.

Aggregate `refresh_candidate_ready=True` ONLY when every ticker classifies into the first two categories. Any other category demotes the aggregate. The module never sets `PRJCT9_AUTOMATION_WRITE_AUTH`, never passes `--write` or `write=True` to any seam, and AST-pins those contracts.

Every external probe is reachable through an injection seam whose default delegates to the existing project module's public function via a deferred local import.

---

## 2. Tests added (13 new)

`project/test_scripts/test_signal_library_source_refresh_readiness.py` (new, ~540 lines) pins:

| # | Test | Pins |
|---|---|---|
| 1 | All tickers source-ready → aggregate ready + `ready_for_supervised_refresh` | happy path |
| 2 | All tickers already cache-ready → aggregate ready + `no_refresh_needed` | no-op path |
| 3 | One ticker `source_equal_cutoff_wait` → aggregate NOT ready | demotion |
| 4 | One ticker provider fetch failed → aggregate NOT ready + `provider_fetch_failed` note | demotion |
| 5 | Cache-ahead overrides missing source state | resilience to transient probe failure |
| 6 | Cache-behind + source state missing → `manual_blocker` | classifier fallback |
| 7 | Module never sets `PRJCT9_AUTOMATION_WRITE_AUTH` | env-var contract |
| 8 | Module passes neither `--write` nor `write=True` to any seam | injection-seam contract |
| 9-10 | CLI `rc=2` (missing tickers / unknown flag) | operator-surface sanity |
| 11 | No raw `pickle.load` | B12 scope |
| 12 | No forbidden top-level imports (yfinance / dash / subprocess / live engines / writers / refresher) | strictly bounded |
| 13 | AST has no `write=True` keyword arg anywhere | belt-and-braces dry-run guard |

The repo-wide B12 raw-pickle static regression guard continues to pass without an allowlist entry.

---

## 3. Repo state

```
Branch: phase-6i-33-spy-k-universe-source-refresh-readiness
Main HEAD (at branch creation): 6ae247b (Phase 6I-32, PR #249)
```

---

## 4. Test results

```
Phase 6I-33 readiness tests        :  13 passed
Phase 6I-32 harness tests          :  23 passed
Phase 6I-31 promotion tests        :  26 passed
Phase 6I-30 builder tests          :  10 passed
Adapter / diagnostic / core /
  builder / planner / writer /
  gap audit / static regression    : 240 passed
                                   -----
Focused 12-way sweep               : 312 passed in 11.36 s

py_compile                         : clean across new Python files
git diff --check                   : clean
```

---

## 5. Real SPY evidence

### 5.1 Temp evidence directory

```
C:\Users\sport\AppData\Local\Temp\phase_6i33_spy_k_universe_source_refresh_readiness\
├── 00_snapshot_before.json
├── 01_readiness_report.json
├── 01_readiness_report.stderr.txt
├── 02_phase_6i32_harness_rerun.json
├── 02_phase_6i32_harness_rerun.stderr.txt
├── staged_libs/                   (Phase 6I-32 harness sandbox output)
├── promotion_writer_log.jsonl
├── patch_writer_log.jsonl
├── 99_snapshot_after.json
├── 99b_snapshot_diff.json
├── snapshot_helper.py              (copied from Phase 6I-30)
└── diff_helper.py                  (copied from Phase 6I-30)
```

Pinned interpreter: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### 5.2 K-universe discovery

The same SPY K=1..12 universe used by the Phase 6I-31 and 6I-32 evidence runs (14 unique member tickers + SPY = 15 total). The universe was discovered by the prior Phase 6I-27 adapter diagnostic against the production StackBuilder run and has been stable across phases. The selected run id is `seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D`.

### 5.3 Command run

```
"<pinned-interp>" signal_library_source_refresh_readiness.py \
  --tickers SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO,TEF \
  --cache-dir cache/results \
  --current-as-of-date 2026-05-14
# rc=0
```

### 5.4 Per-ticker readiness table

| Ticker | `cache_date_range_end` | `new_cache_date_range_end` | `cache_behind_cutoff` | `source_ahead` / `equal` / `behind` | classification |
|---|---|---|---|---|---|
| SPY  | 2026-05-12 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| AROW | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| AWR  | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| CLH  | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| CP   | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| EXPO | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| FCFS | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| GBCI | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| HCSG | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| JNJ  | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| LLY  | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| MO   | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| PRA  | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| PRGO | 2026-05-04 | 2026-05-13 | True | F / F / **T** | `source_behind_or_error` |
| TEF  | 2026-01-28 | **null**    | True | F / F / F | `source_behind_or_error` |

### 5.5 Provider-telemetry summary

Every yfinance dry-run fetch attempt **succeeded** (the source-availability probe returned a result for every ticker). Each probe reported `new_cache_date_range_end=2026-05-13` for the 14 equities — yfinance has data through 2026-05-13 (yesterday's trading day) but NOT yet through 2026-05-14 (today's trading day — the resolved cutoff). TEF returned `new_cache_date_range_end=null`, indicating yfinance did not return a parseable end-date for that ticker on this probe.

### 5.6 Aggregate verdict

```json
{
  "refresh_candidate_ready": false,
  "recommended_next_action": "wait_or_resolve_blockers",
  "counts_by_classification": {"source_behind_or_error": 15}
}
```

The aggregate is **NOT ready**. Per the Phase 6I-33 spec ("If any predicate fails, do not include a future command block"), this doc does NOT prepare a future supervised refresh command.

### 5.7 Phase 6I-32 fresh-staging harness re-run

Re-ran the Phase 6I-32 harness against the same K-universe to confirm downstream readiness is still all-green and production roots remain untouched:

| Stage | Verdict |
|---|---|
| Final state | `STATE_SOURCE_NOT_READY` (unchanged from Phase 6I-32) |
| Sandbox staged build | 75 written / 0 failed |
| Promotion planner | `plan_ready=true` |
| Adapter | `prepared_cell_count=60`, `can_evaluate_full_60_cell_grid=true` |
| Payload builder | `payload_ready=true` |
| Patch planner | `patch_ready=true` |
| Patch writer dry-run | `planner_patch_ready=true`, `wrote_artifact=false` |
| Production-root diff | 0/0/0 |

The downstream staged chain is still all-green against the staged sandbox dir. The only blocker remains source/cache freshness, and **today's blocker is one trading day earlier than yfinance's most-recent data**.

---

## 6. Production-root diff (0/0/0)

| Root | Files | Added | Removed | Changed |
|---|---|---|---|---|
| `cache/results` | 3,239 | 0 | 0 | 0 |
| `cache/status` | 1,634 | 0 | 0 | 0 |
| `output/research_artifacts` | 35 | 0 | 0 | 0 |
| `output/stackbuilder` | 5,220 | 0 | 0 | 0 |
| `signal_library/data/stable` | 72,899 | 0 | 0 | 0 |
| **TOTAL** | **83,027** | **0** | **0** | **0** |

Zero added / zero removed / zero changed across all 83,027 files in all five production roots.

---

## 7. Future supervised refresh command — NOT prepared

The Phase 6I-33 spec is clear: "If any predicate fails, do not include a future command block. Instead list the per-ticker blockers."

The aggregate predicate `refresh_candidate_ready` is **false** (every ticker classifies as `source_behind_or_error`). Therefore:

**No future supervised refresh command is included in this doc.**

For operator reference, the existing Phase 6E-5 refresher CLI surface is:

```
"<pinned-interp>" signal_engine_cache_refresher.py --ticker <T> --write \
    [--cache-dir <DIR>] [--status-dir <DIR>] \
    [--max-sma-day <N>] [--current-as-of-date <YYYY-MM-DD>]
```

The refresher's authorization gate is `--write` alone (it has its own single-key dry-run / write toggle, distinct from the Phase 6H-5 two-key gate that wraps `daily_board_automation_writer` / `multiwindow_k_confluence_patch_writer` / `signal_library_stable_promotion_writer`). This means a future supervised refresh of 15 tickers requires 15 invocations of the refresher with `--write`, each producing its own status JSON and cache PKL.

**This is documentation of the existing authorization contract, not a recommendation. The Phase 6I-33 predicate currently rejects refresh as productive.**

### 7.1 Per-ticker blockers

| Ticker | Blocker |
|---|---|
| SPY | yfinance `new_cache_date_range_end=2026-05-13` < cutoff `2026-05-14`; refresh would NOT advance the cache past cutoff |
| AROW, AWR, CLH, CP, EXPO, FCFS, GBCI, HCSG, JNJ, LLY, MO, PRA, PRGO | same as SPY: yfinance `2026-05-13` < cutoff `2026-05-14` |
| TEF | yfinance `new_cache_date_range_end=null` — provider did not return a parseable end-date for this ticker |

---

## 8. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation (any writer) | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** |
| Authorized launcher script created | **No** |
| Source refresh (`signal_engine_cache_refresher`) in write mode | **No** — only read-only dry-run via `source_availability_probe` was invoked, which calls the refresher with `write=False` (the established Phase 6I-15 read-only probe pattern; yfinance fetches occurred but no disk writes) |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence batch execution | **No** |
| Production data write | **No** (0/0/0 across all 5 roots) |
| Production signal-library write to `signal_library/data/stable/` | **No** |
| Subprocess invocations from production modules | **No** |
| Execution-log writes to `output/automation_logs/` | **No** |

The Phase 6H-5 two-key writer gate, Phase 6I-9 supervised gate, Phase 6I-22 strict full-member-coverage gate, Phase 6I-25 patch-writer 5-gate cascade (including `_writer_plan_payload_is_consistent`), Phase 6I-28 close-source fallback contract, Phase 6I-29 exact-date member alignment, Phase 6I-30 interval-native close builder, Phase 6I-31 promotion writer 5-gate cascade + transactional rollback, and Phase 6I-32 fresh-staging readiness harness are all unchanged in runtime contract.

---

## 9. Operational state carried forward

- STATE 4 / cache-behind-cutoff (`cache_date_range_end=2026-05-12`; `current_as_of_date=2026-05-14`).
- Production `has_true_multiwindow_k_engine_outputs` — still `false` for SPY.
- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still open.
- Writer-surface provider telemetry — still pending.

---

## 10. Exact next step

**Wait until yfinance has 2026-05-14 data**, then re-run the Phase 6I-33 readiness module. The wait is a clock-time wait, not an operator action: yfinance typically publishes a trading day's data after the close (≈ 16:00 ET / 20:00 UTC for US equities). The current evidence pass was conducted while yfinance is still showing 2026-05-13 as the latest available trading day.

When the readiness module subsequently reports `refresh_candidate_ready=true` for every ticker (status `source_ready_for_refresh` or `already_cache_ready`):

1. Re-run this Phase 6I-33 module — confirm `refresh_candidate_ready=true`.
2. In a SEPARATE prompt, request operator authorization for a supervised refresh of the SPY K-universe. The supervised refresh runs 15 invocations of `signal_engine_cache_refresher.py --write --ticker <T>` (one per ticker) with their own execution-log paths outside production roots.
3. After the supervised refresh lands, re-run the Phase 6I-32 fresh-staging harness without `--skip-source-availability`. If it now lands in `STATE_STAGED_REBUILD_READY`, request a Phase 6I-31 promotion writer authorization in a SEPARATE prompt.
4. After the promotion lands, re-run the multi-window K chain against production stable. If it confirms `patch_ready=true`, request a Phase 6I-25 patch writer authorization in a fourth SEPARATE prompt.

**Do NOT skip any step. Each step is a separate supervised authorization.**

The TEF `new_cache_date_range_end=null` finding is a SEPARATE potential blocker that may need investigation before a supervised refresh of TEF is productive — if subsequent readiness runs continue to show TEF as `null`, the operator should triage that ticker independently (e.g. confirm it is still in the StackBuilder K-universe; check yfinance for symbol changes / delisting).

---

## 11. Validation

- `git diff --check`: clean.
- `git diff --stat`: 3 files added — 1 new production module (`signal_library_source_refresh_readiness.py`), 1 new test file (`test_scripts/test_signal_library_source_refresh_readiness.py`), 1 new Markdown evidence doc (this file).
- Pinned interpreter: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
- Focused 12-way: **312 passed in 11.36s**.

---

## 12. Reference paths

- Readiness module: `project/signal_library_source_refresh_readiness.py` (new).
- Tests: `project/test_scripts/test_signal_library_source_refresh_readiness.py` (new; 13 tests).
- Phase 6I-32 evidence (predecessor — fresh-staging readiness harness): `project/md_library/shared/2026-05-14_PHASE_6I32_FRESH_STAGED_SIGNAL_LIBRARY_REBUILD_EVIDENCE.md`.
- Phase 6I-31 promotion path: `project/signal_library_stable_promotion_planner.py` + `project/signal_library_stable_promotion_writer.py`.
- Phase 6E-5 refresher: `project/signal_engine_cache_refresher.py`.
- Phase 6E-5 source-availability probe: `project/source_availability_probe.py`.
- Phase 6H-3 cache-cutoff watcher: `project/cache_cutoff_watcher.py`.
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i33_spy_k_universe_source_refresh_readiness\` (OUTSIDE production roots, OUTSIDE the repo; nothing in it is committed).
- CLAUDE.md § 6 — current sprint state.
