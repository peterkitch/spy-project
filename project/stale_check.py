# stale_check.py
# Requires: pip install yfinance pandas pytz

import time
import yfinance as yf
import pandas as pd
import datetime as dt
import pytz
import numpy as np

symbols = [
    "0619.HK","0812.HK","3152.TWO","3218.TWO","3073.TWO","3508.TWO","5015.TWO","5310.TWO",
    "600121.SS","600635.SS","600801.SS","6144.TWO","6264.TWO","6569.TWO","7198.KL","5LX.F",
    "ARC.V","AR","ARZTD","ASL.L","BIL.BO","BIU2.F","CLAS-B.ST","CODA","CPSS","EBND","EDOC",
    "FREJP","FXE","GTN.F","GTE.TO","HGBL","HUBC","IXJ","JEF","KYL.F","MASA.JK","NFU.MU",
    "OHB.DE","OHB.F","P1I.F","PRBZF","RACE","RBREW.CO","RHHVF","RIT1.TA","RLBD","RNWEF",
    "ROCK-B.CO","RRTS","S3A.F","S3N.SI","STXT","TIG.AX","TMO","TPK.L","UNLRF","YOW.AX"
]

TZ = pytz.timezone("America/Los_Angeles")
STALE_THRESHOLD_DAYS = 7
REQUEST_PAUSE_SEC = 0.35

def last_valid_close_date(sym: str):
    """
    Return (date|None, note)
    Uses period='max' then filters to real closes (non-NaN, finite).
    Also requires Volume > 0 when volume exists.
    """
    try:
        t = yf.Ticker(sym)
        # period='max' avoids missing ancient last trades
        hist = t.history(period="max", interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            return None, "no_data:empty"

        df = hist.copy()
        # Normalize columns that may be missing
        for col in ["Close", "Adj Close", "Volume"]:
            if col not in df.columns:
                if col == "Volume":
                    df[col] = np.nan
                else:
                    df[col] = np.nan

        # Keep rows with a real close
        valid = df[
            df["Close"].notna()
            & np.isfinite(df["Close"])
        ].copy()

        # If volume exists, prefer rows with positive volume
        if valid["Volume"].notna().any():
            posvol = valid[valid["Volume"] > 0]
            if not posvol.empty:
                valid = posvol

        if valid.empty:
            return None, "no_valid_close"

        last_dt = pd.to_datetime(valid.index[-1])
        if last_dt.tzinfo is not None:
            last_dt = last_dt.tz_convert("UTC").tz_localize(None)
        return last_dt.date(), "ok:max"
    except Exception as e:
        return None, f"error:{str(e)[:80]}"

def main():
    today = dt.datetime.now(TZ).date()
    rows = []
    for i, s in enumerate(symbols, 1):
        d, note = last_valid_close_date(s)
        rows.append({"symbol": s, "last_close_date": d, "note": note})
        print(f"[{i}/{len(symbols)}] {s}: {d} ({note})")
        time.sleep(REQUEST_PAUSE_SEC)

    df = pd.DataFrame(rows)
    df["last_close_date"] = pd.to_datetime(df["last_close_date"], errors="coerce")
    today_ts = pd.Timestamp(today)
    df["stale_days"] = (today_ts - df["last_close_date"]).dt.days
    df["stale_days"] = df["stale_days"].fillna(10_000).astype(int)
    df["last_close_date_str"] = df["last_close_date"].dt.date.astype(str)
    df.loc[df["last_close_date"].isna(), "last_close_date_str"] = "None"

    df_display = df[["symbol","last_close_date_str","stale_days","note"]].sort_values("symbol")
    stale = df[(df["last_close_date"].isna()) | (df["stale_days"] > STALE_THRESHOLD_DAYS)]
    stale = stale.sort_values(["stale_days","symbol"], ascending=[False, True])

    print("\n=== LAST VALID CLOSE TABLE ===")
    print(df_display.to_string(index=False))

    print(f"\n=== STALE > {STALE_THRESHOLD_DAYS} DAYS ===")
    print(stale["symbol"].tolist())

    df_display.to_csv("stale_check_results.csv", index=False)
    stale[["symbol","last_close_date_str","stale_days","note"]].to_csv("stale_over_7_days.csv", index=False)
    print("\nWrote: stale_check_results.csv, stale_over_7_days.csv")

if __name__ == "__main__":
    main()
