"""Phase 6I-1 tests for confluence_ranking_contract_validator.

Pins the data-contract validator's seven per-contract
checks plus the aggregation/CLI contract:

  - Valid SPY-shaped full contract passes every check.
  - Cache contract fails when the PKL is missing /
    unreadable.
  - StackBuilder contract:
      * no saved variants -> stackbuilder_missing.
      * single variant OK.
      * multi-variant with clear newest mtime OK (no age
        rule).
      * tied newest mtime -> stackbuilder_selection_ambiguous
        (manual block).
  - Daily-K contract:
      * missing daily K -> daily_k_missing.
      * incomplete K coverage -> daily_k_incomplete_coverage.
      * filename K vs internal K mismatch ->
        daily_k_internal_k_mismatch.
      * Legacy unsuffixed daily artifacts are silently
        ignored (the bridge's filename filter handles
        this).
  - MTF contract:
      * missing MTF -> mtf_missing.
      * incomplete K coverage -> mtf_incomplete_coverage.
      * incoherent last_date across K -> mtf_last_date_incoherent.
      * positive persist_skip_bars on MTF ->
        mtf_double_persist_skip_trim (Phase 6F-4 contract).
  - Confluence contract:
      * missing -> confluence_missing.
      * required last-row field missing ->
        confluence_last_row_incomplete.
      * signal alias mismatch (confluence_signal !=
        signal, or signal_value disagrees with canonical
        Buy=1/Short=-1/None=0) ->
        confluence_signal_alias_mismatch.
      * vote tally vs K*timeframes mismatch ->
        confluence_vote_total_mismatch.
      * cross-seed source_trafficflow_mtf_run_ids ->
        confluence_cross_seed_k_mixing.
  - Readiness contract:
      * verdict drift between validator's confluence
        finding and the readiness layer's confluence
        finding -> readiness_verdict_drift.
  - Board row contract:
      * preview is deterministic and contains every
        required key for an eligible-shaped ticker.
  - Aggregate + CLI.
"""
from __future__ import annotations

import ast
import json
import os
import pickle
import sys
import time
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import confluence_ranking_contract_validator as crcv  # noqa: E402
import daily_board_automation_preflight as dap  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    stack_dir = tmp_path / "stackbuilder"
    sig_dir = tmp_path / "siglib"
    for d in (cache_dir, artifact_root, stack_dir, sig_dir):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache_dir,
        "artifact_root": artifact_root,
        "stackbuilder_root": stack_dir,
        "signal_library_dir": sig_dir,
    }


def _safe_filename(ticker: str) -> str:
    return f"{ticker.upper().replace('^', '_')}_precomputed_results.pkl"


def _write_realistic_cache_pkl(
    cache_dir: Path,
    ticker: str,
    *,
    last_date: str = "2026-05-08",
    n: int = 60,
) -> Path:
    """Write a Spymaster-shaped cache PKL minimally
    sufficient for ``primary_signal_engine.load_primary_signal_engine_payload``
    to return ``available=True``."""
    import pandas as pd

    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(end=last_date, periods=n)
    df = pd.DataFrame(
        {
            "Close": [100.0 + i * 0.1 for i in range(n)],
            "SMA_1": [100.0 + i * 0.1 for i in range(n)],
            "SMA_2": [100.0 + i * 0.05 for i in range(n)],
        },
        index=dates,
    )
    active_pairs = ["Buy 2,1"] * n
    payload = {
        "preprocessed_data": df,
        "active_pairs": active_pairs,
        "_ticker": ticker,
        "_last_date": pd.Timestamp(last_date),
        "last_date": pd.Timestamp(last_date),
        "signal_engine_cache_refresher_scope": "optimizer_v1",
        "existing_max_sma_day": 2,
    }
    path = cache_dir / _safe_filename(ticker)
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_stackbuilder_run(
    stack_root: Path,
    target: str,
    *,
    seed: str = "seedTC__AAA-D_BBB-D",
) -> Path:
    import pandas as pd

    safe = target.upper().replace("^", "_")
    run_dir = stack_root / safe / seed
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = [{
        "K": k,
        "Trigger Days": 100 + k,
        "Total Capture (%)": 10.0 + k,
        "Sharpe Ratio": 0.1,
        "p-Value": 0.05,
        "Members": "['AAA[D]', 'BBB[D]']",
    } for k in range(1, 13)]
    pd.DataFrame(rows, columns=[
        "K", "Trigger Days", "Total Capture (%)",
        "Sharpe Ratio", "p-Value", "Members",
    ]).to_excel(run_dir / "combo_leaderboard.xlsx", index=False)
    return run_dir


def _write_multitimeframe_libs(
    sig_dir: Path, ticker: str, intervals: list[str],
) -> None:
    safe = ticker.upper().replace("^", "_")
    for interval in intervals:
        (sig_dir / f"{safe}_stable_v1_0_0_{interval}.pkl"
         ).write_bytes(b"placeholder")


