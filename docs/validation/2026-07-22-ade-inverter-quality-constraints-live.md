# 2026-07-22 反相器 ADE 质量指标与 VDA constraints live Gate

## 结论

在 `nics4304_tsmc28` / IC6.1.8 / Spectre 21.1.0 上，目标
`vb_pdk_smoke/vda_ade_sweep_inv_001/maestro` 已完成以下真实链路：

```text
saved Maestro calculator outputs
  -> native CL=1f,2f,4f sweep
  -> exact-history RDB scalar values
  -> explicit SI-to-ps/fJ normalization
  -> VDA constraints + objective
  -> selected feasible point
```

最终状态可表述为 **live native Maestro inverter timing/supply-energy result
mapping and constraint evaluation verified**。它证明保存的 ADE setup 既可供人工打开，
也能被 VDA 后台运行、严格取证和选优；还不是二维 VDD×CL、corner 或 L5B 闭环。

## 保存的测量定义

本次只对既有 Maestro view 做 add-only patch，不改 schematic、CL sweep、analysis 或
已有 `Vin/Vout/VoutAvg`。新增并独立重开读回：

- `Tphl`：第二个稳定周期的 IN 50% 上升沿到 OUT 50% 下降沿；
- `Tplh`：同周期 IN 50% 下降沿到 OUT 50% 上升沿；
- `Delay=(Tphl+Tplh)/2`；
- `Rise/Fall`：OUT 10%–90%/90%–10%；
- `RiseFallSkew=abs(Rise-Fall)`；
- `SupplyEnergyCycle`：两个相邻 IN 上升沿之间 `-VDD*integral(I(VDD0))`。

最后一项包含该周期内的泄漏与短路电流，不称为纯开关能量。当前 VDD 固定为
0.9 V，时序阈值也按 0.9 V 固定；切换 VDD 前必须改为显式 `VAR("VDD")`
表达式并重新通过 Gate。

## 表达式与人工 ADE 状态交接

IC6.1.8 会把 `80p` 打印为 `8e-11`，并为二元表达式添加括号。首轮 setup
patch 因逐字符串比较在 `save_setup` 前失败，第二轮加入只解析、不执行表达式的
保守 AST 比较后完成保存。该比较只接受括号与 Spectre 工程单位规范化；不同阈值、
边沿、信号、运算顺序或不支持语法不会被判成相同。

第二轮 adapter 已证明 7 个 output 写前均不存在、一次保存、独立重开均存在、
`existing_outputs_replaced=false`、`schematic_oa_write_performed=false`。当次总
run record 仍因 executor 中另一处旧逐字符串比较而标为 failed；修复后没有重放
setup 写入。

`ade.run.result_mapping` 现在要求每个映射 metric 同时声明：

- exact test/output；
- expected calculator expression；
- VDA metric；
- 显式 scale 和展示 unit。

worker 在运行前后读取实际 saved output expression 和完整 output state，指纹必须
相同；executor 再独立复核声明表达式、指纹和证据来源。最终恢复记录中的 setup
fingerprint 为：

```text
abf61584c09193898d87155f57c93472519832d4819b27611485d1fed4ccc262
```

这会检测人工修改同名 output 的情况，不把旧语义和新 history 静默混用。

## 真实结果

`Interactive.1` 完成 3 点、0 simulation errors。数值为 RDB 显示精度：

| CL | Tphl (ps) | Tplh (ps) | Delay (ps) | Rise (ps) | Fall (ps) | Skew (ps) | Supply energy/cycle (fJ) | constraints |
|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| 1 fF | 3.354 | 2.409 | 2.882 | 3.175 | 4.398 | 1.223 | 1.659 | pass |
| 2 fF | 4.450 | 3.081 | 3.766 | 4.363 | 6.475 | 2.112 | 2.497 | pass |
| 4 fF | 6.592 | 4.413 | 5.503 | 6.857 | 10.800 | 3.939 | 4.147 | pass |

