import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";

const summary = {
  indexed: true,
  vault_path: "H:\\Obsidian Vault",
  markdown_files: 42,
  finished_at: "2026-07-18T01:00:00Z",
  error: 0,
  warning: 2,
  info: 3,
};

const note = {
  id: "project-1",
  path: "02_项目/光互连.md",
  title: "光互连",
  type: "project",
  status: "进行中",
  domain: "MicroLED 光互连",
  modified_at: "2026-07-18T01:00:00Z",
  excerpt: "项目摘要",
  properties: {},
  headings: [],
  aliases: [],
};

function response(data: unknown): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(data), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

beforeEach(() => {
  window.location.hash = "#/today";
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/summary")) return response(summary);
    if (url.startsWith("/api/dashboard")) return response({
      summary,
      active_projects: [note],
      waiting_or_blocked: [],
      recent_notes: [note],
      inbox: [],
      reading_queue: [],
      recent_experiments: [],
      review_items: [],
      next_actions: [{ project_id: "project-1", project: "光互连", status: "进行中", next_step: "完成链路预算", source: "Vault 记录" }],
    });
    if (url.startsWith("/api/facets")) return response({ types: [], statuses: ["进行中"], domains: ["MicroLED 光互连"], reading_stages: [], collections: [] });
    if (url.startsWith("/api/projects")) return response([{ ...note, goal: "完成原型", next_step: "完成链路预算", completion_criteria: "通过测试", source_label: "Vault 记录" }]);
    if (url.startsWith("/api/notes")) return response([]);
    return response({});
  }));
});

describe("Research Workbench shell", () => {
  it("prioritizes concrete next actions on the default page", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "今天继续什么？" })).toBeInTheDocument();
    expect(await screen.findByText("完成链路预算")).toBeInTheDocument();
    expect(screen.getByText("来源：Vault 记录")).toBeInTheDocument();
    expect(screen.getByText("Vault 只读")).toBeInTheDocument();
  });

  it("navigates to the project workflow page", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: /^项目$/ }));
    expect(await screen.findByRole("heading", { name: "项目推进" })).toBeInTheDocument();
    expect(await screen.findByText("完成原型")).toBeInTheDocument();
    expect(screen.getAllByText("Vault 记录").length).toBeGreaterThan(0);
  });
});
