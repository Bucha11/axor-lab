import { useEffect, useState } from "react";
import { currentToken, setToken } from "./lib/api";
import { navigate, segments, useRoute } from "./lib/router";
import { Home } from "./screens/Home";
import { Suites } from "./screens/Suites";
import { Playground } from "./screens/Playground";
import { RunReport, Runs, TrialScreen } from "./screens/Runs";
import { EvidenceList, EvidenceScreen } from "./screens/Evidence";
import { RegressionList, RegressionScreen } from "./screens/Regressions";
import { ArtifactList, ArtifactScreen } from "./screens/Artifacts";
import { Builder } from "./screens/Builder";
import { Integrations } from "./screens/Integrations";

const NAV: [string, string][] = [
  ["/", "Home"],
  ["/suites", "Suites"],
  ["/runs", "Runs"],
  ["/evidence", "Evidence"],
  ["/regressions", "Regressions"],
  ["/artifacts", "Artifacts"],
  ["/playground", "Playground"],
  ["/integrations", "Integrations"],
];

/** Every screen endpoint is gated by the control token. It lives in
 * sessionStorage, not localStorage: a token that outlives the tab is a
 * credential nobody remembers granting.
 *
 * It is applied SYNCHRONOUSLY, before the first render. Doing it in an effect
 * loses a race that only shows up on reload: React runs a child's effects
 * before its parent's, so every screen fired its first request — and got a 401 —
 * before the parent had restored the token. The screen then showed a
 * permission error to a user who had already entered one. */
const TOKEN_KEY = "axor-lab-control-token";

export function restoreToken(): string {
  const stored = sessionStorage.getItem(TOKEN_KEY) ?? "";
  setToken(stored);
  return stored;
}

function Screen({ route }: { route: string }) {
  const parts = segments(route);
  const [head, first, second, third] = parts;

  if (parts.length === 0) return <Home />;
  switch (head) {
    case "suites":
      if (first && second === "builder") return <Builder suiteId={first} />;
      if (first) return <Builder suiteId={first} />;
      return <Suites />;
    case "playground":
      return <Playground />;
    case "runs":
      if (first && second === "trials" && third) {
        return <TrialScreen runId={first} trialId={third} />;
      }
      if (first) return <RunReport runId={first} />;
      return <Runs />;
    case "evidence":
      return first ? <EvidenceScreen id={first} /> : <EvidenceList />;
    case "regressions":
      return first ? <RegressionScreen id={first} /> : <RegressionList />;
    case "artifacts":
      return first ? <ArtifactScreen id={first} /> : <ArtifactList />;
    case "integrations":
      return <Integrations />;
    default:
      return (
        <div className="screen">
          <h1>No such screen</h1>
          <p className="muted">{route}</p>
        </div>
      );
  }
}

export function App() {
  const route = useRoute();
  const [token, setLocal] = useState(restoreToken);

  useEffect(() => {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  }, [token]);

  return (
    <div className="app">
      <nav className="nav">
        <div className="brand" onClick={() => navigate("/")}>
          axor<span>lab</span>
        </div>
        {NAV.map(([to, label]) => (
          <a
            key={to}
            href={`#${to}`}
            className={route === to ? "active" : ""}
            onClick={(event) => {
              event.preventDefault();
              navigate(to);
            }}
          >
            {label}
          </a>
        ))}
        <div className="spacer" />
        <input
          className="token"
          type="password"
          placeholder="control token"
          value={token}
          onChange={(event) => {
            setToken(event.target.value);
            setLocal(event.target.value);
          }}
        />
      </nav>
      <main>
        {/* No token, no requests. Every screen endpoint is gated, so firing
            them first produces a wall of 401s and an error panel that reads
            like the server is broken — when what is actually missing is one
            field the user has not filled in yet. */}
        {currentToken() ? (
          // Keyed by the token: entering one REMOUNTS the screen tree, so every
          // `useAsync` re-runs. Without it a user who typed a token sat looking
          // at whatever the first render produced, with no way forward but a
          // manual reload.
          <Screen key={token} route={route} />
        ) : (
          <div className="screen">
            <h1>Control token</h1>
            <p className="muted">
              Every screen endpoint requires the control token this server was
              started with (<code>axor-lab serve --control-token …</code>). Paste
              it above.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
