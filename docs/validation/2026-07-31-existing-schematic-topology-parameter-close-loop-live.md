# 2026-07-31 existing-schematic 拓扑与参数联合闭环 live Gate

## 结论

在用户明确授权的全新 TSMC N28 cellview 上，`existing_schematic design.close_loop` 已真实
完成一次受控的 topology + raw-instance parameter 联合闭环：同一组两个
`(MN0.Wfg, RD0.r)` 原子 tuple 分别在普通共源和新增 `RS0=750 ohm` 的源极退化变体上运行，
四点均由目标 OA schematic 自动 `si -batch` 网表化并进入 Spectre AC。显式 GBW objective
选择普通共源的第二个 tuple，executor 对 alternative 做 exact inverse，再把完整胜出参数写回
基线；任务外独立 inspect 与最终 topology/参数一致。

当前状态可称为 **single-delta existing-schematic topology-and-parameter same-source close-loop
live verified at nominal top_tt**。这证明了一条面向已有 schematic 的通用局部细化能力，不是
该共源电路的连续/全局最优，也不是 L5B 单模块质量闭环。

## 授权与范围

- target：`vb_pdk_smoke/vda_existing_close_loop_gate_001/schematic`；
- PDK profile：`nics4304_tsmc28`；
- 新建 cellview：是，`replace_existing=false`，未覆盖既有对象；
- OA 写入：是；远端计算：是；
- analysis：nominal `top_tt` AC，`VDD=0.9 V`、`VIN_DC=0.35 V`、`CL=2 fF`；
- sweep：`1 kHz .. 1 THz`，30 points/decade；
- 远端 scratch：
  `/data/xum/virtuoso_bridge_smoke/vda_existing-topology-parameter-close-loop-live_*`；
- create plan token：`31b2a2fc08e28143`；
- close-loop plan token：`4ac167b456ae3949`。

任务只比较用户/OP 路径预先声明的两个 tuple 和一份局部 delta；controller 没有随机生成候选，
也没有扩展为大范围拓扑搜索。

## 新鲜拓扑契约

create 后没有沿用模板中的旧 topology hash。先用独立只读
`existing_schematic schematic.inspect` 获取实际 canonical OA graph，再由
`vda topology-compile` 编译 source-degeneration operations。最终 exact contract 为：

- baseline SHA-256：
  `a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41`；
- alternative SHA-256：
  `f441b938846c366b89b28da1c6866db11afaf816d522479b293ba5d62187c9c6`；
- forward：新增 `NSRC`，`MN0.S: VSS -> NSRC`，新增
  `RS0(NSRC,VSS)`；
- inverse：删除 exact `RS0`，`MN0.S: NSRC -> VSS`，删除 exact `NSRC`。

任何 before/after SHA 漂移都会在参数写入前拒绝；这次没有用“相似结构”替代 exact contract。

## 四点真实结果

约束为 `0.1 <= output_dc_v <= 0.8 V`、gain `>= 2 V/V`、BW `>= 1 GHz`；objective
为最大化 GBW。四点均可行且 analysis complete：

| 点 | topology | `Wfg` | `RD` | `RS` | `VOUT_DC` | gain | BW | GBW |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | common-source | 1.05 µm | 19 kΩ | — | 0.269988 V | 4.587904 | 7.024998 GHz | 32.230013 GHz |
| 2 | common-source | 1.10 µm | 18.5 kΩ | — | 0.264448 V | 4.582927 | 7.224356 GHz | 33.108697 GHz |
| 3 | source-degenerated | 1.05 µm | 19 kΩ | 750 Ω | 0.381280 V | 3.886382 | 5.321878 GHz | 20.682848 GHz |
| 4 | source-degenerated | 1.10 µm | 18.5 kΩ | 750 Ω | 0.379443 V | 3.879611 | 5.412288 GHz | 20.997569 GHz |

胜出点为 2。与使用同一 W/RD tuple 的点 4 相比，本次固定条件下普通共源的 gain、BW、GBW
分别高 `18.13%`、`33.48%`、`57.68%`。这只说明固定 `RS=750 ohm`、两个 tuple、nominal
偏置/负载下的 objective 结果；不能外推成“源极退化一般更差”，也不能称为连续最优。

## 同源和证据一致性

四个 candidate action 均满足：

- topology consistency：`matched`；
- OA-CDF -> `si` parameter consistency：`matched`；
- generator：`Cadence si -batch`；
- AC sample count：271，低频 reference window 均为 `flat`，带宽和 unity crossing 均唯一且
  resolved；
- Spectre artifact manifest：complete；
- process guard：`installed_and_hash_matched`；
- OA access：是；candidate simulation 内 OA write：否；remote compute：是。

四份自动网表 SHA-256 依次为：

1. `dbcac626125421c292f75412a3b2cd3b87b02477e2f1819ccaf1cb9717a3227b`；
2. `8a34e7c5f28c9ec608082bff95cb4f1198be571fbc1e91cbb366246380bb3bae`；
3. `7ed1744ba2bcec187a89c19e7f604a55a019cb7fc3d71c3a66ca92738d845074`；
4. `fcbbe76a51176ef01c3f6d625e568596611e56424a7e24252aa38a5eb1c5b05f`。

