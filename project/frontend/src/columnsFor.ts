import type { PerSecondary } from "./types";
import { hasValidationOutcome } from "./validationDisplay";

// Mirrors mvp_signal_board.py _k6_mtf_board_columns_for_visible
// (PR #364). When every visible ranked row's raw status is in
// {null, "", "ranked"} the Status column is omitted from the
// primary table because every cell would read "ranked" or
// "Unavailable" (visual noise). As soon as any visible row
// carries a meaningful non-"ranked" non-empty status, Status is
// preserved.
//
// Status is always preserved in the modal regardless of what
// this helper returns.
//
// PR-2: a Validation column is appended only when at least one visible
// row carries a validation_outcome (i.e. a v2 fixture). For v1 fixtures
// no row has a validation_outcome, so the column is omitted and the
// board is byte-for-byte the same as before.

export interface RankedTableColumn {
  id: "rank" | "ticker" | "sharpe_score" | "status" | "validation";
  label: string;
}

const RANK_COL: RankedTableColumn = { id: "rank", label: "Rank" };
const TICKER_COL: RankedTableColumn = { id: "ticker", label: "Ticker" };
const SHARPE_COL: RankedTableColumn = { id: "sharpe_score", label: "Sharpe Score" };
const STATUS_COL: RankedTableColumn = { id: "status", label: "Status" };
const VALIDATION_COL: RankedTableColumn = { id: "validation", label: "Validation" };

export function columnsForVisible(rows: PerSecondary[]): RankedTableColumn[] {
  const columns: RankedTableColumn[] = [RANK_COL, TICKER_COL, SHARPE_COL];
  const noMeaningfulStatus = rows.every((r) => {
    const s = r.status;
    return s === null || s === undefined || s === "" || s === "ranked";
  });
  if (!noMeaningfulStatus) {
    columns.push(STATUS_COL);
  }
  if (rows.some((r) => hasValidationOutcome(r))) {
    columns.push(VALIDATION_COL);
  }
  return columns;
}

// Mirrors _k6_mtf_visible_rows: only records with rank != null
// appear in the primary ranked table.
export function visibleRows(rows: PerSecondary[]): PerSecondary[] {
  return rows.filter((r) => r.rank !== null && r.rank !== undefined);
}

// Mirrors _k6_mtf_unranked_rows: rank null OR status in
// {"unranked","failed"}.
export function unrankedRows(rows: PerSecondary[]): PerSecondary[] {
  return rows.filter((r) => {
    if (r.rank === null || r.rank === undefined) {
      return true;
    }
    return r.status === "unranked" || r.status === "failed";
  });
}
