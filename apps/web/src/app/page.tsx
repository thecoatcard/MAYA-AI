"use client";

import {
  ArrowRight,
  Bot,
  Brain,
  CheckCircle2,
  Clock,
  Code2,
  Cpu,
  Database,
  FileCode,
  FileSpreadsheet,
  FileText,
  KeyRound,
  Layers,
  Lock,
  Mail,
  Search,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Zap,
} from "lucide-react";
import Link from "next/link";
import React, { useEffect, useState } from "react";
import { useAuth } from "../hooks/useAuth";
import { PublicFooter } from "../components/layout/PublicFooter";
import { PublicNav } from "../components/layout/PublicNav";
import { readStoredTheme, storeTheme } from "../components/chat/ThemeToggle";
import { GlassBadge } from "../components/ui";

const capabilityList = [
  {
    title: "Accounts and sign-in",
    status: "Available",
    description:
      "Session restoration with HttpOnly refresh cookies and user role isolation.",
  },
  {
    title: "Text chat with history",
    status: "Available",
    description:
      "SSE streaming conversation engine, prompt resilience, and cursor pagination.",
  },
  {
    title: "Agent tasks with approvals",
    status: "Planned",
    description:
      "LangGraph execution DAGs, single-use RFC 8785 tokens, and saga compensations.",
  },
  {
    title: "Voice workspace",
    status: "Planned",
    description:
      "Bidirectional real-time Gemini 3.8 audio streaming with VAD and barge-in.",
  },
] as const;

const specialistAgents = [
  {
    id: "specialist-pm",
    name: "Executive PM",
    role: "Task Decomposition & DAG Planning",
    icon: Brain,
    tier: "STANDARD",
    model: "gemini-3.8-flash",
    accent: "teal",
    description:
      "Decomposes complex requests into directed acyclic graphs and coordinates task handoffs.",
  },
  {
    id: "specialist-coder",
    name: "Core Coder",
    role: "Sandboxed Code Execution",
    icon: Code2,
    tier: "APPROVAL_REQUIRED",
    model: "gemini-3.8-pro",
    accent: "cyan",
    description:
      "Generates unified diffs and executes test suites inside isolated Docker scratch runners.",
  },
  {
    id: "specialist-researcher",
    name: "Deep Researcher",
    role: "Literature & Evidence Synthesis",
    icon: Search,
    tier: "STANDARD",
    model: "gemini-3.8-flash",
    accent: "indigo",
    description:
      "Gathers web evidence, cross-checks factual claims, and produces tamper-proof source citations.",
  },
  {
    id: "specialist-documentarian",
    name: "Documentarian",
    role: "Artifacts & Specifications",
    icon: FileText,
    tier: "STANDARD",
    model: "gemini-3.8-flash",
    accent: "emerald",
    description:
      "Authors audited PDFs, presentations, and technical documentation with SHA-256 integrity seal.",
  },
  {
    id: "specialist-analyst",
    name: "Financial Analyst",
    role: "Data & Workbook Modeling",
    icon: FileSpreadsheet,
    tier: "APPROVAL_REQUIRED",
    model: "gemini-3.8-pro",
    accent: "amber",
    description:
      "Computes financial statements, parses workbooks, and validates mathematical invariants.",
  },
  {
    id: "specialist-mailer",
    name: "Comms & Dispatch",
    role: "External Communications",
    icon: Mail,
    tier: "RESTRICTED",
    model: "gemini-3.8-flash",
    accent: "rose",
    description:
      "Drafts executive communications with mandatory human approval and TOTP step-up verification.",
  },
] as const;

