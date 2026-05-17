import json
import subprocess

from codex_remote_ssh_kit.bridge import (
    DEFAULT_REMOTE_PORT,
    RemoteCodexProfile,
    bootstrap_remote_daemon,
    build_official_ssh_config_block,
    build_bridge_plan,
    build_remote_install_command,
    build_remote_upgrade_command,
    build_ssh_prewarm_command,
    check_local_prewarm_launch_agent,
    check_remote_daemon_launch_agent,
    get_profile,
    install_local_prewarm_launch_agent,
    install_remote_daemon_launch_agent,
    install_official_ssh_config,
    load_profiles,
    parse_ssh_target,
    prewarm_ssh_master,
    run_diagnostics,
    save_profiles,
    uninstall_local_prewarm_launch_agent,
    uninstall_official_ssh_config,
    uninstall_remote_daemon_launch_agent,
    upsert_profile,
)


def test_profile_round_trip(tmp_path):
    path = tmp_path / "hosts.json"
    profile = RemoteCodexProfile(
        name="studio",
        target="user@host.local",
        identity_file="~/.ssh/id_ed25519",
        local_port=40123,
        extra_ssh_args=["-o", "ServerAliveInterval=15"],
    )

    upsert_profile(profile, path)

    loaded = get_profile("studio", path)
    assert loaded == profile
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_profiles_returns_empty_for_missing_file(tmp_path):
    assert load_profiles(tmp_path / "missing.json") == {}


def test_save_profiles_omits_empty_optional_values(tmp_path):
    path = tmp_path / "hosts.json"
    save_profiles({"m": RemoteCodexProfile(name="profile", target="user@host")}, path)

    raw = json.loads(path.read_text())
    saved = raw["profiles"]["m"]
    assert saved["target"] == "user@host"
    assert saved["remote_port"] == DEFAULT_REMOTE_PORT
    assert "identity_file" not in saved
    assert "local_port" not in saved
    assert "extra_ssh_args" not in saved


def test_build_bridge_plan_uses_capability_token_and_loopback_tunnel():
    profile = RemoteCodexProfile(
        name="mac mini",
        target="user@host.local",
        port=2222,
        identity_file="/tmp/key",
        remote_workspace="~/projects",
        extra_ssh_args=["-o", "ProxyJump=bastion"],
    )

    plan = build_bridge_plan(
        profile,
        token="capability-token-value",
        local_port=50001,
        remote_port=50002,
    )

    assert plan.websocket_url == "ws://127.0.0.1:50001"
    assert plan.token_file == "/tmp/codex-remote-ssh-mac-mini.token"
    assert plan.token_preview == "capa...alue"
    assert "capability-token-value" not in plan.token_preview
    assert "if codex app-server --help 2>&1 | grep -q -- '--ws-auth'" in plan.remote_start_command
    assert "codex app-server --listen ws://127.0.0.1:50002" in plan.remote_start_command
    assert "auth_flags='--ws-auth capability-token --ws-token-file /tmp/codex-remote-ssh-mac-mini.token'" in plan.remote_start_command
    assert "codex app-server --listen ws://127.0.0.1:50002 $auth_flags" in plan.remote_start_command
    assert "mkdir -p $HOME/projects" in plan.remote_start_command
    assert plan.tunnel_command == [
        "ssh",
        "-N",
        "-L",
        "127.0.0.1:50001:127.0.0.1:50002",
        "-o",
        "ExitOnForwardFailure=yes",
        "-i",
        "/tmp/key",
        "-p",
        "2222",
        "-o",
        "ProxyJump=bastion",
        "user@host.local",
    ]


def test_run_diagnostics_parses_remote_facts(monkeypatch):
    profile = RemoteCodexProfile(name="profile", target="user@host")

    def fake_run(*args, **kwargs):
        assert args[0][0] == "ssh"
        assert kwargs["timeout"] == 3
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                "hostname=host.local\n"
                "user=user\n"
                "codex_path=/opt/homebrew/bin/codex\n"
                "codex_version=codex-cli 0.130.0\n"
                "session_index_lines=12\n"
                "sessions_size=4.1G\n"
                "mcp_size=32M\n"
                "skills_size=12M\n"
                "app_server=ok\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    facts = run_diagnostics(profile, timeout=3)

    assert facts["ok"] is True
    assert facts["hostname"] == "host.local"
    assert facts["codex_version"] == "codex-cli 0.130.0"
    assert facts["app_server"] == "ok"
    assert facts["profile"]["target"] == "user@host"


def test_run_diagnostics_detects_remote_control_from_top_level_help(monkeypatch):
    profile = RemoteCodexProfile(name="profile", target="user@host")

    def fake_run(*args, **kwargs):
        command = args[0][-1]
        assert "grep -q '^  remote-control\\b'" in command
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="remote_control=missing\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    facts = run_diagnostics(profile, timeout=3)

    assert facts["remote_control"] == "missing"


def test_parse_ssh_target_supports_user_and_plain_host():
    assert parse_ssh_target("user@host.local") == ("user", "host.local")
    assert parse_ssh_target("studio.local") == (None, "studio.local")