alternative 网表明确包含 `MN0(OUT,IN,NSRC,VSS)`、`RD0(VDD,OUT)` 和
`RS0(NSRC,VSS)`，并逐项绑定 `MN0.Wfg/l/nf/simM`、`RD0.r`、`RS0.r`。因此本次结论来自
真实 `eda_result`，不是仅凭 OA 对象存在、命令返回 0 或共享参数推断。

## 真实中断与 checkpoint resume

首次执行在完成 baseline candidate 1、2 后，于
`parameters.restore.before-topology` 遇到 `WinError 10054`。失败被记录为 `system_event`，
controller 没有把它解释为不可行电路；recovery 独立 inspect 后恢复并验证 exact baseline。

checkpoint 当时保留两个 completed candidates、`next_candidate_index=3`、baseline topology
SHA 且无 pending transition。恢复 tunnel 后使用同一 task/token `--resume`：

- 没有重跑 candidate 1、2；
- 只执行 forward delta、candidate 3、4、inverse、winner writeback 和 final inspect；
- 最终 checkpoint 为 complete，`next_candidate_index=5`，候选 `4/4`、topology `2/2`、
  `domain_exhausted=true`。

首次运行约 201 秒；resume 的首个新 action 到 final action 约 159 秒。该实测验证的是“已完成
前缀不重算 + exact topology 恢复”，不是证明 transport reset 已根除。

## 最终 OA 状态

任务外只读 inspect 确认：

- topology SHA 恢复为
  `a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41`；
- instances：`MN0`、`RD0`；不存在 `RS0`；
- nets/pins：`IN/OUT/VDD/VSS`；不存在 `NSRC`；
- `MN0.Wfg=1.1u`、`MN0.l=30n`、`RD0.r=18.5K`。

这与 selected topology/parameters 一致，证明胜出点已原子落回真实 OA，而非仅停留在 run
record。

## 资源生命周期复核

`vda resources --remote` 在任务结束后得到：

- remote `spectre=0`、`si=0`；
- VDA-managed Maestro sessions：0；
- local transient entries/cancel markers：0/0；
- remote `virtuoso=2`。Bridge 文档明确 `stop` 只停止 SSH tunnel，而 SKILL daemon 依附用户
  已有 Virtuoso CIW；未证明归属 VDA，故没有终止这两个用户级 Virtuoso 进程。

复核还明确区分了两类本机进程：`vda bridge start` 有意保留一对共享 jump/tunnel
`ssh.exe`；单次 worker 不应留下自己的 helper descendants。资源盘点前后共享 SSH PID 未
变化；`vda bridge stop` 后 `ssh.exe=0`，VDA/Bridge Python worker=0。VDA 的 Windows worker
边界同时补强为：worker 完成注册资源的 `close()` 并输出结果后，仍清空仅属于该 worker 的
Job Object 后代，再释放 handle。第三方 Bridge 仓库没有修改，当前隔离分支工作树保持干净。

## 证据分层

- OA topology/CDF 写入和独立回读：`bridge_readback`；
- `si` netlist、Spectre DC/OP/AC、波形和 manifest：`eda_result`；
- context binding、topology/parameter consistency、constraints/objective/selection、checkpoint
  与恢复判断：`software_inference`；
- target、delta envelope、候选、规格和授权：`user_input`，候选 shortlist provenance 为带
  hash 的 `software_inference`；
- transport reset：`system_event`。

## 保留边界与下一步

本 Gate 尚未覆盖：

- controller 的真实全域不可行路径；该路径已有本地状态机测试；
- controller 内 topology save 后的故障注入；底层 generic delta 已有独立 live recovery Gate；
- hierarchy、派生 CDF、tran/noise/PVT、多 alternative 和并发人工 editor；
- 更完整的单模块质量指标及跨 analysis 的分阶段淘汰。

下一项更有价值的产品工作不是在这个四点域继续随机加点，而是把 generic
`existing_schematic` 从 nominal DC/AC 扩到可声明的分阶段 multi-analysis refinement：先用
DC/OP 否决工作区失败，再在幸存 topology/tuple 上做 AC objective，按任务需要只对最终少数点
追加 noise/transient/PVT。这样同一 controller 才能迁移到用户给出的其他单模块拓扑，并控制
真实设计时间；它仍需独立契约和 live Gate，不能由本次 AC 结果外推。

## 最终本地回归

```text
773 passed in 4.45s
206/206 example task plans passed
catalog succeeded
git diff --check passed
```

## 产物

- create：`artifacts/runs/existing-topology-parameter-close-loop/create-live-20260731.json`；
- fresh inspect：`.../inspect-after-create-20260731.json`；
- compiled delta：`.../compiled-source-degeneration-20260731.json`；
- interrupted run：`.../live-20260731.json`；
- completed resume：`.../live-resume1-20260731.json`；
- checkpoint：`.../live-20260731.checkpoint.json`；
- final independent inspect：`.../inspect-final-20260731.json`；
- final resource audit：`.../resources-final-after-cleanup-fix-20260731.json`。
