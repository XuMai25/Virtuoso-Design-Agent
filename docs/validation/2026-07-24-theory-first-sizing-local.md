# 2026-07-24 theory-first 尺寸分析本地 Gate

## 结论

VDA 已加入第一版理论先导尺寸分析器，但当前只能称为：

**Gate 6 theory sizing contract locally implemented; TSMC N28 characterization and Spectre calibration pending**

后续状态：同日先完成[Gate 6 topology-local 一阶模型真实校准](2026-07-24-theory-calibration-live.md)，又完成[TSMC N28 独立 MOS characterization 真实 Gate](2026-07-24-tsmc28-mos-characterization-live.md)。独立表已有 240 个 nominal `top_tt` 训练点和 4 个真实留出点，但本尺寸器尚未把 Gate 6 三种器件角色与实际 DC 偏置自动映射到该表，也没有通过 held-out circuit 误差门；本文件中的 synthetic sizing 边界和禁止 OA 写回结论保持不变。

本 Gate 没有调用 Bridge、没有远端计算、没有写 OA，也没有修改第三方仓库。它解决的是“不能从几个人工随意列出的候选里选一个就称为最优”的契约与算法问题；它尚未提供真实 PDK 设计结论。

## 防止伪最优的实现

- `vda theory` 的请求没有 `candidates` 字段；多余的手工候选列表会被 strict schema 拒绝。
- 输入是三个器件角色各自的有限 gm/Id characterization 域：输入 NMOS、PMOS 电流镜负载和尾 NMOS。每点包含 L、`gm/Id`、`gds/Id`、`Id/W`、输出电容密度和 `VDSAT`。
- PDK characterization 或 EDA operating-point 来源必须同时声明 artifact id 和 SHA-256；synthetic 数据必须显式标为 `synthetic_example`。
- characterization 的输入管 VDS、PMOS VSD 和尾管 VDS 必须与当前分析工作点在声明容差内一致；电流镜二极管节点 `OUTP` 与单端输出 `OUTN` 分开输入，并分别检查上下管余量。
- 分析器完整遍历三个表域的笛卡尔积，组合数超过 4096 时直接拒绝，不按隐藏预算截断。
- W 不是输入候选。每个表点组合先由 BW/GBW 方程反解最小支路电流，再由 `W=Id/(Id/W)` 计算输入管、负载管和尾管宽度。
- 达不到寄生渐近上限、超过宽度/功耗/面积上限或不满足增益/余量的组合都保留明确拒绝原因。
- 输出包含约束裕量、主导电流下界、BW/GBW 渐近上限和固定表点下的局部对数敏感性。
- 输出绑定 canonical request SHA-256，并回显器件 artifact id/hash 与 characterization 条件。

结果术语被固定为：

- 完整遍历且有可行点：`best_in_declared_discrete_characterization_domain`；
- 完整遍历且无可行点：`no_feasible_design_in_declared_discrete_characterization_domain`；
- `continuous_optimum_claim=false`；
- `global_optimum_claim=false`。

现有 `design.tune`/`design.close_loop` run record 同时新增 `search_audit`。它结构化保存声明、尝试和完整候选数；预算截断或候选失败时只能是 `best_evaluated`，完整离散网格也只称为 `best_in_declared_discrete_domain`。

## 方程边界

首版只针对已经真实进入 Gate 6 的固定电流镜负载差分对：

```text
gm   = Id_branch * (gm/Id)_input
gout = Id_branch * ((gds/Id)_input + (gds/Id)_load)
Cout = Cload + Id_branch * (Cout_density_input/J_input
                           + Cout_density_load/J_load)
Ad0  = gm/gout
BW   = gout/(2*pi*Cout)
GBW  = gm/(2*pi*Cout)
Pdc  = 2*VDD*Id_branch
```

频率约束可解析反解为：

```text
Id_required(f) = 2*pi*f*Cload /
                 (N - 2*pi*f*Cpar_per_current)
```

分母非正意味着该表点组合已经达到模型的寄生渐近上限，不会靠无限增大 W/电流伪造可行解。三只器件的输出/源节点电压还用于检查输入管、PMOS 负载和尾管的 KVL 余量。

## synthetic 可重复样例

命令：

```powershell
.\.venv\Scripts\vda.exe theory examples\theory\differential-pair-gmid.synthetic.json
```

结果完整评估 `2 × 2 × 1 = 4` 个表点组合。按“增益 ≥ 100 V/V、BW ≥ 50 MHz、GBW ≥ 5 GHz、最小余量 ≥ 30 mV、功耗 ≤ 150 µW，并最小化功耗”的 synthetic 任务：

- 推荐标识为 `in=n-in-efficient|load=p-load-short|tail=n-tail-long`；
- W 由方程得到约 `2.985/1.492/3.980 µm`，不是请求中手列；
- 估算增益 `150 V/V`、BW `52.5 MHz`、GBW `7.875 GHz`、功耗 `107.45 µW`；
- 一个组合因功耗超限拒绝，一个组合因增益不足拒绝；
- 结果明确标为 `user_input` characterization + `software_inference` 推导，并警告 synthetic 数据不是 TSMC N28 证据。

这些数值只验证程序路径和解析关系，不具有电路设计证据等级。

## 本地验证

```text
python -m pytest
467 passed

vda catalog
exit 0

vda plan examples\tasks\inverter-close-loop.demo.json
exit 0，plan token 正常生成

vda theory examples\theory\differential-pair-gmid.synthetic.json
exit 0，4/4 声明组合完整评估
```

测试覆盖：

- 方程反解电流/W 和限制项；
- 声明表域完整计数；
- 功耗上限导致的完整不可行推导；
- 全无可行点时不提升“最接近”候选；
- 超过频率渐近上限的拒绝；
- PDK/EDA 来源缺少 artifact hash 的拒绝；
- characterization 电压与分析工作点不一致的拒绝；
- 任意 `candidates` 输入的拒绝；
- CLI JSON 输出与本地保存一致；
- 既有完整网格、预算截断和全不可行 tuning 的 `search_audit` 分类。

## 尚未验证的边界与下一 Gate

1. 当前没有从 TSMC N28 Spectre model 生成的 gm/Id、`gds/Id`、`Id/W`、电容密度和 `VDSAT` 表；synthetic 示例不能用于 OA 写回。
2. 当前是一阶输出极点模型，没有内部极点/零点、slew、settling、noise、distortion、mismatch、stability 或 PVT 预测。
3. 当前只穷尽声明的离散 characterization 表域，没有表内插值、连续局部优化或全局最优证明。
4. PMOS 电流镜在首版中按理想小信号镜像处理；真实镜像误差、内部二极管连接节点极点和输出摆幅必须由 Spectre 复核。
5. artifact id/hash 目前是请求绑定，本地分析器不会自行下载并重新计算远端文件 hash。

下一 Gate 应先做只读 PDK characterization：用 profile 的真实 model include/section 在固定且记录的 VDS/VSD、L、VGS/电流范围生成表和原始 manifest，产物放在 `/data/xum`。随后选择表域中心、边缘和理论推荐点，比较理论与同源 OA→`si`→Spectre 的 OP、增益、BW、GBW、功耗和余量误差。只有误差范围被量化后，理论结果才可用于缩小真实搜索域；Spectre 仍负责最终规格判定。

该下一 Gate 涉及远端计算，本文记录不构成新的远端授权。
