# 2026-07-23 共源源极退化可逆拓扑微调真实验证

## 结论

状态：**reversible controlled common-source topology patch verified; generic topology editing and L5B closure pending**。

VDA 已把源极退化从单向 add 补成两个明确、幂等的 `schematic.transform` 动作：

```text
add_source_degeneration:
  MN0.S: VSS -> NSRC
  add RS0(NSRC, VSS)

remove_source_degeneration:
  delete RS0 and its two VDA-created wire/label stubs
  MN0.S: NSRC -> VSS
  remove NSRC
```

真实 Gate 在全新 `vb_pdk_smoke/vda_cs_topology_patch_001/schematic` 上完成 nominal create/DC/AC → add/DC/AC → remove/DC/AC。没有覆盖既有对象，`replace_existing=false`；所有 Spectre scratch 均位于 `/data/xum/virtuoso_bridge_smoke/vda_common-source-topology-patch-*`。Bridge 与 Obsidian Vault 均未修改。

## 安全与能力契约

- 旧任务省略 `schematic_transform` 时继续解析为 `add_source_degeneration`；显式 add 只接受 `source_resistance_ohm`。
- remove 必须显式声明且不接受参数；nominal 上重复 remove 为幂等 no-op。
- 两个动作都先精确识别 VDA common-source topology，拒绝未知实例、pins、网络和未保存 OA 编辑。
- remove preflight 以 RS0 两个端子中心为锚，分别要求唯一的 VDA wire 与 label；不按全局 VSS/NSRC 名称批量删除。
- existing cellview 始终用 Bridge editor `mode="a"`；保存前失败只 purge 当前未保存缓存。
- executor 独立比较前后 MN0/RD0 的 master、位置、方向和完整 CDF 参数；pins 不得变化，remove 后实例必须严格为 MN0/RD0 且不得残留 NSRC/RS 参数。
- 可选 `expected_restored_placement_sha256` 覆盖 Bridge `read_placement` 的实例、pin、标签和导线；它进入 plan token，worker 与 executor 都要求保存后完全匹配。

该实现复用 Bridge 的公开 schematic editor、reader 和 SKILL channel；没有复制 SSH、OA 保存或传输逻辑，也没有修改第三方库。它仍不是任意图编辑 DSL，也没有为任意保存后失败提供通用 snapshot rollback。

## OA placement 与结构闭合

add 前 placement：

- SHA-256：`b59661b2047674c4a7da81b56fe745a76bac56082e08bdd92d0e7d2f6433ea35`
- labels/wires：6/6
- labels：`IN, OUT, OUT, VDD, VSS, VSS`

add 后、remove 前：

- SHA-256：`13bdf8bcf9d293b4e397b1d0bdcbb1b709b538c54d73865d781e6cb3fbd1d03e`
- labels/wires：8/8
- 只增加两个 NSRC label、两条 RS0 stub 和 RS0；MN0/RD0/pins 保持。

remove 后：

- SHA-256 回到 `b59661b...ea35`
- labels/wires 回到 6/6，`restored_placement_match=true`
- 实例严格为 MN0/RD0；nets 为 IN/OUT/VDD/VSS；MN0.S/B 均为 VSS；无 RS0、NSRC 或 `source_resistance_ohm`。

因此本 Gate 的“恢复”不仅是结构摘要相似，而是 Bridge placement 指纹与 add 前基线完全相同。

## OA → si → Spectre 结果

固定条件：`W=1 µm, L=0.03 µm, RD=20 kΩ, Vbias=0.35 V, VDD=0.9 V, CL=2 fF`；退化阶段额外 `RS=1 kΩ`。

| 指标 | nominal add 前 | RS=1 kΩ | nominal remove 后 |
| --- | ---: | ---: | ---: |
| Id (µA) | 31.5619 | 24.8196 | 31.5619 |
| saturation margin (V) | 0.163479 | 0.282681 | 0.163479 |
| DC power (µW) | 28.4060 | 22.3370 | 28.4060 |
| low-frequency gain (V/V) | 4.58848 | 3.70059 | 4.58848 |
| bandwidth (GHz) | 6.76211 | 4.89789 | 6.76211 |
| GBW (GHz) | 31.0278 | 18.1251 | 31.0278 |
| unity-gain frequency (GHz) | 30.3567 | 17.4997 | 30.3567 |

两种 topology 的 DC 与 AC 均 `analysis_complete=true`，AC 各有 271 个复数采样点。nominal `si` netlist SHA-256 在 add 前与 remove 后同为 `9280f812770b16adfebd62af44f2889e8dfa71e26e31155901400567edf62700`；退化网表为 `90857b759334ff442171476f4e0cd52fd574b1d4f86ac3937fb7cae4df8e285e`，其中包含 `MN0(OUT IN NSRC VSS)` 和 `RS0(NSRC VSS)`。程序化比较确认 nominal 前后 17 个 DC 指标和 28 个 AC 指标的非零差分均为空。

退化阶段 gain/BW/GBW 的变化是相同 W/L/RD/bias/load 下只加入 RS 的受控 A/B 结果，但仍只适用于这个设计点，不应外推为所有偏置、工艺和负载下的普遍比例。

## 证据记录

- create：`artifacts/runs/common-source-topology-patch-create/live-20260723.json`
- add：`artifacts/runs/common-source-topology-patch-add/live-20260723.json`
- remove：`artifacts/runs/common-source-topology-patch-remove/live-attempt1-20260723.json`
- nominal DC：`artifacts/runs/common-source-topology-patch-nominal-dc/before-add-20260723.json` 与 `after-remove-20260723.json`
- nominal AC：`artifacts/runs/common-source-topology-patch-nominal-ac/before-add-20260723.json` 与 `after-remove-20260723.json`
- degenerated DC/AC：对应 `common-source-topology-patch-degenerated-*` 的 `live-20260723.json`

`bridge_readback` 覆盖 OA 结构、参数与 placement；`eda_result` 覆盖 `si` 输入、Spectre DC/AC 和连续指标；topology/参数一致性、工作区分类、交点提取和前后差分属于 `software_inference`；task 参数与执行授权属于 `user_input`。没有以 return code、OA 存在或单一指标宣称闭合。

## 未闭合与下一道 Gate

- remove 仅支持 VDA 已知 common-source source-degeneration delta，不删除任意人工器件或网络。
- 保存成功但后置审计失败时没有通用 OA snapshot rollback；run 会失败并保留真实状态。
- 未验证用户在 add/remove 之间人工移动 RS0 或 stub 后的恢复；当前 preflight 会因几何选择不唯一或缺失而拒绝，而不是猜测。
- PVT 是可选项，本 Gate 只跑 nominal TT profile；不能外推到其他 foundry/node/model section。

该记录之后的 raw CDF Gate 已在同一测试 cell 上闭合 `MN0.fingers=1/2` 的逐候选定向回读、失败恢复、不同 `si` 网表和最终最佳写回，见 [`2026-07-23-common-source-raw-instance-tuning-live.md`](2026-07-23-common-source-raw-instance-tuning-live.md)。下一默认产品 Gate 转向差分对的固定模板、nominal DC 支路平衡/尾电流/工作区，再进入差模 AC 与 CMRR；不把任意图编辑或全部 CDF 自动搜索作为前置条件。
