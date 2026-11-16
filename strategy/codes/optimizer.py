"""
optimizer.py  -  THE STRATEGY ALGORITHM: plan the cheapest 24 hours, re-plan every 30 minutes.

The idea in plain words (this method is called "Model Predictive Control", MPC)
------------------------------------------------------------------------------
Every 30 minutes the controller:
  1. LOOKS    at the battery level (SOC) and the live price right now
  2. PREDICTS the next 24 hours: solar, house use and prices
  3. PLANS    the cheapest way to charge/discharge over those 24 hours,
              while obeying the battery's physics and the chosen mode's safety rules
  4. ACTS     on the plan, 5 minutes at a time
  5. REPEATS  30 minutes later with fresh information
It works like a GPS: it plans the whole route, but re-plans as things change.

Every 5 minutes, before following the plan, three live safety rules are checked (in this order):
  Rule 1: price <= 0             -> charge at full power (we are PAID to take power)
  Rule 2: price >= spike price   -> sell at full power, down to the safety level (if the mode may sell)
  Rule 3: price >= unlock price  -> the house may use the safety reserve instead of buying
                                    (the reserve is never sold)
  Otherwise: follow the plan, but never discharge below the safety level.
And at a negative price the inverter switches the solar off (see battery.py), so the house and the
battery take paid grid power. The planner knows this: it counts no solar in slots it expects to be negative.

How the plan is found (step 3)
------------------------------
We use "linear programming" (scipy's linprog). We describe the problem, and the solver finds
the best answer. For every 5-minute slot k in the next 24 hours, the solver chooses:
    charge[k], discharge[k]   battery power (W)
    buy[k],    sell[k]        grid power (W)
    soc[k]                    stored energy at the end of the slot (Wh)
    shortfall[k]              how far below the safety level the battery is (Wh), normally 0
It must obey these rules:
    energy balance : pv - load = charge - discharge + sell - buy           (every slot)
    battery        : soc[k] = soc[k-1] + 0.95 x charge - discharge / 0.95  (energy in Wh)
    limits         : 0 <= charge, discharge <= inverter limit;  soc_min <= soc <= soc_max
    safety level   : soc[k] + shortfall[k] >= safety level   (each Wh of shortfall is "fined")
    mode rule      : if the battery may not sell, discharge <= what the house is missing
And it makes this total as SMALL as possible (in cents):
      price x energy bought  -  price x energy sold
    + wear cost x energy discharged
    + fine x energy below the safety level
"""

import numpy as np
from scipy.optimize import linprog
from scipy import sparse

import battery as bat
import strategy as st

SLOT_HOURS = 5 / 60       # one slot = 5 minutes = 1/12 of an hour
HORIZON = 288             # plan 24 hours ahead = 288 slots of 5 minutes

# Price model: which horizon model to use for a slot j steps ahead (shortest model that looks >= j ahead).
# Models look 1, 6, 12, 36, 72, 144, 288 steps ahead -> WHICH_MODEL[j] = row 0..6 (row 0 for j = 0 is unused).
MODEL_STEPS = np.array([1, 6, 12, 36, 72, 144, 288])
WHICH_MODEL = np.searchsorted(MODEL_STEPS, np.arange(HORIZON))


