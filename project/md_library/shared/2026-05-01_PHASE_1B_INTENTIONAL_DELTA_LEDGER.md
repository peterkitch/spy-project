# Phase 1B Intentional Delta Ledger (skeleton)

Document date: 2026-05-01
Branch: phase-1b-1-inventory-canonical-module
Status: skeleton; all entries pending Phase 1B-2.

This ledger is the public record of every behavior diff that lands
during the Phase 1B canonical-scoring rewire. Each entry is one
classified change. A baseline test that flips silently — without an
entry in this ledger — is treated as a regression.

For each entry, Phase 1B-2 fills in:

  - Old behavior: the current main behavior, cited with file:line.
  - New behavior: the post-rewire behavior, cited with file:line and
    the canonical_scoring API used.
  - Affected tests/snapshots: the Phase 1A tests / snapshots flipped
    by this entry, plus any new Phase 1B-2 tests added.
  - Status: stub | drafted | implemented | landed.

Each entry also carries an ELI5 explanation so non-engineers can
read the ledger and understand what changed and why.

Reference inventory:
  project/md_library/shared/2026-05-01_PHASE_1B_IMPLEMENTATION_INVENTORY.md

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
  - Remaining sites for 1B-2A follow-up commits:
      `spymaster.py` (`_PRICE_BASIS` env read; PRICE_COLUMN; results
      pkl `'price_basis'` and `'last_adj_close'` fields; refusal-
      to-guess column-presence check; yfinance `auto_adjust=False`
      and Adj-Close-fallback branches; reader at line 11858).
      `onepass.py` (UI banner; `compute_parity_hash` default;
      env-driven price-basis blocks at lines 1268–1325 and 1835).
      `impactsearch.py` (boot log; cache-key basis tag at line
      1122; env-driven price-basis blocks at lines 1359, 1414,
      2056, 2437; UI banner at 2602).
      `stackbuilder.py` (`load_secondary_prices` /
      `_fetch_secondary_from_yf` `price_basis` parameter; UI default
      `args.price_basis='adj'`; run-metadata field).
      `confluence.py` (`price_basis` cache-key plumbing in the
      cache-key normalizer and three `_cached_fetch_interval_data`
      callers).
  - ELI5: yfinance's "Adj Close" column changes over time as
    dividends and splits get retroactively reapplied, so the same
    historical date can return slightly different prices on
    different days. That kills reproducibility. The spec says use
    raw `Close` only and remove every Adj/raw selector. After this
    entry, every engine reads raw Close and there is no
    `PRICE_BASIS` env var or argument left to tweak.
  - Status: partially landed (signal_library + stale_check). Engine
    sites enumerated above are pending the next 1B-2A commits.

## Entry 2: ddof=0 / implicit ddof -> ddof=1

  - Type: EXPECTED-BY-SPEC
  - Old behavior: TBD in 1B-2. Per inventory §5 the receiver type
    matters: NumPy arrays default to `ddof=0`, pandas Series default
    to `ddof=1`. The only canonical-scoring site whose effective ddof
    differs from the spec is `spymaster.py:11668` (`cap` is a NumPy
    array, currently ddof=0). The remaining implicit-ddof sites are
    pandas Series receivers and are already ddof=1; making `ddof=1`
    explicit at those sites is a clarity-only change.
  - New behavior: TBD in 1B-2 (a canonical scoring call routes
    through `canonical_scoring.score_signals` / `score_captures`,
    which always use `ddof=1` by default).
  - Affected tests/snapshots: TBD in 1B-2. The `spymaster.py:11668`
    fix is the only ddof entry expected to introduce a numeric
    delta. Phase 1A does not pin the Spymaster end-to-end path, so
    even that delta will surface only via Phase 1B-2's end-to-end
    checks; no Phase 1A snapshot file is expected to flip from this
    entry alone.
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
  - Status: numeric-delta site fixed in 1B-2A. The single ddof=0
    canonical-scoring site `spymaster.py:11668` is now
    `cap[trigger_mask].std(ddof=1) if trigger_days > 1 else 0`,
    matching spec §16. The downstream Sharpe / t-stat / p-value
    chain in that block already gates on `std_dev > 0`, so the
    `trigger_days == 1` case continues to short-circuit to the
    no-stats branch (no behaviour difference for that case). Phase
    1A snapshots do not pin this code path, so no Phase 1A snapshot
    flips. The remaining six implicit-but-already-ddof=1 pandas
    Series sites in spymaster (1481, 1542, 8873, 8920, 10605,
    12601) are pending a clarity-only `ddof=1`-explicit pass; they
    are not numeric deltas.

