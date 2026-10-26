from __future__ import annotations

from datetime import UTC, datetime

import pytest

from virtuoso_design_agent.window_lifecycle import EdaWindowInventory, plan_window_lifecycle


def plan(**updates):
    window = {"id": "observed-session-1", "kind": "ade_l", "owner_task_id": "task",
              "target": {"library": "test", "cell": "vda_amp", "view": "schematic"},
              "managed_by_vda": True, "task_state": "completed", "completion_confirmed_by_user": True,
              "dirty": False, "configuration_sha256": "a" * 64, **updates}
    inventory = EdaWindowInventory.model_validate({"observed_at": datetime.now(UTC),
        "evidence_reference": "fixture only, not live UI evidence", "windows": [window]})
    return plan_window_lifecycle(inventory, "task")


def saved(**updates):
    return {"storage": "cellview", "target": {"library": "test", "cell": "vda_amp", "view": "state1"},
            "configuration_sha256": "a" * 64, "loaded_configuration_sha256": "a" * 64,
            "saved_and_read_back": True, "independent_load_verified": True,
            "dependencies_verified": True, "evidence_reference": "synthetic receipt", **updates}


@pytest.mark.parametrize("updates,action", [
    ({"task_state": "active"}, "reuse"),
    ({"task_state": "unknown"}, "ask_task_completion"),
    ({"completion_confirmed_by_user": False}, "ask_task_completion"),
    ({"running": True}, "keep"),
    ({"managed_by_vda": False}, "keep"),
    ({"keep_open_for_user": True}, "keep"),
    ({}, "save_cellview_then_verify_load"),
    ({"saved_state": saved()}, "close_after_recheck"),
    ({"saved_state": saved(storage="directory", path="saved-dir")}, "save_cellview_then_verify_load"),
    ({"saved_state": saved(loaded_configuration_sha256="b" * 64)}, "save_cellview_then_verify_load"),
    ({"saved_state": saved(), "dirty": True}, "save_cellview_then_verify_load"),
    ({"saved_state": saved(target={"library": "test", "cell": "vda_other", "view": "state1"})}, "save_cellview_then_verify_load"),
    ({"owner_task_id": None}, "keep"),
    ({"saved_state": saved(), "has_parametric_setup": True}, "save_cellview_then_verify_load"),
    ({"saved_state": saved(parametric_file="params.param", parametric_load_verified=True), "has_parametric_setup": True}, "close_after_recheck"),
    ({"kind": "print", "content_archived": True}, "close_after_recheck"),
    ({"kind": "schematic", "dirty": True}, "save_or_ask"),
])
def test_lifecycle_conservative_decisions(updates, action):
    report = plan(**updates)
    assert report["actions"][0]["action"] == action
    assert report["executed"] is False
    assert report["requires_fresh_identity_and_state_recheck"]


def test_duplicate_windows_rejected():
    window = {"id": "same", "kind": "print"}
    with pytest.raises(ValueError, match="unique"):
        EdaWindowInventory.model_validate({"observed_at": datetime.now(UTC), "evidence_reference": "test",
                                           "windows": [window, window]})


@pytest.mark.parametrize("state", ["active", "completed", "unknown"])
def test_other_task_windows_are_not_offered_for_save_or_close(state):
    report = plan(owner_task_id="another-task", task_state=state, saved_state=saved())
    assert report["actions"][0]["action"] == "keep"
    assert report["actions"][0]["owner_task_id"] == "another-task"
