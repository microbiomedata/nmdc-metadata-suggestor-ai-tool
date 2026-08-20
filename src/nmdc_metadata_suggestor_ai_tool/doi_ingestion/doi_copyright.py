"""DOI copyright / license check for AI inference-time context use.

Implements the decision logic from ``.claude/skills/doi-copyright-check/SKILL.md``.
Determines whether a publication's content may be passed to an LLM as inference
context (NOT training).  Returns a structured verdict so callers can include or
exclude content automatically.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Verdict type
# ---------------------------------------------------------------------------

Verdict = Literal["allowed", "not_allowed", "uncertain"]


class CopyrightCheckResult(BaseModel):
    """Verdict for one DOI's AI inference-context usability."""

    doi: str
    license_found: str | None
    verdict: Verdict
    condition: str | None = None
    note: str


# ---------------------------------------------------------------------------
# License normalisation helpers
# ---------------------------------------------------------------------------

# Regex to parse Creative Commons URL paths, e.g.
# https://creativecommons.org/licenses/by-nc/4.0/
_CC_URL_RE = re.compile(
    r"creativecommons\.org/licenses/([a-z-]+)/(\d+\.\d+)",
    re.IGNORECASE,
)

# Map normalised CC slugs to (verdict, condition)
_CC_SLUG_VERDICTS: dict[str, tuple[Verdict, str]] = {
    "by": ("allowed", "Attribute the original authors and source"),
    "by-sa": ("allowed", "Attribute; any derived work must carry the same license"),
    "by-nd": ("allowed", "Attribute; use verbatim only — do not modify or redistribute"),
    "by-nc": ("allowed", "Non-commercial use only — verify your use case is non-commercial"),
    "by-nc-sa": ("allowed", "Non-commercial use only; derived works must carry the same license"),
    "by-nc-nd": ("allowed", "Non-commercial use only; use verbatim only — do not modify or redistribute"),
}

# Patterns that indicate a CC license string (non-URL form)
_CC_STRING_RE = re.compile(
    r"cc[-\s]?(by(?:[-\s](?:nc|sa|nd))*(?:[-\s](?:nc|sa|nd))*)\s*(?:\d+\.\d+)?",
    re.IGNORECASE,
)

# CC0 / public domain
_CC0_RE = re.compile(r"cc0|public\s+domain|cc\s+zero|publicdomain/zero", re.IGNORECASE)


def _normalise_license(raw: str) -> str | None:
    """Return a normalised CC slug (e.g. ``"by"``, ``"by-nc"``) or ``None``.

    Handles both URL form (``https://creativecommons.org/licenses/by/4.0/``)
    and plain-text forms (``"CC BY 4.0"``, ``"CC-BY-NC"``).
    """
    if _CC0_RE.search(raw):
        return "cc0"

    # URL form
    url_match = _CC_URL_RE.search(raw)
    if url_match:
        return url_match.group(1).lower()

    # Plain-text form — strip leading "cc" / "cc-" / "cc by" etc.
    text_match = _CC_STRING_RE.search(raw)
    if text_match:
        slug = re.sub(r"[\s]+", "-", text_match.group(1).strip()).lower()
        return slug

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_doi_copyright(
    doi: str,
    license_string: str | None,
    *,
    context_text: str | None = None,
) -> CopyrightCheckResult:
    """Check whether a DOI's content may be used as LLM inference context.

    Parameters
    ----------
    doi:
        The DOI being checked.
    license_string:
        The license field returned by the DOI source (DataCite, CrossRef,
        OpenAlex).  May be a URL or a plain string.  Pass ``None`` if the
        metadata contained no license field.
    context_text:
        Optional abstract / description text.  When ``license_string`` is
        absent the function searches ``context_text`` for an embedded CC
        notice (publishers often include one in the abstract).

    Returns
    -------
    CopyrightCheckResult
    """
    candidates: list[str] = []
    if license_string:
        candidates.append(license_string)
    if context_text:
        candidates.append(context_text)

    for candidate in candidates:
        slug = _normalise_license(candidate)
        if slug is None:
            continue

        if slug == "cc0":
            return CopyrightCheckResult(
                doi=doi,
                license_found=candidate,
                verdict="allowed",
                condition="No attribution required",
                note="CC0 / Public Domain — unrestricted inference-context use.",
            )

        if slug in _CC_SLUG_VERDICTS:
            verdict, condition = _CC_SLUG_VERDICTS[slug]
            nc = "nc" in slug
            nd = "nd" in slug
            if nc:
                note = (
                    f"License {slug.upper()} is non-commercial. "
                    "Allowed for non-commercial use only; "
                    "verify your use case is non-commercial before including."
                )
            elif nd:
                note = (
                    f"License {slug.upper()} prohibits derivatives. "
                    "Passing content verbatim as LLM context is generally non-derivative, "
                    "but this is an interpretive call — flag if content will be paraphrased."
                )
            else:
                note = f"License {slug.upper()} permits inference-context use with attribution."
            return CopyrightCheckResult(
                doi=doi,
                license_found=candidate,
                verdict=verdict,
                condition=condition,
                note=note,
            )

    # No CC license found
    if candidates:
        return CopyrightCheckResult(
            doi=doi,
            license_found=license_string,
            verdict="not_allowed",
            condition=None,
            note=(
                "No Creative Commons license detected. "
                "Cannot use content without explicit publisher permission."
            ),
        )

    # No license information at all
    return CopyrightCheckResult(
        doi=doi,
        license_found=None,
        verdict="uncertain",
        condition=None,
        note="License not found in metadata or abstract text. Flag for manual review.",
    )
