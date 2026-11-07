import { GitFork, Layers3, Shield } from "lucide-react";
import Link from "next/link";
import React from "react";

export function PublicFooter(): React.JSX.Element {
  return (
    <footer className="w-full border-t border-white/5 bg-zinc-950/80 backdrop-blur-md text-zinc-400 font-sans mt-auto">
      <div className="max-w-7xl mx-auto px-6 py-12">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8 mb-12">
          {/* Brand Col */}
          <div className="md:col-span-1 flex flex-col gap-3">
            <Link
              href="/"
              className="flex items-center gap-2.5 font-bold tracking-tight text-white hover:text-teal-400 transition-colors"
            >
              <div className="p-1.5 rounded-lg bg-teal-500/10 border border-teal-500/30 text-teal-400">
                <Layers3 className="w-4 h-4" />
              </div>
              <span>MAYA AI</span>
            </Link>
            <p className="text-xs text-zinc-400 leading-relaxed">
              A voice-first, multi-agent digital employee that asks before it
              acts. Engineered with cryptographic guardrails and deterministic
              execution proofs.
            </p>
            <div className="flex items-center gap-2 text-xs font-mono text-zinc-400">
              <Shield className="w-3.5 h-3.5 text-teal-400" />
              <span>Phases 01–09 Verified</span>
            </div>
          </div>

          {/* Product Col */}
          <div className="flex flex-col gap-2.5 text-xs">
            <span className="font-semibold text-zinc-200 uppercase tracking-wider text-[11px]">
              Platform
            </span>
            <Link
              href="/features"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              Specialist Agent Fleet
            </Link>
            <Link
              href="/architecture"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              System Architecture
            </Link>
            <Link
              href="/pricing"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              Deployment & Plans
            </Link>
            <Link
              href="/workspace"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              Ambient Console
            </Link>
          </div>

          {/* Security & Governance */}
          <div className="flex flex-col gap-2.5 text-xs">
            <span className="font-semibold text-zinc-200 uppercase tracking-wider text-[11px]">
              Security & Trust
            </span>
            <Link
              href="/approvals"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              RFC 8785 Approvals
            </Link>
            <Link
              href="/activity"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              Cryptographic Audit Ledger
            </Link>
            <Link
              href="/integrations"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              MCP Gateway Isolation
            </Link>
            <Link
              href="/docs"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              Verification Proofs
            </Link>
          </div>

          {/* Developer & Community */}
          <div className="flex flex-col gap-2.5 text-xs">
            <span className="font-semibold text-zinc-200 uppercase tracking-wider text-[11px]">
              Resources
            </span>
            <Link
              href="/docs"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              Documentation & API
            </Link>
            <Link
              href="/contact"
              className="text-zinc-400 hover:text-teal-300 transition-colors"
            >
              Security Disclosure & Contact
            </Link>
            <a
              href="https://github.com"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 text-zinc-400 hover:text-teal-300 transition-colors"
            >
              <GitFork className="w-3.5 h-3.5" />
              <span>Source Repository</span>
            </a>
          </div>
        </div>

        {/* Bottom bar */}
        <div className="pt-8 border-t border-white/5 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-zinc-400">
          <p>
            © {new Date().getFullYear()} MAYA AI. Architectural integrity,
            deterministic safety.
          </p>
          <div className="flex items-center gap-6">
            <Link
              href="/docs"
              className="hover:text-zinc-300 transition-colors"
            >
              Privacy & Retention
            </Link>
            <Link
              href="/contact"
              className="hover:text-zinc-300 transition-colors"
            >
              Security
            </Link>
            <Link
              href="/docs"
              className="hover:text-zinc-300 transition-colors"
            >
              Terms
            </Link>
          </div>
        </div>
      </div>
    </footer>
  );
}
