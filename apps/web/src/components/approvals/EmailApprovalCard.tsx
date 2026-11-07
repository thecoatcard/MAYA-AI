"use client";

import React from "react";
import { Mail, FileText } from "lucide-react";

export interface EmailApprovalCardProps {
  to: string;
  subject: string;
  body: string;
  draftId?: string;
  className?: string;
}

export function EmailApprovalCard({
  to,
  subject,
  body,
  draftId,
  className = "",
}: EmailApprovalCardProps): React.JSX.Element {
  return (
    <div
      className={`border rounded-lg overflow-hidden border-zinc-800 bg-zinc-950 text-sm ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3.5 py-2.5 bg-zinc-900 border-b border-zinc-800">
        <div className="flex items-center gap-2 text-zinc-300 font-medium">
          <Mail className="w-4 h-4 text-sky-400" />
          <span>Email Dispatch Review</span>
        </div>
        {draftId && (
          <span className="text-xs font-mono text-zinc-500 bg-zinc-800/80 px-2 py-0.5 rounded">
            Draft: {draftId}
          </span>
        )}
      </div>

      {/* Fields */}
      <div className="p-3.5 space-y-2.5">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-zinc-500 w-12 text-right">
            To:
          </span>
          <span className="font-mono text-zinc-200 bg-zinc-900 px-2 py-0.5 rounded border border-zinc-800">
            {to}
          </span>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-zinc-500 w-12 text-right">
            Subject:
          </span>
          <span className="font-medium text-zinc-100">{subject}</span>
        </div>

        <div className="pt-2 border-t border-zinc-900">
          <div className="text-xs font-semibold text-zinc-500 mb-1.5 flex items-center gap-1.5">
            <FileText className="w-3.5 h-3.5" />
            <span>Message Body</span>
          </div>
          <div className="p-3 rounded bg-zinc-900/60 border border-zinc-800/80 text-zinc-300 text-xs font-mono whitespace-pre-wrap leading-relaxed max-h-60 overflow-y-auto">
            {body}
          </div>
        </div>
      </div>
    </div>
  );
}
