# Research Workbench / 科研工作台

Research Workbench 是面向个人科研工作的本地操作台。它把 Obsidian 中的项目、文献、概念、实验和复盘入口组织成“下一步可执行”的工作流，但不替代 Obsidian、Zotero、外部工程目录或 Codex。

第一阶段是可运行的只读应用：扫描 Obsidian Vault，建立本地 SQLite 索引，提供中文 React 前端、搜索筛选、详情、健康检查、收件箱整理建议与每日/每周复盘草稿。

## 与 Codex Board 的关系

两者是独立产品。Research Workbench：

- 不修改或依赖 Codex Board；
- 不共享后端、数据模型、导航、产品名称或端口；
- 不读取 Codex Board 内部状态；
- 默认使用 `127.0.0.1:4280`，不会占用 `4173`；
- 未来最多提供普通网页链接。

## 快速开始

要求 Python 3.13 和 Node.js。PowerShell 中执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

cd frontend
npm ci
npm run build
cd ..

.\.venv\Scripts\research-workbench.exe serve --vault "H:\Obsidian Vault"
```

打开 [http://127.0.0.1:4280](http://127.0.0.1:4280)。FastAPI 直接提供 `frontend/dist`，正常使用不需要同时启动 Vite。开发时可单独运行 `npm run dev`（端口 `4281`，API 代理到 `4280`）。

生产端口可通过环境变量修改：

```powershell
$env:RESEARCH_WORKBENCH_PORT = "4290"
.\.venv\Scripts\research-workbench.exe serve --vault "H:\Obsidian Vault"
```

## CLI

启动完整应用：

```powershell
research-workbench serve --vault "H:\Obsidian Vault"
```

无界面只读检查：

```powershell
research-workbench check --vault "H:\Obsidian Vault"
research-workbench check --vault "H:\Obsidian Vault" --json
```

将报告导出到本项目 `reports/`：

```powershell
research-workbench export-report --vault "H:\Obsidian Vault"
```

`--data-dir` 可为测试或便携运行指定应用数据目录。常规运行默认使用 `%LOCALAPPDATA%\ResearchWorkbench`；也可设置 `RESEARCH_WORKBENCH_DATA_DIR`。

## 已实现工作流

- **今日工作台**：进行中项目与明确下一步、等待/阻塞、收件箱、待读文献、最近实验、最近知识和复盘提醒。
- **收件箱**：读取 `00_收件箱`，给出去向和关联建议，不移动或改写文件。
- **项目**：目标、状态、下一步、完成标准、领域、外部路径与来源标签。
- **文献**：collection、`reading_stage`、`reading_value`、Zotero/BibTeX key、全文覆盖记录和“需要我细看的地方”。第一阶段只读 Obsidian 文献笔记。
- **知识**：领域、概念、方法、双链/反链详情、未解析链接和同名歧义。
- **实验**：项目、环境、命令/入口、输出路径、证据、判断和下一步；不执行命令。
- **复盘**：每日或每周草稿预览，明确区分文件事实与软件推断，不写入 Vault。
- **集成与设置**：Vault/SQLite/报告路径、忽略目录、问题明细及未启用 adapter 的边界。

全局搜索覆盖标题、正文和相对路径；对象页支持按状态、领域、collection 和阅读阶段筛选。所有页面都有 loading、空状态和错误状态，顶部可重新扫描。

## 安全边界

| 数据源或动作 | 第一阶段行为 |
| --- | --- |
| Obsidian Vault | 只读 Markdown/frontmatter/标题/wiki links；零写入 |
| Zotero | 不查询数据库、不写入；只展示 Obsidian 已记录的 key |
| 外部工程目录 | 仅对记录的 Windows 路径做存在性检查 |
| WSL / SSH / EDA | adapter 边界预留，不执行 |
| SQLite / 缓存 | 写入应用数据目录，不进入 Vault |
| 扫描报告 | 只写入项目 `reports/` |
| 整理建议 / 复盘 | 应用内预览，可复制；不自动写回 |
| 密码 / API key / 云服务 | 不读取、不调用 |

应用启动时会拒绝把数据目录或 `reports/` 放在 Vault 内。扫描器只以只读模式打开 Markdown 文件。完整只读边界由测试覆盖。

## 架构

```text
Obsidian Vault（只读）
        │
        ▼
Markdown parser / link resolver / path checks
        │
        ▼
SQLite index（应用数据目录）
        │
        ▼
FastAPI domain queries ──► reports/（显式导出）
        │
        ▼
React + TypeScript production UI
```

后端是一个本地单体：

- `adapters/obsidian/`：frontmatter、标题、wiki links 与只读扫描；
- `indexing/`：扫描编排、领域视图、建议和复盘；
- `storage/`：SQLite 原子重建与查询；
- `domain/`：项目、文献和实验模型；
- `api/`：FastAPI 路由和前端静态服务；
- `adapters/zotero|projects|runners/`：未来能力的最小协议边界。

前端按页面与公共组件组织，不依赖 CDN 或外部在线服务。更详细说明见 [docs/architecture.md](docs/architecture.md)。

## 测试与构建

```powershell
.\.venv\Scripts\python.exe -m pytest

cd frontend
npm test
npm run build
```

测试覆盖 Markdown/frontmatter、wiki link/别名、中文 Windows 路径、领域模型、SQLite 重建、API、只读边界、fixture Vault 端到端、React 页面与筛选。真实 Vault 检查可使用 `check --json`，并应在扫描前后比较 Vault 指纹或 Git 状态。

## 项目结构

```text
backend/src/research_workbench/  Python 应用
backend/tests/                   后端与端到端测试
frontend/src/                    React 界面
frontend/src/test/               页面与筛选测试
fixtures/vault/                  测试 Vault
reports/                         显式导出的只读报告
docs/                            架构与边界说明
```
