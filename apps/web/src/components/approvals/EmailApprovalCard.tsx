"use client";

import { FileText, Mail } from "lucide-react";
import React from "react";

import { WrapText } from "../ui";

export interface EmailApprovalCardProps {
  to: string;
  subject: string;
  body: string;
  draftId?: string;
  className?: string;
}

/** The email exactly as it would be sent. Static: no animation near the decision controls. */
export function EmailApprovalCard({
  to,
  subject,
  body,
  draftId,
  className = "",
}: EmailApprovalCardProps): React.JSX.Element {
  return (
    <div
      className={`min-w-0 max-w-full rounded-maya-lg overflow-hidden border border-maya-border bg-maya-pane text-maya-small text-maya-text ${className}`.trim()}
    >
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-maya-border bg-maya-raised px-3 py-2">
        <span className="flex min-w-0 items-center gap-2 font-medium">
          <Mail
            aria-hidden="true"
            size={16}
            className="shrink-0 text-maya-secondary"
          />
          <span>Email to be sent</span>
        </span>
        {draftId && (
          <WrapText className="font-mono text-maya-caption text-maya-muted">
            Draft: {draftId}
          </WrapText>
        )}
      </div>

      <dl className="m-0 grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 p-3">
        <dt className="text-maya-caption font-medium text-maya-muted">To</dt>
        <dd className="m-0 min-w-0">
          <WrapText dir="auto" className="font-mono text-maya-mono">
            {to}
          </WrapText>
        </dd>
        <dt className="text-maya-caption font-medium text-maya-muted">
          Subject
        </dt>
        <dd className="m-0 min-w-0">
          <WrapText dir="auto" className="font-medium">
            {subject}
          </WrapText>
        </dd>
      </dl>

      <div className="min-w-0 border-t border-maya-border p-3">
        <p className="m-0 mb-1.5 flex items-center gap-1.5 text-maya-caption font-medium text-maya-muted">
          <FileText aria-hidden="true" size={14} />
          <span>Message body</span>
        </p>
        <WrapText
          as="div"
          dir="auto"
          role="region"
          aria-label="Email message body"
          tabIndex={0}
          className="block max-h-72 overflow-y-auto rounded-maya-md border border-maya-border bg-maya-code p-3 font-mono text-maya-mono leading-relaxed"
        >
          {body}
        </WrapText>
      </div>
    </div>
  );
}
