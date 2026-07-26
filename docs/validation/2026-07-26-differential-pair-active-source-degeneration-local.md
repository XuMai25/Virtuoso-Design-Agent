# 2026-07-26 有源负载差分对与对称源退化组合本地 Gate

## 结论

状态：**active-load plus symmetric source-degeneration contract and analysis routing locally verified; live OA/Spectre Gate pending**。

本轮没有写远端 OA、没有运行 Spectre，也没有修改 `virtuoso-bridge-lite`。VDA 现能把已经支持的 PMOS 电流镜负载差分对，与通用 topology-delta 增加的两支对称源电阻组合成一个正式可识别拓扑；不是为本次 smoke 写一套新的网表模板或分析器。

## 组合拓扑

新增拓扑标识：

```text
pmos_current_mirror_load_nmos_differential_pair_with_tail_device_and_source_degeneration
```

精确结构为：

```text
MN0 (OUTP INP NSP VSS)
MN1 (OUTN INN NSN VSS)
MNTAIL (TAIL BIAS VSS VSS)
MP0 (OUTP OUTP VDD VDD)
MP1 (OUTN OUTP VDD VDD)
RS0 (NSP TAIL)
RS1 (NSN TAIL)
```

旧的专用 Gate 5/Gate 6 transform 仍各自只处理一种变化；组合由 `existing_schematic + topology_delta` 完成。显式 operation 文件为 `examples/topology/differential-pair-active-add-symmetric-source-degeneration.operations.json`，只添加 `NSP/NSN`、重连两只输入 NMOS 的源端并加入 `RS0/RS1`。参数值不进入结构指纹，仍由后续 `parameters.apply` 写入并双重回读。

## 本地实现与硬门

- OA readback 只接受精确七实例组合、两支完全对称的 PMOS/RS、正确 master、端子与 `NSP/NSN` nets。
- `si` parser 不再把 active load 与 source degeneration 视为互斥；它同时核对 MP0/MP1 W/L、RS0/RS1 电阻、输入管源节点及尾管连接。
- semantic 参数同时保留 `pmos_load_width_um/pmos_load_length_um` 与 `source_resistance_ohm`；active-load 仍拒绝 `load_resistance_ohm`。
- DC 从真实 `NSP/NSN/TAIL` 电压重算两支 RS 电流，同时核对 MN0/MN1、MP0/MP1、MNTAIL 与 VDD KCL；任一层误差超过 1% 都是证据失败。
- 现有单端 `OUTN` 动态输出契约继续用于 AC、共模 AC/CMRR、noise、transient/THD 和 PSRR；源退化只额外保存 NSP/NSN 并改变真实 OP，不复制分析器。
- demo adapter 使用同一 topology contract 完成 forward、RS 参数写入、AC/CMRR、transient、noise、两点 ICMR 和 inverse；结果继续标为 `software_inference`。

回归结果：

```text
632 tests collected / 632 passed
174/174 example task plans
compileall: passed
catalog: passed
```

## 只读远端预检

既有 `vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic` 结构化 readback 成功，证据为 `bridge_readback`：

```text
instances = MN0,MN1,MNTAIL,MP0,MP1
topology = pmos_current_mirror_load_nmos_differential_pair_with_tail_device
Wn/L = 1.215/0.03 um
Wp/L = 1.08/0.03 um
Wtail/L = 0.555/0.03 um
```

这些值被用作新 cellview 的控制基线，使真实 Gate 只引入拓扑 delta 与 RS=500 ohm，而不是同时改变三组 MOS 尺寸。

对计划中的新目标 `vb_pdk_smoke/vda_diffpair_active_deg_generic_001/schematic` 做只读 inspect 时，Bridge probe 成功，但 OA reader 返回 SKILL load `system_event`。这与目标尚不存在相符，但错误没有结构化到“cell missing”，因此不把该失败单独当作不存在证明；后续 `schematic.create` 仍必须以 `replace_existing=false` 自行检查并拒绝任何既有对象。

资源 dry-run 显示远端 `spectre=0`、`si=0`、VDA-managed Maestro session=0；本地没有残留 Python/SCP/Spectre/si，只保留原有共享 SSH jump chain 的两个既有 PID。没有执行清理或删除。

## 待授权 live Gate

计划新建、不覆盖：

```text
vb_pdk_smoke/vda_diffpair_active_deg_generic_001/schematic
```

执行顺序：

1. create 差分对 core，加入 MNTAIL，再把 RD0/RD1 替换为 MP0/MP1；三组 MOS 采用上述既有 active-load readback 尺寸。
2. 独立 inspect 后用真实 before readback 编译 exact forward/inverse contract，不能预写指纹。
3. forward 后完整 OA readback，写入并回读 `RS0=RS1=500 ohm`，自动 `si` 核对七实例和全部 semantic 参数。
4. 先执行 DC；只有结构、参数、KCL 和工作区层通过，才依次执行差模 AC+共模 AC/CMRR、noise、transient/THD、ICMR；PSRR 作为额外可选 nominal 只读分析放在主链通过之后。
5. 执行 exact inverse、独立 readback 并重跑恢复后的 active-load DC。前一层失败即停止，不先做大规模参数搜索。

远端仿真 scratch 预计只出现在 `/data/xum/virtuoso_bridge_smoke/vda_differential-pair-active-degenerated-*_<nonce>`。不写 `/home/xum`，不覆盖任何 cellview，不修改 Bridge 或 Obsidian Vault。

本地完成不能证明真实 OA editor 对两支实例位置/label 的结果、`si` 实际七实例网表、Spectre 可行性、bandwidth/GBW/CMRR/noise/THD/ICMR/PSRR 数值，或 inverse 后真实结构恢复；这些必须由下一轮 live 证据补齐。
