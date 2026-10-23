# 2026-10-23 Virtuoso 重启：保存前检查中止，未退出会话

> 本页保留早期中止阶段的现场证据；其“未退出/未恢复”不是当前状态。
> 用户后续允许丢弃旧波形并确认原会话已退出，最终定向恢复与只读验收见
> [后续恢复记录](2026-10-23-virtuoso-restart-readonly-acceptance.md)。

## 授权与结论

用户允许“保存当前工作后重启 Virtuoso”。本轮限定为当前 Bridge 所属的会话；
不重启其他 Virtuoso、不运行 si/Spectre 或恢复 STB。

状态：**preservation incomplete / Virtuoso not restarted**。
旧波形窗口无法确认保存，故未调用退出、终止或重新启动操作。

## 认证和身份

使用已有 SSH 转发和现存远端工作区凭据，仅在内存中用于认证；没有生成、轮换或显示凭据，
没有更新本地认证缓存。签名 handshake 和 Bridge 原有 owner/bind 检查成功：

- 入口：`nics4304-cad1`，Unix 用户 `xum`；
- 当前 Virtuoso PID：`94710`；
- 远端 daemon 实际 bind：`127.0.0.1:65346`；认证 `on`、lane `idle`；
- 当前工作目录：`/data/xum/Analog_VLSI/icfb`。

这次使用既存远端凭据可以通过本机隧道认证，支持“先前本地认证缓存不一致”的判断；
没有据此断言凭据为何产生差异，也不宣称原来的缓存问题已经修复。

## 原理图与会话盘点

当前 PID 中有 28 个已打开 cellview。按 `dbIsCellViewModified` 逐项查询，
可编辑的四张原理图均为 `nil`（无未保存修改），没有调用 `dbSave` 重写 OA：

| Library | Cell | View | 模式 | 未保存修改 |
| --- | --- | --- | --- | --- |
| `vda_uled_rx_20261018` | `tlff_tb_v1` | `schematic` | `a` | 无 |
| `vda_uled_rx_20261018` | `tlff_core_v1` | `schematic` | `a` | 无 |
| `Analog_VLSI` | `vda_t18_hw2_ft_001` | `schematic` | `a` | 无 |
| `Analog_VLSI` | `vda_t18_nmos_tb_20260920_003` | `schematic` | `a` | 无 |

可见窗口共 15 个：1 个 CIW、1 个 schematic 窗口、13 个历史 Waveform 窗口。
可见 waveform 关联的 SEV 对象返回的 ASI environment 均为 nil；
这不等于已经穷尽核实所有可能隐藏的 ADE/绘图状态。

另见其他 Virtuoso PID `36200`、`87971`、`126060`，全部未操作。
只读进程快照未列出正在运行的 Spectre/ocean，但快照不构成对所有任务状态的永久保证。

## 波形保存失败

当前 `awvGetWindowList()` 返回 nil，却仍有上述历史 Waveform HI 窗口。
先读取现场 IC618 官方 `vivaxlskill` 文档确认：

- `awvSaveWindow(windowId fileName)` 保存为 `.grf`；成功返回 t，失败返回 nil。
- `awvLoadWindow` 用于独立加载 `.grf`。

新建非覆盖保护目录：

```text
/data/xum/virtuoso_bridge_smoke/session_restart_20261023_77de4178
```

对现场仍存在、标题明确为 `Waveform Window` 的 `window:166`，
执行原生保存至该目录下的 `waveform_166`，返回：

```lisp
("Waveform Window" nil)
```

随后文件盘点为空。Bridge 请求状态为 SUCCESS 只表示该表达式正常返回；
保存 API 的 nil 及不存在输出文件表明**波形保存未完成**。
没有对其余 12 个窗口批量重复保存，没有关闭窗口或用截图冒充可恢复的波形状态。
保护目录当前为空，不是已完成的备份。

## 实际变更与继续条件

- 仅创建上述新的空保护目录和本地记录；未改已有原理图、ADE state、结果或全局配置。
- 未修改 VDA/Bridge 生产源码、Obsidian Vault、代理/TUN/防火墙或 SSH 配置。
- 未执行 Virtuoso 退出/kill/重启，未操作其他会话；STB 继续暂停。
- 原有本地认证缓存不一致仍未修复。

继续前需确认旧波形是否必须保留：若必须保留，应先定位其真实 ViVA/Print 状态及
可恢复的保存入口；若用户明确允许不保留这些旧波形窗口，则在重新核对当前原理图及
隐藏会话状态后，才继续已授权的定向重启。两条路线均不自动运行电路仿真。
