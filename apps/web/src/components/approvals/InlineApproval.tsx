"use client";

import { ClipboardCopy, ShieldCheck, X } from "lucide-react";
import React, { useContext, useEffect, useRef, useState } from "react";

import {
  decideApproval,
  type ApprovalRequest,
} from "../../lib/approvals-client";
import {
  Button,
  CodeBlockShell,
  MenuSurface,
  Notice,
  WrapText,
  type ContextMenuEntry,
} from "../ui";
import { copyText } from "../../lib/menu-actions";
import { ConversationContext } from "../chat/ConversationContext";
import { VOICE_APPROVAL_NOTICE } from "../voice/voice-copy";
import { TierBadge, formatDateTime, isExpired } from "./ApprovalParts";
import { EmailApprovalCard } from "./EmailApprovalCard";
import { GitDiffViewer } from "./GitDiffViewer";
import { NOTION_WRITE_TOOLS, NotionWritePreview } from "./NotionWritePreview";

export interface InlineApprovalProps {
  readonly approval: ApprovalRequest;
  /** Called after the server recorded the decision (the caller refetches its task). */
  readonly onDecided?: (
    approvalId: string,
    status: "approved" | "rejected" | "revoked" | "denied",
  ) => void;
}

const PAYLOAD_BLOCK = "whitespace-pre-wrap [overflow-wrap:anywhere]";

