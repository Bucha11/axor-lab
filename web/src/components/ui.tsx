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
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
}) {
  return (
    <button className={`btn btn-${variant}`} onClick={onClick} disabled={disabled}>
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
export function Failed({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="failed">
      <strong>Could not load this screen.</strong>
      <p className="muted">{error}</p>
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
