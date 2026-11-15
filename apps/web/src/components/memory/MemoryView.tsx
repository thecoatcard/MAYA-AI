"use client";

import {
  Brain,
  Copy,
  Download,
  FolderOpen,
  MessagesSquare,
  Infinity as InfinityIcon,
  Plus,
  RefreshCw,
  Search,
  Timer,
  Trash2,
  X,
} from "lucide-react";
import React, {
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { useOptionalRouter } from "../../hooks/useOptionalRouter";
import { copyText } from "../../lib/menu-actions";
import { ConversationContext } from "../chat/ConversationContext";

import {
  createMemoryItem,
  deleteMemoryItem,
  listMemoryItems,
  purgeMemory,
  searchMemory,
  type MemoryCreateRequest,
  type MemoryItem,
} from "../../lib/memory-client";
import {
  Badge,
  Button,
  CodeBlockShell,
  ConfirmDeleteDialog,
  MenuSurface,
  type ContextMenuEntry,
  EmptyState,
  ErrorState,
  GlassInput,
  GlassModal,
  GlassTextarea,
  Notice,
  Pane,
  Skeleton,
  Truncate,
  WrapText,
  type Tone,
} from "../ui";

export type MemoryCategory =
  "preference" | "project" | "episodic" | "knowledge";

export interface MemoryEntry {
  readonly id: string;
  readonly category: MemoryCategory;
  /** Only a real title from the record's metadata. Never a cut of the content. */
  readonly title: string | undefined;
  readonly content: string;
  readonly source?: string;
  readonly sourceUrl?: string;
  readonly relevance?: number;
  readonly decayExempt: boolean | undefined;
  readonly expiresAt: string | null;
  readonly createdAt: string;
  readonly metadata: Record<string, unknown>;
}

/** Items per page; the server default for `GET /memory/items` (limit 50). */
const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

const CATEGORIES: readonly {
  readonly id: "all" | MemoryCategory;
  readonly label: string;
}[] = [
  { id: "all", label: "All" },
  { id: "preference", label: "Preferences" },
  { id: "project", label: "Projects" },
  { id: "knowledge", label: "Knowledge" },
  { id: "episodic", label: "Episodes" },
];

const CATEGORY_LABEL: Readonly<Record<MemoryCategory, string>> = {
  preference: "Preference",
  project: "Project",
  knowledge: "Knowledge",
  episodic: "Episode",
};

const CATEGORY_TONE: Readonly<Record<MemoryCategory, Tone>> = {
  preference: "accent",
  project: "info",
  knowledge: "neutral",
  episodic: "warning",
};

function toCategory(value: string): MemoryCategory {
  return value === "preference" ||
    value === "project" ||
    value === "episodic" ||
    value === "knowledge"
    ? value
    : "knowledge";
}

/** Only web links are made clickable (a stored `javascript:` URL must not become a link). */
function safeHref(url: string | undefined): string | undefined {
  if (url === undefined) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:"
      ? parsed.href
      : undefined;
  } catch {
    return undefined;
  }
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
}

function mapMemoryItemToEntry(
  item: MemoryItem,
  similarityScore?: number,
  fromSearch = false,
): MemoryEntry {
  const metadata = item.metadata || {};
  const rawTitle =
    typeof metadata.title === "string" ? metadata.title.trim() : "";
  const source =
    typeof metadata.source === "string"
      ? metadata.source
      : typeof metadata.citation === "string"
        ? metadata.citation
        : typeof metadata.task_id === "string"
          ? `Task ${metadata.task_id}`
          : undefined;
  const sourceUrl =
    typeof metadata.sourceUrl === "string" ? metadata.sourceUrl : undefined;

  return {
    id: item.id,
    category: toCategory(item.category),
    // A title that repeats the body is noise: show the text once.
    title:
      rawTitle !== "" && rawTitle !== item.content.trim()
        ? rawTitle
        : undefined,
    content: item.content,
    source,
    sourceUrl,
    relevance: similarityScore,
    decayExempt: fromSearch ? undefined : Boolean(item.permanent),
    expiresAt: item.expires_at,
    createdAt: item.created_at,
    metadata: item.metadata,
  };
}

