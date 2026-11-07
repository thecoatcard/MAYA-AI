import {
  Check,
  HelpCircle,
  ShieldCheck,
  Sparkles,
  Terminal,
} from "lucide-react";
import Link from "next/link";
import React from "react";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PublicNav } from "../../components/layout/PublicNav";

export const metadata = {
  title: "Deployment & Plans | MAYA AI",
  description:
    "Transparent deployment models for MAYA AI digital employee: Open-source self-hosted community vs Enterprise dedicated cloud.",
};

export default function PricingPage(): React.JSX.Element {
  return (
    <div className="min-h-screen flex flex-col bg-zinc-950 text-zinc-100 font-sans relative overflow-x-hidden">
      {/* Background radial gradients */}
      <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-teal-500/10 rounded-full blur-[120px] pointer-events-none" />
      <div className="absolute top-1/3 right-1/4 w-[500px] h-[500px] bg-cyan-500/10 rounded-full blur-[100px] pointer-events-none" />

      <PublicNav />

      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-16 relative z-10">
        {/* Header */}
        <div className="text-center max-w-3xl mx-auto mb-16">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-teal-500/30 bg-teal-500/10 text-teal-300 text-xs font-mono mb-4">
            <Sparkles className="w-3.5 h-3.5" />
            <span>Deterministic AI Architecture</span>
          </div>
          <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-white mb-4">
            Autonomous Digital Employee, <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-teal-400 via-cyan-300 to-emerald-400">
              Zero License Lock-in.
            </span>
          </h1>
          <p className="text-base text-zinc-400 leading-relaxed">
            Deploy MAYA AI into your own sovereign infrastructure. Fully
            open-source core with enterprise security extensions for
            multi-tenant isolation and hardware token governance.
          </p>
        </div>

        {/* Pricing Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8 max-w-4xl mx-auto mb-20">
          {/* Community Edition */}
          <div className="rounded-2xl border border-white/10 bg-zinc-900/50 backdrop-blur-xl p-8 flex flex-col relative group hover:border-teal-500/30 transition-all duration-300">
            <div className="mb-6">
              <span className="text-xs font-mono uppercase tracking-widest text-teal-400 font-semibold">
                Self-Hosted Community
              </span>
              <h2 className="text-2xl font-bold text-white mt-1">
                Community Core
              </h2>
              <p className="text-xs text-zinc-400 mt-2">
                For autonomous engineers, small teams, and researchers running
                in local Docker or Kubernetes.
              </p>
              <div className="mt-6 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-white">$0</span>
                <span className="text-xs text-zinc-500">
                  / forever free & open source
                </span>
              </div>
            </div>

            <div className="space-y-3 flex-1 mb-8 text-xs text-zinc-300">
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>
                  Full 6 Specialist Agent Fleet (Research, Coding, Document,
                  Email, PM, Orchestrator)
                </span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>LangGraph DAG Execution Engine & Saga Compensations</span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>RFC 8785 Payload Diff & Single-Use Approval Tokens</span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>Brave Search, GitHub, Notion & Google Adapters</span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>
                  Local Docker Sandbox for test reproduction & patch
                  verification
                </span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>PostgreSQL RLS Multi-Tenant Data Isolation</span>
              </div>
            </div>

            <Link
              href="/workspace"
              className="w-full inline-flex items-center justify-center gap-2 py-3 px-4 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-white font-semibold text-xs transition-all"
            >
              <Terminal className="w-4 h-4" />
              <span>Launch Local Console</span>
            </Link>
          </div>

          {/* Enterprise Edition */}
          <div className="rounded-2xl border border-teal-500/40 bg-gradient-to-b from-teal-950/20 via-zinc-900/60 to-zinc-900/40 backdrop-blur-xl p-8 flex flex-col relative shadow-xl shadow-teal-950/20 group hover:border-teal-400 transition-all duration-300">
            <div className="absolute -top-3 right-6 px-3 py-0.5 rounded-full bg-gradient-to-r from-teal-500 to-emerald-400 text-zinc-950 text-[10px] font-bold tracking-wide uppercase">
              Enterprise Ready
            </div>

            <div className="mb-6">
              <span className="text-xs font-mono uppercase tracking-widest text-teal-400 font-semibold">
                Sovereign Enterprise
              </span>
              <h2 className="text-2xl font-bold text-white mt-1">
                Enterprise Cloud
              </h2>
              <p className="text-xs text-zinc-400 mt-2">
                For organizations requiring dedicated air-gapped deployments,
                hardware security tokens, and SOC2 audit compliance.
              </p>
              <div className="mt-6 flex items-baseline gap-1">
                <span className="text-4xl font-extrabold text-white">
                  Custom
                </span>
                <span className="text-xs text-zinc-500">
                  / annual deployment contract
                </span>
              </div>
            </div>

            <div className="space-y-3 flex-1 mb-8 text-xs text-zinc-300">
              <div className="flex items-center gap-2.5 font-medium text-teal-300">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>Everything in Community Core, plus:</span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>
                  Hardware Token & Step-up TOTP Governance for RESTRICTED
                  actions
                </span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>
                  Tamper-Evident Hash-Chained Audit Ledger Export (SOC2 / ISO
                  27001)
                </span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>
                  Custom LLM Gateway (Private Bedrock, Azure OpenAI, self-hosted
                  Ollama/vLLM)
                </span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>
                  Dedicated MCP Gateway instance with custom internal tool
                  connectors
                </span>
              </div>
              <div className="flex items-center gap-2.5">
                <Check className="w-4 h-4 text-teal-400 shrink-0" />
                <span>24/7 Architectural Support & Enterprise SLA</span>
              </div>
            </div>

            <Link
              href="/contact"
              className="w-full inline-flex items-center justify-center gap-2 py-3 px-4 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-bold text-xs shadow-lg shadow-teal-500/20 transition-all hover:scale-[1.01]"
            >
              <ShieldCheck className="w-4 h-4" />
              <span>Contact Enterprise Team</span>
            </Link>
          </div>
        </div>

        {/* Feature Matrix / FAQ Preview */}
        <div className="rounded-2xl border border-white/5 bg-zinc-900/30 backdrop-blur-md p-8 text-center max-w-2xl mx-auto">
          <HelpCircle className="w-6 h-6 text-teal-400 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-white mb-2">
            Have specific deployment constraints?
          </h3>
          <p className="text-xs text-zinc-400 mb-6 leading-relaxed">
            MAYA AI is engineered to be deployed on-premise, in sovereign VPCs,
            or hybrid environments without phone-home telemetry.
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link
              href="/architecture"
              className="text-xs text-teal-400 hover:text-teal-300 font-semibold underline underline-offset-4"
            >
              Review Architecture &rarr;
            </Link>
            <Link
              href="/docs"
              className="text-xs text-zinc-400 hover:text-zinc-200 font-medium"
            >
              Read Deployment Guide
            </Link>
          </div>
        </div>
      </main>

      <PublicFooter />
    </div>
  );
}