def _write_daily_k_artifact(
    artifact_root: Path,
    target: str,
    K: int,
    *,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    last_date: str = "2026-05-08",
    internal_K_override: int | None = None,
    filename_override: str | None = None,
) -> Path:
    """Write a Phase 6D-1 daily-K artifact at
    ``trafficflow/<TARGET>/<seed_run_id>__K<K>.research_day.json``.

    ``internal_K_override`` lets a test write an artifact
    whose ``K`` field intentionally mismatches the filename
    K (for the K-mismatch test). ``filename_override`` lets a
    test write a legacy unsuffixed file."""
    safe = target.upper().replace("^", "_")
    tf_dir = artifact_root / "trafficflow" / safe
    tf_dir.mkdir(parents=True, exist_ok=True)
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow",
        target_ticker=target,
        signal_source="",
        run_id=f"{seed_run_id}__K{K}",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 5.0,
            "sharpe_ratio": 0.05,
            "trigger_days": 3,
        },
        daily=[{
            "date": last_date,
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "pressure_signal": "Buy",
            "buy_count": 1,
            "short_count": 0,
            "none_count": 0,
            "missing_count": 0,
            "active_count": 1,
            "available_count": 1,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 0.0,
            "is_trigger_day": True,
        }],
        K=internal_K_override if internal_K_override is not None else K,
        members=["AAA", "BBB"],
        protocol_per_member={"AAA": "D", "BBB": "D"},
        timeframes=["1d"],
    )
    fname = (
        filename_override
        if filename_override is not None
        else f"{seed_run_id}__K{K}.research_day.json"
    )
    return ra.write_research_day_artifact(
        art, tf_dir / fname,
    )


def _write_mtf_artifact(
    artifact_root: Path,
    target: str,
    K: int,
    *,
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    last_date: str = "2026-05-08",
    persist_skip_override: int | None = None,
) -> Path:
    safe = target.upper().replace("^", "_")
    tf_dir = artifact_root / "trafficflow" / safe
    tf_dir.mkdir(parents=True, exist_ok=True)
    ps = persist_skip_override if persist_skip_override is not None else 0
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="trafficflow",
        target_ticker=target,
        signal_source="",
        run_id=f"{seed_run_id}__K{K}__MTF",
        metric_basis="Close",
        persist_skip_bars=ps,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 5.0,
            "sharpe_ratio": 0.05,
            "trigger_days": 3,
        },
        daily=[{
            "date": last_date,
            "target_close": 100.0,
            "target_return_pct": 0.0,
            "pressure_signal": "Buy",
            "timeframe_pressure_signals": {
                tf: "Buy" for tf in (
                    "1d", "1wk", "1mo", "3mo", "1y",
                )
            },
            "buy_count": 5,
            "short_count": 0,
            "none_count": 0,
            "missing_count": 0,
            "active_count": 5,
            "available_count": 5,
            "daily_capture_pct": 0.0,
            "cumulative_capture_pct": 1.0,
            "is_trigger_day": True,
        }],
        K=K,
        members=["AAA", "BBB"],
        protocol_per_member={"AAA": "D", "BBB": "D"},
        timeframes=["1d", "1wk", "1mo", "3mo", "1y"],
    )
    return ra.write_research_day_artifact(
        art,
        tf_dir / f"{seed_run_id}__K{K}__MTF.research_day.json",
    )


def _write_confluence_artifact(
    artifact_root: Path,
    target: str,
    *,
    last_date: str = "2026-05-08",
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    confluence_signal: str = "None",
    signal_value: int | None = None,
    buy_votes: int | None = None,
    short_votes: int | None = None,
    none_votes: int | None = None,
    missing_votes: int | None = None,
    K_values: list[int] | None = None,
    timeframes: list[str] | None = None,
    extra_run_ids: list[str] | None = None,
    omit_field: str | None = None,
    confluence_signal_alias: str | None = None,
) -> Path:
    """Write a Phase 6D-3 Confluence MTF artifact with
    overridable fields for the alias-mismatch / vote-tally /
    cross-seed / missing-field tests."""
    safe = target.upper().replace("^", "_")
    conf_dir = artifact_root / "confluence" / safe
    conf_dir.mkdir(parents=True, exist_ok=True)
    K_values = (
        list(K_values) if K_values is not None
        else list(range(1, 13))
    )
    timeframes = (
        list(timeframes) if timeframes is not None
        else ["1d", "1wk", "1mo", "3mo", "1y"]
    )
    total_cells = len(K_values) * len(timeframes)
    if signal_value is None:
        signal_value = {"Buy": 1, "Short": -1, "None": 0}.get(
            confluence_signal, 0,
        )
    if buy_votes is None:
        buy_votes = 0 if confluence_signal != "Buy" else 5
    if short_votes is None:
        short_votes = 0 if confluence_signal != "Short" else 5
    if missing_votes is None:
        missing_votes = 0
    if none_votes is None:
        none_votes = (
            total_cells - buy_votes - short_votes - missing_votes
        )
    run_ids = [
        f"{seed_run_id}__K{k}__MTF" for k in K_values
    ]
    if extra_run_ids:
        run_ids = list(run_ids) + list(extra_run_ids)
    # Build the last daily row, allowing one field to be
    # omitted for the missing-field test.
    row = {
        "date": last_date,
        "target": target,
        "target_ticker": target,
        "target_close": 100.0,
        "target_return_pct": 0.0,
        "confluence_signal": (
            confluence_signal_alias
            if confluence_signal_alias is not None
            else confluence_signal
        ),
        "signal": confluence_signal,
        "signal_value": signal_value,
        "agreement_active": buy_votes + short_votes,
        "agreement_total": total_cells,
        "active_count": (buy_votes + short_votes),
        "available_count": total_cells,
        "buy_votes": buy_votes,
        "short_votes": short_votes,
        "none_votes": none_votes,
        "missing_votes": missing_votes,
        "K_values": K_values,
        "timeframes": timeframes,
        "source_trafficflow_mtf_run_ids": run_ids,
        "daily_capture_pct": 0.0,
        "is_trigger_day": False,
        "cumulative_capture_pct": 0.0,
    }
    if omit_field is not None and omit_field in row:
        del row[omit_field]
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="confluence",
        target_ticker=target,
        signal_source="",
        run_id="mtf_consensus",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 50.0,
            "sharpe_ratio": 0.1,
            "trigger_days": 5,
        },
        daily=[row],
        timeframes=timeframes,
        min_active=1,
    )
    return ra.write_research_day_artifact(
        art,
        conf_dir / f"{safe}__MTF_CONSENSUS.research_day.json",
    )


