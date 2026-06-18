# Project Grounding (`docs/grounding/`)

This folder is the **verifiable ground-truth layer** for this repository. It exists so that
AI agents and humans get authoritative, anchored context before changing code — preventing
unscoped edits — and so that every change can be **verified** against documented structure
and intent. A drift-checker keeps these docs honest: they cannot silently rot.

This README is documentation for the folder itself. It is **not** a grounding artifact: it
has **no YAML front-matter** and is **not** listed in `grounding.yml`. Only the seven
grounding kinds are registered there.

## What's in here

| File | Kind | What it answers |
| --- | --- | --- |
| `code-map.md` | `code-map` | Structure: modules, boundaries/seams, dependency rules, "where do I change X?" |
| `domain/glossary.md` | `domain-glossary` | Ubiquitous-language terms -> definition -> code anchor |
| `domain/rules.md` | `domain-rules` | Business rules, each linked to an INV-id (if any) and an anchor |
| `domain/flows/<flow-id>.md` | `flow` | One use-case, step by step, each step anchored to the modules it touches |
| `invariants.md` | `invariants` | The "do not break / do not touch" constraints (INV-ids) |
| `verification.md` | `verification` | Test map + per-change-type checklist; how to verify a change |
| `decisions/NNNN-<slug>.md` | `adr` | Why a decision was made; the INV-ids it establishes |
| `grounding.yml` | (config) | Machine-readable index + checker config (not a doc) |

`grounding.yml` lists `required_documents` that MUST exist — by default `code-map`,
`invariants`, and `verification`.

## How to read an artifact

1. **Front-matter first.** Every grounding `.md` (everything except this README) starts on
   line 1 with `---` and a small YAML block: `grounding_kind`, `status`
   (`draft` | `reviewed` | `stale`), `last_verified` (a quoted `"YYYY-MM-DD"` date a human
   last confirmed the doc against the code), `source_anchors` (the real code it describes),
   and optionally `owners`. Trust a doc more when `status: reviewed` and `last_verified` is
   recent.
2. **Follow the anchors.** Front-matter `source_anchors` point at the files a doc covers.
   In the prose, individual claims are traceable via inline anchors written as
   `[[anchor: path/to/file]]` or `[[anchor: path/to/file#Symbol]]`. **All anchor paths are
   relative to the repo root** (not to this folder), never absolute, no leading `./`.
3. **Follow the IDs.** Invariants are `INV-001`, `INV-002`, … (zero-padded, ≥3 digits),
   declared in `invariants.md`. `verification.md`, `domain/rules.md`, and ADRs **reference**
   those same ids. ADRs are `ADR-0001` etc., file `0001-<slug>.md`. A flow's id is its
   filename without `.md` and is kebab-case.

## How to update it (drift mode)

The docs describe code, so they drift when code changes. After a change that affects a
documented area:

1. **Edit the matching artifact** so its prose and anchors again match reality. Keep the
   "right altitude": structure, intent, and constraints — never restate obvious code.
2. **Fix anchors.** If a documented file moved/renamed, update every `source_anchors.path`
   and inline `[[anchor: …]]` that pointed at it. If a referenced `symbol` was renamed,
   update it too (the checker requires the symbol to appear as a whole word in the file).
3. **Refresh the sha drift signal (optional but recommended).** `source_anchors` may carry a
   `sha` — the **committed blob sha**. After you commit, capture it with:

   ```sh
   git rev-parse HEAD:path/to/file
   ```

   Paste that value into the anchor's `sha`. The checker compares against the exact same
   command, so use **only** this form — do **not** use `git hash-object` or a working-tree
   sha, or drift will be masked or faked. (If a doc has no `sha`, drift simply isn't tracked
   for that anchor; that's fine.)
4. **Bump the metadata.** Set `last_verified` to today's date (quoted), and set `status`
   appropriately: `reviewed` once confirmed, `stale` if you know it lags, `draft` while in
   progress.
5. **Register new docs.** If you add a new artifact, add a `documents:` entry to
   `grounding.yml` with its `file:` (relative to this folder) and `kind:` — and the `kind`
   MUST equal that doc's own `grounding_kind`. The checker reads **only** the documents
   listed in `grounding.yml`; it does not glob the folder. (Do **not** add this README.)
6. **New invariants?** Declare each new `INV-NNN` as a row in `invariants.md` *before* (or
   together with) referencing it from `verification.md`, `rules.md`, or an ADR — a reference
   to an undeclared INV-id is an error.
7. **Run the checker** (below) and fix everything it reports.

### Front-matter rules the checker enforces

- Line 1 is exactly `---`; the block ends at the next line that is exactly `---`. No blank
  line, BOM, or comment before the opening `---`.
- Allowed top-level keys are **exactly** `grounding_kind`, `status`, `last_verified`,
  `source_anchors`, `owners` — any unknown key is an error. Per-anchor keys are exactly
  `path` (required), `symbol`, `sha`.
- Indent with **2 spaces** (never tabs). A `source_anchors` item is `  - path: …` with its
  sibling keys (`symbol`, `sha`) aligned at 4 spaces.
- Quote dates and any value containing `:`, `#`, quotes, or leading/trailing space.
- `source_anchors: []` is valid — a doc with no anchors passes.

## How to run the checker

The checker is Python 3 **stdlib-only** (no third-party packages). From the repo root:

```sh
python3 scripts/check_grounding.py
```

Useful flags:

- `--dir <grounding_dir>` — point at the grounding folder (default `docs/grounding`).
- `--strict` — promote **all** warnings (sha drift, staleness) to errors. Equivalent to
  `strict: true` in `grounding.yml`; the effective mode is the OR of the two.
- `--quiet` — suppress the per-item report, but still print the final summary and return the
  exit code.

**What it checks:** required fields + valid enum values; all `required_documents` present
(matched by kind); every `source_anchors.path` and inline-anchor path exists; declared
`symbol`s appear in their files; `grounding.yml` `kind` matches each doc's `grounding_kind`;
every INV-id referenced in `verification.md` / `rules.md` / ADRs is declared in
`invariants.md`. **Warnings:** sha drift and staleness (older than `staleness_days`, default
90). If git is unavailable, sha drift checks are skipped silently; path and symbol checks
still run.

**Exit code:** `0` when there are no errors (warnings are allowed unless strict); `1`
otherwise — suitable for a CI gate.

## CI note

Run the checker on every push / pull request and fail the build on a nonzero exit. Example
GitHub Actions step:

```yaml
- name: Verify project grounding
  run: python3 scripts/check_grounding.py --strict
```

`--strict` (or `strict: true` in `grounding.yml`) turns staleness and sha-drift warnings
into hard failures, so documentation that has fallen behind the code blocks the merge.
Drop `--strict` if you want drift to surface as warnings without gating.
