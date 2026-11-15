"use client";

import { Download, RotateCcw } from "lucide-react";
import React, { useState } from "react";

import { downloadArtifact } from "../../lib/artifacts-client";
import { Button } from "../ui";

export interface ArtifactDownloadButtonProps {
  readonly artifactId: string;
  readonly fileName: string;
  /** Used as the extension when the name has none. */
  readonly fileType?: string;
  readonly label?: string;
  readonly className?: string;
}

/**
 * Authenticated download (bearer token, never a bare link) with a loading
 * state, and a plain error plus a retry when it fails.
 */
export function ArtifactDownloadButton({
  artifactId,
  fileName,
  fileType,
  label = "Download",
  className = "",
}: ArtifactDownloadButtonProps): React.JSX.Element {
  const [busy, setBusy] = useState<boolean>(false);
  const [failed, setFailed] = useState<boolean>(false);

  const run = async (): Promise<void> => {
    setBusy(true);
    setFailed(false);
    try {
      await downloadArtifact(artifactId, fileName, fileType);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="inline-flex min-w-0 max-w-full flex-col items-end gap-1">
      <Button
        variant="secondary"
        size="sm"
        loading={busy}
        aria-busy={busy}
        className={className}
        leftIcon={failed ? <RotateCcw size={14} /> : <Download size={14} />}
        onClick={(e) => {
          e.stopPropagation();
          void run();
        }}
      >
        {busy ? "Downloading…" : failed ? "Retry download" : label}
      </Button>
      {failed && (
        <span
          role="alert"
          className="max-w-56 text-right text-maya-caption text-maya-danger [overflow-wrap:anywhere]"
        >
          Download failed. Check your connection and try again.
        </span>
      )}
    </span>
  );
}
