import { Loader2 } from "lucide-react";
import React from "react";

import type { ChatRunningTool } from "../../hooks/useChatStream";

/**
 * Plain labels for the inline tools the server reports. The event carries only the tool name, so
 * the chip never shows file content or file names; it says what kind of work is running.
 */
const TOOL_LABELS: Readonly<Record<string, string>> = {
  create_file_inline: "Creating a file",
  edit_attached_file: "Editing a file",
};

export function toolProgressLabel(name: string): string {
  return Object.prototype.hasOwnProperty.call(TOOL_LABELS, name)
    ? TOOL_LABELS[name]
    : "Working on a tool";
}

/**
 * A small progress chip for each inline tool that is running. It is removed when the server says
 * the tool finished (the caller drops it from the list), so nothing lingers after the work ends.
 */
export function ToolProgressChip({
  tools,
}: {
  readonly tools: readonly ChatRunningTool[];
}): React.JSX.Element | null {
  if (tools.length === 0) return null;
  return (
    <div
      className="flex min-w-0 max-w-full flex-wrap gap-1.5"
      role="status"
      aria-live="polite"
    >
      {tools.map((tool) => (
        <span
          key={tool.callId}
          className="maya-chip min-w-0"
          data-testid="tool-progress-chip"
        >
          <Loader2
            aria-hidden="true"
            size={12}
            className="shrink-0 animate-spin motion-reduce:animate-none"
          />
          <span className="min-w-0 [overflow-wrap:anywhere]">
            {toolProgressLabel(tool.name)}
          </span>
        </span>
      ))}
    </div>
  );
}
