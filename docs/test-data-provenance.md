# Test Data Provenance

## tests/fixtures/doi_test_cases.json

Curated DOI test cases from the NMDC MongoDB DOI inventory, covering all 23 DOI prefixes
found in NMDC data plus bogus/malformed DOIs for negative testing.

### Provenance

The full provenance chain:

1. **NMDC production MongoDB** on Google Cloud Platform
2. **Nightly GCP backup** created ~6pm PT on **2026-01-15** (dump `20260116_020423`)
3. **Eric Cavanna uploaded to NERSC** on 2026-01-16, announced in NMDC Slack `#infra-admin`:
   `/global/cfs/cdirs/m3408/nmdc-mongodumps/from_google_cloud/nmdc-runtime-prod-mongo-backup/20260116_020423`
4. **Mark rsynced to local SSD** then **restored to `localhost:27017/nmdc`** (no auth) via
   `~/Desktop/nmdc_mongorestore_20260116.sh` (32 of 132 collections; operational/minting skipped)
5. **Inventory TSV generated** on **2026-02-11** by scanning `study_set`, `biosample_set`,
   and `material_processing_set` for DOI references
6. **This fixture curated** on **2026-02-17** from the inventory TSV

**Verification:** The local MongoDB has 14,938 biosamples. This matches the `20260116_020423`
dump exactly. (Confirmed by counting directly from NERSC dumps via `bsondump`:
`20251219`=13,847; `20260116`=14,938; `20260131`=14,857.
See NMDC Slack `#infra-admin` message from 2026-02-10.)

