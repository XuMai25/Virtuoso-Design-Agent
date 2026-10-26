# 2026-07-23 差分对真实尾管 Gate 4 live 验证

## 结论

状态：**real-tail same-source DC/AC/CMRR/ICMR/transient/noise verified**。

本 Gate 在全新 `vb_pdk_smoke/vda_diffpair_tail_gate4_001/schematic` 上，把 Gate 3 的四器件差分对 core 以严格增量方式加入真实 OA 尾管 `MNTAIL` 和 `BIAS` pin，并证明现有 OA 回读、`si` 自动网表化、Spectre、有限搜索、规格判定、checkpoint、最佳 OA 写回和多 analysis 指标链可以继续使用。它没有修改或复制 `virtuoso-bridge-lite`。

这仍不是完整 L5B：差分对 PVT、mismatch/Monte Carlo、ADE/Maestro 多 test/多 analysis、人工打开/修改/重跑、版图和任意拓扑综合均未验证。

## 范围与安全边界

- PDK/profile：`nics4304_tsmc28`，TSMC N28/`tsmcN28`，nominal `top_tt`。
- 新目标：`vb_pdk_smoke/vda_diffpair_tail_gate4_001/schematic`。
- 原 Gate 3 cell 未修改；`replace_existing=false`。
- 远端产物位于 `/data/xum/virtuoso_bridge_smoke/`；没有写 `/home/xum`。
- create、tail transform 和最佳 tail-width 写回在本次用户授权的新 cell 范围内；bias、AC、ICMR、transient 和 noise 均不写 OA。
- PVT 是可选项，本 Gate 未运行。
- Bridge 仍为 0.7.0；只调用公开 editor/inspect/SKILL/`si`/Spectre/传输能力。第三方仓库保持既有干净分支 `codex/vda-transport-recovery`、提交 `e74379a`，本 Gate 没有第三方改动。

## 新增契约

1. `schematic.transform` 新增 `add_tail_device`。它只接受精确 Gate 3 core，在 append 模式增加：
   - `MNTAIL(TAIL,BIAS,VSS,VSS)`；
   - 输入 pin `BIAS`。
2. transform 前后独立 inspect；原 `MN0/MN1/RD0/RD1`、已有连接、pins 和 placement 必须保持，只允许声明的新增 delta。
3. `tail_width_um/tail_length_um` 属于 OA semantic 参数；写入后立即回读，并在 `si` 网表中核对 MNTAIL master、节点、W/L、fingers 和 multiplicity。
4. `tail_bias_v` 是 wrapper 的 BIAS 电压，不持久化到 OA。真实尾管模式与理想尾源的 `tail_current_ua/tail_output_resistance_ohm` 互斥。
5. DC 从实际 MNTAIL OP 提取 `ids/vgs/vds/vdsat/gm/gds`。尾电流必须与两输入支路之和匹配；VDD 与两只负载 KCL 继续独立核对。
6. `analysis_complete` 只描述所需数据和解析是否齐全。器件非饱和属于显式工作区 metric/warning，并由 constraint 判定可行性，不再把完整的不可行边界误归为证据缺失。
7. differential noise 使用唯一 `VIN_DIFF(VDIFF,0)` 作为 Spectre `iprobe`，由 `+0.5/-0.5` VCVS 叠加到精确 VCM；输入参考定义为 `INP-INN = VIN_DIFF`。

## OA 创建与增量 transform

- create token：`8da8cd3c529ee82b`；状态 `succeeded`。
- transform token：`46798c68ffb34f45`；状态 `succeeded`。
- transform 前：`MN0/MN1/RD0/RD1`，pins 为 `INP/INN/OUTP/OUTN/TAIL/VDD/VSS`。
- transform 后：只增加 `MNTAIL` 与 `BIAS`；尾管初值 `W=1.0 µm/L=0.03 µm`。
- 独立 after inspect 的 topology 为 `resistive_load_nmos_differential_pair_with_tail_device`。

本地记录：

- `artifacts/runs/differential-pair-tail-gate4-create.json`
- `artifacts/runs/differential-pair-tail-gate4-transform.json`

## 真实 DC 调优与 OA 写回

### BIAS 五点只读搜索

名义 `VCM=0.55 V`、`VDD=0.9 V`、初始 `MNTAIL.W=1.0 µm`。约束包含三管工作区、尾电流 `50±25 µA`、KCL、摆幅余量和功耗；objective 最大化摆幅余量。

