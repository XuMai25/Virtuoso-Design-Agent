# 共享入口、换机清单与 Bridge 参考包：本地验收

日期：2026-10-26。范围仅为公共使用文档、第三方差异导出及必要离线回归。
没有改 VDA 执行器／授权／路径守卫，没有修改原 Bridge checkout、系统、网络、Vault、PDK 或远端。
远端连接、部署、OA 写入、网表生成、Spectre 与 GUI 操作均为0。

## 问题与交付

原 README 把安装、维护者绝对路径、私有 artifacts 与多年现场记录混排，外部用户无法区分
可直接运行的入口、环境配置与历史证据。本轮：

- README 改为 clone/install/offline demo、任务导航和明确能力边界；
  原详细内容保存在 maintainer-workflows，移动后的相对链接改为指向原位置，不丢旧记录。
- getting-started 提供从零安装、plan-only、显式 demo、run record/review 和缺项解释。
- agent-start-here 提供接手顺序、真实操作前范围、现有路径选择及可复制 prompt。
- environment-porting 区分原生 Bridge 配置与 VDA 绑定项，列出实际源文件／字段、验证和停止条件：
  Bridge Python、VB_PROFILE、lifecycle 与 worker 的配置边界、PDK facts、/data/xum 守卫、
  CDF／symbol／si 绑定、checkpoint、历史材料与新平台清理。
- references/bridge-compat 导出相对上游 ecaf5a5c 的完整差异，来源为3688192；
  包括两个远端 daemon 资源、客户端／安全行为、测试、原补丁记录、manifest 和原 MIT License。
  不复制整个第三方仓库，不部署补丁，不分发秘密／PDK／OA 或私有运行记录。
- 补丁与许可证在 gitattributes 中固定LF，避免跨机器 checkout 破坏 manifest 的原始字节 SHA-256。
  patch 作为序列化差异文本允许其必需的上下文空白，不关闭源代码的 whitespace 检查。

## 实际本地验收

| 层次 | 实际操作／结果 | 边界 |
| --- | --- | --- |
| 新入门回归 | 3 passed；含空格的独立 cwd、无 Bridge 解释器、plan-only 不创建结果、显式 demo、checkpoint、software_inference 与 review；链接／参考包身份检查 | 软件流程，不是EDA |
| 上游隔离副本 | 从精确基线导出src/tests/tools/examples等，在隔离目录运行git apply --check并应用；manifest中25个patched Git blob全部匹配 | Git规范化内容；不是当前任意新上游适配 |
| 参考补丁聚焦回归 | 隔离后的Bridge源上运行6个相关测试模块，42 passed | 使用原Bridge解释器及依赖，无新Bridge安装，无远端 |
| 新用户干净副本 | Git源导出＋本轮公开文档/测试/参考文件；独立新venv，按pip install -e ".[dev]"安装成功 | 同一Windows主机的隔离演练，不是另一台真实机器 |
| 干净副本功能 | catalog、plan、默认run不执行、显式demo执行、design-review及入门测试3 passed；Bridge import不存在，demo前无私有artifacts | demo自生成的记录仍是software_inference |
| 原双环境完整验收 | 主环境1367 passed、1个声明的Bridge import-only skip；独立Bridge补验8 passed、0 skipped，整体passed | 两阶段分别计数，不作为电路/新环境性能证据 |

干净venv实际版本：Python3.13.14、pydantic2.14.1、pytest9.1.1；VDA包仍为0.1.0。
普通依赖从包索引安装到隔离venv，没有升级维护者主环境或安装第二份Bridge。
这里只验证这些实际版本，未声称所有满足范围的将来版本已通过。

本地材料位于：
- artifacts/sharing-20261026-6b54e0dd65a54ac9836fd257efa3bfcc/
- artifacts/local-validation/20261026-sharing-release/summary.json

不纳入Git。原Bridge HEAD／工作树、用户AGENTS.md字节身份在发布前后另核对。
已发布参考包只包含明确列出的25个源码/测试/模板/文档路径，不含实际.env、token、私钥、
license服务器配置、PDK或远端生成setup；其许可证文件是上游公开的软件版权声明。

## 未闭合与使用边界

1. 新服务器／新账号的远端根仍需对方agent按清单适配；本轮不把现有/data/xum守卫删掉，
   也不声称“改一个host即可迁移所有VDA operation”。
2. 新PDK需独立facts、master/CDF/几何及网表绑定和最小真实验收；不继承维护者的性能数据。
3. Bridge补丁只绑定指定上游。新上游先检查等价修复，不强行重复打补丁；本地客户端更新
   不表示运行中的daemon或SKILL已更新。
4. 未运行对方服务器、Linux/macOS客户端GUI／清理或新环境ADE验收；真实部署和计算须另列授权。
5. 私有历史记录不随GitHub分发；缺材料时完整验收如实报告incomplete，未软化skip或证据门。
6. VDA完整L5B质量闭环、任意拓扑自动识别和连续/全局最优不由本轮文档或demo成立。

到此收口共享入口，不为补文档重跑任何电路。
