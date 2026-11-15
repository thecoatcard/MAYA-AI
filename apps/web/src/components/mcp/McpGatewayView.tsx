"use client";

import {
  Globe,
  Plus,
  RefreshCw,
  Search,
  Server,
  Terminal,
  Trash2,
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
  Badge,
  Button,
  EmptyState,
  ErrorState,
  GlassInput,
  GlassModal,
  Notice,
  Pane,
  ScrollArea,
  Skeleton,
  Truncate,
  WrapText,
  type Tone,
} from "../ui";

/** Static suggestions. None of these is connected; the list is not read from the server. */
const SUGGESTIONS: ReadonlyArray<{
  readonly id: string;
  readonly name: string;
  readonly desc: string;
}> = [
  {
    id: "github",
    name: "GitHub",
    desc: "Repositories, issues and pull requests.",
  },
  {
    id: "notion",
    name: "Notion",
    desc: "Pages and databases from a Notion workspace.",
  },
  {
    id: "google",
    name: "Google Workspace",
    desc: "Gmail and Google Calendar.",
  },
  {
    id: "fs",
    name: "Sandboxed Filesystem",
    desc: "Files inside MAYA's own workspace folder.",
  },
  {
    id: "git",
    name: "Local Git Engine",
    desc: "Status, diffs and commits for a local repository.",
  },
];

const FIELD =
  "w-full rounded-maya-md border border-maya-control-border bg-maya-pane px-3.5 py-2 text-maya-body text-maya-text hover:border-maya-border-strong focus:border-maya-focus";

