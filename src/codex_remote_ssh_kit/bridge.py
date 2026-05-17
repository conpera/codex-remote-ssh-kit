"""SSH helpers for making Codex App remote hosts persistent and low-latency."""
from __future__ import annotations

import json
import os
import plistlib
import secrets
import shlex
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_STATE_PATH = Path("~/.codex-remote-ssh-kit/hosts.json").expanduser()
DEFAULT_REMOTE_PORT = 39287
DEFAULT_SSH_CONFIG_PATH = Path("~/.ssh/config").expanduser()
DEFAULT_CONTROL_PATH = "~/.ssh/control-%C"
DEFAULT_CONTROL_PERSIST = "30m"
DEFAULT_PREWARM_INTERVAL_SECONDS = 60
DEFAULT_REMOTE_DAEMON_INTERVAL_SECONDS = 300
OFFICIAL_SSH_BEGIN = "# >>> codex-remote-ssh official hosts >>>"
OFFICIAL_SSH_END = "# <<< codex-remote-ssh official hosts <<<"


@dataclass(frozen=True)
class RemoteCodexProfile:
    """Saved connection profile for one remote Codex host."""

    name: str
    target: str
    port: int | None = None
    identity_file: str | None = None
    remote_port: int = DEFAULT_REMOTE_PORT
    local_port: int | None = None
    remote_workspace: str = "~"
    extra_ssh_args: list[str] = field(default_factory=list)
    ssh_alias: str | None = None

    def ssh_target(self) -> str:
        if self.port is None:
            return self.target
        return f"{self.target} -p {self.port}"

    def ssh_base_args(self, *, prefer_alias: bool = False) -> list[str]:
        args = ["ssh"]
        use_alias = prefer_alias and self.ssh_alias
        if self.identity_file and not use_alias:
            args.extend(["-i", self.identity_file])
        if self.port is not None and not use_alias:
            args.extend(["-p", str(self.port)])
        if not use_alias:
            args.extend(self.extra_ssh_args)
        args.append(self.ssh_alias if use_alias else self.target)
        return args


@dataclass(frozen=True)
class BridgePlan:
    """Commands and connection details needed to run a bridge."""

    profile: RemoteCodexProfile
    local_port: int
    token_file: str
    token_preview: str
    remote_start_command: str
    tunnel_command: list[str]
    websocket_url: str


@dataclass(frozen=True)
class OfficialSshConfigResult:
    """Result of installing an SSH host entry for official Codex Connections."""

    alias: str
    path: Path
    changed: bool
    block: str


@dataclass(frozen=True)
class RemoteCommandResult:
    """Result from a remote setup command."""

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class LaunchAgentInstallResult(RemoteCommandResult):
    """Result from installing a launchd LaunchAgent."""

    label: str
    plist_path: str
    script_path: str


@dataclass(frozen=True)
class LaunchAgentStatusResult(RemoteCommandResult):
    """Status for a launchd LaunchAgent."""

    label: str
    loaded: bool
    plist_path: str
    script_path: str


@dataclass(frozen=True)
class CleanupResult(RemoteCommandResult):
    """Result from cleaning up local or remote managed files."""

    removed_paths: list[str]


def load_profiles(path: Path = DEFAULT_STATE_PATH) -> dict[str, RemoteCodexProfile]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    profiles: dict[str, RemoteCodexProfile] = {}
    for name, raw in data.get("profiles", {}).items():
        raw = dict(raw)
        raw.setdefault("name", name)
        profiles[name] = RemoteCodexProfile(**raw)
    return profiles


def save_profiles(profiles: dict[str, RemoteCodexProfile], path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "profiles": {
            name: _profile_to_json(profile)
            for name, profile in sorted(profiles.items())
        }
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)


def upsert_profile(profile: RemoteCodexProfile, path: Path = DEFAULT_STATE_PATH) -> None:
    profiles = load_profiles(path)
    profiles[profile.name] = profile
    save_profiles(profiles, path)


def get_profile(name: str, path: Path = DEFAULT_STATE_PATH) -> RemoteCodexProfile:
    profiles = load_profiles(path)
    try:
        return profiles[name]
    except KeyError as exc:
        available = ", ".join(sorted(profiles)) or "(none)"
        raise KeyError(f"Profile not found: {name}. Available: {available}") from exc


