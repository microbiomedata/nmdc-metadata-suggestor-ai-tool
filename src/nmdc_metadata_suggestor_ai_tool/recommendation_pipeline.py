import logging

from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder
from nmdc_metadata_suggestor_ai_tool.system_prompt import system_prompt
from nmdc_metadata_suggestor_ai_tool.utils.build_submission_context import build_submission_context
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import get_submission_fields
from nmdc_metadata_suggestor_ai_tool.utils.utils import validate_output

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
    # adds info to the conversation object
    build_submission_context(
        conversation_manager=conversation_manager, parsed_submission_object=parsed_submission_object
    )
    mixs_extensions = parsed_submission_object.get("mixs_extensions", [])

    builder = SchemaContextBuilder()
    mixs_schema = builder.format_multi_interface_context(mixs_extensions)
    conversation_manager.add_schema_context(mixs_schema)

    # get the LLM's response and validate it against the expected output schema
    response = conversation_manager.generate(max_tokens=max_tokens)
    validated_output = validate_output(response)

    # add model metadata to the output for tracking purposes
    validated_output.model = llm_client.model
    validated_output.access_provider = llm_client.access_provider
    return validated_output
