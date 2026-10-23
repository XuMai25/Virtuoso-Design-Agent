# 设计复盘、下一步建议与窗口管理

整理下一设计的输入时，可用 [`design-intake`](design-intake-workflow.md) 把现有 task/
onboarding、测量和本页的历史复盘/建议合成一份摘要；不重抄角色、权限和候选，不把
历史指标算成当前结果，不自动恢复或执行。单独复盘仍用下列原入口。

## 1. 每轮先读一份摘要

`design-review` 复用现有 `TaskSpec`、`RunRecord`、`evaluate_constraints`，不访问 EDA：

```powershell
.\.venv\Scripts\vda.exe design-review <task.json> <run.json> `
  --checkpoint <optional-checkpoint.json> `
  --output <new-review.json> --markdown <new-review.md>
```

不需要 checkpoint 时省略该选项。JSON 供 agent 使用，中文 Markdown 供人工查看；输出必须
是新文件，不覆盖源 task/run、原报告或人工文件。输入文件的绝对路径和 SHA-256 随摘要保存。

摘要包含目标/PDK/条件、候选参数和 testbench、重新判定的规格值及余量、缺指标/未完成
stage、选择范围、原操作失败、预算与 checkpoint 状态。余量为原指标单位：上限是
`value+tolerance-actual`，下限是 `actual-value+tolerance`，目标是
`tolerance-abs(actual-value)`；缺失不以零替代，也不把离散域不可行称为物理上不可行。
候选原来的来源标签保持不变，报告自身是 `software_inference`。

当前默认要求 task ID 和现版本 plan token 一致。历史版本的 token 不一致时不得伪造 token；
若已有 `op-relinearization-validate` 产生的精确字节审计，可显式提供
`--binding-audit <existing-audit.json>`。只消费其中的 task/run SHA、原 token、执行门与候选
实测值对账，不继承旧的预测精度结论，不补造旧版本缺失的误差字段。报告标为历史审计绑定，
不是现版本远端执行许可；没有对应审计则拒绝拼接旧记录。

`selected_parameter_readback_matched_in_record` 只说明保存的末尾 Bridge 回读与选中参数匹配。
它不是新鲜 OA 检查，也不是全部 topology/placement、原始 PSF/manifest 的再次验收。直接
simulation.run 不写 OA，缺少末尾写回记录属正常边界，不要求为了报告再写一次。
run 的总时长与各 action 时长分别保留；恢复间隔可能计入前者，agent 思考时长没有数据便留空。

## 2. 再决定是否需要继续

```powershell
.\.venv\Scripts\vda.exe design-next <task.json> <run.json> `
  --output <new-next.json> --markdown <new-next.md>
