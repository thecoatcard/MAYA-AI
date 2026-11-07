import {
  ArrowRight,
  CheckCircle2,
  Cpu,
  Database,
  Layers,
  Shield,
  ShieldAlert,
} from "lucide-react";
import Link from "next/link";
import React from "react";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PublicNav } from "../../components/layout/PublicNav";

export const metadata = {
  title: "System Architecture & Security Model | MAYA AI",
  description:
    "Explore MAYA AI's 3-tier defense-in-depth architecture: Model Gateway, LangGraph DAG engine, Transactional Outbox, MCP Gateway, and Docker Sandbox isolation.",
};

export default function ArchitecturePage(): React.JSX.Element {
  return (
    <div className="min-h-screen flex flex-col bg-zinc-950 text-zinc-100 font-sans relative overflow-x-hidden">
      {/* Background radial gradients */}
      <div className="absolute top-0 left-1/3 w-[600px] h-[600px] bg-teal-500/10 rounded-full blur-[140px] pointer-events-none" />
      <div className="absolute bottom-1/3 right-1/4 w-[500px] h-[500px] bg-cyan-500/10 rounded-full blur-[120px] pointer-events-none" />

      <PublicNav />

      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-16 relative z-10">
        {/* Header */}
        <div className="text-center max-w-3xl mx-auto mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-teal-500/30 bg-teal-500/10 text-teal-300 text-xs font-mono mb-4">
            <Shield className="w-3.5 h-3.5" />
            <span>Defense-in-Depth Specification</span>
          </div>
          <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-white mb-6">
            Architectural Topology <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 via-cyan-300 to-emerald-400">
              & Verification Protocols
            </span>
          </h1>
          <p className="text-base text-zinc-400 leading-relaxed">
            MAYA AI enforces strict boundaries between cognitive model planning,
            execution orchestration, external tool side-effects, and persistent
            tenant data.
          </p>
        </div>

        {/* 4 Architectural Layers */}
        <div className="space-y-6 mb-20">
          {/* Layer 1: Client & Ambient Workspace */}
          <div className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl p-8 hover:border-teal-500/30 transition-all">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-teal-500/10 border border-teal-500/30 text-teal-400">
                  <Layers className="w-5 h-5" />
                </div>
                <div>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-teal-400 font-semibold">
                    Layer 01 · Presentation & Interaction
                  </span>
                  <h2 className="text-xl font-bold text-white">
                    Ambient Client & Next.js Same-Origin Proxy
                  </h2>
                </div>
              </div>
              <span className="text-xs font-mono px-3 py-1 rounded-full border border-teal-500/20 bg-teal-950/30 text-teal-300">
                Browser / React 19 / Tailwind v4
              </span>
            </div>
            <p className="text-xs text-zinc-400 leading-relaxed max-w-3xl mb-4">
              Access tokens are stored strictly in-memory and never written to
              localStorage or cookies. Rotating refresh tokens reside in
              HttpOnly, SameSite=Strict cookies. Real-time subtask updates
              stream over Server-Sent Events (SSE) with automatic reconnection
              and sequence deduplication.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs text-zinc-300 font-mono">
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • In-memory JWT access token
              </div>
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • HttpOnly SameSite=Strict refresh cookie
              </div>
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • Server-Sent Events (SSE) streaming
              </div>
            </div>
          </div>

          {/* Layer 2: Cognitive Engine & DAG Planner */}
          <div className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl p-8 hover:border-cyan-500/30 transition-all">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-cyan-500/10 border border-cyan-500/30 text-cyan-400">
                  <Cpu className="w-5 h-5" />
                </div>
                <div>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-cyan-400 font-semibold">
                    Layer 02 · Cognitive Planning & State
                  </span>
                  <h2 className="text-xl font-bold text-white">
                    FastAPI Engine & LangGraph DAG Checkpointer
                  </h2>
                </div>
              </div>
              <span className="text-xs font-mono px-3 py-1 rounded-full border border-cyan-500/20 bg-cyan-950/30 text-cyan-300">
                Python 3.12 / LangGraph / Gemini 3.8
              </span>
            </div>
            <p className="text-xs text-zinc-400 leading-relaxed max-w-3xl mb-4">
              The DAGPlanner compiles user instructions into non-cyclic
              execution graphs. LangGraph checkpoints graph state at every
              subtask boundary. The SagaManager orchestrates compensating
              rollback actions if any node experiences an unrecoverable failure.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs text-zinc-300 font-mono">
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • Cycle-free DAG subtask planner
              </div>
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • ModelGateway cost & token limiter
              </div>
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • Saga compensation rollback engine
              </div>
            </div>
          </div>

          {/* Layer 3: Security Governance & Token Minting */}
          <div className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl p-8 hover:border-amber-500/30 transition-all">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-400">
                  <ShieldAlert className="w-5 h-5" />
                </div>
                <div>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-amber-400 font-semibold">
                    Layer 03 · Execution Governance
                  </span>
                  <h2 className="text-xl font-bold text-white">
                    RFC 8785 Single-Use Token Gatekeeper
                  </h2>
                </div>
              </div>
              <span className="text-xs font-mono px-3 py-1 rounded-full border border-amber-500/20 bg-amber-950/30 text-amber-300">
                HMAC-SHA256 / Canonical JSON / TOTP
              </span>
            </div>
            <p className="text-xs text-zinc-400 leading-relaxed max-w-3xl mb-4">
              Specialist agents are strictly blocked from invoking
              APPROVAL_REQUIRED or RESTRICTED tools without a valid
              cryptographic approval token. Tokens are bound to the SHA-256 hash
              of the canonicalized JSON payload and consumed atomically upon
              invocation.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs text-zinc-300 font-mono">
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • Single-use token consumption
              </div>
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • Payload canonicalization (RFC 8785)
              </div>
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • Hardware TOTP step-up authentication
              </div>
            </div>
          </div>

          {/* Layer 4: Storage & External MCP Gateway */}
          <div className="rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl p-8 hover:border-emerald-500/30 transition-all">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
                  <Database className="w-5 h-5" />
                </div>
                <div>
                  <span className="text-[10px] font-mono uppercase tracking-widest text-emerald-400 font-semibold">
                    Layer 04 · Persistence & Sandboxing
                  </span>
                  <h2 className="text-xl font-bold text-white">
                    PostgreSQL RLS, Transactional Outbox & MCP Gateway
                  </h2>
                </div>
              </div>
              <span className="text-xs font-mono px-3 py-1 rounded-full border border-emerald-500/20 bg-emerald-950/30 text-emerald-300">
                PostgreSQL 16 / Redis / Docker Sandbox
              </span>
            </div>
            <p className="text-xs text-zinc-400 leading-relaxed max-w-3xl mb-4">
              Tenant isolation is enforced by PostgreSQL Row-Level Security
              (`app.current_workspace_id`). Subtask dispatch utilizes a
              transactional outbox table with at-least-once queue delivery to
              Redis. MCP servers run under process supervision with schema
              poisoning defense.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs text-zinc-300 font-mono">
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • PostgreSQL Row-Level Security (RLS)
              </div>
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • Transactional outbox event ledger
              </div>
              <div className="p-3 rounded-xl bg-black/40 border border-white/5">
                • Docker ephemeral container runner
              </div>
            </div>
          </div>
        </div>

        {/* Verification Evidence */}
        <div className="rounded-2xl border border-white/5 bg-zinc-900/30 p-8 text-center max-w-3xl mx-auto">
          <CheckCircle2 className="w-6 h-6 text-emerald-400 mx-auto mb-3" />
          <h3 className="text-sm font-bold text-white mb-2">
            Automated Verification Across All 10 Phases
          </h3>
          <p className="text-xs text-zinc-400 leading-relaxed mb-6">
            Over 1,000 backend integration tests and 900+ frontend unit tests
            verify this architecture on every commit. No mocks are used in
            production verification.
          </p>
          <Link
            href="/workspace"
            className="inline-flex items-center gap-2 px-6 py-2.5 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-semibold text-xs shadow-lg shadow-teal-500/20 transition-all hover:scale-105"
          >
            <span>Test the Architecture in Console</span>
            <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </main>

      <PublicFooter />
    </div>
  );
}
