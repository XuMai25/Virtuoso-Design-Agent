# TSMC18 共源级固定规格设计尝试（2026-09-18）

## 仿真前固定的目标和范围

这不是新增随机器件网格或拓扑全域审计。用户要求 agent 自行选择指标尝试设计，复用已有
TSMC18 profile、建图、参数应用、OA→si→Spectre 和真实指标约束；不修改 Bridge、PDK 或 Vault。

- 目标：`Analog_VLSI/vda_tsmc18_cs_spec_001/schematic`；执行前确认不存在；`replace_existing=false`。
- 拓扑：NMOS 共源，source/bulk 接 VSS，理想电阻 RD 接 VDD/OUT；外部理想输入偏置与电容负载。
- 工况：`nics4304_tsmc18`，`tt + stat_noise`，27°C，VDD=1.8 V；CL=50 fF。
- 预先固定规格：低频 gain≥20 dB，首个半功率带宽≥100 MHz，静态供电功耗≤100 µW，
  DC OUT=0.8–1.0 V，饱和余量≥0.4 V，RD/MOS/供电 KCL 误差≤1%。
- 这是 agent 提议的练习规格（`software_inference`），不是用户真实应用需求；不得事后放宽
  阈值以伪造成功。任务中显式字段是执行契约，不代表这些数值由用户逐项指定。
- 首轮只跑一个理论首值，最多允许三次有物理依据的局部修正；不声明全局/连续最优。
- 1–3 个候选直接 OA，不增加 preview。只有 DC 工作点合格才运行 AC。
- 本地记录在 `artifacts/runs/tsmc18-cs-spec-attempt`；远端产物只在
  `/data/xum/virtuoso_bridge_smoke/tsmc18/vda_*`；OA 只写指定新 cell。

## 理论首值（不是性能证据）

先前真实 `L=0.36 µm,W=2 µm,VGS=0.6 V,VDS≈0.9 V` 的 Id≈13.615 µA、
gm/Id≈12.17 V⁻¹、gm/gds≈118.5，说明加长沟道有助于增益；同一电阻负载条件的
真实 gain≈19.946 dB，CL=10 fF 时 BW≈215.793 MHz。

目标 CL=50 fF，直接保持 W=2 µm/RD=66.1 kΩ 会明显降低带宽。先放大宽度到 8 µm，
使中点偏置所需 RD 约按 1/W 缩小，提升输出电导；再将 VGS 降低到0.58 V，争取更高 gm/Id。
用局部 `d ln Id/d VGS=gm/Id` 首估得到 Id≈42.7 µA、RD≈21.1 kΩ、静态功耗≈76.8 µW，
采用好读的 RD=21.2 kΩ。首值 W/L=8/0.36 µm，nf=m=1。

`Av≈gm/(1/RD+gds)`、`BW≈(1/RD+gds)/(2π(CL+Cpar))` 只用于方向与首值计算。
历史插值留出门只通过2/4点，本轮不把该插值器称为已校准，也不靠它判规格。
新偏置、宽度与真实寄生必须用本次 OA 同源 DC/AC 验证。

## 执行记录

### 理论首值的真实 DC/AC

| 指标 | 预先固定的门限 | RD=21.2 kΩ 实测 |
| --- | --- | --- |
| 低频增益 | ≥20 dB | 20.1091 dB |
| 首个半功率带宽 | ≥100 MHz | 137.9478 MHz |
| 静态核心供电功耗 | ≤100 µW | 68.8819 µW |
| DC OUT | 0.8–1.0 V | 0.988724 V |
| 饱和余量 | ≥0.4 V | 0.887475 V |
| 电流 KCL 最大误差 | ≤1% | 0.0006623% |

真实 Id=38.2675 µA，比局部首估低约10%；gm=519.507 µS、gds=3.76113 µS。
初始 DC 的七项约束通过，随后 AC 的九项约束全部通过；1 kHz–10 GHz 的复数
VOUT/VIN 有281个有效频点，低频参考平坦，−3 dB 与 unity-gain 均在 sweep 内包围。
GBW=1.396914 GHz，unity-gain frequency=1.392202 GHz；两者分别提取，不混用定义。

