import { K6_MTF_SCHEMA_VERSION, type K6MtfRankingArtifact } from "./types";

// Discriminated union mirroring the Dash loader outcomes
// (mvp_signal_board.py load_ranking_artifact returns
// {status: "ok"|"missing"|"unreadable"|"wrong_schema"}). The
// React loader applies the same classification at the artifact
// boundary; it does NOT validate metric values, recompute, or
// derive anything.

export type LoadOutcome =
  | { kind: "ok"; payload: K6MtfRankingArtifact }
  | { kind: "missing" }
  | { kind: "unreadable"; detail: string }
  | { kind: "wrong_schema"; actual: string | null };

const FIXTURE_URL = "fixtures/k6_mtf_ranking.json";

export async function loadRankingArtifact(): Promise<LoadOutcome> {
  // Resolve the fixture URL against the configured Vite base so a
  // future publish-step swap (e.g., a CDN-served artifact URL)
  // does not require component changes.
  const url = new URL(FIXTURE_URL, document.baseURI).toString();
  let response: Response;
  try {
    response = await fetch(url, { cache: "no-store" });
  } catch (err) {
    return { kind: "unreadable", detail: truncate(String(err)) };
  }
  if (response.status === 404) {
    return { kind: "missing" };
  }
  if (!response.ok) {
    return {
      kind: "unreadable",
      detail: `HTTP ${response.status} ${response.statusText}`.trim(),
    };
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (err) {
    return { kind: "unreadable", detail: truncate(String(err)) };
  }
  if (typeof payload !== "object" || payload === null) {
    return {
      kind: "unreadable",
      detail: "artifact root is not a JSON object",
    };
  }
  const obj = payload as { schema_version?: unknown };
  const actual = typeof obj.schema_version === "string" ? obj.schema_version : null;
  if (actual !== K6_MTF_SCHEMA_VERSION) {
    return { kind: "wrong_schema", actual };
  }
  return { kind: "ok", payload: payload as K6MtfRankingArtifact };
}

function truncate(s: string): string {
  return s.length > 240 ? s.slice(0, 240) : s;
}
