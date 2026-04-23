import logging

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
