# Virtuoso Design Agent

Virtuoso Design Agent 是 `virtuoso-bridge-lite` 之上的受控设计编排层。它把“建原理图、读回、应用参数、跑仿真、判定规格、有限调优”组织成可单独执行、可组合、可审计的任务，而不是再造一套 Bridge。

当前版本从 **L5A** 起步：在已知 PDK、固定电路模板、显式规格和有限搜索空间内完成闭环。TSMC28 反相器 Gate 1、电阻负载 NMOS 共源级 nominal DC，以及同一已有 cellview 上的源极退化 transform/DC/RS 有限调优均有真实 OA/`si`/Spectre 证据。2026-07-20 又真实通过共源 nominal/退化复数 AC、12 点不写 OA 的 bias/load 条件搜索、专用 cell 上的 W/RD/RS 调优，以及同一 cell 的 5 点相干 transient 线性度/真实 VDD 功耗和 211 点普通 noise PSF。2026-07-21 固定 `quality` 组合先通过真实只读 bias/load 搜索，随后又完成 8 点 W/RD/RS 三分析搜索、逐候选 OA 写入、transport checkpoint 恢复、最佳写回和全不可行恢复。一个线性度优先任务进一步自动把 RS 从 1 kΩ 改为 2 kΩ，以 29.30% GBW 损失换取 41.93% THD 降低、37.26% P1dB 提升和 17.27% DC 功耗降低。现在又加入首个 ADE 人工交接切片：VDA 可以只读捕获人工聚焦的 Maestro setup、history、Spectre 输入/结果和逐点 output/spec，而不替用户保存、运行或写 OA；该能力尚待 nics4304 live smoke。L/VDD 联合搜索和 corner 仍未闭合，因此仍不能称为完整 L5B 设计质量闭环。

## 当前能做什么

- 将任务编译为带副作用标记的稳定执行计划。
- 单独规划或执行：`schematic.create`、`schematic.inspect`、`schematic.transform`、`parameters.apply`、`ade.capture`、`simulation.run`、`design.tune`、`design.close_loop`。当前 `schematic.transform` 只开放共源级的受控源极退化补丁。
- 用确定性 demo adapter 离线验证闭环、规格判定和参数选择；结果明确标为 `software_inference`。
- 通过独立 worker 调用本机 `virtuoso-bridge-lite` 环境。反相器支持 `OA -> si -> Spectre transient` 的 timing、过冲/欠冲和周期供电能量；共源级支持同一 `OA -> si` 网表上的 DC OP、复数 AC、相干正弦 transient 幅度 sweep 和普通 noise sweep。可提取 `Id/VGS/VDS/VDSAT/gm/gds`、真实 VDD 功耗与 KCL、低频增益、首个 −3 dB 带宽、GBW、unity、HD2/HD3、THD、P1dB，以及频带积分的输出/输入参考噪声；单项执行与提取均有 live 证据。`analysis: "quality"` 已在一次 OA/`si` 核对后依次运行 AC、linearity、noise，并用真实联合指标完成 bias/load 条件搜索与 W/RD/RS 设计参数搜索、约束过滤、最佳 OA 写回和 checkpoint 恢复。
- 源极退化不新建第二套模板或仿真器：在同一 common-source cellview 中把 `MN0.S: VSS -> NSRC`，只新增 `RS0(NSRC,VSS)`；随后由同一 inspect、参数应用、`si` 网表解析、DC 指标和有限搜索路径动态识别该变体。
- `existing_schematic` 提供不依赖固定电路模板的 Bridge 能力面：`schematic.inspect` 保留 Bridge 的完整结构结果和所有可回读 CDF 参数；`parameters.apply` 可按实例透传 Bridge 接受的参数字符串，写入后用定向 CDF 读取再次核对。反相器/共源模板仍可在同一任务中组合 semantic parameters 与原始实例参数。
- `ade.capture` 是人工介入边界，不是另一个仿真器：用户在 ADE Explorer/Assembler Maestro 中调整变量、analysis、sweep、output/spec 并运行后，先保存并聚焦该窗口；VDA 核对 library/cell/view/session，捕获 setup、指定或最新 history、真实 Spectre netlist/PSF/log 的大小与 SHA-256，并尝试读取全部 sweep point 的 output/spec 表。它不打开、保存、关闭或重跑 ADE，也不把捕获成功包装成规格闭环。当前 Bridge 的直接高层接口是 Maestro；旧 ADE L state 的非破坏迁移尚未纳入已验证 VDA operation。
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
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-capture.bridge.json
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

