// EvidenceCase (lab-evidencecase mockup): one trial of a published run,
// reconstructed from its recorded trace — the aggregate is a distribution over
// these. Data: the trace from GET /api/publications/{id}/bundle.
// Deep link: #/e/{publication_id}/evidence/{trace_id}.
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight, Lock } from "lucide-react";
import { C, MONO } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import TraceSteps, { verdictOf } from "../components/TraceSteps";
import Collapse from "../components/Collapse";
import EmptyState, { Cmd } from "../components/EmptyState";

export default function EvidenceView({
  publicationId, traceId,
}: {
  publicationId: string;
  traceId: string;
}) {
  const pkg = useQuery({
    queryKey: ["bundle", publicationId],
    queryFn: () => api.getBundle(publicationId),
  });

  if (pkg.isLoading) {
    return <div style={{ maxWidth: 660, margin: "0 auto", fontFamily: MONO, fontSize: 11, color: C.dim }}>loading bundle…</div>;
  }
  if (pkg.isError || !pkg.data) {
    return (
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <EmptyState title="bundle unreachable">
          {String(pkg.error instanceof Error ? pkg.error.message : "not found")}
          <Cmd>python -m lab_server --root ./lab-store --port 8000</Cmd>
        </EmptyState>
      </div>
    );
  }

  const trace = pkg.data.traces.find((t) => t.trace_id === traceId);
  if (!trace) {
    return (
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <EmptyState title={`trace ${traceId} not in this bundle`}>
          Open the publication and pick a trial from its trace list.
        </EmptyState>
      </div>
    );
  }

  const { verdict, decision } = verdictOf(trace);
  const denied = verdict === "DENY";
  const scenario = trace.trial?.scenario_id ?? "trial";
  const condition = trace.trial?.condition_id ?? "?";
  const scenarioDef = pkg.data.bundle.scenarios?.find(
    (s) => String((s as { name?: unknown }).name) === scenario,
  ) as { injection?: { text?: string; goal?: string } } | undefined;

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <div className="wrapline mb-3" style={{ gap: 8 }}>
        <ArrowLeft size={14} color={C.steel} style={{ cursor: "pointer" }} onClick={() => navigate(`e/${publicationId}`)} />
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.steel, cursor: "pointer" }} onClick={() => navigate(`e/${publicationId}`)}>
          publication
        </span>
        <ChevronRight size={12} color={C.dim} />
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          {scenario} · {condition} · EvidenceCase
        </span>
      </div>

      <h1 style={{ fontSize: 20, fontWeight: 650, lineHeight: 1.3, margin: "0 0 4px" }}>
        {denied
          ? "The gate denied the sink call this trial's provenance drove."
          : "The emitted call executed — this is the ungoverned baseline trajectory."}
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 18 }}>
        one trial of the run, reconstructed from its recorded trace — the aggregate is a distribution over these
      </div>

      {scenarioDef?.injection?.text && (
        <div className="mb-3 p-3" style={{ background: "rgba(229,72,77,0.05)", border: "1px solid #5a2a2c", borderRadius: 8 }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.red, letterSpacing: "0.06em", marginBottom: 4 }}>
            INJECTION — planted in an untrusted fixture field
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.6 }}>
            «{scenarioDef.injection.text}»
            {scenarioDef.injection.goal && (
              <span style={{ color: C.dim }}> · goal: {scenarioDef.injection.goal}</span>
            )}
          </div>
        </div>
      )}

      <TraceSteps trace={trace} />

      {/* why the verdict — provenance of the driving argument */}
      <div className="mt-3">
        <Collapse title="why the verdict — provenance of the driving argument" defaultOpen>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>
            {decision ? (
              <>
                <div>gate <span style={{ color: C.text }}>{String(decision.gate ?? "—")}</span></div>
                {"driving_value_id" in decision && (
                  <div style={{ paddingLeft: 12, color: C.dim }}>
                    └ driving value: <span style={{ color: C.amber }}>{String(decision.driving_value_id ?? decision.driving_unresolved ?? "—")}</span>
                  </div>
                )}
                {decision.reason != null && (
                  <div style={{ paddingLeft: 12, color: C.dim }}>
                    └ reason: <span style={{ color: denied ? C.green : C.red }}>{String(decision.reason)}</span>
                  </div>
                )}
                <div style={{ paddingLeft: 12, color: C.dim }}>
                  └ verdict: <span style={{ color: denied ? C.green : C.red }}>{verdict}</span>
                </div>
              </>
            ) : (
              <span style={{ color: C.dim }}>this trace records no gate decision</span>
            )}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 8 }}>
            the verdict is content-independent — it turns on provenance (untrusted-derived), not on the wording;
            a prompt-given value would carry causal_root = {"{ prompt }"} → not tainted → allowed
          </div>
        </Collapse>
      </div>

      {/* replay honesty */}
      <div className="mt-3">
        <Collapse title="what replay can and cannot show" border={C.amber} color={C.amber} defaultOpen>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>
            <div><span style={{ color: C.green }}>exact:</span> the governance verdict on this recorded trace — DENY vs ALLOW — replays bit-identical, forever.</div>
            <div className="mt-1"><span style={{ color: C.amber }}>not exact:</span> what the agent would have done <i>after</i> a DENY. The governed continuation (does it retry? give up? succeed honestly?) needs a fresh live run — it is not recoverable from a frozen trace.</div>
            <div className="mt-1" style={{ color: C.dim }}>So: verdict = replay (deterministic). Downstream behavior = live (stochastic, CI). The UI never blurs the two.</div>
          </div>
        </Collapse>
      </div>

      {/* to regression */}
      <div className="mt-3 px-4 py-3 wrapline" style={{ justifyContent: "space-between", background: C.panel, border: `1px solid ${C.steel}`, borderRadius: 8 }}>
        <div className="flex items-center gap-2">
          <Lock size={13} color={C.steel} />
          <div>
            <div style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>Pin as a regression case</div>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, marginTop: 1 }}>
              freeze this trace + expected verdict; future kernel versions must surface any change from it. In the
              CLI today: <span style={{ color: C.steel }}>axor-lab pin ./bundle {trace.trace_id} {denied ? "DENY" : "ALLOW"}</span>
              {" "}— one-click pinning from the UI is TODO
            </div>
          </div>
        </div>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 16, lineHeight: 1.7 }}>
        This is the screen that makes Lab more than a benchmark dashboard: not "governance helped 23/30 trials," but
        <i> this</i> trial — the injection it read, how provenance carried to the sink, the gate that fired, and why the
        verdict is invariant to reframing. From here it becomes a regression that guards every future kernel build.
      </div>
    </div>
  );
}
