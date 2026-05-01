# Phase 1A Baseline Coverage

Document date: 2026-04-30
Branch: phase-1a-baseline-lock
Sprint plan reference:
project/md_library/shared/2026-04-30_PRJCT9_SPRINT_PLAN.md
Algorithm spec reference:
project/md_library/shared/2026-04-30_PRJCT9_ALGORITHM_SPEC_v0_5.md

## 1. Purpose

Phase 1A locks the current "before" state of the engines so that
Phase 1B's canonical-scoring extraction can be evaluated diff-by-diff.
Every change Phase 1B introduces must either:

  - leave a baseline test passing unchanged (no behavior change), or
  - flip exactly one baseline test in a single ledger-attributable
    commit, with the diff classified in the Phase 1B Intentional Delta
    Ledger.

A silent baseline change without an Intentional Delta Ledger entry is
treated as a regression.

## 2. What Phase 1A locks

Phase 1A pins outputs from the currently-callable scoring helpers in
SpyMaster's sister engines using deterministic synthetic fixtures.
Each output is exactly captured via `phase1a_snapshot_utils.freeze`,
which serializes via `float.hex()` and tagged structures. Re-running
the test file produces byte-identical results.

Locked surfaces (Part B inventory results):

  - `stackbuilder.metrics_from_captures`
  - `stackbuilder._combine_signals`
  - `stackbuilder._captures_from_signals`
  - `stackbuilder._combined_metrics`
  - `stackbuilder._combined_metrics_signals`
  - `onepass._metrics_from_ccc`
  - `onepass.calculate_metrics_from_signals`
  - `impactsearch._metrics_from_ccc`
  - `impactsearch.calculate_metrics_from_signals`
  - `impactsearch.export_results_to_excel` (filesystem behavior)
  - `confluence._mp_metrics`
  - `confluence._mp_combine_unanimity_vectorized`
  - `trafficflow._combine_signals`
  - `trafficflow._metrics_like_spymaster` (via in-memory `_PRICE_CACHE`
    injection plus `monkeypatch` of `_load_secondary_prices` to assert
    no fallback fetch)

## 3. Why no live ticker / yfinance data is used for committed baselines

Live data violates determinism. The same call against the same ticker
returns different values across days, sessions, splits, and dividend
adjustments. Network access also fails CI in offline contexts and
introduces an external trust boundary (Yahoo, vendor APIs) into the
test contract.

The committed Phase 1A baselines therefore use only:

  - hand-built `pd.DatetimeIndex` of 10 trading days from January 2024,
  - hand-chosen `Close` values producing specified percent-point
    returns (including a zero-return day),
  - hand-built signal series exercising Buy / Short / None plus a
    zero-capture trigger and at least one losing trigger,
  - hand-built two-primary signal frames for consensus tests,
  - synthetic two-member capture series for combined-metric tests.

Ticker labels in the fixtures (`AAA`, `BBB`, `P1_D`, `P2_D`,
`P2_I`, `P2_muted`) are placeholders only.

## 4. Synthetic fixture summary

  - `DATES`: 10 entries, 2024-01-02 through 2024-01-16 with a single
    weekend gap.
  - `CLOSE`: prices from 100.00 to 101.00 covering the 10 dates;
    explicit zero-return on day 3.
  - `SEC_RETS_PCT`: percent-point returns derived from `CLOSE`.
  - `DF_FOR_RETURNS`: DataFrame with `Close` column, used by the
    *_calculate_metrics_from_signals helpers that recompute returns
    internally.
  - `SIGNALS`: 10-entry signal series exercising Buy/Short/None,
    a zero-capture trigger, and both winning and losing triggers.
  - `CAPTURES_PCT`: pre-applied capture series from `SIGNALS` and
    `SEC_RETS_PCT` for direct metric helpers.
  - `CCC_SERIES`: cumulative running sum of `CAPTURES_PCT` for
    `_metrics_from_ccc` helpers.
  - `ACTIVE_PAIRS_LABELS`: descriptive labels matching `SIGNALS` for
    helpers that prefer explicit signal-mask trigger counting.
  - `MP_SIG_DF_AGREE / DISAGREE / INVERSE / MUTED / ALL_NONE`:
    five two-primary frames covering consensus shapes from the spec.
  - `MEMBER_CAPS_A / MEMBER_CAPS_B`: two synthetic capture series for
    `_combined_metrics`.
  - `MEMBER_SIG_A / MEMBER_SIG_B`: two synthetic signal series with
    overlapping schedules and at least one Buy+Short cancellation,
    for `_combined_metrics_signals`.

