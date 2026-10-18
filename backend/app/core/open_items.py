"""What the broker must be asked for, derived deterministically from the rule results.

The narrative model writes prose around this list; it never decides what is open.
"""

from app.core.models import EXTRACTED_FIELDS, FieldResult, FieldState, OpenItem, RuleResult, RuleStatus
from app.core.rules import PASTE_ONLY_REASON, RADIUS_THRESHOLD_MILES

FIELD_LABELS = {
    "business_name": "Business name",
    "requested_effective_date": "Requested effective date",
    "garaging_state": "Garaging state",
    "operations_description": "Operations description",
    "declared_vehicle_count": "Declared vehicle count",
    "operating_radius_miles": "Operating radius (miles)",
    "hazardous_materials": "Hazardous materials answer",
    "loss_runs_present": "Loss runs report",
    "schedule_vehicle_count": "Vehicle schedule",
}

RULE_INPUTS = {
    "R1": ["business_name", "requested_effective_date"],
    "R2": ["declared_vehicle_count", "schedule_vehicle_count"],
    "R3": ["operating_radius_miles"],
    "R4": ["hazardous_materials"],
    "R5": ["loss_runs_present"],
}


def describe_unresolved(field: FieldResult) -> str:
    label = FIELD_LABELS[field.name]
    state = field.effective_state
    if state is FieldState.MISSING:
        if field.name == "schedule_vehicle_count":
            return "No vehicle schedule (CSV) was supplied"
        return f"{label} was not provided"
    if state is FieldState.CONFLICT:
        said = "; ".join(f"{c.source_file} says {c.raw_value!r}" for c in field.candidates if c.verified)
        return f"{label} differs between sources ({said})"
    return f"{label} could not be confirmed: {field.reason or 'value is not usable'}"


def _flag_text(rule: RuleResult, fields: dict[str, FieldResult]) -> str:
    if rule.rule_id == "R2":
        declared = fields["declared_vehicle_count"].effective_value
        schedule = fields["schedule_vehicle_count"].effective_value
        return f"Declared vehicle count ({declared}) does not match the vehicle schedule ({schedule} vehicles)"
    if rule.rule_id == "R3":
        miles = fields["operating_radius_miles"].effective_value
        return f"Operating radius of {miles} miles is above {RADIUS_THRESHOLD_MILES} miles"
    if rule.rule_id == "R4":
        return "Hazardous materials answered Yes; details of the materials hauled are needed"
    if rule.rule_id == "R5":
        return "No loss runs or loss history report is attached"
    return rule.reason  # R1: "Missing: ..."


def build_open_items(fields: dict[str, FieldResult], rules: list[RuleResult]) -> list[OpenItem]:
    items: list[OpenItem] = []
    covered: set[str] = set()

    for rule in rules:
        inputs = RULE_INPUTS[rule.rule_id]
        covered.update(inputs)
        if rule.status is RuleStatus.PASS:
            continue
        if rule.status is RuleStatus.FLAG and rule.rule_id != "R1":
            items.append(OpenItem(rule_id=rule.rule_id, text=_flag_text(rule, fields)))
            continue
        if rule.rule_id == "R5" and rule.reason == PASTE_ONLY_REASON:
            items.append(OpenItem(rule_id="R5", text="No attachments were supplied, so the loss runs report could not be checked"))
            continue
        for name in inputs:  # R1 FLAG and every UNKNOWN: name each unresolved input
            if fields[name].effective_state is not FieldState.KNOWN:
                items.append(OpenItem(rule_id=rule.rule_id, text=describe_unresolved(fields[name])))

    for name in EXTRACTED_FIELDS:  # fields no rule covers (garaging state, operations)
        if name not in covered and fields[name].effective_state is not FieldState.KNOWN:
            items.append(OpenItem(rule_id=None, text=describe_unresolved(fields[name])))
    return items
