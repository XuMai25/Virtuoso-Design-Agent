# Bridge 1.0 main 集成后的操作与实现边界

2026-10-18，VDA 的独立 Bridge 环境从私有 `0156579` 升级到包含上游
`ecaf5a5c5499051ea4c4796a480e278a3e9c0da8` 的独立分支
`codex/vda-upstream-main-20261018`。版本字符串为 `1.0.0`，但本次纳入的是
2026-10-18 main，而不只是 10 月 5 日的 release tag。备份、测试与 live 证据见
[升级验收](validation/2026-10-18-bridge-main-upgrade.md)；逐文件私有差异见
[第三方补丁记录](third-party/virtuoso-bridge-local-patch.md)。

## 1. 认证与旧会话升级

- 新 client/daemon 使用 token HMAC、签名 capability handshake 和 Virtuoso owner PID。
  只更新 Python 包并不会替换已运行的 daemon；旧 0.8 daemon 不能作为新 client 的健康端点。
- 不通过关闭认证解决兼容问题；token 内容不打印、不提交。仍保留 SSH、两端回环和
  VDA 实际 bind fail-closed 检查。回环不再是唯一的应用层保护，但也不因此取消回环。
- 本机新增可选 `VB_REMOTE_TOKEN_PATH`，profile 形式为
  `VB_REMOTE_TOKEN_PATH_<profile>`。当前将 token 放在批准工作区的
  `/data/xum/virtuoso_bridge_xum/Aurora_s_Echo/.auth/bridge_token`，目录 700、文件 600，
  避免上游默认首次初始化写入远端 home。未设置时保留上游默认行为；本服务器应保留此设置。
- client、token 路径与生成 setup 必须匹配；不要对一个仍运行的 daemon 单独换 token。
  本地认证缓存也属于秘密，不放入 run record 或 Git。
- 更新 daemon 前核实选定 CIW 的 PID、用户、实际 bind、弹窗和计算状态。只重启该会话的
  RAMIC 子进程，不退出 Virtuoso，不关闭 ADE，不改 OA。发送后断线是未知结果：先用新签名
  handshake、owner PID、`1+2` 和 doctor 验收，不能重复发送 restart。
- 上游 `bootstrap --window <现场识别的 CIW ID>` 是显式 opt-in，不是自动恢复。
  本次发现“输入已注入”不等于命令框获得焦点或 setup 已加载；不得仅凭该消息宣称成功，
  也不得把历史窗口号或坐标固化成通用默认。现有 0.8→1.0 迁移的一次性适配不成为长期
  未认证降级通道。

正常验收使用：

```powershell
.\.venv\Scripts\vda.exe bridge start
.\.venv\Scripts\vda.exe bridge status
.\.venv\Scripts\vda.exe doctor --adapter bridge --pdk-profile nics4304_tsmc18
.\.venv\Scripts\vda.exe bridge stop
```

profile 按项目选择；N28 仍是默认，TSMC18 不与其混用。start/stop 管理 tunnel，
不等于关闭 Virtuoso。默认端口仍为远端 65346、本地 65347；临时升级测试端口已撤销。

## 2. 共享 CIW、超时与不确定结果

上游新增按 client 显式启用的 `client.dialogs.enable_guard()`，以及
`protect_inflight=True` 的认证请求 receipt。它们能把未发送、执行中和未知结果分开，
但不是桌面锁，也不能阻止人工或其他无 guard client 改动会话。

- VDA 仍使用既有 task token、checkpoint、前置指纹和写后回读，不把一次升级自动包装成
  所有 operation 已消费 recoverable receipts。后续接入应保存原 request handle，并围绕原
  receipt、Maestro marker/history 和 OA 回读对账；不能重新提交相同写入当作恢复。
- `BusyError` 的有界重试保留；网络 reset、发送后 timeout 不自动重放 SKILL。
  本地恢复只允许明确发生于 `connect()`、尚未发送 payload 的连接拒绝，且受总 deadline 限制。
- 不启用自动批量关闭弹窗。上游 legacy bulk dismissal 现在需要显式 opt-in；先只读 inspect，
  必要时对一个已核实窗口执行获准操作。清楚的 inspect 也不证明之前的操作没有执行。
- Windows managed SSH 保留无控制台和 complete-JSON 请求 framing；收到完整签名且 nonce
  匹配的响应即可完成，不再依赖 OpenSSH 及时传递 EOF。分片、UTF-8 和篡改检查仍保留。

## 3. 读图与参数证据更严格

新版 reader 会拒绝不完整的实例/网络/pin 读取，保留引号与字符串值，改进 mosaic 和几何
连通性。读取失败不能当作空电路，不能据此覆盖已有 cellview。

Bridge 增加可选 `include_param_evidence=True`：

```python
from virtuoso_bridge.virtuoso.schematic.reader import read_schematic

data = read_schematic(
    client, library, cell,
    param_filters=None,
    include_param_evidence=True,
)
```

每个实例的 `param_evidence.stored` 与 `.cdf` 分开保存，覆盖 compact `params` 中省略的
nil、空值和长值。它们是 `bridge_readback`，不是最终网表参数；CDF 值也不证明 callback
已按预期运行。VDA 现有路径继续使用 `param_filters=None`，但这只关闭名称过滤，不能把
compact `params` 表述为所有参数的无损快照。后续验收的显式 task 接入见下文。
参数生效仍需 callback 后回读及实际 OA→`si` 网表对照。

2026-10-18 后续验收已把该选项接入正式 `schematic.inspect` task：

