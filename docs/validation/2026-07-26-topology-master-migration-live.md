# 2026-07-26 topology master/CDF 迁移真实同源 Gate

## 结论

在用户明确授权的新 cellview `vb_pdk_smoke/vda_master_migration_001/schematic` 上，VDA 已完成：

```text
LVT OA schematic
  -> independent OA/CDF readback
  -> si netlist + Spectre DC/AC
  -> instance-scoped nch_lvt_mac -> nch_mac
  -> explicit Wfg/l callback + readback
  -> independent SVT OA/CDF readback
  -> SVT si netlist + Spectre DC/AC
  -> exact inverse
  -> independent restored OA/CDF readback
  -> restored LVT si netlist + Spectre DC/AC
```

状态升级为 **controlled instance-scoped symbol-master/CDF migration, OA-to-si-to-Spectre source consistency, and exact inverse restoration live verified at nominal TSMC N28**。

这不是任意 PDK/master 兼容保证，也没有把正常 inverse 包装成保存后故障自动恢复。自动 recovery 的故障注入目前仍只有本地证据。

## 授权与范围

- PDK/library：`nics4304_tsmc28` 与显式 `nics4304_tsmc28_svt`；`tsmcN28`。
- 目标：`vb_pdk_smoke/vda_master_migration_001/schematic`。
- 新建、forward、inverse：`allow_remote_write=true`。
- 四次实际仿真：`allow_remote_compute=true`；其中第一次 0.45 V 点保留为 partial 反例，其余三次构成合格 round-trip。
- `replace_existing=false`；创建前只读 inspect 确认目标不存在。若同名对象并发出现，creator 会拒绝而不是覆盖。
- 远端 scratch 全部位于 `/data/xum/virtuoso_bridge_smoke/`，没有写 `/home/xum`。
- Gate 结束后保留新 cellview，但状态恢复为最初 LVT 共源级；没有删除 OA 对象。

用户确认覆盖上述完整 bundle，本记录不把授权扩展到其他 cellview 或故障注入。

## Before 状态与真实 contract

创建后独立 `existing_schematic` inspect 得到：

- 实例：`MN0=tsmcN28/nch_lvt_mac`、`RD0=analogLib/res`；
- nets/pins：`IN/OUT/VDD/VSS`；
- `MN0.Wfg=1u`、`l=30n`、`fingers=1`、`m=1`；
- `RD0.r=20K`；
- MN0 完整 CDF readback 为 233 项。

`vda topology-compile` 从这份真实 readback 和显式 operation 文件生成 contract，没有手写结构 SHA：

- before topology SHA-256：`4471c93914bf888a3c17df01ca3b97625353e8e0e5b3e2f5110e4d5684d4dea9`；
- after topology SHA-256：`17ddebdb6de97e72c1d9181934d9dece743e7dc6d66acfb5229920492478d5d1`；
- forward：只把 `MN0` 从 `nch_lvt_mac/symbol` 换为 `nch_mac/symbol`；
- inverse：同一实例恢复为 `nch_lvt_mac/symbol`；
- CDF migration：写前 CAS `Wfg=1u/l=30n`，换 master 后 callback 写入并独立回读同一值；未声明字段策略为 `record_only`。

forward/inverse task 内嵌 contract 与编译产物逐字段完全相同，计划 token 分别为 `274bcb6e0e1ff4b4` 和 `abd389c3c3289d92`。

## 偏置反例与修正

第一次 LVT AC 使用 `VBIAS=0.45 V`。`si`、DC 和 271 点 AC 都真实产生，但 `VDS=54.43 mV < VDSAT=152.07 mV`，因此 run record 为 `partial`，问题是 “AC design metrics require a saturated DC operating point”。其解析出的 42.78 GHz 带宽不能作为合格小信号设计证据。

随后不改 OA，只把 testbench bias 降为项目既有共源 Gate 使用的 `0.35 V`。LVT、SVT 和恢复后 LVT 三次都进入饱和区并完整通过。原 partial 文件保留，没有覆盖或删除。

## Forward OA/CDF 证据

