# 2026-10-19 本地设计复盘、下一步建议与窗口策略

## 本轮范围

按用户要求先做设计摘要，再做有依据的下一步建议；复用保存记录和现有分析能力，
不为验收再跑电路。新增 `design-review`、`design-next`、`windows-plan` 三个 CLI 入口。
不连接 Bridge/服务器、不写 OA、不运行 si/Spectre、不改变 TUN、不打开或关闭实际 EDA 窗口。
Bridge 工作树保持干净。项目已有未提交 ADE L/基本操作文档保留，只追加相关窗口约束。

## 实现与验证

- `design_review.py`：task/run/token 和可选 checkpoint 绑定、候选完整性、复用规格计算、
  原单位余量、选择边界、原证据标签、历史回读与当前 OA 状态分离，JSON/中文报告。
- `design_next.py`：先分离执行失败、winner 质量失败、预算、demo；按需调用既有 OP/MNA 和
  局部重线性化。保存模型必须与原 policy/run 复算一致，不能随机补数或放松误差门。
  普通任务草案保留目标/PDK/条件/规格及原白名单和 cell 前缀，compute/write/replace 均关闭。
- `window_lifecycle.py`：对已观察清单做保守决策，不新增远端 GUI 执行器。活动本任务优先
  reuse；未知任务 ask；运行中或用户保留的窗口 keep。ADE L state 必须同设计 cellview
  保存/独立 Load/指纹/扫描文件/依赖齐全且无 dirty 才能建议关闭前现场复核。
- 输出文件必须是新文件；拒绝同路径输出和覆盖。源 task/run/model 不变。

基线在独立临时目录 **937 passed**；最终完整回归 **981 passed / 0 failed / 0 skipped**，
新增 44 项本地测试。覆盖身份/选择/数值/来源错误、模型漂移、PDK/target 漂移、预算、
checkpoint、历史绑定、模型留出失败、禁止放宽执行安全，以及窗口的运行/归属/保存/Load/
配置漂移/外层扫描状态。既有 `catalog`、inverter demo plan、新草案 plan 通过。

首次普通 pytest 启动遇到旧系统临时目录 `pytest-of-Aurora's Echo` 的 WinError 5，
是 fixture setup 权限错误。后续只改用新的 `C:\vbt\review-*` 专用 basetemp，未改系统权限、
未删用户临时目录、未放宽断言。最终 XML：
`artifacts/design-review-20261019/tests-final.xml`。

## 保存的真实记录离线重放

1. TSMC18 固定共源规格的 `10-final-ac.json`：成功生成九条约束的双格式摘要。
   增益余量 `0.826709468 dB`，带宽余量 `25.833556819 MHz`；数值来自旧记录，没有新仿真。
   原任务仅 simulation.run，未包装成该次写回 OA 已确认。
2. PMOS 有源负载共源的通用 onboarding 最终 run：成功识别三候选、candidate 1、
   exact CDF/testbench 选择和保存的最终参数回读；保留历史恢复动作，不把历史失败抹掉。
3. 共源 OP 重线性化最终 run + 原 2D policy：发现旧 plan 与当前重算 token 不同，默认拒绝。
   显式消费已有审计中完全匹配的 task/run SHA、原 token、候选实测值和 execution gate 后，
   仅作为历史绑定放行。该旧审计的 prediction gate 是 partial，未改成通过。
4. 原保存模型与当前算法重算内容不完全相同，因此拒绝直接拿旧模型输出新任务。用原 run
   和 policy 调用既有 `op-relinearize`，本地另存 `model-recomputed.json`；复算通过后才产生
   6 点普通 `design.tune` 草案，plan token `9849a1d8b26d9d74`。它不是执行授权，也不是新最优解。

最终输出在 `artifacts/design-review-20261019/`：`final-tsmc18.json/.md`、`generic.json/.md`、
`final-next.json/.md`、`final-next-task.json` 与模型复算文件。一次本地 CLI wall time 约
0.40 s（摘要）/0.43 s（建议及模型复算）；只代表这两次调用，非统计 benchmark，未测 agent
思考时间，不宣称端到端节省比例。

## 窗口行为与未验证边界

窗口规则已加入 AGENTS，后续实际任务应先盘点再复用，完成后收拢；任务不明则问用户。
关闭参数窗口不等于删除设计变量/CDF。配置未保存或状态漂移时不自动 discard，不杀用户
Virtuoso 会话。`windows-plan` 不执行 UI，不将用户输入的清单当作实时保存成功证明。

已有 ADE L 实测是目录式 state；**cellview 保存→独立 Load→关闭的自动化路径本轮未做 live**。
后续具体任务中要核实当前版本的原生入口及实际回读，不能猜 API 或因需要减少窗口就丢配置。

后续补验：同日获用户授权后，既有 TSMC18 IVGS 的 cellview 保存→独立 Load→关闭已完成，
见[独立 live 记录](2026-10-19-ade-l-cellview-save-load-close.md)。这不改写上文本地回归的范围。
验证时只短暂开一个自有验证会话，完成后收拢。也未改变正式 Maestro `ade.*` 能力边界。

自动数值提案目前复用已有 semantic 局部模型；不是任意 raw CDF/拓扑自主优化器。
摘要不重读远端、不复验全部原始文件，不替代 fresh OA、实际网表或新设计质量验证。
下一步是在用户实际设计中使用这两项本地能力与窗口策略，不新增随机电路 Gate。

入口与契约见[设计复盘与窗口管理](../design-review-workflow.md)。
