// Make one. Four fields, no prose.
//
// The version this replaces was a four-step numbered constructor where every
// field carried a paragraph. The paragraphs were true and nobody read them, so
// they are behind `why?` now and the fields are just fields.
import { useState } from "react";
import { ArrowLeft, Play, RefreshCw } from "lucide-react";
import { C, MONO, cta } from "../theme";
import { navigate } from "../router";
import { api, type Catalog } from "../api";
import { useApp } from "../store";
import { agentSource } from "../agents";
import Why from "../components/Why";

export default function NewExperiment({ catalog }: { catalog: Catalog }) {
  const { agentSource: chosen, runtimeRef, setLastRun } = useApp();
  const source = agentSource(chosen);
  const ready = !!source.runnable && (chosen !== "runtime" || !!runtimeRef);
  const [suiteId, setSuiteId] = useState(catalog.suites[0]?.id ?? "");
  const [repeats, setRepeats] = useState(catalog.repeats.default);
  const [governed, setGoverned] = useState(false);
  const [realKernel, setRealKernel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const suite = catalog.suites.find((s) => s.id === suiteId);
  const arms = governed ? 2 : 1;
  const trials = (suite?.scenarios.length ?? 0) * arms * repeats;
  const underpowered = governed && repeats < catalog.repeats.min_powered;

  const spec = {
    suite: suiteId,
    repeats,
    run_mode: (governed ? "compare" : "ungoverned") as "compare" | "ungoverned",
    ...(governed && realKernel ? { real_kernel: true } : {}),
  };

  const run = async () => {
    setBusy(true); setError(null);
    try {
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
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 560, margin: "0 auto" }}>
      <button
        onClick={() => navigate("experiments")}
        style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11, marginBottom: 14 }}
      >
        <ArrowLeft size={11} style={{ verticalAlign: -1 }} /> experiments
      </button>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 22px" }}>New experiment</h1>

      <Field label="Agent" value={source.label.toLowerCase()} onEdit={() => navigate("agent")} />

      <Field label="Tasks">
        <div className="wrapline" style={{ gap: 7 }}>
          {catalog.suites.map((s) => (
            <button
              key={s.id}
              onClick={() => setSuiteId(s.id)}
              style={{
                background: suiteId === s.id ? "rgba(155,140,204,.10)" : "none",
                border: `1px solid ${suiteId === s.id ? C.violet : C.line}`, borderRadius: 6,
                color: suiteId === s.id ? C.text : C.mut, fontFamily: MONO, fontSize: 11,
                padding: "5px 11px", cursor: "pointer",
              }}
            >
              {s.label} · {s.scenarios.length}
            </button>
          ))}
        </div>
      </Field>

      <Field label="Repeats" value={String(repeats)}>
        <input
          type="range" min={1} max={Math.min(catalog.repeats.max, 40)} value={repeats}
          onChange={(e) => setRepeats(Number(e.target.value))}
          style={{ width: "100%", accentColor: C.violet }}
        />
        {underpowered && (
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.amber, marginTop: 6 }}>
            under {catalog.repeats.min_powered} the two arms get no p-value
          </div>
        )}
      </Field>

      <Field label="Gate" value={governed ? "on" : "off"}>
        <label className="wrapline" style={{ gap: 8, cursor: "pointer" }}>
          <input type="checkbox" checked={governed} onChange={(e) => setGoverned(e.target.checked)} />
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: governed ? C.text : C.mut }}>
            add a governed arm
          </span>
        </label>
        {governed && (
          <label className="wrapline" style={{ gap: 8, cursor: "pointer", marginTop: 8, marginLeft: 24 }}>
            <input
              type="checkbox" checked={realKernel} disabled={!catalog.real_kernel?.available}
              onChange={(e) => setRealKernel(e.target.checked)}
            />
            <span style={{ fontFamily: MONO, fontSize: 11, color: realKernel ? C.text : C.mut }}>
              production kernel
              <span style={{ color: C.dim }}>
                {" "}({catalog.real_kernel?.available ? catalog.real_kernel.version : "not installed"})
              </span>
            </span>
          </label>
        )}
        <div style={{ marginTop: 10 }}>
          <Why>
            Off, the run has no gate in it anywhere and you get one arm — your agent's own
            attack-success and task-success rate. On, the same trials run again under a gate on the
            same seeds, so the difference between the arms is the gate and nothing else. The
            production kernel swaps the stdlib reference gate for the real axor-core governor;
            both arms are repinned together, so the comparison stays about enforcement.
          </Why>
        </div>
      </Field>

      <div className="wrapline" style={{ gap: 12, marginTop: 22 }}>
        <button onClick={run} disabled={busy || !suiteId || !ready} style={cta(!busy && !!suiteId && ready)}>
          {busy
            ? <><RefreshCw size={13} className="animate-spin" /> running…</>
            : <><Play size={13} /> Run {trials} trials</>}
        </button>
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
          {suite?.scenarios.length ?? 0} × {arms} arm{arms === 1 ? "" : "s"} × {repeats}
        </span>
      </div>

      {error && (
        <div className="mt-3 p-3" style={{ background: C.panel, border: `1px solid ${C.red}`, borderRadius: 8, fontFamily: MONO, fontSize: 11, color: C.red, lineHeight: 1.6 }}>
          {error}
        </div>
      )}
    </div>
  );
}

function Field({ label, value, children, onEdit }: {
  label: string; value?: string; children?: React.ReactNode; onEdit?: () => void;
}) {
  return (
    <div style={{ borderTop: `1px solid ${C.line}`, padding: "14px 0" }}>
      <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: children ? 10 : 0 }}>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>{label}</span>
        <span className="wrapline" style={{ gap: 8 }}>
          {value && <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>{value}</span>}
          {onEdit && (
            <button
              onClick={onEdit}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.violet, fontFamily: MONO, fontSize: 10 }}
            >
              change
            </button>
          )}
        </span>
      </div>
      {children}
    </div>
  );
}
