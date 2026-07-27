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
  // "A live model" used to sit here, as a peer of the three real sources. It is
  // not one. A model is a COMPONENT of an agent — the harness, the prompts and
  // the tool set are the thing under test, and in that path they were ours, not
  // yours. Offering it as a fourth agent told a newcomer that testing somebody
  // else's model and testing their own product were the same kind of act.
  //
  // When you bring your own agent, the model inside it is already yours; there
  // is nothing to choose here. The cross-model screen still exists at #/models
  // and says plainly what it measures.
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
