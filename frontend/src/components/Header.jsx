import { Link, NavLink } from "react-router-dom";
import { CONDITION_LABELS, formatNumber } from "../lib/energyData";

export default function Header({ overview, market }) {
  const weather = market?.data.weather;
  const systemState = overview?.data.states.system;
  const systemLabel =
    systemState === "online"
      ? "System Online"
      : systemState === "delayed"
        ? "System Delayed"
        : "System —";

  return (
    <header className="topbar">
      <Link className="brand" to="/" aria-label="Go to Home">
        <span className="leafmark">▰</span>
        <span>AIVY</span>
        <small>Smart Energy. Better Tomorrow.</small>
      </Link>
      <nav aria-label="Primary navigation">
        <NavLink end className="nav-link" to="/">
          Home
        </NavLink>
        <NavLink className="nav-link" to="/ai-assistant">
          AI Assistant
        </NavLink>
      </nav>
      <div className="top-status">
        <span className="sun">☼</span>
        <span>
          <b>Adelaide</b>
          <br />
          {CONDITION_LABELS[weather?.condition] ?? "Unknown"}{" "}
          {formatNumber(weather?.temperature_c, 0)}°C
        </span>
        <i></i>
        <span>{systemLabel}</span>
      </div>
    </header>
  );
}
