# Test Fixtures

## doi_test_cases.json

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
