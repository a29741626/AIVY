# SA1 Electricity Price Forecasting — Technical Documentation

How the system works, how a prediction is produced, how well it performs, and how to integrate it into a larger model.

Notebook: `SA1_price_prediction.ipynb` · Region: South Australia (`SA1`) · Market: Australian National Electricity Market (NEM)

---

## 1. What it does

Given any time, the system returns:

1. **A predicted wholesale spot price** (RRP, $/MWh) with a calibrated **P10 / P50 / P90** range — from 5 minutes to any distance ahead (accuracy falls with distance).
2. **The actual price** for that time, if it has already happened (AEMO history or the live API).
3. **Decisions** built on the forecast: the cheapest / most expensive windows to import or export (`plan_next`), a part-of-day summary (`day_segments`) and a half-hour solar / grid action plan for a day (`daily_plan`).

It is built from three data sources: 5 years of AEMO 5-minute prices, 5 years of AEMO's own pre-dispatch forecasts, and live feeds of both.

---

## 2. Folder layout

| Path | Purpose |
|---|---|
| `SA1_price_prediction.ipynb` | Everything: data loading, analysis, feature engineering, training, evaluation, live data, prediction and planning functions |
| `data/sa1_dispatch_price.csv` | Historical 5-minute SA1 prices (`SETTLEMENTDATE`, `REGIONID`, `RRP`), Oct 2021 – Sep 2026, 525,888 rows |
| `data/sa1_predispatch.csv` | Every AEMO pre-dispatch run for SA1 (`ISSUED`, `PERIOD_END`, `RRP`), Oct 2021 – Sep 2026, 4.78 M rows |
| `data/sa1_predispatch_live.csv` | Cache of recent pre-dispatch runs fetched live (`ISSUED`, `PERIOD_END`, `RRP`, `FILE`); grows each time the notebook runs |
| `scripts/download_history.py` | Rebuilds / extends the price history from AEMO NEMWeb via `nemosis` |
| `scripts/download_predispatch.py` | Rebuilds / extends the pre-dispatch history from AEMO's monthly MMSDM archive |
| `.env` | `OPENELECTRICITY_API_KEY=...` for live prices (keep private) |
| `requirements.txt` | Pinned package versions (Python 3.13 tested; 3.9+ required) |
| `README.md` | Setup and quick start |

---

## 3. Conventions (read this before integrating)

