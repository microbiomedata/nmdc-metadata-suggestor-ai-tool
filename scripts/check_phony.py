"""Check that the Makefile's .PHONY line matches the targets it defines.

A target is phony when running it produces no file named after the target.
Every target in this Makefile is phony, so .PHONY should list all of them
and nothing else.

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


def main() -> int:
    text = MAKEFILE.read_text()

    targets: list[str] = []
    declared: list[str] = []
    for line in text.splitlines():
        phony = PHONY_RE.match(line)
        if phony:
            declared.extend(phony.group(1).split())
            continue
        target = TARGET_RE.match(line)
        if target:
            targets.append(target.group(1))

    problems: list[str] = []

    undeclared = [t for t in targets if t not in declared]
    if undeclared:
        problems.append(
            "targets missing from .PHONY (make will skip these if a file of "
            f"the same name appears): {' '.join(undeclared)}"
        )

    orphaned = [d for d in declared if d not in targets]
    if orphaned:
        problems.append(f".PHONY names that are not targets: {' '.join(orphaned)}")

    # The other half of the rule: a target that really does build a file of
    # its own name must not be phony. We cannot read recipes, but a target
    # name existing as a path is the signal worth failing on.
    root = MAKEFILE.parent
    collide = [t for t in targets if (root / t).exists()]
    if collide:
        problems.append(
            "target names that exist as a path, so they may build a file of "
            f"their own name and should not be phony: {' '.join(collide)}"
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
