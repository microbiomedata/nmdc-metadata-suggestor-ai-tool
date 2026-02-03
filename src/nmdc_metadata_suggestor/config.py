"""Configuration management for NMDC Metadata Suggestor."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # API Keys
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # LLM Configuration
    default_model: str = "gpt-4o-mini"
    max_tokens: int = 4096
    temperature: float = 0.7

    # Application Configuration
    log_level: str = "INFO"
    debug: bool = False


# Global settings instance
settings = Settings()
