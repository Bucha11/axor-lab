"""Multi-tenancy — a workspace is a tenant with its own token and isolated data.

RFC §16's commercial half is a HOSTED workspace: many customers on one server,
each seeing only their own suites, runs, evidence and artifacts. The single-token
demo could not express "who is this" — so it could not isolate one tenant's data
from another's, and had nothing to attach an entitlement to (feature 3).

A `Workspace` is identified by its control token. `Workspaces` owns the token →
workspace map and a per-workspace pair of stores (RuntimeJobStore + ScreenStore),
each rooted in its own durable subdirectory. The server handler resolves the
request's workspace from its bearer token (or a runtime's ingest key) and sets a
THREAD-LOCAL current workspace; the store routers below delegate to that
workspace's stores, so the ~40 existing `jobs.`/`shelf.` call sites route to the
right tenant without changing.

Back-compat: a server given a single control token runs one default workspace —
every existing single-token client and test is that one tenant, unchanged.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from typing import TYPE_CHECKING

from lab_server.screens import ScreenStore

if TYPE_CHECKING:
    from lab_server.runtime_jobs import RuntimeJobStore

# The default entitlement plan. `None` limits mean UNLIMITED and an empty
# capability list means every gated feature is off — but the default grants all
# capabilities, so tenancy alone changes nothing until a restricted plan is set.
# A restricted plan sets a numeric limit (e.g. max_suites: 2) or drops a
# capability.
ALL_CAPABILITIES = ("hosted_execution", "private_registry")

# RBAC roles, most-privileged first. A member's token carries a role within its
# workspace; SSO/OIDC is the identity source that MINTS these member tokens (the
# external IdP maps a login to a workspace + role) — modelled here as member
# provisioning, so the authorization substrate is real even without an IdP wired.
ROLES = ("owner", "admin", "member", "viewer")
_ROLE_RANK = {role: i for i, role in enumerate(ROLES)}  # lower rank = more power


def role_at_least(role: str, minimum: str) -> bool:
    """Whether `role` is at least as privileged as `minimum`."""
    return _ROLE_RANK.get(role, len(ROLES)) <= _ROLE_RANK.get(minimum, -1)

DEFAULT_PLAN: dict[str, object] = {
    "name": "default",
    "max_suites": None,           # None = unlimited
    "max_artifacts": None,        # artifact retention (feature 5)
    "max_hosted_runtimes": None,  # hosted execution pool size (feature 4)
    "capabilities": list(ALL_CAPABILITIES),
}


class EntitlementError(Exception):
    """A gated action refused by the workspace's plan. 402 Payment Required —
    the request is well-formed and authorised, the PLAN does not include it."""

    status = 402  # Payment Required

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def plan_limit(workspace: "Workspace", key: str) -> int | None:
    """The numeric ceiling for `key`, or None for unlimited/unset."""
    value = workspace.plan.get(key)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def has_capability(workspace: "Workspace", capability: str) -> bool:
    caps = workspace.plan.get("capabilities")
    return isinstance(caps, list) and capability in caps


def require_within(workspace: "Workspace", key: str, current_count: int) -> None:
    """Refuse when adding one more would exceed the plan's `key` limit."""
    ceiling = plan_limit(workspace, key)
    if ceiling is not None and current_count >= ceiling:
        raise EntitlementError(
            f"plan '{workspace.plan.get('name', '?')}' allows {ceiling} for {key}; "
            f"this workspace already has {current_count}. Upgrade the plan to add more."
        )


def require_capability(workspace: "Workspace", capability: str) -> None:
    if not has_capability(workspace, capability):
        raise EntitlementError(
            f"capability {capability!r} is not in plan '{workspace.plan.get('name', '?')}'"
        )


@dataclass
class Workspace:
    id: str
    name: str
    token: str
    plan: dict[str, object] = field(default_factory=lambda: dict(DEFAULT_PLAN))
    is_admin: bool = False  # may create/list other workspaces
    org: str | None = None  # workspaces in the same org share a private registry
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, object]:
        """What is safe to show a client — never the token."""
        return {"id": self.id, "name": self.name, "plan": self.plan,
                "is_admin": self.is_admin, "org": self.org,
                "created_at": self.created_at}


