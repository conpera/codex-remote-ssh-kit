---
grounding_kind: invariants
status: reviewed
last_verified: "2026-06-18"
source_anchors:
  - path: src/codex_remote_ssh_kit/bridge.py
    symbol: install_official_ssh_config
owners: [platform-team]
---

# Invariants

Constraints an agent or human MUST obey, and the forbidden zones they must NEVER touch.
Each row is one invariant: a `MUST`/`NEVER` statement, its category, how to verify it
still holds, and an anchor to the code it governs. IDs are `INV-NNN`, declared only here;
other docs reference them. Allowed categories: `security` | `data-integrity` |
`architecture` | `do-not-touch`.

| INV-id  | statement (MUST / NEVER) | category | how-to-verify | anchor |
|---------|---------------------------|----------|----------------|--------|
| INV-001 | SSH/remote commands MUST be built as an argv list derived from `RemoteCodexProfile.ssh_base_args`; user input (target, identity file, port, extra args) MUST NEVER be string-concatenated into a shell command. | security | Read every `subprocess.run`/`Popen` call site — the first element is always `profile.ssh_base_args(...)` (list), and remote payloads are passed as a single trailing arg. Run `uv run pytest tests/test_bridge.py`. | [[anchor: src/codex_remote_ssh_kit/bridge.py#ssh_base_args]] |
| INV-002 | The `bridge` module MUST stay headless: it MUST NEVER import `typer`, `rich`, or the `cli` module. All Typer/Rich code lives in `cli`. | architecture | `grep -n "import typer\|import rich\|from rich\|codex_remote_ssh_kit.cli" src/codex_remote_ssh_kit/bridge.py` returns nothing. | [[anchor: src/codex_remote_ssh_kit/bridge.py#RemoteCodexProfile]] |
| INV-003 | SSH-config writes MUST only touch the region fenced by `OFFICIAL_SSH_BEGIN`/`OFFICIAL_SSH_END`; user-authored entries outside the markers MUST NEVER be modified or reordered. | do-not-touch | Run `uv run pytest tests/test_bridge.py -k ssh_config`; review `_replace_managed_section`, which splits on the markers and preserves `before`/`after` text verbatim. | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_official_ssh_config]] |
| INV-004 | The profile store and any token file MUST be written with mode `0600`; secrets MUST NEVER be world-readable, and OpenAI/API keys MUST NEVER be written into the store. | security | `save_profiles` calls `path.chmod(0o600)`; the bridge plan writes the token file with `chmod 600`. Run `uv run pytest tests/test_bridge.py -k round_trip`. | [[anchor: src/codex_remote_ssh_kit/bridge.py#save_profiles]] |
| INV-005 | Remote Codex capabilities (`app-server`, `daemon`, `remote-control`, `--ws-auth`) MUST be capability-detected by probing `--help`; the kit MUST NEVER assume a capability exists, and a missing one MUST degrade to a `doctor` issue rather than an unhandled error. | architecture | Read `run_diagnostics` (each capability is a `grep -q` of `--help`); run `uv run pytest tests/test_cli.py -k doctor`. | [[anchor: src/codex_remote_ssh_kit/bridge.py#run_diagnostics]] |
| INV-006 | LaunchAgent install MUST be idempotent: it MUST `bootout` any existing agent for the label before `bootstrap`, and the `com.conpera.codex-remote-ssh.<profile>.{prewarm,daemon}` label scheme MUST stay stable because `uninstall` targets agents by that exact label. | data-integrity | Read `install_local_prewarm_launch_agent` (bootout precedes bootstrap) and confirm `uninstall_*` derives the same label; run `uv run pytest tests/test_bridge.py -k launch_agent`. | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_local_prewarm_launch_agent]] |

## Notes

- `INV-001`/`INV-004` are the security spine of this tool: it shells out to `ssh` and
  writes credentials-adjacent files, so argv-only command construction and `0600` file
  modes are non-negotiable.
- `INV-003` is the do-not-touch zone: the user's `~/.ssh/config` is shared, hand-edited
  state. Only the fenced block is owned; everything else is read-only to this tool.
