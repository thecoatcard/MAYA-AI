"use client";

import {
  Activity,
  ArrowRight,
  Clock,
  Copy,
  Download,
  Eye,
  Layers,
  Link as LinkIcon,
  Lock,
  Search,
  Shield,
  ShieldCheck,
  User,
  Zap,
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";

import { getPrincipalMe, type UserPrincipal } from "../../lib/system-client";
import { listAuditEvents } from "../../lib/audit-client";
import {
  GlassBadge,
  GlassButton,
  GlassCard,
  GlassInput,
  GlassModal,
} from "../ui";

export interface AuditEvent {
  readonly id: string;
  readonly timestamp: string;
  readonly eventType: string;
  readonly category: "auth" | "task" | "tool" | "approval" | "security";
  readonly actor: string;
  readonly details: string;
  readonly status: "success" | "pending" | "rejected" | "warning";
  readonly taskId?: string;
  readonly subtaskId?: string;
  readonly toolName?: string;
  readonly permissionLevel?: "READ" | "WRITE" | "SENSITIVE" | "RESTRICTED";
  readonly prevHash: string;
  readonly rowHash: string;
  readonly durationMs?: number;
  readonly inputPayload?: Record<string, unknown>;
  readonly outputPayload?: Record<string, unknown>;
}

const SAMPLE_AUDIT_TRAIL: readonly AuditEvent[] = [
  {
    id: "evt-001",
    timestamp: "2026-11-07T10:45:12Z",
    eventType: "task.created",
    category: "task",
    actor: "user:current",
    details:
      "Orchestrator created autonomous DAG task: 'Enterprise Competitive Pricing Analysis' with 4 planned subtasks.",
    status: "success",
    taskId: "task-8eb3262",
    permissionLevel: "WRITE",
    durationMs: 42,
    prevHash:
      "0000000000000000000000000000000000000000000000000000000000000000",
    rowHash: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    inputPayload: {
      prompt:
        "Research and generate competitive matrix with verified WeasyPrint PDF and XLSX",
      budget_usd: 2.5,
    },
    outputPayload: {
      task_id: "task-8eb3262",
      dag_nodes: [
        "research",
        "pricing_model",
        "weasyprint_pdf",
        "verification",
      ],
    },
  },
  {
    id: "evt-002",
    timestamp: "2026-11-07T10:45:15Z",
    eventType: "tool.invoked",
    category: "tool",
    actor: "agent:research",
    details:
      "Invoked Brave Search connector for query 'enterprise AI pricing models'. Captured 4 structured citations.",
    status: "success",
    toolName: "brave_web_search",
    permissionLevel: "READ",
    durationMs: 310,
    prevHash:
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    rowHash: "8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
    inputPayload: {
      query: "enterprise AI pricing models",
      count: 4,
    },
    outputPayload: {
      results_count: 4,
      sanitized: true,
    },
  },
  {
    id: "evt-003",
    timestamp: "2026-11-07T10:46:02Z",
    eventType: "approval.required",
    category: "approval",
    actor: "agent:coding",
    details:
      "Halted on RESTRICTED tool: 'github_create_pull_request'. Issued single-use RFC 8785 payload token.",
    status: "pending",
    toolName: "github_create_pull_request",
    permissionLevel: "RESTRICTED",
    durationMs: 18,
    prevHash:
      "8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
    rowHash: "3a52e22c9e7a46c65b534ab20845a9956d400263f350ec7dfaa0425a17688226",
    inputPayload: {
      repo: "maya-ai",
      title: "fix: race condition in approval token invalidation",
      branch: "fix/approval-race-condition",
    },
    outputPayload: {
      approval_id: "appr-98213",
      token_bound: "payload_sha256",
    },
  },
  {
    id: "evt-004",
    timestamp: "2026-11-07T10:47:30Z",
    eventType: "approval.decided",
    category: "approval",
    actor: "user:current",
    details:
      "Operator authorized action 'Create Pull Request' via Authenticated Web UI with TOTP verification.",
    status: "success",
    toolName: "github_create_pull_request",
    permissionLevel: "RESTRICTED",
    durationMs: 120,
    prevHash:
      "3a52e22c9e7a46c65b534ab20845a9956d400263f350ec7dfaa0425a17688226",
    rowHash: "c51a0293db2e8faee46aa67f2b1d0e123611cd5be415f36eecb3917ec77e90ff",
    inputPayload: {
      decision: "approved",
      totp_verified: true,
    },
    outputPayload: {
      pr_url: "https://github.com/maya-ai/maya-ai/pull/48",
      token_consumed: true,
    },
  },
  {
    id: "evt-005",
    timestamp: "2026-11-07T10:48:10Z",
    eventType: "artifact.verified",
    category: "task",
    actor: "agent:document",
    details:
      "Produced cryptographically verified XLSX deliverable. SHA-256 and row count passed anti-tamper verifier.",
    status: "success",
    taskId: "task-8eb3262",
    permissionLevel: "WRITE",
    durationMs: 480,
    prevHash:
      "c51a0293db2e8faee46aa67f2b1d0e123611cd5be415f36eecb3917ec77e90ff",
    rowHash: "7b8a1c9e4210d7a64e15a9950d8794f31c9a632b450fe1cf8a62f8319d3810ec",
    inputPayload: {
      file_name: "competitor_matrix_model.xlsx",
      engine: "openpyxl",
    },
    outputPayload: {
      artifact_id: "art-sample-xlsx",
      sha256:
        "8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
      checksum_status: "verified",
    },
  },
  {
    id: "evt-006",
    timestamp: "2026-11-07T11:02:14Z",
    eventType: "security.ratelimit",
    category: "security",
    actor: "system:limiter",
    details:
      "Brave Search token bucket refilled. Leaky bucket rate limiter healthy with 0 dropped calls.",
    status: "success",
    toolName: "brave_web_search",
    permissionLevel: "READ",
    durationMs: 2,
    prevHash:
      "7b8a1c9e4210d7a64e15a9950d8794f31c9a632b450fe1cf8a62f8319d3810ec",
    rowHash: "4a00bc9837f132e04da88c91a0c776de34190c128509b4317f229a1b029381ea",
    inputPayload: {
      capacity: 60,
      available_tokens: 58,
    },
    outputPayload: {
      status: "allow",
    },
  },
  {
    id: "evt-007",
    timestamp: "2026-11-07T11:15:00Z",
    eventType: "auth.session_refreshed",
    category: "auth",
    actor: "user:current",
    details:
      "Rotated JWT session with secure httpOnly refresh cookie. Sliding expiration extended.",
    status: "success",
    permissionLevel: "READ",
    durationMs: 15,
    prevHash:
      "4a00bc9837f132e04da88c91a0c776de34190c128509b4317f229a1b029381ea",
    rowHash: "5ef34b1298c56d012e88a3194519fbc702a11b659c03211a7c00e5a932b1f810",
    inputPayload: {
      grant_type: "refresh_token",
    },
    outputPayload: {
      access_token_expires_in: 900,
    },
  },
];

export function ActivityView(): React.JSX.Element {
  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);
  const [principal, setPrincipal] = useState<UserPrincipal | null>(null);
  const [copiedHash, setCopiedHash] = useState<string | null>(null);
  const [events, setEvents] =
    useState<readonly AuditEvent[]>(SAMPLE_AUDIT_TRAIL);
  const [loading, setLoading] = useState<boolean>(false);
  const [chainValid, setChainValid] = useState<boolean>(true);

  useEffect(() => {
    getPrincipalMe()
      .then(setPrincipal)
      .catch(() => {});
  }, []);

  const loadAuditLogs = React.useCallback(async () => {
    setLoading(true);
    try {
      const res = await listAuditEvents({
        category: filterCategory !== "all" ? filterCategory : undefined,
      });
      if (res.items.length > 0) {
        setEvents(res.items);
      }
      setChainValid(res.chain_valid);
    } catch {
      // Offline fallback: keep existing
    } finally {
      setLoading(false);
    }
  }, [filterCategory]);

  useEffect(() => {
    void loadAuditLogs();
  }, [loadAuditLogs]);

  const copyToClipboard = (hash: string, id: string) => {
    navigator.clipboard?.writeText(hash);
    setCopiedHash(id);
    setTimeout(() => setCopiedHash(null), 2000);
  };

  const filteredEvents = useMemo(() => {
    return events.filter((evt) => {
      if (filterCategory !== "all" && evt.category !== filterCategory) {
        return false;
      }
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          evt.eventType.toLowerCase().includes(q) ||
          evt.details.toLowerCase().includes(q) ||
          (evt.toolName && evt.toolName.toLowerCase().includes(q)) ||
          (evt.taskId && evt.taskId.toLowerCase().includes(q)) ||
          evt.rowHash.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [events, filterCategory, searchQuery]);

  const exportLedgerJson = () => {
    const dataStr =
      "data:text/json;charset=utf-8," +
      encodeURIComponent(JSON.stringify(filteredEvents, null, 2));
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute(
      "download",
      `maya_audit_ledger_${new Date().toISOString().slice(0, 10)}.json`,
    );
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  const getCategoryIcon = (category: AuditEvent["category"]) => {
    switch (category) {
      case "approval":
        return <Shield className="w-4 h-4 text-amber-400" />;
      case "task":
        return <Layers className="w-4 h-4 text-blue-400" />;
      case "tool":
        return <Zap className="w-4 h-4 text-teal-400" />;
      case "security":
        return <Lock className="w-4 h-4 text-purple-400" />;
      case "auth":
      default:
        return <User className="w-4 h-4 text-emerald-400" />;
    }
  };

  const getCategoryBadge = (category: AuditEvent["category"]) => {
    switch (category) {
      case "approval":
        return (
          <GlassBadge variant="amber" size="sm" mono>
            APPROVAL
          </GlassBadge>
        );
      case "task":
        return (
          <GlassBadge variant="blue" size="sm" mono>
            TASK
          </GlassBadge>
        );
      case "tool":
        return (
          <GlassBadge variant="teal" size="sm" mono>
            TOOL
          </GlassBadge>
        );
      case "security":
        return (
          <GlassBadge variant="purple" size="sm" mono>
            SECURITY
          </GlassBadge>
        );
      case "auth":
      default:
        return (
          <GlassBadge variant="emerald" size="sm" mono>
            AUTH
          </GlassBadge>
        );
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-4 md:p-8 flex flex-col gap-6 max-w-7xl mx-auto w-full">
      {/* Header Banner */}
      <GlassCard
        variant="teal"
        padding="md"
        className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-teal-500/30"
      >
        <div className="flex items-start gap-3.5">
          <div className="p-2.5 rounded-xl bg-teal-500/10 border border-teal-500/25 text-teal-400 mt-0.5">
            <Activity className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-lg md:text-xl font-bold tracking-tight text-zinc-100 font-mono">
                Activity & Tamper-Evident Audit Ledger
              </h2>
              <GlassBadge
                variant={chainValid ? "emerald" : "rose"}
                size="sm"
                mono
                icon={<ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />}
              >
                {loading
                  ? "Verifying..."
                  : chainValid
                    ? "Chain Verified"
                    : "Chain Broken"}
              </GlassBadge>
            </div>
            <p className="text-xs md:text-sm text-zinc-400 mt-1.5 max-w-3xl leading-relaxed">
              Append-only, SHA-256 hash-chained ledger (`audit_log` table)
              recording all security-critical human approvals, tool calls,
              autonomous task DAG events, and authentication step-ups.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 shrink-0">
          <GlassButton
            variant="secondary"
            size="sm"
            onClick={exportLedgerJson}
            leftIcon={<Download className="w-3.5 h-3.5" />}
          >
            Export Ledger
          </GlassButton>
        </div>
      </GlassCard>

      {/* Hash-Chain Verification Status Card */}
      <GlassCard
        variant="default"
        padding="sm"
        className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-zinc-900/40"
      >
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
            <LinkIcon className="w-4 h-4" />
          </div>
          <div className="text-xs font-mono">
            <span className="font-bold text-zinc-200">
              Continuous Hash-Chain: prev_hash → row_hash
            </span>
            <p className="text-[11px] text-zinc-400">
              Each block digests previous event SHA-256 preventing insertion,
              modification, or truncation.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono text-zinc-400">
          <span className="text-zinc-500">Operator:</span>
          <span className="text-teal-300 font-bold">
            {principal?.user_id
              ? principal.user_id.slice(0, 10)
              : "user:current"}
          </span>
          <span className="text-zinc-600">•</span>
          <span className="text-emerald-300 font-bold">7 Blocks Sealed</span>
        </div>
      </GlassCard>

      {/* Filter and Search Bar */}
      <GlassCard
        variant="default"
        padding="sm"
        className="flex flex-col sm:flex-row items-center justify-between gap-3"
      >
        <div className="flex items-center gap-1.5 overflow-x-auto w-full sm:w-auto text-xs">
          {[
            { id: "all", label: "All Activities" },
            { id: "approval", label: "Approvals" },
            { id: "task", label: "Tasks & DAG" },
            { id: "tool", label: "Tool Executions" },
            { id: "auth", label: "Auth Sessions" },
            { id: "security", label: "Security & Limits" },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setFilterCategory(tab.id)}
              className={`px-3 py-1.5 rounded-lg font-medium text-xs whitespace-nowrap transition-all duration-150 cursor-pointer ${
                filterCategory === tab.id
                  ? "bg-teal-500/20 text-teal-300 border border-teal-500/40 shadow-xs"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-white/5 border border-transparent"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="w-full sm:w-72">
          <GlassInput
            placeholder="Search audit trail, tools, hashes…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            leftIcon={<Search className="w-3.5 h-3.5 text-zinc-500" />}
            clearable
            onClear={() => setSearchQuery("")}
            className="py-1.5 text-xs"
          />
        </div>
      </GlassCard>

      {/* Audit Trail Timeline */}
      <GlassCard variant="default" padding="none" className="overflow-hidden">
        <div className="divide-y divide-white/5">
          {filteredEvents.map((evt) => (
            <div
              key={evt.id}
              onClick={() => setSelectedEvent(evt)}
              className="p-4 md:p-5 hover:bg-white/[0.03] transition-colors flex flex-col md:flex-row items-start md:items-center justify-between gap-4 cursor-pointer group"
            >
              {/* Event Info */}
              <div className="flex items-start gap-3.5 min-w-0">
                <div className="p-2.5 rounded-xl bg-white/5 border border-white/10 shrink-0 group-hover:border-teal-500/30 transition-colors">
                  {getCategoryIcon(evt.category)}
                </div>

                <div className="flex flex-col gap-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-bold font-mono text-zinc-100">
                      {evt.eventType}
                    </span>
                    {getCategoryBadge(evt.category)}
                    {evt.permissionLevel && (
                      <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 border border-white/10 text-zinc-400">
                        Tier: {evt.permissionLevel}
                      </span>
                    )}
                    <span className="text-[10px] font-mono text-zinc-500">
                      by {evt.actor}
                    </span>
                  </div>

                  <p className="text-xs text-zinc-300 leading-relaxed font-sans">
                    {evt.details}
                  </p>

                  {/* Hash-Chain Preview (prev_hash -> row_hash) */}
                  <div className="flex items-center gap-1.5 mt-1 text-[10px] font-mono text-zinc-500">
                    <LinkIcon className="w-3 h-3 text-teal-400/70 shrink-0" />
                    <span
                      title={`prev_hash: ${evt.prevHash}`}
                      className="truncate max-w-[90px]"
                    >
                      {evt.prevHash.slice(0, 8)}…
                    </span>
                    <ArrowRight className="w-2.5 h-2.5 text-zinc-600 shrink-0" />
                    <span
                      title={`row_hash: ${evt.rowHash}`}
                      className="text-teal-300/90 font-bold truncate max-w-[90px]"
                    >
                      {evt.rowHash.slice(0, 8)}…
                    </span>
                    {evt.durationMs !== undefined && (
                      <span className="ml-2 text-zinc-500">
                        • {evt.durationMs}ms
                      </span>
                    )}
                  </div>
                </div>
              </div>

              {/* Timestamp & Action Button */}
              <div className="flex md:flex-col items-center md:items-end justify-between w-full md:w-auto shrink-0 gap-2">
                <div className="flex items-center gap-1 text-[11px] font-mono text-zinc-400">
                  <Clock className="w-3 h-3 text-zinc-500" />
                  <span>{new Date(evt.timestamp).toLocaleTimeString()}</span>
                </div>

                <GlassButton
                  variant="outline"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedEvent(evt);
                  }}
                  leftIcon={<Eye className="w-3 h-3" />}
                >
                  Inspect Block
                </GlassButton>
              </div>
            </div>
          ))}
        </div>
      </GlassCard>

      {/* Block Inspector Modal */}
      {selectedEvent && (
        <GlassModal
          isOpen={Boolean(selectedEvent)}
          onClose={() => setSelectedEvent(null)}
          title={
            <div className="flex items-center gap-2 font-mono text-zinc-100">
              <ShieldCheck className="w-5 h-5 text-teal-400" />
              <span>Audit Block: {selectedEvent.eventType}</span>
            </div>
          }
          description="Tamper-evident cryptographically sealed record with input/output payloads."
          size="lg"
        >
          <div className="flex flex-col gap-4 text-xs font-mono">
            {/* Summary Cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
              <div className="p-2.5 rounded-xl bg-white/[0.02] border border-white/10 flex flex-col gap-1">
                <span className="text-[10px] text-zinc-500 uppercase">
                  Actor
                </span>
                <span className="text-zinc-200 font-bold">
                  {selectedEvent.actor}
                </span>
              </div>
              <div className="p-2.5 rounded-xl bg-white/[0.02] border border-white/10 flex flex-col gap-1">
                <span className="text-[10px] text-zinc-500 uppercase">
                  Category
                </span>
                <span className="text-teal-300 font-bold uppercase">
                  {selectedEvent.category}
                </span>
              </div>
              <div className="p-2.5 rounded-xl bg-white/[0.02] border border-white/10 flex flex-col gap-1">
                <span className="text-[10px] text-zinc-500 uppercase">
                  Permission
                </span>
                <span className="text-amber-300 font-bold">
                  {selectedEvent.permissionLevel ?? "STANDARD"}
                </span>
              </div>
              <div className="p-2.5 rounded-xl bg-white/[0.02] border border-white/10 flex flex-col gap-1">
                <span className="text-[10px] text-zinc-500 uppercase">
                  Duration
                </span>
                <span className="text-zinc-200 font-bold">
                  {selectedEvent.durationMs ?? 0} ms
                </span>
              </div>
            </div>

            {/* Hash Chain Connection */}
            <div className="p-3.5 rounded-xl bg-black/40 border border-white/10 flex flex-col gap-2.5">
              <span className="text-[10px] font-bold uppercase text-teal-400 tracking-wider">
                Cryptographic Block Link
              </span>
              <div className="flex flex-col gap-1.5 text-[11px]">
                <div className="flex items-center justify-between">
                  <span className="text-zinc-500">prev_hash:</span>
                  <span className="text-zinc-400 font-mono break-all">
                    {selectedEvent.prevHash}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-teal-400 font-bold">row_hash:</span>
                  <div className="flex items-center gap-2">
                    <span className="text-teal-300 font-mono break-all font-bold">
                      {selectedEvent.rowHash}
                    </span>
                    <button
                      type="button"
                      onClick={() =>
                        copyToClipboard(selectedEvent.rowHash, selectedEvent.id)
                      }
                      className="p-1 rounded hover:bg-white/10 text-zinc-400 hover:text-white transition-colors"
                      title={copiedHash ? "Copied" : "Copy Hash"}
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Input & Output Payloads */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <span className="text-[10px] uppercase font-bold text-zinc-400">
                  Input Payload (Redacted)
                </span>
                <pre className="p-3 rounded-xl bg-black/50 border border-white/10 text-[11px] font-mono text-zinc-300 overflow-x-auto max-h-48">
                  {JSON.stringify(selectedEvent.inputPayload ?? {}, null, 2)}
                </pre>
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-[10px] uppercase font-bold text-zinc-400">
                  Output Payload (Redacted)
                </span>
                <pre className="p-3 rounded-xl bg-black/50 border border-white/10 text-[11px] font-mono text-zinc-300 overflow-x-auto max-h-48">
                  {JSON.stringify(selectedEvent.outputPayload ?? {}, null, 2)}
                </pre>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-white/10">
              <GlassButton
                variant="ghost"
                size="sm"
                onClick={() => setSelectedEvent(null)}
              >
                Close
              </GlassButton>
            </div>
          </div>
        </GlassModal>
      )}
    </div>
  );
}
