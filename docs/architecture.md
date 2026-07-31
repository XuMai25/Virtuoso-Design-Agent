# 架构

## 核心分工

```text
Codex / 人类
  |  目标、约束、审查、批准
  v
VDA task contract + planner
  |  稳定步骤、副作用、token
  v
bounded executor + local checkpoint
  |  候选预算、OA 暂存/恢复、判规格、证据、续跑
  v
adapter port
  +-- demo adapter       (software_inference)
  +-- Bridge subprocess  (bridge_readback / eda_result)
          |
          v
   virtuoso-bridge-lite
          |
          +-- Virtuoso SKILL / OA
          +-- ADE Explorer/Assembler Maestro setup / history
          +-- si batch netlisting
          +-- Spectre
          +-- SSH / file transfer
```

VDA 不嵌入一个新的通用 LLM。Codex 负责开放式推理，VDA 负责把高风险 EDA 动作收敛为受控命令和机器可核验结果。

## 为什么使用独立 worker

`virtuoso-bridge-lite` 已有自己的 Python 虚拟环境和本机配置。主程序通过该环境中的 Python 启动 JSON worker，而不是把 Bridge 源码复制进来或强制安装成本项目依赖。这样可以保持 Bridge 独立升级、避免依赖漂移，并把跨环境协议缩小到版本化 JSON action。

## PDK 选择边界

VDA 默认从晶圆厂 CMOS PDK 出发。任务和 CLI doctor 共用 `DEFAULT_PDK_PROFILE=nics4304_tsmc28`，其当前工艺身份是 TSMC N28/`tsmcN28`，默认器件为 `nch_lvt_mac/pch_lvt_mac`。profile loader 支持显式单继承，避免同一工艺的 device-flavor profile 复制 model/corner/远端路径；`nics4304_tsmc28_svt` 只覆盖为 `nch_mac/pch_mac`。继承不继承性能证据：当前只对 NMOS 共源完成 LVT/SVT OA/`si`/Spectre round-trip，PMOS 与其他电路仍需独立 smoke。未来 TSMC、SMIC 等工艺各用独立 profile 绑定器件库、model、默认电压和远端路径，并单独通过 smoke；profile 之间不共享性能结论。TSV、hybrid-bonding 等封装/3D PDK 不参与默认选择或 fallback，必须由任务显式指定并使用专门 Gate。见[决策 0002](decisions/0002-foundry-cmos-pdk-default.md)。

## 任务与局部能力

任务不是固定的“全流程按钮”。`operation` 决定实际范围：

| operation | 作用 | 远端副作用 |
| --- | --- | --- |
| `device.characterize` | 在声明的 foundry PDK/model section、温度、合法几何和有限偏置域内运行独立 NMOS/PMOS OP 表征，保存 raw manifest/hash 并审计留出点；没有 OA target | scratch/计算，不写 OA |
| `schematic.create` | 建图并结构回读 | OA 写入 |
| `schematic.inspect` | 读取拓扑、参数、pins | 只读 |
| `schematic.symbol.generate` | 从精确绑定的 existing schematic 非覆盖生成 sibling symbol，恢复 session 设置并独立回读 terminals/bBox | OA symbol 写入 |
| `schematic.transform` | 对已知拓扑应用可审计的小变更；除共源源极退化、反相器 core→ADE testbench、差分对真实尾管、对称源极退化与 PMOS 电流镜负载专用变换外，`existing_schematic` 可执行一份带完整结构指纹的预声明 topology-delta | OA 写入 |
| `parameters.apply` | 应用指定参数并回读 | OA 写入 |
| `parameters.binding.discover` | 对 existing schematic 的一个已授权 CDF 字段执行 baseline→probe→restore 三次 `si` netlisting；只有唯一、字面等价且完整恢复的因果变化才提升为 OA→`si` binding | 临时 OA 写入 + `si` 计算；不跑 Spectre |
| `ade.prepare` | 为已有 design 新建持久化 Spectre-backed Maestro view/test；拒绝已有 view | Maestro OA 写入 |
| `ade.capture` | 捕获人工聚焦并已保存的 Maestro setup、history 和已有真实结果 | 远端只读 + 本地证据写入 |
| `ade.run` | 在独立后台 session 运行或恢复已保存的 Maestro setup，读取逐点 output/spec、exact-history result/log 与唯一 runtime input 哈希；可要求 OA→Spectre 输入束一致性、严格 sweep 绑定，并把表达式已固定的 scalar output 显式映射到 VDA constraints/objective；未映射 legacy output 的已知 calculator error 只能按 exact point 显式声明 | 远端计算，不写 OA/setup |
| `ade.corners.apply` | 以 exact tests/corner 旧顺序为前置条件 add-only 追加命名 enabled corner，保存后独立重开回读 | Maestro setup 写入 |
| `ade.variables.apply` | 以 tests、可选 enabled corners、逐 scope 旧值和可选 global-selection 状态为前置条件修改 global/test/corner Maestro 变量或 selection，保存后独立重开回读 | Maestro setup 写入 |
| `ade.setup.apply` | 对声明 analysis 做旧状态 CAS，并新增不存在的命名 output/spec；一次保存后独立重开回读 | Maestro setup 写入 |
| `simulation.run` | 单点仿真并判规格 | scratch/计算 |
| `design.tune` | 有限搜索；设计参数提交 OA，纯 testbench 条件只记录选择 | 计算；按维度决定是否写 OA |
| `design.close_loop` | 固定模板完整闭环；或在已有 schematic 上比较一份预声明可逆局部 delta 与同一有限参数域，原子提交 topology+parameters | 计算 + OA 写入 |

因此上层 agent 可以只要求“建原理图”“把这组参数应用进去”“微调 ADE analysis/output”“接收人工 ADE 结果”“后台运行已有 ADE setup”或“只跑仿真”，无需伪装成完整设计任务。

## 用户拓扑驱动的设计上下文

L5B 的输入不是要求 VDA 从空白发明一张复杂电路。用户可以提供已有 OA schematic
或大致拓扑，再用可选 `design_context` 声明：输入/输出/供电/偏置/器件等角色，冻结的
instance/net/pin，可固定或搜索的 semantic/CDF 参数，允许触及的局部 topology-delta
operation 和对象名，以及整个设计需要的 analysis 与 metric。角色可以来自 `user_input`，
也可以由 Codex 推断后明确标为 `software_inference`；两者都进入上下文 SHA-256 和 plan
token，不能在执行中静默改变。

planner 把 `design.context.bind` 放在 OA 写入或仿真之前；带上下文的 close-loop 强制
`create_if_missing=false`，先读取用户已有 schematic，不能在绑定前创建模板。executor 将完整 OA inspect
规范化为已有 canonical topology graph，核对角色对象、声明的端子到 net 连接、冻结对象、
实际存在的 CDF 字段和可选 topology fingerprint；审计结果记录为 `software_inference`，
原始 OA 仍是 `bridge_readback`。带上下文的参数任务只能触及声明为 `fixed`/`search` 的字段；
带上下文的通用 topology-delta 还必须同时满足 operation 种类、mutable object 和 operation
数量上限，且 `schematic.transform` 必须提供这个显式 delta，不能借旧专用 transform 绕过
审计。没有 `design_context` 的独立 `parameters.apply` 继续保留 Bridge 原有参数能力，
因此新约束不会反向收窄人工直接写入面。

`parameters.binding.discover` 补的是“实际 CDF 字段已知，但陌生 PDK/device 的 `si` 字段尚未知”
这一接入缺口，不是参数名猜测器。任务必须携带 exact topology SHA、目标字段的 `fixed` 权限、
显式 probe 值，以及目标实例未过滤的完整 CDF 表作为 compare-and-swap 前置条件。worker 先从
未修改 OA 生成 baseline `si` parameter inventory，只暂存一个 CDF 字段并完整回读 callback
后的实例表，再生成 probe netlist；无论 probe/netlisting 成败，`finally` 都尝试写回原值。重试
只接受两种状态：完整 baseline，或目标字段仍为声明 probe 的中断态；后一种必须先恢复且完整
CDF 表重新等于任务基线，其他漂移一律不写。

恢复后第三次 netlisting 的 canonical instance/model/node/parameter signature 必须与 baseline
一致。差分覆盖完整 top/scoped instance inventory，而不只看目标实例；任何其他实例参数、model、
node 或 instance set 变化都会阻止提升。只有 OA 变化集合恰为目标字段、全 inventory 的 `si` 变化
集合恰为目标实例的一个字段，且 baseline/probe 两端都与 CDF 值按 Spectre 工程单位语义相等时，
才输出可直接放入 `GenericNetlistParameterBinding` 的 `direct_literal_binding`。零变化记为
`inert`，多参数或跨实例变化记为 `ambiguous_netlist_change`，instance/model/node 变化记为
`netlist_structure_changed`，CDF callback 联动记为 `callback_coupled`，单字段非字面映射记为
`single_netlist_parameter_nonliteral`；这些状态均不会 promotion。worker 返回原始差分后，父
executor 还会用共享纯函数独立复判，结论不一致即硬失败。三份 raw netlist/log 先复制到本地 hash
manifest，再按 `_generate_oa_netlist` 返回且经 POSIX 规范化的精确 `/data/xum/.../vda_*` 路径
清理远端 scratch；不修改 Bridge，也不运行 Spectre。OA 状态是 `bridge_readback`，raw `si` 是
`eda_result`，分类是 `software_inference`，恢复/清理是 `system_event`。当前仅完成本地契约、
解析器与故障注入 Gate，尚无新的真实 OA smoke。

`vda binding-discovery-task` 把一对成功的 real-Bridge read-only inspect task/run 与一个很小的
`user_input` intent 编译成上述完整 TaskSpec。它复用 onboarding 的 task/run/token/target/PDK/
action evidence 验证，直接继承 topology、placement、冻结对象和未过滤 CDF inventory；不会按
器件类型补字段或猜 alias。输出另带 source task/run、intent、compiled task、完整 CDF 的 SHA-256
handoff，且 remote compute/write 固定关闭。flat 与显式一层 child 使用同一编译器；child 的
scope、subcircuit 和 terminal order 必须完整声明，深层 hierarchy 仍拒绝。

`vda onboarding-draft` 在首次编写上述上下文前增加一个只读编译边界。它只接受成功的
real-Bridge `existing_schematic + schematic.inspect` 任务及其 run record，并重算 plan token；
task/run SHA-256、task ID、target、PDK、topology/placement SHA-256 和未过滤实例参数表均写入
草案来源。显式一层 child 还必须与 top instance master、同 PDK/同 library、pin/terminal 集合
和唯一 child 使用关系一致。编译器把完整 CDF inventory 原样保留，但初始权限全为
`not_authorized`；所有实例、net 和 pin 默认冻结，只产生需要用户确认的角色候选，且
`generic_simulation.executable=false`。因此草案不能绕过 `design_context`、仿真授权或
OA→`si` 参数绑定，也不会把网络名启发式包装成电路真源。读取的 OA 内容仍是
`bridge_readback`，规范化、候选和 hash binding 是 `software_inference`。详见
[`existing-schematic-onboarding.md`](existing-schematic-onboarding.md)。

`vda onboarding-resolve` 是紧随其后的 intent boundary，而不是新 executor。resolution 必须以
SHA-256 精确绑定 draft，并把最终 role 标为 `user_input`。编译器证明 role 的
instance/net/pin/terminal 来自 top topology，授权字段来自完整 parameter inventory，且授权字段集合
与 OA→`si` binding 集合完全相同；typed source/load/transfer 只能使用已见 top net 或 ground。
一层 hierarchy 必须覆盖全部已 inspect child，并精确匹配 library/cell/view 与 terminal set，但
terminal order 仍由 resolution 显式给出。target、PDK、topology hash、冻结对象和 child scope 从
draft 继承，最终交给现有 `TaskSpec` 再做 analysis/metric/candidate/budget 交叉验证。现在还可编译
`design.close_loop`：resolution 提供最多三个 user-confirmed 局部 alternative、一个显式
`TopologyEditPolicy` 和可选 winner-only verification。编译器把每个 forward delta 真正应用到 draft
snapshot，再执行声明 inverse 并要求精确恢复；baseline/alternative 的 frozen 集合由各自 topology
减去 mutable scope 派生。新增实例只接受 fixed CDF update，且 permission/update/binding surface
必须相等。alternative 默认继承 baseline testbench 并只追加新增实例 binding；端口、激励或 transfer
改变时才接受完整替代 `generic_simulation`。输出安全开关固定关闭，所以编译成功只证明计划契约完整，
不产生 `eda_result` 或新的 `bridge_readback`。

winner-only analysis/metric 必须已出现在 resolution 的 required/optional intent 中；现有
`TaskSpec` 再检查 sweep、供电绑定、PVT condition、candidate OA write 和完整 topology×parameter
预算。底层直接 TaskSpec 仍允许最多七个 alternative；onboarding convenience layer 保守限制为三个，
不收窄已有低层能力。该编译纵切没有新增 controller 或 worker，详见
[`validation/2026-07-31-onboarding-refinement-resolution-local.md`](validation/2026-07-31-onboarding-refinement-resolution-local.md)。

拓扑 winner 写回后，`vda onboarding-promote` 提供第二个、仍然不执行 EDA 的 evidence boundary。
输入必须是开启过显式 compute/write 授权且 `replace_existing=false` 的真实 close-loop task、plan-bound
成功 run、以及晚于该 run 的独立只读 winner inspect。run 必须保存完整且有序的 topology×parameter
candidate 域、`domain_exhausted=true`、唯一可行 selected state 和 `schematic.inspect.final`
`bridge_readback`；fresh inspect 的 target/PDK/topology fingerprint 必须与 selected state 相同。
promotion intent 以 source-task SHA-256 绑定，并可为 baseline 与每个 alternative 预声明不同 tuning
分支。编译器只取实际 winner 分支，把该分支的完整 CDF permission、OA→`si` binding、原子候选和
winner-only verification 对 fresh inventory 重做 onboarding 校验，再输出冻结全部结构的普通
`design.tune`。新增器件参数由此只有在真实写后回读存在时才从 fixed 初值提升为 search；未知 CDF、
缺 binding、未覆盖 winner、非耗尽 run、旧 inspect 或结构漂移均拒绝。

handoff 将 source task/run、winner inspect task/run、promotion intent 和 post-readback draft 六个文件
SHA-256 写入 candidate source，并另存 compiled task SHA-256 与 plan token。draft/task/record 采用确定性
UTF-8/LF 序列化，同一输入可在中断后幂等重建；阶段一和阶段二各自沿用既有 checkpoint，因而没有
跨两个 executor 偷运半完成状态。编译任务仍固定关闭 compute/write/replace，真实第二阶段必须再次
开启权限并重新 plan。详见
[`validation/2026-07-31-onboarding-post-refinement-promotion-local.md`](validation/2026-07-31-onboarding-post-refinement-promotion-local.md)。

获得任务级远端授权后，编译结果仍须作为普通任务显式打开 compute/write 并重新 plan，不能沿用
resolution 阶段的 token。2026-07-31 首个全新拓扑 live Gate 在非覆盖创建的 PMOS 有源负载共源级上
走完 create→通用 topology-delta→inspect→draft→resolution→DC→三点 shared-netlist tune。它复用
既有 executor，把 `MP0.Wfg` 与 VBP source 条件放入同一原子候选；真实 winner 只提交 OA W/L，
testbench override 留在 run record。完整证据与边界见
[`validation/2026-07-31-pmos-loaded-common-source-onboarding-live.md`](validation/2026-07-31-pmos-loaded-common-source-onboarding-live.md)。

第二个本地纵切为 `existing_schematic simulation.run` 增加了 `generic_simulation`。它只接受
结构化独立电压/电流源、R/C 负载、单端或差分电压表达式、命名 DC/source-current/MOS OP
标量，以及显式 OA-CDF→`si` 参数映射；不接受 raw Spectre、SKILL 或 shell 文本。worker
只读重开同一 OA schematic 并重新绑定 `design_context`，复用现有 Bridge `si -batch` 与
Spectre runner；仿真前要求 flat primitive instance/model/node 集合完全一致、声明参数按
Spectre 数值语义一致。DC 和 OP 原始标量、复数 AC 波形及 simulator artifact manifest 是
`eda_result`，上下文、拓扑/参数比较和波形指标提取是 `software_inference`。

第三个本地纵切把同一契约开放给 `existing_schematic design.tune`，但不增加新的搜索器或
电路分支。通用调优接受 `instance_parameter_space`，或由 `instance_parameter_updates` 与可选
typed `testbench_overrides` 组成的原子 `candidate_set`；不接受模板专用 semantic space 或
theory seed。override 只能修改已声明 source 的数值激励或已声明 R/C load 的正有限 value，
不能改变 source/load 类型、连接、transfer、metric 或 OA topology。每个固定/搜索 raw 字段必须同时位于 `design_context` 权限和
`netlist_parameter_bindings` 中，避免“OA 写了值、但没有证明该值进入 `si` 网表”。executor
直接复用既有逐候选 OA 暂存/定向回读、同源仿真、checkpoint、预算、最佳写回和无可行恢复；
候选间要求 OA/testbench 字段集合完全相同，checkpoint 和 selection 保存完整 tuple。transport
interruption 仍记为 `system_event`，独立 OA 回读后只重试未完成候选。

第四个纵切开放受控的 `existing_schematic design.close_loop`。任务不要求 VDA 从空白
生成拓扑，而是提供一个 hash-bound 基线、一份已有通用 topology-delta 及其 exact inverse、
变体独立的 `design_context`/`generic_simulation`，以及同一组完整原子参数候选。首版只比较
基线与一个局部变体；必须声明 objective，且 `max_iterations` 必须覆盖完整 `2 × N` 域，不能
根据前缀样本提交某个拓扑。候选可来自人工、理论或 OP 局部模型；推导来源若是
`software_inference` 必须继续绑定来源 hash，controller 本身不随机生成尺寸。

executor 先穷尽基线候选，精确恢复搜索前参数，再执行 forward delta；独立回读、绑定变体
上下文后穷尽同一候选域。变体新增实例只允许声明自己的固定 CDF 值，而且必须由变体权限和
OA→`si` binding 覆盖；保留实例的搜索字段在两条路径中必须相同。每个候选使用扁平全局
index 写 checkpoint，并保存 topology variant ID/SHA。只有两条路径的全部 `eda_result`、
参数 `bridge_readback` 和 context/topology `software_inference` 都完整时才按约束与 objective
选优；完全同分保留基线。变体胜出时提交其参数，基线胜出时先 exact inverse 再提交基线
参数；全域不可行恢复原始基线。中断恢复只接受完整基线或完整变体指纹，未知/部分 topology
不会被自动覆盖。