function nameOf(entry: MemoryEntry): string {
  return entry.title ?? entry.content.slice(0, 40);
}

/** Retention in words, from real fields only. Search results do not carry it. */
function Retention({ entry }: { readonly entry: MemoryEntry }) {
  if (entry.decayExempt === undefined) return null;
  if (entry.decayExempt) {
    return (
      <span className="inline-flex items-center gap-1">
        <InfinityIcon aria-hidden="true" size={14} className="shrink-0" />
        Kept permanently
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <Timer aria-hidden="true" size={14} className="shrink-0" />
      {entry.expiresAt !== null
        ? `Expires ${formatDate(entry.expiresAt)}`
        : "Standard retention"}
    </span>
  );
}

/** Two-step delete: the first press asks, the second removes. Nothing is deleted by one stray click. */
function DeleteControl({
  entry,
  busy,
  onConfirm,
  label,
}: {
  readonly entry: MemoryEntry;
  readonly busy: boolean;
  readonly onConfirm: () => void;
  readonly label?: string;
}): React.JSX.Element {
  const [asking, setAsking] = useState<boolean>(false);
  if (!asking) {
    return (
      <Button
        variant="ghost"
        size="sm"
        aria-label={`Delete memory ${nameOf(entry)}`}
        leftIcon={<Trash2 size={14} />}
        disabled={busy}
        onClick={() => setAsking(true)}
      >
        {label ?? "Delete"}
      </Button>
    );
  }
  return (
    <span
      role="group"
      aria-label="Confirm delete"
      className="inline-flex flex-wrap items-center gap-2"
    >
      <span className="text-maya-caption text-maya-secondary">
        Delete this memory?
      </span>
      <Button
        variant="danger"
        size="sm"
        loading={busy}
        onClick={() => {
          onConfirm();
          setAsking(false);
        }}
      >
        Yes, delete
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setAsking(false)}>
        Keep
      </Button>
    </span>
  );
}

const LABEL = "text-maya-caption font-medium text-maya-text";

