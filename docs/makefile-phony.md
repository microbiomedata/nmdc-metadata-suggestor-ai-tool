# Keeping `.PHONY` correct

## The rule

A target is phony when running it produces no file named after the target. Every phony target
belongs in `.PHONY`, and no target that does build a file of its own name belongs there.

That is GNU make's own definition. `.PHONY` tells make to stop consulting the filesystem for
that name. Two things go wrong when the line drifts:

- **A phony target left out.** If a file or directory of that name ever appears, make reports
  `make: 'run' is up to date.` and silently skips the recipe.
- **A real file target wrongly declared phony.** Make stops checking its timestamp, so it
  rebuilds every time and other targets can no longer depend on it meaningfully.

Every target in this Makefile is phony today. None of them writes a file named after itself.

## Why this is checked automatically

`make lint` runs `make check-phony`, which runs `scripts/check_phony.py`. It fails on:

- a target missing from `.PHONY`
- a `.PHONY` entry that is not a target
- a target name that exists as a path in the repo, which suggests it should not be phony
- a target defined twice

The check exists because the line drifted once already. It listed 17 names against 23 targets,
so `all-install`, `prod-install`, `docker-dev-build`, `docker-dev-down`, `docker-shell` and `run`
were all skippable, and it listed `lint-fix`, which was never a target.

## Why not use checkmake

[checkmake](https://github.com/checkmake/checkmake) is the obvious off-the-shelf Makefile linter
and it ships a rule called `phonydeclared`. It does not check the rule above. Read from its
source at `rules/phonydeclared/phonydeclared.go`, the condition is:

```go
if len(rule.Body) == 0 && !ok {
    // violation: Target %q should be declared PHONY.
}
```

It only fires on targets with **no recipe body**. Every target in this Makefile has a body, so
checkmake reports zero `phonydeclared` violations against the exact file that was broken. Its
other phony rule, `minphony`, checks only that a configured list (default `all,clean,test`) is
present, and says nothing about the rest of the file.

So a green `checkmake` run is not evidence that `.PHONY` is complete. That is why this repo uses
a local check instead of adding a dependency.

## Adding a target

Add it to `.PHONY` in the same place. `make check-phony` fails if you forget. If the new target
genuinely builds a file named after itself, leave it out of `.PHONY` and the check will tell you
so once that file exists.
