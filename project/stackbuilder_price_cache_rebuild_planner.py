"""Phase 6I-54a: read-only StackBuilder price-cache
rebuild planner.

Determines, for each ticker in the Phase 6I-52 pilot
universe (or a caller-supplied ticker list), whether the
StackBuilder secondary price cache at
``price_cache/daily/<TICKER>.parquet`` (etc.) can be
populated **from existing local data only**, or whether
the operator must run a separate source-refresh /
network-fetch phase first.

This phase is the **plan** that Phase 6I-54b (supervised
price-cache write) will consume. Phase 6I-54a does NOT:

  * write any file under ``price_cache/daily/`` (or any
    other production root).
  * load any ``cache/results/<TICKER>_precomputed_results
    .pkl`` content. Manifest sidecar JSONs are read
    (plain JSON load); the PKLs themselves are NOT
    loaded -- that's Phase 6I-54b's job, gated on its
    own explicit authorization.
  * invoke yfinance, the source-cache refresher,
    StackBuilder, OnePass, ImpactSearch, TrafficFlow,
    Spymaster, or any pipeline runner.
  * import the ``pickle`` module (statically enforced).
  * use ``subprocess`` (statically enforced).

Cache distinction — IMPORTANT
-----------------------------

There are two caches the project uses that are easy to
confuse; this module keeps them strictly separate:

  * ``cache/results/`` — the **signal-engine cache**
    produced by ``signal_engine_cache_refresher`` /
    Spymaster ("optimizer_v1" scope). Each file is
    ``<TICKER>_precomputed_results.pkl`` with a
    ``<TICKER>_precomputed_results.pkl.manifest.json``
    sidecar. The manifest exposes ``params.price_source =
    "Close"`` -- confirming the cache stores Close
    prices.
  * ``price_cache/daily/`` — the **StackBuilder secondary
    price cache** checked by
    ``stackbuilder.load_secondary_prices`` (see
    ``stackbuilder.py:530-556``). Five candidate paths
    per ticker; if none exist StackBuilder falls through
    to ``_fetch_secondary_from_yf`` (live yfinance).

Phase 6I-54a's job is to **plan how the second cache can
be derived from the first** without a network round-trip.

Public surface
--------------

    SCHEMA_VERSION

    ACTION_USE_EXISTING_SIGNAL_CACHE
    ACTION_NEEDS_SOURCE_REFRESH
    ACTION_NEEDS_NETWORK_FETCH
    ACTION_MANUAL_REVIEW
    ALL_RECOMMENDED_ACTIONS

    DEFAULT_SIGNAL_CACHE_DIR_RELATIVE      # cache/results
    DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE  # price_cache/daily

    build_price_cache_rebuild_plan(
        tickers=None, *,
        signal_cache_dir=None,
        stackbuilder_price_cache_dir=None,
    ) -> dict[str, Any]

    main(argv=None) -> int                  # CLI entry

CLI
---

    python stackbuilder_price_cache_rebuild_planner.py \\
        --output md_library/shared/2026-05-15_PHASE_6I54A_PLAN.json

    python stackbuilder_price_cache_rebuild_planner.py \\
        --tickers SPY,AAPL,MSFT

Strict read-only contract pins
------------------------------

  * Forbidden top-level imports: ``pickle``,
    ``subprocess``, ``yfinance``, ``dash``, writer
    modules (``signal_engine_cache_refresher`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    ``confluence_pipeline_runner`` /
    ``daily_board_automation_*``), engine modules
    (``stackbuilder`` / ``onepass`` / ``impactsearch`` /
    ``trafficflow`` / ``spymaster`` / ``confluence``).
  * No ``--write`` CLI flag.
  * Optional ``--output`` JSON is guarded against landing
    inside any of the five production roots OR inside
    ``price_cache/daily/``.
  * The only file I/O is ``Path.exists()`` /
    ``Path.is_file()`` / ``Path.read_text()`` on plain-
    JSON manifest sidecars; no ``pickle.load``, no
    binary read of PKLs.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = (
    "stackbuilder_price_cache_rebuild_planner_v1"
)


ACTION_USE_EXISTING_SIGNAL_CACHE: str = (
    "use_existing_signal_cache"
)
ACTION_NEEDS_SOURCE_REFRESH: str = "needs_source_refresh"
ACTION_NEEDS_NETWORK_FETCH: str = "needs_network_fetch"
ACTION_MANUAL_REVIEW: str = "manual_review"


ALL_RECOMMENDED_ACTIONS: tuple[str, ...] = (
    ACTION_USE_EXISTING_SIGNAL_CACHE,
    ACTION_NEEDS_SOURCE_REFRESH,
    ACTION_NEEDS_NETWORK_FETCH,
    ACTION_MANUAL_REVIEW,
)


DEFAULT_SIGNAL_CACHE_DIR_RELATIVE: str = "cache/results"
DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE: str = (
    "price_cache/daily"
)


# Production-root relative paths guarded against by
# ``--output``. ``price_cache/daily`` is intentionally
# included even though it is not (yet) one of the
# documented production roots -- Phase 6I-54a must not
# write into it.
PRODUCTION_ROOT_RELATIVE_PATHS: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
    "price_cache/daily",
)


# Stable blocker codes used in per-row ``blocker_codes``.
BLOCKER_NO_SIGNAL_CACHE_FILE: str = (
    "no_signal_cache_pkl_found"
)
BLOCKER_NO_MANIFEST_SIDECAR: str = (
    "signal_cache_manifest_sidecar_missing"
)
BLOCKER_UNREADABLE_MANIFEST: str = (
    "signal_cache_manifest_unreadable"
)
BLOCKER_UNEXPECTED_MANIFEST_SHAPE: str = (
    "signal_cache_manifest_unexpected_shape"
)
BLOCKER_PRICE_SOURCE_NOT_CLOSE: str = (
    "signal_cache_price_source_not_close"
)
BLOCKER_PRICE_CACHE_ALREADY_PRESENT: str = (
    "stackbuilder_price_cache_already_present"
)


# Pinned interpreter (matches Phase 6I-50 / 6I-51 / 6I-52
# / 6I-53). Used in the documented next-write-command
# template strings only.
PINNED_INTERPRETER: str = (
    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/"
    "spyproject2/python.exe"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_for_path_guard(p: Any) -> str:
    return str(p).replace("\\", "/").lower()


def _path_is_inside_production_root(p: Any) -> bool:
    norm = _normalize_for_path_guard(p)
    for root in PRODUCTION_ROOT_RELATIVE_PATHS:
        if root in norm:
            return True
    return False


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _normalize_tickers(
    tickers: Iterable[str],
) -> tuple[str, ...]:
    """Strip + uppercase + dedupe (first-seen order)."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        if not isinstance(t, str):
            continue
        norm = t.strip().upper()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return tuple(out)


