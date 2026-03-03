"""DOI-related models and enums.

Data classes for DOI validation, classification, and abstract retrieval.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class DoiCategory(StrEnum):
    """NMDC DOI category.

    Mirrors nmdc-schema ``DoiCategoryEnum`` (``basic_slots.yaml``).
    Ref: https://microbiomedata.github.io/nmdc-schema/DoiCategoryEnum/

    If nmdc-schema is added as a dependency in the future, replace this
    with a direct import.
    """

    PUBLICATION = "publication_doi"
    DATASET = "dataset_doi"
    AWARD = "award_doi"
    DATA_MANAGEMENT_PLAN = "data_management_plan_doi"


class DoiValidation(BaseModel):
    """Result of DOI validation via the Handle API."""

    doi: str
    is_valid: bool
    handle_response_code: int | None = None
    error: str | None = None


class DoiClassification(BaseModel):
    """Classification of a DOI along multiple axes.

    Axes (see ``docs/doi-classification-design.md``):
      1. Resource Type — what does the DOI point to?
      2. Registration Agency — Crossref or DataCite
      3. Publisher/Source — who publishes the content?

    Plus: inferred NMDC DoiCategoryEnum value.
    """

    doi: str
    is_valid: bool
    registration_agency: str | None = None
    resource_type: str | None = None
    resource_type_general: str | None = None
    publisher: str | None = None
    prefix: str | None = None
    inferred_nmdc_category: str | None = None
    error: str | None = None


class SourceRetrievalResult(BaseModel):
    """Unified result model for DOI abstract and context retrieval workflows.

    This combines fields used by publication abstract retrieval and DOI
    context retrieval so both paths can share a single result type.
    """

    doi: str
    source: str | None = None
    provider: str | None = None
    attempts: list[str] = Field(default_factory=list)
    # DOI ingestion / Publication context fields (dataset/repository descriptions)
    context: str | None = None
    raw_context: str | None = None
    context_type: str | None = None

    source_errors: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    # publication related fields
    publication_urls: list[str] | None = Field(
        default=None, description="URLs to the publication or resource (txt, JATS, pdf)"
    )

    publication_dois: list[str] | None = Field(
        default=None,
        description="DOIs of the publication(s) associated with the context. "
        "ONLY FILL IF DIFFERENT FROM THE REQUESTED DOI.",
    )


class FullTextRetrievalResult(BaseModel):
    """Result of full text retrieval from Europe PMC or similar sources.

    Separate from ``SourceRetrievalResult`` because full text produces
    fundamentally different outputs: large JATS XML documents and binary
    PDF content, rather than short abstract strings.
    """

    doi: str
    pmcid: str | None = None
    is_open_access: bool | None = None
    source: str | None = None
    error: str | None = None
    attempts: list[str] = Field(default_factory=list)

    full_text_xml: str | None = None
    pdf_url: str | None = None
    pdf_bytes: bytes | None = Field(default=None, exclude=True, repr=False)
