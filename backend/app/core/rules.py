"""Rules R1-R5 and the overall status, as ordinary deterministic code.

Each rule reads only a field's *effective* state and value, so a human override
changes the outcome while the original extraction is preserved on the field.
Source: rules.json v1.0 and the Assignment Brief, "Rules and result semantics".
"""

from typing import Any

from app.core.models import Evidence, FieldResult, FieldState, OverallStatus, RuleResult, RuleStatus

RADIUS_THRESHOLD_MILES = 150  # PASS at <= 150, FLAG above

RULE_NAMES = {
    "R1": "Required identity and effective date",
    "R2": "Vehicle count reconciliation",
    "R3": "Operating radius",
    "R4": "Hazardous materials",
    "R5": "Loss runs attachment",
}

PASTE_ONLY_REASON = "No attachments supplied (paste-only mode)"


def _inputs(*fields: FieldResult) -> dict[str, Any]:
    return {
        f.name: {
            "value": f.effective_value,
            "state": f.effective_state.value,
            "human_override": f.override is not None,
        }
        for f in fields
    }


def _evidence(*fields: FieldResult) -> list[Evidence]:
    return [c.evidence for f in fields for c in f.candidates if c.evidence and c.verified]


def _result(rule_id: str, status: RuleStatus, reason: str, *fields: FieldResult) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        name=RULE_NAMES[rule_id],
        status=status,
        reason=reason,
        inputs_used=_inputs(*fields),
        evidence=_evidence(*fields),
    )


_STATE_PHRASES = {
    FieldState.MISSING: "is missing",
    FieldState.CONFLICT: "has conflicting values",
    FieldState.INVALID: "could not be confirmed",
}


def _unresolved(f: FieldResult) -> str:
    phrase = _STATE_PHRASES.get(f.effective_state, "is unresolved")
    return f"{f.name} {phrase}" + (f": {f.reason}" if f.reason else "")


def r1_identity(name: FieldResult, date: FieldResult) -> RuleResult:
    """FLAG if a value is absent (takes precedence); UNKNOWN if invalid/conflicting; else PASS."""
    fields = (name, date)
    missing = [f for f in fields if f.effective_state is FieldState.MISSING]
    if missing:
        return _result("R1", RuleStatus.FLAG, "Missing: " + ", ".join(f.name for f in missing), *fields)
    unresolved = [f for f in fields if f.effective_state is not FieldState.KNOWN]
    if unresolved:
        return _result("R1", RuleStatus.UNKNOWN, "; ".join(_unresolved(f) for f in unresolved), *fields)
    return _result("R1", RuleStatus.PASS, "Business name and effective date are present and valid", *fields)


def r2_vehicle_count(declared: FieldResult, schedule: FieldResult) -> RuleResult:
    """UNKNOWN unless both counts are usable; then FLAG if they differ, PASS if equal (0 is valid)."""
    fields = (declared, schedule)
    unresolved = [f for f in fields if f.effective_state is not FieldState.KNOWN]
    if unresolved:
        return _result("R2", RuleStatus.UNKNOWN, "; ".join(_unresolved(f) for f in unresolved), *fields)
    d, s = declared.effective_value, schedule.effective_value
    if d != s:
        return _result("R2", RuleStatus.FLAG, f"Declared {d} vehicle(s) but the schedule lists {s}", *fields)
    return _result("R2", RuleStatus.PASS, f"Declared count matches the schedule ({d})", *fields)


def r3_radius(radius: FieldResult) -> RuleResult:
    """Unresolved first, then the threshold: <= 150 PASS, > 150 FLAG."""
    if radius.effective_state is not FieldState.KNOWN:
        return _result("R3", RuleStatus.UNKNOWN, _unresolved(radius), radius)
    miles = radius.effective_value
    if not isinstance(miles, (int, float)) or isinstance(miles, bool) or miles < 0:
        return _result("R3", RuleStatus.UNKNOWN, f"Radius is not a valid non-negative number: {miles!r}", radius)
    if miles > RADIUS_THRESHOLD_MILES:
        return _result("R3", RuleStatus.FLAG, f"Radius {miles} mi is above {RADIUS_THRESHOLD_MILES} mi", radius)
    return _result("R3", RuleStatus.PASS, f"Radius {miles} mi is within {RADIUS_THRESHOLD_MILES} mi", radius)


def r4_hazmat(hazmat: FieldResult) -> RuleResult:
    """Explicit No -> PASS, explicit Yes -> FLAG, anything else -> UNKNOWN."""
    if hazmat.effective_state is not FieldState.KNOWN or not isinstance(hazmat.effective_value, bool):
        return _result("R4", RuleStatus.UNKNOWN, _unresolved(hazmat), hazmat)
    if hazmat.effective_value:
        return _result("R4", RuleStatus.FLAG, "Hazardous materials: Yes", hazmat)
    return _result("R4", RuleStatus.PASS, "Hazardous materials: No", hazmat)


def r5_loss_runs(loss_runs: FieldResult, paste_only: bool) -> RuleResult:
    """Readable report -> PASS; none attached -> FLAG; unreadable/unclassifiable -> UNKNOWN.

    Project decision: with no files at all (paste-only) the result is UNKNOWN rather than
    FLAG, unless a reviewer has overridden the field. Documented in the README.
    """
    if paste_only and loss_runs.override is None:
        return _result("R5", RuleStatus.UNKNOWN, PASTE_ONLY_REASON, loss_runs)
    if loss_runs.effective_state is not FieldState.KNOWN:
        return _result("R5", RuleStatus.UNKNOWN, _unresolved(loss_runs), loss_runs)
    if loss_runs.effective_value is True:
        return _result("R5", RuleStatus.PASS, loss_runs.reason or "Loss runs report attached", loss_runs)
    return _result(
        "R5", RuleStatus.FLAG,
        "No loss runs or loss history report is attached (a promise in the email does not count)",
        loss_runs,
    )


def evaluate_rules(fields: dict[str, FieldResult], paste_only: bool = False) -> list[RuleResult]:
    return [
        r1_identity(fields["business_name"], fields["requested_effective_date"]),
        r2_vehicle_count(fields["declared_vehicle_count"], fields["schedule_vehicle_count"]),
        r3_radius(fields["operating_radius_miles"]),
        r4_hazmat(fields["hazardous_materials"]),
        r5_loss_runs(fields["loss_runs_present"], paste_only),
    ]


def overall_status(results: list[RuleResult]) -> OverallStatus:
    """READY_FOR_REVIEW only when every rule passes. Neither status is a coverage decision."""
    if results and all(r.status is RuleStatus.PASS for r in results):
        return OverallStatus.READY_FOR_REVIEW
    return OverallStatus.NEEDS_REVIEW
