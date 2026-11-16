"""P1 strategy engine: wraps strategy/codes for the battery, savings, MPC and sandbox API.

Everything here is a SIMULATION over a recorded day from strategy/data. No code on
this path can talk to an inverter, and every sandbox answer says so
(``device_control_attempted: false``).

Days behave exactly like P0: today replays ``data_adapter.replay_day()``
(config.REPLAY_DATE) truncated to completed slots; past dates use the recorded or
mapped archive day; future dates are 400 INVALID_DATE.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date as Date, datetime, timedelta
from functools import lru_cache
from typing import Any, Optional

import numpy as np

import config  # noqa: E402  (puts strategy/codes on sys.path before battery/strategy)
import battery
import backtest
import forecast
import strategy
from services import data_adapter, envelope, replay_clock, state_store
from services.errors import ApiError

log = logging.getLogger("uvicorn.error")

METHOD = "self_consumption_v1"
METHOD_VERSION = "1.0"
MODEL_VERSION = "mpc-linprog-v1"
SIMULATION_MESSAGE = "Strategy saved to the AIVY simulator. No physical device command was sent."
SIM_STRATEGY = {
    "minimise_cost": "save_money",
    "maximise_export_revenue": "save_money",
    "maximise_self_consumption": "save_energy",
}
VERB = {
    "charge": "charges now",
    "export": "sells from the battery",
    "discharge": "covers the house from the battery",
    "hold": "waits for a better price",
}


# ---------------------------------------------------------------------------
# Running the strategy stack over one recorded day
# ---------------------------------------------------------------------------
def forecast_day(station_id: str, day: str) -> tuple[Any, list[dict]]:
    """One day of forecast.prepare(), plus a warning when the asked day is unusable."""
    days = forecast.prepare(station_id)
    subset = days[days.presentation_date.astype(str) == str(day)].reset_index(drop=True)
    if len(subset):
        return subset, []
    # ponytail: AIVY_REPLAY_DATE (or a mapped archive day) may name a day prepare()
    # skips (gaps / no AEMO price). Use the nearest day it does have instead of failing.
    available = [str(d) for d in days.presentation_date]
    target = Date.fromisoformat(str(day))
    nearest = min(available, key=lambda d: (abs((Date.fromisoformat(d) - target).days), d))
    subset = days[days.presentation_date.astype(str) == nearest].reset_index(drop=True)
    return subset, [{"code": "REPLAY_DAY_SUBSTITUTED", "field": "date",
                     "message": f"Forecast data for {day} is incomplete; using {nearest}"}]


def mode_for(mode_name: str, reserve: Optional[float], goal: Optional[str]) -> dict:
    """The settings a run uses: the base mode + the reserve + the goal tweak."""
    changes: dict[str, Any] = {}
    if reserve is not None:
        changes["floor_min_pct"] = float(reserve)
    if goal == "maximise_export_revenue":
        changes["sell_battery"] = "always"
    elif goal == "maximise_self_consumption":
        changes["sell_battery"] = "never"
    return strategy.with_changes(mode_name, changes)


@lru_cache(maxsize=64)
def run_day(station_id: str, strategy_name: str, day: str,
            reserve: Optional[float] = None, goal: Optional[str] = None):
    """(daily scores, slot-by-slot table) for one recorded day. Cached - do not mutate."""
    subset, _ = forecast_day(station_id, day)
    mode = None if strategy_name == "baseline" else mode_for(strategy_name, reserve, goal)
    return backtest.run(station_id, strategy_name, days=subset, mode=mode)


def action_for(battery_w: Optional[float], grid_w: Optional[float]) -> str:
    """Contract action enum from the planned battery/grid power (50 W deadband)."""
    deadband = config.DEADBAND_W
    if battery_w is not None and battery_w > deadband:
        return "charge"
    if battery_w is not None and battery_w < -deadband:
        return "export" if (grid_w is not None and grid_w > deadband) else "discharge"
    return "hold"


def cost_breakdown(sim, upto: int) -> dict:
    """Strict contract math over the first ``upto`` slots of a simulator table.

    import_kwh = max(-grid_w, 0) / 12000   (watts held for 5 minutes -> kWh)
    export_kwh = max(grid_w, 0) / 12000
    money      = kWh * price[c/kWh] / 100  -> AUD
    net        = import_cost - export_earnings
    """
    rows = sim.iloc[:max(0, min(int(upto), len(sim)))]
    grid = rows["grid"].astype(float)
    price = rows["price"].astype(float)
    import_kwh = (-grid).clip(lower=0) / 12000.0
    export_kwh = grid.clip(lower=0) / 12000.0
    import_cost = float((import_kwh * price).sum() / 100.0)
    export_earnings = float((export_kwh * price).sum() / 100.0)
    return {
        "intervals": int(len(rows)),
        "import_kwh": round(float(import_kwh.sum()), 6),
        "export_kwh": round(float(export_kwh.sum()), 6),
        "import_cost_aud": round(import_cost, 6),
        "export_earnings_aud": round(export_earnings, 6),
        "net_cost_aud": round(import_cost - export_earnings, 6),
        "valid_energy_intervals": int(grid.notna().sum()),
        "valid_price_intervals": int(price.notna().sum()),
    }


def _public_costs(costs: Optional[dict]) -> Optional[dict]:
    """Only the fields the contract exposes (the rest are internal counts)."""
    if costs is None:
        return None
    return {key: costs[key] for key in ("import_kwh", "export_kwh", "import_cost_aud",
                                        "export_earnings_aud", "net_cost_aud")}


def _costs(station_id: str, strategy_name: str, day: str, reserve: Optional[float],
           goal: Optional[str], first: int, last: int, warnings: list[dict],
           code: str, label: str) -> Optional[dict]:
    """Costs for slots ``first..last`` of one run, or None + a warning.

    Savings are never fabricated: a failed run returns None instead of 0.
    """
    try:
        _, sim = run_day(station_id, strategy_name, day, reserve, goal)
    except Exception as error:          # noqa: BLE001  (any strategy failure -> null costs)
        log.warning("%s simulation failed for %s: %s", label, station_id, error)
        warnings.append({"code": code, "field": label,
                         "message": f"{label} simulation failed: {error}"})
        return None
    window = sim.iloc[max(0, first):max(0, last + 1)]
    return cost_breakdown(window, len(window))


def _net(costs: Optional[dict]) -> Optional[float]:
    if costs is None or not costs["intervals"]:
        return None
    return costs["net_cost_aud"]


def window_benefit(station_id: str, sim, source_day: str, first: int, last: int,
                   warnings: list[dict]) -> tuple[Optional[float], float, Optional[float]]:
    """(baseline net, simulated net, benefit) for slots ``first..last`` of a run day.

    Shared by the sandbox apply and the P2 proposal, so both cost exactly the same
    window and neither invents a benefit when the baseline cannot be simulated.
    """
    baseline_costs = _costs(station_id, "baseline", source_day, None, None, first, last,
                            warnings, "BASELINE_FAILED", "baseline")
    simulated_costs = cost_breakdown(sim.iloc[first:last + 1], last + 1 - first)
    baseline_net = _net(baseline_costs)
    benefit = (round(baseline_net - simulated_costs["net_cost_aud"], 6)
               if baseline_net is not None else None)
    return baseline_net, simulated_costs["net_cost_aud"], benefit


def _full_day_saving(station_id: str, day: str, daily) -> tuple[Optional[float], Optional[dict]]:
    """Simulated full-day saving of ``daily`` vs the self-consumption baseline."""
    try:
        baseline_daily, _ = run_day(station_id, "baseline", day)
    except Exception as error:          # noqa: BLE001
        log.warning("baseline simulation failed for %s: %s", station_id, error)
        return None, {"code": "BASELINE_FAILED", "field": "baseline",
                      "message": f"Baseline simulation failed: {error}"}
    if daily is None or not len(daily) or not len(baseline_daily):
        return None, {"code": "BASELINE_FAILED", "field": "baseline",
                      "message": "The comparison day could not be simulated"}
    return round(float(baseline_daily.cost_c.iloc[0] - daily.cost_c.iloc[0]) / 100.0, 6), None


# ---------------------------------------------------------------------------
# GET /api/stations/{id}/financial-summary
# ---------------------------------------------------------------------------
def financial_summary(station_id: str, target: Date,
                      now: Optional[datetime] = None) -> data_adapter.Snapshot:
    """Baseline vs the station's active strategy on one day, truncated to now for today."""
    now = now or replay_clock.now_adl()
    mode, source_day, mapped_from, limit = data_adapter.resolve_day(station_id, target, now)
    settings = state_store.settings(station_id)
    warnings: list[dict] = []

    baseline_costs = _costs(station_id, "baseline", source_day, None, None, 0, limit - 1,
                            warnings, "BASELINE_FAILED", "baseline")
    actual_costs = _costs(station_id, settings["current_mode"], source_day,
                          settings["battery_reserve_pct"], settings["optimisation_goal"],
                          0, limit - 1, warnings, "STRATEGY_SIMULATION_FAILED", "actual")

    baseline_net = _net(baseline_costs)
    actual_net = _net(actual_costs)
    savings = None
    if baseline_net is not None and actual_net is not None:
        savings = round(baseline_net - actual_net, 6)
    savings_pct = (round(savings / baseline_net * 100, 1)
                   if savings is not None and baseline_net else None)

    coverage_source = actual_costs or baseline_costs or {}
    valid_energy = int(coverage_source.get("valid_energy_intervals", 0))
    valid_price = int(coverage_source.get("valid_price_intervals", 0))

    if limit == 0:
        warnings.append({"code": "NO_COMPLETED_SLOT_TODAY",
                         "message": "No 5-minute slot has finished today yet; there is no cost to report"})
    elif mode == "replay" and limit < config.SLOTS_PER_DAY:
        warnings.append({"code": "PARTIAL_DAY", "count": limit,
                         "message": f"Day in progress: {limit} of {config.SLOTS_PER_DAY} slots included"})

    start_at = replay_clock.slot_start(target, 0).isoformat()
    end_at = replay_clock.slot_start(target, limit - 1).isoformat() if limit else None
    data = {
        "date": target.isoformat(),
        "currency": "AUD",
        "actual_net_cost_aud": actual_net,
        "baseline_net_cost_aud": baseline_net,
        "today_savings_aud": savings,
        "savings_pct": savings_pct,
        "baseline": {
            "status": "available" if baseline_costs is not None else "failed",
            "method": METHOD,
            "method_version": METHOD_VERSION,
            "net_cost_aud": baseline_net,
            "message": None if baseline_costs is not None else "The self-consumption baseline could not be simulated",
        },
        "actual": {
            "strategy": settings["current_mode"],
            "optimisation_goal": settings["optimisation_goal"],
            "battery_reserve_pct": settings["battery_reserve_pct"],
            "net_cost_aud": actual_net,
        },
        "baseline_costs": _public_costs(baseline_costs),
        "actual_costs": _public_costs(actual_costs),
        "coverage": {
            "expected_intervals_to_now": limit,
            "valid_energy_intervals": valid_energy,
            "valid_price_intervals": valid_price,
            "coverage_pct": round(valid_energy / limit * 100, 1) if limit else 0.0,
        },
        "period": {"date": target.isoformat(), "start_at": start_at, "end_at": end_at,
                   "intervals": limit, "is_partial": limit < config.SLOTS_PER_DAY},
    }
    last_slot = replay_clock.slot_start(target, limit - 1) if limit else None
    freshness = replay_clock.freshness_seconds(last_slot, now)
    source = {"mode": mode,
              "dataset": f"backtest.py: baseline vs {settings['current_mode']}",
              "replay_date": source_day if mode == "replay" else None,
              "real_date": data_adapter.real_date(station_id, source_day),
              "mapped_from": mapped_from}
    return data_adapter.Snapshot(data, source, end_at, freshness,
                                 envelope.data_status(mode, freshness), warnings)


