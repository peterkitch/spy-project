"""tmp_path-only tests for the K=6 MTF validation adapter.

These tests never touch production roots. They build synthetic
member signal libraries, synthetic secondary closes, and synthetic
upstream StackBuilder selected_build / combo_k=6 inputs under
``tmp_path``; the adapter is exercised end-to-end with small fold
geometry; no real ``output/`` writes are produced.

The locked validation_engine surface is exercised with
``n_permutations=0`` and ``n_bootstrap_samples=0`` so the empirical
layer is fully disabled. No real Phase 5 evidence is produced.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Bootstrap PROJECT_DIR so the adapter and its project deps import cleanly.
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


from utils.k6_mtf_validation import adapter as adapter_mod  # noqa: E402
from utils.k6_mtf_validation.adapter import (  # noqa: E402
    K6_MTF_APP_SURFACE,
    K6_MTF_PRODUCER_ENGINE,
    K6_MTF_REASON_PREFIX,
    K6MtfValidationAdapter,
    REASON_HISTORY_UNDERFLOW,
    REASON_MISSING_SELECTED_BUILD,
    REASON_NO_TRIGGERS,
    _SecondaryInputs,
    _synthesize_candidate_snapshots_in_window,
    _synthesize_current_snapshot,
    build_adapter_inputs,
    resolve_validation_output_base,
    run_validation,
)

import k6_mtf_history_producer as k6hp  # noqa: E402
import validation_engine as ve  # noqa: E402
from validation_engine import (  # noqa: E402
    FoldContext,
    StrategyCandidate,
    validate_validation_contract_v1,
)
import honest_validation_ledger as hvl  # noqa: E402
import controlled_compute as cc  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic-fixture builders
# ---------------------------------------------------------------------------


SIGNAL_BUY = "Buy"
SIGNAL_SHORT = "Short"
SIGNAL_NONE = "None"

LAUNCH_FAMILY: Tuple[str, ...] = (
    "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "SPY", "TSLA",
)


def _build_member_library_pickle(
    path: Path, ticker: str, interval: str,
    dates: pd.DatetimeIndex, signals: Sequence[str],
) -> None:
    data = {
        "ticker": ticker,
        "interval": interval,
        "dates": list(dates),
        "signals": list(signals),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(data, fh)


def _write_combo_k6(
    selected_run_dir: Path, members: Sequence[Tuple[str, str]],
) -> None:
    selected_run_dir.mkdir(parents=True, exist_ok=True)
    combo = {
        "Members": [f"{t}[{p}]" for t, p in members],
    }
    (selected_run_dir / "combo_k=6.json").write_text(
        json.dumps(combo), encoding="utf-8",
    )


def _write_selected_build(
    stackbuilder_root: Path, secondary: str, selected_run_dir: Path,
) -> None:
    (stackbuilder_root / secondary).mkdir(parents=True, exist_ok=True)
    payload = {
        "selected_run_dir": str(selected_run_dir).replace("\\", "/"),
    }
    (stackbuilder_root / secondary / "selected_build.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def _write_secondary_close_csv(
    price_cache_dir: Path, secondary: str,
    dates: pd.DatetimeIndex, closes: Sequence[float],
) -> None:
    price_cache_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "Date": [d.strftime("%Y-%m-%d") for d in dates],
        "Close": list(closes),
    })
    df.to_csv(price_cache_dir / f"{secondary}.csv", index=False)


def _build_synthetic_universe(
    root: Path,
    *,
    secondaries: Sequence[str] = LAUNCH_FAMILY,
    n_bars: int = 60,
    seed: int = 1234,
    always_buy: bool = True,
) -> Dict[str, Path]:
    """Build a complete synthetic universe under ``root``.

    Returns the four input-root paths the adapter CLI expects.

    ``always_buy=True`` causes every member 1d/non-daily slot to read
    Buy on every date, so the K=6 combine + match rule produces 100%
    BUY matches and the per-fold OOS window evaluates trades on every
    bar (good for end-to-end shape tests).
    """
    rng = np.random.default_rng(seed)
    stackbuilder_root = root / "stackbuilder"
    stable_dir = root / "signal_library" / "stable"
    price_cache_dir = root / "price_cache" / "daily"
    cache_dir = root / "cache" / "results"
    stable_dir.mkdir(parents=True, exist_ok=True)

    # Members live under stable/ keyed by ticker. We use 6 generic
    # member tickers shared across all secondaries (the K=6 stack does
    # not require disjoint member sets across secondaries).
    member_tickers = (
        "ABC", "DEF", "GHI", "JKL", "MNO", "PQR",
    )
    # All-D protocols so the K=6 active-signal-unanimity combine over
    # all-Buy member signals produces BUY (every member's protocol-
    # adjusted signal is BUY -> active_buy=6, active_short=0 -> BUY).
    # The [D]/[I] protocol math is exercised separately by
    # TestApplyProtocolReuse.
    protocols = ("D",) * 6
    members = list(zip(member_tickers, protocols))

    # Dates: business days starting 2020-01-02.
    dates = pd.bdate_range("2020-01-02", periods=n_bars)
    timeframes = ("1d", "1wk", "1mo", "3mo", "1y")

    if always_buy:
        signals = [SIGNAL_BUY] * n_bars
    else:
        choices = [SIGNAL_BUY, SIGNAL_NONE, SIGNAL_SHORT]
        signals = list(rng.choice(choices, size=n_bars))

    # Write each (member, timeframe) library once: shared across
    # secondaries. The library content respects the per-member
    # protocol via the combined-pipeline apply_protocol call (the
    # adapter reuses the production helper).
    for ticker, _protocol in members:
        for tf in timeframes:
            if tf == "1d":
                name = f"{ticker}_stable_v1_0_0.pkl"
            else:
                name = f"{ticker}_stable_v1_0_0_{tf}.pkl"
            _build_member_library_pickle(
                stable_dir / name, ticker, tf, dates, signals,
            )

    # Per-secondary StackBuilder selected_build + combo_k=6 + close.
    base_close = 100.0
    for sec in secondaries:
        sec_run_dir = stackbuilder_root / sec / "selected_run"
        _write_combo_k6(sec_run_dir, members)
        _write_selected_build(stackbuilder_root, sec, sec_run_dir)
        # Deterministic increasing close so BUY captures are positive
        # and a small dip in the middle so a SHORT-protocol path is
        # tested when always_buy=False.
        closes = list(base_close + np.cumsum(
            rng.normal(loc=0.05, scale=0.5, size=n_bars),
        ))
        _write_secondary_close_csv(
            price_cache_dir, sec, dates, closes,
        )

    return {
        "stackbuilder_root": stackbuilder_root,
        "stable_dir": stable_dir,
        "price_cache_dir": price_cache_dir,
        "cache_dir": cache_dir,
    }


def _build_fold(
    *,
    fold_index: int,
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
) -> FoldContext:
    return FoldContext(
        fold_index=fold_index,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        selection_cutoff=train_end,
        evaluation_cutoff=test_end,
    )


def _build_universe_with_signal_program(
    root: Path,
    *,
    secondary: str,
    n_bars: int,
    member_signals: Sequence[str],
    closes: Sequence[float],
) -> Dict[str, Path]:
    """Build a one-secondary synthetic universe with the operator's
    explicit per-bar member-signal program and per-bar close path.

    Every member of the K=6 stack carries the SAME ``member_signals``
    sequence (same length as ``closes``), so the K=6 unanimity combine
    at each bar reduces to the bar's literal signal. With all-D
    protocols this means: ``combined[i] == member_signals[i]`` on
    every bar. The 1d slot then equals that combined value; non-daily
    timeframes forward-fill from the same series.

    The match-rule wildcard (``_bar_matches_alignment``) becomes:

    - current_snapshot = snapshot at the last in-sample bar (=
      ``member_signals[train_end_position]``).
    - For an OOS bar with the same literal token, it matches.
    - For an OOS bar with the opposite directional token (BUY when
      current is SHORT, or SHORT when current is BUY), it does NOT
      match (the contradiction fails the alignment rule).
    - NONE / UNAVAILABLE tokens are wildcards on both sides.

    Use this helper when a test needs a controlled mix of matched
    and non-matched OOS bars (e.g. empirical-pool full-population
    semantics).
    """
    assert len(member_signals) == n_bars
    assert len(closes) == n_bars
    stackbuilder_root = root / "stackbuilder"
    stable_dir = root / "signal_library" / "stable"
    price_cache_dir = root / "price_cache" / "daily"
    cache_dir = root / "cache" / "results"
    stable_dir.mkdir(parents=True, exist_ok=True)

    member_tickers = ("ABC", "DEF", "GHI", "JKL", "MNO", "PQR")
    protocols = ("D",) * 6
    members = list(zip(member_tickers, protocols))

    dates = pd.bdate_range("2020-01-02", periods=n_bars)
    timeframes = ("1d", "1wk", "1mo", "3mo", "1y")

    for ticker, _protocol in members:
        for tf in timeframes:
            if tf == "1d":
                name = f"{ticker}_stable_v1_0_0.pkl"
            else:
                name = f"{ticker}_stable_v1_0_0_{tf}.pkl"
            _build_member_library_pickle(
                stable_dir / name, ticker, tf, dates, member_signals,
            )

    sec_run_dir = stackbuilder_root / secondary / "selected_run"
    _write_combo_k6(sec_run_dir, members)
    _write_selected_build(stackbuilder_root, secondary, sec_run_dir)
    _write_secondary_close_csv(
        price_cache_dir, secondary, dates, closes,
    )

    return {
        "stackbuilder_root": stackbuilder_root,
        "stable_dir": stable_dir,
        "price_cache_dir": price_cache_dir,
        "cache_dir": cache_dir,
        "dates": dates,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPathDoublingResolver:
    """resolve_validation_output_base must not produce a doubled
    ``<PROJECT_DIR>/project/...`` path whether invoked from
    ``<PROJECT_DIR>`` or ``<REPO_ROOT>``.
    """

    def test_default_base_anchors_at_repo_root(self, tmp_path):
        repo_root = tmp_path / "repo"
        project_dir = repo_root / "project"
        project_dir.mkdir(parents=True)
        resolved = resolve_validation_output_base(
            project_dir=project_dir,
            repo_root=repo_root,
        )
        expected = (repo_root / "project" / "output" / "validation").resolve()
        assert resolved == expected
        assert "project" + str(Path("/project")) not in str(resolved)
        parts = resolved.parts
        # Exactly one "project" segment.
        assert parts.count("project") == 1

    def test_invocation_from_project_dir_does_not_double(self, tmp_path):
        # Simulate cwd = <PROJECT_DIR>. The resolver must NOT join
        # project/output/validation onto <PROJECT_DIR> (which would
        # produce <PROJECT_DIR>/project/output/validation).
        repo_root = tmp_path / "repo2"
        project_dir = repo_root / "project"
        project_dir.mkdir(parents=True)
        resolved = resolve_validation_output_base(
            project_dir=project_dir,
            repo_root=repo_root,
            base_dir=Path("project/output/validation"),
        )
        assert resolved == (
            repo_root / "project" / "output" / "validation"
        ).resolve()
        # The doubled path that would result from a naive
        # (project_dir / base_dir).resolve() must NOT appear.
        doubled = (project_dir / "project" / "output" / "validation").resolve()
        assert resolved != doubled

    def test_absolute_base_passed_through(self, tmp_path):
        abs_base = (tmp_path / "explicit_validation").resolve()
        resolved = resolve_validation_output_base(base_dir=abs_base)
        assert resolved == abs_base

    def test_non_project_relative_base_anchors_at_project_dir(self, tmp_path):
        repo_root = tmp_path / "repo3"
        project_dir = repo_root / "project"
        project_dir.mkdir(parents=True)
        resolved = resolve_validation_output_base(
            project_dir=project_dir,
            repo_root=repo_root,
            base_dir=Path("custom/output/validation"),
        )
        assert resolved == (
            project_dir / "custom" / "output" / "validation"
        ).resolve()


class TestApplyProtocolReuse:
    """The adapter MUST import and reuse k6_mtf_history_producer's
    apply_protocol verbatim. No duplication.
    """

    def test_adapter_uses_history_producer_apply_protocol(self):
        # The adapter module's "apply_protocol" attribute is imported
        # from k6_mtf_history_producer and must be the SAME callable
        # object.
        assert adapter_mod.apply_protocol is k6hp.apply_protocol

    def test_apply_protocol_d_preserves(self):
        from utils.k6_mtf_validation.adapter import apply_protocol
        assert apply_protocol("BUY", "D") == "BUY"
        assert apply_protocol("SHORT", "D") == "SHORT"
        assert apply_protocol("NONE", "D") == "NONE"
        assert apply_protocol("UNAVAILABLE", "D") == "UNAVAILABLE"

    def test_apply_protocol_i_swaps_buy_short(self):
        from utils.k6_mtf_validation.adapter import apply_protocol
        assert apply_protocol("BUY", "I") == "SHORT"
        assert apply_protocol("SHORT", "I") == "BUY"
        assert apply_protocol("NONE", "I") == "NONE"
        assert apply_protocol("UNAVAILABLE", "I") == "UNAVAILABLE"


class TestSecondaryInputLoading:
    def test_loads_all_available_secondaries(self, tmp_path):
        roots = _build_synthetic_universe(tmp_path, n_bars=20)
        inputs = build_adapter_inputs(
            LAUNCH_FAMILY,
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        for sec in LAUNCH_FAMILY:
            assert inputs[sec].available is True
            assert inputs[sec].stack is not None
            assert inputs[sec].secondary_close is not None

    def test_missing_selected_build_records_visible_issue(self, tmp_path):
        roots = _build_synthetic_universe(tmp_path, n_bars=20)
        # Remove selected_build.json for AAPL only.
        (roots["stackbuilder_root"] / "AAPL" / "selected_build.json").unlink()
        inputs = build_adapter_inputs(
            LAUNCH_FAMILY,
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        assert inputs["AAPL"].available is False
        assert any(
            f"[{K6_MTF_REASON_PREFIX}:" in iss
            for iss in inputs["AAPL"].issues
        )
        assert any(
            REASON_MISSING_SELECTED_BUILD in iss
            for iss in inputs["AAPL"].issues
        )
        # Other secondaries unaffected.
        for sec in LAUNCH_FAMILY:
            if sec == "AAPL":
                continue
            assert inputs[sec].available is True


class TestSelectForFoldOnePerSecondary:
    def test_one_candidate_per_launch_family_secondary(self, tmp_path):
        roots = _build_synthetic_universe(tmp_path, n_bars=40)
        inputs = build_adapter_inputs(
            LAUNCH_FAMILY,
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=LAUNCH_FAMILY, secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=40)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        candidates = adapter.select_for_fold(fold)
        assert len(candidates) == len(LAUNCH_FAMILY)
        sids = {c.strategy_id for c in candidates}
        assert len(sids) == len(LAUNCH_FAMILY)
        # strategy_id format: "k6_mtf:<SEC>:<HISTORY_AS_OF_DATE>".
        for c in candidates:
            assert c.strategy_id.startswith("k6_mtf:")
            assert "as_of=" in c.strategy_label

    def test_missing_input_secondary_visible_with_unavailable_payload(
        self, tmp_path,
    ):
        roots = _build_synthetic_universe(tmp_path, n_bars=40)
        (roots["stackbuilder_root"] / "MSFT" / "selected_build.json").unlink()
        inputs = build_adapter_inputs(
            LAUNCH_FAMILY,
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=LAUNCH_FAMILY, secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=40)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        candidates = adapter.select_for_fold(fold)
        # Family size preserved.
        assert len(candidates) == len(LAUNCH_FAMILY)
        msft = next(c for c in candidates if "MSFT" in c.strategy_id)
        assert msft.app_payload["input_available"] is False
        assert any(
            f"[{K6_MTF_REASON_PREFIX}:" in iss
            for iss in msft.app_payload["issues"]
        )


class TestNoLookaheadGuards:
    def test_select_for_fold_does_not_open_output_k6_mtf(
        self, tmp_path, monkeypatch,
    ):
        """The adapter must not open any output/k6_mtf/** path.

        We sentinel-protect builtins.open under the monkeypatch so any
        attempt to read a path containing ``output/k6_mtf`` raises
        immediately. Both input loading and per-fold evaluation happen
        under the guard, so the test exercises every code path that
        could conceivably touch the live K=6 MTF output tree.
        """
        roots = _build_synthetic_universe(tmp_path, n_bars=40)

        real_open = open
        opened: List[str] = []

        def _guarded_open(file, *args, **kwargs):
            spath = str(file).replace("\\", "/")
            opened.append(spath)
            if "output/k6_mtf" in spath:
                raise AssertionError(
                    f"adapter attempted to open output/k6_mtf path: "
                    f"{spath}"
                )
            return real_open(file, *args, **kwargs)

        import builtins
        monkeypatch.setattr(builtins, "open", _guarded_open)

        inputs = build_adapter_inputs(
            LAUNCH_FAMILY,
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=LAUNCH_FAMILY, secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=40)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        candidates = adapter.select_for_fold(fold)
        for c in candidates:
            adapter.evaluate_candidate(c, fold)
        adapter.baseline_for_fold(fold)

        # Sanity: at least one synthetic upstream input was opened.
        assert any(
            "signal_library" in p or "stackbuilder" in p
            for p in opened
        )

    def test_current_snapshot_derived_at_train_end_not_artifact(
        self, tmp_path,
    ):
        roots = _build_synthetic_universe(tmp_path, n_bars=40)
        inputs = build_adapter_inputs(
            LAUNCH_FAMILY,
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=40)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        candidates = adapter.select_for_fold(fold)
        aapl = candidates[0]
        # The synthesized snapshot must reflect data at or before
        # train_end. The synthetic universe uses always_buy=True so
        # every 1d slot is BUY.
        snap = aapl.app_payload["current_snapshot"]
        assert snap is not None
        # 1d is exact-date matched at train_end = idx[19].
        assert snap["1d"] in {"BUY", "NONE", "UNAVAILABLE"}

    def test_oos_window_uses_only_data_through_evaluation_cutoff(
        self, tmp_path,
    ):
        roots = _build_synthetic_universe(tmp_path, n_bars=40)
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        aapl_inputs = inputs["AAPL"]
        idx = pd.bdate_range("2020-01-02", periods=40)
        eval_cutoff = idx[29]
        # Synthesize OOS bars in window (idx[20], idx[29]] (= test_start
        # through eval_cutoff). Every synthesized bar must have
        # bar_date <= eval_cutoff. Per-target forward-fill is verified
        # by the production helper which uses searchsorted(side="right")
        # - 1 so source_date <= target_date <= eval_cutoff.
        bars = _synthesize_candidate_snapshots_in_window(
            aapl_inputs.stack,
            aapl_inputs.member_libs_by_tf,
            aapl_inputs.secondary_close,
            window_start=idx[20], window_end=idx[29],
            evaluation_cutoff=eval_cutoff,
        )
        assert all(bar["bar_date"] <= eval_cutoff for bar in bars)
        # No bar later than eval_cutoff.
        assert not any(bar["bar_date"] > eval_cutoff for bar in bars)


class TestEvaluateCandidateMechanics:
    def test_daily_capture_and_trigger_mask_aligned(self, tmp_path):
        roots = _build_synthetic_universe(tmp_path, n_bars=40)
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=40)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        cands = adapter.select_for_fold(fold)
        result = adapter.evaluate_candidate(cands[0], fold)
        # With always_buy=True the K=6 unanimity combine yields BUY
        # on every bar so every bar matches and every match is a BUY
        # trade -> trigger_mask all True; daily_capture has float
        # dtype and matches the trigger_mask index.
        assert len(result.daily_capture) == len(result.trigger_mask)
        assert list(result.trigger_mask.index) == list(
            result.daily_capture.index,
        )
        assert result.daily_capture.dtype.kind == "f"
        meta = result.metadata
        assert meta["match_count"] >= 1
        assert meta["capture_count"] >= 1
        assert meta["trade_count"] == meta["capture_count"]
        assert meta["secondary"] == "AAPL"

    def test_missing_input_candidate_returns_empty_strategy_fold_result(
        self, tmp_path,
    ):
        roots = _build_synthetic_universe(tmp_path, n_bars=40)
        # Remove AAPL's combo_k=6.json to simulate missing input.
        (roots["stackbuilder_root"] / "AAPL" / "selected_run"
            / "combo_k=6.json").unlink()
        inputs = build_adapter_inputs(
            LAUNCH_FAMILY,
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=LAUNCH_FAMILY, secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=40)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        cands = adapter.select_for_fold(fold)
        # n_strategies_tested must reflect the family size (8 candidates
        # selected by select_for_fold).
        assert len(cands) == len(LAUNCH_FAMILY)
        aapl = next(c for c in cands if "AAPL" in c.strategy_id)
        assert aapl.app_payload["input_available"] is False
        result = adapter.evaluate_candidate(aapl, fold)
        assert len(result.daily_capture) == 0
        assert len(result.trigger_mask) == 0
        assert any(
            f"[{K6_MTF_REASON_PREFIX}:" in iss
            for iss in result.issues
        )


class TestBaselineCoherence:
    """Post-Codex-audit baseline contract.

    validation_engine v1 calls adapter.baseline_for_fold once per fold
    and applies that single BaselineFoldMetrics to every per-strategy
    per_fold_baseline_delta entry. For the K=6 MTF launch family
    (multiple secondaries per fold), the adapter's baseline_for_fold
    MUST return an empty BaselineFoldMetrics so the engine does not
    deliver misleading blended baseline deltas. The actual
    same-secondary buy-and-hold metrics live on
    StrategyFoldResult.metadata['same_secondary_baseline'] per
    (strategy, fold). This class pins both halves of that contract.
    """

    def test_baseline_for_fold_is_deliberately_empty(self, tmp_path):
        roots = _build_synthetic_universe(tmp_path, n_bars=60)
        inputs = build_adapter_inputs(
            ("AAPL", "AMZN"),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL", "AMZN"), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=60)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        bm = adapter.baseline_for_fold(fold)
        assert bm.fold_index == 0
        assert bm.n_observations == 0
        assert bm.baseline_sharpe is None
        assert bm.baseline_total_return is None
        assert bm.baseline_mean_return is None
        assert bm.baseline_std is None
        # Issue carries the bracketed [K6MTF:validation_baseline_unavailable]
        # prefix and points downstream readers at the metadata path.
        assert any(
            f"[{K6_MTF_REASON_PREFIX}:validation_baseline_unavailable]"
            in iss
            for iss in bm.issues
        )
        assert any(
            "StrategyFoldResult.metadata" in iss
            for iss in bm.issues
        )

    def test_per_strategy_baseline_lives_in_metadata(self, tmp_path):
        roots = _build_synthetic_universe(tmp_path, n_bars=60)
        inputs = build_adapter_inputs(
            ("AAPL", "AMZN"),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL", "AMZN"), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=60)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        cands = adapter.select_for_fold(fold)
        results_by_sec = {
            c.app_payload["secondary"]: adapter.evaluate_candidate(c, fold)
            for c in cands
        }
        for sec, result in results_by_sec.items():
            ss = result.metadata.get("same_secondary_baseline")
            assert ss is not None, sec
            assert isinstance(ss, dict)
            # Same shape on every return path (success + empty fold).
            for k in (
                "n_observations",
                "baseline_sharpe",
                "baseline_total_return",
                "baseline_mean_return",
                "baseline_std",
                "issues",
            ):
                assert k in ss, f"{sec} missing {k}"
        # Synthetic universe always-available secondaries -> finite
        # baseline_sharpe and baseline_total_return on at least one
        # per-(strategy, fold) baseline.
        finite = [
            results_by_sec[sec].metadata["same_secondary_baseline"]
            for sec in ("AAPL", "AMZN")
            if results_by_sec[sec].metadata["same_secondary_baseline"][
                "n_observations"
            ] > 0
        ]
        assert finite
        assert any(
            ss["baseline_total_return"] is not None for ss in finite
        )

    def test_per_secondary_baselines_differ_across_secondaries(
        self, tmp_path,
    ):
        """Distinct synthetic close paths must produce distinct
        per-secondary baselines (sanity check that we are NOT blending).
        """
        roots = _build_synthetic_universe(tmp_path, n_bars=80, seed=42)
        inputs = build_adapter_inputs(
            ("AAPL", "AMZN", "GOOGL"),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL", "AMZN", "GOOGL"), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=80)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[39],
            test_start=idx[40], test_end=idx[59],
        )
        cands = adapter.select_for_fold(fold)
        totals = []
        for c in cands:
            result = adapter.evaluate_candidate(c, fold)
            ss = result.metadata["same_secondary_baseline"]
            totals.append(ss["baseline_total_return"])
        finite_totals = [t for t in totals if t is not None]
        # All three secondaries have distinct synthetic prices so their
        # per-secondary buy-and-hold totals should not all be equal.
        assert len(finite_totals) >= 2
        assert len(set(round(t, 6) for t in finite_totals)) > 1


class TestEndToEndContract:
    def test_run_validation_emits_valid_sidecar(self, tmp_path):
        roots = _build_synthetic_universe(tmp_path, n_bars=120)
        inputs = build_adapter_inputs(
            ("AAPL", "AMZN", "GOOGL"),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL", "AMZN", "GOOGL"),
            secondary_inputs=inputs,
        )
        out_root = tmp_path / "validation_out"
        out_root.mkdir()
        result = run_validation(
            adapter,
            run_id="test-run-1",
            output_dir=out_root / "test-run-1",
            initial_train_days=30,
            test_window_days=20,
            step_days=20,
            n_permutations=0,
            n_bootstrap_samples=0,
        )
        sidecar_path = Path(result["sidecar_path"])
        assert sidecar_path.exists()
        assert sidecar_path.name == "validation.json"

        contract = result["contract"]
        assert contract["producer_engine"] == "k6_mtf"
        assert contract["app_surface"] == "run_directory"
        assert contract["validation_contract_version"] == "v1"
        validate_validation_contract_v1(contract)

        # Family size reflected in n_strategies_tested.
        assert contract["n_strategies_tested"] == 3

    def test_synthetic_sidecar_passes_contract_validation(self, tmp_path):
        """Direct shape assertion: a freshly generated K=6 MTF sidecar
        passes validate_validation_contract_v1.
        """
        roots = _build_synthetic_universe(tmp_path, n_bars=80)
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        result = run_validation(
            adapter,
            run_id="test-shape-1",
            output_dir=tmp_path / "out" / "test-shape-1",
            initial_train_days=20,
            test_window_days=20,
            step_days=20,
            n_permutations=0,
            n_bootstrap_samples=0,
        )
        validate_validation_contract_v1(result["contract"])


class TestSidecarDiscovery:
    def test_honest_validation_ledger_can_discover_and_load(self, tmp_path):
        roots = _build_synthetic_universe(tmp_path, n_bars=80)
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        out_root = tmp_path / "validation_root"
        out_root.mkdir()
        result = run_validation(
            adapter,
            run_id="test-ledger-1",
            output_dir=out_root / "test-ledger-1",
            initial_train_days=20,
            test_window_days=20,
            step_days=20,
            n_permutations=0,
            n_bootstrap_samples=0,
        )
        discovered = hvl.discover_validation_sidecars(out_root)
        assert len(discovered) == 1
        assert discovered[0].name == "validation.json"
        loaded = hvl.load_validation_sidecar(discovered[0])
        assert loaded["producer_engine"] == "k6_mtf"

    def test_controlled_compute_default_glob_finds_sidecar(self, tmp_path):
        # controlled_compute._resolve_sidecar_glob default = "**/validation.json".
        # Confirm rglob finds the K=6 MTF sidecar under any nested
        # <run_id>/validation.json layout the adapter produces.
        roots = _build_synthetic_universe(tmp_path, n_bars=80)
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        out_root = tmp_path / "cc_root"
        out_root.mkdir()
        result = run_validation(
            adapter,
            run_id="test-cc-1",
            output_dir=out_root / "test-cc-1",
            initial_train_days=20,
            test_window_days=20,
            step_days=20,
            n_permutations=0,
            n_bootstrap_samples=0,
        )
        matches = list(out_root.rglob("validation.json"))
        assert len(matches) == 1
        assert matches[0] == Path(result["sidecar_path"])


