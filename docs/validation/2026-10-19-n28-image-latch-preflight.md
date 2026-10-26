# N28 图片锁存器复现：只读预检停止记录

后续：用户另行批准隔离会话，已按原 5X2Y2Z N28 目标完成结构/网表验收；
见[隔离会话真实验收](2026-10-19-n28-image-latch-native.md)。以下保留首次停止时的事实，
不是把当前人工 RF/7X2R 会话改成了目标环境。

## 已批准范围

用户批准 `vb_pdk_smoke/vda_img_latch_n28_20261019_001/schematic` 非覆盖新建，
省略图片虚线器件 M3a/M3b/Ctail，保留实线预放大器、锁存器及两只 Cp。
占位参数为 NMOS W/L=0.60/0.03 µm、PMOS W/L=0.72/0.03 µm、单指单倍、
两只 analogLib/cap 各 1 fF；NMOS bulk 接 VSS、PMOS bulk 接 VDD。
仅验收原生图面、实际连接、OA→si 网表，不运行 Spectre；新歧义必须停止。
这些参数属于已批准的验收占位值，不是原图数值或性能设计。

图片 SHA-256：`a53c7ae3ea490e28a90bab82fcb846f08cb349abf42c704c70d2eadb0fb3d570`。

## 实际检查与停止原因

复用 Bridge 的认证连接，仅建立本次本地 tunnel，未部署/重载 daemon。
`_require_loopback_bridge_daemon` 核实 bind 为 `127.0.0.1:65346`。
只读 SKILL 查询成功，保存于本地忽略目录
`artifacts/schematic-image-n28-20261019/preflight.json`（`bridge_readback`）。

| 项目 | 批准计划/本地 profile | 当前会话实际读回 |
|---|---|---|
| 工作目录 | 既有 N28 smoke 环境 | `/data/xum/Analog_VLSI/icfb` |
| 目标 library | `vb_pdk_smoke` | `ddGetObj` 返回 nil，会话不可见 |
| `tsmcN28` 路径 | `/data/technique/tsmcn28_lo_0v9_1p10m_5X2Y2Z/tsmcN28` | `/data/technique/tsmcn28rf_lo_0v9_1p10m_7X2R/tsmcN28` |
| 已有设计窗口 | 仅盘点，保留 | `vda_uled_rx_20261018` 下多个 schematic 以 a 模式打开 |

当前 library 不可见，因此目标 cell 的 nil **不能证明磁盘上不存在**，非覆盖前置检查尚未完成。
相同逻辑库名不代表同一 PDK 版本。不能用 RF/7X2R symbol 与既有 5X2Y2Z profile/models 混合执行。
这是环境绑定不一致，不是连接失败，也没有证据表明已有设计损坏。

已停止：未读取本图实际 master 几何、未生成写入 task/token、未创建或修改 OA、
未导出网表、未运行仿真、未打开/关闭任何 Virtuoso/ADE/原理图窗口。
本地第一次脚本调用仅因 VDA module 搜索路径缺失而退出；修正任务脚本 import 路径后，
第二次只读查询成功。没有修改 Bridge 或网络配置。
随后 `vda bridge stop` 成功清理本次建立的本地 tunnel；status 复核为 NOT running。
这没有停止远端 daemon 或 Virtuoso，也没有关闭人工窗口。

## 恢复需要的新授权

建议保持已批准的 5X2Y2Z N28 profile 和目标不变，使用与当前人工工作隔离的 N28 smoke 会话。
先核对是否已有可复用的匹配会话；若没有，需获准新建专用 Virtuoso 会话及独立回环 Bridge
端点，不抢占当前端点、不关闭当前 Virtuoso、不修改当前 `cds.lib` 或 PDK。
新会话/脚本/日志全部限制在 `/data/xum/virtuoso_bridge_smoke` 的本任务独立目录。
恢复后重新核对实际库路径、目标不存在、真实 master/CDF 与符号几何，再生成执行计划。
