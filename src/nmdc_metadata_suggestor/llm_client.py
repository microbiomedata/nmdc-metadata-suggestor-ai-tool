"""Unified LLM client for Vertex AI (Gemini and Claude)."""

import base64
import os
from pathlib import Path
from typing import Any, cast

import google.auth.transport.requests
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from google.oauth2 import service_account
from openai import OpenAI

from nmdc_metadata_suggestor.system_prompt import system_prompt

load_dotenv()

GCP_CREDENTIALS_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
GCP_PROJECT_ID = os.environ.get("VERTEX_PROJECT_ID")
GCP_REGION = os.environ.get("CLOUD_ML_REGION", "us-east5")
GEMINI_REGION = os.environ.get("GEMINI_REGION", GCP_REGION)
AI_INCUBATOR_KEY = os.environ.get("AI_INCUBATOR_KEY")
BASE_URL = os.environ.get("AI_INCUBATOR_BASE_URL")

GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]


PNNL_GPT_MODELS = [
    "gpt-5-project",
    "gpt-5.1-project",
    "gpt-5.2-project",
    "gpt-4.1-project",
    "o3-project",
    "o4-mini-project",
]

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"


class LLMClient:
    """LLM client supporting two access providers: PNNL AI Incubator and GCP Vertex AI.

    Usage::

        # Default path: PNNL AI Incubator (OpenAI-compatible Responses API)
        client = LLMClient(access_provider="pnnl")
        client.add_message(text="Your prompt here")
        response = client.generate(model="gpt-5-project")

        # GCP Vertex AI path: Gemini via google-genai
        client = LLMClient(access_provider="gcp", llm_provider="gemini")
        client.add_message(text="Your prompt here")
        response = client.generate(model="gemini-2.0-flash")
    """

    def __init__(
        self,
        access_provider: str = "pnnl",
        model: str | None = None,
        project: str | None = None,
        region: str | None = None,
        credentials_file: str | None = None,
    ) -> None:
        if access_provider not in ("pnnl", "gcp"):
            raise ValueError(f"Unknown access_provider '{access_provider}'. Use 'pnnl' or 'gcp'.")

        self.model = model or (
            PNNL_GPT_MODELS[0] if access_provider == "pnnl" else DEFAULT_GEMINI_MODEL
        )
        self.access_provider = access_provider
        self.project = project or GCP_PROJECT_ID
        self.region = region or GEMINI_REGION
        self.credentials_file = credentials_file or GCP_CREDENTIALS_FILE
        self.messages: list[Any] = []  # List to store the conversation messages
        self.client: OpenAI | genai.Client

        if access_provider == "pnnl":
            # load ai incubator key from env
            if not AI_INCUBATOR_KEY or not BASE_URL:
                raise RuntimeError(
                    "AI_INCUBATOR_KEY or AI_INCUBATOR_BASE_URL is not set "
                    "in environment variables."
                )
            self.client = OpenAI(base_url=BASE_URL, api_key=AI_INCUBATOR_KEY)

        if access_provider == "gcp":
            self.client = genai.Client(vertexai=True, project=self.project, location=self.region)

    def generate(
        self,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:
        """Generate a response from queued conversation messages.

        Dispatches by ``access_provider``:
        - ``pnnl``: uses OpenAI-compatible Responses API (model defaults to first
            entry in ``PNNL_GPT_MODELS`` when omitted).
        - ``gcp``: uses Vertex Gemini ``models.generate_content`` (model defaults
            to ``DEFAULT_GEMINI_MODEL`` when omitted).
        """
        if self.access_provider == "gcp":
            return self._generate_gcp(
                max_tokens=max_tokens,
                temperature=temperature,
            )
        elif self.access_provider == "pnnl":
            return self._generate_pnnl(max_tokens=max_tokens, temperature=temperature)
        return ""

    def _generate_pnnl(
        self,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:
        client = cast(OpenAI, self.client)
        response = client.responses.create(
            model=self.model,
            input=self.messages,
            instructions=system_prompt,
            max_output_tokens=max_tokens
        )
        return response.output_text

    def _generate_gcp(
        self,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:

        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_prompt,
        )

        client = cast(genai.Client, self.client)
        response = client.models.generate_content(
            model=self.model,
            contents=self.messages,
            config=config,
        )
        return (response.text or "").strip()

    def _get_access_token(self) -> str:
        """Exchange service account JSON key for a short-lived access token."""
        if not self.credentials_file or not os.path.exists(self.credentials_file):
            raise RuntimeError(
                "GOOGLE_APPLICATION_CREDENTIALS is not set or file not found. "
                "Set it in your .env to the path of your service account JSON key."
            )
        credentials = service_account.Credentials.from_service_account_file(
            self.credentials_file,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        credentials.refresh(google.auth.transport.requests.Request())
        token = credentials.token
        if token is None:
            raise RuntimeError("Failed to obtain access token from credentials")
        return cast(str, token)

    def add_message(self, text: str, pdf_files: list[str] | None = None) -> None:
        """
        Adds a message to the conversation.
        Parameters
        ----------
        text (str): The text content of the message.
        pdf_files (list[str]): A list of paths to PDF files to include in the message.
        """
        if self.access_provider == "pnnl":
            # PNNL goes through OpenAI API which supports list[dict]
            # load the pdf bytes and encode to base64
            pnnl_content: list[dict[str, Any]] = []
            pdf_file_data: list[str] = []
            if pdf_files:
                for pdf_file in pdf_files:
                    with open(pdf_file, "rb") as f:
                        pdf_bytes = f.read()
                        encoded = base64.standard_b64encode(pdf_bytes).decode("utf-8")
                        pdf_file_data.append(f"data:application/pdf;base64,{encoded}")

                pnnl_content = [
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
                pnnl_content.append({"role": "user", "content": text})
            self.messages.extend(pnnl_content)

        if self.access_provider == "gcp":
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

    def add_schema_and_slot_examples(self) -> None:
        """
        Add the curated examples of schema, description, and mappings.
        """
        raise NotImplementedError(
            "This method is not yet implemented. "
            "It will add example mappings from schema context to "
            "YAML output to the conversation history to help guide the LLM's recommendations."
        )
