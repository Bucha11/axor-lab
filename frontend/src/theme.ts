// Shared palette and typography from the spec-v0.3 mockups — one system, all
// screens. AXOR LAB is its own product on its own domain: the brand accent is
// violet (the Control Plane uses steel).
export const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758",
  steel: "#7FA8CC", violet: "#9B8CCC",
} as const;

export const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

export const btn = (extra: React.CSSProperties = {}): React.CSSProperties => ({
  display: "flex", alignItems: "center", gap: 6, background: "none",
  border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut,
  fontFamily: MONO, fontSize: 11, padding: "6px 11px", cursor: "pointer",
  ...extra,
});

// The primary (filled violet) call-to-action from the mockups.
export const cta = (enabled = true, extra: React.CSSProperties = {}): React.CSSProperties => ({
  display: "flex", alignItems: "center", gap: 7,
  background: enabled ? C.violet : C.panel2, border: "none", borderRadius: 5,
  color: enabled ? C.bg : C.dim, fontFamily: MONO, fontSize: 12.5,
  fontWeight: 700, padding: "9px 18px", cursor: enabled ? "pointer" : "default",
  ...extra,
});

export const inp: React.CSSProperties = {
  background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4,
  color: C.text, fontFamily: MONO, fontSize: 12, padding: "6px 9px",
  outline: "none",
};
