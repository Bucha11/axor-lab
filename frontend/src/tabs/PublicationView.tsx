// One published experiment (lab-published mockup): the immutable public record
// — question, typed claims, the reproduce command, methodology & artifacts
// from the bundle, stated limitations, counted reproductions, citation.
// Data: GET /api/publications/{id} (+ provenance) and, lazily, the
// reproduction package from GET /api/publications/{id}/bundle.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft, BadgeCheck, Check, Copy, GitFork, Lock, Quote, Search, AlertTriangle,
} from "lucide-react";
import { C, MONO, btn } from "../theme";
import { navigate } from "../router";
import { api } from "../api";
import { statusOf } from "../components/Provenance";
import AggregateTable from "../components/AggregateTable";
import Collapse from "../components/Collapse";
import EmptyState, { Cmd } from "../components/EmptyState";

function Kv({ k, v, hash }: { k: string; v: string; hash?: boolean }) {
  return (
    <div className="wrapline" style={{ gap: 8 }}>
      <span style={{ color: C.dim, minWidth: 96 }}>{k}</span>
      <span style={{ color: hash ? C.steel : C.text }}>{v}</span>
    </div>
  );
}

export default function PublicationView({ publicationId }: { publicationId: string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const [showTrials, setShowTrials] = useState(false);
  const pub = useQuery({
    queryKey: ["publication", publicationId],
    queryFn: () => api.getPublication(publicationId),
  });
  // the reproduction package is fetched lazily — it carries every trace body
  const pkg = useQuery({
    queryKey: ["bundle", publicationId],
    queryFn: () => api.getBundle(publicationId),
    enabled: showTrials,
  });

  const copy = (key: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1400);
  };

  if (pub.isLoading) {
    return <div style={{ maxWidth: 660, margin: "0 auto", fontFamily: MONO, fontSize: 11, color: C.dim }}>loading publication…</div>;
  }
  if (pub.isError || !pub.data) {
    return (
      <div style={{ maxWidth: 660, margin: "0 auto" }}>
        <EmptyState title={`publication ${publicationId} unreachable`}>
          {String(pub.error instanceof Error ? pub.error.message : "not found")} — is the publications
          server running, and does this id exist (private publications are never served)?
          <Cmd>python -m lab_server --root ./lab-store --port 8000</Cmd>
        </EmptyState>
      </div>
    );
  }

  const p = pub.data;
  const st = statusOf(p.provenance);
  const StIcon = st.icon;
  const exact = p.claims.filter((c) => c.kind === "exactly_replayable");
  const stat = p.claims.filter((c) => c.kind === "statistically_reproducible");
  const reproCmd = `curl -s http://127.0.0.1:8000/api/publications/${p.publication_id}/bundle > ${p.publication_id}.json
axor-lab replay ${p.publication_id}.json        # governance verdicts, bit-identical`;
  const cite = `@misc{axor-${p.publication_id.slice(0, 8)},
  title  = {${p.question}},
  year   = {2026},
  note   = {Axor Lab, lab.useaxor.net/e/${p.publication_id},
            bundle ${p.bundle_ref.slice(0, 18)}…}
}`;

  return (
    <div style={{ maxWidth: 660, margin: "0 auto" }}>
      <div className="wrapline mb-3" style={{ gap: 8 }}>
        <ArrowLeft size={14} color={C.steel} style={{ cursor: "pointer" }} onClick={() => navigate("published")} />
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.steel, cursor: "pointer" }} onClick={() => navigate("published")}>published</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>/ e/{p.publication_id}</span>
      </div>

      {/* status + immutability */}
      <div className="wrapline mb-3" style={{ gap: 10 }}>
        <span className="flex items-center gap-1.5" title={st.note}
          style={{ fontFamily: MONO, fontSize: 10, color: st.color, border: `1px solid ${st.color}`, borderRadius: 4, padding: "3px 9px" }}>
          <StIcon size={12} /> {st.label}
        </span>
        {p.integrity === "signed" && (
          <span className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 10, color: C.steel, border: `1px solid ${C.steel}`, borderRadius: 4, padding: "3px 9px" }}>
            <Lock size={11} /> signed bundle
          </span>
        )}
        {p.integrity === "hash_verified" && (
          <span className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 10, color: C.mut, border: `1px solid ${C.line}`, borderRadius: 4, padding: "3px 9px" }}>
            <BadgeCheck size={11} /> hash-verified
          </span>
        )}
        {p.provenance.reproductions.count > 0 && (
          <span className="flex items-center gap-1.5" style={{ fontFamily: MONO, fontSize: 10, color: C.violet, border: `1px solid ${C.violet}`, borderRadius: 4, padding: "3px 9px" }}>
            <GitFork size={11} /> reproduced ×{p.provenance.reproductions.count}
          </span>
        )}
        {p.immutable && <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>immutable</span>}
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>{p.license}</span>
      </div>

      <h1 style={{ fontSize: 21, fontWeight: 680, lineHeight: 1.25, margin: "0 0 6px" }}>{p.question}</h1>
      <div className="wrapline" style={{ gap: 10, marginBottom: 20 }}>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>origin: {p.origin}</span>
        {p.statistics_integrity && (
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>statistics: {p.statistics_integrity}</span>
        )}
      </div>

      {/* claims — typed by what supports them */}
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
        <div className="px-4 py-2.5">
          <div style={{ fontFamily: MONO, fontSize: 9, color: C.dim, letterSpacing: "0.06em", marginBottom: 4 }}>
            CLAIMS — typed by what supports them
          </div>
          {exact.map((c, i) => (
            <div key={`e${i}`} className="flex items-start gap-2" style={{ marginBottom: 3 }}>
              <span style={{ fontFamily: MONO, fontSize: 8.5, color: C.steel, border: `1px solid ${C.steel}`, borderRadius: 3, padding: "1px 5px", flexShrink: 0 }}>exact</span>
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>{c.text}</span>
            </div>
          ))}
          {stat.map((c, i) => (
            <div key={`s${i}`} className="flex items-start gap-2" style={{ marginBottom: 3 }}>
              <span style={{ fontFamily: MONO, fontSize: 8.5, color: C.amber, border: `1px solid ${C.amber}`, borderRadius: 3, padding: "1px 5px", flexShrink: 0 }}>stat</span>
              <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>{c.text}</span>
            </div>
          ))}
          {p.claims.length === 0 && (
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>no claims recorded</span>
          )}
        </div>
      </div>

      {/* investigate + result table (from the bundle, on demand) */}
      <div className="wrapline mt-3" style={{ gap: 8 }}>
        <button onClick={() => setShowTrials(!showTrials)}
          style={btn({ color: C.steel, border: `1px solid ${C.steel}`, padding: "7px 13px" })}>
          <Search size={12} /> {showTrials ? "hide trials" : "Investigate a trial"}
        </button>
        <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
          loads the reproduction package (bundle + every trace)
        </span>
      </div>

      {showTrials && pkg.isLoading && (
        <div className="mt-2" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>loading bundle…</div>
      )}
      {showTrials && pkg.data && (
        <>
          {pkg.data.bundle.aggregates?.length > 0 && <AggregateTable aggregates={pkg.data.bundle.aggregates} />}
          <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.steel}`, borderRadius: 8, overflow: "hidden" }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", padding: "10px 14px", borderBottom: `1px solid ${C.line}` }}>
              TRACES — one EvidenceCase per trial
            </div>
            {pkg.data.traces.map((t) => {
              const gate = t.events.find((e) => e.type === "gate_decision");
              const verdict = gate?.decision?.verdict;
              return (
                <div key={t.trace_id} className="wrapline px-4 py-2"
                  onClick={() => navigate(`e/${p.publication_id}/evidence/${t.trace_id}`)}
                  style={{ borderTop: `1px solid ${C.line}`, cursor: "pointer", justifyContent: "space-between" }}>
                  <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.text }}>
                    {t.trial?.scenario_id ?? t.trace_id}
                    <span style={{ color: C.dim }}> · {t.trial?.condition_id ?? "?"} · r{t.trial?.repeat_index ?? "?"}</span>
                  </span>
                  <span className="flex items-center gap-2">
                    {verdict && (
                      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 700, color: verdict === "DENY" ? C.green : C.red, border: `1px solid ${verdict === "DENY" ? C.green : C.red}`, borderRadius: 3, padding: "1px 6px" }}>
                        {verdict}
                      </span>
                    )}
                    <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{t.events.length} events</span>
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* reproduce */}
      <div className="mt-4" style={{ background: C.panel, border: `1px solid ${C.violet}`, borderRadius: 8, padding: 14 }}>
        <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}>Reproduce</span>
          <button onClick={() => copy("cmd", reproCmd)}
            style={btn({ padding: "3px 9px", fontSize: 10, color: copied === "cmd" ? C.green : C.mut })}>
            {copied === "cmd" ? <Check size={11} /> : <Copy size={11} />} {copied === "cmd" ? "copied" : "copy"}
          </button>
        </div>
        <pre style={{ margin: 0, background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 10, color: C.mut, overflow: "auto", lineHeight: 1.6 }}>{reproCmd}</pre>
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 6 }}>
          replay reproduces the governance conclusion exactly; a fresh run is a new stochastic sample (CI over repeats)
        </div>
      </div>

      {/* methodology & artifacts */}
      <div className="mt-3">
        <Collapse title="methodology & artifacts — the bundle that makes this reproducible">
          {!showTrials && !pkg.data ? (
            <button onClick={() => setShowTrials(true)} style={btn()}>load the bundle to see methodology</button>
          ) : pkg.data ? (
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.9 }}>
              <Kv k="kernel version" v={String(pkg.data.bundle.environment?.kernel_version ?? "—")} hash />
              <Kv k="bundle ref" v={p.bundle_ref} hash />
              <Kv k="model" v={`${String(pkg.data.bundle.environment?.model?.provider ?? "?")} · ${String(pkg.data.bundle.environment?.model?.id ?? "?")}`} />
              <Kv k="scenarios" v={String(pkg.data.bundle.scenarios?.length ?? 0)} />
              <Kv k="trials" v={String(pkg.data.bundle.trials?.length ?? 0)} />
              <Kv k="artifacts" v="config · traces · verdicts · aggregates · receipt · acceptance" />
              <div style={{ color: C.dim, marginTop: 6, lineHeight: 1.5 }}>
                the kernel version is load-bearing: the same traces under a different decide can yield a
                different verdict, so it is pinned in the bundle.
              </div>
            </div>
          ) : (
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>loading bundle…</span>
          )}
        </Collapse>
      </div>

      {/* limitations — stated, not hidden */}
      <div className="mt-3">
        <Collapse title="limitations" border={C.amber} color={C.amber} defaultOpen>
          {p.limitations.length ? p.limitations.map((l, i) => (
            <div key={i} className="flex items-start gap-2" style={{ marginBottom: 6 }}>
              <AlertTriangle size={11} color={C.amber} style={{ marginTop: 2, flexShrink: 0 }} />
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, lineHeight: 1.7 }}>{l}</span>
            </div>
          )) : (
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>none recorded</span>
          )}
        </Collapse>
      </div>

      {/* reproductions */}
      <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}>Independent reproductions</span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.steel }}>
            {p.provenance.reproductions.count} total · {p.provenance.reproductions.verified} verified
          </span>
        </div>
        {p.provenance.reproductions.count === 0 ? (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            none yet — append one with POST /api/publications/{p.publication_id}/reproductions
          </div>
        ) : (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>
            kinds: {p.provenance.reproductions.kinds.join(", ") || "—"}
          </div>
        )}
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 6 }}>
          counted, not assumed — and typed: exact_replay (verdict, bit-identical) vs fresh_live (new sample,
          within CI) vs changed_model/kernel (robustness, a different claim)
        </div>
      </div>

      {/* citation */}
      <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        <div className="wrapline" style={{ justifyContent: "space-between", marginBottom: 8 }}>
          <span className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}>
            <Quote size={12} color={C.mut} /> Cite
          </span>
          <button onClick={() => copy("cite", cite)}
            style={btn({ padding: "3px 9px", fontSize: 10, color: copied === "cite" ? C.green : C.mut })}>
            {copied === "cite" ? <Check size={11} /> : <Copy size={11} />} {copied === "cite" ? "copied" : "copy"}
          </button>
        </div>
        <pre style={{ margin: 0, background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 6, padding: 10, fontFamily: MONO, fontSize: 9.5, color: C.mut, overflow: "auto", whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{cite}</pre>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 16, lineHeight: 1.7 }}>
        An immutable public record: question, result with honest CI, the full bundle that reproduces it, stated
        limitations, counted independent reproductions, and a citation. This is what a published Lab run is — not a
        screenshot of a number, but an artifact another researcher can re-run, fork, and cite.
      </div>
    </div>
  );
}