人工 ADE 交接使用 `ade.capture`。任务的 `target.view` 必须为 `maestro`，默认要求 setup 已保存且存在非空 EDA result artifacts；可用 `ade_capture.history` 固定某个 `Interactive.N` 等 history，并用 `require_structured_outputs: true` 要求 ADE Detail output/spec 表可读。执行前由用户自己打开、调整、运行、保存并聚焦目标窗口；VDA 只捕获当前状态，不会抢焦点或修改它。setup 标为 `bridge_readback`，网表、PSF、Spectre log 和结构化 output/spec 标为 `eda_result`，自动选择最新 history 标为 `software_inference`，显式 history 标为 `user_input`。这一操作适合把人工结果交回 VDA 审计；把 VDA 候选批量写入 ADE/Maestro、原生 sweep/corner 执行和最终 OA 提交仍是后续 Gate。

共源任务省略 `analysis` 时保持向后兼容的 `dc`。AC 必须显式设置 `analysis: "ac"` 与 `ac_sweep`；线性度使用 `analysis: "transient"` 与 `linearity_sweep`；普通噪声使用 `analysis: "noise"` 与 `noise_sweep`。固定质量组合使用 `analysis: "quality"`，并强制同时声明上述三种 sweep；任一子分析不完整、参数不一致或共享 DC 指标不一致都会拒绝整个候选。worker 只做一次 OA 回读与 `si` 网表生成，再从同一网表分别运行三种 Spectre wrapper；组合逻辑标为 `software_inference`，连续指标仍保持 `eda_result`。所有显式和默认 sweep 字段都进入 token 与证据。线性度在一个 Spectre nested sweep 中运行按幅度递增的相干正弦，P1dB 未被声明范围包围时只报告 unresolved；noise 对 Bridge 已下载的普通 noise PSF 做频带积分，不把 AC 或 transient 数据包装成噪声。`load_ff` 是动态分析的可选 testbench 负载，不写 OA。若 `design.tune` 的搜索维度只有 `bias_v/vdd_v/load_ff` 这类 testbench 条件，计划和 executor 不要求或执行 OA 写入；若搜索包含 W/L/RD/RS，则仍逐候选写入、回读、checkpoint，并只提交最佳可行 OA 参数。

人工指定实例参数时使用 `instance_parameter_updates`，例如 `MN0.fingers="2"` 或 `RD0.r="22k"`。VDA 保留原始字符串，不猜单位、别名、枚举或布尔编码，也不因通用 reader 对空值/长值的摘要策略而提前拒绝 Bridge 可接受的请求；写后改用独立的目标 CDF 值相等检查。该路径目前属于 `parameters.apply`，可以单独使用，也可以与模板 semantic parameters 组合；请求标为 `user_input`，真实 OA 确认标为 `bridge_readback`，demo 结果仍只标为 `software_inference`。首次不一致时至多按声明顺序重放一次，计划会明确披露；仍不一致则失败。CDF 的 `editable`/`display` 元数据只作诊断，不能作为 allowlist，因为真实 smoke 已出现 `RD0.r` 报告不可编辑但能持久化的情况。CDF callback 引起的其他参数联动会保留在完整 Bridge 回读里，但只有任务明确请求且真实保持的字段会宣称确认。任意实例参数尚未自动进入搜索空间，这是下一步拟合能力而不是永久限制。

2026-07-20 的专用 `vda_param_surface_001` live smoke 已闭合 `MN0.fingers=2` 和 `RD0.r=22K` 的 callback、立即 OA 回读和独立再次回读。相同任务中的 `MN0.m=2` 被 PDK callback 恢复为 `1`，因此保留为失败边界；这说明“VDA 能尝试 Bridge 参数”不等于“每个 PDK CDF 字段都可物理持久化”。多字段写入不是 OA 事务，失败可能留下已保存的前缀字段。

