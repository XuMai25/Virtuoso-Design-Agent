# virtuoso-bridge-lite 本地兼容补丁

## 隔离与备份

- 第三方仓库：`https://github.com/Arcadia-1/virtuoso-bridge-lite`
- 本地路径：`C:\Users\aknigsesl\tools\virtuoso-bridge-lite`
- 未修改基线：`dc9a4ec5acb1971f683e4717ff9e5a635ba588cf`
- 修改前备份引用：`codex/backup-vda-transport-dc9a4ec`
- 窗口修复前备份引用：`codex/backup-vda-window-hide-e74379a`
- 本地工作分支：`codex/vda-transport-recovery`
- stale-state/auto-warm：`9e52844463cceaf12bbc39966a7b2db5c7238357`
- 瞬态 SSH 有界退避：`f8fdb9ed7e91c3194675876dcc4b16a06b77a7eb`
- SKILL 发送前 tunnel 恢复：`2f41293aa8c4f297470298e27ccd7747046b3913`
- Windows tunnel 隐藏修复：`cd9aa97b631aa6b9d5db5927cc0e1953124ec658`
- Bridge 内最新自说明文档：`afd7346`
- 回环安全加固前备份：`codex/backup-vda-loopback-e1f248d` → `e1f248dab69aa0e3504d8249613a096e4030673d`
- 回环安全加固分支：`codex/vda-loopback-only`
- 回环安全代码提交：`48b44e6`

这些提交没有推送到第三方 `origin`。VDA 默认 Bridge Python 指向该本地 checkout 的 `.venv`；真实任务前应核对当前分支/提交，不能假设路径相同就代表补丁仍在。

## 修改范围

1. `transport/tunnel.py::SSHClient.is_running`：Windows 端口不可达时不执行 POSIX 风格 `os.kill(pid, 0)`；stale state 可被新启动替换。
2. `transport/ssh.py::SSHRunner.stop_port_forward/is_tunnel_alive`：陈旧 PID 的 `SystemError` 作为失效状态处理。
3. `virtuoso/basic/bridge.py::VirtuosoClient.from_env`：无可用 tunnel 时真正调用 `SSHClient.warm()`。
4. `transport/ssh.py` 的幂等 SSH command/upload/download 重试：瞬态错误之间等待 1 秒、3 秒；普通 connect timeout 纳入瞬态分类，认证、host key、DNS 等确定性错误仍立即失败。
5. `virtuoso/basic/bridge.py::execute_skill`：只有 `connect()` 在 `sendall()` 前被拒绝时，managed client 才 warm 并重试一次。任何可能已发送 SKILL payload 的错误都不自动重放。
6. `transport/ssh.py` 的 Windows 长期 tunnel 启动：保留 `CREATE_NO_WINDOW`、`SW_HIDE` 和 `CREATE_NEW_PROCESS_GROUP`，移除会让 Windows 忽略无窗口标志的 `DETACHED_PROCESS`。Windows 子进程在 launcher 正常退出后本就可继续存活，没有改变 SSH 命令、jump-host 或 state 行为。
7. 新增测试：`test_tunnel_recovery.py`、`test_ssh_retry.py`、`test_virtuoso_tunnel_recovery.py`、`test_windows_process_flags.py`，并扩展 `test_profile_resolver.py`。

没有修改环境变量名、state schema、远端目录、SSH 认证、SKILL payload、Spectre 或 VDA worker JSON 边界，也没有加入 keepalive。payload 发送后的 reset 仍由 VDA checkpoint/resume 处理，不能安全自动重放。

## 验证

- 修改前隔离基线：`78 passed, 1 deselected`。
- stale-state/auto-warm 后：`82 passed, 1 deselected`。
- 有界退避后：`86 passed, 1 deselected`。
- pre-send 恢复后：`91 passed, 1 deselected`。
- 窗口修复相关 transport 测试：`8 passed`。
- 当前 Bridge 共收集 93 项测试。标准全套为 91 passed、2 failed：一项是既有 Windows legacy-`HOME` 基线；另一项是前序测试加载真实 `.env` 后造成的 scratch-root 顺序污染。排除这两项后的 91 项全过，scratch-root 测试在空白 runtime env 中单独运行也通过；没有为本次窗口修复改动这两项无关行为。
- 强制终止 listener 并保留 state 后，第二次只读 inspect 自动建新 tunnel，前后 `bridge_readback` 均为 `MN0/MP0`、`IN/OUT/VDD/VSS`、`Wn/Wp/L=0.6/0.8/0.03 µm`。
- 9 点压力任务在有界退避版本上完成 7 点后，于 `parameters.stage.8` 首次 TCP connect 被拒绝；VDA 成功恢复原始 OA、保留失败记录，并从候选 8 恢复，最终完成 9/9 与最佳写回，没有重复前 7 点。
- pre-send 补丁的同-client smoke 精确停止 own tunnel，保留 stale state；随后自动从 PID 126188 切换到 136176，只读 `1+2` 返回 `3`，最后清理进程和 state。
- 用户截图和进程树把残留标签定位到 OpenSSH tunnel：修复前主 PID 110912、jump proxy PID 102764，同时存在 `WindowsTerminal.exe` 与 `OpenConsole.exe`。
- 修复后 `vda bridge start` 的 `tunnel.warm=3.969s`、总耗时 5.2s；launcher 父进程退出后 65347 仍可达，`WindowsTerminal.exe` 和 `OpenConsole.exe` 均为 0。只读 `vda bridge status` 成功连接 CIW 并识别 Spectre 21.1.0；`vda bridge stop` 后 `ssh.exe=0`、state 文件不存在。