def build_bridge_plan(
    profile: RemoteCodexProfile,
    *,
    token: str | None = None,
    local_port: int | None = None,
    remote_port: int | None = None,
) -> BridgePlan:
    """Build the shell commands that start a remote app-server and local tunnel."""

    resolved_remote_port = remote_port or profile.remote_port
    resolved_local_port = local_port or profile.local_port or find_free_port()
    capability_token = token or secrets.token_urlsafe(32)
    token_file = f"/tmp/codex-remote-ssh-{_safe_name(profile.name)}.token"
    quoted_token_file = shlex.quote(token_file)
    quoted_token = shlex.quote(capability_token)
    quoted_port = shlex.quote(str(resolved_remote_port))
    workspace_expr = _remote_path_expr(profile.remote_workspace)

    remote_start_command = (
        "set -e; "
        "command -v codex >/dev/null 2>&1 || "
        "(echo 'codex CLI not found on remote host' >&2; exit 127); "
        f"mkdir -p {workspace_expr}; "
        f"printf %s {quoted_token} > {quoted_token_file}; "
        f"chmod 600 {quoted_token_file}; "
        "auth_flags=''; "
        "if codex app-server --help 2>&1 | grep -q -- '--ws-auth'; then "
        f"auth_flags='--ws-auth capability-token --ws-token-file {quoted_token_file}'; "
        "fi; "
        "if command -v lsof >/dev/null 2>&1 && "
        f"lsof -iTCP:{quoted_port} -sTCP:LISTEN >/dev/null 2>&1; then "
        f"echo app-server-already-listening:{resolved_remote_port}; "
        "else "
        f"nohup codex app-server --listen ws://127.0.0.1:{resolved_remote_port} "
        "$auth_flags "
        f"> /tmp/codex-remote-ssh-{_safe_name(profile.name)}.log 2>&1 & "
        "echo app-server-started:$!; "
        "fi"
    )

    tunnel_command = profile.ssh_base_args()
    tunnel_command[1:1] = [
        "-N",
        "-L",
        f"127.0.0.1:{resolved_local_port}:127.0.0.1:{resolved_remote_port}",
        "-o",
        "ExitOnForwardFailure=yes",
    ]

    return BridgePlan(
        profile=profile,
        local_port=resolved_local_port,
        token_file=token_file,
        token_preview=_preview_token(capability_token),
        remote_start_command=remote_start_command,
        tunnel_command=tunnel_command,
        websocket_url=f"ws://127.0.0.1:{resolved_local_port}",
    )