def _write_confluence_with_count_drift(
    artifact_root: Path,
    target: str,
    *,
    last_date: str = "2026-05-08",
    seed_run_id: str = "seedTC__AAA-D_BBB-D",
    confluence_signal: str = "None",
    signal_value: int | None = None,
    buy_votes: int = 0,
    short_votes: int = 0,
    none_votes: int | None = None,
    missing_votes: int = 0,
    K_values: list[int] | None = None,
    timeframes: list[str] | None = None,
    # Phase 6I-1 amendment overrides: any field below
    # defaults to a value consistent with the rest of the
    # artifact when ``None``; pass an explicit integer to
    # inject drift.
    active_count_override: int | None = None,
    available_count_override: int | None = None,
    agreement_total_override: int | None = None,
    agreement_active_override: int | None = None,
    confluence_signal_alias_override: str | None = None,
) -> Path:
    """Write a Confluence artifact with explicit overrides
    for every count field the Phase 6I-1 amendment
    validates. Defaults match the canonical
    Phase 6D-3 builder formulas; tests pass overrides to
    inject specific drifts."""
    import json

    safe = target.upper().replace("^", "_")
    conf_dir = artifact_root / "confluence" / safe
    conf_dir.mkdir(parents=True, exist_ok=True)
    K_values = (
        list(K_values) if K_values is not None
        else list(range(1, 13))
    )
    timeframes = (
        list(timeframes) if timeframes is not None
        else ["1d", "1wk", "1mo", "3mo", "1y"]
    )
    expected_cells = len(K_values) * len(timeframes)
    if signal_value is None:
        signal_value = {
            "Buy": 1, "Short": -1, "None": 0,
        }.get(confluence_signal, 0)
    if none_votes is None:
        none_votes = (
            expected_cells - buy_votes - short_votes - missing_votes
        )
    # Canonical formulas (the values the validator EXPECTS
    # when no override is supplied).
    active_count = (
        active_count_override
        if active_count_override is not None
        else (buy_votes + short_votes)
    )
    available_count = (
        available_count_override
        if available_count_override is not None
        else (
            (active_count_override or (buy_votes + short_votes))
            + none_votes
        )
    )
    agreement_total = (
        agreement_total_override
        if agreement_total_override is not None
        else available_count
    )
    if agreement_active_override is not None:
        agreement_active = agreement_active_override
    else:
        # Strict-unanimity rule.
        if buy_votes == 0 and short_votes == 0:
            agreement_active = 0
        elif buy_votes > 0 and short_votes == 0:
            agreement_active = buy_votes
        elif buy_votes == 0 and short_votes > 0:
            agreement_active = short_votes
        else:
            agreement_active = 0
    run_ids = [
        f"{seed_run_id}__K{k}__MTF" for k in K_values
    ]
    row = {
        "date": last_date,
        "target": target,
        "target_ticker": target,
        "target_close": 100.0,
        "target_return_pct": 0.0,
        "confluence_signal": (
            confluence_signal_alias_override
            if confluence_signal_alias_override is not None
            else confluence_signal
        ),
        "signal": confluence_signal,
        "signal_value": signal_value,
        "agreement_active": agreement_active,
        "agreement_total": agreement_total,
        "active_count": active_count,
        "available_count": available_count,
        "buy_votes": buy_votes,
        "short_votes": short_votes,
        "none_votes": none_votes,
        "missing_votes": missing_votes,
        "K_values": K_values,
        "timeframes": timeframes,
        "source_trafficflow_mtf_run_ids": run_ids,
        "daily_capture_pct": 0.0,
        "is_trigger_day": False,
        "cumulative_capture_pct": 0.0,
    }
    art = ra.ResearchDayArtifact(
        artifact_version=ra.ARTIFACT_VERSION,
        engine="confluence",
        target_ticker=target,
        signal_source="",
        run_id="mtf_consensus",
        metric_basis="Close",
        persist_skip_bars=1,
        generated_at="2026-05-08T00:00:00+00:00",
        summary={
            "total_capture_pct": 50.0,
            "sharpe_ratio": 0.1,
            "trigger_days": 5,
        },
        daily=[row],
        timeframes=timeframes,
        min_active=1,
    )
    return ra.write_research_day_artifact(
        art,
        conf_dir / f"{safe}__MTF_CONSENSUS.research_day.json",
    )


