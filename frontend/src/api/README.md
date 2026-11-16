# AIVY API client

All frontend HTTP requests should be implemented in this directory rather than
directly inside React components. Components import the exported functions from
`src/api/index.js`.

## Configuration

By default, requests use the current origin. During Vite development, `/api`
requests are proxied to `http://127.0.0.1:8000` by `vite.config.js`.

An explicit backend origin can be supplied when needed:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

## Dashboard data

| Function | Endpoint | Purpose |
|---|---|---|
| `getStations()` | `GET /api/stations` | Station catalog. |
| `getOverview(stationId)` | `GET /api/stations/{id}/overview` | Latest completed 5-minute slot. |
| `getMarket(stationId)` | `GET /api/stations/{id}/market` | Spot price and weather for the current slot. |
| `getEnergyHistory(stationId, date)` | `GET /api/stations/{id}/energy-history?date=` | Sampled day series plus missing intervals. |
| `getStationHealth(stationId)` | `GET /api/stations/{id}/health` | Today's diagnosis from `fault_detection.py`. |

## `getStationHealth(stationId, options)`

Runs the repo's real `fault_detection.py` over the station's recorded days: the
29 recorded days form the baseline, then today's completed slots are classified.

- Source: `dashboard.js`
- Response `data`:

| Field | Meaning |
|---|---|
| `status` | `healthy`, `sub_healthy` or `broken`. |
| `fault_types` | Detected patterns, e.g. `sudden_drop_to_zero`, `midday_flattening`, `incomplete_day`. |
| `explanation` | Plain-English sentence from the detector. |
| `soc_ceiling_pct` / `charge_limit_w` | The station's own learned "battery full" level and maximum charge rate. |
| `suspected_sign_issue` | `battery` and/or `load` when the energy balance only holds after a sign flip. |
| `trends` | `gradual_pv_decline` and `battery_capacity_shrink` checks. |
| `fault_injected` / `fault_window` | `true` while `backend/fault_config.json` is zeroing this station's PV. |
| `days_analysed` | Recorded days that fed the baseline. |

The readings are real; the fault is simulated. Whenever a fault is injected the
same response carries a `FAULT_INJECTED` warning, so the UI must show
`fault_injected` (the dashboard tags it "Simulated fault") and never present a
simulated drop as a genuine one.

## Assistant, simulation and settings

| Function | Endpoint | Purpose |
|---|---|---|
| `sendChatMessage(stationId, message, { conversationId })` | `POST /api/assistant/chat` | One grounded assistant turn: `content`, `evidence` and an optional `suggested_strategy`. |
| `simulateStrategy(stationId, strategy)` | `POST /api/stations/{id}/simulate` | Saves a proposal to the simulator (simulation only, never a device command). |
| `updateStrategySettings(stationId, settings)` | `PUT /api/stations/{id}/strategy` | Changes the mode, reserve and goal of the simulated strategy. |
| `getStrategy(stationId)` | `GET /api/stations/{id}/strategy` | Current settings plus the latest simulation. |

`sendChatMessage` lives in `assistant.js`; the rest are in `strategy.js`. A proposal
from the chat reply carries extra fields, so `simulationStrategy()` from
`src/lib/strategy.js` forwards only the five keys the sandbox accepts.

## Backend-generated documentation endpoints

These routes are provided automatically by FastAPI and are normally opened in
a browser rather than called through the frontend API client:

| Endpoint | Purpose |
|---|---|
| `GET /docs` | Interactive Swagger documentation. |
| `GET /redoc` | ReDoc API documentation. |
| `GET /openapi.json` | Machine-readable OpenAPI schema. |
