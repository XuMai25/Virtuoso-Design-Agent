# 2026-07-31 PMOS 有源负载共源级自动接入与联合微调真实 Gate

状态：**new flat topology onboarding, OA-CDF plus typed testbench candidate tuning,
same-source DC/AC selection, checkpoint recovery and winner writeback live verified at
nominal TSMC N28; L5B design-quality closure pending**。

本 Gate 面向“用户尚无现成原理图”的情形，但没有新增电路专用 executor。VDA 在新且不覆盖的
`vb_pdk_smoke/vda_pmos_loaded_cs_onboarding_001/schematic` 上建立最小共源基线，再用通用
topology-delta 把电阻负载替换成 PMOS 电流源负载：

```text
MN0: nch_lvt_mac  D=OUT G=IN  S=VSS B=VSS
MP0: pch_lvt_mac  D=OUT G=VBP S=VDD B=VDD
pins: IN OUT VBP VDD VSS
```

`replace_existing=false`；preflight 先确认目标不存在，随后只新建该目标一次，没有覆盖任何已有
cellview。没有修改 Bridge，也没有访问或写入 `/home/xum`。所有远端 scratch 位于
`/data/xum/virtuoso_bridge_smoke/`。

## 新增的可复用能力

通用 `AtomicCandidate` 现在可携带 typed `testbench_overrides`：

- source 只允许覆盖已有 source 的 `dc_value/ac_magnitude/ac_phase_deg`；
- load 只允许覆盖已有 R/C 的正有限 value；
- source/load 名必须存在于已确认的 `generic_simulation`；
- source 类型、连接、transfer、metric 和 OA topology 均不可由候选改变；
- 同一 candidate set 中每个 tuple 必须声明完全相同的 OA/testbench 字段；
- checkpoint/resume 校验完整 tuple，而不是只比较 OA 参数；
- 每个 candidate 和最终 selection 分别记录 testbench override 及其证据来源。

因此本次三个候选可以原子地同时改变 `MP0.Wfg` 与 `VBP_SRC.dc_value`，并显式记录固定的
`VIN_SRC.dc_value`、`CL0.value`。最终 OA 只提交 `MN0/MP0.Wfg/l`；`VIN/VBP/CL` 始终是
testbench 条件，不会伪装成已持久化 ADE 或 schematic 参数。该能力适用于任何通过
`generic_simulation` 接入的 flat existing schematic，并不依赖本次两个晶体管的实例名。

## 理论种子与 standalone preview

种子不是任意列点。VDA 从保留的 TSMC N28 NMOS/PMOS real-PDK characterization 点出发，用
电流匹配和 `gm/(gdsn+gdsp)` 一阶模型得到：

```text
MN0 W/L = 0.5 um / 0.03 um
MP0 continuous W = 1.90198 um -> declared grid 1.9 um
VIN = 0.35 V, VBP = 0.57 V, VDD = 0.9 V, CL = 2 fF
estimated gain = 3.315 V/V = 10.4099 dB
```

理论 artifact SHA-256 为
`6f79b1afaa621fa00f27912681ef53dda8a2c4e802b0ccdfc57ccfd6b662478b`。随后一次 standalone
Spectre preview 批量运行中心点、`Wp` 两侧、`VBP` 两侧和长沟道证伪点共六个物理候选；运行
record SHA-256 为
`6f547ddeb0dc50db4def32a22b5135e78af05754a3aec94f615922871ec534ca`。probe 加六点仿真墙钟
为 `59.571 s`，其中仿真 action 为 `58.537 s`。在读取本 topology 的 OA 仿真真值前冻结的
top-3 顺序为：

1. `preview-vbp-strong`：`Wp=1.9 um, VBP=0.56 V`；
2. `preview-wp-low`：`Wp=1.7 um, VBP=0.57 V`；
3. `preview-center`：`Wp=1.9 um, VBP=0.57 V`。

preview 只负责缩小候选和明显工作区证伪；它没有授权 OA 写入，也没有被当成最终规格真值。

## 非覆盖 OA、草稿与 resolution

执行顺序为：不存在性 preflight → 新建电阻负载共源基线 → 通用 delta 删除 `RD0`、添加
`MP0/VBP/VBP pin` → independent inspect → 理论尺寸写入与回读 → `onboarding-draft` →
`onboarding-resolve`。

最终 topology SHA-256 为
`8c0e4962df6825f0b6e82a537f2b361fcbcbfaef1c0417408404fc8cc33b5686`，placement SHA-256 为
`2a3b306fa8a397518951014bbbc4442a864b5455fa5035c1358f1758c9661af2`。draft SHA-256 为
`be420f1ad66d961e25281702f2acf433c7f8e2fc859a9e74f7da129226c1c7e9`；完整 CDF inventory
默认仍为 `not_authorized`。resolution 只开放两管的 `Wfg/l`，并要求四项 OA→`si`
binding 一一对应。角色与仿真意图来自本次用户批准，候选来源是 hash-bound
`software_inference`。

