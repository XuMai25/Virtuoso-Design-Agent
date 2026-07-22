# 2026-07-22 ADE 反相器 scoped-variable × environmental-corner live Gate

## 结论

在 `nics4304_tsmc28` / IC6.1.8 / Spectre 21.1.0 上，既有目标
`vb_pdk_smoke/vda_ade_sweep_inv_001/maestro` 已真实完成：

```text
test-scope CL=1f,4f
  × enabled corners=[Nominal,VDA_LOW_VDD,VDA_NOMINAL_VDD]
  × global/corner VDD=0.9/0.8/0.9
  -> 2 个真实 Maestro parametric point
  -> raw Detail CSV 的 3 个正交 corner 列
  -> 6 个 VDA case
  -> delay/skew/周期供电能量 constraints 与选优
```

最终状态可表述为 **native Maestro test-scope × environmental-corner quality
selection and bounded timeout recovery verified**。它证明 test/corner scope、global
selection、原始 corner 结果和有限选优已进入同一 ADE/Spectre 证据链；三个 corner
仍共享 nominal `top_tt` model，只改变 VDD，不能称为真实 process/temperature/PVT
corner，也还不是 L5B 单模块闭环。

## 目标和副作用边界

- schematic：`vb_pdk_smoke/vda_ade_sweep_inv_001/schematic`；本 Gate 没有写 OA。
- setup：只增量修改同一 `maestro` view；没有新建、替换或删除 cellview。
- 远端计算：`Interactive.4` 与 `Interactive.5` 均为真实 Maestro/Spectre 运行；
  runtime 产物位于 `/data/xum`。
- Bridge：继续使用 `virtuoso-bridge-lite` 0.7.0 的公开 Maestro、结果、SKILL、
  shell 与传输边界；没有修改、复制或建立私有 Bridge patch。
- 本地 run records 位于被 `.gitignore` 排除的 `artifacts/`；本文件保留关键路径、
  hash、数值和边界，避免把远端或大体积运行产物提交到仓库。

## 增量 setup 写入与独立回读

四个成功任务分别完成：

1. `02-corners-add-20260722.json`：以 tests=`[VDA]`、旧 corners=`[Nominal]`
   为 CAS，add-only 新增并启用 `VDA_LOW_VDD` 与 `VDA_NOMINAL_VDD`。写后完整
   corner 顺序为 `[Nominal,VDA_LOW_VDD,VDA_NOMINAL_VDD]`；没有修改已有
   corner、model file、变量、analysis/output 或 schematic。
2. `12-scoped-variables-named-corners-20260722.json`：把 `CL=1f,4f` 保存到
   test `VDA`，把 `VDD=0.8/0.9` 分别保存到两个 named corner。三个 scope 的
   写前值均不存在，即时回读与独立重开回读完全一致。
3. `13-global-vdd-single-20260722.json`：把 global VDD 从 sweep
   `0.8,0.9` 收敛为单值 `0.9`，避免 global sweep 与 corner override 重复展开。
4. `16-use-test-cl-20260722.json`：以 global CL selection 旧状态 enabled 为
   CAS，把它改为 disabled；未声明的 VDD selection 保持 enabled。保存前完整状态
   为 enabled=`[CL,VDD]`，独立重开后为 enabled=`[VDD]`、disabled=`[CL]`。

内建 `Nominal` 在当前 IC6.1.8 setup 中不是可作为 named-corner handle 读写的对象，
因此它继承 global `VDD=0.9`；两个新增 named corner 才承载显式 VDD override。
`03`–`11` 的失败记录保留了错误 API 形状和内建 Nominal handle 假设，均未保存
setup；最终成功路径不靠静默 fallback，且后续完整独立回读证明没有残留半写状态。

最终 run 前后 setup 指纹一致：

```text
b692f26c4ba9915aab78318363bbb969420caf15d6061911bbb38dbfd50500ba
```

setup 事实属于 `bridge_readback`；声明的 scope、旧值、selection 和目标值属于
`user_input`。

## Corner 结果解析与严格契约

Bridge 0.7.0 的普通结构化 Detail 结果只保留每个 parametric point 的 Nominal
列。VDA 没有修改 Bridge，而是调用其公开 `read_results(..., include_raw=True)`，
在 VDA worker 中解析同一原始 Detail CSV 的正交 corner 列：

- raw CSV：2404 bytes；
- SHA-256：
  `b2231b9a6da663ab83cea7aaaa8a4f8af4825ff184c3fe790891a01bc7c0ff67`；
- 维度：2 个真实 Maestro point × 3 个 exact-order corner；
- parser 必须得到完整 6-cell grid，不能缺列、重列或把 corner 当额外 sweep point；
- strict task 明确声明每个 VDA case 的 `maestro_point` 与 `corner`；
- scope 优先级固定为 corner > test > global；test-scoped sweep 还必须声明
  global selection=false；
- run 前后 tests、corner 顺序、全部有效变量、完整 selection 状态和 pinned output
  expression 都必须相同。

原始 CSV/RDB/log/Spectre 输入属于 `eda_result`；setup 与 OA 回读属于
`bridge_readback`；CSV 归一化、scope precedence、单位换算、constraints 和选优
属于 `software_inference`。

## 六个 EDA case 与选优

约束仍为 delay ≤ 6 ps、rise/fall skew ≤ 5 ps、周期供电能量 ≤ 5 fJ；objective
为最小化周期供电能量。

