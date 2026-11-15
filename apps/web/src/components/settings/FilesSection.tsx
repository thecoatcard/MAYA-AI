"use client";

import {
  CheckCircle2,
  Download,
  FolderOpen,
  Lock,
  Paperclip,
  TriangleAlert,
  Trash2,
  Unplug,
} from "lucide-react";
import Link from "next/link";
import React, {
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import { getFolderPicker, getPathRevealer } from "../../lib/desktop-bridge";
import {
  clearFolder,
  deleteUpload,
  describeFilesError,
  downloadUpload,
  formatBytes,
  getFolder,
  listUploads,
  setFolder,
  type FolderInfo,
  type UploadedFile,
} from "../../lib/files-client";
import { ConversationContext } from "../chat/ConversationContext";
import { FileKindIcon } from "../files/FileParts";
import {
  Badge,
  Button,
  ErrorState,
  IconButton,
  Notice,
  Skeleton,
  Truncate,
  WrapText,
} from "../ui";

const PAGE = 50;

const STATUS_BADGE = {
  ok: { tone: "success", icon: CheckCircle2, text: "Connected" },
  missing: { tone: "warning", icon: TriangleAlert, text: "Folder missing" },
  not_writable: { tone: "warning", icon: Lock, text: "Not writable" },
} as const;

const STATUS_HELP: Record<"missing" | "not_writable", string> = {
  missing:
    "This folder cannot be found any more. It may have been moved, renamed or deleted. Choose it again or connect another folder; until then task outputs stay in MAYA's storage.",
  not_writable:
    "MAYA cannot write to this folder. Check its permissions or choose another folder; until then task outputs stay in MAYA's storage.",
};

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString();
}

