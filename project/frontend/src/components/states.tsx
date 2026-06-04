import { EMPTY_TABLE_MESSAGE } from "../constants";

// Loading / error / empty states. Mirrors the safe error layouts
// that mvp_signal_board.py emits from build_mvp_signal_board_app
// for missing / unreadable / wrong_schema outcomes.

export function LoadingState() {
  return (
    <div className="state-block state-loading">
      {"Loading ranking artifact..."}
    </div>
  );
}

export function MissingArtifactState() {
  return (
    <div className="state-block state-error">
      {"Ranking artifact not found."}
    </div>
  );
}

export function UnreadableArtifactState({ detail }: { detail: string }) {
  return (
    <div className="state-block state-error">
      <div>{"Ranking artifact unreadable."}</div>
      {detail && <div className="state-error-detail">{detail}</div>}
    </div>
  );
}

export function WrongSchemaState({ actual }: { actual: string | null }) {
  const expected = "Expected k6_mtf_ranking_v1 or k6_mtf_ranking_v2.";
  const tail = actual ? `${expected} Got: ${actual}.` : expected;
  return (
    <div className="state-block state-error">
      <div>{"Unrecognized artifact schema."}</div>
      <div className="state-error-detail">{tail}</div>
    </div>
  );
}

export function EmptyTableState() {
  return (
    <div id="mvp-empty-state" className="state-block state-empty">
      {EMPTY_TABLE_MESSAGE}
    </div>
  );
}
