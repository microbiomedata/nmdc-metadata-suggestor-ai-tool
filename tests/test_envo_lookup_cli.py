"""Tests for the env-triad skill's envo_lookup.py wrapper.

The wrapper exists to constrain what the agent can do with the index, so these
check the constraints hold: bad slots and extensions are rejected, and a failing
value produces a non-zero exit the agent cannot quietly ignore.
"""

import importlib.util
import shlex
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "env-triad"
SCRIPT = SKILL_DIR / "scripts" / "envo_lookup.py"
SKILL_DOCS = [SKILL_DIR / "SKILL.md", *sorted((SKILL_DIR / "refs").glob("*.md"))]


def documented_invocations() -> list[tuple[str, str]]:
    """Every envo_lookup.py command line inside a ```bash block in the skill docs.

    Prose mentions in backticks are skipped -- only lines an agent would actually
    copy and run are checked.
    """
    found: list[tuple[str, str]] = []
    for doc in SKILL_DOCS:
        # Rejoin backslash line continuations so a wrapped command stays one line.
        in_bash = False
        for line in doc.read_text().replace("\\\n", " ").splitlines():
            if line.startswith("```"):
                in_bash = line.startswith("```bash")
                continue
            _, separator, tail = line.partition("envo_lookup.py")
            if in_bash and separator:
                found.append((doc.name, tail.strip()))
    return found


@pytest.fixture(scope="module")
def cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("envo_lookup", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["envo_lookup"] = module
    spec.loader.exec_module(module)
    return module


def test_script_is_where_skill_md_says_it_is() -> None:
    assert SCRIPT.is_file()
    assert "scripts/envo_lookup.py" in (SKILL_DIR / "SKILL.md").read_text()


@pytest.mark.parametrize(
    ("doc", "invocation"), documented_invocations(), ids=lambda v: v.replace(" ", "_")[:60]
)
def test_documented_commands_are_real(cli: ModuleType, doc: str, invocation: str) -> None:
    """Every command line in the skill's docs must parse against the real verb set.

    Stops the instructions drifting from the CLI, which the agent would only
    discover at runtime.
    """
    argv = shlex.split(invocation)
    if not argv or argv[0].startswith("-"):  # bare `--help` mentions
        return
    try:
        cli.build_parser().parse_args(argv)
    except SystemExit as exit_error:
        pytest.fail(f"{doc} documents a command the CLI rejects: {invocation!r} ({exit_error})")


def test_the_doc_scan_actually_found_commands() -> None:
    """Guards the parametrize above from silently collecting nothing."""
    assert len(documented_invocations()) >= 6


# --- the read commands ------------------------------------------------------


def test_biomes_prints_the_complete_set(cli: ModuleType, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["biomes"]) == 0
    out = capsys.readouterr().out
    assert "marine biome [ENVO:00000447]" in out
    assert "(127)" in out


def test_pool_accepts_a_portal_name(cli: ModuleType, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["pool", "wastewater_sludge", "env_medium"]) == 0
    assert "sludge [ENVO:00002044]" in capsys.readouterr().out


def test_pool_accepts_a_class_name(cli: ModuleType, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["pool", "AirInterface", "env_medium"]) == 0
    assert "air [ENVO:00002005]" in capsys.readouterr().out


def test_search_scopes_to_a_subtree(cli: ModuleType, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["search", "activated", "--within", "ENVO:00002044"]) == 0
    assert "activated sludge [ENVO:00002046]" in capsys.readouterr().out


def test_descendants_lists_more_specific_terms(
    cli: ModuleType, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["descendants", "ENVO:00002044", "--limit", "5"]) == 0
    assert "activated sludge [ENVO:00002046]" in capsys.readouterr().out


def test_descendants_rejects_an_unknown_curie(cli: ModuleType) -> None:
    with pytest.raises(SystemExit):
        cli.main(["descendants", "ENVO:09999999"])


def test_fallback_prints_one_value(cli: ModuleType, capsys: pytest.CaptureFixture) -> None:
    assert cli.main(["fallback", "soil", "env_medium"]) == 0
    assert capsys.readouterr().out.strip() == "soil [ENVO:00001998]"


# --- the constraints --------------------------------------------------------


def test_unknown_extension_is_rejected_with_the_valid_list(cli: ModuleType) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["fallback", "swamp-gas", "env_medium"])
    assert "SoilInterface (soil)" in str(excinfo.value)


def test_unknown_slot_is_rejected_by_the_parser(cli: ModuleType) -> None:
    with pytest.raises(SystemExit):
        cli.main(["fallback", "soil", "env_broad"])


def test_no_command_is_rejected(cli: ModuleType) -> None:
    with pytest.raises(SystemExit):
        cli.main([])


# --- validate ---------------------------------------------------------------


def test_validate_passes_and_reports_the_tier(
    cli: ModuleType, capsys: pytest.CaptureFixture
) -> None:
    exit_code = cli.main(["validate", "env_medium", "soil [ENVO:00001998]", "--interface", "soil"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith("PASS")
    assert "source: submission_enum" in out


def test_validate_exits_non_zero_on_any_failure(
    cli: ModuleType, capsys: pytest.CaptureFixture
) -> None:
    """The exit code is what stops a bad value being emitted unnoticed."""
    exit_code = cli.main(
        [
            "validate",
            "env_medium",
            "soil [ENVO:00001998]",
            "subterranean lake [ENVO:02000145]",
            "--interface",
            "water",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "PASS  soil [ENVO:00001998]" in out
    assert "FAIL  subterranean lake [ENVO:02000145]" in out


def test_validate_offers_the_corrected_label(
    cli: ModuleType, capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(["validate", "env_medium", "dirt [ENVO:00001998]"]) == 1
    assert "use instead: soil [ENVO:00001998]" in capsys.readouterr().out


def test_validate_honours_the_interface_specific_pattern(
    cli: ModuleType, capsys: pytest.CaptureFixture
) -> None:
    """UBERON is legal for host-associated media and not for air."""
    assert (
        cli.main(
            ["validate", "env_medium", "gut [UBERON:0001555]", "--interface", "host-associated"]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["validate", "env_medium", "gut [UBERON:0001555]", "--interface", "air"]) == 1
