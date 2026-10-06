"""Helper functions that linkml-map transform specs call from their expressions.

linkml-map evaluates ``expr`` strings with a restricted evaluator that knows only a short
list of functions (``str``, ``float``, ``split``, ...) and has no ``strptime``. These helpers
fill that gap for the Metadata Mapper's conversion types. Each one delegates to
``ValueTransformer``, so a spec run and ``apply_mappings`` produce the same values, and
``custom`` expressions still run inside the RestrictedPython sandbox rather than in
linkml-map's evaluator.

A failed cell does not stop the row: the helper records the error in the active error
list (see ``collect_transform_errors``) and returns the raw value, matching how
``apply_mappings`` keeps the raw value under the slot key.

The functions are tagged with linkml-map's ``safe_function``, so this module can also be
passed to the CLI: ``linkml-map map-data ... --functions transform_functions.py``.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from linkml_map.utils.extensions import safe_function  # type: ignore[import-untyped]

from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transformer import (
    TransformError,
    ValueTransformer,
)

transformer = ValueTransformer()

# Errors raised while transforming the current row. None outside collect_transform_errors.
active_errors: ContextVar[list[str] | None] = ContextVar("active_errors", default=None)


@contextmanager
def collect_transform_errors() -> Iterator[list[str]]:
    """Collect per-cell errors raised by the helpers inside this block."""
    errors: list[str] = []
    token = active_errors.set(errors)
    try:
        yield errors
    finally:
        active_errors.reset(token)


def record_error(message: str) -> None:
    errors = active_errors.get()
    if errors is not None:
        errors.append(message)


def is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


@safe_function
def iso_date(value: Any, fmt: str) -> str | None:
    """Parse ``value`` with a strptime format and return an ISO 8601 date."""
    if is_blank(value):
        return None
    try:
        return transformer.date_format(str(value), fmt)
    except TransformError as exc:
        record_error(str(exc))
        return str(value)


@safe_function
def scale(value: Any, factor: str) -> str | None:
    """Multiply the leading number in ``value`` by ``factor``."""
    if is_blank(value):
        return None
    try:
        return transformer.unit(str(value), factor)
    except TransformError as exc:
        record_error(str(exc))
        return str(value)


@safe_function
def split_join(value: Any, delimiter: str) -> str | None:
    """Split ``value`` on ``delimiter`` and rejoin the parts with '; '."""
    if is_blank(value):
        return None
    try:
        return transformer.split(str(value), delimiter)
    except TransformError as exc:
        record_error(str(exc))
        return str(value)


@safe_function
def sandboxed(value: Any, expression: str) -> str | None:
    """Run a mapper ``custom`` expression on one value inside the RestrictedPython sandbox."""
    if is_blank(value):
        return None
    try:
        return transformer.custom(str(value), expression)
    except TransformError as exc:
        record_error(str(exc))
        return str(value)


@safe_function(distributes=False)
def sandboxed_combined(values: dict[str, Any], expression: str) -> str | None:
    """Run a mapper combine expression on a dict of column values inside the sandbox."""
    empty = [column for column, value in values.items() if is_blank(value)]
    if empty:
        record_error(f"combine columns have no value in this row: {empty}")
        return None
    try:
        return transformer.custom_combined({k: str(v) for k, v in values.items()}, expression)
    except TransformError as exc:
        record_error(str(exc))
        return str(values)


TRANSFORM_FUNCTIONS = {
    "iso_date": iso_date,
    "scale": scale,
    "split_join": split_join,
    "sandboxed": sandboxed,
    "sandboxed_combined": sandboxed_combined,
}