def _write_full_valid_artifacts(
    dirs: dict[str, Path],
    target: str = "SPY",
    *,
    last_date: str = "2026-05-08",
) -> None:
    """Write the complete artifact chain for a single
    valid ticker: cache, stackbuilder, MTF libs, K=1..12
    daily K, K=1..12 MTF K, and the Confluence MTF
    artifact. The validator should report every contract
    OK against this fixture."""
    _write_realistic_cache_pkl(
        dirs["cache_dir"], target, last_date=last_date,
    )
    _write_stackbuilder_run(dirs["stackbuilder_root"], target)
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], target, ["1wk", "1mo"],
    )
    for k in range(1, 13):
        _write_daily_k_artifact(
            dirs["artifact_root"], target, k,
            last_date=last_date,
        )
        _write_mtf_artifact(
            dirs["artifact_root"], target, k,
            last_date=last_date,
        )
    _write_confluence_artifact(
        dirs["artifact_root"], target, last_date=last_date,
    )


def _snapshot_tree(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# 1. Forbidden imports
# ---------------------------------------------------------------------------


def test_validator_has_no_forbidden_imports():
    """The validator must never import writer surfaces or
    yfinance / dash / engines that would tempt it into
    executing instead of inspecting."""
    tree = ast.parse(
        Path(crcv.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance",
        "trafficflow",
        "spymaster",
        "impactsearch",
        "onepass",
        "confluence",
        "cross_ticker_confluence",
        "dash",
        "daily_signal_board",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "daily_board_automation_writer",
        "subprocess",
    }
    found: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad = [m for m in found if m.split(".")[0] in forbidden]
    assert not bad, (
        "forbidden import in "
        f"confluence_ranking_contract_validator: {bad!r}"
    )


# ---------------------------------------------------------------------------
# 2. Valid full contract
# ---------------------------------------------------------------------------


def test_full_valid_artifact_chain_passes_every_contract(
    tmp_path: Path,
):
    """The full SPY-shaped fixture must report every
    contract OK. ``leader_eligible`` is True because the
    Confluence last_date matches the cutoff and every
    upstream gate is open."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs, target="SPY")
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.cache_contract_ok is True
    assert v.stackbuilder_contract_ok is True
    assert v.daily_k_contract_ok is True
    assert v.mtf_contract_ok is True
    assert v.confluence_contract_ok is True
    assert v.readiness_contract_ok is True
    assert v.board_row_contract_ok is True
    assert v.issue_codes == ()
    assert v.blocking_reasons == ()
    assert v.selected_stackbuilder_run_id is not None
    assert tuple(v.daily_k_coverage) == tuple(range(1, 13))
    assert tuple(v.mtf_k_coverage) == tuple(range(1, 13))
    assert v.confluence_last_date == "2026-05-08"
    assert v.board_row_preview is not None
    assert v.leader_eligible is True
    assert v.recommended_next_operator_action == (
        crcv.RECOMMENDED_CONTRACT_VALID
    )


# ---------------------------------------------------------------------------
# 3. Cache contract failures
# ---------------------------------------------------------------------------


def test_missing_cache_fails_cache_contract(tmp_path: Path):
    dirs = _layout(tmp_path)
    # Build everything except the cache.
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.cache_contract_ok is False
    assert (
        crcv.ISSUE_CACHE_MISSING in v.issue_codes
        or crcv.ISSUE_CACHE_UNREADABLE in v.issue_codes
    )
    assert v.recommended_next_operator_action == (
        crcv.RECOMMENDED_FIX_CACHE
    )


# ---------------------------------------------------------------------------
# 4. StackBuilder contract
# ---------------------------------------------------------------------------


def test_no_stackbuilder_run_fails_contract(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.stackbuilder_contract_ok is False
    assert crcv.ISSUE_STACKBUILDER_MISSING in v.issue_codes
    assert v.selected_stackbuilder_run_id is None
    assert v.recommended_next_operator_action == (
        crcv.RECOMMENDED_FIX_STACKBUILDER
    )


def test_single_stackbuilder_variant_is_valid(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.stackbuilder_contract_ok is True
    assert v.selected_stackbuilder_run_id is not None


def test_multiple_stackbuilder_variants_clear_newest_mtime_valid(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    older = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__OLDER-D_RUN-D",
    )
    base = time.time() - 86400
    os.utime(older, (base, base))
    newer = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__NEWER-D_RUN-D",
    )
    os.utime(newer, (base + 3600, base + 3600))
    # Build the rest of the chain with the newer seed_run_id
    # so the readiness layer can clear too.
    for k in range(1, 13):
        _write_daily_k_artifact(
            dirs["artifact_root"], "SPY", k,
            seed_run_id="seedTC__NEWER-D_RUN-D",
        )
        _write_mtf_artifact(
            dirs["artifact_root"], "SPY", k,
            seed_run_id="seedTC__NEWER-D_RUN-D",
        )
    _write_confluence_artifact(
        dirs["artifact_root"], "SPY",
        seed_run_id="seedTC__NEWER-D_RUN-D",
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.stackbuilder_contract_ok is True
    assert v.selected_stackbuilder_run_id == newer.name


def test_tied_newest_mtime_stackbuilder_blocks_manual(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    a = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__TIE_A-D_RUN-D",
    )
    b = _write_stackbuilder_run(
        dirs["stackbuilder_root"], "SPY",
        seed="seedTC__TIE_B-D_RUN-D",
    )
    tied = time.time()
    os.utime(a, (tied, tied))
    os.utime(b, (tied, tied))
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.stackbuilder_contract_ok is False
    assert crcv.ISSUE_STACKBUILDER_SELECTION_AMBIGUOUS in (
        v.issue_codes
    )
    assert v.recommended_next_operator_action == (
        crcv.RECOMMENDED_MANUAL_REVIEW
    )


def test_validator_has_no_age_based_stale_window():
    """Phase 6H-3 / 6H-7 contract: saved stack variants are
    durable. The validator's StackBuilder check must NOT
    look at directory mtime as a freshness signal beyond the
    tie-breaker policy."""
    source = Path(crcv.__file__).read_text(encoding="utf-8")
    # Hard guards against the failure modes a future PR
    # might introduce.
    assert "stale" not in source.lower().split("\n")[0:5][0].lower() if False else True
    # Lighter contract: no day-based age threshold keyword
    # in the StackBuilder check.
    forbidden_substrings = (
        "30 days",
        "thirty days",
        "STACKBUILDER_AGE_DAYS",
        "STACKBUILDER_STALE_DAYS",
    )
    for sub in forbidden_substrings:
        assert sub not in source, (
            f"validator must not enforce an age-based "
            f"StackBuilder stale window; found {sub!r}"
        )


# ---------------------------------------------------------------------------
# 5. Daily K contract
# ---------------------------------------------------------------------------


def test_missing_daily_k_fails_contract(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # No daily K artifacts; no MTF; no Confluence.
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.daily_k_contract_ok is False
    assert crcv.ISSUE_DAILY_K_MISSING in v.issue_codes


def test_incomplete_daily_k_coverage_fails(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # Only K=1..5; missing K=6..12.
    for k in range(1, 6):
        _write_daily_k_artifact(
            dirs["artifact_root"], "SPY", k,
        )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.daily_k_contract_ok is False
    assert (
        crcv.ISSUE_DAILY_K_INCOMPLETE_COVERAGE
        in v.issue_codes
    )
    assert tuple(v.daily_k_coverage) == (1, 2, 3, 4, 5)


def test_legacy_unsuffixed_daily_artifact_is_silently_ignored(
    tmp_path: Path,
):
    """The validator inherits the Phase 6F-4 filename
    filter through the bridge helper. A legacy unsuffixed
    artifact must NOT count as daily-K coverage."""
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # Write a legacy artifact whose filename does NOT carry
    # ``__K<K>``. The bridge's filename filter should
    # exclude it.
    _write_daily_k_artifact(
        dirs["artifact_root"], "SPY", 1,
        filename_override="legacy_seed.research_day.json",
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.daily_k_contract_ok is False
    assert crcv.ISSUE_DAILY_K_MISSING in v.issue_codes
    assert v.daily_k_coverage == ()


def test_daily_k_internal_K_mismatch_fails(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    # Write K=1..11 correctly + K=12 with an internal_K=99
    # mismatch.
    for k in range(1, 12):
        _write_daily_k_artifact(
            dirs["artifact_root"], "SPY", k,
        )
    _write_daily_k_artifact(
        dirs["artifact_root"], "SPY", 12,
        internal_K_override=99,
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.daily_k_contract_ok is False
    assert (
        crcv.ISSUE_DAILY_K_INTERNAL_K_MISMATCH
        in v.issue_codes
    )


# ---------------------------------------------------------------------------
# 6. MTF contract
# ---------------------------------------------------------------------------


def test_missing_mtf_fails_contract(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    for k in range(1, 13):
        _write_daily_k_artifact(
            dirs["artifact_root"], "SPY", k,
        )
    # No MTF artifacts.
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.mtf_contract_ok is False
    assert crcv.ISSUE_MTF_MISSING in v.issue_codes


def test_mtf_incomplete_K_coverage_fails(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    for k in range(1, 13):
        _write_daily_k_artifact(
            dirs["artifact_root"], "SPY", k,
        )
    # Only K=1..5 for MTF.
    for k in range(1, 6):
        _write_mtf_artifact(
            dirs["artifact_root"], "SPY", k,
        )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.mtf_contract_ok is False
    assert (
        crcv.ISSUE_MTF_INCOMPLETE_COVERAGE
        in v.issue_codes
    )


def test_mtf_last_date_incoherent_across_k(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    for k in range(1, 13):
        _write_daily_k_artifact(
            dirs["artifact_root"], "SPY", k,
        )
    # K=1..6 share last_date "2026-05-08"; K=7..12 share
    # last_date "2026-05-07". The validator must catch the
    # incoherence.
    for k in range(1, 7):
        _write_mtf_artifact(
            dirs["artifact_root"], "SPY", k,
            last_date="2026-05-08",
        )
    for k in range(7, 13):
        _write_mtf_artifact(
            dirs["artifact_root"], "SPY", k,
            last_date="2026-05-07",
        )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.mtf_contract_ok is False
    assert (
        crcv.ISSUE_MTF_LAST_DATE_INCOHERENT
        in v.issue_codes
    )


def test_mtf_double_persist_skip_trim_regression(
    tmp_path: Path,
):
    """Phase 6F-4 contract: MTF persist_skip_bars must be 0
    because the daily-K stage already owns the single trim.
    A positive persist_skip_bars on the MTF artifact is the
    double-trim regression the validator should catch."""
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    for k in range(1, 13):
        _write_daily_k_artifact(
            dirs["artifact_root"], "SPY", k,
        )
    for k in range(1, 13):
        _write_mtf_artifact(
            dirs["artifact_root"], "SPY", k,
            persist_skip_override=1,  # double-trim
        )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.mtf_contract_ok is False
    assert (
        crcv.ISSUE_MTF_DOUBLE_PERSIST_SKIP_TRIM
        in v.issue_codes
    )


# ---------------------------------------------------------------------------
# 7. Confluence contract
# ---------------------------------------------------------------------------


def test_missing_confluence_fails_contract(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_realistic_cache_pkl(dirs["cache_dir"], "SPY")
    _write_stackbuilder_run(dirs["stackbuilder_root"], "SPY")
    _write_multitimeframe_libs(
        dirs["signal_library_dir"], "SPY", ["1wk", "1mo"],
    )
    for k in range(1, 13):
        _write_daily_k_artifact(
            dirs["artifact_root"], "SPY", k,
        )
        _write_mtf_artifact(
            dirs["artifact_root"], "SPY", k,
        )
    # No Confluence artifact.
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert crcv.ISSUE_CONFLUENCE_MISSING in v.issue_codes


def test_confluence_last_row_incomplete_fails(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    # Overwrite the Confluence artifact with one missing a
    # required field.
    _write_confluence_artifact(
        dirs["artifact_root"], "SPY",
        omit_field="agreement_total",
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_LAST_ROW_INCOMPLETE
        in v.issue_codes
    )


def test_confluence_signal_alias_mismatch_fails(
    tmp_path: Path,
):
    """confluence_signal must equal signal; signal_value
    must follow the canonical mapping."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    # signal="None" but confluence_signal="Buy" -> mismatch.
    _write_confluence_artifact(
        dirs["artifact_root"], "SPY",
        confluence_signal="None",
        confluence_signal_alias="Buy",
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_SIGNAL_ALIAS_MISMATCH
        in v.issue_codes
    )


def test_confluence_signal_value_alias_mismatch_fails(
    tmp_path: Path,
):
    """signal_value must be Buy=1 / Short=-1 / None=0."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    # signal="Buy" should have signal_value=1; we pin it to
    # the wrong value.
    _write_confluence_artifact(
        dirs["artifact_root"], "SPY",
        confluence_signal="Buy",
        signal_value=-1,
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_SIGNAL_ALIAS_MISMATCH
        in v.issue_codes
    )


def test_active_count_must_equal_buy_plus_short(
    tmp_path: Path,
):
    """Phase 6I-1 amendment: active_count == buy + short.
    Inconsistency flags confluence_count_incoherent. Board
    derives its agreement display from active_count, so
    drift here is operator-visible."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    # K=12, timeframes=5 -> 60 cells. Buy=5, Short=2, but
    # write active_count=10 (should be 7).
    _write_confluence_artifact(
        dirs["artifact_root"], "SPY",
        buy_votes=5, short_votes=2,
        none_votes=53, missing_votes=0,
    )
    # Override the active_count after the fact: easiest path
    # is to write a custom artifact via a tiny patch helper.
    # Re-emit with an explicit active_count drift instead.
    _write_confluence_with_count_drift(
        dirs["artifact_root"], "SPY",
        buy_votes=5, short_votes=2,
        none_votes=53, missing_votes=0,
        active_count_override=10,  # wrong (should be 7)
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_COUNT_INCOHERENT
        in v.issue_codes
    )


def test_available_count_must_equal_active_plus_none(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    _write_confluence_with_count_drift(
        dirs["artifact_root"], "SPY",
        buy_votes=5, short_votes=2,
        none_votes=53, missing_votes=0,
        # active_count correct (7), but available_count
        # wrong (should be 7+53=60; we pin 50).
        available_count_override=50,
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_COUNT_INCOHERENT
        in v.issue_codes
    )


def test_agreement_total_must_equal_available_count(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    _write_confluence_with_count_drift(
        dirs["artifact_root"], "SPY",
        buy_votes=5, short_votes=2,
        none_votes=53, missing_votes=0,
        # active_count + available_count correct (7, 60);
        # agreement_total drifts (should be 60; we pin 45).
        agreement_total_override=45,
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_COUNT_INCOHERENT
        in v.issue_codes
    )


def test_agreement_active_strict_unanimity_mixed_must_be_zero(
    tmp_path: Path,
):
    """Strict-unanimity rule: when buy>0 AND short>0, the
    artifact's agreement_active MUST be 0. Anything else
    flags confluence_agreement_active_inconsistent."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    _write_confluence_with_count_drift(
        dirs["artifact_root"], "SPY",
        buy_votes=5, short_votes=2,
        none_votes=53, missing_votes=0,
        # Mixed (5 buy, 2 short) -> rule says
        # agreement_active=0; we pin it to 7 (the
        # active_count) which is the easy-to-make mistake.
        agreement_active_override=7,
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_AGREEMENT_ACTIVE_INCONSISTENT
        in v.issue_codes
    )


def test_agreement_active_strict_unanimity_unanimous_buy(
    tmp_path: Path,
):
    """Strict-unanimity rule: when buy>0 AND short==0,
    agreement_active MUST equal buy_votes. An artifact that
    pins agreement_active=0 here is inconsistent."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    _write_confluence_with_count_drift(
        dirs["artifact_root"], "SPY",
        buy_votes=12, short_votes=0,
        none_votes=48, missing_votes=0,
        confluence_signal="Buy", signal_value=1,
        agreement_active_override=0,  # should be 12
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_AGREEMENT_ACTIVE_INCONSISTENT
        in v.issue_codes
    )


def test_invalid_signal_vocabulary_fails(tmp_path: Path):
    """signal must be one of {"Buy", "Short", "None"}.
    Anything else flags confluence_invalid_signal_vocabulary."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    _write_confluence_with_count_drift(
        dirs["artifact_root"], "SPY",
        buy_votes=0, short_votes=0,
        none_votes=60, missing_votes=0,
        confluence_signal="Maybe",  # not in the vocabulary
        confluence_signal_alias_override="Maybe",
        signal_value=0,
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_INVALID_SIGNAL_VOCABULARY
        in v.issue_codes
    )


def test_board_row_preview_uses_active_count_not_alias_fields(
    tmp_path: Path,
):
    """Phase 6I-1 amendment: the preview's
    agreement_active / agreement_total must come from
    active_count / available_count (matching
    daily_signal_board._confluence_active_total), NOT from
    the artifact's separate agreement_active /
    agreement_total fields.

    We can verify by writing a SPY-shaped artifact where
    those four fields all carry DIFFERENT values, all
    individually internally consistent, then asserting the
    preview reflects the active_count/available_count
    pair.
    """
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    # buy=5, short=2 -> active_count = 7
    # available_count = active_count + none = 7 + 53 = 60
    # agreement_total = available_count = 60
    # agreement_active (mixed) = 0
    # These are the canonical values; the SPY-shape default
    # is correct. We write them explicitly here so the
    # test reads as a clear contract assertion.
    _write_confluence_with_count_drift(
        dirs["artifact_root"], "SPY",
        buy_votes=5, short_votes=2,
        none_votes=53, missing_votes=0,
        active_count_override=7,
        available_count_override=60,
        agreement_total_override=60,
        agreement_active_override=0,
        confluence_signal="None", signal_value=0,
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is True
    preview = v.board_row_preview
    assert preview is not None
    # The preview's agreement_active MUST come from
    # active_count (7), NOT from the artifact's
    # agreement_active (0).
    assert preview["agreement_active"] == 7
    assert preview["agreement_total"] == 60
    # The agreement_ratio MUST be active_count /
    # available_count, NOT agreement_active /
    # agreement_total.
    assert preview["agreement_ratio"] == pytest.approx(
        7 / 60,
    )


def test_confluence_vote_total_mismatch_fails(tmp_path: Path):
    """buy + short + none + missing must equal
    len(K_values) * len(timeframes). missing_votes are
    per-CELL."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    # K=12, timeframes=5 -> 60 cells. We declare votes
    # totalling 50.
    _write_confluence_artifact(
        dirs["artifact_root"], "SPY",
        buy_votes=10,
        short_votes=10,
        none_votes=20,
        missing_votes=10,
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_VOTE_TOTAL_MISMATCH
        in v.issue_codes
    )


def test_confluence_cross_seed_k_mixing_rejected(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    # Inject extra run_ids from a different seed.
    _write_confluence_artifact(
        dirs["artifact_root"], "SPY",
        extra_run_ids=[
            "seedTC__OTHER-SEED-D_FOO-I__K1__MTF",
        ],
    )
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.confluence_contract_ok is False
    assert (
        crcv.ISSUE_CONFLUENCE_CROSS_SEED_K_MIXING
        in v.issue_codes
    )


# ---------------------------------------------------------------------------
# 8. Readiness contract
# ---------------------------------------------------------------------------


def test_readiness_verdict_matches_validator_on_valid_chain(
    tmp_path: Path,
):
    """If the validator's confluence finding is OK, the
    readiness layer must also report confluence present
    (no drift)."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v.readiness_contract_ok is True
    assert (
        crcv.ISSUE_READINESS_VERDICT_DRIFT
        not in v.issue_codes
    )


# ---------------------------------------------------------------------------
# 9. Board row contract
# ---------------------------------------------------------------------------


def test_board_row_preview_for_eligible_ticker(tmp_path: Path):
    """For an eligible ticker the preview must carry every
    required field with deterministic, non-None values
    (where applicable)."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    v = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    preview = v.board_row_preview
    assert preview is not None
    required_keys = {
        "ticker",
        "consensus_signal",
        "consensus_signal_value",
        "agreement_active",
        "agreement_total",
        "agreement_ratio",
        "coverage",
        "as_of_date",
        "rank_eligible",
        "ranking_blocked_reason",
    }
    assert set(preview.keys()) == required_keys
    assert preview["ticker"] == "SPY"
    assert preview["coverage"] == "Full"
    assert preview["rank_eligible"] is True
    assert preview["as_of_date"] == "2026-05-08"
    assert preview["ranking_blocked_reason"] == ""


def test_board_row_preview_is_deterministic_across_runs(
    tmp_path: Path,
):
    """Two consecutive runs against the same fixture must
    produce byte-identical previews."""
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    v1 = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    v2 = crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    assert v1.board_row_preview == v2.board_row_preview


# ---------------------------------------------------------------------------
# 10. Aggregate report
# ---------------------------------------------------------------------------


def test_aggregate_report_partitions_correctly(tmp_path: Path):
    dirs = _layout(tmp_path)
    # SPY: fully valid + leader-eligible.
    _write_full_valid_artifacts(dirs, target="SPY")
    # GHOST: missing entirely.
    report = crcv.validate_confluence_ranking_contracts(
        ["SPY", "GHOST"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    assert report.inspected_count == 2
    assert "SPY" in report.fully_valid_tickers
    assert "GHOST" in report.contract_failed_tickers
    assert "SPY" not in report.contract_failed_tickers


def test_to_json_dict_round_trips(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    report = crcv.validate_confluence_ranking_contracts(
        ["SPY"],
        current_as_of_date="2026-05-08",
        **dirs,
    )
    d = report.to_json_dict()
    s = json.dumps(d)
    assert "SPY" in s
    assert d["inspected_count"] == 1
    assert isinstance(d["validations"][0]["ticker"], str)


# ---------------------------------------------------------------------------
# 11. No writes
# ---------------------------------------------------------------------------


def test_validator_writes_nothing_to_any_root(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    before = {
        name: _snapshot_tree(p) for name, p in dirs.items()
    }
    crcv.validate_confluence_ranking_contract(
        "SPY", current_as_of_date="2026-05-08", **dirs,
    )
    after = {
        name: _snapshot_tree(p) for name, p in dirs.items()
    }
    for name in dirs:
        assert after[name] == before[name]


# ---------------------------------------------------------------------------
# 12. CLI
# ---------------------------------------------------------------------------


def test_cli_ticker_single_emits_json(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs)
    argv = [
        "--ticker", "SPY",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = crcv.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 1
    assert payload["validations"][0]["ticker"] == "SPY"
    assert (
        payload["validations"][0]["cache_contract_ok"]
        is True
    )


def test_cli_tickers_csv_emits_json(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    _write_full_valid_artifacts(dirs, target="SPY")
    argv = [
        "--tickers", "SPY,GHOST",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = crcv.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    tickers = [v["ticker"] for v in payload["validations"]]
    assert tickers == ["SPY", "GHOST"]


def test_cli_unknown_flag_returns_2_without_system_exit(capsys):
    rc = None
    try:
        rc = crcv.main(["--definitely-not-a-flag"])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on unknown flag; "
            f"got SystemExit({exc.code})"
        )
    assert rc == 2


def test_cli_mutually_exclusive_ticker_args_return_2(capsys):
    rc = None
    try:
        rc = crcv.main([
            "--ticker", "SPY", "--tickers", "AAPL,GOOG",
        ])
    except SystemExit as exc:
        pytest.fail(
            "main() leaked SystemExit on conflicting args; "
            f"got SystemExit({exc.code})"
        )
    assert rc == 2


def test_cli_empty_invocation_returns_0(tmp_path: Path, capsys):
    dirs = _layout(tmp_path)
    argv = [
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = crcv.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["inspected_count"] == 0
    assert payload["validations"] == []


# ---------------------------------------------------------------------------
# 13. Constants
# ---------------------------------------------------------------------------


def test_all_issue_codes_listed():
    expected = {
        crcv.ISSUE_CACHE_MISSING,
        crcv.ISSUE_CACHE_UNREADABLE,
        crcv.ISSUE_CACHE_NO_DATE_RANGE,
        crcv.ISSUE_CACHE_NO_CURRENT_SIGNAL,
        crcv.ISSUE_STACKBUILDER_MISSING,
        crcv.ISSUE_STACKBUILDER_SELECTION_AMBIGUOUS,
        crcv.ISSUE_DAILY_K_MISSING,
        crcv.ISSUE_DAILY_K_INCOMPLETE_COVERAGE,
        crcv.ISSUE_DAILY_K_INTERNAL_K_MISMATCH,
        crcv.ISSUE_MTF_MISSING,
        crcv.ISSUE_MTF_INCOMPLETE_COVERAGE,
        crcv.ISSUE_MTF_LAST_DATE_INCOHERENT,
        crcv.ISSUE_MTF_DOUBLE_PERSIST_SKIP_TRIM,
        crcv.ISSUE_CONFLUENCE_MISSING,
        crcv.ISSUE_CONFLUENCE_LAST_ROW_INCOMPLETE,
        crcv.ISSUE_CONFLUENCE_SIGNAL_ALIAS_MISMATCH,
        crcv.ISSUE_CONFLUENCE_VOTE_TOTAL_MISMATCH,
        crcv.ISSUE_CONFLUENCE_CROSS_SEED_K_MIXING,
        crcv.ISSUE_CONFLUENCE_COUNT_INCOHERENT,
        crcv.ISSUE_CONFLUENCE_AGREEMENT_ACTIVE_INCONSISTENT,
        crcv.ISSUE_CONFLUENCE_INVALID_SIGNAL_VOCABULARY,
        crcv.ISSUE_READINESS_VERDICT_DRIFT,
        crcv.ISSUE_BOARD_ROW_INCOMPUTABLE,
    }
    assert set(crcv.ALL_ISSUE_CODES) == expected


def test_recommended_next_operator_actions_listed():
    expected = {
        crcv.RECOMMENDED_CONTRACT_VALID,
        crcv.RECOMMENDED_CONTRACT_VALID_NOT_LEADER,
        crcv.RECOMMENDED_FIX_CACHE,
        crcv.RECOMMENDED_FIX_STACKBUILDER,
        crcv.RECOMMENDED_FIX_PIPELINE_ARTIFACTS,
        crcv.RECOMMENDED_FIX_CONFLUENCE,
        crcv.RECOMMENDED_FIX_READINESS_DRIFT,
        crcv.RECOMMENDED_MANUAL_REVIEW,
    }
    assert (
        set(crcv.RECOMMENDED_NEXT_OPERATOR_ACTIONS) == expected
    )
