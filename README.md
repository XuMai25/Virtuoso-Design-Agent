# Virtuoso Design Agent

Virtuoso Design Agent 是 `virtuoso-bridge-lite` 之上的受控设计编排层。它把“建原理图、读回、应用参数、跑仿真、判定规格、有限调优”组织成可单独执行、可组合、可审计的任务，而不是再造一套 Bridge。

当前版本从 **L5A** 起步：在已知 PDK、固定电路模板、显式规格和有限搜索空间内完成闭环。TSMC28 反相器 Gate 1，以及电阻负载 NMOS 共源/源极退化的 DC、复数 AC、W/L/RD/RS、bias/load、相干 transient 线性度、真实 VDD 功耗、ordinary noise 和固定三分析 `quality` 均有真实 OA/`si`/Spectre 证据。2026-07-22 又完成共源 L/VDD 质量搜索、最佳 OA 写回和固定设计 TT/SS/FF 三条件验证；PVT 作为显式可选项接入 testbench 调优，但不会默认附加。2026-07-23 依次闭合理想尾源差分对 Gate 3、真实 `MNTAIL/BIAS` Gate 4、可逆对称 `RS0/RS1` Gate 5，以及 PMOS 电流镜有源负载 Gate 6。Gate 6 在全新 `vda_diffpair_active_gate6_001` 上完成 RD→`MP0/MP1` exact-delta、OA→`si` 参数一致性、DC 镜像/KCL/工作区、差模与共模 AC/CMRR、10 点 ICMR、相干 transient、ordinary noise、bias/load 与 Wn/Wp 有限搜索、预算耗尽、全不可行恢复、checkpoint/resume、最佳 OA 写回、精确恢复和最终有源负载重建。最终同源网表得到增益 `3.7421 V/V`、带宽 `2.9756 GHz`、GBW `11.1351 GHz`、CMRR `34.8451 dB`；这些只是 nominal `top_tt` 和声明网格内的结果。2026-07-24 又完成 PSRR+/PSRR− 三次同网表 AC 单点：低频分别为 `11.5156 dB` 和 `13.4535 dB`，证明执行链闭合但供电耦合很强。该任务没有 PSRR 数值门，也未运行 bias/load 搜索。可选差分对 PVT、mismatch/Monte Carlo、PSRR 规格调优、slew/settling、ADE multi-test/multi-analysis、人工打开/修改/重跑和旧 ADE L 非覆盖迁移仍未闭合，因此不能称为完整 L5B 设计质量闭环。

反相器 Maestro 路径已 add-only 保存 delay、rise/fall skew 和每周期总供电能量表达式，并从 exact-history RDB 把 CL 三点、VDD×CL 六点和 test-scope CL×environmental-corner 六格结果映射到 VDA constraints/objective；最小能量点为 `0.8 V/1 fF`，delay `3.254 ps`、skew `1.425 ps`、周期供电能量 `1.309 fJ`。该 ADE corner 仍共享 nominal `top_tt`，不能冒充 process corner；真实 TT/SS/FF 证据来自上述共源 direct `si`/Spectre PVT Gate，而不是 Maestro setup。两条仿真状态继续显式分开。

PDK 默认面向晶圆厂 CMOS 设计。当前缺省 profile 为 `nics4304_tsmc28`，对应 TSMC N28/`tsmcN28`；后续 TSMC、SMIC 等工艺使用独立 profile 和各自验证证据。TSV、hybrid-bonding 等封装/3D PDK 只有任务显式选择时才使用，不会成为自动 fallback，也不会改变普通晶体管级模板的默认假设。详见[决策 0002](docs/decisions/0002-foundry-cmos-pdk-default.md)。

## 当前能做什么

