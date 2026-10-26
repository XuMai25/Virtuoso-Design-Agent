# P1-b：既有 N28 原理图的联合参数、实际输入回执与恢复验收

日期：2026-10-23。结论：**批准范围内的 A/B/A 执行与恢复通过**；不是联合模型校准、
新点最优性或完整 L5B 质量闭环。没有新建电路，没有修改第三方 Bridge。

## 授权、目标与预算

- 既有目标：`vb_pdk_smoke/vda_pmos_loaded_cs_onboarding_001/schematic`，不新建、不替换。
- 当前真实映射：`nics4304_tsmc28`、`tsmcN28`、TSMC N28 **5X2Y2Z**、`top_tt`；
  VDD=0.9 V、VIN DC=0.35 V、温度27°C。未沿用另一金属栈的性能证据。
- 仅更新 `MN0.Wfg`、`MP0.Wfg` 及 PDK callback 的派生字段。长度30n、fingers/simM=1、
  master、端子、pins、图面保持。VBP/CL0 只进入 wrapper，不写 OA 源或 ADE。
- 先备份，最多3次 OA→`si`、3个包含 DC＋AC 的 Spectre 作业，完成后恢复新鲜初始值，
  不留下“最佳点”。常规步骤在批准范围内处理，状态漂移或恢复不明则停止。
- 新远端产物只进入 `/data/xum/virtuoso_bridge_smoke/p1b_joint_acceptance_20261023_01`；
  目标 OA 的两字段写入是单独批准范围。没有修改系统、PDK、现有 `cds.lib`、Vault 或网络配置。

| 阶段 | MN0.Wfg | MP0.Wfg | VBP_SRC DC | CL0 |
| --- | --- | --- | --- | --- |
| A 初始 | 500n | 1.9u | 0.560 V | 2.0 fF |
| B 单个联合探针 | 475n | 1.805u | 0.565 V | 2.2 fF |
| C 恢复后 | 初始原字符串500n | 初始原字符串1.9u | 0.560 V | 2.0 fF |

两尺寸同时缩小5%，保留本设计原宽度比3.8；它不是 NMOS/PMOS 的通用默认比例。
这是事前批准的一个原子候选，非随机搜索，也不把一个候选的 best 标记称为最优解。
原普通任务的 token、compute/write 开关、白名单、非覆盖和 checkpoint 检查照常生效。

## 复用及实际执行

复用原 `pmos-loaded-common-source-onboarding-tune.bridge.json` 的实际 master/端子/
parameter bindings、OP/AC 设置与约束，编译 A/C 为 `simulation.run`、B 为只有一个
候选的 `design.tune`，恢复为 `parameters.apply`。没有增加 operation、求解器或仿真器。

使用一个本任务独占的隔离 Virtuoso/Xvfb，UID/SID/PID、工作目录和 PDK 绑定均独立核对。
守护进程仅监听 `127.0.0.1:65436`，本地专用隧道65437。原有两个用户 Virtuoso 会话不参与
验收或关闭。隔离运行配置和 token 文件只保留本地/批准 scratch，不入 Git。

开始前下载 `schematic-before.tar.gz` 和 manifest；归档 SHA-256：
`77a6b45dd4e29568542b79726b726a7694ae0884fd3b4aa42a6feea18f06291c`。
备份不包含锁文件；原锁记录指向的旧 PID 已不存在，没有手工删除锁或强行解锁。

## 真实问题及处理，没有隐藏失败

1. **旧结构指纹形状不同。** 第一份计划在写入/网表之前拒绝。独立比较旧 run 与新鲜
   回读，差异限于5个 pin 新显式记录 `occurrences=1`，以及两处 `macro` 表达式的字符串
   解码。只有这组具体变化投影后完整结构/参数相等；随后任务绑定当前指纹并重新计划。
   不是忽略任意指纹漂移，也没有修改旧 run/token。
2. **Windows 换行导致输入哈希不一致。** 首次 `si` 已完成，但启动 guard 在真正 exec
   Spectre 前以86拒绝：转换后的 OA include 被写成 CRLF，声明哈希却按 LF 文本计算。
   修复 VDA `_generate_oa_netlist`：显式写 LF，并从实际上传文件字节计算哈希。原始 `si`
   文件和失败记录保留；核实没有 simulator log/波形后，A 复用该份 `si`，只重新形成
   正确的 LF envelope，未再生成网表。A 的恢复 helper 使用原 worker/TaskExecutor，
   仅在本次进程内注入已保留、重新核验的网表；不冒充普通 subprocess 的首次直通。
