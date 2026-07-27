// The agent, on its own screen.
//
// It used to be the first of three stacked panels on one page, which meant the
// subject of the experiment was competing for attention with the tasks and the
// policy. It is one decision, it is sticky, and everything downstream depends on
// it — so it gets a screen, is chosen once, and every experiment afterwards runs
// against whatever is set here.
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Check, ExternalLink, RefreshCw } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import { useApp } from "../store";
import { AGENT_SOURCES, agentSource } from "../agents";
import Why from "../components/Why";

export default function AgentSetup() {
  const { agentSource: chosen, setAgentSource, runtimeRef, setRuntimeRef } = useApp();
  const source = agentSource(chosen);
  const runtimes = useQuery({
    queryKey: ["runtimes"], queryFn: api.listRuntimes, enabled: chosen === "runtime",
  });
  const ready = source.runnable && (chosen !== "runtime" || !!runtimeRef);

  return (
    <div style={{ maxWidth: 620, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Your agent</h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 22 }}>
        Every experiment runs against this. Change it any time.
      </div>

      {AGENT_SOURCES.map((a) => {
        const on = chosen === a.id;
        return (
          <div
            key={a.id}
            onClick={() => setAgentSource(a.id)}
            style={{
              cursor: "pointer", marginBottom: 8, padding: "13px 15px", borderRadius: 9,
              background: on ? "rgba(155,140,204,.07)" : C.panel,
              border: `1px solid ${on ? C.violet : C.line}`,
            }}
          >
            <div className="wrapline" style={{ justifyContent: "space-between", gap: 10 }}>
              <span>
                <span style={{ display: "block", fontFamily: MONO, fontSize: 12.5, color: on ? C.text : C.mut, fontWeight: on ? 600 : 400 }}>
                  {a.label}
                </span>
                <span style={{ display: "block", fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 2 }}>
                  {a.hint}
                </span>
              </span>
              <span className="wrapline" style={{ gap: 8 }}>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{a.cost}</span>
                {on && <Check size={13} color={C.violet} />}
              </span>
            </div>

            {/* only the SELECTED source shows its settings — the rest stay one line */}
            {on && a.id === "runtime" && (
              <div className="wrapline" style={{ gap: 8, marginTop: 12 }} onClick={(e) => e.stopPropagation()}>
                {(runtimes.data ?? []).map((rt) => (
                  <button
                    key={rt.runtime_ref}
                    onClick={() => setRuntimeRef(rt.runtime_ref)}
                    style={{
                      background: runtimeRef === rt.runtime_ref ? "rgba(127,168,204,.12)" : "none",
                      border: `1px solid ${runtimeRef === rt.runtime_ref ? C.steel : C.line}`,
                      borderRadius: 5, color: runtimeRef === rt.runtime_ref ? C.steel : C.mut,
                      fontFamily: MONO, fontSize: 10, padding: "4px 9px", cursor: "pointer",
                    }}
                  >
                    {rt.runtime_ref}{rt.model ? ` · ${rt.model}` : ""}
                  </button>
                ))}
                {runtimes.isSuccess && (runtimes.data ?? []).length === 0 && (
                  <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
                    none connected yet
                  </span>
                )}
                <button
                  onClick={() => runtimes.refetch()}
                  style={{ background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.dim, fontFamily: MONO, fontSize: 10, padding: "4px 8px", cursor: "pointer" }}
                >
                  <RefreshCw size={10} style={{ verticalAlign: -1 }} /> refresh
                </button>
              </div>
            )}

            {on && !a.runnable && (
              <button
                onClick={(e) => { e.stopPropagation(); navigate(a.route!); }}
                style={{ ...cta(true), marginTop: 12, fontSize: 11, padding: "7px 13px" }}
              >
                Set it up <ExternalLink size={12} />
              </button>
            )}
          </div>
        );
      })}

      <div style={{ marginTop: 14 }}>
        <Why label="what does the stand-in actually stand in for?">
          A deterministic script in place of the model layer: no provider, no key, no cost, and the
          same behaviour on the same seed every time. That determinism is what makes a governed and
          an ungoverned arm a real matched pair rather than two independent samples — a live model
          redraws per arm and gets compared differently, and honestly.
        </Why>
      </div>

      <div className="wrapline" style={{ gap: 12, marginTop: 26 }}>
        <button onClick={() => navigate("experiments")} disabled={!ready} style={cta(!!ready)}>
          Choose an experiment <ArrowRight size={13} />
        </button>
        {chosen === "runtime" && !runtimeRef && (
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.amber }}>
            pick a connected runtime first
          </span>
        )}
      </div>
    </div>
  );
}
