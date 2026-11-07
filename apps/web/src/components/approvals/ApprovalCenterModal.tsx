"use client";

import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  FileCode,
  KeyRound,
  Loader2,
  Lock,
  RotateCw,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import React, { useCallback, useEffect, useState } from "react";

import {
  decideApproval,
  listApprovals,
  type ApprovalRequest,
} from "../../lib/approvals-client";
import { GitDiffViewer } from "./GitDiffViewer";
import { EmailApprovalCard } from "./EmailApprovalCard";

export interface ApprovalCenterModalProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly initialApprovalId?: string | null;
  readonly onDecisionMade?: (
    approvalId: string,
    status: "approved" | "denied" | "revoked" | "rejected",
  ) => void;
}

export function ApprovalCenterModal({
  isOpen,
  onClose,
  initialApprovalId,
  onDecisionMade,
}: ApprovalCenterModalProps) {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialApprovalId ?? null,
  );
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [submittingAction, setSubmittingAction] = useState<
    "approve" | "reject" | "revoke" | null
  >(null);
  const submitting = submittingAction !== null;
  const modalRef = React.useRef<HTMLDivElement>(null);

  // Step-up TOTP state
  const [showTotpModal, setShowTotpModal] = useState<boolean>(false);
  const [totpCode, setTotpCode] = useState<string>("");
  const [totpError, setTotpError] = useState<string | null>(null);
  const [copiedHash, setCopiedHash] = useState<boolean>(false);
  const [copiedToken, setCopiedToken] = useState<boolean>(false);

  // Fetch pending approvals
  const loadApprovals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listApprovals("pending");
      setApprovals(data);
      if (data.length > 0 && !selectedId) {
        setSelectedId(data[0].id);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load approval requests",
      );
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    if (isOpen) {
      loadApprovals();
    } else {
      setApprovals([]);
      setSelectedId(null);
      setError(null);
      setFeedbackMessage(null);
      setShowTotpModal(false);
      setTotpCode("");
      setTotpError(null);
    }
  }, [isOpen, loadApprovals]);

  // ESC to close and focus trap
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        if (showTotpModal) {
          setShowTotpModal(false);
        } else {
          onClose();
        }
      }
      if (e.key === "Tab" && isOpen && modalRef.current) {
        const focusable = modalRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey) {
          if (document.activeElement === first) {
            last.focus();
            e.preventDefault();
          }
        } else {
          if (document.activeElement === last) {
            first.focus();
            e.preventDefault();
          }
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, showTotpModal, onClose]);

  const selectedApproval = approvals.find((a) => a.id === selectedId) || null;

  const handleCopyHash = (hash: string) => {
    navigator.clipboard.writeText(hash).then(() => {
      setCopiedHash(true);
      setTimeout(() => setCopiedHash(false), 2000);
    });
  };

  const handleCopyToken = (tok: string) => {
    navigator.clipboard.writeText(tok).then(() => {
      setCopiedToken(true);
      setTimeout(() => setCopiedToken(false), 2000);
    });
  };

  const handleApproveClick = () => {
    if (!selectedApproval) return;
    if (selectedApproval.tier === "RESTRICTED") {
      setShowTotpModal(true);
      setTotpCode("");
      setTotpError(null);
    } else {
      executeDecision("approve");
    }
  };

  const executeDecision = async (
    action: "approve" | "reject" | "revoke",
    code?: string,
  ) => {
    if (!selectedApproval || submittingAction !== null) return;
    setSubmittingAction(action);
    setError(null);
    setTotpError(null);

    try {
      const res = await decideApproval(selectedApproval.id, {
        action,
        totp_code: code,
      });

      // Set instant feedback message
      if (action === "approve") {
        setFeedbackMessage(
          `Single-use execution token minted successfully for ${selectedApproval.tool_name}.`,
        );
      } else if (action === "reject") {
        setFeedbackMessage(
          `Action ${selectedApproval.tool_name} rejected. Decision recorded in ledger.`,
        );
      } else {
        setFeedbackMessage(`Request ${selectedApproval.id} revoked.`);
      }

      // Remove decided approval from list
      setApprovals((prev) => prev.filter((a) => a.id !== selectedApproval.id));
      if (onDecisionMade) {
        onDecisionMade(selectedApproval.id, res.status);
      }

      setShowTotpModal(false);
      const remaining = approvals.filter((a) => a.id !== selectedApproval.id);
      setSelectedId(remaining.length > 0 ? remaining[0].id : null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Action failed";
      if (showTotpModal) {
        setTotpError(msg);
      } else {
        setError(msg);
      }
    } finally {
      setSubmittingAction(null);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-md p-4 transition-opacity"
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-center-title"
    >
      <div
        ref={modalRef}
        className="relative w-full max-w-5xl bg-zinc-900/90 backdrop-blur-2xl border border-white/10 rounded-2xl shadow-2xl flex flex-col max-h-[90vh] overflow-hidden text-zinc-100"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/5 bg-zinc-950/40">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.25)]">
              <ShieldAlert className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2
                  id="approval-center-title"
                  className="text-base font-bold tracking-tight text-zinc-100"
                >
                  Approval Center
                </h2>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-300">
                  Governance Ceilings
                </span>
              </div>
              <p className="text-xs text-zinc-400">
                Review and authorize sensitive agent actions with single-use
                cryptographic tokens
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-zinc-400 hover:text-zinc-200 rounded-xl hover:bg-zinc-800/60 transition-colors cursor-pointer"
            aria-label="Close modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Feedback message banner if an action was just taken */}
        {feedbackMessage && (
          <div className="px-6 py-2.5 bg-emerald-950/40 border-b border-emerald-500/30 text-emerald-300 text-xs flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              <span>{feedbackMessage}</span>
            </div>
            <button
              onClick={() => setFeedbackMessage(null)}
              className="text-emerald-400/80 hover:text-emerald-300 text-xs"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Content Body */}
        <div className="flex-1 flex overflow-hidden">
          {/* Sidebar / Request list */}
          <div className="w-full sm:w-80 border-r border-white/5 overflow-y-auto bg-zinc-950/40 p-3 space-y-2 shrink-0">
            <div className="text-xs font-semibold text-zinc-400 uppercase tracking-wider px-2 py-1.5 flex items-center justify-between">
              <span>Pending Requests ({approvals.length})</span>
              <button
                type="button"
                onClick={loadApprovals}
                className="p-1 rounded text-zinc-400 hover:text-zinc-200"
                title="Refresh requests"
              >
                <RotateCw
                  className={`w-3 h-3 ${loading ? "animate-spin text-teal-400" : ""}`}
                />
              </button>
            </div>

            {loading && (
              <div className="flex items-center justify-center py-12 text-zinc-500 text-xs gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-teal-400" />
                <span>Loading requests…</span>
              </div>
            )}

            {!loading && approvals.length === 0 && (
              <div className="text-center py-16 px-4 text-zinc-500 text-xs">
                <CheckCircle2 className="w-8 h-8 mx-auto mb-2.5 text-emerald-400/50" />
                No pending approval requests. All systems operational.
              </div>
            )}

            {approvals.map((req) => {
              const isSelected = req.id === selectedId;
              const isRestricted = req.tier === "RESTRICTED";
              return (
                <button
                  key={req.id}
                  onClick={() => setSelectedId(req.id)}
                  className={`w-full text-left p-3.5 rounded-xl border transition-all cursor-pointer relative overflow-hidden ${
                    isSelected
                      ? isRestricted
                        ? "bg-rose-950/30 border-rose-500/50 shadow-[0_0_15px_rgba(244,63,94,0.15)] ring-1 ring-rose-500/30"
                        : "bg-amber-950/30 border-amber-500/50 shadow-[0_0_15px_rgba(245,158,11,0.15)] ring-1 ring-amber-500/30"
                      : "bg-zinc-900/30 border-white/5 hover:bg-zinc-800/40"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-mono text-xs font-bold text-zinc-200 truncate">
                      {req.tool_name}
                    </span>
                    <span
                      className={`text-[10px] px-2 py-0.5 rounded-full font-mono font-semibold ${
                        isRestricted
                          ? "bg-rose-500/15 text-rose-300 border border-rose-500/30 shadow-[0_0_8px_rgba(244,63,94,0.2)]"
                          : "bg-amber-500/15 text-amber-300 border border-amber-500/30 shadow-[0_0_8px_rgba(245,158,11,0.2)]"
                      }`}
                    >
                      {isRestricted ? "RESTRICTED" : "APPROVAL"}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400 line-clamp-2 mb-2 font-medium leading-relaxed">
                    {req.action_description}
                  </p>
                  <div className="flex items-center text-[10px] text-zinc-500 font-mono">
                    <Clock className="w-3 h-3 mr-1 text-zinc-500" />
                    <span>
                      Expires {new Date(req.expires_at).toLocaleTimeString()}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Main Inspection View */}
          <div className="flex-1 flex flex-col overflow-y-auto p-6 bg-zinc-900/60 backdrop-blur-xl">
            {error && (
              <div className="mb-4 p-3.5 rounded-xl bg-rose-950/50 border border-rose-500/30 text-rose-300 text-xs flex items-start gap-2.5">
                <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-rose-400" />
                <span>{error}</span>
              </div>
            )}

            {selectedApproval ? (
              <div className="space-y-6 flex-1 flex flex-col">
                {/* Header details card */}
                <div className="p-5 rounded-2xl bg-zinc-950/50 border border-white/10 shadow-lg flex flex-col gap-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2.5 mb-1.5">
                        <h3 className="text-base font-bold text-zinc-100 font-mono tracking-tight">
                          {selectedApproval.tool_name}
                        </h3>
                        <span
                          className={`text-xs px-2.5 py-0.5 rounded-full font-mono font-bold ${
                            selectedApproval.tier === "RESTRICTED"
                              ? "bg-rose-500/15 text-rose-300 border border-rose-500/30 shadow-[0_0_12px_rgba(244,63,94,0.25)]"
                              : "bg-amber-500/15 text-amber-300 border border-amber-500/30 shadow-[0_0_12px_rgba(245,158,11,0.25)]"
                          }`}
                        >
                          {selectedApproval.tier}
                        </span>
                      </div>
                      <p className="text-xs text-zinc-300 font-medium leading-relaxed">
                        {selectedApproval.action_description}
                      </p>
                    </div>

                    <div className="text-right text-xs text-zinc-400 font-mono shrink-0">
                      <div>
                        Requested:{" "}
                        <span className="text-zinc-300">
                          {new Date(
                            selectedApproval.requested_at,
                          ).toLocaleTimeString()}
                        </span>
                      </div>
                      <div className="text-amber-400 font-semibold mt-0.5">
                        Expires:{" "}
                        {new Date(
                          selectedApproval.expires_at,
                        ).toLocaleTimeString()}
                      </div>
                    </div>
                  </div>

                  {/* Cryptographic RFC 8785 Hash & Single-Use Token Security Card */}
                  <div className="p-3.5 bg-zinc-950/70 rounded-xl border border-white/5 font-mono text-xs flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <div className="truncate mr-2 flex items-center gap-2">
                        <span className="text-zinc-500">
                          RFC 8785 Digest (SHA-256):
                        </span>
                        <span className="text-teal-300 font-semibold">
                          {selectedApproval.payload_hash}
                        </span>
                      </div>
                      <button
                        onClick={() =>
                          handleCopyHash(selectedApproval.payload_hash)
                        }
                        className="px-2 py-1 hover:bg-zinc-800 rounded-lg text-zinc-400 hover:text-zinc-200 shrink-0 text-[11px] flex items-center gap-1 transition-colors cursor-pointer"
                        title="Copy payload hash"
                      >
                        {copiedHash ? (
                          <Check className="w-3.5 h-3.5 text-emerald-400" />
                        ) : (
                          <Copy className="w-3.5 h-3.5" />
                        )}
                        <span>{copiedHash ? "Copied" : "Copy"}</span>
                      </button>
                    </div>

                    <div className="flex items-center justify-between text-[11px] text-zinc-400 pt-1.5 border-t border-white/5">
                      <span className="flex items-center gap-1.5 text-amber-300/90">
                        <Lock className="w-3 h-3 text-amber-400" />
                        <span>
                          Single-Use Cryptographic Execution Token Protocol
                        </span>
                      </span>
                      <span className="text-zinc-500">
                        Invalidated immediately upon consumption
                      </span>
                    </div>

                    {selectedApproval.approval_token && (
                      <div className="flex items-center justify-between pt-1.5 border-t border-white/5">
                        <span className="text-emerald-400 truncate mr-2">
                          Minted Token:{" "}
                          {selectedApproval.approval_token.slice(0, 24)}…
                        </span>
                        <button
                          onClick={() =>
                            handleCopyToken(selectedApproval.approval_token!)
                          }
                          className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-300 text-[10px] flex items-center gap-1"
                        >
                          {copiedToken ? "Copied" : "Copy Token"}
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {/* Arguments / RFC 8785 Diff viewer */}
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-2 flex items-center gap-1.5">
                    <FileCode className="w-3.5 h-3.5 text-teal-400" />
                    <span>Execution Arguments (Canonical RFC 8785)</span>
                  </div>
                  <pre className="p-4 bg-zinc-950/80 rounded-xl border border-white/5 text-xs font-mono text-zinc-300 overflow-x-auto max-h-56 shadow-inner leading-relaxed">
                    {JSON.stringify(selectedApproval.tool_args, null, 2)}
                  </pre>
                </div>

                {selectedApproval.tool_name === "gmail_send" && (
                  <EmailApprovalCard
                    to={String(selectedApproval.tool_args.to || "")}
                    subject={String(selectedApproval.tool_args.subject || "")}
                    body={String(selectedApproval.tool_args.body || "")}
                    draftId={
                      selectedApproval.tool_args.draft_id
                        ? String(selectedApproval.tool_args.draft_id)
                        : undefined
                    }
                    className="mb-4"
                  />
                )}

                {selectedApproval.payload_diff && (
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-2 flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-teal-400" />
                      <span>Payload Diff / Preview</span>
                    </div>
                    {typeof selectedApproval.payload_diff === "string" ? (
                      <GitDiffViewer diffText={selectedApproval.payload_diff} />
                    ) : (
                      <pre className="p-4 bg-zinc-950/80 rounded-xl border border-white/5 text-xs font-mono text-emerald-300 overflow-x-auto max-h-48 shadow-inner leading-relaxed">
                        {JSON.stringify(selectedApproval.payload_diff, null, 2)}
                      </pre>
                    )}
                  </div>
                )}

                {/* Failure reason if present */}
                {selectedApproval.failure_reason && (
                  <div className="p-3.5 bg-rose-950/40 rounded-xl border border-rose-800/40 text-xs text-rose-300 font-mono">
                    <span className="font-semibold text-rose-400">
                      Failure Reason:
                    </span>{" "}
                    {selectedApproval.failure_reason}
                  </div>
                )}

                {/* Execution output viewer if present */}
                {selectedApproval.execution_output && (
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-2">
                      Execution Output
                    </div>
                    <pre className="p-4 bg-zinc-950/80 rounded-xl border border-white/5 text-xs font-mono text-emerald-400 overflow-x-auto max-h-56 shadow-inner leading-relaxed">
                      {JSON.stringify(
                        selectedApproval.execution_output,
                        null,
                        2,
                      )}
                    </pre>
                  </div>
                )}

                {/* Action buttons footer */}
                <div className="pt-4 border-t border-white/10 flex items-center justify-end gap-3 mt-auto">
                  <button
                    disabled={submitting}
                    onClick={() => executeDecision("revoke")}
                    className="px-4 py-2 text-xs font-semibold rounded-xl text-amber-300 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 transition-all disabled:opacity-50 flex items-center gap-1.5 cursor-pointer"
                  >
                    {submittingAction === "revoke" && (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    )}
                    {submittingAction === "revoke"
                      ? "Revoking..."
                      : "Revoke Request"}
                  </button>
                  <button
                    disabled={submitting}
                    onClick={() => executeDecision("reject")}
                    className="px-4 py-2 text-xs font-semibold rounded-xl text-rose-300 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 transition-all disabled:opacity-50 flex items-center gap-1.5 cursor-pointer"
                  >
                    {submittingAction === "reject" && (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    )}
                    {submittingAction === "reject"
                      ? "Rejecting..."
                      : "Reject Action"}
                  </button>
                  <button
                    disabled={submitting}
                    onClick={handleApproveClick}
                    className="px-5 py-2 text-xs font-bold rounded-xl text-zinc-950 bg-gradient-to-r from-emerald-500 to-teal-400 hover:from-emerald-400 hover:to-teal-300 transition-all flex items-center gap-2 shadow-[0_0_20px_rgba(16,185,129,0.3)] disabled:opacity-50 cursor-pointer"
                  >
                    {submittingAction === "approve" ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <ShieldCheck className="w-4 h-4" />
                    )}
                    {selectedApproval.tier === "RESTRICTED"
                      ? "Verify & Approve"
                      : "Authorize Execution"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center text-zinc-500 text-xs">
                Select an approval request to inspect details.
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Step-Up Authentication TOTP Modal */}
      {showTotpModal && selectedApproval && (
        <div
          className="fixed inset-0 z-60 flex items-center justify-center bg-black/80 backdrop-blur-md p-4"
          role="dialog"
          aria-labelledby="step-up-title"
        >
          <div className="w-full max-w-md bg-zinc-900/95 backdrop-blur-2xl border border-rose-500/50 rounded-2xl shadow-2xl p-6 text-zinc-100 flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-xl bg-rose-500/15 border border-rose-500/30 text-rose-400 shadow-[0_0_15px_rgba(244,63,94,0.25)]">
                <KeyRound className="w-6 h-6" />
              </div>
              <div>
                <h3
                  id="step-up-title"
                  className="text-base font-bold text-zinc-100 tracking-tight"
                >
                  Step-Up Authentication Required
                </h3>
                <p className="text-xs text-zinc-400 font-mono">
                  Authorizing {selectedApproval.tool_name} (RESTRICTED)
                </p>
              </div>
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed">
              This is a high-risk operation. Enter the 6-digit Time-based
              One-Time Password (TOTP) from your authenticator app to authorize
              execution.
            </p>

            {totpError && (
              <div className="p-3 rounded-xl bg-rose-950/60 border border-rose-500/40 text-rose-200 text-xs font-mono">
                {totpError}
              </div>
            )}

            <div>
              <label
                htmlFor="totp-code-input"
                className="block text-xs font-semibold text-zinc-300 mb-2"
              >
                Authentication Code
              </label>
              <input
                id="totp-code-input"
                type="text"
                autoFocus
                maxLength={6}
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
                placeholder="123456"
                className="w-full text-center tracking-widest font-mono text-2xl py-2.5 px-4 rounded-xl bg-zinc-950/80 border border-rose-500/40 text-zinc-100 focus:outline-none focus:border-rose-400 focus:ring-1 focus:ring-rose-400 shadow-inner"
              />
            </div>

            <div className="flex items-center justify-end gap-3 pt-2 border-t border-white/5">
              <button
                disabled={submitting}
                onClick={() => setShowTotpModal(false)}
                className="px-4 py-2 text-xs font-semibold rounded-xl text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                disabled={submitting || totpCode.length !== 6}
                onClick={() => executeDecision("approve", totpCode)}
                className="px-5 py-2 text-xs font-bold rounded-xl text-white bg-gradient-to-r from-rose-600 to-rose-500 hover:from-rose-500 hover:to-rose-400 transition-all flex items-center gap-2 shadow-[0_0_15px_rgba(244,63,94,0.3)] disabled:opacity-50 cursor-pointer"
              >
                {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                Confirm & Mint Token
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
