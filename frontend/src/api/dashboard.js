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

// Real recorded data through the real fault_detection.py. An injected demo fault
// (backend/fault_config.json) is reported by the API like a detected one, so this
// response is the single source of truth for the card; fault_injected/fault_window
// stay diagnostics-only and are not rendered.
export const getStationHealth = (stationId, options) =>
  request(`/api/stations/${encodeURIComponent(stationId)}/health`, options);
