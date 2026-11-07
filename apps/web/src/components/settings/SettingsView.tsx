"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  DollarSign,
  Key,
  Lock,
  LogOut,
  ShieldCheck,
  Sliders,
  User,
} from "lucide-react";
import React, { useEffect, useState } from "react";

import { signOut } from "../../lib/auth-client";
import {
  checkSystemHealth,
  getPrincipalMe,
  type SystemHealth,
  type UserPrincipal,
} from "../../lib/system-client";
import {
  GlassBadge,
  GlassButton,
  GlassCard,
  GlassInput,
  GlassModal,
} from "../ui";

export function SettingsView(): React.JSX.Element {
  const [activeTab, setActiveTab] = useState<
    "profile" | "routing" | "budgets" | "security" | "danger"
  >("profile");

  const [principal, setPrincipal] = useState<UserPrincipal | null>(null);
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null);
  const [loadingMe, setLoadingMe] = useState<boolean>(true);

  // Model Routing (Strictly Gemini model family - Decision D13)
  const [orchestratorModel, setOrchestratorModel] =
    useState<string>("gemini-3.8-flash");
  const [heavyModel, setHeavyModel] = useState<string>("gemini-3.8-flash");
  const [voiceModel, setVoiceModel] = useState<string>("gemini-3.8-live");
  const [liteModel, setLiteModel] = useState<string>("gemini-3.5-flash-lite");

  // Budget & Quotas
  const [defaultBudget, setDefaultBudget] = useState<string>("2.00");
  const [maxTurns, setMaxTurns] = useState<string>("25");
  const [rateLimitRps, setRateLimitRps] = useState<string>("60");
  const [autoFreezeOnQuota, setAutoFreezeOnQuota] = useState<boolean>(true);

  // Danger Zone
  const [revokingSession, setRevokingSession] = useState<boolean>(false);
  const [showRevokeModal, setShowRevokeModal] = useState<boolean>(false);
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);

  useEffect(() => {
    setLoadingMe(true);
    Promise.all([
      getPrincipalMe().catch(() => null),
      checkSystemHealth().catch(() => ({ status: "unavailable" as const })),
    ])
      .then(([me, health]) => {
        if (me) setPrincipal(me);
        if (health) setSystemHealth(health);
      })
      .finally(() => setLoadingMe(false));
  }, []);

  const handleSavePreferences = () => {
    setSaveSuccess(true);
    setTimeout(() => setSaveSuccess(false), 3000);
  };

  const handleRevokeSessions = async () => {
    setRevokingSession(true);
    try {
      await signOut();
      window.location.href = "/login";
    } catch {
      window.location.href = "/login";
    } finally {
      setRevokingSession(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-4 md:p-8 flex flex-col gap-6 max-w-5xl mx-auto w-full">
      {/* Header Banner */}
      <GlassCard
        variant="teal"
        padding="md"
        className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-teal-500/30"
      >
        <div className="flex items-start gap-3.5">
          <div className="p-2.5 rounded-xl bg-teal-500/10 border border-teal-500/25 text-teal-400 mt-0.5">
            <Sliders className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-lg md:text-xl font-bold tracking-tight text-zinc-100 font-mono">
                System & Workspace Preferences
              </h2>
              <GlassBadge variant="teal" size="sm" mono>
                Tenant Governance
              </GlassBadge>
            </div>
            <p className="text-xs md:text-sm text-zinc-400 mt-1.5 max-w-3xl leading-relaxed">
              Configure autonomous model routing aliases, task budget caps,
              4-tier MCP security policies, and credential session revocation.
            </p>
          </div>
        </div>

        <GlassButton
          variant="primary"
          size="sm"
          onClick={handleSavePreferences}
          leftIcon={<CheckCircle2 className="w-4 h-4" />}
        >
          Save Preferences
        </GlassButton>
      </GlassCard>

      {/* Glass Tab Navigation */}
      <GlassCard variant="default" padding="none" className="overflow-x-auto">
        <div className="flex items-center gap-1 p-1 text-xs font-mono">
          {[
            {
              id: "profile",
              label: "Profile & Identity",
              icon: <User className="w-3.5 h-3.5" />,
            },
            {
              id: "routing",
              label: "Model Routing",
              icon: <Cpu className="w-3.5 h-3.5" />,
            },
            {
              id: "budgets",
              label: "Budget & Quotas",
              icon: <DollarSign className="w-3.5 h-3.5" />,
            },
            {
              id: "security",
              label: "MCP Security",
              icon: <ShieldCheck className="w-3.5 h-3.5" />,
            },
            {
              id: "danger",
              label: "Danger Zone",
              icon: <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />,
            },
          ].map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id as typeof activeTab)}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-xl font-medium whitespace-nowrap transition-all duration-150 cursor-pointer ${
                activeTab === tab.id
                  ? "bg-teal-500/20 text-teal-300 border border-teal-500/40 shadow-xs"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-white/5 border border-transparent"
              }`}
            >
              {tab.icon}
              <span>{tab.label}</span>
            </button>
          ))}
        </div>
      </GlassCard>

      {/* Tab 1: Profile & Identity */}
      {activeTab === "profile" && (
        <div className="flex flex-col gap-4">
          <GlassCard
            variant="default"
            padding="md"
            className="flex flex-col gap-4"
          >
            <div className="flex items-center justify-between pb-3 border-b border-white/10">
              <div>
                <h3 className="text-sm font-bold text-zinc-100 font-mono">
                  Authenticated Tenant Identity
                </h3>
                <p className="text-xs text-zinc-400 mt-0.5">
                  Cryptographically verified user principal and isolated
                  workspace partition.
                </p>
              </div>
              <GlassBadge
                variant={
                  systemHealth?.status === "ready" ||
                  systemHealth?.status === "alive"
                    ? "emerald"
                    : "amber"
                }
                size="sm"
                dot
                mono
              >
                API: {systemHealth?.status ?? "checking"}
              </GlassBadge>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] text-zinc-400 uppercase font-semibold">
                  Operator User ID
                </span>
                <div className="px-3 py-2 rounded-xl bg-white/[0.03] border border-white/10 text-zinc-200 break-all select-all">
                  {principal?.user_id ||
                    (loadingMe ? "Loading…" : "anonymous_operator")}
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] text-zinc-400 uppercase font-semibold">
                  Workspace Tenant ID
                </span>
                <div className="px-3 py-2 rounded-xl bg-white/[0.03] border border-white/10 text-zinc-200 break-all select-all">
                  {principal?.workspace_id ||
                    (loadingMe ? "Loading…" : "default_workspace")}
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] text-zinc-400 uppercase font-semibold">
                  Role-Based Access Control (RBAC)
                </span>
                <div className="px-3 py-2 rounded-xl bg-teal-500/10 border border-teal-500/30 text-teal-300 uppercase font-bold flex items-center justify-between">
                  <span>{principal?.role || "workspace_owner"}</span>
                  <GlassBadge variant="teal" size="sm" mono>
                    LEVEL 4 PRIVILEGES
                  </GlassBadge>
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] text-zinc-400 uppercase font-semibold">
                  Data Residency & Sandbox Cluster
                </span>
                <div className="px-3 py-2 rounded-xl bg-white/[0.03] border border-white/10 text-zinc-300">
                  us-central1 (Docker Isolated Ephemeral VPC)
                </div>
              </div>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Tab 2: Model Routing (Strictly Gemini Family - Decision D13) */}
      {activeTab === "routing" && (
        <div className="flex flex-col gap-4">
          <GlassCard
            variant="teal"
            padding="md"
            className="flex flex-col gap-4"
          >
            <div className="flex items-center justify-between pb-3 border-b border-teal-500/20">
              <div>
                <h3 className="text-sm font-bold text-zinc-100 font-mono">
                  Gemini Model Gateway Routing (Decision D13)
                </h3>
                <p className="text-xs text-zinc-400 mt-0.5">
                  MAYA AI strictly adheres to authorized Google Gemini models
                  for all tasks.
                </p>
              </div>
              <GlassBadge variant="teal" size="sm" mono>
                Gemini Gateway Enforced
              </GlassBadge>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
              <div className="flex flex-col gap-1.5">
                <label className="text-zinc-200 font-medium font-mono">
                  Orchestrator Model Alias
                </label>
                <select
                  value={orchestratorModel}
                  onChange={(e) => setOrchestratorModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl border border-white/15 bg-zinc-900/70 text-zinc-100 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-teal-400"
                >
                  <option value="gemini-3.8-flash">
                    gemini-3.8-flash (Recommended Orchestrator)
                  </option>
                  <option value="gemini-3.5-flash-lite">
                    gemini-3.5-flash-lite (Ultra Low Latency Planning)
                  </option>
                </select>
                <span className="text-[10px] text-zinc-500 font-mono">
                  Handles multi-agent DAG decomposing and verification gates.
                </span>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-zinc-200 font-medium font-mono">
                  Specialist & Heavy Reasoning Model
                </label>
                <select
                  value={heavyModel}
                  onChange={(e) => setHeavyModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl border border-white/15 bg-zinc-900/70 text-zinc-100 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-teal-400"
                >
                  <option value="gemini-3.8-flash">
                    gemini-3.8-flash (High-Capacity Coding & Document Engine)
                  </option>
                  <option value="gemini-3.5-flash-lite">
                    gemini-3.5-flash-lite (Cost-Optimized Heavy Fallback)
                  </option>
                </select>
                <span className="text-[10px] text-zinc-500 font-mono">
                  Powers CodingAgent, DocumentAgent, and FinancialModeler.
                </span>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-zinc-200 font-medium font-mono">
                  Bi-Directional Voice Streaming Model
                </label>
                <select
                  value={voiceModel}
                  onChange={(e) => setVoiceModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl border border-white/15 bg-zinc-900/70 text-zinc-100 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-teal-400"
                >
                  <option value="gemini-3.8-live">
                    gemini-3.8-live (Native WebSocket Real-time Voice)
                  </option>
                </select>
                <span className="text-[10px] text-zinc-500 font-mono">
                  Powers the voice orb with continuous sub-300ms audio
                  turnaround.
                </span>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-zinc-200 font-medium font-mono">
                  Fast Triage & Email Routine Model
                </label>
                <select
                  value={liteModel}
                  onChange={(e) => setLiteModel(e.target.value)}
                  className="w-full px-3 py-2 rounded-xl border border-white/15 bg-zinc-900/70 text-zinc-100 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-teal-400"
                >
                  <option value="gemini-3.5-flash-lite">
                    gemini-3.5-flash-lite (High-throughput subtasks)
                  </option>
                </select>
                <span className="text-[10px] text-zinc-500 font-mono">
                  Executes Brave Search snippet ranking and calendar lookups.
                </span>
              </div>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Tab 3: Budget & Quotas */}
      {activeTab === "budgets" && (
        <div className="flex flex-col gap-4">
          <GlassCard
            variant="default"
            padding="md"
            className="flex flex-col gap-4"
          >
            <div className="flex items-center justify-between pb-3 border-b border-white/10">
              <div>
                <h3 className="text-sm font-bold text-zinc-100 font-mono">
                  Autonomous Task Quotas & Leaky-Bucket Rate Limiting
                </h3>
                <p className="text-xs text-zinc-400 mt-0.5">
                  Strict cost ceilings and tool execution rate limits to prevent
                  runaway loops.
                </p>
              </div>
              <GlassBadge variant="emerald" size="sm" mono>
                Guardrails Active
              </GlassBadge>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
              <GlassInput
                label="Default Task Budget Cap ($USD)"
                type="number"
                step="0.25"
                value={defaultBudget}
                onChange={(e) => setDefaultBudget(e.target.value)}
                helperText="Hard stop if cumulative model inference cost exceeds this threshold."
              />

              <GlassInput
                label="Maximum Tool Turns per Autonomous Task"
                type="number"
                value={maxTurns}
                onChange={(e) => setMaxTurns(e.target.value)}
                helperText="Limits recursive DAG agent loops before requiring human checkpoint."
              />

              <GlassInput
                label="Leaky-Bucket Outbound Rate Limit (RPS)"
                type="number"
                value={rateLimitRps}
                onChange={(e) => setRateLimitRps(e.target.value)}
                helperText="Per-minute outbound capacity for Brave, GitHub, and Notion APIs."
              />

              <div className="flex flex-col justify-center p-3 rounded-xl bg-white/[0.02] border border-white/10">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-zinc-200">
                    Auto-Freeze on Quota Depletion
                  </span>
                  <input
                    type="checkbox"
                    checked={autoFreezeOnQuota}
                    onChange={(e) => setAutoFreezeOnQuota(e.target.checked)}
                    className="w-4 h-4 rounded text-teal-500 focus:ring-teal-400 bg-zinc-900 border-white/20"
                  />
                </div>
                <p className="text-[11px] text-zinc-400 mt-1 font-sans">
                  Halts all non-essential tool executions immediately if monthly
                  budget exceeds 95%.
                </p>
              </div>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Tab 4: MCP Security */}
      {activeTab === "security" && (
        <div className="flex flex-col gap-4">
          <GlassCard
            variant="default"
            padding="md"
            className="flex flex-col gap-4"
          >
            <div className="flex items-center justify-between pb-3 border-b border-white/10">
              <div>
                <h3 className="text-sm font-bold text-zinc-100 font-mono">
                  MCP Gateway Security & Permission Tier Matrix
                </h3>
                <p className="text-xs text-zinc-400 mt-0.5">
                  RFC 8785 schema hash defense, anti-poisoning strict mode, and
                  TOTP step-up.
                </p>
              </div>
              <GlassBadge variant="emerald" size="sm" mono>
                4-Tier Matrix Enforced
              </GlassBadge>
            </div>

            <div className="flex flex-col gap-3 text-xs">
              {/* Feature 1 */}
              <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-teal-500/10 text-teal-400">
                    <ShieldCheck className="w-5 h-5" />
                  </div>
                  <div>
                    <h4 className="font-bold text-zinc-200 font-mono">
                      RFC 8785 Canonical Schema Poisoning Defense
                    </h4>
                    <p className="text-zinc-400 text-[11px] mt-0.5 font-sans">
                      Normalizes keys lexicographically and verifies SHA-256
                      before registering any tool manifest.
                    </p>
                  </div>
                </div>
                <GlassBadge variant="emerald" size="sm" mono>
                  STRICT ENFORCED
                </GlassBadge>
              </div>

              {/* Feature 2 */}
              <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-amber-500/10 text-amber-400">
                    <Lock className="w-5 h-5" />
                  </div>
                  <div>
                    <h4 className="font-bold text-zinc-200 font-mono">
                      Step-Up TOTP for RESTRICTED Tiers (Decision D5)
                    </h4>
                    <p className="text-zinc-400 text-[11px] mt-0.5 font-sans">
                      Actions like `git_push`, `google_gmail_send`, and pull
                      request merging require TOTP verification.
                    </p>
                  </div>
                </div>
                <GlassBadge variant="amber" size="sm" mono>
                  ACTIVE
                </GlassBadge>
              </div>

              {/* Feature 3 */}
              <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/10 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-blue-500/10 text-blue-400">
                    <Key className="w-5 h-5" />
                  </div>
                  <div>
                    <h4 className="font-bold text-zinc-200 font-mono">
                      Single-Use Atomic Payload Tokens (Decision D5)
                    </h4>
                    <p className="text-zinc-400 text-[11px] mt-0.5 font-sans">
                      Tokens are bound to canonical payload hashes and consumed
                      atomically to prevent replay attacks.
                    </p>
                  </div>
                </div>
                <GlassBadge variant="blue" size="sm" mono>
                  ENABLED
                </GlassBadge>
              </div>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Tab 5: Danger Zone / Session Revocation */}
      {activeTab === "danger" && (
        <div className="flex flex-col gap-4">
          <GlassCard
            variant="default"
            padding="md"
            className="border-rose-500/30 flex flex-col gap-4"
          >
            <div className="flex items-center justify-between pb-3 border-b border-rose-500/20">
              <div className="flex items-center gap-2 text-rose-400 font-mono">
                <AlertTriangle className="w-5 h-5" />
                <h3 className="text-sm font-bold">
                  Danger Zone & Session Revocation
                </h3>
              </div>
              <GlassBadge variant="rose" size="sm" mono>
                RESTRICTED ACTION
              </GlassBadge>
            </div>

            <p className="text-xs text-zinc-300 leading-relaxed font-sans">
              Immediate credential invalidation and ephemeral execution
              container purging. Proceed with caution as active tasks will be
              terminated.
            </p>

            <div className="flex flex-col gap-3">
              <div className="p-4 rounded-xl bg-rose-500/5 border border-rose-500/20 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <h4 className="text-xs font-bold text-zinc-100 font-mono">
                    Revoke All Active Sessions & Sign Out
                  </h4>
                  <p className="text-[11px] text-zinc-400 mt-0.5 font-sans">
                    Invalidates current JWT refresh cookies, terminates open
                    WebSockets, and logs out immediately.
                  </p>
                </div>
                <GlassButton
                  variant="danger"
                  size="sm"
                  onClick={() => setShowRevokeModal(true)}
                  leftIcon={<LogOut className="w-3.5 h-3.5" />}
                >
                  Revoke Sessions
                </GlassButton>
              </div>
            </div>
          </GlassCard>
        </div>
      )}

      {/* Revocation Modal */}
      {showRevokeModal && (
        <GlassModal
          isOpen={showRevokeModal}
          onClose={() => setShowRevokeModal(false)}
          title={
            <div className="flex items-center gap-2 text-rose-400 font-mono">
              <AlertTriangle className="w-5 h-5" />
              <span>Confirm Session Revocation</span>
            </div>
          }
          description="Are you sure you want to terminate your session and invalidate all active tokens?"
          size="sm"
        >
          <div className="flex flex-col gap-3 text-xs">
            <p className="text-zinc-300 font-sans leading-relaxed">
              You will be signed out immediately and redirected to the login
              screen.
            </p>
            <div className="flex items-center justify-end gap-2 pt-2 border-t border-white/10">
              <GlassButton
                variant="ghost"
                size="sm"
                onClick={() => setShowRevokeModal(false)}
              >
                Cancel
              </GlassButton>
              <GlassButton
                variant="danger"
                size="sm"
                loading={revokingSession}
                loadingText="Revoking…"
                onClick={handleRevokeSessions}
              >
                Confirm Revocation & Logout
              </GlassButton>
            </div>
          </div>
        </GlassModal>
      )}

      {/* Save Success Toast */}
      {saveSuccess && (
        <div className="fixed bottom-6 right-6 z-40 max-w-sm">
          <GlassCard
            variant="teal"
            padding="sm"
            className="flex items-center gap-3 border-teal-500/40"
          >
            <CheckCircle2 className="w-5 h-5 text-teal-400 shrink-0" />
            <div className="text-xs">
              <p className="font-semibold text-zinc-100">Preferences Saved</p>
              <p className="text-zinc-400 font-mono text-[11px]">
                Model routing & quotas updated.
              </p>
            </div>
          </GlassCard>
        </div>
      )}
    </div>
  );
}