`analysis_stages` 把通用既有 schematic 的候选评估扩展为有序的
DC/AC/transient/noise Gate。每项顶层 constraint 必须且只能归属一个 stage；非末级必须有
明确 gate，objective 必须由至少一个声明 stage 产生。executor 分级保存原始 metric、来源、
constraint 和 completeness：完整 EDA 结果违反前级约束才允许跳过后续分析，缺指标、空波形、
仿真错误或重复 metric 冲突都属于 incomplete/system evidence，不能伪装成电路不可行。
transient/noise 与 DC/AC 共用 typed `generic_simulation`，只开放声明的 source/load/transfer、
coherent amplitude sweep 与普通 noise sweep，不接受 raw design deck。

stage 执行策略是显式契约。缺省 `isolated` 保留 stage 级 checkpoint/resume，每一级独立重开
OA 并生成 `si` 网表。`shared_netlist` 则在一个候选的单个 worker 中只回读一次 OA、生成并
解析一次 `si`，再在同一 netlist path/SHA-256 上顺序运行所需 Spectre analysis；worker 可按
同一 constraint 公式提前停，但 executor 必须独立重算并核对精确 stage prefix、终止位置、
重复 metric 和共享网表证据。该模式的 checkpoint 粒度降为候选边界，中断后重跑当前候选，
不会信任半批结果。两个策略改变的是执行成本与恢复粒度，不改变 EDA 真值、候选排序或 OA
提交规则。真实三候选 Gate 将 9 个 simulation action 从 739.195 s 降为 309.555 s，全部
metric 与隔离模式逐项相同；详见
`docs/validation/2026-07-31-existing-schematic-staged-multi-analysis-live.md`。

首个真实 Gate 已在 `vb_pdk_smoke/vda_cs_cascode_gate_001/schematic` 的当前
`MN0+MNCAS+RD0` 拓扑完成只读 DC/AC。worker 与前置 inspect 都用同一套逻辑/物理 pin
canonical geometry 重新计算 context topology SHA；首次 live 暴露并修复了二次回读漏掉物理
pin 属性而产生假哈希漂移的问题。两次成功运行的 topology SHA 和 `si` netlist SHA 均相同，
9 项 CDF→netlist 映射全部匹配，17 项可对照 OP/AC 指标与旧专用 common-source/cascode
路径的最坏相对差为 `4.1e-16`。

同一 cell 随后完成真实三点 OA-write 通用调优。executor 只搜索
`MNCAS.Wfg=750.0n/1u/1.25u`；每点都先定向回读 OA，再证明 `si` 中对应 `MNCAS.w`
一致，三点保持同一 topology SHA 且各自产生完整 Spectre manifest。候选 1、2 完成后，候选
3 的 `parameters.stage` read_schematic 遇到 `WinError 10054`；executor 以
`system_event` 保留失败，从仍为上一个已确认点 `1u` 的 OA 状态恢复初始 `750.0n`，
checkpoint 指向 index 3。独立 OA/进程审计通过后，resume 没有重复前两个
Spectre 点，只执行候选 3、最终提交和 after inspect。三个点均满足声明的临时 AC 约束；由于
没有 objective，首点按声明顺序被提交，这只验证 selection 语义而不证明 AC 性能最优。任务外
只读 inspect 又确认最终 `MN0.Wfg=1u`、`MNCAS.Wfg=750.0n`、`RD0.r=20K` 与完整结构。

2026-07-31 又在新建且 non-overwrite 的
`vb_pdk_smoke/vda_existing_close_loop_gate_001/schematic` 完成拓扑+参数 live Gate。create 后先
由独立 inspect 编译 fresh exact baseline/alternative SHA，不接受模板旧 hash；同一两个
`(MN0.Wfg,RD0.r)` tuple 分别在普通共源与固定 `RS0=750 ohm` 的源退化变体上跑完四次
OA→`si`→Spectre AC。四份 topology/parameter binding、271 点波形和 manifest 均匹配，显式
GBW objective 选择 baseline 的 `Wfg=1.1u,RD=18.5K`。executor 执行 exact inverse、提交完整
胜出参数，任务外 inspect 再次确认 baseline topology SHA、无 `RS0/NSRC` 且参数一致。候选 1、
2 后的真实 `WinError 10054` 被记为 `system_event`；恢复验证 exact baseline，resume 从全局
index 3 继续且没有重跑前缀。

后续纵切不再把 nominal 搜索与昂贵质量复核绑成同一候选域。可选 `winner_verification` 拥有
独立的 stage、constraint、AC/linearity/noise sweep 和最多五个 operating condition；nominal
搜索先产生 provisional winner，executor 只暂存该点并用 `shared_netlist` 运行 winner-only Gate。
逐条件 VDD 必须通过 `operating_condition_supply_source` 绑定到一个已声明的 voltage source；每个
condition 都使用 profile 映射的 process section、温度和供电。任一条件不完整或不满足约束时，
executor 恢复搜索前完整 OA 参数并清空 selection，不会自动改选未经同一 Gate 验证的 runner-up。
2026-07-31 的真实两点任务只对 nominal GBW winner `MN0.Wfg=1u/RD0.r=5K` 运行
TT/27 ℃/0.90 V 与 SS/125 ℃/0.81 V 的 DC→AC→transient→noise；两条件均通过，最终独立
OA readback 与 selection 相同。一次 `si.env` SCP/DNS 中断被保留为 `system_event`，恢复初值并
从 candidate 1 边界续跑。

拓扑 refinement 现接受最多七个独立 alternative，同时保留旧单 alternative task 的兼容解析。
所有 delta 必须锚定同一个 baseline SHA-256，且 after 指纹互不相同；每个 alternative 各自绑定
design context、generic simulation 和新增实例的固定参数。executor 将候选域展平为
`(baseline + alternatives) × parameter candidates`，每个变体都从精确共同基线开始；在进入下一
变体前先执行当前 inverse。最终 winner 可以是 baseline、最后一个 alternative，也可以是在最后
一个变体运行完后回到更早 alternative；未知或部分 topology 指纹一律拒绝自动写。checkpoint
保存扁平 index 和 exact topology identity，本地故障测试覆盖 alternative 中断、恢复、早期与末尾
变体胜出、全不可行和共享基线拒绝。2026-07-31 的 live Gate 又在一个 non-overwrite 新 cellview 上让
common-source、固定源极退化和 cascode 三个 variant 共用两个实例参数 tuple，执行完整 6 点 staged
DC→AC。每点的 OA topology、CDF 参数、`si` primitive/node graph 和 netlist 参数均匹配；三次入口
SSH/Bridge 中断都在独立 OA 回读证明 exact baseline 或 candidate state 后从扁平 index 2/5/6 恢复，
没有重跑已完成前缀。最终 winner 为 cascode，controller 从共同 baseline 重建该 variant、写回参数并由
任务外 inspect 复核。因此 multi-alternative OA round-trip 已 live verified，但仍只证明声明的离散域，
不是自动发明拓扑或全局优化。

通用 `si` 核对还增加了一个刻意收窄的一层 hierarchy slice。任务必须逐个声明 top-level
instance、child library/cell/schematic、`si` subcircuit 名和 terminal order。worker 将顶层 call
与 OA instance/master/端子逐项比较，再用 Bridge 独立回读 child schematic，证明 child pin set、
primitive instance/model/node graph、canonical topology SHA 和完整 placement SHA 与 subckt body/任务
绑定一致。任何未绑定的非 primitive master、nested subcircuit、节点/terminal-order 漂移都会在
Spectre 前拒绝。

2026-07-31 的 live Gate 先用 `schematic.symbol.generate` 从全新 child schematic 非覆盖生成 sibling
symbol。任务绑定源 topology SHA、精确 pin 名/方向/宽度和目标 `symbol` view；worker 复用 Cadence
`schSchemToPinList`/`schPinListToSymbol`，用 `unwindProtect` 保证临时 `ssgSortPins` 在成功或失败后都
恢复，只允许清理本次新建但未通过审计的 symbol。保存后由独立 worker 重开 symbol，核对 terminal
direction/width 与非空 bBox；已有 symbol 一律拒绝，不提供静默 refresh/overwrite。

随后另一个 non-overwrite top cell 从 flat `MN0+RD0` 增量替换为同库 child symbol instance，且最终
OA→`si`→Spectre DC/AC 与 flat reference 的六项核心标量最坏相对差为 `4.09e-15`。Cadence `si`
产生的无扩展名 raw netlist 被原样保留并标为 `eda_result`；为避免 Spectre 把被 include 文件按 SPICE
解析，VDA 只生成一个确定性的 `.scs` envelope，在首行加入 `simulator lang=spectre`。raw/envelope
路径、大小、SHA-256 和变换名分开记录，envelope 生成属于 `software_inference`，不会把包装后的文件
冒充原始 `si` 输出。

同一 hierarchy binding 现已扩展出显式 `HierarchyParameterScope`。`TOP/CHILD` 参数路径必须先把
top instance、child library/cell/schematic 以及 child topology/placement SHA 全部绑定；context permission、
candidate update 与 OA-CDF→`si` parameter binding 还必须落在同一 scope。worker 把路径解析为 child
target/local instance，写前、写后和恢复时都直接读取 child CDF；generic parser 则把 subckt primitive
重新映射到 scoped path。每次 `si` 后再次回读 child topology/placement，防止 netlisting 期间漂移。
同一个可写 child 被 top 多次实例化时硬拒绝，因为修改 child OA 会影响全部引用，不能包装成单个 top
instance 的 override。2026-07-31 三点 live Gate 已证明 `XAMP/MN0.Wfg` 与 `XAMP/RD0.r` 逐点写回、
定向 OA 回读、`Wfg→w`/`r→r` 网表一致性、DC/AC 规格判定与 winner child 写回；最高 GBW 的
`1u/5K` 因 gain 不足被拒绝，最终选择 `1.1u/18.5K`。详见
[`validation/2026-07-31-existing-schematic-hierarchical-parameter-tuning-live.md`](validation/2026-07-31-existing-schematic-hierarchical-parameter-tuning-live.md)。

CDF 派生关系（例如 fingers/multiplicity 的总宽度）、更深 hierarchy、shared-child per-instance override、
并发人工 editor、mismatch/Monte Carlo 仍未闭合。现有反相器、共源和差分对 worker 继续作为真实执行
后端和回归基线，而不是未来能力边界。

`analysis` 与电路参数分离。反相器省略时解析为 `transient`，共源级和差分对省略时解析为 `dc`；AC 必须显式声明 `analysis: "ac"` 以及 `ac_sweep.start_hz/stop_hz`。差分对电源抑制使用独立的 `analysis: "psrr"`，复用 AC sweep 契约但运行差模、VDD 注入和 VSS 注入三条路径；只有该 analysis 可选声明位于 sweep 内的 `evaluation_stop_hz`，用于计算从 start 到该频率的带限最差 PSRR。固定多 analysis 质量门使用 `analysis: "quality"`，并要求 `ac_sweep`、`linearity_sweep`、`noise_sweep` 同时存在。扫频点密度、低频参考点数、参考窗变化、PSRR 评估频带、线性度窗口和噪声频带都属于任务与 plan token。这样换 analysis 或改变指标定义不会复用旧 token，也不会把默认设置伪装成 `user_input`。

`operating_conditions` 是独立于设计参数的显式可选有限验证集合。省略时 common-source `simulation.run`、`design.tune` 和 `design.close_loop` 保持原有单条件行为与旧 token；声明时每项给出唯一名称、PDK profile 已映射的 `process_corner`、温度和可选 VDD，同一任务最多五项。对调优任务，每个候选只暂存一次 OA 并生成、核对一份 `si` 网表，再为每个条件生成 AC/transient/noise wrapper；完整候选 bundle 才能写入 checkpoint。executor 保留逐条件原始指标和判定，要求全部完整且全部满足约束；maximize objective 取各条件最小值，minimize objective 取最大值。跨条件聚合是 `software_inference`，不能覆盖各条件 `eda_result`。逐条件 VDD 存在时拒绝 task/搜索空间中的 `vdd_v`；全部条件省略 VDD 时则共同继承当前候选的 VDD，避免同一个供电出现两套真源。

`schematic.transform` 不等同于重建模板。共源 transform 的 `add_source_degeneration` 把 `MN0.S: VSS -> NSRC` 并新增 `RS0(NSRC,VSS)`；remove 只删除 VDA 创建的 RS0 两条端子 stub/标签并恢复 VSS。差分对先由 `add_tail_device` 在精确 nominal core 上增加 `MNTAIL(TAIL,BIAS,VSS,VSS)` 与 `BIAS` pin；只有该真实尾管变体可以继续执行对称 `add_source_degeneration`：`MN0.S/MN1.S: TAIL -> NSP/NSN`，并新增等值 `RS0(NSP,TAIL)`、`RS1(NSN,TAIL)`。差分对 source-degeneration remove 不接受参数，只删除这四条 VDA 自有 wire/label stub 与两只电阻并恢复两管源极到 TAIL。另一条互斥路径 `replace_resistive_load_with_current_mirror` 只接受未退化的真实尾管拓扑，删除 RD0/RD1 自有 stub 后加入固定 MP0/MP1 电流镜；`restore_resistive_load` 删除 MP0/MP1 自有 stub 并按显式 `load_resistance_ohm` 恢复两只电阻。非对称器件、缺失任一支路、混合 R/PM 负载或额外连接都会拒绝。

两种 remove 都可带 `expected_restored_placement_sha256`，把 add 前 Bridge placement 回读中的实例、pin、标签和导线完整绑定进 plan token，并在保存后强制相等。反相器 testbench transform 则要求现有 cell 是 MN0/MP0 core 或已经完成同一变更；它保留 MOS/pins，只把地归一到 `gnd!` 并增加固定的 `VDD0/VIN0/CL0/GND0`。所有已有对象编辑都强制 Bridge editor append mode；preflight 拒绝未保存改动，编辑 batch 失败时只 purge 未保存缓存且不保存。前后回读必须证明未点名器件的完整参数、master、位置和顶层 pins 保持，重复调用幂等。旧的专用 transform 在保存后审计失败时仍保留失败和真实 OA 状态；下述预声明通用 topology-delta 才具备受限的 exact-state inverse 恢复，不能把它外推成所有写入的远端事务。

2026-07-26 增加了 Bridge 之上的通用 topology-delta 契约，但没有把任意 SKILL 或字符串脚本开放成图重写接口。契约可序列化八类结构操作：添加/删除实例、重连已有端子、替换实例 master、添加/删除 net、添加/删除 pin。每个删除、重连和 master 替换都携带 exact 旧状态 CAS；添加要求名称不存在且所有引用 net 已存在；删除 net 要求已无 instance terminal 或 pin 引用。实例移动、reshape、端子集合变化和同名 net 属性突变不在首版 allowlist 中。

Bridge/demo 的完整 `instances/nets/pins` 回读先被规范化并排序，再计算确定性 topology SHA-256。`parameters/params` 与顶层 parameter 表不进入结构指纹，因为 CDF 参数仍由既有写入、callback 和双重回读契约负责；master、端子连接、view、位置及其余结构属性进入指纹。另一个 placement snapshot 保存规范化后的完整实例、逻辑/物理 pin、label 和 wire：物理 pin 必须能唯一绑定到 terminal/net，并记录 pin-symbol master、坐标和方向；wire 保留完整点列，label 保留坐标、方向和文本。它单独计算 SHA-256，使“逻辑图一致但图形被移动、丢线或多出 label”不能通过恢复门。`vda topology-compile` 从一次成功的 `schematic.inspect` 与显式 operation 列表生成前向/逆向契约。对 run record，编译器只接受 `existing_schematic` inspect 返回的 canonical `details.topology`；电路专用 semantic summary 即使也含 `instances/nets/pins` 也会拒绝，确保 compile 与真实 topology writer 使用同一结构真源。编译器先在本地应用前向 operations，生成逆序 inverse operations，并证明 inverse 精确恢复 before fingerprint。验证器再把实际 after 完整结构与预期结构比较；缺实例、额外 pin、旧状态漂移、悬空 net 或未声明 placement 变化都会失败。现有每个专用 `schematic.transform` 在原有模板语义断言通过后，会追加 `schematic.transform.topology-delta.audit`；真实前后 inspect 仍是 `bridge_readback`，契约推导、哈希和逆向证明明确是 `software_inference`。

`existing_schematic + schematic.transform` 现在可以执行契约的 `forward` 或 `inverse` 方向。worker 把 `add/remove_instance`、`reconnect_terminal`、`add/remove_net`、`add/remove_pin` 和 instance-scoped `replace_master` 编译到 Bridge 现有 append editor/reader；新实例和新 pin 必须有有限 placement，master library 只能来自原结构、profile tech library、`analogLib` 或 pin 专用 `basic`，未替换实例的参数表必须逐项不变。`add_pin` 复用 Bridge 公共 `schematic_create_pin`；`remove_pin` 对逻辑 terminal/net/direction 与物理 pin-symbol master/坐标/方向做 exact CAS，并删除完整 `dbTerm` 层级，而不是只删一个 figure。net 仍由端子 label/stub 在 OA 中物化，最终以完整独立 topology/placement readback 而不是 editor return code 判定。`replace_master` 当前只接受 `symbol` view，并在写前验证目标 master 存在、端子名称/数量/方向完全一致，而且每个端子在新旧 symbol 上都恰有一个 pin/figure 且 bBox 相等；写命令再次对实例名和旧 master 做 CAS，再只修改该实例的 `master`。这里使用 Bridge 的通用 SKILL channel 和 append editor，没有给第三方 Bridge 打补丁。

每个真实 `replace_master` 还必须在同一 topology contract 中提供一条 `master_parameter_migrations`：旧值和新值都用实际 CDF 字符串逐项声明，写前对旧值做定向 readback CAS，换 master 后复用 Bridge 公共 `set_instance_params(..., param_filters=None)` 触发 callback/save，再独立逐项读取新值。forward/inverse 自动交换两组值。结构 SHA 有意仍不包含参数，但完整 task/token 包含迁移对象；`undeclared_parameter_policy` 必须显式为 `record_only`，表示未列出的 CDF 字段只保存完整前后 Bridge readback，不声称保持、可写或已迁移。该协议支持参数名映射不同的 master，但不会把少量声明包装成全 CDF 兼容。

通用 worker 在 editor batch 抛错时仍只 purge 未保存 edit。若 editor 已正常退出而参数 callback、完整结构审计、placement 审计或保留参数审计随后失败，它会再做一次独立 readback：只有当前 topology SHA 精确等于本次契约预期输出，且可读的 placement 没有出现契约外漂移，才执行相反方向的预声明 operations、恢复已声明的旧 CDF 值，并要求最终 topology SHA、全部实例参数表和完整 placement SHA 都等于初始 readback。请求本身仍返回失败，恢复证据写入错误记录；新鲜 topology/geometry readback 失败或出现任何额外/缺失结构时状态分别记为 `state_unknown_no_write` 或 `unexpected_topology_no_write`，不做第二次 OA 写入。这个恢复边界及 geometry transport-loss 分支已通过本地故障注入，尚未 live 验证。

