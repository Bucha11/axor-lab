import { useEffect, useState } from "react";
import { api, currentToken, setToken, setUnauthorizedHandler } from "./lib/api";
import { rememberRefresh, refreshAccess } from "./lib/identity";
import { navigate, segments, useRoute } from "./lib/router";
import { Login } from "./screens/Login";
import { Home } from "./screens/Home";
import { Suites } from "./screens/Suites";
import { Playground } from "./screens/Playground";
import { RunReport, Runs, TrialScreen } from "./screens/Runs";
import { EvidenceList, EvidenceScreen } from "./screens/Evidence";
import { RegressionList, RegressionScreen } from "./screens/Regressions";
import { ArtifactList, ArtifactScreen } from "./screens/Artifacts";
import { Builder } from "./screens/Builder";
import { Integrations } from "./screens/Integrations";
import { Handoff } from "./screens/Handoff";

const NAV: [string, string][] = [
  ["/", "Home"],
  ["/suites", "Suites"],
  ["/runs", "Runs"],
  ["/evidence", "Evidence"],
  ["/regressions", "Regressions"],
  ["/artifacts", "Artifacts"],
  ["/handoff", "Handoff"],
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
      // key by the suite id so navigating between suites REMOUNTS the Builder —
      // without it the load effect kept the previous suite's error/manifest and
      // a bad-suite URL left every later suite stuck on that error screen
      if (first) return <Builder key={first} suiteId={first} />;
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
    case "handoff":
      return <Handoff />;
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
  // null = still asking the server. Told once, before the login gate, so a local
  // (open) server never shows a login screen and a hosted one can offer a guest.
  const [authInfo, setAuthInfo] = useState<{ auth_required: boolean; guest: boolean } | null>(
    null,
  );

  useEffect(() => {
    api
      .authStatus()
      .then(setAuthInfo)
      // if the probe itself fails, assume auth is required — safer than
      // rendering screens that will 401
      .catch(() => setAuthInfo({ auth_required: true, guest: false }));
  }, []);

  useEffect(() => {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  }, [token]);

  // When a request comes back 401, try to refresh the access token (identity
  // login) once and continue. If that fails — an identity refresh token that is
  // itself expired, or a guest session that has no refresh token and has expired
  // — drop the credential so the app returns cleanly to the login screen instead
  // of leaving every screen erroring until a manual reload.
  useEffect(() => {
    setUnauthorizedHandler(async () => {
      const next = await refreshAccess();
      if (next) {
        setLocal(next);
        return next;
      }
      setToken(null);
      setLocal("");
      rememberRefresh(null);
      return null;
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  function authenticate(accessToken: string): void {
    setToken(accessToken);
    setLocal(accessToken);
  }

  function logout(): void {
    setToken(null);
    setLocal("");
    rememberRefresh(null);
  }

  if (authInfo === null) {
    return <div className="app" />; // brief: waiting on GET /auth/status
  }

  if (authInfo.auth_required && !currentToken()) {
    // A hosted server gates every screen endpoint; show the login (with a guest
    // option when the deployment offers one). An OPEN server skips this branch —
    // running locally needs no login at all.
    return (
      <div className="app">
        <main>
          <Login onAuthenticated={authenticate} guestAvailable={authInfo.guest} />
        </main>
      </div>
    );
  }

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
        {currentToken() && (
          <button className="linklike" onClick={logout}>
            Log out
          </button>
        )}
      </nav>
      <main>
        {/* Keyed by the token: a new credential REMOUNTS the screen tree, so
            every `useAsync` re-runs against it. */}
        <Screen key={token} route={route} />
      </main>
    </div>
  );
}
