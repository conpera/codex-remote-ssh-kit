---
grounding_kind: adr
status: reviewed
last_verified: "2026-06-18"
source_anchors:
  - path: src/codex_remote_ssh_kit/bridge.py
    symbol: run_diagnostics
  - path: src/codex_remote_ssh_kit/cli.py
    symbol: app
owners: [platform-team]
---

# ADR-0001: Keep all logic in a pure `bridge` module; `cli` is thin Typer transport

## Context

The kit shells out to `ssh`, `launchctl`, and `brew`, builds `~/.ssh/config` blocks, and
parses remote `--help` output. That logic must be unit-testable without a live remote host
or a real Codex install. Mixing it with Typer argument parsing and Rich rendering would
make every behavior test go through `CliRunner` and force network/OS mocking at the CLI
layer. The codebase is small (two source modules), so a heavyweight layered architecture is
not warranted — but a clean transport/logic seam is.

## Decision

We will keep one pure logic module, `bridge`, that contains every command builder,
filesystem writer, diagnostics, and benchmark function and returns plain dataclasses/dicts.
`cli` ([[anchor: src/codex_remote_ssh_kit/cli.py#app]]) is a thin Typer layer that only
parses arguments, calls `bridge`, renders Rich tables, and sets exit codes. `bridge` never
imports `typer`, `rich`, or `cli`.

## Consequences

Easier: `bridge` functions like [[anchor: src/codex_remote_ssh_kit/bridge.py#run_diagnostics]]
are tested directly in `tests/test_bridge.py` by monkeypatching `subprocess`, with no
`CliRunner`; the suite runs fully offline. Harder: a new feature requires touching two files
(the `bridge` function and the `cli` command). Trade-off accepted: a future agent must not
"shortcut" by putting logic in a command handler — that would break the seam and INV-002.

## Invariants established [INV-ids]

- INV-002 — `bridge` MUST NOT import `typer`, `rich`, or `cli`.

## Status

accepted.
