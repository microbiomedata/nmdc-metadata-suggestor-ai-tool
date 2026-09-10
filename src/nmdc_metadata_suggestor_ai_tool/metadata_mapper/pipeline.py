"""Agentic entry point for the Metadata Mapper."""

import logging
from pathlib import Path
from typing import Any

from nmdc_metadata_suggestor_ai_tool.langfuse_claude_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    query,
)
from nmdc_metadata_suggestor_ai_tool.llm_client import (
    DEFAULT_CLAUDE_MODEL,
    ConversationManager,
    LLMClient,
    build_agent_options,
    structured_output_from_tool_use,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.system_prompt import (
    metadata_mapper_system_prompt,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.utils import (
    build_column_context,
    read_csv_files,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    MetadataMapperOutput,
    SourceFile,
)
from nmdc_metadata_suggestor_ai_tool.tracing import (
    langfuse_client,
    log_assistant_message,
    observe,
    propagate_attributes,
)

logger = logging.getLogger(__name__)

# Skills the mapper agent is allowed to use.
MAPPER_SKILLS = [
    "schema-context",
]


@observe(name="metadata_mapper_agentic", as_type="span", capture_input=False, capture_output=False)
async def run_metadata_mapper_agentic(
    llm_client: LLMClient,
    csv_files: list[tuple[SourceFile, Path]],
    mixs_extensions: list[str],
    session_id: str | None = None,
) -> tuple[MetadataMapperOutput, str | None]:
    """Map columns from user-uploaded CSV files to NMDC metadata slots via the agentic path.

    Parameters
    ----------
    llm_client:
        Configured LLMClient instance.
    csv_files:
        Pairs of (SourceFile, path) for each uploaded file.
    mixs_extensions:
        MIxS extensions the user selected during setup (e.g. ["Soil", "Air"]).
    session_id:
        Optional session ID to resume a previous conversation.

    Returns
    -------
    (MetadataMapperOutput, session_id)
    """
    source_files, column_data = read_csv_files(csv_files)
    message = build_column_context(source_files, column_data, mixs_extensions)

    model = (
        DEFAULT_CLAUDE_MODEL
        if llm_client.access_provider == "gcp"
        else llm_client.model
    )

    options = build_agent_options(
        model,
        skills=MAPPER_SKILLS,
        system_prompt=metadata_mapper_system_prompt,
        output_format={"type": "json_schema", "schema": MetadataMapperOutput.model_json_schema()},
    )

    if langfuse_client is not None:
        langfuse_client.update_current_span(
            input=message,
            metadata={"model": model, "mixs_extensions": mixs_extensions},
        )

    result: MetadataMapperOutput | None = None
    health: dict[str, Any] = {}
    tool_payload: dict[str, Any] | None = None

    async def _process_events(events):
        nonlocal result, health, tool_payload, session_id
        async for event in events:
            if isinstance(event, SystemMessage) and event.subtype == "init":
                session_id = event.data["session_id"]
            elif isinstance(event, AssistantMessage):
                tool_payload = structured_output_from_tool_use(event) or tool_payload
                log_assistant_message(event.content)
            elif isinstance(event, ResultMessage):
                health = ConversationManager.run_health(event)
                raw = event.structured_output or tool_payload
                result = _finalize_mapper_result(raw)
                result.source_files = source_files
                result.model = llm_client.model
                result.access_provider = llm_client.access_provider

    if session_id is None:
        await _process_events(query(prompt=message, options=options))
    else:
        options.resume = session_id
        with propagate_attributes(session_id=session_id):
            await _process_events(query(prompt=message, options=options))

    if langfuse_client is not None:
        langfuse_client.update_current_span(
            output=result,
            metadata={"model": model, "session_id": session_id, **health},
        )

    return result or MetadataMapperOutput(), session_id


def _finalize_mapper_result(raw: Any) -> MetadataMapperOutput:
    """Extract MetadataMapperOutput from whatever shape the agent returned.

    Mirrors the unwrap pattern in ``llm_client.unwrap_structured_output``: try
    direct validation first, then iterate values to handle nested wrapper shapes.
    """
    if raw is None:
        return MetadataMapperOutput()
    if isinstance(raw, MetadataMapperOutput):
        return raw
    if not isinstance(raw, dict):
        logger.warning("Unexpected structured output type %s; returning empty result.", type(raw))
        return MetadataMapperOutput()
    try:
        return MetadataMapperOutput.model_validate(raw)
    except Exception:
        pass
    for v in raw.values():
        if isinstance(v, dict):
            try:
                return MetadataMapperOutput.model_validate(v)
            except Exception:
                continue
    logger.warning("Could not parse MetadataMapperOutput from structured output; returning empty.")
    return MetadataMapperOutput()


if __name__ == "__main__":
    import asyncio
    _csv_path = Path(__file__).parent / "PRJEB13831_Phage_metadata.xlsx - Sheet1.csv"
    _source_file = SourceFile(file_id="phage-001", display_name=_csv_path.name)
    llm_client = LLMClient("gcp")
    asyncio.run(run_metadata_mapper_agentic(
        llm_client=llm_client,
        csv_files=[(_source_file, _csv_path)],
        mixs_extensions=["water"],
        session_id=None,
    ))