# ===========================================================================
# PART 1: make ONE plan (the cheapest 24-hour schedule)
# ===========================================================================
def make_plan(pv, load, price, soc_now, floor, mode, spec):
    """
    Find the cheapest battery schedule for the next len(pv) slots.

    pv, load, price : forecasts, one value per slot (price[0] = the live price right now)
    soc_now         : battery level now (%)
    floor           : the safety level (SOC %) that this mode keeps
    mode            : the mode's settings (from strategy.MODES)
    spec            : the battery specs (from battery.SPECS)
    Returns: planned battery power (W), planned grid power (W), planned SOC (%), planned cost (cents).
    """
    n_slots = len(pv)
    capacity_wh = spec["capacity_kwh"] * 1000
    eff = spec["eff"]
    max_power = spec["max_power_w"]
    watts_to_kwh = SLOT_HOURS / 1000          # power held for one slot (W) -> energy (kWh)

    # ---- Step A: number the unknowns ----
    # The solver works with ONE long list of unknowns, made of 6 blocks of n_slots values:
    #   [ charge | discharge | buy | sell | soc | shortfall ]
    # These arrays tell us where each block sits in that list.
    charge, discharge, buy, sell, soc, shortfall = (np.arange(n_slots) + block * n_slots
                                                    for block in range(6))
    n_unknowns = 6 * n_slots

    # ---- Step B: the cost to make as small as possible (cents) ----
    cost = np.zeros(n_unknowns)
    cost[buy] = price * watts_to_kwh                       # buying costs money
    cost[sell] = -price * watts_to_kwh                     # selling earns money
    cost[discharge] = mode["wear_cost"] * watts_to_kwh     # small wear-and-tear cost for using the battery
    cost[shortfall] = mode["below_floor_fine"] / 1000      # fine per Wh below the safety level, per slot

    # ---- Step C: rules that must hold EXACTLY (equalities) ----
    # Each rule is one row:  sum of (number x unknown) = right-hand side
    rows, cols, values = [], [], []

    def add(row, unknown, number):
        """Put 'number x unknown' into rule number 'row'."""
        rows.append(row)
        cols.append(unknown)
        values.append(number)

    right_side = np.zeros(2 * n_slots)
    for k in range(n_slots):
        # Rule "battery" (rows 0 .. n_slots-1):
        #   soc[k] - soc[k-1] - 0.95 x charge[k] x hours + discharge[k] x hours / 0.95 = 0
        add(k, soc[k], 1.0)
        if k > 0:
            add(k, soc[k - 1], -1.0)
        add(k, charge[k], -eff * SLOT_HOURS)
        add(k, discharge[k], SLOT_HOURS / eff)

        # Rule "energy balance" (rows n_slots .. 2*n_slots-1):
        #   charge[k] - discharge[k] - buy[k] + sell[k] = pv[k] - load[k]
        add(n_slots + k, charge[k], 1.0)
        add(n_slots + k, discharge[k], -1.0)
        add(n_slots + k, buy[k], -1.0)
        add(n_slots + k, sell[k], 1.0)
        right_side[n_slots + k] = pv[k] - load[k]

    right_side[0] = soc_now / 100 * capacity_wh          # the battery starts where it is now
    equal_rules = sparse.csr_matrix((values, (rows, cols)), shape=(2 * n_slots, n_unknowns))

    # ---- Step D: "at least" rules (inequalities): soc[k] + shortfall[k] >= safety level ----
    # linprog only accepts "<=", so we multiply both sides by -1:  -soc - shortfall <= -safety level
    at_least_rules = sparse.hstack([sparse.csr_matrix((n_slots, 4 * n_slots)),
                                    -sparse.eye(n_slots), -sparse.eye(n_slots)]).tocsr()
    at_least_limits = np.full(n_slots, -floor / 100 * capacity_wh)

    # ---- Step E: the lowest and highest value each unknown may take ----
    discharge_max = np.full(n_slots, float(max_power))
    if mode["sell_battery"] != "always":
        # the battery may only cover what the house is missing (no selling) ...
        house_missing = np.clip(load - pv, 0, max_power)
        if mode["sell_battery"] == "spikes_only":
            # ... except in slots where the price is a spike
            discharge_max = np.where(price >= mode["spike_price"], max_power, house_missing)
        else:
            discharge_max = house_missing

    soc_lowest = spec["soc_min"] / 100 * capacity_wh
    soc_highest = spec["soc_max"] / 100 * capacity_wh
    limits = ([(0, max_power)] * n_slots                       # charge
              + [(0, x) for x in discharge_max]                # discharge
              + [(0, None)] * n_slots                          # buy (no upper limit)
              + [(0, None)] * n_slots                          # sell (no upper limit)
              + [(soc_lowest, soc_highest)] * n_slots          # soc
              + [(0, None)] * n_slots)                         # shortfall

    # ---- Step F: ask the solver for the cheapest answer ----
    result = linprog(cost, A_ub=at_least_rules, b_ub=at_least_limits,
                     A_eq=equal_rules, b_eq=right_side, bounds=limits, method="highs")
    if result.status != 0:
        raise RuntimeError("the optimiser could not find a plan: " + result.message)

    # ---- Step G: read the answer back ----
    x = result.x
    return {
        "battery": x[charge] - x[discharge],              # + charge / - discharge (W)
        "grid": x[sell] - x[buy],                         # + export / - import (W)
        "soc": x[soc] / capacity_wh * 100,                # battery level (%)
        "planned_cost_c": float(price @ (x[buy] - x[sell]) * watts_to_kwh),
    }


