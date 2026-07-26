# 2026-07-26 共栅微调与 topology recovery live Gate

## 结论

本轮在 TSMC N28 nominal `top_tt` 上真实完成两条此前只有本地证据的路径：

1. post-save 参数审计失败后，VDA 只在新鲜 OA 结构与预期写后结构精确一致时执行预声明 inverse，并由独立 readback 证明 topology、placement 和完整目标参数恢复；原请求仍保留为失败。
2. 在既有普通共源 cellview 内只增加 `MNCAS/NCAS/VCAS/VCAS pin` 并重连 `MN0.D`，随后由同一 OA 自动 `si` netlist 依次运行 9 点 DC 与完全相同的 9 点 AC，最后 exact inverse 回到普通共源，并用恢复前后的 DC/AC 复跑做身份检查。

当前状态是 **recoverable common-source-to-cascode topology tuning and bounded same-source DC/AC selection live verified at nominal top_tt**。这证明了受控拓扑微调、恢复和同源性能评估能力，不是任意拓扑综合、连续全局最优或完整设计质量闭环。

## 范围与安全边界

- PDK/profile：`nics4304_tsmc28`，TSMC N28 `top_tt`。
- recovery cell：`vb_pdk_smoke/vda_master_recovery_001/schematic`。
- cascode cell：`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`。
- 两个对象都是本轮新建，`replace_existing=false`；没有覆盖既有 cellview。
- OA 写入和远端计算均在用户对上述精确 target、`/data/xum/virtuoso_bridge_smoke/vda_*` scratch 与不覆盖边界明确授权后执行。
- 没有修改 `virtuoso-bridge-lite`，没有修改 Obsidian Vault，也没有把手写 deck 作为原理图仿真源。

## 真实 post-save recovery

故障任务把 `MN0` 从 `nch_lvt_mac` 替换为 `nch_mac`，并声明 `m=1 -> 2`。当前 PDK callback 将该值归一回 `1`，所以写后定向参数审计按预期失败。run record 保留：

- 原 topology：`a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41`。
- 已确认写后 topology：`41a41dc9195c7958cb835223726767459eb0058a67f5eaa7cd31e34385cab038`。
- 自动 inverse 后 topology：再次为 `a0bb019a...`。
- 恢复 placement：`b59661b2047674c4a7da81b56fe745a76bac56082e08bdd92d0e7d2f6433ea35`。
- 恢复参数：`MN0.Wfg=1u, l=30n, m=1`，由独立 targeted CDF readback 确认。

故障 run 的最终状态仍是 `failed`、失败动作仍标为 `system_event`；`automatic_inverse_recovery.status=restored` 只说明恢复成功，不会把原写入包装成成功。随后单独的 `schematic.inspect` 再次读到 `MN0/RD0`、四个原 pins、LVT master 和相同 placement。

## 共源到共栅的原位微调

前向 delta 只执行：

1. 新增 `NCAS`、`VCAS` net。
2. `MN0.D: OUT -> NCAS`。
3. 新增 `MNCAS(OUT,VCAS,NCAS,VSS)`。
4. 新增 `VCAS` input pin。

真实结构审计得到：

- before topology：`a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41`。
- after topology：`2e27d1c68012bc1e7adc0da936a648febde4b685cfa1391512a2d401fb291a51`。
- before exact placement：`b59661b2047674c4a7da81b56fe745a76bac56082e08bdd92d0e7d2f6433ea35`。
- after logical-pin-bound placement：`00af355e590acefe3857987692020320110f693cabff0f724c1f0b11a00774ee`。

Virtuoso 每次重新创建 `VCAS` pin 时会递增内部 figure 名，如 `PIN4/PIN5/PIN6`，即使逻辑 pin、master、方向、坐标、wire 和 label 都相同。VDA 因此保留原始 OA-name placement SHA，同时只对 `basic/ipin|opin|iopin` figure 以独立回读的逻辑 pin 名做稳定绑定；普通器件实例名仍进入指纹。forward 的匹配模式明确记录为 `logical_pin_bound`，inverse 回到基线时仍是 `exact_oa_names`。这不会忽略器件重命名或几何漂移。

真实 inverse 还暴露一个有效边界：某次部分恢复后，待重连端子已没有可改名的 label/wire。修复后的 reconnect 先用 exact instance/instTerm/current-net CAS；若且仅若该端子同时没有 label 和 wire，复用 Bridge 已有的 `schematic_label_instance_term` 构造最小 replacement stub；存在 wire 但缺 label 等歧义状态仍拒绝。该实现完成了剩余 inverse，独立 inspect 恢复原 topology/placement，随后又做了一轮完整 forward→inverse 重放。

新建 `MNCAS` 的 CDF 将 `m` 表示为 `iPar("simM")`。VDA 只解析这一种 exact identifier indirection，并从同一份 readback 取 `simM=1`；缺失、嵌套或任意 SKILL 表达式都会拒绝，不执行字符串。随后 `MNCAS.Wfg/l/m=1u/30n/1` 的 apply 与独立回读成功。

## 理论 seed 与完全相同的 DC/AC 候选

seed 来源是恢复前普通共源的 real-Bridge DC run，来源文件 SHA-256 为 `80efd1aa541a47d99569758f86fbe78d38a5b1871b1890edf52848a0206dc766`。policy/result/template 均进入候选来源绑定。编译器还独立核对 source run 的成功 `bridge.probe.profile=nics4304_tsmc28`，并要求来源 MN0 的 OA/`si` finger width、总宽度、`nf=1`、`m=1` 一致。当前 candidate mapping 没有携带 MNCAS 的 count 参数，所以非 1/1 来源会 fail closed，而不是生成强度含义不一致的推荐。量化后的 9 个原子 tuple 顺序为：