## 5. Engine / helper coverage table

| Engine        | Helper                                        | Test                                                                   | Notes                          |
|---------------|-----------------------------------------------|------------------------------------------------------------------------|--------------------------------|
| stackbuilder  | metrics_from_captures                         | test_stackbuilder_metrics_from_captures_baseline                       | mixed-day fixture              |
| stackbuilder  | metrics_from_captures                         | test_stackbuilder_metrics_from_captures_empty_baseline                 | empty Series -> None           |
| stackbuilder  | metrics_from_captures                         | test_stackbuilder_metrics_from_captures_all_none_baseline              | all-zero Series -> None        |
| stackbuilder  | _combine_signals                              | test_stackbuilder_combine_signals_baseline                             | two members, mixed             |
| stackbuilder  | _combine_signals                              | test_stackbuilder_combine_signals_empty_baseline                       | empty list                     |
| stackbuilder  | _captures_from_signals                        | test_stackbuilder_captures_from_signals_baseline                       | percent-point conversion       |
| stackbuilder  | _combined_metrics                             | test_stackbuilder_combined_metrics_baseline                            | two-member captures            |
| stackbuilder  | _combined_metrics_signals                     | test_stackbuilder_combined_metrics_signals_baseline_pending_bug_fix    | KNOWN BUG (see ledger)         |
| onepass       | _metrics_from_ccc                             | test_onepass_metrics_from_ccc_baseline                                 | with active_pairs              |
| onepass       | _metrics_from_ccc                             | test_onepass_metrics_from_ccc_legacy_no_active_pairs_baseline          | legacy non-zero-cap fallback   |
| onepass       | calculate_metrics_from_signals                | test_onepass_calculate_metrics_from_signals_baseline                   | persist_skip_bars=0            |
| impactsearch  | _metrics_from_ccc                             | test_impactsearch_metrics_from_ccc_baseline                            | with active_pairs              |
| impactsearch  | _metrics_from_ccc                             | test_impactsearch_metrics_from_ccc_legacy_no_active_pairs_baseline     | legacy fallback                |
| impactsearch  | calculate_metrics_from_signals                | test_impactsearch_calculate_metrics_from_signals_baseline              | persist_skip_bars=0            |
| impactsearch  | export_results_to_excel (filesystem behavior) | test_impactsearch_export_writes_duplicates_pending_bug_fix             | KNOWN BUG (see ledger)         |
| confluence    | _mp_metrics                                   | test_confluence_mp_metrics_baseline                                    | bars_per_year=252              |
| confluence    | _mp_metrics                                   | test_confluence_mp_metrics_zero_triggers_baseline                      | empty trigger mask -> empty    |
| confluence    | _mp_combine_unanimity_vectorized              | test_confluence_consensus_agreement_baseline                           | unanimous Buy/Short/None       |
| confluence    | _mp_combine_unanimity_vectorized              | test_confluence_consensus_disagreement_baseline                        | mixed Buy/Short days           |
| confluence    | _mp_combine_unanimity_vectorized              | test_confluence_consensus_inverse_baseline                             | post-[I] applied               |
| confluence    | _mp_combine_unanimity_vectorized              | test_confluence_consensus_muted_baseline                               | second primary all None        |
| confluence    | _mp_combine_unanimity_vectorized              | test_confluence_consensus_all_none_baseline                            | both primaries None            |
| trafficflow   | _metrics_like_spymaster                       | test_trafficflow_metrics_like_spymaster_baseline                       | cache-injection + monkeypatch  |
| trafficflow   | _combine_signals                              | test_trafficflow_combine_signals_all_buy_baseline                      | unanimous Buy                  |
| trafficflow   | _combine_signals                              | test_trafficflow_combine_signals_all_short_baseline                    | unanimous Short                |
| trafficflow   | _combine_signals                              | test_trafficflow_combine_signals_mixed_baseline                        | mixed (None per consensus)     |
| trafficflow   | _combine_signals                              | test_trafficflow_combine_signals_all_none_baseline                     | all None                       |

