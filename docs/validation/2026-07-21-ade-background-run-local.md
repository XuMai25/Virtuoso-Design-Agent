# 2026-07-21 ADE 后台运行与结果回收本地实现

## 目标

在不要求用户打开或聚焦 Virtuoso 窗口的条件下，运行一个已经保存的 Maestro setup，并把 `run_and_wait` 为本次调用返回的 history 中原生 parametric sweep 逐点 output/spec 交回 VDA。该能力只消费既有 setup，不修改变量、analysis、output、corner 或 schematic。

## 实现

- 新增正交 operation `ade.run`，适用于 `existing_schematic`、反相器和共源任务。
- 任务目标必须是明确的 `library/cell/maestro`，不得混入 VDA parameters、analysis、搜索、constraints、objective、创建或覆盖请求。
- planner 将 setup/test 回读标为只读，将 `run_and_wait` 标为 `remote_compute`；因此执行必须显式设置 `allow_remote_compute: true`，但不要求 OA 写授权。
- worker 只复用 Bridge 公共 `open_session`、`run_and_wait`、`read_results` 和 `close_session`。它先确认 Maestro view 与非空 test，再在 background session 中启动并等待仿真，最后按本次返回的确切 history 读取 Detail output/spec 表。
- history 名称在进入结果读取前做有限字符验证；结果返回的 history 必须与本次 run 一致。session 在成功和失败路径都会关闭。
- 默认 `require_structured_outputs: true`：缺少非空 point/output 表时整个 operation 失败。显式关闭该要求时仍可保留已完成 history，但 executor 把 run 标为 `partial`，不会以 callback/return code 宣称设计成功。

## 证据语义

- setup test 列表：`bridge_readback`
- 本次 history、逐 point 参数、output、spec 和 pass/fail：`eda_result`
- “是否具有结构化输出”和“缺输出时降为 partial”的判断：VDA 执行规则

`ade.run` 不调用 `snapshot`，因此本 operation 不保留 setup 指纹、Spectre input、PSF/log 或文件哈希，也不把 Maestro output 自动映射成 VDA constraints/candidates。需要完整产物时仍使用 `ade.capture`；需要规格闭环时还必须定义 output 到稳定指标的映射并保留同源网表证据。

## 本地验证

- 模型测试覆盖有效任务、错误 view、配置混入、覆盖请求和设置泄漏。
- planner/safety 测试覆盖 remote compute 授权与无 OA write。
- worker 模拟覆盖 background session、带引号 history 规范化、确切 history 结果读取、结构化 sweep 输出、空输出拒绝和 finally close。
- executor 测试覆盖成功、允许空输出时 partial，以及不可信 adapter 证据拒绝。
- subprocess 测试确认 payload 不注入 analysis/prepare/capture 配置，结果动作标为 `eda_result`。

全量结果：`182 passed`。全部 `examples/tasks/*.json` 重新生成计划：`49/49 example plans passed`。`existing-maestro-run.bridge.json` 的计划只包含 `remote_compute`，没有 `remote_write`，并明确披露不捕获网表/PSF 哈希和 history 唯一性未证明。

## 未验证边界

- 尚未在 nics4304/Virtuoso 6.1.8 上执行真实 background Maestro run；本记录只证明 VDA 契约和 Bridge API 组合的本地实现。
- Bridge 的示例明确使用 background `open_session -> run_and_wait -> read_results`，但 `read_results` 文档仍写有 GUI evaluator 要求；6.1.8 的实际行为必须由 live smoke 判定，失败不能归类为电路不可行。
- VDA 已有 global/test/corner 逐 scope 变量 CAS patch 的本地契约，但尚未 live 验证，也未自动创建完整 testbench、stimulus 或 analysis/output；`ade.run` 仍只执行已有保存状态。详见 [`2026-07-21-ade-scoped-variable-patch-local.md`](2026-07-21-ade-scoped-variable-patch-local.md)。
- 尚未捕获 background run 的 netlist/PSF 文件证据，也没有 checkpoint/resume；transport 中断后应先检查是否已产生 history，不能盲目重跑。
- history 命名及是否复用/覆盖旧 history 由已保存 setup 决定；当前公共 background API 不能在 run 前证明名称唯一。计划会披露这一点，真实 smoke 前不得宣称自动路径保留了所有旧结果 history。
- 需要人工打开、修改、保存、重跑、旧 ADE L 迁移和数值交叉检查的项目已延期，见 [`../deferred-manual-gates.md`](../deferred-manual-gates.md)。

本次没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`；第三方仓库仍保持干净。