The inventory TSV (`nmdc_mongodb_doi_inventory.tsv`) is **not published to GitHub**. It exists
only in a local `nmdc-schema` checkout. The generation script is in
[external-metadata-awareness PR #307](https://github.com/microbiomedata/external-metadata-awareness/pull/307).

**Inventory counts** (`inventory_count` field) reflect the number of rows in the source TSV
sharing each DOI prefix. These are reference counts (how many NMDC documents cite a DOI with
that prefix), not unique DOI counts.

### Available NERSC dumps

As of 2026-02-17, four dumps exist at the NERSC path above:

| Dump | biosample_set count | Notes |
|------|-------------------|-------|
| `20251219_020011` | 13,847 | |
| `20260116_020423` | 14,938 | **Used for this fixture** |
| `20260131_020013` | 14,857 | Count decreased (known; discussed in Slack) |
| `20260213_020017` | unknown | Most recent |

### Reproducing the counts

From the source TSV:

```bash
awk -F'\t' 'NR>1 {
  split($5, a, ":");
  prefix = a[2];
  sub(/\/.*/, "", prefix);
  print prefix
}' nmdc_mongodb_doi_inventory.tsv | sort | uniq -c | sort -rn
```

From MongoDB directly (requires local NMDC MongoDB at localhost:27017):

```bash
mongosh --quiet nmdc --eval '
  ["study_set", "biosample_set", "material_processing_set"].forEach(coll => {
    db[coll].find({}, {id:1, associated_dois:1, protocol_link:1}).forEach(doc => {
      (doc.associated_dois || []).forEach(d => {
        if (d.doi_value) print(coll + "\t" + doc.id + "\t" + d.doi_value);
      });
      if (doc.protocol_link && doc.protocol_link.url)
        print(coll + "\t" + doc.id + "\t" + doc.protocol_link.url);
    });
  });
'
```

### Structure

Each test case has:

| Field | Description |
|-------|-------------|
| `doi` | Bare DOI string (no `doi:` prefix, no URL wrapper) |
| `valid` | Whether the DOI is expected to resolve |
| `prefix` | DOI prefix (e.g., `10.1038`) |
| `inventory_count` | Reference count in NMDC inventory for this prefix |
| `provider` | Publisher or repository name |
| `registration_agency` | Expected RA: `Crossref`, `DataCite`, or `null` |
| `expected_resource_type` | Expected type from RA API (e.g., `journal-article`, `Dataset`) |
| `expected_nmdc_category` | Expected NMDC DoiCategoryEnum value |
| `nmdc_collection` | MongoDB collection where this DOI appears |
| `nmdc_entity_id` | NMDC identifier of the document containing this DOI |
| `nmdc_path` | JSON path within the document (e.g., `associated_dois[1]`, `protocol_link`) |
| `issue_scope` | Which GitHub issue this DOI is relevant to (`#1597`, `#1599`, `#1592`, or `null`) |
| `notes` | Human-readable context, edge cases, known API behaviors |

### Coverage

- **23 unique prefixes** from the NMDC inventory
- **6 assigned publisher prefixes** for issue #1597: 10.4319, 10.1038, 10.3389, 10.1016, 10.2136, 10.17504
- **Both registration agencies**: Crossref and DataCite
- **All 4 NMDC DOI categories**: publication_doi, dataset_doi, award_doi, (data_management_plan_doi not yet in inventory)
- **3 NMDC collections**: study_set, biosample_set, material_processing_set
- **Edge cases**: old DOI format with parentheses, preprints, book chapters, video journals, protocol DOIs
- **4 bogus DOIs**: nonexistent prefix, valid prefix with fake suffix, malformed string, empty string

## tests/fixtures/nmdc_dois_with_biosamples.tsv

Every DOI-to-biosample mapping in NMDC, generated from the BERDL data lakehouse.

### Provenance

1. **Source database**: `nmdc_flattened_biosamples` in the BERDL lakehouse (NMDC tenant)
2. **Upstream pipeline**: Produced by a DuckDB pipeline in
   [external-metadata-awareness](https://github.com/microbiomedata/external-metadata-awareness),
   loaded into BERDL as Delta Lake tables on **2026-02-18**
3. **Tables joined**:
   - `flattened_study_associated_dois` (71 rows — one per study-DOI association)
   - `flattened_biosample` (14,938 rows, 281 columns)
4. **Generated**: 2026-03-06 via PySpark on BERDL JupyterHub

### Query

```sql
SELECT
    d.study_id,
    d.doi_value,
    d.doi_category,
    b.id AS biosample_id
FROM nmdc_flattened_biosamples.flattened_study_associated_dois d
JOIN nmdc_flattened_biosamples.flattened_biosample b
    ON b.associated_studies = d.study_id
ORDER BY d.study_id, d.doi_value, b.id
```

### Counts

| Metric | Count |
|--------|-------|
| Rows (biosample-DOI pairs) | 16,690 |
| Unique DOIs | 71 |
| Unique studies | 23 |
| Unique biosamples | 4,775 |

Not all 14,938 biosamples appear — only those whose study has at least one DOI.

### Structure

| Column | Description |
|--------|-------------|
| `study_id` | NMDC study identifier (e.g., `nmdc:sty-11-076c9980`) |
| `doi_value` | DOI with `doi:` prefix (e.g., `doi:10.25585/1488160`) |
| `doi_category` | NMDC category: `publication_doi`, `dataset_doi`, or `award_doi` |
| `biosample_id` | NMDC biosample identifier (e.g., `nmdc:bsm-11-02x97z84`) |

### How to regenerate

**Option A: From BERDL lakehouse (authoritative)**

1. Log into [BERDL JupyterHub](https://hub.berdl.kbase.us/) (ORCID auth)
2. Open a terminal or notebook
3. Run the SQL query above via PySpark:

```python
from berdl_notebook_utils import get_spark_session

spark = get_spark_session()
df = spark.sql("""
    SELECT d.study_id, d.doi_value, d.doi_category, b.id AS biosample_id
    FROM nmdc_flattened_biosamples.flattened_study_associated_dois d
    JOIN nmdc_flattened_biosamples.flattened_biosample b
        ON b.associated_studies = d.study_id
    ORDER BY d.study_id, d.doi_value, b.id
""")
df.toPandas().to_csv("nmdc_dois_with_biosamples.tsv", sep="\t", index=False)
```

4. Download the TSV and replace `tests/fixtures/nmdc_dois_with_biosamples.tsv`

**Option B: From local MongoDB + external-metadata-awareness**

1. Restore an NMDC MongoDB dump locally (see doi_test_cases.json provenance above)
2. Run `extract-nmdc-doi-inventory` from
   [external-metadata-awareness](https://github.com/microbiomedata/external-metadata-awareness)
   to produce a DOI inventory TSV
3. That TSV has DOIs and study IDs but **not** biosample IDs — you'd need a
   separate MongoDB query to join `study_set` → `biosample_set`

### Usage with batch DOI retrieval

```bash
make get-abstracts-from-file FILE=tests/fixtures/nmdc_dois_with_biosamples.tsv OUT=results/
```

The `doi_value` column is auto-detected. Duplicate DOIs (same DOI appearing for
multiple biosamples) are deduplicated — only 71 unique DOIs are fetched.
