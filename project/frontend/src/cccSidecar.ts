import {
  CCC_SERIES_SOURCE_BLOB,
  CCC_SIDECAR_SCHEMA_VERSION,
  type CccPoint,
  type CccSidecar,
  type PerSecondary,
} from "./types";

// Lazy-loader for off-repo, full-resolution CCC series stored as immutable
// per-secondary Vercel Blob sidecars. The committed fixture stays slim: a
// Blob-sourced row carries ccc_series === [] plus sidecar metadata, and the
// detail-modal chart fetches the full series from ccc_series_url only when
// the modal opens.
//
// Artifact-boundary discipline (React Migration Declaration): this still
// fetches read-only JSON and never recomputes, sign-flips, or derives any
// metric. The sidecar carries only derived CCC capture fields.
//
// Integrity model: the hard integrity check is server-side -- the promotion
// helper GET-verifies each uploaded sidecar's SHA-256 before stamping its
// URL into the fixture, and the row carries ccc_series_sha256. Browser-side
// re-hashing via SubtleCrypto is intentionally NOT performed by default: it
// requires a secure context, adds async crypto complexity, and would only
// re-check an immutable object already verified at promotion time. The
// stored SHA remains available for a future opt-in browser check.

// Only public Vercel Blob hosts are fetchable.
const VERCEL_BLOB_HOST_RE =
  /^[A-Za-z0-9][A-Za-z0-9.-]*\.public\.blob\.vercel-storage\.com$/;

// Derived CCC point fields (Mode B): a point must carry exactly these.
const CCC_POINT_REQUIRED_FIELDS = [
  "cumulative_capture_pct",
  "date_utc",
  "per_bar_capture_pct",
  "trade_direction",
] as const;

// Defense in depth: a CCC point must never carry raw-price keys (Mode B).
const OHLCV_FORBIDDEN_KEYS = new Set([
  "open",
  "high",
  "low",
  "close",
  "adj_close",
  "adjclose",
  "adjusted_close",
  "volume",
]);

// Parse a public Vercel Blob URL and return its object path (pathname minus
// the leading slash), or null if not an allowlisted HTTPS Blob URL. Uses
// real URL parsing, not string slicing.
function blobUrlPath(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:") return null;
  if (!VERCEL_BLOB_HOST_RE.test(parsed.hostname)) return null;
  if (parsed.search || parsed.hash) return null;
  const path = parsed.pathname.replace(/^\/+/, "");
  return path.length > 0 ? path : null;
}

export type CccLoadResult =
  | { kind: "ok"; series: CccPoint[] }
  | { kind: "empty" }
  | { kind: "error"; message: string };

// Session cache keyed by the immutable sidecar URL. Immutable content =>
// safe to cache for the app session.
const sessionCache = new Map<string, CccLoadResult>();

export function rowUsesBlobSidecar(row: PerSecondary): boolean {
  return row.ccc_series_source === CCC_SERIES_SOURCE_BLOB;
}

function validateSidecar(
  raw: unknown,
  row: PerSecondary,
): CccLoadResult {
  if (typeof raw !== "object" || raw === null) {
    return { kind: "error", message: "sidecar is not a JSON object" };
  }
  const obj = raw as Partial<CccSidecar>;
  if (obj.schema_version !== CCC_SIDECAR_SCHEMA_VERSION) {
    return {
      kind: "error",
      message: `unexpected sidecar schema_version: ${String(obj.schema_version)}`,
    };
  }
  if (obj.secondary !== row.secondary) {
    return {
      kind: "error",
      message: `sidecar secondary mismatch: ${String(obj.secondary)} != ${row.secondary}`,
    };
  }
  const series = obj.ccc_series;
  if (!Array.isArray(series)) {
    return { kind: "error", message: "sidecar ccc_series is not an array" };
  }
  // Optional point-count cross-check against the fixture row metadata.
  if (
    typeof row.ccc_series_points === "number" &&
    series.length !== row.ccc_series_points
  ) {
    return {
      kind: "error",
      message: `sidecar point count ${series.length} != expected ${row.ccc_series_points}`,
    };
  }
  // Per-point validation: object, exactly the derived fields, no raw-price
  // key, no unexpected key (Mode B + shape). Fail safely on any violation.
  for (const point of series) {
    if (typeof point !== "object" || point === null) {
      return { kind: "error", message: "sidecar point is not an object" };
    }
    const keys = Object.keys(point as unknown as Record<string, unknown>);
    for (const key of keys) {
      if (OHLCV_FORBIDDEN_KEYS.has(key.toLowerCase())) {
        return {
          kind: "error",
          message: `sidecar point carries a forbidden raw-price key: ${key}`,
        };
      }
    }
    for (const req of CCC_POINT_REQUIRED_FIELDS) {
      if (!(req in (point as object))) {
        return {
          kind: "error",
          message: `sidecar point missing required field: ${req}`,
        };
      }
    }
    for (const key of keys) {
      if (!(CCC_POINT_REQUIRED_FIELDS as readonly string[]).includes(key)) {
        return {
          kind: "error",
          message: `sidecar point carries an unexpected field: ${key}`,
        };
      }
    }
  }
  if (series.length === 0) {
    return { kind: "empty" };
  }
  return { kind: "ok", series: series as CccPoint[] };
}

export async function loadCccSeries(row: PerSecondary): Promise<CccLoadResult> {
  // Inline fallback: legacy / test fixtures carry the series inline.
  if (!rowUsesBlobSidecar(row)) {
    const inline = Array.isArray(row.ccc_series) ? row.ccc_series : [];
    return inline.length > 0 ? { kind: "ok", series: inline } : { kind: "empty" };
  }
  const url = row.ccc_series_url;
  const pathname = row.ccc_series_pathname;
  const sha = row.ccc_series_sha256;
  if (typeof url !== "string") {
    return {
      kind: "error",
      message: "Blob-sourced row has no ccc_series_url",
    };
  }
  // Bind url <-> pathname <-> sha BEFORE fetching: the URL's object path
  // must equal the row pathname, and the pathname must embed the sha.
  const urlPath = blobUrlPath(url);
  if (
    urlPath === null ||
    typeof pathname !== "string" ||
    urlPath !== pathname ||
    typeof sha !== "string" ||
    !pathname.includes(sha)
  ) {
    return {
      kind: "error",
      message: "Blob row ccc_series_url/pathname/sha256 do not bind",
    };
  }
  const cached = sessionCache.get(url);
  if (cached) {
    return cached;
  }
  let result: CccLoadResult;
  try {
    const response = await fetch(url, { cache: "force-cache" });
    if (!response.ok) {
      result = {
        kind: "error",
        message: `HTTP ${response.status} ${response.statusText}`.trim(),
      };
    } else {
      const raw: unknown = await response.json();
      result = validateSidecar(raw, row);
    }
  } catch (err) {
    result = { kind: "error", message: truncate(String(err)) };
  }
  // Cache only successful / empty outcomes; let errors retry next open.
  if (result.kind !== "error") {
    sessionCache.set(url, result);
  }
  return result;
}

// Test/utility hook: clear the in-memory session cache.
export function _clearCccSessionCache(): void {
  sessionCache.clear();
}

function truncate(s: string): string {
  return s.length > 240 ? s.slice(0, 240) : s;
}
