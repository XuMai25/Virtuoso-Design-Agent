# 2026-07-26 有源负载差分对与对称源退化组合 live Gate

## 结论

状态：**generic topology-delta composition, OA/si/Spectre multi-analysis migration, and exact inverse restoration live verified; design-quality closure pending**。

本轮在新 cellview `vb_pdk_smoke/vda_diffpair_active_deg_generic_001/schematic` 上真实完成：创建差分对、加入真实尾管、把电阻负载换成 PMOS 电流镜、用通用 topology-delta 增加两支对称源退化电阻、写入并回读 `RS0=RS1=500 ohm`、自动 `si` netlisting、DC/AC/CMRR/noise/transient/ICMR/PSRR，以及精确 inverse 和恢复态 DC。没有覆盖既有 cellview，没有修改 Bridge 或 Obsidian Vault。

这证明 VDA 能把一个小拓扑 delta 应用到既有 active-load schematic，并让原有多 analysis 流程迁移到组合拓扑；不证明这些 nominal 数值已经达到产品设计规格，也不把离散 ICMR 或临时 PSRR 门包装成完整闭环。

## 执行范围与环境

- PDK/profile：`nics4304_tsmc28`，TSMC N28/`tsmcN28`，nominal `top_tt`。
- Virtuoso：IC6.1.8；Spectre：21.1.0；Bridge：0.7.0。
- 远端执行主机实际为 `cad52`，通过 `nics4304-cad1` 进入 cad 环境。
- 远端 scratch 全部位于 `/data/xum/virtuoso_bridge_smoke`，没有写 `/home/xum`。
- `replace_existing=false`；目标 library/cell prefix 白名单分别为 `vb_pdk_smoke`/`vda_`。

初始三组 MOS 尺寸来自已有 active-load OA 的只读基线，而不是本轮另做搜索：

```text
Wn/L     = 1.215/0.03 um
Wp/L     = 1.080/0.03 um
Wtail/L  = 0.555/0.03 um
BIAS     = 0.32 V
VDD      = 0.90 V
VCM      = 0.55 V（nominal analyses）
RS0/RS1  = 500 ohm
```

## 通用 delta 与 OA 证据

创建、tail 和 current-mirror 三层分别完成 OA 写入和独立结构化回读。active-load 基线为五实例：

```text
MN0 (OUTP INP TAIL VSS)
MN1 (OUTN INN TAIL VSS)
MNTAIL (TAIL BIAS VSS VSS)
MP0 (OUTP OUTP VDD VDD)
MP1 (OUTN OUTP VDD VDD)
```

通用 forward 只执行六个预声明结构操作：新增 `NSP/NSN`，把 `MN0.S/MN1.S` 从 `TAIL` 重连到 `NSP/NSN`，再新增 `RS0(NSP,TAIL)` 与 `RS1(NSN,TAIL)`。参数值不进入结构指纹，随后由独立 `parameters.apply` 写入并双重回读。

```text
before topology SHA-256 = d3fe4b7328ea54dfbd331bfa71f467d64c18926c16739a89a419d33ccca31b93
after topology SHA-256  = 68c9d2e2625188fac32ae25636f3f3be062dfaa1065eee49e746b3a8bd6a559a
```

forward 写前、预期写后、实际写后 SHA 完全匹配；另一次独立 generic inspect 与 forward action 内部 after readback 的完整 topology JSON 相同。组合拓扑回读为 7 instances、10 nets，semantic 参数为：

```json
{
  "input_width_um": 1.215,
  "length_um": 0.03,
  "pmos_load_width_um": 1.08,
  "pmos_load_length_um": 0.03,
  "tail_width_um": 0.555,
  "tail_length_um": 0.03,
  "source_resistance_ohm": 500.0
}
```

## OA 到 si 的同源门

DC、AC/CMRR、noise、linearity、10 个 ICMR 点和 PSRR 均解析到同一个组合拓扑 netlist SHA：

```text
548124a95992a86cc5435b206988125df348daa2228d898a6861e47ac32e66ad
```

`si` 网表包含且只包含 `MN0/MN1/MNTAIL/MP0/MP1/RS0/RS1` 七个 DUT 实例。每次成功 analysis 都满足：

