// The AXOR LAB application shell. A separate product on its own domain (violet
// accent) — NOT a Control Plane tab. Router-driven; every surface is reachable
// by a deep link so runs (#/runs/{run_id}), publications (#/e/{publication_id})
// and EvidenceCases (#/e/{publication_id}/evidence/{trace_id}) are addressable.
// Four primary tabs; the rest behind "more…".
import { C, MONO } from "./theme";
import { useQuery } from "@tanstack/react-query";
import { navigate, useRoute } from "./router";
import { api } from "./api";
import Home from "./tabs/Home";
import Governance from "./tabs/Governance";
import LiveModels from "./tabs/LiveModels";
import AgentIngest from "./tabs/AgentIngest";
import RunProgress from "./tabs/RunProgress";
import Results from "./tabs/Results";
import Published from "./tabs/Published";
import PublicationView from "./tabs/PublicationView";
import EvidenceView from "./tabs/EvidenceView";
import ScenarioAuthor from "./tabs/ScenarioAuthor";
import ImportIncident from "./tabs/ImportIncident";
import IncidentView from "./tabs/IncidentView";
import Incidents from "./tabs/Incidents";
import Verify from "./tabs/Verify";
import Builder from "./tabs/Builder";
import Workspace from "./tabs/Workspace";

// The nav reads as words, not route ids, and lists only things that PERSIST and
// that you come back to. Verbs — import an incident, bring an agent, author a
// scenario, compare models — are ways to START something and live where you
// start, on the home page.
//
// There is no "experiments" entry any more. Home IS the experiment — the
// playground, where you pick an agent, point it at tasks and optionally attach a
// gate — so a tab reading "experiments" pointed at the page you were already on.
// Governance sits in the overflow for the same reason it is an optional third
// axis rather than the headline: it is something you can add to a run here, not
// what the Lab is for.
const PRIMARY = [
  { id: "home", label: "playground" },
  { id: "results", label: "results" },
  { id: "published", label: "catalog" },
  { id: "incidents", label: "incidents" },
] as const;
const MORE = [
  // the governance add-on, priced against a published benchmark. It is reached
  // from the playground's governance axis, where the question comes up; the nav
  // entry is for coming back to it, not for discovering it.
  { id: "governance", label: "what a gate costs" },
  { id: "verify", label: "verify a package" },
  { id: "workspace", label: "workspace" },
] as const;

function NavLink({ id, label, active }: { id: string; label: string; active: boolean }) {
  return (
    <button
      onClick={() => navigate(id)}
      style={{
        background: "none", border: "none", padding: "2px 0", cursor: "pointer",
        color: active ? C.text : C.dim, fontSize: 13, fontFamily: MONO,
        borderBottom: `2px solid ${active ? C.violet : "transparent"}`,
      }}
    >
      {label}
    </button>
  );
}

function MoreMenu({ activeKey }: { activeKey: string }) {
  const inMore = MORE.some((m) => m.id === activeKey);
  return (
    <div style={{ position: "relative" }}>
      <details>
        <summary style={{ listStyle: "none", cursor: "pointer", color: inMore ? C.text : C.dim, fontSize: 13, fontFamily: MONO }}>
          more…
        </summary>
        <div style={{ position: "absolute", top: 24, left: 0, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 6, zIndex: 10, minWidth: 160 }}>
          {MORE.map(({ id, label }) => (
            <button
              key={id}
              onClick={() => {
                navigate(id);
                document.querySelectorAll("details[open]").forEach((d) => d.removeAttribute("open"));
              }}
              style={{ display: "block", width: "100%", textAlign: "left", background: "none", border: "none", padding: "8px 12px", cursor: "pointer", color: activeKey === id ? C.text : C.mut, fontSize: 12, fontFamily: MONO }}
            >
              {label}
            </button>
          ))}
        </div>
      </details>
    </div>
  );
}

// Header badge: the live workspace tier from /api/license/status. Community
// (self-hosted / unlicensed) reads "free for research"; a licensed hosted
// workspace shows its tier, clickable through to the workspace surface.
function TierBadge() {
  const q = useQuery({ queryKey: ["license-status"], queryFn: api.licenseStatus, retry: false });
  const active = q.data?.active === true;
  const label = active ? `${q.data?.workspace_tier} workspace` : "free for research";
  return (
    <button
      onClick={() => navigate("workspace")}
      title="workspace entitlement"
      style={{
        background: "none", border: `1px solid ${active ? C.green : C.line}`, borderRadius: 20,
        padding: "3px 10px", cursor: "pointer", fontFamily: MONO, fontSize: 10,
        color: active ? C.green : C.dim,
      }}
    >
      {label}
    </button>
  );
}

// which primary tab a route key highlights
function activeTab(key: string): string {
  if (key === "") return "home";
  if (key === "benchmark") return "governance";
  if (key === "e") return "published";
  return key;
}

export default function App() {
  const route = useRoute();
  const key = route.segments[0] ?? "home";
  const tab = activeTab(key);
  const p1 = route.segments[1];
  const p2 = route.segments[2];
  const p3 = route.segments[3];

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "clamp(14px,4vw,28px) clamp(10px,3vw,20px)" }}>
      <div className="wrapline mb-8" style={{ gap: 10, justifyContent: "space-between", maxWidth: 720, margin: "0 auto 32px" }}>
        <div className="wrapline" style={{ gap: 18 }}>
          <button
            onClick={() => navigate("home")}
            style={{ background: "none", border: "none", cursor: "pointer", fontFamily: MONO, fontSize: 15, fontWeight: 700, color: C.text, padding: 0 }}
          >
            AXOR<span style={{ color: C.violet }}> LAB</span>
          </button>
          <div className="wrapline" style={{ gap: 14 }}>
            {PRIMARY.map(({ id, label }) => (
              <NavLink key={id} id={id} label={label} active={tab === id} />
            ))}
            <MoreMenu activeKey={key} />
          </div>
        </div>
        <TierBadge />
      </div>

      {(key === "home" || key === "") && <Home />}
      {/* `#/benchmark` was this page's route through two earlier shapes, so it
          keeps resolving rather than 404ing someone's saved link */}
      {(key === "governance" || key === "benchmark") && <Governance />}
      {key === "builder" && <Builder />}
      {key === "runs" && <RunProgress runId={p1} />}
      {key === "results" && <Results runId={p1} />}
      {key === "published" && <Published />}
      {key === "e" && p1 && p2 === "evidence" && p3 && (
        <EvidenceView publicationId={p1} traceId={p3} />
      )}
      {key === "e" && p1 && p2 !== "evidence" && <PublicationView publicationId={p1} />}
      {key === "agent-ingest" && <AgentIngest />}
      {key === "scenario-author" && <ScenarioAuthor />}
      {/* Control Plane → Lab cross-link: "Open in Lab" deep-links to #/import;
          an imported incident lives at #/i/{incident_id} */}
      {key === "import" && <ImportIncident />}
      {key === "incidents" && <Incidents />}
      {key === "i" && p1 && <IncidentView incidentId={p1} />}
      {key === "models" && <LiveModels />}
      {key === "verify" && <Verify />}
      {key === "workspace" && <Workspace />}
    </div>
  );
}
