"""Value transformation execution for mapped columns."""

import json
import logging
import threading
from datetime import datetime
from typing import Any

from RestrictedPython import compile_restricted, safe_globals
from RestrictedPython.Eval import default_guarded_getiter
from RestrictedPython.Guards import guarded_iter_unpack_sequence

from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import ValueConversion

logger = logging.getLogger(__name__)

# Seconds before a custom expression execution is killed.
_EXEC_TIMEOUT_S = 5


class TransformError(Exception):
    """Raised when a value transformation fails."""


class ValueTransformer:
    """Applies a ValueConversion rule to a single raw string value.

    Known types are handled deterministically. The ``'custom'`` type executes
    LLM-generated code inside a RestrictedPython sandbox with a timeout.
    """

    def transform_combined(self, values: dict[str, str], conversion: ValueConversion) -> str:
        """Apply a custom expression to a dict of column values.

        Used when ColumnMapping.combine_columns is non-empty. The expression
        receives a ``values`` dict keyed by column name rather than a single
        ``value`` string. Always runs through the sandbox.
        """
        if not conversion.expression:
            raise TransformError("combine transform requires a non-null expression")
        return self._custom_combined(values, conversion.expression)

    def transform(self, value: str, conversion: ValueConversion) -> str:
        """Return the transformed value, or raise TransformError on failure."""
        t = conversion.type.lower()
        if t == "none" or conversion.expression is None:
            return value
        if t == "date_format":
            return self._date_format(value, conversion.expression)
        if t == "unit":
            return self._unit(value, conversion.expression)
        if t == "split":
            return self._split(value, conversion.expression)
        if t == "enum_map":
            return self._enum_map(value, conversion.expression)
        if t == "custom":
            return self._custom(value, conversion.expression)
        # Unknown but non-custom type — attempt custom path as best effort.
        logger.warning("Unknown conversion type %r; attempting custom execution.", conversion.type)
        return self._custom(value, conversion.expression)

    def validate_preview(self, conversion: ValueConversion) -> list[dict[str, str | object]]:
        """Run the transformer against the agent-supplied preview pairs.

        Returns a list of result dicts with 'input', 'expected', 'actual', and
        'match' keys. An empty list means there were no preview pairs to check.
        """
        results = []
        for pair in conversion.preview:
            raw = pair.get("input", "")
            expected = pair.get("output", "")
            try:
                actual = self.transform(raw, conversion)
                results.append(
                    {
                        "input": raw,
                        "expected": expected,
                        "actual": actual,
                        "match": actual == expected,
                    }
                )
            except TransformError as exc:
                results.append(
                    {
                        "input": raw,
                        "expected": expected,
                        "actual": None,
                        "error": str(exc),
                        "match": False,
                    }
                )
        return results

    def _date_format(self, value: str, expression: str) -> str:
        """Parse with the given strptime format and return ISO 8601."""
        try:
            dt = datetime.strptime(value.strip(), expression)
            return dt.date().isoformat()
        except ValueError as exc:
            raise TransformError(
                f"date_format: could not parse {value!r} with format {expression!r}: {exc}"
            ) from exc

    def _unit(self, value: str, expression: str) -> str:
        """Apply a numeric scale factor.

        expression should be a float-parseable string, e.g. '0.3048' for ft→m.
        The raw value may include a unit label; only the leading numeric part is scaled.
        """
        try:
            numeric_str = value.strip().split()[0]
            scaled = float(numeric_str) * float(expression)
            # Preserve reasonable precision without scientific notation.
            return f"{scaled:.6g}"
        except (ValueError, IndexError) as exc:
            raise TransformError(
                f"unit: could not apply scale {expression!r} to {value!r}: {exc}"
            ) from exc

    def _split(self, value: str, expression: str) -> str:
        """Split on expression and return all parts joined by '; '.

        expression is the delimiter string, e.g. ',' or ' | '.
        """
        try:
            parts = [p.strip() for p in value.split(expression) if p.strip()]
            return "; ".join(parts)
        except Exception as exc:
            raise TransformError(
                f"split: could not split {value!r} on {expression!r}: {exc}"
            ) from exc

    def _enum_map(self, value: str, expression: str) -> str:
        """Map a source value to a canonical NMDC permissible value.

        expression must be a JSON object whose keys are source values and values
        are the target permissible values. Unrecognised source values raise TransformError
        so the caller can record the problem without silently writing a bad value.
        """
        try:
            mapping = json.loads(expression)
        except json.JSONDecodeError as exc:
            raise TransformError(
                f"enum_map: expression is not valid JSON: {expression!r}: {exc}"
            ) from exc
        if not isinstance(mapping, dict):
            raise TransformError(
                f"enum_map: expression must be a JSON object, got {type(mapping).__name__}"
            )
        if value not in mapping:
            known = list(mapping.keys())
            raise TransformError(
                f"enum_map: source value {value!r} has no mapping; known keys: {known!r}"
            )
        return str(mapping[value])

    def _custom(self, value: str, expression: str) -> str:
        """Execute LLM-generated expression inside a RestrictedPython sandbox.

        The expression is a Python expression string where ``value`` is bound
        to the input. It runs in a thread with a hard timeout.
        """
        result_container: dict[str, Any] = {}

        def _run() -> None:
            try:
                # RestrictedPython forbids identifiers starting with '_', so use
                # plain names for the wrapper function and result variable.
                source = (
                    f"def transform(value):\n"
                    f"    return {expression}\n"
                    f"result = transform(input_value)"
                )
                code = compile_restricted(source, filename="<custom_transform>", mode="exec")
                globs = {
                    **safe_globals,
                    "input_value": value,
                    # Required by RestrictedPython for iteration and subscript access.
                    "_getiter_": default_guarded_getiter,
                    "_getitem_": lambda obj, key: obj[key],
                    "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
                }
                globs["__builtins__"] = safe_globals["__builtins__"]
                exec(code, globs)  # noqa: S102
                result_container["value"] = globs["result"]
            except Exception as exc:  # noqa: BLE001
                result_container["error"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=_EXEC_TIMEOUT_S)

        if thread.is_alive():
            raise TransformError(
                f"custom transform timed out after {_EXEC_TIMEOUT_S}s for value {value!r}"
            )
        if "error" in result_container:
            raise TransformError(
                f"custom transform failed for value {value!r}: {result_container['error']}"
            )
        if "value" not in result_container:
            raise TransformError(f"custom transform produced no result for value {value!r}")

        raw_result = result_container["value"]
        if not isinstance(raw_result, str):
            return str(raw_result)
        return raw_result

    def _custom_combined(self, values: dict[str, str], expression: str) -> str:
        """Execute a combine expression with a 'values' dict in the sandbox."""
        result_container: dict[str, Any] = {}

        def _run() -> None:
            try:
                source = (
                    f"def transform(values):\n"
                    f"    return {expression}\n"
                    f"result = transform(input_values)"
                )
                code = compile_restricted(source, filename="<combine_transform>", mode="exec")
                globs = {
                    **safe_globals,
                    "input_values": values,
                    "_getiter_": default_guarded_getiter,
                    "_getitem_": lambda obj, key: obj[key],
                    "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
                }
                globs["__builtins__"] = safe_globals["__builtins__"]
                exec(code, globs)  # noqa: S102
                result_container["value"] = globs["result"]
            except Exception as exc:  # noqa: BLE001
                result_container["error"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=_EXEC_TIMEOUT_S)

        if thread.is_alive():
            raise TransformError(
                f"combine transform timed out after {_EXEC_TIMEOUT_S}s for values {values!r}"
            )
        if "error" in result_container:
            raise TransformError(
                f"combine transform failed for values {values!r}: {result_container['error']}"
            )
        if "value" not in result_container:
            raise TransformError(f"combine transform produced no result for values {values!r}")

        raw_result = result_container["value"]
        if not isinstance(raw_result, str):
            return str(raw_result)
        return raw_result
