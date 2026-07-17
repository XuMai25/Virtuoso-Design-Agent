import { useState } from "react";
import { queryString } from "../api";
import { useApi } from "../hooks";
import type { Experiment, Facets } from "../types";
import { Badge, FilterSelect, PageTitle, PathText, StatePanel, StatusBadge } from "../components/Ui";
import { Icon } from "../components/Icons";

export function ExperimentsPage({ refreshKey, onOpen }: { refreshKey: number; onOpen: (id: string) => void }) {
  const [status, setStatus] = useState("");
  const [domain, setDomain] = useState("");
  const [query, setQuery] = useState("");
  const facets = useApi<Facets>("/api/facets", refreshKey);
  const state = useApi<Experiment[]>(`/api/experiments${queryString({ status, domain, q: query })}`, refreshKey);
  return (
    <>
      <PageTitle
        eyebrow="EXPERIMENTS"
        title="实验与证据"
        description="保留环境、入口、输出、证据、判断和下一步；第一阶段不运行 WSL、SSH、仿真或 EDA 命令。"
      />
      <div className="filter-bar">
        <label className="filter-field grow"><span>筛选实验</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="实验、项目、命令或证据" /></label>
        <FilterSelect label="状态" value={status} options={facets.data?.statuses ?? []} onChange={setStatus} />
        <FilterSelect label="领域" value={domain} options={facets.data?.domains ?? []} onChange={setDomain} />
      </div>
      {state.loading && <StatePanel kind="loading" title="正在读取实验记录" />}
      {state.error && <StatePanel kind="error" title="实验加载失败" detail={state.error} />}
      {state.data?.length === 0 && <StatePanel kind="empty" title="没有匹配实验" />}
      {state.data && (
        <div className="experiment-list">
          {state.data.map((experiment) => (
            <article className="experiment-card" key={experiment.id}>
              <header><div><span className="object-kind">{experiment.project || experiment.domain || "未关联项目"}</span><h2>{experiment.title}</h2></div><StatusBadge value={experiment.status} /></header>
              <div className="experiment-flow">
                <div><small>环境 / 入口</small><strong>{experiment.environment || "未记录执行环境"}</strong><code>{experiment.command || "未记录命令或脚本入口"}</code></div>
                <Icon name="arrow" />
                <div><small>输出路径</small><PathText>{experiment.output_path}</PathText><StatusBadge value={experiment.output_path_status || "未记录"} /></div>
                <Icon name="arrow" />
                <div><small>判断 / 下一步</small><strong>{experiment.judgment || "未记录判断"}</strong><span>{experiment.next_step || "未记录下一步"}</span></div>
              </div>
              <div className="evidence-line"><Badge>证据</Badge><span>{experiment.evidence || "Vault 实验笔记未记录证据。"}</span></div>
              <footer><Badge>{experiment.source_label}</Badge><button type="button" onClick={() => onOpen(experiment.id)}>查看完整记录 <Icon name="arrow" /></button></footer>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
