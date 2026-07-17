import { useState } from "react";
import { queryString } from "../api";
import { useApi } from "../hooks";
import type { Facets, Note } from "../types";
import { Badge, FilterSelect, NoteRow, PageTitle, Section, StatePanel } from "../components/Ui";

interface KnowledgeResponse {
  items: Note[];
  diagnostics: { unresolved_links: number; ambiguous_links: number };
}

export function KnowledgePage({ refreshKey, onOpen }: { refreshKey: number; onOpen: (id: string) => void }) {
  const [domain, setDomain] = useState("");
  const [query, setQuery] = useState("");
  const facets = useApi<Facets>("/api/facets", refreshKey);
  const state = useApi<KnowledgeResponse>(`/api/knowledge${queryString({ domain, q: query })}`, refreshKey);
  const grouped = state.data?.items.reduce<Record<string, Note[]>>((result, note) => {
    (result[note.type] ||= []).push(note);
    return result;
  }, {}) ?? {};
  return (
    <>
      <PageTitle
        eyebrow="KNOWLEDGE"
        title="可复用知识"
        description="概念、方法和领域入口优先；图谱只是链接诊断的辅助，不替代实际工作流。"
      />
      <div className="filter-bar">
        <label className="filter-field grow"><span>搜索知识</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="概念、方法、领域或正文" /></label>
        <FilterSelect label="领域" value={domain} options={facets.data?.domains ?? []} onChange={setDomain} />
      </div>
      {state.loading && <StatePanel kind="loading" title="正在组织知识索引" />}
      {state.error && <StatePanel kind="error" title="知识加载失败" detail={state.error} />}
      {state.data && (
        <>
          <div className="diagnostic-band">
            <div><small>未解析双链</small><strong>{state.data.diagnostics.unresolved_links}</strong><span>需要检查目标是否存在</span></div>
            <div><small>同名歧义</small><strong>{state.data.diagnostics.ambiguous_links}</strong><span>需要补全路径或别名</span></div>
            <div className="diagnostic-copy"><Badge>软件检查</Badge><span>这些数量不是知识质量评分，只提示链接可解析性。</span></div>
          </div>
          {state.data.items.length === 0 ? <StatePanel kind="empty" title="没有匹配知识笔记" /> : (
            <div className="knowledge-columns">
              {(["area", "concept", "method"] as const).map((type) => (
                <Section key={type} title={type === "area" ? "领域入口" : type === "concept" ? "概念" : "方法"} hint={`${grouped[type]?.length ?? 0} 条`}>
                  <div className="compact-stack">
                    {(grouped[type] ?? []).map((note) => <NoteRow key={note.id} note={note} onOpen={onOpen} meta={note.domain ? <Badge>{note.domain}</Badge> : undefined} />)}
                  </div>
                </Section>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
