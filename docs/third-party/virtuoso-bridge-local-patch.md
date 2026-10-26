# virtuoso-bridge-lite 本地兼容补丁

2026-10-26：为外部接入导出[参考包](../../references/bridge-compat/README.md)，含精确
上游/来源 commit、完整差异 patch、Git blob/SHA-256 清单和上游许可证。它包括两个
远端 daemon 资源的请求 framing 修改；本轮仅导出/本地校验，不修改 Bridge 或远端。
新环境按[移植清单](../environment-porting.md)配置，不能照抄下文维护者路径与历史命令。

当前集成更新于 **2026-10-18**：Bridge `1.0.0`，上游 main `ecaf5a5c`，集成提交 `e214d3a`，本地独立分支
`codex/vda-upstream-main-20261018`。2026-10-23 只读复核本地 package 为 `1.0.0`、分支 tip 为 `3688192`（Windows direct receipt framing 修复及聚焦验收记录）；本次未修改第三方代码，也未重新验证远端 daemon。下文旧分支和结果均是历史记录，不代表当前 tip。
当前逐项差异见本文件末节；操作影响见[1.0 操作说明](../bridge-1.0-operation-notes.md)，
备份和实际验证见[升级验收](../validation/2026-10-18-bridge-main-upgrade.md)。

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
- Bridge 内回环自说明文档：`afd7346`
- 回环安全加固前备份：`codex/backup-vda-loopback-e1f248d` → `e1f248dab69aa0e3504d8249613a096e4030673d`
- 回环安全加固分支：`codex/vda-loopback-only`
- 回环安全代码提交：`48b44e6`
- framing 修复前备份：`codex/backup-vda-framing-afd7346` → `afd7346`
- Windows request-framing 代码与测试：`b1194ca`
- Bridge 内 framing 自说明文档：`ebf7e50`

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

## 2026-09-04 Windows request framing 兼容修复

### 原因与精确改动

- 服务器本机直连安全 daemon 时，`1+2` 返回标准 `STX + 3`；经过当前 Windows OpenSSH forward 时，同一请求返回空响应。保持远端 `127.0.0.1` 和 `GatewayPorts=no`，分别测试显式/省略本地 bind 地址都失败，故问题不是回环安全策略，而是 half-close 行为。
- `virtuoso/basic/bridge.py` 仅在 Windows 的 managed SSH tunnel 上不调用 `shutdown(SHUT_WR)`；本地 Windows 和全部非 Windows 路径保持旧 half-close。
- `ramic_bridge_daemon_3.py` 与 `ramic_bridge_daemon_27.py` 在积累到一个完整 JSON value 后立即解析，不等待 EOF；分片 UTF-8、旧 EOF 客户端和 malformed EOF 均有测试。
- `test_virtuoso_tunnel_recovery.py` 覆盖 Windows keep-open 与 legacy half-close；新增 `test_daemon_request_framing.py` 对两版独立 daemon 源函数做相同 framing 回归。`test_runtime_paths.py` 仅补 `USERPROFILE` 以修正 Windows 测试 fixture，不改运行时代码。

### 隔离、验证与兼容边界

- 修改前先建立 `codex/backup-vda-framing-afd7346`；代码提交 `b1194ca`，文档提交 `ebf7e50`，仍只位于未推送的 `codex/vda-loopback-only`。
- targeted framing/tunnel：`11 passed`；空测试 `.env`、隔离 cwd/basetemp 下完整 Bridge：`106 passed`；完整 VDA：`910 passed in 5.77s`。
- 用户允许关闭无工作影响的当前会话后，两个 2026-07-05 遗留 headless Virtuoso/Xvfb 树经 UID、cwd、可执行文件、restore 和父进程核对，用 SIGTERM 正常退出；没有强杀。唯一替代会话从 `/data/xum/virtuoso_bridge_smoke/vb_bridge_restore.il` 自动加载 setup。
- 最终远端 `ss` 与会话日志均为 `127.0.0.1:65346`；Windows tunnel `1+2=3`；VDA doctor 为 `connected=true`、`daemon_bind=127.0.0.1:65346`、Bridge 0.7.0、TSMC N28 profile。未访问/写入 OA，未运行 Spectre analysis。
- Windows 更新版 client 必须配套重新部署并加载更新版 daemon；旧 daemon 仍会等待 EOF。新 daemon 兼容仍发送 EOF 的旧 client。上游若已有等价 framing，优先采用上游并丢弃 `b1194ca`；否则在新备份分支迁移并重复两次连续 SKILL、doctor、Bridge/VDA 全回归后再启用 OA 写入。

