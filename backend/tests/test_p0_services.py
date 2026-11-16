"""Self-checks for the P0 services (stdlib unittest, real CSVs, no fixtures).

Run from the repo root:  python backend/tests/test_p0_services.py
"""

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config  # noqa: E402  (puts strategy/codes on sys.path before battery/forecast)
import battery
from services import data_adapter, replay_clock
from services.errors import ApiError

ADL = ZoneInfo(config.TIMEZONE)
NOW = datetime(2026, 11, 16, 10, 3, tzinfo=ADL)   # fixed clock: slot 120, 09:55 completed
TODAY = NOW.date()


class ReplayClockTests(unittest.TestCase):
    def test_slot_mapping(self):
        self.assertEqual(replay_clock.current_slot(datetime(2026, 11, 16, 0, 4, tzinfo=ADL)), 0)
        self.assertEqual(replay_clock.current_slot(datetime(2026, 11, 16, 14, 37, tzinfo=ADL)), 175)
        self.assertEqual(replay_clock.current_slot(datetime(2026, 11, 16, 23, 59, tzinfo=ADL)), 287)
        self.assertEqual(replay_clock.completed_points(NOW), 120)

    def test_slot_start_is_adelaide_wall_clock(self):
        self.assertEqual(replay_clock.slot_start(TODAY, 119).isoformat(),
                         "2026-11-16T09:55:00+10:30")
        self.assertEqual(replay_clock.slot_start(TODAY, 0).isoformat(),
                         "2026-11-16T00:00:00+10:30")

    def test_parse_date_rejects_bad_values(self):
        for bad in ("2026-13-01", "16/11/2026", "20261116", ""):
            with self.assertRaises(ApiError) as ctx:
                replay_clock.parse_date(bad)
            self.assertEqual(ctx.exception.code, "INVALID_DATE")
        self.assertEqual(replay_clock.parse_date("2026-11-16"), TODAY)


class SignConventionTests(unittest.TestCase):
    def test_deadband_boundaries(self):
        self.assertEqual(data_adapter.battery_state(50), "idle")
        self.assertEqual(data_adapter.battery_state(51), "charging")
        self.assertEqual(data_adapter.battery_state(-50), "idle")
        self.assertEqual(data_adapter.battery_state(-51), "discharging")
        self.assertEqual(data_adapter.battery_state(None), "unknown")
        self.assertEqual(data_adapter.grid_state(50), "neutral")
        self.assertEqual(data_adapter.grid_state(51), "exporting")
        self.assertEqual(data_adapter.grid_state(-51), "importing")
        self.assertEqual(data_adapter.grid_state(None), "unknown")

    def test_recorded_rows_balance(self):
        frame = data_adapter.station_days("SBX-001")[data_adapter.replay_day("SBX-001")]
        row = frame.dropna().iloc[0]
        self.assertAlmostEqual(row["grid_w"], row["pv_w"] - row["load_w"] - row["battery_w"], delta=1.0)

    def test_flow_directions_follow_signs(self):
        directions = data_adapter.flow_directions(
            {"pv_w": 3000, "load_w": 500, "battery_w": 1000, "grid_w": 1500, "soc_pct": 50})
        self.assertEqual(directions, ["solar_to_home", "solar_to_battery", "solar_to_grid"])
        self.assertEqual(data_adapter.flow_directions(
            {"pv_w": 0, "load_w": 800, "battery_w": -800, "grid_w": 0, "soc_pct": 50}),
            ["battery_to_home"])

    def test_kw_helper(self):
        self.assertEqual(data_adapter.kw(-2500), -2.5)   # helper keeps sign; overview takes abs()
        self.assertEqual(data_adapter.kw(500), 0.5)
        self.assertIsNone(data_adapter.kw(None))

    def test_inverted_recordings_are_flipped_onto_the_charging_sign(self):
        """SBX-002/003 were recorded with the discharge sign; the SOC is the judge."""
        for station in config.STATIONS:
            frame = data_adapter.station_days(station)[data_adapter.replay_day(station)]
            agreement = float((frame["battery_w"].fillna(0.0) * frame["soc_pct"].diff().fillna(0.0)).sum())
            self.assertGreaterEqual(agreement, 0, f"{station} still reads the discharge sign")

    def test_home_usage_is_never_negative(self):
        for station in config.STATIONS:
            display = data_adapter.overview(station, NOW).data["display"]
            self.assertIsNotNone(display["home_usage_kw"])
            self.assertGreaterEqual(display["home_usage_kw"], 0.0, station)


