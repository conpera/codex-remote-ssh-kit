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
    RemoteCodexProfile,
    bootstrap_remote_daemon,
    build_remote_upgrade_command,
    build_ssh_prewarm_command,
    default_state_path_from_env,
    get_profile,
    install_local_prewarm_launch_agent,
    install_official_ssh_config,
    install_remote_daemon_launch_agent,
    load_profiles,
    prewarm_ssh_master,
    run_diagnostics,
    save_profiles,
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
