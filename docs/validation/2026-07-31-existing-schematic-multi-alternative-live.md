# 2026-07-31 existing-schematic 多 topology alternative 真实闭环 Gate

状态：**three-topology checkpointed OA -> si -> Spectre selection and winner writeback live verified; one-level hierarchy live verification and L5B closure pending**。

本轮没有修改 `virtuoso-bridge-lite`，没有访问 `/home/xum`，也没有覆盖既有 cellview。VDA 只在
`vb_pdk_smoke/vda_l5b_multi_alt_gate_001/schematic` 新建一个 `replace_existing=false` 的目标，随后在
该目标上执行任务声明的可逆 topology delta、实例参数暂存和 winner 写回。远端计算与证据仅位于
`/data/xum/virtuoso_bridge_smoke/vda_existing-schematic-multi-alternative-close-loop-live_*`。

## 任务与候选域

- create：`examples/tasks/existing-schematic-multi-alternative-create.bridge.json`
- independent inspect：`examples/tasks/existing-schematic-multi-alternative-inspect.bridge.json`
- close loop：`examples/tasks/existing-schematic-multi-alternative-close-loop.bridge.json`
- close-loop plan token：`58a8971f4552d9f6`
- 声明候选来源：`user_input`，generator id 为 `multi-alternative-two-point-gate`
- `max_iterations=6`，正好等于三个 topology variant 乘两个原子参数 tuple；controller 不随机造点

三个 topology identity 为：

| variant | 结构 | topology SHA-256 |
|---|---|---|
| `common-source` | `MN0 + RD0` | `a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41` |
| `source-degenerated` | baseline 加固定 `RS0=750 ohm` | `f441b938846c366b89b28da1c6866db11afaf816d522479b293ba5d62187c9c6` |
| `cascode` | baseline 改为 `MN0 -> NCAS -> MNCAS -> OUT`，`MNCAS.Wfg=750n/L=30n` | `2e27d1c68012bc1e7adc0da936a648febde4b685cfa1391512a2d401fb291a51` |

两个参数 tuple 为 `compact-load: MN0.Wfg=1u/RD0.r=5K` 和
`gain-load: MN0.Wfg=1.1u/RD0.r=18.5K`。每点先运行 DC，要求
`0.1 V <= output_dc_v <= 0.8 V`；只有完整 DC 结果才进入 AC，再要求 gain 不低于 `5 V/V`、
带宽不低于 `2 GHz`，并在可行点中最大化 GBW。这组门限用于验证控制器能够让不同局部拓扑在同一规格下竞争，
不是产品规格、连续设计空间或 foundry signoff 条件。

## 六点真实结果

| index | topology | MN0.Wfg / RD0.r | output DC | gain | bandwidth | GBW | feasible |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | common-source | 1u / 5K | 0.639971 V | 2.76710 V/V | 16.5187 GHz | 45.7088 GHz | no: gain |
| 2 | common-source | 1.1u / 18.5K | 0.264448 V | 4.58293 V/V | 7.22436 GHz | 33.1087 GHz | no: gain |
| 3 | source-degenerated | 1u / 5K | 0.718790 V | 1.66337 V/V | 14.8055 GHz | 24.6270 GHz | no: gain |
| 4 | source-degenerated | 1.1u / 18.5K | 0.379443 V | 3.87961 V/V | 5.41229 GHz | 20.9976 GHz | no: gain |
| 5 | cascode | 1u / 5K | 0.761425 V | 1.91279 V/V | 13.1224 GHz | 25.1003 GHz | no: gain |
| 6 | cascode | 1.1u / 18.5K | 0.379603 V | 6.38948 V/V | 3.90874 GHz | 24.9748 GHz | yes |

声明域 6/6 全部完成，只有第 6 点同时满足 DC、gain 和 bandwidth 门。最终选择为：

```text
topology = cascode
MN0.Wfg = 1.1u
MNCAS.Wfg = 750.0n
MNCAS.l = 30n
RD0.r = 18.5K
```

search audit 为 `best_in_declared_discrete_domain`、`domain_exhausted=true`，同时明确记录
`continuous_optimum_claim=false`、`global_optimum_claim=false`。因此本结果证明的是“完整声明域中最高排名的可行点”，
不是随便挑一点，也不是连续或全局最优。

## 同源证据

六个完成候选都满足 `one_worker_one_oa_readback_one_si_netlist`：每点只回读一次 OA、生成一次 Cadence
`si -batch` netlist，再在该 netlist 上依次运行 DC 和 AC。六份 netlist evidence 均为：

