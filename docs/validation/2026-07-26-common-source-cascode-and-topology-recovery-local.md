# 2026-07-26 共栅微调与 topology recovery 本地 Gate

## 结论

本轮把两项此前仅列为缺口的能力实现到 VDA 本地边界：

1. 通用 topology-delta 可对逻辑/物理 pin 做 exact add/remove，并以完整实例、pin、label、wire 几何生成独立 placement 指纹；保存后审计失败只在新鲜结构精确匹配时执行预声明 inverse。
2. 既有共源级可用一个最小 delta 原位增加共栅管和 `VCAS` pin；同一个 common-source worker 随后解析 OA→`si` 的两管网表，先跑 DC 工作区/KCL，再跑复数 AC gain、带宽、GBW 和 unity-gain。

当前状态是 **local implementation verified; live OA/si/Spectre Gate pending**。本记录不把模拟 Bridge、plan 成功或单元测试结果表述为真实电路性能证据。

## 通用 pin、placement 与恢复边界

新增/收紧的行为：

- custom read-only SKILL 将每个 OA logical terminal/net 唯一绑定到实际 pin figure，并回读 pin-symbol master、坐标和方向；无法唯一绑定时拒绝。
- `add_pin` 复用 Bridge 公共 `schematic_create_pin`；`remove_pin` 对 name/net/direction/master/xy/orient 做 exact CAS，删除完整 terminal/pin figure 层级，而不是遗留无 figure 的 terminal。
- topology SHA 继续只描述结构图；placement SHA 独立保存规范化的完整 instances、physical pins、labels 和 wire point lists。
- task 可声明 `expected_output_placement_sha256`，该值进入 plan token；保存后 topology、完整实例参数或 placement 任一不符都视为审计失败。
- 自动恢复前重新读取 topology、geometry、placement。只有当前 topology 精确等于本次预期输出时才允许 inverse；geometry/readback transport loss 记为 `state_unknown_no_write`，额外结构记为 `unexpected_topology_no_write`。
- inverse 后必须同时证明初始 topology SHA、全部实例参数表和初始 placement SHA 恢复。原请求仍返回失败；恢复成功不会把原操作改写成成功。

本地故障注入覆盖：保存后参数漂移、placement mismatch、geometry transport loss、额外 topology、master CDF 恢复和 plan-token 绑定。

用于 live 故障 Gate 的任务为：

- `vb_pdk_smoke/vda_master_recovery_001/schematic`
- forward：`MN0 nch_lvt_mac -> nch_mac`
- 声明迁移：`Wfg=1u, l=30n, m=1 -> m=2`
- 当前 PDK 的历史 live 证据显示 callback 会把 `m=2` 归一回 `1`，因此预期在 topology 已保存后触发参数审计失败，再自动 exact inverse。
- 如果 `m=2` 在本次环境意外持久化，不能把它当成故障注入成功；必须立即执行显式 manual inverse 任务并独立 inspect。

## 共源到共栅的最小 delta

前向 contract 只做五类必要变化：

1. 新增 `NCAS`、`VCAS` net。
2. `MN0.D: OUT -> NCAS`。
3. 新增 `MNCAS(OUT,VCAS,NCAS,VSS)`，master 为当前 profile 的 `tsmcN28/nch_lvt_mac/symbol`。
4. 新增 `VCAS` input pin，并绑定 `basic/ipin/symbol`、坐标和方向。
5. 保留 MN0/RD0、原有 pins、未声明参数和所有其他几何。

inverse 由 contract 自动生成并已在本地证明恢复原 topology fingerprint。示例：

- `examples/topology/common-source-add-cascode.operations.json`
- `examples/topology/common-source-add-cascode.contract.json`
- `examples/tasks/common-source-cascode-forward.bridge.json`
- `examples/tasks/common-source-cascode-inverse.bridge.json`

目标 live cell 为 `vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`，`replace_existing=false`；若同名对象已存在，create 必须拒绝而不是覆盖。

## 同源 DC 与 AC

共栅变体没有复制 Bridge、netlister 或 executor：

- OA reader 返回 `topology_variant=cascode_common_source`、两只 MOS 和 RD 的语义参数。
- `si` parser 要求 `MN0(NCAS,IN,VSS,VSS)`、`MNCAS(OUT,VCAS,NCAS,VSS)`、`RD0(VDD,OUT)`，并逐项核对 model、W/L/R 与 OA readback；混入源极电阻或额外 MOS 会拒绝。
- DC wrapper 保存 `IN/OUT/VDD/VSS/VCAS/NCAS`、VDD source current，以及 MN0/MNCAS 的 `ids/vgs/vds/vdsat/gm/gds`。
- DC 完整性门检查两管节点关系、MN0↔MNCAS、MNCAS↔RD、RD↔VDD source 的电流一致性、两管饱和余量与 stack headroom。
- AC wrapper 只有 VIN 为 unit AC，VCAS 为纯 DC；沿用现有复数 `H(f)`、低频参考平坦性、首个向下 −3 dB 交点、GBW 和 unity-gain 规则。
- 空、非有限、长度不一致或没有 −3 dB 交点的波形不能产生完整候选。

