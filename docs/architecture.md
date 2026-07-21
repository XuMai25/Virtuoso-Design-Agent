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

VDA 默认从晶圆厂 CMOS PDK 出发。任务和 CLI doctor 共用 `DEFAULT_PDK_PROFILE=nics4304_tsmc28`，其当前工艺身份是 TSMC N28/`tsmcN28`。未来 TSMC、SMIC 等工艺各用独立 profile 绑定器件库、model、默认电压和远端路径，并单独通过 smoke；profile 之间不共享性能结论。TSV、hybrid-bonding 等封装/3D PDK 不参与默认选择或 fallback，必须由任务显式指定并使用专门 Gate。见[决策 0002](decisions/0002-foundry-cmos-pdk-default.md)。

## 任务与局部能力

任务不是固定的“全流程按钮”。`operation` 决定实际范围：

| operation | 作用 | 远端副作用 |
| --- | --- | --- |
| `schematic.create` | 建图并结构回读 | OA 写入 |
| `schematic.inspect` | 读取拓扑、参数、pins | 只读 |
| `schematic.transform` | 对已知拓扑应用可审计的小变更；当前覆盖共源源极退化和反相器 core→ADE testbench | OA 写入 |
| `parameters.apply` | 应用指定参数并回读 | OA 写入 |
| `ade.prepare` | 为已有 design 新建持久化 Spectre-backed Maestro view/test；拒绝已有 view | Maestro OA 写入 |
| `ade.capture` | 捕获人工聚焦并已保存的 Maestro setup、history 和已有真实结果 | 远端只读 + 本地证据写入 |
| `ade.run` | 在独立后台 session 运行或恢复已保存的 Maestro setup，读取逐点 output/spec、exact-history result/log 与唯一 runtime input 哈希；可要求 OA→Spectre 输入束一致性，以及 exact-point 或 Maestro RDB 模式的严格 sweep 绑定 | 远端计算，不写 OA/setup |
| `ade.variables.apply` | 以 tests、可选 enabled corners 和逐 scope 旧值为前置条件修改 global/test/corner Maestro 变量，保存后独立重开回读 | Maestro setup 写入 |
| `ade.setup.apply` | 对声明 analysis 做旧状态 CAS，并新增不存在的命名 output/spec；一次保存后独立重开回读 | Maestro setup 写入 |
| `simulation.run` | 单点仿真并判规格 | scratch/计算 |
| `design.tune` | 有限搜索；设计参数提交 OA，纯 testbench 条件只记录选择 | 计算；按维度决定是否写 OA |
| `design.close_loop` | 建图、搜索、应用、回读 | 计算 + OA 写入 |

因此上层 agent 可以只要求“建原理图”“把这组参数应用进去”“微调 ADE analysis/output”“接收人工 ADE 结果”“后台运行已有 ADE setup”或“只跑仿真”，无需伪装成完整设计任务。

`analysis` 与电路参数分离。反相器省略时解析为 `transient`，共源级省略时解析为 `dc`；共源 AC 必须显式声明 `analysis: "ac"` 以及 `ac_sweep.start_hz/stop_hz`。固定多 analysis 质量门使用 `analysis: "quality"`，并要求 `ac_sweep`、`linearity_sweep`、`noise_sweep` 同时存在。扫频点密度、低频参考点数、参考窗变化、线性度窗口和噪声频带都属于任务与 plan token。这样换 analysis 或改变指标定义不会复用旧 token，也不会把默认设置伪装成 `user_input`。

