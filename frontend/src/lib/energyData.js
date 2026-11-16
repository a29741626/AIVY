const SERIES_FIELDS = ["pv_w", "load_w", "battery_w", "grid_w"];

export const CONDITION_LABELS = {
  night: "Clear night",
  clear: "Sunny",
  partly_cloudy: "Partly cloudy",
  cloudy: "Cloudy",
  overcast: "Overcast",
  unknown: "Unknown",
};

export const BATTERY_STATE_LABELS = {
  charging: "Charging",
  discharging: "Discharging",
  idle: "Idle",
  unknown: "Unknown",
};

export const GRID_STATE_LABELS = {
  exporting: "Exporting",
  importing: "Importing",
  neutral: "Neutral",
  unknown: "Unknown",
};

export const MODE_LABELS = {
  save_money: "Smart Savings",
  smart_savings: "Smart Savings",
  balanced: "Balanced",
  save_energy: "Save Energy",
};

export const STATUS_LABELS = {
  current: "Live",
  delayed: "Delayed",
  stale: "Stale",
  demo: "Demo day",
  static: "Static",
  unknown: "Unknown",
};

export function adelaideToday(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Australia/Adelaide",
  }).format(now);
}

export function formatNumber(value, digits = 1) {
  return value == null ? "—" : value.toFixed(digits);
}

export function centsPerKwh(audPerKwh) {
  return audPerKwh == null ? null : audPerKwh * 100;
}

/** AUD amount as "$4.82" / "-$0.28"; a missing amount stays an em dash. */
export function formatAud(amount) {
  return amount == null
    ? "—"
    : `${amount < 0 ? "-" : ""}$${Math.abs(amount).toFixed(2)}`;
}

export function hourLabel(hour) {
  if (hour === 0) return "12 AM";
  if (hour < 12) return `${hour} AM`;
  if (hour === 12) return "12 PM";
  return `${hour - 12} PM`;
}

/**
 * Which flow edges to animate. `directions == null` means the overview has not
 * arrived yet, so nothing contradicts the animation (all edges animate); an
 * empty list is a real "no flow in this slot" and animates none.
 */
export function activeFlowIds(directions, edgeDirections) {
  const ids = Object.keys(edgeDirections);
  return directions == null
    ? ids
    : ids.filter((id) => directions.includes(edgeDirections[id]));
}

/**
 * /market price history (AUD/kWh per 5-min slot) -> hourly averages in c/kWh,
 * bucketed by the timestamp's local hour like hourlySeries so both charts share
 * the same 24 labels. Hours with no quote stay null, never a fake 0.
 */
export function priceSeries(points = []) {
  const buckets = Array.from({ length: 24 }, () => []);

  points.forEach((point) => {
    const match = /T(\d{2}):/.exec(point.timestamp ?? "");
    if (!match || point.price_aud_per_kwh == null) return;
    const hour = Number(match[1]);
    if (hour <= 23) buckets[hour].push(point.price_aud_per_kwh);
  });

  return {
    unit: "c/kWh",
    labels: Array.from({ length: 24 }, (_, hour) => hourLabel(hour)),
    values: buckets.map((readings) =>
      readings.length
        ? Number(
            (
              (readings.reduce((sum, value) => sum + value, 0) /
                readings.length) *
              100
            ).toFixed(2),
          )
        : null,
    ),
  };
}

/**
 * Energy-history points (watts) -> hourly averages in kW, bucketed by the
 * timestamp's local wall-clock hour (the API emits Adelaide-local ISO strings).
 * Hours with no readings stay null so the chart shows a gap, never a fake 0.
 */
export function hourlySeries(points = []) {
  const buckets = SERIES_FIELDS.map(() => Array.from({ length: 24 }, () => []));

  points.forEach((point) => {
    const match = /T(\d{2}):/.exec(point.timestamp ?? "");
    if (!match) return;
    const hour = Number(match[1]);
    if (hour > 23) return;

    SERIES_FIELDS.forEach((field, series) => {
      if (point[field] != null) buckets[series][hour].push(point[field]);
    });
  });

  return {
    unit: "kW",
    labels: Array.from({ length: 24 }, (_, hour) => hourLabel(hour)),
    values: buckets.map((series) =>
      series.map((readings) =>
        readings.length
          ? Number(
              (
                readings.reduce((sum, value) => sum + value, 0) /
                readings.length /
                1000
              ).toFixed(3),
            )
          : null,
      ),
    ),
  };
}
