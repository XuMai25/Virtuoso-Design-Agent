# 2026-07-31 existing-schematic 一层 hierarchy 真实 Gate

状态：**non-overwrite schematic-to-symbol plus one-level primitive-child OA -> si ->
Spectre DC/AC live verified at nominal TSMC N28; hierarchical parameter tuning and L5B
closure pending**。

本 Gate 没有修改 `virtuoso-bridge-lite`，没有访问或写入 `/home/xum`，也没有覆盖既有
cellview。目标在执行前均由只读探针确认不存在；所有远端运行产物位于
`/data/xum/virtuoso_bridge_smoke/`。最终保留 child schematic/symbol 与 hierarchical top
作为后续跨层参数 Gate 的可复用夹具。

## 目标与任务

| 角色 | library/cell/view | 结果 |
|---|---|---|
| child source | `vb_pdk_smoke/vda_l5b_hier_child_001/schematic` | 新建，`replace_existing=false` |
| child interface | `vb_pdk_smoke/vda_l5b_hier_child_001/symbol` | 从 source 非覆盖生成 |
| top | `vb_pdk_smoke/vda_l5b_hier_top_001/schematic` | 新建 flat 后，以 topology delta 增量替换为 child instance |

可复现任务文件：

- `examples/tasks/hierarchy-child-create.bridge.json`
- `examples/tasks/hierarchy-child-symbol-generate.bridge.json`
- `examples/tasks/hierarchy-child-inspect.bridge.json`
- `examples/tasks/hierarchy-top-create.bridge.json`
- `examples/tasks/hierarchy-top-flat-dc-ac.bridge.json`
- `examples/tasks/hierarchy-top-transform.bridge.json`
- `examples/tasks/hierarchy-top-hierarchical-dc-ac.bridge.json`
- `examples/tasks/hierarchy-top-inspect.bridge.json`
- `examples/topology/hierarchy-wrap-common-source.operations.json`
- `examples/topology/hierarchy-wrap-common-source.contract.json`

symbol generation plan token 为 `f2ee18a87b37bd1b`；hierarchical DC/AC plan token 为
`0e1bfa68498f60da`。任务声明与授权边界是 `user_input`。

## 非覆盖 symbol 生成

child 是 `MN0 + RD0` 电阻负载共源级，参数为 `MN0.Wfg=1u`、`MN0.L=30n`、
`RD0.r=20K`。生成前绑定 source：

- topology SHA-256：`a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41`
- placement SHA-256：`b59661b2047674c4a7da81b56fe745a76bac56082e08bdd92d0e7d2f6433ea35`
- exact pins：`IN/OUT/VDD/VSS` 及各自 direction/width

worker 复用 Cadence `schSchemToPinList` 和 `schPinListToSymbol`。临时修改
`ssgSortPins` 时使用 `unwindProtect`；本次调用前后均为 `alphanumeric`。保存后由新的独立
worker 重开 symbol，得到 OA terminal 顺序 `IN,OUT,VDD,VSS`，方向/宽度全部匹配，bBox 为
`((-0.025 -0.65) (2.175 0.5875))`。已有 symbol 会在写前拒绝；只有本次刚创建但未通过审计
的 symbol 才允许回滚，不存在覆盖或静默 refresh 路径。

source/symbol OA 回读属于 `bridge_readback`；source hash、pin 比较、session 恢复判断属于
`software_inference`。symbol operation run record SHA-256 为
`47b2386c7f3a392368dd2e3e8470bfb0d7b7160c0180b396fc4bbd9d7de815de`。

## Flat reference 与 hierarchy 变换

top 初始建立与 child 同构的 flat `MN0+RD0` 共源级。flat OA→`si`→Spectre shared-netlist
DC/AC 成功，simulation action 为 `83.717456 s`。随后 topology delta：

- 删除 top `MN0`、`RD0`；
- 添加 `XAMP`，master 为 `vb_pdk_smoke/vda_l5b_hier_child_001/symbol`；
- 绑定 `XAMP.IN/OUT/VDD/VSS` 到同名 top nets；
- 不新建或替换 top cellview。

变换前 topology SHA 与 child source 相同，为 `a0bb...6a41`；变换后及最终任务外回读为
`d558b41830cfe50cb7c344bde426346c5236e3a89c5af3a77ed0a863d9d5fd6a`，最终 placement
SHA-256 为 `8899f1d68e1badb505c46aa15c1a6b78ff436d93389948e199c257afe14a6b52`。
任务外回读只有 `XAMP`，master 和四个 terminal 均匹配。same-library child 被加入本次目标
library 的精确 master allowlist；没有开放任意远端 library。

top transform run record SHA-256 为
`e1e035ae80f7ab4ef58564b605072f40bba4ab9f68a4d738f5fe76d9f3410b36`。
正向 contract 同时包含确定性 inverse，但本 Gate 没有把 reusable hierarchical fixture 还原；
generic inverse 的真实能力由既有 topology-delta Gate 证明，不能用本次未执行的 inverse 冒充新证据。

## OA、si 与 Spectre 同源证据

最终 worker 只启动一次、回读一次 top OA、生成一次 `si` netlist。它逐项核对：

- top `XAMP` master、terminal map 与 `si` subckt call；
- subckt `vda_l5b_hier_child_001` 的 terminal order `IN,OUT,VDD,VSS`；
- child OA pins 与 subckt terminals；
- child `MN0/RD0` primitive/model/node graph 与 subckt body；
- child canonical topology SHA `a0bb...6a41`；
- child full placement SHA `b596...ea35`；
- 未出现未绑定 subcell 或 nested subckt。

Cadence `si` 输出的原始无扩展名网表保留在：

```text
/data/xum/virtuoso_bridge_smoke/vda_hierarchy-top-hierarchical-dc-ac-live_3b3329342e11/netlist
```

