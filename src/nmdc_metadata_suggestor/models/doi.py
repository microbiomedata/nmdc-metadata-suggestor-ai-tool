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


class AbstractResult(BaseModel):
    """Result of an abstract retrieval attempt.

    Attributes:
        doi: The normalized DOI that was looked up.
        abstract: The abstract as clean plain text (tags stripped, entities
            unescaped, inverted index reconstructed), or None if not found.
        raw_abstract: The abstract exactly as the API returned it, before any
            parsing or stripping. For OpenAlex this is the JSON-serialized
            inverted index; for Crossref/content negotiation it may contain
            JATS XML tags; for PubMed it equals ``abstract`` (already plain
            text). Use this when you need the original markup or structure.
        source: Which source provided the abstract
            (``"openalex"``, ``"crossref"``, ``"pubmed"``, ``"content_negotiation"``).
        content_format: The raw format of the response before parsing:

            - ``"inverted_index"`` — OpenAlex ``{word: [positions...]}``
              (reconstructed; may lose punctuation spacing)
            - ``"jats_xml"`` — JATS XML tags stripped (Crossref, content negotiation)
            - ``"plain_text"`` — already clean text (PubMed efetch, or Crossref/
              content negotiation when no XML tags were present)
            - ``"citeproc_json"`` — Citeproc JSON ``abstract`` field (content
              negotiation, when no JATS tags detected)
            - ``None`` — no abstract was found

        pmid: PubMed ID, populated only when PubMed was the source.
        attempts: Sources tried in order, for debugging.
        error: Human-readable error if the DOI was refused or all sources failed.
    """

    doi: str
    abstract: str | None = None
    raw_abstract: str | None = None
    source: str | None = None
    content_format: str | None = None
    pmid: str | None = None
    attempts: list[str] = []
    error: str | None = None


class DoiContextResult(BaseModel):
    """Result of DOI description/abstract retrieval for LLM context expansion.

    Attributes:
        doi: Normalized DOI that was looked up.
        context: Cleaned context text if found (abstract preferred over description).
        raw_context: Context text as returned by the source before cleanup.
        context_type: ``"abstract"`` or ``"description"`` when context is found.
        provider: Repository/provider inferred from DOI prefix or publisher metadata.
        source: API source that returned the context (e.g. ``"datacite"``, ``"zenodo"``).
        attempts: Sources tried in order.
        source_errors: Source-specific failure details captured during lookup.
        error: Human-readable error when no context could be retrieved.
    """

    doi: str
    context: str | None = None
    raw_context: str | None = None
    context_type: str | None = None
    provider: str | None = None
    source: str | None = None
    attempts: list[str] = []
    source_errors: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