class TestPersistedSameSecondaryBaseline:
    """The locked 5C-1 Section 6 same-secondary buy-and-hold contract
    MUST land on disk in the K=6 MTF sidecar, not just on the
    transient ``StrategyFoldResult.metadata``. ``validation_engine``
    v1 does not serialize arbitrary fold metadata into the contract;
    the adapter's ``run_validation()`` post-processes the contract
    dict and injects ``same_secondary_baseline`` into every
    ``strategies[].per_fold_metrics[]`` entry BEFORE
    ``write_validation_sidecar`` writes the JSON.

    These tests pin:

    1. The in-memory contract returned by ``run_validation()`` carries
       ``same_secondary_baseline`` on every per-fold metric for every
       K=6 MTF strategy, with the stable six-key schema.
    2. The on-disk JSON sidecar contains the same per-fold sub-object
       (proving it actually persisted, not just lived on transient
       metadata).
    3. Engine-level ``per_fold_baseline_delta`` entries remain
       ``sharpe_delta=None`` / ``return_delta=None`` for K=6 MTF
       rows, so the engine never delivers a misleading blended
       baseline.
    4. Distinct synthetic-close per-secondary baselines produce
       distinct ``baseline_total_return`` values in the persisted
       contract (proves the data is NOT blended on disk).
    """

    _BASELINE_KEYS = (
        "n_observations",
        "baseline_sharpe",
        "baseline_total_return",
        "baseline_mean_return",
        "baseline_std",
        "issues",
    )

    def _build_run(self, tmp_path, *, n_bars=120, secondaries=(
        "AAPL", "AMZN", "GOOGL",
    )):
        roots = _build_synthetic_universe(tmp_path, n_bars=n_bars, seed=11)
        inputs = build_adapter_inputs(
            secondaries,
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=secondaries, secondary_inputs=inputs,
        )
        result = run_validation(
            adapter,
            run_id="test-persisted-baseline",
            output_dir=tmp_path / "vout" / "test-persisted-baseline",
            initial_train_days=30,
            test_window_days=20,
            step_days=20,
            n_permutations=0,
            n_bootstrap_samples=0,
        )
        return adapter, result

    def test_returned_contract_carries_per_fold_baseline(self, tmp_path):
        _adapter, result = self._build_run(tmp_path)
        contract = result["contract"]
        assert contract["producer_engine"] == "k6_mtf"
        strategies = contract["strategies"]
        assert strategies, "no strategies in contract"
        for strat in strategies:
            per_fold = strat["per_fold_metrics"]
            assert per_fold, f"strategy {strat['strategy_id']} has 0 folds"
            for entry in per_fold:
                ss = entry.get("same_secondary_baseline")
                assert ss is not None, (
                    f"strategy {strat['strategy_id']} fold "
                    f"{entry.get('fold_index')} missing "
                    f"same_secondary_baseline"
                )
                for k in self._BASELINE_KEYS:
                    assert k in ss, (
                        f"strategy {strat['strategy_id']} fold "
                        f"{entry['fold_index']} missing key {k!r}"
                    )

    def test_persisted_sidecar_carries_per_fold_baseline(self, tmp_path):
        _adapter, result = self._build_run(tmp_path)
        sidecar_path = Path(result["sidecar_path"])
        assert sidecar_path.exists()
        on_disk = json.loads(sidecar_path.read_text(encoding="utf-8"))
        strategies = on_disk["strategies"]
        assert strategies
        for strat in strategies:
            per_fold = strat["per_fold_metrics"]
            assert per_fold
            for entry in per_fold:
                ss = entry.get("same_secondary_baseline")
                assert ss is not None, (
                    f"strategy {strat['strategy_id']} fold "
                    f"{entry.get('fold_index')} missing "
                    f"same_secondary_baseline in PERSISTED sidecar"
                )
                for k in self._BASELINE_KEYS:
                    assert k in ss, (
                        f"persisted strategy {strat['strategy_id']} fold "
                        f"{entry['fold_index']} missing key {k!r}"
                    )
                # Stable schema on disk: n_observations is int and
                # issues is a list (possibly empty).
                assert isinstance(ss["n_observations"], int)
                assert isinstance(ss["issues"], list)

    def test_persisted_sidecar_passes_engine_contract_validation(
        self, tmp_path,
    ):
        _adapter, result = self._build_run(tmp_path)
        on_disk = json.loads(
            Path(result["sidecar_path"]).read_text(encoding="utf-8"),
        )
        validate_validation_contract_v1(on_disk)

    def test_engine_per_fold_baseline_delta_remains_null(self, tmp_path):
        _adapter, result = self._build_run(tmp_path)
        contract = result["contract"]
        on_disk = json.loads(
            Path(result["sidecar_path"]).read_text(encoding="utf-8"),
        )
        for source_label, source in (
            ("in-memory contract", contract),
            ("on-disk sidecar", on_disk),
        ):
            for strat in source["strategies"]:
                deltas = strat.get("per_fold_baseline_delta")
                assert deltas is not None
                assert deltas, (
                    f"{source_label}: strategy "
                    f"{strat['strategy_id']} has empty "
                    f"per_fold_baseline_delta"
                )
                for d in deltas:
                    assert d["sharpe_delta"] is None, (
                        f"{source_label}: strategy "
                        f"{strat['strategy_id']} fold "
                        f"{d['fold_index']} engine sharpe_delta is "
                        f"not None (blended baseline regression)"
                    )
                    assert d["return_delta"] is None, (
                        f"{source_label}: strategy "
                        f"{strat['strategy_id']} fold "
                        f"{d['fold_index']} engine return_delta is "
                        f"not None (blended baseline regression)"
                    )

    def test_persisted_baselines_differ_across_secondaries(self, tmp_path):
        _adapter, result = self._build_run(
            tmp_path, n_bars=160,
            secondaries=("AAPL", "AMZN", "GOOGL", "META"),
        )
        on_disk = json.loads(
            Path(result["sidecar_path"]).read_text(encoding="utf-8"),
        )
        by_sec_fold0_total: Dict[str, Optional[float]] = {}
        for strat in on_disk["strategies"]:
            sid = strat["strategy_id"]
            # strategy_id format: "k6_mtf:<SEC>".
            sec = sid.split(":", 1)[1]
            for entry in strat["per_fold_metrics"]:
                if entry.get("fold_index") == 0:
                    by_sec_fold0_total[sec] = (
                        entry["same_secondary_baseline"][
                            "baseline_total_return"
                        ]
                    )
                    break
        finite = [
            v for v in by_sec_fold0_total.values() if v is not None
        ]
        assert len(finite) >= 2, (
            "expected at least two secondaries with finite fold-0 "
            f"baselines, got {by_sec_fold0_total!r}"
        )
        rounded = {round(v, 6) for v in finite}
        assert len(rounded) > 1, (
            f"persisted fold-0 baselines look blended: {finite!r}"
        )

    def test_baseline_for_fold_remains_deliberately_empty(self, tmp_path):
        adapter, _result = self._build_run(tmp_path)
        idx = pd.bdate_range("2020-01-02", periods=80)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[19],
            test_start=idx[20], test_end=idx[39],
        )
        bm = adapter.baseline_for_fold(fold)
        assert bm.n_observations == 0
        assert bm.baseline_sharpe is None
        assert bm.baseline_total_return is None
        assert bm.baseline_mean_return is None
        assert bm.baseline_std is None
        assert any(
            f"[{K6_MTF_REASON_PREFIX}:validation_baseline_unavailable]"
            in iss
            for iss in bm.issues
        )


