"""Offline verification of a downloaded reproduction package — no server trusted.

The point of a portable package is that its claims survive the server that
served it: content hashes, bit-identical replay, and — for a server-issued
package — that EVERY proof object is present and binds. Stripping a proof is a
failure, not a silent pass (review r16), and a package that is not a versioned
envelope cannot masquerade as an honest bare bundle (review r17).

Three properties stay separate here because they are separate: a package can be
INTACT without being AUTHENTIC, and a signature nobody holds a key for is
UNVERIFIED, not invalid and not a pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .checks import Check, CheckStatus
from .outcomes import Outcome

REPRODUCTION_PACKAGE_SCHEMA = "axor-reproduction-package/v1"


@dataclass(frozen=True)
class PackageVerifyResult:
    """What an offline verification of a reproduction package concluded."""

    outcome: Outcome
    checks: tuple[Check, ...] = field(default_factory=tuple)
    trace_count: int = 0
    bit_identical: bool = False


def package_data(path: Path) -> dict[str, object] | None:
    """The raw JSON of a downloaded `.json` package, or None for a bundle
    DIRECTORY (which ships no receipt/publication/acceptance)."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _done(checks: list[Check], outcome: Outcome, name: str, status: CheckStatus,
          message: str, **extra: object) -> PackageVerifyResult:
    checks.append(Check(name, status, message))
    return PackageVerifyResult(outcome, tuple(checks), **extra)  # type: ignore[arg-type]


def verify_package(
    path: Path,
    *,
    pubkey: str | None = None,
    author: str | None = None,
    server_pubkey: str | None = None,
    server: str | None = None,
    server_key_id: str | None = None,
    allow_bare: bool = False,
    allow_unsigned_server: bool = False,
) -> PackageVerifyResult:
    """Verify a reproduction package (or a local bundle directory) on disk.

    A thin loader over `verify_package_document`: a face that already HOLDS the
    package — an upload, a stored artifact — calls that directly instead of
    having to put bytes on a filesystem first.
    """
    from lab_runner.bundle_io import read_bundle_source

    bundle, traces = read_bundle_source(path)
    return verify_package_document(
        bundle, traces,
        None if path.is_dir() else package_data(path),
        from_directory=path.is_dir(),
        pubkey=pubkey, author=author, server_pubkey=server_pubkey, server=server,
        server_key_id=server_key_id, allow_bare=allow_bare,
        allow_unsigned_server=allow_unsigned_server,
    )


