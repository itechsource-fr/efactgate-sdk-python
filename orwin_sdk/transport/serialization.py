"""JSON serialization and deserialization for Orwin SDK models.

Handles conversion between frozen dataclass instances and JSON strings,
applying type-specific formatting rules:
- datetime → ISO 8601 UTC string (suffix "Z")
- UUID → lowercase canonical string with dashes (8-4-4-4-12)
- Decimal → numeric string without float conversion
- Enum → value string (not "EnumClass.member")
- None → null
- Unknown fields at deserialization → silently ignored
"""

from __future__ import annotations

import dataclasses
import json
import types as builtin_types
import typing
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from orwin_sdk.exceptions import DeserializationError

T = TypeVar("T")


def serialize(model: object) -> str:
    """Serialize an SDK model (frozen dataclass) to a JSON string.

    Args:
        model: A frozen dataclass instance from orwin_sdk.models.

    Returns:
        JSON string with type-specific formatting applied.

    Raises:
        TypeError: If the input is not a dataclass instance.
    """
    return json.dumps(_encode_value(model), ensure_ascii=False)


def deserialize(json_str: str, model_class: type[T]) -> T:
    """Deserialize a JSON string into an SDK model instance.

    Args:
        json_str: JSON string to parse.
        model_class: The target dataclass type to instantiate.

    Returns:
        An instance of model_class populated from the JSON data.

    Raises:
        DeserializationError: On invalid JSON, missing required fields,
            or incompatible field types.
    """
    try:
        raw = json.loads(json_str)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DeserializationError(
            code="deserialization_error",
            message=f"Invalid JSON: {exc}",
            field="<root>",
            reason="invalid_json",
        ) from exc

    if not isinstance(raw, dict):
        raise DeserializationError(
            code="deserialization_error",
            message="Expected a JSON object at root level",
            field="<root>",
            reason="expected_object",
        )

    return _decode_dataclass(raw, model_class, path="<root>")


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def _encode_value(value: object) -> Any:
    """Recursively encode a value into a JSON-compatible representation."""
    if value is None:
        return None

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _encode_dataclass(value)

    if isinstance(value, datetime):
        return _encode_datetime(value)

    if isinstance(value, UUID):
        return str(value).lower()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, list):
        return [_encode_value(item) for item in value]

    if isinstance(value, dict):
        return {str(k): _encode_value(v) for k, v in value.items()}

    # Primitives (str, int, float, bool) pass through directly
    return value


def _encode_dataclass(instance: object) -> dict[str, Any]:
    """Encode a dataclass instance into a dict with formatted values."""
    result: dict[str, Any] = {}
    for field in dataclasses.fields(instance):  # type: ignore[arg-type]
        value = getattr(instance, field.name)
        result[field.name] = _encode_value(value)
    return result


def _encode_datetime(dt: datetime) -> str:
    """Encode a datetime as ISO 8601 UTC with 'Z' suffix."""
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

    # Format with Z suffix instead of +00:00
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
    # Include microseconds only if non-zero
    if dt.microsecond:
        iso += f".{dt.microsecond:06d}".rstrip("0")
    iso += "Z"
    return iso


# ---------------------------------------------------------------------------
# Decoding helpers
# ---------------------------------------------------------------------------


def _decode_dataclass(data: dict[str, Any], cls: type[T], path: str) -> T:
    """Decode a dict into a dataclass instance, ignoring unknown fields."""
    if not dataclasses.is_dataclass(cls):
        raise DeserializationError(
            code="deserialization_error",
            message=f"{cls.__name__} is not a dataclass",
            field=path,
            reason="not_a_dataclass",
        )

    fields = dataclasses.fields(cls)
    kwargs: dict[str, Any] = {}

    for field in fields:
        field_path = f"{path}.{field.name}" if path != "<root>" else field.name

        if field.name not in data:
            # Check if the field has a default or is optional
            if _field_has_default(field):
                continue
            if _is_optional_type(field.type):
                kwargs[field.name] = None
                continue
            raise DeserializationError(
                code="deserialization_error",
                message=f"Missing required field: {field.name}",
                field=field_path,
                reason="missing_field",
            )

        raw_value = data[field.name]
        resolved_type = _resolve_field_type(field, cls)
        kwargs[field.name] = _decode_value(raw_value, resolved_type, field_path)

    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise DeserializationError(
            code="deserialization_error",
            message=f"Cannot construct {cls.__name__}: {exc}",
            field=path,
            reason="construction_error",
        ) from exc


