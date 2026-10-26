# TSMC18 DC/AC 初步表征（2026-09-16）

状态：**TSMC18 nominal execution plus preliminary DC/AC characterization verified; reusable interpolation and design-quality closure pending**。

本轮先了解 `nch_mac/pch_mac` 在 nominal `tt` 下的 DC 工作区，以及简单共源级的 AC 权衡。不做大范围拓扑搜索，也不把 standalone deck 当作 OA 同源设计闭环。

## 边界与证据

- OA 写入：否；没有 target，也没有打开、创建或修改 cellview。
- 远端计算：`nics4304-cad1` Spectre host；工艺 `nics4304_tsmc18`，`tt + stat_noise`，`27 °C`。
- 远端只写 `/data/xum/virtuoso_bridge_smoke/tsmc18/vda_*` UUID 新目录，执行前检查不存在。
- Spectre `21.1.0.612.isr15`。
- raw OP、DC/AC PSF 和直接解析指标为 `eda_result`；profile/host probe 为 `bridge_readback`；归一化、插值审计、首值计算和 A/B 比较为 `software_inference`。
- 没有修改 Bridge、PDK、Vault 或人工 `cds.lib`。

Bridge 已把 Virtuoso daemon 与 standalone Spectre 定义为独立服务。本轮 VDA 去掉纯网表路径对 `SSHClient.is_running()` 的依赖，改用 Bridge 公开的 `spectre_runner` role；split-host 时 guard、deck、计算与 inventory 也落到 Spectre host。没有复制 SSH 逻辑，OA/`si`/Maestro 约束不变。

## DC 器件表征

任务：[tsmc18-mos-device-characterize-tt.bridge.json](../../examples/tasks/tsmc18-mos-device-characterize-tt.bridge.json)

有限域为 NMOS/PMOS、`W=2 µm`、`L=0.18/0.36 µm`、`|VGS|=0.30/0.45/0.60/0.75/0.90 V`、`|VDS|=0.10/0.90/1.50 V`、`VSB=0`：60 个训练点和 4 个真实留出点。

`|VDS|=0.9 V` 的代表结果：

| device | L (µm) | |VGS| (V) | |Id|, W=2 µm (µA) | gm/Id (V⁻¹) | gm/gds |
| --- | ---: | ---: | ---: | ---: | ---: |
| NMOS | 0.18 | 0.45 | 2.94 | 21.41 | 43.4 |
| NMOS | 0.18 | 0.60 | 34.24 | 11.80 | 35.9 |
| NMOS | 0.18 | 0.75 | 120.35 | 6.31 | 27.2 |
| NMOS | 0.36 | 0.60 | 13.61 | 12.17 | 118.5 |
| PMOS | 0.18 | 0.45 | 0.72 | 22.05 | 48.9 |
| PMOS | 0.18 | 0.60 | 9.23 | 12.26 | 39.1 |
| PMOS | 0.18 | 0.75 | 34.84 | 7.05 | 28.7 |
| PMOS | 0.36 | 0.60 | 4.40 | 10.81 | 100.8 |

`|VGS|=0.6 V` 是适合首轮模拟偏置的中等反型附近点：`gm/Id≈11–12 V⁻¹`。加长沟道降低同 W/VGS/VDS 电流，但明显提高 `gm/gds`。同 W、同 `|VGS|/|VDS|=0.6/0.9 V` 时 PMOS 电流约为 NMOS 的 `0.27×`；静态等电流宽度比约 `3.7:1`，但这不能直接替代反相器动态 rise/fall 校准。

### 插值门未闭合

4 个留出点只通过 2 个，最大归一化误差依次为 `86% / 11% / 58% / 14%`。两个失败点均为 `L=0.18 µm, |VGS|=0.525 V, |VDS|=0.5 V`。raw 64 点仍是有效 `eda_result`，但当前稀疏多线性插值器未校准；本轮 AC 只使用真实网格点。若实际设计落入该区域，应补局部点或采用合适的非线性模型，而不是抬高阈值。