def start_remote_app_server(
    profile: RemoteCodexProfile,
    *,
    token: str | None = None,
    local_port: int | None = None,
    remote_port: int | None = None,
    run: bool = True,
) -> BridgePlan:
    """Start remote app-server and open a local SSH tunnel.

    When `run=False`, this returns the plan without side effects. Tests use this
    mode, and CLI `--dry-run` exposes it for diagnosis.
    """

    plan = build_bridge_plan(
        profile,
        token=token,
        local_port=local_port,
        remote_port=remote_port,
    )
    if not run:
        return plan

    subprocess.run(
        profile.ssh_base_args() + [plan.remote_start_command],
        check=True,
    )
    subprocess.Popen(  # noqa: S603 - SSH command is intentionally user configured.
        plan.tunnel_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return plan


def run_diagnostics(profile: RemoteCodexProfile, *, timeout: float = 10.0) -> dict[str, Any]:
    """Collect connection facts from a remote Codex host."""

    started = time.perf_counter()
    command = (
        "set -e; "
        "printf 'hostname='; hostname; "
        "printf 'user='; whoami; "
        "printf 'codex_path='; command -v codex || true; "
        "printf 'codex_version='; codex --version 2>/dev/null || true; "
        "printf 'session_index_lines='; "
        "test -f ~/.codex/session_index.jsonl && wc -l < ~/.codex/session_index.jsonl || echo 0; "
        "printf 'sessions_size='; du -sh ~/.codex/sessions 2>/dev/null | awk '{print $1}' || echo 0; "
        "printf 'mcp_size='; du -sh ~/.codex/mcp 2>/dev/null | awk '{print $1}' || echo 0; "
        "printf 'skills_size='; du -sh ~/.codex/skills 2>/dev/null | awk '{print $1}' || echo 0; "
        "printf 'app_server='; codex app-server --help >/dev/null 2>&1 && echo ok || echo missing; "
        "printf 'app_server_ws_auth='; codex app-server --help 2>&1 | grep -q -- '--ws-auth' && echo yes || echo no; "
        "printf 'app_server_proxy='; codex app-server --help 2>&1 | grep -q '^  proxy\\b' && echo yes || echo no; "
        "printf 'app_server_daemon='; codex app-server --help 2>&1 | grep -q '^  daemon\\b' && echo yes || echo no; "
        "printf 'remote_control='; codex --help 2>&1 | grep -q '^  remote-control\\b' && echo ok || echo missing"
    )
    result = subprocess.run(
        profile.ssh_base_args(prefer_alias=True) + [command],
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    facts: dict[str, Any] = {
        "profile": _profile_to_json(profile),
        "ok": result.returncode == 0,
        "latency_ms": elapsed_ms,
        "exit_code": result.returncode,
        "stderr": result.stderr.strip(),
    }
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            facts[key] = value.strip()
    return facts


def benchmark_remote_profile(
    profile: RemoteCodexProfile,
    *,
    samples: int = 5,
    timeout: float = 10.0,
    include_cold: bool = False,
) -> dict[str, Any]:
    """Measure common remote attach paths for a Codex SSH host."""

    sample_count = max(samples, 1)
    payload: dict[str, Any] = {
        "profile": _profile_to_json(profile),
        "samples": sample_count,
        "include_cold": include_cold,
        "benchmarks": {},
    }
    if include_cold:
        close_result = close_ssh_master(profile, timeout=timeout)
        payload["close_master"] = _remote_command_to_json(close_result)
        payload["benchmarks"]["cold_ssh_true"] = benchmark_command(
            profile.ssh_base_args(prefer_alias=True) + ["true"],
            samples=1,
            timeout=timeout,
        )
    prewarm_result = prewarm_ssh_master(profile, timeout=timeout)
    payload["prewarm"] = _remote_command_to_json(prewarm_result)
    payload["benchmarks"]["warm_ssh_true"] = benchmark_command(
        profile.ssh_base_args(prefer_alias=True) + ["true"],
        samples=sample_count,
        timeout=timeout,
    )
    payload["benchmarks"]["daemon_version"] = benchmark_command(
        profile.ssh_base_args(prefer_alias=True) + ["codex app-server daemon version >/dev/null 2>&1"],
        samples=sample_count,
        timeout=timeout,
    )
    payload["benchmarks"]["session_index"] = benchmark_command(
        profile.ssh_base_args(prefer_alias=True)
        + ["test -f ~/.codex/session_index.jsonl && tail -n 20 ~/.codex/session_index.jsonl >/dev/null 2>&1 || true"],
        samples=sample_count,
        timeout=timeout,
    )
    payload["ok"] = all(item["ok"] for item in payload["benchmarks"].values())
    return payload


def benchmark_command(command: list[str], *, samples: int, timeout: float) -> dict[str, Any]:
    """Run a command repeatedly and summarize successful latencies."""

    latencies: list[int] = []
    failures: list[dict[str, Any]] = []
    for _ in range(max(samples, 1)):
        started = time.perf_counter()
        try:
            result = subprocess.run(
                command,
                check=False,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            failures.append(
                {
                    "exit_code": 124,
                    "latency_ms": elapsed_ms,
                    "stderr": f"timed out after {exc.timeout} seconds",
                }
            )
            continue
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if result.returncode == 0:
            latencies.append(elapsed_ms)
        else:
            failures.append(
                {
                    "exit_code": result.returncode,
                    "latency_ms": elapsed_ms,
                    "stderr": result.stderr.strip(),
                }
            )
    return {
        "command": command,
        "ok": not failures,
        "latencies_ms": latencies,
        "failures": failures,
        "summary": _latency_summary(latencies),
    }


def close_ssh_master(profile: RemoteCodexProfile, *, timeout: float = 10.0) -> RemoteCommandResult:
    """Close an existing SSH master connection for explicit cold-start tests."""

    command = profile.ssh_base_args(prefer_alias=True)
    command[1:1] = ["-O", "exit"]
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    stderr = result.stderr.strip()
    missing_master = any(
        marker in stderr
        for marker in ("No ControlPath", "No such file", "Control socket connect")
    )
    return RemoteCommandResult(
        command=command,
        exit_code=0 if result.returncode == 0 or (result.returncode == 255 and missing_master) else result.returncode,
        stdout=result.stdout.strip(),
        stderr=stderr,
    )


def bootstrap_remote_daemon(profile: RemoteCodexProfile, *, timeout: float = 30.0) -> RemoteCommandResult:
    """Install and start durable remote Codex daemon support on the host."""

    command = (
        "set -e; "
        "command -v codex >/dev/null 2>&1 || "
        "(echo 'codex CLI not found on remote host' >&2; exit 127); "
        "if codex app-server daemon version >/dev/null 2>&1; then "
        "codex app-server daemon start; "
        "else "
        "codex app-server daemon bootstrap --remote-control; "
        "codex app-server daemon start; "
        "fi; "
        "codex app-server daemon enable-remote-control; "
        "codex app-server daemon version"
    )
    ssh_command = profile.ssh_base_args(prefer_alias=True) + [command]
    try:
        result = subprocess.run(
            ssh_command,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return _timeout_result(ssh_command, exc)
    return RemoteCommandResult(
        command=ssh_command,
        exit_code=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
    )


def build_ssh_prewarm_command(profile: RemoteCodexProfile) -> list[str]:
    """Return the SSH command that opens a reusable master connection."""

    command = profile.ssh_base_args(prefer_alias=True)
    command[1:1] = [
        "-M",
        "-N",
        "-f",
        "-o",
        "ExitOnForwardFailure=yes",
    ]
    return command


def prewarm_ssh_master(profile: RemoteCodexProfile, *, timeout: float = 10.0) -> RemoteCommandResult:
    """Open a background SSH master connection for low-latency app attach."""

    check_command = profile.ssh_base_args(prefer_alias=True)
    check_command[1:1] = ["-O", "check"]
    check_result = subprocess.run(
        check_command,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check_result.returncode == 0:
        return RemoteCommandResult(
            command=check_command,
            exit_code=0,
            stdout=(check_result.stdout.strip() or "ssh master already running"),
            stderr=check_result.stderr.strip(),
        )

    command = build_ssh_prewarm_command(profile)
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return RemoteCommandResult(
        command=command,
        exit_code=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
    )


def check_local_prewarm_launch_agent(profile: RemoteCodexProfile) -> LaunchAgentStatusResult:
    """Return launchd status for the local SSH prewarm LaunchAgent."""

    label = f"com.conpera.codex-remote-ssh.{_safe_name(profile.name)}.prewarm"
    root = Path("~/.codex-remote-ssh-kit").expanduser()
    plist_path = Path("~/Library/LaunchAgents").expanduser() / f"{label}.plist"
    script_path = root / "bin" / f"{label}.sh"
    command = ["launchctl", "print", f"gui/{os.getuid()}/{label}"]
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
    )
    return LaunchAgentStatusResult(
        command=command,
        exit_code=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        label=label,
        loaded=result.returncode == 0,
        plist_path=str(plist_path),
        script_path=str(script_path),
    )


def check_remote_daemon_launch_agent(profile: RemoteCodexProfile, *, timeout: float = 10.0) -> LaunchAgentStatusResult:
    """Return launchd status for the remote Codex daemon LaunchAgent."""

    label = f"com.conpera.codex-remote-ssh.{_safe_name(profile.name)}.daemon"
    plist_path = f"$HOME/Library/LaunchAgents/{label}.plist"
    script_path = f"$HOME/.codex-remote-ssh-kit/bin/{label}.sh"
    remote_command = (
        "set +e; "
        f"launchctl print gui/$(id -u)/{label}; "
        "exit $?"
    )
    ssh_command = profile.ssh_base_args(prefer_alias=True) + [remote_command]
    try:
        result = subprocess.run(
            ssh_command,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_result = _timeout_result(ssh_command, exc)
        return LaunchAgentStatusResult(
            command=timeout_result.command,
            exit_code=timeout_result.exit_code,
            stdout=timeout_result.stdout,
            stderr=timeout_result.stderr,
            label=label,
            loaded=False,
            plist_path=plist_path,
            script_path=script_path,
        )
    return LaunchAgentStatusResult(
        command=ssh_command,
        exit_code=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        label=label,
        loaded=result.returncode == 0,
        plist_path=plist_path,
        script_path=script_path,
    )


def install_local_prewarm_launch_agent(
    profile: RemoteCodexProfile,
    *,
    interval_seconds: int = DEFAULT_PREWARM_INTERVAL_SECONDS,
) -> LaunchAgentInstallResult:
    """Install a local LaunchAgent that keeps the SSH master connection warm."""

    label = f"com.conpera.codex-remote-ssh.{_safe_name(profile.name)}.prewarm"
    root = Path("~/.codex-remote-ssh-kit").expanduser()
    scripts_dir = root / "bin"
    logs_dir = root / "logs"
    launch_agents_dir = Path("~/Library/LaunchAgents").expanduser()
    script_path = scripts_dir / f"{label}.sh"
    plist_path = launch_agents_dir / f"{label}.plist"

    scripts_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_local_prewarm_script(profile))
    script_path.chmod(0o700)

    _write_plist(
        plist_path,
        _launch_agent_payload(
            label=label,
            program_arguments=[str(script_path)],
            standard_out_path=str(logs_dir / f"{label}.out.log"),
            standard_error_path=str(logs_dir / f"{label}.err.log"),
            interval_seconds=interval_seconds,
        ),
    )

    command = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)]
    bootout = subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)],
        check=False,
        text=True,
        capture_output=True,
    )
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
    )
    stdout = "\n".join(part for part in (bootout.stdout.strip(), result.stdout.strip()) if part)
    stderr = "\n".join(part for part in (bootout.stderr.strip(), result.stderr.strip()) if part)
    return LaunchAgentInstallResult(
        command=command,
        exit_code=result.returncode,
        stdout=stdout,
        stderr=stderr,
        label=label,
        plist_path=str(plist_path),
        script_path=str(script_path),
    )


