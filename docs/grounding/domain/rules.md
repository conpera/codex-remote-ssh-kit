---
grounding_kind: domain-rules
status: reviewed
last_verified: "2026-06-18"
source_anchors: []
owners: [platform-team]
---

# Domain Rules — Business Logic

The rules this kit enforces, in plain language. Each rule is one "the system MUST / MUST
NOT ..." statement, optionally linked to the `INV-id` that hardens it, plus a code anchor
to where it is implemented. Rules describe intent; invariants are the enforceable
constraints an agent may not break.

## Rules

| Rule (plain statement) | INV-id | Code anchor |
| --- | --- | --- |
| Writing the SSH host for the App MUST only add/replace the tool's own fenced block; user-authored config MUST be left intact. | INV-003 | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_official_ssh_config]] |
| Re-running setup MUST be safe to repeat: installing a LaunchAgent boots out any prior agent of the same label before bootstrapping it. | INV-006 | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_local_prewarm_launch_agent]] |
| Every command sent to a remote host MUST be assembled from the profile's SSH argv plus a single payload arg — never an interpolated shell string. | INV-001 | [[anchor: src/codex_remote_ssh_kit/bridge.py#ssh_base_args]] |
| The saved profile store MUST be written private (`0600`) and MUST NOT contain API keys or tokens. | INV-004 | [[anchor: src/codex_remote_ssh_kit/bridge.py#save_profiles]] |
| Remote Codex features are probed, not assumed: a missing `app-server`/`daemon`/`remote-control` becomes a reported `doctor` issue, not a crash. | INV-005 | [[anchor: src/codex_remote_ssh_kit/bridge.py#run_diagnostics]] |
| Public-key-only SSH hardening is added ONLY when the profile supplies an identity file; otherwise password/keyboard-interactive auth is left available. | — | [[anchor: src/codex_remote_ssh_kit/bridge.py#build_official_ssh_config_block]] |
| Cold-attach measurement (`--include-cold`) MUST be opt-in, because it tears down the live SSH master that an active App attach may be using. | — | [[anchor: src/codex_remote_ssh_kit/bridge.py#benchmark_remote_profile]] |

## How to use this table

- One rule per row, one assertion per rule.
- When a rule is safety-critical (security, file modes, shared config), it is promoted to
  a declared row in `invariants.md` and cited here by `INV-NNN`.
- Anchors point at the function that actually upholds the rule, not at every call site.