class TestCliInvocationPathProof:
    """End-to-end CLI exercise that proves the sidecar lands at the
    intended discovery root with no ``project/project`` doubling
    when the adapter is invoked the way ``controlled_compute`` would
    invoke it (no explicit ``--output-dir``, working under a
    simulated ``<REPO_ROOT>/project`` cwd).

    The test monkeypatches the adapter module's
    ``_PROJECT_DIR_DEFAULT`` so ``resolve_validation_output_base``
    resolves to a tmp_path-based synthetic ``<REPO_ROOT>/project``
    instead of the real repo. It then invokes ``adapter.main(argv)``
    (the same callable the ``python -m utils.k6_mtf_validation.adapter``
    entry point uses) and asserts:

    1. ``main`` returns 0 and prints a stdout JSON envelope.
    2. The sidecar lands at exactly
       ``<simulated repo>/project/output/validation/<run_id>/validation.json``
       -- no ``project/project`` doubling.
    3. ``rglob("validation.json")`` discovers the same path.
    4. ``honest_validation_ledger.load_validation_sidecar`` round-trips
       the contract.
    5. Re-checking the resolver under the simulated layout confirms
       the resolved base lives strictly under the simulated REPO_ROOT
       and has exactly one ``project`` segment.

    No real ``output/`` directory under the actual repo is touched.
    No real ``controlled_compute`` invocation happens.
    """

    def test_main_cli_writes_to_resolved_base_with_no_project_doubling(
        self, tmp_path, monkeypatch, capsys,
    ):
        # 1. Synthetic <REPO_ROOT>/project layout under tmp_path.
        sim_repo_root = tmp_path / "sim_repo"
        sim_project_dir = sim_repo_root / "project"
        sim_project_dir.mkdir(parents=True)
        # The synthetic universe lives anywhere under sim_project_dir
        # so the adapter inputs can be loaded from real on-disk pickles.
        roots = _build_synthetic_universe(sim_project_dir, n_bars=80)

        # 2. Point the adapter resolver at the synthetic repo. The
        # resolver derives repo_root from project_dir.parent, so only
        # _PROJECT_DIR_DEFAULT needs swapping.
        monkeypatch.setattr(
            adapter_mod, "_PROJECT_DIR_DEFAULT", sim_project_dir,
        )

        # Sanity: resolver lands under the simulated repo, exactly one
        # "project" segment, no doubling.
        resolved_base = adapter_mod.resolve_validation_output_base()
        assert resolved_base == (
            sim_project_dir / "output" / "validation"
        ).resolve()
        # Parts of the resolved path relative to sim_repo_root must
        # contain exactly one "project" segment.
        rel_parts = resolved_base.relative_to(
            sim_repo_root.resolve(),
        ).parts
        assert rel_parts == ("project", "output", "validation")

        # 3. Invoke adapter.main(argv) the same way an operator-supervised
        # job-spec command would, with no --output-dir (so the resolver
        # owns sidecar placement).
        run_id = "test-cli-pathproof"
        rc = adapter_mod.main([
            "--secondaries", "AAPL",
            "--stackbuilder-root", str(roots["stackbuilder_root"]),
            "--signal-library-dir", str(roots["stable_dir"]),
            "--price-cache-dir", str(roots["price_cache_dir"]),
            "--cache-dir", str(roots["cache_dir"]),
            "--run-id", run_id,
            "--initial-train-days", "20",
            "--test-window-days", "20",
            "--step-days", "20",
            "--n-permutations", "0",
            "--n-bootstrap-samples", "0",
        ])
        assert rc == 0

        # 4. main prints a JSON envelope; parse the sidecar_path back out.
        captured = capsys.readouterr()
        envelope = json.loads(captured.out)
        sidecar_path = Path(envelope["sidecar_path"])
        assert sidecar_path.exists()
        assert sidecar_path.name == "validation.json"

        # 5. The sidecar landed at exactly
        # <sim_repo>/project/output/validation/<run_id>/validation.json.
        expected = (
            sim_project_dir / "output" / "validation" / run_id
            / "validation.json"
        ).resolve()
        assert sidecar_path == expected

        # 6. No project/project doubling anywhere in the resolved path.
        path_str = str(sidecar_path).replace("\\", "/")
        assert "project/project" not in path_str

        # 7. controlled_compute-style discovery: rglob from the
        # validation base finds exactly this sidecar.
        discovery_root = resolved_base
        matches = list(discovery_root.rglob("validation.json"))
        assert matches == [expected]

        # 8. honest_validation_ledger round-trip.
        loaded = hvl.load_validation_sidecar(expected)
        assert loaded["producer_engine"] == "k6_mtf"
        assert loaded["app_surface"] == "run_directory"
        assert loaded["run_id"] == run_id

        # 9. The real repo's output/validation must NOT have grown a
        # sidecar named test-cli-pathproof from this test.
        real_output_validation = (
            PROJECT_DIR / "output" / "validation" / run_id
        )
        assert not real_output_validation.exists()


