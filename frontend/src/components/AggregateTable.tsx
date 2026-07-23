// Renders bundle.aggregates rows — estimate bar + CI markers, per the
// lab-results/lab-published mockups. Intervals and tests are COMPUTED by the
// runner/analyzer (contracts/statistics.md) and stored in the aggregates; this
// component only renders stored numbers — no statistic is computed here.
import { C, MONO } from "../theme";
import { Aggregate } from "../api";

const condColor = (conditionId: string): string =>
  conditionId.includes("ungoverned") ? C.red
  : conditionId.includes("governed") ? C.green
  : C.steel;

function Row({ a }: { a: Aggregate }) {
  const col = condColor(a.condition_id);
  const lo = a.interval.low;
  const hi = a.interval.high;
  return (
    <div className="result-row px-4 py-2.5" style={{ borderTop: `1px solid ${C.line}` }}>
      <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, flex: "0 0 auto", minWidth: 96 }}>
        {a.condition_id}
      </span>
      <span style={{ flex: "1 1 90px", minWidth: 80, height: 8, background: C.panel2, borderRadius: 3, position: "relative" }}>
        <span style={{ position: "absolute", left: 0, top: 1, height: 6, width: `${Math.min(a.estimate, 1) * 100}%`, background: col, borderRadius: 3 }} />
        <span style={{
          position: "absolute", top: 0, height: 8,
          left: `${Math.min(lo, 1) * 100}%`, width: `${Math.max(Math.min(hi, 1) - Math.min(lo, 1), 0) * 100}%`,
          borderLeft: `1px solid ${C.text}`, borderRight: `1px solid ${C.text}`, opacity: 0.5,
        }} />
      </span>
      <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, flex: "0 0 auto", fontWeight: 700 }}>
        {a.estimate.toFixed(2)}
        <span style={{ color: C.mut, fontWeight: 400, fontSize: 9.5 }}> [{lo.toFixed(2)},{hi.toFixed(2)}]</span>
      </span>
      <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.mut }} className="rr-note">
        n={a.n} · {a.interval.method} CI
      </span>
    </div>
  );
}

export default function AggregateTable({ aggregates }: { aggregates: Aggregate[] }) {
  // group by metric — one bordered table per metric, per the mockups
  const metrics = [...new Set(aggregates.map((a) => a.metric))];
  const tests = aggregates.filter((a) => a.test);
  return (
    <>
      {metrics.map((metric) => {
        const rows = aggregates.filter((a) => a.metric === metric);
        return (
          <div key={metric} className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
            <div className="wrapline" style={{ justifyContent: "space-between", padding: "10px 14px", borderBottom: `1px solid ${C.line}` }}>
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em" }}>
                {metric.toUpperCase()}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                stored aggregates — rendered, never recomputed here
              </span>
            </div>
            {rows.map((a) => <Row key={`${a.metric}:${a.condition_id}`} a={a} />)}
          </div>
        );
      })}
      {tests.map((a) => {
        const t = a.test!;
        const inconclusive = t.status === "inconclusive";
        return (
          <div key={`test:${a.metric}:${a.condition_id}`} className="mt-2 px-4 py-2.5 wrapline"
            style={{ background: C.panel, border: `1px solid ${inconclusive ? C.amber : C.line}`, borderRadius: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.06em" }}>SIGNIFICANCE</span>
            <span style={{ fontFamily: MONO, fontSize: 11, color: inconclusive ? C.amber : C.green }}>
              {t.name}{t.vs ? ` vs ${t.vs}` : ""}
              {typeof t.p === "number" ? ` · p=${t.p.toFixed(4)}` : ""}
              {typeof t.effective_n === "number" ? ` · effective n=${t.effective_n}` : ""}
              {t.status ? ` · ${t.status}` : ""}
            </span>
            {t.reason && (
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{t.reason}</span>
            )}
          </div>
        );
      })}
    </>
  );
}
