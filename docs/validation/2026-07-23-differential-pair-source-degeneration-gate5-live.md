# 2026-07-23 差分对对称源极退化 Gate 5 live 验证

## 结论

状态：**reversible symmetric source-degeneration, same-source full-analysis migration, and bounded RS writeback verified**。

本 Gate 在全新 `vb_pdk_smoke/vda_diffpair_deg_gate5_001/schematic` 上，从 nominal 差分对 core 依次加入真实 OA 尾管和对称源极退化；随后完成 OA/`si` 同源 DC、差模/共模 AC、CMRR、ICMR、相干 transient 线性度、ordinary differential noise，以及 `source_resistance_ohm=[250,500]` 的有限搜索与最佳 OA 写回。最后执行 remove，并把完整 add/remove/restore 序列再重复一次。

这证明“给定明确对象和值，局部调整两支对称源电阻并进入后续全流程”已经是 VDA 的正式能力，不是本次手工脚本。它没有修改或复制 `virtuoso-bridge-lite`，也不代表 VDA 已能任意综合拓扑或达到完整 L5B。

## 范围与安全边界

- PDK/profile：`nics4304_tsmc28`，TSMC N28/`tsmcN28`，nominal `top_tt`。
- 新目标：`vb_pdk_smoke/vda_diffpair_deg_gate5_001/schematic`。
- `replace_existing=false`；没有覆盖 Gate 3/4 cell。
- 远端产物位于 `/data/xum/virtuoso_bridge_smoke/`；没有写 `/home/xum`。
- create、tail transform、source-degeneration add/remove 和最佳 RS 写回均在用户明确授权的新 cell 范围内。
- PVT 为可选项，本 Gate 未运行。
- Bridge doctor：connected，Bridge `0.7.0`，SKILL probe `3`，profile `nics4304_tsmc28`。
- 本 Gate 只调用 Bridge 公开能力；第三方 Bridge 仓库没有任何修改。

## 新增能力契约

1. 差分对 `schematic.transform/add_source_degeneration` 只接受精确的 Gate 4 真实尾管拓扑，执行固定对称 delta：
   - `MN0.S: TAIL -> NSP`；
   - `MN1.S: TAIL -> NSN`；
   - 新增 `RS0(NSP,TAIL)` 与 `RS1(NSN,TAIL)`。
2. `source_resistance_ohm` 是 OA semantic 参数。add、`parameters.apply` 或 `design.tune` 必须同时写两只电阻；独立回读要求 `RS0.r == RS1.r == requested`。
3. OA inspect 与 `si` parser 都拒绝单边缺失、非对称电阻值、错误节点或额外拓扑。顶层 pins 仍为 `BIAS/INN/INP/OUTN/OUTP/TAIL/VDD/VSS`；`NSP/NSN` 只允许是内部网。
4. DC wrapper 保存 NSP、NSN 和 TAIL；VGS/VDS/饱和余量使用各管真实源节点。两只电阻电流由 `(NS?-TAIL)/R` 独立重算，并分别与对应 MOS 支路核对；误差超过 1% 是证据失败，不是普通规格不可行。
5. `remove_source_degeneration` 不接受参数，只删除 VDA 创建的 `RS0/RS1` 及其四条端子 wire/label stub，并恢复两管源极到 TAIL。可绑定 add 前 placement SHA；保存后必须精确恢复。
6. Gate 4 的 DC/AC/CMRR/ICMR/transient/noise、有限搜索、checkpoint、最佳写回与恢复状态机全部复用，没有为退化拓扑复制 adapter 或 Spectre deck。

## OA exact-delta 与同源网表

add 前真实尾管 topology：

```text
MN0/MN1/MNTAIL/RD0/RD1
nets = BIAS, INN, INP, OUTN, OUTP, TAIL, VDD, VSS
placement SHA-256 = d18e638165fcb2fa5fce24b7499ba982d0c145afad2198ef887d4e85d58d1f07
```

add 后只增加 `RS0/RS1` 与 `NSP/NSN`；pins 不变：

```text
MN0 OUTP INP NSP VSS
MN1 OUTN INN NSN VSS
RS0 NSP TAIL
RS1 NSN TAIL
MNTAIL TAIL BIAS VSS VSS
placement SHA-256 = 440bbe8492e841cb822b753211ea624692d53461e6e43d9eaaeff7619eb4fa71
```

500 Ω DC 的 `si` 网表：

- 远端路径：`/data/xum/virtuoso_bridge_smoke/vda_differential-pair-degenerated-dc-bridge_50a97c6a9ef1/netlist`。
- SHA-256：`84bfaccc74a2fe7411364ff1104f4d44ea2b78f783a2d12181f358d0eee39d20`。
- topology：`resistive_load_nmos_differential_pair_with_tail_device_and_source_degeneration`。
- OA 与网表 semantic 参数一致：输入管 `W=2 µm/L=0.03 µm`、`RD0=RD1=8 kΩ`、尾管 `W=0.8 µm/L=0.03 µm`、`RS0=RS1=500 Ω`。

