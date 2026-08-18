import logging
from collections.abc import Iterator

from pydantic import ValidationError

from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput

logger = logging.getLogger(__name__)


def validate_output(structured_output: LLMOutput | str | None) -> LLMOutput:
    """Clean the raw output from the LLM and validate it against the expected schema
    Note
    ----
    GCP and OPENAI providers return the output in different shapes, so this function handles
    both cases and validates the output against the expected schema. Hence the if statement.
    """
    logger.debug(f"Structured LLM output: {structured_output}")

    try:
        if isinstance(structured_output, LLMOutput):
            return structured_output
        elif isinstance(structured_output, str):
            validated_output = LLMOutput.model_validate_json(structured_output)
            return validated_output
    except ValidationError as exc:
        raise ValueError(f"LLM response JSON did not match expected output schema: {exc}") from exc
    return structured_output or LLMOutput()  # return empty output if None or unrecognized format


def chunk_samples(samples: list[dict], chunk_size: int) -> Iterator[list[dict]]:
    """
    Yield successive chunks of samples of size `chunk_size`.

    Parameters:
        samples: A list of sample records to generate the env triad for.
        chunk_size: size of each chunk.
    Returns:
        A list of chunks, each containing a subset of the sample records.
    """
    for i in range(0, len(samples), chunk_size):
        yield samples[i : i + chunk_size]


def iri_to_curie(iri: str) -> str | None:
    """Convert an OBO PURL to a CURIE, or None for anything else.

    ``http://purl.obolibrary.org/obo/ENVO_00001998`` -> ``ENVO:00001998``.
    Only the first underscore is replaced, so IDs that themselves contain one
    (``NCBITaxon_Union_0000030``) survive the round trip.
    """
    if "/obo/" not in iri:
        return None
    return iri.rsplit("/", 1)[-1].replace("_", ":", 1)
