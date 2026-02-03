"""LLM client interface for interacting with various LLM providers."""

from typing import Any

from loguru import logger

from .config import settings


class LLMClient:
    """Generic LLM client that supports multiple providers."""

    def __init__(self, provider: str = "openai") -> None:
        """
        Initialize the LLM client.

        Args:
            provider: The LLM provider to use ('openai' or 'anthropic')
        """
        self.provider = provider
        self._client: Any = None
        self._initialize_client()

    def _initialize_client(self) -> None:
        """Initialize the appropriate client based on the provider."""
        if self.provider == "openai":
            if not settings.openai_api_key:
                logger.warning("OpenAI API key not set. Please set OPENAI_API_KEY in .env")
                return

            from openai import OpenAI

            self._client = OpenAI(api_key=settings.openai_api_key)
            logger.info("Initialized OpenAI client")

        elif self.provider == "anthropic":
            if not settings.anthropic_api_key:
                logger.warning("Anthropic API key not set. Please set ANTHROPIC_API_KEY in .env")
                return

            from anthropic import Anthropic

            self._client = Anthropic(api_key=settings.anthropic_api_key)
            logger.info("Initialized Anthropic client")

        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    def generate(self, prompt: str, model: str | None = None, **kwargs: Any) -> str:
        """
        Generate a response from the LLM.

        Args:
            prompt: The input prompt
            model: Optional model override
            **kwargs: Additional parameters for the API call

        Returns:
            The generated text response
        """
        if not self._client:
            raise RuntimeError("LLM client not initialized. Check your API keys.")

        model = model or settings.default_model
        max_tokens = kwargs.pop("max_tokens", settings.max_tokens)
        temperature = kwargs.pop("temperature", settings.temperature)

        logger.debug(f"Generating response with {self.provider} model: {model}")

        if self.provider == "openai":
            response = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
            return response.choices[0].message.content or ""

        elif self.provider == "anthropic":
            response = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            return response.content[0].text if response.content else ""

        raise RuntimeError(f"Unsupported provider: {self.provider}")

    async def generate_async(self, prompt: str, model: str | None = None, **kwargs: Any) -> str:
        """
        Generate a response from the LLM asynchronously.

        Args:
            prompt: The input prompt
            model: Optional model override
            **kwargs: Additional parameters for the API call

        Returns:
            The generated text response
        """
        if not self._client:
            raise RuntimeError("LLM client not initialized. Check your API keys.")

        model = model or settings.default_model
        max_tokens = kwargs.pop("max_tokens", settings.max_tokens)
        temperature = kwargs.pop("temperature", settings.temperature)

        logger.debug(f"Generating async response with {self.provider} model: {model}")

        if self.provider == "openai":
            from openai import AsyncOpenAI

            async_client = AsyncOpenAI(api_key=settings.openai_api_key)
            response = await async_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
            return response.choices[0].message.content or ""

        elif self.provider == "anthropic":
            from anthropic import AsyncAnthropic

            async_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
            response = await async_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
            return response.content[0].text if response.content else ""

        raise RuntimeError(f"Unsupported provider: {self.provider}")