有界退避不能防止每一次 tunnel death；pre-send smoke 只证明“请求尚未发送”的安全窗口。一轮应用 pre-send 补丁后的全新 9 点无中断 sweep 尚未执行，不能据此宣称 transport 全面自愈。

## 上游升级

先检查上游是否已有 stale-state、`from_env().warm()`、重试退避、pre-send-only 恢复和 Windows tunnel 隐藏的等价实现；已有则优先采用上游并丢弃相应本地提交。否则从新上游提交建立新的 `codex/` 分支，依次 cherry-pick `9e52844`、`f8fdb9e`、`2f41293`、`cd9aa97`。随后重跑 Bridge/VDA 测试、强制 stale-state inspect、同-client pre-send smoke 和隐藏 start/status/stop smoke；全部通过前保留原始备份引用。Bridge checkout 内的 `LOCAL_VDA_PATCH.md` 是逐文件主记录。

## 2026-09-03 回环监听安全加固

### 精确改动

1. Bridge `ramic_bridge.il` 把新 daemon 的默认 `RBLocal` 从 `nil` 改为 `t`，即默认只监听 `127.0.0.1`；原 monitor 中的人工切换能力保留，不删除第三方库能力。
2. Bridge `SSHRunner.start_port_forward` 把本地转发明确写成 `127.0.0.1:<local>:127.0.0.1:<remote>`，并加入 `GatewayPorts=no`；jump host、隐藏窗口、重试、state 和清理语义不变。
3. Bridge `daemon_guard.py` 新增实际 `RBLastBind` 回读；`bridge status` 对响应中的非回环、空值或无法核实状态明确失败。没有修改 SKILL JSON 协议或执行能力。
4. VDA `bridge_worker._client()` 在任何 RAMIC 支持的 OA/远端动作前调用 Bridge 的同一 bind guard，并实行没有非回环 bypass 的 fail-closed 策略。成功的 `bridge.probe` 把 bind 写入 `bridge_readback`。

### 本地与真实验证

- Bridge 共收集 100 项：排除两项既有且已记录的 Windows legacy-`HOME`/真实 profile scratch-root 基线后 `98 passed`；全跑只出现这两项旧失败。
- VDA 新增 6 项守卫/证据测试并通过；完整 VDA 回归无失败。
- 远端旧进程被精确核实为当前用户 PID 114251、命令尾部 `0.0.0.0 65346`，且只读 `1+2` 已无响应。核对脚本、地址、端口和 UID 后仅向该子进程发送 SIGTERM；没有终止父 Virtuoso、没有访问或写 OA。随后 `:65346` 无 listener。
- 安全版只上传到 `/data/xum/virtuoso_bridge_xum/Aurora_s_Echo/virtuoso_bridge`；远端 `ramic_bridge.il` SHA-256 为 `ce446a0886fc4c5a8e16ac284767a17d0768bc5c7325dafd1a337e5291cba7df`。
- 独立临时 daemon 在 `65490` 实际报告并由 `ss` 确认 `127.0.0.1:65490`；命令结束后该进程、socket 和日志均为零。
- 重启后的 Windows tunnel 只监听 `127.0.0.1:65347`；持有者为 PID 91348 的 `ssh.exe`，`MainWindowHandle=0`，未生成 Windows Terminal/OpenConsole。
- 在现有交互式 CIW 尚未重载 setup 时，真实 `vda doctor` 被新守卫拦在 `Empty response from daemon`，没有进入 OA 或 Spectre。最终 CIW-attached `1+2` 仍需加载生成的 setup 后复核。

### 安全声明与升级

该补丁闭合管理员通知中的公网/任意网卡监听问题，但不把“localhost”夸大为多用户主机上的应用层认证：同机 Unix 用户隔离仍需管理员策略，若要密码/token 或权限化 Unix socket，必须另立协议升级 Gate。Virtuoso 自身的 Cadence listener 也不属于 VDA/Bridge 部署，未经管理员或厂商判断不能宣称已审计安全。

Bridge 上游更新时，先检查是否已有等价的安全默认、显式本地转发和实际 bind 回读；有则采用上游。否则在新上游备份分支上依次迁移既有四个 transport 提交和 `48b44e6`，重跑 Bridge/VDA 测试、远端 `ss`、CIW `1+2` 与退出清理，再撤销旧备份引用。不得把这一私有分支直接推送到 Arcadia 第三方 origin。