| BIAS (V) | 实际尾电流 (µA) | 摆幅余量 (V) | 尾管饱和余量 (V) | DC 功耗 (µW) | 可行 |
|---:|---:|---:|---:|---:|:---:|
| 0.300 | 14.548 | 0.0582 | 0.2321 | 13.094 | 否，尾电流低于约束 |
| 0.325 | 22.159 | 0.0886 | 0.2048 | 19.943 | 否，尾电流低于约束 |
| 0.350 | 32.292 | 0.1292 | 0.1762 | 29.064 | 是 |
| 0.375 | 44.969 | 0.1799 | 0.1466 | 40.473 | 是 |
| 0.400 | 59.952 | 0.2398 | 0.1166 | 53.957 | 是，选中 |

记录：`artifacts/runs/differential-pair-tail-gate4-dc-bias-resumed.json`。前两次候选 1 的本机 SCP/DNS 失败保留为 `system_event`；checkpoint 与独立 OA 回读后完成 5/5，未把传输错误算成电路不可行。

### 尾管宽度三点 checkpoint

固定 `BIAS=0.40 V`，搜索 `MNTAIL.W=[0.8,1.0,1.2] µm`。每点都执行 OA 暂存、定向回读、`si` 参数一致性和真实 DC；objective 在可行点中最小化功耗。

| W (µm) | 实际尾电流 (µA) | 摆幅余量 (V) | DC 功耗 (µW) | 可行 |
|---:|---:|---:|---:|:---:|
| 0.8 | 49.368 | 0.1975 | 44.432 | 是，选中 |
| 1.0 | 59.952 | 0.2398 | 53.957 | 是 |
| 1.2 | 70.151 | 0.2806 | 63.136 | 是 |

最佳 `W=0.8 µm/L=0.03 µm` 已写回，并由独立 inspect 回读为 `Wfg=w=800n`。记录：`artifacts/runs/differential-pair-tail-gate4-width.json`。

## AC、带宽、GBW 与 CMRR

固定最终 OA、`BIAS=0.40 V`、`VCM=0.55 V`、每端 `CL=1 fF`，1 kHz–1 THz、20 points/decade。差模与共模两次运行共享同一 OA/`si` 网表，DC OP 和频率网格匹配。

- 差模低频增益：`2.927605 V/V`（`9.33025 dB`）。
- 差模 −3 dB 带宽：`13.805733 GHz`。
- GBW：`40.417736 GHz`。
- unity-gain frequency：`38.452575 GHz`。
- 共模低频增益：`0.256900 V/V`（`−11.8047 dB`）。
- 低频 CMRR：`21.134955 dB`。
- CMRR 首次下降 3 dB 的带宽：`19.069097 GHz`。
- 两条 AC 各 181 点，低频 reference 均为 flat；analysis 完整。
- 最终 OA 动态分析共同 `si` 网表 SHA-256：`3056a495e93e59cc0dd87b342ea061d2153981dda2bd3e97eaeafc16edcf40b9`。

记录：`artifacts/runs/differential-pair-tail-gate4-ac-cmrr.json`。

## ICMR 十点扫描

固定最终 OA 与 `BIAS=0.40 V`，扫描 `VCM=0.35–0.80 V`、步进 0.05 V。全部十点最终均 `analysis_complete=true`。

- `0.35 V`：输入对仍饱和，但 MNTAIL 饱和余量 `−19.5 mV`，故规格不可行。
- `0.40–0.80 V`：本次离散点均通过三管工作区、KCL、偏移和 50 mV 摆幅余量。
- `0.65 V`：当前网格中摆幅余量最大，`222.2 mV`，被 objective 选中。
- `0.80 V`：仍通过，但摆幅余量已降到 `84.1 mV`。因此只称采样通过范围 `0.40–0.80 V`，低边界夹在 `0.35–0.40 V`；没有找到或外推连续上边界。

首轮 run 因把“MNTAIL 非饱和”错误计入 `analysis_complete` 而为 `partial`；该记录保留在 `differential-pair-tail-gate4-icmr.json`。修正后完整重跑为 `succeeded`：`artifacts/runs/differential-pair-tail-gate4-icmr-complete.json`。

## 100 MHz transient 线性度

固定最终 OA、名义 `VCM=0.55 V`、每端 `CL=1 fF`，差分输入 peak 幅度为 `[5,20,50,100,150,200] mV`。每点使用 4 个 settling cycles、8 个 measurement cycles 和相干 Fourier 投影。

