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

from lab_server.runtime_jobs import RuntimeJobStore
from lab_server.screens import ScreenStore

# The default entitlement plan (feature 3 fills this with real limits). A
# workspace with no explicit plan gets this — generous, so tenancy alone changes
# nothing about what a workspace may do until an entitlement is actually set.
DEFAULT_PLAN: dict[str, object] = {"name": "default"}


@dataclass
class Workspace:
    id: str
    name: str
    token: str
    plan: dict[str, object] = field(default_factory=lambda: dict(DEFAULT_PLAN))
    is_admin: bool = False  # may create/list other workspaces
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, object]:
        """What is safe to show a client — never the token."""
        return {"id": self.id, "name": self.name, "plan": self.plan,
                "is_admin": self.is_admin, "created_at": self.created_at}


class Workspaces:
    """The tenant registry: tokens, per-workspace stores, and the current-request
    workspace (thread-local, set by the handler after it authenticates)."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._lock = threading.Lock()
        self._by_token: dict[str, Workspace] = {}
        self._by_id: dict[str, Workspace] = {}
        self._stores: dict[str, tuple[RuntimeJobStore, ScreenStore]] = {}
        self._key_ws: dict[str, str] = {}   # runtime ingest_key -> workspace id
        self._current = threading.local()

    # -- registry ---------------------------------------------------------
    def add(self, workspace: Workspace) -> Workspace:
        with self._lock:
            self._by_token[workspace.token] = workspace
            self._by_id[workspace.id] = workspace
        return workspace

    def resolve_token(self, token: str | None) -> Workspace | None:
        if not token:
            return None
        with self._lock:
            return self._by_token.get(token)

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
        with self._lock:
            existing = self._stores.get(ws_id)
            if existing is not None:
                return existing
            persist = str(self._data_dir / ws_id) if self._data_dir is not None else None
            pair = (RuntimeJobStore(), ScreenStore(persist_dir=persist))
            self._stores[ws_id] = pair
            return pair

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
