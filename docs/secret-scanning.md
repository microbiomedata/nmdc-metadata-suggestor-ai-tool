# Secret Scanning

This repo uses multiple layers of secret scanning to prevent credentials from being committed or merged.

## What's in place

| Layer | Tool | Where it runs |
|-------|------|---------------|
| Pre-commit hook | git-secrets | Local (auto-installed via git template) |
| Pre-commit hook | gitleaks | Local (installed via `/secure-repo`) |
| Pre-commit hook | trufflehog | Local (installed via `/secure-repo`) |
| GitHub Action | gitleaks-action | CI — on PRs to `main` |

## Quick setup (Claude Code users)

Run the `/secure-repo` slash command. It will check for required tools, install the pre-commit hook, and verify everything works.

## Manual setup

1. Install the tools:

```bash
brew install git-secrets gitleaks trufflehog
```

2. Copy the pre-commit hook. The hook script lives in the `/secure-repo` Claude skill definition at `.claude/commands/secure-repo.md`. Copy the bash script block from that file into `.git/hooks/pre-commit` and make it executable:

```bash
chmod +x .git/hooks/pre-commit
```

3. Verify it works:

```bash
.git/hooks/pre-commit
```

## Handling blocked commits

If the hook blocks your commit, it means one of the scanners found something that looks like a secret.

1. **Check the output** — the hook prints which scanner flagged the issue.
2. **Remove the secret** from the staged files. Use environment variables or a `.env` file (which is gitignored) instead.
3. **If it's a false positive:**
   - For gitleaks: add the fingerprint to `.gitleaksignore` in the repo root.
   - For git-secrets: run `git secrets --add --allowed '<pattern>'`.
   - For trufflehog: trufflehog only flags verified secrets by default, so false positives are rare.

## Handling CI failures

The GitHub Action (`secret-scan.yml`) runs gitleaks on every PR to `main`. If it fails:

1. Check the action output in the PR's "Checks" tab.
2. Fix the issue or add the fingerprint to `.gitleaksignore` and push again.
