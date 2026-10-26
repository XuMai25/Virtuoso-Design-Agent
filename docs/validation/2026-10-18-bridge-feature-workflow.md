# 2026-10-18 Bridge 新功能与省时工作流验收

## 结论

本次按用户“测试、打磨工作流、以后少花跑的时间”要求，只验收三个有实际收益的环节：
一次读图保留 stored/CDF 参数证据；丢回执/等待耗尽后查询原请求而不重放；新测试 cell 的
可逆参数修改与真实 `si` 绑定。没有追加随机电路、全域搜索或重复电气扫描。

- 正式 `schematic.inspect` 新增可选 `include_param_evidence: true`，完整证据留在 run
  record，既有 compact 参数与拓扑指纹不变；不声明时旧任务 token 不变。
- Windows direct/receipt-only client 与 standalone `skill_exec.py` 的 half-close/reply
  framing 已修正，认证、回环和发送后不重放原则保留。
- 两种不确定请求均恢复原结果，SKILL 计数器和提交数都是 1。
- 新 TSMC18 cell 的 W 从 8→9→8 µm，实际三份 `si` 网表匹配；恢复后的 compact 参数、
  拓扑和原始网表 SHA 都与基线一致。此轮没有运行 Spectre，不新增电气性能结论。
- 最终 VDA **937 passed**；Bridge focused Linux **281 passed / 2 skipped**，最终
  Windows 私有核心集 **96 passed**。更广的 Windows 本地 TCP 集仍有 **3 项失败**，见下文。

## 备份、实现与授权边界

Bridge 修改前备份 ref 为 `codex/backup-pre-feature-validation-20261018`，指向升级 merge
`e214d3a6e153a036d422b5453412365b8172cd06`。本次补丁提交为
`3688192f438833d8da7565b69a311547177128ac`，仍位于独立
`codex/vda-upstream-main-20261018`，未推送第三方 origin。

修改后的 `bridge-feature-validation.bundle` 已通过 `git bundle verify`，SHA-256：
`9f1b44a07bd30b6bcf5850ac14b1b245e38440d340589a8b573dd0af04cd0df5`。
原源码、venv、配置备份及升级前后 bundles 保留。逐文件补丁见
[第三方记录](../third-party/virtuoso-bridge-local-patch.md)和 Bridge `LOCAL_VDA_PATCH.md`。

VDA 通过正常 TaskSpec/plan/token/CLI 执行，非直接越过 executor：

| 动作 | 计划 token | 实际范围 | CLI wall time |
| --- | --- | --- | ---: |
| create | `c77e9bf5e52e594f` | 新建测试 schematic；不覆盖 | 5.603 s |
| inspect | `d19a2ba6a8559066` | 一次结构读取含 stored/CDF | 2.508 s |
| binding discover | `4865024e522383ae` | 两次参数写入、三次 si、前后独立读回 | 61.332 s |

唯一 OA 写目标为 `Analog_VLSI/vda_bridge_features_20261018/schematic`，
profile `nics4304_tsmc18`，`replace_existing=false`。新 cell 保留为验收对象，最终
W/L/RD = 8 µm / 0.36 µm / 23.5 kΩ。原 `vda_tsmc18_cs_spec_001` 只读。
没有修改人工 ADE state、history、PDK、`cds.lib`、Vault 或原有两份 Virtuoso 会话。

## 参数证据与耗时

同一已初始化 client，依次 compact/full/compact/full 读取旧 TSMC18 cell：

| 读取 | wall time | 序列化 JSON bytes |
| --- | ---: | ---: |
| 首次 compact，包含首次 PDK 加载成本 | 7.247857 s | 3077 |
| stored/CDF evidence | 0.187654 s | 5602 |
| 热 compact | 0.132720 s | 3077 |
| 第二次 evidence | 0.196278 s | 5602 |