- OA semantic 参数与网表 W/L/RS `parameter_consistency=matched`；
- MN0/MN1、MP0/MP1、MNTAIL 与 VDD 的节点和电流 KCL `matched`；
- 两支源电阻从真实 `NSP/NSN/TAIL` 电压重算的电流一致性 `matched`；
- `analysis_complete=true`，原始 PSF/标量结果和输入文件均保留相对路径与 SHA-256；
- Spectre 运行受远端 timeout/kill-after guard 约束。

nominal DC 的关键值为：

| 指标 | 真实结果 |
|---|---:|
| 每支输入管电流 | `5.6919 uA` |
| 尾管电流 | `11.3838 uA` |
| VDD 电流 | `11.3839 uA` |
| DC 功耗 | `10.2456 uW` |
| 最小输入管饱和余量 | `0.2537 V` |
| 尾管饱和余量 | `0.2020 V` |
| 最小 PMOS 负载饱和余量 | `0.2126 V` |
| 输出共模 | `0.62038 V` |
| 输出失调绝对值 | `1.08e-5 mV` |

两支信号管、两支负载管和尾管均在当前规则下判为饱和区。

## 多 analysis 迁移结果

| analysis | 真实 Spectre 结果 | 边界 |
|---|---|---|
| 差模 AC | 低频增益 `3.5523 V/V` / `11.010 dB`；`-3 dB BW=2.0576 GHz`；`GBW=7.3093 GHz`；unity `6.5810 GHz` | 共模单独响应在 sweep 内没有首个 -3 dB 交点；不影响按差模/共模比值计算 CMRR |
| CMRR | 低频 `34.446 dB`；CMRR `-3 dB BW=2.1976 GHz` | sweep 高频端已跌到负值，不能把峰值当全频最差值 |
| noise | 输入参考积分噪声 `817.84 uV RMS`；输出积分噪声 `2.8872 mV RMS` | 仅当前声明的 nominal 频带和 ordinary noise |
| transient/THD | `50 mV_peak` 输入时增益压缩 `0.228 dB`、THD `1.4109%`、HD2/HD3 `-39.73/-40.38 dBc` | 当前幅度上限未包围 P1dB，因此 P1dB 保持 unresolved |
| ICMR | 声明的 `0.35–0.80 V` 十点全部分析完整并通过当前饱和/KCL/非负摆幅门；objective 在离散域选择 `0.55 V` | 只称 `best_in_declared_discrete_domain`；continuous/global optimum 均为 false；`0.80 V` 摆幅余量仅约 `0.03 V` |
| nominal PSRR | 低频 `PSRR+=11.064 dB`、`PSRR-=13.081 dB`；`1 kHz–1 MHz` 最差约 `11.064/13.081 dB` | 任务中的 `>=0 dB` 只是数据链 Gate；数值偏低，不是产品 PSRR closure |

ICMR 任务最初把已经由 OA 回读固定的 `source_resistance_ohm=500` 重复列进 fixed parameters，planner 因而正确把它判成潜在 OA 写候选并在 `allow_remote_write=false` 下拒绝。任务现只把 `common_mode_v` 放入候选空间；plan 明确显示 stage/finalize 均为 `read_only`，10 点共用同一 netlist SHA，最终记录“selected the best testbench condition without changing OA parameters”。新增 planner 回归测试防止重新引入隐式 OA 写入。

## transport/system events 与恢复

失败均保留为 `system_event`，没有转成电路不可行或空指标：

1. 首次 topology contract 误用 differential-pair semantic inspect 摘要，得到与 generic writer 不同的 before SHA。forward 在 writer 入口前因 CAS mismatch 拒绝，未写 OA。随后从 `existing_schematic` generic inspect 重新编译后通过。`vda topology-compile` 现在对 run record 强制要求 canonical `details.topology`，没有该字段时提示改用 `existing_schematic`，防止编译器和 writer 再使用不同结构摘要。
2. 两次 DC 尝试在下载生成的 `si.env` 前后遇到沙箱 DNS 无法解析 `nics4304-cad1`。独立 OA 回读一致，资源清单显示 `spectre=0/si=0`，再切换到获准的真实网络环境执行成功。
3. 首次 AC 在 Spectre 完成后下载 raw 目录时 SSH timeout；该 run 没有完整共模链，不能当 AC 结果。独立 OA 回读、远端 `spectre=0/si=0` 和保留的约 `79 kB` scratch 确认后，才安全重跑并完成双链。

