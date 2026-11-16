import { formatAud, formatNumber } from "../lib/energyData";

function evidenceValue(item) {
  if (item.value == null) return "—";
  if (item.unit === "%") return formatNumber(item.value, 0);
  if (item.unit === "AUD/kWh") return formatNumber(item.value, 3);
  if (item.unit === "AUD") return formatAud(item.value);
  return formatNumber(item.value);
}

export default function Message({ message }) {
  const isAi = message.role === "ai";
  const avatar = (
    <span className={isAi ? "bot-avatar" : "user-avatar"} aria-hidden="true">
      {isAi ? "♟" : "●"}
    </span>
  );
  return (
    <div className={`message ${message.role}`}>
      {isAi && avatar}
      <div>
        {message.text}
        {message.evidence?.length > 0 && (
          <ul className="evidence" aria-label="Data behind this answer">
            {message.evidence.map((item) => (
              <li
                key={item.label}
                title={
                  item.price_type === "wholesale_spot"
                    ? "Wholesale spot price"
                    : item.timestamp
                }
              >
                {item.label} <b>{evidenceValue(item)}</b>
                {item.unit && item.unit !== "AUD" ? ` ${item.unit}` : ""}
              </li>
            ))}
          </ul>
        )}
        {message.time && <time>{message.time}</time>}
      </div>
      {!isAi && avatar}
    </div>
  );
}
