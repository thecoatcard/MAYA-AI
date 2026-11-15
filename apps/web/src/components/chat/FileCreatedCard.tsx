import {
  Copy,
  Download,
  File as FileIcon,
  FileDown,
  ImageOff,
  Mail,
  MessageSquare,
  Save,
  Trash2,
} from "lucide-react";
import React, { useEffect, useState } from "react";

import type { ChatFileCard } from "../../hooks/useChatStream";
import { ApiError } from "../../lib/api-client";
import {
  deleteChatFile,
  describeFilesError,
  downloadChatFile,
  fetchChatFileBlob,
  formatBytes,
} from "../../lib/files-client";
import { ConfirmDeleteDialog, MenuSurface, Truncate } from "../ui";
import type { ContextMenuEntry } from "../ui";
import { copyText } from "../../lib/menu-actions";
import { useConversationRef } from "./ConversationContext";

/** Wait before the one automatic retry of a first 404 (the reply may still be being saved). */
export const FILE_RETRY_DELAY_MS = 1200;

function isGone(error: unknown): boolean {
  return error instanceof ApiError && error.status === 410;
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** Run `action`; on a first 404 wait briefly and try once more before giving up. */
async function withOneRetryOn404<T>(action: () => Promise<T>): Promise<T> {
  try {
    return await action();
  } catch (error) {
    if (!isNotFound(error)) throw error;
    await new Promise<void>((resolve) =>
      setTimeout(resolve, FILE_RETRY_DELAY_MS),
    );
    return action();
  }
}

/**
 * One file a reply created or edited: its real name, a type badge, its size and an authenticated
 * Download button. Images also get an inline preview, fetched through the same authenticated
 * route. Works the same live and after a reload (both read the same card data).
 */
export function FileCreatedCard({
  file,
}: {
  readonly file: ChatFileCard;
}): React.JSX.Element {
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // A 404 that survived the retry: the file is usually still being saved, so it is not an error.
  const [notReady, setNotReady] = useState(false);
  // The server says the file was deleted (410), or the user just deleted it here.
  const [deleted, setDeleted] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const isImage = file.mimeType.toLowerCase().startsWith("image/");
  const [preview, setPreview] = useState<string | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const runtimeRef = useConversationRef();
  const typeLabel =
    file.fileType.trim() === "" ? "FILE" : file.fileType.toUpperCase();

  // The preview is an object URL of the authenticated blob; it is revoked when the card goes away.
  useEffect(() => {
    if (!isImage) return undefined;
    let url: string | null = null;
    let live = true;
    withOneRetryOn404(() =>
      fetchChatFileBlob(file.sessionId, file.messageId, file.fileId),
    )
      .then((blob) => {
        if (!live) return;
        url = window.URL.createObjectURL(blob);
        setPreview(url);
      })
      .catch((error: unknown) => {
        if (!live) return;
        if (isGone(error)) setDeleted(true);
        else setPreviewFailed(true);
      });
    return () => {
      live = false;
      if (url !== null) window.URL.revokeObjectURL(url);
    };
  }, [isImage, file.sessionId, file.messageId, file.fileId]);

  const save = async (): Promise<void> => {
    if (saving) return;
    setSaving(true);
    setSaveError(null);
    setNotReady(false);
    try {
      await withOneRetryOn404(() =>
        downloadChatFile(
          file.sessionId,
          file.messageId,
          file.fileId,
          file.name,
          file.fileType,
        ),
      );
    } catch (error) {
      if (isGone(error)) setDeleted(true);
      else if (isNotFound(error)) setNotReady(true);
      else setSaveError(describeFilesError(error));
    } finally {
      setSaving(false);
    }
  };

  const remove = async (): Promise<void> => {
    setRemoving(true);
    setRemoveError(null);
    try {
      await deleteChatFile(file.sessionId, file.messageId, file.fileId);
      setDeleted(true);
      setConfirming(false);
    } catch {
      setRemoveError("The file could not be deleted. Try again.");
    } finally {
      setRemoving(false);
    }
  };

  if (deleted) {
    return (
      <li
        className="flex min-w-0 max-w-full items-center gap-2 rounded-maya-md border border-maya-border bg-maya-raised px-2.5 py-1.5"
        data-testid="chat-file-card-deleted"
      >
        <FileIcon
          aria-hidden="true"
          size={16}
          className="shrink-0 text-maya-muted"
        />
        <span className="min-w-0 text-maya-caption text-maya-muted [overflow-wrap:anywhere]">
          This file was deleted.
        </span>
      </li>
    );
  }

  const menuItems = (): ContextMenuEntry[] => {
    const rt = runtimeRef.current;
    const blocked = rt?.voiceLive === true;
    const reason = blocked
      ? "Not available during a live voice session."
      : undefined;
    const send = (text: string) => (): void => void rt?.sendText(text);
    const items: ContextMenuEntry[] = [
      {
        id: "download",
        label: "Download",
        icon: <Download size={14} />,
        onSelect: () => void save(),
      },
    ];
    if (rt !== null) {
      items.push(
        {
          id: "ask",
          label: "Ask AI",
          icon: <MessageSquare size={14} />,
          disabled: rt.prefillComposer === undefined,
          onSelect: () =>
            rt.prefillComposer?.(`About the file "${file.name}": `),
        },
        { kind: "separator" },
        {
          id: "notion",
          label: "Save to Notion as it is",
          icon: <Save size={14} />,
          disabled: blocked,
          disabledReason: reason,
          onSelect: send(`Save the file "${file.name}" to Notion as it is.`),
        },
        {
          id: "pdf",
          label: "Make a PDF from this",
          icon: <FileDown size={14} />,
          disabled: blocked,
          disabledReason: reason,
          onSelect: send(`Make a PDF from the file "${file.name}".`),
        },
        {
          id: "email",
          label: "Email it",
          icon: <Mail size={14} />,
          disabled: blocked,
          disabledReason: reason,
          onSelect: send(`Email the file "${file.name}".`),
        },
      );
    }
    items.push(
      { kind: "separator" },
      {
        id: "copy-name",
        label: "Copy name",
        icon: <Copy size={14} />,
        onSelect: () => void copyText(file.name),
      },
      {
        id: "delete",
        label: "Delete",
        icon: <Trash2 size={14} />,
        danger: true,
        onSelect: () => {
          setRemoveError(null);
          setConfirming(true);
        },
      },
    );
    return items;
  };

  return (
    <MenuSurface
      as="li"
      label={file.name}
      getItems={menuItems}
      className="group flex min-w-0 max-w-full flex-col gap-2 rounded-maya-lg border border-maya-border bg-maya-raised p-3 shadow-maya-1 transition-[border-color,box-shadow,transform] duration-150 ease-out hover:border-maya-border-strong hover:shadow-maya-2 motion-reduce:transition-none"
      data-testid="chat-file-card"
      data-action={file.action}
    >
      {isImage && (
        <div className="min-w-0 max-w-full overflow-hidden rounded-maya-md border border-maya-border">
          {preview !== null ? (
            // next/image cannot load a blob: object URL, so a plain img is correct here.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={preview}
              alt={`Image: ${file.name}`}
              className="block h-auto max-h-80 max-w-full rounded-maya-sm"
              data-testid="chat-image-preview"
            />
          ) : (
            <p className="m-0 flex items-center gap-1.5 p-2 text-maya-caption text-maya-muted">
              <ImageOff aria-hidden="true" size={14} className="shrink-0" />
              {previewFailed
                ? "Preview not available. Use Download."
                : "Loading preview"}
            </p>
          )}
        </div>
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-maya-md border border-maya-border bg-maya-pane text-maya-accent">
          <FileIcon aria-hidden="true" size={18} className="shrink-0" />
        </div>
        <span className="grid min-w-0 flex-1 basis-40">
          <Truncate
            title={file.name}
            dir="auto"
            className="text-maya-small font-medium text-maya-text"
          >
            {file.name}
          </Truncate>
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-maya-caption text-maya-muted">
            <span
              className="maya-chip font-mono uppercase"
              data-testid="file-type-badge"
            >
              {typeLabel}
            </span>
            <span>{formatBytes(file.sizeBytes)}</span>
            {file.action === "edited" && <span>Edited</span>}
          </span>
        </span>
        <button
          type="button"
          className="maya-msg__action shrink-0 rounded-maya-md transition-[background-color,transform] active:scale-95 motion-reduce:active:scale-100"
          onClick={() => void save()}
          disabled={saving}
          aria-label={`Download ${file.name}`}
          aria-describedby={
            saveError !== null ? `${file.fileId}-error` : undefined
          }
        >
          <Download aria-hidden="true" size={14} />
          <span>{saving ? "Downloading" : "Download"}</span>
        </button>
        <button
          type="button"
          className="maya-msg__action shrink-0 rounded-maya-md transition-[background-color,transform] active:scale-95 motion-reduce:active:scale-100"
          onClick={() => {
            setRemoveError(null);
            setConfirming(true);
          }}
          aria-label={`Delete ${file.name}`}
        >
          <Trash2 aria-hidden="true" size={14} />
          <span>Delete</span>
        </button>
      </div>
      {notReady && (
        <p
          role="status"
          className="m-0 flex w-full min-w-0 flex-wrap items-center gap-x-2 text-maya-caption text-maya-muted"
          data-testid="chat-file-not-ready"
        >
          <span>This file is not ready yet. Try again in a moment.</span>
          <button
            type="button"
            className="maya-msg__action shrink-0"
            onClick={() => void save()}
            disabled={saving}
          >
            Retry
          </button>
        </p>
      )}
      {saveError !== null && (
        <p
          id={`${file.fileId}-error`}
          role="alert"
          className="m-0 w-full min-w-0 text-maya-caption text-maya-danger [overflow-wrap:anywhere]"
        >
          {saveError}
        </p>
      )}
      {confirming && (
        <ConfirmDeleteDialog
          title="Delete this file?"
          removes={[`${file.name}, from MAYA storage.`]}
          keeps={["A copy saved in your working folder is not deleted."]}
          confirmLabel="Delete file"
          busy={removing}
          error={removeError}
          onClose={() => setConfirming(false)}
          onConfirm={() => void remove()}
        />
      )}
    </MenuSurface>
  );
}
