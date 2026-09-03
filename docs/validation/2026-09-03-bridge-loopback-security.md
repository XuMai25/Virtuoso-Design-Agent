# 2026-09-03 Bridge 回环监听安全修复与验证

## 目标与授权边界

用户依据服务器管理员“禁止非必要服务监听 `0.0.0.0`”的通知，要求修复并验证，同时不得损害既有功能。用户此前还要求：第三方 Bridge 必须先备份再修改，并记录逐文件差异或使用自己的分支。

本轮允许修改本地 Bridge 私有分支、更新 `/data/xum` 内既有 Bridge 工作目录并运行只读/临时安全 smoke。没有目标 library/cell/view，没有访问或写 OA，没有运行 Spectre analysis，也没有形成 `eda_result`。

## 修改前发现

- 本地 VDA 不创建 HTTP、数据库、容器或常驻监听服务；RAMIC 是 Bridge 负责的 Virtuoso SKILL TCP daemon。
- Bridge `ramic_bridge.il` 的默认值为 `RBLocal=nil`，daemon 启动参数因而是 `0.0.0.0`。
- 真实远端 `ss` 显示 `*:65346`，持有者 PID 114251；`/proc/114251` 再次确认 UID 1068、父 PID 114250、脚本路径位于声明的 `/data/xum/.../virtuoso_bridge`，命令参数为 `0.0.0.0 65346`。
- 该旧 daemon 对只读 `1+2` 返回 `Empty response from daemon`，因此当时既不安全也不可用。
- Windows SSH forward 过去依赖 OpenSSH 默认本地 bind，没有在参数中显式写出 `127.0.0.1`。

这些是即时 `bridge_readback`/进程与 socket 诊断；“会形成公网攻击面”依据管理员规则作 `software_inference`。管理员通知与用户批准属于 `user_input`。

## 第三方隔离与代码改动

- 修改前精确提交：`e1f248dab69aa0e3504d8249613a096e4030673d`。
- 备份引用：`codex/backup-vda-loopback-e1f248d`。
- 私有工作分支：`codex/vda-loopback-only`。
- 原子代码提交：`48b44e6`，未推送第三方 `Arcadia-1` origin。
- Bridge 自说明文档提交：`afd7346`。
- VDA 强制策略提交：`5cb9500`；复用 Bridge 单一解析实现的边界收敛提交：`76b7fb3`。

逐文件改动：

1. `ramic_bridge.il`：`RBLocal=t` 成为安全默认；人工 monitor 仍可显式改为非回环，能力未删除。
2. `transport/ssh.py`：`-L 127.0.0.1:<local>:127.0.0.1:<remote>`，并设置 `GatewayPorts=no`；其余 tunnel 行为不变。
3. `daemon_guard.py`：读取实际 `RBLastBind`，区分 loopback、wildcard、缺失和查询错误。
4. `cli.py`：响应正常时显示实际 bind；非回环或不可核实返回安全失败。
5. VDA `bridge_worker.py`：任何 RAMIC-backed OA/SKILL action 前复用 Bridge 的 bind guard，并以无 bypass 策略 fail closed；通过值进入 `bridge.probe` 的 `bridge_readback`。
6. Bridge/VDA 新增守卫、status、SSH 命令及证据字段测试。

没有修改 Bridge JSON/SKILL wire protocol、SSH 认证、jump host、远端目录、Spectre、OA schema 或 VDA task token/授权规则。

## 测试结果

- Bridge：收集 100 项。完整运行只有两项既有基线失败：Windows legacy `HOME` 路径和真实 profile scratch-root `/data/xum` 与测试硬编码 `/tmp` 的差异。明确 deselect 这两项后 `98 passed, 2 deselected in 4.25s`。
- VDA：边界收敛后最终回归 `910 passed in 5.70s`，其中新安全文件 6 项全部通过。
- `git diff --check` 通过。

## 真实安全与生命周期验证

1. 安全文件只上传到 `/data/xum/virtuoso_bridge_xum/Aurora_s_Echo/virtuoso_bridge`。远端 `ramic_bridge.il` 的 SHA-256 为 `ce446a0886fc4c5a8e16ac284767a17d0768bc5c7325dafd1a337e5291cba7df`。
2. 在终止前按 PID、UID、脚本、host 参数和端口再次核对；只向 PID 114251 发送 SIGTERM。Virtuoso 父进程、OA 和其他 Cadence 进程未动。随后远端 `:65346` 无 listener。
3. 用安全版 daemon 在独立端口 65490 做有界 smoke：banner 为 `bind=127.0.0.1:65490`，`ss` 同样只显示 `127.0.0.1:65490`。退出后 listener、PID 和临时日志均不存在。
4. 经 VDA 正常 stop/start 后，本地端仅监听 `127.0.0.1:65347`；持有者 PID 91348 为 `ssh.exe`，`MainWindowHandle=0`，未出现 Windows Terminal/OpenConsole。
5. 在交互式 CIW 尚未重新加载 setup 的状态下，真实 VDA doctor 被守卫拦截为 `VDA could not verify ... Empty response from daemon`；没有继续到 OA 或 Spectre。
6. 等待 CIW 人工加载期间执行正常 `vda bridge stop`；本地 `65347` listener 和精确 SSH PID 91348 均消失。系统中仍有 PID 89660/89128 的 Windows Terminal/OpenConsole，但二者创建于 2026-09-02，早于本轮 tunnel，未作删除或归因。

## 尚未完成与判定边界

安全目标已经达到：旧 wildcard RAMIC 已停止，新代码和临时 daemon 均证明回环绑定，本地 forward 也明确回环。功能代码没有回归，Bridge 的人工非回环能力没有被删除。

当前交互式 Virtuoso 会话中的 RAMIC 仍需一次 CIW `load("/data/xum/virtuoso_bridge_xum/Aurora_s_Echo/virtuoso_bridge/virtuoso_setup.il")`，之后才能完成 CIW-attached `RBLastBind`、`1+2` 和 VDA doctor 的最终只读验收。旧 daemon 已经不响应，Bridge 架构又规定 setup 必须由 CIW 加载；本轮没有用不受控 X11 键盘注入或重启 Virtuoso 来绕过这一边界。

loopback 解决外部网络暴露，但不是多用户服务器上的应用层认证。同机用户隔离、原生 Cadence listeners 及未来是否采用 token/权限化 Unix socket，需要管理员规则或独立协议 Gate；不得把它们包装成已验证安全。
