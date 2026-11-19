# Smart battery strategy and AI assistant

Capstone project PG-S2-09 (University of Adelaide) for Ivy Energy.

This folder holds the battery strategy (Assignment 1) and the plain-English AI assistant (Assignment 3):

- **The strategy:** a battery simulator, a baseline and three adaptive strategies (Save money, Balanced, Save energy). A 24-hour plan is re-made every 30 minutes, and live safety rules are checked every 5 minutes.
- **The backtest:** every strategy replayed on every recorded day of the three sandbox homes (SBX-001, SBX-002, SBX-003).
- **The assistant (Ivy bot):** turns a home owner's wish, such as *"save money, but never run out of power at night"*, into checked strategy settings that fit that home's habits.

## Quick start

1. Install the packages once: `pip install -r requirements.txt` (tested with Python 3.10).
2. Open **`Battery Strategy Explorer.html`** in a browser (double-click). It works offline and needs no Python. It shows:
   - any recorded day, minute by minute
   - the plan and every re-plan, with why it chose them
   - the price forecasts
   - Ivy bot (the Gemini key is built in; Ivy bot needs internet)
3. For the full story with all results, open **`strategy_optimizer.ipynb`** in Jupyter and run it top to bottom. It loads the saved results, so it takes minutes, not hours.
4. For the assistant, open **`nl_assistant.ipynb`**, check the Gemini key in cell 1 (`API_KEY`) and run it.

## Files in this folder

| File | What it is |
|---|---|
| `strategy_optimizer.ipynb` | The main notebook: method, results and tests. |
| `nl_assistant.ipynb` | The AI assistant notebook, including the "same wish, three homes" evidence (section 7). |
| `Battery Strategy Explorer.html` | The interactive page: strategies, re-planning, price forecasts and Ivy bot, in one file. |
| `requirements.txt` | The Python packages to install. |
| **`codes/`** | **Our Python code:** |
| `codes/battery.py` | The battery simulator: limits, efficiency (95% each way), and the negative-price rule (the smart strategies switch the solar off and run on paid grid power; the baseline only curtails what it would export). |
| `codes/strategy.py` | The baseline and the three modes, with their settings. |
| `codes/optimizer.py` | The planner: the cheapest 24-hour plan (linear programming), re-made every 30 minutes, plus the live rules. |
| `codes/forecast.py` | Loads the data and makes the forecasts the planner uses: solar (PV model + weather), house use (last 28 days) and price. |
| `codes/price_model.py` | The LightGBM price model: 10th, 50th and 90th percentile forecasts from 5 minutes to 48 hours ahead. |
| `codes/backtest.py` | Runs a strategy over many days and scores it: bill, battery wear (10 c per kWh taken out), backup hours. |
| `codes/run_all.py` | Runs the full backtest for all homes and saves `results/` (about 1 hour): `python codes/run_all.py`. |
| `codes/fetch_data.py` | Downloads the data from the Ivy Bridge API and the real AEMO prices into `data/prices_aemo.csv`. |
| **`data/`** | **The data:** |
| `data/SBX-001_clean.csv`, `SBX-002_clean.csv`, `SBX-003_clean.csv` | The recordings of the three sandbox homes (5-minute solar, house use and battery level). |
| `data/weather_cache.pkl` | Hourly weather (sunlight, temperature, cloud) for the solar forecast. |
| `data/prices_aemo.csv` | Real SA1 5-minute prices for every recorded day. |
| `data/price_forecasts_backtest.npz` | The price model's forecasts for every backtest slot, worked out once so the backtest runs fast. |
| **`models/`** | **The trained models:** |
| `models/pv_prediction_models.pkl` | The PV (solar) model the planner uses. |
| `models/price_models_backtest.pkl` | The price model trained on Oct 2021 – Sep 2024 only, used for the backtest forecasts (no peeking at the test days). |
| `models/price_models_live.pkl` | The price model trained on all data, for live use. |
| **`results/`** | Backtest results per home and strategy (`daily_results.csv` has everything), plus `assistant_evidence.csv`. |

Everything the code and notebooks need is inside this folder.

## API keys

- **Gemini (the assistant):** set `AIVY_GEMINI_API_KEY` in `backend/.env`.
- **Ivy Bridge:** provide its API key through your local environment when running data-fetching tools; never commit it to the repository.

## Main results (bill + battery wear over all recorded days, saved compared with the baseline)

| Home (days) | Save money | Balanced | Save energy |
|---|---|---|---|
| SBX-001 (632) | $804 | $715 | $447 |
| SBX-002 (364) | $857 | $796 | $130 |
| SBX-003 (149) | $224 | $93 | $72 |

- **Price model:** compared with the 7-day average price profile (the benchmark), it is worth about +$265, +$275 and +$71 for Save money on the three homes. It gives the better result on about 2 of every 3 days.
- **Negative prices:** in those hours the three strategies switch the solar off and run the house and battery on grid power, which pays them. The baseline keeps its solar and only curtails what it would export.
- **Re-planning:** every 30 minutes. Every 5 minutes is a little better (about $34 over 91 test days) but takes 6 times as long to run, so it is a next step for live use.

## Limits to know

- **Weather:** the backtest uses recorded weather for the solar forecast. A live system only has a weather forecast, so real savings will probably be a little lower.
- **Prices:** these are wholesale prices passed straight through (import price = feed-in price, no network charges). Retail tariffs would change the numbers.
- **Export limits:** many SA homes may export at most 5 kW (some only 1.5 kW). The simulator does not cap exports, so selling at price spikes may be a little optimistic.
- **Battery wear:** 10 c per kWh is an estimate; a different value changes the "including wear" ranking.
- **Going live:** a live system needs Ivy's battery control API. The sandbox API can only switch the energy mode.
- **Retraining the price model:** this needs the AEMO dispatch and pre-dispatch CSV files (kept in the price data zip); put their folder in `PRICE_DATA` at the top of `codes/price_model.py`. Using the saved models does not need them.
