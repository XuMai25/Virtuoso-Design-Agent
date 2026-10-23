# 2026-10-23 定向重启与 Bridge 只读恢复验收

## 范围与结论

用户允许保存当前工作后重启原 Bridge 所属 Virtuoso，随后允许不保留旧波形窗口，
并明确禁止修改系统、PDK 和已有设计文件。STB 保持暂停，不因连接恢复自动继续。

结果：**指定会话已恢复，Bridge/VDA 只读连接验收通过；没有新增电路性能证据。**

本页续接[保存前检查中止](2026-10-23-virtuoso-restart-preservation-blocked.md)和
[隧道恢复后认证中止](2026-10-23-tunnel-recovery-auth-blocked.md)，保留此前失败，不将其改写成成功。

## 保存与退出的实际情况

- 原会话 PID `94710` 的四份可编辑 schematic 均未修改；没有调用 `dbSave`。
- 四个 schematic 目录已备份到新目录的 `original_schematics.tgz`，并下载到本地；
  本地归档独立打开后包含四份 `sch.oa`。原 `cds.lib` 和启动身份另行备份。
- 旧 ViVA 波形的原生保存失败，用户随后明确允许不保留这 13 个旧波形窗口；没有声称保存成功。
- 发现一个没有可见 ADE GUI 的后台 ASI 对象。`asiSaveState` 返回 ADE-5048，
  表明不是有效 ADE 会话；没有产生可 Load 的 ADE state。
  其设计身份、模型、analysis、变量、options 和结果路径保存为新的原生 Lisp 数据快照，
  并用独立 `lineread` 回读比对。该快照不等于可恢复的 ADE setup。
- 正常退出曾被 Save Display Information 弹窗阻塞。先前自动输入没有可观察效果，
  未生成新的 DRF，未强制终止进程，也没有修改既存 `display.drf`。
- 用户随后明确表示 Virtuoso 已完全退出，并指出确认无需保存的显示设置应选 Cancel。
  本轮先用 OS 只读检查确认 `/proc/94710` 不存在、远端 `65346` 空闲，再启动替代会话；
  不再依赖旧弹窗快照。没有声称由 agent 成功点击退出或 Cancel。

后续处理同类弹窗应区分：系统/PDK 文件、已有设计内容、ADE setup、结果和临时显示设置。
已明确不需要保存的显示设置可按用户指示 Cancel；这不扩展为丢弃未保存设计或覆盖 PDK 的授权。

## 单次启动与只读验收

启动前确认原 `DISPLAY=localhost:28.0` 可访问。使用已有的一次性 guarded launcher，
仅在原进程退出、端口空闲且不存在 launch receipt 时启动一次：

- 新 Virtuoso PID：`48417`，Unix 用户 `xum`（UID `1068`）。
- 可执行文件：`/tools/Cadence/IC618/tools.lnx86/dfII/bin/64bit/virtuoso`。
- 工作目录：`/data/xum/Analog_VLSI/icfb`。
- 使用 `-nocdsinit` 和本次独立 `restore_bridge.il`；不重放原历史实验 `start.il`。
- 仅加载已有 Bridge setup；启动日志出现本次 CIW 与 Bridge loaded 的 PID 标记。
- 新 daemon PID `49362`，OS TCP 表仅监听 `127.0.0.1:65346`。
- 本地复用已登记的 `ssh.exe` PID `113544`，唯一监听 `127.0.0.1:65347`；没有新建重复隧道。

通过既存远端工作区凭据完成签名 handshake，owner PID 与本次新进程一致；
Bridge owner 检查确认预期用户和实际用户均为 `xum`，实际 bind 回读为回环地址。
凭据只在内存中用于认证，未显示、写入报告、复制到 artifacts 或轮换远端凭据。

上述核对通过后，仅更新该 endpoint 的本地私有认证缓存，独立读回并再次认证。
只读 SKILL 返回：

```text
(48417 3 "127.0.0.1:65346")
```

正式 VDA doctor（`nics4304_tsmc28`）结果：

```json
{
  "connected": true,
  "bridge_version": "1.0.0",
  "skill_probe": "3",
  "daemon_bind": "127.0.0.1:65346",
  "profile": "nics4304_tsmc28"
}
```

先确认原 cellview 存在，再复用 Bridge 的 `open_window` builder，显式 `mode="r"`，
只重开原先显示的 `vda_uled_rx_20261018/tlff_core_v1/schematic`。
独立窗口回读为该确切 library/cell/view、模式 `r`、modified `nil`。
X11 只读检查确认新 PID 的 CIW `0x2800008` 无阻塞弹窗。
未恢复旧波形或另建 ADE；这不是 ADE state 独立 Load 验收。

## 最终完整性与边界

四份 `sch.oa` 的 SHA-256 均与备份前一致：

| Library | Cell | 完整性 |
| --- | --- | --- |
| `vda_uled_rx_20261018` | `tlff_tb_v1` | 未变化 |
| `vda_uled_rx_20261018` | `tlff_core_v1` | 未变化 |
| `Analog_VLSI` | `vda_t18_hw2_ft_001` | 未变化 |
| `Analog_VLSI` | `vda_t18_nmos_tb_20260920_003` | 未变化 |

原 `cds.lib` 字节比对一致，现有 Bridge setup SHA-256 与启动前一致。
未对 `/tools`、`/data/technique`、`/data/standard`、`/data/cshrc` 或现有 PDK/DRF 发出修改命令；
这不是对服务器全部文件的全量完整性审计。仍在运行的另一 Virtuoso PID `87971` 未操作。

本次持久备份、脚本、receipt 和日志位于：

```text
/data/xum/virtuoso_bridge_smoke/session_restart_20261023_77de4178
H:\Virtuoso Design Agent\artifacts\stb-live-20261023\session-restart
```

底层 Bridge 的多行执行曾产生 `/tmp/vb_eval_*.il` 临时文件，X11 helper 使用已有用户工作区，
不能宣称所有临时写入都仅发生在上述单个备份目录；它们不属于系统或 PDK 配置编辑。

本轮未修改 VDA/Bridge 生产源码、Vault、网络、TUN、防火墙或 SSH 配置。
OA 写入、si netlisting、Spectre 提交均为 0；没有运行 STB、DC 或性能仿真。
证据为 `bridge_readback` 与 OS 连接/文件完整性观察；用户的保留/丢弃决定属于 `user_input`。
重启和算术成功不证明原 STB 目标电路、任何设计规格或 L5B 闭环已经通过。
