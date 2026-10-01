"""Turn a supplement-retrieval result into LLM context messages.

The retriever hands back files in two shapes: text-like files (csv/tsv/txt)
arrive inlined as ``text``, and everything else (xlsx/pdf/docx) is written to
disk. Only the first shape can go straight into a conversation, so this module
builds one message per inlined file and a short inventory of what else was
retrieved, so the model knows which supplements it is and is not seeing.
"""

from nmdc_metadata_suggestor_ai_tool.models.supplement import (
    SupplementFile,
    SupplementRetrievalResult,
)


def describe_supplement(file: SupplementFile) -> str:
    """One-line label for a supplement: filename, origin, kind, and caption."""
    label = f"Supplementary file {file.filename}"
    details = [part for part in (file.source, file.kind.value) if part]
    if details:
        label += f" [{', '.join(details)}]"
    if file.caption:
        label += f": {file.caption}"
    return label


def format_supplement_context(
    result: SupplementRetrievalResult, *, max_chars: int | None = None
) -> list[str]:
    """Return LLM messages carrying the text-like supplements kept in *result*.

    The first message is an inventory of every kept file, marking which ones are
    included below and which were saved to disk and need a reader of their own.
    Each following message is one inlined file, headed by its label so the
    model can cite it by name. ``max_chars`` truncates each file's text; the
    retriever already caps it at ``SUPPLEMENT_MAX_TEXT_CHARS``.

    Returns an empty list when nothing was inlined, so callers can extend a
    context list without checking first.
    """
    inlined = [file for file in result.files if file.text]
    if not inlined:
        return []

    inventory = [f"Supplementary materials retrieved for DOI {result.doi}:"]
    for file in result.files:
        status = "included below" if file.text else "retrieved but not shown"
        inventory.append(f"- {describe_supplement(file)} ({status})")
    messages = ["\n".join(inventory)]

    for file in inlined:
        text = file.text or ""
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars] + "\n[truncated]"
        messages.append(f"{describe_supplement(file)}\n{text}")
    return messages
