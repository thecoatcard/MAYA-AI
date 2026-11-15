"use client";

import {
  Archive,
  ArchiveRestore,
  ArrowDownAZ,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  CopyPlus,
  Download,
  Eye,
  EyeOff,
  FolderOpen,
  MoreHorizontal,
  PanelLeftClose,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import React, { useEffect, useMemo, useRef, useState } from "react";

import type { SessionSummary } from "../../lib/api-client";
import { copyText } from "../../lib/menu-actions";
import {
  ConfirmDeleteDialog,
  MenuButton,
  useContextMenu,
  type ContextMenuEntry,
} from "../ui";
import {
  useConversationRuntime,
  type ConversationActions,
} from "./ConversationContext";

/** Window event the shell listens for to fold or show the sidebar (same as Ctrl+B). */
export const TOGGLE_SIDEBAR_EVENT = "maya:toggle-sidebar";

export const TITLE_MAX = 120;

type SortMode = "recent" | "az";

/** Pure: pinned first, then the rest, then archived; A-Z when asked. */
export function groupSessions(
  list: readonly SessionSummary[],
  query: string,
  sort: SortMode,
): {
  readonly pinned: SessionSummary[];
  readonly recent: SessionSummary[];
  readonly archived: SessionSummary[];
} {
  const q = query.trim().toLowerCase();
  const visible =
    q === ""
      ? [...list]
      : list.filter((s) => s.title.toLowerCase().includes(q));
  const order = (items: SessionSummary[]): SessionSummary[] =>
    sort === "az"
      ? [...items].sort((a, b) => a.title.localeCompare(b.title))
      : items;
  return {
    pinned: order(
      visible.filter((s) => s.pinned === true && s.archived !== true),
    ),
    recent: order(
      visible.filter((s) => s.pinned !== true && s.archived !== true),
    ),
    archived: order(visible.filter((s) => s.archived === true)),
  };
}

function RenameField({
  initial,
  onSave,
  onCancel,
}: {
  readonly initial: string;
  readonly onSave: (title: string) => void;
  readonly onCancel: () => void;
}): React.JSX.Element {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const trimmed = value.trim();
  const valid =
    Array.from(trimmed).length >= 1 && Array.from(trimmed).length <= TITLE_MAX;
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  return (
    <input
      ref={ref}
      type="text"
      value={value}
      aria-label="Conversation name"
      aria-invalid={!valid}
      maxLength={TITLE_MAX + 20}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          if (valid) onSave(trimmed);
        } else if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          onCancel();
        }
      }}
      onBlur={onCancel}
      className="h-8 min-w-0 flex-1 rounded-maya-sm border border-maya-control-border bg-maya-raised px-2 text-sm text-maya-text aria-[invalid=true]:border-maya-danger"
    />
  );
}

