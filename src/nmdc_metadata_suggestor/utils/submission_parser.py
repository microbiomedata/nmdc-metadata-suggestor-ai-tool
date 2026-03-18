from enum import Enum


class MixisExtensions(Enum):
    """Map NMDC interface names to submission portal form sections."""

    BuiltEnvInterface = "built environment"
    AirInterface = "air"
    SoilInterface = "soil"
    HostAssociatedInterface = "host-associated"
    WaterInterface = "water"
    SedimentInterface = "sediment"
    BiofilmInterface = "microbial mat_biofilm"
    HcrCoresInterface = "hydrocarbon resources - cores"
    HcrFluidsSwabsInterface = "hydrocarbon resources - fluids swabs"
    MiscEnvsInterface = "miscellaneous natural or artifical environment"
    PlantAssociatedInterface = "plant-associated"


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


def get_submission_fields(submission_object: dict) -> dict:
    """
    Extract relevant fields from the submission object for metadata recommendation.
    At the moment, this returns:
    - description
    - notes
    - study name
    - dois
    - gold study id
    - jgi study id
    - protocol descriptions
    - protocol names
    - mixis extensions (mapped to interface names)

    Parameters:
        submission_object: Raw submission object containing NMDC metadata fields.
    Returns:
        A dict with the extracted fields.
    """
    metadata_submission = submission_object.get("metadata_submission", {})
    study_form = metadata_submission.get("studyForm", {})
    multiomics_form = metadata_submission.get("multiOmicsForm", {})

    # study form fields
    description = study_form.get("description", None)
    notes = study_form.get("notes", None)
    study_name = study_form.get("studyName", None)
    data_dois = study_form.get("dataDois", [])
    publication_dois = study_form.get("publicationDois", []) or []
    gold_study_id = study_form.get("GOLDStudyId", None)

    # multiomics form fields
    jgi_study_id = multiomics_form.get("JGIStudyId", None)
    awarddois = multiomics_form.get("awardDois", [])

    # get all protocol DOIs, descriptions, names from the multiomics form
    # go from multiomics form-protocol->externalprotocol->doi, description, name
    protocol_dois = []
    protocol_descs = []
    protocol_names = []
    for protocol_section in [
        "mpProtocols",
        "mbProtocols",
        "mbGcProtocols",
        "lipProtocols",
        "nomProtocols",
        "nomLcProtocols",
    ]:
        protocols = multiomics_form.get(protocol_section) or {}
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

    # sample environment form fields
    mixis_extensions_strings = metadata_submission.get("packageName", [])
    mixis_extensions = []
    # get the interface names
    for ext in mixis_extensions_strings:
        try:
            mixis_extensions.append(MixisExtensions(ext).name)
        except ValueError:
            print(f"Warning: Unrecognized mixis extension '{ext}' in submission data.")

    # combine and dedupe DOI list[dict]
    combined_dois = make_unique_doi_list(data_dois + publication_dois + awarddois + protocol_dois)

    return {
        "description": description,
        "notes": notes,
        "study_name": study_name,
        "dois": combined_dois,
        "gold_study_id": gold_study_id,
        "jgi_study_id": jgi_study_id,
        "protocol_descs": protocol_descs,
        "protocol_names": protocol_names,
        "mixis_extensions": mixis_extensions,
    }