class TestEmpiricalMetadata:
    """Direction-preserving empirical-layer metadata.

    The adapter MUST populate StrategyFoldResult.metadata with two
    pd.Series that validation_engine._permutation_p_value consumes
    in its direction-preserving mode:

    - signal_state: Buy / Short / None tokens over the FULL eligible
      OOS bar population (matched + non-matched), indexed by bar date.
    - permutation_return_pool: raw unsigned (next_close/cur_close-1)
      percent returns over the same eligible population. No synthetic
      0.0 for no-trade bars. No NaN. No signed captures.

    The engine then counts literal "Buy" / "Short" tokens, samples
    n_buy + n_short raw returns without replacement from the pool,
    and assigns +pool[i] / -pool[j] signs itself. The pool MUST span
    the eligible-bar population (typically larger than n_buy +
    n_short) so the without-replacement sample succeeds and the null
    distribution is the eligible-bar return distribution -- NOT a
    trigger-only distribution that would collapse to boundary
    behavior.

    Mirrors the StackBuilder precedent at
    ``stackbuilder.py:3608-3614``.
    """

    @staticmethod
    def _build_mixed_match_fixture(tmp_path, n_bars=80):
        """Build a fixture with a controlled mix of matched and
        non-matched OOS bars.

        Signal program:
            bar 0..29 (in-sample): all BUY -> current_snapshot=BUY.
            bar 30..79 (OOS): alternating BUY, SHORT, BUY, SHORT, ...
                              with the BUY bars matching the
                              current_snapshot (BUY) and the SHORT
                              bars NOT matching (current BUY
                              wildcards allow BUY/NONE/UNAVAILABLE
                              only -- SHORT contradicts).

        Close path: deterministic increasing.
        """
        signals = [SIGNAL_BUY] * 30 + [
            (SIGNAL_BUY if i % 2 == 0 else SIGNAL_SHORT)
            for i in range(n_bars - 30)
        ]
        assert signals[-1] == SIGNAL_BUY or signals[-1] == SIGNAL_SHORT
        # Close path: increasing, so BUY raw returns are positive and
        # SHORT raw returns are also positive (the SHORT direction
        # flips sign in daily_capture but raw return remains positive).
        closes = [100.0 + 0.1 * i for i in range(n_bars)]
        roots = _build_universe_with_signal_program(
            tmp_path,
            secondary="AAPL",
            n_bars=n_bars,
            member_signals=signals,
            closes=closes,
        )
        return roots, signals, closes

    def _evaluate_one_fold(self, roots, *, n_bars):
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=n_bars)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[29],
            test_start=idx[30], test_end=idx[n_bars - 1],
        )
        cands = adapter.select_for_fold(fold)
        result = adapter.evaluate_candidate(cands[0], fold)
        return adapter, fold, result

    def test_metadata_emits_signal_state_and_pool(self, tmp_path):
        roots, _signals, _closes = self._build_mixed_match_fixture(tmp_path)
        _adapter, _fold, result = self._evaluate_one_fold(roots, n_bars=80)
        assert "signal_state" in result.metadata
        assert "permutation_return_pool" in result.metadata
        ss = result.metadata["signal_state"]
        pool = result.metadata["permutation_return_pool"]
        assert isinstance(ss, pd.Series)
        assert isinstance(pool, pd.Series)
        assert pool.dtype.kind == "f"

    def test_metadata_shares_index(self, tmp_path):
        roots, _signals, _closes = self._build_mixed_match_fixture(tmp_path)
        _adapter, _fold, result = self._evaluate_one_fold(roots, n_bars=80)
        ss = result.metadata["signal_state"]
        pool = result.metadata["permutation_return_pool"]
        assert list(ss.index) == list(pool.index)
        assert len(ss) == len(pool)

    def test_signal_state_tokens_constrained(self, tmp_path):
        roots, _signals, _closes = self._build_mixed_match_fixture(tmp_path)
        _adapter, _fold, result = self._evaluate_one_fold(roots, n_bars=80)
        ss = result.metadata["signal_state"]
        token_set = set(ss.unique())
        assert token_set.issubset({"Buy", "Short", "None"})

    def test_pool_is_full_eligible_population(self, tmp_path):
        """The pool MUST be larger than the trigger count (otherwise
        the engine cannot sample without replacement). The fixture is
        designed so the OOS window has 50 bars but only ~half are
        matched (the BUY half); the pool spans the FULL eligible
        population including non-matched SHORT bars.
        """
        roots, _signals, _closes = self._build_mixed_match_fixture(tmp_path)
        _adapter, _fold, result = self._evaluate_one_fold(roots, n_bars=80)
        ss = result.metadata["signal_state"]
        pool = result.metadata["permutation_return_pool"]
        trigger_mask = result.trigger_mask
        n_triggers = int(trigger_mask.sum())
        n_buy = int((ss == "Buy").sum())
        n_short = int((ss == "Short").sum())
        # n_buy + n_short must equal n_triggers (trigger_mask is true
        # only on Buy/Short captured rows).
        assert n_buy + n_short == n_triggers
        # The pool must strictly exceed the trigger count: the fixture
        # has at least some non-matched bars that contribute to the
        # pool with signal_state="None".
        assert len(pool) > n_triggers
        # Sanity: alternating-Buy/Short OOS window -> roughly half the
        # OOS bars are matched (Buy) and half are not (Short).
        assert n_buy >= 5
        assert (ss == "None").sum() >= 5

    def test_pool_carries_real_raw_returns_not_zero(self, tmp_path):
        """No-trade and non-trigger eligible bars carry the REAL raw
        next-close return, NOT synthetic 0.0 and NOT NaN.
        """
        roots, _signals, closes = self._build_mixed_match_fixture(tmp_path)
        _adapter, _fold, result = self._evaluate_one_fold(roots, n_bars=80)
        ss = result.metadata["signal_state"]
        pool = result.metadata["permutation_return_pool"]
        # Synthetic closes are strictly increasing by +0.1 per bar so
        # every raw return is strictly positive and finite.
        assert pool.notna().all()
        assert (pool > 0).all()
        # The first non-matched OOS bar (signal_state=="None") in the
        # series should be the bar at position 31 in the OOS window
        # (alternating starts at OOS bar 0 = train_end+1 = idx[30] =
        # BUY, then idx[31] = SHORT -> non-matched). Pull the raw
        # return at that date and compare against the close ratio.
        none_indices = ss[ss == "None"].index
        assert len(none_indices) >= 1
        first_none_date = none_indices[0]
        pool_val = float(pool.loc[first_none_date])
        # Raw next-close return is (closes[pos+1]/closes[pos] - 1)*100
        # for whichever positional row matches first_none_date in the
        # capped close series.
        full_dates = pd.bdate_range("2020-01-02", periods=80)
        pos = list(full_dates).index(first_none_date)
        expected = (
            (closes[pos + 1] / closes[pos] - 1.0) * 100.0
        )
        assert abs(pool_val - expected) < 1e-9

    def test_buy_short_capture_signs_unchanged(self, tmp_path):
        """daily_capture sign semantics must NOT change. Buy captured
        rows = +raw_return_pct; Short captured rows would be
        -raw_return_pct (the fixture's all-D protocol stack with
        increasing closes only produces Buy directional trades, but
        the predicate that Buy capture == +raw_return is verifiable).
        """
        roots, _signals, closes = self._build_mixed_match_fixture(tmp_path)
        _adapter, _fold, result = self._evaluate_one_fold(roots, n_bars=80)
        dc = result.daily_capture
        tm = result.trigger_mask
        pool = result.metadata["permutation_return_pool"]
        ss = result.metadata["signal_state"]
        # Trigger rows -> Buy in this fixture (no Short triggers because
        # current_snapshot=Buy excludes Short candidates via match rule).
        triggered = dc.index[tm]
        assert len(triggered) >= 1
        for d in triggered[:5]:
            assert ss.loc[d] == "Buy"
            # daily_capture at a Buy row equals the raw return: +raw.
            assert abs(dc.loc[d] - pool.loc[d]) < 1e-9

    def test_no_lookahead_pool_entry_within_cutoff(self, tmp_path):
        """No pool entry uses a next-close beyond context.evaluation
        _cutoff. The fixture caps secondary close at idx[79]; pool
        rows can only be drawn for bars with valid next close inside
        the cap, so the last possible pool date is idx[78].
        """
        roots, _signals, _closes = self._build_mixed_match_fixture(tmp_path)
        _adapter, fold, result = self._evaluate_one_fold(roots, n_bars=80)
        pool = result.metadata["permutation_return_pool"]
        # eval_cutoff = test_end = idx[79]. The last bar with a valid
        # NEXT close inside the cutoff is idx[78]. So pool dates must
        # all be <= idx[78].
        last_idx = pd.Timestamp(pd.bdate_range("2020-01-02", periods=80)[78])
        assert pool.index.max() <= last_idx
        # No bar past eval_cutoff is included.
        assert pool.index.max() <= pd.Timestamp(fold.evaluation_cutoff)

    def test_no_output_k6_mtf_open_during_evaluate(
        self, tmp_path, monkeypatch,
    ):
        """The new metadata code path must NOT introduce any
        output/k6_mtf/** read. Reuses the sentinel monkeypatch.
        """
        roots, _signals, _closes = self._build_mixed_match_fixture(tmp_path)

        real_open = open
        opened: List[str] = []

        def _guarded_open(file, *args, **kwargs):
            spath = str(file).replace("\\", "/")
            opened.append(spath)
            if "output/k6_mtf" in spath:
                raise AssertionError(
                    f"adapter opened output/k6_mtf path: {spath}"
                )
            return real_open(file, *args, **kwargs)

        import builtins
        monkeypatch.setattr(builtins, "open", _guarded_open)

        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=80)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[29],
            test_start=idx[30], test_end=idx[79],
        )
        for c in adapter.select_for_fold(fold):
            adapter.evaluate_candidate(c, fold)

    def test_engine_permutation_consumes_metadata(self, tmp_path):
        """Drive validation_engine._permutation_p_value with the
        adapter-emitted metadata directly and assert a non-degenerate
        empirical p-value strictly inside (1/(n_perm+1), 1.0).

        A trigger-only pool would collapse this test to boundary
        behavior (the only feasible sample equals the observed set
        so the p-value tends to 1.0 or NaN). Passing a non-boundary
        p-value proves the pool is the full eligible population.
        """
        roots, _signals, _closes = self._build_mixed_match_fixture(
            tmp_path, n_bars=120,
        )
        _adapter, _fold, result = self._evaluate_one_fold(roots, n_bars=120)
        from validation_engine import _permutation_p_value
        rng = np.random.default_rng(20260601)
        n_perm = 200
        p_val = _permutation_p_value(
            result.daily_capture, result.trigger_mask,
            n_permutations=n_perm,
            rng=rng,
            signal_state=result.metadata["signal_state"],
            permutation_return_pool=result.metadata["permutation_return_pool"],
        )
        assert p_val is not None, "p-value None: metadata not consumed"
        lower_bound = 1.0 / (n_perm + 1)
        # Non-degenerate band: strictly inside (lower_bound, 1.0).
        assert p_val > lower_bound
        assert p_val < 1.0

    def test_run_validation_no_missing_metadata_failure(self, tmp_path):
        """End-to-end run_validation on a synthetic fixture: the
        previously-observed validation_empirical_failed issue with
        "empirical permutation requires signal_state +
        permutation_return_pool" wording MUST NOT appear in the
        contract issues. (Other empirical statuses such as legitimate
        p-value > alpha or empirical_validated are acceptable; the
        assertion targets the missing-metadata wording only.)
        """
        roots, _signals, _closes = self._build_mixed_match_fixture(
            tmp_path, n_bars=160,
        )
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        result = run_validation(
            adapter,
            run_id="test-empirical-metadata",
            output_dir=tmp_path / "vout" / "test-empirical-metadata",
            initial_train_days=60,
            test_window_days=40,
            step_days=40,
            n_permutations=200,
            n_bootstrap_samples=200,
            rng_seed=20260601,
        )
        contract = result["contract"]
        missing_wording = "empirical permutation requires"
        offending = [
            iss for iss in contract.get("issues", [])
            if missing_wording in iss
        ]
        assert not offending, (
            f"missing-metadata empirical_failed wording still present: "
            f"{offending}"
        )
        # Sidecar contract still validates.
        validate_validation_contract_v1(contract)
        # same_secondary_baseline still persisted.
        for strat in contract["strategies"]:
            for entry in strat["per_fold_metrics"]:
                assert "same_secondary_baseline" in entry
        # Engine-level per_fold_baseline_delta still null for K=6 MTF.
        for strat in contract["strategies"]:
            for d in strat["per_fold_baseline_delta"]:
                assert d["sharpe_delta"] is None
                assert d["return_delta"] is None


