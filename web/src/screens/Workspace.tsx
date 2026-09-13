import { useState } from "react";
import { api, type AuditEntry, type Plan } from "../lib/api";
import { useAsync } from "../lib/useAsync";
import { Button, Card, Empty, Failed, Field, Loading, Stat, Tag } from "../components/ui";

/** A capability a plan grants. Shown as the plan's own word, never translated —
 * the server gates on these exact strings, so a prettier label here would be a
 * second vocabulary for the same thing. */
function Capabilities({ items }: { items: string[] }) {
  if (items.length === 0) return <span className="muted small">none</span>;
  return (
    <>
      {items.map((capability) => (
        <Tag key={capability}>{capability}</Tag>
      ))}
    </>
  );
}

function limit(value: number | null): string {
  // null is UNLIMITED, and rendering it as "0" would invert the meaning of the
  // most permissive plan into the most restrictive one.
  return value === null ? "unlimited" : String(value);
}

function when(at: number): string {
  return new Date(at * 1000).toISOString().replace("T", " ").slice(0, 19);
}

function PlanCard({
  plan,
  current,
  onBuy,
  onGrant,
  busy,
  canAdmin,
}: {
  plan: Plan;
  current: boolean;
  onBuy: (id: string) => void;
  onGrant: (id: string) => void;
  busy: boolean;
  canAdmin: boolean;
}) {
  return (
    <Card>
      <h4>
        {plan.name} {current && <Tag tone="success">current</Tag>}
      </h4>
      <p className="muted small">
        {plan.price_usd === 0 ? "free" : `$${plan.price_usd} / month`}
      </p>
      <div className="grid">
        <Stat label="Suites" value={limit(plan.max_suites)} />
        <Stat label="Artifacts" value={limit(plan.max_artifacts)} />
        <Stat label="Hosted runtimes" value={limit(plan.max_hosted_runtimes)} />
      </div>
      <p className="muted small">
        <Capabilities items={plan.capabilities} />
      </p>
      {!current && (
        <>
          <Button onClick={() => onBuy(plan.name)} disabled={busy}>
            Subscribe
          </Button>{" "}
          {canAdmin && (
            <Button variant="secondary" onClick={() => onGrant(plan.name)} disabled={busy}>
              Grant without paying
            </Button>
          )}
        </>
      )}
    </Card>
  );
}

