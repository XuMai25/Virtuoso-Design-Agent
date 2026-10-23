"""Conservative plans for reusing/retiring observed EDA windows, without UI I/O.

Inputs are observations, not permission to close an arbitrary live window. The
agent must recheck identities and save/load receipts immediately before action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, StrictBool, StrictStr, model_validator

from .models import DesignTarget, StrictModel


class AdeLSavedState(StrictModel):
    storage: Literal["cellview", "directory"]
    target: DesignTarget | None = None
    path: StrictStr | None = None
    configuration_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    loaded_configuration_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    saved_and_read_back: StrictBool = False
    independent_load_verified: StrictBool = False
    parametric_file: StrictStr | None = None
    parametric_load_verified: StrictBool = False
    dependencies_verified: StrictBool = False
    evidence_reference: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def require_location(self) -> "AdeLSavedState":
        if self.storage == "cellview" and self.target is None:
            raise ValueError("cellview state requires exact library/cell/view")
        if self.storage == "directory" and not self.path:
            raise ValueError("directory state requires a path")
        return self


class EdaWindow(StrictModel):
    id: StrictStr = Field(min_length=1)
    kind: Literal["ade_l", "maestro", "schematic", "viva", "print", "calculator", "parametric", "other"]
    target: DesignTarget | None = None
    pdk_profile: StrictStr | None = None
    owner_task_id: StrictStr | None = None
    managed_by_vda: StrictBool = False
    task_state: Literal["active", "completed", "unknown"] = "unknown"
    completion_confirmed_by_user: StrictBool = False
    running: StrictBool = False
    dirty: StrictBool = True
    keep_open_for_user: StrictBool = False
    preserve_configuration: StrictBool = True
    has_parametric_setup: StrictBool = False
    configuration_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    saved_state: AdeLSavedState | None = None
    content_archived: StrictBool = False


class EdaWindowInventory(StrictModel):
    schema_version: Literal[1] = 1
    observed_at: datetime
    evidence_reference: StrictStr = Field(min_length=1)
    windows: list[EdaWindow] = Field(max_length=256)

    @model_validator(mode="after")
    def unique_ids(self) -> "EdaWindowInventory":
        if len({w.id for w in self.windows}) != len(self.windows):
            raise ValueError("window identities must be unique")
        return self


def plan_window_lifecycle(inventory: EdaWindowInventory, task_id: str) -> dict:
    entries = []
    for window in inventory.windows:
        action, reason = "keep", "不是 VDA 管理的窗口，不自动关闭。"
        if window.running:
            reason = "仍有运行中的分析/作业，保留。"
        elif window.keep_open_for_user:
            reason = "用户仍需查看/调整，保留。"
        elif not window.managed_by_vda or not window.owner_task_id:
            pass
        elif window.owner_task_id != task_id:
            reason = "窗口属于其他任务；本任务不安排保存、复用或关闭。"
        elif window.task_state == "active":
            action, reason = "reuse", "优先复用本任务已有窗口；重用前核对 target/PDK/状态。"
        elif window.task_state != "completed" or not window.completion_confirmed_by_user:
            action, reason = "ask_task_completion", "询问该窗口对应任务是否已完成；不能由窗口闲置推断。"
        elif window.kind == "ade_l" and window.preserve_configuration:
            saved = window.saved_state
            ready = bool(saved and saved.storage == "cellview" and saved.target and window.target
                         and saved.target.library == window.target.library
                         and saved.target.cell == window.target.cell
                         and saved.target.view != "schematic"
                         and saved.saved_and_read_back and saved.independent_load_verified
                         and saved.dependencies_verified and window.configuration_sha256
                         and window.configuration_sha256 == saved.configuration_sha256
                         == saved.loaded_configuration_sha256
                         and (not window.has_parametric_setup
                              or (saved.parametric_file and saved.parametric_load_verified)))
            if ready and not window.dirty:
                action, reason = "close_after_recheck", "cellview 保存/独立 Load 与当前配置一致；现场复核身份后可关闭。"
            else:
                action, reason = "save_cellview_then_verify_load", (
                    "保留窗口，先另存非覆盖 ADE L state cellview 并验证 Load；"
                    "参数扫描文件和依赖另存核对。目录备份不替代 cellview 交接。"
                )
        elif window.dirty:
            action, reason = "save_or_ask", "存在未保存改动，先保存或询问是否可以丢弃；不自动点击 discard。"
        elif window.preserve_configuration and not window.content_archived:
            action, reason = "archive_then_close", "先保留可重建配置/结果/图表，再现场核对并关闭。"
        else:
            action, reason = "close_after_recheck", "任务已确认完成，窗口无未保存改动；现场核对身份后可关闭。"
        entries.append({"id": window.id, "kind": window.kind,
                        "target": window.target.model_dump(mode="json") if window.target else None,
                        "owner_task_id": window.owner_task_id, "action": action, "reason": reason,
                        "saved_state": window.saved_state.model_dump(mode="json") if window.saved_state else None})
    return {"schema_version": 1, "kind": "window_lifecycle_plan", "source": "software_inference",
            "observed_at": inventory.observed_at.isoformat(), "evidence_reference": inventory.evidence_reference,
            "task_id": task_id, "actions": entries, "executed": False,
            "requires_fresh_identity_and_state_recheck": True,
            "rules": ["复用前核对 library/cell/view、PDK、会话归属与仿真状态；不以标题相似合并窗口。",
                      "未知任务先询问；不会自动关闭、保存、删除变量或修改 OA。",
                      "无用参数窗口可以关闭；删除 design variables/CDF 字段是另一项显式修改。",
                      "保存失败、配置漂移或 Load 未验证时保持窗口开启。"]}
