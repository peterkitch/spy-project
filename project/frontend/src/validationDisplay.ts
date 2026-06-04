// Pure display helper mapping the operator-locked two-outcome
// validation model onto exactly three v2 badge states (+ a no-badge
// sentinel for v1 / absent validation). No near_threshold, no third
// validation_outcome, no fourth badge. This module recomputes NOTHING
// statistical: it only maps already-derived per-row fields to labels and
// centralizes field-specific N/A behavior for the empirical fields.

import type { PerSecondary, ValidationBadgeState } from "./types";
import {
  NA,
  UNAVAILABLE,
  formatBootstrapCi,
  formatPValue,
  formatQValue,
} from "./format";

export interface ValidationBadge {
  state: ValidationBadgeState;
  // label is null for the no-badge sentinel (v1 / absent outcome).
  label: string | null;
  // CSS modifier suffix (e.g. "validation-badge--pass"). null when no badge.
  variant: string | null;
}

const BADGE_PASS: ValidationBadge = {
  state: "pass",
  label: "Validation: PASS",
  variant: "validation-badge--pass",
};
const BADGE_FAIL: ValidationBadge = {
  state: "fail",
  label: "Validation: FAIL",
  variant: "validation-badge--fail",
};
const BADGE_NOT_ENOUGH: ValidationBadge = {
  state: "not_enough_triggers",
  label: "Not enough trigger days",
  variant: "validation-badge--not-enough",
};
const BADGE_NONE: ValidationBadge = {
  state: "none",
  label: null,
  variant: null,
};

// True only for the not-testable / sparse-directional-trigger sub-state.
export function isEmpiricalNotRun(row: PerSecondary): boolean {
  return row.empirical_validation_status === "empirical_not_run";
}

// Two-outcome -> three-badge mapping.
//   board_validated + validated            -> PASS
//   not_validated  + validated             -> FAIL
//   not_validated  + empirical_failed      -> FAIL
//   not_validated  + empirical_not_run     -> Not enough trigger days
//   (no validation_outcome / v1)           -> none (no badge)
export function badgeForRow(row: PerSecondary): ValidationBadge {
  const outcome = row.validation_outcome;
  if (outcome === undefined || outcome === null) {
    return BADGE_NONE;
  }
  if (outcome === "board_validated") {
    return BADGE_PASS;
  }
  // outcome === "not_validated"
  if (row.empirical_validation_status === "empirical_not_run") {
    return BADGE_NOT_ENOUGH;
  }
  return BADGE_FAIL;
}

export function hasValidationOutcome(row: PerSecondary): boolean {
  return row.validation_outcome === "board_validated"
    || row.validation_outcome === "not_validated";
}

// --- Field-specific N/A display (centralized) ---------------------------
// On empirical_not_run rows the empirical p-value and bootstrap CI are
// not applicable (N/A), while bh_q_value and Bonferroni still render when
// present. On all other rows missing values fall back to UNAVAILABLE.

export function displayEmpiricalP(row: PerSecondary): string {
  const fallback = isEmpiricalNotRun(row) ? NA : UNAVAILABLE;
  return formatPValue(row.empirical_p_value, fallback);
}

export function displayParametricP(row: PerSecondary): string {
  return formatPValue(row.parametric_p_value, UNAVAILABLE);
}

export function displayBhQ(row: PerSecondary): string {
  // BH q-value renders whenever present, including on empirical_not_run.
  return formatQValue(row.bh_q_value, UNAVAILABLE);
}

export function displayBonferroni(row: PerSecondary): string {
  // Bonferroni renders whenever present, including on empirical_not_run.
  return formatPValue(row.bonferroni_p_value, UNAVAILABLE);
}

export function displayBootstrapCi(row: PerSecondary): string {
  const fallback = isEmpiricalNotRun(row) ? NA : UNAVAILABLE;
  return formatBootstrapCi(
    row.bootstrap_sharpe_ci_lower,
    row.bootstrap_sharpe_ci_upper,
    fallback,
  );
}
