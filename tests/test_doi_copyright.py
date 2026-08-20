"""Unit and integration tests for DOI copyright / license checking.

Unit tests cover the decision rules in ``.claude/skills/doi-copyright-check/SKILL.md``:

* CC BY family → allowed (with attribution condition)
* CC BY-ND → allowed (verbatim only)
* CC BY-NC family → allowed (non-commercial use only; restriction noted in condition)
* CC0 / public domain → allowed (no attribution required)
* All-rights-reserved / no CC license → not_allowed
* No license info at all → uncertain
* License supplied as a URL (e.g. creativecommons.org/licenses/by/4.0/)
* License embedded in abstract text rather than a dedicated metadata field
* Version variants (3.0 and 4.0 treated identically)

Integration tests (``-m integration``, require GCP or PNNL credentials) run the
full agentic pipeline against two fixture submissions:

* ``submission_cc_by_publication.json`` — DOI 10.1038/s41564-020-00861-0 (CC BY 4.0).
  The agent should use the abstract as context and produce evidence-cited suggestions.

* ``submission_restricted_publication.json`` — DOI 10.1126/science.aav2566 (all rights
  reserved, no CC license).  The agent should still suggest fields from submission
  metadata alone, but must NOT cite the abstract as a source (it was excluded).
"""

import asyncio
import json
from pathlib import Path

import pytest

from nmdc_metadata_suggestor_ai_tool.doi_ingestion.doi_copyright import (
    CopyrightCheckResult,
    check_doi_copyright,
)

FIXTURES = Path(__file__).parent / "fixtures"
INTEGRATION_TIMEOUT = 180  # seconds


def _load(filename: str) -> dict:
    with (FIXTURES / filename).open() as f:
        return json.load(f)


_DOI_ALLOWED = "10.1038/s41564-020-00861-0"
_DOI_RESTRICTED = "10.1126/science.aav2566"


# ---------------------------------------------------------------------------
# Fixtures — real-world-style publications
# ---------------------------------------------------------------------------


@pytest.fixture
def cc_by_publication() -> dict:
    """Simulate metadata for a Nature Microbiology article published open-access CC BY 4.0.

    Based on: 10.1038/s41564-020-00861-0
    (Nayfach et al., "A genomic catalog of Earth's microbiomes", Nature Microbiology 2021)
    License field as returned by CrossRef / OpenAlex.
    """
    return {
        "doi": _DOI_ALLOWED,
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "abstract": (
            "Genomic sequencing has revolutionized our understanding of microbial "
            "diversity across Earth's ecosystems. Here we present a catalog of "
            "metagenome-assembled genomes spanning major habitats."
        ),
    }


@pytest.fixture
def all_rights_reserved_publication() -> dict:
    """Simulate metadata for a Science article with no open-access license.

    Based on: 10.1126/science.aav2566
    (typical paywalled Science publication — no CC license in CrossRef metadata)
    """
    return {
        "doi": _DOI_RESTRICTED,
        "license": None,
        "abstract": (
            "Soil microbial communities play a key role in biogeochemical cycling. "
            "This study characterizes metagenomes from 189 soil samples across "
            "diverse biomes."
        ),
    }


# ---------------------------------------------------------------------------
# Core verdict tests — the two headline cases
# ---------------------------------------------------------------------------


def test_cc_by_publication_is_allowed(cc_by_publication: dict) -> None:
    """A CC BY 4.0 publication must return verdict=allowed with an attribution condition."""
    result = check_doi_copyright(
        doi=cc_by_publication["doi"],
        license_string=cc_by_publication["license"],
        context_text=cc_by_publication["abstract"],
    )
    assert isinstance(result, CopyrightCheckResult)
    assert result.verdict == "allowed"
    assert result.license_found is not None
    assert result.condition is not None
    assert "ttrib" in result.condition  # "Attribute" or "attribution"