| index | Wcas (µm) | Lcas (µm) | VCAS (V) |
|---:|---:|---:|---:|
| 1 | 1.00 | 0.03 | 0.505 |
| 2 | 1.00 | 0.03 | 0.480 |
| 3 | 1.00 | 0.03 | 0.530 |
| 4 | 1.25 | 0.03 | 0.495 |
| 5 | 1.25 | 0.03 | 0.470 |
| 6 | 1.25 | 0.03 | 0.520 |
| 7 | 0.75 | 0.03 | 0.520 |
| 8 | 0.75 | 0.03 | 0.495 |
| 9 | 0.75 | 0.03 | 0.545 |

DC 与 AC run 都是 `declared=attempted=completed=9`、`domain_exhausted=true`，9/9 候选可行。两份 run 的 tuple 顺序逐项相同，而且对应候选的 OA 自动 `si` netlist SHA-256 为 9/9 匹配；VCAS 只在 wrapper 中作为 testbench 条件变化，不伪装成 OA 参数。

| Gate | 声明 objective 下的最佳离散点 | 关键真实结果 |
|---|---|---|
| DC | `Wcas=1.00 µm, Lcas=0.03 µm, VCAS=0.480 V` | output swing margin `0.222649 V`；MN0/MNCAS 饱和余量 `0.038668/0.183981 V`；Id `23.8753 µA`；功耗 `21.4878 µW` |
| AC | `Wcas=0.75 µm, Lcas=0.03 µm, VCAS=0.545 V` | gain `6.44124 V/V` (`16.1794 dB`)；BW `3.61273 GHz`；GBW `23.2705 GHz`；unity `22.1895 GHz`；output swing margin `0.172029 V`；功耗 `23.3539 µW` |

DC 与 AC 最佳点不同，因为两个任务的 objective 不同；VDA 没有把 DC 最佳点强行包装成 AC 最佳点。两者都只称为声明且完整穷尽的 9 点离散域内最佳，`continuous_optimum_claim=false`、`global_optimum_claim=false`。

## 恢复身份检查

AC 完成后执行 exact inverse，并独立 inspect 恢复普通共源 topology `a0bb019a...`、placement `b59661b...` 与原参数。随后完成两层身份检查：

- 原始普通共源 DC 与恢复后 DC：OA 自动 netlist SHA-256 都为 `12d5a8a7d959bf46916ba7625f5e3c3e63cf16892437bb13269b1e90c48393fd`，17 个指标逐项数值完全一致。
- 恢复后的 AC-A 与再次 forward→inverse 后的 AC-B：同一 netlist SHA-256，28 个 DC/AC 指标逐项数值完全一致；gain=`4.588483 V/V`、BW=`6.762110 GHz`、GBW=`31.027825 GHz`、unity=`30.356729 GHz`。

两次运行的 wrapper/原始 PSF 文件字节哈希不同。它们包含每次运行独立的 scratch/产物状态，未进一步证明差异只来自元数据，因此本 Gate 不声明 raw-file byte identity；身份结论严格限于相同语义 testbench、相同 OA→`si` 网表和全部已记录标量指标一致。

## 中断、恢复与资源审计

最初两个 DC 尝试在 sandbox 内下载生成的 `si.env` 时因 DNS/SCP 无法解析 `nics4304-cad1`，均作为 transport `system_event` 保留；checkpoint 为 0 个完成候选，OA 独立回读恢复，不把它计为电路不可行。进入真实网络上下文后从合法 checkpoint 完成 9/9 DC，再完成 9/9 AC。

最终 `vda resources --remote` 只读盘点：

- local transient VDA resources：`0 entries, 0 bytes`。
- remote `si=0`、`spectre=0`。
- remote Maestro session：`0`。
- remote `virtuoso=2` 是本轮前已存在的共享基线；本地进程清单没有 `vda/python/scp`，仅保留两条 15:37 已存在的 SSH tunnel。
- evidence 目录按保留策略不自动删除。

## 证据来源

- OA topology、pin/placement、CDF 和写后/恢复回读：`bridge_readback`。
- `si` 网表、Spectre DC OP、复数 AC 与原始性能指标：`eda_result`。
- topology/placement hash、inverse 判定、seed、constraint 与离散域排序：`software_inference`。
- target、授权、候选边界、规格和 objective：`user_input`。
- DNS/SCP/transport failure、失败动作和恢复状态：`system_event`。

## 本地回归

- `python -m pytest`：`668 passed`。
- `vda catalog`：通过。
- 交接要求的 `inverter-close-loop.demo.json` plan：通过。
- `examples/tasks/*.json`：`195/195` 可 plan。
- `git diff --check`：通过。

## 未闭合边界与下一道 Gate

- 只验证 TSMC N28 nominal `top_tt` 单条件；PVT 是可选加严项，不默认附加。
- 共栅变体尚未跑 noise、相干 transient/THD/P1dB、slew/settling 或更完整的设计质量权衡。
- 当前是受控 contract 中的最小 topology delta，不是从任意自然语言自动综合任意电路。
- 并发人工 editor、任意 shape/annotation、layout、DRC/LVS/PEX 和旧 ADE L 非覆盖迁移仍未闭合。

下一道有用的 Gate 应是固定同一输入条件，比较普通共源与共栅候选的 gain/BW/GBW、摆幅、功耗、noise 和线性度，并让完整质量规格而非单一 gain objective 决定是否保留共栅拓扑；可选 PVT 只在 nominal 质量点成立后追加。
