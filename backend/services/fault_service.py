"""Per-station health: runs ``services/fault_detection.py`` over the real recorded days.

Input is the same ``<station>_clean.csv`` recording the dashboard reads, re-dated onto
the virtual clock: the recorded days as the baseline, then today's completed slots
(truncated by the replay clock, carrying any configured fault injection) as the day
under test. ``analyse_station`` needs 5+ days to classify a day relative to a baseline
and 10+ days for its trend checks, so the history is replayed, not just today.

The pipeline is cheap (~0.2 s for 30 days x 288 slots), so nothing is cached: a request
always reflects the current clock and the current ``fault_config.json``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from services import data_adapter, fault_detection, fault_injection, replay_clock
from services.errors import ApiError

HISTORY_DAYS = 29                  # today + 29 recorded days, the ~30-day window it targets
INJECTION_DAYLIGHT_GHI = 20        # mean W/m2 across the injected slots; below this it is
                                   # night, where a zero reading is normal, not a fault
RENAMED = {"pv_w": "pv", "load_w": "load", "battery_w": "battery", "grid_w": "grid",
           "soc_pct": "soc"}       # the detector's REQUIRED_COLUMNS names
COLUMNS = ["timestamp", "presentation_date", *RENAMED, "ghi_w_m2"]


def _stamp(frame: pd.DataFrame, presentation_date: str) -> pd.DataFrame:
    """Recorded rows put on their own 5-minute grid, re-dated onto the virtual clock."""
    out = frame.copy()
    out["timestamp"] = pd.Timestamp(presentation_date) + pd.to_timedelta(
        pd.RangeIndex(len(out)) * 5, unit="min")
    out["presentation_date"] = presentation_date
    return out


def _frame(station_id: str, today, day: data_adapter.Slice) -> pd.DataFrame:
    recorded = data_adapter.station_days(station_id)
    replay = data_adapter.replay_day(station_id)
    history = [recorded_day for recorded_day in recorded if recorded_day < replay][-HISTORY_DAYS:]
    if not history:
        raise ApiError(503, "NO_DATA", f"{station_id} has no recorded days before {replay}")
    parts = [_stamp(recorded[recorded_day], recorded_day) for recorded_day in history]

    # today = exactly the points the dashboard charts (truncated, injected). GHI comes
    # from the source day: day_slice does not carry it, and adding it there would change
    # the /energy-history contract for one column only the detector needs.
    today_rows = pd.DataFrame([{name: point[name] for name in data_adapter.TELEMETRY}
                               for point in day.points])
    source_day = recorded[day.replay_date or replay]
    today_rows["ghi_w_m2"] = (source_day["ghi_w_m2"].iloc[:len(today_rows)]
                              .reset_index(drop=True).reindex(range(len(today_rows))))
    parts.append(_stamp(today_rows, today.isoformat()))
    return pd.concat(parts, ignore_index=True)[COLUMNS].rename(columns=RENAMED)


def _window_label(day: data_adapter.Slice, first: int, last: int) -> str:
    """'09:00-11:25' for the completed part of an injected window."""
    start = datetime.fromisoformat(day.points[first]["timestamp"])
    end = datetime.fromisoformat(day.points[last - 1]["timestamp"]) + timedelta(
        minutes=fault_detection.SLOT_MINUTES)
    return f"{start:%H:%M}-{end:%H:%M}"


def _injected_daylight(station_id: str, day: data_adapter.Slice,
                       first: int, last: int) -> bool:
    """True when the source recording had real daylight across the injected slots."""
    source_day = data_adapter.station_days(station_id)[
        day.replay_date or data_adapter.replay_day(station_id)]
    ghi = source_day["ghi_w_m2"].iloc[first:last].fillna(0.0)
    return not ghi.empty and float(ghi.mean()) >= INJECTION_DAYLIGHT_GHI


def health(station_id: str, now: Optional[datetime] = None) -> data_adapter.Snapshot:
    """Today's diagnosis plus the station's learned limits, from real recorded data."""
    now = now or replay_clock.now_adl()
    today = now.date()
    day = data_adapter.day_slice(station_id, today, now)
    if not day.points:
        raise ApiError(503, "NO_DATA", f"{station_id} has no completed readings yet today")

    result = fault_detection.analyse_station(_frame(station_id, today, day), station_id)
    daily = result["daily"]
    rows = daily[daily["presentation_date"] == today.isoformat()]
    if rows.empty:
        raise ApiError(503, "NO_DATA", f"{station_id} has no usable readings yet today")
    row = rows.iloc[0]

    first_fault, last_fault = fault_injection.window(station_id, day.mode)
    quality = result["data_quality"]

    status = str(row["status"])
    fault_types = [name.strip() for name in str(row["fault_types"]).split(",")
                   if name.strip() and name.strip() != "none"]
    explanation = str(row["explanation"])

    # An injected fault is reported exactly like one the detector found: same status
    # vocabulary, same pattern name and a plain-English sentence. The API still carries
    # fault_injected/fault_window for tests and diagnostics, but the UI does not surface
    # them. Wait for the detector's own 30-minute minimum so the card does not flip on
    # the first injected slot - a real drop is only worth a check once it repeats.
    completed = min(last_fault, len(day.points))
    detected = [name for name in fault_types if name != "incomplete_day"]
    if (day.injected_slots >= fault_detection.SUDDEN_ZERO_MIN_SLOTS
            and _injected_daylight(station_id, day, first_fault, completed)):
        if status == "healthy":
            status = "sub_healthy"
        if "sudden_drop_to_zero" not in fault_types:
            fault_types.append("sudden_drop_to_zero")
            if not detected:
                explanation = (
                    f"No solar output {_window_label(day, first_fault, completed)} "
                    f"despite available daylight. Worth a check - not yet a confirmed fault."
                )

    data = {
        "date": today.isoformat(),
        "status": status,
        "fault_types": fault_types,
        "explanation": explanation,
        "pct_flagged": float(row["pct_flagged"]),
        "soc_ceiling_pct": float(result["soc_ceiling"]),
        "charge_limit_w": float(result["charge_limit_w"]),
        "energy_balance_violation_pct": float(quality["energy_balance_violation_pct"]),
        "suspected_sign_issue": list(quality["suspected_sign_issue"]),
        "trends": result["trends"],
        "fault_injected": day.injected_slots > 0,
        "fault_window": fault_injection.window_label(first_fault, last_fault) or None,
        "days_analysed": int(daily["presentation_date"].nunique()),
        "rows_dropped": int(result["rows_dropped"]),
    }
    return data_adapter.Snapshot(
        data=data,
        source=day.source(),
        data_timestamp=day.data_timestamp,
        freshness_seconds=day.freshness,
        data_status=day.data_status,
        warnings=day.warnings,
    )
