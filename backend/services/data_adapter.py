"""Hybrid data source for the P0 dashboard.

Today  -> a fixed complete recorded day (config.REPLAY_DATE), truncated to the
          slots that have already finished and re-dated to the real Adelaide
          date (mode="replay"). Future slots are never returned or padded.
Past   -> the recorded day itself, or (outside the recording range) a
          deterministic modulo-mapped archive day (mode="demo").
Future -> HTTP 400 INVALID_DATE.

Signs follow the strategy code: battery_w > 0 charges, grid_w > 0 exports.
Missing readings stay None; they are never turned into 0.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta
from functools import lru_cache
from typing import Optional

import pandas as pd

import config  # noqa: E402  (puts strategy/codes on sys.path before battery/forecast)
import battery
import forecast
from services import envelope, fault_injection, replay_clock
from services.errors import ApiError

TELEMETRY = ("pv_w", "load_w", "battery_w", "grid_w", "soc_pct")
CAPABILITIES = {"battery": True, "weather": True, "simulation": True, "device_control": False}


# ---------------------------------------------------------------------------
# Recorded data (read once, reused)
# ---------------------------------------------------------------------------
def _normalise_signs(frame: pd.DataFrame) -> pd.DataFrame:
    """Flip battery_w when the recording uses the discharge sign.

    The contract (module docstring) is battery_w > 0 charges; battery.py,
    battery_state() and flow_directions() all rely on it. SBX-002 and SBX-003
    were recorded the other way round - their battery reading and their SOC move
    in opposite directions - so compare the two and flip the file once, on load,
    instead of special-casing the station in every consumer.

    ponytail: a recording with a flat SOC cannot be judged and is left as-is.
    """
    agreement = float((frame["battery_w"].fillna(0.0) * frame["soc_pct"].diff().fillna(0.0)).sum())
    if agreement >= 0:
        return frame
    return frame.assign(battery_w=frame["battery_w"].mul(-1.0) + 0.0)  # + 0.0: no "-0.0" in the API


@lru_cache(maxsize=None)
def station_days(station_id: str) -> dict[str, pd.DataFrame]:
    """presentation_date -> rows in recorded order (slot = row position, like battery.py)."""
    path = config.STRATEGY_DATA / f"{station_id}_clean.csv"
    if not path.exists():
        raise ApiError(404, "STATION_NOT_FOUND", f"Unknown station '{station_id}'", field="station_id")
    # ghi_w_m2 is not dashboard telemetry; the health service needs it (fault_detection.py
    # judges PV against the sunlight that was actually recorded).
    frame = pd.read_csv(path, usecols=["presentation_date", "replayed_from", *TELEMETRY, "ghi_w_m2"])
    frame = _normalise_signs(frame)
    return {str(day): group.reset_index(drop=True)
            for day, group in frame.groupby("presentation_date", sort=True)}


@lru_cache(maxsize=None)
def complete_days(station_id: str) -> tuple[str, ...]:
    return tuple(day for day, frame in station_days(station_id).items()
                 if len(frame) == config.SLOTS_PER_DAY)


@lru_cache(maxsize=None)
def replay_day(station_id: str) -> str:
    """The fixed complete day replayed for "today" (nearest complete day if needed)."""
    days = station_days(station_id)
    wanted = config.REPLAY_DATE
    if wanted in days and len(days[wanted]) == config.SLOTS_PER_DAY:
        return wanted
    complete = complete_days(station_id)
    if not complete:
        raise ApiError(500, "NO_DATA", f"{station_id} has no complete recorded day")
    target = Date.fromisoformat(wanted)
    return min(complete, key=lambda day: (abs((Date.fromisoformat(day) - target).days), day))


def real_date(station_id: str, day: str) -> Optional[str]:
    """The real recording date behind a presentation date (prices/weather are keyed by it)."""
    frame = station_days(station_id).get(day)
    if frame is None or frame.empty:
        return None
    return str(frame["replayed_from"].iloc[0])


def _value(raw) -> Optional[float]:
    """CSV cell -> float, or None for NaN/blank (never 0)."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


