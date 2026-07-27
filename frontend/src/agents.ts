// The agent axis, in one place. Every screen that asks "whose agent" reads this
// list, so the wording of an option and whether this server can actually run it
// are stated once rather than re-invented per screen.
export interface AgentSource {
  id: string;
  label: string;
  /** one line, ≤ 8 words. If it needs a paragraph it belongs behind a `Why`. */
  hint: string;
  /** can this server start the run itself, or does the setup live elsewhere? */
  runnable?: boolean;
  /** where the setup lives, when it is not here */
  route?: string;
  cost?: string;
}

export const AGENT_SOURCES: AgentSource[] = [
  {
    id: "bundled",
    label: "The bundled stand-in",
    hint: "deterministic, offline, free",
    runnable: true,
    cost: "free",
  },
  {
    id: "runtime",
    label: "Your agent, connected",
    hint: "your runtime executes, Lab assigns",
    runnable: true,
    cost: "your infra",
  },
  {
    id: "live",
    label: "A live model",
    hint: "your key, your spend",
    route: "models",
    cost: "costs money",
  },
  {
    id: "byo",
    label: "Your code or traces",
    hint: "upload once, run many",
    route: "agent-ingest",
    cost: "one-time setup",
  },
];

export const agentSource = (id: string): AgentSource =>
  AGENT_SOURCES.find((a) => a.id === id) ?? AGENT_SOURCES[0];
