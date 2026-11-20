# SA1 Electricity Price — Prediction & Buy/Export Planner

A Jupyter notebook that forecasts the South Australian (SA1) wholesale electricity price from 5 minutes to 48 hours ahead, compares it with the actual price, and suggests the cheapest times to import / charge and the most expensive times to export.

For how it works in detail — features, models, how a prediction is made, results, API reference and how to integrate it into a larger model — see **`DOCUMENTATION.md`**.

Open **`SA1_price_prediction.ipynb`**. Its first section, *"How this notebook works"*, explains the pipeline, every section and the main functions.

## What's in this folder

| Path | What it is |
|---|---|
| `SA1_price_prediction.ipynb` | The notebook. It already contains the outputs from the last run, so you can read it without running anything |
| `data/sa1_dispatch_price.csv` | 5 years of AEMO 5-minute SA1 prices (Oct 2021 – Sep 2026) |
| `data/sa1_predispatch.csv` | Every AEMO pre-dispatch (forecast) run for SA1 since Oct 2021 |
| `data/sa1_predispatch_live.csv` | Cache of recent AEMO forecast runs fetched live (the notebook adds to it) |
| `scripts/download_history.py` | Rebuilds / extends the price history from AEMO via `nemosis` |
| `scripts/download_predispatch.py` | Rebuilds / extends the AEMO forecast history |
| `.env` | OpenElectricity API key used for live prices (**keep this private — don't post it publicly**) |
| `requirements.txt` | Python packages and the versions used |
| `DOCUMENTATION.md` | Technical documentation and integration guide |

The `.env` file name starts with a dot, so Finder and Explorer hide it by default (on a Mac, press Cmd+Shift+. to show hidden files).

## Setup (once)

Needs Python 3.9 or later.

```bash
cd "PriceAPI analysis"
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m ipykernel install --user --name priceapi --display-name "Python (PriceAPI)"
jupyter lab
```

In Jupyter, open the notebook and pick the **Python (PriceAPI)** kernel.

## Running it

- **Run all cells.** The first full run takes about 15 minutes, most of it model training in sections 5 and 7. Section 10's live check also downloads about 50 MB of recent AEMO forecasts the first time.
- **After that, for fresh forecasts,** re-run sections 8–10, or call the functions in a new cell:

| Call | Answers |
|---|---|
| `current_price()` | What is the price right now? |
| `price_at("2026-10-23 18:30", tz="Australia/Adelaide")` | What will (or did) the price be at this time? |
| `plan_next(hours=48, window="2h", tz="Australia/Adelaide")` | When should we charge / import, and when should we export? |
| `day_segments(days=2, tz="Australia/Adelaide")` | Which part of today and tomorrow is cheap or expensive? |
| `daily_plan("today")` / `daily_plan("tomorrow")` | Half-hour plan: when to export solar, self-use, or take from the grid |

Sections 12 and 13 at the end of the notebook do the same things with editable settings: type a date and time, or set the day, whether you have a battery, and optional c/kWh thresholds.

Prices are wholesale $/MWh; divide by 10 for c/kWh. Times are NEM time (AEST, UTC+10) unless you pass `tz=`.

## Keeping the data current

AEMO publishes each month's archive about 1–2 weeks after the month ends. To add new months, run both scripts, then re-run the whole notebook to retrain:

```bash
python scripts/download_history.py "2021/10/01 00:00:00" "2026/11/01 00:00:00"
python scripts/download_predispatch.py 2021-10 2026-10
```

They cache AEMO's raw files in `nem_cache/`, which is not included in this zip. The first run re-downloads about 400 MB.

## Notes

- **The API key:** it is a free OpenElectricity *Community* key. Without it (or if it stops working), everything except the live-price parts still runs. Anyone can get their own at platform.openelectricity.org.au and put it in `.env` as `OPENELECTRICITY_API_KEY=...`.
- **Wholesale prices:** these only flow through to a bill on a pass-through retailer (e.g. Amber) or a VPP.
- **Limitations:** see section 11 of the notebook.
