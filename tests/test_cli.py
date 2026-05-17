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


def test_doctor_json_reports_issues(monkeypatch, tmp_path):
    state = tmp_path / "hosts.json"
    runner.invoke(app, ["add", "studio", "user@host.local", "--state", str(state)])

    class FakeAgent:
        ok = False
        exit_code = 3
        command = ["agent"]
        stdout = ""
        stderr = "missing"
        label = "agent"
        loaded = False
        plist_path = "/tmp/agent.plist"
        script_path = "/tmp/agent.sh"

    class FakeCommand:
        ok = True
        exit_code = 0
        command = ["ssh"]
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "codex_remote_ssh_kit.cli.run_diagnostics",
        lambda profile, *, timeout: {
            "ok": True,
            "latency_ms": 9,
            "codex_path": "",
            "app_server": "missing",
            "app_server_daemon": "no",
            "remote_control": "missing",
        },
    )
    monkeypatch.setattr("codex_remote_ssh_kit.cli.check_local_prewarm_launch_agent", lambda profile: FakeAgent())
    monkeypatch.setattr("codex_remote_ssh_kit.cli.check_remote_daemon_launch_agent", lambda profile, *, timeout: FakeAgent())
    monkeypatch.setattr("codex_remote_ssh_kit.cli.prewarm_ssh_master", lambda profile, *, timeout: FakeCommand())

    result = runner.invoke(app, ["doctor", "studio", "--json", "--state", str(state)])

    assert result.exit_code == 1
    assert '"ok": false' in result.output
    assert '"check": "codex"' in result.output


def test_doctor_repair_installs_local_agent_and_rechecks(monkeypatch, tmp_path):
    state = tmp_path / "hosts.json"
    ssh_config = tmp_path / "ssh_config"
    runner.invoke(app, ["add", "studio", "user@host.local", "--state", str(state)])

    class Agent:
        ok = False
        exit_code = 3
        command = ["agent"]
        stdout = ""
        stderr = ""
        label = "agent"
        plist_path = "/tmp/agent.plist"
        script_path = "/tmp/agent.sh"

        def __init__(self, loaded):
            self.loaded = loaded

    class Command:
        command = ["ssh"]
        stdout = ""
        stderr = ""
        exit_code = 0

        def __init__(self, ok):
            self.ok = ok

    local_states = iter([Agent(False), Agent(True)])
    prewarm_states = iter([Command(False), Command(True), Command(True)])
    repairs: list[str] = []

    monkeypatch.setattr(
        "codex_remote_ssh_kit.cli.run_diagnostics",
        lambda profile, *, timeout: {
            "ok": True,
            "latency_ms": 9,
            "codex_path": "/opt/homebrew/bin/codex",
            "app_server": "ok",
            "app_server_daemon": "yes",
            "remote_control": "ok",
        },
    )
    monkeypatch.setattr("codex_remote_ssh_kit.cli.check_local_prewarm_launch_agent", lambda profile: next(local_states))
    monkeypatch.setattr("codex_remote_ssh_kit.cli.check_remote_daemon_launch_agent", lambda profile, *, timeout: Agent(True))
    monkeypatch.setattr("codex_remote_ssh_kit.cli.prewarm_ssh_master", lambda profile, *, timeout: next(prewarm_states))
    monkeypatch.setattr(
        "codex_remote_ssh_kit.cli.install_local_prewarm_launch_agent",
        lambda profile, *, interval_seconds: repairs.append("local-agent") or Agent(True),
    )

    result = runner.invoke(
        app,
        [
            "doctor",
            "studio",
            "--repair",
            "--alias",
            "codex-studio",
            "--ssh-config",
            str(ssh_config),
            "--state",
            str(state),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"ok": true' in result.output
    assert '"repair"' in result.output
    assert repairs == ["local-agent"]
    assert "codex-studio" in ssh_config.read_text()


def test_benchmark_json_uses_profile(monkeypatch, tmp_path):
    state = tmp_path / "hosts.json"
    runner.invoke(app, ["add", "studio", "user@host.local", "--state", str(state)])

    def fake_benchmark(profile, *, samples, timeout, include_cold):
        assert profile.name == "studio"
        assert samples == 2
        assert include_cold is True
        return {
            "ok": True,
            "samples": samples,
            "include_cold": include_cold,
            "benchmarks": {
                "warm_ssh_true": {
                    "ok": True,
                    "latencies_ms": [1, 2],
                    "summary": {"min": 1, "p50": 1, "p95": 2, "max": 2},
                    "failures": [],
                    "command": ["ssh"],
                }
            },
        }

    monkeypatch.setattr("codex_remote_ssh_kit.cli.benchmark_remote_profile", fake_benchmark)

    result = runner.invoke(app, ["benchmark", "studio", "--samples", "2", "--include-cold", "--json", "--state", str(state)])

    assert result.exit_code == 0
    assert '"include_cold": true' in result.output
    assert '"warm_ssh_true"' in result.output


def test_install_remote_codex_dry_run(tmp_path):
    state = tmp_path / "hosts.json"
    runner.invoke(app, ["add", "studio", "user@host.local", "--state", str(state)])

    result = runner.invoke(app, ["install-remote-codex", "studio", "--dry-run", "--state", str(state)])

    assert result.exit_code == 0
    assert "brew install --cask codex" in result.output


def test_uninstall_removes_profile_and_components(monkeypatch, tmp_path):
    state = tmp_path / "hosts.json"
    ssh_config = tmp_path / "ssh_config"
    runner.invoke(app, ["add", "studio", "user@host.local", "--state", str(state)])

    class FakeCleanup:
        ok = True
        exit_code = 0
        command = ["cleanup"]
        stdout = ""
        stderr = ""
        removed_paths = ["/tmp/file"]

    monkeypatch.setattr(
        "codex_remote_ssh_kit.cli.uninstall_local_prewarm_launch_agent",
        lambda profile, *, remove_files: FakeCleanup(),
    )
    monkeypatch.setattr(
        "codex_remote_ssh_kit.cli.uninstall_remote_daemon_launch_agent",
        lambda profile, *, remove_files, timeout: FakeCleanup(),
    )

    result = runner.invoke(
        app,
        [
            "uninstall",
            "studio",
            "--ssh-config",
            str(ssh_config),
            "--state",
            str(state),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"profile_removed": true' in result.output
    assert '"profiles": {}' in state.read_text()
