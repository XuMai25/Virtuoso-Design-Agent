# 2026-07-22 ADE 反相器 VDD×CL 二维质量 Gate

## 范围与结论

本 Gate 在已有 `vb_pdk_smoke/vda_ade_sweep_inv_001` 上做增量修改，没有新建或替换 cellview：

- schematic：把 `VDD0.vdc` 与 `VIN0.v2` 从固定 `900.0m` 改为同一个 OA 符号 `VDD`；`CL0.c=CL` 保持不变。
- Maestro：新增 global `VDD=0.8,0.9`，保留 global `CL=1f,2f,4f`。
- output：只新增七个 VDD-aware point output；没有替换或删除已有 output。
- 运行：执行原生 `VDD×CL` 六点 transient，并把同一符号 `VDD` 同时绑定到供电源与输入高电平。
- 结果：六个点均有真实 RDB scalar；五点满足 delay≤6 ps、skew≤5 ps、周期供电能量≤5 fJ。最小能量目标选择 `VDD=0.8 V, CL=1 fF`。

因此状态升级为 **native Maestro two-variable VDD×CL same-source quality selection and recovery verified**。这仍不是 corner、multi-test、多 analysis 或 L5B 单模块闭环。

## 写入与回读

写入前只读检查确认目标存在、旧 `Interactive.1` 仍可按固定 history/scratch 恢复，且 schematic 参数为 `CL0.c=CL`、`VDD0.vdc=900.0m`、`VIN0.v2=900.0m`。

三个独立任务随后分别完成：

1. `parameters.apply` 把 `VDD0.vdc`、`VIN0.v2` 写为 `VDD`；立即回读和独立 inspect 都匹配。
2. `ade.variables.apply` 以旧值不存在为 CAS 前置条件新增 global `VDD=0.8,0.9`；即时值和独立重开值一致。
3. `ade.setup.apply` add-only 新增 `TphlVdd`、`TplhVdd`、`DelayVdd`、`RiseVdd`、`FallVdd`、`RiseFallSkewVdd`、`SupplyEnergyCycleVdd`；阈值分别使用 `0.5/0.1/0.9 * VAR("VDD")`，能量表达式使用实际 `VAR("VDD")`。全部 output 独立重开一致，既有 analysis、test、CL 和 output 未被替换。

对应 run record 为：

- `artifacts/runs/ade-inverter-vdd-cl-live/04-vdd-symbol-bind-20260722.json`
- `artifacts/runs/ade-inverter-vdd-cl-live/05-vdd-variable-20260722.json`
- `artifacts/runs/ade-inverter-vdd-cl-live/06-vdd-aware-outputs-20260722.json`

这些 setup/OA 事实属于 `bridge_readback`；任务声明属于 `user_input`。

## 运行、中断与恢复

首次六点调用在远端 28 秒内生成 `Interactive.2`，但外层 PowerShell 与远端任务都使用 600 秒界限，外层先终止了本地 worker，未生成 VDA run record。该事件不是电路不可行：done marker、RDB、log 和六点 Detail 都已存在。

远端 scratch 为：

```text
/data/xum/virtuoso_bridge_smoke/vda_ade_run_inverter-ade-vdd-cl-probe_3ab7fd7a254c
```

被终止的 worker 留下 `fnxSession70`。VDA 先从旧成功 record 取得原 project/results dir，随后只恢复这个后台 session 的内存 runtime path 并关闭 session；没有保存 setup、重跑仿真或删除远端数据。最终固定 `Interactive.2` 与同一 scratch 的恢复任务确认：

- `simulation_performed_by_this_invocation=false`
- `runtime_directory_restored=true`
- `oa_write_performed=false`
- `maestro_setup_write_performed=false`
- 所有 manifest 远端路径都位于 `/data/xum`

最终严格 record：

```text
artifacts/runs/ade-inverter-vdd-cl-live/09-vdd-cl-strict-resume-20260722.json
```

外层命令后续使用大于 worker 内部 `timeout_seconds + 240 s` 的等待界限，避免再次在清理窗口抢先终止。VDA 本身已有这 240 秒清理余量，本轮没有把问题误修成 Bridge 或仿真超时。

## 六点 EDA 结果与选优

