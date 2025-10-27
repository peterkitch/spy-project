# stale_check.py
# Purpose: Identify Yahoo Finance symbols whose last *valid* close is older than N days.
# Usage:
#   python stale_check.py --symbols "0812.HK, 2FE.DE, AGL.L, ..."    # comma or newline OK
#   python stale_check.py --file symbols.txt                          # file with comma/newline list
#   python stale_check.py                                             # interactive prompt
# Options:
#   --threshold 7               # days; default 7
#   --timezone America/Los_Angeles
#   --pause 0.35                # seconds between requests
#   --require-posvol            # if Volume present, demand Volume > 0 on last valid close
#
# Outputs:
#   stale_check_results.csv     # full table
#   stale_over_threshold.csv    # stale subset
#
# Requires: pip install yfinance pandas pytz numpy

import argparse
import time
import sys
from collections import OrderedDict

import numpy as np
import pandas as pd
import pytz
import yfinance as yf
import datetime as dt


def parse_symbols_from_text(text: str):
    raw = [tok.strip() for tok in text.replace("\n", ",").split(",")]
    seen = OrderedDict()
    for s in raw:
        if s:
            seen[s] = True
    return list(seen.keys())


def load_symbols():
    p = argparse.ArgumentParser(description="Check Yahoo last valid close dates")
    p.add_argument("--symbols", type=str, default=None, help="Comma/newline separated symbols")
    p.add_argument("--file", type=str, default=None, help="Path to file of symbols")
    p.add_argument("--threshold", type=int, default=7, help="Stale threshold in days")
    p.add_argument("--timezone", type=str, default="America/Los_Angeles", help="IANA timezone")
    p.add_argument("--pause", type=float, default=0.35, help="Pause between requests in seconds")
    p.add_argument("--require-posvol", action="store_true", help="Require Volume > 0 when available")
    args = p.parse_args()

    if args.symbols:
        symbols = parse_symbols_from_text(args.symbols)
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            symbols = parse_symbols_from_text(f.read())
    else:
        print("Enter comma-separated tickers. Example: 0812.HK, 2FE.DE, AGL.L, ...")
        user = input("> ")
        symbols = parse_symbols_from_text(user)

    if not symbols:
        print("No symbols provided.", file=sys.stderr)
        sys.exit(2)

    return args, symbols


def last_valid_close_date(sym: str, require_posvol: bool):
    """
    Return (date|None, note)
    period='max' then filter to finite Close. If require_posvol and Volume exists, keep Volume>0.
    """
    try:
        t = yf.Ticker(sym)
        hist = t.history(period="max", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None, "no_data:empty"

        df = hist.copy()
        for col in ["Close", "Adj Close", "Volume"]:
            if col not in df.columns:
                df[col] = np.nan

        valid = df[df["Close"].notna() & np.isfinite(df["Close"])].copy()

        if require_posvol and valid["Volume"].notna().any():
            posvol = valid[valid["Volume"] > 0]
            if not posvol.empty:
                valid = posvol

        if valid.empty:
            return None, "no_valid_close"

        last_dt = pd.to_datetime(valid.index[-1])
        if last_dt.tzinfo is not None:
            # normalize to naive UTC then to date
            last_dt = last_dt.tz_convert("UTC").tz_localize(None)
        return last_dt.date(), "ok:max"
    except Exception as e:
        return None, f"error:{str(e)[:80]}"


def main():
    args, symbols = load_symbols()
    TZ = pytz.timezone(args.timezone)
    today = dt.datetime.now(TZ).date()

    rows = []
    for i, s in enumerate(symbols, 1):
        d, note = last_valid_close_date(s, args.require_posvol)
        rows.append({"symbol": s, "last_close_date": d, "note": note})
        print(f"[{i}/{len(symbols)}] {s}: {d} ({note})")
        time.sleep(max(0.0, args.pause))

    df = pd.DataFrame(rows)
    df["last_close_date"] = pd.to_datetime(df["last_close_date"], errors="coerce")
    today_ts = pd.Timestamp(today)
    df["stale_days"] = (today_ts - df["last_close_date"]).dt.days
    df["stale_days"] = df["stale_days"].fillna(10_000).astype(int)
    df["last_close_date_str"] = df["last_close_date"].dt.date.astype(str)
    df.loc[df["last_close_date"].isna(), "last_close_date_str"] = "None"

    df_display = df[["symbol", "last_close_date_str", "stale_days", "note"]].sort_values("symbol")
    stale = df[df["last_close_date"].isna() | (df["stale_days"] > args.threshold)]
    stale = stale.sort_values(["stale_days", "symbol"], ascending=[False, True])

    print("\n=== LAST VALID CLOSE TABLE ===")
    print(df_display.to_string(index=False))

    print(f"\n=== STALE > {args.threshold} DAYS ===")
    print(stale["symbol"].tolist())

    df_display.to_csv("stale_check_results.csv", index=False)
    stale[["symbol", "last_close_date_str", "stale_days", "note"]].to_csv(
        "stale_over_threshold.csv", index=False
    )
    print("\nWrote: stale_check_results.csv, stale_over_threshold.csv")


if __name__ == "__main__":
    main()
