"""Turn raw source values into clean, comparable values.

Every normaliser returns a Normalized(state, value, note):
  KNOWN   -> value is the clean value
  MISSING -> the source gave no value ("Not provided", blank, ...); absence is never "No"
  INVALID -> a value was given but cannot be used (bad date, "3 or 4", "sometimes", ...)

Pure functions only: no I/O, no LLM.
"""

import re
from datetime import datetime
from typing import Any, Callable, NamedTuple

from app.core.models import FieldState


class Normalized(NamedTuple):
    state: FieldState
    value: Any = None
    note: str | None = None


def _known(value: Any) -> Normalized:
    return Normalized(FieldState.KNOWN, value)


def _missing(note: str = "No value provided") -> Normalized:
    return Normalized(FieldState.MISSING, None, note)


def _invalid(note: str) -> Normalized:
    return Normalized(FieldState.INVALID, None, note)


# --- absence -----------------------------------------------------------------

_ABSENT_MARKERS = {
    "",
    "-",
    "--",
    "—",
    "n/a",
    "na",
    "not provided",
    "none provided",
    "not available",
    "not stated",
    "not specified",
    "unknown",
    "tbd",
    "to be provided",
    "to be confirmed",
}


def _clean_text(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw)).strip()


def is_absent(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str):
        return _clean_text(raw).rstrip(".").casefold() in _ABSENT_MARKERS
    return False


# --- text ----------------------------------------------------------------------


def normalize_text(raw: Any) -> Normalized:
    """Trim and collapse whitespace; keep the source wording."""
    if is_absent(raw):
        return _missing()
    return _known(_clean_text(raw))


# --- date ----------------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",  # US convention; DD/MM/YYYY is not accepted (documented limitation)
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
)


def normalize_date(raw: Any) -> Normalized:
    """Return an ISO YYYY-MM-DD string. No past/future rule applies."""
    if is_absent(raw):
        return _missing()
    text = _clean_text(raw).rstrip(".")
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)  # "1st" -> "1"
    # Two-digit year ("11/01/26", "Nov 1, 26") means 20YY; tried only after the full formats,
    # so year-first dates such as "2026/11/01" are never re-read.
    for candidate in (text, re.sub(r"(?<=[/ ])(\d{2})$", r"20\1", text)):
        for fmt in _DATE_FORMATS:
            try:
                return _known(datetime.strptime(candidate, fmt).date().isoformat())
            except ValueError:
                continue
    return _invalid(f"Not a valid calendar date: {text!r}")


# --- US state ------------------------------------------------------------------

_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_LOOKUP = {code.casefold(): code for code in _STATES} | {
    name.casefold(): code for code, name in _STATES.items()
}


def normalize_state(raw: Any) -> Normalized:
    """'California', 'california', 'CA', 'ca' -> 'CA'."""
    if is_absent(raw):
        return _missing()
    text = _clean_text(raw).rstrip(".")
    code = _STATE_LOOKUP.get(text.casefold())
    if code is None:
        return _invalid(f"Not a recognised US state: {text!r}")
    return _known(code)


# --- numbers -------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?|-?\.\d+")
_SMALL_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000}
_FRACTION_WORDS = re.compile(r"\b(half|halves|quarters?|thirds?|point)\b", re.IGNORECASE)


def _word_numbers(text: str) -> list[float]:
    """Numbers written in words: 'two vehicles' -> [2], 'one hundred fifty' -> [150],
    'a hundred and fifty miles' -> [150], 'two or three' -> [2, 3]."""
    tokens = re.findall(r"[a-z]+", text.casefold())  # hyphens split: "twenty-five"
    numbers: list[float] = []
    total = group = 0
    in_number = False
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in _SMALL_NUMBERS:
            group += _SMALL_NUMBERS[tok]
        elif tok in _SCALES and in_number:
            if tok == "hundred":
                group *= 100
            else:
                total, group = total + group * 1000, 0
        elif tok == "a" and nxt in _SCALES:  # "a hundred"
            group = 1
        elif tok == "and" and in_number and (nxt in _SMALL_NUMBERS):
            continue  # "one hundred and fifty"
        else:
            if in_number:
                numbers.append(float(total + group))
            total = group = 0
            in_number = False
            continue
        in_number = True
    if in_number:
        numbers.append(float(total + group))
    return numbers


