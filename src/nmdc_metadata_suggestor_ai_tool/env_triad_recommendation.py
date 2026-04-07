from typing import Any

from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
from nmdc_metadata_suggestor_ai_tool.recommendation_pipeline import LLMOutput
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder
from nmdc_metadata_suggestor_ai_tool.system_prompt import env_triad_prompt
from nmdc_metadata_suggestor_ai_tool.utils.utils import clean_and_validate_output


def get_env_triad_recommendation(
    context: list[Any],
    llm_client: LLMClient,
    interface_names: list[str] = None,
    max_tokens: int | None = None,
) -> LLMOutput:
    """Get the recommended environment triad metadata fields for a submission and LLM client.

    Parameters:
        context: Contextual information for the LLM to generate recommendations.
        llm_client: LLMClient instance used for model interaction and configuration.
        interface_names: Optional list of specific interface names to focus on for schema context.
            If None, defaults to all interfaces.
        max_tokens: Optional maximum number of output response tokens.
    Returns:
        The response from the LLM containing the recommended environment triad metadata fields.
    """
    # set system proimpt in conversation manager to env triad filling
    conversation_manager = ConversationManager(
        llm_client=llm_client, system_prompt=env_triad_prompt
    )
    # add schema context
    builder = SchemaContextBuilder()
    mixs_schema = builder.format_multi_interface_context(
        interface_names or builder.list_interfaces()
    )
    conversation_manager.add_schema_context(mixs_schema)
    # send in context to llm to generate
    for message in context:
        conversation_manager.add_message(text=message)
    # parse back recommendaations to the expected output format
    raw_output = conversation_manager.generate(max_tokens=max_tokens)
    # clean and pydantically validate the output
    cleaned_output = clean_and_validate_output(raw_output)

    return cleaned_output
