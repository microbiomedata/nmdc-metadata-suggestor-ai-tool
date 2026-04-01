def get_env_triad_recommendation(
    context,
    llm_client: LLMClient,
    max_tokens: int | None = None,
) -> LLMOutput:
    """Get the recommended environment triad metadata fields for a submission and LLM client.

    Parameters:
        context: Contextual information for the LLM to generate recommendations.
        llm_client: LLMClient instance used for model interaction.
        max_tokens: Optional maximum number of output response tokens.
    Returns:
        The response from the LLM containing the recommended environment triad metadata fields.
    """
    # set system proimpt in llm client to env triad filling
    # sending in context to llm to generate
    # parse back recommendaations to the expected output format
    raw_output = llm_client.get_response(prompt=prompt, max_tokens=max_tokens)
    return parse_llm_response(raw_output)