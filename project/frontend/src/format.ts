// Pure formatting helpers. Mirror mvp_signal_board.py's
// format_number / format_integer / _format_k6_mtf_sharpe. The
// boolean guard matters because JavaScript (like Python) treats
// booleans as numeric in arithmetic contexts; the Dash side
// explicitly rejects booleans, and the React side must do the
// same to avoid surfacing "0.00" for a stray boolean Sharpe.

export const UNAVAILABLE = "Unavailable";
// Field-specific "not applicable" token used by the validation display
// layer (e.g. empirical p-value / bootstrap CI on empirical_not_run
// rows). Distinct from UNAVAILABLE so disclosure copy reads correctly.
export const NA = "N/A";
export const K6_MTF_SHARPE_UNDEFINED = "undefined (insufficient sample)";

function isFiniteNumber(value: unknown): value is number {
  return (
    typeof value === "number"
    && !Number.isNaN(value)
    && Number.isFinite(value)
  );
}

function isStrictNumeric(value: unknown): value is number {
  // Boolean is not numeric for our purposes.
  if (typeof value === "boolean") {
    return false;
  }
  return isFiniteNumber(value);
}

export function formatSharpe(value: unknown): string {
  if (!isStrictNumeric(value)) {
    return K6_MTF_SHARPE_UNDEFINED;
  }
  return value.toFixed(2);
}

export function formatNumber(value: unknown, decimals: number = 2): string {
  if (!isStrictNumeric(value)) {
    return UNAVAILABLE;
  }
  return value.toFixed(decimals);
}

export function formatInteger(value: unknown): string {
  if (!isStrictNumeric(value)) {
    return UNAVAILABLE;
  }
  return String(Math.trunc(value));
}

// --- PR-2 validation-display formatters ---------------------------------
// All return a fixed fallback for non-finite/missing values so the UI
// never surfaces NaN/Infinity. ``fallback`` lets callers choose between
// UNAVAILABLE (generic missing) and NA (field-specific not-applicable).

export function formatPValue(value: unknown, fallback: string = UNAVAILABLE): string {
  if (!isStrictNumeric(value)) {
    return fallback;
  }
  return value.toFixed(4);
}

export function formatQValue(value: unknown, fallback: string = UNAVAILABLE): string {
  if (!isStrictNumeric(value)) {
    return fallback;
  }
  return value.toFixed(4);
}

export function formatBootstrapCi(
  lower: unknown,
  upper: unknown,
  fallback: string = UNAVAILABLE,
): string {
  if (!isStrictNumeric(lower) || !isStrictNumeric(upper)) {
    return fallback;
  }
  return `[${lower.toFixed(3)}, ${upper.toFixed(3)}]`;
}

export function formatShortSha(value: unknown, length: number = 12): string {
  if (typeof value !== "string" || value.length === 0) {
    return UNAVAILABLE;
  }
  return value.length > length ? value.slice(0, length) : value;
}

export function formatText(value: unknown, fallback: string = UNAVAILABLE): string {
  if (typeof value !== "string" || value.length === 0) {
    return fallback;
  }
  return value;
}
