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
          +-- si batch netlisting
          +-- Spectre
          +-- SSH / file transfer
```

VDA 不嵌入一个新的通用 LLM。Codex 负责开放式推理，VDA 负责把高风险 EDA 动作收敛为受控命令和机器可核验结果。

## 为什么使用独立 worker

`virtuoso-bridge-lite` 已有自己的 Python 虚拟环境和本机配置。主程序通过该环境中的 Python 启动 JSON worker，而不是把 Bridge 源码复制进来或强制安装成本项目依赖。这样可以保持 Bridge 独立升级、避免依赖漂移，并把跨环境协议缩小到版本化 JSON action。

## 任务与局部能力

任务不是固定的“全流程按钮”。`operation` 决定实际范围：

| operation | 作用 | 远端副作用 |
| --- | --- | --- |
| `schematic.create` | 建图并结构回读 | OA 写入 |
| `schematic.inspect` | 读取拓扑、参数、pins | 只读 |
| `parameters.apply` | 应用指定参数并回读 | OA 写入 |
| `simulation.run` | 单点仿真并判规格 | scratch/计算 |
| `design.tune` | 有限搜索，选择并应用最佳参数 | 计算 + OA 写入 |
| `design.close_loop` | 建图、搜索、应用、回读 | 计算 + OA 写入 |

因此上层 agent 可以只要求“建原理图”“把这组参数应用进去”或“只跑仿真”，无需伪装成完整设计任务。

## 两层参数契约

VDA 保留两种用途不同的参数表示：

- `parameters` / `parameter_space` 是电路模板已定义的 canonical semantic parameters，例如 `device_width_um`、`load_resistance_ohm` 和 `bias_v`。它们可参与仿真、规格判定和有限搜索。
- `instance_parameter_updates` 是人工明确指定的实例级 CDF/OA 写入，例如 `MN0.fingers="2"`、`MN0.m="1"` 或 `RD0.r="22k"`。参数名和值按 Bridge 字符串契约原样传递，不做单位、别名或枚举推断。

`existing_schematic` 是不依赖固定拓扑模板的通用 circuit kind，只开放 `schematic.inspect` 与 `parameters.apply`：前者保留 Bridge reader 的完整结构对象、geometry、notes、nets/pins 细节和所有可回读 CDF 参数；后者允许人工指定任意已有实例。反相器和共源模板也能使用相同原始参数路径，并可在一个任务中与 semantic parameters 组合；semantic 写入先执行，原始 CDF callback 后执行，最终 OA 必须同时满足所有已声明 semantic 值和原始字段值。

执行路径先结构化回读目标 schematic 并确认实例存在，再复用 Bridge 的 `set_instance_params(..., param_filters=None)` 触发 CDF callback、`schCheck` 和 `dbSave`。通用 reader 为控制输出会省略空值和超长值，因此 VDA 不用摘要缺失来限制 Bridge：写入后另发只读 SKILL，直接打开目标 OA、定位实例 CDF，并逐字段比较真实 `p~>value` 与请求字符串；executor 的 `schematic.inspect.after` 再独立执行一次同样的定向读取。任一比较失败，整个 run 失败。

定向读取的字段名来自 Bridge 写入函数返回的实际应用映射，而不是 VDA 复制的别名表。因此 Bridge 公开的 `wf -> Wfg`、`nf -> fingers` 等简写仍可使用；run record 同时保存原始请求和 Bridge 报告的实际 CDF 目标。

任务请求及原始值标为 `user_input`；真实 OA 确认标为 `bridge_readback`；demo 只能产生 `software_inference`。完整 inspect 会保留 callback 导致的旁路参数变化，但 VDA 只对任务显式列出的字段宣称确认。`instance_parameter_updates` 当前不会自动进入 `parameter_space`；这保留有限搜索的显式边界，但后续会提供实例参数搜索维度，而不是长期维持人工透传上限。

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

当前先选 `si`，因为反相器目标是 DUT-only schematic，未承诺已有 Maestro test/setup；强行创建 Maestro view 会给单点 `simulation.run` 引入额外 OA 配置写入。进入多 analysis、corner 或已有稳定 ADE setup 后，可复用 Bridge 的 `maeCreateNetlistForCorner`，但不能让两条路径同时成为未声明的真源。

2026-07-19 的 live smoke 已验证上述路径可在 nics4304 上从目标 OA 导出真实网表并得到非空 transient 与供电电流波形。`design.tune` 的候选参数暂存、回读、重新 netlist、仿真、最佳点提交，以及不可行/预算耗尽恢复均有成功的真实记录。

同日长 sweep 暴露了 transport 边界：transport 不可用期间既不能继续仿真，也不能保证 OA 恢复。VDA 现在把 Bridge worker 中断视为搜索暂停，不把该候选伪装成不可行点，也不继续消耗后续候选。调优在初始 OA 回读、每次待写入/确认写入以及每个候选完成边界原子更新本地 checkpoint，保存 task/token/adapter、原始 OA 基线、最后确认与待确认 OA 状态、已完成候选前缀、actions 和 notes。

恢复时先拒绝 completed checkpoint、task/token/adapter 不一致和非前缀候选；Bridge 外部恢复后重新 probe，并用 `schematic.inspect.resume` 独立回读 OA。只有当前参数属于原始基线、最后确认/待确认写入或任务声明候选时才继续。已完成索引直接跳过；全部候选完成后若最终写回中断，`next_candidate_index` 保持在末尾，只重试选优、写回和最终回读。checkpoint 只有在最佳点提交或基线恢复且 `schematic.inspect.after` 一致后才标记 `complete=true`。

2026-07-19 的 Gate 1R live 任务经历 3 次随机 SSH/tunnel 中断，分别从候选 2、4 和 `next_candidate_index=10` 恢复，最终形成单一的 1–9 候选前缀、成功最佳写回和独立 OA 回读。VDA 的显式恢复语义因此已验证。随后在 Bridge 的备份隔离分支 `codex/vda-transport-recovery` 上用原子提交 `9e52844` 修复 Windows stale PID 判断与 `VirtuosoClient.from_env` 遗漏的 `warm()`；保留 stale state、强制终止精确 listener PID 后，第二次只读 OA inspect 自动建立新 tunnel 并得到一致回读。该结果只闭合“调用边界发现 tunnel 已死后的重建”，不证明运行中 SSH 上传/仿真的随机 reset 已消失。VDA 不删除或复制 Bridge 的 SSH/SCP 实现，补丁范围和上游兼容流程见 `docs/third-party/virtuoso-bridge-local-patch.md`。

后续 Bridge 提交 `f8fdb9e` 对已有幂等 SSH command/upload/download 重试加入 1 秒、3 秒有界退避，`2f41293` 则把 `connect()` 在 `sendall()` 前的拒绝标成私有 pre-send 错误，允许 managed client warm 并重试一次。安全边界取决于“payload 是否可能已发送”：pre-send 可自动恢复；send/recv 之后一律不重放 SKILL，由 VDA checkpoint 暂停并在新进程中核对 OA 后恢复。9 点压力任务在候选 8 的 pre-send connect refusal 处暂停并成功恢复到 9/9；同-client 强制断链 smoke 又直接验证了 pre-send 自动恢复。单次证据不外推为网络永不掉线。

## 共源 Gate 2A DC 路径

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

工作区分类不读取一个未验证的模型枚举值：`saturation_region` 由 Spectre 给出的 `VDS`、`VDSAT` 和 `IDS` 按显式规则推导，标为 `software_inference`；原始器件量、节点量和从它们计算的连续指标标为 `eda_result`。Gate 2A 已在 `vb_pdk_smoke/vda_cs_gate2a_001/schematic` 完成 6 点真实搜索和最终独立 OA→si→DC OP 复核。当前开发优先级先转向受控拓扑变更，并让新拓扑重新通过 DC；AC gain/bandwidth 仍是随后不可跳过的放大器性能 Gate。现阶段尚不能把 Gate 2A 称为完整放大器闭环。

## 证据链

每次运行至少保存任务和计划 token、adapter 与证据来源、动作状态、候选参数、仿真指标、逐条规格判定、最终选择、OA 回读摘要，以及错误和未验证边界。显式实例写入还保存请求、写入前目标字段、立即确认和独立 inspect 的完整参数表。调优 checkpoint 保留历史失败 actions，但恢复后只有完成的候选证据参与选择；最终 run 可以在完整证据和最终回读成立时成功，同时仍显式留下已恢复的 transport 事件。自动 netlisting 还保存远端网表/wrapper 路径、SHA-256、解析后的实例参数和一致性结论。

timing、过冲/欠冲、`supply_energy_per_cycle_fj`、`average_supply_power_uw` 和共源 DC 连续指标标为 `eda_result`；OA 结构和参数标为 `bridge_readback`；任务显式给出的 VDD、负载或偏置标为 `user_input`；`gate_area_proxy_um2=(Wn+Wp)L` 与共源饱和区分类是 `software_inference`。供电能量在相邻两次 VIN 50% 上升沿间积分，包含该周期泄漏，不称为纯动态开关能量；饱和区分类也不冒充 PDK 模型直接输出。后续 Maestro、Calibre 和 PEX 沿用同一证据模型。
