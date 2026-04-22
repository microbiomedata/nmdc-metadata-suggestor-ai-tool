from pathlib import Path

from nmdc_metadata_suggestor_ai_tool import env_triad_recommendation
from nmdc_metadata_suggestor_ai_tool.llm_client import LLMClient


def load_sample_submission_object() -> dict:
    """Load a sample submission object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "test_submission.json"
    with open(sample_path) as f:
        return json.load(f)


def load_biosample_object() -> dict:
    """Load a sample biosample object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "biosample.json"
    with open(sample_path) as f:
        return json.load(f)

def load_bioscales_object() -> dict:
    """Load 100 bioscales biosample objects from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "bioscales_biosamples.json"
    with open(sample_path) as f:
        return json.load(f)
    
def load_study_object() -> dict:
    """Load bioscales study object from the test fixtures."""
    import json

    sample_path = Path(__file__).parent / "fixtures" / "bioscales_study.json"
    with open(sample_path) as f:
        return json.load(f)

def test_run_env_triad_pipeline(requires_credentials: None) -> None:
    sample_submission_object = load_sample_submission_object()
    llm_client = LLMClient(access_provider="gcp")
    recommended_metadata = env_triad_recommendation.get_env_triad_recommendation(
        context=[
            sample_submission_object.get("description", ""),
            sample_submission_object.get("abstract", ""),
        ],
        interface_names=["SoilInterface"],
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
        context=[biosample_object],
        llm_client=llm_client,
    )

    print(recommended_metadata.model_dump())
    # Ensure at least one of the recommended fields is an env triad slot
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    field_names = {f.field_name for f in recommended_metadata.metadata_fields}
    assert env_triad_names & field_names, f"Expected env triad field in {field_names}"

def test_run_env_triad_pipeline_with_bioscales(requires_credentials: None) -> None:
    llm_client = LLMClient(access_provider="pnnl")
    biosample_objects = load_bioscales_object().get("resources")
    study_object = load_study_object()
    recommended_metadata = env_triad_recommendation.get_env_triad_recommendation(
        context=[study_object],
        # Using a subset of the biosample objects for testing
        samples=biosample_objects[:10],
        llm_client=llm_client,
    )

    print(recommended_metadata.model_dump())
    # Ensure each sample id appears somewhere in the recommendations
    sample_ids = [s.get("id")for s in biosample_objects[:10]]

    for sid in sample_ids:
        assert sid in {f.id for f in recommended_metadata.metadata_fields}, f"Expected sample id {sid} in recommendations"
    
    ids = {f.id for f in recommended_metadata.metadata_fields}
    for id in ids:
        assert id in sample_ids, f"Expected metadata field id {id} to be in sample ids {sample_ids}"

    
    assert set(sample_ids) == set(ids)
    # Ensure at least one of the recommended fields is an env triad slot
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    field_names = {f.field_name for f in recommended_metadata.metadata_fields}
    assert env_triad_names & field_names, f"Expected env triad field in {field_names}"