没有在未知 OA 写入状态下盲目重放 payload，也没有把 return code、OA 对象存在或单条仿真完成当作设计闭合。

## 精确恢复与资源审计

inverse 写前要求 after SHA，写后要求原 before SHA：

```text
inverse input  = 68c9d2e2625188fac32ae25636f3f3be062dfaa1065eee49e746b3a8bd6a559a
inverse output = d3fe4b7328ea54dfbd331bfa71f467d64c18926c16739a89a419d33ccca31b93
```

实际 output SHA 与预期完全相同，另一次独立 generic inspect 与 forward 前 topology JSON 完全相同。最终 cell 保留为 5 instances、8 nets 的 PMOS 电流镜负载真实尾管差分对；本 Gate 添加的 `RS0/RS1/NSP/NSN` 已移除。恢复态自动 `si` netlist SHA 为 `5cb75da60efc95dc14b39a7e30b1b6df3bf436587c1b7083bea407daaae64f6b`，DC/KCL/工作区再次成功。

最终只读资源盘点：

```text
remote Spectre processes = 0
remote si processes      = 0
VDA-managed Maestro      = 0
local new python/scp/si/spectre processes = 0
pre-existing local SSH PIDs = 60988, 61436
```

本 Gate 的 19 个远端 scratch 目录约 `1,355,075 bytes`，本地 29 个 run/checkpoint/resource 文件约 `5,822,636 bytes`。它们仍作为证据保留，未执行删除；这属于可盘点的存储保留，不是 CPU/内存进程泄漏。长期删除应走已有 retention/cleanup 授权，不能在验证记录仍引用这些路径时静默清除。

## 证据来源

- `bridge_readback`：OA create/transform/inspect、CDF 参数回读、拓扑和资源进程清单。
- `eda_result`：`si` netlist、Spectre DC/AC/noise/transient/PSRR 原始结果及文件哈希。
- `software_inference`：拓扑 SHA、inverse 证明、KCL 容差判定、带宽/GBW/CMRR/THD/积分噪声/ICMR 选择与约束聚合。
- `system_event`：指纹来源不一致、DNS/SCP/SSH transport 失败和安全门拒绝。
- `user_input`：目标 cellview、允许的 OA 写入/远端计算、尺寸、RS、偏置、sweep 与临时约束。

## 未闭合边界与下一道 Gate

- P1dB 尚未被声明幅度 sweep 包围；需要扩展幅度范围并加输出摆幅/饱和护栏。
- PSRR 只有约 `11–13 dB`；本轮没有把临时数据链门包装成质量规格。
- 尚未做 mismatch/Monte Carlo、slew/settling、输出驱动、连续 ICMR 边界或 ADE 手工 setup 交接。
- PVT 按产品策略保持可选，本轮没有运行；切换 PDK、corner、温度或 VDD 不能复用 nominal 证据。
- 通用 topology-delta 的真实 allowlist 仍只覆盖已验证的 instance/net/reconnect 子集；master/CDF 替换、pin 几何、wire/shape snapshot、并发 editor 和 post-save 自动回滚仍是独立 Gate。

本轮把“active-load 原理图上增加对称源退化并无缝复用多 analysis”从本地模拟升级为 live 能力。下一步最有价值的工作不是继续为这一固定数值做盲调，而是扩展 topology-delta 的真实结构能力与事务恢复，同时让 Agent 能基于明确规格选择拓扑/参数，再由同一 OA→si→Spectre 证据链复核。

## 本地回归

```text
634/634 pytest passed
177/177 example task plans passed
compileall: passed
catalog: passed
git diff --check: passed
```

live run records 位于：

```text
artifacts/runs/differential-pair-active-degenerated-gate9-live/
```