# ---------------------------------------------------------------------------
# GET /api/stations/{id}/battery
# ---------------------------------------------------------------------------
def battery_detail(station_id: str, now: Optional[datetime] = None) -> data_adapter.Snapshot:
    """Latest P0 telemetry + battery.SPECS, with a time-to-full estimate when charging."""
    snapshot = data_adapter.overview(station_id, now)
    telemetry = snapshot.data["telemetry"]
    spec = battery.SPECS[station_id]
    soc = telemetry["soc_pct"]
    state = snapshot.data["states"]["battery"]
    power_kw = data_adapter.kw(abs(telemetry["battery_w"])) if telemetry["battery_w"] is not None else None

    minutes = None
    if state == "charging" and power_kw and soc is not None:
        # energy still missing / the power actually stored per hour
        minutes = round(((1 - soc / 100.0) * spec["capacity_kwh"]) / (power_kw * spec["eff"]) * 60.0, 1)

    data = {
        "station_id": station_id,
        "timestamp": snapshot.data_timestamp,
        "soc_pct": soc,
        "state": state,
        "power_kw": power_kw,
        "signed_power_kw": data_adapter.kw(telemetry["battery_w"]),
        "capacity_kwh": float(spec["capacity_kwh"]),
        "max_charge_power_kw": round(spec["max_power_w"] / 1000.0, 3),
        "max_discharge_power_kw": round(spec["max_power_w"] / 1000.0, 3),
        "reserve_pct": state_store.settings(station_id)["battery_reserve_pct"],
        "estimated_time_to_full_min": minutes,
        "estimate_quality": "high" if minutes is not None else "unavailable",
    }
    source = dict(snapshot.source)
    source["dataset"] = "recorded telemetry + battery.SPECS + state_store"
    return data_adapter.Snapshot(data, source, snapshot.data_timestamp,
                                 snapshot.freshness_seconds, snapshot.data_status, snapshot.warnings)


