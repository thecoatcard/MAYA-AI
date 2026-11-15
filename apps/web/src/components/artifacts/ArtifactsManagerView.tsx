"use client";

import {
  Code,
  Copy,
  Download,
  FileDown,
  Mail,
  Save,
  ExternalLink,
  FileCheck,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  MessageSquare,
  Presentation,
  RefreshCw,
  Search,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import React, { useCallback, useEffect, useMemo, useState } from "react";

import {
  artifactToFile,
  downloadArtifact,
  canOpenNatively,
  deleteArtifact,
  deleteArtifacts,
  listArtifacts,
  openArtifactNatively,
  type ArtifactMetadata,
} from "../../lib/artifacts-client";
import { useConversationRuntime } from "../chat/ConversationContext";
import { useOptionalWorkspace } from "../layout/WorkspaceProvider";
import {
  Button,
  MenuSurface,
  type ContextMenuEntry,
  ConfirmDeleteDialog,
  EmptyState,
  ErrorState,
  GlassInput,
  Pane,
  SelectionBar,
  Skeleton,
  Truncate,
} from "../ui";
import { copyText } from "../../lib/menu-actions";
import { ArtifactDownloadButton } from "./ArtifactDownloadButton";

/** The server returns the whole list; rows are shown this many at a time. */
const PAGE_SIZE = 50;

const TYPE_FILTERS: readonly { readonly id: string; readonly label: string }[] =
  [
    { id: "all", label: "All" },
    { id: "pdf", label: "PDF" },
    { id: "xls", label: "Spreadsheets" },
    { id: "ppt", label: "Presentations" },
    { id: "docx", label: "Documents" },
    { id: "zip", label: "Zip files" },
    { id: "patch", label: "Code patches" },
  ];

function formatBytes(bytes?: number): string {
  if (!bytes || bytes <= 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(k)),
    sizes.length - 1,
  );
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function fileIcon(type: string): LucideIcon {
  const t = type.toLowerCase();
  if (t.includes("pdf")) return FileText;
  if (t.includes("xls") || t.includes("csv") || t.includes("sheet"))
    return FileSpreadsheet;
  if (t.includes("ppt") || t.includes("presentation")) return Presentation;
  if (t.includes("diff") || t.includes("patch") || t.includes("code"))
    return Code;
  return FileCheck;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

const OPEN_FAILED: Readonly<Record<string, string>> = {
  "blocked-type":
    "This kind of file is not opened from MAYA for safety. Download it instead.",
  "download-failed": "The file could not be fetched to open it. Try again.",
};

/**
 * The artifact list. No report or preview is rendered here: each row has its facts (name, type,
 * size, date) and its actions: Open in the PC's own app, Ask AI, Download and Delete.
 */
export function ArtifactsManagerView(): React.JSX.Element {
  const router = useRouter();
  const runtime = useConversationRuntime();
  const addFiles = runtime.attachments?.addFiles;

  const [artifacts, setArtifacts] = useState<ArtifactMetadata[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [visible, setVisible] = useState<number>(PAGE_SIZE);
  const [native, setNative] = useState<boolean>(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowNote, setRowNote] = useState<{
    readonly id: string;
    readonly text: string;
  } | null>(null);
  const notify = useOptionalWorkspace()?.notify;
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [confirmDelete, setConfirmDelete] = useState<
    readonly ArtifactMetadata[] | null
  >(null);
  const [deleting, setDeleting] = useState<boolean>(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // The desktop bridge exists only in the window of the desktop app.
  useEffect(() => {
    setNative(canOpenNatively());
  }, []);

  const loadArtifacts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listArtifacts();
      setArtifacts(data || []);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : "Failed to load deliverables.",
      );
      setArtifacts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadArtifacts();
  }, [loadArtifacts]);

  useEffect(() => {
    setVisible(PAGE_SIZE);
  }, [typeFilter, searchQuery]);

  const filteredArtifacts = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return artifacts.filter((a) => {
      if (
        typeFilter !== "all" &&
        !a.file_type.toLowerCase().includes(typeFilter)
      ) {
        return false;
      }
      if (q === "") return true;
      return (
        a.file_name.toLowerCase().includes(q) ||
        a.task_id.toLowerCase().includes(q)
      );
    });
  }, [artifacts, typeFilter, searchQuery]);

  const shown = filteredArtifacts.slice(0, visible);

  const open = async (art: ArtifactMetadata): Promise<void> => {
    setBusyId(art.id);
    setRowNote(null);
    const result = await openArtifactNatively(art);
    setBusyId(null);
    if (!result.ok) {
      setRowNote({
        id: art.id,
        text:
          OPEN_FAILED[result.reason ?? ""] ??
          "The file could not be opened. Try Download instead.",
      });
    }
  };

  const sendAbout = (text: string): void => {
    void runtime.sendText(text);
    router.push("/");
  };
  const artifactItems = (
    art: ArtifactMetadata,
    inFolder: boolean,
  ): ContextMenuEntry[] => {
    const name = art.file_name;
    const sendReason = runtime.voiceLive
      ? "Not available during a live voice session."
      : undefined;
    const items: ContextMenuEntry[] = [];
    if (native) {
      items.push({
        id: "open",
        label: "Open",
        icon: <ExternalLink size={14} />,
        onSelect: () => void open(art),
      });
    }
    items.push({
      id: "download",
      label: "Download",
      icon: <Download size={14} />,
      onSelect: () =>
        void downloadArtifact(art.id, art.file_name, art.file_type).catch(() =>
          setRowNote({
            id: art.id,
            text: "The file could not be downloaded. Try again.",
          }),
        ),
    });
    // Reveal in folder needs a local path; the renderer never has one (only an artifact id), so it
    // is only offered for files MAYA says are in the connected folder AND when the bridge exists.
    items.push(
      {
        id: "ask",
        label: "Ask AI",
        icon: <MessageSquare size={14} />,
        disabled: addFiles === undefined,
        disabledReason: "Attaching files is not available here.",
        onSelect: () => void askAi(art),
      },
      { kind: "separator" },
      {
        id: "notion",
        label: "Save to Notion as it is",
        icon: <Save size={14} />,
        disabled: sendReason !== undefined,
        disabledReason: sendReason,
        onSelect: () =>
          sendAbout(`Save the file "${name}" to Notion as it is.`),
      },
      {
        id: "pdf",
        label: "Make a PDF from this",
        icon: <FileDown size={14} />,
        disabled: sendReason !== undefined,
        disabledReason: sendReason,
        onSelect: () => sendAbout(`Make a PDF from the file "${name}".`),
      },
      {
        id: "email",
        label: "Email it",
        icon: <Mail size={14} />,
        disabled: sendReason !== undefined,
        disabledReason: sendReason,
        onSelect: () => sendAbout(`Email the file "${name}".`),
      },
      { kind: "separator" },
      {
        id: "copy-name",
        label: "Copy name",
        icon: <Copy size={14} />,
        onSelect: () => void copyText(name),
      },
      {
        id: "delete",
        label: "Delete",
        icon: <Trash2 size={14} />,
        danger: true,
        disabled: inFolder,
        disabledReason: "MAYA never deletes files in your connected folder.",
        onSelect: () => {
          setDeleteError(null);
          setConfirmDelete([art]);
        },
      },
    );
    return items;
  };

  const askAi = async (art: ArtifactMetadata): Promise<void> => {
    if (addFiles === undefined) return;
    setBusyId(art.id);
    setRowNote(null);
    try {
      addFiles([await artifactToFile(art)]);
      router.push("/");
    } catch {
      setRowNote({
        id: art.id,
        text: "The file could not be attached to a chat. Try again.",
      });
    } finally {
      setBusyId(null);
    }
  };

  const toggle = (id: string): void =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const remove = async (): Promise<void> => {
    if (confirmDelete === null) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const ids = confirmDelete.map((a) => a.id);
      if (ids.length === 1) await deleteArtifact(ids[0] as string);
      else await deleteArtifacts(ids);
      const gone = new Set(ids);
      setArtifacts((prev) => prev.filter((a) => !gone.has(a.id)));
      setSelected((prev) => new Set([...prev].filter((id) => !gone.has(id))));
      setConfirmDelete(null);
      notify?.({
        tone: "success",
        message:
          ids.length === 1 ? "File deleted." : `${ids.length} files deleted.`,
      });
    } catch {
      setDeleteError("The file could not be deleted. Try again.");
    } finally {
      setDeleting(false);
    }
  };

  const countText = loading
    ? "Loading…"
    : filteredArtifacts.length === artifacts.length
      ? `${artifacts.length} ${artifacts.length === 1 ? "artifact" : "artifacts"}`
      : `${filteredArtifacts.length} of ${artifacts.length} artifacts`;

  const header = (
    <div className="grid min-w-0 gap-3 py-1">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 items-baseline gap-3">
          <h1 className="m-0 text-maya-heading font-medium">Artifacts</h1>
          <span
            className="text-maya-caption text-maya-muted"
            aria-live="polite"
          >
            {countText}
          </span>
        </div>
        <Button
          variant="secondary"
          size="sm"
          loading={loading}
          onClick={() => void loadArtifacts()}
          leftIcon={<RefreshCw size={14} />}
        >
          Refresh
        </Button>
      </div>

      <div
        role="group"
        aria-label="Filter by format"
        className="flex min-w-0 flex-wrap items-center gap-1.5"
      >
        {TYPE_FILTERS.map((tab) => (
          <Button
            key={tab.id}
            size="sm"
            variant={typeFilter === tab.id ? "primary" : "secondary"}
            aria-pressed={typeFilter === tab.id}
            onClick={() => setTypeFilter(tab.id)}
          >
            {tab.label}
          </Button>
        ))}
      </div>

      <div className="min-w-0 max-w-xl">
        <GlassInput
          placeholder="Search artifacts"
          aria-label="Search artifacts"
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
        aria-label="Artifacts"
        header={header}
        bodyLabel="Artifact list"
        bodyClassName="p-4"
      >
        <div className="min-w-0">
          {!native && (
            <p
              className="m-0 mb-3 text-maya-small text-maya-muted"
              data-testid="browser-note"
            >
              Opening a file in your PC&apos;s own app needs the MAYA desktop
              app. In a browser, use Download.
            </p>
          )}
          {error !== null && (
            <ErrorState
              className="mb-3"
              title="Could not load artifacts."
              message={error}
              retry={
                <Button
                  size="sm"
                  variant="secondary"
                  leftIcon={<RefreshCw size={14} />}
                  onClick={() => void loadArtifacts()}
                >
                  Retry
                </Button>
              }
            />
          )}

          {loading && artifacts.length === 0 && (
            <div aria-busy="true" className="grid gap-3">
              <p className="m-0 text-maya-small text-maya-muted">
                Loading artifacts…
              </p>
              <Skeleton lines={3} />
              <Skeleton lines={3} />
            </div>
          )}

          {!loading && error === null && artifacts.length === 0 && (
            <EmptyState
              icon={FileCheck}
              title="No deliverables produced yet."
              description="Files MAYA creates while completing a task will be listed here."
            />
          )}

          {!loading &&
            artifacts.length > 0 &&
            filteredArtifacts.length === 0 && (
              <EmptyState title="No deliverables found matching filter criteria." />
            )}

          {filteredArtifacts.length > 0 && (
            <div className="grid min-w-0 content-start gap-2">
              <ul className="m-0 grid min-w-0 list-none gap-2 p-0">
                {shown.map((art) => {
                  const Icon = fileIcon(art.file_type);
                  const inFolder = art.in_connected_folder === true;
                  const busy = busyId === art.id;
                  return (
                    <MenuSurface
                      as="li"
                      key={art.id}
                      label={art.file_name}
                      getItems={() => artifactItems(art, inFolder)}
                      data-testid="artifact-row"
                      className="grid min-w-0 shrink-0 gap-2 rounded-maya-lg border border-maya-border bg-maya-raised p-3"
                    >
                      <div className="flex min-w-0 items-start gap-3">
                        {!inFolder && (
                          <input
                            type="checkbox"
                            className="mt-1 size-4 shrink-0"
                            aria-label={`Select ${art.file_name}`}
                            checked={selected.has(art.id)}
                            onChange={() => toggle(art.id)}
                          />
                        )}
                        <Icon
                          aria-hidden="true"
                          size={20}
                          className="mt-0.5 shrink-0 text-maya-secondary"
                        />
                        <span className="grid min-w-0 flex-1 gap-0.5">
                          <Truncate
                            className="font-medium text-maya-text"
                            title={art.file_name}
                          >
                            {art.file_name}
                          </Truncate>
                          <span className="text-maya-caption text-maya-muted">
                            {art.file_type.toUpperCase()} ·{" "}
                            {formatBytes(art.size_bytes)} ·{" "}
                            {formatDate(art.created_at)}
                            {inFolder ? " · In your connected folder" : ""}
                          </span>
                        </span>
                      </div>
                      <div className="flex min-w-0 flex-wrap items-start gap-2">
                        {native ? (
                          <>
                            <Button
                              variant="secondary"
                              size="sm"
                              loading={busy}
                              leftIcon={<ExternalLink size={14} />}
                              onClick={() => void open(art)}
                            >
                              Open
                            </Button>
                            <ArtifactDownloadButton
                              artifactId={art.id}
                              fileName={art.file_name}
                              fileType={art.file_type}
                            />
                          </>
                        ) : (
                          <ArtifactDownloadButton
                            artifactId={art.id}
                            fileName={art.file_name}
                            fileType={art.file_type}
                            label="Download to open"
                          />
                        )}
                        <Button
                          variant="secondary"
                          size="sm"
                          disabled={busy || addFiles === undefined}
                          leftIcon={<MessageSquare size={14} />}
                          onClick={() => void askAi(art)}
                        >
                          Ask AI
                        </Button>
                        {inFolder ? (
                          <span
                            className="inline-flex min-h-8 items-center gap-1.5 text-maya-caption text-maya-muted"
                            title="MAYA never deletes files in your connected folder."
                          >
                            <FolderOpen size={14} aria-hidden="true" />
                            Stays on your disk
                          </span>
                        ) : (
                          <Button
                            variant="secondary"
                            size="sm"
                            leftIcon={<Trash2 size={14} />}
                            onClick={() => {
                              setDeleteError(null);
                              setConfirmDelete([art]);
                            }}
                          >
                            Delete
                          </Button>
                        )}
                      </div>
                      {rowNote?.id === art.id && (
                        <p
                          role="alert"
                          className="m-0 text-maya-small text-maya-danger [overflow-wrap:anywhere]"
                        >
                          {rowNote.text}
                        </p>
                      )}
                    </MenuSurface>
                  );
                })}
              </ul>
              <SelectionBar
                count={selected.size}
                noun="files"
                onClear={() => setSelected(new Set())}
                onDelete={() => {
                  setDeleteError(null);
                  setConfirmDelete(artifacts.filter((a) => selected.has(a.id)));
                }}
              />
              {filteredArtifacts.length > shown.length && (
                <div className="flex flex-col items-center gap-1">
                  <span className="text-maya-caption text-maya-muted">
                    Showing {shown.length} of {filteredArtifacts.length}
                  </span>
                  <Button
                    variant="secondary"
                    onClick={() => setVisible((v) => v + PAGE_SIZE)}
                  >
                    Show more
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </Pane>

      {confirmDelete !== null && (
        <ConfirmDeleteDialog
          title={
            confirmDelete.length === 1
              ? "Delete this file?"
              : `Delete ${confirmDelete.length} files?`
          }
          removes={[
            confirmDelete.length === 1
              ? `${(confirmDelete[0] as ArtifactMetadata).file_name}, from the list and from MAYA storage.`
              : `${confirmDelete.length} files, from the list and from MAYA storage.`,
          ]}
          keeps={[
            "A copy saved in your working folder is not deleted.",
            "The task that made the file.",
          ]}
          confirmLabel={
            confirmDelete.length === 1
              ? "Delete file"
              : `Delete ${confirmDelete.length} files`
          }
          busy={deleting}
          error={deleteError}
          onClose={() => setConfirmDelete(null)}
          onConfirm={() => void remove()}
        />
      )}
    </div>
  );
}