resolution 编译器仍固定输出 `allow_remote_compute=false/allow_remote_write=false`。获得本次
明确授权后，DC 任务只打开 compute；三点调优任务才同时打开 compute/write 并重新生成 plan
token。最终调优 token 为 `765ffdc5ac4ffe87`。

## 首个同源 DC Gate

中心点的只读 OA→`si`→Spectre DC 完整通过：

| metric | real OA value |
|---|---:|
| output DC | `0.596100 V` |
| supply current | `-26.5415 uA` |
| MN0 `VDS/VDSAT` | `0.596100 / 0.107969 V` |
| MP0 `VDS/VDSAT` | `-0.303900 / -0.076382 V` |
| MN0 `gm/gds` | `364.197 / 30.494 uS` |
| MP0 `gm/gds` | `446.085 / 78.721 uS` |

raw `si` netlist 同时证明 `MN0.Wfg/l=500n/30n`、`MP0.Wfg/l=1.9u/30n` 与 OA 回读一致，
实例 model/node 集合匹配，DC/OP PSF 非空且 artifact manifest 完整。该结果与 preview 中心点
`Vout=0.514242 V` 有明显差异，因此没有把共享 W/L 的 standalone deck 包装成 schematic 真值。

## 三点真实 DC/AC 结果

每个 candidate 只回读一次 OA、生成一次 Cadence `si` 网表，并在同一 netlist 上按顺序运行
DC 和 AC。DC 先检查 `0.3 <= Vout <= 0.65 V`、NMOS/PMOS 粗粒度工作区护栏；AC 再检查
gain `>=3 V/V`、bandwidth `>=3 GHz`。三个点均完整且可行，最后在可行集合中最大化 GBW：

| candidate | `Wp` | `VBP` | output DC | gain | bandwidth | GBW | result |
|---|---:|---:|---:|---:|---:|---:|---|
| `preview-vbp-strong` | `1.9 um` | `0.56 V` | `0.636520 V` | `3.23223 V/V` | `5.72128 GHz` | `18.4925 GHz` | winner |
| `preview-wp-low` | `1.7 um` | `0.57 V` | `0.570478 V` | `3.41181 V/V` | `5.35374 GHz` | `18.2660 GHz` | feasible |
| `preview-center` | `1.9 um` | `0.57 V` | `0.596100 V` | `3.32453 V/V` | `5.42182 GHz` | `18.0250 GHz` | feasible |

search audit 为 `declared=attempted=completed=3`、`domain_exhausted=true`，选择范围是
`best_in_declared_discrete_domain`；连续和全局最优声明均为 false。final run record SHA-256 为
`5d838f937a0e19ea750e9dd5f68832121fac7ed7c2fd58139dc217f2be9fcd8a`，checkpoint SHA-256 为
`79c133b0bf37751381d3596a00eceea83ff34d812898fc6546e0609adb7b3648`。

三点 `si` raw netlist 及 SHA-256：

| candidate | remote path | raw SHA-256 |
|---|---|---|
| strong | `/data/xum/virtuoso_bridge_smoke/vda_pmos-loaded-common-source-onboarding-tune-live_ca18420c9920/netlist` | `ecb7306112e50eee15ac9c9af0e96a7e3ba6794aaa12e91b09c27023edaf4fdd` |
| Wp-low | `/data/xum/virtuoso_bridge_smoke/vda_pmos-loaded-common-source-onboarding-tune-live_a35e0673bead/netlist` | `37cf8e12f4f4e9864b8ff5676937eb09db5a1e23404c5fa2b5e74da78d4a7f04` |
| center | `/data/xum/virtuoso_bridge_smoke/vda_pmos-loaded-common-source-onboarding-tune-live_b4dbb1018a41/netlist` | `ecb7306112e50eee15ac9c9af0e96a7e3ba6794aaa12e91b09c27023edaf4fdd` |

相同 OA 几何的 strong/center raw netlist hash 相同；两者性能差异来自明确记录的 VBP testbench
override。Wp-low 的 OA/netlist 宽度不同，因此 hash 不同。

## Preview 误差与时间边界

在冻结 top-3 内，preview 与 OA 真值的 GBW 排序相同，并保留了三点中的真实 winner；但只跑了
shortlist 的 OA 真值，不能据此声称被淘汰的另外三点绝不可能胜出。按真实 OA 值归一化，top-3
最大绝对误差为：