写前 preflight 在真实 Virtuoso 中验证：目标 symbol 存在；B/D/G/S 端子、方向、唯一 pin/figure 与 bBox 兼容；目标 CDF 含 Wfg/l。editor 再按实例名和旧 master 做 CAS，只设置 `MN0~>master`。

保存后 worker 与独立 inspect 均得到：

- topology SHA 精确等于 `17ddebdb...8d5d1`；
- `MN0=tsmcN28/nch_mac`，`model=nch_mac`；
- `Wfg=1u/l=30n/fingers=1/m=1`；
- 前后 CDF 都是同一组 233 个字段；除 `description` 和 `model` 从 LVT 变为 standard-Vt 外，其余 231 项逐字符串相等；
- 声明的 Wfg/l 使用一次 Bridge batch callback，并通过独立 targeted readback；
- `automatic_inverse_recovery.status=not_needed`。

这里的完整 CDF 表属于 `bridge_readback`。`record_only` 只表示记录未声明字段的真实变化，不把这次 231 项相等外推成任意 master 都会保持。

## LVT/SVT 同源仿真

三次合格仿真使用相同 `VDD=0.9 V`、`VBIAS=0.35 V`、`RD=20 kOhm`、`CL=2 fF` 和 `1 kHz-1 THz, 30 points/decade`。每次有 271 个 AC 频点，DC node/device 与 KCL 一致性均为 matched。

| 指标 | LVT before | SVT forward | LVT restored |
|---|---:|---:|---:|
| si model | `nch_lvt_mac` | `nch_mac` | `nch_lvt_mac` |
| Id | 31.5619 uA | 16.1238 uA | 31.5619 uA |
| VDS / VDSAT | 0.2688 / 0.1053 V | 0.5775 / 0.08457 V | 0.2688 / 0.1053 V |
| low-frequency gain | 4.5885 V/V | 4.0184 V/V | 4.5885 V/V |
| -3 dB bandwidth | 6.7621 GHz | 4.5914 GHz | 6.7621 GHz |
| GBW | 31.0278 GHz | 18.4502 GHz | 31.0278 GHz |
| DC supply power | 28.4060 uW | 14.5114 uW | 28.4060 uW |

`si` 网表证明 forward 后实际使用 `model=nch_mac`，不是只改了 OA 显示名称。三份网表的 SHA-256：

- LVT before：`b26b861eb3dafe3c8a6f5a6e39d22ba21816d0a4684825739a3d8890a5d2d558`；
- SVT：`41d9ac21b571f409337a915fbd04cbce5747eec9b5ae4d14ee30fd07950228c7`；
- LVT restored：`b26b861eb3dafe3c8a6f5a6e39d22ba21816d0a4684825739a3d8890a5d2d558`。

SVT 的独立 raw AC/DC/opinfo SHA-256 分别为 `42913625...730e`、`3184788b...a01b`、`155adb9a...39c4`。LVT before/restored 的原始 PSF 哈希不同，因为它们是两个独立 Spectre run；但 `si` 网表 hash、解析后的核心 DC/AC 标量和完整 OA 参数状态完全相同。本 Gate 不把 raw PSF 逐字节相等列为恢复条件。

## Exact inverse 与最终状态

inverse 写前输入 SHA 为 `17ddebdb...8d5d1`，保存后实际 SHA 精确回到 `4471c939...d4dea9`。随后独立 inspect 确认：

- `MN0=tsmcN28/nch_lvt_mac`，`model=nch_lvt_mac`；
- Wfg/l/fingers/m 恢复；
- MN0 与 RD0 的完整参数 JSON 和创建后 before readback 逐项相等；
- nets、pins 和实例结构恢复；
- 恢复后 `si` 网表 hash 与 before 完全相同；
- 恢复后 LVT 的核心 DC/AC 标量与 before 完全相同。

这证明的是显式 exact inverse round-trip。没有人为制造 post-save 审计失败，所以自动 recovery 仍待独立 disposable-cell fault-injection Gate。

## 资源生命周期

最终 `vda resources --remote` 为只读 dry-run：

- local transient VDA resource：0；cancel marker：0；
- remote Spectre：0；`si`：0；Maestro session：0；
- remote Virtuoso：2，是既有共享 Cadence/Bridge 进程；
- 远端 evidence directory 从 502 增至 506，正好对应本 Gate 的四次仿真；没有执行删除。

