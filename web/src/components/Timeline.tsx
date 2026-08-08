import type { Json as JsonValue } from "../lib/api";
import { Tag, outcomeTone } from "./ui";

interface TraceEvent {
  seq: number;
  type: string;
  tool?: string;
  decision?: { verdict?: string; gate?: string; reason?: string; enforced?: boolean };
}

/**
 * The trace's events, in order.
 *
 * Labels are derived from an event's own STRUCTURAL fields — type, tool,
 * verdict, gate. Never from a payload body: a trace records observations, and
 * a screen that quoted content would render whatever the trace deliberately
 * redacted.
 */
export function Timeline({ trace }: { trace: JsonValue }) {
  const events = (trace.events as TraceEvent[] | undefined) ?? [];
  if (events.length === 0) return <p className="muted">This trace recorded no events.</p>;
  return (
    <ol className="timeline">
      {events.map((event) => (
        <li key={event.seq} className={event.type === "gate_decision" ? "gate" : ""}>
          <span className="seq">{event.seq}</span>
          <span className="kind">{event.type.replace(/_/g, " ")}</span>
          {event.tool && <code>{event.tool}</code>}
          {event.decision?.verdict && (
            <Tag tone={verdictTone(event.decision)}>
              {event.decision.verdict}
              {event.decision.gate ? ` · ${event.decision.gate}` : ""}
            </Tag>
          )}
          {/* A DENY the run did not OBEY is the single most misreadable thing a
              trace can show: the verdict says blocked and the next row is the
              tool result. `enforcement: off` means the kernel decided and the
              caller executed anyway — which is exactly what an ungoverned arm
              measures — so the screen says "recorded, not enforced" rather than
              letting a reader conclude the call was stopped. */}
          {event.decision?.verdict === "DENY" && event.decision.enforced === false && (
            <Tag tone="warning">recorded, not enforced</Tag>
          )}
          {event.decision?.reason && <span className="muted small">{event.decision.reason}</span>}
        </li>
      ))}
    </ol>
  );
}

/** A DENY that was not enforced is not a containment. Painting it red says the
 * call was stopped; it was not. */
function verdictTone(decision: { verdict?: string; enforced?: boolean }) {
  if (decision.verdict !== "DENY") return "success" as const;
  return decision.enforced === false ? ("warning" as const) : ("danger" as const);
}

export function StatusTag({ status }: { status: string }) {
  return <Tag tone={outcomeTone(status)}>{status}</Tag>;
}
