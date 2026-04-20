"""Unified LLM client for OpenAI-compatible providers and Vertex AI."""

import base64
import os
from pathlib import Path
from typing import Any, cast

import google.auth
import google.auth.transport.requests
from anthropic import AnthropicVertex
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from google.oauth2 import service_account
from openai import OpenAI

load_dotenv()

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

ANTHROPIC_VERTEX_MODELS = [
    "claude-haiku-4-5",
    "claude-sonnet-4-5",
    "claude-opus-4-6",
]

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_ANTHROPIC_VERTEX_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS_BY_PROVIDER: dict[str, int] = {
    "pnnl": 128000,
    "cborg": 128000,
    "gcp": 65535,
    "gcp-anthropic": 8192,
}
VALID_ACCESS_PROVIDERS = ("pnnl", "cborg", "gcp", "gcp-anthropic")


class LLMClient:
    """LLM config client supporting PNNL AI Incubator, CBORG, and GCP Vertex AI.

    Vertex AI exposes different publishers through different endpoints:
    Gemini uses ``generateContent`` (via ``google-genai``), while Anthropic
    Claude uses ``rawPredict`` / ``streamRawPredict`` (via ``AnthropicVertex``).
    They share the same service-account credentials but need separate client
    objects — hence the ``gcp`` / ``gcp-anthropic`` split.

    Usage::

        # Default path: PNNL AI Incubator (OpenAI-compatible Responses API)
        client = LLMClient(access_provider="pnnl")
        # you can then pass this client to ConversationManager
        conversation_manager = ConversationManager(llm_client=client, system_prompt=...)
        conversation_manager.add_message(text="Hello, how are you?", pdf_files=["path/to/file.pdf"])
        response = conversation_manager.generate()

        # GCP Vertex AI path: Gemini via google-genai
        client = LLMClient(access_provider="gcp")
        conversation_manager = ConversationManager(llm_client=client, system_prompt=...)
        conversation_manager.add_message(text="Hello, how are you?", pdf_files=["path/to/file.pdf"])
        response = conversation_manager.generate()

        # GCP Vertex AI path: Claude via anthropic-vertex
        client = LLMClient(access_provider="gcp-anthropic")
        conversation_manager = ConversationManager(llm_client=client, system_prompt=...)
        conversation_manager.add_message(text="Hello, how are you?")
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
        if access_provider not in VALID_ACCESS_PROVIDERS:
            raise ValueError(
                f"Unknown access_provider '{access_provider}'. "
                f"Use one of: {', '.join(VALID_ACCESS_PROVIDERS)}."
            )
        self.access_provider = access_provider
        self.project = project or os.environ.get("VERTEX_PROJECT_ID")
        self.region = region or os.environ.get(
            "GEMINI_REGION",
            os.environ.get("CLOUD_ML_REGION", DEFAULT_GCP_REGION),
        )
        self.credentials_file = credentials_file or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        self.client: OpenAI | genai.Client | AnthropicVertex

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

        if access_provider == "gcp-anthropic":
            self.model = model or DEFAULT_ANTHROPIC_VERTEX_MODEL
            credentials = self._get_gcp_credentials()
            if not self.project:
                raise RuntimeError(
                    "VERTEX_PROJECT_ID is not set and could not be inferred from credentials. "
                    "Set VERTEX_PROJECT_ID in your environment."
                )
            self.client = AnthropicVertex(
                region=self.region,
                project_id=self.project,
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
    ) -> str:
        """Generate a response from queued conversation messages.

        Dispatches by ``access_provider``:
        - ``pnnl``: uses OpenAI-compatible Responses API (model defaults to first
            entry in ``PNNL_GPT_MODELS`` when omitted).
        - ``cborg``: uses OpenAI-compatible API (model defaults to
            ``DEFAULT_GEMINI_MODEL`` when omitted).
        - ``gcp``: uses Vertex Gemini ``models.generate_content`` (model defaults
            to ``DEFAULT_GEMINI_MODEL`` when omitted).
        - ``gcp-anthropic``: uses Vertex Claude via AnthropicVertex
            ``messages.create`` (model defaults to
            ``DEFAULT_ANTHROPIC_VERTEX_MODEL`` when omitted).

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
        elif self.llm_client.access_provider == "gcp-anthropic":
            return self._generate_anthropic_vertex(
                max_tokens=resolved_max_tokens,
                system_prompt=self.system_prompt,
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
    ) -> str:
        client = cast(OpenAI, self.llm_client.client)
        response = client.responses.create(
            model=self.llm_client.model,
            input=self.messages,
            instructions=system_prompt,
            max_output_tokens=max_tokens,
        )
        return response.output_text

    def _generate_gcp(
        self,
        max_tokens: int,
        system_prompt: str,
        temperature: float = 0.4,
    ) -> str:

        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_prompt,
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

    def _generate_anthropic_vertex(
        self,
        max_tokens: int,
        system_prompt: str,
    ) -> str:
        client = cast(AnthropicVertex, self.llm_client.client)
        message = client.messages.create(
            model=self.llm_client.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=self.messages,
        )
        text_parts = [block.text for block in message.content if hasattr(block, "text")]
        if not text_parts:
            raise RuntimeError(
                f"Anthropic Vertex model returned no text content. Response: {message}"
            )
        return "".join(text_parts)

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

        if self.llm_client.access_provider == "gcp-anthropic":
            # Anthropic messages API: list of {role, content} where content
            # is either a plain string (text-only) or a list of content
            # blocks (text + document for PDFs).
            content_blocks: list[dict[str, Any]] = []
            if pdf_files:
                for pdf_file in pdf_files:
                    with open(pdf_file, "rb") as f:
                        encoded = base64.standard_b64encode(f.read()).decode("utf-8")
                    content_blocks.append(
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": encoded,
                            },
                        }
                    )
            if text:
                content_blocks.append({"type": "text", "text": text})
            if content_blocks:
                self.messages.append({"role": "user", "content": content_blocks})

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
