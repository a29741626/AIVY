"""Self-checks for the P1 services (stdlib unittest, real CSVs, no fixtures).

Run from the repo root:  python backend/tests/test_p1_services.py
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
import pandas as pd
import battery
import strategy
from services import data_adapter, replay_clock, state_store, strategy_engine
from services.errors import ApiError

ADL = ZoneInfo(config.TIMEZONE)
NOW = datetime(2026, 11, 16, 10, 3, tzinfo=ADL)   # fixed clock: slot 120, 09:55 completed
TODAY = NOW.date()
STATION = "SBX-001"


def _plan_payload(mode="simulation", **strategy):
    spec = {"type": "battery_optimisation", "optimisation_goal": "maximise_export_revenue",
            "battery_reserve_pct": 20, "start_at": "2026-09-10T00:00:00",
            "end_at": "2026-09-10T23:55:00"}
    spec.update(strategy)
    return {"mode": mode, "strategy": spec}


class EnergyMathTests(unittest.TestCase):
    def test_cost_breakdown_watts_over_five_minutes(self):
        sim = pd.DataFrame({"grid": [-12000.0, 12000.0, 0.0], "price": [30.0, 30.0, 5.0]})
        costs = strategy_engine.cost_breakdown(sim, 3)
        self.assertAlmostEqual(costs["import_kwh"], 1.0)
        self.assertAlmostEqual(costs["export_kwh"], 1.0)
        self.assertAlmostEqual(costs["import_cost_aud"], 0.30)
        self.assertAlmostEqual(costs["export_earnings_aud"], 0.30)
        self.assertAlmostEqual(costs["net_cost_aud"], 0.0)

    def test_cost_breakdown_truncates_to_completed_slots(self):
        sim = pd.DataFrame({"grid": [-12000.0] * 10, "price": [100.0] * 10})
        partial = strategy_engine.cost_breakdown(sim, 4)
        self.assertEqual(partial["intervals"], 4)
        self.assertAlmostEqual(partial["import_cost_aud"], 4.0)   # 4 x 1 kWh x 100 c/kWh
        self.assertEqual(strategy_engine.cost_breakdown(sim, 0)["intervals"], 0)
        self.assertEqual(strategy_engine.cost_breakdown(sim, 99)["intervals"], 10)

    def test_missing_price_is_not_a_free_slot(self):
        sim = pd.DataFrame({"grid": [-12000.0, -12000.0], "price": [float("nan"), 30.0]})
        costs = strategy_engine.cost_breakdown(sim, 2)
        self.assertAlmostEqual(costs["import_cost_aud"], 0.30)     # only the priced slot counts
        self.assertEqual(costs["valid_price_intervals"], 1)
        self.assertEqual(costs["import_kwh"], 2.0)                 # energy itself is still counted


class ActionTests(unittest.TestCase):
    def test_action_follows_sign_with_50w_deadband(self):
        self.assertEqual(strategy_engine.action_for(51, 0), "charge")
        self.assertEqual(strategy_engine.action_for(50, 0), "hold")
        self.assertEqual(strategy_engine.action_for(-50, -900), "hold")
        self.assertEqual(strategy_engine.action_for(-51, -400), "discharge")
        self.assertEqual(strategy_engine.action_for(-51, 400), "export")
        self.assertEqual(strategy_engine.action_for(None, None), "hold")


class FinancialSummaryTests(unittest.TestCase):
    def test_today_is_truncated_to_completed_slots(self):
        snap = strategy_engine.financial_summary(STATION, TODAY, NOW)
        expected = replay_clock.completed_points(NOW)
        self.assertEqual(snap.data["coverage"]["expected_intervals_to_now"], expected)
        self.assertEqual(snap.data["period"]["intervals"], expected)
        self.assertEqual(snap.source["mode"], "replay")
        self.assertEqual(snap.data["period"]["end_at"], "2026-11-16T09:55:00+10:30")
        self.assertEqual(snap.data["baseline"]["status"], "available")
        self.assertEqual(snap.data["baseline_costs"]["net_cost_aud"],
                         snap.data["baseline_net_cost_aud"])
        self.assertAlmostEqual(
            snap.data["today_savings_aud"],
            snap.data["baseline_net_cost_aud"] - snap.data["actual_net_cost_aud"], places=6)
        self.assertTrue(any(w["code"] == "PARTIAL_DAY" for w in snap.warnings))

    def test_past_day_is_a_full_demo_day(self):
        snap = strategy_engine.financial_summary(STATION, date(2026, 9, 10), NOW)
        self.assertEqual(snap.source["mode"], "demo")
        self.assertEqual(snap.data["period"]["intervals"], config.SLOTS_PER_DAY)
        self.assertFalse(snap.data["period"]["is_partial"])
        self.assertIsNotNone(snap.data["today_savings_aud"])

    def test_future_day_is_a_400(self):
        with self.assertRaises(ApiError) as ctx:
            strategy_engine.financial_summary(STATION, TODAY + timedelta(days=1), NOW)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "INVALID_DATE")

    def test_baseline_failure_never_fabricates_savings(self):
        original = strategy_engine.run_day

        def failing(station_id, strategy_name, *args, **kwargs):
            if strategy_name == "baseline":
                raise RuntimeError("no baseline today")
            return original(station_id, strategy_name, *args, **kwargs)

        strategy_engine.run_day = failing
        try:
            snap = strategy_engine.financial_summary(STATION, TODAY, NOW)
        finally:
            strategy_engine.run_day = original
        self.assertEqual(snap.data["baseline"]["status"], "failed")
        self.assertIsNone(snap.data["baseline_net_cost_aud"])
        self.assertIsNone(snap.data["today_savings_aud"])
        self.assertTrue(any(w["code"] == "BASELINE_FAILED" for w in snap.warnings))


class BatteryDetailTests(unittest.TestCase):
    def test_matches_overview_and_spec(self):
        snap = strategy_engine.battery_detail(STATION, NOW)
        overview = data_adapter.overview(STATION, NOW)
        spec = battery.SPECS[STATION]
        self.assertEqual(snap.data["timestamp"], overview.data_timestamp)
        self.assertEqual(snap.data["soc_pct"], overview.data["telemetry"]["soc_pct"])
        self.assertEqual(snap.data["state"], overview.data["states"]["battery"])
        self.assertEqual(snap.data["power_kw"], overview.data["display"]["battery_power_kw"])
        self.assertEqual(snap.data["capacity_kwh"], spec["capacity_kwh"])
        self.assertEqual(snap.data["max_charge_power_kw"], round(spec["max_power_w"] / 1000.0, 3))
        self.assertEqual(snap.data["reserve_pct"],
                         state_store.settings(STATION)["battery_reserve_pct"])

    def test_time_to_full_only_while_charging(self):
        data = strategy_engine.battery_detail(STATION, NOW).data
        if data["state"] == "charging":
            self.assertEqual(data["estimate_quality"], "high")
            expected = (((1 - data["soc_pct"] / 100.0) * data["capacity_kwh"])
                        / (data["power_kw"] * battery.SPECS[STATION]["eff"]) * 60.0)
            self.assertAlmostEqual(data["estimated_time_to_full_min"], expected, delta=0.06)
            self.assertGreater(data["estimated_time_to_full_min"], 0)
        else:
            self.assertIsNone(data["estimated_time_to_full_min"])
            self.assertEqual(data["estimate_quality"], "unavailable")


class RecommendationTests(unittest.TestCase):
    def test_plan_slot_drives_the_action(self):
        settings = state_store.settings(STATION)
        _, sim = strategy_engine.run_day(STATION, settings["current_mode"],
                                         data_adapter.replay_day(STATION),
                                         settings["battery_reserve_pct"],
                                         settings["optimisation_goal"])
        row = sim.iloc[replay_clock.current_slot(NOW)]
        data = strategy_engine.recommendation(STATION, NOW).data
        self.assertEqual(data["action"],
                         strategy_engine.action_for(float(row["battery"]), float(row["grid"])))
        self.assertEqual(data["planned_battery_w"], round(float(row["battery"]), 1))
        self.assertEqual(data["soc_pct"], round(float(row["soc"]), 1))
        self.assertEqual(data["recommendation_id"], "rec_20261116_1000_SBX-001")
        self.assertEqual(data["window"]["start_at"], "2026-11-16T10:00:00+10:30")
        self.assertEqual(data["window"]["end_at"], "2026-11-16T10:30:00+10:30")
        self.assertEqual(data["model_version"], strategy_engine.MODEL_VERSION)
        self.assertEqual(data["status"], "draft")
        self.assertIsNotNone(data["estimated_savings_aud"])
        self.assertTrue(data["text"] and data["reason"])


class SimulationTests(unittest.TestCase):
    def setUp(self):
        state_store.reset()

    def test_simulation_is_never_a_device_command(self):
        data = strategy_engine.simulate(STATION, _plan_payload(), NOW).data
        self.assertEqual(data["mode"], "simulation")
        self.assertEqual(data["status"], "applied_in_simulation")
        self.assertFalse(data["device_control_attempted"])
        self.assertTrue(data["strategy_id"].startswith("sim_strat_"))
        self.assertEqual(data["replay_date"], "2026-09-10")
        self.assertIsNotNone(data["simulated_net_cost_aud"])
        self.assertIn("No physical device command", data["message"])
        self.assertEqual(state_store.latest_simulation(STATION)["strategy_id"], data["strategy_id"])

    def test_goal_selects_the_strategy(self):
        for goal, expected in (("minimise_cost", "save_money"),
                               ("maximise_export_revenue", "save_money"),
                               ("maximise_self_consumption", "save_energy")):
            snap = strategy_engine.simulate(STATION, _plan_payload(optimisation_goal=goal), NOW)
            self.assertEqual(snap.data["optimisation_goal"], goal)
            self.assertEqual(strategy_engine.SIM_STRATEGY[goal], expected)

    def test_live_mode_is_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            strategy_engine.simulate(STATION, _plan_payload(mode="live"), NOW)
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (400, "VALIDATION_ERROR"))

    def test_only_the_requested_window_is_costed(self):
        _, sim = strategy_engine.run_day(STATION, "save_money", "2026-09-10", 20.0, "minimise_cost")
        data = strategy_engine.simulate(
            STATION, _plan_payload(optimisation_goal="minimise_cost",
                                   start_at="2026-09-10T12:00:00",
                                   end_at="2026-09-10T13:00:00"), NOW).data
        window = strategy_engine.cost_breakdown(sim.iloc[144:157], 13)   # 12:00 .. 13:00
        self.assertAlmostEqual(data["simulated_net_cost_aud"], window["net_cost_aud"], places=6)
        self.assertEqual(data["valid_from"], "2026-09-10T12:00:00+09:30")
        self.assertEqual(data["valid_to"], "2026-09-10T13:00:00+09:30")

    def test_bad_requests_are_400(self):
        cases = (
            _plan_payload(optimisation_goal="make_coffee"),          # unknown goal
            _plan_payload(battery_reserve_pct=150),                  # outside 0..100
            _plan_payload(start_at="10/09/2026"),                    # not ISO 8601
            _plan_payload(end_at="2026-09-09T23:55:00"),             # ends before it starts
            _plan_payload(end_at="2026-09-11T00:05:00"),             # crosses midnight
            {"mode": "simulation", "strategy": {"type": "", "optimisation_goal": "minimise_cost",
                                                "battery_reserve_pct": 20,
                                                "start_at": "2026-09-10T00:00:00",
                                                "end_at": "2026-09-10T23:55:00"}},
        )
        for payload in cases:
            with self.assertRaises(ApiError) as ctx:
                strategy_engine.simulate(STATION, payload, NOW)
            self.assertEqual(ctx.exception.status_code, 400, payload)
            self.assertEqual(ctx.exception.code, "VALIDATION_ERROR", payload)

    def test_future_simulation_date_is_invalid(self):
        with self.assertRaises(ApiError) as ctx:
            strategy_engine.simulate(STATION, _plan_payload(start_at="2099-01-01T00:00:00",
                                                            end_at="2099-01-01T23:55:00"), NOW)
        self.assertEqual(ctx.exception.code, "INVALID_DATE")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        state_store.reset()

    def test_defaults_come_from_the_balanced_mode(self):
        snap = strategy_engine.current_strategy(STATION)
        self.assertEqual(snap.data["settings"]["current_mode"], state_store.DEFAULT_MODE)
        self.assertEqual(snap.data["settings"]["battery_reserve_pct"],
                         float(strategy.MODES[state_store.DEFAULT_MODE]["floor_min_pct"]))
        self.assertIsNone(snap.data["latest_simulation"])
        self.assertEqual(snap.data_status, "static")

    def test_smart_savings_alias_and_reserve_roundtrip(self):
        snap = strategy_engine.update_strategy_settings(
            STATION, {"current_mode": "smart_savings", "battery_reserve_pct": 25.0,
                      "optimisation_goal": "maximise_self_consumption"})
        self.assertEqual(snap.data["settings"]["current_mode"], "save_money")
        self.assertEqual(snap.data["settings"]["battery_reserve_pct"], 25.0)
        self.assertEqual(strategy_engine.current_strategy(STATION)
                         .data["settings"]["optimisation_goal"], "maximise_self_consumption")

    def test_reserve_outside_the_spec_is_rejected(self):
        for bad in (battery.SPECS[STATION]["soc_min"] - 1, 101):
            with self.assertRaises(ApiError) as ctx:
                strategy_engine.update_strategy_settings(STATION, {"battery_reserve_pct": bad})
            self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(state_store.settings(STATION)["battery_reserve_pct"],
                         float(strategy.MODES[state_store.DEFAULT_MODE]["floor_min_pct"]))

    def test_unknown_mode_and_goal_are_rejected(self):
        for payload in ({"current_mode": "turbo"}, {"optimisation_goal": "make_coffee"}):
            with self.assertRaises(ApiError) as ctx:
                strategy_engine.update_strategy_settings(STATION, payload)
            self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")


class ApiTests(unittest.TestCase):
    """The six P1 routes through FastAPI: envelope shape + the error cases."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        import main
        state_store.reset()
        cls.client = TestClient(main.app)

    def tearDown(self):
        state_store.reset()

    def _ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["meta"]["station_id"], STATION)
        self.assertEqual(body["meta"]["schema_version"], config.SCHEMA_VERSION)
        return body

    def test_battery_route(self):
        body = self._ok(self.client.get(f"/api/stations/{STATION}/battery"))
        self.assertEqual(body["data"]["station_id"], STATION)

    def test_financial_summary_route(self):
        today = datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
        body = self._ok(self.client.get(f"/api/stations/{STATION}/financial-summary?date={today}"))
        self.assertEqual(body["data"]["date"], today)
        self.assertIn(body["data"]["baseline"]["status"], {"available", "failed"})

    def test_recommendation_route(self):
        body = self._ok(self.client.get(f"/api/stations/{STATION}/recommendation"))
        self.assertIn(body["data"]["action"], {"charge", "discharge", "export", "hold"})

    def test_simulation_route(self):
        body = self._ok(self.client.post(f"/api/stations/{STATION}/simulate", json=_plan_payload()))
        self.assertFalse(body["data"]["device_control_attempted"])
        self.assertEqual(body["meta"]["data_status"], "simulation")

    def test_strategy_routes(self):
        body = self._ok(self.client.get(f"/api/stations/{STATION}/strategy"))
        self.assertIsNone(body["data"]["latest_simulation"])
        body = self._ok(self.client.put(f"/api/stations/{STATION}/strategy",
                                        json={"current_mode": "save_energy"}))
        self.assertEqual(body["data"]["settings"]["current_mode"], "save_energy")

    def test_error_envelopes(self):
        cases = (
            (self.client.get("/api/stations/SBX-999/battery"), 404, "STATION_NOT_FOUND"),
            (self.client.get(f"/api/stations/{STATION}/financial-summary?date=2099-01-01"), 400, "INVALID_DATE"),
            (self.client.get(f"/api/stations/{STATION}/financial-summary?date=16%2F11%2F2026"), 400, "INVALID_DATE"),
            (self.client.post(f"/api/stations/{STATION}/simulate", json=_plan_payload(mode="live")), 400, "VALIDATION_ERROR"),
            (self.client.post(f"/api/stations/{STATION}/simulate",
                              json=_plan_payload(battery_reserve_pct=150)), 400, "VALIDATION_ERROR"),
            (self.client.put(f"/api/stations/{STATION}/strategy",
                             json={"battery_reserve_pct": 5}), 400, "VALIDATION_ERROR"),
            (self.client.put(f"/api/stations/{STATION}/strategy", json={"current_mode": "turbo"}), 400, "VALIDATION_ERROR"),
            (self.client.post(f"/api/stations/{STATION}/simulate", json={"mode": "simulation"}), 422, "VALIDATION_ERROR"),
        )
        for response, status_code, code in cases:
            self.assertEqual(response.status_code, status_code, response.text)
            self.assertEqual(response.json()["error"]["code"], code)
            self.assertIn("X-Request-ID", response.headers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
