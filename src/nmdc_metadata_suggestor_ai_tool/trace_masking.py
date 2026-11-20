"""Mask credential-shaped values before traces reach Langfuse.

Two hooks are registered in ``tracing.py``:

* ``mask``: applied to all data sent through the Langfuse SDK (``@observe``, ``create_event``).
* ``mask_otel_spans``: applied to spans from the Claude Agent SDK instrumentor, which
  ``mask`` never sees.

Known token formats come from the rule file of a pinned gitleaks release
(``GITLEAKS_VERSION``), bundled unmodified as ``gitleaks.toml`` with its MIT license in
``gitleaks.LICENSE``. Gitleaks is feature complete and takes security patches only, so
bump the version only when a patch changes a rule listed in ``GITLEAKS_RULES``.

Custom patterns cover what those rules do not: private keys cut off before their END
line, ``Authorization`` headers, passwords in connection URLs, the arguments to
``mc alias set``, ``sk-`` keys without a vendor marker (CBORG and other LiteLLM gateway
keys), Google OAuth access tokens (``ya29.``), and values assigned to credential-named
keys.

A masked value becomes ``[REDACTED:<pattern name>]``. Placeholders such as ``<your-key>``,
``${API_KEY}`` or ``YOUR-ACCESS-KEY-HERE`` are left alone. Emails are not masked, because
submission metadata carries contact emails the suggester is meant to read.

Known gaps, all intentional. For values assigned to a credential-named key
(``keyed_value``):

* values shorter than 8 characters are not masked;
* all-digit values are not masked (they are counts and ids far more often than secrets);
* a quoted value containing spaces is masked only up to its first space, and not at all
  when that first part is shorter than 8 characters.
"""

import re
import tomllib
from importlib.resources import files
from typing import Any

from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch

GITLEAKS_VERSION = "v8.30.1"  # https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1

# Rule ids taken from gitleaks.toml. Every id must exist there and compile in Python;
# load_gitleaks_patterns raises otherwise, so a version bump cannot drop one silently.
GITLEAKS_RULES = (
    "anthropic-api-key",
    "anthropic-admin-api-key",
    "openai-api-key",
    "aws-access-token",
    "github-pat",
    "github-fine-grained-pat",
    "github-oauth",
    "github-app-token",
    "github-refresh-token",
    "slack-bot-token",
    "slack-user-token",
    "slack-app-token",
    "slack-legacy-token",
    "slack-legacy-bot-token",
    "slack-legacy-workspace-token",
    "slack-config-access-token",
    "slack-config-refresh-token",
    "gcp-api-key",
    "jwt",
)

_PLACEHOLDER = re.compile(
    r"""^(?:<[^<>]*>|\$\{?\w+\}?|\*{3,}|x{4,}|\.{3}|your[-_\w]*|changeme|example\w*
        |placeholder|redacted|\[redacted[^\]]*\])$""",
    re.IGNORECASE | re.VERBOSE,
)

# A variable name that says it holds a credential.
_CREDENTIAL_NAME = (
    r"[A-Za-z0-9_.-]*(?:(?:api|access|secret|private|client)[_-]?key|secret|token(?!s)"
    r"|passw(?:or)?d|pwd|credentials?)[A-Za-z0-9_]*"
)

# Quotes around a key or value may be escaped once or twice, because tool output often
# arrives as JSON inside JSON.
_KEYED_VALUE = re.compile(
    r"\b"
    + _CREDENTIAL_NAME
    + r"""
    (?:\\{0,2}["'\]])*           # closing quote or bracket of the key, maybe escaped
    \s* (?: : | = | := ) \s*     # assignment
    (?:\\{0,2}["'])?             # opening quote of the value, maybe escaped
    (?P<value>[^\s"'\\,;}\])(]{8,})   # the value: 8+ chars up to a space, quote or delimiter
    (?![\w./+=-]*\()             # not a function call, such as os.environ.get(
    """,
    re.IGNORECASE | re.VERBOSE,
)