class Workspaces:
    """The tenant registry: tokens, per-workspace stores, and the current-request
    workspace (thread-local, set by the handler after it authenticates)."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._lock = threading.Lock()
        # a token maps to (workspace id, role) — the workspace's own token is its
        # owner; member tokens carry lesser roles
        self._by_token: dict[str, tuple[str, str]] = {}
        self._by_id: dict[str, Workspace] = {}
        self._stores: dict[str, tuple[RuntimeJobStore, ScreenStore]] = {}
        self._org_stores: dict[str, ScreenStore] = {}  # org id -> shared registry
        self._key_ws: dict[str, str] = {}   # runtime ingest_key -> workspace id
        self._members: dict[str, dict[str, str]] = {}  # ws id -> {token: role}
        self._audit: dict[str, list[dict[str, object]]] = {}  # ws id -> entries
        self._current = threading.local()

    # -- registry ---------------------------------------------------------
    def add(self, workspace: Workspace) -> Workspace:
        with self._lock:
            self._by_token[workspace.token] = (workspace.id, "owner")
            self._by_id[workspace.id] = workspace
            self._members.setdefault(workspace.id, {})
        return workspace

    def resolve_token(self, token: str | None) -> Workspace | None:
        entry = self._resolve(token)
        return entry[0] if entry else None

    def role_of(self, token: str | None) -> str | None:
        entry = self._resolve(token)
        return entry[1] if entry else None

    def _resolve(self, token: str | None) -> tuple[Workspace, str] | None:
        if not token:
            return None
        with self._lock:
            binding = self._by_token.get(token)
            if binding is None:
                return None
            ws_id, role = binding
            workspace = self._by_id.get(ws_id)
        return (workspace, role) if workspace is not None else None

    # -- members (RBAC) ---------------------------------------------------
    def add_member(self, ws_id: str, token: str, role: str) -> None:
        with self._lock:
            self._by_token[token] = (ws_id, role)
            self._members.setdefault(ws_id, {})[token] = role

    def remove_member(self, ws_id: str, token: str) -> bool:
        with self._lock:
            existed = self._members.get(ws_id, {}).pop(token, None) is not None
            if existed:
                self._by_token.pop(token, None)
            return existed

    def members_of(self, ws_id: str) -> list[dict[str, object]]:
        """Roles present, WITHOUT the tokens — a member list is not a key dump."""
        with self._lock:
            roles = list(self._members.get(ws_id, {}).values())
        counts: dict[str, int] = {}
        for role in ["owner", *roles]:  # the workspace's own token is the owner
            counts[role] = counts.get(role, 0) + 1
        return [{"role": r, "count": n} for r, n in counts.items()]

    # -- audit log (compliance) ------------------------------------------
    def audit(self, ws_id: str, actor_role: str, action: str, at: float,
              detail: str = "") -> None:
        with self._lock:
            self._audit.setdefault(ws_id, []).append(
                {"at": at, "actor_role": actor_role, "action": action, "detail": detail})

    def audit_log(self, ws_id: str) -> list[dict[str, object]]:
        with self._lock:
            return list(self._audit.get(ws_id, []))

    def get(self, ws_id: str) -> Workspace | None:
        with self._lock:
            return self._by_id.get(ws_id)

    def list(self) -> list[Workspace]:
        with self._lock:
            return list(self._by_id.values())

    # -- per-workspace stores --------------------------------------------
    def stores(self, ws_id: str) -> tuple[RuntimeJobStore, ScreenStore]:
        """The (jobs, shelf) for a workspace, created on first use. A workspace's
        ScreenStore is rooted in its OWN subdirectory, so one tenant's suites
        never land in another's."""
        from lab_server.runtime_jobs import RuntimeJobStore  # lazy: avoids an import cycle

        with self._lock:
            existing = self._stores.get(ws_id)
            if existing is not None:
                return existing
            persist = str(self._data_dir / ws_id) if self._data_dir is not None else None
            pair = (RuntimeJobStore(), ScreenStore(persist_dir=persist))
            self._stores[ws_id] = pair
            return pair

    def org_registry(self, org_id: str) -> ScreenStore:
        """The shared suite registry for an ORG — private to it, visible to every
        workspace whose `org` matches (RFC §16 private registries). Rooted in its
        own durable subdir, so one org's registry never leaks to another's."""
        with self._lock:
            existing = self._org_stores.get(org_id)
            if existing is not None:
                return existing
            persist = str(self._data_dir / "org" / org_id) if self._data_dir else None
            shelf = ScreenStore(persist_dir=persist)
            self._org_stores[org_id] = shelf
            return shelf

    # -- runtime ingest-key routing --------------------------------------
    def bind_key(self, ingest_key: str, ws_id: str) -> None:
        """A runtime's ingest key belongs to the workspace that connected it, so
        a later runtime call (which carries only the key) resolves to it."""
        with self._lock:
            self._key_ws[ingest_key] = ws_id

    def workspace_for_key(self, ingest_key: str | None) -> str | None:
        if not ingest_key:
            return None
        with self._lock:
            return self._key_ws.get(ingest_key)

    # -- current-request workspace (thread-local) ------------------------
    def set_current(self, ws_id: str) -> None:
        self._current.ws_id = ws_id

    def current_id(self) -> str | None:
        return getattr(self._current, "ws_id", None)

    def current_stores(self) -> tuple[RuntimeJobStore, ScreenStore]:
        ws_id = self.current_id()
        if ws_id is None:
            raise RuntimeError("no current workspace — resolve one before using its stores")
        return self.stores(ws_id)


class _JobsRouter:
    """Stands in for the single `jobs` the handler closed over; routes every call
    to the current request's workspace RuntimeJobStore."""

    def __init__(self, workspaces: Workspaces) -> None:
        self._w = workspaces

    def __getattr__(self, name: str) -> object:
        jobs, _ = self._w.current_stores()
        return getattr(jobs, name)


class _ShelfRouter:
    """Same, for the ScreenStore. `__getattr__` fires only for names not found
    normally, so both routers stay transparent to every existing call site."""

    def __init__(self, workspaces: Workspaces) -> None:
        self._w = workspaces

    def __getattr__(self, name: str) -> object:
        _, shelf = self._w.current_stores()
        return getattr(shelf, name)


def single_workspace(token: str | None, data_dir: str | Path | None = None) -> Workspaces:
    """A one-tenant registry from a lone control token — the back-compat path.

    When no token is set the workspace is OPEN (the existing unauthenticated
    local-dev mode); its fixed token is a sentinel the handler treats as the
    always-current default rather than a credential to check."""
    workspaces = Workspaces(data_dir=data_dir)
    # the lone workspace is admin, so a single-token operator can provision more
    workspaces.add(Workspace(id="default", name="Default workspace",
                             token=token or "\x00open\x00", is_admin=True))
    return workspaces


def new_id(prefix: str, existing: set[str]) -> str:
    """A short unique workspace id — deterministic given the set, so no Date/random."""
    n = 1
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"