class HybridSourceTests(unittest.TestCase):
    def test_today_is_truncated_to_completed_slots(self):
        sl = data_adapter.day_slice("SBX-001", TODAY, NOW)
        self.assertEqual(sl.mode, "replay")
        self.assertEqual(len(sl.points), 120)                       # 10:03 -> slots 0..119
        self.assertEqual(sl.points[0]["timestamp"], "2026-11-16T00:00:00+10:30")
        self.assertEqual(sl.points[-1]["timestamp"], "2026-11-16T09:55:00+10:30")
        self.assertTrue(all(p["timestamp"].startswith("2026-11-16") for p in sl.points))
        self.assertTrue(sl.is_partial)
        self.assertEqual(sl.expected_points, 288)
        self.assertEqual(sl.received_points, 120 - len(sl.missing_intervals))
        self.assertEqual(sl.replay_date, data_adapter.replay_day("SBX-001"))

    def test_replay_day_is_gap_free_for_all_stations(self):
        for sid in config.STATIONS:
            sl = data_adapter.day_slice(sid, TODAY, NOW)
            self.assertEqual(len(sl.points), 120, sid)
            self.assertEqual(sl.missing_intervals, [], sid)
            self.assertEqual(sl.received_points, 120, sid)

    def test_today_never_returns_a_future_timestamp(self):
        sl = data_adapter.day_slice("SBX-001", TODAY, NOW)
        last = datetime.fromisoformat(sl.points[-1]["timestamp"])
        self.assertLess(last, NOW)
        self.assertLessEqual(len(sl.points), replay_clock.current_slot(NOW))

    def test_future_date_is_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            data_adapter.day_slice("SBX-001", TODAY + timedelta(days=1), NOW)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "INVALID_DATE")

    def test_past_recorded_day_is_a_full_demo_day(self):
        past = [d for d in data_adapter.complete_days("SBX-001") if d < TODAY.isoformat()]
        sl = data_adapter.day_slice("SBX-001", date.fromisoformat(past[-1]), NOW)
        self.assertEqual(sl.mode, "demo")
        self.assertEqual(len(sl.points), 288)
        self.assertEqual(sl.completeness_pct, 100.0)
        self.assertIsNone(sl.mapped_from)
        self.assertFalse(sl.is_partial)

    def test_out_of_range_date_maps_deterministically(self):
        target = date(2020, 1, 1)
        first = data_adapter.day_slice("SBX-001", target, NOW)
        second = data_adapter.day_slice("SBX-001", target, NOW)
        self.assertEqual(first.mode, "demo")
        self.assertEqual(len(first.points), 288)
        self.assertEqual(first.mapped_from, second.mapped_from)
        self.assertIn(first.mapped_from, data_adapter.complete_days("SBX-001"))
        self.assertLess(first.mapped_from, TODAY.isoformat())      # past maps to past
        self.assertEqual([p["timestamp"] for p in first.points],
                         [p["timestamp"] for p in second.points])
        self.assertTrue(all(p["timestamp"].startswith("2020-01-01") for p in first.points))

    def test_missing_readings_stay_null(self):
        days = data_adapter.station_days("SBX-001")
        target = next(day for day, frame in days.items()
                      if day < TODAY.isoformat()
                      and frame[list(data_adapter.TELEMETRY)].isna().all(axis=1).any())
        sl = data_adapter.day_slice("SBX-001", date.fromisoformat(target), NOW)
        missing = [p for p in sl.points if p["quality"] == "missing"]
        self.assertTrue(missing)
        self.assertTrue(any(p["pv_w"] is None and p["grid_w"] is None for p in missing))
        self.assertTrue(any(w["code"] == "MISSING_DATA" for w in sl.warnings))
        self.assertEqual(len(sl.missing_intervals), len(missing))
        self.assertLess(sl.received_points, 288)


class SnapshotTests(unittest.TestCase):
    def test_overview_uses_last_completed_slot(self):
        snap = data_adapter.overview("SBX-001", NOW)
        self.assertEqual(snap.data_timestamp, "2026-11-16T09:55:00+10:30")
        self.assertEqual(snap.data["timestamp"], snap.data_timestamp)
        self.assertEqual(snap.data["states"]["system"], "online")
        self.assertLessEqual(snap.freshness_seconds, 600)
        self.assertEqual(snap.data["flow"]["mode"], "direction_only")
        self.assertEqual(set(snap.data["telemetry"]), set(data_adapter.TELEMETRY))
        for key in ("solar_power_kw", "home_usage_kw", "battery_power_kw", "grid_power_kw"):
            value = snap.data["display"][key]
            self.assertTrue(value is None or value >= 0, key)

    def test_market_converts_cents_to_aud(self):
        snap = data_adapter.market("SBX-001", NOW)
        slot = replay_clock.current_slot(NOW)
        real_day = data_adapter.real_date("SBX-001", data_adapter.replay_day("SBX-001"))
        raw = data_adapter.price_c_per_kwh(real_day, slot)
        self.assertIsNotNone(raw)
        quote = snap.data["price"]["import"]
        self.assertAlmostEqual(quote["value"], raw / 100.0, places=6)
        self.assertEqual(quote["unit"], "AUD_per_kWh")
        self.assertEqual(quote["price_type"], "wholesale_spot")
        self.assertEqual(snap.data["price"]["feed_in"]["value"], quote["value"])
        self.assertEqual(snap.data["slot"], slot)
        self.assertEqual(quote["valid_from"], "2026-11-16T10:00:00+10:30")
        self.assertEqual(quote["valid_to"], "2026-11-16T10:05:00+10:30")
        self.assertIn(snap.data["weather"]["condition"],
                      {"night", "clear", "partly_cloudy", "cloudy", "overcast", "unknown"})

    def test_station_catalogue_matches_strategy_specs(self):
        catalogue = data_adapter.stations()
        self.assertEqual([s["id"] for s in catalogue], list(config.STATIONS))
        for station in catalogue:
            self.assertEqual(station["capabilities"], data_adapter.CAPABILITIES)
            self.assertEqual(station["spec"]["capacity_kwh"],
                             battery.SPECS[station["id"]]["capacity_kwh"])
            self.assertEqual(station["spec"]["max_power_w"],
                             battery.SPECS[station["id"]]["max_power_w"])

    def test_unknown_station(self):
        with self.assertRaises(ApiError) as ctx:
            data_adapter.require_station("SBX-999")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.code, "STATION_NOT_FOUND")


if __name__ == "__main__":
    unittest.main(verbosity=2)
