import { useEffect, useMemo, useState } from "react";
import { Alert, Select } from "antd";
import EnergyFlow from "../components/EnergyFlow";
import EnergyChart from "../components/EnergyChart";
import PriceHistoryChart from "../components/PriceHistoryChart";
import { getStations } from "../api";
import {
  BATTERY_STATE_LABELS,
  CONDITION_LABELS,
  GRID_STATE_LABELS,
  MODE_LABELS,
  STATUS_LABELS,
  centsPerKwh,
  formatAud,
  formatNumber,
  priceSeries,
} from "../lib/energyData";
import { MODE_OPTIONS } from "../lib/strategy";

const ROUTES = {
  solar_to_home: {
    icon: "☀",
    tone: "sun-route",
    label: "Solar → Home",
    note: "Powering household demand",
    field: "home_usage_kw",
  },
  solar_to_battery: {
    icon: "☀",
    tone: "battery-route",
    label: "Solar → Battery",
    note: "Charging with excess solar",
    field: "battery_power_kw",
  },
  solar_to_grid: {
    icon: "☀",
    tone: "grid-route",
    label: "Solar → Grid",
    note: "Export at current tariff",
    field: "grid_power_kw",
  },
  battery_to_home: {
    icon: "▯",
    tone: "battery-route",
    label: "Battery → Home",
    note: "Covering demand from the battery",
    field: "home_usage_kw",
  },
  battery_to_grid: {
    icon: "▯",
    tone: "grid-route",
    label: "Battery → Grid",
    note: "Exporting stored energy",
    field: "grid_power_kw",
  },
  grid_to_home: {
    icon: "♜",
    tone: "grid-route",
    label: "Grid → Home",
    note: "Importing to meet demand",
    field: "home_usage_kw",
  },
  grid_to_battery: {
    icon: "♜",
    tone: "battery-route",
    label: "Grid → Battery",
    note: "Charging from the grid",
    field: "battery_power_kw",
  },
};

const HEALTH_STATUS_LABELS = {
  healthy: "Healthy",
  sub_healthy: "Needs attention",
  broken: "Fault detected",
};

const HEALTH_STATUS_TONES = {
  healthy: "ok",
  sub_healthy: "warn",
  broken: "bad",
};

// fault_detection.py's pattern names, in words a household would read.
const FAULT_TYPE_LABELS = {
  sudden_drop_to_zero: "sudden drop to zero",
  midday_flattening: "midday flattening",
  abnormal_spike: "abnormal spike",
  communication_dropout: "reporting stopped",
  missing_data: "missing readings",
  incomplete_day: "partial day",
};

