# 测量与跨轮次设计迭代

本地工具将明确测量和已有证据接入普通任务，不替用户猜规格、补造尺寸或开启远端权限。
这两批是 L5B 通用化基础，**不是完整 L5B 已闭合**。不依赖固定共源/差分拓扑；正式测量
首版面向已有 `existing_schematic` 通用任务，其他模板仍可继续使用原正交 operation。

## 1. 先把“要测什么”固定下来

agent 结合用户目标与已知拓扑写出 `MeasurementRequest`，再交给确定性编译器。最小例：

```json
{
  "schema_version": 1,
  "measurements": [{
    "metric": "unity_gain_frequency_hz",
    "analysis": "ac",
    "quantity": "unity_gain_frequency",
    "unit": "Hz",
    "definition": "V(OUT,VSS)/V(IN,VSS) 幅度降到 1 的有效交点；不是增益乘带宽",
    "signals": ["V(IN,VSS)", "V(OUT,VSS)"],
    "required": true,
    "source": "user_input"
  }],
  "uncertainties": []
}
```

此片段不是可执行电路任务。每条约束及 objective 必须有 `required=true` 的定义；完整
复用示例是 [`examples/measurements/pmos-loaded-cs.json`](../examples/measurements/pmos-loaded-cs.json)，
沿用原共源任务的现有指标与规格，不从 PPT 抄一组数成为所有电路默认值。

```powershell
.\.venv\Scripts\vda.exe measurement-compile `
  examples/tasks/pmos-loaded-common-source-onboarding-tune.bridge.json `
  examples/measurements/pmos-loaded-cs.json `
  --output <new-measurement-report.json> --task-output <new-task.json>