- 将任务编译为带副作用标记的稳定执行计划。
- 单独规划或执行：`schematic.create`、`schematic.inspect`、`schematic.transform`、`parameters.apply`、`ade.prepare`、`ade.capture`、`ade.corners.apply`、`ade.variables.apply`、`ade.setup.apply`、`ade.run`、`simulation.run`、`design.tune`、`design.close_loop`。当前 `schematic.transform` 开放共源级源极退化的受控 add/remove、反相器 core→ADE source/load testbench、差分对 core→`MNTAIL/BIAS`、真实尾管差分对的对称源极退化 add/remove，以及无源退化真实尾管差分对的 `RD0/RD1 ↔ MP0/MP1` 电流镜负载可逆变换。
- 用确定性 demo adapter 离线验证闭环、规格判定和参数选择；结果明确标为 `software_inference`。
- 通过独立 worker 调用本机 `virtuoso-bridge-lite` 环境。反相器支持 `OA -> si -> Spectre transient` 的 timing、过冲/欠冲和周期供电能量；共源级支持同一 `OA -> si` 网表上的 DC OP、复数 AC、相干正弦 transient 幅度 sweep 和普通 noise sweep。可提取 `Id/VGS/VDS/VDSAT/gm/gds`、真实 VDD 功耗与 KCL、低频增益、首个 −3 dB 带宽、GBW、unity、HD2/HD3、THD、P1dB，以及频带积分的输出/输入参考噪声；单项执行与提取均有 live 证据。`analysis: "quality"` 已在一次 OA/`si` 核对后依次运行 AC、linearity、noise，并完成 bias/load、W/RD/RS、L/VDD 搜索、固定设计 TT/SS/FF 验证和显式启用的 PVT-aware bias 调优；每个 PVT 条件保留原始 `eda_result`，跨条件约束和最坏值聚合标为 `software_inference`。
- Gate 3/4/5/6 差分对复用同一 worker 与 executor，不复制 Bridge。Gate 3 保留外部理想尾源能力；Gate 4 只新增 `MNTAIL(TAIL,BIAS,VSS,VSS)` 与 `BIAS` pin；Gate 5 再把 `MN0.S/MN1.S` 从 `TAIL` 分离到 `NSP/NSN`，只新增对称 `RS0(NSP,TAIL)`、`RS1(NSN,TAIL)`。Gate 6 从未退化的 Gate 4 拓扑删除 `RD0/RD1` 并加入 `MP0(OUTP,OUTP,VDD,VDD)`、`MP1(OUTN,OUTP,VDD,VDD)`，反向操作可按声明电阻值恢复原负载和可选 placement 指纹。`tail_width_um/tail_length_um/source_resistance_ohm/pmos_load_width_um/pmos_load_length_um` 属于 OA semantic 参数，`tail_bias_v` 只属于 wrapper；真实尾管路径拒绝理想 `tail_current_ua/tail_output_resistance_ohm`。Gate 3–6 的 OA→`si` 证据链均已有 live 结果；Gate 6 还真实覆盖 ICMR、多种有限搜索、预算、不可行和 transport checkpoint/resume。新增 `analysis: "psrr"` 在同一自动 `si` 网表上分别运行平衡差模、VDD 注入和 VSS 注入，并核对三次 DC 与频率网格；nominal 单点已经 live，结果显示低频 PSRR 只有 `11.5156/13.4535 dB`，尚未达到任何用户声明的 PSRR 规格闭环。可选 PVT、mismatch、更多质量指标和 ADE handoff 仍是边界。
- 源极退化不新建第二套模板或仿真器：add 在同一 common-source cellview 中把 `MN0.S: VSS -> NSRC`，只新增 `RS0(NSRC,VSS)`；remove 只删除 VDA 创建的 RS0 两条端子 stub/标签、恢复 `MN0.S: NSRC -> VSS`。同一 inspect、参数应用、`si` 网表解析、DC/AC 指标和有限搜索路径动态识别两种变体。
- `existing_schematic` 提供不依赖固定电路模板的 Bridge 能力面：`schematic.inspect` 保留 Bridge 的完整结构结果和所有可回读 CDF 参数；`parameters.apply` 可按实例透传 Bridge 接受的参数字符串，写入后用定向 CDF 读取再次核对。反相器/共源模板仍可在同一任务中组合 semantic parameters 与原始实例参数。对已有真实仿真 adapter 的固定模板，`design.tune`/`design.close_loop` 还可用 `instance_parameter_space` 声明有限的 `instance.parameter -> [raw strings]` 搜索维度；它与 semantic space 组成同一个有预算上限的笛卡尔积，每点写入、回读、自动 netlist 和仿真，最终最佳值再次写回并独立定向回读。搜索字段名必须来自未过滤 OA inspect 的实际 CDF 名，不猜 Bridge 别名；这不收窄独立 `parameters.apply` 的原有 Bridge 能力。
- `ade.prepare` 与 `ade.capture` 保留显式人工介入边界。`prepare` 只在目标 Maestro view 不存在时新建持久化 Spectre test，可显式指向另一个既有 design schematic；已有 view 一律拒绝，也不预设 analysis/stimulus/sweep/output。`capture` 核对人工聚焦的目标，捕获 setup、history、真实 Spectre netlist/PSF/log 哈希和逐点 output/spec。自动分支中，`ade.corners.apply` 只在 exact tests 与旧 corner 有序列表匹配时 add-only 新增 corner；`ade.variables.apply` 只有在 expected tests、可选 enabled corners、全部声明 scope 旧值和目标 global-selection 状态匹配时才更新变量或 selection；`ade.setup.apply` 对声明 analysis 做旧状态 CAS，并只新增不存在的命名 net/point output 与可选 spec。三个 setup 写 operation 都只保存一次并独立重开回读，已有已配置 session 时拒绝。`ade.run` 为每个 test 临时把 background session 的 project/results dir 定向到唯一 `/data/xum` scratch，运行或按显式 history/scratch 恢复后还原原值；它读取逐点 output/spec，并对 exact-history companion 与唯一 runtime input 根生成大小/SHA-256 清单。任务可显式要求把哈希绑定的 `input.scs` 或 `input.scs`+sibling `netlist` 输入束的 design header、实例、节点和已知 primitive raw 参数映射与 Maestro/OA 回读核对；原生 sweep 又可严格绑定 setup、global-variable selections、共享符号输入束、RDB point/corner 和 completion log。corner 模式通过 Bridge 公开 `include_raw=True` 取得原始 Detail CSV，在 VDA 层保留 Bridge 0.7.0 尚未结构化的正交 corner 列；不会修改 Bridge。若 Bridge completion wait 超时，只有运行前后恰好新增一个名称且其 log 已 completed 时才继续，多个新 history、同名覆盖或未完成日志均拒绝。配置/OA 回读属于 `bridge_readback`，运行输入与结果属于 `eda_result`，兼容性归一化、history 选择和一致性判断属于 `software_inference`。可选 `result_mapping` 再固定 exact scalar output expression、单位 scale 和 VDA constraints/objective。若人工旧 output 在声明点必然产生 calculator `eval err`，`expected_output_evaluation_errors` 只能按 exact test/output/point selector 声明未映射项；worker 与 executor 都要求 RDB 单元格和 log error 数完全相等、未解释错误为零，不能作为通用忽略开关。未声明时保持普通 Bridge-preserving run。它不能证明 history 名称此前不存在。旧 ADE L state 的非破坏迁移尚未纳入已验证 VDA operation。
- 对远端计算和 OA 写入分别授权；真实执行还需要计划 token，避免一句模糊指令直接改库。
- 将动作、候选点、指标、约束判定、最终选择和证据来源写入本地 JSON run record；调优任务还会在候选边界原子保存 checkpoint，并可在独立 OA 回读后续跑。

