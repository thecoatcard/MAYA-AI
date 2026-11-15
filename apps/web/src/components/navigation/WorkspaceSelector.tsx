"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Folder, Plus } from "lucide-react";

import { useDesktopBridge } from "../../lib/desktop-bridge";
import {
  describeWorkspaceError,
  listWorkspaces,
  registerWorkspace,
  setDefaultWorkspace,
  type ProjectWorkspace,
} from "../../lib/workspaces-client";

export interface WorkspaceSelectorProps {
  readonly className?: string;
}

function getStatusDot(status: ProjectWorkspace["status"] | undefined): {
  readonly className: string;
  readonly title: string;
} {
  switch (status) {
    case "ok":
      return {
        className: "bg-maya-success",
        title: "Status: Ready",
      };
    case "missing":
      return {
        className: "bg-maya-danger",
        title: "Status: Missing folder",
      };
    case "not_writable":
      return {
        className: "bg-maya-warning",
        title: "Status: Not writable",
      };
    default:
      return {
        className: "bg-maya-disabled-text opacity-60",
        title: "No project folder",
      };
  }
}

/**
 * WorkspaceSelector allows users to view and switch between multiple connected
 * project workspaces, and connect new project folders via native dialog or prompt.
 */
export function WorkspaceSelector({
  className = "",
}: WorkspaceSelectorProps): React.JSX.Element {
  const [workspaces, setWorkspaces] = useState<ProjectWorkspace[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const bridge = useDesktopBridge();

  const loadWorkspaces = useCallback(async () => {
    try {
      const items = await listWorkspaces();
      setWorkspaces(items);
    } catch {
      // Do not block rendering if list fetch fails
    }
  }, []);

  useEffect(() => {
    void loadWorkspaces();

    const onWorkspaceChanged = (): void => {
      void loadWorkspaces();
    };

    window.addEventListener("maya:workspace-changed", onWorkspaceChanged);
    return () => {
      window.removeEventListener("maya:workspace-changed", onWorkspaceChanged);
    };
  }, [loadWorkspaces]);

  useEffect(() => {
    if (!isOpen) return;

    const handlePointerDown = (event: MouseEvent): void => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  const activeWorkspace = workspaces.find((w) => w.is_default) ?? null;
  const statusDot = getStatusDot(activeWorkspace?.status);

  const handleSelectWorkspace = async (id: string): Promise<void> => {
    if (activeWorkspace?.id === id) {
      setIsOpen(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await setDefaultWorkspace(id);
      window.dispatchEvent(new CustomEvent("maya:workspace-changed"));
      await loadWorkspaces();
      setIsOpen(false);
    } catch (err) {
      setError(describeWorkspaceError(err));
    } finally {
      setBusy(false);
    }
  };

  const handleAddWorkspace = async (): Promise<void> => {
    setError(null);
    let chosenPath: string | null = null;

    try {
      if (bridge.pickFolder) {
        chosenPath = await bridge.pickFolder();
      } else if (typeof window !== "undefined") {
        chosenPath = window.prompt("Enter absolute path for project folder:");
      }
    } catch {
      setError("Failed to open folder picker.");
      return;
    }

    if (!chosenPath || chosenPath.trim() === "") {
      return;
    }

    setBusy(true);
    try {
      await registerWorkspace(chosenPath.trim());
      window.dispatchEvent(new CustomEvent("maya:workspace-changed"));
      await loadWorkspaces();
      setIsOpen(false);
    } catch (err) {
      setError(describeWorkspaceError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      ref={containerRef}
      className={`relative w-full ${className}`}
      data-testid="workspace-selector"
    >
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-label="Select project workspace"
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center justify-between gap-2 px-2.5 py-2 rounded-maya-md border border-maya-control-border bg-maya-raised hover:bg-maya-hover text-maya-small text-maya-text transition-colors motion-interactive focus:outline-none focus:ring-1 focus:ring-maya-focus"
        data-testid="workspace-selector-trigger"
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <Folder
            size={16}
            className="shrink-0 text-maya-muted"
            aria-hidden="true"
          />
          <span
            className={`h-2 w-2 rounded-full shrink-0 ${statusDot.className}`}
            title={statusDot.title}
            aria-hidden="true"
            data-testid="workspace-status-dot"
          />
          <span
            className="truncate font-medium text-left"
            data-testid="active-workspace-name"
          >
            {activeWorkspace ? activeWorkspace.name : "No project folder"}
          </span>
        </div>
        <ChevronDown
          size={14}
          className={`shrink-0 text-maya-muted transition-transform duration-fast ${
            isOpen ? "rotate-180" : ""
          }`}
          aria-hidden="true"
        />
      </button>

      {isOpen && (
        <div
          role="listbox"
          aria-label="Project workspaces"
          className="absolute left-0 right-0 top-full mt-1.5 z-maya-dropdown min-w-[220px] rounded-maya-md border border-maya-control-border bg-maya-raised p-1 shadow-lg text-maya-small backdrop-blur-md"
          data-testid="workspace-dropdown"
        >
          <div className="px-2 py-1 text-maya-label font-medium uppercase tracking-wider text-maya-muted">
            Project Workspaces
          </div>

          <div className="max-h-56 overflow-y-auto flex flex-col gap-0.5">
            {workspaces.length === 0 ? (
              <div
                className="px-2.5 py-2 text-maya-caption text-maya-muted italic"
                data-testid="no-workspaces-message"
              >
                No project folders registered
              </div>
            ) : (
              workspaces.map((ws) => {
                const itemDot = getStatusDot(ws.status);
                return (
                  <button
                    key={ws.id}
                    type="button"
                    role="option"
                    aria-selected={ws.is_default}
                    disabled={busy}
                    onClick={() => void handleSelectWorkspace(ws.id)}
                    className={`w-full flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-maya-sm text-left transition-colors ${
                      ws.is_default
                        ? "bg-maya-hover text-maya-text"
                        : "text-maya-text hover:bg-maya-hover"
                    }`}
                    data-testid={`workspace-item-${ws.id}`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className={`h-2 w-2 rounded-full shrink-0 ${itemDot.className}`}
                        title={itemDot.title}
                        aria-hidden="true"
                      />
                      <span className="truncate font-medium">{ws.name}</span>
                    </div>
                    {ws.is_default && (
                      <Check
                        size={14}
                        className="shrink-0 text-maya-accent"
                        aria-label="Active workspace"
                      />
                    )}
                  </button>
                );
              })
            )}
          </div>

          <div className="my-1 border-t border-maya-border" role="separator" />

          <button
            type="button"
            onClick={() => void handleAddWorkspace()}
            disabled={busy}
            className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-maya-sm text-maya-accent hover:bg-maya-hover text-left transition-colors font-medium text-maya-small disabled:opacity-50"
            data-testid="add-workspace-button"
          >
            <Plus size={14} className="shrink-0" aria-hidden="true" />
            <span className="truncate">+ Add project folder...</span>
          </button>

          {error !== null && (
            <div
              className="px-2.5 py-1 text-maya-caption text-maya-danger"
              role="alert"
              data-testid="workspace-error-message"
            >
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
