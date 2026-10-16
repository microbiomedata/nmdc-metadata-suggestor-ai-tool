"""Credential masking for Langfuse traces.

Every planted secret is random per run, so no real credential is involved and a
pattern can't pass by matching one fixed string.
"""

import secrets
import string

import pytest
from langfuse.types import MaskOtelSpansParams, OtelSpanData, OtelSpanIdentifier
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nmdc_metadata_suggestor_ai_tool import trace_masking
from nmdc_metadata_suggestor_ai_tool.trace_masking import (
    GITLEAKS_RULES,
    load_gitleaks_patterns,
    mask,
    mask_otel_spans,
    mask_text,
)


def _rand(n: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _secret_cases() -> list[tuple[str, str, str]]:
    """(case, text, the secret that must not survive)."""
    upper = string.ascii_uppercase + string.digits
    # AWS access key ids use base32 after the prefix; the gitleaks rule accepts only that.
    base32 = string.ascii_uppercase + "234567"
    cases = []

    def add(case: str, template: str, value: str) -> None:
        cases.append((case, template.format(value), value))

    add("shell export", "export MINIO_SECRET_KEY={}", _rand(40))
    add("json key", '{{"secret_key": "{}"}}', _rand(32))
    add("python assignment", 'access_key = "{}"', _rand(20))
    add("settings env block", '"env": {{"ANTHROPIC_AUTH_TOKEN": "{}"}}', "sk-" + _rand(40))
    add("authorization header", "Authorization: Bearer {}", _rand(40))
    add("curl header", "curl -H 'Authorization: Bearer {}' https://example.org", _rand(40))
    add("bare anthropic key", "key was sk-ant-api03-{} ok", _rand(80))
    add("bare aws key", "id AKIA{} here", _rand(16, base32))
    add("bare github token", "ghp_{}", _rand(36))
    add("bare jwt", "eyJ{}.eyJ{}.{}".format(_rand(20), _rand(20), "{}"), _rand(30))
    add("google access token", '{{"_credentials": {{"token": "ya29.c.{}"}}}}', _rand(200))
    add("escaped json key", r"{{\"secret_key\": \"{}\"}}", _rand(32))
    add("double-escaped json key", r'{{\\"api_token\\": \\"{}\\"}}', _rand(32))
    add("proxy authorization", "Proxy-Authorization: Bearer {}", _rand(40))
    add("postgres url", "postgres://admin:{}@db.example.org:5432/x", _rand(24))
    add("pem key", "-----BEGIN RSA PRIVATE KEY-----\n{}\n-----END RSA PRIVATE KEY-----", _rand(64))
    access, secret = _rand(20, upper), _rand(40)
    cases.append(
        (
            "mc alias access key",
            f"mc alias set berdl https://minio.example.org {access} {secret}",
            access,
        )
    )
    cases.append(
        (
            "mc alias secret key",
            f"mc alias set berdl https://minio.example.org {access} {secret}",
            secret,
        )
    )
    secret = _rand(30)
    cases.append(
        (
            "mc alias quoted key with a space",
            f'mc alias set b https://m.example.org "AK {_rand(8, upper)}" {secret}',
            secret,
        )
    )
    secret = _rand(30)
    cases.append(
        (
            "mc alias backslash-newline",
            f"mc alias set b https://m.example.org \\\n  {_rand(20, upper)} {secret}",
            secret,
        )
    )
    add("quoted uri password", "postgres://u:'{}'@db.example.org/x", _rand(16))
    return cases


_CASES = _secret_cases()


@pytest.mark.parametrize(("case", "text", "secret"), _CASES, ids=[c[0] for c in _CASES])
def test_secret_is_masked(case: str, text: str, secret: str) -> None:
    masked = mask_text(text)
    assert secret not in masked, case
    assert "[REDACTED:" in masked, case


@pytest.mark.parametrize(
    "text",
    [
        'token = os.environ.get("KBASE_AUTH_TOKEN")',
        "assembly GCF_000203835.1",
        "api_key: ${CBORG_API_KEY}",
        "max_tokens=4096",
        "mc alias set berdl https://minio.example.org YOUR-ACCESS-KEY-HERE YOUR-SECRET-KEY-HERE",
        "postgres://user:<password>@db.example.org/x",
        "contact pi@example.org about the samples",
        "env_broad_scale: terrestrial biome [ENVO:00000446]",
        '"_token_uri": "https://oauth2.googleapis.com/token"',
        '"credentials_file": "/home/user/.config/gcloud/application_default_credentials.json"',
        '"max_tokens": "settings.max_tokens"',
        "https://example.org:8080?contact=reader@example.net",
        "https://example.org:8080#section@x",
    ],
)
def test_benign_text_is_unchanged(text: str) -> None:
    assert mask_text(text) == text


def test_mask_walks_nested_data() -> None:
    value = "sk-" + _rand(40)
    data = {"messages": [{"content": f"key {value}"}], "count": 3, "ok": True}
    masked = mask(data=data)
    assert value not in str(masked)
    assert masked["count"] == 3
    assert masked["ok"] is True


def _export_one_span(attributes: dict[str, str], hook):  # type: ignore[no-untyped-def]
    """Send one span through Langfuse's exporter wrapper and return what reached the exporter."""
    # LangfuseTransformingSpanExporter is private; this was written against langfuse==4.15.1.
    # If the import fails after a Langfuse bump, find where the exporter moved before
    # relaxing the test: the negative control below depends on it. The hook logic itself
    # is also covered by test_hook_patches_only_changed_attributes, which uses public types.
    try:
        from langfuse._client.span_exporter import LangfuseTransformingSpanExporter
    except ImportError:
        pytest.skip("LangfuseTransformingSpanExporter not found; check after a Langfuse upgrade")

    sink = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        SimpleSpanProcessor(
            LangfuseTransformingSpanExporter(
                exporter=sink, media_manager=None, mask_otel_spans=hook
            )
        )
    )
    with provider.get_tracer("test").start_as_current_span("Bash") as span:
        for key, value in attributes.items():
            span.set_attribute(key, value)
    provider.shutdown()
    (exported,) = sink.get_finished_spans()
    return dict(exported.attributes or {})


