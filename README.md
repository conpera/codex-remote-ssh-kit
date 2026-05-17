# Codex Remote SSH Kit

Make Codex App Remote SSH feel closer to VS Code Remote: the remote machine keeps the Codex daemon, sessions, and recent history warm, while your local machine keeps the SSH control connection ready.

This project does not replace Codex App. It prepares the SSH and daemon layer that Codex App already uses.

## What It Solves

Codex App remote workflows usually need three things:

- Remote execution should continue after the local laptop sleeps, disconnects, or closes Codex App.
- Reconnecting should feel instant, without paying a fresh SSH handshake every time.
- Session history should already be warm when the App opens the remote host.

This kit configures those pieces with normal system primitives:

- OpenSSH `ControlMaster` / `ControlPersist`
- Codex CLI `app-server daemon`
- macOS `launchd` LaunchAgents
- Session index warmup for recent Codex JSONL files

## Requirements

Local machine:

- macOS
- `ssh`
- Python 3.10+
- `uv` or `pipx`
- Codex App installed

Remote machine:

- macOS
- SSH reachable from the local machine
- Codex CLI installed and logged in
- `codex app-server daemon` and `codex remote-control` available

Check the remote Codex CLI:

```bash
ssh user@host 'codex --version && codex app-server daemon --help && codex remote-control --help'
```

## Install

One-line install from GitHub:

```bash
uv tool install git+https://github.com/conpera/codex-remote-ssh-kit.git
```

From source:

```bash
git clone https://github.com/conpera/codex-remote-ssh-kit.git
cd codex-remote-ssh-kit
uv tool install .
```

Or run inside the repo:

```bash
uv run codex-remote-ssh --help
```

## Quick Start

Add a remote profile:

```bash
codex-remote-ssh add studio user@host.local \
  --identity-file ~/.ssh/id_ed25519
```

Optimize it for Codex App:

```bash
codex-remote-ssh optimize-app studio \
  --alias codex-studio
```

Then open Codex App and select the `codex-studio` SSH host in Connections / Remote SSH.

Check the setup:

```bash
codex-remote-ssh doctor studio
```

Repair common setup drift:

```bash
codex-remote-ssh doctor studio --repair --alias codex-studio
```

Measure warm attach latency:

```bash
codex-remote-ssh benchmark studio
```

## What `optimize-app` Does

`optimize-app` is the normal one-command setup:

1. Writes a managed SSH Host block to `~/.ssh/config`.
2. Enables SSH connection reuse with `ControlMaster auto` and `ControlPersist 30m`.
3. Narrows authentication to public key when an identity file is provided.
4. Ensures the remote Codex app-server daemon is running with remote control enabled.
5. Installs a local LaunchAgent that keeps the SSH master connection warm.
6. Installs a remote LaunchAgent that keeps the Codex daemon healthy.
7. Preloads `~/.codex/session_index.jsonl` and recent session JSONL files on the remote host.

The local LaunchAgent runs every 60 seconds. The remote LaunchAgent runs every 300 seconds by default.

## Commands

```bash
# Save or update a profile
codex-remote-ssh add studio user@host.local --identity-file ~/.ssh/id_ed25519

# Write only the SSH config block for Codex App
codex-remote-ssh official-bootstrap studio --alias codex-studio

# Ensure the remote Codex daemon is running
codex-remote-ssh bootstrap-daemon studio

# Keep the local SSH master connection warm
codex-remote-ssh prewarm studio

# Do all setup steps
codex-remote-ssh optimize-app studio --alias codex-studio

# Inspect remote status
codex-remote-ssh check studio

# Inspect the full product setup and get repair hints
codex-remote-ssh doctor studio

# Repair common setup drift, then re-run diagnostics
codex-remote-ssh doctor studio --repair --alias codex-studio

# Measure warm SSH, daemon, and session-index latency
codex-remote-ssh benchmark studio

# Install or upgrade Codex CLI on the remote host via Homebrew
codex-remote-ssh install-remote-codex studio

# Upgrade remote Codex via Homebrew cask
codex-remote-ssh upgrade-remote studio

# Remove managed SSH config, LaunchAgents, and the saved profile
codex-remote-ssh uninstall studio
```

## Agent One-Shot Deploy Prompt

Use this prompt with Codex or another coding agent on your local machine:

