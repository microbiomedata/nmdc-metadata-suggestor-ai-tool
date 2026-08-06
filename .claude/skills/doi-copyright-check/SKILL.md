---
name: doi-copyright-check
description: Use this skill to check whether a DOI's publication content may be used as AI context (not training). Returns a verdict based on the Creative Commons license reported by the DOI source metadata.
---

# DOI Copyright Check Skill

Use this skill **after** the **doi-ingestion** skill has returned metadata for a DOI, to determine whether the publication content may be used as AI inference context.

> **Scope:** This check covers using content as **context passed to an LLM at inference time** (e.g., summarization, metadata suggestion). It does NOT cover model training, fine-tuning, or retention of the content — those uses carry stricter obligations not addressed here.

---

## Background

Open Access (OA) status and AI usability are **not the same thing**:

- A paper being OA (author paid for open access) does not automatically grant AI usage rights.
- The API or index that surfaced the DOI (OpenAlex, DataCite, etc.) having permissive API terms does **not** transfer to the publication content itself.
- The relevant check is the **Creative Commons (CC) license on the article**, not the OA flag or the API terms.

---

## Decision Rules

Evaluate the license string from the DOI metadata (typically in `result.context`, the abstract, or a license field returned by the source):

| License | AI Context Use Allowed? | Condition |
|---|---|---|
| `CC BY 4.0` / `CC-BY` | Yes | Attribute the original authors and source |
| `CC BY-SA 4.0` | Yes | Attribute; any derived work must carry the same license |
| `CC BY-ND 4.0` | Yes (read-only context) | Attribute; do not modify or redistribute the content itself |
| `CC BY-NC 4.0` | Yes, PNNL internal use only | Non-commercial; attribute |
| `CC BY-NC-SA` / `CC BY-NC-ND` | Yes, PNNL internal use only | Non-commercial; apply SA/ND constraints above |
| `CC0` / Public Domain | Yes | No attribution required |
| All rights reserved / no CC license found | **No** — skip this content | Cannot use without explicit publisher permission |
| License unclear / not found | **Uncertain** — flag for review | Do not use; surface the DOI for manual check |

**Rule of thumb:** If the license starts with `CC BY` or is `CC0`, you can use the content. If there is no CC license, do not use it.

---

## Steps

### 1 — Locate the license

Check these locations in order:

1. A `license` or `rights` field in the DOI source metadata (DataCite, CrossRef, OpenAlex all return this).
2. The abstract/description text itself — publishers often embed the CC notice in the full text (look for phrases like `"licensed under a Creative Commons"`, `"CC BY"`, `"http://creativecommons.org/licenses/"`).
3. If neither is found, treat as **Uncertain**.

### 2 — Apply the decision table above

Map the found license string to the Allowed/No/Uncertain verdict.

### 3 — Return a structured result

```python
{
    "doi": "<doi value>",
    "license_found": "<license string or None>",
    "verdict": "allowed" | "not_allowed" | "uncertain",
    "condition": "<attribution or other constraint, or None>",
    "note": "<short human-readable explanation>"
}
```

---

## Integration with build-study-context

After calling **doi-ingestion** for each DOI:

1. Run this check on the returned metadata.
2. If `verdict == "allowed"`: include `result.context` in `context_texts` as normal.
3. If `verdict == "not_allowed"`: **exclude** the content; log a warning with the DOI and reason.
4. If `verdict == "uncertain"`: **exclude** the content; surface the DOI in a `skipped_dois` list for the caller to review.

---

## Important caveats

- **Author self-use:** An author using their own non-OA paper may have retained rights under their individual copyright agreement, but this tool cannot verify that. Treat non-CC content as `not_allowed` regardless.
- **This is not legal advice.** The legal landscape around AI and copyright is actively evolving. When in doubt, escalate to the PNNL library team before using restricted content.
- **Training vs. context:** These rules apply only to inference-time context. Using content to train or fine-tune a model, or retaining a copy of the content, requires a separate and stricter review.
