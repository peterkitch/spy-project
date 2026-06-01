"""K=6 MTF validation adapter sub-package.

This sub-package implements the K=6 MTF SelectionAdapter for the
locked validation_contract_v1 / validation_methodology_v1 from
``md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md``
(Section 13.5 K=6 MTF, added by the 2026-05-31 amendment).

The adapter is operator-supervised tooling that honestly recomputes
the K=6 MTF ranking-row construction per walk-forward fold from
cutoff-safe upstream inputs and emits a single validation_contract_v1
sidecar at ``output/validation/<run_id>/validation.json``. It does
NOT open ``output/k6_mtf/<run>/k6_mtf_history.json`` or
``output/k6_mtf/<run>/k6_mtf_ranking.json`` as validation evidence.
"""