# ---------------------------------------------------------------------------
# GET /api/stations/{id}/recommendation
# ---------------------------------------------------------------------------
def recommendation(station_id: str, now: Optional[datetime] = None) -> data_adapter.Snapshot:
    """The MPC plan's action for the current 5-minute slot of the replayed day."""
    now = now or replay_clock.now_adl()
    today = now.date()
    slot = min(max(replay_clock.current_slot(now), 0), config.SLOTS_PER_DAY - 1)
    settings = state_store.settings(station_id)
    day = data_adapter.replay_day(station_id)
    warnings: list[dict] = []

    try:
        daily, sim = run_day(station_id, settings["current_mode"], day,
                             settings["battery_reserve_pct"], settings["optimisation_goal"])
    except Exception as error:          # noqa: BLE001
        log.warning("MPC plan failed for %s: %s", station_id, error)
        raise ApiError(503, "STRATEGY_UNAVAILABLE",
                       f"The MPC plan is unavailable: {error}", retryable=True)

    subset, day_warnings = forecast_day(station_id, day)
    warnings.extend(day_warnings)
    row = sim.iloc[slot]
    battery_w, grid_w = float(row["battery"]), float(row["grid"])
    action = action_for(battery_w, grid_w)
    soc_now = float(row["soc"])
    reserve = settings["battery_reserve_pct"]

    # next 6 hours of the plan's own inputs: realised spot price + the PV forecast
    price = np.asarray(subset.iloc[0]["price"], dtype=float)
    pv_fc = np.asarray(subset.iloc[0]["pv_fc"], dtype=float)
    window = slice(slot, min(slot + 72, config.SLOTS_PER_DAY))
    price_now = float(price[slot])
    pv_peak_w = float(np.nanmax(pv_fc[window])) if len(pv_fc[window]) else 0.0
    peak_offset = int(np.nanargmax(price[window])) if len(price[window]) else 0
    price_peak = float(price[window][peak_offset]) if len(price[window]) else price_now
    peak_time = replay_clock.slot_time(min(slot + peak_offset, config.SLOTS_PER_DAY - 1))

    kw = abs(battery_w) / 1000.0
    text = {
        "charge": f"Charge the battery at {kw:.1f} kW now; the spot price is {price_now:.1f} c/kWh.",
        "export": f"Export {kw:.1f} kW from the battery now; the spot price is {price_now:.1f} c/kWh.",
        "discharge": f"Run the house from the battery at {kw:.1f} kW instead of buying at {price_now:.1f} c/kWh.",
        "hold": f"Hold the battery at {soc_now:.0f}% SOC; the spot price is {price_now:.1f} c/kWh.",
    }[action]
    reason = (f"Spot price {price_now:.1f} c/kWh with the battery at {soc_now:.0f}% "
              f"(reserve {reserve:.0f}%). PV is forecast to peak at {pv_peak_w / 1000.0:.1f} kW and "
              f"the highest price in the next 6 h is {price_peak:.1f} c/kWh at "
              f"{peak_time.strftime('%H:%M')}, so the {settings['current_mode']} plan {VERB[action]}.")

    savings, saving_warning = _full_day_saving(station_id, day, daily)
    if saving_warning:
        warnings.append(saving_warning)

    start = replay_clock.slot_start(today, slot)
    valid_to = start + timedelta(minutes=30)
    data = {
        "recommendation_id": f"rec_{today:%Y%m%d}_{start:%H%M}_{station_id}",
        "station_id": station_id,
        "action": action,
        "text": text,
        "reason": reason,
        "reserve_pct": reserve,
        "window": {"start_at": start.isoformat(), "end_at": valid_to.isoformat()},
        "estimated_savings_aud": savings,
        "status": "draft",
        "valid_from": start.isoformat(),
        "valid_to": valid_to.isoformat(),
        "input_freshness": (replay_clock.freshness_seconds(replay_clock.slot_start(today, slot - 1), now)
                            if slot else None),
        "model_version": MODEL_VERSION,
        "price_now_c_per_kwh": round(price_now, 2),
        "soc_pct": round(soc_now, 1),
        "planned_battery_w": round(battery_w, 1),
        "planned_grid_w": round(grid_w, 1),
    }
    freshness = replay_clock.freshness_seconds(start, now)
    source = {"mode": "replay",
              "dataset": f"optimizer.MPCController ({settings['current_mode']})",
              "replay_date": day,
              "real_date": data_adapter.real_date(station_id, day),
              "mapped_from": None}
    return data_adapter.Snapshot(data, source, start.isoformat(), freshness,
                                 envelope.data_status("replay", freshness), warnings)


