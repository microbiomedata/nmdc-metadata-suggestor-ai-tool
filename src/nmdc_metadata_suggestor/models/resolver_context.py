"""Shared resolver context return type for DOI provider resolvers."""

from typing import NamedTuple


class ResolverContext(NamedTuple):
    """Normalized resolver context payload."""

    text: str
    raw_text: str
    kind: str
    source: str | None = None
    urls: list[str] | None = None
    # field used in the instance the publication doi is different
    # than the requested doi (e.g. for a supplemental doi),
    # to avoid confusion with the main DOI of the context
    supplemental_doi: str | None = None
