import type { PerSecondary } from "./types";

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

export interface RankedTableColumn {
  id: "rank" | "ticker" | "sharpe_score" | "status";
  label: string;
}

const BASE_COLUMNS: RankedTableColumn[] = [
  { id: "rank", label: "Rank" },
  { id: "ticker", label: "Ticker" },
  { id: "sharpe_score", label: "Sharpe Score" },
  { id: "status", label: "Status" },
];

export function columnsForVisible(rows: PerSecondary[]): RankedTableColumn[] {
  const noMeaningfulStatus = rows.every((r) => {
    const s = r.status;
    return s === null || s === undefined || s === "" || s === "ranked";
  });
  if (!noMeaningfulStatus) {
    return BASE_COLUMNS;
  }
  return BASE_COLUMNS.filter((c) => c.id !== "status");
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
