from copy import deepcopy
from pathlib import Path
import json
from types import ModuleType
import sys

import pytest
from pydantic import ValidationError

from virtuoso_design_agent.adapters import bridge_worker
from virtuoso_design_agent.adapters.subprocess_bridge import SubprocessBridgeAdapter
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.planner import build_plan


def task(**changes):
    return TaskSpec.model_validate({
        "id": "parameter-evidence", "operation": "schematic.inspect",
        "circuit": "common_source",
        "target": {"library": "vda_test", "cell": "vda_evidence"},
        **changes,
    })


def test_evidence_is_explicit_read_only_and_bound_to_plan():
    plain, full = task(), task(include_param_evidence=True)
    assert "include_param_evidence" not in SubprocessBridgeAdapter._task_payload(plain)
    assert SubprocessBridgeAdapter._task_payload(full)["include_param_evidence"] is True
    assert build_plan(plain).confirmation_token != build_plan(full).confirmation_token
    assert "stored/CDF" in " ".join(step.description for step in build_plan(full).steps)


@pytest.mark.parametrize("value", ["true", 1])
def test_evidence_flag_requires_boolean(value):
    with pytest.raises(ValidationError):
        task(include_param_evidence=value)


def test_evidence_cannot_silently_expand_write_operation():
    with pytest.raises(ValidationError, match="schematic.inspect"):
        task(operation="parameters.apply", include_param_evidence=True)


def test_legacy_task_keeps_its_approval_token():
    payload = json.loads(Path("examples/tasks/tsmc18-common-source-spec-final-ac.bridge.json").read_text())
    assert build_plan(TaskSpec.model_validate(payload)).confirmation_token == "88ed1e09dfb46c10"


def test_single_read_preserves_empty_nil_long_and_stored_cdf_difference(monkeypatch):
    evidence = {"stored": {"note": "", "unset": None, "w": "1u"},
                "cdf": {"note": "a" * 200, "unset": None, "w": "2u", "enabled": True}}
    data = {"instances": [{"name": "M0", "params": {"w": "2u"},
                           "param_evidence": evidence}]}
    calls = []
    module = ModuleType("virtuoso_bridge.virtuoso.schematic.reader")
    def read(*args, **kwargs):
        calls.append(kwargs)
        return deepcopy(data)
    module.read_schematic = read
    monkeypatch.setitem(sys.modules, module.__name__, module)
    result = bridge_worker._read_inspection_schematic(None, "vda_test", "vda_evidence",
                                                       {"include_param_evidence": True})
    assert len(calls) == 1 and calls[0]["include_param_evidence"] is True
    assert calls[0]["param_filters"] is None
    assert result["instances"][0]["param_evidence"] == evidence
    assert bridge_worker._instance_parameters_from_schematic(result) == {"M0": {"w": "2u"}}


def test_missing_evidence_is_not_silently_accepted(monkeypatch):
    monkeypatch.setattr(bridge_worker, "_read_schematic", lambda *a, **k: {
        "instances": [{"name": "M0", "params": {"w": "1u"}}]})
    with pytest.raises(RuntimeError, match="omitted"):
        bridge_worker._read_inspection_schematic(None, "test", "vda_test",
                                                {"include_param_evidence": True})


def test_demo_does_not_silently_ignore_requested_oa_evidence():
    from virtuoso_design_agent.adapters.demo import DeterministicDemoAdapter

    with pytest.raises(RuntimeError, match="requires the Bridge adapter"):
        DeterministicDemoAdapter().inspect_schematic(task(include_param_evidence=True))
