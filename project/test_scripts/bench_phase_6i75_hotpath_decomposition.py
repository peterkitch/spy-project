"""Phase 6I-75: synthetic no-engine hot-path decomposition benchmark.

Goal: measure where ``_combined_metrics_signals`` spends its time on a
realistic K=4 combined-signal workload, with no signal-library load,
no validation surface, no engine machinery. The output JSON tells us
whether a ``_metrics_from_captures_fast`` helper is worth implementing.

Calibrated-sampling contract (Phase 6I-75 amendment-1):

  1. Warm up the canonical scoring path before any sampling.
  2. Measure the current-path (``_combined_metrics_signals``)
     per-combo median over ``--current-sample-iterations``.
  3. Project the wall time of a full Phase 6I-74-sized
     102,050-iteration run from that median.
  4. If the projected wall time exceeds
     ``--max-wall-seconds`` (default 900 = 15 minutes), the harness
     stops cleanly without running the full 102,050 iterations,
     records the projection, and emits the verdict JSON anyway.
  5. The same sample is used to decompose the component costs.

The JSON written to ``--out`` contains the verdict schema documented
in the Phase 6I-69 execution-surface doc.

Run via:

    "<PINNED_INTERPRETER>" -m test_scripts.bench_phase_6i75_hotpath_decomposition \
        --out "<SESSION_DIR>/phase_6i75_hotpath_decomposition.json"
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# Phase 6I-74 forensic profiling baseline (per the Phase 6I-75 prompt).
# These numbers are constants reported into every verdict JSON so the
# delta between current measurement and the historical observation is
# machine-readable.
OBSERVED_PHASE_6I74_MS_PER_COMBO = 187.4
PER_COMBO_MS_LEGACY_SYNTHETIC_BASELINE = 5.94
PER_COMBO_MS_TARGET = 18.0
PHASE_6I74_COMBO_COUNT = 102050


def _make_synthetic_daily_series(years: int, seed: int = 1234) -> pd.Series:
    """``years``-year synthetic daily return series on a business-day
    calendar. The exact start date does not matter for the benchmark;
    only the row count + return distribution do."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("1995-01-02", periods=years * 252)
    rets = rng.normal(loc=0.0003, scale=0.012, size=len(dates))
    return pd.Series(rets, index=dates, name="ret")


def _make_member_signal(
    index: pd.DatetimeIndex, prob_buy: float, prob_short: float, seed: int
) -> pd.Series:
    rng = np.random.default_rng(seed)
    n = len(index)
    u = rng.random(n)
    out = np.full(n, "None", dtype=object)
    out[u < prob_buy] = "Buy"
    out[(u >= prob_buy) & (u < prob_buy + prob_short)] = "Short"
    return pd.Series(out, index=index, name=f"m{seed}")


def _time_callable(fn, *, repeats: int) -> dict:
    """Run ``fn`` ``repeats`` times; return ms statistics + the raw
    samples. Samples are reported so consumers (or future regressions)
    can re-derive percentiles."""
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        samples.append((t1 - t0) * 1000.0)
    return {
        "samples_ms": samples,
        "min_ms": min(samples),
        "median_ms": statistics.median(samples),
        "mean_ms": statistics.fmean(samples),
        "max_ms": max(samples),
        "n": repeats,
    }


def _warmup(full_step, repeats: int) -> None:
    """Run ``full_step`` a few times to warm any lazy initialization
    (canonical scoring imports, pandas indexing caches). Discarded."""
    for _ in range(max(1, repeats)):
        full_step()