`schematic.transform` 不等同于重建模板。共源 transform 要求目标先通过 VDA common-source 结构检查，然后在同一 cellview 中把 MN0 源极标签从 VSS 改为内部网 `NSRC`，新增 `analogLib/RS0(NSRC,VSS)` 并设置 `source_resistance_ohm`。反相器 testbench transform 则要求现有 cell 是 MN0/MP0 core 或已经完成同一变更；它保留 MOS/pins，只把地归一到 `gnd!` 并增加固定的 `VDD0/VIN0/CL0/GND0`，其中供电和负载来自显式任务参数。两者都强制使用 Bridge editor append mode；preflight 拒绝带未保存改动的目标，编辑 batch 失败时只 purge 未保存缓存且不保存。前后回读必须证明未点名器件的完整参数、master、位置和顶层 pins 保持不变，重复调用幂等。为了避免把任意图编辑伪装成安全能力，当前没有通用图重写 DSL，也没有自动逆变换。若保存已成功而后置审计失败，目前会保留失败和真实 OA 状态，尚没有通用 snapshot 回滚。

## 两层参数契约

VDA 保留两种用途不同的参数表示：

- `parameters` / `parameter_space` 是电路模板已定义的 canonical semantic parameters，例如 `device_width_um`、`load_resistance_ohm`、`bias_v` 和 AC `load_ff`。它们可参与仿真、规格判定和有限搜索，但并非都写 OA：W/L/RD/RS 是设计参数，bias/VDD/外部负载是 testbench 条件。当前 MOS width semantic 指单指宽 `Wfg`；多指 OA/`si` 一致性另外核对 `finger_width`、`fingers/nf`、`m/multi` 和总有效宽度，不能把网表 `w` 无条件当成 `Wfg`。
- `instance_parameter_updates` 是人工明确指定的实例级 CDF/OA 写入，例如 `MN0.fingers="2"`、`MN0.m="1"` 或 `RD0.r="22k"`。参数名和值按 Bridge 字符串契约原样传递，不做单位、别名或枚举推断。

`existing_schematic` 是不依赖固定拓扑模板的通用 circuit kind，开放 `schematic.inspect`、`parameters.apply`、`ade.prepare`、`ade.capture`、`ade.run`、`ade.variables.apply` 与 `ade.setup.apply`：前者保留 Bridge reader 的完整结构对象、geometry、notes、nets/pins 细节和所有可回读 CDF 参数；参数操作允许人工指定任意已有实例；ADE 操作则为已有 design 准备新的 Maestro 人工入口、读取人工状态、后台运行一个已保存 setup，或用显式旧状态前置条件微调变量、analysis 和新增 output/spec，不要求 VDA 理解 DUT 拓扑。反相器和共源模板也能使用相同原始参数与 ADE 交接路径，并可在一个参数任务中组合 semantic parameters 与原始实例参数；semantic 写入先执行，原始 CDF callback 后执行，最终 OA 必须同时满足所有已声明 semantic 值和原始字段值。

执行路径先结构化回读目标 schematic 并确认实例存在，再复用 Bridge 的 `set_instance_params(..., param_filters=None)` 触发 CDF callback、`schCheck` 和 `dbSave`。通用 reader 为控制输出会省略空值和超长值，因此 VDA 不用摘要缺失来限制 Bridge：写入后另发只读 SKILL，直接打开目标 OA、定位实例 CDF，并逐字段比较真实 `p~>value` 与请求字符串；executor 的 `schematic.inspect.after` 再独立执行一次同样的定向读取。首次值不一致时，worker 至多按任务声明顺序逐字段重放一次；计划必须披露该副作用，最终仍不一致则整个 run 失败。

定向读取的字段名来自 Bridge 写入函数返回的实际应用映射，而不是 VDA 复制的别名表。因此 Bridge 公开的 `wf -> Wfg`、`nf -> fingers` 等简写仍可使用；run record 同时保存原始请求和 Bridge 报告的实际 CDF 目标。

CDF 的 `display` 和 `editable` 元数据不是写入 allowlist。2026-07-20 的真实 smoke 中，`MN0.m` 为 `editable=nil` 且 callback 后不能保持 `2`，但 `RD0.r` 同样报告 `editable=nil` 却能成功持久化为 `22K`。因此 VDA 不依据 UI 元数据缩窄 Bridge 能力，最终权威只来自 callback 后目标 OA 值；失败仍可能留下部分写入，因为 Bridge 的多实例调用不是 OA 事务。

