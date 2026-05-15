# Phase 6I-43: source-refresh policy v2 + invalid-member handling plan

**Branch:** `phase-6i-43-source-refresh-policy-v2`

## Amendment-1 summary (Codex audit response)

One focused fix: the candidate command now uses the
**pinned spyproject2 audit interpreter path** as its
first token instead of bare `python`. Bare `python` on
this machine can resolve to the wrong environment (e.g.
`C:\Python313`) instead of the project audit interpreter,
so operator-copy commands must name the pinned
interpreter explicitly.

Changes:

- Added `PINNED_PYTHON_INTERPRETER = "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe"`
  to `signal_library_source_refresh_policy_v2.py`.
- `_build_refresh_candidate_command()` now emits the
  pinned path as `argv[0]`; the joined string starts
  with the pinned path followed by the rest of the
  refresher CLI.
- 3 new amendment-1 tests pin the contract:
  `test_amendment1_pinned_interpreter_constant_exposed`,
  `test_amendment1_candidate_command_uses_pinned_interpreter`,
  `test_amendment1_pinned_interpreter_with_invalid_excluded`.
- All previous contracts unchanged: TEF still excluded,
  `--write` still present, no `PRJCT9_AUTOMATION_WRITE_AUTH`
  wording.

Total test count: 27 (24 original + 3 amendment-1). Full
focused suite: **165 passed in 4.87s**.

## 1. ELI5: equal-cutoff vs strict-greater

The Phase 6I-33 readiness coordinator's strict rule
required `new_cache_date_range_end > current_as_of_date`
before a supervised refresh could be authorized. The
Phase 6I-38 evidence run exposed a policy ambiguity: 14
SPY-K-universe tickers had yfinance data through the
cutoff (`new == cutoff`), but the strict-greater
predicate forbade the refresh.

The operator question Phase 6I-38 surfaced:

> *"Should stale cache be allowed to refresh to the
> target cutoff after market close when the provider has
> that target bar?"*

Phase 6I-43 makes this an **explicit operator policy
switch** on the planner — `allow_equal_cutoff_after_close`
(default `False`):

- `False` → preserves Phase 6I-33 strict-greater
  behavior. `new == cutoff` classifies as
  `source_equal_cutoff_wait`. `refresh_candidate_ready=False`.
- `True` → `new == cutoff` classifies as
  `source_equal_cutoff_publishable` and can drive
  `refresh_candidate_ready=True`.

Phase 6I-43 does NOT change the default behavior. It
EXPOSES the policy switch and adds an invalid-member
handling layer; the operator chooses when to flip the
switch.

## 2. Why TEF is treated as invalid / delisted

The Phase 6I-38 evidence run reported TEF's yfinance
telemetry as:

```
fetch_attempted = true
fetch_succeeded = false
rows = 0
new_cache_date_range_end = null
upstream warning: "possibly delisted; no price data found"
```

This pattern has persisted across Phase 6I-33 → 6I-38 →
6I-42 — TEF is not a transient hiccup. Phase 6I-43 adopts
the operator-already-implicit rule: TEF is invalid.

Detection rule
--------------

A ticker classifies as `invalid_or_delisted` when ANY of:

1. `provider_fetch_telemetry.fetch_attempted=True` AND
   `fetch_succeeded=False` AND `rows ∈ (0, None)` AND
   `new_cache_date_range_end=None`.
2. Any of `error / warning / status / message /
   provider_status / telemetry_message` carries one of
   the case-insensitive substrings: `possibly_delisted`,
   `possibly delisted`, `delisted`, `no_data_found`,
   `no data found`, `symbol_may_be_delisted`,
   `symbol may be delisted`, `yfinance_possibly_delisted`,
   `ticker_not_found`.

Invalid-policy modes
--------------------

| `invalid_ticker_policy` | Behavior |
|---|---|
| `warn_and_exclude` (default) | Invalid tickers surface in `invalid_tickers` + `warning_members`, classify as `invalid_or_delisted`, and are EXCLUDED from the refresh-candidate command. |
| `raise` | Invalid tickers classify as `manual_review_required` with the invalid reason in `notes`; the operator must explicitly decide before the planner emits a candidate command. |