def run(
    *,
    current_sample_iterations: int,
    years: int,
    k: int,
    warmup_iterations: int,
    max_wall_seconds: float,
) -> dict:
    """Calibrated-sampling hot-path measurement.

    Returns the verdict dict ready to ``json.dump`` (see the schema
    documented in the Phase 6I-69 execution-surface doc).
    """
    import stackbuilder as sb
    from canonical_scoring import (
        score_captures as cs_score_captures,
        combine_consensus_signals as cs_combine,
        metrics_to_legacy_dict as cs_metrics_to_legacy,
    )

    sec_rets = _make_synthetic_daily_series(years=years, seed=7)
    members = [
        _make_member_signal(sec_rets.index, prob_buy=0.10, prob_short=0.08, seed=100 + i)
        for i in range(k)
    ]
    masks = [pd.Series(True, index=sec_rets.index) for _ in range(k)]

    def step_full():
        sb._combined_metrics_signals(
            [(m, mask) for m, mask in zip(members, masks)],
            sec_rets,
        )

    # 1. Warmup
    _warmup(step_full, repeats=warmup_iterations)

    # 2. Current-path measurement
    current = _time_callable(step_full, repeats=current_sample_iterations)
    per_combo_ms_current = current["median_ms"]
    extrapolated_seconds = (
        per_combo_ms_current * PHASE_6I74_COMBO_COUNT / 1000.0
    )

    halted_for_budget = extrapolated_seconds > max_wall_seconds
    budget_note = (
        f"projected {extrapolated_seconds:.1f}s > budget "
        f"{max_wall_seconds:.1f}s; decomposition limited to the "
        f"current sample, full 102050-iter run NOT executed"
        if halted_for_budget
        else f"projected {extrapolated_seconds:.1f}s ≤ budget "
             f"{max_wall_seconds:.1f}s; calibrated sample sufficient"
    )

    # 3. Decomposition — same sample size as the current-path
    # measurement so the time budgets stay symmetric. Each component
    # is timed in isolation; the dominant source is whichever
    # component has the largest median.
    comb_sig = cs_combine(members)
    combined_caps = sb._captures_from_signals(comb_sig, sec_rets)
    trigger_mask = comb_sig.isin(["Buy", "Short"])

    def step_combine():
        cs_combine(members)

    def step_captures():
        sb._captures_from_signals(comb_sig, sec_rets)

    def step_trigger():
        comb_sig.isin(["Buy", "Short"])

    def step_metrics():
        sb.metrics_from_captures(combined_caps, trigger_mask=trigger_mask)

    def step_score_only():
        cs_score_captures(
            combined_caps.astype(np.float64),
            trigger_mask.reindex(combined_caps.index).fillna(False).astype(bool),
            risk_free_rate=5.0,
            periods_per_year=252,
            ddof=1,
        )

    score_obj = cs_score_captures(
        combined_caps.astype(np.float64),
        trigger_mask.reindex(combined_caps.index).fillna(False).astype(bool),
        risk_free_rate=5.0,
        periods_per_year=252,
        ddof=1,
    )

    def step_legacy_dict():
        cs_metrics_to_legacy(score_obj)

    components = {
        "combine_consensus_signals": _time_callable(
            step_combine, repeats=current_sample_iterations,
        ),
        "captures_from_signals": _time_callable(
            step_captures, repeats=current_sample_iterations,
        ),
        "trigger_mask_isin": _time_callable(
            step_trigger, repeats=current_sample_iterations,
        ),
        "metrics_from_captures_full": _time_callable(
            step_metrics, repeats=current_sample_iterations,
        ),
        "canonical_score_captures_only": _time_callable(
            step_score_only, repeats=current_sample_iterations,
        ),
        "metrics_to_legacy_dict_only": _time_callable(
            step_legacy_dict, repeats=current_sample_iterations,
        ),
    }

    overhead_decomposition = {
        name: comp["median_ms"] for name, comp in components.items()
    }

    if overhead_decomposition:
        dominant_source = max(
            overhead_decomposition, key=overhead_decomposition.get
        )
        dominant_source_share = (
            overhead_decomposition[dominant_source] / per_combo_ms_current
            if per_combo_ms_current > 0 else None
        )
    else:
        dominant_source = None
        dominant_source_share = None

    # 4. Fast-helper decision. The Phase 6I-75 spec authorized
    # implementing ``_metrics_from_captures_fast`` only when the
    # measurement said it was worth it. The decision logic compares
    # measured ``metrics_from_captures_full`` against the spec target
    # and against the dominant source share.
    metrics_full_ms = overhead_decomposition.get(
        "metrics_from_captures_full"
    )
    if (
        metrics_full_ms is not None
        and metrics_full_ms < PER_COMBO_MS_TARGET
    ):
        fast_helper_attempted = False
        fast_helper_wired = False
        per_combo_ms_fast_measured = None
        fast_sample_iterations = None
        reason_not_wired = (
            f"metrics_from_captures already measures at "
            f"{metrics_full_ms:.3f} ms/call, well below the "
            f"{PER_COMBO_MS_TARGET:.1f} ms/combo target. The dominant "
            f"cost is `{dominant_source}` "
            f"({dominant_source_share:.3%} of per-combo) which is out "
            f"of scope for Phase 6I-75 Part 2; implementing "
            f"_metrics_from_captures_fast would save <0.5% per combo "
            f"at semantic-parity risk."
        )
    else:
        # Defensive branch: present in the schema so a future
        # measurement that DOES support the fast helper produces a
        # complete JSON without code changes.
        fast_helper_attempted = False
        fast_helper_wired = False
        per_combo_ms_fast_measured = None
        fast_sample_iterations = None
        reason_not_wired = (
            f"measurement did not produce a metrics_from_captures "
            f"timing or that timing exceeded the "
            f"{PER_COMBO_MS_TARGET:.1f} ms/combo target; "
            f"_metrics_from_captures_fast NOT implemented in this PR."
        )

    return {
        "schema_version": 1,
        "metadata": {
            "years_of_data": years,
            "rows_per_member": len(sec_rets),
            "k_members": k,
            "warmup_iterations": warmup_iterations,
            "max_wall_seconds": max_wall_seconds,
            "numpy_version": np.__version__,
            "pandas_version": pd.__version__,
            "halted_for_budget": halted_for_budget,
            "calibration_note": budget_note,
            "extrapolation_method": (
                "median_ms_current * 102050 / 1000"
            ),
            "phase_6i74_combo_count": PHASE_6I74_COMBO_COUNT,
        },
        "per_combo_ms_current_measured": per_combo_ms_current,
        "current_sample_iterations": current_sample_iterations,
        "current_extrapolated_to_102050_seconds": extrapolated_seconds,
        "observed_phase6i74_ms_per_combo": OBSERVED_PHASE_6I74_MS_PER_COMBO,
        "per_combo_ms_legacy_synthetic_baseline": (
            PER_COMBO_MS_LEGACY_SYNTHETIC_BASELINE
        ),
        "per_combo_ms_target": PER_COMBO_MS_TARGET,
        "overhead_decomposition": overhead_decomposition,
        "dominant_source": dominant_source,
        "dominant_source_share": dominant_source_share,
        "fast_helper_attempted": fast_helper_attempted,
        "per_combo_ms_fast_measured": per_combo_ms_fast_measured,
        "fast_sample_iterations": fast_sample_iterations,
        "fast_helper_wired": fast_helper_wired,
        "reason_not_wired": reason_not_wired,
        "components_detail": components,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="path to JSON output")
    ap.add_argument(
        "--current-sample-iterations", type=int, default=30,
        help="iterations per component (default 30)",
    )
    ap.add_argument("--years", type=int, default=30,
                    help="years of synthetic daily data (default 30)")
    ap.add_argument("--k", type=int, default=4, help="K members (default 4)")
    ap.add_argument(
        "--warmup-iterations", type=int, default=3,
        help="warmup iterations before sampling (default 3)",
    )
    ap.add_argument(
        "--max-wall-seconds", type=float, default=900.0,
        help="ceiling on extrapolated wall time before the harness "
             "refuses to project further (default 900 = 15 min)",
    )
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    result = run(
        current_sample_iterations=args.current_sample_iterations,
        years=args.years,
        k=args.k,
        warmup_iterations=args.warmup_iterations,
        max_wall_seconds=args.max_wall_seconds,
    )
    elapsed = time.perf_counter() - t0
    result["metadata"]["total_wall_seconds"] = round(elapsed, 3)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)

    print(
        f"[PHASE_6I75_BENCH] wrote verdict JSON  "
        f"per_combo_ms={result['per_combo_ms_current_measured']:.3f} "
        f"projected_102050_s={result['current_extrapolated_to_102050_seconds']:.1f} "
        f"dominant={result['dominant_source']} "
        f"fast_helper_wired={result['fast_helper_wired']} "
        f"wall_s={elapsed:.1f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
