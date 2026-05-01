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
  - Old behavior: TBD in 1B-2 (see inventory §2a — ~15 distinct sites
    across spymaster, onepass, impactsearch, stackbuilder, confluence,
    signal_library, stale_check, plus boot logs and UI banners).
  - New behavior: TBD in 1B-2.
  - Affected tests/snapshots: TBD in 1B-2.
  - ELI5: yfinance's "Adj Close" column changes over time as
    dividends and splits get retroactively reapplied, so the same
    historical date can return slightly different prices on
    different days. That kills reproducibility. The spec says use
    raw `Close` only and remove every Adj/raw selector. After this
    entry, every engine reads raw Close and there is no
    `PRICE_BASIS` env var or argument left to tweak.
  - Status: stub, pending 1B-2.

## Entry 2: ddof=0 / implicit ddof -> ddof=1

  - Type: EXPECTED-BY-SPEC
  - Old behavior: TBD in 1B-2 (see inventory §5; canonical-scoring
    sites at `spymaster.py:11668` and `spymaster.py:12601` use
    implicit ddof=0; sister-engine helpers already use ddof=1).
  - New behavior: TBD in 1B-2.
  - Affected tests/snapshots: TBD in 1B-2.
  - ELI5: standard deviation has two flavors. "Population" std (ddof
    = 0) divides by N; "sample" std (ddof = 1) divides by N - 1.
    Sample std is the correct choice when the trigger days we
    observed are themselves a sample from the larger universe of
    possible trigger days. The spec mandates ddof = 1 everywhere;
    most of the codebase already does this, but two spymaster sites
    silently use ddof = 0 today. This entry harmonizes them.
  - Status: stub, pending 1B-2.

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
