"""Compile a task contract into a stable, reviewable execution plan."""

from __future__ import annotations

import hashlib
import json

from .catalog import task_requests_oa_parameter_write, validate_task_capability
from .models import (
    AnalysisKind,
    CircuitKind,
    ExecutionPlan,
    Operation,
    PlanStep,
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
    common_source = task.circuit is CircuitKind.COMMON_SOURCE
    analysis = task.resolved_analysis()
    common_source_ac = common_source and analysis is AnalysisKind.AC
    common_source_linearity = (
        common_source and analysis is AnalysisKind.TRANSIENT
    )
    common_source_noise = common_source and analysis is AnalysisKind.NOISE
    common_source_quality = common_source and analysis is AnalysisKind.QUALITY
    candidate_oa_write = task_requests_oa_parameter_write(task)
    template_name = "共源放大器" if common_source else "反相器"
    simulation_description = (
        "从同一次 OA/si 参数与拓扑核对生成的网表，分别运行 Spectre 复数 AC、"
        "相干 transient 线性度和 noise；三项均完整才接受候选"
        if common_source_quality
        else "用 OA 导出网表，先核对 DC operating point，再运行 Spectre 复数 AC sweep"
        if common_source_ac
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
        else "在 max_iterations 内运行 OA 同源 DC + transient 线性度候选"
        if common_source_linearity
        else "在 max_iterations 内运行 OA 同源 DC + noise 候选"
        if common_source_noise
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
            "从相干稳态窗口提取增益、HD2/HD3、THD、P1dB 和真实 VDD "
            "功耗，并结合 DC 工作区逐条判断规格"
        )
        if common_source_linearity
        else (
            "积分输出与输入参考噪声密度，并结合真实 VDD 功耗和 DC "
            "工作区逐条判断规格"
        )
        if common_source_noise
        else "从波形指标逐条判断规格"
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
            "；必须把每个 test 的 exact input.scs 哈希绑定到 Maestro design 与"
            "只读 OA instance/node/raw parameter 回读；PDK CDF 派生语义单独标记"
            if task.ade_run.require_simulator_input_consistency
            else ""
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
                    f"{artifact_requirement}{consistency_requirement}；"
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
                        else "按 run_and_wait 为本次调用返回的 history"
                    )
                    + " 回收每个 point 的"
                    "变量、output、spec 和 pass/fail；通过 Bridge shell 在"
                    " project/scratch 精确 history 路径与唯一 runtime input 根"
                    "只读枚举网表、PSF/结果和日志，"
                    "在 profile /data/xum run root 写入并保留小型 TSV，再下载"
                    "大小与 SHA-256；双路径同名内容冲突时失败"
                    + (
                        "；从唯一 runtime 根读取 input.scs 并核对 test design、"
                        "OA 连接与显式 raw 参数映射"
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
                    + "后台运行成功不等于已满足 VDA constraints"
                ),
                }
            ),
        ]

    if task.operation is Operation.ADE_VARIABLES_APPLY:
        assert task.ade_variables is not None
        variables = ", ".join(
            update.evidence_key() for update in task.ade_variables.updates
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
                    f"Maestro session；独立后台回读 tests{corner_guard} 与每个"
                    "声明 scope 的变量旧值，必须逐项匹配任务前置条件"
                ),
                SideEffect.READ_ONLY,
            ),
            _step(
                "03-apply",
                "ade.variables.apply",
                (
                    f"仅更新声明的 Maestro design variable scopes: {variables}；"
                    "每项 set_var 后立即 get_var，不改 test/analysis/output/corner "
                    "membership 或 schematic，全部一致后只保存一次 setup；本 "
                    "Gate 不证明未声明 scope 没有覆盖，也不证明仿真采用新值"
                ),
                SideEffect.REMOTE_WRITE,
            ),
            _step(
                "04-readback",
                "ade.variables.readback",
                (
                    "关闭写会话后重新打开后台 session，再次核对 tests、可选 "
                    "enabled corners 与所有目标变量；保存后的不一致或连接失败"
                    "不自动覆盖式重试"
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
        return [
            probe,
            inspect.model_copy(update={"id": "02-before"}),
            _step(
                "03-transform",
                "schematic.transform.source-degeneration",
                (
                    "在同一 cellview 内仅把 MN0.S 的 VSS 标签改为 NSRC，"
                    "新增 RS0(NSRC, VSS) 并设置退化电阻；保留 MN0、RD0、"
                    "pins 与已有实例参数，不新建或替换 cellview"
                ),
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
        finalize_description = (
            "提交最佳可行参数，或恢复搜索前 OA 参数"
            if candidate_oa_write
            else "记录最佳 testbench 条件，并再次确认 OA 参数保持不变"
        )
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
                "按规格违例与 objective 选择候选",
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
            "按规格违例与 objective 选择候选",
            SideEffect.READ_ONLY,
        ),
        _step(
            "07-finalize",
            "parameters.finalize",
            (
                "提交最佳可行参数，或恢复搜索前 OA 参数"
                if candidate_oa_write
                else "记录最佳 testbench 条件，并再次确认 OA 参数保持不变"
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
    payload = {
        "task": task.model_dump(mode="json", exclude_none=True),
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
