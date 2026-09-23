import { useState } from "react";
import { Button } from "./ui";

/** A credential the server returns exactly once — an ingest key, a tenant or
 * member token. No endpoint returns it again, so this render IS the handover:
 * a screen that said "issued, shown once" without showing it (Integrations did,
 * for both kinds of runtime) minted a key nobody could ever use. The value is
 * rendered selectable AND copyable, because a copy that silently fails in a
 * non-secure context must still leave the user something to select by hand. */
export function ShownOnce({
  value,
  label,
  testId,
}: {
  value: string;
  label: string;
  testId?: string;
}) {
  const [copied, setCopied] = useState<"" | "yes" | "no">("");

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied("yes");
    } catch {
      // clipboard needs a secure context and a permission; without them the
      // value is still on screen to select
      setCopied("no");
    }
  }

  return (
    <div className="shown-once" data-testid={testId}>
      <p className="small">
        <strong>{label}</strong>
      </p>
      <div className="row">
        {/* inline: app.css has no rule for a long unbroken token, and one
            this wide pushed the Copy button off a narrow screen */}
        <code className="secret" style={{ wordBreak: "break-all", userSelect: "all" }}>
          {value}
        </code>
        <Button variant="secondary" onClick={copy}>
          {copied === "yes" ? "Copied" : "Copy"}
        </Button>
      </div>
      <p className="muted small">
        Shown once — copy it now. No screen or endpoint returns it again; lose
        it and you issue a new one.
        {copied === "no" && " (Copy is unavailable here; select the value instead.)"}
      </p>
    </div>
  );
}
