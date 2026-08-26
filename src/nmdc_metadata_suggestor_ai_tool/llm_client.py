"""Unified LLM client for OpenAI-compatible providers and Vertex AI."""

import base64
import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any, cast

import google.auth
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from google.oauth2 import service_account
from openai import OpenAI

from nmdc_metadata_suggestor_ai_tool.envo import enforce_env_triad_values
from nmdc_metadata_suggestor_ai_tool.langfuse_claude_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    query,
)
from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
from nmdc_metadata_suggestor_ai_tool.system_prompt import orchestrator_prompt
from nmdc_metadata_suggestor_ai_tool.tracing import (
    langfuse_client,
    observe,
    propagate_attributes,
)

load_dotenv()

logger = logging.getLogger(__name__)

# The tool the agent calls to hand back its final answer.
STRUCTURED_OUTPUT_TOOL = "StructuredOutput"

# Restrictions that apply to the unattended agent but not to a person working in the repo:
# no network fetches, no repo mutation, no dependency changes. Kept out of
# .claude/settings.json deliberately -- that file governs every Claude Code session here, and
# denying `git commit` there breaks ordinary development.
AGENT_SETTINGS = Path(__file__).resolve().parents[2] / ".claude" / "agent-settings.json"

DEFAULT_GCP_REGION = "us-east5"


GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]


PNNL_GPT_MODELS = [
    "gpt-5-project",
    "gpt-5.1-project",
    "gpt-5.2-project",
    "gpt-4.1-project",
    "o3-project",
    "o4-mini-project",
]

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5@20250929"
DEFAULT_MAX_TOKENS_BY_PROVIDER: dict[str, int] = {
    "pnnl": 128000,
    "cborg": 128000,
    "gcp": 65535,
}


class LLMClient:
    """LLM config client supporting PNNL AI Incubator, CBORG, and GCP Vertex AI.

    Usage::

        # Default path: PNNL AI Incubator (OpenAI-compatible Responses API)
        client = LLMClient(access_provider="pnnl")
        # you can then pass this client to ConversationManager
        conversation_manager = ConversationManager(llm_client=client)
        conversation_manager.add_message(text="Hello, how are you?", pdf_files=["path/to/file.pdf"])
        response = conversation_manager.generate()

        # GCP Vertex AI path: Gemini via google-genai
        client = LLMClient(access_provider="gcp", llm_provider="gemini")
        # you can then pass this client to ConversationManager
        conversation_manager = ConversationManager(llm_client=client)
        conversation_manager.add_message(text="Hello, how are you?", pdf_files=["path/to/file.pdf"])
        response = conversation_manager.generate()
    """

    def __init__(
        self,
        access_provider: str,
        model: str | None = None,
        project: str | None = None,
        region: str | None = None,
        credentials_file: str | None = None,
    ) -> None:
        if access_provider not in ("pnnl", "cborg", "gcp"):
            raise ValueError(
                f"Unknown access_provider '{access_provider}'. Use 'pnnl', 'cborg', or 'gcp'."
            )
        self.access_provider = access_provider
        self.project = project or os.environ.get("VERTEX_PROJECT_ID")
        self.region = region or os.environ.get("GCP_REGION")
        self.credentials_file = credentials_file or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        self.client: OpenAI | genai.Client

        if access_provider == "pnnl":
            self.model = model or PNNL_GPT_MODELS[0]
            # load ai incubator key from env
            api_key = os.environ.get("AI_INCUBATOR_KEY")
            base_url = os.environ.get("AI_INCUBATOR_BASE_URL")
            if not api_key or not base_url:
                raise RuntimeError(
                    "AI_INCUBATOR_KEY or AI_INCUBATOR_BASE_URL is not set in environment variables."
                )
            self.client = OpenAI(base_url=base_url, api_key=api_key)

        if access_provider == "cborg":
            self.model = model or DEFAULT_GEMINI_MODEL
            # load cborg key from env
            api_key = os.environ.get("CBORG_KEY")
            base_url = os.environ.get("CBORG_BASE_URL")
            if not api_key or not base_url:
                raise RuntimeError(
                    "CBORG_KEY or CBORG_BASE_URL is not set in environment variables."
                )
            self.client = OpenAI(base_url=base_url, api_key=api_key)

        if access_provider == "gcp":
            self.model = model or DEFAULT_GEMINI_MODEL
            credentials = self._get_gcp_credentials()
            if not self.project:
                raise RuntimeError(
                    "VERTEX_PROJECT_ID is not set and could not be inferred from credentials. "
                    "Set VERTEX_PROJECT_ID in your environment."
                )
            self.client = genai.Client(
                vertexai=True,
                project=self.project,
                location=self.region,
                credentials=credentials,
            )

    def _get_gcp_credentials(self) -> Any:
        """Get OAuth credentials for Vertex AI (service account file or ADC)."""
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]

        if self.credentials_file:
            if not os.path.exists(self.credentials_file):
                raise RuntimeError(
                    "GOOGLE_APPLICATION_CREDENTIALS is set but file was not found: "
                    f"{self.credentials_file}"
                )
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_file,
                scopes=scopes,
            )
            if not self.project:
                self.project = credentials.project_id
        else:
            credentials, inferred_project = google.auth.default(scopes=scopes)
            if not self.project:
                self.project = inferred_project

        credentials.refresh(google.auth.transport.requests.Request())
        if credentials.token is None:
            raise RuntimeError(
                "Failed to obtain OAuth token for Vertex AI. "
                "Check IAM permissions and Google Cloud authentication setup."
            )
        return credentials


