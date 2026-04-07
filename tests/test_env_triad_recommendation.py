from pathlib import Path
from types import SimpleNamespace

import pytest

from nmdc_metadata_suggestor_ai_tool import env_triad_recommendation
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient


def load_sample_submission_object() -> dict:
    """Load a sample submission object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "test_submission.json"
    with open(sample_path) as f:
        return json.load(f)


def test_run_env_triad_pipeline(requires_credentials: None) -> None:
    sample_submission_object = load_sample_submission_object()
    llm_client = LLMClient(access_provider="gcp")
    recommended_metadata = env_triad_recommendation.get_env_triad_recommendation(
        context=
            [
                sample_submission_object.get("description", ""),
                sample_submission_object.get("abstract", ""),
            ],
        interface_names=["SoilInterface"],
        llm_client=llm_client
    )

    print(recommended_metadata.model_dump())
    # Ensure at least one of the recommended fields is an env triad slot
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    field_names = {f.field_name for f in recommended_metadata.metadata_fields}
    assert env_triad_names & field_names, f"Expected env triad field in {field_names}"

test_run_env_triad_pipeline()
