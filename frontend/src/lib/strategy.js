import { MODE_LABELS, formatAud } from "./energyData.js";

export const MODE_OPTIONS = [
  { value: "save_money", label: MODE_LABELS.save_money },
  { value: "balanced", label: MODE_LABELS.balanced },
  { value: "save_energy", label: MODE_LABELS.save_energy },
];

export const GOAL_LABELS = {
  minimise_cost: "Minimise electricity cost",
  maximise_export_revenue: "Maximise export revenue",
  maximise_self_consumption: "Maximise self consumption",
};

export const ACTION_LABELS = {
  charge: "Charge the battery",
  discharge: "Run the house from the battery",
  export: "Export from the battery",
  hold: "Hold the battery",
};

/** "...T14:05:00+10:30" -> "2:05 PM" (the API already sends Adelaide wall time). */
export function clockLabel(iso) {
  const match = /T(\d{2}):(\d{2})/.exec(iso ?? "");
  if (!match) return "—";
  const hour = Number(match[1]);
  return `${hour % 12 || 12}:${match[2]} ${hour < 12 ? "AM" : "PM"}`;
}

/** A 30-minute window's benefit: cents below 10c, dollars above (one card, two units). */
export function benefitLabel(amount) {
  if (amount == null) return "—";
  return Math.abs(amount) < 0.1
    ? `${(amount * 100).toFixed(1)}¢`
    : formatAud(amount);
}

/** Only the fields POST /simulate accepts; anything extra on the proposal is dropped. */
export function simulationStrategy(proposal) {
  return {
    type: proposal.type,
    optimisation_goal: proposal.optimisation_goal,
    battery_reserve_pct: proposal.battery_reserve_pct,
    start_at: proposal.start_at,
    end_at: proposal.end_at,
  };
}

/** Proposal -> what the strategy card shows, or null when there is no proposal. */
export function planCard(proposal) {
  if (!proposal) return null;
  return {
    action: ACTION_LABELS[proposal.type] ?? ACTION_LABELS.hold,
    window: `${clockLabel(proposal.start_at)} – ${clockLabel(proposal.end_at)}`,
    mode: MODE_LABELS[proposal.mode_name] ?? proposal.mode_name,
    reserve: `${Math.round(proposal.battery_reserve_pct)}%`,
    benefit: benefitLabel(proposal.estimated_benefit_aud),
    reason: proposal.reason,
  };
}
