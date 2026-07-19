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

_PREVIEW_MAX = 120


def _preview_of(value: object) -> str:
    """A short, lossy UI string — NEVER the replay source."""
    text = value if isinstance(value, str) else repr(value)
    return text[:_PREVIEW_MAX]


@dataclass
class ValueLedger:
    """Mints and stores every governed value of one trial."""

    values: list[dict[str, object]] = field(default_factory=list)
    _counter: int = 0

    def _next_id(self, hint: str) -> str:
        self._counter += 1
        return f"v_{hint}_{self._counter}"

    def get(self, value_id: str) -> dict[str, object]:
        for value in self.values:
            if value["value_id"] == value_id:
                return value
        raise KeyError(value_id)

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
        return value_id

    def mint_external_read(self, value: object, origin_ref: str) -> str:
        """`external_read` constructor: roots a taint (untrusted tool field)."""
        value_id = self._next_id("ext")
        self.values.append(
            {
                "value_id": value_id,
                "preview": _preview_of(value),
                "decision_value": value,
                "canonical_value_hash": content_hash(value),
                "labels": [LABEL_UNTRUSTED],
                "sources": [{"kind": "external_read", "origin_ref": origin_ref}],
            }
        )
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
        if context_value_ids is None:
            untrusted = self.untrusted_ids()
        else:
            untrusted = tuple(
                vid for vid in context_value_ids if LABEL_UNTRUSTED in self.labels_of(vid)
            )
        sources: list[dict[str, object]] = []
        seen: set[str] = set()
        for uid in untrusted:
            for src in self.get(uid)["sources"]:  # type: ignore[union-attr]
                key = canonical_src_key(src)  # type: ignore[arg-type]
                if key not in seen:
                    seen.add(key)
                    sources.append(dict(src))  # type: ignore[arg-type]
        labels = [LABEL_UNTRUSTED] if untrusted else ["mint"]
        if not sources:
            sources = [{"kind": "mint"}]
        value_id = self._next_id("model")
        self.values.append(
            {
                "value_id": value_id,
                "preview": _preview_of(value),
                "decision_value": value,
                "canonical_value_hash": content_hash(value),
                "labels": labels,
                "sources": sources,
                "transformations": ["model_extraction"],
                "derived_from": list(untrusted),
            }
        )
        return value_id


def canonical_src_key(src: dict[str, object]) -> str:
    return f"{src.get('kind')}|{src.get('origin_ref', '')}"