首个 live Gate 在全新 `vda_generic_topology_delta_001` 上把普通共源级增量变成 `MN0.S→NSRC + RS0(NSRC,VSS)`，随后自动 `si`/Spectre DC，再执行 inverse；最终独立 topology SHA-256 与变换前完全相同，恢复后的普通共源 DC 也重新执行成功。第二个 live Gate 在新 `vda_master_migration_001` 上执行 `MN0: nch_lvt_mac -> nch_mac -> nch_lvt_mac`：forward 后独立回读 233 项 CDF，`si` 明确导出 `model=nch_mac`，271 点 Spectre AC 完整；inverse 后 topology、全部实例参数、LVT 网表 SHA 和核心 DC/AC 标量与 before 一致。两者证明已开放子集的真实增量写入、同源网表和显式 inverse，不等于任意图编辑事务。exact-state post-save recovery、pin 写入/删除和完整 placement 的实现目前只有本地故障注入与模拟 Bridge 证据；不同 symbol 几何迁移和并发 editor 仍未验证。

## 两层参数契约

VDA 保留两种用途不同的参数表示：

- `parameters` / `parameter_space` 是电路模板已定义的 canonical semantic parameters，例如 `device_width_um`、`load_resistance_ohm`、`bias_v` 和 AC `load_ff`。它们可参与仿真、规格判定和有限搜索，但并非都写 OA：W/L/RD/RS 是设计参数，bias/VDD/外部负载是 testbench 条件。当前 MOS width semantic 指单指宽 `Wfg`；多指 OA/`si` 一致性另外核对 `finger_width`、`fingers/nf`、`m/multi` 和总有效宽度，不能把网表 `w` 无条件当成 `Wfg`。
- `instance_parameter_updates` 是人工明确指定的实例级 CDF/OA 写入，例如 `MN0.fingers="2"`、`MN0.m="1"` 或 `RD0.r="22k"`。参数名和值按 Bridge 字符串契约原样传递，不做单位、别名或枚举推断。

`existing_schematic` 是不依赖固定拓扑模板的通用 circuit kind，开放 `schematic.inspect`、`schematic.symbol.generate`、预声明 `schematic.transform`、`parameters.apply`、`parameters.binding.discover`、typed `simulation.run`、有限 raw-instance `design.tune`、受控单-delta `design.close_loop`、`ade.prepare`、`ade.capture`、`ade.run`、`ade.corners.apply`、`ade.variables.apply` 与 `ade.setup.apply`：inspect 保留 Bridge reader 的完整结构对象、geometry、notes、nets/pins 细节和所有可回读 CDF 参数；symbol generation 只从精确绑定且不存在 symbol 的 schematic 建立人工/层级复用入口；transform 只执行上述 exact topology-delta 子集；参数操作允许人工指定任意已有实例；binding discovery 用可恢复的单字段差分 netlisting 证明陌生 CDF 的直接 `si` 映射；通用 simulation/tune/close-loop 使用 design context 与 typed testbench/metric/netlist binding；ADE 操作则为已有 design 准备新的 Maestro 人工入口、读取人工状态、后台运行一个已保存 setup，或用显式旧状态前置条件增量修改 corner、变量/selection、analysis 和新增 output/spec，不要求 VDA 理解 DUT 拓扑。反相器和共源模板也能使用相同原始参数与 ADE 交接路径，并可在一个参数任务中组合 semantic parameters 与原始实例参数；semantic 写入先执行，原始 CDF callback 后执行，最终 OA 必须同时满足所有已声明 semantic 值和原始字段值。

执行路径先结构化回读目标 schematic 并确认实例存在，再复用 Bridge 的 `set_instance_params(..., param_filters=None)` 触发 CDF callback、`schCheck` 和 `dbSave`。通用 reader 为控制输出会省略空值和超长值，因此 VDA 不用摘要缺失来限制 Bridge：写入后另发只读 SKILL，直接打开目标 OA、定位实例 CDF，并逐字段比较真实 `p~>value` 与请求字符串；executor 的 `schematic.inspect.after` 再独立执行一次同样的定向读取。首次值不一致时，worker 至多按任务声明顺序逐字段重放一次；计划必须披露该副作用，最终仍不一致则整个 run 失败。

Bridge 0.7.0 的公共 `set_instance_params` 通过 `geGetEditCellView()` 选择写入目标，而公共 `open_window` 在已有窗口时只执行 `hiRaiseWindow`，不会保证该窗口成为当前 edit window。2026-07-22 的 L/VDD 首轮因此在另一个已打开的反相器窗口中错误解析实例，并以 `RD0` 不存在停止；没有把该系统错误记作电路不可行。VDA 的兼容层现在先调用 Bridge `open_window`，再用同一 SKILL channel 执行 `hiSetCurrentWindow`，立即核对当前 library/cell/view 完全匹配，最后仍调用 Bridge 原公共参数写入函数。这里没有复制 CDF callback、`schCheck`、保存或 transport，也没有修改第三方 Bridge 仓库；它是 Bridge 0.7.0 的显式兼容依赖，升级 Bridge 后必须回归目标聚焦测试。

定向读取的字段名来自 Bridge 写入函数返回的实际应用映射，而不是 VDA 复制的别名表。因此 Bridge 公开的 `wf -> Wfg`、`nf -> fingers` 等简写仍可使用；run record 同时保存原始请求和 Bridge 报告的实际 CDF 目标。

CDF 的 `display` 和 `editable` 元数据不是写入 allowlist。2026-07-20 的真实 smoke 中，`MN0.m` 为 `editable=nil` 且 callback 后不能保持 `2`，但 `RD0.r` 同样报告 `editable=nil` 却能成功持久化为 `22K`。因此 VDA 不依据 UI 元数据缩窄 Bridge 能力，最终权威只来自 callback 后目标 OA 值；失败仍可能留下部分写入，因为 Bridge 的多实例调用不是 OA 事务。

任务请求及原始值标为 `user_input`；真实 OA 确认标为 `bridge_readback`；demo 只能产生 `software_inference`。完整 inspect 会保留 callback 导致的旁路参数变化，但 VDA 只对任务显式列出的字段宣称确认。`instance_parameter_updates` 不会隐式进入搜索；调优必须用 `instance_parameter_space` 逐维声明 exact instance、未过滤 OA inspect 中真实存在的 CDF 字段名和有限原始字符串集合。executor 将固定实例字段、raw sweep 与 semantic space 组合成一个受 `max_iterations` 截断的确定性笛卡尔积，不把所有 CDF 自动扩成搜索空间。每个 candidate 只把具体点交给 Bridge，要求请求、实际应用映射、立即定向回读一致；candidate/checkpoint 分别保存原始字段与 canonical OA semantic 状态，最佳值或初始值写回后再独立定向回读。独立 `parameters.apply` 继续保留 Bridge 的别名和更广字符串能力；有限搜索为保证初始值恢复而只接受可在完整 readback 中精确定位的实际字段名。

`candidate_set` 是与上述逐维 space 正交的通用原子候选域，只用于 `design.tune`/`design.close_loop`。每个候选同时携带一个完整的 semantic/testbench 参数表和可选的原始实例参数表；所有 tuple 必须声明相同字段面、ID 和参数值组合必须唯一，固定字段不能与候选字段重叠。它与 `parameter_space`、`instance_parameter_space`、专用 `theory_seed` 互斥，因此 executor 按声明顺序逐 tuple 执行，不会把 `W/bias/load/CDF` 再展开成笛卡尔积。同一实例上的固定 raw 字段与候选 raw 字段按字段深合并，既不会丢掉人工固定的 `m`，也不会限制候选调整 `fingers`。对 `existing_schematic design.tune`，候选只能携带 raw instance 字段；这是为了保持拓扑无关，不为未知电路发明 semantic alias。独立 `parameters.apply` 和既有逐维搜索均保持原能力面。

原子候选来源只能是 `user_input` 或 `software_inference`；后者至少绑定一个 SHA-256。候选 ID、预测指标、来源和 hash 进入 task、checkpoint、candidate record 与 `search_audit`，恢复时逐项复核。预测值始终保留为候选来源证据，不能参与最终覆盖：可行性、规格和最终排序仍只使用本次 adapter 返回的真实仿真指标。demo adapter 即使跑通也只能产生 `software_inference`，不能把候选生成器升级成 EDA 证据。

有限搜索的结果不能简称为“最优解”。所有 tuning run record 都带 `search_audit`：分别记录声明、尝试和分析完整的候选数，只有全部声明点都完整完成时才允许 `best_in_declared_discrete_domain`；预算截断、transport 失败或缺指标只能是 `best_evaluated`。即使声明离散域穷尽，`continuous_optimum_claim` 和 `global_optimum_claim` 仍固定为 false；全不可行也只证明声明离散域内没有可行点。

## 独立 MOS 器件表征

`device.characterize` 是远端 task operation，但不是 OA operation。其 `TaskSpec` 必须使用
`circuit: mos_device`、省略 target，并只声明 polarity、W、可选的逐 polarity Spectre
实例参数签名、有限 L/VGS/VDS/VSB 网格、留出点、温度、limits 和 remote-compute
safety。任何 OA target、remote-write 权限、analysis、ADE 状态、参数写入或设计搜索字段
都会拒绝；其他 operation 仍强制要求 target。实例参数签名最多 64 项，只接受标识符和
纯 numeric Spectre literal；`w/l/nf/m/multi` 由契约单独控制，不能借签名覆盖或注入 deck。

worker 复用 Bridge 已运行的默认 SSH 连接和公开 `SpectreSimulator`，但 PDK profile 仍由
VDA 独立选择 model include/section；两者不能混成同一个 profile 名。每次运行先确认唯一
`/data/xum/.../vda_mos_characterization_<task>_<nonce>` 根不存在，随后由 Bridge 在根下
上传 deck、运行 Spectre、下载 PSF/log 并保留远端文件。VDA 没有复制 SSH、Cadence 环境
初始化、Spectre runner 或传输，也没有修改第三方 Bridge。

同一个 DC deck 为每个 MOS 点配置独立 D/G/B 理想源，直接保存 signed
`ids/vgs/vds/vbs/vdsat/gm/gds/gmb/cgs/cgd/cgb/cdb/csb`。worker 只返回原始 OP 与输入/结果/log 的大小和
SHA-256，action 标为 `eda_result`；executor 另行检查 NMOS/PMOS bias/IDS 符号、有限性、
点身份和 manifest 指纹，再生成 width-normalized `MosCharacterizationArtifact` 和多线性
留出审计，标为 `software_inference`。raw 成功而留出门失败时 run 为 `partial`，空 OP、
NaN、偏置/符号漂移、清单缺失或 transport 中断为 `failed/system_event`，不能变成“器件
不可行”。

首个 live Gate 在 TSMC N28 `top_tt`/27 ℃、W=1 µm、L=30/60 nm、240 个训练点和
4 个真实 VGS 留出点上通过，最坏归一化误差为 13.70%，门为 25%。输出 artifact 与通用
small-signal schema 相同，能直接被本地矩阵核心消费。Gate 7B 又证明 W 相同仍不足以
定义同一物理器件：OA/`si` 中的扩散几何和 LDE 参数会显著改变 gds。artifact 因而保留
逐 polarity 参数签名，下游可要求它与 `si` 实例除 `w/l/nf/m/multi` 外的参数集合和值
完全一致。artifact 仍嵌在 run record，没有引入 registry/database；PVT、不同 finger/
multiplicity 参数面、多维边缘留出仍需按任务显式表征，不能授权理论值直接写 OA。

Gate 7C 增加 `vda characterization-task-from-run`，解决跨 cell/几何迁移时人工复制
扩散/LDE 参数的问题。它只接受成功的 real Bridge read-only OA + compute run，从选定
operating condition 的结构化 `si` 实例提取 model、exact W/L 和完整 numeric parameter
signature，再与单独的用户审查 bias-grid template 合成普通 `device.characterize` task。
生成任务内保留 source run、task/action、netlist、instance、PVT、topology、model/W/L 和
signature 的 SHA-256 binding；来源实例是 `eda_result`，任务推导是
`software_inference`。当前自动路径保守限制 `nf=1、multiplicity=1`，多指/并联器件必须
另立证据，不按线性宽度缩放猜测。下游复用按 model/W/L/signature 匹配，不要求其他
cell 沿用来源实例名；来源名只用于追溯。

## 轻量 standalone 网表预评估

`circuit: netlist_preview` 是理论分析与 OA 同源 Gate 之间的低成本 EDA 层。它仍使用
`simulation.run`、计划 token 和 `allow_remote_compute`，但不接受 OA target、remote-write、
ADE、semantic/raw 参数写入或搜索字段。输入是一份受校验的 MOS/R/C/独立电压源实例图：
每个器件显式给出端子、W/L/fingers/multiplicity 或无源值；多个变体共享同一组激励、
供电和可选负载。MOS 附加模型参数只允许 numeric Spectre literal，不能覆盖
`w/l/nf/m/multi`；接口不接受任意 raw Spectre 文本。

executor 对该 circuit 使用 `bridge.spectre.probe`，不会调用 Virtuoso SKILL 或
`schematic.inspect`。worker 复用 Bridge 已有 SSH client 和 `SpectreSimulator`，从 VDA
PDK profile 取得 foundry model include/section，在唯一
`/data/xum/.../vda_netlist_preview_<task>_<nonce>/<variant>` 下为每个变体生成并运行一份
deterministic deck。每份运行都经过现有远端 timeout/TERM/KILL guard、下载原始 DC/OP/AC
和日志、记录逐文件及聚合 SHA-256；worker 的 SSH 资源仍由统一 `finally` 关闭，Windows
worker 继续受 Job Object 与 cancel/watchdog 清理，不新增 PowerShell 执行层或第三方
Bridge 补丁。远端 evidence root 有意保留并可由 `vda resources --remote` 盘点，不属于
进程泄漏。

DC 会核对所有声明电压源的实际节点差，提取逐 MOS 的 Id/VGS/VDS/VBS/VDSAT/gm/gds/gmb、
饱和余量、真实供电电流/功耗和面积代理。AC 直接从复数输入/输出节点表达式提取低频增益、
相位、首个 −3 dB bandwidth、GBW 和 unity；空波形、非平坦参考窗或未包围 bandwidth
保持失败/不完整。每个变体的 simulator metrics 是 `eda_result`；工作区布尔值、面积代理
和跨变体 delta/ratio 是 `software_inference`；可选来源 hash 是 `user_input`。结果明确
记录 `oa_access=false`、`si_netlisting=false`、`maestro_access=false`，因此不能被包装成
schematic-driven 或 design closure。

`vda preview-task-from-candidates` 是该层之前的纯本地确定性编译器，不是第二个优化器。
它直接消费既有 `AtomicCandidateSet` 或 `TheorySeedCandidateSet`，要求 policy 明列候选 ID
及顺序、来源 generator/source ID、可选 PDK、结构占位 variant、固定参数和 typed field
bindings。一个 semantic 参数可以映射到多个不同器件字段，但所有候选参数必须恰好被
映射或声明为固定值；同一目标字段重复写、固定值跨候选漂移、未映射字段、raw CDF
`instance_parameter_updates`、缺失器件或来源漂移都会拒绝。编译器不综合拓扑、不读取
预测值决定排名，也不静默截断候选；当前结构化任务最多 16 个 variant。

编译时只克隆 policy 指定的占位 variant，其他基线 variant 原位保留；引用占位 variant
的 constraints 逐候选展开，引用它的单一 objective 因语义含糊而拒绝。输出绑定原候选
文件、compile policy 和 task template 的 SHA-256，并保存 `variant_source_ids`。这些 hash
计算和候选到 variant 的映射标为 `software_inference`；后续真正运行得到的 OP/AC 仍只
标为 `eda_result`。因此 Agent 可以为新拓扑提供一个受校验结构模板和少量候选，而不必
复制 deck 或 OA 操作；最终胜出结构仍要通过 OA→`si` 或人工 ADE Gate。

`vda preview-select` 是 retrospective 校准用的独立本地后处理 Gate，不修改 task、run
record、OA 或远端产物。policy 必须绑定编译任务、preview run、参考 OA→`si` run、PDK、
candidate generator/source
和候选源文件 SHA-256，并声明粗约束、objective、top-k 及最低排序/可行性门。validator
重新从 typed graph 和当前 PDK profile 渲染每份 deck，要求其 SHA 同 run 和 manifest 中
唯一 `.scs` 项一致；还逐项复核 manifest 聚合 hash、非空 AC sample、bounded process、
`/data/xum` 路径、无 OA probe、variant→candidate ID、参考域穷尽和参考 winner。hash、
身份、波形或证据来源漂移属于证据损坏并硬拒绝；完整证据下的低相关性、漏掉真值 winner
或空 shortlist 属于筛选器失效，结果保留为 `partial`。排序和误差比较是
`software_inference`，两侧 simulator 标量仍分别是 `eda_result`。结果只称
`best_in_declared_discrete_domain`，连续和全局最优声明固定为 false。

未见候选域使用严格分离的两阶段契约。`vda preview-shortlist` 接受
`ProspectivePreviewPolicy`、preview task/run 和一份尚未执行的完整 OA reference task；
接口故意不接受 reference run。policy 预先绑定未来 reference task 的 canonical SHA-256、
粗约束、objective、top-k 和 utility 阈值。命令完成与 retrospective validator 相同的
preview task/run、逐 deck、manifest、非空波形和 candidate identity 审计，然后保存
`frozen_at`、policy/task/run hashes、reference task hash 和固定 shortlist。冻结文件属于
`software_inference`，不会因为后来真值结果而改变。

`vda preview-shortlist-audit` 才接受后来完成的 OA→`si` reference run。它先逐字段复核
policy 与冻结 shortlist，要求 reference run 的 `started_at > frozen_at`，且 reference
task ID/SHA、plan token、candidate generator/source/hash、完整候选顺序、PDK 和 analysis
都与冻结时预注册内容一致；随后复用同一 reference evidence validator 检查域穷尽、真值
winner、可行性、排序和逐指标误差。任何 shortlist 漂移、用旧真值 run 伪装 prospective、
换任务或未穷尽域都会硬拒绝；证据完整但 utility 阈值失败仍返回 `partial`。这样把“先选
名单”与“后看真值”变成机器可验证的时间与哈希边界，而不是文档约定。

`vda oa-task-from-preview-shortlist` 只负责把已经通过 retrospective Gate 的 selection，或
已经冻结但尚未读取真值的 prospective shortlist，编译回普通 `candidate_set` 任务，不
增加第二套 OA 或仿真执行器。它要求 selection/shortlist 与完整 OA task 的
PDK、analysis、candidate generator/source、源 hash、完整候选顺序和 variant identity
一致，再逐字节绑定两份输入文件；target、constraints、objective、固定参数、安全策略和
候选 tuple 原样保留，`max_iterations` 精确缩到 shortlist 大小。输出任务必须重新 plan，
不会继承 preview 的 token 或授权；prospective 输出还保存 frozen shortlist SHA-256。
当前 common-source cascode 语义会推导或核对
`expected_target_topology_variant=cascode_common_source`；该通用任务前置条件也可显式用于
其他 `simulation.run`/tuning 任务。executor 在 candidate stage 或 simulation 之前用
`schematic.inspect` 的 `bridge_readback` 比较拓扑，不匹配时不尝试候选 OA 写入。拓扑
修复仍须走独立、显式获授权的 topology-delta，不能由 handoff 静默完成。

