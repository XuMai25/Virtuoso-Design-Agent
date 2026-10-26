# 2026-10-18 Bridge main 升级验收

## 结论与范围

已在备份与独立分支上纳入上游 main `ecaf5a5c5499051ea4c4796a480e278a3e9c0da8`，
保留或用上游等价实现替代原私有安全/稳定性补丁。版本为 `1.0.0`，不是仅停留在 release tag。
VDA 源码与 task schema 不需要迁移；929 项本地测试通过，TSMC18 已有 OA→`si` DC/AC 和
TSMC N28 standalone preview 真实结果保持一致。默认已有 CIW 的新认证协议也已验收。

这是执行底座兼容验证，不是新电路设计完成声明。没有 OA 写入，没有覆盖人工 ADE state、
history、`cds.lib` 或 PDK 文件；没有新增 PVT、noise、linearity 或 topology search。

## 1. 修改前核对与备份

- VDA：分支 `codex/ade-inverter-quality-gate`，起点 `dfc981a`，与跟踪远端一致。
  起点已有 `AGENTS.md`、`README.md`、`docs/deferred-manual-gates.md` 修改，以及三份
  ADE L/基本操作文档未跟踪；全部先备份，本次未修改、暂存或提交这些既有改动。
- Bridge：原干净分支 `codex/vda-upstream-main-20260904`，起点
  `01565791fb261c73552e3e80590a389d2a6b7003`；旧上游基线 `c64461c`。
- 修改前备份 ref：`codex/backup-pre-main-20261018-0156579`。
  独立集成分支：`codex/vda-upstream-main-20261018`，两父 merge 保留旧私有历史与新 main。
  最终 Bridge merge 为 `e214d3a6e153a036d422b5453412365b8172cd06`；结束前重新查询上游
  main，仍为上述 `ecaf5a5c`。VDA 记录位于独立 `codex/bridge-main-upgrade-20261018`，备份 ref
  为 `codex/backup-pre-bridge-upgrade-20261018-dfc981a`。
  没有向 Arcadia 第三方 origin 推送。
- 本地备份根：`artifacts/upgrades/bridge-main-20261018`（忽略，不提交运行产物）。
  包括已 `git bundle verify` 的 Bridge/VDA bundles、原 Bridge source zip、完整原 Bridge venv、
  既有 VDA 文档逐文件副本。配置备份独立存于用户私有 Bridge backups 目录，未输出或提交内容。

| 备份文件 | SHA-256 |
| --- | --- |
| `bridge-pre-upgrade.bundle` | `51f133a349af7472ae43bfc66a7959b4ad1f8666ada88272e44b8d4394e2dc30` |
| `bridge-pre-upgrade-source.zip` | `4b9f21ed39db1954c366af44af986b87fb766e96c62a48d0515d9b04ad56daee` |
| `vda-pre-upgrade.bundle` | `a116d8ad3f05415b6d537576b65440cb3c18a897035516f58047ca30e1ff598a` |

远端修改前原有三份实际 runtime 文件归档为
`/data/xum/virtuoso_bridge_smoke/vda_bridge_upgrade_20261018/runtime-before.tgz`，
SHA-256 `3737847eaeed47e2b8e74a9cd778f966a41bf2180f9b9ae24f5d1b71c0f3fd46`。
当时没有 `daemon_identity.txt`；初次 tar 报该可选文件缺失，随后检查归档确实包含原
`ramic_bridge.il`、Python 3 daemon 和生成 setup，没有把报错当作完整性通过。

## 2. 保留、新增与采用上游等价实现

逐文件主记录仍是 Bridge checkout 的 `LOCAL_VDA_PATCH.md`，VDA 侧摘要见
[第三方补丁记录](../third-party/virtuoso-bridge-local-patch.md)。

- 保留显式两端 loopback/`GatewayPorts=no`、实际 bind fail-closed、Windows 无窗口与
  stale PID 处理、1 s/3 s 瞬态退避、总 deadline、发送前连接拒绝的一次 warm 恢复、
  managed status/restart runner cleanup、Windows complete-JSON 请求 framing。
- 采用上游原生 Windows liveness、target/state cache、默认 RAMIC loopback、HMAC、
  签名 owner、IPC resync、Busy 与 recoverable receipt；没有叠加旧 USER-query 重试。
  保持上游 lazy factory，在实际请求时获取 token/必要时安全 warm，不因 docs 调用启动 EDA。
- 新增可选工作区 token 路径，当前在 `/data/xum` 的私有 `.auth` 下，目录 700、文件 600。
  没有禁用认证或在远端 home 创建认证文件。
