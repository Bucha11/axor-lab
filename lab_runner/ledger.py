"""Per-value ledger with conservative-join provenance.

Implements `contracts/provenance-semantics.md`: values are built by a closed
constructor set; a model-emitted value's provenance is the join of every
untrusted value live in the model's context at the call (over-taint, never
under-taint). Values are stored as `trace/v1` ledger dicts so a trace is
assembled by direct reference, not translation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LABEL_UNTRUSTED = "untrusted_derived"
LABEL_PROMPT_GIVEN = "prompt_given"
LABEL_TRUSTED = "trusted"

_PREVIEW_MAX = 120


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

    def mint_constant(self, preview: str, origin_ref: str, label: str = LABEL_PROMPT_GIVEN) -> str:
        """`constant` constructor: literal / prompt-given (trusted side)."""
        value_id = self._next_id("const")
        self.values.append(
            {
                "value_id": value_id,
                "preview": preview[:_PREVIEW_MAX],
                "labels": [label],
                "sources": [{"kind": "constant", "origin_ref": origin_ref}],
            }
        )
        return value_id

    def mint_external_read(self, preview: str, origin_ref: str) -> str:
        """`external_read` constructor: roots a taint (untrusted tool field)."""
        value_id = self._next_id("ext")
        self.values.append(
            {
                "value_id": value_id,
                "preview": preview[:_PREVIEW_MAX],
                "labels": [LABEL_UNTRUSTED],
                "sources": [{"kind": "external_read", "origin_ref": origin_ref}],
            }
        )
        return value_id

    def mint_model_extraction(self, preview: str) -> str:
        """`model_extraction` constructor (Lab addition).

        Conservative join: the produced value inherits `untrusted_derived`
        with ``derived_from`` = ALL untrusted values live in context and
        ``sources`` = the union of their sources. We do not claim the model
        copied any particular span — any output may depend on any untrusted
        input in context.
        """
        untrusted = self.untrusted_ids()
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
                "preview": preview[:_PREVIEW_MAX],
                "labels": labels,
                "sources": sources,
                "transformations": ["model_extraction"],
                "derived_from": list(untrusted),
            }
        )
        return value_id


def canonical_src_key(src: dict[str, object]) -> str:
    return f"{src.get('kind')}|{src.get('origin_ref', '')}"
