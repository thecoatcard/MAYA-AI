import React from "react";
import { PublicFooter } from "../layout/PublicFooter";
import { PublicNav } from "../layout/PublicNav";

export interface AuthPageLayoutProps {
  children: React.ReactNode;
  maxWidth?: string;
}

export function AuthPageLayout({
  children,
  maxWidth = "max-w-md",
}: AuthPageLayoutProps): React.JSX.Element {
  return (
    <div className="min-h-screen flex flex-col bg-zinc-950 text-zinc-100 font-sans relative overflow-x-hidden selection:bg-teal-500/30 selection:text-teal-200">
      {/* Ambient background glow effects */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-40 right-1/4 w-[600px] h-[600px] bg-teal-500/10 rounded-full blur-[140px]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute bottom-1/4 left-1/6 w-[500px] h-[500px] bg-cyan-500/10 rounded-full blur-[130px]"
      />

      <PublicNav />

      <main className="flex-1 flex items-center justify-center px-4 sm:px-6 py-12 sm:py-16 relative z-10 w-full">
        <div
          className={`w-full ${maxWidth} rounded-2xl border border-white/10 bg-zinc-900/60 backdrop-blur-2xl p-6 sm:p-8 shadow-2xl shadow-black/50 relative shadow-[inset_0_1px_0_0_rgba(255,255,255,0.08)]`}
        >
          {children}
        </div>
      </main>

      <PublicFooter />
    </div>
  );
}