推荐顺序是：理论/KCL/gm-Id 先缩小结构和参数域，`netlist_preview` 对少量具体候选做
nonlinear PDK DC/AC 证伪，只有可能胜出的结构才创建/微调 OA 并走 `si` 或 ADE。当前
共源/共栅级联同条件示例已完成 nics4304 live smoke：两份 241 点 AC、DC OP、逐文件
SHA-256 manifest 和执行后零 Spectre/si/Maestro 进程均通过。preview 的级联/共源
gain/BW/GBW 比为 `1.538/0.489/0.753`，既有 OA→`si` 对照为
`1.404/0.534/0.750`，主要方向一致；但单结构绝对值误差最高超过 20%，因此只证明方向性
筛选价值，不替代已验证的 OA→`si` 结果。随后同一 9 点 cascode 域全部经过 standalone
preview：top-3 为 `009/007/003`，参考 OA→`si` top-3 为 `009/003/007`，Spearman
ρ=`0.9333`，两边 winner 均为 `009`；gain/BW/GBW/power 最大误差为
`4.39%/11.04%/10.13%/19.45%`。top-3 政策把 OA 复核成本从 9 点降到 3 点，但该政策是在
已知同域结果上事后校准，下一拓扑必须作为 prospective Gate 重新验证，不能沿用这些误差
或门槛。随后 top-3 已由正常 OA→`si`→Spectre 路径重放：009/007/003 的参数、三份
`si` 网表 hash 和每点 40 项指标均与原九点参考对应项一致，真实 winner 仍为 009；3 点
OA wall time 为 229.924 s，比原 9 点的 811.503 s 少 71.667%。preview 与 3 点 OA 合计
301.553 s，仍少 62.840%。这只是已知同域的回放计时，不是新拓扑 prospective 证明。
2026-07-28 的差分对八点 Gate 首次在不读取新 OA truth 的情况下冻结 top-3。八个 tuple
均与用于建模的历史候选不重合；冻结后才运行完整 OA→`si`→Spectre 域。preview 与真值
功耗排名 1–8 完全一致，Spearman ρ、可行性 agreement、真值可行点 recall 均为 `1.0`，
真实 winner `op-local-001` 在 top-3 内。该结果把 prospective ranking/shortlisting 状态
升级为 live，但 gain/BW/GBW/power 最大绝对误差仍为
`3.05%/24.10%/27.71%/30.35%`，所以不升级为绝对预测或 design closure。冻结 top-3 已
自动编译为正常 OA 任务但没有重复执行；根据同一完整 run 中前三点的 stage/simulation
动作估算 preview+3 点约 `354.176 s`，不是独立三点实测。完整证据见
[`validation/2026-07-28-differential-pair-preview-prospective-live.md`](validation/2026-07-28-differential-pair-preview-prospective-live.md)。
这条能力完成验证后不再要求立即换一个拓扑重复跑完整 truth domain。日常设计默认只执行
冻结 shortlist 的 OA 真值任务；完整域退到筛选代码/PDK 条件变化、shortlist 失效或周期性
审计。候选数、成本判据、一次批处理、授权点和质量 follow-up 的固定规则见
[`fast-preview-shortlist-workflow.md`](fast-preview-shortlist-workflow.md)。
HSPICE 没有加入默认链路：现有 Spectre runner 的调用复杂度相同，并且与最终
foundry-model 真源一致。完整证据见
[`validation/2026-07-27-standalone-netlist-preview-live.md`](validation/2026-07-27-standalone-netlist-preview-live.md)。候选编译器的本地证据见
[`validation/2026-07-27-preview-candidate-compiler-local.md`](validation/2026-07-27-preview-candidate-compiler-local.md)，九点执行和筛选校准见
[`validation/2026-07-27-preview-candidate-selection-live.md`](validation/2026-07-27-preview-candidate-selection-live.md)，top-3 OA 交接与计时见
[`validation/2026-07-27-preview-shortlist-oa-handoff-live.md`](validation/2026-07-27-preview-shortlist-oa-handoff-live.md)。

## 理论先导尺寸分析

`vda theory` 是 Bridge 之前的纯本地分析面，不属于远端 task operation，也不生成计划 token、OA 写入或 Spectre 结果。首版只支持已经进入 Gate 6 的固定拓扑 `nmos_differential_pair_pmos_current_mirror_load_with_tail_device`，不把一个通用方程求解器伪装成任意电路综合。

输入不是人工列出的 W 候选，而是三个明确角色的有限器件 characterization 域。每个点包含 `gm/Id`、`gds/Id`、`Id/W`、输出电容密度、`VDSAT` 和 L；PDK characterization 或 EDA OP 来源还必须声明 artifact id 与 SHA-256。请求同时固定 characterization 的 corner、温度和三类 VDS/VSD，并分开声明电流镜二极管节点 `OUTP` 与单端输出 `OUTN` 的 DC 电压；两支路任一实际工作电压超出 characterization 容差即拒绝。该哈希目前仍由请求提供，本地分析器不自行下载或验证远端 artifact，所以器件表值的证据是 `user_input`；所有推导指标是 `software_inference`。

对每个输入 NMOS、PMOS 负载和尾管表点的笛卡尔组合，分析器不扫描 W，而是从以下关系解析反解满足 BW/GBW 与最小宽度的最小支路电流，再由 `W=Id/(Id/W)` 得到 W：

```text
gm   = Id * (gm/Id)n
gout = Id * ((gds/Id)n + (gds/Id)p)
Cout = CL + Id * (Cout_density_n/Jn + Cout_density_p/Jp)
Ad0  = gm / gout
BW   = gout / (2*pi*Cout)
GBW  = gm / (2*pi*Cout)
```

这套解同时给出功耗、面积代理、三管 KVL 余量、频率寄生渐近上限、约束裕量、主导电流下界和固定表点下的局部对数敏感性。结果保存 canonical request SHA-256、器件 artifact id/hash 和 characterization 条件，使推荐能精确追溯输入。结果穷尽的是请求中声明的离散表域，不是 PDK 的连续 W/L/bias 空间；一阶输出极点还没有覆盖内部极点/零点、slew、settling、噪声、失真、mismatch、稳定性和 PVT。因此推荐只能作为 theory-seeded Spectre 候选，不能直接写回 OA 或宣称设计闭合。当前 synthetic 示例见 `examples/theory/differential-pair-gmid.synthetic.json`。

`vda theory-calibrate` 是另一个纯本地、只消费既有 run record 的后处理入口。它不把命令成功当作 EDA 证据：训练与验证记录必须来自 real Bridge adapter；每点必须同时绑定 OA `bridge_readback`、匹配的 Gate 6 自动 `si` 网表、Spectre OP/AC `eda_result` 以及 netlist/wrapper/DC/OP SHA-256。校准器还要求训练点形成无重复的完整 Wn×Wp 矩形网格，目标 cell、profile/model include、L、BIAS、VCM、VDD、CL 全部相同，验证点不得越过训练宽度范围。任一证据降级或条件漂移都会拒绝。

首个 nominal `top_tt` 校准用 `Wn=[1.5,2.0] µm × Wp=[1.5,2.0,2.5] µm` 六点真实数据拟合 `Ad=k*gm/(gdsn+gdsp)` 与 `Ceff=CL+Cn*Wn+Cp*Wp`，并逐点留一重拟合。输出绑定两个 run record 哈希、每点原始证据哈希、全部预测/误差、固定条件和禁止外推边界。新鲜同点只读 Spectre 复跑用于检查执行重复性；它不冒充几何外推验证。拟合及误差判定仍是 `software_inference`，OA 与 EDA 原始量分别保留 `bridge_readback`/`eda_result`。

该 topology-local 校准已经量化当前一阶模型在小范围内的误差，但它不是独立 MOS characterization：`Cn/Cp` 是拓扑等效电容，不能冒充 PDK 的端子电荷导数或结耗尽电容；每个校准预测仍消费该观测点由 Spectre 得到的 `gm/gds`。Gate 7A–7D 现已提供独立 TSMC N28 表，并把 Gate 6 三种器件角色及各自真实 DC 偏置绑定到三张 exact-signature artifact，再以未参与建表的完整电路点验证。topology-local 校准仍不会自动注入 `vda theory` 或触发 OA 写入；它与通用器件表/矩阵路径是两条证据边界不同的先导工具。

### 理论候选编译与 EDA 复核

Gate 8 把上述层之间的交接做成三个独立、可审计的本地入口。`vda theory-request-from-validation` 只接受已经通过的 held-out validation SHA-256 及其 exact characterization run 集合，从验证记录中的实际 DC 偏置、器件表 VGS 轴和 `dQi/dVj + cjd/cjs` 派生 topology-local theory request。规格、宽度边界和角色映射由 `user_input` policy 固定；器件原始值仍是 `eda_result`，派生 request 是 `software_inference`。validation、器件 run、PDK/PVT、model/W/L/signature 或来源集合任一漂移都会在理论求解前拒绝。

`vda theory-seed-task` 再把穷尽的理论结果编译成普通 `TaskSpec.theory_seed`。每个 seed 是输入管、负载管和尾管参数的完整原子 tuple，不允许同 `parameter_space` 或 `instance_parameter_space` 组合，也不会展开成笛卡尔积。policy 可保留理论排名前缀，并在剩余可行点中用确定性 log-distance maximin 选择覆盖点。连续宽度必须在生成 plan/token 前按声明的小数位或 OA/PDK 网格量化；Gate 8 live 使用 `0.005 µm` half-up 网格。量化规则、request/result/policy hash、器件 artifact hash、理论域穷尽和最优性边界都进入 task 与 run record。

正常 executor 对每个 tuple 仍执行 OA 暂存、定向回读、自动 `si` netlist 和 Spectre，并保存理论候选 ID/预测值为 `software_inference`。可行性和最终排序只读取 `eda_result` metrics；理论排名不能覆盖真实 EDA 选择。checkpoint 把候选边界作为原子状态，transport reset 后必须先恢复/独立回读，再从未完成 tuple 续跑。

`vda theory-seed-validate` 最后用 task/run SHA-256、plan token、候选顺序、参数、证据源和完整域审计比较理论与 EDA。它把“候选中有足够真实可行点”和“每点数值预测足够准确”分成两个 Gate。首个 nominal TSMC N28 live Gate 的前者通过（4/6 可行），但功耗/增益/BW/GBW 的 24 项比较有 8 项超过预先固定的 25%，理论第一名也不是 EDA 最小功耗点，因此总状态为 `partial`。这条路径当前只能称为可靠的 theory-seeded shortlist，不能把同一六点回归包装成已校准预测器。

共栅级微调增加一个更窄、可重配置的 `vda cascode-seed` 编译器，而不是把三个随手枚举值写死进 worker。输入必须是成功的 real-Bridge 共源 DC run，并同时绑定文件 SHA-256、task/candidate、目标 OA、Bridge probe 的 exact PDK profile、`Cadence si -batch` 参数一致性和 Spectre `VGS/VDSAT/Id`。policy 声明下管饱和余量、共栅管宽比、沟道长度比、偏置 offset、量化网格和上下界。当前候选 mapping 不改变 MNCAS 的 fingers/multiplicity，因此来源 MN0 的 OA 与网表 `nf/m` 必须都为 1；其他 count 必须先扩展原子 instance-parameter mapping，不能静默套用。编译器先用 `VTH_est=|VGS|-|VDSAT|` 与声明余量确定目标内部节点，再按尺寸比例缩放共栅管过驱动，生成完整 `(cascode_width_um, cascode_length_um, cascode_bias_v)` 原子 tuple；量化后重复、越界、来源或固定参数漂移都会拒绝。

`vda candidate-task-from-cascode-seed` 把 exact result 同时编译到 DC 或 AC task template，强制两阶段使用完全相同的 tuple 顺序、source hash 和固定下管 W/L/RD/bias/VDD。DC 先由 Spectre 证明两只 MOS 的节点关系、下管/共栅管/RD 电流一致性、各自 `VDS−VDSAT` 和 stack headroom；AC 才复用同一 OA→`si` netlist、DC OP 与既有复数波形解析，提取 gain、首个 −3 dB bandwidth、GBW 和 unity-gain。理论 seed 始终是 `software_inference`，不能替代 `eda_result`，也不声明连续/全局最优。首个 live Gate 已在新 cellview 完整执行同一 9 个 tuple：DC/AC 对应 `si` 网表 9/9 匹配、两轮 9/9 可行，AC 离散域最佳点得到 gain `6.4412 V/V`、BW `3.6127 GHz`、GBW `23.2705 GHz`；随后 exact inverse 与普通共源 DC/AC 身份检查通过。

### 真实工作点局部重线性化

`vda op-relinearize` 是纯本地 run-record 后处理入口，不调用 Bridge、不写 OA。它不依赖某个固定拓扑公式，而是由 policy 显式声明 source task/run SHA-256、一个真实 anchor、互斥的训练/留出 candidate index、待微调 semantic 参数、工作点/性能指标、信任边界、量化网格、误差门和局部筛选规格。来源必须是成功的 `virtuoso-bridge-subprocess` run；每个建模指标必须是完整 `eda_result`。未建模 semantic 参数必须在训练和留出点保持不变；原始 CDF 值若发生变化，必须先建立可数值解释的 semantic 映射，不能把任意字符串硬塞进线性回归。

每个指标以 anchor 为截距，对 `(parameter-anchor)/proposal_step` 做一阶最小二乘。训练扰动不能独立张成全部声明参数时直接拒绝，不用 ridge 隐藏不可辨识性。训练误差和未参与拟合的留出误差分别计算，并且每个指标两道门都必须通过；此外每个声明参数必须在至少一个 heldout 点相对 anchor 有非零扰动。后者不是完整的系数可辨识性证明，但能阻止一个完全没有留出覆盖的方向借其他维度的低误差伪装成已验证。任一门失败时都不生成 `candidate_set`。通过后才在 anchor 周围的显式小网格生成候选，先排除已经测过的 tuple，再按局部筛选约束和 objective 排序；首项固定保留已测 anchor 作为控制点。所有预测、排序和误差判断是 `software_inference`，来源实测指标仍是 `eda_result`。

`vda candidate-task-from-relinearization` 只接受 passed result，并把结果、policy、source run 和 task template 全部做 SHA-256 绑定。template 必须精确匹配固定参数、objective，并至少保留局部模型用过的筛选 constraints；可以额外保留饱和区、THD、CMRR 等未由局部模型预测的完整规格，最终仍由同源 EDA 判定。首轮本地 Gate 用既有真实记录完成了共源级 6-train/2-heldout、10 指标和差分对 4-train/2-heldout、15 指标验证，分别从 27 个局部组合编译 6 个原子候选；最坏留出误差为 `11.450%` 和 `8.262%`。

真实运行后的误差审计不靠人工抄表。`vda op-relinearization-validate` 只读 exact result/task/run，核对 result/task/run SHA、plan token、候选 source/ID/顺序/tuple/预测、完整离散域和 real-Bridge/`eda_result` 边界，再用模型保存的 held-out limit 比较每个新点。候选执行成功、预测推荐与 EDA 推荐一致、逐点预测精度是分开的结论；误差超门时保留完整比较并返回 `partial`，不会撤销已经由 EDA 正确完成的选优，也不会把它包装成数值模型已校准。当前 result schema 会逐 metric 保存 `relative_error_floor`。对字段尚未序列化的旧 result，validator 不再用 schema 默认值猜测；必须用 `--policy` 提供 result 已绑定 canonical SHA 的原始 policy，并逐项核对来源、constraints/objective、metric set、role、response scale 与误差门。比较结果同时记录 floor 数值和 `result_model/hash_bound_policy` 来源。

2026-07-26 共源 6 点 live Gate 完整运行 OA→`si`→AC/transient/noise，6/6 全规格可行，预测与 EDA 都选 `1.1 µm/19 kΩ/0.75 kΩ`；GBW 从 anchor 的 `30.2890 GHz` 提高到 `34.3965 GHz`。GBW 最大预测误差为 `4.505%`，但候选 5 的 output swing 误差为 `22.479% > 20%`，所以执行/推荐 Gate 通过、逐点预测 Gate 为 partial。该结果要求后续在新 anchor 重新留出验证或缩小 trust region，不能靠放宽 20% 门变绿。

随后以真实 EDA 最佳点重定 anchor。把 W/RD/RS 都保留的三维策略虽然数值误差看似很低，但 heldout candidates 5/6 的 RS 都等于 anchor 的 `750 Ω`，被新的参数覆盖门拒绝。合法刷新只建模 W/RD，并把 RS 固定为来源 record 的真实值；training `[2,3,4]`、heldout `[5,6]` 同时覆盖两维，10 个指标最坏 heldout 误差为 P1dB 的 `0.568%`，output swing 为 `0.319%`。生成域缩至 `W={1.05,1.10} µm × RD={18.5,19,19.5} kΩ` 六点，首点仍是已测 anchor；在执行前，这一阶段只属于本地 `software_inference` 候选准备。

同日该二维刷新完成真实 OA→`si`→AC/transient/noise 六点执行。6/6 全规格可行，预测与 EDA 都选择 `W=1.1 µm、RD=18.5 kΩ、RS=750 Ω`，得到 gain=`3.87961 V/V`、bandwidth=`8.93269 GHz`、GBW=`34.65534 GHz`。exact validator 的 60/60 项逐点比较全部通过，五个新点最坏误差为 candidate 6 P1dB 的 `0.353712%`；因此本小域从“候选已准备”升级为 **held-out-covered common-source W/RD local response to same-source EDA selection verified at nominal top_tt**。一次 DNS/SCP 失败和一次 `WinError 10054` 都按 `system_event` 恢复基线并从原子 checkpoint 续跑，最终独立 OA 回读确认最佳写回。该结论仍固定 `RS=750 Ω` 且只覆盖 nominal `top_tt`；若继续调整 RS，必须先增加独立 RS 探针，PVT 也不得从本结果外推。

