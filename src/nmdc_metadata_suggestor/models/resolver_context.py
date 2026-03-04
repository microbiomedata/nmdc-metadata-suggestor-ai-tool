"""Shared resolver context return type for DOI provider resolvers."""

from typing import NamedTuple


class ResolverContext(NamedTuple):
    """Normalized resolver context payload."""

    text: str
    raw_text: str
    kind: str
    source: str | None = None
    urls: list[str] | None = None
    # Used when the publication DOI differs from the requested DOI
    # (for example, when referring to a supplemental DOI) to avoid
    # confusion with the main DOI of the context.
    publication_dois: list[str] | None = None