3. **首次开图超时。** B 在打开目标 editor 的30秒等待中超时，尚未调用参数 setter；
   立即恢复也被 poisoned lane 拒绝。没有重发写入、重启 daemon 或取消未知弹窗。
   原请求自然 resync 到 idle 后，完整 raw 回读与初始完全相等、未保存 cellview=0，
   才从原 checkpoint 恢复。失败 actions 和当时“恢复未验证”的历史 note 保留。
4. **数值型 CDF 的显示格式。** B 写入后 PDK 把 `475n` 改写为 `475.00n`，旧字符串
   校验错误拒绝；自动恢复成功，独立完整回读再次等于初始。没有消耗新 `si`/Spectre。
   修复 targeted verifier：独立返回实际 CDF 值和 `parseAsNumber`；只有该标志为t，
   且双方都是有限、普通 Spectre 标量字面量时，才允许工程单位等值比较。保留实际
   `475.00n` 回读，不把请求值伪装成回读；表达式、字符串、未知后缀和真正数值变化仍拒绝。
   executor 的请求 echo 与 checkpoint 状态比较仍严格；只在已确认数值 CDF 的确认路径
   接受等值格式。随后仍执行原任务、原候选、原 token，未放宽物理范围或调参。

实际总计：**3次 `si`，3个真正启动的 Spectre DC＋AC 作业**。另有上述一次启动前 guard
拒绝，不是第四个已运行仿真。没有追加候选、PVT、STB、noise 或 transient。
B/C 走修复后的普通 subprocess worker；A 的保留网表恢复边界如上明确保留。

## 输入、结果与恢复证据

每个真实作业均有16项持久文件清单，核对了逐文件大小和 SHA-256：实际 wrapper、原始及
转换后 OA 网表、`si.env`/log、Spectre log、DC/OP/AC PSF、启动/执行记录和输入回执。
每份 AC 有271个复数响应点，-3 dB 带宽及单位增益交越有实际包围区间，不凭 return code 判断。

逐作业检查：

- OA master/端子结构与 `si` 匹配，MN0/MP0 的实际 Wfg/l 分别与网表 w/l 匹配；B 的
  MN0 实际回读和网表均为 `475.00n`，MP0 为 `1.805u`。
- 独立 literal wrapper audit 核对4个源、1个负载、端子及 DC/AC 值；最终上传副本、
  原始 `si` 与转换后 include 的启动前哈希，与同作业下载回执逐项匹配。
- wrapper 明确含 `top_tt`、`temp=27`、DC/OP 和1 kHz–1 THz/30点每 decade 的 AC。
  `model_configuration.temperature_source=simulator_default` 是原模型解析层元数据，
  不能覆盖 wrapper/settings 中的显式27°C；本次以实际输入核对，不按该字段猜温度。
- PDK include 内容未展开/归档；回执不证明所有 PDK 依赖、任意 SPICE 文法或全部分析。
  literal 比较为 `software_inference`，实际启动文件回执与 OA 回读为 `bridge_readback`，
  Spectre DC/OP/复数 AC 为 `eda_result`，授权和候选范围为 `user_input`。

| 指标 | A 初始 | B 联合探针 | C 恢复后 |
| --- | ---: | ---: | ---: |
| 输出 DC / V | 0.636520 | 0.614252 | 0.636520 |
| 低频增益 / V/V | 3.232231 | 3.282129 | 3.232231 |
| -3 dB 带宽 / GHz | 5.721284 | 5.094321 | 5.721284 |
| GBW=A0×BW / GHz | 18.492509 | 16.720219 | 18.492509 |
| 单位增益交越 / GHz | 17.597889 | 15.936068 | 17.597889 |
| 0.9×供电电流幅值 / µW | 24.995489 | 23.443794 | 24.995489 |

最后一行是按明确源方向计算的 `software_inference`，原供电电流负号仍保留。
B 功耗约降6.208%，增益约升1.544%，但带宽约降10.958%、GBW约降9.584%；不是全面改善。
本次多个量同时变化，只证明联合输入生效，不能将变化分别归因于宽度、偏置或负载。

