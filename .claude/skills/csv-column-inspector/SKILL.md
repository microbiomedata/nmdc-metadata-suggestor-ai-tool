---
name: csv-column-inspector
description: Use this skill to retrieve all distinct values for a specific column in an uploaded CSV file, when the inline sample values in the prompt are not enough to determine the correct mapping (e.g. to enumerate permissible values, check date format variation, or assess controlled vocabulary coverage).
---

# CSV Column Inspector

The initial prompt includes a small subset of values per column. Use this skill when you need
more — for example, to enumerate all distinct values for a column that maps to an NMDC
enumeration, or to check whether a date column uses a consistent format across all rows.

## How to use

The uploaded CSV files are available on disk. Each file is identified by its `file_id`
(shown in the prompt as `id: <file_id>`). To find the actual path for a file_id, use Grep:

```
Grep pattern="<file_id>" include="*.csv"
```

Or read the file directly if the path is known. To extract all distinct non-empty values
for a column named `purpose_of_sampling` from a CSV at `/tmp/uploads/abc123.csv`:

```bash
uv run python -c "
import csv
vals = set()
with open('/tmp/uploads/abc123.csv', newline='', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        v = (row.get('purpose_of_sampling') or '').strip()
        if v:
            vals.add(v)
for v in sorted(vals):
    print(v)
"
```

Replace the file path and column name as needed. The output gives you the full distinct
value set to compare against NMDC slot permissible values or to confirm format consistency.

## When to use

- A column appears to map to an NMDC enumeration and you want to verify all source values
  before assigning a transformation rule.
- The 3 inline samples show inconsistent date or unit formats and you need to check the
  full range.
- A column has only empty samples in the prompt (no `[e.g. ...]`) and you want to confirm
  it is truly empty vs. populated later in the file.

## When not to use

- The inline samples already give enough signal for a confident mapping — don't call this
  for every column, only where ambiguity remains after reviewing the prompt context.