# ---------------------------------------------------------------------------
# POST /api/stations/{id}/simulate  (simulation only - never a device command)
# ---------------------------------------------------------------------------
def _parse_moment(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "VALIDATION_ERROR", f"{field} is required (ISO 8601)", field=field)
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ApiError(400, "VALIDATION_ERROR",
                       f"{field} must be an ISO 8601 timestamp; got '{value}'", field=field)
    # naive input is read as the Adelaide wall clock, like every other timestamp
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=envelope.ADL)


def simulate(station_id: str, payload: dict, now: Optional[datetime] = None) -> data_adapter.Snapshot:
    """Run the requested goal over a recorded day and store the result as simulated."""
    sid = data_adapter.require_station(station_id)
    if str(payload.get("mode", "")).strip().lower() != "simulation":
        raise ApiError(400, "VALIDATION_ERROR",
                       "The sandbox is simulation-only: mode must be 'simulation'", field="mode")
    requested = payload.get("strategy") or {}
    if not isinstance(requested, dict) or not str(requested.get("type") or "").strip():
        raise ApiError(400, "VALIDATION_ERROR", "strategy.type is required", field="strategy.type")
    goal = state_store.normalize_goal(requested.get("optimisation_goal"),
                                      field="strategy.optimisation_goal")
    reserve = state_store.require_reserve(sid, requested.get("battery_reserve_pct"),
                                          check_spec=False,
                                          field="strategy.battery_reserve_pct")
    start = _parse_moment(requested.get("start_at"), "strategy.start_at")
    end = _parse_moment(requested.get("end_at"), "strategy.end_at")
    if end <= start:
        raise ApiError(400, "VALIDATION_ERROR",
                       "strategy.end_at must be after strategy.start_at", field="strategy.end_at")
    if start.date() != end.date():
        raise ApiError(400, "VALIDATION_ERROR",
                       "strategy.start_at and strategy.end_at must be on the same day",
                       field="strategy.end_at")

    now = now or replay_clock.now_adl()
    _, source_day, mapped_from, _ = data_adapter.resolve_day(sid, start.date(), now)
    # slots whose start lies in [start_at, end_at] are costed (end_at is inclusive)
    first = replay_clock.current_slot(start)
    last = min(replay_clock.current_slot(end), config.SLOTS_PER_DAY - 1)
    strategy_name = SIM_STRATEGY[goal]
    try:
        _, sim = run_day(sid, strategy_name, source_day, reserve, goal)
    except Exception as error:          # noqa: BLE001
        log.warning("simulation failed for %s: %s", sid, error)
        raise ApiError(503, "STRATEGY_UNAVAILABLE",
                       f"The simulation could not run: {error}", retryable=True)

    warnings: list[dict] = []
    baseline_net, simulated_net, benefit = window_benefit(sid, sim, source_day, first, last,
                                                        warnings)

    record = {
        "strategy_id": "sim_strat_" + uuid.uuid4().hex[:8],
        "station_id": sid,
        "mode": "simulation",
        "status": "applied_in_simulation",
        "device_control_attempted": False,
        "strategy_type": str(requested["type"]).strip(),
        "optimisation_goal": goal,
        "battery_reserve_pct": reserve,
        "valid_from": start.isoformat(),
        "valid_to": end.isoformat(),
        "date": start.date().isoformat(),
        "replay_date": source_day,
        "baseline_net_cost_aud": baseline_net,
        "simulated_net_cost_aud": simulated_net,
        "estimated_benefit_aud": benefit,
        "recommendation_id": payload.get("recommendation_id"),
        "message": SIMULATION_MESSAGE,
        "created_at": envelope.generated_at(now),
    }
    state_store.save_simulation(sid, record)
    source = {"mode": "simulation",
              "dataset": f"backtest.py: baseline vs {strategy_name}",
              "replay_date": source_day,
              "real_date": data_adapter.real_date(sid, source_day),
              "mapped_from": mapped_from}
    return data_adapter.Snapshot(dict(record), source, start.isoformat(), None,
                                 "simulation", warnings)