| metric | max absolute relative error |
|---|---:|
| output DC | `14.562%` |
| low-frequency gain | `7.452%` |
| bandwidth | `5.633%` |
| GBW | `12.958%` |

三次真实 shared-netlist candidate action 共 `394.205 s`，平均 `131.402 s/point`；包含一次已恢复的
连接等待，不能当成纯 Spectre compute benchmark。即使按这次实际值，`59.571 s <
(6-3)*131.402 s`，preview-first 满足预先定义的节省条件。完整 run 从首次调用到两次恢复完成为
`656.350 s`。Agent 规划、实现和人工审批时间没有可靠的统一计时源，本记录不把开发过程墙钟包装成
生产执行延迟；可复用路径已经收敛为确定性的 compile/plan/run/checkpoint 命令。

## 中断、恢复与资源

第一次外层调用失去前台控制时，磁盘 checkpoint 只确认 candidate 1 的 DC stage，而独立 OA 回读
已见声明候选 `MP0.Wfg=1.7u`。VDA 没有信任未落盘的内存进度，恢复时从最后可信 stage 重跑未确认
部分。之后 `parameters.stage.3` 又真实遇到 `WinError 10054`，最终 run 以
`system_event` 保存该失败，并在独立回读后从 index 3 继续；candidate 1/2 没有重跑。

最终任务外 inspect 确认：

```text
MN0.Wfg/l = 500n / 30n
MP0.Wfg/l = 1.9u / 30n
topology SHA-256  = 8c0e4962...b5686
placement SHA-256 = 2a3b306f...61af2
```

inspect record SHA-256 为
`f13c71a729449f47b3ec6c2e5ec2b9aa7e7d355b1ba25f4b5d766d45d8cb2748`。最终只读资源 inventory
为远端 `spectre=0`、`si=0`、VDA-managed Maestro session=`0`；两个 Virtuoso 进程为共享服务。
本地 transient temp=`0`、cancel marker=`0`。Bridge 随后显式停止，额外进程检查得到相关
Python/SSH/PowerShell=`0`、`vda_*` temp=`0`。资源 record SHA-256 为
`34fd676003f1e0a38ed947a58da840243736db80aaefc3b1f892f4bdaf91a049`；远端证据目录有意保留，
`deletion_performed=false`。

## 证据来源

- OA graph、CDF 写前/写后/任务外回读：`bridge_readback`；
- Cadence `si` raw netlist、Spectre DC/AC PSF、标量和 manifest：`eda_result`；
- 理论尺寸、preview 候选、hash/一致性、约束与 objective 判定：`software_inference`；
- topology 意图、角色、约束、objective、预算与远端授权：`user_input`；
- transport reset、外层调用丢失与恢复边界：`system_event`。

return code、cellview 存在和 OA 写入成功均没有被单独当作设计完成证据。

## 本地回归与剩余边界

新增测试覆盖 source/load override schema、未知对象拒绝、跨候选字段完整性、AC 零激励拒绝、
非 generic circuit 拒绝、OA+testbench 原子执行、selected override 记录，以及 checkpoint 对完整 tuple
的精确恢复。最终验证：

```text
853 passed
230/230 example tasks planned
python -m compileall -q src tests
git diff --check
```

本 Gate 尚未证明：

- omitted preview 候选的完整 OA reference 域或连续/全局最优；
- transient linearity、noise、slew/settling、PVT、mismatch/Monte Carlo；
- `VBP/CL` 已持久化到 ADE/Maestro setup；当前只存在于可审计 VDA testbench；
- onboarding resolution 对 topology-edit envelope、`design.close_loop` 或 winner-only quality Gate 的自动编译；
- 深层 hierarchy、共享 child per-instance override、并发人工 editor；
- 用户真实单模块的应用规格与完整质量闭环。

因此下一道产品 Gate 不应继续随机扩充本三点域。更有价值的是把用户确认的局部 topology-delta
权限和 winner-only quality intent 纳入 onboarding resolution：先对既有用户拓扑做最小可逆结构微调，
再只对真实 winner 运行任务所需的 transient/noise 与可选 PVT，并保留 ADE 人工交接。这样推进的是
跨电路 L5B 工作流，而不是继续为本次共源级写专用逻辑。

## 后续状态

同日已完成上述 resolution 编译纵切，见
[`2026-07-31-onboarding-refinement-resolution-local.md`](2026-07-31-onboarding-refinement-resolution-local.md)。
它没有重新运行本页遗漏候选，也没有新增测试电路或远端 EDA；当前结论是本地契约已经能把用户确认的
可逆 topology alternative 与 winner-only quality/PVT 编译到既有 `design.close_loop`，首次真实集成仍
留给下一次用户模块。
