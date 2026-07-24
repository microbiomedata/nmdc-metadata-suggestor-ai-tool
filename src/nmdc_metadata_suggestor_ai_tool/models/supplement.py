"""Models for supplementary-material retrieval.

Supplements are retrieved to give the NMDC metadata suggestor extra context for
characterizing submissions and samples. Not all supplement types are equally
useful for that goal, so files are classified by :class:`SupplementKind` and the
retrieval helpers prioritize the kinds that carry sample/environmental metadata.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class SupplementKind(StrEnum):
    """Coarse classification of a supplement file by its likely usefulness for
    NMDC metadata suggestion.

    ``TABULAR`` and ``DOCUMENT`` are the high-value kinds: tables commonly carry
    per-sample metadata and documents carry supplementary methods/site
    descriptions. The remaining kinds are usually not helpful for value-filling
    and are skipped by default.
    """

    TABULAR = "tabular"  # csv, tsv, xlsx, xls — sample/measurement metadata tables
    DOCUMENT = "document"  # docx, pdf, txt, rtf — supplementary methods/text
    IMAGE = "image"  # figures, gels, micrographs
    MEDIA = "media"  # audio/video
    SEQUENCE = "sequence"  # fasta/fastq/bam and other large omics data
    ARCHIVE = "archive"  # nested zip/tar/gz bundles
    OTHER = "other"  # unrecognized / uncategorized


class SupplementFile(BaseModel):
    """A single supplement file discovered for a publication.

    Byte content is not stored on the model. Text-like files (csv/tsv/txt) are
    decoded into ``text`` up to a character cap; other kept files are written to
    a temp path exposed via ``saved_path``. ``skipped_reason`` is set (and the
    other content fields left empty) when a file was intentionally not kept.
    """

    filename: str
    kind: SupplementKind
    source: str | None = Field(
        default=None,
        description="Which source/repository the file came from "
        "(e.g. 'europepmc', 'pmc_oa', 'dryad', 'zenodo', 'figshare').",
    )
    size_bytes: int | None = None
    caption: str | None = Field(
        default=None,
        description="Caption/label from the article's JATS XML or repository "
        "metadata, when available. Helps judge relevance beyond the extension.",
    )
    text: str | None = Field(
        default=None,
        description="Decoded text for text-like supplements, truncated to a cap.",
    )
    saved_path: str | None = Field(
        default=None,
        description="Local temp-file path for non-text supplements that were kept.",
    )
    skipped_reason: str | None = Field(
        default=None,
        description="Why the file was not kept (low-value kind, too large, cap reached).",
    )


class SupplementRetrievalResult(BaseModel):
    """Result of a supplement-retrieval attempt for a single DOI."""

    doi: str
    source: str | None = None  # e.g. "europepmc"
    pmcid: str | None = None
    files: list[SupplementFile] = Field(
        default_factory=list,
        description="Supplements that were kept (high-value kinds within caps).",
    )
    skipped: list[SupplementFile] = Field(
        default_factory=list,
        description="Supplements that were found but intentionally not kept.",
    )
    attempts: list[str] = Field(default_factory=list)
    detected_accessions: list[str] = Field(
        default_factory=list,
        description="Repository/sequence accessions found while mining publication "
        "text (e.g. PRJNA…, SRR…, GSE…). Surfaced for awareness; not retrieved.",
    )
    error: str | None = None

    @property
    def has_supplements(self) -> bool:
        """True when at least one supplement was kept."""
        return len(self.files) > 0
