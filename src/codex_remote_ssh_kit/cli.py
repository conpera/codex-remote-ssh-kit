from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codex_remote_ssh_kit.bridge import (
    DEFAULT_REMOTE_PORT,
    LaunchAgentStatusResult,
    RemoteCodexProfile,
    RemoteCommandResult,
    benchmark_remote_profile,
    bootstrap_remote_daemon,
    build_remote_install_command,
    build_remote_upgrade_command,
    build_ssh_prewarm_command,
    check_local_prewarm_launch_agent,
    check_remote_daemon_launch_agent,
    default_state_path_from_env,
    get_profile,
    install_local_prewarm_launch_agent,
    install_official_ssh_config,
    install_remote_daemon_launch_agent,
    load_profiles,
    prewarm_ssh_master,
    run_diagnostics,
    save_profiles,
    uninstall_local_prewarm_launch_agent,
    uninstall_official_ssh_config,
    uninstall_remote_daemon_launch_agent,
    upsert_profile,
)


console = Console()

app = typer.Typer(
    name="codex-remote-ssh",
    help="Bootstrap and optimize Codex App Remote SSH hosts.",
    no_args_is_help=True,
)


@app.command("add")
def add_profile(
    name: Annotated[str, typer.Argument(help="Profile name, for example studio.")],
    target: Annotated[str, typer.Argument(help="SSH target, for example user@host.local.")],
    port: Annotated[int | None, typer.Option("--port", help="SSH port.")] = None,
    identity_file: Annotated[Path | None, typer.Option("--identity-file", help="SSH identity file.")] = None,
    remote_port: Annotated[int, typer.Option("--remote-port", help="Fallback app-server listen port.")] = DEFAULT_REMOTE_PORT,
    local_port: Annotated[int | None, typer.Option("--local-port", help="Preferred local forwarded port.")] = None,
    remote_workspace: Annotated[str, typer.Option("--remote-workspace", help="Remote workspace root.")] = "~",
    ssh_alias: Annotated[str | None, typer.Option("--ssh-alias", help="SSH config Host alias to prefer.")] = None,
    ssh_arg: Annotated[list[str] | None, typer.Option("--ssh-arg", help="Extra ssh argument, repeatable.")] = None,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
) -> None:
    profile = RemoteCodexProfile(
        name=name,
        target=target,
        port=port,
        identity_file=str(identity_file.expanduser()) if identity_file else None,
        remote_port=remote_port,
        local_port=local_port,
        remote_workspace=remote_workspace,
        extra_ssh_args=ssh_arg or [],
        ssh_alias=ssh_alias,
    )
    upsert_profile(profile, _state_path(state))
    _print_profile_table([profile], title="Profile Saved")