def verify_package_document(
    bundle: dict[str, object],
    traces: dict[str, dict[str, object]],
    data: dict[str, object] | None,
    *,
    from_directory: bool = False,
    pubkey: str | None = None,
    author: str | None = None,
    server_pubkey: str | None = None,
    server: str | None = None,
    server_key_id: str | None = None,
    allow_bare: bool = False,
    allow_unsigned_server: bool = False,
) -> PackageVerifyResult:
    """Verify an already-loaded package. `data` is the reproduction envelope, or
    None for a bundle DIRECTORY, which never carried server proofs."""
    from lab_capabilities.governance import default_registry, replay_bundle
    from lab_contracts.publication import verify_publication_binding
    from lab_contracts.signing import (
        SignatureInvalid,
        SignatureUnavailable,
        verify_acceptance,
        verify_receipt,
    )
    checks: list[Check] = []
    conditions: list[dict[str, object]] = bundle["conditions"]  # type: ignore[assignment]
    checks.append(Check("content hashes", CheckStatus.OK,
                        f"OK ({len(traces)} trace(s), {len(conditions)} conditions)"))
    versions = tuple(str(c["kernel"]) for c in conditions)
    kernels = {k.version: k for k in default_registry(versions).kernels}
    report = replay_bundle(bundle, traces, kernels)
    if not report.bit_identical:
        return _done(checks, Outcome.FAILURE, "replay", CheckStatus.INVALID,
                     "MISMATCH: recomputed verdicts differ from recorded",
                     trace_count=len(traces))
    checks.append(Check("replay", CheckStatus.OK,
                        f"bit-identical over {len(report.decisions)} trace(s)"))

    # A bundle DIRECTORY is a local artifact that never carried server proofs — it
    # verifies as bundle-integrity + replay only, and cannot be "downgraded".
    if from_directory:
        return _done(checks, Outcome.OK, "receipt", CheckStatus.OK,
                     "none (a bundle directory carries no portable receipt)",
                     trace_count=len(traces), bit_identical=True)

    # A downloaded `.json` MUST be a VERSIONED reproduction envelope. Detection can
    # no longer key on the PRESENCE of a publication/acceptance/receipt: an
    # attacker downgrades a server package to a bare {bundle,traces} by stripping
    # the envelope AND every proof at once, and autodetection then reads it as an
    # honest bare package and exits 0. The envelope schema_version is the one
    # marker whose ABSENCE is meaningful — a bare file without allow_bare is
    # refused, so a stripped server package cannot masquerade as bare (review r17).
    if not (data and str(data.get("schema_version")) == REPRODUCTION_PACKAGE_SCHEMA):
        if not allow_bare:
            return _done(
                checks, Outcome.VALIDATION, "package", CheckStatus.INVALID,
                f"NOT a versioned reproduction envelope (missing schema_version "
                f"{REPRODUCTION_PACKAGE_SCHEMA!r}). A server package cannot be silently "
                "downgraded to a bare bundle — pass --allow-bare to verify a local "
                "bundle+traces file as bare (integrity + replay only, no server proofs).",
                trace_count=len(traces), bit_identical=True)
        return _done(checks, Outcome.OK, "package", CheckStatus.OK,
                     "bare (bundle+traces only, --allow-bare) — no server proofs to check",
                     trace_count=len(traces), bit_identical=True)

    receipt = data.get("receipt")
    if not isinstance(receipt, dict):
        return _done(checks, Outcome.FAILURE, "receipt", CheckStatus.INVALID,
                     "MISSING from a server package (stripped?) — refusing to pass",
                     trace_count=len(traces), bit_identical=True)

    unverified = False  # a signature we could not check (distinct from invalid)
    # 1) author receipt
    try:
        verify_receipt(bundle, receipt, pubkey, expected_author=author)
    except SignatureInvalid as exc:
        return _done(checks, Outcome.FAILURE, "receipt", CheckStatus.INVALID, f"INVALID — {exc}",
                     trace_count=len(traces), bit_identical=True)
    except SignatureUnavailable as exc:
        checks.append(Check("receipt", CheckStatus.UNVERIFIED, f"UNVERIFIED — {exc}"))
        unverified = True
    else:
        kind = ("signature VERIFIED" if str(receipt.get("integrity")) == "signed"
                else "signed_ref OK (hash-only)")
        checks.append(Check("receipt", CheckStatus.OK, kind))

    publication = data.get("publication")
    acceptance = data.get("acceptance")
    if not isinstance(publication, dict) or not isinstance(acceptance, dict):
        return _done(checks, Outcome.FAILURE, "package", CheckStatus.INVALID,
                     "MISSING publication or acceptance (server package) — refusing to pass",
                     trace_count=len(traces), bit_identical=True)
    # 2) publication binds to the bundle and its own id
    problems = verify_publication_binding(publication, bundle)
    if problems:
        return _done(checks, Outcome.FAILURE, "publication", CheckStatus.INVALID,
                     "INVALID — " + "; ".join(problems[:5]),
                     trace_count=len(traces), bit_identical=True)
    checks.append(Check("publication", CheckStatus.OK, "bound (id + bundle_ref + claims)"))
    # 2b) the AUTHOR receipt's integrity must match the publication's — otherwise a
    # `signed` publication can be downgraded by swapping in a valid hash-only
    # receipt (the author signature stripped) while the server acceptance stays
    # signed, and verify would still exit 0. The portable author receipt exists
    # precisely to prove author authenticity WITHOUT trusting the server, so a
    # signed publication must carry a signed, VERIFIED author receipt (review r18).
    pub_integrity = str(publication.get("integrity", "hash_verified"))
    receipt_integrity = str(receipt.get("integrity", "hash_verified"))
    if receipt_integrity != pub_integrity:
        return _done(
            checks, Outcome.FAILURE, "receipt", CheckStatus.INVALID,
            f"INTEGRITY MISMATCH — receipt is {receipt_integrity!r} but the publication is "
            f"{pub_integrity!r} (author-signature downgrade?) — refusing to pass",
            trace_count=len(traces), bit_identical=True)
    if pub_integrity == "signed" and not pubkey:
        # a signed publication whose author signature we hold no key to check is
        # UNVERIFIED, never a pass (the server acceptance is a separate attestation)
        checks.append(Check(
            "receipt", CheckStatus.UNVERIFIED,
            "UNVERIFIED — signed publication but no author public key (--pubkey) was "
            "supplied to verify its receipt"))
        unverified = True
    # 3) server acceptance binds + (optionally) verifies. verify_acceptance also
    # requires acceptance.integrity == publication.integrity. v0.3 keeps a single
    # acceptance/v1 form: a server that finds its persisted acceptance damaged
    # simply re-mints a fresh acceptance/v1 from the current bundle on load, so
    # there is no reacceptance/history chain to resolve here.
    try:
        verify_acceptance(
            acceptance, publication,
            server_pubkey_hex=server_pubkey,
            expected_server=server,
            expected_key_id=server_key_id,
        )
    except SignatureInvalid as exc:
        return _done(checks, Outcome.FAILURE, "acceptance", CheckStatus.INVALID,
                     f"INVALID — {exc}", trace_count=len(traces), bit_identical=True)
    except SignatureUnavailable as exc:
        checks.append(Check("acceptance", CheckStatus.UNVERIFIED, f"UNVERIFIED — {exc}"))
        unverified = True
    else:
        if str(acceptance.get("algorithm")) == "ed25519":
            checks.append(Check("acceptance", CheckStatus.OK, "signature VERIFIED"))
        elif allow_unsigned_server:
            checks.append(Check("acceptance", CheckStatus.OK,
                                "unsigned (accepted via --allow-unsigned-server; local dev only)"))
        else:
            # an UNSIGNED acceptance proves only internal self-consistency, NOT that
            # a specific Axor Lab server ran the checks — anyone can mint one. It is
            # not an authenticated server verification, so it is UNVERIFIED (r17).
            checks.append(Check(
                "acceptance", CheckStatus.UNVERIFIED,
                "UNVERIFIED — unsigned server acceptance is not an authenticated "
                "verification (pass --server-pubkey to check a signed one, or "
                "--allow-unsigned-server for local development)"))
            unverified = True

    return PackageVerifyResult(
        outcome=Outcome.UNVERIFIED if unverified else Outcome.OK,
        checks=tuple(checks),
        trace_count=len(traces),
        bit_identical=True,
    )
