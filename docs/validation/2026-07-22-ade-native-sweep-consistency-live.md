# 2026-07-22 ADE 原生 CL sweep 同源闭环真实验证

## 结论

TSMC N28 专用目标 `vb_pdk_smoke/vda_ade_sweep_inv_001` 已真实完成：

```text
OA schematic 中 CL0.c=CL
  -> 保存的 Maestro global CL=1f,2f,4f
  -> Spectre runtime input.scs include 同目录 netlist
  -> netlist 中 CL0 (OUT 0) capacitor c=CL
  -> Maestro exact-history RDB 的 3 个 point
  -> 每点 CL 参数和非空 VoutAvg
  -> setup/OA/input/RDB/log 一致性与恢复证据
```

状态可表述为 **native Maestro CL sweep same-source execution and evidence recovery verified**。这证明该变量 sweep 已进入真实 Maestro/Spectre 结果链，但 `VoutAvg > 0.1` 只是链路 smoke；尚不能表述为反相器 delay/energy 设计质量闭环或 L5B 规格闭环。

## 目标与副作用

- OA：`vb_pdk_smoke/vda_ade_sweep_inv_001/schematic`；
- ADE：`vb_pdk_smoke/vda_ade_sweep_inv_001/maestro`，test `VDA`；
- PDK：`nics4304_tsmc28` / TSMC N28；
- 首次链路在明确授权下新建上述 cellview，不覆盖已有对象；
- 原生 sweep 只实际运行一次；后两次诊断均固定恢复 `Interactive.0`，没有再次调用 `run_and_wait`；
- runtime 与 manifest 均位于 `/data/xum/virtuoso_bridge_smoke/...`；
- 没有修改 `virtuoso-bridge-lite`，只复用 Bridge 0.7.0 的 Maestro、SKILL、shell 和下载接口。

## 执行链

1. `schematic.create` 新建并结构回读 MN0/MP0 反相器 core。
2. 首次 live 回读发现 core 没有 ADE testbench 的 source/load。新增受限 `schematic.transform`，在同一 cellview 保留 MN0/MP0/pins，只增加 `VDD0/VIN0/CL0/GND0` 并把地改为 `gnd!`；没有重建或复制整个 schematic。
3. `parameters.apply` 把 `CL0.c` 从 `2f` 改为 raw 变量引用 `CL`，立即定向回读和独立再次回读都为 `CL`。
4. `ade.prepare` 新建 Maestro view/test；`ade.setup.apply` 保存 `tran stop=300p maxstep=1p`、`Vin`、`Vout` 和 `VoutAvg=average(VT("/OUT"))`，其中 `VoutAvg` spec 为 `>0.1`。
5. `ade.variables.apply` 以旧值 `null` 为前置条件保存 global `CL=1f,2f,4f`，即时与独立重开回读一致。
6. `ade.run` 执行原生 3 点 sweep。首轮 EDA 完成，但 VDA 原先假设 exact history 会保留逐点 `input.scs`，因此严格证据门失败并保留 manifest。
7. 只读检查真实 IC6.1.8 产物后确认：逐点结果集中在 `Interactive.0.rdb`，runtime 只保留一个符号 point netlist；`input.scs` 又通过 `include "netlist"` 与器件结构文件分离。VDA 增加窄兼容模式，要求这两个 runtime 文件、exact-history RDB 和 completion log 共同闭合，原有逐点文件模式仍保留。
8. 固定 `resume_history=Interactive.0` 和原 runtime scratch 再读取；最终成功，没有重新仿真、OA 写入或 Maestro setup 保存，并恢复了临时 session 目录。

## 最终证据

### Setup 与 OA / Spectre 输入

