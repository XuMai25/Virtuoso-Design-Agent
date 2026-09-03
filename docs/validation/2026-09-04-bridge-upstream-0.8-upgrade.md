# 2026-09-04 virtuoso-bridge-lite 上游 0.8 升级与兼容验证

## 结论

第三方 Bridge 已从旧 `0.7` 基线升级到 GitHub 上游 main `c64461c`（含 `v0.8.0`），并在私有分支保留 VDA 已验证的安全、Windows 和 transport 修复。当前状态可称为：**Bridge 0.8 execution substrate integrated with VDA private hardening; existing VDA workflow and read-only live connectivity verified; unrelated upstream Windows portability gaps remain**。

这不是新的电路设计 Gate，也没有把任何上游 API 的存在包装成 L5B 设计证据。

## 上游核对与版本隔离

- 上游仓库：`https://github.com/Arcadia-1/virtuoso-bridge-lite`。
- 拉取后最新 main：`c64461c0bdc44330c143d386a8aaf3342088a59e`。
- 新 tag：`v0.8.0`。
- 升级前本地私有 tip：`ebf7e5018a886f79403e55aaef1c5805a9d38302`。
- 源码改动前先建立备份：`codex/backup-pre-upstream-20260904-ebf7e50`。
- 集成分支：`codex/vda-upstream-main-20260904`。
- 两父 merge：`106c61ee0d65bae1b1d20c86a7c4151ffe95c3e0`，父提交为上游 `c64461c` 和私有 `ebf7e50`。
- Bridge 内说明提交：`731b67f`。
- 没有向第三方 origin 推送任何私有分支或提交。

## 采用的上游变化

本次不复制实现，而是让 VDA 继续通过 Bridge 独立 `.venv` 使用上游 0.8 代码：

1. scoped Spectre pools、并行仿真目录隔离和 operating-point 失败处理；
2. GUI/deploy/daemon/Spectre 分主机角色及 safe bootstrap；
3. Paramiko session multiplexing 与可选 SOCKS5 transport；
4. strict PSF accessor 与 `ocnPrint` 格式控制；
5. schematic netlist import/export、semantic cleanup、确定性 constraint planner；
6. Maestro API、corner netlist、output escaping 和 run-timeout 修复；
7. library/category、symbol、layout/GDS、docs search 等上游接口保持可用，但不自动进入 VDA 产品声明。

## 保留和合并后的私有修改

- RAMIC 新 daemon 默认 `127.0.0.1`，并回读真实 `RBLastBind`。
- OpenSSH 自动与手工 fallback 均为 `GatewayPorts=no` 和显式本地 loopback forward。
- Windows tunnel 使用 `CREATE_NO_WINDOW`、`SW_HIDE`、`CREATE_NEW_PROCESS_GROUP`，不组合 `DETACHED_PROCESS`。
- Windows managed tunnel 使用 complete-JSON request framing；Python 3/2.7 daemon 收到完整 JSON 即派发，同时兼容 EOF 客户端。
- stale state、auto-warm、1 s/3 s 瞬态退避和仅限发送前连接拒绝的一次恢复保留；恢复等待现受总 deadline 限制。
- user/bind guard 保留；只读查询允许一次立即 retry，空 USER、缺少 bind、非回环、wrong endpoint 或 daemon 无响应均不会被 status 标为健康。
- `VirtuosoClient.from_env()` 支持只设置新式 split-host roles 的 cold start。
- VDA 自身的远端写入/计算授权、安全 token、library/cell 约束和证据分类没有改变。

Bridge checkout 中 `LOCAL_VDA_PATCH.md` 记录 19 个差异文件和升级方法；VDA 的聚合清单见 `docs/third-party/virtuoso-bridge-local-patch.md`。

## 本地验证

### 未修改上游基线

在合并私有 parent 前，Bridge 上游 main 的 Windows 完整基线为：

- `883 passed`
- `35 failed`
- `11 skipped`

失败主要来自 Unix `sh/rm/tail` 假设、Windows symlink 权限、长路径和 POSIX/Windows path fixture。

### 最终升级分支

- transport/security/split-host/Paramiko 集：`132 passed in 24.13s`。
- VDA：`910 passed in 5.95s`。
- `vda catalog`：成功。
- `vda plan examples\tasks\inverter-close-loop.demo.json`：成功，生成正常只规划 token。
- Python `compileall` 与 `git diff --check`：成功。

Bridge 最终完整集使用固定短目录 `C:\vbt\f`：

- collected：959
- passed：915
- failed：33
- skipped：11

33 项失败只出现在未修改的上游测试文件：`test_docs_search.py`、`test_layout_streamout.py`、`test_schematic_netlist.py`、`test_spectre_psf.py`、`test_ssh_control_master.py`。其中一项 symlink 需要 Windows 特权；一项刻意构造的 250 字符目录在再读取子文件时跨过传统 `MAX_PATH`；GDS/docs 项依赖 Unix 命令；两个 schematic netlist fixture 把远端 POSIX 路径表示成 Windows `Path`。这些边界没有被 deselect，也没有为提高通过数而修改无关产品代码。

## 真实只读验收

授权范围只包含 `/data/xum` 内既有 VDA Bridge runtime 更新和读连接；没有 OA target、OA read/write 或 Spectre analysis。

最终 `vda bridge start -> status -> doctor -> stop` 得到：

- Bridge：`0.8.0`
- daemon host/tunnel target：`nics4304-cad1`，实际 CIW/SSH hostname `cad52`
- daemon user：`xum`
- tunnel user：`xum`
- daemon bind：`127.0.0.1:65346`
- local forward：`127.0.0.1:65347`
- Virtuoso：`6.1.8-64b`
- Spectre binary probe：`21.1.0`
- VDA doctor evidence：`connected=true`、`skill_probe=3`、`daemon_bind=127.0.0.1:65346`
- stop 后 local port 65347 listener：0

第一次合并态 status 曾因用裸 client 对 Windows managed tunnel 执行 half-close而假报 `NO RESPONSE`，但紧接着 doctor 成功。修正 status 复用 tunnel context 后，status 与 doctor 一致。身份查询又暴露一次 5 s 瞬态超时；只对 USER/bind 两个幂等读查询增加一次立即 retry 后，真实 status 得到 user 一致证据。这些异常与修复均被保留，没有用单独 return code 0 取代结构化结果。

## 仍未验证的边界

- 33 项上游 Windows portability 测试未闭合；当前 VDA 不依赖其中的 docs search、GDS local streamout 或本地 spiceIn fixture。
- 上游新增 schematic planner/netlist、GDS、library/category 和全部 Maestro 变化只完成本地回归，未逐项形成新的远端 OA live Gate。
- Paramiko/SOCKS5 和真正 split-host 部署完成单测，没有在 nics4304 真实环境启用；当前 live profile 仍是 OpenSSH 单 host。
- loopback 不等于多用户服务器上的应用层认证；原生 Cadence listener 也不属于本 Gate。
- 本轮没有 OA 或 Spectre 结果，因此没有新增 `eda_result`，只有 Bridge/VDA 状态和 SKILL 的 `bridge_readback`。

## 下一步

继续 L5B 主线，而不是为了验证新版本重复扫已知 fixture：对用户给定的大致拓扑执行 read-only onboarding，生成最小 DC/AC 任务；DC 合格后才做有界参数和局部 topology refinement；最终 winner 再做任务需要的质量/PVT。只有该真实模块遇到 Bridge 0.8 新 API 能解决的具体缺口时，才新增对应 VDA operation、证据契约与 live Gate。
