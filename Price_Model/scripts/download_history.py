"""Download SA1 dispatch prices from AEMO NEMWeb via nemosis and save to CSV.

Usage: python scripts/download_history.py [START] [END]
Dates in 'YYYY/MM/DD HH:MM:SS' format. Defaults to the 5-minute settlement era
(Oct 2021) through the start of the current month.
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from nemosis import dynamic_data_compiler

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "nem_cache"
OUT = ROOT / "data" / "sa1_dispatch_price.csv"

start = sys.argv[1] if len(sys.argv) > 1 else "2021/10/01 00:00:00"
end = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y/%m/01 00:00:00")

CACHE.mkdir(exist_ok=True)
# Download month by month so one missing archive doesn't kill the whole run
months = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="MS")
frames = []
for m_start, m_end in zip(months[:-1], months[1:]):
    s, e = m_start.strftime("%Y/%m/%d %H:%M:%S"), m_end.strftime("%Y/%m/%d %H:%M:%S")
    try:
        df = dynamic_data_compiler(
            s, e, "DISPATCHPRICE", str(CACHE),
            filter_cols=["REGIONID"], filter_values=(["SA1"],),
            fformat="parquet", keep_csv=False,
        )
        frames.append(df)
        print(f"{s[:7]}: {len(df):,} rows", flush=True)
    except Exception as exc:
        print(f"{s[:7]}: FAILED ({exc})", flush=True)

df = pd.concat(frames, ignore_index=True)
# Keep the final (non-intervention) price for each interval
if "INTERVENTION" in df.columns:
    df = df[df["INTERVENTION"].astype(int) == 0]
df = (df[["SETTLEMENTDATE", "REGIONID", "RRP"]]
      .drop_duplicates("SETTLEMENTDATE")
      .sort_values("SETTLEMENTDATE"))
OUT.parent.mkdir(exist_ok=True)
df.to_csv(OUT, index=False)
print(f"Saved {len(df):,} rows to {OUT} ({df.SETTLEMENTDATE.min()} -> {df.SETTLEMENTDATE.max()})")
