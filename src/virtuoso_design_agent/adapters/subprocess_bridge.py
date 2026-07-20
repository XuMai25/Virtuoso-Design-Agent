"""Invoke the real Bridge from its own Python environment via JSON worker."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ..models import AnalysisKind, CircuitKind, EvidenceSource, TaskSpec
from ..profiles import load_pdk_profile
from .base import AdapterInterrupted, AdapterResult


DEFAULT_BRIDGE_PYTHON = Path(
    r"C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\python.exe"
)
_MARKER = "VDA_RESULT="

_WORKER_ACTIONS = {
    CircuitKind.EXISTING_SCHEMATIC: {
        "inspect": "inspect_existing_schematic",
        "apply": "apply_existing_schematic_parameters",
    },
    CircuitKind.INVERTER: {
        "create": "create_inverter",
        "inspect": "inspect_inverter",
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
}


class BridgeWorkerError(AdapterInterrupted):
    pass


class SubprocessBridgeAdapter:
    name = "virtuoso-bridge-subprocess"

    def __init__(self, bridge_python: str | Path | None = None) -> None:
        configured = bridge_python or os.getenv("VDA_BRIDGE_PYTHON")
        self.bridge_python = Path(configured) if configured else DEFAULT_BRIDGE_PYTHON
        self.source_root = Path(__file__).resolve().parents[2]

    def _request(
        self, action: str, payload: dict[str, Any], *, timeout: int
    ) -> dict[str, Any]:
        if not self.bridge_python.is_file():
            raise BridgeWorkerError(f"Bridge Python not found: {self.bridge_python}")
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(self.source_root) + (os.pathsep + existing if existing else "")
        try:
            process = subprocess.run(
                [
                    str(self.bridge_python),
                    "-m",
                    "virtuoso_design_agent.adapters.bridge_worker",
                ],
                input=json.dumps({"action": action, "payload": payload}),
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BridgeWorkerError(
                f"Bridge worker timed out after {timeout}s during {action}"
            ) from exc
        result_line = next(
            (line for line in reversed(process.stdout.splitlines()) if line.startswith(_MARKER)),
            None,
        )
        if result_line is None:
            detail = (process.stderr or process.stdout).strip()[-1000:]
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
            "analysis": task.resolved_analysis().value,
            "analysis_source": (
                "user_input" if task.analysis is not None else "software_inference"
            ),
            "target": task.target.model_dump(mode="json"),
            "profile": load_pdk_profile(task.pdk_profile).model_dump(mode="json"),
            "parameters": task.parameters,
            "instance_parameter_updates": [
                update.model_dump(mode="json")
                for update in task.instance_parameter_updates
            ],
            "replace_existing": task.safety.replace_existing,
            "timeout_seconds": task.limits.timeout_seconds,
        }
        if task.ac_sweep is not None:
            payload["ac_sweep"] = task.ac_sweep.model_dump(mode="json")
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
        return payload

    def probe(self, pdk_profile: str) -> AdapterResult:
        profile = load_pdk_profile(pdk_profile)
        data = self._request(
            "probe", {"profile": profile.model_dump(mode="json")}, timeout=30
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.BRIDGE_READBACK)

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
                if task.resolved_analysis() is AnalysisKind.QUALITY
                else task.limits.timeout_seconds + 240
            ),
        )
        return AdapterResult(data=data, evidence_source=EvidenceSource.EDA_RESULT)
