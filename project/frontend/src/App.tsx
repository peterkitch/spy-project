import { useEffect, useState } from "react";
import {
  BOARD_HEADER,
  K6_MTF_BOARD_SUBHEADER,
  K6_MTF_SURFACE_DISTINGUISHER,
  K6_MTF_VALIDATION_DISCLOSURE,
} from "./constants";
import { loadRankingArtifact, type LoadOutcome } from "./loadArtifact";
import { RankedTable } from "./components/RankedTable";
import { DetailModal } from "./components/DetailModal";
import { UnrankedSection } from "./components/UnrankedSection";
import {
  LoadingState,
  MissingArtifactState,
  UnreadableArtifactState,
  WrongSchemaState,
  EmptyTableState,
} from "./components/states";
import { visibleRows } from "./columnsFor";

export function App() {
  const [outcome, setOutcome] = useState<LoadOutcome | null>(null);
  const [activeTicker, setActiveTicker] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    loadRankingArtifact().then((result) => {
      if (!cancelled) {
        setOutcome(result);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div id="mvp-root" className="mvp-root">
      <header className="mvp-header-block">
        <h1 id="mvp-header" className="mvp-header">{BOARD_HEADER}</h1>
        <h2 id="mvp-subheader" className="mvp-subheader">
          {K6_MTF_BOARD_SUBHEADER}
        </h2>
        <div
          id="k6mtf-surface-distinguisher"
          className="mvp-surface-distinguisher"
        >
          {K6_MTF_SURFACE_DISTINGUISHER}
        </div>
        <div
          id="mvp-validation-disclosure"
          className="state-block"
        >
          {K6_MTF_VALIDATION_DISCLOSURE}
        </div>
      </header>
      <main>{renderBody(outcome, activeTicker, setActiveTicker)}</main>
    </div>
  );
}

function renderBody(
  outcome: LoadOutcome | null,
  activeTicker: string | null,
  setActiveTicker: (t: string | null) => void,
): JSX.Element {
  if (outcome === null) {
    return <LoadingState />;
  }
  if (outcome.kind === "missing") {
    return <MissingArtifactState />;
  }
  if (outcome.kind === "unreadable") {
    return <UnreadableArtifactState detail={outcome.detail} />;
  }
  if (outcome.kind === "wrong_schema") {
    return <WrongSchemaState actual={outcome.actual} />;
  }
  const payload = outcome.payload;
  const visible = visibleRows(payload.per_secondary);
  const activeRow = activeTicker
    ? payload.per_secondary.find((r) => r.secondary === activeTicker) ?? null
    : null;
  return (
    <>
      <section id="mvp-board" className="mvp-board-section">
        {visible.length === 0 ? (
          <EmptyTableState />
        ) : (
          <RankedTable
            visibleRows={visible}
            activeTicker={activeTicker}
            onSelect={setActiveTicker}
          />
        )}
      </section>
      <UnrankedSection rows={payload.per_secondary} />
      {activeRow && (
        <DetailModal
          row={activeRow}
          runId={payload.run_id}
          generatedAtUtc={payload.generated_at_utc}
          onClose={() => setActiveTicker(null)}
        />
      )}
    </>
  );
}
