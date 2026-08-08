"""The Builder's YAML mode — the third editor over ONE manifest.

Suite Platform RFC §13: Basic, Advanced and YAML all edit the same `suite/v1`
document. That rule is only real if the conversion is lossless, and
INTEGRATION_PLAN's risk table names the failure it prevents — Basic mode growing
fields the YAML mode cannot express, after which the two modes silently disagree
about what the experiment is.

YAML is not a superset of JSON in the direction that matters here. YAML 1.1
resolves unquoted `yes` / `no` / `on` / `off` to booleans, `~` to null, and
`2026-08-08` to a date object. A manifest with `"on"` as a STRING — an
`enforcement` value, a tag, a metric name — can therefore come back as `True`
after a naive round trip, and the manifest hash changes without anyone touching
the document.

So:

  - dumping quotes anything that would resolve to another type (PyYAML's dumper
    already does this, and `round_trips` proves it per document rather than
    trusting it);
  - loading strips the timestamp resolver, so `created: 2026-08-08` stays the
    STRING the user typed. That is the one coercion producing a value JSON
    cannot represent at all: a `datetime.date` is not serializable back into the
    manifest, so leaving the resolver in would turn an editing session into a
    crash several steps downstream. Keeping it a string is also simply what the
    author meant — `suite/v1` has no date type for it to become.

PyYAML is an OPTIONAL dependency (`pip install axor-lab[yaml]`). The platform's
core stays stdlib-only; a Builder editing mode is not a reason to make every
install carry a parser.
"""

from __future__ import annotations

from typing import Any

from lab_contracts import content_hash

from .errors import SuiteError


class YamlUnavailable(SuiteError):
    """PyYAML is not installed, so the YAML editing mode cannot run."""

    def __init__(self) -> None:
        super().__init__(
            "the YAML editing mode needs PyYAML, which is an optional "
            "dependency: pip install 'axor-lab[yaml]'. Basic and Advanced mode "
            "edit the same manifest as JSON and need nothing extra."
        )


def _yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised by the absent path
        raise YamlUnavailable() from exc
    return yaml


def _loader(yaml: Any) -> Any:
    """SafeLoader with the timestamp resolver removed.

    A `datetime.date` cannot be represented in the manifest at all, so leaving
    the resolver in converts a losslessness bug into a serialization crash three
    steps later. Bool and null resolution stay: they produce JSON-representable
    values, and our dumper quotes any string that would trip them.
    """

    class _NoTimestamps(yaml.SafeLoader):
        pass

    _NoTimestamps.yaml_implicit_resolvers = {
        key: [(tag, regexp) for tag, regexp in resolvers
              if tag != "tag:yaml.org,2002:timestamp"]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    return _NoTimestamps


def to_yaml(manifest: dict[str, Any]) -> str:
    """The manifest as YAML, key order preserved.

    `sort_keys=False` on purpose: the Builder's sections are ordered, and
    re-sorting a document a user is editing makes every diff unreadable even
    though the content is identical.
    """
    yaml = _yaml()
    return yaml.safe_dump(
        manifest, sort_keys=False, allow_unicode=True, default_flow_style=False,
    )


def from_yaml(text: str) -> dict[str, Any]:
    """Parse a YAML manifest back into the document the other modes edit."""
    yaml = _yaml()
    loaded = yaml.load(text, Loader=_loader(yaml))
    if not isinstance(loaded, dict):
        raise SuiteError(
            f"a suite manifest must be a mapping; this YAML parsed as "
            f"{type(loaded).__name__}"
        )
    _refuse_unrepresentable(loaded, path="")
    return loaded


def _refuse_unrepresentable(node: Any, path: str) -> None:
    """Fail loudly on any value the manifest cannot hold.

    A date, a set, a tuple key — YAML can express things `suite/v1` cannot, and
    a manifest carrying one serializes to something else on the way out. Naming
    the path is what makes it fixable; a schema error three layers up is not.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise SuiteError(
                    f"{path or 'the manifest'} has a non-string key {key!r} — "
                    "a suite manifest is a JSON document"
                )
            _refuse_unrepresentable(value, f"{path}.{key}" if path else key)
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            _refuse_unrepresentable(value, f"{path}[{index}]")
        return
    if node is not None and not isinstance(node, (str, int, float, bool)):
        # One check, not a list of known offenders. Dates are handled upstream
        # by the loader (they stay strings); anything else YAML can produce that
        # JSON cannot hold lands here with its path, which is the part that
        # makes it fixable.
        raise SuiteError(
            f"{path} parsed as {type(node).__name__}, which a suite manifest "
            "cannot hold — a manifest is a JSON document"
        )


def round_trips(manifest: dict[str, Any]) -> bool:
    """True when this manifest survives YAML and comes back byte-identical.

    Compared by canonical hash, not by `==`: `1` and `True` compare equal in
    Python, and a mode that turned a count into a boolean would pass an equality
    check while changing what the suite says.
    """
    return content_hash(from_yaml(to_yaml(manifest))) == content_hash(manifest)