return code、OA 对象存在或网表文件存在均未被单独当作通过依据。

## 500 Ω DC 与电阻 KCL

固定 `BIAS=0.40 V`、`VCM=0.55 V`、`VDD=0.9 V`：

| 指标 | 结果 |
| --- | ---: |
| P/N 支路电流 | `24.240235 / 24.240235 µA` |
| 实际尾管电流 | `48.479899 µA` |
| NSP / NSN | `0.259458 / 0.259458 V` |
| TAIL | `0.247338 V` |
| 每支源电阻压降 | `12.11998 mV` |
| 电阻重算电流 | `24.239952 / 24.239952 µA` |
| 最大源电阻电流误差 | `0.001165%` |
| 输入管最小饱和余量 | `0.356291 V` |
| 尾管饱和余量 | `0.120873 V` |
| 最小输出摆幅余量 | `0.193921 V` |
| DC VDD 功耗 | `43.632266 µW` |

两输入管、MNTAIL、两只负载与两只源电阻的 KCL/工作区/摆幅/功耗约束均通过；两支完全对称不是仅从 OA 结构推断，而是由 Spectre OP 和电阻压降分别复核。

## AC、带宽、GBW 与 CMRR

固定每端 `CL=1 fF`，1 kHz–1 THz；差模与共模共享同一 OA/`si` 网表和一致 DC OP：

- 差模低频增益：`2.403897 V/V`（`7.61832 dB`）。
- 差模 −3 dB 带宽：`13.076917 GHz`。
- GBW：`31.435557 GHz`。
- unity-gain frequency：`28.970236 GHz`。
- 共模低频增益：`0.255636 V/V`。
- 低频 CMRR：`19.465878 dB`。
- CMRR 首次下降 3 dB 的带宽：`17.955093 GHz`。

相对 Gate 4 同 W/L/RD/tail/bias/load 的无退化点，低频增益降低 `17.89%`、带宽降低 `5.28%`、GBW 降低 `22.22%`、unity 降低 `24.66%`、低频 CMRR 降低 `7.90%`。这些是本次具体 500 Ω 点的真实权衡，不外推为所有源极退化设计的普遍比例。

## ICMR 十点扫描

`VCM=0.35–0.80 V`、步进 0.05 V；10/10 候选都 `analysis_complete=true`，checkpoint 完整：

| VCM (V) | 最小摆幅余量 (mV) | 尾管饱和余量 (mV) | 可行 |
| ---: | ---: | ---: | :---: |
| 0.35 | 129.04 | −25.65 | 否，尾管非饱和 |
| 0.40 | 150.07 | 8.46 | 是 |
| 0.45 | 166.51 | 45.01 | 是 |
| 0.50 | 180.76 | 82.67 | 是 |
| 0.55 | 193.92 | 120.87 | 是 |
| 0.60 | 206.41 | 159.36 | 是 |
| 0.65 | 218.44 | 197.98 | 是，网格内 objective 最佳 |
| 0.70 | 193.98 | 236.57 | 是 |
| 0.75 | 140.43 | 274.98 | 是 |
| 0.80 | 87.33 | 312.96 | 是 |

因此只报告当前离散采样通过范围 `0.40–0.80 V`；0.80 V 仍通过，不能声称已经找到连续上边界。

## transient 线性度与 RS 有限调优

固定 100 MHz、每端 `1 fF`，差分输入 peak 为 `[5,20,50,100,150,200] mV`：

- 小信号增益：`2.403515 V/V`。
- 输入 P1dB：`150.299 mV peak`；输出 P1dB：`321.914 mV peak`。
- 200 mV 点：增益 `1.961574 V/V`、压缩 `1.76484 dB`、THD `7.10761%`、HD3 `−22.9727 dBc`。
- 小信号/最大平均 VDD 功耗：`43.6328 / 44.5334 µW`。

相对同参数无退化点，输入 P1dB 提升 `29.19%`，200 mV THD 降低 `30.11%`，最大功耗降低 `2.41%`。该改善伴随前述增益、GBW 和噪声代价。

随后用正式 `design.tune` 搜索 `source_resistance_ohm=[250,500]`；两点都执行 OA 暂存、双电阻回读、自动 netlist 和完整 transient：

| RS (Ω，每支) | 小信号增益 (V/V) | 输入 P1dB (mV peak) | 200 mV THD (%) | 最大功耗 (µW) | 可行 |
| ---: | ---: | ---: | ---: | ---: | :---: |
| 250 | 2.64 | 132.26 | 8.51 | 45.07 | 是 |
| 500 | 2.40 | 150.30 | 7.11 | 44.53 | 是，按 P1dB 选中 |

最终独立 inspect 回读 `RS0.r=RS1.r=500`。这验证了“人工明确告诉 VDA 修改哪一参数”与“让 VDA 在显式有限范围内搜索”共用同一个受控能力面。

## differential noise

1 kHz–10 GHz ordinary noise：

