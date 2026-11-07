"use client";

import {
  AlertTriangle,
  CheckCircle,
  ExternalLink,
  ShieldAlert,
} from "lucide-react";
import React, { useState } from "react";
import { GlassBadge } from "../ui/GlassBadge";
import { GlassButton } from "../ui/GlassButton";
import { GlassModal } from "../ui/GlassModal";

export interface BrowserTakeoverModalProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly sessionId: string;
  readonly url: string;
  readonly botType?: string | null;
  readonly screenshotBase64?: string | null;
  readonly onResume: (sessionId: string) => Promise<void>;
  readonly onCancel?: (sessionId: string) => Promise<void>;
}

export function BrowserTakeoverModal({
  isOpen,
  onClose,
  sessionId,
  url,
  botType,
  screenshotBase64,
  onResume,
  onCancel,
}: BrowserTakeoverModalProps): React.JSX.Element {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleResume = async () => {
    setIsSubmitting(true);
    setError(null);
    try {
      await onResume(sessionId);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to resume automation.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancel = async () => {
    setIsSubmitting(true);
    setError(null);
    try {
      if (onCancel) {
        await onCancel(sessionId);
      }
      onClose();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to cancel browser session.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <GlassModal
      isOpen={isOpen}
      onClose={onClose}
      size="xl"
      title={
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-amber-400" />
          <span>Human Takeover Required — Bot Challenge Detected</span>
        </div>
      }
      description="Automated execution paused due to third-party anti-bot challenge."
    >
      <div className="space-y-4 py-2">
        {/* Compliance / Safety Policy Notice */}
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3.5 text-xs text-amber-200">
          <div className="flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400 mt-0.5" />
            <div>
              <p className="font-semibold text-amber-100">
                Anti-Bypass Policy & Security Invariant
              </p>
              <p className="mt-1 leading-relaxed text-amber-200/90">
                In strict compliance with repository safety policy, MAYA AI
                never attempts to solve, bypass, or defeat CAPTCHAs, Cloudflare
                Turnstile, or anti-bot challenges. Please complete the
                verification manually below before resuming automation.
              </p>
            </div>
          </div>
        </div>

        {/* Target URL and Challenge Type */}
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="rounded-lg border border-white/10 bg-white/5 p-3">
            <span className="text-white/50 block">Target URL</span>
            <div className="mt-1 flex items-center gap-1 font-mono text-white/90 truncate">
              <span className="truncate">{url}</span>
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                className="text-white/60 hover:text-white"
                title="Open in new tab"
              >
                <ExternalLink className="h-3 w-3 inline" />
              </a>
            </div>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/5 p-3">
            <span className="text-white/50 block">Detected Challenge</span>
            <div className="mt-1">
              <GlassBadge variant="amber">
                {botType
                  ? botType.replace("_", " ").toUpperCase()
                  : "CAPTCHA CHALLENGE"}
              </GlassBadge>
            </div>
          </div>
        </div>

        {/* Screenshot / Canvas Preview */}
        <div className="rounded-lg border border-white/10 bg-black/40 overflow-hidden">
          <div className="border-b border-white/10 px-3 py-1.5 text-[11px] text-white/40 flex items-center justify-between">
            <span>Browser Viewport Snapshot</span>
            <span className="font-mono">
              Session: {sessionId.slice(0, 8)}...
            </span>
          </div>
          <div className="min-h-[220px] max-h-[360px] overflow-auto flex items-center justify-center p-2 bg-neutral-950">
            {screenshotBase64 ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={`data:image/png;base64,${screenshotBase64}`}
                alt="Browser Challenge Snapshot"
                className="max-w-full rounded border border-white/5 shadow-md"
              />
            ) : (
              <div className="text-center p-8 text-white/40 text-xs">
                <ShieldAlert className="h-8 w-8 mx-auto mb-2 text-white/20" />
                <span>
                  Interactive browser snapshot is pending or challenge displayed
                  in active tab.
                </span>
              </div>
            )}
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-2.5 text-xs text-rose-300">
            {error}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center justify-end gap-2 pt-2 border-t border-white/10">
          <GlassButton
            variant="ghost"
            size="sm"
            onClick={handleCancel}
            disabled={isSubmitting}
          >
            Cancel Automation
          </GlassButton>
          <GlassButton
            variant="primary"
            size="sm"
            onClick={handleResume}
            disabled={isSubmitting}
            className="flex items-center gap-1.5"
          >
            <CheckCircle className="h-4 w-4" />
            <span>
              {isSubmitting
                ? "Resuming..."
                : "I Have Solved the Challenge — Resume"}
            </span>
          </GlassButton>
        </div>
      </div>
    </GlassModal>
  );
}
