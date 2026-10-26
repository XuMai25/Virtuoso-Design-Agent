# 2026-07-20 共源功耗、线性度与 noise 只读真实验证

状态：**same-source linearity/noise execution verified at one design point; quality-driven closure and corner pending**。

本轮在 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` 上执行两次只读远端 smoke。目标 OA cellview 已是源极退化共源级；任务没有写 OA、没有覆盖 cellview，只允许远端计算并把审计产物写入 `/data/xum/virtuoso_bridge_smoke/`。Bridge checkout 保持干净的 `codex/vda-transport-recovery@e74379a`，本轮没有修改第三方库。

## 授权与计划

- target：`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`
- `allow_remote_compute=true`
- `allow_remote_write=false`
- `replace_existing=false`
- linearity token：`0a03cc79cfdcf6a9`
- noise token：`808d58cbbea54ec5`

两个成功 run 都只有 `bridge.probe`、`schematic.inspect.before` 和 `simulation.candidate.1`，没有 parameter stage/apply、schematic transform 或其他 OA 写 action。两次独立 OA 回读均为：

```text
topology = source_degenerated_common_source
W = 1.0 µm
L = 0.03 µm
RD = 20 kΩ
RS = 1 kΩ
```

两次 `si` 结构网表的 SHA-256 均为 `2c529799349ac466606367015a038e22c4ab7048b8b21e89092146515de7b232`，解析后的 W/L/RD/RS 与 OA 回读完全一致。这提供了跨 analysis 的同一目标、同一网表证据，而不是两份手写 DUT deck。

## 线性度 live smoke

成功记录：

```text
artifacts/runs/common-source-linearity-verify/live-retry2-20260720.json
```

保留的远端审计产物：

```text
netlist: /data/xum/virtuoso_bridge_smoke/vda_common-source-linearity-verify_398e80119003/netlist
wrapper: /data/xum/virtuoso_bridge_smoke/vda_common-source-linearity-verify_398e80119003/input_from_oa.scs
wrapper sha256: acffa42b3bb03af1bb1bc5e6a44a2947037063d29f57206e60a832fbd7953807
```

根 DC 文件被独立选取并哈希：

```text
dcOp.dc sha256: 70cdf2304f3ced11c375426d5f49263a4f524b20df7d37b656383e6bc81935ad
dcOpInfo.info sha256: 9bcf00150b4b1d5ae67bc104dd58bed3bdd4ae775342b5e7f77fe2d2241fa155
```

DC operating point 通过饱和、RD/RS/VDD 三组电流一致性检查：

```text
Id = 24.8196 µA
VGS = 0.325181 V
VDS = 0.378802 V
VDSAT = 0.096121 V
saturation margin = 0.282681 V
DC supply power = 22.3370 µW
MN0/RD mismatch = 0.002666%
MN0/RS mismatch = 0.002723%
MN0/VDD-source mismatch = 0.002666%
```

100 MHz nested transient sweep 的每个声明幅度都得到 1026 个测量窗样本，实际输入 fundamental 与声明值在浮点精度内相同：

| Vin peak | Gain V/V | THD | HD2 dBc | HD3 dBc | Vout p-p | VDD power |
|---:|---:|---:|---:|---:|---:|---:|
| 5 mV | 3.6993 | 0.0446% | -67.19 | -81.04 | 0.03699 V | 22.3377 µW |
| 20 mV | 3.6844 | 0.2148% | -55.94 | -56.85 | 0.14715 V | 22.3426 µW |
| 50 mV | 3.5975 | 0.9901% | -56.20 | -40.23 | 0.35617 V | 22.3546 µW |
| 100 mV | 3.2105 | 6.3950% | -30.30 | -25.17 | 0.60669 V | 22.0536 µW |
| 150 mV | 2.7011 | 13.1613% | -22.95 | -19.20 | 0.73255 V | 21.2760 µW |

首点作为 small-signal reference，1 dB 压缩由 50 mV 与 100 mV 两点真实包围并插值：

```text
small-signal gain = 3.69931 V/V = 11.3624 dB
input P1dB = 88.3179 mV peak
output at P1dB = 0.288064 V peak
gain compression at 150 mV = 2.73160 dB
```

这些是该固定 bias、负载、频率、幅度网格和 nominal model section 下的 `eda_result`，不是对放大器普遍线性度的外推。

## 真实目录形状暴露的两个问题

第一次记录 `live-20260720.json` 在 DC 一致性门失败：根 `dcOp.dc` 的节点 VDS 为 `0.378802207894 V`，目录级合并数据中的 MN0 VDS 为 `0.379024248172 V`。检查发现 Bridge 通用目录 parser 会递归合并 sweep 子目录的同名 `dcOpInfo`；节点与器件标量因此可能来自不同 analysis point。

修复没有放宽电压容差，也没有修改 Bridge。VDA worker 改为从 Bridge 已下载结果中显式选取相对深度最小的根 `dcOp.dc`/`dcOpInfo.info`，分别解析并保存 SHA-256；嵌套 sweep 文件不能再覆盖 DC gate。新增回归用根文件和故意冲突的 sweep 文件验证选择语义。

第二次记录 `live-retry1-20260720.json` 通过 DC gate 后，在完整相干 measurement window 检查失败。Spectre strobed transient 没有保证返回声明 stop 的闭区间端点。指标层仍要求完整 4+8 周期；wrapper 仅把 stop 延长一个 strobe interval，确保 12 周期边界有实际样本，不截短或放宽傅里叶窗口。第三次执行成功。

## noise live smoke

成功记录：

```text
artifacts/runs/common-source-noise-verify/live-retry1-20260720.json
```

远端网表：

```text
/data/xum/virtuoso_bridge_smoke/vda_common-source-noise-verify_73463ab1e6a9/netlist
```

普通 noise PSF 由 Bridge 下载并用 Bridge 自身单文件 parser 解析。VDA 随后把同一 PSFASCII 上传到保留 scratch：

```text
path: /data/xum/virtuoso_bridge_smoke/vda_common-source-noise-verify_73463ab1e6a9/noise.noise.psfascii
sha256: 0e1bda41ab32600e01b04c6fa3c0baa360265cb319a86f3ac82ccc56197fa52b
signals: freq, out, in
samples: 211
band: 1 kHz to 10 GHz
```

频率端点与任务声明一致，密度平方用频率梯形积分：

```text
integrated output noise = 3.304339 mV RMS
integrated input-referred noise = 0.982842 mV RMS
output density at 1 kHz / 10 GHz = 8608.58 / 17.6766 nV/sqrt(Hz)
input density at 1 kHz / 10 GHz = 2326.27 / 7.53468 nV/sqrt(Hz)
```

首次 `live-20260720.json` 在远端仿真后下载 raw 结果时发生 SSH timeout。没有 PSF 就没有产生噪声候选证据。Bridge 原生 `restart` 重建 tunnel，VDA doctor 以 `bridge_readback` 确认 Bridge 0.7.0、SKILL probe 和 profile 后，重新运行得到上述成功记录。该事件继续证明运行中传输仍可能失败；本轮只证明显式保留失败并恢复后重跑，不证明 transport 已根治。

## 证据分类

- OA 结构与参数：`bridge_readback`
- `si` 网表、DC OP、transient/noise trace 和积分后的连续指标：`eda_result`
- 傅里叶、THD、P1dB、噪声积分、饱和区和完整性规则：`software_inference`
- 频率、幅度、周期、负载、bias 与 analysis：显式字段为 `user_input`，默认字段为 `software_inference`
- SSH timeout：`system_event`

成功不是由 return code 单独判定：两个 run 均要求 OA/网表参数一致、根 DC PSF 非空、饱和/KCL 通过、analysis trace 形状和声明端点一致、指标提取完整且约束通过。

## 最终本地回归

```text
133 passed in 0.53s
39/39 example task plans passed
vda catalog passed
git diff --check passed
```

## 未验证边界与下一道 Gate

- 只验证了一个 source-degenerated cell、一个 bias/load、100 MHz 线性度和一个 nominal model section；没有 PVT/corner、Monte Carlo 或多频点线性度。
- 本轮 `simulation.run` 不写 OA。尚未让 THD/P1dB/noise/power 共同驱动 W/L/RD/RS/bias/load/VDD 的受预算优化，也没有验证质量规格不可行和预算耗尽路径。
- transient sweep 的逐点连续数据被当场解析并写入 run evidence，但不像 noise PSF 那样另行保留完整 raw 文件；若需要波形级复算审计，应增加受控 raw retention/hash，而不是把当前标量当成完整波形归档。
- `L`、`VDD`、输入电容/面积代理、有限 corner 和跨 analysis 综合目标仍未闭合。
- Bridge 运行中 download timeout 仍是底层可靠性债务。
- Bridge `SimulationResult.tool_version` 在这两份 run record 中为空；Spectre 21.1.0 是既有环境探测，不是本轮每个 run 自带的版本证据。后续应在不修改第三方传输边界的前提下补 run-level tool/version 记录。

下一道 Gate 是把已经 live 验证的 DC+AC+linearity+noise 指标组合为一个受预算的只读多 analysis 评估，再选择少量 W/L/RD/RS/bias/load/VDD 候选做质量驱动调优，并覆盖可行、不可行、预算耗尽和 transport 恢复；随后才进入有限 corner。当前不能称为完整 L5B 单模块规格闭环。
