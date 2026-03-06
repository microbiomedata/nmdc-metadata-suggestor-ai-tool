"""Unified LLM client for Vertex AI (Gemini and Claude)."""

import os
from typing import Any, cast

import google.auth.transport.requests
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from google.oauth2 import service_account
from openai import OpenAI
from pathlib import Path

import base64

from nmdc_metadata_suggestor.doi_ingestion.doi_utils import request_with_retry
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

CLAUDE_MODELS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-5@20250929",
    "haiku": "claude-haiku-4-5@20251001",
}

PNNL_GPT_MODELS = [
    "gpt-5-project",
    "gpt-5.1-project",
    "gpt-5.2-project",
    "gpt-4.1-project",
    "o3-project",
    "o4-mini-project"
]

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5@20250929"


class LLMClient:
    """Vertex AI LLM client supporting Gemini and Claude providers.

    Usage::

        client = LLMClient(llm_provider="gemini")
        response = client.generate("Your prompt here")

        client = LLMClient(llm_provider="claude")
        response = client.generate("Your prompt here", model="opus")
    """

    def __init__(
        self,
        access_provider: str = "pnnl",
        llm_provider: str = "gemini",
        project: str | None = None,
        region: str | None = None,
        credentials_file: str | None = None,
    ) -> None:
        if llm_provider not in ("gemini", "claude"):
            raise ValueError(f"Unknown llm_provider '{llm_provider}'. Use 'gemini' or 'claude'.")
        if access_provider not in ("pnnl", "gcp"):
            raise ValueError(f"Unknown access_provider '{access_provider}'. Use 'pnnl' or 'gcp'.")
        
        self.llm_provider = llm_provider
        self.access_provider = access_provider
        self.project = project or GCP_PROJECT_ID
        self.region = region or (GEMINI_REGION if llm_provider == "gemini" else GCP_REGION)
        self.credentials_file = credentials_file or GCP_CREDENTIALS_FILE
        self.messages = []  # List to store the conversation messages
        
        if access_provider == "pnnl":
            # load ai incubator key from env
            if not AI_INCUBATOR_KEY or not BASE_URL:
                raise RuntimeError(
                    "AI_INCUBATOR_KEY or AI_INCUBATOR_BASE_URL is not set "
                    "in environment variables."
                )
            # add the system prompt as the first message in the conversation
            self.add_message(role="system", text=system_prompt)
            self.client = OpenAI(base_url=BASE_URL, api_key=AI_INCUBATOR_KEY)

        if access_provider == "gcp":
            self.client = genai.Client(
                vertexai=True, project=self.project, location=self.region
            )


    def generate(
        self,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:
        """Send a prompt and return the response text."""
        if self.access_provider == "gcp":
            return self._generate_gcp(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        # elif self.llm_provider == "claude":
        #     return self._generate_claude(
        #         prompt,
        #         model=model,
        #         system=system,
        #         max_tokens=max_tokens,
        #         temperature=temperature,
        #     )
        elif self.access_provider == "pnnl":
            return self._generate_pnnl(
                model=model,
            )
        return ""

    def _generate_pnnl(
        self,
        *,
        model: str | None,
    ) -> str:
        if model is None:
            model = PNNL_GPT_MODELS[0]  # default to the first model in the list
        response = self.client.responses.create(model=model, input=self.messages)
        return response.output_text

    def _generate_gcp(
        self,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:
        
        model = model or DEFAULT_GEMINI_MODEL
        config = genai_types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
            system_instruction=system_prompt,
        )

        response = self.client.models.generate_content(
            model=model,
            contents=self.messages,
            config=config,
        )
        return (response.text or "").strip()

    def _generate_claude(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:
        model_id = CLAUDE_MODELS.get(model, model) if model else DEFAULT_CLAUDE_MODEL
        token = self._get_access_token()

        url = (
            f"https://{self.region}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project}/locations/{self.region}/"
            f"publishers/anthropic/models/{model_id}:rawPredict"
        )

        body: dict[str, Any] = {
            "anthropic_version": "vertex-2023-10-16",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if system:
            body["system"] = system

        response = request_with_retry(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=300,
        )
        response.raise_for_status()
        result = response.json()

        content = result.get("content", [{}])
        return "".join(block.get("text", "") for block in content).strip()

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

    def add_message(self, role: str, text: str, pdf_files: list[str] = None):
        """
        Adds a message to the conversation.
        Parameters
        ----------
        role (str): The role of the message sender must be one of ('user', 'assistant', 'system').
        text (str): The text content of the message.
        pdf_files (list[str]): A list of paths to PDF files to include in the message.
        """
        content = []
        if self.access_provider == "pnnl":
            # PNNL goes through OpenAI API which supports list[dict]
            # load the pdf bytes and encode to base64
            pdf_file_data = []
            if pdf_files:
                for pdf_file in pdf_files:
                    with open(pdf_file, "rb") as f:
                        pdf_bytes = f.read()
                        encoded = base64.standard_b64encode(pdf_bytes).decode("utf-8")
                        pdf_file_data.append(f"data:application/pdf;base64,{encoded}")

                content = [
                    {
                        "role": role,
                        "content": [
                            {
                                "type": "input_file",
                                "filename": f"file_{i}.pdf",
                                "file_data": pdf_file_data[i],
                            }
                            for i in range(len(pdf_files or []))
                        ]
                    }
                ]
            if text:
                content.append({"role": role, "content": text})
            self.messages.extend(content)
        
        if self.access_provider == "gcp":
            # GCP is a list of the messages
            if file_path:
                file_path = Path(file_path)
                content.append(
                    genai_types.Part.from_bytes(
                        data=file_path.read_bytes(),
                        mime_type='application/pdf',
                    )
                )
            if text:
                content.append(text)
            self.messages.extend(content)

    def add_schema_context(self, schema: str):
        """
        Adds a schema description message to the conversation.
        Parameters
        ----------
        schema (str) : The schema description gathered from user input data.
        """
        self.add_message(role="user", text="Utilize the following schema context to inform your metadata field recommendations:\n" + schema)

    def add_schema_and_slot_examples(self):
        """
        Add the currated examples of schema, description, and mappings. 
        """
        raise NotImplementedError("This method is not yet implemented. It will add example mappings from schema context to YAML output to the conversation history to help guide the LLM's recommendations.")