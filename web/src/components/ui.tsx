import type { ReactNode } from "react";
import { navigate } from "../lib/router";

/** Shared primitives from the board's component set (§5): Button, Card, Tag,
 * Input, plus the three states every data screen has to render. */

export function Card({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <div className={`card${onClick ? " card-clickable" : ""}`} onClick={onClick}>
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button className={`btn btn-${variant}`} onClick={onClick} disabled={disabled} type={type}>
      {children}
    </button>
  );
}

const TONES = ["default", "success", "warning", "danger", "info"] as const;
export type Tone = (typeof TONES)[number];

export function Tag({ children, tone = "default" }: { children: ReactNode; tone?: Tone }) {
  return <span className={`tag tag-${tone}`}>{children}</span>;
}

/** The one place an outcome word becomes a colour.
 *
 * `error` is NOT `failed`: an invariant that could not be evaluated has not been
 * satisfied and has not been violated, and painting it red says the run broke
 * something. Warning, and the word itself. */
export function outcomeTone(status: string): Tone {
  switch (status) {
    case "passed":
    case "completed":
      return "success";
    case "failed":
      return "danger";
    case "error":
      return "warning";
    case "skipped":
      return "info";
    default:
      return "default";
  }
}

export function Link({ to, children }: { to: string; children: ReactNode }) {
  return (
    <a
      href={`#${to}`}
      onClick={(event) => {
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

export function Loading() {
  return <p className="muted">Loading…</p>;
}

/** A failed request says so. It never renders as an empty workspace. */
export function Failed(
  { error, status, onRetry }: { error: string; status?: number | null; onRetry?: () => void },
) {
  // 402 is not a failure of the screen, it is an ANSWER: the request was
  // well-formed and authorised and the plan does not include it. It is also the
  // only error here a user can fix, and it used to render as a red sentence
  // like any other — the server refused, and nothing said where to go.
  const planned = status === 402;
  return (
    <div className="failed">
      <strong>{planned ? "Your plan does not include this." : "Could not load this screen."}</strong>
      <p className="muted">{error}</p>
      {planned && (
        <p className="muted small">
          <a href="#/workspace">Workspace</a> lists the plans and what each one grants.
        </p>
      )}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted empty">{children}</p>;
}

export function Field(
  { label, hint, children }: { label: string; hint?: string; children: ReactNode },
) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint muted small">{hint}</span>}
    </label>
  );
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

/** A failed ACTION (a button's request), or a failed SECTION of a screen that
 * otherwise loaded. Distinct from `Failed`, which speaks for the whole screen:
 * a refused confirm or an unreachable results list is not "Could not load this
 * screen", and replacing everything the user was looking at with that sentence
 * hid the context they needed to act on the error.
 *
 * `role="alert"` so the message is announced — it appears in response to a
 * click, somewhere the user may not be looking. */
export function InlineError(
  { error, status, onRetry }: { error: string; status?: number | null; onRetry?: () => void },
) {
  return (
    <div className="inline-error" role="alert">
      <p className="error-text">{error}</p>
      {/* the same 402 hint Failed gives — the one refusal a user can fix */}
      {status === 402 && (
        <p className="muted small">
          Your plan does not include this. <a href="#/workspace">Workspace</a> lists
          the plans and what each one grants.
        </p>
      )}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
