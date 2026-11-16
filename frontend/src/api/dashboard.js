import { request } from "./client";

export const getStations = (options) => request("/api/stations", options);

export const getOverview = (stationId, options) =>
  request(`/api/stations/${encodeURIComponent(stationId)}/overview`, options);

export const getMarket = (stationId, options) =>
  request(`/api/stations/${encodeURIComponent(stationId)}/market`, options);

export const getEnergyHistory = (stationId, date, options) =>
  request(
    `/api/stations/${encodeURIComponent(stationId)}/energy-history?${new URLSearchParams({ date })}`,
    options,
  );

// Real recorded data through the real fault_detection.py; the fault itself is
// simulated (backend/fault_config.json) and flagged by data.fault_injected.
export const getStationHealth = (stationId, options) =>
  request(`/api/stations/${encodeURIComponent(stationId)}/health`, options);
