import { useEffect, useState } from "react";
import { Button, Card, Field } from "../components/ui";
import { api, probeToken } from "../lib/api";
import {
  emailAvailable,
  forgotPassword,
  IdentityError,
  login,
  oauthExchange,
  oauthProviders,
  oauthStartUrl,
  rememberRefresh,
  resetPassword,
  signup,
  takeAuthReturn,
  verifyEmail,
  type OAuthProvider,
} from "../lib/identity";

/** The login gate. A human signs in (or signs up) against the axor-identity
 * service and the access token becomes this session's credential. Operators who
 * hold a static control token can still paste one directly. When the deployment
 * offers it, a guest can start an ephemeral session with no account at all. */
export function Login({
  onAuthenticated,
  guestAvailable = false,
  identityAvailable = true,
  notice = null,
}: {
  /** `guest` is true for an anonymous trial session, so the shell can keep
   * offering a real sign-in instead of a log-out. */
  onAuthenticated: (accessToken: string, guest?: boolean) => void;
  guestAvailable?: boolean;
  /** whether the server trusts an identity service (/auth/status `identity`).
   * Without one, an email/password form is a dead end — the sign-in succeeds
   * at identity and every request then 401s here — so only the control token
   * (and guest, if offered) is shown. */
  identityAvailable?: boolean;
  /** why the user is back here when they did not choose to be */
  notice?: string | null;
}) {
  const [mode, setMode] = useState<"login" | "signup" | "forgot" | "reset">("login");
  const [canMail, setCanMail] = useState(false);
  const [resetToken, setResetToken] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [orgId, setOrgId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showToken, setShowToken] = useState(!identityAvailable);
  const [pasted, setPasted] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [providers, setProviders] = useState<OAuthProvider[]>([]);

  useEffect(() => {
    if (!identityAvailable) return;
    void oauthProviders().then(setProviders);
    void emailAvailable().then(setCanMail);
    // back from GitHub / Google (a one-time code to trade), or from a mailed link
    const back = takeAuthReturn();
    if (back?.error) setError(back.error);
    if (back?.reset) {
      setResetToken(back.reset);
      setMode("reset");
    }
    if (back?.verify) {
      verifyEmail(back.verify)
        .then((r) => setInfo(`${r.email} is confirmed — log in to continue.`))
        .catch((err) =>
          setError(err instanceof IdentityError ? err.message : "could not confirm the address"));
    }
    if (back?.code) {
      setBusy(true);
      oauthExchange(back.code)
        .then((session) => {
          rememberRefresh(session.refresh_token);
          onAuthenticated(session.access_token);
        })
        .catch((err) => setError(err instanceof IdentityError ? err.message : "sign-in failed"))
        .finally(() => setBusy(false));
    }
    // once, on arrival
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);
    if (mode === "forgot" || mode === "reset") {
      try {
        if (mode === "forgot") {
          setInfo((await forgotPassword(email)).detail);
        } else {
          await resetPassword(resetToken!, password);
          setResetToken(null);
          setPassword("");
          setMode("login");
          setInfo("Password changed, and every other session signed out. Log in with the new one.");
        }
      } catch (err) {
        setError(err instanceof IdentityError ? err.message : "that did not work");
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const session =
        mode === "signup"
          ? await signup(email, password, orgName)
          : await login(email, password, orgId || undefined);
      rememberRefresh(session.refresh_token);
      onAuthenticated(session.access_token);
    } catch (err) {
      setError(err instanceof IdentityError ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  async function guest() {
    setBusy(true);
    setError(null);
    try {
      const session = await api.guestSession();
      rememberRefresh(null); // a guest session has no refresh token
      onAuthenticated(session.token, true);
    } catch {
      setError("could not start a guest session");
    } finally {
      setBusy(false);
    }
  }

  async function submitToken() {
    setBusy(true);
    setTokenError(null);
    try {
      // checked BEFORE it becomes the session: accepted blind, a wrong token
      // bounced the user straight back here with nothing said
      await probeToken(pasted);
      rememberRefresh(null); // a control token has no refresh token
      onAuthenticated(pasted);
    } catch (err) {
      setTokenError(err instanceof Error ? err.message : "the token was not accepted");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit =
    mode === "forgot"
      ? email.length > 0
      : mode === "reset"
        ? password.length >= 8
        : email.length > 0 && password.length > 0 && (mode === "login" || orgName.length > 0);

  return (
    <div className="screen login">
      <header className="screen-head">
        <h1>
          {mode === "signup"
            ? "Create your workspace"
            : mode === "forgot"
              ? "Reset your password"
              : mode === "reset"
                ? "Choose a new password"
                : "Log in"}
        </h1>
        <p className="muted">
          {mode === "signup"
            ? "A new account and its first organization."
            : mode === "forgot"
              ? "We will e-mail you a link."
              : mode === "reset"
                ? "At least 8 characters."
                : "Sign in to your Axor workspace."}
        </p>
      </header>
      {info && (
        <p className="muted" role="status" data-testid="login-info">
          {info}
        </p>
      )}
      {notice && (
        <p className="error-text" role="status">
          {notice}
        </p>
      )}
      {/* the form's own errors render inside it; without the form, a guest
          failure still needs somewhere to say so */}
      {error && !identityAvailable && <p className="error-text">{error}</p>}
      {identityAvailable && (
      <Card>
        {providers.length > 0 && (mode === "login" || mode === "signup") && (
          <div className="oauth-buttons">
            {providers.map((p) => (
              <a key={p.name} className="btn btn-secondary" href={oauthStartUrl(p.name)}
                data-testid={`oauth-${p.name}`}>
                Continue with {p.label}
              </a>
            ))}
            <p className="muted small">or with e-mail</p>
          </div>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit && !busy) submit();
          }}
        >
          {mode !== "reset" && (
          <Field label="Email">
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>
          )}
          {mode !== "forgot" && (
          <Field label={mode === "reset" ? "New password" : "Password"}>
            <input
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          )}
          {mode === "signup" && (
            <Field label="Organization name">
              <input value={orgName} onChange={(event) => setOrgName(event.target.value)} />
            </Field>
          )}
          {mode === "login" && (
            <Field
              label="Organization id (optional)"
              hint="Only needed if you belong to more than one organization."
            >
              <input value={orgId} onChange={(event) => setOrgId(event.target.value)} />
            </Field>
          )}
          {error && <p className="error-text">{error}</p>}
          <Button type="submit" disabled={!canSubmit || busy}>
            {busy
              ? "…"
              : mode === "signup"
                ? "Create workspace"
                : mode === "forgot"
                  ? "Send reset link"
                  : mode === "reset"
                    ? "Set password"
                    : "Log in"}
          </Button>
        </form>
        {mode === "login" && canMail && (
          <p className="muted small">
            <a
              href="#"
              onClick={(event) => {
                event.preventDefault();
                setError(null);
                setInfo(null);
                setMode("forgot");
              }}
            >
              Forgot password?
            </a>
          </p>
        )}
        {(mode === "forgot" || mode === "reset") && (
          <p className="muted small">
            <a
              href="#"
              onClick={(event) => {
                event.preventDefault();
                setError(null);
                setMode("login");
              }}
            >
              Back to log in
            </a>
          </p>
        )}
        {(mode === "login" || mode === "signup") && (
        <p className="muted small">
          {mode === "signup" ? "Already have an account? " : "New here? "}
          <a
            href="#"
            onClick={(event) => {
              event.preventDefault();
              setError(null);
              setMode(mode === "signup" ? "login" : "signup");
            }}
          >
            {mode === "signup" ? "Log in" : "Create a workspace"}
          </a>
        </p>
        )}
      </Card>
      )}

      {guestAvailable && (
        <Card>
          <p className="muted small">
            Just want to try it? Start a guest session — nothing is saved and it
            expires.
          </p>
          <Button variant="secondary" onClick={guest} disabled={busy}>
            Try without an account
          </Button>
        </Card>
      )}

      {identityAvailable && (
        <p className="muted small">
          <a
            href="#"
            onClick={(event) => {
              event.preventDefault();
              setShowToken((v) => !v);
            }}
          >
            {showToken ? "Hide" : "Use a control token instead"}
          </a>
        </p>
      )}
      {showToken && (
        <Card>
          <Field
            label="Control token"
            hint="For operators: the static token this server was started with (axor-lab serve --control-token …)."
          >
            <input
              type="password"
              value={pasted}
              onChange={(event) => setPasted(event.target.value)}
            />
          </Field>
          <Button onClick={submitToken} disabled={!pasted || busy}>
            Use token
          </Button>
          {tokenError && <p className="error-text">{tokenError}</p>}
        </Card>
      )}
    </div>
  );
}
