# 2026-07-26 差分对工作点重线性化六点真实 Gate

## 结论

本 Gate 把 Gate 8 真实 TSMC N28 电流镜负载差分对记录拟合出的 6 个原子
`Wn/Wp/Wtail` 候选送回既有 OA，逐点完成：

```text
OA 参数暂存与回读
  -> si -batch 自动网表
  -> Spectre 双支路 DC
  -> 差模 AC
  -> 共模 AC 与 CMRR
  -> 完整规格判断
  -> EDA 选优
  -> 最佳参数写回与独立 OA 回读
```

6/6 候选都完成且可行。局部模型与真实 EDA 都选择最小功耗候选
`op-local-002`：

```text
Wn(MN0/MN1)       = 1.215 µm
Wp(MP0/MP1)       = 1.080 µm
Wtail(MNTAIL)     = 0.555 µm
L(all MOS)        = 0.030 µm
```

该点的 Spectre 结果为功耗 `10.2971 µW`、差模低频增益 `3.71568 V/V`、
−3 dB 带宽 `2.68269 GHz`、GBW `9.96800 GHz`、低频 CMRR `34.8249 dB`。
exact validator 对 6 点 × 15 指标的 `90/90` 比较全部通过，五个新点共 `75/75`
通过，最坏误差是候选 2 的带宽 `2.5693% < 20%`。因此当前可以称为：

> **differential-pair held-out local-response shortlist to same-source EDA selection verified at nominal top_tt**

这仍是已声明离散域中的最佳点，不是连续或全局最优，也不是完整差分放大器质量闭环。

## 目标、授权与预检

