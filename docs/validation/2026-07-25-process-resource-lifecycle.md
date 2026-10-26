# 2026-07-25 进程与资源生命周期审计

> 同日 follow-up 已完成 ADE/Maestro 硬中断实机故障注入，并新增只读 retention/pin/age/size 盘点。见[资源取消、盘点与保留策略 follow-up Gate](2026-07-25-resource-cancellation-retention.md)。本页以下“未闭合”条目保留为首轮 direct Gate 当时的状态。

## 结论

本 Gate 修复并真实验证了 direct Bridge worker 与 direct Spectre 的进程边界。VDA 每次 Bridge request 直接启动 Bridge Python worker，不启动 PowerShell；正常路径只复用一条固定的 SSH tunnel/jump chain。Windows 超时或调用方中断现在由 Job Object 清理完整本地后代树，worker 正常或 action-error 退出时显式关闭本次 Bridge client。远端 Spectre 由哈希匹配的 timeout guard 限定最大运行时间，子运行目录归属到唯一 VDA root。

这不能表述为“所有 EDA 资源都已永久零占用”。共享 SSH tunnel 是有意常驻的固定资源；run records、`si` 网表和仿真/characterization 证据是有意持久化的磁盘资源。ADE/Maestro 硬中断和通用证据 retention 仍未闭合。

## 调用链与 PowerShell 边界

VDA adapter 的实际命令是：

```text
<virtuoso-bridge venv>/python.exe
  -m virtuoso_design_agent.adapters.bridge_worker
```

worker 内部复用 Bridge 的 `ssh.exe`、`scp.exe`、Windows `tar.exe`、远端 `si`、Spectre 和 Virtuoso SKILL/OA。PowerShell 只来自当前 Codex 桌面任务的宿主 shell 或用户手工命令，不在 VDA→Bridge request 链中。worker、Bridge 的一次性 Windows subprocess 和新进程树清理命令都使用 hidden/no-window flags。

## 本地进程证据

修复前基线只有同一条 tunnel/jump chain 的两个 `ssh.exe`：约 13.3 MiB 和 14.1 MiB，句柄数分别 153/151；没有 Python、SCP、Spectre 或本地 Virtuoso。连续执行 5 次真实只读 `schematic.inspect`，每次包含 probe + inspect，共 10 个 Bridge worker request：

- 5 次均成功，单次 1.60–1.97 秒；
- SSH PID/启动时间和句柄数保持不变；
- working set 只出现约 1.1 MiB 的非线性驻留变化；
- 结束后 Python/SCP/Spectre 为 0；
- `%TEMP%` 中 `vda_*`/`virtuoso*` 目录为 0。

第一次合成超时测试让 worker 生成 120 秒 Python child。原实现的 `taskkill /T` 返回后 child 仍活着，证明“父进程退出”不足以作为清理证据。改用请求专属 Windows Job Object 后，同一测试在 1 秒 timeout 后确认父子 PID 都消失；另有单测覆盖 `KeyboardInterrupt` 清树后继续向调用方抛出，以及 action 失败时逆序关闭全部已注册 client、单个 close 失败不短路其他 close。

真实 read-only OA inspect 在新 Job Object 路径下成功。随后一次 CAD transport timeout 触发 Bridge tunnel recovery，旧的两个 SSH PID 被新的两个 PID 替换，而不是叠加；新链句柄仍为 153/151。最终本地仍只有这两个共享 SSH，无 Python/SCP/Spectre 和 `vda_*` temp。

Gate 8 完成后又做了一次面向真实长任务的复核。一次只读 inventory 得到远端
`spectre=0`、`si=0`、Maestro session=0；连续两次新的 OA inspect 分别成功返回后，本地
仍是完全相同的 `ssh.exe` PID `38784/41248`，启动时间都为 `2026-07-25 16:29:14`，
没有残留 Python 或 SCP。两次 inspect 没有造成 PID 替换或计数增长。这一结果继续支持
“固定双层 tunnel/jump chain 被跨请求复用”，不支持“每个 Bridge/VDA 调用都应当留下
零 SSH”。因此没有采用会在每次正常 worker 退出时终止共享 tunnel 的实现；该做法会
破坏复用并显著增加下一次只读请求延迟。Bridge 本身在本轮保持未修改。

## 远端进程证据

一个唯一 scratch root 下的合成 `sleep 60` 由一次性 SSH 执行，本地 2 秒 timeout 后独立检查得到 `child_alive_after_timeout=1`。该 child 已按记录的精确 PID TERM/KILL，探针 root 随即删除；前后远端 `spectre/si/virtuoso` 均为 0。这是本轮确认的远端孤儿风险，不能归类为电路失败。

VDA direct Spectre 因此安装：

```sh
exec timeout --signal=TERM --kill-after=10s <task-timeout>s spectre "$@"
```

脚本只位于目标 `/data/xum/.../vda_<task>_<nonce>/`，上传后执行 `chmod 700` 和远端 `sha256sum`；返回 hash 必须与本地内容匹配。Bridge 的等待 timeout 为 `<task-timeout> + 15 s`，让 guard 先完成清理。SpectreSimulator 的 `remote_work_dir` 也固定为同一 VDA root。