初始 DC 与成功 AC 的 OA/si 均为 W/L=8/0.36 µm、nf=m=1、RD=21.2 kΩ，
si 网表 SHA-256 均为 `0e0e2e35ab5ada91128a888a2a756b2fc263fb68ecfb28296fd5bc1eac733767`。
网表包含 PDK 自动生成的扩散/LDE 参数；wrapper 仅配置模型、外部激励/负载和 analysis。
指标从真实 DC/OP 与 AC PSF 提取，不以 OA 存在或退出码判规格。

### 执行失败及聚焦修复

- `01-create.json`：创建动作和内部结构/尺寸回读成功，但随后独立 inspect 超时，整个任务保留 failed；
  没有覆盖、删除或重复创建。`02-dc.json` 的新 worker 独立回读成功并完成同源 DC。
- `03-ac.json`：`si` log 已有 End netlisting，远端已生成 netlist 且 si 进程已退出，
  但通过 CIW `csh()` 等待结果超时；没有把已有网表当作成功仿真。
- VDA `_generate_oa_netlist` 改用 Bridge 已有的 OA-host SSH runner 执行外部 `si`；
  `simInitEnvWithArgs` 仍由目标 Virtuoso/CIW 完成。使用远端 TERM/10 s kill-after 限时，
  Bridge 等待预算增加15 s。非零退出/超时，即使 log 有完成标记也失败；日志、非空网表、
  模型/端子/实际 OA 参数一致性门不变。不修改或复制 Bridge 的 SSH 实现。
- `04-ac-retry.json`：同一任务、同一参数、同一固定规格成功；AC action 约30 s。
  初始 DC action 约86 s；分析和缓存状态不同，不能据此宣称普遍加速比例。
- `05-rd-refine.json`：单字段 RD 局部修改动作在 `read_schematic` 阶段超时；
  `07-state-recovery.json` 的独立只读 probe 也超时。保持失败记录，先恢复连接并重新回读，
  不盲目重放写入。没有证据把这些间歇超时的根因归结为 PDK、OA 或某个具体进程。

### 有依据的单参数修正

首值已经可行，但增益只比下限高0.109 dB，OUT 接近上限。由本次真实 OP 在当前点线性化
KCL：`Vnew=(VDD/Rnew−Id+gds*Vout)/(1/Rnew+gds)`，选择 RD=23.5 kΩ，
预计 OUT≈0.908 V、gain≈21 dB，同时核对较高 RD 是否使真实带宽低于100 MHz。
仅修改 RD，不改 W/L、bias、CL、拓扑或任何规格；这不是无依据的随机第二点。

`08-state-recovery.json` 在正式 stop/start 重建本地隐藏 tunnel 后，独立回读确认
RD仍为21.2 kΩ、W/L未变；因此才执行 `09-rd-refine-retry.json`，写入并独立回读
RD=23.5 kΩ。随后 `10-final-ac.json` 在新 worker 中重新读同一 OA、重新生成 si 网表，
DC/OP/AC 一起复核，九项原定约束全部通过。

| 指标 | RD=23.5 kΩ 最终值 |
| --- | --- |
| 低频增益 | 20.82671 dB / 10.99855 V/V |
| 首个半功率带宽 | 125.83356 MHz |
| GBW（低频增益×带宽） | 1.383987 GHz |
| 独立 unity-gain frequency | 1.379870 GHz |
| 静态核心供电功耗 | 68.32239 µW |
| DC OUT / Id | 0.908013 V / 37.95687 µA |
| 饱和余量 | 0.806765 V |
| KCL 最大误差 | 0.00004169% |

真实增益增加0.718 dB，带宽下降约8.8%但仍高于固定下限；保持W/L=8/0.36 µm、
VGS=0.58 V、CL=50 fF、nf=m=1。仅一个首值和一次已验证的RD修改，不是有限域或连续
全局最优。输出中点的局部理论预测有效，不代表稀疏插值器获得新校准或任意拓扑误差保证。

最终远端目录：
`/data/xum/virtuoso_bridge_smoke/tsmc18/vda_tsmc18-common-source-spec-final-ac_5865b725b351`。

