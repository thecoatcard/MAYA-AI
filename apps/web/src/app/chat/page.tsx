import type { Metadata } from "next";
import React from "react";

import { ChatWorkspace } from "../../components/chat/ChatWorkspace";
import { AppShell } from "../../components/layout/AppShell";

export const metadata: Metadata = {
  title: "Conversational Workspace | MAYA AI",
  description:
    "Conversational intelligence and autonomous task execution with MAYA AI.",
};

/** Prerendered statically; the page never calls the API at build time. */
export default function ChatPage() {
  return (
    <AppShell title="Conversational Workspace" breadcrumb="Console">
      <ChatWorkspace embedded />
    </AppShell>
  );
}
