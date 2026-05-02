# Phase 1B Intentional Delta Ledger

Document date: 2026-05-01
Branches: phase-1b-2a-canonical-rewire (PR #132, merged),
          phase-1b-2b-backlog (PR #133).
Status: 1B-2A merged; 1B-2B backlog cleanup in flight.
  - Entries 1-5 + 10: implemented in 1B-2A (PR #132).
  - Entries 6-9: implemented in 1B-2B (PR #133).
  - 1B-2B-1, 1B-2B-2, 1B-2B-3: implemented in 1B-2B (PR #133)
    as backlog-cleanup entries below.
  - QC clone Adj Close sites: still deferred per scope notes.

Per-entry status:
  - Entry 1 (Adj Close removal): implemented across signal_library,
    stale_check, spymaster, onepass, impactsearch, stackbuilder,
    confluence, and impact_fastpath. QC clone deferred.
  - Entry 2 (ddof=1 at spymaster.py:11668): implemented in commit
    56b0338; the inline override was subsequently replaced by
    canonical-scoring delegation in amendment 1 commit 4c61bce
    (behaviour preserved).
  - Entry 3 (cdf -> sf p-value): implemented across all 15 engine
    canonical-scoring p-value sites.
  - Entry 4 (zero-capture trigger-day counting): implemented at
    the four enumerated sites. The single-arg
    `stackbuilder.metrics_from_captures(captures)` deprecated
    fallback retains the legacy `captures.ne(0.0)` mask.
  - Entry 5 (Phase 2 vs Phase 3 scoring divergence): calendar-policy
    unification implemented in commit 033aa93; the
    `_pending_bug_fix` test was retired alongside the Entry 4
    zero-capture fix.
  - Entry 6 (ImpactSearch xlsx duplicate-row dedupe): implemented
    in 1B-2B (PR #133).
  - Entry 7 (calendar grace days default unification to 10):
    implemented in 1B-2B (PR #133). Phase 2/3 path unification
    landed in Entry 5 (1B-2A); the default value flip to 10 and
    the run_for_secondary force-to-zero fix land here.
  - Entry 8 (sentinel pair standardization): implemented in
    1B-2B (PR #133, two-stage: Spymaster streaming-path removal
    + OnePass / TrafficFlow / ImpactSearch sentinel
    canonicalization).
  - Entry 9 (TrafficFlow cache key normalization): implemented
    in 1B-2B (PR #133).
  - Entry 10 (Phase 1A snapshot updates): implemented; see the
    snapshot replacement table in the entry body.

Format used for each entry below:

  - Old behavior: the pre-rewire behavior, cited with file:line.
  - New behavior: the post-rewire behavior, cited with file:line
    and the canonical_scoring API used.
  - Affected tests/snapshots: the Phase 1A tests / snapshots
    flipped by this entry, plus any new Phase 1B-2 tests added.
  - Status: implemented (this PR), or deferred (with target
    sprint).

Each entry also carries an ELI5 explanation so non-engineers can
read the ledger and understand what changed and why.

Reference inventory:
  project/md_library/shared/2026-05-01_PHASE_1B_IMPLEMENTATION_INVENTORY.md

Canonical-scoring delegation amendments (1B-2A, post-32c6242):
  In addition to the formula-law deltas captured in Entries 1–10,
  every engine metric helper in stackbuilder, confluence,
  trafficflow, onepass, impactsearch, and spymaster now routes
  canonical metric scoring through
  `project/canonical_scoring.py` (`score_captures` /
  `score_signals`). Inline Sharpe, t-statistic, p-value, std-dev,
  win-rate, and win/loss counting on capture series have been
  removed from the engines wherever a canonical score is in
  scope (see grep verification in the amendment commit messages).
  Trigger-mask construction and pre-score early-exit guards
  (e.g. `if int(trigger_mask.sum()) == 0: return`) remain inline
  as necessary control flow rather than duplicate math.

  Documented exceptions where inline math is intentionally retained:

    1. `stackbuilder.metrics_from_captures(captures)` single-arg
       fallback at `stackbuilder.py:449` — kept as deprecated
       compatibility-only with `captures.ne(0.0)` as a stand-in
       trigger mask. Canonical callers pass an explicit
       `trigger_mask`. Will be removed once every external
       caller is plumbed with signal info.

    2. Two spymaster Total Capture display sites
       (`Total Capture (%)` in the dynamic-strategy block ~9020
       and `Total %` in the per-ticker secondary block ~11030)
       continue to source the displayed total from
       `cumulative_combined_captures.iloc[-1]` rather than
       `score.total_capture`. These are mathematically equivalent
       for canonical capture series (non-trigger days zeroed)
       and the cumulative-final-value preservation avoids
       changing the displayed total wording.

    3. Max Drawdown, Calmar Ratio, and CAGR-style annualizations
       in spymaster's manual SMA + leader UI blocks. These are
       not canonical-scoring metrics and the canonical module
       does not expose them.

  Bit-equivalence statement (Phase 1A fixtures):
    The synthetic Phase 1A fixtures pinned by
    `test_phase1a_baseline_lock.py` did not move as a result of
    the engine-helper delegation in amendment 1 (verified). The
    spymaster delegation in amendment 1 + amendment 2 covers
    code paths that Phase 1A does not snapshot.

  EXPECTED-BY-SPEC canonicalizations introduced by amendment 2
  (no Phase 1A snapshot pins these paths):

    - Buy-leader / Short-leader Sharpe (spymaster.py ~8800-8870):
      previously computed with a CAGR-style annualization
      `((1 + capture/100) ** (1/total_years)) - 1`. Now uses the
      canonical `avg_daily_capture * 252 - rfr` form per spec
      §16. The displayed Sharpe values in
      `total_capture_buy_leader` / `total_capture_short_leader`
      strings will differ for non-flat strategies.

    - Manual SMA combined-strategy Sharpe (spymaster.py ~10505):
      previously computed std on the FULL combined_returns series
      (including zeros on non-trigger days). Now uses canonical
      std on trigger-day captures only per spec §15-§16. The
      displayed combined Sharpe will differ for strategies with
      non-trigger gaps.

    - Manual SMA per-pair buy/short metrics (spymaster.py ~10309):
      delegation preserves the existing semantics
      (`buy_signals_shifted` / `short_signals_shifted` were
      already used to extract trigger captures); the displayed
      values are bit-equivalent to the pre-amendment behaviour
      for the same trigger mask, only the implementation path
      moves.

    - Next-signal performance-expectation text (spymaster.py
      ~9063): now sourced from the same CanonicalScore as the
      leader Sharpe block, so its avg_daily_capture and
      win_rate values trace directly to canonical scoring.

---

## Entry 1: Adj Close removal

  - Type: EXPECTED-BY-SPEC
  - Old behavior (pre-1B-2A):
      `stale_check.py:79` initialised an `"Adj Close"` column even
      though only `Close` was read.
      `signal_library/multi_timeframe_builder.py` carried a
      module-level `PRICE_BASIS = os.environ.get('PRICE_BASIS',
      'close').lower()` env read, plus an `if price_basis == 'adj'`
      Adj-Close-rename branch in `fetch_interval_data`, plus a
      `'price_source': 'Close' if PRICE_BASIS == 'close' else
      'Adj Close'` library field.
      `signal_library/impact_fastpath.py` derived
      `env_basis = "Adj Close" if os.environ.get("PRICE_BASIS",
      "adj").lower() == "adj" else "Close"` and threaded it into
      `_is_compatible` for library/env basis matching.
  - New behavior (landed in 1B-2A first wave):
      `stale_check.py` now extracts a pure
      `_last_valid_close_from_history(hist, require_posvol)` helper
      that initialises only `Close` and `Volume` columns; an Adj
      Close column in the input no longer influences the result and
      is never substituted for a missing Close.
      `multi_timeframe_builder.fetch_interval_data` drops the
      `price_basis` parameter and always selects `'Close'`. The
      module-level `PRICE_BASIS` env read is removed. The library
      schema field is fixed at `'Close'`.
      `impact_fastpath._is_compatible(lib)` no longer takes a basis
      parameter; it checks the library's `price_source` against the
      canonical `"Close"` literal. The `IMPACTSEARCH_ALLOW_LIB_BASIS`
      escape hatch is preserved.
  - Affected tests/snapshots:
      New: 7 `test_stale_check_close_basis.py` tests covering the
      raw-Close positive path and four Adj-Close-fallback negative
      paths plus volume-filter and edge-case behaviour.
      No Phase 1A snapshot flips. Phase 1A pins synthetic-input
      pure helpers that already operated only on `Close`; the sites
      changed in this wave do not feed those snapshots.
  - Engine-site removals landed in 1B-2A (this commit):
      `spymaster.py`: `_PRICE_BASIS` env read removed; `PRICE_COLUMN`
      hardcoded to `'Close'`; refusal-to-guess column-presence check
      simplified; yfinance call retained `auto_adjust=False` (the
      Adj Close column it produces is now ignored); Adj-Close-fallback
      branches in standardization removed; `results['price_basis']`
      and `results['last_adj_close']` fields removed; the reader
      that previously consulted `last_adj_close` now reads only
      `last_close`.
      `onepass.py`: UI banner hardcoded to `'Close'`;
      `compute_parity_hash` default `price_source='Close'`;
      `save_signal_library` default `price_source='Close'`;
      env-driven price-basis blocks at the previously cited lines
      replaced with `'Close'`; the `ONEPASS_ALLOW_LIB_BASIS` toggle
      that gated library-basis override is removed.
      `impactsearch.py`: boot log echoes `price_basis=Close (raw)`;
      cache-key basis tag is now constant `'close'`; env-driven
      price-basis blocks replaced with `'Close'`; UI banner
      hardcoded to `'Close'`; the `IMPACTSEARCH_ALLOW_LIB_BASIS`
      override branch in metrics path is removed.
      `stackbuilder.py`: `load_secondary_prices` and
      `_fetch_secondary_from_yf` no longer take a `price_basis`
      parameter; the Adj/raw branching in both is removed; the
      `--price-basis` CLI flag and `args.price_basis='adj'` UI
      default are removed; the run-manifest `price_basis` field
      is dropped.
      `confluence.py`: cache-key normalizer no longer sets a
      `price_basis` default; the three `_cached_fetch_interval_data`
      callers no longer pass `price_basis='close'`.
      `signal_library/impact_fastpath.py`: `ALLOW_LIB_BASIS` module
      constant and the `IMPACTSEARCH_ALLOW_LIB_BASIS`-keyed env
      override that bypassed `_is_compatible`'s price-basis check
      are both removed; `_is_compatible` now rejects any library
      whose `price_source != 'Close'` unconditionally. The
      basis-mismatch override loophole is closed.
  - Affected tests/snapshots: no Phase 1A baseline snapshot flips —
    Phase 1A pinned helpers operate on synthetic Close-only inputs
    that do not exercise the env-driven branches removed here. The
    51-test baseline stayed green across this commit.
  - Sites intentionally untouched: parser-helper field-set
    memberships in `onepass.py:1215`, `impactsearch.py:1273, 1488`
    that include `'Adj Close'` as a known yfinance column-name for
    MultiIndex orientation detection (no SELECTION semantics);
    rescale-cols list in `onepass.py:674` (operates harmlessly if
    Adj Close is present in the frame); inline comments noting
    the historical removal in `trafficflow.py:14, 83, 232`,
    `signal_library/multi_timeframe_builder.py:98, 149`, and
    `signal_library/shared_integrity.py:273`. QC clone sites
    (`project/QC/Clone of Project 9/main.py:103, 918, 1509`)
    deferred per scope note in inventory §2a.
  - ELI5: yfinance's "Adj Close" column changes over time as
    dividends and splits get retroactively reapplied, so the same
    historical date can return slightly different prices on
    different days. That kills reproducibility. The spec says use
    raw `Close` only and remove every Adj/raw selector. After this
    entry, every engine reads raw Close and there is no
    `PRICE_BASIS` env var or argument left to tweak.
  - Status: implemented across signal_library, stale_check,
    spymaster, onepass, impactsearch, stackbuilder, confluence,
    and impact_fastpath in 1B-2A. QC clone deferred.

## Entry 2: ddof=0 / implicit ddof -> ddof=1

  - Type: EXPECTED-BY-SPEC
  - Old behavior: at `spymaster.py:11668` the per-trigger-day std
    was computed as `cap[trigger_mask].std() if trigger_days > 1
    else 0`, where `cap` is a NumPy array; NumPy arrays default to
    `ddof=0` (divide by N), so this site silently used population
    std despite spec §16 mandating sample std. Per inventory §5 the
    other implicit-ddof sites in spymaster (1481, 1542, 8873, 8920,
    10605, 12601) compute std on pandas Series, which already
    defaults to `ddof=1`, so they were numerically correct but
    unannotated.
  - New behavior: `spymaster.py:11668` is now
    `cap[trigger_mask].std(ddof=1) if trigger_days > 1 else 0`,
    matching spec §16. A same-line comment marks the site as
    Ledger Entry 2. Implementing commit: 56b0338 ("Phase 1B-2A:
    fix ddof=0 canonical-scoring site in spymaster"). The downstream
    Sharpe / t-stat / p-value chain in that block already gates on
    `std_dev > 0`, so the `trigger_days == 1` case continues to
    short-circuit to the no-stats branch (no behaviour difference
    for that case).
  - Affected tests/snapshots: no Phase 1A baseline snapshot flips —
    Phase 1A does not pin the Spymaster end-to-end path that feeds
    this site, so the 51-test baseline remained green across the
    fix (verified post-56b0338). The other six implicit-but-already-
    ddof=1 pandas Series sites in spymaster do not move
    numerically; they are pending a clarity-only `ddof=1`-explicit
    pass through canonical-scoring wiring. The numeric delta at
    11668 will surface only via the engine-level smoke checks that
    1B-2A wiring will introduce.
  - ELI5: standard deviation has two flavors. "Population" std (ddof
    = 0) divides by N; "sample" std (ddof = 1) divides by N - 1.
    Sample std is the correct choice when the trigger days we
    observed are themselves a sample from the larger universe of
    possible trigger days. The spec mandates ddof = 1 everywhere.
    Most call sites already use ddof = 1 (either explicitly or
    because pandas Series defaults to it); one Spymaster canonical-
    scoring site computes std on a NumPy array and therefore
    silently uses ddof = 0 today. This entry harmonizes that one
    site numerically; the others are clarity-only.
  - Status: implemented for `spymaster.py:11668` (commit 56b0338).
    The 1B-2A canonical-scoring delegation amendment (Commit 5
    of the amendment) routes the metric block at line 11668
    through `canonical_scoring.score_captures` directly; the
    inline `cap[trigger_mask].std(ddof=1)` line is removed in
    favor of the delegation, which always uses `ddof=1` by
    spec §16 default. Behavior is preserved (numerically
    equivalent); the implementation path moves from inline math
    to canonical scoring. The remaining six clarity-only pandas
    Series sites in spymaster (1481, 1542, 8873, 8920, 10605,
    12601) are utility-display sites outside the canonical
    scoring chain and stay as-is.

## Entry 3: cdf -> sf p-value

  - Type: EXPECTED-BY-SPEC
  - Old behavior: every canonical-scoring p-value site computed
    `2 * (1 - stats.t.cdf(abs(t), df))`, the algebraic equivalent
    of the spec's stable form but vulnerable to float subtractive
    cancellation when `cdf(|t|)` is near 1.
  - New behavior: every canonical-scoring p-value site now uses
    `2 * stats.t.sf(abs(t), df))`, matching spec §17. Sites
    converted in 1B-2A: `confluence.py:372`,
    `impactsearch.py:1638, 1823`, `onepass.py:1519, 1656`,
    `spymaster.py:9036, 11074, 11627, 12560`,
    `stackbuilder.py:444`, `trafficflow.py:1605, 2063, 2440,
    2565, 2715`. The vectorized site at `trafficflow.py:2440`
    uses `t.sf(...)` in vectorized form.
  - Affected tests/snapshots:
      `SNAP_STACKBUILDER_METRICS_FROM_CAPTURES.p_raw`:
      `0x1.ddc4c5daf1688p-2 -> 0x1.ddc4c5daf168ap-2` (2 ULPs).
      `SNAP_STACKBUILDER_COMBINED_METRICS.metrics.p_raw`:
      `0x1.e4bc2cbb3bc50p-1 -> 0x1.e4bc2cbb3bc51p-1` (1 ULP).
      No other Phase 1A snapshot p-Value rounded to 4 decimals
      moved (the cdf/sf delta is below display rounding for the
      synthetic |t| values pinned). Other affected helpers
      (`SNAP_ONEPASS_METRICS_FROM_CCC`,
      `SNAP_IMPACTSEARCH_METRICS_FROM_CCC`,
      `SNAP_CONFLUENCE_MP_METRICS`,
      `SNAP_*_CALCULATE_METRICS_FROM_SIGNALS`) verified
      bit-identical post-conversion.
  - ELI5: for very large t, `cdf(|t|)` is so close to 1.0 that
    `1 - cdf(|t|)` rounds to exactly zero in float64, and the
    resulting p-value is reported as exactly 0. SciPy's `t.sf`
    ("survival function") computes the same tail probability
    directly without that subtraction, so it stays a tiny but
    nonzero number for all t. The spec mandates the sf form.
  - Status: implemented across all 15 engine canonical-scoring
    p-value sites in 1B-2A.

## Entry 4: zero-capture trigger-day counting

  - Type: BUG-FIX
  - Old behavior: four canonical-scoring sites computed the trigger
    mask from the capture series rather than from signal state:
    `stackbuilder.py:442` (`mask = captures.ne(0.0)` inside
    `metrics_from_captures`), `trafficflow.py:1600`
    (`trig_mask = daily_captures.to_numpy() != 0.0` inside
    `_metrics_like_spymaster`), and the legacy non-`active_pairs`
    fallback paths in `onepass.py:1487` and `impactsearch.py:1792`
    (`trig_mask = np.abs(caps) > 0`).
  - New behavior:
      `metrics_from_captures` accepts an optional `trigger_mask`
      parameter; when callers supply a signal-state mask, the
      helper uses it as-is (spec §15). The single-arg
      `metrics_from_captures(captures)` form is retained for
      callers that have not yet been wired (the legacy
      `captures.ne(0.0)` fallback runs only there).
      `_combined_metrics_signals` in stackbuilder now constructs
      and passes a signal-state mask (`comb_sig.isin(['Buy',
      'Short'])`); both Phase 2 and Phase 3 call sites therefore
      converge on signal-state counting.
      `trafficflow._metrics_like_spymaster` now uses
      `trig_mask = buy_mask | short_mask` directly (spec §15);
      `losses = trigger_days - wins` so zero-capture trigger days
      count as losses.
      `onepass._metrics_from_ccc` and
      `impactsearch._metrics_from_ccc` no longer fall back to the
      `np.abs(caps) > 0` heuristic; when `active_pairs` is missing
      they return `None` rather than producing a buggy count.
  - Affected tests/snapshots:
      `SNAP_STACKBUILDER_COMBINED_METRICS_SIGNALS_PENDING_BUG_FIX`
      retired and replaced with
      `SNAP_STACKBUILDER_COMBINED_METRICS_SIGNALS` (Trigger Days
      6 -> 8; Losses 1 -> 3; Win Ratio, Sharpe, Std Dev,
      t-Statistic, p-Value all flip; combined_caps unchanged). The
      corresponding test was renamed to drop the
      `_pending_bug_fix` suffix.
      `SNAP_TRAFFICFLOW_METRICS_LIKE_SPYMASTER`: Triggers 7 -> 8;
      Losses 2 -> 3; Avg, Sharpe, Std Dev, Total, Win %, t, p
      all flip.
      `SNAP_ONEPASS_METRICS_FROM_CCC_LEGACY` and
      `SNAP_IMPACTSEARCH_METRICS_FROM_CCC_LEGACY`: both flip from
      a full metrics dict to the `None` sentinel `('n',)` because
      the legacy fallback returns `None` rather than a buggy count.
      `SNAP_STACKBUILDER_METRICS_FROM_CAPTURES` is unchanged at
      this entry's surface — it tests the single-arg form, which
      retains the legacy `captures.ne(0.0)` mask. (Its 1-ULP
      `p_raw` flip is from Entry 3 only.)
  - ELI5: a "trigger day" is any day where the strategy actually has
    a position (Buy or Short). Some current code asks "did the
    capture move on that day?" instead of "was there a position
    that day?" — those two questions agree until the position is
    held over a day with zero return, in which case the second
    question correctly counts the day and the first one drops it.
    The spec is explicit: zero-return days under an active position
    are still trigger days, and they count as losses.
  - Status: implemented in 1B-2A across the 4 enumerated sites.
    Legacy fallback in `metrics_from_captures` is retained as a
    deprecated single-arg form for callers that lack signal info;
    in-engine callers now pass an explicit `trigger_mask`.

## Entry 5: StackBuilder Phase 2 vs Phase 3 scoring divergence

  - Type: BUG-FIX
  - Old behavior: per inventory §15, Phase 2's
    `apply_signals_to_secondary` uses `DEFAULT_GRACE_DAYS=7` while
    Phase 3's `_signals_aligned_and_mask` (`stackbuilder.py:717`)
    read `IMPACT_CALENDAR_GRACE_DAYS` with a separate default of
    `0`, producing divergent calendar-aligned capture series for
    the same primary/secondary pair on dates that fell inside the
    grace window for one phase but outside for the other.
  - New behavior: `_signals_aligned_and_mask` now uses the same
    `DEFAULT_GRACE_DAYS` constant Phase 2 already used, so both
    phases apply identical calendar tolerance. The trigger-mask
    side of the divergence (Phase 3's `present_all` zeroing vs
    Phase 2's grace-padded captures) is now equivalent for any
    pair where the grace policy produced the same aligned signal
    series. Both phases continue to flow through
    `metrics_from_captures`; the residual zero-capture trigger-day
    issue at `metrics_from_captures` is a separate ledger Entry 4
    fix and lands in the same PR.
  - Affected tests/snapshots: no Phase 1A snapshot flip from this
    change alone — the
    `test_stackbuilder_combined_metrics_signals_baseline_pending_bug_fix`
    fixture has perfectly aligned member/secondary indices, so the
    grace-days unification does not move its output. The
    `_pending_bug_fix` suffix is retained on the test until
    Entry 4 (zero-capture trigger counting) lands and produces the
    canonical signal-state-based output. Renaming and snapshot
    replacement happens with that commit.
  - ELI5: the same K=1 stack can return two different Sharpe and
    Total Capture numbers depending on whether you scored it
    through Phase 2's "rank everything" path or Phase 3's
    "build best stack" path, because the two paths use
    different rules for filling missing trading days. Codex
    sampled 10 real K=1 outputs and every one of them mismatched.
    The spec says one canonical scoring function; this entry
    unifies the calendar-grace rule across both phases so they
    can no longer disagree on which days are in scope.
  - Phase 2B-2A follow-up (PR #136):
      Codex's pre-flight identified one remaining Phase 2 vs
      Phase 3 divergence after the calendar-grace unification:
      `_score_primary` (Phase 2's per-primary scorer at
      `stackbuilder.py:497`) called the single-arg
      `metrics_from_captures(caps)` form, which uses the legacy
      `captures.ne(0.0)` mask and drops zero-return Buy/Short
      trigger days. Phase 3's `_combined_metrics_signals`
      already passed an explicit signal-state mask after 1B-2A,
      so Phase 2 K=1 metrics diverged from Phase 3 K=1 metrics
      whenever a primary had a zero-return trigger day against
      the secondary.
      Fix: refactor `apply_signals_to_secondary` with
      `return_mask=True` so it returns the same aligned-signal
      trigger mask alongside captures; thread the mask into
      `metrics_from_captures(caps, trigger_mask=mask)` from
      `_score_primary`. The legacy single-arg form is retained
      for callers that don't have signal info.
      Affected tests:
        - `test_within_engine_parity.py::test_b1_stackbuilder_direct_k1_parity`
          (Phase 2 rank_direct.iloc[0] now matches Phase 3 K=1
          leaderboard on trigger_days, sharpe, total_capture,
          p_value).
        - `test_within_engine_parity.py::test_b2_stackbuilder_inverse_k1_parity`
          (Phase 2 rank_inverse.iloc[0] matches Phase 3 K=1
          leaderboard on trigger_days and total_capture; the
          negate-and-view rank_inverse construction does NOT
          give a real inverse-mode Sharpe due to the
          risk-free-rate offset, documented in the test).
  - Status: calendar-policy unification implemented in 1B-2A;
    `_pending_bug_fix` test retired in the same PR alongside
    the Entry 4 zero-capture fix; Phase 2 `_score_primary`
    explicit-mask plumbing implemented in 2B-2A (PR #136).

## Entry 6: ImpactSearch xlsx duplicate-row dedupe

  - Type: BUG-FIX
  - Old behavior: `impactsearch.export_results_to_excel` read any
    existing xlsx and concatenated new rows on top with no dedupe.
    Calling it twice with the same `metrics_list` therefore wrote
    every row twice. The pre-fix Phase 1A snapshot
    `SNAP_IMPACTSEARCH_EXPORT_WRITES_DUPLICATES_PENDING_BUG_FIX`
    encoded `row_count = 4, primary_tickers = AAA, AAA, BBB, BBB`
    for a 2-primary `metrics_list` exported twice.
  - New behavior: after the read+concat, the combined frame is
    deduped by `Primary Ticker` (uppercase-stripped), with
    `Resolved/Fetched` as a fallback when `Primary Ticker` is
    empty, using `keep="last"`. The latest call's metric values
    win for any given ticker. Sharpe-descending sort is preserved
    (the existing post-dedupe sort uses the deduped values).
  - Affected tests/snapshots:
      `test_impactsearch_export_writes_duplicates_pending_bug_fix`
      retired and renamed to
      `test_impactsearch_export_dedupes_by_primary_ticker`. The
      replacement test calls export twice with the same primaries
      and changed metric values, then asserts the deduped row
      count is 2 (not 4), the retained values are the second
      call's, and the Sharpe-descending sort is preserved.
      `SNAP_IMPACTSEARCH_EXPORT_WRITES_DUPLICATES_PENDING_BUG_FIX`
      removed from `phase1a_baseline_snapshots.py`; the new test
      asserts dedupe semantics directly rather than via a
      snapshot constant.
  - ELI5: today, if you re-run ImpactSearch and it writes to an
    xlsx that already exists, every row gets duplicated. After
    this entry, a re-run replaces a ticker's row with the new
    metrics instead of doubling.
  - Status: implemented in 1B-2B.

## Entry 7: calendar grace days default unification

  - Type: EXPECTED-BY-SPEC
  - Old behavior: defaults were split — `7` in impactsearch
    (boot-log echo, `_metrics_from_signals` alignment, secondary
    coercion) and `signal_library/impact_fastpath` (calendar
    coverage check), `7` for `stackbuilder.DEFAULT_GRACE_DAYS`,
    and (most damaging) `stackbuilder.run_for_secondary` set
    `os.environ['IMPACT_CALENDAR_GRACE_DAYS'] = str(getattr(args,
    'grace_days', 0) or 0)` which forced grace to 0 for any args
    without an explicit `grace_days` attribute, defeating
    `DEFAULT_GRACE_DAYS`. QC sets 3 (deferred per scope note).
  - New behavior: every non-QC default is now 10 per spec §20:
      `impactsearch.py:312` boot-log echo default 10.
      `impactsearch.py:1964` `_metrics_from_signals` alignment
      default 10.
      `impactsearch.py:2314` secondary-coercion alignment default
      10.
      `signal_library/impact_fastpath.py:82`
      `IMPACT_CALENDAR_GRACE_DAYS` constant default 10.
      `stackbuilder.py:75` `DEFAULT_GRACE_DAYS` default 10.
      `stackbuilder.py:1488-1492`
      `run_for_secondary` no longer writes
      `IMPACT_CALENDAR_GRACE_DAYS = 0` when `args.grace_days` is
      unset; the env var is only set when the caller supplied an
      explicit grace_days override. `DEFAULT_GRACE_DAYS=10` now
      governs by default.
  - Affected tests: new `test_grace_days_default.py`:
      `test_stackbuilder_default_grace_days_is_10`,
      `test_impact_fastpath_default_grace_days_is_10`,
      `test_impactsearch_default_grace_days_is_10` (subprocess
      probe of boot-log echo),
      `test_stackbuilder_run_for_secondary_does_not_force_grace_zero`
      (asserts the env var is untouched when args.grace_days is
      unset, and is honored when explicitly supplied).
  - ELI5: trading calendars differ across markets; "grace days"
    is how far we let a missing day on one calendar pad against
    the nearest valid day on another. The codebase had at least
    three different defaults (7 / 0 / 3), which contributed to
    the StackBuilder Phase 2 vs Phase 3 divergence. The spec
    mandates a single default of 10. After this entry, every
    non-QC engine uses 10 by default.
  - Status: implemented in 1B-2B.

## Entry 8: sentinel pair standardization

  - Type: BUG-FIX
  - Old behavior: see inventory §8. The dead streaming path in
    Spymaster used `(1, 2)` for buy and `(2, 1)` for short as
    sentinel placeholders, while the live vectorized / leader
    fallback used `(MAX_SMA_DAY, MAX_SMA_DAY - 1)` /
    `(MAX_SMA_DAY - 1, MAX_SMA_DAY)`. OnePass init/fallback sites
    used the buy sentinel for short. TrafficFlow used `(1, 2)`
    for both buy and short fallbacks.
  - New behavior, two-stage:
      Stage 1 (this commit, Spymaster): the dead streaming path
      and its `(1, 2)` / `(2, 1)` sentinels are removed entirely
      from `spymaster.py`. The streaming function definition,
      the `use_streaming = False` flag, the
      `_compute_daily_top_pairs_streaming()` body, the
      `if use_streaming:` branch, and the related `work_estimate`
      log line are gone. Vectorized path is the only path.
      Stage 2 (next commit, OnePass + TrafficFlow + ImpactSearch):
      short-sentinel sites in OnePass switch to
      `(MAX_SMA_DAY - 1, MAX_SMA_DAY)`;
      TrafficFlow `(1, 2)` fallback replaced with the canonical
      MAX-SMA-1 form.
  - Affected tests:
      Stage 1: new `test_dead_streaming_path_removed.py` asserts
      the function definition is gone, the `use_streaming` flag
      is gone, the vectorized call remains, and no
      `(1, 2) / (2, 1)` sentinel literals remain as fallback
      assignments in `spymaster.py`.
      Stage 2: see Entry 8 stage-2 commit notes.
  - ELI5: when the engine has no valid pair to choose on a given
    day, it inserts a placeholder pair so downstream code does
    not crash. Three different placeholders existed across the
    engines (`(1, 2)`, `(2, 1)`, `(MAX_SMA_DAY, MAX_SMA_DAY - 1)`).
    After this entry, every engine uses the MAX-SMA form.
  - Stage 2 (OnePass + TrafficFlow + ImpactSearch):
      OnePass: three sites that previously used the buy sentinel
      `(MAX_SMA_DAY, MAX_SMA_DAY - 1)` for short are switched to
      the canonical `(MAX_SMA_DAY - 1, MAX_SMA_DAY)`:
      `onepass.py:782` (signal-library reuse fallback),
      `onepass.py:2133` (per-pair init for the canonical scoring
      loop), and `onepass.py:2178` (day-0 store in
      `daily_top_short_pairs`).
      TrafficFlow: a module-level `MAX_SMA_DAY = 114` plus
      `_BUY_SENTINEL` / `_SHORT_SENTINEL` constants are added.
      The `bdict.get(prev, ((1, 2), 0.0))` /
      `sdict.get(prev, ((1, 2), 0.0))` fallback at
      `trafficflow.py:1810-1811` is replaced with
      `(_BUY_SENTINEL, 0.0)` / `(_SHORT_SENTINEL, 0.0)`. The
      `(1, 2)` literal was unsafe because SMA_1 / SMA_2 have
      finite values most days, so the gating logic could
      accidentally produce a tradable signal from a missing-data
      sentinel.
      ImpactSearch: the same class of bug surfaced at
      `impactsearch.py:2272-2273`, where the per-date gating loop
      that builds primary signals from cached
      `daily_top_*_pairs` dicts used `((1, 2), 0.0)` as the
      `dict.get` default for both buy and short. ImpactSearch
      already imports `MAX_SMA_DAY = 114` at module scope, so
      the fix uses inline canonical tuples
      `((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)` for buy and
      `((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)` for short. Missed
      during the original 1B-2B sentinel inventory because the
      adjacent alignment helper at `impactsearch.py:1651-1656`
      was already canonical and the grep landed on that one.
      New test assertions in `test_sentinel_standardization.py`:
        - Spymaster has no `(1, 2) / (2, 1)` sentinel literals.
        - OnePass short-sentinel assignments use the canonical
          `MAX_SMA_DAY-1,MAX_SMA_DAY` form.
        - TrafficFlow defines the canonical sentinel constants
          and has no legacy sentinel literals.
        - ImpactSearch has no `(1, 2) / (2, 1)` sentinel
          literals (`test_impactsearch_no_legacy_sentinel_literals`)
          and the two `daily_top_*_pairs.get(...)` calls default
          to canonical MAX-SMA tuples
          (`test_impactsearch_uses_canonical_maxsma_sentinels`).
  - Stage 3 (signal_library, Phase 2A PR #134):
      The Phase 2A static regression guard `test_b2_daily_top_pairs_fallbacks_are_canonical`
      surfaced two more files with the same buy-form-reused-for-short
      bug:
        `signal_library/multi_timeframe_builder.py:626-627`
          (compute_dynamic_combined_capture_vectorized inner loop)
        `signal_library/multi_timeframe_builder.py:687-688`
          (generate_signal_series_dynamic per-day fallback)
        `signal_library/confluence_analyzer.py:77-78`
          (load_signal_library_interval signal-from-pkl)
      All three used `((114, 113), 0.0)` for both buy and short,
      letting SMA_113 / SMA_114 comparisons gate a tradable signal
      from a missing-data fallback. Same class as the
      ImpactSearch site fixed in 1B-2B amendment 1.
      Phase 2A's sparse-cache scenario tests
      (`test_sparse_cache_fallbacks.py`) then surfaced one more
      site in the same file: `multi_timeframe_builder.py:415-417`
      stored `((114, 113), 0.0)` for both buy and short on the
      day-0 init store inside the streaming-pair loop. Same
      class, just on the write side rather than the read side.
      Phase 2A amendment (PR #134, post-Codex audit) added a
      dedicated write-init static guard (B7,
      `test_b7_daily_top_pairs_write_init_canonical`) which
      caught one additional site:
        `impactsearch.py:2218-2219`
          Day-0 init at the top of the streaming pair loop
          stored `((114, 113), 0.0)` for both buy and short.
          Buy form was numerically correct but used a hardcoded
          literal where MAX_SMA_DAY is in scope; short form was
          wrong (buy-sentinel-reused-for-short).
      Fix: both lines now use canonical inline tuples
        buy:   ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)
        short: ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)
      Static guard B7 enforces:
        daily_top_buy_pairs[key]   = ((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)
        daily_top_short_pairs[key] = ((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)
      across all production files. Hardcoded numeric pairs
      (even when numerically canonical) are rejected to push
      every write-init through the named constant. B2 covers
      the read-fallback shape; B7 covers the write-init shape.
      Together they pin both sides of the bug class.
      Fixes:
        multi_timeframe_builder already had `MAX_SMA_DAY = 114` at
        module scope; the two read sites and the one day-0 write
        site now use canonical
        `((MAX_SMA_DAY, MAX_SMA_DAY - 1), 0.0)` for buy and
        `((MAX_SMA_DAY - 1, MAX_SMA_DAY), 0.0)` for short.
        confluence_analyzer gained a module-level
        `MAX_SMA_DAY = 114` constant; the single site uses the
        same canonical inline tuples.
  - Status: implemented in 1B-2B (stages 1 and 2) and Phase 2A
    (stage 3). Engine coverage: Spymaster (stage 1) + OnePass +
    TrafficFlow + ImpactSearch (stage 2) + signal_library
    multi_timeframe_builder + signal_library confluence_analyzer
    (stage 3).

## Entry 9: TrafficFlow cache key normalization

  - Type: BUG-FIX
  - Old behavior: `_PRICE_CACHE` reads and writes used the raw
    `secondary` argument as the key in most engine call sites
    (e.g. `_metrics_like_spymaster`, the subset-metrics helpers,
    the K-extension passes). `_load_secondary_prices` and
    `refresh_secondary_caches` normalized to uppercase before
    writing. A mixed-case or whitespace-padded lookup after an
    uppercase write therefore missed the cache and fell through
    to a redundant fetch.
  - New behavior: a `_price_cache_key(symbol)` helper at module
    scope returns `str(symbol or "").strip().upper()`. Every
    `_PRICE_CACHE.get(secondary)` and
    `_PRICE_CACHE[secondary] = sec_df` site now goes through the
    helper. `_load_secondary_prices` continues to normalize via
    `_price_cache_key` (replacing the inline `(secondary or "").upper()`).
    `refresh_secondary_caches` does the same for its symbol set.
  - Affected tests: new
    `test_trafficflow_price_cache_key_normalization`. Seeds
    `_PRICE_CACHE` under the canonical uppercase key, monkeypatches
    `_load_secondary_prices` to raise on call, then asserts that
    lowercase (`"syn"`), padded (`" SYN "`), and canonical (`"SYN"`)
    lookups all hit the same cached frame and produce identical
    metric output. Phase 1A's existing TrafficFlow test uses
    `"SYN"` (already canonical) and continues to pass unchanged.
  - ELI5: today, asking the cache for `"spy"` and asking for
    `"SPY"` can give different answers because the writer and the
    reader disagree on whether to uppercase the key. After this
    entry, the cache is case-consistent: every read and write
    flows through one normalizer.
  - Status: implemented in 1B-2B.

## Entry 10: Phase 1A snapshot updates

  - Type: depends on linked entry (each baseline-flip is recorded
    by its parent ledger entry above; this is a meta-entry tracking
    the umbrella).
  - 1B-2A snapshot replacements (each on the same commit as its
    parent ledger entry):
      | Snapshot constant | Parent | Reason |
      |---|---|---|
      | `SNAP_STACKBUILDER_METRICS_FROM_CAPTURES` | Entry 3 | `p_raw` flipped 2 ULPs (cdf -> sf) |
      | `SNAP_STACKBUILDER_COMBINED_METRICS` | Entry 3 | `metrics.p_raw` flipped 1 ULP (cdf -> sf) |
      | `SNAP_STACKBUILDER_COMBINED_METRICS_SIGNALS_PENDING_BUG_FIX` -> `SNAP_STACKBUILDER_COMBINED_METRICS_SIGNALS` | Entries 4 + 5 | signal-state trigger mask + Phase 2/3 calendar unification (test renamed to drop `_pending_bug_fix`) |
      | `SNAP_ONEPASS_METRICS_FROM_CCC_LEGACY` | Entry 4 | legacy `np.abs(caps) > 0` fallback removed; helper returns `None` |
      | `SNAP_IMPACTSEARCH_METRICS_FROM_CCC_LEGACY` | Entry 4 | same as above |
      | `SNAP_TRAFFICFLOW_METRICS_LIKE_SPYMASTER` | Entries 3 + 4 | signal-state trigger mask (Triggers 7 -> 8) + cdf -> sf |
  - Snapshots verified bit-identical post-conversion (no flip
    needed, included for audit completeness):
    `SNAP_ONEPASS_METRICS_FROM_CCC`,
    `SNAP_IMPACTSEARCH_METRICS_FROM_CCC`,
    `SNAP_CONFLUENCE_MP_METRICS`,
    `SNAP_ONEPASS_CALCULATE_METRICS_FROM_SIGNALS`,
    `SNAP_IMPACTSEARCH_CALCULATE_METRICS_FROM_SIGNALS` — the
    cdf/sf delta is below display rounding for the synthetic
    `|t|` values pinned, and these helpers were already
    signal-state based.
  - ELI5: any Phase 1A baseline test that changes its expected
    output during the rewire gets a one-line entry here naming
    which ledger item drove the change. This is the audit trail
    that lets a reviewer follow each diff back to a classified
    decision.
  - Status: 1B-2A snapshot replacements landed; Entry-2-driven
    spymaster snapshot is not pinned by Phase 1A (no flip).

---

# Phase 1B-2B backlog cleanup

The following entries close out the deferred 1B-2A backlog. Each
landed in PR #133 (branch `phase-1b-2b-backlog`).

## 1B-2B-1: Engine log handler anchoring

  - Type: BUG-FIX / OPS-FIX
  - Old behavior: import-time `FileHandler` in `spymaster.py`,
    `onepass.py`, and `impactsearch.py` opened `logs/<engine>.log`
    relative to the caller's cwd. Running pytest from the repo
    root therefore left a stray `logs/` directory under the repo
    root. `impactsearch.py` also had a separate
    `LOGS_ROOT = os.environ.get("IMPACT_LOGS_ROOT", "logs")`
    default that triggered an `os.makedirs(LOGS_ROOT, ...)` at
    import time with the same cwd-relative leakage.
  - New behavior: the three engines anchor their import-time log
    files to `Path(__file__).resolve().parent / "logs"` and call
    `mkdir(parents=True, exist_ok=True)` on that path.
    `impactsearch.LOGS_ROOT` defaults to the same anchored path
    when `IMPACT_LOGS_ROOT` is not set. Callers can still override
    with the env var (preserved for multi-instance usage).
  - Affected tests: new `test_log_anchoring.py` runs each engine
    import in a fresh subprocess from a temporary cwd outside
    `project/`, asserting the subprocess cwd does not get a
    `logs/` directory and `project/logs/<engine>.log` exists.
  - ELI5: previously, "running" the test suite or any tooling
    from the wrong working directory left an orphan `logs/`
    folder there. Now the engines always write logs into the
    project's own `logs/` directory regardless of where they
    were invoked from.
  - Status: implemented in 1B-2B.

## 1B-2B-2: StackBuilder Dash batch closure bug

  - Type: BUG-FIX
  - Old behavior: in the multi-secondary Dash launch loop at
    `stackbuilder.py:1239-1280`, the worker function `_job()` was
    a closure over the loop variables `args`, `sec`, `ppath`, and
    `primaries`. Python's late-binding closure semantics mean
    every thread sees the LAST iteration's bindings once the
    for-loop completes. Threads launched early therefore ran
    against the wrong secondary's parameters.
  - New behavior: `_job(job_args, job_sec, job_ppath, job_primaries)`
    takes the loop values as positional parameters; the loop
    body passes them via `threading.Thread(args=(...))`. A
    `primaries_snapshot = list(primaries) if primaries else None`
    snapshot is also taken so threads cannot mutate each other's
    primary list.
  - Affected tests: new `test_stackbuilder_closure.py`:
      `test_closure_bug_reproduction` demonstrates that Python's
      late-binding closure semantics still produce the bug
      pattern (canary against language changes invalidating the
      fix).
      `test_threadargs_pattern_delivers_correct_values` exercises
      the fix pattern in isolation.
      `test_stackbuilder_dispatches_distinct_args_per_thread`
      monkeypatches `run_for_secondary` to record its arguments,
      drives the production loop body by hand, and asserts that
      each thread's recorded `args.secondary`, `args.outdir`,
      `sec`, and `specified_primaries` match its iteration —
      with each thread seeing its own `args` object id (no
      cross-binding leak).
  - ELI5: when the user kicks off StackBuilder for several
    tickers at once, the buggy code could re-run the LAST
    ticker's settings against earlier tickers because of how
    Python "remembers" loop variables inside nested functions.
    The fix hands each background job its own copy of the
    settings.
  - Status: implemented in 1B-2B.

## 1B-2B-3: StackBuilder --outdir honored

  - Type: BUG-FIX
  - Old behavior: the `--outdir` CLI flag was parsed and
    `ensure_dir(args.outdir)` was called in `main()`, but
    `run_for_secondary()` constructed `secondary_parent` as
    `os.path.join(RUNS_ROOT, ...)` (hardcoded), ignoring the
    user's `--outdir` setting. CLI single-secondary, CLI
    multi-secondary, and Dash-launched flows therefore all wrote
    under `output/stackbuilder` regardless of the flag. The Dash
    callback also hardcoded `outdir=RUNS_ROOT` in the per-job
    args, defeating the `outdir` parameter that `run_dash`
    already accepted. `main()` called `run_dash(None, ...)` in
    its no-arguments branch, dropping the user's `--outdir` on
    the floor.
  - New behavior:
      `run_for_secondary` now uses
      `output_root = getattr(args, "outdir", None) or RUNS_ROOT`
      to build `secondary_parent`. The `RUNS_ROOT` fallback
      preserves behavior when `args.outdir` is absent (e.g.
      legacy callers).
      The Dash callback's per-job args now set
      `outdir = outdir if outdir else RUNS_ROOT`, threading the
      `run_dash(outdir, port)` parameter into the job args.
      `main()`'s no-args branch passes `args.outdir` into
      `run_dash` instead of `None`, so the Dash UI uses the
      user-supplied directory. The serve-after-CLI-run path
      (`run_dash(run_dirs[-1], ...)`) is unchanged because it
      passes a specific run directory rather than a root.
  - Affected tests: new `test_stackbuilder_outdir.py`:
      `test_run_for_secondary_uses_args_outdir`: stubs
      `phase1_preflight` and intercepts the first
      `ensure_dir` call to assert `secondary_parent` is built
      under a custom `args.outdir = /tmp/custom_outdir/SPY`.
      `test_run_for_secondary_falls_back_to_runs_root_when_outdir_none`:
      asserts the legacy `RUNS_ROOT/SPY` path is used when
      `args.outdir` is unset.
      `test_dash_callback_threads_outdir_into_job_args`: source-
      text assertions that the job-args block uses
      `outdir=_job_outdir`, that `_job_outdir = outdir if outdir
      else RUNS_ROOT` is computed from the run_dash parameter,
      that `main()` calls `run_dash(args.outdir, ...)`, and
      that the legacy `run_dash(None, ...)` call is gone.
  - ELI5: the CLI accepted a `--outdir` flag but ignored it.
    Multi-ticker runs and the Dash UI both wrote results to the
    default `output/stackbuilder` directory regardless of where
    the user pointed `--outdir`. After this entry, every output
    path honors `--outdir`.
  - Status: implemented in 1B-2B.
