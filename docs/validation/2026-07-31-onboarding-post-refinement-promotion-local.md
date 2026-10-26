# Onboarding post-refinement CDF promotion 本地 Gate

日期：2026-07-31

状态：本地编译 Gate 通过；本批没有调用 Bridge、运行远端 Spectre 或写 OA。

## 问题

首次 topology delta 之前，新增实例尚不存在于 OA。VDA 可以用用户声明的 fixed 初值完成可逆结构
比较，但不能把尚未回读的 `RS0.r`、新 MOS CDF 或其他字段直接包装成已确认的搜索权限。若永远保持
fixed，新拓扑虽然能被比较，却不能无缝进入精细参数调优；若提前开放 search，又会把猜测的 CDF 名
冒充 `bridge_readback`。

本 Gate 增加 `vda onboarding-promote`，把这个边界拆成两个可恢复阶段：

1. 原有 `design.close_loop` 在粗粒度 fixed 初值下完成 topology×parameter nominal 选择并写回；
2. 独立只读 inspect 回读 winner 的实际 topology、placement 和完整 CDF；
3. promotion compiler 根据真实 winner 选择预声明调优分支，编译普通固定拓扑 `design.tune`；
4. 第二阶段有限搜索后，才对它的真实 winner 运行声明的质量分析和可选 PVT。

这不是新 executor。第一、第二阶段分别复用既有 TaskSpec、planner、Bridge adapter、checkpoint、
OA→`si` 一致性和 winner verification。

## 输入与拒绝边界

promotion 需要五类输入：

- 最终 stage-1 close-loop task；
- 与该 task/token 一致的真实成功 run；
- 后续独立只读 winner inspect task/run；
- 用 stage-1 task 文件 SHA-256 绑定的 promotion intent；
- hierarchy 情况下，每个保留的一层 child 的独立 inspect task/run。

stage-1 run 必须同时满足：

- adapter 为 `virtuoso-bridge-subprocess`；
- task 显式开启 compute/write、`replace_existing=false`；
- 声明 topology×parameter 域全部完成且 `domain_exhausted=true`；
- candidate index 连续、数量与 task 域一致；
- selected state 唯一对应一个 complete、feasible candidate；
- 最终 `schematic.inspect.final` 为 `bridge_readback`，其 topology hash 与 selected hash 相同。

独立 inspect 必须晚于 stage 1，且 target、PDK 和 topology hash 均与真实 winner 一致。promotion intent
可预先提供 baseline 与最多三个 alternative 分支；编译器只消费实际 selected variant 对应分支。未覆盖
winner 不会 fallback 到相似拓扑。

每个分支显式声明完整 CDF permission、完整 OA→`si` binding、可选 fixed update、原子 candidate set、
winner-only verification 和预算。permission/binding 集合必须相等，所有字段必须存在于 fresh、未过滤
CDF inventory；候选 search mode、analysis/metric、source/load/transfer 与 PVT supply 继续由普通
TaskSpec 校验。故该接口不依赖固定电路或 `RS0` 名称，人为指定的真实 CDF 可以进入调优，但未知字段和
未知 netlist 参数不会被猜测。

## 输出与恢复

CLI 同时输出：

```text
post-readback onboarding draft
fixed-topology design.tune TaskSpec
promotion compilation record
```

candidate source 绑定六个 SHA-256：stage-1 task/run、winner inspect task/run、promotion intent 和
post-readback draft。record 另存 compiled task SHA-256 与 plan token。三份文件以确定性 UTF-8/LF
序列化；相同输入重复执行得到逐字节相同输出。该检查实际发现并修复了 Windows `write_text` 换行转换
会令内存 hash 与落盘文件 hash 不同的问题。

输出任务固定：

```text
operation            = design.tune
topology_refinement  = absent
all topology objects = frozen
allow_remote_compute = false
allow_remote_write   = false
replace_existing     = false
```

因此 promotion 只生成新计划边界。第二阶段真实执行必须重新打开权限并重新 plan，不能复用 stage-1
token。stage 1 中断时恢复 stage-1 checkpoint；stage 2 中断时恢复普通 tuning checkpoint；promotion
本身不持有远端 session，相同输入可幂等重建。

## 本地代表用例

夹具使用 `MN0 + RD0` baseline 与新增 `NSRC + RS0` 的源极退化 alternative。stage 1 有两个 topology、
每个两个原子候选，共四点；真实 winner record 被构造成 source-degenerated 分支，并带最终结构化
Bridge readback。fresh inspect 新增：

```text
RS0.r       = 1K
RS0.isnoisy = yes
```

promotion intent 同时预声明 baseline 和 source-degenerated 分支。编译器没有取列表第一项，而是按真实
winner 选择第二项，将 `MN0.Wfg` 与 `RS0.r` 提升为 search，产生 `750 ohm/1 KOhm` 两点普通 tuning
任务，并只把 transient linearity/noise 放入 stage-2 winner verification。

拒绝与恢复测试覆盖：

- stage-1 completed count 小于声明域；
- fresh CDF 缺少 `RS0.r`；
- fresh CDF 中的 selected `RS0.r` 与 stage-1 winner 漂移；
- stage-1 task 未携带显式 OA write 权限；
- independent inspect topology master 漂移；
- promotion intent 未覆盖真实 winner；
- 同一输入重复函数编译与重复 CLI 落盘逐字节一致；
- task/draft record SHA-256 与实际落盘字节一致。

## 验证结果

```text
37 passed  # tests/test_onboarding.py
867 passed # 全量 Python 回归
230 planned, 0 failed # examples/tasks
python -m compileall -q src tests
vda catalog
vda resources # local transient=0, cancel marker=0
git diff --check
```

## 证据分类与未验证边界

- stage-1 final topology 与 fresh CDF inventory：输入要求为 `bridge_readback`；本 Gate 没有新增真实回读；
- branch 的参数、候选、规格与 quality/PVT intent：`user_input`；
- winner 关联、hash binding、权限提升判断和 TaskSpec 编译：`software_inference`；
- 本 Gate 没有 `eda_result` 或远端 `system_event`。

尚未验证的是一个真实用户模块上的完整
delta write→independent inspect→promotion→fine tune→winner quality/PVT。当前没有为证明编译器再造新
cellview，也没有重复跑已知候选。第一次 live integration 应使用实际模块、实际新增实例 CDF 和实际
规格；写后仍必须通过 OA 回读、`si` 网表一致性和真实波形/标量结果，不能凭 promotion record 宣称设计
闭合。
