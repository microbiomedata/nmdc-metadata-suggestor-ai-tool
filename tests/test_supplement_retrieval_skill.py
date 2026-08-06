"""Tests for the ``supplement-retrieval`` skill as an agent actually uses it.

Two layers:

* **Contract tests** (run in CI, no network) check that what the skill *tells* an
  agent to write still works: the import paths in ``SKILL.md`` and
  ``refs/programmatic.md``, the keyword arguments documented for
  ``retrieve_supplements``, and the ``sources=[...]`` names. An agent writes code
  straight out of these docs, so drift between them and the package is a broken
  agent, not a stale comment.
* An **integration test** (opt-in, `-m integration`) that runs a real agent with
  the skill loaded and asserts it invoked the skill and came back with
  supplements.
"""

import ast
import asyncio
import importlib
import inspect
import re
from pathlib import Path

import pytest

from nmdc_metadata_suggestor_ai_tool.models.supplement import SupplementRetrievalResult
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements import (
    retrieve as retrieve_module,
)
from nmdc_metadata_suggestor_ai_tool.publication_ingestion.supplements import retrieve_supplements

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "supplement-retrieval"
SKILL_DOCS = [SKILL_DIR / "SKILL.md", SKILL_DIR / "refs" / "programmatic.md"]

# ``from nmdc_metadata_suggestor_ai_tool... import ...`` as written in the docs,
# parenthesized or on one line, whether or not the enclosing fence is valid
# standalone Python (the reference includes an annotated signature block).
_DOC_IMPORT_PATTERNS = (
    re.compile(
        r"^from (nmdc_metadata_suggestor_ai_tool[\w.]*) import \(\n(.*?)^\)$",
        re.MULTILINE | re.DOTALL,
    ),
    re.compile(r"^from (nmdc_metadata_suggestor_ai_tool[\w.]*) import ([^(\n]+)$", re.MULTILINE),
)


def _documented_imports() -> list[tuple[Path, str, list[str]]]:
    """Return ``(doc, module, symbols)`` for every import statement in the skill docs."""
    found = []
    for doc in SKILL_DOCS:
        text = doc.read_text()
        for pattern in _DOC_IMPORT_PATTERNS:
            for module, body in pattern.findall(text):
                symbols = [
                    name
                    for line in body.strip().splitlines()
                    for name in line.split("#", 1)[0].split(",")
                    if name.strip()
                ]
                found.append((doc, module, [name.strip() for name in symbols]))
    return found


def test_skill_docs_contain_import_examples() -> None:
    # Guards the parsing itself: an empty result would make the tests below vacuous.
    imports = _documented_imports()
    assert imports, f"no import examples found in {[d.name for d in SKILL_DOCS]}"
    assert any(len(symbols) > 5 for _, _, symbols in imports), (
        "expected the programmatic reference to document the full API surface"
    )


def test_documented_imports_resolve() -> None:
    # An agent copies these imports verbatim; every one must still work.
    for doc, module_name, symbols in _documented_imports():
        module = importlib.import_module(module_name)
        missing = [name for name in symbols if not hasattr(module, name)]
        assert not missing, f"{doc.name} imports {missing} from {module_name}, which lacks them"


def test_documented_retrieve_supplements_signature_matches() -> None:
    # refs/programmatic.md spells out the call signature; an agent passes those
    # keywords by name, so an unknown one is a TypeError at agent runtime.
    reference = (SKILL_DIR / "refs" / "programmatic.md").read_text()
    block = re.search(r"retrieve_supplements\(\n(.*?)\n\) -> ", reference, re.DOTALL)
    assert block, "expected a retrieve_supplements(...) signature block in programmatic.md"

    documented = {
        match.group(1)
        for match in re.finditer(r"^\s{4}(\w+):", block.group(1), re.MULTILINE)
        if match.group(1) != "self"
    }
    actual = set(inspect.signature(retrieve_supplements).parameters)
    assert documented <= actual, f"documented but not accepted: {sorted(documented - actual)}"
    assert "doi" in documented


