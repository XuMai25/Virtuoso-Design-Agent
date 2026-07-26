"""Compile a task contract into a stable, reviewable execution plan."""

from __future__ import annotations

import hashlib
import json

from .catalog import (
    task_requests_oa_parameter_write,
    task_semantic_parameter_names,
    validate_task_capability,
)
from .models import (
    AnalysisKind,
    CircuitKind,
    ExecutionPlan,
    Operation,
    PlanStep,
    SchematicTransformAction,
    SideEffect,
    TaskSpec,
)


def _step(
    step_id: str, capability: str, description: str, side_effect: SideEffect
) -> PlanStep:
    return PlanStep(
        id=step_id,
        capability=capability,
        description=description,
        side_effect=side_effect,
    )


def _steps_for(task: TaskSpec) -> list[PlanStep]:
    if task.operation is Operation.DEVICE_CHARACTERIZE:
        settings = task.device_characterization
        if settings is None:  # TaskSpec validation owns the user-facing error.
            raise ValueError("device characterization settings are missing")
        return [
            _step(
                "01-probe",
                "bridge.probe",
                "只读核对 Bridge、Spectre 与声明的 foundry PDK profile；不打开或写入 OA",
                SideEffect.READ_ONLY,
            ),
            _step(
                "02-characterize",
                "device.characterize",
                (
                    f"在独立远端 scratch 中用 PDK 模型运行 {settings.training_point_count} "
                    f"个训练点和 {len(settings.holdout_points)} 个留出点的 Spectre "
                    "operating-point 表征；保留输入 deck、原始 PSF、日志、大小与 "
                    "SHA-256 清单，不创建或修改 OA cellview"
                ),
                SideEffect.REMOTE_COMPUTE,
            ),
            _step(
                "03-validate",
                "device.characterize.validate",
                (
                    "核对声明偏置、器件极性、有限标量和文件清单，按宽度归一化 "
                    "Id/gm/gds/gmb/电容，并用未进入网格的真实 Spectre 点审计插值误差"
                ),
                SideEffect.READ_ONLY,
            ),
            _step(
                "04-persist",
                "evidence.persist",
                (
                    "保存 raw operating point=eda_result、归一化与插值审计="
                    "software_inference；return code 0 单独不能构成有效表征"
                ),
                SideEffect.LOCAL_WRITE,
            ),
        ]
    common_source = task.circuit is CircuitKind.COMMON_SOURCE
    differential_pair = task.circuit is CircuitKind.DIFFERENTIAL_PAIR
    analysis = task.resolved_analysis()
    declared_parameters = task_semantic_parameter_names(task)
    differential_pair_real_tail = differential_pair and "tail_bias_v" in declared_parameters
    common_source_ac = common_source and analysis is AnalysisKind.AC
    differential_pair_ac = differential_pair and analysis is AnalysisKind.AC
    differential_pair_psrr = differential_pair and analysis is AnalysisKind.PSRR
    differential_pair_cmrr = differential_pair_ac and (
        differential_pair_real_tail or "tail_output_resistance_ohm" in declared_parameters
    )
    differential_pair_linearity = (
        differential_pair and analysis is AnalysisKind.TRANSIENT
    )
    common_source_linearity = (
        common_source and analysis is AnalysisKind.TRANSIENT
    )
    common_source_noise = common_source and analysis is AnalysisKind.NOISE
    differential_pair_noise = differential_pair and analysis is AnalysisKind.NOISE
    common_source_quality = common_source and analysis is AnalysisKind.QUALITY
    candidate_oa_write = task_requests_oa_parameter_write(task)
    explicit_instance_search = bool(task.instance_parameter_space)
    template_name = (
        "共源放大器"
        if common_source
        else "NMOS 差分对"
        if differential_pair
        else "反相器"
    )
    differential_tail_description = (
        "由 OA 中 MNTAIL 与外部 BIAS 电压形成真实尾电流"
        if differential_pair_real_tail
        else "由外部共模/尾电流 testbench 提供理想尾偏置"
    )
    simulation_description = (
        "从同一次 OA/si 参数与拓扑核对生成的网表，分别运行 Spectre 复数 AC、"
        "相干 transient 线性度和 noise；三项均完整才接受候选"
        if common_source_quality
        else "用 OA 导出网表，先核对 DC operating point，再运行 Spectre 复数 AC sweep"
        if common_source_ac
        else (
            "用同一次 OA/si 网表运行平衡差模、VDD 正电源注入和 VSS 负电源注入"
            "三次 Spectre 复数 AC，并核对三次 DC 工作点与频率网格一致"
        )
        if differential_pair_psrr
        else (
            f"用 OA 导出网表，{differential_tail_description}，先核对双支路 DC "
            "operating point，再运行平衡差模 Spectre 复数 AC sweep"
        )
        if differential_pair_ac and not differential_pair_cmrr
        else (
            "用同一次 OA/si 网表分别运行平衡差模与同相共模 Spectre 复数 AC；"
            + (
                "尾管与 BIAS 来自同一 OA 拓扑，核对两次 DC 工作点一致"
                if differential_pair_real_tail
                else "显式有限尾源输出电阻只存在于外部 testbench，并核对两次 DC 工作点一致"
            )
        )
        if differential_pair_cmrr
        else (
            f"用 OA 导出网表，{differential_tail_description}，先核对双支路 DC，"
            "再运行平衡差分正弦 Spectre transient 幅度 sweep"
        )
        if differential_pair_linearity
        else (
            "用 OA 导出网表，先核对 DC operating point，再用 Spectre transient "
            "参数 sweep 运行相干正弦幅度扫描"
        )
        if common_source_linearity
        else (
            "用 OA 导出网表，先核对 DC operating point，再运行 Spectre "
            "小信号 noise sweep"
        )
        if common_source_noise
        else (
            f"用 OA 导出网表，{differential_tail_description}，先核对双支路 DC，"
            "再用单一差模输入源运行 Spectre noise sweep"
        )
        if differential_pair_noise
        else (
            f"用 OA 导出网表，{differential_tail_description}运行 Spectre DC，"
            "核对双支路 operating point"
        )
        if differential_pair
        else "用 OA 导出网表和受控 testbench 运行 Spectre DC operating point"
        if common_source
        else "用 OA 导出网表和受控 testbench 运行 Spectre transient"
    )
    sweep_description = (
        "在 max_iterations 内对每个候选运行一次 OA 同源网表核对及 AC + "
        "transient 线性度 + noise 质量组合"
        if common_source_quality
        else "在 max_iterations 内运行 OA 同源 DC + 复数 AC 候选"
        if common_source_ac
        else (
            "在 max_iterations 内运行 OA 同源双支路 DC + 差模/VDD/VSS 三路复数 "
            "AC PSRR 候选"
        )
        if differential_pair_psrr
        else "在 max_iterations 内运行 OA 同源双支路 DC + 差模复数 AC 候选"
        if differential_pair_ac and not differential_pair_cmrr
        else (
            "在 max_iterations 内运行 OA 同源双支路 DC + 差模/共模复数 AC 候选"
        )
        if differential_pair_cmrr
        else (
            "在 max_iterations 内运行 OA 同源双支路 DC + 平衡差分 transient "
            "线性度候选"
        )
        if differential_pair_linearity
        else "在 max_iterations 内运行 OA 同源 DC + transient 线性度候选"
        if common_source_linearity
        else "在 max_iterations 内运行 OA 同源 DC + noise 候选"
        if common_source_noise
        else "在 max_iterations 内运行 OA 同源双支路 DC + 差分 noise 候选"
        if differential_pair_noise
        else "在 max_iterations 内运行 OA 同源双支路 DC operating-point 候选"
        if differential_pair
        else "在 max_iterations 内运行 OA 同源 DC operating-point 候选"
        if common_source
        else "在 max_iterations 内运行 OA 同源网表候选"
    )
    evaluation_description = (
        "联合判断 DC 工作区、增益、首个 -3 dB 带宽、GBW、unity-gain、"
        "P1dB、THD、真实 VDD 功耗和积分输入参考噪声；任一分析缺证据即拒绝候选"
        if common_source_quality
        else "从复数 VOUT/VIN 提取低频增益、首个 -3 dB 带宽、GBW、"
        "unity-gain frequency，并结合 DC 工作区逐条判断规格"
        if common_source_ac
        else (
            "提取 VDD/VSS 到拓扑定义输出的 supply gain，计算 PSRR+=|Ad/Avdd|、"
            "PSRR-=|Ad/Avss|、扫频最差值与首次下降 3 dB 频点；三次 AC 必须共享 "
            "si 网表、频率网格和 DC 工作点"
        )
        if differential_pair_psrr
        else (
            "从复数 (OUTP-OUTN)/(INP-INN) 提取差模低频增益、首个 -3 dB "
            "带宽、GBW、unity-gain frequency，并结合双支路 DC/KCL 逐条判断规格"
        )
        if differential_pair_ac and not differential_pair_cmrr
        else (
            "提取差模低频增益/首个 -3 dB 带宽、共模低频增益/响应形状，以及"
            "CMRR 首次下降 3 dB 的带宽；两次 AC 必须共享 si 网表且 DC 工作点一致"
        )
        if differential_pair_cmrr
        else (
            "从 VINP-INN 与拓扑定义输出（电阻负载为 OUTP-OUTN，电流镜负载为 "
            "OUTN）的相干稳态窗口提取增益、HD2/HD3、THD、P1dB 和真实 VDD "
            "功耗，并结合双支路 DC/KCL 判定规格"
        )
        if differential_pair_linearity
        else (
            "从相干稳态窗口提取增益、HD2/HD3、THD、P1dB 和真实 VDD "
            "功耗，并结合 DC 工作区逐条判断规格"
        )
        if common_source_linearity
        else (
            "积分输出与输入参考噪声密度，并结合真实 VDD 功耗和 DC "
            "工作区逐条判断规格"
        )
        if common_source_noise
        else (
            "积分拓扑定义输出（电阻负载为 OUTP-OUTN，电流镜负载为 OUTN）与"
            "输入参考噪声密度，并结合真实尾管工作区、真实 VDD 功耗及双支路 "
            "DC/KCL 逐条判断规格"
        )
        if differential_pair_noise
        else (
            "联合判断双支路电流平衡、尾电流/供电/负载 KCL、两管饱和区、"
            "输出失调、摆幅余量和真实 DC 功耗"
        )
        if differential_pair
        else "从波形指标逐条判断规格"
    )
    if task.operating_conditions:
        condition_names = ", ".join(
            condition.name for condition in task.operating_conditions
        )
        simulation_description += (
            f"；复用同一份已核对 OA/si 网表，在 {len(task.operating_conditions)} "
            f"个显式 PVT 条件运行：{condition_names}"
        )
        evaluation_description += (
            "；每个条件独立保留 EDA 指标，全部满足约束才通过，objective 按"
            "跨条件最坏值判定"
        )
        sweep_description += (
            f"；每个候选复用一份已核对 OA/si 网表，跨 "
            f"{len(task.operating_conditions)} 个显式 PVT 条件运行："
            f"{condition_names}"
        )
    selection_description = "按规格违例与 objective 选择候选"
    if task.candidate_set is not None:
        sweep_description += (
            "；候选是显式原子 tuple，semantic/raw/testbench 参数保持成组顺序，"
            "不展开为笛卡尔积"
        )
        selection_description += (
            "；候选来源单独记录，最终排序只采用本次仿真证据"
        )
    elif task.theory_seed is not None:
        sweep_description += (
            "；候选是 hash 绑定的 theory tuple，保持成组顺序，不展开为笛卡尔积"
        )
        selection_description += (
            "；理论预测只作 software_inference 种子，最终排序只采用本次 EDA 结果"
        )
    if task.operating_conditions:
        selection_description += (
            "；只有全部条件均通过的候选才可提交，objective 使用跨条件最坏值"
        )
    probe = _step(
        "01-probe",
        "bridge.probe",
        "确认 Bridge、Virtuoso SKILL channel 与目标 profile 可用",
        SideEffect.READ_ONLY,
    )
    inspect = _step(
        "inspect",
        "schematic.inspect",
        "结构化回读实例、网络、pins 与参数",
        SideEffect.READ_ONLY,
    )
    persist = _step(
        "persist",
        "evidence.persist",
        "把动作、判定和证据写入本地 run record",
        SideEffect.LOCAL_WRITE,
    )
    netlist = _step(
        "netlist",
        "netlist.generate",
        "从目标 OA schematic 生成 si Spectre 网表并核对参数一致性",
        SideEffect.REMOTE_COMPUTE,
    )

    if task.operation is Operation.ADE_PREPARE:
        assert task.ade_prepare is not None
        design = task.ade_prepare.design or task.target.model_copy(
            update={"view": task.ade_prepare.design_view}
        )
        return [
            probe,
            _step(
                "02-preflight",
                "ade.prepare.preflight",
                (
                    f"确认 design {design.library}/{design.cell}/{design.view} 已存在"
                    f"且目标 {task.target.library}/{task.target.cell}/maestro 不存在；"
                    "已有 Maestro 状态一律拒绝，不覆盖、不合并"
                ),
                SideEffect.READ_ONLY,
            ),
            _step(
                "03-prepare",
                "ade.prepare",
                (
                    f"新建持久化 Maestro view 与 test={task.ade_prepare.test_name}，"
                    f"design={design.library}/{design.cell}/{design.view}，"
                    f"simulator={task.ade_prepare.simulator}；不设置 analysis、"
                    "stimulus、sweep 或 output，交由人工继续调整"
                ),
                SideEffect.REMOTE_WRITE,
            ),
            _step(
                "04-readback",
                "ade.prepare.readback",
                "重新打开持久化 setup，核对 Maestro view、test 名称及其实际 design library/cell/view",
                SideEffect.READ_ONLY,
            ),
            persist.model_copy(update={"id": "05-persist"}),
        ]

    if task.operation is Operation.ADE_CAPTURE:
        assert task.ade_capture is not None
        history = task.ade_capture.history or "当前可用的最新 history"
        saved_requirement = (
            "要求 setup 已保存"
            if task.ade_capture.require_saved_setup
            else "允许捕获未保存 setup，但必须显式标记"
        )
        result_requirement = (
            "要求存在非空 EDA result artifacts"
            if task.ade_capture.require_results
            else "允许只捕获 setup"
        )
        return [
            probe,
            _step(
                "02-verify-focus",
                "ade.focus.verify",
                (
                    "只读核对当前聚焦窗口正是任务声明的 library/cell/maestro；"
                    "不打开、保存、关闭或运行 ADE"
                ),
                SideEffect.READ_ONLY,
            ),
            _step(
                "03-capture",
                "ade.capture",
                (
                    f"捕获 Maestro setup、{history}、Spectre 网表/结果及哈希；"
                    f"{saved_requirement}，{result_requirement}"
                ),
                SideEffect.LOCAL_WRITE,
            ),
            persist.model_copy(
                update={
                    "id": "04-persist",
                    "description": (
                        "记录 ADE setup=bridge_readback、仿真产物/输出=eda_result；"
                        "捕获成功不等于 VDA 规格闭环"
                    ),
                }
            ),
        ]

    if task.operation is Operation.ADE_RUN:
        assert task.ade_run is not None
        resume = task.ade_run.resume_history is not None
        output_requirement = (
            "必须读回非空逐点 output/spec 表"
            if task.ade_run.require_structured_outputs
            else "允许没有逐点 output/spec，但 run record 只能记为 partial"
        )
        artifact_requirement = (
            "必须取得本次 history 的非空网表、结果和日志哈希清单"
            if task.ade_run.require_artifact_manifest
            else "允许缺少 history 产物清单，但 run record 只能记为 partial"
        )
        consistency_requirement = (
            "；必须把每个 test 的 simulator input.scs/netlist 输入束哈希绑定到 "
            "Maestro design 与只读 OA instance/node/raw parameter 回读；PDK CDF "
            "派生语义单独标记"
            if task.ade_run.require_simulator_input_consistency
            else ""
        )
        sweep_requirement = ""
        sweep_log_requirement = "完成点数和零仿真错误日志"
        timeout_recovery_requirement = ""
        if task.ade_run.sweep_verification is not None:
            sweep = task.ade_run.sweep_verification
            variables = ", ".join(
                variable.evidence_key() for variable in sweep.variables
            )
            expected_error_cells = {
                (expectation.output, point.point)
                for expectation in sweep.expected_output_evaluation_errors
                for point in sweep.points
                if all(
                    point.values[name] == value
                    for name, value in expectation.point_values.items()
                )
            }
            if expected_error_cells:
                sweep_log_requirement = (
                    "完成点数、"
                    f"{len(expected_error_cells)} 个显式声明且逐点匹配的 legacy "
                    "output evaluation error，以及零未解释错误日志"
                )
            sweep_requirement = (
                f"；必须精确回读 tests={sweep.expected_tests!r} 与 sweep "
                f"variables={variables}，并把 {len(sweep.points)} 个声明 point "
                "逐一绑定到 Detail 参数/非空 output、OA 变量引用以及非空 EDA "
                "结果；优先使用完整 exact-history 逐点输入/结果，IC6.1.8 未保留"
                "逐点文件时则强制核对唯一 runtime 符号输入束、exact-history "
                "RDB 和完成日志"
            )
            if sweep.corner_mode() and not resume:
                timeout_recovery_requirement = (
                    "；若 Bridge completion wait 超时，只允许在运行前后恰好"
                    "新增一个名称且其 log 明确 completed 时继续；多个、同名"
                    "覆盖或未完成 history 一律拒绝，不自动重跑"
                )
        result_requirement = ""
        if task.ade_run.result_mapping is not None:
            mapped_metrics = ", ".join(
                binding.metric for binding in task.ade_run.result_mapping.metrics
            )
            result_requirement = (
                "；必须在 run 前后回读并固定 sole test 中每个声明 output 的 "
                "calculator expression，再从 exact-history RDB 逐点读取 scalar "
                f"outputs，按显式 scale 映射为 VDA metrics={mapped_metrics}，"
                "再独立判定 constraints/objective；空值、非有限值或缺 output "
                "属于证据失败，不得降级成不可行候选"
            )
        return [
            probe,
            _step(
                "02-preflight",
                "ade.run.preflight",
                (
                    "确认目标 Maestro view 已存在，以独立后台 session 回读 setup tests；"
                    "逐 test 把当前 session 的 project/results dir 临时重定向到"
                    + (
                        f"已声明恢复根 {task.ade_run.resume_runtime_scratch_root}，"
                        f"并固定读取 history={task.ade_run.resume_history}；"
                        if resume
                        else " profile /data/xum 唯一新 run root，"
                    )
                    + "立即回读 analog run dir；不要求或"
                    "改变 GUI 焦点，不保存或修改 setup，结束前恢复原 session 值"
                ),
                SideEffect.READ_ONLY,
            ),
            _step(
                "03-run",
                "ade.run.resume" if resume else "ade.run",
                (
                    (
                        "不再次调用 run_and_wait；按显式 history 和唯一 runtime "
                        "scratch 恢复先前已完成的 VDA 后台运行"
                        if resume
                        else "通过 Bridge run_and_wait 执行已保存的 Maestro 原生 "
                        "analysis/parametric sweep 并等待本次返回的 history"
                    )
                    + f"；{output_requirement}；"
                    f"{artifact_requirement}{consistency_requirement}"
                    f"{sweep_requirement}{result_requirement}"
                    f"{timeout_recovery_requirement}；"
                    "history 命名/覆盖策略沿用已保存 setup，VDA 不改写也尚不能"
                    "证明名称唯一"
                ),
                SideEffect.REMOTE_COMPUTE,
            ),
            _step(
                "04-results",
                "ade.results.read",
                (
                    (
                        "按显式恢复 history"
                        if resume
                        else (
                            "按 run_and_wait 返回的 history，或严格 corner Gate "
                            "在唯一新增 completed log 下恢复的 history"
                        )
                    )
                    + " 回收每个 point 的"
                    "变量、output、spec 和 pass/fail；通过 Bridge shell 在"
                    " project/scratch 精确 history 路径与唯一 runtime input 根"
                    "只读枚举网表、PSF/结果和日志，"
                    "在 profile /data/xum run root 写入并保留小型 TSV，再下载"
                    "大小与 SHA-256；双路径同名内容冲突时失败"
                    + (
                        (
                            "；若存在逐 point 输入目录则要求每点完整；否则从唯一 "
                            "runtime 根读取 input.scs 及其显式 include 的 netlist，"
                            "并将符号 OA 绑定与 exact-history RDB/Detail 的逐点参数/"
                            f"输出、{sweep_log_requirement}共同核对"
                            if task.ade_run.sweep_verification is not None
                            else "；从唯一 runtime 根读取 input.scs 并核对 test "
                            "design、OA 连接与显式 raw 参数映射"
                        )
                        if task.ade_run.require_simulator_input_consistency
                        else ""
                    )
                ),
                SideEffect.REMOTE_COMPUTE,
            ),
            persist.model_copy(
                update={
                    "id": "05-persist",
                    "description": (
                    "记录 setup test=bridge_readback、history/output=eda_result；"
                    + (
                        "resume 成功只补齐既有 history 证据，不重复计算；"
                        if resume
                        else ""
                    )
                    + (
                        "原始 scalar output=eda_result；显式单位换算、constraint "
                        "判定与 point 选优=software_inference"
                        if task.ade_run.result_mapping is not None
                        else (
                            "原生 sweep 值与输入束、RDB 逐点结果完成一致性绑定仍不"
                            "等于已满足 VDA constraints"
                            if task.ade_run.sweep_verification is not None
                            else "后台运行成功不等于已满足 VDA constraints"
                        )
                    )
                ),
                }
            ),
        ]

    if task.operation is Operation.ADE_VARIABLES_APPLY:
        assert task.ade_variables is not None
        variables = ", ".join(
            update.evidence_key() for update in task.ade_variables.updates
        )
        selections = ", ".join(
            f"{update.name}:{update.expected_enabled}->{update.enabled}"
            for update in task.ade_variables.global_selection_updates
        )
        declared_changes = "; ".join(
            value
            for value in (
                f"scopes={variables}" if variables else "",
                f"global selections={selections}" if selections else "",
            )
            if value
        )
        corner_guard = (
            "，并精确核对声明的 enabled corners"
            if task.ade_variables.expected_corners is not None
            else ""
        )
        return [
            probe,
            _step(
                "02-preflight",
                "ade.variables.preflight",
                (
                    "确认目标 Maestro view 已存在且当前没有任何已配置的开放 "
                    f"Maestro session；独立后台回读 tests{corner_guard}、每个"
                    "声明 scope 的变量旧值及目标 global-variable selection，"
                    "必须逐项匹配任务前置条件"
                ),
                SideEffect.READ_ONLY,
            ),
            _step(
                "03-apply",
                "ade.variables.apply",
                (
                    f"仅更新声明的 Maestro design variable 状态: {declared_changes}；"
                    "每项写后立即回读，不改 test/analysis/output/corner membership "
                    "或 schematic，全部一致后只保存一次 setup；global selection "
                    "只移动声明名称并保持其余集合；本 Gate 不证明未声明 scope "
                    "没有覆盖，也不证明仿真采用新值"
                ),
                SideEffect.REMOTE_WRITE,
            ),
            _step(
                "04-readback",
                "ade.variables.readback",
                (
                    "关闭写会话后重新打开后台 session，再次核对 tests、可选 "
                    "enabled corners、所有目标变量与 global selection 全集合；"
                    "保存后的不一致或连接失败不自动覆盖式重试"
                ),
                SideEffect.READ_ONLY,
            ),
            persist.model_copy(
                update={
                    "id": "05-persist",
                    "description": (
                        "记录请求=user_input、旧值/即时值/持久化值="
                        "bridge_readback；成功只证明声明 scope 的 CAS patch"
                    ),
                }
            ),
        ]

    if task.operation is Operation.ADE_CORNERS_APPLY:
        assert task.ade_corners is not None
        additions = ", ".join(
            addition.name for addition in task.ade_corners.additions
        )
        return [
            probe,
            _step(
                "02-preflight",
                "ade.corners.preflight",
                (
                    "确认目标 Maestro view 已存在且没有任何已配置的开放 session；"
                    "精确核对 tests，以及 enabled/all corner 有序列表都与声明的"
                    "旧顺序一致；待新增名称在 disabled corner 中也必须不存在"
                ),
                SideEffect.READ_ONLY,
            ),
            _step(
                "03-apply",
                "ade.corners.apply",
                (
                    f"只用 Bridge public set_corner add-only 新增 enabled corners: "
                    f"{additions}；逐项写后立即回读，全部一致后只保存一次 "
                    "setup；不配置 disabled tests、model file、变量、"
                    "analysis、output 或 schematic"
                ),
                SideEffect.REMOTE_WRITE,
            ),
            _step(
                "04-readback",
                "ade.corners.readback",
                (
                    "关闭写会话后重新打开 background session，重新核对 tests、"
                    "全部 corner 和 enabled corner 的完整顺序；不一致不自动重写"
                ),
                SideEffect.READ_ONLY,
            ),
            persist.model_copy(
                update={
                    "id": "05-persist",
                    "description": (
                        "记录请求=user_input、corner membership 前后状态="
                        "bridge_readback；成功只证明 add-only scope 建立，不证明"
                        "任何 process/environment corner 已进入仿真"
                    ),
                }
            ),
        ]

    if task.operation is Operation.ADE_SETUP_APPLY:
        assert task.ade_setup is not None
        analyses = ", ".join(
            update.label() for update in task.ade_setup.analyses
        ) or "none"
        outputs = ", ".join(
            output.label() for output in task.ade_setup.outputs
        ) or "none"
        return [
            probe,
            _step(
                "02-preflight",
                "ade.setup.preflight",
                (
                    "确认目标 Maestro view 已存在且没有任何已配置的开放 session；"
                    "精确核对 tests、每个声明 analysis 的 enabled/options 旧状态，"
                    "并确认所有待新增命名 output 均不存在；任一不符则零写入"
                ),
                SideEffect.READ_ONLY,
            ),
            _step(
                "03-apply",
                "ade.setup.apply",
                (
                    f"更新 analyses: {analyses}；新增 outputs/specs: {outputs}；"
                    "逐项写后立即结构化回读，全部一致后只保存一次 setup。"
                    "不替换已有 output，也不运行仿真或改 schematic/variables/corners"
                ),
                SideEffect.REMOTE_WRITE,
            ),
            _step(
                "04-readback",
                "ade.setup.readback",
                (
                    "关闭写会话后重新打开 background session，核对 tests 与所有"
                    "目标 analysis/output/spec 的持久化状态；不一致不自动重写"
                ),
                SideEffect.READ_ONLY,
            ),
            persist.model_copy(
                update={
                    "id": "05-persist",
                    "description": (
                        "记录请求=user_input、旧值/即时值/持久化值="
                        "bridge_readback；成功不等于 analysis 已执行或 output 已产生结果"
                    ),
                }
            ),
        ]

    if task.operation is Operation.SCHEMATIC_CREATE:
        create_description = (
            f"显式删除并按受控模板替换已有{template_name} schematic"
            if task.safety.replace_existing
            else f"按受控模板创建{template_name} schematic；已有对象保持不变"
        )
        return [
            probe,
            _step(
                "02-create",
                "schematic.create",
                create_description,
                SideEffect.REMOTE_WRITE,
            ),
            inspect.model_copy(update={"id": "03-inspect"}),
            persist.model_copy(update={"id": "04-persist"}),
        ]
    if task.operation is Operation.SCHEMATIC_INSPECT:
        return [
            probe,
            inspect.model_copy(update={"id": "02-inspect"}),
            persist.model_copy(update={"id": "03-persist"}),
        ]
    if task.operation is Operation.SCHEMATIC_TRANSFORM:
        if task.circuit is CircuitKind.EXISTING_SCHEMATIC:
            assert task.topology_delta is not None
            direction = task.topology_delta.direction
            contract = task.topology_delta.contract
            input_sha256 = (
                contract.expected_before_sha256
                if direction == "forward"
                else contract.expected_after_sha256
            )
            output_sha256 = (
                contract.expected_after_sha256
                if direction == "forward"
                else contract.expected_before_sha256
            )
            capability = f"schematic.transform.topology-delta.{direction}"
            if contract.master_parameter_migrations:
                parameter_clause = (
                    f"另对 {len(contract.master_parameter_migrations)} 个被替换实例"
                    "执行契约绑定的旧 CDF 值 CAS、新值 callback 写入及独立回读；"
                )
            else:
                parameter_clause = "不改设备参数；"
            placement_clause = (
                ""
                if task.topology_delta.expected_output_placement_sha256 is None
                else (
                    "写后 wire/label/pin/instance placement SHA-256 还必须等于 "
                    f"{task.topology_delta.expected_output_placement_sha256}；"
                )
            )
            resume_clause = (
                ""
                if not task.topology_delta.resume_partial_prefix
                else (
                    "若 fresh readback 证明当前状态是本契约唯一可逆的已执行前缀，"
                    "且孤立物理 pin figure 精确匹配该前缀，则允许清理 figure 并从"
                    "剩余 operation 继续；"
                )
            )
            description = (
                f"对现有 schematic 执行预声明 topology delta {contract.id!r} 的"
                f"{direction}方向，共 "
                f"{len(contract.operations if direction == 'forward' else contract.inverse_operations)} "
                "个 allowlisted 结构操作；写前完整结构 SHA-256 必须等于 "
                f"{input_sha256}，写后独立完整回读必须等于 {output_sha256}；"
                f"{placement_clause}{parameter_clause}{resume_clause}"
                "若保存后的任一审计失败，"
                "只有 fresh readback 精确匹配声明输出时才自动执行逆向恢复；"
                "不创建或替换目标 cellview"
            )
        elif task.circuit is CircuitKind.INVERTER:
            capability = "schematic.transform.inverter-testbench"
            description = (
                "在同一 cellview 内保留 MN0/MP0 与 pins，新增固定边界的 "
                "VDD0/VIN0/CL0/GND0 testbench，把 MN0.S/B 接到 gnd!，并按 "
                "vdd_v/load_ff 设置源和负载；不替换或另建 cellview"
            )
        elif task.circuit is CircuitKind.DIFFERENTIAL_PAIR:
            transform_action = task.resolved_schematic_transform_action()
            if transform_action is SchematicTransformAction.ADD_TAIL_DEVICE:
                capability = "schematic.transform.differential-pair-tail-device"
                description = (
                    "在同一 cellview 内保留 MN0/MN1/RD0/RD1 与全部已有 pins，"
                    "新增 MNTAIL(D=TAIL,G=BIAS,S/B=VSS) 和 BIAS pin，并按 "
                    "tail_width_um/tail_length_um 设置尾管；不替换或另建 cellview"
                )
            elif transform_action is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION:
                capability = (
                    "schematic.transform.differential-pair-source-degeneration.remove"
                )
                description = (
                    "在同一真实尾管差分对 cellview 内仅删除 RS0/RS1 及各自的 "
                    "VDA 端子 stub，把 MN0.S/MN1.S 从 NSP/NSN 恢复到 TAIL，"
                    "并移除两个内部网；保留核心、MNTAIL、pins 与实例参数"
                )
                if (
                    task.schematic_transform is not None
                    and task.schematic_transform.expected_restored_placement_sha256
                    is not None
                ):
                    description += "；恢复后 placement SHA-256 必须与声明基线一致"
            elif (
                transform_action
                is SchematicTransformAction.REPLACE_RESISTIVE_LOAD_WITH_CURRENT_MIRROR
            ):
                capability = (
                    "schematic.transform.differential-pair-current-mirror-load"
                )
                description = (
                    "在同一真实尾管、无源退化的差分对 cellview 内仅删除 "
                    "RD0/RD1 及其 VDA 端子 stub，新增匹配 MP0/MP1；MP0 二极管"
                    "连接到 OUTP，MP1 镜像到 OUTN，并按 pmos_load_width_um/"
                    "pmos_load_length_um 设置；保留 MN0/MN1/MNTAIL、pins 与 nets"
                )
            elif transform_action is SchematicTransformAction.RESTORE_RESISTIVE_LOAD:
                capability = (
                    "schematic.transform.differential-pair-current-mirror-load.remove"
                )
                description = (
                    "在同一 PMOS 电流镜负载差分对 cellview 内仅删除 MP0/MP1 "
                    "及其 VDA 端子 stub，按 load_resistance_ohm 恢复 RD0/RD1；"
                    "保留 MN0/MN1/MNTAIL、pins 与 nets"
                )
                if (
                    task.schematic_transform is not None
                    and task.schematic_transform.expected_restored_placement_sha256
                    is not None
                ):
                    description += "；恢复后 placement SHA-256 必须与声明基线一致"
            else:
                capability = (
                    "schematic.transform.differential-pair-source-degeneration"
                )
                description = (
                    "在同一真实尾管差分对 cellview 内把 MN0.S/MN1.S 分别改接 "
                    "NSP/NSN，新增匹配的 RS0(NSP,TAIL)/RS1(NSN,TAIL) 并设置 "
                    "同一个 source_resistance_ohm；保留核心、MNTAIL 与全部 pins"
                )
        else:
            transform_action = task.resolved_schematic_transform_action()
            if (
                transform_action
                is SchematicTransformAction.REMOVE_SOURCE_DEGENERATION
            ):
                capability = "schematic.transform.source-degeneration.remove"
                description = (
                    "在同一 cellview 内仅删除 RS0 及其 VDA 创建的端子 stub，"
                    "把 MN0.S 的 NSRC 标签恢复为 VSS，并移除 NSRC；保留 "
                    "MN0、RD0、pins 与已有实例参数，不新建或替换 cellview"
                )
                if (
                    task.schematic_transform is not None
                    and task.schematic_transform.expected_restored_placement_sha256
                    is not None
                ):
                    description += (
                        "；恢复后的实例/pin/标签/导线 placement SHA-256 必须与"
                        "声明基线完全一致"
                    )
            else:
                capability = "schematic.transform.source-degeneration"
                description = (
                    "在同一 cellview 内仅把 MN0.S 的 VSS 标签改为 NSRC，"
                    "新增 RS0(NSRC, VSS) 并设置退化电阻；保留 MN0、RD0、"
                    "pins 与已有实例参数，不新建或替换 cellview"
                )
        return [
            probe,
            inspect.model_copy(update={"id": "02-before"}),
            _step(
                "03-transform",
                capability,
                description,
                SideEffect.REMOTE_WRITE,
            ),
            inspect.model_copy(update={"id": "04-after"}),
            persist.model_copy(update={"id": "05-persist"}),
        ]
    if task.operation is Operation.PARAMETERS_APPLY:
        if task.instance_parameter_updates and task.parameters:
            apply_description = (
                "应用器件语义参数及按实例给出的原始 CDF/OA 字符串，"
                "并逐项定向回读；callback 后不一致时至多按声明顺序重放一次"
            )
        elif task.instance_parameter_updates:
            apply_description = (
                "按实例应用明确给出的原始 CDF/OA 参数字符串并逐项定向回读；"
                "callback 后不一致时至多按声明顺序重放一次"
            )
        else:
            apply_description = "应用明确给出的器件语义参数"
        return [
            probe,
            inspect.model_copy(update={"id": "02-before"}),
            _step(
                "03-apply",
                "parameters.apply",
                apply_description,
                SideEffect.REMOTE_WRITE,
            ),
            inspect.model_copy(update={"id": "04-after"}),
            persist.model_copy(update={"id": "05-persist"}),
        ]
    if task.operation is Operation.SIMULATION_RUN:
        return [
            probe,
            inspect.model_copy(update={"id": "02-inspect"}),
            netlist.model_copy(update={"id": "03-netlist"}),
            _step(
                "04-simulate",
                "simulation.run",
                simulation_description,
                SideEffect.REMOTE_COMPUTE,
            ),
            _step(
                "05-evaluate",
                "results.evaluate",
                evaluation_description,
                SideEffect.READ_ONLY,
            ),
            persist.model_copy(update={"id": "06-persist"}),
        ]
    if task.operation is Operation.DESIGN_TUNE:
        stage_description = (
            "逐候选暂存 OA 参数并回读；失败或无可行点时恢复初始参数"
            if candidate_oa_write
            else "候选只改变显式 testbench 条件；每点复用同一 OA readback，不写 OA"
        )
        if task.operating_conditions and candidate_oa_write:
            stage_description += "；每个候选只暂存一次 OA，再跨条件复用"
        if explicit_instance_search:
            stage_description += (
                "；实例 CDF 候选按原始字符串透传并逐项定向回读，不猜单位或别名"
            )
        finalize_description = (
            "提交最佳可行参数，或恢复搜索前 OA 参数"
            if candidate_oa_write
            else "记录最佳 testbench 条件，并再次确认 OA 参数保持不变"
        )
        if explicit_instance_search:
            finalize_description += "；最佳实例字段必须再次定向回读"
        return [
            probe,
            inspect.model_copy(update={"id": "02-before"}),
            _step(
                "03-stage",
                "parameters.stage",
                stage_description,
                SideEffect.REMOTE_WRITE if candidate_oa_write else SideEffect.READ_ONLY,
            ),
            netlist.model_copy(update={"id": "04-netlist"}),
            _step(
                "05-sweep",
                "simulation.sweep",
                sweep_description,
                SideEffect.REMOTE_COMPUTE,
            ),
            _step(
                "06-select",
                "results.select",
                selection_description,
                SideEffect.READ_ONLY,
            ),
            _step(
                "07-finalize",
                "parameters.finalize",
                finalize_description,
                SideEffect.REMOTE_WRITE if candidate_oa_write else SideEffect.READ_ONLY,
            ),
            inspect.model_copy(update={"id": "08-after"}),
            persist.model_copy(
                update={
                    "id": "09-persist",
                    "description": "逐候选原子保存 checkpoint，并写入最终 run record",
                }
            ),
        ]
    return [
        probe,
        _step(
            "02-create-or-verify",
            "schematic.ensure",
            f"创建缺失的{template_name} schematic，或验证已有 topology",
            SideEffect.REMOTE_WRITE,
        ),
        _step(
            "03-stage",
            "parameters.stage",
            (
                "逐候选暂存 OA 参数并回读；失败或无可行点时恢复初始参数"
                if candidate_oa_write
                else "候选只改变显式 testbench 条件；每点复用同一 OA readback，不写 OA"
            )
            + (
                "；每个候选只暂存一次 OA，再跨条件复用"
                if task.operating_conditions and candidate_oa_write
                else ""
            )
            + (
                "；实例 CDF 候选按原始字符串透传并逐项定向回读，不猜单位或别名"
                if explicit_instance_search
                else ""
            ),
            SideEffect.REMOTE_WRITE if candidate_oa_write else SideEffect.READ_ONLY,
        ),
        netlist.model_copy(update={"id": "04-netlist"}),
        _step(
            "05-sweep",
            "simulation.sweep",
            sweep_description,
            SideEffect.REMOTE_COMPUTE,
        ),
        _step(
            "06-select",
            "results.select",
            selection_description,
            SideEffect.READ_ONLY,
        ),
        _step(
            "07-finalize",
            "parameters.finalize",
            (
                "提交最佳可行参数，或恢复搜索前 OA 参数"
                if candidate_oa_write
                else "记录最佳 testbench 条件，并再次确认 OA 参数保持不变"
            )
            + (
                "；最佳实例字段必须再次定向回读"
                if explicit_instance_search
                else ""
            ),
            SideEffect.REMOTE_WRITE if candidate_oa_write else SideEffect.READ_ONLY,
        ),
        inspect.model_copy(update={"id": "08-after"}),
        persist.model_copy(
            update={
                "id": "09-persist",
                "description": "逐候选原子保存 checkpoint，并写入最终 run record",
            }
        ),
    ]


def build_plan(task: TaskSpec) -> ExecutionPlan:
    validate_task_capability(task)
    steps = _steps_for(task)
    task_payload = task.model_dump(mode="json", exclude_none=True)
    if not task.instance_parameter_space:
        # Preserve tokens for tasks created before raw instance sweeps existed.
        task_payload.pop("instance_parameter_space", None)
    if not task.operating_conditions:
        # Keep pre-PVT task tokens stable; this field did not exist in schema v1
        # records before the bounded operating-condition extension.
        task_payload.pop("operating_conditions", None)
    payload = {
        "task": task_payload,
        "steps": [step.model_dump(mode="json") for step in steps],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    token = hashlib.sha256(canonical).hexdigest()[:16]
    return ExecutionPlan(
        task_id=task.id,
        operation=task.operation,
        circuit=task.circuit,
        steps=steps,
        confirmation_token=token,
    )
