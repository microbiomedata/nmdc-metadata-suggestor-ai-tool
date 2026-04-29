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
from nmdc_metadata_suggestor_ai_tool.utils.utils import chunk_samples, validate_output


def get_env_triad_recommendation(
    llm_client: LLMClient,
    samples: list[dict],
    study_context: list[Any] | None = None,
    submission_object: dict | None = None,
    interface_names: list[str] | None = None,
    max_tokens: int | None = None,
    chunk_size: int = 50,
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
    # normalize and shallow-copy samples to avoid mutating caller-supplied objects
    samples = [dict(s) for s in (samples or [])]
    for idx, s in enumerate(samples):
        s.setdefault("id", str(idx))

    # prepare schema/context once
    if interface_names:
        interface_names = MixsExtensions.map_to_interface_name(interface_names)
    builder = SchemaContextBuilder()
    mixs_schema = builder.format_env_triad_context(
        class_names=interface_names or builder.list_interfaces()
    )

    parsed_submission = get_submission_fields(submission_object) if submission_object else None
    study_texts = [m if isinstance(m, str) else str(m) for m in (study_context or [])]

    # process samples in chunks to avoid very large prompts
    aggregated = LLMOutput()
    errors: list[dict] = []

    for chunk_idx, chunk in enumerate(chunk_samples(samples, chunk_size)):
        cm = ConversationManager(llm_client=llm_client, system_prompt=env_triad_prompt)
        if parsed_submission:
            build_submission_context(
                conversation_manager=cm, parsed_submission_object=parsed_submission
            )
        cm.add_schema_context(mixs_schema)
        for text in study_texts:
            cm.add_message(text=text)

        cm.add_message(
            text=(
                "The following sample records need env triad recommendations.\n"
                f"Return each with their id: {chunk}"
            )
        )

        try:
            raw = cm.generate(max_tokens=max_tokens)
            validated = validate_output(raw)
            # merge metadata fields
            aggregated.metadata_fields.extend(validated.metadata_fields)
            # preserve model/access_provider from last successful call if present
            if validated.model:
                aggregated.model = validated.model
            if validated.access_provider:
                aggregated.access_provider = validated.access_provider
        except Exception as exc:  # pragma: no cover - defensive
            errors.append({"chunk_index": chunk_idx, "size": len(chunk), "error": str(exc)})

    if errors:
        # attach a simple error summary to the access_provider field for visibility
        aggregated.access_provider = (
            f"errors_in_chunks:{len(errors)}"
            if not aggregated.access_provider
            else aggregated.access_provider
        )

    return aggregated