def test_build_official_ssh_config_block():
    profile = RemoteCodexProfile(
        name="Studio Mac",
        target="user@host.local",
        port=2222,
        identity_file="~/.ssh/id_ed25519",
        extra_ssh_args=["-o", "ProxyJump=bastion", "-oServerAliveInterval=15"],
    )

    block = build_official_ssh_config_block(profile, alias="codex-studio")

    assert "Host codex-studio" in block
    assert "HostName host.local" in block
    assert "User user" in block
    assert "Port 2222" in block
    assert "IdentityFile ~/.ssh/id_ed25519" in block
    assert "IdentitiesOnly yes" in block
    assert "PreferredAuthentications publickey" in block
    assert "PasswordAuthentication no" in block
    assert "KbdInteractiveAuthentication no" in block
    assert "StrictHostKeyChecking accept-new" in block
    assert "ControlMaster auto" in block
    assert "ControlPersist 30m" in block
    assert "Compression no" in block
    assert "ConnectTimeout 5" in block
    assert "ConnectionAttempts 1" in block
    assert "ProxyJump bastion" in block
    assert "ServerAliveInterval 15" in block


def test_build_official_ssh_config_block_keeps_password_fallback_without_identity():
    profile = RemoteCodexProfile(name="Studio Mac", target="user@host.local")

    block = build_official_ssh_config_block(profile, alias="codex-studio")

    assert "PreferredAuthentications publickey" not in block
    assert "PasswordAuthentication no" not in block
    assert "ControlMaster auto" in block
    assert "ControlPersist 30m" in block


def test_build_ssh_prewarm_command_prefers_alias():
    profile = RemoteCodexProfile(
        name="studio",
        target="user@host.local",
        ssh_alias="codex-studio",
    )

    command = build_ssh_prewarm_command(profile)

    assert command == [
        "ssh",
        "-M",
        "-N",
        "-f",
        "-o",
        "ExitOnForwardFailure=yes",
        "codex-studio",
    ]


def test_bootstrap_remote_daemon_runs_official_daemon_commands(monkeypatch):
    profile = RemoteCodexProfile(name="profile", target="user@host", ssh_alias="codex-host")

    def fake_run(*args, **kwargs):
        command = args[0]
        assert command[:2] == ["ssh", "codex-host"]
        assert "codex app-server daemon version >/dev/null 2>&1" in command[-1]
        assert "codex app-server daemon bootstrap --remote-control" in command[-1]
        assert "codex app-server daemon start" in command[-1]
        assert "codex app-server daemon enable-remote-control" in command[-1]
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"status":"running"}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = bootstrap_remote_daemon(profile, timeout=3)

    assert result.ok is True
    assert result.stdout == '{"status":"running"}'


def test_prewarm_ssh_master_is_idempotent_when_master_exists(monkeypatch):
    profile = RemoteCodexProfile(name="profile", target="user@host", ssh_alias="codex-host")
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        command = args[0]
        calls.append(command)
        assert command[:4] == ["ssh", "-O", "check", "codex-host"]
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="Master running (pid=123)\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = prewarm_ssh_master(profile, timeout=3)

    assert result.ok is True
    assert result.stdout == "Master running (pid=123)"
    assert calls == [["ssh", "-O", "check", "codex-host"]]