最小无 PDK、无 OA 的电阻分压 Spectre probe 真实得到：

- `simulation_ok=1`；
- `guard_status=installed_and_hash_matched`；
- simulator 子目录位于唯一 probe root；
- Bridge 下载和清理后 root 内只剩 guard；
- probe root 随后精确删除。

随后把同一个 guard 的 PATH 指向唯一假 `spectre`：外层 shell 和内层 `sleep 60` 都忽略 TERM。guard 在 5 秒预算后发 TERM、10 秒后发 KILL，真实返回 `137`，总时长 `15.984 s`；独立 `kill -0` 得到 `fake_spectre=dead`、`sleep=dead`，精确 probe root 删除成功。这证明二级强杀覆盖远端后代，而不是只终止一层 shell，且没有调用 Cadence license。

运行刚返回的瞬间进程计数短暂为 2；独立延迟复查读取 PID/PPID/state/elapsed/name 时已为 0，因此记录为有限退出延迟，不作为泄漏，也不把瞬时返回码当成清理证明。

## 真实 Gate 6 DC smoke

目标为 `vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`，`allow_remote_compute=true`、`allow_remote_write=false`、`replace_existing=false`。预计路径为唯一 `/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-final-dc-bridge_<nonce>`。

前两次尝试都在 Spectre 启动前失败：第一次是 VDA 把底层 `CommandResult.returncode` 错按高层 `.ok` 判断；第二次在下载 `si.env` 时发生 CAD SSH timeout。两者分别保存在本地失败 run record，未分类为电路不可行。只读 inspect 确认通道恢复后第三次成功，run record 为：

```text
artifacts/runs/resource-lifecycle-audit/spectre-guard-dc-recovered.json
```

成功记录中的 lifecycle 证据：

- remote guard：`..._bc504a455b09/vda_spectre_guard.sh`；
- SHA-256：`1b0aec65f31569bc37e835b9205e7d32cd519b654424afee37501d2b5f3ba24b`；
- 独立远端 readback：hash matched；
- Spectre timeout 900 秒，kill-after 10 秒，Bridge wait 915 秒；
- 结束后的独立远端 `spectre/si/virtuoso` 进程为 0。

这次 DC 的 OP/KCL/工作区结果属于 `eda_result`，OA/netlist/guard 回读属于 `bridge_readback`，guard 配置与清理判读属于 `software_inference/system_event`。本记录只证明资源路径和既有单点 DC 仍能执行，不据此重复宣称新的设计质量闭环。

两次启动前失败产生的精确远端 root `..._0abb21873d3f`（78,739 B）和 `..._5e84e1f1fd1a`（1,189 B）在路径/nonce 校验后删除；它们不可恢复，但本地失败 records 保留。成功 root 和历史证据未删除。

## 磁盘快照与保留策略

审计结束时：

- 本地 `artifacts/runs`：95 个顶层 run 目录、377 个文件、55,699,791 B；
- 本地 `%TEMP%` 匹配 `vda_*`/`virtuoso*`：0；
- 远端 `/data/xum/virtuoso_bridge_smoke`：431 个顶层目录、335,917,056 B；
- 其中 `vda_*` 为 384 个；顶层 8 位 hex simulator 目录为 0；
- 46 个顶层目录早于 7 天，均不在本轮精确删除范围内。

这里的 335.9 MB 不是进程泄漏。它主要是跨 Gate 保留的 `si`、wrapper、characterization 和失败证据，但会随项目继续增长。当前策略是不在 worker 退出时做广泛删除：成功/失败证据的 retention 必须先定义保留期、pin/manifest 规则和 dry-run，再提供精确清理入口。未经该 Gate 不删除历史目录。

## 第三方边界

本轮只读审查了 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite` 的 subprocess、tunnel 和 Spectre cleanup 实现，没有修改 Bridge。Bridge 工作树保持在用户的隔离分支 `codex/vda-transport-recovery` 且无本轮改动。所有新增行为位于 VDA worker/adapter；因此后续 Bridge 更新不需要合并一份新的本轮第三方补丁。

## 未验证边界与下一 Gate

- `ade.run` 的正常 session `finally` 已代码审查，但尚未对 worker hard-kill 注入并证明远端 Maestro session/history process 自动收敛；direct Spectre guard 不覆盖 ADE 内部启动的 Spectre。
- 共享 tunnel 在正常 worker 退出后有意保留；本轮证明固定为一条两进程链并可恢复替换，但没有把“每次任务后 stop tunnel”设为策略。
- 机器掉电、远端主机重启或 Python 被 `TerminateProcess` 时，本地 `TemporaryDirectory` 可能留下旧目录；当前启动前快照为 0，尚无跨重启 stale-temp sweeper。
- 持久证据没有通用 retention CLI。下一资源 Gate 应先做只读 inventory + manifest pin + age/size dry-run，再考虑用户显式确认的精确删除。
- remote guard 的 TERM/KILL 已用忽略 TERM 的假 Spectre 进程树真实验证，真实 Spectre 本轮覆盖正常完成；故意让 Cadence Spectre 超时仍未执行，以避免无必要消耗 license/compute。
