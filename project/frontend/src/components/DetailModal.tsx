import { useEffect, useRef } from "react";
import type { PerSecondary, PerSecondaryIssue } from "../types";
import {
  DISCLAIMER,
  K6_MTF_SURFACE_DISTINGUISHER,
  V1_TIMEFRAMES,
} from "../constants";
import { UNAVAILABLE, formatInteger, formatNumber, formatSharpe } from "../format";
import { CccStepChart } from "./CccStepChart";

interface DetailModalProps {
  row: PerSecondary;
  runId: string;
  generatedAtUtc: string;
  onClose: () => void;
}

// Centered modal with dimmed backdrop. Esc closes; backdrop click
// closes; explicit close button closes. Renders the same blocks
// as mvp_signal_board.py render_k6_mtf_modal_content.
export function DetailModal({
  row,
  runId,
  generatedAtUtc,
  onClose,
}: DetailModalProps) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", onKey);
    // Move keyboard focus into the modal so Esc / Enter work
    // immediately without requiring a mouse click first.
    closeButtonRef.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        id="k6mtf-modal-body"
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="k6mtf-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          ref={closeButtonRef}
          type="button"
          className="modal-close"
          aria-label="Close detail"
          onClick={onClose}
        >
          {"Close"}
        </button>
        <h3 id="k6mtf-modal-title" className="modal-title">
          {row.secondary || UNAVAILABLE}
        </h3>
        <div id="k6mtf-modal-distinguisher" className="modal-distinguisher">
          {K6_MTF_SURFACE_DISTINGUISHER}
        </div>
        <section id="k6mtf-modal-status" className="modal-row">
          <strong>{"Status: "}</strong>
          <span>{row.status || UNAVAILABLE}</span>
        </section>
        <section id="k6mtf-modal-as-of" className="modal-row">
          <strong>{"history_as_of_date: "}</strong>
          <span>{row.history_as_of_date || UNAVAILABLE}</span>
        </section>
        {renderSnapshot(row)}
        {renderStack(row)}
        {renderCcc(row)}
        {renderMetrics(row)}
        {renderCounts(row)}
        {renderIssues(row)}
        {renderProvenance(row, runId, generatedAtUtc)}
        <div id="k6mtf-modal-disclaimer" className="modal-disclaimer">
          {DISCLAIMER}
        </div>
      </div>
    </div>
  );
}

function renderSnapshot(row: PerSecondary): JSX.Element {
  const snapshot = row.current_snapshot;
  return (
    <section id="k6mtf-modal-snapshot" className="modal-section">
      <strong>{"Current snapshot (K=6 MTF)"}</strong>
      {snapshot && typeof snapshot === "object" ? (
        <ul id="k6mtf-modal-snapshot-list" className="modal-list">
          {V1_TIMEFRAMES.map((tf) => {
            const v = snapshot[tf];
            const text = typeof v === "string" && v.length > 0 ? v : UNAVAILABLE;
            return <li key={tf}>{`${tf} = ${text}`}</li>;
          })}
        </ul>
      ) : (
        <div id="k6mtf-modal-snapshot-empty">{UNAVAILABLE}</div>
      )}
    </section>
  );
}

function renderStack(row: PerSecondary): JSX.Element {
  const stack = row.k6_stack;
  const members = stack && Array.isArray(stack.members) ? stack.members : null;
  return (
    <section id="k6mtf-modal-stack" className="modal-section">
      <strong>{"K=6 stack members"}</strong>
      {members && members.length > 0 ? (
        <ul id="k6mtf-modal-stack-list" className="modal-list">
          {members.map((m, idx) => {
            const ticker = m && m.ticker ? m.ticker : UNAVAILABLE;
            const protocol = m && m.protocol ? m.protocol : "?";
            return <li key={`${ticker}-${idx}`}>{`${ticker} [${protocol}]`}</li>;
          })}
        </ul>
      ) : (
        <div id="k6mtf-modal-stack-empty">{UNAVAILABLE}</div>
      )}
    </section>
  );
}

