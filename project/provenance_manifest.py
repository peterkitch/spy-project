"""
Provenance manifests for PRJCT9 signal-library artifacts.

Phase 3A scope: signal-library producers and consumers only. Result/output
manifests for StackBuilder runs, OnePass / ImpactSearch xlsx exports,
Spymaster PKLs, TrafficFlow, and Confluence durable outputs are deferred to
Phase 3B.

Phase 3B-1 additions:

  - ``manifest_hash`` LRU cache keyed by ``(resolved_path, st_mtime_ns,
    st_size)``. ``content_hash`` is recomputed only on cache miss; thread-
    safe via an ``RLock``. Used only when callers explicitly supply
    ``cache_path`` (the central loader does), so in-memory mutation
    detection on direct ``verify_manifest`` calls is unaffected.
  - ``pickle_load_compat`` — central NumPy 1.x / 2.x pickle compatibility
    loader. Replaces the per-engine helpers in ``impactsearch.py`` and
    ``signal_library/impact_fastpath.py``.
  - ``load_verified_signal_library(path, ...)`` — central verified loader.
    Returns ``(library_dict, VerificationResult)`` and bundles the raw
    ``pickle.load``, type-check, and ``verify_manifest`` call. Each
    consumer keeps its own reject/warn/quarantine policy.

Design notes (preflight risks 1-7):

  - VOLATILE_LIBRARY_KEYS lists the library-level keys excluded from
    ``content_hash``. ``_manifest`` is excluded so the hash is not
    self-referential. ``build_timestamp`` is excluded because existing
    libraries already carry a top-level wall-clock build timestamp; including
    it would make every save flip the hash even when payload bytes are
    identical.

  - ``content_hash`` digests the canonical payload, not the raw pickle.
    Numpy arrays are hashed by ``(dtype, shape, sha256(bytes))`` so a 250-bar
    int8 signals array does not balloon into a JSON list.

  - Verification skips the source comparison unless the caller supplies
    ``current_source_close``. OnePass and multi-timeframe library dicts
    discard the raw Close after construction, so consumers don't always have
    one to pass.

  - Runtime versions (numpy / pandas / scipy / python) are captured
    dynamically via ``importlib.metadata`` / ``sys.version_info`` rather
    than hard-coded; the pinned env may drift faster than CLAUDE.md.

  - Git capture returns ``"unknown"`` instead of crashing when ``git``
    is missing, the cwd is non-repo, or the subprocess fails.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import logging
import math
import os
import pickle
import platform
import subprocess
import sys
import threading
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FIELD = "_manifest"
VOLATILE_LIBRARY_KEYS: frozenset[str] = frozenset({"_manifest", "build_timestamp"})


# ---------------------------------------------------------------------------
# Phase 3B-1: content_hash performance cache
# ---------------------------------------------------------------------------
#
# The cache is keyed by a tuple of (resolved_path, st_mtime_ns, st_size).
# Atomic ``os.replace`` and in-place rewrites both move at least one of
# mtime_ns / size, which invalidates the entry. We deliberately do NOT
# infer the cache key from sidecar_path -- callers must explicitly supply
# ``cache_path`` (and they do so only after they have just ``pickle.load``-ed
# the file via the central loader). This keeps verify_manifest's in-memory
# mutation contract intact for the legacy direct-call shape.

_MANIFEST_HASH_CACHE: "OrderedDict[Tuple[str, int, int], str]" = OrderedDict()
_MANIFEST_HASH_CACHE_MAX = 256
_MANIFEST_HASH_CACHE_LOCK = threading.RLock()
_MANIFEST_HASH_CACHE_STATS = {
    "hits": 0,
    "misses": 0,
    "evictions": 0,
}
_DISABLE_CACHE_ENV = "PRJCT9_DISABLE_MANIFEST_HASH_CACHE"


def _cache_enabled() -> bool:
    return os.environ.get(_DISABLE_CACHE_ENV, "0").lower() not in (
        "1", "true", "on", "yes",
    )


def manifest_hash_cache_clear() -> None:
    """Drop every cached content_hash and reset stats."""
    with _MANIFEST_HASH_CACHE_LOCK:
        _MANIFEST_HASH_CACHE.clear()
        _MANIFEST_HASH_CACHE_STATS.update(hits=0, misses=0, evictions=0)


def manifest_hash_cache_info() -> dict:
    """Return a snapshot of cache stats."""
    with _MANIFEST_HASH_CACHE_LOCK:
        return {
            "hits": _MANIFEST_HASH_CACHE_STATS["hits"],
            "misses": _MANIFEST_HASH_CACHE_STATS["misses"],
            "evictions": _MANIFEST_HASH_CACHE_STATS["evictions"],
            "current_size": len(_MANIFEST_HASH_CACHE),
            "max_size": _MANIFEST_HASH_CACHE_MAX,
            "enabled": _cache_enabled(),
        }


def _cache_key_for(cache_path: Any) -> Optional[Tuple[str, int, int]]:
    """Resolve ``cache_path`` to a stat-derived cache key, or None on error."""
    try:
        resolved = str(Path(cache_path).resolve(strict=False))
        st = os.stat(resolved)
        return (resolved, int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return None


def _cached_content_hash(
    library_dict: Mapping[str, Any], cache_path: Any
) -> str:
    """Return the canonical content_hash, using the LRU cache when possible.

    Falls back to direct recomputation when:
      - the cache is disabled via ``PRJCT9_DISABLE_MANIFEST_HASH_CACHE``,
      - ``cache_path`` is None,
      - the path cannot be stat-ed.
    """
    if cache_path is None or not _cache_enabled():
        return content_hash(library_dict)
    key = _cache_key_for(cache_path)
    if key is None:
        return content_hash(library_dict)
    with _MANIFEST_HASH_CACHE_LOCK:
        if key in _MANIFEST_HASH_CACHE:
            _MANIFEST_HASH_CACHE.move_to_end(key)
            _MANIFEST_HASH_CACHE_STATS["hits"] += 1
            return _MANIFEST_HASH_CACHE[key]
        _MANIFEST_HASH_CACHE_STATS["misses"] += 1
    # Compute outside the lock; the canonical_blob walk can be expensive.
    digest = content_hash(library_dict)
    with _MANIFEST_HASH_CACHE_LOCK:
        _MANIFEST_HASH_CACHE[key] = digest
        _MANIFEST_HASH_CACHE.move_to_end(key)
        if len(_MANIFEST_HASH_CACHE) > _MANIFEST_HASH_CACHE_MAX:
            _MANIFEST_HASH_CACHE.popitem(last=False)
            _MANIFEST_HASH_CACHE_STATS["evictions"] += 1
    return digest


# ---------------------------------------------------------------------------
# Phase 3B-1: NumPy 1.x / 2.x pickle compatibility (central)
# ---------------------------------------------------------------------------
#
# These shims previously lived in ``impactsearch.py`` and
# ``signal_library/impact_fastpath.py``. Centralizing them lets the new
# ``load_verified_signal_library`` be the single signal-library load path,
# which in turn enables the tightened B12 raw-pickle-load static guard.

_PICKLE_COMPAT_INSTALLED = False
_PICKLE_COMPAT_LOCK = threading.Lock()


def _install_numpy_pickle_compat_shims() -> None:
    """Alias ``numpy.core.*`` ↔ ``numpy._core.*`` so 1.x/2.x pickles cross-load.

    Idempotent; safe to call repeatedly. No-op if NumPy is missing.
    """
    global _PICKLE_COMPAT_INSTALLED
    try:
        import numpy as _np
    except Exception:
        return
    major = int((_np.__version__.split(".")[0] or "1"))
    pairs_1x = [
        ("numpy._core", "numpy.core"),
        ("numpy._core.numeric", "numpy.core.numeric"),
        ("numpy._core.multiarray", "numpy.core.multiarray"),
        ("numpy._core._multiarray_umath", "numpy.core._multiarray_umath"),
        ("numpy._core.umath", "numpy.core.umath"),
        ("numpy._core.arrayprint", "numpy.core.arrayprint"),
        ("numpy._core.fromnumeric", "numpy.core.fromnumeric"),
        ("numpy._core.shape_base", "numpy.core.shape_base"),
    ]
    pairs_2x = [
        ("numpy.core", "numpy._core"),
        ("numpy.core.numeric", "numpy._core.numeric"),
        ("numpy.core.multiarray", "numpy._core.multiarray"),
        ("numpy.core._multiarray_umath", "numpy._core._multiarray_umath"),
        ("numpy.core.umath", "numpy._core.umath"),
        ("numpy.core.arrayprint", "numpy._core.arrayprint"),
        ("numpy.core.fromnumeric", "numpy._core.fromnumeric"),
        ("numpy.core.shape_base", "numpy._core.shape_base"),
    ]
    pairs = pairs_1x if major < 2 else pairs_2x
    for alias_mod, target_mod in pairs:
        try:
            if target_mod not in sys.modules:
                importlib.import_module(target_mod)
            sys.modules.setdefault(alias_mod, sys.modules[target_mod])
        except Exception:
            pass
    _PICKLE_COMPAT_INSTALLED = True


def _ensure_pickle_compat() -> None:
    if _PICKLE_COMPAT_INSTALLED:
        return
    with _PICKLE_COMPAT_LOCK:
        if not _PICKLE_COMPAT_INSTALLED:
            _install_numpy_pickle_compat_shims()


# Install once at import time so cold pickles unpickle on first try.
_install_numpy_pickle_compat_shims()


def pickle_load_compat(file_obj) -> Any:
    """Single ``pickle.load`` site allowed by B12.

    Wraps ``pickle.load`` with the cross-version NumPy shim retry: on a
    ``ModuleNotFoundError`` for ``numpy._core`` / ``numpy.core``, install
    the shims and rewind/retry once.
    """
    try:
        return pickle.load(file_obj)  # noqa: B12 — central compat loader
    except ModuleNotFoundError as exc:
        msg = str(exc)
        if "numpy._core" in msg or "numpy.core" in msg:
            _ensure_pickle_compat()
            try:
                file_obj.seek(0)
            except Exception:
                pass
            return pickle.load(file_obj)  # noqa: B12 — central compat loader
        raise

# Sidecar file extension appended to the pickle path.
SIDECAR_SUFFIX = ".manifest.json"

# Tracked runtime packages. Keep small to avoid import-time bloat.
_TRACKED_PACKAGES: Tuple[str, ...] = ("numpy", "pandas", "scipy")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    """Outcome of ``verify_manifest``.

    ``ok`` is True for both valid manifests and legacy (no-manifest) loads.
    Callers that need to reject manifest mismatches should treat
    ``ok and not legacy`` as the strictly-verified case.
    """

    ok: bool
    legacy: bool = False
    mismatches: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def __bool__(self) -> bool:  # convenience for legacy callers
        return self.ok


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def _canonicalize(obj: Any) -> Any:
    """Recursively coerce ``obj`` into a JSON-serializable canonical form.

    NaN/Inf are encoded explicitly (``{"__nan__": True}`` /
    ``{"__inf__": "pos"|"neg"}``) so they do not silently coerce to None.
    Numpy arrays and pandas Series are reduced to a stable digest rather
    than expanded into JSON lists.
    """
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        if math.isnan(f):
            return {"__nan__": True}
        if math.isinf(f):
            return {"__inf__": "pos" if f > 0 else "neg"}
        return f
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_sha256__": hashlib.sha256(bytes(obj)).hexdigest(),
                "len": len(obj)}
    if isinstance(obj, np.ndarray):
        return _hash_ndarray(obj)
    if isinstance(obj, pd.Timestamp):
        if pd.isna(obj):
            return {"__nan__": True}
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, pd.DatetimeIndex):
        return _hash_datetime_index(obj)
    if isinstance(obj, pd.Index):
        return [_canonicalize(x) for x in obj.tolist()]
    if isinstance(obj, pd.Series):
        return _hash_pandas_series(obj)
    if isinstance(obj, pd.DataFrame):
        return {
            "__df__": True,
            "columns": [_canonicalize(c) for c in obj.columns.tolist()],
            "index": _canonicalize(obj.index),
            "values": _hash_ndarray(obj.to_numpy()),
        }
    if isinstance(obj, Mapping):
        return {str(k): _canonicalize(v)
                for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(x) for x in obj]
    if isinstance(obj, (set, frozenset)):
        return [_canonicalize(x) for x in sorted(obj, key=lambda x: str(x))]
    # Fallback: stringify so the canonical form remains deterministic
    return {"__repr__": repr(obj)}


def _hash_ndarray(arr: np.ndarray) -> dict:
    """Hash an ndarray by ``(dtype, shape, sha256(bytes))``.

    Object arrays cannot tobytes(); fall back to canonicalizing the list
    so the digest still depends on element content.
    """
    if arr.dtype == object:
        return {
            "__np_object__": True,
            "dtype": str(arr.dtype),
            "shape": list(arr.shape),
            "values": _canonicalize(arr.tolist()),
        }
    contig = np.ascontiguousarray(arr)
    digest = hashlib.sha256(contig.tobytes()).hexdigest()
    return {
        "__np_array__": True,
        "dtype": str(arr.dtype),
        "shape": list(arr.shape),
        "sha256": digest,
    }


def _hash_datetime_index(idx: pd.DatetimeIndex) -> dict:
    arr = idx.to_numpy(dtype="datetime64[ns]").astype("int64", copy=False)
    digest = hashlib.sha256(arr.tobytes()).hexdigest()
    return {
        "__dt_index__": True,
        "len": len(idx),
        "sha256": digest,
        "tz": str(idx.tz) if idx.tz is not None else None,
    }


def _hash_pandas_series(s: pd.Series) -> dict:
    return {
        "__series__": True,
        "name": _canonicalize(s.name),
        "index": _canonicalize(s.index),
        "values": _hash_ndarray(s.to_numpy()),
    }


def _canonical_blob(library_dict: Mapping[str, Any]) -> bytes:
    """Produce the canonical JSON bytes used as the content_hash input."""
    canonical = {}
    for k, v in library_dict.items():
        if k in VOLATILE_LIBRARY_KEYS:
            continue
        canonical[str(k)] = _canonicalize(v)
    return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def content_hash(library_dict: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical payload, with volatile keys excluded."""
    return hashlib.sha256(_canonical_blob(library_dict)).hexdigest()


