# 2026-07-24 TSMC N28 独立 MOS characterization 真实 Gate

## 结论

VDA 已把独立 MOS 器件表征实现为正式且正交的 `device.characterize` operation，
并在 nics4304 的 TSMC N28 `top_tt` 模型上完成真实只读验证。当前状态是：

**standalone TSMC N28 MOS characterization live verified at nominal top_tt; si graph binding and held-out circuit validation pending**

这条路径没有 OA target，不打开或修改 library/cell/view。Bridge 继续负责 SSH、Cadence
环境、Spectre 和文件传输；VDA 只生成有限表征契约、核对原始 OP、归一化器件表、
审计留出点并记录证据，没有修改 `virtuoso-bridge-lite`。

## 授权与副作用

- operation：`device.characterize`；circuit：`mos_device`；
- PDK profile：`nics4304_tsmc28`；模型 section：`top_tt`；温度：27 ℃；
- OA target：不存在；`allow_remote_write=false`；`replace_existing=false`；
- 远端计算：用户明确批准，`allow_remote_compute=true`；
- 最终唯一远端根：
  `/data/xum/virtuoso_bridge_smoke/vda_mos_characterization_mos-device-characterize-top-tt_c29db5086128/`；
- 最终 Spectre 子目录：上述根下 `b04aca87/`；
- 目录在运行前必须不存在；Bridge runner 在根下生成唯一子目录并保留输入、PSF 和日志。

该 task 明确拒绝 OA target、OA 写权限、analysis、参数写入、搜索约束或 ADE 状态。
其他所有既有 operation 仍必须提供 OA target，没有因本 Gate 放松安全边界。

## 表征域与实现

单个 Spectre DC operating-point deck 放置多个相互独立的 MOS 实例，每个实例有自己的
D/G/B 理想电压源，source 接地。NMOS 使用正 VGS/VDS 和负 VBS；PMOS 使用负 VGS/VDS
和正 VBS。这样全部点共享同一个 model include、section、温度和仿真进程，不需要为
每一点启动一次 Spectre。

训练表固定：

```text
model: nch_lvt_mac, pch_lvt_mac
W: 1.0 um, nf=1, m=1
L: 0.03, 0.06 um
|VGS|: 0.35, 0.45, 0.55, 0.65 V
|VDS|: 0.15, 0.30, 0.45, 0.60, 0.75 V
|VSB|: 0, 0.075, 0.15 V
2 polarities x 2 L x 4 VGS x 5 VDS x 3 VSB = 240 points
```

另有四个没有进入训练表的真实 Spectre 留出点：每种 polarity 在 L=0.03/0.06 µm
各一个，VDS/VSB 取表内值，VGS 分别取 0.40/0.60 V，以审计局部 VGS 插值。

每点从 `dcOpInfo.info` 读取并检查：

```text
ids, vgs, vds, vbs, vdsat, gm, gds, gmb
cgs, cgd, cgb, cdb, csb
```

原始 signed OP 属于 `eda_result`。VDA 核对 NMOS/PMOS 的 VGS/VDS/VBS/IDS 方向、
所有量有限且 Id/gm 非零，再生成 width-normalized `Id/W`、`gm/Id`、`gds/Id`、
`gmb/Id` 和五类电容密度；这些归一化值属于 `software_inference`。

## 最终真实证据

最终记录：

`artifacts/runs/mos-device-characterization-gate7a/nominal-final-20260724.json`

| 项目 | 结果 |
| --- | --- |
| run status | `succeeded` |
| Bridge | `0.7.0` |
| Spectre | `21.1.0.612.isr15` |
| 原始 OP 点 | 244 |
| 训练点 | 240（NMOS 120 + PMOS 120） |
| 留出点 | 4 |
| bias/sign consistency | `matched` |
| manifest consistency | `matched` |
| OA access/write | `false / false` |
| deck SHA-256 | `ccb002427053c89602d353c1bbeb13b1a816912db7880fcfe8a6273d8b63c439` |
| manifest SHA-256 | `e9f7c74562b2d80da30615fdac3a0acd6f9928a24c76520e11802e6822201df8` |

六项 manifest 覆盖 90,019-byte deck、254,347-byte `dcOp.dc`、670,295-byte
`dcOpInfo.info`、PSF log/status 和 8,234-byte `spectre.out`；每项都有大小与 SHA-256。
artifact 的 `source_artifact_sha256` 等于该 manifest 指纹，不以 return code 代替数据。

## 留出点误差门

门限在运行前声明为 0.25。误差定义为：

```text
abs(predicted - actual)
----------------------------------------------
max(abs(predicted), abs(actual), metric floor)
```

电容的 floor 为 `1e-17 F/um`，避免亚 attofarad 的近零矩阵项以纯相对误差支配设计
判断；绝对误差、每项 floor、预测值和真实值都保存在 run record，不隐藏近零差异。

| 留出点 | polarity | 最大归一化误差 | 最坏量 | 结果 |
| --- | --- | ---: | --- | --- |
| ho-001 | NMOS | 7.18% | Id/W | pass |
| ho-002 | NMOS | 2.44% | gm/Id | pass |
| ho-003 | PMOS | 13.70% | Id/W | pass |
| ho-004 | PMOS | 4.07% | gm/Id | pass |

