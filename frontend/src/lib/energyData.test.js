import assert from "node:assert/strict";
import test from "node:test";
import {
  MODE_LABELS,
  activeFlowIds,
  adelaideToday,
  centsPerKwh,
  formatAud,
  formatNumber,
  hourlySeries,
  priceSeries,
} from "./energyData.js";

const EDGE_DIRECTIONS = {
  "solar-home": "solar_to_home",
  "solar-battery": "solar_to_battery",
  "battery-home": "battery_to_home",
};

const slot = (hour, minute) =>
  `2026-09-16T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00+09:30`;

test("a full day becomes 24 hourly averages in kW", () => {
  const points = Array.from({ length: 288 }, (_, index) => ({
    timestamp: slot(Math.floor(index / 12), (index % 12) * 5),
    pv_w: 12000,
    load_w: 0,
    battery_w: null,
    grid_w: 3000,
  }));

  const series = hourlySeries(points);

  assert.equal(series.labels.length, 24);
  assert.equal(series.labels[0], "12 AM");
  assert.equal(series.labels[18], "6 PM");
  assert.deepEqual(series.values[0], Array(24).fill(12));
  assert.deepEqual(series.values[2], Array(24).fill(null));
});

test("hours without readings stay empty instead of shifting the series", () => {
  const series = hourlySeries([
    { timestamp: slot(0, 5), pv_w: 1000 },
    { timestamp: slot(2, 5), pv_w: 3000 },
    { timestamp: "not-a-timestamp", pv_w: 9000 },
  ]);

  assert.deepEqual(series.values[0].slice(0, 4), [1, null, 3, null]);
});

test("market price history aggregates per local hour in c/kWh", () => {
  const series = priceSeries([
    { timestamp: slot(9, 5), price_aud_per_kwh: -0.02 },
    { timestamp: slot(9, 10), price_aud_per_kwh: -0.04 },
    { timestamp: slot(10, 0), price_aud_per_kwh: 0.1 },
    { timestamp: slot(11, 0), price_aud_per_kwh: null },
    { timestamp: "not-a-timestamp", price_aud_per_kwh: 9 },
  ]);

  assert.equal(series.unit, "c/kWh");
  assert.equal(series.labels.length, 24);
  assert.equal(series.labels[9], "9 AM");
  assert.equal(series.values[9], -3);
  assert.equal(series.values[10], 10);
  assert.equal(series.values[11], null);
});

test("formatting helpers keep null as a dash and convert AUD/kWh to c/kWh", () => {
  assert.equal(formatNumber(null), "—");
  assert.equal(formatNumber(4.567), "4.6");
  assert.equal(formatNumber(82.4, 0), "82");
  assert.equal(centsPerKwh(null), null);
  assert.equal(formatNumber(centsPerKwh(0.124)), "12.4");
});

test("AUD amounts keep their sign and a missing amount stays a dash", () => {
  assert.equal(formatAud(null), "—");
  assert.equal(formatAud(4.823), "$4.82");
  assert.equal(formatAud(-0.284), "-$0.28");
  assert.equal(MODE_LABELS.save_money, "Smart Savings");
  assert.equal(MODE_LABELS.save_energy, "Save Energy");
});

test("adelaideToday is an Adelaide calendar date", () => {
  assert.equal(
    adelaideToday(new Date("2026-09-16T14:30:00Z")),
    "2026-09-17",
  );
});

test("flow activity: unknown directions animate all, an empty slot animates none", () => {
  const all = Object.keys(EDGE_DIRECTIONS);
  assert.deepEqual(activeFlowIds(undefined, EDGE_DIRECTIONS), all);
  assert.deepEqual(activeFlowIds([], EDGE_DIRECTIONS), []);
  assert.deepEqual(activeFlowIds(["solar_to_battery"], EDGE_DIRECTIONS), [
    "solar-battery",
  ]);
});