Either way, **invalid tickers are never silently dropped**.
The data-completeness flow on the website board surfaces
them as warning rows so a downstream renderer can show the
`!` symbol.

## 3. Stable v2 classification taxonomy

| Class | Trigger |
|---|---|
| `cache_already_ready` | `cache_ahead_of_cutoff=True` — cache strictly ahead, no refresh needed |
| `source_strictly_ahead_refreshable` | `source_ahead_of_cutoff=True` — refresh would advance the predicate |
| `source_equal_cutoff_wait` | `source_equal_to_cutoff=True` AND `allow_equal_cutoff_after_close=False` |
| `source_equal_cutoff_publishable` | `source_equal_to_cutoff=True` AND `allow_equal_cutoff_after_close=True` |
| `source_behind_or_error` | source behind cutoff OR provider fetch failed (without an invalid signal) |
| `invalid_or_delisted` | provider telemetry signals invalid (see § 2) and `invalid_ticker_policy="warn_and_exclude"` |
| `manual_review_required` | catch-all for unclassifiable states / missing probes / invalid signal under `policy="raise"` |

## 4. `refresh_candidate_ready` rule

The aggregate verdict is `True` ONLY when:

- Every non-invalid ticker classifies as one of
  `cache_already_ready / source_strictly_ahead_refreshable /
  source_equal_cutoff_publishable`.
- All invalid tickers are explicitly classified and
  excluded under `invalid_ticker_policy="warn_and_exclude"`.
- No `manual_review_required` classifications exist for
  any ticker (invalid or otherwise).
- At least one ticker is in the universe AND at least one
  non-invalid ticker exists.

## 5. Exact refresh candidate command rules

The planner emits a candidate command only when
`refresh_candidate_ready=True` AND at least one non-invalid
ticker classifies as `source_strictly_ahead_refreshable`
OR `source_equal_cutoff_publishable` (i.e., there is real
work to do — already-cache-ready tickers are excluded
because refreshing them would be a no-op).

### Authorization correction (carried forward)

The Phase 6E-5 refresher CLI
`signal_engine_cache_refresher.py` uses the explicit
`--write` flag plus its internal optimizer / provenance
write guards. It does **NOT** use the
`PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` two-key
gate.

The env-var gate applies to later guarded writer
surfaces (Phase 6I-25 Confluence patch writer,
Phase 6I-31 promotion writer, Phase 6H-5 daily-board
automation writer) — NOT the refresher CLI.

The emitted command carries `--write` ONLY. No
`PRJCT9_AUTOMATION_WRITE_AUTH` wording.

### Candidate command shape (amendment-1: pinned interpreter)

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
  signal_engine_cache_refresher.py \
  --tickers <non-invalid CSV> \
  --cache-dir <cache_dir> \
  --current-as-of-date <YYYY-MM-DD> \
  --write
```

The first token is the **pinned spyproject2 audit
interpreter path**, NOT bare `python`. Bare `python` on
this machine can resolve to the wrong environment (e.g.
`C:\Python313`), which is not the project audit interpreter
and cannot use the SciPy 1.13.1 wheel set the
baseline-lock contract depends on. Operator-copy commands
must therefore name the pinned interpreter explicitly.

Invalid tickers like TEF are excluded from the `--tickers`
list. The planner NEVER runs the refresher; the operator
must invoke it in a separate supervised session.

## 6. Current production evidence (Phase 6I-43 read-only run)

A real read-only probe of the SPY K-universe (15 tickers)
on the pinned interpreter
(`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`)
with `--allow-equal-cutoff-after-close --current-as-of-date 2026-05-14`:

```
refresh_candidate_ready: True
counts_by_classification: {
  source_equal_cutoff_publishable: 14,
  invalid_or_delisted: 1
}
invalid_tickers: ['TEF']
refresh_candidate_tickers: [
  'SPY', 'AROW', 'AWR', 'CLH', 'CP', 'EXPO', 'FCFS',
  'GBCI', 'HCSG', 'JNJ', 'LLY', 'MO', 'PRA', 'PRGO'
]
blocker_reasons: []
refresh_candidate_command:
  "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
    signal_engine_cache_refresher.py \
    --tickers SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO \
    --cache-dir cache/results \
    --current-as-of-date 2026-05-14 \
    --write