# ---------------------------------------------------------------------------
# GET / PUT /api/stations/{id}/strategy
# ---------------------------------------------------------------------------
def current_strategy(station_id: str) -> data_adapter.Snapshot:
    """The station's settings + its latest simulated strategy (None until one is run)."""
    sid = data_adapter.require_station(station_id)
    data = {
        "station_id": sid,
        "settings": state_store.settings(sid),
        "latest_simulation": state_store.latest_simulation(sid),
    }
    source = {"mode": "static", "dataset": "state_store (in-memory)"}
    return data_adapter.Snapshot(data, source, None, None,
                                 envelope.data_status("static", None), [])


def update_strategy_settings(station_id: str, payload: dict) -> data_adapter.Snapshot:
    """Change the active (simulated) strategy settings; the reserve must fit the spec."""
    sid = data_adapter.require_station(station_id)
    settings = state_store.update_settings(
        sid,
        current_mode=payload.get("current_mode"),
        battery_reserve_pct=payload.get("battery_reserve_pct"),
        optimisation_goal=payload.get("optimisation_goal"),
    )
    data = {"station_id": sid, "settings": settings,
            "latest_simulation": state_store.latest_simulation(sid)}
    source = {"mode": "static", "dataset": "state_store (in-memory)"}
    return data_adapter.Snapshot(data, source, None, None,
                                 envelope.data_status("static", None), [])


# ---------------------------------------------------------------------------
# Startup warm-up
# ---------------------------------------------------------------------------
def warm_today(station_id: str) -> list[str]:
    """Pre-run today's baseline + active mode so the first P1 request is fast."""
    settings = state_store.settings(station_id)
    day = data_adapter.replay_day(station_id)
    runs = [("baseline", None, None),
            (settings["current_mode"], settings["battery_reserve_pct"],
             settings["optimisation_goal"])]
    for strategy_name, reserve, goal in runs:
        run_day(station_id, strategy_name, day, reserve, goal)
    return [name for name, _, _ in runs]
