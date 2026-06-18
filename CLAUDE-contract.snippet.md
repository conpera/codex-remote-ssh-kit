<!-- append into the repo's CLAUDE.md / AGENTS.md -->
## House rules (CONTRACT)

### Enforceable (each MUST name its guard)
- NEVER <forbidden action> (INV-00x · <test/lint that enforces it>).
- Destructive ops / prod access / secrets: stop and ask a human first.

### Soft taste (review-only, decay-exempt — NOT machine-checked)
- <conventions, naming, preferences in the ubiquitous language>

## Capturing knowledge (where things go)
- Reusable lesson/pitfall → `docs/memory/<pat|lrn|pit>-NNN-*.md`
- Repeatable procedure → `docs/runbooks/` (repo) or global `sops/` (cross-repo)
- Architecture decision → `docs/grounding/decisions/` (ADR, immutable)
- Structure/invariants/verification → `docs/grounding/` (run the project-grounding skill)
