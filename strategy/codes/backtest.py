"""
backtest.py  -  The RACE: run a strategy over all the real days and score it.

Rules of the race (so it is fair):
  * every strategy runs on the same days, with the same house, solar and prices
  * every strategy starts with the same SOC, and its SOC carries over from day to day
  * every strategy pays the REAL price
  * the simulator's physics checks run on every result

Two scores for each strategy:
  * COST     : the electricity bill ($). Lower is better; negative = the home earned money.
  * SECURITY : how much energy is kept for the house:
               - backup hours: how many hours the battery could run the house on its own (average)
               - lowest SOC reached
               - energy bought from the grid (kWh)
"""

import numpy as np
import pandas as pd

import battery as bat
import forecast as fc
import strategy as st
from optimizer import MPCController

SLOT_HOURS = 5 / 60
WEAR_C_PER_KWH = 10      # battery wear (c per kWh taken out of the battery), reported for EVERY strategy


def make_timeline(days, price_forecast="mean"):
    """
    Join all days into one long timeline (day after day) for the simulator.
    The end is padded with a copy of the last day, so the last plans can still look 24 h ahead.

    price_forecast : "p50" / "p10" / "p90" = the price model's forecast (median / low / high guess)
                     "mean"               = average of p10, p50 and p90  <- DEFAULT: best bills in our test
                                            (the median ignores the rare high prices, so it makes the
                                             future look too cheap - the same lesson as the 7-day average)
                     "avg7"               = the 7-day benchmark (average of the last 7 days)
    """
    padded = pd.concat([days, days.iloc[[-1]]], ignore_index=True)
    # price model: one long timeline per horizon (5min, 30min, ... 24h), stacked as rows
    price_by_horizon = None
    if price_forecast != "avg7":
        quantiles = ["p10", "p50", "p90"] if price_forecast == "mean" else [price_forecast]
        columns = [f"price_{h}_{q}" for h in fc.PRICE_HORIZONS for q in quantiles]
        if all(c in padded for c in columns):
            # "mean" = the average of the low, middle and high guess (a rough "expected" price)
            price_by_horizon = np.vstack([
                np.mean([fc.timeline(padded, f"price_{h}_{q}") for q in quantiles], axis=0)
                for h in fc.PRICE_HORIZONS])
        else:
            print("price model forecasts not found -> using the 7-day average")
    return {
        # what really happened
        "pv": fc.timeline(padded, "pv"),
        "load": fc.timeline(padded, "load"),
        "price": fc.timeline(padded, "price"),
        # forecasts
        "pv_fc": fc.timeline(padded, "pv_fc"),
        "load_fc_days": list(padded.load_fc),       # one 288-value profile per day
        "load_p75_days": list(padded.load_p75),
        "price_fc_days": list(padded.price_fc),
        "price_by_horizon": price_by_horizon,       # None = use the 7-day average
        "n_slots": len(days) * 288,                 # the real slots (without the padding)
    }


def run(station, strategy="baseline", replan_minutes=30, perfect=False, days=None, mode=None,
        price_forecast="mean", curtail=True):
    """
    Run one strategy for one home.

    strategy : "baseline", "save_money", "balanced" or "save_energy"
    mode     : optional changed settings (e.g. from strategy.with_changes) instead of the standard mode
    days     : optional subset of days (default: all days of the home)
    price_forecast : "mean" (price model, default) or "avg7" (7-day benchmark), see make_timeline
    curtail  : True = at a negative price never export solar (all strategies); the smart strategies
               also switch the solar off, so the house and battery take paid grid power (see battery.py)
    Returns (daily results table, slot-by-slot simulator table).
    """
    days = fc.prepare(station) if days is None else days.reset_index(drop=True)
    spec = bat.SPECS[station]
    data = make_timeline(days, price_forecast)
    n = data["n_slots"]
    pv, load, price = data["pv"][:n], data["load"][:n], data["price"][:n]

    # pick the strategy (the baseline never buys on purpose, so it does not switch the solar off)
    solar_off = curtail and strategy != "baseline"
    if strategy == "baseline":
        policy = st.baseline_policy(pv, load)
    else:
        policy = MPCController(data, mode or st.MODES[strategy], spec, replan_minutes, perfect, solar_off)

    # run the simulator over the whole timeline, starting from the real SOC on day 1
    sim = bat.simulate(pv, load, price, policy, spec, soc_start=days.soc0.iloc[0], curtail=curtail,
                       solar_off=solar_off)
    bat.check(sim, spec)                                   # physics must hold

    sim["day"] = sim.slot // 288
    sim["action"] = policy.log if strategy != "baseline" else "self_consumption"
    return daily_scores(sim, days, station, strategy, spec), sim


