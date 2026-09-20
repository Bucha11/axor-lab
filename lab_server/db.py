"""Postgres storage for the hosted server — optional, and only for the server.

Three rules this module exists to keep.

**The CLI never needs it.** `axor-lab run/replay/evidence/pin/regress/verify`
are offline, single-shot and local-first: Community is local-only and the
contract validator, replay and the statistics engine pull in nothing. So
`psycopg` is an OPTIONAL dependency, imported lazily, and nothing outside
`lab_server` may import this module. A Lab that could not produce a bundle on a
laptop without a database would be a different product.

**jsonb is safe here because hashing canonicalizes the OBJECT.**
`content_hash` hashes `canonical_json(obj)` — RFC 8785 over the parsed
document — not the bytes a store happened to keep. So jsonb reordering keys,
dropping whitespace and normalizing numbers changes nothing a hash sees, and a
document survives a round-trip with its content hash intact. That is asserted,
not assumed: see `tests/test_postgres_store.py`.

**A document jsonb cannot hold is REFUSED, never repaired.** Postgres rejects
`\\u0000` inside a jsonb string; canonical JSON accepts it. Silently stripping
it would change the document, change its hash, and break the offline
verification this product is for — so an unstorable document raises before it
reaches the driver, naming the field. Normalizing content on the way into
storage is how a receipt stops matching the thing it is a receipt for.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psycopg_pool import ConnectionPool

ENV_DSN = "AXOR_LAB_DATABASE_URL"

# One schema, created idempotently at first use. No migration framework yet: the
# table set is small and additive, and inventing a migration story before the
# second migration exists is how you get a framework nobody has exercised.
SCHEMA = """
create table if not exists lab_documents (
    workspace_id text        not null,
    kind         text        not null,
    doc_id       text        not null,
    doc          jsonb       not null,
    updated_at   timestamptz not null default now(),
    primary key (workspace_id, kind, doc_id)
);
-- the point of jsonb over a file: asking questions INSIDE the document.
-- jsonb_path_ops is the containment-only operator class — smaller and faster
-- than the default for the `doc @> '{...}'` queries this serves.
create index if not exists lab_documents_doc_gin
    on lab_documents using gin (doc jsonb_path_ops);
create index if not exists lab_documents_kind
    on lab_documents (workspace_id, kind, updated_at desc);

-- A run and everything it produced: its assignment, per-trial events, metrics
-- and the finished TRACES. Traces were the one thing the job store never
-- persisted, so the Trial screen for any past run went blank after a restart
-- while its artifact — which references those traces — survived.
create table if not exists lab_runs (
    workspace_id text        not null,
    job_id       text        not null,
    job          jsonb       not null,
    updated_at   timestamptz not null default now(),
    primary key (workspace_id, job_id)
);
create index if not exists lab_runs_recent
    on lab_runs (workspace_id, updated_at desc);
create index if not exists lab_runs_gin
    on lab_runs using gin (job jsonb_path_ops);
"""


class StorageError(RuntimeError):
    """A document the store cannot keep, or a backend that cannot be reached."""


class UnstorableDocument(StorageError):
    """A document Postgres cannot represent. Refused rather than rewritten."""


def dsn_from_env() -> str | None:
    """The configured DSN, or None for the file/in-memory backends."""
    value = os.environ.get(ENV_DSN, "").strip()
    return value or None


def reject_unstorable(document: Any, _path: str = "") -> None:
    """Raise if `document` holds a NUL, which jsonb cannot store.

    Checked here rather than left to the driver so the failure names the field
    instead of surfacing as `UntranslatableCharacter` from somewhere in libpq,
    and so it happens before a partially-written transaction.
    """
    if isinstance(document, str):
        if "\x00" in document:
            raise UnstorableDocument(
                f"{_path or 'document'} contains a NUL (U+0000), which Postgres "
                "cannot store in jsonb. It is not stripped: that would change "
                "the document and therefore its content hash. Remove it at the "
                "producer, where the value still means something."
            )
    elif isinstance(document, dict):
        for key, value in document.items():
            reject_unstorable(key, f"{_path}.{key}" if _path else str(key))
            reject_unstorable(value, f"{_path}.{key}" if _path else str(key))
    elif isinstance(document, (list, tuple)):
        for index, value in enumerate(document):
            reject_unstorable(value, f"{_path}[{index}]")


_pools: dict[str, "ConnectionPool"] = {}
_pools_lock = threading.Lock()


def pool(dsn: str) -> "ConnectionPool":
    """The process-wide pool for `dsn`, created once.

    A pool rather than connect-per-request because the server is threaded: one
    shared connection would serialize every screen behind a mutex, and a new
    connection per request pays a TLS handshake to read one row.
    """
    with _pools_lock:
        existing = _pools.get(dsn)
        if existing is not None:
            return existing
        try:
            from psycopg_pool import ConnectionPool
        except ModuleNotFoundError as exc:  # pragma: no cover - deployment error
            raise StorageError(
                f"{ENV_DSN} is set but psycopg is not installed. "
                "Install the server extra: pip install 'axor-lab[postgres]'"
            ) from exc
        created = ConnectionPool(dsn, min_size=1, max_size=8, open=True,
                                 kwargs={"autocommit": True})
        created.wait(timeout=10)
        with created.connection() as conn:
            conn.execute(SCHEMA)
        _pools[dsn] = created
        return created


def close_pools() -> None:
    """Close every pool — for tests and a clean shutdown."""
    with _pools_lock:
        for existing in _pools.values():
            existing.close()
        _pools.clear()
