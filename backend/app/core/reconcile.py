"""Combine per-source candidates into one FieldResult, and apply human overrides.

Policy (field_specification.json "conflicts"):
- Explicit values that agree after normalisation      -> KNOWN
- Explicit values that disagree                       -> CONFLICT, value null, all alternatives kept,
                                                         no preferred source
- A source that is silent is not a conflicting value  -> the other source's value stands
- Only unusable values                                -> INVALID
- Nothing found                                       -> MISSING, with the sources that were checked
Candidates whose excerpt could not be verified are shown but never used.
"""

from typing import Any

from app.core.models import Candidate, FieldResult, FieldState, Override
from app.core.normalize import comparison_key, normalize_field


def _describe(c: Candidate) -> str:
    return f"{c.source_file} says {c.raw_value!r}"


def reconcile_field(name: str, candidates: list[Candidate], sources_checked: list[str]) -> FieldResult:
    usable = [c for c in candidates if c.verified]
    unverified = len(candidates) - len(usable)
    known = [c for c in usable if c.state is FieldState.KNOWN]
    invalid = [c for c in usable if c.state is FieldState.INVALID]

    def result(state: FieldState, reason: str, value: Any = None) -> FieldResult:
        if unverified:
            reason += f" ({unverified} unverified value(s) ignored)"
        return FieldResult(
            name=name,
            state=state,
            value=value,
            reason=reason,
            candidates=candidates,
            sources_checked=sources_checked,
        )

    distinct_keys = {comparison_key(name, c.value) for c in known}

    if len(distinct_keys) > 1:
        return result(FieldState.CONFLICT, "Sources disagree: " + "; ".join(_describe(c) for c in known))

    if known and invalid:
        # One source has a usable value, another an unusable one: do not silently prefer either.
        return result(
            FieldState.INVALID,
            "Unresolved: " + "; ".join(_describe(c) for c in known + invalid),
        )

    if known:
        sources = ", ".join(c.source_file for c in known)
        return result(FieldState.KNOWN, f"Stated in {sources}", value=known[0].value)

    if invalid:
        return result(FieldState.INVALID, "; ".join(f"{c.source_file}: {c.note}" for c in invalid))

    checked = ", ".join(sources_checked) if sources_checked else "no sources"
    return result(FieldState.MISSING, f"Not provided in: {checked}")


def apply_override(field: FieldResult, raw_value: Any, note: str = "") -> FieldResult:
    """Return a copy with a human override. The value must normalise cleanly."""
    normalized = normalize_field(field.name, raw_value)
    if normalized.state is not FieldState.KNOWN:
        raise ValueError(f"Override for {field.name} is not usable: {normalized.note}")
    return field.model_copy(update={"override": Override(value=normalized.value, note=note)})


def remove_override(field: FieldResult) -> FieldResult:
    return field.model_copy(update={"override": None})
