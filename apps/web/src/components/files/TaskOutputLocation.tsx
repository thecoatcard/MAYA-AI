"use client";

import { FolderOpen, HardDrive } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";

import type { ArtifactMetadata } from "../../lib/artifacts-client";
import { getPathRevealer } from "../../lib/desktop-bridge";
import { getFolder } from "../../lib/files-client";
import type { TaskDetailResponse } from "../../lib/tasks-client";
import { Button, WrapText } from "../ui";

/** "folder" or "artifacts" as recorded by the server on the task's steps, or null for older tasks. */
export function outputMode(
  task: Pick<TaskDetailResponse, "subtasks">,
): "folder" | "artifacts" | null {
  let mode: "folder" | "artifacts" | null = null;
  for (const subtask of task.subtasks ?? []) {
    const value = subtask.input_data?.output_dir_mode;
    if (value === "folder") return "folder";
    if (value === "artifacts") mode = "artifacts";
  }
  return mode;
}

/** The saved path of an artifact, when the server reports one (absolute paths only). */
function savedPath(artifact: ArtifactMetadata): string | null {
  const raw = artifact as unknown as Record<string, unknown>;
  for (const key of ["saved_path", "storage_path", "storage"]) {
    const value = raw[key];
    if (typeof value === "string" && /^([a-zA-Z]:[\\/]|\/)/.test(value)) {
      return value;
    }
  }
  return null;
}

/**
 * Where a finished task's files went. Shown only when the task has result files and the server
 * recorded where it was told to save them; never guessed.
 */
export function TaskOutputLocation({
  task,
  artifacts,
}: {
  readonly task: Pick<TaskDetailResponse, "subtasks">;
  readonly artifacts: readonly ArtifactMetadata[];
}): React.JSX.Element | null {
  const mode = outputMode(task);
  const [folderPath, setFolderPath] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [reveal, setReveal] = useState<
    ((path: string) => Promise<boolean>) | null
  >(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    setReveal(() => getPathRevealer());
    return () => {
      mounted.current = false;
    };
  }, []);

  const saved = artifacts.map(savedPath).find((p) => p !== null) ?? null;
  useEffect(() => {
    if (mode !== "folder" || saved !== null) return;
    getFolder()
      .then((info) => {
        if (mounted.current) setFolderPath(info.path);
      })
      .catch(() => undefined);
  }, [mode, saved]);

  if (mode === null || artifacts.length === 0) return null;

  if (mode === "artifacts") {
    return (
      <p
        className="m-0 flex min-w-0 items-start gap-2 text-maya-small text-maya-muted"
        data-testid="output-location"
        data-mode="artifacts"
      >
        <HardDrive aria-hidden="true" size={14} className="mt-0.5 shrink-0" />
        <span className="min-w-0 [overflow-wrap:anywhere]">
          Saved to MAYA&apos;s storage. No working folder was connected for this
          task. Download the files from the list below.
        </span>
      </p>
    );
  }

  const path = saved ?? folderPath;
  return (
    <div
      className="flex min-w-0 flex-col gap-1.5"
      data-testid="output-location"
      data-mode="folder"
    >
      <p className="m-0 flex min-w-0 items-start gap-2 text-maya-small text-maya-text">
        <FolderOpen
          aria-hidden="true"
          size={14}
          className="mt-0.5 shrink-0 text-maya-secondary"
        />
        <span className="min-w-0 [overflow-wrap:anywhere]">
          Saved in your working folder.
        </span>
      </p>
      {path !== null && (
        <WrapText
          as="p"
          dir="auto"
          title={path}
          className="m-0 font-mono text-maya-mono text-maya-secondary"
        >
          {path}
        </WrapText>
      )}
      {path !== null && reveal !== null && (
        <div>
          <Button
            size="sm"
            leftIcon={<FolderOpen size={14} aria-hidden="true" />}
            onClick={() => {
              setNote(null);
              void reveal(path).then((shown) => {
                if (!shown && mounted.current) {
                  setNote(
                    "The folder could not be opened here. Open it from Settings > Files.",
                  );
                }
              });
            }}
          >
            Open folder
          </Button>
        </div>
      )}
      {note !== null && (
        <p role="status" className="m-0 text-maya-small text-maya-muted">
          {note}
        </p>
      )}
    </div>
  );
}