.\.venv\Scripts\vda.exe plan <new-task.json>
```

输出拒绝覆盖已有文件。疑点未清时 `needs_confirmation`，定义不足时 `needs_definition`，
均不生成 task；省略 `--task-output` 可保存诊断。输入标明的 `user_input`/`software_inference`
不变成 EDA 证据。编译结果仍关闭 `allow_remote_compute`、`allow_remote_write` 和
`replace_existing`；重新允许副作用会改变计划，不能沿用旧 token。

`signals` 是严格绑定，不是自然语言备注：

| 测量 | signals 格式 |
| --- | --- |
| 自定义节点 DC 电压 | `V(positive,negative)`，必须匹配声明的电压表达式 |
| 自定义源电流 | `I(source_name)`，保留 Spectre 的电流符号 |
| 自定义 OP 标量 | `OP(instance,quantity)`，匹配实际实例与 OP 名 |
| AC / noise 派生指标 | transfer 的输入、输出两个电压表达式 |
| transient 派生指标 | transfer 的两个表达式和 `I(power_source)` |
| 环路 STB 指标 | `loopGain(probe_source)` |

已知指标检查 quantity 与 unit。`gain_bandwidth_product_hz` 是低频线性增益×3 dB 带宽，
`unity_gain_frequency_hz` 才是传递函数单位幅度交点，不能互换；普通 AC 的
`phase_at_unity_gain_deg` 不是 PM。指标完整性仍由既有 extractor/executor 验证，
一条定义或一个成功退出码都不能证明已经测到。

`required=true` 即使没有附加数值门槛也要实际返回有限标量；缺失时 worker 与 executor
均置为 incomplete，共享网表阶段批次不再继续后面的昂贵 analysis。PVT 的每个点独立检查，
不能由某一个点或旧轮结果补齐。`required=false` 的可选测量允许保留缺失及诊断。

测量 context SHA 绑定 PDK profile 的本地配置 SHA、目标、testbench、拓扑备选、sweep 与条件；改变这些
内容需重新编译和审阅。拓扑备选必须保留所声明的测量/探针绑定。具体搜索尺寸仍由原
instance permissions/candidates/topology delta 约束；这不是无条件重用旧测量结果。

## 2. 可选温度、电源范围

仅在 request 中增加 `operating_range` 才展开，不给日常 nominal 任务自动附加 PVT：

```json
{
  "process_corner": "tt",
  "temperature_c": {"minimum": -40, "nominal": 25, "maximum": 125},
  "supply_v": {"minimum": 0.81, "nominal": 0.9, "maximum": 0.99}
}
```

这是 N28 电源范围的**语法例子，不是已批准安全工作范围或新性能证据**。真实数值按当前
PDK/模型和任务确定。两个轴各 3 点交叉共 9 点；相同值去重。单轴检查必须提供另一轴的
`nominal_temperature_c` 或 `nominal_supply_v`；不固化 27°C/1.8V。有矛盾标称声明时拒绝。
已有显式 operating_conditions 不会被覆盖。generic 需声明 `operating_condition_supply_source`。

主任务和 winner-only conditions 均可容纳 9 点，最多显式 32 点；不自动增加 process corners。
昂贵范围复核可直接放进已有 `winner_verification`，不必对所有候选重复执行。报告只称
有限采样最坏值，不称连续范围保证；成本估计只覆盖主候选×条件×analysis 上界，另列
winner、OA/回读、重试与 agent 思考不在该数字内。

## 3. 稳定性：显式 STB，不从 AC 冒充 PM

`analysis: "stability"` 可单独运行、作为 analysis stage、或只放到 winner verification。
需要 `stability_sweep` 和 `generic_simulation.stability`；后者例如：

```json
{
  "probe_source": "VLOOP",
  "feedback_configuration": "由用户确认的单端闭环及探针正负方向说明",
  "loop_scope": "single_loop",
  "raw_loop_gain_convention": "return_ratio",
  "open_loop_rhp_poles": 0,
  "assumptions_source": "user_input",
  "reference_phase_tolerance_deg": 30
}
```

探针必须是 testbench sources 中两个不同且非地节点间的 0 V voltage source；VDA 不会
自行断开、重连 OA 内部环路。上述只是契约示例，不证明一个未知放大器具有正确反馈。
反馈配置、加载、共模、探针位置与符号都必须先明确；多环或内部节点不可达时需设计具体
接入，不猜 API、不到内部乱插探针。普通开环 AC 与闭环 STB 常需不同 testbench/任务，
不能为了拼成一个 stage 列表混淆两种测量配置。

现有 `si` 输出仍是设计真源，typed wrapper 加入 `stb stb ... probe=VLOOP`；继续使用
Bridge 的 `SpectreSimulator.run_simulation` 和 `spectre.psf.read_psf_ascii`，没有新增
Bridge 公共 API。Spectre 支持电压源环路探针的语法见
[Cadence 官方说明](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/52516/spectre-stability-analysis-with-active-device-as-probe/1385877)。

VDA 要求下载的 `stb.stb` 唯一，读取非空、有限、频率严格递增的 `freq/loopGain`；核对
sweep 两端并绑定原始文件大小/SHA。按显式 `return_ratio`（原值）或 `feedback_transfer`
（取负号）归一到 `1+T`，相位展开后做 log-frequency 交点插值。

- 起始相位不匹配、频段未包围 unity、多交点、触点或平台：不输出貌似有效的 PM。
- 单一有效交点可得负 PM；这是有测量证据的失败，不是空结果。
- 未覆盖唯一 -180° 交点时 GM 保留缺失，不伪造无穷值；若任务要求 GM，既有完整性门会阻止通过。
- 原向量和交点诊断保留。单环、无开环 RHP 极点属于显式输入假设，不是程序已独立证明。
  `assumptions_source` 必填：用户实际声明为 `user_input`；agent 根据理论推断则为
  `software_inference`，不能伪装成人工已确认。
  局部 PM/GM 不能证明任意多环全局稳定，见
  [Cadence 关于探针选择的讨论](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/65080/stb-analysis-breakpoint-selection-and-nyquist-plot-support-in-cadence/1406021)。

**2026-10-23 已在既有 N28/5X2Y2Z 差分对完成一次不写 OA 的 DC＋STB 原生对照。**
实际 `stb.stb`/`stb.margin.stb`、外部反馈探针与 `feedback_transfer` 符号约定已在这个
单环工作点核对：PM 差 0.00758°，环路 unity 频率相对差约 0.00896%；GM 无有限值时保留缺失。
见[真实验收与边界](validation/2026-10-23-n28-stb-native-acceptance.md)。
该次真实执行使用受控辅助脚本。随后已将持久留存、原生对照、`stability-audit` 和复盘保护
接入正式 STB 路径，并以同一份旧文件完成离线回归；见[证据工作流](stability-evidence-workflow.md)。
修改后的正式 worker 尚未新增远端全链验收，也不是 ADE GUI 的 state 保存/Load/运行验收。其他 PDK/条件、
自动寻找环路、差分共模稳定性或多环 Nyquist 证明仍未覆盖。不绕过 ADE L 人工调节，
也不宣称新增了正式 ADE L API。

## 4. 跨轮次控制：旧指标只比较，不补齐本轮

`IterationRequest` 指定完整规格的阶段策略、已有 rounds 和停止预算。使用已有记录的
完整例子：[`examples/iteration/pmos-loaded-cs-history.json`](../examples/iteration/pmos-loaded-cs-history.json)。
路径相对 request 文件解析，不假设当前工作目录；若没有本地历史 artifacts，需换成实际
保存的 task/run，不补造结果或为了示例重跑电路。

```powershell
.\.venv\Scripts\vda.exe design-iterate examples/iteration/pmos-loaded-cs-history.json `
  --output <new-iteration.json> --markdown <new-iteration.md>
.\.venv\Scripts\vda.exe design-next <latest-task.json> <latest-run.json> `
  --iteration <iteration-request.json> --output <new-next.json>
