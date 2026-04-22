from typing import Any

from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder
from nmdc_metadata_suggestor_ai_tool.system_prompt import env_triad_prompt
from nmdc_metadata_suggestor_ai_tool.utils.build_submission_context import build_submission_context
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import (
    MixsExtensions,
    get_submission_fields,
)
from nmdc_metadata_suggestor_ai_tool.utils.utils import clean_and_validate_output


def get_env_triad_recommendation(
    llm_client: LLMClient,
    samples: list[dict],
    study_context: list[Any] = [],
    submission_object: dict | None = None,
    interface_names: list[str] | None = None,
    max_tokens: int | None = None,
) -> LLMOutput:
    """Get the recommended environment triad metadata fields for a submission and LLM client.

    Example for ETL:
        llm_client = LLMClient(access_provider="gcp")
        # the study dict, maybe other useful context as well
        study_context = [{"nmdc-study-record": "value"}]
        # the biosamples
        samples = [{"nmdc-biosample-record": "value"}, {"nmdc-biosample-record": "value"}]
        # get the results
        result = get_env_triad_recommendation(
            study_context=study_context,
            samples=samples,
            llm_client=llm_client,
        )
        print(result)

    Example for submission server:
        llm_client = LLMClient(access_provider="gcp")
        # full object
        submission_object = {"fields":"yay"}
        # break out which page the user is on and send those samples
        samples = submission_object.get("metadata_submission").get("sampleData")["water_data"]
        # send the interface tab name as well
        interface_names = ["water"]
        result = get_env_triad_recommendation(
            submission_object=submission_object,
            samples=samples,
            llm_client=llm_client,
            interface_names=interface_names,
        )
        print(result)


    Parameters:
        study_context: Optional contextual information for the LLM to generate recommendations.
        samples: A list of sample records to generate the env triad for.
        submission_object: Optional submission object containing metadata fields.
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
    # if this is a submission, collect submission info
    # and add to the conversation using tools we already built
    if submission_object is not None:
        build_submission_context(
            conversation_manager=conversation_manager,
            parsed_submission_object=get_submission_fields(submission_object),
        )
    # add identifiers to samples if they don't already have one ie submission rows
    for idx, sample in enumerate(samples):
        if "id" not in sample:
            sample["id"] = idx
    if interface_names:
        interface_names = MixsExtensions.map_to_interface_name(interface_names)
    # add schema context - env triad specific
    builder = SchemaContextBuilder()
    mixs_schema = builder.format_env_triad_context(
        class_names=interface_names or builder.list_interfaces()
    )
    conversation_manager.add_schema_context(mixs_schema)

    # send in extra context to llm to generate if it exists
    for message in study_context:
        if type(message) is not str:
            message = str(message)
        conversation_manager.add_message(text=message)

    # for now lets assume 100 samples
    conversation_manager.add_message(
        text=f"The following sample records need env triad recommendations. \n"
        f"Return each with their id if available:{str(samples)}",
    )
    # parse back recommendaations to the expected output format,
    # including the provided sample records
    raw_output = conversation_manager.generate(max_tokens=max_tokens)
    # clean and pydantically validate the output
    cleaned_output = clean_and_validate_output(raw_output)

    return cleaned_output
