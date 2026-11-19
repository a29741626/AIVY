# AIVY backend (FastAPI)

FastAPI service behind the AIVY dashboard. It serves the P0 energy dashboard, the P1
savings/strategy views, the simulation-only strategy sandbox, the P2 AI assistant and
P2 fault diagnostics — all over **recorded** data from `strategy/data` and the strategy
code in `strategy/codes`.

Nothing in this service can command a real battery or inverter. Every P1 response says
so explicitly (`device_control_attempted: false`), and the assistant can only propose a
strategy that the user then applies **in simulation**.

| Phase | What it adds | Routes |
|---|---|---|
| P0 | Live-style dashboard from recorded telemetry, prices and weather | `/api/stations`, `.../overview`, `.../energy-history`, `.../market` |
| P1 | Battery detail, savings vs a baseline, MPC recommendation, sandbox | `.../battery`, `.../financial-summary`, `.../recommendation`, `.../simulate`, `.../strategy` |
| P2 | Grounded Gemini assistant and fault diagnostics | `/api/assistant/chat`, `.../health` |

The response contract is `schema_version: "0.1"` (`schemas/contract.py`).

## Folder layout

| Path | What it is |
|---|---|
| `main.py` | FastAPI app: CORS, startup forecast warm-up, request ID, error handlers |
| `config.py` | Paths, stations, time constants, environment variables; puts `strategy/codes` on `sys.path` |
| `fault_config.json` | Demo fault injection (see [fault_config.json](#fault_configjson-simulated-pv-fault)) |
| `requirements.txt` | Backend-only packages (the notebooks' Jupyter/matplotlib are not needed) |
| `routers/` | HTTP layer: `p0_dashboard`, `p1_strategies`, `p2_assistant`, `p2_fault` |
| `schemas/contract.py` | Pydantic response/request models |
| `services/` | Replay clock, data adapter, strategy engine, state, fault injection/detection, assistant |
| `tests/` | Four standalone `unittest` self-check scripts |
| `../strategy/codes/` | Strategy stack the backend imports (`battery.py`, `strategy.py`, `forecast.py`, …) |
| `../strategy/data/` | Recorded days, AEMO prices, weather cache, price forecasts |
| `../strategy/models/` | Trained PV/price models used by `forecast.py` |

## Requirements

- Python 3.10+ (validated on Python 3.13.5, Windows 11). `tzdata` is required on Windows
  for `Australia/Adelaide`.
- Install once:

  ```sh
  pip install -r backend/requirements.txt
  ```

  (validated with `fastapi 0.163.1`, `uvicorn 0.54.0`, `pydantic 2.x`)
- The data files must exist, because the services read them directly:

  | File | Used for |
  |---|---|
  | `strategy/data/SBX-001_clean.csv`, `SBX-002_clean.csv`, `SBX-003_clean.csv` | Recorded telemetry (one row per 5 minutes) |
  | `strategy/data/prices_aemo.csv` | Wholesale spot prices for `/market` |
  | `strategy/data/weather_cache.pkl` | Temperature, cloud, GHI for `/market` |
  | `strategy/data/price_forecasts_backtest.npz` | The planner's 7-day price forecasts |
  | `strategy/models/pv_prediction_models.pkl` | PV forecast used by `forecast.prepare()` |

## Run the server

Run **from this folder**, not from the repo root:

```sh
cd backend
python -m uvicorn main:app --reload --port 8000
```

`uvicorn main:app` also works. `main.py` imports `config`, `routers` and `services` as
top-level modules, and `config.py` adds `strategy/codes` to `sys.path`; starting from the
repo root with `backend.main:app` breaks those imports.

Then open:

| URL | What it is |
|---|---|
| http://127.0.0.1:8000/api/stations | First real endpoint |
| http://127.0.0.1:8000/docs | Swagger UI (try every endpoint) |
| http://127.0.0.1:8000/redoc | ReDoc |
| http://127.0.0.1:8000/openapi.json | OpenAPI schema |

The frontend dev server proxies `/api` to `http://127.0.0.1:8000` (`frontend/vite.config.js`),
so start the backend first and on this port.

On startup the app **warms the forecast cache in the background** for all three stations
(about 1–2 minutes of CPU work on a laptop; the console logs
`warming forecast cache ...`, then `forecast cache warm for SBX-...` and
`strategy cache warm for ...`). Requests are served immediately; only the first P1/P2
call that needs forecasts waits for the warm-up. Set `AIVY_PRELOAD=0` to skip it.

For a LAN demo without the reloader:

```sh
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Use **one worker**: settings and chat history live in process memory.

## Configuration

All configuration is environment variables read by `config.py`:

| Variable | Default | Meaning |
|---|---|---|
| `AIVY_REPLAY_DATE` | `2026-09-16` | Recorded day presented as "today" (`source.replay_date`) |
| `AIVY_PRELOAD` | `1` | `0` disables the startup forecast warm-up |
| `AIVY_GEMINI_API_KEY` | built-in demo key | Assistant key; override with your own |
| `AIVY_GEMINI_URL` | Google's OpenAI-compatible endpoint | Chat-completions URL |
| `AIVY_GEMINI_MODELS` | 4 Gemini model names, comma-separated | Tried in order until one answers |
| `AIVY_GEMINI_TIMEOUT` | `30` | Seconds per model attempt |

PowerShell (current shell only):

```powershell
$env:AIVY_PRELOAD = "0"
$env:AIVY_GEMINI_API_KEY = "<your key>"
cd backend; python -m uvicorn main:app --reload --port 8000
```

The assistant key is only ever sent as an `Authorization` header. It is never logged,
never put in a prompt and never copied into a response. Do not commit a real key; the
fallback in `config.py` exists so the demo runs out of the box, not as a secret store.

## Response format

### Success envelope

Every 2xx response has the same three top-level keys:

```json
{
  "data": { "...": "endpoint-specific payload" },
  "meta": {
    "station_id": "SBX-003",
    "timezone": "Australia/Adelaide",
    "generated_at": "2026-11-16T18:17:05.190327+10:30",
    "data_timestamp": "2026-11-16T18:10:00+10:30",
    "freshness_seconds": 425,
    "data_status": "current",
    "source": {
      "mode": "replay",
      "dataset": "SBX-003_clean.csv",
      "replay_date": "2026-09-16",
      "real_date": "2025-09-16",
      "mapped_from": null
    },
    "schema_version": "0.1"
  },
  "warnings": [
    {
      "code": "PARTIAL_DAY",
      "message": "Day in progress: 219 of 288 slots completed",
      "field": null,
      "count": 219
    }
  ]
}
```

| `meta` field | Meaning |
|---|---|
| `station_id` | Requested station, or `null` for `/api/stations` |
| `timezone` | Always `Australia/Adelaide` |
| `generated_at` | Server time the answer was built (Adelaide offset) |
| `data_timestamp` | Timestamp of the newest reading used, or `null` |
| `freshness_seconds` | Age of `data_timestamp` at `generated_at` |
| `data_status` | One word for the UI (below) |
| `source` | Where the answer came from, including the replay date |
| `schema_version` | Contract version, `"0.1"` |

`data_status` values: `current` (≤ 10 min old), `delayed` (≤ 1 h), `stale` (> 1 h),
`demo` (any past date), `static` (`/api/stations`, `/strategy`), `simulation` (the
`/simulate` result), `unknown` (no timestamp). The assistant's `source.mode` is `static`
because it is not a telemetry reading, but its `data_status` mirrors the overview it was
grounded in.

`source.mode` values:

| Mode | When |
|---|---|
| `replay` | "Today": the fixed `AIVY_REPLAY_DATE` recording, truncated to finished slots |
| `demo` | A past date: its own recording, or a deterministic mapped recording (`mapped_from`) |
| `static` | `/api/stations`, `GET/PUT .../strategy`, assistant metadata |
| `simulation` | A saved sandbox result (`POST .../simulate` and `latest_simulation`) |

### Warnings

Warnings never change the HTTP status; they explain a partial or simulated answer.
The frontend can show them as-is.

| Code | Raised by | Meaning |
|---|---|---|
| `MISSING_DATA` | overview, history, market | A slot or field has no reading; values are `null` |
| `PARTIAL_DAY` | overview, history, summary | Today is still in progress |
| `FAULT_INJECTED` | overview, history, health | `fault_config.json` zeroed PV for some slots |
| `NO_COMPLETED_SLOT_TODAY` | overview, summary | Just after midnight; overview shows yesterday's last slot |
| `REPLAY_DAY_SUBSTITUTED` | recommendation, summary | Forecast data for the asked day was unusable; nearest day used |
| `BASELINE_FAILED` | summary, recommendation, simulate, assistant | The self-consumption baseline could not be simulated, so savings are `null` |
| `STRATEGY_SIMULATION_FAILED` | summary | The active mode could not be simulated, so actual cost is `null` |
| `RECOMMENDATION_UNAVAILABLE` | assistant | The MPC plan failed while building the chat context |
| `SAVINGS_UNAVAILABLE` | assistant | Today's saving failed while building the chat context |
| `STALE_CONTEXT` | assistant | Live data is stale/incomplete, so only an explanation is offered |
| `AI_ANSWER_SANITIZED` | assistant | The model proposed a value outside our enums; the current/default value was kept |
| `STRATEGY_UNAVAILABLE` | assistant | The suggested mode could not be simulated, so no proposal is offered |

### Errors

Errors use a different envelope (`meta` contains only `generated_at` and
`schema_version`):

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The sandbox is simulation-only: mode must be 'simulation'",
    "field": "mode",
    "retryable": false,
    "request_id": "53f76d0f027a4e7b81b75e65a9a89229"
  },
  "meta": {
    "generated_at": "2026-11-16T18:18:29.164767+10:30",
    "schema_version": "0.1"
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `INVALID_DATE` | `date` is not `YYYY-MM-DD`, not a calendar date, or in the future |
| 400 | `VALIDATION_ERROR` | A rule the handler checks itself: bad mode, goal, reserve, timestamps, empty chat |
| 404 | `STATION_NOT_FOUND` | Station is not `SBX-001`/`002`/`003` (case-insensitive) |
| 404 | `NOT_FOUND` | No such route |
| 422 | `VALIDATION_ERROR` | Request body/path/query does not match the Pydantic model |
| 500 | `INTERNAL_ERROR` | Unexpected error (details are logged, not returned) |
| 502 | `AI_SERVICE_ERROR` | Gemini is unreachable, rejects the key, or replies without usable JSON |
| 503 | `NO_DATA` | No recorded days, or no completed slot yet today |
| 503 | `STRATEGY_UNAVAILABLE` | The MPC/simulation run failed; `retryable: true` |

Every response, success or error, carries an `X-Request-ID` header; the same value is in
`error.request_id`, so a UI report can be matched to the console log.

## Time, dates and power signs

### Virtual 5-minute clock

- Timezone is `Australia/Adelaide`; a day is **288 slots** of 5 minutes, slot `0` at
  00:00 and `287` at 23:55. `current_slot(18:17)` is `219`.
- A slot **in progress is not a reading**. `overview`, `energy-history` and
  `financial-summary` only ever include finished slots, so "today" is truncated and
  carries `PARTIAL_DAY`.
- `freshness_seconds` is `now - start of the newest completed slot`, which is why a
  fresh replay reading can be a few minutes old (the in-progress slot is excluded).
- Between 00:00 and 00:04 there is no completed slot yet:
  `overview` falls back to yesterday's last slot with `NO_COMPLETED_SLOT_TODAY`,
  `financial-summary` returns `null` costs with the same warning, and `health` returns
  `503 NO_DATA`.

### Dates

- `?date=` is strict `YYYY-MM-DD`; anything else is `400 INVALID_DATE`.
- Future dates are `400 INVALID_DATE`; only completed data is served.
- Today → `source.mode: "replay"`, source day `AIVY_REPLAY_DATE`, `real_date` is the
  recording behind it (prices/weather are keyed by that real date).
- A past date with its own recording → `source.mode: "demo"`.
- A past date without a recording → a deterministic modulo-mapped recording;
  `source.mode: "demo"` and `source.mapped_from` names the day used.
- Timestamps in `/simulate` and chat proposals are ISO 8601. A timestamp without an
  offset is read as Adelaide wall-clock time.

### Power sign convention

| Reading | Positive | Negative | Deadband |
|---|---|---|---|
| `battery_w` | charging | discharging | `|w| <= 50 W` → idle |
| `grid_w` | export to the grid | import from the grid | `|w| <= 50 W` → neutral |

- The energy balance is `pv = load + battery + grid` (with those signs).
- `null` means "no reading". Missing values are never turned into `0`.
- kWh over one slot is `watts / 12000` (5 minutes = 1/12 hour).
- Some recordings are net-metered: `load_w` goes negative while the home exports.
  `overview.display.home_usage_kw` floors it at 0, and the grid card carries the export.
- `overview.display` gives absolute kW for the battery and grid cards;
  `battery.signed_power_kw` keeps the sign. `flow.active_directions` is derived only
  from signs, so it can list several paths at once.

### Stations

| Station | Battery | Max charge/discharge | SOC range | Round-trip efficiency |
|---|---|---|---|---|
| `SBX-001` | 9.6 kWh | 10 kW | 10–100% | 0.95 |
| `SBX-002` | 29 kWh | 10 kW | 10–100% | 0.95 |
| `SBX-003` | 34 kWh | 5 kW | 10–97% | 0.95 |

All three report `capabilities.device_control: false`. Station IDs are
case-insensitive (`sbx-001` works) and are normalised to upper case in responses.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/stations` | Station catalogue and battery specs |
| GET | `/api/stations/{id}/overview` | Latest completed slot + display/state/flow |
| GET | `/api/stations/{id}/energy-history?date=` | One day of 5-minute points |
| GET | `/api/stations/{id}/market` | Spot price, price history and weather |
| GET | `/api/stations/{id}/battery` | Battery detail and time-to-full |
| GET | `/api/stations/{id}/financial-summary?date=` | Baseline vs active strategy cost |
| GET | `/api/stations/{id}/recommendation` | Current MPC action for this slot |
| POST | `/api/stations/{id}/simulate` | Run a goal in the simulation sandbox |
| GET | `/api/stations/{id}/strategy` | Stored settings + latest simulation |
| PUT | `/api/stations/{id}/strategy` | Update settings (simulation only) |
| POST | `/api/assistant/chat` | Grounded answer, optional proposal |
| GET | `/api/stations/{id}/health` | Fault diagnosis + trends |

Examples below use `curl`; on Windows use `curl.exe` and double-quote JSON bodies, or
`Invoke-RestMethod` as shown in the `PUT` example.

### GET /api/stations

Catalogue from `battery.SPECS`, optionally enriched by `strategy/data/stations.json`.
`data` is an array of `{id, name, spec, capabilities, profile}`; `profile` is `null`
unless `stations.json` exists. `source.mode` is `static`.

```sh
curl "http://127.0.0.1:8000/api/stations"
```

### GET /api/stations/{id}/overview

The newest finished slot, with sign-derived extras:

| Field | Meaning |
|---|---|
| `timestamp` | Start of the newest completed slot |
| `telemetry` | Raw `pv_w`, `load_w`, `battery_w`, `grid_w`, `soc_pct` (W, W, W, W, %) |
| `display` | `solar_power_kw`, `home_usage_kw` (floored at 0), `battery_power_kw` (abs), `grid_power_kw` (abs) |
| `states` | `battery` (`charging`/`discharging`/`idle`/`unknown`), `grid` (`exporting`/`importing`/`neutral`/`unknown`), `system` (`online` when freshness ≤ 600 s, else `delayed`) |
| `flow` | `mode: "direction_only"` plus `active_directions` |

`active_directions` can contain `solar_to_home`, `solar_to_battery`, `solar_to_grid`,
`battery_to_home`, `battery_to_grid`, `grid_to_home`, `grid_to_battery`.

```sh
curl "http://127.0.0.1:8000/api/stations/SBX-003/overview"
```

Warnings: `MISSING_DATA`, `PARTIAL_DAY`, `FAULT_INJECTED`, `NO_COMPLETED_SLOT_TODAY`.
No telemetry at all → `503 NO_DATA`.

### GET /api/stations/{id}/energy-history?date=YYYY-MM-DD

| Field | Meaning |
|---|---|
| `date` | Requested date (echoed) |
| `expected_points` | 288 |
| `received_points` | Slots with at least one non-null value |
| `completeness_pct` | `received_points / 288 * 100`, one decimal |
| `missing_intervals` | `[{slot, timestamp}]` for slots where every value is `null` |
| `is_partial` | True while today is still in progress |
| `points` | `[{timestamp, pv_w, load_w, battery_w, grid_w, soc_pct, quality}]` |

`quality` is `good`, or `missing` when any value in the slot is `null`. A slot in the
fault window has `pv_w: 0.0` and its grid rewritten, but stays `quality: "good"`
because the row is complete (see [fault injection](#fault_configjson-simulated-pv-fault)).

```sh
curl "http://127.0.0.1:8000/api/stations/SBX-003/energy-history?date=2026-11-16"
```

### GET /api/stations/{id}/market

| Field | Meaning |
|---|---|
| `slot` | Current 5-minute slot (0–287) |
| `price.import`, `price.feed_in` | Wholesale spot price in AUD/kWh (5 dp); both use the same AEMO price because the source has no network tariff/FIT |
| `price.price_type` | `wholesale_spot` |
| `price.valid_from`, `valid_to` | The 5-minute window of this price |
| `history` | Today's prices from slot 0 to the current slot, `{timestamp, price_aud_per_kwh}` |
| `weather` | `temperature_c`, `cloud_cover_pct`, `ghi_w_m2`, `condition`, `observed_at` |
| `expected_peak_price_time` | ISO start of the highest forecast price still ahead today; `null` when no forecast remains |

`weather.condition` is `night` (before 06:00 or from 19:00, or GHI ≤ 0), then `clear`
(<20% cloud), `partly_cloudy` (<60%), `cloudy` (<85%), `overcast`, or `unknown` when
the reading is missing. Prices and weather come from the recording's **real** date, not
the presentation date. `/market` is not affected by fault injection.

Warnings: `MISSING_DATA` with `field: "price"` or `field: "weather"`.

```sh
curl "http://127.0.0.1:8000/api/stations/SBX-003/market"
```

### GET /api/stations/{id}/battery

| Field | Meaning |
|---|---|
| `timestamp` | Newest completed slot used |
| `soc_pct` | State of charge, or `null` |
| `state` | `charging` / `discharging` / `idle` / `unknown` |
| `power_kw` | Absolute power now |
| `signed_power_kw` | > 0 charging, < 0 discharging |
| `capacity_kwh`, `max_charge_power_kw`, `max_discharge_power_kw` | Specs |
| `reserve_pct` | Reserve currently stored in the strategy settings |
| `estimated_time_to_full_min` | Only when charging: energy still missing ÷ (power × efficiency) |
| `estimate_quality` | `high` when an estimate exists, otherwise `unavailable` |

```sh
curl "http://127.0.0.1:8000/api/stations/SBX-003/battery"
```

### GET /api/stations/{id}/financial-summary?date=YYYY-MM-DD

Simulates the self-consumption baseline (`method: self_consumption_v1`,
`method_version: 1.0`) and the **stored active strategy** over the requested day, then
compares them. Today is truncated to finished slots.

| Field | Meaning |
|---|---|
| `actual_net_cost_aud` | Cost of the active strategy (AUD, can be negative when export earns) |
| `baseline_net_cost_aud` | Cost of the baseline |
| `today_savings_aud` | `baseline - actual`; `null` if either side failed |
| `savings_pct` | Savings as a percentage of a non-zero baseline |
| `baseline` | `{status: available/failed, method, method_version, net_cost_aud, message}` |
| `actual` | `{strategy, optimisation_goal, battery_reserve_pct, net_cost_aud}` |
| `baseline_costs`, `actual_costs` | `{import_kwh, export_kwh, import_cost_aud, export_earnings_aud, net_cost_aud}` |
| `coverage` | `{expected_intervals_to_now, valid_energy_intervals, valid_price_intervals, coverage_pct}` |
| `period` | `{date, start_at, end_at, intervals, is_partial}` |

Cost math: `import_kwh = max(-grid_w, 0) / 12000`, `export_kwh = max(grid_w, 0) / 12000`,
money = kWh × c/kWh ÷ 100, `net = import_cost - export_earnings`. Failed simulations
return `null`, never `0`.

Warnings: `PARTIAL_DAY`, `NO_COMPLETED_SLOT_TODAY`, `BASELINE_FAILED`,
`STRATEGY_SIMULATION_FAILED`. `400 INVALID_DATE` for bad/future dates.

```sh
curl "http://127.0.0.1:8000/api/stations/SBX-003/financial-summary?date=2026-11-16"
```

### GET /api/stations/{id}/recommendation

Runs the MPC planner with the stored settings for the current slot of the replay day:
`optimizer.MPCController`, `model_version: mpc-linprog-v1`, action derived from the
planned battery/grid power with the 50 W deadband.

| Field | Meaning |
|---|---|
| `recommendation_id` | `rec_<YYYYMMDD>_<HHMM>_<station>` |
| `action` | `charge` / `discharge` / `export` / `hold` |
| `text` | One owner-facing sentence with the real numbers |
| `reason` | Plain-English rationale from price, SOC, reserve, PV peak and 6-hour price peak |
| `reserve_pct` | Reserve used by the plan |
| `window` | `start_at` = slot start, `end_at` = +30 min |
| `estimated_savings_aud` | Simulated **full-day** saving of this plan vs baseline; can be negative; `null` when the baseline failed |
| `status` | `draft` (nothing applied) |
| `valid_from` / `valid_to` | Same 30-minute validity window |
| `input_freshness` | Age of the previous slot in seconds; `null` for slot 0 |
| `price_now_c_per_kwh`, `soc_pct` | Inputs at decision time |
| `planned_battery_w`, `planned_grid_w` | Signed plan for this slot |

```sh
curl "http://127.0.0.1:8000/api/stations/SBX-003/recommendation"
```

If the planner fails: `503 STRATEGY_UNAVAILABLE` (`retryable: true`). A substituted
forecast day adds `REPLAY_DAY_SUBSTITUTED`; a baseline failure adds `BASELINE_FAILED`
and leaves `estimated_savings_aud` null.

### POST /api/stations/{id}/simulate

Runs the requested goal over the recorded day and stores the result as the station's
`latest_simulation`. No device command is ever attempted.

```json
{
  "mode": "simulation",
  "strategy": {
    "type": "mpc",
    "optimisation_goal": "minimise_cost",
    "battery_reserve_pct": 25,
    "start_at": "2026-11-16T18:15:00+10:30",
    "end_at": "2026-11-16T18:45:00+10:30"
  },
  "recommendation_id": null
}
```

| Rule | Result |
|---|---|
| `mode` must be `simulation` (case-insensitive) | `400 VALIDATION_ERROR`, `field: "mode"` |
| `strategy.type` required and non-empty | `400`, `field: "strategy.type"` |
| `optimisation_goal` ∈ `minimise_cost`, `maximise_export_revenue`, `maximise_self_consumption` | `400`, `field: "strategy.optimisation_goal"` |
| `battery_reserve_pct` must be a number 0–100 | `400` (not 422) |
| `start_at` / `end_at` ISO 8601, `end > start`, same day | `400` |
| Slots whose **start** lies in `[start_at, end_at]` are costed | `end_at` is inclusive |
| Planner failure | `503 STRATEGY_UNAVAILABLE`, `retryable: true` |

Missing required body fields (or wrong JSON types) are rejected by Pydantic with `422`
before these checks; the rows above describe values that are present but invalid.

Goal → simulator mode: `minimise_cost` → `save_money`; `maximise_export_revenue` →
`save_money` with `sell_battery: always`; `maximise_self_consumption` → `save_energy`
with `sell_battery: never`.

Response (`SimulationData`): `strategy_id` (`sim_strat_xxxxxxxx`), `status`
`applied_in_simulation`, `device_control_attempted: false`, the settings used,
`date`, `replay_date`, `baseline_net_cost_aud`, `simulated_net_cost_aud`,
`estimated_benefit_aud`, `recommendation_id`, `created_at`, and
`message: "Strategy saved to the AIVY simulator. No physical device command was sent."`
A baseline failure adds `BASELINE_FAILED` and leaves the benefit `null`.
Response meta: `source.mode: "simulation"`, `data_status: "simulation"`,
`freshness_seconds: null`, and `data_timestamp` = the window start.

```sh
curl -X POST "http://127.0.0.1:8000/api/stations/SBX-003/simulate" \
  -H "Content-Type: application/json" \
  -d "{\"mode\":\"simulation\",\"strategy\":{\"type\":\"mpc\",\"optimisation_goal\":\"minimise_cost\",\"battery_reserve_pct\":25,\"start_at\":\"2026-11-16T18:15:00+10:30\",\"end_at\":\"2026-11-16T18:45:00+10:30\"}}"
```

### GET /api/stations/{id}/strategy

Returns `{station_id, settings, latest_simulation}`. Defaults per station:
`current_mode: "balanced"`, `battery_reserve_pct` = the balanced mode's floor (30),
`optimisation_goal: "minimise_cost"`, `strategy_status: "active"` (active **in the
simulator only**), `updated_by: "system"`. `latest_simulation` is `null` until a
successful `POST .../simulate`, and is replaced by each new simulation.

```sh
curl "http://127.0.0.1:8000/api/stations/SBX-003/strategy"
```

### PUT /api/stations/{id}/strategy

Partial update; any omitted field keeps its current value. Body fields:
`current_mode`, `battery_reserve_pct`, `optimisation_goal`.

- Modes: `save_money` (alias `smart_savings`), `balanced`, `save_energy`.
- Goals: `minimise_cost`, `maximise_export_revenue`, `maximise_self_consumption`.
- Reserve: 0–100 **and** inside the battery's `soc_min`–`soc_max` (10–100 for SBX-001/002,
  10–97 for SBX-003). `/simulate` only enforces 0–100.
- Any invalid value → `400 VALIDATION_ERROR` with the offending `field`; nothing is stored.
- Response is the full `StrategyData` (updated `updated_at`, `updated_by: "user"`).

```sh
curl -X PUT "http://127.0.0.1:8000/api/stations/SBX-003/strategy" \
  -H "Content-Type: application/json" \
  -d "{\"current_mode\":\"save_money\",\"battery_reserve_pct\":20,\"optimisation_goal\":\"maximise_self_consumption\"}"
```

PowerShell equivalent:

```powershell
$body = '{"current_mode":"save_money","battery_reserve_pct":20,"optimisation_goal":"maximise_self_consumption"}'
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8000/api/stations/SBX-003/strategy `
  -ContentType 'application/json' -Body $body
```

Settings live in process memory only: a restart returns every station to its defaults.

### POST /api/assistant/chat

One grounded answer per call, optionally with a simulation-only proposal.

```json
{
  "conversation_id": "conv_ab12cd34",
  "station_id": "SBX-001",
  "message": "Keep 40% back tonight and save money",
  "client_context": { "locale": "en-AU" }
}
```

| Field | Notes |
|---|---|
| `conversation_id` | Optional; `conv_xxxxxxxx` is generated when absent |
| `station_id` | Required; unknown → `404 STATION_NOT_FOUND` |
| `message` | Required after trimming; 1–1000 characters, else `400` |
| `client_context` | Accepted for client hints; it is **not** used as grounding |

What the server does:

1. Builds a fresh context from `overview`, `market`, `recommendation` and
   `financial-summary` (telemetry, price, weather, strategy settings, MPC plan, today's
   savings, data quality). The client cannot inject telemetry.
2. Sends the context plus the last 10 turns of that conversation to Gemini
   (`temperature: 0`), trying each configured model in order.
3. Parses the JSON reply, sanitises it against our own enums and ranges, and builds the
   evidence list itself. The model's numbers are never trusted over the server's.

Response (`data`):

| Field | Meaning |
|---|---|
| `conversation_id`, `message_id` | Stable conversation id and `msg_xxxxxxxx` |
| `role` | `assistant` |
| `content` | 1–3 sentences, capped at 2000 characters |
| `evidence` | `[{label, value, unit, timestamp, price_type?}]` for SOC, solar, load, battery, grid, prices, reserve, savings, temperature; unavailable metrics are omitted |
| `suggested_strategy` | `null` or `{strategy_id, type, mode_name, start_at, end_at, battery_reserve_pct, optimisation_goal, estimated_benefit_aud, reason}` |

Proposal rules:

- Only when the owner asks how the battery should run, live data is `current`
  (freshness ≤ 1 h) and SOC + import price are present; otherwise the reply is
  explanation-only with `STALE_CONTEXT`.
- `mode_name` must be `save_money`, `balanced` or `save_energy`; a goal must be one of
  ours. Anything else keeps the current value and adds `AI_ANSWER_SANITIZED`.
- Reserve must fit the battery spec; an unusable value falls back to the mode's default.
- The window is the current slot plus 30 minutes, kept inside today. The benefit is
  costed over exactly that window against the baseline and can be `null`.
- The proposal is never applied. The UI must POST the five window fields to
  `/api/stations/{id}/simulate`; when a proposal exists the reply says so explicitly.

Conversation history is in process memory (last 10 user+assistant turns), so a restart
clears it. Errors: `400 VALIDATION_ERROR`, `502 AI_SERVICE_ERROR` (`retryable: true`;
`false` when no key is configured). The assistant adds `RECOMMENDATION_UNAVAILABLE`,
`SAVINGS_UNAVAILABLE`, `STRATEGY_UNAVAILABLE`, `STALE_CONTEXT`, `AI_ANSWER_SANITIZED`
and the P0 warnings it saw.

```sh
curl -X POST "http://127.0.0.1:8000/api/assistant/chat" \
  -H "Content-Type: application/json" \
  -d "{\"station_id\":\"SBX-001\",\"message\":\"What is my battery doing right now?\"}"
```

### GET /api/stations/{id}/health

Runs the real detector (`services/fault_detection.py`) over the station's recorded
history: the 29 recorded days before the replay day plus today's completed slots
(including any injected fault), re-dated onto the virtual clock.

| Field | Meaning |
|---|---|
| `date` | Today |
| `status` | `healthy` / `sub_healthy` / `broken` |
| `fault_types` | Detected patterns: `sudden_drop_to_zero`, `midday_flattening`, `abnormal_spike`, `communication_dropout`, `missing_data`, `incomplete_day` |
| `explanation` | Plain English, generated from the pattern and status |
| `pct_flagged` | Percentage of today's readings flagged low vs what the weather allowed |
| `soc_ceiling_pct` | The station's own learned "battery full" level |
| `charge_limit_w` | The station's own learned maximum charging rate |
| `energy_balance_violation_pct` | Rows that break `pv = load + battery + grid` |
| `suspected_sign_issue` | `battery` and/or `load` when the signs look inverted |
| `trends.gradual_pv_decline` | `{detected, change_pct, correlation, days_used, reason}` |
| `trends.battery_capacity_shrink` | Same shape; estimated from clean charging periods |
| `fault_injected` | True while `fault_config.json` changed today's slots |
| `fault_window` | e.g. `"09:00-13:00"`, or `null` |
| `days_analysed` | Recorded days + today |
| `rows_dropped` | Readings the detector had to discard |

The detector needs 5+ days to classify a day and 10+ days for trends; it is cheap
(~0.2 s for 30 days), so nothing is cached and a request always reflects the current
clock and the current `fault_config.json`. `503 NO_DATA` when no slot has completed
today or today's rows are unusable.

A demo fault from `fault_config.json` is reported here like one the detector found:
once 30 minutes of the injected window have completed during daylight, `status`
becomes at least `sub_healthy`, `fault_types` includes `sudden_drop_to_zero`, and
`explanation` is a plain detector sentence (for example, "No solar output
09:00-11:20 despite available daylight..."). `fault_injected` and `fault_window`
stay in the payload so tests and diagnostics can tell the two apart; the dashboard
does not display them.

```sh
curl "http://127.0.0.1:8000/api/stations/SBX-003/health"
```

### API docs

`/docs`, `/redoc` and `/openapi.json` are served by FastAPI from the same models. They
are the source of truth for exact field names and request shapes; this file documents
the behaviour behind them.

## fault_config.json (simulated PV fault)

`backend/fault_config.json` is a demo knob for the P2 fault diagnostics. It forces a
**simulated** solar failure into the data the dashboard and detector read. The detection
is real; the fault is not, and every affected response says so with a
`FAULT_INJECTED` warning plus `fault_injected`/`fault_window` on `/health`. Those fields
are for API clients and tests; the dashboard deliberately renders the injected drop as an
ordinary detected fault (see the `/health` section above).

The file is read next to `main.py` (not under `strategy/data`) and is hot-reloaded:
whenever its mtime changes, the next request picks up the new config — no restart.

Current contents:

```json
{
  "enabled": true,
  "target_stations": ["SBX-003"],
  "fault_type": "pv_zero",
  "start_hour": 9,
  "end_hour": 13,
  "apply_to_replay_date_only": true
}
```

| Field | Type | Rules |
|---|---|---|
| `enabled` | boolean | Must be exactly `true`, otherwise injection is off |
| `target_stations` | string or array of strings | Trimmed/upper-cased; must contain at least one station ID; `[]` disables |
| `fault_type` | string | Only `pv_zero` is supported; anything else disables injection (with a log warning) |
| `start_hour` | integer | Clamped to 0–24 (invalid → 0); converted to slots (`hour × 12`) |
| `end_hour` | integer | Clamped to 0–24 (invalid → 0); must be greater than `start_hour`; exclusive |
| `apply_to_replay_date_only` | boolean, default `true` | `true`: only "today" (`mode == "replay"`); `false`: past/demo days too |

### What injection changes

For every slot in `[start_hour × 12, end_hour × 12)`:

- `pv_w` becomes `0.0`;
- `grid_w` is rewritten as `-load_w - battery_w`, so `pv = load + battery + grid` still
  holds (the detector's energy-balance check sees a consistent row);
- a slot whose recorded `pv_w` is already `null` is **not** touched — injection must not
  turn a missing reading into a 0 W reading;
- `soc_pct` and `load_w` are unchanged.

Example from a live run (SBX-003, window 09:00–13:00, 48 slots):

```json
{ "timestamp": "2026-11-16T11:00:00+10:30",
  "pv_w": 0.0, "load_w": -2252.0, "battery_w": 5051.0, "grid_w": -2799.0,
  "soc_pct": 68.7, "quality": "good" }
```

### Which endpoints see it

| Endpoint | Affected? |
|---|---|
| `.../overview` | If the injected window is in today's completed slots |
| `.../energy-history` | Yes, for today; also for past days when `apply_to_replay_date_only` is `false` |
| `.../health` | Yes — this is the point: the detector sees a real-looking PV failure |
| `.../battery` | Indirectly (it builds on `overview`) |
| `.../market` | No — prices and weather only |
| `.../financial-summary`, `.../recommendation`, `.../simulate`, assistant `/chat` context | No: these run the strategy stack from `strategy/data`, not the injected slice (the assistant's live telemetry can still show PV 0 if the latest slot is in the window) |

### Failure modes (never fatal, never silent)

- File missing or deleted → injection disabled, no API error.
- Invalid JSON or not an object → disabled + `fault_config.json unusable (...) -> fault injection disabled` in the log.
- `enabled: true` but unsupported fault, no target, or `start_hour >= end_hour` → ignored
  + a log warning naming the requirement.
- `enabled: false` → injected window is `(0, 0)`; nothing is changed and no warning is
  emitted.
- An active injection always produces `FAULT_INJECTED` with the count and window label in
  the day responses, so an API client can tell it apart from a recorded failure. The
  dashboard does not surface those fields: by product requirement the injected drop is
  presented as an ordinary detected fault.

### Examples

All three stations, 09:00–17:00, including past days:

```json
{
  "enabled": true,
  "target_stations": ["SBX-001", "SBX-002", "SBX-003"],
  "fault_type": "pv_zero",
  "start_hour": 9,
  "end_hour": 17,
  "apply_to_replay_date_only": false
}
```

A short outage on one station, today only:

```json
{
  "enabled": true,
  "target_stations": "SBX-001",
  "fault_type": "pv_zero",
  "start_hour": 12,
  "end_hour": 13,
  "apply_to_replay_date_only": true
}
```

Turn the demo fault off (keep the file for the next demo):

```json
{
  "enabled": false,
  "target_stations": ["SBX-003"],
  "fault_type": "pv_zero",
  "start_hour": 9,
  "end_hour": 13,
  "apply_to_replay_date_only": true
}
```

### Verify it is working

1. Edit the file (save it — the mtime change is the reload trigger).
2. Compare a slot inside and outside the window:

   ```sh
   curl "http://127.0.0.1:8000/api/stations/SBX-003/energy-history?date=2026-11-16"
   ```

   Inside the window `pv_w` is `0.0`; the response has
   `{"code":"FAULT_INJECTED","count":48,"message":"Simulated PV fault: PV forced to 0 W for 48 slot(s) (09:00-13:00)"}`.
3. Check the diagnosis:

   ```sh
   curl "http://127.0.0.1:8000/api/stations/SBX-003/health"
   ```

   Expected with the defaults once 30 minutes of the window have completed:
   `fault_injected: true`, `fault_window: "09:00-13:00"`,
   `fault_types: ["sudden_drop_to_zero"]`, `status: "sub_healthy"` and an explanation like
   `"No solar output 09:00-11:20 despite available daylight. Worth a check - not yet a confirmed fault."`
   If the detector has already reached its own threshold, its sentence (for example
   `"11:15-13:00 despite strong sunlight"`) is kept instead.
4. Other stations must be unaffected; `FAULT_INJECTED` only appears for a targeted one.

Injection at night (e.g. 22:00–23:00) still zeroes PV and still warns, but `/health`
stays `healthy`: the service only escalates an injected window whose completed slots had
daylight (mean GHI at least 20 W/m2), because a zero reading at night is normal.

## Service layer and data flow

```text
request
  -> routers/*            validate path/query, call one service, wrap the envelope
  -> services/*           business logic
       replay_clock       Adelaide now -> slot, dates, freshness
       data_adapter       CSV -> sign-normalised day slice -> P0 snapshots
       fault_injection    fault_config.json -> window + PV zeroing
       strategy_engine    strategy/codes over one recorded day -> P1 snapshots
       state_store        in-memory settings + latest simulation
       fault_service      detector over 29 recorded days + today
       fault_detection    the real analysis (patterns, trends, explanation)
       assistant_service  server-built context -> Gemini -> sanitised answer
       envelope/errors    the response and error shapes
  -> strategy/codes       battery.py, strategy.py, forecast.py, optimizer.py, backtest.py
  -> strategy/data        *_clean.csv, prices_aemo.csv, weather_cache.pkl, forecasts
```

| Module | Responsibility |
|---|---|
| `main.py` | App assembly, CORS `*`, lifespan warm-up, `X-Request-ID`, four error handlers |
| `config.py` | Paths, station list, slot/freshness constants, replay date, Gemini settings |
| `services/replay_clock.py` | Slot math, strict date parsing, slot start timestamps, freshness |
| `services/data_adapter.py` | Station catalogue, CSV days, day mapping, sign normalisation, overview and market |
| `services/strategy_engine.py` | Baseline/MPC runs, costs, financial summary, battery detail, recommendation, simulate, settings |
| `services/state_store.py` | Thread-safe settings and simulation store, mode/goal/reserve validation |
| `services/fault_injection.py` | Reads and normalises `fault_config.json`, applies the PV fault |
| `services/fault_service.py` | Builds the 30-day analysis frame and the `/health` payload |
| `services/fault_detection.py` | Pattern detection, learned limits, trends, explanations |
| `services/assistant_service.py` | Grounding context, Gemini call, JSON parsing, sanitising, history, evidence |
| `schemas/contract.py` | Every request/response model, `SCHEMA_VERSION = "0.1"` |
| `routers/` | Thin HTTP adapters; no business logic |
| `tests/` | Standalone `unittest` scripts against real CSVs (Gemini mocked) |

Notable implementation details:

- Runs over one recorded day are cached (`lru_cache` in `strategy_engine.run_day`), so
  repeated P1 calls are cheap once the day is prepared.
- `/simulate` and the assistant's proposal share `window_benefit`, so both cost exactly
  the same inclusive window and neither invents a benefit when the baseline fails.
- `source` always names the dataset, e.g. `SBX-003_clean.csv`,
  `prices_aemo.csv + weather_cache.pkl`, `backtest.py: baseline vs save_money`,
  `optimizer.MPCController (balanced)`.

## Tests

Four standalone `unittest` scripts (no pytest, no network, no API key — Gemini is
mocked). They run against the real recorded CSVs from the repo root:

```sh
python backend/tests/test_p0_services.py
python backend/tests/test_p1_services.py
python backend/tests/test_fault_service.py
python backend/tests/test_p2_assistant.py
```

| File | Tests | What it covers |
|---|---|---|
| `test_p0_services.py` | 20 | Envelope/date rules, sign handling, day slicing, overview, market, error codes |
| `test_p1_services.py` | 27 | Costs, baseline vs strategy, recommendation, `/simulate` validation, settings |
| `test_fault_service.py` | 9 | `fault_config.json` parsing, window application, energy balance, warnings |
| `test_p2_assistant.py` | 18 | Grounding, sanitising, stale context, proposals, `/chat` via `TestClient` |

Verified on this machine: **74 tests, all passing** (P0 ≈ 24 s, P1 ≈ 34 s, P2 ≈ 31 s,
fault ≈ 2 s; the first P1/P2 run may include forecast preparation). A non-zero exit code
means a failure; each script prints `OK` or a traceback.

PowerShell note: `unittest` writes its report to stderr, which Windows PowerShell shows
as red error records even when tests pass. Use `cmd` to merge the streams, or redirect
to a file:

```powershell
cmd /c "python backend\tests\test_p0_services.py 2>&1"
python backend\tests\test_p0_services.py 2> $env:TEMP\p0.log; Write-Output "exit=$LASTEXITCODE"
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: No module named 'config'` | Started from the repo root. `cd backend` and run `uvicorn main:app` |
| `ModuleNotFoundError: No module named 'battery'` | `config` was not imported first; run through `main.py` as documented |
| `[Errno 10048] address already in use` | Another server is on the port; use `--port 8001` and update the frontend proxy |
| All dashboard panels show `Unknown` / `Live data unavailable` | Backend not running, or not on `127.0.0.1:8000` (the Vite proxy target) |
| `/recommendation` hangs on the first call | Forecast warm-up is still running; watch the uvicorn console, or set `AIVY_PRELOAD=0` and accept a slow first call |
| `expected_peak_price_time` is `null` in `/market` | It reads the warm-up's forecast cache and never builds it, so it stays `null` until the warm-up has prepared that station |
| `503 NO_DATA` from overview/health | No completed slot yet (00:00–00:04), or a recording CSV is missing/empty |
| `502 AI_SERVICE_ERROR` from `/chat` | No internet, key rejected, model names unavailable, or the reply was not JSON. `retryable: true` |
| `400 INVALID_DATE` on a past date | Date format is strict and future dates are rejected |
| Settings reset after restart | By design: `state_store` is in-memory only |
| Fault seems to do nothing | Window already passed, station not targeted, `enabled` is not `true`, or `fault_type` is not `pv_zero`; check the server log |
| `FAULT_INJECTED` appears but `/health` stays `healthy` | Fewer than 30 injected slots have completed, or the window fell at night (no daylight to compare against) |

## Known limitations

- **Simulation only.** `/simulate`, `/strategy` PUT and the assistant never control a
  device; `device_control: false` is returned everywhere it matters.
- **Recorded replay, not live telemetry.** "Today" is `AIVY_REPLAY_DATE` (default
  `2026-09-16`), replayed against the real Adelaide clock; past days are archived
  recordings or deterministic mappings.
- **In-memory state.** Strategy settings and chat history reset on restart and are not
  shared between processes; run a single worker.
- **No auth, CORS `*`.** The service is a demo/lab API; put it behind a gateway before
  exposing it beyond localhost.
- **Wholesale prices only.** `import` and `feed_in` are the same AEMO spot price; network
  tariffs, GST and retailer FIT are not modelled.
- **Fixed 288-slot grid.** On a DST-change day the wall clock repeats/skips an hour;
  the API keeps 288 evenly named slots instead of 276/300.
- **Fault injection is limited to `pv_zero`.** It is deliberately visible and is not a
  hardware or communication fault simulator.
- **Assistant depends on an external LLM.** Replies can be unavailable (`502`) and
  proposals are constrained to our modes, goals, reserve ranges and 30-minute window.
- **Single-day model runs.** P1 runs the planner over one recorded day at a time; it is
  not a multi-day rolling forecast service.

For the frontend mapping and UI behaviour, see `../frontend/README.md` and
`../frontend/src/api/README.md`. For the strategy, backtest and assistant notebooks,
see `../strategy/README.md`.