| Topic | Convention |
|---|---|
| **Time zone** | All internal timestamps are **NEM time = AEST, fixed UTC+10, no daylight saving**, stored as *naive* pandas timestamps. Adelaide local time is 30 min behind NEM time in winter (ACST, UTC+9:30) and 30 min ahead during daylight saving (ACDT, UTC+10:30, early Oct – early Apr). Public functions accept `tz=` for local input/output. |
| **Interval labelling** | A timestamp is the **end** of its 5-minute interval (AEMO's `SETTLEMENTDATE` convention): `18:05` = the interval 18:00–18:05. A user time of exactly 18:00 is mapped to the interval 18:00–18:05 (label 18:05). |
| **Resolution** | Prices and forecasts are 5-minute. AEMO pre-dispatch is half-hourly (`PERIOD_END` = end of the half-hour); each 5-minute target uses the half-hour that contains it. |
| **Units** | Price in **$/MWh** (AUD, wholesale, excl. GST and network charges). **c/kWh = $/MWh ÷ 10.** `daily_plan()` reports c/kWh; everything else $/MWh unless the column says c/kWh. |
| **Price range** | SA1 prices range from the market floor (−$1,000) to the cap (~$17,500–$20,300 in this period). About 25% of intervals are negative. |
| **Market intervention** | History keeps only `INTERVENTION == 0` (the price actually paid). |

---

## 4. Data sources

| Source | What | Access | Used for |
|---|---|---|---|
| AEMO NEMWeb via `nemosis` (`DISPATCHPRICE`) | Official 5-minute regional price | Free, no key | Training targets and price features |
| AEMO MMSDM archive (`PREDISPATCHPRICE`, all runs) | AEMO's half-hourly price forecast, published every 30 min, horizon up to ~40 h | Free, no key | Model input (strongest single predictor) |
| OpenElectricity API v4 (`openelectricity` SDK) | Live 5-minute SA1 price | Free key (Community plan) | Current price, features for live forecasts, actuals after the archive ends |
| NEMWeb `Reports/Current/PredispatchIS_Reports` | Latest pre-dispatch runs (last ~14 days) | Free, no key | AEMO input for live forecasts |

Checks built in:

- **API alignment:** `align_check()` compares a day of API prices with AEMO history. Confirmed exact (100% match, $0.00 difference) with no time shift.
- **Missing archive month:** AEMO's archive has no pre-dispatch file for **Oct 2022**; those rows have no AEMO input and LightGBM treats them as missing.
- **Archive lag:** AEMO publishes each month's archive ~1–2 weeks after month end. The live API (back to 60 days) and NEMWeb "Current" folder (14 days) fill the gap.

---

## 5. Pipeline overview

```
 AEMO price history ─────┐
 AEMO pre-dispatch hist ─┤                          ┌── seasonal model (calendar only, > 48 h ahead)
                         ├─► feature engineering ───┤
 Live prices (API) ──────┤   (per horizon k)        └── 9 horizon models: 5min 30min 1h 3h 6h 12h 24h 36h 48h
 Live pre-dispatch ──────┘                                each = 3 LightGBM quantile models (P10, P50, P90)
                                                                │
                                         conformal band calibration (BAND_ADJ per model)
                                                                │
                                   forecast(targets, as_of)  ◄──┘   picks the model per target by its horizon
                                                                │
                 price_at() · compare() · plan_next() · day_segments() · daily_plan() · show_prediction()
```

---

## 6. Features

Every horizon-*k* model sees the same feature *names*, but each value is taken from data that is **at least *k* old** relative to the target. This is what makes a 6 h model a 6 h model.

### Calendar features (`calendar_features(idx)`) — known for any date

| Feature | Definition |
|---|---|
| `slot` | 5-minute slot of the day, 0–287 (from interval start) |
| `hour` | Hour of day 0–23 |
| `dow` | Day of week, Mon = 0 |
| `is_weekend` | 1 for Sat/Sun |
| `is_holiday` | 1 for SA public holidays (`holidays` package, `subdiv="SA"`) |
| `month` | 1–12 |
| `doy_sin`, `doy_cos` | Day of year encoded on a circle (smooth seasonality) |
| `days_since_start` | Days since 1 Oct 2021 (long-term trend) |

### Price features (`price_features(s, k)`) — all shifted by ≥ k intervals

| Feature | Definition (s = 5-min price series, d = 288 intervals/day) |
|---|---|
| `last` | Price k intervals before the target — the latest known price |
| `mean_1h` | Mean of the 12 intervals ending at `last` |
| `mean_24h`, `std_24h`, `max_24h` | Mean / std / max of the 24 h ending at `last` |
| `prev_week_mean` | Mean of the 7 days ending at `last` |
| `same_slot_yday` | Price at the same time of day on the most recent day that is ≥ k old (1 day back for k ≤ 24 h, 2 days back for 36/48 h) |
| `same_slot_lastwk` | Same time of day, one week back |
| `same_slot_median_7d` | Median of the same time of day over the 7 most recent eligible days |

### AEMO features (`aemo_features(idx, cutoff, pdp)`)

| Feature | Definition |
|---|---|
| `aemo` | AEMO pre-dispatch price for the target's half-hour, from the **latest run published at or before the cut-off** (cut-off = target − k in training; = now in live forecasts). `merge_asof` with a 2 h tolerance, so a stale run is not used. |
| `aemo_lead_h` | Hours between that run's publication and the target |

AEMO coverage by horizon: 98% up to 12 h, 63% at 24 h, 14% at 36 h, 0% at 48 h (runs only reach the end of the next trading day).

---

## 7. Models

| Item | Value |
|---|---|
| Algorithm | LightGBM gradient-boosted trees, **quantile objective** (pinball loss) |
| Quantiles | P10 (α = 0.1), P50 (α = 0.5), P90 (α = 0.9) — three separate models per horizon; predictions are sorted so they never cross |
| Hyperparameters | `n_estimators=500, learning_rate=0.05, num_leaves=63, min_child_samples=200, subsample=0.8 (every iteration), colsample_bytree=0.9` |
| Horizon models | `HORIZONS = {"5min": 1, "30min": 6, "1h": 12, "3h": 36, "6h": 72, "12h": 144, "24h": 288, "36h": 432, "48h": 576}` (value = k in 5-min steps) |
| Inputs | `FEATURES = CAL_FEATURES + H_FEATURES` (20 columns: 9 calendar, 9 price, 2 AEMO) |
| 5-minute horizon | Persistence: the three quantiles are re-centred so P50 = latest price (tested better than any trained model at 5 min) |
| Seasonal model | Same quantile setup on `CAL_FEATURES` only; used beyond 48 h |
| Missing inputs | Rows missing any core price feature (`PRICE_COLS`) are dropped from training; missing AEMO values are left as NaN and handled natively by LightGBM |
| Total models | 9 × 3 horizon + 3 seasonal = **30 LightGBM models** |
| Training data | ~471 k rows per horizon (5-minute intervals, Oct 2021 – Mar 2026 for evaluation; all data to Sep 2026 for the production refit) |
| Training time | ~5 min per full fit on a 16 GB laptop; the notebook fits twice (evaluation + refit), ~15 min end to end |

**Why one model per horizon** (rather than one model with "hours ahead" as an input): each model is trained only on information that would genuinely have existed that far ahead, and learns its own weighting. Close in, the latest price dominates; further out, AEMO's forecast, same-time-yesterday and calendar effects take over (see the feature-importance chart in section 6 of the notebook).

---

## 8. How a prediction is produced

`forecast(targets, as_of=None)` is the core function; every other prediction function calls it.

1. **Known prices.** `_known_series()` = AEMO history + live API prices after the history ends.
2. **Forecast origin (`as_of`).** Default: the latest known price. For back-testing, pass an earlier `as_of`; nothing after it is used.
3. **Choose a model per target.** `gap = target − as_of`; `pick_horizon(gap)` returns the **shortest horizon ≥ gap** (a target 2 h away uses the 3 h model). Gap > 48 h → seasonal model.
4. **Build features.** Prices after `as_of` are removed, the series is put on a continuous 5-minute grid (gaps ≤ 30 min interpolated), then `calendar_features`, `price_features(grid, k)` and `aemo_features` are computed. In live mode the AEMO cut-off is *now* (newest run); in back-test mode it is `as_of`.
5. **Predict.** The horizon's P10/P50/P90 models are applied (`horizon_predict`); 5-min is re-centred on the latest price.
6. **Calibrate.** The band is widened/narrowed by `BAND_ADJ[model]` so ~80% of actual prices fall inside P10–P90.
7. **Fallback.** Any target whose core price features are missing goes to the seasonal model.

`price_at(when, tz=None, as_of=None)` wraps this for a single time and adds the actual price. If `when` is in the past and no `as_of` is given, it returns the forecast that would have been made 24 h earlier.

---

## 9. Evaluation

**Method.** Time-based split: train Oct 2021 – Mar 2026, test Apr – Sep 2026 (52,704 unseen intervals). Every model is tested on the same intervals from its own horizon. Baselines: **persistence** (latest price), **AEMO pre-dispatch alone**, **profile median** (month × weekday × slot), **seasonal model**.

### Error by horizon ($/MWh, test set)

| Horizon | Persistence MAE | **Model MAE** | Model median AE | AEMO coverage | AEMO MAE\* | Model MAE\* |
|---|---|---|---|---|---|---|
| 5 min | 15.1 | **15.1** | 3.2 | 100% | 23.7 | 15.1 |
| 30 min | 24.2 | **21.3** | 7.4 | 100% | 25.1 | 21.3 |
| 1 h | 32.5 | **24.3** | 9.1 | 100% | 26.4 | 24.3 |
| 3 h | 47.6 | **30.3** | 12.8 | 100% | 33.6 | 30.3 |
| 6 h | 60.8 | **32.6** | 13.9 | 100% | 37.4 | 32.6 |
| 12 h | 66.2 | **34.6** | 16.0 | 100% | 40.7 | 34.6 |
| 24 h | 68.6 | **45.6** | 25.0 | 65% | 100.3 | 37.0 |
| 36 h | 86.9 | **58.7** | 37.8 | 15% | 111.0 | 37.0 |
| 48 h | 81.8 | **62.1** | 42.8 | 0% | — | — |
| Seasonal (any) | | 70.0 | 47.3 | | | |
| Profile median | | 82.9 | | | | |

\* only rows where an AEMO forecast exists, so the comparison is like-for-like. The model beats AEMO's own forecast at every horizon; AEMO's raw forecast over-predicts spikes badly beyond ~24 h.

### Decision quality — picking the best 2-hour window each day (183 test days)

"Capture" = share of the best possible gain achieved versus trading at the day's average price.

| Forecast | Buy capture | Buy: $/MWh below day avg | Sell capture | Sell: $/MWh above day avg |
|---|---|---|---|---|
| 1 h-ahead model | 87% | 54.3 | 86% | 117.5 |
| 6 h-ahead model | 73% | 45.6 | 42% | 56.8 |
| 24 h-ahead model | 66% | 41.3 | 20% | 27.1 |
| 48 h-ahead model | 61% | 38.1 | 24% | 32.3 |
| AEMO alone (6 h) | 69% | 43.1 | 40% | 54.8 |
| Seasonal model | 60% | 37.9 | 26% | 35.7 |

Windows are ranked by P50 for buying and by the average of P50 and AEMO's forecast for selling (`BUY_RANK`, `SELL_RANK`, chosen automatically from these tests). **Buy timing is reliable even 2 days out; sell timing is only reliable within a few hours**, because the profit is in price spikes, which nobody forecasts well a day ahead.

### Calibration of the P10–P90 band (target 80%)

Conformalised quantile regression: the widening is learned on the first half of the test period and checked on the second half.

| Model | Widen by ±$ | Coverage before | Coverage after |
|---|---|---|---|
| 1 h | −1.7 | 86.3% | 83.6% |
| 6 h | 0.7 | 81.0% | 81.8% |
| 24 h | 11.6 | 72.2% | 83.6% |
| 48 h | 43.7 | 56.0% | 83.6% |
| Seasonal | 56.6 | 57.8% | 86.1% |

---

## 10. Decision layer

| Function | Logic |
|---|---|
| `plan_next(hours=48, window="2h", n=3, tz=None)` | Forecasts every 5-min interval, averages to half-hours, returns the *n* cheapest and *n* most expensive non-overlapping windows of length `window`, plus negative-price periods. |
| `day_segments(days=2, tz="Australia/Adelaide")` | Splits the forecast into Overnight 0–6, Morning 6–9, Midday 9–16, Evening peak 16–21, Late evening 21–24 (local) and compares each to the 5-year median for that segment and month. |
| `daily_plan(day="today", tz, has_battery, import_below_c, export_above_c)` | Half-hour action plan. **(1)** Useful solar = sunrise + 1 h to sunset − 1 h (NOAA solar-position formula for Adelaide). **(2)** Level = negative / cheap (≤ day's 25th percentile and ≥ 2 c below the day's median) / expensive (≥ 75th percentile and ≥ 2 c above median) / normal — or fixed c/kWh thresholds. **(3)** Action from the level × sun table below. Blocks under 1 h are merged into the previous block (negative periods never are). |

| | Sun up | Sun down |
|---|---|---|
| Negative | Don't export — use / store power | Take from grid — paid to use power |
| Cheap | Use solar / charge battery | Take from grid — charge battery, run big loads |
| Normal | Self-use, export surplus | Normal use |
| Expensive | Export solar | Avoid grid — run on / export battery |

The decision layer only uses wholesale prices. It does not model a household's load, battery capacity or state of charge, inverter limits or retail tariffs — these are the natural things for a larger model to add.

---

## 11. API reference

All functions live in the notebook namespace after it has run (sections 1–9 must have run; sections 10–13 for planners).

### `forecast(targets, as_of=None) -> pd.DataFrame`
- `targets`: `DatetimeIndex` of NEM-time interval ends.
- `as_of`: forecast origin (NEM time). Default: latest known price.
- Returns one row per target: `p10`, `p50`, `p90` ($/MWh, calibrated), `model` (e.g. `"6h-ahead"`, `"seasonal"`), `aemo` (AEMO forecast or NaN), `forecast_as_of`.

### `price_at(when, tz=None, as_of=None) -> dict`
Keys: `interval_end_nem`, `forecast_as_of`, `model`, `predicted_p50`, `predicted_p10`, `predicted_p90`, `aemo_forecast`, `actual`, `actual_source`, `error` (actual − P50).

### `compare(times, tz=None, as_of=None) -> pd.DataFrame`
`price_at()` for a list of times, indexed by `interval_end_nem`.

### `current_price() -> dict`
`interval_end_nem`, `price` ($/MWh), `c_per_kWh`, `source`.

### `plan_next(...) -> dict`
`half_hourly` (DataFrame: `p10`, `p50`, `p90`, `aemo`, $/MWh), `buy`, `sell` (DataFrames: `from`, `to`, `P50 $/MWh`, `P10`, `P90`, `P50 c/kWh`), `negative` (list of (start, end)).

### `day_segments(...) -> pd.DataFrame`
Index (`date`, `segment`); columns `hours`, `forecast $/MWh`, `c/kWh`, `low (P10)`, `high (P90)`, `typical for month`, `negative share`, `note`.

### `daily_plan(...) -> dict`
`half_hourly` (DataFrame in **c/kWh**: `p10`, `p50`, `p90`, `sun`, `level`, `action`; index = local half-hour start), `blocks` (`from`, `to`, `hours`, `what to do`, `avg c/kWh`, `range c/kWh (P10–P90)`), `sunrise`, `sunset`.

### `show_prediction(when, tz="Australia/Adelaide")`
Displays a one-time summary table (used by the section-12 form) and returns the `price_at()` dict.

### Lower-level building blocks
`calendar_features(idx)`, `price_features(s, k)`, `aemo_features(idx, cutoff, pdp)`, `pick_horizon(gap)`, `horizon_predict(name, X)`, `predict_quantiles(models, X)`, `get_live_prices(start, end)`, `recent_prices()`, `update_live_predispatch(since, every)`, `predispatch_all()`, `sun_times(day)`.

Key objects: `horizon_models` (dict name → {"p10","p50","p90": LGBMRegressor}), `seasonal_models`, `BAND_ADJ` (dict model name → $ widening), `BUY_RANK`, `SELL_RANK`, `price` (history Series).

---

## 12. Integrating with a larger model

### Option A — use the forecasts as inputs (recommended)

The P10/P50/P90 outputs are good features for a downstream model (household optimisation, battery dispatch, demand model, etc.).

1. Run the notebook once per session (or after refreshing data) so the models are trained.
2. Call `forecast(targets, as_of)` for the intervals your model needs and join on `interval_end_nem` (NEM time, interval end).
3. Use `p50` as the point forecast, `p90 − p10` as an uncertainty feature, `model` / the horizon as a reliability indicator, and `aemo` if you want AEMO's raw view separately.
4. **Avoid leakage when building training data for the larger model:** generate historical forecasts with the correct `as_of` (e.g. `as_of = decision time`), never with `as_of` after the target. The `rolling_forecast(name, index)` helper in section 10 produces k-ahead forecasts for a whole index in one call.
5. Be aware the production models were refit on all data up to Sep 2026, so forecasts for Apr–Sep 2026 are in-sample. For an honest back-test of the larger model, use dates after the training cut-off or retrain on an earlier window.

### Option B — move the code into a Python module

The notebook is self-contained but not importable. For a service or pipeline:

1. Move sections 1, 2, 4, 5 (fit/predict helpers), 8 and 9 into a module (e.g. `sa1price/`), keeping the function signatures above.
2. **Persist the trained models** instead of retraining each session. Suggested (not yet in the notebook):
   ```python
   import json, pathlib
   out = pathlib.Path("models"); out.mkdir(exist_ok=True)
   for name, qs in {**horizon_models, "seasonal": seasonal_models}.items():
       for q, m in qs.items():
           m.booster_.save_model(out / f"{name}_{q}.txt")
   json.dump({"BAND_ADJ": BAND_ADJ, "BUY_RANK": BUY_RANK, "SELL_RANK": SELL_RANK,
              "HORIZONS": HORIZONS, "FEATURES": FEATURES}, open(out / "meta.json", "w"))
   # load: booster = lightgbm.Booster(model_file="models/6h_p50.txt"); booster.predict(X[FEATURES])
   ```
   Feature columns must be passed in exactly the order of `FEATURES` (`CAL_FEATURES` only for the seasonal model).
3. Schedule refreshes: live data on every call (cached 5 min); monthly archive download + retrain (~15 min).

### Operational notes

| Item | Detail |
|---|---|
| Compute | CPU only; ~16 GB RAM is comfortable (feature frames are kept as float32). |
| Live inputs | OpenElectricity Community key (rate-limited — give each consumer its own key); NEMWeb is public. |
| Freshness | Live prices lag real time by a few minutes; AEMO pre-dispatch runs every 30 min. |
| Failure modes | No API key → live price parts skipped, history-based functions still work. NEMWeb unreachable → cached runs used, AEMO feature may be missing (models still predict). |
| Regions | SA1 only. Other NEM regions (NSW1, QLD1, VIC1, TAS1) need the scripts' `REGIONID` filter changed and a retrain. |

---

## 13. Limitations

- **Spikes** (> $300/MWh, ~2% of intervals) carry most of the money but are only partly predictable beyond a few hours; they appear as a wide P10–P90 band.
- **Test period** is Apr–Sep 2026 (autumn/winter). Summer behaviour is in the training data but not in the reported test results; a rolling multi-season back-test would strengthen the evaluation.
- **Beyond 48 h** the seasonal model gives "what this time usually costs", not a market forecast. Tree models don't extrapolate trends.
- **Wholesale only.** Prices reach a household bill only on pass-through retailers (e.g. Amber) or VPPs; network charges and GST are extra.
- **Training data ends Sep 2026** in this version. Re-run both download scripts monthly and retrain.

---

## 14. Glossary

| Term | Meaning |
|---|---|
| NEM | National Electricity Market (eastern & southern Australia) |
| AEMO | Australian Energy Market Operator |
| SA1 | South Australia pricing region |
| RRP | Regional Reference Price — the 5-minute spot price, $/MWh |
| Dispatch interval | 5-minute pricing period |
| Pre-dispatch | AEMO's half-hourly price forecast, rerun every 30 min, up to the end of the next trading day |
| Horizon | Time between the latest known price and the target time |
| P10 / P50 / P90 | 10th / 50th / 90th percentile forecasts: P50 is the central estimate; 80% of outcomes should fall between P10 and P90 |
| MAE | Mean absolute error |
| Persistence | Baseline that assumes the price stays at its latest value |
| Conformal calibration | Adjusting the forecast band using out-of-sample errors so its coverage matches the target |
| Capture | Share of the best possible buy/sell gain a forecast achieves when picking windows |
