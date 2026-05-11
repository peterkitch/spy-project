"""Phase 6D-1: TrafficFlow research-day artifact builder for all
StackBuilder K builds.

Closes the first half of the Phase 6C-8 audit's
``insufficient_trafficflow_k_coverage`` gap: reads a saved
StackBuilder combo_leaderboard and materializes one TrafficFlow
``research_day_v1`` artifact per K row (K = 1..12 by default)
under the canonical ``output/research_artifacts/trafficflow/<TARGET>/``
tree. The multi-timeframe TrafficFlow / K-build projection
remains out of scope - that is Phase 6D-2.

Strictly read-only / offline:

  - No yfinance import.
  - No live engine execution (no trafficflow.py / spymaster.py
    / impactsearch.py import).
  - No Dash dependency.
  - Builder writes research_day_v1 artifacts ONLY when invoked
    with ``write=True`` (and only into the artifact tree); the
    web tier never touches this module.

Artifact path / collision convention
------------------------------------

``research_artifacts.artifact_path_for_trafficflow`` derives a
single ``<run_id>.research_day.json`` file from the
``(target, run_id)`` pair. Phase 6D-1 needs to keep K=1..12 rows
distinct on disk, so the builder uses a suffixed run id when
invoking the canonical path helper:

    run_id_for_path = f"{seed_run_id}__K{K}"

i.e. the saved file becomes::

    <safe_target>/<SAFE_RUN>__K<K>.research_day.json

The artifact's internal ``K`` field is still set to the integer
K value via ``build_trafficflow_day_artifact_from_local(K=K)``.
The suffix only affects on-disk uniqueness; readers do not need
to parse the suffix.

Public surface
--------------

    KBuildRow                          # dataclass
    BuildResult                        # dataclass
    DEFAULT_EXPECTED_K                 # tuple[int, ...] = (1..12)
    LEADERBOARD_FILENAME               # "combo_leaderboard.xlsx"

    ISSUE_*                            # str constants

    discover_latest_stackbuilder_run(target_ticker, *,
                                     stackbuilder_root=None)
        -> Optional[Path]
    load_stackbuilder_leaderboard(run_dir) -> pandas.DataFrame
    iter_k_build_rows(leaderboard, *, target_ticker, run_id,
                      expected_k=DEFAULT_EXPECTED_K)
        -> list[KBuildRow]
    artifact_run_id_for_k(seed_run_id, K) -> str
    build_trafficflow_artifacts_for_stack_run(target_ticker, *,
        run_dir=None, stackbuilder_root=None, cache_dir=None,
        artifact_root=None, expected_k=DEFAULT_EXPECTED_K,
        write=False) -> BuildResult
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import research_artifacts as _ra


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EXPECTED_K: tuple[int, ...] = tuple(range(1, 13))
LEADERBOARD_FILENAME = "combo_leaderboard.xlsx"
TRAFFICFLOW_ENGINE = "trafficflow"

# Issue codes emitted on the BuildResult. Stable strings so audit
# tooling can match without translation.
ISSUE_NO_STACKBUILDER_RUN = "no_stackbuilder_run"
ISSUE_MISSING_COMBO_LEADERBOARD = "missing_combo_leaderboard"
ISSUE_MISSING_MEMBERS_COLUMN = "missing_members_column"
ISSUE_MISSING_K_COLUMN = "missing_k_column"
ISSUE_MISSING_TARGET_CACHE = "missing_target_cache"
ISSUE_NO_MEMBER_CACHES = "no_member_caches"
ISSUE_ARTIFACT_WRITE_FAILED = "artifact_write_failed"
ISSUE_PARTIAL_K_COVERAGE = "partial_k_coverage"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class KBuildRow:
    """One row of the saved StackBuilder ``combo_leaderboard.xlsx``.

    ``run_id`` is the seed-run directory's basename (the K-bearing
    suffix is added when the artifact is persisted - see
    ``artifact_run_id_for_k``). Summary metrics are best-effort:
    a leaderboard without ``Significant 95%`` simply reports
    ``significant_95=None``.
    """

    target_ticker: str
    run_id: str
    K: int
    members_str: str
    source_row_index: int
    total_capture_pct: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    trigger_days: Optional[int] = None
    p_value: Optional[float] = None
    significant_95: Optional[bool] = None

    def summary_overrides(self) -> dict[str, Any]:
        """Return the leaderboard-derived summary slice in the
        shape ``build_trafficflow_day_artifact_from_local``
        expects via ``summary_overrides=``. ``None`` keys are
        included so callers can distinguish "the leaderboard had
        this value" from "the leaderboard omitted it"."""
        return {
            "total_capture_pct": self.total_capture_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "trigger_days": self.trigger_days,
            "p_value": self.p_value,
            "significant_95": self.significant_95,
        }


