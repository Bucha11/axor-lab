// Pick an experiment, or make one.
//
// This is the screen you live on once setup is done. It answers one question —
// what do I run — and it runs it against whatever agent the agent screen holds,
// rather than re-asking. Cards, not paragraphs: a name, the shape of it, and a
// Run button. Anything that needs explaining sits behind `why?`.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Plus, RefreshCw, Shield } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { navigate } from "../router";
import { api, type Catalog } from "../api";
import { useApp } from "../store";
import { agentSource } from "../agents";
import Why from "../components/Why";

export default function Experiments({ catalog }: { catalog: Catalog }) {
  const { agentSource: chosen, runtimeRef, setLastRun } = useApp();
  const source = agentSource(chosen);
  // "connected" is runnable in principle and not until a runtime is picked. The
  // agent screen gates on this too, but nothing stops a deep link straight here,
  // and `createRun(null, …)` fails with a message about the wrong thing.
  const ready = !!source.runnable && (chosen !== "runtime" || !!runtimeRef);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const published = useQuery({ queryKey: ["publications"], queryFn: api.listPublications });

  const run = async (suiteId: string) => {
    setBusy(suiteId); setError(null);
    try {
      const spec = {
        suite: suiteId,
        repeats: catalog.repeats.default,
        run_mode: "ungoverned" as const,
      };
      if (chosen === "runtime") {
        const composed = await api.composeExperiment(spec);
        const experiment = (composed.document as { experiment: Record<string, unknown> }).experiment;
        const created = await api.createRun(
          runtimeRef!, experiment, composed.planned_trials,
          composed.estimate as unknown as Record<string, unknown>,
        );
        setLastRun(created.run_id);
        navigate(`runs/${created.run_id}`);
        return;
      }
      const landed = await api.runComposed(spec);
      setLastRun(landed.run_id);
      navigate(`results/${landed.run_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ maxWidth: 620, margin: "0 auto" }}>
      <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 22 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>Experiments</h1>
          <div className="wrapline" style={{ gap: 6, fontFamily: MONO, fontSize: 11, color: C.mut }}>
            running against
            <button
              onClick={() => navigate("agent")}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.violet, fontFamily: MONO, fontSize: 11 }}
            >
              {source.label.toLowerCase()}
              {chosen === "runtime" && runtimeRef ? ` · ${runtimeRef}` : ""} ⌄
            </button>
          </div>
        </div>
        <button onClick={() => navigate("experiments/new")} style={cta(true, { fontSize: 11.5, padding: "8px 14px" })}>
          <Plus size={13} /> New
        </button>
      </div>

      {!ready && (
        <div className="p-3 mb-4" style={{ background: C.panel, border: `1px solid ${C.amber}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.amber, lineHeight: 1.6 }}>
          {source.label} needs setup before it can run these.{" "}
          <button
            onClick={() => navigate(source.route ?? "agent")}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.amber, fontFamily: MONO, fontSize: 11, textDecoration: "underline" }}
          >
            set it up →
          </button>
        </div>
      )}

      <Section label="Ready to run" />
      {catalog.suites.map((s) => (
        <Card
          key={s.id}
          title={s.label}
          sub={`${s.scenarios.length} scenarios · ${s.source}`}
          action={
            <button
              onClick={() => run(s.id)}
              disabled={busy !== null || !ready}
              style={cta(busy === null && ready, { fontSize: 11, padding: "7px 13px" })}
            >
              {busy === s.id
                ? <><RefreshCw size={12} className="animate-spin" /> running…</>
                : "Run"}
            </button>
          }
        />
      ))}

      <Section label="Priced against a benchmark" />
      <Card
        title="What a gate costs"
        sub="4 AgentDojo suites, no model needed"
        icon={<Shield size={13} color={C.violet} />}
        action={
          <button onClick={() => navigate("governance")} style={{ ...cta(true), background: "none", border: `1px solid ${C.line}`, color: C.mut, fontSize: 11, padding: "7px 13px" }}>
            Open <ArrowRight size={12} />
          </button>
        }
      />

      {(published.data?.length ?? 0) > 0 && (
        <>
          <Section label="Published by others" />
          {(published.data ?? []).slice(0, 4).map((p) => (
            <Card
              key={p.publication_id}
              title={p.question}
              sub={p.publication_id}
              action={
                <button
                  onClick={() => navigate(`e/${p.publication_id}`)}
                  style={{ ...cta(true), background: "none", border: `1px solid ${C.line}`, color: C.mut, fontSize: 11, padding: "7px 13px" }}
                >
                  Open <ArrowRight size={12} />
                </button>
              }
            />
          ))}
          <button
            onClick={() => navigate("published")}
            style={{ background: "none", border: "none", padding: "4px 0", cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 10.5 }}
          >
            the whole catalog →
          </button>
        </>
      )}

      {error && (
        <div className="mt-3 p-3" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red, lineHeight: 1.6 }}>
          {error}
        </div>
      )}

      {/* the quieter doors. An entry point linked from nowhere is one that does
          not exist, and these three are not on any other screen. */}
      <div className="wrapline" style={{ gap: 14, marginTop: 26, paddingTop: 14, borderTop: `1px solid ${C.line}` }}>
        {[
          { to: "builder", label: "as a file · CLI" },
          { to: "scenario-author", label: "write a scenario" },
          { to: "import", label: "import an incident" },
        ].map((l) => (
          <button
            key={l.to}
            onClick={() => navigate(l.to)}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 10.5 }}
          >
            {l.label} →
          </button>
        ))}
      </div>

      <div style={{ marginTop: 16 }}>
        <Why label="what does Run actually do?">
          It composes an .axl for the suite, executes every scenario the number of times set on the
          experiment, and keeps each trial's trace. Nothing leaves this machine, and the run is
          byte-identical to what <span style={{ color: C.text }}>axor-lab run</span> produces from the
          same file — so a result here can be replayed, pinned or published without being re-run.
        </Why>
      </div>
    </div>
  );
}

function Section({ label }: { label: string }) {
  return (
    <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: .5, margin: "20px 0 8px" }}>
      {label.toUpperCase()}
    </div>
  );
}

function Card({ title, sub, action, icon }: {
  title: string; sub: string; action: React.ReactNode; icon?: React.ReactNode;
}) {
  return (
    <div
      className="wrapline"
      style={{
        justifyContent: "space-between", gap: 12, marginBottom: 8,
        padding: "12px 15px", borderRadius: 9, background: C.panel, border: `1px solid ${C.line}`,
      }}
    >
      <span style={{ flex: "1 1 240px", minWidth: 180 }}>
        <span className="wrapline" style={{ gap: 7 }}>
          {icon}
          <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, fontWeight: 600 }}>{title}</span>
        </span>
        <span style={{ display: "block", fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 3 }}>
          {sub}
        </span>
      </span>
      {action}
    </div>
  );
}