function ConversationRow({
  session,
  active,
  actions,
  editing,
  onEdit,
  onOpen,
  onDelete,
}: {
  readonly session: SessionSummary;
  readonly active: boolean;
  readonly actions: ConversationActions | undefined;
  readonly editing: boolean;
  readonly onEdit: (editing: boolean) => void;
  readonly onOpen: () => void;
  readonly onDelete: () => void;
}): React.JSX.Element {
  const hidden = actions?.unsupported ?? new Set<string>();
  const pinned = session.pinned === true;
  const archived = session.archived === true;
  const { menu, triggerProps, buttonProps } = useContextMenu({
    label: `Actions for ${session.title}`,
    getItems: () => {
      const items: ContextMenuEntry[] = [
        {
          id: "open",
          label: "Open",
          icon: <FolderOpen size={14} />,
          onSelect: onOpen,
        },
      ];
      if (actions !== undefined) {
        const edit: ContextMenuEntry[] = [];
        if (!hidden.has("rename"))
          edit.push({
            id: "rename",
            label: "Rename",
            icon: <Pencil size={14} />,
            shortcut: "F2",
            onSelect: () => onEdit(true),
          });
        if (!hidden.has("pin"))
          edit.push({
            id: "pin",
            label: pinned ? "Unpin" : "Pin",
            icon: pinned ? <PinOff size={14} /> : <Pin size={14} />,
            onSelect: () => void actions.setPinned(session.id, !pinned),
          });
        if (!hidden.has("archive"))
          edit.push({
            id: "archive",
            label: archived ? "Unarchive" : "Archive",
            icon: archived ? (
              <ArchiveRestore size={14} />
            ) : (
              <Archive size={14} />
            ),
            onSelect: () => void actions.setArchived(session.id, !archived),
          });
        if (!hidden.has("duplicate"))
          edit.push({
            id: "duplicate",
            label: "Duplicate (branch)",
            icon: <CopyPlus size={14} />,
            onSelect: () => void actions.duplicate(session.id),
          });
        if (!hidden.has("export"))
          edit.push({
            id: "export",
            label: "Export as Markdown",
            icon: <Download size={14} />,
            onSelect: () =>
              void actions.exportMarkdown(session.id, session.title),
          });
        items.push({ kind: "separator" }, ...edit);
      }
      items.push(
        {
          id: "copy-title",
          label: "Copy title",
          icon: <Copy size={14} />,
          onSelect: () => void copyText(session.title),
        },
        { kind: "separator" },
        {
          id: "delete",
          label: "Delete",
          icon: <Trash2 size={14} />,
          danger: true,
          onSelect: onDelete,
        },
      );
      return items;
    },
  });

  return (
    <li
      className="maya-reveal group flex min-w-0 items-center gap-1"
      {...triggerProps}
      onKeyDown={(e) => {
        if (e.key === "F2" && actions !== undefined && !hidden.has("rename")) {
          e.preventDefault();
          onEdit(true);
          return;
        }
        triggerProps.onKeyDown(e);
      }}
    >
      {editing ? (
        <RenameField
          initial={session.title}
          onCancel={() => onEdit(false)}
          onSave={(title) => {
            onEdit(false);
            if (title !== session.title && actions !== undefined)
              void actions.rename(session.id, title);
          }}
        />
      ) : (
        <button
          type="button"
          dir="auto"
          aria-current={active ? "true" : undefined}
          title={session.title}
          onClick={onOpen}
          className={`maya-row motion-interactive motion-press h-8 min-w-0 flex-1 truncate rounded-maya-md px-2 text-start text-sm ${
            active
              ? "bg-maya-hover font-medium text-maya-text"
              : "text-maya-muted hover:bg-maya-hover hover:text-maya-text"
          }`}
        >
          {session.title}
        </button>
      )}
      <div data-reveal className="flex shrink-0 items-center gap-0.5">
        <MenuButton
          label={`More actions for ${session.title}`}
          buttonProps={buttonProps}
        >
          <MoreHorizontal size={14} aria-hidden="true" />
        </MenuButton>
        <button
          type="button"
          aria-label={`Delete conversation ${session.title}`}
          onClick={onDelete}
          className="motion-interactive motion-press inline-flex size-8 shrink-0 items-center justify-center rounded-maya-sm text-maya-muted hover:bg-maya-hover hover:text-maya-danger"
        >
          <Trash2 size={14} aria-hidden="true" />
        </button>
      </div>
      {menu}
    </li>
  );
}

