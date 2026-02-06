"""
Test script: call Claude Sonnet/Haiku on Vertex AI for ENVO biome term
suggestion, then validate against the EBI Ontology Lookup Service (OLS).

Workflow
========
1. Send a made-up environmental sampling abstract to Claude.
2. Claude returns ENVO biome term IDs, labels, and justifications as JSON.
3. Each term is validated against the OLS4 REST API:
   - Does the ENVO ID exist?
   - Does the label returned by the model match the canonical OLS label?
4. If any terms fail validation, the OLS lookup results are sent back to
   Claude so it can self-correct and suggest valid, matching terms.

Prerequisites
=============
1. A Google Cloud **service account key** in JSON format.
   - Go to https://console.cloud.google.com/iam-admin/serviceaccounts
   - Select the service account for your project (e.g. nmdc-llm)
   - Keys → Add Key → Create new key → JSON
   - Save the file somewhere safe (NOT inside the repo)

2. Set ``GOOGLE_APPLICATION_CREDENTIALS`` in your ``.env`` file to the
   absolute path of that JSON key file::

       export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-service-account-key.json

   The key file is the **only** credential you need. This script exchanges
   it for a short-lived access token automatically.

3. Install project dependencies (already in pyproject.toml)::

       uv sync

Available Claude models in us-east5
====================================
  Model                          Input $/1M   Output $/1M
  claude-opus-4-6                  $5.00        $25.00
  claude-sonnet-4-5@20250929       $3.00        $15.00
  claude-haiku-4-5@20251001        $1.00         $5.00

Usage
=====
  uv run python scripts/test_claude_vertex.py
  uv run python scripts/test_claude_vertex.py opus
  uv run python scripts/test_claude_vertex.py sonnet
  uv run python scripts/test_claude_vertex.py haiku
"""

import json
import os
import re
import sys

from dotenv import load_dotenv
from google.oauth2 import service_account
import google.auth.transport.requests
import requests as http_requests


OLS_API_BASE = "https://www.ebi.ac.uk/ols4/api"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

CREDENTIALS_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
PROJECT_ID = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "638610442344")
REGION = os.environ.get("CLOUD_ML_REGION", "us-east5")

CLAUDE_MODELS: dict[str, str] = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-5@20250929",
    "haiku": "claude-haiku-4-5@20251001",
}

DEFAULT_ALIAS = "sonnet"

# Pricing per 1M tokens
CLAUDE_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-5@20250929": (3.00, 15.00),
    "claude-haiku-4-5@20251001": (1.00, 5.00),
}

# ---------------------------------------------------------------------------
# Test prompt — made-up abstract → ENVO biome terms
# ---------------------------------------------------------------------------

SAMPLE_ABSTRACT = """\
Soil and rhizosphere microbial communities were sampled along a 200 m \
elevational transect in the Cascade Range of central Oregon. The transect \
spanned a transition from low-elevation dry ponderosa pine woodland \
through mixed-conifer forest to sub-alpine meadow near the treeline at \
approximately 2,100 m elevation. Samples were collected from the upper \
10 cm of soil at five stations during late July 2024. Each station was \
characterized by distinct vegetation cover: bunchgrass-dominated openings \
in the pine woodland, dense Douglas-fir and western hemlock canopy at \
mid-elevation, and alpine bunchgrass and wildflower meadows at the \
highest site. Environmental metadata including soil pH, moisture content, \
and canopy cover were recorded at each sampling point.\
"""

