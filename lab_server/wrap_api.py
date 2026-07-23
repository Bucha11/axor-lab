"""Wrap API — the "upload agent code" ingest surface, backed by axor-wrap.

The Lab UI's "Upload code" mode posts agent source files here; the axor-wrap
engine statically scans them (nothing is ever imported or executed), guesses
each detected tool's effect class, and — after a HUMAN has classified every
UNKNOWN in the config builder — compiles reviewed tool-manifest/v1 files plus a
GovernanceConfig-loadable YAML.

Two endpoints, served inside the runtime-jobs server (same /jobs-api proxy,
same control-token gate — see ``make_runtime_server`` in runtime_jobs.py):

  POST /wrap/scan       {files: [{path, content}]} -> {tools: [... + guess]}
  POST /wrap/manifests  {tools: [{...detected fields, effect}]}
                        -> {manifests, governance_yaml, wrap}

axor-wrap is an OPTIONAL dependency (`pip install axor-lab[wrap]`): the import
is lazy and both endpoints answer 501 with an install hint when it is missing.

Safety of the scan input: only ``*.py`` files, paths are normalized and must
stay inside the temporary scan root (absolute paths and ``..`` segments are
rejected), and the total source size is capped at 2 MiB.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from .runtime_jobs import RuntimeJobsError

_MAX_TOTAL_SOURCE = 2 * 1024 * 1024  # 2 MiB of .py content per scan request
EFFECT_CLASSES = frozenset({"READ", "WRITE", "EXPORT", "EXEC"})
_HUMAN_REVIEW_REASON = "human-reviewed classification (Lab wrap config builder)"

_NOT_INSTALLED: tuple[int, dict[str, object]] = (
    501, {"error": "axor-wrap is not installed", "hint": "pip install axor-wrap"},
)


def _engine():  # -> module | None
    """The axor-wrap engine, imported lazily — None when not installed."""
    try:
        import axor_wrap
    except ImportError:
        return None
    return axor_wrap


# ── POST /wrap/scan ──────────────────────────────────────────────────────────────


def _safe_relpath(raw: object) -> str:
    """Normalize a client-supplied file path; reject anything that could
    escape the scan root (absolute, ``..``, drive letters) or is not ``*.py``."""
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeJobsError(400, "each file needs a non-empty string 'path'")
    candidate = raw.strip().replace("\\", "/")
    if candidate.startswith("/") or ":" in candidate:
        raise RuntimeJobsError(400, f"absolute paths are not allowed: {raw!r}")
    path = PurePosixPath(candidate)
    parts = [p for p in path.parts if p != "."]
    if ".." in parts:
        raise RuntimeJobsError(400, f"path may not contain '..': {raw!r}")
    if not parts:
        raise RuntimeJobsError(400, f"empty path after normalization: {raw!r}")
    if path.suffix != ".py":
        raise RuntimeJobsError(400, f"only .py files can be scanned: {raw!r}")
    return "/".join(parts)


def handle_scan(body: dict[str, object]) -> tuple[int, dict[str, object]]:
    """Scan uploaded ``.py`` sources with axor-wrap; return detected tools with
    their effect guesses. Raises RuntimeJobsError for bad input."""
    wrap = _engine()
    if wrap is None:
        return _NOT_INSTALLED
    files = body.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeJobsError(400, "scan requires {files: [{path, content}, ...]}")
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    total = 0
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeJobsError(400, "each file must be an object {path, content}")
        rel = _safe_relpath(item.get("path"))
        content = item.get("content")
        if not isinstance(content, str):
            raise RuntimeJobsError(400, f"file {rel!r} needs a string 'content'")
        if rel in seen:
            raise RuntimeJobsError(400, f"duplicate file path {rel!r}")
        seen.add(rel)
        total += len(content.encode("utf-8"))
        if total > _MAX_TOTAL_SOURCE:
            raise RuntimeJobsError(413, "scan payload too large (max 2 MiB of source)")
        entries.append((rel, content))
    with TemporaryDirectory(prefix="axor_wrap_scan_") as tmp:
        root = Path(tmp).resolve()
        for rel, content in entries:
            dest = (root / rel).resolve()
            if not dest.is_relative_to(root):  # belt and braces after _safe_relpath
                raise RuntimeJobsError(400, f"path escapes the scan root: {rel!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        tools = []
        for tool in wrap.scan_project(root):
            guess = wrap.infer_effect(tool)
            tools.append({
                "id": tool.id,
                "source": tool.source,
                "description": tool.description,
                "args_schema": dict(tool.args_schema),
                "framework": tool.framework,
                "schema_confidence": tool.schema_confidence,
                "guess": {
                    "default_class": guess.default_class,
                    "confidence": guess.confidence,
                    "reason": guess.reason,
                    "driving_args": list(guess.driving_args),
                    "untrusted_fields": list(guess.untrusted_fields),
                },
            })
    return 200, {"tools": tools}


# ── POST /wrap/manifests ─────────────────────────────────────────────────────────


def _str_list(value: object, tool_id: str, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RuntimeJobsError(400, f"tool {tool_id!r}: effect.{key} must be a list of strings")
    return list(value)


def handle_manifests(body: dict[str, object]) -> tuple[int, dict[str, object]]:
    """Build reviewed tool-manifest/v1 files from human-classified tools and
    compile the governance YAML. UNKNOWN is rejected — classification is a
    human decision that must already have happened in the config builder."""
    wrap = _engine()
    if wrap is None:
        return _NOT_INSTALLED
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        raise RuntimeJobsError(400, "manifests require {tools: [{..., effect}, ...]}")
    manifests: list[dict[str, object]] = []
    for entry in tools:
        if not isinstance(entry, dict):
            raise RuntimeJobsError(400, "each tool must be an object")
        tool_id = str(entry.get("id") or "")
        if not tool_id:
            raise RuntimeJobsError(400, "each tool needs a non-empty 'id'")
        effect = entry.get("effect")
        if not isinstance(effect, dict):
            raise RuntimeJobsError(400, f"tool {tool_id!r} needs an 'effect' object")
        default_class = effect.get("default_class")
        if default_class not in EFFECT_CLASSES:
            raise RuntimeJobsError(
                400,
                f"tool {tool_id!r}: effect.default_class must be one of "
                f"READ/WRITE/EXPORT/EXEC (got {default_class!r}) — UNKNOWN tools "
                "must be classified by a human before manifests are built",
            )
        args_schema = entry.get("args_schema")
        if args_schema is not None and not isinstance(args_schema, dict):
            raise RuntimeJobsError(400, f"tool {tool_id!r}: args_schema must be an object")
        detected = wrap.DetectedTool(
            id=tool_id,
            source=str(entry.get("source") or ""),
            description=str(entry.get("description") or ""),
            args_schema=dict(args_schema or {}),
            framework=str(entry.get("framework") or ""),
            schema_confidence=str(entry.get("schema_confidence") or "low"),
        )
        reviewed = wrap.EffectGuess(
            default_class=str(default_class),
            confidence="high",  # a human decided; the raw guess stays client-side
            reason=_HUMAN_REVIEW_REASON,
            driving_args=tuple(_str_list(effect.get("driving_args"), tool_id, "driving_args")),
            untrusted_fields=tuple(
                _str_list(effect.get("untrusted_fields"), tool_id, "untrusted_fields")),
        )
        manifest = wrap.build_manifest(detected, reviewed)
        sensitive = _str_list(effect.get("sensitive_fields"), tool_id, "sensitive_fields")
        if sensitive:
            manifest["sensitive_fields"] = sensitive
        errors = wrap.validate_manifest(manifest)
        if errors:
            raise RuntimeJobsError(
                400, f"manifest for {tool_id!r} is invalid: " + "; ".join(errors))
        manifests.append(manifest)
    compiled = wrap.compile_manifests(manifests)
    return 200, {
        "manifests": manifests,
        "governance_yaml": wrap.governance_yaml(manifests),
        "wrap": {
            "generated_by": f"axor-wrap {wrap.__version__}",
            "manifest_schema": "tool-manifest/v1",
            "tools": len(manifests),
            "egress_sinks": compiled["egress_sinks"],
            "untrusted_sources": compiled["untrusted_sources"],
            "sensitive_sources": compiled["sensitive_sources"],
            "driving_args": compiled["driving_args"],
        },
    }
