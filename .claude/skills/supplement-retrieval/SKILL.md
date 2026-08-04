---
name: supplement-retrieval
description: Use this skill to retrieve supplementary materials for a manuscript DOI, prioritizing the file types useful for characterizing NMDC submissions and samples (tabular metadata and supplementary documents). Performance-first: single attempt, no repeated retries.
---

# Supplement Retrieval Skill

Use this skill to obtain **supplementary materials** for a publication DOI so they
can serve as extra context for NMDC metadata suggestion (value-filling for
submissions and biosamples).

## What to prioritize

We do **not** want every supplement — only the kinds that help characterize
samples and studies. Keep, in priority order:

1. **Tabular data** (`.csv`, `.tsv`, `.xlsx`, `.xls`, `.ods`) — usually per-sample
   metadata / measurement tables. **Highest value.**
2. **Documents** (`.pdf`, `.docx`, `.txt`, `.rtf`) — supplementary methods, site
   descriptions, sampling protocols.

Skip (low value for metadata suggestion): figures/images, audio/video, raw
sequence data (`.fasta`, `.fastq`, `.bam`, …), and nested archives.

## Performance first

Supplements are a *nice-to-have*, not a blocker. Make **one** attempt per DOI. If
supplements are absent, gated, or hard to retrieve, move on — do **not** retry
repeatedly or try to brute-force publisher paywalls.

## Which DOI do I pass?

Pass **whatever DOI the submission gives you** — the skill routes by DOI type, so
you don't need to know a supplement's own DOI in advance:

- A **publication DOI** → the skill fetches the article's hosted supplements *and*
  follows the article's Crossref/DataCite relation metadata to any linked data
  repositories (Dryad/Zenodo/Figshare). Optionally pass `text=` (an abstract or
  PDF text you already have) to also mine "Data Availability" DOIs from prose.
- A **data-repository DOI** (`10.5061` Dryad, `10.5281` Zenodo, `10.6084` Figshare)
  → the skill fetches that repository's files directly.

## Programmatic path (default)

Call `retrieve_supplements(doi)`. For a publication DOI it resolves in layers and
**merges** the results (each `SupplementFile.source` records its origin):

1. **Europe PMC** `supplementaryFiles` (ZIP; open-access). Supplement **captions**
   are read from the article's JATS full text, so files can be judged by content,
   not just extension.
2. **NCBI PMC OA Web Service** (`.tar.gz`) — fallback when Europe PMC has none;
   captions from the bundled `.nxml`.
3. **Related datasets** — Dryad/Zenodo/Figshare DOIs linked via the article's
   relation metadata (`find_related_data_dois`). Dryad content is fetched from
   its Zenodo mirror (Dryad's own download API is auth-gated).
4. **Text-mined datasets** — when `text=` is given, data-repository DOIs found in
   it are also fetched; sequence/proteomics accessions (PRJNA…, SRR…, GSE…) are
   surfaced in `result.detected_accessions` but **not** retrieved.

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements import (
    retrieve_supplements,
)

result = retrieve_supplements(doi="10.1038/s41564-020-00861-0", text=abstract_text)

for f in result.files:
    label = f"Supplement {f.filename} [{f.source}]" + (f" — {f.caption}" if f.caption else "")
    if f.text:  # csv/tsv/txt inlined directly
        add_to_evidence(f"{label}:\n{f.text}")
    elif f.saved_path:  # xlsx/pdf/docx written to a temp file
        ...  # hand to a downstream reader if needed
```

Key fields: `result.files` (kept, merged across sources), `result.skipped` (found
but dropped, with `skipped_reason`), `result.source` (contributing sources joined
by `+`), `result.detected_accessions`, `result.error`. `SupplementFile` carries
`source`, `caption`, and either `text` (text-like) or `saved_path` (binary).

Pass `sources=[...]` to run an explicit list (`"europepmc"`, `"pmc_oa"`, `"dryad"`,
`"zenodo"`, `"figshare"`), or `follow_related=False` to skip relation-following.
To cheaply check availability *before* downloading, call
`find_supplement_source_europepmc(doi)` or `find_supplement_source_pmc_oa(pmcid)`.

For the full API (per-source helpers, caps, kind filtering, temp-file cleanup),
see [refs/programmatic.md](refs/programmatic.md).

## Agentic fallback (optional, one shot)

If the programmatic path returns nothing and a supplementary-material URL is known
(e.g. from a publisher landing page in `SourceRetrievalResult.publication_urls`),
you may make **one** `web_fetch` attempt on a tabular/document supplement URL. Do
not loop over many candidate URLs and do not persist temp files.