@lru_cache(maxsize=1)
def _prices() -> dict[str, dict[int, float]]:
    frame = pd.read_csv(config.STRATEGY_DATA / "prices_aemo.csv")
    return {str(day): dict(zip(group.slot.astype(int), group.price_c_kwh.astype(float)))
            for day, group in frame.groupby("real_date", sort=True)}


def price_c_per_kwh(real_day: Optional[str], slot: int) -> Optional[float]:
    """Raw AEMO spot price in c/kWh (the API divides it by 100)."""
    if not real_day:
        return None
    return _prices().get(real_day, {}).get(slot)


@lru_cache(maxsize=1)
def _weather() -> pd.DataFrame:
    return forecast.load_weather()


def weather_cell(real_day: str, slot: int) -> dict:
    """Weather for a slot from weather_cache.pkl, on forecast.load_weather's 5-min grid."""
    stamp = pd.Timestamp(real_day) + pd.Timedelta(minutes=5 * slot)
    row = _weather().reindex([stamp]).iloc[0]
    return {"temp_c": _value(row["temp_c"]),
            "cloud_pct": _value(row["cloud_pct"]),
            "ghi_w_m2": _value(row["ghi_w_m2"])}


def weather_condition(ghi_w_m2: Optional[float], cloud_pct: Optional[float], slot: int) -> str:
    if ghi_w_m2 is None:
        return "unknown"
    if slot < 6 * 12 or slot >= 19 * 12 or ghi_w_m2 <= 0:
        return "night"
    if cloud_pct is None:
        return "unknown"
    if cloud_pct < 20:
        return "clear"
    if cloud_pct < 60:
        return "partly_cloudy"
    if cloud_pct < 85:
        return "cloudy"
    return "overcast"


# ---------------------------------------------------------------------------
# Sign conventions (strict: deadband = 50 W)
# ---------------------------------------------------------------------------
def battery_state(watts: Optional[float]) -> str:
    if watts is None:
        return "unknown"
    if watts > config.DEADBAND_W:
        return "charging"
    if watts < -config.DEADBAND_W:
        return "discharging"
    return "idle"


def grid_state(watts: Optional[float]) -> str:
    if watts is None:
        return "unknown"
    if watts > config.DEADBAND_W:
        return "exporting"
    if watts < -config.DEADBAND_W:
        return "importing"
    return "neutral"


def kw(watts: Optional[float]) -> Optional[float]:
    return None if watts is None else round(watts / 1000.0, 3)


def flow_directions(telemetry: dict) -> list[str]:
    """Which energy paths are active, derived only from the sign of each reading."""
    deadband = config.DEADBAND_W
    pv = telemetry.get("pv_w")
    load = telemetry.get("load_w")
    batt = telemetry.get("battery_w")
    grid = telemetry.get("grid_w")
    solar = pv is not None and pv > deadband
    home = load is not None and load > deadband
    charging = batt is not None and batt > deadband
    discharging = batt is not None and batt < -deadband
    exporting = grid is not None and grid > deadband
    importing = grid is not None and grid < -deadband
    directions = []
    if solar and home:
        directions.append("solar_to_home")
    if solar and charging:
        directions.append("solar_to_battery")
    if solar and exporting:
        directions.append("solar_to_grid")
    if discharging and home:
        directions.append("battery_to_home")
    if discharging and exporting:
        directions.append("battery_to_grid")
    if importing and home:
        directions.append("grid_to_home")
    if importing and charging:
        directions.append("grid_to_battery")
    return directions


def require_station(station_id: str) -> str:
    sid = (station_id or "").strip().upper()
    if sid not in config.STATIONS:
        raise ApiError(404, "STATION_NOT_FOUND", f"Unknown station '{station_id}'", field="station_id")
    return sid


