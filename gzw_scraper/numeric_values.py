"""Add safe numeric projections without changing scraped source values."""

from __future__ import annotations

import math
import re
from typing import Any, Dict


_NUMERIC_WITH_UNIT = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(kg|g|lbs?|rpm|moa|m/s|km/h|cm|mm|m|%)\s*$",
    re.IGNORECASE,
)


def normalize_numeric_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with unambiguous unit-bearing strings in ``numeric_values``.

    Original source fields are retained. Values without an explicit recognized
    unit, comma-decimal values, ranges, and mixed text are deliberately skipped.
    Existing source-provided ``numeric_values`` entries take precedence.
    """
    result = dict(record)
    existing = record.get("numeric_values")
    if existing is not None and not isinstance(existing, dict):
        return result

    normalized: Dict[str, Any] = {}
    for field, raw_value in record.items():
        if field == "numeric_values" or not isinstance(raw_value, str):
            continue
        match = _NUMERIC_WITH_UNIT.fullmatch(raw_value)
        if not match:
            continue
        value = float(match.group(1))
        if not math.isfinite(value):
            continue
        normalized[field] = {"value": value, "unit": match.group(2).lower()}

    if isinstance(existing, dict):
        normalized.update(existing)
    if normalized or isinstance(existing, dict):
        result["numeric_values"] = normalized
    return result
