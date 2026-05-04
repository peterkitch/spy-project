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
    the run_for_secondary force-to-zero fix land here. Phase
    2B-2B (PR #137) amendment removes the residual env-write,
    flips parser default to None, and threads explicit
    ``grace_days`` kwargs through the Phase 2 / Phase 3 chain.
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
  - Phase 2B-2B amendment (PR #137):
      Codex's 2B preflight surfaced the remaining sharp edge in
      the 1B-2B fix: `run_for_secondary` still mutated
      `os.environ['IMPACT_CALENDAR_GRACE_DAYS']` when the user
      supplied an explicit `args.grace_days`. The env mutation
      leaked grace state into worker subprocesses, persisted
      after `run_for_secondary` returned, and made the value
      hard to reason about for any in-process caller that did
      not snapshot/restore the env around the call. Parser
      default `0` also still meant a CLI invocation without
      `--grace-days` resolved to strict mode rather than the
      spec-default 10.
      Refactor (Option C scope):
        New helper `_effective_grace_days(grace_days)` resolves
        `None` to `DEFAULT_GRACE_DAYS` and honors any concrete
        int (including 0) verbatim. Six functions gained a
        kwarg-only `grace_days=None` parameter and thread the
        concrete value through:
          run_for_secondary
          phase2_rank_all
          _score_primary
          apply_signals_to_secondary
          _captures_for
          phase3_build_stacks
          _signals_aligned_and_mask
        Parser `--grace-days` default flips from `0` to `None`;
        help text documents `None -> DEFAULT_GRACE_DAYS=10` and
        `0` as strict mode. `run_for_secondary` resolves
        effective grace once via
          effective_grace = _effective_grace_days(
              grace_days if grace_days is not None
              else getattr(args, 'grace_days', None)
          )
        and passes the concrete int into Phase 2 and Phase 3
        instead of writing to `os.environ`. The env write is
        removed.
      Affected tests:
        `test_grace_days_default.py` rewritten:
          - `test_stackbuilder_run_for_secondary_does_not_write_env`
            (replaces the prior `..._does_not_force_grace_zero`):
            asserts the env var is never written and that
            default (10) and explicit (5) values both reach
            `phase2_rank_all` and `phase3_build_stacks` via
            kwarg.
          - `test_stackbuilder_explicit_grace_zero_strict_mode`:
            grace=0 reaches both phases verbatim.
          - `test_stackbuilder_kwarg_grace_overrides_args`:
            explicit `grace_days=` kwarg on
            `run_for_secondary` overrides `args.grace_days`.
          - `test_parse_args_grace_default_none`: parser
            default flipped to `None`.
        The pre-existing module-default tests
        (`test_stackbuilder_default_grace_days_is_10` etc.)
        continue to pass unchanged; they pin the constant, not
        the orchestration plumbing.
  - Status: 1B-2B for default flip + first env-write
    suppression; 2B-2B for explicit kwarg threading + complete
    env-write removal + parser default flip to None.

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

## 2B-2B-1: StackBuilder rank_inverse structural correction

  - Type: BUG-FIX
  - Old behavior: `phase2_rank_all` constructed `rank_inverse` by
    copying `rank_all` and negating three numeric columns:
    ``Avg Daily Capture (%)``, ``Total Capture (%)``, and
    ``Sharpe Ratio``. The resulting Sharpe values were
    mathematically incorrect because the canonical Sharpe formula
    contains a risk-free-rate offset:
        Sharpe_direct  = (avg_daily * 252 - rfr) / std_dev
        Sharpe_inverse = (-avg_daily * 252 - rfr) / std_dev
    Negating only the displayed Sharpe gives
    -(avg_daily * 252 - rfr) / std_dev, which differs from the
    real inverse-mode Sharpe by 2 * rfr / std_dev. The same flaw
    applied to ``p-Value`` (left untouched while Sharpe was
    negated, producing inconsistent significance vs ranking).
    Codex's PR #136 audit flagged this as a structural bug, and
    Phase 2B-2A's `test_b2_stackbuilder_inverse_k1_parity` had to
    skip Sharpe parity assertions to accommodate it (asserted only
    `trigger_days` and `total_capture`, which DO match exactly
    under negate-and-view because the RFR term cancels out and
    captures sign-flip cleanly).
  - New behavior: Phase 2 normal path now scores both modes from
    the same loaded primary library:
        ``_flip_signals(signals)``: relabel Buy<->Short, leave
        None untouched. Accepts string-form or int8-form payloads
        and returns the same shape.
        ``_load_primary_signals(primary)``: returns
        ``(vendor, sigs, dates)`` once per primary, decoding
        int8 to string labels before return so direct and
        inverse paths can share the decoded payload without
        duplicate IO.
        ``_score_primary_from_signals(vendor, sigs, dates,
        sec_rets, *, mode='D'|'I', grace_days=None)``: scores
        pre-decoded signals in the requested mode. ``mode='I'``
        flips signals before alignment so the resulting
        Sharpe / p-value / std-dev / win-rate / trigger-mask are
        all real inverse-mode scores. Any value other than 'D' /
        'I' raises ``ValueError``.
        ``_score_primary_both_modes(primary, sec_rets, *,
        grace_days=None)``: returns
        ``(direct_metrics, inverse_metrics)`` from a single
        library load. Used by ``phase2_rank_all`` so the normal
        path doesn't pay 2x IO cost.
        ``_score_primary(primary, sec_rets, *, mode='D',
        grace_days=None)`` gains the ``mode`` kwarg for callers
        that want a single mode at a time.
    `phase2_rank_all` normal path now collects two row lists
    (direct and inverse) per primary, builds ``rank_all`` and
    ``rank_direct`` from direct rows, and ``rank_inverse`` from
    real inverse-mode rows. The rank DataFrame schema is
    unchanged: no ``Mode`` column on ``rank_all`` /
    ``rank_direct`` / ``rank_inverse``;
    ``phase3_build_stacks`` continues to attach
    ``Mode`` to its cohort copies (``top['Mode'] = 'D'``,
    ``bottom['Mode'] = 'I'``).
    xlsx fast-path: direct ``rank_all`` / ``rank_direct`` still
    come from the ImpactSearch Excel verbatim (after schema
    coercion), but ``rank_inverse`` is now recomputed from
    signal libraries via ``_score_primary_from_signals(...,
    mode='I')`` for each ticker in the xlsx cohort. Negate-and-
    view is removed from this branch as well.
    Missing-library fallback (xlsx fast-path): a ticker whose
    signal library is missing or corrupt, or whose inverse-mode
    score returns ``None`` (e.g. zero trigger days), is skipped
    from ``rank_inverse`` with a single-line warning that names
    up to the first 10 tickers. The run fails loudly only when
    ``args.bottom_n > 0`` and no usable inverse-mode rows
    survived; the user is told to verify signal libraries
    exist or to drop ``--prefer-impact-xlsx``. This is the
    "least disruptive documented behavior" alternative from the
    Phase 2B-2B preflight scope: xlsx fast-paths typically run
    against a curated cohort whose libraries are available, so
    skipping is the expected outcome for the rare missing case;
    the loud bottom_n>0 failure prevents the silent regression
    case where the user requested an inverse-mode cohort and
    got an empty one.
  - Affected tests:
      ``test_within_engine_parity.py::test_b2_stackbuilder_inverse_k1_parity``
      now asserts full canonical parity with Phase 3 K=1 inverse
      (``trigger_days``, ``sharpe``, ``total_capture``,
      ``p_value``) — the same contract B1 enforces on the direct
      path. The prior test docstring's caveat about the RFR
      asymmetry has been retired.
      New: ``test_b2b_rank_inverse_not_negate_symmetry_when_rfr_nonzero``
      pins the regression signal: with non-zero RFR, the
      displayed inverse Sharpe must NOT equal -direct Sharpe
      (modulo display rounding); negate-and-view would produce
      delta ~ 0. Also asserts trigger_days symmetry.
      New: ``test_b2c_xlsx_fastpath_inverse_recomputed_not_negated``
      monkeypatches ``try_load_rank_from_impact_xlsx`` to return
      a synthetic xlsx with deliberately-provocative direct
      values, then asserts ``rank_inverse`` rows match
      ``_score_primary_from_signals(..., mode='I')`` rather than
      the sign-flipped xlsx values.
  - ELI5: previously, "inverse" rank rows for the same primary
    were built by flipping the sign of three displayed numbers
    on the direct row. That worked for total capture and avg
    daily capture (which really do flip sign cleanly) but it
    was wrong for Sharpe, because Sharpe's formula has a
    risk-free-rate term that doesn't change sign when you flip
    signals. The fix runs the inverse strategy through the same
    scoring code as the direct strategy, after flipping
    Buy<->Short on the primary signals. Now the inverse Sharpe
    is the actual Sharpe you'd see if you traded the inverse
    strategy, not a sign-flipped view of the direct one.
  - Status: normal path implemented in 2B-2B (PR #137 commit 2);
    xlsx fast-path implemented in 2B-2B (PR #137 commit 3).

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


## Phase 3A: Provenance Manifests (Signal Libraries)

  - Type: ADDITIVE-PROVENANCE (no behavior change to scoring math).
  - Old behavior: signal-library pickles carried only ad-hoc fields
    (`engine_version`, `max_sma_day`, `parity_hash`, `data_fingerprint`,
    `head_tail_snapshot`). Consumers checked engine_version + max_sma_day
    + price_source piecemeal; there was no single artifact-level
    fingerprint pinning the source data, the run parameters, the
    repository state, and the runtime versions together. A library
    rebuilt with a different scipy/pandas/numpy minor version would
    silently swap into reuse with no detection at the load boundary.
  - New behavior: all signal-library producers attach a
    ``_manifest`` dict (and a sibling ``.manifest.json`` sidecar) at
    write time, and all signal-library consumers verify the manifest
    immediately after the raw ``pickle.load`` and before any reuse.

    Central helper: ``project/provenance_manifest.py``. Public surface:

      - ``MANIFEST_SCHEMA_VERSION = 1``
      - ``MANIFEST_FIELD = "_manifest"``
      - ``VOLATILE_LIBRARY_KEYS = {"_manifest", "build_timestamp"}``
      - ``VerificationResult(ok, legacy, mismatches, warnings)``
      - ``build_manifest(library_dict, *, artifact_type, ticker, ...) -> dict``
      - ``attach_manifest(library_dict, sidecar_path, ...) -> (dict, dict)``
      - ``read_manifest(library_dict, sidecar_path=None) -> dict | None``
      - ``verify_manifest(library_dict, sidecar_path=None, *,
        strict=False, requested_params=None,
        current_source_close=None) -> VerificationResult``
      - ``content_hash(library_dict) -> str``
      - ``source_close_hash(close_series) -> str | None``
      - ``refresh_or_attach_manifest(library_dict, sidecar_path, ...)
        -> (dict, dict, bool)`` — used by metadata-repair persists.

    Manifest schema (stable):
      ``schema_version, artifact_type, ticker, resolved_symbol, interval,
       date_range_start, date_range_end, row_count, source_data
       (hash_method, source_close_hash, row_count, start, end), params,
       engine_version, git_commit, git_dirty, package_versions,
       content_hash``

    Manifest schema (volatile, excluded from content_hash):
      ``build_timestamp, builder_identity, host_platform``

    Hash contract:

      - ``content_hash`` is SHA-256 of the canonical JSON of the library
        dict with ``VOLATILE_LIBRARY_KEYS`` excluded. ``_manifest``
        exclusion is required to keep the hash from being self-
        referential. ``build_timestamp`` exclusion is required because
        existing libraries already carry a top-level wall-clock build
        timestamp; including it would flip the hash on every save even
        for identical payloads.
      - Numpy arrays are reduced to ``(dtype, shape, sha256(bytes))``;
        pandas Series and DatetimeIndex similarly. NaN/Inf are encoded
        explicitly so they do not silently coerce to JSON null.
      - ``source_close_hash`` digests the price series by dtype, value
        bytes, and index bytes. Returns None when no usable Close is
        available — the producer signal that a source comparison
        cannot be performed.

    Producer sites (write manifest before pickle.dump):

      - ``onepass.save_signal_library`` — manifest params capture
        MAX_SMA_DAY, price_source, group_by_mode, persist_skip_bars,
        tiebreak_rule, auto_adjust=False, parity_hash. ``source_close``
        is the post-T-1-skip ``df['Close']`` so the hashed source
        matches the persisted signals/dates byte-for-byte.
      - ``onepass._ensure_signal_alignment_and_persist`` and
        ``onepass._persist_library_metadata`` — metadata-repair
        persists. Both call ``refresh_or_attach_manifest``: when no
        ``source_close`` is available, the existing ``source_data``
        block is preserved verbatim (no fabrication of source hashes
        mid-flight). ``params.repair_kind`` distinguishes the two.
      - ``signal_library/multi_timeframe_builder.save_signal_library``
        — non-daily interval libraries (``1wk``, ``1mo``, ``3mo``,
        ``1y``). The post-fetch Close is threaded from
        ``generate_signals_for_interval`` via a transient
        ``_source_close_transient`` library key, popped before
        ``pickle.dump``. ``artifact_type =
        "interval_signal_library"``.

    Consumer sites (verify manifest after pickle.load, before reuse):

      - ``onepass.load_signal_library`` — manifest mismatch returns
        ``None`` (caller rebuilds). Legacy libraries warn and proceed.
      - ``impactsearch.load_signal_library`` — manifest mismatch
        returns ``None`` (caller falls back to slow path). Legacy
        warns and proceeds.
      - ``signal_library/impact_fastpath._load_signal_library_quick``
        — manifest mismatch disables the fast path for that ticker
        (returns ``None`` so the caller falls back). Legacy warns.
      - ``stackbuilder.fallback_load_signal_library`` — manifest
        mismatch skips to the next candidate. ``load_lib_or_none``
        routes through ``onepass.load_signal_library`` first
        (verified there); falls back to ``fallback_load_signal_library``.
      - ``signal_library/confluence_analyzer.load_signal_library_interval``
        — manifest mismatch returns ``None`` for that interval. The
        spymaster cache fallback inside the same function remains
        un-verified for now (Spymaster PKL manifests are Phase 3B).

    Legacy-compatibility contract (Part E):

      - Library has no ``_manifest`` and no sidecar JSON ->
        ``VerificationResult(ok=True, legacy=True)``. Caller may
        proceed; a warning is logged so legacy libraries surface in
        observability without being rejected.
      - Embedded vs. sidecar drift -> warn, prefer embedded. The
        embedded manifest is atomic with the pickle (single fsync
        cliff); the sidecar can lag.
      - No mass rebuild in 3A. New manifests appear on the next
        normal rebuild/write at any producer site.
      - Reading a legacy library does NOT inject a fake legacy
        manifest. The library is unchanged in memory.

    Static guard (B12):

      - Added ``test_b12_signal_library_consumers_use_verify_manifest``
        in ``project/test_scripts/test_static_regression_guards.py``.
        Uses an AST walk scoped to each named consumer function, not a
        broad file grep. Fails when any of the five guarded functions
        contains a load path that does not call ``verify_manifest`` /
        ``_verify_manifest`` somewhere in the function body.
      - Allowlist (recorded in the test docstring): ``provenance_manifest``
        itself, ``test_scripts/``, and non-signal-library pickle
        consumers explicitly deferred to Phase 3B (spymaster PKLs,
        trafficflow's signal-library quick load, ImpactSearch
        CacheManager, the spymaster-cache fallback inside
        ``confluence_analyzer.load_signal_library_interval``).

    Affected tests/snapshots:

      - New ``project/test_scripts/test_provenance_manifest.py``: F1-F10
        helper-only tests + F11-F15 consumer hooks + F16 metadata-repair
        preservation + F17 B12-catches-violation. ~18 new tests
        total. No Phase 1A baseline-lock snapshot churn — Phase 3A is
        purely additive provenance.

  - Phase 3B deferrals (NOT in 3A scope):
      - StackBuilder run_manifest enrichment + Excel/CSV export
        provenance.
      - OnePass / ImpactSearch xlsx output manifests.
      - Spymaster PKL manifests (cache/results/*.pkl).
      - TrafficFlow durable outputs.
      - Confluence durable outputs (the analyzer reads signal-library
        manifests in 3A, but does not produce its own confluence-
        artifact manifests).
      - Strict CLI / backfill controls (``strict=True`` plumbing).
      - Mass rebuild of legacy libraries.
      - B11 ``compute_signals`` delete-or-shift-correct decision (this
        deferral is unchanged from the Phase 2B preflight — Phase 3A
        does not touch B11).
      - QC clone files.

  - ELI5: every saved signal library now carries a tamper-evident
    receipt — a ``_manifest`` dict embedded in the pickle plus a
    JSON sidecar. The receipt records what code built the library
    (engine version, git commit, runtime package versions), what
    inputs it consumed (a hash of the source Close series, the date
    range, run parameters), and a content_hash of the artifact
    itself. When the library is later loaded, the receipt is checked
    against the artifact and the requested run parameters; mismatches
    force a rebuild. Older libraries without a receipt are still
    accepted, but they log a warning so the next clean rebuild
    upgrades them.
  - Status: implemented in Phase 3A (this PR). Phase 3 is NOT
    complete after 3A — see Phase 3B deferrals above.


## Phase 3B-1: Manifest performance cache + central verified loader + B12 tightening

  - Type: ADDITIVE-PROVENANCE (no behavior change to scoring math).
    Carry-forwards from Phase 3A; output / Spymaster PKL manifests
    remain Phase 3B-2 scope.

  - Old behavior:
      - Each signal-library consumer recomputed ``content_hash`` on
        every load. The hash walks the canonical payload (numpy
        arrays digested as ``(dtype, shape, sha256(bytes))``, pandas
        Series / DatetimeIndex similarly), which is bounded but
        adds ~30 ms per 2000-bar library load. StackBuilder runs
        through ThreadPoolExecutor over many libraries; the
        per-load cost compounded.
      - ``impactsearch.py`` and ``signal_library/impact_fastpath.py``
        each carried a private ~30-line copy of the NumPy 1.x / 2.x
        pickle-compat shim plus a private ``_pickle_load_compat``
        wrapper. Both inlined a raw ``pickle.load`` call.
      - The five signal-library consumers each open + load + type-check
        + verify_manifest by hand. Five copies of the same boilerplate.
      - Phase 3A B12 was function-scoped: it asserted that each of the
        five named consumer functions called ``verify_manifest`` in its
        body. A new consumer function added without B12 awareness, or
        a new pickle.load site outside the named functions, would slip
        through.

  - New behavior:
      - ``project/provenance_manifest.py`` adds an LRU
        ``content_hash`` cache keyed by
        ``(resolved_path, st_mtime_ns, st_size)``. ``content_hash``
        is recomputed only on cache miss. The cache is consulted
        only when callers explicitly supply ``cache_path`` to
        ``verify_manifest``; direct in-memory callers (the legacy
        helper-test shape) keep the strict recomputation contract.
      - LRU bound: 256 entries (``_MANIFEST_HASH_CACHE_MAX``).
      - Thread-safe via an ``RLock``. ``content_hash`` is computed
        outside the lock so concurrent loads do not serialize on
        the canonical-blob walk.
      - Env-var disable:
        ``PRJCT9_DISABLE_MANIFEST_HASH_CACHE=1`` forces recompute
        and skips cache insertion / hit-miss accounting.
      - Public surface: ``manifest_hash_cache_clear()`` and
        ``manifest_hash_cache_info()`` (hits / misses / evictions /
        current_size / max_size / enabled).
      - NumPy 1.x / 2.x pickle compatibility centralized as
        ``provenance_manifest.pickle_load_compat``. Importing the
        module installs the shims as a side effect (idempotent).
        Per-engine duplicate definitions in ``impactsearch.py`` and
        ``signal_library/impact_fastpath.py`` were removed.
      - Central verified loader:
        ``load_verified_signal_library(path, *, requested_params,
        strict, expected_type, cache)`` returns
        ``(library_dict, VerificationResult)``. It bundles open +
        ``pickle_load_compat`` + type-check + ``verify_manifest``,
        feeding the path through to the cache. Load errors
        (``UnpicklingError``, ``EOFError``, ``ModuleNotFoundError``,
        ``OSError``) surface as a single ``("load_error", type, msg)``
        mismatch; non-dict loads as ``("type_error", expected, actual)``.
        Each consumer keeps its own corrupt-file quarantine and
        manifest-mismatch policy.

      Migrated consumer sites (Phase 3B-1):
        - ``onepass.load_signal_library``
        - ``impactsearch.load_signal_library``
        - ``signal_library/impact_fastpath._load_signal_library_quick``
        - ``stackbuilder.fallback_load_signal_library``
        - ``signal_library/confluence_analyzer.load_signal_library_interval``
          (signal-library branch only — the Spymaster cache fallback
          was extracted into ``_load_spymaster_cache_fallback`` so the
          tightened B12 can allowlist *only* that helper as a Phase
          3B-2 deferred surface.)

      Tightened B12 (``test_b12_no_raw_pickle_load_outside_central_loader``):
        - AST scan across every production .py file in scope.
        - Bans raw ``pickle.load(...)`` outside an explicit allowlist.
        - File allowlist:
          * ``provenance_manifest.py`` — the central loader itself.
        - Line-precise allowlist (Phase 3B-2 deferred surfaces):
          * ``spymaster.py:3629, 4036, 4383, 8512`` — Spymaster cache PKLs.
          * ``trafficflow.py:1349`` — TrafficFlow Spymaster PKL consumer.
          * ``signal_library/confluence_analyzer.py:72`` —
            ``_load_spymaster_cache_fallback``.
        - The function-scoped Phase 3A B12 is preserved as a stricter
          inner gate
          (``test_b12_signal_library_consumers_use_verify_manifest``).

  - Affected tests/snapshots:
      - ``project/test_scripts/test_provenance_manifest.py``: +20
        cache and central-loader tests covering uncached mutation
        detection, hit/miss patterns, alias resolution, size /
        mtime / atomic-replace / in-place rewrite invalidation, LRU
        eviction, env-var disable, threaded smoke, central loader
        success / legacy / mismatch / corrupt / type-error / strict
        runtime mismatch, ``pickle_load_compat`` smoke, and a
        synthetic B12 helper test.
      - ``project/test_scripts/test_static_regression_guards.py``:
        new ``test_b12_no_raw_pickle_load_outside_central_loader``
        with the line-precise allowlist; existing function-scoped
        B12 retained.
      - 159 baseline -> 179 with Phase 3B-1.

  - Phase 3B-2 surface (NOT in 3B-1 scope):
      - StackBuilder run_manifest enrichment + Excel/CSV export
        provenance.
      - OnePass / ImpactSearch xlsx output manifests + xlsx upsert
        provenance.
      - Spymaster PKL manifests (``cache/results/*.pkl``).
      - TrafficFlow Spymaster PKL verification.
      - Confluence durable outputs and the
        ``_load_spymaster_cache_fallback`` allowlist retirement.
      - CLI strict-mode controls (``--manifest-strict`` plumbing).
      - Mass rebuild of legacy libraries.
      - B11 ``compute_signals`` decision (still deferred).
      - QC clone files.

  - ELI5: re-loading the same signal library twice in a row used to
    re-walk every byte of the canonical payload to recompute the
    artifact hash. Phase 3B-1 keeps the most recent 256 such hashes
    in a small lookup table keyed by ``(path, modification time,
    size)``. If any of those three change — atomic replace, in-place
    rewrite, even a touch — the entry is invalidated and the hash
    recomputes from scratch. Mid-flight mutation in memory still
    forces a recompute because that path bypasses the cache. As a
    side benefit, every signal-library load now goes through one
    centrally maintained loader with NumPy 1.x/2.x compatibility,
    and a stricter static guard (``B12``) catches any future
    raw-pickle.load leak in production code.
  - Status: implemented in Phase 3B-1 (this PR). Phase 3 is NOT
    complete after 3B-1 — Phase 3B-2 covers output manifests and
    the remaining Spymaster / TrafficFlow / Confluence deferred
    surfaces.


## Phase 3B-2A: Output Manifest Helper + StackBuilder Run Manifests + Spymaster PKLs

  - Type: ADDITIVE-PROVENANCE (no behavior change to scoring math).
    Carry-forwards from Phase 3B-1; XLSX upsert manifests, CLI
    strict-mode, and final Phase 3 close remain Phase 3B-2B scope.

  - Old behavior:
      - Provenance was signal-library-only (Phase 3A) plus a perf
        cache and tightened B12 (Phase 3B-1). Output artifacts
        (StackBuilder run dirs, Spymaster cache PKLs) had no manifest
        contract; consumers loaded them with raw ``pickle.load`` plus
        ad-hoc validation. Spymaster cache PKLs in particular were
        consumed by Spymaster, TrafficFlow, and Confluence with three
        independent corruption / contamination guards.
      - StackBuilder ``run_manifest.json`` recorded only the secondary
        ticker, started/finished timestamps, params subset, and a flat
        ``outputs`` filename mapping. There was no record of which
        signal libraries the run consumed, no on-disk file SHAs for
        the rank/cohort/leaderboard tables, and no schema_version /
        artifact_kind / engine context to tie it to the broader Phase
        3 manifest contract.
      - The Phase 3B-1 perf-cache invalidation tests paid for
        filesystem mtime resolution by sleeping 1.05 s past the
        coarse-mtime granularity. ~2 s of test-suite wall-clock that
        was structural noise.

  - New behavior:

    Output manifest helper / schema (provenance_manifest.py):

      - Constants: ``ARTIFACT_KIND_SIGNAL_LIBRARY = "signal_library"``
        (Phase 3A default; manifests without ``artifact_kind`` continue
        to read as signal_library, no Phase 3A regression) and
        ``ARTIFACT_KIND_OUTPUT = "output"``.
      - ``file_sha256(path)``: streamed SHA-256 of file bytes; bound
        memory.
      - ``build_output_manifest(...)``: produces an output-flavored
        manifest with stable identity (schema_version, artifact_kind,
        artifact_type, producer_engine, engine_version), run / config
        inputs (params, cli_args, ui_args, input_manifest_hashes,
        input_secondary_hash, output_schema), runtime / environment
        (git_commit, git_dirty, package_versions), and volatile fields
        (build_timestamp, builder_identity, host_platform). Logical
        ``content_hash`` is computed via the Phase 3A canonical-blob
        walk for mappings; non-mappings go through canonical JSON.
      - ``write_output_manifest(artifact_path, manifest, *,
        include_file_sha256=True, sidecar_path=None)``: atomic temp +
        os.replace sidecar write, with optional ``artifact_file_sha256``
        stamped over the on-disk bytes. The on-disk SHA lives ONLY in
        the sidecar — embedding it in a pickle's ``_manifest`` would be
        self-referential.
      - ``load_verified_pickle_artifact(path, *, requested_params,
        strict, expected_type=dict, cache=True)``: mirrors
        ``load_verified_signal_library`` but routes through the new
        output-verification path, including the optional sidecar
        ``artifact_file_sha256`` byte-level check.
      - ``load_verified_json_artifact(path, *, requested_params,
        strict, expected_type=dict)``: for non-self JSON outputs whose
        manifest lives in a sidecar.
      - Internal ``_verify_output_manifest`` shared body covering:
          * logical content_hash mismatch -> ok=False
          * artifact_file_sha256 mismatch (when sidecar has it) -> ok=False
          * params subset mismatch -> ok=False
          * input_manifest_hashes subset -> warn (strict=False) /
            fail (strict=True)
          * runtime / package version drift -> warn / fail by strict
          * schema_version drift -> warn

    Hash contract:

      - logical content_hash digests artifact content excluding
        ``_manifest``. Same canonical walk as Phase 3A. May be embedded.
      - artifact_file_sha256 digests final on-disk bytes. Sidecar-only
        for embedded-pickle manifests (Risk 1: self-referential file
        SHA inside a file is mathematically impossible to satisfy).
      - JSON artifacts may carry a file SHA in the sidecar.
        ``run_manifest.json`` does NOT embed its own self-SHA; if a
        future reader wants tamper-evidence over the JSON itself, it
        would compute the SHA over a canonical version with the
        self-hash field excluded.

    Sidecar / embedded drift (Risk 2):

      - Two-file (pickle + sidecar) write is not atomic across both
        files. The contract tolerates a torn sidecar:
          * embedded manifest is authoritative for logical verification.
          * sidecar adds a file-byte check when present.
          * sidecar/embedded disagreement -> warn, prefer embedded.
          * strict-mode callers may require sidecar verification;
            failures surface as a strict mismatch, not a load crash.

    StackBuilder run_manifest enrichment:

      - Existing keys preserved verbatim: ``secondary``, ``started_at``,
        ``params``, ``finished_at``, ``elapsed_seconds``, ``outputs``.
        The ``outputs`` mapping retains its existing flat
        ``name -> filename`` shape unchanged.
      - New keys (Phase 3B-2A): ``schema_version``, ``artifact_kind``,
        ``artifact_type``, ``producer_engine``, ``engine_version``,
        ``run_id``, ``git_commit``, ``git_dirty``, ``package_versions``,
        ``build_timestamp``, ``builder_identity``, ``host_platform``,
        ``cli_args`` (stable subset), ``status``,
        ``output_artifacts`` (per-file entries with filename / format /
        size / file_sha256 / produced_at; row_count + column_schema for
        CSV; xlsx / parquet schema deferred to 3B-2B), and
        ``input_manifest_hashes`` / ``input_legacy_count`` /
        ``input_missing_manifest_count`` collected via a thread-safe
        per-run RLock-protected collector hooked into
        ``load_lib_or_none``. ``input_secondary_hash`` is a placeholder
        None pending 3B-2B's secondary fingerprinting.
      - Run-manifest readers grep: only ``stackbuilder.py`` writes
        ``run_manifest.json``; no external readers exist, so the
        schema can grow freely as long as existing keys are preserved.
      - The collector starts at ``run_for_secondary`` entry and
        finalizes either on success (snapshot embedded in the final
        manifest) or in the except handler (drops the snapshot so a
        failed run does not bleed into the next run's manifest).

    Spymaster PKL manifests:

      - Producer: ``spymaster.save_precomputed_results`` now embeds a
        Phase 3 output manifest in ``results_to_disk`` BEFORE
        ``pickle.dump``, then writes a sidecar JSON with
        ``artifact_file_sha256`` over final on-disk bytes after the
        atomic ``os.replace``. Sidecar write failures log a warning
        but do not fail the save — the embedded manifest is
        authoritative for logical verification (Risk 2).
      - Internal consumers (4) all routed through
        ``load_verified_pickle_artifact``:
          * ``_quick_last_fingerprint`` (cache fingerprint probe)
          * ``load_precomputed_results_from_file`` (with retry; the
            ``_ticker`` contamination check remains AFTER verified load)
          * ``load_preprocessed_df`` (DataFrame-only load)
          * UI disk fallback in the dynamic-strategy callback (~ line
            8605)
      - TrafficFlow consumer: ``load_spymaster_pkl`` migrated;
        ``_PKL_CACHE`` is populated only after a verified-or-legacy
        accept so a tampered PKL does not poison the in-memory cache.
      - Confluence consumer: ``_load_spymaster_cache_fallback``
        migrated; the surrounding signal construction is unchanged.

    B12 allowlist retirements:

      - All 6 deferred entries removed:
          * ``spymaster.py:3629 / 4036 / 4383 / 8512`` (4 sites)
          * ``trafficflow.py:1349``
          * ``signal_library/confluence_analyzer.py:72``
      - Final 3B-2A allowlist: only ``provenance_manifest.py`` (the
        central loader internals). Every other production .py file
        routes through one of ``load_verified_signal_library``,
        ``load_verified_pickle_artifact``, ``load_verified_json_artifact``,
        or ``pickle_load_compat``.

    mtime test tightening:

      - ``test_3b1_cache_atomic_replace_invalidates`` and
        ``test_3b1_cache_inplace_rewrite_invalidates`` now bump
        mtime explicitly via ``os.utime(..., ns=...)`` past the
        original mtime_ns instead of sleeping 1.05 s past the
        filesystem mtime resolution. Both rewrite the file with the
        same bytes (size unchanged) so the cache miss is unambiguously
        driven by the mtime key component, not size. Suite runtime
        drops by ~2 s end-to-end.

  ### Architecture exception: sanctioned Spymaster import

  Old standalone rule (CLAUDE.md):
    "Spymaster.py is intentionally standalone by design. NO
     dependencies on other project modules (signal_library,
     global_ticker_library, onepass, impactsearch). Direct yfinance
     calls for all data fetching. Isolated caching system. Self-
     contained calculations for all metrics."

  New narrow exception (Phase 3B-2A):
    spymaster.py is authorized to import from
    ``project/provenance_manifest.py`` for the manifest contract only:
        from provenance_manifest import (
            build_output_manifest,
            write_output_manifest,
            load_verified_pickle_artifact,
            file_sha256,
            MANIFEST_FIELD,
            ARTIFACT_KIND_OUTPUT,
        )
    Spymaster's scoring / data-fetch / regression-baseline behavior
    remains standalone; the import is scoped to producer / consumer
    manifest IO only.

  Why it is justified:
    - Spymaster cache PKLs are durable artifacts consumed by
      Spymaster itself, TrafficFlow, the Confluence Spymaster
      fallback, and (as of Phase 4) the Cross-Ticker Confluence
      Dashboard. A single manifest contract across producers and
      consumers is the Phase 3 single-source-of-truth invariant.
    - Duplicating ``provenance_manifest.py`` logic inside
      ``spymaster.py`` would violate that invariant. A separate
      schema would prevent shared verification across engines and
      regress the cross-engine provenance work landed in Phase 3A
      and 3B-1.
    - The exception is narrow: only the manifest helper, only at the
      producer write site and the four consumer load sites. No new
      coupling is introduced into the scoring or data-fetch paths.

  Extraction / injection contingency:
    If Spymaster is ever split into a separate package, the
    provenance helper travels with it (vendor in) or gets injected
    as a dependency through the producer/consumer entry points
    (callers pass ``build_output_manifest`` / ``write_output_manifest``
    / ``load_verified_pickle_artifact`` callables in). The exception
    does not block the standalone extraction; it just makes the
    helper a build-time dependency rather than a packaging concern.

  - Affected tests/snapshots:
      - ``project/test_scripts/test_provenance_manifest.py``: +19
        Phase 3B-2A tests covering helper/schema fields,
        sidecar/embedded contract, JSON artifact verification,
        StackBuilder collector + output_artifact_entry +
        _build_output_artifacts + run_manifest legacy-keys grep,
        Spymaster producer/consumer/legacy/mismatch/torn-sidecar
        paths, TrafficFlow valid/legacy/tampered with _PKL_CACHE
        invariant, Confluence fallback valid/legacy/tampered.
      - ``project/test_scripts/test_static_regression_guards.py``:
        B12 allowlist shrunk from 7 entries to 1 (only
        ``provenance_manifest.py``).
      - 179 baseline -> 198 with Phase 3B-2A.

  - Phase 3B-2B surface (NOT in 3B-2A scope):
      - OnePass XLSX upsert manifests
      - ImpactSearch XLSX upsert manifests
      - ``load_verified_xlsx_artifact``
      - StackBuilder XLSX fast-path strict verification
      - ``--strict-manifests`` CLI plumbing
      - ``TRAFFICFLOW_STRICT_MANIFESTS`` env-var
      - final Phase 3 ledger close

  - Out of scope later (unchanged):
      - ``--backfill-manifests``, ``--rebuild-on-mismatch``,
        ``--verify-only``
      - environment.yml / requirements.txt hygiene
      - B11 ``compute_signals``
      - QC clone files
      - OnePass run reports JSON manifests

  - ELI5: every Spymaster cache pickle now carries the same kind of
    receipt that Phase 3A introduced for signal libraries: an
    embedded ``_manifest`` plus a JSON sidecar with a file-byte SHA.
    StackBuilder's ``run_manifest.json`` grew a detailed
    ``output_artifacts`` list with file SHAs for every rank /
    cohort / leaderboard / summary file the run produced, plus a
    record of which signal libraries fed into the run. The static
    guard that bans raw ``pickle.load`` in production code has
    tightened from "every consumer except a few specific
    Phase-3B-2-deferred sites" to "every consumer except the
    central loader itself" — every signal-library AND output
    pickle now flows through the central provenance contract.
  - Status: implemented in Phase 3B-2A (this PR). Phase 3 is NOT
    complete after 3B-2A — Phase 3B-2B covers XLSX upsert
    manifests, CLI strict-mode plumbing, and the final Phase 3
    close.

### Phase 3B-2A amendment (PR #143): collector isolation + save_ok guard

  Codex audit found two blockers in the original PR #143 commits 1-7.
  Both were fixed in-flight on the same PR.

  **Blocker 1 — collector isolation:**
    - Old (e5f3eb7 commits 1-7): the StackBuilder input-manifest
      collector was a single module-level dict guarded by an RLock
      (``_INPUT_MANIFEST_COLLECTOR``). The lock prevents data-structure
      races but does NOT isolate concurrent ``run_for_secondary``
      jobs. The Dash multi-secondary launch path at
      ``stackbuilder.py:1731`` spawns one ``threading.Thread`` per
      secondary, so two concurrent jobs would clobber each other's
      collector and produce wrong (or empty) ``input_manifest_hashes``
      in the resulting ``run_manifest.json``.
    - New (amendment): the collector lives in a
      ``contextvars.ContextVar`` (``_INPUT_COLLECTOR_VAR``). Each
      ``run_for_secondary`` invocation calls
      ``_start_input_manifest_collection()`` which constructs a fresh
      collector ``{"hashes": set(), "legacy": 0, "missing": 0,
      "lock": RLock()}`` and ``set()``s the ContextVar, returning the
      token. ``_finalize_input_manifest_collection(token)`` snapshots
      the run's state and ``reset()``s the ContextVar to its prior
      value (so nested or sibling runs are unaffected).
    - ContextVars do NOT automatically propagate from the submitter
      into long-lived ``ThreadPoolExecutor`` worker threads. The
      ``phase2_rank_all`` executor was therefore wrapped:
      ``_submit_with_context(executor, fn, *args, **kwargs)`` captures
      the caller's Context via ``contextvars.copy_context()`` and runs
      the worker callable inside it, so the per-run collector
      ContextVar is visible to ``_record_input_lib`` from inside
      ``_score_primary_both_modes`` workers.
    - Regression test:
      ``test_3b2a_collector_isolation_concurrent_runs``. Two threads
      barrier-rendezvous on collection start, each records a distinct
      set of synthetic manifested libraries via the production
      ``_submit_with_context`` wrapper, then finalizes. The test
      asserts both snapshots contain only their run's hashes
      (disjoint sets) and that legacy/missing counters are isolated
      too. Verified to fail on the e5f3eb7 module-global collector
      (cross-contamination / empty Run A snapshot) and to pass after
      the ContextVar + ``_submit_with_context`` migration.

  **Blocker 2 — save_ok sidecar gate:**
    - Old (e5f3eb7 commits 1-7): ``save_precomputed_results`` would
      write the new sidecar manifest even when the final pickle
      replacement failed. That left an orphan sidecar describing
      content that was never written, or caused legitimate older
      pickles still on disk to fail verification because their bytes
      no longer matched the (overwritten) sidecar file_sha256.
    - New (amendment): a local ``save_ok`` flag is set to True only
      after the final pickle replacement succeeds (either via
      ``os.replace`` or the ``shutil.copy2`` fallback). The sidecar
      write is gated on ``save_ok``; if neither path succeeded, no
      sidecar action is taken — any existing sidecar on disk is left
      alone so the older pickle continues to verify.
    - The ``save_precomputed_results`` public return contract is
      unchanged.
    - Regression test:
      ``test_3b2a_spymaster_save_failure_no_orphan_sidecar``.
      Pre-populates an OLD pickle + OLD sidecar, monkeypatches
      ``os.replace`` and ``shutil.copy2`` to fail, calls
      ``save_precomputed_results`` with NEW content, and asserts:
        * the call does not raise
        * the on-disk pickle still contains OLD content
        * the on-disk sidecar still contains OLD manifest bytes
        * no new sidecar describing NEW content was written
      Verified to fail on the un-gated sidecar write (orphan sidecar
      describing content that was never persisted) and to pass with
      the ``save_ok`` gate.

  Status: implemented in PR #143 amendment commits 8-9.


## Phase 3B-2B: XLSX Upsert Manifests + Strict-Mode CLI + Phase 3 Close

  - Type: ADDITIVE-PROVENANCE (no behavior change to scoring math).
    Closes Phase 3.

  - Old behavior:
      - OnePass and ImpactSearch result XLSX workbooks had no
        provenance manifest. A run combined newly-computed rows with
        previously-persisted rows in the same workbook (upsert by
        Primary Ticker) without recording which rows came from THIS
        run vs which were retained from a prior run.
      - StackBuilder's ``--prefer-impact-xlsx`` fast path would
        consume any matching ImpactSearch workbook found in the
        configured directory, regardless of whether the workbook had
        been mutated, partially upserted, or produced by an
        incompatible engine version.
      - TrafficFlow and Confluence Spymaster PKL consumers had a
        Phase 3B-2A non-strict policy (legacy proceeds, mismatch
        rejects). There was no env-driven strict mode for users who
        want every load to verify a manifest.
      - StackBuilder's per-run input-manifest collector silently fell
        back to ``set(None)`` when ``ContextVar.reset(token)`` failed
        across contexts. A future ContextVar mismanagement bug would
        not have surfaced until the symptom appeared in
        ``run_manifest.json``.

  - New behavior:

    XLSX manifest helper (``provenance_manifest.py``):
      - ``load_verified_xlsx_artifact(path, *, requested_params,
        strict)`` -> ``(DataFrame | None, VerificationResult)``
      - ``build_xlsx_output_manifest(...)`` -> XLSX-shape manifest
        builder
      - ``inspect_preexisting_xlsx_manifest(path)`` -> ``"none"`` /
        ``"legacy"`` / ``"valid"`` / ``"mismatched"`` classifier (run
        BEFORE overwriting a workbook so the new manifest can record
        the prior pair's status)
      - Internal helpers: ``_canonical_workbook_hash`` (SHA-256 over
        parsed-DataFrame logical content; preserves row order;
        distinguishes None / NaN / empty strings; columns serialized
        in DF order), ``_xlsx_key_strings`` (per-row normalized key
        with priority across multiple key_columns; matches OnePass
        and ImpactSearch dedupe semantics), ``_compute_legacy_row_count``
        (final-workbook rows whose normalized key was NOT touched by
        THIS run), ``_current_run_input_hash`` (normalized current-
        run rows AFTER exporter schema normalization — exporter-input
        provenance, not raw market-data provenance).

    Hash contract (DECISION 2):
      - ``full_workbook_content_hash`` digests parsed DataFrame
        logical content. NOT raw XLSX bytes (XLSX is a ZIP container
        with writer metadata that drifts across openpyxl versions).
      - ``artifact_file_sha256`` digests raw XLSX bytes; sidecar-only
        tamper check.
      - Both fields are required and serve different purposes.

    Sidecar naming (DECISION 3):
      - ``onepass.xlsx`` -> ``onepass.xlsx.manifest.json``
      - No hidden sheets, custom XML, or workbook-schema changes.
        Excel users and downstream tooling see the workbook
        unchanged.

    legacy_row_count semantics (DECISION 1):
      - "Rows retained in the FINAL workbook whose key tuple was NOT
        touched by the current run."
      - Full refresh over an existing workbook -> 0
      - Partial upsert that adds/updates only some keys -> non-zero
      - Strict consumers must not treat a partially verified workbook
        as fully verified.

    Producer sites:
      - ``onepass.export_results_to_excel``: classifies preexisting
        workbook+sidecar via ``inspect_preexisting_xlsx_manifest``
        BEFORE overwrite, runs the existing concat + dedupe logic
        unchanged, then writes the sidecar after the workbook is
        committed. ``key_columns=["Primary Ticker"]``.
      - ``impactsearch.export_results_to_excel``: same shape;
        ``key_columns=["Primary Ticker", "Resolved/Fetched"]`` with
        Primary Ticker priority recorded in
        ``params.key_priority``.
      - Both producers: sidecar write failures log a warning but do
        not fail the export.

    Consumer site (workbook-level accept/reject — DECISION 4):
      - ``stackbuilder.try_load_rank_from_impact_xlsx`` accepts a
        kw-only ``strict_manifests`` parameter and runs the freshest
        matching workbook through ``load_verified_xlsx_artifact``
        before serving the fast-path. Behavior:
          * non-strict + missing/legacy manifest -> warn, use fast-path
          * non-strict + mismatched manifest -> warn, reject (None)
          * non-strict + legacy_row_count > 0 -> warn, use fast-path
          * strict + missing/legacy/mismatched -> reject (None)
          * strict + legacy_row_count > 0 -> reject (None)
        Hard load errors (corrupt workbook) reject the fast-path
        uniformly.

    StackBuilder CLI:
      - ``--strict-manifests`` (default off): "Require verified
        manifests for manifest-aware fast paths; reject legacy/
        missing/mismatched ImpactSearch XLSX in strict mode."
      - ``phase2_rank_all`` hard-fail rule: when fast-path returns
        None under strict mode and NO primaries were provided,
        ``SystemExit`` with the prompt-required message ("provide
        primaries / repair manifest / drop --strict-manifests").
      - When primaries ARE provided, the fast-path rejection falls
        through to the slow path so the caller cohort is recomputed
        (the existing 70K-primary guard is bypassed under this
        specific shape).

    TrafficFlow strict env:
      - ``_strict_manifests_enabled()`` returns True when EITHER
        ``PRJCT9_STRICT_MANIFESTS`` (project-wide) or
        ``TRAFFICFLOW_STRICT_MANIFESTS`` (engine-local) is truthy
        ("1", "true", "yes", "on"). Strict propagates DOWNWARD only:
        a project-wide truthy value is not overridden by a local "0".
      - Under strict, ``load_spymaster_pkl`` returns None on legacy
        and mismatch and does NOT populate ``_PKL_CACHE`` so the
        next call re-checks. No SystemExit; TrafficFlow is
        UI / long-running.

    Confluence strict env:
      - Mirrors TrafficFlow with ``CONFLUENCE_STRICT_MANIFESTS``.
      - Under strict, ``_load_spymaster_cache_fallback`` skips the
        interval (returns None) on legacy and mismatch. The broader
        confluence load is unaffected; just this interval's fallback
        is skipped with a warning.

    Cross-context token reset logging (carry-forward):
      - ``stackbuilder._finalize_input_manifest_collection`` now logs
        a warning when ``ContextVar.reset(token)`` fails across
        contexts before falling back to ``set(None)``. The warning
        message: "Cross-context input-manifest collector token reset
        detected; clearing current collector. This may indicate
        ContextVar mismanagement." Appearance during the test suite
        would indicate a real issue; verified absent during the
        Phase 3B-2B test runs.

  - Affected tests/snapshots:
      - ``project/test_scripts/test_provenance_manifest.py``: +27
        Phase 3B-2B tests covering XLSX helper determinism, key
        normalization, legacy_row_count, missing-sidecar legacy vs
        strict, workbook content mismatch, preexisting status
        classification, OnePass + ImpactSearch upsert producer
        coverage (fresh / retained-row / full-refresh / mismatched),
        StackBuilder strict fast-path (legacy / mismatched / no-
        primaries SystemExit / primaries-provided fall-through), and
        TrafficFlow + Confluence strict env truthy parsing + legacy
        skip + mismatch skip.
      - 201 baseline -> 228 with Phase 3B-2B.

  - Phase 3 close:
      The Phase 3 provenance-manifest contract is now complete across
      all four sub-phases:
        - 3A:    signal-library manifests + Phase 3A B12
        - 3B-1:  perf cache + central loader + tightened B12
        - 3B-2A: output manifests + StackBuilder run manifests +
                 Spymaster PKLs + sanctioned standalone exception
        - 3B-2B: XLSX upsert manifests + strict-mode CLI + strict-env
                 modes + cross-context warning
      Every signal-library AND output artifact now flows through the
      central provenance contract. The B12 raw-pickle-load static
      guard allowlist contains exactly one entry
      (``provenance_manifest.py``).

  - Deferred (Phase 4+):
      - B11 ``compute_signals`` delete-or-shift-correct decision.
      - environment.yml / requirements.txt hygiene.
      - ``--backfill-manifests``, ``--rebuild-on-mismatch``,
        ``--verify-only`` CLI surfaces.
      - ``make_distinct_signal_library_dict`` test helper.
      - Ticker-in-canonical-content as diagnostic input entries (not
        as a content_hash replacement).
      - QC clone Adj Close sites.
      - OnePass run reports JSON manifests (separate provenance
        surface from the XLSX exports).

  - ELI5: every artifact PRJCT9 produces — signal libraries,
    Spymaster cache PKLs, StackBuilder run directories, OnePass
    XLSX, ImpactSearch XLSX — now carries a tamper-evident
    receipt. Reading any artifact through the central loader checks
    the receipt against the artifact bytes and the artifact content
    before consumers reuse it. Strict mode escalates "no manifest"
    and "manifest mismatch" from warnings to outright rejection,
    forcing a rebuild instead of silently consuming a possibly
    stale or partially-upserted workbook. After this PR, Phase 4
    (Cross-Ticker Confluence Dashboard) can rely on the manifest
    contract for cross-engine reproducibility.

  Status: implemented in Phase 3B-2B (this PR). **Phase 3 is
  COMPLETE.** Phase 4 (Cross-Ticker Confluence Dashboard) is now
  unblocked.

### Phase 3B-2B amendment (PR #144): canonical-cell type tags + boundary-safe row hashing

  Codex audit on PR #144 found one blocker plus one adjacent risk in the
  XLSX canonical encoder. Both were fixed in-flight on the same PR.

  **Blocker — type-vs-string collisions in `_xlsx_canonical_cell`:**
    - Old (29e45f0 commits 1-6): the encoder used `repr()` for
      ints / bools / numpy scalars, `repr()` for floats, `isoformat()`
      for timestamps, and a bare `str()` fallback for strings. With no
      type tag and no string-payload escaping, real distinct cells
      produced colliding encodings:
        * `1` (int) and `"1"` (str) -> both `"1"`
        * `True` and `"True"` -> both `"True"`
        * `1.0` (float) and `"1.0"` -> both `"1.0"`
        * `pd.Timestamp("2026-05-04T00:00:00")` and the matching ISO
          string -> both `"2026-05-04T00:00:00"`
      A workbook that swapped a typed cell for its string twin would
      produce the same `full_workbook_content_hash`.
    - New (amendment): every encoding carries an explicit `<type>:`
      prefix (`none:`, `nan:`, `bool:<True|False>`, `int:<n>`,
      `float:<repr(f)>`, `timestamp:<iso>`, `str:<json.dumps(s)>`).
      String payloads go through `json.dumps(ensure_ascii=True)` so
      quotes / backslashes / control characters / sentinel-like
      literals are unambiguously escaped. ``bool`` is dispatched
      BEFORE ``int`` because ``isinstance(True, int)`` is True
      (bool inherits from int in Python). NumPy scalars
      (``np.bool_``, ``np.integer``, ``np.floating``) follow the
      same contract as their Python counterparts. ``pd.isna`` is
      tried before string fallback so pandas-flavored missing values
      (NaT, pd.NA, NaN inside object columns) collapse to ``"nan:"``.
    - Side benefit: the prior ``\x00__NONE__\x00`` /
      ``\x00__NAN__\x00`` sentinels are retired in favor of the
      tagged ``"none:"`` / ``"nan:"`` strings, which are
      ``json.dumps``-safe and human-readable in failure messages.

  **Adjacent risk — row-boundary ambiguity in `_canonical_workbook_hash`:**
    - Old: row encoding concatenated raw cell strings with ``"|"``
      delimiters: ``cell1|cell2|cell3|\n``. A row like
      ``["x|", "y"]`` produced bytes ``"x||y|"``, identical to what
      ``["x", "|y"]`` produced. Newlines in cell content could
      similarly bleed across the row separator.
    - New: each row is serialized as a JSON list of tagged cell
      encodings (``json.dumps(encoded_row, ensure_ascii=True,
      separators=(",", ":"))``). Cell boundaries are unambiguous;
      delimiter / newline content inside cells cannot bleed across
      cells or across rows.

  **Comment correction:**
    - Pre-amendment comment block at line ~1510 said the sidecar
      naming was ``<artifact>.manifest.json`` (e.g. ``SPY_analysis.xlsx
      -> SPY_analysis.manifest.json``). The actual sidecar suffix is
      ``.xlsx.manifest.json``: ``SPY_analysis.xlsx ->
      SPY_analysis.xlsx.manifest.json``. Comment corrected.

  **Backwards compatibility:**
    PR #144 has not shipped. No on-disk legacy XLSX manifests exist
    yet at the new schema, so the encoder change has zero legacy
    impact. A repo-wide grep confirmed no existing test contained a
    hardcoded XLSX hash value that the new encoder would invalidate.

  **Regression tests (8 new):**
    - ``test_3b2b_canonical_cell_int_vs_str_no_collision``
    - ``test_3b2b_canonical_cell_bool_vs_str_no_collision``
    - ``test_3b2b_canonical_cell_float_vs_str_no_collision``
    - ``test_3b2b_canonical_cell_timestamp_vs_iso_str_no_collision``
    - ``test_3b2b_canonical_cell_sentinel_literal_vs_sentinel``
    - ``test_3b2b_canonical_cell_numpy_scalars_tagged``
    - ``test_3b2b_canonical_workbook_hash_cell_boundary_safe``
    - ``test_3b2b_xlsx_upsert_fills_empty_cell_changes_hash``

  All 6 collision tests + the boundary-safe test failed under
  29e45f0 and pass after the fix. The sentinel-literal test
  (``test_3b2b_canonical_cell_sentinel_literal_vs_sentinel``) and
  the empty-cell upsert test
  (``test_3b2b_xlsx_upsert_fills_empty_cell_changes_hash``) also
  passed under the old encoder by accident (the old NaN/None
  sentinels happened to be ``\x00``-bracketed, not collidable with
  the literal text); they remain as documented invariants.

  Status: implemented in PR #144 amendment commit 7.