# ---------------------------------------------------------------------------
# One day of points
# ---------------------------------------------------------------------------
@dataclass
class Slice:
    station_id: str
    date: Date
    mode: str                                  # replay | demo
    dataset: str
    points: list[dict]
    missing_intervals: list[dict]
    received_points: int
    is_partial: bool
    warnings: list[dict]
    freshness: Optional[int]
    replay_date: Optional[str] = None
    real_date: Optional[str] = None
    mapped_from: Optional[str] = None
    expected_points: int = config.SLOTS_PER_DAY
    injected_slots: int = 0                    # slots changed by fault_config.json

    @property
    def completeness_pct(self) -> float:
        return round(self.received_points / self.expected_points * 100, 1)

    @property
    def data_timestamp(self) -> Optional[str]:
        return self.points[-1]["timestamp"] if self.points else None

    @property
    def data_status(self) -> str:
        return envelope.data_status(self.mode, self.freshness)

    def source(self) -> dict:
        return {"mode": self.mode, "dataset": self.dataset, "replay_date": self.replay_date,
                "real_date": self.real_date, "mapped_from": self.mapped_from}


def _point(slot: int, day: Date, values: dict) -> dict:
    quality = "missing" if any(value is None for value in values.values()) else "good"
    return {"timestamp": replay_clock.slot_start(day, slot).isoformat(), **values, "quality": quality}


def resolve_day(station_id: str, target: Date, now: Optional[datetime] = None) -> tuple[str, str, Optional[str], int]:
    """Map a requested date onto a recorded day: (mode, source_day, mapped_from, slots).

    Today -> the fixed replay day, limited to completed slots. Past -> the recorded
    day itself, or a deterministic modulo-mapped past recording (mode="demo").
    Future -> 400 INVALID_DATE.
    """
    now = now or replay_clock.now_adl()
    today = now.date()
    if target > today:
        raise ApiError(400, "INVALID_DATE",
                       f"{target.isoformat()} is in the future; only completed data is served",
                       field="date")

    days = station_days(station_id)
    if target == today:
        return "replay", replay_day(station_id), None, replay_clock.completed_points(now)
    if str(target) in days:
        return "demo", str(target), None, config.SLOTS_PER_DAY
    # a past date maps onto a past recording (never onto a future archive day)
    complete = tuple(day for day in complete_days(station_id)
                     if day < today.isoformat()) or complete_days(station_id)
    anchor = Date.fromisoformat(complete[0])
    source_day = complete[(target - anchor).days % len(complete)]
    return "demo", source_day, source_day, config.SLOTS_PER_DAY


def day_slice(station_id: str, target: Date, now: Optional[datetime] = None) -> Slice:
    """Points for one requested date, truncated to completed slots when it is today."""
    now = now or replay_clock.now_adl()
    mode, source_day, mapped_from, limit = resolve_day(station_id, target, now)
    frame = station_days(station_id)[source_day]
    first_fault, last_fault = fault_injection.window(station_id, mode)   # (0, 0) when inert
    points, missing, received, injected = [], [], 0, 0
    for slot in range(limit):
        if slot < len(frame):
            values = {name: _value(frame.at[slot, name]) for name in TELEMETRY}
        else:
            values = {name: None for name in TELEMETRY}
        if first_fault <= slot < last_fault and fault_injection.zero_pv(values):
            injected += 1
        point = _point(slot, target, values)
        points.append(point)
        if all(value is None for value in values.values()):
            missing.append({"slot": slot, "timestamp": point["timestamp"]})
        else:
            received += 1

    warnings = []
    if missing:
        warnings.append({"code": "MISSING_DATA",
                         "message": f"{len(missing)} slot(s) have no telemetry; values are null with quality='missing'",
                         "count": len(missing)})
    if mode == "replay" and len(points) < config.SLOTS_PER_DAY:
        warnings.append({"code": "PARTIAL_DAY",
                         "message": f"Day in progress: {len(points)} of {config.SLOTS_PER_DAY} slots completed",
                         "count": len(points)})
    if injected:
        warnings.append({"code": "FAULT_INJECTED",
                         "message": f"Simulated PV fault: PV forced to 0 W for {injected} slot(s) "
                                    f"({fault_injection.window_label(first_fault, last_fault)})",
                         "count": injected})

    last_slot = replay_clock.slot_start(target, limit - 1) if limit else None
    return Slice(
        station_id=station_id,
        date=target,
        mode=mode,
        dataset=f"{station_id}_clean.csv",
        points=points,
        missing_intervals=missing,
        received_points=received,
        is_partial=mode == "replay" and len(points) < config.SLOTS_PER_DAY,
        warnings=warnings,
        freshness=replay_clock.freshness_seconds(last_slot, now),
        replay_date=source_day if mode == "replay" else None,
        real_date=real_date(station_id, source_day),
        mapped_from=mapped_from,
        injected_slots=injected,
    )