def test_install_local_prewarm_launch_agent_writes_script_and_plist(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    profile = RemoteCodexProfile(name="studio", target="user@host", ssh_alias="codex-studio")

    result = install_local_prewarm_launch_agent(profile, interval_seconds=60)

    assert result.ok is True
    assert result.label == "com.conpera.codex-remote-ssh.studio.prewarm"
    assert "ssh -O check codex-studio" in (tmp_path / ".codex-remote-ssh-kit/bin/com.conpera.codex-remote-ssh.studio.prewarm.sh").read_text()
    assert (tmp_path / "Library/LaunchAgents/com.conpera.codex-remote-ssh.studio.prewarm.plist").exists()
    assert calls[-1][:2] == ["launchctl", "bootstrap"]


def test_install_remote_daemon_launch_agent_warms_sessions(monkeypatch):
    profile = RemoteCodexProfile(name="studio", target="user@host", ssh_alias="codex-studio")

    def fake_run(*args, **kwargs):
        command = args[0]
        assert command[:2] == ["ssh", "codex-studio"]
        script = command[-1]
        assert "codex app-server daemon start >/dev/null 2>&1 || true" in script
        assert "codex app-server daemon enable-remote-control >/dev/null 2>&1 || true" in script
        assert "tail -n 200 \"$HOME/.codex/session_index.jsonl\"" in script
        assert "tail -n 25 | while" in script
        assert "launchctl bootstrap gui/$(id -u)" in script
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = install_remote_daemon_launch_agent(profile, warm_session_count=25, timeout=3)

    assert result.ok is True
    assert result.label == "com.conpera.codex-remote-ssh.studio.daemon"


def test_install_official_ssh_config_is_managed_and_idempotent(tmp_path):
    path = tmp_path / "config"
    path.write_text("Host existing\n  HostName example.com\n")
    profile = RemoteCodexProfile(name="studio", target="user@host.local")

    first = install_official_ssh_config(profile, alias="codex-studio", path=path)
    second = install_official_ssh_config(profile, alias="codex-studio", path=path)

    text = path.read_text()
    assert first.changed is True
    assert second.changed is False
    assert "Host existing" in text
    assert text.count("Host codex-studio") == 1


def test_install_official_ssh_config_preserves_multiple_managed_hosts(tmp_path):
    path = tmp_path / "config"
    first = RemoteCodexProfile(name="studio", target="user@host.local")
    second = RemoteCodexProfile(name="lab", target="ops@lab.local")

    install_official_ssh_config(first, alias="codex-studio", path=path)
    install_official_ssh_config(second, alias="codex-lab", path=path)

    text = path.read_text()
    assert text.count("Host codex-studio") == 1
    assert text.count("Host codex-lab") == 1
    assert text.count("# >>> codex-remote-ssh official hosts >>>") == 1


def test_uninstall_official_ssh_config_removes_only_one_managed_host(tmp_path):
    path = tmp_path / "config"
    first = RemoteCodexProfile(name="studio", target="user@host.local", ssh_alias="codex-studio")
    second = RemoteCodexProfile(name="lab", target="ops@lab.local")
    install_official_ssh_config(first, alias="codex-studio", path=path)
    install_official_ssh_config(second, alias="codex-lab", path=path)

    result = uninstall_official_ssh_config(first, path=path)

    text = path.read_text()
    assert result.changed is True
    assert "Host codex-studio" not in text
    assert "Host codex-lab" in text


def test_uninstall_official_ssh_config_removes_empty_section(tmp_path):
    path = tmp_path / "config"
    profile = RemoteCodexProfile(name="studio", target="user@host.local")
    install_official_ssh_config(profile, alias="codex-studio", path=path)

    uninstall_official_ssh_config(profile, alias="codex-studio", path=path)

    assert "codex-remote-ssh official hosts" not in path.read_text()


def test_build_remote_upgrade_command_defaults_to_no_auto_update():
    profile = RemoteCodexProfile(name="studio", target="user@host.local")

    command = build_remote_upgrade_command(profile)

    assert command[:2] == ["ssh", "user@host.local"]
    assert "HOMEBREW_NO_AUTO_UPDATE=1 brew upgrade --cask codex" in command[-1]
    assert "remote-control" in command[-1]


def test_build_remote_install_command_installs_when_codex_missing():
    profile = RemoteCodexProfile(name="studio", target="user@host.local")

    command = build_remote_install_command(profile)

    assert "brew install --cask codex" in command[-1]
    assert "brew upgrade --cask codex" in command[-1]


def test_check_local_prewarm_launch_agent_reports_loaded(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="state = running\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = check_local_prewarm_launch_agent(RemoteCodexProfile(name="studio", target="user@host"))

    assert result.loaded is True
    assert result.label == "com.conpera.codex-remote-ssh.studio.prewarm"


def test_check_remote_daemon_launch_agent_reports_missing(monkeypatch):
    profile = RemoteCodexProfile(name="studio", target="user@host", ssh_alias="codex-studio")

    def fake_run(*args, **kwargs):
        assert args[0][:2] == ["ssh", "codex-studio"]
        return subprocess.CompletedProcess(args=args[0], returncode=3, stdout="", stderr="missing")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = check_remote_daemon_launch_agent(profile)

    assert result.loaded is False
    assert result.exit_code == 3


def test_uninstall_local_prewarm_launch_agent_removes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    profile = RemoteCodexProfile(name="studio", target="user@host")
    label = "com.conpera.codex-remote-ssh.studio.prewarm"
    plist = tmp_path / f"Library/LaunchAgents/{label}.plist"
    script = tmp_path / f".codex-remote-ssh-kit/bin/{label}.sh"
    plist.parent.mkdir(parents=True)
    script.parent.mkdir(parents=True)
    plist.write_text("plist")
    script.write_text("script")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""),
    )

    result = uninstall_local_prewarm_launch_agent(profile)

    assert str(plist) in result.removed_paths
    assert str(script) in result.removed_paths
    assert not plist.exists()
    assert not script.exists()


def test_uninstall_remote_daemon_launch_agent_removes_remote_files(monkeypatch):
    profile = RemoteCodexProfile(name="studio", target="user@host", ssh_alias="codex-studio")

    def fake_run(*args, **kwargs):
        command = args[0]
        assert command[:2] == ["ssh", "codex-studio"]
        assert "launchctl bootout" in command[-1]
        assert "rm -f" in command[-1]
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = uninstall_remote_daemon_launch_agent(profile)

    assert result.exit_code == 0
    assert "$HOME/Library/LaunchAgents/com.conpera.codex-remote-ssh.studio.daemon.plist" in result.removed_paths
