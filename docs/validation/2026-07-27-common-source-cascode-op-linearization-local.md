# 2026-07-27 共源/共栅 OP 理论线性化本地 Gate

## 结论

VDA 已增加 `vda small-signal-from-run`：从一个成功 real-Bridge action 的结构化
OA→`si` 图和同一次 Spectre DC operating point 自动构造通用 MOS/R/C 节点矩阵。
本轮只读复用既有真实 run record，没有调用 Bridge、SSH、`si` 或 Spectre，没有写 OA，
也没有修改第三方库。

当前状态是：

**same-action operating-point graph linearization locally implemented; common-source and cascode low-frequency gain replay verified; dynamic derivative live capture pending**

## 为什么替代原计划的 A/B quality sweep

普通共源低频增益的一阶关系是：

```text
Av = -gm1 / (gds1 + 1/RD)
```

对 `MN0 -> MNCAS -> RD` 两节点网络，VDA 不使用硬编码的共栅公式，而是由实例
`D/G/S/B` 连接统一 stamp。忽略旧 run 未保存的 `gmb` 时，解析化简等价于：

```text
A2 = gm2 + gds2
Av = -gm1*A2 / (gds1*(1/RD + gds2) + A2/RD)
```

这里所有 `gm/gds/RD` 都来自已有同一 action 的 `eda_result`/`si` 网表，不是手列候选。
结果为：

| 拓扑 | 通用矩阵预测 | 已有 Spectre AC | 相对误差 |
|---|---:|---:|---:|
| 普通共源 | 4.5957436 V/V | 4.5884830 V/V | 0.1582% |
| 共栅级联 | 6.3996495 V/V | 6.4412443 V/V | 0.6458% |

预测增益提升为 `39.2517%`，已有真实 AC 为 `40.3785%`。这个误差水平已经足够证明：
再启动一个完整 noise/linearity A/B bundle 来“发现共栅增益更高”没有决策价值。

## 通用能力边界

policy 只声明：

- 每只 `si` MOS 的 polarity；
- AC 固定边界节点；
- 输入/输出节点线性表达式；
- 可选外部电容与频率网格；
- 期望 PDK/corner/temperature/topology identity。

实现从 run record 自动完成：

1. 精确选择声明的 `simulation.candidate.N`；
2. 要求 action、`si` netlist 和 OP 都是成功 `eda_result`；
3. 核对 PDK、model section、topology、节点 VGS/VDS、一致性与 KCL 标记；
4. 用有效总宽度把 `Id/gm/gds/gmb/capacitance` 规范化为
   `eda_operating_point` artifact；
5. 用既有 topology-independent `Y(f)` core 求解；
6. 将预测与同 action 已有 AC 标量分开记录为
   `software_inference` 和 `eda_result`，并绑定 policy/run/action/netlist SHA-256。

这条能力适用于其他 MOS/R 图，只需改变显式边界和输入/输出表达式；它不是任意层级
flattening、非线性 DC 求解器或 topology synthesis。

## 为下一次 DC 增加的证据面

旧 common-source/cascode wrapper 只请求 `ids/vgs/vds/vdsat/gm/gds`。本轮已把未来
wrapper 的 OP save 扩展为两管各自的：

```text
gmb
signed cgg/cgd/cgs/cgb ... cbb（完整 4x4 dQi/dVj）
cjd, cjs
```

parser 将它们保留在 `evidence.operating_point.device_values`。该改动复用 Bridge 现有
PSFASCII scalar parser，没有修改 Bridge。deck 生成和完整矩阵回读已有本地测试；尚未
进行新的 live capture，所以不能宣称 GHz 带宽预测已由这条新路径验证。

## 证据边界

- 既有 `si` 网表、OP、已有 AC 指标：`eda_result`；
- policy 的输入/输出/边界/PVT 期望：`user_input`；
- OP 规范化、矩阵 stamping、预测、误差和覆盖判定：`software_inference`；
- 本轮没有新的 `bridge_readback` 或 `system_event`。

普通共源中 body/source 同为 VSS，缺失 `gmb` 对该低频式无贡献，因此结果为
`succeeded`。共栅管 source 为 NCAS、bulk 为 VSS，旧 run 缺失 `gmb` 会影响低频模型，
故结果保留为 `partial`，没有靠实测误差小而抹掉缺项。两份旧 run 都缺少动态电荷导数；
产物明确把 bandwidth、GBW、unity、noise、distortion、slew 和 settling 列为未覆盖。

## 本地产物

- policy：`examples/theory/common-source-op-small-signal-policy.json`
- policy：`examples/theory/common-source-cascode-op-small-signal-policy.json`
- result：`artifacts/theory/common-source-op-small-signal-20260727.json`
- result：`artifacts/theory/common-source-cascode-op-small-signal-20260727.json`

## 本地验证

- `python -m pytest`：`674 passed`。
- `vda --help`：包含 `small-signal-from-run`。
- `vda catalog`：通过，并已校正共栅 live Gate 的陈旧说明。
- `examples/tasks/*.json`：`195/195` 可 plan。
- `vda resources`：本地 transient VDA resources 为 `0 entries, 0 bytes`。
- `compileall` 与 `git diff --check`：通过。

## 下一道有用的 Gate

不再默认运行普通共源/共栅的完整 quality A/B。下一次只有在需要评估某个具体新候选时，
才先做一次只读、显式 PVT 的 DC capture，以取得新增 `gmb+dQi/dVj+cjd/cjs`；随后先在
本地预测 gain/BW/GBW。只有预测误差校验失败，或 noise/linearity 确实可能改变设计
objective/约束结论时，才请求相应最小 AC/noise/transient 远端 Gate。可选 PVT 仍不默认
附加。