export default function Dashboard({
  stationId,
  onStationChange,
  overview,
  market,
  strategy,
  financial,
  financialError,
  health,
  healthError,
  error,
  onModeChange,
  modeError,
}) {
  const [stations, setStations] = useState([]);
  const [catalogError, setCatalogError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    getStations({ signal: controller.signal })
      .then((body) => setStations(body.data))
      .catch((requestError) => {
        if (requestError.name !== "AbortError") {
          setCatalogError(requestError.message || "Unable to load stations.");
        }
      });
    return () => controller.abort();
  }, []);

  const view = overview?.data;
  const display = view?.display;
  const telemetry = view?.telemetry;
  const states = view?.states;
  const directions = view?.flow.active_directions ?? [];
  const price = market?.data.price.import;
  const weather = market?.data.weather;
  const status = overview?.meta.data_status ?? "unknown";
  const mode = strategy?.data?.settings?.current_mode;
  const summary = financial?.data;
  // Memoised so each poll rebuilds the chart only when /market itself changes.
  const priceHistory = useMemo(
    () => priceSeries(market?.data.history),
    [market],
  );
  const savingsText = summary ? formatAud(summary.today_savings_aud) : "—";
  const routes = directions.map((direction) => ROUTES[direction]).filter(Boolean);
  const diagnosis = health?.data;
  // incomplete_day only says "today is still running"; the card is about faults.
  const faultTypes = (diagnosis?.fault_types ?? []).filter(
    (fault) => fault !== "incomplete_day",
  );
  const stationOptions = (stations.length
    ? stations
    : [{ id: stationId, name: stationId }]
  ).map((station) => ({ value: station.id, label: station.name }));
  const solarState =
    telemetry?.pv_w == null
      ? "No reading"
      : telemetry.pv_w > 50
        ? "Generating"
        : "Not generating";

  return (
    <section
      id="home"
      className="screen active-screen"
      aria-label="Home Energy Dashboard"
    >
      <div
        className="heading"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <div>
          <h1>
            Good morning <span>👋</span>
          </h1>
          <p>Here's how your home energy is performing today.</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Select
            aria-label="Station"
            value={stationId}
            onChange={onStationChange}
            options={stationOptions}
            size="small"
            style={{ width: 190 }}
          />
          <Select
            aria-label="Strategy mode"
            value={mode ?? undefined}
            onChange={onModeChange}
            options={MODE_OPTIONS}
            placeholder="Mode"
            size="small"
            style={{ width: 150 }}
          />
        </div>
      </div>
      {(error || catalogError) && (
        <Alert
          type="error"
          showIcon
          message="Live data unavailable — the /api backend did not respond"
          description={`${error || catalogError}. Start the backend on 127.0.0.1:8000; this panel retries every 10 s.`}
          style={{ marginTop: 8 }}
        />
      )}
      {modeError && (
        <Alert
          type="error"
          showIcon
          message="Strategy mode not changed"
          description={modeError}
          style={{ marginTop: 8 }}
        />
      )}
      <div className="metric-grid">
        <article className="metric">
          <span className="icon solar">☀</span>
          <div>
            <b>Solar</b>
            <strong>
              {formatNumber(display?.solar_power_kw)} <small>kW</small>
            </strong>
            <em>● {solarState}</em>
          </div>
        </article>
        <article className="metric">
          <span className="icon homeicon">⌂</span>
          <div>
            <b>Home Usage</b>
            <strong>
              {formatNumber(display?.home_usage_kw)} <small>kW</small>
            </strong>
            <em className="blue">● Current consumption</em>
          </div>
        </article>
        <article className="metric">
          <span className="icon battery">▯</span>
          <div>
            <b>Battery</b>
            <strong>
              {formatNumber(telemetry?.soc_pct, 0)}
              <small>%</small>
            </strong>
            <em>
              ● {BATTERY_STATE_LABELS[states?.battery] ?? "Unknown"}
            </em>
          </div>
        </article>
        <article className="metric">
          <span className="icon grid">♜</span>
          <div>
            <b>Grid</b>
            <strong>
              {formatNumber(display?.grid_power_kw)} <small>kW</small>
            </strong>
            <em>● {GRID_STATE_LABELS[states?.grid] ?? "Unknown"}</em>
          </div>
        </article>
      </div>

      <div className="dashboard-top">
        <article className="card flow-card">
          <h2>
            Live Energy Flow <span className="info">i</span>
          </h2>
          <p className="flow-note">
            Measured from the recorded day — the strategy mode changes the
            plan, not these readings.
          </p>
          <EnergyFlow overview={overview} />
        </article>
        <article className="card exchange-card">
          <div className="exchange-title">
            <h2>Energy Exchange</h2>
            <span>● {STATUS_LABELS[status] ?? status}</span>
          </div>
          <div className="exchange-hero">
            <div>
              <small>Active strategy</small>
              <strong>{MODE_LABELS[mode] ?? "—"}</strong>
              <p>Optimising every available energy path.</p>
            </div>
            <span className="exchange-mark">↗</span>
          </div>
          <div className="route-list">
            {routes.length === 0 && (
              <small>No active energy flows in this slot.</small>
            )}
            {routes.map((route) => (
              <div key={route.label}>
                <span className={`route-icon ${route.tone}`}>{route.icon}</span>
                <p>
                  <b>{route.label}</b>
                  <small>{route.note}</small>
                </p>
                <strong>{formatNumber(display?.[route.field])} kW</strong>
              </div>
            ))}
          </div>
          <div className="exchange-footer">
            <span>Estimated benefit today</span>
            <strong title={financialError || undefined}>{savingsText}</strong>
            <small>
              Current price&nbsp;{" "}
              {formatNumber(centsPerKwh(price?.value))} c/kWh
            </small>
          </div>
        </article>
      </div>
      <EnergyChart stationId={stationId} refreshKey={overview?.meta.data_timestamp} />
      <div className="bottom-grid">
        <article className="mini-card savings">
          <span className="icon money">$</span>
          <div>
            <b>Today's Savings</b>
            <strong title={financialError || undefined}>{savingsText}</strong>
            <em>
              {summary
                ? `+${formatAud(summary.actual_costs?.export_earnings_aud)} earned from grid export`
                : financialError || "Calculating today's savings…"}
            </em>
            <small>
              Compared with standard
              <br />
              battery operation
              {summary?.period?.is_partial && (
                <>
                  <br />
                  {summary.period.intervals}/288 slots so far
                </>
              )}
            </small>
          </div>
          <div className="spark"></div>
        </article>
        <article className="mini-card price price-history-card">
          <div className="price-summary">
            <span className="icon price-icon">▯</span>
            <div>
              <b>Electricity Price</b>
              <strong>
                {formatNumber(centsPerKwh(price?.value))} <small>c/kWh</small>
              </strong>
              <small>Current Price</small>
            </div>
          </div>
          <div className="price-history-panel">
            <div className="price-history-heading">
              <b>Today's prices</b>
              <small>c/kWh</small>
            </div>
            <PriceHistoryChart series={priceHistory} />
          </div>
        </article>
        <article className="mini-card weather">
          <span className="icon solar">☀</span>
          <div>
            <b>Weather</b>
            <strong>
              {formatNumber(weather?.temperature_c, 0)}
              <small>°C</small>
            </strong>
            <small>{CONDITION_LABELS[weather?.condition] ?? "Unknown"}</small>
            <em>
              GHI&nbsp; {formatNumber(weather?.ghi_w_m2, 0)} W/m²
              <br />
              Cloud Cover&nbsp; {formatNumber(weather?.cloud_cover_pct, 0)}%
            </em>
          </div>
        </article>
        <article className="mini-card health-card">
          <div className="health-head">
            <b>System Health</b>
            <span
              className={`health-badge ${
                HEALTH_STATUS_TONES[diagnosis?.status] ?? "idle"
              }`}
            >
              ● {HEALTH_STATUS_LABELS[diagnosis?.status] ?? "Checking…"}
            </span>
          </div>
          <p className="health-explain" title={diagnosis?.explanation}>
            {healthError ||
              diagnosis?.explanation ||
              "Running the fault detector over today's readings…"}
          </p>
          <div className="health-tags">
            {faultTypes.map((fault) => (
              <span key={fault}>{FAULT_TYPE_LABELS[fault] ?? fault}</span>
            ))}
            {diagnosis?.suspected_sign_issue?.length > 0 && (
              <span
                title={`${formatNumber(
                  diagnosis.energy_balance_violation_pct,
                  1,
                )}% of slots fail pv = load + battery + grid`}
              >
                {diagnosis.suspected_sign_issue.join("/")} sign
              </span>
            )}
          </div>
          <small className="health-meta">
            {diagnosis
              ? `Battery full at ${formatNumber(
                  diagnosis.soc_ceiling_pct,
                  1,
                )}% · max charge ${formatNumber(
                  diagnosis.charge_limit_w / 1000,
                  1,
                )} kW · ${diagnosis.days_analysed}-day baseline`
              : "Recorded-day baseline"}
          </small>
        </article>
      </div>
    </section>
  );
}
