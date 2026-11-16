export default function ApplyButton({ applied, busy, onApply }) {
  return (
    <button
      type="button"
      className={`apply${applied ? " applied" : ""}`}
      onClick={onApply}
      disabled={applied || busy}
    >
      {applied ? "✓ Saved to simulation" : busy ? "Saving…" : "Apply in Simulation"}
    </button>
  );
}
