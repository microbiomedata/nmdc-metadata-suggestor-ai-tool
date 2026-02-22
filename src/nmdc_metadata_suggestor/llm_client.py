"""Unified LLM client for Vertex AI (Gemini and Claude)."""

import os
from typing import Any, cast

import google.auth.transport.requests
import requests as http_requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.oauth2 import service_account

load_dotenv()

CREDENTIALS_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
PROJECT_ID = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
REGION = os.environ.get("CLOUD_ML_REGION", "us-east5")
GEMINI_REGION = os.environ.get("GEMINI_REGION", REGION)

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

DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5@20250929"


class LLMClient:
    """Vertex AI LLM client supporting Gemini and Claude providers.

    Usage::

        client = LLMClient(provider="gemini")
        response = client.generate("Your prompt here")

        client = LLMClient(provider="claude")
        response = client.generate("Your prompt here", model="opus")
    """

    def __init__(
        self,
        provider: str = "gemini",
        project: str | None = None,
        region: str | None = None,
        credentials_file: str | None = None,
    ) -> None:
        if provider not in ("gemini", "claude", "pnnl"):
            raise ValueError(f"Unknown provider '{provider}'. Use 'gemini', 'claude', or 'pnnl'.")

        self.provider = provider
        self.project = project or PROJECT_ID
        self.region = region or (GEMINI_REGION if provider == "gemini" else REGION)
        self.credentials_file = credentials_file or CREDENTIALS_FILE
        if provider == "pnnl":
            # load ai incubator key from env
            AI_INCUBATOR_KEY = os.environ.get("AI_INCUBATOR_KEY")
            BASE_URL = os.environ.get("AI_INCUBATOR_BASE_URL")
            if not AI_INCUBATOR_KEY or not BASE_URL:
                raise RuntimeError(
                    "AI_INCUBATOR_KEY or AI_INCUBATOR_BASE_URL is not set in environment variables. "
                    "Set them in your .env to the values of your AI Incubator API key and base URL."
                )
            

        if provider == "gemini":
            self._gemini_client = genai.Client(
                vertexai=True, project=self.project, location=self.region
            )

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:
        """Send a prompt and return the response text."""
        if self.provider == "gemini":
            return self._generate_gemini(
                prompt,
                model=model,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        elif self.provider == "claude":
            return self._generate_claude(
                prompt,
                model=model,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        elif self.provider == "pnnl":
            return self._generate_pnnl(
                prompt,
                model=model,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        
    def _generate_pnnl(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:
        """Placeholder for PNNL LLM generation logic."""
        raise NotImplementedError("PNNL provider is not implemented yet.")
    
    def _generate_gemini(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.4,
    ) -> str:
        model = model or DEFAULT_GEMINI_MODEL
        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        if system:
            config.system_instruction = system

        response = self._gemini_client.models.generate_content(
            model=model,
            contents=prompt,
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

        response = http_requests.post(
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