def test_tool_span_output_is_masked_before_export() -> None:
    value = "sk-" + _rand(40)
    output = (
        '{"env": {"ANTHROPIC_AUTH_TOKEN": "'
        + value
        + '", "ANTHROPIC_BASE_URL": "https://gw.example.org"}}'
    )
    exported = _export_one_span({"output.value": output, "tool.name": "Bash"}, mask_otel_spans)
    assert value not in exported["output.value"]
    assert "https://gw.example.org" in exported["output.value"]
    assert exported["tool.name"] == "Bash"


def test_negative_control_without_hook_the_secret_is_exported() -> None:
    """Shows the export test above can fail: with no hook, the value goes out as is."""
    value = "sk-" + _rand(40)
    exported = _export_one_span({"output.value": f"ANTHROPIC_AUTH_TOKEN={value}"}, None)
    assert value in exported["output.value"]


def _span(attributes: dict) -> tuple[OtelSpanIdentifier, OtelSpanData]:  # type: ignore[type-arg]
    identifier = OtelSpanIdentifier(trace_id="t1", span_id="s1")
    data = OtelSpanData(
        trace_id="t1",
        span_id="s1",
        parent_span_id=None,
        name="Bash",
        instrumentation_scope_name=None,
        instrumentation_scope_version=None,
        attributes=attributes,
        resource_attributes={},
    )
    return identifier, data


def test_hook_patches_only_changed_attributes() -> None:
    """The mask_otel_spans logic, through Langfuse's public types only."""
    value = "sk-" + _rand(40)
    identifier, span = _span(
        {
            "output.value": f"ANTHROPIC_AUTH_TOKEN={value}",
            "input.args": ["cat", f"key {value}"],
            "tool.name": "Bash",
            "exit_code": 0,
        }
    )
    result = mask_otel_spans(params=MaskOtelSpansParams(spans={identifier: span}))
    assert result is not None
    patch = result.span_patches[identifier]
    assert patch is not None
    assert set(patch.set_attributes) == {"output.value", "input.args"}
    assert value not in str(patch.set_attributes)


def test_hook_returns_none_when_nothing_to_mask() -> None:
    identifier, span = _span({"tool.name": "Bash", "output.value": "3 files"})
    assert mask_otel_spans(params=MaskOtelSpansParams(spans={identifier: span})) is None


def test_every_listed_gitleaks_rule_loads() -> None:
    names = [name for name, _ in load_gitleaks_patterns()]
    assert names == list(GITLEAKS_RULES)


def test_a_rule_missing_from_the_bundled_file_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trace_masking, "GITLEAKS_RULES", (*GITLEAKS_RULES, "no-such-rule"))
    with pytest.raises(ValueError, match="no-such-rule"):
        load_gitleaks_patterns()


@pytest.mark.parametrize(
    ("rule", "token"),
    [
        (
            "anthropic-api-key",
            "sk-ant-api03-" + _rand(93, string.ascii_letters + string.digits + "_-") + "AA",
        ),
        (
            "slack-bot-token",
            "xoxb-" + _rand(12, string.digits) + "-" + _rand(12, string.digits) + "-" + _rand(24),
        ),
        ("gcp-api-key", "AIza" + _rand(35)),
        ("github-fine-grained-pat", "github_pat_" + _rand(82)),
    ],
    ids=["anthropic-api-key", "slack-bot-token", "gcp-api-key", "github-fine-grained-pat"],
)
def test_gitleaks_rule_catches_its_token(rule: str, token: str) -> None:
    """Each token is masked under the gitleaks rule's own name, not by a custom pattern."""
    masked = mask_text(f'"key": "{token}" ok')
    assert token not in masked
    assert f"[REDACTED:{rule}]" in masked
    # gitleaks rules also match the delimiter after the token; it must survive.
    assert masked.endswith('" ok')


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("password = abc1234", "shorter than 8 characters"),
        ("pin_token = 123456789012", "all digits"),
        ('password = "my secret phrase"', "quoted value with spaces, first part short"),
    ],
    ids=["short", "all-digit", "quoted-with-spaces"],
)
def test_known_gaps_are_not_masked(text: str, why: str) -> None:
    """The gaps listed in the module docstring. If one starts failing, update the docstring."""
    assert mask_text(text) == text, why
