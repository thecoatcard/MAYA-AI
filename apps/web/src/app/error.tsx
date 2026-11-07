"use client";

import { AlertTriangle, Home, RefreshCw } from "lucide-react";
import Link from "next/link";
import React, { useEffect } from "react";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.JSX.Element {
  useEffect(() => {
    // Log unexpected runtime client errors safely
  }, [error]);

  return (
    <div className="min-h-screen w-full flex items-center justify-center p-6 bg-zinc-950 text-zinc-100 font-sans relative overflow-hidden">
      {/* Ambient background glows */}
      <div className="absolute top-1/4 left-1/3 w-96 h-96 bg-rose-500/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/3 w-96 h-96 bg-amber-500/10 rounded-full blur-3xl pointer-events-none" />

      <div className="max-w-md w-full relative z-10 p-8 rounded-2xl border border-rose-500/20 bg-zinc-900/60 backdrop-blur-xl shadow-2xl text-center">
        <div className="inline-flex p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 mb-6">
          <AlertTriangle className="w-8 h-8" />
        </div>

        <p className="text-xs font-mono tracking-widest uppercase text-rose-400 font-semibold mb-2">
          Runtime Exception
        </p>

        <h1 className="text-2xl font-bold tracking-tight text-white mb-3">
          Workspace Interruption
        </h1>

        <p className="text-sm text-zinc-400 mb-6 leading-relaxed">
          An unexpected error interrupted current operations. Your
          authentication session and data integrity remain secure.
        </p>

        {error.message && (
          <div className="mb-6 p-3 rounded-lg bg-black/40 border border-white/5 text-left text-xs font-mono text-zinc-400 overflow-x-auto max-h-24">
            {error.message}
          </div>
        )}

        <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => reset()}
            className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-semibold text-xs shadow-lg shadow-teal-500/20 transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <RefreshCw className="w-4 h-4" />
            <span>Attempt Recovery</span>
          </button>
          <Link
            href="/workspace"
            className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-zinc-200 text-xs font-medium transition-all"
          >
            <Home className="w-4 h-4" />
            <span>Back to Console</span>
          </Link>
        </div>
      </div>
    </div>
  );
}
