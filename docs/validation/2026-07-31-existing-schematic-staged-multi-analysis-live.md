# 2026-07-31 existing-schematic staged multi-analysis live Gate

> 后续已只对 nominal winner 完成 TT/SS 两条件的 DC/AC/transient/noise Gate，并实现多
> alternative 与显式一层 hierarchy 的本地契约。见
> [winner-only PVT、多拓扑与一层 hierarchy Gate](2026-07-31-existing-schematic-winner-verification-multi-topology-hierarchy.md)。
> 本页末尾的“尚未验证”保留本 Gate 当时的范围。

## 结论

在新建且不覆盖的 `vb_pdk_smoke/vda_l5b_staged_gate_001/schematic` 上，VDA 已真实完成
同一候选内按 `DC -> AC -> transient -> noise` 顺序执行、前级约束提前淘汰、有限候选
选优、winner OA 写回与独立回读。随后新增的显式 `analysis_stage_execution:
shared_netlist` 把每个候选从“每一级各启动一次 Bridge/OA/si”收敛为“一次 worker、一次 OA
回读、一次 `si` 网表，多次 Spectre analysis”。相同 3 个候选的 9 个实际 analysis 指标与
隔离模式逐项完全相同，成功续跑命令墙钟时间从约 787 s 降到 358 s。

这是可迁移到用户给定 flat topology 的 L5B 基础执行原语，不是完整 L5B closure。

## 范围与授权

- PDK/profile：`nics4304_tsmc28`，TSMC N28/`tsmcN28`，nominal model 条件。
- OA 目标：`vb_pdk_smoke/vda_l5b_staged_gate_001/schematic`。
- 创建：`replace_existing=false`；目标是本 Gate 新建 cellview。
- OA 写入：只暂存 `MN0.Wfg`、`RD0.r`，最后写回真实 winner。
- 远端计算：`si -batch` 与 Spectre，scratch 只在 `/data/xum/virtuoso_bridge_smoke/`。
- 未修改 `virtuoso-bridge-lite`；VDA 只调用其公开 SSH/SKILL/OA/Spectre 能力。

## 任务契约

三个原子候选保持成组，不做笛卡尔积：

1. `MN0.Wfg=2u, RD0.r=30K`
2. `MN0.Wfg=1u, RD0.r=5K`
3. `MN0.Wfg=1.1u, RD0.r=18.5K`

有序 stage 与门控为：

1. `bias` / DC：`0.1 V <= output_dc_v <= 0.8 V`
2. `gain-bandwidth` / AC：gain >= 2 V/V，bandwidth >= 1 GHz
3. `linearity` / transient：max THD <= 10%
4. `noise` / noise：无提前 gate，只采集质量指标

目标是在全部约束通过的候选中最大化 GBW。前三级的完整 EDA 结果若违反约束，允许跳过
后续 stage；缺指标、空结果、analysis incomplete 或证据冲突不能作为“不可行”依据。

## 隔离模式基线

`artifacts/runs/existing-schematic-staged-gate/quality-live-20260731.json` 成功完成 9 个
analysis（最坏情况 12 个）：候选 1 在 DC 后被拒绝，候选 2/3 完成全部四级。每个 stage
单独启动 worker 并重新 OA 回读/`si`，9 次 simulation action 共 739.195 s，完整命令约
787 s。单 stage 为 80.060--84.582 s，说明固定启动与 netlist 成本主导。

## shared-netlist 实现与真实结果

新任务：
`examples/tasks/existing-schematic-staged-quality-shared-netlist.bridge.json`，plan token
`84f575f204dc8de3`。旧隔离任务 token 仍为 `f45b2242af90f5c1`。

首次执行在 Spectre 前失败：worker 错把同一 `output_dc_v` 的上下界视为重复 metric。
executor 成功恢复初始 `MN0.Wfg=1u, RD0.r=5K`；独立 inspect 证实恢复。实现随后改为一项
metric 可绑定多个 constraint relation，并增加回归测试。使用同一 checkpoint 从候选 1
边界续跑成功；半批 stage 不被复用。

成功 action 的实测如下：

| 候选 | 实际 stage | 隔离模式 | shared-netlist | 变化 |
| --- | ---: | ---: | ---: | ---: |
| 1（DC 拒绝） | 1 | 81.875 s | 85.360 s | 单级无收益，+4.3% |
| 2 | 4 | 331.555 s | 109.326 s | 3.03x，-67.0% |
| 3 | 4 | 325.765 s | 114.868 s | 2.84x，-64.7% |
| 合计 | 9 | 739.195 s | 309.555 s | 2.39x，-58.1% |

