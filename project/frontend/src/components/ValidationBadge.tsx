import type { PerSecondary } from "../types";
import { badgeForRow } from "../validationDisplay";

// Small validation badge derived purely from the operator-locked
// two-outcome model. Renders nothing for v1 / no-outcome rows (the
// no-badge sentinel) so v1 fixtures show an empty validation cell.
// Visually and semantically separate from rank, row.status, and the
// performance/Sharpe score.
export function ValidationBadge({ row }: { row: PerSecondary }) {
  const badge = badgeForRow(row);
  if (badge.label === null || badge.variant === null) {
    return null;
  }
  return (
    <span
      className={`validation-badge ${badge.variant}`}
      data-validation-state={badge.state}
    >
      {badge.label}
    </span>
  );
}
