# 2026-10-22 第一、第二批：测量契约与跨轮次控制本地验收

## 授权与证据边界

本轮仅修改 `H:\Virtuoso Design Agent` 的本地代码、文档、示例和必要测试；不做真实远端
OA 写入、仿真、Bridge smoke、GUI 操作或 ADE state 修改。不修改第三方 Bridge 或 Obsidian
Vault，没有为验收另建一堆电路。原工作树已有未提交修改，未 reset、清理或覆盖这些工作。

项目仍为 L5B 通用化开发，**不是完整 L5B closure**。本记录的结论是本地实现通过与既有
记录可复用；新 STB 及跨轮次真实执行仍有独立未验证边界。

## 实现

第一批：

- `measurement-compile` 将显式 metric/analysis/quantity/unit/definition/signals/source 编译为
  普通 TaskSpec 的可选 measurement_plan，绑定 PDK 本地配置 SHA、目标、testbench、sweep、
  条件与 topology alternatives。疑点、缺定义、错节点或未配置 corner 不生成执行任务。
- 明确 UGB 与 GBW、传递函数相位与环路 PM 的区别；required 测量没有数值门槛时仍检查
  是否实际产生有限值，worker/executor 各自执行完整性门。
- optional 温度×电源 min/nom/max 取点，通常 9 点；单轴要求另一轴标称，退化轴去重，
  不覆盖显式 conditions，不默认展开 PVT。主 generic 与 winner conditions 支持九点。
- generic `stability` 接到原 planner/payload/wrapper/worker/executor/stages/winner 路径：
  使用显式 0 V testbench 反馈探针、实际 OA→si 设计 include 和原 Bridge Spectre/PSF 能力；
  本地实现原向量/文件 SHA、符号归一、有效交点和 PM/GM 判定，不从普通 AC 造 PM。

第二批：

- `design-iterate` 重放显式、按时间排列的 task/run/可选 checkpoint/audit；不另建数据库或
  执行器。相同 run 复制路径也不能重复计轮/预算，task/plan、字节审计和来源不符时拒绝。
- 保护已通过的阶段及原始 specs/objective/winner gate。数值退化、缺少复验、来源不足、
  中断未决写入、预算、停滞和达标停止分开记录。保存最佳历史可行锚点，不自动恢复 OA。
- 输入指纹区分 PDK、topology、参数、testbench、条件和 analysis；换验收上下文不沿用旧
  锚点或旧指标补齐本轮。九点需逐点身份/完整性/实测来源及约束；聚合值保留软件推断标签，
  只有真实逐点 lineage 可追溯才作为测量派生结果。
- `design-next --iteration` 先过保护门，再复用既有 OP/MNA/局部重线性化建议；不随机提出
  尺寸、不新增通用求解器。普通草案保留编辑范围、白名单、完整阶段和 winner 规格，关闭
  compute/write/replace。检查草案不是 checkpoint resume 或自动 inverse。

## 本地测试

基线在本轮修改前为 **1026 passed, 1 skipped**。默认 pytest 临时目录受 Windows 权限影响，
因此本轮使用项目内独立 basetemp；未删除用户临时目录、未改系统权限或放宽业务断言。

最终完整回归：**1091 passed, 1 skipped**。XML 位于
`artifacts/l5b-contracts-20261022/tests-release-final.xml`。
跳过的是依赖已安装 Bridge 的 `tests/test_schematic_drawing_bridge.py` 收集；本地 VDA venv
不安装 Bridge。新功能的 worker 测试使用明确的 fake simulator/PSF，不访问 EDA。

风险覆盖包括：