def _single_number(raw: Any) -> float | str:
    """Return the one number in raw, or an error message string.

    Digits and words may both appear ("2 (two)") as long as they agree.
    """
    if isinstance(raw, bool):  # bool is a subclass of int in Python
        return "Expected a number, got a yes/no value"
    if isinstance(raw, (int, float)):
        return float(raw)
    text = _clean_text(raw)
    if _FRACTION_WORDS.search(text):
        return f"Fractions written in words are not accepted: {text!r}"
    found = [float(m.replace(",", "")) for m in _NUMBER_RE.findall(text)] + _word_numbers(text)
    if len(set(found)) != 1:
        return f"Expected exactly one number, found {len(set(found))} in {text!r}"
    return found[0]


def _tidy(number: float) -> int | float:
    return int(number) if number.is_integer() else number


def normalize_count(raw: Any) -> Normalized:
    """Non-negative whole number. Zero is valid."""
    if is_absent(raw):
        return _missing()
    number = _single_number(raw)
    if isinstance(number, str):
        return _invalid(number)
    if not number.is_integer():
        return _invalid(f"Count is not a whole number: {raw!r}")
    if number < 0:
        return _invalid(f"Count is negative: {raw!r}")
    return _known(int(number))


_NON_MILE_UNITS = re.compile(r"\b(km|kms|kilomet(?:er|re)s?)\b", re.IGNORECASE)


def normalize_miles(raw: Any) -> Normalized:
    """Numeric miles. 150, 150.0 and '150 miles' are all 150. Negative is invalid."""
    if is_absent(raw):
        return _missing()
    if isinstance(raw, str) and _NON_MILE_UNITS.search(raw):
        return _invalid(f"Radius is not in miles: {raw!r}")
    number = _single_number(raw)
    if isinstance(number, str):
        return _invalid(number)
    if number < 0:
        return _invalid(f"Radius is negative: {raw!r}")
    return _known(_tidy(number))


# --- yes / no ------------------------------------------------------------------

_YES = {"yes", "y", "true"}
_NO = {"no", "n", "false"}
_LEADING_WORD = re.compile(r"^([a-z]+)\b[\s,.;:\-–(]*")


def normalize_yes_no(raw: Any) -> Normalized:
    """Explicit Yes/true or No/false only. Anything else is ambiguous (INVALID)."""
    if is_absent(raw):
        return _missing()
    if isinstance(raw, bool):
        return _known(raw)
    text = _clean_text(raw).casefold()
    match = _LEADING_WORD.match(text)
    word = match.group(1) if match else ""
    if word in _YES:
        return _known(True)
    if word in _NO:
        return _known(False)
    return _invalid(f"Not an explicit Yes or No: {_clean_text(raw)!r}")


# --- dispatch ------------------------------------------------------------------

FIELD_NORMALIZERS: dict[str, Callable[[Any], Normalized]] = {
    "business_name": normalize_text,
    "requested_effective_date": normalize_date,
    "garaging_state": normalize_state,
    "operations_description": normalize_text,
    "declared_vehicle_count": normalize_count,
    "operating_radius_miles": normalize_miles,
    "hazardous_materials": normalize_yes_no,
    # Derived fields are computed by code, but a reviewer can still override them.
    "schedule_vehicle_count": normalize_count,
    "loss_runs_present": normalize_yes_no,
}


def normalize_field(name: str, raw: Any) -> Normalized:
    return FIELD_NORMALIZERS[name](raw)


def comparison_key(name: str, value: Any) -> Any:
    """Key used to decide whether two KNOWN values agree.

    Text compares case-insensitively and ignores whitespace and trailing
    punctuation, so 'Local delivery.' and 'local delivery' agree.
    Numbers already compare equal across 150 / 150.0.
    """
    if isinstance(value, str) and name in ("business_name", "operations_description"):
        return re.sub(r"\s+", " ", value).strip().rstrip(".!;,").casefold()
    return value