2026-07-19 已在 nics4304 完成首轮真实远端 smoke：Bridge doctor、反相器单点 Spectre、OA 建图与结构回读、局部参数写入与前后回读、9 点有限搜索和最佳参数写回，以及不可行规格下的禁止写回均通过。该轮 live smoke 使用的仍是手写 Spectre deck。

随后代码已改为从目标 OA schematic 调用 `si -batch` 生成结构网表，核对 OA 回读与网表中的实例、端口和 `W/L`，再用只含激励、负载、model 和 analysis 的 wrapper 运行 Spectre。第二轮只读 live smoke 验证了 OA/网表一致性和非空波形；第三轮又保存 `VDD_SRC:p`，得到 9 点真实 timing/energy 数据，在收紧规格下选择并回读 `Wn=0.6 µm, Wp=0.8 µm`，并通过不可行 + 预算耗尽恢复。第四轮加入候选级 checkpoint/resume；真实 9 点任务经历 3 次 SSH/tunnel 中断后，从候选 2、4 和最终写回边界继续，未重复已完成前缀，最终 run、checkpoint 和独立 OA 回读均成功。因此当前状态是 **inverter L5A same-source bounded closure and explicit checkpoint/resume recovery verified**。随后在 Bridge 的备份隔离分支上修复 Windows stale PID 和 no-tunnel 分支遗漏 `warm()`，强制终止精确 tunnel PID 后的只读 inspect 已自动恢复；运行中传输的随机 reset/timeout 仍未证明消失。补丁来源和升级办法见 `docs/third-party/virtuoso-bridge-local-patch.md`。

Bridge 隔离分支随后又增加幂等 SSH 传输的 1 秒/3 秒有界退避，以及只在 SKILL payload 发送前 `connect()` 被拒绝时 warm 一次 tunnel。压力任务仍真实遇到过一次候选 8 建连拒绝，但 checkpoint 成功恢复并完成 9/9；同-client 强制断链 smoke 已验证 pre-send 自动恢复。payload 发送后的中断不会自动重放，仍保留为显式 checkpoint/resume 边界。

同日 Gate 2A 在新建的 `vb_pdk_smoke/vda_cs_gate2a_001/schematic` 上完成 nominal 共源 DC。2026-07-20 又在已有 `vda_param_surface_001` 上由正式 `schematic.transform` 原位加入 RS0/NSRC，完成真实 DC、6 点 `Vbias×RS` 搜索和 checkpoint 恢复。随后 nominal/退化只读 AC 与各 6 点 bias/load 搜索通过。专用 `vda_cs_ac_tradeoff_001` 又在完全相同 W/L/RD/bias/load 下只加入 RS=2 kΩ，真实测得 gain −36.98%、BW −20.12%、GBW −49.66%；随后 W/RD/RS 8 点搜索全部可行，按 GBW 选择并写回 `W=1.0 µm, RD=20 kΩ, RS=1 kΩ`，得到 `gain=3.701 V/V, BW=8.190 GHz, GBW=30.306 GHz`。预算耗尽和人为不可行任务分别正确标为 partial，并完成最佳前缀写回或初始 OA 恢复。

## 快速开始

推荐 Python 3.13：