因此最坏值 `13.70% < 25%`。这只验证声明表域内的四个局部 VGS 留出点；不是任意
L、任意偏置或完整电路的误差上界。

## 失败记录与调整依据

所有失败都保留，没有改写为电路不可行：

1. `nominal-20260724.json`：错误地把 VDA PDK profile 当成 Bridge 命名连接；默认
   Bridge 隧道实际在线，但同名 daemon 不存在。实现改为像其他 adapter 一样复用默认
   Bridge 连接，同时独立记录 PDK profile。
2. `nominal-retry1-20260724.json`：沙箱 DNS 无法解析 `nics4304-cad1`，属于 transport
   `system_event`；在获批范围内从沙箱外重跑。
3. `nominal-retry2-20260724.json`：四个 L=45 nm 留出实例被该 PDK 报
   `CMI-2049 l should be greater than zero`。没有忽略模型错误；留出 L 改为已真实使用的
   30/60 nm，后续任意新 L 都必须先确认 foundry 合法几何。
4. `nominal-retry3-20260724.json`：96 点粗网格完成，但 3/4 留出点失败。主要是弱反型
   `gds/Id` 的稀疏偏置插值和近零 `Cdb` 的纯相对误差。未提高 25% 门；改为加密
   VDS/VSB 训练轴，并显式使用有记录的 mixed normalization floor。
5. `nominal-retry4-20260724.json`：240+4 点首次通过；最终再运行一次，把从日志提取的
   Spectre 版本写入自足记录。

## 与通用小信号核心的接口

`device.characterize.validate` action 的 `details.artifact` 已是现有
`MosCharacterizationArtifact`，包含真实 source/hash/profile/corner/temperature 和 240
个归一化点。用其中 `tr-0-0-2-2-0`（NMOS、L=30 nm、VGS=0.55 V、VDS=0.45 V、
VSB=0）直接构造一个通用 MOS+20 kΩ 共源节点图，现有 kernel 成功得到：

```text
gm = 1.443845 mS
gds = 0.203741 mS
low-frequency gain = 15.1026 dB
derived -3 dB bandwidth = 166.747 GHz
```

该接口 smoke 保持 raw source=`eda_result`、normalized point 和网络指标=
`software_inference`。增益/带宽只是该简化节点图的理论输出，不是某个 OA 完整电路的
Spectre 结果，也没有用于 OA 写回。

## 测试

新增测试覆盖：无 OA target、其他 operation 仍要求 target、compute 单独授权、禁止
remote write、stable plan、subprocess payload、NMOS/PMOS deck 偏置、manifest/hash、
空点、NaN、PMOS IDS 符号、偏置漂移、留出点 pass/partial、transport interruption、
Spectre 版本提取，以及归一化 artifact 进入通用 small-signal kernel。

最终全套测试结果见仓库提交前验证；本 Gate 不以 live smoke 代替本地回归。

## 未闭合边界与下一 Gate

1. 只有 nominal `top_tt`/27 ℃；TT/SS/FF 与温度扩展是可选后续，不默认增加成本。
2. 只有 W=1 µm、nf=1、m=1 和两种 L；窄宽效应、finger、multiplicity、扩散共享、
   mismatch 和 layout-dependent effect 均未表征。
3. 当前留出只审计表内 L 的局部 VGS 插值。VDS/VSB、跨合法 L、边缘/外推误差仍未
   完整量化；任何外推都应拒绝。
4. artifact 嵌在 run record 的 validation action 中；下游可直接读取该 JSON 对象，
   尚无 artifact registry/database，也不需要为此引入数据库。
5. 尚未把 OA/`si` 结构网表自动映射成通用 MOS/R/C 网络，也没有用完全留出的电路
   topology 对比理论 AC 与同源 Spectre。
6. Spectre 仍是最终规格证据；本 Gate 不授权理论结果直接写回 OA。

下一道 Gate 是 Gate 7B：从现有只读 OA→`si` 路径提取一个已验证共源 cell 的
MOS/R/C 图和真实 DC 偏置，在表域内选择或插值器件点，生成通用 small-signal 预测，
再用同一 `si` 网表的 Spectre AC 对比 gain、phase、−3 dB bandwidth 和 GBW。先以未参与
器件建表的 nominal 共源为完整 held-out topology，通过后再迁移到源极退化和差分对。
PVT 仍是可选扩展，不是 Gate 7B 的默认前置条件。

## 后续状态

Gate 7B 已在同日完成首个 nominal 共源 held-out 验证。该过程发现 W/L 相同仍不足以
代表同一 OA 器件：扩散几何和 LDE 参数必须进入 characterization identity。VDA 已加入
受限的逐 polarity Spectre 参数签名，并用匹配 `si` MN0 的 31 项参数表通过固定 DC/AC
误差门。Gate 7A 的通用 W=1 µm 表和本页边界仍保留，不追溯包装成 exact-geometry
电路证据。详见
[Gate 7B 共源小信号真实验证](2026-07-24-common-source-small-signal-validation-live.md)。