任务请求及原始值标为 `user_input`；真实 OA 确认标为 `bridge_readback`；demo 只能产生 `software_inference`。完整 inspect 会保留 callback 导致的旁路参数变化，但 VDA 只对任务显式列出的字段宣称确认。`instance_parameter_updates` 当前不会自动进入 `parameter_space`；这保留有限搜索的显式边界，但后续会提供实例参数搜索维度，而不是长期维持人工透传上限。

## ADE 人工介入与状态所有权

ADE 兼容是当前架构约束，不是 UI 附加项。VDA 可以规划、搜索、判规格和选优，但可复核的 ADE setup/history 必须继续允许人类打开、调整、运行和保存；VDA 不能把唯一真源藏在一次性 wrapper 或内存状态里。自动路径和人工路径通过显式 operation 交接，不能在一次运行中暗中互相覆盖。

首个纵向切片由 `ade.prepare` 和 `ade.capture` 组成，当前只使用 Bridge 已有的 ADE Explorer/Assembler Maestro 公共接口，不修改 Bridge，也不复制 SKILL、结果导出或文件传输。`prepare` 要求 design view 已存在并预检相邻 Maestro view 不存在，然后创建一个持久化 Spectre test、保存、关闭并重新打开核对 test 名称。它不配置 analysis、stimulus、sweep 或 output；这些状态留给人工调整。若 Maestro view 已存在，无论 `replace_existing` 如何都在模型或 worker 边界拒绝，不覆盖也不尝试合并。

`prepare` 不是跨 OA/transport 的事务：若 `save_setup` 已落盘而 close、独立回读或连接随后失败，新 Maestro view 可能真实存在但本次 run 失败。下一次执行会因 view 已存在而停止，要求人工检查；VDA 不自动删除这个可能包含有效状态的 view。

执行 `capture` 前由用户自行打开、调整、运行、保存并聚焦目标 `library/cell/maestro`。worker 先做一次轻量 snapshot，拒绝无焦点、目标不匹配或会话中途切换；默认也拒绝带 `*` 的未保存 setup。随后 Bridge 捕获 setup 文件、指定或按 mtime 选择的最新 history、Spectre netlist、PSF 和日志，并读取 ADE Detail 表中的每个 sweep point、变量、output、spec 和 pass/fail。

本地 manifest 对每个文件记录相对路径、大小、SHA-256、类别与来源。Maestro setup 属于 `bridge_readback`；实际 simulator input、PSF/log 和 ADE output/spec 属于 `eda_result`；任务固定的 history 属于 `user_input`，自动选取最新 history 的规则属于 `software_inference`。setup 和 simulation artifacts 分别形成聚合指纹，供后续人工前后对比与冲突检查。`ade.capture` 不调用 run/save/close，不写 OA；其成功只说明“人工 ADE 状态和已有结果已被完整捕获”，不等于这些结果满足 VDA 规格。

无需 GUI 的自动分支由 `ade.run` 提供。worker 明确打开目标 cell 的独立 background Maestro session，回读非空 test 列表，通过 Bridge `run_and_wait` 取得本次调用返回的 history，再把该 history 传给 `read_results`。它不调用 `save_setup`、不改变窗口焦点、不写 schematic 或 Maestro OA。默认必须同时得到非空逐点 output/spec 与完整产物清单；任一要求被显式放宽且证据缺失时，executor 只记为 `partial`。setup test 标为 `bridge_readback`，history/output 与 simulator input/result/log 哈希标为 `eda_result`。

