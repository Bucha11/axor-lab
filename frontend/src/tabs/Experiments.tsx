// One roof over the two ways to build an experiment.
//
// They were separate top-level tabs — "build" and "experiments" — which put two
// doors on one room. Both compose a governed run and both end at a result; they
// differ only in where the tasks come from:
//
//   * a BENCHMARK supplies the tasks, with published reference numbers to sit
//     beside your own, so the question is "what does the gate cost me";
//   * your own SCENARIOS are ones you wrote or composed, so the question is
//     "what does the gate do to MY agent".
//
// A newcomer cannot tell "build" from "experiments" by their names, and picking
// wrong means finding out two screens later. Naming the axis they differ on —
// whose tasks — is the whole fix; neither screen changed.
//
// `#/builder` and `#/benchmark` still resolve and select their mode, so every
// existing link and deep-link keeps working.
import { C, MONO } from "../theme";
import { navigate } from "../router";
import Benchmark from "./Benchmark";
import Builder from "./Builder";

export type ExperimentSource = "benchmark" | "scenarios";

const SOURCES = [
  {
    id: "benchmark" as const,
    route: "benchmark",
    label: "A benchmark's tasks",
    hint: "published suites with reference numbers to compare against",
  },
  {
    id: "scenarios" as const,
    route: "builder",
    label: "Your own scenarios",
    hint: "compose or author the tasks, then run them under the same gate",
  },
];

export default function Experiments({ source }: { source: ExperimentSource }) {
  return (
    <div style={{ maxWidth: 880, margin: "0 auto" }}>
      <div
        className="wrapline"
        style={{ gap: 6, marginBottom: 18, borderBottom: `1px solid ${C.line}`, paddingBottom: 12 }}
      >
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginRight: 4 }}>
          whose tasks?
        </span>
        {SOURCES.map((s) => {
          const on = s.id === source;
          return (
            <button
              key={s.id}
              onClick={() => navigate(s.route)}
              title={s.hint}
              style={{
                fontFamily: MONO, fontSize: 11, padding: "5px 11px", borderRadius: 6,
                cursor: "pointer", background: "transparent",
                border: `1px solid ${on ? C.violet : C.line}`,
                color: on ? C.text : C.mut,
              }}
            >
              {s.label}
            </button>
          );
        })}
      </div>
      {source === "benchmark" ? <Benchmark /> : <Builder />}
    </div>
  );
}