| VDA case | Maestro point | corner | CL (fF) | VDD (V) | delay (ps) | skew (ps) | energy/cycle (fJ) | feasible |
|---:|---:|:---|---:|---:|---:|---:|---:|:---:|
| 1 | 1 | Nominal | 1 | 0.9 | 2.882 | 1.223 | 1.659 | yes |
| 2 | 1 | VDA_LOW_VDD | 1 | 0.8 | 3.254 | 1.425 | 1.309 | yes, selected |
| 3 | 1 | VDA_NOMINAL_VDD | 1 | 0.9 | 2.882 | 1.223 | 1.659 | yes |
| 4 | 2 | Nominal | 4 | 0.9 | 5.503 | 3.939 | 4.147 | yes |
| 5 | 2 | VDA_LOW_VDD | 4 | 0.8 | 6.209 | 4.493 | 3.277 | no: delay |
| 6 | 2 | VDA_NOMINAL_VDD | 4 | 0.9 | 5.503 | 3.939 | 4.147 | yes |

选中 `CL=1 fF, VDD=0.8 V`，但这里只能解释为当前 nominal model、两个 CL、
三个环境 VDD case 中的最小能量可行点。周期供电能量仍包含泄漏和短路电流，
不称为纯动态能量。

completion log 报告 2 个真实 parametric point、4 个 simulation errors；错误集合
恰好是两个低压 cell 中旧固定阈值 `Rise` 与 `RiseFallSkew` 的四个 `eval err`。
strict task、RDB 单元格和 log 数量完全相等，未解释错误为 0；三个 VDD-aware
映射 output 均为有限 scalar。这里没有把“有错误但 return code 为 0”包装成成功。

## Timeout、显式恢复与自动恢复

首次严格任务：

```text
artifacts/runs/ade-inverter-scope-corner-live/17-corner-quality-run-20260722.json
```

Bridge `run_and_wait` 在 600 s 后报超时，record 因而失败；只读检查证明新 history
`Interactive.4` 已在远端完成。显式固定 history/scratch 的恢复记录：

```text
artifacts/runs/ade-inverter-scope-corner-live/18-corner-quality-resumed-20260722.json
```

成功读取同一 6-cell 结果，没有重跑、写 OA 或保存 setup。随后 VDA 层新增一个窄的
callback-timeout 恢复条件：只在 strict corner 模式中，若 run 前后恰好新增一个
history 名，且 exact log 明确 `<history> completed.`，才继续绑定该 history；多个
新 history、同名覆盖、缺日志或未完成日志一律失败，不会自动重跑。

90 s live probe 产生：

```text
artifacts/runs/ade-inverter-scope-corner-live/19-corner-quality-auto-recovery-20260722.json
```

它从运行前 `Interactive.0`–`Interactive.4` 中唯一选出新 `Interactive.5`，记录
`run_status=recovered_after_bridge_timeout`、
`simulation_performed_by_this_invocation=true` 和
`callback_timeout_history_recovery_performed=true`。运行输入、RDB、log、raw CSV、
setup 及六个 case 全部通过 executor 的第二层核对；runtime 目录已恢复，OA/setup
写入均为 false。该机制解决 callback marker 丢失，不放宽仿真或设计质量证据门。

## 产物与指纹

`Interactive.5` 最终记录包含：

- 58 个 `simulator_input`、1 个 `eda_result`、2 个 `run_log`；
- exact-history RDB：540672 bytes，SHA-256
  `247e3074f227d1643ce0be63a5260d066ea7b4179872ae339fc8f08e84477971`；
- completion log：1223 bytes，SHA-256
  `a2f7ea2586b76606835615db5bcc102035c9fbaf8ba3ba590562132a48391c17`；
- simulation fingerprint：
  `1fdd05db40740f7975383b4976fbd981ffbcc2d0f093d08ad5f43b2d287a28a3`；
- result-mapping setup fingerprint：
  `f00460c0e03378ae37b661a91fa10e92b8913e854611ad4b9550f4ae43754c1a`；
- evidence mode：
  `maestro_exact_history_rdb_with_shared_symbolic_runtime_input`；
- `native_sweep_database_binding_verified=true`，同时保持
  `exact_point_input_result_binding_verified=false`，没有虚构逐 case `input.scs`。

## 本地回归与未验证边界

```text
353 passed in 0.66s
71/71 example task plans succeeded
compileall succeeded
vda catalog succeeded
inverter-close-loop.demo plan succeeded
```

新增回归覆盖 corner add-only CAS、global selection 完整读写、scope precedence、
raw corner CSV parser、6-cell grid、setup 前后不变、exact error accounting、显式恢复、
唯一 completed-history 自动恢复，以及 worker/executor 双层失败路径。

尚未闭合：

1. 当前 corner 是 VDD 环境 case，不含真实 process section、temperature 或 model
   file 变化。
2. 当前只有一个 test 和一个 transient analysis；multi-test/multi-analysis 结果形状
   仍未 live。
3. history 名称唯一性仍未证明；自动恢复只证明“本次唯一新增且 completed”。
4. 仍没有人工打开 ADE、修改/重跑并与 VDA 数值交叉核对；该 Gate 按延期记录处理。
5. 反相器 testbench 的 CL/VDD 选优不是共源级 L/VDD 设计参数搜索，也没有最佳
   transistor 参数 OA 写回。

下一道自动化 Gate 是在既有共源 cellview 上加入受限 L/VDD 搜索，并把 DC、AC、
transient/noise 的 multi-analysis 结果统一进候选证据；随后接入一个明确 model
section/temperature 的小型真实 PVT corner 集合。已有 output 的安全替换继续作为
独立 setup Gate，不与本次 add-only 路径混合。
