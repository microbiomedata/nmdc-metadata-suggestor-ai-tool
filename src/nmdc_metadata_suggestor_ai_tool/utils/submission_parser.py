import logging
from enum import Enum

from nmdc_metadata_suggestor_ai_tool.tracing import observe

logger = logging.getLogger(__name__)


class MixsExtensions(Enum):
    """Map NMDC interface names to submission portal form sections.

    Values are the portal's own template keys, from HARMONIZER_TEMPLATES in
    nmdc-server (web/src/views/SubmissionPortal/types.ts).
    """

    BuiltEnvInterface = "built environment"
    AirInterface = "air"
    SoilInterface = "soil"
    HostAssociatedInterface = "host-associated"
    WaterInterface = "water"
    SedimentInterface = "sediment"
    BiofilmInterface = "microbial mat_biofilm"
    HcrCoresInterface = "hydrocarbon resources-cores"
    HcrFluidsSwabsInterface = "hydrocarbon resources-fluids_swabs"
    MiscEnvsInterface = "miscellaneous natural or artificial environment"
    PlantAssociatedInterface = "plant-associated"
    # In the submission schema (and so in the ETL path) but not yet a portal
    # template; keyed to its wastewater_sludge_data sample slot.
    WastewaterSludgeInterface = "wastewater_sludge"

    @staticmethod
    def map_to_interface_name(mixs_extensions_strings: list[str]) -> list[str]:
        """
        Accept either enum values (e.g., "soil") or enum names (e.g., "SoilInterface"),
        and return enum names (e.g., "SoilInterface").
        """
        mixs_extensions: list[str] = []
        for ext in mixs_extensions_strings:
            if not ext:
                continue
            normalized = MIXS_EXTENSION_ALIASES.get(ext, ext)
            try:
                # If ext is a value like "soil"
                mixs_extensions.append(MixsExtensions(normalized).name)
                continue
            except ValueError:
                pass

            try:
                # If ext is a name like "SoilInterface"
                mixs_extensions.append(MixsExtensions[normalized].name)
            except KeyError:
                logger.warning(f"Unrecognized MIxS extension '{ext}' in submission data.")

        return mixs_extensions


# Spellings that have appeared in submission data but are not the portal's
# current template key.
MIXS_EXTENSION_ALIASES: dict[str, str] = {
    "miscellaneous natural or artifical environment": MixsExtensions.MiscEnvsInterface.value,
}


def make_unique_doi_list(entries: list[dict]) -> list[dict]:
    """Return unique dict entries. Preserves original order."""
    deduped_entries = []
    seen = set()

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = tuple(sorted(entry.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped_entries.append(entry)

    return deduped_entries


# The raw submission object is large and holds every sample row; the parsed
# fields are the part worth seeing.
@observe(name="parse_submission", as_type="span", capture_input=False)
def get_submission_fields(submission_object: dict, sample_set_object: dict | None = None) -> dict:
    """
    Extract relevant fields from the submission object for metadata recommendation.
    At the moment, this returns:
    - description
    - notes
    - study name
    - dois
    - gold study id

    Parameters:
        submission_object: Raw submission object containing submission metadata fields.
        sample_set_object: Raw sample set object containing sample set fields.
    Returns:
        A dict with the extracted fields.
    """
    study_form = submission_object.get("study_form", {})

    # study form fields
    description = study_form.get("description", None)
    notes = study_form.get("notes", None)
    study_name = study_form.get("studyName", None)
    data_dois = study_form.get("dataDois", []) or []
    publication_dois = study_form.get("publicationDois", []) or []
    gold_study_id = study_form.get("GOLDStudyId", None)
    ncbi_bio_project = study_form.get("NCBIBioProjectId", None)

    sample_set_fields = get_sample_set_fields(sample_set_object or {})
    # combine and dedupe DOI list[dict]
    combined_dois = make_unique_doi_list(
        data_dois
        + publication_dois
        + sample_set_fields.get("award_dois", [])
        + sample_set_fields.get("protocol_dois", [])
    )

    return {
        "description": description,
        "notes": notes,
        "study_name": study_name,
        "dois": combined_dois,
        "gold_study_id": gold_study_id,
        "ncbi_bio_project": ncbi_bio_project,
        **sample_set_fields,
    }


def get_sample_set_fields(sample_set_object: dict) -> dict:
    """
    Extract relevant fields from the sample set object for metadata recommendation.

    Parameters:
        sample_set_object: Raw sample set object containing sample set fields.
    Returns:
        A dict with the extracted fields.
    """
    multi_omics_form = sample_set_object.get("multi_omics_form", {})
    jgi_study_id = multi_omics_form.get("JGIStudyId", None)
    awarddois = multi_omics_form.get("awardDois", []) or []

    protocol_dois: list[dict] = []
    protocol_descs: list[str] = []
    protocol_names: list[str] = []
    for protocol_section in [
        "mpProtocols",
        "mbProtocols",
        "mbGcProtocols",
        "lipProtocols",
        "nomProtocols",
        "nomLcProtocols",
    ]:
        protocols = multi_omics_form.get(protocol_section) or {}
        for protocol in protocols.values():
            if protocol and isinstance(protocol, dict):
                doi = protocol.get("doi")
                desc = protocol.get("description")
                name = protocol.get("name")
                if isinstance(doi, str) and doi.strip():
                    protocol_dois.append({"value": doi.strip(), "provider": None})
                if isinstance(desc, str) and desc.strip():
                    protocol_descs.append(desc.strip())
                if isinstance(name, str) and name.strip():
                    protocol_names.append(name.strip())

    sample_environment_form = sample_set_object.get("sample_environment_form", {})
    mixs_extensions_strings = sample_environment_form.get("packageName", []) or []
    mixs_extensions = MixsExtensions.map_to_interface_name(mixs_extensions_strings)

    return {
        "jgi_study_id": jgi_study_id,
        "award_dois": awarddois,
        "protocol_dois": protocol_dois,
        "protocol_descs": protocol_descs,
        "protocol_names": protocol_names,
        "mixs_extensions": mixs_extensions,
    }

def get_samples(sample_set_object: dict, interface_name: str) -> list[dict]:
    """
    Extract individual sample records from the sample set object.

    Parameters:
        sample_set_object: Raw sample set object containing sample data.
    Returns:
        A list of sample records as dictionaries.
    """
    return (
        sample_set_object.get("sample_data", {}).get("data", {}).get(interface_name + "_data", [])
    )
