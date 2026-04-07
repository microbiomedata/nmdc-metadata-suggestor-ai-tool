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


def test_run_env_triad_pipeline_with_biosample() -> None:
    sample_submission_object = load_sample_submission_object()
    llm_client = LLMClient(access_provider="gcp")
    recommended_metadata = env_triad_recommendation.get_env_triad_recommendation(
        context=[
            """
{
  "biosample_set": [
    {
      "associated_studies": ["nmdc:sty-11-547rwq94"],
      "collection_date": {
        "has_raw_value": "2013-08-02",
        "type": "nmdc:TimestampValue"
      },
      "description": "Arctic glacier soil microbial communities from Hook Island, Arctic Ocean",
      "ecosystem": "Environmental",
      "ecosystem_category": "Terrestrial",
      "ecosystem_subtype": "Glacier",
      "ecosystem_type": "Soil",
      "elev": 0,
      "geo_loc_name": {
        "has_raw_value": "Arctic Ocean: Hook Island",
        "type": "nmdc:TextValue"
      },
      "gold_biosample_identifiers": ["gold:Gb0239928"],
      "habitat": "Arctic glacier soil",
      "id": "nmdc:bsm-11-48fce216",
      "lat_lon": {
        "has_raw_value": "80.3333333333 52.7833333333",
        "latitude": 80.3333333333,
        "longitude": 52.7833333333,
        "type": "nmdc:GeolocationValue"
      },
      "location": "Arctic Ocean",
      "name": "Arctic glacier soil microbial communities from Hook Island, Arctic Ocean - Rohwer84.arctic.glacier.soil.FJLH2",
      "ncbi_taxonomy_name": "soil metagenome",
      "samp_name": "Rohwer84.arctic.glacier.soil.FJLH2",
      "samp_taxon_id": {
        "has_raw_value": "soil metagenome [NCBITaxon:410658]",
        "term": {
          "id": "NCBITaxon:410658",
          "name": "soil metagenome",
          "type": "nmdc:OntologyClass"
        },
        "type": "nmdc:ControlledIdentifiedTermValue"
      },
      "sample_collection_site": "Arctic glacier soil",
      "specific_ecosystem": "Unclassified",
      "type": "nmdc:Biosample"
    }
  ]
}
"""
        ],
        llm_client=llm_client,
    )

    print(recommended_metadata.model_dump())
    # Ensure at least one of the recommended fields is an env triad slot
    env_triad_names = {"env_broad_scale", "env_local_scale", "env_medium"}
    field_names = {f.field_name for f in recommended_metadata.metadata_fields}
    assert env_triad_names & field_names, f"Expected env triad field in {field_names}"

