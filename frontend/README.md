# AIVY frontend

React 19 single-page app for the AIVY smart-home energy demo. Two routes, one thin
API layer, no server-side rendering: a live **Home Energy Dashboard** and a grounded
**AI Energy Assistant**, both reading the FastAPI backend in `../backend`.

Ported from `../prototype` to function components, hooks, immutable state updates,
React Router and SCSS compiled by Vite. No class components, no HTML injection and no
manual DOM-based page switching.

- [Quick start](#quick-start)
- [Scripts](#scripts)
- [How it talks to the backend](#how-it-talks-to-the-backend)
- [Routes](#routes)
- [Directory map](#directory-map)
- [Where state lives](#where-state-lives)
- [Dashboard](#dashboard)
- [AI Assistant](#ai-assistant)
- [Library helpers](#library-helpers)
- [Components](#components)
- [Styling](#styling)
- [Tests and lint](#tests-and-lint)
- [Build and deploy](#build-and-deploy)
- [Troubleshooting](#troubleshooting)
- [Conventions](#conventions)

## Quick start

```sh
cd frontend
nvm use            # .nvmrc -> Node 22; package.json engines: >=22.12.0
npm install
npm run dev        # http://localhost:5173
```

Start the backend first (`uvicorn main:app --reload` on `127.0.0.1:8000`, see
`../backend/README.md`). `vite.config.js` proxies `/api` there, so the browser only
ever talks to the Vite origin and CORS never enters the picture.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server on :5173 with HMR |
| `npm run build` | production bundle into `dist/` |
| `npm run preview` | serves the built `dist/` |
| `npm run lint` | `eslint .` (flat config, see `eslint.config.js`) |
| `npm test` | `node --test "src/**/*.test.js"` - 16 unit tests |

## How it talks to the backend

- The app always calls same-origin `/api/...` (`src/api/client.js`).
- In development `vite.config.js` proxies `/api` to `http://127.0.0.1:8000`.
- For any other origin (built bundle, remote backend) set `VITE_API_BASE_URL`, e.g.
  `VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev`. Vite inlines `VITE_*` at
  build time, so for `npm run build` it must be set before the build, not after.
- `client.js` is the only module that calls `fetch`: JSON body only when one is
  passed, `signal` forwarded, and the raw response is parsed once.
- **Success** is the backend envelope `{ data, meta }`. The whole envelope is stored
  in state; pages read `.data` and `.meta.data_status` / `.meta.data_timestamp`.
- **Failure** (non-2xx) throws `Error(payload.error.message)`, falling back to
  `Request failed with status N`. Aborted calls reject with `AbortError`, which every
  caller ignores.
- There is deliberately **no request timeout**. The first `/financial-summary` runs
  two whole backtests and the first `/recommendation` waits for the forecast warm-up;
  a global `AbortSignal.timeout` would kill both. Cancellation is per call, via the
  `AbortController` each hook owns (unmount, station switch).

## Routes

| Path | Component | Purpose |
|---|---|---|
| `/` | `pages/Dashboard.jsx` | Home Energy Dashboard |
| `/ai-assistant` | `pages/AIAssistant.jsx` | AI Energy Assistant |
| `*` | `<Navigate to="/" replace />` | unknown links fall back to Home |

`BrowserRouter` (real history) wraps the app in `main.jsx`, so back/forward work.
A production host must rewrite unknown paths to `index.html`, otherwise a refresh on
`/ai-assistant` is a 404.

## Directory map

```
src/
  main.jsx                 StrictMode + BrowserRouter + both stylesheets
  App.jsx                  station state, every polling hook, the routes
  api/
    client.js              single fetch wrapper (envelope + error message)
    dashboard.js           stations, overview, market, energy-history, health
    strategy.js            strategy GET/PUT, recommendation, financial-summary, simulate
    assistant.js           POST /api/assistant/chat
    index.js               barrel file - components import from here only
    README.md              per-endpoint contract notes
  pages/
    Dashboard.jsx          / : live flow, exchange, chart, savings, price, weather, health
    AIAssistant.jsx        /ai-assistant : chat, proposal card, strategy settings
  components/
    Header.jsx             brand, nav, Adelaide weather, system state
    EnergyFlow.jsx         React Flow live energy diagram
    EnergyChart.jsx        ECharts day chart with a Today / week / month range
    PriceHistoryChart.jsx  ECharts hourly spot-price line
    Message.jsx            one chat bubble with its evidence chips
    ApplyButton.jsx        "Apply in Simulation" button states
  lib/
    energyData.js          labels, formatters, Adelaide date, series builders
    strategy.js            mode/goal/action labels, plan card, simulate payload
    chatStorage.js         localStorage thread, one per station
    *.test.js              node:test unit tests (16)
  styles.scss              base prototype look: shell, cards, chat
  layout.scss              layout overrides, React Flow styles, responsive rules
```

Dependencies: React 19, React Router 7, antd 6 (`Select` and `Alert` only), ECharts 6
(imported per module: `echarts/core` plus explicit `use([...])` registrations),
`@xyflow/react` 12 for the flow diagram, Sass, Vite 8, ESLint 9.

## Where state lives

`App.jsx` owns every server value. Pages and components are presentational and keep
only UI state (open form, selected range, draft settings, chat input). The three hooks
below are the whole polling strategy; each owns an `AbortController` and a `setTimeout`
loop, and clears both on unmount or station switch.

| Hook | Data | Poll | Retry | On station switch |
|---|---|---|---|---|
| `useEnergyData(stationId)` | `/overview`, `/market`, `/strategy` | 60 s | 10 s | whole slot set replaced |
| `useFinancialSummary(stationId, revision)` | `/financial-summary` | 10 min | 60 s | number cleared at once |
| `useStationHealth(stationId)` | `/health` | 5 min | 60 s | cleared and refetched |

### `useEnergyData` - the P0/P1 independence rule

`overview` is awaited, because the four flow cards, the exchange card and the header all
need it. `market` and `strategy` go through a local `settle()` helper that writes **its
own slot the moment that one response lands**:

```js
settle(getMarket(stationId, { signal: controller.signal }), "market");
settle(getStrategy(stationId, { signal: controller.signal }), "strategy");
// then: await getOverview(...) and store overview + error
```

- A failed or slow P1 call leaves its own panel at the em dash. It never blanks the four
  P0 cards and never blocks the retry timer.
- One cycle ends with `setTimeout(load, failed ? RETRY_MS : POLL_MS)`, where `failed`
  only reflects `/overview`, so a backend started after the page recovers within 10 s
  without an F5.
- `setEnergy` writes are skipped while `controller.signal.aborted`, so a late answer for
  the previous station can never overwrite the current one.
- `setStrategy(envelope)` is returned by the hook, so a `PUT /strategy` answer replaces
  the cached strategy immediately instead of waiting for the next poll.

Do **not** put these three back into one `Promise.all` or `Promise.allSettled`. That was
the original bug: `/market` used to build the multi-day forecast inside the request and
never answered, so a single hanging promise held every card hostage. The server side of
that fix is described in `../backend/README.md` (the `/recommendation` warm-up note).

### `useFinancialSummary(stationId, revision)`

- 10 minute cadence: the first call simulates two whole backtests, so it is the most
  expensive endpoint on the dashboard.
- A station switch clears the figure immediately, because it belongs to the previous home.
- `revision` is bumped by a mode change. That re-runs the backtests but keeps the previous
  figure on screen, since a 30-60 s gap is better than a flashing dash.

### `useStationHealth(stationId)`

5 minute cadence: a diagnosis only moves when a 30-minute pattern completes. The error
string is kept next to the data, so the card can show it without dropping the badge.

### Mode and station changes

- Strategy mode `Select`: `changeMode(mode)` sends `PUT /api/stations/{id}/strategy` with
  `{ current_mode }`, then calls `setStrategy(envelope)` so Home shows the new mode at
  once, and bumps `financialRevision` because today's savings is baseline vs the active
  mode. A rejected PUT lands in `modeError`, which the dashboard renders as an `Alert`.
- Station `Select`: `setStationId`, which re-runs all three hooks. `AIAssistant` is mounted
  with `key={stationId}`, so it remounts with that station's own thread, and `EnergyChart`
  refetches through its `refreshKey` prop.

## Dashboard

`pages/Dashboard.jsx` renders one scrolling screen: the heading row with the two
`Select`s, the optional error `Alert`s, three metric cards, the Live Energy Flow and
Energy Exchange row, the Today's Energy chart, then four mini cards.

| Block | Read from | Fields used |
|---|---|---|
| Station `Select` | `GET /api/stations` | `data[].id`, `data[].name`; falls back to the current station id if the catalog fails |
| Mode `Select` | `/strategy` | `data.settings.current_mode` |
| Solar / Battery / Grid cards | `/overview` | `data.display.solar_power_kw`, `data.telemetry.pv_w`, `data.telemetry.soc_pct`, `data.display.grid_power_kw`, `data.states.battery`, `data.states.grid` |
| Live Energy Flow | `/overview` | `data.display`, `data.telemetry`, `data.states`, `data.flow.active_directions` |
| Energy Exchange | `/overview` + `/strategy` + `/market` + `/financial-summary` | `data.flow.active_directions`, `meta.data_status`, `settings.current_mode`, `price.import.value`, savings |
| Today's Energy chart | `GET /api/stations/{id}/energy-history?date=` | `data.points[].pv_w / load_w / battery_w / grid_w`, `data.received_points`, `data.expected_points`, `meta.source.mode`, `meta.data_status` |
| Today's Savings | `/financial-summary` | `data.today_savings_aud`, `data.actual_costs.export_earnings_aud`, `data.period.is_partial`, `data.period.intervals` (out of 288) |
| Electricity Price | `/market` | `data.price.import.value` (AUD/kWh, shown in c/kWh), `data.history[]` |
| Weather | `/market` | `data.weather.condition`, `.temperature_c`, `.ghi_w_m2`, `.cloud_cover_pct` |
| System Health | `/health` | `data.status`, `.fault_types`, `.explanation`, `.suspected_sign_issue`, `.energy_balance_violation_pct`, `.fault_injected`, `.fault_window`, `.soc_ceiling_pct`, `.charge_limit_w`, `.days_analysed` |

### Flow directions

The `ROUTES` map covers the seven directions `/overview` can report. Each entry has an
icon, a tone class, an owner-facing label, a note, and the `display` field whose power
value is printed next to it:

| Direction | Label | Value field |
|---|---|---|
| `solar_to_home` | Solar to Home | `home_usage_kw` |
| `solar_to_battery` | Solar to Battery | `battery_power_kw` |
| `solar_to_grid` | Solar to Grid | `grid_power_kw` |
| `battery_to_home` | Battery to Home | `home_usage_kw` |
| `battery_to_grid` | Battery to Grid | `grid_power_kw` |
| `grid_to_home` | Grid to Home | `home_usage_kw` |
| `grid_to_battery` | Grid to Battery | `battery_power_kw` |

An empty array is a real "nothing flowing in this slot" and renders the empty note; an
unknown string is filtered out instead of crashing the list.

### Health card

- `incomplete_day` is filtered out of the chips: it only means today is still running,
  and this card is about faults.
- Pattern names are translated for a household (`sudden_drop_to_zero` becomes "sudden
  drop to zero", `communication_dropout` becomes "reporting stopped", and so on).
- A `suspected_sign_issue` chip explains itself through the `title` attribute, which
  carries `energy_balance_violation_pct` (the share of slots that fail
  `pv = load + battery + grid`).
- When `fault_injected` is true the card shows a **Simulated fault** chip with the
  `fault_window`. The readings are real, the injected drop is not: never present a
  simulated drop as a genuine fault.
- The footnote prints the station's learned `soc_ceiling_pct`, its `charge_limit_w`, and
  how many recorded days formed the baseline.

### Chart ranges

- **Today** is measured: `/energy-history` for the Adelaide calendar date, four series
  averaged per local hour into kW, with a note giving `Replay`/`Recorded`,
  `received/expected` slots and `data_status`.
- **This week** and **This month** are the hard-coded `RANGE_DATA` constants inside
  `EnergyChart.jsx` and are labelled "Illustrative demo data". They are not API data and
  must stay labelled.
- `refreshKey` is `overview.meta.data_timestamp`, so a poll that advances the slot also
  refetches the day series.
- Two known loose ends, documented rather than silently fixed: `RANGE_DATA.Today` is now
  unused (Today always renders `/energy-history`), and the optional `peak` mark line is
  wired up but no live series supplies `peak`, so it does not currently draw.

### Money

`formatAud` is used for every amount, and an unknown amount stays an em dash. Savings
come only from `/financial-summary`, so a failure shows the em dash with the message in
the element's `title` instead of a fake "$0.00".

## AI Assistant

`pages/AIAssistant.jsx` is a three-part screen: the chat column (log, quick questions,
input), the right column (Live Energy, AI Strategy), and the "How AIVY helps you" strip.

### One turn

1. `ask(text)` trims the text, ignores it when empty or while a turn is in flight, and
   appends a user bubble with `crypto.randomUUID()` and an Adelaide `h:mm` stamp.
2. `POST /api/assistant/chat` with `{ station_id, message, conversation_id }`. The id stays
   `undefined` until the first reply, so it only reaches the wire from the second turn on,
   and the backend mints the first one as `conv_xxxxxxxx`.
3. The reply `data` carries `content`, `message_id`, `evidence[]`, `conversation_id` and an
   optional `suggested_strategy`.
4. The conversation id is stored, the AI bubble is appended with its evidence chips, and
   `suggested_strategy` becomes the proposal card.
5. While `asking` is true the input and the quick questions are disabled and the log shows
   an "AIVY is checking your live data" status line.
6. A failure sets `chatError` (rendered with `role="alert"`) and keeps the user bubble, so
   the question is never lost.

The input has `maxLength=1000`, mirroring the backend's `MAX_CHAT_MESSAGE_CHARS`, and is a
plain form, so Enter submits.

### Proposal card

`planCard(proposal)` in `lib/strategy.js` turns the raw proposal into what the card prints:
action (`charge` / `discharge` / `export` / `hold`), the start-end window via `clockLabel`,
mode name, reserve percentage, the estimated benefit (cents below 10c, dollars above), and
the model's reason.

**Apply in Simulation** does two writes, in this order:

1. `POST /api/stations/{id}/simulate` with
   `{ mode: "simulation", strategy: simulationStrategy(proposal) }`. Only five keys are
   forwarded (`type`, `optimisation_goal`, `battery_reserve_pct`, `start_at`, `end_at`);
   anything else the chat reply carries is dropped, because the sandbox contract accepts
   exactly those.
2. `PUT /api/stations/{id}/strategy` with the proposal's mode, reserve and goal, so Home
   and this panel agree with the card instead of drifting apart.

`onStrategyChange(envelope)` pushes the new strategy up to `App` immediately, so Home shows
the new mode without waiting for its poll. The button then reads "Saved to simulation" and
the sandbox message appears under it. A failure sets `applyError` under the button and leaves
the proposal in place, so a retry is one click; a fresh reply clears `saved` so a stale
"Saved" never sits under a different proposal. Nothing here sends a device command: the whole
path is simulation-only, by design.

### Strategy settings

- `GET /strategy` on mount fills the AI Strategy card (mode, reserve, goal).
- "Change settings" opens a draft form: mode `select` (`MODE_OPTIONS`), battery reserve
  `number` (10-100, step 1), goal `select` (`GOAL_LABELS`).
- Save sends `PUT /strategy` with those three fields; the response replaces the local
  settings and is pushed up through `onStrategyChange`. Cancel drops the draft. A rejected
  PUT shows `settingsError` with `role="alert"`.
- Editing happens on a draft object, so a half-typed reserve never reaches the server or the
  card above it.

### Right column

Live Energy repeats `overview.data.display` / `.telemetry` / `.states` plus
`market.data.price.import` and `market.data.weather`. These are the same values the
dashboard shows, so a failed `/market` only turns the price and weather rows into unknown
values; the flow numbers still render.

### Persistence

| Layer | Where | Lifetime | Limit |
|---|---|---|---|
| Transcript, conversation id, proposal | `localStorage["aivy.chat.<stationId>"]` | survives reloads, one thread per station and browser | last 50 messages |
| Model context | backend `_history` dict keyed by `conversation_id` | process memory only | last 10 turns |

`loadChat` runs once inside a `useState` initialiser; `saveChat` runs whenever `stationId`,
`conversationId`, `messages` or `proposal` changes. Unreadable JSON, a wrong shape, private
mode or a full quota all degrade to "no stored thread" and never throw.

Because the server context lives in RAM, restarting the backend (including `--reload`)
leaves the transcript on screen while the model has forgotten the earlier turns. That is
expected behaviour: the backend README says the same under its `/api/assistant/chat`
endpoint notes.

## Library helpers

Everything in `src/lib` is pure: no React, no `fetch`, and no direct DOM access (the storage
helpers receive the storage object as an argument). That is what lets the tests run in plain
Node.

### `energyData.js`

| Export | Behaviour |
|---|---|
| `CONDITION_LABELS`, `BATTERY_STATE_LABELS`, `GRID_STATE_LABELS`, `MODE_LABELS`, `STATUS_LABELS` | owner-facing labels for the API's enum strings; call sites fall back when a key is unknown |
| `adelaideToday(now)` | `YYYY-MM-DD` in `Australia/Adelaide` via `en-CA`, never the browser's zone |
| `formatNumber(value, digits)` | fixed digits, em dash for `null`/`undefined` |
| `centsPerKwh(audPerKwh)` | AUD/kWh times 100, null-safe |
| `formatAud(amount)` | `$4.82` or `-$0.28`, em dash when missing |
| `hourLabel(hour)` | `0` gives `12 AM`, `12` gives `12 PM`, otherwise `h AM` / `h PM` |
| `activeFlowIds(directions, edgeDirections)` | every edge when `directions` is `null` (no data yet), none when it is `[]`, otherwise the matching ids |
| `priceSeries(points)` | `/market` history bucketed into 24 hourly averages in c/kWh, 2 decimals, empty hours `null` |
| `hourlySeries(points)` | `/energy-history` points bucketed into four 24-hour series in kW, 3 decimals, empty hours `null` |

Two rules worth keeping:

- The hour is read from the timestamp text (`/T(\d{2}):/`), not from `Date.getHours()`, because
  the API already sends Adelaide wall time while the browser may sit in another timezone.
- A missing hour stays `null` so ECharts draws a gap. Filling it with `0` would invent a 12 kW
  drop at midnight and make a broken sensor look like a real reading.

### `strategy.js`

| Export | Behaviour |
|---|---|
| `MODE_OPTIONS` | the three owner modes in menu order |
| `GOAL_LABELS`, `ACTION_LABELS` | goal and action wording |
| `clockLabel(iso)` | `T14:05` becomes `2:05 PM`, em dash for junk |
| `benefitLabel(amount)` | below 0.1 becomes cents (`4.2c`), otherwise dollars (`$2.40`) |
| `simulationStrategy(proposal)` | exactly the five keys the sandbox accepts |
| `planCard(proposal)` | `null` for no proposal, otherwise the card's six fields |

### `chatStorage.js`

`KEY = aivy.chat.<stationId>`, `MAX_MESSAGES = 50`. Both functions take the storage object
instead of reading `window.localStorage` themselves, which keeps them testable in Node and
lets the caller decide what "storage" means.

## Components

| Component | Props | Notes |
|---|---|---|
| `Header` | `overview`, `market` | brand link, two `NavLink`s (`end` on Home), Adelaide weather, and "System Online" / "System Delayed" from `states.system` |
| `EnergyFlow` | `overview` | the React Flow diagram, details below |
| `EnergyChart` | `stationId`, `refreshKey` | its own `/energy-history` request plus the range `Select` |
| `PriceHistoryChart` | `series` | ECharts line from `priceSeries(...)`; marks the newest non-null hour, tooltip says "no quote" for a null |
| `Message` | `message` | bubble; `role === "ai"` gets the bot avatar and the evidence list; `%`, `AUD/kWh` and `AUD` each format differently |
| `ApplyButton` | `applied`, `busy`, `onApply` | "Apply in Simulation", then "Saving", then a tick with "Saved to simulation"; disabled once applied |

### EnergyFlow specifics

- Four nodes (Solar, Home, Battery, Grid) plus two invisible boundary nodes that only widen
  the `fitView` box, and ten handles per node so each edge can leave and enter on a
  different side.
- Seven edges are **always** mounted; `active_directions` only toggles their rendering, so the
  topology never disappears for a frame.
- The particles sit in an always-mounted group that is hidden by CSS when idle. A removed
  `animateMotion` does not restart when Chrome re-inserts it, which used to freeze the
  animation after a station switch.
- Nodes are memoised on `[display, telemetry, states]`. React Flow re-adopts a node (dropping
  its measured size and handle bounds) whenever the node object is a new reference, and a
  node with no measured size is rendered hidden, blanking the whole graph until an F5.
  `initialWidth`/`initialHeight` (150x64, matching the CSS box) cover the frames before the
  first measurement.
- Pan, zoom, drag and selection are all off: the diagram is a picture, not an editor.
  `proOptions.hideAttribution` hides the React Flow badge for the demo.

## Styling

Two stylesheets, both imported once in `main.jsx`, both plain SCSS (no CSS modules, no
Tailwind).

| File | Responsibility |
|---|---|
| `styles.scss` | the base prototype look: reset, DM Sans, the 16:9 stage, cards, chat, health and price panels |
| `layout.scss` | later layout work layered on top: readable desktop type, grid layouts, React Flow nodes and edges, chat sizing, reduced-motion, two breakpoints |

- The app is a **fixed 16:9 stage**. `.prototype-shell` is `width: min(100vw, 177.7778vh)`
  and `height: min(100vh, 56.25vw)` with a 980x551 minimum, centred with a shadow. `body`
  has `overflow: hidden`, so the page itself never scrolls; each `.screen` scrolls
  internally with `scrollbar-gutter: stable`. A `max-aspect-ratio: 16/9` branch at the end of
  `styles.scss` drops the minimums and switches to a `vw` type scale so the stage keeps its
  shape on a narrower window.
- The type scale is in px and tuned for a desktop demo (the letterboxed branch above is the
  only `vw` exception); `layout.scss` raises body copy from 14px to 15px.
- Breakpoints: `1000px` (smaller brand, metrics and assistant columns) and `700px`
  (single-column dashboard, hidden top status, two-column metric grid).
- `@media (prefers-reduced-motion: reduce)` stops the flow particles.
- Fonts: DM Sans is `@import`ed from Google Fonts at runtime and falls back to Arial offline.
  No font files are vendored.
- antd supplies only `Select` and `Alert`; everything else is hand-written markup with class
  names. React Flow ships its own `dist/style.css`, imported inside `EnergyFlow.jsx`; ECharts
  needs no stylesheet.
- Accessibility is carried in the markup: `aria-label` on both `Select`s, `role="img"` plus a
  description on both charts and on the flow diagram, `role="log"` with `aria-live="polite"`
  on the chat log, `role="status"` on the thinking line, `role="alert"` on every error.

## Tests and lint

```sh
npm test        # node --test "src/**/*.test.js"
npm run lint
```

`node:test` plus `node:assert/strict`, no jsdom and no testing library: only the pure helpers
are unit-tested, 16 tests across three files.

| File | Tests | Covers |
|---|---|---|
| `lib/energyData.test.js` | 7 | 24-hour bucketing for both series, null gaps, c/kWh conversion, AUD and dash formatting, `adelaideToday` across the UTC midnight edge, `activeFlowIds` for null vs empty vs filtered |
| `lib/strategy.test.js` | 5 | `simulationStrategy` forwards exactly five keys, `planCard` wording, `benefitLabel` cents versus dollars, `clockLabel` midnight, noon and junk, the three modes and three goals |
| `lib/chatStorage.test.js` | 4 | round trip per station, threads do not leak between stations, junk JSON and wrong shapes read as empty, a throwing `setItem` still succeeds |

Components are covered by a manual checklist instead of rendered tests, on purpose: adding
jsdom and React Testing Library to a demo costs more than it would catch, and the risky logic
already lives in `lib`.

Manual smoke checklist before a demo:

1. Home: the four live-flow cards fill from `/overview`, price and weather from `/market`,
   System Health from `/health`, savings from `/financial-summary`.
2. DevTools Network: no red requests once warm-up is done, and `/market` answers in under a
   second.
3. AI Assistant: ask a quick question, evidence chips appear, Apply in Simulation turns into
   "Saved to simulation".
4. Switch station: cards blank and refill, the chat shows that station's own thread, savings
   recompute.
5. Switch mode: Home shows the new mode immediately, savings re-runs while the old figure
   stays visible.
6. Chart range: Today is measured, week and month say "Illustrative demo data".
7. Narrow the window below 700px: single-column dashboard, no horizontal scrollbar.

Lint rules worth knowing (`eslint.config.js`): ESLint 9 flat config with browser globals,
`js.configs.recommended`, the recommended `react-hooks` rules (so a missing dependency array
entry is an error, not a style nit), `react-refresh/only-export-components` as a warning, and
`no-unused-vars` as an error with `varsIgnorePattern: ^[A-Z]`, which lets capitalised
identifiers go unused (component imports, placeholder constants). `dist` and `.vite` are
ignored.

## Build and deploy

```sh
npm run build     # writes dist/
npm run preview
```

- Output is a static `dist/index.html` plus hashed assets, so any web server or CDN works.
- The build prints a chunk-size warning: one JS bundle of about 1.27 MB (421 kB gzipped),
  because ECharts, React Flow and antd all land in it. It is a warning, not a failure, and
  this demo does not need code splitting.
- The host must serve `index.html` for unknown paths (SPA rewrite), otherwise a refresh on
  `/ai-assistant` returns 404.
- `/api` must be reverse-proxied to the FastAPI process on the same origin, or the bundle must
  be built with `VITE_API_BASE_URL` pointing at the backend, in which case the backend has to
  allow that origin.
- `VITE_*` values are inlined at build time, so changing them means rebuilding.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every panel shows dashes, "Unknown" and the red "Live data unavailable" alert | nothing is answering behind the `/api` proxy | start the backend on `127.0.0.1:8000`; the page retries every 10 s, no F5 needed |
| The four flow cards are fine, but price, weather and the price chart stay empty | `/market` failed or is still slow (a cold backend warming up its forecast cache) | `curl http://127.0.0.1:8000/api/stations/SBX-001/market`; the panel recovers on the next 60 s poll |
| System Health keeps its "Checking" label for a long time | `/health` replays 29 recorded days through `fault_detection.py` on first use | wait for the first answer; if it fails, the card prints the error |
| Today's Savings keeps its "Calculating today's savings" label | the first `/financial-summary` runs two whole backtests | wait; it is cached per station and date afterwards |
| The chart note says "Illustrative demo data" | "This week" and "This month" are constants, by design | only "Today" is measured |
| The flow diagram is blank after switching stations | node objects were rebuilt, so React Flow re-adopted dimensionless nodes | keep the `useMemo` on `[display, telemetry, states]` and the `initialWidth`/`initialHeight` |
| Particles are frozen or every edge looks grey | `active_directions` is empty (a real "nothing flowing" slot) or `/overview` has not landed yet | inspect `data.flow.active_directions` in the response |
| Chat answers "The assistant could not answer just now." | the backend returned an error (missing key, quota, unparsable reply) | the text is the backend's own message; see the backend README |
| Transcript is still on screen after a backend restart, but the model forgot everything | the server keeps turns in process memory | expected: a restart loses the conversation context |
| Red requests in DevTools right after a cold start | `/recommendation` and the first `/market` wait for the forecast warm-up | expected once; the polls recover on their own |
| A CORS error appears | the browser was pointed straight at `:8000` through `VITE_API_BASE_URL` | use the Vite proxy, or allow the frontend origin on the backend |

## Conventions

- New endpoints go in `src/api/<area>.js` and are re-exported from `src/api/index.js`. No
  component calls `fetch` directly.
- Server state lives in the `App.jsx` hooks; components receive props. Component state is UI
  state only.
- One request per state slot. Never gate a panel on another endpoint, and never bundle
  independent endpoints into a single `Promise.all`.
- No global fetch timeout. Cancellation is per call, through the hook's `AbortController`.
- Adelaide wall clock everywhere: `adelaideToday()`, `clockLabel()`, `Intl` with
  `timeZone`, and hour bucketing read from the timestamp text.
- Unknown is not zero: `null` renders as the em dash, missing hours stay `null`, and a failed
  request keeps the previous or skeleton state instead of inventing a value.
- `key={stationId}` for anything that must be per station, `refreshKey` for anything that must
  follow the poll.
- `simulationStrategy()` is the only path from a chat proposal to the sandbox; do not widen it.
- No HTML injection, no class components, no direct DOM manipulation beyond the chart and
  graph containers.
- Keep the demo copy honest: "Simulated fault" for injected faults, "Illustrative demo data"
  for the hard-coded ranges.
