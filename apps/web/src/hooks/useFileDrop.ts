import { useCallback, useRef, useState, type DragEvent } from "react";

function carriesFiles(event: DragEvent): boolean {
  const types = event.dataTransfer?.types;
  return types !== undefined && Array.from(types).includes("Files");
}

export interface FileDropBindings {
  readonly onDragEnter: (event: DragEvent) => void;
  readonly onDragOver: (event: DragEvent) => void;
  readonly onDragLeave: (event: DragEvent) => void;
  readonly onDrop: (event: DragEvent) => void;
}

/**
 * Drag-and-drop of files onto an element. Only drags that carry files react (dragging text or a
 * link does nothing), nested children do not flicker the state, and dropping always stops the
 * browser from opening the file. Returns `dragging` for the overlay and the handlers to spread on
 * the element. `enabled: false` ignores drags entirely.
 */
export function useFileDrop(
  onFiles: (files: File[]) => void,
  enabled = true,
): { dragging: boolean; bindings: FileDropBindings } {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  const onDragEnter = useCallback(
    (event: DragEvent) => {
      if (!enabled || !carriesFiles(event)) return;
      event.preventDefault();
      depth.current += 1;
      setDragging(true);
    },
    [enabled],
  );

  const onDragOver = useCallback(
    (event: DragEvent) => {
      if (!enabled || !carriesFiles(event)) return;
      event.preventDefault(); // required for the drop to fire
      event.dataTransfer.dropEffect = "copy";
    },
    [enabled],
  );

  const onDragLeave = useCallback(
    (event: DragEvent) => {
      if (!enabled || !carriesFiles(event)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    },
    [enabled],
  );

  const onDrop = useCallback(
    (event: DragEvent) => {
      if (!enabled || !carriesFiles(event)) return;
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      const files = Array.from(event.dataTransfer.files);
      if (files.length > 0) onFiles(files);
    },
    [enabled, onFiles],
  );

  return {
    dragging: enabled && dragging,
    bindings: { onDragEnter, onDragOver, onDragLeave, onDrop },
  };
}
