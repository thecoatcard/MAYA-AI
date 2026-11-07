import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GitDiffViewer } from "./GitDiffViewer";

describe("GitDiffViewer", () => {
  const sampleDiff = [
    "--- a/service.py",
    "+++ b/service.py",
    "@@ -1,3 +1,3 @@",
    " def process():",
    "-    return 'bad'",
    "+    return 'good'",
  ].join("\n");

  it("renders diff lines with addition and deletion counts", () => {
    const { container } = render(
      <GitDiffViewer diffText={sampleDiff} filePath="service.py" />,
    );

    expect(screen.getByText("service.py")).toBeDefined();
    const ones = screen.getAllByText("1");
    expect(ones.length).toBeGreaterThanOrEqual(2);
    expect(container.textContent).toContain("+    return 'good'");
    expect(container.textContent).toContain("-    return 'bad'");
  });
});