- 小信号增益：`2.926910 V/V`（`9.32819 dB`），与 AC 的 `2.927605 V/V` 一致。
- 输入 P1dB：`116.335 mV peak`；输出 P1dB：`301.018 mV peak`。
- 200 mV peak 点：增益 `2.160844 V/V`、THD `10.1699%`、HD3 `−19.8953 dBc`。
- 最大平均 VDD 功耗：`45.6342 µW`；小信号功耗 `44.4323 µW`。

第一次运行已完成远端 raw，但下载时 SSH 到 `design.nics4304.top:22` 超时；失败记录保留。Bridge doctor 随即通过，第二次运行成功：`artifacts/runs/differential-pair-tail-gate4-linearity-retry.json`。

## differential noise

频带 1 kHz–10 GHz、20 points/decade；ordinary noise PSF 共 141 点。

- 输入参考积分噪声：`921.765 µV RMS`。
- 差分输出积分噪声：`2.584496 mV RMS`。
- 输入噪声密度：1 kHz `2219.50 nV/√Hz`，10 GHz `6.9759 nV/√Hz`。
- 输出噪声密度：1 kHz `6497.81 nV/√Hz`，10 GHz `16.5410 nV/√Hz`。
- PSF SHA-256：`3b1affdbd7c473c740fa69905799e906581bdb47a6a018e3513aac39e302df41`。

首版单一 `VIN_DIFF(INP,INN)` 加对称 1 TΩ VCM links 虽被 Spectre 接受，但实际 DC `INP=0.439511 V`，被 VDA 与声明 `0.55 V` 的一致性检查拒绝。修正为 `VIN_DIFF(VDIFF,0)` 加 `+0.5/-0.5` VCVS 后，DC 共模、非空 PSF、积分与工作区全部通过。成功记录：`artifacts/runs/differential-pair-tail-gate4-noise-vcvs.json`。

这些噪声约束用于证明数据链完整，不是已经优化过的设计规格；当前数值不能包装成低噪声设计完成。

## 证据分类

- `bridge_readback`：OA create/transform 前后结构、实例参数、最终 MNTAIL W/L、nets/pins/placement。
- `eda_result`：`si` 网表内容与哈希、Spectre OP/AC/transient/noise PSF、连续电流/电压/增益/功耗/噪声结果。
- `software_inference`：OA/网表一致性、工作区分类、KCL residual、带宽/GBW/unity/CMRR/P1dB/THD/积分规则、constraint 与 objective 判定。
- `user_input`：目标 cell、BIAS/VCM/VDD/CL、sweep、约束、预算和执行授权。

return code 0、OA 对象存在或 Spectre 完成均未被单独用作通过依据。

## 本地验证

```text
431 passed in 1.14s
106/106 example task plans passed
catalog passed
git diff --check passed
```

## 未验证边界与下一 Gate

- 本 Gate 只有 nominal `top_tt`；差分对 PVT 仍为显式可选加严项，不默认运行。
- ICMR 是有限采样，不是解析连续范围；上边界尚未夹逼。
- 未做 mismatch、Monte Carlo、offset distribution、PSRR、slew/settling 或稳定性裕量。
- 未创建差分对 Maestro setup，也未验证人工 ADE 打开、修改和重跑。
- `add_tail_device` 是一个精确固定 delta，不代表 VDA 已能任意生成或修改拓扑。
- transform 的拓扑保存与随后 CDF 参数写入仍是两个受控步骤；若拓扑已保存后参数写入失败，目前没有通用 OA snapshot 自动回滚，只能依靠独立 inspect、显式修复或后续可逆 transform。
- `SFE-1131 scalefactor` 三条 PDK scope warning 仍存在；Spectre 为 0 error，但该 warning 没有被静默删除。
- transient 下载超时仍靠保留失败 record 后重试；尚未实现从已完成远端 raw 直接恢复下载。

下一默认 Gate 是差分对的另一个可逆、exact-delta 拓扑小变更。优先候选是对称 source degeneration：在同一 cellview 中把两只输入管 source 分开、各加入一只退化电阻后再汇入 TAIL，并要求 add→完整 DC/AC/CMRR/ICMR/transient/noise→remove 的结构、网表和指标恢复。active-load/current-mirror 属于再下一层更大拓扑变化。PVT 只在任务显式选择时并行加严，不作为进入该 Gate 的默认前置成本。