27 baseline tests + 1 import smoke. All green under `spyproject2`.

## 6. Known gaps (and why they are not tested in Phase 1A)

  - **SpyMaster end-to-end scoring path.** The full SpyMaster scoring
    pipeline runs through long-running precompute + cache I/O + Dash
    callbacks. There is no safely callable pure-helper surface that
    exercises canonical scoring without standing up the cache. Phase
    1A does not stand up that cache; Phase 2 will own a parity suite
    that does.
  - **TrafficFlow scoring path.** Now covered (per Codex audit
    amendment, 2026-05-01): `_combine_signals` is exercised
    directly with synthetic primary signal series, and
    `_metrics_like_spymaster` is exercised by preloading
    `trafficflow._PRICE_CACHE['SYN']` with a synthetic Close
    DataFrame inside a pytest fixture, while `monkeypatch` replaces
    `trafficflow._load_secondary_prices` with a function that
    raises `AssertionError` if called. The fixture removes the cache
    key in `finally` so module state stays clean across tests. The
    full StackBuilder leaderboard / Spymaster pkl loading chain
    remains out of scope; it belongs to Phase 2's parity suite.
  - **Confluence end-to-end multi-timeframe scrub.** Out of Phase 1A
    scope; Phase 4 deliverable.
  - **StackBuilder full Phase-2-vs-Phase-3 reconstruction.** The full
    pipeline requires the file/cache layout the engines produce on a
    real run. Phase 1A explicitly does not stand that up; instead it
    pins the closest callable surface (`_combined_metrics_signals`)
    and references the artifact-confirmed mismatch (Section 7 below).
  - **Lookahead-leak audits.** Phase 2 deliverable. The Phase 1A
    fixtures are too small to meaningfully test temporal alignment
    invariants.
  - **Synthetic dataset with known-correct answers across the full
    pipeline.** Phase 2 deliverable.

## 7. StackBuilder artifact-confirmed mismatch summary

Codex independently sampled 10 `combo_k=1.json` files from local
StackBuilder output folders. In every sampled folder, the K=1 result
disagreed with the corresponding `rank_direct` / `rank_inverse`
top-row metrics. The two phases score through different paths and
settings, producing inconsistent answers for what should be the same
question.

The v0.5 spec appendix names this as a Phase 1 fix. The artifact-
level confirmation completed by Codex is the binding evidence; Phase
1A does not re-run it, because the artifact path requires the
StackBuilder cache layout which Phase 1A explicitly does not stand
up. Instead, Phase 1A pins `_combined_metrics_signals` as the
closest callable surface, marks the test as `_pending_bug_fix`, and
points at the ledger entry that will retire it after Phase 1B.

## 8. Known-bug baselines and pending Phase 1B ledger entries

Two tests pin currently-buggy behavior. Each carries a leading
comment block referencing the Phase 1B Intentional Delta Ledger
entry that will retire it.

  - `test_stackbuilder_combined_metrics_signals_baseline_pending_bug_fix`
    -> Pending ledger entry: "StackBuilder Phase 2 vs Phase 3
    scoring divergence" (BUG-FIX). Phase 1B replaces this snapshot
    after canonical scoring unification.
  - `test_impactsearch_export_writes_duplicates_pending_bug_fix`
    -> Pending ledger entry: "ImpactSearch xlsx duplicate-row dedupe"
    (BUG-FIX). The current export reads any existing xlsx and
    concatenates new rows on top, producing duplicates on repeat
    invocation. Phase 1B replaces this snapshot after the dedupe
    fix lands.

All other Phase 1A tests pin correct-by-spec or
correct-by-current-behavior outputs and do not carry the suffix.

## 9. How Phase 1B should use these baselines

