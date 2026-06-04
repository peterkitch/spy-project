// String constants mirroring mvp_signal_board.py so the Dash and
// React surfaces carry identical user-facing copy.

export const BOARD_HEADER = "PRJCT9 Daily Signal Board";
export const K6_MTF_BOARD_SUBHEADER = "K=6 MTF";
export const K6_MTF_SURFACE_DISTINGUISHER =
  "K=6 MTF (stack-derived; distinct from OnePass-MTF)";
export const K6_MTF_VALIDATION_DISCLOSURE = [
  "Validation status (Phase 5 honest-validation).",
  "",
  "This board is a K=6 MTF leaderboard, ordered by K=6 MTF",
  "performance metrics (Sharpe and total capture over the",
  "per-secondary history window). Leaderboard position does",
  "NOT reflect Phase 5 multiple-comparisons survivorship.",
  "",
  "Backing evidence: Phase 5 honest-validation report at",
  "md_library/shared/2026-06-01_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT.md.",
  "",
  "Of the 8 candidates tested, 4 cleared the Phase 5",
  "Benjamini-Hochberg plus empirical-permutation validation",
  "gate: AMZN, GOOGL, NVDA, TSLA. AAPL, META, MSFT, SPY did",
  "not clear the BH gate; META was outside the empirical",
  "subset (empirical_not_run).",
  "",
  "Research only. Not investment advice. Past performance",
  "does not guarantee future results.",
].join(" ");
// PR-2: short guardrail used in the detail modal to keep ranking status
// and validation status from being conflated.
export const K6_MTF_RANKING_VS_VALIDATION_NOTE =
  "Ranking status (leaderboard position) is independent of validation "
  + "status. A high rank does NOT imply statistical validation; only "
  + "rows marked Validation: PASS cleared the Phase 5 Benjamini-Hochberg "
  + "gate.";

// PR-2: schema-aware v2 disclosure. Counts are taken from the artifact's
// validation_summary / validation_metadata (NOT hardcoded), so this copy
// stays correct for any v2 fixture (test fixtures use tiny counts). The
// old hardcoded 8-ticker copy (K6_MTF_VALIDATION_DISCLOSURE below) is
// shown ONLY for v1 artifacts.
export function buildK6MtfV2ValidationDisclosure(
  boardValidated: number | null | undefined,
  notValidated: number | null | undefined,
  tested: number | null | undefined,
  runId: string | null | undefined,
): string {
  const bv = numOrQuestion(boardValidated);
  const nv = numOrQuestion(notValidated);
  const t = numOrQuestion(tested);
  const rid = runId && runId.length > 0 ? runId : "unknown";
  return [
    "Validation status (Phase 5 honest-validation).",
    "",
    "All ranked rows are research-ranked by K=6 MTF performance",
    "(Sharpe and total capture over the per-secondary history window).",
    "Leaderboard position does NOT reflect Phase 5 survivorship.",
    "",
    `Of ${t} secondaries tested, ${bv} cleared the Phase 5`,
    "Benjamini-Hochberg validation gate (Validation: PASS).",
    `${nv} ranked rows did not clear the gate and are shown for`,
    "research transparency, not as board-validated rows.",
    "Rows marked \"Not enough trigger days\" were not testable due to",
    "sparse directional triggers (empirical_not_run).",
    "",
    "Stage-A-excluded secondaries are omitted from the ranked board",
    "and listed separately in the coverage section below.",
    "",
    `Validation run: ${rid}.`,
    "",
    "Research only. Not investment advice. Past performance",
    "does not guarantee future results.",
  ].join(" ");
}

function numOrQuestion(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : "?";
}

export const DISCLAIMER =
  "Historical performance does not guarantee future returns.";
export const EMPTY_TABLE_MESSAGE =
  "No ranked secondaries available in this run.";
export const K6_MTF_UNRANKED_EMPTY_MESSAGE =
  "No failed or unranked records in this run.";
export const K6_MTF_UNRANKED_SECTION_TITLE =
  "Failed or unranked records (K=6 MTF)";
export const CCC_EMPTY_MESSAGE =
  "No matching historical bars in this run; CCC chart unavailable.";

export const V1_TIMEFRAMES = ["1d", "1wk", "1mo", "3mo", "1y"] as const;