## 2026-09-04 上游 0.8.0/main 升级

### 当前引用与回滚点

- GitHub 上游：`Arcadia-1/virtuoso-bridge-lite`。
- 升级前私有 tip：`ebf7e5018a886f79403e55aaef1c5805a9d38302`。
- 修改前备份：`codex/backup-pre-upstream-20260904-ebf7e50`，与上述 tip 完全一致。
- 升级目标：上游 main `c64461c0bdc44330c143d386a8aaf3342088a59e`；仓库同时取得 `v0.8.0` annotated tag。
- 私有集成分支：`codex/vda-upstream-main-20260904`。
- 两父 merge：`106c61ee0d65bae1b1d20c86a7c4151ffe95c3e0`，父提交依次为 `c64461c` 与 `ebf7e50`。
- managed daemon restart 修正：`40ff8919b8d79aff3b58aa2167ad28be9a395a92`。
- Bridge 内最终说明提交：`01565791fb261c73552e3e80590a389d2a6b7003`。
- 以上均未推送第三方 origin。VDA 继续通过该 checkout 自己的 `.venv` 启动独立 worker。

### 接入的上游能力

- v0.8 scoped Spectre pools、并行运行目录隔离与 OP 失败处理。
- GUI/deploy/daemon/Spectre 分主机角色与 safe bootstrap。
- Paramiko session backend、可选 SOCKS5 transport，以及现有 OpenSSH 路径。
- strict Spectre PSF accessors、`ocnPrint` 精度/宽度/numberNotation 参数。
- schematic netlist import/export、netlist semantic cleanup、确定性 schematic constraint planner。
- 当前 Maestro client、corner netlist、output escaping 和 run timeout 修复。
- library/category、symbol/layout/GDS/docs 等 API 仍保留在 Bridge；VDA 未因升级自动宣称这些路径已成为 L5B 证据。

### 保留并新增的私有差异

原有 stale-state、auto-warm、1 s/3 s 瞬态退避、发送前一次恢复、Windows 无窗口启动、RAMIC/SSH loopback、实际 bind/user 核验和 Windows complete-JSON framing 均保留。合并及现场 reload 时另外闭合五个兼容点：

1. recovery warm 与 sleep 受调用者总 deadline 限制，短 timeout 不再被固定等待超越；只允许 payload 发送前重试。
2. `VirtuosoClient.from_env()` 接受纯 split-host 配置，不再先强制旧 `VB_REMOTE_HOST`。
3. 自动和 CLI 打印的手工 SSH forward 都使用 `GatewayPorts=no` 与显式本地 `127.0.0.1`。
4. Windows `status` 复用 tunnel 上下文，避免 half-close 假阴性；daemon 无响应、安全证据缺失或错误 endpoint 返回非零。USER/bind 两个幂等读查询可立即重试一次，空 USER 仍拒绝。
5. Windows `restart` 也把 managed tunnel context 交给 daemon client，并在 `finally` 中关闭 SSH runner；不会重现裸 client half-close，也不会在一次命令退出后遗留 runner 资源。跨用户 daemon 仍拒绝，旧 daemon 重启时的预期断线仍可识别。

Bridge checkout 内 `LOCAL_VDA_PATCH.md` 是逐文件主记录；本文件只维护 VDA 的依赖与验收视角。将来再次升级时，从新的上游 tip 建新 `codex/` 分支，保留本次 backup 与 merge，不在原分支强行 rebase，也不向 Arcadia origin 推送私有历史。

### 最终验证