- OA topology SHA 与当前 variant 完全匹配；
- netlist primitive、节点和 OA graph 一致；
- 每个声明的 `Wfg/l/nf/simM/r` OA-CDF 到 Spectre 参数绑定均为 `matched`；
- DC/AC 波形和标量是 `eda_result`，OA 原始结构与参数是 `bridge_readback`；
- topology/parameter consistency、stage gate 和最终排序是 `software_inference`；
- 候选 tuple、约束、objective 和预算是 `user_input`。

winner netlist 为
`/data/xum/virtuoso_bridge_smoke/vda_existing-schematic-multi-alternative-close-loop-live_5c442dd07b46/netlist`，
SHA-256 为 `d7777488490a9714dff153a3e2a7d985ab9d9c73562f1dcad9ea4039655a5f58`。其结构化网表回读包含：

```text
MN0   (NCAS, IN,   VSS, VSS) nch_lvt_mac
MNCAS (OUT,  VCAS, NCAS,VSS) nch_lvt_mac
RD0   (VDD,  OUT)            resistor
```

## 中断与 checkpoint 恢复

本次校园网/入口 SSH 在长任务中三次中断，均被保留为 `system_event`，没有计为电路不可行：

1. candidate 2 上传 Spectre 文件时入口 SSH timeout；自动恢复回读又遇到 `WinError 10054`。重启 Bridge 后，
   任务外 inspect 确认 OA 正是 checkpoint 声明的 common-source `1.1u/18.5K`，随后从 index 2 恢复，
   candidate 1 没有重跑。
2. 四个候选完成后，source-degenerated inverse 已恢复 baseline，但末尾回读连接断开。新的任务外 inspect 确认
   精确初始 baseline `MN0.Wfg=1u/RD0.r=20K`、无 `RS0/MNCAS`，随后只从 index 5 继续。
3. candidate 5 完成后，cascode inverse 的末尾回读再次断开。独立 inspect 再次确认同一初始 baseline，随后只从
   index 6 继续。

最终 run record 保留失败 action、三次 resume/readback、6 个完成候选和 76 个有来源 action。六次成功
simulation action 分别为 `84.97/84.62/87.26/110.45/111.75/85.97 s`；从首次 run 开始到最终完成的
记录跨度为 `1065.26 s`，其中包含三次连接中断、人工边界核对和 tunnel 重启，不能当作纯 Spectre compute time。

## Winner 写回与独立回读

最终选择后，executor 从共同 baseline 重新执行 cascode forward，并写回 winner 参数。新的任务外 inspect
`inspect-final-20260731.json` 独立确认：

- instances：`MN0,MNCAS,RD0`；不存在 `RS0`；
- nets：`IN,NCAS,OUT,VCAS,VDD,VSS`；pins：`IN,OUT,VCAS,VDD,VSS`；
- `MN0.G=IN, MN0.D=NCAS, MN0.S/B=VSS`；
- `MNCAS.G=VCAS, MNCAS.D=OUT, MNCAS.S=NCAS, MNCAS.B=VSS`；
- `MN0.Wfg=1.1u/L=30n`、`MNCAS.Wfg=750.0n/L=30n`、`RD0.r=18.5K`；
- placement SHA-256：`bd5e068b6ce0cae5ef081e8a68b5c345de74ad0dd92c263a93b997dae60b7e8f`。

## 资源与验证边界

只读 resource audit 从本地 163 个 evidence entries、107,751,037 bytes 变为 164 个、116,631,907 bytes；
远端从 571 个 evidence directories、311,013,370 bytes 变为 578 个、311,575,078 bytes。本 Gate 因六个
成功 netlist 和一次失败上传留下七个精确证据目录，共增加 561,708 bytes；它们是可审计证据，不是泄漏进程，
本轮没有删除。

postflight 为 `spectre=0`、`si=0`、VDA-managed Maestro session=0、本地 transient/cancel marker=0。
Bridge tunnel 已显式停止；系统进程表中与本次 workspace、Bridge 或本地端口匹配的 Python/SSH/SCP/si/Spectre
进程为 0。两个远端 Virtuoso 进程在 preflight 已存在，本轮未新增或终止。

全量 Python 回归：

```text
798 passed
```

主要本地证据位于 `artifacts/runs/existing-schematic-multi-alternative/`：create、三次中断后的独立 inspect、
checkpoint、三份 resume run record、最终 inspect 和前后 resource audit。该目录由 Git 忽略，不提交大型运行产物。

本 Gate 将多 alternative controller 从“本地确定性/故障测试”升级为真实 OA round-trip、同源 DC/AC、跨拓扑
checkpoint 恢复和 winner 写回均已验证。仍未真实验证的是显式一层 hierarchy OA -> si、派生 CDF、深层 hierarchy、
并发人工 editor、mismatch/Monte Carlo，以及用户实际单模块上的完整质量规格闭环；因此仍不是 L5B closure。