- 输入参考积分噪声：`1086.046 µV RMS`。
- 差分输出积分噪声：`2468.530 µV RMS`。
- 输入噪声密度：1 kHz `2214.651 nV/√Hz`，10 GHz `9.03671 nV/√Hz`。
- 输出噪声密度：1 kHz `5323.792 nV/√Hz`，10 GHz `17.25678 nV/√Hz`。

相对同参数无退化点，输入参考积分噪声增加 `17.82%`，输出积分噪声降低 `4.49%`。这些噪声约束用于证明数据链与权衡记录完整，不代表噪声已经优化。

## remove 与两次恢复证明

第一次完整全分析与 RS 写回后执行 remove；恢复 DC 后再执行第二次 add→remove→恢复 DC：

- 两次 remove 的 add 后 placement SHA 都为 `440bbe...fa71`，remove 后都精确等于 add 前 `d18e6381...1f07`，`restored_placement_match=true`。
- 最终 instances 为 `MN0/MN1/MNTAIL/RD0/RD1`；nets 为 `BIAS/INN/INP/OUTN/OUTP/TAIL/VDD/VSS`；不存在 `RS0/RS1/NSP/NSN` 或 `source_resistance_ohm`。
- 两次恢复 DC 的网表 SHA 都为 `70e0c93465f979cc1e67f38d85e09d136e4c509db24c48f78d3d5e941403b265`，解析参数完全相同。
- 两份恢复 run 的全部 selected metrics JSON 完全相同：尾电流 `49.368024 µA`、两支路各 `24.684021 µA`、DC 功耗 `44.431570 µW`、摆幅余量 `0.197474 V`、尾管饱和余量 `0.131623 V`。
- 这些恢复数值也与 Gate 4 同参数 nominal 点一致；由于 cell/subckt 名不同，不用跨 cell 的文件哈希冒充同一文件。

最终远端 cell 保持 Gate 4 真实尾管拓扑，未留下源极退化网络。

## 运行记录

- create/tail/add：`differential-pair-degenerated-gate5-create.json`、`...-tail.json`、`...-add.json`。
- 单点分析：`...-dc.json`、`...-ac-cmrr.json`、`...-icmr.json`、`...-linearity.json`、`...-noise.json`。
- RS 调优：`...-rs-tune.json`；checkpoint 同名保存在 `artifacts/checkpoints/`。
- remove/recovery：`...-remove-1.json`、`...-restored-dc-1.json`、`...-add-2.json`、`...-remove-2.json`、`...-restored-dc-2.json`。

首次 DC 命令的本地调用等待上限为 60 秒，而 run record 在约 78 秒后完整写出并成功。它是调用方等待边界，不是远端 transport、Spectre 或电路失败；没有把它记录为不可行候选，也没有因此重复 OA 写入。

## 证据分类

- `bridge_readback`：OA create/transform 前后结构、RS0/RS1 参数、NSP/NSN 连接、nets/pins/placement 和最终独立 inspect。
- `eda_result`：`si` 网表与哈希、Spectre OP/AC/transient/noise PSF、连续电流/电压/增益/功耗/噪声。
- `software_inference`：OA/网表一致性、电阻电流重算、KCL residual、工作区、带宽/GBW/unity/CMRR/P1dB/THD/积分、constraint/objective 与恢复等价判断。
- `user_input`：目标 cell、RS 候选、BIAS/VCM/VDD/CL、sweep、约束、预算与执行授权。

## 本地验证

```text
437 passed in 1.10s
117/117 example task plans passed
catalog passed
git diff --check passed
```

## 未验证边界与下一 Gate

- 本 Gate 只有 nominal `top_tt`；差分对 PVT 仍为显式可选项，不默认运行。
- ICMR 只是有限采样；尚未夹逼上边界。
- 未做 mismatch、Monte Carlo、offset distribution、PSRR、slew/settling 或稳定性裕量。
- 未创建差分对 Maestro setup，也未验证人工 ADE 打开、修改和重跑；旧 ADE L 仍按延期记录处理。
- 只支持成对相等的 RS。非对称退化可能用于 offset/mismatch 实验，但尚无契约与验证，不能通过本 Gate 外推。
- `add_source_degeneration` 是固定 exact delta，不代表任意拓扑编辑。保存成功后的任意后置失败仍没有通用 OA snapshot 回滚；本 Gate 证明的是显式 remove 恢复。
- Gate 5 没有专门注入空波形或 payload-after-send transport 中断；这些通用拒绝/checkpoint 路径有本地测试和既有 Gate 证据，但本次新拓扑没有重复制造远端故障。
- `SFE-1131 scalefactor` PDK scope warning 仍存在；没有被静默删除。

下一默认 Gate 是固定的 active-load/current-mirror 差分对拓扑。它比加入两只 RS 更大，因此先做 exact OA delta、器件匹配、DC KCL、偏置和工作区；DC 通过后再迁移 AC/CMRR/ICMR/transient/noise。PVT 仍只在任务显式选择时加严。
