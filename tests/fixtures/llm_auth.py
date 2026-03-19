"""Shared pytest fixtures for tests requiring LLM credentials (GCP or PNNL)."""

import os
from pathlib import Path

import pytest

_repo_root = Path(__file__).resolve().parent.parent.parent
_gcp_credentials_file = _repo_root / "gcp_credentials.json"


@pytest.fixture(scope="session", autouse=True)
def configure_google_application_credentials() -> None:
    """Populate GOOGLE_APPLICATION_CREDENTIALS from local test defaults when available."""
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and _gcp_credentials_file.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_gcp_credentials_file)


@pytest.fixture(scope="session")
def has_gcp_credentials() -> bool:
    """Return whether any supported GCP credential source is configured."""
    return bool(
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        or os.environ.get("GCLOUD_PROJECT")
        or os.environ.get("CLOUDSDK_CONFIG")
        or _gcp_credentials_file.exists()
    )


@pytest.fixture(scope="session")
def has_pnnl_credentials() -> bool:
    """Return whether PNNL credentials are configured via API key and base URL."""
    return bool(os.environ.get("AI_INCUBATOR_KEY") and os.environ.get("AI_INCUBATOR_BASE_URL"))


@pytest.fixture
def requires_credentials(has_gcp_credentials: bool, has_pnnl_credentials: bool) -> None:
    """Skip the current test when neither GCP nor PNNL credentials are available."""
    if not has_gcp_credentials and not has_pnnl_credentials:
        pytest.skip("Neither GCP nor PNNL credentials available")
