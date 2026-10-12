from pathlib import Path
from typing import Any

import pytest

import nmdc_metadata_suggestor_ai_tool.env_triad_recommendation as env_triad_recommendation
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient

pytestmark = pytest.mark.integration


def load_sample_submission_object() -> dict[str, Any]:
    """Load a sample submission object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "test_submission.json"
    with open(sample_path) as f:
        data: dict[str, Any] = json.load(f)
        return data


def load_biosample_object() -> dict[str, Any]:
    """Load a sample biosample object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "biosample.json"
    with open(sample_path) as f:
        data: dict[str, Any] = json.load(f)
        return data


def load_bioscales_object() -> dict[str, Any]:
    """Load 100 bioscales biosample objects from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "bioscales_biosamples.json"
    with open(sample_path) as f:
        data: dict[str, Any] = json.load(f)
        return data


def load_study_object() -> dict[str, Any]:
    """Load bioscales study object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "bioscales_study.json"
    with open(sample_path) as f:
        data: dict[str, Any] = json.load(f)
        return data


def load_full_submission() -> dict[str, Any]:
    """Load full submission object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "full_submission.json"
    with open(sample_path) as f:
        data: dict[str, Any] = json.load(f)
        return data


def test_run_env_triad_pipeline(requires_credentials: None) -> None:
    sample_submission_object = load_sample_submission_object()
    llm_client = LLMClient(access_provider="gcp")
    recommended_metadata = env_triad_recommendation.get_env_triad_recommendation(
        submission_object=sample_submission_object,
        samples=sample_submission_object["metadata_submission"]["sampleData"]["soil_data"],
        interface_names=["soil"],
        llm_client=llm_client,
    )

    print(recommended_metadata.model_dump())
    # Ensure at least one of the recommended fields is an env triad slot
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    field_names = {f.field_name for f in recommended_metadata.metadata_fields}
    assert env_triad_names & field_names, f"Expected env triad field in {field_names}"


def test_run_env_triad_pipeline_with_biosample(requires_credentials: None) -> None:
    llm_client = LLMClient(access_provider="gcp")
    biosample_object = load_biosample_object()
    recommended_metadata = env_triad_recommendation.get_env_triad_recommendation(
        samples=biosample_object["biosample_set"],
        llm_client=llm_client,
    )

    print(recommended_metadata.model_dump())
    # Ensure at least one of the recommended fields is an env triad slot
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    field_names = {f.field_name for f in recommended_metadata.metadata_fields}
    assert env_triad_names & field_names, f"Expected env triad field in {field_names}"


def test_run_env_triad_pipeline_with_bioscales(requires_credentials: None) -> None:
    llm_client = LLMClient(access_provider="gcp")
    biosample_objects = load_bioscales_object()["resources"]
    study_object = load_study_object()
    recommended_metadata = env_triad_recommendation.get_env_triad_recommendation(
        study_context=[study_object],
        # Using a subset of the biosample objects for testing
        samples=biosample_objects,
        llm_client=llm_client,
    )

    print(recommended_metadata.model_dump())
    # Ensure each sample id appears somewhere in the recommendations
    sample_ids = [s["id"] for s in biosample_objects]

    for sid in sample_ids:
        assert sid in {f.id for f in recommended_metadata.metadata_fields}, (
            f"Expected sample id {sid} in recommendations"
        )

    ids = {f.id for f in recommended_metadata.metadata_fields}
    for id in ids:
        assert id in sample_ids, f"Expected metadata field id {id} to be in sample ids {sample_ids}"

    assert set(sample_ids) == set(ids)
    # Ensure at least one of the recommended fields is an env triad slot
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    field_names = {f.field_name for f in recommended_metadata.metadata_fields}
    assert env_triad_names & field_names, f"Expected env triad field in {field_names}"


def test_run_env_triad_pipeline_with_submission_samples(requires_credentials: None) -> None:
    llm_client = LLMClient(access_provider="gcp")
    submission_object = load_full_submission()
    samples = submission_object["metadata_submission"]["sampleData"]["water_data"][:10]
    # pop env_medium, env_broad_scale, and env_local_scale from the samples
    for sample in samples:
        sample.pop("env_medium", None)
        sample.pop("env_broad_scale", None)
        sample.pop("env_local_scale", None)

    recommended_metadata = env_triad_recommendation.get_env_triad_recommendation(
        submission_object=submission_object,
        # Using a subset of the sample objects for testing
        interface_names=["water"],
        samples=samples,
        llm_client=llm_client,
    )

    print(recommended_metadata.model_dump())

    # get a unique list of ids from the LLM output
    ids = {f.id for f in recommended_metadata.metadata_fields if f.id is not None}
    # in this case we know there is 10 samples -> so the ids should be 0-9
    truth_ids = [str(x) for x in sorted(range(10))]
    # assert the ids are 0-9
    assert sorted(set(ids)) == truth_ids
    # Ensure at least one of the recommended fields is an env triad slot
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    field_names = {f.field_name for f in recommended_metadata.metadata_fields}
    assert env_triad_names & field_names, f"Expected env triad field in {field_names}"
