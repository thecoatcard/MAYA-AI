"use client";

import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Copy,
  FileCode,
  KeyRound,
  Loader2,
  Lock,
  RefreshCw,
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

export interface ApprovalCenterViewProps {
  readonly initialApprovalId?: string | null;
}

export function ApprovalCenterView({
  initialApprovalId,
}: ApprovalCenterViewProps): React.JSX.Element {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialApprovalId ?? null,
  );
  const [filterStatus, setFilterStatus] = useState<string>("pending");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null);
  const [submittingAction, setSubmittingAction] = useState<
    "approve" | "reject" | "revoke" | null
  >(null);

  // Step-up TOTP state for RESTRICTED tier
  const [showTotpModal, setShowTotpModal] = useState<boolean>(false);
  const [totpCode, setTotpCode] = useState<string>("");
  const [totpError, setTotpError] = useState<string | null>(null);
  const [rejectionReason, setRejectionReason] = useState<string>("");
  const [showRejectModal, setShowRejectModal] = useState<boolean>(false);
  const [copiedHash, setCopiedHash] = useState<boolean>(false);
  const [copiedToken, setCopiedToken] = useState<boolean>(false);

  const loadApprovals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const statusParam = filterStatus === "all" ? undefined : filterStatus;
      const data = await listApprovals(statusParam, 50);
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
  }, [filterStatus, selectedId]);

  useEffect(() => {
    loadApprovals();
  }, [loadApprovals]);

  const activeItem =
    approvals.find((a) => a.id === selectedId) ?? approvals[0] ?? null;

  const handleApproveClick = () => {
    if (!activeItem) return;
    if (activeItem.tier === "RESTRICTED") {
      setShowTotpModal(true);
      setTotpCode("");
      setTotpError(null);
      return;
    }
    executeDecision("approve");
  };

  const executeDecision = async (
    action: "approve" | "reject" | "revoke",
    code?: string,
    reason?: string,
  ) => {
    if (!activeItem) return;
    setSubmittingAction(action);
    setError(null);
    try {
      await decideApproval(activeItem.id, {
        action,
        totp_code: code,
        rejection_reason: reason,
      });

      if (action === "approve") {
        setFeedbackMessage(
          `Action ${activeItem.tool_name} approved. Single-use execution token minted.`,
        );
      } else if (action === "reject") {
        setFeedbackMessage(
          `Action ${activeItem.tool_name} rejected. Reason logged in audit chain.`,
        );
      } else {
        setFeedbackMessage(`Approval request ${activeItem.id} revoked.`);
      }

      setShowTotpModal(false);
      setShowRejectModal(false);
      await loadApprovals();
    } catch (err) {
      if (showTotpModal) {
        setTotpError(
          err instanceof Error
            ? err.message
            : "Invalid or expired step-up code.",
        );
      } else {
        setError(
          err instanceof Error ? err.message : "Failed to submit decision",
        );
      }
    } finally {
      setSubmittingAction(null);
    }
  };

  const getTierBadge = (tier: string) => {
    if (tier === "RESTRICTED") {
      return (
        <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-3 py-1 rounded-full bg-rose-500/15 border border-rose-500/30 text-rose-300 font-bold shadow-[0_0_12px_rgba(244,63,94,0.25)]">
          <Lock className="w-3.5 h-3.5 text-rose-400" />
          <span>RESTRICTED (Step-Up TOTP)</span>
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] font-mono px-3 py-1 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-300 font-bold shadow-[0_0_12px_rgba(245,158,11,0.25)]">
        <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
        <span>APPROVAL REQUIRED</span>
      </span>
    );
  };

  return (
    <div className="flex h-full flex-col lg:flex-row overflow-hidden bg-zinc-950 text-zinc-100">
      {/* Left List of Approvals (Glass Surface) */}
      <div className="w-full lg:w-96 flex flex-col border-r border-white/10 bg-zinc-900/30 backdrop-blur-xl shrink-0">
        <div className="p-4 border-b border-white/5 flex flex-col gap-3 bg-zinc-950/40">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-zinc-100 flex items-center gap-2 tracking-tight">
              <div className="p-1 rounded-md bg-amber-500/15 border border-amber-500/30 text-amber-400">
                <ShieldAlert className="w-3.5 h-3.5" />
              </div>
              <span>Approval Requests</span>
            </h2>
            <button
              type="button"
              onClick={loadApprovals}
              className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 transition-colors"
              title="Refresh approvals"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 ${loading ? "animate-spin text-amber-400" : ""}`}
              />
            </button>
          </div>

          {/* Filter Tabs */}
          <div className="flex items-center gap-1 text-[11px]">
            {[
              { id: "pending", label: "Pending" },
              { id: "approved", label: "Approved" },
              { id: "rejected", label: "Denied" },
              { id: "all", label: "All" },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setFilterStatus(tab.id)}
                className={`px-3 py-1 rounded-lg transition-all font-medium ${
                  filterStatus === tab.id
                    ? "bg-amber-500/20 text-amber-300 border border-amber-500/30 font-semibold shadow-[0_0_10px_rgba(245,158,11,0.2)]"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/40"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* List items */}
        <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
          {loading && approvals.length === 0 ? (
            <div className="p-10 text-center text-zinc-500 text-xs flex flex-col items-center justify-center gap-2">
              <Loader2 className="w-5 h-5 animate-spin text-amber-400" />
              <span>Loading approvals…</span>
            </div>
          ) : approvals.length === 0 ? (
            <div className="p-10 text-center text-zinc-500 text-xs">
              No approval requests matching filter.
            </div>
          ) : (
            approvals.map((item) => {
              const isSelected = item.id === selectedId;
              const isRestricted = item.tier === "RESTRICTED";
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setSelectedId(item.id)}
                  className={`w-full text-left p-3.5 rounded-xl border transition-all duration-200 cursor-pointer relative overflow-hidden ${
                    isSelected
                      ? isRestricted
                        ? "border-rose-500/60 bg-gradient-to-r from-rose-950/40 via-zinc-900/80 to-zinc-900/80 shadow-[0_0_20px_rgba(244,63,94,0.15)] ring-1 ring-rose-500/30"
                        : "border-amber-500/60 bg-gradient-to-r from-amber-950/40 via-zinc-900/80 to-zinc-900/80 shadow-[0_0_20px_rgba(245,158,11,0.15)] ring-1 ring-amber-500/30"
                      : "border-white/5 bg-zinc-900/30 hover:bg-zinc-900/60 hover:border-white/10"
                  }`}
                >
                  {/* Subtle top edge glow */}
                  {isSelected && (
                    <div
                      className={`absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent ${
                        isRestricted ? "via-rose-400/60" : "via-amber-400/60"
                      } to-transparent`}
                    />
                  )}

                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-bold text-zinc-200 font-mono truncate">
                      {item.tool_name}
                    </span>
                    <span
                      className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded-full font-semibold ${
                        item.status === "pending"
                          ? "bg-amber-500/15 text-amber-300 border border-amber-500/30 shadow-[0_0_8px_rgba(245,158,11,0.2)]"
                          : item.status === "approved"
                            ? "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 shadow-[0_0_8px_rgba(16,185,129,0.2)]"
                            : "bg-rose-500/15 text-rose-300 border border-rose-500/30"
                      }`}
                    >
                      {item.status}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400 line-clamp-2 mt-1.5 font-medium leading-relaxed">
                    {item.action_description}
                  </p>
                  <div className="flex items-center justify-between text-[11px] font-mono text-zinc-500 mt-2.5 pt-2 border-t border-white/5">
                    <span
                      className={
                        isRestricted
                          ? "text-rose-400 font-semibold"
                          : "text-amber-400 font-semibold"
                      }
                    >
                      {item.tier}
                    </span>
                    <span>
                      Expires: {new Date(item.expires_at).toLocaleTimeString()}
                    </span>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* Right Detailed Approval Card (Glass Surface) */}
      <div className="flex-1 flex flex-col overflow-y-auto bg-zinc-950 p-6">
        {activeItem ? (
          <div className="max-w-5xl flex flex-col gap-6 mx-auto w-full">
            {feedbackMessage && (
              <div className="p-4 rounded-xl border border-emerald-500/40 bg-emerald-950/50 text-emerald-200 text-xs flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span>{feedbackMessage}</span>
                </div>
                <button
                  onClick={() => setFeedbackMessage(null)}
                  className="text-emerald-400 hover:text-emerald-200 text-xs"
                >
                  Dismiss
                </button>
              </div>
            )}

            {error && (
              <div className="p-4 rounded-xl border border-rose-500/40 bg-rose-950/50 text-rose-200 text-xs flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-rose-400" />
                <span>{error}</span>
              </div>
            )}

            {/* Header Card */}
            <div className="p-6 rounded-2xl border border-white/10 bg-zinc-900/40 backdrop-blur-xl flex flex-col gap-5 shadow-xl">
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="flex items-center gap-3.5">
                  <div className="p-2.5 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.2)]">
                    <ShieldAlert className="w-6 h-6" />
                  </div>
                  <div>
                    <h2 className="text-lg font-bold text-zinc-100 font-mono tracking-tight">
                      {activeItem.tool_name}
                    </h2>
                    <p className="text-xs text-zinc-300 mt-1 font-medium leading-relaxed">
                      {activeItem.action_description}
                    </p>
                  </div>
                </div>
                {getTierBadge(activeItem.tier)}
              </div>

              {/* Exact Payload Hash & RFC 8785 Proof */}
              <div className="flex flex-wrap items-center justify-between gap-3 p-4 rounded-xl bg-zinc-950/70 border border-white/5 text-xs font-mono">
                <div className="flex items-center gap-2 text-zinc-400">
                  <span className="text-zinc-500 font-semibold">
                    RFC 8785 Canonical Digest (SHA-256):
                  </span>
                  <span className="text-teal-300 font-bold">
                    {activeItem.payload_hash}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard.writeText(activeItem.payload_hash);
                    setCopiedHash(true);
                    setTimeout(() => setCopiedHash(false), 2000);
                  }}
                  className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs flex items-center gap-1.5 transition-colors cursor-pointer"
                >
                  {copiedHash ? (
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
                  <span>{copiedHash ? "Copied" : "Copy Hash"}</span>
                </button>
              </div>

              {/* Single-Use Cryptographic Execution Token Inspection */}
              <div className="p-4 rounded-xl bg-zinc-950/50 border border-white/5 text-xs flex flex-col gap-2.5 font-mono">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Lock className="w-4 h-4 text-amber-400" />
                    <span className="font-bold text-zinc-200">
                      Single-Use Execution Token Protocol (Phase 06)
                    </span>
                  </div>
                  <span className="px-2 py-0.5 rounded-full text-[10px] bg-amber-500/10 text-amber-300 border border-amber-500/20">
                    One-Time Token Guarantee
                  </span>
                </div>
                <p className="text-[11px] text-zinc-400 leading-relaxed font-sans">
                  The issued cryptographic token grants single-use permission
                  strictly for the RFC 8785 hashed payload. Once executed by the
                  designated worker, the token is permanently consumed and
                  invalidated.
                </p>
                {activeItem.approval_token && (
                  <div className="flex items-center justify-between pt-2 border-t border-white/5 text-[11px]">
                    <span className="text-emerald-400 truncate mr-2">
                      Active Token: {activeItem.approval_token}
                    </span>
                    <button
                      type="button"
                      onClick={() => {
                        navigator.clipboard.writeText(
                          activeItem.approval_token!,
                        );
                        setCopiedToken(true);
                        setTimeout(() => setCopiedToken(false), 2000);
                      }}
                      className="px-2 py-1 rounded bg-zinc-800 text-zinc-300 text-[10px] flex items-center gap-1"
                    >
                      {copiedToken ? "Copied" : "Copy Token"}
                    </button>
                  </div>
                )}
              </div>

              {/* Actions if Pending */}
              {activeItem.status === "pending" && (
                <div className="flex items-center justify-end gap-3 pt-4 border-t border-white/10">
                  <button
                    type="button"
                    onClick={() => setShowRejectModal(true)}
                    disabled={submittingAction !== null}
                    className="px-4 py-2 rounded-xl border border-rose-500/40 bg-rose-950/40 hover:bg-rose-900/60 text-rose-200 text-xs font-semibold transition-all shadow-[0_0_12px_rgba(244,63,94,0.15)] cursor-pointer"
                  >
                    Reject Action
                  </button>
                  <button
                    type="button"
                    onClick={handleApproveClick}
                    disabled={submittingAction !== null}
                    className="flex items-center gap-2 px-5 py-2 rounded-xl bg-gradient-to-r from-emerald-500 to-teal-400 hover:from-emerald-400 hover:to-teal-300 text-zinc-950 font-bold text-xs shadow-[0_0_20px_rgba(16,185,129,0.3)] transition-all cursor-pointer"
                  >
                    {submittingAction === "approve" ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <ShieldCheck className="w-4 h-4" />
                    )}
                    <span>
                      {activeItem.tier === "RESTRICTED"
                        ? "Verify TOTP & Execute"
                        : "Approve & Execute"}
                    </span>
                  </button>
                </div>
              )}
            </div>

            {/* Specialized Payload Diff Viewers */}
            {activeItem.payload_diff && (
              <div className="flex flex-col gap-2.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-teal-400" />
                  <span>Payload Differences / Inspection</span>
                </span>
                {activeItem.tool_name.includes("email") ||
                activeItem.tool_name.includes("gmail") ? (
                  <EmailApprovalCard
                    to={String(
                      activeItem.tool_args.to ||
                        activeItem.tool_args.recipient ||
                        "",
                    )}
                    subject={String(activeItem.tool_args.subject || "")}
                    body={String(
                      activeItem.tool_args.body ||
                        activeItem.tool_args.message ||
                        "",
                    )}
                    draftId={
                      typeof activeItem.tool_args.draft_id === "string"
                        ? activeItem.tool_args.draft_id
                        : undefined
                    }
                  />
                ) : typeof activeItem.payload_diff === "string" ? (
                  <GitDiffViewer
                    diffText={activeItem.payload_diff}
                    filePath="patch.diff"
                  />
                ) : (
                  <pre className="p-4 rounded-2xl bg-zinc-950/80 border border-white/5 text-xs font-mono text-emerald-300 overflow-x-auto shadow-inner leading-relaxed">
                    {JSON.stringify(activeItem.payload_diff, null, 2)}
                  </pre>
                )}
              </div>
            )}

            {/* Exact Parameters */}
            <div className="flex flex-col gap-2.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
                <FileCode className="w-3.5 h-3.5 text-teal-400" />
                <span>Exact Tool Arguments (RFC 8785 Canonical JSON)</span>
              </span>
              <pre className="p-4 rounded-2xl bg-zinc-950/80 border border-white/5 text-xs font-mono text-zinc-300 overflow-x-auto shadow-inner leading-relaxed">
                {JSON.stringify(activeItem.tool_args, null, 2)}
              </pre>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 text-center">
            <div className="p-4 rounded-2xl bg-zinc-900/40 border border-white/5 shadow-inner mb-3">
              <ShieldCheck className="w-12 h-12 text-amber-400 opacity-60" />
            </div>
            <p className="text-base font-bold text-zinc-200">
              Select an Approval Request
            </p>
            <p className="text-xs max-w-sm mt-1.5 text-zinc-400 leading-relaxed">
              Select an item on the left to inspect exact payloads and execute
              cryptographically verifiable approvals.
            </p>
          </div>
        )}
      </div>

      {/* Step-Up TOTP Modal for RESTRICTED tier */}
      {showTotpModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-md rounded-2xl border border-rose-500/50 bg-zinc-900/95 backdrop-blur-2xl p-6 shadow-2xl text-zinc-100 flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-white/5 pb-3">
              <div className="flex items-center gap-2 text-rose-400 font-bold text-sm">
                <KeyRound className="w-5 h-5" />
                <span>Step-Up Authentication Required</span>
              </div>
              <button
                type="button"
                onClick={() => setShowTotpModal(false)}
                className="p-1 rounded-lg text-zinc-400 hover:text-zinc-200"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed">
              This action operates in the <strong>RESTRICTED</strong> tier.
              Enter your 6-digit authenticator TOTP code to authorize the
              issuance of a single-use execution token.
            </p>

            {totpError && (
              <div className="p-3 rounded-xl bg-rose-950/60 border border-rose-500/40 text-rose-200 text-xs font-mono">
                {totpError}
              </div>
            )}

            <input
              type="text"
              maxLength={6}
              autoFocus
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, ""))}
              placeholder="000000"
              className="text-center font-mono tracking-widest text-2xl py-2.5 rounded-xl border border-rose-500/40 bg-zinc-950/80 text-zinc-100 focus:outline-none focus:border-rose-400 shadow-inner"
            />

            <div className="flex items-center justify-end gap-3 pt-2 border-t border-white/5">
              <button
                type="button"
                onClick={() => setShowTotpModal(false)}
                className="px-4 py-2 rounded-xl text-xs text-zinc-400 hover:text-zinc-200"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => executeDecision("approve", totpCode)}
                disabled={totpCode.length !== 6 || submittingAction !== null}
                className="px-5 py-2 rounded-xl bg-gradient-to-r from-rose-600 to-rose-500 hover:from-rose-500 hover:to-rose-400 disabled:opacity-50 text-white font-bold text-xs shadow-[0_0_15px_rgba(244,63,94,0.3)] transition-all cursor-pointer"
              >
                {submittingAction !== null ? "Verifying…" : "Verify & Approve"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reject Reason Modal */}
      {showRejectModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md"
          role="dialog"
          aria-modal="true"
        >
          <div className="w-full max-w-md rounded-2xl border border-white/10 bg-zinc-900/95 backdrop-blur-2xl p-6 shadow-2xl text-zinc-100 flex flex-col gap-4">
            <h3 className="text-sm font-bold text-zinc-100">Reject Action</h3>
            <p className="text-xs text-zinc-400">
              Provide a rationale for the rejection to record in the governance
              ledger.
            </p>
            <textarea
              rows={3}
              value={rejectionReason}
              onChange={(e) => setRejectionReason(e.target.value)}
              placeholder="Provide a reason for rejection (e.g. invalid git diff or unauthorized scope)"
              className="p-3 rounded-xl border border-white/10 bg-zinc-950/70 text-xs text-zinc-200 focus:outline-none focus:border-rose-400 font-mono resize-none leading-relaxed"
            />
            <div className="flex items-center justify-end gap-3 pt-2 border-t border-white/5">
              <button
                type="button"
                onClick={() => setShowRejectModal(false)}
                className="px-4 py-2 rounded-xl text-xs text-zinc-400 hover:text-zinc-200"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() =>
                  executeDecision("reject", undefined, rejectionReason)
                }
                className="px-5 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-bold text-xs shadow-[0_0_12px_rgba(244,63,94,0.25)] transition-all cursor-pointer"
              >
                Confirm Rejection
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
