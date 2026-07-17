import { useState } from "react";
import { api } from "../api";
import { useApi } from "../hooks";
import type { Issue, Summary } from "../types";
import { Badge, PageTitle, PathText, SeverityMark, StatePanel, StatusBadge } from "../components/Ui";
import { Icon } from "../components/Icons";

interface Settings {
  vault_path: string;
  vault_status: string;
  permission_mode: string;
  data_dir: string;
  database_path: string;
  reports_dir: string;
  ignored_directories: string[];
  zotero_adapter: { status: string; mode: string };
  runner_adapters: { status: string; mode: string };
  codex_board: { url: string | null; relationship: string };
}

export function SettingsPage({ refreshKey }: { refreshKey: number }) {
  const settings = useApi<Settings>("/api/settings", refreshKey);
  const summary = useApi<Summary>("/api/summary", refreshKey);
  const issues = useApi<Issue[]>("/api/issues?limit=80", refreshKey);
  const [reportState, setReportState] = useState<{ loading: boolean; path?: string; error?: string }>({ loading: false });

  async function exportReport() {
    setReportState({ loading: true });
    try {
      const result = await api<{ path: string }>("/api/reports", { method: "POST" });
      setReportState({ loading: false, path: result.path });
    } catch (error) {
      setReportState({ loading: false, error: error instanceof Error ? error.message : "导出失败" });
    }
  }

  return (
    <>
      <PageTitle
        eyebrow="INTEGRATIONS & SETTINGS"
        title="集成与设置"
        description="确认读取边界、索引位置和 adapter 状态。Research Workbench 不依赖 Codex Board。"
      />
      {settings.loading && <StatePanel kind="loading" title="正在读取本地设置" />}
      {settings.error && <StatePanel kind="error" title="设置加载失败" detail={settings.error} />}
      {settings.data && (
        <div className="settings-grid">
          <section className="settings-card span-two">
            <header><div><small>OBSIDIAN VAULT</small><h2>知识库连接</h2></div><StatusBadge value={settings.data.vault_status} /></header>
            <dl className="settings-list"><div><dt>Vault 路径</dt><dd><PathText>{settings.data.vault_path}</PathText></dd></div><div><dt>权限模式</dt><dd><Badge tone="success">{settings.data.permission_mode}</Badge></dd></div><div><dt>忽略目录</dt><dd>{settings.data.ignored_directories.map((item) => <code key={item}>{item}</code>)}</dd></div></dl>
          </section>
          <section className="settings-card">
            <header><div><small>LOCAL INDEX</small><h2>应用数据</h2></div><StatusBadge value={summary.data?.indexed ? "exists" : "未索引"} /></header>
            <dl className="settings-list"><div><dt>SQLite</dt><dd><PathText>{settings.data.database_path}</PathText></dd></div><div><dt>数据目录</dt><dd><PathText>{settings.data.data_dir}</PathText></dd></div><div><dt>最近扫描</dt><dd>{summary.data?.finished_at ? new Date(summary.data.finished_at).toLocaleString("zh-CN") : "尚未扫描"}</dd></div></dl>
          </section>
          <section className="settings-card">
            <header><div><small>REPORTS</small><h2>只读检查报告</h2></div></header>
            <p>报告只写入 Research Workbench 自己的 <PathText>{settings.data.reports_dir}</PathText>。</p>
            <button className="secondary-button" type="button" onClick={exportReport} disabled={reportState.loading}><Icon name="review" />{reportState.loading ? "导出中" : "导出当前报告"}</button>
            {reportState.path && <div className="operation-success"><Icon name="check" /><PathText>{reportState.path}</PathText></div>}
            {reportState.error && <p className="operation-error">{reportState.error}</p>}
          </section>
          <section className="settings-card">
            <header><div><small>ZOTERO</small><h2>文献 adapter</h2></div><StatusBadge value={settings.data.zotero_adapter.status} /></header>
            <p>{settings.data.zotero_adapter.mode}</p>
          </section>
          <section className="settings-card">
            <header><div><small>RUNNERS</small><h2>实验执行 adapter</h2></div><StatusBadge value={settings.data.runner_adapters.status} /></header>
            <p>{settings.data.runner_adapters.mode}</p>
          </section>
          <section className="settings-card span-two">
            <header><div><small>CODEX BOARD</small><h2>独立产品边界</h2></div><StatusBadge value="未连接" /></header>
            <p>{settings.data.codex_board.relationship}。不共享后端、端口、导航或数据模型，也不读取其内部状态。</p>
          </section>
        </div>
      )}

      <section className="content-section issue-section">
        <div className="section-heading"><h2>检查问题</h2><span>最多显示 80 条</span></div>
        {issues.loading && <StatePanel kind="loading" title="正在读取问题" />}
        {issues.error && <StatePanel kind="error" title="问题列表加载失败" detail={issues.error} />}
        {issues.data?.length === 0 && <StatePanel kind="empty" title="当前没有检查问题" />}
        {issues.data && issues.data.length > 0 && <div className="issue-table">{issues.data.map((issue, index) => (
          <div className="issue-table-row" key={`${issue.code}-${index}`}><SeverityMark severity={issue.severity} /><code>{issue.code}</code><span>{issue.message}</span><PathText>{issue.path}</PathText></div>
        ))}</div>}
      </section>
    </>
  );
}
