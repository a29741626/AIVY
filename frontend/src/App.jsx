import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Header from "./components/Header";
import Dashboard from "./pages/Dashboard";
import AIAssistant from "./pages/AIAssistant";
import {
  getBattery,
  getFinancialSummary,
  getMarket,
  getOverview,
  getRecommendation,
  getStationHealth,
  getStrategy,
  updateStrategySettings,
} from "./api";
import { adelaideToday } from "./lib/energyData";

const DEFAULT_STATION = "SBX-001";
const POLL_MS = 60_000;
const RETRY_MS = 10_000;
const FINANCIAL_POLL_MS = 600_000;
const FINANCIAL_RETRY_MS = 60_000;
const HEALTH_POLL_MS = 300_000;
const HEALTH_RETRY_MS = 60_000;
const BATTERY_POLL_MS = 60_000;
const RECOMMENDATION_POLL_MS = 300_000;
const DETAIL_RETRY_MS = 60_000;

function useEnergyData(stationId) {
  const [energy, setEnergy] = useState({
    stationId,
    overview: null,
    market: null,
    strategy: null,
    error: "",
  });

  useEffect(() => {
    const controller = new AbortController();
    let timer;

    setEnergy({
      stationId,
      overview: null,
      market: null,
      strategy: null,
      error: "",
    });

    // ponytail: each P1 panel lands in its own slot the moment it answers - a slow /market
    // must never hold back the four P0 cards, which only need /overview.
    const settle = (promise, key) =>
      promise
        .then((envelope) => {
          if (!controller.signal.aborted) {
            setEnergy((previous) => ({ ...previous, [key]: envelope }));
          }
        })
        .catch(() => {}); // a failed P1 call leaves its panel at "—" instead of blanking the page

    async function load() {
      settle(getMarket(stationId, { signal: controller.signal }), "market");
      settle(getStrategy(stationId, { signal: controller.signal }), "strategy");

      let failed = false;
      try {
        const overview = await getOverview(stationId, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setEnergy((previous) => ({ ...previous, overview, error: "" }));
      } catch (error) {
        if (controller.signal.aborted) return;
        failed = true;
        setEnergy((previous) => ({
          ...previous,
          error: error.message || "Unable to load live energy data.",
        }));
      }

      // ponytail: a failed poll retries in 10 s, so a backend started after the page recovers without F5.
      timer = setTimeout(load, failed ? RETRY_MS : POLL_MS);
    }

    load();
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [stationId]);

  return {
    ...(energy.stationId === stationId
      ? energy
      : { overview: null, market: null, strategy: null, error: "" }),
    // A settings update answers with the same envelope shape as GET /strategy.
    setStrategy: (envelope) =>
      setEnergy((previous) => ({ ...previous, strategy: envelope })),
  };
}

function useStationDetails(stationId) {
  const [details, setDetails] = useState({
    stationId,
    battery: null,
    recommendation: null,
    batteryError: "",
    recommendationError: "",
  });

  useEffect(() => {
    const controller = new AbortController();
    let batteryTimer;
    let recommendationTimer;

    setDetails({
      stationId,
      battery: null,
      recommendation: null,
      batteryError: "",
      recommendationError: "",
    });

    async function loadBattery() {
      let failed = false;
      try {
        const battery = await getBattery(stationId, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setDetails((previous) => ({
          ...previous,
          battery,
          batteryError: "",
        }));
      } catch (error) {
        if (controller.signal.aborted) return;
        failed = true;
        setDetails((previous) => ({
          ...previous,
          batteryError: error.message || "Unable to load battery details.",
        }));
      }
      batteryTimer = setTimeout(
        loadBattery,
        failed ? DETAIL_RETRY_MS : BATTERY_POLL_MS,
      );
    }

    async function loadRecommendation() {
      let failed = false;
      try {
        const recommendation = await getRecommendation(stationId, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        setDetails((previous) => ({
          ...previous,
          recommendation,
          recommendationError: "",
        }));
      } catch (error) {
        if (controller.signal.aborted) return;
        failed = true;
        setDetails((previous) => ({
          ...previous,
          recommendationError:
            error.message || "Unable to load the strategy recommendation.",
        }));
      }
      recommendationTimer = setTimeout(
        loadRecommendation,
        failed ? DETAIL_RETRY_MS : RECOMMENDATION_POLL_MS,
      );
    }

    loadBattery();
    loadRecommendation();
    return () => {
      clearTimeout(batteryTimer);
      clearTimeout(recommendationTimer);
      controller.abort();
    };
  }, [stationId]);

  return details.stationId === stationId
    ? details
    : {
        battery: null,
        recommendation: null,
        batteryError: "",
        recommendationError: "",
      };
}

function useFinancialSummary(stationId, revision) {
  const [financial, setFinancial] = useState({ summary: null, error: "" });

  // A station switch invalidates the numbers outright; a mode change (revision) does
  // not - the old figure stays up while the two backtests behind it are re-run.
  useEffect(() => {
    setFinancial({ summary: null, error: "" });
  }, [stationId]);

  useEffect(() => {
    const controller = new AbortController();
    let timer;

    async function load() {
      let failed = false;
      try {
        const summary = await getFinancialSummary(stationId, adelaideToday(), {
          signal: controller.signal,
        });
        setFinancial({ summary, error: "" });
      } catch (error) {
        if (error.name === "AbortError") return;
        failed = true;
        setFinancial((previous) => ({
          ...previous,
          error: error.message || "Unable to load today's savings.",
        }));
      }
      // ponytail: 10 min cadence once warm - the first call simulates two whole backtests.
      if (!controller.signal.aborted) {
        timer = setTimeout(
          load,
          failed ? FINANCIAL_RETRY_MS : FINANCIAL_POLL_MS,
        );
      }
    }

    load();
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [stationId, revision]);

  return financial;
}

function useStationHealth(stationId) {
  const [diagnosis, setDiagnosis] = useState({ health: null, error: "" });

  useEffect(() => {
    const controller = new AbortController();
    let timer;

    async function load() {
      let failed = false;
      try {
        const health = await getStationHealth(stationId, {
          signal: controller.signal,
        });
        setDiagnosis({ health, error: "" });
      } catch (error) {
        if (error.name === "AbortError") return;
        failed = true;
        setDiagnosis((previous) => ({
          ...previous,
          error: error.message || "Unable to load the station diagnosis.",
        }));
      }
      // ponytail: 5 min cadence - a diagnosis only moves when a 30-minute pattern completes.
      if (!controller.signal.aborted) {
        timer = setTimeout(load, failed ? HEALTH_RETRY_MS : HEALTH_POLL_MS);
      }
    }

    setDiagnosis({ health: null, error: "" });
    load();
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [stationId]);

  return diagnosis;
}

export default function App() {
  const [stationId, setStationId] = useState(DEFAULT_STATION);
  const { overview, market, strategy, error, setStrategy } =
    useEnergyData(stationId);
  const [modeError, setModeError] = useState("");
  const [financialRevision, setFinancialRevision] = useState(0);

  async function changeMode(mode) {
    setModeError("");
    try {
      setStrategy(
        await updateStrategySettings(stationId, { current_mode: mode }),
      );
      // Today's savings is baseline vs the active mode, so it is stale the moment the
      // mode changes. The measured panels (/overview) do not depend on the mode.
      setFinancialRevision((revision) => revision + 1);
    } catch (modeFailure) {
      setModeError(modeFailure.message || "Unable to change the strategy mode.");
    }
  }
  const { summary: financial, error: financialError } =
    useFinancialSummary(stationId, financialRevision);
  const { health, error: healthError } = useStationHealth(stationId);
  const { battery, recommendation, batteryError, recommendationError } =
    useStationDetails(stationId);
  return (
    <>
      <main
        className="prototype-shell"
        aria-label="AIVY smart home energy demo"
      >
        <Header overview={overview} market={market} />
        <Routes>
          <Route
            path="/"
            element={
              <Dashboard
                stationId={stationId}
                onStationChange={setStationId}
                overview={overview}
                market={market}
                strategy={strategy}
                financial={financial}
                financialError={financialError}
                health={health}
                healthError={healthError}
                battery={battery}
                batteryError={batteryError}
                recommendation={recommendation}
                recommendationError={recommendationError}
                error={error}
                onModeChange={changeMode}
                modeError={modeError}
              />
            }
          />
          <Route
            path="/ai-assistant"
            element={
              <AIAssistant
                key={stationId}
                stationId={stationId}
                overview={overview}
                market={market}
                onStrategyChange={setStrategy}
              />
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