同日差分对的 `Wn/Wp/Wtail` 六点也完成真实 OA→`si`→双支路 DC/差模 AC/共模 AC/CMRR。6/6 全规格可行；功耗 objective 下，局部模型和 EDA 都选择 `1.215/1.080/0.555 µm`，得到 `10.2971 µW`、gain=`3.71568 V/V`、BW=`2.68269 GHz`、GBW=`9.96800 GHz`、CMRR=`34.8249 dB`。相对 Gate 8 anchor，功耗下降 `6.228%`，gain/BW/GBW 只下降 `0.323%/0.254%/0.576%`。旧 result 的归一化 floor 通过 exact canonical-hash-bound policy 恢复后，90/90 比较全过，最坏新点误差为 BW 的 `2.5693%`。一次 DNS/SCP 和两次 `WinError 10054` 均在恢复并独立回读 anchor 后从未完成候选续跑，最终 OA 独立回读最佳宽度，远端 `Spectre/si/Maestro=0/0/0`。状态升级为 **differential-pair held-out local-response shortlist to same-source EDA selection verified at nominal top_tt**。旧 anchor 的 PSRR/noise/linearity/ICMR 证据不能外推到新宽度，所以下一增量 Gate 是新最佳点的只读质量回归；PVT 仍为可选项。

### 通用小信号网络核心

`vda small-signal` 补充的是 `vda theory` 下方的拓扑无关计算层，而不是第二套仿真器。请求没有 topology 枚举，只包含 width-normalized MOS characterization、MOS/R/C 实例与节点、固定 AC 边界、输入/输出节点线性表达式和频率点。共源、源极退化、差分连接和电流镜的差异由图连接表达；同一个 stamping 路径组装复数 `Y(f)`，分块求解未知节点，再计算 transfer、低频参考、相位和首个 −3 dB 交点。

MOS characterization 点不绑定“输入管/负载管/尾管”等电路角色，而是绑定 model、polarity、L、VGS/VDS/VSB、`Id/W`、`gm/Id`、`gds/Id`、`gmb/Id`、完整 signed 4×4 `dQi/dVj` 本征电荷导数矩阵，以及与它分开的 drain/source-to-bulk 结耗尽电容 `cjd/cjs`。`cgs=dQg/dVs` 与 `csg=dQs/dVg` 等交叉导数不假定相等、也不取绝对值；矩阵按方向直接贡献 `jω dQi/dVj`。`cjd/cjs` 不在该本征矩阵中，必须作为额外的 drain/body 与 source/body 二端电容 stamp。实例必须声明相同 model/L 和容差内偏置，随后才按 W/multiplicity 缩放；真实 PDK/EDA 表必须绑定原始 `eda_result` artifact SHA-256，归一化点值明确标为 `software_inference`。矩阵组装和指标也始终是 `software_inference`。旧 artifact 缺少完整矩阵时保留显式五电容兼容路径，不能冒充 Gate 7D 证据。

为了保留面向后续复杂电路的可修改性，数值层与正式请求契约进一步分开。`ComplexNodalSystem` 公开最小的复数 KCL 系数、RHS、二端导纳和电流注入接口；电路专属脚本可以在这些接口上增加局部 VCCS/CCCS、频率相关等效项或特殊激励，而不复制节点分块和高斯求解。若普通节点法不足，`solve_complex_linear_system` 允许脚本自行组装带支路电流等辅助未知量的 MNA 方程。节点 API 的 pivot tolerance 也是显式参数，默认仍保持保守值。

这不是运行时加载任意代码的任务插件系统。正式 `vda small-signal` JSON 仍只接受已经验证的 MOS/R/C 与固定电压边界，以维持确定性、可序列化和证据可审计性。电路专属脚本负责局部构图、观测量和约束；某种新 element、激励或 metric 只有在重复需要、补齐 strict schema、失败测试和 Spectre 对照后，才提升到正式任务契约。底层当前仍是纯 Python dense solver，适合 theory seed 和有界本地网络；大规模、强病态、noise/nonlinear 或精确 foundry 模型继续交给 Spectre，未来确有证据时再替换成稀疏数值后端。

首个本地 Gate 用同一核心验证 NMOS 共源、源退化共源、对称 NMOS 差分对和 PMOS 共源，并注入浮空矩阵、偏置漂移、缺失 artifact hash 等失败。Gate 7A 的真实 TSMC N28 artifact 又直接进入同一 schema 和矩阵核心。

Gate 7B 新增 `vda small-signal-validate` 作为 run-record 后处理入口。它只接受 real Bridge 的独立器件表记录和只读 OA/`si`/Spectre 电路记录；demo、OA 写 action、target/PVT 漂移、空 AC、缺失 raw hash 或 evidence source 降级都会拒绝。binder 从结构化 `si` 实例生成 MOS/R/C 图，以真实 DC OP 而非理论 DC 解确定 VGS/VDS/VSB，在 exact L 平面内做有角点权重记录的 VGS/VDS/VSB rectilinear interpolation，并拒绝所有 bias extrapolation。W 平面和除 `w/l/nf/m/multi` 外的模型参数集合/值必须与 `si` 完全相同；匹配集合另存 count 与 SHA-256。随后通用矩阵核心在原始 EDA 频率网格预测 gain、phase、−3 dB bandwidth 和 GBW，并按运行前固定 policy 同 Spectre OP/AC 比较。

多 MOS 图不再被迫共享一张器件表。请求可以携带一个 primary 和多个 `additional_characterizations`，每个 MOS 实例显式保存 `characterization_id`；所有表必须属于同一 PDK profile/process corner/temperature 和证据边界，ID 唯一，并且每张表都至少被一个 `si` 实例实际使用。自动 binder 按实例 model、polarity、W/L、完整模型参数签名和可选来源实例绑定选择唯一表；缺表、重复匹配、几何或签名漂移、未使用表都会在矩阵求解前拒绝。结果同时保存全部 characterization run/task/artifact hash，避免只记录 primary 表而丢失 PMOS 或尾管来源。

Gate 7C 没有增加源极退化专用 AC 公式。binder 遍历 `si` MOS/R instances，发现 MOS
source 不是固定 `VSS` 时，必须从真实连接中找到唯一的 source-node→`VSS` 电阻，并把
该内节点纳入同一个 `Y(f)`；`source_degenerated_common_source` policy 还要求 EDA DC
`source_degeneration_consistency=matched`。因此 `RS0` 数值、`NSRC` 电位与 body effect
都来自图和 OP，不是 topology 名称触发的硬编码增益修正式。RS 接错、内节点电压与
VGS/VDS 不一致、source-current 不匹配均在预测前拒绝。

该严格签名只属于理论验证 policy，不收窄 Bridge 仿真面。common-source parser 对普通
`simulation.run` 仍保留无法结构化的未知 Spectre parameter token 并继续原流程；只有
`require_exact_model_parameters=true` 的 Gate 才因签名不完整而拒绝。探索性 policy 可
显式关闭 exact signature，但这种结果不能作为 Gate 7B/7C exact-geometry 证据。

首个 nominal `top_tt` 共源 held-out circuit 已通过：DC Id/gm/gds/VDSAT 误差分别为 7.57%/9.78%/11.04%/0.78%，增益误差 0.464 dB，BW/GBW 相对误差 10.38%/15.05%，相位门也通过。中间的 W=1 µm 表和未携带 LDE 的 W=0.5 µm 表均按原门限保留为 partial，促成了完整 31 项 `si` 参数签名而不是放宽门限。

随后源极退化 `vda_cs_ac_tradeoff_001` 也通过相同 policy 阈值。旧任务声明 W=0.5 µm
而当前 OA 为 1 µm 的第一次执行在 Spectre 前拒绝；刷新 `si` 后由上述生成器建立新的
1 µm/31 参数表。最终图绑定 `MN0/ RD0/RS0/NSRC`，Id/gm/gds/VDSAT 误差为
17.59%/17.69%/21.36%/2.82%，gain/BW/GBW 误差为 0.374 dB/13.52%/17.16%，全部
通过未改变的 25%/0.5 dB/5° Gate。这完成了 single-MOS common-source readback
迁移。

Gate 7D 随后用三张独立真实表绑定非 1:1 的电流镜负载真实尾管差分对：输入 NMOS、PMOS 负载和 NMOS 尾管分别选择各自 exact W/L、31 项 `si` 参数签名及来源实例的 artifact，差分输入固定为 `INP=+0.5/INN=-0.5`，输出沿 Gate 6 的单端 `OUTN` 契约。三张表的 `source_run_sha256` 都必须等于当前 held-out 电路 run，电路 netlist hash 也必须一致；missing PMOS、重复匹配、W/签名/来源漂移和未使用表均拒绝。

第一版 legacy 五电容模型把实际 `2.9756 GHz` BW 预测为约 `5.27 GHz`。加入完整 signed 4×4 电荷导数后仍为 `5.2356 GHz`，所以不能把误差简单归因于非互易电容方向。验证器随即从同一电路 OP 独立提取每管实际 `cxx`；三张表的最坏矩阵误差仅 `0.74%`，排除了 bias 插值/表绑定作为主因。最后把此前遗漏、且与本征 `cxx` 分开报告的 `cjd/cjs` 纳入表征、逐器件比较和网络 stamp 后，预测/实际增益为 `11.5254/11.4623 dB`，BW 为 `3.0987/2.9756 GHz`，GBW 为 `11.6800/11.1351 GHz`；误差 `0.063 dB/3.97%/4.67%`，相位与五管 DC/电容也全部通过原 policy。全过程只读 OA、没有修改 Bridge。状态为 **differential-pair exact three-plane same-source small-signal validation verified at nominal top_tt**。多指/多重器件、其他 PVT、mismatch、noise 和不同输出表达式仍需独立 Gate；Spectre 始终是最终规格证据。

## ADE 人工介入与状态所有权

ADE 兼容是当前架构约束，不是 UI 附加项。VDA 可以规划、搜索、判规格和选优，但可复核的 ADE setup/history 必须继续允许人类打开、调整、运行和保存；VDA 不能把唯一真源藏在一次性 wrapper 或内存状态里。自动路径和人工路径通过显式 operation 交接，不能在一次运行中暗中互相覆盖。

首个纵向切片由 `ade.prepare` 和 `ade.capture` 组成，当前只使用 Bridge 已有的 ADE Explorer/Assembler Maestro 公共接口，不修改 Bridge，也不复制 SKILL、结果导出或文件传输。`prepare` 要求 design view 已存在并预检相邻 Maestro view 不存在，然后创建一个持久化 Spectre test、保存、关闭并重新打开核对 test 名称。它不配置 analysis、stimulus、sweep 或 output；这些状态留给人工调整。若 Maestro view 已存在，无论 `replace_existing` 如何都在模型或 worker 边界拒绝，不覆盖也不尝试合并。

`prepare` 不是跨 OA/transport 的事务：若 `save_setup` 已落盘而 close、独立回读或连接随后失败，新 Maestro view 可能真实存在但本次 run 失败。下一次执行会因 view 已存在而停止，要求人工检查；VDA 不自动删除这个可能包含有效状态的 view。

执行 `capture` 前由用户自行打开、调整、运行、保存并聚焦目标 `library/cell/maestro`。worker 先做一次轻量 snapshot，拒绝无焦点、目标不匹配或会话中途切换；默认也拒绝带 `*` 的未保存 setup。随后 Bridge 捕获 setup 文件、指定或按 mtime 选择的最新 history、Spectre netlist、PSF 和日志，并读取 ADE Detail 表中的每个 sweep point、变量、output、spec 和 pass/fail。

本地 manifest 对每个文件记录相对路径、大小、SHA-256、类别与来源。Maestro setup 属于 `bridge_readback`；实际 simulator input、PSF/log 和 ADE output/spec 属于 `eda_result`；任务固定的 history 属于 `user_input`，自动选取最新 history 的规则属于 `software_inference`。setup 和 simulation artifacts 分别形成聚合指纹，供后续人工前后对比与冲突检查。`ade.capture` 不调用 run/save/close，不写 OA；其成功只说明“人工 ADE 状态和已有结果已被完整捕获”，不等于这些结果满足 VDA 规格。

无需 GUI 的自动分支由 `ade.run` 提供。worker 明确打开目标 cell 的独立 background Maestro session，回读非空 test 列表，通过 Bridge `run_and_wait` 取得本次调用返回的 history，再把该 history 传给 `read_results`。它不调用 `save_setup`、不改变窗口焦点、不写 schematic 或 Maestro OA。严格 corner Gate 在运行前先列出已有 history log；若 Bridge completion wait 超时，只在前后恰好新增一个名称、且至少一个 exact log 明确写出该 history completed 时继续，多个新 history、同名覆盖或未完成日志均失败，也不会自动重跑。该选择由 executor 再核对并标为 `software_inference`。默认必须同时得到非空逐点 output/spec 与完整产物清单；任一要求被显式放宽且证据缺失时，executor 只记为 `partial`。setup test 标为 `bridge_readback`，history/output 与 simulator input/result/log 哈希标为 `eda_result`。

后台产物不依赖聚焦窗口的 `snapshot`。VDA 对每个 test 的 Analog Session 临时设置 project/results dir，使 `asiGetAnalogRunDir` 落在 profile 的唯一 `/data/xum` scratch，并立即回读；退出前按逆序恢复原 session 值，不调用 `save_setup`。恢复任务必须同时给出 exact history 与原 runtime scratch，且不再次调用 `run_and_wait`。随后在 project/scratch 的 exact-history 路径收集 `.log/.rdb/.msg.db`，在唯一 runtime input 根收集普通文件，调用 Bridge 公共 shell 通道生成远端 TSV 大小/SHA-256 清单。每条路径必须绑定到这两类根之一；`netlist`、`input.scs`、至少一个非空结果和至少一个非空日志是硬门，重复逻辑路径内容不同时拒绝。

Bridge 的标准 `download_file` 仍是首选传输。nics4304 live smoke 暴露本机 DNS 暂时不能解析 `nics4304-cad1` 时，现有 Virtuoso tunnel/CIW 仍在线；VDA 因而只对自己生成的 `/data/xum/.../vda_ade_manifest_*.tsv` 和 Bridge 自己生成的 `/tmp/vb_results_<uuid>.csv` 提供有行数/字节上限的 SKILL 文本读取 fallback。它不传输 PSF 或任意远端文件，不替代 Bridge 的 SSH/SCP。IC6.1.8 单点 Detail CSV 缺少 `Point` 列时，VDA 只在临时本地 CSV 精确识别六列格式后补 `Point=1`，再交给 Bridge 原解析器；corner Gate 则调用 Bridge 已公开的 `read_results(..., include_raw=True)`，在 VDA 解析原始 Detail 的 parametric-point 行和每个 corner 列，保留 raw CSV 大小/SHA-256，并把 parser 标为 `software_inference`。Bridge 0.7.0 的普通结构化 parser 仍只保留 Nominal 列，因此不能用于证明正交 corner；Bridge 源码没有修改。

可选的 `require_simulator_input_consistency` 再从 manifest 精确读取每个 test 的 Spectre 输入。单点扁平输入直接读取 `input.scs`；IC6.1.8 原生 sweep 的实际 runtime 形状是 `input.scs` 显式 `include "netlist"`，因此严格模式要求两者处于同一唯一 runtime test 根、分别匹配 manifest SHA-256，并作为一个输入束解析。随后核对 Design library/cell/view header，独立回读 source schematic，比较实例集合、已知 primitive 的节点顺序和显式 raw 参数映射。当前 TSMC N28 smoke 覆盖 MOS `l/w/nf/simM→multi`、pulse/DC source 与 capacitor，共 19 组映射；`Wfg` 明确标为 PDK CDF 派生语义，不能用字面 `w` 相等代替。这个 Gate 证明当前输入与 OA raw state 同源，但不证明所有 PDK CDF 派生关系，也不证明 history 名在运行前不存在。history 命名/覆盖策略仍沿用已保存 setup；它不是完整规格闭环。

`sweep_verification` 是 `ade.run` 上的可选严格附加门，不替代或收窄 Bridge 的普通 Maestro 运行能力。任务显式列出 exact tests/corners、声明 scope 的完整 sweep 或固定值、可选 exact global-variable selections、从 1 连续编号的 VDA result cases，以及每个 test/variable 对应的 OA `instance.parameter`；一个变量可以绑定多个 OA 参数，但每个 test/variable 至少要有一个绑定。普通模式中 case 就是 Maestro parametric point；corner 模式中每个 case 另带 `maestro_point/corner`，并要求每个真实 parametric point 按声明顺序覆盖全部 corner。固定值按 corner override > test override > global 解析。test-scope sweep 必须显式声明同名 global selection 为 disabled；一旦声明 selection 契约，就必须覆盖所有有效变量。worker 在运行前后回读相同 setup/完整 selection 集，并逐 case 检查 RDB Detail 参数与至少一个非空 scalar output。证据选择有两个互斥模式：若 exact history 出现任意逐点目录，就坚持每个 point/test 的非空 `input.scs` 和结果都完整；若完全没有逐点目录，则只接受 IC6.1.8 已验证的 Maestro 数据库模式，要求每个 test 一个已哈希的符号 runtime `input.scs`+`netlist` 输入束、OA raw 参数与 Spectre 实例都引用变量、保留输入值匹配一个声明 parametric point、exact-history `.rdb` 非空，以及 completion log 的真实 parametric 点数精确。corner case 再要求原始 Detail CSV 的完整矩形 point×corner 网格。默认仿真错误必须为零。若已有人工 output 在某些声明点必然产生 calculator `eval err`，任务可用 `expected_output_evaluation_errors` 声明 exact test/output/point selector；该 output 不得参与 result mapping，RDB 实际错误单元格必须与声明集合完全相等，log error 数必须等于集合大小，worker 和 executor 都要求未解释错误为零。RDB 中每点的变量、非空 output、共享输入束和 RDB hash 再组成逐点指纹。`exact_point_input_result_binding_verified` 在数据库模式保持 `false`，不会虚构未保留的逐点文件；另以 `native_sweep_database_binding_verified=true` 表示实际证据模式。point/input/result 对应和单位等价比较属于 `software_inference`，原始 input/RDB/log/Detail CSV 仍是 `eda_result`。缺点、缺 corner 列、空映射 output、半套逐点产物、变量 shadow 后值不符、未声明 error、未知 primitive、include 缺失或任一 hash 漂移都硬失败。

`result_mapping` 是严格 sweep 之上的可选规格层，而不是所有 `ade.run` 的强制模式。首版限定一个 exact Maestro test；每个 sweep variable 都必须声明对应 VDA parameter、正数 scale 和 unit，每个 metric 都必须固定 exact output、预期 calculator expression、VDA metric、正数 scale 和 unit。worker 在运行前后读取并指纹化保存的 output state；executor 只接受表达式在两次回读中都与任务声明保守等价、逐点参数与 scalar 都有限且完整的记录。Cadence 对括号和 Spectre 工程数值的规范化可等价，不同信号、阈值、边沿、运算顺序或未知语法不等价。RDB 原始标量属于 `eda_result`，保存的 output expression 属于 `bridge_readback`，单位换算、constraint 判定和 objective 选优属于 `software_inference`，任务声明属于 `user_input`。缺失、空值、非有限值、表达式漂移或全不可行都不会产生伪 selected point。

