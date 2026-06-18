---
grounding_kind: flow
status: reviewed
last_verified: "2026-06-18"
source_anchors: []
owners: [platform-team]
---

# Flow: optimize-app — one-command setup of a remote Codex host

The primary end-to-end use case: `codex-remote-ssh optimize-app <profile> --alias <alias>`
turns a saved profile into a fully prepared, low-latency remote Codex host that the Codex
App can attach to. Walked at the seam/module level.

## Trigger

A user has already added a profile (`codex-remote-ssh add <name> <user@host>`) and runs
`optimize-app`. Precondition: local `ssh` can reach the host and remote Codex CLI is
installed. The command handler is [[anchor: src/codex_remote_ssh_kit/cli.py#optimize_app]].

## Actors / boundaries crossed

local CLI -> `~/.ssh/config` (managed block) -> remote host over SSH (`app-server daemon`)
-> macOS `launchd` on both ends -> Codex App (reads the SSH host afterward).

## Steps

1. **Write the managed SSH host.** Upsert the fenced `Host <alias>` block into
   `~/.ssh/config` (ControlMaster/Persist, optional pubkey-only hardening) and persist the
   alias back onto the profile. Touches
   [[anchor: src/codex_remote_ssh_kit/bridge.py#install_official_ssh_config]] and
   [[anchor: src/codex_remote_ssh_kit/bridge.py#build_official_ssh_config_block]].
2. **Bootstrap the remote daemon.** Over SSH, start `codex app-server daemon` and enable
   remote control. Touches [[anchor: src/codex_remote_ssh_kit/bridge.py#bootstrap_remote_daemon]].
3. **Install the remote daemon LaunchAgent.** Push a warmup script + plist to the host and
   `launchctl bootstrap` it (re-runs every `--remote-interval` seconds, warming the daemon
   and recent session files). Touches
   [[anchor: src/codex_remote_ssh_kit/bridge.py#install_remote_daemon_launch_agent]].
4. **Install the local prewarm LaunchAgent.** Write a local script + plist that keeps the
   SSH master warm every `--local-interval` seconds. Touches
   [[anchor: src/codex_remote_ssh_kit/bridge.py#install_local_prewarm_launch_agent]].
5. **Prewarm now + diagnose.** Open the SSH master immediately and collect remote facts for
   the summary. Touches [[anchor: src/codex_remote_ssh_kit/bridge.py#prewarm_ssh_master]] and
   [[anchor: src/codex_remote_ssh_kit/bridge.py#run_diagnostics]].

## Result / postcondition

The alias appears in `~/.ssh/config`; the remote daemon is running with remote control
enabled; both LaunchAgents are loaded; the SSH master is warm. Re-running `optimize-app`
is a no-op-shaped idempotent operation (block upserted, agents bootout-then-bootstrapped).
The user then selects the alias in Codex App Connections.

## Failure & edge cases

If SSH diagnostics fail (INV-005), `doctor` and `optimize-app` surface the failing checks
and the process exits non-zero rather than raising. A missing remote Codex CLI is reported
with a fix hint (`install-remote-codex`). The managed SSH block never disturbs user entries
outside its markers (see INV-003 in `invariants.md`).