# ===========================================================================
# PART 2: the controller (re-plans every 30 minutes, decides every 5 minutes)
# ===========================================================================
class MPCController:
    """
    The controller that runs the battery. The simulator calls it every 5 minutes:
        controller(t, soc)  ->  the battery power it wants (W, + charge / - discharge)

    data           : the timeline (all days joined together), from backtest.make_timeline()
    mode           : the mode's settings (one of strategy.MODES, or a changed copy)
    spec           : the battery specs
    replan_minutes : how often to make a new plan (30 by default)
    perfect        : True = use what really happened instead of forecasts (used only to
                     measure how much better forecasts could help)
    solar_off      : True = the inverter switches the solar off at negative prices (see battery.py),
                     so the planner counts no solar in slots it expects to be negative
    """

    def __init__(self, data, mode, spec, replan_minutes=30, perfect=False, solar_off=True):
        self.data = data
        self.mode = mode
        self.spec = spec
        self.slots_between_plans = max(1, replan_minutes // 5)    # e.g. 30 min = 6 slots
        self.perfect = perfect
        self.plan = None             # the current plan
        self.plan_start = 0          # the slot when the current plan was made
        self.solar_off = solar_off
        self.log = []                # what the controller did in each slot (used for the explanations)

        # The safety level for each day. It changes daily with tonight's predicted house use.
        self.floors = [st.floor_pct(mode, p50, p75, spec)
                       for p50, p75 in zip(data["load_fc_days"], data["load_p75_days"])]

    # ----- what the controller is allowed to know at slot t -----
    def forecast(self, t):
        """The forecasts for the next 24 hours, as they would be known at slot t."""
        d = self.data
        next_24h = np.arange(t, t + HORIZON)

        if self.perfect:
            # "perfect forecast" test only: use what really happened
            return d["pv"][next_24h], d["load"][next_24h], d["price"][next_24h]

        today = t // 288
        time_of_day = next_24h % 288                               # slot number inside a day (0..287)
        pv = d["pv_fc"][next_24h]                                  # our PV model + weather
        load = d["load_fc_days"][today][time_of_day]               # usual house use at each time of day

        if d["price_by_horizon"] is None:
            price = d["price_fc_days"][today][time_of_day].copy()  # 7-day benchmark: usual price at each time of day
        else:
            # price model: for a slot j steps ahead, use the model that looks j steps (or a bit more) ahead
            #   1 step -> "5min" model, 2-6 steps -> "30min", 7-12 -> "1h", ... 145-288 -> "24h"
            price = d["price_by_horizon"][WHICH_MODEL, next_24h]
        price[0] = d["price"][t]                                   # the price NOW is known exactly
        return pv, load, price

    # ----- called by the simulator every 5 minutes -----
    def __call__(self, t, soc):
        d, mode, spec = self.data, self.mode, self.spec
        floor = self.floors[t // 288]                         # today's safety level (%)
        price_now = d["price"][t]                             # the live price
        pv_now, load_now = d["pv"][t], d["load"][t]           # measured live by the inverter

        # STEP 1: is it time for a new plan? (every 30 minutes)
        if self.plan is None or (t - self.plan_start) >= self.slots_between_plans:
            pv, load, price = self.forecast(t)
            if self.solar_off:
                pv = np.where(price < 0, 0.0, pv)              # solar is switched off at negative prices
            self.plan = make_plan(pv, load, price, soc, floor, mode, spec)
            self.plan_start = t

        # STEP 2 (live rule 1): price is zero or negative -> charge at full power
        if price_now <= 0:
            self.log.append("charge_negative_price")
            return spec["max_power_w"]

        # STEP 3 (live rule 2): price spike -> sell down to the safety level (only if this mode may sell)
        spike_price = mode["spike_price"]
        if spike_price is not None and price_now >= spike_price and soc > floor:
            self.log.append("sell_spike")
            return -bat.max_discharge_w(soc, floor, spec)

        # STEP 4 (live rule 3): grid power is extremely expensive -> the house may use the reserve
        #                       (down to the battery's minimum). The reserve is never sold.
        unlock_price = mode.get("reserve_unlock_price")
        if unlock_price is not None and price_now >= unlock_price and load_now > pv_now:
            self.log.append("use_reserve_expensive")
            house_missing = load_now - pv_now
            return -min(house_missing, bat.max_discharge_w(soc, spec["soc_min"], spec))

        # STEP 5: otherwise, follow the plan
        wanted = self.plan["battery"][t - self.plan_start]

        if wanted < 0:
            # never discharge below the safety level
            wanted = max(wanted, -bat.max_discharge_w(soc, floor, spec))
            # modes that don't sell: the battery only covers what the house is missing right now
            if mode["sell_battery"] != "always":
                wanted = max(wanted, min(pv_now - load_now, 0))

        self.log.append("follow_plan")
        return wanted