def _expected_stackbuilder_cache_paths(
    ticker: str,
    *,
    stackbuilder_price_cache_dir: Path,
) -> list[Path]:
    """Mirror the five candidate paths
    ``stackbuilder.load_secondary_prices`` checks (also
    pinned by the Phase 6I-53 preflight module). The
    caret-stripped variants matter for index tickers
    like ``^GSPC``."""
    sec = (ticker or "").upper()
    sec_clean = sec.replace("^", "")
    return [
        stackbuilder_price_cache_dir / f"{sec}.parquet",
        stackbuilder_price_cache_dir / f"{sec}.csv",
        (
            stackbuilder_price_cache_dir
            / f"{sec_clean}.parquet"
        ),
        stackbuilder_price_cache_dir / f"{sec_clean}.csv",
        (
            stackbuilder_price_cache_dir
            / sec / "daily.parquet"
        ),
    ]


def _find_first_existing(
    paths: Iterable[Path],
) -> Optional[Path]:
    for p in paths:
        try:
            if p.exists() and p.is_file():
                return p
        except OSError:
            continue
    return None


def _read_manifest_sidecar(
    manifest_path: Path,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Plain JSON load. Returns ``(payload, error_code)``.
    On unreadable / non-dict / parse error, returns
    ``(None, <stable_blocker_code>)``."""
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return None, BLOCKER_UNREADABLE_MANIFEST
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, BLOCKER_UNREADABLE_MANIFEST
    if not isinstance(parsed, dict):
        return None, BLOCKER_UNEXPECTED_MANIFEST_SHAPE
    return parsed, None


def _classify_ticker(
    ticker: str,
    *,
    signal_cache_dir: Path,
    stackbuilder_price_cache_dir: Path,
) -> dict[str, Any]:
    """Per-ticker classification. Read-only: no PKL
    load, no binary file content access, no network."""
    expected_paths = _expected_stackbuilder_cache_paths(
        ticker,
        stackbuilder_price_cache_dir=(
            stackbuilder_price_cache_dir
        ),
    )
    existing_stackbuilder_cache_path = _find_first_existing(
        expected_paths,
    )

    # Signal-engine cache PKL (the SOURCE we'd transform).
    # ``cache_cutoff_watcher`` and ``signal_engine_cache_
    # refresher`` write files of the shape
    # ``<TICKER>_precomputed_results.pkl`` with a
    # ``.manifest.json`` sidecar.
    pkl_path = (
        signal_cache_dir
        / f"{ticker}_precomputed_results.pkl"
    )
    manifest_path = Path(str(pkl_path) + ".manifest.json")
    pkl_exists = pkl_path.exists() and pkl_path.is_file()
    manifest_exists = (
        manifest_path.exists()
        and manifest_path.is_file()
    )

    # Outputs (initialize default).
    blocker_codes: list[str] = []
    manifest_data: Optional[dict[str, Any]] = None
    manifest_error: Optional[str] = None
    price_source_value: Optional[str] = None
    builder_engine: Optional[str] = None
    engine_version: Optional[str] = None
    build_timestamp: Optional[str] = None

    if manifest_exists:
        manifest_data, manifest_error = (
            _read_manifest_sidecar(manifest_path)
        )
        if manifest_error is not None:
            blocker_codes.append(manifest_error)
        if isinstance(manifest_data, dict):
            params = manifest_data.get("params")
            if isinstance(params, dict):
                ps = params.get("price_source")
                if isinstance(ps, str):
                    price_source_value = ps
            be = manifest_data.get("producer_engine")
            if isinstance(be, str):
                builder_engine = be
            ev = manifest_data.get("engine_version")
            if isinstance(ev, str):
                engine_version = ev
            bt = manifest_data.get("build_timestamp")
            if isinstance(bt, str):
                build_timestamp = bt

    # Recommendation cascade.
    if existing_stackbuilder_cache_path is not None:
        # The StackBuilder cache already has data for
        # this ticker. The planner does NOT know whether
        # it's the correct shape / freshness; surface as
        # manual_review so the operator decides whether
        # to keep / overwrite / regenerate.
        recommended_action = ACTION_MANUAL_REVIEW
        blocker_codes.append(
            BLOCKER_PRICE_CACHE_ALREADY_PRESENT,
        )
        transformation_possible_without_network = True
    elif not pkl_exists:
        # No source PKL on disk. The Phase 6E-5 source-
        # cache refresher (a separately-authorized
        # network-using phase) would need to run before a
        # transformation is possible.
        recommended_action = ACTION_NEEDS_SOURCE_REFRESH
        blocker_codes.append(
            BLOCKER_NO_SIGNAL_CACHE_FILE,
        )
        transformation_possible_without_network = False
    elif not manifest_exists:
        # PKL present but manifest missing; the verified
        # loader will refuse to read the PKL. Operator
        # must decide whether to trust the PKL or
        # regenerate it.
        recommended_action = ACTION_MANUAL_REVIEW
        blocker_codes.append(
            BLOCKER_NO_MANIFEST_SIDECAR,
        )
        transformation_possible_without_network = False
    elif manifest_error is not None:
        recommended_action = ACTION_MANUAL_REVIEW
        transformation_possible_without_network = False
    elif (
        price_source_value is not None
        and price_source_value.lower() != "close"
    ):
        # Non-Close price source -- the planner can't
        # safely emit a Close-column parquet without
        # operator review.
        recommended_action = ACTION_MANUAL_REVIEW
        blocker_codes.append(
            BLOCKER_PRICE_SOURCE_NOT_CLOSE,
        )
        transformation_possible_without_network = False
    else:
        # The happy path: PKL + manifest both present,
        # manifest is well-formed JSON with
        # ``price_source="Close"`` (or unset, which the
        # source-cache contract treats as Close). Phase
        # 6I-54b can load the PKL via the verified
        # loader, extract the Close series, and write
        # ``price_cache/daily/<TICKER>.parquet`` -- all
        # without a network round-trip.
        recommended_action = (
            ACTION_USE_EXISTING_SIGNAL_CACHE
        )
        transformation_possible_without_network = True

    return {
        "ticker": ticker,
        "expected_stackbuilder_cache_paths": [
            str(p) for p in expected_paths
        ],
        "current_cache_status": (
            "present"
            if existing_stackbuilder_cache_path is not None
            else "missing"
        ),
        "existing_stackbuilder_cache_path": (
            str(existing_stackbuilder_cache_path)
            if existing_stackbuilder_cache_path is not None
            else None
        ),
        "signal_cache_pkl_path": (
            str(pkl_path) if pkl_exists else None
        ),
        "signal_cache_manifest_path": (
            str(manifest_path)
            if manifest_exists else None
        ),
        "signal_cache_pkl_present": bool(pkl_exists),
        "signal_cache_manifest_present": bool(
            manifest_exists,
        ),
        "signal_cache_price_source": price_source_value,
        "signal_cache_producer_engine": builder_engine,
        "signal_cache_engine_version": engine_version,
        "signal_cache_build_timestamp": build_timestamp,
        "transformation_possible_without_network": (
            transformation_possible_without_network
        ),
        "recommended_action": recommended_action,
        "blocker_codes": blocker_codes,
    }


def _default_pilot_universe() -> tuple[str, ...]:
    """Deferred-import wrapper for the Phase 6I-52 pilot
    universe (with its built-in dedup/normalization)."""
    import confluence_stackbuilder_rollout_policy as srp
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    return tuple(manifest["seed_universe_tickers"])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_price_cache_rebuild_plan(
    tickers: Optional[Iterable[str]] = None,
    *,
    signal_cache_dir: Optional[Any] = None,
    stackbuilder_price_cache_dir: Optional[Any] = None,
) -> dict[str, Any]:
    """Build the Phase 6I-54a per-ticker plan.

    ``tickers`` defaults to the Phase 6I-52 25-ticker
    pilot universe. The two cache-dir kwargs default to
    ``cache/results`` and ``price_cache/daily``
    respectively. The planner does NOT mutate either
    directory.
    """
    if tickers is None:
        normalized = _default_pilot_universe()
    else:
        normalized = _normalize_tickers(tickers)

    scd = Path(
        signal_cache_dir
        if signal_cache_dir is not None
        else DEFAULT_SIGNAL_CACHE_DIR_RELATIVE
    )
    pcd = Path(
        stackbuilder_price_cache_dir
        if stackbuilder_price_cache_dir is not None
        else DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE
    )

    rows: list[dict[str, Any]] = []
    for ticker in normalized:
        rows.append(_classify_ticker(
            ticker,
            signal_cache_dir=scd,
            stackbuilder_price_cache_dir=pcd,
        ))

    counts: dict[str, int] = {
        a: 0 for a in ALL_RECOMMENDED_ACTIONS
    }
    for r in rows:
        counts[r["recommended_action"]] = (
            counts.get(r["recommended_action"], 0) + 1
        )

    tickers_by_action: dict[str, list[str]] = {
        a: [] for a in ALL_RECOMMENDED_ACTIONS
    }
    for r in rows:
        tickers_by_action[r["recommended_action"]].append(
            r["ticker"],
        )

    # Phase 6I-54a amendment-1: provenance summary over the
    # ``use_existing_signal_cache`` rows. Codex audit
    # observed that the on-disk cache/results PKLs are NOT
    # all produced by the same builder -- some are legacy
    # ``spymaster`` outputs, others are
    # ``signal_engine_cache_refresher`` outputs. The
    # planner does NOT pick one over the other; both are
    # candidates. Phase 6I-54b MUST verify each candidate
    # file via the approved provenance/loader path and
    # actual Close extraction before producing
    # ``price_cache/daily/<TICKER>.parquet``.
    provenance_groups: dict[
        tuple[str, str], list[str]
    ] = {}
    for r in rows:
        if r["recommended_action"] != (
            ACTION_USE_EXISTING_SIGNAL_CACHE
        ):
            continue
        engine = r.get(
            "signal_cache_producer_engine",
        ) or "unknown_engine"
        version = r.get(
            "signal_cache_engine_version",
        ) or "unknown_version"
        key = (str(engine), str(version))
        provenance_groups.setdefault(key, []).append(
            r["ticker"],
        )
    provenance_summary = {
        "groups": [
            {
                "producer_engine": engine,
                "engine_version": version,
                "ticker_count": len(tix),
                "tickers": sorted(tix),
            }
            for (engine, version), tix
            in sorted(provenance_groups.items())
        ],
        "distinct_provenance_count": len(
            provenance_groups,
        ),
        "phase_6i_54b_verification_requirement": (
            "Phase 6I-54b MUST load and verify each "
            "candidate file via the approved "
            "provenance/loader path (NOT raw "
            "pickle.load) and perform actual Close-"
            "series extraction per ticker. Files "
            "produced by different builders / engine "
            "versions are NOT silently treated as "
            "identical -- the writer should record "
            "per-ticker provenance in its own evidence."
        ),
    }

    # Future-write command template Phase 6I-54b will
    # implement. The template is DOCUMENTATION ONLY; this
    # planner never invokes it.
    documented_next_write_command_template = (
        f'<PINNED_PYTHON> stackbuilder_price_cache_writer'
        f'.py --tickers <T1>,<T2>,... '
        f'--signal-cache-dir {DEFAULT_SIGNAL_CACHE_DIR_RELATIVE} '
        f'--stackbuilder-price-cache-dir '
        f'{DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE} '
        f'--format parquet '
        f'--write'
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso_now(),
        "phase": "phase_6i_54a",
        "phase_label": (
            "stackbuilder_price_cache_rebuild_planner"
        ),
        "signal_cache_dir": str(scd),
        "signal_cache_dir_exists": (
            scd.exists() and scd.is_dir()
        ),
        "stackbuilder_price_cache_dir": str(pcd),
        "stackbuilder_price_cache_dir_exists": (
            pcd.exists() and pcd.is_dir()
        ),
        "ticker_count": len(rows),
        "counts_by_recommended_action": counts,
        "tickers_by_recommended_action": (
            tickers_by_action
        ),
        "provenance_summary": provenance_summary,
        "rows": rows,
        "documented_next_write_command_template": (
            documented_next_write_command_template
        ),
        "future_write_contract": {
            "destination_root": str(pcd),
            "output_format_primary": "parquet",
            "output_format_fallback": "csv",
            "required_columns": ["Date", "Close"],
            "files_per_ticker": 1,
            "uses_network": False,
            "uses_yfinance": False,
            "transformation_source": (
                "cache/results/<TICKER>_precomputed_"
                "results.pkl (read via the repo's "
                "verified pickle/provenance loader; NOT "
                "raw pickle.load)"
            ),
            "authorization_notes": (
                "Phase 6I-54b is an authorized-write "
                "phase. Writing to price_cache/daily/ "
                "is NOT gated by --write / "
                "PRJCT9_AUTOMATION_WRITE_AUTH (those "
                "apply to the Phase 6H-5 / 6I-25 / "
                "6I-31 writer family, NOT to "
                "StackBuilder's secondary price cache). "
                "The Phase 6I-54b writer module should "
                "carry its own --write flag mirroring "
                "the Phase 6E-5 pattern."
            ),
        },
        "remaining_limitations": [
            (
                "This planner reads ONLY plain-JSON "
                "manifest sidecars. It does NOT load any "
                "``<TICKER>_precomputed_results.pkl`` "
                "content -- that's Phase 6I-54b's job "
                "via the repo's verified pickle/"
                "provenance loader."
            ),
            (
                "Date-range metadata is NOT extracted in "
                "this phase. The manifest sidecar does "
                "not carry it; only the PKL does. Phase "
                "6I-54b will surface per-ticker date "
                "ranges as part of its write evidence."
            ),
            (
                "``cache/results/`` (the signal-engine "
                "cache) and ``price_cache/daily/`` (the "
                "StackBuilder secondary price cache) are "
                "DISTINCT roots. This planner keeps them "
                "strictly separate; it never "
                "conflates them."
            ),
            (
                "Tickers classified as "
                "``needs_source_refresh`` require the "
                "Phase 6E-5 source-cache refresher to "
                "run first. That refresher uses "
                "yfinance internally; it is its own "
                "explicitly-authorized phase, NOT "
                "implicitly authorized by Phase 6I-54a."
            ),
            (
                "Tickers classified as ``manual_review`` "
                "carry blocker codes explaining why; "
                "common cases are (a) the StackBuilder "
                "price cache already has a file for the "
                "ticker (operator must decide overwrite "
                "vs keep), (b) the signal-cache "
                "manifest sidecar is missing or "
                "unreadable, or (c) the manifest's "
                "price_source is not 'Close'."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=(
            "stackbuilder_price_cache_rebuild_planner"
        ),
        description=(
            "Phase 6I-54a read-only planner for "
            "rebuilding the StackBuilder secondary price "
            "cache (price_cache/daily/) for the Phase "
            "6I-52 pilot universe. STRICTLY READ-ONLY -- "
            "no yfinance, no source refresh, no "
            "StackBuilder invocation, no pickle.load."
        ),
    )
    parser.add_argument(
        "--tickers", default=None,
        help=(
            "Optional comma-separated ticker override. "
            "Default: the Phase 6I-52 25-ticker pilot "
            "universe."
        ),
    )
    parser.add_argument(
        "--signal-cache-dir", default=None,
        help=(
            "Optional override for the signal-engine "
            "cache directory. Default: 'cache/results'."
        ),
    )
    parser.add_argument(
        "--stackbuilder-price-cache-dir", default=None,
        help=(
            "Optional override for the StackBuilder "
            "secondary price cache directory. Default: "
            "'price_cache/daily'."
        ),
    )
    parser.add_argument(
        "--output", default=None,
        help=(
            "Optional JSON output path. Guarded against "
            "landing inside any production root "
            "(including price_cache/daily)."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    if args.output and _path_is_inside_production_root(
        args.output,
    ):
        print(
            json.dumps({
                "error": "output_path_inside_production_root",
                "detail": (
                    f"Refusing to write planner JSON to "
                    f"{args.output!r}: that path is "
                    "inside a production root."
                ),
            }),
            file=sys.stderr,
        )
        return 2

    if args.tickers:
        tickers: Optional[Iterable[str]] = [
            t.strip() for t in args.tickers.split(",")
            if t.strip()
        ]
    else:
        tickers = None

    plan = build_price_cache_rebuild_plan(
        tickers=tickers,
        signal_cache_dir=args.signal_cache_dir,
        stackbuilder_price_cache_dir=(
            args.stackbuilder_price_cache_dir
        ),
    )
    text = json.dumps(plan, indent=2)
    if args.output:
        Path(args.output).write_text(
            text, encoding="utf-8",
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
