from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from virtuoso_design_agent import bridge_lifecycle
from virtuoso_design_agent.bridge_lifecycle import (
    BridgeLifecycleError,
    bridge_cli_path,
    run_bridge_lifecycle,
)
from virtuoso_design_agent.cli import main


class _Output:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class _Process:
    def __init__(self, lines: list[str], *, returncode: int = 0) -> None:
        self.stdout = _Output(lines)
        self.returncode = returncode
        self.pid = 4321
        self._handle = 9876
        self.waited = False

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode if self.waited else None


def _fake_bridge_install(tmp_path: Path) -> tuple[Path, Path]:
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    python.touch()
    bridge = bridge_cli_path(python)
    bridge.touch()
    return python, bridge


def test_lifecycle_delegates_to_bridge_cli_without_a_visible_child_console(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    python, bridge = _fake_bridge_install(tmp_path)
    env_file = tmp_path / "bridge.env"
    env_file.touch()
    observed: dict[str, object] = {}
    process = _Process(
        [
            "Starting tunnel [cad]...\n",
            "[cmd] ssh sensitive-but-not-secret-diagnostic\n",
            "tunnel.warm = 1.2s\n",
        ]
    )

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return process

    jobs = []

    class FakeJob:
        def __init__(self, child) -> None:
            assert child is process
            self.closed = False
            jobs.append(self)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(bridge_lifecycle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(bridge_lifecycle, "_WindowsProcessJob", FakeJob)

    assert (
        run_bridge_lifecycle(
            "start",
            bridge_python=python,
            profile="cad",
            env_file=env_file,
        )
        == 0
    )

    assert observed["command"] == [
        str(bridge),
        "start",
        "--profile",
        "cad",
        "--env",
        str(env_file),
    ]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["env"]["PYTHONUNBUFFERED"] == "1"
    assert "shell" not in kwargs
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert kwargs["startupinfo"].wShowWindow == subprocess.SW_HIDE
        assert len(jobs) == 1 and jobs[0].closed is True
    else:
        assert kwargs["start_new_session"] is True
        assert jobs == []

    assert process.waited is True
    assert process.stdout.closed is True
    output = capsys.readouterr().out
    assert "without a separate PowerShell window" in output
    assert "Starting tunnel [cad]" in output
    assert "tunnel.warm = 1.2s" in output
    assert "sensitive-but-not-secret-diagnostic" not in output
    assert "Bridge tunnel: running" in output


def test_lifecycle_verbose_mode_keeps_bridge_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    python, _ = _fake_bridge_install(tmp_path)
    process = _Process(["[cmd] ssh diagnostic\n"])
    monkeypatch.setattr(
        bridge_lifecycle.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        bridge_lifecycle,
        "_WindowsProcessJob",
        lambda child: type("Job", (), {"close": lambda self: None})(),
    )

    assert run_bridge_lifecycle("status", bridge_python=python, verbose=True) == 0
    assert "[cmd] ssh diagnostic" in capsys.readouterr().out


def test_lifecycle_interrupt_terminates_the_owned_process_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python, _ = _fake_bridge_install(tmp_path)

    class InterruptedOutput(_Output):
        def __iter__(self):
            raise KeyboardInterrupt

    process = _Process([])
    process.stdout = InterruptedOutput([])
    terminated: dict[str, object] = {}
    job = type("Job", (), {"close": lambda self: None})()

    monkeypatch.setattr(
        bridge_lifecycle.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(bridge_lifecycle, "_WindowsProcessJob", lambda child: job)
    monkeypatch.setattr(
        bridge_lifecycle,
        "_terminate_process_tree",
        lambda child, *, windows_job=None: terminated.update(
            child=child,
            windows_job=windows_job,
        ),
    )

    with pytest.raises(KeyboardInterrupt):
        run_bridge_lifecycle("start", bridge_python=python)

    assert terminated == {
        "child": process,
        "windows_job": job if os.name == "nt" else None,
    }
    assert process.stdout.closed is True


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object path")
def test_lifecycle_job_creation_failure_cleans_the_child_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python, _ = _fake_bridge_install(tmp_path)
    process = _Process([])
    terminated: list[object] = []
    monkeypatch.setattr(
        bridge_lifecycle.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        bridge_lifecycle,
        "_WindowsProcessJob",
        lambda child: (_ for _ in ()).throw(OSError("job unavailable")),
    )
    monkeypatch.setattr(
        bridge_lifecycle,
        "_terminate_process_tree",
        lambda child, *, windows_job=None: terminated.append(child),
    )

    with pytest.raises(
        BridgeLifecycleError,
        match="Could not establish Bridge launcher process ownership",
    ):
        run_bridge_lifecycle("start", bridge_python=python)

    assert terminated == [process]
    assert process.stdout.closed is True


def test_lifecycle_rejects_a_missing_bridge_cli(tmp_path: Path) -> None:
    python = tmp_path / "Scripts" / "python.exe"
    python.parent.mkdir()
    python.touch()

    with pytest.raises(BridgeLifecycleError, match="Bridge CLI executable not found"):
        run_bridge_lifecycle("start", bridge_python=python)


def test_vda_bridge_command_forwards_connection_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    python = tmp_path / "python.exe"
    env_file = tmp_path / "bridge.env"

    def fake_run(action, **kwargs):
        observed.update(action=action, **kwargs)
        return 0

    monkeypatch.setattr(
        "virtuoso_design_agent.cli.run_bridge_lifecycle",
        fake_run,
    )

    assert (
        main(
            [
                "bridge",
                "start",
                "--bridge-python",
                str(python),
                "--profile",
                "cad",
                "--env",
                str(env_file),
                "--verbose",
            ]
        )
        == 0
    )
    assert observed == {
        "action": "start",
        "bridge_python": str(python),
        "profile": "cad",
        "env_file": env_file,
        "verbose": True,
    }


def test_vda_bridge_command_reports_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "virtuoso_design_agent.cli.run_bridge_lifecycle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            BridgeLifecycleError("cannot launch Bridge")
        ),
    )

    assert main(["bridge", "start"]) == 2
    assert "cannot launch Bridge" in capsys.readouterr().err
