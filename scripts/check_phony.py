"""Check that the Makefile's .PHONY line matches the targets it defines.

A target is phony when running it produces no file named after the target.
Every phony target must be in .PHONY. A target that genuinely does build a
file of its own name must instead be listed in the Makefile's FILE_TARGETS
variable, and kept out of .PHONY. Every target must be in exactly one of the
two.

checkmake's rule of the same name does not check this: its `phonydeclared`
only fires on targets with an empty recipe body, so it passes a Makefile
whose .PHONY line omits every target that actually has a recipe. See
docs/makefile-phony.md.

Run via `make check-phony`, which `make lint` calls.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"

# A target line: name, then ':' not followed by '=' (which would be an
# immediately-expanded variable assignment such as `VAR := value`).
TARGET_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:(?!=)")
PHONY_RE = re.compile(r"^\.PHONY\s*:(.*)$")
# Targets the Makefile declares as genuinely building a file of their own name.
FILE_TARGETS_RE = re.compile(r"^FILE_TARGETS\s*[:?]?=(.*)$")


def main() -> int:
    text = MAKEFILE.read_text()

    targets: list[str] = []
    declared: list[str] = []
    file_targets: list[str] = []
    for line in text.splitlines():
        phony = PHONY_RE.match(line)
        if phony:
            declared.extend(phony.group(1).split())
            continue
        file_decl = FILE_TARGETS_RE.match(line)
        if file_decl:
            file_targets.extend(file_decl.group(1).split())
            continue
        target = TARGET_RE.match(line)
        if target:
            targets.append(target.group(1))

    problems: list[str] = []

    undeclared = [t for t in targets if t not in declared and t not in file_targets]
    if undeclared:
        problems.append(
            "targets missing from .PHONY (make will skip these if a file of "
            f"the same name appears): {' '.join(undeclared)}"
        )

    orphaned = [d for d in declared if d not in targets]
    if orphaned:
        problems.append(f".PHONY names that are not targets: {' '.join(orphaned)}")

    both = [t for t in file_targets if t in declared]
    if both:
        problems.append(
            "declared in FILE_TARGETS and in .PHONY; a target that builds a "
            f"file of its own name must not be phony: {' '.join(both)}"
        )

    unknown = [t for t in file_targets if t not in targets]
    if unknown:
        problems.append(f"FILE_TARGETS names that are not targets: {' '.join(unknown)}")

    # A target name that also exists as a path is the case .PHONY exists to
    # handle, so it is worth failing on whatever put the path there. This
    # matches untracked files too, which is deliberate: make skips a
    # non-phony target just as readily for a local stray directory as for a
    # committed one. It does not prove the target built that path.
    root = MAKEFILE.parent
    collide = [t for t in targets if t not in file_targets and (root / t).exists()]
    if collide:
        problems.append(
            "target names that also exist as a path in the working tree, "
            "tracked or not; keep them phony, or rename the target if it is "
            f"meant to build that path: {' '.join(collide)}"
        )

    duplicates = sorted({t for t in targets if targets.count(t) > 1})
    if duplicates:
        problems.append(f"targets defined more than once: {' '.join(duplicates)}")

    if problems:
        print("Makefile .PHONY check failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f".PHONY check passed: {len(targets)} targets, all declared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
