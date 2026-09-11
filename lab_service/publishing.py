"""Publishing a bundle — locally, or through a server's publish handshake.

The two paths mint DIFFERENT claims, and the difference is the point. A local
publish proves REPLAY: it re-ran the verdicts, so it may assert
`exactly_replayable`. It does not independently recompute the aggregates, so it
must NOT mint `statistically_reproducible` over self-reported numbers — a
hand-edited bundle could carry a fabricated aggregate, and the schema forbids
self-reported backing for that claim. Statistical claims are minted only by a
server that recomputes them from the traces (review r12).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .outcomes import Outcome

if TYPE_CHECKING:
    from pathlib import Path

PUBLICATIONS_PATH = "/api/publications"


@dataclass(frozen=True)
class LocalPublishResult:
    """A local, content-addressed publication asserting replay only."""

    outcome: Outcome
    publication: dict[str, object]
    aggregate_count: int = 0

    @property
    def publication_id(self) -> str:
        return str(self.publication["publication_id"])


@dataclass(frozen=True)
class UploadResult:
    """The server's answer to a publish handshake."""

    outcome: Outcome
    publication_id: str = ""
    url: str = ""
    acceptance: dict[str, object] | None = None
    error: str = ""
    status: int | None = None

    @property
    def acceptance_is_signed(self) -> bool:
        return bool(self.acceptance and self.acceptance.get("algorithm") == "ed25519")


def check_publishable(
    bundle: dict[str, object], traces: dict[str, dict[str, object]]
) -> bool:
    """True when every recorded verdict recomputes — the gate both paths pass."""
    from lab_capabilities.governance import default_registry, replay_bundle

    conditions: list[dict[str, object]] = bundle["conditions"]  # type: ignore[assignment]
    versions = tuple(str(c["kernel"]) for c in conditions)
    kernels = {k.version: k for k in default_registry(versions).kernels}
    return bool(replay_bundle(bundle, traces, kernels).bit_identical)


def build_local_publication(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    *,
    question: str,
    license_id: str | None = None,
    visibility: str = "unlisted",
) -> LocalPublishResult:
    """Mint a local `publication/v1` asserting replay, never statistics."""
    from lab_capabilities.governance.claims import deny_claim_text
    from lab_contracts import (
        build_publication,
        content_hash,
        finalize_publication_id,
        make_claim,
        validate_artifact,
    )
    from lab_runner.errors import RunnerError

    from .evidence import first_denied_trace

    trace_refs = frozenset(content_hash(t) for t in traces.values())
    aggregates: list[dict[str, object]] = bundle["aggregates"]  # type: ignore[assignment]
    aggregate_refs = frozenset(
        f"agg:{a['metric']}:{a['condition_id']}" for a in aggregates
    )
    claims: list[dict[str, object]] = []
    denied = first_denied_trace(traces)
    if denied is not None:
        claims.append(
            make_claim(
                "exactly_replayable",
                # same decision-derived text the server uses — not a template
                deny_claim_text(denied),
                content_hash(denied),
                trace_refs=trace_refs,
                aggregate_refs=aggregate_refs,
            )
        )
    publication = build_publication(
        publication_id="e_pending",  # placeholder; content-addressed below
        bundle_ref=content_hash(bundle),
        question=question,
        origin="local",
        integrity="hash_verified",
        claims=claims,
        license_id=license_id,
        visibility=visibility,
        statistics_integrity=None,  # no statistical claims are asserted locally
    )
    # content-address the WHOLE body (the shared definition), so the same bundle
    # published with a different question/visibility/license is a genuinely
    # different publication with its own id, not an id-colliding overwrite (r12)
    finalize_publication_id(publication)
    errors = validate_artifact(publication, "publication")
    if errors:
        raise RunnerError(f"publication failed schema validation: {errors}")
    return LocalPublishResult(Outcome.OK, publication, len(aggregates))


def upload_publication(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    *,
    server: str,
    question: str,
    license_id: str | None = None,
    visibility: str = "unlisted",
    author: str | None = None,
    signature: str | None = None,
    token: str | None = None,
) -> UploadResult:
    """Upload through the publish handshake; the server re-verifies before minting.

    A rejection is an OUTCOME, not an exception: the server's status and body are
    the answer to the request. An unreachable server is different — that is a
    `RunnerError`, because nothing was asked and nothing answered.
    """
    import urllib.error
    import urllib.request

    from lab_runner.errors import RunnerError

    body: dict[str, object] = {
        "bundle": bundle,
        "traces": traces,
        "question": question,
        "license": license_id,
        "visibility": visibility,
    }
    # a signed, attributed upload: author + detached signature travel in the body
    if author:
        body["author"] = author
    if signature:
        body["signature"] = signature
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        server.rstrip("/") + PUBLICATIONS_PATH,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 (operator-supplied URL)
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return UploadResult(
            outcome=Outcome.FAILURE,
            error=exc.read().decode("utf-8", "replace"),
            status=exc.code,
        )
    except urllib.error.URLError as exc:
        raise RunnerError(f"cannot reach server {server}: {exc.reason}") from exc
    return UploadResult(
        outcome=Outcome.OK,
        publication_id=str(result["publication_id"]),
        url=str(result["url"]),
        acceptance=result.get("acceptance"),
    )


def default_acceptance_path(publication_id: str) -> Path:
    """Where a receipt lands when the caller names no destination."""
    from pathlib import Path

    return Path(f"{publication_id}.acceptance.json")
