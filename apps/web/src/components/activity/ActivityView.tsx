"use client";

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock,
  Copy,
  Download,
  Eye,
  Hourglass,
  Link as LinkIcon,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Square,
  Timer,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import React, { useCallback, useEffect, useMemo, useState } from "react";

import { listAuditEvents } from "../../lib/audit-client";
import {
  Badge,
  Button,
  CodeBlockShell,
  EmptyState,
  ErrorState,
  GlassInput,
  GlassModal,
  Pane,
  Skeleton,
  Truncate,
  WrapText,
  type Tone,
} from "../ui";

export interface AuditEvent {
  readonly id: string;
  readonly timestamp: string;
  readonly eventType: string;
  readonly category: "auth" | "task" | "tool" | "approval" | "security";
  readonly actor: string;
  readonly details: string;
  readonly status: AuditStatus | (string & {});
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

export type AuditStatus =
  | "success"
  | "failed"
  | "denied"
  | "pending"
  | "rejected"
  | "warning"
  | "aborted"
  | "timeout"
  | "expired";

type ChainState =
  | { readonly kind: "unknown" }
  | { readonly kind: "valid"; readonly length: number }
  | { readonly kind: "broken"; readonly firstBadId?: string };

/** Rows per request (server maximum is 100). */
const PAGE_SIZE = 50;

/**
 * The whole status vocabulary, each with its own icon and word. Failed and denied differ in icon
 * and wording, and no status relies on colour alone.
 */
const STATUS: Readonly<
  Record<
    AuditStatus,
    { readonly text: string; readonly tone: Tone; readonly icon: LucideIcon }
  >
> = {
  success: { text: "SUCCESS", tone: "success", icon: CheckCircle2 },
  failed: { text: "FAILED", tone: "danger", icon: XCircle },
  denied: { text: "DENIED", tone: "danger", icon: Ban },
  rejected: { text: "REJECTED", tone: "warning", icon: ShieldAlert },
  warning: { text: "WARNING", tone: "warning", icon: AlertTriangle },
  pending: { text: "PENDING", tone: "neutral", icon: Hourglass },
  aborted: { text: "ABORTED", tone: "neutral", icon: Square },
  timeout: { text: "TIMEOUT", tone: "warning", icon: Timer },
  expired: { text: "EXPIRED", tone: "neutral", icon: Clock },
};

function StatusBadge({ status }: { readonly status: string }) {
  const known = (STATUS as Record<string, (typeof STATUS)[AuditStatus]>)[
    status
  ];
  const spec = known ?? {
    text: status.toUpperCase(),
    tone: "neutral" as Tone,
    icon: ShieldQuestion,
  };
  return (
    <Badge tone={spec.tone} icon={spec.icon} data-status={status}>
      {spec.text}
    </Badge>
  );
}

const CATEGORIES: readonly { readonly id: string; readonly label: string }[] = [
  { id: "all", label: "All" },
  { id: "approval", label: "Approvals" },
  { id: "task", label: "Tasks" },
  { id: "tool", label: "Tools" },
  { id: "auth", label: "Sign-in" },
  { id: "security", label: "Security" },
];

const CATEGORY_LABEL: Readonly<Record<string, string>> = {
  approval: "Approval",
  task: "Task",
  tool: "Tool",
  auth: "Sign-in",
  security: "Security",
};

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

const LABEL = "text-maya-caption font-medium text-maya-muted";

export function ActivityView(): React.JSX.Element {
  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);
  const [copiedHash, setCopiedHash] = useState<string | null>(null);
  const [events, setEvents] = useState<readonly AuditEvent[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [chain, setChain] = useState<ChainState>({ kind: "unknown" });

  const loadAuditLogs = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    setMoreError(null);
    try {
      const res = await listAuditEvents({
        category: filterCategory !== "all" ? filterCategory : undefined,
        limit: PAGE_SIZE,
        offset: 0,
      });
      setEvents(res.items);
      setTotal(res.total ?? res.items.length);
      setChain(
        res.chain_valid
          ? { kind: "valid", length: res.chain_length ?? 0 }
          : {
              kind: "broken",
              firstBadId: res.chain_first_bad_id ?? undefined,
            },
      );
    } catch {
      setEvents([]);
      setTotal(0);
      setChain({ kind: "unknown" });
      setLoadError("Unable to load the audit ledger from the MAYA backend.");
    } finally {
      setLoading(false);
    }
  }, [filterCategory]);

  useEffect(() => {
    void loadAuditLogs();
  }, [loadAuditLogs]);

  const loadMore = async (): Promise<void> => {
    if (loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const res = await listAuditEvents({
        category: filterCategory !== "all" ? filterCategory : undefined,
        limit: PAGE_SIZE,
        offset: events.length,
      });
      setEvents((prev) => {
        const seen = new Set(prev.map((e) => e.id));
        return [...prev, ...res.items.filter((e) => !seen.has(e.id))];
      });
      setTotal(res.total ?? total);
    } catch {
      setMoreError("Could not load more rows. Try again.");
    } finally {
      setLoadingMore(false);
    }
  };

  const copyToClipboard = (hash: string, id: string) => {
    void navigator.clipboard?.writeText(hash);
    setCopiedHash(id);
    setTimeout(() => setCopiedHash(null), 2000);
  };

  const filteredEvents = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return events.filter((evt) => {
      if (filterCategory !== "all" && evt.category !== filterCategory) {
        return false;
      }
      if (q === "") return true;
      return (
        evt.eventType.toLowerCase().includes(q) ||
        evt.details.toLowerCase().includes(q) ||
        (evt.toolName?.toLowerCase().includes(q) ?? false) ||
        (evt.taskId?.toLowerCase().includes(q) ?? false) ||
        evt.rowHash.toLowerCase().includes(q)
      );
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

  const chainBadge = (() => {
    if (loading) {
      return (
        <Badge tone="neutral" icon={ShieldQuestion}>
          Chain: verifying
        </Badge>
      );
    }
    if (chain.kind === "valid") {
      return (
        <Badge tone="success" icon={ShieldCheck}>
          Chain verified
        </Badge>
      );
    }
    if (chain.kind === "broken") {
      return (
        <Badge tone="danger" icon={ShieldAlert}>
          {chain.firstBadId
            ? `Chain broken at row ${chain.firstBadId}`
            : "Chain broken"}
        </Badge>
      );
    }
    return (
      <Badge tone="warning" icon={ShieldQuestion}>
        Chain status unknown
      </Badge>
    );
  })();

  const loaded = events.length;
  const countText = loading
    ? "Loading…"
    : `${loaded} of ${Math.max(total, loaded)} rows loaded`;

  const header = (
    <div className="grid min-w-0 gap-3 py-1">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="m-0 text-maya-heading font-medium">Activity</h1>
          {chainBadge}
          <span
            className="text-maya-caption text-maya-muted"
            aria-live="polite"
          >
            {countText}
            {!loading && chain.kind === "valid" && chain.length > 0
              ? `, ${chain.length} verified`
              : ""}
          </span>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={exportLedgerJson}
          disabled={filteredEvents.length === 0}
          leftIcon={<Download size={14} />}
        >
          Export Ledger
        </Button>
      </div>

      <div
        role="group"
        aria-label="Filter by category"
        className="flex min-w-0 flex-wrap items-center gap-1.5"
      >
        {CATEGORIES.map((tab) => (
          <Button
            key={tab.id}
            size="sm"
            variant={filterCategory === tab.id ? "primary" : "secondary"}
            aria-pressed={filterCategory === tab.id}
            onClick={() => setFilterCategory(tab.id)}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      <div className="min-w-0 max-w-xl">
        <GlassInput
          placeholder="Search loaded rows"
          aria-label="Search loaded rows"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          leftIcon={<Search size={14} aria-hidden="true" />}
          clearable
          onClear={() => setSearchQuery("")}
        />
      </div>
    </div>
  );

  return (
    <div className="h-full min-h-0 min-w-0">
      <Pane
        aria-label="Activity"
        header={header}
        bodyLabel="Audit rows"
        bodyClassName="p-4"
      >
        <div className="mx-auto grid min-w-0 max-w-5xl gap-3">
          {loadError !== null && (
            <ErrorState
              title={loadError}
              retry={
                <Button
                  size="sm"
                  variant="secondary"
                  leftIcon={<RefreshCw size={14} />}
                  onClick={() => void loadAuditLogs()}
                >
                  Retry
                </Button>
              }
            />
          )}

          {loading && (
            <div aria-busy="true" className="grid gap-3">
              <p className="m-0 text-maya-small text-maya-muted">
                Loading the audit ledger…
              </p>
              <Skeleton lines={3} />
              <Skeleton lines={3} />
            </div>
          )}

          {!loading && loadError === null && events.length === 0 && (
            <EmptyState
              title="No audit events recorded yet."
              description="Approvals, tool calls, task events and sign-ins are recorded here as they happen."
            />
          )}

          {!loading && events.length > 0 && filteredEvents.length === 0 && (
            <EmptyState
              title="No loaded rows match your search."
              description={
                loaded < total
                  ? "Search only covers rows already loaded. Load more rows to search further back."
                  : undefined
              }
            />
          )}

          {filteredEvents.length > 0 && (
            <ul className="m-0 grid min-w-0 list-none gap-2 p-0">
              {filteredEvents.map((evt) => (
                <li
                  key={evt.id}
                  className="grid min-w-0 shrink-0 gap-2 rounded-maya-lg border border-maya-border bg-maya-raised p-3"
                >
                  <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                    <StatusBadge status={evt.status} />
                    <Badge tone="neutral">
                      {CATEGORY_LABEL[evt.category] ?? evt.category}
                    </Badge>
                    <WrapText className="min-w-0 font-mono text-maya-mono font-medium text-maya-text">
                      {evt.eventType}
                    </WrapText>
                  </div>

                  <WrapText
                    as="p"
                    dir="auto"
                    className="m-0 block text-maya-small text-maya-secondary"
                  >
                    {evt.details}
                  </WrapText>

                  <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 text-maya-caption text-maya-muted">
                    <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                      <time dateTime={evt.timestamp}>
                        {formatWhen(evt.timestamp)}
                      </time>
                      <Truncate className="max-w-64" title={evt.actor}>
                        {`by ${evt.actor}`}
                      </Truncate>
                      {evt.permissionLevel && (
                        <span>Tier: {evt.permissionLevel}</span>
                      )}
                      {evt.durationMs !== undefined && (
                        <span>{evt.durationMs} ms</span>
                      )}
                      <span
                        className="inline-flex min-w-0 items-center gap-1 font-mono"
                        title={`prev ${evt.prevHash} to row ${evt.rowHash}`}
                      >
                        <LinkIcon
                          aria-hidden="true"
                          size={14}
                          className="shrink-0"
                        />
                        {evt.prevHash.slice(0, 8)}…{evt.rowHash.slice(0, 8)}
                      </span>
                    </span>
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label={`Inspect block ${evt.eventType}`}
                      leftIcon={<Eye size={14} />}
                      onClick={() => setSelectedEvent(evt)}
                    >
                      Inspect Block
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {moreError !== null && (
            <p role="alert" className="m-0 text-maya-small text-maya-danger">
              {moreError}
            </p>
          )}
          {!loading && loaded < total && (
            <div className="flex justify-center">
              <Button
                variant="secondary"
                loading={loadingMore}
                onClick={() => void loadMore()}
              >
                Load more
              </Button>
            </div>
          )}
        </div>
      </Pane>

      {selectedEvent && (
        <GlassModal
          isOpen
          onClose={() => setSelectedEvent(null)}
          title={`Audit Block: ${selectedEvent.eventType}`}
          description="The sealed record, with its input and output as stored."
          size="lg"
        >
          <div className="grid min-w-0 gap-4 text-maya-small">
            <dl className="m-0 grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
              {[
                [
                  "Status",
                  <StatusBadge key="s" status={selectedEvent.status} />,
                ],
                ["When", formatWhen(selectedEvent.timestamp)],
                ["Actor", selectedEvent.actor],
                [
                  "Category",
                  CATEGORY_LABEL[selectedEvent.category] ??
                    selectedEvent.category,
                ],
                ["Permission", selectedEvent.permissionLevel ?? "Not recorded"],
                [
                  "Duration",
                  selectedEvent.durationMs !== undefined
                    ? `${selectedEvent.durationMs} ms`
                    : "Not recorded",
                ],
              ].map(([k, v]) => (
                <div key={String(k)} className="grid min-w-0 gap-0.5">
                  <dt className={LABEL}>{k}</dt>
                  <dd className="m-0 min-w-0 text-maya-text [overflow-wrap:anywhere]">
                    {v}
                  </dd>
                </div>
              ))}
            </dl>

            <div className="grid min-w-0 gap-2 rounded-maya-md border border-maya-border bg-maya-code p-3">
              <span className={LABEL}>Hash link</span>
              <div className="grid min-w-0 gap-0.5">
                <span className="text-maya-caption text-maya-muted">
                  Previous hash
                </span>
                <WrapText className="font-mono text-maya-mono text-maya-secondary">
                  {selectedEvent.prevHash}
                </WrapText>
              </div>
              <div className="grid min-w-0 gap-0.5">
                <span className="text-maya-caption text-maya-muted">
                  Row hash
                </span>
                <span className="flex min-w-0 items-start gap-2">
                  <WrapText className="min-w-0 flex-1 font-mono text-maya-mono text-maya-text">
                    {selectedEvent.rowHash}
                  </WrapText>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label="Copy row hash"
                    leftIcon={<Copy size={14} />}
                    onClick={() =>
                      copyToClipboard(selectedEvent.rowHash, selectedEvent.id)
                    }
                  >
                    {copiedHash === selectedEvent.id ? "Copied" : "Copy"}
                  </Button>
                </span>
              </div>
            </div>

            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              <div className="grid min-w-0 gap-1">
                <span className={LABEL}>Input (redacted)</span>
                <CodeBlockShell label="Input payload">
                  {JSON.stringify(selectedEvent.inputPayload ?? {}, null, 2)}
                </CodeBlockShell>
              </div>
              <div className="grid min-w-0 gap-1">
                <span className={LABEL}>Output (redacted)</span>
                <CodeBlockShell label="Output payload">
                  {JSON.stringify(selectedEvent.outputPayload ?? {}, null, 2)}
                </CodeBlockShell>
              </div>
            </div>

            <div className="flex justify-end border-t border-maya-border pt-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedEvent(null)}
              >
                Close
              </Button>
            </div>
          </div>
        </GlassModal>
      )}
    </div>
  );
}
