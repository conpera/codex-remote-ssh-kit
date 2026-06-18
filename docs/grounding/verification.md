---
grounding_kind: verification
status: reviewed
last_verified: "2026-06-18"
source_anchors:
  - path: tests/test_bridge.py
  - path: tests/test_cli.py
owners: [platform-team]
---

# Verification Standard

The single answer to "how does anyone — human or AI — prove a change in this repo is
correct?" The whole suite is pure/offline: every test that would shell out to `ssh`,
`launchctl`, `brew`, or `open` monkeypatches `subprocess`, so `uv run pytest` needs no
remote host. Before merging, walk the relevant per-change-type checklist, run the listed
commands, and confirm no invariant (see `invariants.md`) is broken.

## 1. Test map

| Area (what is covered) | Covering tests / files | Command to run | Related invariants |
| --- | --- | --- | --- |
| Profile store round-trip + `0600` mode | `tests/test_bridge.py::test_profile_round_trip`, `::test_save_profiles_omits_empty_optional_values` | `uv run pytest tests/test_bridge.py -k profile` | INV-004 |
| SSH argv / command builders | `tests/test_bridge.py::test_build_ssh_prewarm_command_prefers_alias`, `::test_build_bridge_plan_uses_capability_token_and_loopback_tunnel` | `uv run pytest tests/test_bridge.py -k "prewarm or bridge_plan"` | INV-001 |
| Managed SSH config block (idempotent, isolated) | `tests/test_bridge.py::test_install_official_ssh_config_is_managed_and_idempotent`, `::test_install_official_ssh_config_preserves_multiple_managed_hosts`, `::test_uninstall_official_ssh_config_removes_only_one_managed_host` | `uv run pytest tests/test_bridge.py -k ssh_config` | INV-003 |
| Remote capability diagnostics | `tests/test_bridge.py::test_run_diagnostics_parses_remote_facts`, `tests/test_cli.py::test_doctor_json_reports_issues` | `uv run pytest -k "diagnostics or doctor"` | INV-005 |
| LaunchAgent install/uninstall lifecycle | `tests/test_bridge.py::test_install_local_prewarm_launch_agent_writes_script_and_plist`, `::test_uninstall_local_prewarm_launch_agent_removes_files` | `uv run pytest tests/test_bridge.py -k launch_agent` | INV-006 |
| CLI transport (commands, exit codes, JSON) | `tests/test_cli.py` | `uv run pytest tests/test_cli.py` | INV-002 |
| Whole suite + package build | (all) | `uv run pytest && uv build` | — |

## 2. Per-change-type checklists

### 2a. Feature (new behavior)
- [ ] New/changed behavior has a test that fails without the change and passes with it.
- [ ] If it shells out, the test monkeypatches `subprocess` (no live SSH in CI — see CI: `.github/workflows/test.yml`).
- [ ] Logic added to `bridge`, transport-only glue added to `cli` (INV-002).
- [ ] Re-read `invariants.md`; confirmed no security/do-not-touch invariant is crossed (esp. INV-001, INV-003, INV-004).
- [ ] Updated `code-map.md` (and bumped its `last_verified`) if a module/seam changed; added an ADR if a notable decision was made.
- [ ] Full suite + build green: `uv run pytest && uv build`.

### 2b. Bugfix
- [ ] Added a regression test (red before, green after).
- [ ] Confirmed the fix does not weaken the invariant guarding the area (cite the `INV-NNN`).
- [ ] Ran the **Test map** command for the affected area plus the regression test.

### 2c. Refactor (no intended behavior change)
- [ ] No public contract changed: same CLI command/option surface, same JSON keys, same SSH-config block, same LaunchAgent labels.
- [ ] Existing tests pass unchanged — do not edit a test to make a refactor pass.
- [ ] Dependency-direction rules in `code-map.md` §5 still hold (INV-002).

### 2d. Dependency bump
- [ ] `uv.lock` updated and committed; `uv sync` reproduces from clean.
- [ ] Full suite + build green: `uv run pytest && uv build`.
- [ ] Reviewed the dependency's changelog (Typer/Rich) for breaking changes in the CLI surface.

## 3. Regression signals

The concrete things that catch silent breakage:

- **SSH-config isolation (INV-003):** the managed-block tests must stay green — any change
  that mutates text outside `OFFICIAL_SSH_BEGIN`/`OFFICIAL_SSH_END` is a regression.
- **File modes (INV-004):** `test_profile_round_trip` asserts `0600` on the store; keep it.
- **argv-only commands (INV-001):** SSH/remote command builders return lists, not strings;
  the prewarm/bridge-plan tests assert exact argv.
- **Capability degradation (INV-005):** `doctor` returns issues (exit 1) instead of raising
  when a remote capability is missing — `test_doctor_json_reports_issues` guards this.

## 4. Done means

A change is verifiable-and-verified when: the matching §2 checklist is fully ticked, every
**Test map** command for the touched areas passes, the §3 **Regression signals** are clean,
`uv run pytest && uv build` is green, and no `INV-NNN` referenced here is violated. If you
cannot satisfy an invariant, stop and open an ADR proposing to change it.