@dataclass
class BuildResult:
    """Outcome of a single
    ``build_trafficflow_artifacts_for_stack_run`` invocation.

    ``attempted_k`` records the K values present in the
    leaderboard ∩ ``expected_k``. ``built_k`` records the K values
    that successfully produced an artifact (and were written when
    ``write=True``). ``skipped_k`` records K values that failed
    artifact construction (missing member caches, etc.).
    ``issue_codes`` collects every issue raised across all K rows,
    deduplicated and ordered by first appearance."""

    target_ticker: str
    run_id: Optional[str]
    attempted_k: tuple[int, ...] = ()
    built_k: tuple[int, ...] = ()
    skipped_k: tuple[int, ...] = ()
    artifact_paths: tuple[Path, ...] = ()
    issue_codes: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_stackbuilder_root() -> Path:
    return _project_dir() / "output" / "stackbuilder"


def _default_cache_dir() -> Path:
    return _project_dir() / "cache" / "results"


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _filename_safe_ticker(ticker: str) -> str:
    """Mirrors the safe-form rewrite the rest of the repo uses:
    ``^GSPC`` -> ``_GSPC``; non-alphanumerics collapse to ``_``."""
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    s = s.replace("^", "_")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    return "".join(c if c in allowed else "_" for c in s)


def _ticker_form_candidates(ticker: str) -> list[str]:
    real = str(ticker or "").strip().upper()
    if not real:
        return []
    safe = _filename_safe_ticker(real)
    out: list[str] = []
    for cand in (real, safe):
        if cand and cand not in out:
            out.append(cand)
    return out


def artifact_run_id_for_k(seed_run_id: str, K: int) -> str:
    """Return the suffixed run id used as the on-disk artifact
    identifier for a specific K row. Keeps K=1..12 artifacts
    distinct under
    ``output/research_artifacts/trafficflow/<TARGET>/``.

    Empty ``seed_run_id`` returns an empty string so callers can
    surface a clean error rather than producing
    ``__K<K>.research_day.json`` files with no run identity.
    """
    if not seed_run_id:
        return ""
    return f"{str(seed_run_id).strip()}__K{int(K)}"


# ---------------------------------------------------------------------------
# StackBuilder run discovery
# ---------------------------------------------------------------------------


def discover_latest_stackbuilder_run(
    target_ticker: str,
    *,
    stackbuilder_root: Optional[Path] = None,
) -> Optional[Path]:
    """Return the most recent seed-run directory under
    ``stackbuilder_root/<TARGET>/`` for the given target, or
    ``None`` when no run is saved.

    "Most recent" = newest directory ``mtime``. Direct-form
    (``^GSPC``) is tried before safe-form (``_GSPC``) so the
    builder finds the canonical real-name run first; if no
    direct-form run exists, the safe-form fallback covers
    targets that were saved with the artifact-output form.
    """
    root = (
        Path(stackbuilder_root) if stackbuilder_root is not None
        else _default_stackbuilder_root()
    )
    if not root.exists() or not root.is_dir():
        return None
    candidates: list[Path] = []
    for form in _ticker_form_candidates(target_ticker):
        target_dir = root / form
        if not target_dir.exists() or not target_dir.is_dir():
            continue
        for entry in target_dir.iterdir():
            if not entry.is_dir():
                continue
            # Skip dot-prefixed bookkeeping like ``_progress``.
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue
            candidates.append(entry)
        if candidates:
            # First form with any seed-run dirs wins; the
            # filename-safe fallback is only consulted when the
            # direct form produced nothing.
            break
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Leaderboard load + iteration
# ---------------------------------------------------------------------------


def load_stackbuilder_leaderboard(run_dir: Path) -> Any:
    """Load ``combo_leaderboard.xlsx`` from a seed-run directory.

    Returns a ``pandas.DataFrame``. Raises ``FileNotFoundError``
    when the file is absent. Excel is the canonical persisted
    shape across the existing repo's StackBuilder outputs; this
    helper deliberately does NOT try alternative formats so a
    missing leaderboard surfaces cleanly through
    ``ISSUE_MISSING_COMBO_LEADERBOARD`` upstream.
    """
    import pandas as pd

    path = Path(run_dir) / LEADERBOARD_FILENAME
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    return pd.read_excel(path)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _safe_int(value: Any) -> Optional[int]:
    f = _safe_float(value)
    if f is None:
        return None
    return int(round(f))


