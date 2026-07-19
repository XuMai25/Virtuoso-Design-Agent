# 2026-07-19 显式实例参数能力验证

## 目标

在不复制或缩窄 `virtuoso-bridge-lite` 的前提下，让 VDA 支持两类工作：

1. 不声明固定电路模板，读取任意已有 schematic 的 Bridge 结构结果与实例 CDF 参数。
2. 人工给出实例、原始 CDF 参数名和值字符串后，复用 Bridge callback 写入并形成可审计的 OA 回读证据。

本记录不把人工参数透传表述为自动设计、自动调优或网表逐字段闭环。

## 实现范围

- 新增 `existing_schematic` circuit kind，只开放 `schematic.inspect` 和 `parameters.apply`，不伪装成可仿真的电路模板。
- `instance_parameter_updates` 保留 Bridge 字符串契约，不在 VDA 层限制为空值、120 字符摘要长度或固定参数名格式。
- 固定反相器/共源模板允许 semantic parameters 与实例参数组合；先写 semantic，后执行显式 CDF callback，最终 OA 必须同时满足已声明值。
- inspect 保留 `bridge_schematic` 原始结构，包括 geometry、notes、详细 nets/pins、实例参数与 `nlAction` 等 Bridge reader 字段，同时提供兼容的摘要。
- 写入前确认实例存在；写入复用 Bridge `set_instance_params(..., param_filters=None)`，不复制 callback、`schCheck` 或 `dbSave`。
- 通用 reader 会省略空值和过长值，因此写后另用只读目标 CDF 查询直接比较 `p~>value`；立即验证与 executor 独立 after 验证都必须成功。
- 请求字段标为 `user_input`；真实定向 OA 确认标为 `bridge_readback`；demo 确认仍为 `software_inference`。
- 未修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。

## 本地验证

最终完整测试：`72 passed`。

覆盖内容包括：

- 原始字符串任务契约、空值、256 字符值、非标参数名和非字符串拒绝。
- `existing_schematic` 仅允许 read/apply，不错误宣称 create/simulate/tune。
- semantic + explicit 参数组合不会丢失任一请求。
- demo 的立即确认与独立 after 确认，以及伪造 adapter 回读时整次 run 失败。
- worker 保留未过滤参数，Bridge callback 参数透传，目标 CDF 相等查询不依赖 reader 的空值/长度过滤。
- planner token、副作用、catalog、compileall 和新增示例任务。

## 真实只读证据

目标：`vb_pdk_smoke/vda_cs_gate2a_001/schematic`。

第一次从受限沙箱执行时，本机 DNS 无法解析 `nics4304-cad1`，任务在接触 OA 前失败；失败记录保留：

- `artifacts/runs/common-source-inspect-bridge/explicit-parameter-surface-readonly-20260719.json`

在允许连接既有 EDA 环境后，同一个模板感知任务成功：

- `artifacts/runs/common-source-inspect-bridge/explicit-parameter-surface-readonly-retry1-20260719.json`
- 状态：`succeeded`
- inspect 证据源：`bridge_readback`
- 读取：MN0 233 个非空 CDF 字段，RD0 2 个字段
- 基线字段：`MN0.Wfg=500n`、`MN0.l=30n`、`MN0.fingers=1`、`MN0.m=1`、`RD0.r=20K`

随后使用 `existing_schematic`，不声明共源拓扑，再次成功读取同一目标：

- `artifacts/runs/existing-schematic-inspect-bridge/generic-parameter-surface-readonly-20260719.json`
- 状态：`succeeded`
- inspect 证据源：`bridge_readback`
- 完整结构：2 instances、4 nets、4 pins、geometry 存在
- 参数计数仍为 MN0 233、RD0 2

这闭合了“不依赖固定模板且不丢失 Bridge reader 输出”的读取半边。

最后直接调用同一个 read-only worker inspect action，对当前基线执行目标 CDF 值相等检查；没有调用参数写入：

- `MN0.Wfg=500n`
- `MN0.fingers=1`
- `MN0.m=1`
- `RD0.r=20K`
- 返回：`bridge_readback`、`independent_targeted_cdf_equality`、全部 matched

因此定向回读 SKILL 已在真实 Virtuoso 上验证，不只是 fake client 单元测试。Bridge 公共 `wf`/`nf` 简写由 `set_instance_params` 返回的实际应用映射（`Wfg`/`fingers`）驱动后续验证，VDA 不复制一份可能漂移的别名表。

## 尚未验证的边界

- 本轮没有 OA 参数写入授权，因此真实 callback 以及围绕一次写入的“立即确认 + 独立 after 确认”尚未 live smoke；定向读取本身已验证，但仍不能写成 write/readback verified。
- 空字符串、长字符串与非标参数名已通过契约和 worker 测试，但尚未在当前 PDK 上选取真实 CDF 字段写入；具体 CDF callback 仍可能拒绝不合法值，这应作为 Bridge/PDK 错误保留，而不是由 VDA 猜测。
- 多实例显式写入按 Bridge 调用顺序执行，不是 OA 事务；payload 发送后的 transport 中断仍可能形成不确定状态。独立 `parameters.apply` 尚无调优 checkpoint 的自动 resume 语义。
- 任意实例参数还不能声明为自动 `parameter_space`，也没有对 `si` 网表中的所有原始 CDF 字段逐项建立一致性。当前自动搜索仍只覆盖模板 canonical semantic parameters。
- `existing_schematic` 不提供 create、simulation 或 closure；这些能力需要明确 topology/analysis 契约和新的真实 Gate，不能因通用参数写入而推断成立。

## 下一道 Gate

先通过 `common-source-create-parameter-surface.bridge.json` 新建专用
`vb_pdk_smoke/vda_param_surface_001/schematic`，再用
`common-source-apply-instance-parameters.bridge.json` 把 `MN0.fingers`、`MN0.m` 和 `RD0.r`
分别从创建基线改为 `2`、`2` 和 `22K`，完成一次真实显式参数写入/回读 smoke；两项任务都保持
`replace_existing: false`，不改 Gate 2A 已验证基线。随后进入受控拓扑变更：新建源极退化共源 cellview，明确新增实例/网络/参数和逆操作，先通过 DC 工作区，再进入各拓扑 AC gain/bandwidth。
