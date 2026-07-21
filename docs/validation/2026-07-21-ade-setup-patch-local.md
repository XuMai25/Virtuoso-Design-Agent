# 2026-07-21 Maestro analysis/output setup patch 本地实现

状态：**local declared-analysis CAS and add-only output/spec persistence contract implemented; nics4304 live pending**。

## 目标

把“在已有 ADE setup 中微调 analysis、增加可由人工继续查看和修改的 output/spec”做成 VDA 的正式正交能力，而不是一次性脚本。新增 operation `ade.setup.apply` 不创建 schematic/test/corner、不修改 design variable、不运行仿真；它只在显式旧状态前置条件成立时保存一次 Maestro setup，并独立重开回读。

## Bridge 与 Cadence 接口边界

- 本机 `virtuoso-bridge-lite` 0.7.0 已公开 `set_analysis`、`add_output`、`set_spec`、`save_setup` 和 background session lifecycle；VDA 直接复用这些 writer，没有复制 SSH、SKILL 执行、OA 或文件传输。
- analysis 回读经 Bridge 的通用 SKILL channel 调用 Cadence `maeGetEnabledAnalysis`/`maeGetAnalysis`。
- output 回读使用 `maeGetTestOutputs`；spec 使用 `axlGetMainSetupDB` + `axlGetSpecData`。Cadence 官方社区也给出了这组读取方法：[SKILL function to get spec column of Maestro test output](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/49224/skill-function-to-get-spec-column-of-meastro-test-output/1378591)。
- 本轮没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。VDA 侧只增加 worker action 和任务/证据契约，因此不产生新的第三方补丁或升级分支负担。

## 契约与执行顺序

任务必须声明 exact `expected_tests`。每个 analysis update 包含：

- `test` / `analysis`
- 完整旧 `expected.enabled/options`；`expected: null` 表示要求该 analysis 不存在
- 目标 `enabled`
- 有限 option delta

已有 analysis 的最终 option map 必须等于“完整旧 map + delta”；新 analysis 允许 Cadence 补充默认项，但任务声明的 option 必须逐项一致。不存在的 analysis 不能用 disabled/no-options 形成空操作；已有状态完全不变的更新也被模型拒绝。

output 首版采用 add-only 契约：

- `net` output 必须给 `signal_name`；
- `point` output 必须给 calculator `expression`；
- 可选 spec 只开放 Bridge public writer 已支持的 `lt` / `gt`；
- 同一 test/name 不能重复，且写前必须确认目标名称不存在。

worker 拒绝与任何已配置的开放 Maestro session 并发保存。写会话先读取全部声明 analysis 旧状态与全部 output absence；任一不符都在第一个 `set_analysis`/`add_output` 前失败。随后每项写入立即结构化回读，全部一致才调用一次 `save_setup`。关闭后以全新 background session 再次核对 tests 和所有目标状态；即时值与重开值必须完全相同。

## 证据语义

- 任务中的旧状态、目标 delta、output/expression/spec：`user_input`
- tests、旧值、即时回读、保存后独立重开值：`bridge_readback`
- targeted 前后 SHA-256：VDA 对上述结构化 readback 的确定性指纹
- 实际 Spectre input、波形和 output 数值：本 operation 不产生，不能标为 `eda_result`

run record 明确保存 `existing_outputs_replaced=false`、`unlisted_setup_state_checked=false`、`full_setup_fingerprint_verified=false`、`automated_simulation_performed=false`。因此“setup patch succeeded”只表示声明配置持久化，不表示 analysis 已执行、output 可求值或规格通过。

## 本地验证

新增测试覆盖：

- model：analysis 旧状态 CAS、new-analysis/no-op 拒绝、net/point source 互斥、test membership、重复 output、operation 泄漏和覆盖请求；
- planner/safety：五步 plan、首写前零写入边界、一次 save、独立重开、只需 remote-write 而不需 remote-compute；
- worker：SKILL S-expression 解析、字符串转义、analysis/output/spec 结构化回读、全部旧值先读后写、一次保存、全新 session 持久化回读；
- 失败注入：任一旧状态不匹配时仍读完全部目标但零 writer/零 save，保存后 readback 不一致时失败；
- subprocess/executor：表达式和旧状态不失真序列化，伪造 target/scope/fingerprint 或即时/持久化不一致均被拒绝。

本地最终回归为 `234 passed in 0.52s`。`existing-maestro-setup-apply.bridge.json` 可生成只含 remote-write、不含 remote-compute 的计划；`52/52` 个 example task 全部重新生成计划成功。`catalog` 在 `existing_schematic`、`inverter` 和 `common_source` 三个 executable circuit 中均列出 `ade.setup.apply`。

## 未验证边界

- 尚未在 nics4304/Virtuoso 6.1.8 上执行真实 setup patch；尤其需要现场确认 `maeGetAnalysis` 默认项、output object 字段和 `axlGetSpecData` 返回形状。
- analysis option 目前只接受可精确比较的扁平 `string | bool | null` alist。遇到嵌套结构会失败并保留原始错误，不会静默扁平化。
- 不替换已有 output。Bridge public writer 没有公开 delete wrapper；同名 `add_output` 的更新/重复语义和未建模的 plot/save/description 保留性未证明，因此不能做破坏性猜测。Bridge 原始能力没有被删除或屏蔽，仍可独立使用。
- targeted fingerprint 只覆盖 exact tests 和任务点名的 analyses/outputs，不声称整个 Maestro setup 未变化。变量、corners、models、stimulus、run mode、job policy 和未声明 output 尚未纳入同一事务指纹。
- `save_setup` 成功后若 close、重开或 transport 失败，setup 可能已经持久化；任务会失败并要求先读回，不能盲目重放，也没有自动回滚。
- 本 operation 不证明 OA design variable 进入 netlist，不产生 Spectre 结果，也不替代 `ade.run`/`ade.capture`。

## 下一道 Gate

background `ade.run` 的 exact-history simulator-input/result/log manifest 与哈希已完成本地实现，见 [`2026-07-21-ade-background-artifact-manifest-local.md`](2026-07-21-ade-background-artifact-manifest-local.md)。下一步是在一个专用 Maestro cell 上按“setup patch → background run → artifact/result readback → netlist 参数核对”执行 live smoke。真实 smoke 前仍需重新列出目标 library/cell/view、OA 写入、远端计算、远端路径和覆盖风险并取得明确授权。
