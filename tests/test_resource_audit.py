from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from virtuoso_design_agent import resource_audit
from virtuoso_design_agent.cli import main
from virtuoso_design_agent.resource_audit import (
    audit_local_resources,
    load_retention_pins,
)


def test_retention_pin_manifest_requires_exact_local_or_data_xum_paths(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "pins.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pins": [
                    {"path": "critical-run", "reason": "published evidence"},
                    {
                        "path": "/data/xum/vda_runs/vda_gate_001",
                        "reason": "exact live Gate root",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_retention_pins(manifest) == {
        "critical-run": "published evidence",
        "/data/xum/vda_runs/vda_gate_001": "exact live Gate root",
    }

    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pins": [{"path": "old-*", "reason": "too broad"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exact path"):
        load_retention_pins(manifest)


def test_local_resource_audit_is_pin_aware_and_never_authorizes_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_root = tmp_path / "runs"
    pinned = artifact_root / "pinned-run"
    review = artifact_root / "review-run"
    pinned.mkdir(parents=True)
    review.mkdir()
    (pinned / "record.json").write_text("{}", encoding="utf-8")
    (review / "record.json").write_text('{"status":"failed"}', encoding="utf-8")
    now = 2_000_000_000.0
    old = now - 10 * 86400
    os.utime(pinned, (old, old))
    os.utime(review, (old, old))
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    marker = temp_root / "vda_bridge_cancel_stale.flag"
    marker.write_text("", encoding="utf-8")
    stale_simulation = temp_root / "vda_inverter_stale"
    stale_simulation.mkdir()
    (stale_simulation / "input.scs").write_text("simulator lang=spectre\n", encoding="utf-8")
    os.utime(marker, (old, old))
    os.utime(stale_simulation, (old, old))
    monkeypatch.setattr(resource_audit.tempfile, "gettempdir", lambda: str(temp_root))

    audit = audit_local_resources(
        artifact_root,
        older_than_days=7,
        pins={"pinned-run": "validation record"},
        now=now,
    )

    entries = {item["artifact_root_entry"]: item for item in audit["entries"]}
    assert entries["pinned-run"]["pinned"] is True
    assert entries["pinned-run"]["review_candidate"] is False
    assert entries["review-run"]["review_candidate"] is True
    assert all(item["delete_authorized"] is False for item in audit["entries"])
    assert audit["deletion_performed"] is False
    assert audit["transient_cancel_marker_count"] == 1
    assert audit["transient_entry_count"] == 2
    assert {item["kind"] for item in audit["transient_entries"]} == {
        "worker_cancel_marker",
        "inverter_simulation_temp",
    }
    assert all(
        item["delete_authorized"] is False for item in audit["transient_entries"]
    )


def test_resources_cli_emits_read_only_json(tmp_path: Path, capsys) -> None:
    artifact_root = tmp_path / "runs"
    artifact_root.mkdir()
    (artifact_root / "one-run").mkdir()
    output = tmp_path / "resource-audit.json"

    assert (
        main(
            [
                "resources",
                "--artifact-root",
                str(artifact_root),
                "--older-than-days",
                "0",
                "--output",
                str(output),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "read_only_dry_run"
    assert payload["deletion_performed"] is False
    assert payload["local"]["entry_count"] == 1
    assert payload["local"]["entries"][0]["delete_authorized"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == payload
