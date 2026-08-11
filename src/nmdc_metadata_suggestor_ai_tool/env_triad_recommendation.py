import logging
from typing import Any

from nmdc_metadata_suggestor_ai_tool.constants import ENV_TRIAD_SLOTS
from nmdc_metadata_suggestor_ai_tool.envo_index import ValidationResult, get_envo_index
from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.download_pdf import remove_temp_files
from nmdc_metadata_suggestor_ai_tool.schema_context import SchemaContextBuilder
from nmdc_metadata_suggestor_ai_tool.system_prompt import env_triad_prompt
from nmdc_metadata_suggestor_ai_tool.utils.build_submission_context import build_submission_context
from nmdc_metadata_suggestor_ai_tool.utils.submission_parser import (
    MixsExtensions,
    get_submission_fields,
)
from nmdc_metadata_suggestor_ai_tool.utils.utils import chunk_samples, validate_output

logger = logging.getLogger(__name__)


def build_envo_expansion_context(interface_names: list[str] | None) -> str:
    """Assemble the tier-2 ENVO candidate context for the given interfaces.

    Always includes the complete biome list -- all of ENVO holds ~127 biomes, so
    that is the entire env_broad_scale universe for any MIxS extension. For the
    other two slots it names the extension-specific subtrees to draw from, which
    is far smaller than dumping the anchor subtrees themselves.
    """
    index = get_envo_index()
    biomes = index.biome_values()
    sections = [
        "# Verified ENVO terms (ENVO expansion tier)",
        "Use these only when the schema enumerations above have nothing appropriate, "
        "or when no enumeration was supplied for this MIxS extension.",
        "",
        f"## env_broad_scale — every ENVO biome ({len(biomes)}; this is the complete set)",
        ", ".join(biomes),
    ]
    for name in interface_names or []:
        for slot in ("env_local_scale", "env_medium"):
            if index.expansion_seeds(name, slot):
                sections.append("")
                sections.append(index.format_expansion_context(name, slot))
    return "\n".join(sections)


def enforce_env_triad_values(output: LLMOutput, interface_names: list[str] | None) -> LLMOutput:
    """Drop or repair env triad values that would fail submission portal validation.

    Label drift is corrected in place. Anything that fails a hard check (bad
    pattern, unknown or obsolete CURIE, wrong anchor class) is replaced by the
    extension's generic fallback rather than emitted, so callers never receive an
    invalid value and never receive an empty one.

    ``source`` is set from the validation result rather than taken from the
    model, so the tier label reflects whether the value really is in the
    extension's curated set.
    """
    index = get_envo_index()
    interfaces: list[str | None] = list(interface_names) if interface_names else [None]
    fallback_interface = interface_names[0] if interface_names else ""

    for suggestion in output.metadata_fields:
        slot = suggestion.field_name
        if slot not in ENV_TRIAD_SLOTS or not isinstance(suggestion.value, str):
            continue
        if not suggestion.value:
            continue

        # A value only has to satisfy one of the interfaces in play: the schema
        # patterns differ, and host-associated admits UBERON where soil does not.
        results = [index.validate(suggestion.value, slot, name) for name in interfaces]
        result: ValidationResult = next((r for r in results if r.ok), results[0])
        if result.ok:
            suggestion.source = result.source
            continue
        if result.corrected_value and len(result.failures) == 1:
            logger.info(f"Corrected {slot} label: {suggestion.value} -> {result.corrected_value}")
            suggestion.value = result.corrected_value
            suggestion.source = index.validate(result.corrected_value, slot, interfaces[0]).source
            continue

        fallback = index.generic_fallback(fallback_interface, slot)
        logger.warning(
            f"Rejected {slot} value {suggestion.value!r} ({'; '.join(result.failures)}); "
            f"falling back to {fallback!r}"
        )
        suggestion.value = fallback
        suggestion.source = "generalized"
        suggestion.reason = f"{suggestion.reason} [Original suggestion failed ENVO validation.]"

    return output


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
            If None, defaults to all interfaces -- roughly 51 KB of value sets, which costs
            tokens and blurs precision. Pass the extension(s) actually in play whenever they
            are known. ENVO expansion candidates are scoped to the same list.
        max_tokens: Optional maximum number of output response tokens.
    Returns:
        The response from the LLM containing the recommended environment triad metadata fields.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")
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
    envo_context = build_envo_expansion_context(interface_names)

    parsed_submission = get_submission_fields(submission_object) if submission_object else None
    # build submission context once to avoid repeated DOI lookups / PDF downloads per chunk
    submission_messages: list[Any] = []
    if parsed_submission:
        submission_messages, pdf_files = build_submission_context(
            parsed_submission_object=parsed_submission
        )

    # if there is study context make sure it is a string
    study_texts = [m if isinstance(m, str) else str(m) for m in (study_context or [])]

    # process samples in chunks to avoid very large prompts
    aggregated = LLMOutput()
    errors: list[dict] = []

    for chunk_idx, chunk in enumerate(chunk_samples(samples, chunk_size)):
        cm = ConversationManager(llm_client=llm_client, system_prompt=env_triad_prompt)
        # reuse pre-built submission context messages
        if submission_messages:
            for message in submission_messages:
                cm.add_message(text=message)
            if pdf_files:
                cm.add_message(text="Use the PDFs to inform your suggestions", pdf_files=pdf_files)

        cm.add_schema_context(mixs_schema)
        cm.add_message(text=envo_context)
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
            validated.access_provider = llm_client.access_provider
            validated.model = llm_client.model
            # merge metadata fields
            aggregated.metadata_fields.extend(validated.metadata_fields)
        except Exception as exc:
            errors.append({"chunk_index": chunk_idx, "size": len(chunk), "error": str(exc)})

    if parsed_submission and pdf_files:
        remove_temp_files(pdf_files)

    if errors:
        logger.error(f"Errors occurred in chunks: {errors}")

    return enforce_env_triad_values(aggregated, interface_names)
