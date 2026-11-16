"""
battery.py  -  The battery SIMULATOR: our own model of what the battery really does.

The idea in plain words
-----------------------
Every 5 minutes, the strategy says: "I want the battery to charge/discharge at X watts".
The simulator then plays out what REALLY happens:
  * the inverter can't go faster than its power limit
  * the battery can't go above its max SOC or below its min SOC
  * a little energy is lost going in and coming out (efficiency)
  * whatever the house + battery don't use goes to the grid (or comes from it)
  * we pay for what we buy and get paid for what we sell
  * NEGATIVE PRICES: buying earns money and selling costs money, so the inverter changes what it does:
      - the three smart strategies switch the solar OFF: the house and the battery run on grid power,
        which pays us (solar would only replace that paid power)
      - the baseline never buys on purpose, so it only CURTAILS: it turns the solar down so that
        nothing is exported (the house and battery still use the solar)

Signs (same as the Ivy API):
  battery > 0  = charging        battery < 0 = discharging
  grid    > 0  = exporting       grid    < 0 = importing
Energy balance in every slot:   grid = pv - load - battery
"""

import numpy as np
import pandas as pd

SLOT_HOURS = 5 / 60          # one slot = 5 minutes = 1/12 of an hour
SLOTS_PER_DAY = 288          # 24 hours x 12 slots per hour


# ---------------------------------------------------------------------------
# 1) Battery specs for each home
# ---------------------------------------------------------------------------
# capacity_kwh = how much energy the battery STORES (size of the "tank")
# max_power_w  = how FAST it can charge/discharge (width of the "tap"), set by the inverter
# soc_min/max  = the lowest / highest charge level allowed (%)
# eff          = one-way efficiency: 0.95 means 5% lost going in, 5% lost coming out
#
# The API does not tell us the battery size, so we estimated it from the data
# (see estimate_capacity below). SBX-001 is reliable; SBX-002 and SBX-003 are rough.
SPECS = {
    "SBX-001": {"capacity_kwh": 9.6,  "max_power_w": 10000, "soc_min": 10, "soc_max": 100, "eff": 0.95},
    "SBX-002": {"capacity_kwh": 29.0, "max_power_w": 10000, "soc_min": 10, "soc_max": 100, "eff": 0.95},
    "SBX-003": {"capacity_kwh": 34.0, "max_power_w": 5000,  "soc_min": 10, "soc_max": 97,  "eff": 0.95},
}


def estimate_capacity(df):
    """
    Estimate the battery size (kWh) from the data.

    At night there is no solar. If the house is also not buying from the grid,
    then the battery alone is running the house. So:
        battery size = energy the house used overnight / how much the SOC dropped
    We do this for every "clean" night and take the median (the middle value).

    df = one home's clean CSV (columns: presentation_date, pv_w, load_w, grid_w, soc_pct)
    """
    d = df.copy()
    d["slot"] = d.groupby("presentation_date").cumcount()     # 0..287 inside each day
    night = d[d.slot < 72]                                     # midnight to 6 am (72 slots)

    nights = night.groupby("presentation_date").agg(
        load_wh=("load_w", "sum"),                             # house use (sum of watts)
        pv_w=("pv_w", "sum"),                                  # should be ~0 at night
        soc_start=("soc_pct", "first"),
        soc_end=("soc_pct", "last"),
        soc_lowest=("soc_pct", "min"),
        grid_lowest=("grid_w", "min"),                          # most negative = biggest import
    )
    nights["load_kwh"] = nights.load_wh * SLOT_HOURS / 1000    # watts x hours -> kWh
    nights["soc_drop"] = nights.soc_start - nights.soc_end

    # keep only clean nights: no solar, (almost) no grid import, SOC not near empty, a real drop
    clean = nights[(nights.pv_w < 100) & (nights.grid_lowest > -300)
                   & (nights.soc_lowest > 10) & (nights.soc_drop > 5)]
    return (clean.load_kwh / clean.soc_drop * 100).median()


# ---------------------------------------------------------------------------
# 2) Small helper: how much can we discharge before hitting a SOC level?
# ---------------------------------------------------------------------------
def max_discharge_w(soc, floor_pct, spec):
    """The biggest discharge power (W, positive number) for ONE slot that keeps SOC >= floor_pct."""
    energy_above_floor_wh = max(soc - floor_pct, 0) / 100 * spec["capacity_kwh"] * 1000
    # energy coming out of the battery reaches the house x eff, and it all happens in one slot
    return energy_above_floor_wh * spec["eff"] / SLOT_HOURS


