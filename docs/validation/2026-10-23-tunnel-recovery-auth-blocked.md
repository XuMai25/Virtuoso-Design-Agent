# 2026-10-23 最小隧道恢复：转发已建立，认证验收中止

> 本页保留当时认证中止的证据。后续在用户授权定向重启、重新验证 owner 和回环 bind 后，
> 本地认证缓存已更新并通过 VDA doctor；见[后续恢复记录](2026-10-23-virtuoso-restart-readonly-acceptance.md)。

## 授权与结果

用户只批准恢复本地 `127.0.0.1:65347` 到
`nics4304-cad1/127.0.0.1:65346` 的 SSH 转发及只读验收。
不重启 Virtuoso、不重载 setup、不修改网络配置、不写 OA、不仿真；遇到新问题停止，
STB 暂不继续。

结果：**tunnel restored and tracked / authenticated acceptance blocked**。
这不是 Bridge 完整健康验收通过，也不是 STB 或目标电路验收通过。

## 已完成

1. 执行前本地 65347 无监听，旧状态中的 PID 122116 已退出。
2. 使用 Bridge 现有 `SSHRunner.start_port_forward`，只尝试指定端口；没有使用会部署
   文件的完整 `warm/start` 路径，也未调用会自动改端口的 `ensure_tunnel`。
3. 2026-10-23 01:46:17（北京时间）建立新的 `ssh.exe` 转发进程 PID **113544**。
   独立 Windows TCP 表核对本地唯一监听为 `127.0.0.1:65347`，归属于该进程。
4. 使用原 Bridge `save_state` 保存管理状态；保留原目标、setup、identity、host/profile
   等元数据，仅更新隧道 PID 和启动时间；保存后逐项回读匹配。
5. 更新前状态已备份到：
   `artifacts/stb-live-20261023/tunnel-recovery-20261022T174617Z/tunnel-state-before.json`。
   状态文件不含认证 token；认证缓存未复制进该目录。

本次不修改 VDA/Bridge 生产源码。按照 karpathy-guidelines 的最小改动原则，
仅组合已有转发、状态保存与只读认证接口，不新增通用恢复器或扩大重试范围。

## 新问题与停止点

随后使用现有本地认证缓存，通过新隧道执行首次签名 capability handshake，返回：

```text
DaemonAuthError: AuthError: bridge token mismatch - the daemon on this port belongs to a different user (or the token was rotated)
```

这是认证拒绝，不是请求超时。该错误本身无法区分本地缓存过期、远端 token 改变或其他
身份不一致，不能直接断言“另一个用户”或“已经轮换 token”。不得依据错误文本中的通用
restart 建议直接重启服务。

本轮在该握手失败后立即停止：未发出后续 SKILL，不自动 refresh/provision token，
不修改认证开关，不重试，不重启 daemon 或 Virtuoso，不重载 setup。
同一轮计划中的 owner/bind/`1+2` 验收均未完成。

上一轮只读排查曾在服务器本机通过已有工作区凭据完成认证及 `1+2`，当时 owner 为
94710、通道 idle、实际 bind 为 `127.0.0.1:65346`。那是前一轮的证据，不冒充本轮
通过本机隧道的身份验收；“本地缓存过期”目前只是一项有依据但未闭合的推断。

## 留存与边界

- 仅保留上述一条已登记的转发供后续检查；没有再创建第二条隧道。
- OA 写入、OA 结构回读、si netlisting、Spectre 提交均为 0。
- 没有修改网络/TUN/代理/防火墙、SSH 配置、`.env` 或任何远端文件。
- 没有关闭或操作用户 Virtuoso/ADE 窗口。
- 证据为连接管理状态与 `system_event`；没有新增目标电路 `eda_result`。

## 如需继续，须再确认的最小范围

只读获取既存远端工作区认证凭据，在内存中用于签名握手；核对原 owner PID、Unix 用户、
实际 loopback bind 和只读算术。全部匹配后，仅更新对应的本地认证缓存，再做一次正常
本机链路验收。不生成或轮换远端 token，不创建/修改远端凭据或权限；凭据内容不显示、
不进入报告或 Git。任何身份变化或新问题继续停止，STB 仍不自动恢复。

本次未执行该后续动作。初始 STB 前检失败见
[前检记录](2026-10-23-stb-readonly-preflight.md)。
