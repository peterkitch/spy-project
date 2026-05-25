# PRJCT9 Known Bugs Log

**Date:** 2026-05-25

**Status:** Living document. Append-only entries; do not delete entries when bugs are fixed. Mark resolved entries with a date and PR reference instead.

---

## Purpose

This document tracks bugs that have been investigated and deliberately deferred, so they do not get lost between sprints. When a sprint surfaces a bug that is real but not on the current launch-path critical path, the bug is recorded here with enough context that a future operator or contributor can pick it up cleanly.

Each entry records:

- the bug
- root cause, if known
- blast radius across the four Confluence Concepts from the 2026-05-25 Confluence Terminology Glossary (PR #323)
- reason for deferral
- conditions that force revisit

This document does NOT track:

- open architectural decisions (those live in audit docs and sprint-state notes)
- carry-forward sprint items (those live in dedicated carry-forward / sprint-state docs)
- test failures or build issues being addressed directly in active PRs

When a deferred bug is fixed, do not delete its entry; move it under "Resolved Entries" with the fix date and PR reference.

---

## Entry Format

Each entry uses the following structure:

- **ID:** BUG-NNN
- **Discovered:** YYYY-MM-DD
- **Discovered during:** context (sprint phase, test surface, operator action)
- **Affected scripts:** file paths
- **Symptom:** operator- or test-visible behavior
- **Root cause:** verified or hypothesized technical cause, with function names and `file:line` references where known
- **Blast radius:** which Confluence Concepts (1, 2, 3, 4) inherit the bug
- **Deferral rationale:** why it is not being fixed now
- **Revisit trigger:** what should force a fix
- **Status:** Open, or Resolved with date and PR reference

---

## Active Entries

### BUG-001: confluence.py multi-primary 1mo metrics produce near-all-zero return vector

- **ID:** BUG-001
- **Discovered:** 2026-05-25
- **Discovered during:** Operator testing of Concept 1, multi-primary mode, in `confluence.py` on port 8056. Test case: primaries=SPY,JNJ and secondary=AAPL across the canonical 1d / 1wk / 1mo / 3mo / 1y interval set.
- **Affected scripts:** `confluence.py`, especially the non-daily multi-primary return-grid path in `_mp_eval_interval` and the related capture path in `_confluence_capture_series_for_interval`.
- **Symptom:** The Multi-Primary Results table 1mo row reported 328 triggers, 0 wins, 328 losses, Sharpe around -62.67, Avg around -0.0013%, Total around -0.4184%. The same run emitted repeated diagnostic lines of the form `[1mo] return sanity: nonzero=1 / N`, indicating that the return series entering metric computation contained essentially one non-zero value out of N. The metric output is structurally impossible for an honestly-computed monthly return series and the diagnostic line is the smoking gun.
- **Root cause:** Partially verified. The suspicious runtime path computes interval returns and then reindexes / fills missing values inside `confluence.py`'s non-daily grid. The 2026-05-25 audit corrected the initial calendar-mismatch theory: the current code explicitly intersects evaluation dates with `sec_close.index`, so a simple primary-vs-secondary date mismatch would shrink the date set rather than flood it with zeros. The exact internal failure point therefore lives in `confluence.py`'s runtime metric construction, not in the stored 1mo primary signal libraries. The same risky non-daily pattern applies to 1wk / 1mo / 3mo / 1y, but only 1mo currently emits the explicit sanity log, so the other intervals are not proven clean.
- **Library integrity finding:** Claude Code's 2026-05-25 read-only PKL diagnostic verified that the stored SPY and JNJ 1mo signal libraries are structurally healthy. SPY had 400 / 400 nonzero close values and normal derived monthly returns (mean ~0.80% per month, stdev ~4.28%). JNJ had 496 / 496 nonzero close values and normal derived monthly returns (mean ~1.08% per month, stdev ~5.56%). AAPL's 1mo library was not present in that diagnostic, but the bad `confluence.py` run fetches secondary close data through a separate path, so the primary 1mo libraries are ruled out as the source of this observed corruption.
- **Blast radius:** Concept 1 is directly affected. Concept 2 may be conditionally affected where it shares `confluence.py` helper paths. Concept 3 is not affected by this `confluence.py` runtime path. Concept 4, the launch path, is not affected by this observed bug based on current evidence; it does not consume `confluence.py`'s interval-grid metric path.
- **Deferral rationale:** Concept 1 is not on the launch path per the 2026-05-25 Confluence Terminology Glossary (PR #323). This is currently an operator-tool bug, not a launch-path blocker. The next launch-path work should stay focused on TrafficFlow Producer A vs Producer B reconciliation and on Concept 4 pipeline validation.
- **Revisit trigger:** Revisit before exposing Concept 1 or Concept 2 publicly, before relying on their non-daily metrics for research conclusions, or if later evidence shows the bug propagates into Concept 3 or Concept 4.
- **Status:** Open.

### BUG-002: confluence.py Multi-Primary results table flashes and disappears on first click

- **ID:** BUG-002
- **Discovered:** 2026-05-25
- **Discovered during:** Operator testing of Concept 1, multi-primary mode, in `confluence.py` on port 8056.
- **Affected scripts:** `confluence.py`, especially the diagnostics callback and the multi-primary results-rendering callback.
- **Symptom:** Clicking Run Multi-Primary Analysis causes the results table to render briefly and then disappear behind a diagnostics-panel re-render. A second click typically succeeds.
- **Root cause:** Verified as a callback collision. The diagnostics callback listens to the same run-multi-primary click input as the results-rendering callback. If the diagnostics callback completes after the results callback, it can overwrite the visible results.
- **Blast radius:** Concept 1 is directly affected. No other Confluence Concept uses this Dash callback graph. This is a UX bug, not data corruption.
- **Deferral rationale:** Same as BUG-001. Concept 1 is not on the launch path, and the bug has a known operator workaround (click again). Fixing it does not unblock the launch-path pipeline.
- **Revisit trigger:** Revisit before exposing Concept 1 publicly or before relying on `confluence.py` as a serious operator surface. If BUG-001 is fixed, this should likely be fixed in the same PR or an adjacent PR.
- **Status:** Open.

---

## Resolved Entries

(none yet)

---

## References

- `md_library/shared/2026-05-25_CONFLUENCE_TERMINOLOGY_GLOSSARY.md` (PR #323): defines the four Confluence Concepts referenced throughout this log.
- Codex 2026-05-25 1mo data integrity investigation: scoped BUG-001 and recommended deferral conditional on healthy stored 1mo libraries.
- Claude Code 2026-05-25 read-only 1mo PKL diagnostic: verified SPY and JNJ 1mo libraries were structurally healthy, satisfying the condition for BUG-001 deferral.
- 2026-05-25 operator testing of `confluence.py` Concept 1 multi-primary mode: surfaced both BUG-001 and BUG-002.
