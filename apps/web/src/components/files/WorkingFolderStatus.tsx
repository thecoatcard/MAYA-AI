"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";

import { getFolder, type FolderInfo } from "../../lib/files-client";

/**
 * One small line in the chat composer that says whether a working folder is connected. It reads
 * the same connected-folder API as Settings > Files and shows nothing when that read fails, so
 * a failed status never blocks the composer.
 */
export function WorkingFolderStatus(): React.JSX.Element | null {
  const [folder, setFolder] = useState<FolderInfo | null>(null);

  useEffect(() => {
    let live = true;
    const refresh = (): void => {
      getFolder()
        .then((value) => {
          if (live) setFolder(value);
        })
        .catch(() => undefined);
    };

    refresh();

    const onWorkspaceChanged = (): void => {
      refresh();
    };

    window.addEventListener("maya:workspace-changed", onWorkspaceChanged);
    return () => {
      live = false;
      window.removeEventListener("maya:workspace-changed", onWorkspaceChanged);
    };
  }, []);

  if (folder === null) return null;

  let text: string;
  switch (folder.status) {
    case "ok":
      text = `Working folder: ${folder.name ?? folder.path ?? "connected"}. Files and task outputs go here.`;
      break;
    case "missing":
      text = "Working folder is missing. Reconnect it in Settings.";
      break;
    case "not_writable":
      text = "Working folder is not writable. Check it in Settings.";
      break;
    default:
      text = "No working folder. Task outputs stay in MAYA storage.";
  }

  return (
    <p
      className="m-0 px-1 text-maya-caption text-maya-muted"
      data-testid="working-folder-status"
      data-status={folder.status}
    >
      {text}{" "}
      <Link
        href="/settings#settings-files"
        className="text-maya-link underline"
      >
        {folder.status === "ok" ? "Manage" : "Settings > Files"}
      </Link>
    </p>
  );
}
