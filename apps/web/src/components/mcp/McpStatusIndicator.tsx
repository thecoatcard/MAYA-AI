"use client";

import React from "react";
import { Server, ShieldCheck, AlertTriangle, AlertOctagon } from "lucide-react";

export interface McpStatusIndicatorProps {
  healthStatus?: "healthy" | "degraded" | "dead" | string;
  activeServersCount?: number;
  totalToolsCount?: number;
  onClick?: () => void;
  className?: string;
}

export function McpStatusIndicator({
  healthStatus = "healthy",
  activeServersCount = 1,
  totalToolsCount = 6,
  onClick,
  className = "",
}: McpStatusIndicatorProps): React.JSX.Element {
  const normStatus = healthStatus.toLowerCase();

  const getStatusConfig = () => {
    switch (normStatus) {
      case "healthy":
        return {
          label: "MCP Gateway: Healthy",
          dotClass: "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.7)]",
          badgeClass:
            "border-emerald-500/30 text-emerald-300 bg-emerald-950/30 hover:bg-emerald-950/50 shadow-sm shadow-emerald-500/10",
          icon: <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />,
        };
      case "degraded":
        return {
          label: "MCP Gateway: Degraded",
          dotClass:
            "bg-amber-400 animate-pulse shadow-[0_0_8px_rgba(251,191,36,0.7)]",
          badgeClass:
            "border-amber-500/30 text-amber-300 bg-amber-950/30 hover:bg-amber-950/50 shadow-sm shadow-amber-500/10",
          icon: <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />,
        };
      case "dead":
      default:
        return {
          label: "MCP Gateway: Offline",
          dotClass: "bg-rose-400 shadow-[0_0_8px_rgba(244,63,94,0.7)]",
          badgeClass:
            "border-rose-500/30 text-rose-300 bg-rose-950/30 hover:bg-rose-950/50 shadow-sm shadow-rose-500/10",
          icon: <AlertOctagon className="w-3.5 h-3.5 text-rose-400" />,
        };
    }
  };

  const config = getStatusConfig();

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={config.label}
      role="status"
      className={`group inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono font-medium border backdrop-blur-md transition-all duration-200 cursor-pointer ${config.badgeClass} ${className}`}
    >
      <span
        className={`w-2 h-2 rounded-full ${config.dotClass}`}
        aria-hidden="true"
      />
      <span className="flex items-center gap-1.5 text-zinc-200">
        <Server className="w-3 h-3 text-teal-400/80 group-hover:text-teal-300" />
        <span>{activeServersCount} srv</span>
      </span>
      <span className="text-zinc-600 dark:text-zinc-500">/</span>
      <span className="text-zinc-300 group-hover:text-white transition-colors">
        {totalToolsCount} tools
      </span>
    </button>
  );
}
