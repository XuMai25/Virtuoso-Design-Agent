# 2026-07-22 ADE 原生 sweep 逐点同源证据本地实现

## 结论

VDA 已为 `ade.run` 增加一个可选的严格 `sweep_verification` 契约。任务只有显式启用该契约时，才要求一个保存的 Maestro 原生 sweep 同时满足：

```text
exact tests / optional corners
  -> 声明 scope 的变量值回读
  -> Bridge run_and_wait 或显式 history 恢复
  -> ADE Detail 每个 point 的有效变量值和非空 scalar output
  -> exact-history 每个 point/test 的 input.scs 与非空结果
  -> OA 参数引用变量，Spectre 输入采用该 point 的有效值
  -> 运行后 setup 再回读且未变化
```

本轮只完成本地契约、worker、executor、失败测试和 TSMC N28 任务链，没有连接远端、写 OA、保存 Maestro setup 或运行 Spectre。因此当前状态是 **native Maestro sweep exact-point input/result consistency contract implemented locally**，不是 live sweep verified。

## 任务契约

`ade_run.sweep_verification` 声明：

- exact `expected_tests` 和可选 `expected_corners`；
- 一个或多个 global/test/corner sweep variable，以及该 scope 保存的完整逗号值字符串；
- 从 1 开始连续、无重复的 expected points；
- 每个 test/variable 对至少一个明确的 `instance.oa_parameter` 绑定。

启用后必须同时保留：

- `require_structured_outputs: true`；
- `require_artifact_manifest: true`；
- `require_simulator_input_consistency: true`。

不声明 `sweep_verification` 的既有 `ade.run` 行为没有收窄；Bridge 仍可运行任意已有 setup。严格点证据只是调用方主动选择的附加 Gate。

## Worker 证据门

运行前，worker 使用已有 Maestro/Bridge 接口读取 tests、可选 enabled corners 和声明 scope 的变量值。任何目标、scope 或逗号 sweep 字符串不一致都会在 `run_and_wait` 前停止。

运行或恢复后，worker 对每个 expected point：

1. 要求 Detail 表中存在同号 point、全部声明变量和值，并至少有一个非空 scalar output；
2. 要求 exact-history 下每个 test 至少存在一个非空 `input.scs` 和一个非空结果产物；
3. 重新读取 `input.scs`，确认内容 SHA-256 与 manifest 相同；
4. 核对 Design header、OA 实例集合、节点和已知 primitive raw 参数；
5. 对声明绑定，要求 OA raw 参数字面引用变量名，并要求 Spectre 实例已经解析成 point 值，或 `parameters NAME=value` 给出该 point 的有效值；
6. 将 Detail point、input hash、OA/input comparison hash 和 result hash 组成逐点指纹；
7. 运行后再次读取 setup，要求 tests/corners/变量及指纹与运行前完全相同。

当前已知 primitive contract 继续限制在 VDA 明确核对过的 MOS、`analogLib/cap`、`res`、`vdc` 和 `vpulse`。未知 primitive 或未知参数映射会失败，不会降级为“已同源”。

## 证据来源

- task 中的 expected tests、scope、points 和 OA binding：`user_input`；
- Maestro setup scope 与 OA schematic：`bridge_readback`；
- Detail 参数/output、exact-history `input.scs` 和结果产物：`eda_result`；
- point/input/result 对应、单位等价比较和聚合 SHA-256：`software_inference`。

executor 会重新检查上述来源、setup 前后指纹、Detail 结果参数的单位等价值、每个 point/test 的 input/result manifest 行、OA/input comparison 一一对应关系和逐点绑定 SHA-256。adapter 只返回布尔值、内部自洽但点值错误的指纹，或少一类证据都不能通过。

## 本地失败覆盖

测试明确拒绝：

- point 编号不连续、变量集合不同、重复组合或未绑定变量；
- sweep verification 关闭 structured output、artifact manifest 或 input consistency 任一门；
- Detail point 的变量值与声明不一致；
- point 只有空 output；
- point/test 缺少 exact-history `input.scs`；
- point/test 缺少非空结果；
- 多 test 时目录未标注 test，防止同一 input/result 被重复归给多个 test；
- OA 参数没有引用声明变量；
- Spectre 有效变量值与 point 不符；
- 输入内容在 manifest 后改变；
- setup 在 background run 前后改变；
- executor 收到来源、manifest、结果参数、OA/input 对应或逐点指纹不完整的 adapter 证据。

本地回归为 `276 passed in 0.56s`；全部 `58/58` example plans 通过。

## 可执行任务链

新增六个独立任务，目标均为非覆盖专用 cell `vb_pdk_smoke/vda_ade_sweep_inv_001`：

1. `inverter-ade-sweep-create.bridge.json`：新建并回读 inverter schematic；
2. `inverter-ade-sweep-bind.bridge.json`：把 `CL0.c` 改为 raw 变量引用 `CL` 并双重回读；
3. `inverter-ade-sweep-prepare.bridge.json`：新建同 cell 的 Maestro view/test；
4. `inverter-ade-sweep-setup.bridge.json`：CAS 配置 transient 和 `VoutAvg` output/spec；
5. `inverter-ade-sweep-variables.bridge.json`：CAS 保存 `CL=1f,2f,4f`；
6. `inverter-ade-sweep-run.bridge.json`：后台运行并要求三个 point 的完整同源证据。

这些文件带真实安全声明，但仍必须经过 CLI plan token、`--execute` 和一次明确 live 授权；文件存在本身不构成远端执行授权。

## Live 前仍需证实的边界

1. IC6.1.8 对这类 global variable sweep 是否确实在 exact history 生成 `1..N/test/.../input.scs`；若实际路径或变量文件不同，VDA 会保留 manifest 并失败，不会把 central netlist 冒充逐点输入。
2. 当前输入解析只接受实例参数已解析为数值，或直接的 Spectre `parameters NAME=value`。若 Cadence 把有效值放进独立 `variables_file`/parameter-run 文件，需要在看到真实只读产物后增加窄解析，不预先猜格式。
3. `VoutAvg > 0.1` 仍只是 sweep 链路 smoke，不是设计质量规格；delay、rise/fall、能量和 VDA constraint 映射属于下一 Gate。
4. corner、多 test/multi-analysis 的真实目录形状、二维 `VDD×CL`、history 名唯一性和人工 ADE 交叉检查尚未验证。
5. 本轮没有修改 `virtuoso-bridge-lite`。标准 Bridge Maestro、shell、download 和 SKILL channel 仍是唯一远端执行与传输机制。
