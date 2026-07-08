# Dependency security and hygiene

How this repo checks dependencies for known vulnerabilities and for hygiene
problems (unused, missing, or misplaced packages). CI (`ci.yml`) already covers
lint, types, and tests; the pieces below cover the dependency surface it did not.

## What runs

| Check | Tool | Where | Purpose |
|-------|------|-------|---------|
| Known CVEs | `pip-audit` | `make security`, `security.yml` (audit job) | Flags locked dependencies with published advisories |
| New CVEs in a PR | Dependency Review Action | `security.yml` (dependency-review job, PR only) | Flags dependency changes that *introduce* vulnerable packages, comparing head vs base |
| Dependency hygiene | `deptry` | `make check-deps`, `ci.yml` | Flags unused / missing / misplaced dependencies |
| Ongoing upgrades | Dependabot | `.github/dependabot.yml` | Weekly version-update PRs plus security-update PRs |

Note the split: `pip-audit` and Dependency Review are about security (CVEs).
`deptry` is about hygiene, not security, so it lives with the other static
checks in `ci.yml`, not under the Security workflow.

## Running locally

```bash
make security      # pip-audit against the full locked dependency tree
make check-deps    # deptry hygiene check on src/
```

Both use ephemeral tool installs (`uvx` / `uv run --with`), so neither adds a
locked dev dependency.

`deptry` ignores one package by design: `nmdc-submission-schema` is loaded at
runtime via `importlib.resources.files("nmdc_submission_schema.schema")` in
`schema_context.py`, which its static import scan cannot see. That ignore is
configured in `[tool.deptry.per_rule_ignores]` in `pyproject.toml`.

## The pip-audit gate is report-only for now

`main` currently carries pre-existing CVEs, so a hard `pip-audit` failure would
red every build until they are cleared. The CI step is therefore marked
`continue-on-error: true`: it reports, it does not block. Dependabot security
updates drive the actual fixes. Once the backlog is clear, remove
`continue-on-error` from the audit step in `security.yml` to make it blocking.

## Enabling the GitHub-native pieces

Two features need a repo setting toggled on (Settings > Code security),
which only an admin can do:

- **Dependency graph.** Required by the Dependency Review job. Until it is on,
  that job is `continue-on-error` and reports "Dependency review is not
  supported on this repository". Turn it on, then remove `continue-on-error`
  from the dependency-review step in `security.yml`.
- **Dependabot alerts and security updates.** Let Dependabot open the CVE-fix
  PRs that `.github/dependabot.yml` schedules.

## Alternatives considered

- **Dependabot alone (no custom workflow).** Zero maintenance, but Dependabot
  opens PRs; it does not fail CI. Kept it as the fix engine and added the
  `pip-audit` gate and Dependency Review for the parts it does not cover.
- **osv-scanner instead of pip-audit.** Google's scanner reads `uv.lock`
  directly, avoiding the `uv export` step. A reasonable swap if the export
  becomes a maintenance burden.
- **CodeQL code scanning.** Overlaps `bandit`-style code-level findings (for
  example unsafe XML parsing). Left out here; revisit if code-level scanning is
  wanted.
- **CLI tools vs GitHub-native features.** The CLI tools (`pip-audit`,
  `deptry`, `osv-scanner`) run in Actions minutes, free on public repos. The
  GitHub-native Security features (code scanning, secret scanning, Dependency
  Review, Dependabot) are also free on public repos; on private repos the Code
  Security and Secret Protection products are billed per active committer. This
  repo is public, so all of the above are free.

## References

- Dependabot uv support: https://github.blog/changelog/2025-03-13-dependabot-version-updates-now-support-uv-in-general-availability/
- Dependency Review Action: https://github.com/actions/dependency-review-action
- pip-audit: https://github.com/pypa/pip-audit
- deptry: https://deptry.com/