| point | VDD (V) | CL (fF) | delay (ps) | rise (ps) | fall (ps) | skew (ps) | energy/cycle (fJ) | feasible |
|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 1 | 0.8 | 1 | 3.254 | 3.392 | 4.817 | 1.425 | 1.309 | yes |
| 2 | 0.9 | 1 | 2.882 | 3.175 | 4.398 | 1.223 | 1.659 | yes |
| 3 | 0.8 | 2 | 4.246 | 4.792 | 7.187 | 2.395 | 1.973 | yes |
| 4 | 0.9 | 2 | 3.766 | 4.363 | 6.475 | 2.112 | 2.497 | yes |
| 5 | 0.8 | 4 | 6.209 | 7.666 | 12.160 | 4.493 | 3.277 | no: delay |
| 6 | 0.9 | 4 | 5.503 | 6.857 | 10.800 | 3.939 | 4.147 | yes |

同一负载下，0.9 V 均更快但能量更高；这只是当前 TSMC N28 nominal setup、当前波形窗口和有限六点中的真实结果，不能外推到其他 PDK、corner 或完整能耗模型。周期供电能量包含泄漏与短路电流，不称为纯动态能量。

## 旧 output 错误的精确处理

`Interactive.2.log` 报告 6 个 simulation errors。逐点 RDB 证明它们恰好是旧固定阈值 output：

- `Rise` 在三个 `VDD=0.8 V` 点返回 `eval err`；
- `RiseFallSkew` 在三个 `VDD=0.8 V` 点返回 `eval err`；
- 七个新 VDD-aware 映射 output 在六点全部为有限 scalar。

没有删除或替换这些人工旧 output。VDA 新增 `expected_output_evaluation_errors` 严格契约，但默认仍要求 0 error：

- 任务必须声明 exact test、output 和 point-value selector；
- 声明 output 不得与任何映射 metric output 重叠；
- worker 要求 RDB 中实际 `eval err` 单元格与声明集合完全相等；
- completion log 的 error 数必须等于该集合大小；
- executor 独立重算同一集合，并要求 `unaccounted_simulation_errors=0`。

因此本 Gate 没有把“忽略所有错误”作为兼容策略，也没有用 return code 或非空 RDB 代替完整性检查。第一次严格恢复因旧的零错误规则失败，第二次在 worker 通过后又被 executor 的独立旧规则拒绝；两层都更新并补回归测试后，第三次恢复才成功。

## 证据形状

最终 record 包含：

- 共享符号 runtime `input.scs` + sibling `netlist`；`CL0.c=CL`、`VDD0.vdc=VDD`、`VIN0.v2=VDD` 三个 OA/Spectre 绑定全部通过。
- exact-history `Interactive.2.rdb` 与 completion log；六个点、参数和值均匹配。
- 61 个 manifest 条目：58 个 simulator input、1 个 EDA result、2 个 run log。
- simulation fingerprint：`ec3d57f76c92cf38dbedb9ac9c01660b4294ecfcc482e352e1ea746002da80ae`。
- 证据模式：`maestro_exact_history_rdb_with_shared_symbolic_runtime_input`；明确保持 `exact_point_input_result_binding_verified=false`。
- 原始 RDB scalar 为 `eda_result`；OA/setup 回读为 `bridge_readback`；SI→ps/fJ 换算、constraints 和最优点选择为 `software_inference`。

## 本地验证与边界

```text
323 passed in 0.62s
65/65 example task plans succeeded
vda catalog succeeded
inverter-close-loop.demo plan succeeded
```

本轮没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。新增行为全部位于 VDA task contract、worker 证据核对、executor 独立守卫、planner 披露、示例和测试。

未验证边界：

- history 名称唯一性仍未证明；
- 当前 IC6.1.8 证据是共享符号输入束 + RDB，不是逐点输入文件；
- test/corner-scoped variable 写入和真实有限 process corner 尚未 live；
- multi-test/multi-analysis result mapping、已有 output 安全替换和人工 ADE 数值交叉检查仍未闭合；
- 本 Gate 只扫 testbench VDD/CL，没有调整 MOS L/W，也没有写回一个“最佳 OA 设计参数”。

下一道自动化 Gate 是在不新建拓扑的前提下完成一个受限的 Maestro corner/test-scope Gate；随后再把 L/VDD 与有限 corner 纳入共源/单模块质量搜索。人工打开 ADE、旧 ADE L 迁移和人工数值交叉检查继续按延期记录处理。
