from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient, ConversationManager
from nmdc_metadata_suggestor_ai_tool.recommendation_pipeline import LLMOutput
from typing import Any
from nmdc_metadata_suggestor_ai_tool.system_prompt import env_triad_prompt

def get_env_triad_recommendation(
    context: list[Any],
    llm_client: LLMClient,
    max_tokens: int | None = None,
) -> LLMOutput:
    """Get the recommended environment triad metadata fields for a submission and LLM client.

    Parameters:
        context: Contextual information for the LLM to generate recommendations.
        llm_client: LLMClient instance used for model interaction and configuration.
        max_tokens: Optional maximum number of output response tokens.
    Returns:
        The response from the LLM containing the recommended environment triad metadata fields.
    """
    # set system proimpt in conversation manager to env triad filling
    conversation_manager= ConversationManager(llm_client=llm_client, system_prompt=env_triad_prompt)
    # add schema context
    
    # send in context to llm to generate
    for message in context:
        conversation_manager.add_message(text=message)
    # parse back recommendaations to the expected output format
    raw_output = conversation_manager.generate(max_tokens=max_tokens)
    return 