- setup 前后 tests、corners 和 global `CL=1f,2f,4f` 指纹相同：`bridge_readback`；
- source design：`vb_pdk_smoke/vda_ade_sweep_inv_001/schematic`；
- OA `CL0.c=CL`，真实 Spectre 结构网表 `CL0 (OUT 0) capacitor c=CL`；
- runtime `input.scs` SHA-256：`a85ca97b52978b24b5e70c5b14083f516993de2f00305a666a8ce8949c3987de`；
- 其显式包含的 sibling `netlist` SHA-256：`196eade700099c4c870cdbc72ab578341198f8b7f2dc80557239c6bc9876bc15`；
- 两文件输入束 SHA-256：`486b42619c285332feff1f7ffdc661d3324acf93c075e196eaac168d0b1fa564`；
- 保留的 runtime point 值为 `CL=1f`，它只证明共享符号输入束对应一个声明点，不被重复包装成三个逐点文件。

### Exact-history 结果

`Interactive.0.log` 明确给出 `Number of points completed: 3`、`Number of simulation errors: 0` 和 completed 状态。Bridge 从同一 `Interactive.0` RDB 读到：

| point | 声明 CL | RDB CL | VoutAvg | spec |
| ---: | ---: | ---: | ---: | --- |
| 1 | 1f | 1f | 415.5 mV | pass |
| 2 | 2f | 2f | 419.8 mV | pass |
| 3 | 4f | 4f | 428.3 mV | pass |

每个 point 的声明值、RDB 参数、非空 scalar output、共享输入束、exact-history RDB 和逐点聚合指纹都由 executor 再核对。最终 run record 明确记录：

- `sweep_point_evidence_mode=maestro_exact_history_rdb_with_shared_symbolic_runtime_input`；
- `native_sweep_database_binding_verified=true`；
- `exact_point_input_result_binding_verified=false`；
- `effective_simulation_values_verified=true`；
- `simulation_performed_by_this_invocation=false`；
- `oa_write_performed=false`；
- `maestro_setup_write_performed=false`；
- `runtime_directory_restored=true`。

这里将不存在的逐点输入标为 `false` 是证据边界，不是降级失败。若某个 Cadence 环境实际出现任意逐点目录，VDA 会坚持原有 exact-point 模式并要求所有 point/test 的输入与结果完整；不能用 RDB 模式掩盖半套逐点产物。

## 证据来源

- 目标、预期 tests/scope/points 和 OA binding：`user_input`；
- Maestro setup 与 OA readback：`bridge_readback`；
- runtime Spectre 输入束、exact-history RDB/log、Detail point 参数和 output：`eda_result`；
- 单位等价、OA/input 对应、输入束与逐点聚合指纹：`software_inference`。

return code、cellview 存在、RDB 文件存在或 history completed 任一单项都不足以通过此 Gate。

## 失败恢复价值

两次证据失败都发生在 EDA 已完成之后：第一次是假设了不存在的逐点目录，第二次是只解析 `input.scs` 而没有跟随其 sibling `netlist`。两次都保留 manifest、没有把运行成功冒充闭环，也没有重算。修正限制在 VDA 证据发现与核对层；第三方 Bridge checkout 未修改，因此后续 Bridge 更新不需要合并私有补丁。

## 回归与未验证边界

- 最终本地回归：`288 passed`；
- `59/59` example task plans 可编译；
- 单 test、单 transient analysis、单 global variable、nominal TSMC N28 已 live；
- test/corner scope、二维 `VDD×CL`、有限 corner、多 test/multi-analysis RDB 形状尚未 live；
- `Vin`/`Vout` 波形 output 在 Detail 中没有 scalar 值，当前严格门只使用非空 `VoutAvg`；delay、rise/fall skew、overshoot/undershoot、动态能量和 VDA constraint 映射尚未进入此 ADE sweep；
- history 名与覆盖策略仍来自保存的 Maestro setup，VDA 没有证明 `Interactive.0` 在运行前不存在；
- 人工打开 ADE、修改、重跑并交叉检查相同 setup/history 的 Gate 仍按计划延期。

下一自动化 Gate 是把有物理意义的反相器 delay/skew/energy output 纳入保存的 Maestro setup 和 VDA constraint 判定，再做受限 `VDD×CL` 二维 sweep 与有限 corner；不能用本次宽松的 `VoutAvg > 0.1` smoke 代替设计质量闭环。
