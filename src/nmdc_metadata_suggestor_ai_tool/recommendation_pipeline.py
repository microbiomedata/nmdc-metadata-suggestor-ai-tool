import logging

from nmdc_metadata_suggestor_ai_tool.doi_ingestion.main import get_doi_description_or_abstract
from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import (
    download_pdf_to_tempfile,
    remove_temp_file,
)
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder
from nmdc_metadata_suggestor_ai_tool.system_prompt import system_prompt
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import get_submission_fields
from nmdc_metadata_suggestor_ai_tool.utils.utils import clean_and_validate_output

logger = logging.getLogger(__name__)


def run_recommendation_pipeline(
    submission_object: dict,
    llm_client: LLMClient,
    max_tokens: int | None = None,
) -> LLMOutput:
    """Run the metadata recommendation pipeline for a submission and LLM client.

    Parameters:
        submission_object: Raw submission object containing NMDC metadata fields.
        llm_client: LLMClient instance used for model interaction.
        max_tokens: Optional maximum number of output response tokens.

    Returns:
        The response from the LLM containing the recommended metadata fields.
    """
    conversation_manager = ConversationManager(
        llm_client=llm_client, system_prompt=system_prompt
    )  # initialize the conversation manager with the LLM client
    parsed_submission_object = get_submission_fields(submission_object=submission_object)
    mixs_extensions = parsed_submission_object.get("mixs_extensions", [])
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

    builder = SchemaContextBuilder()
    mixs_schema = builder.format_multi_interface_context(mixs_extensions)
    conversation_manager.add_schema_context(mixs_schema)
    conversation_manager.add_message(
        text="Utilize the provided information and PDF content to "
        "inform your metadata field recommendations.",
        pdf_files=pdf_files,
    )

    # get the LLM's response and validate it against the expected output schema
    response = conversation_manager.generate(max_tokens=max_tokens)
    validated_output = clean_and_validate_output(response)

    # delete the temporary PDF files after processing
    if pdf_files:
        for pdf in pdf_files:
            remove_temp_file(pdf)

    # add model metadata to the output for tracking purposes
    validated_output.model = llm_client.model
    validated_output.access_provider = llm_client.access_provider
    return validated_output
