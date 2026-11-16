import test from "node:test";
import assert from "node:assert/strict";
import {
  ACTION_LABELS,
  GOAL_LABELS,
  MODE_OPTIONS,
  benefitLabel,
  clockLabel,
  planCard,
  simulationStrategy,
} from "./strategy.js";

const proposal = {
  strategy_id: "prop_strat_abc12345",
  type: "export",
  mode_name: "save_money",
  start_at: "2026-11-16T14:05:00+10:30",
  end_at: "2026-11-16T14:35:00+10:30",
  battery_reserve_pct: 40,
  optimisation_goal: "maximise_export_revenue",
  estimated_benefit_aud: 0.0416,
  reason: "Sell into the evening spike.",
  floor_min_pct: 0,
  mode: "live",
};

test("simulationStrategy forwards only the five sandbox fields", () => {
  const body = simulationStrategy(proposal);
  assert.deepEqual(Object.keys(body).sort(), [
    "battery_reserve_pct",
    "end_at",
    "optimisation_goal",
    "start_at",
    "type",
  ]);
  assert.equal(body.type, "export");
  assert.equal(body.battery_reserve_pct, 40);
  assert.equal(body.start_at, proposal.start_at);
});

test("planCard labels the proposal in the owner's words", () => {
  const card = planCard(proposal);
  assert.equal(card.action, ACTION_LABELS.export);
  assert.equal(card.window, "2:05 PM – 2:35 PM");
  assert.equal(card.mode, "Smart Savings");
  assert.equal(card.reserve, "40%");
  assert.equal(card.benefit, "4.2¢");
  assert.equal(card.reason, "Sell into the evening spike.");
  assert.equal(planCard(null), null);
});

test("benefitLabel keeps a tiny window benefit visible", () => {
  assert.equal(benefitLabel(null), "—");
  assert.equal(benefitLabel(0.00162), "0.2¢");
  assert.equal(benefitLabel(-0.00162), "-0.2¢");
  assert.equal(benefitLabel(2.4), "$2.40");
});

test("clockLabel handles midnight, noon and junk", () => {
  assert.equal(clockLabel("2026-11-16T00:00:00+10:30"), "12:00 AM");
  assert.equal(clockLabel("2026-11-16T12:05:00+10:30"), "12:05 PM");
  assert.equal(clockLabel(undefined), "—");
});

test("the settings panel offers the three owner modes and three goals", () => {
  assert.deepEqual(
    MODE_OPTIONS.map(({ value }) => value),
    ["save_money", "balanced", "save_energy"],
  );
  assert.ok(MODE_OPTIONS.every(({ label }) => Boolean(label)));
  assert.deepEqual(Object.keys(GOAL_LABELS), [
    "minimise_cost",
    "maximise_export_revenue",
    "maximise_self_consumption",
  ]);
});
