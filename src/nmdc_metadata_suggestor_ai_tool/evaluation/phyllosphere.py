"""The phyllosphere study as a supplement-driven env triad test case (issue 143).

Study ``nmdc:sty-11-e4yb9z58``, "Seasonal activities of the phyllosphere
microbiome of perennial crops", 192 biosamples. Its publication DOI serves
twelve supplements through Europe PMC, and one of them
(``41467_2023_36515_MOESM4_ESM.csv``) is a per-sample table carrying the
authors' own env triad. Its award DOI resolves to a funding record with nothing
to fetch. That pair is why the study was chosen: one DOI yields files, the other
yields nothing, and the files carry values the model can be scored against.

Everything specific to this case lives here: the identifiers, the fixture
loaders, the submission object the pipeline needs, and the two references.
The runner that calls a model and scores the arms lives in ``nmdc-ai-eval``
and imports this module, so the case is defined once.

Snapshot provenance: ``docs/test-data-provenance.md``.
"""

import csv
import io
import json
from pathlib import Path
from typing import Any

from nmdc_metadata_suggestor_ai_tool.constants import ENV_TRIAD_SLOTS
from nmdc_metadata_suggestor_ai_tool.evaluation.env_triad_scoring import (
    Reference,
    reference_from_biosamples,
    reference_from_rows,
    rekey_reference,
)
from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementFile

STUDY_ID = "nmdc:sty-11-e4yb9z58"
PUBLICATION_DOI = "10.1038/s41467-023-36515-y"
AWARD_DOI = "10.46936/10.25585/60000818"
INTERFACE = "plant-associated"
SUPPLEMENT_REFERENCE_FILE = "41467_2023_36515_MOESM4_ESM.csv"
SUPPLEMENT_SAMPLE_COLUMN = "sample_name"

# Shipped inside the package, not under tests/, because nmdc-ai-eval installs
# this package from git and needs the same snapshot the docs describe.
DATA = Path(__file__).resolve().parent / "data"
STUDY_FIXTURE = DATA / "phyllosphere_study.json"
BIOSAMPLES_FIXTURE = DATA / "phyllosphere_biosamples.json"


def load_study(path: Path = STUDY_FIXTURE) -> dict[str, Any]:
    """The study record as the NMDC API returned it on 2026-09-11."""
    with path.open() as f:
        study: dict[str, Any] = json.load(f)
        return study


def load_biosamples(path: Path = BIOSAMPLES_FIXTURE) -> list[dict[str, Any]]:
    """The 192 biosample records, in the API's order, triads still attached."""
    with path.open() as f:
        resources: list[dict[str, Any]] = json.load(f)["resources"]
        return resources


def submission_object_for(study: dict[str, Any]) -> dict[str, Any]:
    """A submission-portal shaped object carrying only what the study record gives.

    Going through the pipeline's submission path, rather than ``study_context``,
    makes it fetch the publication abstract itself, exactly as it would for a
    real submission, and exercises the award DOI routing the ticket asks about.
    """
    return {
        "metadata_submission": {
            "packageName": [INTERFACE],
            "studyForm": {
                "studyName": study["name"],
                "description": study["description"],
                "publicationDois": [{"value": PUBLICATION_DOI, "provider": None}],
            },
            "multiOmicsForm": {"awardDois": [{"value": AWARD_DOI, "provider": "jgi"}]},
        }
    }


def strip_triad(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of *samples* with the env triad removed, so the model must supply it."""
    stripped = []
    for sample in samples:
        copy = dict(sample)
        for slot in ENV_TRIAD_SLOTS:
            copy.pop(slot, None)
        stripped.append(copy)
    return stripped


def nmdc_reference(samples: list[dict[str, Any]]) -> Reference:
    """The triad currently stored in NMDC, keyed by biosample id."""
    return reference_from_biosamples(samples)


def supplement_reference(
    files: list[SupplementFile], samples: list[dict[str, Any]]
) -> tuple[Reference, list[str]]:
    """The authors' triad from the supplement table, keyed by biosample id.

    The table keys on ``sample_name``, which equals the biosample ``name`` (and
    ``samp_name``). Returns the reference and the ids of samples with no row.

    Raises:
        ValueError: when the table is not among *files* or was not inlined.
    """
    table = next((f for f in files if f.filename == SUPPLEMENT_REFERENCE_FILE), None)
    if table is None or not table.text:
        raise ValueError(f"{SUPPLEMENT_REFERENCE_FILE} was not among the retrieved supplements")
    rows = list(csv.DictReader(io.StringIO(table.text)))
    by_name = reference_from_rows(rows, id_column=SUPPLEMENT_SAMPLE_COLUMN)
    name_to_id = {
        str(sample.get("name") or sample.get("samp_name")): str(sample["id"]) for sample in samples
    }
    reference = rekey_reference(by_name, name_to_id)
    unmatched = [str(sample["id"]) for sample in samples if str(sample["id"]) not in reference]
    return reference, unmatched
