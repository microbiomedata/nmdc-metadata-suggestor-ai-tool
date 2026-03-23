"""Test configuration for pytest."""

import sys
from pathlib import Path

# Make `tests/` importable so fixture plugins under tests/fixtures can be loaded.
tests_path = Path(__file__).parent
sys.path.insert(0, str(tests_path))

pytest_plugins = ["fixtures.llm_auth"]

# Add src directory to Python path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))