```powershell
cd "H:\Virtuoso Design Agent"
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

查看能力目录并生成计划：

```powershell
.\.venv\Scripts\vda.exe catalog
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-close-loop.demo.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-dc-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-create-parameter-surface.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-apply-instance-parameters.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-add-source-degeneration.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-dc-op.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-dc-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-inspect.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-ac-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-ac-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-ac-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-ac-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-ac-design-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-ac-tune.demo.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-linearity-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-noise-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-tune.demo.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-bias-load-budget.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-bias-load-infeasible.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-design-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-design-tune-infeasible.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-linearity-priority.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-length-vdd-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-pvt-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-pvt-bias-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-raw-fingers-ac-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-apply.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-apply-instance-parameters.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-bias-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-design-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-design-budget.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-design-infeasible.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-close-loop.demo.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-ac-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-cmrr-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-common-mode-range.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-common-mode-upper-edge.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-linearity-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-ac-tail-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-transform.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-dc-bias-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-width-checkpoint.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-ac-cmrr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-icmr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-linearity.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-noise.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-tail-transform.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-add.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-dc.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-ac-cmrr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-icmr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-linearity.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-noise.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-resistance-linearity-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-remove.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-restored-dc.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-tail.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-transform.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-inspect.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-dc.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-icmr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-linearity.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-noise.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-psrr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-psrr-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac-geometry-budget.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac-geometry-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac-geometry-infeasible.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-restore.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-prepare.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-capture.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-variables-apply.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-scoped-variables-apply.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-setup-apply.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-run.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-sweep-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-sweep-run.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-quality-outputs.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-quality-run.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-corners.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-scoped-variables.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-use-test-cl.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-corner-quality-run.bridge.json
```

计划会打印确认 token。复制该 token 后运行离线闭环：

```powershell
.\.venv\Scripts\vda.exe run examples\tasks\inverter-close-loop.demo.json `
  --adapter demo --execute --token <PLAN_TOKEN>
```

离线结果写入 `artifacts/runs/`，不会连接远端，也不会修改 Virtuoso。

## 接入真实 Bridge

已知本机 Bridge Python 默认路径：

```text
C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\python.exe
```

当前本地补丁能在没有可用 tunnel 时自动 warm；仍建议先核对 Bridge 分支/提交和状态，再做只读探测：

```powershell
C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\virtuoso-bridge.exe status
.\.venv\Scripts\vda.exe doctor --adapter bridge
```

真实任务仍必须先 `plan`，再使用同一个 token 执行。任务文件还要显式允许远端计算或写入。默认 profile `nics4304_tsmc28` 的反相器 transient 与共源 DC OP 均已有 OA/`si` 单点和有限搜索 live 证据，包括逐候选暂存、最佳参数提交、不可行/预算耗尽恢复与 checkpoint；共源 nominal/退化 AC、相干 transient 线性度和 ordinary noise PSF 也已有只读 live 证据，专用 cell 的 W/RD/RS `design.tune` 已真实执行。调优默认在 run record 旁生成 `*.checkpoint.json`；Bridge 外部恢复后用 `--resume <checkpoint>` 续跑。恢复会重新核对 task、plan token、adapter 和当前 OA 参数，已完成 checkpoint 或不属于基线/已确认写入/待确认写入/声明候选的 OA 状态都会被拒绝。换 library、cell 模板、PDK、analysis 或服务器也必须重新验证，不能从既有 smoke 外推。

```powershell
.\.venv\Scripts\vda.exe run examples\tasks\inverter-tune-energy.bridge.json `
  --adapter bridge --execute --token <PLAN_TOKEN>

# Bridge 按自身流程恢复后，使用上次打印的 checkpoint：
.\.venv\Scripts\vda.exe run examples\tasks\inverter-tune-energy.bridge.json `
  --adapter bridge --execute --token <SAME_PLAN_TOKEN> `
  --resume artifacts\runs\...\run-....checkpoint.json
