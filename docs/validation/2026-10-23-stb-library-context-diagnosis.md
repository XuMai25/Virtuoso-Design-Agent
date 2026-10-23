# 2026-10-23 STB 目标库访问：只读原因核对与最小接入方案

> 后续状态：该隔离方案已在后续授权下执行，原目标可复用，没有新建或写 OA；
> 见[一次 DC＋STB 的真实原生对照](2026-10-23-n28-stb-native-acceptance.md)。
> 以下保留本次只读诊断和当时尚未执行的提案。

## 授权和结果

用户本轮只允许核对目标文件、当前库映射和原 smoke 配置；不改系统、PDK、现有
`cds.lib` 或 OA，暂不仿真。未启动或重启任何会话，没有 netlisting、Spectre 或清锁。

原因已定位为**当前 Virtuoso 的库上下文不匹配**，不是凭目标文件不存在作出的猜测。
文件系统可读不等于 native OA 已成功打开；本轮仍不宣称目标拓扑、参数或 STB 已验收。

结构化诊断：`artifacts/stb-live-20261023/03-library-mapping-readonly-diagnosis.json`。
前次失败见[STB 前检记录](2026-10-23-stb-readonly-preflight.md)。

## 实际证据

通过 Bridge 现存本地私有认证缓存完成签名 handshake，目标仍是 PID `48417`。
使用 Bridge 的 `library_list_skill`/`library_get_skill` 只读 builder 查询；没有调用
create、technology bind、库刷新、强制切换库表或任何写入 API。

| 核对项 | 实际结果 |
| --- | --- |
| 目标文件 | `/data/xum/virtuoso_bridge_smoke/vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic/sch.oa` 存在且 OS 可读，36644 bytes，owner 为 `xum` |
| 当前会话工作目录 | `/data/xum/Analog_VLSI/icfb` |
| 当前 DD 库解析 | 库清单不含 `vb_pdk_smoke`，查询返回 `libraryNotFound` |
| 当前 `tsmcN28` 实际路径 | `/data/technique/tsmcn28rf_lo_0v9_1p10m_7X2R/tsmcN28` |
| 原 smoke 库表 | `/data/xum/virtuoso_bridge_smoke/cds.lib` 存在且可读，含目标测试库定义 |
| 原 smoke 与 VDA profile 的 `tsmcN28` | `/data/technique/tsmcn28_lo_0v9_1p10m_5X2Y2Z/tsmcN28` |

原 smoke 库表还含 `SOFTINCLUDE /home/xum/cds.lib`；本轮仅记录该指令，未沿其读取 home，
更没有写 home。原 profile 的两个 LVT MOS symbol 和两个模型入口路径均存在且可读；
没有读取模型正文、求值 model section、证明版本未变或建立新的性能证据。

因此不能只给当前会话追加 `vb_pdk_smoke` 就继续：同名 `tsmcN28` 已加载为另一目录。
路径不同不自动证明两套器件电气不兼容，但一致性尚未证明，不能混用并冒充原 profile。
也不应在含已打开接收机原理图的当前会话中全局切换 PDK 映射。

目标目录有 `sch.oa.cdslck` 及 `sch.oa.cdslck.RHEL30.cad52.113976`。
文件名中的 PID 在当前 cad 的 `/proc` 不存在；这只是有限观察，不代表已排除所有其他
上下文中的 writer，也不构成清锁授权。未删除或修改锁；后续若 native 只读打开被锁阻挡，
仍应停止，不自动强制解锁或改为编辑模式。

## 前后完整性

本轮两次只读文件检查得到一致 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| 目标 `sch.oa` | `f92b6a94e4661c355fda6da45a04cefb525256a6a5ddefff333ce5ca997a8129` |
| 当前工作目录 `cds.lib` | `3402adc0189c6f012b8bd2c7567bc857683bcd063c1bf97699cd08c26bafbe43` |
| 原 smoke `cds.lib` | `674391325f18e0c46a6fcc9b9c9759a6a8ebce2a8300fd94f0adcb792e8f0a18` |

当前工作目录配置还与本地重启前备份的哈希一致。目标哈希只证明本轮检查窗口内文件
未变化，不证明与 7 月历史 OA 拓扑或参数完全一致。

## 最小接入方案（提案，未执行）

1. 不改当前会话、原 smoke 库表、系统/PDK 或目标 OA。为这一个任务使用短时隔离
   Virtuoso 进程，读取原 smoke 库配置，固定 `tsmcN28` 为已声明的 5X2Y2Z profile。
   新日志、启动资料及必要的私有入口文件只写到新的
   `/data/xum/virtuoso_bridge_smoke/stb_acceptance_<id>/`。
   若使用私有 `cds.lib`，只引用原 smoke 配置，不覆盖它。
2. 继续使用 Bridge 已有的启动/传输/认证机制与独立回环 endpoint；不抢占当前 Bridge
   端口、不关闭当前窗口、不修改网络设置。临时会话启动前确认身份、端口及目录，
   明确登记本任务拥有的进程；不能批量结束同名进程。
3. 先只读核对隔离会话的真实 PDK 路径、目标 masters/terminal→net/pins/参数，
   对照历史基线并记录目标文件哈希。锁、结构漂移、PDK 解析变化或其他新问题均先停。
   同名 profile 不替代实际路径核对，也不直接复用历史性能结论。
4. 本轮仍不启动计算。只有后续明确批准隔离接入及原单作业 Gate 后，才做一次
   OA→si 和一次 DC＋STB Spectre 作业，保留原始 PSF/网表/日志及同作业原生 margin，
   核对前后 OA；不调参或追加仿真。原始 PSF 持久留存和原生 margin 接入仍是计算前条件。
5. 作业或只读接入结束后只关闭本任务的临时会话和独立转发，保留证据；当前用户
   Virtuoso、原 Bridge 及其他任务不受影响。实际清理须按届时身份复核，不能照抄 PID。

隔离是避免对一个已加载其他同名 PDK 的会话做全局变更，不是重新搭框架或新建电路。
这会增加一个临时 EDA 进程及 scratch 写入，需要用户确认后才执行。本轮未创建该目录、
私有配置、endpoint 或进程。

## 证据分类与未验证边界

DD 库清单、路径及查询状态为 `bridge_readback`；OS 文件/权限/哈希和 `/proc` 观察为
`system_event`；原因判断与接入提案为 `software_inference`。用户范围为 `user_input`。
本轮没有新的 `eda_result`，未做 Bridge 源码、VDA 生产代码、Vault 或远端配置修改。
按 karpathy-guidelines 的最小改动原则，仅复用现有只读 builder 和 SSH runner，
新增本地诊断记录，不引入自动注册库、清锁或重试逻辑。