def _decode_value(value: Any, target_type: Any, path: str) -> Any:
    """Decode a single value according to its target type."""
    # Handle None
    if value is None:
        return None

    # Unwrap Optional[X] → X
    inner_type = _unwrap_optional(target_type)
    if inner_type is not None:
        return _decode_value(value, inner_type, path)

    # Since inner_type is None, actual_type is target_type
    actual_type = target_type

    # datetime
    if actual_type is datetime:
        return _decode_datetime(value, path)

    # UUID
    if actual_type is UUID:
        return _decode_uuid(value, path)

    # Decimal
    if actual_type is Decimal:
        return _decode_decimal(value, path)

    # Enum subclass
    if isinstance(actual_type, type) and issubclass(actual_type, Enum):
        return _decode_enum(value, actual_type, path)

    # list[X]
    origin = getattr(actual_type, "__origin__", None)
    if origin is list:
        args = getattr(actual_type, "__args__", (Any,))
        item_type = args[0] if args else Any
        if not isinstance(value, list):
            raise DeserializationError(
                code="deserialization_error",
                message=f"Expected list at {path}, got {type(value).__name__}",
                field=path,
                reason="type_mismatch",
            )
        return [
            _decode_value(item, item_type, f"{path}[{i}]")
            for i, item in enumerate(value)
        ]

    # dict[str, X]
    if origin is dict:
        raw_args = getattr(actual_type, "__args__", None)
        val_type: Any = raw_args[1] if raw_args and len(raw_args) > 1 else Any
        if not isinstance(value, dict):
            raise DeserializationError(
                code="deserialization_error",
                message=f"Expected dict at {path}, got {type(value).__name__}",
                field=path,
                reason="type_mismatch",
            )
        return {k: _decode_value(v, val_type, f"{path}.{k}") for k, v in value.items()}

    # Nested dataclass
    if isinstance(actual_type, type) and dataclasses.is_dataclass(actual_type):
        if not isinstance(value, dict):
            raise DeserializationError(
                code="deserialization_error",
                message=f"Expected object at {path}, got {type(value).__name__}",
                field=path,
                reason="type_mismatch",
            )
        return _decode_dataclass(value, actual_type, path)

    # Primitives (str, int, float, bool) — validate type loosely
    if actual_type is str:
        if not isinstance(value, str):
            raise DeserializationError(
                code="deserialization_error",
                message=f"Expected string at {path}, got {type(value).__name__}",
                field=path,
                reason="type_mismatch",
            )
        return value

    if actual_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise DeserializationError(
                code="deserialization_error",
                message=f"Expected int at {path}, got {type(value).__name__}",
                field=path,
                reason="type_mismatch",
            )
        return value

    if actual_type is float:
        if not isinstance(value, (int, float)):
            raise DeserializationError(
                code="deserialization_error",
                message=f"Expected number at {path}, got {type(value).__name__}",
                field=path,
                reason="type_mismatch",
            )
        return float(value)

    if actual_type is bool:
        if not isinstance(value, bool):
            raise DeserializationError(
                code="deserialization_error",
                message=f"Expected bool at {path}, got {type(value).__name__}",
                field=path,
                reason="type_mismatch",
            )
        return value

    # Any or unknown type — pass through
    return value


def _decode_datetime(value: Any, path: str) -> datetime:
    """Parse an ISO 8601 datetime string to a UTC datetime."""
    if not isinstance(value, str):
        raise DeserializationError(
            code="deserialization_error",
            message=f"Expected ISO 8601 string at {path}, got {type(value).__name__}",
            field=path,
            reason="type_mismatch",
        )
    try:
        # Handle 'Z' suffix
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # Ensure UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise DeserializationError(
            code="deserialization_error",
            message=f"Invalid datetime at {path}: {exc}",
            field=path,
            reason="invalid_format",
        ) from exc


def _decode_uuid(value: Any, path: str) -> UUID:
    """Parse a UUID string."""
    if not isinstance(value, str):
        raise DeserializationError(
            code="deserialization_error",
            message=f"Expected UUID string at {path}, got {type(value).__name__}",
            field=path,
            reason="type_mismatch",
        )
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise DeserializationError(
            code="deserialization_error",
            message=f"Invalid UUID at {path}: {exc}",
            field=path,
            reason="invalid_format",
        ) from exc


