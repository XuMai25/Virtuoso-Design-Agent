# TSMC18 profile preflight（2026-09-15）

## 结论

状态：**profile 与本地证据契约已接入；真实 OA→si→Spectre smoke 待执行**。

这次工作使 VDA 能显式选择 `nics4304_tsmc18`，并修复了此前隐含的“OA cell 名必然等于 Spectre model 名”假设。它没有改动 Bridge、Obsidian Vault、服务器 PDK 或现有 `Analog_VLSI` cellview，也没有把一次本地测试写成 TSMC18 电路性能证据。

## 远端只读事实

- PDK 根：`/data/xum/Analog_VLSI/PDK_TSMC18_1809`。
- OA tech library：`tsmc18`。
- 标准 1.8 V OA master：`nmos2v_mac`、`pmos2v_mac`。
- CDF/Spectre terminal order：`D G S B`。
- CDF 可调尺寸字段：`w`、`wf`、`l`、`fingers`、`simM`；Spectre propMapping 包含 `w <- wf`、`nf <- fingers`、`multi <- simM`。
- Spectre model/subcircuit：NMOS `nch_mac`、PMOS `pch_mac`。这与 OA master 名不同。
- 标准 MOS include：`models/spectre/cor_std_mos.scs`，可用 section 为 `tt`、`ff`、`ss`、`sf`、`fs`。
- 现有 `/data/xum/Analog_VLSI/icfb/Analog_VLSI` 绑定 `tsmc18`，只用于核实人工环境；VDA 不以它作为自动 smoke 写入目标。

以上属于环境/PDK readback，不是 `eda_result`，也不能证明任何电路规格。

## 实现

- `PdkProfile` 新增 `nmos_model/pmos_model`。`nmos_cell/pmos_cell` 继续只描述 OA master。
- inverter/common-source/differential-pair 的 `si` parser 改为核对 model 字段；OA 结构断言仍核对 cell 字段。
- standalone preview 与 MOS characterization deck 改为使用 model 字段；从真实电路 run 派生 characterization task 时也核对 model 字段。
- N28 LVT/SVT profile 显式补齐 model 字段，保持原行为和默认选择。
- TSMC18 只读复用已核实的 `/data/xum/Analog_VLSI/icfb/cds.lib` 和已绑定 `tsmc18` 的 `Analog_VLSI` work library；只把仿真产物放入独立运行子目录，不修改人工 `cds.lib`。

## 默认值边界

PDK CDF 直接给出 `W=2 µm`、`L=180 nm` 和 1.8 V nominal device 身份。profile 暂以 `Wn=2 µm`、`Wp/Wn=2.0`、`CL=10 fF`、共源偏置 `0.9 V`、`RD=20 kΩ` 作为可覆盖的初始化值。除 W/L 的 CDF 起点外，这些值尚未经过 TSMC18 电路校准；尤其 2:1 不是已证明的最优比例。任务显式参数和已有 OA 回读仍优先于 profile 默认值。

## 本地验证

- TSMC18 profile 加载，N28 默认保持不变。
- 普通 `schematic.inspect` 任务可编译出无写入/无计算计划。
- OA readback 接受 `tsmc18/nmos2v_mac` 与 `tsmc18/pmos2v_mac`。
- 同一 profile 的 `si` parser 接受 `nch_mac/pch_mac`，并拒绝把 OA cell 名冒充 simulator model。
- standalone Spectre deck 使用 `cor_std_mos.scs section=tt` 和 `nch_mac/pch_mac`。
- 涉及 profile、preview、器件表征和 source binding 的聚焦回归：`170 passed`。
- VDA 全量回归（仓库内独立 pytest basetemp）：`915 passed`。

## 下一道 live Gate

1. 只读复核 `Analog_VLSI` 仍绑定 `tsmc18`，并保存当前 `cds.lib` SHA-256；不改写 library 配置。
2. 非覆盖创建 `Analog_VLSI/vda_tsmc18_inv_001/schematic`；若同名对象已存在则停止。
3. 回读 OA master、D/G/S/B、pins/nets 和 `wf/l`；确认请求参数真实持久化。
4. 自动 `si` netlist，要求实例为 `nch_mac/pch_mac` 且 W/L 与 OA 回读一致。
5. 运行一个 nominal `tt` transient，要求有非空波形、解析指标和输入/deck/result/log manifest；无规格时只称 execution smoke，不称设计闭环。
6. 结束后验证本地 tunnel/worker 与远端本次 Spectre/si 进程清理。