2026-07-22 的 TSMC N28 live Gate 已在 `vda_ade_sweep_inv_001` 上保存 global `CL=1f,2f,4f`，真实运行 3 点 transient 并从 `Interactive.0` RDB 回读 `VoutAvg=415.5/419.8/428.3 mV`；completion log 为 3 点完成、0 错误。首轮运行后因错误的逐点目录假设失败，随后固定同一 history/scratch 只读恢复，没有重算。OA `CL0.c=CL`、Spectre `CL0 ... c=CL`、setup 前后指纹、两份 runtime 输入哈希和 RDB/log 均闭合。随后在同一 view add-only 保存质量 outputs；`Interactive.1` 三点全部通过约束并按最小能量选中 CL=1 fF。之后把 `VDD0.vdc` 与 `VIN0.v2` 共同绑定为 `VDD`，新增 global `VDD=0.8,0.9` 和七个 VDD-aware outputs，`Interactive.2` 六点中五点可行，选中 `0.8 V/1 fF`（3.254 ps、1.425 ps、1.309 fJ）。最后 add-only 新增两个 named corner，把 `CL=1f,4f` 写入 test scope、VDD=0.8/0.9 写入 corner scope，并把 global CL selection 从 enabled 改为 disabled。`Interactive.4/5` 均证明 2 个真实 parametric point × 3 个 corner 列完整，六个 VDA case 的 raw Detail CSV hash 相同，4 个低压 legacy error 全部解释，仍选中 `0.8 V/1 fF`。`Interactive.5` 还 live 验证 Bridge completion wait 超时后的唯一新 completed-history 自动恢复。这里所有 corner 仍使用同一 nominal `top_tt`，只改变 VDD，不是 process/temperature corner。

自动微调 setup 的首个切片是 `ade.variables.apply`。任务必须给出 exact `expected_tests`；使用 corner scope 时还要给出 exact enabled `expected_corners`。每个 value update 必须给出 scope、可选 scope name、`expected_value`（`null` 表示该 scope 不存在）和新字符串；selection update 则给出 global 名称及 `expected_enabled/enabled`。worker 在任何已配置 Maestro session 已打开时拒绝，随后以独立 background session 先读完所有声明 scope 的旧值与完整 global enabled/disabled 集；只有 tests/corners/旧值/selection 全部匹配才逐项写入，一次 `save_setup` 后关闭，并用全新 session 再次核对。global value 读写复用 Bridge public `get_var/set_var`；test 读写使用 public scoped `maeGetVar/set_var`；named corner 通过 Bridge SKILL channel 调用 Cadence `axlGetCorner/axlGetVarValue/axlPutVar`；selection 使用 `maeGetSetup/maeSetSetup`。没有修改第三方仓库。请求属于 `user_input`，旧值、即时值和持久化值属于 `bridge_readback`。tests + 可选 corners + 声明 scope + 完整 selection 集形成 targeted 前后指纹，但不是完整 setup 指纹。逗号列表本身只说明该 scope 声明了 sweep；只有额外启用并通过 `sweep_verification`，才能把 OA 输入束、RDB point/corner 与实际结果联系起来。2026-07-22 已先闭合 global CL 三点，再真实闭合 `test:VDA:CL=1f,4f`、两个 named-corner VDD 值以及 global CL enabled→disabled 的三阶段回读和后续运行。

corner membership 使用正交的 `ade.corners.apply`，不塞进 variable patch。它以 exact tests 和旧 corner 顺序为 CAS，只允许 add-only 新增不存在的命名 corner；每次新增后立即读取完整列表，全部写完才保存一次，再独立重开核对。删除、改名、替换和 model/temperature 配置都不在该 operation 内。2026-07-22 live 从内建 `Nominal` 增加 `VDA_LOW_VDD/VDA_NOMINAL_VDD`；内建 Nominal 不能当 named `axlGetCorner` handle 使用，因此其 VDD 由 global 0.9 V 提供，两个 named corner 分别覆盖 0.8/0.9 V。

第二个切片 `ade.setup.apply` 保持与变量修改正交。任务列出 exact tests；每个 analysis update 都提供完整旧 `enabled/options`（不存在时为 `null`）、目标 enabled 和有限 option delta。worker 先读取全部 analysis 旧状态，并确认全部待新增 output 名在对应 test 中不存在；任一不符都在第一个 writer 调用前停止。随后复用 Bridge public `set_analysis`、`add_output`、`set_spec`，每项写后经 SKILL channel 调用 `maeGetAnalysis`、`maeGetTestOutputs` 和 `axlGetSpecData` 回读，全部一致才保存一次，关闭后用全新 session 再次核对。请求属于 `user_input`；旧值、即时值和持久化值属于 `bridge_readback`。该 operation 不运行仿真，因此 output 只有配置而没有 `eda_result`。

首版本只新增不存在的命名 output，不替换已有 output：Bridge public writer 没有暴露删除接口，而仅凭同名 `add_output` 无法证明是更新、重复还是丢失未建模的 plot/save/description 状态。VDA 没有删除或屏蔽 Bridge 原有接口；需要直接使用 Bridge 的场景仍可独立进行。要把已有 output 替换纳入受控 operation，必须先扩充完整旧状态契约、删除/恢复语义和 live smoke。analysis options 当前只接受可精确回读的扁平 string/bool/null alist；嵌套 option 返回会保守失败而非丢弃。

当前受支持的持久化后端是 `maestro`，因为这是 Bridge 已公开并带 setup/history/result API 的路径。Bridge 同时提供 ADE L state 到 Maestro 的迁移原语，但 VDA 尚未把迁移包装成 operation，也不会在没有备份、目标冲突检查和 live smoke 时改写旧 state。2026-07-21 已在 nics4304 真实通过 `ade.prepare → ade.setup.apply → ade.run/resume`；2026-07-22 又真实通过 global 与 test/corner scoped `ade.variables.apply`、add-only `ade.corners.apply`、global selection CAS、原生 CL/VDD×CL/point×corner sweep 的输入束/RDB/Detail 绑定，以及表达式固定的 delay/skew/周期供电能量到 VDA constraints/objective 的映射。`ade.capture` 的人工入口、已有 output 安全替换、真实 process/temperature corner、multi-test/multi-analysis mapping 和共源 L/VDD 联合搜索仍待 live；需要打开 ADE、旧 ADE L 迁移和人工数值对照的 Gate 已延期到 [`deferred-manual-gates.md`](deferred-manual-gates.md)。人工改动必须通过前置条件、setup/output 指纹或重新 snapshot 被发现，而不是被 VDA 静默覆盖。

这里的待验证项只针对 Maestro/ADE setup；direct common-source `si`/Spectre 已另行通过 L/VDD 和 fixed-design TT/SS/FF，二者不会静默共享状态或证据。

## 反相器同源仿真路径

真实 adapter 的反相器仿真按以下顺序执行：

```text
目标 OA schematic
  -> Bridge 结构与参数回读
  -> simInitEnvWithArgs 生成基础 si.env
  -> 补齐已验证的 Spectre formatter / view-list 上下文
  -> si -batch 导出结构网表
  -> 解析 MN0/MP0、端口、master、W/L 并与 OA 回读比对
  -> 只含 model、激励、负载和 analysis 的 wrapper
  -> Spectre PSFASCII
  -> 非空 time/IN/OUT/VDD_SRC:p 波形检查
  -> timing + 周期供电能量/平均功率提取、规格判断
```

这里的 wrapper 是 testbench 契约，不再重复 MOS 拓扑。显式给出的 `VDD/CL` 标为 `user_input`，省略时采用 profile 默认值并标为 `software_inference`；MOS 拓扑与尺寸来自 OA/`si`。`simulation.run` 只读 OA，省略器件尺寸时采用回读值，显式给出时必须匹配。调优任务则在已经授权 OA 写入时逐候选暂存并回读，最后提交最佳可行点；无可行点或可恢复中断时恢复初始尺寸。

新建反相器缺少尺寸时也只从 PDK profile 取初始值。`nics4304_tsmc28` 当前使用 `default_inverter_nmos_width_um=0.6` 和 `default_inverter_pmos_to_nmos_width_ratio=1.2`。2026-07-25 在隔离 cell 上完整实测 `1.20/1.25/1.30/1.35`；相同 nominal `top_tt`、0.9 V、2 fF、5 ps input edge 下，1.20 的 rise/fall skew 为 0.0394 ps，优于 1.25 的 0.2119 ps 和 1.30 的 0.3639 ps，因此替代前一天粗网格选出的 1.25。解析优先级仍为“任务显式参数 > 已有 OA 回读 > profile 缺省”，所以只修改已有 OA 的 Wn 不会暗中联动 Wp，显式 Wp 也永远覆盖比例。该缺省只是一个负载/slew/PVT 尚未扩展的 nominal 起点，不收窄 `parameters.apply`、raw CDF 或有限搜索的能力面，详见[粗网格记录](validation/2026-07-24-inverter-drive-ratio-calibration-live.md)和[细化记录](validation/2026-07-25-inverter-ratio-refinement-live.md)。

自动单点路径仍先选 `si`，因为反相器目标是 DUT-only schematic，未承诺已有 Maestro test/setup；强行创建 Maestro view 会给普通 `simulation.run` 引入额外 OA 配置写入。`ade.capture` 只接收一个显式存在的人工 Maestro 真源，不会让它与 wrapper 路径暗中混用。进入 VDA-managed sweep/corner 前，必须明确任务选择哪一个仿真状态、保存其指纹，并复用 Bridge 的 Maestro/netlist API；两条路径不能同时成为未声明的真源。

2026-07-19 的 live smoke 已验证上述路径可在 nics4304 上从目标 OA 导出真实网表并得到非空 transient 与供电电流波形。`design.tune` 的候选参数暂存、回读、重新 netlist、仿真、最佳点提交，以及不可行/预算耗尽恢复均有成功的真实记录。

同日长 sweep 暴露了 transport 边界：transport 不可用期间既不能继续仿真，也不能保证 OA 恢复。VDA 现在把 Bridge worker 中断视为搜索暂停，不把该候选伪装成不可行点，也不继续消耗后续候选。调优在初始 OA 回读、每次待写入/确认写入以及每个候选完成边界原子更新本地 checkpoint，保存 task/token/adapter、原始 OA 基线、最后确认与待确认 OA 状态、已完成候选前缀、actions 和 notes。

恢复时先拒绝 completed checkpoint、task/token/adapter 不一致和非前缀候选；Bridge 外部恢复后重新 probe，并用 `schematic.inspect.resume` 独立回读 OA。candidate record 的任务声明字段必须精确匹配；允许额外保存仿真实际使用的 canonical OA 补全字段，但字段名只能来自 checkpoint 初始 OA 基线且值必须匹配，未知或被篡改字段仍拒绝。只有当前 OA 参数属于原始基线、最后确认/待确认写入或任务声明候选时才继续。已完成索引直接跳过；全部候选完成后若最终写回中断，`next_candidate_index` 保持在末尾，只重试选优、写回和最终回读。checkpoint 只有在最佳点提交或基线恢复且 `schematic.inspect.after` 一致后才标记 `complete=true`。

2026-07-19 的 Gate 1R live 任务经历 3 次随机 SSH/tunnel 中断，分别从候选 2、4 和 `next_candidate_index=10` 恢复，最终形成单一的 1–9 候选前缀、成功最佳写回和独立 OA 回读。VDA 的显式恢复语义因此已验证。随后在 Bridge 的备份隔离分支 `codex/vda-transport-recovery` 上用原子提交 `9e52844` 修复 Windows stale PID 判断与 `VirtuosoClient.from_env` 遗漏的 `warm()`；保留 stale state、强制终止精确 listener PID 后，第二次只读 OA inspect 自动建立新 tunnel 并得到一致回读。该结果只闭合“调用边界发现 tunnel 已死后的重建”，不证明运行中 SSH 上传/仿真的随机 reset 已消失。VDA 不删除或复制 Bridge 的 SSH/SCP 实现，补丁范围和上游兼容流程见 `docs/third-party/virtuoso-bridge-local-patch.md`。

后续 Bridge 提交 `f8fdb9e` 对已有幂等 SSH command/upload/download 重试加入 1 秒、3 秒有界退避，`2f41293` 则把 `connect()` 在 `sendall()` 前的拒绝标成私有 pre-send 错误，允许 managed client warm 并重试一次。安全边界取决于“payload 是否可能已发送”：pre-send 可自动恢复；send/recv 之后一律不重放 SKILL，由 VDA checkpoint 暂停并在新进程中核对 OA 后恢复。9 点压力任务在候选 8 的 pre-send connect refusal 处暂停并成功恢复到 9/9；同-client 强制断链 smoke 又直接验证了 pre-send 自动恢复。单次证据不外推为网络永不掉线。

## 共源 Gate 2 DC 与 AC 路径

共源级沿用同一个 adapter port、worker 边界和 checkpoint 状态机，没有增加第二套执行框架：

```text
目标 OA schematic: MN0 + analogLib/RD0
  -> Bridge 结构/连接/W/L/R 回读
  -> si -batch 结构网表
  -> 解析 MN0/RD0 端口、master、W/L/R，并与 OA 比对
  -> 只含 VDD/VIN/VSS source 和 dcOp/info 的 wrapper
  -> Spectre PSFASCII 节点电压 + MN0 operating-point 标量
  -> Id/VGS/VDS/VDSAT/gm/gds、KCL、饱和余量和摆幅余量
  -> 规格判定、有限 W/Vbias 搜索、最佳 W 写回和 OA 回读
```

`RD0` 是设计的一部分，因此在 OA/`si` 中；`Vbias` 和 `VDD` 是 testbench 条件，保留在 wrapper，并按任务是否显式给出标成 `user_input` 或 `software_inference`。调优时 W/L/R 属于可写回 OA 的 canonical semantic parameters；偏置条件进入候选和 run record，但当前没有被伪装成 OA 属性。

Spectre 的通用 `dcOpInfo` 在当前 Bridge parser 中以器件聚合对象出现。VDA 没有修改或复制 Bridge parser，而是在 wrapper 中显式 `save MN0:ids/vgs/vds/vdsat/gm/gds`，使 Bridge 已有 PSFASCII 标量路径直接返回所需量。第一次未显式 save 的失败记录被保留；不会把存在 `dcOpInfo_MN0` 聚合对象误当成完整标量证据。

工作区分类不读取一个未验证的模型枚举值：`saturation_region` 由 Spectre 给出的 `VDS`、`VDSAT` 和 `IDS` 按显式规则推导，标为 `software_inference`；原始器件量、节点量和从它们计算的连续指标标为 `eda_result`。Gate 2A 已在 `vb_pdk_smoke/vda_cs_gate2a_001/schematic` 完成 6 点真实搜索和最终独立 OA→si→DC OP 复核。

源极退化沿用该路径而不复制 executor：结构回读动态返回 `topology_variant`；`si` parser 在同一 common-source action 中要求 nominal `MN0(OUT IN VSS VSS)` 或退化 `MN0(OUT IN NSRC VSS)`+`RS0(NSRC VSS)`，并把 RS0.r 纳入 OA/网表参数一致性；DC wrapper 额外保存 NSRC，器件 VGS/VDS 改由 NSRC 计算，同时核对 MN0/RD0 与 MN0/RS0 两组 KCL。`source_resistance_ohm` 可直接进入原有有限 `parameter_space`。2026-07-20/21 的真实 smoke 已完成原位 add、DC/AC、W/RD/RS 搜索、多 analysis 质量组合、checkpoint 恢复和最佳回读；2026-07-22/23 又闭合 L/VDD、固定 PVT、可选 PVT-aware bias、add/remove 可逆拓扑，以及 `MN0.fingers` 原始 CDF 两点搜索和最佳写回。尚未闭合的是 PVT-aware OA 设计参数 live 写回、更复杂 CDF callback 组合和更复杂拓扑。

共栅级继续走同一个 common-source action，而不是新增一套 netlister 或 executor。其结构变体必须精确为 `MN0(NCAS,IN,VSS,VSS)`、`MNCAS(OUT,VCAS,NCAS,VSS)` 与 `RD0(VDD,OUT)`，并存在独立 `VCAS` pin；混入 RS 或其他 MOS 会拒绝。OA semantic 增加共栅管 W/L，`cascode_bias_v` 只属于 testbench。`si` parser 要求两只 MOS 的 model、W/L 和端子顺序匹配回读；DC wrapper 另外保存 `VCAS/NCAS` 与 `MNCAS:ids/vgs/vds/vdsat/gm/gds`，逐项检查输入管↔共栅管、共栅管↔RD、RD↔VDD supply 的电流一致性、两管饱和余量和堆叠余量。AC 只有 VIN 带 unit AC，VCAS 是纯 DC 源，避免把共栅偏置误作第二输入。真实 Gate 已完成增量 OA 写入、独立 pin/placement/CDF 回读、9 点 DC、相同 9 点 AC、checkpoint resume 和 exact inverse；恢复前后普通共源网表以及全部已记录 DC/AC 标量一致。该结论只覆盖 nominal `top_tt` 与声明离散域。

候选分析现在增加一条 theory-first 快路径。`vda small-signal-from-run` 不根据
`topology_variant` 选择共源或共栅解析式，而是读取一个成功 real-Bridge action 中的
结构化 `si` MOS/R 图、DC 节点和器件 OP，再把每只 MOS 的 `Id/gm/gds/gmb`、signed
`dQi/dVj` 与独立 `cjd/cjs` 规范化成 `eda_operating_point` artifact，交给既有通用
`Y(f)` 矩阵核心。policy 只声明 polarity、AC 固定边界、输入/输出线性表达式、外部电容
和频率网格；run/action/netlist/policy 均有 SHA-256 绑定。它的主要用途是在新拓扑先取得
一个必要的 DC 工作点后，用局部导数筛掉明显不值得跑 AC 的候选，再只对 shortlist 做
Spectre AC/noise/transient。它不是独立 DC 求解器，也不能从一个 OP 外推大范围非线性变化。

2026-07-27 用此前已经存在的普通共源与共栅 AC run 做了零远端调用的校验：通用矩阵
分别预测低频增益 `4.59574/6.39965 V/V`，已有 Spectre 为 `4.58848/6.44124 V/V`，
误差 `0.158%/0.646%`；预测/实测增益提升为 `39.25%/40.38%`。因此默认不再追加一组
noise/linearity A/B 来“发现”共栅会提高低频输出电阻。旧 run 没有保存 `gmb` 或动态
电荷导数，级联结果明确标为 partial，BW/GBW/noise/非线性仍未由该理论产物覆盖。为使
后续一次 DC 能支持更完整的频率预测，common-source wrapper 已补请求两管的
`gmb + dQi/dVj + cjd/cjs` 并将返回值纳入 OP evidence；这一新增采集行为当前只有本地
deck/parser 测试，尚无 live 证据。

AC 没有第二套 topology、netlister 或 executor。相同 wrapper 保留 `dcOp/info`，把 VIN 设为 DC bias + unit AC source，可选加入任务声明的 `CL0=load_ff`，再运行对数 AC sweep。Bridge 现有 PSFASCII parser 原样返回 `ac_freq/ac_IN/ac_OUT` 的复数向量；VDA 不修改 Bridge，也不把幅度解析复制回第三方库，而是在 worker 内计算复数传递函数 `H(f)=VOUT/VIN`。