client 初始化另为 1.421064 s。四份结果剔除 `param_evidence` 后逐项一致。
完整参数数据量约为 compact 的 1.82 倍，所以不默认在每个候选上开启：onboarding 或参数
诊断时一次取全，之后复用已确认的角色/绑定，搜索仍做必要的定向写后回读。

这些是一次具体环境的工具读取耗时，不是前后版本性能 benchmark、统计置信区间或
agent 思考时间。本轮没有量化模型思考 token/时长，不能将 7.25→0.13 s 宣传为端到端
设计提速。实际可复用的结论是减少冷启动、重复读图和逐实例补查；完整原始数据保留在
artifact，日常 agent 摘要只需目标参数、差异、hash 和失败原因。

`bridge_schematic.instances[].param_evidence` 分开保留 typed stored/CDF，不合并、不
求值表达式、不把空串或 nil 当缺失。旧 Bridge 缺字段时明确失败；demo 不伪造证据。
读回是 `bridge_readback`，不是参数已经进入网表的证明。

## 真实 OA→si 可逆绑定

使用现有 `parameters.binding.discover`，没有新增电路专用 executor。

- 基线与探针 OA `MN0.w` 分别为 `8u`、`9u`，`wf` 随 callback 同步。
- `si` 中同一实例 `nch_mac` 的 `w` 也分别为 `8u`、`9u`；
  `ad/as/nrd/nrs/pd/ps` 六个派生参数的变化逐项与 OA 一致。
- 无 netlist structure change、无其他实例参数变化；得到
  `direct_literal_binding_with_derived_callbacks`，没有仅凭同名猜测映射。
- 恢复后 compact OA 表 SHA 为
  `09bf5fdaffc25dacbaab3f4c527c7f55184bf114ed015aab07c6a2aa07f5b52e`，与基线相同。
- 基线和恢复网表 SHA 相同：
  `59d8b0196b7dca2ecf7bc771dc224896f21b44a2b0061110ce888889f92b3eb7`；
  探针为 `4c216cfe5cb73224c8b23ec58ac474a7553b061fc87584417c7e059b5dc7ec21`。

三份网表、si 日志及 SHA manifest 已下载到本地
`artifacts/ade-captures/bridge-features-binding-20261018/c470c8b0499641c2bd858eb4996b6d80`。
对应远端三个唯一 netlist scratch 已由原有清理逻辑删除；可用本地副本追溯。
run record 保留各精确 remote path，均位于 `/data/xum/virtuoso_bridge_smoke/tsmc18`。

恢复断言覆盖声明的 compact table，不包括 compact 中省略的 nil/空值/长字段；丰富读取
本身不自动扩大 CAS 保护范围。未注入 OA 写入中断，此次 receipt 测试也不等于验证了
每个高层 OA operation 的恢复。网表是 `eda_result`，绑定分类是 `software_inference`，
声明的探针契约是 `user_input`，进程/清理属于 `system_event`。

## 请求恢复与 Windows 修复

1. **丢回执**：独立有映射 CIW，启用 `protect_inflight=True`；发送一次无 OA 副作用的
   SKILL 计数器递增，在服务器已受理之后注入连接错误。持久化原 handle，只替换本地
   tunnel，再由 receipt-only client 查询原请求，得到结果 1；独立计数器读回为 1。
   原结果查询耗时 0.371934 s，不包含 tunnel 重建时间。
2. **真实等待预算耗尽**：一次 `ipcSleep(8)` 后递增，client budget 4 s，约 4.156 s
   返回 running/unknown；六次 receipt 查询后完成，计数器仍为 1。由于剩余 X11 inspect
   预算不足，实际 phase 为 `awaiting_user`、`waiting_for_user=false`，不是字面
   `wait_timeout` 分支；没有伪造 clear、发送 SIGINT、重启 daemon 或重发原操作。

