import { useState } from "react";
import { queryString } from "../api";
import { useApi } from "../hooks";
import type { Facets, Literature } from "../types";
import { Badge, FilterSelect, PageTitle, PathText, StatePanel, StatusBadge } from "../components/Ui";
import { Icon } from "../components/Icons";

export function LiteraturePage({ refreshKey, onOpen }: { refreshKey: number; onOpen: (id: string) => void }) {
  const [collection, setCollection] = useState("");
  const [stage, setStage] = useState("");
  const [domain, setDomain] = useState("");
  const [query, setQuery] = useState("");
  const facets = useApi<Facets>("/api/facets", refreshKey);
  const state = useApi<Literature[]>(
    `/api/literature${queryString({ collection, reading_stage: stage, domain, q: query })}`,
    refreshKey,
  );
  return (
    <>
      <PageTitle
        eyebrow="LITERATURE"
        title="文献阅读"
        description="围绕 collection、阅读阶段、价值和精读入口组织；题录事实仍以 Zotero 为准。"
      />
      <div className="boundary-note"><Icon name="literature" /><span>第一阶段只读取 Obsidian 文献笔记，<strong>未查询或写入 Zotero</strong>。</span></div>
      <div className="filter-bar literature-filters">
        <label className="filter-field grow"><span>筛选文献</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="长标题、正文或引用键" /></label>
        <FilterSelect label="Collection" value={collection} options={facets.data?.collections ?? []} onChange={setCollection} />
        <FilterSelect label="阅读阶段" value={stage} options={facets.data?.reading_stages ?? []} onChange={setStage} />
        <FilterSelect label="领域" value={domain} options={facets.data?.domains ?? []} onChange={setDomain} />
      </div>
      {state.loading && <StatePanel kind="loading" title="正在读取文献索引" />}
      {state.error && <StatePanel kind="error" title="文献加载失败" detail={state.error} />}
      {state.data?.length === 0 && <StatePanel kind="empty" title="没有匹配文献" />}
      {state.data && state.data.length > 0 && (
        <div className="literature-table" role="table" aria-label="文献列表">
          <div className="literature-head" role="row"><span>文献</span><span>阅读状态</span><span>事实键</span><span>精读入口</span></div>
          {state.data.map((paper) => (
            <button type="button" className="literature-row" role="row" key={paper.id} onClick={() => onOpen(paper.id)}>
              <span className="paper-main"><strong>{paper.title}</strong><small>{paper.collection || "未映射 collection"} · {paper.domain || "未记录领域"}</small><PathText>{paper.path}</PathText></span>
              <span className="paper-state"><StatusBadge value={paper.reading_stage} />{paper.reading_value && <Badge>{paper.reading_value}</Badge>}<small>{paper.fulltext_coverage || "全文覆盖未记录"}</small></span>
              <span className="paper-keys"><code>{paper.zotero_key || "无 Zotero key"}</code><code>{paper.bibtex_key || "无 BibTeX key"}</code></span>
              <span className="paper-close-read"><span>{paper.needs_close_reading || "未记录需要细看的位置"}</span><Icon name="arrow" /></span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}
