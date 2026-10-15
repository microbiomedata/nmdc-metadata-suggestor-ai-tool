"""Mask credentials in everything the suggester sends to Langfuse.

Two Langfuse hooks are wired up in ``tracing.py``:

* ``mask`` covers data set through the Langfuse SDK (``@observe``, ``create_event``).
* ``mask_otel_spans`` covers spans from other OpenTelemetry instrumentations. The
  Claude Agent SDK instrumentor records tool inputs and outputs this way, which is
  how a ``cat`` of a settings file sent an API key to Langfuse on 2026-08-20.

The patterns follow the BERIL filters compared on 2026-10-14 (langfuse-retro-load's
``redaction.py``, the BERIL live hook, and Workbench's ``scrub()``), plus the two
shapes all three missed: passwords in connection URLs and the keys in
``mc alias set``. Email addresses are not masked, because submission metadata carries
contact emails the suggester is meant to read.

A masked value becomes ``[REDACTED:<pattern name>]``. Placeholders such as
``<your-key>``, ``${API_KEY}`` or ``YOUR-ACCESS-KEY-HERE`` are left alone.
"""

import re
from collections.abc import Callable
from typing import Any

from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch

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
#
# (name, pattern). Group "value" is the part that gets replaced; without it, the
# whole match is. Order matters: earlier patterns run first, and later ones skip
# text that is already masked.
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
    ("anthropic_or_openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("google_oauth_token", re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    (
        "keyed_value",
        re.compile(
            rf"""(?i)\b{_CREDENTIAL_NAME}(?:\\{{0,2}}[\"'\]])*\s*(?::|=|:=)\s*(?:\\{{0,2}}[\"'])?(?P<value>[^\s\"'\\,;}}\])(]{{8,}})(?![\w./+=-]*\()"""
        ),
    ),
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


def mask_text(text: str) -> str:
    """Return ``text`` with every credential-shaped value replaced."""
    for name, pattern in PATTERNS:

        def replace(match: re.Match[str], name: str = name) -> str:
            groups = [g for g in ("value",) if g in match.re.groupindex and match.group(g)]
            if not groups:
                return f"{_MASK_PREFIX}{name}]"
            out, last = [], match.start()
            for group in groups:
                start, end = match.span(group)
                out.append(match.string[last:start])
                value = match.group(group)
                out.append(value if _is_placeholder(value) else f"{_MASK_PREFIX}{name}]")
                last = end
            out.append(match.string[last : match.end()])
            return "".join(out)

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
            masked = _mask_attribute(value, mask_text)
            if masked is not None:
                changed[key] = masked
        if changed:
            patches[identifier] = OtelSpanPatch(set_attributes=changed)
    return MaskOtelSpansResult(span_patches=patches) if patches else None


def _mask_attribute(value: Any, masker: Callable[[str], str]) -> Any:
    """The masked attribute value, or None when nothing changed."""
    if isinstance(value, str):
        masked = masker(value)
        return masked if masked != value else None
    if isinstance(value, list | tuple) and value and all(isinstance(v, str) for v in value):
        masked_seq = tuple(masker(v) for v in value)
        return masked_seq if masked_seq != tuple(value) else None
    return None
