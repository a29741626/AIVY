"""
forecast.py  -  Load the data, and make the PREDICTIONS the optimiser needs.

The optimiser plans 24 hours ahead, so it needs to guess three things:
  1. PV (solar)  -> our PV model (pv_prediction_models.pkl) + the weather
  2. Load (house use) -> "what did the house use at this time of day, over the last 28 days?"
  3. Price -> our LightGBM price model (price_model.py), one model per "how far ahead".
     Benchmark kept for comparison: "the average price at this time of day, last 7 days".
     (the price RIGHT NOW is known exactly - it is published live every 5 minutes)

Important rule: a forecast may only use the PAST. No peeking at the future.
"""

import os
import joblib
import numpy as np
import pandas as pd

# folders: this file lives in strategy/codes; the data is in strategy/data, the trained models in strategy/models
HERE = os.path.dirname(os.path.abspath(__file__))                  # the codes folder
ROOT = os.path.dirname(HERE)                                        # the strategy folder
DATA = os.path.join(ROOT, "data")                                  # home recordings, weather, prices
MODELS = os.path.join(ROOT, "models")                              # the trained PV and price models
SLOTS_PER_DAY = 288


# ---------------------------------------------------------------------------
# 1) The house data (one row per day)
# ---------------------------------------------------------------------------
def load_days(station):
    """
    Read one home's clean CSV and return one row per COMPLETE day:
      presentation_date, real_date, pv (288 values), load (288 values), soc0 (SOC at midnight)
    Days with missing slots or lots of missing readings are skipped.
    """
    csv = os.path.join(DATA, station + "_clean.csv")
    d = pd.read_csv(csv, usecols=["presentation_date", "replayed_from", "pv_w", "load_w", "soc_pct"])

    days = []
    for date, g in d.groupby("presentation_date"):
        too_short = len(g) != SLOTS_PER_DAY
        too_many_gaps = g[["pv_w", "load_w"]].isna().sum().sum() > 12
        if too_short or too_many_gaps:
            continue
        g = g.ffill().bfill()                                  # fill the few missing readings
        days.append({
            "presentation_date": date,
            "real_date": g.replayed_from.iloc[0],              # the REAL recording date
            "pv": g.pv_w.clip(lower=0).values,
            "load": g.load_w.clip(lower=0).values,
            "soc0": g.soc_pct.iloc[0],
        })
    return pd.DataFrame(days)


# ---------------------------------------------------------------------------
# 2) Prices (real AEMO SA1 prices, matched on the REAL date)
# ---------------------------------------------------------------------------
def load_prices(path=os.path.join(DATA, "prices_aemo.csv")):
    """Return a dictionary:  real_date -> array of 288 prices (c/kWh)."""
    p = pd.read_csv(path)
    table = p.pivot_table(index="real_date", columns="slot", values="price_c_kwh")
    # daylight-saving days have a missing hour: fill it with the neighbouring price
    table = table.reindex(columns=range(SLOTS_PER_DAY)).ffill(axis=1).bfill(axis=1)
    return {date: row.values for date, row in table.iterrows()}


# ---------------------------------------------------------------------------
# 3) PV forecast = our PV model + weather
# ---------------------------------------------------------------------------
def load_weather():
    """Hourly Adelaide weather -> every 5 minutes (same steps as the PV notebook)."""
    wx = pd.read_pickle(os.path.join(DATA, "weather_cache.pkl"))
    sun = wx[["ghi_w_m2"]].shift(-30, freq="min")              # GHI value = average of the previous hour
    w = wx[["temp_c", "cloud_pct"]].resample("5min").interpolate()
    w = w.join(sun.resample("5min").interpolate(), how="outer").interpolate()
    return w


class PVForecaster:
    """Our saved PV model:  predict(station, real_date) -> 288 predicted PV values (W)."""

    def __init__(self):
        saved = joblib.load(os.path.join(MODELS, "pv_prediction_models.pkl"))
        self.models = saved["models"]            # one model per home
        self.features = saved["features"]        # ghi_w_m2, temp_c, cloud_pct, hour, day_of_year
        self.weather = load_weather()

    def predict(self, station, real_date):
        times = pd.Timestamp(real_date) + pd.to_timedelta(np.arange(SLOTS_PER_DAY) * 5, unit="min")
        day = self.weather.reindex(times).interpolate().ffill().bfill()
        if day.isna().any().any():
            return None                          # no weather for this date
        day["hour"] = times.hour + times.minute / 60
        day["day_of_year"] = times.dayofyear
        prediction = self.models[station].predict(day[self.features])
        return np.clip(prediction, 0, None)      # PV can't be negative