- live 暴露了完整签名响应已到达、Windows forward 不及时传递 EOF 的问题。现在仅当
  全部累积字节通过对应 nonce 的 HMAC 验证时完成接收；分片、UTF-8、篡改和大小/时限测试
  保留。非完整或伪造响应没有提前成功通道。
- 新版严格读图、quoted values、stored/CDF evidence、连通性、shell stdout、私有 netlist
  目录、Maestro session/job/history 与发布资源修复均保留；未通过“为兼容而还原整个旧文件”
  丢弃上游安全能力。完整操作影响见[操作说明](../bridge-1.0-operation-notes.md)。

## 3. 测试：基线与最终结果分开记录

| 环境与范围 | Passed | Failed | Errors | Skipped |
| --- | ---: | ---: | ---: | ---: |
| 修改前 VDA | 929 | 0 | 0 | 0 |
| 原私有 Bridge 0.8 / Windows | 914 | 34 | 0 | 11 |
| 纯上游 ecaf5a5c / Windows | 2148 | 76 | 2 | 149 |
| 最终集成 Bridge / Windows | 2192 | 71 | 2 | 149 |
| 纯上游 ecaf5a5c / Linux POSIX | 2286 | 0 | 0 | 89 |
| 最终集成 Bridge / Linux POSIX | 2325 | 0 | 0 | 89 |
| 最终私有兼容/transport 核心集 / Windows | 165 | 0 | 0 | 2 |
| 最终 VDA | 929 | 0 | 0 | 0 |

另外实际构建 wheel 与 source distribution，设置 `VB_TEST_DIST_DIR` 后两项资源字节完整性
检查均通过（`distribution-final.xml`）。完整集默认跳过这两项，不重复计入表中；CDF filter
YAML 与全部 package Python/IL/template 文件在两个包内均存在且字节一致。

最终 Bridge 总收集 2414 项；相对纯上游多 39 项。Windows 失败/错误的逐项集合比较没有新增，
原有五个失败不再出现，但不将减少的数字全部表述为生产 bug 修复。剩余问题包括 Windows
socket/half-close fixtures、POSIX shell/path/permission/symlink 假设、docs/cache 和
远端路径在本地 Windows 表示等，涉及 auth/recovery、GDS、doctor 等测试文件；不能泛称
“全部功能在 Windows 通过”。Linux 使用真实 POSIX `/tmp`，不是在 NTFS 上模拟 chmod。

可追溯 XML 均在备份根：`bridge-baseline.xml`、`upstream-windows.xml`、
`upstream-linux-posix.xml`、`bridge-merged-windows-final.xml`、
`bridge-merged-linux-final.xml`、`private-final.xml`、`vda-final-isolated.xml`。
中间环境错误没有删除：首次 Linux 缺 console entrypoint、一次 NTFS chmod 试验，以及 VDA
默认 pytest temp ACL 导致的 239 个 setup errors 均保留原报告；修正测试环境后独立重跑，
没有调整产品阈值或系统权限来美化结果。

VDA 最终使用显式短 basetemp 并关闭非必要 cache provider；`catalog`、inverter demo
`plan` 和 Bridge `pip check` 通过。Bridge 仍在原独立 venv 内 editable 安装，Python
3.11.15 未被全局替换；WSL Python 3.10 测试环境不进入生产 worker。

## 4. 真实 DC/AC 与 preview 兼容验证

### TSMC18：已有 OA 的同源路径

- 任务：`examples/tasks/tsmc18-common-source-spec-final-ac.bridge.json`，计划 token
  `88ed1e09dfb46c10`。目标 `Analog_VLSI/vda_tsmc18_cs_spec_001/schematic`，只读 OA，
  `allow_remote_write=false`；仿真 scratch 有明确 compute 授权。
- OA 实际 W/L/RD = `8 µm / 0.36 µm / 23.5 kΩ`，TT/27°C、1.8 V、VGS 0.58 V、CL 50 fF。
  回读与实际 `si` 网表匹配。新旧网表 SHA 完全相同：
  `c07d9cac5636d5a51e06505c7eb331d4572b32975259d7a66bf8ec77868f5581`。
- 新旧所有 28 个原始 DC/AC 指标逐项精确一致，原九项固定约束仍全部通过：gain
  `20.82670946804447 dB`、BW `125833556.8188726 Hz`、GBW `1383986793.5857651 Hz`、
  核心静态功耗 `68.32239401357106 µW`、OUT `0.9080131892672668 V`。