后台产物不依赖聚焦窗口的 `snapshot`。VDA 对每个 test 的 Analog Session 临时设置 project/results dir，使 `asiGetAnalogRunDir` 落在 profile 的唯一 `/data/xum` scratch，并立即回读；退出前按逆序恢复原 session 值，不调用 `save_setup`。恢复任务必须同时给出 exact history 与原 runtime scratch，且不再次调用 `run_and_wait`。随后在 project/scratch 的 exact-history 路径收集 `.log/.rdb/.msg.db`，在唯一 runtime input 根收集普通文件，调用 Bridge 公共 shell 通道生成远端 TSV 大小/SHA-256 清单。每条路径必须绑定到这两类根之一；`netlist`、`input.scs`、至少一个非空结果和至少一个非空日志是硬门，重复逻辑路径内容不同时拒绝。

Bridge 的标准 `download_file` 仍是首选传输。nics4304 live smoke 暴露本机 DNS 暂时不能解析 `nics4304-cad1` 时，现有 Virtuoso tunnel/CIW 仍在线；VDA 因而只对自己生成的 `/data/xum/.../vda_ade_manifest_*.tsv` 和 Bridge 自己生成的 `/tmp/vb_results_<uuid>.csv` 提供有行数/字节上限的 SKILL 文本读取 fallback。它不传输 PSF 或任意远端文件，不替代 Bridge 的 SSH/SCP。IC6.1.8 单点 Detail CSV 缺少 `Point` 列时，VDA 只在临时本地 CSV 精确识别六列格式后补 `Point=1`，再交给 Bridge 原解析器；归一化前后 SHA-256 和 `software_inference` 标记进入证据。Bridge 源码没有修改。

可选的 `require_simulator_input_consistency` 再从 manifest 精确读取每个 test 的 Spectre 输入。单点扁平输入直接读取 `input.scs`；IC6.1.8 原生 sweep 的实际 runtime 形状是 `input.scs` 显式 `include "netlist"`，因此严格模式要求两者处于同一唯一 runtime test 根、分别匹配 manifest SHA-256，并作为一个输入束解析。随后核对 Design library/cell/view header，独立回读 source schematic，比较实例集合、已知 primitive 的节点顺序和显式 raw 参数映射。当前 TSMC N28 smoke 覆盖 MOS `l/w/nf/simM→multi`、pulse/DC source 与 capacitor，共 19 组映射；`Wfg` 明确标为 PDK CDF 派生语义，不能用字面 `w` 相等代替。这个 Gate 证明当前输入与 OA raw state 同源，但不证明所有 PDK CDF 派生关系，也不证明 history 名在运行前不存在。history 命名/覆盖策略仍沿用已保存 setup，ADE output 也尚未映射为通用 VDA constraints；它不是完整规格闭环。

`sweep_verification` 是 `ade.run` 上的可选严格附加门，不替代或收窄 Bridge 的普通 Maestro 运行能力。任务显式列出 exact tests/corners、声明 scope 的完整 sweep 字符串、从 1 连续编号的 expected points，以及每个 test/variable 对应的 OA `instance.parameter`。worker 在运行前后回读相同 setup，并逐 point 检查 RDB Detail 参数与至少一个非空 scalar output。证据选择有两个互斥模式：若 exact history 出现任意逐点目录，就坚持每个 point/test 的非空 `input.scs` 和结果都完整；若完全没有逐点目录，则只接受 IC6.1.8 已验证的 Maestro 数据库模式，要求每个 test 一个已哈希的符号 runtime `input.scs`+`netlist` 输入束、OA raw 参数与 Spectre 实例都引用变量、保留输入值匹配一个声明点、exact-history `.rdb` 非空，以及 completion log 的点数精确且仿真错误为零。RDB 中每点的变量、非空 output、共享输入束和 RDB hash 再组成逐点指纹。`exact_point_input_result_binding_verified` 在数据库模式保持 `false`，不会虚构未保留的逐点文件；另以 `native_sweep_database_binding_verified=true` 表示实际证据模式。point/input/result 对应和单位等价比较属于 `software_inference`，原始 input/RDB/log 仍是 `eda_result`。缺点、空 output、半套逐点产物、变量 shadow 后值不符、未知 primitive、include 缺失或任一 hash 漂移都硬失败。

