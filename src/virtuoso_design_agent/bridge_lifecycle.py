"""Run the public Bridge lifecycle CLI without opening a child console window."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .adapters.subprocess_bridge import (
    DEFAULT_BRIDGE_PYTHON,
    _WindowsProcessJob,
    _terminate_process_tree,
    _worker_process_kwargs,
)


_ACTIONS = frozenset({"start", "status", "stop"})


class BridgeLifecycleError(RuntimeError):
    """The delegated Bridge lifecycle command could not be launched safely."""


def bridge_cli_path(bridge_python: str | Path) -> Path:
    """Resolve the Bridge console-script installed beside its venv Python."""

    python = Path(bridge_python)
    executable = (
        "virtuoso-bridge.exe"
        if python.suffix.lower() == ".exe"
        else "virtuoso-bridge"
    )
    return python.with_name(executable)


def _print_phase(action: str) -> None:
    if action == "start":
        print(
            "Bridge tunnel: starting without a separate PowerShell window...",
            flush=True,
        )
    elif action == "status":
        print("Bridge tunnel: checking status...", flush=True)
    else:
        print("Bridge tunnel: stopping...", flush=True)


def _print_result(action: str, returncode: int, elapsed_seconds: float) -> None:
    elapsed = f"{elapsed_seconds:.1f}s"
    if returncode != 0:
        print(
            f"Bridge tunnel: {action} failed (rc={returncode}, {elapsed}).",
            flush=True,
        )
    elif action == "start":
        print(f"Bridge tunnel: running ({elapsed}).", flush=True)
    elif action == "status":
        print(f"Bridge tunnel: status healthy ({elapsed}).", flush=True)
    else:
        print(f"Bridge tunnel: stopped ({elapsed}).", flush=True)


def run_bridge_lifecycle(
    action: str,
    *,
    bridge_python: str | Path | None = None,
    profile: str | None = None,
    env_file: Path | None = None,
    verbose: bool = False,
) -> int:
    """Delegate start/status/stop to Bridge and stream output in this console.

    VDA owns only the short-lived launcher process. Bridge continues to own
    connection profiles, SSH, daemon deployment, state, and the persistent
    tunnel. On Windows the launcher is hidden and placed in the same Job Object
    boundary used by VDA workers so an interrupted launch cannot leak its
    still-attached descendants.
    """

    if action not in _ACTIONS:
        raise BridgeLifecycleError(f"Unsupported Bridge lifecycle action: {action}")

    python = Path(bridge_python) if bridge_python is not None else DEFAULT_BRIDGE_PYTHON
    executable = bridge_cli_path(python)
    if not executable.is_file():
        raise BridgeLifecycleError(
            f"Bridge CLI executable not found beside Bridge Python: {executable}"
        )
    if env_file is not None and not env_file.is_file():
        raise BridgeLifecycleError(f"Bridge environment file not found: {env_file}")

    command = [str(executable), action]
    if profile:
        command.extend(["--profile", profile])
    if env_file is not None:
        command.extend(["--env", str(env_file)])

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    _print_phase(action)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            **_worker_process_kwargs(),
        )
    except OSError as exc:
        raise BridgeLifecycleError(
            f"Could not launch Bridge lifecycle command: {exc}"
        ) from exc

    windows_job: _WindowsProcessJob | None = None
    if os.name == "nt":
        try:
            windows_job = _WindowsProcessJob(process)
        except Exception as exc:
            try:
                _terminate_process_tree(process)
            except Exception:
                pass
            if process.stdout is not None:
                process.stdout.close()
            raise BridgeLifecycleError(
                f"Could not establish Bridge launcher process ownership: {exc}"
            ) from exc

    try:
        if process.stdout is None:
            raise BridgeLifecycleError("Bridge lifecycle output pipe was not created")
        for raw_line in process.stdout:
            line = raw_line.rstrip("\r\n")
            if line and (verbose or not line.lstrip().startswith("[cmd]")):
                print(line, flush=True)
        returncode = process.wait()
    except KeyboardInterrupt:
        try:
            _terminate_process_tree(process, windows_job=windows_job)
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            _terminate_process_tree(process, windows_job=windows_job)
        except Exception:
            pass
        if isinstance(exc, BridgeLifecycleError):
            raise
        raise BridgeLifecycleError(
            f"Bridge lifecycle command did not finish cleanly: {exc}"
        ) from exc
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if windows_job is not None:
            windows_job.close()

    _print_result(action, returncode, time.monotonic() - started)
    return returncode
