// String constants mirroring mvp_signal_board.py so the Dash and
// React surfaces carry identical user-facing copy.

export const BOARD_HEADER = "PRJCT9 Daily Signal Board";
export const K6_MTF_BOARD_SUBHEADER = "K=6 MTF";
export const K6_MTF_SURFACE_DISTINGUISHER =
  "K=6 MTF (stack-derived; distinct from OnePass-MTF)";
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