def test_documented_source_names_route_to_a_retriever(monkeypatch) -> None:
    # SKILL.md tells the agent it may pass sources=["europepmc", ...]. Each name
    # must reach a retriever rather than being silently ignored.
    skill = (SKILL_DIR / "SKILL.md").read_text()
    listing = re.search(r"Pass `sources=\[\.\.\.\]`[^.]*?\(([^)]*)\)", skill, re.DOTALL)
    assert listing, "expected SKILL.md to list the accepted `sources` values"
    documented = re.findall(r"`\"(\w+)\"`", listing.group(1))
    assert set(documented) == {"europepmc", "pmc_oa", "dryad", "zenodo", "figshare"}

    called: list[str] = []

    def _recorder(name: str):
        def _retrieve(doi: str, **kwargs) -> SupplementRetrievalResult:
            called.append(name)
            return SupplementRetrievalResult(doi=doi, source=name, attempts=[name])

        return _retrieve

    monkeypatch.setattr(
        retrieve_module, "retrieve_supplements_from_europepmc", _recorder("europepmc")
    )
    monkeypatch.setattr(
        retrieve_module,
        "retrieve_supplements_from_pmc_oa",
        lambda pmcid, **kwargs: _recorder("pmc_oa")(kwargs.get("doi", pmcid)),
    )
    monkeypatch.setattr(
        retrieve_module, "find_supplement_source_europepmc", lambda doi: {"pmcid": "PMC123456"}
    )
    for repo in ("dryad", "zenodo", "figshare"):
        monkeypatch.setitem(retrieve_module.DATA_REPO_RETRIEVERS, repo, _recorder(repo))

    for name in documented:
        called.clear()
        retrieve_supplements("10.1038/s41564-020-00861-0", sources=[name])
        assert called == [name], f"sources=[{name!r}] did not reach a retriever"


def test_skill_example_code_parses() -> None:
    # The usage example in SKILL.md is what an agent adapts; it must at least be
    # syntactically valid Python.
    skill = (SKILL_DIR / "SKILL.md").read_text()
    blocks = re.findall(r"```python\n(.*?)```", skill, re.DOTALL)
    assert blocks, "expected at least one python example in SKILL.md"
    for block in blocks:
        ast.parse(block)


# ---------------------------------------------------------------------------
# Live agent test (opt-in): does an agent given this skill actually use it?
#   uv run pytest tests/test_supplement_retrieval_skill.py -m integration
# ---------------------------------------------------------------------------

# An open-access article with Europe PMC supplementary files.
AGENT_TEST_DOI = "10.1038/s41564-020-00861-0"
AGENT_TIMEOUT = 300  # seconds


async def _run_agent(prompt: str) -> tuple[list[str], str]:
    """Run a skill-enabled agent and return ``(tools_used, final_text)``."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        ToolUseBlock,
        query,
    )

    from nmdc_metadata_suggestor_ai_tool.llm_client import DEFAULT_CLAUDE_MODEL

    options = ClaudeAgentOptions(
        skills="all",
        model=DEFAULT_CLAUDE_MODEL,
        cwd=str(Path(__file__).resolve().parent.parent),
        setting_sources=["project"],
        permission_mode="bypassPermissions",
        max_turns=30,
    )

    tools_used: list[str] = []
    transcript: list[str] = []
    async for event in query(prompt=prompt, options=options):
        if not isinstance(event, AssistantMessage):
            continue
        for block in event.content:
            if isinstance(block, ToolUseBlock):
                tools_used.append(f"{block.name}:{block.input}")
            elif isinstance(block, TextBlock):
                transcript.append(block.text)
    return tools_used, "\n".join(transcript)


@pytest.mark.integration
@pytest.mark.timeout(AGENT_TIMEOUT)
def test_agent_uses_supplement_retrieval_skill(requires_credentials: None) -> None:
    """An agent asked for a DOI's supplements reaches for the skill and gets files."""
    prompt = (
        f"Retrieve the supplementary materials for DOI {AGENT_TEST_DOI}. "
        "List the filename and kind of each supplement you retrieved, then state "
        "the value of result.source. Do not guess -- report only what the tooling returns."
    )
    tools_used, final_text = asyncio.run(_run_agent(prompt))

    assert any("supplement-retrieval" in used for used in tools_used), (
        f"agent never invoked the supplement-retrieval skill; tools used: {tools_used}"
    )
    assert "retrieve_supplements" in "\n".join(tools_used), (
        "agent invoked the skill but never called its documented entry point"
    )
    assert re.search(r"\.(csv|tsv|txt|xlsx|xls|pdf|docx)\b", final_text, re.IGNORECASE), (
        f"agent reported no supplement files. Answer was:\n{final_text}"
    )
