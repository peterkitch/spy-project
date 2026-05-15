# Phase 6I-44: SPY K-universe source-cache refresh — evidence + Phase 6I-43 emitter fix

**Date:** 2026-05-14
**Scope:** the 15-ticker SPY K-universe — SPY, AROW, AWR, CLH, CP, EXPO,
FCFS, GBCI, HCSG, JNJ, LLY, MO, PRA, PRGO, TEF.
**Authorization basis:** explicit operator authorization for one supervised
source-cache refresh round consisting of exactly 14 per-ticker invocations
(non-TEF only). TEF, the Confluence patch writer, the signal-library
promotion writer, `confluence_pipeline_runner`, StackBuilder / OnePass /
ImpactSearch / TrafficFlow / Spymaster batch execution, and any write to
`signal_library/data/stable`, `output/research_artifacts`, or
`output/stackbuilder` were explicitly NOT authorized.
**Production-write surfaces touched:** `cache/results` (14 PKLs + 14
manifests) and `cache/status` (14 status JSONs). No other production root
changed.

---

## 1. Background

Two distinct earlier phases set the stage for Phase 6I-44 and must not be
conflated:

  * **Phase 6I-34** (PR #251, merged at `d5ee23b`) introduced the
    multi-ticker TrafficFlow-style Confluence ranking / export contract,
    the strict Phase 6I-20 validation surface, and the 9-code
    blocked-reason taxonomy. Phase 6I-34 did not touch the source-cache
    refresh path.
  * **Phase 6I-43** (PR #260, merged at `b0f7ff3` on 2026-05-14)
    introduced the read-only source-refresh policy v2 planner
    (`signal_library_source_refresh_policy_v2.plan_source_refresh_policy_v2()`),
    the 7-class v2 taxonomy, the explicit
    `allow_equal_cutoff_after_close` operator policy switch, the
    invalid-member (TEF-style) handling with
    `invalid_ticker_policy="warn_and_exclude"`, and the
    refresh-candidate-command emitter on `SourceRefreshPolicyV2Report`.
    The emitted command was intended to be the exact, copy-pasteable
    supervised refresher invocation the operator would run when
    `refresh_candidate_ready=true`.

Phase 6I-44 is therefore an amendment to the Phase 6I-43 emitter (and a
supervised production refresh round that exercises the corrected
emitter), not to the Phase 6I-34 ranking/validation surface.

A first attempt to use that emitted shape against the SPY K-universe in
this phase failed at the refresher CLI with **rc=2** at argparse time:

```
usage: signal_engine_cache_refresher [-h] --ticker TICKER
                                     [--dry-run | --write]
                                     [--cache-dir CACHE_DIR]
                                     [--status-dir STATUS_DIR]
                                     [--max-sma-day MAX_SMA_DAY]
                                     [--current-as-of-date CURRENT_AS_OF_DATE]
signal_engine_cache_refresher: error: the following arguments are required: --ticker
```

Root cause: Phase 6I-43's emitter built a single command with
`--tickers <CSV>` (plural). The Phase 6E-5 refresher CLI
(`signal_engine_cache_refresher.py`) actually requires `--ticker <T>`
(singular), one invocation per ticker. The plural shape was rejected by
argparse before any side effect could occur. Production roots were
verified untouched (file counts and per-ticker mtimes identical pre/post
the failed attempt). The contract bug is therefore real but did not put
production state at risk.

This phase carries two pieces of work:

  1. Fix the merged Phase 6I-43 emitter so the planner emits per-ticker
     commands using the refresher CLI's actual shape (`--ticker <T>`, one
     per invocation), with the plural list as the authoritative surface
     and the deprecated singular fields kept for backward compatibility.
  2. Run the 14 explicitly authorized refresh commands once each, capture
     stdout/stderr/rc per command, and prove that the production-write
     surface was exactly the surface that was authorized — `cache/results`
     and `cache/status` for the 14 non-TEF tickers only, with TEF and the
     three downstream production roots untouched.

---

## 2. Part A — Phase 6I-43 emitter fix (`--tickers` → per-ticker `--ticker`)

### 2.1. Module changes (`signal_library_source_refresh_policy_v2.py`)

  * Added two new authoritative fields to
    `SourceRefreshPolicyV2Report`:
    * `refresh_candidate_commands: tuple[str, ...]` — one joined command
      string per non-invalid candidate ticker.
    * `refresh_candidate_command_argvs: tuple[tuple[str, ...], ...]` —
      one argv tuple per command, parallel to `refresh_candidate_commands`.
  * Replaced the old `_build_refresh_candidate_command()` (plural-CSV,
    broken) with `_build_one_refresh_command()` (single ticker, uses
    `--ticker` singular) and a `_build_refresh_candidate_commands()`
    wrapper that fans across the candidate ticker list.
  * `plan_source_refresh_policy_v2()` now populates BOTH the new plural
    fields and the deprecated singular fields. The deprecated singular
    fields (`refresh_candidate_command` and
    `refresh_candidate_command_argv`) now carry the FIRST per-ticker
    command only and remain on the surface purely for backward
    compatibility. Callers MUST use the plural fields.
  * `to_json_dict()` emits both the singular and plural fields so the JSON
    surface matches the in-memory contract.

### 2.2. Test changes (`test_scripts/test_signal_library_source_refresh_policy_v2.py`)

Three existing tests still asserted the old singular-CSV shape and were
updated to assert the new plural per-ticker contract:

  * `test_invalid_ticker_excluded_from_candidate_command` — now asserts
    `len(refresh_candidate_commands) == 2`, each command uses
    `--ticker`, TEF excluded.
  * `test_ready_true_with_policy_and_invalid_excluded` — now asserts
    14 commands on the SPY K-universe fixture, each per-ticker, no
    `--tickers`, TEF excluded.
  * `test_amendment1_pinned_interpreter_with_invalid_excluded` —
    extended to walk every command in the plural list and check (a)
    pinned interpreter first, (b) `--ticker` singular, (c) no `--tickers`
    plural, (d) no `PRJCT9_AUTOMATION_WRITE_AUTH` / `phase_6h5_explicit`
    wording, (e) `--write` present, (f) no bare `python`.

Six new Phase 6I-44 tests were added, each operating on a shared
14-non-TEF + TEF fixture:

  * `test_phase_6i44_commands_use_singular_ticker_not_plural` — every
    command uses `--ticker <T>`, never `--tickers <CSV>`; argv values
    after `--ticker` contain no comma.
  * `test_phase_6i44_one_command_per_non_invalid_candidate` —
    `len(commands) == 14`, the set of tickers across argvs equals the
    14 non-TEF set, TEF appears nowhere.
  * `test_phase_6i44_each_command_starts_with_pinned_interpreter` —
    every command's first token is the spyproject2 audit interpreter
    path; argv[0] equals `PINNED_PYTHON_INTERPRETER`; no bare `python`.
  * `test_phase_6i44_each_argv_includes_refresher_script_and_ticker` —
    every argv includes `signal_engine_cache_refresher.py` and
    `--ticker <T>`.
  * `test_phase_6i44_no_env_var_or_phase_6h5_wording_in_any_command` —
    no command and no argv token contains `PRJCT9_AUTOMATION_WRITE_AUTH`
    or `phase_6h5_explicit`; same guarantee on the `to_json_dict()`
    serialization (which now includes both plural fields).
  * `test_phase_6i44_singular_field_is_first_plural_command` — the
    deprecated singular fields equal the first element of the plural
    list.

### 2.3. Test result

```
$ python -m pytest test_scripts/test_signal_library_source_refresh_policy_v2.py -q
.................................                                        [100%]
33 passed in 1.92s
```

Full regression after the fix:

```
$ python -m pytest test_scripts -q
2164 passed, 165 warnings in 361.06s (0:06:01)
```

The 165 warnings are the pre-existing pandas fragmentation warnings
(`test_lookahead_poison.py` × 60 + `multi_timeframe_builder.py` × 105)
already documented in the sprint state; no new warnings.

---

## 3. Part B — 14 authorized per-ticker refresh commands

### 3.1. Pre-refresh readiness

The Phase 6I-43 policy v2 verdict against the SPY K-universe with
`--allow-equal-cutoff-after-close` (captured at
`_phase_6i_44_preflight.json`):

```
refresh_candidate_ready = True
invalid_tickers         = ['TEF']
counts_by_classification = {
    'source_equal_cutoff_publishable': 14,
    'invalid_or_delisted':              1,
}
refresh_candidate_tickers = ['SPY', 'AROW', 'AWR', 'CLH', 'CP',
                              'EXPO', 'FCFS', 'GBCI', 'HCSG',
                              'JNJ', 'LLY', 'MO', 'PRA', 'PRGO']
len(refresh_candidate_commands) = 14
```

The preflight stderr captured the yfinance "possibly delisted" warning on
TEF and nothing else.

### 3.2. Pre-refresh production-root snapshot

Snapshot at `_phase_6i_44_pre_snapshot.json` immediately before the first
authorized refresh command:

| Root                          | File count |
|-------------------------------|-----------:|
| `cache/results`               |       3239 |
| `cache/status`                |       1634 |
| `output/research_artifacts`   |         35 |
| `output/stackbuilder`         |       5223 |
| `signal_library/data/stable`  |      72899 |
| **Total**                     |    **83030** |

### 3.3. Commands run + per-ticker outcome

Each command was a single Bash invocation of the form below, with one of
the 14 authorized tickers substituted into `<TICKER>`. The pinned
spyproject2 interpreter was used per the CLAUDE.md interpreter pin.

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
    signal_engine_cache_refresher.py \
    --ticker <TICKER> \
    --cache-dir cache/results \
    --current-as-of-date 2026-05-14 \
    --write
```

Per-ticker stdout JSON (`_phase_6i_44_refresh_<TICKER>.stdout`) and
stderr (`_phase_6i_44_refresh_<TICKER>.stderr`) were captured for each
invocation. All 14 commands exited rc=0. All 14 stderr files are 0 bytes.

| Ticker | refreshed | old `cache_date_range_end` | new `cache_date_range_end` | yfinance rows | elapsed (s) | issue_codes |
|--------|:---------:|---------------------------:|---------------------------:|--------------:|------------:|-------------|
| SPY    |   True    |               2026-05-12   |               2026-05-14   |          8380 |        4.25 | `[]`        |
| AROW   |   True    |               2026-05-04   |               2026-05-14   |         11506 |        5.20 | `[]`        |
| AWR    |   True    |               2026-05-04   |               2026-05-14   |         13421 |        5.75 | `[]`        |
| CLH    |   True    |               2026-05-04   |               2026-05-14   |          9690 |        4.58 | `[]`        |
| CP     |   True    |               2026-05-04   |               2026-05-14   |         10676 |        4.97 | `[]`        |
| EXPO   |   True    |               2026-05-04   |               2026-05-14   |          9000 |        4.31 | `[]`        |
| FCFS   |   True    |               2026-05-04   |               2026-05-14   |          8796 |        4.19 | `[]`        |
| GBCI   |   True    |               2026-05-04   |               2026-05-14   |         10619 |        4.94 | `[]`        |
| HCSG   |   True    |               2026-05-04   |               2026-05-14   |         10698 |        4.88 | `[]`        |
| JNJ    |   True    |               2026-05-04   |               2026-05-14   |         16200 |        6.86 | `[]`        |
| LLY    |   True    |               2026-05-04   |               2026-05-14   |         13601 |        5.97 | `[]`        |
| MO     |   True    |               2026-05-04   |               2026-05-14   |         16200 |        6.86 | `[]`        |
| PRA    |   True    |               2026-05-04   |               2026-05-14   |          8736 |        4.16 | `[]`        |
| PRGO   |   True    |               2026-05-04   |               2026-05-14   |          8663 |        4.31 | `[]`        |

Notes:

  * SPY was already at `2026-05-12` (Phase 6I-11 had refreshed it once
    before). The other 13 tickers were stale by approximately 10 days.
  * Every ticker advanced to `cache_date_range_end == 2026-05-14`,
    matching the supplied `--current-as-of-date`.
  * Every ticker carries `refreshed=True`, `stale_before=True`,
    `current_after=True`, `issue_codes=[]`, with non-zero yfinance row
    counts and small (< 7 s) elapsed times.

---

## 4. Part C — Post-refresh evidence

### 4.1. Production-root snapshot diff (pre → post 14 refreshes)

The full diff lives at `_phase_6i_44_diff_report.txt`. Summary:

| Root                          | Modified files | Added | Removed |
|-------------------------------|---------------:|------:|--------:|
| `cache/results`               |             28 |     0 |       0 |
| `cache/status`                |             14 |     0 |       0 |
| `output/research_artifacts`   |              0 |     0 |       0 |
| `output/stackbuilder`         |              0 |     0 |       0 |
| `signal_library/data/stable`  |              0 |     0 |       0 |

The 28 `cache/results` files are exactly the 14 PKLs plus 14
`*.manifest.json` files for the authorized tickers. The 14 `cache/status`
files are exactly the per-ticker `<TICKER>_status.json` files. No file
was added or removed anywhere; the refresher only updates existing
files in place.

Per-ticker change matrix from the diff report:

```
ticker results.modified   status.modified
SPY    2                  1
AROW   2                  1
AWR    2                  1
CLH    2                  1
CP     2                  1
EXPO   2                  1
FCFS   2                  1
GBCI   2                  1
HCSG   2                  1
JNJ    2                  1
LLY    2                  1
MO     2                  1
PRA    2                  1
PRGO   2                  1
TEF    0                  0
```

**TEF: 0 / 0. TEF is provably untouched.**

### 4.2. Post-refresh cache cutoff watcher (all 15 tickers)

`_phase_6i_44_post_cutoff_watcher.json` summary:

| Ticker | `cache_date_range_end` | `cache_ahead_of_cutoff` | `cache_equal_to_cutoff` | `cache_behind_cutoff` |
|--------|:----------------------:|:-----------------------:|:-----------------------:|:---------------------:|
| SPY    |       2026-05-14       |          False          |          **True**       |         False         |
| AROW   |       2026-05-14       |          False          |          **True**       |         False         |
| AWR    |       2026-05-14       |          False          |          **True**       |         False         |
| CLH    |       2026-05-14       |          False          |          **True**       |         False         |
| CP     |       2026-05-14       |          False          |          **True**       |         False         |
| EXPO   |       2026-05-14       |          False          |          **True**       |         False         |
| FCFS   |       2026-05-14       |          False          |          **True**       |         False         |
| GBCI   |       2026-05-14       |          False          |          **True**       |         False         |
| HCSG   |       2026-05-14       |          False          |          **True**       |         False         |
| JNJ    |       2026-05-14       |          False          |          **True**       |         False         |
| LLY    |       2026-05-14       |          False          |          **True**       |         False         |
| MO     |       2026-05-14       |          False          |          **True**       |         False         |
| PRA    |       2026-05-14       |          False          |          **True**       |         False         |
| PRGO   |       2026-05-14       |          False          |          **True**       |         False         |
| TEF    |       2026-01-28       |          False          |          False          |         **True**      |

The 14 authorized tickers are now at `cache_equal_to_cutoff=True` (the
strict-greater predicate is still not satisfied because that would
require a 2026-05-15 trading day, which does not exist yet). TEF
remains at its prior cache end of `2026-01-28`, untouched.

### 4.3. Post-refresh Phase 6I-43 policy v2 (with `--allow-equal-cutoff-after-close`)

`_phase_6i_44_post_policy_v2.json` summary:

```
refresh_candidate_ready  = True
invalid_tickers          = ['TEF']
counts_by_classification = {
    'source_equal_cutoff_publishable': 14,
    'invalid_or_delisted':              1,
}
```

Every non-TEF ticker classifies as `source_equal_cutoff_publishable`.
TEF classifies as `invalid_or_delisted` with reason
`provider_fetch_failed_zero_rows` and is excluded from the candidate
commands. Under the explicit `--allow-equal-cutoff-after-close` policy
switch, the universe is publishable; under the default strict-greater
predicate it would be `source_equal_cutoff_wait`. Both outcomes are
operator-controlled.

### 4.4. Phase 6I-32 fresh-staging readiness verdict

`_phase_6i_44_post_phase6i32.json` summary:

```
state                   = source_not_ready
recommended_next_action = refresh_source_cache
issue_codes             = [
    'source_cache_not_ready',
    'adapter_not_full_grid',
    'payload_not_ready',
    'patch_plan_not_ready',
]
source_cache_ready      = False
sandbox_build_attempted = True
sandbox_build_written   = 75
sandbox_build_failed    = 0
promotion_plan_ready    = True
production_root_diff    = 0/0/0 across all 5 roots
```

This is consistent with the Phase 6I-32 contract — the harness gates on
the strict-greater cache predicate (`cache_ahead_of_cutoff`), which
remains False because the universe is at equal-cutoff, not strictly
ahead. Sandbox builder still produces 75/0; promotion planner still
reports `plan_ready=True`. The downstream chain (adapter / payload
builder / patch planner / patch writer dry-run) gates on
source-cache-ready, so it reports the four issue codes above. Phase
6I-32 does NOT honour `--allow-equal-cutoff-after-close`; that policy
switch lives only on Phase 6I-43. Phase 6I-32 production-root diff is
0/0/0 across all 5 roots — Phase 6I-32 is read-only as merged.

### 4.5. Phase 6I-42 board overlay / static renderer smoke

Two smoke runs of `confluence_static_board_renderer.py` against the
SPY K-universe with `--current-as-of-date 2026-05-14`:

  * **Without overlays** — wrote `_phase_6i_44_board.html` (52,164
    bytes). rc=0. Empty stderr.
  * **With Phase 6I-42 local overlays** — `--with-local-overlays`
    against the four production roots. Wrote
    `_phase_6i_44_board_with_overlays.html` (52,164 bytes). rc=0.
    Empty stderr.

Both renderer invocations are read-only and produced clean output. The
static board renders the post-refresh state.

### 4.6. Final production-root snapshot (after all post-refresh evidence)

`_phase_6i_44_final_snapshot.json` diffed against the post-refresh
snapshot from § 4.1:

```
PRE  counts: 3239 / 1634 / 35 / 5223 / 72899
POST counts: 3239 / 1634 / 35 / 5223 / 72899

cache/results:               modified 0  added 0  removed 0
cache/status:                modified 0  added 0  removed 0
output/research_artifacts:   modified 0  added 0  removed 0
output/stackbuilder:         modified 0  added 0  removed 0
signal_library/data/stable:  modified 0  added 0  removed 0
```

**Every probe run after the 14 authorized refresh commands was
read-only.** The cutoff watcher, the Phase 6I-43 policy v2 verdict, the
Phase 6I-32 readiness harness (with its in-process sandbox builder
targeting `_phase_6i_44_staged_sandbox/`), and both static-board renders
left every production root untouched.

---

## 5. Authorization scope reconciliation

| Surface                                                       | Authorized? | Touched in this phase? |
|---------------------------------------------------------------|:-----------:|:----------------------:|
| `cache/results` (14 non-TEF tickers: PKL + manifest)          |   **Yes**   |     **Yes (14 × 2)**   |
| `cache/status` (14 non-TEF tickers: status.json)              |   **Yes**   |       **Yes (14)**     |
| `cache/results` (TEF) / `cache/status` (TEF)                  |     No      |          **No**        |
| `output/research_artifacts`                                   |     No      |          **No**        |
| `output/stackbuilder`                                         |     No      |          **No**        |
| `signal_library/data/stable`                                  |     No      |          **No**        |
| `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` env var     |     No      |    **No (never set)**  |
| Confluence patch writer (`multiwindow_k_confluence_patch_writer`) |  No     |          **No**        |
| Signal-library promotion writer (`signal_library_stable_promotion_writer`) | No |     **No**        |
| `confluence_pipeline_runner`                                  |     No      |          **No**        |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster batch | No  |          **No**        |
| `daily_board_automation_writer`                               |     No      |          **No**        |

Every authorized surface was used. No unauthorized surface was touched.
The refresher's own write guard is `--write` plus its internal optimizer
/ provenance guards; the `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`
two-key gate (which applies to LATER guarded writer surfaces, not the
refresher CLI) was not set and is not part of the refresher contract.

---

## 6. Forward state

Post-Phase-6I-44 SPY K-universe condition:

  * 14 non-TEF ticker caches now at `cache_date_range_end == 2026-05-14`
    (== resolved `current_as_of_date`); 14 `cache_status.json` files
    refreshed in place.
  * TEF still flagged `invalid_or_delisted` by Phase 6I-43 policy v2;
    yfinance still reports zero rows on TEF; TEF cache + status
    untouched at their prior `2026-01-28` end.
  * Phase 6I-43 policy v2 verdict with `--allow-equal-cutoff-after-close`
    remains **publishable** (14/1).
  * Phase 6I-43 policy v2 verdict at the default (strict-greater)
    setting would be `source_equal_cutoff_wait`.
  * Phase 6I-32 strict-greater readiness still reports
    `source_not_ready` (correctly — strict-greater is not satisfied at
    equal-cutoff). Sandbox + promotion planner ready; downstream chain
    still gated by source cache.
  * No downstream production write (Confluence patch writer, promotion
    writer, pipeline runner, board automation writer) has been
    authorized in this phase, and none was performed.

The next operational event that would change the strict-greater
predicate is yfinance publishing the 2026-05-15 trading-day data (or
later). Until then, any downstream write would need an explicit
operator authorization to use the publishable-at-equal-cutoff path,
which is a SEPARATE phase from this one.

---

## 7. Evidence artifact index (working-tree, not committed)

| Artifact path                                       | Purpose                                                  |
|-----------------------------------------------------|----------------------------------------------------------|
| `_phase_6i_44_preflight.json`                       | Pre-refresh Phase 6I-43 policy v2 verdict.               |
| `_phase_6i_44_preflight.stderr`                     | yfinance "possibly delisted" warning on TEF.             |
| `_phase_6i_44_refresh.stderr`                       | Initial rc=2 argparse failure of the broken `--tickers` shape. |
| `_phase_6i_44_refresh.stdout`                       | Empty stdout from the rc=2 failure.                      |
| `_phase_6i_44_refresh_<TICKER>.stdout` × 14         | Per-ticker refresh JSON outcome.                         |
| `_phase_6i_44_refresh_<TICKER>.stderr` × 14         | All 0 bytes.                                             |
| `_phase_6i_44_pre_snapshot.json`                    | Pre-refresh production-root snapshot.                    |
| `_phase_6i_44_post_snapshot.json`                   | Post-refresh production-root snapshot.                   |
| `_phase_6i_44_diff_report.txt`                      | Pre → post refresh diff.                                 |
| `_phase_6i_44_post_cutoff_watcher.json`             | Post-refresh cache cutoff watcher.                       |
| `_phase_6i_44_post_policy_v2.json`                  | Post-refresh Phase 6I-43 policy v2 verdict.              |
| `_phase_6i_44_post_phase6i32.json`                  | Post-refresh Phase 6I-32 staged-readiness verdict.       |
| `_phase_6i_44_board.html`                           | Phase 6I-42 board (no overlays).                         |
| `_phase_6i_44_board_with_overlays.html`             | Phase 6I-42 board (local overlays).                      |
| `_phase_6i_44_final_snapshot.json`                  | Final production-root snapshot.                          |
| `_phase_6i_44_post_evidence_diff.txt`               | Post-refresh → final diff (proves probes are read-only). |
| `_phase_6i_44_snapshot_tool.py`                     | Snapshot helper (read-only, working-tree).               |
| `_phase_6i_44_diff_tool.py`                         | Diff helper (read-only, working-tree).                   |

These working-tree files are intentionally NOT committed. They are
intermediate evidence files that this document summarizes; the
authoritative record of the phase is this markdown plus the module +
test changes.
