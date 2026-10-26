# Windows 本地 TCP reset：遗留问题与使用边界

## 状态与决定

2026-10-19，用户决定暂缓排查，先使用 VDA。状态为 **open / deferred by user**，
不是已修复，也不是关闭问题。正常有人监督的设计可继续；不宣称长时间无人值守完全可靠。
不得为了继续诊断而关闭 TUN、修改系统代理、防火墙或服务器配置。

本次只整理 2026-10-18 已保存证据并核对报告，没有新增测试、远端计算、OA 写入或网络变更。
VDA 基线为 `e0aea2d`；Bridge 私有分支 `codex/vda-upstream-main-20261018` 的 tip 为
`3688192f438833d8da7565b69a311547177128ac`。诊断阶段没有继续修改 Bridge 生产源码或测试断言。

## 已观察到的现象

上一轮较广 Windows 集的三项失败分别涉及 malformed capability 负 owner PID、standalone
显式 auth-off fixture、pending request 的第二提交 Busy 检查。它们收到 `WinError 10054`，
而非预期诊断或回复；不等于三个正式功能全部不可用。原报告见
[Bridge 新功能验收](2026-10-18-bridge-feature-workflow.md)。

后续诊断表明失败具有间歇性：

| 已保存报告 | Passed | Failed | Skipped | 边界 |
| --- | ---: | ---: | ---: | --- |
| `baseline.xml` | 10 | 0 | 0 | 小集合通过，不证明故障消失 |
| `baseline-broad.xml` | 116 | 2 | 6 | 原 `127.0.0.1`，三个相关测试文件 |
| `isolated-loopback-ab.xml` | 118 | 0 | 6 | 诊断 wrapper 将 bind/connect 改到 `127.0.0.2`，断言不变 |
| `linux-baseline.xml` | 122 | 0 | 2 | WSL，同三个文件；不是原生 Windows 验收 |

集合有交叠，不相加。后续两项失败为 standalone auth-off 与 popup/request recovery；
失败位置变化不能被解释为原三项已有其中一项修好。

不依赖 Bridge、SSH、Virtuoso 的最小 Python TCP 程序也能复现：

- `127.0.0.1`：直接 close 100 次中 3 次失败；先 SHUT_RDWR 再 close 100 次中 5 次失败。
- `127.0.0.2`：同样两组各 100 次均未复现。
- `.1` 的 8 次失败中有 7 次已经收到全部 108 bytes，另 1 次收到 0 bytes。
  因此不能只修补“收到数据后把 reset 当成功”，也不能把 EOF 异常统一忽略。
- 这些是合成小报文诊断，不是生产任务失败率，不能把 8/200 外推成真实设计有 4% 失败。

上述结果支持继续关注本机 `.1` TCP 路径，但不足以归因到某个软件、驱动或 TUN。
`127.0.0.2`/IPv6 只用于诊断，没有修改正式 Bridge 的回环地址。

## TUN 对照未完成

用户曾批准短暂关闭 FlClash TUN 并立即恢复。实际关闭后当前联网/Codex 连接中断，
未取得有效的 TUN-off 测试报告。之后界面确认 TUN 已重新开启且有流量；不将其表述为
agent 已自动完成恢复，也不把本次中断作为 TUN 导致 TCP reset 的证据。

`socket_probe_tun-still-on-ui-click-failed.json` 是首次 UI 点击失败、TUN 仍开启时的运行，
已按真实状态命名；不得用作 TUN-off 对照。后续默认不再切换 TUN，不重复这条断网试验。

## 实际影响与安全使用

| 情况 | 处理要求 |
| --- | --- |
| 能证明 payload 尚未发送 | 可由现有有界 pre-send 恢复机制处理；不能扩大成所有 reset 自动重试 |
| 只读请求回复中断 | 本次读取无效；重新建立可靠连接后独立读取，不把空结果当空电路 |
| 写入已发送但回执丢失 | 状态未知，不等于未执行；保留 run/checkpoint/handle，先查询原请求并独立 OA 回读 |
| 仿真提交后断线 | 计算可能仍在运行；先检查已有作业/结果，不能直接提交第二份计算 |
| 当前 OA 或证据与原任务不符 | 停止写入和恢复，保留差异；不能靠覆盖未知状态强行续跑 |

日常操作约束：

1. 复用已有 CIW/managed tunnel，按原 `plan`、token、compute/write 开关和目标白名单执行。
   不关闭认证、不开放公网监听、不为了避错放宽断言，不在每个候选上重新创建会话。
2. 发送后 reset/timeout 不自动重放 SKILL。若已有 request handle，先查询原 receipt；
   没有 handle 时依靠该 operation 的独立回读、作业状态与 checkpoint 对账。
   VDA 高层 operation 尚未全部自动持久化 handles，不宣称全操作自动恢复。
3. 参数/拓扑写入中断后，核对同一 target、PDK、task/token 及当前 OA 指纹和字段值。
   只有原 operation 的恢复前置条件通过才允许用原 checkpoint 续跑，不能从头重跑整项任务。
4. receipt 完成只证明原请求状态；设计成功仍需要 OA 回读、实际网表、结果解析和规格判断。
   已有完整且相互一致的证据不会因别处一次 reset 自动作废，但当前 OA 仍须新鲜检查。
5. 结束时只清理本任务拥有的 worker/tunnel/计算，不杀用户 Virtuoso 会话或其他任务进程。
   保留必要证据和已知磁盘占用，不为了“清零”删除恢复资料。

现有认证、完整签名回复检查与 no-replay 行为未被放宽。没有观察到本诊断导致错误仿真数值、
OA 损坏或安全绕过，但这不是对所有失败组合零风险的证明。主要未闭合影响是中断、
结果待确认和额外恢复成本，尤其影响无人值守连续运行。

## 证据位置与重新开启条件

本地忽略目录：`artifacts/upgrades/bridge-reset-20261018/`。保留上述 XML、最小 socket
脚本、各地址/关闭方式 JSON、endpoint 检查和诊断 wrapper。它们不是正式工作流入口，
不得把换地址的绿色结果包装成原地址已修复。

修改前备份 ref 为 `codex/backup-pre-tcp-reset-20261018`，指向上述 Bridge tip；
`bridge-pre-fix.bundle` 也保留在该目录。本记录不新增备份验证声明，不提交私密配置或凭据。

仅当真实任务再次受阻、准备交付无人值守流程，或明确针对该故障的上游修复出现时重新评估。
先保存时间、平台/版本、operation、发送阶段、完整错误、request handle（无 token）、
checkpoint、独立 OA/作业状态与清理结果；不为了增加统计数字反复跑随机压力测试。
上游升级本身不自动解除本遗留项，必须用同条件证据复核。

正常用法入口：[Bridge 操作说明](../bridge-1.0-operation-notes.md)。