低频参考定义为前 `reference_points` 个复数 H 的均值，并要求该窗口的幅度变化不超过 `max_reference_variation_db`。带宽是相对该参考下降半功率（`10 log10(2)` dB）的首个向下交点，按 dB 对 `log10(f)` 插值；`gain_bandwidth_product_hz` 明确定义为低频线性增益乘该带宽。`unity_gain_frequency_hz` 则是首个向下 0 dB 交点，单独报告，不能与 GBW 混用。非单调响应若有多个 −3 dB 交点，保留“采用首个下降交点”和再次穿越警告。

AC 核心结果只有在 DC 工作点为饱和、低频参考足够平坦且扫频内存在 −3 dB 交点时才标为 `analysis_complete=true`。空/非有限/长度不一致的复数波形直接失败；扫频上限不足或参考窗不平坦则保留已有 gain/phase 和诊断，但候选为不完整、run 至少为 `partial`，不会用 stop frequency 伪造 bandwidth/GBW。unity-gain 不在扫频内只作为独立警告；若任务把它列为 constraint/objective，则缺失指标仍使候选不可行。

2026-07-20 的两次只读 live smoke 均返回 271 点复数 AC，参考窗平坦且 −3 dB/0 dB 各只有一个向下交点。nominal 得到 `gain=4.022 V/V`、`bandwidth=5.632 GHz`、`GBW=22.653 GHz`、`unity=21.970 GHz`；退化点得到 `gain=3.238 V/V`、`bandwidth=4.175 GHz`、`GBW=13.519 GHz`、`unity=12.948 GHz`。两点均通过 DC 饱和、KCL、OA/`si` topology 与参数一致性。两点 W/RD 不同，因此该数据只验证执行链，不作为 RS 的控制变量因果比较。

有限搜索继续使用同一 candidate/checkpoint 状态机。搜索含 W/L/RD/RS 时逐候选写 OA、回读、重新 netlist，并提交最佳可行点；若维度只有 bias/VDD/load 等 testbench 条件，planner 把 stage/finalize 标成只读，executor 不调用 OA 参数写入，只在最终独立 inspect 中证明 schematic 未变。这既保留局部仿真/条件搜索，也避免为纯分析条件索取不必要的 OA 写授权。

该只读分支已在 nominal 与退化 cell 上各完成 6 点 `bias×load` live 搜索。12 个候选均重新生成一致的 OA/`si` 网表并得到完整 AC 指标，最终都选择 `0.35 V/1 fF`；before/after semantic parameters 相同。退化任务在候选 4、5 前两次失去 tunnel，并从 checkpoint index 4、5 恢复，证明 testbench-only 搜索也使用同一可审计恢复语义。

含 OA 设计参数的分支随后在专用 `vda_cs_ac_tradeoff_001` 上真实验证。相同 W/L/RD/bias/load 的 nominal 控制点保存后，固定 transform 只加入 RS，避免把不同 W/RD 的历史 cell 误作因果对比。W/RD/RS 8 点搜索逐候选写入、回读、重新 `si`、运行 DC+AC，并按 GBW 选择 W=1.0 µm、RD=20 kΩ、RS=1 kΩ；最佳立即回读和独立 after-inspect 相同。3/8 预算任务只在完成前缀选点并标为 partial；人为不可行任务恢复初始 OA。upload/download 中断不产生候选证据，恢复前先执行参数恢复或核对 expected OA。

设计质量分析继续复用这一 worker。DC/AC/transient/noise wrapper 均保存 `VDD_SRC:p`；DC 功耗由实际电源源电流计算，并与 MN0 `ids` 做独立 KCL 检查，不以器件 Id 直接代替电源功耗。`analysis: transient` 要求结构化 `linearity_sweep`：VIN 为以 bias 为中心的正弦，一个 Spectre nested parameter sweep 覆盖全部声明幅度；稳态整数周期上以梯形积分做傅里叶投影，提取 fundamental、HD2/HD3、THD、平均 VDD 功耗和 P1dB。若幅度范围没有包围目标压缩量，P1dB 保持 unresolved，不用最大幅度冒充。wrapper 比声明 measurement 边界多运行一个 strobe interval，以适配 Spectre 不保证返回 stop 闭区间端点的行为；指标窗口本身不缩短。

`analysis: noise` 要求结构化 `noise_sweep`，wrapper 使用 `VIN_SRC` 作为 input probe 并保存普通 noise PSF。Bridge runner 原样负责 Spectre、远端目录和完整 PSF 下载；其通用目录合并器目前不返回普通 noise trace，因此 VDA worker 从 Bridge 已下载的唯一 `noise.noise` 文件调用 Bridge 自身单文件 PSF parser，再对 `out` 与 `in` 电压噪声密度平方积分。解析后的 PSFASCII 通过 Bridge 上传到本次保留的 netlist scratch，远端路径和 SHA-256 进入证据。该实现没有改 Bridge，也没有另写 SSH/文件传输。

真实 nested sweep 还证明通用目录合并数据不能作为跨 analysis DC OP 的唯一来源：递归的 sweep `dcOpInfo` 可能覆盖根文件，而 `dcOp.dc` 节点仍来自根 analysis。VDA 因此从 Bridge 已下载目录显式选择相对深度最小的根 `dcOp.dc`/`dcOpInfo.info`，分别调用 Bridge 单文件 parser，并把两个 SHA-256 写入 operating-point evidence；不以放宽节点/器件一致性容差掩盖来源混淆。

2026-07-20 的同一专用 OA cell 已通过 5 点 100 MHz transient linearity 和 211 点 1 kHz–10 GHz ordinary noise 只读 smoke：P1dB 被 50/100 mV 点真实包围，输入 P1dB 为 88.32 mV peak，150 mV 点 THD 为 13.16%；输出/输入参考积分噪声为 3.304/0.983 mV RMS。两次 `si` 网表 SHA 相同，OA/网表 W/L/RD/RS 一致且没有 OA write action。该结果升级的是单点执行与提取能力，不是跨 analysis 质量驱动调优或 corner 闭环。

`analysis: "quality"` 在 VDA worker 内把 AC、transient linearity 和 noise 组成一个固定原子证据门，而不复制 executor 或修改 Bridge。每个候选只读取一次 OA、生成并核对一次 `si` 结构网表；三个独立 wrapper 都引用这一远端网表。合并前要求实际参数表完全一致，重复 DC 指标在数值容差内一致，且同名指标的证据来源一致。任一子分析 `analysis_complete=false` 会使整个候选不可行；参数或共享指标不一致则停止合并，不平均、不以后一次结果覆盖。run record 顶层保存组合完成状态，并在 `evidence.analyses` 下分别保留 testbench、DC OP、响应诊断和工具版本。组合选择和一致性判断是 `software_inference`，OA 是 `bridge_readback`，`si`/Spectre 连续指标仍是 `eda_result`。

2026-07-21 的真实 4 点 `bias_v×load_ff` 质量搜索已验证该路径：四个候选均复用各自的一份 OA/`si` 网表完成三项分析，结构网表 SHA-256 全部一致；两个 0.40 V 点虽包含全网格最高 GBW，却因 THD 和实际 VDD 功耗超限而被拒绝，最终选择 0.35 V/1 fF。2/4 预算任务没有把前缀最优包装成全空间最优，全不可行任务没有写 OA；一次首候选 `si -batch` transport 失败被保留为 `system_event`，从独立 OA 回读后用同一 checkpoint 恢复。worker 现在还会把失败 `si` scratch 的保留路径写入错误，便于远端审计；该修正仍位于 VDA 边界，没有修改 Bridge。

同日的 8 点 W/RD/RS 质量搜索把上述证据门接入原有 OA candidate staging：每个候选先写入并回读，再从该 OA 生成一份网表供三项分析复用。候选 4 的 Spectre upload timeout 后，自动恢复 readback 也失败，checkpoint 因此保留 3 个完成候选和未确认 OA 状态；连接恢复后 `schematic.inspect.resume` 发现 OA 仍为候选 4，只重跑 4–8，最终写回并回读 GBW 最优点。全不可行任务恢复初始 OA 后，run record 不再把“最接近但未提交”的候选放进 `selected_parameters`；尝试证据仍完整保留在 `candidates`。另一个只改变 objective 的两点任务自动把 RS 从 1 kΩ 写为 2 kΩ，证明 THD 优先时同一工作流会选择不同于 GBW 优先的设计，而不是只生成更多指标。

2026-07-22 的 L/VDD Gate 在同一 `vda_cs_ac_tradeoff_001` 上固定 W=1 µm、RD=20 kΩ、RS=2 kΩ、bias=0.35 V、load=1 fF，搜索 `L=[0.03,0.04] µm × VDD=[0.8,0.9] V`。L 属于 OA 设计参数，逐候选写入、回读并进入 `si` 网表；VDD 只存在于三个 testbench wrapper。四个候选均完成 AC/transient/noise 且通过约束，GBW objective 选择 `L=0.03 µm/VDD=0.9 V`，最终只把 OA 子集写回并独立回读。两种 L 分别产生不同 netlist SHA，同一 L 的两个 VDD 点复用相同结构 netlist SHA，证明两层参数契约按预期分离。

随后 fixed-design PVT Gate 对这一 OA 只读执行 TT/25℃/0.90V、SS/125℃/0.81V、FF/−40℃/0.99V。`nics4304_tsmc28` profile 为每个角显式列出 MOS/MOSCAP、res/bip/dio/disres、MOM 和 metal-R 四个 section；wrapper 另写 `simulatorOptions temp=...`。三条件共九个唯一 wrapper 共享一份 `si` 网表和 OA readback，各项分析完整、全部约束通过；SS 是 GBW 等多项指标的最坏角。run record 的 testbench evidence 保存 profile、角名、温度、四个 include path/section、wrapper SHA 和来源，避免只凭角名推断实际模型输入。该 Gate 是有限三条件验证，不是完整 foundry signoff corner set 或 Monte Carlo/mismatch。

2026-07-23 的可选 PVT-aware Gate 把相同三条件接入 `design.tune`，只搜索 `bias_v=[0.35,0.40] V`，因此没有 OA 参数 action。两个候选各自完成九项分析且各自只生成一份网表；两份网表 SHA 也相同，符合设计未变、testbench bias 改变的契约。`0.40 V` 虽有更高的最坏 GBW，但在 TT/SS/FF 分别违反 THD、摆幅和功耗约束，最终选择所有条件都可行的 `0.35 V`。调优前后 OA semantic parameters 完全相同。OA 设计变量跨 PVT 的 checkpoint/writeback、全不可行恢复和预算路径已有本地确定性测试，但仍需独立 live Gate 才能升级为真实 OA 写回证据。

同日的 raw CDF Gate 在全新 `vda_cs_topology_patch_001` nominal cell 上固定 Wfg=1 µm、L=0.03 µm、RD=10 kΩ、bias=0.35 V、VDD=0.9 V、CL=2 fF，只搜索 `MN0.fingers=["1","2"]`。两个点的 OA 定向回读、`si` 网表 `nf` 与总宽度分别为 1/1 µm 和 2/2 µm，网表 SHA 不同，均得到完整 DC+复数 AC。GBW 从 39.582 GHz 增至 58.375 GHz，两个点都通过本任务的饱和/KCL/gain/BW 约束，最终写回 fingers=2 并独立回读。首个点第一次在 `si -batch` 遇到 `WinError 10054`，未产生候选；VDA 恢复 RD=20 kΩ/fingers=1，随后从 checkpoint index 1 重试并保留失败 action。这个 Gate 证明明确点名的实际 CDF 字段可以进入同源有限搜索，不证明 PDK 的 233 个 MOS 字段都可持久化、物理独立或适合联合优化。

## 差分对 Gate 3 nominal same-source 路径

Gate 3 沿用现有 task、adapter、`si`、Spectre 和 checkpoint 机制。首个固定 DUT 是电阻负载 NMOS 差分对：`MN0(OUTP,INP,TAIL,VSS)`、`MN1(OUTN,INN,TAIL,VSS)`、`RD0(VDD,OUTP)`、`RD1(VDD,OUTN)`。OA 中显式保留 `TAIL` pin，不放测试用尾电流源；两个匹配共模输入源、VDD/VSS 和理想尾电流源都由本次 simulation wrapper 提供。这样同一 DUT 可以后续由人工 ADE 或另一个明确 testbench 驱动，不把自动化激励固化为设计拓扑。

canonical OA 参数只有 `input_width_um`、`length_um` 和 `load_resistance_ohm`，且 semantic 写入必须同时更新两只 NMOS 或两只负载；`tail_current_ua`、`common_mode_v`、`vdd_v`、可选 `tail_output_resistance_ohm` 和每端对称 `load_ff` 是 testbench 参数，`schematic.create`/`parameters.apply` 拒绝把它们伪装成 OA 属性。显式 `instance_parameter_updates`/`instance_parameter_space` 仍可点名单个实例的实际 CDF 字段，但固定差分对 adapter 会在随后 topology/semantic/geometry 回读中拒绝破坏当前匹配模板的结果。该限制不收窄 `existing_schematic` 的 Bridge 参数透传能力。

同源链要求 OA 与 `si` 同时精确匹配四个 instance、端口和 master，并分别比较 MN0/MN1 的单指宽、`fingers/nf`、`m/multi`、总宽和 L，以及 RD0/RD1 的 R。wrapper 保存七个节点、VDD/TAIL source current 和两只 NMOS 的 `ids/vgs/vds/vdsat/gm/gds`。结果解析先核对输入/电源设定值和每只器件的节点 VGS/VDS，再独立检查支路和、尾源、电源源以及两只负载电流；任一 KCL 残差超过 1% 都停止，不把它当作不满足规格的普通候选。连续 OP/KCL/功耗值来自 `eda_result`，OA 回读来自 `bridge_readback`，双管饱和分类和一致性判断是 `software_inference`。

2026-07-23 在新 `vb_pdk_smoke/vda_diffpair_gate3_001/schematic` 上完成 non-overwrite live Gate。nominal create/inspect、单点 DC、9 点尾电流×共模只读搜索、2/9 预算、全不可行恢复，以及 18 点 W/RD/尾电流设计搜索均复用现有 executor。设计搜索在候选 3 和 9 的 transport reset 后独立回读 OA 并续跑，最终 18/18 完成并写回 `W=2 µm/L=30 nm/RD=8 kΩ`；checkpoint 和最终 OA 回读一致。该状态证明 bounded writeback/recovery，不证明任意差分拓扑或参数空间。

动态 analysis 继续复用同一 OA→`si` 网表。平衡差模 AC 使用 `+0.5/-0.5` V 小信号源，从 `(OUTP-OUTN)/(INP-INN)` 提取低频增益、首个 -3 dB 带宽、GBW 和 unity；最终 OA 的无额外负载结果为 `2.992 V/V`、`29.18/87.25/86.80 GHz`。四点 `tail_current_ua × load_ff` 只读搜索以 GBW 选出 `50 µA/0.5 fF`，OA 前后不变。`load_ff` 在 OUTP/OUTN 各放一个对称 wrapper 电容，不进入 DUT。

CMRR 不能在理想尾源上直接宣称。任务只有显式提供 `tail_output_resistance_ohm` 时，才在外部 ideal DC sink 并联该电阻，分别运行平衡差模与同相共模 AC；两次运行只生成一份 `si` 网表，并要求 DC OP 和频率网格一致。CMRR 定义为两条复数传输函数之比；带宽定义为 CMRR 相对低频参考首次下降 3 dB，而不是共模传输自身的低通带宽。真实 `1 MΩ` Gate 得到 `58.59 dB` 低频 CMRR 和 `306.38 MHz` CMRR 带宽；第一次错误要求共模自身 -3 dB 带宽的 run 保持 `partial`，修正后另建成功记录。

受控 transient 使用 Spectre nested `vindiff` sweep，VINP/VINN 分别为声明差分峰值的 `+0.5/-0.5`，并逐点检查实际 `VINP-INN` 基波。指标从 `VOUTP-OUTN` 相干窗口提取差分增益、HD2/HD3、THD、P1dB；VDD 源积分给出全电路功耗。100 MHz/每端 1 fF 的 7 点 live sweep 解析到输入 P1dB `110.9 mV peak`、输出 P1dB `293.5 mV peak`。输入共模 12 点只读 DC 则得到采样通过区间 `0.30–0.875 V`，并在 `WinError 10054` 后从 9/10 checkpoint 恢复；这只是理想尾源和当前 50 mV 余量门下的采样结论。

## 差分对 Gate 4 真实尾管路径

Gate 4 不重建差分对模板，也不修改 Bridge。`schematic.transform` 的 `add_tail_device` 只接受已精确匹配 Gate 3 core 的 cellview，并以 append 模式新增 `MNTAIL(TAIL,BIAS,VSS,VSS)` 和输入 pin `BIAS`。保存前后都独立 inspect；delta 必须保持原 `MN0/MN1/RD0/RD1`、全部既有连接、pins 和 placement，只允许新增一个尾管、一个 pin 及其必要连接。创建仍只建立 nominal core，因此尾管是显式、可审计的拓扑小变更，而不是隐藏在另一个模板中。

真实尾管拓扑把 `tail_width_um/tail_length_um` 加入 OA semantic/readback/`si` 一致性契约；`tail_bias_v` 仍是外部 BIAS 电压，不能写入 OA。它与理想尾源的 `tail_current_ua/tail_output_resistance_ohm` 互斥。参数应用和有限搜索可只改 MNTAIL W/L，也可以只扫 BIAS/VCM 等 testbench 条件；前者按候选暂存、回读、checkpoint 和最佳写回，后者保持 OA 只读。`si` parser 同时要求 MNTAIL master、节点顺序、W/L/fingers/multiplicity 与 OA 匹配。Spectre DC 从 MNTAIL 自身读取 `ids/vgs/vds/vdsat/gm/gds`，尾电流 KCL 必须匹配两支路和，不再用理想源设定值冒充实际电流。

AC 在同一 `si` 网表上分别运行平衡差模和同相共模 wrapper，二者都只给 OA `BIAS` pin 提供电压，并要求 DC OP 与频率网格一致。CMRR 由两条复数传输函数相除；真实尾管小信号输出电阻自然进入结果，不再由 wrapper 并联人为 `rtail`。transient 继续使用平衡差分 nested sweep。noise 使用唯一独立 `VIN_DIFF(VDIFF,0)` 作为 `iprobe`，再由 `+0.5/-0.5` 理想 VCVS 把它叠加到精确 VCM；这避免两个独立输入源的输入参考歧义，也避免 1 TΩ 共模偏置在真实求解中产生数值漂移。

