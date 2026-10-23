# 延期的人工 ADE Gate

状态：2026-07-21 经用户确认延期。以下项目不阻塞当前自动化开发，也不得在未执行前写成已验证能力。

2026-10-23 路线优先级说明：这些是特定人工交接/实机证据待办，不是完整缺失的 ADE
执行实现，也不是 L5B 的统一前置清单。只有当前真实任务需要时，才按
[路线的按需验收优先级](l5-roadmap.md)恢复对应项；不为分级重复仿真或要求用户逐个配合。

## 2026-09-21 范围澄清

ADE L 的新建原生会话、保存、新会话加载及 DC 重跑已具备历史实测证据，见[基本操作验证记录](validation/2026-09-21-virtuoso-basic-operations.md)与[ADE L 操作说明](ade-l-native-workflow.md)。这不等于下面 M2 的旧 state 非覆盖迁移已验证，也不自动关闭 M1 的完整人工编辑后捕获或 M3 的其他分析数值交叉检查。延期记录不能再被解释为“ADE L 完全不能使用”。

## M1：人工 Maestro 可重开与双向交接

- 在专用 cell 上人工打开 VDA 新建或已有的 Maestro view。
- 人工修改 design variable、analysis、原生 sweep、output/spec，保存并运行。
- VDA 用 `ade.capture` 固定同一 history，比较修改前后 setup 指纹、Spectre 输入、PSF/log 和逐点 output/spec。
- 验收重点是人工改动没有被 VDA 覆盖，且捕获的结果确实对应修改后的 setup。

## M2：旧 ADE L state 非破坏迁移

- 先备份旧 state 和目标 cellview，记录来源、目标及哈希。
- 只迁移到明确不存在的新 Maestro 目标；不得覆盖旧 state 或已有 Maestro view。
- 迁移后由人工在 Virtuoso 中重开，检查 analysis、变量、output、model/setup 和可重复运行性。
- 完成真实 smoke 前，不宣称 VDA 已直接兼容或打通旧 ADE L。

## M3：人工数值交叉检查

- 在 ADE 中手动读取至少一个 DC、AC 或 sweep 结果，与 VDA run record 中相同 history/point/output 对照。
- 核对 −3 dB bandwidth、GBW 与 unity-gain frequency 没有混用。
- 单次命令成功、history 存在或 return code 0 都不能替代这一步。

## 恢复条件

后续恢复这些 Gate 时，需要用户可操作的 Virtuoso/ADE 窗口和一个专用、允许新增 view/history 的 cell。恢复前重新列出目标 library/cell/view、是否写 OA、是否运行远端计算、预计远端路径以及是否可能覆盖；本文件只记录待办，不构成新的远端授权。
