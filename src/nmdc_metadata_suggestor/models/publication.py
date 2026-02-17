"""Publication model for DOI-resolved metadata.

Merge note (for branch 1597-publisher-api-abstract-text):
    This refines the Publication model from Olivia's branch
    (1599-create-api-access-to-the-osti-api).  Changes from her version:

    1. ``osti_doi`` renamed to ``doi`` — not all DOIs come from OSTI; the
       field now holds any DOI regardless of registration agency.
    2. Added ``doi_category`` — carries the NMDC DoiCategoryEnum value
       (publication_doi, dataset_doi, award_doi, data_management_plan_doi)
       so downstream code knows what kind of resource the DOI points to.
    3. Added ``registration_agency`` — Crossref, DataCite, or other; useful
       for routing to the correct fetching mechanism.
    4. Modernized type annotations — ``str | None`` instead of
       ``Optional[str]``, ``list[HttpUrl]`` instead of ``List[HttpUrl]``,
       per the repo's py312 target and ruff UP rules.
    5. ``publication_doi`` field renamed to ``associated_publication_doi`` to
       avoid confusion with the doi_category value ``"publication_doi"``.

    To merge with Olivia's branch, her callers that set ``osti_doi`` should
    set ``doi`` instead, and her callers that set ``publication_doi`` should
    set ``associated_publication_doi``.  All other fields are additive.
"""

from pydantic import BaseModel, Field, HttpUrl


class Publication(BaseModel):
    """Metadata for a publication or other DOI-resolved resource.

    This model holds the result of resolving a DOI: what it is, where it
    came from, and what content was fetched.  It is intentionally broader
    than OSTI-specific metadata so it works for any registration agency.
    """

    doi: str | None = Field(None, description="Bare DOI string (e.g. 10.1038/s41564-020-00861-0)")
    doi_category: str | None = Field(
        None,
        description=(
            "NMDC DoiCategoryEnum value: publication_doi, dataset_doi, "
            "award_doi, or data_management_plan_doi.  "
            "Source: nmdc-schema DoiCategoryEnum (4 permissible values)."
        ),
    )
    registration_agency: str | None = Field(
        None,
        description="DOI registration agency: Crossref, DataCite, mEDRA, etc.",
    )
    source: str | None = Field(
        None,
        description="Source from which metadata/PDF was retrieved (e.g. crossref, pubmed, osti)",
    )
    associated_publication_doi: str | None = Field(
        None,
        description=(
            "DOI of an associated publication (e.g. the journal article "
            "linked from an OSTI dataset record).  Renamed from "
            "``publication_doi`` in Olivia's model to avoid collision with "
            "the doi_category value."
        ),
    )
    pmid: str | None = Field(None, description="PubMed ID")
    urls: list[HttpUrl] | None = Field(None, description="URLs to the publication or resource")
    abstract: str | None = Field(None, description="Publication abstract text")
