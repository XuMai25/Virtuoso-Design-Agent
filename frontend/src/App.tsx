import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { Layout } from "./components/Layout";
import { NoteDrawer } from "./components/NoteDrawer";
import { useApi } from "./hooks";
import { ExperimentsPage } from "./pages/ExperimentsPage";
import { InboxPage } from "./pages/InboxPage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { LiteraturePage } from "./pages/LiteraturePage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TodayPage } from "./pages/TodayPage";
import type { PageKey, Summary } from "./types";

const pages = new Set<PageKey>([
  "today",
  "inbox",
  "projects",
  "literature",
  "knowledge",
  "experiments",
  "review",
  "settings",
]);

function pageFromHash(): PageKey {
  const candidate = window.location.hash.replace(/^#\/?/, "") as PageKey;
  return pages.has(candidate) ? candidate : "today";
}

function initialTheme(): "light" | "dark" {
  const stored = window.localStorage.getItem("research-workbench-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export default function App() {
  const [page, setPage] = useState<PageKey>(pageFromHash);
  const [theme, setTheme] = useState<"light" | "dark">(initialTheme);
  const [refreshKey, setRefreshKey] = useState(0);
  const [scanning, setScanning] = useState(false);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [noteId, setNoteId] = useState<string | null>(null);
  const summary = useApi<Summary>("/api/summary", refreshKey);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("research-workbench-theme", theme);
  }, [theme]);

  useEffect(() => {
    const handleHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", handleHash);
    return () => window.removeEventListener("hashchange", handleHash);
  }, []);

  const navigate = useCallback((next: PageKey) => {
    window.location.hash = `/${next}`;
    setPage(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const runScan = useCallback(async () => {
    setScanning(true);
    setScanMessage(null);
    try {
      const result = await api<Summary>("/api/scan", { method: "POST" });
      setRefreshKey((value) => value + 1);
      setScanMessage(`扫描完成：${result.markdown_files ?? 0} 个 Markdown`);
    } catch (error) {
      setScanMessage(error instanceof Error ? error.message : "扫描失败");
    } finally {
      setScanning(false);
      window.setTimeout(() => setScanMessage(null), 3500);
    }
  }, []);

  const content = useMemo(() => {
    const common = { refreshKey, onOpen: setNoteId };
    switch (page) {
      case "inbox": return <InboxPage {...common} />;
      case "projects": return <ProjectsPage {...common} />;
      case "literature": return <LiteraturePage {...common} />;
      case "knowledge": return <KnowledgePage {...common} />;
      case "experiments": return <ExperimentsPage {...common} />;
      case "review": return <ReviewPage refreshKey={refreshKey} />;
      case "settings": return <SettingsPage refreshKey={refreshKey} />;
      default: return <TodayPage {...common} />;
    }
  }, [page, refreshKey]);

  return (
    <>
      <Layout
        page={page}
        onNavigate={navigate}
        theme={theme}
        onToggleTheme={() => setTheme((value) => value === "dark" ? "light" : "dark")}
        summary={summary.data}
        scanning={scanning}
        onScan={runScan}
        refreshKey={refreshKey}
        onOpenNote={setNoteId}
      >
        {content}
      </Layout>
      <NoteDrawer noteId={noteId} onClose={() => setNoteId(null)} />
      {scanMessage && <div className="toast" role="status">{scanMessage}</div>}
    </>
  );
}
