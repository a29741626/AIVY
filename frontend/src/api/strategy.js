import { request } from "./client";

const station = (stationId, suffix = "") =>
  `/api/stations/${encodeURIComponent(stationId)}${suffix}`;

export const getStrategy = (stationId, options) =>
  request(station(stationId, "/strategy"), options);

export const getBattery = (stationId, options) =>
  request(station(stationId, "/battery"), options);

export const updateStrategySettings = (stationId, settings, options) =>
  request(station(stationId, "/strategy"), {
    ...options,
    method: "PUT",
    body: settings,
  });

export const getRecommendation = (stationId, options) =>
  request(station(stationId, "/recommendation"), options);

export const getFinancialSummary = (stationId, date, options) =>
  request(
    `${station(stationId, "/financial-summary")}?${new URLSearchParams({ date })}`,
    options,
  );

/** Simulation-only: saves the plan to the sandbox, never to the battery. */
export const simulateStrategy = (stationId, strategy, options) =>
  request(station(stationId, "/simulate"), {
    ...options,
    method: "POST",
    body: { mode: "simulation", strategy },
  });
