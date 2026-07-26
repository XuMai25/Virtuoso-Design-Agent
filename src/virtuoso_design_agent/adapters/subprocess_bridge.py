"""Invoke the real Bridge from its own Python environment via JSON worker."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ..models import AnalysisKind, CircuitKind, EvidenceSource, Operation, TaskSpec
from ..profiles import load_pdk_profile
from .base import AdapterInterrupted, AdapterResult


DEFAULT_BRIDGE_PYTHON = Path(
    r"C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\python.exe"
)
_MARKER = "VDA_RESULT="
_PROCESS_TREE_GRACE_SECONDS = 3.0
_WORKER_COOPERATIVE_CLEANUP_SECONDS = 30.0

_WORKER_ACTIONS = {
    CircuitKind.EXISTING_SCHEMATIC: {
        "inspect": "inspect_existing_schematic",
        "transform": "transform_existing_schematic_topology_delta",
        "apply": "apply_existing_schematic_parameters",
    },
    CircuitKind.INVERTER: {
        "create": "create_inverter",
        "inspect": "inspect_inverter",
        "transform": "transform_inverter_testbench",
        "apply": "apply_inverter_parameters",
        "simulate": "simulate_inverter",
    },
    CircuitKind.COMMON_SOURCE: {
        "create": "create_common_source",
        "inspect": "inspect_common_source",
        "transform": "transform_common_source_source_degeneration",
        "apply": "apply_common_source_parameters",
        "simulate": "simulate_common_source",
    },
    CircuitKind.DIFFERENTIAL_PAIR: {
        "create": "create_differential_pair",
        "inspect": "inspect_differential_pair",
        "transform": "transform_differential_pair",
        "apply": "apply_differential_pair_parameters",
        "simulate": "simulate_differential_pair",
    },
}


class BridgeWorkerError(AdapterInterrupted):
    pass


class _WindowsProcessJob:
    """Own a Windows Job Object without killing it on normal handle close."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel32 = kernel32
        self._handle = handle
        if not kernel32.AssignProcessToJobObject(
            handle,
            wintypes.HANDLE(int(process._handle)),  # type: ignore[attr-defined]
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def terminate(self) -> None:
        import ctypes

        if self._handle is None:
            raise RuntimeError("Bridge worker Job Object is already closed")
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if getattr(self, "_handle", None) is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _hidden_windows_process_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        ),
        "startupinfo": startupinfo,
    }


def _worker_process_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return _hidden_windows_process_kwargs()
    return {"start_new_session": True}


def _terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    windows_job: _WindowsProcessJob | None = None,
) -> None:
    """Stop the worker and descendants after timeout or caller cancellation."""

    if process.poll() is not None and os.name == "nt" and windows_job is None:
        return
    cleanup_error: Exception | None = None
    try:
        if windows_job is not None:
            windows_job.terminate()
        elif os.name == "nt":
            completed = subprocess.run(
                [
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                text=True,
                capture_output=True,
                check=False,
                **_hidden_windows_process_kwargs(),
            )
            if completed.returncode != 0 and process.poll() is None:
                detail = (completed.stderr or completed.stdout).strip()[-500:]
                raise RuntimeError(
                    "taskkill could not stop Bridge worker tree "
                    f"(rc={completed.returncode}): {detail}"
                )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=_PROCESS_TREE_GRACE_SECONDS)
    except Exception as exc:  # best-effort escalation still verifies the parent
        cleanup_error = exc
        if process.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=_PROCESS_TREE_GRACE_SECONDS)
            except Exception as force_exc:
                raise RuntimeError(
                    "Bridge worker process tree cleanup failed: "
                    f"{type(force_exc).__name__}: {force_exc}"
                ) from cleanup_error
    if process.poll() is None:
        raise RuntimeError("Bridge worker remained alive after process tree cleanup")
    if cleanup_error is not None and os.name == "nt":
        raise RuntimeError(
            "Bridge worker exited, but descendant cleanup was not confirmed: "
            f"{type(cleanup_error).__name__}: {cleanup_error}"
        ) from cleanup_error


