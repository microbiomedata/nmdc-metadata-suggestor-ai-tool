"""Tests for configuration module."""

from nmdc_metadata_suggestor.config import Settings


def test_settings_defaults() -> None:
    """Test default settings values."""
    settings = Settings()
    assert settings.default_model == "gpt-4o-mini"
    assert settings.max_tokens == 4096
    assert settings.temperature == 0.7
    assert settings.log_level == "INFO"
    assert settings.debug is False


def test_settings_override() -> None:
    """Test overriding settings."""
    settings = Settings(
        default_model="gpt-4",
        max_tokens=2048,
        temperature=0.5,
    )
    assert settings.default_model == "gpt-4"
    assert settings.max_tokens == 2048
    assert settings.temperature == 0.5
