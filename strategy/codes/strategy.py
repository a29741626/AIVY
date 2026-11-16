"""
strategy.py  -  WHAT we want the battery to do: the baseline and the 3 modes.

  * Baseline (self-consumption): store spare solar, use it for the house later. Ignores prices.
  * 3 modes for the optimiser (optimizer.py works out HOW to do it as cheaply as possible):

      SAVE MONEY   - lowest bill. Buys when cheap/negative, sells when expensive.
      BALANCED     - keeps enough energy for tonight, sells only at big price spikes.
      SAVE ENERGY  - keeps a large safety reserve, the battery is for the house only (never sold).

A mode is just a small dictionary of settings. That makes it easy for the
Assignment 3 AI to turn plain English ("keep 20% in reserve") into settings.
"""

import copy

SLOT_HOURS = 5 / 60


# ---------------------------------------------------------------------------
# 1) Baseline: self-consumption
# ---------------------------------------------------------------------------
def baseline_policy(pv, load):
    """Spare solar -> into the battery.  House needs power -> out of the battery."""
    def policy(t, soc):
        return pv[t] - load[t]
    return policy


# ---------------------------------------------------------------------------
# 2) The 3 modes
# ---------------------------------------------------------------------------
# What each setting means:
#   floor_min_pct      : never plan to go below this SOC (%)
#   night_load_factor  : ALSO keep enough for tonight's predicted house use x this factor
#                        (0 = don't care about tonight, 1 = exactly tonight, 1.5 = tonight + 50% extra)
#   night_load_guess   : "p50" = normal guess of tonight's use, "p75" = cautious (higher) guess
#   sell_battery       : may the battery sell energy to the grid?
#                        "always" | "spikes_only" (only when price >= spike_price) | "never"
#   spike_price        : c/kWh. At or above this the battery sells at full power right away
#                        (checked every 5 minutes, even between plans). None = never.
#   reserve_unlock_price : c/kWh. At or above this price the house may use the safety reserve
#                        (down to the battery's min SOC) instead of buying at an extremely high price.
#                        The reserve is still never SOLD. None = never unlock.
#   wear_cost          : c/kWh. The "wear and tear" cost of using the battery, so the
#                        optimiser doesn't cycle it for tiny gains. 10 c/kWh is a realistic guess:
#                        e.g. a $5,000 battery that delivers ~50,000 kWh in its life = 10 c per kWh.
#                        (We tested 2 c/kWh before: the battery cycled too much for small gains.)
#   below_floor_fine   : c per kWh, per 5 minutes spent below the floor. A "fine" that pushes
#                        the optimiser to refill the battery when it is below the safety level.
MODES = {
    "save_money": {
        "floor_min_pct": 10, "night_load_factor": 0.0, "night_load_guess": "p50",
        "sell_battery": "always", "spike_price": 300, "reserve_unlock_price": None,
        "wear_cost": 10, "below_floor_fine": 5,
    },
    "balanced": {
        "floor_min_pct": 30, "night_load_factor": 1.0, "night_load_guess": "p50",
        "sell_battery": "spikes_only", "spike_price": 300, "reserve_unlock_price": 300,
        "wear_cost": 10, "below_floor_fine": 5,
    },
    "save_energy": {
        "floor_min_pct": 50, "night_load_factor": 1.5, "night_load_guess": "p75",
        "sell_battery": "never", "spike_price": None, "reserve_unlock_price": 300,
        "wear_cost": 10, "below_floor_fine": 5,
    },
}

# "Tonight" = 6 pm until 7 am the next morning (slot numbers inside a day)
EVENING_START_SLOT = 18 * 12      # 18:00
MORNING_END_SLOT = 7 * 12         # 07:00


def tonight_kwh(load_profile):
    """Predicted house use from 6 pm to 7 am (kWh), from a 288-value load forecast for one day."""
    evening = load_profile[EVENING_START_SLOT:].sum()             # 18:00 -> midnight
    early_morning = load_profile[:MORNING_END_SLOT].sum()          # midnight -> 07:00
    return (evening + early_morning) * SLOT_HOURS / 1000


def floor_pct(mode, load_p50, load_p75, spec):
    """
    The safety level (SOC %) this mode keeps for one day.

    = the bigger of:  floor_min_pct
                      battery min SOC + enough energy for tonight x night_load_factor
    (never higher than 5% below the battery's max, so the battery can still be used)
    """
    profile = load_p75 if mode["night_load_guess"] == "p75" else load_p50
    needed_kwh = tonight_kwh(profile) * mode["night_load_factor"]
    needed_pct = needed_kwh / spec["eff"] / spec["capacity_kwh"] * 100   # /eff: losses coming out
    floor = max(mode["floor_min_pct"], spec["soc_min"] + needed_pct)
    return float(min(floor, spec["soc_max"] - 5))


def with_changes(mode_name, changes):
    """A copy of a mode with some settings changed. Handy for testing and for the Assignment 3 AI.
    Example:  with_changes("balanced", {"floor_min_pct": 20})"""
    mode = copy.deepcopy(MODES[mode_name])
    mode.update(changes)
    return mode
