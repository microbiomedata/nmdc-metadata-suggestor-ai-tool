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

## Programmatic path (default)

The single reliable programmatic source is the **Europe PMC** `supplementaryFiles`
endpoint (open-access articles). Use the helper:

```python
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.retrieve_supplements import (
    retrieve_supplements,
)

result = retrieve_supplements(doi="10.1038/s41564-020-00861-0")

for f in result.files:
    if f.text:            # csv/tsv/txt inlined directly
        add_to_evidence(f"Supplement {f.filename}:\n{f.text}")
    elif f.saved_path:    # xlsx/pdf/docx written to a temp file
        ...               # hand to a downstream reader if needed
```

Key fields: `result.files` (kept high-value supplements), `result.skipped` (found
but intentionally dropped), `result.error` (why nothing was kept). Text-like files
arrive inline as `SupplementFile.text`; others as `SupplementFile.saved_path`.

To cheaply check availability *before* downloading, call
`find_supplement_source_europepmc(doi)`.

For the full API (caps, kind filtering, temp-file cleanup), see
[refs/programmatic.md](refs/programmatic.md).

## Agentic fallback (optional, one shot)

If the programmatic path returns nothing and a supplementary-material URL is known
(e.g. from a publisher landing page in `SourceRetrievalResult.publication_urls`),
you may make **one** `web_fetch` attempt on a tabular/document supplement URL. Do
not loop over many candidate URLs and do not persist temp files.
