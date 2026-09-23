import { useEffect, useState } from "react";
import {
  api, currentToken, setToken, setUnauthorizedHandler, type AuthStatus,
} from "./lib/api";
import { logout as identityLogout, rememberRefresh, refreshAccess } from "./lib/identity";
import { consumeBillingParams, pendingPlan } from "./lib/billing";
import { navigate, segments, useRoute } from "./lib/router";
import { Login } from "./screens/Login";
import { Home } from "./screens/Home";
import { Suites } from "./screens/Suites";
import { Playground } from "./screens/Playground";
import { RunReport, Runs, TrialScreen } from "./screens/Runs";
import { EvidenceList, EvidenceScreen } from "./screens/Evidence";
import { RegressionList, RegressionScreen } from "./screens/Regressions";
import { ArtifactList, ArtifactScreen, PublicationScreen } from "./screens/Artifacts";
import { Builder } from "./screens/Builder";
import { Integrations } from "./screens/Integrations";
import { Handoff } from "./screens/Handoff";
import { Workspace } from "./screens/Workspace";
import { ScenarioList, ScenarioScreen } from "./screens/Scenarios";
import { RegistrySuiteScreen } from "./screens/Registry";

const NAV: [string, string][] = [
  ["/", "Home"],
  ["/suites", "Suites"],
  ["/scenarios", "Scenarios"],
  ["/runs", "Runs"],
  ["/evidence", "Evidence"],
  ["/regressions", "Regressions"],
  ["/artifacts", "Artifacts"],
  ["/handoff", "Handoff"],
  ["/playground", "Playground"],
  ["/integrations", "Integrations"],
  ["/workspace", "Workspace"],
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

/** The nav entry a route belongs to. Only an EXACT match was highlighted, so
 * every detail page (a run, a trial, an artifact) left the nav with nothing
 * lit — the user lost where they were the moment they clicked into anything.
 * A route that is not under any entry (`/publications/…`, `/registry/…`)
 * borrows the section its content lives in. */
const PARENT: Record<string, string> = {
  publications: "/artifacts",
  registry: "/suites",
};

export function navSection(route: string): string {
  const [head] = segments(route);
  if (!head) return "/";
  return PARENT[head] ?? `/${head}`;
}

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
    case "publications":
      // the list lives on the Artifacts screen; this is one entry of it
      return first ? <PublicationScreen key={first} id={first} /> : <ArtifactList />;
    case "scenarios":
      return first ? <ScenarioScreen key={first} name={first} /> : <ScenarioList />;
    case "registry":
      // `#/registry/suites/{id}`: one org-shared suite, read-only
      if (first === "suites" && second) {
        return <RegistrySuiteScreen key={second} id={second} />;
      }
      return <Suites />;
    case "handoff":
      return <Handoff />;
    case "integrations":
      return <Integrations />;
    case "workspace":
      return <Workspace />;
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
  const [authInfo, setAuthInfo] = useState<AuthStatus | null>(null);
  // Bumped when the CREDENTIAL changes identity — a login, a logout, a pasted
  // token — and NOT when a refresh swaps the access token for a fresher copy of
  // the same session. The screen tree used to be keyed by the token itself, so
  // every silent refresh remounted every screen: a half-filled form, an open
  // trial, a Builder draft all vanished at the moment the refresh was supposed
  // to make the expiry invisible.
  const [session, setSession] = useState(0);
  // why the user is looking at the login screen again, when it was not their
  // choice — an expired session used to bounce them there without a word
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api
      .authStatus()
      .then(setAuthInfo)
      // if the probe itself fails, assume auth is required — safer than
      // rendering screens that will 401
      .catch(() => setAuthInfo({ auth_required: true, guest: false }));
  }, []);

  // Back from checkout (?billing=) or arriving from a landing page's ?plan=
  // link: both are finished on the Workspace screen, where the plan lives.
  // Remount once done, so the screens read the session the refresh produced.
  useEffect(() => {
    void consumeBillingParams().then((present) => {
      if (!present) return;
      navigate("/workspace");
      setSession((n) => n + 1);
    });
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
      setNotice("Your session expired — sign in again.");
      setSession((n) => n + 1);
      return null;
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  function authenticate(accessToken: string): void {
    setToken(accessToken);
    setLocal(accessToken);
    setNotice(null);
    setSession((n) => n + 1);
    // a plan picked on the landing page before signing up: buy it now
    if (pendingPlan()) navigate("/workspace");
  }

  function logout(): void {
    // revoke the identity refresh token server-side (best-effort, and a no-op
    // for a pasted control token or a guest, which hold none). A guest session
    // has no release endpoint on the server; it lapses at its own expiry.
    void identityLogout();
    setToken(null);
    setLocal("");
    rememberRefresh(null);
    setNotice(null);
    setSession((n) => n + 1);
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
          <Login
            onAuthenticated={authenticate}
            guestAvailable={authInfo.guest}
            // absent (an older server) is "unknown", which keeps the form
            identityAvailable={authInfo.identity !== false}
            notice={notice}
          />
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
            className={navSection(route) === to ? "active" : ""}
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
        {/* Keyed by the SESSION, not the token: a new credential remounts the
            screen tree so every `useAsync` re-runs against it, while a refresh
            (same session, fresher token) leaves what is on screen alone. */}
        <Screen key={session} route={route} />
      </main>
    </div>
  );
}