- target：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`
- profile：`nics4304_tsmc28`，model section `top_tt`
- OA write：逐候选暂存五只 MOS 的宽度，成功时保留最佳点；失败或全不可行时恢复预检 anchor
- remote compute：每候选运行同源 `si`、双支路 DC、差模 AC、共模 AC/CMRR
- scratch：
  `/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-op-relinearized-next_<12hex>/`
- `replace_existing=false`；不删除、重建或替换 cellview
- 未授权且未运行：transient、noise、PSRR、PVT、mismatch/Monte Carlo
- plan token：`b7660e1c2551fd75`
- task SHA-256：
  `85a4e9e704a56847340fe2f1e7cd3635078fa3fbcd9cd8f49ed9dd64e2e92e96`
- relinearization result SHA-256：
  `f6728aada557bcae242dc889db9af5da6accff98b411aca214ba3964c95caf39`
- policy canonical SHA-256：
  `0b6f328342934097236048dbda4375c7fb77852cdb49f3bc2b242eb2f5040534`
- 第三方 Bridge：`codex/vda-transport-recovery@e74379a`，工作树干净；本轮未修改

独立只读 preflight 回读到 Gate 8 anchor：

```text
MN0/MN1   Wfg=1.315u  L=30n
MP0/MP1   Wfg=1.180u  L=30n
MNTAIL    Wfg=0.605u  L=30n
```

实例、连接和 pins 均匹配五管电流镜负载拓扑；preflight inspect SHA-256 为
`cba75c3b025ff4efe018be61a16aaece85187e5511babb735c7db89c6ad5c68a`。

## Transport 故障与恢复

真实执行遇到三次外部传输故障，均记录为 `system_event`，没有包装成电路不可行：

| 阶段 | 故障 | checkpoint 状态 | 恢复证据 |
|---|---|---|---|
| 候选 1 | 下载 `si.env` 时 DNS/SCP 无法解析 `nics4304-cad1` | `next=1`，0 个候选，pending=null | 恢复并独立回读 anchor，再重跑候选 1 |
| 候选 2 | `si -batch` 连接发生 `WinError 10054` | `next=2`，1 个候选，pending=null | 恢复并独立回读 anchor，从候选 2 续跑 |
| 候选 3 | `si -batch` 再次发生 `WinError 10054` | `next=3`，2 个候选，pending=null | 恢复并独立回读 anchor，从候选 3 续跑 |

两个不确定的 `si` scratch 被保留用于审计：

```text
/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-op-relinearized-next_31741fc759eb
/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-op-relinearized-next_c918a292850e
```

恢复没有重放已完成候选。最终 checkpoint 为 `complete=true`、`next=7`、6 个候选、
`pending_oa_parameters=null`，SHA-256 为
`a962ee98fcfc50cf33a7d66d9fea52cdcd99a09e258005cd3a7f9b56252af34c`。

最终 run：
`artifacts/runs/differential-pair-op-relinearized-next/live-resume3-20260726.json`，
SHA-256 为
`cd0d211256157eed41117a022ebe2a85cdc7f431a24f7fe238f3f60e2f09acfe`。

## 六点真实结果

固定条件为 `L=30 nm`、VCM=`0.55 V`、VDD=`0.9 V`、BIAS=`0.32 V`、
OUTN load=`0.5 fF`。任务要求全部 signal MOS 饱和、负载电流失配不超过 1%、输出摆幅
余量至少 0.1 V、功耗不超过 25 µW、增益至少 3、BW 至少 2.5 GHz、GBW 至少
9 GHz、peaking 不超过 3 dB、低频 CMRR 至少 30 dB；objective 为最小功耗。

| # | ID | Wn/Wp/Wtail (µm) | Power (µW) | Gain | BW (GHz) | GBW (GHz) | CMRR (dB) | CMRR BW (GHz) | Load mismatch (%) | Swing (V) |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | op-local-001 | 1.315/1.180/0.605 | 10.9810 | 3.72772 | 2.68951 | 10.02575 | 34.8150 | 2.21186 | 0.001340 | 0.212009 |
| 2 | op-local-002 | 1.215/1.080/0.555 | **10.2971** | 3.71568 | 2.68269 | 9.96800 | 34.8249 | 2.25072 | 0.001345 | 0.212835 |
| 3 | op-local-003 | 1.215/1.180/0.555 | 10.3028 | 3.69883 | 2.59830 | 9.61067 | 34.8733 | 2.15376 | 0.000792 | 0.209616 |
| 4 | op-local-004 | 1.315/1.080/0.555 | 10.3507 | 3.73784 | 2.63665 | 9.85538 | 34.9027 | 2.20008 | 0.004317 | 0.213030 |
| 5 | op-local-005 | 1.315/1.180/0.555 | 10.3564 | 3.72101 | 2.55740 | 9.51611 | 34.9515 | 2.10921 | 0.003222 | 0.209811 |
| 6 | op-local-006 | 1.415/1.080/0.555 | 10.3999 | 3.75760 | 2.59225 | 9.74065 | 34.9725 | 2.15063 | 0.008975 | 0.213208 |

相对候选 1/搜索前 anchor，最佳点功耗下降 `6.2279%`；代价是 gain、BW 和 GBW
分别下降 `0.3230%`、`0.2538%` 和 `0.5760%`。CMRR 增加 `0.00993 dB`，摆幅余量
增加 `0.3897%`。这些是本任务最小功耗 objective 下的显式权衡；若应用更重视 GBW，
候选 1 仍可能更合适。

## OA、网表与 analysis 一致性

六个候选均为 `parameter_consistency=matched`、`analysis_complete=true`，每点包含 71 个
`eda_result` 指标和 19 个 `software_inference` 指标。所有点都运行 Spectre
`21.1.0.612.isr15`，并保留不同的自动 `si` netlist SHA-256：

| # | remote suffix | netlist SHA-256 | differential AC SHA-256 |
|---:|---|---|---|
| 1 | `d960af077e78` | `5ed747565bff138be935252af874cb5ef86e5265a26d44acd714b0de89f80b0f` | `d9def9ad3cb4bbe326a3495107a39496882c600e0112ac283bca974e123456c2` |
| 2 | `a8df4ea88c1a` | `5628c8236485706325bf24df56e9fb8d0fc965b5b8d773795e2e1a123e3e7474` | `5c031eb935e8422f8ab4da0d267841893f5f0ab985f10c4b7f278cce271a23e5` |
| 3 | `e036ebe2dd20` | `25311a3934c531d5a8b2013f90ed2ca7003617e85119eefdf66c4fbfe02a9938` | `e359ca414ebd6ab918f7126d2bbef3b3cdc93f6e676df10a8f1cea4fd44ffae1` |
| 4 | `2494eb88e5a9` | `edff959c9eccf760de181c74810cf091e71b2e541baf72baab24de643a0533ba` | `ebb55617ce7206db0301a606b22f55183494e8863ce949cf77b853aaeeb78654` |
| 5 | `3910b3cb8ab9` | `c31d429f1c75ce8690cc2e3726239e649ad0972ff2644c8d87c6ec7d033cde17` | `52ec814a28a5e45b151e3d5329aab2f71499aa20674e031184571cfcaadaa626` |
| 6 | `c063303e2c4c` | `b149f8cdedbf43cfc632af3af8f018e824805aadb76621d2db6355fcff8ca782` | `64525ed72550cf592b241a1841236eefa5011dd88ae36d5a7876c6e799345e53` |

`parameters.apply.best` 后的独立 `schematic.inspect` 回读 MN0/MN1=`1.215u`、
MP0/MP1=`1.08u`、MNTAIL=`555n`，五只器件 L 都为 `30n`；实例、nets 和 pins 未改变。
该 inspect SHA-256 为
`42e3a56f7cd3e35215b9649dcadedfd4ea057674a93d783a3e905f57fed80f14`。

## Exact validator 与旧结果兼容修复

正确的只读审计命令为：

```powershell
.\.venv\Scripts\vda.exe op-relinearization-validate `
  artifacts\relinearization\differential-pair-op-relinearization.json `
  artifacts\relinearization\differential-pair-op-relinearized-task.json `
  artifacts\runs\differential-pair-op-relinearized-next\live-resume3-20260726.json `
  --policy examples\theory\differential-pair-op-relinearization-policy.json `
  --output artifacts\relinearization\differential-pair-op-relinearization-live-validation-bound-policy.json
```

