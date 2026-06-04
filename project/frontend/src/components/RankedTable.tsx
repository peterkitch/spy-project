import type { PerSecondary } from "../types";
import { columnsForVisible, type RankedTableColumn } from "../columnsFor";
import { formatSharpe } from "../format";
import { UNAVAILABLE } from "../format";
import { ValidationBadge } from "./ValidationBadge";

interface RankedTableProps {
  visibleRows: PerSecondary[];
  activeTicker: string | null;
  onSelect: (ticker: string) => void;
}

// Renders per_secondary in engine-emitted order. NO client-side
// re-sort, re-rank, or re-filter. The Status column is hidden
// when the predicate in columnsForVisible fires (PR #364 parity).
export function RankedTable({
  visibleRows,
  activeTicker,
  onSelect,
}: RankedTableProps) {
  const columns = columnsForVisible(visibleRows);
  return (
    <table id="mvp-board-table" className="mvp-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.id} scope="col">{c.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {visibleRows.map((row) => (
          <tr
            key={row.secondary}
            className={
              row.secondary === activeTicker
                ? "mvp-row mvp-row-active"
                : "mvp-row"
            }
            onClick={() => onSelect(row.secondary)}
            tabIndex={0}
            role="button"
            aria-label={`Open detail for ${row.secondary}`}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(row.secondary);
              }
            }}
          >
            {columns.map((c) => (
              <td key={c.id}>
                {c.id === "validation" ? (
                  <ValidationBadge row={row} />
                ) : (
                  renderCell(c, row)
                )}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function renderCell(column: RankedTableColumn, row: PerSecondary): string {
  switch (column.id) {
    case "rank":
      return row.rank === null || row.rank === undefined
        ? UNAVAILABLE
        : String(row.rank);
    case "ticker":
      return row.secondary || UNAVAILABLE;
    case "sharpe_score":
      return formatSharpe(row.sharpe_k6_mtf);
    case "status":
      return row.status || UNAVAILABLE;
    default:
      return UNAVAILABLE;
  }
}
