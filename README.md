# Virtuoso Design Agent

Virtuoso Design Agent 是 `virtuoso-bridge-lite` 之上的受控设计编排层。它把“建原理图、读回、应用参数、跑仿真、判定规格、有限调优”组织成可单独执行、可组合、可审计的任务，而不是再造一套 Bridge。

当前版本从 **L5A** 起步：在已知 PDK、固定电路模板、显式规格和有限搜索空间内完成闭环。TSMC28 反相器 Gate 1 与电阻负载 NMOS 共源级 Gate 2A（DC operating point）已有真实 adapter；共源 AC、源极退化和差分对仍是后续验收门。

## 当前能做什么

- 将任务编译为带副作用标记的稳定执行计划。
- 单独规划或执行：`schematic.create`、`schematic.inspect`、`parameters.apply`、`simulation.run`、`design.tune`、`design.close_loop`。
- 用确定性 demo adapter 离线验证闭环、规格判定和参数选择；结果明确标为 `software_inference`。
- 通过独立 worker 调用本机 `virtuoso-bridge-lite` 环境。反相器支持 `OA -> si -> Spectre transient` 的 timing、过冲/欠冲和周期供电能量；共源级支持 `OA -> si -> Spectre DC OP` 的 `Id/VGS/VDS/VDSAT/gm/gds`、KCL、饱和余量和输出摆幅余量。
- `existing_schematic` 提供不依赖固定电路模板的 Bridge 能力面：`schematic.inspect` 保留 Bridge 的完整结构结果和所有可回读 CDF 参数；`parameters.apply` 可按实例透传 Bridge 接受的参数字符串，写入后用定向 CDF 读取再次核对。反相器/共源模板仍可在同一任务中组合 semantic parameters 与原始实例参数。
- 对远端计算和 OA 写入分别授权；真实执行还需要计划 token，避免一句模糊指令直接改库。
- 将动作、候选点、指标、约束判定、最终选择和证据来源写入本地 JSON run record；调优任务还会在候选边界原子保存 checkpoint，并可在独立 OA 回读后续跑。

2026-07-19 已在 nics4304 完成首轮真实远端 smoke：Bridge doctor、反相器单点 Spectre、OA 建图与结构回读、局部参数写入与前后回读、9 点有限搜索和最佳参数写回，以及不可行规格下的禁止写回均通过。该轮 live smoke 使用的仍是手写 Spectre deck。

随后代码已改为从目标 OA schematic 调用 `si -batch` 生成结构网表，核对 OA 回读与网表中的实例、端口和 `W/L`，再用只含激励、负载、model 和 analysis 的 wrapper 运行 Spectre。第二轮只读 live smoke 验证了 OA/网表一致性和非空波形；第三轮又保存 `VDD_SRC:p`，得到 9 点真实 timing/energy 数据，在收紧规格下选择并回读 `Wn=0.6 µm, Wp=0.8 µm`，并通过不可行 + 预算耗尽恢复。第四轮加入候选级 checkpoint/resume；真实 9 点任务经历 3 次 SSH/tunnel 中断后，从候选 2、4 和最终写回边界继续，未重复已完成前缀，最终 run、checkpoint 和独立 OA 回读均成功。因此当前状态是 **inverter L5A same-source bounded closure and explicit checkpoint/resume recovery verified**。随后在 Bridge 的备份隔离分支上修复 Windows stale PID 和 no-tunnel 分支遗漏 `warm()`，强制终止精确 tunnel PID 后的只读 inspect 已自动恢复；运行中传输的随机 reset/timeout 仍未证明消失。补丁来源和升级办法见 `docs/third-party/virtuoso-bridge-local-patch.md`。

Bridge 隔离分支随后又增加幂等 SSH 传输的 1 秒/3 秒有界退避，以及只在 SKILL payload 发送前 `connect()` 被拒绝时 warm 一次 tunnel。压力任务仍真实遇到过一次候选 8 建连拒绝，但 checkpoint 成功恢复并完成 9/9；同-client 强制断链 smoke 已验证 pre-send 自动恢复。payload 发送后的中断不会自动重放，仍保留为显式 checkpoint/resume 边界。

