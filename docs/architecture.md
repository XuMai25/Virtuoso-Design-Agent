# 架构

## 核心分工

```text
Codex / 人类
  |  目标、约束、审查、批准
  v
VDA task contract + planner
  |  稳定步骤、副作用、token
  v
bounded executor
  |  候选预算、判规格、证据
  v
adapter port
  +-- demo adapter       (software_inference)
  +-- Bridge subprocess  (bridge_readback / eda_result)
          |
          v
   virtuoso-bridge-lite
          |
          +-- Virtuoso SKILL / OA
          +-- Spectre
          +-- SSH / file transfer
```

VDA 不嵌入一个新的通用 LLM。Codex 负责开放式推理，VDA 负责把高风险 EDA 动作收敛为受控命令和机器可核验结果。

## 为什么使用独立 worker

`virtuoso-bridge-lite` 已有自己的 Python 虚拟环境和本机配置。主程序通过该环境中的 Python 启动 JSON worker，而不是把 Bridge 源码复制进来或强制安装成本项目依赖。这样可以保持 Bridge 独立升级、避免依赖漂移，并把跨环境协议缩小到版本化 JSON action。

## 任务与局部能力

任务不是固定的“全流程按钮”。`operation` 决定实际范围：

| operation | 作用 | 远端副作用 |
| --- | --- | --- |
| `schematic.create` | 建图并结构回读 | OA 写入 |
| `schematic.inspect` | 读取拓扑、参数、pins | 只读 |
| `parameters.apply` | 应用指定参数并回读 | OA 写入 |
| `simulation.run` | 单点仿真并判规格 | scratch/计算 |
| `design.tune` | 有限搜索，选择并应用最佳参数 | 计算 + OA 写入 |
| `design.close_loop` | 建图、搜索、应用、回读 | 计算 + OA 写入 |

因此上层 agent 可以只要求“建原理图”“把这组参数应用进去”或“只跑仿真”，无需伪装成完整设计任务。

## 证据链

每次运行至少保存任务和计划 token、adapter 与证据来源、动作状态、候选参数、仿真指标、逐条规格判定、最终选择、OA 回读摘要，以及错误和未验证边界。后续 Maestro、Calibre 和 PEX 沿用同一证据模型。
