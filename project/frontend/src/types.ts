// Type mirror of the k6_mtf_ranking_v1 schema as locked in
// project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md
// "Ranking Artifact" section. The React app reads this shape only.
// If a future contract amendment adds a field, extend this file in
// lockstep; React must never display data not present in the
// artifact (React Migration Declaration "Forbidden Behaviors").

export const K6_MTF_SCHEMA_VERSION = "k6_mtf_ranking_v1" as const;
// PR-1 (sprint500): k6_mtf_ranking_v2 = the v1 ranking artifact joined
// with the Phase 5 validation sidecar (empirical p/q/Bonferroni/bootstrap
// per row + validation metadata + Stage-A drop-list). The display UI for
// v2 is PR-2; the v2 additions below are all OPTIONAL so v1 fixtures and
// the existing v1-only components keep compiling unchanged.
export const K6_MTF_SCHEMA_VERSION_V2 = "k6_mtf_ranking_v2" as const;
export type K6MtfSchemaVersion =
  | typeof K6_MTF_SCHEMA_VERSION
  | typeof K6_MTF_SCHEMA_VERSION_V2;

export type PerSecondaryStatus = "ranked" | "unranked" | "failed";
export type Protocol = "D" | "I";

// Operator-locked two-outcome validation model (no near_threshold).
export type ValidationOutcome = "board_validated" | "not_validated";

export interface K6StackMember {
  ticker: string;
  protocol: Protocol;
}

export interface K6Stack {
  selected_build_path: string;
  selected_run_dir: string;
  combo_k6_path: string;
  members: K6StackMember[];
}

export interface CurrentSnapshot {
  "1d": string;
  "1wk": string;
  "1mo": string;
  "3mo": string;
  "1y": string;
}

export interface CccPoint {
  date_utc: string;
  cumulative_capture_pct: number;
  per_bar_capture_pct: number;
  trade_direction: string;
}

export interface PerSecondaryIssue {
  code?: string;
  message?: string;
}

export interface PerSecondary {
  secondary: string;
  rank: number | null;
  status: PerSecondaryStatus | string;
  history_artifact_path: string;
  history_as_of_date: string;
  current_snapshot: CurrentSnapshot;
  k6_stack: K6Stack;
  sharpe_k6_mtf: number | null;
  total_capture_pct: number | null;
  avg_capture_pct: number | null;
  stddev_pct: number | null;
  match_count: number;
  capture_count: number;
  trade_count: number;
  no_trade_count: number;
  skipped_capture_count: number;
  win_count: number;
  loss_count: number;
  win_pct: number | null;
  low_sample_warning: boolean;
  ccc_series: CccPoint[];
  issues: PerSecondaryIssue[];
  // --- v2 validation join (optional; present only on k6_mtf_ranking_v2) ---
  validation_outcome?: ValidationOutcome;
  empirical_validation_status?: string;
  empirical_p_value?: number | null;
  parametric_p_value?: number | null;
  bh_q_value?: number | null;
  bonferroni_p_value?: number | null;
  bootstrap_sharpe_ci_lower?: number | null;
  bootstrap_sharpe_ci_upper?: number | null;
  empirical_not_run_reason?: string | null;
  validation_trigger_days?: number | null;
  validation_strategy_id?: string;
  validation_run_id?: string | null;
  validation_artifact_sha256?: string | null;
}

// --- v2 top-level validation blocks (optional on the artifact) ---
export interface ValidationMetadata {
  run_id: string | null;
  artifact_sha256: string;
  validation_status: string;
  validated_as_of_utc: string | null;
  data_available_through?: string | null;
  n_strategies_tested: number;
  n_strategies_reported: number;
  n_permutations: number;
  n_bootstrap_samples: number;
  walk_forward_n_folds: number;
  bootstrap_ci_level?: number | null;
  multiple_comparisons_control_alpha: number;
  multiple_comparisons_control_method: string | null;
  multiple_comparisons_supplementary?: string | null;
  validation_contract_version: string | null;
  validation_methodology_version: string | null;
  rng_seed: number | null;
  source_sidecar_path?: string | null;
  source_ranking_path?: string | null;
}

export interface ValidationSummary {
  board_validated_count: number;
  not_validated_count: number;
  empirical_status_counts: Record<string, number>;
  stage_a_excluded_count: number;
  displayed_ranked_count: number;
  validation_non_reported_count: number;
}

export interface StageAExclusionCause {
  ticker?: string | null;
  ticker_classification?: string | null;
  dependent_role?: string | null;
  member_token?: string;
  member_protocol?: string;
}

export interface StageAExclusion {
  secondary: string;
  reason: string;
  causes: StageAExclusionCause[];
  evidence_source: string;
}

export interface TopLevelIssue {
  code?: string;
  message?: string;
}

export interface K6MtfRankingArtifact {
  schema_version: typeof K6_MTF_SCHEMA_VERSION;
  generated_at_utc: string;
  run_id: string;
  secondaries_requested: string[];
  secondaries_ranked: string[];
  per_secondary: PerSecondary[];
  issues: TopLevelIssue[];
  // v2-only top-level blocks (optional; absent on v1 fixtures).
  validation_metadata?: ValidationMetadata;
  validation_summary?: ValidationSummary;
  stage_a_excluded_secondaries?: StageAExclusion[];
}

// Explicit v2 artifact shape for PR-2 consumers. Structurally a v1
// artifact with the validation blocks present (still optional here so a
// narrowing read can fall back gracefully).
export interface K6MtfRankingArtifactV2
  extends Omit<K6MtfRankingArtifact, "schema_version"> {
  schema_version: typeof K6_MTF_SCHEMA_VERSION_V2;
}