def _decode_decimal(value: Any, path: str) -> Decimal:
    """Parse a Decimal from a string representation."""
    if not isinstance(value, str):
        raise DeserializationError(
            code="deserialization_error",
            message=f"Expected numeric string at {path}, got {type(value).__name__}",
            field=path,
            reason="type_mismatch",
        )
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise DeserializationError(
            code="deserialization_error",
            message=f"Invalid decimal at {path}: {exc}",
            field=path,
            reason="invalid_format",
        ) from exc


def _decode_enum(value: Any, enum_class: type[Enum], path: str) -> Enum:
    """Parse an enum value."""
    if not isinstance(value, str):
        raise DeserializationError(
            code="deserialization_error",
            message=f"Expected string for enum at {path}, got {type(value).__name__}",
            field=path,
            reason="type_mismatch",
        )
    try:
        return enum_class(value)
    except ValueError as exc:
        valid_values = [e.value for e in enum_class]
        raise DeserializationError(
            code="deserialization_error",
            message=f"Invalid enum value '{value}' at {path}. Valid: {valid_values}",
            field=path,
            reason="invalid_enum_value",
        ) from exc


# ---------------------------------------------------------------------------
# Type introspection utilities
# ---------------------------------------------------------------------------


def _field_has_default(field: dataclasses.Field[Any]) -> bool:
    """Check if a dataclass field has a default value."""
    has_default = field.default is not dataclasses.MISSING
    has_factory = field.default_factory is not dataclasses.MISSING
    return has_default or has_factory


def _is_optional_type(type_annotation: Any) -> bool:
    """Check if a type annotation represents an Optional type (Union with None)."""
    origin = getattr(type_annotation, "__origin__", None)

    if origin is builtin_types.UnionType or _is_union(origin):
        args = getattr(type_annotation, "__args__", ())
        return type(None) in args

    # Handle string annotations
    if isinstance(type_annotation, str):
        return "None" in type_annotation or "| None" in type_annotation

    return False


def _is_union(origin: Any) -> bool:
    """Check if origin is typing.Union."""
    return origin is typing.Union


def _unwrap_optional(type_annotation: Any) -> Any:
    """If type is Optional[X] / X | None, return X. Otherwise return None."""
    origin = getattr(type_annotation, "__origin__", None)

    if origin is builtin_types.UnionType or origin is typing.Union:
        args = getattr(type_annotation, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and type(None) in args:
            return non_none[0]

    return None


def _resolve_field_type(field: dataclasses.Field[Any], cls: type[Any]) -> Any:
    """Resolve the actual type for a dataclass field, handling string annotations."""
    import importlib
    import sys

    annotation = field.type

    # If it's already a real type, return it
    if not isinstance(annotation, str):
        return annotation

    # Resolve string annotations using the class's module globals
    module_name = cls.__module__
    if module_name in sys.modules:
        module = sys.modules[module_name]
        ns = getattr(module, "__dict__", {})
    else:
        module = importlib.import_module(module_name)
        ns = module.__dict__

    # Add common types to namespace for resolution
    eval_ns: dict[str, Any] = {
        "datetime": datetime,
        "UUID": UUID,
        "Decimal": Decimal,
        "Any": Any,
    }
    eval_ns.update(ns)

    # Also import model enums for resolution
    try:
        from orwin_sdk.models.enums import (
            FluxStatus,
            FluxType,
            ImportFormat,
            InvoiceFormat,
        )

        eval_ns["FluxStatus"] = FluxStatus
        eval_ns["FluxType"] = FluxType
        eval_ns["ImportFormat"] = ImportFormat
        eval_ns["InvoiceFormat"] = InvoiceFormat
    except ImportError:
        pass

    # Import model classes for nested resolution
    try:
        from orwin_sdk.models.invoice import ImportErrorDetail
        from orwin_sdk.models.status import TransitionDetail

        eval_ns["TransitionDetail"] = TransitionDetail
        eval_ns["ImportErrorDetail"] = ImportErrorDetail
    except ImportError:
        pass

    try:
        return eval(annotation, eval_ns)  # eval needed for string annotations
    except Exception:
        # Fallback: treat as Any
        return Any


__all__ = [
    "deserialize",
    "serialize",
]