def test_all_rights_reserved_publication_is_not_allowed(
    all_rights_reserved_publication: dict,
) -> None:
    """A publication with no CC license must return verdict=not_allowed."""
    result = check_doi_copyright(
        doi=all_rights_reserved_publication["doi"],
        license_string=all_rights_reserved_publication["license"],
        context_text=all_rights_reserved_publication["abstract"],
    )
    assert isinstance(result, CopyrightCheckResult)
    assert result.verdict == "not_allowed"
    assert result.license_found is None


# ---------------------------------------------------------------------------
# Full decision-table coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "license_string,expected_verdict",
    [
        # CC BY variants — allowed
        ("https://creativecommons.org/licenses/by/4.0/", "allowed"),
        ("https://creativecommons.org/licenses/by/3.0/", "allowed"),
        ("CC BY 4.0", "allowed"),
        ("CC-BY", "allowed"),
        # CC BY-SA — allowed
        ("https://creativecommons.org/licenses/by-sa/4.0/", "allowed"),
        ("CC BY-SA 3.0", "allowed"),
        # CC BY-ND — allowed (verbatim-only caveat)
        ("https://creativecommons.org/licenses/by-nd/4.0/", "allowed"),
        ("CC BY-ND 3.0", "allowed"),
        # CC BY-NC family — allowed for PNNL internal use (non-commercial restriction in condition)
        ("https://creativecommons.org/licenses/by-nc/4.0/", "allowed"),
        ("CC BY-NC 4.0", "allowed"),
        ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "allowed"),
        ("https://creativecommons.org/licenses/by-nc-nd/4.0/", "allowed"),
        # CC0 / public domain — allowed
        ("https://creativecommons.org/publicdomain/zero/1.0/", "allowed"),
        ("CC0 1.0", "allowed"),
        ("Public Domain", "allowed"),
    ],
)
def test_license_decision_table(license_string: str, expected_verdict: str) -> None:
    """Every entry in the SKILL.md decision table maps to the correct verdict."""
    result = check_doi_copyright(doi="10.9999/test", license_string=license_string)
    assert result.verdict == expected_verdict, (
        f"License {license_string!r} → expected {expected_verdict!r}, got {result.verdict!r}"
    )


def test_no_license_info_at_all_is_uncertain() -> None:
    """When neither metadata nor abstract text contains a license, verdict is uncertain."""
    result = check_doi_copyright(
        doi="10.9999/mystery",
        license_string=None,
        context_text=None,
    )
    assert result.verdict == "uncertain"
    assert result.license_found is None


def test_all_rights_reserved_string_is_not_allowed() -> None:
    """A plain 'All rights reserved' license string with no CC marker is not_allowed."""
    result = check_doi_copyright(
        doi="10.9999/restricted",
        license_string="All rights reserved",
    )
    assert result.verdict == "not_allowed"


# ---------------------------------------------------------------------------
# License URL parsing
# ---------------------------------------------------------------------------


def test_license_url_by_nc_parsed_correctly() -> None:
    """CC BY-NC URL is parsed to the correct slug and returns allowed (non-commercial use only)."""
    result = check_doi_copyright(
        doi="10.9999/nc-url",
        license_string="http://creativecommons.org/licenses/by-nc/4.0/",
    )
    assert result.verdict == "allowed"
    assert result.license_found is not None
    assert result.condition is not None
    assert "non-commercial" in result.condition.lower()


def test_license_url_by_sa_parsed_correctly() -> None:
    """CC BY-SA URL is parsed to the correct slug and returns allowed."""
    result = check_doi_copyright(
        doi="10.9999/sa-url",
        license_string="https://creativecommons.org/licenses/by-sa/3.0/",
    )
    assert result.verdict == "allowed"


# ---------------------------------------------------------------------------
# License embedded in abstract text
# ---------------------------------------------------------------------------


def test_cc_license_in_abstract_text_detected() -> None:
    """A CC BY notice embedded in the abstract should be found when no metadata field exists."""
    abstract = (
        "This article is distributed under the terms of the Creative Commons "
        "Attribution License (CC BY 4.0), which permits unrestricted use."
    )
    result = check_doi_copyright(
        doi="10.9999/abstract-cc",
        license_string=None,
        context_text=abstract,
    )
    assert result.verdict == "allowed"
    assert result.license_found == abstract


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