def uninstall_local_prewarm_launch_agent(profile: RemoteCodexProfile, *, remove_files: bool = True) -> CleanupResult:
    """Unload and optionally remove the local SSH prewarm LaunchAgent."""

    label = f"com.conpera.codex-remote-ssh.{_safe_name(profile.name)}.prewarm"
    root = Path("~/.codex-remote-ssh-kit").expanduser()
    plist_path = Path("~/Library/LaunchAgents").expanduser() / f"{label}.plist"
    script_path = root / "bin" / f"{label}.sh"
    log_paths = [
        root / "logs" / f"{label}.out.log",
        root / "logs" / f"{label}.err.log",
    ]
    command = ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)]
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
    )
    removed_paths: list[str] = []
    if remove_files:
        for path in [plist_path, script_path, *log_paths]:
            if path.exists():
                path.unlink()
                removed_paths.append(str(path))
    return CleanupResult(
        command=command,
        exit_code=0 if result.returncode in (0, 5) else result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        removed_paths=removed_paths,
    )


def install_remote_daemon_launch_agent(
    profile: RemoteCodexProfile,
    *,
    interval_seconds: int = DEFAULT_REMOTE_DAEMON_INTERVAL_SECONDS,
    warm_session_count: int = 25,
    timeout: float = 20.0,
) -> LaunchAgentInstallResult:
    """Install a remote LaunchAgent that keeps Codex daemon and sessions warm."""

    label = f"com.conpera.codex-remote-ssh.{_safe_name(profile.name)}.daemon"
    remote_root = "$HOME/.codex-remote-ssh-kit"
    script_path = f"{remote_root}/bin/{label}.sh"
    plist_path = f"$HOME/Library/LaunchAgents/{label}.plist"
    log_path = f"{remote_root}/logs/{label}.out.log"
    err_path = f"{remote_root}/logs/{label}.err.log"
    script = _remote_daemon_script(warm_session_count=warm_session_count)
    plist = plistlib.dumps(
        _launch_agent_payload(
            label=label,
            program_arguments=[script_path],
            standard_out_path=log_path,
            standard_error_path=err_path,
            interval_seconds=interval_seconds,
        ),
        sort_keys=True,
    ).decode("utf-8")
    command = (
        "set -e; "
        f"mkdir -p {remote_root}/bin {remote_root}/logs $HOME/Library/LaunchAgents; "
        f"cat > {script_path} <<'CODEX_REMOTE_SSH_SCRIPT'\n"
        f"{script}"
        "CODEX_REMOTE_SSH_SCRIPT\n"
        f"chmod 700 {script_path}; "
        f"cat > {plist_path} <<CODEX_REMOTE_SSH_PLIST\n"
        f"{plist}"
        "CODEX_REMOTE_SSH_PLIST\n"
        f"launchctl bootout gui/$(id -u) {plist_path} >/dev/null 2>&1 || true; "
        f"launchctl bootstrap gui/$(id -u) {plist_path}; "
        f"launchctl kickstart -k gui/$(id -u)/{label}; "
        f"echo label={label}; "
        f"echo plist={plist_path}; "
        f"echo script={script_path}"
    )
    ssh_command = profile.ssh_base_args(prefer_alias=True) + [command]
    result = subprocess.run(
        ssh_command,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return LaunchAgentInstallResult(
        command=ssh_command,
        exit_code=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        label=label,
        plist_path=plist_path,
        script_path=script_path,
    )


def uninstall_remote_daemon_launch_agent(
    profile: RemoteCodexProfile,
    *,
    remove_files: bool = True,
    timeout: float = 20.0,
) -> CleanupResult:
    """Unload and optionally remove the remote Codex daemon LaunchAgent."""

    label = f"com.conpera.codex-remote-ssh.{_safe_name(profile.name)}.daemon"
    files = [
        f"$HOME/Library/LaunchAgents/{label}.plist",
        f"$HOME/.codex-remote-ssh-kit/bin/{label}.sh",
        f"$HOME/.codex-remote-ssh-kit/logs/{label}.out.log",
        f"$HOME/.codex-remote-ssh-kit/logs/{label}.err.log",
    ]
    remove_command = ""
    if remove_files:
        quoted_files = " ".join(files)
        remove_command = f"rm -f {quoted_files}; "
    remote_command = (
        "set +e; "
        f"launchctl bootout gui/$(id -u) $HOME/Library/LaunchAgents/{label}.plist >/dev/null 2>&1; "
        f"{remove_command}"
        "exit 0"
    )
    ssh_command = profile.ssh_base_args(prefer_alias=True) + [remote_command]
    try:
        result = subprocess.run(
            ssh_command,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_result = _timeout_result(ssh_command, exc)
        return CleanupResult(
            command=timeout_result.command,
            exit_code=timeout_result.exit_code,
            stdout=timeout_result.stdout,
            stderr=timeout_result.stderr,
            removed_paths=[],
        )
    return CleanupResult(
        command=ssh_command,
        exit_code=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
        removed_paths=files if remove_files else [],
    )


def build_remote_install_command(
    profile: RemoteCodexProfile,
    *,
    no_auto_update: bool = True,
    force_reinstall: bool = False,
) -> list[str]:
    """Return the SSH command that installs or upgrades Codex on the remote host."""

    prefix = "HOMEBREW_NO_AUTO_UPDATE=1 " if no_auto_update else ""
    brew_action = "reinstall" if force_reinstall else "upgrade"
    command = (
        "set -e; "
        "if ! command -v brew >/dev/null 2>&1; then "
        "echo 'Homebrew not found on remote host' >&2; exit 127; "
        "fi; "
        "if ! command -v codex >/dev/null 2>&1; then "
        f"{prefix}brew install --cask codex; "
        "else "
        f"{prefix}brew {brew_action} --cask codex; "
        "fi; "
        "codex --version; "
        "codex --help | grep -E '^  (remote-control|app-server)\\b' || true; "
        "codex app-server --help | grep -E '^  (daemon|proxy)\\b|--ws-auth' || true"
    )
    return profile.ssh_base_args() + [command]


def build_remote_upgrade_command(profile: RemoteCodexProfile, *, no_auto_update: bool = True) -> list[str]:
    """Return the SSH command that upgrades Codex on the remote host via Homebrew."""

    return build_remote_install_command(profile, no_auto_update=no_auto_update)


def install_official_ssh_config(
    profile: RemoteCodexProfile,
    *,
    alias: str | None = None,
    path: Path = DEFAULT_SSH_CONFIG_PATH,
) -> OfficialSshConfigResult:
    """Install a managed SSH Host block for official Codex App Connections.

    The official Codex desktop app discovers remote machines from SSH config.
    This function writes only a small managed block, leaving user-owned SSH
    entries outside the markers untouched.
    """

    resolved_alias = alias or _safe_ssh_alias(profile.name)
    block = build_official_ssh_config_block(profile, alias=resolved_alias)
    path = path.expanduser()
    existing = path.read_text() if path.exists() else ""
    updated = _upsert_managed_host_block(existing, resolved_alias, block)
    changed = updated != existing
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated)
    return OfficialSshConfigResult(
        alias=resolved_alias,
        path=path,
        changed=changed,
        block=block,
    )


def uninstall_official_ssh_config(
    profile: RemoteCodexProfile,
    *,
    alias: str | None = None,
    path: Path = DEFAULT_SSH_CONFIG_PATH,
) -> OfficialSshConfigResult:
    """Remove one managed SSH Host block from the Codex Remote SSH section."""

    resolved_alias = alias or profile.ssh_alias or _safe_ssh_alias(profile.name)
    path = path.expanduser()
    existing = path.read_text() if path.exists() else ""
    updated = _remove_managed_host_block(existing, resolved_alias)
    changed = updated != existing
    if changed:
        path.write_text(updated)
    return OfficialSshConfigResult(
        alias=resolved_alias,
        path=path,
        changed=changed,
        block="",
    )


def build_official_ssh_config_block(profile: RemoteCodexProfile, *, alias: str | None = None) -> str:
    """Build the managed SSH config block used by official Codex Connections."""

    resolved_alias = alias or _safe_ssh_alias(profile.name)
    user, host = parse_ssh_target(profile.target)
    lines = [
        OFFICIAL_SSH_BEGIN,
        "# Managed by `codex-remote-ssh official-bootstrap`.",
        "# Codex App reads this host from ~/.ssh/config for Remote SSH.",
        f"Host {resolved_alias}",
        f"  HostName {host}",
    ]
    if user:
        lines.append(f"  User {user}")
    if profile.port is not None:
        lines.append(f"  Port {profile.port}")
    if profile.identity_file:
        lines.append(f"  IdentityFile {profile.identity_file}")
        lines.extend(
            [
                "  IdentitiesOnly yes",
                "  PreferredAuthentications publickey",
                "  PubkeyAuthentication yes",
                "  PasswordAuthentication no",
                "  KbdInteractiveAuthentication no",
            ]
        )
    lines.extend(
        [
            "  StrictHostKeyChecking accept-new",
            "  ControlMaster auto",
            f"  ControlPath {DEFAULT_CONTROL_PATH}",
            f"  ControlPersist {DEFAULT_CONTROL_PERSIST}",
            "  Compression no",
            "  IPQoS throughput",
            "  ConnectTimeout 5",
            "  ConnectionAttempts 1",
            "  ServerAliveInterval 30",
            "  ServerAliveCountMax 3",
            "  TCPKeepAlive yes",
        ]
    )
    lines.extend(_ssh_config_options_from_args(profile.extra_ssh_args))
    lines.append(OFFICIAL_SSH_END)
    return "\n".join(lines) + "\n"


def parse_ssh_target(target: str) -> tuple[str | None, str]:
    """Return (user, host) for a simple SSH target such as user@example.com."""

    if "://" in target:
        raise ValueError("SSH target must be host or user@host, not a URL")
    if "@" in target:
        user, host = target.rsplit("@", 1)
        return user or None, host
    return None, target


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _profile_to_json(profile: RemoteCodexProfile) -> dict[str, Any]:
    payload = asdict(profile)
    return {key: value for key, value in payload.items() if value not in (None, [], "")}


def _remote_command_to_json(result: RemoteCommandResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "exit_code": result.exit_code,
        "command": result.command,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _latency_summary(latencies: list[int]) -> dict[str, int | None]:
    if not latencies:
        return {"min": None, "p50": None, "p95": None, "max": None}
    sorted_latencies = sorted(latencies)
    return {
        "min": sorted_latencies[0],
        "p50": _percentile(sorted_latencies, 0.50),
        "p95": _percentile(sorted_latencies, 0.95),
        "max": sorted_latencies[-1],
    }


def _percentile(sorted_values: list[int], percentile: float) -> int:
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = round((len(sorted_values) - 1) * percentile)
    return sorted_values[index]


def _timeout_result(command: list[str], exc: subprocess.TimeoutExpired) -> RemoteCommandResult:
    stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    message = f"timed out after {exc.timeout} seconds"
    stderr = "\n".join(part for part in (stderr.strip(), message) if part)
    return RemoteCommandResult(
        command=command,
        exit_code=124,
        stdout=stdout.strip(),
        stderr=stderr,
    )


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in name)


def _safe_ssh_alias(name: str) -> str:
    alias = _safe_name(name.strip()).strip("-_").lower()
    return alias or "remote-codex"


def _upsert_managed_host_block(existing: str, alias: str, block: str) -> str:
    entries = _managed_host_blocks(existing)
    entries[alias] = _strip_managed_markers(block)
    return _replace_managed_section(existing, entries)


def _remove_managed_host_block(existing: str, alias: str) -> str:
    entries = _managed_host_blocks(existing)
    entries.pop(alias, None)
    return _replace_managed_section(existing, entries)


def _managed_host_blocks(existing: str) -> dict[str, str]:
    section = _managed_section(existing)
    entries: dict[str, str] = {}
    current_alias: str | None = None
    current_lines: list[str] = []
    for line in section.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            continue
        if line.startswith("Host "):
            if current_alias and current_lines:
                entries[current_alias] = "\n".join(current_lines) + "\n"
            current_alias = line.split(None, 1)[1].strip()
            current_lines = [line]
            continue
        if current_alias:
            current_lines.append(line)
    if current_alias and current_lines:
        entries[current_alias] = "\n".join(current_lines) + "\n"
    return entries


def _managed_section(existing: str) -> str:
    if OFFICIAL_SSH_BEGIN not in existing or OFFICIAL_SSH_END not in existing:
        return ""
    _, rest = existing.split(OFFICIAL_SSH_BEGIN, 1)
    section, _ = rest.split(OFFICIAL_SSH_END, 1)
    return section


def _replace_managed_section(existing: str, entries: dict[str, str]) -> str:
    section = _render_managed_section(entries)
    if OFFICIAL_SSH_BEGIN in existing and OFFICIAL_SSH_END in existing:
        before, rest = existing.split(OFFICIAL_SSH_BEGIN, 1)
        _, after = rest.split(OFFICIAL_SSH_END, 1)
        before = before.rstrip()
        after = after.lstrip("\n")
        if section:
            prefix = before + "\n\n" if before else ""
            suffix = "\n" + after if after else ""
            return prefix + section + suffix
        return (before + ("\n" + after if before and after else after)).rstrip() + ("\n" if before or after else "")
    if not section:
        return existing
    return existing.rstrip() + "\n\n" + section if existing else section


def _render_managed_section(entries: dict[str, str]) -> str:
    if not entries:
        return ""
    lines = [
        OFFICIAL_SSH_BEGIN,
        "# Managed by `codex-remote-ssh official-bootstrap`.",
        "# Codex App reads these hosts from ~/.ssh/config for Remote SSH.",
    ]
    for alias in sorted(entries):
        lines.append("")
        lines.append(entries[alias].strip())
    lines.append(OFFICIAL_SSH_END)
    return "\n".join(lines) + "\n"


def _strip_managed_markers(block: str) -> str:
    lines = [
        line
        for line in block.splitlines()
        if line not in (OFFICIAL_SSH_BEGIN, OFFICIAL_SSH_END)
        and not line.startswith("# Managed by ")
        and not line.startswith("# Codex App reads ")
    ]
    return "\n".join(line for line in lines if line.strip()) + "\n"


def _ssh_config_options_from_args(args: list[str]) -> list[str]:
    options: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        value: str | None = None
        if arg == "-o" and index + 1 < len(args):
            value = args[index + 1]
            index += 2
        elif arg.startswith("-o") and len(arg) > 2:
            value = arg[2:]
            index += 1
        else:
            index += 1
        if not value or "=" not in value:
            continue
        key, raw = value.split("=", 1)
        if key.replace("_", "").replace("-", "").isalnum() and raw:
            options.append(f"  {key} {raw}")
    return options


def _remote_path_expr(path: str) -> str:
    if path == "~":
        return "$HOME"
    if path.startswith("~/"):
        return "$HOME/" + shlex.quote(path[2:])
    return shlex.quote(path)


def _write_plist(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes(plistlib.dumps(payload, sort_keys=True))


def _launch_agent_payload(
    *,
    label: str,
    program_arguments: list[str],
    standard_out_path: str,
    standard_error_path: str,
    interval_seconds: int,
) -> dict[str, Any]:
    return {
        "Label": label,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "StandardOutPath": standard_out_path,
        "StandardErrorPath": standard_error_path,
    }


def _local_prewarm_script(profile: RemoteCodexProfile) -> str:
    check_command = profile.ssh_base_args(prefer_alias=True)
    check_command[1:1] = ["-O", "check"]
    warm_command = build_ssh_prewarm_command(profile)
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        f"{' '.join(shlex.quote(part) for part in check_command)} >/dev/null 2>&1 && exit 0\n"
        f"exec {' '.join(shlex.quote(part) for part in warm_command)}\n"
    )


def _remote_daemon_script(*, warm_session_count: int) -> str:
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        "export PATH=\"$HOME/.codex/packages/standalone/current:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH\"\n"
        "command -v codex >/dev/null 2>&1 || exit 0\n"
        "codex app-server daemon start >/dev/null 2>&1 || true\n"
        "codex app-server daemon enable-remote-control >/dev/null 2>&1 || true\n"
        "codex app-server daemon version >/dev/null 2>&1 || true\n"
        "test -f \"$HOME/.codex/session_index.jsonl\" && tail -n 200 \"$HOME/.codex/session_index.jsonl\" >/dev/null 2>&1 || true\n"
        "if [ -d \"$HOME/.codex/sessions\" ]; then\n"
        f"  find \"$HOME/.codex/sessions\" -type f -name '*.jsonl' -print 2>/dev/null | sort | tail -n {warm_session_count} | while IFS= read -r file; do\n"
        "    test -f \"$file\" && tail -n 80 \"$file\" >/dev/null 2>&1 || true\n"
        "  done\n"
        "fi\n"
    )


def _preview_token(token: str) -> str:
    if len(token) <= 10:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def default_state_path_from_env() -> Path:
    value = os.environ.get("CODEX_REMOTE_SSH_STATE")
    return Path(value).expanduser() if value else DEFAULT_STATE_PATH
