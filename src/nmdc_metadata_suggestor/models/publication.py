"""Publication model for DOI-resolved metadata."""

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
            "See DoiCategory enum in models/doi.py."
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
            "DOI of an associated publication "
            "(e.g. the journal article linked from a dataset record)."
        ),
    )
    pmid: str | None = Field(None, description="PubMed ID")
    urls: list[HttpUrl] | None = Field(None, description="URLs to the publication or resource")
    abstract: str | None = Field(None, description="Publication abstract text")
    error: str | None = Field(None, description="Error message if retrieval failed")