```text
You are helping me set up Codex App Remote SSH so my local Codex App can use a remote Mac as the execution host.

Goal:
- Remote Codex turns must continue when my local laptop sleeps, disconnects, or closes Codex App.
- Reconnecting should be low-latency.
- Recent Codex sessions should be warmed before I open the App.

Use this repo:
https://github.com/conpera/codex-remote-ssh-kit

Remote target:
- Profile name: <profile-name>
- SSH target: <user@host-or-ip>
- SSH alias for Codex App: <codex-remote-alias>
- Identity file: <path-to-private-key-or-none>

Tasks:
1. Verify local `ssh` can reach the remote host.
2. Verify remote `codex --version`, `codex app-server daemon --help`, and `codex remote-control --help`.
3. Install this CLI locally with `uv tool install git+https://github.com/conpera/codex-remote-ssh-kit.git`, or clone the repo and run it with `uv run`.
4. Add the profile with `codex-remote-ssh add`.
5. Run `codex-remote-ssh optimize-app <profile-name> --alias <codex-remote-alias>`.
6. Run `codex-remote-ssh doctor <profile-name> --json`.
7. If Codex is missing or outdated on the remote host, run `codex-remote-ssh install-remote-codex <profile-name>`.
8. If `doctor` reports drift, run `codex-remote-ssh doctor <profile-name> --repair --alias <codex-remote-alias>`.
9. Re-run `codex-remote-ssh optimize-app <profile-name> --alias <codex-remote-alias>` if repair still reports issues.
10. Measure latency with `codex-remote-ssh benchmark <profile-name> --json`.
11. Summarize what was installed, where the LaunchAgents live, current daemon status, and the measured latency.

Do not copy API keys or secrets into files. Do not modify unrelated SSH Host entries.
```

## How It Works

The remote machine owns the long-running Codex runtime. Your local Codex App is only the client.

When a turn has been submitted to the remote daemon, closing the local App or disconnecting SSH should not stop the remote work. When you reconnect, the App attaches back to the remote session state.

Unsubmitted text in a local input box is still local state. It cannot run remotely until the App sends it.

## Files Installed

Local machine:

- `~/.codex-remote-ssh-kit/hosts.json`
- `~/.codex-remote-ssh-kit/bin/*.sh`
- `~/.codex-remote-ssh-kit/logs/*.log`
- `~/Library/LaunchAgents/com.conpera.codex-remote-ssh.<profile>.prewarm.plist`
- A managed SSH block in `~/.ssh/config`

Remote machine:

- `~/.codex-remote-ssh-kit/bin/*.sh`
- `~/.codex-remote-ssh-kit/logs/*.log`
- `~/Library/LaunchAgents/com.conpera.codex-remote-ssh.<profile>.daemon.plist`

## Safety Notes

- The App authenticates using your existing SSH setup.
- This tool does not store OpenAI API keys.
- The SSH config block is wrapped in managed markers and can be replaced idempotently.
- Multiple remote hosts are supported inside the same managed SSH config section.
- Token files used by the fallback app-server bridge are created under `/tmp` with mode `0600`.
- Public-key-only SSH hardening is only added when an identity file is provided.

## Troubleshooting

Run this first:

```bash
codex-remote-ssh doctor studio
```

Codex App does not show the host:

- Confirm the managed host exists: `grep -A20 codex-studio ~/.ssh/config`
- Re-run: `codex-remote-ssh official-bootstrap studio --alias codex-studio`
- Restart Codex App after changing SSH config.

SSH is still slow:

- Run: `codex-remote-ssh prewarm studio`
- Measure warm attach: `codex-remote-ssh benchmark studio`
- Check the local LaunchAgent: `launchctl print gui/$(id -u)/com.conpera.codex-remote-ssh.studio.prewarm`

To measure a cold SSH attach once, run:

```bash
codex-remote-ssh benchmark studio --include-cold
```

`--include-cold` closes the current SSH master connection once before measuring. Avoid it during an active Codex App attach if you do not want to disturb that connection.

Remote Codex is missing or too old:

```bash
codex-remote-ssh install-remote-codex studio
codex-remote-ssh optimize-app studio --alias codex-studio
```

Remote sessions do not appear:

- Verify the remote host is the one selected in Codex App.
- Check `codex-remote-ssh doctor studio`.
- Confirm remote session files exist: `ssh codex-studio 'ls ~/.codex/sessions | tail'`.

Clean rollback:

```bash
codex-remote-ssh uninstall studio
```

## Limitations

- macOS LaunchAgent support is implemented first.
- Linux systemd support is not implemented yet.
- This project does not patch the official Codex App UI.
- If the official Codex App changes its remote behavior, this kit should still be useful at the SSH/daemon layer, but App-specific behavior may differ.

## Development

```bash
uv sync
uv run pytest
uv run codex-remote-ssh --help
```

## License

MIT