Bridge 仓库没有修改。本 Gate 复用其公开 SSH、SKILL/editor、CDF callback、`si`、Spectre、传输和关闭逻辑。

## 证据来源

- target、授权、master mapping、Wfg/l、bias 和 analysis 条件：`user_input`；
- OA existence、topology、master、完整 CDF、forward/inverse 回读与资源盘点：`bridge_readback`；
- `si` netlist、DC/opinfo、271 点 AC、指标和 raw-file hash：`eda_result`；
- topology normalization/SHA、contract/inverse 生成、指标提取和表格比较：`software_inference`；
- 0.45 V 规格不完整状态：电路数据为 `eda_result`，partial 判定为 `software_inference`，不是 transport failure。

## 本地 run record

| 文件 | SHA-256 |
|---|---|
| `02-before-inspect-20260726.json` | `f93bbc0303270137f4db2789ce54176b0c4e4ec5c834569e2e8714f780d44dcb` |
| `03-lvt-ac-baseline-20260726.json` | `6bb7a4fcddbf97f60dfe94ba0d17b50f0ed0631775255d41bf043d35f4758a54` |
| `04-lvt-ac-saturated-20260726.json` | `239ec935896c7d6f09fcec202d979c95d0b0f8bb9fde3ce6322e34c248b8c437` |
| `05-contract-20260726.json` | `9d5483dbb39e7eb64ede9b05c7b83cf995edec0e890f2bfb5ac789236216b97b` |
| `06-forward-20260726.json` | `9f2628add084a9e8a0846614f21b2a44e16e80d181555e8c28620a8fa762c35e` |
| `07-svt-independent-inspect-20260726.json` | `d5097cb06c12add16a0c3ac90aed53d774ee456dd5086626c61ac9d31f5069ae` |
| `08-svt-ac-20260726.json` | `53d964e6ac66fc9f87e1d2c835be715e5a6dbeb34c41323dc9fea6698fa9ede7` |
| `09-inverse-20260726.json` | `010d38beffd3b3c827f77ca3fe0552d8dfd71cad73b96688609f622e8ff42a9a` |
| `10-restored-independent-inspect-20260726.json` | `f1e300e19c88b1ac88c5470fb8ff55d9805659c39b2e9d4846333fdc84f427ff` |
| `11-restored-lvt-ac-20260726.json` | `20c9825d76172ade93df3fc88870cd129eb5b12aafc29fbf550687c47ce58e54` |
| `12-resource-final-20260726.json` | `caf165f5cdf9690a52ab3e222e37665e1200264b466bfff6c1c63a9a54fce1f7` |

以上文件位于 `artifacts/runs/common-source-master-migration/`。它们是本地 retained evidence，不随 Python package 提交；验证记录用 SHA-256 固化其身份。

## Post-Gate 本地回归

- `python -m pytest`：`648 passed`；
- `python -m compileall -q src tests`：通过；
- 全部 `184/184` 个 `examples/tasks/*.json` 均可生成计划；
- `vda catalog`、反相器 demo plan、master forward/inverse plan 和 `git diff --check`：通过。

## 未验证边界与下一 Gate

- 自动 post-save inverse recovery 尚未 live 故障注入；
- PMOS `pch_lvt_mac -> pch_mac` 尚未完成 direction + OA/`si`/Spectre round-trip；
- 不同 terminal/pin 几何、参数名映射不同或 CDF 字段集合不同的 master 继续拒绝或必须单独声明；
- pin add/remove、wire/label/shape 通用 snapshot、并发人工 editor、PVT/mismatch 不由本 Gate 证明；
- 本次 LVT/SVT 差异只对应 nominal 0.9 V、27 C、固定 W/L/RD/CL/bias，不是器件 flavor 的一般优劣结论。

下一道安全 Gate 应在另一个 disposable `vda_` cellview 上做可控 post-save 失败注入，证明自动 recovery 的真实状态机；正常 master-migration 主线已经可以供后续受控器件 flavor/threshold 微调复用。
