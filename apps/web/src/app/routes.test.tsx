import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentsRedirect from "./agents/page";
import ApprovalsRedirect from "./approvals/page";
import ChatRedirect from "./chat/page";
import DeliverablesRedirect from "./deliverables/page";
import IntegrationsRedirect from "./integrations/page";
import WorkspaceRedirect from "./workspace/page";
import { TasksRoute } from "./(app)/tasks/TasksRoute";

const mockReplace = vi.fn();
let mockSearch = "";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(mockSearch),
}));
vi.mock("../components/tasks/TaskCenterView", () => ({
  TaskCenterView: ({ initialTaskId }: { initialTaskId?: string | null }) => (
    <div data-testid="task-center">{initialTaskId ?? "no-task"}</div>
  ),
}));

beforeEach(() => {
  mockReplace.mockClear();
  mockSearch = "";
  window.history.replaceState({}, "", "/");
});

describe("legacy redirects", () => {
  it.each([
    ["/chat", ChatRedirect, "/"],
    ["/workspace", WorkspaceRedirect, "/"],
    ["/deliverables", DeliverablesRedirect, "/artifacts"],
    ["/integrations", IntegrationsRedirect, "/connections"],
    ["/agents", AgentsRedirect, "/settings"],
    ["/approvals", ApprovalsRedirect, "/tasks"],
  ])("%s goes to %s", async (_from, Page, to) => {
    render(<Page />);
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith(to));
    expect(screen.getByRole("link", { name: "Continue" })).toHaveAttribute(
      "href",
      to,
    );
  });

  it("keeps the query string", async () => {
    window.history.replaceState({}, "", "/integrations?x=1");
    render(<IntegrationsRedirect />);
    await waitFor(() =>
      expect(mockReplace).toHaveBeenCalledWith("/connections?x=1"),
    );
  });
});

describe("/approvals (removed section)", () => {
  it("keeps the task link of an old deep link and opens Tasks", async () => {
    window.history.replaceState({}, "", "/approvals?task_id=tsk-9");
    render(<ApprovalsRedirect />);
    await waitFor(() =>
      expect(mockReplace).toHaveBeenCalledWith("/tasks?task_id=tsk-9"),
    );
  });
});

describe("/tasks deep link", () => {
  it("passes ?task_id= to the task center", () => {
    mockSearch = "task_id=tsk-42";
    render(<TasksRoute />);
    expect(screen.getByTestId("task-center")).toHaveTextContent("tsk-42");
  });

  it("opens with no selection when there is no task_id", () => {
    render(<TasksRoute />);
    expect(screen.getByTestId("task-center")).toHaveTextContent("no-task");
  });
});

describe("design tokens in globals.css", () => {
  const css = readFileSync(join(__dirname, "globals.css"), "utf8");
  const required = [
    "--maya-bg",
    "--maya-surface",
    "--maya-raised",
    "--maya-border",
    "--maya-text",
    "--maya-text-muted",
    "--maya-accent",
    "--maya-accent-text",
    "--maya-focus",
    "--maya-danger",
    "--maya-warning",
    "--maya-success",
    "--maya-info",
  ];

  function block(start: RegExp): string {
    const match = start.exec(css);
    if (match === null) throw new Error(`block not found: ${start}`);
    const open = css.indexOf("{", match.index);
    let depth = 0;
    for (let i = open; i < css.length; i += 1) {
      if (css[i] === "{") depth += 1;
      if (css[i] === "}") depth -= 1;
      if (depth === 0) return css.slice(open, i);
    }
    throw new Error("unbalanced css");
  }

  it("defines every required role in the default (dark) theme on :root", () => {
    const dark = block(/^:root\s*\{/m);
    for (const token of required) expect(dark).toContain(`${token}:`);
  });

  it("redefines every required role for data-theme=light", () => {
    const light = block(/^:root\[data-theme="light"\]/m);
    // --maya-surface is an alias of --maya-pane, so it is declared once (dark block) only.
    for (const token of required.filter((t) => t !== "--maya-surface"))
      expect(light).toContain(`${token}:`);
  });

  it("has only two themes: no system mode and no prefers-color-scheme logic", () => {
    expect(css).not.toContain("prefers-color-scheme");
    expect(css).not.toContain("data-maya-theme");
  });

  it("maps the tokens into Tailwind through @theme inline", () => {
    const theme = block(/@theme inline/);
    expect(theme).toContain("--color-maya-pane: var(--maya-pane)");
    expect(theme).toContain("--color-maya-accent: var(--maya-accent)");
  });

  it("respects reduced motion and has no blur or glow", () => {
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).not.toMatch(/backdrop-filter|[^-]blur\(|box-shadow:\s*0 0 /);
  });
});
