"""Test configuration for pytest."""

import sys
from pathlib import Path

import pytest

# Make `tests/` importable so fixture plugins under tests/fixtures can be loaded.
tests_path = Path(__file__).parent
sys.path.insert(0, str(tests_path))

pytest_plugins = ["fixtures.llm_auth"]

# Add src directory to Python path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark any test that uses requires_credentials as integration."""
    integration = pytest.mark.integration
    for item in items:
        if "requires_credentials" in getattr(item, "fixturenames", []):
            item.add_marker(integration, append=False)