- 未合并私有补丁的上游 Windows 基线：`883 passed, 35 failed, 11 skipped`。
- 相对上游的全部私有测试文件：`122 passed`；加入 10 项 Spectre runtime/split-role 路径测试后的核心 transport/security/split-host/Paramiko 集：`132 passed`。
- 最终完整 Bridge，固定短 basetemp `C:\vbt\full-final-20260904`：`914 passed, 34 failed, 11 skipped`（959 项）。34 项均位于未修改的上游测试文件，集中于 Unix docs/GDS shell、Windows remote-index cache 断言、symlink 权限、远端 POSIX 路径被本地 Windows fixture 表示，以及刻意跨 260 字符的路径；新增的一项 docs 失败已单独复现，没有把它们伪装成通过或为提高数字修改无关模块。
- VDA：`910 passed`；catalog 与 inverter close-loop demo plan 通过。
- 真实只读 smoke：已从当前 `0.8.0` checkout 执行 `restart`，runtime 仅上传并加载到 `/data/xum/virtuoso_bridge_xum/Aurora_s_Echo/virtuoso_bridge`；daemon/tunnel user 均为 `xum`；daemon `127.0.0.1:65346`；Virtuoso 6.1.8；Spectre 21.1.0；SKILL `1+2 -> 3`。没有访问或写 OA，没有 Spectre analysis。
- `vda bridge stop` 后本地 `65347` listener 与匹配的 managed `ssh.exe` 均为 0。

这次升级证明当前 VDA 正交 operation 与既有真实工作流可运行在 Bridge 0.8 底座上；没有证明上游新增 GDS、docs、local netlist 或全部 Maestro/PVT 功能已被 VDA 逐项 live 验收。

## 2026-10-18：认证 1.0 main 集成（当前）

### 备份与版本

- 上游仍为 `Arcadia-1/virtuoso-bridge-lite`；结束前再次 `ls-remote` 确认 main 为
  `ecaf5a5c5499051ea4c4796a480e278a3e9c0da8`，不是只选 release tag。
- 修改前干净私有 tip `0156579`，备份分支 `codex/backup-pre-main-20261018-0156579`。
  独立工作分支 `codex/vda-upstream-main-20261018`，最终两父 merge
  `e214d3a6e153a036d422b5453412365b8172cd06`；parents 为旧私有 tip 与该上游 main。
- 修改前/后完整 bundles、原源码 zip、原完整 venv、配置和 runtime 备份均保留。
  Bridge 工作树干净；没有把私有分支推到第三方 origin。
- VDA 也从 `dfc981a` 建立 `codex/backup-pre-bridge-upgrade-20261018-dfc981a` 与
  `codex/bridge-main-upgrade-20261018`；原未提交文档六个文件的 SHA 与备份一致，未混入升级提交。

### 相对上游的源文件差异

| 文件 | 当前私有差异 |
| --- | --- |
| `transport/ssh.py` | loopback/`GatewayPorts=no`，Windows 无窗口且无 `DETACHED_PROCESS`，stale PID `SystemError`，总 budget 内 1/3 s 退避。 |
| `transport/tunnel.py` | 可选 `VB_REMOTE_TOKEN_PATH[_profile]`、工作区 token 初始化与生成 setup 中的 `RB_TOKEN_PATH`。保留上游默认路径，不强制所有用户迁移。 |
| `daemon_guard.py` | VDA 需要的实际 `RBLastBind` 回读、合法端口与回环 fail-closed；保留上游签名 PID owner 守卫。 |
| `virtuoso/basic/bridge.py` | 明确 pre-send 连接拒绝才能恢复；保留上游有界 jump-host grace；实际请求懒获取 token；Windows no-half-close；完整响应通过 nonce/HMAC 后结束接收。 |
| `ramic_bridge_daemon_3.py`、`_27.py` | complete-JSON 请求，不等待 EOF；保留新版认证、owner、resync/receipt、deadline 和大小限制。 |
| `cli.py` | 手工 forward 提示安全一致；status/restart 的 managed context/cleanup；无响应或安全证据不明返回失败。 |
| `resources/.env_template` | 工作区 token 选项的注释说明，无秘密值。 |
| `examples/_pdk.py` | 仅删除上游一个 EOF 空行，使整个 merge 的 whitespace check 通过。 |

原生 Windows liveness、默认 RAMIC 回环、target/state cache、signed owner、IPC resync、
Busy 和 durable receipts 采用上游实现，不机械叠加旧补丁。原 USER/bind 的额外只读重试被更强
身份机制与原有 VDA checkpoint 语义取代。factory 保持 lazy，仅真正 EDA 请求时必要 warm，
既不为 docs 查找启动 EDA，也不允许 shared guard 绕过 no-replay 规则。

测试迁移覆盖原 framing/retry/tunnel/Windows flags 文件，并新增
`test_vda_upgrade_compatibility.py`。fixture 修正隔离真实 env/owner，适配 complete-JSON
派发与 upstream lazy factory，不放宽 OA、HMAC 或不确定结果判定。