```

Under the **default policy** (`--allow-equal-cutoff-after-close` NOT set), the same probe returns
`refresh_candidate_ready=False` — the strict-greater rule is preserved by default.

### Production-root snapshot (probe was read-only)

| Root | Count |
|---|---|
| `cache/results` | 3239 |
| `cache/status` | 1634 |
| `output/research_artifacts` | 35 |
| `output/stackbuilder` | 5221 |
| `signal_library/data/stable` | 72899 |
| **Total** | **83028** |

Identical to the Phase 6I-38 / 6I-42 baseline. No
production write occurred.

## 7. Decision tree for the operator

The planner does NOT execute the refresher. The operator
must:

1. Decide whether to set `--allow-equal-cutoff-after-close`. The Phase 6I-38 evidence doc
   describes the policy question.
2. Decide on TEF: confirm delisting; then drop TEF from
   the K-universe permanently OR pin TEF's evaluation
   cutoff to `2026-01-28` (its last available trading
   date) OR replace TEF with a valid ticker. Phase
   6I-30 / 6I-32 sandbox proofs already accommodate the
   TEF cutoff of `2026-01-28`, so a SPY-pilot refresh
   that excludes TEF is technically feasible.
3. If `refresh_candidate_ready=True`, the operator runs
   the emitted candidate command in a SEPARATE
   supervised session.
4. After the supervised refresh writes to
   `cache/results/`, the Phase 6I-31 promotion writer
   moves the new signal library; the Phase 6I-25
   Confluence patch writer then writes the Phase 6I-20
   multi-window fields onto the production Confluence
   artifact; the board flips to `eligible_count > 0`.

Each of these is a separate operator-authorized step
gated by its own writer's `--write` flag (and, for the
Phase 6I-25 / 6I-31 / 6H-5 paths, the
`PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`
two-key gate).

## 8. Tests (24 new + 138 existing focused tests = 162 total)

New file:
`project/test_scripts/test_signal_library_source_refresh_policy_v2.py`.

| # | Name | Pins |
|---|---|---|
| 1 | `test_source_strictly_ahead_classifies_refreshable` | source > cutoff -> source_strictly_ahead_refreshable + ready=True |
| 2 | `test_source_equal_cutoff_defaults_to_wait` | source == cutoff + default policy -> source_equal_cutoff_wait + ready=False |
| 3 | `test_source_equal_cutoff_with_policy_becomes_publishable` | source == cutoff + `allow_equal_cutoff_after_close=True` -> source_equal_cutoff_publishable + ready=True |
| 4 | `test_tef_style_delisted_telemetry_classifies_invalid` | "possibly delisted" warning -> invalid_or_delisted; TEF in invalid_tickers + warning_members |
| 5 | `test_invalid_telemetry_zero_rows_no_new_date_classifies_invalid` | Pure zero-rows-no-new-date signal also classifies invalid |
| 6 | `test_invalid_ticker_excluded_from_candidate_command` | TEF excluded from emitted candidate command and `refresh_candidate_tickers` |
| 7 | `test_invalid_surfaces_in_warning_members` | warning_members carries the ticker + reason + classification |
| 8 | `test_ready_false_when_equal_cutoff_not_allowed` | ready=False default; no candidate command emitted |
| 9 | `test_ready_true_with_policy_and_invalid_excluded` | 14 non-TEF tickers + TEF + policy=ON -> ready=True; 14 tickers in candidate command, TEF excluded |
| 10 | `test_candidate_command_has_no_auth_env_var_wording` | Emitted command + argv have no `PRJCT9_AUTOMATION_WRITE_AUTH` / `phase_6h5_explicit` substring; `--write` IS present |
| 11 | `test_candidate_command_includes_cache_dir_and_cutoff` | `--cache-dir / --current-as-of-date` baked into the command |
| 12 | `test_cache_already_ahead_classifies_short_circuit` | cache strictly ahead -> cache_already_ready; no candidate command; ready=True |
| 13 | `test_source_state_missing_yields_manual_review` | Missing source state -> manual_review_required; ready=False |
| 14 | `test_unknown_invalid_policy_raises_value_error` | Unknown `invalid_ticker_policy` raises ValueError |
| 15 | `test_probe_exception_degrades_gracefully` | Both probes raising at runtime -> manual_review_required (no crash) |
| 16 | `test_report_to_json_dict_round_trips` | Report round-trips through json.dumps |
| 17 | `test_cli_emits_report_json` | CLI rc=0 + JSON output |
| 18 | `test_cli_missing_tickers_returns_rc_2` | rc=2 on empty tickers |
| 19 | `test_cli_unknown_invalid_policy_returns_rc_2` | rc=2 on unknown policy |
| 20 | `test_module_no_forbidden_top_level_imports` | No yfinance / dash / live engine / writer / pipeline_runner |
| 21 | `test_module_no_raw_pickle_load` | No raw `pickle.load` |
| 22 | `test_module_no_write_true_kwarg_anywhere` | No `write=True` kwarg |
| 23 | `test_module_no_yfinance_import_anywhere` | No yfinance imports |
| 24 | `test_module_no_prjct9_automation_write_auth_in_emitted_command` | Functional check: emitted command + argv + JSON serialization carry no env-var wording |

## 9. Validation run

Pinned conda interpreter
(`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`):

- `py_compile project/signal_library_source_refresh_policy_v2.py` → clean.
- `py_compile project/test_scripts/test_signal_library_source_refresh_policy_v2.py` → clean.
- `pytest test_scripts/test_signal_library_source_refresh_policy_v2.py -q` → **24 passed in 1.73s**.
- `pytest <focused suite> -q` → **162 passed in 5.05s** (24 policy-v2 + Phase 6I-33 readiness + Phase 6I-32 staging readiness + Phase 6I-42 overlays + Phase 6I-41 renderer + Phase 6I-40 sortable + static regression).
- `git diff --check`: clean.
- B12 raw-pickle regression guard still passes.

## 10. SPY pilot status (still PARKED)

The planner exposing `refresh_candidate_ready=True` does
NOT unpark SPY. The operator must still:

- (a) consciously set `--allow-equal-cutoff-after-close`,
- (b) decide on TEF (drop / pin / replace),
- (c) invoke the emitted refresher command in a SEPARATE
  supervised session,
- (d) drive the Phase 6I-31 promotion writer,
- (e) drive the Phase 6I-25 Confluence patch writer,

— in that order, with each step independently authorized.

This phase only prepares an honest plan. SPY remains
PARKED.

## 11. No-production-activity confirmation

- No writer `--write` invocation (any writer).
- `PRJCT9_AUTOMATION_WRITE_AUTH` never read or set.
- No source refresh in write mode (no
  `signal_engine_cache_refresher.py --write` invocation).
  The candidate command is a STRING; the planner never
  runs it.
- The internal source-availability probe dry-runs the
  Phase 6E-5 refresher with `write=False` (the
  established Phase 6I-15 / 6I-33 read-only pattern).
- No production promotion
  (`signal_library_stable_promotion_writer`).
- No Confluence patch writer
  (`multiwindow_k_confluence_patch_writer`).
- No `confluence_pipeline_runner` invocation.
- No StackBuilder / OnePass / ImpactSearch / TrafficFlow /
  Spymaster batch execution.
- No production data write.
- Production-root snapshot identical pre- and post-probe
  (83,028 files unchanged).
- Production `signal_library/data/stable/` untouched.
- Production `output/research_artifacts/` untouched.