- 最终 si 网表 SHA-256：`c07d9cac5636d5a51e06505c7eb331d4572b32975259d7a66bf8ec77868f5581`。
  OA 与网表的W/L/RD/nf/m及MN0端子一致；RD网表从21200变为23500。
- `si_execution`：实际returncode=0，`bridge_oa_host_ssh_runner`，远端180 s / kill-after10 s、
  Bridge等待195 s。完整AC action约27 s；没有通过CIW运行外部si。
- 最终AC有281个有限频点，低频变化约1.6e−10 dB；半功率交点在118.850–125.893 MHz
  之间被包围，且只有一个向下交点。DC/OP一致性和饱和/KCL护栏全部通过。
- 原始DC SHA-256：`18f9ed0feb5a15746de078688ad7bdeedb08cc25f2b3db2fc68a3afd62307c82`；
  OP：`38ccff71d49aabf202b023ad6324e3fbe6677916ae4ab6a43cd39f5b83847041`；
  AC（117827 bytes）：`d71abe748cfd58901e9d48415f93a1002f8e7c624c8560f1e125e2c28fbbcdac`。

## 证据、资源与未验证边界

- task的显式规格/工况在现有执行器中按契约字段标为 `user_input`；它指输入契约，不是声称
  用户亲自选择了练习指标。上述指标提议、首值计算、局部KCL预测仍为 `software_inference`。
  OA尺寸/结构与SSH退出码是 `bridge_readback`；si网表及PSF OP/复数AC是 `eda_result`；
  饱和分类、交点插值、约束判定和PVT聚合逻辑是软件计算，不创造新的EDA事实。
- 本轮common-source路径保存了网表、wrapper和实际解析DC/OP/AC的哈希，以及指标/
  交点诊断，但没有反相器/通用OA路径那种完整 input/result/log archive manifest。
  临时下载的PSF随worker清理；远端Spectre临时结果也已删除。因此不能声称已归档全部原始
  波形或可离线完整重放；本次有效仿真由严格PSF解析及run record证明，重新运行依赖原PDK。
- 运行期只启动自有headless Virtuoso（PID13803，SID13721），未bootstrap或关闭用户原有
  可见Virtuoso（PID126060）。结束时先核对xum/SID/唯一restore路径，再TERM其所有进程组；
  Cadence Xvfb自动收尾helper也已退出，MessageServer、IPC、daemon无本次遗留。
  远端si/Spectre=0，daemon65346无listener；本地65347无listener、相关worker/tunnel ssh=0。
- `cds.lib`前后SHA-256相同：
  `377ad9f53db1c9ad07683cc8cab027899569b38f55eb8f1286fe45f1867bb6ed`。
  四个netlist/scratch目录加session目录约744 KiB，保留供复核；没有无界日志或循环常驻服务。
  只保留本次新OA DUT及参数；不修改Bridge、PDK、Vault和其他用户设计。
- 本地完整pytest：**929 passed**；新增覆盖TSMC18首值/局部修改/原门限不变，
  si使用OA-host SSH及限时、缺失transport、空网表，以及即使日志有完成标记仍拒绝失败退出/超时。
  `git diff --check`通过。N28网表和既有operation均由完整回归覆盖，但本轮没有重复N28远端运行。
- 真实范围仅TT/27°C/1.8 V、50 fF、理想低阻输入偏置和analogLib电阻负载；功耗不包含实际
  偏置发生器或驱动器。未测试PVT、noise、transient大信号/线性度、mismatch、物理电阻及版图。
  本次未新建ADE setup，不能声称该DUT已经可以直接在人工ADE中点击Run。
- 当前状态：**TSMC18 nominal OA-sourced common-source fixed DC/AC specification verified**。
  fresh-create整体原子成功和间歇连接超时仍是可靠性边界；不宣称任意电路或完整L5B。
  下一步只按实际用途在同一已合格点加噪声/大信号等必要质量项；PVT可选，不默认追加扫参或
  新拓扑全域审计。完整原始波形归档是后续可审计交接的明确缺口。