def _safe_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in {"nan", "none", "null", "-"}:
        return None
    if s in {"true", "yes", "y", "1"}:
        return True
    if s in {"false", "no", "n", "0"}:
        return False
    return None


def iter_k_build_rows(
    leaderboard: Any,
    *,
    target_ticker: str,
    run_id: str,
    expected_k: Iterable[int] = DEFAULT_EXPECTED_K,
) -> list[KBuildRow]:
    """Yield one ``KBuildRow`` per leaderboard row whose ``K``
    value is in ``expected_k``. The leaderboard MUST carry
    ``K`` and ``Members`` columns; otherwise the caller surface
    raises by inspecting the dataframe columns up front.

    Rows with an unparseable ``K`` value are skipped silently.
    Duplicate K values are accepted - the caller decides whether
    to dedupe upstream (the existing StackBuilder writer produces
    one row per K, so duplicates indicate a fixture / authoring
    bug rather than something this iterator should swallow).
    """
    import pandas as pd

    if not isinstance(leaderboard, pd.DataFrame):
        raise TypeError(
            "leaderboard must be a pandas.DataFrame; got "
            f"{type(leaderboard).__name__}"
        )
    if "K" not in leaderboard.columns:
        raise KeyError(ISSUE_MISSING_K_COLUMN)
    if "Members" not in leaderboard.columns:
        raise KeyError(ISSUE_MISSING_MEMBERS_COLUMN)

    wanted = {int(k) for k in expected_k}
    rows: list[KBuildRow] = []
    for idx, row in leaderboard.iterrows():
        try:
            k_value = int(row["K"])
        except (TypeError, ValueError):
            continue
        if k_value not in wanted:
            continue
        members_str = str(row.get("Members") or "").strip()
        if not members_str:
            continue
        rows.append(KBuildRow(
            target_ticker=target_ticker,
            run_id=run_id,
            K=k_value,
            members_str=members_str,
            source_row_index=int(idx),
            total_capture_pct=_safe_float(
                row.get("Total Capture (%)"),
            ),
            sharpe_ratio=_safe_float(row.get("Sharpe Ratio")),
            trigger_days=_safe_int(row.get("Trigger Days")),
            p_value=_safe_float(row.get("p-Value")),
            significant_95=_safe_bool(row.get("Significant 95%")),
        ))
    return rows


# ---------------------------------------------------------------------------
# Per-K build dispatch
# ---------------------------------------------------------------------------


def _target_cache_exists(
    target_ticker: str, cache_dir: Path,
) -> bool:
    if not cache_dir.exists() or not cache_dir.is_dir():
        return False
    for form in _ticker_form_candidates(target_ticker):
        p = cache_dir / f"{form}_precomputed_results.pkl"
        if p.exists() and p.is_file():
            return True
    return False


def _append_unique(issues: list[str], code: str) -> None:
    if code and code not in issues:
        issues.append(code)