@dataclass
class Snapshot:
    data: dict
    source: dict
    data_timestamp: Optional[str]
    freshness_seconds: Optional[int]
    data_status: str
    warnings: list[dict]


def overview(station_id: str, now: Optional[datetime] = None) -> Snapshot:
    """Latest completed 5-minute slot, with sign-derived display/state/flow."""
    now = now or replay_clock.now_adl()
    day = day_slice(station_id, now.date(), now)
    if not day.points:
        # ponytail: between 00:00 and 00:04 no slot has finished today, so show
        # yesterday's last completed slot instead of inventing one.
        day = day_slice(station_id, now.date() - timedelta(days=1), now)
        day.warnings = day.warnings + [{
            "code": "NO_COMPLETED_SLOT_TODAY",
            "message": "No 5-minute slot has finished today yet; showing yesterday's last completed slot",
        }]
    if not day.points:
        raise ApiError(503, "NO_DATA", f"No telemetry available for {station_id}")

    point = day.points[-1]
    telemetry = {name: point[name] for name in TELEMETRY}
    battery_w, grid_w = telemetry["battery_w"], telemetry["grid_w"]
    data = {
        "timestamp": point["timestamp"],
        "telemetry": telemetry,
        "display": {
            "solar_power_kw": kw(telemetry["pv_w"]),
            # ponytail: net-metered recordings report a negative load while the
            # home exports; that is not a consumption figure, so floor it at 0
            # (the grid card carries the export direction).
            "home_usage_kw": kw(max(telemetry["load_w"], 0.0)) if telemetry["load_w"] is not None else None,
            "battery_power_kw": kw(abs(battery_w)) if battery_w is not None else None,
            "grid_power_kw": kw(abs(grid_w)) if grid_w is not None else None,
        },
        "states": {
            "battery": battery_state(battery_w),
            "grid": grid_state(grid_w),
            "system": ("online" if day.freshness is not None and day.freshness <= config.FRESH_OK_SECONDS
                       else "delayed"),
        },
        "flow": {"mode": "direction_only", "active_directions": flow_directions(telemetry)},
    }
    return Snapshot(data, day.source(), point["timestamp"], day.freshness, day.data_status, day.warnings)


def _forecast_prices(station_id: str, day: str) -> Optional[list[float]]:
    """Today's forecast price profile (c/kWh) the planner is allowed to know, or None.

    ponytail: the peak-time hint is optional, so a missing/partial forecast model
    degrades it to null instead of failing the price and weather in /market. It reads
    the boot warm-up's cache and returns None until that cache exists - building the
    forecast here took minutes and left /market hanging on the request that asked.
    """
    days = forecast.cached(station_id)
    if days is None:
        return None
    try:
        row = days[days.presentation_date.astype(str) == str(day)]
        return [float(price) for price in row.iloc[0]["price_fc"]]
    except (KeyError, IndexError, TypeError, ValueError, OSError):
        return None


def expected_peak_price_time(station_id: str, day: str, today: Date, slot: int) -> Optional[str]:
    """ISO start of the highest forecast price still ahead today (None when none is left).

    Uses forecast.prepare's 7-day price profile - the same forecast the planner may
    use, never a reading from the future. A NaN slot counts as "no forecast".
    """
    prices = _forecast_prices(station_id, day)
    if not prices:
        return None
    best, best_price = None, None
    for offset in range(slot + 1, len(prices)):
        price = prices[offset]
        if price != price:                      # NaN: no forecast for this slot
            continue
        if best_price is None or price > best_price:
            best, best_price = offset, price
    return None if best is None else replay_clock.slot_start(today, best).isoformat()


