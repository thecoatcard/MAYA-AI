"use client";

import {
  ArrowRight,
  Bot,
  Box,
  Clock,
  Code,
  Cpu,
  FileText,
  Filter,
  Mail,
  Search,
  Shield,
  Terminal,
  X,
} from "lucide-react";
import React, { useMemo, useState } from "react";

export interface AgentSpec {
  readonly id: string;
  readonly name: string;
  readonly role: string;
  readonly modelAlias: string;
  readonly modelArchitecture: string;
  readonly ceiling: "READ" | "WRITE" | "APPROVAL_REQUIRED";
  readonly description: string;
  readonly systemPromptSummary: string;
  readonly contextWindow: string;
  readonly tools: readonly string[];
  readonly status: "Active" | "Standby" | "Gated";
  readonly statusColor: string;
  readonly icon: React.ReactNode;
  readonly sandboxTier: string;
  readonly memoryCap: string;
  readonly timeoutSeconds: number;
}

const SPECIALIST_FLEET: readonly AgentSpec[] = [
  {
    id: "orchestrator",
    name: "Orchestrator Agent",
    role: "Supervisor & Planner",
    modelAlias: "gemini-3.8-flash (orchestrator)",
    modelArchitecture: "Gemini 3.8 Flash (Structured Function Calling)",
    ceiling: "APPROVAL_REQUIRED",
    description:
      "Decomposes user instructions into execution DAG plans, performs acyclic cycle checks, validates task budgets, and supervises specialist delegations.",
    systemPromptSummary:
      "Acts as LangGraph supervisor. Never executes mutations directly; validates acyclic graph topology, schedules subtasks with predecessor links, and triggers saga compensations on abort.",
    contextWindow: "1,000,000 tokens",
    tools: [
      "dag_planner",
      "subtask_delegate",
      "budget_tracker",
      "saga_compensator",
    ],
    status: "Active",
    statusColor:
      "text-emerald-300 bg-emerald-500/15 border-emerald-500/30 shadow-[0_0_10px_rgba(16,185,129,0.2)]",
    icon: <Cpu className="w-5 h-5 text-teal-400" />,
    sandboxTier: "Host Orchestrator (Rootless)",
    memoryCap: "1024 MB",
    timeoutSeconds: 120,
  },
  {
    id: "research",
    name: "Research Agent",
    role: "Autonomous Investigation",
    modelAlias: "gemini-3.8-flash (research)",
    modelArchitecture: "Gemini 3.8 Flash (High-Throughput Reasoning)",
    ceiling: "READ",
    description:
      "Executes deep market, technical, and competitive research queries using Brave Search. Emits structured citations with verbatim snippets and source URLs.",
    systemPromptSummary:
      "Strict READ-only ceiling. Must ground all factual statements in Brave Search primary citations. Strips affiliate URLs and emits canonical quote anchors.",
    contextWindow: "1,000,000 tokens",
    tools: ["brave_web_search", "fetch_web_page", "file_read"],
    status: "Active",
    statusColor:
      "text-cyan-300 bg-cyan-500/15 border-cyan-500/30 shadow-[0_0_10px_rgba(6,182,212,0.2)]",
    icon: <Search className="w-5 h-5 text-cyan-400" />,
    sandboxTier: "Containerized Sandbox (Decision D14)",
    memoryCap: "512 MB",
    timeoutSeconds: 60,
  },
  {
    id: "coding",
    name: "Coding Agent",
    role: "Autonomous Bug Fix & Engineering",
    modelAlias: "gemini-3.8-flash (heavy)",
    modelArchitecture: "Gemini 3.8 Flash (Code Intelligence)",
    ceiling: "APPROVAL_REQUIRED",
    description:
      "Executes Workflow 3: reproduces failing tests in Docker sandbox, develops patches, verifies exit code 0, commits locally, and gates PR creation behind approval tokens.",
    systemPromptSummary:
      "Runs under non-root UID 10001:10001. Requires human approval token before GitHub pull requests are created or remote pushes are executed.",
    contextWindow: "1,000,000 tokens",
    tools: [
      "sandbox_runner",
      "git_status",
      "git_diff",
      "git_commit_local",
      "github_create_pull_request",
    ],
    status: "Active",
    statusColor:
      "text-emerald-300 bg-emerald-500/15 border-emerald-500/30 shadow-[0_0_10px_rgba(16,185,129,0.2)]",
    icon: <Code className="w-5 h-5 text-emerald-400" />,
    sandboxTier: "Fixed-Function Sandbox Isolation (D14)",
    memoryCap: "512 MB",
    timeoutSeconds: 60,
  },
  {
    id: "document",
    name: "Document Agent",
    role: "Artifact Generation",
    modelAlias: "gemini-3.8-flash (document)",
    modelArchitecture: "Gemini 3.8 Flash (Multi-Modal Document Layout)",
    ceiling: "WRITE",
    description:
      "Generates verified PDFs via WeasyPrint, financial spreadsheets via OpenPyXL, Word documents, and slide presentations. Records cryptographic SHA-256 checksums in the ledger.",
    systemPromptSummary:
      "Authorized for local WRITE operations into ephemeral artifact volumes. Emits verifiable SHA-256 checksum digests for every compiled binary.",
    contextWindow: "1,000,000 tokens",
    tools: ["generate_pdf", "generate_xlsx", "generate_docx", "generate_pptx"],
    status: "Active",
    statusColor:
      "text-purple-300 bg-purple-500/15 border-purple-500/30 shadow-[0_0_10px_rgba(168,85,247,0.2)]",
    icon: <FileText className="w-5 h-5 text-purple-400" />,
    sandboxTier: "WeasyPrint Isolated Container",
    memoryCap: "512 MB",
    timeoutSeconds: 60,
  },
  {
    id: "email",
    name: "Email Agent",
    role: "Communications & Triage",
    modelAlias: "gemini-3.8-flash (lite)",
    modelArchitecture: "Gemini 3.8 Flash (Natural Language Synthesis)",
    ceiling: "APPROVAL_REQUIRED",
    description:
      "Reads and triages inbox threads autonomously, drafts structured replies, and pauses execution with a draft preview until human approval authorizes dispatch.",
    systemPromptSummary:
      "Under no circumstances may email_send be dispatched without an authorized single-use approval token. Emits RFC 8785 draft previews.",
    contextWindow: "1,000,000 tokens",
    tools: [
      "gmail_search_emails",
      "gmail_read_thread",
      "gmail_create_draft",
      "gmail_send_email",
    ],
    status: "Active",
    statusColor:
      "text-blue-300 bg-blue-500/15 border-blue-500/30 shadow-[0_0_10px_rgba(59,130,246,0.2)]",
    icon: <Mail className="w-5 h-5 text-blue-400" />,
    sandboxTier: "OAuth2 Delegated Sandbox",
    memoryCap: "256 MB",
    timeoutSeconds: 30,
  },
  {
    id: "pm",
    name: "PM Agent",
    role: "Project Management & Briefings",
    modelAlias: "gemini-3.8-flash (pm)",
    modelArchitecture: "Gemini 3.8 Flash (Summarization & Synthesis)",
    ceiling: "READ",
    description:
      "Executes Workflow 1 (Daily Briefing): queries Google Calendar events and Notion sprint databases under strict read-only ceiling to synthesize daily executive summaries.",
    systemPromptSummary:
      "Aggregates daily schedule and Notion task database. Read-only permissions enforced via tenant IAM scopes.",
    contextWindow: "1,000,000 tokens",
    tools: [
      "google_calendar_list",
      "google_calendar_get",
      "notion_query_database",
      "notion_get_page",
    ],
    status: "Active",
    statusColor:
      "text-amber-300 bg-amber-500/15 border-amber-500/30 shadow-[0_0_10px_rgba(245,158,11,0.2)]",
    icon: <Clock className="w-5 h-5 text-amber-400" />,
    sandboxTier: "Read-Only API Gateway",
    memoryCap: "256 MB",
    timeoutSeconds: 30,
  },
];