```

request 的每个 round 含 `task`、`run`，可有 `checkpoint`、`binding_audit` 和
`candidate_index`。有 selection 时复核唯一候选，无 selection 的多候选记录必须指定 index。
每轮按原 task/plan 或既有 exact-byte audit 验证；文件变动、重复 run、乱序或规格/objective
改变会拒绝。阶段策略必须恰好保护每条约束，不重复分配。

所有阶段从**本轮相应 analysis 的实际 metrics**重新判定，不合并旧轮缺失指标。变化分为
PDK、target、topology、parameters、testbench、conditions 和 analysis/验收定义指纹。
历史记录没有当前 PDK 模型文件、完整 topology 或实时 OA 时明确保留未验证维度。
不同上下文的指标只能对照；换 PDK/testbench/条件/验收定义后不沿用旧合格锚点。

本地 profile 配置 hash 不等于远端模型文件内容 hash；同名 profile 下的远端模型版本漂移
仍须在真实执行时通过实际 model/netlist/manifest 核对。本地复盘不能证明当前模型未变化。
跨条件最坏值保留 `software_inference`；只有逐点身份、完整性和 `eda_result` 来源均可追溯，
才接受它作为测量派生结果，不把聚合标签强改为原始 EDA。理论预测不能依此混入达标判定。

| 状态 | 下一步 |
| --- | --- |
| `refine_stage` / `refine_objective` | 复用既有 OP/MNA/经过留出的局部响应模型；不随机猜尺寸 |
| `regressed` | 前面已通过的 stage 退化，生成只读 inspect 草案，先核对再讨论恢复 |
| `revalidation_required` / `winner_verification_required` | 补齐本轮缺失的 analysis 或 winner 门，不拿旧指标凑数 |
| `evidence_source_unverified` | 所需数值缺实测来源或逐条件 lineage，不能形成可行锚点 |
| `reconcile_execution` | 故障、结果不完整或 checkpoint 未决写入：只生成 inspect，不重放 |
| `specifications_satisfied` | 默认停止；不宣称全局最优 |
| `budget_exhausted` / `stagnated` | 停止自动续提案；不等于电路物理不可行 |
| `software_only` | 不产生实际性能可行锚点 |

默认 `continue_after_feasible=false`。`max_rounds` 必须明确；可选 `max_action_seconds` 只
统计记录里的 action 时长，不算 agent 思考。`stagnation_rounds` 与
`minimum_objective_improvement` 依据同 objective 的实际数值、原单位判断，不捏造收益。

最佳可行锚点绑定具体 run/candidate、参数、topology 与 testbench identity；仍是历史点，
不证明今天的 OA 已恢复。**不自动执行 inverse patch、参数回写或 checkpoint resume。**
已有 executor 的恢复机制继续负责真实任务内恢复；跨轮次恢复前需 fresh readback 与明确计划。

可加 `--task-template <next-template.json> --task-output <new-task.json>`：复用普通任务结构，
保持所有约束/objective、完整阶段门、参数/拓扑编辑范围、library/prefix 白名单；不创建或
覆盖 cellview，并关闭所有远端副作用许可。模板缺测量设置时返回 `draft_issues`，不生成
假任务。这个编译器不替理论模块生成新数值；`design-next` 仍复用既有 OP 重线性化，
未支持的 raw-CDF 灵敏度或拓扑数值提案继续如实报告缺口。

## 5. 后续真实验收的最小范围

不是要求新跑一组电路。等实际设计需要稳定性时，先确定用户模块的 PDK、完整
library/cell/view、闭环连接、模型、负载、探针及符号，列出是否 OA 写入、计算次数、
`/data/xum` scratch、覆盖风险、参数占位和恢复边界，再请用户明确授权。
已完成一次既有 N28 单环的只读 OA→si→STB 和原生 Spectre 标量对照，不为相同证明再跑一批。
后续在正常设计需求中验证正式集成和原生 ADE GUI 交接，核对原始 netlist/PSF/hash；
若现有 OA 无外露反馈端子，先提出最小非覆盖接入方案，不能静默改原设计。

跨轮次控制的真实验收随后结合正常设计中的必要修改完成：一轮先过前级、一轮受控优化、
前级回归检查及停止决策；复用已有 checkpoint/winner 证据，不另开全域、PVT 或随机拓扑。
新的真实门未通过前，保持本地实现/历史重放与新电路性能结论分开。