DC manifest SHA-256 为 `cf566159ecc6f2779cb007ea3ffd939b0c6e020b11a67b71921899a532f998cc`，共 7 个非空输入/PSF/log 文件。

## DC 驱动的共源 AC

任务：[tsmc18-common-source-dc-seeded-ac.bridge.json](../../examples/tasks/tsmc18-common-source-dc-seeded-ac.bridge.json)

从 `VGS=0.6 V, VDS=0.9 V` 的真实 NMOS 点，用 `RD=(1.8-0.9)/Id` 得到 `L=0.18/0.36 µm` 的 `RD≈26.29/66.11 kΩ`，任务采用 `26.3/66.1 kΩ`。只运行基准、仅增大负载、增大沟长并重定 DC 中点三个有物理意义的条件。

| variant | L (µm) | CL (fF) | VOUT (V) | gain (dB) | BW (MHz) | UGF (GHz) | GBW (GHz) | power (µW) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `l018_cl10` | 0.18 | 10 | 0.8996 | 18.109 | 640.240 | 5.121 | 5.150 | 61.622 |
| `l018_cl100` | 0.18 | 100 | 0.8996 | 18.109 | 76.276 | 0.609 | 0.614 | 61.622 |
| `l036_cl10` | 0.36 | 10 | 0.9001 | 19.946 | 215.793 | 2.137 | 2.144 | 24.507 |

三点最小饱和余量为 `0.788–0.807 V`，低频参考平坦，−3 dB 与 unity crossing 均被 sweep 包围。10× 负载不改变 DC/低频增益，带宽降为 `0.119×`。长沟道条件相对基准增益提高 `23.5%`、功耗降 `60.2%`，但带宽降 `66.3%`、GBW 降 `58.4%`；因两个长度分别调整 RD，这不是同电流或同电阻比较，不能把全部差异只归因于沟长。

一阶 `Av≈gm(RD||ro)` 对两个长度的增益误差约 `1.9%/0.9%`。只计显式 10 fF 时，单极点带宽高估约 `22%`，真实带宽反推有效输出电容约 `12.2 fF`；100 fF 点误差降至约 `2.8%`。所以理论适合选偏置、算首值和预判方向，寄生和最终指标仍服从 PDK AC。

## 证据、清理和边界

- DC run：[01-device-characterization.json](../../artifacts/runs/tsmc18-dc-ac-exploration/01-device-characterization.json)
- AC run：[02-common-source-ac.json](../../artifacts/runs/tsmc18-dc-ac-exploration/02-common-source-ac.json)
- AC 三个 variant 各有 8 项 manifest；SHA-256 为 `f44bf5d5607f312c1d9eca09736d93badcd5e78193a8a1a195e6a24d2712bdcb`、`1fab5248370f2148cc016c0c492f2a4d80164e50d5808c966d1e0c8e95337ce9`、`58fc612cdb24463141741faa5788afb54c5d9b539cbe5bc33189003b4c7e333d`。
- 远端 DC/AC 证据根分别为 `..._59a25f650925`（348,257 bytes）和 `..._773813adba1a`（357,511 bytes），均在批准的 TSMC18 scratch。
- 结束检查：远端 `spectre=0`、`si=0`，本地 `65347` 无 listener；没有启动/关闭用户 Virtuoso。
- 回归：完整 VDA 测试 `919 passed`；`vda catalog` 和新增 AC task 的 `vda plan` 通过。
- 只覆盖 `tt/27 °C/VSB=0` 和有限偏置/尺寸；没有 PVT、noise、mismatch、linearity 或可靠性结论。
- AC 是 standalone 电阻负载共源，不是 OA→`si`/ADE 结果；没有 winner，也没有写回 OA。

下一道有价值的 Gate 是拿到用户实际 TSMC18 拓扑后，用本轮 DC 点确定首个偏置，再做该拓扑的 OA→`si` DC/AC 与规格判定。除非实际偏置落入插值失败区域，不继续扩器件网格。