def build_trafficflow_artifacts_for_stack_run(
    target_ticker: str,
    *,
    run_dir: Optional[Path] = None,
    stackbuilder_root: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    artifact_root: Optional[Path] = None,
    expected_k: Iterable[int] = DEFAULT_EXPECTED_K,
    write: bool = False,
) -> BuildResult:
    """Materialize one TrafficFlow ``research_day_v1`` artifact
    per K row of a StackBuilder seed run.

    The default invocation walks the canonical layout under
    ``project/`` for inputs and outputs. Callers can override any
    directory for tests / dry runs.

    ``write=False`` (the default) builds every artifact in memory
    and returns the BuildResult without touching disk under
    ``artifact_root``. ``write=True`` persists each successful
    artifact via ``research_artifacts.write_research_day_artifact``
    at the K-distinguished path computed by
    ``artifact_path_for_trafficflow`` over
    ``artifact_run_id_for_k(seed_run_id, K)``.

    Failure modes are reported through ``BuildResult.issue_codes``;
    the function never raises for a missing run / missing
    leaderboard / missing target cache / missing member cache. A
    real Python error during artifact build is reported via
    ``ISSUE_ARTIFACT_WRITE_FAILED`` and the K row is added to
    ``skipped_k`` so the rest of the leaderboard still attempts.
    """
    t0 = time.perf_counter()
    expected_k_tuple = tuple(int(k) for k in expected_k)
    cache_d = (
        Path(cache_dir) if cache_dir is not None
        else _default_cache_dir()
    )
    artifact_d = (
        Path(artifact_root) if artifact_root is not None
        else _default_artifact_root()
    )
    issues: list[str] = []

    if run_dir is None:
        run_dir = discover_latest_stackbuilder_run(
            target_ticker,
            stackbuilder_root=stackbuilder_root,
        )
    if run_dir is None:
        return BuildResult(
            target_ticker=target_ticker,
            run_id=None,
            attempted_k=(),
            built_k=(),
            skipped_k=(),
            artifact_paths=(),
            issue_codes=(ISSUE_NO_STACKBUILDER_RUN,),
            elapsed_seconds=time.perf_counter() - t0,
        )

    run_dir = Path(run_dir)
    seed_run_id = run_dir.name

    try:
        df = load_stackbuilder_leaderboard(run_dir)
    except FileNotFoundError:
        return BuildResult(
            target_ticker=target_ticker,
            run_id=seed_run_id,
            attempted_k=(),
            built_k=(),
            skipped_k=(),
            artifact_paths=(),
            issue_codes=(ISSUE_MISSING_COMBO_LEADERBOARD,),
            elapsed_seconds=time.perf_counter() - t0,
        )

    try:
        k_rows = iter_k_build_rows(
            df,
            target_ticker=target_ticker,
            run_id=seed_run_id,
            expected_k=expected_k_tuple,
        )
    except KeyError as exc:
        missing_code = str(exc).strip("'").strip('"')
        return BuildResult(
            target_ticker=target_ticker,
            run_id=seed_run_id,
            attempted_k=(),
            built_k=(),
            skipped_k=(),
            artifact_paths=(),
            issue_codes=(missing_code,),
            elapsed_seconds=time.perf_counter() - t0,
        )

    if not _target_cache_exists(target_ticker, cache_d):
        return BuildResult(
            target_ticker=target_ticker,
            run_id=seed_run_id,
            attempted_k=tuple(r.K for r in k_rows),
            built_k=(),
            skipped_k=tuple(r.K for r in k_rows),
            artifact_paths=(),
            issue_codes=(ISSUE_MISSING_TARGET_CACHE,),
            elapsed_seconds=time.perf_counter() - t0,
        )

    attempted: list[int] = []
    built: list[int] = []
    skipped: list[int] = []
    paths: list[Path] = []

    for row in k_rows:
        attempted.append(row.K)
        suffixed_run_id = artifact_run_id_for_k(
            row.run_id, row.K,
        )
        if not suffixed_run_id:
            skipped.append(row.K)
            _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
            continue
        try:
            artifact = _ra.build_trafficflow_day_artifact_from_local(
                target_ticker,
                suffixed_run_id,
                members_str=row.members_str,
                K=row.K,
                summary_overrides=row.summary_overrides(),
                cache_dir=cache_d,
            )
        except Exception:
            skipped.append(row.K)
            _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
            continue
        if artifact is None:
            skipped.append(row.K)
            _append_unique(issues, ISSUE_NO_MEMBER_CACHES)
            continue

        if not write:
            built.append(row.K)
            continue

        out_path = _ra.artifact_path_for_trafficflow(
            target_ticker, suffixed_run_id, base_dir=artifact_d,
        )
        if out_path is None:
            skipped.append(row.K)
            _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
            continue
        try:
            written_path = _ra.write_research_day_artifact(
                artifact, out_path,
            )
        except Exception:
            skipped.append(row.K)
            _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
            continue
        built.append(row.K)
        paths.append(Path(written_path))

    # Partial coverage flag: anything in expected_k that did not
    # appear as a leaderboard row (and therefore was never
    # attempted) is a coverage gap. Anything attempted but
    # skipped is already reflected via its specific issue code.
    wanted = set(int(k) for k in expected_k_tuple)
    if not wanted.issubset(set(attempted)) and attempted:
        _append_unique(issues, ISSUE_PARTIAL_K_COVERAGE)
    if attempted and skipped:
        _append_unique(issues, ISSUE_PARTIAL_K_COVERAGE)

    return BuildResult(
        target_ticker=target_ticker,
        run_id=seed_run_id,
        attempted_k=tuple(attempted),
        built_k=tuple(built),
        skipped_k=tuple(skipped),
        artifact_paths=tuple(paths),
        issue_codes=tuple(issues),
        elapsed_seconds=time.perf_counter() - t0,
    )