| 风险 | 本地验证方法 |
| --- | --- |
| PM/GM 算法算错 | 两极点解析 unity 解及三极点负裕量的独立公式对照；不是跑另一组电路 |
| 错符号/多交点/触点/平台/频段不足 | 保留诊断并拒绝伪 PM；没有 -180° 交点不造无穷 GM |
| 空/非有限/不递增/长度不符波形 | 提取拒绝；PSF 路径歧义、sweep 端点和 raw SHA 检查 |
| 新 STB 破坏旧任务 | generic DC/AC、独立 operation、staged/winner、原 token 与完整回归 |
| 测量只有文案没有绑定 | 错信号、UGB/GBW/单位混用、上下文/本地 PDK 配置漂移均拒绝 |
| required 指标未产生仍通过 | 单项、stage、PVT 单项、PVT stage 和完整 worker 路径均检查 |
| nominal 冒充九点 | 缺 condition rows、单点违例、聚合假 pass、理论来源混入均不能形成锚点 |
| 后级优化牺牲前级 | 前级 pass→fail 只产生 inspection 草案，保留历史锚点，不自动恢复 |
| 旧指标完成新设计 | 新轮缺 analysis/objective 时要求复验，不 merge 旧 metrics |
| gate 被草案弱化 | 原约束/objective/winner、拓扑/参数编辑范围和白名单检查 |
| 中断盲重放 | failed/incomplete/pending write 仅生成只读检查任务 |
| 无限续跑或假最优 | 显式轮次/预算/停滞/达标停止；有限域范围与物理不可行分开 |

`catalog`、原 `plan examples/tasks/inverter-close-loop.demo.json` 和新 measurement 任务的
plan 均成功。旧反相器计划 token 为 `815e068f84bd532e`，未执行 demo 或远端任务。
新 measurement 任务直接套用旧 run 时被 plan token 绑定门拒绝，诊断后没有生成输出文件。

## 实际保存记录的离线复用

使用已有 N28 有源负载共源任务：

- task：`examples/tasks/pmos-loaded-common-source-onboarding-tune.bridge.json`
- run：`artifacts/runs/vda-pmos-loaded-cs-onboarding/13-onboarding-tune-resume2-20260731.json`
- 原 run token：`765ffdc5ac4ffe87`；当前计划重算匹配，没有补造历史审计或改 token。

`measurement-compile` 成功绑定六项既有测量，输出普通安全草案，两个 analysis/三个原子候选
的主 analysis 上界为 6；这只是编译成本数，不是执行。`design-iterate` 与
`design-next --iteration` 均重算到 `specifications_satisfied → stop`，引用原 candidate 1
作为历史可行锚点，`current_remote_state_verified=false`；没有因工具升级再跑候选。

本地输出在 `artifacts/l5b-contracts-20261022/`，包含 `measurement-report.json`、
`measurement-task.json`、`iteration-final.json/.md`、`next-final.json/.md`。报告是
software_inference；原记录内的 EDA 与 Bridge 回读分类不提升为本轮新测量。
不从本轮 pytest 或 CLI 时长推导远端/agent 思考的端到端提速比例。

## 尚未验证与下一道 Gate

1. 真实 Spectre STB 的实际 PSF 名称、loopGain 符号/探针正负、闭环加载、原生 ADE 数值
   对照及当前 PDK 行为未验收。无环路自动发现、多环 Nyquist 或差分共模稳定性声明。
2. 本地 profile 配置 SHA 不是远端模型文件内容 SHA；历史复盘不验证当前模型、OA 或原始
   PSF/manifest 是否仍未改变，必须在正常真实执行时核对。
3. 新模块跨轮次的参数/局部拓扑改进、真实恢复和最终规格仍需正常任务内验收；本轮没有
   升级任意 raw-CDF 自动灵敏度/优化器，也没有自动执行跨轮次回退。
4. ADE L 已有保存/Load/窗口策略不变；本轮未操作窗口或修改 setup。

下一道 Gate 应依附真实反馈模块需求：确定 PDK、library/cell/view、闭环/探针/负载及符号，
列清 OA 是否写入、计算次数、`/data/xum` scratch、覆盖风险和恢复方式后另行授权。首选
一次不写 OA 的 OA→si→STB 与原生 ADE 对照；若反馈内部不可达，先提出最小非覆盖接入，
不碰已有人工设计。随后在必要设计迭代中验跨轮次保护，不追加随机拓扑或完整全域扫描。

入口与完整契约见[测量与迭代工作流](../measurement-and-iteration-workflow.md)。
