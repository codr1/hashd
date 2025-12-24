"""
Schema validation for AOS.

Enforces JSON Schema validation at every data boundary.
Fails hard with clear errors when data doesn't match schema.
"""

import json
import sys
from pathlib import Path
from typing import Any

import jsonschema


class ValidationError(Exception):
    """Schema validation failed."""

    def __init__(self, schema_name: str, message: str, path: str = None):
        self.schema_name = schema_name
        self.path = path
        super().__init__(f"[{schema_name}] {message}" + (f" at {path}" if path else ""))


# Cache loaded schemas
_schema_cache: dict[str, dict] = {}


def _get_schemas_dir() -> Path:
    """Get path to schemas directory."""
    return Path(__file__).parent.parent.parent / "schemas"


def _load_schema(schema_name: str) -> dict:
    """Load schema by name, with caching."""
    if schema_name not in _schema_cache:
        schema_path = _get_schemas_dir() / f"{schema_name}.schema.json"
        if not schema_path.exists():
            raise ValidationError(schema_name, f"Schema file not found: {schema_path}")
        _schema_cache[schema_name] = json.loads(schema_path.read_text())
    return _schema_cache[schema_name]


def validate(data: dict, schema_name: str) -> None:
    """
    Validate data against named schema.

    Args:
        data: Dictionary to validate
        schema_name: Schema name (e.g., "meta", "result", "review")

    Raises:
        ValidationError: If validation fails
    """
    schema = _load_schema(schema_name)

    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        # Build clear error message
        path = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "(root)"
        message = f"{e.message}"
        raise ValidationError(schema_name, message, path) from None


def validate_file(filepath: Path, schema_name: str) -> dict:
    """
    Load JSON file and validate against schema.

    Args:
        filepath: Path to JSON file
        schema_name: Schema name to validate against

    Returns:
        Parsed and validated data

    Raises:
        ValidationError: If file invalid or doesn't match schema
    """
    if not filepath.exists():
        raise ValidationError(schema_name, f"File not found: {filepath}")

    try:
        data = json.loads(filepath.read_text())
    except json.JSONDecodeError as e:
        raise ValidationError(schema_name, f"Invalid JSON in {filepath}: {e}") from None

    validate(data, schema_name)
    return data


def validate_or_die(data: dict, schema_name: str, context: str, exit_code: int = 2) -> None:
    """
    Validate data and exit with error if invalid.

    Args:
        data: Dictionary to validate
        schema_name: Schema name to validate against
        context: Human-readable context for error message
        exit_code: Exit code to use on failure (default: 2 for config error)
    """
    try:
        validate(data, schema_name)
    except ValidationError as e:
        print(f"ERROR: Schema validation failed while {context}", file=sys.stderr)
        print(f"  Schema: {e.schema_name}", file=sys.stderr)
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(exit_code)


def validate_before_write(data: dict, schema_name: str, filepath: Path) -> None:
    """
    Validate data before writing to file. Ensures we never write invalid data.

    Args:
        data: Dictionary to validate and write
        schema_name: Schema name to validate against
        filepath: Where the data will be written (for error context)

    Raises:
        ValidationError: If data doesn't match schema
    """
    try:
        validate(data, schema_name)
    except ValidationError as e:
        raise ValidationError(
            schema_name,
            f"Refusing to write invalid data to {filepath}: {e}"
        ) from None
