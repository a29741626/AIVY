"""Per-station strategy settings and simulated strategies, held in process memory.

Every station starts in "balanced"; a user (or the P1 settings endpoint) can change
the mode, the reserve and the optimisation goal, and every sandbox simulation is
kept so ``GET /api/strategies/current`` can return the latest one.

ponytail: no JSON on disk - a restart returns each station to its mode default.
The upgrade path is one state file behind these same functions (settings /
update_settings / save_simulation / latest_simulation).
"""

from __future__ import annotations

import math
import threading
from typing import Any, Optional

import config  # noqa: F401  (puts strategy/codes on sys.path before battery/strategy)
import battery
import strategy
from services import envelope
from services.errors import ApiError

DEFAULT_MODE = "balanced"
DEFAULT_GOAL = "minimise_cost"
GOALS = ("minimise_cost", "maximise_export_revenue", "maximise_self_consumption")
MODE_ALIASES = {"smart_savings": "save_money"}

_lock = threading.Lock()
_settings: dict[str, dict[str, Any]] = {}
_simulations: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Validation helpers (shared with strategy_engine, 400 VALIDATION_ERROR)
# ---------------------------------------------------------------------------
def normalize_mode(name: Optional[str], field: str = "current_mode") -> str:
    """Contract mode name -> a strategy.MODES key ("smart_savings" = "save_money")."""
    key = (name or "").strip().lower()
    key = MODE_ALIASES.get(key, key)
    if key not in strategy.MODES:
        raise ApiError(400, "VALIDATION_ERROR",
                       f"{field} must be one of {sorted(strategy.MODES)} or 'smart_savings'; got '{name}'",
                       field=field)
    return key


def normalize_goal(name: Optional[str], field: str = "optimisation_goal") -> str:
    key = (name or "").strip().lower()
    if key not in GOALS:
        raise ApiError(400, "VALIDATION_ERROR",
                       f"{field} must be one of {list(GOALS)}; got '{name}'", field=field)
    return key


def require_reserve(station_id: str, value: Any, *, check_spec: bool,
                    field: str = "battery_reserve_pct") -> float:
    """0..100 always; settings also stay inside the battery's soc_min..soc_max."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, "VALIDATION_ERROR", f"{field} must be a number; got {value!r}", field=field)
    if math.isnan(number) or not 0 <= number <= 100:
        raise ApiError(400, "VALIDATION_ERROR",
                       f"{field} must be between 0 and 100; got {number}", field=field)
    spec = battery.SPECS[station_id]
    if check_spec and not spec["soc_min"] <= number <= spec["soc_max"]:
        raise ApiError(400, "VALIDATION_ERROR",
                       f"{field} must be between {spec['soc_min']} and {spec['soc_max']} "
                       f"for {station_id}; got {number}", field=field)
    return number


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _defaults(station_id: str) -> dict[str, Any]:
    return {
        "station_id": station_id,
        "current_mode": DEFAULT_MODE,
        # strategy.floor_pct() needs load forecasts; the mode's own minimum is the
        # data-free start and sits inside soc_min..soc_max for every spec.
        "battery_reserve_pct": float(strategy.MODES[DEFAULT_MODE]["floor_min_pct"]),
        "optimisation_goal": DEFAULT_GOAL,
        "strategy_status": "active",
        "updated_at": envelope.generated_at(),
        "updated_by": "system",
    }


def settings(station_id: str) -> dict[str, Any]:
    with _lock:
        return dict(_settings.setdefault(station_id, _defaults(station_id)))


def update_settings(station_id: str, *, current_mode: Optional[str] = None,
                    battery_reserve_pct: Optional[float] = None,
                    optimisation_goal: Optional[str] = None,
                    updated_by: str = "user") -> dict[str, Any]:
    current = settings(station_id)
    if current_mode is not None:
        current["current_mode"] = normalize_mode(current_mode)
    if optimisation_goal is not None:
        current["optimisation_goal"] = normalize_goal(optimisation_goal)
    if battery_reserve_pct is not None:
        current["battery_reserve_pct"] = require_reserve(
            station_id, battery_reserve_pct, check_spec=True)
    current["strategy_status"] = "active"           # active in the simulation only
    current["updated_at"] = envelope.generated_at()
    current["updated_by"] = updated_by
    with _lock:
        _settings[station_id] = current
    return dict(current)


# ---------------------------------------------------------------------------
# Simulated strategies
# ---------------------------------------------------------------------------
def save_simulation(station_id: str, record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        _simulations[station_id] = dict(record)
    return dict(record)


def latest_simulation(station_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        record = _simulations.get(station_id)
        return dict(record) if record else None


def reset() -> None:
    """Drop all state (tests / demo reset)."""
    with _lock:
        _settings.clear()
        _simulations.clear()