2026-07-22 的 TSMC N28 live Gate 已在 `vda_ade_sweep_inv_001` 上保存 global `CL=1f,2f,4f`，真实运行 3 点 transient 并从 `Interactive.0` RDB 回读 `VoutAvg=415.5/419.8/428.3 mV`；completion log 为 3 点完成、0 错误。首轮运行后因错误的逐点目录假设失败，随后固定同一 history/scratch 只读恢复，没有重算。OA `CL0.c=CL`、Spectre `CL0 ... c=CL`、setup 前后指纹、两份 runtime 输入哈希和 RDB/log 均闭合。状态是原生单变量 sweep 同源执行与证据恢复已验证；二维 sweep、corner、多 test/multi-analysis 和通用 VDA constraint 映射仍待 Gate。

自动微调 setup 的首个切片是 `ade.variables.apply`。任务必须给出 exact `expected_tests`；使用 corner scope 时还要给出 exact enabled `expected_corners`。每个变量必须给出 scope、可选 scope name、`expected_value`（`null` 表示该 scope 不存在）和新字符串。worker 在任何已配置 Maestro session 已打开时拒绝，随后以独立 background session 先读完所有声明 scope 的旧值；只有 tests/corners/旧值全部匹配才逐项 `set_var/get_var`，一次 `save_setup` 后关闭，并用全新 session 再次核对。global 读取复用 Bridge public `get_var`；test/corner 写入复用 public `set_var`，读取通过 Bridge SKILL channel 调用 Cadence public scoped `maeGetVar`，没有修改第三方仓库。请求属于 `user_input`，旧值、即时值和持久化值属于 `bridge_readback`。tests + 可选 corners + 声明 scope 形成 targeted 前后指纹，但不是完整 setup 指纹。逗号列表本身只说明该 scope 声明了 sweep；只有额外启用并通过 `sweep_verification`，才能把 OA 输入束、RDB point 与实际结果联系起来。2026-07-22 已真实闭合 global `CL` 从不存在到 `1f,2f,4f` 的保存、独立重开和后续 3 点运行；test/corner scope 仍只有本地契约证据。

第二个切片 `ade.setup.apply` 保持与变量修改正交。任务列出 exact tests；每个 analysis update 都提供完整旧 `enabled/options`（不存在时为 `null`）、目标 enabled 和有限 option delta。worker 先读取全部 analysis 旧状态，并确认全部待新增 output 名在对应 test 中不存在；任一不符都在第一个 writer 调用前停止。随后复用 Bridge public `set_analysis`、`add_output`、`set_spec`，每项写后经 SKILL channel 调用 `maeGetAnalysis`、`maeGetTestOutputs` 和 `axlGetSpecData` 回读，全部一致才保存一次，关闭后用全新 session 再次核对。请求属于 `user_input`；旧值、即时值和持久化值属于 `bridge_readback`。该 operation 不运行仿真，因此 output 只有配置而没有 `eda_result`。

首版本只新增不存在的命名 output，不替换已有 output：Bridge public writer 没有暴露删除接口，而仅凭同名 `add_output` 无法证明是更新、重复还是丢失未建模的 plot/save/description 状态。VDA 没有删除或屏蔽 Bridge 原有接口；需要直接使用 Bridge 的场景仍可独立进行。要把已有 output 替换纳入受控 operation，必须先扩充完整旧状态契约、删除/恢复语义和 live smoke。analysis options 当前只接受可精确回读的扁平 string/bool/null alist；嵌套 option 返回会保守失败而非丢弃。