@app.command("list")
def list_profiles(
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profiles = load_profiles(_state_path(state))
    if json_output:
        console.print_json(json.dumps({"profiles": {name: profile.__dict__ for name, profile in profiles.items()}}))
        return
    _print_profile_table(list(profiles.values()), title="Profiles")


@app.command("remove")
def remove_profile(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
) -> None:
    path = _state_path(state)
    profiles = load_profiles(path)
    if name not in profiles:
        _error(f"Profile not found: {name}")
        raise typer.Exit(1)
    profiles.pop(name)
    save_profiles(profiles, path)
    console.print(f"[green]Removed[/green] {name}")


@app.command("check")
def check_profile(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    timeout: Annotated[float, typer.Option("--timeout", help="SSH timeout in seconds.")] = 10.0,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profile = _profile(name, state)
    facts = run_diagnostics(profile, timeout=timeout)
    if json_output:
        console.print_json(json.dumps(facts))
        return
    _print_diagnostics(facts)
    if not facts["ok"]:
        raise typer.Exit(1)


@app.command("doctor")
def doctor_profile(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    timeout: Annotated[float, typer.Option("--timeout", help="SSH timeout in seconds.")] = 10.0,
    repair: Annotated[bool, typer.Option("--repair", help="Repair common setup issues, then re-run diagnostics.")] = False,
    alias: Annotated[str | None, typer.Option("--alias", help="SSH Host alias to use when repairing SSH config.")] = None,
    ssh_config: Annotated[Path, typer.Option("--ssh-config", help="SSH config path to update during repair.")] = Path("~/.ssh/config"),
    local_interval: Annotated[int, typer.Option("--local-interval", help="Seconds between local SSH prewarm checks during repair.")] = 60,
    remote_interval: Annotated[int, typer.Option("--remote-interval", help="Seconds between remote daemon warmups during repair.")] = 300,
    warm_session_count: Annotated[int, typer.Option("--warm-session-count", help="Recent session files to touch during repair.")] = 25,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profile = _profile(name, state)
    payload = _doctor_payload(profile, timeout=timeout)
    repair_payload: dict | None = None
    if repair and payload["issues"]:
        repair_payload = _repair_profile(
            profile,
            issues=payload["issues"],
            alias=alias,
            ssh_config=ssh_config,
            local_interval=local_interval,
            remote_interval=remote_interval,
            warm_session_count=warm_session_count,
            timeout=timeout,
            state=state,
        )
        profile = _profile(name, state)
        payload = _doctor_payload(profile, timeout=timeout)
    if repair_payload:
        payload["repair"] = repair_payload
    if json_output:
        console.print_json(json.dumps(payload))
    else:
        _print_doctor(payload)
    if payload["issues"]:
        raise typer.Exit(1)


@app.command("official-bootstrap")
def official_bootstrap(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    alias: Annotated[str | None, typer.Option("--alias", help="SSH Host alias shown to Codex App.")] = None,
    ssh_config: Annotated[Path, typer.Option("--ssh-config", help="SSH config path to update.")] = Path("~/.ssh/config"),
    timeout: Annotated[float, typer.Option("--timeout", help="SSH diagnostic timeout in seconds.")] = 10.0,
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open Codex Connections.")] = False,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profile = _profile(name, state)
    result = install_official_ssh_config(profile, alias=alias, path=ssh_config)
    profile = _save_alias(profile, result.alias, state)
    facts = run_diagnostics(profile, timeout=timeout)
    payload = {
        "alias": result.alias,
        "ssh_config": str(result.path),
        "changed": result.changed,
        "diagnostics": facts,
        "connections_url": "codex://settings/connections",
    }
    if json_output:
        console.print_json(json.dumps(payload))
    else:
        _print_bootstrap(result.alias, result.path, result.changed, facts)
    if not no_open:
        subprocess.run(["open", "codex://settings/connections"], check=False)
    if not facts["ok"]:
        raise typer.Exit(1)


@app.command("bootstrap-daemon")
def bootstrap_daemon(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    timeout: Annotated[float, typer.Option("--timeout", help="SSH timeout in seconds.")] = 30.0,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profile = _profile(name, state)
    result = bootstrap_remote_daemon(profile, timeout=timeout)
    if json_output:
        console.print_json(json.dumps(_command_payload(result)))
    else:
        _print_command_result("Remote Codex Daemon", result, "green" if result.ok else "red")
    if not result.ok:
        raise typer.Exit(result.exit_code)


@app.command("prewarm")
def prewarm(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    timeout: Annotated[float, typer.Option("--timeout", help="SSH timeout in seconds.")] = 10.0,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only print the SSH prewarm command.")] = False,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profile = _profile(name, state)
    command = build_ssh_prewarm_command(profile)
    if dry_run:
        if json_output:
            console.print_json(json.dumps({"command": command}))
        else:
            console.print(" ".join(shlex.quote(part) for part in command))
        return
    result = prewarm_ssh_master(profile, timeout=timeout)
    if json_output:
        console.print_json(json.dumps(_command_payload(result)))
    else:
        _print_command_result("SSH Master Prewarm", result, "green" if result.ok else "yellow")
    if not result.ok:
        raise typer.Exit(result.exit_code)


@app.command("benchmark")
def benchmark_profile(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    samples: Annotated[int, typer.Option("--samples", min=1, help="Number of warm samples per benchmark.")] = 5,
    timeout: Annotated[float, typer.Option("--timeout", help="Per-command timeout in seconds.")] = 10.0,
    include_cold: Annotated[bool, typer.Option("--include-cold", help="Close the SSH master once to measure cold attach.")] = False,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profile = _profile(name, state)
    payload = benchmark_remote_profile(
        profile,
        samples=samples,
        timeout=timeout,
        include_cold=include_cold,
    )
    if json_output:
        console.print_json(json.dumps(payload))
    else:
        _print_benchmark(payload)
    if not payload["ok"]:
        raise typer.Exit(1)


@app.command("optimize-app")
def optimize_app(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    alias: Annotated[str | None, typer.Option("--alias", help="SSH Host alias shown to Codex App.")] = None,
    ssh_config: Annotated[Path, typer.Option("--ssh-config", help="SSH config path to update.")] = Path("~/.ssh/config"),
    local_interval: Annotated[int, typer.Option("--local-interval", help="Seconds between local SSH prewarm checks.")] = 60,
    remote_interval: Annotated[int, typer.Option("--remote-interval", help="Seconds between remote daemon warmups.")] = 300,
    warm_session_count: Annotated[int, typer.Option("--warm-session-count", help="Recent session files to touch during warmup.")] = 25,
    timeout: Annotated[float, typer.Option("--timeout", help="SSH timeout in seconds.")] = 30.0,
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open Codex Connections.")] = False,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profile = _profile(name, state)
    result = install_official_ssh_config(profile, alias=alias, path=ssh_config)
    profile = _save_alias(profile, result.alias, state)

    daemon_result = bootstrap_remote_daemon(profile, timeout=timeout)
    remote_agent_result = install_remote_daemon_launch_agent(
        profile,
        interval_seconds=remote_interval,
        warm_session_count=warm_session_count,
        timeout=timeout,
    )
    local_agent_result = install_local_prewarm_launch_agent(profile, interval_seconds=local_interval)
    prewarm_result = prewarm_ssh_master(profile, timeout=min(timeout, 10.0))
    facts = run_diagnostics(profile, timeout=min(timeout, 10.0))

    payload = {
        "alias": result.alias,
        "ssh_config": str(result.path),
        "ssh_config_changed": result.changed,
        "daemon": _command_payload(daemon_result),
        "remote_launch_agent": _launch_agent_payload(remote_agent_result),
        "local_launch_agent": _launch_agent_payload(local_agent_result),
        "prewarm": _command_payload(prewarm_result),
        "diagnostics": facts,
    }
    if json_output:
        console.print_json(json.dumps(payload))
    else:
        _print_optimize(result.alias, result.path, daemon_result, remote_agent_result, local_agent_result, prewarm_result, facts)
    if not no_open:
        subprocess.run(["open", "codex://settings/connections"], check=False)
    if any(not item.ok for item in (daemon_result, remote_agent_result, local_agent_result, prewarm_result)) or not facts["ok"]:
        raise typer.Exit(1)


@app.command("upgrade-remote")
def upgrade_remote(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    allow_auto_update: Annotated[bool, typer.Option("--allow-auto-update", help="Allow Homebrew auto-update.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only print the remote upgrade command.")] = False,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
) -> None:
    profile = _profile(name, state)
    command = build_remote_upgrade_command(profile, no_auto_update=not allow_auto_update)
    rendered = " ".join(shlex.quote(part) for part in command)
    if dry_run:
        console.print(rendered)
        return
    console.print(Panel(rendered, title="Remote Codex Upgrade", title_align="left", border_style="yellow"))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


@app.command("install-remote-codex")
def install_remote_codex(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    allow_auto_update: Annotated[bool, typer.Option("--allow-auto-update", help="Allow Homebrew auto-update.")] = False,
    force_reinstall: Annotated[bool, typer.Option("--force-reinstall", help="Run brew reinstall when Codex already exists.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only print the remote install command.")] = False,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
) -> None:
    profile = _profile(name, state)
    command = build_remote_install_command(
        profile,
        no_auto_update=not allow_auto_update,
        force_reinstall=force_reinstall,
    )
    rendered = " ".join(shlex.quote(part) for part in command)
    if dry_run:
        console.print(rendered)
        return
    console.print(Panel(rendered, title="Remote Codex Install", title_align="left", border_style="yellow"))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


@app.command("uninstall")
def uninstall_profile(
    name: Annotated[str, typer.Argument(help="Profile name.")],
    alias: Annotated[str | None, typer.Option("--alias", help="SSH Host alias to remove. Defaults to saved alias.")] = None,
    ssh_config: Annotated[Path, typer.Option("--ssh-config", help="SSH config path to update.")] = Path("~/.ssh/config"),
    keep_profile: Annotated[bool, typer.Option("--keep-profile", help="Do not remove the saved profile.")] = False,
    keep_files: Annotated[bool, typer.Option("--keep-files", help="Unload agents but keep scripts and logs.")] = False,
    timeout: Annotated[float, typer.Option("--timeout", help="SSH timeout in seconds.")] = 20.0,
    state: Annotated[Path | None, typer.Option("--state", help="Profile store path.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    profile = _profile(name, state)
    ssh_result = uninstall_official_ssh_config(profile, alias=alias, path=ssh_config)
    local_result = uninstall_local_prewarm_launch_agent(profile, remove_files=not keep_files)
    remote_result = uninstall_remote_daemon_launch_agent(
        profile,
        remove_files=not keep_files,
        timeout=timeout,
    )
    removed_profile = False
    if not keep_profile:
        path = _state_path(state)
        profiles = load_profiles(path)
        removed_profile = profiles.pop(name, None) is not None
        save_profiles(profiles, path)
    payload = {
        "ssh_config": {
            "alias": ssh_result.alias,
            "path": str(ssh_result.path),
            "changed": ssh_result.changed,
        },
        "local_launch_agent": _cleanup_payload(local_result),
        "remote_launch_agent": _cleanup_payload(remote_result),
        "profile_removed": removed_profile,
    }
    if json_output:
        console.print_json(json.dumps(payload))
    else:
        _print_uninstall(payload)
    if local_result.exit_code != 0 or remote_result.exit_code != 0:
        raise typer.Exit(1)


def _state_path(path: Path | None) -> Path:
    return path.expanduser() if path else default_state_path_from_env()


def _profile(name: str, state: Path | None) -> RemoteCodexProfile:
    try:
        return get_profile(name, _state_path(state))
    except KeyError as exc:
        _error(exc.args[0])
        raise typer.Exit(1) from exc


def _save_alias(profile: RemoteCodexProfile, alias: str, state: Path | None) -> RemoteCodexProfile:
    if profile.ssh_alias == alias:
        return profile
    updated = RemoteCodexProfile(**{**profile.__dict__, "ssh_alias": alias})
    upsert_profile(updated, _state_path(state))
    return updated


def _error(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")


def _doctor_payload(profile: RemoteCodexProfile, *, timeout: float) -> dict:
    facts = run_diagnostics(profile, timeout=timeout)
    local_agent = check_local_prewarm_launch_agent(profile)
    if facts.get("ok"):
        remote_agent = check_remote_daemon_launch_agent(profile, timeout=timeout)
        prewarm_result = prewarm_ssh_master(profile, timeout=min(timeout, 10.0))
    else:
        remote_agent = _skipped_remote_agent_status(profile)
        prewarm_result = _skipped_command_result(profile)
    issues = _doctor_issues(facts, local_agent, remote_agent, prewarm_result)
    return {
        "profile": profile.__dict__,
        "diagnostics": facts,
        "local_launch_agent": _launch_agent_status_payload(local_agent),
        "remote_launch_agent": _launch_agent_status_payload(remote_agent),
        "prewarm": _command_payload(prewarm_result),
        "issues": issues,
        "ok": not issues,
    }


def _repair_profile(
    profile: RemoteCodexProfile,
    *,
    issues: list[dict[str, str]],
    alias: str | None,
    ssh_config: Path,
    local_interval: int,
    remote_interval: int,
    warm_session_count: int,
    timeout: float,
    state: Path | None,
) -> dict:
    issue_names = {issue["check"] for issue in issues}
    repairs: dict[str, dict] = {}
    if "ssh" not in issue_names:
        if any(name in issue_names for name in ("codex", "app-server", "daemon", "remote-control")):
            command = build_remote_install_command(profile)
            result = subprocess.run(command, check=False, text=True, capture_output=True)
            repairs["install_remote_codex"] = {
                "ok": result.returncode == 0,
                "exit_code": result.returncode,
                "command": command,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        if "daemon" in issue_names or "remote-control" in issue_names or "remote-launch-agent" in issue_names:
            daemon_result = bootstrap_remote_daemon(profile, timeout=timeout)
            repairs["bootstrap_daemon"] = _command_payload(daemon_result)
            remote_agent_result = install_remote_daemon_launch_agent(
                profile,
                interval_seconds=remote_interval,
                warm_session_count=warm_session_count,
                timeout=timeout,
            )
            repairs["remote_launch_agent"] = _launch_agent_payload(remote_agent_result)
    ssh_result = install_official_ssh_config(profile, alias=alias, path=ssh_config)
    profile = _save_alias(profile, ssh_result.alias, state)
    repairs["ssh_config"] = {
        "alias": ssh_result.alias,
        "path": str(ssh_result.path),
        "changed": ssh_result.changed,
    }
    if "local-launch-agent" in issue_names or "ssh-master" in issue_names:
        local_agent_result = install_local_prewarm_launch_agent(profile, interval_seconds=local_interval)
        repairs["local_launch_agent"] = _launch_agent_payload(local_agent_result)
    if "ssh" not in issue_names:
        prewarm_result = prewarm_ssh_master(profile, timeout=min(timeout, 10.0))
        repairs["prewarm"] = _command_payload(prewarm_result)
    return repairs


def _skipped_remote_agent_status(profile: RemoteCodexProfile) -> LaunchAgentStatusResult:
    label = f"com.conpera.codex-remote-ssh.{_safe_label_name(profile.name)}.daemon"
    return LaunchAgentStatusResult(
        command=[],
        exit_code=1,
        stdout="",
        stderr="skipped because SSH diagnostics failed",
        label=label,
        loaded=False,
        plist_path=f"$HOME/Library/LaunchAgents/{label}.plist",
        script_path=f"$HOME/.codex-remote-ssh-kit/bin/{label}.sh",
    )


def _skipped_command_result(profile: RemoteCodexProfile) -> RemoteCommandResult:
    return RemoteCommandResult(
        command=profile.ssh_base_args(prefer_alias=True),
        exit_code=1,
        stdout="",
        stderr="skipped because SSH diagnostics failed",
    )


def _safe_label_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in name)


def _print_profile_table(profiles: list[RemoteCodexProfile], *, title: str) -> None:
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Name")
    table.add_column("Target")
    table.add_column("SSH Alias")
    table.add_column("SSH Port")
    table.add_column("Workspace")
    for profile in profiles:
        table.add_row(
            profile.name,
            profile.target,
            profile.ssh_alias or "-",
            str(profile.port or "-"),
            profile.remote_workspace,
        )
    console.print(Panel(table, title=title, title_align="left", border_style="blue"))


def _print_diagnostics(facts: dict) -> None:
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 3))
    table.add_column(style="bold")
    table.add_column()
    for key in (
        "ok",
        "latency_ms",
        "hostname",
        "user",
        "codex_path",
        "codex_version",
        "app_server",
        "app_server_proxy",
        "app_server_daemon",
        "remote_control",
        "session_index_lines",
        "sessions_size",
        "mcp_size",
        "skills_size",
        "stderr",
    ):
        value = facts.get(key)
        if value not in (None, ""):
            table.add_row(key, str(value))
    console.print(Panel(table, title="Remote Codex Diagnostics", title_align="left", border_style="cyan"))


def _doctor_issues(facts: dict, local_agent, remote_agent, prewarm_result) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not facts.get("ok"):
        issues.append(
            {
                "check": "ssh",
                "message": facts.get("stderr") or "SSH diagnostics failed.",
                "fix": "Verify the target, identity file, VPN, and SSH config.",
            }
        )
        return issues
    if not facts.get("codex_path"):
        issues.append(
            {
                "check": "codex",
                "message": "Codex CLI was not found on the remote host.",
                "fix": "Run `codex-remote-ssh install-remote-codex <profile>` or install Codex manually.",
            }
        )
    if facts.get("app_server") != "ok":
        issues.append(
            {
                "check": "app-server",
                "message": "Remote Codex CLI does not expose app-server.",
                "fix": "Upgrade remote Codex with `codex-remote-ssh install-remote-codex <profile>`.",
            }
        )
    if facts.get("app_server_daemon") != "yes":
        issues.append(
            {
                "check": "daemon",
                "message": "Remote Codex app-server daemon is missing.",
                "fix": "Upgrade remote Codex, then run `codex-remote-ssh optimize-app <profile>`.",
            }
        )
    if facts.get("remote_control") != "ok":
        issues.append(
            {
                "check": "remote-control",
                "message": "Remote Codex CLI does not expose remote-control.",
                "fix": "Upgrade remote Codex and confirm the desktop app supports Remote SSH.",
            }
        )
    if not local_agent.loaded:
        issues.append(
            {
                "check": "local-launch-agent",
                "message": "Local SSH prewarm LaunchAgent is not loaded.",
                "fix": "Run `codex-remote-ssh optimize-app <profile>`.",
            }
        )
    if not remote_agent.loaded:
        issues.append(
            {
                "check": "remote-launch-agent",
                "message": "Remote Codex daemon LaunchAgent is not loaded.",
                "fix": "Run `codex-remote-ssh optimize-app <profile>`.",
            }
        )
    if not prewarm_result.ok:
        issues.append(
            {
                "check": "ssh-master",
                "message": prewarm_result.stderr or "SSH master prewarm failed.",
                "fix": "Run `codex-remote-ssh prewarm <profile>` and inspect SSH errors.",
            }
        )
    return issues


def _print_doctor(payload: dict) -> None:
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 3))
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    facts = payload["diagnostics"]
    table.add_row("Overall", "OK" if payload["ok"] else "Needs attention")
    table.add_row("Latency", f"{facts.get('latency_ms', '?')} ms")
    table.add_row("Codex", str(facts.get("codex_version") or "missing"))
    table.add_row("App server", str(facts.get("app_server") or "missing"))
    table.add_row("Daemon capability", str(facts.get("app_server_daemon") or "missing"))
    table.add_row("Remote control", str(facts.get("remote_control") or "missing"))
    table.add_row("Local LaunchAgent", "loaded" if payload["local_launch_agent"]["loaded"] else "missing")
    table.add_row("Remote LaunchAgent", "loaded" if payload["remote_launch_agent"]["loaded"] else "missing")
    table.add_row("SSH master", "warm" if payload["prewarm"]["ok"] else "failed")
    if payload.get("repair"):
        table.add_row("Repair", ", ".join(payload["repair"].keys()) or "-")
    if payload["issues"]:
        for issue in payload["issues"]:
            table.add_row(f"Issue: {issue['check']}", f"{issue['message']} Fix: {issue['fix']}")
    console.print(Panel(table, title="Remote Codex Doctor", title_align="left", border_style="green" if payload["ok"] else "yellow"))


def _print_bootstrap(alias: str, path: Path, changed: bool, facts: dict) -> None:
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 3))
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    table.add_row("SSH Host", alias)
    table.add_row("SSH config", str(path))
    table.add_row("Config changed", str(changed))
    table.add_row("Remote", f"{facts.get('user', '?')}@{facts.get('hostname', '?')}")
    table.add_row("Latency", f"{facts.get('latency_ms', '?')} ms")
    table.add_row("Codex", str(facts.get("codex_version") or "missing"))
    table.add_row("Remote control", str(facts.get("remote_control") or "missing"))
    table.add_row("Sessions", str(facts.get("session_index_lines") or "0"))
    console.print(Panel(table, title="Codex App Remote SSH", title_align="left", border_style="green"))


def _print_command_result(title: str, result, border_style: str) -> None:
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 3))
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    table.add_row("OK", str(result.ok))
    table.add_row("Exit code", str(result.exit_code))
    table.add_row("Command", " ".join(shlex.quote(part) for part in result.command))
    if result.stdout:
        table.add_row("stdout", result.stdout)
    if result.stderr:
        table.add_row("stderr", result.stderr)
    console.print(Panel(table, title=title, title_align="left", border_style=border_style))


def _print_optimize(alias: str, ssh_config: Path, daemon_result, remote_agent_result, local_agent_result, prewarm_result, facts: dict) -> None:
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 3))
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    table.add_row("SSH Host", alias)
    table.add_row("SSH config", str(ssh_config))
    table.add_row("Remote daemon", "running" if daemon_result.ok else f"failed ({daemon_result.exit_code})")
    table.add_row("Remote LaunchAgent", remote_agent_result.label if remote_agent_result.ok else f"failed ({remote_agent_result.exit_code})")
    table.add_row("Remote warmup", remote_agent_result.script_path)
    table.add_row("Local LaunchAgent", local_agent_result.label if local_agent_result.ok else f"failed ({local_agent_result.exit_code})")
    table.add_row("Local prewarm", local_agent_result.script_path)
    table.add_row("SSH master", "warm" if prewarm_result.ok else f"failed ({prewarm_result.exit_code})")
    table.add_row("Latency", f"{facts.get('latency_ms', '?')} ms")
    table.add_row("Codex", str(facts.get("codex_version") or "missing"))
    table.add_row("Sessions", str(facts.get("session_index_lines") or "0"))
    console.print(Panel(table, title="Codex App Remote Optimization", title_align="left", border_style="green"))


def _print_benchmark(payload: dict) -> None:
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Benchmark")
    table.add_column("OK")
    table.add_column("Samples")
    table.add_column("Min")
    table.add_column("P50")
    table.add_column("P95")
    table.add_column("Max")
    for name, item in payload["benchmarks"].items():
        summary = item["summary"]
        table.add_row(
            name,
            str(item["ok"]),
            str(len(item["latencies_ms"])),
            _ms(summary["min"]),
            _ms(summary["p50"]),
            _ms(summary["p95"]),
            _ms(summary["max"]),
        )
    console.print(Panel(table, title="Remote Codex Benchmark", title_align="left", border_style="green" if payload["ok"] else "yellow"))


def _print_uninstall(payload: dict) -> None:
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 3))
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    table.add_row("SSH config changed", str(payload["ssh_config"]["changed"]))
    table.add_row("SSH alias", payload["ssh_config"]["alias"])
    table.add_row("Local removed", ", ".join(payload["local_launch_agent"]["removed_paths"]) or "-")
    table.add_row("Remote removed", ", ".join(payload["remote_launch_agent"]["removed_paths"]) or "-")
    table.add_row("Profile removed", str(payload["profile_removed"]))
    console.print(Panel(table, title="Remote Codex Uninstall", title_align="left", border_style="yellow"))


def _command_payload(result) -> dict:
    return {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "command": result.command,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _launch_agent_payload(result) -> dict:
    payload = _command_payload(result)
    payload.update(
        {
            "label": result.label,
            "plist_path": result.plist_path,
            "script_path": result.script_path,
        }
    )
    return payload


def _launch_agent_status_payload(result) -> dict:
    payload = _command_payload(result)
    payload.update(
        {
            "label": result.label,
            "loaded": result.loaded,
            "plist_path": result.plist_path,
            "script_path": result.script_path,
        }
    )
    return payload


def _cleanup_payload(result) -> dict:
    payload = _command_payload(result)
    payload["removed_paths"] = result.removed_paths
    return payload


def _ms(value: int | None) -> str:
    return "-" if value is None else f"{value} ms"
