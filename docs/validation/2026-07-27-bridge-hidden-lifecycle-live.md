# 2026-07-27 Bridge 隐藏启动与状态回显验证

状态：**VDA-owned hidden Bridge launcher locally and live verified; user-visible popup confirmation and Bridge temp-log retention remain open**。

## 目标与边界

本 Gate 解决显式启动 Bridge 时反复出现空白 PowerShell 窗口、却看不到进度的问题。实现只修改 VDA：

- 新增 `vda bridge start|status|stop`，委托 Bridge 已安装的公开 console script；
- 不复制或修改 Bridge 的 SSH、profile、`.env`、daemon、state、文件传输或 Spectre 实现；
- Windows launcher 使用 `CREATE_NO_WINDOW + SW_HIDE + CREATE_NEW_PROCESS_GROUP`，并加入与 VDA worker 相同的 Job Object 所有权边界；
- stdout/stderr 回到当前 VDA 终端；默认过滤 `[cmd]` 原始 SSH 命令，`--verbose` 才保留；
- 正常完成只关闭 launcher 句柄，保留 Bridge 共享 tunnel；Ctrl+C 回收本次仍受控的启动进程树。

本次真实 smoke 没有目标 library/cell/view，没有访问或写入 OA，没有创建 cellview，没有运行 Spectre analysis，也没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。`status` 只连接既有 CIW daemon 并查询 Spectre 版本。

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

受限网络环境中的首次启动如实失败并返回 `rc=1`，原因是 `nics4304-cad1` DNS 不可解析；没有输出 running。使用已授权网络后，同一入口真实得到：

```text
Bridge tunnel: starting without a separate PowerShell window...
Starting tunnel...
tunnel.warm = 4.344s
Bridge tunnel: running (5.6s).
```

随后 `vda bridge status` 返回 Bridge `0.7.0`、tunnel running、CIW daemon connected、Virtuoso `6.1.8-64b`、Spectre `21.1.0`，最终为 `Bridge tunnel: status healthy (2.6s).`。命令结束后 `Get-Process` 没有发现残留 `vda` 或 `virtuoso-bridge` launcher；共享 SSH tunnel 按设计继续运行。

## 未闭合边界

- Codex 不能观察用户桌面，因而“屏幕上完全没有闪窗”目前由进程创建 flags 和单元测试证明，仍需用户实际视觉确认；如果仍有窗口，下一步应采样该窗口进程 PID/父 PID，而不是继续猜 Bridge。
- 系统临时目录中发现 92 份历史 `vb_tunnel_stderr_*.log`，多数为 0–93 bytes。它们来自第三方 Bridge tunnel stderr 捕获，不是本次 VDA launcher，也不是空窗口原因；本 Gate 没有删除文件或修改 Bridge。
- 若处理该 temp-log 保留，必须按第三方规则先在 `codex/vda-transport-recovery` 当前提交建立新的备份引用，再做独立 Bridge 提交、修改清单和双仓回归。
- 生命周期状态只是系统诊断，不能作为 OA 结构、仿真结果或设计规格闭合证据。
