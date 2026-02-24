"""Shared resolver context return type for DOI provider resolvers."""

from typing import NamedTuple


class ResolverContext(NamedTuple):
    """Normalized resolver context payload."""

    text: str
    raw_text: str
    kind: str

