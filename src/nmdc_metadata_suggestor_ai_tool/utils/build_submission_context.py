import logging

from nmdc_metadata_suggestor_ai_tool.doi_ingestion.main import get_doi_description_or_abstract
from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import (
    download_pdf_to_tempfile,
    remove_temp_file,
)

logger = logging.getLogger(__name__)


def build_submission_context(
    conversation_manager: ConversationManager, parsed_submission_object: dict
) -> None:
    """
    Build the submission context for the LLM conversation.

    Parameters:
        conversation_manager: The ConversationManager instance to add context to.
        parsed_submission_object: The parsed submission object containing metadata fields.

    Returns:
        None
    """
    doi = parsed_submission_object.get("dois", [])
    description = parsed_submission_object.get("description", "")
    notes = parsed_submission_object.get("notes", "")
    study_name = parsed_submission_object.get("study_name", "")
    protocol_descs = parsed_submission_object.get("protocol_descs", [])
    protocol_names = parsed_submission_object.get("protocol_names", [])

    # TODO: do not yet have jgi or gold id look up - only by DOI, so skipping for now
    # gold_study_id = parsed_submission_object.get("gold_study_id", "")
    # jgi_study_id = parsed_submission_object.get("jgi_study_id", "")

    # add the submission context to the LLM
    submission_context = (
        f"Submission description: {description}\n"
        f"Notes: {notes}\n"
        f"Study name: {study_name}\n"
        f"Protocol descriptions: {'; '.join(protocol_descs)}\n"
        f"Protocol names: {'; '.join(protocol_names)}"
    )
    conversation_manager.add_message(text=submission_context)
    publication_links: list[str] = []
    # loop through dois and retrieve abstracts/descriptions to add to the LLM context
    for doi, provider in parsed_submission_object.get("dois", []):
        # based on the DOI, retrieve the abstract and PDF if available, and add to the LLM context
        result = get_doi_description_or_abstract(
            doi=doi, skip_classification=True, sources=provider
        )
        abstract = result.context if result.context else None
        if result.publication_urls:
            publication_links.extend(result.publication_urls)
        if abstract is not None:
            conversation_manager.add_message(
                text=f"Context retrieved for DOI {doi} from provider {provider}:\n{abstract}"
            )

    # collect pdf files if available, and add the abstract and PDF content to the LLM context
    pdf_files: list[str] | None
    if publication_links:
        pdf_files = []
        for url in publication_links:
            try:
                pdf_path = download_pdf_to_tempfile(str(url))
                pdf_files.append(pdf_path)
            except Exception:
                logger.exception(f"Error downloading PDF from {url}")
    else:
        pdf_files = None
    conversation_manager.add_message(
        text="Utilize the provided information and PDF content to "
        "inform your metadata field recommendations.",
        pdf_files=pdf_files,
    )
    if pdf_files:
        for pdf in pdf_files:
            remove_temp_file(pdf)
