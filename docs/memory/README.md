# Project memory — codex-remote-ssh-kit

Reusable lessons, patterns, decisions, pitfalls for **this repo**, beside the code.
Read automatically: the conpera memory-recall hook injects the most relevant at the
top of every prompt run in this repo; the nightly sleep-cycle harvests cross-cutting
ones to the global store.

## Capture (agents: the moment you learn something reusable, write it)

`docs/memory/<type>-<NNN>-<slug>.md`, types: `pat` pattern · `lrn` lesson ·
`dec` decision · `pit` pitfall · `cmd` verified-command. Frontmatter:

```yaml
---
id: <type>-<NNN>          # == filename head
type: pattern             # pattern|lesson|decision|pitfall|verified-command
tags: [<2-6>]
scope: project:codex-remote-ssh-kit   # cross-cutting? tooling|engineering|research → harvested global
related: []
status: active
decay: recall             # EXPERIENCE fades by disuse; this is the only kind that should
---
```
One idea per file, specific > general. `_index.yaml` is auto-generated.