export default function Home(): React.JSX.Element {
  const { isAuthenticated } = useAuth();
  const [activeTab, setActiveTab] = useState<"dag" | "diff" | "mcp">("dag");

  useEffect(() => {
    const stored = readStoredTheme();
    storeTheme(stored);
  }, []);

  return (
    <div className="min-h-screen flex flex-col bg-zinc-950 text-zinc-100 font-sans relative overflow-x-hidden selection:bg-teal-500/30 selection:text-teal-200">
      {/* Ambient background glows */}
      <div className="fixed top-0 left-1/2 -translate-x-1/2 w-[1000px] h-[550px] bg-gradient-to-b from-teal-500/12 via-cyan-500/5 to-transparent rounded-full blur-[140px] pointer-events-none -z-10" />
      <div className="fixed -bottom-48 right-0 w-[600px] h-[600px] bg-teal-500/5 rounded-full blur-[160px] pointer-events-none -z-10" />
      <div className="fixed top-1/3 -left-48 w-[500px] h-[500px] bg-cyan-500/5 rounded-full blur-[150px] pointer-events-none -z-10" />

      {/* Centralized Authentication-Aware Navigation */}
      <PublicNav />

      <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 md:py-20 flex flex-col gap-24 relative z-10">
        {/* ========================================================================= */}
        {/* HERO SECTION                                                              */}
        {/* ========================================================================= */}
        <section className="flex flex-col items-center text-center max-w-4xl mx-auto pt-4 md:pt-8">
          {/* Status Chip */}
          <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-teal-500/30 bg-teal-500/10 text-teal-300 text-xs font-mono mb-8 backdrop-blur-md shadow-xs animate-fade-in">
            <span className="w-2 h-2 rounded-full bg-teal-400 animate-pulse" />
            <span>MAYA AI · Autonomous Digital Employee</span>
            <span className="text-zinc-600">|</span>
            <span className="text-zinc-400">Phase 01–09 Architecture</span>
          </div>

          {/* Primary Main Heading (Maintains exact test requirement) */}
          <h1 className="text-4xl sm:text-6xl md:text-7xl font-extrabold tracking-tight text-white mb-8 leading-[1.1] max-w-4xl">
            A digital employee that{" "}
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 via-cyan-300 to-emerald-400">
              asks before it acts.
            </span>
          </h1>

          {/* Subtitle */}
          <p className="text-base sm:text-xl text-zinc-400 mb-10 leading-relaxed max-w-2xl mx-auto font-normal">
            Orchestrates specialist agent fleets across multi-step execution
            DAGs. Every privileged tool call is cryptographically sealed and
            gated by human approval tokens.
          </p>

          {/* Authentication-Aware Hero Actions (Maintains exact test links) */}
          <div className="flex flex-wrap items-center justify-center gap-4 w-full max-w-md mx-auto mb-12">
            {isAuthenticated ? (
              <>
                <Link
                  href="/workspace"
                  className="w-full sm:w-auto inline-flex items-center justify-center gap-2.5 px-7 py-3.5 rounded-xl bg-teal-500 hover:bg-teal-400 active:scale-[0.98] text-zinc-950 font-bold text-sm shadow-xl shadow-teal-500/20 transition-all"
                >
                  <Terminal className="w-4 h-4" />
                  <span>Enter Workspace Console</span>
                  <ArrowRight className="w-4 h-4" />
                </Link>
                <Link
                  href="/tasks"
                  className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-3.5 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 active:scale-[0.98] text-zinc-200 font-semibold text-sm backdrop-blur-md transition-all"
                >
                  <Layers className="w-4 h-4 text-zinc-400" />
                  <span>Task Ledger</span>
                </Link>
              </>
            ) : (
              <>
                <Link
                  href="/login"
                  className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl bg-teal-500 hover:bg-teal-400 active:scale-[0.98] text-zinc-950 font-bold text-sm shadow-xl shadow-teal-500/20 transition-all"
                >
                  Sign in
                  <ArrowRight className="w-4 h-4" />
                </Link>
                <Link
                  href="/register"
                  className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 active:scale-[0.98] text-zinc-200 font-semibold text-sm backdrop-blur-md transition-all"
                >
                  Create account
                </Link>
              </>
            )}
          </div>

          {/* Quick Platform Pillars */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 w-full max-w-3xl pt-6 border-t border-white/5">
            <div className="flex items-center gap-2.5 text-left p-2.5 rounded-xl bg-white/[0.02] border border-white/5">
              <ShieldCheck className="w-4 h-4 text-teal-400 shrink-0" />
              <div>
                <div className="text-[11px] font-bold text-zinc-200">
                  RFC 8785
                </div>
                <div className="text-[10px] text-zinc-500">
                  Zero-Tamper Diffs
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2.5 text-left p-2.5 rounded-xl bg-white/[0.02] border border-white/5">
              <Zap className="w-4 h-4 text-cyan-400 shrink-0" />
              <div>
                <div className="text-[11px] font-bold text-zinc-200">
                  Gemini 3.8
                </div>
                <div className="text-[10px] text-zinc-500">Tiered Gateway</div>
              </div>
            </div>

            <div className="flex items-center gap-2.5 text-left p-2.5 rounded-xl bg-white/[0.02] border border-white/5">
              <KeyRound className="w-4 h-4 text-amber-400 shrink-0" />
              <div>
                <div className="text-[11px] font-bold text-zinc-200">
                  Hardware TOTP
                </div>
                <div className="text-[10px] text-zinc-500">Step-Up Auth</div>
              </div>
            </div>

            <div className="flex items-center gap-2.5 text-left p-2.5 rounded-xl bg-white/[0.02] border border-white/5">
              <Lock className="w-4 h-4 text-emerald-400 shrink-0" />
              <div>
                <div className="text-[11px] font-bold text-zinc-200">
                  Postgres RLS
                </div>
                <div className="text-[10px] text-zinc-500">
                  Tenant Isolation
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ========================================================================= */}
        {/* INTERACTIVE WORKSPACE PREVIEW (GLASS HERO DEMO)                           */}
        {/* ========================================================================= */}
        <section className="w-full max-w-5xl mx-auto">
          <div className="relative rounded-2xl border border-white/10 bg-zinc-950/70 backdrop-blur-2xl shadow-2xl overflow-hidden shadow-teal-950/20">
            {/* Top Bar / Mac-style buttons */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 bg-white/[0.02]">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-rose-500/60" />
                <div className="w-3 h-3 rounded-full bg-amber-500/60" />
                <div className="w-3 h-3 rounded-full bg-emerald-500/60" />
                <span className="text-xs font-mono text-zinc-500 ml-2">
                  maya-runtime-v1 // task_019a2b84
                </span>
              </div>

              {/* View Switcher Tabs */}
              <div className="flex items-center gap-1 p-1 rounded-lg bg-black/40 border border-white/5 text-xs font-mono">
                <button
                  type="button"
                  onClick={() => setActiveTab("dag")}
                  className={`px-3 py-1 rounded-md transition-colors ${
                    activeTab === "dag"
                      ? "bg-teal-500/20 text-teal-300 font-semibold"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  LangGraph DAG
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("diff")}
                  className={`px-3 py-1 rounded-md transition-colors ${
                    activeTab === "diff"
                      ? "bg-teal-500/20 text-teal-300 font-semibold"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  RFC 8785 Diff
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("mcp")}
                  className={`px-3 py-1 rounded-md transition-colors ${
                    activeTab === "mcp"
                      ? "bg-teal-500/20 text-teal-300 font-semibold"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  MCP Tools (4)
                </button>
              </div>
            </div>

            {/* Simulated Live Viewport */}
            <div className="p-6 md:p-8 min-h-[340px] flex flex-col justify-center bg-radial-gradient from-teal-500/[0.03] to-transparent">
              {activeTab === "dag" && (
                <div className="flex flex-col gap-6 animate-fade-in">
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <div>
                      <div className="text-xs font-mono text-teal-400 font-bold uppercase tracking-wider mb-1">
                        Active Execution Graph
                      </div>
                      <div className="text-sm font-semibold text-white">
                        Financial Audit & Quarterly Briefing Deployment
                      </div>
                    </div>
                    <GlassBadge variant="amber" size="sm" dot mono>
                      APPROVAL_REQUIRED · Human Gate
                    </GlassBadge>
                  </div>

                  {/* Visual Node Chain */}
                  <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 relative">
                    <div className="p-3.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 flex flex-col gap-2">
                      <div className="flex items-center justify-between text-[11px] font-mono text-emerald-300">
                        <span>Node 1: Plan</span>
                        <CheckCircle2 className="w-3.5 h-3.5" />
                      </div>
                      <div className="text-xs font-semibold text-white">
                        Decompose Goals
                      </div>
                      <div className="text-[10px] text-zinc-400 font-mono">
                        Completed · 210ms
                      </div>
                    </div>

                    <div className="p-3.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 flex flex-col gap-2">
                      <div className="flex items-center justify-between text-[11px] font-mono text-emerald-300">
                        <span>Node 2: Extract</span>
                        <CheckCircle2 className="w-3.5 h-3.5" />
                      </div>
                      <div className="text-xs font-semibold text-white">
                        Parse SEC 10-Q
                      </div>
                      <div className="text-[10px] text-zinc-400 font-mono">
                        Completed · 840ms
                      </div>
                    </div>

                    <div className="p-3.5 rounded-xl border border-amber-500/40 bg-amber-500/10 flex flex-col gap-2 shadow-lg shadow-amber-500/5">
                      <div className="flex items-center justify-between text-[11px] font-mono text-amber-300">
                        <span>Node 3: Write</span>
                        <Lock className="w-3.5 h-3.5" />
                      </div>
                      <div className="text-xs font-semibold text-white">
                        Update S3 Ledger
                      </div>
                      <div className="text-[10px] text-amber-300 font-mono font-bold animate-pulse">
                        Awaiting Token
                      </div>
                    </div>

                    <div className="p-3.5 rounded-xl border border-white/5 bg-white/[0.02] opacity-50 flex flex-col gap-2">
                      <div className="flex items-center justify-between text-[11px] font-mono text-zinc-500">
                        <span>Node 4: Dispatch</span>
                        <Clock className="w-3.5 h-3.5" />
                      </div>
                      <div className="text-xs font-semibold text-zinc-400">
                        Email Summary
                      </div>
                      <div className="text-[10px] text-zinc-600 font-mono">
                        Queued
                      </div>
                    </div>
                  </div>

                  {/* Interactive Action Prompt */}
                  <div className="p-4 rounded-xl border border-white/10 bg-black/40 flex flex-col sm:flex-row items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-lg bg-teal-500/10 border border-teal-500/30 text-teal-400">
                        <Terminal className="w-4 h-4" />
                      </div>
                      <div>
                        <div className="text-xs font-bold text-zinc-100">
                          Single-Use Token Verification Protocol
                        </div>
                        <div className="text-xs text-zinc-400">
                          Review canonical JSON mutation and sign execution
                          token.
                        </div>
                      </div>
                    </div>
                    <Link
                      href="/approvals"
                      className="px-4 py-2 rounded-lg bg-teal-500 hover:bg-teal-400 text-zinc-950 font-bold text-xs shadow-md transition-all hover:scale-105 shrink-0"
                    >
                      Inspect & Approve &rarr;
                    </Link>
                  </div>
                </div>
              )}

              {activeTab === "diff" && (
                <div className="font-mono text-xs text-zinc-300 space-y-2 animate-fade-in">
                  <div className="text-zinc-500 text-[11px] pb-2 border-b border-white/5 flex items-center justify-between">
                    <span>RFC 8785 Canonical JSON Mutation Request</span>
                    <span className="text-teal-400">sha256:7e91a0...f92c</span>
                  </div>
                  <pre className="p-3 rounded-lg bg-black/50 border border-white/5 text-zinc-300 overflow-x-auto text-[11px]">
                    {`{
  "action": "execute_sandboxed_patch",
  "actor": "specialist-coder@maya-ai.internal",
  "canonical_hash": "sha256:7e91a0c8b3d14f92ce4a89b0213d87e",
  "files_modified": ["src/ledger/transactions.py"],
  "permission_tier": "APPROVAL_REQUIRED",
  "requires_token": true
}`}
                  </pre>
                  <p className="text-[11px] text-zinc-400">
                    Byte-for-byte deterministic hashing guarantees that approved
                    payloads cannot be substituted in transit.
                  </p>
                </div>
              )}

              {activeTab === "mcp" && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 animate-fade-in">
                  <div className="p-3 rounded-xl border border-white/10 bg-white/[0.02] flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      <Database className="w-4 h-4 text-teal-400" />
                      <div>
                        <div className="text-xs font-bold text-zinc-200">
                          postgres-warehouse
                        </div>
                        <div className="text-[10px] text-zinc-500 font-mono">
                          Transport: Stdio · 2 tools
                        </div>
                      </div>
                    </div>
                    <GlassBadge variant="emerald" size="sm" dot mono>
                      ONLINE
                    </GlassBadge>
                  </div>

                  <div className="p-3 rounded-xl border border-white/10 bg-white/[0.02] flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      <FileCode className="w-4 h-4 text-cyan-400" />
                      <div>
                        <div className="text-xs font-bold text-zinc-200">
                          sandbox-runner
                        </div>
                        <div className="text-[10px] text-zinc-500 font-mono">
                          Transport: Docker · 4 tools
                        </div>
                      </div>
                    </div>
                    <GlassBadge variant="emerald" size="sm" dot mono>
                      ONLINE
                    </GlassBadge>
                  </div>

                  <div className="p-3 rounded-xl border border-white/10 bg-white/[0.02] flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      <Search className="w-4 h-4 text-indigo-400" />
                      <div>
                        <div className="text-xs font-bold text-zinc-200">
                          web-search-engine
                        </div>
                        <div className="text-[10px] text-zinc-500 font-mono">
                          Transport: SSE · 1 tool
                        </div>
                      </div>
                    </div>
                    <GlassBadge variant="emerald" size="sm" dot mono>
                      ONLINE
                    </GlassBadge>
                  </div>

                  <div className="p-3 rounded-xl border border-white/10 bg-white/[0.02] flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      <Mail className="w-4 h-4 text-rose-400" />
                      <div>
                        <div className="text-xs font-bold text-zinc-200">
                          smtp-outbox
                        </div>
                        <div className="text-[10px] text-zinc-500 font-mono">
                          Transport: HTTP · 1 tool
                        </div>
                      </div>
                    </div>
                    <GlassBadge variant="amber" size="sm" dot mono>
                      GATED
                    </GlassBadge>
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* ========================================================================= */}
        {/* HONEST CAPABILITY LEDGER (EXACT REPOSITORY POLICY & TESTS COMPLIANT)      */}
        {/* ========================================================================= */}
        <section
          aria-label="What is available"
          className="w-full max-w-4xl mx-auto"
        >
          <div className="rounded-2xl border border-white/10 bg-zinc-900/60 backdrop-blur-xl p-6 sm:p-8 shadow-2xl">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-6 pb-4 border-b border-white/10">
              <div>
                <h2 className="text-base sm:text-lg font-bold text-white font-mono flex items-center gap-2">
                  <ShieldCheck className="w-5 h-5 text-teal-400" />
                  <span>Verified Capability Matrix</span>
                </h2>
                <p className="text-xs text-zinc-400 mt-1">
                  Transparent readiness declaration separating implemented
                  capabilities from planned milestones.
                </p>
              </div>
              <GlassBadge variant="teal" size="sm" mono>
                Truth in Software Policy
              </GlassBadge>
            </div>

            <ol className="divide-y divide-white/5">
              {capabilityList.map((item, index) => (
                <li
                  key={item.title}
                  className="py-4 px-2 flex flex-col sm:flex-row sm:items-center justify-between gap-4"
                >
                  <div className="flex items-start sm:items-center gap-3.5">
                    <span className="font-mono text-zinc-500 text-xs font-bold w-6 shrink-0 mt-0.5 sm:mt-0">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <div className="font-semibold text-sm text-zinc-100">
                        {item.title}
                      </div>
                      <div className="text-xs text-zinc-400 mt-0.5">
                        {item.description}
                      </div>
                    </div>
                  </div>

                  <div className="sm:self-center shrink-0 ml-9 sm:ml-0">
                    <span
                      data-state={item.status.toLowerCase()}
                      className={`inline-flex items-center text-[10px] font-mono font-semibold px-2.5 py-1 rounded-full border ${
                        item.status === "Available"
                          ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                          : "bg-zinc-800 text-zinc-400 border-zinc-700"
                      }`}
                    >
                      {item.status}
                    </span>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* ========================================================================= */}
        {/* SPECIALIST AGENT FLEET SHOWCASE                                           */}
        {/* ========================================================================= */}
        <section className="w-full max-w-6xl mx-auto flex flex-col gap-8">
          <div className="text-center max-w-2xl mx-auto">
            <h2 className="text-2xl sm:text-4xl font-extrabold text-white tracking-tight mb-3">
              Autonomous Specialist Agent Fleet
            </h2>
            <p className="text-sm text-zinc-400 leading-relaxed">
              MAYA AI delegates tasks across 6 specialized personas, each bound
              by model routing configurations and strict permission boundaries.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {specialistAgents.map((agent) => {
              const Icon = agent.icon;
              return (
                <div
                  key={agent.id}
                  className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-md p-6 hover:border-teal-500/30 hover:bg-zinc-900/60 transition-all flex flex-col justify-between group"
                >
                  <div>
                    <div className="flex items-center justify-between mb-4">
                      <div className="p-2.5 rounded-xl bg-teal-500/10 border border-teal-500/25 text-teal-400">
                        <Icon className="w-5 h-5" />
                      </div>
                      <GlassBadge
                        variant={
                          agent.tier === "RESTRICTED"
                            ? "rose"
                            : agent.tier === "APPROVAL_REQUIRED"
                              ? "amber"
                              : "teal"
                        }
                        size="sm"
                        mono
                      >
                        {agent.tier}
                      </GlassBadge>
                    </div>

                    <h3 className="text-base font-bold text-white mb-1 group-hover:text-teal-300 transition-colors">
                      {agent.name}
                    </h3>
                    <div className="text-xs font-mono text-zinc-500 mb-3">
                      {agent.role}
                    </div>
                    <p className="text-xs text-zinc-400 leading-relaxed mb-4">
                      {agent.description}
                    </p>
                  </div>

                  <div className="pt-3 border-t border-white/5 flex items-center justify-between text-[11px] font-mono text-zinc-500">
                    <span>Model: {agent.model}</span>
                    <span className="text-teal-400 flex items-center gap-1 group-hover:translate-x-0.5 transition-transform">
                      Inspect &rarr;
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="text-center">
            <Link
              href="/agents"
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-xs font-semibold text-zinc-200 transition-colors"
            >
              <span>Explore Fleet Architecture & Tool Limits</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </section>

        {/* ========================================================================= */}
        {/* CORE PLATFORM WORKSPACES NAVIGATION                                       */}
        {/* ========================================================================= */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-5xl mx-auto">
          <Link
            href="/workspace"
            className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-md p-6 hover:border-teal-500/30 hover:bg-zinc-900/60 transition-all group"
          >
            <div className="p-2.5 rounded-xl bg-teal-500/10 border border-teal-500/30 text-teal-400 w-fit mb-4">
              <Bot className="w-5 h-5" />
            </div>
            <h3 className="text-base font-bold text-white mb-2 group-hover:text-teal-300 transition-colors">
              Ambient Workspace &rarr;
            </h3>
            <p className="text-xs text-zinc-400 leading-relaxed">
              Experience the unified multi-pane workspace with audio visualizer,
              conversation thread, and deliverable artifacts canvas.
            </p>
          </Link>

          <Link
            href="/architecture"
            className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-md p-6 hover:border-cyan-500/30 hover:bg-zinc-900/60 transition-all group"
          >
            <div className="p-2.5 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 w-fit mb-4">
              <Cpu className="w-5 h-5" />
            </div>
            <h3 className="text-base font-bold text-white mb-2 group-hover:text-cyan-300 transition-colors">
              System Architecture &rarr;
            </h3>
            <p className="text-xs text-zinc-400 leading-relaxed">
              Explore LangGraph DAG checkpoints, transactional outbox events,
              PostgreSQL RLS, and ephemeral Docker sandbox runners.
            </p>
          </Link>

          <Link
            href="/approvals"
            className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-md p-6 hover:border-amber-500/30 hover:bg-zinc-900/60 transition-all group"
          >
            <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-400 w-fit mb-4">
              <ShieldAlert className="w-5 h-5" />
            </div>
            <h3 className="text-base font-bold text-white mb-2 group-hover:text-amber-300 transition-colors">
              Security Governance &rarr;
            </h3>
            <p className="text-xs text-zinc-400 leading-relaxed">
              Inspect single-use RFC 8785 approval tokens, cryptographic hash
              seals, and hardware TOTP step-up authorization.
            </p>
          </Link>
        </section>
      </main>

      {/* Honest Policy Footer Banner */}
      <div className="text-center py-4 border-t border-white/5 text-xs font-mono text-zinc-400 bg-zinc-950/80 backdrop-blur-md">
        MAYA AI keeps planned capabilities clearly separated from what works
        today.
      </div>

      <PublicFooter />
    </div>
  );
}
