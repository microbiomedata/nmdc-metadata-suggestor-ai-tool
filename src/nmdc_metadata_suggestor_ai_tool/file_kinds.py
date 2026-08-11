"""Reusable classification of files by extension.

Maps a filename to a coarse :class:`SupplementKind` category so any code that
reasons about file types — supplement retrieval today, other loaders later —
shares a single taxonomy instead of re-deriving its own extension lists.

The category enum itself lives with the models
(:class:`nmdc_metadata_suggestor_ai_tool.models.supplement.SupplementKind`);
this module owns the extension → category mapping and the helpers built on it.
"""

import os

from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementKind

# Extension (lowercase, no dot) -> kind. Extensions not listed classify as OTHER.
EXTENSION_KINDS: dict[str, SupplementKind] = {
    # Tabular: highest value -- typically per-sample metadata/measurements.
    "csv": SupplementKind.TABULAR,
    "tsv": SupplementKind.TABULAR,
    "tab": SupplementKind.TABULAR,
    "xlsx": SupplementKind.TABULAR,
    "xls": SupplementKind.TABULAR,
    "xlsm": SupplementKind.TABULAR,
    "ods": SupplementKind.TABULAR,
    # Documents: supplementary methods, site descriptions, etc.
    "pdf": SupplementKind.DOCUMENT,
    "docx": SupplementKind.DOCUMENT,
    "doc": SupplementKind.DOCUMENT,
    "rtf": SupplementKind.DOCUMENT,
    "txt": SupplementKind.DOCUMENT,
    "text": SupplementKind.DOCUMENT,
    "md": SupplementKind.DOCUMENT,
    # Images / media -- rarely useful for value-filling.
    "png": SupplementKind.IMAGE,
    "jpg": SupplementKind.IMAGE,
    "jpeg": SupplementKind.IMAGE,
    "gif": SupplementKind.IMAGE,
    "tif": SupplementKind.IMAGE,
    "tiff": SupplementKind.IMAGE,
    "bmp": SupplementKind.IMAGE,
    "eps": SupplementKind.IMAGE,
    "svg": SupplementKind.IMAGE,
    "mov": SupplementKind.MEDIA,
    "mp4": SupplementKind.MEDIA,
    "avi": SupplementKind.MEDIA,
    "wmv": SupplementKind.MEDIA,
    "mp3": SupplementKind.MEDIA,
    "wav": SupplementKind.MEDIA,
    # Sequence / large omics data -- out of scope for metadata suggestion.
    "fasta": SupplementKind.SEQUENCE,
    "fa": SupplementKind.SEQUENCE,
    "fastq": SupplementKind.SEQUENCE,
    "fq": SupplementKind.SEQUENCE,
    "sam": SupplementKind.SEQUENCE,
    "bam": SupplementKind.SEQUENCE,
    "vcf": SupplementKind.SEQUENCE,
    "gb": SupplementKind.SEQUENCE,
    "gbk": SupplementKind.SEQUENCE,
    # Nested archives.
    "zip": SupplementKind.ARCHIVE,
    "gz": SupplementKind.ARCHIVE,
    "tar": SupplementKind.ARCHIVE,
    "tgz": SupplementKind.ARCHIVE,
    "7z": SupplementKind.ARCHIVE,
    "rar": SupplementKind.ARCHIVE,
}

# Extensions whose content is decodable as plain text and worth inlining directly.
TEXT_LIKE_EXTENSIONS: frozenset[str] = frozenset({"csv", "tsv", "tab", "txt", "text", "md"})

# High-value kinds most callers keep by default.
DEFAULT_USEFUL_KINDS: frozenset[SupplementKind] = frozenset(
    {SupplementKind.TABULAR, SupplementKind.DOCUMENT}
)


def file_extension(filename: str) -> str:
    """Return the lowercase extension of *filename* without the dot (``""`` if none)."""
    base = os.path.basename(filename)
    _, _, ext = base.rpartition(".")
    return ext.lower() if "." in base else ""


def classify_file(filename: str) -> SupplementKind:
    """Classify a file by its extension.

    Args:
        filename: The file's name or path (extension is all that matters).

    Returns:
        The :class:`SupplementKind` for the file, or ``OTHER`` if unrecognized.
    """
    return EXTENSION_KINDS.get(file_extension(filename), SupplementKind.OTHER)
