# Bridge 安全／兼容改动参考包

这是从维护者已使用的 Bridge 独立分支导出的**源码差异参考**，供换机 agent 比对／迁移，
不是另一套 SSH/SKILL/Spectre 实现，也不是自动安装、升级或远端部署入口。

- 上游：[Arcadia-1/virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite)
- 精确上游基线：`ecaf5a5c5499051ea4c4796a480e278a3e9c0da8`
- 参考分支 tip：`3688192f438833d8da7565b69a311547177128ac`（维护者本地分支，不保证上游能 fetch 此 tip）
- 对应 Bridge 包版本字符串：`1.0.0`；版本号不代替代码身份。
- 导出日期：2026-10-26；不是对当前最新上游的升级承诺。

## 包含什么

- `upstream-ecaf5a5c-to-vda-3688192.patch`：相对上述基线的完整差异，含运行代码、测试、
  通用 `.env_template` 变化及原 LOCAL_VDA_PATCH 历史说明，共25个路径。
- `manifest.json`：补丁 SHA-256、基线／来源 commit、逐路径 Git blob 身份和模式。
- `UPSTREAM-LICENSE.txt`：原上游 MIT License 与版权声明；仅适用于对应上游来源内容，
  不替 VDA 其他内容新增或变更许可。

没有包含实际 .env、认证缓存、token、私钥、Cadence license 配置、PDK、OA、波形或用户运行数据。
补丁内的认证测试使用合成测试值，`.env_template` 是上游公开配置模板，不是维护者实际配置。
原 LOCAL_VDA_PATCH.md 中的账号／路径／窗口／命令是历史记录，不是新用户应执行的默认值。

## 确认有远端代码改动吗

**有。** 相对本基线，两个会被 Bridge 部署到远端的 daemon 资源有改动：

- `src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_27.py`
- `src/virtuoso_bridge/virtuoso/basic/resources/ramic_bridge_daemon_3.py`

两者改为收到完整 JSON 请求即可派发，不依赖 Windows OpenSSH 的 TCP half-close/EOF。
签名／nonce／大小限制保留；这不是允许未认证访问。客户端响应 framing 必须配套。
当前差异里**没有额外的 ramic_bridge.il 修改**；早期 SKILL 回环默认改动已有上游等价实现，
不要从旧记录再重复覆盖。生成的 setup、部署路径和 token 位置由接入时自己的配置确定，
不能复制维护者服务器上的生成文件。

只更新本地 Python 不会更新运行中的 daemon。涉及重新部署、加载 setup 或重启 daemon 时，
需列清自己的会话／用户／路径／恢复范围并另获授权；本参考包不自动执行它们。

## 主要保留行为与文件

| 行为 | 主要路径（相对 Bridge 根） |
| --- | --- |
| 实际 bind 回读、loopback fail-closed | `src/virtuoso_bridge/daemon_guard.py`、`cli.py` |
| 本地 forward 显式 loopback、隐藏窗口／stale PID、有限幂等 SSH 重试 | `transport/ssh.py`、`transport/tunnel.py` |
| 未发送前的有限恢复、完整签名响应 framing、direct/receipt-only 路径 | `virtuoso/basic/bridge.py`、`tools/skill_exec.py` |
| token 可放到自己获准的远端目录 | `transport/tunnel.py`、`resources/.env_template` |
| CLI status 实际探测与关闭短生命周期资源 | `src/virtuoso_bridge/cli.py` |
| 请求分片／UTF-8、认证、超时、恢复与进程标志回归 | patch 中对应 `tests/` |
| 现场经过与已知 TCP reset 边界 | `LOCAL_VDA_PATCH.md`、VDA 的第三方补丁记录 |

它不是“连接重置全部修好”的声明。发送后断线／超时仍是未知结果，不能自动重放可能已生效的写入。
完整工程背景见[补丁历史](../../docs/third-party/virtuoso-bridge-local-patch.md)与
[1.0 操作说明](../../docs/bridge-1.0-operation-notes.md)。

## 如何迁移

### 先看自己的上游，避免重复修补

在自己的 Bridge checkout 保存已有修改、建立备份引用和独立分支，核对 HEAD 与实际 package。
如果上游已经有等价实现，使用上游，不重复应用相同补丁。若版本与本基线不同，逐项移植和测试；
不要用 `--reject`、强制覆盖、全目录复制或关闭 bind/auth 检查来绕过冲突。

### 需要复现本参考版本时

下面在**新的 Bridge checkout**演示；路径均要替换为自己的。
不在维护者原 Bridge、已有部署或用户设计目录里试补丁。

```sh
git clone https://github.com/Arcadia-1/virtuoso-bridge-lite.git
cd virtuoso-bridge-lite
git switch -c codex/vda-compat ecaf5a5c5499051ea4c4796a480e278a3e9c0da8
git apply --check /ABSOLUTE/PATH/TO/VDA/references/bridge-compat/upstream-ecaf5a5c-to-vda-3688192.patch
git apply /ABSOLUTE/PATH/TO/VDA/references/bridge-compat/upstream-ecaf5a5c-to-vda-3688192.patch
git diff --check
```

Windows 同样可用 Git，给含空格的 patch 绝对路径加引号。
先核对 patch 的 SHA-256 与 manifest；apply 后可用 `git hash-object <path>`
逐项核对 `patched_git_blob`（Git 规范化后的 blob 身份，不是文件 SHA-256）。
Windows checkout/apply 可能按 Git 配置使用 CRLF；不要把工作副本的原始字节 SHA-1
与 LF 的 Git blob 混淆。patch 自身的 SHA-256 则始终检查原始字节，不做换行归一化。
`upstream_git_blob=null` 表示基线没有此文件。不要把 `git apply` 当成已经安装或远端部署成功。

按 Bridge 自己的安装方式创建独立 Python 环境。聚焦离线回归示例：

```sh
python -m pytest tests/test_daemon_request_framing.py tests/test_ssh_retry.py tests/test_tunnel_recovery.py tests/test_virtuoso_tunnel_recovery.py tests/test_windows_process_flags.py tests/test_vda_upgrade_compatibility.py
```

这些测试不等于所有 Bridge 平台／版本通过；完整验收、与 VDA 的双环境补验及获准的最小连接验收分别记录。
自己持有的旧修改不要丢弃；保留回退引用，避免以后上游升级难以合并。

## VDA agent 下一步看什么

继续[环境移植清单](../../docs/environment-porting.md)，处理自己的工作区、PDK facts 和任务绑定。
SSH 连接配置成功、补丁可应用、本地测试通过、daemon 已部署、OA 回读与实际网表有效是不同层次，
不得把其中一个替代其他层次。
