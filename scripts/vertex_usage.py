#!/usr/bin/env python3
"""Query Vertex AI token usage and estimate costs.

Usage:
    python scripts/vertex_usage.py [--days N] [--project PROJECT_ID]

Requires: gcloud CLI authenticated with access to the project.
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# Pricing per 1M tokens (USD)
# Source: https://platform.claude.com/docs/en/about-claude/pricing
#         https://cloud.google.com/vertex-ai/generative-ai/pricing
PRICING = {
    "claude-opus-4-6":       {"input": 5.00,  "output": 25.00},
    "claude-opus-4-5":       {"input": 5.00,  "output": 25.00},
    "claude-opus-4-1":       {"input": 15.00, "output": 75.00},
    "claude-opus-4":         {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-5":     {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4":       {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5":      {"input": 1.00,  "output": 5.00},
    "claude-haiku-3-5":      {"input": 0.80,  "output": 4.00},
    "gemini-2.0-flash":      {"input": 0.15,  "output": 0.60},
    "gemini-2.0-flash-001":  {"input": 0.15,  "output": 0.60},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-2.0-flash-lite-001": {"input": 0.075, "output": 0.30},
    "gemini-2.5-flash":      {"input": 0.30,  "output": 2.50},
    "gemini-2.5-pro":        {"input": 1.25,  "output": 10.00},
}

# Cache pricing multipliers (relative to base input price)
# Source: https://platform.claude.com/docs/en/about-claude/pricing
CACHE_MULTIPLIERS = {
    "input":                1.0,    # standard input
    "cache_read_input":     0.1,    # cache hits: 90% cheaper
    "cache_write_input":    1.25,   # 5-min cache write (default)
    "cache_write_5m_input": 1.25,   # 5-min cache write (explicit)
    "cache_write_1h_input": 2.0,    # 1-hour cache write
}
OUTPUT_TYPES = {"output"}


def get_access_token():
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error getting access token: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def query_token_usage(project_id, days):
    token = get_access_token()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    url = (
        f"https://monitoring.googleapis.com/v3/projects/{project_id}/timeSeries"
        f"?filter=metric.type%3D%22aiplatform.googleapis.com%2Fpublisher%2Fonline_serving%2Ftoken_count%22"
        f"&interval.startTime={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"&interval.endTime={now.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )

    result = subprocess.run(
        ["curl", "-s", "-H", f"Authorization: Bearer {token}", url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error querying monitoring API: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    return json.loads(result.stdout)


def aggregate(data):
    """Aggregate token counts by model and token type."""
    totals = defaultdict(lambda: defaultdict(int))
    for ts in data.get("timeSeries", []):
        model = ts["resource"]["labels"].get("model_user_id", "unknown")
        token_type = ts["metric"]["labels"].get("type", "unknown")
        for point in ts.get("points", []):
            val = int(point["value"].get("int64Value", 0))
            totals[model][token_type] += val
    return totals


def compute_input_cost(model, token_type, count):
    """Compute cost for an input token type, applying cache multipliers."""
    prices = PRICING.get(model)
    if not prices:
        return 0.0
    multiplier = CACHE_MULTIPLIERS.get(token_type, 1.0)
    return (count / 1_000_000) * prices["input"] * multiplier


def main():
    parser = argparse.ArgumentParser(description="Query Vertex AI token usage and estimate costs")
    parser.add_argument("--days", type=int, default=7, help="Number of days to query (default: 7)")
    parser.add_argument("--project", type=str, default="nmdc-llm", help="GCP project ID (default: nmdc-llm)")
    args = parser.parse_args()

    print(f"Querying Vertex AI usage for project '{args.project}' (last {args.days} days)...\n")

    data = query_token_usage(args.project, args.days)
    totals = aggregate(data)

    if not totals:
        print("No token usage found.")
        return

    # Build per-model summary
    model_summary = {}
    for model, types in totals.items():
        input_tokens = types.get("input", 0)
        cache_read_tokens = types.get("cache_read_input", 0)
        cache_write_tokens = (
            types.get("cache_write_input", 0)
            + types.get("cache_write_5m_input", 0)
            + types.get("cache_write_1h_input", 0)
        )
        output_tokens = types.get("output", 0)

        # Compute costs with correct cache multipliers
        input_cost = 0.0
        for token_type, count in types.items():
            if token_type in CACHE_MULTIPLIERS:
                input_cost += compute_input_cost(model, token_type, count)

        output_cost = 0.0
        prices = PRICING.get(model)
        if prices:
            output_cost = (output_tokens / 1_000_000) * prices["output"]

        model_summary[model] = {
            "input_tokens": input_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "output_tokens": output_tokens,
            "input_cost": input_cost,
            "output_cost": output_cost,
            "total_cost": input_cost + output_cost,
        }

    # Sort by total cost descending
    sorted_models = sorted(model_summary.items(), key=lambda x: x[1]["total_cost"], reverse=True)

    # Check if any caching is happening
    has_cache = any(
        s["cache_read_tokens"] > 0 or s["cache_write_tokens"] > 0
        for _, s in sorted_models
    )

    # Print table
    if has_cache:
        header = (
            f"{'Model':<28} {'Input':>12} {'Cache Read':>12} {'Cache Write':>12} "
            f"{'Output':>12} {'Input $':>10} {'Output $':>10} {'Total $':>10}"
        )
    else:
        header = f"{'Model':<28} {'Input Tokens':>14} {'Output Tokens':>14} {'Input Cost':>12} {'Output Cost':>12} {'Total Cost':>12}"

    print(header)
    print("-" * len(header))

    grand_total = 0.0
    for model, s in sorted_models:
        if s["input_tokens"] == 0 and s["output_tokens"] == 0 and s["cache_read_tokens"] == 0:
            continue

        fmt_cost = lambda v: f"${v:.4f}" if v > 0 else "-"

        if has_cache:
            print(
                f"{model:<28} {s['input_tokens']:>12,} {s['cache_read_tokens']:>12,} "
                f"{s['cache_write_tokens']:>12,} {s['output_tokens']:>12,} "
                f"{fmt_cost(s['input_cost']):>10} {fmt_cost(s['output_cost']):>10} "
                f"{fmt_cost(s['total_cost']):>10}"
            )
        else:
            print(
                f"{model:<28} {s['input_tokens']:>14,} {s['output_tokens']:>14,} "
                f"{fmt_cost(s['input_cost']):>12} {fmt_cost(s['output_cost']):>12} "
                f"{fmt_cost(s['total_cost']):>12}"
            )
        grand_total += s["total_cost"]

    print("-" * len(header))
    if has_cache:
        print(f"{'TOTAL':<28} {'':>12} {'':>12} {'':>12} {'':>12} {'':>10} {'':>10} {'$' + f'{grand_total:.4f}':>10}")
    else:
        print(f"{'TOTAL':<28} {'':>14} {'':>14} {'':>12} {'':>12} {'$' + f'{grand_total:.4f}':>12}")

    # Pricing note
    print(f"\nNote: Costs are estimates based on published API pricing.")
    print(f"      Cache read tokens are priced at 0.1x base input rate.")
    print(f"      Cache write tokens are priced at 1.25x (5min) or 2x (1hr) base input rate.")
    print(f"      Vertex AI regional endpoints may include a 10% premium.")
    print(f"      Actual billing may differ — check Cloud Billing for exact costs.")


if __name__ == "__main__":
    main()