这份 relinearization result 生成于 validator 保存 `relative_error_floor` 之前。首次审计按
schema 默认的 `1e-30` 错误归一化 floor 读取缺失字段，导致接近零的 load mismatch 被夸大
成最高约 83% 的相对误差。该诊断 artifact 保留为
`differential-pair-op-relinearization-live-validation.json`，SHA-256 为
`f5da4d803116655fd008d568c492459ac8e9e5cd7bc735093dfdb5bc630a20d5`；它不是电路失败。

修复没有事后放宽门限：原始 policy 在本次运行前已声明 mismatch floor=`1.0%`，且其
canonical SHA 已由 result/task 绑定。现在旧 result 缺少序列化 floor 时必须显式提供该
exact policy；canonical hash、policy/source identity、constraints、objective、metric set、
role、response scale 和误差门任一不匹配都会拒绝。新 result 自带 floor 时仍直接使用
`result_model`，不要求额外 policy。

最终验证 artifact SHA-256 为
`3110368550c05042639cb249726aef9c7353c2b9f90d5d3a926829dacefe8a59`：

- candidate execution：passed，6/6 完整，6/6 EDA feasible
- recommendation agreement：passed，预测和 EDA 都选 `op-local-002`
- prediction accuracy：passed，90/90；五个新点 75/75
- 最坏新点误差：candidate 2 differential BW `2.5693% < 20%`
- 最大 mismatch 归一化误差：`0.7449% < 20%`
- overall：`succeeded`，命令返回 0

## 证据分类与资源收尾

- `eda_result`：Spectre DC/差模 AC/共模 AC 原始量及从相应波形/OP 提取的性能指标
- `bridge_readback`：OA 实例/网络/pin、逐点参数写回、失败恢复和最终独立回读
- `software_inference`：局部预测、约束判断、离散域排序、误差审计和相对变化
- `system_event`：DNS/SCP 与两次 `WinError 10054`
- `user_input`：任务规格、本轮授权与安全边界

只读资源盘点前后为：

```text
local VDA transient processes: 0 -> 0
remote Spectre:              0 -> 0
remote si:                   0 -> 0
remote Virtuoso:             2 -> 2  (既有 Cadence/Bridge 基线)
VDA-managed Maestro:         0 -> 0
remote evidence dirs:      470 -> 479
```

新增目录是 6 个成功候选和 3 个失败审计目录，不是仍在运行的进程。本轮未执行删除；
post inventory SHA-256 为
`5ec6dcb73bcf3899b581da0c638cc1f06d645ec0cfb2b4a36aeca95b2fe85d82`。

## 本地回归

- 新增 validator/CLI 定向测试：`16 passed`
- Python 全量测试：`607 passed`
- `examples/tasks/*.json`：`155/155` plan passed
- `compileall`：通过
- `vda catalog`：通过，并报告
  `Gate 10 held-out Wn/Wp/Wtail local-response EDA validation verified at nominal TSMC N28`
- `inverter-close-loop.demo.json`：plan 通过，token=`d952f73ecaa92e3b`
- 差分对 exact validator：返回 0
- 旧共源三维 validator + hash-bound policy：按既有 output-swing 结论返回 1/`partial`
- 现代共源 W/RD refresh validator：返回 0
- `git diff --check`：通过

## 未闭合边界与下一 Gate

- 本轮只覆盖 nominal `top_tt` 的 DC、差模 AC、共模 AC/CMRR。没有运行 transient、
  noise、PSRR、PVT 或 mismatch/Monte Carlo。
- common-mode standalone response 在 sweep stop 前没有首次 −3 dB crossing；该指标明确
  记为 warning 且不用于 acceptance。低频 common-mode reference、完整 CMRR 频率响应和
  CMRR 带宽均已解析，不能把前者包装成 standalone common-mode BW 已测得。
- Gate 8 在旧 anchor 上的 PSRR/noise/linearity/ICMR 结果不能自动迁移到新宽度。
- 临时 20 dB PSRR 门仍未闭合；它也不是产品规格。
- 原子 semantic tuple 已真实通过；semantic+raw CDF 混合 tuple、全不可行的新来源组合
  和跨 PVT 设计写回仍没有这次 live 证据。

下一道有用的增量 Gate 是对新最佳点做**只读质量回归**：复用同一 OA/`si` 路径重新运行
PSRR、noise、transient/P1dB 和 ICMR，确认减小三组宽度没有让旧质量证据失效。PVT 保持
显式可选，不默认附加。该 follow-up 需要另行列出精确远端计算范围；本次授权没有覆盖它。