SYSTEM_PROMPT = """\
You are an expert environmental ontology curator. Given a scientific \
abstract describing environmental sampling, return the most appropriate \
ENVO (Environment Ontology) terms for the biome(s) the samples were \
taken from.

For each biome, return:
- The ENVO term ID (e.g. ENVO:01000174)
- The label (e.g. "temperate coniferous forest biome")
- A one-sentence justification citing the abstract

Return your answer as JSON with the key "biome_terms", where each entry \
has "envo_id", "label", and "justification". Return only valid JSON, no \
markdown fences.\
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def validate_credentials() -> str:
    """Check that the credentials file exists and return its path."""
    if not CREDENTIALS_FILE:
        print("Error: GOOGLE_APPLICATION_CREDENTIALS is not set.")
        print("Add this to your .env file:")
        print("  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-key.json")
        sys.exit(1)
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"Error: credentials file not found: {CREDENTIALS_FILE}")
        sys.exit(1)
    return CREDENTIALS_FILE


def get_access_token(credentials_file: str) -> str:
    """Exchange the service account JSON key for a short-lived access token."""
    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def print_config(model_id: str) -> None:
    """Print the active configuration."""
    pricing = CLAUDE_PRICING.get(model_id, (0.0, 0.0))
    print(f"Project:            {PROJECT_ID}")
    print(f"Region:             {REGION}")
    print(f"Model:              {model_id}")
    print(f"Pricing:            ${pricing[0]}/1M input, ${pricing[1]}/1M output")
    print(f"Credentials:        {CREDENTIALS_FILE}")
    print()


def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` fences that models love to add."""
    return re.sub(r"^```(?:json)?\s*\n?|```\s*$", "", text.strip(), flags=re.MULTILINE).strip()


def estimate_cost(input_tokens: int, output_tokens: int, model_id: str) -> None:
    """Print token usage and estimated cost."""
    pricing = CLAUDE_PRICING.get(model_id, (0.0, 0.0))
    input_cost = input_tokens * pricing[0] / 1_000_000
    output_cost = output_tokens * pricing[1] / 1_000_000
    total = input_cost + output_cost
    print(f"Input tokens:  {input_tokens:,}  (${input_cost:.6f})")
    print(f"Output tokens: {output_tokens:,}  (${output_cost:.6f})")
    print(f"Total cost:    ${total:.6f}")
    print()


# ---------------------------------------------------------------------------
# OLS validation — check ENVO terms against EBI Ontology Lookup Service
# ---------------------------------------------------------------------------


def envo_id_to_iri(envo_id: str) -> str:
    """Convert 'ENVO:01000174' → 'http://purl.obolibrary.org/obo/ENVO_01000174'."""
    return "http://purl.obolibrary.org/obo/" + envo_id.replace(":", "_")