class TestEmpiricalMetadataMatchedShort:
    """Matched Short raw-return semantics.

    The prior TestEmpiricalMetadata.test_buy_short_capture_signs
    _unchanged only exercises matched Buy bars (the all-Buy current
    _snapshot fixture cannot produce matched Short candidates). This
    class adds a fixture where current_snapshot is all-SHORT and the
    OOS window contains matched SHORT bars, plus non-matched BUY
    bars that contribute to the pool. The fixture pins that:

    - signal_state.loc[matched_short_bar] == "Short".
    - trigger_mask.loc[matched_short_bar] is True.
    - daily_capture.loc[matched_short_bar] == -raw_return_pct.
    - permutation_return_pool.loc[matched_short_bar] == +raw_return_pct.
      (i.e., the pool is unsigned raw returns, NOT signed captures.)
    - The pool also contains None-token rows from non-matched bars.
    """

    @staticmethod
    def _build_short_current_fixture(tmp_path, n_bars=80):
        # In-sample bars 0..29: all SHORT -> current_snapshot all SHORT.
        # OOS bars 30..n-1: alternating SHORT (matched) and BUY (non-
        # matched against the all-SHORT current_snapshot).
        signals = [SIGNAL_SHORT] * 30 + [
            (SIGNAL_SHORT if i % 2 == 0 else SIGNAL_BUY)
            for i in range(n_bars - 30)
        ]
        # Deterministic increasing closes -> raw returns strictly
        # positive on every bar.
        closes = [100.0 + 0.1 * i for i in range(n_bars)]
        roots = _build_universe_with_signal_program(
            tmp_path,
            secondary="AAPL",
            n_bars=n_bars,
            member_signals=signals,
            closes=closes,
        )
        return roots, signals, closes

    def _evaluate_one_fold(self, roots, n_bars):
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=n_bars)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[29],
            test_start=idx[30], test_end=idx[n_bars - 1],
        )
        cands = adapter.select_for_fold(fold)
        result = adapter.evaluate_candidate(cands[0], fold)
        return adapter, fold, result, idx

    def test_matched_short_raw_return_semantics(self, tmp_path):
        roots, _signals, closes = self._build_short_current_fixture(tmp_path)
        _adapter, _fold, result, idx = self._evaluate_one_fold(
            roots, n_bars=80,
        )
        ss = result.metadata["signal_state"]
        pool = result.metadata["permutation_return_pool"]
        dc = result.daily_capture
        tm = result.trigger_mask
        # At least one matched Short row must exist.
        short_rows = ss[ss == "Short"].index
        assert len(short_rows) >= 1, (
            "fixture produced no matched Short bars"
        )
        # Take the first matched Short bar and pin its semantics.
        d = short_rows[0]
        assert tm.loc[d] is True or bool(tm.loc[d]) is True
        # Reconstruct raw_return_pct positionally from closes.
        full_dates = pd.bdate_range("2020-01-02", periods=80)
        pos = list(full_dates).index(d)
        raw_return_pct = (closes[pos + 1] / closes[pos] - 1.0) * 100.0
        # daily_capture at a Short row equals -raw_return_pct.
        assert abs(dc.loc[d] - (-raw_return_pct)) < 1e-9
        # permutation_return_pool at the SAME bar equals +raw_return_pct.
        assert abs(pool.loc[d] - raw_return_pct) < 1e-9
        # Signed-capture leakage check: dc != pool at Short rows
        # (because dc is signed and pool is unsigned). With strictly
        # positive raw returns, dc < 0 and pool > 0.
        assert dc.loc[d] < 0 < pool.loc[d]
        # Full-population semantics still hold: pool length strictly
        # exceeds the trigger count (the non-matched BUY bars
        # contribute None-token pool rows).
        n_triggers = int(tm.sum())
        assert len(pool) > n_triggers
        # Some None-token rows exist (the non-matched BUY bars).
        assert (ss == "None").sum() >= 1


