// Pure formatting helpers. Mirror mvp_signal_board.py's
// format_number / format_integer / _format_k6_mtf_sharpe. The
// boolean guard matters because JavaScript (like Python) treats
// booleans as numeric in arithmetic contexts; the Dash side
// explicitly rejects booleans, and the React side must do the
// same to avoid surfacing "0.00" for a stray boolean Sharpe.

export const UNAVAILABLE = "Unavailable";
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
