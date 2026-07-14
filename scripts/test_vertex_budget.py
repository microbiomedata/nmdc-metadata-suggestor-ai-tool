"""
Test script: call Claude Opus on Vertex AI to verify budget enforcer.

Claude Opus 4.5/4.6 pricing on Vertex AI:
  Input:  $5.00  per 1M tokens
  Output: $25.00 per 1M tokens

With max_tokens=3800, worst-case output cost ≈ $0.095 (9.5 cents).

Prerequisites:
  - Service account JSON key file
  - GOOGLE_APPLICATION_CREDENTIALS env var pointing to it
    (loaded automatically from .env if present)

Usage:
  uv run python scripts/test_vertex_budget.py
"""

import os
import sys

import google.auth.transport.requests
import requests
from dotenv import load_dotenv
from google.oauth2 import service_account

# Load .env file (picks up GOOGLE_APPLICATION_CREDENTIALS, project ID, region)
load_dotenv()

# Configuration — reads from .env or environment
CREDENTIALS_FILE = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
PROJECT_ID = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "638610442344")
REGION = os.environ.get("GCP_REGION", "us-east5")
MODEL_ID = "claude-opus-4-6"

# Pricing (per 1M tokens)
INPUT_PRICE_PER_M = 5.00
OUTPUT_PRICE_PER_M = 25.00

# Target ~$0.095 in output spend → 3,800 tokens
MAX_TOKENS = 3800


def get_access_token(credentials_file: str) -> str:
    """Exchange service account JSON key for a short-lived access token."""
    credentials = service_account.Credentials.from_service_account_file(
        credentials_file,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def call_claude_opus() -> dict:
    """Call Claude Opus on Vertex AI via REST and report token usage/cost."""
    if not CREDENTIALS_FILE:
        print("Error: GOOGLE_APPLICATION_CREDENTIALS is not set.")
        print("Set it in your .env file or environment to the path of your service account JSON key.")
        sys.exit(1)

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"Error: credentials file not found: {CREDENTIALS_FILE}")
        sys.exit(1)

    # Step 1: JSON key → access token
    print(f"Authenticating with: {CREDENTIALS_FILE}")
    token = get_access_token(CREDENTIALS_FILE)

    # Step 2: Build the Vertex AI request
    url = (
        f"https://{REGION}-aiplatform.googleapis.com/v1/"
        f"projects/{PROJECT_ID}/locations/{REGION}/"
        f"publishers/anthropic/models/{MODEL_ID}:rawPredict"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = {
        "anthropic_version": "vertex-2023-10-16",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Write a detailed essay about the history of microbiology, "
                    "from Antonie van Leeuwenhoek's first observations through "
                    "modern genomics. Be thorough and detailed."
                ),
            }
        ],
        "max_tokens": MAX_TOKENS,
    }

    estimated_max_cost = MAX_TOKENS * OUTPUT_PRICE_PER_M / 1_000_000
    print(f"Model:              {MODEL_ID}")
    print(f"Region:             {REGION}")
    print(f"Project:            {PROJECT_ID}")
    print(f"Max output tokens:  {MAX_TOKENS}")
    print(f"Estimated max cost: ${estimated_max_cost:.4f}")
    print()
    print("Sending request...")

    # Step 3: Make the API call
    response = requests.post(url, headers=headers, json=body, timeout=300)
    response.raise_for_status()
    result = response.json()

    # Step 4: Report usage and cost
    usage = result.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    input_cost = input_tokens * INPUT_PRICE_PER_M / 1_000_000
    output_cost = output_tokens * OUTPUT_PRICE_PER_M / 1_000_000
    total_cost = input_cost + output_cost

    print()
    print("=== Results ===")
    print(f"Input tokens:  {input_tokens:,}  (${input_cost:.6f})")
    print(f"Output tokens: {output_tokens:,}  (${output_cost:.6f})")
    print(f"Total cost:    ${total_cost:.6f}")
    print()

    # Preview the response
    content = result.get("content", [{}])[0].get("text", "")
    print(f"Response preview:\n{content[:300]}...")

    return result


if __name__ == "__main__":
    call_claude_opus()