恢复后与初始 **25个记录指标完全相等**，原始 `si` 文件 SHA 也相同：
`ecb7306112e50eee15ac9c9af0e96a7e3ba6794aaa12e91b09c27023edaf4fdd`。
完整 raw 实例/master/端子→net/pins/参数/位置回读等于初始；恢复前后 canonical
placement（含导线和标签）相等。没有保留 B 作为最终 OA 参数。

这是可观察原值/结构的恢复，**不是 OA 二进制逐字节恢复**。`sch.oa` 经正常 callback/
保存后 SHA 从3083d1…变为ed311a…；其他 material 文件及原始 `sch.oa-` 保留。没有用
tar 覆盖当前 OA。关闭前后 material 文件哈希不变；原 `cds.lib` SHA 前后相同。

## 资源收尾

先确认本任务独占、lane idle、计算完成及修改 cellview=0，再只请求本次临时会话退出。
退出遇到 `Save Display Information`，用 Bridge 原生只读 X11 capture 核实确切 PID/window：
提示明确“继续且不保存请点 Cancel”，保存对象是 `display.drf`，不是 OA/ADE state。
因此仅对这一个已审阅的私有窗口发送一次 Escape；没有盲目关闭其他弹窗或写显示文件。
原 exit 未重发，随后原生进程正常退出，无需 SIGTERM/KILL。

最终本次 SID 内存活进程=0、远端65436和本地65437均关闭，原两个 Virtuoso PID及工作目录
保留；Bridge 已清理下载后的 UUID 仿真临时目录，原始 `si`、备份、本地 raw 和失败证据保留。
没有把远端 UUID log 已清理误报为未运行，计算计数由真实执行回执及 DC/AC/log文件对账。

## 本地文件与验证

本地证据根：`artifacts/p1b-joint-live-20261023/`，不提交原始产物/备份/运行配置。
关键文件：`A-recovered-run.json`、`B-format-fixed-run.json`、`restore-run.json`、`C-run.json`、
原始及恢复 checkpoint、`10-final-readback.json`、`11-cleanup.json`、`12-acceptance-audit.json`。
离线 audit 重核全部文件、typed testbench、尺寸 binding、实际输入回执和 A/C 一致性后 passed。

| 阶段 | 输入回执 SHA-256 |
| --- | --- |
| A | f0e571077ed6722833685eb3610e63643156f943f82c61e9c322f878267e69aa |
| B | 5df84b204af9c088d52254fb039e07de9282f3cc80bf961a5735d871ba7d67c4 |
| C | bf0931e6e59628a0bc52f85c6724f6670aecfed3d2f9e7cddbd7fbbd6627eb12 |

本地完整入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation `
  --output artifacts/local-validation/20261023-joint-live-final
```

主环境 **1283 passed**，1个已声明 Bridge import-only 模块交补验；Bridge 环境
**8 passed、0 skipped**，总状态 passed。`catalog` 和旧 inverter demo plan 通过。
回归聚焦 Windows 实际上传字节、数值 CDF 格式/非数值字符串/表达式/非有限值、真实数值
变化、缺失/重复回读、严格请求 echo 和 checkpoint；没有为验收增加电路或全分析。

## 仍保留的缺口与下一步

这次打通普通原子候选的“两字段＋偏置＋负载→实际 OA/si/输入→DC/AC→原值恢复”。
没有从不足的 PMOS 历史点硬拟合联合模型；本次只有一个新联合方向，没有逐轴独立训练
和未见留出，仍不能估计三个独立轴的可靠灵敏度，也不证明局部模型提出的新点有改善。
多个源/负载成员的 linked_names、其他 PDK/拓扑、首次开图阻塞的通用自动处理、STB 新
worker 路径与其他质量分析不因此升级为已验证。首次开图和退出显示提示的风险仍按现场
身份/状态处理，不硬编码此次 PID/window 或全局取消策略。

下一次有实际设计指标时，先用已有 OP/MNA、历史记录与数据检查判断哪个方向最影响决策，
确需模型校准才申请最小独立证据；不立即追加点来“补齐模型”，不为提升分级重跑电路。
