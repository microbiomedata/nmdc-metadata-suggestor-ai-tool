import json
import re

from pydantic import ValidationError

from nmdc_metadata_suggestor_ai_tool.doi_ingestion.main import get_doi_description_or_abstract
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import (
    download_pdf_to_tempfile,
    remove_temp_file,
)
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import get_submission_fields


def clean_and_validate_output(raw_output: str) -> LLMOutput:
    """Clean the raw output from the LLM and validate it against the expected schema"""
    print(f"Raw LLM output: {raw_output}")
    cleaned_response = re.sub(
        r"^```(?:json)?\s*\n?|\n?```$",
        "",
        raw_output.strip(),
        flags=re.MULTILINE,
    ).strip()
    print(f"Cleaned LLM response: {cleaned_response}")
    try:
        parsed_response = json.loads(cleaned_response)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM response was not valid JSON: {exc}\n"
            f"Raw output from LLM: {raw_output}\n"
            f"Cleaned response: {cleaned_response}"
        ) from exc

    if not isinstance(parsed_response, dict):
        raise ValueError("LLM response JSON must be an object with top-level keys.")

    try:
        validated_output = LLMOutput.model_validate(parsed_response)
    except ValidationError as exc:
        raise ValueError(f"LLM response JSON did not match expected output schema: {exc}") from exc

    return validated_output


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
    parsed_submission_object = get_submission_fields(submission_object=submission_object)
    mixis_extensions = parsed_submission_object.get("mixis_extensions", [])
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
    llm_client.add_message(text=submission_context)
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
            llm_client.add_message(
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
            except Exception as e:
                print(f"Error downloading PDF from {url}: {e}")
    else:
        pdf_files = None

    builder = SchemaContextBuilder()
    mixis_schema = builder.format_multi_interface_context(mixis_extensions)
    llm_client.add_schema_context(mixis_schema)
    llm_client.add_message(
        text="Utilize the provided information and PDF content to "
        "inform your metadata field recommendations.",
        pdf_files=pdf_files,
    )

    # get the LLM's response and validate it against the expected output schema
    response = llm_client.generate(max_tokens=max_tokens)
    validated_output = clean_and_validate_output(response)

    # delete the temporary PDF files after processing
    if pdf_files:
        for pdf in pdf_files:
            remove_temp_file(pdf)

    # add model metadata to the output for tracking purposes
    validated_output.model = llm_client.model
    validated_output.access_provider = llm_client.access_provider
    return validated_output
