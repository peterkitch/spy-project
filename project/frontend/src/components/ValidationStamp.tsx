import type { K6MtfRankingArtifactAny, ValidationMetadata, ValidationSummary } from "../types";
import { UNAVAILABLE, formatInteger, formatNumber, formatShortSha, formatText } from "../format";

// Phase 5 validation stamp / methodology block. Renders ONLY for v2
// artifacts (those carrying validation_metadata). Counts come from
// validation_summary / validation_metadata verbatim -- never recomputed
// from per-row badges. For v1 artifacts the component renders nothing.
export function ValidationStamp({ artifact }: { artifact: K6MtfRankingArtifactAny }) {
  const meta: ValidationMetadata | undefined = artifact.validation_metadata;
  const summary: ValidationSummary | undefined = artifact.validation_summary;
  if (!meta) {
    // v1 / no validation metadata: render nothing (no validation claim).
    return null;
  }
  const rngSeed =
    meta.rng_seed === null || meta.rng_seed === undefined
      ? "not recorded"
      : formatInteger(meta.rng_seed);
  return (
    <section id="k6mtf-validation-stamp" className="validation-stamp">
      <h3 className="validation-stamp-title">{"Phase 5 validation stamp"}</h3>
      <ul className="validation-stamp-list">
        <li>{`Validation run id: ${formatText(meta.run_id)}`}</li>
        <li>{`Validation sidecar (short SHA): ${formatShortSha(meta.artifact_sha256)}`}</li>
        <li>{`Validated as of: ${formatText(meta.validated_as_of_utc)}`}</li>
        <li>{`Data available through: ${formatText(meta.data_available_through)}`}</li>
        <li>{`Strategies tested: ${formatInteger(meta.n_strategies_tested)}`}</li>
        <li>{`Strategies reported (board-validated): ${formatInteger(meta.n_strategies_reported)}`}</li>
        {summary && (
          <li>{`Board validated / not validated: ${formatInteger(summary.board_validated_count)} / ${formatInteger(summary.not_validated_count)}`}</li>
        )}
        <li>{`Permutations: ${formatInteger(meta.n_permutations)}`}</li>
        <li>{`Bootstrap samples: ${formatInteger(meta.n_bootstrap_samples)}`}</li>
        <li>{`Walk-forward folds: ${formatInteger(meta.walk_forward_n_folds)}`}</li>
        <li>{`Multiple-comparisons method: ${formatText(meta.multiple_comparisons_control_method)}`}</li>
        <li>{`Alpha: ${formatNumber(meta.multiple_comparisons_control_alpha, 3)}`}</li>
        <li>{`RNG seed: ${rngSeed}`}</li>
      </ul>
      <div className="validation-stamp-note">
        {
          "Counts above are taken verbatim from the validation sidecar; "
          + "they are not recomputed from displayed rows."
        }
      </div>
    </section>
  );
}

// Exported for clarity; UNAVAILABLE is the shared missing-value token.
export const VALIDATION_STAMP_FALLBACK = UNAVAILABLE;
