# An introduction to agent sandboxing for local usage with [nono](https://github.com/nolabs-ai/nono)

## Using nono via the command line
This is the easiest way to use nono. If you already use your coding assistant via CLI, this will be super easy for you. Follow the installation instructions on nono and use this base command to get started:

`nono run --profile claude --allow . -- claude`

This tells nono to run the Claude profile with permissions to the current working directory (and not outside).

See details on the profile [here](https://github.com/nolabs-ai/nono-packs/blob/main/claude/policy.json)

I (Olivia) do not use the Claude CLI because you cannot override the base URL easily via the CLI. I only access LLMs through lab instances so I can be very aware of the security associated with using it. This has downstream helpful effects on being sure my data stays in the org and does not get back to providers.

## Sandboxing VSCode Claude Code extension with nono
I created a custom profile because of this that denies most everything except what claude needs to run:
```json
{
  "extends": "default",
  "meta": {
    "name": "claude-custom",
    "version": "1.0.0",
    "description": "Claude Code, no keychain, .env denied"
  },
  "groups": {
    "include": [
      "claude_code_macos",
      "user_caches_macos",
      "node_runtime",
      "python_runtime",
      "vscode_macos",
      "git_config",
      "unlink_protection"
    ]
  },
  "security": {
    "signal_mode": "isolated",
    "capability_elevation": false
  },
  "workdir": { "access": "readwrite" },
  "filesystem": {
    "allow": [
      "$HOME/.claude",
      "$HOME/.local/state/claude/locks",
      "$HOME/.cache/claude",
      "/tmp/claude-$UID"
    ],
    "allow_file": [
      "$HOME/.claude.json",
      "$HOME/.claude.json.lock",
      "$HOME/.claude.lock"
    ],
    "deny": [
      "$WORKDIR/.env"
    ]
  },
  "network": { "block": false }
}
```
note - This profile is based on nono's built-in claude profile, with keychain access and the login/URL-opening features removed.
Each project's .claude/settings.local.json is covered by the workdir grant. Add other secrets files to deny as needed (e.g. $WORKDIR/.env.local). The deny list matches exact paths, not patterns.

I put this file in `~/.config/nono/profiles/claude-custom.json` and can see it when I run `nono profile list`

I use Claude Code via the VSCode extension (easy to override the base URL and log in with the PNNL-supported AI Incubator). What needs to happen to run this sandboxed with nono? In the background, VSCode runs the `claude` command to start the extension. If we can wrap that with nono, we can sandbox it.

`VS Code extension → ~/bin/claude-nono (wrapper) → nono → claude (sandboxed)`

### Wrap VSCode Extension
Claude Code in VS Code runs inside a nono sandbox. It can read and write the current project and its own config, but it can't access .env, ~/.ssh, ~/.aws, or the rest of the home directory.

Tested with: nono v0.80.0, Claude Code extension 2.1.304, macOS (Apple Silicon)

Auth: Claude authenticates through each project's .claude/settings.local.json. **It does not use /login or the keychain.**

1. Create `~/.config/nono/profiles/claude-custom.json`
2. Identify your nono path: `which nono`
3. Create the script that will redirect `claude` to `nono run claude`
```bash
mkdir -p ~/bin

cat > ~/bin/claude-nono <<'EOF'
#!/usr/bin/env bash

# Create folders Claude expects to exist
mkdir -p ~/.claude ~/.local/state/claude/locks ~/.cache/claude "/tmp/claude-$(id -u)"

# Log each launch (useful for confirming VS Code uses this wrapper)
echo "$(date) cwd=$PWD args=$*" >> /tmp/claude-nono.log

NONO=/opt/homebrew/bin/nono

# The extension passes its bundled claude binary as the first argument.
# Fall back to claude on PATH when run manually.
if [[ -n "$1" && -x "$1" && "$(basename "$1")" == claude* ]]; then
  exec "$NONO" run --allow-cwd --profile claude-custom -- "$@"
else
  exec "$NONO" run --allow-cwd --profile claude-custom -- claude "$@"
fi
EOF

chmod +x ~/bin/claude-nono
```
--allow-cwd is required. Without it, nono asks "Share this folder with read+write access? [y/N]" and waits. VS Code can't answer the prompt, so the extension hangs and shows "Not logged in · Please run /login".
4. Point the VS Code extension at the wrapper. Open the Command Palette and choose `Preferences: Open User Settings (JSON)`. Add:
`"claudeCode.claudeProcessWrapper": "/Users/YOURNAME/bin/claude-nono"`
Use the full path, not ~. Get it with `echo ~/bin/claude-nono`.
Put this in **user settings**, not .vscode/settings.json. The project folder is writable inside the sandbox, so Claude could remove a workspace-level setting.
5. Then Open the Command Palette and choose `Developer: Reload Window`.
6. You can see the nono logs with tail /tmp/claude-nono.log
