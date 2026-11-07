import { Layers3 } from "lucide-react";
import React from "react";

export default function Loading(): React.JSX.Element {
  return (
    <div className="min-h-screen w-full flex flex-col items-center justify-center p-6 bg-zinc-950 text-zinc-100 font-sans relative overflow-hidden">
      {/* Ambient background glow */}
      <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-80 h-80 bg-teal-500/10 rounded-full blur-3xl pointer-events-none" />

      <div className="relative z-10 flex flex-col items-center">
        <div className="relative p-3 rounded-2xl bg-zinc-900/80 border border-white/10 shadow-2xl mb-4">
          <Layers3 className="w-8 h-8 text-teal-400 animate-pulse" />
          <div className="absolute inset-0 rounded-2xl border border-teal-500/30 animate-ping pointer-events-none" />
        </div>

        <p className="text-xs font-mono tracking-widest uppercase text-zinc-400">
          Loading Workspace
        </p>
      </div>
    </div>
  );
}
