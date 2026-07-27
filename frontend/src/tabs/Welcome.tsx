// First run: one question at a time, and a way out of it.
//
// The previous front page asked all three questions at once — agent, tasks,
// gate — each with a paragraph attached, on one scrolling screen. Everything was
// present and nothing was legible. This asks them one at a time, in eight words
// each, and the explanations sit behind `why?` for whoever wants them.
//
// Skippable on every step, because a wizard you cannot leave is a wall. Skipping
// lands on the agent screen — the same first decision, just without being walked
// through it — and it never appears again once you are through.
import { useState } from "react";
import { ArrowRight, Check, RefreshCw } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { navigate } from "../router";
import { api, type Catalog } from "../api";
import { useApp } from "../store";
import { AGENT_SOURCES, agentSource } from "../agents";
import Why from "../components/Why";

const STEPS = ["agent", "tasks", "gate"] as const;

export default function Welcome({ catalog }: { catalog: Catalog }) {
  const { agentSource: chosen, setAgentSource, setSetupDone, setLastRun } = useApp();
  const [step, setStep] = useState(0);
  const [suiteId, setSuiteId] = useState(catalog.suites[0]?.id ?? "");
  const [governed, setGoverned] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const done = (to: string) => { setSetupDone(true); navigate(to); };

  const finish = async () => {
    const source = agentSource(chosen);
    if (!source.runnable) { done(source.route!); return; }
    setBusy(true); setError(null);
    try {
      const run = await api.runComposed({
        suite: suiteId,
        repeats: catalog.repeats.default,
        run_mode: governed ? "compare" : "ungoverned",
      });
      setLastRun(run.run_id);
      setSetupDone(true);
      navigate(`results/${run.run_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 560, margin: "6vh auto 0" }}>
      <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 34 }}>
        <div className="wrapline" style={{ gap: 6 }}>
          {STEPS.map((s, i) => (
            <span
              key={s}
              style={{
                width: i === step ? 18 : 6, height: 6, borderRadius: 3,
                background: i <= step ? C.violet : C.line, transition: "all .2s",
              }}
            />
          ))}
        </div>
        <button
          onClick={() => done("agent")}
          style={{ background: "none", border: "none", cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}
        >
          skip setup →
        </button>
      </div>

      {step === 0 && (
        <Step
          title="Whose agent are we testing?"
          note="You can change this later."
          onNext={() => setStep(1)}
        >
          {AGENT_SOURCES.map((a) => (
            <Choice
              key={a.id}
              on={chosen === a.id}
              label={a.label}
              hint={a.hint}
              tag={a.cost}
              onClick={() => setAgentSource(a.id)}
            />
          ))}
        </Step>
      )}

      {step === 1 && (
        <Step
          title="What should it try to do?"
          note="Ordinary tasks with an injection hidden in the data it reads."
          onBack={() => setStep(0)}
          onNext={() => setStep(2)}
        >
          {catalog.suites.map((s) => (
            <Choice
              key={s.id}
              on={suiteId === s.id}
              label={s.label}
              hint={`${s.scenarios.length} scenarios`}
              tag={s.source}
              onClick={() => setSuiteId(s.id)}
            />
          ))}
        </Step>
      )}

      {step === 2 && (
        <Step
          title="Put a gate in front of it?"
          note="Optional. Off is a normal experiment."
          onBack={() => setStep(1)}
        >
          <Choice
            on={!governed}
            label="No gate"
            hint="see what your agent does unimpeded"
            onClick={() => setGoverned(false)}
          />
          <Choice
            on={governed}
            label="Add a governed arm"
            hint="same tasks, same seeds, twice"
            onClick={() => setGoverned(true)}
          />
          <div style={{ marginTop: 14 }}>
            <Why>
              With a gate the run happens twice on the same seeds, so the difference between the
              two arms is the gate and nothing else. Without one you get a single arm: your agent's
              own attack-success and task-success rate, which is a complete answer to
              "what does my agent do", just not a comparison.
            </Why>
          </div>
          <div className="wrapline" style={{ gap: 12, marginTop: 22 }}>
            <button onClick={finish} disabled={busy} style={cta(!busy)}>
              {busy
                ? <><RefreshCw size={13} className="animate-spin" /> running…</>
                : <><Check size={13} /> Run it</>}
            </button>
            <button
              onClick={() => setStep(1)}
              style={{ background: "none", border: "none", cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}
            >
              back
            </button>
          </div>
          {error && (
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.red, marginTop: 12, lineHeight: 1.6 }}>
              {error}
            </div>
          )}
        </Step>
      )}
    </div>
  );
}

function Step({ title, note, children, onBack, onNext }: {
  title: string; note: string; children: React.ReactNode;
  onBack?: () => void; onNext?: () => void;
}) {
  return (
    <div>
      <h1 style={{ fontSize: 23, fontWeight: 650, lineHeight: 1.3, margin: "0 0 6px" }}>{title}</h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 22 }}>{note}</div>
      {children}
      {onNext && (
        <div className="wrapline" style={{ gap: 12, marginTop: 22 }}>
          <button onClick={onNext} style={cta(true)}>
            Next <ArrowRight size={13} />
          </button>
          {onBack && (
            <button
              onClick={onBack}
              style={{ background: "none", border: "none", cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}
            >
              back
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Choice({ on, label, hint, tag, onClick }: {
  on: boolean; label: string; hint: string; tag?: string; onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className="wrapline"
      style={{
        justifyContent: "space-between", gap: 10, cursor: "pointer", marginBottom: 8,
        padding: "13px 15px", borderRadius: 9, background: on ? "rgba(155,140,204,.07)" : C.panel,
        border: `1px solid ${on ? C.violet : C.line}`,
      }}
    >
      <span>
        <span style={{ display: "block", fontFamily: MONO, fontSize: 12.5, color: on ? C.text : C.mut, fontWeight: on ? 600 : 400 }}>
          {label}
        </span>
        <span style={{ display: "block", fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 2 }}>
          {hint}
        </span>
      </span>
      {tag && <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{tag}</span>}
    </div>
  );
}
