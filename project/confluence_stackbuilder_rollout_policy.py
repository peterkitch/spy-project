"""Phase 6I-52: locked StackBuilder rollout policy +
first seed-universe manifest.

Read-only policy artifact. Encodes the six previously-
unresolved StackBuilder policy questions from Phase 6I-50
/ 6I-51 as explicit, versioned, test-pinned decisions for
the FIRST large-universe StackBuilder rollout, and emits
the per-ticker candidate command manifest the operator
would run next (in a separate, explicitly authorized
session).

This module does NOT run StackBuilder. It does NOT modify
any production root. It does NOT invoke yfinance, the
source-cache refresher, the stable-promotion writer, the
Confluence patch writer, the pipeline runner, or any of
the batch engines. It is a policy + seed-universe lock
that downstream phases (Phase 6I-53 supervised batch
execution) consume.

IMPORTANT (Phase 6I-52 amendment-1): stackbuilder.py has
NO ``--write`` flag and does NOT use the
``PRJCT9_AUTOMATION_WRITE_AUTH`` two-key gate that the
Phase 6H-5 / 6I-25 / 6I-31 writer family relies on. When
stackbuilder.py is invoked it writes outputs to
``output/stackbuilder/<TICKER>/`` by default; the only
authorization gate is the separate operator decision to
run the command. Phase 6I-52 does not invoke it.
Phase 6I-53 (the supervised batch execution phase) must
preflight local secondary-price-cache availability
before running each candidate command, because
stackbuilder.py has a yfinance fallback path
(``_fetch_secondary_from_yf``) that kicks in when the
local price source is missing -- running a candidate
without a cache preflight could trigger a network fetch.

What this module IS
-------------------

  * A locked **policy decision record** for six items that
    Phase 6I-50 / 6I-51 left as ``unresolved_questions``:
    ``both_modes``, ``combine_mode``, ``seed_by`` /
    ``optimize_by``, member-universe sizing, re-run
    cadence, invalid-member rotation. Each decision is a
    stable constant with a short rationale.
  * A **first seed-universe manifest** (25 large-cap
    tickers + SPY for continuity with the Phase 6I-49
    pilot). The list is committed as a Python tuple in
    this module; the evidence JSON pins the count + the
    sorted ticker list so a future Codex audit catches
    accidental drift.
  * A **candidate command manifest** -- one StackBuilder
    invocation per ticker, using the Phase 6I-50-
    amendment-1 corrected ``--secondary <TICKER>`` shape
    + the six locked policy flags. Each record carries
    ``authorization_class="stackbuilder_write"``,
    ``requires_separate_operator_authorization=True``,
    ``policy_basis="phase_6i_52_locked_policy"``,
    ``blocked_by_policy_decision=False``.

What this module IS NOT
-----------------------

  * **NOT an executor.** No path through this module
    invokes ``stackbuilder.py`` (or any other engine).
    The candidate commands live as STRINGS in the JSON
    output; the operator runs them (or not) in a
    separate, explicitly authorized session.
  * **NOT the final policy.** The six decisions are
    framed as ``phase_6i_52_locked_policy`` -- the FIRST
    rollout policy. Future phases may revise them as
    operational evidence accumulates.
  * **NOT a writer.** No --write flag, no
    ``PRJCT9_AUTOMATION_WRITE_AUTH`` reference, no on-disk
    write at any layer.

Public surface
--------------

    SCHEMA_VERSION
    POLICY_NAME
    POLICY_VERSION
    POLICY_BASIS

    POLICY_BOTH_MODES
    POLICY_COMBINE_MODE
    POLICY_SEED_BY
    POLICY_OPTIMIZE_BY
    POLICY_TOP_N
    POLICY_BOTTOM_N
    POLICY_MAX_K
    POLICY_SEARCH
    POLICY_BEAM_WIDTH
    POLICY_MIN_TRIGGER_DAYS
    POLICY_RERUN_CADENCE
    POLICY_INVALID_MEMBER_ROTATION

    LOCKED_POLICY_DECISIONS

    FIRST_ROLLOUT_PILOT_UNIVERSE_V1
    SEED_UNIVERSE_SOURCE

    PINNED_INTERPRETER

    build_stackbuilder_rollout_policy_manifest(
        tickers=None, *,
        seed_universe_source=None,
        signal_library_dir=None,
    ) -> dict[str, Any]

    main(argv=None) -> int

CLI
---

    python confluence_stackbuilder_rollout_policy.py \\
        --output md_library/shared/2026-05-15_PHASE_6I52_STACKBUILDER_ROLLOUT_POLICY_EVIDENCE.json

Integration with Phase 6I-51
----------------------------

Phase 6I-51's read-only rollout batch planner has its own
StackBuilder rerun candidate emitter; it gates those
candidates on ``--accept-proposed-stackbuilder-defaults``.
Phase 6I-52 is the *upstream policy artifact* that
formally accepts those defaults. Phase 6I-53 (not yet
implemented) will consume this policy + seed manifest to
actually execute the first supervised StackBuilder batch.

Phase 6I-52 deliberately does NOT modify Phase 6I-51's
rollout batch planner. The 6I-51 planner remains the
authoritative per-ticker command emitter; this module is
the *policy lock* and *seed universe* it consumes
implicitly when the operator passes
``--accept-proposed-stackbuilder-defaults`` plus the seed
universe via ``--tickers`` / ``--universe-file``.

Strictly read-only contract pins
--------------------------------

  * No top-level imports of ``yfinance`` / ``subprocess``
    / ``dash`` / ``signal_engine_cache_refresher`` /
    ``signal_library_stable_promotion_writer`` /
    ``multiwindow_k_confluence_patch_writer`` /
    ``confluence_pipeline_runner`` /
    ``daily_board_automation_*`` / engine modules
    (``stackbuilder`` / ``onepass`` / ``impactsearch`` /
    ``trafficflow`` / ``spymaster`` / ``confluence``).
  * No ``write=True`` keyword argument passed to any
    callable.
  * No ``--write`` argument on the CLI.
  * No on-disk write at any layer except the optional
    ``--output`` JSON, which is guarded against landing
    inside a production root.
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
    "confluence_stackbuilder_rollout_policy_v1"
)
POLICY_NAME: str = "phase_6i_52_locked_policy"
POLICY_VERSION: str = "v1"
POLICY_BASIS: str = "phase_6i_52_locked_policy"


# Locked policy decision #1: ``both_modes``. The Phase 6I-50
# StackBuilder observed-defaults block records ``False`` as
# the source default; the Phase 6I-52 policy adopts that
# value explicitly for the first rollout.
POLICY_BOTH_MODES: bool = False

# Locked policy decision #2: ``combine_mode``. Phase 6I-50
# amendment-1 confirmed the CLI exposes
# ``--combine-mode choices=['intersection','union']
# default='intersection'``. The first rollout keeps the
# conservative all-members-agree path.
POLICY_COMBINE_MODE: str = "intersection"

# Locked policy decision #3: ``seed_by`` / ``optimize_by``.
# Both pinned to ``total_capture`` -- the observed
# stackbuilder.py default and the existing TrafficFlow-
# style sort axis.
POLICY_SEED_BY: str = "total_capture"
POLICY_OPTIMIZE_BY: str = "total_capture"

# Phase 6I-52 amendment-1: the original block claimed a
# ``member_universe_size=12`` locked decision (because the
# legacy SPY seed-run directory name carries 12 ticker-
# mode tokens). That value is NOT enforced by any command
# the planner emits -- the generated argv pins
# ``--top-n 20 --bottom-n 20 --max-k 6 --search beam
# --beam-width 12``, which is the StackBuilder
# candidate-selection setting, not a member-count
# guarantee. Amendment-1 replaces the misleading
# ``member_universe_size=12`` decision with explicit
# StackBuilder command-parameter locks (below). The "SPY
# historical seed run had 12 members" observation is now
# background context only, NOT a locked launch
# guarantee.
POLICY_TOP_N: int = 20
POLICY_BOTTOM_N: int = 20
POLICY_MAX_K: int = 6
POLICY_SEARCH: str = "beam"
POLICY_BEAM_WIDTH: int = 12
POLICY_MIN_TRIGGER_DAYS: int = 30

# Locked policy decision #5: re-run cadence. Pinned to
# ``manual_supervised`` -- no scheduler. Phase 6I-53 will
# be the FIRST supervised batch execution, one command per
# ticker, gated by the existing two-key writer
# authorization at invocation time.
POLICY_RERUN_CADENCE: str = "manual_supervised"

# Locked policy decision #6: invalid-member rotation
# policy. Pinned to ``partial_effective_members_with_warning``
# -- when a member is flagged ``invalid_or_delisted``
# (Phase 6I-43), the StackBuilder run still completes and
# the downstream Confluence partial-payload path
# (Phase 6I-46 / 6I-47 / 6I-48 / 6I-49) carries the
# partial result with the visible "!" warning. No
# auto-substitution in the first rollout.
POLICY_INVALID_MEMBER_ROTATION: str = (
    "partial_effective_members_with_warning"
)


LOCKED_POLICY_DECISIONS: dict[str, dict[str, Any]] = {
    "both_modes": {
        "value": POLICY_BOTH_MODES,
        "rationale": (
            "Keep the first large-universe pass aligned "
            "with the observed StackBuilder default "
            "(``--both-modes`` is a store_true flag with "
            "no-set => False). Do not double-compute "
            "Buy + Short candidates until the multi-"
            "ticker board path is proven on at least one "
            "supervised batch."
        ),
    },
    "combine_mode": {
        "value": POLICY_COMBINE_MODE,
        "rationale": (
            "The CLI exposes "
            "``--combine-mode choices=['intersection',"
            "'union'] default='intersection'``. "
            "Phase 6I-52 keeps the conservative all-"
            "members-agree path; ``union`` is a strict "
            "operator decision and not yet on the table."
        ),
    },
    "seed_by": {
        "value": POLICY_SEED_BY,
        "rationale": (
            "Pinned to ``total_capture`` -- the "
            "stackbuilder.py default and the existing "
            "TrafficFlow-style sort axis. Other axes "
            "(``sharpe``) require their own validation "
            "pass before a large-universe sweep."
        ),
    },
    "optimize_by": {
        "value": POLICY_OPTIMIZE_BY,
        "rationale": (
            "Pinned to ``total_capture`` (matches "
            "seed_by). stackbuilder.py treats unset "
            "``--optimize-by`` as auto-resolving to "
            "``--seed-by``; the first rollout pins both "
            "explicitly for auditability."
        ),
    },
    "stackbuilder_command_parameters": {
        "top_n": POLICY_TOP_N,
        "bottom_n": POLICY_BOTTOM_N,
        "max_k": POLICY_MAX_K,
        "search": POLICY_SEARCH,
        "beam_width": POLICY_BEAM_WIDTH,
        "min_trigger_days": POLICY_MIN_TRIGGER_DAYS,
        "rationale": (
            "Phase 6I-52 amendment-1: locks the actual "
            "StackBuilder candidate-selection parameters "
            "that the generated argv pins. ``top_n=20`` "
            "and ``bottom_n=20`` are the candidate ranks "
            "stackbuilder.py considers; ``max_k=6`` is "
            "the maximum stack size; ``search='beam'`` + "
            "``beam_width=12`` are the beam-search "
            "controls; ``min_trigger_days=30`` is the "
            "minimum trigger-day floor. These are the "
            "stackbuilder.py argparse defaults and the "
            "Phase 6I-50 proposed launch defaults. The "
            "original Phase 6I-52 block instead claimed a "
            "``member_universe_size=12`` decision -- a "
            "background observation about the legacy SPY "
            "seed-run directory shape, NOT something any "
            "generated command enforces. Amendment-1 "
            "rephrases the lock as command parameters so "
            "the test suite + evidence JSON match the "
            "actual argv."
        ),
    },
    "rerun_cadence": {
        "value": POLICY_RERUN_CADENCE,
        "rationale": (
            "Pinned to ``manual_supervised``. No "
            "scheduler / cron / automation runner. "
            "Phase 6I-53 will be the first supervised "
            "batch execution using this locked policy; "
            "each ticker is a separate explicitly "
            "authorized invocation."
        ),
    },
    "invalid_member_rotation": {
        "value": POLICY_INVALID_MEMBER_ROTATION,
        "rationale": (
            "Pinned to "
            "``partial_effective_members_with_warning``. "
            "When a member is flagged "
            "``invalid_or_delisted`` (Phase 6I-43), the "
            "downstream partial-payload contract "
            "(Phase 6I-46 / 6I-47 / 6I-48 / 6I-49) "
            "carries the partial result honestly with "
            "the visible ``!`` warning, exactly as SPY "
            "does today. No auto-substitution in the "
            "first rollout -- that's a future-phase "
            "decision pending operational evidence."
        ),
    },
}


# Phase 6I-52 first-rollout pilot universe (v1). 25 large-
# cap equities + SPY for continuity with the proven
# Phase 6I-49 pilot. All names have deep yfinance history
# and well-known liquidity. Curated explicitly for the
# FIRST rollout -- this is NOT a final universe.
#
# The list is committed as a Python tuple here so a future
# Codex audit catches drift; the evidence JSON also pins
# the sorted list + count.
FIRST_ROLLOUT_PILOT_UNIVERSE_V1: tuple[str, ...] = (
    "SPY",
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "AVGO",
    "ORCL",
    "ADBE",
    "CRM",
    "AMD",
    "QCOM",
    "CSCO",
    "JPM",
    "BRK-B",
    "V",
    "MA",
    "JNJ",
    "WMT",
    "PG",
    "HD",
    "KO",
    "MCD",
    "JPM",  # intentional duplicate -- the public-entry
            # normalizer must dedupe. Pinned by the
            # ``test_seed_universe_dedupes_and_normalizes``
            # test below.
)


SEED_UNIVERSE_SOURCE: str = (
    "phase_6i_52_first_rollout_pilot_universe_v1"
)


# Pinned interpreter path (matches Phase 6I-50 / 6I-51).
PINNED_INTERPRETER: str = (
    "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/"
    "spyproject2/python.exe"
)


# Production-root relative paths guarded by --output.
PRODUCTION_ROOT_RELATIVE_PATHS: tuple[str, ...] = (
    "cache/results",
    "cache/status",
    "output/research_artifacts",
    "output/stackbuilder",
    "signal_library/data/stable",
)


# Items deliberately deferred for later phases. Phase 6I-53
# will execute the first supervised batch with this
# locked policy; future phases may revisit these.
UNRESOLVED_OR_DEFERRED_POLICY_ITEMS: tuple[str, ...] = (
    "per_ticker_member_universe_sizing: the first rollout "
    "fixes member universe size at 12 for every ticker. "
    "A future phase may introduce market-cap-tuned or "
    "liquidity-tuned per-ticker sizing once we have "
    "operational evidence from the supervised batch.",
    "automated_rerun_cadence: the first rollout is "
    "manual / supervised. A future phase may introduce a "
    "scheduler (daily / weekly / on-invalid-member-"
    "detected) once the supervised batch path is "
    "proven.",
    "invalid_member_auto_substitution: the first rollout "
    "uses the existing partial-effective-members + ``!`` "
    "warning path. A future phase may auto-substitute "
    "another candidate when a member is flagged "
    "``invalid_or_delisted`` -- but only after the "
    "supervised batch confirms the partial path is "
    "stable at scale.",
    "combine_mode_union_evaluation: the first rollout "
    "pins ``intersection``. A future evaluation phase "
    "may A/B the ``union`` mode against the same "
    "ticker universe before deciding whether to switch.",
    "seed_by_sharpe_evaluation: the first rollout pins "
    "``total_capture``. A future evaluation phase may "
    "A/B against ``sharpe`` once the total_capture "
    "rollout produces stable evidence.",
    "second_rollout_universe_size: the first rollout "
    "pilot universe is 25 explicit large-cap names. The "
    "next universe expansion (50 / 100 / 250 / full "
    "stackbuilder-existing-runs / GTL-discovered) is a "
    "future-phase decision.",
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


def _quote(s: Any) -> str:
    """Display-only argv-token quoting (matches the
    Phase 6I-51 planner's _quote)."""
    text = str(s)
    if not text:
        return '""'
    needs_quotes = any(c in text for c in " \t\"'\\")
    if needs_quotes:
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_stackbuilder_rollout_policy_manifest(
    tickers: Optional[Iterable[str]] = None,
    *,
    seed_universe_source: Optional[str] = None,
    signal_library_dir: Optional[Any] = None,
) -> dict[str, Any]:
    """Build the locked Phase 6I-52 policy manifest +
    per-ticker StackBuilder candidate command list.

    ``tickers`` defaults to
    ``FIRST_ROLLOUT_PILOT_UNIVERSE_V1`` (with normalization
    + dedup). ``seed_universe_source`` defaults to the
    pilot-universe-v1 label; callers can override it when
    feeding a different universe through the same locked
    policy. ``signal_library_dir`` is threaded through to
    each candidate command's ``--signal-lib-dir`` flag
    when supplied.
    """
    raw_tickers = (
        tuple(tickers)
        if tickers is not None
        else FIRST_ROLLOUT_PILOT_UNIVERSE_V1
    )
    normalized = _normalize_tickers(raw_tickers)
    source_label = (
        seed_universe_source or SEED_UNIVERSE_SOURCE
    )

    sl_dir = (
        str(signal_library_dir)
        if signal_library_dir is not None else None
    )

    commands: list[dict[str, Any]] = []
    for ticker in normalized:
        argv = [
            PINNED_INTERPRETER,
            "stackbuilder.py",
            "--secondary", ticker,
            "--top-n", "20",
            "--bottom-n", "20",
            "--max-k", "6",
            "--search", "beam",
            "--beam-width", "12",
            "--seed-by", POLICY_SEED_BY,
            "--optimize-by", POLICY_OPTIMIZE_BY,
            "--min-trigger-days", "30",
            "--combine-mode", POLICY_COMBINE_MODE,
        ]
        if sl_dir is not None:
            argv += ["--signal-lib-dir", sl_dir]
        # Phase 6I-52 deliberately omits --both-modes (the
        # policy pins both_modes=False; the absence of the
        # flag matches the store_true default).
        commands.append({
            "ticker": ticker,
            "command_label": (
                "stackbuilder_first_rollout_run"
            ),
            "argv": argv,
            "command": " ".join(_quote(a) for a in argv),
            "authorization_class": "stackbuilder_write",
            "requires_separate_operator_authorization": True,
            "policy_basis": POLICY_BASIS,
            "blocked_by_policy_decision": False,
            "notes": (
                "Phase 6I-52 locked-policy StackBuilder "
                "run for the first large-universe rollout "
                "pilot. The command is READY_FOR_"
                "AUTHORIZATION but STILL is not executed "
                "by this policy module. IMPORTANT: "
                "stackbuilder.py has NO --write flag and "
                "does NOT use PRJCT9_AUTOMATION_WRITE_AUTH "
                "-- it writes outputs to "
                "output/stackbuilder/<TICKER>/ by default "
                "WHENEVER INVOKED. The only authorization "
                "gate is the separate operator decision "
                "to actually run the command. Phase 6I-52 "
                "does not invoke it. Phase 6I-53 must "
                "preflight local secondary-price-cache "
                "availability before running each "
                "command, because stackbuilder.py falls "
                "back to a live yfinance fetch "
                "(``_fetch_secondary_from_yf``) when the "
                "local price source is missing -- so "
                "running a candidate command without a "
                "cache preflight could trigger a network "
                "fetch."
            ),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "policy_name": POLICY_NAME,
        "policy_version": POLICY_VERSION,
        "policy_basis": POLICY_BASIS,
        "generated_at": _iso_now(),
        "seed_universe_source": source_label,
        "seed_universe_count": len(normalized),
        "seed_universe_tickers": list(normalized),
        "locked_policy_decisions": LOCKED_POLICY_DECISIONS,
        "unresolved_or_deferred_policy_items": list(
            UNRESOLVED_OR_DEFERRED_POLICY_ITEMS,
        ),
        "command_manifest": commands,
        "remaining_limitations": [
            (
                "This module is the POLICY ARTIFACT only. "
                "It does NOT execute any StackBuilder "
                "command. The candidate command STRINGS "
                "live in the JSON output; the operator "
                "runs each command (or not) in a "
                "separate, explicitly authorized "
                "session."
            ),
            (
                "Phase 6I-53 is the next operational "
                "step: the first supervised StackBuilder "
                "batch execution using this locked "
                "policy + seed universe. Phase 6I-52 "
                "does NOT pre-authorize Phase 6I-53; "
                "Phase 6I-53 will be a separate prompt "
                "with its own evidence pass."
            ),
            (
                "The seed universe v1 is a CONSERVATIVE "
                "PILOT (25 large-cap equities + SPY for "
                "continuity), NOT the final universe. "
                "Universe expansion is a deferred policy "
                "item; see "
                "``unresolved_or_deferred_policy_items"
                ".second_rollout_universe_size``."
            ),
            (
                "The locked policy decisions are FIRST-"
                "ROLLOUT-SCOPED. Future phases may revise "
                "them as operational evidence "
                "accumulates; each revision should bump "
                "POLICY_VERSION."
            ),
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_stackbuilder_rollout_policy",
        description=(
            "Phase 6I-52 read-only locked StackBuilder "
            "rollout policy + first seed-universe "
            "manifest. STRICTLY READ-ONLY -- never runs "
            "StackBuilder or any other engine, never "
            "executes any candidate command. Emits the "
            "policy manifest as JSON to stdout (or to "
            "--output)."
        ),
    )
    parser.add_argument(
        "--tickers", default=None,
        help=(
            "Optional comma-separated ticker override. "
            "Default: the first rollout pilot universe "
            "v1 (committed in this module)."
        ),
    )
    parser.add_argument(
        "--seed-universe-source", default=None,
        help=(
            "Optional label for the seed universe "
            "source. Default: "
            "``phase_6i_52_first_rollout_pilot_universe"
            "_v1``."
        ),
    )
    parser.add_argument(
        "--signal-library-dir", default=None,
        help=(
            "Optional --signal-lib-dir value threaded "
            "through to each candidate command."
        ),
    )
    parser.add_argument(
        "--output", default=None,
        help=(
            "Optional JSON output path. Guarded against "
            "landing inside a production root."
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
                    f"Refusing to write the rollout "
                    f"policy JSON to {args.output!r}: "
                    "that path is inside one of the "
                    "documented production roots "
                    f"({PRODUCTION_ROOT_RELATIVE_PATHS!r})"
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

    manifest = build_stackbuilder_rollout_policy_manifest(
        tickers=tickers,
        seed_universe_source=args.seed_universe_source,
        signal_library_dir=args.signal_library_dir,
    )

    text = json.dumps(manifest, indent=2)
    if args.output:
        Path(args.output).write_text(
            text, encoding="utf-8",
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
