# 2026-07-27 Bridge 隐藏启动与状态回显验证

状态：**VDA launcher 与 Bridge OpenSSH tunnel 的隐藏启动均已做进程级 live 验证；补丁后用户视觉复核和 Bridge temp-log retention 仍待闭合**。

## 目标与边界

本 Gate 解决显式启动 Bridge 时反复出现空白终端窗口、却看不到进度的问题。第一版只修改 VDA，但用户截图证明它没有覆盖 Bridge 自己的长期 OpenSSH tunnel，因此最终包含两个聚焦层次：

- 新增 `vda bridge start|status|stop`，委托 Bridge 已安装的公开 console script；
- VDA 不复制 Bridge 的 SSH、profile、`.env`、daemon、state、文件传输或 Spectre 实现；
- Windows launcher 使用 `CREATE_NO_WINDOW + SW_HIDE + CREATE_NEW_PROCESS_GROUP`，并加入与 VDA worker 相同的 Job Object 所有权边界；
- stdout/stderr 回到当前 VDA 终端；默认过滤 `[cmd]` 原始 SSH 命令，`--verbose` 才保留；
- 正常完成只关闭 launcher 句柄，保留 Bridge 共享 tunnel；Ctrl+C 回收本次仍受控的启动进程树。
- Bridge 的 Windows tunnel 保留 `CREATE_NO_WINDOW + SW_HIDE + CREATE_NEW_PROCESS_GROUP`，移除与无窗口语义冲突的 `DETACHED_PROCESS`；修改隔离在第三方 checkout 的 `codex/vda-transport-recovery`，修改前恢复点为 `codex/backup-vda-window-hide-e74379a`。

本次真实 smoke 没有目标 library/cell/view，没有访问或写入 OA，没有创建 cellview，也没有运行 Spectre analysis。`status` 只连接既有 CIW daemon 并查询 Spectre 版本。Bridge 精确修改和升级恢复规则记录在 `docs/third-party/virtuoso-bridge-local-patch.md` 及其 checkout 的 `LOCAL_VDA_PATCH.md`。

## 测试与真实结果

新增七项本地测试覆盖：

1. 只从 Bridge venv 的 Python 旁解析 console script，不使用 `shell=True`；
2. Windows hidden-window flags 与 Job Object 正常关闭；
3. 默认可读状态和 `[cmd]` 过滤；
4. `--verbose` 诊断保留；
5. Ctrl+C 进程树终止与 pipe 关闭；
6. Job Object 建立失败时的子进程与 pipe 清理；
7. CLI 参数透传和缺失/启动错误报告。

完整 VDA 回归：`718 passed`。`vda bridge --help`、`vda bridge start --help` 和 `vda catalog` 均通过。

Bridge 新增一项可跨平台运行的 flags 测试，窗口修复相关 transport 测试为 `8 passed`。Bridge 当前收集 93 项：标准全套中 91 passed、2 failed；一项是文档已记录的 Windows legacy-`HOME` 基线，另一项是全套前序测试把真实 `.env` scratch root 留在进程环境中的顺序污染。排除两项后的 91 项全过，后者在空白 runtime env 中单独运行也通过。本 Gate 没有修改这两个无关测试或业务规则。

受限网络环境中的首次启动如实失败并返回 `rc=1`，原因是 `nics4304-cad1` DNS 不可解析；没有输出 running。使用已授权网络后，同一入口真实得到：

```text
Bridge tunnel: starting without a separate PowerShell window...
Starting tunnel...
tunnel.warm = 4.344s
Bridge tunnel: running (5.6s).
```

随后 `vda bridge status` 返回 Bridge `0.7.0`、tunnel running、CIW daemon connected、Virtuoso `6.1.8-64b`、Spectre `21.1.0`，最终为 `Bridge tunnel: status healthy (2.6s).`。命令结束后 `Get-Process` 没有发现残留 `vda` 或 `virtuoso-bridge` launcher；共享 SSH tunnel 按设计继续运行。

但用户随后截图仍看到标题以 `C:\WINDOWS\System32\Open...` 开头的 Windows Terminal 标签。只读进程树确认它不是 PowerShell：主 tunnel 为 `ssh.exe` PID 110912，jump proxy 为 PID 102764，同时存在 `WindowsTerminal.exe` 与 `OpenConsole.exe`。Bridge 当时把 `CREATE_NO_WINDOW`、`DETACHED_PROCESS` 与 `CREATE_NEW_PROCESS_GROUP` 组合使用；这使无窗口标志失效。第一版“已修复”的表述因此被撤回。

在恢复点 `e74379a` 上创建 `codex/backup-vda-window-hide-e74379a` 后，Bridge 提交 `cd9aa97` 移除了 tunnel 的 `DETACHED_PROCESS`，文档提交为 `e1f248d`。补丁版 live smoke 得到：

```text
Bridge tunnel: starting without a separate PowerShell window...
Starting tunnel...
tunnel.warm = 3.969s
Bridge tunnel: running (5.2s).
```

launcher 父 PID 已退出，而本地端口 65347 仍可达；此时 `WindowsTerminal.exe=0`、`OpenConsole.exe=0`。随后 `status` 再次连接 CIW，Spectre 21.1.0 探测成功。最后 `stop` 在 0.3s 内完成，并确认 `ssh.exe=0`、state 文件不存在、Windows Terminal/OpenConsole 仍为 0。OpenSSH 跳板链可保留一个 `MainWindowHandle=0` 的后台 `conhost.exe`，它不是用户看到的 Windows Terminal 标签。

## 未闭合边界

- 补丁后进程证据已排除 `WindowsTerminal.exe`/`OpenConsole.exe`，但“用户屏幕上完全没有闪窗”仍需用户对这一次新启动做最终视觉确认；若仍有窗口，必须采样新窗口 PID/父 PID，不再根据标题猜测。
- 系统临时目录中发现 92 份历史 `vb_tunnel_stderr_*.log`，多数为 0–93 bytes。它们来自第三方 Bridge tunnel stderr 捕获，不是本次 VDA launcher，也不是空窗口原因；本 Gate 没有删除文件或修改 Bridge。
- 若处理该 temp-log 保留，必须按第三方规则先在 `codex/vda-transport-recovery` 当前提交建立新的备份引用，再做独立 Bridge 提交、修改清单和双仓回归。
- 生命周期状态只是系统诊断，不能作为 OA 结构、仿真结果或设计规格闭合证据。