def daily_scores(sim, days, station, strategy, spec):
    """Turn the slot-by-slot result into one row per day."""
    avg_load_w = sim.load.mean()                           # the home's average use (W)
    usable_wh = (sim.soc - spec["soc_min"]).clip(lower=0) / 100 * spec["capacity_kwh"] * 1000

    sim = sim.assign(
        import_kwh=(-sim.grid).clip(lower=0) * SLOT_HOURS / 1000,
        export_kwh=sim.grid.clip(lower=0) * SLOT_HOURS / 1000,
        backup_hours=usable_wh * spec["eff"] / avg_load_w,
        # energy put into the battery from the GRID (more than the spare solar)
        grid_charge_kwh=(sim.battery - (sim.pv - sim.load).clip(lower=0)).clip(lower=0) * SLOT_HOURS / 1000,
        # energy SOLD from the battery (more than the house needed)
        battery_sold_kwh=(-sim.battery - (sim.load - sim.pv).clip(lower=0)).clip(lower=0) * SLOT_HOURS / 1000,
        # energy delivered by the battery (this is what wears it out)
        discharge_kwh=(-sim.battery).clip(lower=0) * SLOT_HOURS / 1000,
        curtailed_kwh=sim.curtailed * SLOT_HOURS / 1000,
        negative_price_slot=sim.action == "charge_negative_price",
        spike_slot=sim.action == "sell_spike",
    )
    g = sim.groupby("day")
    out = pd.DataFrame({
        "station": station,
        "strategy": strategy,
        "real_date": days.real_date.values,
        "month": days.month.values,
        "season": days.season.values,
        "cost_c": g.cost_c.sum().values,
        "wear_c": g.discharge_kwh.sum().values * WEAR_C_PER_KWH,
        "discharge_kwh": g.discharge_kwh.sum().values,
        "curtailed_kwh": g.curtailed_kwh.sum().values,
        "import_kwh": g.import_kwh.sum().values,
        "export_kwh": g.export_kwh.sum().values,
        "grid_charge_kwh": g.grid_charge_kwh.sum().values,
        "battery_sold_kwh": g.battery_sold_kwh.sum().values,
        "lowest_soc": g.soc.min().values,
        "backup_hours": g.backup_hours.mean().values,
        "negative_price_min": g.negative_price_slot.sum().values * 5,
        "spike_sell_min": g.spike_slot.sum().values * 5,
        "max_price": g.price.max().values,
    })
    out["cost_with_wear_c"] = out.cost_c + out.wear_c       # the bill + what the battery wear really costs
    return out


def add_savings(results):
    """
    Add a column saved_c = baseline bill - strategy bill, for the same home and the same day.
    (We match days by their position: day 0, day 1, ... because a few real dates appear twice.)
    """
    r = results.copy()
    r["day_number"] = r.groupby(["station", "strategy"]).cumcount()
    base = r[r.strategy == "baseline"].set_index(["station", "day_number"]).cost_c
    r = r.join(base.rename("baseline_cost_c"), on=["station", "day_number"])
    r["saved_c"] = r.baseline_cost_c - r.cost_c
    base_w = r[r.strategy == "baseline"].set_index(["station", "day_number"]).cost_with_wear_c
    r = r.join(base_w.rename("baseline_with_wear_c"), on=["station", "day_number"])
    r["saved_with_wear_c"] = r.baseline_with_wear_c - r.cost_with_wear_c
    return r


def summary(results):
    """
    One row per home x strategy:
      cost $, saved vs baseline $, energy bought per day, backup hours, lowest SOC,
      and how often the strategy beat the baseline on a single day.
    """
    r = add_savings(results)
    g = r.groupby(["station", "strategy"], sort=False)
    table = pd.DataFrame({
        "days": g.size(),
        "bill $": g.cost_c.sum() / 100,
        "saved vs baseline $": g.saved_c.sum() / 100,
        "wear $": g.wear_c.sum() / 100,
        "bill + wear $": g.cost_with_wear_c.sum() / 100,
        "saved incl. wear $": g.saved_with_wear_c.sum() / 100,
        "curtailed kWh": g.curtailed_kwh.sum(),
        "days better than baseline %": g.saved_c.apply(lambda x: (x > 0.5).mean() * 100),
        "bought from grid kWh/day": g.import_kwh.mean(),
        "avg backup hours": g.backup_hours.mean(),
        "lowest SOC %": g.lowest_soc.min(),
    })
    return table.round(1)
