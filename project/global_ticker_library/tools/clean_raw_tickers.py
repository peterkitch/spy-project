#!/usr/bin/env python3
"""
Cleaner for raw Yahoo tickers from URLs or local files.

- Preserves global exchange suffix tickers exactly (e.g., 0524.HK, GGAL.BA, 5227.KL, AX-UN.TO, DCON-R.BK)
- Preserves FX (=X), futures (=F), indices (^...), crypto pairs (-USD)
- Preserves mixed hyphen+suffix formats (AX-UN.TO, DCON-R.BK)
- US share-class canonicalization (BRK.B -> BRK-B) is **opt-in** via --normalize-share-classes
- Rejects bare numeric tokens (e.g., "1022000") unless --allow-bare-numeric is given
- Can skip symbols already present in registry (default ON)
- Writes deduped symbols to data/manual_input.txt
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path
from typing import Iterator, List, Set, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Make package importable when called as a script
PKG_ROOT = Path(__file__).resolve().parents[2]  # .../project
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from global_ticker_library.gl_config import DATA_DIR, MANUAL_FILE, DEFAULT_UA, REQUEST_TIMEOUT

# get_all_symbols is optional — if missing, we simply won't skip anything
try:
    from global_ticker_library.registry import get_all_symbols
except Exception:
    def get_all_symbols() -> Set[str]:
        return set()

# ---------- Exchange suffixes ----------

# Bring in any suffixes maintained in config
try:
    from global_ticker_library.gl_config import VALID_SUFFIXES as CFG_VALID_SUFFIXES
    _CFG_SUFS = {s.lstrip(".").upper() for s in (CFG_VALID_SUFFIXES or [])}
except Exception:
    _CFG_SUFS = set()

# Curated additions: broad, not exhaustive, but covers common Yahoo exchanges
_EXTRA_SUFS = {
    # Americas
    "BA",      # Buenos Aires
    "BR",      # Brussels (Yahoo uses .BR)
    "MX",      # Mexico
    "SA",      # Brazil B3
    "TO", "V", # TSX / TSX-V
    # Europe
    "AS", "PA", "MC", "MI", "SW", "ST", "CO", "OL",
    "DE", "F", "MU", "BE",  # Germany: XETRA/Frankfurt/Munich/Berlin
    "L",                    # London
    "HE",                   # Helsinki
    "VI",                   # Vienna
    "AT",                   # Austria
    "IS",                   # Iceland
    # APAC / ME
    "HK",                   # Hong Kong
    "T",                    # Tokyo
    "KS", "KQ",             # Korea
    "SS", "SZ",             # China
    "TW", "TWO",            # Taiwan / OTC
    "AX",                   # Australia
    "JK",                   # Jakarta
    "KL",                   # Kuala Lumpur
    "NS", "BO",             # India (NSE / BSE)
    "SG", "SI",             # Singapore (Yahoo uses .SI)
    "TA",                   # Tel Aviv
    "NZ",                   # New Zealand
    "BK",                   # Bangkok
    "PH",                   # Philippines
}

_EXCHANGE_SUFFIXES = _CFG_SUFS | _EXTRA_SUFS

# ---------- fetching ----------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": DEFAULT_UA,
        "Accept": "text/plain, text/csv, application/json",
        "Referer": "https://finance.yahoo.com/"
    })
    retry = Retry(
        total=3,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s

def _stream_lines(src: str) -> Iterator[str]:
    if src.startswith(("http://", "https://")):
        with _session().get(src, timeout=REQUEST_TIMEOUT, stream=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                for line in chunk.decode("utf-8", errors="ignore").splitlines():
                    yield line
    else:
        with open(src, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                yield line

# ---------- recognition ----------

# Grab plausible tokens; validate shape next. Includes letters/digits and Yahoo punctuation.
_TOKEN_RE = re.compile(r"[A-Za-z0-9\^\.\-\=\&]{1,24}")

def _is_index(s: str) -> bool:
    return s.startswith("^") and 1 <= len(s) <= 22

def _is_fx(s: str) -> bool:
    # e.g., EURUSD=X or USDJPY=X
    return s.endswith("=X") and re.fullmatch(r"[A-Z]{6,10}=X", s) is not None

def _is_future(s: str) -> bool:
    # e.g., ES=F, CL=F
    return s.endswith("=F") and re.fullmatch(r"[A-Z0-9\.\-]{1,10}=F", s) is not None

def _split_suffix(s: str) -> Tuple[str, str] | Tuple[str, None]:
    """Return (base, suffix) if symbol has ONE trailing .SUF (letters), else (s, None)."""
    if "." not in s:
        return s, None
    base, suf = s.rsplit(".", 1)
    if suf.isalpha():
        return base, suf
    return s, None

def _has_exchange_suffix(s: str) -> bool:
    base, suf = _split_suffix(s)
    return suf is not None and suf.upper() in _EXCHANGE_SUFFIXES and len(base) >= 1

def _is_plain_equity_with_letters(s: str) -> bool:
    """
    Plain equity without an exchange suffix must include at least one letter.
    Allows optional series chunks after hyphens: -UN/-W/-R/-PA/-PJ/-A/-B, etc.
    Examples: NQP, CRANE-R, HGBS11, BTC-USD
    """
    return re.fullmatch(r"(?=.*[A-Z])[A-Z0-9\&]{1,21}(\-[A-Z0-9]{1,5}){0,3}", s) is not None

def _is_bare_numeric(s: str) -> bool:
    # Reject things like "1022000"; valid numeric tickers normally carry a suffix (e.g., 0524.HK)
    return s.isdigit()

# ---------- canonicalization ----------

def _normalize_us_share_class(s: str, enable: bool) -> str:
    """
    BRK.B -> BRK-B **only** when:
      - enabled explicitly
      - there is exactly one dot
      - the part after the dot is NOT a known exchange suffix
      - the class part is 1–2 letters (A,B,C,...)
    Otherwise, return s unchanged.
    """
    if not enable:
        return s
    base, suf = _split_suffix(s)
    if suf is None:
        return s
    # If .L/.T/.HK/... it's an exchange -> preserve exactly
    if suf.upper() in _EXCHANGE_SUFFIXES:
        return s
    # Class letter(s) only, and base doesn't already have a dash
    if re.fullmatch(r"[A-Z]{1,2}", suf) and "-" not in base:
        return f"{base}-{suf}"
    return s

def _canonical(raw: str, *, enable_shareclass: bool, allow_bare_numeric: bool) -> Tuple[str, str]:
    """
    Returns (symbol, reason) where reason == "" if accepted; otherwise reason explains rejection.
    """
    s = raw.upper().strip()
    if not (1 <= len(s) <= 24):
        return "", "length"

    # Special types: preserve exactly
    if _is_index(s) or _is_fx(s) or _is_future(s):
        return s, ""

    # Exchange suffix? keep exactly as provided
    if _has_exchange_suffix(s):
        return s, ""

    # Bare numeric? reject unless explicitly allowed
    if _is_bare_numeric(s) and not allow_bare_numeric:
        return "", "bare_numeric"

    # Plain equity (must contain at least one letter)
    if _is_plain_equity_with_letters(s):
        s2 = _normalize_us_share_class(s, enable=enable_shareclass)
        return s2, ""

    return "", "invalid_pattern"

# ---------- main cleaning ----------

def clean_symbols_from_stream(
    src: str,
    skip_existing: bool = True,
    excluded_path: str | None = None,
    normalize_share_classes: bool = False,
    allow_bare_numeric: bool = False,
) -> Tuple[int, int, int]:
    """
    Returns (total_seen, kept, excluded).
    """
    seen_total = kept = excluded = 0
    kept_syms: Set[str] = set()
    excluded_rows: List[str] = []  # small notes; fine for one-off batch use

    existing = get_all_symbols() if skip_existing else set()

    print(f"Streaming from: {src}")
    if skip_existing:
        print(f"Skipping {len(existing):,} existing symbols from registry")
    else:
        print("NOT skipping existing symbols - all will be included")

    for line in _stream_lines(src):
        for raw in _TOKEN_RE.findall(line):
            seen_total += 1
            s, reason = _canonical(raw, enable_shareclass=normalize_share_classes,
                                   allow_bare_numeric=allow_bare_numeric)
            if not s:
                excluded += 1
                if excluded_path:
                    excluded_rows.append(f"{raw}\t{reason}\n")
                continue
            if skip_existing and s in existing:
                # Already in DB — skip adding to manual input
                continue
            kept_syms.add(s)

            if seen_total % 10000 == 0:
                print(f"  ... processed {seen_total:,} tokens -> {len(kept_syms):,} kept", flush=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_FILE.write_text("\n".join(sorted(kept_syms)), encoding="utf-8")
    kept = len(kept_syms)

    if excluded_path:
        Path(excluded_path).write_text("".join(excluded_rows), encoding="utf-8")

    return seen_total, kept, excluded

# ---------- CLI ----------

def main():
    p = argparse.ArgumentParser(description="Clean raw Yahoo tickers and write to manual_input.txt")
    p.add_argument("--src", required=True, help="URL or local path to raw text/CSV")
    p.add_argument("--skip-existing", dest="skip_existing", action="store_true", help="Skip symbols already present in registry (default)")
    p.add_argument("--no-skip-existing", dest="skip_existing", action="store_false", help="Do not skip existing registry symbols")
    p.add_argument("--write-excluded", default=None, help="Optional path to write excluded items and reasons")
    p.add_argument("--normalize-share-classes", action="store_true",
                   help="Convert US share class dot form to dash (BRK.B -> BRK-B). OFF by default.")
    p.add_argument("--allow-bare-numeric", action="store_true",
                   help="Allow bare numeric tokens like '1022000' (normally rejected).")
    p.set_defaults(skip_existing=True)

    args = p.parse_args()

    total, kept, rej = clean_symbols_from_stream(
        src=args.src,
        skip_existing=args.skip_existing,
        excluded_path=args.write_excluded,
        normalize_share_classes=args.normalize_share_classes,
        allow_bare_numeric=args.allow_bare_numeric,
    )
    print("\n=== CLEAN REPORT ===")
    print(f"Scanned:  {total:,}")
    print(f"Kept:     {kept:,}")
    print(f"Rejected: {rej:,}")
    print(f"Output -> {MANUAL_FILE}")
    if args.write_excluded:
        print(f"Excluded items written to: {args.write_excluded}")

if __name__ == "__main__":
    main()