function WorkingFolder(): React.JSX.Element {
  const [folder, setFolderState] = useState<FolderInfo | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState<"connect" | "disconnect" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const fieldId = useId();
  const mounted = useRef(true);
  // Read on the client only, after mount: a browser has no bridge.
  const [bridge, setBridge] = useState<{
    pick: (() => Promise<string | null>) | null;
    reveal: ((path: string) => Promise<boolean>) | null;
  }>({ pick: null, reveal: null });

  useEffect(() => {
    mounted.current = true;
    setBridge({ pick: getFolderPicker(), reveal: getPathRevealer() });
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(() => {
    setLoadFailed(false);
    getFolder()
      .then((value) => {
        if (mounted.current) setFolderState(value);
      })
      .catch(() => {
        if (mounted.current) setLoadFailed(true);
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const connect = async (path: string): Promise<void> => {
    setBusy("connect");
    setError(null);
    try {
      const saved = await setFolder(path);
      if (!mounted.current) return;
      setFolderState(saved);
      setTyped("");
    } catch (e) {
      if (mounted.current) setError(describeFilesError(e));
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const choose = async (): Promise<void> => {
    if (bridge.pick === null) return;
    setError(null);
    let picked: string | null;
    try {
      picked = await bridge.pick();
    } catch {
      if (mounted.current) {
        setError("The folder dialog could not be opened. Try again.");
      }
      return;
    }
    if (picked !== null && mounted.current) await connect(picked);
  };

  const disconnect = async (): Promise<void> => {
    setBusy("disconnect");
    setError(null);
    try {
      await clearFolder();
      if (!mounted.current) return;
      setFolderState({
        path: null,
        status: "none",
        writable: false,
        name: null,
      });
    } catch (e) {
      if (mounted.current) setError(describeFilesError(e));
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const reveal = async (): Promise<void> => {
    if (bridge.reveal === null || folder?.path == null) return;
    setError(null);
    const shown = await bridge.reveal(folder.path).catch(() => false);
    if (!shown && mounted.current) {
      setError(
        "The folder could not be opened here. Choose the folder again with Choose folder, then try Open folder.",
      );
    }
  };

  const connected = folder !== null && folder.status !== "none";
  const badge =
    folder !== null && folder.status !== "none"
      ? STATUS_BADGE[folder.status]
      : null;

  return (
    <div className="grid min-w-0 gap-3 py-3">
      <div className="min-w-0">
        <p className="m-0 text-maya-body font-medium">Working folder</p>
        <p className="m-0 mt-1 text-maya-small text-maya-secondary">
          MAYA creates and edits files only inside this folder, and existing
          files are never overwritten without your approval. Task outputs are
          saved here; with no folder connected they stay in MAYA&apos;s storage.
        </p>
      </div>

      {loadFailed ? (
        <ErrorState
          title="The working folder could not be loaded"
          message="Nothing was changed."
          onRetry={load}
        />
      ) : folder === null ? (
        <div aria-busy="true">
          <Skeleton lines={2} />
        </div>
      ) : (
        <>
          {connected && badge !== null && (
            <div
              className="grid min-w-0 gap-2 rounded-maya-lg border border-maya-border bg-maya-raised p-3"
              data-testid="folder-card"
              data-status={folder.status}
            >
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
                <Truncate
                  as="p"
                  title={folder.name ?? undefined}
                  className="m-0 min-w-0 flex-1 basis-40 text-maya-body font-medium"
                >
                  {folder.name ?? "Connected folder"}
                </Truncate>
                <Badge tone={badge.tone} icon={badge.icon}>
                  {badge.text}
                </Badge>
              </div>
              {folder.path !== null && (
                <Truncate
                  as="p"
                  title={folder.path}
                  dir="auto"
                  className="m-0 font-mono text-maya-mono text-maya-secondary"
                >
                  {folder.path}
                </Truncate>
              )}
              {(folder.status === "missing" ||
                folder.status === "not_writable") && (
                <p className="m-0 text-maya-small text-maya-text [overflow-wrap:anywhere]">
                  {STATUS_HELP[folder.status]}
                </p>
              )}
            </div>
          )}
          {!connected && (
            <p
              className="m-0 text-maya-small text-maya-muted"
              data-testid="folder-none"
            >
              No folder is connected.
            </p>
          )}

          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {bridge.pick !== null && (
              <Button
                variant={connected ? "secondary" : "primary"}
                leftIcon={<FolderOpen size={16} aria-hidden="true" />}
                loading={busy === "connect"}
                onClick={() => void choose()}
              >
                Choose folder...
              </Button>
            )}
            {connected && bridge.reveal !== null && folder.path !== null && (
              <Button
                variant="secondary"
                leftIcon={<FolderOpen size={16} aria-hidden="true" />}
                onClick={() => void reveal()}
              >
                Open folder
              </Button>
            )}
            {connected && (
              <Button
                variant="secondary"
                leftIcon={<Unplug size={16} aria-hidden="true" />}
                loading={busy === "disconnect"}
                onClick={() => void disconnect()}
              >
                Disconnect
              </Button>
            )}
          </div>

          {bridge.pick === null && (
            <form
              className="grid min-w-0 gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (typed.trim() !== "") void connect(typed.trim());
              }}
            >
              <label
                htmlFor={fieldId}
                className="text-maya-caption text-maya-muted"
              >
                Folder path on the computer that runs MAYA
              </label>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <input
                  id={fieldId}
                  type="text"
                  dir="auto"
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                  placeholder="C:\Users\you\Documents\MAYA"
                  autoComplete="off"
                  spellCheck={false}
                  className="min-h-9 min-w-0 flex-1 basis-64 rounded-maya-md border border-maya-control-border bg-maya-pane px-3 py-2 font-mono text-maya-mono text-maya-text placeholder:text-maya-muted hover:border-maya-border-strong focus:border-maya-focus"
                />
                <Button
                  type="submit"
                  variant="primary"
                  loading={busy === "connect"}
                  disabled={typed.trim() === ""}
                >
                  Connect
                </Button>
              </div>
              <p className="m-0 text-maya-caption text-maya-muted">
                The desktop app opens a folder dialog instead. In a browser,
                type the full path.
              </p>
            </form>
          )}
        </>
      )}

      {error !== null && (
        <Notice tone="danger" onDismiss={() => setError(null)}>
          {error}
        </Notice>
      )}
    </div>
  );
}

function UploadedFiles(): React.JSX.Element {
  const conversation = useContext(ConversationContext);
  const attachments = conversation?.attachments;
  const [items, setItems] = useState<readonly UploadedFile[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [attachedId, setAttachedId] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(() => {
    setLoadFailed(false);
    listUploads({ limit: PAGE })
      .then((page) => {
        if (!mounted.current) return;
        setItems(page.items);
        setCursor(page.next_cursor);
      })
      .catch(() => {
        if (mounted.current) setLoadFailed(true);
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const more = async (): Promise<void> => {
    if (cursor === null) return;
    setLoadingMore(true);
    try {
      const page = await listUploads({ limit: PAGE, cursor });
      if (!mounted.current) return;
      setItems((prev) => [...(prev ?? []), ...page.items]);
      setCursor(page.next_cursor);
    } catch (e) {
      if (mounted.current) setRowError(describeFilesError(e));
    } finally {
      if (mounted.current) setLoadingMore(false);
    }
  };

  const remove = async (file: UploadedFile): Promise<void> => {
    setRowError(null);
    try {
      await deleteUpload(file.id);
      if (!mounted.current) return;
      setItems((prev) => (prev ?? []).filter((i) => i.id !== file.id));
      setConfirmId(null);
    } catch (e) {
      if (mounted.current) setRowError(describeFilesError(e));
    }
  };

  const save = async (file: UploadedFile): Promise<void> => {
    setRowError(null);
    setSavingId(file.id);
    try {
      await downloadUpload(file.id, file.name);
    } catch (e) {
      if (mounted.current) setRowError(describeFilesError(e));
    } finally {
      if (mounted.current) setSavingId(null);
    }
  };

  return (
    <div className="grid min-w-0 gap-3 py-3">
      <div className="min-w-0">
        <p className="m-0 text-maya-body font-medium">Uploaded files</p>
        <p className="m-0 mt-1 text-maya-small text-maya-secondary">
          Files you attached in chat. MAYA reads them when you send a message
          that includes them.
        </p>
      </div>

      {loadFailed ? (
        <ErrorState
          title="Uploaded files could not be loaded"
          message="Nothing was changed."
          onRetry={load}
        />
      ) : items === null ? (
        <div aria-busy="true">
          <Skeleton lines={3} />
        </div>
      ) : items.length === 0 ? (
        <p className="m-0 text-maya-small text-maya-muted">
          No uploaded files yet. Use the paperclip in the chat composer, or drag
          files onto the conversation.
        </p>
      ) : (
        <ul
          className="m-0 grid min-w-0 list-none divide-y divide-maya-border rounded-maya-lg overflow-hidden border border-maya-border p-0"
          aria-label="Uploaded files"
        >
          {items.map((file) => (
            <li
              key={file.id}
              className="grid min-w-0 gap-2 p-3"
              data-testid="upload-row"
            >
              <div className="flex min-w-0 items-center gap-2">
                <FileKindIcon kind={file.kind} />
                <Truncate
                  title={file.name}
                  dir="auto"
                  className="min-w-0 flex-1 text-maya-small font-medium"
                >
                  {file.name}
                </Truncate>
              </div>
              <p className="m-0 text-maya-caption text-maya-muted">
                {file.kind} · {formatBytes(file.size)}
                {formatDate(file.created_at) !== "" &&
                  ` · ${formatDate(file.created_at)}`}
              </p>
              {confirmId === file.id ? (
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="text-maya-small text-maya-text">
                    Delete this file?
                  </span>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => void remove(file)}
                  >
                    Delete
                  </Button>
                  <Button size="sm" onClick={() => setConfirmId(null)}>
                    Keep
                  </Button>
                </div>
              ) : (
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  {attachments !== undefined && (
                    <Button
                      size="sm"
                      leftIcon={<Paperclip size={14} aria-hidden="true" />}
                      aria-label={`Use ${file.name} in chat`}
                      onClick={() => {
                        attachments.addExisting(file);
                        setAttachedId(file.id);
                      }}
                    >
                      Use in chat
                    </Button>
                  )}
                  <IconButton
                    size="sm"
                    variant="secondary"
                    label={`Download ${file.name}`}
                    icon={<Download size={14} />}
                    loading={savingId === file.id}
                    onClick={() => void save(file)}
                  />
                  <IconButton
                    size="sm"
                    variant="secondary"
                    label={`Delete ${file.name}`}
                    icon={<Trash2 size={14} />}
                    onClick={() => setConfirmId(file.id)}
                  />
                </div>
              )}
              {attachedId === file.id && (
                <p role="status" className="m-0 text-maya-small text-maya-text">
                  Attached to your next message.{" "}
                  <Link href="/chat" className="text-maya-link underline">
                    Go to the conversation
                  </Link>
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {cursor !== null && (
        <div>
          <Button loading={loadingMore} onClick={() => void more()}>
            Show more
          </Button>
        </div>
      )}
      {rowError !== null && (
        <Notice tone="danger" onDismiss={() => setRowError(null)}>
          <WrapText>{rowError}</WrapText>
        </Notice>
      )}
    </div>
  );
}

/** Content of the Settings "Files" section: the working folder and the uploaded files. */
export function FilesSection(): React.JSX.Element {
  return (
    <>
      <WorkingFolder />
      <UploadedFiles />
    </>
  );
}
