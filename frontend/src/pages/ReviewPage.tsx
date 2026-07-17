import { useState } from "react";
import { useApi } from "../hooks";
import { Badge, PageTitle, StatePanel } from "../components/Ui";
import { Icon } from "../components/Icons";

interface ReviewDraft {
  markdown: string;
  generated_at: string;
  source_window_days: number;
  recent_count: number;
  write_action: string;
}

export function ReviewPage({ refreshKey }: { refreshKey: number }) {
  const [period, setPeriod] = useState<"daily" | "weekly">("weekly");
  const [copied, setCopied] = useState(false);
  const state = useApi<ReviewDraft>(`/api/reviews/${period}`, refreshKey);

  async function copyDraft() {
    if (!state.data) return;
    await navigator.clipboard.writeText(state.data.markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  return (
    <>
      <PageTitle
        eyebrow="REVIEW"
        title="周期复盘"
        description="根据最近修改和当前项目属性生成草稿。内容先在应用中预览，不自动写入 Vault。"
        actions={
          <div className="segmented-control" aria-label="复盘周期">
            <button type="button" className={period === "daily" ? "active" : ""} onClick={() => setPeriod("daily")}>每日</button>
            <button type="button" className={period === "weekly" ? "active" : ""} onClick={() => setPeriod("weekly")}>每周</button>
          </div>
        }
      />
      {state.loading && <StatePanel kind="loading" title="正在生成复盘草稿" />}
      {state.error && <StatePanel kind="error" title="复盘草稿生成失败" detail={state.error} />}
      {state.data && (
        <div className="review-workspace">
          <div className="review-toolbar">
            <div><Badge>{state.data.source_window_days} 天窗口</Badge><span>检测到 {state.data.recent_count} 条最近修改</span><span>{state.data.write_action}</span></div>
            <button className="secondary-button" type="button" onClick={copyDraft}><Icon name={copied ? "check" : "review"} />{copied ? "已复制" : "复制草稿"}</button>
          </div>
          <pre className="review-preview">{state.data.markdown}</pre>
          <aside className="review-guidance">
            <h3>确认后再使用</h3>
            <p>文件修改时间只能说明“最近动过”，不能证明项目真正推进。请人工核对状态变化、证据和下周期重点。</p>
            <ul><li>Vault 事实：frontmatter、正文和文件时间</li><li>软件推断：长期未更新、候选重点</li><li>未执行：写入日记、修改项目或同步 Zotero</li></ul>
          </aside>
        </div>
      )}
    </>
  );
}
