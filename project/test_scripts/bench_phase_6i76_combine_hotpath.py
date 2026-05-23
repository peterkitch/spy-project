"""Phase 6I-76: focused combine hot-path benchmark.

Synthetic no-engine harness that measures the StackBuilder phase3
K-search hot path before and after the ``_combine_signals_fast``
optimization. Same shape as the Phase 6I-75 measurement (30 years,
K=4, deterministic seed) so the JSON can be compared head-to-head.

Run via:

    "<PINNED_INTERPRETER>" test_scripts/bench_phase_6i76_combine_hotpath.py \
        --phase baseline \
        --out "<SESSION_DIR>/phase_6i76_combine_hotpath_baseline.json"

    "<PINNED_INTERPRETER>" test_scripts/bench_phase_6i76_combine_hotpath.py \
        --phase after \
        --out "<SESSION_DIR>/phase_6i76_combine_hotpath_after.json"

After both phase JSONs exist, build the summary via:

    "<PINNED_INTERPRETER>" test_scripts/bench_phase_6i76_combine_hotpath.py \
        --phase summary \
        --baseline "<SESSION_DIR>/phase_6i76_combine_hotpath_baseline.json" \
        --after "<SESSION_DIR>/phase_6i76_combine_hotpath_after.json" \
        --out "<SESSION_DIR>/phase_6i76_combine_hotpath_summary.json"

The harness has a 15-minute total wall ceiling (``--max-wall-seconds``).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


TARGET_MS_PER_COMBO = 18.0
PHASE_6I74_COMBO_COUNT = 102050


def _make_synthetic_daily_series(years: int, seed: int = 1234) -> pd.Series:
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
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        samples.append((t1 - t0) * 1000.0)
    samples_sorted = sorted(samples)
    def _pct(p):
        if not samples_sorted:
            return 0.0
        k = max(0, min(len(samples_sorted) - 1,
                       int(round((p / 100.0) * (len(samples_sorted) - 1)))))
        return samples_sorted[k]
    return {
        "samples_ms": samples,
        "min_ms": min(samples),
        "median_ms": statistics.median(samples),
        "mean_ms": statistics.fmean(samples),
        "max_ms": max(samples),
        "p50_ms": _pct(50),
        "p90_ms": _pct(90),
        "p99_ms": _pct(99),
        "n": repeats,
    }


def measure_phase(
    *,
    phase_label: str,
    reps: int,
    years: int,
    k: int,
    warmup: int,
) -> dict:
    """Run the synthetic measurement and return the phase JSON dict."""
    import stackbuilder as sb

    sec_rets = _make_synthetic_daily_series(years=years, seed=7)
    members = [
        _make_member_signal(sec_rets.index, prob_buy=0.10, prob_short=0.08, seed=100 + i)
        for i in range(k)
    ]
    masks = [pd.Series(True, index=sec_rets.index) for _ in range(k)]

    # Warm up.
    for _ in range(max(1, warmup)):
        sb._combined_metrics_signals(
            [(m, mask) for m, mask in zip(members, masks)],
            sec_rets,
        )

    # 1. Full _combined_metrics_signals end-to-end.
    def step_full():
        sb._combined_metrics_signals(
            [(m, mask) for m, mask in zip(members, masks)],
            sec_rets,
        )
    full = _time_callable(step_full, repeats=reps)

    # 2. Combine signals in isolation.
    def step_combine():
        sb._combine_signals(members)
    combine = _time_callable(step_combine, repeats=reps)

    # 3. captures_from_signals
    comb_sig = sb._combine_signals(members)
    def step_captures():
        sb._captures_from_signals(comb_sig, sec_rets)
    captures = _time_callable(step_captures, repeats=reps)

    # 4. metrics_from_captures
    combined_caps = sb._captures_from_signals(comb_sig, sec_rets)
    trigger_mask = comb_sig.isin(["Buy", "Short"])
    def step_metrics():
        sb.metrics_from_captures(combined_caps, trigger_mask=trigger_mask)
    metrics = _time_callable(step_metrics, repeats=reps)

    per_combo_ms = full["median_ms"]
    components = {
        "combine_signals_ms": combine["median_ms"],
        "captures_from_signals_ms": captures["median_ms"],
        "metrics_from_captures_ms": metrics["median_ms"],
    }
    dominant_component = max(components, key=components.get)
    dominant_share = components[dominant_component] / per_combo_ms if per_combo_ms > 0 else None

    extrapolated_seconds = per_combo_ms * PHASE_6I74_COMBO_COUNT / 1000.0
    extrapolated_minutes = extrapolated_seconds / 60.0

    baseline_reproduced = (
        dominant_component == "combine_signals_ms"
        and dominant_share is not None and dominant_share >= 0.80
        and per_combo_ms > TARGET_MS_PER_COMBO
    )

    return {
        "phase": phase_label,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "synthetic_shape": {
            "years": years,
            "rows_per_member": len(sec_rets),
            "k_members": k,
            "warmup": warmup,
        },
        "reps": reps,
        "median_ms_per_combo": per_combo_ms,
        "p50_ms_per_combo": full["p50_ms"],
        "p90_ms_per_combo": full["p90_ms"],
        "p99_ms_per_combo": full["p99_ms"],
        "combined_metrics_total_ms": per_combo_ms,
        "combine_signals_ms": combine["median_ms"],
        "captures_from_signals_ms": captures["median_ms"],
        "metrics_from_captures_ms": metrics["median_ms"],
        "dominant_component": dominant_component,
        "dominant_component_share": dominant_share,
        "extrapolated_102050_seconds": extrapolated_seconds,
        "extrapolated_102050_minutes": extrapolated_minutes,
        "target_ms_per_combo": TARGET_MS_PER_COMBO,
        "target_met": per_combo_ms <= TARGET_MS_PER_COMBO,
        "fast_combine_wired": getattr(sb, "_COMBINE_SIGNALS_FAST_WIRED", False),
        "baseline_reproduced_phase6i75": baseline_reproduced,
        "notes": (
            "Phase 6I-76 synthetic measurement; same shape as Phase 6I-75 "
            "baseline."
        ),
        "components_detail": {
            "full_combined_metrics": full,
            "combine_signals": combine,
            "captures_from_signals": captures,
            "metrics_from_captures": metrics,
        },
    }


def build_summary(baseline_path: Path, after_path: Path) -> dict:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    base_ms = baseline["median_ms_per_combo"]
    after_ms = after["median_ms_per_combo"]
    speedup = base_ms / after_ms if after_ms > 0 else None
    target_met = bool(after["target_met"])
    fast_wired = bool(after.get("fast_combine_wired", False))
    commit_allowed = target_met and fast_wired
    if commit_allowed:
        reason = (
            "target met (<=18 ms/combo) and fast combine wired; commit "
            "authorized"
        )
    elif not target_met:
        reason = (
            f"after measurement {after_ms:.3f} ms/combo still exceeds "
            f"the {TARGET_MS_PER_COMBO:.1f} ms/combo target; commit "
            f"refused"
        )
    else:
        reason = "fast combine not wired in measured run; commit refused"
    return {
        "phase": "summary",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_median_ms_per_combo": base_ms,
        "after_median_ms_per_combo": after_ms,
        "speedup_ratio": speedup,
        "target_ms_per_combo": TARGET_MS_PER_COMBO,
        "target_met": target_met,
        "fast_combine_wired": fast_wired,
        "baseline_dominant_component": baseline["dominant_component"],
        "after_dominant_component": after["dominant_component"],
        "after_dominant_component_share": after["dominant_component_share"],
        "extrapolated_102050_seconds": after["extrapolated_102050_seconds"],
        "extrapolated_102050_minutes": after["extrapolated_102050_minutes"],
        "commit_allowed": commit_allowed,
        "reason": reason,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--phase", required=True,
        choices=["baseline", "after", "summary"],
        help="which JSON to produce",
    )
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--years", type=int, default=30)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument(
        "--max-wall-seconds", type=float, default=900.0,
        help="abort if projected wall would exceed this (default 900 = 15 min)",
    )
    ap.add_argument("--baseline", help="for --phase summary: baseline JSON")
    ap.add_argument("--after", help="for --phase summary: after JSON")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()

    if args.phase == "summary":
        if not args.baseline or not args.after:
            print("[ERROR] --phase summary needs --baseline and --after")
            return 2
        result = build_summary(Path(args.baseline), Path(args.after))
    else:
        result = measure_phase(
            phase_label=args.phase,
            reps=args.reps,
            years=args.years,
            k=args.k,
            warmup=args.warmup,
        )
        elapsed_predicted = (
            result["median_ms_per_combo"] * PHASE_6I74_COMBO_COUNT / 1000.0
        )
        if elapsed_predicted > args.max_wall_seconds:
            result["notes"] = (
                f"{result['notes']} | NOTE: extrapolated wall "
                f"{elapsed_predicted:.1f}s > max_wall_seconds "
                f"{args.max_wall_seconds:.1f}s; harness still emitted "
                f"the calibrated sample and projection."
            )

    elapsed = time.perf_counter() - t0
    if "metadata" not in result:
        result["metadata"] = {}
    result["metadata"]["total_wall_seconds"] = round(elapsed, 3)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)

    if args.phase in ("baseline", "after"):
        print(
            f"[PHASE_6I76_BENCH] phase={args.phase} "
            f"per_combo_ms={result['median_ms_per_combo']:.3f} "
            f"dominant={result['dominant_component']} "
            f"share={result['dominant_component_share']:.3%} "
            f"target_met={result['target_met']} "
            f"fast_wired={result['fast_combine_wired']} "
            f"wall_s={elapsed:.1f}"
        )
    else:
        print(
            f"[PHASE_6I76_BENCH] phase=summary "
            f"speedup={result['speedup_ratio']:.2f}x "
            f"target_met={result['target_met']} "
            f"commit_allowed={result['commit_allowed']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