function renderCcc(row: PerSecondary): JSX.Element {
  const series = Array.isArray(row.ccc_series) ? row.ccc_series : [];
  const first = series[0];
  const last = series[series.length - 1];
  const summary = series.length > 0 && first && last
    ? `CCC summary: first ${first.date_utc} = ${formatNumber(first.cumulative_capture_pct)}, last ${last.date_utc} = ${formatNumber(last.cumulative_capture_pct)}, len = ${series.length}`
    : null;
  return (
    <section id="k6mtf-modal-ccc" className="modal-section">
      <strong>{"Cumulative Capture Chart (K=6 MTF)"}</strong>
      <CccStepChart series={series} secondary={row.secondary || ""} />
      {summary && (
        <div id="k6mtf-modal-ccc-summary" className="modal-ccc-summary">
          {summary}
        </div>
      )}
    </section>
  );
}

function renderMetrics(row: PerSecondary): JSX.Element {
  const lowSample = Boolean(row.low_sample_warning);
  return (
    <section id="k6mtf-modal-metrics" className="modal-section">
      <strong>{"K=6 MTF metrics"}</strong>
      <ul className="modal-list">
        <li id="k6mtf-modal-sharpe">
          {`sharpe_k6_mtf: ${formatSharpe(row.sharpe_k6_mtf)}`}
        </li>
        <li>{`total_capture_pct: ${formatNumber(row.total_capture_pct)}`}</li>
        <li>{`avg_capture_pct: ${formatNumber(row.avg_capture_pct)}`}</li>
        <li>{`stddev_pct: ${formatNumber(row.stddev_pct)}`}</li>
        <li>{`win_pct: ${formatNumber(row.win_pct)}`}</li>
        <li id="k6mtf-modal-low-sample-warning">
          {`low_sample_warning: ${String(lowSample)}`}
        </li>
      </ul>
      {lowSample && (
        <div id="k6mtf-modal-low-sample-indicator" className="modal-low-sample">
          {"!"}
        </div>
      )}
    </section>
  );
}

function renderCounts(row: PerSecondary): JSX.Element {
  return (
    <section id="k6mtf-modal-counts" className="modal-section">
      <strong>{"Counts"}</strong>
      <ul className="modal-list">
        <li>{`match_count: ${formatInteger(row.match_count)}`}</li>
        <li>{`capture_count: ${formatInteger(row.capture_count)}`}</li>
        <li>{`trade_count: ${formatInteger(row.trade_count)}`}</li>
        <li>{`no_trade_count: ${formatInteger(row.no_trade_count)}`}</li>
        <li>{`skipped_capture_count: ${formatInteger(row.skipped_capture_count)}`}</li>
        <li>{`win_count: ${formatInteger(row.win_count)}`}</li>
        <li>{`loss_count: ${formatInteger(row.loss_count)}`}</li>
      </ul>
    </section>
  );
}

function renderIssues(row: PerSecondary): JSX.Element {
  const issues = Array.isArray(row.issues) ? row.issues : [];
  return (
    <section id="k6mtf-modal-issues" className="modal-section">
      <strong>{"Issues"}</strong>
      {issues.length > 0 ? (
        <ul id="k6mtf-modal-issues-list" className="modal-list">
          {issues.map((entry: PerSecondaryIssue, idx: number) => {
            const code = entry && entry.code ? entry.code : "issue";
            const message = entry && entry.message ? entry.message : "";
            return <li key={`${code}-${idx}`}>{`${code}: ${message}`}</li>;
          })}
        </ul>
      ) : (
        <div id="k6mtf-modal-issues-empty">
          {"No per-secondary issues recorded."}
        </div>
      )}
    </section>
  );
}

function renderProvenance(
  row: PerSecondary,
  runId: string,
  generatedAtUtc: string,
): JSX.Element {
  const historyPath = row.history_artifact_path || UNAVAILABLE;
  const safeRunId = runId || UNAVAILABLE;
  const safeGenerated = generatedAtUtc || UNAVAILABLE;
  return (
    <section id="k6mtf-modal-provenance" className="modal-section">
      <strong>{"Provenance"}</strong>
      <ul className="modal-list">
        <li>{`K=6 MTF run id: ${safeRunId}`}</li>
        <li>{`history_artifact_path: ${historyPath}`}</li>
        <li>{`Ranking generated at: ${safeGenerated}`}</li>
      </ul>
    </section>
  );
}
