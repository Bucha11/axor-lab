"""Build the Control Plane export tree and have the vault sign its manifest.

The last capability that lived only in the CLI, and the only one where "just move
it to the server" was the wrong answer twice over. A Lab server cannot hold your
signing key, and a browser should not either: a private key that vouches for a
production config does not belong in a web form, however convenient.

The Control Plane already solved this, for exactly this class of action. Its
signing vault SIGNS and never surrenders — `sign(operator, key_id, payload)`
returns a signature over bytes you submit, the private half never leaves, every
request is authorised against the key's operator list and audited unconditionally.
An export signature says "this named author vouches for this production config",
which is an operator action, and operator actions in CP are vault-signed and
audited already. So the signature comes from there.

What Lab does here is assemble the tree and hand over the manifest's canonical
bytes. It never sees a key.

The degradation is loud on purpose. No vault configured, unreachable, or refusing
→ the tree comes back UNSIGNED and labelled unsigned. An export that quietly
claims an authority it does not have is the failure this whole subsystem exists
to prevent, so a missing signature is always visible, never smoothed over.
"""
from __future__ import annotations

import base64
import json
import shutil
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Any

# The export tree carries the source bundle and every frozen trace, so it is
# large by design. This bounds what one request will hold in memory to return.
MAX_EXPORT_BYTES = 64 * 1024 * 1024


class CpSignRefused(ValueError):
    """The export could not be built. Carries an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _sign_via_vault(
    manifest: dict[str, Any], *, cp_url: str, operator: str, key_id: str, token: str | None,
) -> str:
    """Ask the Control Plane vault for a signature over the manifest's bytes.

    Signs the manifest MINUS its `signature` field, canonicalized — byte-identical
    to what `lab_contracts.signing.sign_bundle` produces locally, so an export
    signed here verifies under exactly the same check as one signed by the CLI.
    """
    import urllib.error
    import urllib.request

    from lab_contracts import canonical_json

    body = {k: v for k, v in manifest.items() if k != "signature"}
    payload = canonical_json(body).encode("utf-8")

    request = urllib.request.Request(
        cp_url.rstrip("/") + "/v1/vault/signing/sign",
        data=json.dumps({
            "operator": operator,
            "key_id": key_id,
            "payload_b64": base64.b64encode(payload).decode(),
        }).encode(),
        headers={
            "Content-Type": "application/json",
            **({"X-Vault-Signing-Token": token} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        raise CpSignRefused(
            exc.code if exc.code in (401, 403, 404) else 502,
            f"the Control Plane vault refused to sign ({exc.code}): {detail}",
        ) from exc
    except OSError as exc:
        raise CpSignRefused(502, f"the Control Plane vault is unreachable: {exc}") from exc

    signature = str(answer.get("signature_hex") or "")
    if not signature:
        raise CpSignRefused(502, "the vault returned no signature")
    return signature


def build_export(
    bundle: dict[str, Any],
    traces: dict[str, Any],
    regressions: list[dict[str, Any]] | None = None,
    condition_id: str | None = None,
    signing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce the whole export tree, signed when a vault is configured.

    Returns `{files, manifest, signed, signature_note, earned_bridge}` where
    `files` maps each relative path to its text content — the same tree
    `axor-lab export-cp` writes, assembled by the same code so the two cannot
    drift apart.
    """
    from lab_runner.bundle_io import write_bundle_dir
    from lab_runner.cli import _cmd_export_cp
    from lab_runner.errors import RunnerError

    work = Path(tempfile.mkdtemp(prefix="axor-cp-export-"))
    try:
        source = work / "source"
        write_bundle_dir(source, bundle, traces, overwrite=True)
        out = work / "export"

        # the CLI's own command, so the tree cannot diverge from what
        # `verify-cp-export` expects. Signing is left off here and applied below
        # from the vault instead of a local key file.
        args = Namespace(
            bundle=str(source), out=str(out), pins=None, condition=condition_id,
            overwrite=True, author=None, sign_key=None,
        )
        if regressions:
            pins_path = work / "pins.json"
            pins_path.write_text(json.dumps(list(regressions)))
            args.pins = str(pins_path)

        try:
            _cmd_export_cp(args)
        except RunnerError as exc:
            raise CpSignRefused(422, str(exc)) from exc

        manifest_path = out / "manifest.json"
        manifest: dict[str, Any] = json.loads(manifest_path.read_text())

        signed = False
        note = (
            "UNSIGNED — integrity and derivability only. verify-cp-export can "
            "recompute this export from its own bytes, but nothing here says WHO "
            "vouches for it."
        )
        if signing:
            # author goes in BEFORE the bytes are signed, exactly as sign_bundle
            # does locally: the signature covers the manifest including its author.
            manifest["author"] = str(signing.get("operator") or "")
            manifest["signature"] = _sign_via_vault(
                manifest,
                cp_url=str(signing.get("cp_url") or ""),
                operator=str(signing.get("operator") or ""),
                key_id=str(signing.get("key_id") or ""),
                token=signing.get("token"),
            )
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
            signed = True
            note = (
                f"signed by {manifest['author']} via Control Plane vault custody "
                f"(key {signing.get('key_id')!r}) — the private key never left the "
                "vault, and the request is in its audit log."
            )

        files: dict[str, str] = {}
        total = 0
        for path in sorted(out.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            data = path.read_bytes()
            total += len(data)
            if total > MAX_EXPORT_BYTES:
                raise CpSignRefused(
                    413,
                    f"the export exceeds {MAX_EXPORT_BYTES // (1024 * 1024)} MiB — "
                    "run `axor-lab export-cp` locally for a tree this large",
                )
            files[str(path.relative_to(out)).replace("\\", "/")] = data.decode("utf-8")

        return {
            "files": files,
            "manifest": manifest,
            "signed": signed,
            "signature_note": note,
            "file_count": len(files),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
