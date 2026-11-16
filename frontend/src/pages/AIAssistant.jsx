import { useEffect, useRef, useState } from "react";
import {
  getStrategy,
  sendChatMessage,
  simulateStrategy,
  updateStrategySettings,
} from "../api";
import ApplyButton from "../components/ApplyButton";
import Message from "../components/Message";
import {
  CONDITION_LABELS,
  GRID_STATE_LABELS,
  MODE_LABELS,
  centsPerKwh,
  formatNumber,
} from "../lib/energyData";
import { loadChat, saveChat } from "../lib/chatStorage";
import {
  GOAL_LABELS,
  MODE_OPTIONS,
  planCard,
  simulationStrategy,
} from "../lib/strategy";

const QUICK_QUESTIONS = [
  "How can I save more today?",
  "When should I charge my battery?",
  "Should I sell electricity to the grid?",
  "Should I save battery for tomorrow?",
];

/** The bubble's stamp, in the home's own timezone. */
function nowLabel() {
  return new Intl.DateTimeFormat("en-AU", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "Australia/Adelaide",
  }).format(new Date());
}

export default function AIAssistant({
  stationId,
  overview,
  market,
  onStrategyChange,
}) {
  const view = overview?.data;
  const price = market?.data.price.import;
  const weather = market?.data.weather;

  // The station's thread is restored from the browser, so a page switch keeps it.
  const [restored] = useState(() => loadChat(window.localStorage, stationId));
  const [messages, setMessages] = useState(restored.messages);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState(restored.conversationId);
  const [asking, setAsking] = useState(false);
  const [chatError, setChatError] = useState("");
  const [proposal, setProposal] = useState(restored.proposal);
  const [saved, setSaved] = useState("");
  const [applyError, setApplyError] = useState("");
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState(null);
  const [draft, setDraft] = useState(null);
  const [settingsError, setSettingsError] = useState("");
  const chatRef = useRef(null);

  const card = planCard(proposal);

  // App keys this page by station, so a station switch remounts it with empty state.
  useEffect(() => {
    const controller = new AbortController();
    getStrategy(stationId, { signal: controller.signal })
      .then((envelope) => setSettings(envelope.data.settings))
      .catch((error) => {
        if (error.name === "AbortError") return;
        setSettingsError(
          error.message || "Unable to load the strategy settings.",
        );
      });
    return () => controller.abort();
  }, [stationId]);

  useEffect(() => {
    const log = chatRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [messages, asking, card]);

  // One thread per station; saving the conversation id keeps the server's turns too.
  useEffect(() => {
    saveChat(window.localStorage, stationId, {
      conversationId,
      messages,
      proposal,
    });
  }, [stationId, conversationId, messages, proposal]);

  async function ask(question) {
    const text = question.trim();
    if (!text || asking) return;
    const stamp = nowLabel();
    setMessages((previous) => [
      ...previous,
      { id: `q-${crypto.randomUUID()}`, role: "user", text, time: stamp },
    ]);
    setInput("");
    setAsking(true);
    setChatError("");
    try {
      const envelope = await sendChatMessage(stationId, text, { conversationId });
      const reply = envelope.data;
      setConversationId(reply.conversation_id);
      setMessages((previous) => [
        ...previous,
        {
          id: reply.message_id,
          role: "ai",
          text: reply.content,
          time: stamp,
          evidence: reply.evidence,
        },
      ]);
      setProposal(reply.suggested_strategy);
      if (reply.suggested_strategy) setSaved("");
    } catch (error) {
      setChatError(error.message || "The assistant could not answer just now.");
    } finally {
      setAsking(false);
    }
  }

  async function applyProposal() {
    if (!proposal || saving) return;
    setSaving(true);
    setApplyError("");
    try {
      const envelope = await simulateStrategy(
        stationId,
        simulationStrategy(proposal),
      );
      // A sandbox record is not the active mode: make the applied proposal this
      // station's simulated strategy too, so Home and this panel agree with the card.
      const applied = await updateStrategySettings(stationId, {
        current_mode: proposal.mode_name,
        battery_reserve_pct: proposal.battery_reserve_pct,
        optimisation_goal: proposal.optimisation_goal,
      });
      setSettings(applied.data.settings);
      onStrategyChange?.(applied); // Home shows the new mode without waiting for its poll
      setSaved(envelope.data.message);
    } catch (error) {
      setApplyError(error.message || "The simulation could not run.");
    } finally {
      setSaving(false);
    }
  }

  function openSettings() {
    if (!settings) return;
    setSettingsError("");
    setDraft({
      current_mode: settings.current_mode,
      battery_reserve_pct: settings.battery_reserve_pct,
      optimisation_goal: settings.optimisation_goal,
    });
  }

  async function saveSettings(event) {
    event.preventDefault();
    setSaving(true);
    setSettingsError("");
    try {
      const envelope = await updateStrategySettings(stationId, {
        current_mode: draft.current_mode,
        battery_reserve_pct: Number(draft.battery_reserve_pct),
        optimisation_goal: draft.optimisation_goal,
      });
      setSettings(envelope.data.settings);
      onStrategyChange?.(envelope);
      setDraft(null);
    } catch (error) {
      setSettingsError(error.message || "Those settings were rejected.");
    } finally {
      setSaving(false);
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    ask(input);
  }

  return (
    <section
      id="assistant"
      className="screen assistant-screen active-screen"
      aria-label="AI Energy Assistant"
    >
      <div className="assistant-heading">
        <h1>✦ AIVY Energy Assistant</h1>
        <p>Make smarter decisions about your home energy.</p>
      </div>
      <div className="assistant-layout">
        <div className="chat-side">
          <div
            ref={chatRef}
            className="chat-log"
            role="log"
            aria-label="Energy assistant conversation"
            aria-live="polite"
          >
            {messages.length === 0 && (
              <p className="chat-empty">
                Ask about your battery, solar or power price — every answer
                is grounded in this home&apos;s live data.
              </p>
            )}
            {messages.map((message) => (
              <Message key={message.id} message={message} />
            ))}
            {asking && (
              <p className="chat-thinking" role="status">
                AIVY is checking your live data…
              </p>
            )}
            {card && (
              <article className="strategy-card">
                <h2>Suggested Strategy</h2>
                <div className="strategy-data">
                  <span>
                    ◔<b>{card.action}</b>
                    <small>{card.window}</small>
                  </span>
                  <span>
                    ▯<b>Battery Reserve</b>
                    <small>{card.reserve}</small>
                  </span>
                  <span>
                    $<b>Estimated Benefit</b>
                    <small>{card.benefit}</small>
                  </span>
                </div>
                <p className="strategy-reason">
                  {card.mode} ·{" "}
                  {GOAL_LABELS[proposal.optimisation_goal] ??
                    proposal.optimisation_goal}
                  {card.reason ? ` — ${card.reason}` : ""}
                </p>
                <ApplyButton
                  applied={Boolean(saved)}
                  busy={saving}
                  onApply={applyProposal}
                />
                {saved && <small className="strategy-note">{saved}</small>}
                {applyError && (
                  <small className="chat-error" role="alert">
                    {applyError}
                  </small>
                )}
              </article>
            )}
            {chatError && (
              <p className="chat-error" role="alert">
                {chatError}
              </p>
            )}
          </div>
          <div className="suggestions">
            <b>Suggested Questions</b>
            <div>
              {QUICK_QUESTIONS.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => ask(question)}
                  disabled={asking}
                >
                  {question}
                </button>
              ))}
            </div>
          </div>
          <form onSubmit={handleSubmit} className="chat-input">
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="Ask AIVY about your home energy..."
              aria-label="Ask AIVY about your home energy"
              maxLength={1000}
              disabled={asking}
            />
            <button
              aria-label="Send message"
              disabled={asking || !input.trim()}
            >
              ➤
            </button>
          </form>
          <small className="enter-note">
            Grounded in live data · simulation only · Press Enter to send
          </small>
        </div>
        <aside className="right-side">
          <article className="side-card">
            <h2>Live Energy</h2>
            <ul>
              <li>
                <span className="icon solar">☀</span>Solar{" "}
                <b>
                  {formatNumber(view?.display.solar_power_kw)} <small>kW</small>
                </b>
              </li>
              <li>
                <span className="icon homeicon">⌂</span>Home Usage{" "}
                <b>
                  {formatNumber(view?.display.home_usage_kw)} <small>kW</small>
                </b>
              </li>
              <li>
                <span className="icon battery">▯</span>Battery{" "}
                <b>{formatNumber(view?.telemetry.soc_pct, 0)}%</b>
              </li>
              <li>
                <span className="icon grid">♜</span>Grid{" "}
                <b>
                  <small>
                    {GRID_STATE_LABELS[view?.states.grid] ?? "Unknown"}
                  </small>{" "}
                  {formatNumber(view?.display.grid_power_kw)} <small>kW</small>
                </b>
              </li>
              <li>
                <span className="icon price-icon">$</span>Current Price{" "}
                <b>
                  {formatNumber(centsPerKwh(price?.value))}{" "}
                  <small>c/kWh</small>
                </b>
              </li>
              <li>
                <span className="icon solar">☀</span>Weather{" "}
                <b>
                  <small>
                    {CONDITION_LABELS[weather?.condition] ?? "Unknown"},{" "}
                    {formatNumber(weather?.temperature_c, 0)}°C
                  </small>
                </b>
              </li>
            </ul>
          </article>
          <article className="side-card strategy-side">
            <h2>AI Strategy</h2>
            <p>
              Mode{" "}
              <span>{MODE_LABELS[settings?.current_mode] ?? "—"}</span>
            </p>
            <p>
              Battery Reserve{" "}
              <b>
                {settings
                  ? `${Math.round(settings.battery_reserve_pct)}%`
                  : "—"}
              </b>
            </p>
            <p>
              Goal <b>{GOAL_LABELS[settings?.optimisation_goal] ?? "—"}</b>
            </p>
            {draft ? (
              <form className="settings-form" onSubmit={saveSettings}>
                <label>
                  Mode
                  <select
                    value={draft.current_mode}
                    onChange={(event) =>
                      setDraft({ ...draft, current_mode: event.target.value })
                    }
                  >
                    {MODE_OPTIONS.map(({ value, label }) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Battery reserve (%)
                  <input
                    type="number"
                    min="10"
                    max="100"
                    step="1"
                    value={draft.battery_reserve_pct}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        battery_reserve_pct: event.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  Goal
                  <select
                    value={draft.optimisation_goal}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        optimisation_goal: event.target.value,
                      })
                    }
                  >
                    {Object.entries(GOAL_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="settings-actions">
                  <button type="submit" className="apply" disabled={saving}>
                    Save settings
                  </button>
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => setDraft(null)}
                  >
                    Cancel
                  </button>
                </div>
                {settingsError && (
                  <small className="chat-error" role="alert">
                    {settingsError}
                  </small>
                )}
              </form>
            ) : (
              <button
                type="button"
                className="apply"
                onClick={openSettings}
                disabled={!settings || saving}
              >
                Change settings
              </button>
            )}
          </article>
        </aside>
      </div>
      <article className="how-card">
        <h2>How AIVY helps you</h2>
        <div>
          <p>
            ◒ <b>Learns your habits</b>
            <small>Analyses your historical usage patterns</small>
          </p>
          <p>
            ☀ <b>Analyses conditions</b>
            <small>Solar, battery, weather and electricity prices</small>
          </p>
          <p>
            〽 <b>Recommends actions</b>
            <small>Charge, discharge, save or sell at the right time</small>
          </p>
          <p>
            $ <b>Saves you money</b>
            <small>Reduce electricity bills and earn from exports</small>
          </p>
        </div>
      </article>
    </section>
  );
}
