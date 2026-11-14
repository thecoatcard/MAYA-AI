"use client";

import {
  Download,
  File as FileIcon,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  Presentation,
  TriangleAlert,
  X,
} from "lucide-react";
import React, { useState } from "react";

import {
  describeFilesError,
  downloadUpload,
  formatBytes,
} from "../../lib/files-client";
import { IconButton, Truncate, cx } from "../ui";

/** Icon for an upload kind. The kind is always also written out in the chip's accessible name. */
export function FileKindIcon({
  kind,
  size = 16,
  className,
}: {
  readonly kind: string;
  readonly size?: number;
  readonly className?: string;
}): React.JSX.Element {
  const props = {
    "aria-hidden": true as const,
    size,
    className: cx("shrink-0 text-maya-secondary", className),
  };
  switch (kind) {
    case "code":
    case "json":
      return <FileCode {...props} />;
    case "csv":
    case "xlsx":
      return <FileSpreadsheet {...props} />;
    case "pptx":
      return <Presentation {...props} />;
    case "image":
      return <FileImage {...props} />;
    case "text":
    case "markdown":
    case "docx":
    case "pdf":
      return <FileText {...props} />;
    default:
      return <FileIcon {...props} />;
  }
}

export type AttachmentStatus = "uploading" | "ready" | "error" | "sent";

export interface AttachmentChipProps {
  readonly name: string;
  readonly kind: string;
  readonly size?: number;
  readonly status: AttachmentStatus;
  /** 0 to 100 while uploading. */
  readonly progress?: number;
  /** Plain reason, shown when status is "error". */
  readonly error?: string;
  readonly onRemove?: () => void;
  /** Upload id: when set and the status is ready or sent, a download action is offered. */
  readonly downloadId?: string;
}

const KIND_LABEL: Record<string, string> = {
  text: "text file",
  code: "code file",
  markdown: "Markdown file",
  csv: "CSV file",
  json: "JSON file",
  docx: "Word document",
  xlsx: "spreadsheet",
  pptx: "presentation",
  pdf: "PDF",
  image: "image",
};

/** One file chip: used in the composer, in sent messages and in the uploaded files list. */
export function AttachmentChip({
  name,
  kind,
  size,
  status,
  progress = 0,
  error,
  onRemove,
  downloadId,
}: AttachmentChipProps): React.JSX.Element {
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const failed = status === "error";
  const shownError = failed ? error : (saveError ?? undefined);
  const kindText = KIND_LABEL[kind] ?? "file";

  const save = async (): Promise<void> => {
    if (downloadId === undefined || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      await downloadUpload(downloadId, name);
    } catch (e) {
      setSaveError(describeFilesError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className={cx(
        "maya-attachment flex max-w-full min-w-0 flex-col gap-0.5 rounded-maya-md border bg-maya-raised px-2 py-1 text-maya-caption text-maya-text",
        failed ? "border-maya-danger" : "border-maya-border",
      )}
      data-status={status}
      data-testid="attachment-chip"
    >
      <div className="flex min-w-0 items-center gap-1.5">
        {failed ? (
          <TriangleAlert
            aria-hidden="true"
            size={16}
            className="shrink-0 text-maya-danger"
          />
        ) : (
          <FileKindIcon kind={kind} />
        )}
        <Truncate
          title={name}
          dir="auto"
          className="min-w-0 max-w-[14rem] font-medium"
        >
          {name}
        </Truncate>
        <span className="maya-sr-only">
          {`, ${kindText}${failed ? ", could not be attached" : status === "uploading" ? ", uploading" : ""}`}
        </span>
        {size !== undefined && (
          <span className="shrink-0 text-maya-muted">{formatBytes(size)}</span>
        )}
        {status === "uploading" && (
          <span className="shrink-0 text-maya-muted" aria-hidden="true">
            {Math.round(progress)}%
          </span>
        )}
        {downloadId !== undefined && !failed && (
          <IconButton
            size="sm"
            label={`Download ${name}`}
            icon={<Download size={14} />}
            loading={saving}
            onClick={() => void save()}
            className="-my-1 shrink-0"
          />
        )}
        {onRemove !== undefined && (
          <IconButton
            size="sm"
            label={`Remove ${name}`}
            icon={<X size={14} />}
            onClick={onRemove}
            className="-my-1 shrink-0"
          />
        )}
      </div>
      {status === "uploading" && (
        <progress
          className="maya-attachment__bar"
          max={100}
          value={Math.round(progress)}
          aria-label={`Uploading ${name}`}
        />
      )}
      {shownError !== undefined && (
        <p className="m-0 text-maya-caption text-maya-danger [overflow-wrap:anywhere]">
          {shownError}
        </p>
      )}
    </div>
  );
}
