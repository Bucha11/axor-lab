import { useEffect, useState } from "react";
import {
  api, currentToken, setToken, setUnauthorizedHandler, type AuthStatus, type GuestClaim,
} from "./lib/api";
import {
  AUTH_RETURN,
  logout as identityLogout,
  rememberRefresh,
  refreshAccess,
} from "./lib/identity";
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

/** Whether the credential in this tab is an anonymous guest session. A guest
 * is shown "Log in", not "Log out", and an expired guest is quietly replaced
 * by a fresh one instead of being bounced to a login screen. */
const GUEST_KEY = "axor-lab-guest";

/** One guest mint at a time. React runs effects twice in development, and a
 * page that opens with two requests in flight would spend two of the server's
 * capped guest slots on one visitor. */
let guestMint: Promise<string | null> | null = null;
/** When the last guest was minted. A guest that 401s within a minute of being
 * created did not EXPIRE (they last 30) — the server is refusing it, and minting
 * another would only be refused too, forever. */
let lastGuestAt = 0;
const GUEST_RETRY_MS = 60_000;
function mintGuest(): Promise<string | null> {
  if (!guestMint) {
    guestMint = api
      .guestSession()
      .then((s) => {
        rememberRefresh(null); // a guest session has no refresh token
        lastGuestAt = Date.now();
        return s.token;
      })
      .catch(() => null)
      .finally(() => {
        guestMint = null;
      });
  }
  return guestMint;
}

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
  const [isGuest, setIsGuest] = useState(() => sessionStorage.getItem(GUEST_KEY) === "1");
  // the sign-in panel, opened over the product rather than in place of it
  // — open from the start when the browser is back from GitHub / Google, or
  // from an e-mailed link: the panel's Login is what finishes either
  const [signingIn, setSigningIn] = useState(() => AUTH_RETURN.test(window.location.search));
  // a guest could not be started (capacity, or the route is off): only then
  // does a hosted server fall back to the full-page login
  const [guestFailed, setGuestFailed] = useState(false);
  // what a sign-in carried over from the guest session, said once
  const [carried, setCarried] = useState<string | null>(null);

  useEffect(() => {
    if (isGuest) sessionStorage.setItem(GUEST_KEY, "1");
    else sessionStorage.removeItem(GUEST_KEY);
  }, [isGuest]);

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

  // No wall in front of the product. A hosted server that offers guest
  // sessions starts one on its own, so a visitor lands on Lab itself — the
  // screens, the suites, a trial they can run — and signs in only when they
  // want to keep something. Re-runs after a log-out (session bump), which
  // drops a signed-in user back to a guest rather than to a login page.
  useEffect(() => {
    if (!authInfo?.auth_required || !authInfo.guest || guestFailed) return;
    // an open sign-in panel is a question to the user; do not answer it for
    // them by slipping a guest in behind it
    if (currentToken() || signingIn) return;
    let alive = true;
    void mintGuest().then((t) => {
      if (!alive) return;
      if (!t) {
        setGuestFailed(true);
        return;
      }
      // not authenticate(): that closes the sign-in panel and clears its
      // notice, and an expired user who is being asked to sign in again must
      // keep both — the guest only fills in the product behind the panel
      setToken(t);
      setLocal(t);
      setIsGuest(true);
      setSession((n) => n + 1);
    });
    return () => {
      alive = false;
    };
  }, [authInfo, guestFailed, session, signingIn]);

  // When a request comes back 401, try to refresh the access token (identity
  // login) once and continue. If that fails, a GUEST is quietly given a fresh
  // guest session — it held nothing worth a login prompt — while a signed-in
  // user is asked to sign in again, over the screen they were on.
  useEffect(() => {
    setUnauthorizedHandler(async () => {
      const next = await refreshAccess();
      if (next) {
        setLocal(next);
        return next;
      }
      if (
        sessionStorage.getItem(GUEST_KEY) === "1" &&
        Date.now() - lastGuestAt > GUEST_RETRY_MS
      ) {
        const fresh = await mintGuest();
        if (fresh) {
          authenticate(fresh, true);
          return fresh;
        }
      }
      setToken(null);
      setLocal("");
      rememberRefresh(null);
      setIsGuest(false);
      setNotice("Your session expired — sign in again.");
      setSigningIn(true);
      setSession((n) => n + 1);
      return null;
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  function authenticate(accessToken: string, guest = false): void {
    const guestToken = !guest && sessionStorage.getItem(GUEST_KEY) === "1" ? currentToken() : "";
    setToken(accessToken);
    if (guestToken) {
      // signing in from a guest session: take what it made into the account
      // before the screens reload, so they reload WITH it
      void api
        .claimGuest(guestToken)
        .then((claim) => {
          setCarried(describeClaim(claim));
          setSession((n) => n + 1);
        })
        .catch(() => {
          /* an expired guest has nothing left to move; the sign-in stands */
        });
    }
    setLocal(accessToken);
    setIsGuest(guest);
    setNotice(null);
    setSigningIn(false);
    setSession((n) => n + 1);
  }

  function logout(): void {
    // revoke the identity refresh token server-side (best-effort, and a no-op
    // for a pasted control token or a guest, which hold none). A guest session
    // has no release endpoint on the server; it lapses at its own expiry.
    void identityLogout();
    setToken(null);
    setLocal("");
    rememberRefresh(null);
    setIsGuest(false);
    setNotice(null);
    setSession((n) => n + 1);
  }

  if (authInfo === null) {
    return <div className="app" />; // brief: waiting on GET /auth/status
  }

  if (authInfo.auth_required && !currentToken() && authInfo.guest && !guestFailed && !signingIn) {
    return <div className="app" />; // brief: a guest session is being started
  }

  if (authInfo.auth_required && !currentToken() && !signingIn) {
    // Only when there is no way in without an account: a private install that
    // offers no guests, or a public one whose guest slots are all taken. An
    // OPEN server never reaches this — running locally needs no login at all.
    return (
      <div className="app">
        <main>
          <Login
            onAuthenticated={authenticate}
            guestAvailable={authInfo.guest}
            // absent (an older server) is "unknown", which keeps the form
            identityAvailable={authInfo.identity !== false}
            notice={guestFailed && authInfo.guest
              ? "Guest sessions are full right now — sign in, or try again in a few minutes."
              : notice}
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
        <VersionBadge />
        {isGuest ? (
          <>
            <span className="muted small" title="Nothing is saved; it expires after 30 minutes">
              guest
            </span>
            <button className="btn btn-primary btn-sm" onClick={() => setSigningIn(true)}>
              Log in
            </button>
          </>
        ) : (
          currentToken() && (
            <button className="linklike" onClick={logout}>
              Log out
            </button>
          )
        )}
      </nav>
      {signingIn && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            // close only when nothing would be lost: a guest or signed-in user
            // has the product behind the panel; an expired session does not
            if (e.target === e.currentTarget && currentToken()) setSigningIn(false);
          }}
        >
          <div className="modal" role="dialog" aria-label="Log in">
            {currentToken() && (
              <button className="linklike modal-close" onClick={() => setSigningIn(false)} aria-label="Close">
                ×
              </button>
            )}
            <Login
              onAuthenticated={authenticate}
              // with no credential behind the panel, "try without an account"
              // is the way back to the product for someone who does not want
              // to sign in again
              guestAvailable={authInfo.guest && !currentToken()}
              identityAvailable={authInfo.identity !== false}
              notice={notice}
            />
          </div>
        </div>
      )}
      <main>
        {/* Keyed by the SESSION, not the token: a new credential remounts the
            screen tree so every `useAsync` re-runs against it, while a refresh
            (same session, fresher token) leaves what is on screen alone. */}
        {carried && (
          <p className="banner" role="status">
            {carried}{" "}
            <button className="linklike" onClick={() => setCarried(null)} aria-label="Dismiss">
              ×
            </button>
          </p>
        )}
        {currentToken() || !authInfo.auth_required ? (
          <Screen key={session} route={route} />
        ) : null}
      </main>
    </div>
  );
}

/** Which build is answering. The KERNEL version is on screen because that is
 * what decides what Lab's numbers mean; the rest of the stack is in the
 * tooltip, where an operator who is diffing a deploy will look for it.
 *
 * Renders NOTHING when the call does not answer — an older server that has no
 * `/version`, or a session whose token has lapsed. A deploy check that shows a
 * blank where a version should be reads as a broken page; no badge at all
 * reads as a server that predates the badge, which is the truth.
 */
function VersionBadge() {
  const [versions, setVersions] = useState<Record<string, string | null> | null>(
    null,
  );
  useEffect(() => {
    let alive = true;
    api
      .version()
      .then((v) => {
        if (alive) setVersions(v.versions);
      })
      .catch(() => {
        /* older server, or no credential yet: say nothing */
      });
    return () => {
      alive = false;
    };
  }, []);
  const kernel = versions?.["axor-core"];
  if (!kernel) return null;
  const detail = Object.entries(versions ?? {})
    .filter(([, v]) => v)
    .map(([name, v]) => `${name} ${v}`)
    .join("\n");
  return (
    <span className="version-badge" title={detail}>
      kernel {kernel}
    </span>
  );
}

const KIND_LABEL: Record<string, [string, string]> = {
  suite: ["suite", "suites"],
  scenario: ["scenario", "scenarios"],
  "evidence-case": ["EvidenceCase", "EvidenceCases"],
  regression: ["regression", "regressions"],
  artifact: ["artifact", "artifacts"],
  publication: ["publication", "publications"],
};

/** "Moved from your guest session: 2 suites, 1 run." — or null when the guest
 * had made nothing, which deserves no banner. */
export function describeClaim(claim: GuestClaim): string | null {
  const parts: string[] = [];
  for (const [kind, n] of Object.entries(claim.documents)) {
    const [one, many] = KIND_LABEL[kind] ?? [kind, kind];
    parts.push(`${n} ${n === 1 ? one : many}`);
  }
  if (claim.runs) parts.push(`${claim.runs} ${claim.runs === 1 ? "run" : "runs"}`);
  let text = parts.length ? `Moved from your guest session: ${parts.join(", ")}.` : "";
  if (claim.left_running) {
    text += `${text ? " " : ""}${claim.left_running} ${
      claim.left_running === 1 ? "run was" : "runs were"
    } still running and stayed behind.`;
  }
  return text || null;
}
