from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_offline_quickstart_from_an_unrelated_directory_without_bridge(tmp_path):
    work = tmp_path / "new user project"
    work.mkdir()
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("VB_"):
            env.pop(name)
    env["VDA_BRIDGE_PYTHON"] = str(work / "no-bridge-installed")
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    task = ROOT / "examples/tasks/inverter-close-loop.demo.json"

    def cli(*arguments):
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "virtuoso_design_agent", *map(str, arguments)],
            cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert completed.returncode == 0, completed.stderr + completed.stdout
        return completed.stdout

    assert "inverter" in cli("catalog", "--json")
    plan = json.loads(cli("plan", task, "--json"))
    assert plan["confirmation_token"]
    output = work / "artifacts/demo-run.json"
    assert "No action executed" in cli("run", task, "--adapter", "demo", "--output", output)
    assert not output.exists()
    cli("run", task, "--adapter", "demo", "--execute", "--token", plan["confirmation_token"],
        "--output", output)
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["adapter"] == "deterministic-demo"
    assert record["status"] == "succeeded"
    assert record["actions"]
    assert {action["evidence_source"] for action in record["actions"]} == {"software_inference"}
    assert output.with_name("demo-run.checkpoint.json").is_file()
    assert "software_inference" in cli("design-review", task, output, "--json")


def test_public_entrypoint_links_resolve_without_private_artifacts():
    guides = [
        ROOT / "README.md", ROOT / "docs/getting-started.md",
        ROOT / "docs/agent-start-here.md", ROOT / "docs/environment-porting.md",
        ROOT / "references/bridge-compat/README.md",
    ]
    for guide in guides:
        for target in re.findall(r"\]\(([^)]+)\)", guide.read_text(encoding="utf-8")):
            if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                continue
            relative = target.split("#", 1)[0]
            assert relative and (guide.parent / relative).resolve().exists(), (guide, target)
            assert not relative.startswith(("artifacts/", "../artifacts/")), (guide, target)


def test_bridge_reference_bundle_identity_and_safe_scope():
    folder = ROOT / "references/bridge-compat"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    patch = (folder / manifest["patch"]["path"]).read_bytes()
    assert hashlib.sha256(patch).hexdigest() == manifest["patch"]["sha256"]
    assert len(patch) == manifest["patch"]["size_bytes"]
    assert manifest["auto_deploy"] is False
    paths = re.findall(r"^diff --git a/(.+) b/\1$", patch.decode("utf-8"), re.MULTILINE)
    assert set(paths) == {entry["path"] for entry in manifest["files"]}
    assert len(paths) == len(set(paths)) == 25
    for path in paths:
        assert not Path(path).is_absolute() and ".." not in Path(path).parts
        assert Path(path).name != ".env"
        assert path.startswith(("src/", "tests/", "tools/", "examples/")) or path == "LOCAL_VDA_PATCH.md"
    assert {"src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_27.py",
            "src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_3.py"} <= set(paths)
    license_bytes = (folder / manifest["license"]["path"]).read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == manifest["license"]["sha256"]
    assert b"MIT License" in license_bytes and b"Zhishuai Zhang" in license_bytes