### 验证与操作影响

- Linux：纯上游 `2286 passed, 89 skipped`；集成 `2325 passed, 89 skipped`。
- Windows：纯上游 `2148 passed, 76 failed, 2 errors, 149 skipped`；集成
  `2192 passed, 71 failed, 2 errors, 149 skipped`。逐项集合没有新增失败/错误；剩余是已复现
  的平台/fixture 边界，不宣传为“所有 Windows 功能通过”。私有核心集 `165 passed, 2 skipped`。
- wheel/source distribution 独立构建后，全部 package `.py/.il/.yaml/.env_template` 字节与
  两种分发包逐项比较，`2 passed`，包含 CDF filter；主完整集默认跳过这两项，不能重复计数。
- VDA `929 passed`；既有 TSMC18 OA→`si` DC/AC 的网表 SHA 与 28 个 raw metrics 精确一致；
  N28 两点 standalone AC preview 的 125 个输出也精确一致。两者不同 profile，不相互借性能证据。
- 原 PID 94710 的 CIW 在同一窗口保留，RAMIC 已升级为认证 daemon；新签名 owner、实际
  `127.0.0.1:65346`、`1+2`、两 profile doctor 与 status 均通过。没有 OA 写入或 ADE state 覆盖。
- 原有第二个 Virtuoso PID 126060 保留；自有 headless/计算/tunnel 已清理。
  正常 tunnel `MainWindowHandle=0`，停止后本地监听与匹配 SSH 进程均为 0。

操作行为变化、兼容调用和明确未接入的 API 见[操作说明](../bridge-1.0-operation-notes.md)；
精确备份 SHA、测试环境失败、一次性旧协议迁移与未验证边界见
[升级验收](../validation/2026-10-18-bridge-main-upgrade.md)。本次不改变 VDA 授权、正交操作
或 ADE L 优先策略，不自动把 shared-CIW/Maestro/SOS 新 API 计入 L5B 电气闭环能力。

## 2026-10-18 新功能验收后的 Windows direct 修正

- 备份 ref `codex/backup-pre-feature-validation-20261018` 指向 `e214d3a6`；补丁提交
  `3688192f438833d8da7565b69a311547177128ac`，仍在独立私有分支，未推第三方 origin。
  新完整 bundle 已验证；路径、SHA 和 live 证据见
  [新功能验收](../validation/2026-10-18-bridge-feature-workflow.md)。
- `virtuoso/basic/bridge.py` 将 Windows no-`SHUT_WR` 扩展到 direct/receipt-only client，
  不再限于挂有 tunnel 对象的 client。原生 Windows socketpair 也复现 half-close 空回复，
  因而不是回环安全策略问题；POSIX 旧行为保留。
- `tools/skill_exec.py` 同样采用 Windows complete-JSON 请求、完整 nonce/HMAC 响应结束
  与接收总 deadline；不关闭认证、不增加发送后 retry。
- auth/recoverable-daemon 测试 fixture 改为匹配真实 complete-JSON dispatch，Windows
  不再依赖 half-close；私有测试新增 direct/standalone 分片、篡改与 POSIX 行为矩阵。
  pending-receipt 测试只增加失败诊断，不放宽安全断言。
- final Windows private set 96 passed；Linux focused 281 passed / 2 skipped，最终私有
  matrix 29 passed。更广 Windows TCP 集 271 passed / 3 failed / 6 skipped，连接重置
  诊断仍未完全解决；不能以 focused pass 宣传全量全绿。VDA 最终 937 项通过。
- 丢回执/超时 live 在自有隔离会话完成，未重复执行；参数 probe 只写新测试 cell，
  三份 si 确认 8→9→8 µm 和恢复一致。原会话、人工状态、配置保留，自有进程/tunnel 已清理。
- 手动 `ensure_tunnel()` 后须 `save_state()`，以供跨进程接管/停止；验收 harness 漏存
  状态造成的一次精确自有 forward 遗留已定位清理，不改写生产 warm 生命周期语义。

后续合并上游时先检查是否已有这些等价修复；只保留必要差异，并重复认证/分片/不重放/
退出清理测试。VDA rich inspection 是对公开 Bridge reader 的薄适配，不复制 reader。