export function AgentFleetView(): React.JSX.Element {
  const [filterCeiling, setFilterCeiling] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [selectedAgent, setSelectedAgent] = useState<AgentSpec | null>(null);

  const filteredAgents = useMemo(() => {
    return SPECIALIST_FLEET.filter((agent) => {
      const matchesCeiling =
        filterCeiling === "ALL" || agent.ceiling === filterCeiling;
      const q = searchQuery.toLowerCase().trim();
      const matchesSearch =
        !q ||
        agent.name.toLowerCase().includes(q) ||
        agent.role.toLowerCase().includes(q) ||
        agent.description.toLowerCase().includes(q) ||
        agent.tools.some((t) => t.toLowerCase().includes(q));
      return matchesCeiling && matchesSearch;
    });
  }, [filterCeiling, searchQuery]);

  return (
    <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-6 max-w-6xl mx-auto w-full text-zinc-100">
      {/* Fleet Header (Glass Surface) */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-5 p-6 rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl shadow-xl">
        <div className="flex items-start gap-4">
          <div className="p-3 rounded-2xl bg-teal-500/15 border border-teal-500/30 text-teal-400 shadow-[0_0_20px_rgba(20,184,166,0.25)]">
            <Bot className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-lg font-bold text-zinc-100 tracking-tight">
                Specialist AI Agent Fleet
              </h2>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-teal-500/10 text-teal-300 border border-teal-500/20 font-semibold">
                Autonomous Cluster
              </span>
            </div>
            <p className="text-xs text-zinc-400 mt-1 max-w-2xl leading-relaxed">
              MAYA delegates tasks to specialized agents governed by immutable
              permission ceilings, strictly assigned tools, and fixed-function
              sandbox runners (Phase 09).
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-xl border border-white/10 bg-zinc-950/60 text-xs font-mono">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(16,185,129,0.8)] animate-pulse" />
            <span className="font-bold text-zinc-200">6 Agents Online</span>
          </div>
        </div>
      </div>

      {/* Sandbox Isolation Specifications Card (Decision D14) */}
      <div className="p-5 rounded-2xl border border-white/10 bg-zinc-900/30 backdrop-blur-xl flex flex-col md:flex-row items-start md:items-center justify-between gap-4 shadow-lg">
        <div className="flex items-center gap-3.5">
          <div className="p-2.5 rounded-xl bg-teal-500/10 text-teal-400 border border-teal-500/20 shadow-xs">
            <Box className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-bold text-zinc-200 tracking-wide uppercase">
                Fixed-Function Sandbox Isolation (Decision D14)
              </h3>
              <span className="text-[10px] font-mono px-2 py-0.2 rounded-full bg-zinc-800 text-teal-300 border border-zinc-700">
                Active Policy
              </span>
            </div>
            <p className="text-[11px] text-zinc-400 mt-1 font-mono">
              Non-root UID 10001:10001 • 512 MB Memory Cap • 60s Timeout •
              Docker Socket Prohibited
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 text-[11px] font-mono text-zinc-300">
          <span className="px-2.5 py-1 rounded-lg bg-zinc-950/70 border border-white/5">
            Output Cap: 1 MB
          </span>
          <span className="px-2.5 py-1 rounded-lg bg-zinc-950/70 border border-white/5">
            Ephemeral Cleanups
          </span>
        </div>
      </div>

      {/* Search & Ceiling Filter Controls */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 p-2">
        <div className="relative w-full sm:w-72">
          <Search className="w-3.5 h-3.5 absolute left-3 top-3 text-zinc-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search agents, tools, domain roles…"
            className="w-full pl-9 pr-3 py-2 rounded-xl border border-white/10 bg-zinc-900/50 backdrop-blur-md text-xs text-zinc-200 placeholder:text-zinc-500 focus:outline-none focus:border-teal-400/60 focus:ring-1 focus:ring-teal-400/20 font-medium"
          />
        </div>

        {/* Ceiling Filter Tabs */}
        <div className="flex items-center gap-1.5 self-start sm:self-auto">
          <span className="text-[11px] font-mono text-zinc-500 mr-1 flex items-center gap-1">
            <Filter className="w-3 h-3" />
            <span>Ceiling:</span>
          </span>
          {[
            { id: "ALL", label: "All" },
            { id: "READ", label: "Read-Only" },
            { id: "WRITE", label: "Write" },
            { id: "APPROVAL_REQUIRED", label: "Approval Required" },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setFilterCeiling(tab.id)}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                filterCeiling === tab.id
                  ? "bg-teal-500/20 text-teal-300 border border-teal-500/30 shadow-[0_0_10px_rgba(20,184,166,0.2)] font-semibold"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40 border border-transparent"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Agents Roster Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
        {filteredAgents.map((agent) => (
          <div
            key={agent.id}
            onClick={() => setSelectedAgent(agent)}
            className="flex flex-col justify-between p-5 rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl hover:bg-zinc-900/70 hover:border-teal-400/40 transition-all duration-200 shadow-xl cursor-pointer group relative overflow-hidden"
          >
            {/* Top edge glow on hover */}
            <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-teal-400/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />

            <div className="flex flex-col gap-3.5">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2.5 rounded-xl bg-zinc-800/90 border border-white/10 shadow-inner">
                    {agent.icon}
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-zinc-100 tracking-tight group-hover:text-teal-300 transition-colors">
                      {agent.name}
                    </h3>
                    <span className="text-[11px] text-zinc-400 font-mono font-medium">
                      {agent.role}
                    </span>
                  </div>
                </div>
                <span
                  className={`text-[10px] font-mono px-2.5 py-0.5 rounded-full border font-bold ${agent.statusColor}`}
                >
                  {agent.status}
                </span>
              </div>

              <p className="text-xs text-zinc-300 leading-relaxed font-medium">
                {agent.description}
              </p>

              {/* Technical Specifications */}
              <div className="flex flex-col gap-2 pt-3 border-t border-white/5 text-xs">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-zinc-500 font-mono flex items-center gap-1">
                    <Shield className="w-3 h-3 text-zinc-500" />
                    <span>Ceiling:</span>
                  </span>
                  <span
                    className={`font-mono font-bold px-2 py-0.5 rounded-md border text-[10px] ${
                      agent.ceiling === "READ"
                        ? "bg-zinc-800/80 border-zinc-700/60 text-zinc-300"
                        : agent.ceiling === "WRITE"
                          ? "bg-blue-950/60 border-blue-500/40 text-blue-300 shadow-[0_0_8px_rgba(59,130,246,0.2)]"
                          : "bg-amber-950/60 border-amber-500/40 text-amber-300 shadow-[0_0_8px_rgba(245,158,11,0.2)]"
                    }`}
                  >
                    {agent.ceiling}
                  </span>
                </div>

                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-zinc-500 font-mono flex items-center gap-1">
                    <Cpu className="w-3 h-3 text-zinc-500" />
                    <span>Model Routing:</span>
                  </span>
                  <span className="font-mono text-teal-300 font-bold text-[10px] px-1.5 py-0.5 rounded bg-teal-950/40 border border-teal-500/30">
                    {agent.modelAlias}
                  </span>
                </div>

                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-zinc-500 font-mono flex items-center gap-1">
                    <Box className="w-3 h-3 text-zinc-500" />
                    <span>Sandbox Cap:</span>
                  </span>
                  <span className="font-mono text-zinc-400 text-[10px]">
                    {agent.memoryCap} • {agent.timeoutSeconds}s
                  </span>
                </div>
              </div>

              {/* Assigned Tools Allowlist */}
              <div className="flex flex-col gap-1.5 pt-2">
                <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                  Assigned Tools Allowlist ({agent.tools.length})
                </span>
                <div className="flex flex-wrap gap-1">
                  {agent.tools.map((t) => (
                    <span
                      key={t}
                      className="px-2 py-0.5 rounded-md bg-zinc-950/80 border border-white/5 text-zinc-300 font-mono text-[10px]"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="pt-3 mt-3 border-t border-white/5 flex items-center justify-between text-[11px] text-zinc-500">
              <span className="group-hover:text-teal-400 transition-colors">
                Inspect architecture & parameters
              </span>
              <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 group-hover:text-teal-400 transition-all" />
            </div>
          </div>
        ))}
      </div>

      {/* Interactive Agent Inspection Modal */}
      {selectedAgent && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-2xl rounded-2xl border border-white/10 bg-zinc-900/95 backdrop-blur-2xl p-6 shadow-2xl text-zinc-100 flex flex-col gap-5 max-h-[90vh] overflow-y-auto">
            {/* Header */}
            <div className="flex items-start justify-between border-b border-white/5 pb-4">
              <div className="flex items-center gap-3.5">
                <div className="p-3 rounded-xl bg-zinc-800 border border-white/10 text-teal-400">
                  {selectedAgent.icon}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-base font-bold text-zinc-100">
                      {selectedAgent.name}
                    </h2>
                    <span
                      className={`text-[10px] font-mono px-2 py-0.5 rounded-full border font-bold ${selectedAgent.statusColor}`}
                    >
                      {selectedAgent.status}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400 font-mono mt-0.5">
                    {selectedAgent.role} • ID: {selectedAgent.id}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setSelectedAgent(null)}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Content Details */}
            <div className="flex flex-col gap-4 text-xs">
              <div className="p-4 rounded-xl bg-zinc-950/60 border border-white/5">
                <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider block mb-1">
                  Agent Functional Overview
                </span>
                <p className="text-zinc-200 leading-relaxed font-medium">
                  {selectedAgent.description}
                </p>
              </div>

              {/* Model & Architecture */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="p-3.5 rounded-xl bg-zinc-950/60 border border-white/5">
                  <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider block">
                    LLM Model Routing
                  </span>
                  <span className="font-mono text-teal-300 font-bold text-xs mt-1 block">
                    {selectedAgent.modelArchitecture}
                  </span>
                  <span className="text-[10px] font-mono text-zinc-500 mt-0.5 block">
                    Alias: {selectedAgent.modelAlias}
                  </span>
                </div>

                <div className="p-3.5 rounded-xl bg-zinc-950/60 border border-white/5">
                  <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider block">
                    Context Window & Ceilings
                  </span>
                  <span className="font-mono text-zinc-200 font-bold text-xs mt-1 block">
                    {selectedAgent.contextWindow}
                  </span>
                  <span className="text-[10px] font-mono text-amber-400 mt-0.5 block">
                    Ceiling: {selectedAgent.ceiling}
                  </span>
                </div>
              </div>

              {/* System Instruction Strategy */}
              <div className="p-4 rounded-xl bg-zinc-950/60 border border-white/5">
                <span className="text-[10px] font-semibold text-teal-400 uppercase tracking-wider block mb-1.5 flex items-center gap-1.5">
                  <Terminal className="w-3.5 h-3.5" />
                  <span>System Instruction & Guardrails Policy</span>
                </span>
                <p className="text-zinc-300 font-mono text-[11px] leading-relaxed">
                  {selectedAgent.systemPromptSummary}
                </p>
              </div>

              {/* Sandbox Isolation Specifications */}
              <div className="p-4 rounded-xl bg-zinc-950/60 border border-white/5 flex flex-col gap-2 font-mono text-[11px]">
                <div className="flex items-center justify-between text-zinc-400 pb-2 border-b border-white/5">
                  <span className="font-bold text-zinc-300">
                    Sandbox Isolation Profile
                  </span>
                  <span className="text-teal-400">
                    {selectedAgent.sandboxTier}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-zinc-400">
                  <div>Memory Ceiling: {selectedAgent.memoryCap}</div>
                  <div>Execution Timeout: {selectedAgent.timeoutSeconds}s</div>
                  <div>Security Context: Non-Root UID 10001</div>
                  <div>Network: Restricted Egress</div>
                </div>
              </div>

              {/* Tools Allowlist */}
              <div>
                <span className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider block mb-2">
                  Assigned Tools Allowlist
                </span>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {selectedAgent.tools.map((t) => (
                    <div
                      key={t}
                      className="p-2.5 rounded-xl bg-zinc-950/80 border border-white/5 font-mono text-[11px] text-teal-300 flex items-center gap-2"
                    >
                      <div className="w-1.5 h-1.5 rounded-full bg-teal-400" />
                      <span>{t}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end pt-3 border-t border-white/5">
              <button
                type="button"
                onClick={() => setSelectedAgent(null)}
                className="px-5 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-xs font-semibold text-zinc-200 transition-colors"
              >
                Close Inspector
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
