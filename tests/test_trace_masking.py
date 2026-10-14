"""Credential masking for Langfuse traces.

Every planted secret is random per run, so no real credential is involved and a
pattern can't pass by matching one fixed string.
"""

import secrets
import string

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nmdc_metadata_suggestor_ai_tool.trace_masking import mask, mask_otel_spans, mask_text


def _rand(n: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _secret_cases() -> list[tuple[str, str, str]]:
    """(case, text, the secret that must not survive)."""
    upper = string.ascii_uppercase + string.digits
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
    add("bare aws key", "id AKIA{} here", _rand(16, upper))
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
    return cases


@pytest.mark.parametrize(
    ("case", "text", "secret"), _secret_cases(), ids=lambda v: v if " " in str(v) else ""
)
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
    from langfuse._client.span_exporter import LangfuseTransformingSpanExporter

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
