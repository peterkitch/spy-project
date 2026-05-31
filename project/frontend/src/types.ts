// Type mirror of the k6_mtf_ranking_v1 schema as locked in
// project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md
// "Ranking Artifact" section. The React app reads this shape only.
// If a future contract amendment adds a field, extend this file in
// lockstep; React must never display data not present in the
// artifact (React Migration Declaration "Forbidden Behaviors").

export const K6_MTF_SCHEMA_VERSION = "k6_mtf_ranking_v1" as const;

export type PerSecondaryStatus = "ranked" | "unranked" | "failed";
export type Protocol = "D" | "I";

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
}
