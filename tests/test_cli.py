from typer.testing import CliRunner

from codex_remote_ssh_kit.cli import app


runner = CliRunner()


def test_add_and_list_json(tmp_path):
    state = tmp_path / "hosts.json"

    add = runner.invoke(
        app,
        [
            "add",
            "studio",
            "user@host.local",
            "--identity-file",
            "~/.ssh/id_ed25519",
            "--state",
            str(state),
        ],
    )

    assert add.exit_code == 0
    assert "studio" in add.output

    listed = runner.invoke(app, ["list", "--json", "--state", str(state)])

    assert listed.exit_code == 0
    assert '"studio"' in listed.output
    assert '"target": "user@host.local"' in listed.output


def test_missing_profile_returns_error(tmp_path):
    result = runner.invoke(app, ["check", "missing", "--state", str(tmp_path / "hosts.json")])

    assert result.exit_code == 1
    assert "Profile not found" in result.output


def test_official_bootstrap_writes_ssh_config(monkeypatch, tmp_path):
    state = tmp_path / "hosts.json"
    ssh_config = tmp_path / "ssh_config"
    runner.invoke(app, ["add", "studio", "user@host.local", "--state", str(state)])

    def fake_diagnostics(profile, *, timeout):
        return {
            "ok": True,
            "latency_ms": 12,
            "hostname": "host.local",
            "user": "user",
            "codex_version": "codex-cli 0.131.0",
            "remote_control": "ok",
            "session_index_lines": "11",
        }

    monkeypatch.setattr("codex_remote_ssh_kit.cli.run_diagnostics", fake_diagnostics)
    opened: list[list[str]] = []
    monkeypatch.setattr("codex_remote_ssh_kit.cli.subprocess.run", lambda args, check=False: opened.append(args))

    result = runner.invoke(
        app,
        [
            "official-bootstrap",
            "studio",
            "--alias",
            "codex-studio",
            "--ssh-config",
            str(ssh_config),
            "--state",
            str(state),
        ],
    )

    assert result.exit_code == 0
    assert "Codex App Remote SSH" in result.output
    assert "codex-studio" in ssh_config.read_text()
    assert '"ssh_alias": "codex-studio"' in state.read_text()
    assert opened == [["open", "codex://settings/connections"]]


def test_prewarm_dry_run_prefers_alias(tmp_path):
    state = tmp_path / "hosts.json"
    runner.invoke(
        app,
        [
            "add",
            "studio",
            "user@host.local",
            "--ssh-alias",
            "codex-studio",
            "--state",
            str(state),
        ],
    )

    result = runner.invoke(app, ["prewarm", "studio", "--dry-run", "--state", str(state)])

    assert result.exit_code == 0
    assert "ssh -M -N -f -o ExitOnForwardFailure=yes codex-studio" in result.output


def test_optimize_app_runs_all_setup(monkeypatch, tmp_path):
    state = tmp_path / "hosts.json"
    ssh_config = tmp_path / "ssh_config"
    runner.invoke(app, ["add", "studio", "user@host.local", "--state", str(state)])
    calls: list[str] = []

    class FakeCommand:
        ok = True
        exit_code = 0
        command = ["ok"]
        stdout = ""
        stderr = ""

    class FakeAgent(FakeCommand):
        label = "agent"
        plist_path = "/tmp/agent.plist"
        script_path = "/tmp/agent.sh"

    monkeypatch.setattr("codex_remote_ssh_kit.cli.bootstrap_remote_daemon", lambda profile, *, timeout: calls.append("daemon") or FakeCommand())
    monkeypatch.setattr(
        "codex_remote_ssh_kit.cli.install_remote_daemon_launch_agent",
        lambda profile, *, interval_seconds, warm_session_count, timeout: calls.append("remote-agent") or FakeAgent(),
    )
    monkeypatch.setattr(
        "codex_remote_ssh_kit.cli.install_local_prewarm_launch_agent",
        lambda profile, *, interval_seconds: calls.append("local-agent") or FakeAgent(),
    )
    monkeypatch.setattr("codex_remote_ssh_kit.cli.prewarm_ssh_master", lambda profile, *, timeout: calls.append("prewarm") or FakeCommand())
    monkeypatch.setattr(
        "codex_remote_ssh_kit.cli.run_diagnostics",
        lambda profile, *, timeout: {
            "ok": True,
            "latency_ms": 8,
            "codex_version": "codex-cli 0.131.0",
            "session_index_lines": "12",
        },
    )
    opened: list[list[str]] = []
    monkeypatch.setattr("codex_remote_ssh_kit.cli.subprocess.run", lambda args, check=False: opened.append(args))

    result = runner.invoke(
        app,
        [
            "optimize-app",
            "studio",
            "--alias",
            "codex-studio",
            "--ssh-config",
            str(ssh_config),
            "--state",
            str(state),
        ],
    )

    assert result.exit_code == 0
    assert calls == ["daemon", "remote-agent", "local-agent", "prewarm"]
    assert "Codex App Remote Optimization" in result.output
    assert "codex-studio" in ssh_config.read_text()
    assert opened == [["open", "codex://settings/connections"]]
