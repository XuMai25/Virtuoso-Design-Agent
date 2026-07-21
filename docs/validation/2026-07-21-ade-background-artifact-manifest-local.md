# 2026-07-21 ADE background exact-history 产物清单本地实现

## 目标

在既有 `ade.run` 后台运行与结构化结果回收之上，为 `run_and_wait` 本次返回的确切 history 建立 simulator input、结果和日志的只读大小/SHA-256 清单。该能力只消费已保存 setup，不修改变量、analysis、output、corner 或 schematic。

## 实现

- planner 将远端清单生成纳入 `remote_compute`；执行仍需 `allow_remote_compute: true`，不需要 OA 写授权。
- worker 继续复用 Bridge 公共 `open_session`、`run_and_wait`、`read_results` 与 `close_session`，并用公共 `run_shell_command`/`download_file` 完成远端哈希和小型 TSV 回收。VDA 没有复制 SSH/file-transfer，也没有修改 Bridge。
- 通过 SKILL channel 读取 library path 与当前 background session 的 `asiGetAnalogRunDir`，只接受 `/data/xum` 下且包含声明 `library/cell/maestro/results/maestro` anchor 的路径。
- project 与 scratch 两个 Maestro 根都固定到本次 `<history>`；不会按 mtime 或“最新 history”重新选择。每条记录必须位于该 history 子树或其同名 `.log/.rdb/.msg.db` companion。
- 只枚举核心 `netlist`、`input.scs`、辅助输入、普通 PSF/结果和 Spectre log。远端清单仅包含路径、字节数和 SHA-256；完整波形不下载。
- project/scratch 中同一逻辑路径的大小/哈希相同则去重，不同则拒绝歧义。非空 `netlist`、`input.scs`、至少一个结果和至少一个日志是完整清单的硬门。
- 新增默认 `require_artifact_manifest: true`。显式设为 `false` 且清单失败时保留原因，但 executor 只能记为 `partial`。原有 `require_structured_outputs` 仍独立生效。
- 远端 TSV 保留在 PDK profile 的 `/data/xum` run root 下，并将目录、来源根、entry count 与 TSV 自身哈希写入 run evidence，便于中断后定位。

## 证据语义

- setup tests：`bridge_readback`
- history、逐 point output/spec、simulator input/result/log 的大小与 SHA-256：`eda_result`
- 清单路径、收集方式和 project/scratch 来源：证据定位元数据
- 清单完整性、冲突和 partial 判定：VDA 执行规则

`artifact_history_path_binding_verified: true` 只表示清单路径锚定到返回的 history 名。它不证明该名称在运行前不存在，不排除已保存 setup 复用/覆盖同名 history，也不证明声明 design variable 已按预期语义进入 `netlist`。

## 本地验证

- worker 模拟覆盖 project/scratch 合并与去重、双根内容冲突、跨 history 路径混入、空核心网表、缺结果/日志、manifest transport 中断、结构化结果 history 不一致和所有失败路径的 session close。
- shell command 测试约束为单行、完整 shell quoting，避免 Bridge SKILL string 中出现原始换行。
- executor 覆盖完整证据成功、显式允许清单缺失时 partial，以及伪造 history/manifest 关系拒绝。
- model/planner/subprocess 覆盖新 requirement 默认值、计划披露和 payload 透传。

全量结果：`242 passed in 0.51s`。全部 `examples/tasks/*.json` 重新生成计划：`52/52 example plans passed`；`existing-maestro-run.bridge.json` 已显式要求结构化结果与 exact-history manifest，计划只含 `remote_compute`，没有 `remote_write`。

## 未验证边界

- 尚未在 nics4304/Virtuoso 6.1.8 上执行真实 background Maestro run；本记录只证明本地契约和失败门。
- nics4304 上 `asiGetAnalogRunDir` 的真实返回形状、project/scratch 实际分布、`sha256sum` 可用性、二进制/空结果形态和远端 TSV 下载仍需 live smoke。
- 当前不解析清单内 netlist 内容，因而尚未核对 setup CAS 的变量值真正进入 simulator input；下一 live Gate 必须在专用 cell 上执行“setup patch → background run → result/manifest → netlist 参数核对”。
- history 命名/覆盖策略来自已保存 setup，当前不能在 run 前证明名称唯一。exact-history 路径哈希没有解除这条风险。
- 当前没有 background-run checkpoint/resume，也不自动清理远端 manifest。transport 中断后必须先检查 history/manifest，不能盲目重跑。
- ADE output 尚未映射为 VDA constraints/candidates，人工 ADE/ADE L 延期项目也未因此完成。

本次没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`；第三方仓库保持既有隔离分支与干净工作树。
