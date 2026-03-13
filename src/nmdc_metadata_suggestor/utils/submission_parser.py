def get_submission_fields(submission_object: dict) -> dict:
    """
    Extract relevant fields from the submission object for metadata recommendation.
    At the moment, this returns:
    - description
    - notes
    - study name
    - dois


    """
    metadata_submission = submission_object.get("metadata_submission", {})
    study_form = metadata_submission.get("studyForm", {})
    multiomics_form = metadata_submission.get("multiOmicsForm", {})

    # study form fields
    description = study_form.get("description", None)
    notes = study_form.get("notes", None)
    study_name = study_form.get("studyName", None)
    data_dois = study_form.get("dataDois", [])
    publication_dois = study_form.get("publicationDois", None)
    dois = [doi.get("value") for doi in (data_dois or []) + (publication_dois or []) if doi.get("value")]
    gold_study_id = study_form.get("GOLDStudyId", None)

    # multiomics form fields
    jgi_study_id = multiomics_form.get("JGIStudyId", None)
    awarddois = multiomics_form.get("awardDois", [])
    award_dois = [doi.get("value") for doi in (awarddois or []) if doi.get("value")]
    # get all protocol DOIs, descriptions, names from the multiomics form
    # go from multiomics form-protocol->externalprotocol->doi, description, name
    protocol_dois = []
    protocol_descs = []
    protocol_names = []
    for protocol_section in ["mpProtocols", "mbProtocols", "mbGcProtocols", "lipProtocols", "nomProtocols", "nomLcProtocols"]:
        protocols = multiomics_form.get(protocol_section, {})
        for protocol in protocols.values():
            if protocol and isinstance(protocol, dict):
                doi = protocol.get("doi")
                desc = protocol.get("description")
                name = protocol.get("name")
                if isinstance(doi, str) and doi.strip():
                    protocol_dois.append(doi.strip())
                if isinstance(desc, str) and desc.strip():
                    protocol_descs.append(desc.strip())
                if isinstance(name, str) and name.strip():
                    protocol_names.append(name.strip())
    

    # sample environment form fields
    mixis_extensions = metadata_submission.get("projectName", [])
    
    return {
        "description": description,
        "notes": notes,
        "study_name": study_name,
        "dois": dois,
        "gold_study_id": gold_study_id,
        "jgi_study_id": jgi_study_id,
        "award_dois": award_dois,
        "protocol_dois": protocol_dois,
        "protocol_descs": protocol_descs,
        "protocol_names": protocol_names,
        "mixis_extensions": mixis_extensions,
    }
