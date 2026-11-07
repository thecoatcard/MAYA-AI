import { ArrowLeft, Home, ShieldAlert } from "lucide-react";
import Link from "next/link";
import React from "react";

export default function NotFound(): React.JSX.Element {
  return (
    <div className="min-h-screen w-full flex items-center justify-center p-6 bg-zinc-950 text-zinc-100 font-sans relative overflow-hidden">
      {/* Ambient background glows */}
      <div className="absolute top-1/4 left-1/3 w-96 h-96 bg-teal-500/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/3 w-96 h-96 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />

      <div className="max-w-md w-full relative z-10 p-8 rounded-2xl border border-white/10 bg-zinc-900/60 backdrop-blur-xl shadow-2xl text-center">
        <div className="inline-flex p-3 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-400 mb-6">
          <ShieldAlert className="w-8 h-8" />
        </div>

        <p className="text-xs font-mono tracking-widest uppercase text-teal-400 font-semibold mb-2">
          404 · Page Not Found
        </p>

        <h1 className="text-2xl font-bold tracking-tight text-white mb-3">
          Lost in Ambient Space
        </h1>

        <p className="text-sm text-zinc-400 mb-8 leading-relaxed">
          The requested coordinate does not exist or has been relocated to
          another workspace partition.
        </p>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
          <Link
            href="/workspace"
            className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-teal-500 hover:bg-teal-400 text-zinc-950 font-semibold text-xs shadow-lg shadow-teal-500/20 transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <Home className="w-4 h-4" />
            <span>Go to Workspace</span>
          </Link>
          <Link
            href="/"
            className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-zinc-200 text-xs font-medium transition-all"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Return Home</span>
          </Link>
        </div>
      </div>
    </div>
  );
}
