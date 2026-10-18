import json
import re


def parse_json_object(text: str) -> dict:
    """Accept a bare JSON object, optionally wrapped in code fences or stray prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("reply contained no JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON ({exc.msg})") from None
    if not isinstance(data, dict):
        raise ValueError("reply was not a JSON object")
    return data
