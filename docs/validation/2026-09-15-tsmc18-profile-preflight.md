# TSMC18 profile 与 nominal live Gate（2026-09-15）

## 结论

状态：**TSMC18 nominal OA→si→Spectre execution path verified; design-quality closure pending**。

VDA 现在可以显式选择 `nics4304_tsmc18`，在既有 TSMC18 work area 中创建/读取受保护的 `vda_` schematic，以 PDK 正确的 CDF 字段写入尺寸，并从同一 OA schematic 自动 `si` 后运行 Spectre。最终 Gate 有 OA 回读、si 网表、非空 transient、解析指标和输入/PSF/log 哈希清单。它没有修改 Bridge、服务器 PDK、人工 `cds.lib`、`tb_f_T` 或其他 cellview。

本轮没有规格约束、搜索或 winner 判定，因此不能把一次成功 transient 称为 TSMC18 设计质量闭环，也不能继承任何 N28 性能证据。

## 环境与 PDK 事实

- Bridge：`0.8.0`；Virtuoso：`6.1.8-64b`；Spectre：`21.1.0.612.isr15`。
- PDK 根：`/data/xum/Analog_VLSI/PDK_TSMC18_1809`。
- work area：`/data/xum/Analog_VLSI/icfb`；OA work library：`Analog_VLSI`；tech library：`tsmc18`。
- 标准 1.8 V OA master：`nmos2v_mac`、`pmos2v_mac`；terminal order 为 `D G S B`。
- Spectre model：NMOS `nch_mac`、PMOS `pch_mac`，与 OA master 名不同。
- CDF 尺寸面包含 `w`、`wf`、`l`、`fingers`、`nf`、`simM`、`m` 和 `multi`。TSMC18 写总宽度 `w` 后，PDK callback 会同步 `wf`；不能套用 N28 的 Bridge `wf -> Wfg` 映射。
- 现有人工 ADE L state 使用主 model 文件的 nominal `tt` section，并另外加载 `stat_noise`。只加载 `cor_std_mos.scs section=tt` 会在层次展开时报未定义 `par1fn_mc`。

这些环境、CDF 和 OA 查询属于 `bridge_readback`；只有真实 si/Spectre 输入和结果属于 `eda_result`。

## 实现

- `PdkProfile` 分开声明 OA `nmos_cell/pmos_cell` 与 simulator `nmos_model/pmos_model`。
- `mos_width_write_argument` 选择 PDK 的稳定写入面：N28 保持 `wf`，TSMC18 使用 `w`。inverter、common-source/cascode 和 differential-pair 的语义尺寸写入均通过同一 profile 规则。
- 一个 process corner 可映射为有序的多个 `SpectreModelInclude`。standalone preview、MOS characterization、反相器 wrapper 与通用 OA testbench 共用同一解析器；没有 default-corner bundle 的旧 profile 继续回退到 `model_include + model_section`。
- TSMC18 的 `tt/ff/ss/sf/fs` bundle 均显式包含对应主 section 和 `stat_noise`；本轮只 live 验证 `tt`。
- 反相器仿真记录新增完整 Spectre artifact manifest 和 manifest SHA-256，并在 Bridge 未返回版本时从本次 `spectre.out` 提取版本。该行为复用已有 characterization/通用 OA 仿真机制，没有复制 Bridge。

## 执行与恢复记录

目标始终限定为 `Analog_VLSI/vda_tsmc18_inv_001/schematic`，`replace_existing=false`，运行根限定为 `/data/xum/virtuoso_bridge_smoke/tsmc18`。

1. `01-create-20260915.json`：旧 headless CIW 的 cwd 不在 TSMC18 work area，无法解析目标 library/master；在创建对象前失败。
2. `02-create-retry-20260915.json`：切换到正确 work area 后，实例、nets 和 pins 已保存；随后参数阶段因 Bridge 把 `wf` 映射为该 PDK 不存在的 `Wfg` 而失败。
3. `03-apply-recovery-20260915.json`：profile 改用 `w` 后，以独立 `parameters.apply` 完成写入、callback 和重新打开回读。该恢复属于 `bridge_readback`，不是性能证据。
4. `04-simulate-20260915.json`：OA 与 si 一致，但仅加载 wrapper `tt`，Spectre 因 `par1fn_mc` 未定义而失败；无波形，保留为失败证据。
5. `05-simulate-retry-20260915.json`：`tt + stat_noise` 后首次得到稳定 transient 和指标。
6. `06-simulate-manifest-20260915.json`：同参数复跑，指标逐值一致，并补齐最终输入/PSF/log manifest；这是本 Gate 的规范结果记录。