同日 Gate 2A 在新建的 `vb_pdk_smoke/vda_cs_gate2a_001/schematic` 上完成。OA 中的 `MN0 + analogLib/RD0` 经结构回读和 `si` 网表核对后运行 Spectre DC OP；初始 `W=1 µm, Vbias=0.45 V` 被证据判为线性区，随后 6 点 `W × Vbias` 有限搜索得到 3 个可行点和 3 个不可行点，选择 `W=0.5 µm, Vbias=0.35 V`。最终 OA 重新 netlist 的独立紧规格复核通过，`Id=22.291 µA`、`VDS=0.4542 V`、`VDSAT=0.1044 V`、饱和/摆幅余量 `0.3498 V`；真实不可行 guard 完成恢复，预算 guard 经一次 transport 中断后从 checkpoint 完成且没有重复候选。这只表示 **common-source Gate 2A DC same-source loop verified**；尚未验证 AC gain/bandwidth、源极退化、noise 或 corner。

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

真实任务仍必须先 `plan`，再使用同一个 token 执行。任务文件还要显式允许远端计算或写入。默认 profile `nics4304_tsmc28` 的反相器 transient 与共源 DC OP 均已有 OA/`si` 单点和有限搜索 live 证据，包括逐候选暂存、最佳参数提交、不可行/预算耗尽恢复与 checkpoint。调优默认在 run record 旁生成 `*.checkpoint.json`；Bridge 外部恢复后用 `--resume <checkpoint>` 续跑。恢复会重新核对 task、plan token、adapter 和当前 OA 参数，已完成 checkpoint 或不属于基线/已确认写入/待确认写入/声明候选的 OA 状态都会被拒绝。换 library、cell 模板、PDK、analysis 或服务器也必须重新验证，不能从既有 smoke 外推。

```powershell
.\.venv\Scripts\vda.exe run examples\tasks\inverter-tune-energy.bridge.json `
  --adapter bridge --execute --token <PLAN_TOKEN>

# Bridge 按自身流程恢复后，使用上次打印的 checkpoint：
.\.venv\Scripts\vda.exe run examples\tasks\inverter-tune-energy.bridge.json `
  --adapter bridge --execute --token <SAME_PLAN_TOKEN> `
  --resume artifacts\runs\...\run-....checkpoint.json
```

`simulation.run` 不写 OA：省略器件尺寸时直接采用目标 OA 回读值；如果任务显式给出尺寸，则必须与 OA 一致，否则停止，不会用请求值覆盖 schematic。`design.tune` 和 `design.close_loop` 为保证每个候选都来自真实 OA 状态，会在已授权写入的前提下逐点暂存参数并回读；无可行候选或可恢复中断时恢复搜索前参数。该暂存行为会明确出现在计划和 run record 中。

人工指定实例参数时使用 `instance_parameter_updates`，例如 `MN0.fingers="2"` 或 `RD0.r="22k"`。VDA 保留原始字符串，不猜单位、别名、枚举或布尔编码，也不因通用 reader 对空值/长值的摘要策略而提前拒绝 Bridge 可接受的请求；写后改用独立的目标 CDF 值相等检查。该路径目前属于 `parameters.apply`，可以单独使用，也可以与模板 semantic parameters 组合；请求标为 `user_input`，真实 OA 确认标为 `bridge_readback`，demo 结果仍只标为 `software_inference`。首次不一致时至多按声明顺序重放一次，计划会明确披露；仍不一致则失败。CDF 的 `editable`/`display` 元数据只作诊断，不能作为 allowlist，因为真实 smoke 已出现 `RD0.r` 报告不可编辑但能持久化的情况。CDF callback 引起的其他参数联动会保留在完整 Bridge 回读里，但只有任务明确请求且真实保持的字段会宣称确认。任意实例参数尚未自动进入搜索空间，这是下一步拟合能力而不是永久限制。

2026-07-20 的专用 `vda_param_surface_001` live smoke 已闭合 `MN0.fingers=2` 和 `RD0.r=22K` 的 callback、立即 OA 回读和独立再次回读。相同任务中的 `MN0.m=2` 被 PDK callback 恢复为 `1`，因此保留为失败边界；这说明“VDA 能尝试 Bridge 参数”不等于“每个 PDK CDF 字段都可物理持久化”。多字段写入不是 OA 事务，失败可能留下已保存的前缀字段。

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