其 SHA-256 为
`78c5874ce82c6d98b6afd640d9a829259036268b91181975a6fb8858436638b0`。Spectre 对被 include
文件按文件自身语法判断，父 wrapper 的 language reset 不能改变无扩展名 child include 的解析。
VDA 因而保留 raw 文件，并生成唯一确定性 envelope：

```text
/data/xum/virtuoso_bridge_smoke/vda_hierarchy-top-hierarchical-dc-ac-live_3b3329342e11/vda_oa_netlist.scs
```

envelope 只在首行加入 `simulator lang=spectre`，SHA-256 为
`0cffdcb5b01661ff68f7fc7fdbc7249e304c7f45ab96e4f08ec67892d028a35c`，记录的 transform
为 `prepend_simulator_lang_spectre`、`raw_preserved=true`。raw `si` netlist、Spectre OP/AC 和
波形/标量属于 `eda_result`；OA top/child/symbol 属于 `bridge_readback`；envelope 生成、图一致性
和数值比较属于 `software_inference`。

## Flat 与 hierarchical 数值对照

| metric | flat | hierarchical | 结论 |
|---|---:|---:|---|
| output DC | `0.2687551190114393 V` | `0.2687551190114393 V` | 相同 |
| supply current | `-3.156224404942804e-05 A` | `-3.156224404942804e-05 A` | 相同 |
| low-frequency gain | `4.588483049639973 V/V` | `4.588483049639973 V/V` | 相同 |
| gain | `13.233382634236758 dB` | `13.233382634236758 dB` | 相同 |
| bandwidth | `6.762109563482076 GHz` | `6.762109563482103 GHz` | 浮点差 |
| GBW | `31.027825111845863 GHz` | `31.027825111845990 GHz` | 浮点差 |
| unity | `30.356729494804530 GHz` | `30.356729494804530 GHz` | 相同 |

对 output DC、supply current、gain、bandwidth、GBW 和 unity 计算的最大相对误差为
`4.0899300604558383e-15`。hierarchical simulation action 为 `83.630236 s`，与 flat 同量级；
该 Gate 证明层级兼容性与同源等价，不声称层级化能降低 Spectre 时间。

flat run record SHA-256 为
`957ad879a14803aca260cd0f83bba8e0469ff88465565ba3d1039d188da87c2c`；最终 hierarchical
run record SHA-256 为
`d712f676eac6e4b74e233be8f911f87a2bd2c7163bec7e7094752743e077d34f`。最终 DC/AC artifact
manifest SHA-256 分别为 `fcebf7692dd618868aac2445479b76ddea68c78e77d1e0e8c570205730f18a8c`
和 `65113a66ec9edc9894a593ecb0e4e1a2607cbc8d435eb708e6f61e86db0b2d16`。

## 失败路径与修正

失败均发生在只读 simulation，没有追加 OA 写入；每个远端目录保留供审计：

1. `_852314735cf7`：parser 把 `si` subckt 内的普通缩进行误认为 continuation，合并后报
   terminal 非法/重复。修正为只接受前导 `+` 或上一行尾部 `\\` 续行。
2. `_03c0ade42c97`：raw 无扩展名 `si` netlist 被 Spectre 按 SPICE 解析，`subckt` 不支持。
3. `_f6a4b15b4653`：仅在父 wrapper 重置 `simulator lang=spectre` 仍失败，证明 include 文件自身
   需要 language header。
4. `_037689699de2`：envelope 后数值成功，但 child evidence 使用 logical-only SHA
   `4471...`，与完整几何 canonical source SHA 不同，因此未接受为最终 Gate。
5. `_3b3329342e11`：加入完整 child geometry bundle 后，canonical topology/placement 与仿真全部通过。

这些失败分别增加 parser continuation、Spectre language-envelope、canonical child placement 和
负向回归测试；没有通过放宽 terminal、图一致性或数值门限来“做绿”。

## 资源、测试与边界

最终只读资源盘点保存于
`artifacts/runs/hierarchy-live/resource-audit-final.json`，`deletion_performed=false`。远端
`spectre=0`、`si=0`、VDA-managed Maestro session=`0`；两个 Virtuoso 进程是 Gate 前已经存在的
远端服务。Bridge tunnel 已显式停止，本地没有遗留 VDA worker、Bridge SSH 或 Spectre 进程。
失败与成功证据目录均保留，本轮没有执行广泛清理。

本地测试覆盖：symbol model/planner/payload、已有 symbol 拒绝、独立 source/symbol action、bBox
解析、Cadence API/session 恢复、source drift、read-only hierarchy 空 parameter binding、tuning 对
缺 binding 的拒绝、真实缩进 subckt/续行、language envelope、child canonical placement，以及
same-target-library master allowlist。全量回归为 `805 passed in 4.31s`。

本 Gate 尚未证明：

- top task 直接寻址、暂存、回读并优化 child 内部 CDF 参数；
- 多个 child、两层以上 nested hierarchy 或递归 subckt；
- symbol 更新、覆盖或 schematic 变化后的自动 refresh；
- 派生 CDF、`nf/m` 层级总宽度语义；
- transient/noise/PVT/mismatch/Monte Carlo 或 ADE 人工 handoff；
- 该共源级满足任何产品规格或 L5B 设计质量 closure。

下一道有用的 Gate 是将显式 `child-instance/primitive-instance/parameter` 路径接入既有有限
candidate、checkpoint、winner verification 和恢复状态机。它应使用这个保留夹具的小范围 W/RD
候选，证明跨层 OA 写入/回读真正进入 `si`，并覆盖可行、不可行、transport resume 与最终写回；
不需要再创建另一个拓扑重复本次 flat/hierarchy 数值等价测试。