def _cancel_then_terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    cancel_file: Path,
    windows_job: _WindowsProcessJob | None = None,
    cooperative_timeout: float = _WORKER_COOPERATIVE_CLEANUP_SECONDS,
) -> bool:
    """Let worker ``finally`` blocks run, then clear any local descendants."""

    cooperative_cleanup = False
    if process.poll() is None:
        try:
            cancel_file.touch(exist_ok=True)
            process.wait(timeout=cooperative_timeout)
            cooperative_cleanup = True
        except (OSError, subprocess.TimeoutExpired):
            pass
    _terminate_process_tree(process, windows_job=windows_job)
    return cooperative_cleanup


def _drain_worker_pipes(process: subprocess.Popen[str]) -> None:
    try:
        process.communicate(timeout=_PROCESS_TREE_GRACE_SECONDS)
    except Exception:
        # The process has already been stopped; pipe draining must not hide the
        # original timeout or cancellation.
        pass


class SubprocessBridgeAdapter:
    name = "virtuoso-bridge-subprocess"

    def __init__(
        self,
        bridge_python: str | Path | None = None,
        *,
        artifact_root: str | Path | None = None,
    ) -> None:
        configured = bridge_python or os.getenv("VDA_BRIDGE_PYTHON")
        self.bridge_python = Path(configured) if configured else DEFAULT_BRIDGE_PYTHON
        self.source_root = Path(__file__).resolve().parents[2]
        self.artifact_root = (
            Path(artifact_root)
            if artifact_root is not None
            else Path(__file__).resolve().parents[3] / "artifacts" / "ade-captures"
        )

    def _request(
        self, action: str, payload: dict[str, Any], *, timeout: int
    ) -> dict[str, Any]:
        if not self.bridge_python.is_file():
            raise BridgeWorkerError(f"Bridge Python not found: {self.bridge_python}")
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(self.source_root) + (os.pathsep + existing if existing else "")
        cancel_file = (
            Path(tempfile.gettempdir())
            / f"vda_bridge_cancel_{uuid.uuid4().hex}.flag"
        )
        env["VDA_WORKER_CANCEL_FILE"] = str(cancel_file)
        env["VDA_WORKER_PARENT_PID"] = str(os.getpid())
        process = subprocess.Popen(
            [
                str(self.bridge_python),
                "-m",
                "virtuoso_design_agent.adapters.bridge_worker",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            **_worker_process_kwargs(),
        )
        windows_job: _WindowsProcessJob | None = None
        if os.name == "nt":
            try:
                windows_job = _WindowsProcessJob(process)
            except Exception as exc:
                process.kill()
                process.wait(timeout=_PROCESS_TREE_GRACE_SECONDS)
                _drain_worker_pipes(process)
                cancel_file.unlink(missing_ok=True)
                raise BridgeWorkerError(
                    "Bridge worker was not started because Windows process-tree "
                    f"containment failed: {type(exc).__name__}: {exc}"
                ) from exc
        try:
            stdout, stderr = process.communicate(
                json.dumps({"action": action, "payload": payload}),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            cooperative_cleanup = False
            try:
                cooperative_cleanup = _cancel_then_terminate_process_tree(
                    process,
                    cancel_file=cancel_file,
                    windows_job=windows_job,
                )
            except Exception as cleanup_exc:
                raise BridgeWorkerError(
                    f"Bridge worker timed out after {timeout}s during {action}; "
                    "process tree cleanup was not confirmed: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                ) from exc
            finally:
                _drain_worker_pipes(process)
                if windows_job is not None:
                    windows_job.close()
                cancel_file.unlink(missing_ok=True)
            raise BridgeWorkerError(
                f"Bridge worker timed out after {timeout}s during {action}; "
                "cooperative cleanup "
                + ("completed" if cooperative_cleanup else "was not confirmed")
            ) from exc
        except BaseException:
            try:
                _cancel_then_terminate_process_tree(
                    process,
                    cancel_file=cancel_file,
                    windows_job=windows_job,
                )
            finally:
                _drain_worker_pipes(process)
                if windows_job is not None:
                    windows_job.close()
                cancel_file.unlink(missing_ok=True)
            raise
        if windows_job is not None:
            windows_job.close()
        cancel_file.unlink(missing_ok=True)
        result_line = next(
            (line for line in reversed(stdout.splitlines()) if line.startswith(_MARKER)),
            None,
        )
        if result_line is None:
            detail = (stderr or stdout).strip()[-1000:]
            raise BridgeWorkerError(
                f"Bridge worker returned no structured result (rc={process.returncode}): {detail}"
            )
        result = json.loads(result_line[len(_MARKER) :])
        if not result.get("ok", False):
            raise BridgeWorkerError(result.get("error", "Bridge worker failed"))
        return result["data"]

    @staticmethod
    def _task_payload(task: TaskSpec) -> dict[str, Any]:
        payload = {
            "task_id": task.id,
            "operation": task.operation.value,
            "circuit": task.circuit.value,
            "profile": load_pdk_profile(task.pdk_profile).model_dump(mode="json"),
            "parameters": task.parameters,
            "schematic_transform": (
                task.schematic_transform.model_dump(mode="json", exclude_none=True)
                if task.schematic_transform is not None
                else None
            ),
            "topology_delta": (
                task.topology_delta.model_dump(mode="json")
                if task.topology_delta is not None
                else None
            ),
            "instance_parameter_updates": [
                update.model_dump(mode="json")
                for update in task.instance_parameter_updates
            ],
            "instance_parameter_space": [
                sweep.model_dump(mode="json")
                for sweep in task.instance_parameter_space
            ],
            "replace_existing": task.safety.replace_existing,
            "timeout_seconds": task.limits.timeout_seconds,
        }
        if task.target is not None:
            payload["target"] = task.target.model_dump(mode="json")
        if task.device_characterization is not None:
            payload["device_characterization"] = (
                task.device_characterization.model_dump(mode="json")
            )
        if task.operation not in {
            Operation.DEVICE_CHARACTERIZE,
            Operation.ADE_PREPARE,
            Operation.ADE_CAPTURE,
            Operation.ADE_RUN,
            Operation.ADE_VARIABLES_APPLY,
            Operation.ADE_CORNERS_APPLY,
            Operation.ADE_SETUP_APPLY,
        }:
            payload["analysis"] = task.resolved_analysis().value
            payload["analysis_source"] = (
                "user_input" if task.analysis is not None else "software_inference"
            )
        if task.ac_sweep is not None:
            payload["ac_sweep"] = task.ac_sweep.model_dump(
                mode="json", exclude_none=True
            )
            payload["ac_sweep_user_fields"] = sorted(
                task.ac_sweep.model_fields_set
            )
        if task.linearity_sweep is not None:
            payload["linearity_sweep"] = task.linearity_sweep.model_dump(mode="json")
            payload["linearity_sweep_user_fields"] = sorted(
                task.linearity_sweep.model_fields_set
            )
        if task.noise_sweep is not None:
            payload["noise_sweep"] = task.noise_sweep.model_dump(mode="json")
            payload["noise_sweep_user_fields"] = sorted(
                task.noise_sweep.model_fields_set
            )
        if task.operating_conditions:
            payload["operating_conditions"] = [
                condition.model_dump(mode="json")
                for condition in task.operating_conditions
            ]
            payload["operating_conditions_source"] = "user_input"
        if task.ade_capture is not None:
            payload["ade_capture"] = task.ade_capture.model_dump(mode="json")
            payload["ade_capture_user_fields"] = sorted(
                task.ade_capture.model_fields_set
            )
        if task.ade_prepare is not None:
            payload["ade_prepare"] = task.ade_prepare.model_dump(mode="json")
            payload["ade_prepare_user_fields"] = sorted(
                task.ade_prepare.model_fields_set
            )
        if task.ade_run is not None:
            payload["ade_run"] = task.ade_run.model_dump(mode="json")
            payload["ade_run_user_fields"] = sorted(task.ade_run.model_fields_set)
        if task.ade_variables is not None:
            payload["ade_variables"] = task.ade_variables.model_dump(mode="json")
            payload["ade_variables_user_fields"] = sorted(
                task.ade_variables.model_fields_set
            )
        if task.ade_corners is not None:
            payload["ade_corners"] = task.ade_corners.model_dump(mode="json")
            payload["ade_corners_user_fields"] = sorted(
                task.ade_corners.model_fields_set
            )
        if task.ade_setup is not None:
            payload["ade_setup"] = task.ade_setup.model_dump(mode="json")
            payload["ade_setup_user_fields"] = sorted(
                task.ade_setup.model_fields_set
            )
        return payload

    def probe(self, pdk_profile: str) -> AdapterResult:
        profile = load_pdk_profile(pdk_profile)
        data = self._request(
            "probe", {"profile": profile.model_dump(mode="json")}, timeout=30
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def audit_resources(
        self, pdk_profile: str, *, older_than_days: float = 7.0
    ) -> AdapterResult:
        profile = load_pdk_profile(pdk_profile)
        data = self._request(
            "audit_resources",
            {
                "profile": profile.model_dump(mode="json"),
                "older_than_days": older_than_days,
            },
            timeout=300,
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def characterize_devices(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            "characterize_mos_devices",
            self._task_payload(task),
            timeout=task.limits.timeout_seconds + 240,
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.EDA_RESULT)

    def create_schematic(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            _WORKER_ACTIONS[task.circuit]["create"],
            self._task_payload(task),
            timeout=task.limits.timeout_seconds,
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def inspect_schematic(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            _WORKER_ACTIONS[task.circuit]["inspect"],
            self._task_payload(task),
            timeout=min(task.limits.timeout_seconds, 120),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def transform_schematic(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            _WORKER_ACTIONS[task.circuit]["transform"],
            self._task_payload(task),
            timeout=min(task.limits.timeout_seconds, 180),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def verify_parameters(
        self, task: TaskSpec, expected: dict[str, dict[str, str]]
    ) -> AdapterResult:
        payload = self._task_payload(task)
        payload["verify_instance_parameters"] = True
        payload["expected_instance_parameters"] = expected
        data = self._request(
            _WORKER_ACTIONS[task.circuit]["inspect"],
            payload,
            timeout=min(task.limits.timeout_seconds, 180),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def apply_parameters(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        payload = self._task_payload(task)
        payload["parameters"] = parameters
        data = self._request(
            _WORKER_ACTIONS[task.circuit]["apply"],
            payload,
            timeout=min(task.limits.timeout_seconds, 180),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def capture_ade(self, task: TaskSpec) -> AdapterResult:
        payload = self._task_payload(task)
        payload["capture_output_root"] = str(
            self.artifact_root / task.id / uuid.uuid4().hex
        )
        data = self._request(
            "capture_focused_maestro",
            payload,
            timeout=task.limits.timeout_seconds + 240,
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def prepare_ade(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            "prepare_maestro",
            self._task_payload(task),
            timeout=min(task.limits.timeout_seconds, 180),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def run_ade(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            "run_background_maestro",
            self._task_payload(task),
            timeout=task.limits.timeout_seconds + 240,
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.EDA_RESULT)

    def apply_ade_variables(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            "apply_maestro_variables",
            self._task_payload(task),
            timeout=min(task.limits.timeout_seconds, 180),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def apply_ade_corners(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            "apply_maestro_corners",
            self._task_payload(task),
            timeout=min(task.limits.timeout_seconds, 180),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def apply_ade_setup(self, task: TaskSpec) -> AdapterResult:
        data = self._request(
            "apply_maestro_setup",
            self._task_payload(task),
            timeout=min(task.limits.timeout_seconds, 240),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

    def simulate(
        self, task: TaskSpec, parameters: dict[str, float]
    ) -> AdapterResult:
        payload = self._task_payload(task)
        payload["parameters"] = parameters
        data = self._request(
            _WORKER_ACTIONS[task.circuit]["simulate"],
            payload,
            timeout=(
                task.limits.timeout_seconds * 3 + 240
                if task.resolved_analysis()
                in {AnalysisKind.QUALITY, AnalysisKind.PSRR}
                else task.limits.timeout_seconds + 240
            ),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.EDA_RESULT)
