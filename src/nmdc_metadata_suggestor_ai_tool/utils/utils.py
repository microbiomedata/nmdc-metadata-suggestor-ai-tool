import json
import logging
import re

from pydantic import ValidationError

from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput

logger = logging.getLogger(__name__)


def clean_and_validate_output(raw_output: str) -> LLMOutput:
    """Clean the raw output from the LLM and validate it against the expected schema"""
    logger.debug(f"Raw LLM output: {raw_output}")
    cleaned_response = re.sub(
        r"^```(?:json)?\s*\n?|\n?```$",
        "",
        raw_output.strip(),
        flags=re.MULTILINE,
    ).strip()
    logger.debug(f"Cleaned LLM response: {cleaned_response}")
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