VDA 约束为 delay ≤ 6 ps、skew ≤ 5 ps、周期供电能量 ≤ 5 fJ；objective 为
最小化周期供电能量。三点都可行，选中 CL=1 fF。`Delay` 与
`(Tphl+Tplh)/2`、`Skew` 与 `abs(Rise-Fall)` 在 RDB 舍入精度内一致。

原始 RDB 字符串属于 `eda_result`；scale、metric 命名、constraints 判定与选优
属于 `software_inference`；saved setup/output 状态属于 `bridge_readback`；任务中
的阈值、映射和规格属于 `user_input`。

## 同源与产物证据

最终记录使用 IC6.1.8 已验证的 database 模式：

- `exact_point_input_result_binding_verified=false`；
- `native_sweep_database_binding_verified=true`；
- runtime `input.scs` + sibling `netlist` 输入束 hash：
  `9edd85e108310bf5e1cdb4888d2b116ec96705f31460dd7485d32dec4831035e`；
- exact-history RDB：540672 bytes，SHA-256
  `9322da3dc1b255994cfc3c4356d1af2e82fd76e5adeff6e3813bbdfcba547cd8`；
- completion log：3 points、0 errors，SHA-256
  `119912b9aad426bf6d246d41a15af89e2fa1f8c590e912481d7e4645de493b16`；
- simulation fingerprint：
  `743549b051c041eec4d8b05f3d70a318efc1bcf935d8b0db78e1bcd1c16ae895`；
- manifest：58 simulator-input、1 eda-result、2 run-log。

OA→Spectre 仍核对 5 个真实器件、连接和 19 组 raw parameter；`CL0.c=CL`、
Spectre `c=CL`、RDB 每点 CL 值一致。MOS `Wfg` 仍按 PDK CDF 派生语义排除，
没有包装成字面 `w` 相等。

## 恢复路径

`05-quality-resume-20260722.json` 固定读取 `Interactive.1` 和原 runtime
scratch：

- `simulation_performed_by_this_invocation=false`；
- `history_recovery_performed=true`；
- expression setup fingerprint 前后相同；
- `runtime_directory_restored=true`；
- `oa_write_performed=false`；
- `maestro_setup_write_performed=false`。

因此补充 expression 状态指纹没有重跑 Spectre，也没有再次保存 setup。

## 本地回归

- `python -m pytest`：`314 passed`；
- 全部 `examples/tasks/*.json`：`61/61` 可生成计划；
- 新 quality run 计划明确披露 expression pin、scalar scale、constraints/objective
  与四类证据来源。

## 记录

- setup 首轮未保存失败：
  `artifacts/runs/ade-inverter-quality-live/01-quality-outputs-20260722.json`
- setup 已保存但本地最终门失败：
  `artifacts/runs/ade-inverter-quality-live/02-quality-outputs-retry-20260722.json`
- 首次真实运行与映射成功：
  `artifacts/runs/ade-inverter-quality-live/03-quality-run-20260722.json`
- expression-pinned 恢复任务：
  `artifacts/runs/ade-inverter-quality-live/04-quality-resume-task-20260722.json`
- 最终只读恢复成功：
  `artifacts/runs/ade-inverter-quality-live/05-quality-resume-20260722.json`

## 尚未闭合

1. 当前只扫 CL；VDD 尚未成为 OA/Spectre 符号变量，二维 VDD×CL 未执行。
2. 当前 0.9 V calculator expressions 不能直接用于 VDD sweep。
3. corner、多 test/multi-analysis、已有 output 安全替换仍未 live。
4. 尚未以独立导出波形再次交叉计算这些 ADE scalar；本次算术自洽不等于独立
   测量器交叉验证。
5. history 名称唯一性仍未证明；database 模式也没有虚构逐点 `input.scs`。

下一自动化 Gate 应先以显式授权把 `VDD0.vdc` 与 `VIN0.v2` 变成同一个 VDD
变量，新增不覆盖旧 output 的 VDD-aware calculator expressions，再执行受限
`VDD×CL` 6 点 sweep。corner 在二维输入、结果映射和恢复路径闭合后再进入。
