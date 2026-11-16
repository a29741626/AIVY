"""Self-checks for the fault injection + health service (stdlib unittest, real CSVs).

Run from the repo root:  python backend/tests/test_fault_service.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config  # noqa: E402
from services import data_adapter, fault_injection, fault_service, replay_clock  # noqa: E402

ADL = ZoneInfo(config.TIMEZONE)
NOW = datetime(2026, 11, 16, 14, 3, tzinfo=ADL)   # past the 11:00-13:00 window: it is complete
TODAY = NOW.date()
FAULTY = "SBX-003"                                # the station fault_config.json targets
CLEAN = ("SBX-001", "SBX-002")


class InjectionTests(unittest.TestCase):
    def test_injected_slots_conserve_energy(self):
        sl = data_adapter.day_slice(FAULTY, TODAY, NOW)
        first, last = fault_injection.window(FAULTY, sl.mode)
        self.assertEqual((first, last), (132, 156), "11:00-13:00 on the 288-slot day")
        for point in sl.points[first:last]:
            self.assertEqual(point["pv_w"], 0.0, point["timestamp"])
            self.assertAlmostEqual(point["grid_w"],
                                   point["pv_w"] - point["load_w"] - point["battery_w"],
                                   delta=1.0, msg=point["timestamp"])

    def test_injected_slot_count_is_announced_in_warnings(self):
        sl = data_adapter.day_slice(FAULTY, TODAY, NOW)
        self.assertEqual(len(sl.points), 168)                    # 14:03 -> slots 0..167
        self.assertEqual(sl.injected_slots, 24)
        warn = next(w for w in sl.warnings if w["code"] == "FAULT_INJECTED")
        self.assertEqual(warn["count"], 24)
        self.assertIn("11:00-13:00", warn["message"])

    def test_other_stations_are_not_injected(self):
        for sid in CLEAN:
            self.assertEqual(data_adapter.day_slice(sid, TODAY, NOW).injected_slots, 0, sid)



    def test_config_reloads_without_restart(self):
        original_path, original_state = fault_injection.CONFIG_PATH, dict(fault_injection._state)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "fault_config.json"
                fault_injection.CONFIG_PATH = path
                fault_injection._state.update(mtime=None, config=fault_injection.DISABLED)

                row = {"enabled": True, "target_stations": ["sbx-003"],
                       "fault_type": "pv_zero", "start_hour": 9, "end_hour": 10}
                path.write_text(json.dumps(row), encoding="utf-8")
                os.utime(path, (1_000_000, 1_000_000))
                self.assertEqual(fault_injection.window("SBX-003", "replay"), (108, 120))

                row["start_hour"], row["end_hour"] = 11, 13
                path.write_text(json.dumps(row), encoding="utf-8")
                os.utime(path, (2_000_000, 2_000_000))            # mtime moves -> re-read
                self.assertEqual(fault_injection.window("SBX-003", "replay"), (132, 156))

                path.write_text("{not json", encoding="utf-8")
                os.utime(path, (3_000_000, 3_000_000))
                self.assertFalse(fault_injection.config_now()["enabled"])

                path.unlink()                                     # missing file -> disabled
                self.assertEqual(fault_injection.window("SBX-003", "replay"), (0, 0))
        finally:
            fault_injection.CONFIG_PATH = original_path
            fault_injection._state.update(mtime=None, config=fault_injection.DISABLED)
        # the real file is in use again
        self.assertEqual(fault_injection.window(FAULTY, "replay"), (132, 156))


class HealthTests(unittest.TestCase):
    def test_injected_station_is_flagged(self):
        data = fault_service.health(FAULTY, NOW).data
        self.assertIn(data["status"], ("sub_healthy", "broken"))
        self.assertIn("sudden_drop_to_zero", data["fault_types"])
        self.assertTrue(data["fault_injected"])
        self.assertEqual(data["fault_window"], "11:00-13:00")
        self.assertTrue(0 < data["soc_ceiling_pct"] <= 100)
        self.assertEqual(data["date"], TODAY.isoformat())

    def test_untouched_stations_stay_healthy(self):
        for sid in CLEAN:
            data = fault_service.health(sid, NOW).data
            self.assertEqual(data["status"], "healthy", sid)
            self.assertNotIn("sudden_drop_to_zero", data["fault_types"], sid)
            self.assertFalse(data["fault_injected"], sid)

    def test_health_refuses_a_day_with_no_completed_slot(self):
        midnight = datetime(2026, 11, 16, 0, 2, tzinfo=ADL)
        with self.assertRaises(Exception) as ctx:
            fault_service.health(FAULTY, midnight)
        self.assertEqual(getattr(ctx.exception, "code", None), "NO_DATA")


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from main import app
        cls.client = TestClient(app)

    def setUp(self):
        if not replay_clock.completed_points(replay_clock.now_adl()):
            self.skipTest("just after midnight: no completed slot to analyse today")

    def test_health_endpoint_serves_the_envelope(self):
        response = self.client.get(f"/api/stations/{FAULTY}/health")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["meta"]["schema_version"], config.SCHEMA_VERSION)
        self.assertEqual(body["meta"]["station_id"], FAULTY)
        self.assertIn("status", body["data"])

    def test_unknown_station_is_404(self):
        response = self.client.get("/api/stations/SBX-999/health")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "STATION_NOT_FOUND")


if __name__ == "__main__":
    unittest.main(verbosity=2)
