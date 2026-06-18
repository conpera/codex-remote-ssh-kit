---
grounding_kind: domain-glossary
status: reviewed
last_verified: "2026-06-18"
source_anchors: []
owners: [platform-team]
---

# Domain Glossary — Ubiquitous Language

The shared, authoritative vocabulary for this codebase. One row per term: the term as it
appears in code and docs, a one-line definition in plain language, and the code anchor
where the concept actually lives.

## Terms

| Term | Definition | Code anchor |
| --- | --- | --- |
| Profile | A saved connection definition for one remote Codex host (name, SSH target, port, identity file, alias, ports, workspace). The single persisted entity of the kit. | [[anchor: src/codex_remote_ssh_kit/bridge.py#RemoteCodexProfile]] |
| Alias | The SSH `Host` name written into `~/.ssh/config` and shown to the Codex App (e.g. `codex-studio`). Distinct from the internal profile name. | [[anchor: src/codex_remote_ssh_kit/bridge.py#build_official_ssh_config_block]] |
| Managed SSH block | The fenced region of `~/.ssh/config` (between the begin/end markers) that this tool owns and rewrites idempotently; everything outside is user-owned. | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_official_ssh_config]] |
| Prewarm | Opening (or reusing) a background SSH `ControlMaster` connection so the next attach skips the TCP/auth handshake. | [[anchor: src/codex_remote_ssh_kit/bridge.py#prewarm_ssh_master]] |
| Remote daemon | The `codex app-server daemon` on the remote host that keeps the Codex runtime and remote-control warm independent of the local App. | [[anchor: src/codex_remote_ssh_kit/bridge.py#bootstrap_remote_daemon]] |
| LaunchAgent | A macOS `launchd` job (local prewarm or remote daemon warmup) installed under a `com.conpera.codex-remote-ssh.<profile>.*` label. | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_local_prewarm_launch_agent]] |
| Diagnostics / facts | The dict of remote facts collected over one SSH round-trip (latency, codex version, capability flags, session counts) that drives `check`/`doctor`. | [[anchor: src/codex_remote_ssh_kit/bridge.py#run_diagnostics]] |
| Bridge plan | The fallback path: commands + token to start a remote `app-server` over a loopback SSH tunnel when the official daemon path is unavailable. | [[anchor: src/codex_remote_ssh_kit/bridge.py#build_bridge_plan]] |

## How to use this table

- Definitions state intent; the anchor carries the implementation.
- One anchor per term — the canonical symbol that owns the concept.
- "Profile name" vs "alias" is the most common confusion: the name keys the local store;
  the alias is the SSH `Host` the App connects to.
