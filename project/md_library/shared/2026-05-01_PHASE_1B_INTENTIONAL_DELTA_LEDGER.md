# Phase 1B Intentional Delta Ledger

Document date: 2026-05-01
Branch: phase-1b-2a-canonical-rewire
Status: implemented in PR #132.

Per-entry status (1B-2A delivery):
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
  - Entry 6 (ImpactSearch xlsx duplicate-row dedupe): deferred to
    1B-2B per scope note.
  - Entry 7 (calendar grace days default unification to 10):
    deferred to 1B-2B per scope note (the path-level unification
    landed in Entry 5).
  - Entry 8 (sentinel pair standardization): deferred to 1B-2B
    (paired with the dead streaming-path removal).
  - Entry 9 (TrafficFlow cache key normalization): deferred to
    1B-2B.
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
  - Status: calendar-policy unification implemented in 1B-2A;
    `_pending_bug_fix` test retired in the same PR alongside
    the Entry 4 zero-capture fix.

## Entry 6: ImpactSearch xlsx duplicate-row dedupe

  - Type: BUG-FIX
  - Old behavior: TBD in 1B-2 (see inventory §14;
    `impactsearch.py:1933–1947` reads any existing xlsx and
    concatenates new rows on top with no dedupe).
  - New behavior: TBD in 1B-2 (dedupe by `Primary Ticker`, or
    overwrite-and-replace; final policy decided in 1B-2).
  - Affected tests/snapshots: TBD in 1B-2 (the Phase 1A
    `_pending_bug_fix` test
    `test_impactsearch_export_writes_duplicates_pending_bug_fix`
    will flip; the suffix is dropped after this entry lands).
  - ELI5: today, if you re-run ImpactSearch and it writes to an
    xlsx that already exists, every row gets duplicated. After
    this entry, a re-run produces the right number of rows.
  - Status: stub, pending 1B-2.

## Entry 7: calendar grace days default unification

  - Type: EXPECTED-BY-SPEC
  - Old behavior: TBD in 1B-2 (see inventory §9; defaults are
    split — `7` in impactsearch / stackbuilder / impact_fastpath,
    `0` at one stackbuilder site, `3` in QC).
  - New behavior: TBD in 1B-2 (default `10` per spec §20).
  - Affected tests/snapshots: TBD in 1B-2.
  - ELI5: trading calendars differ across markets; "grace days" is
    how far we let a missing day on one calendar pad against the
    nearest valid day on another. The codebase has at least three
    different defaults today, which contributes to the StackBuilder
    Phase 2 vs Phase 3 divergence above. The spec mandates a
    single default of 10. Grace days never change computed
    metrics on overlapping days; they only affect which days
    count.
  - Status: stub, pending 1B-2.

## Entry 8: sentinel pair standardization

  - Type: BUG-FIX
  - Old behavior: TBD in 1B-2 (see inventory §8; the dead streaming
    path uses `(1, 2)` / `(2, 1)`; live vectorized / leader
    fallback uses `(MAX_SMA_DAY, MAX_SMA_DAY - 1)` /
    `(MAX_SMA_DAY - 1, MAX_SMA_DAY)`).
  - New behavior: TBD in 1B-2 (single MAX-SMA sentinel everywhere;
    largely a side effect of removing the dead streaming path —
    inventory §16).
  - Affected tests/snapshots: TBD in 1B-2.
  - ELI5: when the engine has no valid pair to choose on a given
    day, it inserts a placeholder pair so downstream code does not
    crash. Today the placeholder differs depending on which code
    path inserted it. After this entry, there is one placeholder
    everywhere, matching what `MAX_SMA_DAY` already chose.
  - Status: stub, pending 1B-2.

## Entry 9: TrafficFlow cache key normalization

  - Type: BUG-FIX if behavior-visible
  - Old behavior: TBD in 1B-2 (see inventory §11; some read/write
    sites use the literal `secondary` argument; `_load_secondary_prices`
    writes only the uppercase form. A mixed-case lookup after an
    uppercase write misses and falls through to a fetch).
  - New behavior: TBD in 1B-2 (one normalization rule across all
    cache reads and writes).
  - Affected tests/snapshots: TBD in 1B-2 (Phase 1A's TrafficFlow
    test uses `'SYN'`, which is already uppercase, so it does not
    flip).
  - ELI5: today, asking the cache for `"spy"` and asking for
    `"SPY"` can give different answers because the writer and the
    reader disagree on whether to uppercase the key. After this
    entry, the cache is case-consistent.
  - Status: stub, pending 1B-2.

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