# ---------------------------------------------------------------------------
# 4) Load forecast = what the house usually uses at this time of day
# ---------------------------------------------------------------------------
def load_forecasts(days):
    """
    For each day: look at the previous 28 days of the SAME type (weekday or weekend)
    and take, for each 5-minute slot:
      - the median (p50)  = a "normal" guess
      - the 75th percentile (p75) = a "cautious" guess (the house uses this much or less 75% of the time)
    """
    all_loads = np.vstack(days.load.values)                     # one row per day, 288 columns
    dates = pd.to_datetime(days.real_date)
    is_weekend = (dates.dt.dayofweek >= 5).values

    p50, p75 = [], []
    for i in range(len(days)):
        in_last_28_days = ((dates < dates.iloc[i]) & (dates >= dates.iloc[i] - pd.Timedelta(days=28))).values
        same_type = is_weekend == is_weekend[i]
        past = all_loads[in_last_28_days & same_type]
        if len(past) < 3:                                       # first few days: not enough history yet
            past = all_loads[:max(i, 1)]
        p50.append(np.percentile(past, 50, axis=0))
        p75.append(np.percentile(past, 75, axis=0))
    return p50, p75


# ---------------------------------------------------------------------------
# 5) Price forecast = what the price usually was at this time of day last week
# ---------------------------------------------------------------------------
SPIKE_CAP = 300     # c/kWh: in the forecast, treat any price above this as 300


def price_forecasts(days, prices):
    """
    For each day: the AVERAGE price for each time of day over the previous 7 days.

    Why the average and not the median? SA prices are "spiky": most of the time they are low,
    but sometimes very high. The median ignores the high ones, so it makes the future look
    cheaper than it really is, and the optimiser then sells energy it should have kept.
    (Tested on 90 days of SBX-001: the average saved ~$57 more than the median.)
    One rare $20/kWh spike would make the average silly, so we cap prices at SPIKE_CAP first.
    """
    result = []
    for date in pd.to_datetime(days.real_date):
        last_7_days = (date - pd.to_timedelta(np.arange(1, 8), unit="D")).strftime("%Y-%m-%d")
        past = [prices[d] for d in last_7_days if d in prices]
        if not past:                                    # the very first day: no history yet
            past = [prices[date.strftime("%Y-%m-%d")]]
        result.append(np.clip(past, None, SPIKE_CAP).mean(axis=0))
    return result


# ---------------------------------------------------------------------------
# 5b) Price forecast from the LightGBM price model (pre-computed by price_model.py)
# ---------------------------------------------------------------------------
# How far ahead each model looks (in 5-min slots). "5min" = 1 slot ahead, "24h" = 288 slots ahead.
PRICE_HORIZONS = {"5min": 1, "30min": 6, "1h": 12, "3h": 36, "6h": 72, "12h": 144, "24h": 288}


def load_model_price_forecasts(path=os.path.join(DATA, "price_forecasts_backtest.npz")):
    """
    Read the pre-computed forecasts: {"1h|p50": {real_date: 288 values (c/kWh)}, ...}.
    The value for a slot from the "1h" model is what the model said 1 hour BEFORE that slot,
    using only information from that time (trained only on data before Oct 2024 = no peeking).
    """
    if not os.path.exists(path):
        return None
    table = {}
    with np.load(path) as f:
        for key in f.files:
            horizon, quantile, date = key.split("|")
            table.setdefault(f"{horizon}|{quantile}", {})[date] = f[key]
    return table


# ---------------------------------------------------------------------------
# 6) Put it all together for one home
# ---------------------------------------------------------------------------
_cache = {}


def prepare(station):
    """
    Everything the backtest needs for one home, one row per day:
      pv, load, price          = what REALLY happened (used to pay the bill)
      pv_fc, load_fc, load_p75, price_fc = the forecasts (what the optimiser is allowed to know)
      price_<horizon>_<quantile> = the price model's forecasts (if price_forecasts_backtest.npz exists)
    The result is cached, so the second call is instant.
    """
    if station in _cache:
        return _cache[station]

    prices = load_prices()
    days = load_days(station)
    days = days[days.real_date.isin(prices.keys())].reset_index(drop=True)   # need a price for each day

    days["price"] = [prices[d] for d in days.real_date]
    days["price_fc"] = price_forecasts(days, prices)              # the 7-day benchmark (average of the last 7 days)

    # new method: the price model, one column per horizon and quantile (e.g. "price_1h_p50")
    model = load_model_price_forecasts()
    if model is not None:
        for key, by_date in model.items():
            horizon, quantile = key.split("|")
            days[f"price_{horizon}_{quantile}"] = [by_date.get(d, bench) for d, bench in zip(days.real_date, days.price_fc)]
    days["load_fc"], days["load_p75"] = load_forecasts(days)

    pv_model = PVForecaster()
    predicted = [pv_model.predict(station, d) for d in days.real_date]
    # if there is no weather for a day (rare), fall back to the actual PV
    days["pv_fc"] = [p if p is not None else actual for p, actual in zip(predicted, days.pv)]

    days["month"] = pd.to_datetime(days.real_date).dt.month
    days["season"] = np.where(days.month.isin([5, 6, 7, 8, 9]), "winter", "summer")
    _cache[station] = days
    return days


def cached(station):
    """The already-prepared frame for one home, or None - it never builds it."""
    return _cache.get(station)


def timeline(days, column):
    """Join one column of all days into ONE long array (day after day), for the simulator."""
    return np.concatenate(days[column].values)