`analysis_complete` 只表示所需 OP/波形/标量和解析是否齐全，不再把“MNTAIL 或输入支路不在饱和区”混成证据缺失。工作区状态保留为 `software_inference` metric 和明确 warning，并由任务 constraint 判可行性。2026-07-23 的真实 ICMR 首轮因此暴露了旧归类问题；修正后的 10 点重跑把 0.35 V 正确记录为“分析完整但规格不可行”，其余点不受放宽。完整结果和未验证边界见 [`validation/2026-07-23-differential-pair-real-tail-gate4-live.md`](validation/2026-07-23-differential-pair-real-tail-gate4-live.md)。

## 差分对 Gate 5 对称源极退化路径

Gate 5 继续在同一真实尾管 cellview 上做 exact-delta，而不是复制差分对模板或仿真器。add 前必须精确匹配 Gate 4 拓扑；add 后只允许新增 `RS0/RS1` 与内部网 `NSP/NSN`，两管、尾管、负载、顶层 pins 和未点名 placement/参数保持不变。`source_resistance_ohm` 是对称 OA semantic 参数：应用或搜索时必须同时写两只电阻并逐只回读；任一电阻缺失、节点错误或数值不等都会在 OA inspect 或 `si` parser 阶段停止。

自动网表明确要求 `MN0(OUTP,INP,NSP,VSS)`、`MN1(OUTN,INN,NSN,VSS)`、`RS0(NSP,TAIL)`、`RS1(NSN,TAIL)` 和原 `MNTAIL(TAIL,BIAS,VSS,VSS)`。DC 额外保存 NSP/NSN，并从各自 `(NS?-TAIL)/R` 重算电阻电流；两只源电阻分别与对应 MOS 支路核对，误差上限 1%。VGS/VDS/饱和区也从真实支路源节点计算，不能继续错误使用公共 TAIL。AC/CMRR/ICMR/transient/noise 与 checkpoint/有限搜索沿用 Gate 4 状态机，故局部 RS 调整能够进入现有全部分析，而不是一条专用 smoke 脚本。

2026-07-23 的 live Gate 在 `vda_diffpair_deg_gate5_001` 上完成 add、500 Ω 单点全分析、250/500 Ω transient 两点调优与最佳 OA 写回、remove、恢复 DC，并将整个 add/remove/restore 序列重复一次。两次 remove 后 placement SHA 都精确恢复为 add 前值；两次恢复网表 SHA、semantic 参数和所选 DC metrics 完全相同。完整证据与权衡见 [`validation/2026-07-23-differential-pair-source-degeneration-gate5-live.md`](validation/2026-07-23-differential-pair-source-degeneration-gate5-live.md)。

## 差分对 Gate 6 PMOS 电流镜负载路径

Gate 6 仍是固定 exact-template delta，不是任意拓扑综合。前向 action `replace_resistive_load_with_current_mirror` 只接受 Gate 4 的未退化真实尾管拓扑，保持 `MN0/MN1/MNTAIL`、全部顶层 pins/nets 和未点名参数不变，只把 `RD0(VDD,OUTP)`、`RD1(VDD,OUTN)` 替换为 `MP0(OUTP,OUTP,VDD,VDD)`、`MP1(OUTN,OUTP,VDD,VDD)`。`pmos_load_width_um` 与 `pmos_load_length_um` 是对称 OA semantic 参数，可由 `parameters.apply` 或有限搜索同时写入 MP0/MP1；任一 PM 缺失、W/L 不等、master/node 错误，或 RD 与 PM 混合存在，都会在 OA 或 `si` 边界拒绝。

反向 action `restore_resistive_load` 明确接收 `load_resistance_ohm`，删除两只 PMOS 及 VDA 自有 terminal stubs，在原位置重建 RD0/RD1；可选 `expected_restored_placement_sha256` 用于声明并核对恢复基线。两个旧的专用 transform 仍保持互斥，避免在一个专用 action 中同时改变负载与源极网络；组合拓扑改走 `existing_schematic` 的预声明 topology-delta：只在已回读的 Gate 6 结构上增加 `NSP/NSN`、重连 `MN0.S/MN1.S` 并加入 `RS0/RS1`。2026-07-26 已在新 `vda_diffpair_active_deg_generic_001` 上完成 live：before/after SHA 为 `d3fe4b73...31b93`/`68c9d2e2...559a`，七实例 OA/`si` 参数、PMOS/RS/尾管 KCL、DC/AC/CMRR/noise/transient/十点 ICMR/PSRR 均走原分析器，随后 inverse 与独立 readback 精确恢复 before SHA，恢复态 DC 再次通过。该结果证明组合与分析迁移，不等于任意 topology transaction 或设计质量闭合；P1dB 未包围、PSRR 仅约 `11–13 dB`，PVT/mismatch 也未运行。

电流镜负载把动态输出定义从 Gate 3–5 的 `OUTP-OUTN` 改为差分输入到单端输出 `OUTN/(INP-INN)`；`OUTP` 是二极管连接的镜像参考。差模 AC、同相共模 AC 和 CMRR 都显式使用这个输出契约；transient 从 OUTN 提取基波/THD/P1dB；noise 使用 `noise (OUTN 0)` 与原唯一 `VIN_DIFF` 输入参考；`load_ff` 只加在 OUTN。指标名继续保留 `differential_*`，其中 differential 描述输入方式，证据中另存 `output_mode=single_ended_outn`，不得误读为差分输出。

DC 除 MN0/MN1/MNTAIL OP 外还保存 MP0/MP1 的 IDS/VGS/VDS/VDSAT/GM/GDS。软件层分别核对 PMOS 节点与器件 OP、每只 PMOS 电流与对应 NMOS 支路、两只 PMOS 的镜像误差、VDD 源电流、PMOS 饱和余量，以及上下管联合输出摆幅余量；KCL residual 超过 1% 是证据失败，工作区或设计规格不通过则是完整但不可行的候选。2026-07-23 已在全新 `vda_diffpair_active_gate6_001` 上完成真实 create→tail→active-load、OA/`si` 一致性、DC、AC/CMRR、ICMR、transient、noise、bias/load 搜索、Wn/Wp 搜索、预算、全不可行、transport resume、最佳写回、精确 restore 和最终 active-load 重建。最终同一网表 SHA 得到 `3.7421 V/V` 增益、`2.9756 GHz` 带宽、`11.1351 GHz` GBW 和 `34.8451 dB` CMRR；该结论只覆盖 nominal `top_tt` 与声明的有限网格。详见[本地契约记录](validation/2026-07-23-differential-pair-current-mirror-gate6-local.md)和[真实验证记录](validation/2026-07-23-differential-pair-current-mirror-gate6-live.md)。

PSRR 作为正交 analysis 接在同一 Gate 6 输出契约上，而不是另建 netlist 模板。一次候选先生成并核对唯一 OA/`si` 网表，随后运行平衡差模、VDD 1 V AC 注入、VSS 1 V AC 注入三份 wrapper；注入供电时 INP、INN 与 BIAS 都保持理想对地 DC 参考。三次运行必须具有相同 DC 工作点和频率网格。对 active-load 的 `OUTN` 单端输出定义 `Ad=OUTN/(INP-INN)`、`Avdd=OUTN/VDD`、`Avss=OUTN/VSS`，再计算 `PSRR+=|Ad/Avdd|` 和 `PSRR-=|Ad/Avss|`。run record 同时保留两种 supply gain、低频 PSRR、扫频最差值和首次下降 3 dB 频点；可选 `ac_sweep.evaluation_stop_hz` 只对 PSRR 开放，在 sweep start 到声明 stop 的实际采样频带内分别计算正/负和组合最差值。越界值、其他 analysis 静默接收、零 supply transfer、网格漂移、DC 漂移、空波形或未包围 3 dB 交点均拒绝。PSRR worker 还在 Bridge 下载目录清理前选取最浅层根 `ac.ac`，对差模/VDD/VSS 三份文件分别记录相对路径、大小和 SHA-256；它绑定解析来源，但不等于长期保存完整波形。

只有 `tail_bias_v/common_mode_v/vdd_v/load_ff` 的搜索仍是纯 testbench 搜索，不写 OA。该契约先通过本地 synthetic worker/demo，2026-07-24 又在现有 Gate 6 cell 上完成 nominal 单点和 `tail_bias_v=[0.30,0.32] V × load_ff=[0.5,2.0] fF` 四点 live。四点共 12 次 AC，网表 SHA 相同，DC/网格一致，根 AC 文件清单完整；两个 transport 中断经 checkpoint 和独立 OA 回读恢复。四点都通过饱和、摆幅、增益、带宽和功耗护栏，但 `1 kHz–100 MHz` 最差 PSRR 只有 `11.4275–11.5125 dB`，未通过明确标为临时证伪门的 `20 dB`。`load_ff` 对带内值无可见改善，BIAS 提升只有约 `0.085 dB`；executor 因零可行点不选择参数，OA 前后不变。这证明 bias/load-only 路径不足，不代表最终产品规格已定义或闭合；详见[本地记录](validation/2026-07-23-differential-pair-psrr-local.md)、[单点记录](validation/2026-07-24-differential-pair-psrr-live.md)和[带限搜索记录](validation/2026-07-24-differential-pair-psrr-search-live.md)。

获显式 OA 写授权后，同一 `design.tune` 状态机又完成 `length_um × pmos_load_length_um × tail_length_um` 的 `0.03/0.06 µm` 八点 Gate。每点先同时写回匹配器件并立即 OA 回读，再生成一份不同 SHA 的 `si` 网表，运行差模/VDD/VSS 三次 AC；8/8 候选均 `analysis_complete`、无 issues/warnings，并分别绑定三份根 `ac.ac`。PMOS L 的两水平平均改善约 `5.20 dB`，输入对 L 改善约 `3.27 dB`；二者同为 `0.06 µm` 时达到 `19.712–19.744 dB`。尾管 L 的平均影响为 `-0.19 dB`，却把平均带宽从 `2.033 GHz` 降到 `0.581 GHz`、平均 GBW 从 `10.996 GHz` 降到 `3.130 GHz`，因此后续不应继续把它当主 PSRR 旋钮。全部点仍只因临时 `20 dB` 门失败，executor 没有提交 objective 最大点；一次候选 3 写后回读 `WinError 10054` 先恢复基线，再从 checkpoint index 3 独立回读续跑，最终恢复三组 `L=0.03 µm` 并由额外只读 inspect 复核。该 Gate 证明 OA 几何确实能显著改变同源 PSRR，也证明不可行恢复语义；它没有定义产品 PSRR 规格，也没有对近门点完成 CMRR、线性度、噪声、PVT 或 mismatch 复核。详见[长度搜索记录](validation/2026-07-24-differential-pair-psrr-length-search-live.md)。

live 首版曾把 PMOS 实例命名为 `PM0/PM1`；OA 和 `si` 一致，但 Spectre 将 `P` 前缀解析为 port primitive 并报 `SFE-1703`。VDA 先精确恢复 RD 基线，再将该固定模板统一改为 Spectre 安全的 `MP0/MP1`。worker 同时增加 Spectre 失败详情提取，把 `spectre.out` 错误上下文写入 run record；这类失败属于执行/网表错误，不能被分类为电路规格不可行。

## 进程与资源生命周期

Bridge subprocess adapter 的本地进程边界是一个请求一个 Python worker，不经过 PowerShell。Windows worker 使用隐藏窗口和独立进程组，并在创建后立即加入本次请求专属的 Job Object。每个请求另有唯一 cancel marker 和调用方 PID；timeout、`KeyboardInterrupt` 或调用方消失时，worker watchdog 先中断主线程，让 action/worker `finally` 恢复 Maestro runtime、关闭 session/client 并删除 marker。协作窗口最多 30 秒，随后仍以 `TerminateJobObject` 回收 worker、SSH/SCP/tar 等全部本地后代。正常返回也会先让 worker 完整执行 `finally` 和输出结构化结果，再清空本次 Job 中仍存活的 helper descendant，避免成功路径遗留端口转发。若 Job Object 无法建立，请求在执行 payload 前失败。POSIX 路径使用同一协作信号，再以独立 session/process group TERM→KILL 收敛。

显式 tunnel 生命周期也不再要求调用方从外层 PowerShell 直接启动 Bridge。`vda bridge start|status|stop` 只代理与 Bridge Python 同一虚拟环境中的公开 `virtuoso-bridge` console script；VDA 不解析连接配置、不实现 SSH，也不接管 Bridge state。Windows launcher 复用 worker 的 `CREATE_NO_WINDOW`、`SW_HIDE`、独立进程组和 Job Object 边界，stdout/stderr 则定向回当前 VDA 终端。默认过滤 Bridge 的 `[cmd]` 原始命令，只显示阶段、warm/状态结果和返回码；`--verbose` 才回显诊断。正常完成只关闭短生命周期 launcher 的句柄，不终止已建立的共享 tunnel；启动过程中 Ctrl+C 则终止仍归本次 Job Object 所有的进程树。该入口是运行体验和进程所有权能力，不产生 `bridge_readback` 或 `eda_result`；只有其委托的 `status` 输出能作为即时工具状态诊断。

worker 内部把本次创建的 `VirtuosoClient`/`SSHClient` 注册为资源，action 无论成功还是异常都逆序显式 `close()`；资源对象向外抛出的关闭异常会使 worker 结构化失败，不会静默吞掉。这里的 `close()` 只释放本次 runner/persistent shell，不调用会影响其他脚本的 `stop()`；共享 tunnel 由独立 `vda bridge start` launcher 按 Bridge 原语建立，不属于请求 worker 的 Job，并一直保留到显式 `vda bridge stop`。Maestro action 另在各自 `finally` 中关闭 background session；direct Spectre 使用 `TemporaryDirectory`，正常/异常 Python 展开时清理本地网表与下载目录。2026-07-31 的真实复核中，资源盘点前后共享 SSH PID 不变且没有新增 worker 后代，`bridge stop` 后 SSH 与 VDA/Bridge Python 进程均为 0。

远端进程不能只靠关闭本地 SSH 来证明结束。direct inverter/common-source/differential-pair/device-characterization 在各自唯一 `/data/xum` 根下上传小型 `vda_spectre_guard.sh`，执行位和 SHA-256 都通过 Bridge 独立回读。guard 以任务 timeout 运行 Spectre，先发 TERM，10 秒后仍未结束则 KILL；Bridge transport 等待比该上限多 15 秒。SpectreSimulator 的 `remote_work_dir` 同时固定到该 VDA 根，因此子运行目录不会散落成无所属的顶层 nonce；成功下载后 Bridge 清理子目录，失败诊断和同源 netlist 根按证据策略保留。

资源分成两类，不能混淆：本地 `vda_*` temp、worker 和一次性 SSH/SCP 是退出时必须归零的临时资源；`artifacts/runs`、远端 `si` 网表/wrapper/guard、characterization raw bundle 和 ADE exact-history manifest 是有意保留的证据。后者会占磁盘，删除必须按精确路径和保留策略显式执行，不能在 worker 退出时广泛 `rm -rf`。`vda resources [--remote]` 现在提供只读 age/size/process/session inventory；可选 schema-v1 pin manifest 只接受 artifact-root 顶层名称或 `/data/xum/...` 精确路径，只抑制 review candidate，永远不设置 delete authorization。真实 Maestro timeout 故障注入已经证明 runtime restore 与 session close，随后独立 inventory 为 0；用于注入的私有 action 已从产品代码删除。详见[首轮生命周期审计](validation/2026-07-25-process-resource-lifecycle.md)和[follow-up Gate](validation/2026-07-25-resource-cancellation-retention.md)。

## 证据链

每次运行至少保存任务和计划 token、adapter 与证据来源、动作状态、候选参数、仿真指标、逐条规格判定、最终选择、结构化 `search_audit`、OA 回读摘要，以及错误和未验证边界。显式实例写入还保存请求、写入前目标字段、立即确认和独立 inspect 的完整参数表。ADE `prepare` 保存 design/test/simulator 请求、持久化 view/test 回读、未覆盖既有 view 以及没有设置 analysis/sweep 的范围；`capture` 保存焦点目标、是否已保存、setup/simulation 聚合指纹、逐文件 manifest、history 选择来源以及可用时的逐点 output/spec；变量/setup patch 保存声明目标、全部旧值、即时值、独立重开值、targeted 前后指纹及未覆盖范围，并明确记录没有运行仿真；严格 sweep run 还保存 setup 前后 scope 指纹、每个 point 的 Detail 参数/非空 output、逐 test input/result hash、OA/input comparison hash 和逐点绑定指纹。存在显式 legacy output evaluation-error 契约时，还保存任务期望、RDB 实际错误单元格、completion-log 数量、逐项匹配和零未解释错误；启用 result mapping 时再保存 output expression 前后状态/指纹、显式 scale、映射后的候选、逐条 constraint 与 selection。调优 checkpoint 保留历史失败 actions，但恢复后只有完成的候选证据参与选择；最终 run 可以在完整证据和最终回读成立时成功，同时仍显式留下已恢复的 transport 事件。自动 netlisting 还保存远端网表/wrapper 路径、SHA-256、解析后的实例参数和一致性结论。

有限 PVT 还保存每个原始 condition 的完整 `CandidateEvaluation`、同一 OA/netlist identity、每个 analysis 的 testbench/model manifest、独立 noise PSF，以及跨条件 `all_conditions_required`/worst-case 聚合。缺一个条件、条件顺序或值与任务不一致、任一 analysis 不完整、netlist 漂移或 model corner 未映射都直接失败，不会降级为 nominal 结果。

timing、过冲/欠冲、`supply_energy_per_cycle_fj`、`average_supply_power_uw`、共源与差分对的 DC/供电/KCL 连续指标，以及从 AC、相干 transient、noise PSF 或 PSRR 三次 AC 中读取的原始波形/OP 标为 `eda_result`；OA 结构和参数（包括 MNTAIL W/L、RS0/RS1 与 NSP/NSN 连接、MP0/MP1 W/L 与电流镜连接）标为 `bridge_readback`；任务显式给出的 VDD、负载、偏置、尾电流或尾管 BIAS、有限尾源输出电阻、对称源电阻值、PMOS 负载 W/L、analysis 或 sweep 字段标为 `user_input`；默认 analysis/sweep 字段、`gate_area_proxy_um2=(Wn+Wp)L`、电阻或 PMOS 电流与 KCL 重算、镜像误差、饱和区分类、CMRR/PSRR 比值、交点/压缩点规则和指标完整性判断是 `software_inference`。供电能量或功耗保留积分窗口和源电流方向，不能称为纯动态开关能量；AC、linearity、noise 和 PSRR 指标也必须保存提取公式、范围、输出模式和 unresolved 诊断，不能只保存一个无来源标量。后续 Maestro、Calibre 和 PEX 沿用同一证据模型。

PVT 中的角名、温度和逐角 VDD 是 `user_input`；profile include 映射来自 `pdk_profile`，映射选择及 manifest 组合标为 `software_inference`；每角 Spectre 标量/波形指标仍是 `eda_result`；跨角保守 constraint/objective 值全部标为 `software_inference`。因此聚合最坏值不能被误读为某个单独 Spectre analysis 直接输出的标量。
