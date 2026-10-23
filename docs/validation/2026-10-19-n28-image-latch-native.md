# N28 图片预放大器/锁存器：隔离原生图、实体连接与 OA→si 验收

## 结论与证据范围

**目标已非覆盖创建；经一次显式部分状态补完，OA 结构、实体导线所属网络和实际 si
网表与中间描述一致。原生图已取得，人工可读性确认待用户完成；没有性能仿真。**

这不是无故障单次 create 成功，更不是动态比较器性能完成。第一次真实检查暴露的错误、
补完方式、四个原生 warning 和两处标签跨接均保留。

## 授权与隔离

- 目标：`vb_pdk_smoke/vda_img_latch_n28_20261019_001/schematic`，`replace_existing=false`。
- PDK：`nics4304_tsmc28`，实际路径
  `/data/technique/tsmcn28_lo_0v9_1p10m_5X2Y2Z/tsmcN28`，LVT MOS。
- 图中省略 M3a/M3b/CTAIL 及虚线连接，保留实线 M3、完整实线 preamplifier/latch 和两只 Cp。
- NMOS W/L=600n/30n、PMOS=720n/30n、fingers=1、simM=1；Cp=1f。
  MOS bulk 分别接 VSS/VDD。以上为用户接受的占位值，不是从图片提取的尺寸。
- 输入 VINP/VINN/CLK/CLKB、电源 VDD/VSS 和输出 OUTP/OUTN 都是 pins；不添激励或反相器。
- 前次检查发现人工会话绑定 RF/7X2R，详见[预检停止记录](2026-10-19-n28-image-latch-preflight.md)。
  用户另行批准隔离会话，保持已批准 PDK、目标和参数不变。
- 新根：`/data/xum/virtuoso_bridge_smoke/vda_img_latch_n28_20261019_001_session`。
  仅回环 `127.0.0.1:65406`，本地 tunnel `127.0.0.1:65407`；旧 65346 端点不重载、不改动。
  使用 Bridge warm/SSH/传输/执行能力，第三方源码未修改；未修改当前人工 cds.lib 或 PDK。
- 新会话内再次确认真实库路径、技术库和目标不存在后才写入。原 smoke `cds.lib` SHA-256
  在前后都为 `674391325f18e0c46a6fcc9b9c9759a6a8ebce2a8300fd94f0adcb792e8f0a18`。

## 可审阅意图与原生图

本机证据根：`artifacts/schematic-image-n28-20261019/`，属于忽略目录，避免把 PDK 完整 CDF
证据、运行环境配置或庞大现场文件加入公开仓库。可直接打开：

- `description.json`：图片解释、电气拓扑、显式布局/路线/疑点处理。
- `symbols.json` / `symbols-raw.json`：3 个真实 master、命名端子和 symbol selection boxes。
- `compiled.json` / `preview.svg`：软件编译及非原生预览。
- `native-large.png` / `native-image-evidence.json`：准确绑定目标窗口的 Virtuoso 原生截图。
- `independent-comparison.json`：完整实例/参数/网络/pins、placement、实际导线 net/layer 回读。
- `netlist/oa_netlist.scs` / `netlist-result.json`：实际 si 网表、CDF 端子顺序、参数比较。
- `create-run.json`、`partial-readback.json`、`finish-routing-plan.json`、`routed-readback.json`：
  首次失败与补完前后记录，未用最后成功结果覆盖早期失败。

原图 SHA-256：`a53c7ae3ea490e28a90bab82fcb846f08cb349abf42c704c70d2eadb0fb3d570`。
中间描述命名 `DIN=Di−`、`DIP=Di+`、`OUTP=OUT+`、`OUTN=OUT−`、`CLKB=CLK̅`。
PMOS/NMOS 使用真实 named terminals 和 R0/MY 变换，没有猜端子顺序或插入逻辑端子兜底。

## 实际结果

| 验收项 | 结果 | 证据类别 |
|---|---|---|
| 实例/master | 13 MOS + 2 analogLib/cap，共 15；master/方向/位置一致 | bridge_readback + software_inference |
| 参数/CDF | 批准字段独立回读一致；画线前 56 个实际放置端子位置通过 | bridge_readback |
| 网络/pins | 13 nets、8 pins、全部 terminal→net 与意图一致 | bridge_readback + software_inference |
| 实体导线 | 81 个 OA wire/drawing line 对象；与计划 83 段几何覆盖、网络归属一致 | bridge_readback + software_inference |
| 同源网表 | OA→si `-command nl`，15 实例模型/连接一致，54 项尺寸/倍数/Cp 通过 | eda_result + software_inference |
| OA 不变 | netlist 前后完整结构/参数回读指纹相同 | bridge_readback + software_inference |
| 原生图面 | 已真实截图；agent 能追踪主要支路，人工确认待完成 | bridge_readback / 人工审阅待定 |
| 性能 | 未运行 Spectre，也未准备性能激励，不做规格结论 | 未验证 |

