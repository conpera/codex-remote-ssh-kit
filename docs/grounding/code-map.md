---
grounding_kind: code-map
status: reviewed
last_verified: "2026-06-18"
source_anchors:
  - path: src/codex_remote_ssh_kit/bridge.py
    symbol: RemoteCodexProfile
  - path: src/codex_remote_ssh_kit/cli.py
    symbol: app
owners: [platform-team]
---

# Code Map

The structural ground truth for this repo: what the modules are, where they meet
(the breakage-prone seams), which way dependencies point, and where to go to change a
given thing. Entries stay at the module / boundary / contract level. Live claims are
anchored to real files/symbols, which the drift-checker resolves.

## 1. System overview

`codex-remote-ssh-kit` is a single-binary Python Typer CLI (`codex-remote-ssh`) that
prepares the SSH and Codex-daemon layer used by the Codex desktop App's Remote SSH
feature. It does three things: writes a managed `Host` block into `~/.ssh/config` with
OpenSSH `ControlMaster`/`ControlPersist` connection reuse, installs macOS `launchd`
LaunchAgents (local SSH-master prewarm + remote Codex daemon/session warmup), and runs
diagnostics/benchmarks over SSH against the remote host. All real logic lives in one
pure module ([[anchor: src/codex_remote_ssh_kit/bridge.py#RemoteCodexProfile]]); the CLI
([[anchor: src/codex_remote_ssh_kit/cli.py#app]]) is a thin Typer transport layer that
calls it and renders Rich tables.

## 2. Module inventory

One row per module. `entry` is the file/symbol you open first.

| Module | Purpose (1 line) | Entry | Key files | Depends-on | Depended-by |
| --- | --- | --- | --- | --- | --- |
| `cli` | Typer transport: defines every subcommand, parses args, renders Rich output, sets exit codes | [[anchor: src/codex_remote_ssh_kit/cli.py#app]] | `src/codex_remote_ssh_kit/cli.py` | `bridge` | (end user / shell) |
| `bridge` | All real logic: profile store, SSH command builders, diagnostics, benchmarks, SSH-config block, LaunchAgent install/uninstall | [[anchor: src/codex_remote_ssh_kit/bridge.py#RemoteCodexProfile]] | `src/codex_remote_ssh_kit/bridge.py` | (stdlib: `subprocess`, `plistlib`, `socket`) | `cli` |
| `package` | Package metadata + version + console-script entry point | [[anchor: src/codex_remote_ssh_kit/__init__.py#__version__]] | `src/codex_remote_ssh_kit/__init__.py`, `pyproject.toml` | — | `cli`, `bridge` |

## 3. Boundaries / seams

The interfaces most likely to break when changed. The kit's seams are almost all
external operating-system / filesystem contracts, not internal APIs.

| Seam | Kind | What crosses it | Owner / definition | Compatibility rule |
| --- | --- | --- | --- | --- |
| Managed `~/.ssh/config` block | file | A `Host` block fenced by begin/end markers; Codex App reads it for Remote SSH | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_official_ssh_config]], markers `OFFICIAL_SSH_BEGIN`/`OFFICIAL_SSH_END` | Only the fenced region is owned; user entries outside the markers MUST stay byte-untouched. Upsert/remove is idempotent and keyed by alias. |
| Profile store `~/.codex-remote-ssh-kit/hosts.json` | file | JSON `{profiles: {name: {...}}}` written `0600` | [[anchor: src/codex_remote_ssh_kit/bridge.py#save_profiles]] / [[anchor: src/codex_remote_ssh_kit/bridge.py#load_profiles]] | Round-trips `RemoteCodexProfile`; empty optionals are dropped on save and re-defaulted on load. File mode stays `0600`. |
| LaunchAgent plists + labels | launchd | `com.conpera.codex-remote-ssh.<profile>.{prewarm,daemon}` plists and shell scripts | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_local_prewarm_launch_agent]], [[anchor: src/codex_remote_ssh_kit/bridge.py#install_remote_daemon_launch_agent]] | Label scheme is the uninstall contract; bootout-before-bootstrap keeps install idempotent. |
| Remote Codex CLI surface | SSH/CLI | `codex --version`, `codex app-server [--help|daemon ...]`, `codex remote-control` probed over SSH | [[anchor: src/codex_remote_ssh_kit/bridge.py#run_diagnostics]] | Capability-detected (grep of `--help`), never assumed; missing capabilities become `doctor` issues, not crashes. |
| Fallback app-server bridge token | file | Capability token written to `/tmp/codex-remote-ssh-<name>.token` mode `0600` | [[anchor: src/codex_remote_ssh_kit/bridge.py#build_bridge_plan]] | Token files are `0600`; tokens are never persisted to the profile store or logged in full (preview only). |

## 4. Data-model pointers

- Connection profile (the one persisted entity): [[anchor: src/codex_remote_ssh_kit/bridge.py#RemoteCodexProfile]] — frozen dataclass; `ssh_base_args` / `ssh_target` derive every SSH invocation from it.
- Result value objects (returned to the CLI for rendering): [[anchor: src/codex_remote_ssh_kit/bridge.py#RemoteCommandResult]], `LaunchAgentInstallResult`, `LaunchAgentStatusResult`, `CleanupResult`, `OfficialSshConfigResult`, `BridgePlan` in `src/codex_remote_ssh_kit/bridge.py`.
- Diagnostics fact dict shape: produced by [[anchor: src/codex_remote_ssh_kit/bridge.py#run_diagnostics]] (keys `ok`, `latency_ms`, `codex_version`, `app_server`, `app_server_daemon`, `remote_control`, ...).
- On-disk JSON store layout: [[anchor: src/codex_remote_ssh_kit/bridge.py#save_profiles]].

## 5. Dependency-direction rules

**Allowed (imports point this way):**
- `cli` -> `bridge` (transport depends inward on logic). Every symbol `cli` uses is imported from `bridge` at [[anchor: src/codex_remote_ssh_kit/cli.py#app]].
- `bridge` -> Python stdlib only (`subprocess`, `plistlib`, `socket`, `secrets`, `json`, `shlex`). No third-party imports.

**Forbidden:**
- `bridge` -> `cli` (logic must never import the Typer/Rich transport layer; cite INV-002).
- `bridge` -> `typer` / `rich` (keeps the logic layer headless and unit-testable; cite INV-002).
- Building SSH commands by string-concatenating user input instead of going through `RemoteCodexProfile.ssh_base_args` + list-form argv (cite INV-001).

## 6. Where do I change X? (task -> files index)

| Task ("I want to change…") | Files to edit | Watch out for |
| --- | --- | --- |
| Add a new CLI subcommand | `src/codex_remote_ssh_kit/cli.py` (add `@app.command`), `src/codex_remote_ssh_kit/bridge.py` (add the logic fn) | Keep logic in `bridge`; `cli` only parses/renders (§5, INV-002). |
| Change what the SSH config block contains | [[anchor: src/codex_remote_ssh_kit/bridge.py#build_official_ssh_config_block]] | Stay inside the managed markers; never touch user entries (§3, INV-003). |
| Change profile fields / storage | [[anchor: src/codex_remote_ssh_kit/bridge.py#RemoteCodexProfile]], `save_profiles`/`load_profiles` | Preserve round-trip + `0600` mode (§3, INV-004). |
| Change a LaunchAgent's schedule/script | [[anchor: src/codex_remote_ssh_kit/bridge.py#install_remote_daemon_launch_agent]], `install_local_prewarm_launch_agent` | Label scheme is the uninstall key; keep bootout-before-bootstrap (§3). |
| Add a remote capability probe | [[anchor: src/codex_remote_ssh_kit/bridge.py#run_diagnostics]] and the issue list in [[anchor: src/codex_remote_ssh_kit/cli.py#app]] | Detect via `--help` grep; degrade to a doctor issue, never crash. |
