# virtuoso-bridge-lite 本地兼容补丁

## 隔离与备份

- 第三方仓库：`https://github.com/Arcadia-1/virtuoso-bridge-lite`
- 本地路径：`C:\Users\aknigsesl\tools\virtuoso-bridge-lite`
- 未修改基线：`dc9a4ec5acb1971f683e4717ff9e5a635ba588cf`
- 修改前备份引用：`codex/backup-vda-transport-dc9a4ec`
- 本地工作分支：`codex/vda-transport-recovery`
- stale-state/auto-warm：`9e52844463cceaf12bbc39966a7b2db5c7238357`
- 瞬态 SSH 有界退避：`f8fdb9ed7e91c3194675876dcc4b16a06b77a7eb`
- SKILL 发送前 tunnel 恢复：`2f41293aa8c4f297470298e27ccd7747046b3913`
- Bridge 内最新自说明文档：`e74379a7e72c4886a1b578b221f770b62cd23433`

这些提交没有推送到第三方 `origin`。VDA 默认 Bridge Python 指向该本地 checkout 的 `.venv`；真实任务前应核对当前分支/提交，不能假设路径相同就代表补丁仍在。

## 修改范围

1. `transport/tunnel.py::SSHClient.is_running`：Windows 端口不可达时不执行 POSIX 风格 `os.kill(pid, 0)`；stale state 可被新启动替换。
2. `transport/ssh.py::SSHRunner.stop_port_forward/is_tunnel_alive`：陈旧 PID 的 `SystemError` 作为失效状态处理。
3. `virtuoso/basic/bridge.py::VirtuosoClient.from_env`：无可用 tunnel 时真正调用 `SSHClient.warm()`。
4. `transport/ssh.py` 的幂等 SSH command/upload/download 重试：瞬态错误之间等待 1 秒、3 秒；普通 connect timeout 纳入瞬态分类，认证、host key、DNS 等确定性错误仍立即失败。
5. `virtuoso/basic/bridge.py::execute_skill`：只有 `connect()` 在 `sendall()` 前被拒绝时，managed client 才 warm 并重试一次。任何可能已发送 SKILL payload 的错误都不自动重放。
6. 新增测试：`test_tunnel_recovery.py`、`test_ssh_retry.py`、`test_virtuoso_tunnel_recovery.py`，并扩展 `test_profile_resolver.py`。

没有修改环境变量名、state schema、远端目录、SSH 认证、SKILL payload、Spectre 或 VDA worker JSON 边界，也没有加入 keepalive。payload 发送后的 reset 仍由 VDA checkpoint/resume 处理，不能安全自动重放。

## 验证

- 修改前隔离基线：`78 passed, 1 deselected`。
- stale-state/auto-warm 后：`82 passed, 1 deselected`。
- 有界退避后：`86 passed, 1 deselected`。
- pre-send 恢复后：`91 passed, 1 deselected`。
- 强制终止 listener 并保留 state 后，第二次只读 inspect 自动建新 tunnel，前后 `bridge_readback` 均为 `MN0/MP0`、`IN/OUT/VDD/VSS`、`Wn/Wp/L=0.6/0.8/0.03 µm`。
- 9 点压力任务在有界退避版本上完成 7 点后，于 `parameters.stage.8` 首次 TCP connect 被拒绝；VDA 成功恢复原始 OA、保留失败记录，并从候选 8 恢复，最终完成 9/9 与最佳写回，没有重复前 7 点。
- pre-send 补丁的同-client smoke 精确停止 own tunnel，保留 stale state；随后自动从 PID 126188 切换到 136176，只读 `1+2` 返回 `3`，最后清理进程和 state。

有界退避不能防止每一次 tunnel death；pre-send smoke 只证明“请求尚未发送”的安全窗口。一轮应用 pre-send 补丁后的全新 9 点无中断 sweep 尚未执行，不能据此宣称 transport 全面自愈。

## 上游升级

先检查上游是否已有 stale-state、`from_env().warm()`、重试退避和 pre-send-only 恢复的等价实现；已有则优先采用上游并丢弃相应本地提交。否则从新上游提交建立新的 `codex/` 分支，依次 cherry-pick `9e52844`、`f8fdb9e`、`2f41293`。随后重跑 Bridge/VDA 测试、强制 stale-state inspect 和同-client pre-send smoke；全部通过前保留原始备份引用。Bridge checkout 内的 `LOCAL_VDA_PATCH.md` 是逐文件主记录。
