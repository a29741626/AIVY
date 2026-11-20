"""Download AEMO pre-dispatch (PREDISPATCHPRICE, all runs) for SA1 and save to CSV.

Usage: python scripts/download_predispatch.py [START_MONTH] [END_MONTH]
Months in 'YYYY-MM' format, inclusive. Defaults to Oct 2021 (start of 5-minute settlement) through last month.
Each row is one forecast: when AEMO published it (ISSUED), the half-hour it is
for (PERIOD_END, NEM time, interval-ending) and the forecast price (RRP).
"""
import csv
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "nem_cache" / "predispatch"
OUT = ROOT / "data" / "sa1_predispatch.csv"
BASE = ("https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/{y}/MMSDM_{y}_{m:02d}/"
        "MMSDM_Historical_Data_SQLLoader/PREDISP_ALL_DATA/")
# AEMO renamed the archive files from Aug 2024
FILENAMES = ["PUBLIC_ARCHIVE%23PREDISPATCHPRICE%23ALL%23FILE01%23{y}{m:02d}010000.zip",
             "PUBLIC_DVD_PREDISPATCHPRICE_{y}{m:02d}010000.zip"]

this_month = pd.Timestamp.now().to_period("M")
start = pd.Period(sys.argv[1], "M") if len(sys.argv) > 1 else pd.Period("2021-10", "M")
end = pd.Period(sys.argv[2], "M") if len(sys.argv) > 2 else this_month - 1

CACHE.mkdir(parents=True, exist_ok=True)
rows = []
for month in pd.period_range(start, end, freq="M"):
    zpath = CACHE / f"PREDISPATCHPRICE_ALL_{month.year}{month.month:02d}.zip"
    if not zpath.exists():
        for fname in FILENAMES:
            try:
                urllib.request.urlretrieve((BASE + fname).format(y=month.year, m=month.month), zpath)
                break
            except Exception as exc:
                err = exc
        else:
            print(f"{month}: FAILED ({err})", flush=True)
            continue
    n = 0
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            with zf.open(name) as fh:
                header = None
                for rec in csv.reader(io.TextIOWrapper(fh, encoding="utf-8")):
                    if rec[:3] == ["I", "PREDISPATCH", "REGION_PRICES"]:
                        header = rec
                    elif rec[:3] == ["D", "PREDISPATCH", "REGION_PRICES"] and header:
                        r = dict(zip(header, rec))
                        if r["REGIONID"] == "SA1" and r["INTERVENTION"] == "0":
                            rows.append((r["LASTCHANGED"], r["DATETIME"], float(r["RRP"])))
                            n += 1
    print(f"{month}: {n:,} SA1 forecasts", flush=True)

df = pd.DataFrame(rows, columns=["ISSUED", "PERIOD_END", "RRP"])
df["ISSUED"] = pd.to_datetime(df["ISSUED"])
df["PERIOD_END"] = pd.to_datetime(df["PERIOD_END"])
df = df.drop_duplicates(["ISSUED", "PERIOD_END"]).sort_values(["ISSUED", "PERIOD_END"])
OUT.parent.mkdir(exist_ok=True)
df.to_csv(OUT, index=False)
print(f"Saved {len(df):,} rows to {OUT}")