# ---------------------------------------------------------------------------
# 3) The simulator
# ---------------------------------------------------------------------------
def simulate(pv, load, price, policy, spec, soc_start, curtail=True, solar_off=False):
    """
    Play out a strategy over many 5-minute slots (one day, or many days in a row).

    pv, load      : arrays of watts, one value per slot
    price         : array of c/kWh, one value per slot (here import price = feed-in price)
    policy(t, soc): the strategy - returns the battery power it WANTS in slot t (W)
    spec          : the battery specs (one row of SPECS)
    soc_start     : SOC (%) at the start
    curtail       : True = never export at a negative price (turn the solar down instead)
    solar_off     : True = at a negative price switch ALL solar off, so the house and the battery
                    take paid grid power instead (the smart strategies; needs curtail=True)

    Returns a table with one row per slot.
    """
    capacity_wh = spec["capacity_kwh"] * 1000
    eff = spec["eff"]
    max_power = spec["max_power_w"]

    # start inside the allowed range
    soc = min(max(soc_start, spec["soc_min"]), spec["soc_max"])

    rows = []
    for t in range(len(pv)):
        # --- a) ask the strategy what it wants ---
        wanted = policy(t, soc)

        # --- b) the inverter can't go faster than its limit ---
        battery = float(np.clip(wanted, -max_power, max_power))

        # --- c) SOC limits + efficiency losses ---
        if battery > 0:
            # charging: only 95% of the energy ends up stored
            room_wh = (spec["soc_max"] - soc) / 100 * capacity_wh
            battery = min(battery, room_wh / (SLOT_HOURS * eff))           # stop when full
            soc = soc + battery * SLOT_HOURS * eff / capacity_wh * 100
        else:
            # discharging: to deliver 1 Wh we must take 1/0.95 Wh out of the battery
            available_wh = (soc - spec["soc_min"]) / 100 * capacity_wh
            battery = max(battery, -available_wh * eff / SLOT_HOURS)        # stop when empty
            soc = soc + battery * SLOT_HOURS / eff / capacity_wh * 100

        soc = min(max(soc, spec["soc_min"]), spec["soc_max"])                # tidy tiny rounding errors

        # --- d) energy balance: whatever is left over goes to (or comes from) the grid ---
        grid = pv[t] - load[t] - battery

        # --- e) negative price: solar is worth less than nothing ---
        curtailed = 0.0
        if curtail and price[t] < 0:
            if solar_off:
                curtailed = pv[t]                 # all solar off: house + battery run on paid grid power
            elif grid > 0:
                curtailed = min(grid, pv[t])      # only turn down what would be exported
            grid = grid - curtailed
        pv_used = pv[t] - curtailed

        # --- f) money (in cents): pay for imports, get paid for exports ---
        import_kwh = max(-grid, 0) * SLOT_HOURS / 1000
        export_kwh = max(grid, 0) * SLOT_HOURS / 1000
        cost_c = import_kwh * price[t] - export_kwh * price[t]

        rows.append((t, pv_used, load[t], wanted, battery, grid, soc, price[t], cost_c, curtailed))

    # pv = the solar actually used, curtailed = the solar switched off (W)
    return pd.DataFrame(rows, columns=["slot", "pv", "load", "wanted", "battery", "grid",
                                       "soc", "price", "cost_c", "curtailed"])


# ---------------------------------------------------------------------------
# 4) Validation: is the simulator obeying physics?
# ---------------------------------------------------------------------------
def check(result, spec):
    """Raise an error if energy does not balance or SOC/power go out of bounds. Returns True if OK."""
    balance_error = (result.pv - result.load - result.battery - result.grid).abs().max()
    assert balance_error < 1e-6, f"energy does not balance (error {balance_error} W)"
    assert result.soc.min() >= spec["soc_min"] - 1e-9, "SOC went below the minimum"
    assert result.soc.max() <= spec["soc_max"] + 1e-9, "SOC went above the maximum"
    assert (result.battery.abs() <= spec["max_power_w"] + 1e-6).all(), "battery power above the limit"
    return True
