import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ProjectsPage } from "../pages/ProjectsPage";

it("sends project text and status filters to the API", async () => {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const data = url.startsWith("/api/facets")
      ? { types: [], statuses: ["进行中", "阻塞"], domains: ["3DIC"], reading_stages: [], collections: [] }
      : [];
    return Promise.resolve(new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<ProjectsPage refreshKey={0} onOpen={vi.fn()} />);
  await user.type(screen.getByPlaceholderText("标题、正文或路径"), "NPU");
  await user.selectOptions(screen.getByLabelText("状态"), "阻塞");
  await waitFor(() => {
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes("q=NPU") && url.includes("status=%E9%98%BB%E5%A1%9E"))).toBe(true);
  });
});