def load_gitleaks_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Compile the ``GITLEAKS_RULES`` rules from the bundled gitleaks.toml, in that order."""
    text = files(__package__).joinpath("gitleaks.toml").read_text(encoding="utf-8")
    by_id = {rule["id"]: rule for rule in tomllib.loads(text).get("rules", [])}
    missing = [rule_id for rule_id in GITLEAKS_RULES if rule_id not in by_id]
    if missing:
        raise ValueError(f"gitleaks {GITLEAKS_VERSION} has no rule {missing}")
    # A Go-only construct raises re.error here rather than being skipped.
    return [(rule_id, re.compile(by_id[rule_id]["regex"])) for rule_id in GITLEAKS_RULES]


# (name, pattern). Group "value", or else group 1, is the part that gets replaced (gitleaks
# rules capture the token in group 1 and also match the delimiter after it); with neither,
# the whole match is. Order matters: earlier patterns run first, and later ones skip text
# that is already masked.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
    ),
    (
        "auth_header",
        re.compile(
            r"(?i)\b(?:authorization\s*[:=]\s*[\"']?(?:bearer|basic|token)|bearer)\s+(?P<value>[A-Za-z0-9._~+/=-]{16,})"
        ),
    ),
    (
        "uri_password",
        re.compile(
            # The authority ends at ? or #, and RFC 3986 allows ' in a password.
            r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@'\"\\?#]{1,64}:(?P<value>[^\s@/\"\\?#]{1,128})@"
        ),
    ),
    # Everything after the URL, to the end of the logical line: a quoted key with a space
    # or a backslash-newline would shift the arguments if they were matched one by one.
    ("mc_alias", re.compile(r"\bmc\s+alias\s+set\s+\S+\s+\S+\s+(?P<value>(?:\\\n|[^\n])+)")),
    *load_gitleaks_patterns(),
    ("sk_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("google_oauth_token", re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}")),
    ("keyed_value", _KEYED_VALUE),
]

_MASK_PREFIX = "[REDACTED:"

# A URL or a file path next to a credential name (token_uri, credentials_file) is
# where a credential lives, not the credential.
_LOCATION = re.compile(r"^(?:https?://|/|~/|\./)")


def _is_placeholder(value: str) -> bool:
    words = [w for w in value.split() if w != "\\"]
    if len(words) > 1:
        return all(_is_placeholder(w) for w in words)
    return (
        bool(_PLACEHOLDER.match(value) or _LOCATION.match(value))
        or value.startswith(_MASK_PREFIX)
        or value.isdigit()
    )


def _secret_group(match: re.Match[str]) -> str | int | None:
    if "value" in match.re.groupindex:
        return "value" if match.group("value") else None
    return 1 if match.re.groups and match.group(1) else None


def mask_text(text: str) -> str:
    """Return ``text`` with every credential-shaped value replaced."""
    for name, pattern in PATTERNS:

        def replace(match: re.Match[str], _name: str = name) -> str:
            group = _secret_group(match)
            if group is None:
                return f"{_MASK_PREFIX}{_name}]"
            value = match.group(group)
            if _is_placeholder(value):
                return match.group(0)
            start, end = match.span(group)
            return (
                match.string[match.start() : start]
                + f"{_MASK_PREFIX}{_name}]"
                + match.string[end : match.end()]
            )

        text = pattern.sub(replace, text)
    return text


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_mask_value(v) for v in value)
    return value


def mask(*, data: Any, **_kwargs: Any) -> Any:
    """Langfuse ``mask`` hook: mask strings anywhere inside ``data``."""
    return _mask_value(data)


def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult | None:
    """Langfuse ``mask_otel_spans`` hook: mask string attributes of third-party spans."""
    patches: dict[Any, OtelSpanPatch] = {}
    for identifier, span in params.spans.items():
        changed: dict[str, Any] = {}
        for key, value in span.attributes.items():
            masked = mask_attribute(value)
            if masked is not None:
                changed[key] = masked
        if changed:
            patches[identifier] = OtelSpanPatch(set_attributes=changed)
    return MaskOtelSpansResult(span_patches=patches) if patches else None


def mask_attribute(value: Any) -> Any:
    """The masked attribute value, or None when nothing changed."""
    if isinstance(value, str):
        masked = mask_text(value)
        return masked if masked != value else None
    if isinstance(value, list | tuple) and value and all(isinstance(v, str) for v in value):
        masked_seq = tuple(mask_text(v) for v in value)
        return masked_seq if masked_seq != tuple(value) else None
    return None