```

决策顺序：执行/证据失败先对账 → winner 额外复核失败先解释 → 预算耗尽先决定是否值得续跑 →
demo 只谈编排 → 已达标可以停止。没有可靠理论/扰动证据时不自动编出 W/L/R 或拓扑改动。
checkpoint 只用于提示恢复边界，不能由摘要自动重放写入。

2026-10-22 增加 `design-iterate <request.json>` 和
`design-next <task> <run> --iteration <request.json>`：从明确的历史 round 列表重建跨轮次
规格保护、复验需求、历史可行锚点及停止条件，不补造一次新仿真。另有
`measurement-compile` 将测量定义和可选范围编译进普通任务。详细契约、可复制示例及
分析边界见[测量与迭代工作流](measurement-and-iteration-workflow.md)；STB 已有一次 N28
原生对照，正式 worker 新接入代码的验证仍为本地回归和旧文件离线重放，详见
[STB 证据工作流](stability-evidence-workflow.md)。

两种已有分析路径可按需接入：

- `--small-signal-policy <policy.json>`：调用已有 OP→MNA 分析器，展示理论/EDA 比较、缺失量
  和未覆盖指标；单点小信号解释不是尺寸灵敏度，也不是跨拓扑性能保证。
- `--model <result.json> --policy <policy.json> --task-template <template.json>`：使用已有
  局部重线性化。先从原 run/policy 确定性复算并比较完整模型；留出门不通过、model 漂移、
  锚点不是本轮已选点、目标/PDK/分析/testbench 漂移时拒绝生成数值任务。显示各 metric 的
  局部灵敏度、留出误差及每个候选的预测收益/代价。未建模的约束仍保留，不能称为已预测通过。

需要普通任务草案时加 `--task-output <new-task.json>`。仍复用原
`build_task_from_relinearization`；目标和规格不变，compute/write/replace 全部关闭，
只能另行审查、授权、plan。没有可用模型时可以保存诊断建议，但不能要求输出假任务。
旧模型与当前算法不一致时，用原 `op-relinearize` 在本地重算到新文件，旧记录不修改。

自动数值提案复用已有局部模型；不宣称自动拟合未经显式映射的 raw CDF 或任意
拓扑。已有通用 CDF、onboarding、topology-delta 能力不受限制；需要新方程/角色映射时仍由
agent 明确分析并在原契约内编译，不为“智能建议”引入第二套求解器或执行器。

2026-10-23 的 P1-b 本地切片已支持显式 `instance_parameter` 数值映射：声明原 CDF 的
Spectre SI 字面量格式、模型单位、含义和范围，复用同一拟合/留出/候选编译器，再输出
普通原子 CDF 候选。未建模的拓扑/testbench/条件变化会拒绝拟合；格点量化不能越界。
`design-next` 无预测改善时不再生成新计算任务。此扩展不是任意 raw CDF 自动识别，
也不证明新候选已在 OA/si/Spectre 上验收。见[使用说明](numeric-cdf-refinement.md)。

固定的源/负载 override 必须随模型和每个候选保留，摘要会展示这些仿真条件；结果审计
也比较逐候选及最终选择的 override，不能仅凭参数/指标相同接受不同条件。旧模型若曾
丢失这些条件，应从原 run/policy 本地重算到新文件并重新编译/plan，不补改旧 token 或
checkpoint。此一致性是记录层检查，不替代实际 simulator input、OA 或新点性能验收。

需要联动的等值 CDF 字段可通过 instance_parameter.linked_parameters 共用一个模型
变量；摘要展示整组关系及逐字段前后值。它不是自动匹配或比例推断，实际 OA/CDF/si
仍分别验收。使用与边界见[数值微调说明](numeric-cdf-refinement.md)。

testbench_parameter 可把原 generic DC 源或电阻/电容负载加入同一个数值模型。摘要
展示映射、模型单位及逐候选源/负载修改，fixed_testbench_overrides 此时只展示剩余
固定项；完整元组仍须一致审计。记录不足、输入缺失、共线或独立留出不通过时不出任务，
不把未变化的负载拟合为零灵敏度，也不把报告值当实际 simulator input 已核对。

## 3. 窗口优先复用，任务完成后收拢

运行前记录真实窗口/session 身份、target、PDK、归属、任务状态、dirty/running 状态及是否
要交给用户。新窗口只在没有兼容可复用会话、确需并排对比或短暂独立 Load 验证时创建。

`windows-plan` 接受 `window_lifecycle.EdaWindowInventory` JSON：

```json
{
  "schema_version": 1,
  "observed_at": "2026-10-19T12:00:00+08:00",
  "evidence_reference": "本次实际会话盘点记录的路径或标识",
  "windows": [{
    "id": "本次实际观察到的句柄，不使用此示例字符串执行",
    "kind": "ade_l",
    "target": {"library": "design_library", "cell": "vda_module", "view": "schematic"},
    "pdk_profile": "nics4304_tsmc18",
    "owner_task_id": "my-task",
    "managed_by_vda": true,
    "task_state": "active",
    "dirty": true,
    "running": false
  }]
}
```

默认缺失信息按保守状态处理。`task_state=completed` 还需
`completion_confirmed_by_user=true`；不得仅凭 idle、旧日期或关闭要求推断其他任务已结束。
本任务状态不明时返回 `ask_task_completion`；不同 `owner_task_id` 的窗口始终 keep，
即使另一任务已确认完成、配置已保存，也不由本任务安排保存、复用或关闭。非 VDA 管理、
归属不明、运行中或用户要求保留的窗口同样 keep。

需要保留的 ADE L 配置返回 `save_cellview_then_verify_load`，直到 `saved_state` 明确记录：

- `storage=cellview`，同 library/cell 的新 state view（不是 schematic）；
- 保存且回读、独立 Load 均通过，当前/保存/加载三个 configuration SHA-256 一致；
- 所需模型/路径依赖可用；若有 Parametric Analysis，额外文件及其加载已确认；
- 当前窗口无未保存改动。

符合条件才给出 `close_after_recheck`。它**不是关闭命令或授权**，实际动作前必须重新盘点，
确认未发生人工改动。未保存的其他窗口先保存或询问；Print/ViVA 要保留的内容先归档。
关闭“参数窗口”不表示删除 design variables、CDF 或原电路参数。

本入口不启动 Bridge、不注入 UI、不自动保存 cellview 或关闭窗口。操作策略已写入 AGENTS
供后续实际任务执行。2026-10-19 已在既有 TSMC18 IVGS 配置上验证 cellview 保存、独立
Load、外层扫描文件恢复和自有窗口关闭，未运行仿真；不等于本地 `windows-plan` 会执行这些
动作，也不代表任意配置均已验证。详见[验收记录](validation/2026-10-19-ade-l-cellview-save-load-close.md)与
[ADE L 原生工作流](ade-l-native-workflow.md)。

连接问题继续按[遗留记录](validation/2026-10-19-windows-tcp-reset-deferred.md)暂缓，不切换 TUN。