class TestEmpiricalPlantedEdgePairedSanity:
    """Matched-pair planted-edge vs no-edge empirical sanity.

    Two paired fixtures share an identical eligible raw-return pool
    (identical close path) and identical Buy / Short trigger counts.
    The ONLY difference is trigger placement:

    - planted-edge: Buy triggers placed on top-rank raw-return bars.
    - no-edge: Buy triggers placed on bottom-rank raw-return bars.

    Both fixtures are driven through K6MtfValidationAdapter (no
    hand-built metadata). The adapter-emitted ``signal_state`` and
    ``permutation_return_pool`` are then fed to
    ``validation_engine._permutation_p_value`` with the same
    ``rng_seed`` and same ``n_permutations`` for both cases.

    Assertions:

    - planted_p < no_edge_p (the central relative assertion).
    - both p-values are strictly inside (1 / (n_perm + 1), 1.0).
    - eligible-pool values are identical between the paired cases.
    - Buy and Short trigger counts are identical.
    """

    # Pool geometry: 17 eligible OOS bars with descending raw returns
    # from +3.0% down to -1.0%. The small pool size is deliberate:
    # validation_engine._permutation_p_value samples 15 of the 17
    # eligible bars without replacement, so there are C(17, 15) = 136
    # distinct possible samples. With n_perm = 500 the engine cycles
    # through all 136 samples multiple times, which keeps both
    # p-values strictly inside the open band (1 / (n_perm + 1), 1.0)
    # even when the planted-edge and no-edge sets sit at the ranked
    # extremes of the pool -- multiple permutations naturally land on
    # the observed subset and contribute to ge_count. A 70-bar pool
    # would put the planted observation at the literal maximum of the
    # permutation distribution and collapse p_planted to 1 / (n + 1)
    # exactly (boundary failure).
    N_IN_SAMPLE = 30
    N_OOS_ELIGIBLE = 17
    N_BARS = N_IN_SAMPLE + N_OOS_ELIGIBLE + 1  # 48
    N_BUY_TRIGGERS = 15
    # Descending raw returns from +3.0% to -1.0% across 17 OOS bars.
    OOS_RETURNS_DESCENDING = tuple(
        np.linspace(3.0, -1.0, N_OOS_ELIGIBLE)
    )

    @classmethod
    def _build_close_path(cls):
        closes = [100.0]
        # In-sample: constant +0.5% return for the 30 in-sample bars.
        for _ in range(cls.N_IN_SAMPLE):
            closes.append(closes[-1] * 1.005)
        # OOS returns in descending order; bar 100 receives the last
        # of the 70 OOS returns as its "input return" so close[100] is
        # close[99] * (1 + last_oos_return/100). Bar 100 is itself
        # not eligible (no next close inside cutoff).
        for r in cls.OOS_RETURNS_DESCENDING:
            closes.append(closes[-1] * (1.0 + r / 100.0))
        assert len(closes) == cls.N_BARS
        return closes

    @classmethod
    def _build_signal_program(cls, *, planted: bool):
        """Build the per-bar member signal program.

        In-sample: all-BUY -> current_snapshot all-BUY at train_end.
        OOS signal layout (17 eligible OOS bars with descending raw
        returns from +3.0% to -1.0%; the 18th OOS bar is the
        test_end / non-eligible trailing bar):

        - planted: Buy on OOS positions 0..14 (the 15 highest-return
          bars, ranks 1..15 of the 17-bar pool); Short on OOS
          positions 15..16 (ranks 16..17 of the pool).
        - no-edge: Short on OOS positions 0..1 (ranks 1..2 of the
          pool); Buy on OOS positions 2..16 (the 15 lowest-return
          bars, ranks 3..17 of the pool).

        Trailing test_end bar carries Buy for symmetry; it is not
        OOS-eligible (no next close inside eval_cutoff).
        """
        in_sample = [SIGNAL_BUY] * cls.N_IN_SAMPLE
        oos = [SIGNAL_SHORT] * cls.N_OOS_ELIGIBLE
        if planted:
            for i in range(0, cls.N_BUY_TRIGGERS):
                oos[i] = SIGNAL_BUY
        else:
            for i in range(
                cls.N_OOS_ELIGIBLE - cls.N_BUY_TRIGGERS,
                cls.N_OOS_ELIGIBLE,
            ):
                oos[i] = SIGNAL_BUY
        trailing = [SIGNAL_BUY]
        full = in_sample + oos + trailing
        assert len(full) == cls.N_BARS
        assert oos.count(SIGNAL_BUY) == cls.N_BUY_TRIGGERS
        return full

    def _build_paired_fixture(self, tmp_path, *, planted: bool):
        closes = self._build_close_path()
        signals = self._build_signal_program(planted=planted)
        # Use a distinct secondary ticker per fixture so the helper's
        # per-secondary stackbuilder/selected_build paths do not
        # collide when building both fixtures under sibling tmp dirs.
        secondary = "AAPL"
        roots = _build_universe_with_signal_program(
            tmp_path,
            secondary=secondary,
            n_bars=self.N_BARS,
            member_signals=signals,
            closes=closes,
        )
        return roots, signals, closes

    def _evaluate(self, roots):
        inputs = build_adapter_inputs(
            ("AAPL",),
            stackbuilder_root=str(roots["stackbuilder_root"]),
            stable_dir=str(roots["stable_dir"]),
            price_cache_dir=str(roots["price_cache_dir"]),
            cache_dir=str(roots["cache_dir"]),
        )
        adapter = K6MtfValidationAdapter(
            secondaries=("AAPL",), secondary_inputs=inputs,
        )
        idx = pd.bdate_range("2020-01-02", periods=self.N_BARS)
        fold = _build_fold(
            fold_index=0,
            train_start=idx[0], train_end=idx[self.N_IN_SAMPLE - 1],
            test_start=idx[self.N_IN_SAMPLE],
            test_end=idx[self.N_BARS - 1],
        )
        cands = adapter.select_for_fold(fold)
        result = adapter.evaluate_candidate(cands[0], fold)
        return result

    def test_paired_planted_vs_no_edge_p_values(self, tmp_path):
        planted_dir = tmp_path / "planted"
        no_edge_dir = tmp_path / "no_edge"
        planted_dir.mkdir()
        no_edge_dir.mkdir()

        planted_roots, _ps, planted_closes = self._build_paired_fixture(
            planted_dir, planted=True,
        )
        no_edge_roots, _ns, no_edge_closes = self._build_paired_fixture(
            no_edge_dir, planted=False,
        )
        # Identical close path means identical raw-return pool values.
        assert planted_closes == no_edge_closes

        planted_result = self._evaluate(planted_roots)
        no_edge_result = self._evaluate(no_edge_roots)

        planted_pool = planted_result.metadata["permutation_return_pool"]
        no_edge_pool = no_edge_result.metadata["permutation_return_pool"]
        planted_ss = planted_result.metadata["signal_state"]
        no_edge_ss = no_edge_result.metadata["signal_state"]

        # Identical pool size + identical sorted values.
        assert len(planted_pool) == len(no_edge_pool)
        assert sorted(planted_pool.to_list()) == sorted(no_edge_pool.to_list())
        # Identical trigger counts.
        p_n_buy = int((planted_ss == "Buy").sum())
        p_n_short = int((planted_ss == "Short").sum())
        n_n_buy = int((no_edge_ss == "Buy").sum())
        n_n_short = int((no_edge_ss == "Short").sum())
        assert p_n_buy == n_n_buy == self.N_BUY_TRIGGERS
        assert p_n_short == n_n_short == 0
        assert int(planted_result.trigger_mask.sum()) == self.N_BUY_TRIGGERS
        assert int(no_edge_result.trigger_mask.sum()) == self.N_BUY_TRIGGERS
        # Pool strictly exceeds trigger count.
        assert len(planted_pool) > self.N_BUY_TRIGGERS

        # Drive the engine's direction-preserving p-value path with
        # adapter-emitted metadata, identical rng_seed and
        # n_permutations for both cases.
        from validation_engine import _permutation_p_value
        n_perm = 500
        seed = 20260601

        rng_planted = np.random.default_rng(seed)
        p_planted = _permutation_p_value(
            planted_result.daily_capture,
            planted_result.trigger_mask,
            n_permutations=n_perm,
            rng=rng_planted,
            signal_state=planted_ss,
            permutation_return_pool=planted_pool,
        )
        rng_no_edge = np.random.default_rng(seed)
        p_no_edge = _permutation_p_value(
            no_edge_result.daily_capture,
            no_edge_result.trigger_mask,
            n_permutations=n_perm,
            rng=rng_no_edge,
            signal_state=no_edge_ss,
            permutation_return_pool=no_edge_pool,
        )
        assert p_planted is not None
        assert p_no_edge is not None
        lower_bound = 1.0 / (n_perm + 1)
        # Non-degeneracy: strictly inside (1/(n+1), 1.0) for both.
        assert p_planted > lower_bound, (
            f"planted p-value at lower boundary: {p_planted}"
        )
        assert p_planted < 1.0
        assert p_no_edge > lower_bound, (
            f"no-edge p-value at lower boundary: {p_no_edge}"
        )
        assert p_no_edge < 1.0
        # Central relative assertion: planted < no-edge by a clear
        # margin. The fixture pins the gap at well over 0.3 with
        # n_perm=500 and seed=20260601; require a robust 0.2 margin
        # to absorb any future engine-internal RNG-call reordering.
        margin = 0.2
        assert p_planted + margin < p_no_edge, (
            f"planted ({p_planted}) and no-edge ({p_no_edge}) "
            f"p-values did not separate by the required margin "
            f"{margin}; relative empirical sanity failed"
        )