## Entry 3: cdf -> sf p-value

  - Type: EXPECTED-BY-SPEC
  - Old behavior: TBD in 1B-2 (see inventory §6; every p-value
    site uses `2 * (1 - stats.t.cdf(abs(t), df=df))`).
  - New behavior: TBD in 1B-2.
  - Affected tests/snapshots: TBD in 1B-2.
  - ELI5: for very large t, `cdf(|t|)` is so close to 1.0 that
    `1 - cdf(|t|)` rounds to exactly zero in float64, and the
    resulting p-value is reported as exactly 0. SciPy's `t.sf`
    ("survival function") computes the same tail probability
    directly without that subtraction, so it stays a tiny but
    nonzero number for all t. The spec mandates the sf form.
  - Status: stub, pending 1B-2.

## Entry 4: zero-capture trigger-day counting

  - Type: BUG-FIX
  - Old behavior: TBD in 1B-2 (see inventory §7; sites
    `stackbuilder.py:442`, `trafficflow.py:1600`, plus the legacy
    fallback paths in onepass / impactsearch `_metrics_from_ccc`
    drop zero-capture days from the trigger mask).
  - New behavior: TBD in 1B-2 (signal-state trigger mask everywhere;
    zero-capture trigger days count as losses).
  - Affected tests/snapshots: TBD in 1B-2 (will at minimum touch
    `test_stackbuilder_metrics_from_captures_baseline`,
    `test_trafficflow_metrics_like_spymaster_baseline`, and the
    legacy `_metrics_from_ccc` snapshots).
  - ELI5: a "trigger day" is any day where the strategy actually has
    a position (Buy or Short). Some current code asks "did the
    capture move on that day?" instead of "was there a position
    that day?" — those two questions agree until the position is
    held over a day with zero return, in which case the second
    question correctly counts the day and the first one drops it.
    The spec is explicit: zero-return days under an active position
    are still trigger days, and they count as losses.
  - Status: stub, pending 1B-2.

## Entry 5: StackBuilder Phase 2 vs Phase 3 scoring divergence

  - Type: BUG-FIX
  - Old behavior: TBD in 1B-2 (see inventory §15; Phase 2's
    `apply_signals_to_secondary` uses `DEFAULT_GRACE_DAYS=7` while
    Phase 3's `_signals_aligned_and_mask` defaults grace to `0`,
    and the two paths score against different calendar-aligned
    capture series for the same primary).
  - New behavior: TBD in 1B-2 (one calendar-alignment policy and
    one trigger-mask policy across both phases via the canonical
    scoring module).
  - Affected tests/snapshots: TBD in 1B-2 (the Phase 1A
    `_pending_bug_fix` test
    `test_stackbuilder_combined_metrics_signals_baseline_pending_bug_fix`
    will flip; the suffix is dropped after this entry lands).
  - ELI5: the same K=1 stack can return two different Sharpe and
    Total Capture numbers depending on whether you scored it
    through Phase 2's "rank everything" path or Phase 3's
    "build best stack" path, because the two paths use
    different rules for filling missing trading days. Codex
    sampled 10 real K=1 outputs and every one of them mismatched.
    The spec says one canonical scoring function; this entry
    routes both phases through it.
  - Status: stub, pending 1B-2.

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
  - Old behavior: TBD in 1B-2 — for each Phase 1A test that flips,
    the prior snapshot constant in
    `project/test_scripts/phase1a_baseline_snapshots.py`.
  - New behavior: TBD in 1B-2 — the new snapshot constant captured
    after the corresponding ledger entry lands.
  - Affected tests/snapshots: TBD in 1B-2; this entry will be a
    table of (test_name, ledger_entry, old_snap, new_snap). Each
    snapshot replacement happens in a single ledger-attributable
    commit whose message names the parent ledger entry.
  - ELI5: any Phase 1A baseline test that changes its expected
    output during the rewire gets a one-line entry here naming
    which ledger item drove the change. This is the audit trail
    that lets a reviewer follow each diff back to a classified
    decision.
  - Status: stub, pending 1B-2.
