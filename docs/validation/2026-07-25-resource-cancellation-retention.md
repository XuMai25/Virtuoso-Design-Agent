# 2026-07-25 资源取消、盘点与保留策略 follow-up Gate

## 结论

本 Gate 闭合了上一轮尚未实机故障注入的 ADE/Maestro 中断路径，并把本地临时资源、远端 EDA 进程、Maestro session 和持久证据目录纳入同一个只读盘点入口。对当前已识别的 VDA 所有者路径，正常返回、action error、CLI timeout、`KeyboardInterrupt` 和调用方消失都已有有界退出或可见的审计状态；没有通过每次任务停止共享 Bridge tunnel 或删除历史证据来制造“零占用”假象。

这不是对断电、操作系统崩溃、远端主机故障或不可中断内核调用的绝对保证。若 Python 在 30 秒协作窗口内不能展开，Windows Job Object/进程组仍会强制清理本地树；远端 direct Spectre 继续由独立 timeout guard 收敛，Maestro session 则会在后续 `vda resources --remote` 中显式暴露，不能静默当作已清理。

## 实现边界

每个 Bridge worker 获得唯一 `%TEMP%/vda_bridge_cancel_<uuid>.flag` 和调用方 PID。调用超时或中断时：

1. adapter 原子创建 cancel marker；
2. worker watchdog 每 200 ms 检查 marker 与父 PID，并用主线程中断让 Python `finally` 展开；
3. Maestro action 先恢复临时 project/results/analog-run 路径，再 `maeCloseSession(forceClose)`；
4. worker 逆序关闭本次 `VirtuosoClient`/`SSHClient` 并删除 marker；
5. 最迟 30 秒后仍未退出时，Job Object 或 POSIX process group 清理本地 worker、SSH/SCP/tar 后代。

正常 worker 仍不会停止 Bridge 共享 tunnel。direct Spectre 的远端 timeout/TERM/KILL guard 没有改变。

新增 `vda resources` 只做 dry-run inventory，不执行删除。它报告：

- `artifacts/runs` 顶层证据的 age/size/file count；
- `%TEMP%` 中 VDA cancel marker 和已知 simulation/manifest temp；
- `/data/xum` profile run root 的顶层目录 age/size；
- exact-name `spectre`/`si`/`virtuoso` 进程数；
- Maestro session、每个 test 的 runtime path 以及是否属于 `vda_ade_run_*`；
- exact-path pin manifest 对 review candidate 的抑制。

pin 只表示保留意图，不授权删除。历史证据和远端 run root 不能在 worker 退出时广泛 `rm -rf`。

## 真实 Maestro 中断故障注入

目标为既有 `vb_pdk_smoke/vda_ade_sweep_inv_001/maestro`。本次不保存 setup、不写 OA、不启动 Spectre，只在新 background session 中临时设置 VDA runtime 路径并保持等待：

- session：`fnxSession102`；
- runtime root：`/data/xum/virtuoso_bridge_smoke/vda_ade_run_resource-cancel-live-20260725_09bf184963e1`；
- 外层 timeout：15 秒；
- 返回：`cooperative cleanup completed`，总时长 17.887 秒；
- cleanup record：`runtime_restored=true`、`session_closed=true`。

用于这次故障注入的私有 worker action 在验证后已从产品 action map 和源码删除。独立远端盘点随后得到：

- `spectre=0`；
- `si=0`；
- Maestro session 总数 0，VDA-managed 0；
- 本地 Python worker 0、cancel marker 0；
- 远端 `virtuoso=2` 是既有 Cadence/Bridge 会话进程，不属于本次 worker，不能由 VDA 擅自终止。

runtime root 只包含 ADE 创建的空目录树，`psf` 与 `netlist` 均为空。逐层只读确认后，仅删除这个精确诊断 root，并用远端 `isDir` 回读为 `absent`；历史 run evidence 未删除。

## 磁盘和传输盘点

最终 ratio Gate 后的只读快照为：

- 本地 `artifacts/runs`：129 个顶层 entry，58,016,033 B；
- 本地已知 VDA temp：0；cancel marker：0；
- 远端 run root：437 个顶层目录，297,379,779 B；
- 早于 7 天的 review candidate：46；
- 删除执行：false。

当普通 Bridge SSH 因当前执行沙箱的 DNS/网络边界不可用时，盘点使用既有 Virtuoso SKILL channel 写入唯一 transient TSV、分段回读并在 `finally` 中精确删除；清理后用 `isFile` 验证 absent。该 fallback 只用于只读 inventory，不用于 Spectre 结果传输或 OA 写入。

本地示例 pin manifest 为 `examples/resource-retention-pins.example.json`。它只接受 artifact root 的单一顶层名称，或 `/data/xum/...` 下的精确路径；glob、`..`、宽泛根和重复项均拒绝。

## 证据与回归

主要本地证据：

- `artifacts/runs/resource-lifecycle-audit/maestro-cancel-ready-20260725.json`，SHA-256 `62232c5311e7e6db1ade81e049bfbd553284f7f525be3959ba320ee9b31b06d2`；
- `artifacts/runs/resource-lifecycle-audit/maestro-cancel-cleanup-20260725.json`，SHA-256 `a8aadf87f388ea0181db773b786ea8ab2736998ab7e0f6bc4322e92fdf7b11a1`；
- `artifacts/runs/resource-lifecycle-audit/resource-inventory-final-20260725.json`，SHA-256 `74909fb20659f7d72ec51770a66b28cc99cfdeec5ef89122aa9080bd94ff0f37`；
- `artifacts/runs/resource-lifecycle-audit/resource-inventory-post-refinement-20260725.json`，SHA-256 `a11e895fa0a8fa5cd861030950d3c2202aa1c31a338553bd956b5bfc4b9d9653`。
- `artifacts/runs/resource-lifecycle-audit/resource-inventory-final-code-20260725.json`，SHA-256 `a38131e73b7ac9af9f770383c8ad03ed92092739ddc1c4e44f105a040437a00d`；该文件由最终 CLI 代码生成，包含 0 个已知本地 VDA temp 的结构化字段。

runtime/session 回读与远端 inventory 属于 `bridge_readback`，取消、cleanup 和本地进程状态属于 `system_event`，age/size/review 判定属于 `software_inference`，pin reason 属于 `user_input`。`return code 0` 没有被单独用作清理证明。

本轮没有修改 `virtuoso-bridge-lite`。所有改动均位于 VDA adapter/worker/CLI；因此没有新的第三方补丁、备份分支或升级合并负担。

## 保留边界

- `vda resources` 有意不提供隐式 GC；review candidate 不是删除授权。
- 共享 tunnel 是固定、可复用资源，不应在每个 operation 后关闭。
- 成功/失败 run 的网表、guard、wrapper、raw bundle 和本地 JSON 是审计证据，会有受控磁盘成本。
- 机器崩溃后遗留的已知 `vda_*` temp 会被 inventory 显示，但当前不会自动删除；精确删除仍需单独审查。
- 故意让真实 Cadence Spectre 超时仍未执行；远端二级强杀已由忽略 TERM 的等价进程树验证，正常真实 Spectre 已多次验证。
