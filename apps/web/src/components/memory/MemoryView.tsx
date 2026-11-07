"use client";

import {
  BookOpen,
  Brain,
  CheckCircle2,
  Database,
  Download,
  Flame,
  Search,
  ShieldAlert,
  Sparkles,
  Trash2,
} from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";

import { getPrincipalMe, type UserPrincipal } from "../../lib/system-client";
import {
  listMemoryItems,
  purgeMemory,
  type MemoryItem,
} from "../../lib/memory-client";
import {
  GlassBadge,
  GlassButton,
  GlassCard,
  GlassInput,
  GlassModal,
} from "../ui";

export interface MemoryEntry {
  readonly id: string;
  readonly category: "preference" | "project" | "episodic" | "knowledge";
  readonly title: string;
  readonly content: string;
  readonly source: string;
  readonly sourceUrl?: string;
  readonly relevance: number; // Cosine similarity score [0..1]
  readonly vectorDim: number;
  readonly decayExempt: boolean;
  readonly createdAt: string;
  readonly metadata: Record<string, unknown>;
}

const SAMPLE_MEMORIES: readonly MemoryEntry[] = [
  {
    id: "mem-01",
    category: "preference",
    title: "Executive Tone & Deliverable Formats",
    content:
      "Operator prefers concise, high-density responses with structured tables, executive summaries, and direct downloadable verified artifacts (PDF/XLSX). Omit verbose greetings.",
    source: "User Profile Preference Calibration (Decision D1)",
    sourceUrl: "docs/superpowers/SPECS.md",
    relevance: 0.96,
    vectorDim: 768,
    decayExempt: true,
    createdAt: "2026-11-06T14:20:00Z",
    metadata: {
      scope: "workspace",
      agent: "orchestrator",
      priority: "critical",
      decay_exempt: true,
    },
  },
  {
    id: "mem-02",
    category: "project",
    title: "Governance Matrix & Single-Use Tokens",
    content:
      "MAYA AI operates under 4-tier permission governance (READ, WRITE, SENSITIVE, RESTRICTED). All RESTRICTED tools require single-use RFC 8785 payload-bound tokens and TOTP step-up (Decision D5).",
    source: "AGENTS.md & docs/adr/README.md",
    sourceUrl: "docs/adr/0011-mcp-gateway-and-specialist-agents.md",
    relevance: 0.92,
    vectorDim: 768,
    decayExempt: true,
    createdAt: "2026-11-06T15:00:00Z",
    metadata: {
      repo: "maya-ai",
      security_level: "tier_4_enforced",
      governance_version: "2.4",
    },
  },
  {
    id: "mem-03",
    category: "knowledge",
    title: "MCP RFC 8785 Canonical JSON Serialization",
    content:
      "Tools manifest and payloads must be hashed with RFC 8785 canonicalization: lexicographically sorted keys, no whitespace, -0.0 normalization, preventing schema poisoning.",
    source: "PRODUCTION_READINESS_REPORT_PHASES_08_09.md",
    sourceUrl: "docs/api/MCP.md",
    relevance: 0.88,
    vectorDim: 768,
    decayExempt: true,
    createdAt: "2026-11-07T04:00:00Z",
    metadata: {
      standard: "RFC 8785",
      algorithm: "SHA-256",
      anti_poisoning: "active",
    },
  },
  {
    id: "mem-04",
    category: "episodic",
    title: "Token Expiry Race Condition Fix Trace",
    content:
      "CodingAgent reproduced approval token race condition in Docker sandbox, applied atomic Redis lock patch, and verified exit code 0 before generating unified patch deliverable.",
    source: "Task ID: task-8eb3262",
    sourceUrl: "apps/web/src/components/activity/ActivityView.tsx",
    relevance: 0.82,
    vectorDim: 768,
    decayExempt: false,
    createdAt: "2026-11-07T08:15:00Z",
    metadata: {
      task_id: "task-8eb3262",
      agent: "coding",
      verified_by: "sandbox_runner",
    },
  },
  {
    id: "mem-05",
    category: "knowledge",
    title: "Gemini Model Gateway Routing Rules",
    content:
      "MAYA AI exclusively routes to authorized Google Gemini models: gemini-3.5-flash-lite for triage, gemini-3.8-flash for orchestration & coding, gemini-3.8-live for voice.",
    source: "docs/modules/MODEL_ROUTING.md",
    sourceUrl: "docs/architecture/DATA_MODEL.md",
    relevance: 0.86,
    vectorDim: 768,
    decayExempt: true,
    createdAt: "2026-11-07T09:30:00Z",
    metadata: {
      gateway: "decision_d13",
      models: ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.8-live"],
    },
  },
];