class ConversationManager:
    """
    Manages a conversation with the LLM including system prompts, user messages, and schema context.
    Handles generating responses based on the conversation history and system instructions.
    """

    def __init__(self, llm_client: LLMClient, system_prompt: str) -> None:
        self.llm_client = llm_client
        self.messages: list[Any] = []
        # set the system prompt in the constructor so it can be parameterized in the future
        self.system_prompt = system_prompt

    def generate(
        self,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        gemini_temperature: float = 0.4,
    ) -> LLMOutput | str | None:
        """Generate a response from queued conversation messages.

        Dispatches by ``access_provider``:
        - ``pnnl``: uses OpenAI-compatible Responses API (model defaults to first
            entry in ``PNNL_GPT_MODELS`` when omitted).
        - ``cborg``: uses OpenAI-compatible API (model defaults to
            ``DEFAULT_GEMINI_MODEL`` when omitted).
        - ``gcp``: uses Vertex Gemini ``models.generate_content`` (model defaults
            to ``DEFAULT_GEMINI_MODEL`` when omitted).

        Parameters
        ----------
        model: Optional model name to use for generation. If omitted, defaults will be used based
            on the provider.
        max_tokens: Optional maximum number of tokens for the generated response. If omitted,
            defaults will be used based on the provider.
        gemini_temperature: Optional temperature setting for Gemini models (only applicable when
            ``access_provider`` is ``gcp``). Defaults to 0.4.
        """
        if model:
            self.llm_client.model = model

        resolved_max_tokens = (
            max_tokens
            if max_tokens is not None
            else DEFAULT_MAX_TOKENS_BY_PROVIDER[self.llm_client.access_provider]
        )

        if self.llm_client.access_provider == "gcp":
            return self._generate_gcp(
                max_tokens=resolved_max_tokens,
                system_prompt=self.system_prompt,
                temperature=gemini_temperature,
            )
        elif (
            self.llm_client.access_provider == "pnnl" or self.llm_client.access_provider == "cborg"
        ):
            return self._generate_openai(
                max_tokens=resolved_max_tokens, system_prompt=self.system_prompt
            )
        return ""

    def _generate_openai(
        self,
        max_tokens: int,
        system_prompt: str,
    ) -> LLMOutput | str | None:
        client = cast(OpenAI, self.llm_client.client)
        response = client.responses.parse(
            model=self.llm_client.model,
            input=self.messages,
            instructions=system_prompt,
            max_output_tokens=max_tokens,
            text_format=LLMOutput,
        )
        return response.output_parsed

    def _generate_gcp(
        self,
        max_tokens: int,
        system_prompt: str,
        temperature: float = 0.4,
    ) -> LLMOutput | str | None:

        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_json_schema=LLMOutput.model_json_schema(),
        )

        client = cast(genai.Client, self.llm_client.client)
        response = client.models.generate_content(
            model=self.llm_client.model,
            contents=self.messages,
            config=config,
        )
        if response.text is None:
            raise RuntimeError(
                f"GCP model returned an empty text response. Response object: {response}"
            )
        return response.text

    def add_message(self, text: str, pdf_files: list[str] | None = None) -> None:
        """
        Adds a message to the conversation.
        Parameters
        ----------
        text (str): The text content of the message.
        pdf_files (list[str]): A list of paths to PDF files to include in the message.
        """
        if self.llm_client.access_provider in ("pnnl", "cborg"):
            # OpenAI-compatible providers use list[dict] message format
            openai_content: list[dict[str, Any]] = []
            pdf_file_data: list[str] = []
            if pdf_files:
                for pdf_file in pdf_files:
                    with open(pdf_file, "rb") as f:
                        pdf_bytes = f.read()
                        encoded = base64.standard_b64encode(pdf_bytes).decode("utf-8")
                        pdf_file_data.append(f"data:application/pdf;base64,{encoded}")

                openai_content = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_file",
                                "filename": f"file_{i}.pdf",
                                "file_data": pdf_file_data[i],
                            }
                            for i in range(len(pdf_files or []))
                        ],
                    }
                ]
            if text:
                openai_content.append({"role": "user", "content": text})
            self.messages.extend(openai_content)

        if self.llm_client.access_provider == "gcp":
            # GCP is a list of the messages
            gcp_content: list[genai_types.Part | str] = []
            if pdf_files:
                for file_path_str in pdf_files:
                    file_path = Path(file_path_str)
                    gcp_content.append(
                        genai_types.Part.from_bytes(
                            data=file_path.read_bytes(),
                            mime_type="application/pdf",
                        )
                    )
            if text:
                gcp_content.append(text)
            self.messages.extend(gcp_content)

    def add_schema_context(self, schema: str) -> None:
        """
        Adds a schema description message to the conversation.
        Parameters
        ----------
        schema (str) : The schema description gathered from user input data.
        """
        self.add_message(
            text="Utilize the following schema context to "
            "inform your metadata field recommendations:\n" + schema,
        )

    @staticmethod
    def unwrap_structured_output(raw: Any) -> LLMOutput:
        """Extract LLMOutput from whatever wrapper shape Claude produced."""
        if not isinstance(raw, dict):
            raise ValueError(f"structured_output is not a dict: {type(raw)}")
        try:
            return LLMOutput.model_validate(raw)
        except Exception:
            for v in raw.values():
                if isinstance(v, dict):
                    try:
                        return LLMOutput.model_validate(v)
                    except Exception:
                        continue
        raise ValueError(f"Could not extract LLMOutput from structured_output: {raw}")

    @staticmethod
    def structured_output_from_tool_use(event: AssistantMessage) -> dict[str, Any] | None:
        """The payload the agent passed to the StructuredOutput tool, if it called it.

        ``ResultMessage.structured_output`` has come back empty on runs where the agent did
        call the tool with a complete answer, which silently drops every suggestion. This
        recovers it from the tool call itself.
        """
        for block in event.content or []:
            if getattr(block, "name", None) != STRUCTURED_OUTPUT_TOOL:
                continue
            payload = getattr(block, "input", None)
            if not isinstance(payload, dict):
                continue
            # The argument name has been seen as both "output" and "json_output", and the
            # value as both a dict and a JSON string, so search rather than assume. Requiring
            # metadata_fields keeps an empty wrapper from passing as a recovered result.
            for candidate in (*payload.values(), payload):
                parsed: Any = candidate
                if isinstance(parsed, str):
                    try:
                        parsed = json.loads(parsed)
                    except json.JSONDecodeError:
                        continue
                if isinstance(parsed, dict) and parsed.get("metadata_fields"):
                    return parsed
        return None

    @staticmethod
    def run_health(event: ResultMessage) -> dict[str, Any]:
        """Surface how the run actually went, rather than only what it returned.

        A headless run cannot approve tool use, so it can be blocked on every call and still
        finish. Those denials were previously invisible.
        """
        denials = event.permission_denials or []
        health: dict[str, Any] = {
            "num_turns": event.num_turns,
            "permission_denials": len(denials),
            "terminal_reason": event.terminal_reason,
            "stop_reason": event.stop_reason,
            "is_error": event.is_error,
        }
        if denials:
            by_tool = Counter(d.get("tool_name", "?") for d in denials if isinstance(d, dict))
            health["permission_denials_by_tool"] = dict(by_tool)
            logger.warning(
                f"Agent run blocked on {len(denials)} tool permission denial(s): {dict(by_tool)}. "
                "Headless runs cannot approve tools; grant them in settings."
            )
        if event.is_error or event.errors:
            health["errors"] = event.errors
            logger.error(f"Agent run errored ({event.terminal_reason}): {event.errors}")
        return health

    def finalize_result(self, event: ResultMessage, tool_payload: dict[str, Any] | None) -> Any:
        """Build the validated LLMOutput from whichever source actually carried it."""
        output = None
        if isinstance(event.structured_output, dict):
            try:
                output = self.unwrap_structured_output(event.structured_output)
            except ValueError:
                output = None
        if (output is None or not output.metadata_fields) and tool_payload is not None:
            recovered = self.unwrap_structured_output(tool_payload)
            if recovered.metadata_fields:
                logger.warning(
                    f"ResultMessage carried no metadata_fields; recovered "
                    f"{len(recovered.metadata_fields)} from the {STRUCTURED_OUTPUT_TOOL} call."
                )
                output = recovered
        if output is None:
            raise ValueError(
                f"No usable structured output: got {type(event.structured_output)} from "
                f"ResultMessage and no {STRUCTURED_OUTPUT_TOOL} tool call."
            )
        return enforce_env_triad_values(output, None)

    @observe(name="agentic", as_type="span", capture_input=False, capture_output=False)
    async def agentic(
        self, session_id: str | None = None, message: str | None = None
    ) -> tuple[Any, str | None]:
        """
        Agentic interaction, session handling, and skill/tool usage via Claude Agent SDK
        IMPORTANT NOTE: This only works with GCP auth right now.

        Parameters
        ----------
        session_id: Optional session ID to resume a previous conversation.
        If None, starts a new session.

        """
        model = (
            DEFAULT_CLAUDE_MODEL
            if self.llm_client.access_provider == "gcp"
            else self.llm_client.model
        )
        # set env variable to enable Claude Agent SDK to pick up GCP credentials
        # Claude Agent SDK requires a Claude model, not a Gemini model, even on Vertex AI
        options = ClaudeAgentOptions(
            skills="all",
            model=model,
            system_prompt=orchestrator_prompt,
            # Read .claude/settings.json. Without this the SDK runs under the default
            # permission mode, where Bash needs interactive approval -- which a headless run
            # cannot give, so every ontology lookup the skill asks for is denied and the
            # agent answers from the prompt alone.
            setting_sources=["project"],
            settings=str(AGENT_SETTINGS) if AGENT_SETTINGS.is_file() else None,
            output_format={"type": "json_schema", "schema": LLMOutput.model_json_schema()},
        )

        if message is None:
            raise ValueError("message is required")

        if langfuse_client is not None:
            langfuse_client.update_current_span(
                input=message,
                metadata={"model": model},
            )

        result: Any = None
        health: dict[str, Any] = {}
        tool_payload: dict[str, Any] | None = None
        if session_id is None:
            # Session ID is unknown until the first init event, so we tag
            # the outer span via metadata after the loop completes.
            async for event in query(
                prompt=message,
                options=options,
            ):
                if isinstance(event, SystemMessage) and event.subtype == "init":
                    session_id = event.data["session_id"]
                elif isinstance(event, AssistantMessage):
                    print(f"Assistant: {event.content}")
                    tool_payload = self.structured_output_from_tool_use(event) or tool_payload
                elif isinstance(event, ResultMessage):
                    health = self.run_health(event)
                    result = self.finalize_result(event, tool_payload)
            if langfuse_client is not None:
                langfuse_client.update_current_span(
                    output=result,
                    metadata={
                        "model": model,
                        "session_id": session_id,
                        **health,
                    },
                )
        else:
            options.resume = session_id
            # Session ID is known upfront — propagate it so all child spans
            # (produced by ClaudeAgentSDKInstrumentor) inherit it.
            with propagate_attributes(session_id=session_id):
                async for event in query(
                    prompt=message,
                    options=options,
                ):
                    if isinstance(event, SystemMessage) and event.subtype == "init":
                        session_id = event.data["session_id"]
                    elif isinstance(event, AssistantMessage):
                        tool_payload = self.structured_output_from_tool_use(event) or tool_payload
                    elif isinstance(event, ResultMessage):
                        health = self.run_health(event)
                        result = self.finalize_result(event, tool_payload)
            if langfuse_client is not None:
                langfuse_client.update_current_span(output=result, metadata=health)
        return result, session_id