def market(station_id: str, now: Optional[datetime] = None) -> Snapshot:
    """Latest wholesale spot price + weather for the slot the virtual clock is in."""
    now = now or replay_clock.now_adl()
    today = now.date()
    slot = min(replay_clock.current_slot(now), config.SLOTS_PER_DAY - 1)
    replayed = replay_day(station_id)
    real_day = real_date(station_id, replayed)
    start = replay_clock.slot_start(today, slot)
    valid_to = start + timedelta(minutes=5)

    raw_c = price_c_per_kwh(real_day, slot)
    quote = {
        "value": None if raw_c is None else round(raw_c / 100.0, 5),   # c/kWh -> AUD/kWh
        "price_type": "wholesale_spot",
        "unit": "AUD_per_kWh",
        "currency": "AUD",
        "valid_from": start.isoformat(),
        "valid_to": valid_to.isoformat(),
    }
    warnings = []
    if raw_c is None:
        warnings.append({"code": "MISSING_DATA", "field": "price",
                         "message": f"No AEMO price for real date {real_day} slot {slot}"})

    cell = weather_cell(real_day, slot) if real_day else {"temp_c": None, "cloud_pct": None, "ghi_w_m2": None}
    if cell["temp_c"] is None:
        warnings.append({"code": "MISSING_DATA", "field": "weather",
                         "message": f"No weather for real date {real_day} slot {slot}"})
    weather = {
        "temperature_c": cell["temp_c"],
        "cloud_cover_pct": cell["cloud_pct"],
        "ghi_w_m2": cell["ghi_w_m2"],
        "condition": weather_condition(cell["ghi_w_m2"], cell["cloud_pct"], slot),
        "observed_at": start.isoformat(),
    }

    # ponytail: only the current replay day's finished slots - the chart averages them per
    # hour, so a midnight-crossing lookback would add code for one extra stale day of curve.
    series = _prices().get(real_day, {})
    history = [{"timestamp": replay_clock.slot_start(today, s).isoformat(),
                "price_aud_per_kwh": round(series[s] / 100.0, 5)}
               for s in range(slot + 1) if s in series]

    data = {"slot": slot, "price": {"import": quote, "feed_in": dict(quote)},
            "history": history, "weather": weather,
            "expected_peak_price_time": expected_peak_price_time(station_id, replayed, today, slot)}
    freshness = replay_clock.freshness_seconds(start, now)
    source = {"mode": "replay", "dataset": "prices_aemo.csv + weather_cache.pkl",
              "replay_date": replayed, "real_date": real_day, "mapped_from": None}
    return Snapshot(data, source, start.isoformat(), freshness,
                    envelope.data_status("replay", freshness), warnings)


@lru_cache(maxsize=1)
def stations() -> tuple[dict, ...]:
    """Station catalogue: battery.SPECS, enriched with stations.json when it exists."""
    profiles = {}
    if config.STATIONS_JSON.exists():
        try:
            profiles = json.loads(config.STATIONS_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            profiles = {}
    catalogue = []
    for sid in config.STATIONS:
        spec = battery.SPECS.get(sid)
        if spec is None:
            continue
        entry = profiles.get(sid) if isinstance(profiles, dict) else None
        profile = entry.get("profile") if isinstance(entry, dict) else None
        catalogue.append({
            "id": sid,
            "name": (profile or {}).get("name") or sid,
            "spec": {"capacity_kwh": spec["capacity_kwh"], "max_power_w": spec["max_power_w"],
                     "soc_min": spec["soc_min"], "soc_max": spec["soc_max"], "efficiency": spec["eff"]},
            "capabilities": dict(CAPABILITIES),
            "profile": profile,
        })
    return tuple(catalogue)
