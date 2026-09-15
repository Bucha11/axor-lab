"""The contract validation engine — re-exported from axor-core.

This file held a 180-line implementation. It was a typed port of
`contracts/validate.py`, which no longer exists either; axor-core now owns the
engine, because it owns three of the thirteen schemas it runs over and a generic
validator had no business belonging to one consumer of the contracts it
enforces.

The port and its original had already drifted — the port grew `maxLength`,
typed cross-schema refs and fail-closed type matching that the original never
got, so the vertical-slice examples were being checked by the weaker of two
engines while `lab_contracts` used the stronger one. Two implementations of one
thing do not stay one thing.

The name stays so the call sites do not move: `validate_against(obj,
schema_name, schemas)` takes the same arguments and means the same thing.
"""

from __future__ import annotations

from axor_core.contracts.schemas import validate_against

__all__ = ["validate_against"]