def source_close_hash(close_series: Any) -> Optional[str]:
    """Stable digest of a Close price series (or DataFrame with a Close column).

    Returns ``None`` when no usable Close is available — this is the signal
    that a producer/consumer cannot perform a source comparison and should
    fall back to legacy/best-effort behavior.
    """
    if close_series is None:
        return None
    if isinstance(close_series, pd.DataFrame):
        if "Close" not in close_series.columns:
            return None
        s = close_series["Close"]
    elif isinstance(close_series, pd.Series):
        s = close_series
    else:
        try:
            s = pd.Series(close_series)
        except Exception:
            return None

    if len(s) == 0:
        return hashlib.sha256(b"empty-close").hexdigest()

    h = hashlib.sha256()
    h.update(b"close|")
    h.update(str(s.dtype).encode("utf-8"))
    h.update(b"|values|")
    try:
        arr = np.ascontiguousarray(s.to_numpy())
        h.update(arr.tobytes())
    except Exception:
        h.update(repr(list(s.tolist())).encode("utf-8"))
    h.update(b"|index|")
    if isinstance(s.index, pd.DatetimeIndex):
        idx_arr = s.index.to_numpy(dtype="datetime64[ns]").astype("int64", copy=False)
        h.update(idx_arr.tobytes())
        h.update(str(s.index.tz).encode("utf-8"))
    else:
        try:
            h.update(np.ascontiguousarray(s.index.to_numpy()).tobytes())
        except Exception:
            h.update(repr(list(s.index)).encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Runtime / git capture
# ---------------------------------------------------------------------------


def _capture_package_versions() -> dict:
    versions: dict = {
        "python": (
            f"{sys.version_info.major}."
            f"{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        ),
    }
    for pkg in _TRACKED_PACKAGES:
        ver = "unknown"
        try:
            ver = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            try:
                mod = importlib.import_module(pkg)
                ver = str(getattr(mod, "__version__", "unknown"))
            except Exception:
                ver = "unknown"
        except Exception:
            ver = "unknown"
        versions[pkg] = ver
    return versions


def _capture_git_info(repo_root: Optional[Path] = None) -> dict:
    """Best-effort git capture. Returns ``{"commit": ..., "dirty": ...}``.

    Always returns a dict; never raises. ``commit`` is either a SHA or
    ``"unknown"``. ``dirty`` is True/False/None (None when undetermined).
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent
    repo_root = Path(repo_root)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": None}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"commit": "unknown", "dirty": None}
    commit_sha = proc.stdout.strip()
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if status.returncode != 0:
            return {"commit": commit_sha, "dirty": None}
        dirty = bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return {"commit": commit_sha, "dirty": None}
    return {"commit": commit_sha, "dirty": dirty}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _builder_identity() -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
    return f"{user}@{platform.node() or 'unknown-host'}"


# ---------------------------------------------------------------------------
# Manifest construction / IO
# ---------------------------------------------------------------------------


def _date_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (pd.Timestamp, datetime)):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _derive_date_range(
    library_dict: Mapping[str, Any],
    *,
    source_close: Any,
) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Best-effort extraction of (start, end, row_count) from library/source."""
    start = library_dict.get("start_date")
    end = library_dict.get("end_date")
    row_count = library_dict.get("num_days") or library_dict.get("row_count")
    if start and end and row_count:
        return _date_str(start), _date_str(end), int(row_count)
    dates = library_dict.get("dates") or library_dict.get("date_index")
    if dates is not None and len(dates) > 0:
        try:
            row_count = row_count or len(dates)
            start = start or _date_str(dates[0])
            end = end or _date_str(dates[-1])
            return _date_str(start), _date_str(end), int(row_count)
        except Exception:
            pass
    if source_close is not None:
        try:
            if isinstance(source_close, pd.DataFrame):
                idx = source_close.index
            elif isinstance(source_close, pd.Series):
                idx = source_close.index
            else:
                idx = None
            if idx is not None and len(idx) > 0:
                return (
                    _date_str(idx[0]),
                    _date_str(idx[-1]),
                    int(len(idx)),
                )
        except Exception:
            pass
    return _date_str(start), _date_str(end), int(row_count) if row_count else None


def _build_source_data_block(source_close: Any) -> Optional[dict]:
    if source_close is None:
        return None
    digest = source_close_hash(source_close)
    if digest is None:
        return None
    if isinstance(source_close, pd.DataFrame):
        s = source_close["Close"] if "Close" in source_close.columns else None
    elif isinstance(source_close, pd.Series):
        s = source_close
    else:
        s = None
    if s is None:
        return {"hash_method": "sha256", "source_close_hash": digest}
    return {
        "hash_method": "sha256",
        "source_close_hash": digest,
        "row_count": int(len(s)),
        "start": _date_str(s.index[0]) if len(s) > 0 else None,
        "end": _date_str(s.index[-1]) if len(s) > 0 else None,
    }


def build_manifest(
    library_dict: Mapping[str, Any],
    *,
    artifact_type: str,
    ticker: str,
    resolved_symbol: Optional[str] = None,
    interval: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    source_close: Any = None,
    engine_version: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> dict:
    """Produce a manifest dict from ``library_dict`` and caller-supplied context.

    The manifest is NOT inserted into ``library_dict``; use
    ``attach_manifest`` for that. ``content_hash`` is computed from the
    library dict with ``VOLATILE_LIBRARY_KEYS`` excluded.
    """
    if not artifact_type:
        raise ValueError("artifact_type is required")
    if not ticker:
        raise ValueError("ticker is required")

    start, end, row_count = _derive_date_range(
        library_dict, source_close=source_close
    )
    source_block = _build_source_data_block(source_close)
    pkg_versions = _capture_package_versions()
    git_info = _capture_git_info(repo_root)

    # Engine version: caller override > library field > "unknown"
    eng_ver = engine_version or library_dict.get("engine_version") or "unknown"

    manifest = {
        # Stable provenance
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_type": str(artifact_type),
        "ticker": str(ticker),
        "resolved_symbol": str(resolved_symbol) if resolved_symbol else None,
        "interval": str(interval) if interval else None,
        "date_range_start": start,
        "date_range_end": end,
        "row_count": row_count,
        "source_data": source_block,
        "params": _canonicalize(dict(params)) if params else None,
        "engine_version": str(eng_ver),
        "git_commit": git_info["commit"],
        "git_dirty": git_info["dirty"],
        "package_versions": pkg_versions,
        # Volatile provenance
        "build_timestamp": _utc_now_iso(),
        "builder_identity": _builder_identity(),
        "host_platform": platform.platform(),
    }

    # content_hash: computed last, after all manifest-independent fields are
    # finalized. The library_dict may already carry a stale _manifest from a
    # prior write; the canonical-blob filter strips it via VOLATILE_LIBRARY_KEYS.
    manifest["content_hash"] = content_hash(library_dict)
    return manifest


def _sidecar_path_for(pickle_path: Any) -> Path:
    p = Path(pickle_path)
    return p.with_name(p.name + SIDECAR_SUFFIX)


def _write_sidecar(sidecar_path: Path, manifest: Mapping[str, Any]) -> None:
    sidecar_path = Path(sidecar_path)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = sidecar_path.with_suffix(sidecar_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, sort_keys=True, indent=2)
    os.replace(tmp, sidecar_path)


def attach_manifest(
    library_dict: dict,
    sidecar_path: Any,
    *,
    artifact_type: str,
    ticker: str,
    resolved_symbol: Optional[str] = None,
    interval: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    source_close: Any = None,
    engine_version: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> Tuple[dict, dict]:
    """Build, embed, and sidecar-persist a manifest for ``library_dict``.

    Mutates ``library_dict`` in place (sets ``library_dict[_manifest]``)
    and writes the JSON sidecar to ``sidecar_path``.

    Returns ``(library_dict, manifest)``.
    """
    # Strip any prior manifest so the new content_hash is computed against
    # the bare payload and not against an old self-referential manifest.
    library_dict.pop(MANIFEST_FIELD, None)

    manifest = build_manifest(
        library_dict,
        artifact_type=artifact_type,
        ticker=ticker,
        resolved_symbol=resolved_symbol,
        interval=interval,
        params=params,
        source_close=source_close,
        engine_version=engine_version,
        repo_root=repo_root,
    )
    library_dict[MANIFEST_FIELD] = manifest

    if sidecar_path is not None:
        try:
            _write_sidecar(_sidecar_path_for(sidecar_path), manifest)
        except OSError as exc:
            LOGGER.warning("Failed to write manifest sidecar at %s: %s",
                           sidecar_path, exc)
    return library_dict, manifest


def _read_sidecar(sidecar_path: Path) -> Optional[dict]:
    try:
        with open(sidecar_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def read_manifest(
    library_dict: Optional[Mapping[str, Any]] = None,
    sidecar_path: Any = None,
) -> Optional[dict]:
    """Return the manifest, preferring embedded over sidecar.

    Caller passes the pickle path (not the sidecar path); the .manifest.json
    sidecar is derived. Returns ``None`` when neither source has a manifest.
    """
    embedded = None
    if library_dict is not None:
        embedded = library_dict.get(MANIFEST_FIELD)
    sidecar = None
    if sidecar_path is not None:
        sidecar = _read_sidecar(_sidecar_path_for(sidecar_path))
    if embedded is None and sidecar is None:
        return None
    if embedded is not None and sidecar is not None:
        try:
            if (embedded.get("content_hash") != sidecar.get("content_hash")
                    or embedded.get("build_timestamp")
                    != sidecar.get("build_timestamp")):
                LOGGER.warning(
                    "Manifest sidecar disagrees with embedded manifest at %s; "
                    "preferring embedded (atomic with pickle).",
                    sidecar_path,
                )
        except AttributeError:
            pass
        return dict(embedded)
    return dict(embedded if embedded is not None else sidecar)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _params_subset_diff(
    requested: Any, manifest_params: Any, *, prefix: str = ""
) -> list:
    """Recursive subset diff. Returns a list of (path, manifest, requested)
    tuples for keys that requested but where the manifest disagrees.
    """
    diffs: list = []
    if requested is None:
        return diffs
    if not isinstance(requested, Mapping):
        if requested != manifest_params:
            diffs.append((prefix or ".", manifest_params, requested))
        return diffs
    if not isinstance(manifest_params, Mapping):
        diffs.append((prefix or ".", manifest_params, requested))
        return diffs
    for k, v in requested.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if k not in manifest_params:
            diffs.append((path, "<missing>", v))
            continue
        sub_manifest = manifest_params[k]
        if isinstance(v, Mapping):
            diffs.extend(_params_subset_diff(v, sub_manifest, prefix=path))
        else:
            if sub_manifest != v:
                # Allow numeric equality across int/float
                if isinstance(v, (int, float)) and isinstance(
                    sub_manifest, (int, float)
                ) and float(sub_manifest) == float(v):
                    continue
                diffs.append((path, sub_manifest, v))
    return diffs


def verify_manifest(
    library_dict: Mapping[str, Any],
    sidecar_path: Any = None,
    *,
    strict: bool = False,
    requested_params: Optional[Mapping[str, Any]] = None,
    current_source_close: Any = None,
    cache_path: Any = None,
) -> VerificationResult:
    """Verify the manifest embedded in ``library_dict`` (with sidecar fallback).

    Behavior summary:

      - No manifest at all -> ``ok=True, legacy=True`` with a warning. The
        caller is allowed to use the library; it just predates Phase 3A.
      - Embedded vs. sidecar drift -> warn, prefer embedded.
      - Recomputed ``content_hash`` mismatch -> ``ok=False`` (mismatches
        list non-empty). The library has been mutated since the manifest
        was attached.
      - ``requested_params`` provided -> recursive subset comparison; only
        the keys the caller cares about are compared. Manifest params with
        extra keys are fine.
      - ``current_source_close`` provided -> hash and compare to
        ``manifest.source_data.source_close_hash``. Skipped when not
        provided (Risk 2).
      - Git / package mismatches: warn under strict=False, fail under
        strict=True.
      - ``cache_path`` (Phase 3B-1) -> when supplied, the central loader's
        path is used to look up the previously-computed content_hash via
        the LRU cache keyed by ``(resolved_path, mtime_ns, size)``. Direct
        callers that pass ``cache_path=None`` keep the strict in-memory
        recomputation; the central loader supplies it explicitly.

    Returns a ``VerificationResult``; truthy when ``ok``.
    """
    manifest = read_manifest(library_dict, sidecar_path=sidecar_path)
    if manifest is None:
        return VerificationResult(
            ok=True,
            legacy=True,
            warnings=["no_manifest (legacy library)"],
        )

    mismatches: list = []
    warnings: list = []

    # 1) content_hash check
    expected = manifest.get("content_hash")
    actual = _cached_content_hash(library_dict, cache_path)
    if expected is None:
        warnings.append("manifest_missing_content_hash")
    elif expected != actual:
        mismatches.append(
            ("content_hash", expected, actual)
        )

    # 2) requested_params subset
    if requested_params is not None:
        m_params = manifest.get("params") or {}
        for diff in _params_subset_diff(dict(requested_params), m_params):
            mismatches.append((f"params.{diff[0]}", diff[1], diff[2]))

    # 3) source close hash
    if current_source_close is not None:
        cur_hash = source_close_hash(current_source_close)
        m_source = manifest.get("source_data") or {}
        m_source_hash = m_source.get("source_close_hash")
        if m_source_hash is None:
            warnings.append("manifest_missing_source_close_hash")
        elif cur_hash != m_source_hash:
            mismatches.append(("source_close_hash", m_source_hash, cur_hash))

    # 4) runtime version drift (warn-only unless strict)
    runtime_versions = _capture_package_versions()
    m_versions = manifest.get("package_versions") or {}
    for pkg in _TRACKED_PACKAGES + ("python",):
        m_ver = m_versions.get(pkg)
        cur_ver = runtime_versions.get(pkg)
        if m_ver and cur_ver and m_ver != cur_ver:
            entry = (f"package_versions.{pkg}", m_ver, cur_ver)
            if strict:
                mismatches.append(entry)
            else:
                warnings.append(entry)

    # 5) schema version
    sv = manifest.get("schema_version")
    if sv != MANIFEST_SCHEMA_VERSION:
        warnings.append(("schema_version", MANIFEST_SCHEMA_VERSION, sv))

    ok = not mismatches
    return VerificationResult(
        ok=ok, legacy=False, mismatches=mismatches, warnings=warnings
    )


# ---------------------------------------------------------------------------
# Convenience: attach-or-refresh helper for repair persists
# ---------------------------------------------------------------------------


def refresh_or_attach_manifest(
    library_dict: dict,
    sidecar_path: Any,
    *,
    artifact_type: str,
    ticker: str,
    resolved_symbol: Optional[str] = None,
    interval: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    source_close: Any = None,
    engine_version: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> Tuple[dict, dict, bool]:
    """For metadata-repair persists.

    If ``library_dict`` already carries a manifest, refresh ``content_hash``
    and the volatile fields while preserving the existing ``source_data``
    block (Risk 2: don't fabricate a source hash mid-flight if no
    ``source_close`` is available). Otherwise, behave like
    ``attach_manifest``.

    Returns ``(library_dict, manifest, was_refresh)``.
    """
    existing = library_dict.get(MANIFEST_FIELD)
    if existing is None:
        lib, man = attach_manifest(
            library_dict,
            sidecar_path,
            artifact_type=artifact_type,
            ticker=ticker,
            resolved_symbol=resolved_symbol,
            interval=interval,
            params=params,
            source_close=source_close,
            engine_version=engine_version,
            repo_root=repo_root,
        )
        return lib, man, False

    # Refresh path: build new manifest, but if no source_close was supplied
    # and the existing manifest has a source_data block, preserve it.
    library_dict.pop(MANIFEST_FIELD, None)
    new_manifest = build_manifest(
        library_dict,
        artifact_type=artifact_type,
        ticker=ticker,
        resolved_symbol=resolved_symbol,
        interval=interval,
        params=params,
        source_close=source_close,
        engine_version=engine_version,
        repo_root=repo_root,
    )
    if source_close is None and existing.get("source_data") is not None:
        new_manifest["source_data"] = existing["source_data"]
    library_dict[MANIFEST_FIELD] = new_manifest
    if sidecar_path is not None:
        try:
            _write_sidecar(_sidecar_path_for(sidecar_path), new_manifest)
        except OSError as exc:
            LOGGER.warning(
                "Failed to refresh manifest sidecar at %s: %s",
                sidecar_path, exc,
            )
    return library_dict, new_manifest, True


# ---------------------------------------------------------------------------
# Phase 3B-1: central verified signal-library loader
# ---------------------------------------------------------------------------


def load_verified_signal_library(
    path: Any,
    *,
    requested_params: Optional[Mapping[str, Any]] = None,
    strict: bool = False,
    expected_type: type = dict,
    cache: bool = True,
) -> Tuple[Optional[Any], VerificationResult]:
    """Load a signal-library pickle and verify its provenance manifest.

    Bundles the four steps every signal-library consumer used to do by
    hand: open + ``pickle_load_compat`` + type-check + ``verify_manifest``.
    Each consumer keeps its own policy for what to do with the result
    (rebuild, slow-path fallback, skip candidate, fast-path disable).

    Returns ``(library_dict, VerificationResult)``. On a load error, the
    library is ``None`` and the result has ``ok=False, legacy=False`` with
    a single ``("load_error", error_type, message)`` mismatch so the caller
    can branch on the corrupt-file case (e.g. quarantine to ``.corrupt``).

    Parameters:
      - ``path``: pickle path, used both for ``open`` and for the manifest
        sidecar lookup / hash cache key.
      - ``requested_params``: forwarded to ``verify_manifest``.
      - ``strict``: forwarded to ``verify_manifest``.
      - ``expected_type``: typically ``dict``. A non-matching loaded value
        produces ``("type_error", expected, actual)``.
      - ``cache``: when True, the path's stat-derived key feeds the LRU
        content_hash cache. Disable when the caller wants a guaranteed
        recomputation (the env-var disable also does this globally).
    """
    try:
        with open(path, "rb") as fh:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", category=DeprecationWarning,
                    message=".*numpy.core.numeric.*",
                )
                warnings.filterwarnings(
                    "ignore", category=DeprecationWarning,
                    message=".*numpy._core.numeric.*",
                )
                # Suppress generic DeprecationWarnings the per-engine loaders
                # also suppressed; the shimmed pickle path can emit them on
                # legacy libraries.
                warnings.filterwarnings(
                    "ignore", category=DeprecationWarning,
                )
                data = pickle_load_compat(fh)
    except (pickle.UnpicklingError, EOFError) as exc:
        return None, VerificationResult(
            ok=False, legacy=False,
            mismatches=[("load_error", type(exc).__name__, str(exc))],
        )
    except ModuleNotFoundError as exc:
        return None, VerificationResult(
            ok=False, legacy=False,
            mismatches=[("load_error", type(exc).__name__, str(exc))],
        )
    except OSError as exc:
        return None, VerificationResult(
            ok=False, legacy=False,
            mismatches=[("load_error", type(exc).__name__, str(exc))],
        )

    if not isinstance(data, expected_type):
        return None, VerificationResult(
            ok=False, legacy=False,
            mismatches=[(
                "type_error",
                expected_type.__name__ if hasattr(expected_type, "__name__")
                else str(expected_type),
                type(data).__name__,
            )],
        )

    result = verify_manifest(
        data,
        sidecar_path=path,
        strict=strict,
        requested_params=requested_params,
        cache_path=path if cache else None,
    )
    return data, result


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "MANIFEST_FIELD",
    "VOLATILE_LIBRARY_KEYS",
    "VerificationResult",
    "build_manifest",
    "attach_manifest",
    "read_manifest",
    "verify_manifest",
    "refresh_or_attach_manifest",
    "content_hash",
    "source_close_hash",
    # Phase 3B-1
    "manifest_hash_cache_clear",
    "manifest_hash_cache_info",
    "pickle_load_compat",
    "load_verified_signal_library",
]
