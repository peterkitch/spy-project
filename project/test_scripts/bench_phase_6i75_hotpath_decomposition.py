"""Phase 6I-75: synthetic no-engine hot-path decomposition benchmark.

Goal: measure where ``_combined_metrics_signals`` spends its time on a
realistic K=4 combined-signal workload, with no signal-library load,
no validation surface, no engine machinery. The output JSON tells us
whether a ``_metrics_from_captures_fast`` helper is worth implementing.

Budget: < 15 minutes wall time. Each component is sampled with a small
``time.perf_counter`` loop and the per-call median is reported.

Run via:

    "<PINNED_INTERPRETER>" -m test_scripts.bench_phase_6i75_hotpath_decomposition \
        --out "<SESSION_DIR>/phase_6i75_hotpath_decomposition.json"

The harness is intentionally side-effect free — it writes one JSON file
to the path given by ``--out`` and prints a short summary.
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


def _make_synthetic_daily_series(years: int, seed: int = 1234) -> pd.Series:
    """30-year synthetic daily return series on a business-day calendar."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("1995-01-02", periods=years * 252)
    rets = rng.normal(loc=0.0003, scale=0.012, size=len(dates))
    return pd.Series(rets, index=dates, name="ret")


def _make_member_signal(
    index: pd.DatetimeIndex, prob_buy: float, prob_short: float, seed: int
) -> pd.Series:
    """One member's Buy/Short/None signal series aligned to ``index``."""
    rng = np.random.default_rng(seed)
    n = len(index)
    u = rng.random(n)
    out = np.full(n, "None", dtype=object)
    out[u < prob_buy] = "Buy"
    out[(u >= prob_buy) & (u < prob_buy + prob_short)] = "Short"
    return pd.Series(out, index=index, name=f"m{seed}")


def _time_callable(fn, *, repeats: int) -> dict:
    """Run ``fn`` ``repeats`` times; return min / median / mean / max in ms."""
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


def run(repeats: int, years: int, k: int) -> dict:
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

    # Component A: combine_consensus_signals (single combine call)
    def step_combine():
        cs_combine(members)
    comp_combine = _time_callable(step_combine, repeats=repeats)

    # Component B: _captures_from_signals
    comb_sig = cs_combine(members)
    def step_captures():
        sb._captures_from_signals(comb_sig, sec_rets)
    comp_captures = _time_callable(step_captures, repeats=repeats)

    # Component C: trigger_mask construction
    def step_trigger():
        comb_sig.isin(["Buy", "Short"])
    comp_trigger = _time_callable(step_trigger, repeats=repeats)

    # Component D: metrics_from_captures (full canonical delegation)
    combined_caps = sb._captures_from_signals(comb_sig, sec_rets)
    trigger_mask = comb_sig.isin(["Buy", "Short"])
    def step_metrics():
        sb.metrics_from_captures(combined_caps, trigger_mask=trigger_mask)
    comp_metrics = _time_callable(step_metrics, repeats=repeats)

    # Component E: _canonical_score_captures direct (no legacy dict)
    def step_score_only():
        cs_score_captures(
            combined_caps.astype(np.float64),
            trigger_mask.reindex(combined_caps.index).fillna(False).astype(bool),
            risk_free_rate=5.0,
            periods_per_year=252,
            ddof=1,
        )
    comp_score_only = _time_callable(step_score_only, repeats=repeats)

    # Component F: legacy-dict conversion only
    score_obj = cs_score_captures(
        combined_caps.astype(np.float64),
        trigger_mask.reindex(combined_caps.index).fillna(False).astype(bool),
        risk_free_rate=5.0,
        periods_per_year=252,
        ddof=1,
    )
    def step_legacy_dict():
        cs_metrics_to_legacy(score_obj)
    comp_legacy_dict = _time_callable(step_legacy_dict, repeats=repeats)

    # Component G: full _combined_metrics_signals end-to-end
    def step_full():
        sb._combined_metrics_signals(
            [(m, mask) for m, mask in zip(members, masks)],
            sec_rets,
        )
    comp_full = _time_callable(step_full, repeats=repeats)

    return {
        "metadata": {
            "years_of_data": years,
            "rows_per_member": len(sec_rets),
            "k_members": k,
            "repeats_per_component": repeats,
            "numpy_version": np.__version__,
            "pandas_version": pd.__version__,
        },
        "components": {
            "A_combine_consensus_signals": comp_combine,
            "B_captures_from_signals": comp_captures,
            "C_trigger_mask_isin": comp_trigger,
            "D_metrics_from_captures_full": comp_metrics,
            "E_canonical_score_captures_only": comp_score_only,
            "F_metrics_to_legacy_dict_only": comp_legacy_dict,
            "G_combined_metrics_signals_full": comp_full,
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="path to JSON output")
    ap.add_argument("--repeats", type=int, default=30,
                    help="iterations per component (default 30)")
    ap.add_argument("--years", type=int, default=30,
                    help="years of synthetic daily data (default 30)")
    ap.add_argument("--k", type=int, default=4, help="K members (default 4)")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    result = run(repeats=args.repeats, years=args.years, k=args.k)
    elapsed = time.perf_counter() - t0
    result["metadata"]["total_wall_seconds"] = round(elapsed, 3)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)

    summary = result["components"]
    print(f"[PHASE_6I75_BENCH] wrote {out_path}  total_wall={elapsed:.1f}s")
    for name, comp in summary.items():
        print(f"  {name}: median={comp['median_ms']:.3f} ms "
              f"min={comp['min_ms']:.3f} ms n={comp['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
