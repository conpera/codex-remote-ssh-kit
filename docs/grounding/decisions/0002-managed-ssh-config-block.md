---
grounding_kind: adr
status: reviewed
last_verified: "2026-06-18"
source_anchors:
  - path: src/codex_remote_ssh_kit/bridge.py
    symbol: install_official_ssh_config
owners: [platform-team]
---

# ADR-0002: Own only a marker-fenced block of `~/.ssh/config`

## Context

The Codex desktop App discovers remote hosts from the user's `~/.ssh/config`, so the kit
must write an SSH `Host` entry there. But `~/.ssh/config` is shared, hand-edited, security-
sensitive state full of the user's other hosts. Rewriting the whole file, or appending
duplicate `Host` entries on every re-run, would corrupt user config and break idempotency.
We also support managing several remote Codex hosts from the same tool.

## Decision

We will own only a single section of the file fenced by the markers
`# >>> codex-remote-ssh official hosts >>>` / `# <<< ... <<<`. Install parses the existing
file, replaces just the fenced section (keyed by alias, so multiple managed hosts coexist),
and writes the surrounding user text back byte-for-byte. Implemented in
[[anchor: src/codex_remote_ssh_kit/bridge.py#install_official_ssh_config]] via
[[anchor: src/codex_remote_ssh_kit/bridge.py#build_official_ssh_config_block]]; uninstall
removes one alias's block and collapses the section when empty.

## Consequences

Easier: re-running setup is idempotent (upsert by alias), multiple hosts share one managed
section, and uninstall is precise. Harder: the marker/alias scheme is now a compatibility
contract — renaming the markers or the alias-keying logic would orphan previously written
blocks. Trade-off accepted and hardened as an invariant so agents never touch user entries
outside the fence.

## Invariants established [INV-ids]

- INV-003 — SSH-config writes MUST stay inside the fenced block; user entries outside the
  markers MUST NEVER be modified.

## Status

accepted.