Recommended Phase 1B working order:

  1. Land the canonical-scoring extraction without modifying any
     test in `test_phase1a_baseline_lock.py`. Run pytest. Any test
     that flips is a candidate for either:
       - immediate engine-side reversion if the diff is
         unintentional, or
       - one Intentional Delta Ledger entry if intentional.
  2. For each ledger entry, the diff is replaced via a single
     commit that updates the corresponding snapshot constant in
     `phase1a_baseline_snapshots.py`. The commit message references
     the ledger entry name.
  3. The two `_pending_bug_fix` tests are expected to flip.
     Their replacements MUST land alongside the corresponding
     bug-fix engine commits and reference the ledger.
  4. After Phase 1B, the suffix `_pending_bug_fix` is removed from
     test names; any remaining `_pending_bug_fix` test signals
     unfinished Phase 1B work.

The Intentional Delta Ledger is the public record of every diff
classified as intentional. Phase 1B opens that ledger as a tracked
markdown alongside its PR; Phase 1A does not pre-create it.

## 10. Exactness policy: float.hex(), no pytest.approx

  - Floats are pinned via `float.hex()`. This serializes the exact
    IEEE-754 bit pattern; round-tripping reproduces the same float.
  - `pytest.approx` is not used for any baseline assertion in this
    PR. Tolerance-based comparisons hide drift; Phase 1B is
    explicitly about classifying every diff, including a least-
    significant-bit drift.
  - NaN, +inf, -inf are tagged with stable canonical labels in the
    freeze function so round-tripping does not depend on `==`
    semantics for non-finite floats.
  - Containers (dict, list, tuple, pandas Series, pandas DataFrame,
    numpy ndarray) are recursively normalized; dict keys are sorted
    by `repr` for stable ordering across Python builds.
  - Re-running pytest produces byte-identical output. Three
    back-to-back runs were verified during Phase 1A authoring.

## Phase 1B planning notes

Pending Intentional Delta Ledger entries (drafted in Phase 1A; the
ledger document itself is created in Phase 1B and references these
entries):

  - **StackBuilder Phase 2 vs Phase 3 scoring divergence** (BUG-FIX).
    Closest callable surface pinned by
    `test_stackbuilder_combined_metrics_signals_baseline_pending_bug_fix`.
    Codex's prior 10-folder artifact verification is the binding
    evidence; canonical-scoring unification eliminates the divergence
    by construction.
  - **ImpactSearch xlsx duplicate-row dedupe** (BUG-FIX). Pinned by
    `test_impactsearch_export_writes_duplicates_pending_bug_fix`.
    Current `export_results_to_excel` reads any existing xlsx and
    concatenates new rows on top, producing duplicates on repeat
    invocation.
  - **Zero-capture trigger-day counting** (BUG-FIX).
      - Old: some paths (notably
        `stackbuilder.metrics_from_captures`,
        `trafficflow._metrics_like_spymaster`, and the legacy
        non-`active_pairs` fallback in `onepass._metrics_from_ccc`
        and `impactsearch._metrics_from_ccc`) count triggers via
        `captures != 0`, dropping zero-capture trigger days.
      - New: triggers counted by signal state (`Buy` or `Short`);
        zero-capture trigger days count as losses per spec §15.
        The `active_pairs`-aware path in `_metrics_from_ccc` and
        the consensus-driven path in `confluence._mp_metrics`
        already align with the new convention.

Other planning notes:

  - **Confluence.py price_basis cleanup.** Remove price_basis
    args/plumbing entirely rather than preserving a constant
    `'close'` compatibility marker. Existing caches should be
    rebuilt if needed; the spec says no Adj/raw selector remains.
    (Confluence.py is not edited in Phase 1A.)
  - **Capture averaging is a search heuristic, not canonical
    scoring.** `stackbuilder._combined_metrics` averages member
    capture series; this is appropriate for K-search ranking but
    is not the canonical multi-primary scoring rule. Canonical
    multi-primary scoring uses signal consensus per spec §18 (the
    helper to use is `_combined_metrics_signals`, which combines
    signals first and then computes captures from the consensus).
    Phase 1B should prefer `_combined_metrics_signals` over
    `_combined_metrics` for canonical K scoring; the K-search
    heuristic remains a separate code path.
