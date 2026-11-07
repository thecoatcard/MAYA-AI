import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { AgentFleetView } from "./AgentFleetView";

describe("AgentFleetView", () => {
  it("renders agent fleet overview, sandbox isolation banner, and 6 specialist agents", () => {
    render(<AgentFleetView />);

    expect(screen.getByText("Specialist AI Agent Fleet")).toBeInTheDocument();
    expect(
      screen.getByText("Fixed-Function Sandbox Isolation (Decision D14)"),
    ).toBeInTheDocument();
    expect(screen.getByText("6 Agents Online")).toBeInTheDocument();

    // Verify all 6 specialist agents are present
    expect(screen.getByText("Orchestrator Agent")).toBeInTheDocument();
    expect(screen.getByText("Research Agent")).toBeInTheDocument();
    expect(screen.getByText("Coding Agent")).toBeInTheDocument();
    expect(screen.getByText("Document Agent")).toBeInTheDocument();
    expect(screen.getByText("Email Agent")).toBeInTheDocument();
    expect(screen.getByText("PM Agent")).toBeInTheDocument();

    // Verify model routing highlights gemini-3.8-flash
    expect(
      screen.getByText("gemini-3.8-flash (orchestrator)"),
    ).toBeInTheDocument();
    expect(screen.getByText("gemini-3.8-flash (research)")).toBeInTheDocument();
    expect(screen.getByText("gemini-3.8-flash (heavy)")).toBeInTheDocument();
  });

  it("filters agents by permission ceiling", () => {
    render(<AgentFleetView />);

    // Filter by Read-Only
    fireEvent.click(screen.getByRole("button", { name: "Read-Only" }));

    expect(screen.getByText("Research Agent")).toBeInTheDocument();
    expect(screen.getByText("PM Agent")).toBeInTheDocument();
    expect(screen.queryByText("Coding Agent")).not.toBeInTheDocument();

    // Filter by Approval Required
    fireEvent.click(screen.getByRole("button", { name: "Approval Required" }));
    expect(screen.getByText("Orchestrator Agent")).toBeInTheDocument();
    expect(screen.getByText("Coding Agent")).toBeInTheDocument();
    expect(screen.getByText("Email Agent")).toBeInTheDocument();
    expect(screen.queryByText("Research Agent")).not.toBeInTheDocument();
  });

  it("filters agents using search input", () => {
    render(<AgentFleetView />);

    const searchInput = screen.getByPlaceholderText(
      /Search agents, tools, domain roles/i,
    );
    fireEvent.change(searchInput, { target: { value: "WeasyPrint" } });

    expect(screen.getByText("Document Agent")).toBeInTheDocument();
    expect(screen.queryByText("Research Agent")).not.toBeInTheDocument();
    expect(screen.queryByText("Coding Agent")).not.toBeInTheDocument();
  });

  it("opens modal on card click to inspect architecture and guardrails", () => {
    render(<AgentFleetView />);

    fireEvent.click(screen.getByText("Research Agent"));

    expect(
      screen.getByText("System Instruction & Guardrails Policy"),
    ).toBeInTheDocument();
    expect(screen.getByText("Sandbox Isolation Profile")).toBeInTheDocument();
    expect(screen.getByText("1,000,000 tokens")).toBeInTheDocument();

    // Close inspector
    fireEvent.click(screen.getByText("Close Inspector"));
    expect(
      screen.queryByText("System Instruction & Guardrails Policy"),
    ).not.toBeInTheDocument();
  });
});
