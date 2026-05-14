"""Phase 6I-42: read-only local runtime overlay providers
for the Confluence board.

The Phase 6I-40 contract added stable per-row sub-dicts for
``data_completeness``, ``current_signal_status_block``, and
``flip_risk`` -- with injection seams on the Phase 6I-34
ranking export so a provider can populate them at build
time. The Phase 6I-41 static renderer ALREADY surfaces the
warning symbol, current-status badge, and latest-price
column.

This module ships **the actual local providers**: it scans
local read-only sources (the on-disk Confluence artifact,
the local ``cache/results`` PKLs, the local StackBuilder
member surface, and the local signal-library directory)
and emits a per-ticker overlay report. The report can be
plugged straight into the Phase 6I-34 export as both a
``member_completeness_provider_callable`` and a
``live_price_provider_callable`` so the eventual website
HTML carries the warning symbols, the current-signal-
status badges, and the latest local price values.

What this module IS
-------------------

* Strictly read-only.
* A defensive local-data scanner: every external read is
  guarded; a missing / unreadable / unfamiliar artifact
  returns ``unknown`` / ``stale`` honestly, never an
  exception.
* The default cache loader uses
  ``provenance_manifest.load_verified_pickle_artifact`` so
  the B12 raw-pickle ban is preserved.
* The default member-completeness provider is conservative:
  it returns ``has_incomplete_build_members=False`` unless
  an injected ``stackbuilder_member_callable`` (or the
  caller-supplied adapter diagnostic) actually flags a
  member as missing / stale / invalid. TEF-style invalid
  members surface ONLY when an upstream provider says so;
  the module never invents them.

What this module IS NOT
-----------------------

* NOT a writer / refresher / pipeline runner / batch
  engine.
* NOT a live-data fetcher. There is NO ``yfinance``
  import, NO ``subprocess``, NO HTTP / socket call. Live
  data is exclusively whatever has been written to local
  cache already.
* NOT a fabricator: an unknown cache shape, a missing
  PKL, or a stale StackBuilder row surfaces ``unknown`` /
  ``stale`` with an explicit ``issue_code``, never a
  fabricated locked / complete state.
* NOT a relaxer of strict Phase 6I-20 multi-window
  truth. The overlay populates Phase 6I-40 sub-dicts
  only; rank-eligibility STILL requires the full Phase
  6I-20 60-cell payload upstream.

Public surface
--------------

    SCHEMA_VERSION
    SIGNAL_STATUS_*
    COMPLETENESS_STATUS_*
    ISSUE_CODE_*

    @dataclass BoardRuntimeOverlayReport

    build_board_runtime_overlays(
        tickers, *,
        artifact_root, cache_dir,
        stackbuilder_root=None,
        signal_library_dir=None,
        current_as_of_date=None,
        cache_loader_callable=None,
        adapter_diagnostic_callable=None,
        stackbuilder_member_callable=None,
    ) -> BoardRuntimeOverlayReport

    make_member_completeness_provider(report) -> Callable
    make_live_price_provider(report) -> Callable

    main(argv=None) -> int           # CLI entry
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


SCHEMA_VERSION: str = "confluence_board_runtime_overlays_v1"


# ---------------------------------------------------------------------------
# Stable label / status taxonomies
# ---------------------------------------------------------------------------

SIGNAL_STATUS_LOCKED: str = "locked"
SIGNAL_STATUS_PROVISIONAL: str = "provisional"
SIGNAL_STATUS_STALE: str = "stale"
SIGNAL_STATUS_BLOCKED: str = "blocked"
SIGNAL_STATUS_UNKNOWN: str = "unknown"

ALL_SIGNAL_STATUSES: tuple[str, ...] = (
    SIGNAL_STATUS_LOCKED,
    SIGNAL_STATUS_PROVISIONAL,
    SIGNAL_STATUS_STALE,
    SIGNAL_STATUS_BLOCKED,
    SIGNAL_STATUS_UNKNOWN,
)


COMPLETENESS_STATUS_COMPLETE: str = "complete"
COMPLETENESS_STATUS_PARTIAL: str = "partial"
COMPLETENESS_STATUS_BLOCKED: str = "blocked"
COMPLETENESS_STATUS_UNKNOWN: str = "unknown"

ALL_COMPLETENESS_STATUSES: tuple[str, ...] = (
    COMPLETENESS_STATUS_COMPLETE,
    COMPLETENESS_STATUS_PARTIAL,
    COMPLETENESS_STATUS_BLOCKED,
    COMPLETENESS_STATUS_UNKNOWN,
)


SIGNAL_UPDATE_SOURCE_LOCAL_CACHE: str = "local_cache"
SIGNAL_UPDATE_SOURCE_ARTIFACT: str = "artifact"
SIGNAL_UPDATE_SOURCE_UNAVAILABLE: str = "unavailable"


# Stable issue-code taxonomy: surfaced when the overlay
# can't classify confidently. The renderer can choose to
# surface them in the warning tooltip.
ISSUE_CODE_CACHE_MISSING: str = "cache_pkl_missing"
ISSUE_CODE_CACHE_UNREADABLE: str = "cache_pkl_unreadable"
ISSUE_CODE_CACHE_UNKNOWN_SHAPE: str = "cache_pkl_unknown_shape"
ISSUE_CODE_CACHE_DATE_UNPARSABLE: str = (
    "cache_date_unparsable"
)
ISSUE_CODE_CACHE_STALE: str = "cache_stale_vs_cutoff"
ISSUE_CODE_PROVIDER_RAISED: str = (
    "completeness_provider_raised"
)
ISSUE_CODE_NO_LOCAL_INPUTS: str = "no_local_inputs_supplied"


DATA_WARNING_SYMBOL: str = "!"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class BoardRuntimeOverlayReport:
    schema_version: str
    generated_at: str
    inspected_count: int
    overlays_by_ticker: dict[str, dict[str, Any]]
    data_completeness_by_ticker: dict[str, dict[str, Any]]
    latest_price_by_ticker: dict[str, dict[str, Any]]
    current_signal_status_by_ticker: dict[
        str, dict[str, Any]
    ]
    issue_codes: dict[str, list[str]]
    summary: dict[str, Any]
    remaining_limitations: tuple[str, ...] = field(
        default_factory=tuple,
    )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "inspected_count": int(self.inspected_count),
            "overlays_by_ticker": {
                t: dict(v)
                for t, v in self.overlays_by_ticker.items()
            },
            "data_completeness_by_ticker": {
                t: dict(v)
                for t, v in (
                    self.data_completeness_by_ticker.items()
                )
            },
            "latest_price_by_ticker": {
                t: dict(v)
                for t, v in (
                    self.latest_price_by_ticker.items()
                )
            },
            "current_signal_status_by_ticker": {
                t: dict(v)
                for t, v in (
                    self
                    .current_signal_status_by_ticker
                    .items()
                )
            },
            "issue_codes": {
                t: list(v)
                for t, v in self.issue_codes.items()
            },
            "summary": dict(self.summary),
            "remaining_limitations": list(
                self.remaining_limitations,
            ),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )


def _parse_iso_date(value: Any) -> Optional[Any]:
    """Defensive ISO / YYYY-MM-DD parser. Returns the
    parsed ``date`` or ``None``; never raises."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            # Treat ints / floats as epoch seconds.
            return datetime.fromtimestamp(
                float(value), tz=timezone.utc,
            ).date()
        s = str(value).strip()
    except Exception:
        return None
    if not s:
        return None
    # Try ISO 8601 datetime.
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        pass
    # Try YYYY-MM-DD.
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _default_cache_loader(
    path: Path,
) -> tuple[Optional[Mapping[str, Any]], list[str]]:
    """Default local cache PKL loader.

    Uses the central provenance loader
    (``provenance_manifest.load_verified_pickle_artifact``)
    via a deferred import so the module's import surface
    stays small AND the B12 raw-pickle ban is preserved.

    Returns ``(payload_or_None, issues)``. ``payload`` is
    the dict the central loader returned, or ``None`` when
    the artifact is unreadable / type-mismatched / missing.
    ``issues`` is a list of stable issue codes describing
    why a None payload happened.
    """
    issues: list[str] = []
    p = Path(path)
    if not p.exists() or not p.is_file():
        issues.append(ISSUE_CODE_CACHE_MISSING)
        return None, issues
    try:
        import provenance_manifest as _pm  # local deferred
        data, result = _pm.load_verified_pickle_artifact(
            p, strict=False,
        )
    except Exception:
        # Central loader itself raised -- defensive
        # fallback: report unreadable rather than crash.
        issues.append(ISSUE_CODE_CACHE_UNREADABLE)
        return None, issues
    if data is None:
        issues.append(ISSUE_CODE_CACHE_UNREADABLE)
        return None, issues
    if not isinstance(data, Mapping):
        issues.append(ISSUE_CODE_CACHE_UNREADABLE)
        return None, issues
    return data, issues


