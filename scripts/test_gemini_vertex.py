"""
Test script: call Gemini models on Vertex AI for ENVO biome term suggestion,
then validate the suggested terms against the EBI Ontology Lookup Service (OLS).

Workflow
========
1. Send a made-up environmental sampling abstract to Gemini.
2. Gemini returns ENVO biome term IDs, labels, and justifications as JSON.
3. Each term is validated against the OLS4 REST API:
   - Does the ENVO ID exist?
   - Does the label returned by the model match the canonical OLS label?
4. If any terms fail validation, the OLS lookup results are sent back to
   Gemini so it can self-correct and suggest valid, matching terms.

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

   The key file is the **only** credential you need. The SDK exchanges
   it for a short-lived access token automatically.

3. Install project dependencies::

       uv sync
       uv pip install google-genai

OLS MCP for interactive use
===========================
For interactive ontology lookups inside Claude Code, run the
``/setup-ols-mcp`` slash command to install the OLS MCP server.

Available Gemini models in us-east5
===================================
  Model                 Input $/1M   Output $/1M
  gemini-2.5-pro          $1.25       $10.00
  gemini-2.5-flash        $0.30        $2.50
  gemini-2.0-flash        $0.15        $0.60
  gemini-2.0-flash-lite   $0.075       $0.30

Usage
=====
  uv run python scripts/test_gemini_vertex.py
  uv run python scripts/test_gemini_vertex.py gemini-2.5-flash
  uv run python scripts/test_gemini_vertex.py gemini-2.0-flash
"""

import json
import os
import re
import sys

from dotenv import load_dotenv
import requests as http_requests

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: google-genai is not installed.")
    print("Install it with:  uv pip install google-genai")
    sys.exit(1)

OLS_API_BASE = "https://www.ebi.ac.uk/ols4/api"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

CREDENTIALS_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
PROJECT_ID = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
REGION = os.environ.get("GEMINI_REGION", os.environ.get("CLOUD_ML_REGION"))
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL")

VALID_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

# Pricing per 1M tokens
GEMINI_PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.15, 0.60),
    "gemini-2.0-flash-lite": (0.075, 0.30),
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


def print_config(model: str) -> None:
    """Print the active configuration."""
    pricing = GEMINI_PRICING.get(model, (0.0, 0.0))
    print(f"Project:            {PROJECT_ID}")
    print(f"Region:             {REGION}")
    print(f"Model:              {model}")
    print(f"Pricing:            ${pricing[0]}/1M input, ${pricing[1]}/1M output")
    print(f"Credentials:        {CREDENTIALS_FILE}")
    print()


def strip_markdown_fences(text: str) -> str:
    """Remove ```json ... ``` fences that models love to add."""
    return re.sub(r"^```(?:json)?\s*\n?|```\s*$", "", text.strip(), flags=re.MULTILINE).strip()


def print_results(result_json: str) -> None:
    """Parse and display ENVO biome terms from the model response."""
    result_json = strip_markdown_fences(result_json)
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        print("=== Raw Response (not valid JSON) ===")
        print()
        print(result_json)
        return

    print("=== ENVO Biome Terms (JSON) ===")
    print()
    print(json.dumps(data, indent=2))
    print()


def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> None:
    """Print token usage and estimated cost."""
    pricing = GEMINI_PRICING.get(model, (0.0, 0.0))
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
    """Look up a single ENVO term in OLS4 and return validation info.

    Returns a dict with keys:
      found     — bool, whether the term exists in OLS
      ols_label — the canonical label from OLS (or None)
      envo_id   — echo back the queried ID
    """
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
    """Validate a list of biome terms against OLS.

    Returns (validation_results, all_valid).
    Each result dict has: envo_id, model_label, ols_label, id_valid, label_matches.
    """
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
            print(f"         ID not found in OLS")
        elif not r["label_matches"]:
            print(f"         Model label:  {r['model_label']}")
            print(f"         OLS label:    {r['ols_label']}")
        else:
            print(f"         Label confirmed: {r['ols_label']}")
        print()


# ---------------------------------------------------------------------------
# google-genai SDK — recommended for application code
#
# This is the Google-native equivalent of the AsyncOpenAI pattern used in
# nmdc-mass-spec-automation's LLMClient, but talks directly to Vertex AI
# instead of a gateway.
# ---------------------------------------------------------------------------


def make_gemini_client() -> genai.Client:
    """Create an authenticated Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location=REGION)


def ask_gemini(client: genai.Client, model: str, contents: str, system: str) -> str:
    """Send a request to Gemini and return the response text."""
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=8192,
            temperature=0.4,
        ),
    )

    usage = response.usage_metadata
    input_tokens = (usage.prompt_token_count or 0) if usage else 0
    output_tokens = (usage.candidates_token_count or 0) if usage else 0
    estimate_cost(input_tokens, output_tokens, model)

    return (response.text or "").strip()


def call_gemini(model: str) -> None:
    """Call Gemini, validate ENVO terms via OLS, and self-correct if needed."""
    validate_credentials()
    print_config(model)

    client = make_gemini_client()

    # --- Step 1: Initial suggestion ---
    print("Step 1: Asking Gemini for ENVO biome terms...")
    print()

    text = ask_gemini(client, model, f"Abstract:\n{SAMPLE_ABSTRACT}", SYSTEM_PROMPT)
    text = strip_markdown_fences(text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print("=== Raw Response (not valid JSON) ===")
        print(text)
        return

    biome_terms = data.get("biome_terms", data if isinstance(data, list) else [data])

    print("=== Initial Gemini Response ===")
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

    # --- Step 3: Send failures back to Gemini for correction ---
    print("Step 3: Sending validation failures back to Gemini for correction...")
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

    corrected_text = ask_gemini(client, model, correction_prompt, SYSTEM_PROMPT)
    corrected_text = strip_markdown_fences(corrected_text)

    try:
        corrected_data = json.loads(corrected_text)
    except json.JSONDecodeError:
        print("=== Corrected Response (not valid JSON) ===")
        print(corrected_text)
        return

    print("=== Corrected Gemini Response ===")
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
    model = DEFAULT_MODEL

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        if arg not in VALID_MODELS:
            print(f"Error: unknown model '{arg}'")
            print(f"Valid models: {', '.join(VALID_MODELS)}")
            sys.exit(1)
        model = arg

    print()
    print("=" * 60)
    print("  Gemini on Vertex AI — ENVO Biome Term Suggestion Test")
    print("=" * 60)
    print()

    call_gemini(model)