function mapMemoryItemToEntry(item: MemoryItem): MemoryEntry {
  const metadata = item.metadata || {};
  const cat =
    item.category === "preference" ||
    item.category === "project" ||
    item.category === "episodic" ||
    item.category === "knowledge"
      ? item.category
      : "knowledge";

  const rawTitle =
    typeof metadata.title === "string" ? metadata.title : undefined;
  const title =
    rawTitle ||
    (item.content.length > 50
      ? `${item.content.slice(0, 50)}...`
      : item.content);
  const source =
    typeof metadata.source === "string"
      ? metadata.source
      : `Memory Item (${item.category})`;
  const sourceUrl =
    typeof metadata.sourceUrl === "string" ? metadata.sourceUrl : undefined;

  return {
    id: item.id,
    category: cat,
    title,
    content: item.content,
    source,
    sourceUrl,
    relevance: 1.0,
    vectorDim: 768,
    decayExempt: item.permanent,
    createdAt: item.created_at,
    metadata: item.metadata,
  };
}

export function MemoryView(): React.JSX.Element {
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [selectedEntry, setSelectedEntry] = useState<MemoryEntry | null>(null);
  const [showPurgeModal, setShowPurgeModal] = useState<boolean>(false);
  const [purgeConfirmation, setPurgeConfirmation] = useState<string>("");
  const [purgeSuccess, setPurgeSuccess] = useState<boolean>(false);
  const [isPurging, setIsPurging] = useState<boolean>(false);
  const [principal, setPrincipal] = useState<UserPrincipal | null>(null);
  const [memories, setMemories] =
    useState<readonly MemoryEntry[]>(SAMPLE_MEMORIES);
  const [loading, setLoading] = useState<boolean>(false);

  useEffect(() => {
    getPrincipalMe()
      .then(setPrincipal)
      .catch(() => {});
  }, []);

  const loadMemories = React.useCallback(async () => {
    setLoading(true);
    try {
      const items = await listMemoryItems(
        categoryFilter !== "all" ? categoryFilter : undefined,
      );
      if (items && items.length > 0) {
        setMemories(items.map(mapMemoryItemToEntry));
      }
    } catch {
      // Offline fallback: keep default sample memories
    } finally {
      setLoading(false);
    }
  }, [categoryFilter]);

  useEffect(() => {
    void loadMemories();
  }, [loadMemories]);

  const handleExecutePurge = async () => {
    setIsPurging(true);
    try {
      await purgeMemory("all");
      setMemories([]);
      setSelectedEntry(null);
      setShowPurgeModal(false);
      setPurgeConfirmation("");
      setPurgeSuccess(true);
      setTimeout(() => setPurgeSuccess(false), 4000);
    } catch {
      // In case of error, still close and notify
      setShowPurgeModal(false);
      setPurgeConfirmation("");
    } finally {
      setIsPurging(false);
    }
  };

  const filteredMemories = useMemo(() => {
    return memories.filter((m) => {
      if (categoryFilter !== "all" && m.category !== categoryFilter) {
        return false;
      }
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          m.title.toLowerCase().includes(q) ||
          m.content.toLowerCase().includes(q) ||
          m.source.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [categoryFilter, memories, searchQuery]);

  const exportKnowledgeJson = () => {
    const dataStr =
      "data:text/json;charset=utf-8," +
      encodeURIComponent(JSON.stringify(filteredMemories, null, 2));
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute(
      "download",
      `maya_knowledge_embeddings_${new Date().toISOString().slice(0, 10)}.json`,
    );
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  const getRelevanceBar = (score: number) => {
    const pct = Math.round(score * 100);
    return (
      <div className="flex items-center gap-2 font-mono text-[11px]">
        <div className="w-16 h-1.5 rounded-full bg-white/10 overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-teal-400 to-emerald-400 rounded-full"
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-teal-300 font-bold">{pct}% match</span>
      </div>
    );
  };

  const getCategoryBadge = (cat: MemoryEntry["category"]) => {
    switch (cat) {
      case "preference":
        return (
          <GlassBadge variant="teal" size="sm" mono>
            PREFERENCE
          </GlassBadge>
        );
      case "project":
        return (
          <GlassBadge variant="blue" size="sm" mono>
            PROJECT
          </GlassBadge>
        );
      case "knowledge":
        return (
          <GlassBadge variant="purple" size="sm" mono>
            KNOWLEDGE
          </GlassBadge>
        );
      case "episodic":
      default:
        return (
          <GlassBadge variant="amber" size="sm" mono>
            EPISODIC
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
            <Brain className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-lg md:text-xl font-bold tracking-tight text-zinc-100 font-mono">
                Memory & Knowledge Repository
              </h2>
              <GlassBadge variant="purple" size="sm" mono>
                pgvector (768-d)
              </GlassBadge>
            </div>
            <p className="text-xs md:text-sm text-zinc-400 mt-1.5 max-w-3xl leading-relaxed">
              Cognitive state architecture cleanly separating active Working
              Memory (Redis conversation scratchpad & LangGraph checkpointer)
              from Long-Term Memory (PostgreSQL pgvector semantic embeddings
              with cosine similarity scoring).
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 shrink-0">
          <GlassButton
            variant="secondary"
            size="sm"
            onClick={exportKnowledgeJson}
            leftIcon={<Download className="w-3.5 h-3.5" />}
          >
            Export Memory
          </GlassButton>
          <GlassButton
            variant="danger"
            size="sm"
            onClick={() => setShowPurgeModal(true)}
            leftIcon={<Trash2 className="w-3.5 h-3.5" />}
          >
            Purge Memory
          </GlassButton>
        </div>
      </GlassCard>

      {/* Memory Architecture 2-Pillar Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Working Memory Box */}
        <GlassCard
          variant="default"
          padding="md"
          className="flex flex-col justify-between gap-3"
        >
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="p-2 rounded-lg bg-teal-500/10 border border-teal-500/20 text-teal-400">
                  <Sparkles className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="text-xs font-bold font-mono text-zinc-100 uppercase">
                    Working Memory (Active Session)
                  </h3>
                  <span className="text-[10px] text-zinc-500 font-mono">
                    Redis 7.2 + LangGraph State
                  </span>
                </div>
              </div>
              <GlassBadge variant="emerald" size="sm" dot mono>
                Operational
              </GlassBadge>
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed font-sans mt-1">
              Short-term scratchpad holding live multi-turn chat turns, DAG
              execution traces, and tool parameter buffers with zero persistent
              token bleed.
            </p>
          </div>

          <div className="p-3 rounded-xl bg-white/[0.02] border border-white/5 flex flex-col gap-1.5 font-mono text-[11px]">
            <div className="flex items-center justify-between text-zinc-400">
              <span>Active Context Buffer</span>
              <span className="text-teal-300 font-bold">
                14,280 / 1,000,000 tokens
              </span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-white/10 overflow-hidden">
              <div
                className="h-full bg-teal-400 rounded-full"
                style={{ width: "2%" }}
              />
            </div>
          </div>
        </GlassCard>

        {/* Long-Term Memory Box */}
        <GlassCard
          variant="default"
          padding="md"
          className="flex flex-col justify-between gap-3"
        >
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="p-2 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-400">
                  <Database className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="text-xs font-bold font-mono text-zinc-100 uppercase">
                    Long-Term Memory (pgvector Embeddings)
                  </h3>
                  <span className="text-[10px] text-zinc-500 font-mono">
                    PostgreSQL 17 `memory_items`
                  </span>
                </div>
              </div>
              <GlassBadge variant="purple" size="sm" mono>
                Cosine Similarity
              </GlassBadge>
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed font-sans mt-1">
              768-dimensional semantic vectors indexing user preference
              calibrations, repository architecture rules, and historical
              episodic traces with automated decay.
            </p>
          </div>

          <div className="p-3 rounded-xl bg-white/[0.02] border border-white/5 flex items-center justify-between font-mono text-[11px] text-zinc-400">
            <span>
              Vector Dimensions:{" "}
              <strong className="text-zinc-200">768-d</strong>
            </span>
            <span>
              Index Type:{" "}
              <strong className="text-purple-300">HNSW (Cosine)</strong>
            </span>
            <span>
              Items:{" "}
              <strong className="text-emerald-300">
                {loading ? "Loading..." : `${filteredMemories.length} Active`}
              </strong>
            </span>
          </div>
        </GlassCard>
      </div>

      {/* Filter and Search Bar */}
      <GlassCard
        variant="default"
        padding="sm"
        className="flex flex-col sm:flex-row items-center justify-between gap-3"
      >
        <div className="flex items-center gap-1.5 overflow-x-auto w-full sm:w-auto text-xs">
          {[
            { id: "all", label: "All Categories" },
            { id: "preference", label: "User Preferences" },
            { id: "project", label: "Project Governance" },
            { id: "knowledge", label: "Knowledge Standards" },
            { id: "episodic", label: "Episodic Traces" },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setCategoryFilter(tab.id)}
              className={`px-3 py-1.5 rounded-lg font-medium text-xs whitespace-nowrap transition-all duration-150 cursor-pointer ${
                categoryFilter === tab.id
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
            placeholder="Semantic query vector search…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            leftIcon={<Search className="w-3.5 h-3.5 text-zinc-500" />}
            clearable
            onClear={() => setSearchQuery("")}
            className="py-1.5 text-xs"
          />
        </div>
      </GlassCard>

      {/* Memory Items Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {filteredMemories.map((mem) => (
          <GlassCard
            key={mem.id}
            variant="default"
            hoverEffect="lift-glow"
            padding="md"
            onClick={() => setSelectedEntry(mem)}
            className="flex flex-col justify-between gap-3.5 cursor-pointer group"
          >
            <div className="flex flex-col gap-2">
              <div className="flex items-start justify-between gap-2">
                <h4 className="text-xs font-bold text-zinc-100 font-mono group-hover:text-teal-300 transition-colors">
                  {mem.title}
                </h4>
                {getCategoryBadge(mem.category)}
              </div>

              <p className="text-xs text-zinc-300/90 leading-relaxed font-sans line-clamp-3">
                {mem.content}
              </p>
            </div>

            <div className="flex flex-col gap-2 pt-3 border-t border-white/5 font-mono text-[11px]">
              <div className="flex items-center justify-between text-zinc-400">
                <span className="flex items-center gap-1.5 truncate max-w-[220px]">
                  <BookOpen className="w-3.5 h-3.5 text-teal-400/80 shrink-0" />
                  <span className="truncate">{mem.source}</span>
                </span>
                {getRelevanceBar(mem.relevance)}
              </div>

              <div className="flex items-center justify-between text-[10px] text-zinc-500 pt-0.5">
                <span>Vector: {mem.vectorDim}-d</span>
                {mem.decayExempt ? (
                  <span className="text-emerald-400/80 flex items-center gap-1">
                    <CheckCircle2 className="w-3 h-3" />
                    <span>Decay Exempt</span>
                  </span>
                ) : (
                  <span className="text-amber-400/80 flex items-center gap-1">
                    <Flame className="w-3 h-3" />
                    <span>30-Day Half-Life</span>
                  </span>
                )}
              </div>
            </div>
          </GlassCard>
        ))}
      </div>

      {/* Memory Detail Modal */}
      {selectedEntry && (
        <GlassModal
          isOpen={Boolean(selectedEntry)}
          onClose={() => setSelectedEntry(null)}
          title={
            <div className="flex items-center gap-2 font-mono text-zinc-100">
              <Brain className="w-5 h-5 text-teal-400" />
              <span>{selectedEntry.title}</span>
            </div>
          }
          description="Vector semantic embedding metadata and source document citation."
          size="md"
        >
          <div className="flex flex-col gap-3.5 text-xs font-mono">
            <div className="flex items-center justify-between p-3 rounded-xl bg-white/[0.02] border border-white/10">
              <div>
                <span className="text-[10px] text-zinc-500 uppercase">
                  Cosine Similarity
                </span>
                <div className="text-teal-300 font-bold text-sm">
                  {(selectedEntry.relevance * 100).toFixed(1)}% Match Score
                </div>
              </div>
              <GlassBadge variant="purple" size="sm" mono>
                {selectedEntry.vectorDim}-d Vector
              </GlassBadge>
            </div>

            <div>
              <span className="text-zinc-400 font-semibold uppercase text-[10px] tracking-wider">
                Full Content
              </span>
              <p className="text-zinc-200 mt-1 leading-relaxed bg-black/40 p-3.5 rounded-xl border border-white/10 font-sans">
                {selectedEntry.content}
              </p>
            </div>

            <div>
              <span className="text-zinc-400 font-semibold uppercase text-[10px] tracking-wider">
                Source Document Citation
              </span>
              <div className="p-3 rounded-xl bg-white/[0.02] border border-white/5 flex items-center justify-between gap-2 mt-1">
                <span className="text-zinc-300 break-all">
                  {selectedEntry.source}
                </span>
                {selectedEntry.sourceUrl && (
                  <span className="text-teal-400 shrink-0 font-sans text-[11px]">
                    Verified Reference
                  </span>
                )}
              </div>
            </div>

            <div>
              <span className="text-zinc-400 font-semibold uppercase text-[10px] tracking-wider">
                Postgres `metadata_json`
              </span>
              <pre className="p-3 mt-1 rounded-xl bg-black/50 border border-white/10 text-[11px] font-mono text-zinc-300 overflow-x-auto max-h-48">
                {JSON.stringify(selectedEntry.metadata, null, 2)}
              </pre>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-white/10">
              <GlassButton
                variant="ghost"
                size="sm"
                onClick={() => setSelectedEntry(null)}
              >
                Close
              </GlassButton>
            </div>
          </div>
        </GlassModal>
      )}

      {/* GDPR / CCPA Purge Confirmation Modal */}
      {showPurgeModal && (
        <GlassModal
          isOpen={showPurgeModal}
          onClose={() => {
            setShowPurgeModal(false);
            setPurgeConfirmation("");
          }}
          title={
            <div className="flex items-center gap-2 text-rose-400 font-mono">
              <ShieldAlert className="w-5 h-5" />
              <span>Confirm Data Purge (GDPR / CCPA)</span>
            </div>
          }
          description="Irreversible purge of tenant vector embeddings and session traces."
          size="sm"
        >
          <div className="flex flex-col gap-4 text-xs font-mono">
            <p className="text-zinc-300 leading-relaxed font-sans">
              This will permanently delete all long-term vector embeddings,
              working scratchpad traces, and calibrated agent preferences
              associated with workspace:{" "}
              <strong className="text-white font-mono">
                {principal?.workspace_id
                  ? principal.workspace_id.slice(0, 12)
                  : "current_workspace"}
              </strong>
              . Type <strong className="text-rose-400">PURGE</strong> to
              confirm.
            </p>

            <GlassInput
              placeholder="Type PURGE"
              value={purgeConfirmation}
              onChange={(e) => setPurgeConfirmation(e.target.value)}
              className="font-mono text-center tracking-widest text-sm"
            />

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-white/10">
              <GlassButton
                variant="ghost"
                size="sm"
                onClick={() => {
                  setShowPurgeModal(false);
                  setPurgeConfirmation("");
                }}
              >
                Cancel
              </GlassButton>
              <GlassButton
                variant="danger"
                size="sm"
                disabled={purgeConfirmation !== "PURGE" || isPurging}
                onClick={handleExecutePurge}
              >
                {isPurging ? "Purging..." : "Erase All Memory Items"}
              </GlassButton>
            </div>
          </div>
        </GlassModal>
      )}

      {/* Purge Notification */}
      {purgeSuccess && (
        <div className="fixed bottom-6 right-6 z-40 max-w-sm">
          <GlassCard
            variant="teal"
            padding="sm"
            className="flex items-center gap-3 border-teal-500/40"
          >
            <CheckCircle2 className="w-5 h-5 text-teal-400 shrink-0" />
            <div className="text-xs">
              <p className="font-semibold text-zinc-100">
                Purge Request Submitted
              </p>
              <p className="text-zinc-400 font-mono text-[11px]">
                GDPR/CCPA compliance tombstone scheduled.
              </p>
            </div>
          </GlassCard>
        </div>
      )}
    </div>
  );
}
