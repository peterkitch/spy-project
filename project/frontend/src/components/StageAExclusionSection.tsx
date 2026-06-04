import type {
  K6MtfRankingArtifactAny,
  StageAExclusion,
  StageAExclusionCause,
} from "../types";
import { formatText } from "../format";

// Stage-A coverage / drop-list section. DROP-AND-LIST ONLY: it lists the
// secondaries excluded upstream at Stage A with their causes. It adds no
// recovery / refresh / remap / rotation controls and never rewrites
// frozen K=6 stacks. Renders ONLY for v2 artifacts that carry a
// non-empty stage_a_excluded_secondaries[]; renders nothing otherwise.
export function StageAExclusionSection({
  artifact,
}: {
  artifact: K6MtfRankingArtifactAny;
}) {
  const excluded: StageAExclusion[] | undefined =
    artifact.stage_a_excluded_secondaries;
  if (!Array.isArray(excluded) || excluded.length === 0) {
    return null;
  }
  return (
    <section id="k6mtf-stage-a-exclusions" className="stage-a-section">
      <h3 className="stage-a-title">
        {`Stage-A excluded secondaries (${excluded.length})`}
      </h3>
      <div className="stage-a-intro">
        {
          "These secondaries were dropped upstream at Stage A and are NOT "
          + "part of the ranked board. They are listed here for coverage "
          + "transparency only."
        }
      </div>
      <ul className="stage-a-list">
        {excluded.map((entry, idx) => (
          <li key={`${entry.secondary}-${idx}`} className="stage-a-row">
            <div className="stage-a-secondary">
              <strong>{entry.secondary}</strong>
              {` -- ${formatText(entry.reason)}`}
            </div>
            <div className="stage-a-evidence">
              {`evidence_source: ${formatText(entry.evidence_source)}`}
            </div>
            {Array.isArray(entry.causes) && entry.causes.length > 0 && (
              <ul className="stage-a-causes">
                {entry.causes.map((c: StageAExclusionCause, ci: number) => (
                  <li key={`${entry.secondary}-cause-${ci}`}>
                    {renderCause(c)}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function renderCause(c: StageAExclusionCause): string {
  const ticker = formatText(c.ticker);
  const classification = formatText(c.ticker_classification);
  const role = formatText(c.dependent_role);
  let line = `${ticker} (${classification}; role: ${role})`;
  if (c.member_token) {
    line += ` token: ${c.member_token}`;
  }
  if (c.member_protocol) {
    line += ` protocol: ${c.member_protocol}`;
  }
  return line;
}