export function MemoryView(): React.JSX.Element {
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [searchInput, setSearchInput] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [selectedEntry, setSelectedEntry] = useState<MemoryEntry | null>(null);
  const [showPurgeModal, setShowPurgeModal] = useState<boolean>(false);
  const [purgeConfirmation, setPurgeConfirmation] = useState<string>("");
  const [purgeSuccess, setPurgeSuccess] = useState<boolean>(false);
  const [isPurging, setIsPurging] = useState<boolean>(false);
  const [memories, setMemories] = useState<readonly MemoryEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const [hasMore, setHasMore] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [showCreateModal, setShowCreateModal] = useState<boolean>(false);
  const [createCategory, setCreateCategory] =
    useState<MemoryCategory>("preference");
  const [createContent, setCreateContent] = useState<string>("");
  const [createPermanent, setCreatePermanent] = useState<boolean>(false);
  const [isCreating, setIsCreating] = useState<boolean>(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // "Forget" from the context menu asks in a dialog (the inline control is for the row button).
  const [forgetting, setForgetting] = useState<MemoryEntry | null>(null);
  const runtimeCtx = useContext(ConversationContext);
  const router = useOptionalRouter();
  const memoryItems = (mem: MemoryEntry): ContextMenuEntry[] => {
    // There is no memory update endpoint in the client, so there is no Edit action.
    const meta = mem.metadata;
    const sid =
      typeof meta.session_id === "string"
        ? meta.session_id
        : typeof meta.source_session_id === "string"
          ? meta.source_session_id
          : null;
    const items: ContextMenuEntry[] = [
      {
        id: "open",
        label: "Open",
        icon: <FolderOpen size={14} />,
        onSelect: () => setSelectedEntry(mem),
      },
      {
        id: "copy",
        label: "Copy",
        icon: <Copy size={14} />,
        onSelect: () => void copyText(mem.content),
      },
    ];
    if (sid !== null && runtimeCtx !== null) {
      items.push({
        id: "source",
        label: "Show source conversation",
        icon: <MessagesSquare size={14} />,
        onSelect: () => {
          void runtimeCtx.openConversation(sid);
          router.push("/");
        },
      });
    }
    items.push(
      { kind: "separator" },
      {
        id: "forget",
        label: "Forget",
        icon: <Trash2 size={14} />,
        danger: true,
        onSelect: () => setForgetting(mem),
      },
    );
    return items;
  };

  /** Ignores the answer of a request that a newer one has replaced. */
  const requestId = useRef<number>(0);

  useEffect(() => {
    const handle = setTimeout(
      () => setSearchQuery(searchInput),
      searchInput === "" ? 0 : SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(handle);
  }, [searchInput]);

  const searching = searchQuery.trim().length > 0;

  const loadMemories = useCallback(async () => {
    const mine = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      if (searchQuery.trim().length > 0) {
        const res = await searchMemory({
          query: searchQuery.trim(),
          categories: categoryFilter !== "all" ? [categoryFilter] : undefined,
        });
        if (mine !== requestId.current) return;
        setMemories(
          (res.results || []).map((r) =>
            mapMemoryItemToEntry(
              {
                id: r.id,
                workspace_id: "",
                user_id: "",
                category: r.category,
                content: r.content,
                metadata: r.metadata,
                permanent: false,
                access_count: 0,
                last_accessed_at: null,
                expires_at: null,
                created_at: r.created_at,
              },
              r.effective_score ?? r.raw_similarity,
              true,
            ),
          ),
        );
        setHasMore(false);
      } else {
        const items = await listMemoryItems(
          categoryFilter !== "all" ? categoryFilter : undefined,
        );
        if (mine !== requestId.current) return;
        setMemories(items.map((item) => mapMemoryItemToEntry(item)));
        setHasMore(items.length >= PAGE_SIZE);
      }
    } catch {
      if (mine !== requestId.current) return;
      setError("Unable to load memory records from the MAYA backend.");
      setMemories([]);
      setHasMore(false);
    } finally {
      if (mine === requestId.current) setLoading(false);
    }
  }, [categoryFilter, searchQuery]);

  useEffect(() => {
    void loadMemories();
  }, [loadMemories]);

  const loadMore = async (): Promise<void> => {
    if (loadingMore || searching) return;
    setLoadingMore(true);
    setActionError(null);
    const mine = requestId.current;
    try {
      const items = await listMemoryItems(
        categoryFilter !== "all" ? categoryFilter : undefined,
        PAGE_SIZE,
        memories.length,
      );
      if (mine !== requestId.current) return;
      setMemories((prev) => {
        const seen = new Set(prev.map((m) => m.id));
        return [
          ...prev,
          ...items
            .filter((item) => !seen.has(item.id))
            .map((item) => mapMemoryItemToEntry(item)),
        ];
      });
      setHasMore(items.length >= PAGE_SIZE);
    } catch {
      setActionError("Could not load more memories. Try again.");
    } finally {
      setLoadingMore(false);
    }
  };

  const handleCreateMemory = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createContent.trim() || isCreating) return;
    setIsCreating(true);
    try {
      const payload: MemoryCreateRequest = {
        category: createCategory,
        content: createContent.trim(),
        permanent: createPermanent,
      };
      await createMemoryItem(payload);
      setShowCreateModal(false);
      setCreateContent("");
      setCreatePermanent(false);
      setActionError(null);
      await loadMemories();
    } catch {
      setActionError("Failed to create memory item.");
    } finally {
      setIsCreating(false);
    }
  };

  const handleDeleteMemory = async (itemId: string) => {
    setDeletingId(itemId);
    setActionError(null);
    try {
      await deleteMemoryItem(itemId);
      setMemories((prev) => prev.filter((m) => m.id !== itemId));
      setSelectedEntry((curr) => (curr?.id === itemId ? null : curr));
    } catch {
      setActionError("Failed to delete memory item. It was not removed.");
    } finally {
      setDeletingId(null);
    }
  };

  const handleExecutePurge = async () => {
    setIsPurging(true);
    try {
      await purgeMemory("all");
      setMemories([]);
      setHasMore(false);
      setSelectedEntry(null);
      setShowPurgeModal(false);
      setPurgeConfirmation("");
      setPurgeSuccess(true);
    } catch {
      setShowPurgeModal(false);
      setPurgeConfirmation("");
      setActionError("Failed to erase memories.");
    } finally {
      setIsPurging(false);
    }
  };

  const exportKnowledgeJson = () => {
    const dataStr =
      "data:text/json;charset=utf-8," +
      encodeURIComponent(JSON.stringify(memories, null, 2));
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute(
      "download",
      `maya_memory_export_${new Date().toISOString().slice(0, 10)}.json`,
    );
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  const countText = loading
    ? "Loading…"
    : searching
      ? `${memories.length} ${memories.length === 1 ? "result" : "results"}`
      : `${memories.length}${hasMore ? "+" : ""} ${
          memories.length === 1 && !hasMore ? "memory" : "memories"
        }`;

  const header = (
    <div className="grid min-w-0 gap-3 py-1">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 items-baseline gap-3">
          <h1 className="m-0 text-maya-heading font-medium">Memory</h1>
          <span
            className="text-maya-caption text-maya-muted"
            aria-live="polite"
            data-testid="memory-count"
          >
            {countText}
          </span>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowCreateModal(true)}
            leftIcon={<Plus size={14} />}
          >
            Add Memory
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={exportKnowledgeJson}
            disabled={memories.length === 0}
            leftIcon={<Download size={14} />}
          >
            Export
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={() => setShowPurgeModal(true)}
            leftIcon={<Trash2 size={14} />}
          >
            Purge Memory
          </Button>
        </div>
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
            variant={categoryFilter === tab.id ? "primary" : "secondary"}
            aria-pressed={categoryFilter === tab.id}
            onClick={() => setCategoryFilter(tab.id)}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      <div className="min-w-0 max-w-xl">
        <GlassInput
          placeholder="Search memories"
          aria-label="Search memories"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          leftIcon={<Search size={14} aria-hidden="true" />}
          clearable
          onClear={() => setSearchInput("")}
        />
      </div>
    </div>
  );

  return (
    <div className="h-full min-h-0 min-w-0">
      <Pane
        aria-label="Memory"
        header={header}
        bodyLabel="Memories"
        bodyClassName="p-4"
      >
        <div className="mx-auto grid min-w-0 max-w-5xl gap-4">
          {purgeSuccess && (
            <Notice
              tone="success"
              title="All memories erased"
              onDismiss={() => setPurgeSuccess(false)}
            >
              Nothing is stored for you any more.
            </Notice>
          )}
          {actionError !== null && (
            <Notice tone="danger" onDismiss={() => setActionError(null)}>
              {actionError}
            </Notice>
          )}

          {error !== null && (
            <ErrorState
              title={error}
              message="Your memories are safe; this view just could not reach them."
              retry={
                <Button
                  size="sm"
                  variant="secondary"
                  leftIcon={<RefreshCw size={14} />}
                  onClick={() => void loadMemories()}
                >
                  Retry
                </Button>
              }
            />
          )}

          {loading && (
            <div aria-busy="true" className="grid gap-3">
              <p className="m-0 text-maya-small text-maya-muted">
                Loading memories…
              </p>
              <Skeleton lines={3} />
              <Skeleton lines={3} />
            </div>
          )}

          {!loading && error === null && memories.length === 0 && (
            <EmptyState
              icon={Brain}
              title="No memory items recorded"
              description={
                searching
                  ? `No memories matched "${searchQuery.trim()}".`
                  : "Things MAYA learns from your conversations, and anything you add yourself, will appear here."
              }
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  leftIcon={<Plus size={14} />}
                  onClick={() => setShowCreateModal(true)}
                >
                  Add first memory item
                </Button>
              }
            />
          )}

          {!loading && memories.length > 0 && (
            <ul className="m-0 grid min-w-0 list-none gap-3 p-0">
              {memories.map((mem) => (
                <MenuSurface
                  as="li"
                  key={mem.id}
                  label={nameOf(mem)}
                  getItems={() => memoryItems(mem)}
                  className="min-w-0 shrink-0 rounded-maya-lg overflow-hidden border border-maya-border bg-maya-raised"
                >
                  <button
                    type="button"
                    onClick={() => setSelectedEntry(mem)}
                    aria-label={`Open memory ${nameOf(mem)}`}
                    className="motion-interactive grid w-full min-w-0 cursor-pointer gap-2 p-3 text-left hover:bg-maya-hover"
                  >
                    <span className="flex min-w-0 flex-wrap items-center gap-2">
                      <Badge tone={CATEGORY_TONE[mem.category]}>
                        {CATEGORY_LABEL[mem.category]}
                      </Badge>
                      {mem.relevance !== undefined && (
                        <Badge tone="neutral">
                          {Math.round(mem.relevance * 100)}% match
                        </Badge>
                      )}
                      {mem.title !== undefined && (
                        <Truncate
                          className="min-w-0 flex-1 text-maya-small font-medium text-maya-text"
                          title={mem.title}
                        >
                          {mem.title}
                        </Truncate>
                      )}
                    </span>
                    <Truncate
                      as="span"
                      lines={3}
                      dir="auto"
                      className="text-maya-body text-maya-text"
                      title={mem.content}
                    >
                      {mem.content}
                    </Truncate>
                  </button>
                  <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-maya-border px-3 py-2 text-maya-caption text-maya-muted">
                    <span className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
                      <span>{formatDate(mem.createdAt)}</span>
                      {mem.source !== undefined && (
                        <Truncate
                          className="max-w-64"
                          title={`Source: ${mem.source}`}
                        >
                          {mem.source}
                        </Truncate>
                      )}
                      <Retention entry={mem} />
                    </span>
                    <DeleteControl
                      entry={mem}
                      busy={deletingId === mem.id}
                      onConfirm={() => void handleDeleteMemory(mem.id)}
                    />
                  </div>
                </MenuSurface>
              ))}
            </ul>
          )}

          {!loading && hasMore && !searching && (
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

      {forgetting !== null && (
        <ConfirmDeleteDialog
          title="Forget this memory?"
          removes={[`The memory "${nameOf(forgetting)}".`]}
          keeps={["Your conversations and files."]}
          confirmLabel="Forget memory"
          busy={deletingId === forgetting.id}
          onClose={() => setForgetting(null)}
          onConfirm={() => {
            const id = forgetting.id;
            void handleDeleteMemory(id).finally(() => setForgetting(null));
          }}
        />
      )}

      {selectedEntry && (
        <GlassModal
          isOpen
          onClose={() => setSelectedEntry(null)}
          title={
            <span className="min-w-0 [overflow-wrap:anywhere]">
              {selectedEntry.title ?? CATEGORY_LABEL[selectedEntry.category]}
            </span>
          }
          description={`${CATEGORY_LABEL[selectedEntry.category]} memory, saved ${formatDate(selectedEntry.createdAt)}`}
          size="md"
        >
          <div className="grid min-w-0 gap-4 text-maya-small">
            {selectedEntry.relevance !== undefined && (
              <p className="m-0 text-maya-secondary">
                Match for your search:{" "}
                {Math.round(selectedEntry.relevance * 100)}%
              </p>
            )}

            <div className="grid gap-1">
              <span className={LABEL}>Memory</span>
              <WrapText
                as="p"
                dir="auto"
                className="m-0 block rounded-maya-md border border-maya-border bg-maya-code p-3 text-maya-body text-maya-text"
              >
                {selectedEntry.content}
              </WrapText>
            </div>

            <p className="m-0 flex flex-wrap gap-x-4 gap-y-1 text-maya-caption text-maya-muted">
              <Retention entry={selectedEntry} />
            </p>

            {selectedEntry.source && (
              <div className="grid gap-1">
                <span className={LABEL}>Source</span>
                <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 text-maya-secondary">
                  <WrapText className="min-w-0">
                    {selectedEntry.source}
                  </WrapText>
                  {safeHref(selectedEntry.sourceUrl) && (
                    <a
                      href={safeHref(selectedEntry.sourceUrl)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="shrink-0 text-maya-link underline"
                    >
                      Open reference
                    </a>
                  )}
                </div>
              </div>
            )}

            {Object.keys(selectedEntry.metadata ?? {}).length > 0 && (
              <details className="min-w-0">
                <summary className="cursor-pointer text-maya-caption text-maya-secondary">
                  Technical details
                </summary>
                <CodeBlockShell label="Memory metadata" className="mt-2">
                  {JSON.stringify(selectedEntry.metadata, null, 2)}
                </CodeBlockShell>
              </details>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-maya-border pt-3">
              <DeleteControl
                entry={selectedEntry}
                label="Delete item"
                busy={deletingId === selectedEntry.id}
                onConfirm={() => void handleDeleteMemory(selectedEntry.id)}
              />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedEntry(null)}
              >
                Close
              </Button>
            </div>
          </div>
        </GlassModal>
      )}

      {showCreateModal && (
        <GlassModal
          isOpen
          onClose={() => setShowCreateModal(false)}
          title="Add Memory Item"
          description="Save something you want MAYA to remember."
          size="md"
        >
          <form
            onSubmit={handleCreateMemory}
            className="grid min-w-0 gap-4 text-maya-small"
          >
            <div className="grid gap-1.5">
              <label htmlFor="memory-create-category" className={LABEL}>
                Category
              </label>
              <select
                id="memory-create-category"
                value={createCategory}
                onChange={(e) => setCreateCategory(toCategory(e.target.value))}
                className="w-full rounded-maya-md border border-maya-control-border bg-maya-pane px-3 py-2 text-maya-body text-maya-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maya-focus"
              >
                <option value="preference">Preference</option>
                <option value="project">Project</option>
                <option value="knowledge">Knowledge</option>
                <option value="episodic">Episode</option>
              </select>
            </div>

            <GlassTextarea
              label="Content"
              required
              value={createContent}
              onChange={(e) => setCreateContent(e.target.value)}
              placeholder="Enter memory content..."
              rows={4}
              dir="auto"
            />

            <label className="flex cursor-pointer items-center gap-2 text-maya-secondary">
              <input
                type="checkbox"
                checked={createPermanent}
                onChange={(e) => setCreatePermanent(e.target.checked)}
                className="size-4 accent-maya-accent"
              />
              <span>Keep permanently (never fades)</span>
            </label>

            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-maya-border pt-3">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowCreateModal(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                type="submit"
                disabled={!createContent.trim()}
                loading={isCreating}
              >
                Save Memory
              </Button>
            </div>
          </form>
        </GlassModal>
      )}

      {showPurgeModal && (
        <GlassModal
          isOpen
          onClose={() => {
            setShowPurgeModal(false);
            setPurgeConfirmation("");
          }}
          title="Erase all memory"
          description="This cannot be undone."
          size="sm"
        >
          <div className="grid min-w-0 gap-4 text-maya-small">
            <p className="m-0 text-maya-secondary">
              Every memory MAYA holds for you will be permanently deleted. Type{" "}
              <strong className="text-maya-danger">PURGE</strong> to confirm.
            </p>

            <GlassInput
              placeholder="Type PURGE"
              aria-label="Type PURGE to confirm"
              value={purgeConfirmation}
              onChange={(e) => setPurgeConfirmation(e.target.value)}
            />

            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-maya-border pt-3">
              <Button
                variant="ghost"
                size="sm"
                leftIcon={<X size={14} />}
                onClick={() => {
                  setShowPurgeModal(false);
                  setPurgeConfirmation("");
                }}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                size="sm"
                disabled={purgeConfirmation !== "PURGE"}
                loading={isPurging}
                onClick={() => void handleExecutePurge()}
              >
                Erase All Memory Items
              </Button>
            </div>
          </div>
        </GlassModal>
      )}
    </div>
  );
}
