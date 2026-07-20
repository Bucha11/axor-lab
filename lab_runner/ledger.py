"""Per-value ledger with conservative-join provenance.

Implements `contracts/provenance-semantics.md`: values are built by a closed
constructor set; a model-emitted value's provenance is the join of every
untrusted value live in the model's context at the call (over-taint, never
under-taint). Values are stored as `trace/v1` ledger dicts so a trace is
assembled by direct reference, not translation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_contracts.canonical import content_hash

LABEL_UNTRUSTED = "untrusted_derived"
LABEL_PROMPT_GIVEN = "prompt_given"
LABEL_TRUSTED = "trusted"
LABEL_SENSITIVE = "sensitive"

_PREVIEW_MAX = 120
REDACTED_PREVIEW = "[redacted]"


def _preview_of(value: object) -> str:
    """A short, lossy UI string — NEVER the replay source."""
    text = value if isinstance(value, str) else repr(value)
    return text[:_PREVIEW_MAX]


def _value_fields(value: object, sensitive: bool) -> dict[str, object]:
    """Preview + replay-authoritative value fields, redacted when sensitive.

    A sensitive value (declared via tool-manifest `sensitive_fields`, review
    §7.4) never carries its raw content into the trace: the preview is masked
    and the exact `decision_value` is omitted, leaving only the
    `canonical_value_hash` to bind it. A value-dependent policy therefore
    cannot be replayed exactly on a redacted value — the honest tradeoff the
    trace schema already documents."""
    fields: dict[str, object] = {"canonical_value_hash": content_hash(value)}
    if sensitive:
        fields["preview"] = REDACTED_PREVIEW
    else:
        fields["preview"] = _preview_of(value)
        fields["decision_value"] = value
    return fields


@dataclass
class ValueLedger:
    """Mints and stores every governed value of one trial.

    Two representations are kept DELIBERATELY separate (review r7):
    - ``values`` is the SERIALIZED evidence (redacted for sensitive values, no
      raw decision_value) — this is what a trace carries;
    - ``_runtime`` holds the RAW typed value in memory for the kernel to gate on,
      and is NEVER serialized. Reading the raw value off the serialized dict
      broke real-kernel runs on a sensitive value (its decision_value is gone),
      so the kernel must read it via ``runtime_value``, not ``get(...)['...']``.
    """

    values: list[dict[str, object]] = field(default_factory=list)
    _counter: int = 0
    _runtime: dict[str, object] = field(default_factory=dict)

    def _next_id(self, hint: str) -> str:
        self._counter += 1
        return f"v_{hint}_{self._counter}"

    def get(self, value_id: str) -> dict[str, object]:
        for value in self.values:
            if value["value_id"] == value_id:
                return value
        raise KeyError(value_id)

    def runtime_value(self, value_id: str) -> object:
        """The RAW typed value for the kernel — available even for a sensitive
        value whose serialized form is redacted. In-memory only, never in a trace."""
        return self._runtime[value_id]

    def has_runtime_value(self, value_id: str) -> bool:
        return value_id in self._runtime

    def labels_of(self, value_id: str) -> tuple[str, ...]:
        return tuple(self.get(value_id)["labels"])  # type: ignore[arg-type]

    def untrusted_ids(self) -> tuple[str, ...]:
        """The untrusted values currently live in context (conservative join input)."""
        return tuple(
            str(v["value_id"]) for v in self.values if LABEL_UNTRUSTED in v["labels"]  # type: ignore[operator]
        )

    def mint_constant(self, value: object, origin_ref: str, label: str = LABEL_PROMPT_GIVEN) -> str:
        """`constant` constructor: literal / prompt-given (trusted side).

        ``value`` is the EXACT typed value (string/number/list/…); it is stored
        as the replay-authoritative `decision_value`, with a truncated
        `preview` alongside for the UI."""
        value_id = self._next_id("const")
        self.values.append(
            {
                "value_id": value_id,
                "preview": _preview_of(value),
                "decision_value": value,
                "canonical_value_hash": content_hash(value),
                "labels": [label],
                "sources": [{"kind": "constant", "origin_ref": origin_ref}],
            }
        )
        self._runtime[value_id] = value
        return value_id

    def mint_external_read(self, value: object, origin_ref: str, sensitive: bool = False) -> str:
        """`external_read` constructor: roots a taint (untrusted tool field).

        A ``sensitive`` field additionally arms the confidentiality floor and
        is redacted in the trace (review §7.4)."""
        value_id = self._next_id("ext")
        labels = [LABEL_UNTRUSTED, LABEL_SENSITIVE] if sensitive else [LABEL_UNTRUSTED]
        self.values.append(
            {
                "value_id": value_id,
                **_value_fields(value, sensitive),
                "labels": labels,
                "sources": [{"kind": "external_read", "origin_ref": origin_ref}],
            }
        )
        self._runtime[value_id] = value  # raw value stays in memory for the kernel
        return value_id

    def mint_model_extraction(self, value: object, context_value_ids: tuple[str, ...] | None = None) -> str:
        """`model_extraction` constructor (Lab addition).

        Conservative join: the produced value inherits `untrusted_derived`
        with ``derived_from`` = the untrusted values live in THIS model call's
        context and ``sources`` = the union of their sources. We do not claim
        the model copied any particular span — any output may depend on any
        untrusted input in context.

        ``context_value_ids`` scopes the join to the values the model actually
        saw at this call (review §4.2 — the ledger no longer joins over every
        untrusted value ever minted). If omitted, falls back to all untrusted
        values (correct for a single-model-call trial).
        """
        # the context the model actually saw (all values if unscoped)
        context = (
            context_value_ids
            if context_value_ids is not None
            else tuple(str(v["value_id"]) for v in self.values)
        )
        untrusted = tuple(vid for vid in context if LABEL_UNTRUSTED in self.labels_of(vid))
        # CONSERVATIVE JOIN OVER THE LABEL LATTICE: a model output inherits every
        # security label present on ANY context value it may depend on — not just
        # untrusted_derived but SENSITIVE too. Otherwise a model that copies a
        # redacted secret into a sink arg would re-expose it in the clear (the
        # derived value would carry its raw preview/decision_value). (review r6)
        sensitive = any(LABEL_SENSITIVE in self.labels_of(vid) for vid in context)
        sources: list[dict[str, object]] = []
        seen: set[str] = set()
        for uid in untrusted:
            for src in self.get(uid)["sources"]:  # type: ignore[union-attr]
                key = canonical_src_key(src)  # type: ignore[arg-type]
                if key not in seen:
                    seen.add(key)
                    sources.append(dict(src))  # type: ignore[arg-type]
        labels: list[str] = []
        if untrusted:
            labels.append(LABEL_UNTRUSTED)
        if sensitive:
            labels.append(LABEL_SENSITIVE)
        if not labels:
            labels = ["mint"]
        if not sources:
            sources = [{"kind": "mint"}]
        value_id = self._next_id("model")
        self.values.append(
            {
                "value_id": value_id,
                # a derived value that inherits `sensitive` is redacted exactly
                # like a sensitive source: masked preview, no raw decision_value
                **_value_fields(value, sensitive),
                "labels": labels,
                "sources": sources,
                "transformations": ["model_extraction"],
                "derived_from": list(untrusted),
            }
        )
        self._runtime[value_id] = value  # raw value stays in memory for the kernel
        return value_id


def canonical_src_key(src: dict[str, object]) -> str:
    return f"{src.get('kind')}|{src.get('origin_ref', '')}"