/** A URL without credentials, query or fragment: those can hold secrets and are never shown. */
export function safeUrl(raw: string): string {
  try {
    const u = new URL(raw);
    return `${u.protocol}//${u.host}${u.pathname === "/" ? "" : u.pathname}`;
  } catch {
    return raw.split(/[?#]/)[0] ?? "";
  }
}

/** Hides the value of an argument that looks like a secret (--token=abc becomes --token=...). */
export function safeArg(arg: string): string {
  const eq = arg.indexOf("=");
  if (
    eq > 0 &&
    /token|key|secret|password|passwd|auth/i.test(arg.slice(0, eq))
  ) {
    return `${arg.slice(0, eq)}=…`;
  }
  return arg;
}

function health(status: string): { tone: Tone; label: string } {
  switch (status.toLowerCase()) {
    case "healthy":
      return { tone: "success", label: "Connected" };
    case "degraded":
      return { tone: "warning", label: "Degraded" };
    case "dead":
      return { tone: "danger", label: "Failed" };
    default:
      return { tone: "neutral", label: "Unknown" };
  }
}

function usable(server: McpServer): boolean {
  return Boolean(server.schema_hash && server.pinned_version);
}

function when(iso?: string | null): string {
  if (!iso) return "Never checked";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "Unknown" : d.toLocaleString();
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

function Detail({
  label,
  children,
}: {
  readonly label: string;
  readonly children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="grid min-w-0 gap-0.5 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-3">
      <dt className="text-maya-caption text-maya-muted">{label}</dt>
      <dd className="m-0 min-w-0 text-maya-small text-maya-secondary">
        {children}
      </dd>
    </div>
  );
}

function ServerDetails({
  server,
}: {
  readonly server: McpServer;
}): React.JSX.Element {
  return (
    <dl className="m-0 grid min-w-0 gap-1.5">
      {server.transport === "stdio" ? (
        <Detail label="Command">
          <WrapText className="font-mono text-maya-mono">
            {[server.command ?? "", ...(server.args ?? []).map(safeArg)]
              .join(" ")
              .trim() || "None"}
          </WrapText>
        </Detail>
      ) : (
        <Detail label="Address">
          <WrapText className="font-mono text-maya-mono">
            {server.url ? safeUrl(server.url) : "None"}
          </WrapText>
        </Detail>
      )}
      <Detail label="Pinned version">
        <WrapText>{server.pinned_version || "Not set"}</WrapText>
      </Detail>
      <Detail label="Schema hash">
        <WrapText className="font-mono text-maya-mono">
          {server.schema_hash || "Not set"}
        </WrapText>
      </Detail>
      <Detail label="Last status check">{when(server.last_ping_at)}</Detail>
      {server.consecutive_failures > 0 && (
        <Detail label="Failed checks in a row">
          {server.consecutive_failures}
        </Detail>
      )}
    </dl>
  );
}

export function McpGatewayView(): React.JSX.Element {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [query, setQuery] = useState<string>("");

  const [inspecting, setInspecting] = useState<McpServer | null>(null);
  const [removing, setRemoving] = useState<McpServer | null>(null);
  const [removeBusy, setRemoveBusy] = useState<boolean>(false);
  const [checkingId, setCheckingId] = useState<string | null>(null);
  const [checkNote, setCheckNote] = useState<{
    id: string;
    ok: boolean;
    text: string;
  } | null>(null);

  const [isRegisterOpen, setIsRegisterOpen] = useState<boolean>(false);
  const [name, setName] = useState<string>("");
  const [transport, setTransport] = useState<"stdio" | "streamable_http">(
    "stdio",
  );
  const [command, setCommand] = useState<string>("");
  const [argsText, setArgsText] = useState<string>("");
  const [url, setUrl] = useState<string>("");
  const [pinnedVersion, setPinnedVersion] = useState<string>("");
  const [schemaHash, setSchemaHash] = useState<string>("");
  const [registering, setRegistering] = useState<boolean>(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  const loadServers = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setServers(await listMcpServers());
    } catch (err) {
      setLoadError(errorText(err, "The server list could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadServers();
  }, [loadServers]);

  const checkStatus = async (server: McpServer): Promise<void> => {
    setCheckingId(server.id);
    setCheckNote(null);
    try {
      const result = await getMcpServerHealth(server.id);
      setServers((prev) =>
        prev.map((s) =>
          s.id === server.id
            ? {
                ...s,
                health_status: result.health_status,
                last_ping_at: result.last_ping_at,
                consecutive_failures: result.consecutive_failures,
                tool_count: result.tool_count,
              }
            : s,
        ),
      );
      setCheckNote({
        id: server.id,
        ok: true,
        text: `Status read: ${health(result.health_status).label.toLowerCase()}.`,
      });
    } catch (err) {
      setCheckNote({
        id: server.id,
        ok: false,
        text: errorText(err, "The status could not be read."),
      });
    } finally {
      setCheckingId(null);
    }
  };

  const confirmRemove = async (): Promise<void> => {
    if (!removing) return;
    setRemoveBusy(true);
    try {
      await deleteMcpServer(removing.id);
      setRemoving(null);
      await loadServers();
    } catch (err) {
      setRemoving(null);
      setActionError(errorText(err, "The server could not be removed."));
    } finally {
      setRemoveBusy(false);
    }
  };

  const openRegister = (): void => {
    setRegisterError(null);
    setIsRegisterOpen(true);
  };

  const submitRegister = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setRegistering(true);
    setRegisterError(null);
    const payload: McpServerCreate = {
      name: name.trim(),
      transport,
      command: transport === "stdio" ? command.trim() || undefined : undefined,
      args:
        transport === "stdio" && argsText.trim()
          ? argsText
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean)
          : undefined,
      url:
        transport === "streamable_http" ? url.trim() || undefined : undefined,
      pinned_version: pinnedVersion.trim() || undefined,
      schema_hash: schemaHash.trim() || undefined,
    };
    try {
      await registerMcpServer(payload);
      setIsRegisterOpen(false);
      setName("");
      setCommand("");
      setArgsText("");
      setUrl("");
      setPinnedVersion("");
      setSchemaHash("");
      await loadServers();
    } catch (err) {
      setRegisterError(errorText(err, "The server could not be added."));
    } finally {
      setRegistering(false);
    }
  };

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return servers;
    return servers.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.transport.toLowerCase().includes(q) ||
        (s.url ? safeUrl(s.url).toLowerCase().includes(q) : false),
    );
  }, [servers, query]);

  const manifest = inspecting?.tools_manifest;

  return (
    <div className="h-full min-h-0 min-w-0">
      <Pane as="div" bodyLabel="Connections">
        <div className="mx-auto grid w-full max-w-(--maya-column-wide) min-w-0 gap-8 p-4 sm:p-6">
          <header className="flex min-w-0 flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1 basis-64">
              <h1 className="m-0 text-maya-title font-semibold text-maya-text">
                Connections
              </h1>
              <p className="m-0 mt-1 max-w-(--maya-measure) text-maya-small text-maya-secondary">
                Tool servers you added for MAYA to use, and whether they are
                reachable.
              </p>
            </div>
            <div className="flex min-w-0 flex-wrap gap-2">
              <Button
                variant="secondary"
                leftIcon={
                  <RefreshCw
                    size={16}
                    aria-hidden="true"
                    className={
                      loading
                        ? "animate-spin motion-reduce:animate-none"
                        : undefined
                    }
                  />
                }
                onClick={() => void loadServers()}
                disabled={loading}
              >
                Refresh list
              </Button>
              <Button
                variant="primary"
                leftIcon={<Plus size={16} aria-hidden="true" />}
                onClick={openRegister}
              >
                Register server
              </Button>
            </div>
          </header>

          {actionError !== null && (
            <Notice tone="danger" onDismiss={() => setActionError(null)}>
              {actionError}
            </Notice>
          )}

          <section aria-labelledby="mcp-servers" className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 border-b border-maya-border pb-2">
              <h2
                id="mcp-servers"
                className="m-0 text-maya-heading font-semibold text-maya-text"
              >
                Your servers
                {!loading && loadError === null ? ` (${servers.length})` : ""}
              </h2>
              {servers.length > 0 && (
                <div className="w-full min-w-0 sm:w-72">
                  <GlassInput
                    aria-label="Search servers"
                    placeholder="Search servers"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    leftIcon={
                      <Search className="size-3.5" aria-hidden="true" />
                    }
                    clearable
                    onClear={() => setQuery("")}
                  />
                </div>
              )}
            </div>

            {loadError !== null ? (
              <div className="mt-3">
                <ErrorState
                  title="Your servers could not be loaded"
                  message={loadError}
                  onRetry={() => void loadServers()}
                />
              </div>
            ) : loading && servers.length === 0 ? (
              <div aria-busy="true" className="mt-3">
                <Skeleton lines={4} />
              </div>
            ) : servers.length === 0 ? (
              <div className="mt-3">
                <EmptyState
                  icon={Server}
                  title="No servers registered"
                  description="Register a tool server to let MAYA use its tools."
                  action={
                    <Button variant="primary" onClick={openRegister}>
                      Register a server
                    </Button>
                  }
                />
              </div>
            ) : shown.length === 0 ? (
              <p className="m-0 mt-3 text-maya-small text-maya-muted">
                No server matches that search.
              </p>
            ) : (
              <ul className="m-0 grid min-w-0 list-none gap-3 p-0 pt-3">
                {shown.map((s) => {
                  const h = health(s.health_status);
                  return (
                    <li
                      key={s.id}
                      className="grid min-w-0 shrink-0 gap-3 rounded-maya-lg border border-maya-border bg-maya-pane p-3"
                    >
                      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                        <div className="flex min-w-0 flex-1 basis-48 items-center gap-2">
                          <Server
                            size={16}
                            aria-hidden="true"
                            className="shrink-0 text-maya-muted"
                          />
                          <Truncate
                            as="h3"
                            dir="auto"
                            className="m-0 text-maya-body font-semibold"
                          >
                            {s.name}
                          </Truncate>
                        </div>
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <Badge tone={h.tone}>{h.label}</Badge>
                          <Badge
                            tone="neutral"
                            icon={s.transport === "stdio" ? Terminal : Globe}
                          >
                            {s.transport}
                          </Badge>
                          <Badge tone="neutral" icon={Server}>
                            {s.tool_count === 1
                              ? "1 tool"
                              : `${s.tool_count} tools`}
                          </Badge>
                        </div>
                      </div>

                      <ServerDetails server={s} />

                      {!usable(s) && (
                        <Notice tone="warning">
                          MAYA does not use this server yet: it needs a pinned
                          version and a schema hash.
                        </Notice>
                      )}

                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={checkingId === s.id}
                          title="Reads the status the server list last recorded"
                          onClick={() => void checkStatus(s)}
                        >
                          Refresh status
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setInspecting(s)}
                        >
                          Details
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          leftIcon={<Trash2 size={14} aria-hidden="true" />}
                          aria-label={`Remove ${s.name}`}
                          onClick={() => setRemoving(s)}
                        >
                          Remove
                        </Button>
                        {checkNote?.id === s.id && (
                          <span
                            role={checkNote.ok ? "status" : "alert"}
                            className="min-w-0 text-maya-small text-maya-secondary [overflow-wrap:anywhere]"
                          >
                            {checkNote.text}
                          </span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section aria-labelledby="mcp-suggestions" className="min-w-0">
            <h2
              id="mcp-suggestions"
              className="m-0 border-b border-maya-border pb-2 text-maya-heading font-semibold text-maya-text"
            >
              Suggestions, not connected
            </h2>
            <p className="m-0 mt-2 text-maya-small text-maya-secondary">
              A fixed list of ideas. Nothing here is connected, and it is not
              read from the server. Register a server above to connect one.
            </p>
            <ul className="m-0 mt-3 grid min-w-0 list-none gap-2 p-0 sm:grid-cols-2">
              {SUGGESTIONS.map((item) => (
                <li
                  key={item.id}
                  className="min-w-0 rounded-maya-lg border border-maya-border p-3"
                >
                  <p className="m-0 text-maya-body font-medium [overflow-wrap:anywhere]">
                    {item.name}
                  </p>
                  <p className="m-0 mt-0.5 text-maya-small text-maya-secondary">
                    {item.desc}
                  </p>
                </li>
              ))}
            </ul>
            <p className="m-0 mt-3 text-maya-small text-maya-muted">
              Planned: showing here whether GitHub, Gmail, Calendar and Notion
              are set up on this server. The server does not report that yet.
            </p>
          </section>
        </div>
      </Pane>

      {inspecting !== null && (
        <GlassModal
          isOpen
          onClose={() => setInspecting(null)}
          title={
            <span className="[overflow-wrap:anywhere]">{inspecting.name}</span>
          }
          description="What is recorded for this server."
          size="lg"
        >
          <div className="grid min-w-0 gap-4">
            <ServerDetails server={inspecting} />
            <div className="min-w-0">
              <h3 className="m-0 text-maya-body font-semibold">Tools</h3>
              {Array.isArray(manifest) && manifest.length > 0 ? (
                <ScrollArea
                  label="Tools of this server"
                  className="mt-2 max-h-72 rounded-maya-md border border-maya-border p-2"
                >
                  <ul className="m-0 grid list-none gap-2 p-0">
                    {manifest.map((tool, idx) => (
                      <li key={idx} className="min-w-0 shrink-0">
                        <p className="m-0 font-mono text-maya-mono text-maya-text [overflow-wrap:anywhere]">
                          {String(tool.name || `Tool ${idx + 1}`)}
                        </p>
                        {Boolean(tool.description) && (
                          <p className="m-0 text-maya-small text-maya-secondary [overflow-wrap:anywhere]">
                            {String(tool.description)}
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                </ScrollArea>
              ) : (
                <p className="m-0 mt-1 text-maya-small text-maya-muted">
                  No tool list is recorded for this server.
                </p>
              )}
            </div>
          </div>
        </GlassModal>
      )}

      {removing !== null && (
        <GlassModal
          isOpen
          onClose={() => setRemoving(null)}
          title="Remove this server?"
          size="sm"
        >
          <div className="grid min-w-0 gap-4">
            <p className="m-0 text-maya-body [overflow-wrap:anywhere]">
              MAYA stops using {removing.name}. You can register it again later.
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="ghost" onClick={() => setRemoving(null)}>
                Cancel
              </Button>
              <Button
                variant="danger"
                loading={removeBusy}
                onClick={() => void confirmRemove()}
              >
                Remove server
              </Button>
            </div>
          </div>
        </GlassModal>
      )}

      <GlassModal
        isOpen={isRegisterOpen}
        onClose={() => setIsRegisterOpen(false)}
        title="Register a server"
        description="MAYA only uses a server that has a pinned version and a schema hash."
        size="md"
      >
        <form
          onSubmit={(e) => void submitRegister(e)}
          className="grid min-w-0 gap-4"
        >
          {registerError !== null && (
            <Notice tone="danger">{registerError}</Notice>
          )}

          <GlassInput
            label="Name"
            required
            placeholder="analytics"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="mcp-transport"
              className="text-maya-caption font-medium text-maya-text"
            >
              Transport
            </label>
            <select
              id="mcp-transport"
              value={transport}
              onChange={(e) =>
                setTransport(e.target.value as "stdio" | "streamable_http")
              }
              className={FIELD}
            >
              <option value="stdio">stdio (a program on this computer)</option>
              <option value="streamable_http">
                streamable_http (a web address)
              </option>
            </select>
          </div>

          {transport === "stdio" ? (
            <>
              <GlassInput
                label="Command"
                required
                placeholder="node"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
              />
              <GlassInput
                label="Arguments"
                placeholder="dist/index.js, --port=8080"
                helperText="Separate arguments with commas, so an argument cannot contain a comma. Do not put passwords or tokens here."
                value={argsText}
                onChange={(e) => setArgsText(e.target.value)}
              />
            </>
          ) : (
            <GlassInput
              label="Address"
              type="url"
              required
              placeholder="https://example.com/mcp"
              helperText="Do not put passwords or tokens in the address."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          )}

          <div className="grid min-w-0 gap-4 sm:grid-cols-2">
            <GlassInput
              label="Pinned version"
              required
              placeholder="1.2.0"
              helperText="Unpinned launchers (@latest, npx -y) are refused."
              value={pinnedVersion}
              onChange={(e) => setPinnedVersion(e.target.value)}
            />
            <GlassInput
              label="Schema hash"
              required
              placeholder="sha256:…"
              helperText="MAYA refuses the server if its tool list stops matching."
              value={schemaHash}
              onChange={(e) => setSchemaHash(e.target.value)}
            />
          </div>

          <div className="flex flex-wrap justify-end gap-2 border-t border-maya-border pt-3">
            <Button variant="ghost" onClick={() => setIsRegisterOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={registering}>
              Add server
            </Button>
          </div>
        </form>
      </GlassModal>
    </div>
  );
}
