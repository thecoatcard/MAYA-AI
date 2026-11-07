"use client";

import { Layers } from "lucide-react";
import React, { useCallback, useEffect, useState } from "react";

import { useLiveVoice } from "../../hooks/useLiveVoice";
import {
  listArtifacts,
  type ArtifactMetadata,
} from "../../lib/artifacts-client";
import { DeliverableCanvas } from "../canvas/DeliverableCanvas";
import { AppShell } from "../layout/AppShell";
import { VoiceOrb, type VoiceOrbState } from "../voice/VoiceOrb";
import { ChatWorkspace } from "./ChatWorkspace";

export function AmbientWorkspace(): React.JSX.Element {
  const [deliverables, setDeliverables] = useState<ArtifactMetadata[]>([]);
  const [canvasOpen, setCanvasOpen] = useState<boolean>(true);
  const [voiceState, setVoiceState] = useState<VoiceOrbState>("idle");
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(
    null,
  );

  const {
    isConnected: isVoiceConnected,
    isSpeaking: isVoiceSpeaking,
    isListening: isVoiceListening,
    isInterrupted: isVoiceInterrupted,
    transcripts,
    activeTask,
    activeAction,
    error: voiceError,
    startSession: startVoiceSession,
    stopSession: stopVoiceSession,
    interruptPlayback,
  } = useLiveVoice();

  // Sync VoiceOrb state from useLiveVoice engine
  useEffect(() => {
    if (voiceError) {
      setVoiceState("error");
    } else if (isVoiceInterrupted) {
      setVoiceState("interrupted");
    } else if (isVoiceSpeaking) {
      setVoiceState("speaking");
    } else if (isVoiceListening) {
      setVoiceState("listening");
    } else if (isVoiceConnected) {
      setVoiceState("listening");
    } else {
      setVoiceState("idle");
    }
  }, [
    voiceError,
    isVoiceInterrupted,
    isVoiceSpeaking,
    isVoiceListening,
    isVoiceConnected,
  ]);

  const handleVoiceStateChange = useCallback(
    (newState: VoiceOrbState) => {
      setVoiceState(newState);
      if (newState === "listening" && !isVoiceListening && !isVoiceConnected) {
        startVoiceSession().catch(() => {});
      } else if (
        newState === "idle" &&
        (isVoiceListening || isVoiceConnected)
      ) {
        stopVoiceSession();
      }
    },
    [isVoiceListening, isVoiceConnected, startVoiceSession, stopVoiceSession],
  );

  const handleBargeIn = useCallback(() => {
    interruptPlayback();
  }, [interruptPlayback]);

  const loadDeliverables = useCallback(async () => {
    try {
      const data = await listArtifacts();
      setDeliverables(data);
    } catch {
      // Not signed in or no artifacts
    }
  }, []);

  useEffect(() => {
    loadDeliverables();
    const interval = setInterval(loadDeliverables, 10000);
    return () => clearInterval(interval);
  }, [loadDeliverables]);

  const latestTranscript =
    transcripts.length > 0 ? transcripts[transcripts.length - 1] : null;

  return (
    <AppShell title="Ambient Workspace" breadcrumb="Home" requireAuth={false}>
      <div className="flex h-full w-full overflow-hidden bg-zinc-950">
        {/* Main Conversation & Voice Space */}
        <div className="flex-1 flex flex-col h-full overflow-hidden min-w-0">
          {/* Executive Unified Ambient Topbar: Compact Voice & Agent Status */}
          <div className="border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur-md flex items-center justify-between px-5 py-2.5 shrink-0 z-10">
            <div className="flex items-center gap-3.5 min-w-0">
              <VoiceOrb
                compact
                state={voiceState}
                onStateChange={handleVoiceStateChange}
                onBargeIn={handleBargeIn}
              />

              {/* Real-time active task indicator from live voice tools */}
              {(activeAction || activeTask) && (
                <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-teal-500/10 border border-teal-500/30 text-teal-300 text-xs font-mono shrink-0 animate-pulse">
                  <span className="w-1.5 h-1.5 rounded-full bg-teal-400" />
                  <span>
                    Maya Tool:{" "}
                    {activeAction?.toolName ||
                      activeTask?.intent ||
                      activeTask?.taskId}
                  </span>
                </div>
              )}

              {/* Streaming voice transcript ticker */}
              {latestTranscript && (
                <div className="hidden lg:flex items-center gap-2 text-xs text-zinc-400 font-mono truncate max-w-sm">
                  <span
                    className={`font-semibold ${
                      latestTranscript.sender === "model"
                        ? "text-teal-400"
                        : "text-zinc-300"
                    }`}
                  >
                    {latestTranscript.sender === "model" ? "MAYA:" : "You:"}
                  </span>
                  <span className="truncate">{latestTranscript.text}</span>
                </div>
              )}
            </div>

            {/* Canvas Toggle Button */}
            {!canvasOpen && deliverables.length > 0 && (
              <button
                type="button"
                onClick={() => setCanvasOpen(true)}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-teal-500/30 bg-teal-950/40 text-teal-300 text-xs font-medium hover:bg-teal-900/50 transition-colors shrink-0"
              >
                <Layers className="w-3.5 h-3.5" />
                <span>Deliverable Canvas ({deliverables.length})</span>
              </button>
            )}
          </div>

          {/* Unified Chat & Voice Conversational Stream */}
          <div className="flex-1 overflow-hidden relative">
            <ChatWorkspace
              embedded
              hideHeader
              hideSidebar
              hideConversationTopbar
              isVoiceListening={isVoiceListening}
              onToggleVoice={() => {
                if (isVoiceListening || isVoiceConnected) {
                  handleVoiceStateChange("idle");
                } else {
                  handleVoiceStateChange("listening");
                }
              }}
              activeAction={activeAction}
              voiceTranscripts={transcripts}
              onViewDeliverables={() => setCanvasOpen(true)}
            />
          </div>
        </div>

        {/* Deliverable Canvas Split Pane */}
        {canvasOpen && (
          <div className="w-96 lg:w-[420px] h-full shrink-0">
            <DeliverableCanvas
              deliverables={deliverables}
              isOpen={canvasOpen}
              onClose={() => setCanvasOpen(false)}
              selectedArtifactId={selectedArtifactId}
              onSelectArtifact={setSelectedArtifactId}
            />
          </div>
        )}
      </div>
    </AppShell>
  );
}
