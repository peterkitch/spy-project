"""Phase 6I-53: StackBuilder pilot-batch preflight.

Read-only module. For a given ticker list (default: the
Phase 6I-52 first rollout pilot universe), check whether
``stackbuilder.load_secondary_prices(<TICKER>)`` would
find a local price-cache file or fall through to the
``_fetch_secondary_from_yf`` yfinance fallback. Classify
each ticker accordingly. Emit a per-ticker preflight
table.

Why this module exists
----------------------

The Phase 6I-52 policy module emits 25 candidate
StackBuilder commands. Running each command invokes
``stackbuilder.py``, which calls
``load_secondary_prices(secondary_ticker)``. That helper
checks five candidate paths under
``$PRICE_CACHE_DIR`` (default ``price_cache/daily``):

    {PRICE_CACHE_DIR}/{TICKER}.parquet
    {PRICE_CACHE_DIR}/{TICKER}.csv
    {PRICE_CACHE_DIR}/{TICKER_no_caret}.parquet
    {PRICE_CACHE_DIR}/{TICKER_no_caret}.csv
    {PRICE_CACHE_DIR}/{TICKER}/daily.parquet

If none of those exist, ``load_secondary_prices`` falls
through to ``_fetch_secondary_from_yf`` and hits the live
yfinance network.

Phase 6I-52 amendment-1 added a "Phase 6I-53 must
preflight local secondary-price-cache availability"
warning to every command's notes. This module is that
preflight. It is **read-only**: it does NOT load any
parquet/csv into memory (only ``Path.exists()`` /
``Path.is_file()`` checks), it does NOT call
``stackbuilder.load_secondary_prices``, it does NOT call
``_fetch_secondary_from_yf``, and it does NOT invoke any
subprocess.

The Phase 6I-53 supervised pilot batch consumes this
module's output to decide which tickers may safely run
StackBuilder (cache-pass) and which must be skipped
(cache-missing -> would trigger yfinance).

Public surface
--------------

    SCHEMA_VERSION
    DEFAULT_PRICE_CACHE_DIR_RELATIVE
    PREFLIGHT_STATUS_PASS
    PREFLIGHT_STATUS_SKIP_MISSING_CACHE
    ALL_PREFLIGHT_STATUSES

    build_preflight_table(
        tickers=None, *,
        price_cache_dir=None,
        env_overrides=None,
    ) -> dict[str, Any]

    main(argv=None) -> int

CLI
---

    python confluence_stackbuilder_pilot_preflight.py \\
        --output md_library/shared/2026-05-15_PHASE_6I53_PREFLIGHT.json

    # Override price cache dir explicitly:
    python confluence_stackbuilder_pilot_preflight.py \\
        --price-cache-dir price_cache/daily

Strictly read-only contract pins
--------------------------------

  * No top-level imports of ``yfinance`` / ``subprocess``
    / ``dash`` / writer modules / engine modules
    (``stackbuilder`` / ``onepass`` / ``impactsearch`` /
    ``trafficflow`` / ``spymaster`` / ``confluence``).
  * No ``--write`` argument on the CLI.
  * No on-disk write at any layer except the optional
    ``--output`` JSON path, which is guarded against
    landing inside a production root.
  * Filesystem reads are limited to ``Path.exists()`` /
    ``Path.is_file()`` checks against the five candidate
    cache paths per ticker; no file content is loaded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Stable constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION: str = (
    "confluence_stackbuilder_pilot_preflight_v1"
)


# ``stackbuilder.py`` reads this from the ``PRICE_CACHE_DIR``
# environment variable, defaulting to ``price_cache/daily``
# (stackbuilder.py:225). This module mirrors that default
# explicitly so the preflight resolves the same paths.
DEFAULT_PRICE_CACHE_DIR_RELATIVE: str = "price_cache/daily"


PREFLIGHT_STATUS_PASS: str = "pass"
PREFLIGHT_STATUS_SKIP_MISSING_CACHE: str = (
    "skip_missing_cache_would_fetch_yfinance"
)


ALL_PREFLIGHT_STATUSES: tuple[str, ...] = (
    PREFLIGHT_STATUS_PASS,
    PREFLIGHT_STATUS_SKIP_MISSING_CACHE,
)


# Production-root relative paths guarded against by
# ``--output``. Same set every Phase 6I-50+ module uses.
PRODUCTION_ROOT_RELATIVE_PATHS: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
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


def _candidate_paths_for_ticker(
    ticker: str,
    *,
    price_cache_dir: Path,
) -> list[Path]:
    """Return the five candidate cache paths that
    ``stackbuilder.load_secondary_prices`` checks, in the
    same order.

    Mirrors stackbuilder.py:530-556. The caret-stripped
    variants matter for index tickers (``^GSPC`` ->
    ``GSPC``).
    """
    sec = (ticker or "").upper()
    sec_clean = sec.replace("^", "")
    return [
        price_cache_dir / f"{sec}.parquet",
        price_cache_dir / f"{sec}.csv",
        price_cache_dir / f"{sec_clean}.parquet",
        price_cache_dir / f"{sec_clean}.csv",
        price_cache_dir / sec / "daily.parquet",
    ]


def _classify_ticker(
    ticker: str,
    *,
    price_cache_dir: Path,
) -> dict[str, Any]:
    """Check the five candidate paths; classify pass vs
    skip-missing-cache. Read-only: only ``Path.exists()``
    + ``Path.is_file()`` checks; no file content load."""
    candidates = _candidate_paths_for_ticker(
        ticker, price_cache_dir=price_cache_dir,
    )
    resolved: Optional[str] = None
    for p in candidates:
        try:
            if p.exists() and p.is_file():
                resolved = str(p)
                break
        except OSError:
            continue
    if resolved is not None:
        return {
            "ticker": ticker,
            "local_price_cache_available": True,
            "resolved_cache_path": resolved,
            "would_fetch_yfinance": False,
            "preflight_status": PREFLIGHT_STATUS_PASS,
            "candidate_paths_checked": [
                str(p) for p in candidates
            ],
        }
    return {
        "ticker": ticker,
        "local_price_cache_available": False,
        "resolved_cache_path": None,
        "would_fetch_yfinance": True,
        "preflight_status": (
            PREFLIGHT_STATUS_SKIP_MISSING_CACHE
        ),
        "candidate_paths_checked": [
            str(p) for p in candidates
        ],
    }


def _default_pilot_universe() -> tuple[str, ...]:
    """Deferred-import wrapper that pulls the 25-ticker
    pilot universe from the Phase 6I-52 policy module
    (with its in-built dedup/normalization)."""
    import confluence_stackbuilder_rollout_policy as srp
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    return tuple(manifest["seed_universe_tickers"])


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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_preflight_table(
    tickers: Optional[Iterable[str]] = None,
    *,
    price_cache_dir: Optional[Any] = None,
    env_overrides: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Build the Phase 6I-53 preflight table.

    ``tickers`` defaults to the Phase 6I-52 pilot
    universe. ``price_cache_dir`` defaults to the
    ``PRICE_CACHE_DIR`` env var, then to
    ``DEFAULT_PRICE_CACHE_DIR_RELATIVE`` ("price_cache/
    daily"). ``env_overrides`` lets tests inject a fake
    env without mutating ``os.environ``.
    """
    if tickers is None:
        normalized = _default_pilot_universe()
    else:
        normalized = _normalize_tickers(tickers)

    # Resolve price-cache dir. The lookup order matches
    # stackbuilder.py:225 (env var first, then default).
    env = env_overrides if env_overrides is not None else (
        os.environ
    )
    env_pcd = env.get("PRICE_CACHE_DIR")
    if price_cache_dir is not None:
        pcd_resolved = Path(price_cache_dir)
    elif env_pcd:
        pcd_resolved = Path(env_pcd)
    else:
        pcd_resolved = Path(
            DEFAULT_PRICE_CACHE_DIR_RELATIVE,
        )

    rows: list[dict[str, Any]] = []
    for ticker in normalized:
        rows.append(_classify_ticker(
            ticker, price_cache_dir=pcd_resolved,
        ))

    pass_tickers = sorted(
        r["ticker"] for r in rows
        if r["preflight_status"] == PREFLIGHT_STATUS_PASS
    )
    skip_tickers = sorted(
        r["ticker"] for r in rows
        if r["preflight_status"]
        == PREFLIGHT_STATUS_SKIP_MISSING_CACHE
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso_now(),
        "price_cache_dir_used": str(pcd_resolved),
        "price_cache_dir_exists": (
            pcd_resolved.exists()
            and pcd_resolved.is_dir()
        ),
        "default_price_cache_dir_relative": (
            DEFAULT_PRICE_CACHE_DIR_RELATIVE
        ),
        "ticker_count": len(rows),
        "pass_count": len(pass_tickers),
        "skip_count": len(skip_tickers),
        "tickers_passing_preflight": pass_tickers,
        "tickers_skipped_missing_cache": skip_tickers,
        "rows": rows,
        "stackbuilder_load_secondary_prices_source": (
            "stackbuilder.py:530-556 -- the helper checks "
            "five candidate paths in PRICE_CACHE_DIR "
            "(default 'price_cache/daily'), then falls "
            "through to _fetch_secondary_from_yf "
            "(stackbuilder.py:506). Phase 6I-53 preflight "
            "MUST be cache-pass for every ticker before "
            "StackBuilder is invoked, otherwise the run "
            "will trigger a live yfinance fetch."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_stackbuilder_pilot_preflight",
        description=(
            "Phase 6I-53 read-only StackBuilder pilot-"
            "batch preflight. STRICTLY READ-ONLY -- "
            "never runs StackBuilder, never invokes "
            "yfinance, never loads any parquet/csv into "
            "memory. Emits a per-ticker preflight table "
            "as JSON to stdout (or --output)."
        ),
    )
    parser.add_argument(
        "--tickers", default=None,
        help=(
            "Optional comma-separated ticker override. "
            "Default: the Phase 6I-52 first rollout "
            "pilot universe (25 tickers)."
        ),
    )
    parser.add_argument(
        "--price-cache-dir", default=None,
        help=(
            "Optional override for the price-cache "
            "directory. Default: PRICE_CACHE_DIR env "
            "var, then 'price_cache/daily'."
        ),
    )
    parser.add_argument(
        "--output", default=None,
        help=(
            "Optional JSON output path. Guarded against "
            "landing inside any production root."
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
                    f"Refusing to write preflight JSON to "
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

    table = build_preflight_table(
        tickers=tickers,
        price_cache_dir=args.price_cache_dir,
    )
    text = json.dumps(table, indent=2)
    if args.output:
        Path(args.output).write_text(
            text, encoding="utf-8",
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