def lookup_envo_term(envo_id: str) -> dict:
    """Look up a single ENVO term in OLS4 and return validation info."""
    iri = envo_id_to_iri(envo_id)
    url = f"{OLS_API_BASE}/ontologies/envo/terms"
    try:
        resp = http_requests.get(url, params={"iri": iri}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        terms = data.get("_embedded", {}).get("terms", [])
        if terms:
            return {"found": True, "ols_label": terms[0].get("label"), "envo_id": envo_id}
    except Exception as exc:
        print(f"  Warning: OLS lookup failed for {envo_id}: {exc}")
    return {"found": False, "ols_label": None, "envo_id": envo_id}


def validate_terms(biome_terms: list[dict]) -> tuple[list[dict], bool]:
    """Validate a list of biome terms against OLS."""
    results = []
    all_valid = True
    for term in biome_terms:
        envo_id = term.get("envo_id", "")
        model_label = term.get("label", "")
        ols = lookup_envo_term(envo_id)

        id_valid = ols["found"]
        ols_label = ols["ols_label"]
        label_matches = (
            id_valid and ols_label is not None and ols_label.lower() == model_label.lower()
        )

        if not id_valid or not label_matches:
            all_valid = False

        results.append(
            {
                "envo_id": envo_id,
                "model_label": model_label,
                "ols_label": ols_label,
                "id_valid": id_valid,
                "label_matches": label_matches,
            }
        )
    return results, all_valid


def print_validation(results: list[dict]) -> None:
    """Print the OLS validation results."""
    for r in results:
        status = "PASS" if (r["id_valid"] and r["label_matches"]) else "FAIL"
        print(f"  [{status}] {r['envo_id']}")
        if not r["id_valid"]:
            print("         ID not found in OLS")
        elif not r["label_matches"]:
            print(f"         Model label:  {r['model_label']}")
            print(f"         OLS label:    {r['ols_label']}")
        else:
            print(f"         Label confirmed: {r['ols_label']}")
        print()


# ---------------------------------------------------------------------------
# Claude on Vertex AI via Anthropic rawPredict REST API
# ---------------------------------------------------------------------------


def ask_claude(token: str, model_id: str, user_content: str, system: str) -> str:
    """Send a request to Claude on Vertex AI and return the response text."""
    url = (
        f"https://{REGION}-aiplatform.googleapis.com/v1/"
        f"projects/{PROJECT_ID}/locations/{REGION}/"
        f"publishers/anthropic/models/{model_id}:rawPredict"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "anthropic_version": "vertex-2023-10-16",
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
        "max_tokens": 4096,
    }

    response = http_requests.post(url, headers=headers, json=body, timeout=300)
    response.raise_for_status()
    result = response.json()

    usage = result.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    estimate_cost(input_tokens, output_tokens, model_id)

    content = result.get("content", [{}])
    return "".join(block.get("text", "") for block in content).strip()


def call_claude(model_id: str) -> None:
    """Call Claude, validate ENVO terms via OLS, and self-correct if needed."""
    creds_file = validate_credentials()
    print_config(model_id)

    print("Authenticating with service account key...")
    token = get_access_token(creds_file)
    print()

    # --- Step 1: Initial suggestion ---
    print("Step 1: Asking Claude for ENVO biome terms...")
    print()

    text = ask_claude(token, model_id, f"Abstract:\n{SAMPLE_ABSTRACT}", SYSTEM_PROMPT)
    text = strip_markdown_fences(text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print("=== Raw Response (not valid JSON) ===")
        print(text)
        return

    biome_terms = data.get("biome_terms", data if isinstance(data, list) else [data])

    print("=== Initial Claude Response ===")
    print()
    print(json.dumps(data, indent=2))
    print()

    # --- Step 2: Validate against OLS ---
    print("Step 2: Validating terms against OLS (EBI Ontology Lookup Service)...")
    print()

    results, all_valid = validate_terms(biome_terms)
    print_validation(results)

    if all_valid:
        print("All terms validated successfully.")
        return

    # --- Step 3: Send failures back to Claude for correction ---
    print("Step 3: Sending validation failures back to Claude for correction...")
    print()

    correction_prompt = (
        "I asked you to suggest ENVO biome terms for the following abstract, "
        "and then I validated your suggestions against the EBI Ontology Lookup "
        "Service (OLS). Some of your terms did not pass validation.\n\n"
        f"Abstract:\n{SAMPLE_ABSTRACT}\n\n"
        f"Your original suggestions:\n{json.dumps(biome_terms, indent=2)}\n\n"
        f"OLS validation results:\n{json.dumps(results, indent=2)}\n\n"
        "For each term that failed validation:\n"
        "- If the ID was not found, suggest a real ENVO term ID that exists in OLS.\n"
        "- If the label did not match, use the correct OLS label for that ID, or "
        "pick a different ID whose label better fits the abstract.\n\n"
        "Return the complete corrected list as JSON with the same format: "
        '{"biome_terms": [{"envo_id": "...", "label": "...", "justification": "..."}]}. '
        "Return only valid JSON, no markdown fences."
    )

    corrected_text = ask_claude(token, model_id, correction_prompt, SYSTEM_PROMPT)
    corrected_text = strip_markdown_fences(corrected_text)

    try:
        corrected_data = json.loads(corrected_text)
    except json.JSONDecodeError:
        print("=== Corrected Response (not valid JSON) ===")
        print(corrected_text)
        return

    print("=== Corrected Claude Response ===")
    print()
    print(json.dumps(corrected_data, indent=2))
    print()

    # --- Step 4: Re-validate corrected terms ---
    corrected_terms = corrected_data.get(
        "biome_terms", corrected_data if isinstance(corrected_data, list) else [corrected_data]
    )

    print("Step 4: Re-validating corrected terms against OLS...")
    print()

    results2, all_valid2 = validate_terms(corrected_terms)
    print_validation(results2)

    if all_valid2:
        print("All corrected terms validated successfully.")
    else:
        print("Some terms still failed validation. Manual review recommended.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    alias = DEFAULT_ALIAS
    valid_aliases = list(CLAUDE_MODELS.keys())

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        if arg not in valid_aliases:
            print(f"Error: unknown model alias '{arg}'")
            print(f"Valid aliases: {', '.join(valid_aliases)}")
            sys.exit(1)
        alias = arg

    model_id = CLAUDE_MODELS[alias]

    print()
    print("=" * 60)
    print("  Claude on Vertex AI — ENVO Biome Term Suggestion Test")
    print("=" * 60)
    print()

    call_claude(model_id)
