import { BookmarkCheck } from "lucide-react";
import { useCallback, useState } from "react";

import type { SavedMemory } from "../../hooks/useChatStream";
import { ApiError } from "../../lib/api-client";
import { deleteMemoryItem } from "../../lib/memory-client";

type ChipState = "saved" | "undoing" | "removed" | "failed";

export interface MemorySavedChipProps {
  memory: SavedMemory;
  /** Delete the memory. Defaults to the Memory API; replaced in tests. */
  onUndo?: (memoryId: string) => Promise<void>;
}

/**
 * "Saved to memory · Undo" under a reply that saved something. Undo calls the
 * Memory API delete. A 404 means it is already gone, which is the state the
 * user asked for, so it reads as removed. The state is in words, never colour
 * alone, and changes are announced through the status region.
 */
export function MemorySavedChip({
  memory,
  onUndo = deleteMemoryItem,
}: MemorySavedChipProps) {
  const [state, setState] = useState<ChipState>("saved");

  const undo = useCallback(async () => {
    setState("undoing");
    try {
      await onUndo(memory.memoryId);
      setState("removed");
    } catch (error) {
      setState(
        error instanceof ApiError && error.status === 404
          ? "removed"
          : "failed",
      );
    }
  }, [memory.memoryId, onUndo]);

  const label = memory.updated ? "Memory updated" : "Saved to memory";
  return (
    <p className="maya-msg__note" role="status" data-memory-state={state}>
      <BookmarkCheck aria-hidden="true" size={14} />
      {state === "removed" ? (
        <span>Removed from memory.</span>
      ) : (
        <span>
          {label} · <span data-testid="memory-text">{memory.text}</span>
          {state === "failed" ? (
            <span> Could not remove it. Try again.</span>
          ) : null}
          <button
            type="button"
            className="maya-msg__action"
            disabled={state === "undoing"}
            aria-label={`Undo: remove "${memory.text}" from memory`}
            onClick={() => {
              void undo();
            }}
          >
            {state === "undoing" ? "Removing" : "Undo"}
          </button>
        </span>
      )}
    </p>
  );
}
