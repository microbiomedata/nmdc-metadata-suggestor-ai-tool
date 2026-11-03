"""Store the Langfuse credentials as an E2B secret, for e2b_agent_demo.py --langfuse.

Langfuse authenticates with `Authorization: Basic base64(public_key:secret_key)`.
This stores that base64 value as the E2B secret `langfuse-basic-auth`. The demo then
tells E2B's egress proxy to set the real header on requests to the Langfuse host, so
the secret key itself never enters a sandbox: the sandbox only gets a placeholder.

Reads LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY from the shell or .env.
Run again after rotating the Langfuse keys. --delete removes the secret.

    uv run --extra e2b python scripts/e2b_langfuse_secret.py
    uv run --extra e2b python scripts/e2b_langfuse_secret.py --delete
"""

import argparse
import base64
import sys

from e2b_demo import e2b_api_key, env_value

try:
    from e2b import Secret
except ImportError:
    sys.exit("e2b is not installed. Run: uv sync --extra e2b")

SECRET_NAME = "langfuse-basic-auth"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--delete", action="store_true", help="remove the secret")
    args = parser.parse_args()
    api_key = e2b_api_key()

    if args.delete:
        Secret.destroy(SECRET_NAME, api_key=api_key)
        print(f"deleted E2B secret {SECRET_NAME!r}")
        return

    public, secret = env_value("LANGFUSE_PUBLIC_KEY"), env_value("LANGFUSE_SECRET_KEY")
    if not (public and secret):
        sys.exit("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set.")
    value = base64.b64encode(f"{public}:{secret}".encode()).decode()

    if Secret.exists(SECRET_NAME, api_key=api_key):
        Secret.update(SECRET_NAME, value, api_key=api_key)
        print(f"updated E2B secret {SECRET_NAME!r}")
    else:
        Secret.create(SECRET_NAME, value, metadata={"use": "nmdc-e2b-demo"}, api_key=api_key)
        print(f"created E2B secret {SECRET_NAME!r}")


if __name__ == "__main__":
    main()
