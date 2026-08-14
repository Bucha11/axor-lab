import { useState } from "react";
import { Button, Card, Field } from "../components/ui";
import { IdentityError, login, rememberRefresh, signup } from "../lib/identity";

/** The login gate. A human signs in (or signs up) against the axor-identity
 * service and the access token becomes this session's credential. Operators who
 * hold a static control token can still paste one directly. */
export function Login({ onAuthenticated }: { onAuthenticated: (accessToken: string) => void }) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [orgId, setOrgId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [pasted, setPasted] = useState("");

  async function submit() {
    setBusy(true);
    setError(null);
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

  const canSubmit =
    email.length > 0 && password.length > 0 && (mode === "login" || orgName.length > 0);

  return (
    <div className="screen login">
      <header className="screen-head">
        <h1>{mode === "signup" ? "Create your workspace" : "Log in"}</h1>
        <p className="muted">
          {mode === "signup"
            ? "A new account and its first organization."
            : "Sign in to your Axor workspace."}
        </p>
      </header>
      <Card>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit && !busy) submit();
          }}
        >
          <Field label="Email">
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>
          <Field label="Password">
            <input
              type="password"
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
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
            {busy ? "…" : mode === "signup" ? "Create workspace" : "Log in"}
          </Button>
        </form>
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
      </Card>

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
          <Button onClick={() => pasted && onAuthenticated(pasted)} disabled={!pasted}>
            Use token
          </Button>
        </Card>
      )}
    </div>
  );
}
