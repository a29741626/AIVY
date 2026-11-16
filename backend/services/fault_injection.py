"""Simulated PV faults for the demo, driven by ``backend/fault_config.json``.

The file is re-read whenever its mtime changes, so editing it applies to the next
request without restarting the server. Anything wrong with it (missing, invalid
JSON, unknown fault type) disables injection and logs a warning: a demo knob must
never take the API down, and an injected fault must never be silent either - the
day slice that carries one also carries a FAULT_INJECTED warning.

``pv_zero`` is the only supported fault: solar output drops to 0 W for every slot
in the window, and the grid field is rewritten as ``-load - battery`` so the row
still obeys the energy balance the detector checks. (The recorded replay day has
no negative-price curtailment, and a hardware PV failure re-imports the shortfall
rather than leaving the meter out of balance.)
"""

from __future__ import annotations

import json
import logging

import config

log = logging.getLogger("uvicorn.error")

CONFIG_PATH = config.BACKEND_DIR / "fault_config.json"
SUPPORTED_FAULTS = ("pv_zero",)
SLOTS_PER_HOUR = config.SLOTS_PER_DAY // 24          # 12 five-minute slots per hour

DISABLED = {"enabled": False, "target_stations": (), "fault_type": None,
            "start_hour": 0, "end_hour": 0, "apply_to_replay_date_only": True}

_state = {"mtime": None, "config": DISABLED}


def _hour(value, fallback: int) -> int:
    try:
        return min(max(int(value), 0), 24)
    except (TypeError, ValueError):
        return fallback


def _normalise(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    stations = raw.get("target_stations") or []
    if isinstance(stations, str):
        stations = [stations]
    targets = tuple(str(s).strip().upper() for s in stations if str(s).strip())
    fault = str(raw.get("fault_type") or "").strip()
    start = _hour(raw.get("start_hour"), 0)
    end = _hour(raw.get("end_hour"), 0)
    wanted = raw.get("enabled") is True
    enabled = wanted and fault in SUPPORTED_FAULTS and bool(targets) and start < end
    if wanted and not enabled:
        log.warning("fault_config.json ignored: needs fault_type %s, one target station "
                    "and start_hour < end_hour", SUPPORTED_FAULTS)
    return {"enabled": enabled, "target_stations": targets,
            "fault_type": fault if enabled else None, "start_hour": start, "end_hour": end,
            "apply_to_replay_date_only": bool(raw.get("apply_to_replay_date_only", True))}


def config_now() -> dict:
    """Active config, re-read only when the file's mtime changed."""
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        _state.update(mtime=None, config=DISABLED)      # deleted -> injection off
        return DISABLED
    if _state["mtime"] == mtime:
        return _state["config"]
    try:
        value = _normalise(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as error:
        log.warning("fault_config.json unusable (%s) -> fault injection disabled", error)
        value = DISABLED
    _state.update(mtime=mtime, config=value)
    return value


def window(station_id: str, mode: str) -> tuple[int, int]:
    """(first slot, last slot exclusive) to force to zero for this station, or (0, 0)."""
    cfg = config_now()
    if not cfg["enabled"] or station_id not in cfg["target_stations"]:
        return 0, 0
    if cfg["apply_to_replay_date_only"] and mode != "replay":
        return 0, 0
    return cfg["start_hour"] * SLOTS_PER_HOUR, cfg["end_hour"] * SLOTS_PER_HOUR


def window_label(first: int, last: int) -> str:
    """'11:00-13:00' for messages; empty when injection is inert."""
    if first >= last:
        return ""
    return f"{first // SLOTS_PER_HOUR:02d}:00-{last // SLOTS_PER_HOUR:02d}:00"


def zero_pv(values: dict) -> bool:
    """Force one slot's readings to a dead PV array. True when the row changed.

    A slot with no recorded PV reading is left alone: injection must not turn a
    missing reading into a 0 W reading.
    """
    if values.get("pv_w") is None:
        return False
    values["pv_w"] = 0.0
    load, battery = values.get("load_w"), values.get("battery_w")
    if load is not None and battery is not None:
        values["grid_w"] = 0.0 - load - battery
    return True