def test_result_has_required_fields() -> None:
    """CopyrightCheckResult always carries doi, license_found, verdict, and note."""
    result = check_doi_copyright(doi="10.9999/fields", license_string="CC BY 4.0")
    assert result.doi == "10.9999/fields"
    assert result.verdict in {"allowed", "not_allowed", "uncertain"}
    assert isinstance(result.note, str)
    assert len(result.note) > 0


# ---------------------------------------------------------------------------
# Integration tests — full agentic pipeline, require LLM credentials
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_agent_uses_abstract_context_for_cc_by_publication(
    requires_credentials: None,
) -> None:
    """Agent should cite abstract evidence when the publication is CC BY 4.0.

    Fixture: submission_cc_by_publication.json
    DOI:     10.1038/s41564-020-00861-0  (Nature Microbiology 2021, CC BY 4.0)

    The full pipeline (nmdc-metadata-suggestor skill) fetches the abstract via
    doi-ingestion, passes it through doi-copyright-check (verdict=allowed), and
    includes it as LLM context.  At least one suggestion should cite the abstract
    as its evidence source.
    """
    from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
    from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
    from nmdc_metadata_suggestor_ai_tool.system_prompt import orchestrator_prompt

    submission = _load("submission_cc_by_publication.json")
    client = LLMClient(access_provider="gcp")
    cm = ConversationManager(llm_client=client, system_prompt=orchestrator_prompt)

    result = asyncio.run(cm.agentic(message=str(submission)))

    assert result is not None, "agentic() returned None"
    structured, session_id = result
    assert session_id is not None

    output = (
        structured if isinstance(structured, LLMOutput) else LLMOutput.model_validate(structured)
    )
    assert output.metadata_fields, "Expected at least one metadata field suggestion"

    # At least one reason should reference the abstract, confirming context was used.
    reasons = [f.reason.lower() for f in output.metadata_fields if f.reason]
    abstract_cited = any("abstract" in r for r in reasons)
    assert abstract_cited, (
        f"Expected at least one suggestion to cite the abstract as evidence. Got reasons: {reasons}"
    )


@pytest.mark.integration
@pytest.mark.timeout(INTEGRATION_TIMEOUT)
def test_agent_does_not_cite_abstract_for_restricted_publication(
    requires_credentials: None,
) -> None:
    """Agent must not cite the abstract when the publication has no CC license.

    Fixture: submission_restricted_publication.json
    DOI:     10.1126/science.aav2566  (Science 2017, all rights reserved)

    doi-copyright-check returns verdict=not_allowed, so the abstract is excluded
    from LLM context.  The agent may still suggest fields from the submission's
    own description, notes, and sample data — but must not cite the abstract.
    """
    from nmdc_metadata_suggestor_ai_tool.llm_client import ConversationManager, LLMClient
    from nmdc_metadata_suggestor_ai_tool.models.llm_output import LLMOutput
    from nmdc_metadata_suggestor_ai_tool.system_prompt import orchestrator_prompt

    submission = _load("submission_restricted_publication.json")
    client = LLMClient(access_provider="gcp")
    cm = ConversationManager(llm_client=client, system_prompt=orchestrator_prompt)

    result = asyncio.run(cm.agentic(message=str(submission)))

    assert result is not None, "agentic() returned None"
    structured, session_id = result
    assert session_id is not None

    output = (
        structured if isinstance(structured, LLMOutput) else LLMOutput.model_validate(structured)
    )

    # Suggestions may still come from submission description / sample data.
    # The key constraint: no reason should cite the abstract as evidence.
    reasons = [f.reason.lower() for f in output.metadata_fields if f.reason]
    abstract_cited = any("abstract" in r for r in reasons)
    assert not abstract_cited, (
        "Agent cited the abstract for a restricted publication — "
        "copyright check should have excluded it. "
        f"Got reasons: {reasons}"
    )
