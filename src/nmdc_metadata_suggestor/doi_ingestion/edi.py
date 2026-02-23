"""EDI DOI resolver."""

import xml.etree.ElementTree as ET

import requests

from nmdc_metadata_suggestor.doi_ingestion.common import append_error, extract_first_xml_text
from nmdc_metadata_suggestor.publication_ingestion.doi_utils import DEFAULT_TIMEOUT, USER_AGENT

EDI_DOI_API = "https://pasta.lternet.edu/package/doi"


def try_edi(doi: str, errors: list[str] | None = None) -> tuple[str, str] | None:
    """Return ``(text, kind)`` from EDI PASTA metadata resolved by DOI."""
    metadata_url = _resolve_edi_metadata_url(doi, errors=errors)
    if metadata_url is None:
        return None

    try:
        response = requests.get(
            metadata_url,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"EDI metadata request returned HTTP {response.status_code}")
            return None
        root = ET.fromstring(response.text)
    except requests.RequestException as exc:
        append_error(errors, f"EDI metadata request failed: {exc.__class__.__name__}")
        return None
    except ET.ParseError:
        append_error(errors, "EDI metadata response was not valid XML")
        return None

    abstract = extract_first_xml_text(root, {"abstract"})
    if abstract:
        return abstract, "abstract"

    description = extract_first_xml_text(root, {"purpose", "description", "title"})
    if description:
        return description, "description"
    append_error(errors, "EDI metadata contained no abstract/description text")
    return None


def _resolve_edi_metadata_url(doi: str, errors: list[str] | None = None) -> str | None:
    """Resolve EDI metadata URL from an EDI DOI."""
    try:
        response = requests.get(
            f"{EDI_DOI_API}/doi:{doi}",
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
        )
        if response.status_code != 200:
            append_error(errors, f"EDI DOI API returned HTTP {response.status_code}")
            return None
    except requests.RequestException as exc:
        append_error(errors, f"EDI DOI API request failed: {exc.__class__.__name__}")
        return None

    for line in response.text.splitlines():
        candidate = line.strip()
        if "/package/metadata/eml/" in candidate:
            return candidate
    append_error(errors, "EDI DOI API response did not include metadata URL")
    return None