worker 的模拟 Bridge 测试覆盖完整 OA inspect、`si` parser、DC OP 和复数 AC 流程；这仍是软件测试，不是 `eda_result`。

## 理论先导候选

`vda cascode-seed` 不接受人工随手列出的三个值。它要求 hash-bound 的成功 real-Bridge 共源 DC run，并验证：

- exact task/candidate/target/PDK；
- OA `bridge_readback` 与 `Cadence si -batch` 网表参数一致；
- Spectre `VGS`、`VDSAT`、`Id` 为 `eda_result`；
- source topology 仍是普通共源，固定 W/L/RD/bias/VDD 未漂移。

当前一阶 seed 关系为：

```text
VTH_est = |VGS| - |VDSAT|
V_NCAS,target = |VDSAT| + declared lower-device saturation margin
VOV_cas = |VDSAT| * sqrt((Wsource * Lcas) / (Wcas * Lsource))
VCAS = V_NCAS,target + VTH_est + VOV_cas + declared offset
```

policy 显式给出 width ratios、bias offsets、length ratio、网格和上下界。量化后重合、越界或候选数不符都会拒绝。输出是完整原子 `(Wcas,Lcas,VCAS)` tuple，并固定 `continuous_optimum_claim=false`、`global_optimum_claim=false`。

`vda candidate-task-from-cascode-seed` 将同一 candidate set 原样编译进 DC/AC task template。DC 必须先通过；AC 不会自行换一组更好看的点。最终选优仍只由 Spectre 指标决定，seed 排名不能覆盖 EDA。

## 本地验证

已完成：

- topology contract 与两个 task 内嵌 contract 字节语义一致。
- 全部新 JSON 示例可解析；共栅/恢复的十个具体任务均能生成带副作用边界的稳定 plan。
- `compileall` 与 `git diff --check` 通过。
- pin/placement/recovery、共栅 worker、theory seed、task compiler、CLI、planner、executor 的定向 pytest 通过。

- 全量 `python -m pytest`：`664 passed`。
- `vda catalog` 与交接要求的反相器 demo plan 通过；`examples/tasks/*.json` 为 `194/194` 可 plan。

## 证据边界

- OA topology、CDF、pin/geometry readback：`bridge_readback`。
- `si` 网表、Spectre OP/复数波形和原始指标：`eda_result`。
- contract/inverse/placement hash、理论公式、规格判断和候选排序：`software_inference`。
- target、参数边界、饱和余量、候选宽比/offset 和授权：`user_input`。
- transport reset、timeout、恢复状态：`system_event`。

return code 0、OA 对象存在、plan 成功、理论排名最高或某项指标最大，均不单独构成设计闭合。

## live Gate 顺序与未验证边界

取得精确授权后按以下顺序连续执行：

1. 在 `vda_master_recovery_001` 新建基线、独立 inspect，触发 post-save 参数审计失败；要求自动 inverse 和再次独立 inspect 都恢复 topology/placement/完整参数。异常时只执行预先准备的显式 inverse，不重放不确定 payload。
2. 在 `vda_cs_cascode_gate_001` 新建基线并只读运行普通共源 DC OP，得到理论 seed 的真实来源。
3. forward 共栅 delta，独立 inspect；必要时按实际 after placement hash 做 forward→inverse→forward 的确定性 replay。
4. 对 MNCAS W/L 做显式 OA apply/readback；用真实 seed OP 生成 9 个原子候选。
5. 先运行 9 点 DC/checkpoint；只有工作区/KCL 完整的点才进入结论。
6. 对完全相同的 9 点运行 DC+AC，提取 gain/BW/GBW/unity，并按声明规格/objective 选优与写回。
7. exact inverse 恢复普通共源 topology/placement/参数，并复跑基线 DC/AC 身份检查。
8. 用 `vda resources --remote` 和本地进程清单审计 worker、SSH/SCP、si、Spectre 和 Maestro session；不自动删除保留 evidence。

live 前尚未验证：真实 `basic/ipin` 创建/删除几何、真实 wire/label after placement 的确定性、post-save 自动 recovery、MNCAS 的 PDK callback/`si` 端子顺序、两管 DC 工作区、AC 是否在声明 sweep 内出现有效 −3 dB 交点、9 点中的可行比例、最佳点、transport resume 和最终 exact inverse。跨 PVT 是可选后续 Gate，不作为本轮默认成本。
