from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from virtuoso_design_agent.cli import main
from virtuoso_design_agent.execution_scope import (
    ExecutionScopeCompilation,
    canonical_task_bytes,
    compile_execution_scope,
)
from virtuoso_design_agent.models import TaskSpec
from virtuoso_design_agent.planner import build_plan


ROOT = Path(__file__).resolve().parents[1]


def _disabled_copy(source: Path, output: Path) -> TaskSpec:
    task = TaskSpec.model_validate_json(source.read_bytes())
    payload = task.model_dump(mode="json", exclude_none=True)
    payload["safety"]["allow_remote_compute"] = False
    payload["safety"]["allow_remote_write"] = False
    disabled = TaskSpec.model_validate(payload)
    output.write_bytes(canonical_task_bytes(disabled))
    return disabled


def test_execution_scope_compiles_minimum_compute_only_task_and_exact_cli_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "safe.json"
    disabled = _disabled_copy(
        ROOT / "examples/tasks/existing-schematic-generic-common-source-ac.bridge.json",
        source,
    )
    source.write_bytes(source.read_bytes().replace(b"\n", b"\r\n"))

    task, compilation = compile_execution_scope(source)

    assert task.safety.allow_remote_compute is True
    assert task.safety.allow_remote_write is False
    assert task.safety.replace_existing is False
    assert compilation.source_plan_token == build_plan(disabled).confirmation_token
    assert compilation.source_task_file_sha256 != (
        compilation.source_task_canonical_sha256
    )
    assert compilation.execution_plan_token == build_plan(task).confirmation_token
    assert compilation.execution_task_sha256 == hashlib.sha256(
        canonical_task_bytes(task)
    ).hexdigest()
    assert {item.side_effect for item in compilation.remote_steps} == {
        "remote_compute"
    }
    assert compilation.user_confirmation_required is True
    assert compilation.execution_performed is False
    assert set(compilation.evidence_sources.values()) == {"software_inference"}

    output = tmp_path / "live.json"
    record_output = tmp_path / "scope.json"
    assert (
        main(
            [
                "execution-scope",
                str(source),
                "--output",
                str(output),
                "--record-output",
                str(record_output),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert output.read_bytes() == canonical_task_bytes(task)
    assert hashlib.sha256(output.read_bytes()).hexdigest() == (
        compilation.execution_task_sha256
    )
    assert ExecutionScopeCompilation.model_validate_json(
        record_output.read_bytes()
    ) == compilation


def test_execution_scope_derives_write_only_and_compute_plus_write(
    tmp_path: Path,
) -> None:
    write_task = TaskSpec.model_validate_json(
        (
            ROOT
            / "examples/tasks/pmos-loaded-common-source-onboarding-create.bridge.json"
        ).read_bytes()
    )
    write_payload = write_task.model_dump(mode="json", exclude_none=True)
    write_payload["safety"]["allow_remote_write"] = False
    disabled_write = TaskSpec.model_validate(write_payload)

    compute_write = TaskSpec.model_validate_json(
        (
            ROOT
            / "examples/tasks/existing-schematic-parameter-binding-discovery.demo.json"
        ).read_bytes()
    )

    write_path = tmp_path / "write.json"
    both_path = tmp_path / "both.json"
    write_path.write_bytes(canonical_task_bytes(disabled_write))
    both_path.write_bytes(canonical_task_bytes(compute_write))
    write_compiled, _ = compile_execution_scope(write_path)
    both_compiled, _ = compile_execution_scope(both_path)

    assert write_compiled.safety.allow_remote_compute is False
    assert write_compiled.safety.allow_remote_write is True
    assert both_compiled.safety.allow_remote_compute is True
    assert both_compiled.safety.allow_remote_write is True


def test_execution_scope_rejects_enabled_or_purely_read_only_source(
    tmp_path: Path,
) -> None:
    enabled = ROOT / "examples/tasks/existing-schematic-generic-common-source-ac.bridge.json"
    with pytest.raises(ValueError, match="must disable remote compute"):
        compile_execution_scope(enabled)

    read_only = TaskSpec.model_validate_json(
        (ROOT / "examples/tasks/existing-schematic-inspect.bridge.json").read_bytes()
    )
    read_only_path = tmp_path / "read-only.json"
    read_only_path.write_bytes(canonical_task_bytes(read_only))
    with pytest.raises(ValueError, match="no remote compute or OA write"):
        compile_execution_scope(read_only_path)