```

`simulation.run` 不写 OA：省略器件尺寸时直接采用目标 OA 回读值；如果任务显式给出尺寸，则必须与 OA 一致，否则停止，不会用请求值覆盖 schematic。包含 OA 设计参数的 `design.tune` 和 `design.close_loop` 会在已授权写入的前提下逐点暂存参数并回读；无可行候选或可恢复中断时恢复搜索前参数。该暂存行为会明确出现在计划和 run record 中。

人工 ADE 交接先用可选的 `ade.prepare`，再用 `ade.capture`。两者的 `target.view` 都必须为 `maestro`。`prepare` 需要 OA 写授权、library 白名单和 cell 前缀，只创建一个新 Maestro view/test；若目标已经存在则失败，因此不会覆盖人工状态。`capture` 默认要求 setup 已保存且存在非空 EDA result artifacts；可用 `ade_capture.history` 固定某个 `Interactive.N` 等 history，并用 `require_structured_outputs: true` 要求 ADE Detail output/spec 表可读。捕获前由用户自己打开、调整、运行、保存并聚焦目标窗口；VDA 不会抢焦点或修改它。setup 标为 `bridge_readback`，网表、PSF、Spectre log 和结构化 output/spec 标为 `eda_result`，自动选择最新 history 标为 `software_inference`，显式 history 标为 `user_input`。

不需要人工窗口的已保存 setup 可用 `ade.run`。它要求 `target.view: "maestro"`、`ade_run` 设置和 `allow_remote_compute: true`，但不要求 `allow_remote_write`；worker 新开 background session，回读 tests，运行 setup，并用本次调用返回的 history 读取逐点 output/spec。默认同时启用 `require_structured_outputs: true` 和 `require_artifact_manifest: true`：后者从 Cadence 暴露的 library/analog-run 路径推导 project 与 scratch Maestro 根，只枚举确切 history 下的核心 `netlist`、`input.scs`、PSF/结果、Spectre log 及同名 `.rdb/.msg.db`，用远端 `sha256sum` 生成小型清单，再经 Bridge 公共下载接口读回。核心网表、非空结果或日志缺失，路径逃逸，或者 project/scratch 同一逻辑文件内容冲突都会失败；显式关闭任一要求且证据缺失时 run record 只能是 `partial`。远端清单保留在 profile 的 `/data/xum` run root 下，本操作不下载完整波形、不配置/保存 setup、不写 OA。精确 history 路径绑定不等于名称唯一：命名/覆盖策略仍由已保存 setup 决定，VDA 当前不能在运行前证明该名称不存在。单点 nics4304 live 已覆盖 OA 到真实 `input.scs` 的 raw 参数核对；原生 CL、VDD×CL 和 test-scope CL×VDD-corner sweep 又覆盖 `input.scs` 显式 include 的 sibling `netlist` 输入束、exact-history RDB/log、global selection 与逐点/逐 corner 有效值。可选 `result_mapping` 在 strict sweep 之上要求一个 exact test、全部 sweep variable 的参数映射，以及每个 metric 的 exact output、预期 expression、scale/unit；同一个 sweep variable 可以绑定多个 OA 参数，例如 `VDD0.vdc` 与 `VIN0.v2`。worker 固定保存的 output state，executor 才把有限 RDB scalar 映射到 constraints/objective。缺失、空、非有限值、表达式漂移或全不可行都不会产生 selected point。单 test 环境 VDD corner/test-scope quality mapping 已 live；真实 process/temperature corner、multi-test mapping 和共源 L/VDD 联合搜索仍是后续 Gate。人工相关验证见 `docs/deferred-manual-gates.md`。

上一段“后续 Gate”专指已保存 Maestro/ADE setup。direct common-source `si`/Spectre 路径已经完成 L/VDD 搜索和固定设计 TT/SS/FF；两条状态不混为同一仿真真源。

需要证明原生 parametric sweep 真正进入仿真时，可在 `ade_run.sweep_verification` 中额外声明 exact tests/corners、各 scope 保存的逗号变量值、连续 expected points，以及每个 test/variable 对应的 `instance.oa_parameter`。该可选严格门强制同时打开 structured outputs、artifact manifest 和 simulator input consistency，并在运行前后精确回读 setup。若 Cadence 保留任意逐点目录，VDA 坚持每个 point/test 都有完整 `input.scs` 和结果；若完全没有逐点目录，只接受已 live 验证的 IC6.1.8 模式：每个 test 一个已哈希且有显式 include 关系的 `input.scs`+`netlist` 符号输入束、OA/Spectre 变量引用、一个 exact-history RDB、完成点数精确的 log，以及 RDB 中每点匹配变量和非空 scalar output。corner 模式再要求 exact global selection、scope precedence、真实 Maestro point 数与完整 raw Detail corner grid。默认要求 log 零错误；只有 `expected_output_evaluation_errors` 精确声明的未映射 legacy calculator 单元格才可逐点计入，实际 RDB `eval err` 集合、log 数量和 executor 重算必须完全一致，任何未解释错误仍失败。数据库模式明确记录 `exact_point_input_result_binding_verified=false`，不会虚构逐点文件。任务预期属于 `user_input`，setup/OA 属于 `bridge_readback`，input/RDB/log 属于 `eda_result`，对应与哈希聚合属于 `software_inference`。未声明该字段的普通 `ade.run` 不受限制。2026-07-22 nics4304 live 已从 global `CL=1f,2f,4f` 三点扩展到 global `VDD=0.8,0.9` 的六点二维 Gate，又闭合 test-scope CL × 三列 environmental-corner raw result grid。详见 `examples/tasks/inverter-ade-sweep-*.bridge.json`、`examples/tasks/inverter-ade-vdd-*.bridge.json`、[二维真实验证记录](docs/validation/2026-07-22-ade-inverter-vdd-cl-live.md)和[scoped-corner 真实验证记录](docs/validation/2026-07-22-ade-inverter-scoped-corner-live.md)。

自动修改 design variable 使用 `ade.variables.apply`。任务必须列出 `expected_tests`；使用 corner scope 时还必须列出 exact `expected_corners`。每个 value update 给出 `name`、显式 `expected_value`、`value`，以及可选的 `scope: global|test|corner`/`scope_name`；`expected_value: null` 表示要求变量在该 scope 不存在。同名变量可分别出现在不同 scope，但同一 scope 不能重复。`global_selection_updates` 则用 `expected_enabled → enabled` 对 global-variable 选择做 CAS，并在写前、即时和独立重开阶段比较完整 enabled/disabled 集合，拒绝未声明名称漂移。任何已配置 Maestro session 已打开时都会保守拒绝。该 operation 需要完整 OA-write 授权，只在所有 tests/corners/旧值/selection 匹配后逐项写入，保存一次 setup，再用全新 session 逐 scope 回读；不运行仿真或修改 schematic/test/analysis/output/corner membership。逗号列表只是该 scope 的 sweep 声明，必须由后续 strict `ade.run` 证明真正进入 simulator。

新增 enabled corner 使用独立 `ade.corners.apply`。它要求 exact tests 与旧 corner 有序列表，且只允许在末尾新增明确不存在的命名 corner；写后立即回读、保存一次、独立重开复核，不删除、改名或替换 corner，也不配置 model files、temperature 或 process section。反相器 live 中的 `VDA_LOW_VDD/VDA_NOMINAL_VDD` 仅承载 VDD scope override，仍使用 nominal `top_tt`，不能称为 PVT/process corner。

自动微调 analysis/output 使用 `ade.setup.apply`。任务必须列出 exact `expected_tests`；每个 analysis update 给出目标 test/analysis、完整旧 `enabled/options`（不存在时为 `null`）、目标 enabled 和 option delta。output 只支持新增：命名 net output 要求 `signal_name`，point output 要求 calculator `expression`，可附 `lt`/`gt` spec。worker 在任何 writer 前读取全部旧 analysis 并确认所有 output 名不存在；任一不符零写入。写后逐项结构化回读，全部一致才保存一次，再用全新 session 核对持久化状态。它复用 Bridge public writer 和 SKILL channel，没有修改 Bridge；但首版不替换已有 output，也只接受可精确回读的扁平 analysis option。成功只证明 setup 配置持久化为 `bridge_readback`，不代表 analysis 已运行或 output 已产生 `eda_result`。

共源任务省略 `analysis` 时保持向后兼容的 `dc`。AC 必须显式设置 `analysis: "ac"` 与 `ac_sweep`；线性度使用 `analysis: "transient"` 与 `linearity_sweep`；普通噪声使用 `analysis: "noise"` 与 `noise_sweep`。固定质量组合使用 `analysis: "quality"`，并强制同时声明上述三种 sweep；任一子分析不完整、参数不一致或共享 DC 指标不一致都会拒绝整个候选。worker 只做一次 OA 回读与 `si` 网表生成，再从同一网表分别运行三种 Spectre wrapper；组合逻辑标为 `software_inference`，连续指标仍保持 `eda_result`。所有显式和默认 sweep 字段都进入 token 与证据。线性度在一个 Spectre nested sweep 中运行按幅度递增的相干正弦，P1dB 未被声明范围包围时只报告 unresolved；noise 对 Bridge 已下载的普通 noise PSF 做频带积分，不把 AC 或 transient 数据包装成噪声。`load_ff` 是动态分析的可选 testbench 负载，不写 OA。若 `design.tune` 的搜索维度只有 `bias_v/vdd_v/load_ff` 这类 testbench 条件，计划和 executor 不要求或执行 OA 写入；若搜索包含 W/L/RD/RS，则仍逐候选写入、回读、checkpoint，并只提交最佳可行 OA 参数。`operating_conditions` 是 common-source 的显式可选有限验证集合，可用于 `simulation.run`、`design.tune` 或 `design.close_loop`；省略时仍是原有单条件流程。启用后每个候选只暂存一次 OA、生成一份已核对 `si` 网表，再跨所有条件运行；每个条件必须独立完整并通过，objective 取保守最坏值。逐条件 VDD 与候选 `vdd_v` 不能同时声明；若条件省略 VDD，则共同继承该候选 VDD。

差分对电源抑制使用显式 `analysis: "psrr"` 与 `ac_sweep`。每个候选只回读一次 OA、生成并核对一份 `si` 网表，再依次运行 1 V 平衡差模、1 V VDD AC 注入和 1 V VSS AC 注入；供电注入时 INP、INN 和 BIAS 保持对地 DC 参考。三次 DC 工作点、频率网格和网表 SHA 必须一致。固定输出契约下计算 `PSRR+=|Ad/Avdd|` 与 `PSRR-=|Ad/Avss|`，同时保留 supply-to-output gain、低频 PSRR、扫频最差 PSRR 和首次下降 3 dB 频点。波形和 OP 属于 `eda_result`，OA 属于 `bridge_readback`，比值、交点与一致性判断属于 `software_inference`。首版 worker 要求真实 OA 尾管，拒绝用理想尾源 wrapper 代替 VSS 敏感电路。2026-07-24 nominal active-load 单点已完成三组各 181 点的真实 AC，DC/网格一致且独立 OA 回读无变化；该点仅通过饱和与摆幅门，没有声明或通过 PSRR 目标。

人工指定实例参数时使用 `instance_parameter_updates`，例如 `MN0.fingers="2"` 或 `RD0.r="22k"`。VDA 保留原始字符串，不猜单位、别名、枚举或布尔编码，也不因通用 reader 对空值/长值的摘要策略而提前拒绝 Bridge 可接受的请求；写后改用独立的目标 CDF 值相等检查。该固定写入可用于独立 `parameters.apply`，也可与模板 semantic 参数或显式 raw sweep 组合；请求标为 `user_input`，真实 OA 确认标为 `bridge_readback`，demo 结果仍只标为 `software_inference`。首次不一致时至多按声明顺序重放一次，计划会明确披露；仍不一致则失败。CDF 的 `editable`/`display` 元数据只作诊断，不能作为 allowlist，因为真实 smoke 已出现 `RD0.r` 报告不可编辑但能持久化的情况。CDF callback 引起的其他参数联动会保留在完整 Bridge 回读里，但只有任务明确请求且真实保持的字段会宣称确认。有限调优另用 `instance_parameter_space` 显式列出实际 CDF 字段及有限字符串值；它不会把全部可读字段自动纳入搜索，也不会收窄独立 `parameters.apply` 的 Bridge 别名能力。

2026-07-20 的专用 `vda_param_surface_001` live smoke 已闭合 `MN0.fingers=2` 和 `RD0.r=22K` 的 callback、立即 OA 回读和独立再次回读。相同任务中的 `MN0.m=2` 被 PDK callback 恢复为 `1`，因此保留为失败边界；这说明“VDA 能尝试 Bridge 参数”不等于“每个 PDK CDF 字段都可物理持久化”。多字段写入不是 OA 事务，失败可能留下已保存的前缀字段。

源极退化的增量实现不会重置未点名参数：共源 semantic 写入只向 Bridge 发送任务实际包含的 `W/L/RD/RS` 字段，不再附带 `fingers=1` 或 `m=1`。transform 强制用 append mode 打开已有 cellview，拒绝已有未保存改动，编辑 batch 失败时 purge 本次未保存缓存；前后独立回读再逐项核对 MN0/RD0 的完整参数、master、位置、pins 和 nets。add 只允许 MN0.S 改接 NSRC、增加 RS0/NSRC 和设置 RS0.r；remove 只接受无参数的显式动作，并几何唯一选择 RS0 两条 VDA stub 后删除。两者均幂等。remove 可绑定 add 前由 Bridge 回读的实例/pin/标签/导线 placement SHA-256，保存后不一致即失败。

`vda_param_surface_001` 和 `vda_cs_ac_tradeoff_001` 已依次覆盖同一 cellview 原位退化、DC、nominal/退化 AC、bias/load、W/RD/RS、线性度/功耗/noise 和三分析质量调优；多次 transport 失败都保留为 `system_event` 并经 OA readback/checkpoint 恢复。2026-07-22 闭合 L/VDD 四点写回和固定设计三条件 PVT 验证；2026-07-23 又闭合不写 OA 的两候选 PVT-aware bias 搜索，并在独立新 cell 上完成 source degeneration add→DC/AC→remove 的可逆 Gate。恢复后 placement、`si` 网表 SHA-256 及 17 个 DC/28 个 AC 指标均与 add 前完全一致；随后 `MN0.fingers=1/2` 两点又闭合 callback、定向回读、不同同源网表、GBW 选优、最佳写回和首候选 transport 恢复。尚未 live 闭合的是 OA 设计变量跨 PVT 写回、复杂多字段 callback 联合搜索和更复杂拓扑。完整 live 证据见 `docs/validation/` 下对应记录。

## 安全模型

真实写入需同时满足：

1. CLI 提供 `--execute`。
2. CLI 提供与当前任务和计划一致的 token。
3. 任务设置 `allow_remote_write: true`。
4. `target.library` 等于任务声明的 `allowed_library`。
5. cell 名满足 `required_cell_prefix`，默认 `vda_`。
6. 默认 `replace_existing: false`。

仿真虽不修改 OA，也会创建远端 scratch，因此需要 `allow_remote_compute: true`。密码、Bridge `.env` 和 license 内容不进入任务或 run record。

自动 netlisting 产物按 profile 写在 `/data/xum/virtuoso_bridge_smoke/vda_<task>_<nonce>/`，保留 `si.env`、`si` 日志、结构网表和 wrapper 供审计。run record 保存路径、SHA-256、参数一致性和波形指标，不以 return code 0 或文件存在单独判定成功。

## 为什么暂不做成 Skill

当前先把稳定能力做成普通本地工具：契约、状态机、执行边界和证据格式都可以独立测试。Codex 可以调用 CLI 充当上层 agent；当命令和边界稳定后，再决定是否封装成 Skill 或 MCP。这样不会把尚未稳定的实验流程固化成提示词约定。

## 文档

- [L5 路线与定义](docs/l5-roadmap.md)
- [系统架构](docs/architecture.md)
- [三类起步电路与验收门](docs/initial-circuits.md)
- [首个决策记录](docs/decisions/0001-l5a-first.md)
- [PDK 默认使用晶圆厂 CMOS 的决策](docs/decisions/0002-foundry-cmos-pdk-default.md)
- [2026-07-19 反相器 L5A smoke](docs/validation/2026-07-19-inverter-l5a-smoke.md)
- [2026-07-19 共源放大器 Gate 2A DC smoke](docs/validation/2026-07-19-common-source-gate2a-dc-smoke.md)
- [2026-07-19 显式实例参数能力验证](docs/validation/2026-07-19-explicit-instance-parameters.md)
- [2026-07-20 源极退化原位变更实现验证](docs/validation/2026-07-20-source-degeneration-in-place.md)
- [2026-07-20 源极退化原位微调与同源 DC 真实验证](docs/validation/2026-07-20-source-degeneration-live.md)
- [2026-07-23 共源源极退化可逆拓扑微调真实验证](docs/validation/2026-07-23-common-source-reversible-topology-live.md)
- [2026-07-20 共源复数 AC 指标与调优能力实现](docs/validation/2026-07-20-common-source-ac-implementation.md)
- [2026-07-20 共源与源极退化只读同源 AC 真实验证](docs/validation/2026-07-20-common-source-ac-live.md)
- [2026-07-20 共源 AC 控制变量与 W/RD/RS 真实调优](docs/validation/2026-07-20-common-source-ac-design-tuning-live.md)
- [2026-07-20 共源功耗、线性度与 noise 实现](docs/validation/2026-07-20-common-source-quality-local.md)
- [2026-07-20 共源功耗、线性度与 noise 只读真实验证](docs/validation/2026-07-20-common-source-quality-live.md)
- [2026-07-21 共源多 analysis 质量组合本地验证](docs/validation/2026-07-21-common-source-quality-bundle-local.md)
- [2026-07-21 共源多 analysis 质量组合真实验证](docs/validation/2026-07-21-common-source-quality-bundle-live.md)
- [2026-07-21 共源质量驱动设计参数写回真实验证](docs/validation/2026-07-21-common-source-quality-design-tuning-live.md)
- [2026-07-21 ADE 双向人工交接本地实现](docs/validation/2026-07-21-ade-human-handoff-local.md)
- [2026-07-21 ADE 后台运行与结果回收本地实现](docs/validation/2026-07-21-ade-background-run-local.md)
- [2026-07-21 ADE background exact-history 产物清单本地实现](docs/validation/2026-07-21-ade-background-artifact-manifest-local.md)
- [2026-07-21 Maestro 全局变量 CAS patch 本地实现](docs/validation/2026-07-21-ade-variable-patch-local.md)
- [2026-07-21 Maestro scoped 变量 CAS 扩展本地实现](docs/validation/2026-07-21-ade-scoped-variable-patch-local.md)
- [2026-07-21 Maestro analysis/output setup patch 本地实现](docs/validation/2026-07-21-ade-setup-patch-local.md)
- [2026-07-22 ADE 原生 sweep 逐点同源证据本地实现](docs/validation/2026-07-22-ade-native-sweep-consistency-local.md)
- [2026-07-22 ADE 原生 CL sweep 同源闭环真实验证](docs/validation/2026-07-22-ade-native-sweep-consistency-live.md)
- [2026-07-22 ADE 反相器 delay/skew/供电能量 constraints 真实验证](docs/validation/2026-07-22-ade-inverter-quality-constraints-live.md)
- [2026-07-22 ADE 反相器 VDD×CL 二维质量 Gate](docs/validation/2026-07-22-ade-inverter-vdd-cl-live.md)
- [2026-07-22 ADE 反相器 scoped-variable × environmental-corner Gate](docs/validation/2026-07-22-ade-inverter-scoped-corner-live.md)
- [2026-07-22 共源 L/VDD 质量调优与真实 PVT Gate](docs/validation/2026-07-22-common-source-length-vdd-pvt-live.md)
- [2026-07-23 共源可选 PVT-aware 调优 live Gate](docs/validation/2026-07-23-common-source-optional-pvt-tuning-live.md)
- [2026-07-23 差分对 Gate 3 同源 DC/AC/CMRR/线性度真实验证](docs/validation/2026-07-23-differential-pair-gate3-live.md)
- [2026-07-23 差分对 Gate 4 真实尾管多分析同源验证](docs/validation/2026-07-23-differential-pair-real-tail-gate4-live.md)
- [2026-07-23 差分对 Gate 5 对称源极退化可逆全分析验证](docs/validation/2026-07-23-differential-pair-source-degeneration-gate5-live.md)
- [2026-07-23 差分对 Gate 6 PMOS 电流镜负载本地实现](docs/validation/2026-07-23-differential-pair-current-mirror-gate6-local.md)
- [2026-07-23 差分对 Gate 6 PMOS 电流镜负载同源闭环真实验证](docs/validation/2026-07-23-differential-pair-current-mirror-gate6-live.md)
- [2026-07-23 差分对 PSRR 三次同网表 AC 本地实现](docs/validation/2026-07-23-differential-pair-psrr-local.md)
- [2026-07-24 差分对 PSRR 三次同网表 AC 真实单点](docs/validation/2026-07-24-differential-pair-psrr-live.md)
- [延期的人工 ADE Gate](docs/deferred-manual-gates.md)
