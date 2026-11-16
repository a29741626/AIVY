# AIVY

React migration of `../prototype`, using function components, hooks, immutable state updates React Router and SCSS (compiled by Sass through Vite). No class components, HTML injection or manual DOM-based page switching.

## Development

```sh
nvm use
npm install
npm run dev
```

Node 22.12+ is required. `npm run build`, `npm run lint`, and `npm run preview` are available.

The dev server proxies `/api` to the FastAPI backend on `http://127.0.0.1:8000`
(see `vite.config.js`), so start the backend first. Without it every panel falls
back to `Unknown` / `Live data unavailable`.

## Routes

- `/`: Home Energy Dashboard
- `/ai-assistant`: AI Energy Assistant

Shared navigation supports browser back/forward. Production hosting must rewrite unknown routes to `index.html` for direct links and refreshes.

## Structure

- `src/pages`: the two route components
- `src/components`: header, canvas views, messages and shared strategy action
- `src/lib`: isolated canvas drawing, energy formatters and the assistant's strategy-card helpers
- `src/api`: one function per backend endpoint (dashboard, strategies, assistant)
- `src/App.jsx`: routes and shared polling state
- `src/styles.scss`: base and component styles
- `src/layout.scss`: layout overrides and responsive styles

Chat answers now come from the P2 backend (`POST /api/assistant/chat`), grounded in
this home's live readings, and stay in the page while it is mounted. "Apply in
Simulation" posts the proposal to `POST /api/stations/{id}/simulate`, which saves it
to the simulator and never sends a device command. Range selection uses illustrative profiles rather than real historical aggregation. Chat and strategy state persist during navigation and reset on reload. The original prototype's scrollable desktop layout is retained, with narrow-screen adjustments. Canvas effects disconnect observers and cancel animation frames on unmount.