export function Workspace() {
  const workspace = useAsync(() => api.currentWorkspace());
  const plans = useAsync(() => api.plans());
  const members = useAsync(() => api.members());
  // Listing every tenant is an ADMIN act — a tenant must not enumerate the
  // others — so this 403s for anyone else and the section stays hidden.
  const tenants = useAsync(() => api.workspaces());
  const [newName, setNewName] = useState("");
  const [issued, setIssued] = useState<{ role: string; token: string } | null>(null);
  const [role, setRole] = useState("member");
  const [audit, setAudit] = useState<AuditEntry[] | null>(null);
  const [checkout, setCheckout] = useState<string | null>(null);
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);

  const current = workspace.data;
  const isAdmin = current?.role === "owner" || current?.role === "admin";

  async function guard(work: () => Promise<void>) {
    setBusy(true);
    setFailure("");
    try {
      await work();
    } catch (caught) {
      setFailure(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  const buy = (planId: string) =>
    guard(async () => setCheckout((await api.checkout(planId)).checkout_url));
  const grant = (planId: string) =>
    guard(async () => {
      await api.grantPlan(planId);
      setCheckout(null);
      workspace.reload();
    });
  const invite = () =>
    guard(async () => setIssued(await api.addMember(role)));
  const loadAudit = () => guard(async () => setAudit((await api.audit()).audit));
  const createTenant = () =>
    guard(async () => {
      await api.createWorkspace(newName);
      setNewName("");
      tenants.reload();
    });

  if (workspace.loading) return <Loading />;
  if (workspace.error) return <Failed error={workspace.error} onRetry={workspace.reload} />;
  if (!current) return null;

  return (
    <div className="screen">
      <header className="screen-head">
        <h1>Workspace</h1>
        <p className="muted">
          A workspace is a tenant: its own token, its own data, its own plan. What
          the plan grants is what the server gates on — the capability names below
          are the same strings it checks.
        </p>
      </header>

      {failure && <Failed error={failure} />}

      <Card>
        <h4>
          {current.name} <Tag>{current.id}</Tag>{" "}
          {current.role && <Tag tone="info">you are {current.role}</Tag>}
        </h4>
        <div className="grid">
          <Stat label="Plan" value={current.subscription.plan_id} />
          <Stat label="Status" value={current.subscription.status} />
          <Stat label="Org" value={current.org ?? "—"} />
          <Stat label="Created" value={when(current.created_at)} />
        </div>
        <p className="muted small">
          Capabilities: <Capabilities items={current.plan.capabilities} />
        </p>
      </Card>

      <section>
        <h3>Members</h3>
        <p className="muted small">
          A member token carries a role. Roles bound what a caller may do, and the
          token is shown once — nothing here can read it back.
        </p>
        {members.error && <Failed error={members.error} onRetry={members.reload} />}
        {(members.data?.members ?? []).length === 0 ? (
          <Empty>No members recorded.</Empty>
        ) : (
          <ul className="rows">
            {(members.data?.members ?? []).map((row) => (
              <li key={row.role}>
                <Tag>{row.role}</Tag>
                <span className="muted">{row.count}</span>
              </li>
            ))}
          </ul>
        )}
        {isAdmin && (
          <Card>
            <Field label="Role" hint="owner is the workspace itself and cannot be minted.">
              <select id="member-role" value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="admin">admin</option>
                <option value="member">member</option>
                <option value="viewer">viewer</option>
              </select>
            </Field>
            <Button onClick={invite} disabled={busy}>
              Mint a member token
            </Button>
            {issued && (
              <p className="muted small">
                <code>{issued.token}</code> — role {issued.role}. Copy it now; it is
                not stored anywhere you can read it back.
              </p>
            )}
          </Card>
        )}
      </section>

      <section>
        <h3>Plans</h3>
        {plans.error && <Failed error={plans.error} onRetry={plans.reload} />}
        <div className="grid">
          {(plans.data?.plans ?? []).map((plan) => (
            <PlanCard
              key={plan.name}
              plan={plan}
              current={plan.name === current.subscription.plan_id}
              onBuy={buy}
              onGrant={grant}
              busy={busy}
              canAdmin={Boolean(isAdmin)}
            />
          ))}
        </div>
        {checkout && (
          <p className="muted small">
            Checkout opened:{" "}
            <a href={checkout} rel="noreferrer noopener" target="_blank">
              {checkout}
            </a>
            . The plan changes when the provider confirms payment, not when this
            link is opened.
          </p>
        )}
      </section>

      {!tenants.error && (
        <section>
          <h3>Tenants</h3>
          <p className="muted small">
            Every workspace on this server. Each has its own token and its own
            data; nothing here reads across the boundary except this listing.
          </p>
          <Card>
            <Field label="Name" hint="A new tenant starts on the default plan.">
              <input
                id="tenant-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="acme-research"
              />
            </Field>
            <Button onClick={createTenant} disabled={busy || !newName}>
              Create workspace
            </Button>
          </Card>
          <ul className="rows">
            {(tenants.data?.workspaces ?? []).map((tenant) => (
              <li key={tenant.id}>
                <code>{tenant.id}</code>
                <span className="muted">{tenant.name}</span>
                <Tag>{tenant.subscription.plan_id}</Tag>
                {tenant.org && <Tag tone="info">{tenant.org}</Tag>}
                {tenant.id === current.id && <Tag tone="success">this one</Tag>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {isAdmin && (
        <section>
          <h3>Audit</h3>
          <p className="muted small">
            Who changed what. Admin only, and read on demand rather than on every
            visit — a compliance log is not dashboard furniture.
          </p>
          <Button variant="secondary" onClick={loadAudit} disabled={busy}>
            {audit ? "Reload audit log" : "Load audit log"}
          </Button>
          {audit !== null &&
            (audit.length === 0 ? (
              <Empty>Nothing recorded yet.</Empty>
            ) : (
              <ul className="rows">
                {audit.map((entry, index) => (
                  <li key={`${entry.at}-${index}`}>
                    <code>{when(entry.at)}</code>
                    <Tag>{entry.actor_role}</Tag>
                    <span className="muted">{entry.action}</span>
                    {entry.detail && <span className="muted small">{entry.detail}</span>}
                  </li>
                ))}
              </ul>
            ))}
        </section>
      )}
    </div>
  );
}
