# 2026-07-20 共源真实功耗、线性度与 noise 实现

状态：**local implementation and one-point read-only live smoke verified; quality-driven closure pending**。

本轮在既有 common-source OA→`si`→Spectre worker 上增量加入真实 VDD 功耗、相干 transient 线性度和普通 noise 分析。没有新建第二套 topology、netlister、executor 或远端脚本，也没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。本记录只证明任务契约、指标算法、Bridge 边界和离线编排通过，不把 demo 或 mock PSF 当成电路性能证据。

## 正式任务契约

- 共源 DC/AC/transient/noise wrapper 均保存 `VDD_SRC:p`。`dc_supply_power_uw` 使用实际 VDD source current，且与 MN0 `ids` 做独立 `supply_current_mismatch_percent` KCL；不把器件 Id 直接包装成电源功耗。
- `analysis: "transient"` 必须提供 `linearity_sweep`：频率、严格递增幅度、settling/measurement 整数周期、每周期点数、最高谐波和压缩阈值全部进入 plan token。
- 所有幅度放在一个 Spectre nested parameter sweep 内，同一 OA/`si` 网表只生成一次；`load_ff` 保持 testbench-only，不写 OA。
- `analysis: "noise"` 必须提供 `noise_sweep.start_hz/stop_hz`。wrapper 使用 `noise (OUT 0) ... iprobe=VIN_SRC`，积分范围就是显式扫频范围。

## 指标与失败语义

线性度在丢弃 settling cycles 后的整数周期窗口上，用梯形积分做相干傅里叶投影：

- 每点：输入/输出 fundamental peak、large-signal gain、HD2/HD3、指定最高谐波内 THD、输出 DC/min/max/peak-to-peak、实际 VDD 平均功耗和每周期能量。
- 幅度 sweep：首个幅度作为显式 small-signal reference；报告最大幅度增益压缩、最大 THD、最大功耗和 P1dB。
- P1dB 只有在相邻两个声明幅度包围目标压缩量时才线性插值；未跨越只保留 unresolved warning，不拿末点冒充。
- 空 waveform、长度不一致、非有限值、非递增时间、measurement window 不完整、输入 fundamental 与声明幅度不符或 VDD current 极性异常都会拒绝该候选证据。

普通 noise 从 Bridge 已下载的唯一 `noise.noise` 文件调用 Bridge 自身 `parse_spectre_psf_ascii`，要求 `freq/out/in` 三条 trace。VDA 对输出和输入参考电压噪声密度平方做频率梯形积分，报告 V/µV RMS 与频带端点密度；频率端点必须与任务声明一致。解析后的 PSFASCII 再由 Bridge 上传到本次保留的 netlist scratch，远端路径和 SHA-256 进入 run evidence。Bridge 的 runner/SSH/download 和 parser 保持第三方所有权，VDA 没有复制传输或通用 PSF parser。

## 证据分类

- OA/netlist 结构与参数一致性：`bridge_readback` + `eda_result`；
- 真实 source current、transient/noise trace 及其连续积分量：`eda_result`；
- task 中 analysis、幅度、频率、周期、负载和 sweep 范围：`user_input`；
- 默认 sweep 字段、傅里叶/P1dB/积分算法、饱和与 analysis 完整性：`software_inference`；
- demo 的全部数值仍为 `software_inference`。

## 本地验证

```text
133 passed
39/39 example task plans passed
git diff --check passed
```

覆盖包括：模型互斥与预算、DC source-current 极性和 KCL、相干 fundamental/HD2/THD/功耗、P1dB 已包围与未包围、noise density 积分、Spectre deck 不复制 DUT、sweep point 映射、普通 noise PSF 路径/hash、payload user/default source、planner 副作用和 demo executor。新增任务：

- `examples/tasks/common-source-linearity-verify.bridge.json`
- `examples/tasks/common-source-noise-verify.bridge.json`

## Live 结果与剩余 Gate

同日已在 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` 完成上述集中只读 smoke。5 点 100 MHz nested transient 得到完整幅度/失真/功耗数据并解析出被真实 bracket 的 P1dB；1 kHz–10 GHz ordinary noise 得到 211 点 `freq/out/in` PSF 和输出/输入参考积分噪声。真实目录形状还暴露了 sweep `dcOpInfo` 覆盖根 DC 与 stop 端点缺样本两个问题；VDA 分别用根 analysis-specific PSF 选择和一个额外 strobe 修复，没有改 Bridge 或放宽指标门。完整数值、哈希、失败记录和 transport 恢复见 [共源功耗、线性度与 noise 只读真实验证](2026-07-20-common-source-quality-live.md)。

当前已验证的是一个固定设计点的执行与提取能力，不是质量驱动的设计闭环。下一道 Gate 是组合 DC+AC+linearity+noise 的受预算多 analysis 评估/调优，覆盖质量规格的可行、不可行、预算耗尽和恢复路径，再加入有限 corner 与 L/VDD。
