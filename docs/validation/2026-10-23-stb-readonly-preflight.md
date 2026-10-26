# 2026-10-23 STB 只读验收：前检超时，未提交计算

> 后续状态：用户批准隔离接入及按原 5X2Y2Z PDK 继续后，已复用原 smoke 完成一次
> DC＋STB 和同作业原生对照；见[真实验收](2026-10-23-n28-stb-native-acceptance.md)。
> 以下保留先前失败及当时授权边界，不代表最新执行仍阻塞。

## 结论

状态为 **blocked at read-only Bridge preflight / STB not executed**。
不据此判断目标电路、STB 实现或服务器故障；尚未取得当前 OA 数据。
按用户“遇到状态漂移或新问题先停下来”的要求，本轮没有重试或自动恢复。

## 已批准范围

- 复用 `vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`。
- PDK 为 `nics4304_tsmc28`；不写 OA、不覆盖 cellview、不调参。
- 计划一次 OA→si netlisting、一次包含 DC operating point 与 STB 的 Spectre 作业。
- 从同一作业读取原生 margin，并比对执行前后 OA；不追加性能分析或 ADE 作业。
- 新问题先停下，不重启或关闭现有 Virtuoso，不修改网络、TUN、认证或服务器配置。

## 实际执行

先使用现有只读 inspect 任务及其重新计算的计划 token：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent plan examples\tasks\differential-pair-current-mirror-inspect.bridge.json
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent run examples\tasks\differential-pair-current-mirror-inspect.bridge.json --adapter bridge --execute --token b07612cb524dabd9 --bridge-python C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\python.exe --output artifacts\stb-live-20261023\01-preflight-inspect.json
```

该任务的 `allow_remote_compute=false`、`allow_remote_write=false`、
`replace_existing=false`。计划 token 仅对应本次所用 inspect 任务，不是 STB 执行 token。

run record 的唯一 action 为 `bridge.probe`，状态 `failed`，错误为：

```text
BridgeWorkerError: RuntimeError: VDA could not verify the RAMIC daemon bind address: Socket timeout after 5s
```

记录时间为 UTC `2026-10-22T16:17:01.888336Z` 至
`2026-10-22T16:17:21.267452Z`，对应北京时间 2026-10-23 00:17。
整个 action 约 19.38 秒；5 秒是错误中报告的 socket 等待窗口，不是整个前检耗时。

安全检查未完成即拒绝继续；没有跳过实际 loopback bind 检查，也没有关闭认证。
这不能证明 daemon 非回环监听，只能证明本次无法核实其监听状态。

## 执行数量与证据边界

| 项目 | 实际情况 |
| --- | --- |
| 目标 OA 结构回读 | 未开始；前后状态均未验证 |
| OA 写入 / 参数调整 | 0；未调用写入路径 |
| si netlisting | 0 |
| Spectre 提交 | 0 |
| 原生 margin / VDA 提取对照 | 未执行 |
| 自动重试 / 会话重启 / 网络修改 | 0 |

唯一新 run record 位于 `artifacts/stb-live-20261023/01-preflight-inspect.json`，
失败来源为 `system_event`，不是 `eda_result` 或目标 `bridge_readback`。
本轮没有修改 VDA 生产代码、第三方 Bridge 或 Obsidian Vault；仅新增此记录。
本地进程核对时未发现命令行匹配
`virtuoso_design_agent.adapters.bridge_worker` 的遗留 Python worker；
这不是对所有共享 tunnel、远端 daemon 或其他任务资源的清理声明。

## 继续前需要处理

先另行确认是否进行仅只读的 managed tunnel / Bridge endpoint 状态排查。
在用户确认前不启动新远端动作。连接恢复后必须重新取得新鲜 OA 回读并与批准基线比较；
如有漂移继续停止，不恢复旧尺寸、不自动修改目标。

原始 PSF 持久留存及同作业 native margin 的接入仍是计算启动前的必要条件；
本轮未开始实现或验证这两项，不能因前检失败而表述为 STB 已通过。

## 连接恢复后的再次前检（2026-10-23）

用户在[定向重启与只读恢复验收](2026-10-23-virtuoso-restart-readonly-acceptance.md)后，
重新批准同一目标、不写 OA、一次 DC＋STB Spectre 作业，并要求新问题先停。

重新计算原 inspect task 的计划，token 仍为 `b07612cb524dabd9`，执行一次只读前检。
新记录：`artifacts/stb-live-20261023/02-recovered-preflight-inspect.json`。

- `bridge.probe` 通过：Bridge `1.0.0`、`1+2=3`、实际 bind `127.0.0.1:65346`。
- `schematic.inspect` 失败：

  ```text
  read_schematic could not open schematic vb_pdk_smoke/vda_diffpair_active_gate6_001
  ```

- 本次记录时间 UTC `2026-10-22T18:39:52.241365Z` 至
  `2026-10-22T18:39:53.935201Z`（北京时间 2026-10-23 02:39），没有取得新鲜目标 OA。
- 遇到该新问题立即停止远端动作；没有重试、修复映射、改用其他 cell 或提交 Spectre。
  本次 OA 写入、si netlisting 和 Spectre 提交均为 0。

仅复核本地保存的 `/data/xum/Analog_VLSI/icfb/cds.lib` 备份，未见
`DEFINE vb_pdk_smoke` 或用户 smoke 库配置的 include；它引用 N28 PDK、Cadence 自带库
以及当前 Analog_VLSI/接收机用户库。恢复验收已确认该文件字节未变。
因此“新会话缺少 smoke 库映射”是有依据的优先诊断，仍不是本次独立核实的完整运行时库表；
尚未排除目标路径不可读、文件缺失或其他打开失败原因，不能声称目标 OA 已漂移或损坏。

后续先获准只读核对目标 `sch.oa` 的存在/可读性、当前 DD 库路径与原 smoke 配置，
再提出确切的最小接入方式。不能为绕过前检改系统/PDK、覆盖现有 `cds.lib`、
重建目标设计或自动更换电路。新鲜 OA 基线、原始 PSF 持久保存及同作业原生 margin
对照仍未完成；本次不能报告任何 PM/GM 或设计稳定性结论。

后续获准的只读诊断已确认：目标文件可读，但当前 DD 缺少测试库，且同名 N28 指向
另一 PDK 目录；见[库上下文诊断与最小接入提案](2026-10-23-stb-library-context-diagnosis.md)。
该诊断没有恢复目标访问或运行计算，STB 仍未执行。