当前受支持的持久化后端是 `maestro`，因为这是 Bridge 已公开并带 setup/history/result API 的路径。Bridge 同时提供 ADE L state 到 Maestro 的迁移原语，但 VDA 尚未把迁移包装成 operation，也不会在没有备份、目标冲突检查和 live smoke 时改写旧 state。2026-07-21 已在 nics4304 真实通过 `ade.prepare → ade.setup.apply → ade.run/resume`：目标 Maestro test 指向另一个既有反相器 schematic，transient/output/spec 持久化回读，`Interactive.0` 的 Detail 结果、RDB/log 与 runtime `input.scs` 哈希闭合，OA→input raw 参数映射一致。2026-07-22 又真实通过 global `ade.variables.apply` 与原生 CL sweep 的输入束/RDB 绑定。test/corner scope CAS、`ade.capture` 的人工入口、已有 output 安全替换和有限 corner 仍待 live；需要打开 ADE、旧 ADE L 迁移和人工数值对照的 Gate 已延期到 [`deferred-manual-gates.md`](deferred-manual-gates.md)。人工改动必须通过前置条件、setup 指纹或重新 snapshot 被发现，而不是被 VDA 静默覆盖。

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

源极退化沿用该路径而不复制 executor：结构回读动态返回 `topology_variant`；`si` parser 在同一 common-source action 中要求 `MN0(OUT IN NSRC VSS)` 与 `RS0(NSRC VSS)`，并把 RS0.r 纳入 OA/网表参数一致性；DC wrapper 额外保存 NSRC，器件 VGS/VDS 改由 NSRC 计算，同时核对 MN0/RD0 与 MN0/RS0 两组 KCL。`source_resistance_ohm` 可直接进入原有有限 `parameter_space`。2026-07-20 的真实 smoke 已完成原位 transform、DC/AC、有限搜索、W/RD/RS AC design tuning、checkpoint 恢复和最佳回读；2026-07-21 又把同一参数 staging/writeback 路径接入多 analysis 质量组合。尚未闭合的是 L/VDD、corner 和更复杂拓扑，而不是基本 gain/bandwidth 或 W/RD/RS 质量调优执行路径。

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

## 证据链

每次运行至少保存任务和计划 token、adapter 与证据来源、动作状态、候选参数、仿真指标、逐条规格判定、最终选择、OA 回读摘要，以及错误和未验证边界。显式实例写入还保存请求、写入前目标字段、立即确认和独立 inspect 的完整参数表。ADE `prepare` 保存 design/test/simulator 请求、持久化 view/test 回读、未覆盖既有 view 以及没有设置 analysis/sweep 的范围；`capture` 保存焦点目标、是否已保存、setup/simulation 聚合指纹、逐文件 manifest、history 选择来源以及可用时的逐点 output/spec；变量/setup patch 保存声明目标、全部旧值、即时值、独立重开值、targeted 前后指纹及未覆盖范围，并明确记录没有运行仿真；严格 sweep run 还保存 setup 前后 scope 指纹、每个 point 的 Detail 参数/非空 output、逐 test input/result hash、OA/input comparison hash 和逐点绑定指纹。调优 checkpoint 保留历史失败 actions，但恢复后只有完成的候选证据参与选择；最终 run 可以在完整证据和最终回读成立时成功，同时仍显式留下已恢复的 transport 事件。自动 netlisting 还保存远端网表/wrapper 路径、SHA-256、解析后的实例参数和一致性结论。

timing、过冲/欠冲、`supply_energy_per_cycle_fj`、`average_supply_power_uw`、共源 DC/供电连续指标，以及从 AC、相干 transient 或 noise PSF 提取的连续量标为 `eda_result`；OA 结构和参数标为 `bridge_readback`；任务显式给出的 VDD、负载、偏置、analysis 或 sweep 字段标为 `user_input`；默认 analysis/sweep 字段、`gate_area_proxy_um2=(Wn+Wp)L`、饱和区分类、交点/压缩点规则和指标完整性判断是 `software_inference`。供电能量或功耗保留积分窗口和源电流方向，不能称为纯动态开关能量；AC、linearity 和 noise 指标也必须保存提取公式、范围和 unresolved 诊断，不能只保存一个无来源标量。后续 Maestro、Calibre 和 PEX 沿用同一证据模型。
