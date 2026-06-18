# Runbook: set up a remote Codex host for Codex App Remote SSH

**Trigger:** you have a reachable remote Mac with Codex CLI installed and want the Codex
App to attach to it over SSH with warm, low-latency reconnects.

**Preconditions:** local macOS with `ssh`, Python 3.10+, and `uv`; remote SSH reachable;
remote `codex` installed and logged in.

1. Confirm the remote Codex surface exists:
   `ssh user@host 'codex --version && codex app-server daemon --help && codex remote-control --help'`
2. Install this CLI locally:
   `uv tool install git+https://github.com/conpera/codex-remote-ssh-kit.git`
   (or from a clone: `uv tool install .`)
3. Save the profile:
   `codex-remote-ssh add studio user@host.local --identity-file ~/.ssh/id_ed25519`
4. Run the one-command setup (writes the managed SSH block, bootstraps the remote daemon,
   installs both LaunchAgents, prewarms the SSH master):
   `codex-remote-ssh optimize-app studio --alias codex-studio`
5. **Verify:** `codex-remote-ssh doctor studio` reports `Overall: OK` (exit 0). For machine
   output: `codex-remote-ssh doctor studio --json`.
6. Open Codex App and select the `codex-studio` host under Connections / Remote SSH.
7. (Optional) Measure warm attach latency: `codex-remote-ssh benchmark studio`.

**If `doctor` reports drift:** `codex-remote-ssh doctor studio --repair --alias codex-studio`,
then re-run step 4 if issues remain. If remote Codex is missing/old:
`codex-remote-ssh install-remote-codex studio` then re-run step 4.

**Rollback:** `codex-remote-ssh uninstall studio` removes the managed SSH block, both
LaunchAgents, their scripts/logs, and the saved profile.

**Validated-by:** commands transcribed from `README.md` and the CLI command surface in
`src/codex_remote_ssh_kit/cli.py`. (No `last_verified` field — no job stamps it yet.)