# Known cache-payload keys we'll probe for the latest
# close + date. Order matters: the first key that yields
# a usable (price, date) pair wins.
_PRICE_KEYS: tuple[str, ...] = (
    "target_close", "close", "Close", "Adj Close",
    "adjusted_close",
)
_DATE_KEYS: tuple[str, ...] = (
    "dates", "date_index", "Date", "index",
)


def _last_scalar(value: Any) -> Optional[float]:
    """Defensively return the last numeric value of a
    sequence-like object, OR the scalar numeric value
    itself when ``value`` is already a number. Returns
    ``None`` on any failure.

    Phase 6I-42 amendment-1: intercepts scalar
    ``int`` / ``float`` (and string-formatted numerics)
    BEFORE the ``len() / seq[-1]`` path to avoid the
    same "str-treated-as-sequence" footgun the
    ``_last_date_label`` helper had.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    # Scalar numeric-string (e.g. "100.0"). DON'T fall
    # through to the sequence path -- ``"100.0"`` would
    # otherwise return its last character.
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except Exception:
            return None
    if isinstance(value, bytes):
        try:
            return float(value.decode("ascii", "replace"))
        except Exception:
            return None
    # Sequence path.
    try:
        n = len(value)
    except Exception:
        return None
    if n == 0:
        return None
    try:
        v = value[-1]
    except Exception:
        try:
            v = list(value)[-1]
        except Exception:
            return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except Exception:
        return None


def _last_date_label(value: Any) -> Optional[str]:
    """Defensively return the last date label from a
    sequence-like input OR the scalar date itself when
    ``value`` is a scalar string / bytes / datetime.

    Codex audit (Phase 6I-42 amendment-1): the previous
    implementation treated a scalar string like
    ``"2026-05-14"`` as a sequence and returned its
    last character (``"4"``). The scalar paths below
    guard that case explicitly so a nested ``daily``
    block carrying ``{"close": [...], "last_date":
    "2026-05-14"}`` extracts the date correctly.
    """
    if value is None:
        return None
    # Scalar string. ``str`` IS a sequence in Python, so
    # we must intercept it BEFORE the ``len(seq) -> seq[-1]``
    # path or we'd return the last character.
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("ascii", "replace")
        except Exception:
            return None
    # Scalar datetime / date / pandas Timestamp (none of
    # these are list/tuple/range; all of them have
    # ``isoformat()``).
    if (
        hasattr(value, "isoformat")
        and not isinstance(value, (list, tuple, range))
    ):
        try:
            return value.isoformat()
        except Exception:
            try:
                return str(value)
            except Exception:
                return None
    # Sequence path.
    try:
        n = len(value)
    except Exception:
        return None
    if n == 0:
        return None
    try:
        v = value[-1]
    except Exception:
        try:
            v = list(value)[-1]
        except Exception:
            return None
    # Some pandas-flavored Timestamp / datetime objects
    # support isoformat(); fall back to str().
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            pass
    if isinstance(v, str):
        return v
    return str(v)


def _extract_latest_price_from_payload(
    payload: Mapping[str, Any],
) -> tuple[Optional[float], Optional[str], list[str]]:
    """Defensively extract ``(latest_price, latest_price_as_of,
    issues)`` from a cache payload mapping.

    Probes known top-level keys + the legacy nested
    ``daily`` block. Returns ``(None, None, [unknown_shape])``
    when nothing matches -- never raises.
    """
    issues: list[str] = []
    price: Optional[float] = None
    as_of: Optional[str] = None
    # Top-level price key sweep.
    for k in _PRICE_KEYS:
        if k in payload:
            v = payload[k]
            scalar = _last_scalar(v)
            if scalar is not None:
                price = scalar
                break
    # Top-level date key sweep.
    for k in _DATE_KEYS:
        if k in payload:
            v = payload[k]
            as_of = _last_date_label(v)
            if as_of is not None:
                break
    if price is None or as_of is None:
        # Try a nested ``daily`` block (Phase 6C-shaped).
        daily = payload.get("daily")
        if isinstance(daily, Mapping):
            if price is None:
                for k in _PRICE_KEYS:
                    if k in daily:
                        scalar = _last_scalar(daily[k])
                        if scalar is not None:
                            price = scalar
                            break
            if as_of is None:
                for k in _DATE_KEYS + ("last_date",):
                    if k in daily:
                        as_of = _last_date_label(daily[k])
                        if as_of is not None:
                            break
                if (
                    as_of is None
                    and "last_date" in daily
                ):
                    raw = daily["last_date"]
                    as_of = (
                        str(raw) if raw is not None else None
                    )
    if price is None and as_of is None:
        issues.append(ISSUE_CODE_CACHE_UNKNOWN_SHAPE)
    return price, as_of, issues


def _classify_signal_status(
    *,
    cache_last_date: Optional[str],
    current_as_of_date: Optional[str],
    have_artifact: bool,
    cache_loader_issues: Sequence[str],
) -> tuple[str, list[str]]:
    """Map (cache_last_date, current_as_of_date) into the
    Phase 6I-40 ``current_signal_status`` taxonomy.

    Rules:

      * No artifact AND no usable cache -> ``unknown``.
      * Cache failed to load -> ``unknown`` (issue code
        already in issues).
      * Cache date >= current_as_of_date (or
        current_as_of_date is None) -> ``locked``.
      * Cache date strictly behind current_as_of_date by
        any number of days -> ``stale``.
      * Cache date unparsable -> ``unknown`` with
        ``cache_date_unparsable``.
      * Live overlay (``provisional``) is reserved for a
        future live-quote injection; the local-only path
        never returns ``provisional``.
    """
    extra_issues: list[str] = []
    # Loader-level issues already include
    # ``cache_pkl_missing`` / ``cache_pkl_unreadable`` /
    # ``cache_pkl_unknown_shape``. Promote them to status.
    if (
        ISSUE_CODE_CACHE_MISSING in cache_loader_issues
        or ISSUE_CODE_CACHE_UNREADABLE in cache_loader_issues
    ):
        if not have_artifact:
            return SIGNAL_STATUS_UNKNOWN, extra_issues
        # We have an artifact but no cache -- the artifact
        # is the source of truth, status is ``locked``.
        return SIGNAL_STATUS_LOCKED, extra_issues
    if cache_last_date is None:
        # The loader returned a payload but no date keys.
        return SIGNAL_STATUS_UNKNOWN, extra_issues
    cd = _parse_iso_date(cache_last_date)
    if cd is None:
        extra_issues.append(ISSUE_CODE_CACHE_DATE_UNPARSABLE)
        return SIGNAL_STATUS_UNKNOWN, extra_issues
    if current_as_of_date is None:
        return SIGNAL_STATUS_LOCKED, extra_issues
    cur = _parse_iso_date(current_as_of_date)
    if cur is None:
        return SIGNAL_STATUS_LOCKED, extra_issues
    if cd >= cur:
        return SIGNAL_STATUS_LOCKED, extra_issues
    extra_issues.append(ISSUE_CODE_CACHE_STALE)
    return SIGNAL_STATUS_STALE, extra_issues


def _build_data_completeness(
    *,
    incomplete_members: Sequence[str],
    incomplete_reasons: Mapping[str, str],
    rank_eligible_hint: Optional[bool],
    blocked_reason_hint: Optional[str],
) -> dict[str, Any]:
    """Build the Phase 6I-40 ``data_completeness`` block.

    Status taxonomy:

      * blocked -- ``rank_eligible_hint`` is explicitly
        False (artifact missing / daily-only / invalid
        payload upstream).
      * partial -- at least one member is flagged
        incomplete / stale / invalid.
      * complete -- no incomplete members AND
        ``rank_eligible_hint`` is not False.
      * unknown -- defensive catch-all.
    """
    members = [m for m in incomplete_members if m]
    reasons = {
        k: v for k, v in incomplete_reasons.items() if k
    }
    if rank_eligible_hint is False:
        status = COMPLETENESS_STATUS_BLOCKED
        message = (
            f"blocked: {blocked_reason_hint}"
            if blocked_reason_hint
            else "blocked"
        )
        warning_symbol: Optional[str] = DATA_WARNING_SYMBOL
    elif members:
        status = COMPLETENESS_STATUS_PARTIAL
        message = (
            f"partial: {len(members)} member(s) "
            "incomplete or stale"
        )
        warning_symbol = DATA_WARNING_SYMBOL
    elif rank_eligible_hint is None:
        # We don't know whether the row is eligible yet
        # (caller didn't pass a hint). Default to
        # complete; the ranking export's strict gate
        # supersedes this if it later marks the row
        # blocked.
        status = COMPLETENESS_STATUS_COMPLETE
        message = "complete: all build members reporting"
        warning_symbol = None
    else:
        status = COMPLETENESS_STATUS_COMPLETE
        message = "complete: all build members reporting"
        warning_symbol = None
    return {
        "has_incomplete_build_members": bool(members),
        "incomplete_member_count": len(members),
        "incomplete_members": list(members),
        "incomplete_member_reasons": dict(reasons),
        "data_warning_symbol": warning_symbol,
        "data_completeness_status": status,
        "data_completeness_message": message,
    }


def _build_signal_status_block(
    *,
    rank_eligible_hint: Optional[bool],
    signal_status: str,
    cache_last_date: Optional[str],
    latest_price: Optional[float],
    update_source: str,
) -> dict[str, Any]:
    if rank_eligible_hint is False:
        return {
            "current_signal_status": SIGNAL_STATUS_BLOCKED,
            "current_signal_as_of": None,
            "latest_price": None,
            "latest_price_as_of": None,
            "uses_provisional_price": False,
            "signal_update_source": (
                SIGNAL_UPDATE_SOURCE_UNAVAILABLE
            ),
        }
    return {
        "current_signal_status": signal_status,
        "current_signal_as_of": cache_last_date,
        "latest_price": latest_price,
        "latest_price_as_of": cache_last_date,
        "uses_provisional_price": False,
        "signal_update_source": update_source,
    }


# ---------------------------------------------------------------------------
# Per-ticker overlay
# ---------------------------------------------------------------------------


def _overlay_one_ticker(
    ticker: str,
    *,
    artifact_root: Optional[Path],
    cache_dir: Optional[Path],
    stackbuilder_root: Optional[Path],
    signal_library_dir: Optional[Path],
    current_as_of_date: Optional[str],
    cache_loader_callable: Callable[
        [Path],
        tuple[Optional[Mapping[str, Any]], list[str]],
    ],
    adapter_diagnostic_callable: Optional[
        Callable[..., Mapping[str, Any]]
    ],
    stackbuilder_member_callable: Optional[
        Callable[..., Mapping[str, Any]]
    ],
) -> dict[str, Any]:
    """Run all local-data probes for one ticker; return
    the per-ticker overlay payload."""
    issues: list[str] = []

    # ----- artifact presence (rank_eligible_hint) -----
    rank_eligible_hint: Optional[bool] = None
    blocked_reason_hint: Optional[str] = None
    have_artifact = False
    if artifact_root is not None:
        candidates = [
            artifact_root / "confluence" / ticker
            / f"{ticker}__MTF_CONSENSUS.research_day.json",
            artifact_root / "confluence" / ticker
            / f"{ticker}.research_day.json",
        ]
        for c in candidates:
            try:
                if c.exists() and c.is_file():
                    have_artifact = True
                    break
            except Exception:
                continue
        if not have_artifact:
            rank_eligible_hint = False
            blocked_reason_hint = "artifact_missing"

    # ----- cache PKL load + price extraction -----
    cache_loader_issues: list[str] = []
    cache_payload: Optional[Mapping[str, Any]] = None
    latest_price: Optional[float] = None
    cache_last_date: Optional[str] = None
    update_source = SIGNAL_UPDATE_SOURCE_UNAVAILABLE
    if cache_dir is not None:
        cache_path = (
            Path(cache_dir)
            / f"{ticker}_precomputed_results.pkl"
        )
        try:
            cache_payload, cache_loader_issues = (
                cache_loader_callable(cache_path)
            )
        except Exception:
            cache_loader_issues = [
                ISSUE_CODE_CACHE_UNREADABLE,
            ]
            cache_payload = None
    if cache_payload is not None:
        price, as_of, extract_issues = (
            _extract_latest_price_from_payload(
                cache_payload,
            )
        )
        latest_price = price
        cache_last_date = as_of
        cache_loader_issues = list(cache_loader_issues) + (
            list(extract_issues)
        )
        if price is not None or as_of is not None:
            update_source = (
                SIGNAL_UPDATE_SOURCE_LOCAL_CACHE
            )

    signal_status, status_extra_issues = (
        _classify_signal_status(
            cache_last_date=cache_last_date,
            current_as_of_date=current_as_of_date,
            have_artifact=have_artifact,
            cache_loader_issues=cache_loader_issues,
        )
    )
    if (
        signal_status == SIGNAL_STATUS_LOCKED
        and have_artifact
        and cache_payload is None
    ):
        # Artifact present, cache absent -- the artifact
        # is the source of truth, no price overlay.
        update_source = SIGNAL_UPDATE_SOURCE_ARTIFACT
        cache_last_date = None
    issues.extend(cache_loader_issues)
    issues.extend(status_extra_issues)

    # ----- incomplete-member detection -----
    incomplete_members: list[str] = []
    incomplete_reasons: dict[str, str] = {}
    if stackbuilder_member_callable is not None:
        try:
            mb = stackbuilder_member_callable(
                ticker,
                stackbuilder_root=stackbuilder_root,
                signal_library_dir=signal_library_dir,
            )
        except Exception:
            mb = None
            issues.append(ISSUE_CODE_PROVIDER_RAISED)
        if isinstance(mb, Mapping):
            raw_members = mb.get(
                "incomplete_members", [],
            ) or []
            if isinstance(raw_members, list):
                incomplete_members = [
                    str(m) for m in raw_members if m
                ]
            raw_reasons = mb.get(
                "incomplete_member_reasons", {},
            ) or {}
            if isinstance(raw_reasons, Mapping):
                incomplete_reasons = {
                    str(k): str(v)
                    for k, v in raw_reasons.items() if k
                }
    if adapter_diagnostic_callable is not None:
        try:
            ad = adapter_diagnostic_callable(
                ticker,
                artifact_root=artifact_root,
                cache_dir=cache_dir,
            )
        except Exception:
            ad = None
            issues.append(ISSUE_CODE_PROVIDER_RAISED)
        if isinstance(ad, Mapping):
            raw_members = ad.get(
                "incomplete_members", [],
            ) or []
            if isinstance(raw_members, list):
                for m in raw_members:
                    if m and str(m) not in incomplete_members:
                        incomplete_members.append(str(m))
            raw_reasons = ad.get(
                "incomplete_member_reasons", {},
            ) or {}
            if isinstance(raw_reasons, Mapping):
                for k, v in raw_reasons.items():
                    if k and str(k) not in incomplete_reasons:
                        incomplete_reasons[str(k)] = str(v)

    data_completeness = _build_data_completeness(
        incomplete_members=incomplete_members,
        incomplete_reasons=incomplete_reasons,
        rank_eligible_hint=rank_eligible_hint,
        blocked_reason_hint=blocked_reason_hint,
    )

    signal_block = _build_signal_status_block(
        rank_eligible_hint=rank_eligible_hint,
        signal_status=signal_status,
        cache_last_date=cache_last_date,
        latest_price=latest_price,
        update_source=update_source,
    )

    latest_price_block = {
        "latest_price": signal_block["latest_price"],
        "latest_price_as_of": signal_block[
            "latest_price_as_of"
        ],
        "uses_provisional_price": signal_block[
            "uses_provisional_price"
        ],
        "signal_update_source": signal_block[
            "signal_update_source"
        ],
    }

    overlay = {
        "ticker": ticker,
        "data_completeness": data_completeness,
        "current_signal_status_block": signal_block,
        "latest_price_block": latest_price_block,
        "issue_codes": list(issues),
    }
    return overlay


# ---------------------------------------------------------------------------
# Public entry: build_board_runtime_overlays
# ---------------------------------------------------------------------------


_DEFAULT_REMAINING_LIMITATIONS: tuple[str, ...] = (
    "Latest price values are read from local "
    "cache/results PKLs only; no live feed is fetched. "
    "When the operator wires a live-quote injection seam "
    "in a future phase, uses_provisional_price flips to "
    "True with signal_update_source=live_price_overlay.",
    "Member-completeness detection is conservative: the "
    "default path returns no incomplete members. Real "
    "TEF-style invalid-member detection requires an "
    "injected stackbuilder_member_callable (or adapter "
    "diagnostic) that classifies members against the "
    "StackBuilder leaderboard / signal-library directory. "
    "Tests pass a fake provider; production wiring is a "
    "future phase.",
    "This overlay layer NEVER alters the strict Phase "
    "6I-20 rank-eligibility gate. Rank eligibility still "
    "requires the full per_window_k_metrics + "
    "build_wide_window_alignment + "
    "multiwindow_k_engine_payload_metadata payload "
    "upstream.",
)


def build_board_runtime_overlays(
    tickers: Iterable[str],
    *,
    artifact_root: Optional[Any] = None,
    cache_dir: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    cache_loader_callable: Optional[
        Callable[
            [Path],
            tuple[Optional[Mapping[str, Any]], list[str]],
        ]
    ] = None,
    adapter_diagnostic_callable: Optional[
        Callable[..., Mapping[str, Any]]
    ] = None,
    stackbuilder_member_callable: Optional[
        Callable[..., Mapping[str, Any]]
    ] = None,
) -> BoardRuntimeOverlayReport:
    """Build the Phase 6I-42 local runtime overlay report.

    Strictly read-only. All external probes are guarded;
    a missing / unreadable / unfamiliar source returns
    ``unknown`` / ``stale`` honestly via the issue-code
    taxonomy.
    """
    ticker_list = [
        str(t).strip().upper()
        for t in tickers if str(t).strip()
    ]
    seen: set[str] = set()
    deduped: list[str] = []
    for t in ticker_list:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    ticker_list = deduped

    artifact_root_p = (
        Path(artifact_root) if artifact_root is not None
        else None
    )
    cache_dir_p = (
        Path(cache_dir) if cache_dir is not None else None
    )
    stackbuilder_p = (
        Path(stackbuilder_root)
        if stackbuilder_root is not None
        else None
    )
    signal_library_p = (
        Path(signal_library_dir)
        if signal_library_dir is not None
        else None
    )

    loader = (
        cache_loader_callable or _default_cache_loader
    )

    overlays_by_ticker: dict[str, dict[str, Any]] = {}
    data_completeness_by_ticker: dict[
        str, dict[str, Any]
    ] = {}
    latest_price_by_ticker: dict[str, dict[str, Any]] = {}
    current_signal_status_by_ticker: dict[
        str, dict[str, Any]
    ] = {}
    issue_codes_by_ticker: dict[str, list[str]] = {}

    no_local_inputs = (
        artifact_root_p is None and cache_dir_p is None
    )

    summary: dict[str, Any] = {
        "by_signal_status": {},
        "by_completeness_status": {},
        "tickers_with_incomplete_members": 0,
        "tickers_with_latest_price": 0,
    }

    for t in ticker_list:
        overlay = _overlay_one_ticker(
            t,
            artifact_root=artifact_root_p,
            cache_dir=cache_dir_p,
            stackbuilder_root=stackbuilder_p,
            signal_library_dir=signal_library_p,
            current_as_of_date=current_as_of_date,
            cache_loader_callable=loader,
            adapter_diagnostic_callable=(
                adapter_diagnostic_callable
            ),
            stackbuilder_member_callable=(
                stackbuilder_member_callable
            ),
        )
        if no_local_inputs:
            overlay["issue_codes"].append(
                ISSUE_CODE_NO_LOCAL_INPUTS,
            )
        overlays_by_ticker[t] = overlay
        data_completeness_by_ticker[t] = (
            overlay["data_completeness"]
        )
        latest_price_by_ticker[t] = (
            overlay["latest_price_block"]
        )
        current_signal_status_by_ticker[t] = (
            overlay["current_signal_status_block"]
        )
        issue_codes_by_ticker[t] = list(
            overlay["issue_codes"],
        )
        # Aggregate summary.
        sig = overlay[
            "current_signal_status_block"
        ]["current_signal_status"]
        summary["by_signal_status"][sig] = (
            summary["by_signal_status"].get(sig, 0) + 1
        )
        comp = overlay[
            "data_completeness"
        ]["data_completeness_status"]
        summary["by_completeness_status"][comp] = (
            summary["by_completeness_status"].get(comp, 0)
            + 1
        )
        if overlay["data_completeness"][
            "has_incomplete_build_members"
        ]:
            summary["tickers_with_incomplete_members"] += 1
        if overlay["latest_price_block"][
            "latest_price"
        ] is not None:
            summary["tickers_with_latest_price"] += 1

    return BoardRuntimeOverlayReport(
        schema_version=SCHEMA_VERSION,
        generated_at=_iso_now(),
        inspected_count=len(ticker_list),
        overlays_by_ticker=overlays_by_ticker,
        data_completeness_by_ticker=(
            data_completeness_by_ticker
        ),
        latest_price_by_ticker=latest_price_by_ticker,
        current_signal_status_by_ticker=(
            current_signal_status_by_ticker
        ),
        issue_codes=issue_codes_by_ticker,
        summary=summary,
        remaining_limitations=(
            _DEFAULT_REMAINING_LIMITATIONS
        ),
    )


# ---------------------------------------------------------------------------
# Provider factories -- plug into Phase 6I-34 ranking export
# ---------------------------------------------------------------------------


def make_member_completeness_provider(
    report: BoardRuntimeOverlayReport,
) -> Callable[..., Mapping[str, Any]]:
    """Adapt a Phase 6I-42 overlay report into a callable
    that ``confluence_multiwindow_ranking_export``'s
    ``member_completeness_provider_callable`` kwarg
    accepts."""
    table = dict(report.data_completeness_by_ticker)

    def provider(
        ticker: str,
        artifact: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any]:
        key = str(ticker).strip().upper()
        block = table.get(key)
        if isinstance(block, Mapping):
            # Only return the four member-level keys the
            # ranking export consumes (it composes the
            # full data_completeness block itself, which
            # picks up `status` / `message` / symbol from
            # the rank_eligible state -- we ONLY pass the
            # member-level data through).
            return {
                "has_incomplete_build_members": bool(
                    block.get(
                        "has_incomplete_build_members",
                        False,
                    ),
                ),
                "incomplete_member_count": int(
                    block.get(
                        "incomplete_member_count", 0,
                    ) or 0,
                ),
                "incomplete_members": list(
                    block.get("incomplete_members", []),
                ),
                "incomplete_member_reasons": dict(
                    block.get(
                        "incomplete_member_reasons", {},
                    ),
                ),
            }
        return {
            "has_incomplete_build_members": False,
            "incomplete_member_count": 0,
            "incomplete_members": [],
            "incomplete_member_reasons": {},
        }

    return provider


def make_live_price_provider(
    report: BoardRuntimeOverlayReport,
) -> Callable[..., Optional[Mapping[str, Any]]]:
    """Adapt a Phase 6I-42 overlay report into a callable
    that ``confluence_multiwindow_ranking_export``'s
    ``live_price_provider_callable`` kwarg accepts.

    The ranking export's seam expects a payload like
    ``{latest_price, latest_price_as_of,
    uses_provisional_price, [current_signal_status]}``.
    We pass the overlay's status through so a stale local
    cache marks the eligible row as ``stale`` rather than
    ``locked`` -- otherwise the renderer wouldn't see the
    distinction.
    """
    status_table = dict(
        report.current_signal_status_by_ticker,
    )
    price_table = dict(report.latest_price_by_ticker)

    def provider(
        ticker: str,
        artifact: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Mapping[str, Any]]:
        key = str(ticker).strip().upper()
        sb = status_table.get(key)
        pb = price_table.get(key)
        if not isinstance(sb, Mapping):
            return None
        status = sb.get("current_signal_status")
        # When the overlay's standalone classification says
        # "blocked" (or "unavailable" source), return None
        # so the ranking export's own strict Phase 6I-20
        # gate handles blocked-row classification. The
        # overlay's role at this seam is to provide the
        # PRICE / FRESHNESS overlay for eligible rows -- it
        # must not override the ranking export's eligibility
        # decision.
        if status == SIGNAL_STATUS_BLOCKED:
            return None
        source = sb.get("signal_update_source")
        if source == SIGNAL_UPDATE_SOURCE_UNAVAILABLE:
            return None
        payload: dict[str, Any] = {
            "latest_price": (
                sb.get("latest_price")
                if not isinstance(pb, Mapping)
                else pb.get("latest_price")
            ),
            "latest_price_as_of": (
                sb.get("latest_price_as_of")
                if not isinstance(pb, Mapping)
                else pb.get("latest_price_as_of")
            ),
            "uses_provisional_price": bool(
                sb.get("uses_provisional_price", False),
            ),
            "current_signal_as_of": sb.get(
                "current_signal_as_of",
            ),
        }
        if status in (
            SIGNAL_STATUS_LOCKED,
            SIGNAL_STATUS_PROVISIONAL,
            SIGNAL_STATUS_STALE,
            SIGNAL_STATUS_UNKNOWN,
        ):
            payload["current_signal_status"] = status
        # Phase 6I-42 amendment-1: propagate
        # signal_update_source so the ranking export can
        # preserve "local_cache" (instead of masking
        # everything non-provisional back to "artifact").
        # The ranking export now sanctions {artifact,
        # live_price_overlay, local_cache, unavailable}.
        provided_source = sb.get("signal_update_source")
        if provided_source in (
            SIGNAL_UPDATE_SOURCE_ARTIFACT,
            SIGNAL_UPDATE_SOURCE_LOCAL_CACHE,
        ):
            payload["signal_update_source"] = (
                provided_source
            )
        return payload

    return provider


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confluence_board_runtime_overlays",
        description=(
            "Phase 6I-42 read-only local runtime overlay "
            "report. Scans local cache/results PKLs + "
            "(optionally) StackBuilder + signal-library "
            "inputs and emits per-ticker overlay "
            "payloads. STRICTLY READ-ONLY -- never "
            "fetches live data, never writes."
        ),
    )
    parser.add_argument(
        "--tickers", required=True,
        help="Comma-separated tickers.",
    )
    parser.add_argument(
        "--artifact-root",
        default=None,
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
    )
    parser.add_argument(
        "--stackbuilder-root", default=None,
    )
    parser.add_argument(
        "--signal-library-dir", default=None,
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
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
    tickers = [
        t.strip() for t in args.tickers.split(",")
        if t.strip()
    ]
    if not tickers:
        print(
            json.dumps({"error": "missing_tickers"}),
            file=sys.stderr,
        )
        return 2
    try:
        report = build_board_runtime_overlays(
            tickers,
            artifact_root=args.artifact_root,
            cache_dir=args.cache_dir,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            current_as_of_date=args.current_as_of_date,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(
            json.dumps({
                "error": "unhandled_exception",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 3
    print(json.dumps(report.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