源极退化的增量实现不会重置未点名参数：共源 semantic 写入只向 Bridge 发送任务实际包含的 `W/L/RD/RS` 字段，不再附带 `fingers=1` 或 `m=1`。transform 强制用 append mode 打开已有 cellview，拒绝已有未保存改动，编辑 batch 失败时 purge 本次未保存缓存；前后独立回读再逐项核对 MN0/RD0 的完整参数、master、位置、pins 和 nets。只有 MN0.S 改接 NSRC、增加 RS0/NSRC 以及任务给定的 RS0.r 被允许。重复 transform 幂等；已退化拓扑上改变阻值只写 RS0。

2026-07-20 的 `vda_param_surface_001` live smoke 已由正式 `schematic.transform` 完成同一 cellview 原位退化，并复用同一 OA→`si`→Spectre DC 与 6 点 `Vbias×RS` 搜索。随后 nominal/退化复数 AC、两个 6 点 bias/load 搜索和专用 cell 的 W/RD/RS AC design tuning 均已通过；同一专用 cell 又完成 5 点线性度/真实 VDD 功耗和 1 kHz–10 GHz ordinary noise PSF。多次 `WinError 10054`、upload/download timeout 都被保留并在 OA 回读或 tunnel 重建后恢复；这验证了恢复边界，也说明底层 transport 债务仍存在。2026-07-21 的 4 点只读质量搜索得到 2 个可行点和 2 个同时违反 THD/功耗约束的点；最高 GBW 点因质量约束被拒绝。随后 8 点 W/RD/RS 质量搜索全部三项完整，在候选 4 transport 中断后跳过前三点恢复，按 GBW 选择并写回 `W=1 µm/RD=20 kΩ/RS=1 kΩ`；2 点全不可行任务恢复初始 OA，且不再把最接近候选标成 selected。最后的线性度优先控制任务在同一约束下自动选择并写回 `RS=2 kΩ`。corner、L/VDD 联合调优和自动逆变换仍未闭合。DC 事故与恢复见 `docs/validation/2026-07-20-source-degeneration-live.md`，AC 实现见 `docs/validation/2026-07-20-common-source-ac-implementation.md`，只读 AC 见 `docs/validation/2026-07-20-common-source-ac-live.md`，设计参数调优见 `docs/validation/2026-07-20-common-source-ac-design-tuning-live.md`，设计质量单项 live 证据见 `docs/validation/2026-07-20-common-source-quality-live.md`，组合实现见 `docs/validation/2026-07-21-common-source-quality-bundle-local.md`，只读组合 Gate 见 `docs/validation/2026-07-21-common-source-quality-bundle-live.md`，质量驱动写回见 `docs/validation/2026-07-21-common-source-quality-design-tuning-live.md`。

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
- [2026-07-19 反相器 L5A smoke](docs/validation/2026-07-19-inverter-l5a-smoke.md)
- [2026-07-19 共源放大器 Gate 2A DC smoke](docs/validation/2026-07-19-common-source-gate2a-dc-smoke.md)
- [2026-07-19 显式实例参数能力验证](docs/validation/2026-07-19-explicit-instance-parameters.md)
- [2026-07-20 源极退化原位变更实现验证](docs/validation/2026-07-20-source-degeneration-in-place.md)
- [2026-07-20 源极退化原位微调与同源 DC 真实验证](docs/validation/2026-07-20-source-degeneration-live.md)
- [2026-07-20 共源复数 AC 指标与调优能力实现](docs/validation/2026-07-20-common-source-ac-implementation.md)
- [2026-07-20 共源与源极退化只读同源 AC 真实验证](docs/validation/2026-07-20-common-source-ac-live.md)
- [2026-07-20 共源 AC 控制变量与 W/RD/RS 真实调优](docs/validation/2026-07-20-common-source-ac-design-tuning-live.md)
- [2026-07-20 共源功耗、线性度与 noise 实现](docs/validation/2026-07-20-common-source-quality-local.md)
- [2026-07-20 共源功耗、线性度与 noise 只读真实验证](docs/validation/2026-07-20-common-source-quality-live.md)
- [2026-07-21 共源多 analysis 质量组合本地验证](docs/validation/2026-07-21-common-source-quality-bundle-local.md)
- [2026-07-21 共源多 analysis 质量组合真实验证](docs/validation/2026-07-21-common-source-quality-bundle-live.md)
- [2026-07-21 共源质量驱动设计参数写回真实验证](docs/validation/2026-07-21-common-source-quality-design-tuning-live.md)
- [2026-07-21 ADE 人工交接捕获本地实现](docs/validation/2026-07-21-ade-human-handoff-local.md)