- run record：`artifacts/upgrades/bridge-main-20261018/tsmc18-ac-live.json`。
  实际网表与仿真输入/PSF/log 绑定到唯一 scratch
  `/data/xum/virtuoso_bridge_smoke/tsmc18/vda_tsmc18-common-source-spec-final-ac_e34f5743c48c`。
  OA 回读为 `bridge_readback`；实际网表/结果为 `eda_result`；跨条件汇总为
  `software_inference`。没有以 exit code 或 OA 存在替代性能验证。

### TSMC N28：已有两变体 standalone AC preview

- 复用 `common-source-cascode-netlist-preview.bridge.json`，不新增随机参数点；正常计划 token
  `250892d5b4b601e7`。一次 `run_parallel`，两候选、两 worker、一个 simulator/guard/inventory。
- 与 2026-09-04 同任务 run record 比较，125 个输出指标逐项完全一致；两点均完成 DC/AC
  解析和饱和护栏，原始值仍是 `eda_result`，A/B 差值及比值仍是 `software_inference`。
- run record：`artifacts/upgrades/bridge-main-20261018/n28-preview-live.json`；scratch
  `/data/xum/virtuoso_bridge_smoke/vda_netlist_preview_common-source-cascode-netlist-preview_852a7e1637de`。
  没有访问 OA、`si` 或 Maestro。它证明已有 preview 工作流迁移，不证明 N28 所有 OA 写入路径。

## 5. 当前会话验收与退出清理

- 先用自有 headless session/PID 54210、临时 65436/65437 验证新版认证与真实 TSMC18 AC，
  再清理经过用户/PID/restore 路径核对的全部自有 session process groups。
- 原有 Virtuoso PID 94710 与 126060 均保留。默认旧 daemon 97507 的 owner、bind 和 PID
  已独立核实；旧未认证协议在 Windows 不结束 EOF，普通客户端查询超时。一次性迁移适配
  仅接受三个独立已知的精确只读 PID/USER/bind 响应，然后通过旧公开 Bridge client 发出
  **一次** `RBStop()+load(generated setup)`。发送后超时没有重放。
- UI bootstrap 虽报告输入已注入，但没有产生新的 daemon，因此没有以此消息验收。一次性
  UI loader 已保留 audit 原件并替换为 no-op，避免迟到输入造成重复 restart；历史坐标与窗口
  号未进入 VDA/Bridge 生产源码。真正完成状态来自随后独立的新认证握手。
- 新 daemon PID 85510 的签名 owner PID = **94710**，实际 bind
  `127.0.0.1:65346`，`1+2 → 3`；Bridge status 核对同用户 xum、CIW host cad52、
  Virtuoso 6.1.8 和 Spectre 21.1。VDA doctor 在 TSMC18 与 N28 profile 均通过。
- 默认配置恢复原远端/本地 65346/65347，保留工作区 token 配置。运行中的 managed
  `ssh.exe` PID 63688 的 `MainWindowHandle=0`；最后正式 `vda bridge stop` 清理 tunnel。
- 人工 `cds.lib` 前后 SHA 不变：
  `377ad9f53db1c9ad07683cc8cab027899569b38f55eb8f1286fe45f1867bb6ed`。
  自有临时 Virtuoso、Spectre/si 和本地 tunnel 不作为长期后台服务保留；原有交互式 Virtuoso
  及其新认证 daemon 则是有意保留的用户会话。备份和仿真证据有意保留，不伪称零磁盘产物。
  最终本地 65347/65437 listener = 0，匹配 managed SSH = 0；远端没有自有临时 session、
  Xvfb、Spectre/si 遗留。两个只读测试 worktree 已移除，可由 Git refs 重建；备份 refs、
  源码/venv 和测试环境保留用于回退与复核。

## 6. 回退与未验证边界

回退先停新任务并核对 session 空闲；使用当前可认证 client 做受控的 runtime 回退/重载，
确认旧协议实际起来后，再切换备份 Bridge ref/原 venv 和经核对的旧配置。不要先换旧 client
再盲试新 daemon，也不要仅回退 source 而留下 client/daemon/token 不一致。原 venv 备份
可恢复，不需要删除当前环境来尝试；备份与私有分支均保留。

本次没有逐项 live 验收全部新 SOS/GDS/layout/Monte Carlo、共享弹窗恢复、所有参数类型、
全部 OA 写操作或人工 ADE state 迁移。Windows 上游平台问题仍需真实需求驱动的独立修复。
这不阻塞已经实测的 VDA DC/AC/preview 路径，但不能用版本号替代上述边界的验收。