/** Sidebar conversation list: search, sections, open, rename, pin, archive, delete. */
export function ConversationSidebar(): React.JSX.Element {
  const runtime = useConversationRuntime();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortMode>("recent");
  const [archivedOpen, setArchivedOpen] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{
    readonly id: string;
    readonly title: string;
  } | null>(null);
  const [alsoContent, setAlsoContent] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const { list, loading, error, reload } = runtime.sessions;
  // Before the first load has finished the list is unknown, not empty.
  const loaded = runtime.sessions.loaded ?? !loading;
  const currentId = runtime.chat.sessionId;
  const showArchived = runtime.sessions.showArchived ?? false;
  const actions = runtime.conversationActions;

  const groups = useMemo(
    () => groupSessions(list, query, sort),
    [list, query, sort],
  );
  const matches =
    groups.pinned.length + groups.recent.length + groups.archived.length;

  const open = (id: string): void => {
    void runtime.openConversation(id);
    router.push("/");
  };
  const askDelete = (s: SessionSummary): void => {
    setAlsoContent(false);
    setPendingDelete({ id: s.id, title: s.title });
  };

  const blank = useContextMenu({
    label: "Conversation list actions",
    getItems: () => {
      const items: ContextMenuEntry[] = [
        {
          id: "new",
          label: "New conversation",
          icon: <Plus size={14} />,
          onSelect: () => {
            runtime.newConversation();
            router.push("/");
          },
        },
      ];
      if (runtime.sessions.setShowArchived !== undefined) {
        items.push({
          id: "archived",
          label: showArchived ? "Hide archived" : "Show archived",
          icon: showArchived ? <EyeOff size={14} /> : <Eye size={14} />,
          onSelect: () => runtime.sessions.setShowArchived?.(!showArchived),
        });
      }
      items.push(
        {
          id: "sort",
          label: "Sort by",
          icon: sort === "az" ? <ArrowDownAZ size={14} /> : <Clock size={14} />,
          children: [
            {
              id: "sort-recent",
              label: "Recent",
              checked: sort === "recent",
              onSelect: () => setSort("recent"),
            },
            {
              id: "sort-az",
              label: "A to Z",
              checked: sort === "az",
              onSelect: () => setSort("az"),
            },
          ],
        },
        { kind: "separator" },
        {
          id: "collapse",
          label: "Collapse sidebar",
          icon: <PanelLeftClose size={14} />,
          shortcut: "Ctrl+B",
          onSelect: () =>
            window.dispatchEvent(new CustomEvent(TOGGLE_SIDEBAR_EVENT)),
        },
      );
      return items;
    },
  });

  const renderRows = (items: readonly SessionSummary[], label: string) => (
    <ul className="flex min-w-0 flex-col gap-0.5" aria-label={label}>
      {items.map((session) => (
        <ConversationRow
          key={session.id}
          session={session}
          active={session.id === currentId}
          actions={actions}
          editing={editingId === session.id}
          onEdit={(on) => setEditingId(on ? session.id : null)}
          onOpen={() => open(session.id)}
          onDelete={() => askDelete(session)}
        />
      ))}
    </ul>
  );

  return (
    <div
      className="flex min-h-full flex-col gap-2 py-1"
      data-testid="conversation-list"
      {...blank.triggerProps}
    >
      <label className="relative block">
        <span className="maya-sr-only">Search conversations</span>
        <Search
          size={14}
          aria-hidden="true"
          className="pointer-events-none absolute left-2 top-2.5 text-maya-muted"
        />
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search conversations"
          className="h-8 w-full rounded-maya-sm border border-maya-control-border bg-maya-raised pl-7 pr-2 text-sm text-maya-text"
        />
      </label>

      {(loading || !loaded) && error === null && list.length === 0 && (
        <p className="px-1 text-sm text-maya-muted" role="status">
          Loading conversations
        </p>
      )}
      {error !== null && (
        <p className="px-1 text-sm text-maya-danger" role="alert">
          {error}{" "}
          <button
            type="button"
            className="underline"
            onClick={() => void reload()}
          >
            Retry
          </button>
        </p>
      )}
      {loaded && !loading && error === null && list.length === 0 && (
        <p className="px-1 text-sm text-maya-muted">No conversations yet.</p>
      )}
      {list.length > 0 && matches === 0 && (
        <p className="px-1 text-sm text-maya-muted">No conversation matches.</p>
      )}

      {groups.pinned.length > 0 && (
        <section
          aria-label="Pinned conversations"
          className="grid grid-cols-[minmax(0,1fr)] gap-0.5"
        >
          <h3 className="m-0 flex items-center gap-1 px-2 text-maya-caption font-medium text-maya-muted">
            <Pin size={12} aria-hidden="true" />
            Pinned
          </h3>
          {renderRows(groups.pinned, "Pinned conversations")}
        </section>
      )}
      {groups.recent.length > 0 && (
        <section
          aria-label="Recent conversations"
          className="grid grid-cols-[minmax(0,1fr)] gap-0.5"
        >
          {groups.pinned.length > 0 && (
            <h3 className="m-0 px-2 text-maya-caption font-medium text-maya-muted">
              Recent
            </h3>
          )}
          {renderRows(
            groups.recent,
            groups.pinned.length > 0
              ? "Recent conversations"
              : "Conversation history",
          )}
        </section>
      )}
      {showArchived && groups.archived.length > 0 && (
        <section
          aria-label="Archived conversations"
          className="grid grid-cols-[minmax(0,1fr)] gap-0.5"
        >
          <h3 className="m-0">
            <button
              type="button"
              aria-expanded={archivedOpen}
              onClick={() => setArchivedOpen((v) => !v)}
              className="flex w-full items-center gap-1 rounded-maya-sm px-2 py-1 text-left text-maya-caption font-medium text-maya-muted hover:bg-maya-hover"
            >
              {archivedOpen ? (
                <ChevronDown size={12} aria-hidden="true" />
              ) : (
                <ChevronRight size={12} aria-hidden="true" />
              )}
              Archived ({groups.archived.length})
            </button>
          </h3>
          {archivedOpen &&
            renderRows(groups.archived, "Archived conversations")}
        </section>
      )}

      {blank.menu}

      {pendingDelete !== null && (
        <ConfirmDeleteDialog
          title="Delete this conversation?"
          removes={[
            `The messages of "${pendingDelete.title}".`,
            ...(alsoContent
              ? [
                  "Finished tasks and the files MAYA stored for this conversation.",
                ]
              : []),
          ]}
          keeps={
            alsoContent
              ? [
                  "Tasks that are still running.",
                  "Copies saved in your working folder.",
                  "The audit log.",
                ]
              : [
                  "Its tasks and files stay in Tasks and Artifacts.",
                  "Copies saved in your working folder.",
                  "The audit log.",
                ]
          }
          confirmLabel="Delete conversation"
          busy={deleting}
          onClose={() => setPendingDelete(null)}
          onConfirm={() => {
            setDeleting(true);
            void runtime
              .deleteConversation(pendingDelete.id, alsoContent)
              .finally(() => {
                setDeleting(false);
                setPendingDelete(null);
              });
          }}
        >
          <label className="flex min-w-0 items-start gap-2 text-maya-small text-maya-text">
            <input
              type="checkbox"
              checked={alsoContent}
              onChange={(e) => setAlsoContent(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              Also delete files and tasks created in this conversation
            </span>
          </label>
        </ConfirmDeleteDialog>
      )}
    </div>
  );
}
