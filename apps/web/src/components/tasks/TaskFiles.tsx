"use client";

import { File as FileIcon } from "lucide-react";
import React, { useEffect, useState } from "react";

import {
  listArtifacts,
  type ArtifactMetadata,
} from "../../lib/artifacts-client";
import { formatBytes } from "../../lib/files-client";
import type { TaskDetailResponse } from "../../lib/tasks-client";
import { ArtifactDownloadButton } from "../artifacts/ArtifactDownloadButton";
import { TaskOutputLocation } from "../files/TaskOutputLocation";
import { Truncate } from "../ui";

export interface TaskFilesProps {
  readonly taskId: string;
  /** Task detail, used only to say where the files were saved. */
  readonly task: Pick<TaskDetailResponse, "subtasks"> | null;
  /** Reloads the list when the phase changes (a finished task has new files). */
  readonly phase: string;
}

/**
 * The files a task produced, as compact cards inside the chat thread. Chat is the one place a
 * file can be downloaded (authenticated, with its real name and extension, zips included).
 */
export function TaskFiles({
  taskId,
  task,
  phase,
}: TaskFilesProps): React.JSX.Element | null {
  const [files, setFiles] = useState<readonly ArtifactMetadata[]>([]);

  useEffect(() => {
    let live = true;
    listArtifacts(taskId)
      .then((items) => {
        if (live) setFiles(items);
      })
      .catch(() => {
        if (live) setFiles([]);
      });
    return () => {
      live = false;
    };
  }, [taskId, phase]);

  if (files.length === 0) return null;
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="task-files">
      {task !== null && <TaskOutputLocation task={task} artifacts={files} />}
      <ul
        className="m-0 flex min-w-0 list-none flex-col gap-1.5 p-0"
        aria-label="Files"
      >
        {files.map((file) => (
          <li
            key={file.id}
            data-testid="file-card"
            className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-maya-md border border-maya-border bg-maya-raised px-2.5 py-1.5"
          >
            <FileIcon
              aria-hidden="true"
              size={16}
              className="shrink-0 text-maya-secondary"
            />
            <span className="grid min-w-0 flex-1 basis-40">
              <Truncate
                title={file.file_name}
                dir="auto"
                className="text-maya-small font-medium text-maya-text"
              >
                {file.file_name}
              </Truncate>
              <span className="text-maya-caption text-maya-muted">
                {file.file_type.toUpperCase()} · {formatBytes(file.size_bytes)}
              </span>
            </span>
            <ArtifactDownloadButton
              artifactId={file.id}
              fileName={file.file_name}
              fileType={file.file_type}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}
