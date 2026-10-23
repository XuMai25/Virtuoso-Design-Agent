# 2026-10-23 STB 证据接入正式路径：本地与历史实测重放

## 结论和实际范围

已将上次辅助脚本的证据留存/原生对照接入正式 STB worker、离线 CLI、设计复盘和
跨轮次控制。**本轮远端动作 0、OA 写入 0、新增 Spectre 作业 0**。
没有修改第三方 Bridge、系统、PDK、已有 `cds.lib`、用户会话或 Vault。

不是新增一个固定差分对模板，也没有另造执行器。正式入口仍是已有
`existing_schematic` 的独立仿真、分析阶段及 winner-only verification。

## 改动

1. STB adapter 分配独占持久目录；共享网表阶段复制原 si 输入，不追加 netlisting。
   正常结果、原始文件和部分失败均留存；submission/receipt/lifecycle 指明未决状态，
   没有自动重试、重新提交或清理证据的逻辑。
2. 对实际 `margin.stb` scalar/metadata 做窄范围解析，核对与波形的 probe、版本、日期、
   analysis 和 OP producer。原生与 VDA 的 PM/频率/有限 GM 不一致时 incomplete；
   缺文件、空文件、错误单位、重复字段、无原因的 NaN 均不能被成功 return code 掩盖。
3. `stability-audit` 纯本地核对外层 bundle SHA、清单、输入与核心文件字节，重放已保存的
   Bridge 解码样本，并重读原生 margin。明确区分 hash/软件重算与新 EDA 测量。
4. `design-review` 报告原始证据状态；`design-iterate` / `design-next` 不把缺失/漂移的
   STB 文件或 nominal/winner 混用的结果当作完整实测闭环，必要时仅生成无写/计算权限
   的 inspect 草案。ADE 和既有局部 operation 的授权边界不变。

## 本地回归

最终执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts='-p no:cacheprovider' -q -ra `
  --basetemp artifacts/test-temp/stb-formal-full-final2 `
  --junitxml=artifacts/stb-formal-20261023/pytest-final2.xml
```

结果：**1122 passed、1 skipped**，8.66 s。skip 为主 VDA 环境没有安装 `virtuoso_bridge`，
因此 `tests/test_schematic_drawing_bridge.py` 的 Bridge symbol/unit tests 跳过；不是远端失败。
另行在 Bridge Python 中运行 `tests/test_schematic_drawing_bridge.py` 和
`tests/test_stability_evidence.py`：**35 passed**，1.43 s；补齐上述 Bridge symbol/unit tests，
没有连接远端。两个环境的测试有重叠，不将数量直接相加。
对应本地 JUnit：`artifacts/stb-formal-20261023/pytest-bridge-env.xml`。

风险测试涵盖：

- 正负 PM/有限负 GM、NaN 原因及含空格 state 字符串；probe/version/date 串用，重复/错单位标量。
- 原生对照超出预先声明容差；缺 margin、原始字节漂移、缺/空 log、清单/绑定/路径逃逸。
- 持久目录并发不冲突、中断保留、缺持久根时在 Bridge 导入和远端动作前拒绝。
- 正式 worker 正常/缺 margin/错 probe/数值不一致/要求有限 GM 的路径，以及共享一次 si 的
  DC→STB 阶段；模拟计数证明不为打包或对照再提交一遍。
- candidate、winner-only 的复盘、可行锚点及旧文件丢失后的停止决策；无 bundle 的旧 STB
  记录不自动补齐，也不重新仿真。旧 DC/AC/其他 operation 经完整测试集回归。

`catalog` 与反相器 demo `plan` 本地入口亦正常；未调用 demo 结果证明电路性能。

## 复用旧 N28 真实文件，而非新跑电路

输入来自[上次真实验收](2026-10-23-n28-stb-native-acceptance.md)保存的 `retained-job`；
首先按旧 `09-native-comparison.json` 清单核对源字节。使用实际 Bridge PSF reader 解码
原 `stb.stb`，经新正式提取、native scalar 对照与留存 helper，再由主 VDA CLI 离线复核。

| 指标 | 原生值 | 新代码重提取 |
| --- | --- | --- |
| PM | 85.82459515° | 85.83217331° |
| 环路单位增益频率 | 8.097762669 GHz | 8.097037259 GHz |
| GM | 无有限值，原因保留 | 缺失，不伪造无穷 |

PM 差 0.00757816°、频率相对差 0.00895816%，通过预设一致性门。
复核结果 `verified`，source 为 `software_inference`；原始文件仍是旧作业的 `eda_result`，
没有将重放时间或新打包文件冒充新一次仿真。

旧作业是模板差分对的 raw-si include，离线导入时为它建立**同字节** include/log 别名，
便于复用正式打包 helper；没有生成新网表、改变电气参数或证明新 TaskSpec 已实机执行。
详细来源、别名、hash 和结果保留在本地 `artifacts/stb-formal-20261023/real-native-replay.json`，
CLI 报告为同目录 `cli-real-audit.json` / `.md`，另以原外层 SHA 强绑定重审得到
`cli-final-bound-audit.json` / `.md`，同样为 verified。没有提交原生 log 或任何凭据。

## 边界和后续

- 新 formal worker 的远端全链仍未重跑；本次是本地集成测试＋真实旧文件重放。
- 已验证的 native scalar 格式来自 Spectre 21.1；其他格式先报不完整，不静默猜测。
- 清单/哈希证明记录间字节绑定，不是数字签名、独立波形解码或当前 OA/模型未漂移证明。
- 仍未完成 ADE STB GUI state/Load/重跑、其他 PDK、有限 PVT、多环/差分共模稳定性和
  正常新模块多轮设计质量闭环。PVT 不因此变成默认步骤。
- 下一次真实设计时，按实际拓扑明确反馈、probe 与规格，在正常任务中验证正式集成执行，
  复用本地复核与跨轮次控制；不为同一功能额外造电路或铺随机扫描。

karpathy-guidelines 促使这轮只接通已有 Bridge/VDA 通路，新增一个窄 scalar reader 和
本地审计入口；没有升级 Bridge、复制传输/仿真框架或改动 GUI。
