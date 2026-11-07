import {
  ArrowRight,
  Bot,
  Code,
  Cpu,
  FileText,
  FolderGit2,
  Lock,
  Mail,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import React from "react";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PublicNav } from "../../components/layout/PublicNav";

export const metadata = {
  title: "Specialist Agent Fleet & Features | MAYA AI",
  description:
    "Comprehensive capabilities of MAYA AI: 6 specialist agents, LangGraph DAG engine, single-use approval tokens, and MCP Gateway.",
};

export default function FeaturesPage(): React.JSX.Element {
  const agents = [
    {
      title: "Orchestrator Agent",
      role: "Decomposition, DAG Planning & Saga Compensation",
      model: "gemini-3.8-flash",
      ceiling: "APPROVAL_REQUIRED",
      icon: <Cpu className="w-6 h-6 text-teal-400" />,
      desc: "Analyzes natural language instructions and compiles them into a dependency graph (DAG) of subtasks. Performs cycle detection, token and cost budget allocation, and triggers compensating actions if any node fails.",
    },
    {
      title: "Research Agent",
      role: "Autonomous Web & Documentation Investigation",
      model: "gemini-3.8-flash",
      ceiling: "READ (Autonomous)",
      icon: <Search className="w-6 h-6 text-cyan-400" />,
      desc: "Performs iterative search across the web using Brave Search API and local documentation repositories. Captures verbatim text snippets, source URLs, and publication dates as structured, auditable citations.",
    },
    {
      title: "Coding Agent",
      role: "Reproduce, Fix, Verify in Sandbox & Pull Requests",
      model: "gemini-3.8-flash",
      ceiling: "APPROVAL_REQUIRED",
      icon: <Code className="w-6 h-6 text-emerald-400" />,
      desc: "Executes Workflow 3: reproduces failing tests inside isolated Docker sandboxes, formulates minimal patches, verifies exit code 0, and gates PR creation behind RFC 8785 single-use approval tokens.",
    },
    {
      title: "Document Agent",
      role: "Cryptographically Verified Deliverables",
      model: "gemini-3.8-flash",
      ceiling: "WRITE",
      icon: <FileText className="w-6 h-6 text-purple-400" />,
      desc: "Generates publication-quality PDFs via WeasyPrint, financial models in XLSX via OpenPyXL, Word documents, and slide decks. Each artifact is stamped with SHA-256 checksums and verification reports.",
    },
    {
      title: "Email Agent",
      role: "Communications Triage & Protected Dispatch",
      model: "gemini-3.5-flash-lite",
      ceiling: "APPROVAL_REQUIRED",
      icon: <Mail className="w-6 h-6 text-blue-400" />,
      desc: "Searches Gmail threads, summarizes message chains, and prepares email drafts. Halts execution with an interactive email approval card—never sends an email without human authorization.",
    },
    {
      title: "PM Agent",
      role: "Milestone Tracking & Backlog Coordination",
      model: "gemini-3.5-flash-lite",
      ceiling: "CONTROLLED_WRITE",
      icon: <Bot className="w-6 h-6 text-amber-400" />,
      desc: "Synchronizes task states across Notion and GitHub Issues, produces executive sprint summaries, and maintains the project event ledger with zero manual status reporting overhead.",
    },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-zinc-950 text-zinc-100 font-sans relative overflow-x-hidden">
      {/* Background radial gradients */}
      <div className="absolute top-0 right-1/3 w-[600px] h-[600px] bg-teal-500/10 rounded-full blur-[140px] pointer-events-none" />
      <div className="absolute top-1/2 left-1/4 w-[500px] h-[500px] bg-cyan-500/10 rounded-full blur-[120px] pointer-events-none" />

      <PublicNav />

      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-16 relative z-10">
        {/* Hero */}
        <div className="text-center max-w-3xl mx-auto mb-20">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-teal-500/30 bg-teal-500/10 text-teal-300 text-xs font-mono mb-4">
            <Sparkles className="w-3.5 h-3.5" />
            <span>Phases 01–09 Full Fleet</span>
          </div>
          <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-white mb-6">
            Engineered for Autonomy, <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 via-cyan-300 to-emerald-400">
              Governed by Cryptography.
            </span>
          </h1>
          <p className="text-base text-zinc-400 leading-relaxed">
            MAYA AI operates not as a solitary chatbot, but as a coordinated
            fleet of specialist agents with explicit permission ceilings,
            hardware token step-up authentication, and tamper-evident audit
            trails.
          </p>
        </div>

        {/* 6 Specialist Agents Grid */}
        <div className="mb-24">
          <div className="flex items-center justify-between mb-8">
            <div>
              <h2 className="text-2xl font-bold text-white">
                The Specialist Agent Fleet
              </h2>
              <p className="text-xs text-zinc-400 mt-1">
                Domain-specific agents with isolated system prompts, tool
                ceilings, and verified output schemas.
              </p>
            </div>
            <Link
              href="/agents"
              className="hidden sm:inline-flex items-center gap-1.5 text-xs text-teal-400 hover:text-teal-300 font-semibold"
            >
              <span>Explore Fleet Console</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {agents.map((agent) => (
              <div
                key={agent.title}
                className="rounded-2xl border border-white/10 bg-zinc-900/50 backdrop-blur-xl p-6 flex flex-col group hover:border-teal-500/30 hover:bg-zinc-900/70 transition-all duration-300"
              >
                <div className="flex items-center justify-between mb-4">
                  <div className="p-2.5 rounded-xl bg-white/5 border border-white/10 group-hover:border-teal-500/30 transition-colors">
                    {agent.icon}
                  </div>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-full border border-white/10 bg-black/40 text-zinc-400">
                    {agent.ceiling}
                  </span>
                </div>

                <h3 className="text-base font-bold text-white mb-1 group-hover:text-teal-300 transition-colors">
                  {agent.title}
                </h3>
                <p className="text-xs font-mono text-teal-400/80 mb-3">
                  {agent.role}
                </p>

                <p className="text-xs text-zinc-400 leading-relaxed mb-6 flex-1">
                  {agent.desc}
                </p>

                <div className="pt-4 border-t border-white/5 flex items-center justify-between text-[11px] text-zinc-500 font-mono">
                  <span>Model: {agent.model}</span>
                  <span className="text-emerald-400">Active</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 3 Core Architectural Pillars */}
        <div className="rounded-3xl border border-white/10 bg-gradient-to-b from-zinc-900/80 to-zinc-950/80 backdrop-blur-2xl p-8 sm:p-12 mb-20">
          <div className="text-center max-w-2xl mx-auto mb-12">
            <span className="text-xs font-mono uppercase tracking-widest text-teal-400 font-semibold">
              Deterministic Safety Foundation
            </span>
            <h2 className="text-3xl font-extrabold text-white mt-2">
              How MAYA AI Prevents Unintended Action
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 text-left">
            {/* Pillar 1 */}
            <div className="flex flex-col gap-3">
              <div className="w-10 h-10 rounded-xl bg-teal-500/10 border border-teal-500/30 flex items-center justify-center text-teal-400">
                <ShieldAlert className="w-5 h-5" />
              </div>
              <h3 className="text-sm font-bold text-white">
                RFC 8785 Payload Hashing
              </h3>
              <p className="text-xs text-zinc-400 leading-relaxed">
                Before any state-altering action occurs (e.g. Git PR, Gmail
                dispatch, shell execution), the tool argument payload is
                canonicalized into a cryptographic SHA-256 hash. The single-use
                authorization token is cryptographically bound to that exact
                payload.
              </p>
            </div>

            {/* Pillar 2 */}
            <div className="flex flex-col gap-3">
              <div className="w-10 h-10 rounded-xl bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center text-cyan-400">
                <FolderGit2 className="w-5 h-5" />
              </div>
              <h3 className="text-sm font-bold text-white">
                Docker Sandbox Isolation
              </h3>
              <p className="text-xs text-zinc-400 leading-relaxed">
                Coding agents reproduce bugs, execute compilers, and run
                integration tests inside isolated Docker containers with
                read-only root filesystems, resource quotas, and zero host
                network access.
              </p>
            </div>

            {/* Pillar 3 */}
            <div className="flex flex-col gap-3">
              <div className="w-10 h-10 rounded-xl bg-purple-500/10 border border-purple-500/30 flex items-center justify-center text-purple-400">
                <Lock className="w-5 h-5" />
              </div>
              <h3 className="text-sm font-bold text-white">
                Step-up Hardware TOTP
              </h3>
              <p className="text-xs text-zinc-400 leading-relaxed">
                High-consequence actions classified under the RESTRICTED tier
                require physical hardware or app-based TOTP code verification
                with replay defense and exponential lockout to prevent
                credential compromise.
              </p>
            </div>
          </div>
        </div>

        {/* CTA banner */}
        <div className="text-center py-10">
          <Link
            href="/workspace"
            className="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-bold text-sm shadow-xl shadow-teal-500/25 transition-all hover:scale-105"
          >
            <span>Launch Ambient Workspace Console</span>
            <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </main>

      <PublicFooter />
    </div>
  );
}