function json(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function diffTextOf(approval: ApprovalRequest): string | null {
  const diff = approval.payload_diff;
  if (typeof diff === "string") return diff;
  if (
    diff !== null &&
    diff !== undefined &&
    typeof diff === "object" &&
    !Array.isArray(diff) &&
    typeof (diff as { diff?: unknown }).diff === "string"
  ) {
    return (diff as { diff: string }).diff;
  }
  return null;
}

/**
 * The approval, right where the task is (a chat task card or a task on the Tasks page). It shows
 * what will happen in plain words, the exact payload on demand, and the only two things that
 * decide it: Approve and Reject. Both go through the existing approvals endpoint
 * (`decideApproval`), so the server still checks the user, the workspace and, for a restricted
 * action, the authenticator code that is asked for here. Typing "yes" anywhere approves nothing.
 * Nothing animates near the buttons (`motion-none`).
 */
export function InlineApproval({
  approval,
  onDecided,
}: InlineApprovalProps): React.JSX.Element {
  // Shown only while voice is on (the context is absent outside the conversation runtime).
  const voiceOn = useContext(ConversationContext)?.voiceLive === true;
  const [showPayload, setShowPayload] = useState(false);
  const [submitting, setSubmitting] = useState<"approve" | "reject" | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    setError(null);
    setCode("");
    setDone(null);
  }, [approval.id]);

  const args = approval.tool_args ?? {};
  const pending = approval.status === "pending" && done === null;
  const restricted = approval.tier === "RESTRICTED";
  const expired = isExpired(approval);
  const busy = submitting !== null;
  const isEmail = /email|gmail/i.test(approval.tool_name);
  const isNotion = NOTION_WRITE_TOOLS.includes(approval.tool_name);
  const isFileReplace = approval.tool_name === "file_replace_original";
  const isTerminalCommand =
    approval.tool_name === "workspace_run_command" ||
    approval.tool_name === "terminal_run_command" ||
    approval.tool_name === "run_command";
  const isWorkspaceDelete =
    approval.tool_name === "workspace_delete_file" ||
    approval.tool_name === "workspace_delete_dir";
  const diffText = diffTextOf(approval);
  const replacePath = typeof args.path === "string" ? args.path : undefined;
  const replaceNew =
    typeof args.new_version === "string" ? args.new_version : undefined;

  const decide = async (action: "approve" | "reject"): Promise<void> => {
    if (busy) return;
    setSubmitting(action);
    setError(null);
    try {
      const res = await decideApproval(approval.id, {
        action,
        ...(action === "approve" && restricted ? { totp_code: code } : {}),
      });
      if (!mounted.current) return;
      setDone(
        action === "approve"
          ? "Approved. It runs once, for exactly this request."
          : "Rejected. The decision was recorded.",
      );
      onDecided?.(approval.id, res.status);
    } catch (err) {
      if (!mounted.current) return;
      setError(
        err instanceof Error && err.message !== ""
          ? err.message
          : "The decision could not be recorded. Try again.",
      );
    } finally {
      if (mounted.current) setSubmitting(null);
    }
  };

  // The menu never offers Approve: approving stays on the button, and only the button.
  const menuItems = (): ContextMenuEntry[] => [
    {
      id: "copy-payload",
      label: "Copy exact payload",
      icon: <ClipboardCopy size={14} />,
      onSelect: () =>
        void copyText(
          json({
            tool_name: approval.tool_name,
            tool_args: approval.tool_args ?? {},
            ...(approval.payload_diff
              ? { payload_diff: approval.payload_diff }
              : {}),
          }),
        ),
    },
    {
      id: "reject",
      label: "Reject",
      icon: <X size={14} />,
      danger: true,
      disabled: !pending || busy || expired,
      disabledReason: expired
        ? "This request has expired."
        : "This request is already decided.",
      onSelect: () => void decide("reject"),
    },
  ];

  return (
    <MenuSurface
      as="section"
      label={`approval for ${approval.tool_name}`}
      getItems={menuItems}
      aria-label={`Approval needed: ${approval.tool_name}`}
      data-testid="inline-approval"
      data-pending={pending ? "true" : "false"}
      className="motion-none flex min-w-0 flex-col gap-2 rounded-maya-lg border border-maya-warning bg-maya-warning-soft p-3 text-maya-text"
    >
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 text-maya-small font-medium">
          <ShieldCheck
            aria-hidden="true"
            size={16}
            className="shrink-0 text-maya-warning"
          />
          What will happen if you approve
        </span>
        <TierBadge tier={approval.tier} />
      </div>
      {approval.action_description !== "" && (
        <WrapText
          as="p"
          dir="auto"
          className="m-0 block text-maya-small text-maya-text"
        >
          {approval.action_description}
        </WrapText>
      )}

      {isEmail && (
        <EmailApprovalCard
          to={String(args.to || args.recipient || "")}
          subject={String(args.subject || "")}
          body={String(args.body || args.message || "")}
          draftId={
            typeof args.draft_id === "string" && args.draft_id !== ""
              ? args.draft_id
              : undefined
          }
        />
      )}
      {isNotion && (
        <div className="min-w-0">
          <h3 className="maya-label m-0 mb-1">
            What will be written to Notion
          </h3>
          <NotionWritePreview args={args} />
        </div>
      )}
      {isFileReplace && (
        <div className="min-w-0">
          <h3 className="maya-label m-0 mb-1">Replace the original file</h3>
          <dl className="m-0 grid min-w-0 grid-cols-[repeat(auto-fit,minmax(14rem,1fr))] gap-x-4 gap-y-2 text-maya-small">
            <div className="min-w-0">
              <dt className="maya-label">Original (will be replaced)</dt>
              <dd className="m-0 font-mono text-maya-mono [overflow-wrap:anywhere]">
                {replacePath ?? "Not given"}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="maya-label">New version (kept next to it)</dt>
              <dd className="m-0 font-mono text-maya-mono [overflow-wrap:anywhere]">
                {replaceNew ?? "Not given"}
              </dd>
            </div>
          </dl>
          <p className="m-0 mt-1 text-maya-small text-maya-secondary">
            Approving writes the new version over the original. The original
            file&apos;s content is lost unless you have your own copy.
          </p>
        </div>
      )}
      {isTerminalCommand && (
        <div className="min-w-0">
          <h3 className="maya-label m-0 mb-1">Terminal Command to Execute</h3>
          <pre className="rounded-maya-md bg-maya-bg p-2 font-mono text-maya-small text-maya-accent [overflow-wrap:anywhere] whitespace-pre-wrap">
            $ {String(args.command || "")}
          </pre>
        </div>
      )}
      {isWorkspaceDelete && (
        <div className="min-w-0">
          <h3 className="maya-label m-0 mb-1">Target to Delete</h3>
          <p className="m-0 font-mono text-maya-small text-maya-danger [overflow-wrap:anywhere]">
            {String(args.path || "")}
          </p>
        </div>
      )}
      {approval.payload_diff && (
        <div className="min-w-0">
          <h3 className="maya-label m-0 mb-1">What will change</h3>
          {diffText !== null ? (
            <GitDiffViewer diffText={diffText} filePath={replacePath} />
          ) : (
            <CodeBlockShell
              label="Payload preview"
              maxHeight="16rem"
              className={PAYLOAD_BLOCK}
            >
              {json(approval.payload_diff)}
            </CodeBlockShell>
          )}
        </div>
      )}

      <div className="min-w-0">
        <button
          type="button"
          aria-expanded={showPayload}
          onClick={() => setShowPayload((open) => !open)}
          className="text-maya-small text-maya-secondary underline hover:text-maya-text"
        >
          {showPayload ? "Hide exact payload" : "Show exact payload"}
        </button>
        {showPayload && (
          <div className="mt-1">
            <CodeBlockShell
              label="Exact tool arguments"
              maxHeight="20rem"
              dir="auto"
              className={PAYLOAD_BLOCK}
            >
              {json(args)}
            </CodeBlockShell>
            <p className="m-0 mt-1 text-maya-caption text-maya-muted">
              Tool: {approval.tool_name}. Requested{" "}
              {formatDateTime(approval.requested_at)}. Approving gives a
              single-use permission for exactly this payload.
            </p>
          </div>
        )}
      </div>

      {done !== null && <Notice tone="success">{done}</Notice>}
      {error !== null && (
        <Notice tone="danger" onDismiss={() => setError(null)}>
          {error}
        </Notice>
      )}
      {pending && (
        <>
          {expired && (
            <p role="status" className="m-0 text-maya-small text-maya-warning">
              This request has expired. The server will refuse a decision.
            </p>
          )}
          {restricted && (
            <div className="flex min-w-0 flex-col gap-1">
              <label
                htmlFor={`approval-code-${approval.id}`}
                className="text-maya-caption font-medium text-maya-text"
              >
                Authenticator code (6 digits)
              </label>
              <input
                id={`approval-code-${approval.id}`}
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                placeholder="000000"
                className="maya-tabular w-40 rounded-maya-md border border-maya-control-border bg-maya-pane px-3 py-2 text-center font-mono text-maya-body text-maya-text"
              />
            </div>
          )}
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              disabled={busy || (restricted && code.length !== 6)}
              loading={submitting === "approve"}
              onClick={() => void decide("approve")}
            >
              Approve
            </Button>
            <Button
              variant="danger"
              size="sm"
              disabled={busy}
              loading={submitting === "reject"}
              onClick={() => void decide("reject")}
            >
              Reject
            </Button>
          </div>
          <p className="m-0 text-maya-caption text-maya-muted">
            Only these buttons approve. Typing or saying &quot;yes&quot; does
            not.
          </p>
          {voiceOn && (
            <p
              className="m-0 text-maya-caption text-maya-muted"
              data-testid="voice-approval-notice"
            >
              {VOICE_APPROVAL_NOTICE}
            </p>
          )}
        </>
      )}
    </MenuSurface>
  );
}
