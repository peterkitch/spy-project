import type { PerSecondary } from "../types";
import { unrankedRows } from "../columnsFor";
import {
  K6_MTF_UNRANKED_EMPTY_MESSAGE,
  K6_MTF_UNRANKED_SECTION_TITLE,
} from "../constants";
import { formatSharpe, UNAVAILABLE } from "../format";

interface UnrankedSectionProps {
  rows: PerSecondary[];
}

// Mirrors mvp_signal_board.py _render_k6_mtf_unranked_section.
// For the live fixture all 8 records are status="ranked" with
// non-null rank, so this section renders only the empty-state
// placeholder. Full per-record rendering remains in scope so the
// component continues to work if a future artifact carries
// failed / unranked records.
export function UnrankedSection({ rows }: UnrankedSectionProps) {
  const unranked = unrankedRows(rows);
  return (
    <section id="k6mtf-unranked-section" className="unranked-section">
      <h3>{K6_MTF_UNRANKED_SECTION_TITLE}</h3>
      {unranked.length === 0 ? (
        <div id="k6mtf-unranked-empty" className="unranked-empty">
          {K6_MTF_UNRANKED_EMPTY_MESSAGE}
        </div>
      ) : (
        <div id="k6mtf-unranked-list" className="unranked-list">
          {unranked.map((row) => (
            <UnrankedRecord key={row.secondary} row={row} />
          ))}
        </div>
      )}
    </section>
  );
}

function UnrankedRecord({ row }: { row: PerSecondary }) {
  const issues = Array.isArray(row.issues) ? row.issues : [];
  return (
    <div className="k6mtf-unranked-record">
      <div>
        <strong>{"Ticker: "}</strong>
        <span>{row.secondary || UNAVAILABLE}</span>
      </div>
      <div>
        <strong>{"Status: "}</strong>
        <span>{row.status || UNAVAILABLE}</span>
      </div>
      <div>
        <strong>{"sharpe_k6_mtf: "}</strong>
        <span>{formatSharpe(row.sharpe_k6_mtf)}</span>
      </div>
      <div>
        <strong>{"Issues:"}</strong>
        {issues.length > 0 ? (
          <ul className="modal-list">
            {issues.map((entry, idx) => {
              const code = entry && entry.code ? entry.code : "issue";
              const message = entry && entry.message ? entry.message : "";
              return <li key={`${code}-${idx}`}>{`${code}: ${message}`}</li>;
            })}
          </ul>
        ) : (
          <div>{"No issues recorded."}</div>
        )}
      </div>
    </div>
  );
}