本机最小 socketpair 复现了不涉及 SSH 的 `SHUT_WR` 后空回复。因此 Windows direct
client 也需要完整 JSON dispatch，不能只修带 tunnel 的实例；POSIX half-close 保留。
standalone 工具同样仅在完整累积回复通过对应 nonce/HMAC 后提前结束，分片/篡改测试保留。
这不是 auth-off fallback，也没有新增发送后的自动重试。

VDA 各 operation 尚未统一自动持久化这些 request handles；现在证明 Bridge 恢复机制可用，
并记录安全操作流程，不宣称所有任务已自动恢复。操作说明见
[共享 CIW 与省时用法](../bridge-1.0-operation-notes.md)。

## 测试范围与未通过项

| 报告 | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| `vda-final.xml`，完整 VDA，含 8 个新增契约测试 | 937 | 0 | 0 |
| `linux-focused.xml`，auth/recovery/framing/reader | 281 | 0 | 2 |
| `linux-private.xml`，最终私有兼容与平台矩阵 | 29 | 0 | 0 |
| `windows-private.xml`，最终确定性私有核心集 | 96 | 0 | 0 |
| `windows-focused.xml`，更广本地 TCP 集 | 271 | 3 | 6 |

集合有交叠，不相加；本轮不把旧升级的 Linux 全量 2325 项当作新补丁后的全量运行。
`catalog`、inverter demo `plan` 和 diff whitespace check 通过。

Windows 三个失败分别为 malformed capability 负 PID、standalone 显式 auth-off 测试、
pending-request 的第二提交 Busy 测试，均收到 `WinError 10054` 而非期望诊断/回复。
它们仍 fail closed，但诊断和本地 TCP 稳定性未闭合；不能统称为“只是无关测试”或删除断言。
完整报告保留，不反复跑到偶然全绿。真实 Windows→SSH→认证 daemon 的上述路径通过，
不外推为所有本地 TCP/旧 daemon/弹窗/平台组合均稳定。

## 失败尝试与退出清理

- 初次纯 headless 会话没有映射 CIW，guard 返回 indeterminate；不能用该会话假装通过
  shared-CIW 验收。随后独立 Xvfb mapped 会话完成 receipt 测试。
- mapped 会话首次加载 PDK 时出现 `TSMC PDK setting information`，读取 60 s 超时。
  只读确认弹窗，不盲目 dismiss；当时未写 OA。结束该自有会话，改用既有项目工作目录
  初始化的独立 EDA headless 会话完成参数读取/创建/si。PDK 首次弹窗自动处理仍未验收。
- 一次手动重建 forward 的验收 harness 漏调用 `save_state()`，导致下一进程无法接管。
  核对 PID 7052、创建时间、父进程和精确 65437→65436 loopback 命令后，终止该自有树，
  修正 harness 为 `ensure_tunnel()` 后保存状态。生产 `warm()` 语义未改，未广泛杀进程。
- 三个自有 session SID 71366、76961、84125 的已记录进程全部退出；远端 65436 不再
  监听。最终 xum 的 EDA 进程列表只剩原有 Virtuoso PID 94710、126060，无 Xvfb/si/Spectre。
- 本地 65347/65437/65438 listener 与匹配 SSH forward 全部为 0；原有两个用户会话保留。
  人工 `cds.lib` SHA 保持
  `377ad9f53db1c9ad07683cc8cab027899569b38f55eb8f1286fe45f1867bb6ed`。
- 临时 session 日志、测试 cell、备份和本地证据有意保留，不声称零磁盘占用。

本轮证据根为忽略目录 `artifacts/upgrades/bridge-features-20261018`：含任务、计划、run
record、invocation timing、read timing、两种 request handle/receipt、cleanup verification
和测试 XML；不提交凭据、原始 PDK 参数表或运行产物。

下一步回到真实模块工作：复用会话、一次 onboarding、最小必要仿真、按差异迭代。
不再为验收新版 Bridge 单独重复另一个拓扑或更多参数点；真正的 UI 弹窗或跨操作恢复需求
出现时，再以具体失败路径补契约。
