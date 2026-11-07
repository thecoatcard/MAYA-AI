import {
  BookOpen,
  FileCode,
  Server,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import Link from "next/link";
import React from "react";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PublicNav } from "../../components/layout/PublicNav";

export const metadata = {
  title: "Documentation & Developer Guide | MAYA AI",
  description:
    "Developer documentation, API endpoints, Model Context Protocol integration, and SDK setup for MAYA AI digital employee.",
};

export default function DocsPage(): React.JSX.Element {
  const endpoints = [
    {
      method: "POST",
      path: "/api/v1/auth/token",
      desc: "Exchange credentials for in-memory access token and HttpOnly refresh cookie.",
    },
    {
      method: "POST",
      path: "/api/v1/chat",
      desc: "Stream chat conversation replies using Server-Sent Events (SSE) with idempotency.",
    },
    {
      method: "POST",
      path: "/api/v1/tasks",
      desc: "Submit autonomous instruction. Triggers DAG decomposition, cycle check, and subtask dispatch.",
    },
    {
      method: "GET",
      path: "/api/v1/tasks/{task_id}/events",
      desc: "Real-time SSE event stream for live subtask status, execution traces, and agent citations.",
    },
    {
      method: "GET",
      path: "/api/v1/approvals",
      desc: "List pending RFC 8785 authorization requests requiring human review.",
    },
    {
      method: "POST",
      path: "/api/v1/approvals/{approval_id}/decision",
      desc: "Approve or reject action. Mints single-use token or verifies step-up TOTP for RESTRICTED tier.",
    },
    {
      method: "GET",
      path: "/api/v1/artifacts",
      desc: "List cryptographically verified file deliverables (PDF, XLSX, DOCX, PPTX, patches).",
    },
    {
      method: "GET",
      path: "/api/v1/mcp/servers",
      desc: "List and manage Model Context Protocol servers with schema hash inspection.",
    },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-zinc-950 text-zinc-100 font-sans relative overflow-x-hidden">
      {/* Background radial gradients */}
      <div className="absolute top-0 right-1/4 w-[600px] h-[600px] bg-teal-500/10 rounded-full blur-[140px] pointer-events-none" />
      <div className="absolute top-1/2 left-1/3 w-[500px] h-[500px] bg-cyan-500/10 rounded-full blur-[120px] pointer-events-none" />

      <PublicNav />

      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-16 relative z-10">
        {/* Header */}
        <div className="max-w-3xl mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-teal-500/30 bg-teal-500/10 text-teal-300 text-xs font-mono mb-4">
            <BookOpen className="w-3.5 h-3.5" />
            <span>Developer Reference</span>
          </div>
          <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-white mb-4">
            MAYA AI Platform <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 via-cyan-300 to-emerald-400">
              Developer Documentation
            </span>
          </h1>
          <p className="text-base text-zinc-400 leading-relaxed">
            Everything you need to integrate, extend, and deploy MAYA AI. From
            Model Context Protocol (MCP) server creation to automated LangGraph
            subtask execution.
          </p>
        </div>

        {/* Quickstart Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-16">
          <div className="rounded-2xl border border-white/10 bg-zinc-900/50 backdrop-blur-xl p-6">
            <div className="p-2.5 rounded-xl bg-teal-500/10 border border-teal-500/30 text-teal-400 w-fit mb-4">
              <Terminal className="w-5 h-5" />
            </div>
            <h2 className="text-base font-bold text-white mb-2">
              Local Development
            </h2>
            <p className="text-xs text-zinc-400 leading-relaxed mb-4">
              Boot the complete backend and web client in under two minutes with
              Docker Compose and local PostgreSQL.
            </p>
            <div className="p-3 rounded-lg bg-black/50 border border-white/5 font-mono text-xs text-teal-300">
              npm run dev
            </div>
          </div>

          <div className="rounded-2xl border border-white/10 bg-zinc-900/50 backdrop-blur-xl p-6">
            <div className="p-2.5 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 w-fit mb-4">
              <Server className="w-5 h-5" />
            </div>
            <h2 className="text-base font-bold text-white mb-2">
              MCP Integration
            </h2>
            <p className="text-xs text-zinc-400 leading-relaxed mb-4">
              Connect external tools over standard stdio or HTTP streaming
              transports with schema poisoning defenses.
            </p>
            <div className="p-3 rounded-lg bg-black/50 border border-white/5 font-mono text-xs text-cyan-300">
              POST /api/v1/mcp/servers
            </div>
          </div>

          <div className="rounded-2xl border border-white/10 bg-zinc-900/50 backdrop-blur-xl p-6">
            <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-400 w-fit mb-4">
              <ShieldCheck className="w-5 h-5" />
            </div>
            <h2 className="text-base font-bold text-white mb-2">
              Security Guardrails
            </h2>
            <p className="text-xs text-zinc-400 leading-relaxed mb-4">
              Explore permission ceilings (READ, WRITE, APPROVAL_REQUIRED,
              RESTRICTED) and RFC 8785 tokens.
            </p>
            <div className="p-3 rounded-lg bg-black/50 border border-white/5 font-mono text-xs text-amber-300">
              RFC 8785 Canonical JSON
            </div>
          </div>
        </div>

        {/* API Specification Table */}
        <div className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl p-8 mb-16">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <FileCode className="w-5 h-5 text-teal-400" />
                <span>Core REST & SSE API Surface</span>
              </h2>
              <p className="text-xs text-zinc-400 mt-1">
                All requests require Bearer token authorization in the
                Authorization header.
              </p>
            </div>
          </div>

          <div className="divide-y divide-white/5">
            {endpoints.map((ep) => (
              <div
                key={ep.path + ep.method}
                className="py-4 flex flex-col md:flex-row md:items-center justify-between gap-3 text-xs"
              >
                <div className="flex items-center gap-3">
                  <span
                    className={`font-mono font-bold px-2 py-0.5 rounded text-[10px] ${
                      ep.method === "POST"
                        ? "bg-blue-500/20 text-blue-300 border border-blue-500/30"
                        : "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                    }`}
                  >
                    {ep.method}
                  </span>
                  <code className="text-zinc-200 font-mono font-semibold">
                    {ep.path}
                  </code>
                </div>
                <p className="text-zinc-400 max-w-lg md:text-right">
                  {ep.desc}
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* Docs Links */}
        <div className="text-center py-6">
          <Link
            href="/workspace"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-semibold text-xs shadow-lg shadow-teal-500/20 transition-all hover:scale-105"
          >
            <span>Launch Ambient Console</span>
          </Link>
        </div>
      </main>

      <PublicFooter />
    </div>
  );
}
