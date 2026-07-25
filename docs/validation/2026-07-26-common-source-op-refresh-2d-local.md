# 2026-07-26 共源级新 anchor W/RD 二维局部刷新

## 结论

本 Gate 只读上一轮真实 Bridge run record，没有连接 Bridge、没有运行 Spectre、没有写 OA。
它完成了两件事：

1. 新增通用 heldout 参数方向覆盖门，拒绝没有被任何留出点扰动的可调参数；
2. 以真实 EDA 最佳点为新 anchor，在固定 `RS=750 Ω` 后生成更小的 W/RD 六点任务。

三维 W/RD/RS 刷新虽然对现有 heldout 数值给出低误差，但 candidates 5/6 的 RS 都等于
anchor，不能验证 RS 灵敏度。实现明确返回：

```text
Error: held-out points do not perturb declared parameter directions: source_resistance_ohm
```

因此没有把三维结果包装成已校准。合法二维刷新状态是：

**held-out-covered common-source W/RD local refresh prepared; new-point EDA pending**

## 来源与分割

- source task：`common-source-quality-op-relinearized-next`
- source run：
  `artifacts/runs/common-source-quality-op-relinearized-next/live-resume1-20260726.json`
- source SHA-256：
  `f6d5c98acb7b14f7d2c8407c5e8b3a6c281f3e3945e0e7a19eea0494d05bb635`
- anchor：candidate 2，`W=1.1 µm/RD=19 kΩ/RS=750 Ω`
- training：`[2,3,4]`
- heldout：`[5,6]`
- 建模参数：`device_width_um`、`load_resistance_ohm`
- 固定参数：`RS=750 Ω`、`L=30 nm`、`BIAS=0.35 V`、`VDD=0.9 V`、`CL=1 fF`

training 中 anchor、独立 RD 扰动和独立 W 扰动张成两维。heldout candidate 5 改变 RD，
candidate 6 同时改变 W/RD，因此结果保存：

```json
{
  "device_width_um": true,
  "load_resistance_ohm": true
}
```

参数覆盖只证明每个方向至少进入独立留出检查，不把两个 heldout 点夸大为连续全域或每个
系数的独立统计置信区间。

## 历史留出误差

所有来源数值都是上一轮 run record 中的 `eda_result`；回归、误差和筛选是
`software_inference`。

| metric | 最大 heldout 误差 |
| --- | ---: |
| drain current | 0.158% |
| gm | 0.176% |
| gds | 0.201% |
| output swing | 0.319% |
| gain | 0.235% |
| bandwidth | 0.435% |
| P1dB | **0.568%** |
| input-referred integrated noise | 0.050% |
| DC power | 0.158% |
| GBW | 0.108% |

最坏项 `0.568%` 低于保持不变的 OP `15%`、performance `20%` 门。训练只有 anchor 加两
个独立方向，恰好确定一阶模型，所以 training error 为 0%；真正的历史泛化检查来自未
参与拟合的 candidates 5/6。

## 生成候选

可信域缩到半步长：

- `W ∈ {1.05, 1.10} µm`
- `RD ∈ {18.5, 19.0, 19.5} kΩ`
- `RS = 750 Ω` 固定

| ID | W (µm) | RD (kΩ) | predicted GBW (GHz) | 说明 |
| --- | ---: | ---: | ---: | --- |
| op-local-001 | 1.10 | 19.0 | 34.3965 | 已测 anchor 控制点 |
| op-local-002 | 1.10 | 18.5 | 34.6472 | 预测第一名 |
| op-local-003 | 1.05 | 18.5 | 34.5939 | 新点 |
| op-local-004 | 1.05 | 19.0 | 34.3432 | 新点 |
| op-local-005 | 1.10 | 19.5 | 34.1458 | 新点 |
| op-local-006 | 1.05 | 19.5 | 34.0925 | 新点 |

预测第一名只比 anchor 高约 `0.729%`；在真实 EDA 前不能宣称改进，也不能用预测第一名
直接覆盖 OA。完整 quality task 仍保留 saturation、mismatch、THD、noise、power 和 P1dB
等 EDA constraints。

## 命令与绑定

```powershell
.\.venv\Scripts\vda.exe op-relinearize `
  examples\theory\common-source-op-refresh-2d-policy.json `
  artifacts\runs\common-source-quality-op-relinearized-next\live-resume1-20260726.json `
  --output artifacts\relinearization\common-source-op-refresh-2d.json
.\.venv\Scripts\vda.exe candidate-task-from-relinearization `
  artifacts\relinearization\common-source-op-refresh-2d.json `
  examples\theory\common-source-op-refresh-2d-task-template.json `
  --output artifacts\relinearization\common-source-op-refresh-2d-task.json
.\.venv\Scripts\vda.exe plan `
  artifacts\relinearization\common-source-op-refresh-2d-task.json
```

- policy file SHA-256：
  `64f5dfae4e69499f81830f7bed49f02f4b617c9db9c111893c026b5b8a96e8eb`
- result SHA-256：
  `081567a8f5cb4bcf0964d94060dbb1ffe6989d296af664fe394024fdb271869d`
- template SHA-256：
  `872371f1c63d718a4347c32a0e43a38c29e812be441823e3f0c894dc524a9e26`
- compiled task SHA-256：
  `ddbe5146799353647431ea9f01746468daffc6a35e439af74b8bc06edb2f2c05`
- plan token：`ac7b19632251a835`

## 本地回归

- Python：`606 passed`
- `tests/test_op_relinearization.py`：`10 passed`
- 既有共源三维 policy：参数覆盖 `W/RD/RS=true`，仍通过原误差 Gate
- 既有差分对三维 policy：参数覆盖 `Wn/Wp/Wtail=true`，仍通过原误差 Gate
- `examples/tasks/*.json`：`155/155` plan passed
- 新二维 task：`vda plan` 通过，token 如上
- `compileall`、`vda catalog`、`git diff --check`：通过

## 下一次真实执行边界

本地 task 中存在 allow flags 只表示合同可被显式授权执行，不构成本记录或此前授权的延伸。
若执行，必须先重新独立回读 OA，并确认以下范围：

- target：`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`
- OA 写入：是，只暂存六个 W/RD tuple 并最终写回真实最佳点；失败则恢复 preflight 基线
- 远端计算：是，逐候选自动 `si` + Spectre AC/transient/noise
- scratch：
  `/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-op-refresh-2d-next_<nonce>/`
- 覆盖：`replace_existing=false`，不重建或替换 cellview
- PVT：本轮不默认执行

预检若不是 `W=1.1 µm/RD=19 kΩ/RS=750 Ω`，必须停止并重新生成合同，不能把旧 anchor
强写回当前 OA。完成后还要运行 exact `op-relinearization-validate`，分别报告候选执行、推荐
一致性和新点数值精度。

## 未闭合边界

- 六个刷新候选尚未运行 Spectre；当前 GBW 都只是 `software_inference`。
- RS 调整能力没有删除，但现有数据不足以同时训练和 heldout 验证 RS。继续三维搜索前至少
  需要一个相对新 anchor 的独立 RS 探针进入 heldout。
- 本轮没有增加 PVT、mismatch/Monte Carlo 或新拓扑。
- Bridge 和 Obsidian Vault 均未修改。