成功续跑命令还包含 probe、独立 OA resume readback、参数写入、最终提交与 after inspect，墙钟
358 s；相对隔离完整命令约 787 s 为 2.20x、减少 54.5%。这是保守比较，因为 shared 命令
额外执行了 resume 核验。

三个候选分别只生成一份 `si` 网表；每个 action 内所有 stage 的 netlist remote path、
SHA-256 和解析证据完全相同：

- 候选 1：`..._de951ec80f02/netlist`，SHA-256 `d552f594...d4dd8af`
- 候选 2：`..._e877954f8d66/netlist`，SHA-256 `3fbb25de...3d3680`
- 候选 3：`..._576e81a30801/netlist`，SHA-256 `4904ee7f...265dc1`

隔离模式有 9 个不同 remote path、3 个内容 SHA；shared 模式降为 3 个 path、3 个 SHA。
executor 独立重算 stage constraints、检查返回 stage 必须是任务的精确前缀，并核对 worker
报告的提前终止位置。缺少共享网表证据或 `netlist_generation_count != 1` 会拒绝候选。

## winner 与一致性

两个模式都选择候选 2：`MN0.Wfg=1u, RD0.r=5K`。三个候选的 stage 列表、终止位置、
metric key 和所有浮点 metric 在 JSON 数值上逐项相等，最坏相对差为 0。

winner 核心结果：

- `output_dc_v = 0.6399714692 V`
- gain `= 2.767097571 V/V` (`8.840489463 dB`)
- bandwidth `= 16.518663482 GHz`
- GBW `= 45.708753600 GHz`
- max THD `= 1.083740029%`
- integrated input-referred noise `= 690.6679674 uV rms`
- integrated output noise `= 1910.8103967 uV rms`
- small/large-signal supply power `= 46.8056756/46.8590685 uW`

任务外独立 inspect 最终确认：实例仍只有 `MN0/RD0`，nets/pins 仍为
`IN/OUT/VDD/VSS`，且 OA 为 `MN0.Wfg=1u, RD0.r=5K`。

结束时的只读资源审计显示远端 `spectre=0`、`si=0`、VDA-managed Maestro session `=0`；
服务器已有 `virtuoso=2`，本轮未终止或修改这些会话。审计临时启动的 hidden Bridge tunnel
随后显式停止；本机复核为 `ssh/spectre/si/virtuoso=0`、transient cancel marker `=0`。
第三方 Bridge 仓库仍在既有隔离分支 `codex/vda-transport-recovery`、工作树干净，本轮没有改动。
审计同时报告 `/data/xum/virtuoso_bridge_smoke` 有 567 个留存证据目录、合计约 310.8 MB，
其中 389 个超过 7 天；本地 `artifacts` 约 117.1 MB。它们不是活动进程或 transient leak，
但仍会长期占盘。本轮为保留可追溯证据只做 dry-run、未删除；后续应先定义 pin/retention policy
再执行可恢复或明确授权的清理，不能把“进程已退出”包装成“磁盘已自动回收”。

## 证据分类

- `eda_result`：实际 `si` netlist、Spectre DC/AC/transient/noise 数据、artifact manifest。
- `bridge_readback`：OA topology/CDF 参数、暂存回读、恢复回读、winner 最终独立回读。
- `software_inference`：context/netlist 一致性、stage completeness、constraint、提前终止、
  metric merge、objective 排序和 timing 比较。
- `user_input`：候选、stage 顺序、约束、objective、安全授权和目标 cellview。
- `system_event`：首次 shared worker 的重复指标约束实现错误及 checkpoint resume 记录。

## 尚未验证的边界与下一 Gate

- 本 Gate 是 flat、nominal、直接 CDF→netlist binding；未覆盖层次化设计和派生 CDF。
- `shared_netlist` 的 checkpoint 粒度有意为候选边界；worker 中断会重跑当前候选，不能复用
  未完成批次。
- 未对 shared 模式做真实传输中断注入；隔离模式已有真实 transport resume 证据。
- PVT、mismatch、stability/phase-margin、更多 topology alternative 仍应按任务需要加入，不能
  因 nominal 四分析成功而默认通过。
- 下一道有用 Gate 是把同一 staged contract 应用于一份未为本测试硬编码的新用户 topology，
  并按该电路的真实目标选择 DC/AC/transient/noise/PVT 子集；不再重复扩大本三点域。