```json
{"operation": "schematic.inspect", "include_param_evidence": true}
```

此处只展示新增字段，仍须提供正常任务的 id、circuit、target 与 profile。
四种现有 schematic circuit 路径共用同一读取 helper；结果位于
`bridge_schematic.instances[].param_evidence`，沿用 action 的 `bridge_readback` 来源。
不把 stored 与 CDF 合并，不将 nil/空串改成缺失，不求值参数表达式，也不改变既有 compact
`instance_parameters` 或拓扑指纹。只读任务的 plan token 会绑定这个选项；未声明时旧任务
token 不变。该字段仅用于 `schematic.inspect`，其他 operation 拒绝，避免声明后被静默忽略。
旧 Bridge 不支持时明确失败；不会回退为缺证据的成功结果。demo adapter 也明确拒绝此
选项，不伪造 stored/CDF 或静默忽略；普通 demo inspection 不受影响。

按需在 onboarding、查找遗漏参数或诊断 callback 时读取一次。搜索循环仍用 compact
读取和既有定向写后校验，不在每个候选上重复传输整份 CDF。一次读取已包含三类表，
不需要随后逐实例补发同类查询。与 OA 参数的最终绑定仍由实际网表验证。

## 4. Shell、netlist、Maestro 与人工 ADE

- 上游 `run_shell_command()` 修复了只返回 `t/nil` 的行为，现在返回实际 stdout 与失败信息。
  需要 shell 的代码应读取结构化状态和 output，不能继续把输出与字符串 `t` 比较。
  当前 VDA 外部 `si` 保持 OA-host `ssh_runner.run_command()`：不重新回到 CIW 同步等待。
- 上游通用 netlist export 改用私有 run 目录，不写 `~/simulation`。VDA 仍保留经验证的
  OA→`si` 环境、参数一致性和输入/结果 manifest；不因有新 API 就绕过来源证据。
- Maestro 增加可恢复 job/history、session locks 和显式状态控制。history 名称、回调成功或
  setup 已保存，仍不能替代 exact simulator input、PSF/log 和指标证据。VDA 正式 `ade.*`
  operation 仍按现有契约执行，不自动宣称新 Monte Carlo/SOS 功能已经 live 验收。
- 单测试人工交接继续优先 ADE L，保留已有 ADE L 操作约束。
  本次没有迁移或覆盖人工 ADE state、参数扫描文件、history，也不把 ViVA XL 标题当成 ADE XL。
- CDF filter YAML 现已纳入 wheel/source distribution；split-host、Spectre scoped pools、strict
  PSF reader 和已有并行 preview 都保留。网站、SOS/layout/GDS 等上游内容的存在不等于 VDA
  新增这些正式能力，更不等于通过相应工艺或电气 Gate。

本次是执行底座升级，不新增电路搜索域、不扩大 L5B 完成声明，不取消任何原正交 operation。

## 5. 经过验收的省时与恢复用法

- 同一批任务复用已初始化的 CIW 与 managed tunnel。不要逐候选 start/stop 或创建新的
  Virtuoso；首次 PDK 加载可能出现提示，fresh session 不是零成本的连接替代品。
- 正常读图用一次 `read_schematic`，需要完整参数时显式开启 evidence。返回完整数据存
  run record，给 agent 的日常摘要只列目标参数、差异、SHA 和失败原因。
- 只有可能阻塞/产生副作用、且确实需要共享 CIW 保护的调用才显式开启
  `dialogs.enable_guard(protect_inflight=True)`。guard 每次都有 X11/认证 preflight 成本；
  纯 `-nographE` 无映射 CIW 会返回 indeterminate，不得伪造 clear 或关闭认证绕过。
- 请求不确定时先持久化 `result.metadata.request_handle`，后续调用
  `client.requests.receipt(handle)`。它查询原结果，不启动第二次操作；不要自动重跑整个
  VDA task。现有 VDA 所有高层 operation 尚未自动持久化这些 handles，不能宣称全面接入。
- 若确需更换 tunnel，保留相同 daemon，重新建立 forward 后必须保存其管理状态。
  手动使用 Bridge `tunnel.ensure_tunnel()` 时配套 `tunnel.save_state()`；否则下一进程
  无法可靠接管/清理该 forward。receipt-only client 使用已认证 endpoint/token，
  不在 pending CIW 上运行 factory 的 SKILL 身份探测。token 只在内存中传递，不进入记录。
- Windows direct/receipt-only client 现在也避开 `SHUT_WR`；不再只有带 tunnel 对象的
  client 享有该修正。`tools/skill_exec.py` 同样支持完整签名响应完成，保持总等待 budget。
  发送后 reset/timeout 不新增自动重试。

实测、失败记录和测试边界见[新功能验收](validation/2026-10-18-bridge-feature-workflow.md)。

## 6. Windows TCP reset 暂缓排查（2026-10-19）

用户决定先使用 VDA，连接重置保留为 open/deferred，不再为诊断关闭 TUN 或修改网络配置。
正常有人监督的设计可以继续，长时间无人值守可靠性仍未完全验收。写入或仿真提交后断线
必须先查询原请求/作业并独立回读，不能把“没收到回执”当作“没执行”而从头重跑任务。
当前各高层 operation 尚未全部集成自动 receipt 持久化；继续沿用其已有 checkpoint 和
前置指纹校验。详细证据、失败位置、恢复注意事项及重新开启条件见
[Windows TCP reset 遗留记录](validation/2026-10-19-windows-tcp-reset-deferred.md)。