由于第 2 步已合法保存结构，安全策略禁止删除或覆盖现有 cellview。本轮因此没有在修复后重复一次 fresh atomic `schematic.create`；创建与参数写入的分段恢复已验证，但“新 profile 一次调用全原子完成 create”仍保留为未 live 验证边界。

## 最终同源证据

- OA `bridge_readback`：MN0=`tsmc18/nmos2v_mac`，MP0=`tsmc18/pmos2v_mac`；四个 nets/pins 为 IN/OUT/VDD/VSS；D/G/S/B 连接符合反相器。
- OA 参数回读：`Wn=2 µm`、`Wp=4 µm`、`L=0.18 µm`，两管 `fingers=nf=m=multi=1`。
- si `eda_result`：`nch_mac/pch_mac`，nodes 与 OA 一致，W/L 与 OA 逐项一致；网表 SHA-256 为 `70332352e4d17e34ea78284f4afa2835611c8ea12ec58c620a27eae383816bd1`。
- testbench：model section 顺序为 `tt, stat_noise`，VDD=1.8 V，CL=10 fF；wrapper SHA-256 绑定在 run record 中。
- Spectre `eda_result`：786 个 transient 样本，信号 `time/IN/OUT/VDD_SRC:p`；manifest 共 9 项，包括 OA si 网表、wrapper、`spectre.out`、`tran.tran.tran`、状态/日志和进程 guard。manifest SHA-256 为 `d7d334ba240dbcf291f355511d69fa9d08cad09e45bdf14af8205ff88004e008`。
- 解析指标：delay `20.717071 ps`，rise/fall `33.024628/25.813592 ps`，skew `7.211036 ps`，VOH `1.793498 V`，VOL `17.138 µV`，每周期供电能量 `67.927619 fJ`，平均供电功耗 `339.638093 µW`。这些是本次波形的 `eda_result`；面积代理 `1.08 µm²` 是 `software_inference`。
- overshoot/undershoot 分别为 `0.209863/0.188347 V`。本 Gate 没有为它们设规格，故仅报告，不判定质量合格。

## 安全与资源复核

- `cds.lib` 前后 SHA-256 都是 `377ad9f53db1c9ad07683cc8cab027899569b38f55eb8f1286fe45f1867bb6ed`。
- Bridge daemon 只绑定 `127.0.0.1:65346`，没有公网监听。
- 结束时 `si=0`、`spectre=0`；VDA tunnel 已停止，本地 65347 不再监听。
- 本轮 headless Virtuoso、Xvfb/wrapper 和一次更早遗留的只读 `csh/sh/sed` 链均经精确 PID 核对后用 SIGTERM 清理；用户原有可见 Virtuoso PID `126060` 保留。
- 三个失败/成功运行目录共 222,549 bytes，留在批准的 TSMC18 scratch 中供复核；没有写入 `/home/xum` 或工作区外路径。

## 本地验证

- 聚焦 profile/preview/characterization/Bridge 边界回归：251 项全部通过。
- VDA 全量回归（独立仓库内 basetemp）：917 项全部通过。
- `git diff --check` 通过。

## 未验证边界与下一 Gate

- TSMC18 fresh atomic create 的修复后 live 复跑尚未做；需要另一个明确的新 `vda_` cell 才能保持非覆盖策略。
- `ff/ss/sf/fs` bundle 仅通过 profile/渲染回归，没有 live PVT 证据。
- 尚未做 TSMC18 DC transfer、AC、noise、输入 slew/load 扫描、约束、有限搜索或 winner 写回。
- `Wp/Wn=2.0` 只是易读的初始化值，不是已校准最优比例。
- Bridge 对当前可见 CIW 标题的自动分类仍把它当作 dialog candidate；本轮使用相同 work area 的受控 headless CIW。直接复用可见 CIW 的 bootstrap 仍需 Bridge 侧独立修复/升级，不能写成已打通。

下一步不默认扩点。若用户给出 TSMC18 目标拓扑和规格，先复用现有 onboarding/inspect，做最小 DC operating-point Gate，再按需要做 AC/bandwidth/GBW；只有模型、工作区或 shortlist 失效时才增加更昂贵的搜索或 PVT。若优先验证创建能力，则只需授权另一个全新 cell 做一次非覆盖 atomic create/readback，不需要重复本次 transient。
