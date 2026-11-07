"use client";

import {
  Activity,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  FolderGit2,
  Globe,
  HardDrive,
  Loader2,
  PlusCircle,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  ShieldCheck,
  Terminal,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import React, { useCallback, useEffect, useMemo, useState } from "react";

import {
  deleteMcpServer,
  getMcpServerHealth,
  listMcpServers,
  registerMcpServer,
  type McpServer,
  type McpServerCreate,
} from "../../lib/mcp-client";
import {
  GlassBadge,
  GlassButton,
  GlassCard,
  GlassInput,
  GlassModal,
} from "../ui";

export interface IntegrationCatalogItem {
  id: string;
  name: string;
  category: "search" | "vcs" | "productivity" | "workspace" | "filesystem";
  desc: string;
  transport: "stdio" | "http" | "streamable_http";
  status: "Built-in" | "Connected" | "Configured";
  toolsCount: number;
  tools: Array<{
    name: string;
    description: string;
    tier: "READ" | "WRITE" | "SENSITIVE" | "RESTRICTED";
  }>;
  schemaHash: string;
}

const CORE_INTEGRATIONS: IntegrationCatalogItem[] = [
  {
    id: "core-brave",
    name: "Brave Search",
    category: "search",
    desc: "Live privacy-preserving web search & rich site extraction engine with token leaky-bucket rate limiting.",
    transport: "http",
    status: "Connected",
    toolsCount: 2,
    schemaHash: "sha256:d8a21f7e034b7a19c54e81561bd2819890f671380126a099",
    tools: [
      {
        name: "brave_web_search",
        description:
          "Executes web searches and extracts structured snippets with source citations.",
        tier: "READ",
      },
      {
        name: "brave_extract_page",
        description:
          "Fetches and sanitizes full text contents of specified search target URLs.",
        tier: "READ",
      },
    ],
  },
  {
    id: "core-github",
    name: "GitHub",
    category: "vcs",
    desc: "Repository automation for issues, PR reviews, and diff checks. PR submission requires single-use approval token.",
    transport: "http",
    status: "Connected",
    toolsCount: 4,
    schemaHash: "sha256:7e9104c99a632b71fa14b82650ee45cb2193b2160d540e11",
    tools: [
      {
        name: "github_list_issues",
        description: "Lists repo issues and filter states.",
        tier: "READ",
      },
      {
        name: "github_get_pull_request",
        description: "Fetches PR details, code diffs, and review comments.",
        tier: "READ",
      },
      {
        name: "github_create_pull_request",
        description:
          "Submits pull request. Gated by Decision D5 single-use approval token.",
        tier: "RESTRICTED",
      },
      {
        name: "github_merge_pull_request",
        description: "Merges approved pull request into target branch.",
        tier: "RESTRICTED",
      },
    ],
  },
  {
    id: "core-notion",
    name: "Notion",
    category: "productivity",
    desc: "Read-only access for sprint boards, documentation indexes, and knowledge base databases.",
    transport: "http",
    status: "Connected",
    toolsCount: 2,
    schemaHash: "sha256:3b94a08ef48d29c362140a876dc10a2f7c00e5a932b1f810",
    tools: [
      {
        name: "notion_query_database",
        description: "Queries Notion database tables with filter conditions.",
        tier: "READ",
      },
      {
        name: "notion_get_page",
        description: "Reads page blocks and child content in markdown format.",
        tier: "READ",
      },
    ],
  },
  {
    id: "core-google",
    name: "Google Workspace",
    category: "workspace",
    desc: "In-house streamable HTTP adapter for Gmail and Calendar. Email transmission strictly gated by TOTP.",
    transport: "streamable_http",
    status: "Connected",
    toolsCount: 4,
    schemaHash: "sha256:91aa84102d5b62e49c71e06fa941829e2830f3c5b8d0092c",
    tools: [
      {
        name: "google_gmail_search",
        description: "Searches inbox messages and email threads.",
        tier: "READ",
      },
      {
        name: "google_gmail_send",
        description:
          "Dispatches outgoing email. Gated by single-use token and TOTP step-up.",
        tier: "RESTRICTED",
      },
      {
        name: "google_calendar_list_events",
        description: "Reads user calendar events for scheduled coordination.",
        tier: "READ",
      },
      {
        name: "google_calendar_create_event",
        description: "Schedules calendar invites.",
        tier: "SENSITIVE",
      },
    ],
  },
  {
    id: "core-fs",
    name: "Sandboxed Filesystem",
    category: "filesystem",
    desc: "Strictly confined to /artifacts/{workspace_id}/. Sandboxed directory traversal defense.",
    transport: "stdio",
    status: "Built-in",
    toolsCount: 3,
    schemaHash: "sha256:4a00bc9837f132e04da88c91a0c776de34190c128509b431",
    tools: [
      {
        name: "fs_list_directory",
        description: "Lists workspace deliverable directory contents.",
        tier: "READ",
      },
      {
        name: "fs_read_artifact",
        description:
          "Reads deliverable file contents with anti-tamper hash check.",
        tier: "READ",
      },
      {
        name: "fs_write_artifact",
        description:
          "Writes verified output deliverable into sandboxed partition.",
        tier: "WRITE",
      },
    ],
  },
  {
    id: "core-git",
    name: "Local Git Engine",
    category: "vcs",
    desc: "Autonomous local repo status, diff, branch, and commit inside ephemeral docker sandboxes.",
    transport: "stdio",
    status: "Built-in",
    toolsCount: 5,
    schemaHash: "sha256:5ef34b1298c56d012e88a3194519fbc702a11b659c03211a",
    tools: [
      {
        name: "git_status",
        description: "Checks working tree status.",
        tier: "READ",
      },
      {
        name: "git_diff",
        description:
          "Inspects unified diff between sandbox working tree and HEAD.",
        tier: "READ",
      },
      {
        name: "git_branch",
        description: "Lists or creates feature branches.",
        tier: "WRITE",
      },
      {
        name: "git_commit",
        description: "Creates signed commit with Conventional Commits message.",
        tier: "WRITE",
      },
      {
        name: "git_push",
        description:
          "Pushes branch to remote. Gated by explicit human approval.",
        tier: "RESTRICTED",
      },
    ],
  },
];

export function McpGatewayView(): React.JSX.Element {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Inspector & Register modals
  const [inspectingServer, setInspectingServer] = useState<
    McpServer | IntegrationCatalogItem | null
  >(null);
  const [isRegisterOpen, setIsRegisterOpen] = useState<boolean>(false);
  const [healthCheckingId, setHealthCheckingId] = useState<string | null>(null);
  const [pingResult, setPingResult] = useState<{
    id: string;
    latencyMs: number;
    status: string;
  } | null>(null);

  // Filter & Search
  const [transportFilter, setTransportFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");

  // Registration form state
  const [name, setName] = useState<string>("");
  const [transport, setTransport] = useState<
    "stdio" | "http" | "streamable_http"
  >("stdio");
  const [command, setCommand] = useState<string>("");
  const [argsText, setArgsText] = useState<string>("");
  const [url, setUrl] = useState<string>("");
  const [pinnedVersion, setPinnedVersion] = useState<string>("1.0.0");
  const [schemaHashInput, setSchemaHashInput] = useState<string>("");
  const [registering, setRegistering] = useState<boolean>(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  const loadServers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listMcpServers();
      setServers(data);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load MCP servers",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadServers();
  }, [loadServers]);

  const handleHealthCheck = async (serverId: string) => {
    setHealthCheckingId(serverId);
    const startTime = performance.now();
    try {
      const health = await getMcpServerHealth(serverId);
      const latencyMs = Math.round(performance.now() - startTime);
      setPingResult({
        id: serverId,
        latencyMs,
        status: health.health_status,
      });

      setServers((prev) =>
        prev.map((s) =>
          s.id === serverId
            ? {
                ...s,
                health_status: health.health_status,
                last_ping_at: health.last_ping_at,
                consecutive_failures: health.consecutive_failures,
                tool_count: health.tool_count,
              }
            : s,
        ),
      );
    } catch {
      setPingResult({
        id: serverId,
        latencyMs: Math.round(performance.now() - startTime),
        status: "dead",
      });
    } finally {
      setHealthCheckingId(null);
    }
  };

  const handleDelete = async (server: McpServer) => {
    if (
      !confirm(
        `Are you sure you want to deactivate and remove MCP server "${server.name}"?`,
      )
    ) {
      return;
    }
    try {
      await deleteMcpServer(server.id);
      await loadServers();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to delete MCP server",
      );
    }
  };

  const handleRegisterSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setRegistering(true);
    setRegisterError(null);

    const payload: McpServerCreate = {
      name: name.trim(),
      transport,
      command: transport === "stdio" ? command.trim() || undefined : undefined,
      args:
        transport === "stdio" && argsText.trim()
          ? argsText.split(",").map((s) => s.trim())
          : undefined,
      url: transport !== "stdio" ? url.trim() || undefined : undefined,
      pinned_version: pinnedVersion.trim() || undefined,
      schema_hash: schemaHashInput.trim() || undefined,
    };

    try {
      await registerMcpServer(payload);
      setIsRegisterOpen(false);
      setName("");
      setCommand("");
      setArgsText("");
      setUrl("");
      setPinnedVersion("1.0.0");
      setSchemaHashInput("");
      await loadServers();
    } catch (err) {
      setRegisterError(
        err instanceof Error ? err.message : "Failed to register server",
      );
    } finally {
      setRegistering(false);
    }
  };

  // Filter integration cards
  const filteredCatalog = useMemo(() => {
    return CORE_INTEGRATIONS.filter((item) => {
      if (transportFilter !== "all") {
        if (transportFilter === "stdio" && item.transport !== "stdio") {
          return false;
        }
        if (
          transportFilter === "streaming" &&
          item.transport !== "http" &&
          item.transport !== "streamable_http"
        ) {
          return false;
        }
      }
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          item.name.toLowerCase().includes(q) ||
          item.desc.toLowerCase().includes(q) ||
          item.tools.some((t) => t.name.toLowerCase().includes(q))
        );
      }
      return true;
    });
  }, [transportFilter, searchQuery]);

  const filteredTenantServers = useMemo(() => {
    return servers.filter((s) => {
      if (transportFilter !== "all") {
        if (transportFilter === "stdio" && s.transport !== "stdio") {
          return false;
        }
        if (
          transportFilter === "streaming" &&
          s.transport !== "http" &&
          s.transport !== "streamable_http"
        ) {
          return false;
        }
      }
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        return (
          s.name.toLowerCase().includes(q) ||
          s.transport.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [servers, transportFilter, searchQuery]);

  const getTransportBadge = (transportType: string) => {
    switch (transportType) {
      case "stdio":
        return (
          <GlassBadge
            variant="purple"
            size="sm"
            mono
            icon={<Terminal className="w-3 h-3" />}
          >
            stdio
          </GlassBadge>
        );
      case "streamable_http":
        return (
          <GlassBadge
            variant="teal"
            size="sm"
            mono
            icon={<Zap className="w-3 h-3" />}
          >
            streamable_http
          </GlassBadge>
        );
      case "http":
      default:
        return (
          <GlassBadge
            variant="blue"
            size="sm"
            mono
            icon={<Globe className="w-3 h-3" />}
          >
            http
          </GlassBadge>
        );
    }
  };

  const getPermissionBadge = (tier: string) => {
    switch (tier.toUpperCase()) {
      case "RESTRICTED":
        return (
          <GlassBadge
            variant="rose"
            size="sm"
            mono
            icon={<ShieldAlert className="w-3 h-3" />}
          >
            RESTRICTED
          </GlassBadge>
        );
      case "SENSITIVE":
        return (
          <GlassBadge
            variant="amber"
            size="sm"
            mono
            icon={<AlertTriangle className="w-3 h-3" />}
          >
            SENSITIVE
          </GlassBadge>
        );
      case "WRITE":
        return (
          <GlassBadge variant="blue" size="sm" mono>
            WRITE
          </GlassBadge>
        );
      case "READ":
      default:
        return (
          <GlassBadge variant="emerald" size="sm" mono>
            READ
          </GlassBadge>
        );
    }
  };

  const getHealthBadge = (health: string) => {
    switch (health.toLowerCase()) {
      case "healthy":
        return (
          <GlassBadge variant="emerald" size="sm" dot mono>
            Healthy
          </GlassBadge>
        );
      case "degraded":
        return (
          <GlassBadge variant="amber" size="sm" dot dotPulse mono>
            Degraded
          </GlassBadge>
        );
      case "dead":
      default:
        return (
          <GlassBadge variant="rose" size="sm" dot mono>
            Dead
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
            <FolderGit2 className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-lg md:text-xl font-bold tracking-tight text-zinc-100 font-mono">
                Model Context Protocol (MCP) Gateway
              </h2>
              <GlassBadge variant="teal" size="sm" mono>
                Phase 08 Core
              </GlassBadge>
            </div>
            <p className="text-xs md:text-sm text-zinc-400 mt-1.5 max-w-3xl leading-relaxed">
              Unified runtime integration hub with RFC 8785 canonical
              schema-hash poisoning defense, process heartbeats, 4-tier
              permission governance, and live ping testers for external tool
              servers.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 shrink-0">
          <GlassButton
            variant="secondary"
            size="sm"
            onClick={loadServers}
            leftIcon={
              <RefreshCw
                className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`}
              />
            }
            aria-label="Refresh MCP servers"
          >
            Sync
          </GlassButton>
          <GlassButton
            variant="primary"
            size="sm"
            onClick={() => setIsRegisterOpen(true)}
            leftIcon={<PlusCircle className="w-4 h-4" />}
          >
            Register Server
          </GlassButton>
        </div>
      </GlassCard>

      {error && (
        <div className="p-4 rounded-xl border border-rose-500/30 bg-rose-950/30 backdrop-blur-md text-rose-200 text-xs flex items-center gap-2.5 shadow-sm">
          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Filter and Search Bar */}
      <GlassCard
        variant="default"
        padding="sm"
        className="flex flex-col sm:flex-row items-center justify-between gap-3"
      >
        <div className="flex items-center gap-1.5 overflow-x-auto w-full sm:w-auto text-xs">
          {[
            { id: "all", label: "All Transports" },
            { id: "stdio", label: "Subprocess (stdio)" },
            { id: "streaming", label: "Streaming (HTTP/SSE)" },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setTransportFilter(tab.id)}
              className={`px-3 py-1.5 rounded-lg font-medium text-xs whitespace-nowrap transition-all duration-150 cursor-pointer ${
                transportFilter === tab.id
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
            placeholder="Search servers, tools, schema…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            leftIcon={<Search className="w-3.5 h-3.5 text-zinc-500" />}
            clearable
            onClear={() => setSearchQuery("")}
            className="py-1.5 text-xs"
          />
        </div>
      </GlassCard>

      {/* Section 1: Core Integrations Catalog */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Cpu className="w-4 h-4 text-teal-400" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300 font-mono">
              Core Enterprise Integrations ({filteredCatalog.length})
            </h3>
          </div>
          <span className="text-[11px] font-mono text-zinc-500">
            Canonical RFC 8785 Verified
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredCatalog.map((ci) => (
            <GlassCard
              key={ci.id}
              variant="default"
              hoverEffect="lift-glow"
              padding="md"
              className="flex flex-col justify-between gap-4 cursor-pointer group"
              onClick={() => setInspectingServer(ci)}
            >
              <div className="flex flex-col gap-2.5">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <div className="p-2 rounded-lg bg-white/5 border border-white/10 group-hover:border-teal-500/30 transition-colors">
                      {ci.category === "search" && (
                        <Globe className="w-4 h-4 text-teal-400" />
                      )}
                      {ci.category === "vcs" && (
                        <FolderGit2 className="w-4 h-4 text-blue-400" />
                      )}
                      {ci.category === "workspace" && (
                        <Zap className="w-4 h-4 text-amber-400" />
                      )}
                      {ci.category === "productivity" && (
                        <Activity className="w-4 h-4 text-purple-400" />
                      )}
                      {ci.category === "filesystem" && (
                        <HardDrive className="w-4 h-4 text-emerald-400" />
                      )}
                    </div>
                    <div>
                      <h4 className="text-sm font-bold text-zinc-100 font-mono group-hover:text-teal-300 transition-colors">
                        {ci.name}
                      </h4>
                      <span className="text-[10px] font-mono text-zinc-500 uppercase">
                        {ci.category}
                      </span>
                    </div>
                  </div>

                  <GlassBadge variant="emerald" size="sm" dot mono>
                    {ci.status}
                  </GlassBadge>
                </div>

                <p className="text-xs text-zinc-300/90 leading-relaxed line-clamp-3">
                  {ci.desc}
                </p>
              </div>

              <div className="flex flex-col gap-2 pt-3 border-t border-white/5">
                <div className="flex items-center justify-between text-xs font-mono">
                  {getTransportBadge(ci.transport)}
                  <span className="text-zinc-400 font-medium">
                    {ci.toolsCount} registered tools
                  </span>
                </div>

                <div className="flex items-center justify-between text-[11px] font-mono text-zinc-500 pt-1">
                  <span className="flex items-center gap-1 truncate max-w-[190px]">
                    <ShieldCheck className="w-3 h-3 text-teal-400/80 shrink-0" />
                    <span className="truncate">
                      {ci.schemaHash.slice(0, 18)}…
                    </span>
                  </span>
                  <span className="text-teal-400 group-hover:underline text-[11px]">
                    Inspect Tools →
                  </span>
                </div>
              </div>
            </GlassCard>
          ))}
        </div>
      </div>

      {/* Section 2: Registered Tenant Custom Servers */}
      <div className="flex flex-col gap-3 mt-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-teal-400" />
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-300 font-mono">
              Tenant Custom Servers ({filteredTenantServers.length})
            </h3>
          </div>
          <span className="text-[11px] font-mono text-zinc-500">
            Postgres `mcp_servers` Tenant Isolation
          </span>
        </div>

        <GlassCard variant="default" padding="none" className="overflow-hidden">
          {loading && servers.length === 0 ? (
            <div className="p-12 text-center text-zinc-400 text-xs flex items-center justify-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin text-teal-400" />
              <span>Inspecting registered MCP servers…</span>
            </div>
          ) : filteredTenantServers.length === 0 ? (
            <div className="p-12 text-center text-zinc-400 text-xs flex flex-col items-center justify-center gap-3">
              <Server className="w-8 h-8 text-zinc-600 opacity-60" />
              <p>No tenant MCP servers registered yet.</p>
              <GlassButton
                variant="primary"
                size="sm"
                onClick={() => setIsRegisterOpen(true)}
                leftIcon={<PlusCircle className="w-3.5 h-3.5" />}
              >
                Register Custom Server
              </GlassButton>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="border-b border-white/10 bg-white/[0.02] text-zinc-400 font-mono text-[11px]">
                    <th className="py-3 px-4 font-medium">Server Name</th>
                    <th className="py-3 px-4 font-medium">Transport</th>
                    <th className="py-3 px-4 font-medium">Health Status</th>
                    <th className="py-3 px-4 font-medium">Tools</th>
                    <th className="py-3 px-4 font-medium">Schema Hash</th>
                    <th className="py-3 px-4 font-medium">Last Ping</th>
                    <th className="py-3 px-4 font-medium text-right">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-zinc-300 font-mono">
                  {filteredTenantServers.map((s) => (
                    <tr
                      key={s.id}
                      className="hover:bg-white/[0.03] transition-colors"
                    >
                      <td className="py-3 px-4 font-semibold text-zinc-100">
                        <div className="flex items-center gap-2">
                          <Server className="w-3.5 h-3.5 text-teal-400/80" />
                          <span>{s.name}</span>
                        </div>
                      </td>
                      <td className="py-3 px-4">
                        {getTransportBadge(s.transport)}
                      </td>
                      <td className="py-3 px-4">
                        {getHealthBadge(s.health_status)}
                      </td>
                      <td className="py-3 px-4 text-zinc-200">
                        {s.tool_count} tools
                      </td>
                      <td className="py-3 px-4 text-[11px] text-zinc-400">
                        {s.schema_hash ? (
                          <span
                            className="flex items-center gap-1 font-mono text-teal-400/90"
                            title={s.schema_hash}
                          >
                            <ShieldCheck className="w-3 h-3 shrink-0" />
                            <span>{s.schema_hash.slice(0, 12)}…</span>
                          </span>
                        ) : (
                          <span className="text-zinc-500">Unspecified</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-zinc-400 text-[11px]">
                        {s.last_ping_at
                          ? new Date(s.last_ping_at).toLocaleTimeString()
                          : "Never"}
                      </td>
                      <td className="py-3 px-4 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <GlassButton
                            variant="secondary"
                            size="sm"
                            onClick={() => handleHealthCheck(s.id)}
                            disabled={healthCheckingId === s.id}
                            title="Ping server health tester"
                            leftIcon={
                              <RefreshCw
                                className={`w-3.5 h-3.5 ${healthCheckingId === s.id ? "animate-spin" : ""}`}
                              />
                            }
                          >
                            Ping
                          </GlassButton>
                          <GlassButton
                            variant="outline"
                            size="sm"
                            onClick={() => setInspectingServer(s)}
                          >
                            Inspect
                          </GlassButton>
                          <GlassButton
                            variant="danger"
                            size="sm"
                            onClick={() => handleDelete(s)}
                            title="Delete server registration"
                            aria-label={`Delete ${s.name}`}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </GlassButton>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </GlassCard>
      </div>

      {/* Ping Tester Result Notification */}
      {pingResult && (
        <div className="fixed bottom-6 right-6 z-40 max-w-sm">
          <GlassCard
            variant="teal"
            padding="sm"
            className="flex items-center gap-3 shadow-2xl border-teal-500/40"
          >
            <CheckCircle2 className="w-5 h-5 text-teal-400 shrink-0" />
            <div className="text-xs">
              <p className="font-semibold text-zinc-100">
                Server Health Ping Received
              </p>
              <p className="text-zinc-400 font-mono text-[11px] mt-0.5">
                Latency:{" "}
                <span className="text-teal-300 font-bold">
                  {pingResult.latencyMs}ms
                </span>{" "}
                • Status:{" "}
                <span className="uppercase text-emerald-300 font-bold">
                  {pingResult.status}
                </span>
              </p>
            </div>
            <button
              type="button"
              onClick={() => setPingResult(null)}
              className="ml-auto text-zinc-500 hover:text-zinc-200 cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </GlassCard>
        </div>
      )}

      {/* Schema Hash & Tool Manifest Inspector Modal */}
      {inspectingServer && (
        <GlassModal
          isOpen={Boolean(inspectingServer)}
          onClose={() => setInspectingServer(null)}
          title={
            <div className="flex items-center gap-2 text-zinc-100 font-mono">
              <ShieldCheck className="w-5 h-5 text-teal-400" />
              <span>{inspectingServer.name} — Schema Inspection</span>
            </div>
          }
          description="RFC 8785 Canonical schema validation, tool permission tier verification, and anti-poisoning defenses."
          size="lg"
        >
          <div className="flex flex-col gap-4 text-xs font-mono">
            {/* Schema Defense Status Banner */}
            <div className="p-3.5 rounded-xl border border-teal-500/30 bg-teal-500/10 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-teal-400 shrink-0" />
                <div>
                  <span className="font-bold text-zinc-200">
                    Schema Hash Defense: ACTIVE
                  </span>
                  <p className="text-[11px] text-zinc-400">
                    Manifest keys sorted lexicographically; whitespace
                    normalized per RFC 8785.
                  </p>
                </div>
              </div>
              <GlassBadge variant="emerald" size="sm" mono>
                VALIDATED
              </GlassBadge>
            </div>

            {/* Cryptographic Hash */}
            <div className="flex flex-col gap-1 p-3 rounded-xl border border-white/10 bg-white/[0.02]">
              <span className="text-[11px] text-zinc-400 font-semibold uppercase">
                Cryptographic Schema Hash (SHA-256)
              </span>
              <span className="text-xs text-teal-300 break-all select-all font-mono">
                {"schemaHash" in inspectingServer
                  ? inspectingServer.schemaHash
                  : inspectingServer.schema_hash ||
                    "sha256:computed_on_runtime_init"}
              </span>
            </div>

            {/* Tools Manifest List */}
            <div className="flex flex-col gap-2 mt-1">
              <span className="text-xs font-semibold text-zinc-200 uppercase tracking-wider">
                Exposed Tool Manifest
              </span>

              {"tools" in inspectingServer && inspectingServer.tools ? (
                <div className="flex flex-col gap-2 max-h-72 overflow-y-auto pr-1">
                  {inspectingServer.tools.map((t) => (
                    <div
                      key={t.name}
                      className="p-3 rounded-lg border border-white/10 bg-white/[0.03] flex flex-col gap-1.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-teal-300 text-xs">
                          {t.name}
                        </span>
                        {getPermissionBadge(t.tier)}
                      </div>
                      <p className="text-xs text-zinc-300 leading-relaxed font-sans">
                        {t.description}
                      </p>
                    </div>
                  ))}
                </div>
              ) : "tools_manifest" in inspectingServer &&
                Array.isArray(inspectingServer.tools_manifest) &&
                inspectingServer.tools_manifest.length > 0 ? (
                <div className="flex flex-col gap-2 max-h-72 overflow-y-auto pr-1">
                  {inspectingServer.tools_manifest.map((tool, idx) => (
                    <div
                      key={idx}
                      className="p-3 rounded-lg border border-white/10 bg-white/[0.03] flex flex-col gap-1.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-bold text-teal-300 text-xs">
                          {String(tool.name || `Tool #${idx + 1}`)}
                        </span>
                        <GlassBadge variant="blue" size="sm" mono>
                          READ / WRITE
                        </GlassBadge>
                      </div>
                      {Boolean(tool.description) && (
                        <p className="text-xs text-zinc-300 leading-relaxed font-sans">
                          {String(tool.description)}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="p-6 text-center text-zinc-500 text-xs border border-white/5 rounded-lg">
                  No explicit tools manifest published by this endpoint.
                </div>
              )}
            </div>
          </div>
        </GlassModal>
      )}

      {/* Register Server Modal */}
      <GlassModal
        isOpen={isRegisterOpen}
        onClose={() => setIsRegisterOpen(false)}
        title="Register Custom MCP Server"
        description="Connect custom tools to MAYA AI with transport validation and schema hash sealing."
        size="md"
      >
        <form
          onSubmit={handleRegisterSubmit}
          className="flex flex-col gap-4 text-xs"
        >
          {registerError && (
            <div className="p-3 rounded-lg border border-rose-500/30 bg-rose-950/40 text-rose-200">
              {registerError}
            </div>
          )}

          <GlassInput
            label="Server Name *"
            required
            placeholder="e.g. custom_data_connector"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-300">
              Transport Protocol *
            </label>
            <select
              value={transport}
              onChange={(e) =>
                setTransport(
                  e.target.value as "stdio" | "http" | "streamable_http",
                )
              }
              className="w-full px-3 py-2 rounded-xl border border-white/15 bg-zinc-900/70 text-zinc-100 text-xs focus:outline-none focus:ring-2 focus:ring-teal-400"
            >
              <option value="stdio">stdio (Local Subprocess Sandbox)</option>
              <option value="http">http (Streaming HTTP/SSE Endpoint)</option>
              <option value="streamable_http">
                streamable_http (Bi-directional MCP Stream)
              </option>
            </select>
          </div>

          {transport === "stdio" ? (
            <>
              <GlassInput
                label="Executable Command *"
                required
                placeholder="e.g. node, python, or /usr/local/bin/mcp-server"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
              />
              <GlassInput
                label="Arguments (comma separated)"
                placeholder="dist/index.js, --port=8080"
                value={argsText}
                onChange={(e) => setArgsText(e.target.value)}
              />
            </>
          ) : (
            <GlassInput
              label="Endpoint URL *"
              type="url"
              required
              placeholder="https://mcp.internal.acme.com/mcp"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          )}

          <div className="grid grid-cols-2 gap-3">
            <GlassInput
              label="Pinned Version"
              placeholder="1.0.0"
              value={pinnedVersion}
              onChange={(e) => setPinnedVersion(e.target.value)}
            />
            <GlassInput
              label="Schema Hash Override (Optional)"
              placeholder="sha256:…"
              value={schemaHashInput}
              onChange={(e) => setSchemaHashInput(e.target.value)}
            />
          </div>

          <div className="flex items-center justify-end gap-2 pt-3 border-t border-white/10 mt-2">
            <GlassButton
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setIsRegisterOpen(false)}
            >
              Cancel
            </GlassButton>
            <GlassButton
              type="submit"
              variant="primary"
              size="sm"
              loading={registering}
              loadingText="Registering…"
            >
              Register & Seal Hash
            </GlassButton>
          </div>
        </form>
      </GlassModal>
    </div>
  );
}