Netlist SHA-256：`252bfd1de1bed12341ed7b8afe90d6ef85c548d18a0155ed8068ab46acc2e4cf`。
OA 结构回读 SHA-256：`daf494b478fa186a10c4e49be99a075c5eb4654231cc963f2244f0d9a8cd5c45`。
真实 CDF Spectre termOrder：MOS `(D G S B)`，cap `(PLUS MINUS)`。
本次单指条件下 `Wfg→w`、`l→l`、`simM→multi`、`fingers→nf` 逐实例比较通过；
不能推广为多指条件下一切 Wfg/w 派生规则均已验证。Cp 两项 c 同样核对。

## 图面限制与原生 warning

- VS 公共尾节点、DIN/DIP 长支路、LP/RP 堆叠、OUTP 反馈、CLKB 支路用实体导线。
- 不同网络交叉仍按首版保守契约处理：OUTN 在原图两处不连接的交叉附近使用明确的短
  标签跨接，共 3 个实体分量；CLK 两个区域也明确标签关联。电源/地和 bulk 使用标签。
  **不声称所有反馈均连续实体导线，也不把标签降级藏在“生成成功”里。**
- PDK 默认参数文字在局部镜像支路较拥挤；截图放大后可读，仍不是人工通过的图面质量。
- `schCheck` 保留 4 个 `Solder dot on cross over` warning，位于
  `DIN(-2,1.75)`、`DIP(2,1.75)`、`OUTP(-2,5.5)`、`OUTN(2,5.25)`。
  四处都是意图中声明的同网四向 junction，端子回读和网表证明应相连；不是异网短接。
  没有关闭检查或删除 warning 来制造“零告警”。

## 首次失败、修复与边界

1. 普通 create 计划 token `d24780105c34382d`。15 器件/CDF 已保存后，端点 guard 报
   `eval: not a function - 0.9375`；未添加 pins/线。问题是 SKILL 把 `(0.9375)` 当成函数调用。
2. 只读确认 15 实例、0 pins、0 wire，保存全参数/放置证据。修正算术表达式，先只读核对
   全部 56 个端点。没有删除目标或重放 create。
3. 单独补完计划 token `758421d6d6b9f730` 绑定部分状态 SHA
   `246ca99fea0125fdb41f10182b2d5bb13f7eed9802da8013935d9453f5bb8a76`；再次回读相同后，
   只新增批准的 pins/线/标签，不重复实例/CDF。目标未替换。
4. 写后回读器把原生 netless ellipse junction 圆点当成未知导线而拒绝。先保留并回读
   实际形状，再仅修正读取分类；不再写 OA。ellipse 和 label 不计导电路径，未知类型仍拒绝。
5. OA→si token `bf56d73f7dcda813`，目录 `<隔离根>/netlist_only_001`。si 实际成功、初步解析
   通过后，附加 CDF 查询因 plist 链使用 `~>` 失败。改为 `->`，只复用已生成网表重做
   只读比较；**未重跑 si，更未跑 Spectre**。这是任务脚本修正，不修改 Bridge。
6. 本地测试 `1026 passed, 1 skipped`，8.27 s。跳过项是默认 VDA 环境未安装 Bridge 的
   专门接入测试文件；在 Bridge Python 中另跑 `8 passed`。首次新测试用默认临时目录
   受权限阻止，使用新的任务专属 basetemp 后通过；全量报告仍有 pytest cache 写权限警告。
   没有删除用户临时目录或调整系统权限。

这次完成的是一个具体目标的验收与针对真实失败的本地修复。修复后未额外新建第二个 cell，
所以 fresh-create 单次不间断全链、通用自动恢复、任意 PDK/图片仍未验证。

## 清理与交接

目标最终 `modified=nil`。结束前核对 cwd、PID/SID、保存状态，退出本任务 Virtuoso 并仅对
同一 UID/SID 的残余自有 helpers 做 TERM；不删除 OA/证据。独立复核 SID `122052` 无存活
进程、远端 65406 无 listener、本地 65407 无 listener。原 Virtuoso PIDs `94710`、`126060`
和原回环 daemon 保留。现场两个旧 Spectre PIDs `11005`、`25503` 属于旧 SID `94710`、已
运行约 23 小时，本任务未启动、未终止它们；不把“本任务干净退出”说成“整台服务器没有计算”。

人工打开应在匹配的 **5X2Y2Z** N28 环境访问此 cell；不要把当前 RF/7X2R 的同名库当作
同一 PDK。下一步先看 `native-large.png` 并逐块确认图面。如需整理，只对本目标另列仅
图面变更计划、保留拓扑/参数及网表等价，不自动重排人工设计。性能须另给规格/激励并授权。
