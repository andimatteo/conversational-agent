"""Validation for job specs described by a vertical pack.

The pack schema is intentionally small and is not quite JSON Schema.  A field
definition is either a bare type name (``str``, ``int``, ...) or a mapping such
as ``{type: enum, values: [...]}``.  Keeping the validator here, independent of
the API, gives every intake path the same rules without coupling it to FastAPI.

``validate_spec`` validates a complete spec, including top-level required
fields.  ``sanitize_extracted`` is for partial, untrusted extraction output: it
drops unknown/invalid values, preserves valid siblings, and reports what it
dropped.  Neither function mutates its input.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import re
from typing import Any


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DROP = object()

# These fields carry product metadata alongside domain-defined spec fields.
# Their values still have a minimal shape check; only their internals are not
# governed by the vertical pack.
_SYSTEM_FIELDS: dict[str, dict[str, Any]] = {
    "vertical": {"type": "str"},
    "existing_quote": {"type": "object"},
    "existing_quotes": {"type": "list", "items": {"type": "object"}},
    "notes": {"type": "str"},
}


def validate_spec(spec: dict, pack: dict) -> list[str]:
    """Return human-readable validation errors for a complete job spec.

    An empty list means the spec conforms to ``pack['spec_schema']``.  Required
    values may be ``False`` or ``0``; only ``None`` and empty strings/
    collections count as missing.
    """

    fields, required, schema_errors = _schema(pack)
    if schema_errors:
        return schema_errors
    if not isinstance(spec, dict):
        return [f"spec: expected object, got {_type_name(spec)}"]

    errors: list[str] = []
    allowed = set(fields) | set(_SYSTEM_FIELDS)
    for key in spec:
        if key not in allowed:
            errors.append(f"{key}: unknown top-level field")

    for name in required:
        if name not in spec or _is_missing(spec[name]):
            errors.append(f"{name}: required field is missing or empty")

    for name, value in spec.items():
        definition = fields.get(name, _SYSTEM_FIELDS.get(name))
        if definition is None:
            continue
        # Avoid a redundant type error after the clearer required-field error.
        if name in required and _is_missing(value):
            continue
        errors.extend(_validate_value(value, definition, name))
    return errors


def sanitize_extracted(extracted: dict, pack: dict) -> tuple[dict, list[str]]:
    """Sanitize partial, untrusted spec data produced by a parser or model.

    Unknown keys and invalid values are omitted.  Valid values inside a partly
    invalid object/list are retained.  Missing required fields are deliberately
    not errors because one document or interview turn commonly supplies only a
    subset of the final spec.
    """

    fields, _required, schema_errors = _schema(pack)
    if schema_errors:
        return {}, schema_errors
    if not isinstance(extracted, dict):
        return {}, [f"extracted: expected object, got {_type_name(extracted)}"]

    clean: dict[str, Any] = {}
    errors: list[str] = []
    for name, value in extracted.items():
        definition = fields.get(name, _SYSTEM_FIELDS.get(name))
        if definition is None:
            errors.append(f"{name}: unknown top-level field")
            continue
        sanitized, value_errors = _sanitize_value(value, definition, name)
        errors.extend(value_errors)
        if sanitized is not _DROP and not _is_missing(sanitized):
            clean[name] = sanitized
    return clean, errors


def _schema(pack: Any) -> tuple[dict[str, Any], list[str], list[str]]:
    if not isinstance(pack, dict):
        return {}, [], [f"pack: expected object, got {_type_name(pack)}"]
    schema = pack.get("spec_schema")
    if not isinstance(schema, dict):
        return {}, [], ["pack.spec_schema: expected object"]
    fields = schema.get("fields")
    required = schema.get("required", [])
    errors: list[str] = []
    if not isinstance(fields, dict):
        errors.append("pack.spec_schema.fields: expected object")
        fields = {}
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        errors.append("pack.spec_schema.required: expected list of strings")
        required = []
    else:
        for name in required:
            if name not in fields:
                errors.append(f"pack.spec_schema.required: unknown field {name!r}")
    return fields, required, errors


def _definition(definition: Any, path: str) -> tuple[dict[str, Any] | None, list[str]]:
    if isinstance(definition, str):
        return {"type": definition}, []
    if isinstance(definition, dict):
        return definition, []
    return None, [f"{path}: invalid field definition in pack"]


def _validate_value(value: Any, definition: Any, path: str) -> list[str]:
    defn, errors = _definition(definition, path)
    if defn is None:
        return errors
    field_type = defn.get("type", "str")

    if field_type == "str":
        return [] if isinstance(value, str) else [_expected(path, "str", value)]
    if field_type == "int":
        return [] if isinstance(value, int) and not isinstance(value, bool) else [_expected(path, "int", value)]
    if field_type in ("number", "float"):
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        if valid and isinstance(value, float):
            valid = math.isfinite(value)
        return [] if valid else [_expected(path, "number", value)]
    if field_type == "bool":
        return [] if isinstance(value, bool) else [_expected(path, "bool", value)]
    if field_type == "date":
        return [] if _valid_date(value) else [f"{path}: expected date in YYYY-MM-DD format"]
    if field_type == "enum":
        values = defn.get("values")
        if not isinstance(values, list) or not values:
            return [f"{path}: enum definition has no values"]
        if any(type(value) is type(candidate) and value == candidate for candidate in values):
            return []
        return [f"{path}: expected one of {values!r}, got {value!r}"]
    if field_type == "object":
        if not isinstance(value, dict):
            return [_expected(path, "object", value)]
        nested = defn.get("fields")
        if nested is None:
            return []
        if not isinstance(nested, dict):
            return [f"{path}: object definition fields must be an object"]
        errors = []
        for key in value:
            if key not in nested:
                errors.append(f"{path}.{key}: unknown field")
        nested_required = defn.get("required", [])
        if isinstance(nested_required, list):
            for key in nested_required:
                if key not in value or _is_missing(value[key]):
                    errors.append(f"{path}.{key}: required field is missing or empty")
        for key, nested_value in value.items():
            if key in nested:
                errors.extend(_validate_value(nested_value, nested[key], f"{path}.{key}"))
        return errors
    if field_type == "list":
        if not isinstance(value, list):
            return [_expected(path, "list", value)]
        item_definition = _list_item_definition(defn)
        if item_definition is None:
            return []
        errors = []
        for index, item in enumerate(value):
            errors.extend(_validate_value(item, item_definition, f"{path}[{index}]"))
        return errors
    return [f"{path}: unsupported field type {field_type!r} in pack"]


def _sanitize_value(value: Any, definition: Any, path: str) -> tuple[Any, list[str]]:
    defn, errors = _definition(definition, path)
    if defn is None:
        return _DROP, errors
    field_type = defn.get("type", "str")

    if field_type == "object":
        if not isinstance(value, dict):
            return _DROP, [_expected(path, "object", value)]
        nested = defn.get("fields")
        if nested is None:
            return deepcopy(value), []
        if not isinstance(nested, dict):
            return _DROP, [f"{path}: object definition fields must be an object"]
        clean: dict[str, Any] = {}
        errors = []
        for key, nested_value in value.items():
            if key not in nested:
                errors.append(f"{path}.{key}: unknown field")
                continue
            sanitized, nested_errors = _sanitize_value(nested_value, nested[key], f"{path}.{key}")
            errors.extend(nested_errors)
            if sanitized is not _DROP and not _is_missing(sanitized):
                clean[key] = sanitized
        return (clean if clean else _DROP), errors

    if field_type == "list":
        if not isinstance(value, list):
            return _DROP, [_expected(path, "list", value)]
        item_definition = _list_item_definition(defn)
        if item_definition is None:
            return deepcopy(value), []
        clean_items: list[Any] = []
        errors = []
        for index, item in enumerate(value):
            sanitized, item_errors = _sanitize_value(item, item_definition, f"{path}[{index}]")
            errors.extend(item_errors)
            if sanitized is not _DROP and not _is_missing(sanitized):
                clean_items.append(sanitized)
        return (clean_items if clean_items else _DROP), errors

    value_errors = _validate_value(value, defn, path)
    if value_errors:
        return _DROP, value_errors
    return deepcopy(value), []


def _list_item_definition(definition: dict[str, Any]) -> dict[str, Any] | str | None:
    item_fields = definition.get("item_fields")
    if item_fields is not None:
        return {"type": "object", "fields": item_fields}
    if "items" in definition:
        return definition["items"]
    if "item_type" in definition:
        return definition["item_type"]
    return None


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


def _expected(path: str, expected: str, value: Any) -> str:
    return f"{path}: expected {expected}, got {_type_name(value)}"


def _type_name(value: Any) -> str:
    return type(value).__name__
