"""Retrieve supplementary materials for a manuscript (or dataset) DOI.

The goal is to give the NMDC metadata suggestor extra context for characterizing
submissions and samples -- not to comprehensively mirror every supplement. This
package therefore favors *performance over completeness*:

* Only high-value file kinds (tabular data and documents; see
  :class:`~nmdc_metadata_suggestor_ai_tool.models.supplement.SupplementKind`)
  are kept by default.
* Downloads are bounded by size/count caps and are never retried beyond
  ``request_with_retry``'s transient-error handling. If supplements are hard to
  obtain, we give up quickly rather than hammering the source.

Layout -- one module per source, over a shared core:

* :mod:`~...supplements.shared` -- bounded downloads, member selection against
  the caps, JATS caption parsing. Every source funnels through it.
* :mod:`~...supplements.europepmc` -- Europe PMC ``supplementaryFiles`` ZIP.
* :mod:`~...supplements.pmc_oa` -- NCBI PMC OA ``.tar.gz`` package.
* :mod:`~...supplements.dryad` / ``zenodo`` / ``figshare`` -- data repositories.
* :mod:`~...supplements.related_dois` -- the data deposits a publication links to.
* :mod:`~...supplements.retrieve` -- :func:`retrieve_supplements`, which routes by
  DOI type and merges every contributing source under one shared budget.

For publisher-hosted supplements behind Cloudflare/JS challenges, prefer the
agentic ``web_fetch`` path documented in the ``supplement-retrieval`` skill.
"""

from nmdc_metadata_suggestor_ai_tool.file_kinds import DEFAULT_USEFUL_KINDS
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.dryad import (
    is_dryad_doi,
    retrieve_supplements_from_dryad,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.europepmc import (
    find_supplement_source_europepmc,
    retrieve_supplements_from_europepmc,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.figshare import (
    retrieve_supplements_from_figshare,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.pmc_oa import (
    find_supplement_source_pmc_oa,
    retrieve_supplements_from_pmc_oa,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.related_dois import (
    extract_accessions_from_text,
    extract_dataset_dois_from_text,
    find_related_data_dois,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.retrieve import (
    retrieve_supplements,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.shared import (
    SupplementCaps,
    classify_supplement,
    parse_supplement_captions,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements.zenodo import (
    retrieve_supplements_from_zenodo,
)

__all__ = [
    "DEFAULT_USEFUL_KINDS",
    "SupplementCaps",
    "classify_supplement",
    "extract_accessions_from_text",
    "extract_dataset_dois_from_text",
    "find_related_data_dois",
    "find_supplement_source_europepmc",
    "find_supplement_source_pmc_oa",
    "is_dryad_doi",
    "parse_supplement_captions",
    "retrieve_supplements",
    "retrieve_supplements_from_dryad",
    "retrieve_supplements_from_europepmc",
    "retrieve_supplements_from_figshare",
    "retrieve_supplements_from_pmc_oa",
    "retrieve_supplements_from_zenodo",
]
